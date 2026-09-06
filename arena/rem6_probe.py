# -*- coding: utf-8 -*-
"""rem6：5日級 残6局の検死プローブ（読み取り専用＝AI・エンジンには触れない）。

ベンチ（arena/benchmark.loops_to_win）と同一条件でゲームを再現し、
神視点の脚本定義と、公開履歴（loop_board を一次ソース）でループ別の敗北機序を出す。

CLI:
    PYTHONHASHSEED=0 python -m arena.rem6_probe overview --set FS --seed 1 --days 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import random_script, run_game


def _fmt_chosen(o: dict) -> str:
    o = {k: v for k, v in o.items() if k != "prov"}
    if "card" in o:
        return f'{o.get("card")}→{o.get("target")}'
    return f'{o.get("action")}' + (f'→{o.get("target")}' if o.get("target") else "")


def cmd_overview(args) -> int:
    sl = args.script_loops or (4 if args.days == 5 else 3)
    sc = random_script(args.set_name, args.seed, loops=sl, days=args.days)
    print(f"=== random_{args.set_name} seed={args.seed} days={sc.days_per_loop} "
          f"script_loops={sl} bench_loops={args.loops} ===")
    print(f"[脚本] rule_y={getattr(sc, 'rule_y', None)}  rule_x={getattr(sc, 'rule_x', None)}")
    print("[配役]")
    for n, r in sc.roles.items():
        print(f"  {n}: {r}")
    print("[事件]")
    for inc in sc.incidents:
        print(f"  {inc}")

    probe = replace(sc, loops=args.loops)
    mm = HeuristicMastermind(args.seed)
    hp = HeuristicProtagonist(args.seed)
    log: list[dict] = []
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                        log=log)
    fb = any(e.get("event") == "final_battle" for e in state.history)
    print(f"\n[結果] winner={state.winner}  final_battle={fb}  loop_no={state.loop_no}")

    # ループ別の公開イベント要約（loop_board＝公開カウンター合計を一次ソースに）
    for e in state.history:
        ev = e.get("event")
        if ev in ("loop_start",):
            print(f"\n--- L{e.get('loop')} ---")
        if ev == "incident" and e.get("occurs"):
            print(f"  D{e.get('day')} ⚡事件発生: {e.get('name')} "
                  f"(犯人={e.get('culprit', '?')})")
        elif ev == "incident" and args.verbose:
            print(f"  D{e.get('day')} （事件不発: {e.get('name')}）")
        elif ev == "death":
            print(f"  D{e.get('day')} 💀死亡: {e.get('name')} ({e.get('cause', '?')})")
        elif ev == "protagonist_death":
            print(f"  D{e.get('day')} 💀主人公死亡: {json.dumps({k: v for k, v in e.items() if k not in ('event', 'loop', 'day')}, ensure_ascii=False)}")
        elif ev == "loop_end":
            print(f"  D{e.get('day')} loop_end: {json.dumps({k: v for k, v in e.items() if k not in ('event', 'loop', 'day')}, ensure_ascii=False)}")
        elif ev == "loop_board":
            print(f"  loop_board: board={e.get('board_anyaku')} "
                  f"goodwill={e.get('char_goodwill', '')}")
        elif ev == "defeat":
            print(f"  D{e.get('day', '?')} ☠defeat: {json.dumps({k: v for k, v in e.items() if k not in ('event', 'loop', 'day')}, ensure_ascii=False)}")
        elif ev == "loop_result":
            print(f"  loop_result: {e.get('result')}")
        elif ev == "final_battle":
            print(f"  ⚔final_battle: {json.dumps({k: v for k, v in e.items() if k not in ('event', 'loop', 'day')}, ensure_ascii=False)}")
        elif ev == "game_over":
            print(f"  game_over: {json.dumps({k: v for k, v in e.items() if k not in ('event', 'loop', 'day')}, ensure_ascii=False)}")
    return 0


def cmd_why(args) -> int:
    """指定ループ/日の主人公セット手の採点表（agents.debug のプローブ＝挙動同一）。"""
    from agents.debug import probe_game, format_records

    sl = args.script_loops or (4 if args.days == 5 else 3)
    sc = random_script(args.set_name, args.seed, loops=sl, days=args.days)
    probe = replace(sc, loops=args.loops)
    state, hp_recs, mm_recs = probe_game(probe, seed=args.seed)
    print(f"=== why random_{args.set_name} s{args.seed} L{args.loop}D{args.day} ===")
    print("[主人公]")
    print(format_records(hp_recs, loop=args.loop, day=args.day, top=args.top))
    if args.mm:
        print("[脚本家]")
        from agents.debug import mastermind_mind_md
        print(mastermind_mind_md(mm_recs, loop=args.loop, day=args.day, top=args.top))
    return 0


def cmd_classify(args) -> int:
    """postmortem の1日分だけを分類（コンテナ再起動に耐えるチャンク実行用）。

    --loop/--day 指定＝その日だけ classify_day。--list で決定打の日を列挙のみ。"""
    from arena.postmortem import (replay_with_snapshots, _mm_set_of,
                                  _lost_loops, _decisive_days)
    from sim.mate import classify_day

    sl = args.script_loops or (4 if args.days == 5 else 3)
    sc = random_script(args.set_name, args.seed, loops=sl, days=args.days)
    state, log, snaps = replay_with_snapshots(sc, args.seed, loops=args.loops)
    if args.list:
        for loop in _lost_loops(state):
            for day, objective in _decisive_days(state, loop):
                print(f"L{loop}D{day} {objective}")
        return 0
    objective = args.objective
    if objective == "no_death":
        objective = ("no_death", frozenset([args.victim]))
    snap = snaps.get((args.loop, args.day))
    mm_set = _mm_set_of(log, args.loop, args.day)
    if snap is None or not mm_set:
        print("snap/mm_set が無い（日付を確認）")
        return 1
    res = classify_day(snap, mm_set=mm_set, objective=objective)
    d = res.get("defense")
    extra = ""
    if d:
        extra = " 防御例: " + " / ".join(f"{p['card']}→{p['target']}" for p in d)
    print(f"L{args.loop}D{args.day} [{objective}]: {res['verdict']}"
          f"（中身{res['n_contents']}通り×応手{res['n_responses']}通り）{extra}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rem6 残6局の検死プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["overview", "why", "classify"])
    ap.add_argument("--objective", default="survive",
                    choices=["survive", "no_incident", "no_death"])
    ap.add_argument("--victim", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--mm", action="store_true")
    ap.add_argument("--set", dest="set_name", choices=("FS", "BTX"), default="FS")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--script-loops", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"overview": cmd_overview, "why": cmd_why,
            "classify": cmd_classify}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
