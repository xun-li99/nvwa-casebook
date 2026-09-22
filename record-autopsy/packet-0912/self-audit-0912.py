#!/usr/bin/env python3
"""self-audit-0912.py —— 把"我今天到底说过什么"从会话文件里抽出来，交给同一把量具。

为什么（2026-09-12，浔："全部回头看看自己"）：
我先用 grep 数今天改过的 327 个文本文件里的"不是…是…"，得到 0 次——差点报出"这个习惯没了"。
问题在于：**我日常说的话不在那些文件里**，而在会话里；文件里只有我整理过的产物。
所以这一步把会话里属于我的消息抽出来（role=assistant），写成 autopsy 能吃的 JSONL，
口径与基线（源-信号.md 47%）可比。抽取本身就是一次筛选，所以分母要一起报。
"""
import json
import sys
from pathlib import Path

SESS = Path(r'C:\Users\Mechrevo\.dsh\storages\session_projcache\sessions')
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('self-said.jsonl')


def text_of(m):
    c = m.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                if b.get('type') == 'text' and b.get('text'):
                    parts.append(b['text'])
                elif b.get('type') == 'tool_use':
                    parts.append(f"[tool_use {b.get('name')}]")
        return '\n'.join(parts)
    return ''


files = sorted(SESS.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
rows, scanned = [], []
for f in files:
    try:
        data = json.loads(f.read_text(encoding='utf-8', errors='replace'))
    except Exception as e:                                              # noqa: BLE001
        print(f'跳过 {f.name}: {type(e).__name__}')
        continue
    msgs = data if isinstance(data, list) else (data.get('messages') or data.get('history') or [])
    n_assist = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if (m.get('role') or m.get('type')) not in ('assistant',):
            continue
        t = text_of(m).strip()
        if not t:
            continue
        rows.append({'ts': m.get('timestamp') or m.get('created_at') or '', 'role': 'assistant',
                     'content': t})
        n_assist += 1
    scanned.append((f.name, len(msgs), n_assist))
    print(f'{f.name}: 消息 {len(msgs)}，其中我的文本 {n_assist}')

out.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
print(f'写出 {out}：{len(rows)} 条我的消息，{out.stat().st_size} 字节')
print('（分母：只含 assistant 文本块，不含工具结果与系统提示）')
