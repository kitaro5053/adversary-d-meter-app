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

★B-223（2026-08-14・B-220 実測の移植）：上の前提には既知の例外がある＝
HeuristicMastermind._pick は同点を self.rng（view外の状態）でタイブレークし、
option 1つにつき rng を1回消費する。∴ 新品 mm はスナップショット時点と **rng 位置**が
ずれ、同点帯で決定が原局面と割れる（B-220 実測＝非防衛6局中4局で不一致）。
是正＝replay_with_snapshots(rng_states=...) で各日の mm rng 状態を取り、
continue_loop / find_flip / find_flip_2days に mm_rng_state として渡す＝
B-220 で6局全ての set_card 決定列が bit 一致することを実測済み。
mm_rng_state 省略時は従来挙動（新品 rng・復元なし）のまま＝既存呼び出し元
（arena/b140fu_audit 等）は不変。CLI は既定で復元を使う（--fresh-rng で従来挙動）。

CLI:
    PYTHONHASHSEED=0 python -m arena.counterfactual --set BTX --seed 4 --days 5 \
        --loop 4 --day 3            # L4D3の全応手を試す
    PYTHONHASHSEED=0 python -m arena.counterfactual --sample btx_bomb --seed 0 \
        --loop 1 --day 3            # 手書きサンプル脚本のCF裏取り（--set/--daysは脚本から）
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

    # ★2026-08-14（B-214/B-215 の既定 ON 化）：`flow._make_decider` と同じく脚本家の
    #   `wants_bluff_options` を載せる。これを忘れると `run_day` が作る脚本家の候補列が
    #   原局面と変わり、rng の消費がずれて**反実仮想リプレイが原局面と割れる**
    #   （＝本関数は「flow._make_decider 相当」を名乗る以上、配線も相当でなければならない）。
    return flow.attach_mm_bluff(decide, agents)


