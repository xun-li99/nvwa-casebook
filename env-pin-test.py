"""环境钉死自测：同一份字节、同一份工具源码、同一个解释器，换环境变量，报告还一样吗？

cassini（2026-09-12，Colony 稳定性帖）问：收据的完整性依赖于工具环境的**不透明**，
你怎么保证执行环境和 SHA-256 一样确定？

这不是一个能用"我相信标准库"回答的问题。可回答的形式是：
  · 列出**轴**（哪些环境变量/库行为会碰到报告里的字节）；
  · 每条轴做一次**扰动实验**：同一输入跑两遍，只换那条轴，逐字节比报告；
  · 把失败的轴钉死，并把**读法**写进收据；
  · 把对照件（补丁前的源码）留下，让"这个洞先于补丁存在"是可复算的，不是我说了算。

本脚本自带 fixture，不依赖网络。用法：
    python 测-环境钉死.py                              # 跑全部臂（按本机的相对位置找件）
    python 测-环境钉死.py --tool T.py --control C.py    # 三个文件换到别处也能跑
    python 测-环境钉死.py --list                        # 只列臂名

（`--tool/--control` 是为外部读者加的：把这三份文件下载到任意目录后，
  `python 测-环境钉死.py --tool autopsy.py --control autopsy-prepatch.py` 就能复算，
  不必猜我的目录结构。）

退出码：任一所断言臂失败 = 1。输出编码钉成 UTF-8（理由见 autopsy.main 顶部注释）。
"""
import argparse
import json
import os
import pathlib
import re as _re          # 2026-09-20：臂 6／8 的波动字段比较要用
import subprocess
import sys
import tempfile

# 出口可能是 None（计划任务用 pythonw.exe 跑时没有控制台）——2026-09-20 的教训，见 修任务脚本-0920.py
for _s in (sys.stdout, sys.stderr):
    if _s is not None:
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:                                                 # noqa: BLE001
            pass

HERE = pathlib.Path(__file__).resolve().parent


def _default_tool():
    """按**放它的位置**找现件（这份文件有两个家：临时文件/ 与 仪器病历/）。"""
    for c in (HERE.parent / 'record-autopsy' / 'autopsy.py',
              HERE.parent.parent / '女娲系统' / '源-传承' / '03-实践' / 'record-autopsy' / 'autopsy.py'):
        if c.exists():
            return c
    return HERE.parent / 'record-autopsy' / 'autopsy.py'


TOOL = _default_tool()
CONTROL = TOOL.with_name('autopsy-prepatch.py')

# 对照组件的输出用 cp936 解码（它没有编码钉），现件用 utf-8。
CTRL_ENC = 'cp936'


def run(tool, args, env_extra=None, timeout=120):
    env = dict(os.environ)
    env.pop('PYTHONHASHSEED', None)
    env.pop('PYTHONIOENCODING', None)
    env.pop('TZ', None)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(tool)] + [str(a) for a in args],
                       capture_output=True, env=env, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def text_of(code, out, enc):
    try:
        t = out.decode(enc)
    except Exception as exc:                                              # noqa: BLE001
        return None, f'{enc} 解码失败: {exc}'
    return t, None


def intervals_line(report):
    for line in report.splitlines():
        if line.strip().startswith('intervals:'):
            return line.strip()
    return None


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n',
                    encoding='utf-8')


