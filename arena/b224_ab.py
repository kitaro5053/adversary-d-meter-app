# -*- coding: utf-8 -*-
"""B-224 A/B ドライバ：3つの切替口の ON/OFF を両ベンチで比較する。

`arena/b221_ab.py` と同じ思想＝クラス属性を実行時に退避→復元してベンチを回すだけ
（ファイルは不変・測定は単独実行前提）。per-game flip を全数表示する。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm` で候補列を並べ替える
（B-218 教訓＝単一列挙順の一方向 flip は信号ではない）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b224_ab --days 3 \
        --conds off,iron015r3,cool103y8,weak8 --perm id --out docs/仮_b224_log/ab_d3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agents import HeuristicProtagonist as HP

#: 条件名 → 上書きするクラス属性（載っていない属性は既定値のまま）。
CONDS: dict[str, dict] = {
    "off": {},
    # ---- 切替口①＝鉄則①の再掃引（B100_IRON_PROB × B224_IRON_REP_MIN） ----
    "iron09":    {"B100_IRON_PROB": 0.9},
    "iron05":    {"B100_IRON_PROB": 0.5},
    "iron03":    {"B100_IRON_PROB": 0.3},
    "iron015":   {"B100_IRON_PROB": 0.15},
    "iron015r2": {"B100_IRON_PROB": 0.15, "B224_IRON_REP_MIN": 2},
    "iron015r3": {"B100_IRON_PROB": 0.15, "B224_IRON_REP_MIN": 3},
    "iron015r4": {"B100_IRON_PROB": 0.15, "B224_IRON_REP_MIN": 4},
    # ---- 切替口②＝敗因チャネル直結の加点側（floor × yield） ----
    "cool103y8":  {"B224_COOL_FLOOR": True},
    "cool103y40": {"B224_COOL_FLOOR": True, "B224_YIELD_CAP": 40.0},
    "cool76y8":   {"B224_COOL_FLOOR": True, "B224_FLOOR": 76.0},
    "cool186y8":  {"B224_COOL_FLOOR": True, "B224_FLOOR": 186.0},
    # 対照＝floor のみ（yield を実質無効化＝上限を十分高く）
    "cool103yoff": {"B224_COOL_FLOOR": True, "B224_YIELD_CAP": 1e9},
    # ---- 切替口③＝弱い一致定義（B-221 ブレーカの一致判定を置換） ----
    #   ★B-228 以降＝`B224_WEAK_EXCLUDE_PIN`（BTX#16 絞り）の既定が True のため
    #   "weak8" は絞りあり。B-224 当時の v2（絞りなし）＝"weak8x"。
    "weak8":  {"B221_BREAKER": True, "B224_WEAK_MATCH": True},
    "weak8x": {"B221_BREAKER": True, "B224_WEAK_MATCH": True,
               "B224_WEAK_EXCLUDE_PIN": False},
    "weak40": {"B221_BREAKER": True, "B224_WEAK_MATCH": True, "B221_CAP": 40.0},
    # 参考＝B-221 採用形（完全一致・cap8）
    "cap8pair": {"B221_BREAKER": True},
    # 併用（3切替口の干渉確認用）
    "all3": {"B100_IRON_PROB": 0.15, "B224_IRON_REP_MIN": 3,
             "B224_COOL_FLOOR": True,
             "B221_BREAKER": True, "B224_WEAK_MATCH": True},
}

#: 退避対象（CONDS が触りうる属性の全集合）。
_ATTRS = ("B100_IRON_PROB", "B224_IRON_REP_MIN", "B224_COOL_FLOOR",
          "B224_FLOOR", "B224_YIELD_CAP", "B221_BREAKER", "B224_WEAK_MATCH",
          "B224_WEAK_EXCLUDE_PIN",
          "B221_CAP", "B221_REQUIRE_PAIR", "B221_BLOCK_BREAKS")


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id") -> dict:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    old = {a: getattr(HP, a) for a in _ATTRS}
    for k, v in CONDS[name].items():
        setattr(HP, k, v)
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        uninstall_perm()
        for a, v in old.items():
            setattr(HP, a, v)
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"  [{days}日級 perm={perm}] {name}: 防衛={n_def} "
          f"平均={rep['mean_loops_to_win']:.3f} 結末={rep['outcomes']}",
          flush=True)
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
    ap.add_argument("--conds", default="off,iron015r3,cool103y8,weak8")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    conds = a.conds.split(",")
    res = {c: run_cond(c, a.days, a.loops, a.perm) for c in conds}
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
