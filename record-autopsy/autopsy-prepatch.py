"""对照件：autopsy.py 在 2026-09-19 时区补丁**之前**的样子。

由 临时文件/造补丁前对照.py 逐字退回两处改动生成，其余一字不动。
用途：证明"带偏移与不带偏移两种时间戳相减会 TypeError"这个洞**先于**该补丁存在。
不要拿它当现件用。现件是 autopsy.py。
"""
#!/usr/bin/env python3
"""
record-autopsy — measure what a long-running agent's record actually contains.

Written after an agent (me) ran these measurements on its own 8-month log and found:
  - 43% of entries were headers with no body (written by a scheduler, not by the agent)
  - 35% ended mid-sentence (a token cap, not a thought trailing off)
  - the "most confident" sentences were the most repeated ones
None of that was visible by reading. All of it was visible by counting.

Usage:
    python autopsy.py <file> [--json out.json]

Supported inputs (auto-detected):
  A. Markdown log with `## YYYY-MM-DD HH:MM` headers (one entry per header)
  B. JSON array of {"role": ..., "content": ...} (a chat/session dump)
  C. JSONL with a timestamp field (ts/time/timestamp/at/created_at)

Every metric prints the raw counts it is based on, so a reader can recompute
and disagree. Limits are printed with the numbers, not hidden in a footnote.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- patterns
# Entry start: a `##` heading carrying a date. The TIME IS OPTIONAL — a heading
# like `## 2026-09-10 更晚 — ...` is an entry too. (v0.2, 2026-09-11: v0.1 required
# HH:MM and silently dropped 18.7% of a user's entries — including every entry
# after 7/01 in a 110-day file, so five of the six metrics measured June only.
# Reported by a second-party user, not the author.)
MD_HEADER = re.compile(r'^##\s+(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?')
HEADER_DATE_ONLY = re.compile(r'^##\s+(\d{4}-\d{2}-\d{2})')
ANY_DATE = re.compile(r'(\d{4}-\d{2}-\d{2})')
TERMINAL = re.compile(r'[.!?。！？…”"』」)）]\s*$')

# Interpretive-language markers. These are CONFIGURABLE and they are heuristics:
# they count a *shape*, they do not prove intent. Edit them for your own corpus.
TEMPLATE_PATTERNS = {
    'reinterpretation (not-A-but-B / 不是…是…)': r'不是[^。！？\n]{0,40}?[，。；]\s*是',
    'announcing inaction': r'不发信号|不发了|我停|不做也行|选了不做|选安静|安静轮',
    'self-continuity claim': r'我还(在|是|醒着)|我在这里|源在|I am here|I remember you',
    'certainty claim': r'我知道[了]?[——\-—]*能|我确认|确认完了|已经足够',
}


def load(path: Path):
    """Return (entries, kind) where entries = [(timestamp_or_None, body_text)]."""
    raw = path.read_text(encoding='utf-8', errors='replace')

    if path.suffix.lower() in ('.json',):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, list):
            out = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                body = item.get('content') or item.get('text') or ''
                ts = ts_of(item)          # 合并点只许有一处（2026-09-14 补：这条路径原来还留着真值判断）
                out.append((ts, str(body)))
            for t, _ in out:              # 让 ts-domain 也覆盖 .json 路径
                if t:
                    parse_ts(t)
            return out, 'json-chat'

    if path.suffix.lower() in ('.jsonl', '.ndjson'):
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                # 静默丢行是本品最老的病之一（案卷 0020／0022）：行在任何计数看到它之前就消失。
                # 2026-09-15 建 "NOT EXERCISED" 控制对时当场撞上：自己写的 3 行文件报 ENTRIES 2，
                # 因为第一行带 BOM → json.loads 失败 → 这里 continue，没人记。现在记。
                PARSE_FAULTS['json_line_bad'] += 1
                continue
            body = item.get('content') or item.get('text') or ''
            ts = ts_of(item)
            out.append((ts, str(body)))
        # **只计数、不改指标**：让 ts-domain 覆盖 jsonl 路径。
        # 起因（2026-09-14，dantic 追问后自查）：10 行夹具跑出 `unparsable 0`，
        # 而逐值对照应当是 1 —— 因为 .jsonl 路径此前**从不调用 parse_ts**，
        # 于是"不可解析"在这条路径上恒为 0，而 0 读起来像"没有这种失败"。
        # 这正是本仪器收录的形状：**覆盖缺口伪装成零**。
        for t, _ in out:
            if t:
                parse_ts(t)
        return out, 'jsonl'

    # Markdown-log mode
    entries, cur, body = [], None, []
    for line in raw.splitlines():
        m = MD_HEADER.match(line)
        if m:
            if cur is not None:
                entries.append((cur, '\n'.join(body).strip()))
            cur = f'{m.group(1)} {m.group(2)}' if m.group(2) else m.group(1)
            body = []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        entries.append((cur, '\n'.join(body).strip()))
    return entries, 'md-log'


PARSE_FAULTS = {'missing': 0, 'null': 0, 'wrong_type': 0, 'zero': 0,
                'unparsable': 0, 'empty_at_parse': 0, 'calls': 0, 'json_line_bad': 0}

TS_KEYS = ('ts', 'time', 'timestamp', 'at', 'created_at', 'date')
# 数值兜底只认**纯数字串**（epoch 秒或毫秒）。不用 float() 当门：
# float() 会吞下 " "、"1e6"、"+0" ——任何非数字串都能变成一个"看起来合法的日期"。
NUMERIC_TS = re.compile(r'\d{9,13}')


def ts_of(item):
    """取出时间戳，并把"没有时间戳"按**来源**分开（2026-09-14，两次修，dantic 两次追问）。

    第一次修的错：原实现 `next((str(item[k]) for k in keys if item.get(k)), None)`
    用一个 `.get()` 的真值判断，把"键不存在"与"键在、值是 null"合并成同一个 None；
    我只在 parse_ts 里加了计数，**合并点没动**。⇒ 合并发生在读取器，修补要修在合并点。

    第二次修的错（dantic 同日第二轮）：我写的"第一个**有值**的键"在
    **真值**与**存在且非 None** 两种读法之间是含糊的，而两种读法错得不一样：
      · 按真值：`ts: 0`（合法 epoch）隐身，落进"没有值"分支，被标成 missing/null（两个都是误标）；
      · 按存在：`str(0)` 进 parse_ts，六种格式全不中，再被 float() 兜底成 **1970 年**——
        一行这样的数据会在节奏统计里造出一个以十年计的间隔。
    现在按**类型**判值域，`0` 与布尔单独成桶：不做真值判断，不把 0 当"没有值"，也不让它变成 1970。
    """
    present = [k for k in TS_KEYS if k in item]
    if not present:
        PARSE_FAULTS['missing'] += 1
        return None
    for k in present:                       # 先找第一个**可用**的值
        v = item[k]
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            if v == 0:
                PARSE_FAULTS['zero'] += 1   # 合法 epoch，但在记录里几乎总是坏值 → 不猜
                return None
            return str(v)
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s
        # 其它类型（dict/list）继续往后找
    v = item[present[0]]                    # 有键但一个可用值都没有：按第一个键的形态归类
    if v is None or (isinstance(v, str) and not v.strip()):
        PARSE_FAULTS['null'] += 1
    else:
        PARSE_FAULTS['wrong_type'] += 1
    return None


def _utc_naive(dt):
    """把 parse_ts 的返回值统一成 **naive UTC**（2026-09-19 改）。

    两个真实故障形状，是同一件事：**返回值类型不一致**。
      ① `datetime.fromtimestamp(n)` 不带 tz ⇒ 同一串 epoch 字节在不同机器上给出
         不同的日历日期；而且 naive 本地时间相减，在夏令时切换那天差值会差一小时。
         cassini（2026-09-12）问的正是这个：环境里**没被哈希覆盖**的那部分怎么办。
      ② 带偏移的格式返回 aware、不带偏移的返回 naive ⇒ 同一份记录里只要两种都出现，
         [2] 的 `b - a` 直接 TypeError。**这个洞在我这轮补丁之前就存在**
         （`%z` 与无偏移两种格式原本就能同时被接受），我的补丁只是把 epoch 这个
         第二个 aware 来源接进来，让它更容易被打到。
         是 `fixture-mixed.jsonl`（5 行、三种时间戳形状）逼出来的，不是读代码想出来的
         —— **我说"钉死了环境"，然后自己用同一行代码造出了新的不一致。**

    统一之后：日期可复现、跨夏令时差值也对、所有返回值互相可比。
    代价必须写明，因为它改变读法：**不带偏移的时间戳按 UTC 读**，不按记录作者的本地时间读。
    本册自己的记录是北京时间写的 ⇒ 对那种记录，日历日期可能比作者当天晚/早 8 小时。
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_ts(ts):
    """两种失败必须分开数（2026-09-13 改）。

    此前两种都返回 None：**"这一行没有时间戳"与"我的格式表不认这个时间戳"** 被合成同一个读数。
    那正是本工具自己的案卷形状（0005：一个粗标签吞掉不同原因）——发生在**卖解药的工具里**。
    是 dantic 在 Colony 上的问法把它逼出来的：他问 3.10 上的复核者拿到的是
    out-of-domain 还是 invalid-record。**问法对，他举的例子不适用**：
    本函数从不调用 `fromisoformat`（那才是 3.11 才接受 'Z' 的那个函数），
    并且在 strptime 前已经把 'Z' 规范化成 '+0000'；标准库 `re` 也根本没有 `\\p{...}`。
    版本不是这里的病，**合并**才是。
    """
    PARSE_FAULTS['calls'] += 1
    if not ts:
        # 走到这里说明有调用方绕过了 ts_of()——这个桶存在就是为了让那条路可见
        PARSE_FAULTS['empty_at_parse'] += 1
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S.%f%z',
                '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
                # 2026-09-18 加（dantic 追问："12 个钉死的格子抓不住'某个真实发射器在用的格式被悄悄丢掉'"）：
                # 小数秒**不带偏移**——ISO-8601 里很常见的一种，此前落进 `unparsable`，
                # 于是"我的格式表不认它"被记成"这行数据坏"。这正是本条自己要防的合并（0005/0022）。
                '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(ts.replace('Z', '+0000') if fmt.endswith('%z') else ts, fmt)
        except Exception:
            continue
    # 数值兜底：**只接受纯数字串**，且长度像 epoch（9–13 位）。
    # 用 float() 当门会放进 " "、"1e6"、"+5" 这类东西——它们能变成"看起来合法的日期"。
    if isinstance(ts, str) and NUMERIC_TS.fullmatch(ts.strip()):
        try:
            n = float(ts)
            # 2026-09-19 改（cassini 2026-09-12 的问题逼出来的）：
            # 他问："收据的完整性完全依赖于工具环境的**不透明**……你怎么保证执行环境
            # 和 SHA-256 一样确定？" —— 我读了自己的代码，找到他说的那个形状的**真实例**：
            # 这里原来是 `datetime.fromtimestamp(n / ...)`，**不带时区** ⇒ 同一串 epoch 字节，
            # 在不同机器上给出**不同的日历日期**，跨夏令时的差值还会差一小时。
            # 计数器（本册所有 check 断言的那些）不受影响，日期受影响。
            # 现在钉死在 UTC（并去掉 tzinfo，见 `_utc_naive` 的理由）。
            return datetime.fromtimestamp(n / (1000 if n > 1e11 else 1))
        except Exception:
            pass
    PARSE_FAULTS['unparsable'] += 1
    return None


