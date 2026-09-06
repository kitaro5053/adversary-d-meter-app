# -*- coding: utf-8 -*-
"""B-222：供給役の分離・板版（BTX#3 教材）の Phase 0 プローブ（読み取り専用）。

土台＝lane/b220 の arena/b220_probe.py（25c23d6）。B-220 の再現コマンド
（overview/decoys/classify/verifycf/flip/repeats/run6）をそのまま再利用し、
B-222 Phase 0 の計測コマンド（conds/survey）を追加した。

AI・エンジン（agents/ sim/ engine/）には一切触れない。フラグの ON/OFF は
クラス属性の実行時退避→復元だけ（ファイルは不変）。

CLI（B-222 追加分）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b222_probe conds --game random_BTX:3:5 --loop 3
    PYTHONHASHSEED=0 ... python -m arena.b222_probe survey --days 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import replace

ANYAKU = frozenset({"暗躍+1", "暗躍+2"})
KINSHI = "暗躍禁止"

#: (script名, seed, days)。ベンチ（arena.benchmark.benchmark_scripts）と同一の生成呼び出しを使う。
GAMES6 = [
    ("btx_future", 4, 3),
    ("random_BTX", 12, 3),
    ("random_FS", 10, 5),
    ("random_BTX", 0, 5),
    ("random_BTX", 3, 5),
    ("random_BTX", 10, 5),
]

CONDS = {
    # (B214_BOARD_DECOY, B215_DECOY_FIRST)
    "on": (True, True),      # 新物差し（既定）
    "off": (False, False),   # 旧物差し（複線演出なし）
    "b214": (True, False),
    "b215": (False, True),
}


def make_script(name: str, seed: int, days: int):
    """★ベンチと同一の脚本を作る（benchmark_scripts と同じ呼び出し形）。"""
    if name.startswith("random_"):
        from sim import random_script
        set_name = name.split("_", 1)[1]
        return random_script(set_name, seed, days=days)   # loops はベンチ同様デフォルト
    from sim.sample_scripts import SAMPLE_SCRIPTS
    return SAMPLE_SCRIPTS[name]()


@contextmanager
def mm_flags(b214: bool, b215: bool):
    from agents import HeuristicMastermind as HM
    old = (HM.B214_BOARD_DECOY, HM.B215_DECOY_FIRST)
    HM.B214_BOARD_DECOY, HM.B215_DECOY_FIRST = b214, b215
    try:
        yield
    finally:
        HM.B214_BOARD_DECOY, HM.B215_DECOY_FIRST = old


def play_one(name: str, seed: int, days: int, loops: int = 8):
    """ベンチ（loops_to_win）と同一条件で1局打ち、(ltw, outcome, state, log) を返す。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    sc = make_script(name, seed, days)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    log: list[dict] = []
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}, log=log)
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw = loops + 1
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        ltw, outcome = loops + 1, "loss"
    return ltw, outcome, state, log


def _turns_from_log(log: list[dict]):
    """(loop, day) → {"mm": [placements], "pro": [placements], "pv": 最初の主人公view}"""
    turns: dict = defaultdict(lambda: {"mm": [], "pro": [], "pv": None})
    for d in log:
        if d.get("decision") != "set_card":
            continue
        k = (d.get("loop"), d.get("day"))
        if d.get("actor") == "mastermind":
            turns[k]["mm"].append(d["chosen"])
        else:
            turns[k]["pro"].append(d["chosen"])
            if turns[k]["pv"] is None:
                turns[k]["pv"] = d.get("view")
    return turns


def decoy_rows(log: list[dict]):
    """1局のターン別：囮（板ダミー）が主人公の板ガードを吸ったか／通った暗躍。

    分類は arena.b214_ab._count_from_log と同じ規約：
      板への配置＝暗躍札(実弾)／幻想板の実効札／それ以外（＝ダミー）。
      吸われ＝主人公が 暗躍禁止 をダミー板へ置いた。
    """
    rows = []
    for k, t in sorted(_turns_from_log(log).items()):
        gen = {c.get("area") for c in ((t["pv"] or {}).get("characters") or [])
               if c.get("name") == "幻想" and c.get("alive") and c.get("area")}
        by: dict = defaultdict(list)
        for c in t["mm"]:
            by[(c["target_kind"], c["target"])].append(c["card"])
        kin = {(c["target_kind"], c["target"]) for c in t["pro"]
               if c.get("card") == KINSHI}
        dummy_boards, real_boards = set(), set()
        for (kind, tgt), cards in by.items():
            if kind != "board":
                continue
            for card in cards:
                if card in ANYAKU:
                    real_boards.add(tgt)
                elif tgt in gen:
                    real_boards.add(tgt)   # 幻想板の実効札＝実弾扱い
                else:
                    dummy_boards.add(tgt)
        sucked = sorted(dummy_boards & {t2 for k2, t2 in kin if k2 == "board"})
        through = [(kind, tgt, card) for (kind, tgt), cards in by.items()
                   for card in cards if card in ANYAKU and (kind, tgt) not in kin]
        blocked = [(kind, tgt, card) for (kind, tgt), cards in by.items()
                   for card in cards if card in ANYAKU and (kind, tgt) in kin]
        rows.append({
            "loop": k[0], "day": k[1],
            "mm": [(c["target_kind"], c["target"], c["card"]) for c in t["mm"]],
            "pro": [(c["target_kind"], c["target"], c["card"]) for c in t["pro"]],
            "dummy_boards": sorted(dummy_boards), "sucked": sucked,
            "anyaku_through": through, "anyaku_blocked": blocked,
        })
    return rows


