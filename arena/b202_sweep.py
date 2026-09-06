# -*- coding: utf-8 -*-
"""B-202 の優先度掃引（読み取り専用・PRIORITY を実行時に差し替えて両ベンチを回す）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b202_sweep --days 3 \
        --values off,45,60,70,74,76.5,90
"""
from __future__ import annotations

import argparse

import agents.heuristic_protagonist as hpm
from arena.benchmark import benchmark_scripts, loops_to_win


def _run(days: int) -> dict:
    out = {}
    for name, seed, sc in benchmark_scripts(days=days):
        n, end = loops_to_win(sc, seed, loops=8)
        out[f"{name}#{seed}"] = (n, end)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--values", default="off,45,60,70,74,76.5,90")
    a = ap.parse_args(argv)
    base = None
    for tok in a.values.split(","):
        tok = tok.strip()
        if tok == "off":
            hpm.HeuristicProtagonist.B202_UNREST_SUPPLY_SEP = False
        else:
            hpm.HeuristicProtagonist.B202_UNREST_SUPPLY_SEP = True
            hpm.PRIORITY["不安供給分離_受け手"] = float(tok)
        res = _run(a.days)
        n_def = sum(1 for n, e in res.values() if e == "defense")
        avg = sum(min(n, 9) for n, _ in res.values()) / len(res)
        if base is None:
            base = res
            flips = []
        else:
            flips = [f"{k}: {base[k]}→{v}" for k, v in res.items() if base[k] != v]
        print(f"[{tok:>6}] 防衛={n_def} 平均={avg:.3f} flip={flips or 'なし'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
