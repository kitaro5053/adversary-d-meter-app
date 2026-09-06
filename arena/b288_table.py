# -*- coding: utf-8 -*-
"""B-288 の判定表（`arena/b288_results/*.json` を読むだけ・対局は走らせない）。

物差し＝§72-104 の3層＋番人：
  第1層＝ループ間の再置席（N→N+1）・累積・再封
  第2層＝脚本家が取ったループ総数 Σ(min(ltw,9)−1)  ← ゲートの主
  第3層＝perm 条件での符号一致
  番人＝複線演出の非decoy（no_contribution − designed_decoy）と mm_lint D1〜D6

★4日級は perm `rot1` が `id` と結果一致＝独立条件でない（§72-122）＝id/rev/h1 の3本で見る。

CLI:
    PYTHONIOENCODING=utf-8 python -m arena.b288_table --days 3 \
        --perms id,rev,h1,rot1 --base off --cands ab,abg
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

from arena.b287_d4_table import flips, stats

RESULTS = os.path.join(os.path.dirname(__file__), "b288_results")


def load(days: int, perm: str, mode: str) -> dict:
    with open(os.path.join(RESULTS, f"d{days}_{perm}_{mode}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--perms", type=str, default="id,rev,h1,rot1")
    ap.add_argument("--base", type=str, default="off")
    ap.add_argument("--cands", type=str, default="ab,abg")
    args = ap.parse_args(argv)
    perms = args.perms.split(",")
    cands = args.cands.split(",")

    print("=" * 112)
    print(f"{'条件':<12}{'形':<7}{'第1層(N→N+1)':>13}{'累積':>6}{'再封':>6}"
          f"{'Σmm':>6}{'Δ':>5}{'平均ltw':>9}{'def(参考)':>10}"
          f"{'D1':>4}{'D2':>4}{'非decoy':>8}{'C279':>6}")
    tot = {m: 0 for m in [args.base] + cands}
    signs = {m: [] for m in cands}
    for perm in perms:
        base = stats(load(args.days, perm, args.base))
        for tag in [args.base] + cands:
            s = stats(load(args.days, perm, tag))
            d = s["l2"] - base["l2"]
            tot[tag] += s["l2"]
            if tag in signs:
                signs[tag].append(d)
            print(f"d{args.days}_{perm:<9}{tag:<7}{s['l1_prev']:>13}"
                  f"{s['l1_cum']:>6}{s['blocked_again']:>6}{s['l2']:>6}"
                  f"{d:>+5}{s['mean']:>9.3f}{s['oc'].get('defense', 0):>10}"
                  f"{s['lint'].get('D1', 0):>4}{s['lint'].get('D2', 0):>4}"
                  f"{s['non_decoy']:>8}{s['c279']:>6}")
        print("-" * 112)
    print(f"Σ第2層  {args.base}={tot[args.base]}")
    for m in cands:
        sg = signs[m]
        ok = ("YES(全正)" if all(x > 0 for x in sg)
              else ("全負" if all(x < 0 for x in sg)
                    else ("ゼロ含み" if all(x >= 0 for x in sg) else "NO(混在)")))
        print(f"  {m}: Σ={tot[m]}  Δ合計={tot[m] - tot[args.base]:+d}  "
              f"perm別Δ={sg}  第3層符号一致={ok}")
    print()
    print("== 番人（複線演出の非decoy／mm_lint）==")
    for perm in perms:
        for tag in [args.base] + cands:
            s = stats(load(args.days, perm, tag))
            print(f"  d{args.days}_{perm} {tag}: 非decoy={s['non_decoy']}"
                  f" ボード暗躍{s['board_anyaku']}席/寄与なし{s['no_contrib']}"
                  f"(ダミー{s['designed_decoy']})"
                  f" lint={dict(sorted(s['lint'].items()))}"
                  f" 結末={dict(sorted(s['oc'].items()))}")
    print()
    print(f"== flip 全数（{args.base}→各形）==")
    for perm in perms:
        for tag in cands:
            fl = flips(load(args.days, perm, args.base),
                       load(args.days, perm, tag))
            txt = "  ".join(f"{a}#{b}:{c}({d[:4]})→{e}({f[:4]})[{g:+d}]"
                            for a, b, c, d, e, f, g in fl)
            print(f"d{args.days}_{perm} {tag}: {txt if txt else '(flip なし)'}")
    print()
    print("== 形どうしの per-game 一致（bit 同値の検問）==")
    for perm in perms:
        base_g = {(g["script"], g["seed"]): (g["loops_to_win"], g["outcome"])
                  for g in load(args.days, perm, args.base)["games"]}
        for tag in cands:
            g2 = {(g["script"], g["seed"]): (g["loops_to_win"], g["outcome"])
                  for g in load(args.days, perm, tag)["games"]}
            n = sum(1 for k in base_g if base_g[k] != g2.get(k))
            print(f"  d{args.days}_{perm} {args.base} vs {tag}: 帰結の違う局 = {n}")


if __name__ == "__main__":
    main()
