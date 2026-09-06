# -*- coding: utf-8 -*-
"""B-284：副作用の番人（計測専用）。

§72-104 の作法どおり、第1層の数え上げに**取引を隠さない**ための対照を取る：
  - `arena.mm_lint` の D1〜D6（規則上そのループで効果を持ち得ない手）
  - `arena.b279_probe` (a) の「勝利条件に寄与しないボード暗躍」のうち
    **設計上のダミー配置（複線演出）でない席**

切替口（既定 OFF）を立てた状態と OFF の両方で数え、増えていないことを確認する。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b284_guard --flag off
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b284_guard --flag exact
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

_FLAGS = {
    "off": {},
    "exact": {"B284_SUPPLY_EXACT": True},
    "flat1": {"B284_SUPPLY_FLAT1": True},
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--flag", default="off", choices=sorted(_FLAGS))
    ap.add_argument("--days", type=int, default=0, help="0＝3日と5日の両方")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    from agents.heuristic import HeuristicMastermind
    for k in ("B284_SUPPLY_EXACT", "B284_SUPPLY_FLAT1", "B284_LIVENESS_BREAK"):
        setattr(HeuristicMastermind, k, False)
    for k, v in _FLAGS[args.flag].items():
        setattr(HeuristicMastermind, k, v)
    print("切替口: " + " ".join(
        f"{k}={getattr(HeuristicMastermind, k)}"
        for k in ("B284_SUPPLY_EXACT", "B284_SUPPLY_FLAT1")))

    days_list = (3, 5) if args.days == 0 else (args.days,)

    from arena.mm_lint import sweep as lint_sweep
    vios = lint_sweep(days_list=days_list)
    lint = Counter(v.detector for v in vios)
    print("mm_lint: " + "  ".join(f"{d}:{lint.get(d, 0)}"
                                  for d in ("D1", "D2", "D3", "D4", "D5", "D6")))

    from arena.benchmark import benchmark_scripts
    from arena.b279_probe import probe_game as b279_game
    a_tot = Counter()
    for days in days_list:
        for name, seed, sc in benchmark_scripts(days=days):
            rep = b279_game(name, seed, sc, days)
            for row in rep["a"]:
                if row["cls"] == "win_condition":
                    a_tot[f"d{days}_win"] += 1
                elif row.get("is_designed_decoy"):
                    a_tot[f"d{days}_decoy"] += 1
                else:
                    a_tot[f"d{days}_other"] += 1
    print("寄与しないボード暗躍: " + "  ".join(
        f"{k}:{v}" for k, v in sorted(a_tot.items())))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"flag": args.flag, "lint": dict(lint),
                       "board_anyaku": dict(a_tot)}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
