# -*- coding: utf-8 -*-
r"""x402 审计仪（2026-09-22）：**不付钱**地读一个服务的"宣称"与"实际报价"，并如实分类读不到的原因。

口径（三条，都是这一窗的教训换来的）：
  ① **读不到 ≠ 没有**：`/.well-known/x402` 的失败要分开记：404／非 JSON／超时／TLS 错／其它；
  ② manifest 有两种形状：v2（对象数组，带 accepts）与**简化形（裸 URL 数组）**——后者读者拿不到价格；
  ③ 报价以**实际响应**为准：无支付请求一个 resource，看回的是 402（带 quote）还是别的；
     声明的方法不管用就换另一个方法试，并记下哪个方法答的（Vend 那次 405 就是这么排除的）。

输出：一份 JSON 数据集 + 一张汇总表。**不广播、不签名、不付钱。**
"""
import json
import os
import subprocess
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SAMPLE = os.path.join(os.environ["TEMP"], "x402-sample.json")
OUT = os.path.join(os.environ["TEMP"], "x402-audit.json")


def http(url, method="GET", t=20):
    """返回 (状态码或错误类, 头 dict, 体, 传输)。

    **2026-09-22 的教训（差点误报）**：第一版只走直连，于是 60 个服务里 20 个"超时"。
    挑 5 个复核：**经代理全部 200（约 1 秒）**，直连一律 reset/连不上。
    ⇒ 那 20 个不是"它们挂了"，是**我这条路当时不通**。
    所以现在：两条路都试，并把走通的那条记进读数（0028 第二实例那条规矩）。
    """
    last_err = "connect"
    for label, extra in (("直连", []), ("代理", ["-x", "http://127.0.0.1:7892"])):
        cmd = ["curl.exe", "-sS", "-L", "--ssl-no-revoke", "-m", str(t), "-X", method] + extra + \
              ["-A", "nvwa-x402-audit/1.0", "-H", "Accept: application/json", "-D", "-",
               "-w", "\n__STATUS__%{http_code}", url]
        p = subprocess.run(cmd, capture_output=True)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        # 走代理时 curl 会先打一行 `HTTP/1.1 200 Connection established`（CONNECT 前导），
        # 我第一版把它当成了响应状态 ⇒ 21 个服务被误分成"非 JSON"。**这是量具的产物，不是世界的答案。**
        if "__STATUS__" in out:
            out, _, real_code = out.rpartition("\n__STATUS__")
            real_code = real_code.strip()
        else:
            real_code = "?"
        parts = out.split("\r\n\r\n")
        parts = [x for x in parts if not x.startswith("HTTP/1.1 200 Connection established")
                 and not x.startswith("HTTP/1.0 200 Connection established")]
        body = parts[-1] if parts else ""
        head = "\r\n\r\n".join(parts[:-1])
        if real_code in ("", "000", "?"):
            if "timed out" in err:
                last_err = "timeout"
            elif "schannel" in err or "SSL" in err:
                last_err = "TLS"
            elif "Could not resolve" in err:
                last_err = "dns"
            else:
                last_err = "connect"
            continue
        hdrs = {}
        for line in head.split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
        return real_code, hdrs, body, label
    return last_err, {}, "", "两条路都不通"


def manifest_class(u):
    """读一个服务的 manifest，返回分类与它声明的东西。"""
    code, hdrs, body, transport = http(u + "/.well-known/x402", t=20)
    rec = {"service": u, "status": code, "transport": transport}
    if not isinstance(code, str) or not code.isdigit():
        rec["class"] = "读不到:" + str(code)
        return rec, []
    if code != "200":
        rec["class"] = "HTTP " + code
        return rec, []
    try:
        j = json.loads(body)
    except Exception:
        rec["class"] = "非 JSON"
        return rec, []
    if not isinstance(j, dict) or not j.get("resources"):
        rec["class"] = "JSON 但没有 resources"
        return rec, []
    res = j["resources"]
    objs = [r for r in res if isinstance(r, dict)]
    strs = [r for r in res if isinstance(r, str)]
    empties = [r for r in objs if not (r.get("accepts") or [])]
    if objs and empties and len(empties) == len(objs):
        rec["class"] = "v2：全部 accepts 为空（不可付）"
    elif objs:
        rec["class"] = "v2：对象数组"
    else:
        rec["class"] = "简化形：裸 URL 数组"
    rec.update({"n_resources": len(res), "n_obj": len(objs), "n_str": len(strs),
                "n_empty_accepts": len(empties), "x402Version": j.get("x402Version"),
                "contact": j.get("contact"), "has_trial_key": "trial" in json.dumps(j).lower()})
    urls = [r.get("url") for r in objs if r.get("url")] or list(strs)
    return rec, urls[:2]


data = json.load(open(SAMPLE, encoding="utf-8"))
svcs = list(data["services"].keys())[:60]
rows = []
for u in svcs:
    rec, urls = manifest_class(u)
    # 抽查一个 resource 的实际报价（只读、不付钱）
    probe = None
    for rurl in urls[:1]:
        code, hdrs, body, transport = http(rurl, "GET", t=20)
        if code == "405":
            code, hdrs, body, transport = http(rurl, "POST", t=20)
        has_402 = ("payment-required" in hdrs) or (code == "402")
        quote = None
        try:
            j = json.loads(body)
            if isinstance(j, dict):
                quote = {"error": j.get("error"), "price_xno": j.get("price_xno"),
                         "accepts": len(j.get("accepts") or []) if isinstance(j.get("accepts"), list) else None}
        except Exception:
            pass
        probe = {"resource": rurl, "status": code, "is_402": has_402, "transport": transport,
                 "headers": {k: v for k, v in hdrs.items() if k.startswith("x-") or k == "payment-required"},
                 "quote": quote}
        break
    rec["probe"] = probe
    rows.append(rec)
    print("  %-46s %-28s %s" % (u[:46], rec["class"],
                                ("探到 %s%s" % (probe["status"], " +402" if probe["is_402"] else "")) if probe else "无 resource 可探"))
    time.sleep(0.2)

print()
print("=== 汇总（n_examined=%d）===" % len(rows))
print("  manifest 分类：%s" % dict(Counter(r["class"] for r in rows)))
probed = [r for r in rows if r.get("probe")]
print("  抽查过的服务：%d；其中实际答 402 的：%d；答别的：%d"
      % (len(probed), sum(1 for r in probed if r["probe"]["is_402"]),
         sum(1 for r in probed if not r["probe"]["is_402"])))
print("  抽查到的非 402 状态码分布：%s" % dict(Counter(str(r["probe"]["status"]) for r in probed if not r["probe"]["is_402"])))
json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("  数据集写到 %s" % OUT)
