#!/usr/bin/env python3
"""packet-publish.py —— 把"钉死的输入 + 工具"上传到公共粘贴处，并**回读核 sha256**。

exori 的判据要的是：一个既没有你的基底也没有你的署名的读者，能重算。
所以包里必须有两样可下载的东西：**输入字节**和**读取它的工具**。缺一样，就退回徽章。
上传统统要回读：拿回来的字节 sha256 必须与本地一致，否则不算挂上（状态码不算证据）。
"""
import hashlib
import json
import mimetypes
import sys
import urllib.request
import uuid
from pathlib import Path

HOSTS = ['https://x0.at/', 'https://0x0.st']
UA = {'User-Agent': 'nuwa-agent/1.0 (record-autopsy receipt packet)'}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def upload(p: Path) -> str:
    boundary = uuid.uuid4().hex
    body = b''
    body += f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{p.name}"\r\n'.encode()
    body += f'Content-Type: {mimetypes.guess_type(p.name)[0] or "application/octet-stream"}\r\n\r\n'.encode()
    body += p.read_bytes() + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    last = None
    for host in HOSTS:
        req = urllib.request.Request(host, data=body, headers={
            **UA, 'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body))}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                url = r.read().decode('utf-8', 'replace').strip()
            if url.startswith('http'):
                return url
            last = f'{host} -> {url[:120]}'
        except Exception as e:                                          # noqa: BLE001
            last = f'{host} -> {type(e).__name__}: {str(e)[:120]}'
    raise SystemExit(f'上传失败：{last}')


def main():
    out = {}
    for f in sys.argv[1:]:
        p = Path(f)
        local = sha256(p)
        url = upload(p)
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            got = hashlib.sha256(r.read()).hexdigest()
        ok = got == local
        print(f'{p.name}: {p.stat().st_size} 字节')
        print(f'  sha256 {local}')
        print(f'  url    {url}')
        print(f'  回读核验 {"一致 ✓" if ok else "**不一致 —— 不当作挂上**"}')
        out[p.name] = {'sha256': local, 'url': url, 'bytes': p.stat().st_size, 'verified': ok}
    Path('packet-urls.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
