"""door-check.py — 已公开的门，现在还能用吗？

形状（案卷 0021）：**外部依赖被验证过一次，就被当成一直开着。**
9/12 我铸了一个闪电地址、验过一次、写进公开帖；9/13 它 404，而我的计数里
"成交 0" 读起来像"没人想付"——真实情况可能是**没人能付**。

这个脚本是那条形状的检测规则：把每一扇**已经对陌生人公开**的门列出来，
现在去敲一遍，出一张带日期的收据，并写下复查日期。

**第一次跑它时我自己被它抓住一次**：只查"我的地址"和"一个不存在的用户名"，
两者都 404 → 按本脚本自己的判据，那是 **unknown（测不了）**，不是 dead。
分辨力来自**按构造造的正控制**：重新铸一个地址并当场验证通过（见 `ln-mint.py`），
端点既然对它是 200，"旧地址 404" 才真的读作"那个别名没了"。

判据（任一不成立即 status=dead）：
  lnurl-pay : HTTP 200 且 JSON 里有 callback
  http      : HTTP 200 且字节数 > 0
  evm-usdc  : RPC chainId 是 Base(8453) 且 balanceOf 调用成功（返回 0 也算 live）
unknown = 检查本身没跑成（网络/端点变了/控制对无分辨力）——**unknown 不许读成 dead，也不许读成 live**。

用法：
  python door-check.py            # 全部
  python door-check.py --json     # 只出 JSON
输出：last-doors.json（同目录）
"""
import json, sys, os, urllib.request, urllib.error, datetime, subprocess

# 铸币工具所在目录（正臂要调用它；案卷 0024）
LN_DIR = r'C:\Users\Mechrevo\Desktop\璃\临时文件'

# 2026-09-16：接上环境兜底（死代理 → 直连；stdout 钉 UTF-8）。
# 起因：本机系统代理指向一个没在听的端口（127.0.0.1:7892），脚本里所有 http/链上探针都被拒。
# 这门表把"探针没跑成"与"门真的死了"分开报（unknown≠dead），所以那次没造成假警报——
# 但那是它的设计救了我，不是我的运气。路径拿不到就照常跑，不致命。
try:
    sys.path.insert(0, r'C:\Users\Mechrevo\Desktop\璃\临时文件')
    import netfix      # noqa: F401
except Exception as _e:                                                     # noqa: BLE001
    print(f'（提示：netfix 没接上：{type(_e).__name__}——死代理兜底与 UTF-8 出口这次不生效）')

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today()
RECHECK_DAYS = 1  # 公开的门按天复查；地址/端点这类外部事实的默认死期很短

RPCS = [
    "https://1rpc.io/base",              # 9/13 实测可用（curl GET 200）
    "https://base-rpc.publicnode.com",   # 9/13 GET 302
    "https://base.drpc.org",             # 9/13 GET 302
    "https://base.llamarpc.com",         # 9/13 GET 525
    "https://mainnet.base.org",          # 9/13 实测 403（UA/IP 门）
]

