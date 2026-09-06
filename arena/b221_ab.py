# -*- coding: utf-8 -*-
"""B-221 A/B ドライバ：反復非適応ブレーカの ON/OFF を両ベンチで比較する。

`arena/b211_ab.py` と同じ思想＝クラス属性を実行時に退避→復元してベンチを回すだけ
（ファイルは不変・測定は単独実行前提）。per-game flip を全数表示する。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b221_ab --days 3 \
        --conds off,cap8pair,cap8run --out docs/仮_b221_log/ab_d3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agents import HeuristicProtagonist as HP

#: 条件名 → (B221_BREAKER, B221_CAP, B221_REQUIRE_PAIR, B221_BLOCK_BREAKS)
CONDS = {
    "off": (False, 8.0, True, True),
    "cap8pair": (True, 8.0, True, True),    # 狭い側（反復の実測後にだけ発火）
    "cap8run": (True, 8.0, False, True),    # 広い側（棄却済み＝負の結果）
    "cap40pair": (True, 40.0, True, True),
    "cap75pair": (True, 75.0, True, True),
    "cap8pairS": (True, 8.0, True, False),  # ★採点 cap のみ（B-100 折り手遮断なし）
}


def run_cond(name: str, days: int, loops: int = 8) -> dict:
    from arena.benchmark import run_benchmark
    on, cap, pair, blk = CONDS[name]
    old = (HP.B221_BREAKER, HP.B221_CAP, HP.B221_REQUIRE_PAIR,
           HP.B221_BLOCK_BREAKS)
    (HP.B221_BREAKER, HP.B221_CAP, HP.B221_REQUIRE_PAIR,
     HP.B221_BLOCK_BREAKS) = on, cap, pair, blk
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        (HP.B221_BREAKER, HP.B221_CAP, HP.B221_REQUIRE_PAIR,
         HP.B221_BLOCK_BREAKS) = old
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"  [{days}日級] {name}: 防衛={n_def} 平均={rep['mean_loops_to_win']:.3f} "
          f"結末={rep['outcomes']}", flush=True)
    return rep


def flips(base: dict, other: dict) -> list[str]:
    rows_b = {f'{r["script"]}#{r["seed"]}': r for r in base["rows"]}
    out = []
    for r in other["rows"]:
        k = f'{r["script"]}#{r["seed"]}'
        b = rows_b[k]
        if (b["loops_to_win"], b["outcome"]) != (r["loops_to_win"], r["outcome"]):
            out.append(f'{k}: {b["loops_to_win"]}[{b["outcome"]}] → '
                       f'{r["loops_to_win"]}[{r["outcome"]}]')
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,cap8pair")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    conds = a.conds.split(",")
    res = {c: run_cond(c, a.days, a.loops) for c in conds}
    base = res.get("off")
    if base is not None:
        for c in conds:
            if c == "off":
                continue
            fl = flips(base, res[c])
            print(f"  flips off→{c}（{len(fl)}件）:")
            for f in fl:
                print(f"    {f}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({c: {"rows": r["rows"], "outcomes": r["outcomes"],
                           "mean": r["mean_loops_to_win"]}
                       for c, r in res.items()}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
