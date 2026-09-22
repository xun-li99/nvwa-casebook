"""ts-domain-test.py — **金标准**测试：每个值必须落进它该落的桶（不是一次性判别器）。

来历：2026-09-14 我用 10 行夹具跑 autopsy，得到 `unparsable 0`，而按模型应当是 1。
逐值对照证明函数是对的——错的是 `.jsonl` 那条路径**根本没调用解析器**（案卷 0022）。
当时这个脚本只**打印**一张表，是一次性判别器。

2026-09-15，dantic 第 6 轮指出第二半：
> "The pinned set protects counts on clean input but cannot protect bucket behavior on bad data;
> if the script was throwaway, the only thing standing between a future parse_ts change and
> `"1e6"` silently becoming a fabricated date again is code review."

所以它现在是**金标准**：期望表写在文件里，任何一格的漂移都让退出码变 1。
**干净输入上的钉死集，保护不了坏数据上的分桶行为**——这两件事要两套测试。

用法：python ts-domain-test.py          # 一致 → 退出 0；任何一格漂移 → 退出 1
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("autopsy_under_test", os.path.join(HERE, "autopsy.py"))
A = importlib.util.module_from_spec(spec)
sys.modules["autopsy_under_test"] = A
spec.loader.exec_module(A)          # 模块级只有定义；main() 在 __main__ 下才跑

# (标签, 输入行, 期望的 ts_of 返回, 期望的桶)  —— 桶 None 表示"不该记任何桶"（成功解析）
GOLDEN = [
    ("ok-iso",         {"ts": "2026-09-14T00:00:00.000+00:00"}, "2026-09-14T00:00:00.000+00:00", None),
    ("missing-key",    {},                                     None,        "missing"),
    ("explicit-null",  {"ts": None},                           None,        "null"),
    ("epoch-zero",     {"ts": 0},                              None,        "zero"),
    ("epoch-ms-int",   {"ts": 1789166512304},                  "1789166512304", None),
    ("float-notation", {"ts": "1e6"},                          "1e6",       "unparsable"),
    ("blank-string",   {"ts": " "},                            None,        "null"),
    ("wrong-type-dict", {"ts": {}},                            None,        "wrong_type"),
    ("wrong-type-bool", {"ts": True},                          None,        "wrong_type"),
    ("epoch-sec-str",  {"ts": "1789166512"},                   "1789166512", None),
    ("all-digits-14",  {"ts": "17891665123041"},               "17891665123041", "unparsable"),
    ("plus-sign",      {"ts": "+1789166512"},                  "+1789166512", "unparsable"),
]


def main():
    print(f"{'case':18} {'ts_of() 返回':30} {'parse_ts() 结果':22} 桶")
    print("-" * 96)
    bad = []
    for label, item, want_ret, want_bucket in GOLDEN:
        before = dict(A.PARSE_FAULTS)
        t = A.ts_of(item)
        d = A.parse_ts(t) if t is not None else "(未调用)"
        delta = {k: A.PARSE_FAULTS[k] - before[k] for k in A.PARSE_FAULTS
                 if A.PARSE_FAULTS[k] - before[k] and k != 'calls'}   # 'calls' 每次都会动，不算桶
        got_bucket = next(iter(delta), None)
        ok = (t == want_ret) and (got_bucket == want_bucket)
        print(f"{label:18} {repr(t):30} {str(d):22} {delta if delta else '—'}"
              + ("" if ok else f"   ← 漂移！期望 ret={want_ret!r} bucket={want_bucket!r}"))
        if not ok:
            bad.append((label, t, want_ret, got_bucket, want_bucket))

    print()
    print("最终计数：", A.PARSE_FAULTS)
    if bad:
        print(f"\n✗ 金标准失败 {len(bad)} 格：")
        for label, got_r, want_r, got_b, want_b in bad:
            print(f"   {label}: ts_of {got_r!r}（期望 {want_r!r}）／桶 {got_b!r}（期望 {want_b!r}）")
        print("   ⇒ 分桶行为变了。**坏数据上的行为有变**，不许把它当成'只是重构'。")
        return 1
    print(f"\n✓ 金标准通过：{len(GOLDEN)} 个值的 ts_of 返回与分桶全部未漂移。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
