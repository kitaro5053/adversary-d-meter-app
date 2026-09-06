# -*- coding: utf-8 -*-
"""B-229：5日級 `random_BTX#1`（fb_loss）の検死プローブ（読み取り専用）。

AI・エンジン（agents/ sim/ engine/）には一切触れない。フラグの ON/OFF は
クラス属性を実行時に退避→復元するだけ（ファイルは不変）。B-220 プローブ
（lane/b220 `25c23d6` の arena/b220_probe.py）の型を踏襲し、フルセット既定 ON
（2026-08-16・`4970d98`）時代の主人公側フラグも条件に加えた。

対象＝random_BTX seed=1 days=5（現行正典で 9[fb_loss]・5日級 68/70 の非防衛2局の一方）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b229_probe run --conds full,mmoff,poff
    PYTHONHASHSEED=0 ... python -m arena.b229_probe overview --cond full
    PYTHONHASHSEED=0 ... python -m arena.b229_probe decoys --cond full
    PYTHONHASHSEED=0 ... python -m arena.b229_probe diffpro --cond full --cond2 noC
    PYTHONHASHSEED=0 ... python -m arena.b229_probe classify --cond full --list
    PYTHONHASHSEED=0 ... python -m arena.b229_probe verifycf --cond full --loop 8 --rng
    PYTHONHASHSEED=0 ... python -m arena.b229_probe flip --cond full --loop 8 --day 3
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

GAME = ("random_BTX", 1, 5)

#: 条件＝{ "mm": {属性: 値}, "pro": {属性: 値} }。書いていない属性は現行既定のまま。
#: 現行既定（main 4970d98）＝mm: B214/B215 ON ／ pro: A(B221)・B(B222)・C(B224_COOL_FLOOR)・
#: D(B224_WEAK_MATCH)・B225・B227 ON・B100_IRON_PROB=0.15。
_PRO_ALL_OFF = {
    "B221_BREAKER": False, "B222_FERRY_GEOMETRY": False,
    "B224_COOL_FLOOR": False, "B224_WEAK_MATCH": False,
    "B225_REPEAT_AWARE_BREAK": False, "B227_IRON_BAND": False,
    "B100_IRON_PROB": None,
}
CONDS: dict[str, dict] = {
    "full":  {},                                            # 現行既定（フルセット）
    "mmoff": {"mm": {"B214_BOARD_DECOY": False,
                     "B215_DECOY_FIRST": False}},           # 旧物差し（複線演出 OFF）×主人公フルセット
    "poff":  {"pro": dict(_PRO_ALL_OFF)},                   # 新物差し×主人公フラグ全 OFF（B-220 期基準）
    "alloff": {"mm": {"B214_BOARD_DECOY": False,
                      "B215_DECOY_FIRST": False},
               "pro": dict(_PRO_ALL_OFF)},                  # 旧物差し×全 OFF（stack3+#3 相当）
    "noA":   {"pro": {"B221_BREAKER": False}},              # full−A（D も土台喪失で不発）
    "noB":   {"pro": {"B222_FERRY_GEOMETRY": False}},
    "noC":   {"pro": {"B224_COOL_FLOOR": False}},
    "noD":   {"pro": {"B224_WEAK_MATCH": False}},
    "no225": {"pro": {"B225_REPEAT_AWARE_BREAK": False}},
    "no227": {"pro": {"B227_IRON_BAND": False}},            # 帯だけ外す（iron0.15 は残る）
    "noiron": {"pro": {"B100_IRON_PROB": None}},            # 資格を閉じる（帯は no-op 化）
    "bcd":   {"pro": {"B225_REPEAT_AWARE_BREAK": False,
                      "B227_IRON_BAND": False,
                      "B100_IRON_PROB": None}},             # §72-14 の BCD 期
    "Conly": {"pro": {**_PRO_ALL_OFF, "B224_COOL_FLOOR": True}},   # B-224 単独 C 実測の再現
    "poff_mmoff_C": {"mm": {"B214_BOARD_DECOY": False, "B215_DECOY_FIRST": False},
                     "pro": {**_PRO_ALL_OFF, "B224_COOL_FLOOR": True}},  # 旧物差し×C 単独
}


@contextmanager
def cond_flags(cond: dict):
    from agents import HeuristicMastermind as HM, HeuristicProtagonist as HP
    saved: list[tuple[type, str, object]] = []
    try:
        for cls, key in ((HM, "mm"), (HP, "pro")):
            for attr, val in (cond.get(key) or {}).items():
                saved.append((cls, attr, getattr(cls, attr)))
                setattr(cls, attr, val)
        yield
    finally:
        for cls, attr, val in reversed(saved):
            setattr(cls, attr, val)


def make_script():
    from sim import random_script
    name, seed, days = GAME
    return random_script(name.split("_", 1)[1], seed, days=days)


def play_one(loops: int = 8):
    """ベンチ（loops_to_win）と同一条件で1局打ち、(ltw, outcome, state, log) を返す。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    _, seed, _ = GAME
    sc = make_script()
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
                    real_boards.add(tgt)
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


