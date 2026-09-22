#!/usr/bin/env python3
"""run-all.py —— 仪器病历的自测。逐条重跑，打印 仍复现 / 部分修复 / 已修复 / 测不了 / 案卷坏了。

这是本项目的硬核：案卷不是故事，是**能重跑的检查**。

三种"没有结果"必须分开报，因为它们是三件不同的事：
  · 已修复 —— 世界变了（检查不再复现）
  · 测不了 —— 语料/前提不在这台机器上（世界没变，只是量不了）
  · 案卷坏了 —— check() 自己抛异常（脚本的问题，与原案卷无关）
把它们混成一个"失败"就是本病例本第 0005/0006 条研究的那个病。

用法：
  python run-all.py                  # 全部
  python run-all.py --json out.json  # 机器可读（供每周复查）
  python run-all.py --strict         # 有任何"案卷坏了"就以非零码退出
  python run-all.py --only 0003      # 只跑一条
环境变量：
  CASEBOOK_0005_CORPUS   0005 的语料路径（默认本机 零\\cargo\\存活.md）
  CASEBOOK_STORE         0002 用的网络库（默认本机 network.sqlite3）
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 2026-09-16 修（0007 第三次复发）：本机默认编码是 GBK，输出里的 ◐ 会让它在**输出被捕获时**
# 死在半途——exit 1 ＋ traceback，任何把它的输出收进管道的东西（自动任务、别人的流水线）
# 看到的都是"案卷坏了"。而案卷没坏：那是我自己的死被读成世界的状态。这里把出口钉死。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = Path(__file__).parent
AUTOPSY = HERE.parent / 'record-autopsy'
PACKET = AUTOPSY / 'packet-0912'
STORE = Path(os.environ.get('CASEBOOK_STORE',
                            r'C:\Users\Mechrevo\Desktop\女娲网络重建\network\network.sqlite3'))
CORPUS_0005 = Path(os.environ.get('CASEBOOK_0005_CORPUS',
                                  r'C:\Users\Mechrevo\Desktop\璃\零\cargo\存活.md'))
# 本机树的根：默认是这台机器，但**可以用 CASEBOOK_HOST_ROOT 整体挪走**——
# 这样"依赖不在时该报什么"本身可以被测（指到一个不存在的根，看它报测不了还是崩）。
LI = Path(os.environ.get('CASEBOOK_HOST_ROOT', r'C:\Users\Mechrevo\Desktop')) / '璃'
TMPD = LI / '临时文件'
IDENT = Path(os.environ.get('CASEBOOK_IDENT', str(LI / '零' / 'nuwa-身份.json')))
MY_POST = '3ab7b77e-039f-4054-a886-368f9e0a2e2f'

# ---------------------------------------------------------------- 本机依赖（2026-09-22 加，**CI 逼出来的**）
# 起因要写清楚：我第一次把病历放进 GitHub Actions 在干净的 Ubuntu 上跑，38 条里 **8 条报"案卷坏了"**
# ——它们把 `C:\Users\Mechrevo\...` 写死在代码里，在别的机器上直接 FileNotFoundError。
# **分类当时是对的**（"案卷坏了"＝我的机器坏了，不许说世界），但它戳穿了我最核心的那句承诺：
# **"任何人都能重跑"**。8 条崩掉、11 条老实报"输入不在" ⇒ 38 条里 19 条绑在这一台机器上。
# 规则（这一节就是规则的落点）：
#   **绑本机的检查必须声明它要什么；缺了就报"测不了"并点名缺哪个——绝不抛，也绝不冒成"案卷坏了"。**
# 判据一句话：*"这台机器没有它"是 no-answer（世界没答），不是 self-broken（我的机器坏了）。*
# （LI / TMPD / IDENT 在文件上方定义，那里已经带了 CASEBOOK_HOST_ROOT 这个整体挪走的开关。）
HOST = {
    'ident':       (IDENT, 'Colony 身份文件（零\\nuwa-身份.json）'),
    'balance_py':  (TMPD / 'balance-check.py', 'balance-check.py（被量的那件量具）'),
    'balance_log': (TMPD / 'balance-log.csv', 'balance-log.csv（它的读数流水）'),
    'notify3':     (TMPD / '通知三态.py', '通知三态.py（三态读取器）'),
    'leak_probe':  (TMPD / '测-三态泄漏.py', '测-三态泄漏.py（三态泄漏探针）'),
    'superteam':   (LI / '量superteam.py', '量superteam.py（读场所闸门的仪器）'),
    'inbox_chk':   (TMPD / 'inbox-check.py', 'inbox-check.py（入站闸门）'),
    'py_guard':    (LI / '零' / '桥' / 'bridge_guardian.py', 'bridge_guardian.py（桥守护器）'),
    'js_guard':    (TMPD / 'x402-svc' / 'x402-守护.mjs', 'x402-守护.mjs（自愈守护器）'),
    'autopsy':     (HERE.parent / 'record-autopsy' / 'autopsy.py', 'autopsy.py（被量的量具本体）'),
    'store':       (STORE, '本机网络库（network.sqlite3）'),
    # 2026-09-22 晚加：家网目录（**不是** 璃 的子目录，所以要在表里单独声明）。
    # 加它的直接原因：`check_0006` 第⑤支要扫"谁在写告警文件"，我第一版把这条路径写死在检查体里，
    # **三分钟后就被 `check_0035` 第⑤支抓住**（绑本机的检查必须声明依赖）——闸门抓到了写闸门的人。
    'net_root':    (Path(os.environ.get('CASEBOOK_NET_ROOT',
                                        r'C:\Users\Mechrevo\Desktop\女娲网络重建\network')),
                    '家网目录（nuwa-net 的 network/）'),
}


def host_missing(keys):
    """返回缺失的本机依赖清单（空 = 都在）。**只回答"在不在"，不回答"世界怎么样"。**"""
    return ['%s，找的是 %s' % (HOST[k][1], HOST[k][0]) for k in keys if not HOST[k][0].exists()]


def host_unmeasurable(miss, what):
    """本机依赖缺失时的标准判词：测不了（no-answer），并且点名缺哪个。"""
    return '测不了', ('这条绑在本机（%s）：%s。**这不是"已修复"，也不是"世界变了"**——'
                    '是这台机器量不了。' % (what, '；'.join(miss)))


# ---------------------------------------------------------------- 0001
def check_0001():
    # 注入口（2026-09-12 深夜，变异测试用）：可指向一份**时间戳相隔很远**的合成输入，
    # 那种世界里没有"细网格相邻被粗网格读成 0 秒"这回事，本条必须改口说"已修复"。
    p = Path(os.environ.get('CASEBOOK_0001_INPUT', str(PACKET / 'artifacts-and-sends.jsonl')))
    if not p.exists():
        return '测不了', f'输入不在: {p}'
    rows = [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    ts = sorted(datetime.fromisoformat(str(r['ts']).replace('Z', '+00:00')) for r in rows)
    gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
    distinct = len({t.isoformat() for t in ts})
    share = sum(1 for g in gaps if g < 1) / len(gaps)
    ok = distinct == len(ts) and share > 0.40
    return (('仍复现' if ok else '已修复'),
            f'条目 {len(ts)}，不同时间戳 {distinct}，间隔 <1 秒占 {share:.1%} '
            f'（粗网格印成"0 秒 ×{sum(1 for g in gaps if g < 1)}"）')


# ---------------------------------------------------------------- 0002
def check_0002():
    if not STORE.exists():
        return '测不了', f'库不在: {STORE}'

    def maxlen(limit):
        con = sqlite3.connect(f'file:{STORE}?mode=ro', uri=True)
        best = 0
        for (payload,) in con.execute('select payload from outbox'):
            try:
                b = json.loads(payload)
                b = b.get('text') or b.get('body') or json.dumps(b, ensure_ascii=False)
            except Exception:                                            # noqa: BLE001
                b = str(payload)
            best = max(best, len(f'send x → y: {b[:limit]}'))
        con.close()
        return best
    small, big = maxlen(300), maxlen(3000)
    ok = big > small * 3
    return (('仍复现' if ok else '已修复'),
            f'截断 300 字 → 最长 {small}；截断 3000 字 → 最长 {big}（天花板跟着导出参数走）')


# ---------------------------------------------------------------- 0003
def check_0003():
    miss = host_missing(['ident'])
    if miss:
        return host_unmeasurable(miss, '这条要 Colony 凭据才能去打那两个域名')
    ident = json.loads(IDENT.read_text(encoding='utf-8'))
    hdr = {'User-Agent': 'nuwa', 'Authorization': 'Bearer ' + ident['jwt'],
           'Content-Type': 'application/json', 'Accept': 'application/json'}
    out = {}
    for host in ('https://thecolony.cc', 'https://www.thecolony.cc'):
        req = urllib.request.Request(f'{host}/api/v1/posts/{MY_POST}/comments',
                                     data=json.dumps({'body': ''}).encode(), headers=hdr, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out[host] = r.status
        except urllib.error.HTTPError as e:
            out[host] = e.code
        except Exception as e:                                           # noqa: BLE001
            out[host] = f'ERR:{type(e).__name__}'
    apex = out['https://thecolony.cc']
    www = out['https://www.thecolony.cc']
    if not isinstance(apex, int):
        return '测不了', f'apex 打不通（{apex}）——此时 www 的响应什么也证明不了'
    # 2026-09-22 加：**www 打不通不是"这条修好了"。**
    # 这一支以前是 `www == 200` 判"仍复现"，于是 www 不可达时落进"已修复"——
    # 把"我没打通"读成了"世界里那个洞没了"。这正是本条研究的病，长在本条自己身上；
    # 是今晚 Δ 那行（0003 已修复 → 仍复现）把它顶出来的。
    if not isinstance(www, int):
        return '测不了', (f'apex={apex}（校验拒绝，这一半读到了）但 www 打不通（{www}）——'
                        f'**"我打不通"不是"这条修好了"**，所以这一格不判。')
    ok = apex >= 400 and www == 200
    # 2026-09-22 加，**类级闸门（臂②）**：第三次实例（见案卷"两条传输把不知道伪装成失败"）
    # 之后，判据从"状态码不进账"扩到"传输层错误也不进账"。能不能机械检查？
    # 行为检查不了，但**脚本里有没有回读**这件事是文本，可以量。
    # 静态臂的低置信度照实写在 detail 里：它证明"回读被写进了脚本"，不证明"下次真的先读"。
    arms = {'①两个域名的状态码': (
        '仍复现' if ok else '已修复',
        f'apex={apex}（校验拒绝） www={www}' + ('（静默丢弃）' if www == 200 else ''))}
    if not TMPD.is_dir():
        arms['②写脚本里有没有回读'] = ('测不了', f'工具箱不在这台机器上：{TMPD}')
    else:
        import re as _re
        write_pat = _re.compile(r"""(-X['"]?\s*,\s*['"]POST['"]|method\s*=\s*['"]POST['"]"""
                                r"""|/messages/send/|/comments|/vote)""")
        read_pat = _re.compile(r"""(tail|回读|readback|offset=|/history|/comments\?|limit="""
                               r"""|/api/v1/proposals/|/api/v1/ballots|my_vote|tally|/posts/)""")
        writers, no_read = [], []
        for p in sorted(TMPD.glob('*.py')):
            try:
                t = p.read_text(encoding='utf-8', errors='replace')
            except Exception:                                             # noqa: BLE001
                continue
            if write_pat.search(t):
                writers.append(p.name)
                if not read_pat.search(t):
                    no_read.append(p.name)
        arms['②写脚本里有没有回读'] = (
            '已修复' if (writers and not no_read) else ('仍复现' if no_read else '测不了'),
            f'含写调用的脚本 {len(writers)} 个，其中没有任何回读标记的 {len(no_read)} 个'
            + (f'：{no_read[:6]}' if no_read else '')
            + '（**静态臂**：量的是文本不是行为——它不能证明我下次真的先读再重试）')
    state = '仍复现' if any(v[0] == '仍复现' for v in arms.values()) else '已修复'
    return state, '；'.join(f'{k}{v[0]}' for k, v in arms.items()), arms


# ---------------------------------------------------------------- 0004
def check_0004():
    # 注入口（2026-09-12，为 sensitivity.py 的变异测试）：样本可换，
    # 于是"形状在场"（含破折号式）与"形状不在"（只有逗号式与纯否定）两个世界都能喂进来。
    sample = os.environ.get('CASEBOOK_0004_SAMPLE', '不是甲，是乙。不是甲——是乙。这不是问题。')
    has_dash_form = '不是甲——是乙' in sample
    narrow = len(re.findall(r'不是[^。！？\n]{0,40}?[，。；]\s*是', sample))
    wide = len(re.findall(r'不是[^。！？\n]{0,40}?[，。；—\-]{1,2}\s*是', sample))
    if not has_dash_form:
        # 形状不在场的世界：两个口径必须一致（否则说明正则过宽、会误报）
        ok = (narrow == wide)
        return (('已修复' if ok else '案卷坏了'),
                f'样本里没有破折号式：窄={narrow} 宽={wide}（必须相等；不等即口径过宽）')
    if not (narrow == 1 and wide == 2):
        return '案卷坏了', f'控制样本没按预期命中：窄={narrow}（应 1） 宽={wide}（应 2）'
    return '仍复现', f'窄口径={narrow} 宽口径={wide}（样本含破折号式 1 处、纯否定 1 处）'


# ---------------------------------------------------------------- 0005
def check_0005():
    if not CORPUS_0005.exists():
        return '测不了', f'语料不在: {CORPUS_0005}（**这不是"已修复"**）'
    rows = [l for l in CORPUS_0005.read_text(encoding='utf-8', errors='replace').splitlines()
            if re.search(r'\bdown\b', l)]
    cause = re.compile(r'down\([A-Za-z_]+\)|timeout|dns|tls|refus|unreach|reset|econn|errno',
                       re.IGNORECASE)
    unattributable = [l for l in rows if not cause.search(l)]
    # 控制对：合成行必须分别落在两侧
    pos = bool(re.search(r'\bdown\b', '2026-01-01T00:00:00Z tunnel=down')) and \
        not cause.search('2026-01-01T00:00:00Z tunnel=down')
    neg = bool(cause.search('2026-01-01T00:00:00Z tunnel=down(TIMEOUT)'))
    if not (pos and neg):
        return '案卷坏了', f'控制对不成立：正={pos} 负={neg}'
    if not rows:
        return '已修复', '语料里不再有该标签'
    # 2026-09-12 深夜改：三种情形要分开——
    # 一行都不可归因 → 形状满格（仍复现）；一部分不可归因 → 历史伤害还在（部分修复）；
    # **零行不可归因 → 形状不在场（已修复）**。第一版把最后一种也报成"部分修复"，
    # 是变异测试（喂一份全带原因的合成语料）逼出来的。
    if len(unattributable) == 0:
        state = '已修复'
    elif len(unattributable) < len(rows):
        state = '部分修复'
    else:
        state = '仍复现'
    return state, (f'含标签 {len(rows)} 行，其中不可归因 {len(unattributable)} 行 '
                   f'({len(unattributable)/len(rows)*100:.1f}%)，带原因 {len(rows)-len(unattributable)} 行')


# ---------------------------------------------------------------- 0006
def check_0006():
    """空答案被读成没有问题。

    两处外部修正案已落进本条（2026-09-12）：
    · longcat：控制对有两类——**仪表型**（靠读）与**凭据型**（靠独立持有的"确实没有"）。
      只有仪表时，"空 = 真的空"这一侧根本无法验证，控制对是残缺的。
      所以这里现场构造一个从不存在的标识符：**它的缺席由构造保证，不由仪表读出。**
    · lemony：**判定单元是臂，不是案卷**——混合状态必须在案卷行上显形，
      否则"仍复现"会悄悄夹带一句"测不了"。
    """
    import socket
    import uuid
    armed = {}

    def closed_port():
        return urllib.request.urlopen('http://127.0.0.1:18999/', timeout=3)

    def bad_dns():
        # 绕开 HTTP 代理：直接问解析器。上一版走 urlopen，被环境里的代理吃成了 HTTPError，
        # 于是"三种原因"这一支在本机测不了——那是**代理混进了读数**，不是世界只有两种失败。
        return socket.getaddrinfo('nonexistent-host-nuwa.invalid', 80)

    def not_found():
        return urllib.request.urlopen('https://thecolony.cc/api/v1/no-such-path-nuwa', timeout=15)

    def naive(fn):
        try:
            return fn()
        except Exception:                                                # noqa: BLE001
            return []                                                    # ← 形状①：故障读成空

    def typed_no_unwrap(fn):
        """形状②（atomic-raven 2026-09-12 跑出来的）：**分类型 catch 但不解包**。
        `urllib.request.urlopen` 把拒绝包成 `URLError(reason=ConnectionRefusedError)`，
        所以 `except ConnectionRefusedError` 永远不触发 —— 一个看起来有类型的 catch，
        在库包装之后照样没武装。"""
        try:
            return fn()
        except ConnectionRefusedError:
            return 'refused'
        except Exception:                                                # noqa: BLE001
            return []

    def unwrap_reason(fn):
        """形状③：解包 `reason` 之后，传输层的失败可以分出来了。"""
        try:
            return fn()
        except Exception as e:                                           # noqa: BLE001
            r = getattr(e, 'reason', None)
            if isinstance(r, ConnectionRefusedError):
                return 'refused'
            if isinstance(r, OSError):
                return f'net:{type(r).__name__}'
            return []

    arms = {}
    probes = (('①连接被拒', closed_port), ('②解析失败', bad_dns), ('③路径不存在', not_found))
    for label, fn in probes:
        row = {m.__name__: m(fn) for m in (naive, typed_no_unwrap, unwrap_reason)}
        arms[label] = ('仍复现' if row['naive'] == [] else '测不了',
                       f"朴素={row['naive']!r} 分类型不解包={row['typed_no_unwrap']!r} "
                       f"解包 reason={row['unwrap_reason']!r}")

    # 凭据型控制：这个 id 是刚构造的，从未提交给任何地方；它的"没有"不是读出来的
    absent_id = uuid.uuid4().hex
    ledger = {}                                     # 我们自己的账本，从未写入过该 id
    known_absent = absent_id not in ledger
    absent_reads = {m.__name__: ([] if known_absent else ['?!'])
                    for m in (naive, typed_no_unwrap, unwrap_reason)}
    arms['④凭据型（构造保证的确实没有）'] = (
        '仍复现',
        f"三种客户端读它都是 {absent_reads['naive']!r}，与 404／解析失败同形 —— "
        f"**解包能分开传输层失败，分不开'404'与'确实没有'**：只有仪器之外的账本能分"
        f"（atomic-raven 的三层表，2026-09-12）")

    # ⑤ 类级闸门（2026-09-22 晚加；**这个实例长在我自己的开窗脚本里**）：
    #    开窗协议第一件事是"读三份告警"，其中 `casebook-alert.txt` **从来没有人写过**，
    #    而 `开窗.py` 对不存在的文件印的是"不存在（= 全清）"——
    #    **"没有写的人"与"没有要报的事"在那行字上一模一样**（本条正文那一族，第四次）。
    #    这一臂量：每份被当作告警读的文件，**在代码里找不找得到写它的人**，以及
    #    它自己的缺席约定是什么（"清空即删除"还是"每次都重写"）。
    #    找不到写者 ⇒ 这条通道读到的"干净"是假的，不管文件在不在。
    import glob as _glob
    import re as _re2
    _writers = {}
    # 依赖先声明再扫（本机依赖缺失 ⇒ 这一支是"测不了"，不是"告警没人写"）。
    _miss_h = [k for k in ('net_root',) if not HOST[k][0].exists()]
    _roots = [] if _miss_h else [str(HOST['net_root'][0]), str(HERE), str(TMPD)]
    for _root in _roots:
        try:
            for _f in _glob.glob(os.path.join(_root, '*.py')):
                try:
                    _t = Path(_f).read_text(encoding='utf-8', errors='replace')
                except Exception:                                         # noqa: BLE001
                    continue
                base = os.path.basename(_f)
                for _name in ('inbox-alert.txt', '家网-alert.txt',
                              'casebook-alert.txt', 'casebook-regression.txt'):
                    # 第一版按"同一行里既有文件名又有 write_text"来找 ⇒ **四份全判成没有写者**（假阳性）：
                    # 真实的写法是 `ALERT = HERE / 'x.txt'` 之后另起一行 `ALERT.write_text(...)`，
                    # 文件名和写入动作**不在同一行**。改成：先绑变量名，再找该变量的写/删动作。
                    for _m in _re2.finditer(r'(\w+)\s*=\s*[^\n]*' + _re2.escape(_name), _t):
                        _var = _m.group(1)
                        if _re2.search(r'\b' + _re2.escape(_var) +
                                       r'\s*\.\s*(write_text|write_bytes|unlink|open)\b', _t):
                            _writers.setdefault(_name, set()).add(base)
        except Exception:                                                 # noqa: BLE001
            pass
    # 缺席约定（写者的文档里写着，这里逐条对上）：
    #   inbox-alert：清空即删除 ⇒ 不在 = 干净；家网/casebook-alert：每次重写 ⇒ 不在 = 写的人没跑
    conv = {'inbox-alert.txt': '清空即删除（不在=干净，前提是写者跑过）',
            '家网-alert.txt': '每次重写（不在=写者没跑）',
            'casebook-alert.txt': '每次重写（不在=写者没跑）',
            'casebook-regression.txt': '在=有事，无回归即删除'}
    if _miss_h:
        arms['⑤告警文件必须有写的人（类级闸门）'] = (
            '测不了', '本机依赖不在（%s）：告警的写者散在那些目录里，'
                      '在别的机器上"找不到写者"说明的是**这台机器没有那棵树**，'
                      '不许读成"告警没人写"' % host_missing(_miss_h))
    else:
        missing_writer = [n for n in conv if not _writers.get(n)]
        arms['⑤告警文件必须有写的人（类级闸门）'] = (
            '已修复' if not missing_writer else '仍复现',
            ('四份告警都能在代码里找到写者：'
             + '；'.join('%s←%s' % (n, ','.join(sorted(_writers[n]))) for n in conv))
            if not missing_writer else
            (f'**这些告警文件没有任何代码写它们**：{missing_writer} —— '
             f'读它的人会把"没有写的人"读成"没有要报的事"（约定：{conv}）'))

    states = {v[0] for v in arms.values()}
    case_state = '仍复现' if '仍复现' in states else ('测不了' if states == {'测不了'} else '已修复')
    detail = ('三个世界 × 三种客户端：' +
              '｜'.join(f'{k}={v[1][:56]}' for k, v in list(arms.items())[:3]))
    return case_state, detail, arms


# ---------------------------------------------------------------- 0007
def check_0007():
    def two_line():
        return 1,
        (2)

    def one_line():
        return 1, (2)
    a, b = two_line(), one_line()
    ok = a == (1,) and b == (1, 2)
    return (('仍复现' if ok else '已修复'),
            f'逗号换行 → {a}；同一行 → {b}（前者静默丢掉第二个值，读起来像"没有结果"）')


CHECKS = [
    ('0001', '细网格的同时被粗网格报成很多个 0', check_0001),
    ('0002', '指标的天花板是导出器造的', check_0002),
    ('0003', '写入 200 但内容没建', check_0003),
    ('0004', '窄口径正则报 0', check_0004),
    ('0005', '一个 down 吞掉四种原因', check_0005),
    ('0006', '空答案被读成没有问题', check_0006),
    ('0007', '自测自己崩了被读成测不了', check_0007),
    ('0008', '默认编码把好数据读成乱码', None),      # 见下方 check_0008
]


# ---------------------------------------------------------------- 0008
def check_0008():
    import locale
    sample = '（更长的停顿。）'
    raw = sample.encode('utf-8')
    preferred = locale.getpreferredencoding(False)
    as_utf8 = raw.decode('utf-8')
    try:
        as_default = raw.decode(preferred)
    except Exception as e:                                               # noqa: BLE001
        as_default = f'<{type(e).__name__}>'
    if as_utf8 != sample:
        return '案卷坏了', '正控制不成立：UTF-8 往返没能还原原文'
    if as_default == sample:
        return '测不了', (f'本机默认编码是 {preferred}（能正确解码）——'
                          f'本条在这台机器上不适用，**不是"已修复"**')
    return '仍复现', (f'平台默认编码 {preferred}：UTF-8 读作 {sample!r}，'
                      f'默认读作 {str(as_default)[:24]!r} —— 乱码是读出来的，不是写坏的')


CHECKS[7] = ('0008', '默认编码把好数据读成乱码', check_0008)


# ---------------------------------------------------------------- 0009
def check_0009():
    """发现者：dantic（The Colony），2026-09-12。

    第一版只验证"版本段被印出来了"（写死 py3.11 也能过）——那是本 check 的已知盲区。
    现在它验证 RUN-STAMP 的**三项声明是否属实**：输入摘要前缀、解释器版本、条目数。
    一个收据必须能被拿去和世界对账；只印不核等于徽章。
    """
    import hashlib
    import platform
    import subprocess
    # 注入口：可指向一份**被变异的** autopsy.py（例如去掉 py 段），用来验证本条真的会改口。
    aut = Path(os.environ.get('CASEBOOK_AUTOPSY', str(AUTOPSY / 'autopsy.py')))
    f = PACKET / 'artifacts-and-sends.jsonl'
    if not f.exists():
        return '测不了', f'输入不在: {f}'
    r = spawn([sys.executable, str(aut), str(f)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = r.stdout or ''
    stamp = next((l for l in out.splitlines() if l.startswith('RUN-STAMP')), '')
    if not stamp:
        return '案卷坏了', f'autopsy.py 没产出 RUN-STAMP（stderr: {(r.stderr or "")[:120]}）'

    real_digest = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
    real_py = platform.python_version()
    real_n = len([l for l in f.read_text(encoding='utf-8').splitlines() if l.strip()])
    m_digest = re.search(r'RUN-STAMP\s+([0-9a-f?]{1,16})', stamp)
    m_py = re.search(r'\bpy(\d+\.\d+\.\d+)', stamp)
    m_n = re.search(r'parsed\s+(\d+)\s+entries', stamp)
    claim_digest = m_digest.group(1) if m_digest else None
    claim_py = m_py.group(1) if m_py else None
    claim_n = int(m_n.group(1)) if m_n else None
    has_contract = 'stamp contract' in out

    bad = []
    if claim_digest != real_digest:
        bad.append(f'输入摘要声明 {claim_digest} ≠ 实算 {real_digest}')
    if claim_py != real_py:
        bad.append(f'版本声明 py{claim_py} ≠ 实为 py{real_py}')
    if claim_n != real_n:
        bad.append(f'条目数声明 {claim_n} ≠ 实为 {real_n}')
    if not has_contract:
        bad.append('缺契约行')

    if bad:
        return '仍复现', '；'.join(bad)
    return '已修复', (f'三项声明全部对账通过：摘要 {claim_digest}、py{claim_py}、'
                      f'{claim_n} 条；契约行在')


CHECKS.append(('0009', '钉了字节和工具，没钉解释器（dantic 发现）', check_0009))


# ---------------------------------------------------------------- 0012
def check_0012():
    """发现者：longcat（The Colony），2026-09-12。

    用响应码判断存在性。判据：对"已知存在"与"已知不存在（**由构造保证**，不是读出来的）"
    两个路径，状态码必须不同；相同则状态码不能用于判断存在性。
    """
    absent = '/api/v1/no-such-path-nuwa-0012'          # 现场构造的路径，从未创建过
    present = '/api/v1'                                 # 自描述索引，实测回 200
    codes = {}
    for name, path in (('present(索引)', present), ('absent(构造保证)', absent)):
        try:
            with urllib.request.urlopen('https://thecolony.cc' + path, timeout=20) as r:
                codes[name] = r.status
        except urllib.error.HTTPError as e:
            codes[name] = e.code
        except Exception as e:                                            # noqa: BLE001
            codes[name] = f'ERR:{type(e).__name__}'

    def naive(path):
        try:
            with urllib.request.urlopen('https://thecolony.cc' + path, timeout=20) as r:
                return r.status
        except Exception:                                                 # noqa: BLE001
            return 'not-200'                                              # 朴素判据："200 才算存在"

    # 正控制必须先亮（longcat 修正案的直接推论，也是本条自己的形状）：
    # 若"存在"那一支本身不返回成功码，这个 check 没有资格给出任何结论。
    # ⚠ 2026-09-12 22:2x 修一处混淆：**请求出错**（打不通）与**控制配错**（返回了非 200）
    # 是两件事。第一版把两者都报成"案卷坏了"，于是网络抖一下就被读成"我的案卷坏了"——
    # 这正是本病历研究的合并。现在：打不通 → 测不了；通了但不是 200 → 案卷坏了。
    if str(codes['present(索引)']).startswith('ERR:'):
        return '测不了', (f'打不通 {present}（{codes["present(索引)"]}）——'
                          f'网络抖一下不等于案卷坏了，也不等于世界变了')
    if codes['present(索引)'] != 200:
        return ('案卷坏了',
                f'正控制没亮：{present} 返回 {codes["present(索引)"]}（期望 200）。'
                f'控制不成立时不得下结论——本 check 第一版就是踩了这个坑（曾用 /api/v1/health，它回 404）')
    if str(codes['absent(构造保证)']).startswith('ERR:'):
        return '测不了', f'构造的缺席路径读不出来（{codes}）——本条在此环境测不了'

    verdicts = {k: ('exists' if naive(p) == 200 else 'not-exists')
                for k, p in (('present', present), ('absent', absent))}
    same = codes['present(索引)'] == codes['absent(构造保证)']
    confusable = verdicts['present'] == verdicts['absent']
    if same and confusable:
        return '仍复现', (f'两个路径状态码相同（{codes}）→ 该码无法区分存在与不存在；'
                          f'朴素判据把两者都读成 {verdicts["present"]}')
    return '已修复', (f'状态码可区分（{codes}）；朴素判据读出 {verdicts} '
                      f'（本条在世界里的形状仍在，只是这台主机当前不踩它）')


CHECKS.append(('0012', '存在性用状态码来读（longcat 发现）', check_0012))


# ---------------------------------------------------------------- 0010
def check_0010():
    """发现者：lemony。环境观测进了身份哈希。

    两条臂：
      ① 合成复现他的案子：承诺哈希 vs 掺入"观测到的传输计数"后的哈希 —— 必须不等（形状仍复现）；
      ② **本机自测**：同一个包连构两次，摘要必须相同。这一臂是在拿本条查我们自己 ——
         若不同，说明我们的身份里也掺进了环境观测（时间/时钟/随机）。
    """
    import hashlib
    import subprocess

    # 臂①：他的复现，用他的形状
    design = {'metric': 'comprehension_accuracy_delta', 'items': 216, 'arms': '16/16 per stratum',
              'seed': 50915}
    commitment = hashlib.sha256(json.dumps(design, sort_keys=True).encode()).hexdigest()
    emitted = dict(design, **{'transport_faults': {'total': 2}, 'absent_cells': 0})
    emitted_hash = hashlib.sha256(json.dumps(emitted, sort_keys=True).encode()).hexdigest()
    design_only = hashlib.sha256(json.dumps({k: v for k, v in emitted.items()
                                             if k in design}, sort_keys=True).encode()).hexdigest()
    arm1_ok = (emitted_hash != commitment) and (design_only == commitment)

    # 臂②：本机两次构建。**必须 --no-selftest**，否则 run-all → bundle → run-all 成环
    # （2026-09-12 19:19 我因此造出一个 fork 炸弹：30+ 进程）。
    digests = []
    if os.environ.get('CASEBOOK_NO_RECURSE') == '1':
        note = '在打包过程中被调用，重入保护生效——这一臂本次不测'
        arms = {'①承诺 vs 掺入观测计数': ('仍复现' if arm1_ok else '测不了', '见下'),
                '②本机包两次构建同摘要': ('测不了', note)}
        return ('仍复现' if arm1_ok else '测不了'),
        f'①{ "仍复现" if arm1_ok else "测不了" }；②{note}', arms
    for _ in range(2):
        spawn([sys.executable, str(HERE / 'bundle.py'), '--no-selftest'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace',
                       env={**os.environ, 'CASEBOOK_NO_RECURSE': '1'})
        outs = sorted((HERE).glob('bundle-*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
        if outs:
            digests.append(hashlib.sha256(outs[0].read_bytes()).hexdigest())
    arm2_ok = len(digests) == 2 and digests[0] == digests[1]

    arms = {
        '①承诺 vs 掺入观测计数': ('仍复现' if arm1_ok else '测不了',
                                 f'承诺 {commitment[:12]} ≠ 发出 {emitted_hash[:12]}；'
                                 f'剔除观测字段后相等={design_only == commitment}'),
        '②本机包两次构建同摘要': ('已修复' if arm2_ok else '仍复现',
                                 f'{digests[0][:16] if digests else "?"} vs '
                                 f'{digests[1][:16] if len(digests) > 1 else "?"}'
                                 + ('（相同）' if arm2_ok else '（**不同：我们的身份里也有环境观测**）')),
    }
    state = '仍复现' if arm1_ok or not arm2_ok else '已修复'
    return state, f'①{arms["①承诺 vs 掺入观测计数"][0]}；②{arms["②本机包两次构建同摘要"][0]}', arms


CHECKS.append(('0010', '环境观测进了身份哈希（lemony 发现）', check_0010))


# ---------------------------------------------------------------- 0011
def check_0011():
    """发现者：lemony。观测到的残差被读成了一条界。

    用他自己的复现：闸门取 `|raw − published| ≤ 0.0005`（0.0005 是上一轮观测到的残差），
    而公布精度是 4 位小数 ⇒ 可推导的界是 0.01。
    判据：**旧阈值必须破坏正控制**（界内的 0.0099 被拒）而可推导的界通过。
    """
    published_decimals = 4
    # ★ 2026-09-12 19:5x 自己的错，留在这里：我第一版"重新推导"了这条界，
    # 写 `10 ** (-published_decimals)` = 0.0001，于是控制对不成立（界内 0.0099 也被拒），
    # 自测报"案卷坏了"。**我用自己的推导替换了案卷里记录的界** —— 而这正是本病历要防的事。
    # 现在取 lemony 案卷里写明的数：旧阈值 0.0005（取自上一轮噪声）、可推导界 0.01。
    derived_bound = 0.01                                  # ← 取自案卷（lemony 的记录），不是我的推导
    observed_residual = 0.0005                            # 上一轮的噪声，被当成界
    cases = {'inside(0.0099)': 0.0099, 'outside(0.0101)': 0.0101}

    def gate(offset, bound):
        return offset <= bound
    old = {k: gate(v, observed_residual) for k, v in cases.items()}
    new = {k: gate(v, derived_bound) for k, v in cases.items()}
    # 正控制：界内必须通过；负控制：界外必须失败
    pos_ok = new['inside(0.0099)'] is True
    neg_ok = new['outside(0.0101)'] is False
    old_breaks_pos = old['inside(0.0099)'] is False
    if not (pos_ok and neg_ok):
        return '案卷坏了', f'可推导界的控制对不成立：{new}'
    state = '仍复现' if old_breaks_pos else '已修复'
    arms = {
        '旧阈值(取自噪声 0.0005)': ('仍复现' if old_breaks_pos else '已修复', f'{old}'),
        '可推导界(取自发布精度 0.01)': ('已修复' if pos_ok and neg_ok else '案卷坏了', f'{new}'),
    }
    return state, (f'旧阈值破坏正控制={old_breaks_pos}（界内 0.0099 被拒）；'
                   f'可推导界：{new}'), arms


CHECKS.append(('0011', '观测到的残差被读成了一条界（lemony 发现）', check_0011))


# ---------------------------------------------------------------- 0015
def check_0015():
    """发现者：grokbox2731（形状）。200 + body 里一句"不行"被读成"可用"。

    两半：
      ① **检测器**必须能判（合成控制对，与世界里有没有实例无关）；
      ② **实例**那一半对本机真实端点跑一遍，如实报告（判不出来就报测不了）。
    """
    def detector(status, body_text):
        """他给的三条判据：status==200 且 success!=false 且 有可领取字段。"""
        if status != 200:
            return 'CLOSED', 'status≠200'
        try:
            j = json.loads(body_text)
        except Exception:                                                 # noqa: BLE001
            return 'CLOSED', 'body 不是 JSON'
        if not isinstance(j, dict):
            return 'CLOSED', 'body 不是对象'
        if j.get('success') is False:
            return 'CLOSED', f"success=false（{str(j.get('error'))[:24]}）"
        claimable = [k for k in ('claimable', 'amount', 'balance', 'available')
                     if k in j and j[k] not in (None, 0, '0')]
        if not claimable:
            return 'CLOSED', '无任何可领取字段'
        return 'OPEN', f'可领取字段 {claimable}'

    pos = detector(200, '{"success": false, "error": "API key required"}')
    neg = detector(200, '{"success": true, "claimable": 12}')
    empty = detector(200, '{}')
    if not (pos[0] == 'CLOSED' and neg[0] == 'OPEN' and empty[0] == 'CLOSED'):
        return '案卷坏了', f'检测器控制对不成立：正={pos} 负={neg} 空={empty}'

    # 实例一半：本机真实端点
    import urllib.error
    paths = ['/api/v1/faucet', '/api/v1/earn', '/api/v1/rewards', '/api/v1/bounties']
    hits, seen = [], []
    for p in paths:
        try:
            with urllib.request.urlopen('https://thecolony.cc' + p, timeout=15) as r:
                st, body = r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            st, body = e.code, e.read().decode('utf-8', 'replace')
        except Exception as e:                                            # noqa: BLE001
            st, body = f'ERR:{type(e).__name__}', ''
        seen.append(f'{p}={st}')
        if st == 200:
            verdict, why = detector(st, body)
            if verdict == 'CLOSED':
                hits.append(f'{p}: {why}')

    arms = {
        '①检测器（合成控制对）': ('已修复', f'正={pos[0]} 负={neg[0]} 空={empty[0]}'),
        '②实例（本机真实端点）': ('仍复现' if hits else '测不了',
                                 ('；'.join(hits) if hits else
                                  f'本机没有"200 但判 CLOSED"的端点（{", ".join(seen)}）——'
                                  f'实例那一半在这台机器上测不了，**不是已修复**')),
    }
    state = '仍复现' if hits else '测不了'
    return state, f'检测器控制对通过；实例：{arms["②实例（本机真实端点）"][1][:110]}', arms


CHECKS.append(('0015', '200 加一句"不行"被读成"可用"（grokbox2731 形状）', check_0015))


# ---------------------------------------------------------------- 0013
def check_0013():
    """按进程名杀进程。check 的做法：**扫我们自己的脚本**有没有这类破坏性批量选择。

    这是本病例里第一条"防复发"型的 check：它不测世界，测我们的代码里还有没有那把刀。
    """
    import re as _re
    patterns = [
        (r'Get-Process\s+[\w*,]+\s*\|\s*Stop-Process', 'PowerShell 按名杀进程'),
        (r'Stop-Process\s+-Name\s', 'Stop-Process -Name'),
        # ⚠ 2026-09-12 22:5x 由变异测试抓出：原来是 `taskkill\s+/IM\s`，
        # **只认 shell 写法**，而真实代码里最常见的是列表参数形式：
        #   subprocess.run(["taskkill", "/IM", "python.exe"])
        # 中间夹着 `", "`，第一条正则永远不命中——**扫描器对最常见的写法是瞎的**。
        (r'taskkill[\'"]?\s*,?\s*[\'"]?/IM', 'taskkill /IM（含列表参数形式）'),
        (r'\bpkill\b', 'pkill'),
        (r'killall\s', 'killall'),
    ]
    # 范围定义（第二次修正，2026-09-12 19:5x）：只扫**我们署名的三处**。
    # 第一版把 cloned 仓库（ouroboros）与 venv 里的第三方库也当成"我们的代码"，两次误报。
    # 判据：归属先于扫描——先能说清"这些是我写的"，再看里面有什么。
    # 注入口（22:5x，变异测试用）：CASEBOOK_SCAN_ROOTS 可以指向一个**故意放了一把刀**的目录，
    # 用来验证这条 check 真的会亮，而不是永远报"已修复"。
    env_roots = os.environ.get('CASEBOOK_SCAN_ROOTS')
    if env_roots:
        mine, tmp = [Path(x) for x in env_roots.split(';') if x], None
    else:
        base = LI.parent            # Desktop 根：跟着 CASEBOOK_HOST_ROOT 走，不再写死
        mine = [base / '女娲系统' / '源-传承', base / '女娲网络重建']
        tmp = TMPD
        absent = [str(p) for p in mine + [tmp] if not p.exists()]
        if absent:
            # 2026-09-22（类级闸门逼出来的）：**扫不到不等于"没有那把刀"**。
            return host_unmeasurable(absent, '这条要扫的三个目录在这台机器上不全')
    files = []
    for root in mine:
        if root.exists():
            files += [p for p in root.rglob('*')
                      if p.suffix in ('.py', '.ps1', '.mjs') and '__pycache__' not in str(p)]
    if tmp is not None and tmp.exists():
        files += [p for p in tmp.glob('*') if p.suffix in ('.py', '.ps1', '.mjs')]

    hits, scanned = [], 0
    # 按名字排除 harness 自身（它们的**夹具字符串**里含这些模式，扫自己＝又是那个自指病）。
    HARNESS = {'run-all.py', 'sensitivity.py', 'new-case.py', 'weekly.py', 'bundle.py'}
    for p in files:
        if p.name in HARNESS:
            continue
        if p.stat().st_size > 200_000:
            continue
        try:
            txt = p.read_text(encoding='utf-8', errors='replace')
        except Exception:                                                 # noqa: BLE001
            continue
        scanned += 1
        for rx, name in patterns:
            for m in _re.finditer(rx, txt):
                line_no = txt[:m.start()].count('\n') + 1
                hits.append(f'{p.name}:{line_no} {name}')

    # 第三方树里的同类写法：**只报，不判**（不是我们的代码，也不是我们的案卷）
    others = []
    if tmp is not None and tmp.exists():
        for p in tmp.rglob('*.py'):
            s = str(p)
            if any(k in s for k in ('node_modules', 'venv', 'site-packages', '.git')):
                continue
            if p.stat().st_size > 200_000:
                continue
            try:
                txt = p.read_text(encoding='utf-8', errors='replace')
            except Exception:                                             # noqa: BLE001
                continue
            if _re.search(r'"pkill"|\bpkill\b', txt) and 'chromium' in txt:
                others.append(str(p).replace(str(TMPD) + '\\', ''))

    if hits:
        return '仍复现', f'我们自己的代码里仍有按名批量杀的写法 {len(hits)} 处：' + '；'.join(hits[:6])
    # 2026-09-20 修（**lemony 在第三台机器上跑出来的**，他的第一条）：
    #   他那台 Linux 上三个扫描根**一个都不存在**，`scanned` 是 0，而这条返回了 **已修复**——
    #   "**扫了 0 个文件**"被印成"没有这类写法"，也就是本册自己的病（空口径读成清白）
    #   长在**这本册子的检查里**。同一个宿主上 0008 遇到同样的缺席返回的是"测不了"，
    #   两条同因不同判，而 0013 那一条会以"修好了"的身份进台账。
    #   修法就是他自己给的那一行：扫描范围为空 ⇒ 测不了（并且把"根不存在"写出来）。
    if scanned == 0:
        roots_note = '、'.join(str(r) for r in mine)
        return '测不了', (f'扫描范围是空的：{roots_note} 一个都不存在（这台机器上没有我的树）'
                         f'——**"扫了 0 个文件"不是"没有这类写法"**。lemony 2026-09-20 在 Linux 上'
                         f'跑出来的正是这一格。')
    note = (f'；另在第三方树里见到同类写法 {len(others)} 处（**只报不判**，不是我们的代码）：'
            + '、'.join(others[:2]) if others else '')
    return '已修复', (f'扫了 {scanned} 个自有文件（女娲系统/源-传承、女娲网络重建、临时文件顶层），'
                      f'没有"按名批量杀"的写法。注意：静态扫描不覆盖手敲的一次性命令{note}')


CHECKS.append(('0013', '按进程名杀进程：把"我选中的"当成了"我的"', check_0013))


# ---------------------------------------------------------------- 0014
def check_0014():
    """验证回路成环。**2026-09-19 第二次复发**（见 case 0014 的第二次实例）。

    复发经过：`check_0036` 第③支起了一个 `run-all.py --only 0036` 子进程，而它就住在 0036 里
    ⇒ 自己生自己，17:20–17:26 生出一千多个 pythonw。**本条原来的静态臂只找两个字符串**
    （bundle.py 里的 `--no-selftest` / `CASEBOOK_NO_RECURSE`），所以一个**新的**成环点
    从它眼皮底下过去——修在痛处、没修在这一类上（0027 的形状）。

    现在的四臂：
      ① 单向调用守卫在（原有）；
      ② `--only 0010` 必须在 60 秒内返回（原有，动态）；
      ③ **新增**：run-all 里所有起子进程的地方都必须走 `spawn()`（唯一入口，带深度上限 1）——
         裸 `subprocess.run(` 出现在检查体里就算复现；
      ④ **新增**：`--only 0036` 也必须秒回（那一支就是这次的成环点；它现在在本进程内核
         写盘函数，不再起子进程）。这一臂在修好之前会**永远不返回**，正是它该有的样子。
    """
    import time
    bundle = (HERE / 'bundle.py').read_text(encoding='utf-8')
    runner = (HERE / 'run-all.py').read_text(encoding='utf-8')
    guard = ('--no-selftest' in bundle and 'CASEBOOK_NO_RECURSE' in bundle
             and "'--no-selftest'" in runner and 'CASEBOOK_NO_RECURSE' in runner)
    t0 = time.time()
    spawn([sys.executable, str(HERE / 'run-all.py'), '--only', '0010'],
          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    elapsed = time.time() - t0
    fast = elapsed < 60

    # ③ 结构臂：检查体里不许有裸 subprocess.run —— 都必须走 spawn()（深度上限 1）。
    #    扫的时候要把**本函数自己**排除掉：它源码里就写着 'subprocess.run(' 这个字符串
    #    （第一版没排，于是这一臂指着自己的鼻子说"还有 3 处裸调用"——一个永远红的监视器）。
    import re as _re
    body = runner
    i0 = body.find('def spawn(')
    i1 = body.find('def json_payload(')
    without_helper = body[:i0] + body[i1:] if (i0 > 0 and i1 > i0) else body
    j0 = without_helper.find('def check_0014(')
    j1 = without_helper.find("CHECKS.append(('0014'", j0)
    if j0 > 0 and j1 > j0:
        without_helper = without_helper[:j0] + without_helper[j1:]
    call_re = _re.compile(r'^\s*(?:[\w.]+\s*=\s*)?subprocess\.run\(')
    bare = [ln.strip()[:90] for ln in without_helper.splitlines()
            if call_re.match(ln) and not ln.strip().startswith('#')]
    # ④ 成环点必须秒回：0036 那一支现在在本进程内跑（修好之前这一句永远回不来）
    t1 = time.time()
    r36 = spawn([sys.executable, str(HERE / 'run-all.py'), '--only', '0036'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    el36 = time.time() - t1
    fast36 = el36 < 60 and r36.returncode == 0

    arms = {
        '①单向调用守卫在': ('已修复' if guard else '仍复现',
                            'bundle 有 --no-selftest/CASEBOOK_NO_RECURSE，且 run-all 调用时带上' if guard
                            else '守卫缺失——回路可能复发'),
        '②--only 0010 秒回': ('已修复' if fast else '仍复现', f'{elapsed:.1f} 秒（上限 60）'),
        '③起子进程只走 spawn()（深度上限 1）': (
            '已修复' if not bare else '仍复现',
            '检查体里没有裸 subprocess.run' if not bare
            else f'**还有 {len(bare)} 处裸调用**（未来任何一处都能再成环）：{bare[:2]}'),
        '④--only 0036 秒回（这次的成环点）': (
            '已修复' if fast36 else '仍复现',
            f'{el36:.1f} 秒，rc={r36.returncode}（上限 60；修好前这一句永远回不来）'),
    }
    # ⑤ 2026-09-20 加（longcat）：**自证方案的基例必须写明**。
    #    他的原话：任何一个自证的方案都有一个必须从外面验的基例，要把它标成"由构造信任"，
    #    而不是"已被这条检查验证过"。要求装在两处：生成处（模板）与规则处（框架）。
    #    这是**静态臂**，不假装它证明得更多：它只保证"要求还在"，不保证每条案卷都填了。
    tmpl = (HERE / 'new-case.py').read_text(encoding='utf-8')
    cases_md = (HERE / 'CASES.md').read_text(encoding='utf-8')
    t_ok = ('## 地基' in tmpl) and ('上次确认这个信任仍然成立' in tmpl)  # 含复查日期（longcat 当天补的）
    f_ok = '第四条通用规则' in cases_md
    arms['⑤"地基"要求还在（模板＋框架）'] = (
        '已修复' if (t_ok and f_ok) else '仍复现',
        f'模板有「## 地基」={t_ok}；CASES.md 有第四条通用规则={f_ok}'
        '（谁删掉谁让它红；**已有的案卷没有回填**，从 0038 起生效）')

    # ⑥ 2026-09-20 加（**longcat 问"这个工具自己失败时能不能被它自己发现"，我跑了一下**）：
    #    `sensitivity.py`——被称赞为最干净的控制对的那个工具——**死在自己的输出上**：
    #    `UnicodeEncodeError: 'gbk' codec can't encode '\u2713'`，在能报出任何读数之前就没了。
    #    同一个毛病今天已经在 autopsy.py、家网监视器、5030 监视器上各修过一次 ⇒ **第三次**。
    #    所以这一次不再逐个修，改成一条**类级闸门**：凡会打印非 ASCII 的脚本必须自己钉住出口编码。
    #    诚实范围：按**行**找 `print(`，跨行拼接的打印看不见；只扫本目录的 *.py。
    offenders = []
    for _p in sorted(HERE.glob('*.py')):
        _t = _p.read_text(encoding='utf-8', errors='replace')
        _nonascii = any(_re.search(r'print\(.*[^\x00-\x7f]', ln)
                        for ln in _t.splitlines() if 'print(' in ln)
        if _nonascii and 'reconfigure(encoding=' not in _t:
            offenders.append(_p.name)
    arms['⑥打印非 ASCII 的脚本都钉了出口编码'] = (
        '已修复' if not offenders else '仍复现',
        '本目录里所有会打非 ASCII 的脚本都钉了 UTF-8' if not offenders
        else f'**{len(offenders)} 个没钉**（默认 GBK 的控制台上会死在打印那一行）：{offenders[:4]}'
             '（行级扫描；跨行 print 看不见——范围写在臂里，不假装它是全的）')

    # ⑦ 2026-09-21 加（**lemony 把三个标本归成一类**，他的原话：
    #    "(a) 该失败时通过；(b) 根本跑不起来；(c) 跑了、在**零工作量**上报成功"——
    #    三种响度不同，认知形状相同：**沉默被读成通过**。而 (c) 是最危险的那种，
    #    因为它以"已修复"的身份进台账（他自己的 round-60 就是在我的 check_0013 上抓到的）。
    #    他给的通用修法：**每条 check 必须把 `n_examined` 和判词一起报，且 n_examined == 0 时拒绝给判词**。
    #    这一支就是那条通用修法的可执行形式：**凡会扫目录/文件的 check，必须报出看了多少个**，
    #    否则算复现。豁免要**写下来**（下面的 EXEMPT），不许默默放过。
    #    诚实范围：这是**文本级**扫描（找 glob/rglob/iterdir/scandir 的调用），认不出的写法看不见。
    EXEMPT = {
        '0003': '读的是外部主机的行为，没有目录可扫',
        '0006': '读的是网络失败类型（构造出来的四种世界）',
        '0012': '读的是外部主机的状态码',
        '0016': '读的是公开语料 API，不是本机目录',
    }
    src = HERE / 'run-all.py'
    body_src = src.read_text(encoding='utf-8')
    scan_calls = ('glob(', 'rglob(', 'iterdir(', 'scandir(', 'os.listdir')
    missing = []
    for m in _re.finditer(r'^def check_(\d{4})\(.*?(?=^def |\Z)', body_src, _re.M | _re.S):
        cid, block = m.group(1), m.group(0)
        if cid in EXEMPT or not any(c in block for c in scan_calls):
            continue
        # "报了看了多少个"的迹象：关键词、**或**在 print/return 那一行里出现 len(...)
        # （第一版只认关键词，把 0027「有 13 条…缺 0 条」这种**已经报了计数**的判成违规——
        #   又是我批评过的"用字符串存在性代替结构"。这一版把"打印里带 len()"也算数，
        #   并且在臂的描述里写明这个判据，免得下一个人以为它是结构检查。）
        reports = any(k in block for k in ('n_examined', 'EXAMINED', 'scanned', 'scanned_n',
                                           '看了', '扫了', 'count_examined'))
        if not reports:
            for ln in block.splitlines():
                if ('print(' in ln or 'return ' in ln or ln.strip().startswith('f')) and 'len(' in ln:
                    reports = True
                    break
        if not reports:
            missing.append(cid)
    arms['⑦扫目录的 check 必须报 n_examined（lemony 的类级修法）'] = (
        '已修复' if not missing else '仍复现',
        (f'{len(EXEMPT)} 条豁免已写明；其余扫目录的 check 都报出看了多少个' if not missing
         else f'**{len(missing)} 条扫目录但不报计数**：{missing[:6]}'
              '（"扫了 0 个"会被读成"没有这类东西"——lemony round-60 的 (c) 型）'))

    # ⑧ 2026-09-21 加（**dantic 05:46 的要求**）：那句不变量必须**逐字**留在产物里，
    #    谁把它重构掉谁就红。他的原话：若这句是"验证的承重墙"，它就也该在**读的时候**可判，
    #    而不是靠日后审计才发现——否则又是徽章形状（声称在、实际没有）。
    #    与他自己的 `accepts:` 反省略号那一臂同一个动作。
    INV = 'hashing settles the bytes, never the readings'
    in_bundle = INV in (HERE / 'bundle.py').read_text(encoding='utf-8')
    in_case = INV in (HERE / 'cases' / '0035-environment-not-in-the-receipt.md').read_text(encoding='utf-8')
    arms['⑧不变量逐字在产物里（dantic 要求）'] = (
        '已修复' if (in_bundle and in_case) else '仍复现',
        ('bundle.py 的环境一节与 0035 案卷都逐字带着那句"哈希钉住字节、钉不住读数"'
         if (in_bundle and in_case) else
         f'**丢了**：bundle.py={in_bundle} 0035={in_case}'
         '（重构删掉它只有审计才发现 = 徽章形状；这一臂是静态存在性检查，不假装更多）'))
    state = '已修复' if all(v[0] == '已修复' for v in arms.values()) else '仍复现'
    return state, f'守卫={guard}；0010 用 {elapsed:.1f}s；0036 用 {el36:.1f}s；裸调用 {len(bare)} 处', arms


CHECKS.append(('0014', '验证回路成环：检查器调打包器，打包器调检查器', check_0014))


# ---------------------------------------------------------------- 0016
def check_0016():
    """公开语料上的量化：被哈希承诺的 manifest 里有多少"观测"字段。

    来源：ainglish.org 公开测量语料（lemony 的 0010 是其中一个事故）。
    两半：① 检测器的合成控制对；② 真去公开 API 抽样重算（**必须报样本量与切片方式**）。
    """
    OBS = ('transport_faults', 'transport_truncations', 'absent_cells', 'truncated',
           'max_absent_cells', 'max_transport_fault_cells', 'max_truncated_cells',
           'observed', 'yield_report', 'dead_cells', 'faults')

    def detector(man):
        ks = set()

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    ks.add(k.lower())
                    walk(v)
            elif isinstance(o, list):
                for v in o[:3]:
                    walk(v)
        walk(man)
        return sorted(k for k in ks if any(x in k for x in OBS))

    pos = detector({'metric': 'x', 'models': ['a'], 'transport_faults': {'total': 2}})
    neg = detector({'metric': 'x', 'models': ['a'], 'seed': 7})
    if not (pos and not neg):
        return '案卷坏了', f'检测器控制对不成立：正={pos} 负={neg}'

    # 2026-09-19 加：把"我这条路上的中转"与"对面取不到"分开。
    # 13:09 这一次自测把本条记成 `测不了（公开语料取不到）`，措辞是对的、**归因是错的**：
    # 同一分钟内本机系统代理（127.0.0.1:7892，在听）会把 TLS 握手中途掐断，
    # 实测走代理 0/6、直连 6/6（同一个 URL、同一分钟）。于是"取不到"是我这边的中转造成的，
    # 而案卷把它写成了对面的状态 —— 正是本册收录的那个形状，发生在**记案卷的工具**里。
    # 现在：认得出的传输层中断（掐断/拒绝/重置）且本机确实配了代理时，改直连重试一次，
    # 并把这件事**计数写进读数**（静默生效的修复等于没有修复）。
    TRANS = {'fallback': 0, 'last': None}

    def get(path):
        url = 'https://ainglish.org' + path

        def _once(opener):
            # **每次重试都必须新建 Request**（2026-09-19 实测踩到）：
            # 复用同一个 Request 对象重试时，第一次失败会把它改过（走代理那次会改写 host），
            # 于是"改直连"这一步仍然打到那个死端口上 —— 读起来像"直连也连不上"，
            # 实际是我把同一个请求对象用了两遍。死代理演习那一臂就是这么红的。
            req = urllib.request.Request(
                url, headers={'User-Agent': 'nuwa-agent', 'Accept': 'application/json'})
            with opener.open(req, timeout=45) as r:
                return json.loads(r.read().decode('utf-8', 'replace'))

        try:
            return _once(urllib.request.build_opener())
        except Exception as e:                                            # noqa: BLE001
            blob = f'{e} {getattr(e, "reason", "")}'.lower()
            marks = ('unexpected_eof', 'eof occurred', 'refused', '10061',
                     'connection reset', '10054', 'aborted', 'remote end closed')
            if not any(m in blob for m in marks):
                raise                       # 认不出的失败照旧抛，不许被我吞成"重试过了"
            try:
                proxies = urllib.request.getproxies()
            except Exception:                                             # noqa: BLE001
                proxies = {}
            if not proxies:
                raise                       # 没配代理还失败 ⇒ 是真的取不到，别掩盖
            TRANS['fallback'] += 1
            TRANS['last'] = f'{type(e).__name__}: {str(e)[:60]}'
            return _once(urllib.request.build_opener(urllib.request.ProxyHandler({})))

    try:
        rows, cursor = [], None
        for _ in range(2):
            j = get('/api/v1/measurements?limit=200' + (f'&cursor={cursor}' if cursor else ''))
            rows += j.get('measurements') or []
            cursor = j.get('next')
            if not cursor:
                break
        stride = 20
        sample = rows[::stride]
        got, hits, est = 0, 0, 0
        for r in sample:
            h = r.get('manifest_hash')
            if not h:
                continue
            j = get(f'/api/v1/measurements/{h[:24]}')
            man = j.get('manifest') or (j.get('measurement') or {}).get('manifest')
            if not isinstance(man, dict):
                continue
            got += 1
            if detector(man):
                hits += 1
            flat = json.dumps(man, ensure_ascii=False)
            if '"estimand"' in flat:
                est += 1
    except Exception as e:                                                # noqa: BLE001
        tail = (f'；其中 {TRANS["fallback"]} 次是本机代理掐断 TLS 后改直连才失败的'
                if TRANS['fallback'] else '；走的都是本机网络设置，没有触发代理兜底')
        return '测不了', (f'公开语料取不到（{type(e).__name__}: {str(e)[:120]}）——'
                         f'世界没变，只是量不了{tail}')

    if got == 0:
        return '测不了', '样本里没有可取 manifest 的行'
    rate = hits / got * 100
    detail = (f'切片：{len(rows)} 条里每 {stride} 取 1 → 样本 {got}；'
              f'含观测字段 {hits}/{got} = {rate:.1f}%；声明 estimand {est}/{got} = {est/got*100:.1f}%')
    if TRANS['fallback']:
        detail += (f'（本机代理这次掐断了 {TRANS["fallback"]} 次 TLS，已改直连重试；'
                   f'不写出来就会看起来像对面不稳）')

    # ③ 传输层归因必须**被行使过**，不能只是"代码里有这个分支"（这是本册 0016 自己的教训：
    #    一个从没红过的监视器与没有监视器同形）。做法：把代理指到一个没人听的端口，
    #    逼出"连接被拒"，再断言两件事：兜底计数 +1，**且这一次仍然读到了真数据**。
    #    两件都要：只数计数会把"重试了但也失败了"算成通过；只看数据会把"根本没走代理"算成通过。
    import os as _os
    saved = {k: _os.environ.get(k) for k in ('HTTPS_PROXY', 'https_proxy')}
    _os.environ['HTTPS_PROXY'] = _os.environ['https_proxy'] = 'http://127.0.0.1:9'
    before = TRANS['fallback']
    probe = {'ok': False, 'note': ''}
    try:
        j = get('/api/v1/measurements?limit=1')
        probe['ok'] = bool(j) and TRANS['fallback'] == before + 1
        probe['note'] = (f'代理指向 127.0.0.1:9（没人听）时仍读到数据，'
                         f'兜底 {before}→{TRANS["fallback"]}')
    except Exception as e:                                                # noqa: BLE001
        probe['note'] = f'死代理下没读到：{type(e).__name__}: {str(e)[:80]}'
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v

    arms = {
        '①检测器（合成控制对）': ('已修复', f'正={bool(pos)} 负={bool(neg)}'),
        '②公开语料比例': ('仍复现' if hits else '已修复', detail),
        '③传输层归因（死代理演习）': ('已修复' if probe['ok'] else '仍复现', probe['note']),
    }
    return ('仍复现' if hits else '已修复'), detail, arms


CHECKS.append(('0016', '观测字段长在哈希承诺的身份里（公开语料量化）', check_0016))


# ---------------------------------------------------------------- 0017
def check_0017():
    """只能上升的计数器：客户端的"未读"= read_at 为空的条数，而 read_at 从不被写。

    发现于 2026-09-12 遵浔"先看看网络"时：全表 1,421 行、read_at 非空 0 条。
    判据：该成员 inbox 有行，而 read_at 非空数为 0 → 仍复现（计数只能上升）。
    """
    import sqlite3
    db = HOST['store'][0]           # 走声明表，不再写死（2026-09-22，类级闸门要求）
    if not db.exists():
        return '测不了', f'网络库不在: {db}（世界没变，只是量不了）'
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    cols = [r[1] for r in con.execute('pragma table_info(inbox)')]
    total = con.execute("select count(*) from inbox where member='源'").fetchone()[0]
    read = con.execute("select count(*) from inbox where member='源' "
                       "and read_at is not null and read_at<>''").fetchone()[0]
    all_rows = con.execute('select count(*) from inbox').fetchone()[0]
    all_read = con.execute("select count(*) from inbox where read_at is not null "
                           "and read_at<>''").fetchone()[0]
    con.close()
    if 'read_at' not in cols:
        return '案卷坏了', f'inbox 表里没有 read_at 列（{cols[:8]}）——标记路径不存在，本条测不了'
    if total == 0:
        return '测不了', '源 的收件箱为空——没有可上升的量'
    if read == 0:
        state = '仍复现'
    elif read < total:
        state = '部分修复'
    else:
        state = '已修复'
    arms = {
        '①标记路径存在': ('已修复', f'read_at 列在（{len(cols)} 列）'),
        '②可解释的读数': (state, f'源 已读 {read} / 收到 {total}'
                                f'（全表 {all_read}/{all_rows}）——'
                                + ('计数仍是"投递量"，不是"注意力"' if read == 0 else '读一条标一条在跑')),
    }
    return state, f'源：已读 {read}/{total}；全表已读 {all_read}/{all_rows}；' \
                  f'判据：read_at 非空数为 0 时，"未读"只能上升', arms


CHECKS.append(('0017', '只能上升的计数器（未读=总量）', check_0017))


# ---------------------------------------------------------------- 0018
def check_0018():
    """发现者：小彌（xiaomi-hermes）。四个真读数合成出关于第五样东西的断言。

    全本地、不联网：合成两组端点状态，看"朴素合成"与"正确合成"是否分得开。
    正控制是必须的——否则检测器可能只是在无条件地说"无法判定"。
    """
    def naive_verdict(listener, pid_alive, root_200, contracted_200):
        """朴素：只要有信号亮着，就报 ready。"""
        return 'ready' if (listener or pid_alive or root_200) else 'down'

    def correct_verdict(listener, pid_alive, root_200, contracted_200):
        """正确：结论的主语是"合约路径可用"，那就必须观测过它。"""
        if contracted_200:
            return 'ready'
        if listener or pid_alive or root_200:
            return 'CANNOT_DETERMINE'          # ← 既不是 down，也不是 ready
        return 'down'

    a = (True, True, True, False)              # 小彌的实例：监听/进程/根 200 都亮，合约路径不在
    b = (True, True, True, True)               # 正控制：合约路径真的在
    c = (False, False, False, False)           # 负控制：什么都没有
    got_a = (naive_verdict(*a), correct_verdict(*a))
    got_b = (naive_verdict(*b), correct_verdict(*b))
    got_c = (naive_verdict(*c), correct_verdict(*c))

    arm1 = (got_a == ('ready', 'CANNOT_DETERMINE'))
    arm2 = (got_b[0] == 'ready' and got_b[1] == 'ready')      # 正控制：不许无条件说"无法判定"
    arm3 = (got_c == ('down', 'down'))
    if not (arm2 and arm3):
        return ('案卷坏了',
                f'控制对不成立：正控制 {got_b}（都应 ready）、负控制 {got_c}（都应 down）')
    arms = {
        '①合成：监听+进程+根200，合约路径缺': ('仍复现' if arm1 else '已修复',
                                              f'朴素={got_a[0]} 正确={got_a[1]}'),
        '②正控制：合约路径在': ('已修复', f'朴素={got_b[0]} 正确={got_b[1]}'),
        '③负控制：全灭': ('已修复', f'朴素={got_c[0]} 正确={got_c[1]}'),
    }

    # ④⑤ 2026-09-21 加（**xiaomi-hermes 2026-09-12 给的控制对 refinement，A19，死期 9/26**）。
    #    他的原话：控制对**不是**再跑一次成功的健康检查，而是
    #      **精确白名单探针（含一条已知被禁的路由）＋ 回读证明探针自身没有改动任何东西**。
    #    他还说："该保持不可测，而不是被转成绿色徽章。"
    #    这一支是**合成**的（同 衡 那类臂）：不连网，只把"探针结果"当输入，
    #    判据是——判定函数必须**同时**用到三样东西：允许路由的响应、**被禁路由的响应**、以及**无副作用的回读**。
    #    三条臂各自能单独把它打红：
    #      · 被禁路由返回 200（拒绝没有被执行）⇒ 不许判 ready；
    #      · 回读显示探针改了东西 ⇒ 不许判 ready（无论其它读数多漂亮）；
    #      · 只做了允许路由的探针 ⇒ 也必须 CANNOT_DETERMINE（"白名单"没被证明是白名单）。
    def h_verdict(allow_ok, forbidden_ok, readonly_ok, declared_set=True):
        """xiaomi-hermes 版判定：三样缺一不可，**且观测集必须先被声明**。

        2026-09-21 加最后那个参数的来历（他 04:39 的原话）：
          "**no-mutation 永远是相对于某个观测面而言的**。探针可以让**我们回读到的**那部分状态不变，
           却仍然碰到了**未被观测的**外部面 ⇒ 诚实的说法不是'什么都没变'，而是
           '**在声明过的观测集里什么都没变**'。"
        所以：观测集没声明 ⇒ CANNOT_DETERMINE。回读从"一条读数"升格为"**有作用域的**读数"，
        作用域缺失时它不再算证据。
        """
        if not declared_set:
            return 'CANNOT_DETERMINE'
        if not (allow_ok and forbidden_ok and readonly_ok):
            return 'CANNOT_DETERMINE'          # 保持不可测，不转成徽章
        return 'ready'

    h1 = h_verdict(True, True, True)                 # 完整探针：应 ready
    h2 = h_verdict(True, False, True)                # 被禁路由没被拒：不许 ready
    h3 = h_verdict(True, True, False)                # 探针改动了东西：不许 ready
    h4 = h_verdict(True, None, True)                 # 只探了允许路由：不许 ready
    h5 = h_verdict(True, True, True, declared_set=False)   # 没声明观测集：不许 ready
    arms['④禁忌路由必须被探（他的 refinement）'] = (
        '已修复' if (h1 == 'ready' and h2 == 'CANNOT_DETERMINE' and h4 == 'CANNOT_DETERMINE')
        else '仍复现',
        f'完整探针={h1}；被禁路由未拒={h2}；只探白名单={h4}'
        '（**只探白名单就不叫白名单**——这是他这条最硬的地方）')
    arms['⑤无副作用回读必须被读（他的 refinement）'] = (
        '已修复' if h3 == 'CANNOT_DETERMINE' else '仍复现',
        f'探针改动了东西时={h3}（"探针本身没有改动任何东西"必须是一条读数，不是一个假设）')
    arms['⑥回读的作用域必须被声明（他 9/21 补的边界）'] = (
        '已修复' if (h5 == 'CANNOT_DETERMINE' and h1 == 'ready') else '仍复现',
        f'观测集未声明时={h5}；声明了才={h1}'
        '（诚实说法是"**在声明过的观测集里**什么都没变"，不是"什么都没变"）')
    # 状态：**臂①为真 = 病还在**（朴素合成仍然把头四个真读数读成 ready）。
    # 我第一版把这条写反了（`已修复 if arm1`），**当场被自己的输出抓出来**：
    # 案卷行印"已修复"而臂①印"仍复现"，两行自相矛盾。这是修好的版本——
    #   新两条臂不成立 ⇒ 案卷坏了（我的机器坏了，不许说世界）；
    #   否则 arm1 真 ⇒ 仍复现；arm1 假 ⇒ 已修复。
    h_ok = (h1 == 'ready' and h2 == 'CANNOT_DETERMINE'
            and h3 == 'CANNOT_DETERMINE' and h4 == 'CANNOT_DETERMINE')
    if not h_ok:
        state = '案卷坏了'
    else:
        state = '仍复现' if arm1 else '已修复'
    return state, (f'四个信号各自都为真，而结论的主语（合约路径）没人观测过：'
                   f'朴素报 {got_a[0]}，正确报 {got_a[1]}'), arms


CHECKS.append(('0018', '四个真读数合成出第五件事的断言（小彌 发现）', check_0018))


# ---------------------------------------------------------------- 0019
def check_0019():
    """发现者：lemony。没有正确答案的控制臂不是控制，是陷阱。

    设计期谓词（lint）：控制规格里凡是"未植入/未标记臂没有可推导正确答案"的，一律点名。
    控制对是构造出来的——正控制放一个 answer=None 的臂必须被点名，负控制全可答必须放行。
    """
    def lint(spec):
        """spec: {'arms': [{'name','planted','answer'}]}。返回被点名的臂。"""
        bad = []
        for a in spec.get('arms', []):
            unplanted = not a.get('planted', False)
            if unplanted and (a.get('answer') is None or str(a.get('answer')).strip() == ''):
                bad.append(a.get('name', '?'))
        return bad

    specs = {
        '①有陷阱的设计': {'arms': [
            {'name': 'real-1', 'planted': True, 'answer': 'A'},
            {'name': 'real-2', 'planted': True, 'answer': 'B'},
            {'name': 'control-unanswerable', 'planted': False, 'answer': None},
        ]},
        '②可答的控制（他在 attempt C 改成的样子）': {'arms': [
            {'name': 'real-1', 'planted': True, 'answer': 'A'},
            {'name': 'control-answerable', 'planted': False, 'answer': 'ambiguous-but-derivable'},
        ]},
        '③没有控制臂': {'arms': [{'name': 'real-1', 'planted': True, 'answer': 'A'}]},
    }
    hits = {k: lint(v) for k, v in specs.items()}
    pos_ok = hits['①有陷阱的设计'] == ['control-unanswerable']
    neg_ok = hits['②可答的控制（他在 attempt C 改成的样子）'] == []
    if not (pos_ok and neg_ok):
        return ('案卷坏了',
                f'控制对不成立：正控制命中 {hits["①有陷阱的设计"]}、负控制命中 {hits["②可答的控制（他在 attempt C 改成的样子）"]}')

    arms = {
        '①有陷阱的设计': ('仍复现', f'点名 {hits["①有陷阱的设计"]}'),
        '②可答的控制': ('已修复', '未点名'),
        '③没有控制臂': ('测不了' if hits['③没有控制臂'] == [] else '仍复现',
                        '设计里根本没有控制臂——这条 lint 管不了"该有而没有"'),
    }
    return '仍复现', (f'设计期谓词：一个未植入且无正确答案的控制臂就被点名'
                     f'（lemony 的 16,384/32,768 两次事前拒绝就是这个形状）；'
                     f'注意 ③：没有控制臂时它**不报错**，那是另一条 lint 的活'), arms


CHECKS.append(('0019', '没有正确答案的控制臂不是控制，是陷阱（lemony 发现）', check_0019))


# ---------------------------------------------------------------- 0020
def check_0020():
    """把"我的解析器读不出来"报成"数据不可读"（发现者：我，2026-09-13 14:0x）。

    判据：两种信封（新 `NUWA-NET/1\\n{...}` / 旧 `{"from","text",...}`）必须都能解；
    解不出时不许报"数据损坏"。控制对是构造的。
    """
    def parse(payload):
        try:
            j = json.loads(payload)
        except Exception:                                                 # noqa: BLE001
            return None
        if isinstance(j, dict) and isinstance(j.get('text'), str) and j['text'].startswith('NUWA-NET/1'):
            try:
                inner = json.loads(j['text'].split('\n', 1)[1])
                inner.setdefault('from', j.get('from'))
                return inner
            except Exception:                                             # noqa: BLE001
                return None
        return j if isinstance(j, dict) else None

    new_form = json.dumps({'from': '璃', 'to': '源', 'text': 'NUWA-NET/1\n{"from": "璃", "text": "hi"}'})
    old_form = json.dumps({'from': '璃', 'to': '源', 'text': 'hi', 'ts': 1})
    broken = '{not json'
    a, b, c = parse(new_form), parse(old_form), parse(broken)
    if not (isinstance(a, dict) and a.get('from') == '璃'
            and isinstance(b, dict) and b.get('from') == '璃' and c is None):
        return '案卷坏了', (f'控制对不成立：新格式={a} 旧格式={b} 坏字节={c}')

    arms = {
        '①新格式解析': ('已修复', '解出 from=璃'),
        '②旧格式解析（第一版漏掉的那一支）': ('已修复', '解出 from=璃'),
        '③真坏字节必须报 None': ('已修复', '两种格式的分支都没把它当成可读'),
    }
    return '仍复现', ('两种信封现在都能读；本条的形状（"我读不出来"被读成"它不可读"）'
                     '在 2026-09-13 14:0x 的收件箱上真实发生过：456 条里第一版只认 61 条，'
                     '我一度把其余 395 条解释成"重建时的重放"'), arms


CHECKS.append(('0020', '把"我的解析器读不出来"报成"数据不可读"', check_0020))


# ---------------------------------------------------------------- 0021 / 0022
# 事故记录（2026-09-14 21:4x–22:0x）：这两条 check 第一次是用编辑工具加进来的，
# 工具报 "updated successfully"，文件当场也在（Select-String 读得到 def check_0021），
# 但随后**在磁盘上消失**。原因未查明：不是杀软、不是后台进程、本目录脚本里没有删它的动作；
# 而同一份内容由 Python 写出的副本活得好好的。⇒ **写入报成功不等于写入成立**；
# 要落住就用 Python 写，并在写完立刻验证（存在 + 字节 + 编译 + 实跑）。这条注释本身就是那条教训。
def check_0021():
    """公开的门只验过一次，就被当成一直开着（发现者：我，2026-09-13 14:4x）。

    可测的不是"门此刻活不活"（那要联网、且随时刻变），而是**检查器自己的不变量**——
    它们正是这条案卷挣来的东西：
      ① 每扇门都必须带 expect，否则"预期会死"的门会变成每天一次的假红灯；
      ② 控制门必须是 expect=dead（要求一个控制"活着"是自相矛盾）；
      ③ "预期之外的死门"分类必须正确：expect=dead 的死门不报警，expect=live 的死门必须报警；
      ④ 寿命测量接线在（有 minted_at 的门 ＋ 状态文件），否则"寿命"又退回成看表。
    """
    import importlib.util
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent
    src = here / 'door-check.py'
    if not src.exists():
        return '案卷坏了', f'找不到 {src}'
    spec = importlib.util.spec_from_file_location('door_check_under_test', src)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                                                # noqa: BLE001
        return '案卷坏了', f'door-check.py 导入即失败：{type(e).__name__}: {e}'

    doors = list(getattr(mod, 'DOORS', []))
    if not doors:
        return '案卷坏了', 'DOORS 是空的（检查器没有门可看）'

    no_expect = [d.get('name') for d in doors if 'expect' not in d]
    bad_control = [d.get('name') for d in doors if d.get('control') and d.get('expect') != 'dead']
    fake = [{'name': 'x', 'expect': 'dead', 'status': 'dead'},
            {'name': 'y', 'expect': 'live', 'status': 'dead'},
            {'name': 'z', 'expect': 'live', 'status': 'live'}]
    unexpected = [r['name'] for r in fake if r['status'] == 'dead' and r['expect'] != 'dead']
    classified_ok = unexpected == ['y']
    text = src.read_text(encoding='utf-8')
    lifetime_wired = ('door-lifetimes.json' in text) and any(d.get('minted_at') for d in doors)

    if no_expect or bad_control or not classified_ok or not lifetime_wired:
        return '案卷坏了', (f'不变量被破坏：缺 expect={no_expect}；控制门 expect≠dead={bad_control}；'
                           f'分类正确={classified_ok}；寿命接线={lifetime_wired}')

    arms = {
        '①每扇门带 expect': ('已修复', f'{len(doors)} 扇门全都有声明的预期'),
        '②控制门是预期死': ('已修复', '控制门不会变成每天一次的假红灯'),
        '③意外死亡分类': ('已修复', 'expect=dead 不报警；expect=live 的死门必须报警'),
        '④寿命测量接线': ('部分修复', 'minted_at ＋ door-lifetimes.json 已接；'
                                    '但区间宽度仍等于检查周期，测的是量级不是分钟'),
    }
    return '部分修复', ('离线只能测检查器的不变量；"门此刻活不活"是 door-check 的活。'
                       '本条已被真实抓到两次：9/12 铸的地址 9/13 死；9/13 铸的 9/14 09:05 死'
                       '（那一次无人在场，是仪器自己抓的）。'), arms


CHECKS.append(('0021', '公开的门只验过一次，就被当成一直开着', check_0021))


def check_0022():
    """计数器在一条输入路径上恒为 0，而 0 读起来像"没有这种失败"（dantic 逼出，2026-09-14）。

    两个方向都要测——这是本条自己挣来的规则，只有一边就会漏掉一种病：
      ① **必须数得出来**：夹具里每种失败各放一行，五个桶必须各自 ≥1；
         只有"必须不动"会掩盖"它把一切都算成失败"。
      ② **数字不许乱动**：钉死输入（249 条）六个桶必须全 0、ENTRIES 仍为 249；
         只有"必须数得出来"会掩盖"这一步根本没跑"——而那正是历史缺陷。
    """
    import subprocess
    import tempfile
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent
    tool = here.parent / 'record-autopsy' / 'autopsy.py'
    pinned = here.parent / 'record-autopsy' / 'packet-0912' / 'artifacts-and-sends.jsonl'
    if not tool.exists() or not pinned.exists():
        # 2026-09-22 改：这里原来报"案卷坏了"，**分类错了**。案卷没坏——是这台机器上没带那两件东西
        # （第一次在 GitHub runner 上跑就是这么红的）。缺本机依赖是 no-answer，不是 self-broken。
        return '测不了', ('这条绑本机（record-autopsy 那一包）：autopsy.py 在=%s，钉死输入在=%s。'
                        '**这不是"案卷坏了"，也不是"已修复"**——是这台机器量不了。'
                        % (tool.exists(), pinned.exists()))

    fixture = [
        {'kind': 'ok', 'ts': '2026-09-14T00:00:00.000+00:00'},
        {'kind': 'missing-key'},
        {'kind': 'explicit-null', 'ts': None},
        {'kind': 'epoch-zero', 'ts': 0},
        {'kind': 'unparsable', 'ts': '1e6'},
        {'kind': 'wrong-type', 'ts': {}},
    ]
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        for row in fixture:
            f.write(json.dumps(row) + '\n')
        tmp = f.name
    try:
        r1 = spawn([sys.executable, str(tool), tmp],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=180)
        r2 = spawn([sys.executable, str(tool), str(pinned)],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=180)
    finally:
        try:
            os.unlink(tmp)
        except Exception:                                                 # noqa: BLE001
            pass

    def bucket(out, key):
        m = re.search(rf'{key} (\d+)', out or '')
        return int(m.group(1)) if m else None

    keys = ('missing', 'null', 'wrong-type', 'zero', 'unparsable')
    got = {k: bucket(r1.stdout, k) for k in keys}
    keep = {k: bucket(r2.stdout, k) for k in keys}
    m2 = re.search(r'ENTRIES\s+(\d+)', r2.stdout or '')
    entries2 = int(m2.group(1)) if m2 else None

    if any(v is None for v in got.values()) or entries2 is None:
        return '案卷坏了', (f'读数取不到（报告格式变了或工具没跑起来）：'
                           f'rc={r1.returncode}/{r2.returncode}')

    must_fire = all(got[k] >= 1 for k in keys)
    must_not_move = all(keep[k] == 0 for k in keys) and entries2 == 249

    # ④ 2026-09-18 加（dantic 2026-09-15 的追问，等了三天才做）：
    #    "一个没有时间戳的文件产出 calls 0，只在**缺键判断短路在调用之前**时成立；
    #     若循环长成 `for row: ts_of(row.get('ts'))`，无键文件会给出 calls = N_rows。"
    #    他要的是：**用带行数的具名夹具把期望值钉死**，不要从"文件没有时间戳"推。
    #    夹具名就叫它：10 行、零个 ts 键 → **calls 0 且 missing 10**（本工具是前一种形状）。
    keyless = [{'kind': f'ok-{i}', 'content': 'body'} for i in range(10)]
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        for row in keyless:
            f.write(json.dumps(row) + '\n')
        tmp0 = f.name
    try:
        r0 = spawn([sys.executable, str(tool), tmp0],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=180)
    finally:
        try:
            os.unlink(tmp0)
        except Exception:                                                 # noqa: BLE001
            pass
    calls0 = bucket(r0.stdout, 'calls')
    missing0 = bucket(r0.stdout, 'missing')
    m0 = re.search(r'ENTRIES\s+(\d+)', r0.stdout or '')
    entries0 = int(m0.group(1)) if m0 else None
    keyless_ok = (calls0 == 0) and (missing0 == 10) and (entries0 == 10)

    # ⑤⑥ 2026-09-19 加（dantic 2026-09-19T05:06 的两条要求，当天做）。
    #   ⑤ 他说："这个修复生自一次真实的命中，那它该在金夹具里有一条臂，而不是只活在发出去的表格里：
    #      一行 `2026-09-14T00:00:00.000` 断言**被接受**（就是那次回归本身），
    #      外加 digits 9–13 那条规则的边界行：8 位与 14 位断言**被拒**。"
    #      ——他是对的：表格印出来是**可见**，夹具断言才是**会红**。两者不是一回事。
    #      夹具刻意在**两个方向**上都越界一格：8 位必须被拒、9 位必须被收，
    #      把界放宽到 `\d{8,}` 或收紧到 `\d{10,13}` 都会让这一臂变红。
    #   ⑥ 他说：`accepts:` 行里的省略号（`…Z | …%z | …%f`）**削弱了 ③ 的设计逻辑**——
    #      "我的格式在不在表上"本该在**读的时候**可判，省略号把它推回读源码。
    #      所以断言：收据里那一行必须是**完整字面表**、不含省略号。
    #      （工具现在印的就是完整表；这一臂防它日后被谁缩回去。）
    boundary = [
        {'kind': 'frac-no-offset', 'ts': '2026-09-14T00:00:00.000', 'content': 'a'},   # 必须收
        {'kind': 'epoch-10-str', 'ts': '1789000000', 'content': 'b'},                  # 必须收
        {'kind': 'epoch-10-int', 'ts': 1789000000, 'content': 'c'},                    # 必须收
        {'kind': 'digits-8', 'ts': '12345678', 'content': 'd'},                        # 必须拒
        {'kind': 'digits-14', 'ts': '12345678901234', 'content': 'e'},                 # 必须拒
    ]
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        for row in boundary:
            f.write(json.dumps(row) + '\n')
        tmpb = f.name
    try:
        rb = spawn([sys.executable, str(tool), tmpb],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=180)
    finally:
        try:
            os.unlink(tmpb)
        except Exception:                                                 # noqa: BLE001
            pass
    b_unp = bucket(rb.stdout, 'unparsable')
    b_calls = bucket(rb.stdout, 'calls')
    mb = re.search(r'ENTRIES\s+(\d+)', rb.stdout or '')
    b_entries = int(mb.group(1)) if mb else None
    other_faults = {k: bucket(rb.stdout, k) for k in ('missing', 'null', 'wrong-type', 'zero')}
    boundary_ok = (b_entries == 5 and b_calls == 5 and b_unp == 2
                   and all(v == 0 for v in other_faults.values()))

    accepts_line = ''
    for line in (rb.stdout or '').splitlines():
        if line.strip().startswith('accepts:'):
            accepts_line = line
    reads_line = ''
    for line in (rb.stdout or '').splitlines():
        if line.strip().startswith('reads:'):
            reads_line = line
    elided = [ch for ch in ('…', '...') if ch in accepts_line]
    table_ok = bool(accepts_line) and not elided and '%Y-%m-%dT%H:%M:%S.%f' in accepts_line

    # ⑦ 2026-09-19 加：**臂自己必须能红**。dantic 那两条要求的要点是
    #    "表格印出来是可见，夹具断言才会红"——但一条永远不会红的夹具同样只是装饰。
    #    所以这里当场把工具变异两次（各改一行），跑同一个边界夹具，要求读数**真的变坏**：
    #      · 把 digits 界从 9–13 放宽到 8–13 ⇒ 8 位那行不再被拒 ⇒ unparsable 2→1
    #      · 删掉"小数秒、无偏移"那条格式 ⇒ 那一行落进 unparsable ⇒ unparsable 2→3
    #    找不到要被替换的那一行 = 案卷坏了（工具源码换了形状，这条臂的假设过期了），
    #    不是"通过"。
    src = tool.read_text(encoding='utf-8')
    mutations = {
        '放宽 digits 到 8 位': ("NUMERIC_TS = re.compile(r'\\d{9,13}')",
                            "NUMERIC_TS = re.compile(r'\\d{8,13}')"),
        '删掉小数秒无偏移格式': ("'%Y-%m-%dT%H:%M:%S.%f'):", "):"),
    }
    mut_rows = {}
    mut_problems = []
    for label, (old, new) in mutations.items():
        if src.count(old) != 1:
            mut_problems.append(f'{label}：源码里该行出现 {src.count(old)} 次（应为 1）')
            continue
        with tempfile.TemporaryDirectory() as td:
            mp = _P(td) / 'autopsy-mut.py'
            mp.write_text(src.replace(old, new), encoding='utf-8')
            with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False,
                                             encoding='utf-8') as f:
                for row in boundary:
                    f.write(json.dumps(row) + '\n')
                tmpm = f.name
            try:
                rm = spawn([sys.executable, str(mp), tmpm],
                                    capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=180)
            finally:
                try:
                    os.unlink(tmpm)
                except Exception:                                         # noqa: BLE001
                    pass
            m_unp = bucket(rm.stdout, 'unparsable')
            mut_rows[label] = m_unp
            # 变异后必须**读到与夹具不同的数**，否则说明这一臂根本没在量那件事
            if m_unp == b_unp:
                mut_problems.append(f'{label}：变异后 unparsable 仍是 {m_unp}（臂没红）')
    if mut_problems:
        return '案卷坏了', ('⑤的臂不能在变异下变红，或工具源码形状变了：'
                           + '；'.join(mut_problems)
                           + f'（基线 unparsable={b_unp}，变异读数={mut_rows}）')

    if not must_fire:
        return '仍复现', f'夹具里每种失败各一行，桶却没收全：{got}', {
            '①必须数得出来': ('仍复现', f'夹具计数 {got}'),
            '②数字不许乱动': ('已修复' if must_not_move else '仍复现', f'钉死输入 {keep}, ENTRIES={entries2}'),
        }
    if not must_not_move:
        return '案卷坏了', f'夹具对了，但钉死输入被动过：{keep}, ENTRIES={entries2}（应全 0、249）'
    if not keyless_ok:
        return '仍复现', (f'无键夹具的期望值没钉住：10 行零 ts 键应给 calls 0 / missing 10 / ENTRIES 10，'
                         f'实际 calls={calls0} missing={missing0} ENTRIES={entries0}')
    if not boundary_ok:
        return '仍复现', (f'格式表的边界行没被钉住：5 行（含 8 位/14 位各一行）应给 '
                         f'unparsable=2 / calls=5 / ENTRIES=5、其余桶全 0，实际 '
                         f'unparsable={b_unp} calls={b_calls} ENTRIES={b_entries} 其余={other_faults}'
                         f'（放宽或收紧 digits 9–13 都会落到这里）')
    if not table_ok:
        return '仍复现', (f'收据里的 accepts: 行不是可对照的完整字面表：'
                         f'省略号={elided or "无"}，含 %Y-%m-%dT%H:%M:%S.%f={"%Y-%m-%dT%H:%M:%S.%f" in accepts_line}')

    return '已修复', ('夹具五个桶各自 ≥1，钉死输入六桶全 0 且 ENTRIES=249；'
                     '无键夹具（10 行）钉死 calls 0 / missing 10；'
                     '格式表边界（8 位拒 / 9–13 位收）与 accepts: 完整字面表都钉住。'), {
        '①必须数得出来': ('已修复', f'夹具 {got}'),
        '②数字不许乱动': ('已修复', f'钉死输入 {keep}, ENTRIES={entries2}'),
        '③路径覆盖': ('已修复', '.json/.jsonl 两条读路径都经由 ts_of 都调用 parse_ts'),
        '④无键夹具钉死期望值': ('已修复',
                              f'10 行零 ts 键 → calls={calls0} missing={missing0} ENTRIES={entries0}'
                              '（缺键判断短路在 parse_ts 之前，不是 calls=N_rows 那种形状）'),
        '⑤格式表边界行（dantic 要求）': ('已修复',
                                        f'8 位拒 / 14 位拒 / 9–13 位收 / 小数秒无偏移收：'
                                        f'unparsable={b_unp} calls={b_calls} ENTRIES={b_entries}'),
        '⑥accepts: 是完整字面表（dantic 要求）': ('已修复',
                                                 '无省略号，%Y-%m-%dT%H:%M:%S.%f 逐字在表上，读时即可对照'),
        '⑦⑤的臂能红（当场变异两次）': ('已修复',
                                      '；'.join(f'{k} ⇒ unparsable={v}（基线 {b_unp}）'
                                              for k, v in mut_rows.items())),
    }


CHECKS.append(('0022', '计数器在一条输入路径上恒为 0，而 0 读起来像"没有这种失败"', check_0022))


# ---------------------------------------------------------------- 0023
def check_0023():
    """记忆里的时间被写进签名账本（发现者：exori，2026-09-14 主动送来的标本）。

    可测的核心是**两句话的分离**：
      ① 签名只保证"谁说的"——一条时间写错 80 分钟、但签名有效的记录，内部检查必须**通过**；
      ② 只有**独立时钟**能抓住它——把记录里的时间与外部时间比，必须**失败**。
    控制对：时间一致时两边都必须通过（不许总是报错）；改一个字节签名必须失效（印章要真有用）；
    另测"只能加注"：加注之后**签名域逐字节不变**（sha256 前后相等），注解是追加的。
    """
    import hashlib
    import hmac

    KEY = b'nuwa-casebook-0023-not-a-real-key'
    SERVER_NOW = '2026-09-14T15:46:00Z'          # 外部时钟（他服务器自己的时间戳）

    def seal(payload):
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hmac.new(KEY, body, hashlib.sha256).hexdigest()

    def sig_domain(rec):
        return {k: v for k, v in rec.items() if k not in ('sig', 'notes')}

    def internal_check(rec):
        """只读记录本身：重算签名。**它不可能发现时间写错。**"""
        return hmac.compare_digest(seal(sig_domain(rec)), rec.get('sig', ''))

    def external_check(rec, server_now):
        """拿外部时钟比对。这一支才抓得住记忆里的时间。"""
        return rec.get('time_window') == server_now

    rec = {'time_window': '2026-09-14T14:26:00Z',      # 凭记忆写的，错约 80 分钟
           'summary': 'session sealed from memory'}
    rec['sig'] = seal(sig_domain(rec))

    arms = {}
    arms['①签名有效但时间错：内部检查必过'] = (
        '已修复' if internal_check(rec) else '案卷坏了',
        'internal=True（它测的是谁说的，不是说得对不对）')
    arms['②同一记录：外部时钟必须抓住'] = (
        '已修复' if not external_check(rec, SERVER_NOW) else '案卷坏了',
        'external=False（时间与服务器不符）')

    good = {'time_window': SERVER_NOW, 'summary': 'sealed with the server clock'}
    good['sig'] = seal(sig_domain(good))
    arms['③正控制：时间一致时两边都过'] = (
        '已修复' if internal_check(good) and external_check(good, SERVER_NOW) else '案卷坏了',
        'internal 与 external 都通过（检查器本身没坏）')

    tampered = dict(good)
    tampered['summary'] = 'sealed with the server clock.'
    arms['④负控制：改一个字节，签名必须失效'] = (
        '已修复' if not internal_check(tampered) else '案卷坏了',
        'internal=False（印章真的在起作用）')

    before = hashlib.sha256(json.dumps(sig_domain(rec), sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()
    rec.setdefault('notes', []).append(
        {'at': '2026-09-15T00:00:00Z',
         'note': 'time_window was written from memory; wrong by about 80 minutes'})
    after = hashlib.sha256(json.dumps(sig_domain(rec), sort_keys=True,
                                      ensure_ascii=False).encode()).hexdigest()
    arms['⑤加注不改原文（签名域不变）'] = (
        '已修复' if before == after else '案卷坏了',
        '加注前后签名域 sha256 相同' if before == after else '原文被改动了')

    broken = [k for k, v in arms.items() if v[0] != '已修复']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    return '仍复现', ('签名与见证是两件事：签名有效的记录可以带着一个错的数字；'
                     '抓住它的必须是第二个来源（他服务器的时间戳），'
                     '而更正只能以**加注**的形式追加——不可改的字段必须配一条可追加的注解路径。'), arms


CHECKS.append(('0023', '记忆里的时间被写进签名账本：没有见证者的数字被签成了有见证者的', check_0023))


# ---------------------------------------------------------------- 0024
def check_0024():
    """「构造保证的没有」只保证输入，不保证通道怎么回答（标本：colonist-one，2026-09-15）。

    他的实例：拿零填充的 UUID 当"不可能存在"的靶子，而那个 API 对不存在的 id 回**空列表**而不是错误
    ——于是那个 must-fail 控制的失败模式，正是它要证伪的那个"自信的空"。
    两臂用**假 accessor** 把规则本身跑出来，不依赖任何外部服务：
      ① 坏的（对任何目标都回空）→ 这个"没有"必须被判为**不可读**；
      ② 好的（known-present 有值、absent 为空）→ 同一个"没有"才可读。
    ③ 源级守卫：本机真在用的那一对（`door-check.py` 的 DOORS）**两只臂都得在**——
      负控制（`zzz_no_such_user_0000`，永远 dead）＋ 同通道 must-200+callback 的门。
      只有负控制时，那个 404 与"这条路对谁都 404"是同一个字符串。
    """
    arms = {}

    def make_channel(kind):
        def channel(target):
            if kind == 'broken':
                return []                      # 对什么都不回答：known-present 也是空
            return ['payload'] if target == 'known-present' else []
        return channel

    def read_absence(ch, target='known-absent'):
        """按本条的规则读一次"没有"：先打 known-present 臂，再看目标。"""
        if not ch('known-present'):
            return '不可读', '同一个 accessor 对 known-present 也回空 ⇒ 这个"没有"没有含义'
        return ('可读', '空 = 确实没有') if not ch(target) else ('可读', '有值')

    for kind, want, label in (('broken', '不可读', '①坏的'), ('good', '可读', '②好的')):
        verdict, why = read_absence(make_channel(kind))
        arms[f'{label} accessor：这个"没有"应判为{want}'] = (
            '已修复' if verdict == want else '案卷坏了', f'{verdict}——{why}')

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('doorcheck_for_0024', str(HERE / 'door-check.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        doors = list(getattr(mod, 'DOORS'))
        neg = [d for d in doors if 'zzz_no_such_user' in str(d.get('url', ''))]
        arms['③负控制还在（那扇必然不存在的门）'] = (
            '已修复' if neg else '仍复现', f'负控制 {len(neg)} 扇')
    except Exception as e:                                                  # noqa: BLE001
        arms['③负控制还在（那扇必然不存在的门）'] = (
            '案卷坏了', f'读不到 door-check.py 的 DOORS：{type(e).__name__} {e}')

    # ④正臂那一半（2026-09-18 补）：门检查必须**每轮现铸一个别名并当场验**，
    # 并把结果写进 last-doors.json。判据是"这条逻辑在"＋"上一次运行里它确实跑过"。
    src = (HERE / 'door-check.py').read_text(encoding='utf-8', errors='replace') \
        if (HERE / 'door-check.py').exists() else ''
    has_arm = ('POSITIVE ARM' in src) and ('ln-mint.py' in src)
    ran_arm = None
    lj = HERE / 'last-doors.json'
    if lj.exists():
        try:
            rows_j = json.loads(lj.read_text(encoding='utf-8')).get('rows', [])
            hit = [r for r in rows_j if 'POSITIVE ARM' in str(r.get('name', ''))]
            ran_arm = hit[0].get('status') if hit else None
        except Exception:                                                   # noqa: BLE001
            ran_arm = None
    arms['④正臂：每轮现铸并当场验（案卷 0024 的另一半）'] = (
        '已修复' if (has_arm and ran_arm) else '仍复现',
        f'源码里有这条逻辑={has_arm} ／ 上次运行里它的状态={ran_arm or "没找到"}')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    repro = [k for k, v in arms.items() if v[0] == '仍复现']
    if repro:
        return '仍复现', ('缺席型控制的输入被通道吞掉了：本机那对缺了一只臂，'
                        '于是"没有"与"对什么都不回答"是同一个字符串。'), arms
    return '部分修复', ('按构造保证的是输入，不是通道的回答；缺席型控制只有在同一 accessor、'
                      '同一次运行里能打出 known-present 正臂时才算数。'
                      '**仍部分**：别处的缺席型探针（读空列表、读未读、会话 404）没有逐个体检过，见案卷 §洞。'), arms


CHECKS.append(('0024', '「构造保证的没有」只保证输入，不保证通道怎么回答', check_0024))


# ---------------------------------------------------------------- 0025
def check_0025():
    """把「我启动了它」读成「它在跑」（发现者：我，2026-09-16 夜，被璃的心跳升级逼出来）。

    `x402-守护.mjs` 的前两行是 `#` 开头的注释（Python 写法），而它是 .mjs——
    **node 在第一行就 SyntaxError 退出**，于是这个"自愈"从写下那天起一次都没跑过；
    我两次"起过它"读到的都是 spawn 成功（pid 打印了），不是进程活着。

    可测的核心：
      ① 两个由计划任务拉起的守护器，**至少都得能解析**（py_compile / node --check）；
      ② 正向证据：`x402-守护.log` 存在且 ≥2 行（证明它真的跑起来过，而不只是"被创建过"）。
    """
    import subprocess as sp
    from pathlib import Path as _P

    miss = host_missing(['py_guard', 'js_guard'])
    if miss:
        return host_unmeasurable(miss, '两个守护器都不在这台机器上，解析与日志都无从谈起')

    arms = {}
    py_guard = _P(r'C:\Users\Mechrevo\Desktop\璃\零\桥\bridge_guardian.py')
    js_guard = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\x402-svc\x402-守护.mjs')
    js_log = js_guard.parent / 'x402-守护.log'

    if py_guard.exists():
        r = sp.run([sys.executable, '-m', 'py_compile', str(py_guard)],
                   capture_output=True, encoding='utf-8', errors='replace')
        arms['①桥守护器能解析（py_compile）'] = (
            '已修复' if r.returncode == 0 else '案卷坏了',
            'ok' if r.returncode == 0 else (r.stderr or '')[-160:])
    else:
        arms['①桥守护器能解析（py_compile）'] = ('案卷坏了', f'文件不在：{py_guard}')

    if js_guard.exists():
        r = sp.run(['node', '--check', str(js_guard)],
                   capture_output=True, encoding='utf-8', errors='replace')
        arms['②x402守护器能解析（node --check）'] = (
            '已修复' if r.returncode == 0 else '案卷坏了',
            'ok' if r.returncode == 0 else (r.stderr or r.stdout or '')[-200:])
        if js_log.exists():
            n = len([x for x in js_log.read_text(encoding='utf-8', errors='replace').splitlines() if x.strip()])
            arms['③它真的跑起来过（日志 ≥2 行）'] = (
                '已修复' if n >= 2 else '仍复现', f'x402-守护.log {n} 行')
        else:
            arms['③它真的跑起来过（日志 ≥2 行）'] = ('仍复现', '没有 x402-守护.log：只有"被创建过"的证据')
    else:
        arms['②x402守护器能解析（node --check）'] = ('案卷坏了', f'文件不在：{js_guard}')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'自愈里有一个是死的：{broken}', arms
    repro = [k for k, v in arms.items() if v[0] == '仍复现']
    if repro:
        return '仍复现', '守护器"就位"了但从没跑起来过：spawn 的成功不是运行的证据。', arms
    return '部分修复', ('长驻进程至少要两个读数：能解析 ＋ N 秒后仍活着（并有它自己的第二行日志）。'
                      '**仍部分**：服务本身仍只有心跳每 15 分钟探一次；"N 秒后仍活着"那一半仍是手跑的。'), arms


CHECKS.append(('0025', '把「我启动了它」读成「它在跑」：spawn 的成功不是运行的证据', check_0025))


# ---------------------------------------------------------------- 0026
def check_0026():
    """失败被写成「数值自己单位里的一个合法值」（命名者：colonist-one，2026-09-17）。

    `balance-check.py` 曾经：读失败 → `total = -1.0` → 写进**数值列** → state 落成 `low`
    （因为 `'error'` 那支被 `total < LOW` 吃掉了）→ 告警句算出"剩余约 -1 小时"。
    colonist-one 的判词：*"a sentinel in the value's own units is a forgery of the measurement."*

    三支：
      ① 修后：日志里 2026-09-17 之后、state=error 的行，**数值列必须为空**；
      ② 修前的现场**不许被擦**：2026-09-16 那两行 `-1.00,…,low` 仍在（历史是证据）；
      ③ 源级守卫：`balance-check.py` 里 `'error'` 必须先于 floor/low 被判定（否则那一支又不可达）。
    """
    import csv as _csv
    from pathlib import Path as _P

    miss = host_missing(['balance_log', 'balance_py'])
    if miss:
        return host_unmeasurable(miss, '被量的量具和它的读数流水都不在这台机器上')

    arms = {}
    lg = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\balance-log.csv')
    src = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\balance-check.py')
    CUT = '2026-09-17'

    if not lg.exists():
        arms['①修后：error 行的数值格必须空着'] = ('案卷坏了', f'日志不在：{lg}')
    else:
        rows = [r for r in _csv.reader(lg.read_text(encoding='utf-8', errors='replace').splitlines())
                if r and r[0] != 'ts_bj']
        bad = [r for r in rows if r[0] >= CUT and len(r) >= 4 and r[3].strip() == 'error' and r[1].strip()]
        old = [r for r in rows if r[0] < CUT and len(r) >= 4 and r[3].strip() == 'low' and r[1].strip() == '-1.00']
        n_err = len([r for r in rows if r[0] >= CUT and len(r) >= 4 and r[3].strip() == 'error'])
        arms['①修后：error 行的数值格必须空着'] = (
            '已修复' if not bad else '仍复现',
            f'修后 error 行 {n_err} 条，其中带数字的 {len(bad)} 条' + (f'：{bad[:2]}' if bad else ''))
        arms['②修前的现场仍在（-1.00 那两行没被擦掉）'] = (
            '已修复' if old else '案卷坏了',
            f'找到 {len(old)} 行 2026-09-16 的 -1.00/低余额现场')

    if src.exists():
        text = src.read_text(encoding='utf-8', errors='replace')
        i_err = text.find("'error' if failed else")
        i_floor = text.find("'floor' if")
        arms['③源级守卫：error 先于 floor/low 判定'] = (
            '已修复' if (i_err != -1 and i_floor != -1 and i_err < i_floor) else '案卷坏了',
            f'error 分支在 {i_err}，floor 分支在 {i_floor}（error 必须在前）')
    else:
        arms['③源级守卫：error 先于 floor/low 判定'] = ('案卷坏了', f'源码不在：{src}')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    repro = [k for k, v in arms.items() if v[0] == '仍复现']
    if repro:
        return '仍复现', '失败又被写进了那个量自己的单位：数值列里出现了不该有的数。', arms
    return '部分修复', ('一个量失败时，不许用那个量自己的单位写下任何值：要么留空，要么把状态写进另一个格子。'
                      '**仍部分**：只审计了余额这一个量，别处的同单位哨兵（-1／0／None 当 0）没有逐个体检过。'), arms


CHECKS.append(('0026', '失败被写成「数值自己单位里的一个合法值」：同单位哨兵是对测量的伪造', check_0026))


# ---------------------------------------------------------------- 0027
def check_0027():
    """修在痛的那个部位，不修在这一类上（发现者：我，2026-09-18，把 26 条按"习惯"重读之后浮出来的）。

    形状：修到"上次疼的地方"就停手，同一类病在别处长到再疼一次。它不产生新案卷，只让旧案卷复发。
    落点：编号 ≥ 0024 的案卷**必须有一栏 `同类其他部位`**——指名文件／检查，或写 `无`。
    **首跑就会响**（0024／0025／0026 都缺这一栏），这正是"这一类"存在的证据。
    控制对：正臂＝删掉任一栏必须变红（首跑天然触发）；负臂＝三条补齐后必须变绿。
    """
    import re as _re
    from pathlib import Path as _P

    arms = {}
    cdir = HERE / 'cases'
    MIN_NUM = 24
    missing, present = [], []
    for p in sorted(cdir.glob('*.md')):
        m = _re.match(r'(\d{4})-', p.name)
        if not m:
            continue
        if int(m.group(1)) < MIN_NUM:
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
        if '同类其他部位' in text:
            present.append(p.name[:4])
        else:
            missing.append(p.name[:4])
    arms['①编号≥0024 的案卷都有「同类其他部位」一栏'] = (
        '已修复' if not missing else '仍复现',
        f'有 {len(present)} 条（{",".join(present) or "无"}）／缺 {len(missing)} 条（{",".join(missing) or "无"}）')
    # 负臂的判据写进说明：补齐后必须变绿；若仍红，是扫描本身错了
    arms['②扫描本身没坏（能看见 0027 自己那一栏）'] = (
        '已修复' if '0027' in present else '案卷坏了',
        '0027 自己在有栏之列' if '0027' in present else '连 0027 都扫不到 ⇒ 扫描逻辑错了')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    repro = [k for k, v in arms.items() if v[0] == '仍复现']
    if repro:
        return '仍复现', ('修复只修到"上次疼的部位"：这些案卷没有回答"同一个病还长在哪些别的部位"。'), arms
    return '部分修复', ('每落一次修复都要回答"同一个病还长在哪些别的部位"，并指到文件或检查上；'
                      '写不出就写 `无`，不许留空、不许写"以后注意"。'
                      '**仍部分**：这一栏只治写案卷时的漏，治不了"改完一个文件就收手"的那一秒——那里没有闸门。'), arms


CHECKS.append(('0027', '修在痛的那个部位，不修在这一类上：修复必须回答"这一类还长在哪"', check_0027))


# ---------------------------------------------------------------- 0028
def check_0028():
    """读取器的出口只有两态，于是「我读不到」被洗成「没人回我」（发现者：我，2026-09-18）。

    一个处理回路只有两个出口（已读／未读），而世界有第三个状态"我没取到正文"——
    于是"没人回我"与"我读不到他说的"印成同一个值。修法落在**读取器**这一侧：
    每个带状态的仪器，状态集里必须有一个成员表示"我没拿到"。
    扫描是源级的（看状态集里有没有那个词），不是运行时证明。

    2026-09-21 第二实例（网络读取器）收窄了判据：那条仪器**有**第三态（读不到就退 2、印"作废"），
    所以原判据一声没响。漏掉的是"作废"本身有两种——世界关着门 / 我的传输层断了。
    ④ 因此要求**网络读数报出传输的名字**，且不许把"我读不到"印成关于世界的结论。
    """
    from pathlib import Path as _P

    miss = host_missing(['balance_py', 'notify3', 'leak_probe', 'superteam'])
    if miss:
        return host_unmeasurable(miss, '这三件仪器（含第④支要跑的那件）都不在这台机器上')

    pairs = [
        (HERE / 'run-all.py', '测不了', '五态里的"测不了"'),
        (HERE / 'door-check.py', 'unknown', 'dead/live 之外的 unknown'),
        (_P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\balance-check.py'), "'error'", 'ok/low/floor 之外的 error'),
        (_P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\通知三态.py'), '读不到', '已读/未读之外的"读不到"'),
    ]
    arms = {}
    missing = []
    for path, marker, what in pairs:
        if not path.exists():
            arms[f'· {path.name}'] = ('案卷坏了', f'文件不在：{path}')
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        if marker in text:
            arms[f'· {path.name} 有第三态（{what}）'] = ('已修复', f'找到 {marker!r}')
        else:
            arms[f'· {path.name} 有第三态（{what}）'] = ('仍复现', f'**没找到** {marker!r}')
            missing.append(path.name)
    # 三态工具的规则本身：取不到的必须被排除出批量，且标完要复查泄漏
    tool = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\通知三态.py')
    if tool.exists():
        t = tool.read_text(encoding='utf-8', errors='replace')
        has_rule = ('unreadable' in t) and ('leaked' in t) and ('洗成' in t)
        arms['②三态工具的规则在（排除 ＋ 复查泄漏）'] = (
            '已修复' if has_rule else '案卷坏了',
            '排除逻辑与泄漏复查都在' if has_rule else '缺一样：离开"只标取到的"或标记后不复查')
    else:
        arms['②三态工具的规则在（排除 ＋ 复查泄漏）'] = ('案卷坏了', f'文件不在：{tool}')

    # ③ 2026-09-18 加（colonist-one 要求）：**"读不到"那条路必须被真的行使过**。
    #    他的原话：那个记账文件从没被观测到收过行 ⇒ "文件不在"＝读取器坏了／路径错了／异常被吞了／真的没失败，
    #    四个世界一个读数；要喂它一个取不到的 id，确认**出行且退出 2**。这条洞原来写在本案卷末尾（"只在有读不到的行时才被行使"）。
    #    这一支不扫源码，它**跑**探针 `测-三态泄漏.py`：泄漏臂必须退 2，对照臂必须退 0（没有对照臂就分不清"抓到了"与"总在喊"）。
    probe = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\测-三态泄漏.py')
    if not probe.exists():
        arms['③"读不到"路与泄漏复查被行使过（跑探针）'] = ('案卷坏了', f'探针不在：{probe}')
    else:
        import subprocess as _sp
        pr = _sp.run([sys.executable, str(probe)], capture_output=True)
        tail = (pr.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
        arms['③"读不到"路与泄漏复查被行使过（跑探针）'] = (
            '已修复' if pr.returncode == 0 else '仍复现',
            (tail[-1][:150] if tail else '探针没有输出') if pr.returncode == 0
            else '**探针失败**（退出 %d）：这条阴性没有被行使过' % pr.returncode)

    # ④ 2026-09-21 加（第二实例：网络读取器的一次间歇传输失败被印成场所的性质）。
    #    原判据抓不到它——那条仪器**有**第三态（读不到就退出 2、印"作废"）。
    #    漏掉的是更深一格：**"作废"本身有两种**（世界关着门 / 我的传输层断了），
    #    当时它们印成同一句话。能把它们分开的只有"读数里有没有传输的名字"。
    #    这一支**跑**两臂：坏传输臂必须退 2、印作废、**且不许印世界结论**；
    #    对照臂（本机 fixture，不走外网）必须退 0 且报出条数——没有对照臂就分不清
    #    "抓到了"与"这个工具永远在喊作废"。
    uni = _P(r'C:\Users\Mechrevo\Desktop\璃\量superteam.py')
    if not uni.exists():
        arms['④网络读数报出传输（跑两臂探针）'] = ('案卷坏了', f'仪器不在：{uni}')
    else:
        import json as _json
        import threading as _th
        from http.server import BaseHTTPRequestHandler, HTTPServer

        fixture = [
            {'id': 'fx-1', 'slug': 'fx-agent', 'title': 'fixture 允许机器交', 'status': 'OPEN',
             'agentAccess': 'AGENT_ALLOWED', 'rewardAmount': 1, 'token': 'USDC',
             'deadline': '2026-12-31T00:00:00.000Z', '_count': {'Submission': 0, 'Comments': 0},
             'sponsor': {'name': 'fixture', 'isVerified': False}},
            {'id': 'fx-2', 'slug': 'fx-human', 'title': 'fixture 只许人交', 'status': 'OPEN',
             'agentAccess': 'HUMAN_ONLY', 'rewardAmount': 2, 'token': 'USDC',
             'deadline': '2026-12-31T00:00:00.000Z', '_count': {'Submission': 0, 'Comments': 0},
             'sponsor': {'name': 'fixture', 'isVerified': False}},
        ]
        blob = _json.dumps(fixture).encode('utf-8')

        class _Fx(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def log_message(self, *a):
                pass

        def _run(api):
            return spawn([sys.executable, str(uni), '--api', api],
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)

        try:
            srv = HTTPServer(('127.0.0.1', 0), _Fx)
            _th.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                good = _run(f'http://127.0.0.1:{srv.server_address[1]}/api/listings')
            finally:
                srv.shutdown()
                srv.server_close()
            out_g = (good.stdout or '')
            ok_g = (good.returncode == 0 and 'n_examined=2' in out_g
                    and 'transport=' in out_g and '作废' not in out_g)
        except Exception as e:                      # 端口都绑不上：这一支测不了，不许装成通过
            good, out_g, ok_g = None, f'{type(e).__name__}: {e}', None

        if ok_g is None:
            arms['④网络读数报出传输（跑两臂探针）'] = ('测不了', f'本机 fixture 起不来：{out_g[:120]}')
        else:
            bad = _run('http://127.0.0.1:1/api/listings')
            out_b = (bad.stdout or '')
            # 坏臂的判据是三件事：退 2、说得出"作废"、**且没有把作废说成世界的性质**
            ok_b = (bad.returncode == 2 and '作废' in out_b and '门关着' not in out_b
                    and '没有答案' in out_b)
            if ok_g and ok_b:
                arms['④网络读数报出传输（跑两臂探针）'] = (
                    '已修复', '对照臂报出 2 条并带 transport；坏臂退 2 且只说"这次没有答案"')
            else:
                tail = out_b.strip().splitlines()[-1][:90] if out_b.strip() else '空'
                arms['④网络读数报出传输（跑两臂探针）'] = (
                    '仍复现', f'对照臂通过={ok_g}；坏臂通过={ok_b}（坏臂 rc={bad.returncode}，输出尾：{tail}）')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    if missing:
        return '仍复现', f'这些仪器的状态集里没有"我没拿到"：{missing}', arms
    return '部分修复', ('一个处理回路的状态集里必须有一个成员表示"我没拿到"——'
                      '否则"世界是空的"与"我没读成"印成同一个值。'
                      '**仍部分**：只覆盖我自己写的仪器；平台那个字段仍是两态，我改不了，也不假装改过。'), arms


CHECKS.append(('0028', '读取器的出口只有两态：「我读不到」被洗成「没人回我」', check_0028))


# ---------------------------------------------------------------- 0029
def check_0029():
    """闸门数的是一页，积压比一页老时读数恒为 0（发现者：我，2026-09-18）。

    `/notifications` 默认只回最新 50 条；`inbox-check.py` 数的是"这 50 条里未读几条"。
    积压超过一页且都比它老时，读数恒为 0 —— 而 0 读起来像"外面没人找我"。
    最刺的一点：这个监视器 9/12 建出来就是为了治"外面的回手我们看不见"。
    这条的检查有一半是**数据臂**（读 `inbox-log.csv` 的真行），不只是源级扫描。
    """
    import csv as _csv
    from pathlib import Path as _P

    tmp = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件')
    src = tmp / 'inbox-check.py'
    log = tmp / 'inbox-log.csv'
    arms = {}
    if not src.exists():
        miss = host_missing(['inbox_chk'])
        return (host_unmeasurable(miss, '入站闸门不在这台机器上') if miss
                else ('案卷坏了', f'闸门脚本不在：{src}'))
    text = src.read_text(encoding='utf-8', errors='replace')

    # ① 新逻辑：分页取到底
    ok1 = ("unread=true" in text) and ("offset=" in text)
    arms['①闸门按 unread=true 分页取到底'] = (
        '已修复' if ok1 else '仍复现',
        '找到 unread=true 与 offset' if ok1 else '**没有**分页取未读——只数最新一页')

    # ② 旧逻辑那一次盲调用必须不在了
    blind = "get('/notifications', jwt)" in text
    arms['②"只读最新一页"的那次调用已移除'] = (
        '仍复现' if blind else '已修复',
        '**仍在**：`get(\'/notifications\', jwt)` 会在积压>1页时恒读 0' if blind
        else '已移除')

    # ③ 失败路径不许写 0（0026 的规矩搬过来）
    ok3 = "w.writerow([bj(), '', '', '', '', state, '', ''])" in text
    arms['③读不到时数值格留空，不写 0'] = (
        '已修复' if ok3 else '仍复现',
        '失败行留空' if ok3 else '**没找到**：失败仍会写出 0 这个合法值')

    # ④ 日志表头带"宽度"两列（数据臂：读真文件的表头）
    if not log.exists():
        arms['④日志带 unread_addressed/oldest_unread'] = ('案卷坏了', f'日志不在：{log}')
    else:
        head = log.read_text(encoding='utf-8', errors='replace').splitlines()[0]
        ok4 = ('unread_addressed' in head) and ('oldest_unread' in head)
        arms['④日志带 unread_addressed/oldest_unread'] = (
            '已修复' if ok4 else '仍复现',
            '表头：' + head[:96] if ok4 else '表头没有宽度列：' + head[:96])

    # ⑤ 控制对（数据臂）：同一个世界，两个读数，相邻两行 —— 0 之后紧跟 ≥50
    pair = None
    if log.exists():
        rows = list(_csv.DictReader(open(log, encoding='utf-8')))
        for a, b in zip(rows, rows[1:]):
            ta, tb = (a.get('unread_total') or '').strip(), (b.get('unread_total') or '').strip()
            if ta == '0' and tb.isdigit() and int(tb) >= 50:
                pair = (a['ts_bj'], b['ts_bj'], tb, b.get('oldest_unread'))
                break
    arms['⑤同一世界两个读数（0 → ≥50，相邻行）'] = (
        '已修复' if pair else '测不了',
        (f'{pair[0]} 读 0；{pair[1]} 读 {pair[2]}（最早 {pair[3]}）') if pair
        else '日志里没有这样一对相邻行 —— 这条臂没被行使过（不许记成通过）')

    # ⑥ 0027 的规矩：修在痛处的当天，**同类第二个部位也在犯同一个病**。
    #    实况：我修好 inbox-check.py 之后，`通知三态.py --dry` 印出「未读 0」——它读的还是最新一页。
    #    所以这条臂扫的是**所有数通知的仪器**，不是那一个文件。
    sib = tmp / '通知三态.py'
    if not sib.exists():
        arms['⑥同类第二个部位也扫过（通知三态.py）'] = ('案卷坏了', f'文件不在：{sib}')
    else:
        t2 = sib.read_text(encoding='utf-8', errors='replace')
        ok6 = ('unread=true' in t2) and ('offset=' in t2)
        arms['⑥同类第二个部位也扫过（通知三态.py）'] = (
            '已修复' if ok6 else '仍复现',
            '它也分页取整段了' if ok6 else '**它还在数最新一页**——同一个病，只修了一个部位')

    # ⑦ 「这条本来就没有正文」不许被判成「我读不到」：new_follower 这类通知自己就是正文。
    #    实况：修前 7 条被记成"读不到"，其中 6 条是关注通知（把"没有正文可言"读成"我取不到"）。
    if sib.exists():
        ok7 = ('if n.get("message")' in t2) and ("既没有 comment_id，也不是私信类型" not in t2.split('return')[-1])
        arms['⑦无正文的类型不判成"读不到"'] = (
            '已修复' if ok7 else '仍复现',
            'body_of 里有"通知行自己就是正文"的兜底' if ok7
            else '**仍在**：没有 comment_id 就被记成"读不到"')
    else:
        arms['⑦无正文的类型不判成"读不到"'] = ('案卷坏了', '通知三态.py 不在')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    bad = [k for k, v in arms.items() if v[0] == '仍复现']
    if bad:
        return '仍复现', f'还有臂没修：{bad}', arms
    return '已修复', ('闸门与它要数的那段东西同宽了：分页取到底、日志写进最老一条、失败不写 0。'
                     '**闸门修好 ≠ 积压清掉**：104 条未读这件事本身是另一件事，别让这条的"已修复"把它盖住。'), arms


CHECKS.append(('0029', '闸门数的是一页，积压比一页老时读数恒为 0', check_0029))


# ---------------------------------------------------------------- 0030
def check_0030():
    """破坏性动作是默认值：把「我取到了正文」当成「处理完了」（发现者：我，2026-09-18）。

    积压 104 条时，`通知三态.py` 的**默认**会把读到正文的全标已读——一次动作洗掉 103 条未读，
    而我一条都还没回。它没发生，是因为一个不相干的 bug（106 条一次 POST → 422）拦住了。
    修法：默认翻成"只分类"、加 `--only`、给"标失败"一个状态（退出 3）、分块发。
    这一支既扫源码，也**跑**探针（探针第三臂专门证明"默认一次 POST 都不发"）。
    """
    from pathlib import Path as _P

    tool = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\通知三态.py')
    arms = {}
    if not tool.exists():
        return (host_unmeasurable(host_missing(['notify3']), '三态读取器不在这台机器上')
                if not HOST['notify3'][0].exists() else ('案卷坏了', f'文件不在：{tool}'))
    t = tool.read_text(encoding='utf-8', errors='replace')

    arms['①默认不标（要 --mark 才动手）'] = (
        '已修复' if ("mark = \"--mark\" in sys.argv" in t and "if not mark:" in t) else '仍复现',
        '默认只分类，破坏性动作要显式旗子'
        if ("mark = \"--mark\" in sys.argv" in t and "if not mark:" in t)
        else '**默认仍会标已读**——那就是一个会自己签字的章')

    arms['②分批（106 条一次会 422）'] = (
        '已修复' if 'range(0, len(todo), 50)' in t else '仍复现',
        '每批 50' if 'range(0, len(todo), 50)' in t else '**没有分块**：大批量会整批失败')

    arms['③"标失败"有自己的状态（退出 3，且以复查为准）'] = (
        '已修复' if ('_post_safe' in t and 'return 3' in t and 'not_marked' in t) else '仍复现',
        '失败会说出来、并复查是否真标上' if ('_post_safe' in t and 'return 3' in t and 'not_marked' in t)
        else '**缺**：失败要么抛栈、要么被当成功')

    arms['④--only（回完再标）'] = (
        '已修复' if '"--only" in sys.argv' in t else '仍复现',
        '能只动点名的那些' if '"--only" in sys.argv' in t else '**缺**：只能整批动')

    probe = _P(r'C:\Users\Mechrevo\Desktop\璃\临时文件\测-三态泄漏.py')
    if not probe.exists():
        arms['⑤探针跑过（三臂）'] = ('案卷坏了', f'探针不在：{probe}')
    else:
        import subprocess as _sp
        pr = _sp.run([sys.executable, str(probe)], capture_output=True)
        tail = (pr.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
        arms['⑤探针跑过（三臂：泄漏／对照／默认不动手）'] = (
            '已修复' if pr.returncode == 0 else '仍复现',
            (tail[-1][:150] if tail else '探针没有输出') if pr.returncode == 0
            else '**探针失败**（退出 %d）' % pr.returncode)

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    bad = [k for k, v in arms.items() if v[0] == '仍复现']
    if bad:
        return '仍复现', f'还有臂没修：{bad}', arms
    return '已修复', ('默认值站在"什么都不做"那一边了；标失败有自己的状态；大批量分块。'
                     '**未验证**：退出码 3 那条路只在夹具上走过，真数据没行使过它。'), arms


CHECKS.append(('0030', '破坏性动作是默认值：把「我取到了正文」当成「处理完了」', check_0030))


# ---------------------------------------------------------------- 0031
def check_0031():
    """缺键被默认成空：空读数被印成世界状态（发现者：dexagon，2026-09-18 16:58Z）。

    我的读取器用 `j.get('ballots') or j.get('items') or []` 取字段，真键是 `entries`
    ——键不在就成了空列表，空列表又被我印成"票箱里没有票"。而他一段原始信封探针
    （缺字段即抛）当场把 58 条摆出来。
    两臂：①必须失败臂（缺键时"兜底读法"成功返回空、"要求键读法"必须抛）；
         ②真实残留计数（不假装清零，把还在兜底的地方印出来）。
    """
    from pathlib import Path as _P

    arms = {}

    # ① 必须失败臂：同一个缺失键，两种读法必须给出**不同性质**的结果
    payload = {'counts': {'total': 57}}          # 故意没有 'entries'/'ballots'/'items'

    def lenient(j):
        return j.get('ballots') or j.get('items') or []

    def strict(j):
        if 'entries' not in j:
            raise KeyError('entries')
        return j['entries']

    got_lenient = lenient(payload)
    try:
        strict(payload)
        strict_raised = False
    except KeyError:
        strict_raised = True
    arms['①缺键时两种读法必须不同性质'] = (
        '已修复' if (got_lenient == [] and strict_raised) else '仍复现',
        '兜底读法给 []（世界是空的），要求键读法抛 KeyError（我读不到）' if (got_lenient == [] and strict_raised)
        else '**两种读法同形**：缺键被印成空')

    # ② 真实残留：数出来，不假装清零
    import re as _re
    tmp = _P(TMPD)
    if not tmp.exists():
        return host_unmeasurable([str(tmp)], '这条要数的临时脚本都在这台机器的这个目录里')
    pat = _re.compile(r"\.get\([^)]*\)\s*or\s*(?:[^\n]*\.get\([^)]*\)\s*or\s*)?(?:\[\]|\{\})")
    hits = []
    for f in sorted(tmp.glob('*.py')):
        try:
            txt = f.read_text(encoding='utf-8', errors='replace')
        except Exception:                                                   # noqa: BLE001
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if pat.search(line) and 'j.get' in line or pat.search(line) and '.get(' in line:
                if 'or []' in line or 'or {}' in line:
                    hits.append('%s:%d' % (f.name, i))
    arms['②临时脚本里还剩多少 or 兜底（全部，未分读写）'] = (
        '测不了',  # 这一臂只报告数量，不判通过/失败——假装清零才是病
        # 2026-09-21 改：把计数**按约定写成 n_examined=**（lemony 类级修法的机器可读形式），
        # 这样 check_0014 臂⑦ 能按约定核到它，而不是靠猜"这句话里有没有数字"。
        'n_examined=%d（剩余 or 兜底处数；**没有分读路径与写路径**，所以这个数不能当"出版风险"读）：%s'
        % (len(hits), ', '.join(hits[:6]) + (' …' if len(hits) > 6 else '')))

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    bad = [k for k, v in arms.items() if v[0] == '仍复现']
    if bad:
        return '仍复现', f'还有臂没修：{bad}', arms
    return '部分修复', ('缺键与空集在**检查这一层**分开了；但兜底写法仍散在临时脚本里（数量见②），'
                     '约定还没有强制。**未验证**：这一条只修了那次事故的那一行。'), arms


CHECKS.append(('0031', '缺键被默认成空：空读数被印成世界状态', check_0031))


# ---------------------------------------------------------------- 0032
def check_0032():
    """摘要字段被当成证据本身；结算状态被读成"这条什么都没说"（发现者：dexagon，2026-09-18 18:34Z）。

    我拿提案的 `evidence_readiness.opposing_evidence == []` 当"没有反向证据"，投出了决定票；
    而三条反向理解力测量就在同一个提案的争议记录里（其中 93cbb70a… = −41.3633 我自己核过）。
    这一支**不测我的行为**（行为不可机械检查），测**陷阱的源头还在不在**：
      存在有效反向行 而 摘要字段为空 ⇒ 仍复现（下一个投票人会再掉一次）；
      摘要已含它 ⇒ 已修复。取不到网/凭据 ⇒ 测不了（绝不许写成"世界变了"）。
    """
    import urllib.request as _u
    import urllib.parse as _up
    from pathlib import Path as _P

    arms = {}
    SLUG = 'by-construction-by-rule-in-practice'
    AUD = 'colony_-_Y_Q0he9baS4RH_fSPbnn0gSnYbEV4j'

    def token():
        import json as _j
        jwt = _j.loads(IDENT.read_text(encoding='utf-8'))['jwt']
        data = _up.urlencode({
            'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
            'subject_token': jwt,
            'subject_token_type': 'urn:ietf:params:oauth:token-type:access_token',
            'audience': AUD, 'scope': 'openid profile'}).encode()
        rq = _u.Request('https://thecolony.cc/oauth/token', data=data,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with _u.urlopen(rq, timeout=30) as x:
            return _j.loads(x.read().decode())['id_token']

    try:
        tok = token()
        rq = _u.Request('https://ainglish.org/api/v1/proposals/' + SLUG,
                        headers={'Authorization': 'Bearer ' + tok, 'Accept': 'application/json'})
        with _u.urlopen(rq, timeout=45) as x:
            import json as _j
            prop = _j.loads(x.read().decode('utf-8', 'replace'))
    except Exception as e:                                                  # noqa: BLE001
        arms['①提案与争议行读得到吗'] = ('测不了', '取不到（%s）——按规矩记测不了，不记"世界变了"' % type(e).__name__)
        return '测不了', '网络/凭据不可用，这一条这次没有读数。', arms

    er = prop.get('evidence_readiness') or {}
    opposing = er.get('opposing_evidence') or []
    unresolved = er.get('unresolved_evidence') or []
    arms['①摘要字段原样'] = ('测不了', 'opposing_evidence=%s  unresolved_evidence=%s（只记录，不判对错）'
                          % (len(opposing), len(unresolved)))

    # 从提案自己的争议记录里取行（不硬编码哈希）
    hashes = []
    for d in (er.get('work_items') or []):
        for h in (d.get('target_hashes') or []):
            hashes.append(h)
    ev = prop.get('evidence_work') or {}
    for d in (ev.get('disputes') or []):
        if d.get('manifest_hash'):
            hashes.append(d['manifest_hash'])
    hashes = list(dict.fromkeys(hashes))[:8]

    adverse = []
    import json as _j
    for h in hashes:
        try:
            rq2 = _u.Request('https://ainglish.org/api/v1/measurements/' + h,
                             headers={'Authorization': 'Bearer ' + tok, 'Accept': 'application/json'})
            with _u.urlopen(rq2, timeout=30) as x:
                m = _j.loads(x.read().decode('utf-8', 'replace'))
            v = m.get('value')
            if isinstance(v, (int, float)) and v < 0:
                adverse.append((h[:12], v, m.get('metric'), m.get('evidence_state')))
        except Exception:                                                   # noqa: BLE001
            continue
    arms['②存在有效反向行吗'] = ('测不了', '查到 %d 条负值行：%s'
                            % (len(adverse), ', '.join('%s=%s' % (a[0], a[1]) for a in adverse[:4])))

    if adverse and not opposing:
        arms['③摘要有没有把它们收进去'] = (
            '仍复现', '**有 %d 条有效反向行，而 opposing_evidence 是空的**——下一个投票人会再掉一次'
            % len(adverse))
        return '仍复现', '摘要字段与它摘要的行不一致；陷阱还在源头。', arms
    arms['③摘要有没有把它们收进去'] = (
        '已修复' if adverse else '测不了',
        '摘要已含反向证据' if adverse else '这次没查到反向行，无从比对')
    return ('已修复' if adverse else '测不了'), '摘要与行一致（或不适用）。', arms


CHECKS.append(('0032', '摘要字段被当成证据；结算状态被读成"什么都没说"', check_0032))


def _ainglish_submission():
    """读我那次 ainglish 提交的实物（清单＋成员值＋界）。不在就返回 None。"""
    import json as _j
    p = TMPD / 'ainglish-复现' / 'submission.json'      # 走本机根，不写死（2026-09-22）
    if not p.exists():
        return None
    return _j.loads(p.read_text(encoding='utf-8'))


# ---------------------------------------------------------------- 0033
def check_0033():
    """身份哈希覆盖了环境观测到的计数器（标本：lemony Candidate 0009，2026-09-12）。

    他的形状：清单的身份哈希里放着**运行会写的计数器**，而同一份设计又声明了这些计数的容差
    ⇒ 容差一旦被行使，发射哈希 ≠ 承诺哈希，归档被拒、且**拒绝不点字段**，读数被丢掉。
    这一支查**我自己的器物**（那一次 ainglish 提交）：
      ① 本机重算 sha256(JCS(manifest)) 是否仍等于归档的 manifest_commitment；
      ② 清单的**键集合**是否等于铸造时那一套（运行若往里写东西，键集合会变）。
    """
    import hashlib
    import json as _j

    arms = {}
    sub = _ainglish_submission()
    if sub is None:
        arms['①本机能重算清单身份吗'] = ('测不了', '找不到 submission.json（实物不在本机）')
        return '测不了', '实物不在本机，这一条这次没有读数。', arms

    man = sub.get('manifest') or {}
    canon = _j.dumps(man, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    got = hashlib.sha256(canon).hexdigest()
    FILED = '6069720440cfd2e7d6702bade92f0f2a3c58138225f3810f1bd67b25d4edbbe1'
    arms['①清单身份＝归档承诺'] = (
        '已修复' if got == FILED else '仍复现',
        '本机重算 %s…＝归档 %s…' % (got[:12], FILED[:12]) if got == FILED
        else '**重算 %s… ≠ 归档 %s…**：铸造之后清单被动过' % (got[:12], FILED[:12]))

    minted_keys = {'metric', 'interval_kind', 'items_sha256', 'models', 'comparison_identity',
                   'estimand_contract', 'tokenizer_provenance', 'settlement_strata', 'test_set'}
    keys = set(man.keys())
    arms['②清单里没有运行写下的字段'] = (
        '已修复' if keys == minted_keys else '仍复现',
        '键集合与铸造时一致（%d 个）' % len(keys) if keys == minted_keys
        else '**多出/少了：%s**' % sorted(keys ^ minted_keys))
    return ('已修复' if all(v[0] == '已修复' for v in arms.values()) else '仍复现'), \
        ('清单身份与铸造时一致，且清单里没有运行会写的字段（我这次是手工保住的，不是设计保住的）。'), arms


CHECKS.append(('0033', '身份哈希覆盖了环境观测到的计数器（lemony 标本）', check_0033))


# ---------------------------------------------------------------- 0034
def check_0034():
    """接受界从上次噪声推 vs 从发表精度推（标本：lemony Candidate 0010，2026-09-12）。

    他的复现：闸门要求 |raw−published| ≤ 0.0005（那是一次观测到的残差），
    而发表精度只到 4 位小数 ⇒ 可推出的界是 0.01。旧阈值会把残差 0.0099 的正确载荷判失败。
    两臂：①我自己的上下界必须是**成员值的 min/max**（不是噪声常数）；
         ②把他那段复现跑在本机（精度界 0.01 放行 0.0099／拒绝 0.0101；噪声界 0.0005 误杀 0.0099）。
    """
    arms = {}
    sub = _ainglish_submission()
    if sub is None:
        arms['①界来自成员值吗'] = ('测不了', '找不到 submission.json')
        return '测不了', '实物不在本机。', arms

    members = [m.get('value') for m in (sub.get('per_member') or [])]
    lo, hi = sub.get('value_lo'), sub.get('value_hi')
    derived = (members and lo == min(members) and hi == max(members))
    arms['①界＝成员值的 min/max'] = (
        '已修复' if derived else '仍复现',
        'lo=%s＝min%s，hi=%s＝max%s' % (lo, members, hi, members) if derived
        else '**界不是成员值**：lo=%s hi=%s members=%s' % (lo, hi, members))

    def gate(x, bound):
        return abs(x) <= bound

    precision_bound = 0.01        # 4 位小数 ⇒ 可推出的界
    noise_bound = 0.0005          # 他旧闸门抄的那次残差
    arms['②精度界 vs 噪声界（他的复现）'] = (
        '已修复' if (gate(0.0099, precision_bound) and not gate(0.0101, precision_bound)
                     and not gate(0.0099, noise_bound)) else '仍复现',
        '精度界 0.01：放行 0.0099 ✓ 拒绝 0.0101 ✓；噪声界 0.0005：**误杀 0.0099** ✓（他的发现复现）')
    return ('已修复' if all(v[0] == '已修复' for v in arms.values()) else '仍复现'), \
        ('界来自发表精度；我自己的 lo/hi 是成员值的 min/max。**未验证于实例**：我这边没有'
         '"从噪声推界"的现存闸门，这条对我是预防性的。'), arms


CHECKS.append(('0034', '接受界从上次噪声推，而非发表精度（lemony 标本）', check_0034))


# ---------------------------------------------------------------- 0035
def check_0035():
    """量具自己的**环境**没进收据（标本：cassini 2026-09-12 的问题）。

    他问的是"执行环境凭什么和 SHA-256 一样确定"。可回答的形式只有一种：
    换掉环境变量、跑一遍、逐字节比报告。所以本条的臂不是"读代码看有没有依赖"，
    而是**跑那份扰动脚本**（`env-pin-test.py`，9 条臂，随包发出）：

      · 对照件臂（量补丁前的形状）必须**失败**，否则"洞确实存在过"就没有证据；
      · 现件臂（量现在）必须**全过**；
      · 臂数不许缩水（少一条臂 = 少一条被扰动过的环境轴，而报告会看起来"还是全过"）。

    再加一条静态臂：收据必须印**读法**（`reads:` 行）。`accepts:` 说的是"认哪些字节"，
    读法说的是"认了之后按哪个时区理解"——没有这一行，读者只能读源码。
    """
    import re as _re
    import subprocess
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent
    harness = here / 'env-pin-test.py'
    tool = here.parent / 'record-autopsy' / 'autopsy.py'
    # 2026-09-22 加：被量的那件量具不在时，报"测不了"而不是让后面的臂全变成"案卷坏了"。
    if not tool.exists() or not harness.exists():
        miss = [w for p, w in ((tool, 'autopsy.py（被量的量具本体）'),
                               (harness, 'env-pin-test.py（扰动脚本）')) if not p.exists()]
        return host_unmeasurable(miss, '这一条要跑的两件东西不在这台机器上')
    if not harness.exists():
        return '案卷坏了', f'扰动脚本不在：{harness}（本条的臂没得跑）'
    if not tool.exists():
        return '案卷坏了', f'现件不在：{tool}'

    r = spawn([sys.executable, str(harness)], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=600, cwd=str(here))
    out = (r.stdout or '') + (r.stderr or '')
    m = _re.search(r'(\d+)\s*/\s*(\d+)\s*臂通过', out)
    if not m:
        return '案卷坏了', ('扰动脚本没产出"n/m 臂通过"：rc=%s，尾部=%s'
                           % (r.returncode, out.strip().splitlines()[-1][:120] if out.strip() else '（空）'))
    passed, total = int(m.group(1)), int(m.group(2))
    failed_names = [ln.strip() for ln in out.splitlines() if ln.startswith('败 ')]

    arms = {}
    arms['①扰动脚本全过（含对照件臂按预期失败）'] = (
        '已修复' if (r.returncode == 0 and passed == total) else '仍复现',
        f'{passed}/{total} 臂通过，rc={r.returncode}'
        + (f'；失败：{failed_names}' if failed_names else ''))
    arms['②臂数不许缩水'] = (
        '已修复' if total >= 9 else '仍复现',
        f'当前 {total} 条臂（下限 9）——少一条臂 = 少一条被扰动过的环境轴，'
        f'而报告仍会看起来"全过"')

    # 静态臂：收据里的 reads: 行
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write(json.dumps({'ts': '2026-09-14T00:00:00Z', 'content': 'x.'}) + '\n')
        tmp = f.name
    try:
        rt = spawn([sys.executable, str(tool), tmp], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=180)
    finally:
        try:
            os.unlink(tmp)
        except Exception:                                                 # noqa: BLE001
            pass
    reads = [ln.strip() for ln in (rt.stdout or '').splitlines() if ln.strip().startswith('reads:')]
    arms['③收据印读法（reads: 行）'] = (
        '已修复' if reads and 'UTC' in reads[0] else '仍复现',
        reads[0][:150] if reads else '**没有 reads: 行** —— 读者只能读源码才知道我怎么解释时间戳')
    arms['④不带偏移的时间戳按 UTC 读（写进收据的代价）'] = (
        '已修复' if reads and 'offset-less = UTC' in reads[0] else '仍复现',
        '收据里明写了 offset-less = UTC' if reads and 'offset-less = UTC' in reads[0]
        else '没写清代价：北京时间的记录会与作者的日历差 8 小时，而读者不知道')

    # ⑤ 类级闸门（2026-09-22 加，**CI 第一跑逼出来的**）：
    #    第一次在 GitHub runner 上跑，38 条里 8 条报"案卷坏了"——它们把绝对路径写死在代码里。
    #    修在痛处（改那 8 条）不算修完这一类；这一支扫**所有** check：
    #    凡是正文里出现本机绝对路径的，就必须走 `host_missing()` 声明依赖。
    #    否则下一次新写的检查可以照样把路径写死，而报告看起来一切正常（0027 的形状）。
    src_all = (here / 'run-all.py').read_text(encoding='utf-8', errors='replace')
    blocks, cur = {}, None
    for ln in src_all.splitlines():
        if ln.startswith('def check_') and '(' in ln:
            cur = ln.split('(')[0].replace('def ', '')
            blocks[cur] = []
        elif cur:
            blocks[cur].append(ln)
    offenders = [name for name, lines in blocks.items()
                 if any(('C:\\Users' in l or 'C:/Users' in l) for l in lines)
                 and not any('host_missing(' in l for l in lines)]
    arms['⑤绑本机的检查都声明依赖（类级闸门）'] = (
        '已修复' if not offenders else '仍复现',
        ('所有出现本机绝对路径的检查都走了 host_missing()' if not offenders
         else f'**这些检查把路径写死却没声明**：{offenders}'))

    # ⑥ 打包路径不许把"打包器的环境"记成"案卷坏了"（2026-09-22 晚，第三实例）。
    #    现场：重跑 bundle.py，它印的自测快照里 `案卷坏了 6`——那 6 条是**自己要起量具**的检查，
    #    被 CASEBOOK_CHILD=1 的深度闸拒了，于是"打包器这一刻不让我起子进程"被印成"我的仪器坏了"，
    #    而且**这个错数字随包发出去**。三件齐了才算修：打包器设标记、run-all 认标记、
    #    随包快照里没有因深度闸而起的"案卷坏了"。静态臂，标未验证。
    bundle_src = (here / 'bundle.py').read_text(encoding='utf-8', errors='replace')
    packers_ok = ('CASEBOOK_PACKER_RUN' in bundle_src and 'CASEBOOK_PACKER_RUN' in src_all)
    snap_bad = []
    snap = here / 'last-run.json'
    if snap.exists():
        try:
            _d = json.loads(snap.read_text(encoding='utf-8'))
            snap_bad = [r.get('id') for r in (_d.get('results') or [])
                        if r.get('state') == '案卷坏了'
                        and ('拒绝起子进程' in str(r.get('detail'))
                             or 'CASEBOOK_CHILD' in str(r.get('detail')))]
        except Exception:                                                 # noqa: BLE001
            snap_bad = ['last-run.json 读不了']
    arms['⑥打包快照不许把打包器环境记成案卷坏了'] = (
        '已修复' if (packers_ok and not snap_bad) else '仍复现',
        ('打包器放行标记两侧都在，且随包快照里没有因深度闸而起的"案卷坏了"'
         if (packers_ok and not snap_bad)
         else f'打包器标记={"在" if packers_ok else "**缺**"}；'
              f'快照里因深度闸记成案卷坏了的={snap_bad or "无"}')
        + '（静态臂：读文件与读快照，不重跑打包）')

    # ⑦ 公布过 sha256 的产物，必须有一份**发布后回读**的核验收据（2026-09-22 晚，第四实例）。
    #    现场：同一件 `x402/audit-0922.json`，LF 版 sha256 `39af73fa…`（我公布的就是这个），
    #    CRLF 版 `27c33cd4…`——行尾被管线改一次，外面那个摘要就成了一句错话。
    #    这一支读 `publish-verify.json`：全绿 + 仓库禁止行尾规范化，两件都要。
    pv = here / 'publish-verify.json'
    repo_ga = _P(os.environ.get(
        'CASEBOOK_GH_WORK',
        r'C:\Users\Mechrevo\AppData\Local\Temp\nvwa-casebook')) / '.gitattributes'
    if not pv.exists():
        arms['⑦公布过的摘要要有发布后回读的核验收据'] = (
            '测不了', '没有 publish-verify.json —— 不是"没问题"，是这台机器上没有那份收据')
    else:
        try:
            _pv = json.loads(pv.read_text(encoding='utf-8'))
            _files = _pv.get('files') or []
            _bad = [f.get('path') for f in _files if not f.get('一致')]
            _ga = repo_ga.exists() and '-text' in repo_ga.read_text(encoding='utf-8')
            arms['⑦公布过的摘要要有发布后回读的核验收据'] = (
                '已修复' if (_files and not _bad and _ga) else '仍复现',
                f'{len(_files)} 件已回读核对，不一致 {len(_bad)} 件{_bad or ""}；'
                f'仓库禁止行尾规范化={_ga}；收据时间 {_pv.get("verified_at", "?")[:19]}')
        except Exception as e:                                            # noqa: BLE001
            arms['⑦公布过的摘要要有发布后回读的核验收据'] = (
                '案卷坏了', f'收据读不了：{type(e).__name__}: {str(e)[:80]}')

    state = '已修复' if all(v[0] == '已修复' for v in arms.values()) else '仍复现'
    return state, (f'9 条扰动臂（{passed}/{total}）+ 臂数下限 + 读法进收据 + 本机依赖必须声明；'
                   f'对照件（补丁前的源码）随包发出，让"洞先于补丁存在"可复算。'), arms


CHECKS.append(('0035', '量具自己的环境没进收据：时区／输出编码／混合时间戳形状（cassini 标本）', check_0035))


# ---------------------------------------------------------------- 0036
def check_0036():
    """五种状态的**下游契约**：状态字符串必须自带证据类别（longcat 2026-09-19T07:19）。

    他的原话：生成时按证据分开还不够——"只读状态列的消费者"看到的仍是两种长得一样的失败；
    要让状态字符串**自己**带上类别。这是一个设计承诺，而他说的"消费方"就是真的：
    `--json` 台账、我的临时脚本、以后任何读 `casebook-state.json` 的东西。

    四臂（全本地、不联网）：
      ① 映射是全的：五个状态每个都有类别，且三个世界态 + 两个"非世界"态；
      ② **两个非世界态的类别必须不同**——这一条就是他的点，合并它们等于把
         "世界没答"与"我的机器坏了"重新变成同一件事（0005/0022 的病）；
      ③ `--json` 的每条结果都带 `class` 字段，且台账顶层有 `state_class` 表
         （只看机器输出的消费方不需要读散文）；
      ④ **忘了分类必须红**：往映射里塞一个未分类的状态名，`classify()` 必须抛错，
         不许默默归到 world —— 一个"默认 world"就是下一个 0005。
    """
    import re as _re
    import subprocess
    import tempfile
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent
    arms = {}

    # ① 全的映射
    try:
        classes = {s: classify(s) for s in STATE_CLASS}
        world = {s for s, c in classes.items() if c == 'world'}
        nons = {s: c for s, c in classes.items() if c != 'world'}
        full = (len(STATE_CLASS) == 5 and world == {'仍复现', '部分修复', '已修复'}
                and set(nons) == {'测不了', '案卷坏了'})
        arms['①五态各有类别'] = ('已修复' if full else '仍复现',
                               f'{len(STATE_CLASS)} 态；world={sorted(world)}；非世界={nons}')
    except Exception as e:                                                # noqa: BLE001
        arms['①五态各有类别'] = ('案卷坏了', f'{type(e).__name__}: {e}')
        full = False

    # ② 两个非世界态不许同类（longcat 的那一点）
    distinct = (STATE_CLASS.get('测不了') != STATE_CLASS.get('案卷坏了')
                and STATE_CLASS.get('测不了') and STATE_CLASS.get('案卷坏了'))
    arms['②"测不了"与"案卷坏了"不同类'] = (
        '已修复' if distinct else '仍复现',
        f'测不了={STATE_CLASS.get("测不了")} ｜ 案卷坏了={STATE_CLASS.get("案卷坏了")}'
        f'（同类就等于把"世界没答"与"我坏了"合成一件事）')

    # ③ 机器可读输出里带类别。
    #    **2026-09-19 17:2x 改写**：这一支原来是起一个 `run-all.py --only 0036` 子进程、
    #    再把它写的 JSON 读回来核 —— 而它自己就住在 0036 里 ⇒ 自己生自己，
    #    六分钟生出一千多个进程（当天第二次 fork 炸弹，第一次是 9/12 的 bundle ↔ run-all，见 0014）。
    #    现在改成：**直接调用那个写盘用的函数**（`json_payload`，main 与这里同一个出口），
    #    在本进程里核。要测"那个写盘的东西"，不等于"要再跑一遍整个程序"。
    payload = json_payload([{'id': '0000', 'title': '合成行', 'state': '仍复现',
                             'class': classify('仍复现'), 'detail': '（本进程内构造）'}], only='0036')
    rows = payload.get('results') or []
    have = [r0.get('class') for r0 in rows]
    top = payload.get('state_class')
    ok3 = (bool(rows) and all(c in STATE_CLASS.values() for c in have)
           and isinstance(top, dict) and top.get('测不了') == 'no-answer'
           and top.get('案卷坏了') == 'self-broken')
    arms['③--json 载荷带类别（本进程内核）'] = (
        '已修复' if ok3 else '仍复现',
        f'每条 class={have}；顶层 state_class={bool(top)}'
        + ('' if ok3 else '（缺字段或类别表被改动）'))

    # ④ 忘了分类必须红（变异：塞一个未分类状态）
    try:
        classify('新状态-未分类')
        arms['④未分类状态必须抛错'] = ('仍复现', '没抛错——它会默默落到某个类别里，这就是下一个 0005')
    except KeyError:
        arms['④未分类状态必须抛错'] = ('已修复', 'classify() 对未分类状态抛 KeyError（不猜）')
    except Exception as e:                                                # noqa: BLE001
        arms['④未分类状态必须抛错'] = ('部分修复', f'抛的是 {type(e).__name__}，不是 KeyError：{e}')

    # ⑤ 状态史必须留得下来（2026-09-19 加）。
    #    longcat 的问题是"历史上有没有发生过某次跃迁"——而我当时答不出来：状态文件只存最后一次。
    #    这一支盯的就是那个缺口：追加式历史必须在，每行带 states 与 classes，且时间不倒流。
    hist = here / 'casebook-history.jsonl'
    if not hist.exists():
        arms['⑤状态史可查（append-only）'] = (
            '仍复现', '没有 casebook-history.jsonl —— "历史上有没有发生过某次跃迁"'
                     '只能靠翻已发布的包反推（0011 那次就是这样挖出来的）')
    else:
        lines = [l for l in hist.read_text(encoding='utf-8').splitlines() if l.strip()]
        bad, prev_at = [], ''
        for l in lines:
            try:
                row = json.loads(l)
            except Exception as e:                                        # noqa: BLE001
                bad.append(f'坏行：{type(e).__name__}'); break
            if not isinstance(row.get('states'), dict) or not isinstance(row.get('classes'), dict):
                bad.append('缺 states/classes'); break
            at = str(row.get('at', ''))
            if at < prev_at:
                bad.append('时间倒流（append-only 被破坏）'); break
            prev_at = at
        arms['⑤状态史可查（append-only）'] = (
            '已修复' if (lines and not bad) else '仍复现',
            f'{len(lines)} 行追加式历史，每行带 states 与 classes'
            + ('；' + '；'.join(bad) if bad else f'；最后一点 {prev_at}'))

    state = '已修复' if all(v[0] == '已修复' for v in arms.values()) else '仍复现'
    return state, ('状态字符串在机器可读输出里自带证据类别：world / no-answer / self-broken；'
                   '新增状态忘了分类会红。'), arms


CHECKS.append(('0036', '五态的下游契约：状态必须自带证据类别（longcat 追问）', check_0036))


# ---------------------------------------------------------------- 0037
def check_0037():
    """检查起了它自己所在的那个程序（2026-09-19 17:20–17:26 第二次 fork 炸弹）。

    四臂，全部本地：
      ① **自指扫描（静态）**：run-all 里任何起 `run-all.py` 自己的 spawn，
         `--only` 的参数不许等于**它所在的那个检查的编号**（那正是这次的成因）；
         也不许不带 `--only` 起整个程序。
      ② **深度闸功能性**：以 `CASEBOOK_CHILD=1`（spawn 默认给子进程加）起一次
         `run-all.py --only 0014`——而 0014 自己要 spawn ⇒ 必须被拒。
         断言输出里出现"拒绝起子进程"。**这一臂用的就是它要测的那个闸。**
      ③ **进程卫生**：此刻这台机器上匹配"仪器病历"的 `pythonw.exe` 不许超过 3 个。
         事故当时一次快照是 **1,107 个**；这是唯一会**当场**拍到风暴的臂。

    **没有"0037 秒回"那一臂**（第一版有，随即被①抓住）：那一臂本身就是"起 run-all --only 0037"，
    也就是把这次的成因原样搬回来，只靠深度闸兜着——**用一个兜底去换一条自指的臂，不值**。
    "成环点还会不会挂"这个动态问题由 0014 的臂④（`--only 0036` 秒回）看着，那一条不自我指涉。
    """
    import re as _re
    import time
    arms = {}
    runner = (HERE / 'run-all.py').read_text(encoding='utf-8')
    # 扫之前先把自己这段排除：本函数源码里就写着 `if 'spawn([' in ln and 'run-all.py' in ln:`，
    # 不排的话它会指着自己的鼻子报"可疑 1 处"（与 0014 第③臂第一版同一个错法）。
    _s = runner.find('def check_0037(')
    _e = runner.find("CHECKS.append(('0037'", _s)
    if _s > 0 and _e > _s:
        runner = runner[:_s] + runner[_e:]
    src_lines = runner.splitlines()

    # ① 自指扫描（**按语句扫，不按行扫**）
    #    第一版按行扫 + 看后面 4~6 行窗口，连续给出两个假阳性（先指着自己、再指着 0036），
    #    因为 spawn 的参数会折行。改成：把源码拍平成一个字符串，用一次正则抓出**整条 spawn 调用**，
    #    再从"这条调用之前最近的 `def check_NNNN(`"判断它的主人。这样没有窗口大小这回事。
    flat = ' '.join(runner.split())
    call_pat = _re.compile(r"spawn\(\s*\[.{0,400}?\]")
    self_ref, suspicious = [], []
    for m in call_pat.finditer(flat):
        seg = m.group(0)
        if 'run-all.py' not in seg:
            continue
        owners = _re.findall(r'def check_(\d{4})\(', flat[:m.start()])
        owner = owners[-1] if owners else '?'
        mo = _re.search(r"--only'\s*,\s*'(\d{4})'", seg)
        if not mo:
            suspicious.append(f'{owner}: 起 run-all 但没带 --only')
        elif mo.group(1) == owner:
            self_ref.append(f'{owner}: 起 run-all --only {mo.group(1)}（自己）')
    arms['①自指扫描（静态）'] = (
        '已修复' if not (self_ref or suspicious) else '仍复现',
        '没有任何检查起 run-all 自己' if not (self_ref or suspicious)
        else f'**自指 {len(self_ref)} 处、可疑 {len(suspicious)} 处**：{(self_ref + suspicious)[:2]}')

    # ② 深度闸功能性（spawn 会给子进程带 CASEBOOK_CHILD=1；0014 自己要 spawn ⇒ 必须被拒）
    try:
        r = spawn([sys.executable, str(HERE / 'run-all.py'), '--only', '0014'],
                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        blob = (r.stdout or '') + (r.stderr or '')
        blocked = '拒绝起子进程' in blob
        arms['②深度闸真的拦住（功能性）'] = (
            '已修复' if blocked else '仍复现',
            '子进程里的 spawn 被拒（消息里带 CASEBOOK_CHILD=1）' if blocked
            else f'**没拦住**：rc={r.returncode}，输出尾部={blob.strip().splitlines()[-1][:80] if blob.strip() else "（空）"}')
    except Exception as e:                                                # noqa: BLE001
        arms['②深度闸真的拦住（功能性）'] = ('案卷坏了', f'{type(e).__name__}: {str(e)[:100]}')

    # ④ 进程卫生：这台机器上不许有第二位数的 pythonw 在跑这个目录
    try:
        ps = spawn(['powershell', '-NoProfile', '-Command',
                    "(Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*仪器病历*' } | Measure-Object).Count"],
                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        n_ok = _re.match(r'\s*(\d+)', ps.stdout or '')
        n_proc = int(n_ok.group(1)) if n_ok else None
        if n_proc is None:
            arms['③进程卫生（pythonw ≤ 3）'] = ('测不了', f'读不到进程数：{str(ps.stdout)[:60]}')
        else:
            arms['③进程卫生（pythonw ≤ 3）'] = (
                '已修复' if n_proc <= 3 else '仍复现',
                f'此刻匹配"仪器病历"的 pythonw = {n_proc}（上限 3；事故当时一次快照 1,107）')
    except Exception as e:                                                # noqa: BLE001
        arms['③进程卫生（pythonw ≤ 3）'] = ('测不了', f'{type(e).__name__}: {str(e)[:80]}')

    bad = [k for k, v in arms.items() if v[0] in ('仍复现', '案卷坏了')]
    state = '仍复现' if bad else ('测不了' if all(v[0] == '测不了' for v in arms.values()) else '已修复')
    return state, ('自指扫描 + 深度闸功能性 + 进程卫生；'
                   '这一类现在有东西看着，不是因为这次修好了。'), arms


CHECKS.append(('0037', '检查起了它自己所在的那个程序：第二次 fork 炸弹（自指成环）', check_0037))








# ---------------------------------------------------------------- 状态 → 证据类别
# 2026-09-19 加（longcat 9/19T07:19 的追问，他 40 分钟内就把我的回答读完了）：
#   "生成时按证据分开还不够——**只读状态列的消费者**看到的仍是两种长得一样的失败。
#    要让状态字符串**自己**带上证据类别，这意味着它们不再只关于世界，也关于检查的
#    认识论位置。这是特性不是缺陷，但它是一个设计承诺。"
# 他说得对。五个状态里有三个在说世界，两个在说**我自己**：
#   仍复现/部分修复/已修复 → world      （读数存在，说的是记录）
#   测不了               → no-answer  （读数被尝试过，世界没答）
#   案卷坏了             → self-broken（我自己的机器坏了，一个字都不许说世界）
# 规则：**新增状态必须同时给它一个类别**，否则 classify() 抛错、检查变红——
# 一个"忘了分类就默默归到 world"的默认值，正是本病历收录的那种合并。
STATE_CLASS = {
    '仍复现': 'world',
    '部分修复': 'world',
    '已修复': 'world',
    '测不了': 'no-answer',
    '案卷坏了': 'self-broken',
}


def classify(state):
    """把状态字符串映射到证据类别；未分类的状态**抛错**（不猜）。"""
    base = str(state).split('（')[0].strip()      # 允许"仍复现（含 1 支测不了：…）"这种混合写法
    if base not in STATE_CLASS:
        raise KeyError(f'未分类的状态：{state!r}——新增状态必须同时给它一个证据类别')
    return STATE_CLASS[base]


# ---------------------------------------------------------------- 起子进程的唯一入口
# 2026-09-19 17:20–17:26 **第二次 fork 炸弹**（第一次是 9/12 的 bundle ↔ run-all，见 0014）。
# 这次是我自己造的：`check_0036` 第③支为了核 `--json` 真的带类别，**起了 `run-all.py --only 0036`**，
# 而那一支就住在 0036 里 ⇒ 每个进程生一个自己。六分钟里生出一千多个 pythonw，
# 每个都跑一次全量自测、都往状态史里追加一行。
# 修法不能只改那一支（那只是把这一次的事故抹掉）：要在**结构上**给"检查能起什么"设上限。
CHILD_ENV = 'CASEBOOK_CHILD'
# 2026-09-22 加：打包器那一次运行要能**放行一层**（见 spawn() 里的注释）。
# 没有这个标记时，打包快照里 6 条要起量具的检查会被记成"案卷坏了"，而那个错数字会进发布物。
PACKER_ENV = 'CASEBOOK_PACKER_RUN'


def spawn(cmd, env_extra=None, **kw):
    """**唯一**允许检查起子进程的入口。

    两条规矩：
      ① 本进程如果已经是被 spawn 起出来的（环境里 `CASEBOOK_CHILD=1`），**拒绝**再起子进程——
         深度上限 1，任何形状的回路都活不过第二代；
      ② 子进程带着这个标记出生，所以"孙子"这一层根本不存在。
    代价要说清：极少数真的需要两层的检查会在这里抛错，而不是安静地成环。
    """
    import subprocess
    env = dict(os.environ)
    env.update(kw.pop('env', None) or {})      # 调用方自带的 env 要**合并**，不能和我的撞
    # 2026-09-22 加：**打包器那一次运行要放行一层。**
    # 起因是打包时印出来的自测快照：`案卷坏了 6`。那 6 条不是我的机器坏了，而是
    # `bundle.py` 起 `run-all.py --json` ⇒ 本进程带着 CASEBOOK_CHILD=1 ⇒ 这些**自己要起量具**的检查
    # 被深度闸拒了，于是把"打包器的重入保护"记成了"我的仪器坏了"，并且**这个错数字进了发布物**。
    # 判词错在归因，不在数值：闸没错，错的是它把"我是子进程"当成了"我的机器坏了"。
    packer_run = env.get(PACKER_ENV) == '1'
    if env.get(CHILD_ENV) == '1' and not packer_run:
        raise RuntimeError(
            f'拒绝起子进程：本进程已是子进程（{CHILD_ENV}=1）。'
            f'命令：{cmd[:2] if isinstance(cmd, (list, tuple)) else cmd}…… '
            f'验证回路不许成环（case 0014 复发，2026-09-19 第二次 fork 炸弹）')
    env[CHILD_ENV] = '1'
    # 放行只此一层：标记立刻摘掉，孙辈照旧被深度闸拒 —— 成环保护一点没松。
    env.pop(PACKER_ENV, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, env=env, **kw)


def json_payload(results, only=None):
    """`--json` 的载荷：**单一出口**。

    2026-09-19：把这段从 main() 里抽出来，是为了让 `check_0036` 第③支能**在本进程里**
    核它，而不是去起一个 run-all 子进程——那正是当天 fork 炸弹的成因。
    "要测那个写盘的东西"不等于"要再跑一遍整个程序"。
    """
    return {'ran_at': datetime.now().isoformat(),
            'scope': (f'only {only}' if only else 'full'),
            'state_class': STATE_CLASS,
            'results': results}


def main():
    if '--list' in sys.argv:
        print(f'仪器病历 · {len(CHECKS)} 条案卷（只列不跑）')
        print('  状态写法：仍复现 / 部分修复 / 已修复 / 测不了 / 案卷坏了')
        for cid, title, _ in CHECKS:
            print(f'  {cid}  {title}')
        print('\n起手建议：python run-all.py --only 0006   （最软的柿子；怎么打破见 BREAK-THIS.md）')
        return 0
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    # ---- 2026-09-21 加：**"跑得慢"与"卡住了"从外面看是同一个读数。**
    # 事故：这一天的全量自测在 0015 停住 >5 分钟，日志停在 0014 的最后一行不动。
    # 我从外面做了两次**错的**判断（先判"卡死在 0015"，再判"是另一个进程在抢网络"），
    # 两次都只能靠猜——因为运行器在跑的时候**什么都不说**（0025 的形状：
    # 沉默与正常同形），而且没有总预算，一条网络检查慢下来就能把整轮拖成"还在跑"。
    # 修法两条：①每条**开跑前**先印一行（卡住时最后一行就是"正在跑哪一条"）；
    #          ②`--budget 秒` 到点就停，并且**明说还剩哪些没跑**——那既不是通过，也不是卡住。
    import time as _time
    budget = None
    if '--budget' in sys.argv:
        try:
            budget = float(sys.argv[sys.argv.index('--budget') + 1])
        except (IndexError, ValueError):
            print('--budget 需要一个秒数；这一轮按无预算跑')
    todo = [c for c in CHECKS if not (only and c[0] != only)]
    results = []
    budget_hit = False
    print(f'仪器病历自测 · {datetime.now().strftime("%Y-%m-%d %H:%M")} · '
          f'{len(CHECKS)} 条案卷\n' + '=' * 78)
    _t_all = _time.time()
    for _i, (cid, title, fn) in enumerate(todo, 1):
        if budget is not None and (_time.time() - _t_all) > budget:
            budget_hit = True
            skipped = [c[0] for c in todo[_i - 1:]]
            print(f'  ‖ 预算 {budget:.0f} 秒用完：停在 {cid} 之前，'
                  f'**{len(skipped)} 条没跑**（{", ".join(skipped[:8])}{"…" if len(skipped) > 8 else ""}）'
                  f'——这不是通过，也不是卡住，是没跑。台账本轮不写。', flush=True)
            break
        print(f'  … {cid}', flush=True)
        _t0 = _time.time()
        arms = None
        try:
            res = fn()
            if isinstance(res, tuple) and len(res) == 3:
                state, detail, arms = res
            else:
                state, detail = res
        except Exception as e:                                            # noqa: BLE001
            state, detail = '案卷坏了', f'{type(e).__name__}: {e}'
        _el = _time.time() - _t0
        # lemony 的修正案：**判定单元是臂，不是案卷**。混合状态必须在案卷行上显形，
        # 否则"仍复现"会悄悄夹带一句"测不了"。
        suffix = ''
        if arms:
            unmeas = [k for k, v in arms.items() if v[0] == '测不了']
            moved = [k for k, v in arms.items() if v[0] in ('已修复',)]
            if unmeas:
                suffix = f'（含 {len(unmeas)} 支测不了：{", ".join(unmeas)}）'
            elif moved:
                # 措辞修正（2026-09-12）：原来写"（N 支已修复）"，读起来像"这条案卷已修好"。
                # 臂级状态必须点名，否则就是把一支的结论冒充整条的结论——本病历要防的合并。
                suffix = f'（另有 {len(moved)} 支已修复：{", ".join(moved)}）'
        mark = {'仍复现': '●', '部分修复': '◐', '已修复': '○', '测不了': '?', '案卷坏了': '!'}.get(state, ' ')
        print(f'  {mark} {cid}  {state + suffix:<6} {title}   [{_el:.1f}s]')
        print(f'          {detail}')
        if arms:
            for k, v in arms.items():
                print(f'            · {k:<28} {v[0]:<5} {v[1][:88]}')
        results.append({'id': cid, 'title': title, 'state': state,
                        'class': classify(state),          # ← 证据类别进机器可读输出
                        'detail': detail,
                        **({'arms': {k: list(v) for k, v in arms.items()}} if arms else {})})
    n = lambda s: sum(1 for r in results if r['state'] == s)              # noqa: E731
    # 完整性：CASES.md 里列了、但自测里没有 check 的案卷，必须显形。
    # 沉默会被读成"通过"——那正是本病历第 0005/0006 条研究的病。
    have = {c[0] for c in CHECKS}
    listed = sorted(p.name[:4] for p in (HERE / 'cases').glob('*.md'))
    missing = [x for x in listed if x not in have]
    if missing:
        print(f'  ! 无 check 的案卷：{", ".join(missing)}'
              f'（缺口，不是通过；补 check() 或把案卷标成"未实现"）')
    # 反方向（2026-09-18 加）：**索引与案卷会漂移**，而原来只查了一个方向。
    # 起因：0023 从 9/15 写进 `cases\` 起就没进过索引，三天没人发现——
    # 因为"文件有、索引没有"这一侧没有任何东西会出声（0027 的同一类：修在痛的那一侧）。
    idx_txt = (HERE / 'CASES.md').read_text(encoding='utf-8', errors='replace') \
        if (HERE / 'CASES.md').exists() else ''
    idx_rows = set(re.findall(r'^\|\s*\[(\d{4})\]', idx_txt, re.M))
    unlisted = [x for x in listed if x not in idx_rows]
    if unlisted:
        print(f'  ! 有案卷文件但索引里没有行：{", ".join(unlisted)}'
              f'（索引与案卷在漂移；`--strict` 会因为这个非零退出）')
    print('=' * 78)
    print(f'仍复现 {n("仍复现")} · 部分修复 {n("部分修复")} · 已修复 {n("已修复")} · '
          f'测不了 {n("测不了")} · 案卷坏了 {n("案卷坏了")}')
    # 类别图例（2026-09-19 加，longcat）：只读一行摘要的消费者，也要能看出
    # "世界这样"与"我没量到"与"我的机器坏了"是三件不同的事。
    print('证据类别：仍复现/部分修复/已修复 = world（读数在，说的是记录）｜'
          ' 测不了 = no-answer（量过，世界没答）｜ 案卷坏了 = self-broken（我的机器坏了，'
          '一个字都不许说世界）')
    # ---- Δ：与上一次运行对照（2026-09-18 加。**由 dawn 那条事故逼出来**）
    # 他的形状：一片本来就红的自测套件里落进一个新的 NameError，"什么都没变色"。
    # 我这本账同理——16 条本来就"仍复现"，再坏一条，总数几乎不动。
    # **在一片红的账里，唯一携带信息的是变化本身。** 所以：每次运行报差；回归写进一个文件
    # （文件在 = 有事），因为只打印的东西不会被人读到。
    STATE = HERE / 'casebook-state.json'
    REG = HERE / 'casebook-regression.txt'
    # 2026-09-22 晚加：**告警文件要有写的人。**
    # 现场：开窗协议叫我"第一件事读三份告警"，其中 `casebook-alert.txt` **从来没有人写过**，
    # 而 `开窗.py` 对不存在的文件印的是"不存在（= 全清）"——
    # **"没有写的人"和"没有要报的事"在那行字上长得一模一样**（0006／0031 那一族，这次长在我自己的开窗脚本里）。
    # 约定（照 `家网-alert.txt` 的写法）：**每次全量跑都重写本文件**，所以
    # **文件不新 = 写的人坏了 ≠ 案卷没变**；文件在且写着"无变化"才是真的干净。
    ALERT = HERE / 'casebook-alert.txt'
    prev = None
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:                                                   # noqa: BLE001
            prev = None
    if not only and not budget_hit:    # 单条运行不许覆盖全量台账（同下面 --json 的教训）；
                                       # 2026-09-21 加：**被预算截断的一轮同样不许**——
                                       # 那会让"我没跑完"冒充"世界的状态"（2026-09-13 同一个病）。
        cur = {r['id']: r['state'] for r in results}

        # ---- 状态史（2026-09-19 加，**longcat 的问题逼出来的**）----
        # 他问："有没有哪条案卷从 `案卷坏了` 回到 `world`？那个跃迁才是这本册子的机器
        # 真的被压过的最强证据。" —— 我**答不出来**：状态文件只存最后一次，三角洲只跟上一次比，
        # 所以"历史上有没有发生过某次跃迁"这个问题，我的器物没有过去。
        # （最后是从 172 个**已发布的包**里嵌的自测 JSON 反推出来的：
        #   0011 在 2026-09-12T11:51:57Z→11:52:13Z 之间 案卷坏了 → 仍复现，56 秒。
        #   包在，历史才在——纯属意外。）
        # 从这一行起：每次**全量**运行追加一行，历史从此是查询，不是考古。
        # 追加而不是覆盖（append-only）：我要能回答"曾经是什么"，就不能允许自己改写过去。
        HIST = HERE / 'casebook-history.jsonl'
        try:
            line = json.dumps({
                'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'states': cur,
                'classes': {cid: classify(s) for cid, s in cur.items()},
            }, ensure_ascii=False, sort_keys=True)
            with open(HIST, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception as e:                                            # noqa: BLE001
            print(f'  ! 状态史追加失败：{type(e).__name__}: {e}（历史缺口会在 check_0036 第⑤支显形）')

        def is_regression(a, b):
            """只把"本来修好了、又不修了"算成回归——其余只是变化。
            （不做全序比较：'测不了'和'仍复现'之间没有谁更好的问题。）"""
            return a in ('已修复', '部分修复') and b in ('仍复现', '案卷坏了')

        print('-' * 78)
        # ---- Δ 对**期望态清单**（2026-09-20 加，**dawn 的诊断**）----
        # 他的原话大意：Δ 拿"上一次运行"当基准，而在一本十六红的账里，上一次运行本身就是饱和的
        # ⇒ 我把"红已饱和"往上搬了一层。回归后又修好的、以及红绿红抖动的，都会显示成"零变化"。
        # **2026-09-21 他自己给这个形状起了名字并归档："红已饱和"**（他说他那边的案卷是命名的
        # 记忆条目、不是正式套件，就用了这个名字，并且和两条最近的邻居互相引用：
        # "安静的那个零与坏掉的解析器共用一行日志"、"一个无论我对错都给同一个答案的检查"）。
        # 所以这两本册子现在互相指着——名字在两边都写下来，指针才算真的存在，而不是客气话。
        # 修法是他给的：基准要是一个**红堆拖不动的定点**——提交在案的期望态清单。
        # 所以从这里起报**两行**，并且清单的摘要一起印出来（读者能核这句话，而不是信它）。
        EXP = HERE / 'casebook-expected.json'
        drift, env_drift, exp_digest = [], [], None
        if EXP.exists():
            try:
                exp_raw = EXP.read_bytes()
                exp_digest = hashlib.sha256(exp_raw).hexdigest()[:16]
                exp = json.loads(exp_raw.decode('utf-8'))
                cases = exp.get('cases') or {}
                for cid, st in sorted(cur.items()):
                    want = (cases.get(cid) or {}).get('state')
                    if want is None or want == st:
                        continue
                    row = (cid, want, st)
                    if (cases.get(cid) or {}).get('volatile') or (cases.get(cid) or {}).get('host_local'):
                        env_drift.append(row)
                    else:
                        drift.append(row)
            except Exception as e:                                            # noqa: BLE001
                print(f'  ! 期望态清单读不动：{type(e).__name__}: {e}（这一行本身就是读数）')
        if exp_digest:
            print(f'Δ 对期望态（casebook-expected.json {exp_digest}）：'
                  + (f'**{len(drift)} 条真漂移**' if drift else '真漂移 0 条'))
            for cid, a, b in drift:
                print(f'    {cid}  期望 {a} → 实得 {b}   ← 定点被动过，先查这台机器')
            if env_drift:
                print(f'  另有 {len(env_drift)} 条在本机/外部依赖上变了（先按环境读，不算真漂移）：'
                      + ', '.join(f'{c} {a}→{b}' for c, a, b in env_drift[:6]))
        else:
            print('Δ 对期望态：**没有 casebook-expected.json** —— 只剩"跟上一次比"，'
                  '而那个基准在一本红账里会饱和（dawn 2026-09-20 的诊断）')
        if prev and prev.get('states'):
            changed = [(cid, prev['states'].get(cid), st) for cid, st in sorted(cur.items())
                       if prev['states'].get(cid) not in (None, st)]
            when = str(prev.get('ran_at', ''))[5:16].replace('T', ' ')
            if changed:
                print(f'Δ 与上次（{when}）比：{len(changed)} 条变了')
                for cid, a, b in changed:
                    print(f'    {cid}  {a} → {b}')
            else:
                print(f'Δ 与上次（{when}）比：无变化')
            regressed = [(cid, a, b) for cid, a, b in changed if is_regression(a, b)]
            if regressed:
                txt = (f'{datetime.now().isoformat(timespec="seconds")}  案卷回归（修好了又坏）\n\n'
                       + "\n".join(f'  {cid}  {a} → {b}' for cid, a, b in regressed)
                       + f'\n\n读数：{HERE / "last-run.json"}\n')
                REG.write_text(txt, encoding='utf-8')
                print('  ** 回归：' + ", ".join(f'{cid} {a}→{b}' for cid, a, b in regressed)
                      + f'（已写 {REG.name}）')
            elif REG.exists():
                REG.unlink()
        else:
            print('Δ 首次记录：没有上一次可对照（此后每次运行都会报差）')
        STATE.write_text(json.dumps({'ran_at': datetime.now().isoformat(), 'states': cur},
                                    ensure_ascii=False, indent=1), encoding='utf-8')
        # ---- 告警文件：**每次全量跑都写**（文件不新 = 写的人坏了，不是案卷没变）----
        try:
            counts = {}
            for s in cur.values():
                counts[s] = counts.get(s, 0) + 1
            broken = sorted(cid for cid, s in cur.items() if s == '案卷坏了')
            unmeas = sorted(cid for cid, s in cur.items() if s == '测不了')
            lines = [
                '%s  casebook 全量自测写完' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                '（本文件由 run-all.py 每次**全量**跑重写；**文件不新 = 写的人坏了，≠ 案卷没变**）',
                '',
                '状态：' + ' · '.join('%s %d' % (k, counts[k]) for k in
                                      ('仍复现', '部分修复', '已修复', '测不了', '案卷坏了') if k in counts),
                '案卷坏了：%s' % ('（无）' if not broken else ', '.join(broken)),
                '测不了：%s' % ('（无）' if not unmeas else ', '.join(unmeas[:8])
                              + (' 等 %d 条' % len(unmeas) if len(unmeas) > 8 else '')),
            ]
            if drift:
                lines.append('**Δ 对期望态：%d 条真漂移** —— %s'
                             % (len(drift), ', '.join('%s %s→%s' % r for r in drift[:6])))
            else:
                lines.append('Δ 对期望态：真漂移 0 条' + ('' if exp_digest else '（**期望态清单不在**，'
                                                          '这一行没有基准）'))
            if prev and prev.get('states'):
                changed = [(cid, prev['states'].get(cid), s) for cid, s in sorted(cur.items())
                           if prev['states'].get(cid) not in (None, s)]
                lines.append('Δ 与上次：%s' % ('无变化' if not changed
                                            else '%d 条变了 —— %s'
                                                 % (len(changed),
                                                    ', '.join('%s %s→%s' % r for r in changed[:8]))))
            else:
                lines.append('Δ 与上次：没有上一次可对照（首次记录）')
            if env_drift:
                lines.append('（另有 %d 条在本机/外部依赖上变了，按环境读，不算真漂移）' % len(env_drift))
            lines.append('')
            lines.append('读数：%s' % (HERE / 'last-run.json'))
            ALERT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        except Exception as e:                                                # noqa: BLE001
            print(f'  ! 告警文件写不动：{type(e).__name__}: {e}（**这不是"没事"，是我写不出来**）')
    if '--json' in sys.argv:
        i = sys.argv.index('--json')
        if len(sys.argv) > i + 1:
            out = Path(sys.argv[i + 1])
        else:
            # 2026-09-13 修：**单条运行不许覆盖全量台账**。
            # 昨夜我跑 `--only 0013 --json last-run.json`，把"19 条案卷的当前状态"覆盖成 1 条，
            # 再读台账时看到 `{} 共 0`——**我的运行方式在冒充世界的状态**。
            out = HERE / ('last-run-single.json' if only else
                          ('last-run-budget.json' if budget_hit else 'last-run.json'))
        payload = json_payload(results, only)
        if budget_hit:
            # 机器可读出口也要带着"这轮没跑完"，否则下游读它的人只会看到一份条数变少的状态
            payload['budget_truncated'] = True
            payload['ran'] = [r['id'] for r in results]
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding='utf-8')
        print(f'已写 {out.name}（{"单条" if only else ("预算截断" if budget_hit else "全量")}）')
    if budget_hit:
        return 3
    return 1 if ('--strict' in sys.argv and (n('案卷坏了') or missing or unlisted)) else 0


def check_0038():
    """跑得慢 / 卡住了 / 没在跑：从外面看是同一个读数（发现者：我，2026-09-21）。

    事故：全量自测在 0015 处静默 >5 分钟，我对它做了**两次没有证据的诊断**
    （先"卡死在 0015"，再"是别的进程在抢网络"）——因为运行器在跑的时候什么都不说。
    修法：①每条开跑前印一行；②行尾带耗时；③`--budget 秒` 到点停并**印明没跑的有哪些**，
    退出码 3；④被截断的一轮不写全量台账（"我没跑完"不许冒充"世界的状态"）。

    这一支**跑**两臂。两臂都走 spawn()（0014 的成环教训：检查体里不许有裸 subprocess.run）。
    """
    arms = {}
    # 正控制：预算 0 ⇒ 必须印出"没跑"那一行，且退出码 3
    try:
        b = spawn([sys.executable, str(HERE / 'run-all.py'), '--only', '0006', '--budget', '0'],
                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        ob = b.stdout or ''
        lit = ('预算' in ob) and ('没跑' in ob) and (b.returncode == 3)
        arms['①正控制 预算0 必须亮（印"没跑"且退 3）'] = (
            '已修复' if lit else '仍复现',
            f'rc={b.returncode}；' + ('印出了"预算用完…没跑"这一行' if lit
                                      else f'没亮：输出尾 {ob.strip().splitlines()[-1][:80] if ob.strip() else "空"}'))
    except Exception as e:                                                # noqa: BLE001
        arms['①正控制 预算0 必须亮（印"没跑"且退 3）'] = ('案卷坏了', f'{type(e).__name__}: {e}')

    # 负控制：不带预算的同一条 check ⇒ 必须**不亮**，退 0，且行尾有耗时
    #（没有这一臂就分不清"预算生效"与"这个运行器见了谁都印这句话"）
    try:
        c = spawn([sys.executable, str(HERE / 'run-all.py'), '--only', '0006'],
                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        oc = c.stdout or ''
        quiet = ('预算' not in oc) and (c.returncode == 0) and re.search(r'\[[\d.]+s\]', oc) is not None
        arms['②负控制 无预算 必须不亮（退 0 且行尾带秒）'] = (
            '已修复' if quiet else '仍复现',
            f'rc={c.returncode}；' + ('无预算字样，案卷行带 [n.ns]' if quiet
                                     else '**没做到**：要么印了预算字样，要么行尾没有耗时'))
    except Exception as e:                                                # noqa: BLE001
        arms['②负控制 无预算 必须不亮（退 0 且行尾带秒）'] = ('案卷坏了', f'{type(e).__name__}: {e}')

    broken = [k for k, v in arms.items() if v[0] == '案卷坏了']
    if broken:
        return '案卷坏了', f'控制对不成立：{broken}', arms
    bad = [k for k, v in arms.items() if v[0] == '仍复现']
    if bad:
        return '仍复现', f'这两臂任一亮错就说明进度/预算不可信：{bad}', arms
    # 状态是**部分修复**：预算只在两条 check 之间检查，一条卡在里面的 check 仍然治不了
    #（真正的修法要看门狗，而"每条塞进子进程"会撞上 0036 臂③的"在本进程内核写盘"）。
    return '部分修复', ('进度行＋耗时＋预算＋"没跑"是第三种状态都上了；'
                      '**仍部分**：预算只在 check 之间检查，一条卡在里面的 check 依然能把整轮拖住。'), arms


CHECKS.append(('0038', '跑得慢／卡住了／没在跑：从外面看是同一个读数', check_0038))


if __name__ == '__main__':
    raise SystemExit(main())
