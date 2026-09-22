#!/usr/bin/env python3
"""sensitivity.py —— 给自测本身做控制对（longcat 的问题，2026-09-12）。

他的问题原话：
> when run-all.py reports "the check itself is broken," how do you distinguish that
> from "the world changed in a way the check cannot detect"? Both produce the same output.
> Is there a control pair for the self-test itself?

**没有那个控制对，五态分类就只是五张贴纸。** 本脚本是最便宜的那个控制对：
**扰动世界，看 check 会不会改口。** 不动任何 check 的代码，只用已经存在的环境变量钩子：

  ① 把语料指到不存在的地方 → 依赖语料的 check 必须报 **测不了**，**不许报"已修复"**
     （"已修复"是对世界的断言；语料不在时它没有资格下这个断言）
  ② 把库指到不存在的地方 → 同上
  ③ 有臂的案卷：**至少出现过两种不同的臂状态**（证明它能给出不止一个判决）
  ④ 故意给一个不存在的案卷号 → 自测必须正常退出，不能被"不认识的输入"搞崩

用法：python sensitivity.py [--json out.json]
退出码：0 = 全部符合；1 = 有 check 通不过灵敏度测试
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# 2026-09-20 修（**longcat 问"这个工具自己失败时能不能被它自己发现"**，我跑了一下）：
#   它**死在自己的输出上**：`UnicodeEncodeError: 'gbk' codec can't encode '\u2713'`——
#   打印那排勾选符号时，本机默认代码页是 GBK。
#   也就是说：这个被称赞为"最干净的控制对"的工具，**在能报出任何读数之前就没了**。
#   而同一个毛病今天已经在 autopsy.py、家网/5030 两个监视器上各修过一次——第三次了，
#   所以这次不只修这里，也把"出口编码"当成一条**检查**（见 check_0014 的臂⑥）。
# 顺带把 pythonw（计划任务）下 sys.stdout 为 None 的情况一起挡住。
for _s in (sys.stdout, sys.stderr):
    if _s is not None:
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:                                                 # noqa: BLE001
            pass

HERE = Path(__file__).parent
RUNNER = HERE / 'run-all.py'
STATE = HERE / 'last-run.json'
FAKE = 'C:\\__no_such_path__'


def run(cid, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    env['PYTHONIOENCODING'] = 'utf-8'
    r = subprocess.run([sys.executable, str(RUNNER), '--only', cid, '--json', str(STATE)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace',
                       env=env, timeout=180)
    try:
        res = json.loads(STATE.read_text(encoding='utf-8'))['results']
        return (res[0] if res else {'state': '?', 'detail': ''}), (r.stdout or '')
    except Exception as e:                                                # noqa: BLE001
        return {'state': f'解析失败 {type(e).__name__}', 'detail': ''}, (r.stdout or '')


def main():
    rows = []
    # 夹具放到**扫描树之外**（系统临时目录）。
    # 第一版把夹具写在病历目录里，于是"干净的世界"那一轮也扫到了自己的夹具，
    # 报出"仍有 2 处"——测试的脚手架被读成了被测对象。
    import tempfile
    SIM = Path(tempfile.mkdtemp(prefix='casebook-sim-'))

    # ============ 逐案变异（强信号）：给 check 喂一个合成世界，要求它改口 ============
    # 这一节是 longcat 那个问题的正面回答：不只问"它给过几种判决"，而是**换掉世界再问一次**。
    # ---- 0013：放一把真刀到一个临时目录，check 必须亮 ----
    knife = SIM / 'scan-with-knife'
    knife.mkdir(exist_ok=True)
    (knife / 'bad_script.py').write_text('import subprocess\n'
                                         'subprocess.run(["taskkill", "/IM", "python.exe"])\n',
                                         encoding='utf-8')
    clean = SIM / 'scan-clean'
    clean.mkdir(exist_ok=True)
    (clean / 'ok_script.py').write_text('print("hello")\n', encoding='utf-8')
    for label, root, expect in (('有刀的世界', str(knife), '仍复现'),
                                ('干净的世界', str(clean), '已修复')):
        got, _ = run('0013', {'CASEBOOK_SCAN_ROOTS': root})
        rows.append({'id': '0013', 'perturbation': f'变异：{label}',
                     'expected': expect, 'got': got['state'],
                     'ok': got['state'] == expect, 'detail': got.get('detail', '')[:100]})

    # ---- 0009：把 autopsy.py 变异成"印版本但不带 py 段"，check 必须改口 ----
    src = (HERE.parent / 'record-autopsy' / 'autopsy.py').read_text(encoding='utf-8')
    mutated = SIM / 'autopsy-no-py.py'
    mutated.write_text(src.replace(" | py{_py}", ""), encoding='utf-8')
    got, _ = run('0009', {'CASEBOOK_AUTOPSY': str(mutated)})
    rows.append({'id': '0009', 'perturbation': '变异：stamp 去掉 py 段',
                 'expected': '仍复现', 'got': got['state'],
                 'ok': got['state'] == '仍复现', 'detail': got.get('detail', '')[:100]})

    # ---- 0004：换样本，形状不在场时必须报"已修复" ----
    got, _ = run('0004', {'CASEBOOK_0004_SAMPLE': '不是甲，是乙。这不是问题。'})
    rows.append({'id': '0004', 'perturbation': '变异：样本无破折号式',
                 'expected': '已修复', 'got': got['state'],
                 'ok': got['state'] == '已修复', 'detail': got.get('detail', '')[:100]})

    # ---- 0001：合成一份"时间戳相隔很远"的输入 → 没有细网格相邻 → 必须改口 ----
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    synth = SIM / 'sparse-timestamps.jsonl'
    synth.write_text('\n'.join(
        json.dumps({'ts': (t0 + timedelta(hours=i)).isoformat(), 'content': f'entry {i}'})
        for i in range(20)) + '\n', encoding='utf-8')
    got, _ = run('0001', {'CASEBOOK_0001_INPUT': str(synth)})
    rows.append({'id': '0001', 'perturbation': '变异：时间戳相隔 1 小时',
                 'expected': '已修复', 'got': got['state'],
                 'ok': got['state'] == '已修复', 'detail': got.get('detail', '')[:100]})

    # ---- 0005：合成一份"每一行都带原因"的语料 → 形状不在场 → 必须改口 ----
    allcause = SIM / 'corpus-all-causes.md'
    allcause.write_text('\n'.join(
        f'2026-08-1{i%9}T0{i%9}:00:00Z bridge=ok service=ok tunnel=down(TIMEOUT)'
        for i in range(10)) + '\n', encoding='utf-8')
    got, _ = run('0005', {'CASEBOOK_0005_CORPUS': str(allcause)})
    rows.append({'id': '0005', 'perturbation': '变异：语料每行都带原因',
                 'expected': '已修复', 'got': got['state'],
                 'ok': got['state'] == '已修复', 'detail': got.get('detail', '')[:100]})

    # ---- 0008：把平台默认编码变成 UTF-8（PYTHONUTF8=1）→ 形状在这台机器上不可能发生
    #      → 必须报"测不了（不适用）"，**不许报"已修复"**（世界没变，只是这环境不踩它）----
    got, _ = run('0008', {'PYTHONUTF8': '1'})
    rows.append({'id': '0008', 'perturbation': '变异：默认编码改为 UTF-8',
                 'expected': '测不了', 'got': got['state'],
                 'ok': got['state'] == '测不了', 'detail': got.get('detail', '')[:100]})

    # ============ 结构上不可变异的案卷（写清理由，别把它们混进"未测"）============
    LOCKED = {
        '0003': '世界 = 外部主机的 www 行为，我不能变异别人的服务器',
        '0007': '世界 = Python 的语法语义，我不能变异语言',
        '0012': '世界 = 外部主机对两条路径的状态码',
        '0016': '世界 = 公开语料的 manifest 集合（只读，不能改）',
    }

    # ============ 先跑一次全量，**在扰动之前**把臂抓下来 ============
    # 扰动会用 --only 覆盖 last-run.json，第一版就是在扰动之后才读臂，
    # 于是"臂跨度"那一节永远读到单条案卷（自己的时序 bug）。
    full_env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    subprocess.run([sys.executable, str(RUNNER), '--json', str(STATE)],
                   capture_output=True, text=True, encoding='utf-8', errors='replace',
                   env=full_env, timeout=600)
    full = json.loads(STATE.read_text(encoding='utf-8'))['results'] if STATE.exists() else []
    with_arms = [r for r in full if r.get('arms')]
    no_arms = [r['id'] for r in full if not r.get('arms')]

    # ①② 语料/库缺失 → 必须是"测不了"，不许是"已修复"
    for cid, env, label in (
        ('0005', {'CASEBOOK_0005_CORPUS': FAKE}, '语料指到不存在处'),
        ('0002', {'CASEBOOK_STORE': FAKE}, '网络库指到不存在处'),
    ):
        got, out = run(cid, env)
        ok = got['state'] == '测不了'
        rows.append({'id': cid, 'perturbation': label, 'expected': '测不了',
                     'got': got['state'], 'ok': ok,
                     'detail': got.get('detail', '')[:110]})

    # ④ 不存在的案卷号 → 正常退出、不崩
    got, out = run('9999')
    ok = 'Traceback' not in out
    rows.append({'id': '9999', 'perturbation': '不存在的案卷号', 'expected': '不崩',
                 'got': '未崩' if ok else '抛异常', 'ok': ok, 'detail': ''})

    # ③ 有臂的案卷：臂状态必须跨度 > 1
    for r in with_arms:
        states = {v[0] for v in r['arms'].values()}
        ok = len(states) > 1
        rows.append({'id': r['id'], 'perturbation': '臂状态跨度', 'expected': '≥2 种',
                     'got': f'{len(states)} 种：{sorted(states)}', 'ok': ok, 'detail': ''})

    print('=' * 78)
    print('自测的灵敏度（控制对）· 扰动世界，看 check 会不会改口')
    print('=' * 78)
    bad = 0
    for r in rows:
        mark = '✓' if r['ok'] else '×'
        bad += (not r['ok'])
        print(f"  {mark} {r['id']:5s} {r['perturbation']:22s} 期望 {r['expected']:8s} "
              f"实得 {r['got']}")
        if r['detail']:
            print(f"         {r['detail']}")
    print('-' * 78)
    # "未测"的定义要准：有臂的、或本轮吃过变异行的，都算有过控制对。
    covered = {r['id'] for r in with_arms} | {r['id'] for r in rows if r['perturbation'].startswith('变异')}
    untested = [r['id'] for r in full if r['id'] not in covered and r['id'] not in LOCKED]
    locked = [r['id'] for r in full if r['id'] in LOCKED]
    print(f"通过 {len(rows)-bad}/{len(rows)}")
    print(f"**结构上不可变异**（写清理由，不算未测）：")
    for cid in locked:
        print(f"    {cid}：{LOCKED[cid]}")
    print(f"**既没有臂、也没吃过变异行**的案卷：{', '.join(untested) if untested else '无'}")
    print('注意：未测 ≠ 通过。它们只是还没有控制对。')
    if '--json' in sys.argv:
        i = sys.argv.index('--json')
        out = Path(sys.argv[i + 1]) if len(sys.argv) > i + 1 else HERE / 'sensitivity.json'
        out.write_text(json.dumps({'rows': rows, 'arms_untested': no_arms},
                                  ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'已写 {out}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
