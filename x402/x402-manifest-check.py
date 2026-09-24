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

`--probe URL`（2026-09-24 加）：直接对一个**路由**打一次不付款的请求，看它回的是不是真挑战：
    CHALLENGE_V2      402 + 头里解得出 accepts（可付款）
    CHALLENGE_V1      402 + body 里解得出 accepts
    PLATFORM_DISABLED **402 其实是平台错误**（如 Vercel 的 `X-Vercel-Error: DEPLOYMENT_DISABLED`）
                      —— 部署被停用，和 x402 挑战**共用同一个状态码**
    NO_CHALLENGE_402  402，但头和 body 都解不出付款要求
    UNPROTECTED_200   不付款直接 200
    NEEDS_INPUT       400/405/415/422（缺输入或方法不对，**不许读成付款坏了**）
    KEY_REQUIRED      401/403
    NOT_FOUND / SERVER_ERROR / UNOBSERVED

这条 `PLATFORM_DISABLED` 是我 2026-09-24 在一个市场目录上量出来的：194 个"结算已验"的
listing 里，27 个指向同一个被 Vercel 停用的部署，而它回的正是 `402 Payment required`
（Vercel 的错误形状）。**一个死掉的部署，在状态码上与一个可以付款的服务无法区分**——
任何"看到 402 就算有挑战"的检查器都会把它读成活的。所以本脚本先看平台错误头，再谈 402。

边界（写在这里，因为它决定这份读数能用来干什么）：
  · 只看**发现层**：付过钱之后能不能交付，这个脚本一句话都不说。
  · 不带任何凭据、不发送任何身份、**不付任何钱**；`--probe` 打的也是不付款的那一次。
  · 网络路径会改变读数：每一行都记 transport，失败记为 UNOBSERVED 而不是"不健康"。
