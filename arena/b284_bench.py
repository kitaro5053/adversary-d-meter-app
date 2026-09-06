# -*- coding: utf-8 -*-
"""B-284：切替口を立てた状態でベンチを回す薄いドライバ（計測専用）。

`arena.benchmark.run_benchmark` をそのまま呼ぶ（局の生成・進行はベンチと同一）。
違いは **`HeuristicMastermind` のクラス属性（既定 OFF の切替口）を立てるだけ**。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b284_bench \
        --flag off --days 3 --perm id --out /tmp/x.json

--flag: off / exact / flat1 / liveness
--perm: id / rev / h1 / rot1 ...（`arena.tie_noise.install_perm` の並べ替え条件）

第2層（脚本家が取ったループ総数）＝ `Σ(min(loops_to_win, 9) − 1)` も出す。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


_FLAGS = {
    "off": {},
    "exact": {"B284_SUPPLY_EXACT": True},
    "flat1": {"B284_SUPPLY_FLAT1": True},
    "liveness": {"B284_LIVENESS_BREAK": True},
}


def mm_loops(rep: dict) -> int:
    """第2層＝脚本家が取ったループ総数 Σ(min(ltw,9) − 1)。"""
    return sum(min(r["loops_to_win"], 9) - 1 for r in rep["rows"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--flag", default="off", choices=sorted(_FLAGS))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--set", action="append", default=[],
                    help="MM_PARAMS の上書き（key=value・複数可）")
    args = ap.parse_args(argv)

    if os.environ.get("PYTHONHASHSEED") != "0":
        print("⚠ PYTHONHASHSEED=0 で実行すること", file=sys.stderr)

    from agents.heuristic import HeuristicMastermind
    from arena.benchmark import run_benchmark, format_report

    for k in ("B284_SUPPLY_EXACT", "B284_SUPPLY_FLAT1", "B284_LIVENESS_BREAK"):
        setattr(HeuristicMastermind, k, False)
    for k, v in _FLAGS[args.flag].items():
        setattr(HeuristicMastermind, k, v)
    print("切替口: " + " ".join(
        f"{k}={getattr(HeuristicMastermind, k)}"
        for k in ("B284_SUPPLY_EXACT", "B284_SUPPLY_FLAT1", "B284_LIVENESS_BREAK")))

    mm_params = None
    if args.set:
        mm_params = {}
        for kv in args.set:
            k, _, v = kv.partition("=")
            mm_params[k.strip()] = float(v)
        print(f"mm_params 上書き: {mm_params}")

    uninstall = None
    if args.perm != "id":
        from arena.tie_noise import install_perm, uninstall_perm
        install_perm(args.perm)
        uninstall = uninstall_perm
    try:
        rep = run_benchmark(loops=args.loops, days=args.days,
                            verbose=not args.quiet, mm_params=mm_params)
    finally:
        if uninstall:
            uninstall()

    rep["b284_params"] = mm_params
    rep["b284_flag"] = args.flag
    rep["b284_perm"] = args.perm
    rep["mm_loops"] = mm_loops(rep)
    print(format_report(rep))
    print(f"  第2層 脚本家が取ったループ総数 Σ(min(ltw,9)−1) = {rep['mm_loops']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