def cmd_run(args) -> int:
    from arena.b214_ab import _count_from_log
    for cname in args.conds.split(","):
        with cond_flags(CONDS[cname]):
            ltw, outcome, state, log = play_one(loops=args.loops)
        acc: Counter = Counter()
        _count_from_log(log, acc)
        name, seed, days = GAME
        print(f"[{cname:>12}] {name}#{seed} d{days}: ltw={ltw} {outcome}"
              f"  dummy={acc.get('mm_board_dummy', 0)}"
              f" sucked={acc.get('kinshi_board_whiff_dummy', 0)}"
              f" through={acc.get('mm_anyaku_through', 0)}"
              f"/{acc.get('mm_anyaku_total', 0)}"
              f" 板ガード={acc.get('kinshi_board_hit', 0)}hit"
              f"/{acc.get('kinshi_board_total', 0)}", flush=True)
    return 0


def cmd_overview(args) -> int:
    sc = make_script()
    name, seed, days = GAME
    print(f"=== {name}#{seed} days={sc.days_per_loop} [{args.cond}] ===")
    print(f"[脚本] rule_y={getattr(sc, 'rule_y', None)}  rule_x={getattr(sc, 'rule_x', None)}")
    print("[配役]")
    for n, r in sc.roles.items():
        print(f"  {n}: {r}")
    print("[事件]")
    for inc in sc.incidents:
        print(f"  {inc}")
    with cond_flags(CONDS[args.cond]):
        ltw, outcome, state, log = play_one(loops=args.loops)
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


def cmd_decoys(args) -> int:
    with cond_flags(CONDS[args.cond]):
        ltw, outcome, state, log = play_one(loops=args.loops)
    name, seed, days = GAME
    print(f"=== {name}#{seed} d{days} [{args.cond}] ltw={ltw} {outcome} ===")
    for r in decoy_rows(log):
        mark = " ★吸われ" if r["sucked"] else ""
        if not (args.all or r["dummy_boards"] or r["anyaku_through"]
                or r["anyaku_blocked"]):
            continue
        if args.loop and r["loop"] != args.loop:
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


def cmd_diffpro(args) -> int:
    """2条件の間で set_card 決定（mm/pro とも）が最初に割れる箇所と、以後の pro 差分。"""
    def seq(cname):
        with cond_flags(CONDS[cname]):
            ltw, outcome, state, log = play_one(loops=args.loops)
        rows = [(e["loop"], e["day"], e["actor"], e["chosen"]["card"],
                 e["chosen"]["target"], e["chosen"]["target_kind"])
                for e in log if e.get("decision") == "set_card"]
        return ltw, outcome, rows

    ltw1, out1, s1 = seq(args.cond)
    ltw2, out2, s2 = seq(args.cond2)
    print(f"[{args.cond}] ltw={ltw1} {out1}  vs  [{args.cond2}] ltw={ltw2} {out2}")
    n = min(len(s1), len(s2))
    first = None
    for i in range(n):
        if s1[i] != s2[i]:
            first = i
            break
    if first is None:
        print(f"決定列は先頭 {n} 手まで一致（長さ {len(s1)} vs {len(s2)}）")
        return 0
    print(f"最初の差分 #{first}:")
    print(f"  [{args.cond}]  {s1[first]}")
    print(f"  [{args.cond2}] {s2[first]}")
    # 以後の差分をターン単位で要約（多すぎるときは --max-diff で制限）
    shown = 0
    for i in range(first, n):
        if s1[i] != s2[i]:
            print(f"  #{i}: {s1[i]}  |  {s2[i]}")
            shown += 1
            if args.max_diff and shown >= args.max_diff:
                print("  …（--max-diff 打ち切り）")
                break
    return 0


