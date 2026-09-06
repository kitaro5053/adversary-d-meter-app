# -*- coding: utf-8 -*-
"""B-287 の適用域計測（4日級）＝`arena/b287_probe.py` の JSON を突き合わせる表組み。

用途＝`docs/提案_4日級ベンチ_2026-08-31.md` 案A の判定表を作る（計測のみ・land しない）。
対照＝`cur5`（現行既定＝`B287_MIN_DAYS=5`＝4日級では日数ゲートが閉＝不発）と
      `on4`（同じ3口・`B287_MIN_DAYS=4`＝4日級へ広げた仮定）。

★本モジュールは JSON を読むだけ＝対局を走らせない（測定汚染なし）。

CLI:
    PYTHONIOENCODING=utf-8 python -m arena.b287_d4_table --days 4 \
        --perms id,rev,h1,rot1 --base cur5 --cand on4
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

RESULTS = os.path.join(os.path.dirname(__file__), "b287_results")


def load(days: int, perm: str, mode: str) -> dict:
    with open(os.path.join(RESULTS, f"d{days}_{perm}_{mode}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def stats(rep: dict) -> dict:
    games = rep["games"]
    rows = [r for g in games for r in g["replace_rows"]]
    prev_rows = [r for r in rows if r["from_prev_loop"]]
    oc = Counter(g["outcome"] for g in games)
    guard = Counter()
    lint = Counter()
    for g in games:
        guard.update(g["guard_a"])
        lint.update(g["lint"])
    return {
        "n": len(games),
        "l1_prev": len(prev_rows),
        "l1_cum": len(rows),
        "blocked_again": sum(1 for r in prev_rows if r["blocked_again"]),
        "l2": sum(min(g["loops_to_win"], 9) - 1 for g in games),
        "mean": sum(g["loops_to_win"] for g in games) / len(games),
        "oc": oc,
        "no_contrib": guard["no_contribution"],
        "designed_decoy": guard["designed_decoy"],
        "non_decoy": guard["no_contribution"] - guard["designed_decoy"],
        "board_anyaku": guard["board_anyaku"],
        "lint": lint,
        "c279": sum(g["c279_repeat_after_block"] for g in games),
    }


def flips(base: dict, cand: dict) -> list[tuple]:
    b = {(g["script"], g["seed"]): g for g in base["games"]}
    out = []
    for g in cand["games"]:
        k = (g["script"], g["seed"])
        o = b.get(k)
        if o is None:
            continue
        if (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"], g["outcome"]):
            out.append((k[0], k[1], o["loops_to_win"], o["outcome"],
                        g["loops_to_win"], g["outcome"],
                        min(g["loops_to_win"], 9) - min(o["loops_to_win"], 9)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--perms", type=str, default="id,rev,h1,rot1")
    ap.add_argument("--base", type=str, default="cur5")
    ap.add_argument("--cand", type=str, default="on4")
    args = ap.parse_args(argv)
    perms = args.perms.split(",")

    print("=" * 104)
    hdr = (f"{'条件':<12}{'形':<7}{'第1層(N→N+1)':>13}{'累積':>6}{'再封':>6}"
           f"{'Σmm':>6}{'Δ':>5}{'平均ltw':>9}{'def(参考)':>10}"
           f"{'D1':>4}{'D2':>4}{'非decoy':>8}{'C279':>6}")
    print(hdr)
    tot = {args.base: 0, args.cand: 0}
    signs = []
    for perm in perms:
        base = stats(load(args.days, perm, args.base))
        cand = stats(load(args.days, perm, args.cand))
        for tag, s in ((args.base, base), (args.cand, cand)):
            d = s["l2"] - base["l2"]
            tot[tag] += s["l2"]
            print(f"d{args.days}_{perm:<9}{tag:<7}{s['l1_prev']:>13}"
                  f"{s['l1_cum']:>6}{s['blocked_again']:>6}{s['l2']:>6}"
                  f"{d:>+5}{s['mean']:>9.3f}{s['oc'].get('defense', 0):>10}"
                  f"{s['lint'].get('D1', 0):>4}{s['lint'].get('D2', 0):>4}"
                  f"{s['non_decoy']:>8}{s['c279']:>6}")
        signs.append(cand["l2"] - base["l2"])
        print("-" * 104)
    print(f"Σ第2層  {args.base}={tot[args.base]}  {args.cand}={tot[args.cand]}  "
          f"Δ合計={tot[args.cand] - tot[args.base]:+d}  "
          f"perm別Δ={signs}  符号一致={'YES' if all(x > 0 for x in signs) else ('全負' if all(x < 0 for x in signs) else 'NO(混在)')}")
    print()
    print(f"== 結末の内訳 ==")
    for perm in perms:
        for tag in (args.base, args.cand):
            s = stats(load(args.days, perm, tag))
            print(f"  d{args.days}_{perm} {tag}: {dict(sorted(s['oc'].items()))}"
                  f"  ボード暗躍{s['board_anyaku']}席/寄与なし{s['no_contrib']}"
                  f"(ダミー配置{s['designed_decoy']})"
                  f"  lint={dict(sorted(s['lint'].items()))}")
    print()
    print(f"== flip 全数（{args.base}→{args.cand}）==")
    for perm in perms:
        fl = flips(load(args.days, perm, args.base),
                   load(args.days, perm, args.cand))
        s = "  ".join(f"{a}#{b}:{c}({d[:4]})→{e}({f[:4]})[{g:+d}]"
                      for a, b, c, d, e, f, g in fl)
        print(f"d{args.days}_{perm}: {s if s else '(flip なし)'}")


if __name__ == "__main__":
    main()
