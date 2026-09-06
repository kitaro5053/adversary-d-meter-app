# -*- coding: utf-8 -*-
"""B-246：`arena.b246_probe census --json` の出力2本を per-game で突き合わせる。

規約 §5「per-game 差分（flip）で判定する」／§7 主人公側ゲート「per-game 差分の退行ゼロ」
を字義で確認するための道具。集計値（防衛数・平均）は入れ替わりを隠す。

    PYTHONIOENCODING=utf-8 python -m arena.b246_diff OFF.json ON.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter

KEYS = ("esc_seat", "esc_partial", "esc_tie_better", "esc_any_better",
        "esc_here_ge", "esc_pull_better", "yose_seat", "yose_bad",
        "yose_tie_clean", "yose_any_clean")


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    if len(argv) < 2:
        print("usage: python -m arena.b246_diff OFF.json ON.json")
        return 2
    a = json.load(open(argv[0], encoding="utf-8"))
    b = json.load(open(argv[1], encoding="utf-8"))
    ba, bb = a["bench"], b["bench"]
    keys = sorted(set(ba) | set(bb))
    flips = []
    for k in keys:
        va, vb = ba.get(k), bb.get(k)
        if va != vb:
            flips.append((k, va, vb))
    n_def_a = sum(1 for v in ba.values() if v[1] == "defense")
    n_def_b = sum(1 for v in bb.values() if v[1] == "defense")
    mean_a = round(sum(v[0] for v in ba.values()) / len(ba), 3)
    mean_b = round(sum(v[0] for v in bb.values()) / len(bb), 3)
    print(f"防衛 {n_def_a} → {n_def_b} ／ 平均 {mean_a} → {mean_b} "
          f"／ ★per-game flip = {len(flips)}")
    worse = better = 0
    for k, va, vb in flips:
        mark = "改善" if vb[0] < va[0] else "★退行"
        if vb[0] < va[0]:
            better += 1
        else:
            worse += 1
        print(f"  {mark} {k}: {va} → {vb}")
    if flips:
        print(f"  [flip 内訳] 改善={better} ★退行={worse}")
    print("[指標の差分]")
    ta, tb = a["total"], b["total"]
    for k in KEYS:
        if ta.get(k, 0) or tb.get(k, 0):
            print(f"  {k}: {ta.get(k, 0)} → {tb.get(k, 0)} "
                  f"(Δ={tb.get(k, 0) - ta.get(k, 0)})")
    dg = b.get("diag") or []
    if dg:
        print(f"[振り替えた席] {dict(Counter(d['kind'] for d in dg))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