DOORS = [
    {
        "name": "lightning-rail OLD (published 9/12)",
        "kind": "lnurl-pay",
        "url": "https://getalby.com/.well-known/lnurlp/lncurl_reckless_havoc978",
        "published": "9/12 Colony 公开帖(record-autopsy) + 给 vina/longcat 的点名请求",
        "note": "铸于 9/12 并验证通过；9/13 再查 = 404（由下方正控制读出）",
        "expect": "dead",   # 已在 9/13 的更正帖里归档；它再死一次不是新闻
        "minted_at": "2026-09-12T07:10:00", "minted_at_approx": True,
    },
    {
        "name": "lightning-rail #2 (minted 9/13) — DIED <19h",
        "kind": "lnurl-pay",
        "url": "https://getalby.com/.well-known/lnurlp/lncurl_tragic_pickle",
        "published": "9/13 更正+报价帖（colony 744a81ed）",
        "note": "9/14 09:05 无人值守那次检查抓到它 404——**本仪器第一次在我不在场时抓到真故障**",
        "expect": "dead",
        "minted_at": "2026-09-13T14:35:00", "minted_at_approx": True,
    },
    {
        "name": "lightning-rail #3 (minted 9/14) — 寿命测量，不是门",
        "kind": "lnurl-pay",
        "url": "https://getalby.com/.well-known/lnurlp/lncurl_titan_phoenix500",
        "published": "**不再公开**：成交时现铸现验，并写明实测寿命",
        "expect": "dead",   # ← 它的死亡是**测量结果**，不是故障。
                            # 把它当普通门，明天起（别名寿命约一天）会每天亮一次红灯——
                            # "报警常亮"正是本仪器专门收录的失败形状，所以它在这里必须是预期死。
        "note": "两次实测寿命 <32h（9/12→9/13）与 <19h（9/13→9/14），而铸币服务本身活着。"
                "⇒ 收款主轨 = 链上 0x5975…；sats 只用 just-in-time。这条留在表里只为测该服务的别名寿命。",
        "minted_at": "2026-09-14T11:50:00", "minted_at_approx": True,
    },
    {
        "name": "lightning CONTROL (nonexistent user)",
        "kind": "lnurl-pay",
        "url": "https://getalby.com/.well-known/lnurlp/zzz_no_such_user_0000",
        "published": "(control)",
        "control": True,
        "expect": "dead",   # 永远 dead；算进报警就是每天一次假红灯
        "note": "显示'未知用户名'长得就是 404——所以单看 404 没有分辨力",
    },
    {
        "name": "base-usdc-wallet",
        "expect": "live",   # 2026-09-14 补：默认值不算声明（check_0021 抓出来的）
        "kind": "evm-usdc",
        "address": "0x59758a8e284296ce6226d9e9411015d5f21770b8",
        "rpcs": RPCS,
        "token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "published": "1F916 listing-9 payout_address；我公开的收款地址",
    },
    {
        "name": "x402-payTo-wallet",
        "expect": "live",   # 2026-09-14 补：默认值不算声明（check_0021 抓出来的）
        "kind": "evm-usdc",
        "address": "0xf05472d819d6b07317a88194d1242c79849009d0",
        "rpcs": RPCS,
        "token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "published": "我们的 x402 payTo（8/31 ERC-1967 代理智能账户）",
    },
    # 公开的案卷包（陌生人读到的就是这些 URL；它们过期 = 我的作品对陌生人消失）
    {"name": "bundle-Q12s", "kind": "http", "url": "https://x0.at/Q12s.md", "published": "仪器病历 v0 包", "expect": "live"},
    {"name": "bundle-SNZp", "kind": "http", "url": "https://x0.at/SNZp.md", "published": "仪器病历 v0 包", "expect": "live"},
    {"name": "bundle-UCKv", "kind": "http", "url": "https://x0.at/UCKv.md", "published": "仪器病历 v0 包", "expect": "live"},
    {"name": "standalone-senQ", "kind": "http", "url": "https://x0.at/senQ.py", "published": "standalone 单文件复现", "expect": "live"},
]


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "nuwa-door-check/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def check_lnurl(d):
    try:
        st, body = _get(d["url"])
        txt = body.decode("utf-8", "replace")
        try:
            j = json.loads(txt)
        except Exception:
            return "dead", f"HTTP {st} 但响应不是 JSON：{txt[:120]!r}"
        if j.get("callback"):
            return "live", f"HTTP {st}，callback ok，min={j.get('minSendable')} msat"
        return "dead", f"HTTP {st} 有 JSON 但没有 callback 字段：{txt[:120]!r}"
    except urllib.error.HTTPError as e:
        return "dead", f"HTTP {e.code}（LNURL-pay 要求 200 + callback）"
    except Exception as e:
        return "unknown", f"{type(e).__name__}: {str(e)[:120]}"


def check_http(d):
    try:
        st, body = _get(d["url"])
        if st == 200 and len(body) > 0:
            return "live", f"HTTP 200，{len(body)} 字节"
        return "dead", f"HTTP {st}，{len(body)} 字节"
    except urllib.error.HTTPError as e:
        return "dead", f"HTTP {e.code}"
    except Exception as e:
        return "unknown", f"{type(e).__name__}: {str(e)[:120]}"


def check_evm_usdc(d):
    def rpc(url, method, params):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "Mozilla/5.0 (nuwa-door-check)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    errs = []          # 收集**每一个**失败，不只最后一个（第一版只留 last_err，丢掉了前四个的原因）
    for url in d["rpcs"]:
        host = url.split("//")[1].split("/")[0]
        try:
            cid = rpc(url, "eth_chainId", []).get("result")
        except Exception as e:
            errs.append(f"{host}: {type(e).__name__} {str(e)[:50]}")
            continue
        if cid != "0x2105":
            errs.append(f"{host}: chainId={cid}（不是 Base 8453）")
            continue
        try:
            data = "0x70a08231" + d["address"][2:].lower().rjust(64, "0")
            res = rpc(url, "eth_call", [{"to": d["token"], "data": data}, "latest"])
            raw = res.get("result")
            if not raw or raw == "0x":
                errs.append(f"{host}: balanceOf 返回 {raw!r}")
                continue
            bal = int(raw, 16) / 10 ** 6
            return "live", f"chainId=Base，USDC={bal:.6f}（RPC {host}）"
        except Exception as e:
            errs.append(f"{host}: {type(e).__name__} {str(e)[:50]}")
    return "unknown", f"没有可用的 Base RPC；逐个失败：{' | '.join(errs)}"