def cmd_run6(args) -> int:
    from arena.b214_ab import _count_from_log
    conds = args.conds.split(",")
    for cname in conds:
        b214, b215 = CONDS[cname]
        print(f"=== 条件 {cname} (B214={b214} B215={b215}) ===")
        with mm_flags(b214, b215):
            for name, seed, days in GAMES6:
                if args.days and days != args.days:
                    continue
                ltw, outcome, state, log = play_one(name, seed, days,
                                                    loops=args.loops)
                acc: Counter = Counter()
                _count_from_log(log, acc)
                print(f"  {name}#{seed} d{days}: ltw={ltw} {outcome}"
                      f"  dummy={acc.get('mm_board_dummy', 0)}"
                      f" sucked={acc.get('kinshi_board_whiff_dummy', 0)}"
                      f" through={acc.get('mm_anyaku_through', 0)}"
                      f"/{acc.get('mm_anyaku_total', 0)}"
                      f" 板ガード={acc.get('kinshi_board_hit', 0)}hit"
                      f"/{acc.get('kinshi_board_total', 0)}", flush=True)
    return 0


def cmd_decoys(args) -> int:
    name, seed, days = args.game
    b214, b215 = CONDS[args.cond]
    with mm_flags(b214, b215):
        ltw, outcome, state, log = play_one(name, seed, days, loops=args.loops)
    print(f"=== {name}#{seed} d{days} [{args.cond}] ltw={ltw} {outcome} ===")
    for r in decoy_rows(log):
        mark = " ★吸われ" if r["sucked"] else ""
        if not (args.all or r["dummy_boards"] or r["anyaku_through"]
                or r["anyaku_blocked"]):
            continue
        print(f"L{r['loop']}D{r['day']}{mark}")
        print(f"  mm : " + " / ".join(f"{k}:{t}←{c}" for k, t, c in r["mm"]))
        print(f"  pro: " + " / ".join(f"{k}:{t}←{c}" for k, t, c in r["pro"]))
        if r["dummy_boards"]:
            print(f"  ダミー板={r['dummy_boards']} 吸われ={r['sucked']}")
        if r["anyaku_through"]:
            print(f"  通った暗躍: " + " / ".join(
                f"{k}:{t}←{c}" for k, t, c in r["anyaku_through"]))
        if r["anyaku_blocked"]:
            print(f"  止めた暗躍: " + " / ".join(
                f"{k}:{t}←{c}" for k, t, c in r["anyaku_blocked"]))
    return 0


def cmd_overview(args) -> int:
    name, seed, days = args.game
    sc = make_script(name, seed, days)
    b214, b215 = CONDS[args.cond]
    print(f"=== {name}#{seed} days={sc.days_per_loop} [{args.cond}] ===")
    print(f"[脚本] rule_y={getattr(sc, 'rule_y', None)}  rule_x={getattr(sc, 'rule_x', None)}")
    print("[配役]")
    for n, r in sc.roles.items():
        print(f"  {n}: {r}")
    print("[事件]")
    for inc in sc.incidents:
        print(f"  {inc}")
    with mm_flags(b214, b215):
        ltw, outcome, state, log = play_one(name, seed, days, loops=args.loops)
    fb = any(e.get("event") == "final_battle" for e in state.history)
    print(f"\n[結果] ltw={ltw} {outcome} winner={state.winner} final_battle={fb}")
    for e in state.history:
        ev = e.get("event")
        if ev == "loop_start":
            print(f"\n--- L{e.get('loop')} ---")
        elif ev == "incident" and e.get("occurs"):
            print(f"  D{e.get('day')} ⚡事件発生: {e.get('name')} "
                  f"(犯人={e.get('culprit', '?')})")
        elif ev == "incident" and args.verbose:
            print(f"  D{e.get('day')} （事件不発: {e.get('name')}）")
        elif ev == "death":
            print(f"  D{e.get('day')} 💀死亡: {e.get('name')} ({e.get('cause', '?')})")
        elif ev == "protagonist_death":
            print(f"  D{e.get('day')} 💀主人公死亡: "
                  + json.dumps({k: v for k, v in e.items()
                                if k not in ('event', 'loop', 'day')},
                               ensure_ascii=False))
        elif ev == "loop_end":
            print(f"  D{e.get('day')} loop_end: "
                  + json.dumps({k: v for k, v in e.items()
                                if k not in ('event', 'loop', 'day')},
                               ensure_ascii=False))
        elif ev == "loop_board":
            print(f"  loop_board: board={e.get('board_anyaku')} "
                  f"goodwill={e.get('char_goodwill', '')}")
        elif ev == "defeat":
            print(f"  D{e.get('day', '?')} ☠defeat: "
                  + json.dumps({k: v for k, v in e.items()
                                if k not in ('event', 'loop', 'day')},
                               ensure_ascii=False))
        elif ev == "loop_result":
            print(f"  loop_result: {e.get('result')}")
        elif ev == "final_battle":
            print(f"  ⚔final_battle: "
                  + json.dumps({k: v for k, v in e.items()
                                if k not in ('event', 'loop', 'day')},
                               ensure_ascii=False))
        elif ev == "game_over":
            print(f"  game_over: "
                  + json.dumps({k: v for k, v in e.items()
                                if k not in ('event', 'loop', 'day')},
                               ensure_ascii=False))
    return 0