def _replay_snaps_rng(loops: int, cond: dict):
    from arena.postmortem import replay_with_snapshots
    _, seed, _ = GAME
    sc = make_script()
    rngs: dict = {}
    with cond_flags(cond):
        state, log, snaps = replay_with_snapshots(sc, seed, loops=loops,
                                                  rng_states=rngs)
    return state, log, snaps, rngs


def cmd_classify(args) -> int:
    from arena.postmortem import _mm_set_of, _lost_loops, _decisive_days
    from sim.mate import classify_day
    state, log, snaps, rngs = _replay_snaps_rng(args.loops, CONDS[args.cond])
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
        print(f"L{args.loop}D{args.day} [{objective}]: 量化不能（{e}）")
        return 1
    d = res.get("defense")
    extra = ""
    if d:
        extra = " 防御例: " + " / ".join(f"{p['card']}→{p['target']}" for p in d)
    print(f"L{args.loop}D{args.day} [{objective}]: {res['verdict']}"
          f"（中身{res['n_contents']}通り×応手{res['n_responses']}通り）{extra}")
    return 0


def cmd_verifycf(args) -> int:
    """反実仮想リプレイの原局面 bit 再現チェック（差し替えゼロ continue）。"""
    from arena.counterfactual import _mk_decider
    from arena.postmortem import fast_copy
    from sim import flow
    from sim.state import PROTAGONIST_SEATS
    from agents import HeuristicMastermind, HeuristicProtagonist

    _, seed, _ = GAME
    cond = CONDS[args.cond]
    state, log, snaps, rngs = _replay_snaps_rng(args.loops, cond)
    loop = args.loop
    day0 = args.day or 1
    snap = snaps.get((loop, day0))
    if snap is None:
        print(f"スナップショット無し: L{loop}D{day0}")
        return 1
    with cond_flags(cond):
        st = fast_copy(snap)
        mm = HeuristicMastermind(seed)
        if args.rng:
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
    print(f"L{loop}D{day0}〜 [{args.cond}] 再現 {'一致 ✅' if ok else '不一致 ❌'}"
          f"（CF {len(rec)}手 vs 元 {len(orig)}手）  CF末 defeat={st.defeat}")
    if not ok:
        for i, (a, b) in enumerate(zip(rec, orig)):
            if a != b:
                print(f"  最初の差分 #{i}: CF={a}  元={b}")
                break
        if len(rec) != len(orig):
            print(f"  手数が違う: CF={len(rec)} 元={len(orig)}")
    return 0 if ok else 1


def cmd_flip(args) -> int:
    """日単位の総当たり差し替え（mm rng 状態復元つき）。--two-days で2日連鎖。"""
    from arena.postmortem import _mm_set_of
    from arena.counterfactual import find_flip, find_flip_2days
    _, seed, _ = GAME
    cond = CONDS[args.cond]
    state, log, snaps, rngs = _replay_snaps_rng(args.loops, cond)
    key = (args.loop, args.day)
    snap = snaps.get(key)
    if snap is None:
        print(f"スナップショット無し: L{args.loop}D{args.day}")
        return 1
    mm_set = _mm_set_of(log, args.loop, args.day)
    with cond_flags(cond):
        if args.two_days:
            found = find_flip_2days(snap, seed, mm_set, top_k=args.top_k,
                                    mm_rng_state=rngs[key])
            if found:
                for (lp, dy), R in sorted(found.items()):
                    print(f"flip L{lp}D{dy}: " + " / ".join(
                        f"{p['card']}→{p['target']}" for p in R))
                return 0
            print(f"2日連鎖でも flip なし（上位{args.top_k}×{args.top_k}）")
            return 1
        R, tried = find_flip(snap, seed, mm_set,
                             max_candidates=args.max_candidates,
                             mm_rng_state=rngs[key])
    if R:
        print(f"flip 発見（{tried}試行）: " + " / ".join(
            f"{p['card']}→{p['target']}" for p in R))
        return 0
    lim = (f"上位{tried}打ち切り" if (args.max_candidates
                                      and tried >= args.max_candidates)
           else f"全{tried}応手を試行")
    print(f"単日の差し替えでは flip なし（{lim}）")
    return 1


