"""B-199 プローブ：loop_race の暗躍供給の勘定を局面ごとに開示する（読み取り専用・計測専用）。

使い方:
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b199_probe one --set BTX --seed 10 --days 5
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b199_probe scan --days 5
"""
from __future__ import annotations

import argparse

from sim.generator import random_script
from sim.loop_race import analyze_script, describe_report
from sim.script_quality import incident_feasibility
from engine.data import forbidden_of, initial_area_of


BOARD_FEED_INCIDENTS = {"邪気の汚染": 2, "行方不明": 1}


def _goal_boards_of(script) -> set:
    from sim.state import GameState
    from sim.loop_race import _goal_boards
    st = GameState(script=script)
    dyn = {n: "都市" for n in script.cast if initial_area_of(n) is None}
    st.prepare_loop(dyn)
    return _goal_boards(st)


def _missing_boards(script, culprit: str) -> set:
    """行方不明の供給先候補＝犯人の禁止エリアを除く全ボード（A-78 の規則）。"""
    from engine.board import AREAS
    forb = forbidden_of(culprit) or frozenset()
    return set(AREAS) - set(forb)


def board_feed_rows(script) -> list[dict]:
    """ボード暗躍を供給する事件の一覧（供給先・grade つき）。"""
    goals = _goal_boards_of(script)
    out = []
    for f in incident_feasibility(script):
        if f.name not in BOARD_FEED_INCIDENTS:
            continue
        if f.name == "邪気の汚染":
            boards = {"神社"}
        else:
            boards = _missing_boards(script, f.culprit)
        out.append({"day": f.day, "name": f.name, "culprit": f.culprit,
                    "grade": f.grade, "boards": boards, "amount": BOARD_FEED_INCIDENTS[f.name],
                    "goals": goals, "hits": boards & goals})
    return out


def cmd_one(args):
    sc = random_script(args.set, args.seed, days=args.days)
    print(f"=== {args.set} seed={args.seed} days={args.days} ===")
    print(f"rule_y={sc.rule_y} / rule_xs={list(sc.rule_xs)}")
    print("配役: " + " / ".join(f"{n}={sc.role_of(n)}" for n in sc.cast))
    print("事件: " + " / ".join(f"D{i.day} {i.name}(犯人{i.culprit})"
                                for i in sc.incidents))
    print(f"goal_boards={_goal_boards_of(sc)}")
    print("--- incident_feasibility（全事件）---")
    for f in incident_feasibility(sc):
        print(f"  D{f.day} {f.name} 犯人{f.culprit} 臨界{f.threshold} "
              f"uncontested={f.mm_uncontested} contested={f.mm_contested} grade={f.grade}")
    print("--- ボード供給事件（KB: 邪気の汚染+2 / 行方不明+1）---")
    for r in board_feed_rows(sc):
        print(f"  D{r['day']} {r['name']} 犯人{r['culprit']} grade={r['grade']} "
              f"+{r['amount']} 供給先={sorted(r['boards'])} goal={sorted(r['goals'])} "
              f"→ゴール板へ届く={sorted(r['hits'])}")
    print("--- loop_race.analyze_script ---")
    for line in describe_report(analyze_script(sc)):
        print(line)


def cmd_scan(args):
    """両ベンチのコーパスで、事件由来のボード供給がゴール板に届く局を数える。"""
    from arena.benchmark import benchmark_scripts
    n_hit = 0
    rows = []
    for name, seed, sc in benchmark_scripts(days=args.days):
        verdict = analyze_script(sc).verdict
        for r in board_feed_rows(sc):
            if not r["hits"]:
                continue
            rows.append((name, seed, r["day"], r["name"], r["culprit"], r["grade"],
                         sorted(r["hits"]), verdict))
        if rows and rows[-1][0] == name and rows[-1][1] == seed:
            n_hit += 1
    print(f"=== days={args.days}: ゴール板へ届くボード供給事件 {len(rows)} 件 ===")
    for r in rows:
        print(f"  {r[0]}#{r[1]} D{r[2]} {r[3]}(犯人{r[4]}) grade={r[5]} "
              f"→{r[6]} / verdict={r[7]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("one")
    p1.add_argument("--set", default="BTX")
    p1.add_argument("--seed", type=int, default=10)
    p1.add_argument("--days", type=int, default=5)
    p1.set_defaults(func=cmd_one)
    p2 = sub.add_parser("scan")
    p2.add_argument("--days", type=int, default=5)
    p2.set_defaults(func=cmd_scan)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
