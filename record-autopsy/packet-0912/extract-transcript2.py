#!/usr/bin/env python3
"""extract-transcript2.py —— 会话正文是**事件流**（每行 {type, data, seq, time}），不是消息数组。

第一版我按 role 找，抽到 0 条——因为这里的类型是 'assistant/message'、'tool/call' 这种。
本版按 type 取：assistant/message 的 data 里取文本，写成 autopsy 吃的 JSONL。
抽不到就打印它认出的结构，不猜。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'C:\Users\Mechrevo\.dsh\sessions')


def decompress(p: Path) -> bytes:
    from compression import zstd
    return zstd.decompress(p.read_bytes())


def texts_from(data):
    """在 data 里尽力找文本块，返回 (文本列表, 认出的形状)。"""
    out, shape = [], []
    def walk(o, depth=0):
        if depth > 6:
            return
        if isinstance(o, dict):
            t = o.get('type')
            if t == 'text' and isinstance(o.get('text'), str):
                out.append(o['text'])
                shape.append('text')
            elif t == 'tool_use' or t == 'tool-call':
                shape.append(f'tool:{o.get("name")}')
            else:
                for v in o.values():
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
        elif isinstance(o, str) and depth <= 2 and len(o) > 0:
            out.append(o)                       # 有些事件直接把文本放在顶层字符串里
            shape.append('str')
    walk(data)
    return out, shape


def main():
    out = Path(sys.argv[1])
    files = sorted(ROOT.rglob('session.v3.jsonl.zstd'), key=lambda p: p.stat().st_mtime, reverse=True)
    p = files[0]
    print(f'会话: {p.parent.name}')
    lines = decompress(p).decode('utf-8', 'replace').splitlines()
    kinds, rows, sample_shown = {}, [], False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:                                                    # noqa: BLE001
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get('type')
        kinds[t] = kinds.get(t, 0) + 1
        if t != 'assistant/message':
            continue
        data = ev.get('data') or {}
        if not sample_shown:
            print('  assistant/message 的 data 键:', sorted(data)[:14] if isinstance(data, dict) else type(data).__name__)
            sample_shown = True
        txts, shape = texts_from(data)
        body = '\n'.join(x for x in txts if x.strip())
        if body.strip():
            rows.append({'ts': ev.get('time') or '', 'content': body})
    if not rows:
        print('仍未抽到文本。事件类型分布（前 12）:',
              dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:12]))
    else:
        print(f'assistant/message 事件 {kinds.get("assistant/message", 0)} 条，其中含文本 {len(rows)} 条')
    out.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
    print(f'写出 {out.name}: {len(rows)} 条，{out.stat().st_size} 字节')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