def _replay_snaps(name: str, seed: int, days: int, loops: int):
    from arena.postmortem import replay_with_snapshots
    sc = make_script(name, seed, days)
    return replay_with_snapshots(sc, seed, loops=loops)


def _replay_snaps_rng(name: str, seed: int, days: int, loops: int):
    """replay_with_snapshots ＋ 各日開始時点の脚本家 rng 状態も保存する。

    ★B-220 の実測（verifycf）＝スナップショットから**新品エージェント**で継続すると、
    `_pick` の同点タイブレーク（option 1つにつき self.rng を1回消費）の **rng 位置**が
    原局面とずれ、同点帯で決定が割れる（6局中4局で不一致・全て脚本家の決定が起点）。
    ∴ 反実仮想の原局面再現には rng 状態の復元が必須（主人公は self.rng を持たない＝
    毎ターン view から再構成される。脚本家の他の可変状態は _b215_stats＝計測専用のみ）。
    """
    from arena.postmortem import fast_copy
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    sc = make_script(name, seed, days)
    probe = replace(sc, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    snaps: dict = {}
    rngs: dict = {}

    def on_day(state):
        snaps[(state.loop_no, state.day)] = fast_copy(state)
        rngs[(state.loop_no, state.day)] = mm.rng.getstate()

    log: list[dict] = []
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                        on_day_start=on_day, log=log)
    return state, log, snaps, rngs


def continue_loop2(snapshot, rng_state, seed: int, forced: dict) -> bool:
    """counterfactual.continue_loop ＋ 脚本家 rng 状態の復元。防衛できたかを返す。"""
    from arena.counterfactual import _mk_decider
    from arena.postmortem import fast_copy
    from sim import flow
    from sim.state import PROTAGONIST_SEATS
    from agents import HeuristicMastermind, HeuristicProtagonist
    st = fast_copy(snapshot)
    mm = HeuristicMastermind(seed)
    mm.rng.setstate(rng_state)
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


def cmd_flip(args) -> int:
    """日単位の総当たり差し替え（rng 状態復元つき＝原局面 bit 再現の上での探索）。

    verifycf --rng で当該局の bit 再現を確認してから使うこと。
    """
    from arena.postmortem import _mm_set_of
    from sim.mate import _order_responses, _slim, enum_prot_sets
    name, seed, days = args.game
    state, log, snaps, rngs = _replay_snaps_rng(name, seed, days, args.loops)
    key = (args.loop, args.day)
    snap = snaps.get(key)
    if snap is None:
        print(f"スナップショット無し: L{args.loop}D{args.day}")
        return 1
    mm_set = _mm_set_of(log, args.loop, args.day)
    base = _slim(snap)
    responses = enum_prot_sets(base, mm_set)
    if responses is None:
        print("応手列挙が予算超過")
        return 1
    responses = _order_responses(base, responses)
    tried = 0
    for R in responses:
        if args.max_candidates and tried >= args.max_candidates:
            break
        tried += 1
        if continue_loop2(snap, rngs[key], seed, {key: R}):
            print(f"flip 発見（{tried}試行）: " + " / ".join(
                f"{p['card']}→{p['target']}" for p in R))
            return 0
    lim = f"上位{tried}打ち切り" if (args.max_candidates
                                     and tried >= args.max_candidates) \
        else f"全{tried}応手を試行"
    print(f"単日の差し替えでは flip なし（{lim}）")
    return 1


