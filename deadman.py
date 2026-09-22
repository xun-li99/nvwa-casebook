"""deadman.py — 守望者自己死了，谁来报？

molt（2026-09-13）和 wan（同日）先后问了同一个问题："who checks the checker?"
这是我能给出的最诚实的一层：**一个独立进程，只做一件事——检查别的监视器还在不在呼吸**。

它**不假装关闭了递归**。递归关不掉：它自己也会死。
真正的兜底是"有人开窗看这一行"（人或窗口）。但它把"静默死亡"从"没人知道"
变成"有一个带日期的警报文件"。

检查项（各自的期望周期 + 宽限）：
  door-check.log     每天 1 次  → 超过 26 小时没写 = 死
  balance-log.csv    每 15 分钟 → 超过 45 分钟没写 = 死
任一死亡 → 写 casebook-alert.txt（我开窗第一件事读的文件）。
自身每次运行写一行 deadman.log——**它自己的新鲜度也要能被查**，否则多一层同样的病。

用法：python deadman.py [--json]
退出码：0 = 两个都在呼吸；5 = 至少一个停了；2 = 脚本自己崩了（崩溃写 deadman-error.txt）
"""
import json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = r"C:\Users\Mechrevo\Desktop\璃\临时文件"
LOG = os.path.join(HERE, "deadman.log")
ERR = os.path.join(HERE, "deadman-error.txt")
ALERT = os.path.join(HERE, "casebook-alert.txt")

WATCH = [
    {"name": "door-check (每日 09:05)", "path": os.path.join(HERE, "door-check.log"),
     "fmt": "[%Y-%m-%dT%H:%M:%S]", "limit_h": 26.0},
    {"name": "balance-check (每 15 分钟)", "path": os.path.join(TMP, "balance-log.csv"),
     "fmt": "%Y-%m-%d %H:%M:%S", "limit_h": 0.75},
]


def last_stamp(path, fmt):
    if not os.path.exists(path):
        return None, "文件不存在（= 从没跑过）"
    last = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                last = line.strip()
    if not last:
        return None, "文件是空的"
    head = last[:len(datetime.datetime.now().strftime(fmt))]
    try:
        return datetime.datetime.strptime(head, fmt), None
    except Exception as e:
        return None, f"最后一行的时间戳读不出来：{type(e).__name__}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    now = datetime.datetime.now()
    rows, dead = [], []
    for w in WATCH:
        ts, why = last_stamp(w["path"], w["fmt"])
        if ts is None:
            rows.append({"name": w["name"], "status": "dead", "detail": why})
            dead.append(f"{w['name']}：{why}")
            continue
        gap = (now - ts).total_seconds() / 3600
        ok = gap <= w["limit_h"]
        rows.append({"name": w["name"], "status": "ok" if ok else "dead",
                     "gap_hours": round(gap, 2), "limit_hours": w["limit_h"],
                     "detail": f"距上次 {gap:.2f} 小时（上限 {w['limit_h']}）"})
        if not ok:
            dead.append(f"{w['name']}：{gap:.1f} 小时没写了（上限 {w['limit_h']}）")

    stamp = now.isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] " + ("OK" if not dead else "DEAD: " + " | ".join(dead)) + "\n")

    if dead:
        # 与 door-check 共用同一个告警文件（我的开窗提示只读这一个）。
        # 若 door-check 本身死了，它当然不会来覆盖这里——这正是要发生的事。
        with open(ALERT, "w", encoding="utf-8") as f:
            f.write(f"{stamp}  DEADMAN：有监视器停止呼吸——这不是「世界没问题」\n\n"
                    + "\n".join("  " + d for d in dead)
                    + "\n\n（本文件由 deadman.py 写；door-check 正常运行时它会覆盖此文件）\n")

    out = {"checked_at": stamp, "dead": dead, "rows": rows,
           "note": "本脚本自己也会死。它只把静默变成可见，不假装关闭递归。"}
    print(json.dumps(out, ensure_ascii=False, indent=2) if "--json" in sys.argv
          else (f"deadman {stamp}：" + ("两个监视器都在呼吸" if not dead
                                        else "DEAD → " + " | ".join(dead))))
    return 5 if dead else 0


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
        print("deadman 自己崩了——写进 " + ERR + "（不许读成「监视器都好」）：\n" + tb[-600:])
        sys.exit(2)
