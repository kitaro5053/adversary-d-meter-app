# -*- coding: utf-8 -*-
"""B-224 計測プローブ：事前登録の的①②③を条件つきで数える（読み取り専用）。

的（`docs/仮_b224_log/事前登録_B224.md`＝実装前にコミットで固定）：
  ①（`random_BTX#12` 3日）＝rep≥1×fatal×defendable の脅威が `b100_alloc` の
    『資格ゼロ』段で終わった席数 → 減（ベースライン 15）
  ②（`btx_future#4` D3／`random_BTX#0` D5）＝決定打日にチームが `不安-1` を
    出せるのに『不安-1→当日犯人（検死確定）』がどの席の options にも無いターン数
    → 減（ベースライン 5／2）
  ③＝該当局の敗北条件成立ループ数 → 減（ベースライン 7/7/7）

計測は `B100_HOOK`／`b100_alloc.TRACE`（計測専用・挙動非接触）経由。
条件は `arena.b224_ab.CONDS` の名前で与える（off＝挙動不変）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b224_probe metrics --cond off
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import replace

from arena.b221_probe import make_script

#: 的の3局＝(script, seed, days, 決定打日, 検死確定の当日犯人（照合キー・計測のみ）)
GAMES = [
    ("btx_future", 4, 3, 3, "刑事"),     # D3 殺人事件（B-220 §3.1）
    ("random_BTX", 12, 3, None, None),   # 的①の局（決定打は D1 暗躍＝冷却の的ではない）
    ("random_BTX", 0, 5, 5, "巫女"),     # D5 自殺（B-220 §3.4）
]


def run_game_metrics(name: str, seed: int, days: int, dday, culprit,
                     cond: str = "off", loops: int = 8) -> dict:
    import agents.heuristic_protagonist as HPmod
    from agents import b100_alloc
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.b224_ab import CONDS, _ATTRS
    from sim import run_game

    old = {a: getattr(HeuristicProtagonist, a) for a in _ATTRS}
    for k, v in CONDS[cond].items():
        setattr(HeuristicProtagonist, k, v)

    seats: list[dict] = []
    traces: list[dict] = []

    def hook(agent, view, options, best, score):
        cools = sorted({(o["card"], o["target"]) for o in options
                        if o["card"] == "不安-1"})
        seats.append({"loop": view.get("loop"), "day": view.get("day"),
                      "seat": view.get("seat"),
                      "cool_targets": [t for _c, t in cools],
                      "chosen": (best["card"], best["target"])})

    def trace(d):
        traces.append({"loop": d.get("loop"), "day": d.get("day"),
                       "stage": d.get("stage"),
                       "reps": [c.get("repeat", 0) for c in (d.get("cons") or [])]})

    prev_hook, prev_trace = HPmod.B100_HOOK, b100_alloc.TRACE
    HPmod.B100_HOOK, b100_alloc.TRACE = hook, trace
    try:
        sc = make_script(name, seed, days)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        HPmod.B100_HOOK, b100_alloc.TRACE = prev_hook, prev_trace
        for a, v in old.items():
            setattr(HeuristicProtagonist, a, v)

    lost = sorted({e["loop"] for e in state.history
                   if e.get("event") == "loop_result"
                   and "敗北" in str(e.get("result"))})
    # 的①＝『資格ゼロ』段で終わり、かつ制約のどれかが rep≥1 の席数
    m1 = sum(1 for t in traces
             if str(t["stage"]).startswith("資格ゼロ")
             and any(r >= 1 for r in t["reps"]))
    # 的②＝決定打日：チームは冷却を出せる（どこかの席に 不安-1→* がある）のに
    #       『不安-1→当日犯人』がどの席にも無いターン数
    m2 = None
    if dday is not None:
        by_ld: dict = defaultdict(list)
        for s in seats:
            if s["day"] == dday:
                by_ld[s["loop"]].append(s)
        m2 = 0
        for lp, ss in sorted(by_ld.items()):
            has_any = any(s["cool_targets"] for s in ss)
            has_culp = any(culprit in s["cool_targets"] for s in ss)
            if has_any and not has_culp:
                m2 += 1
    return {"game": f"{name}#{seed}", "days": days, "cond": cond,
            "winner": state.winner, "m1_silent": m1, "m2_erased": m2,
            "m3_lost_loops": len(lost), "lost": lost}


def cmd_metrics(args) -> int:
    for name, seed, days, dday, culp in GAMES:
        r = run_game_metrics(name, seed, days, dday, culp, cond=args.cond,
                             loops=args.loops)
        print(f"[{args.cond}] {r['game']} d{days}: "
              f"的①資格ゼロ席={r['m1_silent']} 的②冷却消失ターン={r['m2_erased']} "
              f"的③敗北ループ={r['m3_lost_loops']} winner={r['winner']} "
              f"lost={r['lost']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-224 的の計測（読み取り専用）")
    ap.add_argument("cmd", choices=["metrics"])
    ap.add_argument("--cond", default="off")
    ap.add_argument("--loops", type=int, default=8)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"metrics": cmd_metrics}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