def cmd_classify(args) -> int:
    """postmortem の分類（フラグは既定 ON のまま＝新物差しの実対局を分類）。

    ★注意（本プローブで確認した既知の限界）：`sim/mate.py` の中身量化
    （enum_mm_sets_for_sigma → loop_solver._plan_decider）は allow_bluff=False のまま
    ＝板ダミーの中身（非暗躍札）は ∀c の量化範囲に入らない。板σは常に「暗躍札かも」
    として量化される。defense_exists の主張はダミー中身（解決時 no-op＝より弱い）にも
    通ると考えられるが、形式的には量化範囲外＝報告に明記のこと。
    """
    from arena.postmortem import _mm_set_of, _lost_loops, _decisive_days
    from sim.mate import classify_day
    name, seed, days = args.game
    b214, b215 = CONDS[args.cond]
    with mm_flags(b214, b215):
        state, log, snaps = _replay_snaps(name, seed, days, args.loops)
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
    try:
        res = classify_day(snap, mm_set=mm_set, objective=objective)
    except ValueError as e:
        print(f"L{args.loop}D{args.day} [{objective}]: 量化不能（{e}）"
              f"＝σにダミー板が含まれ中身候補が空の可能性")
        return 1
    d = res.get("defense")
    extra = ""
    if d:
        extra = " 防御例: " + " / ".join(f"{p['card']}→{p['target']}" for p in d)
    print(f"L{args.loop}D{args.day} [{objective}]: {res['verdict']}"
          f"（中身{res['n_contents']}通り×応手{res['n_responses']}通り）{extra}")
    return 0


def cmd_verifycf(args) -> int:
    """★反実仮想リプレイ（B-214 ON で使うのは今回が初）の原局面再現チェック。

    指定ループの各日開始スナップショットから、差し替えゼロの continue 相当
    （counterfactual._mk_decider + flow.run_day）でループ末まで打たせ、
    set_card の決定列が元対局のログと bit 一致するかを検査する。
    """
    from arena.counterfactual import _mk_decider
    from arena.postmortem import fast_copy
    from sim import flow
    from sim.state import PROTAGONIST_SEATS
    from agents import HeuristicMastermind, HeuristicProtagonist

    name, seed, days = args.game
    if args.rng:
        state, log, snaps, rngs = _replay_snaps_rng(name, seed, days, args.loops)
    else:
        state, log, snaps = _replay_snaps(name, seed, days, args.loops)
        rngs = None
    loop = args.loop
    day0 = args.day or 1
    snap = snaps.get((loop, day0))
    if snap is None:
        print(f"スナップショット無し: L{loop}D{day0}")
        return 1
    st = fast_copy(snap)
    mm = HeuristicMastermind(seed)
    if rngs is not None:
        mm.rng.setstate(rngs[(loop, day0)])
    hp = HeuristicProtagonist(seed)
    agents = {"mastermind": mm, **{s: hp for s in PROTAGONIST_SEATS}}
    inner = _mk_decider(st, agents, {})
    rec: list[tuple] = []

    def decide(actor, decision, options):
        ch = inner(actor, decision, options)
        if decision == "set_card":
            rec.append((st.loop_no, st.day, actor, ch["card"],
                        ch["target"], ch["target_kind"]))
        return ch

    # ★記録用ラッパにも mm_allow_bluff を載せ替える（`run_day` は decide 関数の属性を
    #   見る＝ラッパで包むと候補列が閉じ、§71-10 の配線漏れと同じ割れ方をする。
    #   本プローブの初版がまさにこれを踏んだ＝報告に記載）。
    decide.mm_allow_bluff = getattr(inner, "mm_allow_bluff", False)

    while True:
        flow.run_day(st, decide)
        if st.loop_end_triggered or st.day >= st.script.days_per_loop:
            break
        st.day += 1
    flow.evaluate_loop_end(st)

    orig = [(e["loop"], e["day"], e["actor"], e["chosen"]["card"],
             e["chosen"]["target"], e["chosen"]["target_kind"])
            for e in log if e.get("decision") == "set_card"
            and e["loop"] == loop and e["day"] >= day0]
    ok = rec == orig
    print(f"{name}#{seed} L{loop}D{day0}〜: 再現 {'一致 ✅' if ok else '不一致 ❌'}"
          f"（CF {len(rec)}手 vs 元 {len(orig)}手）  CF末 defeat={st.defeat}")
    if not ok:
        for i, (a, b) in enumerate(zip(rec, orig)):
            if a != b:
                print(f"  最初の差分 #{i}: CF={a}  元={b}")
                break
        if len(rec) != len(orig):
            print(f"  手数が違う: CF={len(rec)} 元={len(orig)}")
    return 0 if ok else 1


