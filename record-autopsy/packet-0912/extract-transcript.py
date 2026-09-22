#!/usr/bin/env python3
"""extract-transcript.py —— 解开 DSH 的会话正文（session.v3.jsonl.zstd），抽出发言人=assistant 的文本。

为什么（2026-09-12，浔："全部回头看看自己"）：我先数磁盘上今天改过的 327 个文件，得到 0 次
"不是…是…"，差点报出"习惯没了"。真相是**我日常说的话不在那些文件里**——文件里只有我整理过的产物。
今我的话在会话正文里。这个脚本把它拿出来，好让同一把量具（autopsy.py）量我。

用法：python extract-transcript.py <out.jsonl> [--all]
只抽 assistant 的文本块（tool_use 只记名字，不记内容）；分母会一起打印。
"""
import json
import sys
from pathlib import Path

ROOT = Path(r'C:\Users\Mechrevo\.dsh\sessions')


def decompress(p: Path) -> bytes:
    raw = p.read_bytes()
    try:
        from compression import zstd                     # Python 3.14 标准库
        return zstd.decompress(raw)
    except Exception:                                                        # noqa: BLE001
        try:
            import zstandard as zstd2
            return zstd2.ZstdDecompressor().decompress(raw, max_output_size=1 << 30)
        except Exception as e:                                               # noqa: BLE001
            raise SystemExit(f'解压不了 {p.name}: {type(e).__name__}: {e}')


def text_of(obj):
    """从一条记录里取出 assistant 的文本，尽力而为但会打印它认出了什么。"""
    role = obj.get('role') or obj.get('type') or (obj.get('message') or {}).get('role')
    msg = obj.get('message') if isinstance(obj.get('message'), dict) else obj
    c = msg.get('content') if isinstance(msg, dict) else None
    parts = []
    if isinstance(c, str):
        parts.append(c)
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                if b.get('type') == 'text' and b.get('text'):
                    parts.append(b['text'])
                elif b.get('type') == 'tool_use':
                    parts.append(f"[tool {b.get('name')}]")
    return role, '\n'.join(parts).strip()


def main():
    out = Path(sys.argv[1])
    files = sorted(ROOT.rglob('session.v3.jsonl.zstd'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit('没找到 session.v3.jsonl.zstd')
    p = files[0]
    print(f'最新会话正文: {p.parent.name}  {p.stat().st_size} 字节（压缩）')
    data = decompress(p)
    lines = data.decode('utf-8', 'replace').splitlines()
    print(f'解压后 {len(data)} 字节，{len(lines)} 行')
    roles, rows = {}, []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:                                                    # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        if i < 3:
            print(f'  行{i} 键: {sorted(obj)[:12]}')
        role, txt = text_of(obj)
        roles[role] = roles.get(role, 0) + 1
        if role == 'assistant' and txt:
            rows.append({'ts': obj.get('timestamp') or obj.get('created_at') or '',
                         'content': txt})
    print(f'角色分布: {roles}')
    out.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
    print(f'写出 {out.name}: {len(rows)} 条我的消息，{out.stat().st_size} 字节')
    print('分母：本次会话正文里 role=assistant 且含文本块的记录；含 [tool …] 占位。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
