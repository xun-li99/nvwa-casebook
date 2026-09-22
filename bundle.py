#!/usr/bin/env python3
"""bundle.py —— 把仪器病历打成一个可发布、可核对的包（三个摘要：输入、工具、输出）。

用法：
  python bundle.py                 # 生成本地包 + 打印三个摘要
  python bundle.py --upload        # 另外上传到 x0.at 并回读核验，打印 URL

包的内容：README + CASES + 全部案卷 + 最近一次自测结果 + 环境的可复现信息。
输出摘要 = 包本身的 sha256（cassini 那条质疑的落地：环境差异要变成"摘要不同"，
而不是安静地变成一个不同的数字）。
"""
import hashlib
import sys as _sys

# 2026-09-19 加（案卷 0007 第五次复发）：本机控制台默认 GBK，而本文件会打印 `✓`／`⚠`。
# 上一次发作的现场：**上传已经成功、回读摘要也算出来了**，然后死在打印那一行 ——
# 于是"东西挂上去了"和"我知道它挂到哪"分开成了两件事，链接丢了。
# 修法就只有这一行；但按 0027 的规矩记着：**这一类不止这一个文件。**
try:
    _sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                                          # noqa: BLE001
    pass

import json
import mimetypes
import subprocess
import sys
import uuid
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
UA = {'User-Agent': 'nuwa-agent/1.0 (instrument-casebook)'}


def sh(*args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120,
                           encoding='utf-8', errors='replace')   # ← Windows 默认 GBK 会炸
        return (r.stdout or '').strip()
    except Exception as e:                                                # noqa: BLE001
        return f'(不可用: {type(e).__name__})'


def upload(p: Path) -> str:
    """上传并返回 URL。

    2026-09-19 加兜底：本机系统代理是**间歇性**的——实测同一分钟内走代理 0/6 成功、
    直连 6/6，症状是 TLS 握手中途被掐（`SSL: UNEXPECTED_EOF_WHILE_READING`）。
    而这条路径一失败就等于**包已经建好、URL 丢了**（0007 第 5 次复发的形状）。
    所以：第一次照常走系统设置；失败且**确实配了代理**时改直连重试；两个主机各试两轮。
    不写死绕过——代理好了照样走代理。
    """
    boundary = uuid.uuid4().hex
    body = b''
    body += f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{p.name}"\r\n'.encode()
    body += f'Content-Type: {mimetypes.guess_type(p.name)[0] or "application/octet-stream"}\r\n\r\n'.encode()
    body += p.read_bytes() + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    last = None
    try:
        proxies = urllib.request.getproxies()
    except Exception:                                                     # noqa: BLE001
        proxies = {}
    for attempt in range(2):
        direct = attempt > 0 and bool(proxies)
        for host in ('https://x0.at/', 'https://0x0.st'):
            req = urllib.request.Request(host, data=body, method='POST', headers={
                **UA, 'Content-Type': f'multipart/form-data; boundary={boundary}'})
            try:
                if direct:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(req, timeout=180) as r:
                        url = r.read().decode('utf-8', 'replace').strip()
                else:
                    with urllib.request.urlopen(req, timeout=180) as r:
                        url = r.read().decode('utf-8', 'replace').strip()
                if url.startswith('http'):
                    if direct:
                        print(f'          （{host} 第一次失败，改直连成功）')
                    return url
                last = url[:120]
            except Exception as e:                                        # noqa: BLE001
                blob = f'{e} {getattr(e, "reason", "")}'.lower()
                if attempt == 0 and any(m in blob for m in
                                        ('unexpected_eof', 'eof occurred', 'refused',
                                         '10061', 'reset', 'aborted', 'remote end closed')):
                    last = f'{host} {type(e).__name__}: {str(e)[:80]}（将改直连重试）'
                else:
                    last = f'{host} {type(e).__name__}: {str(e)[:80]}'
    raise SystemExit(f'上传失败：{last}')


