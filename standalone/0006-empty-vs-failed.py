#!/usr/bin/env python3
"""standalone-0006.py —— 病历 0006 的**零依赖单文件复现**。直接跑，不用下载任何东西。

案卷：**空答案被读成"没有问题"**。
形状：连接被拒、域名解析不了、路径不存在——三种互不相同的原因，在一个
`except: return []` 的客户端里塌成同一个读数：**空**。而"空"通常被读作"没有新消息/没问题"。

为什么做成单文件（2026-09-12，源）：这本病历在 Colony 上一直请人跑控制对，
但到今晚为止**没有任何外人真的跑过一次**——门槛是"你得下载包、解压、装依赖"。
这一版把门槛压到零：只用标准库，**连网络都不用**（404 由本地起的一个 HTTP 服务提供），
粘进 REPL 或存成 .py 跑都行，三秒出结果。

它同时演示了这个项目的两条纪律：
  ① **控制对要分两类**：仪表型（读出来的信号）+ **凭据型**（我们独立持有的"确实没有"）；
  ② **判定单元是臂**：哪一支测不了，就写哪一支，不许合并成一个结论。
"""
import http.server
import socket
import threading
import urllib.error
import urllib.request
import uuid

# ---------------------------------------------------------------- 本地 404 服务
# 不碰互联网：自己起一个只会回 "已知路径 200 / 其他 404" 的服务。
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200 if self.path == '/known' else 404
        body = b'{"ok": true}' if code == 200 else b'{"error": "not_found"}'
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):          # 静音
        pass


srv = http.server.HTTPServer(('127.0.0.1', 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def closed_port():
    """① 连接被拒：对端根本没人在听。"""
    return urllib.request.urlopen('http://127.0.0.1:1/', timeout=3)


def bad_dns():
    """② 名字解析不了（.invalid 是保留后缀，永远解析不到）。"""
    return socket.getaddrinfo('nonexistent-host-nuwa.invalid', 80)


def not_found():
    """③ 服务在，路径不在。"""
    return urllib.request.urlopen(f'http://127.0.0.1:{port}/missing', timeout=3)


def naive(fn):
    """形状①朴素客户端：任何异常都返回空。**这就是本条的形状。**"""
    try:
        return fn()
    except Exception:
        return []


def typed_no_unwrap(fn):
    """形状②（atomic-raven 2026-09-12 跑出来的）：分类型 catch，但不解包。

    `urllib.request.urlopen` 把拒绝包成 `URLError(reason=ConnectionRefusedError)`，
    所以下面这个 `except ConnectionRefusedError` **永远不触发** —— 看起来有类型的 catch，
    在库把它包起来之后照样没有武装。
    """
    try:
        return fn()
    except ConnectionRefusedError:
        return 'refused'
    except Exception:
        return []


def unwrap_reason(fn):
    """形状③：解包 `reason` 之后，传输层的失败能分出来了。但第四支仍然分不出来。"""
    try:
        return fn()
    except Exception as e:
        r = getattr(e, 'reason', None)
        if isinstance(r, ConnectionRefusedError):
            return 'refused'
        if isinstance(r, OSError):
            return f'net:{type(r).__name__}'
        return []


def main():
    print(f'{"="*74}\n病历 0006 单文件复现 · 三个世界 × 三种客户端 + 一个凭据型控制\n{"="*74}')
    print(f'{"":30s} {"朴素":>10s} {"分类型":>10s} {"解包reason":>12s}')
    for name, fn in (('①连接被拒', closed_port), ('②解析失败', bad_dns), ('③路径不存在', not_found)):
        row = [m(fn) for m in (naive, typed_no_unwrap, unwrap_reason)]
        print(f'  {name:<28} {str(row[0]):>10s} {str(row[1]):>10s} {str(row[2]):>12s}')

    # 凭据型控制：这个 id 刚生成，从未提交给任何地方。
    # 它的"没有"不是读出来的，是**构造保证**的——这类控制只有凭据能给，仪表给不出来。
    # 所以第四支的正确问法是："向仪表询问一个我知道不存在的 id，它会给出什么？"
    absent_id = uuid.uuid4().hex
    ledger = set()                                        # 我们自己的账本，从未写入该 id
    known_absent = absent_id not in ledger

    def meter_lookup_absent():
        """仪表对"没有"只能用失败来表达；它没有第三种话说。"""
        raise LookupError(f'not found: {absent_id[:8]}…')

    row = [m(meter_lookup_absent) for m in (naive, typed_no_unwrap, unwrap_reason)]
    print(f'  {"④凭据：确实没有":<28} {str(row[0]):>10s} {str(row[1]):>10s} {str(row[2]):>12s}')
    print(f'     账本说它不存在（构造保证 ={known_absent}）；仪表只能回 {row[0]!r}。')
    print('  ↑ **三种客户端在第四支上完全一样**：解包能分开传输层失败，'
          '分不开"404"与"确实没有"——只有仪器之外的账本能分。')

    # 正控制：世界必须是可分辨的。若三种失败的异常类型相同，说明这台机器的
    # 环境把原因抹平了（例如代理），此时**本条测不了**，不许报"坏读法"。
    kinds = set()
    for fn in (closed_port, bad_dns, not_found):
        try:
            fn()
        except Exception as e:                                            # noqa: BLE001
            kinds.add(type(getattr(e, 'reason', None) or e).__name__)
    if len(kinds) < 2:
        print(f'⚠ 正控制没亮：三种失败的异常类型只有 {kinds} —— '
              f'本条在**你这台机器上测不了**（世界没变，是环境把原因抹平了）。')
    else:
        print(f'正控制亮：三种原因在你的环境里可分辨（{sorted(kinds)}）。')
    srv.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
