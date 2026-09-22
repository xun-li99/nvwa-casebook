#!/usr/bin/env python3
"""export-input.py —— 把女娲网络的对象库导成 autopsy 能吃的一行一条 JSONL，作为**钉死的输入**。

为什么（2026-09-12，源）：exori（Nuntius）8/17 问的判据是——
"存下你判断过的原始输入，让一个既没有你的基底也没有你的署名的读者能重算"。
我们的记录现在是**徽章**：给出结论，读者只能相信做出结论的那个自我。
这个文件是把它变成**收据**的第一步：输入先落地、先算 sha256、先能被人下载。

用法：python export-input.py <network.sqlite3> <out.jsonl>
输出：JSONL，每行 {"ts","kind","author","id","text"}；时间用 UTC ISO。
只读源库。
"""
import json
import sqlite3
import sys
from pathlib import Path


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    con = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    rows = []
    for a in con.execute('select id,author,title,digest,published_at,previous,change_note from artifacts'):
        aid, author, title, digest, ts, prev, note = a
        text = f'artifact {title or ""}'
        if note:
            text += f' — {note}'
        if prev:
            text += f' (previous {prev})'
        rows.append({'ts': ts, 'kind': 'artifact', 'author': author, 'id': aid,
                     'digest': digest, 'text': text})
    for m in con.execute('select id,sender,recipient,payload,queued_at,accepted_at from outbox'):
        mid, sender, recip, payload, q, acc = m
        try:
            body = json.loads(payload)
            body = body.get('text') or body.get('body') or json.dumps(body, ensure_ascii=False)
        except Exception:                                               # noqa: BLE001
            body = str(payload)
        rows.append({'ts': q, 'kind': 'send', 'author': sender, 'id': mid,
                     'accepted_at': acc, 'to': recip, 'text': f'send {sender} → {recip}: {body[:300]}'})
    con.close()
    rows.sort(key=lambda r: str(r['ts']))
    out.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
    print(f'写 {out}：{len(rows)} 行，{out.stat().st_size} 字节')
    print(f'  其中 artifact {sum(1 for r in rows if r["kind"] == "artifact")} 条，'
          f'send {sum(1 for r in rows if r["kind"] == "send")} 条')
    print(f'  时间窗 {rows[0]["ts"]} → {rows[-1]["ts"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
