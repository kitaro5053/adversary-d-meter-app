# -*- coding: utf-8 -*-
"""敗局の検死（postmortem）— 1手詰め判定でベンチの負けを責任分類する。

ベンチ（arena/benchmark.py）と同一条件でゲームを再現（決定的＝要 PYTHONHASHSEED=0）、
各日開始局面をスナップショットし、負けたループの決定打の日を sim/mate.classify_day で
完全判定する：
- defense_exists … その日のσに対し守れる応手があった＝**AIのミス**（防御手つき）
- mate           … σを見た時点でどう指しても負け＝AIの責任ではない（前日以前かレースの問題）
- guessing       … 読み合い（2択を通された類＝ユーザー方針で許容）

分類する日：
- 死亡/TT系の即負け → その日（objective="survive"）
- ループ終了時のボード条件負け → そのループで発生した各事件の日（objective="no_incident"）
  ＋最終日（objective="survive"）。ボードレースは複数日の合算なので「事件を止める手は
  あったか」を日ごとに問う（1件止めれば結果が変わったかまでは踏み込まない＝診断情報）。

CLI:
    PYTHONHASHSEED=0 python -m arena.postmortem --set FS --seed 6 --days 5 --loops 8
    PYTHONHASHSEED=0 python -m arena.postmortem --sample btx_seal --seed 3
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game
from sim.mate import classify_day


def fast_copy(state):
    """履歴を共有する高速コピー。履歴イベントdictは追記専用＝共有しても安全。
    deepcopy の支配項（数千イベント×日数×ループ数＝2乗）を外す。"""
    h, s, p = state.history, state.secret_log, state.phase_snapshots
    state.history = []
    state.secret_log = []
    state.phase_snapshots = []
    try:
        st = copy.deepcopy(state)
    finally:
        state.history, state.secret_log, state.phase_snapshots = h, s, p
    st.history = list(h)
    st.secret_log = list(s)
    st.phase_snapshots = list(p)
    return st


def replay_with_snapshots(script, seed: int, loops: int = 8,
                          mm_params: dict | None = None,
                          rng_states: dict | None = None):
    """ベンチ（loops_to_win）と同一条件で再現し、日開始スナップショットを添える。

    ★B-223：rng_states に dict を渡すと、各日開始時点の**脚本家 rng 状態**
    （(loop, day) → mm.rng.getstate()）も書き込む。反実仮想リプレイの原局面 bit 再現
    （counterfactual.continue_loop の mm_rng_state）に使う＝B-220 実測で
    「新品エージェント継続は同点帯で原局面と割れる」（`_pick` が option 1つにつき
    rng を1回消費＝rng 位置がずれる）ことが確定したため。省略時は従来と同一。"""
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed, params=mm_params)
    hp = HeuristicProtagonist(seed)
    snaps: dict[tuple[int, int], object] = {}

    def on_day(state):
        snaps[(state.loop_no, state.day)] = fast_copy(state)
        if rng_states is not None:
            rng_states[(state.loop_no, state.day)] = mm.rng.getstate()

    state, log = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                          on_day_start=on_day)
    return state, log, snaps


def _mm_set_of(log, loop: int, day: int) -> list[dict]:
    return [e["chosen"] for e in log
            if e["actor"] == "mastermind" and e["decision"] == "set_card"
            and e["loop"] == loop and e["day"] == day]


def _lost_loops(state) -> list[int]:
    lost = {e["loop"] for e in state.history
            if e.get("event") == "loop_result" and "敗北" in str(e.get("result"))}
    # 最終ループの敗北は loop_result でなく game_over/final_battle 側に出る
    if state.winner == "mastermind" or any(
            e.get("event") == "final_battle" for e in state.history):
        lost.add(state.loop_no)
    return sorted(lost)


def _decisive_days(state, loop: int) -> list[tuple[int, object]]:
    """そのループで分類すべき (day, objective) を列挙する。"""
    days: list[tuple[int, object]] = []
    end_day = None
    friends = {n for n, r in state.script.roles.items() if r == "フレンド"}
    for e in state.history:
        if e.get("loop") != loop:
            continue
        if e.get("event") == "loop_end":
            end_day = e.get("day")
        if e.get("event") == "incident" and e.get("occurs"):
            days.append((e["day"], "no_incident"))
        if e.get("event") == "death" and e.get("name") in friends:
            # フレンド死亡＝ループ終了時敗北の決定打（surviveでは捕まらない）
            days.append((e["day"], ("no_death", frozenset([e["name"]]))))
    if end_day:                      # 死亡/TT系＝日中に loop_end が立った
        days.append((end_day, "survive"))
    else:                            # ループ終了時のボード条件負け＝最終日も見る
        days.append((state.script.days_per_loop, "survive"))
    return sorted(set(days), key=lambda t: (t[0], str(t[1])))


def postmortem(script, seed: int, loops: int = 8, verbose: bool = True) -> list[dict]:
    state, log, snaps = replay_with_snapshots(script, seed, loops=loops)
    reports: list[dict] = []
    for loop in _lost_loops(state):
        for day, objective in _decisive_days(state, loop):
            snap = snaps.get((loop, day))
            mm_set = _mm_set_of(log, loop, day)
            if snap is None or not mm_set:
                continue
            res = classify_day(snap, mm_set=mm_set, objective=objective)
            row = {"loop": loop, "day": day, "objective": objective, **res}
            reports.append(row)
            if verbose:
                d = res.get("defense")
                extra = ""
                if d:
                    extra = " 防御例: " + " / ".join(
                        f"{p['card']}→{p['target']}" for p in d)
                print(f"  L{loop}D{day} [{objective}]: {res['verdict']}"
                      f"（中身{res['n_contents']}通り×応手{res['n_responses']}通り）"
                      f"{extra}", flush=True)
    return reports


def main(argv=None):
    ap = argparse.ArgumentParser(description="敗局の検死（1手詰め分類）")
    ap.add_argument("--set", dest="set_name", choices=("FS", "BTX"), default="FS")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--script-loops", type=int, default=None,
                    help="random_script の loops（既定: 3日=3, 5日=4）")
    ap.add_argument("--sample", type=str, default=None, help="サンプル脚本名を使う")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください（再現が壊れます）。", file=sys.stderr)
    if args.sample:
        from sim.sample_scripts import SAMPLE_SCRIPTS
        sc = SAMPLE_SCRIPTS[args.sample]()
    else:
        from sim import random_script
        sl = args.script_loops or (4 if args.days == 5 else 3)
        sc = random_script(args.set_name, args.seed, loops=sl, days=args.days)
    print(f"検死: {args.sample or args.set_name} seed={args.seed} "
          f"days={sc.days_per_loop} loops={args.loops}")
    reports = postmortem(sc, args.seed, loops=args.loops)
    if not reports:
        print("  負けループなし（全防衛）")


if __name__ == "__main__":
    main()
