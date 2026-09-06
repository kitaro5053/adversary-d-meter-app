# -*- coding: utf-8 -*-
"""B-227 A/B ドライバ：鉄則①「資格の帯」を両ベンチで比較する。

`arena/b225_ab.py` と同じ思想＝クラス属性を実行時に退避→復元してベンチを回すだけ。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm`（B-218 教訓）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b227_ab --days 3 \
        --conds off,i015b225,band015b225 --perm id --out docs/仮_b227_log/ab_d3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agents import HeuristicProtagonist as HP

_BAND = {"B100_IRON_PROB": 0.15, "B227_IRON_BAND": True}
_B225 = {"B225_REPEAT_AWARE_BREAK": True}
#: B-226/B-228 の B+C+D（完全体）＝帰還時裁定の最終推薦セット。
_BCD = {"B222_FERRY_GEOMETRY": True, "B224_COOL_FLOOR": True,
        "B221_BREAKER": True, "B224_WEAK_MATCH": True}

#: 条件名 → 上書きするクラス属性。
CONDS: dict[str, dict] = {
    "off": {},
    # ---- 対照（B-224/B-225 の既存条件の再現） ----
    "iron015":  {"B100_IRON_PROB": 0.15},
    "i015b225": {"B100_IRON_PROB": 0.15, **_B225},
    # ---- 本語彙 ----
    "bandonly":    {"B227_IRON_BAND": True},          # iron None＝no-op（bit 不変の確認）
    "band015":     dict(_BAND),                        # 帯のみ（B-225 なし＝ablation）
    "band015b225": {**_BAND, **_B225},                 # ★主形（設計どおり B-225 と併用）
    # ---- 掃引（帯の下端 p・帯の幅＝設置札集合） ----
    "band03b225":   {"B100_IRON_PROB": 0.3, "B227_IRON_BAND": True, **_B225},
    "band015b225ng": {**_BAND, **_B225,
                      "B227_GUARD_CARDS": ("暗躍禁止", "移動禁止")},  # 不安-1 抜き
    # ---- フルセット共存（B+C+D＋B-225＋本語彙＝ON セット候補の本丸） ----
    "bcd":      dict(_BCD),
    "bcdp":     {**_BCD, **_B225},
    "bcdpband": {**_BCD, **_B225, **_BAND},
}

#: 退避対象（CONDS が触りうる属性の全集合）。
_ATTRS = ("B100_IRON_PROB", "B227_IRON_BAND", "B227_GUARD_CARDS",
          "B225_REPEAT_AWARE_BREAK", "B225_REP_MIN", "B224_IRON_REP_MIN",
          "B222_FERRY_GEOMETRY", "B224_COOL_FLOOR",
          "B221_BREAKER", "B224_WEAK_MATCH", "B224_WEAK_EXCLUDE_PIN")


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
    ap.add_argument("--conds", default="off,i015b225,band015b225")
    ap.add_argument("--base", default="off",
                    help="flip の基準条件（フルセット共存では bcd を使う）")
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