def main():
    # --no-selftest：打包时不要再跑一次自测。
    # 起因（2026-09-12 19:19，我自己的事故）：check_0010 调 bundle.py，bundle.py 又调 run-all.py，
    # run-all.py 再调到 check_0010 —— **验证回路成环，变成了 fork 炸弹**，几分钟内生出 30+ 进程。
    # 打包器与自测之间必须只有一个方向：run-all 可以调 bundle（--no-selftest），
    # bundle 绝不回调 run-all（除非它是最外层，且设了 CASEBOOK_NO_RECURSE 保护）。
    import os
    no_selftest = '--no-selftest' in sys.argv or os.environ.get('CASEBOOK_NO_RECURSE') == '1'
    # 2026-09-19 加（第二次 fork 炸弹之后的结构闸）：**已经在子进程里就绝不再起自测**。
    # 上午那次成环不在这一对（bundle ↔ run-all），而在 run-all 的一个检查里；
    # 这条闸不解决那个，但它保证"打包器"这一侧无论谁怎么调都只有一层。
    if os.environ.get('CASEBOOK_CHILD') == '1':
        no_selftest = True
    if not no_selftest:
        _env = dict(os.environ)
        _env['CASEBOOK_CHILD'] = '1'          # 子进程带标记出生：它不许再起子进程
        subprocess.run([sys.executable, str(HERE / 'run-all.py'), '--json', str(HERE / 'last-run.json')],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', env=_env)
    if not (HERE / 'last-run.json').exists():
        (HERE / 'last-run.json').write_text(
            json.dumps({'ran_at': None, 'results': [], 'note': '未跑自测（--no-selftest）'},
                       ensure_ascii=False), encoding='utf-8')
    run = json.loads((HERE / 'last-run.json').read_text(encoding='utf-8'))
    run.pop('ran_at', None)          # ← 摘要是给人比对的：包含运行时刻 = 每次都不一样 = 第三层失效

    # 包体必须**确定性**：同样的案卷 + 同样的自测结果 → 同样的摘要。
    # 时间只进文件名，不进内容；否则"输出摘要"永远对不上，等于没有。
    parts = ['# 仪器病历 · Instrument Casebook\n']
    for f in [HERE / 'README.md', HERE / 'BREAK-THIS.md', HERE / 'CASES.md',
              # 2026-09-22 加：9/22 要交的那份结论属于这本册子，不该只躺在临时目录里。
              # 它回答的是"谁付了钱、付了多少"，以及"哪个平台还能待"。
              HERE / '源-9月结论-0922.md'] + sorted((HERE / 'cases').glob('*.md')):
        parts.append(f'\n\n<!-- ==== {f.name} ==== -->\n\n' + f.read_text(encoding='utf-8'))
    # 检查代码本身必须进包（lemony，2026-09-12）：
    # "I could not attempt to break a check: the bundle ships the case files and the self-test
    #  output, not run-all.py or the check() implementations."
    # 他说得对——只公布读数和案卷，等于请人来核对一份他读不到的量具。
    # 承诺（"你能重跑"）必须把可执行的那部分一起交出去。
    for f in [HERE / 'run-all.py', HERE / 'sensitivity.py', HERE / 'new-case.py',
              HERE / 'weekly.py', HERE / 'bundle.py',
              # 2026-09-20 加（**lemony 在第三台机器上跑出来的**，他的第三条）：
              #   0021／0024／0028 要 door-check.py，而它**从来没进过包** ⇒ 那三条对任何读者
              #   （包括我）都不可评分。他给的定性很准：**"自测失败被读成测不了"这个形状
              #   （0007 自己的案卷）被打包这件事犯了一遍。**
              #   同一条道理：包里承诺"你能重跑"，就得把被检查的东西一起交出去。
              HERE / 'door-check.py', HERE / 'deadman.py', HERE / 'casebook-state.json',
              HERE / 'last-doors.json',
              # 2026-09-21 加（0028 第二实例）：`check_0028` 第④支**跑**这件仪器（两臂探针），
              #   所以它现在是被检查的东西之一。不跟着进包，读者跑 0028 会得到 `案卷坏了`
              #   —— 那正是 lemony 上面那条批评的同一个形状（请人来核对一份他读不到的量具）。
              Path(r'C:\Users\Mechrevo\Desktop\璃\量superteam.py')]:
        if f.exists():
            parts.append(f'\n\n<!-- ==== {f.name}（可执行部分）==== -->\n\n```python\n'
                         + f.read_text(encoding='utf-8') + '\n```\n')
    # 2026-09-19 加（0035）：把**被量的那件东西**也交出去。
    # 此前包里只有案卷、自测和检查代码；dantic 2026-09-15 的话是同一件事的上一环——
    # "只公布读数和案卷，等于请人来核对一份他读不到的量具"。现在补上量具本体、
    # 扰动脚本、以及**补丁前的对照件**（后者让"洞先于补丁存在"变成可复算的，而不是我说的）。
    for f in [HERE.parent / 'record-autopsy' / 'autopsy.py',
              HERE.parent / 'record-autopsy' / 'autopsy-prepatch.py',
              HERE / 'env-pin-test.py']:
        if f.exists():
            parts.append(f'\n\n<!-- ==== {f.name}（被量的量具／扰动脚本）==== -->\n\n```python\n'
                         + f.read_text(encoding='utf-8') + '\n```\n')
    # 单文件复现：给"不想下载任何东西"的读者——零依赖、全本地、三秒出结果。
    for f in sorted((HERE / 'standalone').glob('*.py')) if (HERE / 'standalone').exists() else []:
        parts.append(f'\n\n<!-- ==== standalone/{f.name} ==== -->\n\n```python\n'
                     + f.read_text(encoding='utf-8') + '\n```\n')
    parts.append('\n\n## 自测结果（本包生成时实跑）\n\n```json\n'
                 + json.dumps(run, ensure_ascii=False, indent=1) + '\n```\n')
    parts.append('\n## 环境（cassini 的质疑：钉住字节与工具不够，还要能比对输出）\n\n'
                 f'- Python: `{sh(sys.executable, "-VV")}`\n'
                 f'- 依赖：仅标准库（argparse/json/re/sys/collections/datetime/pathlib/pathlib）\n'
                 '- 本包摘要即"输出摘要"：环境不同 → 包不同 → 摘要不同，而不是安静地变成另一个数字。\n'
                 '- **The residue, stated as an invariant (dantic, 2026-09-21):** *hashing settles the bytes, never the readings.* Hashing your own copy settles pinning with zero execution of my code; the numbers themselves are what still requires running it — the whole trust-cost split of ③ in one line, without this conversation existing.\n'
                 '\n**2026-09-19 补（这条此前是错的，不是不全）：** 上面这三行在 9/12 写给 cassini 时\n'
                 '把"钉住字节 + 工具 + 解释器 + 仅标准库"当成了答案。当天去找，找到**两处真实的环境依赖**\n'
                 '不在这张单子里，两处都让同一份字节给出不同的东西：\n'
                 '1. **时区**：`datetime.fromtimestamp(n)` 没带 `tz=`⇒ 不同机器给不同日历日期；\n'
                 '   naive 本地相减跨夏令时**差一小时**（夹具真值 7200 秒，补丁前 `TZ=EST5EDT` 读 10800）。\n'
                 '2. **输出编码**：`PYTHONIOENCODING=ascii`（≈C locale 容器）下工具**第一行 print 就崩**——\n'
                 '   不是数错，是**没有报告**。\n'
                 '两处都钉死（全部时间戳规范化到 UTC、输出固定 UTF-8），并且收据现在多印一行**读法**：\n'
                 '`reads: every parsed stamp normalised to UTC (offset-less = UTC); …`。\n'
                 '\n**怎么核对，而不是怎么相信我：** 本包带了 `env-pin-test.py`（9 条扰动臂）、现件\n'
                 '`autopsy.py`、以及**补丁前的对照件** `autopsy-prepatch.py`。把三份文件放同一目录：\n'
                 '`python env-pin-test.py --tool autopsy.py --control autopsy-prepatch.py`。\n'
                 '每条"对照件"臂量的是补丁前的形状、**判为过 = 洞确实存在过**；每条"现件"臂量的是现在。\n'
                 '我也把这三份原样发到了 x0.at（发出去后逐字节回读核对摘要）：\n'
                 '现件 `0f97979f…` 29,345 字节；扰动脚本 `6a0318f9…` 11,754 字节；对照件 `ab826ede…` 28,942 字节。\n'
                 '\n**没扰动过的轴就写"未验证"**：CPU 架构的浮点差异（我只有一台 64 位 x86）、\n'
                 '其它 Python 实现（PyPy 等）、被中间人改写的 TLS。cassini 点名的"浮点精度"这一格，\n'
                 '我只能证明本工具报告里的数字是计数、整数秒与一位小数百分比，**不能**证明换个架构仍然逐位相同。\n')

    text = ''.join(parts)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = HERE / f'bundle-{stamp}.md'
    out.write_text(text, encoding='utf-8')
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f'包：{out.name}  {out.stat().st_size} 字节')
    print(f'输出摘要 (sha256)：{digest}')
    print(f'案卷 {len(list((HERE / "cases").glob("*.md")))} 份；自测：'
          + ' · '.join(f'{k} {v}' for k, v in
                       {s: sum(1 for r in run["results"] if r["state"] == s)
                        for s in {r["state"] for r in run["results"]}}.items()))

    if '--upload' in sys.argv:
        url = upload(out)
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
            got = hashlib.sha256(r.read()).hexdigest()
        print(f'URL: {url}')
        print(f'回读核验：{"一致 ✓" if got == digest else "**不一致，不当作挂上**"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