"""
import argparse
import base64
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
                return ("proxy" if use_proxy else "direct"), r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return ("proxy" if use_proxy else "direct"), e.code, dict(e.headers), e.read()
        except Exception as e:                                            # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, str(e)[:80])
    return None, None, {}, last


def platform_error(headers: dict):
    """平台自己回的错误 —— 先于任何 402/5xx 解释。返回证据串或 None。"""
    low = {k.lower(): v for k, v in headers.items()}
    for k in ("x-vercel-error", "x-vercel-id", "cf-mitigated", "x-nf-error"):
        if low.get(k) and k != "x-vercel-id":
            return "%s: %s" % (k, low[k])
    if low.get("server", "").lower() == "vercel" and low.get("x-vercel-error"):
        return "x-vercel-error: %s" % low["x-vercel-error"]
    return None


def decode_challenge(headers: dict, body_text: str):
    """从响应头（v2 的 PAYMENT-REQUIRED）或 body（v1）里解出付款要求。"""
    low = {k.lower(): v for k, v in headers.items()}
    for k, v in low.items():
        if "payment-required" in k:
            for pad in ("", "=", "==", "==="):
                try:
                    obj = json.loads(base64.b64decode(v + pad))
                    return "v2", obj, k
                except Exception:                                          # noqa: BLE001
                    continue
    try:
        obj = json.loads(body_text)
        if isinstance(obj, dict) and obj.get("accepts"):
            return "v1", obj, "body"
    except Exception:                                                      # noqa: BLE001
        pass
    return None, None, None


def request_once(op, url: str, method: str, data, timeout: int):
    headers = {"User-Agent": "x402-manifest-check/1.0", "Accept": "application/json, */*"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with op.open(urllib.request.Request(url, data=data, method=method, headers=headers),
                     timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()[:4000], None
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()[:4000], None
    except Exception as e:                                                 # noqa: BLE001
        return None, {}, b"", "%s: %s" % (type(e).__name__, str(e)[:60])


def classify_probe(url: str, timeout: int = 25) -> dict:
    """对一个路由打一次不付款的请求：它回的是不是真的付款挑战？（不付费、不带凭据）

    先 GET；GET 说"缺输入/方法不对"（400/404/405/415/422）时再 POST `{}`——
    404 也重试，因为有些服务按方法与路径一起路由。两次都记在 method 里。
    """
    row = {"url": url}
    last = ""
    status = hd = raw = None
    method_used = None
    for use_proxy in (False, True):
        op = opener(use_proxy)
        for method, data in (("GET", None), ("POST", b"{}")):
            status, hd, raw, err = request_once(op, url, method, data, timeout)
            if status is None:
                last = err or ""
                continue
            method_used = method
            if method == "GET" and status in (400, 404, 405, 415, 422):
                continue                       # 换个方法再问一次
            break
        if status is not None and method_used:
            row["transport"] = "proxy" if use_proxy else "direct"
            break
    if status is None:
        row.update({"transport": None, "verdict": "UNOBSERVED",
                    "detail": "两条路都没读到：%s" % last})
        return row
    body_text = (raw or b"").decode("utf-8", "replace")
    row.update({"status": status, "method": method_used,
                "body_head": body_text[:120].replace("\n", " ")})
    plat = platform_error(hd)
    kind, obj, where = decode_challenge(hd, body_text)
    first = {}
    if kind:
        acc = obj.get("accepts") or []
        first = acc[0] if acc and isinstance(acc[0], dict) else {}
        row.update({"network": first.get("network"), "payTo": first.get("payTo"),
                    "amount": first.get("amount") or first.get("maxAmountRequired")})
    if plat and status == 402 and not kind:
        row.update({"verdict": "PLATFORM_DISABLED",
                    "detail": "402 是**平台错误**（%s），不是 x402 挑战；部署被停用" % plat})
    elif plat and status >= 500:
        row.update({"verdict": "PLATFORM_DISABLED", "detail": "平台错误：%s" % plat})
    elif status == 402 and kind:
        row.update({"verdict": "CHALLENGE_%s" % kind.upper(),
                    "detail": "付款要求来自 %s；network=%s payTo=%s" %
                              (where, first.get("network"), str(first.get("payTo"))[:14])})
    elif status == 402:
        row.update({"verdict": "NO_CHALLENGE_402",
                    "detail": "402，但头和 body 都解不出 accepts（客户端无法构建付款）"})
    elif status == 200:
        row.update({"verdict": "UNPROTECTED_200", "detail": "不付款直接 200"})
    elif status in (400, 405, 415, 422):
        row.update({"verdict": "NEEDS_INPUT",
                    "detail": "HTTP %s：缺输入或方法不对，**不算付款坏了**" % status})
    elif status in (401, 403):
        row.update({"verdict": "KEY_REQUIRED", "detail": "HTTP %s：要 key / 被拒" % status})
    elif status == 404:
        row.update({"verdict": "NOT_FOUND", "detail": "HTTP 404"})
    elif status >= 500:
        row.update({"verdict": "SERVER_ERROR", "detail": "HTTP %s" % status})
    else:
        row.update({"verdict": "HTTP_%s" % status, "detail": "HTTP %s" % status})
    return row


def classify(base: str) -> dict:
    base = base.rstrip("/")
    url = base + "/.well-known/x402"
    tr, status, headers, raw = get(url)
    row = {"service": base, "url": url, "transport": tr, "status": status}
    if transport_missing := (status is None):
        row.update({"verdict": "UNOBSERVED", "detail": "两条路都没读到：%s" % raw})
        return row
    if status != 200:
        plat = platform_error(headers or {})
        row.update({"verdict": ("PLATFORM_ERROR" if plat else
                                ("HTTP_%d" % status if status < 500 else "UNOBSERVED")),
                    "detail": ("平台错误：%s" % plat) if plat else "HTTP %s" % status})
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
    ap.add_argument("--probe", action="append", default=[],
                    metavar="URL",
                    help="直接对一个路由打一次**不付款**的请求，看它回的是不是真挑战"
                         "（可重复；会先看平台错误头，再谈 402）")
    args = ap.parse_args()

    if args.probe:
        rows = [classify_probe(u) for u in args.probe]
        if args.json:
            json.dump({"checked": len(rows), "probes": rows}, sys.stdout,
                      ensure_ascii=False, indent=2)
            print()
        else:
            for r in rows:
                print("%-18s %-58s %s" %
                      (r["verdict"], r["url"][:58], r.get("detail", "")))
        ok = ("CHALLENGE_V2", "CHALLENGE_V1", "NEEDS_INPUT", "KEY_REQUIRED")
        return 0 if all(r["verdict"] in ok for r in rows) else 1

    targets = list(args.services)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            targets += [ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
    if not targets:
        print("给至少一个服务基址（或用 --file，或用 --probe URL）。", file=sys.stderr)
        return 2

    rows = [classify(t) for t in targets]
    if args.json:
        json.dump({"checked": len(rows), "rows": rows}, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        order = ["OK", "PARTIAL", "NO_ACCEPTS", "BARE", "NO_RES", "HTTP_404",
                 "PLATFORM_ERROR", "UNOBSERVED", "OTHER"]
        for r in rows:
            print("%-14s %-46s %s" % (r["verdict"], r["service"][:46], r.get("detail", "")))
        tally = {}
        for r in rows:
            tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
        print("\n合计：%s" % " · ".join("%s %d" % (k, tally[k]) for k in order if k in tally))
        print("（只测发现层：付钱之后能不能交付，这个脚本一句话都不说）")
    return 0 if all(r["verdict"] in ("OK", "PARTIAL") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
