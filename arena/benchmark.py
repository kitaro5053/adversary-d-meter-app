# -*- coding: utf-8 -*-
"""ループ数ベンチマーク（ユーザー方針 2026-07-08）— 標準評価軸。

勝敗の2値は感度が低い（強くなるほど全部「勝ち」で差が見えない）。そこで
**ループ数を長め（既定8）に取り、「主人公が勝利にかかったループ数」を測る**。
- 1ループで勝つ＝完璧な防衛。2〜3＝情報を集めてから勝つ通常形。
- 全ループ敗北（＋BTXは最後の戦いにも敗北）＝打ち切り値 loops+1 として集計。

脚本セット＝開発の標準130本（手書きサンプル9本 × seed 0-9 ＋ random_FS/BTX × seed 0-19）。
`--days 5` でランダム脚本を4ループ×5日級に切り替え（サンプル9本は3日設計のため除外）。

★測定は必ず PYTHONHASHSEED=0 で実行（スコア同点のタイブレークがsetのハッシュ順に
依存し、プロセスごとに結果が揺れる）。

CLI:
    PYTHONHASHSEED=0 python -m arena.benchmark              # 標準（3日・loops=8）
    PYTHONHASHSEED=0 python -m arena.benchmark --days 5     # 5日級（4L基準→loops=8）
    PYTHONHASHSEED=0 python -m arena.benchmark --out r.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import random_script, run_game
from sim.sample_scripts import SAMPLE_SCRIPTS


def benchmark_scripts(days: int = 3, n_sample_seeds: int = 10, n_random: int = 20):
    """(名前, seed, Script) の標準セットを列挙する。

    手書きサンプルは days_per_loop が一致するものだけ（3日級9本／5日級3本）。"""
    out = []
    for name, f in SAMPLE_SCRIPTS.items():
        sc = f()
        if sc.days_per_loop == days:
            for seed in range(n_sample_seeds):
                out.append((name, seed, f()))
    for set_name in ("FS", "BTX"):
        for seed in range(n_random):
            sc = random_script(set_name, seed, days=days)
            out.append((f"random_{set_name}", seed, sc))
    return out


def loops_to_win(script, seed: int, loops: int = 8,
                 mm_params: dict | None = None) -> tuple[int, str]:
    """(防衛勝ちにかかったループ数, 結末) を返す。

    ★最後の戦い（FB）は防衛勝ちと別扱い（ユーザー方針 2026-07-08）：
    FBで勝ってもループは1つも守れていない＝ループ数は打ち切り値 loops+1。
    結末: "defense"（あるループを守って勝ち）/ "fb_win" / "fb_loss" / "loss"。
    mm_params: 脚本家AIのパラメータ上書き（arena/params/*.json の "params"。
    CEM調整済みmm等の強い相手でロバスト性を測る＝単一相手への過学習の検出）。
    """
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed, params=mm_params)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def run_benchmark(loops: int = 8, days: int = 3, verbose: bool = True,
                  mm_params: dict | None = None) -> dict:
    from sim.loop_race import analyze_script
    t0 = time.time()
    rows = []
    for name, seed, sc in benchmark_scripts(days=days):
        # レース解析（神視点）：1ループが構造的に防衛不能（mastermind）な脚本は
        # 「全ループ落として最後の戦い」が最適＝loops_to_win=loopsが期待値。
        # 防衛可能（protagonist/contested）なのにループ数が嵩む方がAIの伸びしろ。
        race = analyze_script(sc).verdict
        ltw, outcome = loops_to_win(sc, seed, loops=loops, mm_params=mm_params)
        rows.append({"script": name, "seed": seed, "loops_to_win": ltw,
                     "outcome": outcome, "race": race})
        if verbose:
            mark = {"defense": "", "fb_win": "⚔", "fb_loss": "☠", "loss": "☠"}[outcome]
            print(f"  {name} s{seed}: {ltw}{mark} [{race[:4]}]", flush=True)
    vals = [r["loops_to_win"] for r in rows]
    censored = sum(1 for v in vals if v > loops)
    dist = Counter(vals)
    by_script: dict[str, list[int]] = {}
    for r in rows:
        by_script.setdefault(r["script"], []).append(r["loops_to_win"])
    defensible = [r["loops_to_win"] for r in rows if r["race"] != "mastermind"]
    undefendable = [r["loops_to_win"] for r in rows if r["race"] == "mastermind"]
    outcomes = Counter(r["outcome"] for r in rows)
    report = {
        "loops": loops, "days": days, "n_games": len(rows),
        "outcomes": dict(outcomes),   # defense / fb_win / fb_loss / loss
        "mean_loops_to_win": round(sum(vals) / len(vals), 3),
        # ★主指標：防衛可能な脚本だけの平均（防衛不能脚本のFB勝負は脚本側の性質）
        "mean_defensible": round(sum(defensible) / len(defensible), 3)
        if defensible else None,
        "n_undefendable": len(undefendable),
        "mean_undefendable": round(sum(undefendable) / len(undefendable), 3)
        if undefendable else None,
        "censored": censored,   # 全敗（打ち切り loops+1）の件数
        "distribution": {str(k): dist[k] for k in sorted(dist)},
        "by_script_mean": {k: round(sum(v) / len(v), 2)
                           for k, v in sorted(by_script.items())},
        "elapsed_sec": round(time.time() - t0, 1),
        "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
        "rows": rows,
    }
    return report


def format_report(rep: dict) -> str:
    lines = [
        f"ループ数ベンチ: {rep['n_games']}局 loops={rep['loops']} days={rep['days']} "
        f"PYTHONHASHSEED={rep['hashseed']}",
        f"  平均 勝利までのループ数: {rep['mean_loops_to_win']}"
        f"（全敗{rep['censored']}件は{rep['loops'] + 1}として計上）",
        f"  ★防衛可能な脚本のみ: {rep['mean_defensible']}"
        f"（防衛不能{rep['n_undefendable']}局は平均{rep['mean_undefendable']}"
        f"＝FB勝負が最適な脚本）",
        "  結末: " + "  ".join(
            f"{k}:{v}" for k, v in sorted(rep.get("outcomes", {}).items())),
        "  分布: " + "  ".join(f"L{k}:{v}" for k, v in rep["distribution"].items()),
        "  脚本別平均: " + "  ".join(f"{k}={v}" for k, v in rep["by_script_mean"].items()),
        f"  所要 {rep['elapsed_sec']}秒",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ループ数ベンチマーク")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--mm-params", type=str, default=None,
                    help="脚本家AIパラメータJSON（arena/params/*.json）＝強い相手での測定")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。測定は PYTHONHASHSEED=0 で実行してください。",
              file=sys.stderr)
    mm_params = None
    if args.mm_params:
        with open(args.mm_params, encoding="utf-8") as f:
            data = json.load(f)
        mm_params = data.get("params", data)
        print(f"脚本家パラメータ: {args.mm_params}")
    rep = run_benchmark(loops=args.loops, days=args.days, verbose=not args.quiet,
                       mm_params=mm_params)
    print(format_report(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
