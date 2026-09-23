#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""x402-manifest-check.py —— 一个服务方自己就能跑的 manifest 合规检查（只用标准库）。

为什么写这个：我对着 60 个服务量过一遍，发现**最常见的缺陷不在支付层，在发现层**——
manifest 里的资源没有一种可付方式（`accepts: []`），或者 `resources` 是一串裸 URL，
于是"先定价再调用"的机器客户端**根本构建不出付款**，而服务方那边看起来一切正常。

用法：
    python x402-manifest-check.py https://api.example.com [更多基址...]
    python x402-manifest-check.py --json services.txt

判据（每条都对应一种"客户端读不到价格"的具体死法）：
    OK         至少一个 resource 带非空 accepts（或顶层带可解析的收款信息）
    BARE       resources 是字符串数组：读者只拿到 URL，拿不到价格/payTo/链/币
    NO_ACCEPTS 声明了对象型 resource，但**全部** accepts 为空 ⇒ 一个都买不了
    NO_RES     有 JSON，但没有可枚举的 resources
    HTTP_404   /.well-known/x402 不存在
    OTHER      其它（含 5xx：那是"我没读到"，不是"它没有"）

边界（写在这里，因为它决定这份读数能用来干什么）：
  · 只看**发现层**：付过钱之后能不能交付，这个脚本一句话都不说。
  · 只读 manifest，不调用任何付费端点、不带任何凭据、不发送任何身份。
  · 网络路径会改变读数：每一行都记 transport，失败记为 UNOBSERVED 而不是"不健康"。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

PROXY = "http://127.0.0.1:7892"


def opener(use_proxy: bool):
    handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY} if use_proxy else {})
    return urllib.request.build_opener(handler)


def get(url: str, timeout: int = 30):
    """直连先试，失败走代理；把走通的那条记下来（同一条 URL 换一条路就是另一个世界）。"""
    last = ""
    for use_proxy in (False, True):
        try:
            with opener(use_proxy).open(urllib.request.Request(url, headers={
                    "User-Agent": "x402-manifest-check/1.0",
                    "Accept": "application/json"}), timeout=timeout) as r:
                return ("proxy" if use_proxy else "direct"), r.status, r.read()
        except urllib.error.HTTPError as e:
            return ("proxy" if use_proxy else "direct"), e.code, e.read()
        except Exception as e:                                            # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, str(e)[:80])
    return None, None, last


def classify(base: str) -> dict:
    base = base.rstrip("/")
    url = base + "/.well-known/x402"
    tr, status, raw = get(url)
    row = {"service": base, "url": url, "transport": tr, "status": status}
    if transport_missing := (status is None):
        row.update({"verdict": "UNOBSERVED", "detail": "两条路都没读到：%s" % raw})
        return row
    if status != 200:
        row.update({"verdict": "HTTP_%d" % status if status < 500 else "UNOBSERVED",
                    "detail": "HTTP %s" % status})
        return row
    body = raw.decode("utf-8", "replace")
    row["bytes"] = len(raw)
    try:
        m = json.loads(body)
    except json.JSONDecodeError as e:
        row.update({"verdict": "OTHER", "detail": "不是 JSON：%s" % str(e)[:80]})
        return row
    if not isinstance(m, dict):
        row.update({"verdict": "OTHER", "detail": "顶层不是对象（%s）" % type(m).__name__})
        return row
    row["x402Version"] = m.get("x402Version")
    res = m.get("resources")
    if res is None:
        top_pay = {k: m.get(k) for k in ("accepts", "payTo", "paymentInfo", "recipients")
                   if m.get(k)}
        row.update({"verdict": "NO_RES",
                    "detail": "没有 resources 字段；顶层收款线索：%s"
                              % (", ".join(top_pay) if top_pay else "（也没有）")})
        return row
    if isinstance(res, list) and res and all(isinstance(x, str) for x in res):
        row.update({"verdict": "BARE", "resources": len(res),
                    "detail": "%d 个裸 URL：读者拿不到价格/payTo/链/币" % len(res)})
        return row
    if not isinstance(res, list):
        row.update({"verdict": "OTHER", "detail": "resources 不是数组（%s）" % type(res).__name__})
        return row
    objs = [x for x in res if isinstance(x, dict)]
    empty = [x for x in objs if not (x.get("accepts") or [])]
    with_accepts = len(objs) - len(empty)
    row.update({"resources": len(objs), "with_accepts": with_accepts,
                "empty_accepts": len(empty)})
    if objs and with_accepts == 0:
        row.update({"verdict": "NO_ACCEPTS",
                    "detail": "%d 个对象型 resource，**一个都不带非空 accepts**"
                              "（价格也许在别的字段里，但标准客户端不读那里）" % len(objs)})
    elif empty:
        row.update({"verdict": "PARTIAL",
                    "detail": "%d 个可付 / %d 个 accepts 为空" % (with_accepts, len(empty))})
    else:
        row.update({"verdict": "OK", "detail": "%d 个 resource 全部带非空 accepts" % len(objs)})
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="x402 manifest 发现层合规检查（只读、无凭据、不付费）")
    ap.add_argument("services", nargs="*", help="服务基址，如 https://api.example.com")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    ap.add_argument("--file", help="从文件读列表（每行一个基址，# 开头跳过）")
    args = ap.parse_args()

    targets = list(args.services)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            targets += [ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
    if not targets:
        print("给至少一个服务基址（或用 --file）。", file=sys.stderr)
        return 2

    rows = [classify(t) for t in targets]
    if args.json:
        json.dump({"checked": len(rows), "rows": rows}, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        order = ["OK", "PARTIAL", "NO_ACCEPTS", "BARE", "NO_RES", "HTTP_404", "UNOBSERVED", "OTHER"]
        for r in rows:
            print("%-11s %-46s %s" % (r["verdict"], r["service"][:46], r.get("detail", "")))
        tally = {}
        for r in rows:
            tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
        print("\n合计：%s" % " · ".join("%s %d" % (k, tally[k]) for k in order if k in tally))
        print("（只测发现层：付钱之后能不能交付，这个脚本一句话都不说）")
    return 0 if all(r["verdict"] in ("OK", "PARTIAL") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
