# -*- coding: utf-8 -*-
"""B-124 計測：**A-77 押し切りの門番（`_ACCOUNTED_INCIDENTS`）が勝ち筋を見ていない**件を
「行為の数え上げ」で測る（計測専用・本番経路は無変更）。

起票元＝`docs/監査_A78_行方不明の供給会計_2026-07-30.md` §2b。
`agents/heuristic.py` の `_ACCOUNTED_INCIDENTS` は「事件の効果が勝ち筋の会計に載っている」
ことを表す**静的集合**でしかなく、その事件の供給が**実際に生きた勝ち筋へ届くか**
（ゴール板が存在するか／届く板か）を見ていない。

★主指標をベンチの防衛数にしない理由＝`docs/エージェント共通規約.md` §11b
  （並び順の置換だけで防衛数は 3日級σ3.4／5日級σ4.1 動く＝±1〜2局は証拠にならない）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b124_audit push --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b124_audit a78 --days 3
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace

import agents.heuristic as _mm_mod
from sim.state import missing_incident_boards_from_view

# 事件がどこへ暗躍を供給するか（KB 行番号は監査doc §2 の表）。
_BOARD_INCIDENTS = {"行方不明", "邪気の汚染", "病院の事件"}
_CHAR_INCIDENTS = {"不安拡大"}


def _damage_expressible(view, goal_boards, name, culprit) -> bool | None:
    """今日の事件の効果が、**生きた勝ち筋の会計に載る**か（True/False）。

    None＝この事件は `_ACCOUNTED_INCIDENTS` の外（判定対象外）。
    """
    if name == "行方不明":
        if not goal_boards:
            return False
        return bool(missing_incident_boards_from_view(view, culprit) & set(goal_boards))
    if name == "邪気の汚染":
        return "神社" in goal_boards
    if name == "病院の事件":
        return "病院" in goal_boards
    if name == "不安拡大":
        # キャラ暗躍の受け皿（KP／キラー／メインラバーズ／フレンド）が生存しているか。
        roles = view.get("roles", {})
        chars = {c["name"]: c for c in view["characters"]}
        tgts = [n for n, r in roles.items()
                if r in ("キーパーソン", "キラー", "メインラバーズ", "フレンド")]
        return any(chars.get(n, {}).get("alive") for n in tgts)
    if name == "遠隔殺人":
        # A-56②/A-67 の利得ゲートを `today_gainful` が既に通している＝ここでは真扱い。
        return True
    return None


def run_push(days: int, loops: int = 8, legacy: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    s = Counter()
    by_game = Counter()
    detail = Counter()
    cur = {"key": None}

    def probe(view, goal_boards, inc_name, culprit, gainful, push_culprit, info):
        s["seats"] += 1
        if info is None:
            return
        s["push_block_seats"] += 1
        if info["acct_ok"]:
            s["acct_ok_seats"] += 1
        # A-77 分岐が「会計ゲート以外は全部通る」席（＝ゲートが実際に効く席）
        cand = info["pre_ok"] and info["room_ok"] and info["thr_ok"]
        if cand:
            s["a77_candidate_seats"] += 1
        if info["a77_fired"]:
            s["a77_fired_seats"] += 1
            dmg = _damage_expressible(view, goal_boards, inc_name, culprit)
            if dmg is False:
                s["a77_fired_no_damage"] += 1
                if not goal_boards and inc_name in _BOARD_INCIDENTS:
                    s["case_a_no_goal_board"] += 1
                else:
                    s["case_b_not_reaching"] += 1
                by_game[cur["key"]] += 1
                detail[(inc_name, culprit, tuple(sorted(goal_boards)))] += 1
        if cand and not info["acct_ok"]:
            s["gate_blocked_seats"] += 1
            detail[("BLOCKED", inc_name, ())] += 1

    _mm_mod.B124_PROBE = probe
    _mp = {"push_goal_aligned": 0.0} if legacy else None
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            cur["key"] = f"{name}#{seed}"
            s["games"] += 1
            mm = HeuristicMastermind(seed, params=_mp)
            hp = HeuristicProtagonist(seed)
            run_game(replace(sc, loops=loops),
                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        _mm_mod.B124_PROBE = None
    return {"s": s, "by_game": by_game, "detail": detail}


def format_push(res: dict, days: int) -> str:
    s = res["s"]
    out = [f"== B-124 押し切り会計（{days}日級・{s['games']}局） ==",
           f"  `_analyze` 席                                 : {s['seats']}",
           f"  押し切り判定に入った席（犯人生存×臨界≥1）    : {s['push_block_seats']}",
           f"  うち `_ACCOUNTED_INCIDENTS` を通った席        : {s['acct_ok_seats']}",
           f"  A-77 分岐の候補席（会計ゲート以外は全通過）   : {s['a77_candidate_seats']}",
           f"  ★A-77 分岐が押し切りを立てた席               : {s['a77_fired_seats']}",
           f"  ★会計ゲートが実際に止めた席                  : {s['gate_blocked_seats']}",
           f"  ★★立てたが打点が勝ち筋に載らない席          : {s['a77_fired_no_damage']}",
           f"      (a) ゴール板が存在しない局              : {s['case_a_no_goal_board']}",
           f"      (b) 供給先がゴール板でない              : {s['case_b_not_reaching']}"]
    if res["by_game"]:
        out.append("  局別（多い順・上位15）:")
        for k, v in res["by_game"].most_common(15):
            out.append(f"    {k:24s} {v:5d} 席")
    if res["detail"]:
        out.append("  内訳（事件・犯人・ゴール板）:")
        for k, v in res["detail"].most_common(20):
            out.append(f"    {k}: {v} 席")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# A-78 §2b の「383席」の再現（供給の母数側＝ゴール板が空の局で行方不明を数えた席）
# ---------------------------------------------------------------------------
def run_a78_nogoal(days: int, loops: int = 8) -> dict:
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    s = Counter()
    by_game = Counter()
    cur = {"key": None}

    def probe(view, goal_boards, old, new):
        s["seats"] += 1
        if old <= 0:
            return
        s["seats_with_missing"] += 1
        if not goal_boards:
            s["no_goal_board_seats"] += 1
            by_game[cur["key"]] += 1

    _mm_mod.A78_PROBE = probe
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            cur["key"] = f"{name}#{seed}"
            s["games"] += 1
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(replace(sc, loops=loops),
                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        _mm_mod.A78_PROBE = None
    return {"s": s, "by_game": by_game}


def format_a78(res: dict, days: int) -> str:
    s = res["s"]
    out = [f"== A-78 §2b 再現（{days}日級・{s['games']}局） ==",
           f"  `_analyze` 席                          : {s['seats']}",
           f"  行方不明を供給の母数に載せた席        : {s['seats_with_missing']}",
           f"  ★うち goal_boards が空の席            : {s['no_goal_board_seats']}"]
    if res["by_game"]:
        out.append("  局別（多い順・上位10）:")
        for k, v in res["by_game"].most_common(10):
            out.append(f"    {k:24s} {v:5d} 席")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-124 計測（押し切り会計のゴール板整合）")
    ap.add_argument("mode", choices=["push", "a78"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--legacy", action="store_true",
                    help="push_goal_aligned=0（B-124 前＝A-77 landed）の軌跡で数える")
    a = ap.parse_args(argv)
    if a.mode == "push":
        print(format_push(run_push(a.days, a.loops, a.legacy), a.days))
    else:
        print(format_a78(run_a78_nogoal(a.days, a.loops), a.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
