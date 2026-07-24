# -*- coding: utf-8 -*-
"""防御ミス率の監査 — 係数学習（B5）の信号測定＆教師ラベル源。

問い：主人公AIは「その日に守る応手が存在した（defense_exists）」のに、実際には
その応手を打てなかった、ということがどれくらいあるか？

この「防御ミス率」＝(防御が存在したのにAIが防御手を打たなかった日) / (防御が存在した日)
は、勝敗より密で帰属可能な指標（どの局面のどの席が悪かったかまで分かる）。
係数学習（PRIORITYでなく計算スコアの係数を find_defenses ラベルで監督する方式・
引き継ぎ§4c(c)）の教師信号そのもの。

効率：各決定日で、まず AI の実手が防御か安価にチェック（is_defense＝|contents|局）。
防御でなかった時だけ、他に防御が在るか find_defenses(max_found=1) で確認する
（＝ミス候補のみ高コスト探索）。負けたループの決定打の日だけを見る（postmortem流）。

CLI:
    PYTHONHASHSEED=0 python -m arena.defense_audit --days 3
    PYTHONHASHSEED=0 python -m arena.defense_audit --days 5 --mm-params arena/params/mm_cem_gen2_20260709.json
    PYTHONHASHSEED=0 python -m arena.defense_audit --sample FS --seeds 0-19 --days 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from arena.postmortem import (
    _decisive_days,
    _lost_loops,
    _mm_set_of,
    replay_with_snapshots,
)
from sim.mate import defense_exists, is_defense


def _prot_play(log, loop: int, day: int) -> list[dict]:
    """その (loop, day) に主人公3席が実際に置いた手（owner無し option 形）。"""
    return [e["chosen"] for e in log
            if e["actor"] in ("p1", "p2", "p3") and e["decision"] == "set_card"
            and e["loop"] == loop and e["day"] == day]


def audit_game(script, seed: int, loops: int = 8, mm_params: dict | None = None,
               max_scan: int | None = 4000, verbose: bool = False) -> list[dict]:
    """1ゲームの負けループの決定日を監査し、日ごとの結果行を返す。

    行: {loop, day, objective, verdict}
      verdict ∈ {"defended"(AIが防御手を打てた), "miss"(防御在るのに逃した),
                 "no_defense"(そもそも防御不能＝AIの責任外), "unknown"(予算超過)}
    max_scan＝「他に防御が在るか」を確かめる走査予算（no_defense の全走査コストを
    上限で打ち切って unknown に丸める。ミス率の分母には影響しない）。
    """
    state, log, snaps = replay_with_snapshots(script, seed, loops=loops,
                                              mm_params=mm_params)
    rows: list[dict] = []
    for loop in _lost_loops(state):
        for day, objective in _decisive_days(state, loop):
            snap = snaps.get((loop, day))
            mm_set = _mm_set_of(log, loop, day)
            play = _prot_play(log, loop, day)
            if snap is None or not mm_set or not play:
                continue
            try:
                if is_defense(snap, play, mm_set=mm_set, objective=objective):
                    verdict = "defended"
                else:
                    # AIの実手は防御でない→他に防御が在るか（在れば「ミス」）
                    ex = defense_exists(snap, mm_set=mm_set, objective=objective,
                                        max_scan=max_scan)
                    verdict = {"yes": "miss", "no": "no_defense",
                               "unknown": "unknown"}[ex]
            except ValueError:
                continue
            row = {"loop": loop, "day": day, "objective": str(objective),
                   "verdict": verdict}
            rows.append(row)
            if verbose:
                print(f"  L{loop}D{day} [{str(objective)[:10]}]: {verdict}",
                      flush=True)
    return rows


def run_audit(days: int = 3, loops: int = 8, mm_params: dict | None = None,
              max_scan: int | None = 4000, verbose: bool = True) -> dict:
    from arena.benchmark import benchmark_scripts
    t0 = time.time()
    from collections import Counter
    tally: Counter = Counter()
    misses: list[dict] = []
    n_games = 0
    for name, seed, sc in benchmark_scripts(days=days):
        n_games += 1
        for row in audit_game(sc, seed, loops=loops, mm_params=mm_params,
                              max_scan=max_scan):
            tally[row["verdict"]] += 1
            if row["verdict"] == "miss":
                misses.append({"script": name, "seed": seed, **row})
        if verbose:
            print(f"  ... {n_games}局 tally={dict(tally)}", file=sys.stderr,
                  flush=True)
    # 防御ミス率＝miss / (defended + miss)（分母＝防御が存在した決定日）
    denom = tally["defended"] + tally["miss"]
    miss_rate = tally["miss"] / denom if denom else 0.0
    return {
        "days": days, "loops": loops, "n_games": n_games,
        "mm": "tuned" if mm_params else "既定",
        "tally": dict(tally),
        "defense_days": denom,
        "miss_rate": round(miss_rate, 4),
        "misses": misses,
        "elapsed_sec": round(time.time() - t0, 1),
    }


def format_report(rep: dict) -> str:
    lines = [
        f"防御ミス率監査: {rep['days']}日級 {rep['n_games']}局 "
        f"loops={rep['loops']} mm={rep['mm']}",
        f"  内訳: " + "  ".join(f"{k}={v}" for k, v in sorted(rep["tally"].items())),
        f"  ★防御ミス率: {rep['miss_rate']:.1%}"
        f"（防御が存在した決定日 {rep['defense_days']} のうち逃した割合）",
        f"  所要 {rep['elapsed_sec']}秒",
    ]
    if rep["misses"]:
        lines.append("  ミスした局面（係数学習の教師サンプル）:")
        for m in rep["misses"][:20]:
            lines.append(f"    {m['script']} s{m['seed']} L{m['loop']}D{m['day']}"
                         f" [{m['objective'][:10]}]")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="防御ミス率の監査（B5信号測定）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--mm-params", type=str, default=None)
    ap.add_argument("--max-scan", type=int, default=4000,
                    help="他に防御が在るかの走査予算（no_defense証明の打ち切り）")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    mm_params = None
    if args.mm_params:
        with open(args.mm_params, encoding="utf-8") as f:
            mm_params = json.load(f).get("params")
    rep = run_audit(days=args.days, loops=args.loops, mm_params=mm_params,
                   max_scan=args.max_scan)
    print(format_report(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