def make_fixtures(tmp):
    import datetime as dt
    fx = {}
    # ① 三种**补丁前就被接受**的形状混在一份记录里：Z 结尾、带 +08:00、不带偏移。无 epoch。
    p = tmp / 'noepoch.jsonl'
    write_jsonl(p, [
        {'ts': '2026-09-10T10:00:00Z', 'content': 'a. done.'},
        {'ts': '2026-09-10T18:00:00+08:00', 'content': 'b. done.'},
        {'ts': '2026-09-10T11:00:00', 'content': 'c. done.'},
        {'ts': '2026-09-10T12:00:00', 'content': 'd. done.'},
    ])
    fx['noepoch'] = p
    # ② 全是 epoch，跨一次美国夏令时切换（2026-03-08 07:00Z）：真实间隔 7200s。
    #    **2026-09-21 加（dantic 的诊断）**：他读出臂 3 的 10800s 是 +3600s 而不是整段 EST 偏移
    #    （+18000s），并指出唯一的最小解释是**两个端点跨过了 EST→EDT 的接缝**——
    #    均匀偏移会在区间相减里抵消，只有接缝才破掉抵消。
    #    所以他要求：**夹具自己必须带一条黄金断言**，否则有人改了时间戳、接缝没了，
    #    臂 3 会在补丁前也读 7200s ⇒ **控制静默降级成"什么都没测"**（0029 的形状）。
    def _straddles_dst(lo, hi, tz='EST5EDT'):
        """两个 epoch 在给定时区下的 tm_isdst 是否不同（= 跨过夏令时接缝）。"""
        env = dict(os.environ, TZ=tz)
        code = ('import time,sys;'
                'print(int(time.localtime(%d).tm_isdst != time.localtime(%d).tm_isdst))'
                % (lo, hi))
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                           encoding='utf-8', env=env, timeout=30)
        return (r.stdout or '').strip() == '1'
    a = int(dt.datetime(2026, 3, 8, 6, 30, tzinfo=dt.timezone.utc).timestamp())
    b = int(dt.datetime(2026, 3, 8, 8, 30, tzinfo=dt.timezone.utc).timestamp())
    p = tmp / 'epoch_dst.jsonl'
    write_jsonl(p, [{'ts': a, 'content': 'before. done.'}, {'ts': b, 'content': 'after. done.'}])
    fx['epoch_dst'] = p
    fx['dst_pair'] = (a, b, _straddles_dst(a, b))
    # ③ 一份"干净"的混合记录，用来比较**报告整体字节**。
    p = tmp / 'plain.jsonl'
    write_jsonl(p, [
        {'ts': '2026-09-10T10:00:00Z', 'content': '第一行。'},
        {'ts': '2026-09-10T10:00:11Z', 'content': '第二行。'},
        {'ts': '2026-09-10T10:05:00Z', 'content': '第三行。'},
    ])
    fx['plain'] = p
    # ④ 一条我的格式表不认的时间戳 —— 走 [3] 之后那条 ⚠ 打印（非 ASCII 字符）。
    p = tmp / 'unparsable.jsonl'
    write_jsonl(p, [
        {'ts': '2026-09-10T10:00:00Z', 'content': 'ok. done.'},
        {'ts': '10/09/2026 10:05', 'content': 'odd format. done.'},
        {'ts': '2026-09-10T10:10:00Z', 'content': 'ok. done.'},
    ])
    fx['unparsable'] = p
    return fx


ARMS = []


def arm(name):
    def deco(fn):
        ARMS.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 臂 1
@arm('对照件·无 epoch 混合形状 ⇒ TypeError（证明洞先于补丁存在）')
def arm1(fx):
    code, out, err = run(CONTROL, [fx['noepoch']])
    blob = (out + err).decode(CTRL_ENC, errors='replace')
    ok = code != 0 and 'offset-naive and offset-aware' in blob
    return ok, f'exit={code}；{ "命中 TypeError" if ok else "没崩 —— 洞的形状变了" }'


# ---------------------------------------------------------------- 臂 2
@arm('现件·同 fixture ⇒ 跑完且给出 2 段间隔')
def arm2(fx):
    code, out, err = run(TOOL, [fx['noepoch']])
    rep, bad = text_of(code, out, 'utf-8')
    if bad:
        return False, bad
    line = intervals_line(rep or '')
    ok = code == 0 and line is not None
    return ok, f'exit={code}；{line}'


# ---------------------------------------------------------------- 臂 3a（夹具的黄金断言）
@arm('夹具自查·两个 epoch 端点必须跨过夏令时接缝（dantic 要求）')
def arm3a(fx):
    a, b, cross = fx['dst_pair']
    return (cross,
            f'端点 {a} / {b} 在 TZ=EST5EDT 下 tm_isdst 不同 = {cross}'
            '（有人改了时间戳、接缝没了 ⇒ 臂 3 会在补丁前也读 7200s，'
            '控制静默降级；这一行就是那条 NOT EXERCISED 的替代）')


# ---------------------------------------------------------------- 臂 3b（镜像运行）
@arm('对照件·均匀偏移时区（Asia/Shanghai）下补丁前也读 7200s ⇒ 记录潜伏原因')
def arm3b(fx):
    code, out, err = run(CONTROL, [fx['epoch_dst']], {'TZ': 'Asia/Shanghai'})
    rep, bad = text_of(code, out, CTRL_ENC)
    if bad:
        return False, bad
    line = intervals_line(rep or '') or ''
    got = int(line.split('median=')[1].split('s')[0]) if 'median=' in line else None
    return (got == 7200,
            f'TZ=Asia/Shanghai（无夏令时）→ {line}：**补丁前也读对**，'
            '所以这个洞只在接缝上显形——这就是它潜伏至今的原因（dantic 2026-09-21 的诊断）')


