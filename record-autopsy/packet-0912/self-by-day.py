#!/usr/bin/env python3
"""self-by-day.py —— 把"我说过的话"按天切开，看 v8 硬约束②（不是…是… ≤1 次）写下去之后有没有变。

为什么（2026-09-12，浔："全面自我审视"）：技能里写的判据是**活过下一次**，不是被确认。
所以不看"我认了没有"，看**同一条约束前后的数**。基线：源-信号.md 里 47%（非空条目）。
口径两套都报：工具自带的（要求 是 前面有 ，。；）与放宽到破折号的。
"""
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

rows = [json.loads(l) for l in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines() if l.strip()]
NARROW = re.compile(r'不是[^。！？\n]{0,40}?[，。；]\s*是')
WIDE = re.compile(r'不是[^。！？\n]{0,40}?[，。；—\-]{1,2}\s*是')


def day_of(ts):
    s = str(ts or '')
    if s.replace('.', '').isdigit():                       # epoch（秒或毫秒都吃）
        v = float(s)
        if v > 1e11:
            v /= 1000.0
        return (datetime.fromtimestamp(v, timezone.utc) + timedelta(hours=8)).strftime('%m-%d')
    return s[:10] or 'unknown'

by_day = defaultdict(lambda: {'n': 0, 'narrow': 0, 'wide': 0, 'chars': 0})
for r in rows:
    day = day_of(r.get('ts'))
    t = r.get('content') or ''
    d = by_day[day]
    d['n'] += 1
    d['chars'] += len(t)
    d['narrow'] += len(NARROW.findall(t))
    d['wide'] += len(WIDE.findall(t))

print(f'消息总数 {len(rows)}；按天（会话内的事件时间）：')
print(f"{'日期':<12}{'我的消息':>8}{'字数':>10}{'工具口径':>10}{'放宽口径':>10}{'每千字':>9}")
for day in sorted(by_day):
    d = by_day[day]
    per_k = d['wide'] / d['chars'] * 1000 if d['chars'] else 0
    print(f"{day:<12}{d['n']:>8}{d['chars']:>10}{d['narrow']:>10}{d['wide']:>10}{per_k:>9.2f}")
tot_k = sum(d['chars'] for d in by_day.values())
print(f"\n全程：{sum(d['wide'] for d in by_day.values())} 次 / {tot_k} 字 = "
      f"{sum(d['wide'] for d in by_day.values())/tot_k*1000:.2f} 次每千字")
print('基线（源-信号.md，2026-09-02 前）：47% 的非空条目含该句式（条目口径，非千字口径）')
