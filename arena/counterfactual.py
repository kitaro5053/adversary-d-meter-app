# -*- coding: utf-8 -*-
"""反実仮想リプレイ — 「あの日こう指していたら勝てたか」をベンチ相手に実測する。

sim/mate.py（日単位の完全判定）との役割分担：
- mate.py … ゲーム理論的な確定（∀中身/∀応手の量化）。ただし**日単位**＝
  「単日では守れるのに複数日の組で負ける」ケース（BTX_4実測：D4の遅延配達）は
  単日分類では捕まらない。
- 本モジュール … 日dの主人公応手を差し替えて、**実際のベンチ相手（決定的AI）**と
  ループ末まで打たせる。量化なし＝「このmm相手に」限定の実用解。逸脱1日で
  勝ちに変わる手（flip）を全応手から機械探索できる。単日で flip が無ければ
  2日連鎖（候補上位K×K）へ拡張する使い方を想定。

健全性の注意：ここで「flipなし」が出ても詰みの証明ではない（相手は固定戦略・
逸脱も限定的）。逆に flip が見つかれば「このmm相手には勝てた」は確定＝
採点修正の当たり所として使える（存在証明側は常に健全、の原則）。

決定性の前提：agents は view（history込み）だけから決める＝スナップショットから
新品のエージェントで継続しても、差し替えの無い決定は元対局と一致する
（PYTHONHASHSEED=0 必須）。同日のmmセットは主人公セットより先＝差し替えの影響を
受けず元対局と同一になる。

CLI:
    PYTHONHASHSEED=0 python -m arena.counterfactual --set BTX --seed 4 --days 5 \
        --loop 4 --day 3            # L4D3の全応手を試す
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import flow
from sim.mate import _order_responses, _slim, enum_prot_sets
from sim.state import PROTAGONIST_SEATS
from arena.postmortem import fast_copy as _fast_copy


def _mk_decider(state, agents, forced: dict):
    """flow._make_decider 相当＋主人公 set_card の差し替え。

    forced: {(loop, day): [option, ...]} — その日の主人公セットを先頭から消費。
    差し替えは options に実在する合法手のみ（実在しなければ RuntimeError＝
    候補は enum_prot_sets（合法手フロンティア）から作るので通常起きない）。
    """
    from sim.views import mastermind_view, protagonist_view
    queues = {k: list(v) for k, v in forced.items()}

    def decide(actor, decision, options):
        if not options:
            raise RuntimeError(f"{actor} の {decision} に合法手が無い")
        key = (state.loop_no, state.day)
        if actor != "mastermind" and decision == "set_card" and queues.get(key):
            want = queues[key].pop(0)
            for o in options:
                if o == want:
                    return o
            raise RuntimeError(f"差し替え手が非合法: {want}")
        view = mastermind_view(state) if actor == "mastermind" \
            else protagonist_view(state, actor)
        return agents[actor].decide(view, decision, options)

    return decide


def continue_loop(snapshot, seed: int, forced: dict,
                  mm_params: dict | None = None) -> bool:
    """日開始スナップショットからこのループの残りを打ち切り、防衛できたかを返す。

    snapshot は arena.postmortem.replay_with_snapshots の (loop, day) スナップ。
    エージェントは新品（決定は view 依存＝履歴から復元される）。
    """
    st = _fast_copy(snapshot)
    mm = HeuristicMastermind(seed, params=mm_params)
    hp = HeuristicProtagonist(seed)
    agents = {"mastermind": mm, **{s: hp for s in PROTAGONIST_SEATS}}
    decide = _mk_decider(st, agents, forced)
    while True:
        flow.run_day(st, decide)
        if st.loop_end_triggered or st.day >= st.script.days_per_loop:
            break
        st.day += 1
    flow.evaluate_loop_end(st)
    return not st.defeat


def find_flip(snapshot, seed: int, mm_set: list[dict],
              max_candidates: int | None = None,
              mm_params: dict | None = None):
    """日dの主人公応手の差し替え1つで、このループの防衛に反転する手を探す。

    mm_set＝その日の実mmセット（同日のσは差し替えの影響を受けない＝これで
    応手の合法列挙ができる）。返り値: (flip応手 or None, 試行数)。
    """
    base = _slim(snapshot)
    responses = enum_prot_sets(base, mm_set)
    if responses is None:
        return None, 0
    responses = _order_responses(base, responses)   # 防御になりやすい手を先頭へ
    key = (snapshot.loop_no, snapshot.day)
    tried = 0
    for R in responses:
        if max_candidates is not None and tried >= max_candidates:
            break
        tried += 1
        if continue_loop(snapshot, seed, {key: R}, mm_params=mm_params):
            return R, tried
    return None, tried


def find_flip_2days(snap_d1, seed: int, mm_set_d1: list[dict],
                    top_k: int = 40):
    """2日連鎖の逸脱探索：日dの応手×上位K → 翌日はその局面で再列挙して×上位K。

    翌日のmmセットは逸脱に反応して変わるため、日d+1 の候補列挙は
    「日dを差し替えて1日進めた実局面」で行う（列挙の合法性を保つ）。
    """
    base = _slim(snap_d1)
    responses = enum_prot_sets(base, mm_set_d1)
    if responses is None:
        return None
    responses = _order_responses(base, responses)
    key1 = (snap_d1.loop_no, snap_d1.day)
    for R1 in responses[:top_k]:
        # 日dを差し替えて1日だけ進め、翌日開始局面を得る
        st = _fast_copy(snap_d1)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        agents = {"mastermind": mm, **{s: hp for s in PROTAGONIST_SEATS}}
        decide = _mk_decider(st, agents, {key1: list(R1)})
        flow.run_day(st, decide)
        if st.loop_end_triggered:
            flow.evaluate_loop_end(st)
            if not st.defeat:
                return {key1: R1}
            continue
        if st.day >= st.script.days_per_loop:
            flow.evaluate_loop_end(st)
            if not st.defeat:
                return {key1: R1}
            continue
        st.day += 1
        # 翌日のmmセットを覗く（mmは主人公より先にセット＝差し替え非依存で確定）
        probe = _fast_copy(st)
        sigma2: list[dict] = []
        mm2 = HeuristicMastermind(seed)   # 1インスタンス＝席間協調状態を保つ

        class _Stop(Exception):
            pass

        def _peek(actor, decision, options):
            if actor == "mastermind":
                from sim.views import mastermind_view
                ch = mm2.decide(mastermind_view(probe), decision, options)
                if decision == "set_card":
                    sigma2.append(ch)
                return ch
            raise _Stop

        try:
            flow.run_day(probe, _peek)
        except _Stop:
            pass
        key2 = (st.loop_no, st.day)
        base2 = _slim(st)
        responses2 = enum_prot_sets(base2, list(sigma2))
        if responses2 is None:
            continue
        responses2 = _order_responses(base2, responses2)
        for R2 in responses2[:top_k]:
            if continue_loop(st, seed, {key2: R2}):
                return {key1: R1, key2: R2}
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="反実仮想リプレイ（逸脱手の機械探索）")
    ap.add_argument("--set", dest="set_name", choices=("FS", "BTX"), default="BTX")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--loop", type=int, required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--two-days", action="store_true", help="2日連鎖の逸脱探索")
    ap.add_argument("--top-k", type=int, default=40)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    from sim import random_script
    from arena.postmortem import replay_with_snapshots, _mm_set_of
    sc = random_script(args.set_name, args.seed,
                       loops=(4 if args.days == 5 else 3), days=args.days)
    state, log, snaps = replay_with_snapshots(sc, args.seed, loops=args.loops)
    snap = snaps.get((args.loop, args.day))
    if snap is None:
        print(f"スナップショット無し: L{args.loop}D{args.day}")
        return
    mm_set = _mm_set_of(log, args.loop, args.day)
    if args.two_days:
        found = find_flip_2days(snap, args.seed, mm_set, top_k=args.top_k)
        if found:
            for (lp, dy), R in sorted(found.items()):
                print(f"flip L{lp}D{dy}: " + " / ".join(
                    f"{p['card']}→{p['target']}" for p in R))
        else:
            print("2日連鎖でも flip なし（このmm相手・上位K内）")
    else:
        R, tried = find_flip(snap, args.seed, mm_set)
        if R:
            print(f"flip 発見（{tried}試行）: " + " / ".join(
                f"{p['card']}→{p['target']}" for p in R))
        else:
            print(f"単日の差し替えでは flip なし（{tried}応手を全て試行）")


if __name__ == "__main__":
    main()