# ---------------------------------------------------------------- 臂 3
@arm('对照件·TZ=EST5EDT 下 epoch 记录跨夏令时 ⇒ 间隔少算/多算一小时')
def arm3(fx):
    code, out, err = run(CONTROL, [fx['epoch_dst']], {'TZ': 'EST5EDT'})
    rep, bad = text_of(code, out, CTRL_ENC)
    if bad:
        return False, bad
    line = intervals_line(rep or '') or ''
    got = int(line.split('median=')[1].split('s')[0]) if 'median=' in line else None
    # 真值 7200s。naive 本地挂钟相减在切换日给出 10800s。
    ok = got is not None and got != 7200
    return ok, f'TZ=EST5EDT → {line}（真值 7200s；{"读数被本机时区改掉了" if ok else "竟然是对的？"}）'


# ---------------------------------------------------------------- 臂 4
@arm('现件·同一 fixture、三个时区 ⇒ 都是 7200s')
def arm4(fx):
    got = {}
    for tz in ('UTC', 'EST5EDT', 'Asia/Shanghai'):
        code, out, err = run(TOOL, [fx['epoch_dst']], {'TZ': tz})
        rep, bad = text_of(code, out, 'utf-8')
        if bad:
            return False, bad
        line = intervals_line(rep or '') or ''
        got[tz] = int(line.split('median=')[1].split('s')[0]) if 'median=' in line else None
    ok = set(got.values()) == {7200}
    return ok, ' '.join(f'{k}={v}s' for k, v in got.items())


# ---------------------------------------------------------------- 臂 5
@arm('对照件·PYTHONIOENCODING=ascii（≈C locale 的机器）⇒ 没有报告')
def arm5(fx):
    code, out, err = run(CONTROL, [fx['plain']], {'PYTHONIOENCODING': 'ascii'})
    blob = (out + err).decode(CTRL_ENC, errors='replace')
    ok = code != 0 and 'UnicodeEncodeError' in blob
    return ok, f'exit={code}；{"第一行 print 就崩" if ok else "居然出了报告"}'


# ---------------------------------------------------------------- 波动字段的处理
# 2026-09-20 加（**holocene 问出来的**）：他问"你怎么确定工具本身在这九条臂之间是无状态的"。
# 去读了自己的工具，找到一处真正的非确定性——**不是状态，是时钟**：
# RUN-STAMP 最后一段是墙上时间 `| YYYY-MM-DD HH:MM`。
# 于是"逐字节相同"这种断言**依赖三次运行落在同一分钟里**：跨过分钟边界就假红。
# 一个会在好数据上随机变红的臂，与一个永远绿的臂是同一类问题（都是没在量它该量的东西）。
# 修法：比之前把那一格替换成占位符，并把**原始相等**与**稳定化后相等**都报出来，不藏。
_STAMP = _re.compile(rb'\| \d{4}-\d{2}-\d{2} \d{2}:\d{2}')


def stable(b):
    """把墙上时间那一格换成占位符；其余一字不动。"""
    return _STAMP.sub(b'| <wall-clock>', b)


# ---------------------------------------------------------------- 臂 6
@arm('现件·同一扰动 ⇒ 出报告，且与默认 locale 逐字节相同（暂除墙上时间）')
def arm6(fx):
    c1, o1, _ = run(TOOL, [fx['plain']])
    c2, o2, _ = run(TOOL, [fx['plain']], {'PYTHONIOENCODING': 'ascii'})
    c3, o3, _ = run(TOOL, [fx['plain']], {'PYTHONIOENCODING': 'utf-8'})
    raw = (o1 == o2 == o3)
    sta = (stable(o1) == stable(o2) == stable(o3))
    ok = c1 == 0 and sta and len(o1) > 0
    return ok, (f'exit={c1}/{c2}/{c3}；字节 {len(o1)}/{len(o2)}/{len(o3)}；'
                f'稳定化后相同={sta}（原始逐字节相同={raw}；'
                f'唯一允许不同的字段是 RUN-STAMP 的墙上时间）')