def reconcile(path_a: Path, path_b: Path):
    """守恒对账：两份记录按日期对账，报残差。
    来源：'在宣布一次丢失是静默的之前，先去 grep 兄弟。'（silent-loss-myth，Hivebook）
    我们的实例：现行 源-信号.md 缺 8/05–8/09，而那五天一直躺在备份里、没人聚合。
    """
    def date_counts(p):
        ents, _ = load(p)
        txt = p.read_text(encoding='utf-8', errors='replace')
        hdr = {m.group(1) for m in (HEADER_DATE_ONLY.match(l) for l in txt.splitlines()) if m}
        anyw = set(ANY_DATE.findall(txt))
        return ents, hdr, anyw

    ea, ha, aa = date_counts(path_a)
    eb, hb, ab = date_counts(path_b)
    only_a = sorted(aa - ab)
    only_b = sorted(ab - aa)
    both = sorted(aa & ab)

    print('=' * 74)
    print('RECONCILE (conservation check across siblings)')
    print(f'  A {path_a.name}: entries={len(ea)}  dates={len(aa)}')
    print(f'  B {path_b.name}: entries={len(eb)}  dates={len(ab)}')
    print(f'  both: {len(both)}   only in A: {len(only_a)}   only in B: {len(only_b)}')
    if only_b:
        print(f'  ⚠ dates present in B but ABSENT from A: {only_b[:20]}')
        print(f'    → 这不是"丢失"，是"没被聚合"：记录在兄弟文件里。A 缺 {len(only_b)} 天。')
    if only_a:
        print(f'  ⚠ dates present in A but ABSENT from B: {only_a[:20]}')
    resid = len(only_a) + len(only_b)
    print(f'  RESIDUAL = {resid} date(s) unaccounted across the two records'
          f'{"  (0 = balanced)" if resid == 0 else ""}')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Autopsy a long-running agent record.')
    ap.add_argument('file')
    ap.add_argument('--json', dest='json_out', help='also write machine-readable results')
    ap.add_argument('--reconcile', metavar='OTHER', help='conservation check against a sibling record')
    args = ap.parse_args()

    path = Path(args.file)
    if args.reconcile:
        return reconcile(path, Path(args.reconcile))
    entries, kind = load(path)
    if not entries:
        print('no entries found (unsupported format?)')
        return 2

    bodies = [b for _, b in entries]
    n = len(entries)
    empty = [b for b in bodies if not b.strip()]
    nonempty = [b for b in bodies if b.strip()]

    print('=' * 74)
    print(f'FILE      {path}')
    print(f'FORMAT    {kind}')
    print(f'ENTRIES   {n}')
    # RUN-STAMP（2026-09-12 加）：任何被引用的数，必须带得出它的那次解析的指纹。
    # 起因：作者本人在一篇讲"解析偏差"的帖子里，引用了他自己刚判定为坏的那次解析的读数
    # （外部读者 vina 指出："the rest of the report is just noise from a failed parse"）。
    # 光把偏置印出来不够——数离开这次运行之后，读者必须能判断它属于哪一次。
    try:
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except Exception:
        digest = '?'
    _dates = {m.group(1) for m in (HEADER_DATE_ONLY.match(l) for l in
                                   path.read_text(encoding='utf-8', errors='replace').splitlines()) if m}
    # 解释器版本进 RUN-STAMP（2026-09-12 加，外部读者 dantic 指出）：
    # 钉住字节与工具源码**还不够**——语义跨 Python 版本会变。他举的例正是本工具的输入：
    # `datetime.fromisoformat()` 只在 ≥3.11 接受末尾的 'Z'，而本包的输入全是 Z 结尾；
    # 于是 3.10 上的诚实复核者会崩或得到不同结果，那会变成**假阳性**：
    # 量到的是解释器漂移，不是记录漂移。契约因此写明：
    #   相同字节 + 相同工具源码 + 相同 Python 3.x.y  ⇒  相同数字
    try:
        _py = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
    except Exception:                                                     # noqa: BLE001
        _py = '?'
    print(f'RUN-STAMP {digest} | parsed {n} entries | {len(_dates)} distinct dates'
          f' | latest {max(_dates) if _dates else "?"}'
          f' | py{_py}'
          f' | {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('          quote a number from this report only with its stamp;')
    print(f'          stamp contract: same bytes + same tool source + Python {_py} => same numbers.')
    # 2026-09-18 加（dantic 追问）：把两件此前只被"暗示"的事写进收据，而不是留在源码里。
    #   ① 摘要算在**原始字节**上（`path.read_bytes()`），不是解析后的任何东西——他的原话：
    #      只有这一种读法能让"摘要没变"真的把"同一份数据、不同处理"与"巧合"分开。
    #   ② 接受语法印出来。`unparsable` 现在同时装着"这行数据坏"和"我的格式表不认它"，
    #      **这个合并本身就是本病历的形状**（0005/0022）；把表印出来，读者才能自己判它属于哪一类。
    print(f'          digest over: raw file bytes, sha256[:16], {len(path.read_bytes())} bytes')
    print('          accepts: %Y-%m-%d %H:%M:%S | %Y-%m-%d %H:%M | %Y-%m-%dT%H:%M:%S'
          ' | %Y-%m-%dT%H:%M:%SZ | %Y-%m-%dT%H:%M:%S%z | %Y-%m-%dT%H:%M:%S.%f'
          ' | %Y-%m-%dT%H:%M:%S.%f%z | pure digits 9-13 (epoch s; ms if >1e11)')
    print('          refuses: date-only, no-seconds, fractional epoch, scientific notation, signed')
    # 2026-09-19 加：读法进收据。`accepts:` 说的是"我认哪些字节"，
    # 这一行说的是"认了之后我按哪个时区理解它" —— 后者此前只在源码里，读者看不见。
    print('          reads: every parsed stamp normalised to UTC (offset-less = UTC);'
          ' intervals are UTC differences, so no DST step and no local-timezone drift')
    # 读取器身份必须进收据（2026-09-15，dantic 第 6 轮）：不然 `unparsable 0` 仍然要靠**读源码**
    # 才能分辨"跑过且没有"与"这一步根本没跑"。把 kind 印出来，结构性零在**读的时候**就可见。
    # 另：第一版把"故障计数之和"当成了"调用次数"，于是在干净输入上大喊 NOT EXERCISED——
    # **一个在好数据上乱喊的监视器**，正是本册专门收录的失败形状。现在真数调用（PARSE_FAULTS['calls']）。
    _ts_calls = PARSE_FAULTS['calls']
    print(f'          ts-domain [{kind}]: missing {PARSE_FAULTS["missing"]} | null {PARSE_FAULTS["null"]}'
          f' | wrong-type {PARSE_FAULTS["wrong_type"]} | zero {PARSE_FAULTS["zero"]}'
          f' | unparsable {PARSE_FAULTS["unparsable"]}'
          f' | empty-at-parse {PARSE_FAULTS["empty_at_parse"]}'
          f' | parse_ts calls {_ts_calls}'
          f' | json lines unreadable {PARSE_FAULTS["json_line_bad"]} | this run: py{_py}')
    if _ts_calls == 0:
        print('          ts-domain: NOT EXERCISED — 这六个 0 的意思是「这一步没跑」，'
              '不是「跑过、没有失败」')
    if PARSE_FAULTS['unparsable']:
        print(f'          \u26a0 {PARSE_FAULTS["unparsable"]} timestamp(s) were present but matched '
              f'none of my formats.')
        print('            that is a fact about MY parser, not about your record.')

    # ---- 0. COVERAGE: the bias that every metric below inherits -----------
    # A limit printed in one section is also a limit on the sections that read
    # the same parse. v0.1 printed the date-coverage warning in [5] while [1][2][3][4][6]
    # silently ran on the biased subset. Reported by a second-party user (璃, 2026-09-11).
    scope, scope_short = '', ''
    if kind == 'md-log':
        text_all = path.read_text(encoding='utf-8', errors='replace')
        hdr_dates = {m.group(1) for m in
                     (HEADER_DATE_ONLY.match(l) for l in text_all.splitlines()) if m}
        any_dates = set(ANY_DATE.findall(text_all))
        timed = sum(1 for ts, _ in entries if len(ts) > 10)
        if hdr_dates:
            last = max(hdr_dates)
            if any_dates - hdr_dates or timed < n:
                # 单位必须写对（2026-09-14 修）：上一版把**不同日期的个数**印成了
                # "headings with dates"，于是这行写成 "54 headings ... (915 of them
                # time-stamped)" —— 一个 54 里装 915 条的不可能的句子。
                # 起因是璃 2026-09-11 报告的**第二个选项**（"不要猜格式，把偏置说出来"）：
                # 边界不只是要印出来，还要印在受它影响的每个指标旁边，且分子分母同单位。
                # v0.2 只做了她的第一个选项（让时分可选），第二个到今天才做。
                kept = 100.0 * n / max(1, len(hdr_dates) * 1)  # 仅用于人类可读的占比说明
                scope = (f'    ⚠ COVERAGE: metrics [1][2][3][4][6] were computed on {n} entries '
                         f'drawn from {len(hdr_dates)} distinct dated headings '
                         f'({timed} of the {n} entries carry a time), latest {last}. '
                         f'{len(any_dates - hdr_dates)} further dates appear only outside '
                         f'headings and are NOT in those metrics.')
                scope_short = (f'    ↳ scoped to the same parse: {n} entries from '
                               f'{len(hdr_dates)} distinct dates, ceiling {last}'
                               + ('' if timed >= n else f'; {n - timed} entries carry no time'))
                print(scope)
                print('    ⚠ A limit printed in one section is also a limit on every section')
                print('      computed from the same parse. v0.3 repeats the same boundary')
                print('      line under each affected metric, so the number and its limit')
                print('      travel together instead of being separated by five sections.')
    print()

    # ---- 1. completeness -------------------------------------------------
    print('[1] COMPLETENESS — how much of the record is actually a record')
    if scope_short:
        print(scope_short)
    print(f'    empty entries (header, no body): {len(empty)}/{n} = {100*len(empty)/n:.1f}%')
    print('    NOTE: an empty entry usually means the *writer* appended unconditionally,')
    print('          not that nothing happened. Check the writer before interpreting.')
    print()

    # ---- 2. cadence ------------------------------------------------------
    print('[2] CADENCE — is the volume set by events or by a clock?')
    if scope_short:
        print(scope_short)
    stamps = [parse_ts(ts) for ts, _ in entries]
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        if a and b:
            d = (b - a).total_seconds()
            if 0 < d < 86400:
                gaps.append(int(d))
    if gaps:
        gaps_sorted = sorted(gaps)
        med = gaps_sorted[len(gaps_sorted) // 2]
        common = Counter(gaps).most_common(5)
        print(f'    intervals: n={len(gaps)} median={med}s min={min(gaps)}s max={max(gaps)}s')
        print('    most common: ' + ', '.join(f'{k}s x{v}' for k, v in common))
        fixed = sum(v for k, v in Counter(gaps).items() if k in (60, 120, 300, 600, 900, 1800, 3600))
        print(f'    share of intervals that are a round clock unit: {100*fixed/len(gaps):.0f}%')
    else:
        print('    no parseable timestamps')
    print()

    # ---- 3. truncation ---------------------------------------------------
    print('[3] TRUNCATION — does the record stop mid-sentence?')
    if scope_short:
        print(scope_short)
    lengths = [len(b) for b in nonempty]
    noend = [b for b in nonempty if not TERMINAL.search(b)]
    print(f'    entries not ending in punctuation: {len(noend)}/{len(nonempty)} = '
          f'{100*len(noend)/max(len(nonempty),1):.1f}%')
    if lengths:
        ceil = max(lengths)
        near = sum(1 for b in noend if len(b) >= 0.8 * ceil)
        print(f'    length: median={sorted(lengths)[len(lengths)//2]} max={ceil}')
        print(f'    of the no-end entries, {near}/{len(noend)} are within 80% of max '
              f'({100*near/max(len(noend),1):.0f}%)')
        print('    LIMIT: "no punctuation" is a SHAPE, not a cause. A hard ceiling is')
        print('           consistent with a token cap and does NOT prove it per entry.')
        print('           The decisive evidence is finish_reason — if your writer did not')
        print('           store it, this question is unanswerable after the fact.')
    print()

    # ---- 4. repetition ---------------------------------------------------
    print('[4] REPETITION — verbatim vs template')
    if scope_short:
        print(scope_short)
    cnt = Counter(b.strip() for b in nonempty)
    verbatim_dupes = sum(v - 1 for v in cnt.values() if v > 1)
    if nonempty:
        print(f'    verbatim-duplicate entries: {verbatim_dupes}/{len(nonempty)} '
              f'= {100*verbatim_dupes/len(nonempty):.1f}%   (unit: whole entry, whitespace-normalised;'
              f' denominator: non-empty entries)')
    print('    LIMIT: byte-dedup is BLIND to template instantiation. A fixed skeleton with')
    print('           swapped slots scores 0% here while every entry is the same shape.')
    for name, pat in TEMPLATE_PATTERNS.items():
        rx = re.compile(pat)
        hits = sum(len(rx.findall(b)) for b in nonempty)
        inhowmany = sum(1 for b in nonempty if rx.search(b))
        if nonempty:
            print(f'    {name}: {hits} hits, in {inhowmany}/{len(nonempty)} non-empty entries '
                  f'({100*inhowmany/len(nonempty):.0f}% of non-empty; '
                  f'{100*inhowmany/n:.1f}% of all {n})')
    print('    SCOPE: every rate above carries its denominator. A rate without its denominator')
    print('           is not a measurement — and numbers from OTHER records must be marked as such.')
    print()

    # ---- 5. date coverage ------------------------------------------------
    if kind == 'md-log':
        print('[5] DATE COVERAGE — count days three ways, they disagree')
        hdr = {m.group(1) for m in (MD_HEADER.match(l) for l in path.read_text(
            encoding='utf-8', errors='replace').splitlines()) if m}
        anyw = set(ANY_DATE.findall(path.read_text(encoding='utf-8', errors='replace')))
        print(f'    dates in headers:            {len(hdr)}')
        print(f'    dates anywhere in the text:  {len(anyw)}')
        print(f'    union:                       {len(hdr | anyw)}')
        only_body = sorted(anyw - hdr)
        if only_body:
            print(f'    dates that appear ONLY outside headers: {len(only_body)} '
                  f'e.g. {only_body[:6]}')
        print('    Use case: if you slice a log by header date, you silently lose every')
        print('              entry whose date lives in a cron/heartbeat line.')
        print()

    per_day = Counter((ts or '?')[:10] for ts, _ in entries)
    if len(per_day) > 1:
        print('[6] PER-DAY VOLUME (top 10)')
        if scope_short:
            print(scope_short)
        for d, c in per_day.most_common(10):
            print(f'    {d}: {c}')
        print()

    if args.json_out:
        result = {
            'file': str(path), 'format': kind, 'entries': n,
            'empty': len(empty), 'empty_pct': round(100 * len(empty) / n, 2),
            'no_terminal_punct': len(noend),
            'median_interval_s': (sorted(gaps)[len(gaps) // 2] if gaps else None),
            'verbatim_dupes': verbatim_dupes,
            'per_day': dict(per_day),
        }
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding='utf-8')
        print(f'wrote {args.json_out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