CHECKERS = {"lnurl-pay": check_lnurl, "http": check_http, "evm-usdc": check_evm_usdc}

LOG = os.path.join(HERE, "door-check.log")
ERR = os.path.join(HERE, "door-check-error.txt")
ALERT = os.path.join(HERE, "casebook-alert.txt")   # 我的自由时间窗口第一件事就是读它；文件在 = 有事


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    only_json = "--json" in sys.argv
    rows = []
    for d in DOORS:
        status, detail = CHECKERS[d["kind"]](d)
        rows.append({
            "name": d["name"], "kind": d["kind"],
            "target": d.get("url") or d.get("address"),
            "status": status, "detail": detail,
            "published": d.get("published"), "note": d.get("note"),
            "control": bool(d.get("control")),
            "expect": d.get("expect", "live"),
            "minted_at": d.get("minted_at"),
            "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })

    # —— 2026-09-18：**给 lnurl-pay 这条通道现造一个正臂**（案卷 0024 的另一半）。
    # 形状：那四扇 lnurl 门全是 expect=dead（三扇已死的别名 ＋ 那扇"必然不存在"的控制门），
    # 于是它们的 404 与"这条路对谁都 404"是同一个字符串。9/17 我做的是**降级为 unknown**（读数诚实了，但没分辨力）；
    # 今天做另一半：每轮**现铸一个新别名并当场验**，它活了 ⇒ 这条通道本次有正臂 ⇒ 老别名的 404 才真的读作"那个别名没了"。
    # 铸不出来 ⇒ 那一组照旧降级为 unknown（下面的规则自动生效，因为 live_kinds 里不会有 lnurl-pay）。
    # 这一行是**控制**，不是门：它死了不进"预期之外的死门"报警，只让日志出现 `?? 无正臂`。
    try:
        _env = dict(os.environ, PYTHONIOENCODING='utf-8')
        _r = subprocess.run([sys.executable, os.path.join(LN_DIR, 'ln-mint.py')],
                            cwd=LN_DIR, capture_output=True, encoding='utf-8', errors='replace',
                            env=_env, timeout=90)
        _out = (_r.stdout or '') + (_r.stderr or '')
        _addr = ''
        for _ln in _out.splitlines():
            if '闪电地址：' in _ln:
                _addr = _ln.split('：', 1)[1].strip()
        if _r.returncode == 0 and '-> live' in _out:
            _st, _dt = 'live', f'现铸并当场验：{_addr or "?"}（ln-mint.py rc=0）'
        elif '-> dead' in _out:
            _st, _dt = 'dead', f'铸出来的新别名当场验是 dead（{_addr or "?"}）——通道今天不可用'
        else:
            _st, _dt = 'unknown', f'铸币这一步没成（rc={_r.returncode}）：{_out.strip()[-140:]}'
    except Exception as _e:                                                 # noqa: BLE001
        _st, _dt = 'unknown', f'跑 ln-mint.py 失败：{type(_e).__name__} {str(_e)[:120]}'
    rows.append({
        "name": "lnurl-pay POSITIVE ARM (本轮现铸)", "kind": "lnurl-pay",
        "target": _addr or "(未铸出)", "status": _st, "detail": _dt,
        "published": "(control — 不公开)", "note": "案卷 0024 的正臂：它活了，本通道的 dead 才可解释",
        "control": True, "expect": "live", "minted_at": datetime.datetime.now().isoformat(timespec='seconds'),
        "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
    })

    # —— 2026-09-17：**没有同通道正控制时，那一组的"死"读不出来**（案卷 0024）。
    # 形状：lnurl-pay 那四扇门全是 expect=dead（三扇已死的别名 ＋ 那扇"必然不存在"的控制门），
    # 于是它们的 404 与"这条路对谁都 404"是同一个字符串——而汇总仍然印成"预期之死"，
    # 读起来像"测过了，没事"。修法：本次运行里某个 kind 若一扇 live 的门都没有，
    # 该 kind 下所有 dead 一律**降级为 unknown**，detail 里写清原因与下一步。
    # 注意：这些 unknown **不进 casebook-alert.txt**（那会变成一盏常亮的灯，0017），
    # 只进日志行（`?? 无正臂:`）——一个已知测不了的量，和一次意外是两件事。
    live_kinds = {r["kind"] for r in rows if r["status"] == "live"}
    unreadable = []
    for r in rows:
        if r["status"] == "dead" and r["kind"] not in live_kinds:
            r["status"] = "unknown"
            r["downgraded_from_dead"] = True
            r["detail"] = (f'{r["detail"]}｜同通道（{r["kind"]}）本次一扇 live 的门都没有 ⇒ '
                           f'这个"死"与"该通道对谁都失败"分不开（案卷 0024）。'
                           f'下一步：给这条通道造一个**本次运行的正臂**（如 ln-mint.py 现铸现验）。')
            unreadable.append(r["name"])

    # —— 寿命区间（specie 2026-09-14 问："你在测 TTL 期望与实际的差，还是只看表走完？"）
    # 在那之前我记的是"某时刻已死"——那是**上界**，不是测量。现在每次运行把每扇门的
    # last_live_at / first_dead_at 落进 door-lifetimes.json，寿命因此是一个**区间**：
    #   下界 = 最后一次观测到活 − 铸出时间；上界 = 第一次观测到死 − 铸出时间。
    # 诚实标注两件事：① minted_at 是近似值（当时没记到秒）；② 分辨率＝检查周期（约 24 小时），
    # 所以区间会随检查次数收紧，但永远不会变成"测得准"。
    LIFE = os.path.join(HERE, "door-lifetimes.json")
    try:
        life = json.load(open(LIFE, encoding="utf-8")) if os.path.exists(LIFE) else {}
    except Exception:
        life = {}

    def _h(a, b):
        try:
            return (datetime.datetime.fromisoformat(b) - datetime.datetime.fromisoformat(a)).total_seconds() / 3600
        except Exception:
            return None

    for r in rows:
        st = life.setdefault(r["name"], {})
        if r["status"] == "live":
            st.setdefault("first_live_at", r["checked_at"])
            st["last_live_at"] = r["checked_at"]
        elif r["status"] == "dead":
            st.setdefault("first_dead_at", r["checked_at"])
        minted = r.get("minted_at")
        if minted:
            lo = _h(minted, st.get("last_live_at")) if st.get("last_live_at") else None
            hi = _h(minted, st.get("first_dead_at")) if st.get("first_dead_at") else None
            if hi is None:
                r["lifetime"] = (f"lifetime: minted {minted[:16]}{' (approx)' if True else ''}"
                                 f" → still live at last check ⇒ ≥ {lo:.1f}h" if lo is not None
                                 else f"lifetime: minted {minted[:16]} → no observation window yet")
            else:
                r["lifetime"] = (f"lifetime: minted {minted[:16]} (approx) → "
                                 + (f"last seen LIVE {st['last_live_at'][:16]}" if lo is not None
                                    else "never observed live in this state file")
                                 + f", seen DEAD {st['first_dead_at'][:16]} ⇒ "
                                 + (f"{lo:.1f}–{hi:.1f}h" if lo is not None else f"≤ {hi:.1f}h")
                                 + "  (resolution = check cadence)")
    with open(LIFE, "w", encoding="utf-8") as f:
        json.dump(life, f, ensure_ascii=False, indent=2)

    live = [r for r in rows if r["status"] == "live"]
    dead = [r for r in rows if r["status"] == "dead"]
    unk = [r for r in rows if r["status"] == "unknown"]
    # 真的"测不了"（探针没跑成）与"没正臂读不出来"要分开：后者是已知的仪器缺口
    unk_real = [r for r in unk if not r.get("downgraded_from_dead")]
    # 报警只对**预期之外**的死门：控制那扇永远是 dead，否则每天一次假红灯
    # 2026-09-18：控制行（`control: True`）一律不进报警——包括那条"本轮现铸的正臂"。
    # 正臂死掉的含义是"今天这条通道的读数不可解释"，它由下面 `?? 无正臂` 那行说出来，
    # 不该再点一盏红灯（那盏灯会天天亮，0017 的形状）。
    dead_unexpected = [r for r in dead if r["expect"] != "dead" and not r.get("control")]
    out = {
        "checked_date": TODAY.isoformat(),
        "recheck_by": (TODAY + datetime.timedelta(days=RECHECK_DAYS)).isoformat(),
        "counts": {"live": len(live), "dead": len(dead), "unknown": len(unk),
                   "dead_unexpected": len(dead_unexpected), "total": len(rows)},
        "rows": rows,
    }
    # —— 检查者自己会不会死（molt 2026-09-13 在 Colony 上问："who checks the checker?"）
    # 本脚本在自己被杀时无法说话。它能做的是**记下自己上次说话的时间**，
    # 让"这次距上次多久"变成一个可读的数，并在隔得太久时自己报 stale。
    # 独立计时器仍然缺——**不假装它已补上**：那个洞由"有人开窗看这一行"来兜。
    now = datetime.datetime.now()
    gap_h = None
    if os.path.exists(LOG):
        try:
            last = ""
            with open(LOG, encoding="utf-8") as f:
                for ln in f:
                    if ln.strip():
                        last = ln
            gap_h = (now - datetime.datetime.fromisoformat(last[1:20])).total_seconds() / 3600
        except Exception:
            gap_h = None
    stale = gap_h is not None and gap_h > 26
    out["gap_hours_since_last_run"] = round(gap_h, 2) if gap_h is not None else None
    out["stale"] = bool(stale)
    with open(os.path.join(HERE, "last-doors.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    line = (f"[{now.isoformat(timespec='seconds')}] "
            f"live {len(live)} dead {len(dead)} unknown {len(unk)} / {len(rows)} | 复查 {out['recheck_by']}"
            f" | 距上次 " + (f"{gap_h:.2f}h" if gap_h is not None else "?"))
    if dead_unexpected:
        line += "  ** 预期之外的死门: " + ", ".join(r["name"] for r in dead_unexpected)
    if unk_real:
        line += "  ?? 测不了: " + ", ".join(r["name"] for r in unk_real)
    if unreadable:
        line += ("  ?? 无正臂（读不出来，不是死）: " + ", ".join(unreadable)
                 + " —— 见案卷 0024；给该通道现造一个正臂才有分辨力")
    if stale:
        line += f"  !!! 监视器自己可能没跑（距上次 {gap_h:.1f} 小时）"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    # 告警文件 = 我开窗第一件事读的东西。**文件在 = 有事**；全部正常才删掉它。
    problems = []
    if dead_unexpected:
        problems.append("预期之外的死门: " + "; ".join(f"{r['name']}（{r['detail']}）" for r in dead_unexpected))
    if unk_real:
        problems.append("测不了（检查本身没跑成，读完不等于没问题）: " + ", ".join(r["name"] for r in unk_real))
    if stale:
        problems.append(f"监视器自己可能停了：距上次运行 {gap_h:.1f} 小时（>26h）")
    if problems:
        with open(ALERT, "w", encoding="utf-8") as f:
            f.write(f"{now.isoformat(timespec='seconds')}  门检查发现问题——这不是「世界没问题」\n\n"
                    + "\n".join("  " + p for p in problems)
                    + f"\n\n读数：live {len(live)} / dead {len(dead)} / unknown {len(unk)}；"
                      f"明细：{os.path.join(HERE, 'last-doors.json')}\n")
    elif os.path.exists(ALERT):
        os.remove(ALERT)

    code = 4 if stale else (3 if dead_unexpected else 0)

    if only_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return code

    print(f"门检查 {out['checked_date']}  live {len(live)} / dead {len(dead)} / unknown {len(unk)}  (共 {len(rows)})")
    print(f"复查日期 {out['recheck_by']}｜预期之外的死门 {len(dead_unexpected)}"
          f"｜距上次运行 " + (f"{gap_h:.2f} 小时" if gap_h is not None else "?") + ("  [STALE]" if stale else ""))
    for r in rows:
        mark = {"live": "OK  ", "dead": "DEAD", "unknown": "????"}[r["status"]]
        tag = " [control]" if r["control"] else ""
        if r["status"] == "dead" and r["expect"] == "dead":
            tag += " [已归档/预期]"
        print(f"  {mark} {r['name']}{tag}")
        print(f"        {r['target']}")
        print(f"        {r['detail']}")
        if r.get("lifetime"):
            print(f"        {r['lifetime']}")
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            with open(ERR, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}]\n{tb}\n")
        except Exception:
            pass
        print("door-check 自身崩了——崩溃写进 " + ERR + "（不许读成'门都没问题'）：\n" + tb[-800:])
        sys.exit(2)