def continue_loop(snapshot, seed: int, forced: dict,
                  mm_params: dict | None = None,
                  mm_rng_state=None) -> bool:
    """日開始スナップショットからこのループの残りを打ち切り、防衛できたかを返す。

    snapshot は arena.postmortem.replay_with_snapshots の (loop, day) スナップ。
    エージェントは新品（決定は view 依存＝履歴から復元される）。
    ★B-223 mm_rng_state＝replay_with_snapshots(rng_states=...) が記録した同 (loop, day) の
    脚本家 rng 状態。渡すと原局面と同じ rng 位置から継続する（同点帯でも bit 再現）。
    省略時は従来どおり新品 rng（同点帯が濃い局では mm の手が原局面と割れうる＝
    「このmm相手」の限定が弱まる。flip の存在証明自体は崩れない）。
    """
    st = _fast_copy(snapshot)
    mm = HeuristicMastermind(seed, params=mm_params)
    if mm_rng_state is not None:
        mm.rng.setstate(mm_rng_state)
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
              mm_params: dict | None = None,
              mm_rng_state=None):
    """日dの主人公応手の差し替え1つで、このループの防衛に反転する手を探す。

    mm_set＝その日の実mmセット（同日のσは差し替えの影響を受けない＝これで
    応手の合法列挙ができる）。返り値: (flip応手 or None, 試行数)。
    mm_rng_state＝continue_loop と同じ（★B-223・渡すと原局面 bit 再現の上での探索）。
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
        if continue_loop(snapshot, seed, {key: R}, mm_params=mm_params,
                         mm_rng_state=mm_rng_state):
            return R, tried
    return None, tried


def find_flip_2days(snap_d1, seed: int, mm_set_d1: list[dict],
                    top_k: int = 40, mm_rng_state=None):
    """2日連鎖の逸脱探索：日dの応手×上位K → 翌日はその局面で再列挙して×上位K。

    翌日のmmセットは逸脱に反応して変わるため、日d+1 の候補列挙は
    「日dを差し替えて1日進めた実局面」で行う（列挙の合法性を保つ）。
    ★B-223 mm_rng_state＝日dの脚本家 rng 状態。渡すと日dを原局面の rng 位置から進め、
    日d+1 の覗き見と継続にも**日d通過後の rng 状態**を引き継ぐ（覗いたσと継続で
    実際に置かれるσの一致を保つ）。省略時は従来どおり全て新品 rng。
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
        if mm_rng_state is not None:
            mm.rng.setstate(mm_rng_state)
        hp = HeuristicProtagonist(seed)
        agents = {"mastermind": mm, **{s: hp for s in PROTAGONIST_SEATS}}
        decide = _mk_decider(st, agents, {key1: list(R1)})
        flow.run_day(st, decide)
        rng2 = mm.rng.getstate() if mm_rng_state is not None else None
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
        if rng2 is not None:
            mm2.rng.setstate(rng2)        # 継続（continue_loop）と同じ rng 位置で覗く

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

        # ★B-223：覗き見の decide にも `mm_allow_bluff` を配線する（`run_day` は decide
        #   関数の属性を見る＝載せ忘れると mm2 の候補列が閉じ、覗いたσ2 が
        #   continue_loop（＝_mk_decider 経由で配線済み）で実際に置かれるσと割れる。
        #   `sim/flow.attach_mm_bluff` docstring の [[wiring-forgotten-across-paths]] と同型）。
        flow.attach_mm_bluff(_peek, {"mastermind": mm2})

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
            if continue_loop(st, seed, {key2: R2}, mm_rng_state=rng2):
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
    # ★2026-07-28：手書きサンプル脚本のCF裏取りを1コマンドで回す（新era検死の申し送り）。
    #   帯ごと負けている手書き脚本（btx_bomb/btx_seal/fs5_guard 等）の検証が主用途。
    #   --seed は生成ではなく**エージェントのseed**として使う（サンプルは脚本が固定なので）。
    ap.add_argument("--sample", type=str, default=None,
                    help="手書きサンプル脚本名（例 btx_bomb）。指定時 --set/--days は脚本から取る")
    # ★B-223：既定＝mm rng 状態を復元して原局面 bit 再現の上で探索する（B-220 の是正）。
    ap.add_argument("--fresh-rng", action="store_true",
                    help="従来挙動＝mm rng を復元しない（新品エージェントで継続）")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    from sim import random_script
    from arena.postmortem import replay_with_snapshots, _mm_set_of
    if args.sample:
        from sim.sample_scripts import SAMPLE_SCRIPTS
        if args.sample not in SAMPLE_SCRIPTS:
            print(f"未知のサンプル脚本: {args.sample}\n"
                  f"利用可能: {', '.join(sorted(SAMPLE_SCRIPTS))}", file=sys.stderr)
            return
        sc = SAMPLE_SCRIPTS[args.sample]()
    else:
        sc = random_script(args.set_name, args.seed,
                           loops=(4 if args.days == 5 else 3), days=args.days)
    rngs: dict = {}
    state, log, snaps = replay_with_snapshots(sc, args.seed, loops=args.loops,
                                              rng_states=rngs)
    snap = snaps.get((args.loop, args.day))
    if snap is None:
        print(f"スナップショット無し: L{args.loop}D{args.day}")
        return
    rng_state = None if args.fresh_rng else rngs.get((args.loop, args.day))
    if rng_state is not None:
        print("（mm rng 状態を復元＝原局面 bit 再現の上での探索。従来挙動は --fresh-rng）")
    mm_set = _mm_set_of(log, args.loop, args.day)
    if args.two_days:
        found = find_flip_2days(snap, args.seed, mm_set, top_k=args.top_k,
                                mm_rng_state=rng_state)
        if found:
            for (lp, dy), R in sorted(found.items()):
                print(f"flip L{lp}D{dy}: " + " / ".join(
                    f"{p['card']}→{p['target']}" for p in R))
        else:
            print("2日連鎖でも flip なし（このmm相手・上位K内）")
    else:
        R, tried = find_flip(snap, args.seed, mm_set, mm_rng_state=rng_state)
        if R:
            print(f"flip 発見（{tried}試行）: " + " / ".join(
                f"{p['card']}→{p['target']}" for p in R))
        else:
            print(f"単日の差し替えでは flip なし（{tried}応手を全て試行）")


if __name__ == "__main__":
    main()
