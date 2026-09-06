# -*- coding: utf-8 -*-
"""B-221 Phase 0：反復非適応の一次調査プローブ（読み取り専用・挙動に影響しない）。

問い（チケット §3 Phase 0）：
  1. なぜ既存の `repeat_count`（B-211 是正済み）で反復が止まらないのか
     （帰属キーが立っていない？点が足りない？席が食われる？発火経路が死んでいる？）
  2. 敗北ターンで「反復手」は「flip の手」に**何点差**で勝っているのか。

計測は `agents.heuristic_protagonist.B100_HOOK`（既定 None の計測専用フック・
戻り値不使用・例外握り潰し）と `agents.b100_alloc.TRACE`（同・計測専用）経由＝
AI の選択には一切影響しない。既存前例＝`arena/b100_audit.py`・`arena/b130_audit.py`。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b221_probe repeats --days 3 --perm id
    PYTHONHASHSEED=0 ... python -m arena.b221_probe why --game btx_future:4:3 --loops 8
    PYTHONHASHSEED=0 ... python -m arena.b221_probe why --game random_FS:10:5 --day 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import replace

#: B-220 検死の非防衛4局（族A＝反復非適応）。
GAMES4 = [
    ("btx_future", 4, 3),
    ("random_BTX", 12, 3),
    ("random_FS", 10, 5),
    ("random_BTX", 0, 5),
]


def make_script(name: str, seed: int, days: int):
    """★ベンチと同一の脚本を作る（`arena.b220_probe.make_script` と同じ形）。"""
    if name.startswith("random_"):
        from sim import random_script
        set_name = name.split("_", 1)[1]
        return random_script(set_name, seed, days=days)
    from sim.sample_scripts import SAMPLE_SCRIPTS
    return SAMPLE_SCRIPTS[name]()


# ---------------------------------------------------------------------------
# repeats：B-220 の行為指標（完全一致対）を perm 条件つきで数える
# ---------------------------------------------------------------------------
def count_repeats(days: int, loops: int = 8, perm: str = "id",
                  cond: str = "off") -> dict:
    """`arena.b220_probe.cmd_repeats` と同一の定義＋`arena.tie_noise.install_perm`。

    `cond`＝`arena.b221_ab.CONDS` の条件名（off＝既定＝挙動不変）。
    """
    from arena.b221_ab import CONDS
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    total_pairs = 0
    total_lost_pairs = 0
    games_with_streak: list[tuple] = []
    on, cap, pair, blk = CONDS[cond]
    old = (HeuristicProtagonist.B221_BREAKER, HeuristicProtagonist.B221_CAP,
           HeuristicProtagonist.B221_REQUIRE_PAIR,
           HeuristicProtagonist.B221_BLOCK_BREAKS)
    (HeuristicProtagonist.B221_BREAKER, HeuristicProtagonist.B221_CAP,
     HeuristicProtagonist.B221_REQUIRE_PAIR,
     HeuristicProtagonist.B221_BLOCK_BREAKS) = on, cap, pair, blk
    install_perm(perm)
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            log: list[dict] = []
            state, _ = run_game(replace(sc, loops=loops),
                                {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                log=log)
            lost = {e["loop"] for e in state.history
                    if e.get("event") == "loop_result"
                    and "敗北" in str(e.get("result"))}
            if state.winner == "mastermind" or any(
                    e.get("event") == "final_battle" for e in state.history):
                lost.add(state.loop_no)
            pro_by_loop: dict = defaultdict(list)
            for e in log:
                if e.get("decision") == "set_card" and e.get("actor") != "mastermind":
                    c = e["chosen"]
                    pro_by_loop[e["loop"]].append(
                        (e["day"], c["card"], c["target"], c["target_kind"]))
            rep = 0
            streak = 0
            best = 0
            for lp in sorted(lost):
                if (lp - 1) in lost:
                    total_lost_pairs += 1
                    if (sorted(pro_by_loop.get(lp, []))
                            == sorted(pro_by_loop.get(lp - 1, []))):
                        rep += 1
                        streak += 1
                        best = max(best, streak)
                    else:
                        streak = 0
            total_pairs += rep
            if rep:
                games_with_streak.append((name, seed, rep, best,
                                          len(lost), state.winner))
    finally:
        uninstall_perm()
        (HeuristicProtagonist.B221_BREAKER, HeuristicProtagonist.B221_CAP,
         HeuristicProtagonist.B221_REQUIRE_PAIR,
         HeuristicProtagonist.B221_BLOCK_BREAKS) = old
    return {"days": days, "perm": perm, "cond": cond,
            "lost_pairs": total_lost_pairs,
            "identical_pairs": total_pairs,
            "games": [{"game": f"{n}#{s}", "pairs": r, "best_streak": b,
                       "n_lost": nl, "winner": w}
                      for n, s, r, b, nl, w in games_with_streak]}


def cmd_repeats(args) -> int:
    res = count_repeats(args.days or 3, loops=args.loops, perm=args.perm,
                        cond=args.cond)
    print(f"days={res['days']} perm={res['perm']} cond={res['cond']}: "
          f"連続敗北ループ対={res['lost_pairs']}  完全一致={res['identical_pairs']}")
    for g in res["games"]:
        print(f"  {g['game']}: 一致対={g['pairs']} 最長連続={g['best_streak']}"
              f" 敗北ループ={g['n_lost']} winner={g['winner']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


# ---------------------------------------------------------------------------
# why：1局を再生し、各席の採点表・脅威・帰属・B-100 の段を記録する
# ---------------------------------------------------------------------------
def _fmt_move(card, target, kind=None) -> str:
    return f"{card}→{target}"


def why_game(name: str, seed: int, days: int, loops: int = 8) -> dict:
    """1局を素で再生し、席ごとの観測（採点表・脅威・repeat・B-100 段）を返す。"""
    import agents.heuristic_protagonist as HPmod
    from agents import b100_alloc
    from agents.b100_mix import (B211_FRIEND_DEATH_ATTR_DEFAULT, past_loss_keys,
                                 repeat_count)
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    sc = make_script(name, seed, days)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    seats: list[dict] = []
    traces: list[dict] = []

    def hook(agent, view, options, best, score):
        loop, day = view.get("loop"), view.get("day")
        try:
            table = sorted(((round(float(score(o)), 2), o["card"], o["target"],
                             o.get("target_kind")) for o in options),
                           reverse=True)
        except Exception:
            table = []
        past = past_loss_keys(
            view.get("history", []) or [], loop,
            friend_attr=getattr(agent, "B211_FRIEND_DEATH_ATTR",
                                B211_FRIEND_DEATH_ATTR_DEFAULT))
        cast = [c.get("name") for c in view.get("characters", []) or []]
        inc_names = {i.get("name") for i in view.get("incidents", []) or []}
        stash = getattr(agent, "_b100_plan", None)
        threats = []
        if stash:
            for t in stash[0]:
                threats.append({
                    "kind": t.kind, "label": t.label,
                    "prob": round(float(t.prob), 3),
                    "fatal": bool(t.fatal), "defendable": bool(t.defendable),
                    "due_day": getattr(t, "due_day", None),
                    "repeat": repeat_count(t, past, cast, inc_names),
                    "breaks": [(b.card, b.target, b.target_kind,
                                round(float(b.cost), 1))
                               for c in t.conditions for b in c.breaks][:8],
                })
        seats.append({
            "loop": loop, "day": day, "seat": view.get("seat"),
            "chosen": (best["card"], best["target"], best.get("target_kind"),
                       best.get("prov")),
            "chosen_score": (round(float(score(best)), 2)
                             if table else None),
            "table": table,
            "past_keys": {ty: dict(ctr) for ty, ctr in past.items()},
            "threats": threats,
            "b100_seats_used": getattr(agent, "_b100_seats", 0),
            "experiment": bool(getattr(agent, "_experiment", False)),
            "lost_loops": getattr(agent, "_lost_loops", 0),
        })

    def trace(d):
        traces.append(dict(d))

    prev_hook = HPmod.B100_HOOK
    prev_trace = b100_alloc.TRACE
    HPmod.B100_HOOK = hook
    b100_alloc.TRACE = trace
    log: list[dict] = []
    try:
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                            log=log)
    finally:
        HPmod.B100_HOOK = prev_hook
        b100_alloc.TRACE = prev_trace
    lost = sorted({e["loop"] for e in state.history
                   if e.get("event") == "loop_result"
                   and "敗北" in str(e.get("result"))})
    return {"game": f"{name}#{seed}", "days": days, "winner": state.winner,
            "lost_loops": lost, "seats": seats, "traces": traces,
            "b100_log": list(getattr(hp, "_b100_log", []))}


def cmd_why(args) -> int:
    name, seed, days = args.game
    res = why_game(name, seed, days, loops=args.loops)
    print(f"=== {res['game']} d{days} winner={res['winner']} "
          f"敗北ループ={res['lost_loops']} ===")
    want_loops = set(args.loop_list or res["lost_loops"])
    for s in res["seats"]:
        if s["loop"] not in want_loops:
            continue
        if args.day and s["day"] != args.day:
            continue
        c = s["chosen"]
        print(f"\nL{s['loop']}D{s['day']} {s['seat']} "
              f"chosen={_fmt_move(c[0], c[1])} ({s['chosen_score']}) prov={c[3]}"
              f" exp={s['experiment']} lost={s['lost_loops']}"
              f" b100_used={s['b100_seats_used']}")
        if args.table:
            for sc_, card, tgt, k in s["table"][:args.table]:
                print(f"    {sc_:8.2f} {_fmt_move(card, tgt)} [{k}]")
        if s["past_keys"]:
            print(f"  past_keys: "
                  + json.dumps(s["past_keys"], ensure_ascii=False))
        for t in s["threats"]:
            if args.fatal_only and not t["fatal"]:
                continue
            print(f"  threat {t['kind']} p={t['prob']} fatal={t['fatal']}"
                  f" def={t['defendable']} due={t['due_day']}"
                  f" repeat={t['repeat']}  {t['label']}")
            if args.breaks:
                for b in t["breaks"]:
                    print(f"      break {_fmt_move(b[0], b[1])} cost={b[3]}")
    if args.trace:
        print("\n--- b100_alloc TRACE（席単位の段） ---")
        for tr in res["traces"]:
            if tr.get("loop") not in want_loops:
                continue
            if args.day and tr.get("day") != args.day:
                continue
            cons = tr.get("cons") or []
            gated = [c for c in cons if c.get("gate")]
            print(f"L{tr.get('loop')}D{tr.get('day')} stage={tr['stage']}"
                  f" 資格つき={len(gated)}/{len(cons)}")
            for c in cons:
                print(f"    {'★' if c.get('gate') else ' '} {c['kind']}"
                      f" p={c['prob']:.2f} rep={c['repeat']}"
                      f" gate={c.get('gate')} here0={c.get('here0')}"
                      f"  {c['label']}")
    if res["b100_log"]:
        print("\n--- B-100 介入（実際に強制した席） ---")
        for e in res["b100_log"]:
            if e.get("loop") not in want_loops:
                continue
            print(f"  L{e['loop']}D{e['day']} {e.get('seat')}"
                  f" {_fmt_move(e['card'], e['target'])}"
                  f" [{e['kind']} p={e['prob']}] {e['reason']}"
                  f" displaced={e.get('displaced')}({e.get('displaced_score')})")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


def _parse_game(s: str):
    name, seed, days = s.split(":")
    return name, int(seed), int(days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-221 Phase 0 プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["repeats", "why"])
    ap.add_argument("--game", type=_parse_game, default=None,
                    help="script:seed:days（例 btx_future:4:3）")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", default="id", help="repeats: id/rev/h1 等")
    ap.add_argument("--cond", default="off",
                    help="repeats: b221_ab.CONDS の条件名（off＝挙動不変）")
    ap.add_argument("--loop-list", type=int, nargs="*", default=None,
                    help="why: 表示するループ（既定＝敗北ループ全部）")
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--table", type=int, default=0,
                    help="why: 採点表の上位N行を表示")
    ap.add_argument("--breaks", action="store_true")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--fatal-only", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"repeats": cmd_repeats, "why": cmd_why}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