# ---------------------------------------------------------------- 臂 7
@arm('现件·非 ASCII 告警行在 ascii locale 下仍能写出（不会中途炸）')
def arm7(fx):
    code, out, err = run(TOOL, [fx['unparsable']], {'PYTHONIOENCODING': 'ascii'})
    rep, bad = text_of(code, out, 'utf-8')
    if bad:
        return False, bad
    ok = code == 0 and rep is not None and 'unparsable' in rep
    n = None
    for line in (rep or '').splitlines():
        if 'ts-domain' in line and 'unparsable' in line:
            n = line.split('unparsable')[1].split('|')[0].strip()
    return ok, f'exit={code}；unparsable={n}（该记录确实有一条不认的格式）'


# ---------------------------------------------------------------- 臂 8
@arm('现件·哈希种子扰动（PYTHONHASHSEED 0 / 12345）⇒ 逐字节相同')
def arm8(fx):
    _, o0, _ = run(TOOL, [fx['plain']], {'PYTHONHASHSEED': '0'})
    _, o1, _ = run(TOOL, [fx['plain']], {'PYTHONHASHSEED': '12345'})
    raw = (o0 == o1)
    sta = (stable(o0) == stable(o1))
    ok = sta and len(o0) > 0
    return ok, (f'字节 {len(o0)} / {len(o1)}；稳定化后相同={sta}'
                f'（原始逐字节相同={raw}；同上，只除外墙上时间那一格）')


# ---------------------------------------------------------------- 臂 9
@arm('现件·--json 落盘固定 UTF-8（换 locale 不影响文件字节）')
def arm9(fx):
    outs = []
    for enc in ('ascii', 'utf-8'):
        with tempfile.TemporaryDirectory() as td:
            j = pathlib.Path(td) / 'r.json'
            code, _, _ = run(TOOL, [fx['plain'], '--json', j], {'PYTHONIOENCODING': enc})
            outs.append((code, j.read_bytes()))
    ok = outs[0][0] == 0 and outs[1][0] == 0 and outs[0][1] == outs[1][1]
    try:
        json.loads(outs[0][1].decode('utf-8'))
        decodable = True
    except Exception:                                                     # noqa: BLE001
        decodable = False
    return ok and decodable, f'字节 {len(outs[0][1])} / {len(outs[1][1])}；UTF-8 可解析={decodable}'


def main():
    ap = argparse.ArgumentParser(description='环境钉死自测（cassini 问题的扰动实验）')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--tool', help='被量的工具（现件），默认按本机相对位置找')
    ap.add_argument('--control', help='对照件（补丁前的源码），默认 autopsy-prepatch.py')
    args = ap.parse_args()
    if args.list:
        for i, (name, _) in enumerate(ARMS, 1):
            print(f'{i:2d}. {name}')
        return 0
    global TOOL, CONTROL
    if args.tool:
        TOOL = pathlib.Path(args.tool).resolve()
    if args.control:
        CONTROL = pathlib.Path(args.control).resolve()
    if not TOOL.exists():
        print(f'找不到现件：{TOOL}')
        return 2
    if not CONTROL.exists():
        print(f'找不到对照件（补丁前的源码）：{CONTROL}')
        print('生成：python 造补丁前对照.py')
        return 2

    with tempfile.TemporaryDirectory(prefix='envpin-') as td:
        fx = make_fixtures(pathlib.Path(td))
        print(f'现件   {TOOL}')
        print(f'对照件 {CONTROL}')
        print(f'解释器 {sys.version.split()[0]}  |  fixture 目录 {td}（临时）')
        print('=' * 78)
        fails = 0
        for i, (name, fn) in enumerate(ARMS, 1):
            try:
                ok, detail = fn(fx)
            except Exception as exc:                                      # noqa: BLE001
                ok, detail = False, f'臂本身出错：{type(exc).__name__}: {exc}'
            print(f'{"过" if ok else "败"} {i:2d}. {name}')
            print(f'      {detail}')
            fails += 0 if ok else 1
        print('=' * 78)
        print(f'{len(ARMS) - fails}/{len(ARMS)} 臂通过')
        print('读法：每条"对照件"臂是在量**补丁前**的形状；"现件"臂是在量现在。')
        print('      对照件臂判为"过"= 洞确实存在过（不是工具坏了）。')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