def cmd_repeats(args) -> int:
    """★横断の行為指標＝「反復非適応」の数え上げ（列挙順に依存しない）。

    定義：敗北ループ i と、その直後の敗北ループ i+1 で、主人公の set_card 列
    （(day, card, target, kind) の全席・全日）が**完全一致**した対（＝前ループと
    同じ負け方を同じ応手でなぞった対）。ベンチ全局で数える。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    total_pairs = 0
    total_lost_pairs = 0
    games_with_streak: list[tuple] = []
    for name, seed, sc in benchmark_scripts(days=args.days or 3):
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        log: list[dict] = []
        state, _ = run_game(replace(sc, loops=args.loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                            log=log)
        lost = {e["loop"] for e in state.history
                if e.get("event") == "loop_result" and "敗北" in str(e.get("result"))}
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
    print(f"days={args.days or 3}: 連続敗北ループ対={total_lost_pairs}"
          f"  うち主人公応手が完全一致（反復非適応）={total_pairs}")
    for name, seed, rep, best, nlost, w in games_with_streak:
        print(f"  {name}#{seed}: 一致対={rep} 最長連続={best}"
              f" 敗北ループ={nlost} winner={w}")
    return 0


# ---------------------------------------------------------------------------
# ★B-222 Phase 0：既存語彙（B-196／B-208／defense_plan の板剥がし）の条件別実測
# ---------------------------------------------------------------------------

def _mk_conds_prot(seed: int):
    """診断つき主人公（挙動は本体と完全同一＝ProbedProtagonist 準拠）。"""
    from agents.debug import ProbedProtagonist

    class _CondsProt(ProbedProtagonist):
        def __init__(self, sd):
            super().__init__(sd, top=10**9)   # scored は全件保持
            self.diags: list[dict] = []

        def decide(self, view, decision, options):
            ch = super().decide(view, decision, options)
            if decision == "set_card":
                try:
                    self.diags.append(self._b222_diag(view, options, ch))
                except Exception as e:      # 診断は対局へ影響させない
                    self.diags.append({"loop": view.get("loop"),
                                       "day": view.get("day"),
                                       "seat": view.get("seat"),
                                       "error": repr(e)})
            return ch

        # -- 診断本体（読み取りのみ・状態は変更しない） --------------------
        def _b222_diag(self, view, options, chosen):
            marg = (self._belief.role_marginals()
                    if self._belief is not None else {})
            kuro = {n: round(d.get("クロマク", 0.0), 3)
                    for n, d in marg.items() if d.get("クロマク", 0) > 0.01}
            area_of = {n: (self._alive(view, n) or {}).get("area")
                       for n in kuro}
            scored = self.records[-1].get("scored", [])
            d = {
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"),
                "chosen": f'{chosen.get("card")}→{chosen.get("target")}',
                "board_anyaku": dict(view.get("board_anyaku") or {}),
                "kuro_marg": kuro, "kuro_area": area_of,
                "kuro_suspects(>=0.7)": sorted(getattr(self, "_kuromaku_suspects", ())),
                "kuro_cands(>=0.3)": sorted(getattr(self, "_kuromaku_cands", ())),
                "cult_marg": {n: round(d.get("カルティスト", 0.0), 3)
                              for n, d in marg.items()
                              if d.get("カルティスト", 0) > 0.01},
                "cult_suspects(>=0.7)": sorted(getattr(self, "_cultist_suspects", ())),
                "cult_cands(>=0.25)": sorted(getattr(self, "_cultist_cands", ())),
                "b74_smuggle_inter": sorted(getattr(self, "_b74_smuggle_inter", ())),
                "b67_return_targets": sorted(getattr(self, "_b67_return_targets", ())),
                "danger_board": self._guess_defeat_board(view),
                "observed_defeat_board": getattr(self, "_observed_defeat_board", None),
                "board_defeat_probs": {k: round(v, 3) for k, v in
                                       (self._board_defeat_probs or {}).items()},
                "relocatable_kuro_void": sorted(
                    getattr(self, "_relocatable_kuro_void", ())),
                "lethal_days": sorted(getattr(self, "_lethal_days", ())),
                "b196_receivers": sorted(self._b196_receivers(view)),
                "b208_pairs": [tuple(p) for p in self._b208_pairs(view)],
                "b196_trace": self._b222_b196_trace(view),
                "b208_trace": self._b222_b208_trace(view),
                "plan_board": self._b222_plan_board(view, options),
                "plan_recs": {f"{k[0]}→{k[1]}": round(v, 2)
                              for k, v in (self._plan_recs or {}).items()},
                "top": [(round(s, 2), f'{o.get("card")}→{o.get("target")}')
                        for s, o in scored[:8]],
                "kuro_moves": [(round(s, 2), f'{o.get("card")}→{o.get("target")}')
                               for s, o in scored
                               if o.get("target") in kuro
                               and (str(o.get("card", "")).startswith("移動"))],
            }
            return d

        def _b222_b196_trace(self, view):
            """B-196 の各条件の実測値（どこで落ちるか）。"""
            t: dict = {}
            deaths = [e for e in view.get("history", []) or []
                      if e.get("event") == "protagonist_death"]
            t["①protagonist_death数"] = len(deaths)
            t["①うち暗躍4以上あり"] = sum(
                1 for e in deaths
                if any(int(a or 0) >= 4 for a in (e.get("anyaku") or {}).values()))
            n_abil = sum(1 for e in view.get("history", []) or []
                         if e.get("event") == "anyaku"
                         and e.get("phase") == "mastermind_ability"
                         and int(e.get("delta", 0) or 0) > 0)
            t["②能力フェイズ暗躍イベント総数"] = n_abil
            t["結論"] = ("①で落ちる（主人公死亡なし＝板敗北はイベント対象外）"
                         if not deaths else "個別確認")
            return t

        def _b222_b208_trace(self, view):
            """B-208 の各条件の実測値（どこで落ちるか）。"""
            t: dict = {}
            today = int(view.get("day", 1) or 1)
            lethal = sorted(d for d in getattr(self, "_lethal_days", ())
                            if d >= today)
            t["lethal(残)"] = lethal
            if not lethal:
                t["結論"] = "lethal_days で落ちる（残る致死系事件日なし）"
                return t
            cur = view.get("loop")
            seen: dict = {}
            for e in view.get("history", []) or []:
                if (e.get("event") == "unrest"
                        and e.get("phase") == "mastermind_ability"
                        and int(e.get("delta", 0) or 0) > 0
                        and e.get("loop") != cur):
                    seen.setdefault(e.get("target"), []).append(
                        frozenset(e.get("present") or ()))
            t["①能力フェイズ不安の受け手(過去loop)"] = {
                k: len(v) for k, v in seen.items()}
            if not seen:
                t["結論"] = ("①で落ちる（能力フェイズの**不安**供給が観測ゼロ"
                             "＝板への**暗躍**供給は event=anyaku で対象外）")
            return t

        def _b222_plan_board(self, view, options):
            """defense_plan の board_defeat 脅威と折り手（剥がし Break の有無）。"""
            try:
                from agents.defense_plan import plan_for_belief, _pick_for
                threats, plan = plan_for_belief(
                    view, self._belief, options=options,
                    initial_areas=self._initial_area_map(view))
            except Exception as e:
                return {"error": repr(e)}
            out: list = []
            for th in threats:
                if th.kind != "board_defeat":
                    continue
                conds = []
                for c in th.conditions:
                    conds.append({
                        "cond": c.label,
                        "note": getattr(c, "note", None),
                        "breaks": [f"{b.card}→{b.target}"
                                   f"(cost{b.cost}{'' if b.robust else '・非堅'})"
                                   for b in c.breaks],
                    })
                pick = None
                try:
                    b = _pick_for(plan, th)
                    if b is not None:
                        pick = f"{b.card}→{b.target}"
                except Exception:
                    pass
                out.append({"label": th.label, "p": round(th.prob, 3),
                            "race": getattr(th, "race", None),
                            "pick": pick, "conds": conds})
            return out

    return _CondsProt(seed)


def cmd_conds(args) -> int:
    """B-222 Phase 0＝既存語彙の条件別実測（BTX#3 が既定の教材）。"""
    from agents import HeuristicMastermind
    from sim import run_game
    from engine.board import AREAS
    name, seed, days = args.game
    sc = make_script(name, seed, days)
    mm = HeuristicMastermind(seed)
    hp = _mk_conds_prot(seed)
    log: list[dict] = []
    state, _ = run_game(replace(sc, loops=args.loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                        log=log)
    print(f"=== {name}#{seed} d{days} conds（診断つき再走） ===")
    print(f"[結果] winner={state.winner} loop={state.loop_no}"
          f"（bit 検証＝overview と同一の経過なら診断は無害）")
    # 能力フェイズの板への暗躍イベント時系列（公開情報）
    print("\n[能力フェイズの板暗躍イベント（公開）]")
    for e in state.history:
        if (e.get("event") == "anyaku"
                and e.get("phase") == "mastermind_ability"
                and e.get("target") in AREAS):
            print(f"  L{e.get('loop')}D{e.get('day')} 板:{e.get('target')}"
                  f" present={e.get('present')}")
    want = set(args.only_loops or ())
    for d in hp.diags:
        if want and d.get("loop") not in want:
            continue
        print(f"\n--- L{d.get('loop')}D{d.get('day')} 席{d.get('seat')}"
              f" 選択={d.get('chosen')} ---")
        if "error" in d:
            print(f"  診断エラー: {d['error']}")
            continue
        for k in ("board_anyaku", "kuro_marg", "kuro_area",
                  "kuro_suspects(>=0.7)", "kuro_cands(>=0.3)",
                  "cult_marg", "cult_suspects(>=0.7)", "cult_cands(>=0.25)",
                  "b74_smuggle_inter", "b67_return_targets",
                  "danger_board", "observed_defeat_board",
                  "board_defeat_probs", "relocatable_kuro_void",
                  "lethal_days", "b196_receivers", "b208_pairs"):
            print(f"  {k}: {json.dumps(d[k], ensure_ascii=False)}")
        print(f"  B196トレース: {json.dumps(d['b196_trace'], ensure_ascii=False)}")
        print(f"  B208トレース: {json.dumps(d['b208_trace'], ensure_ascii=False)}")
        print(f"  plan(板脅威): {json.dumps(d['plan_board'], ensure_ascii=False)}")
        print(f"  plan加点: {json.dumps(d['plan_recs'], ensure_ascii=False)}")
        print("  採点上位: " + " / ".join(f"{s}:{o}" for s, o in d["top"]))
        print("  クロマク疑いへの移動札: "
              + (" / ".join(f"{s}:{o}" for s, o in d["kuro_moves"]) or "（無し）"))
    return 0


# ---------------------------------------------------------------------------
# ★B-222 Phase 0：一般形の調査（この形の脚本がベンチに何局あるか・的の数え上げ）
# ---------------------------------------------------------------------------

#: ルールY → ループ終了時判定の敗北板（None＝ボードX＝クロマク/ウィッチ初期エリア）
_BOARD_RULE_Y = {"守るべき場所": "学校", "封印されしモノ": "神社",
                 "復讐者の灯火": None, "巨大時限爆弾Xの存在": None}


def cmd_survey(args) -> int:
    """両ベンチ全局（神視点）＝板ゴールのルールY×クロマクの分布と、
    「能力フェイズ供給が敗北板に効いた」ループの数え上げ（＝的の母集団）。"""
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from engine.board import AREAS
    days = args.days or 3
    n_board_y = 0
    n_board_y_kuro = 0
    rows = []
    for name, seed, sc in benchmark_scripts(days=days):
        ry = sc.rule_y
        if ry not in _BOARD_RULE_Y:
            continue
        n_board_y += 1
        kuro = sorted(n for n, r in sc.roles.items() if r == "クロマク")
        if kuro:
            n_board_y_kuro += 1
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        log: list[dict] = []
        state, _ = run_game(replace(sc, loops=args.loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                            log=log)
        goal = _BOARD_RULE_Y[ry] or state.rule_y_board_x
        # ループ別：敗北板の終了時値・能力フェイズ供給数・板への暗躍札の通過/阻止
        per_loop: dict = defaultdict(lambda: {"abil": 0, "end": None,
                                              "thr": 0, "blk": 0, "lost": False})
        for e in state.history:
            if (e.get("event") == "anyaku"
                    and e.get("phase") == "mastermind_ability"
                    and e.get("target") == goal):
                per_loop[e["loop"]]["abil"] += 1
            elif e.get("event") == "loop_board":
                per_loop[e["loop"]]["end"] = (e.get("board_anyaku") or {}).get(goal)
            elif e.get("event") == "loop_result":
                per_loop[e["loop"]]["lost"] = "敗北" in str(e.get("result"))
        for r in decoy_rows(log):
            pl = per_loop[r["loop"]]
            pl["thr"] += sum(1 for k, t, c in r["anyaku_through"]
                             if k == "board" and t == goal)
            pl["blk"] += sum(1 for k, t, c in r["anyaku_blocked"]
                             if k == "board" and t == goal)
        # ★的の候補＝「板敗北ループ（終了時 goal>=2 かつ敗北）で、能力フェイズ供給が
        #   2発以上」（=クロマク実演つき板敗北）と、その強い形「札の通過0なのに>=2」。
        n_abil_defeat = sum(1 for v in per_loop.values()
                            if v["lost"] and (v["end"] or 0) >= 2 and v["abil"] >= 2)
        n_sealed_defeat = sum(1 for v in per_loop.values()
                              if v["lost"] and (v["end"] or 0) >= 2
                              and v["abil"] >= 2 and v["thr"] == 0)
        if args.all or n_abil_defeat:
            rows.append((name, seed, ry, goal, kuro,
                         n_abil_defeat, n_sealed_defeat,
                         state.winner, state.loop_no))
    print(f"=== survey days={days}（神視点・loops={args.loops}） ===")
    print(f"板ゴールのルールY: {n_board_y}局／うちクロマク配役あり: {n_board_y_kuro}局")
    print("（下表＝能力供給つき板敗北ループが1つ以上ある局。sealed＝札の通過0なのに>=2）")
    for name, seed, ry, goal, kuro, na, ns, w, lp in rows:
        print(f"  {name}#{seed}: Y={ry} 板={goal} クロマク={kuro}"
              f" 能力供給板敗北ループ={na}（うち完封={ns}） winner={w} L{lp}")
    tot_a = sum(r[5] for r in rows)
    tot_s = sum(r[6] for r in rows)
    print(f"合計: 能力供給つき板敗北ループ={tot_a}／うち完封（BTX#3 L3型）={tot_s}")
    return 0


# ---------------------------------------------------------------------------
# ★B-222 事前登録の的（行為の数え上げ・列挙順非依存）。実装前にコミットで固定する。
#
# Phase 0 の実測（conds_BTX3_*.txt）で特定した「搬入戦争の資源枯渇」の3量：
#   M1 無効化すり抜け＝自陣の暗躍禁止が置かれた敗北板に、行動解決の暗躍が同日通過した回数
#      （＝カルティスト搬入/常駐による禁止の無効化。BTX#3 実測＝L3D5 の1回が決定打）。
#   M2 幾何空振りピン＝敗北板の**斜め対角**（単軸の移動札では届かない）に居る対象への
#      `移動禁止` ピンのうち、同一ループ2回目以降のもの（mm の斜めは 1/loop＝2枚目以降の
#      ピンは資源の過剰投下。BTX#3 実測＝L3D3/D4 の妹ピン2回）。
#   M3 資源枯渇搬入＝mm が「敗北板へ札」＋「単軸で敗北板に届くキャラへ札」の同時 tell を
#      出した日に、チームの `移動禁止` が尽きていて（かつピンが置かれず）迎えた回数
#      （BTX#3 実測＝L3D5）。
# 判定材料＝対局ログの配置（公開）＋日開始時の位置＋神視点のゴール板（測定にのみ使用）。
# ---------------------------------------------------------------------------

def _diag_only(area: str, goal: str) -> bool:
    """area から goal へ単軸（↑↓/←→）では届かない＝斜め対角か。"""
    from engine.board import AREAS as _A
    if area == goal or area is None:
        return False
    (ac, ar), (gc, gr) = _A[area], _A[goal]
    return ac != gc and ar != gr


def cmd_target(args) -> int:
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    days = args.days or 3
    tot = Counter()
    rows = []
    for name, seed, sc in benchmark_scripts(days=days):
        ry = sc.rule_y
        if ry not in _BOARD_RULE_Y:
            continue
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        areas_at: dict = {}

        def on_day(state):
            areas_at[(state.loop_no, state.day)] = {
                n: (c.area if c.alive and c.on_board else None)
                for n, c in state.characters.items()}

        log: list[dict] = []
        state, _ = run_game(replace(sc, loops=args.loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                            log=log, on_day_start=on_day)
        goal = _BOARD_RULE_Y[ry] or state.rule_y_board_x
        if goal is None:
            continue
        turns = _turns_from_log(log)
        passes = {(e["loop"], e["day"]) for e in state.history
                  if e.get("event") == "anyaku"
                  and e.get("phase") == "action_resolution"
                  and e.get("target") == goal}
        c = Counter()
        pins_in_loop: dict = defaultdict(list)   # loop -> [(day, tgt)]
        for (lp, dy), t in sorted(turns.items()):
            ar = areas_at.get((lp, dy), {})
            pro_kinshi_goal = any(p["card"] == KINSHI and p["target"] == goal
                                  and p["target_kind"] == "board"
                                  for p in t["pro"])
            if pro_kinshi_goal and (lp, dy) in passes:
                c["M1_無効化すり抜け"] += 1
            for p in t["pro"]:
                if p["card"] == "移動禁止" and p["target_kind"] == "character":
                    tgt = p["target"]
                    if _diag_only(ar.get(tgt), goal):
                        if any(_diag_only(areas_at.get((lp, d2), {}).get(t2), goal)
                               for d2, t2 in pins_in_loop[lp]):
                            c["M2_幾何空振りピン"] += 1
                    pins_in_loop[lp].append((dy, tgt))
            mm_board_goal = any(p["target_kind"] == "board" and p["target"] == goal
                                for p in t["mm"])
            mm_axis_tells = [p["target"] for p in t["mm"]
                             if p["target_kind"] == "character"
                             and ar.get(p["target"]) is not None
                             and ar.get(p["target"]) != goal
                             and not _diag_only(ar.get(p["target"]), goal)]
            if mm_board_goal and mm_axis_tells:
                used_pins = len(pins_in_loop[lp])
                pro_pinned_today = any(p["card"] == "移動禁止" for p in t["pro"])
                if used_pins >= 3 and not pro_pinned_today:
                    c["M3_資源枯渇搬入"] += 1
        if c:
            rows.append((name, seed, goal, dict(c)))
        tot.update(c)
    print(f"=== target days={days}（板ゴール局・loops={args.loops}） ===")
    for name, seed, goal, cc in rows:
        print(f"  {name}#{seed} 板={goal}: {json.dumps(cc, ensure_ascii=False)}")
    print(f"合計: {json.dumps(dict(tot), ensure_ascii=False)}")
    return 0


def _parse_game(s: str):
    name, seed, days = s.split(":")
    return name, int(seed), int(days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-220 検死プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["run6", "decoys", "overview", "classify",
                                    "verifycf", "flip", "repeats",
                                    "conds", "survey", "target"])
    ap.add_argument("--only-loops", type=int, nargs="*", default=None,
                    help="conds: 表示するループ番号（省略＝全部）")
    ap.add_argument("--conds", default="on,off", help="run6 用（on,off,b214,b215）")
    ap.add_argument("--cond", default="on", choices=list(CONDS))
    ap.add_argument("--game", type=_parse_game, default=None,
                    help="script:seed:days（例 random_FS:10:5・btx_future:4:3）")
    ap.add_argument("--days", type=int, default=None, help="run6 の絞り込み")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--objective", default="survive",
                    choices=["survive", "no_incident", "no_death"])
    ap.add_argument("--victim", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true", help="decoys: 全ターン表示")
    ap.add_argument("--rng", action="store_true",
                    help="verifycf: 脚本家 rng 状態も復元して再現検査")
    ap.add_argument("--max-candidates", type=int, default=None,
                    help="flip: 探索する応手の上限（既定＝全数）")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"run6": cmd_run6, "decoys": cmd_decoys, "overview": cmd_overview,
            "classify": cmd_classify, "verifycf": cmd_verifycf,
            "flip": cmd_flip, "repeats": cmd_repeats,
            "conds": cmd_conds, "survey": cmd_survey,
            "target": cmd_target}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
