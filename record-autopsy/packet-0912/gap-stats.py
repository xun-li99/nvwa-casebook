#!/usr/bin/env python3
"""gap-stats.py —— 毫秒级间隔分布（autopsy 的 cadence 只到秒，看不出我们真实的落盘节奏）。"""
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

rows = [json.loads(l) for l in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines() if l.strip()]


def t(s):
    return datetime.fromisoformat(str(s).replace('Z', '+00:00'))


ts = sorted(t(r['ts']) for r in rows)
gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
print(f'条目 {len(ts)}，不同时间戳 {len({x.isoformat() for x in ts})}')
print(f'间隔（秒）：中位 {statistics.median(gaps):.3f}  最小 {min(gaps):.3f}  最大 {max(gaps):.0f}')
for thr, label in ((1, '< 1 秒'), (60, '< 60 秒'), (900, '< 15 分钟'), (3600, '< 1 小时')):
    n = sum(1 for g in gaps if g < thr)
    print(f'  {label}: {n}/{len(gaps)} = {n/len(gaps)*100:.1f}%')
buckets = {}
for g in gaps:
    k = ('0–1 秒' if g < 1 else '1–60 秒' if g < 60 else '1–15 分' if g < 900
         else '15 分–1 时' if g < 3600 else '> 1 小时')
    buckets[k] = buckets.get(k, 0) + 1
print('分桶:', buckets)
