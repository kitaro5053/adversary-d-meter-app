# -*- coding: utf-8 -*-
"""B-225 A/B ドライバ：折り手選択の是正（先払い＋選好）を両ベンチで比較する。

`arena/b224_ab.py` と同じ思想＝クラス属性を実行時に退避→復元してベンチを回すだけ。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm`（B-218 教訓）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b225_ab --days 3 \
        --conds off,b225,i015b225 --perm id --out docs/仮_b225_log/ab_d3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agents import HeuristicProtagonist as HP

#: 条件名 → 上書きするクラス属性。
CONDS: dict[str, dict] = {
    "off": {},
    # ---- 本語彙単独（鉄則なし＝θ/当夜×帯の資格で発火した席にだけ効く） ----
    "b225": {"B225_REPEAT_AWARE_BREAK": True},
    # ---- 鉄則①（B-224① の資格側配線）との組＝BTX#12 の的2 の形 ----
    "iron015":  {"B100_IRON_PROB": 0.15},
    "i015b225": {"B100_IRON_PROB": 0.15, "B225_REPEAT_AWARE_BREAK": True},
    "i03b225":  {"B100_IRON_PROB": 0.3, "B225_REPEAT_AWARE_BREAK": True},
    # ---- 掃引（優先の強さ＝要求する反復敗北数） ----
    "i015b225r2": {"B100_IRON_PROB": 0.15, "B225_REPEAT_AWARE_BREAK": True,
                   "B225_REP_MIN": 2},
    "i015b225r3": {"B100_IRON_PROB": 0.15, "B225_REPEAT_AWARE_BREAK": True,
                   "B225_REP_MIN": 3},
    # 鉄則の資格側にも深い反復を要求する組（B-224①の iron015r2 の後継形）
    "i015ir2b225": {"B100_IRON_PROB": 0.15, "B224_IRON_REP_MIN": 2,
                    "B225_REPEAT_AWARE_BREAK": True},
    # ---- B-226 推薦セット B+C との共存確認 ----
    "bc":   {"B222_FERRY_GEOMETRY": True, "B224_COOL_FLOOR": True},
    "bcp":  {"B222_FERRY_GEOMETRY": True, "B224_COOL_FLOOR": True,
             "B225_REPEAT_AWARE_BREAK": True},
    "bcpi": {"B222_FERRY_GEOMETRY": True, "B224_COOL_FLOOR": True,
             "B225_REPEAT_AWARE_BREAK": True, "B100_IRON_PROB": 0.15},
}

#: 退避対象（CONDS が触りうる属性の全集合）。
_ATTRS = ("B225_REPEAT_AWARE_BREAK", "B225_REP_MIN", "B100_IRON_PROB",
          "B224_IRON_REP_MIN", "B222_FERRY_GEOMETRY", "B224_COOL_FLOOR")


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


def main(argv=None) -> int:
    from arena.b224_ab import flips
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,b225,i015b225")
    ap.add_argument("--base", default="off",
                    help="flip の基準条件（共存確認では bc を使う）")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    conds = a.conds.split(",")
    res = {c: run_cond(c, a.days, a.loops, a.perm) for c in conds}
    base = res.get(a.base)
    if base is not None:
        for c in conds:
            if c == a.base:
                continue
            fl = flips(base, res[c])
            print(f"  flips {a.base}→{c}（{len(fl)}件）:")
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