def cmd_selfcancel(args) -> int:
    """★行為の数え上げ（列挙順非依存）＝「分離相殺」席。

    定義：主人公チームが同一ターンに、同じエリアにいる2キャラへ**同種の移動札**を
    置いた組（合成が同方向＝移動後も同室＝分離になっていない）。うち、過去ループの
    脚本家能力フェイズ不安+1 の（受け手×同室supplier候補）実績がある組を主指標とする。
    ベンチ全局（現行既定フラグのまま）で数える。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    moves_set = {"移動←→", "移動↑↓", "移動斜め"}
    tot_pairs = 0
    tot_sup = 0
    games = []
    for name, seed, sc in benchmark_scripts(days=args.days or 5):
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        log: list[dict] = []
        state, _ = run_game(replace(sc, loops=args.loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                            log=log)
        supply_pairs = set()
        for e in state.history:
            if (e.get("event") == "unrest"
                    and e.get("phase") == "mastermind_ability"
                    and (e.get("delta") or 0) > 0):
                rcv = e.get("target")
                for p in (e.get("present") or ()):
                    if p != rcv:
                        supply_pairs.add((e.get("loop"), rcv, p))
        turns = defaultdict(list)
        pos_by_turn: dict = {}
        for e in log:
            if e.get("decision") != "set_card" or e.get("actor") == "mastermind":
                continue
            c = e["chosen"]
            key = (e["loop"], e["day"])
            if c["card"] in moves_set and c["target_kind"] == "character":
                turns[key].append((c["card"], c["target"]))
            if key not in pos_by_turn:
                v = e.get("view") or {}
                pos_by_turn[key] = {ch.get("name"): ch.get("area")
                                    for ch in (v.get("characters") or [])}
        n_pair = 0
        n_sup = 0
        for (lp, dy), mv in turns.items():
            pos = pos_by_turn.get((lp, dy), {})
            for i in range(len(mv)):
                for j in range(i + 1, len(mv)):
                    (c1, t1), (c2, t2) = mv[i], mv[j]
                    if c1 != c2 or t1 == t2:
                        continue
                    if pos.get(t1) is None or pos.get(t1) != pos.get(t2):
                        continue
                    n_pair += 1
                    if any(l0 < lp and (r, p) in ((t1, t2), (t2, t1))
                           for (l0, r, p) in supply_pairs):
                        n_sup += 1
        tot_pairs += n_pair
        tot_sup += n_sup
        if n_sup:
            games.append((name, seed, n_pair, n_sup, state.winner))
    print(f"days={args.days or 5}: 同室同種ペア移動（相殺）={tot_pairs}"
          f"  うち供給実績ペア（受け手×供給役候補）={tot_sup}")
    for g in games:
        print("  ", g)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-229 検死プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["run", "overview", "decoys", "diffpro",
                                    "classify", "verifycf", "flip", "selfcancel"])
    ap.add_argument("--conds", default="full,mmoff,poff,alloff",
                    help="run 用（カンマ区切り）: " + ",".join(CONDS))
    ap.add_argument("--cond", default="full", choices=list(CONDS))
    ap.add_argument("--cond2", default="noC", choices=list(CONDS),
                    help="diffpro 用の比較条件")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--days", type=int, default=None, help="selfcancel: ベンチの日数")
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--objective", default="survive",
                    choices=["survive", "no_incident", "no_death"])
    ap.add_argument("--victim", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true", help="decoys: 全ターン表示")
    ap.add_argument("--rng", action="store_true",
                    help="verifycf: 脚本家 rng 状態も復元して再現検査")
    ap.add_argument("--two-days", action="store_true", help="flip: 2日連鎖")
    ap.add_argument("--top-k", type=int, default=40)
    ap.add_argument("--max-candidates", type=int, default=None)
    ap.add_argument("--max-diff", type=int, default=40)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"run": cmd_run, "overview": cmd_overview, "decoys": cmd_decoys,
            "diffpro": cmd_diffpro, "classify": cmd_classify,
            "verifycf": cmd_verifycf, "flip": cmd_flip,
            "selfcancel": cmd_selfcancel}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
