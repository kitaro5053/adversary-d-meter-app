# -*- coding: utf-8 -*-
"""主人公AIの「計算スコア係数」の4面ベンチ・スイープ（Bonapeti主人公版・軽量）。

tune_protagonist.py（PRIORITY表のCEM）は**汎化しなかった**（PRIORITYは0.5%差の
順位アンカーが仕様＝一様摂動は訓練脚本に過適合。docs/引き継ぎ§4c）。対して
**連続量の計算スコア係数**（危険度・救済ボーナス・部屋人口閾値等）は
ベンチ監督のスイープで過学習せず効く（実証：_VIP_RISK_ROOM_MAX 3→2 で gen4-5d +1）。

このツールはその手法を codify＝共進化ラウンドごとに主人公係数を素早く再最適化する。
- 対象＝HeuristicProtagonist の**クラス属性**係数（monkeypatch でスイープ）。
  インラインマジックナンバーは要抽出（クラス属性化）＝抽出するほど探索範囲が広がる。
- 目的＝4面（既定・強敵mm × 3日/5日）の防衛数。**非退行制約が本体**：
  どの面も基準を下回らない候補だけを「採用可」と表示する（片面改善・他面退行は不採用）。
- 決定性＝PYTHONHASHSEED=0（同点タイブレークのハッシュ順依存を固定）。

使い方（例）:
    # 単一係数のスイープ（既定 vs 最新gen）
    PYTHONHASHSEED=0 python -m arena.tune_coeffs \
        --coeff _VIP_RISK_ROOM_MAX --values 2,3,4 \
        --strong arena/params/mm_cem_gen4_20260709.json
    # 2係数のグリッド
    PYTHONHASHSEED=0 python -m arena.tune_coeffs \
        --coeff _VIP_SAFE_BONUS --values 16,22,30 \
        --coeff2 _VIP_RISK_ROOM_MAX --values2 2,3

出力：各候補の 既定3d/5d・強敵3d/5d 防衛数と、基準（現行係数値）比の増減。
★採用は人間判断（既定化＝コミット）。このツールは測定のみ。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

os.environ.setdefault("PYTHONHASHSEED", "0")

import agents.heuristic_protagonist as _hp  # noqa: E402
from arena.benchmark import run_benchmark  # noqa: E402


def _four_face(strong_params: dict | None):
    """4面の防衛数タプルと、面ごとの「防衛した局の集合」を返す。

    ★件数だけでは入れ替わり（swap＝ある局を失い別の局を得て件数不変）を見逃す。
    局集合まで返し flipレベルで退行を検出する（danger_murder=84 が件数flatなのに
    FS_10 を loss にした実測から必須と判明）。"""
    counts, sets = [], []
    for mm in (None, strong_params):
        for days in (3, 5):
            rep = run_benchmark(days=days, verbose=False, mm_params=mm)
            counts.append(rep["outcomes"].get("defense", 0))
            sets.append({f"{r['script']}|{r['seed']}" for r in rep["rows"]
                         if r["outcome"] == "defense"})
    return tuple(counts), sets


def _parse_values(s: str) -> list:
    return [ast.literal_eval(v.strip()) for v in s.split(",")]


def _get_coeff(HP, name):
    """name が "COEFF.key" ならdictキー、そうでなければクラス属性を読む。"""
    if "." in name:
        d, k = name.split(".", 1)
        return getattr(HP, d)[k]
    return getattr(HP, name)


def _set_coeff(HP, name, value):
    if "." in name:
        d, k = name.split(".", 1)
        getattr(HP, d)[k] = value
    else:
        setattr(HP, name, value)


def _has_coeff(HP, name):
    if "." in name:
        d, k = name.split(".", 1)
        return hasattr(HP, d) and k in getattr(HP, d)
    return hasattr(HP, name)


def sweep(coeff: str, values: list, strong_params: dict | None,
          coeff2: str | None = None, values2: list | None = None) -> None:
    HP = _hp.HeuristicProtagonist
    if not _has_coeff(HP, coeff):
        raise SystemExit(f"{coeff} は HeuristicProtagonist のクラス属性ではない"
                         f"（インライン係数はクラス属性へ抽出が必要）")
    base_val = _get_coeff(HP, coeff)
    base_val2 = _get_coeff(HP, coeff2) if coeff2 else None
    grid = [(v, w) for v in values for w in (values2 or [base_val2])]

    print(f"係数スイープ: {coeff}∈{values}"
          + (f" × {coeff2}∈{values2}" if coeff2 else "")
          + f"  強敵={'あり' if strong_params else 'なし'}")
    base_counts, base_sets = _four_face(strong_params)
    print(f"  基準（{coeff}={base_val}"
          + (f", {coeff2}={base_val2}" if coeff2 else "")
          + f"）: {'/'.join(map(str, base_counts))}（既定3d/5d・強敵3d/5d）", flush=True)
    try:
        for v, w in grid:
            if v == base_val and w == base_val2:
                continue
            _set_coeff(HP, coeff, v)
            if coeff2:
                _set_coeff(HP, coeff2, w)
            counts, sets = _four_face(strong_params)
            delta = [c - b for c, b in zip(counts, base_counts)]
            # flipレベル：面ごとに得た局(gain)と失った局(reg)を数える
            gains = sum(len(s - b) for s, b in zip(sets, base_sets))
            regs = sum(len(b - s) for s, b in zip(sets, base_sets))
            tag = f"{coeff}={v}" + (f", {coeff2}={w}" if coeff2 else "")
            if regs == 0 and gains > 0:
                adopt = "採用可"
            elif regs == 0 and gains == 0:
                adopt = "同値"
            else:
                adopt = f"退行あり(得{gains}/失{regs})"
            dstr = "/".join(f"{c}({d:+d})" for c, d in zip(counts, delta))
            print(f"  {tag}: {dstr}  [{adopt}]", flush=True)
    finally:
        _set_coeff(HP, coeff, base_val)
        if coeff2:
            _set_coeff(HP, coeff2, base_val2)


def main(argv=None):
    ap = argparse.ArgumentParser(description="主人公計算スコア係数の4面スイープ")
    ap.add_argument("--coeff", required=True)
    ap.add_argument("--values", required=True, help="カンマ区切り（例 2,3,4）")
    ap.add_argument("--coeff2", default=None)
    ap.add_argument("--values2", default=None)
    ap.add_argument("--strong", default=None, help="強敵mm params JSON")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    strong = None
    if args.strong:
        with open(args.strong, encoding="utf-8") as f:
            strong = json.load(f).get("params")
    sweep(args.coeff, _parse_values(args.values), strong,
          coeff2=args.coeff2,
          values2=_parse_values(args.values2) if args.values2 else None)


if __name__ == "__main__":
    main()
