# -*- coding: utf-8 -*-
"""B-116 Phase 1：★「トレッドミル」の数え上げ（シャドー集計・挙動変更なし）。

背景（`docs/監査_B114_θ再掃引_新風景_2026-07-31.md` §6-1）：
B-100 が**同じ負け筋ラベルにループを跨いで毎ループ発火**し、そのたびに通常採点の
高得点手（実測例＝103点）を低得点の防御（5.5点）で押し出し続ける形＝
「ループ横断のトレッドミル」は、防げない負け筋に対して自傷になる
（θ=1.0 時代の 3日級 `random_FS` s10 の敗局がこの形・計14回）。
`B100_MAX_PER_LOOP` は**ループ内**の上限なのでこの形を切れない。

本CLIは `arena.b100_shadow.run_config` の介入ログ（`agent._b100_log`）から

  - per-game・負け筋ラベル別の **発火ループ数**（distinct loop）・**最大連続ループ長**・
    **総発火回数**
  - 各介入の **押し出し得点差** gap = displaced_score − forced_score
  - 「同一ラベルに K ループ以上発火」×「押し出し得点差 ≥ G」の該当対局と勝敗

を数える。★計測専用＝本番経路・採点には一切触れない（config の適用は
`b100_shadow._apply` の流儀＝終了時に必ず復元）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_treadmill --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_treadmill --days 5 \
        --config default --out /path/out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

# ---------------------------------------------------------------------------
# 純関数（単体テスト対象）＝介入ログ → ラベル別のループ横断統計
# ---------------------------------------------------------------------------


def _max_streak(loops: list[int]) -> int:
    """distinct なループ番号列（昇順）の最大**連続**長（例 [2,3,4,6,7]→3）。"""
    if not loops:
        return 0
    best = cur = 1
    for a, b in zip(loops, loops[1:]):
        cur = cur + 1 if b == a + 1 else 1
        best = max(best, cur)
    return best


def label_runs(interventions: list[dict]) -> dict:
    """1局の介入ログを負け筋ラベル別に畳む。

    返り値 {label: {"fires", "loops"(distinct・昇順), "n_loops", "max_streak",
                    "kinds", "gaps"(押し出し得点差・記録がある席のみ), "max_gap"}}
    gap = displaced_score − forced_score（押し出した手が記録されている席のみ。
    displaced が None＝押し出し無しで空席に置いた形は gap の分布に入れない）。
    """
    out: dict = {}
    for iv in interventions:
        lab = iv.get("label")
        r = out.setdefault(lab, {"fires": 0, "loops": [], "kinds": set(),
                                 "gaps": []})
        r["fires"] += 1
        lp = iv.get("loop")
        if lp is not None and lp not in r["loops"]:
            r["loops"].append(lp)
        r["kinds"].add(iv.get("kind"))
        ds, fs = iv.get("displaced_score"), iv.get("forced_score")
        if ds is not None and fs is not None:
            r["gaps"].append(round(ds - fs, 1))
    for r in out.values():
        r["loops"] = sorted(r["loops"])
        r["n_loops"] = len(r["loops"])
        r["max_streak"] = _max_streak(r["loops"])
        r["kinds"] = sorted(k for k in r["kinds"] if k is not None)
        r["max_gap"] = max(r["gaps"]) if r["gaps"] else None
    return out


def treadmill_labels(runs: dict, k_loops: int, min_gap: float) -> list[str]:
    """「トレッドミル」該当ラベル＝同一ラベルに **k_loops 以上の distinct ループ**で
    発火し、かつ押し出し得点差の最大が **min_gap 以上**（gap の記録が1件も無い
    ラベルは該当しない＝押し出しの無い発火は自傷の証拠にならない）。"""
    return [lab for lab, r in runs.items()
            if r["n_loops"] >= k_loops
            and r["max_gap"] is not None and r["max_gap"] >= min_gap]


def game_stats(rows: list[dict]) -> list[dict]:
    """`b100_shadow.run_config` の rows → per-game のトレッドミル統計。"""
    out = []
    for row in rows:
        runs = label_runs(row.get("interventions", []))
        out.append({"game": row["game"], "seed": row["seed"],
                    "ltw": row["ltw"], "outcome": row["outcome"],
                    "n_interventions": len(row.get("interventions", [])),
                    "runs": runs})
    return out


# ---------------------------------------------------------------------------
# 集計・表示
# ---------------------------------------------------------------------------

#: 閾値マトリクス（Phase 1＝定義の妥当性を実データで見るため複数点を出す）
K_GRID = (2, 3, 4)
GAP_GRID = (0.0, 30.0, 60.0, 90.0)


def summarize(stats: list[dict], rep: dict) -> str:
    L = [f"== B-116 トレッドミル数え上げ（{rep['days']}日級 {rep['n_games']}局・"
         f"config={rep['config']}） ==",
         f"  防衛={rep['defense']} 平均={rep['mean']} 結末={rep['outcomes']}"
         f" 介入局={rep['n_intervened_games']} 介入回={rep['n_interventions']}"
         f"／PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}"]
    # (1) per-game×ラベルの「発火ループ数」分布
    nl_hist: Counter = Counter()
    st_hist: Counter = Counter()
    gap_hist: Counter = Counter()
    lab_tot: Counter = Counter()
    for g in stats:
        for lab, r in g["runs"].items():
            nl_hist[r["n_loops"]] += 1
            st_hist[r["max_streak"]] += 1
            lab_tot[lab] += r["fires"]
            for gp in r["gaps"]:
                gap_hist[_gap_band(gp)] += 1
    L.append("  [分布1] 同一対局×同一ラベルの発火ループ数（distinct loop）:")
    for k in sorted(nl_hist):
        L.append(f"    {k}ループ: {nl_hist[k]} 件")
    L.append("  [分布2] 同・最大連続ループ長: " +
             " ".join(f"{k}連続:{st_hist[k]}件" for k in sorted(st_hist)))
    L.append("  [分布3] 押し出し得点差 gap=displaced−forced（介入1回ごと）:")
    for band in sorted(gap_hist, key=_band_key):
        L.append(f"    {band}: {gap_hist[band]} 件")
    L.append("  [ラベル別] 総発火回数（多い順）: " +
             " ".join(f"{lab}:{n}" for lab, n in lab_tot.most_common()))
    # (2) 閾値マトリクス＝該当対局数（勝敗の内訳つき）
    L.append("  [該当対局マトリクス] Kループ以上×max_gap≥G（局数・()内=非防衛局）:")
    header = "    K\\G   " + "".join(f"{g:>10.0f}" for g in GAP_GRID)
    L.append(header)
    for k in K_GRID:
        cells = []
        for gp in GAP_GRID:
            hit = [g for g in stats if treadmill_labels(g["runs"], k, gp)]
            bad = sum(1 for g in hit if g["outcome"] != "defense")
            cells.append(f"{len(hit):>6}({bad})")
        L.append(f"    K={k}  " + "".join(f"{c:>10}" for c in cells))
    # (3) 該当対局の明細（最も緩い K=2/G=0 で列挙・ラベル別の中身つき）
    L.append("  [明細] K=2・G=0 の該当対局（ラベル: ループ列・発火回・max_gap）:")
    for g in stats:
        labs = treadmill_labels(g["runs"], 2, 0.0)
        if not labs:
            continue
        parts = []
        for lab in labs:
            r = g["runs"][lab]
            parts.append(f"{lab}[loops={r['loops']} fires={r['fires']}"
                         f" streak={r['max_streak']} max_gap={r['max_gap']}]")
        L.append(f"    {g['game']} s{g['seed']} ltw={g['ltw']} {g['outcome']}: "
                 + "／".join(parts))
    return "\n".join(L)


def _gap_band(gp: float) -> str:
    if gp < 0:
        return "<0（強制の方が高得点）"
    for hi in (30, 60, 90):
        if gp < hi:
            return f"{hi - 30}〜{hi}"
    return "90以上"


def _band_key(band: str) -> float:
    if band.startswith("<0"):
        return -1.0
    if band.startswith("90"):
        return 90.0
    return float(band.split("〜")[0])


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-116 Phase 1：トレッドミル数え上げ")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--config", type=str, default="default",
                    help="arena.b100_shadow の config 名（既定=default＝main挙動）")
    ap.add_argument("--scripts", type=str, default="bench",
                    choices=["bench", "user"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    from arena.b100_shadow import run_config
    rep = run_config(args.config, args.days, loops=args.loops,
                     limit=args.limit, verbose=False, scripts=args.scripts)
    stats = game_stats(rep["rows"])
    print(summarize(stats, rep))
    if args.out:
        dump = {"days": args.days, "config": args.config,
                "defense": rep["defense"], "mean": rep["mean"],
                "outcomes": rep["outcomes"],
                "stats": [{**g, "runs": {lab: {**r} for lab, r in
                                         g["runs"].items()}} for g in stats]}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
