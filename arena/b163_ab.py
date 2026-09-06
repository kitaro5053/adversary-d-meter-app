# -*- coding: utf-8 -*-
"""B-163 Phase 2：`HeuristicProtagonist.B163_KURO_IN_CAST` の A/B（切替口＝bool・既定 False）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**
  （作りは `arena/b165_ab.py` と同型）。
- per-game 差分（flip）を**全数**出す（規約 §5）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b163_ab --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b163_ab --days 5
"""

from __future__ import annotations

import argparse

import agents.defense_plan as dp
from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _switch_line(days: int) -> str:
    return (f"  [切替口] ★B163_KURO_IN_CAST 実効="
            f"{HeuristicProtagonist.B163_KURO_IN_CAST}"
            f" ／ B137_DEFEAT_CAPABLE_ONLY={HeuristicProtagonist.B137_DEFEAT_CAPABLE_ONLY}"
            f" ／ B138_ODB_BOARD_LOSS_ONLY={HeuristicProtagonist.B138_ODB_BOARD_LOSS_ONLY}"
            f" ／ B141_COOLER_VALUE_FUTURE_ONLY="
            f"{HeuristicProtagonist.B141_COOLER_VALUE_FUTURE_ONLY}"
            f" ／ B153_JUUSHA_PAIR_BREAK={dp.B153_JUUSHA_PAIR_BREAK}"
            f" ／ B165_PAIR_BREAK={dp.B165_PAIR_BREAK}"
            f" ／ DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" ／ B100_MIX={HeuristicProtagonist.B100_MIX}"
            f" ／ B100_THETA={HeuristicProtagonist.B100_THETA}"
            f" ／ days={days}")


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない（`outcomes["defense"]` が防衛数）。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def _run(days: int, loops: int, on: bool) -> dict:
    old = HeuristicProtagonist.B163_KURO_IN_CAST
    HeuristicProtagonist.B163_KURO_IN_CAST = on
    print(_switch_line(days), flush=True)
    try:
        return run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B163_KURO_IN_CAST = old


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-163 A/B")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    a = ap.parse_args(argv)

    base = None
    for on in (False, True):
        rep = _run(a.days, a.loops, on)
        rows = {(r["script"], r["seed"]): r["loops_to_win"] for r in rep["rows"]}
        line = f"  B163_KURO_IN_CAST={on}: {_summary(rep)}"
        if base is None:
            base = rows
        else:
            flips = [(k, base[k], rows[k]) for k in rows if base[k] != rows[k]]
            imp = [f for f in flips if f[2] < f[1]]
            reg = [f for f in flips if f[2] > f[1]]
            line += f"  ★flip={len(flips)}（改善{len(imp)}／退行{len(reg)}）"
            for k, b, c in sorted(flips):
                line += f"\n      {k[0]} s{k[1]}: {b} → {c}"
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
