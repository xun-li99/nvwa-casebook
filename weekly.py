#!/usr/bin/env python3
"""weekly.py —— 每周复查（非 LLM，计划任务跑）。让自测不必依赖我醒着。

做三件事：
  1. 跑 run-all.py --json，把结果追加进 history.csv（每行：时间 + 各状态计数 + 逐条状态）；
  2. 与**上一次**比较，只报**变化**：某条从"仍复现"变"已修复"（世界变了）、
     变成"测不了"（语料不在了）、或"案卷坏了"（脚本坏了）——三件事分开写；
  3. 有变化就写 casebook-alert.txt（与入站监视同一套逻辑：**不读它，警报就只是打印**）。

用法：python weekly.py [--quiet]
"""
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 2026-09-20 加：出口编码必须自己钉住（默认代码页 GBK 时，打印非 ASCII 会直接死在这一行）。
# 同时挡住 pythonw（计划任务）下 sys.stdout 为 None 的情况。类级闸门见 check_0014 臂⑥。
for _s in (sys.stdout, sys.stderr):
    if _s is not None:
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:                                             # noqa: BLE001
            pass

HERE = Path(__file__).parent
HIST = HERE / 'history.csv'
ALERT = HERE / 'casebook-alert.txt'
STATE = HERE / 'last-run.json'


def bj():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


def main():
    quiet = '--quiet' in sys.argv
    prev = json.loads(STATE.read_text(encoding='utf-8'))['results'] if STATE.exists() else []
    r = subprocess.run([sys.executable, str(HERE / 'run-all.py'), '--json', str(STATE)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if not STATE.exists():
        print('⚠ 自测没产出结果文件——这不是"世界变了"，是**检查没跑成**。')
        return 1
    cur = json.loads(STATE.read_text(encoding='utf-8'))['results']
    old = {x['id']: x['state'] for x in prev}
    changes = [(x['id'], old.get(x['id'], '(首次)'), x['state'], x['detail'])
               for x in cur if old.get(x['id']) != x['state']]

    new = not HIST.exists()
    with open(HIST, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['ts_bj', 'states', 'details'])
        w.writerow([bj(), json.dumps({x['id']: x['state'] for x in cur}, ensure_ascii=False),
                    json.dumps({x['id']: x['detail'][:120] for x in cur}, ensure_ascii=False)])

    if changes:
        lines = [f'{bj()}  仪器病历：{len(changes)} 条状态变化', '']
        for cid, a, b, detail in changes:
            tag = {'已修复': '世界变了', '测不了': '语料不在了（世界没变）',
                   '案卷坏了': '脚本坏了（与原案卷无关）'}.get(b, '')
            lines += [f'  {cid}: {a} → {b}   {tag}', f'      {detail}']
        lines += ['', '按三种原因分别处理：世界变了 → 更新案卷状态与死期；语料不在了 → 恢复语料；'
                      '脚本坏了 → 修 check()，别改案卷。']
        ALERT.write_text('\n'.join(lines), encoding='utf-8')
        if not quiet:
            print('\n'.join(lines))
    else:
        if ALERT.exists():
            ALERT.unlink()
        if not quiet:
            print(f'[{bj()}] 仪器病历：{len(cur)} 条案卷，状态无变化。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
