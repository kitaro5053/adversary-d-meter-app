# -*- coding: utf-8 -*-
"""B-238：現行構成（主人公側4切替口 既定 ON）における**非防衛局の全数検死**プローブ。

★読み取り専用＝`agents/` `sim/` `engine/` `rules/` には一切触れない。
並べ替え（perm）は `arena.tie_noise.install_perm`／`uninstall_perm` を
コンテキストで着脱するだけ（ファイル不変）。

B-229 プローブ（`arena/b229_probe.py`）の型を踏襲しつつ、
**任意の (脚本, seed, days, perm)** を扱えるよう一般化した。

CLI 例:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b238_probe inventory --days 5
    PYTHONHASHSEED=0 ... python -m arena.b238_probe overview --game random_BTX#6 --days 3 --perm rev
    PYTHONHASHSEED=0 ... python -m arena.b238_probe decoys  --game random_BTX#6 --days 3 --perm rev
    PYTHONHASHSEED=0 ... python -m arena.b238_probe classify --game random_BTX#6 --days 3 --perm rev --list
    PYTHONHASHSEED=0 ... python -m arena.b238_probe verifycf --game random_BTX#6 --days 3 --perm rev --loop 8
    PYTHONHASHSEED=0 ... python -m arena.b238_probe flip     --game random_BTX#6 --days 3 --perm rev --loop 8 --day 2
    PYTHONHASHSEED=0 ... python -m arena.b238_probe knobs    --game random_BTX#6 --days 3 --perm rev
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

#: 主人公側の切替口4つ（現行 main は全部 True）
KNOBS = {
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
    "b232": ("hp", "B232_COOL_MATH"),
    "b234": ("bl", "B234_GOSHINBOKU_TRAIT"),
}
#: 既に land 済みの主人公側の旧切替口（B-229 の条件表と同じ顔ぶれ）
OLD_KNOBS = {
    "A": ("hp", "B221_BREAKER"),
    "B": ("hp", "B222_FERRY_GEOMETRY"),
    "C": ("hp", "B224_COOL_FLOOR"),
    "D": ("hp", "B224_WEAK_MATCH"),
    "225": ("hp", "B225_REPEAT_AWARE_BREAK"),
    "227": ("hp", "B227_IRON_BAND"),
}
MM_DECOY = {
    "B214": "B214_BOARD_DECOY",
    "B215": "B215_DECOY_FIRST",
}


# ---------------------------------------------------------------- 環境の着脱
@contextmanager
def env(perm: str = "id", pro_off: tuple = (), mm_off: tuple = ()):
    """perm の着脱＋主人公/脚本家フラグの一時上書き（必ず復元）。"""
    from arena.tie_noise import install_perm, uninstall_perm
    from agents import HeuristicMastermind as HM, HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    saved: list[tuple[object, str, object]] = []
    try:
        for k in pro_off:
            if k in KNOBS:
                where, attr = KNOBS[k]
                obj = holders[where]
            elif k in OLD_KNOBS:
                where, attr = OLD_KNOBS[k]
                obj = holders[where]
            else:
                raise SystemExit(f"未知の主人公切替口: {k}")
            saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, False)
        for k in mm_off:
            attr = MM_DECOY[k]
            saved.append((HM, attr, getattr(HM, attr)))
            setattr(HM, attr, False)
        install_perm(perm)
        yield
    finally:
        uninstall_perm()
        for obj, attr, val in reversed(saved):
            setattr(obj, attr, val)


def parse_game(spec: str) -> tuple[str, int]:
    """'random_BTX#6' → ('random_BTX', 6)"""
    name, _, seed = spec.partition("#")
    return name, int(seed or 0)


def make_script(spec: str, days: int):
    from sim import random_script
    from sim.sample_scripts import SAMPLE_SCRIPTS
    name, seed = parse_game(spec)
    if name.startswith("random_"):
        return random_script(name.split("_", 1)[1], seed, days=days)
    if name in SAMPLE_SCRIPTS:
        return SAMPLE_SCRIPTS[name]()
    raise SystemExit(f"未知の脚本: {spec}")


def play_one(spec: str, days: int, loops: int = 8):
    """ベンチ（loops_to_win）と同一条件で1局打ち、(ltw, outcome, state, log)。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    _, seed = parse_game(spec)
    sc = make_script(spec, days)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    log: list[dict] = []
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}, log=log)
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense", state, log
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss"), state, log
    return loops + 1, "loss", state, log


# ---------------------------------------------------------------- 棚卸し
def cmd_inventory(args) -> int:
    """全 perm × 指定 days でベンチを回し、非防衛局を列挙する。"""
    from arena.benchmark import benchmark_scripts, loops_to_win
    from sim.loop_race import analyze_script
    perms = args.perm.split(",")
    for days in [int(d) for d in str(args.days).split(",")]:
        for p in perms:
            with env(p):
                rows = []
                for gname, seed, sc in benchmark_scripts(days=days):
                    n, outcome = loops_to_win(sc, seed, loops=args.loops)
                    rows.append((gname, seed, n, outcome, sc))
            bad = [r for r in rows if r[3] != "defense"]
            n_def = sum(1 for r in rows if r[3] == "defense")
            print(f"=== days={days} perm={p} 防衛={n_def}/{len(rows)} 非防衛={len(bad)}")
            for gname, seed, n, outcome, sc in bad:
                race = analyze_script(sc).verdict   # 神視点（答え合わせ専用）
                print(f"   {gname}#{seed} ltw={n} {outcome} [race={race}]")
    return 0


# ---------------------------------------------------------------- 囮の数え上げ
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
    """1局の集計（囮枚数・吸われ席・板ガード命中）。"""
    from arena.b214_ab import _count_from_log
    for spec in args.game.split(","):
        with env(args.perm, tuple(x for x in args.pro_off.split(",") if x),
                 tuple(x for x in args.mm_off.split(",") if x)):
            ltw, outcome, state, log = play_one(spec, args.days, loops=args.loops)
        acc: Counter = Counter()
        _count_from_log(log, acc)
        print(f"[{spec}#d{args.days} perm={args.perm}"
              f"{' pro_off=' + args.pro_off if args.pro_off else ''}"
              f"{' mm_off=' + args.mm_off if args.mm_off else ''}]"
              f" ltw={ltw} {outcome}"
              f"  囮板={acc.get('mm_board_dummy', 0)}"
              f" 吸われ={acc.get('kinshi_board_whiff_dummy', 0)}"
              f" 通過暗躍={acc.get('mm_anyaku_through', 0)}"
              f"/{acc.get('mm_anyaku_total', 0)}"
              f" 板ガード={acc.get('kinshi_board_hit', 0)}hit"
              f"/{acc.get('kinshi_board_total', 0)}", flush=True)
    return 0


def cmd_decoys(args) -> int:
    with env(args.perm):
        ltw, outcome, state, log = play_one(args.game, args.days, loops=args.loops)
    print(f"=== {args.game} d{args.days} perm={args.perm} ltw={ltw} {outcome} ===")
    for r in decoy_rows(log):
        if args.loop and r["loop"] != args.loop:
            continue
        mark = " ★吸われ" if r["sucked"] else ""
        if not (args.all or r["dummy_boards"] or r["anyaku_through"]
                or r["anyaku_blocked"]):
            continue
        print(f"L{r['loop']}D{r['day']}{mark}")
        print("  mm : " + " / ".join(f"{k}:{t}←{c}" for k, t, c in r["mm"]))
        print("  pro: " + " / ".join(f"{k}:{t}←{c}" for k, t, c in r["pro"]))
        if r["dummy_boards"]:
            print(f"  ダミー板={r['dummy_boards']} 吸われ={r['sucked']}")
        if r["anyaku_through"]:
            print("  通った暗躍: " + " / ".join(
                f"{k}:{t}←{c}" for k, t, c in r["anyaku_through"]))
        if r["anyaku_blocked"]:
            print("  止めた暗躍: " + " / ".join(
                f"{k}:{t}←{c}" for k, t, c in r["anyaku_blocked"]))
    return 0


# ---------------------------------------------------------------- 全体像
def cmd_overview(args) -> int:
    sc = make_script(args.game, args.days)
    print(f"=== {args.game} days={sc.days_per_loop} perm={args.perm} ===")
    print(f"[脚本] rule_y={getattr(sc, 'rule_y', None)} rule_x={getattr(sc, 'rule_x', None)}")
    print("[配役]（★神視点＝答え合わせ専用）")
    for n, r in sc.roles.items():
        print(f"  {n}: {r}")
    print("[事件]")
    for inc in sc.incidents:
        print(f"  {inc}")
    with env(args.perm):
        ltw, outcome, state, log = play_one(args.game, args.days, loops=args.loops)
    fb = any(e.get("event") == "final_battle" for e in state.history)
    print(f"\n[結果] ltw={ltw} {outcome} winner={state.winner} final_battle={fb}")
    for e in state.history:
        ev = e.get("event")
        if ev == "loop_start":
            print(f"\n--- L{e.get('loop')} ---")
        elif ev == "incident" and e.get("occurs"):
            print(f"  D{e.get('day')} ⚡事件発生: {e.get('name')} (犯人={e.get('culprit', '?')})")
        elif ev == "incident" and args.verbose:
            print(f"  D{e.get('day')} （事件不発: {e.get('name')}）")
        elif ev == "death":
            print(f"  D{e.get('day')} 💀死亡: {e.get('name')} ({e.get('cause', '?')})")
        elif ev in ("protagonist_death", "loop_end", "defeat", "final_battle",
                    "game_over"):
            body = json.dumps({k: v for k, v in e.items()
                               if k not in ('event', 'loop', 'day')},
                              ensure_ascii=False)
            print(f"  D{e.get('day', '?')} {ev}: {body}")
        elif ev == "loop_board":
            print(f"  loop_board: board={e.get('board_anyaku')} "
                  f"goodwill={e.get('char_goodwill', '')}")
        elif ev == "loop_result":
            print(f"  loop_result: {e.get('result')}")
    return 0


# ---------------------------------------------------------------- 切替口の切り分け
def cmd_knobs(args) -> int:
    """4切替口（＋旧切替口・囮）の単独 OFF で結果が変わるかを見る。"""
    conds: list[tuple[str, tuple, tuple]] = [("full", (), ())]
    for k in KNOBS:
        conds.append((f"no_{k}", (k,), ()))
    conds.append(("no4", tuple(KNOBS), ()))
    if args.deep:
        for k in OLD_KNOBS:
            conds.append((f"no_{k}", (k,), ()))
        conds.append(("mmoff", (), tuple(MM_DECOY)))
        conds.append(("no4_mmoff", tuple(KNOBS), tuple(MM_DECOY)))
    from arena.b214_ab import _count_from_log
    for cname, pro_off, mm_off in conds:
        with env(args.perm, pro_off, mm_off):
            ltw, outcome, state, log = play_one(args.game, args.days, loops=args.loops)
        acc: Counter = Counter()
        _count_from_log(log, acc)
        print(f"[{cname:>12}] ltw={ltw} {outcome}"
              f"  囮板={acc.get('mm_board_dummy', 0)}"
              f" 吸われ={acc.get('kinshi_board_whiff_dummy', 0)}"
              f" 板ガード={acc.get('kinshi_board_hit', 0)}"
              f"/{acc.get('kinshi_board_total', 0)}", flush=True)
    return 0


# ---------------------------------------------------------------- 反実仮想
def _replay(args):
    from arena.postmortem import replay_with_snapshots
    _, seed = parse_game(args.game)
    sc = make_script(args.game, args.days)
    rngs: dict = {}
    with env(args.perm):
        state, log, snaps = replay_with_snapshots(sc, seed, loops=args.loops,
                                                  rng_states=rngs)
    return state, log, snaps, rngs, seed


def cmd_classify(args) -> int:
    from arena.postmortem import _mm_set_of, _lost_loops, _decisive_days
    from sim.mate import classify_day
    state, log, snaps, rngs, seed = _replay(args)
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
    with env(args.perm):
        try:
            res = classify_day(snap, mm_set=mm_set, objective=objective)
        except ValueError as e:
            print(f"L{args.loop}D{args.day} [{objective}]: 量化不能（{e}）")
            return 1
    d = res.get("defense")
    extra = (" 防御例: " + " / ".join(f"{p['card']}→{p['target']}" for p in d)) if d else ""
    print(f"L{args.loop}D{args.day} [{objective}]: {res['verdict']}"
          f"（中身{res['n_contents']}通り×応手{res['n_responses']}通り）{extra}")
    return 0


def cmd_verifycf(args) -> int:
    """反実仮想の原局面 bit 再現チェック（差し替えゼロ continue）。"""
    from arena.counterfactual import _mk_decider
    from arena.postmortem import fast_copy
    from sim import flow
    from sim.state import PROTAGONIST_SEATS
    from agents import HeuristicMastermind, HeuristicProtagonist
    state, log, snaps, rngs, seed = _replay(args)
    loop = args.loop
    day0 = args.day or 1
    snap = snaps.get((loop, day0))
    if snap is None:
        print(f"スナップショット無し: L{loop}D{day0}")
        return 1
    with env(args.perm):
        st = fast_copy(snap)
        mm = HeuristicMastermind(seed)
        if not args.fresh_rng:
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
    print(f"L{loop}D{day0}〜 {args.game} perm={args.perm} 再現"
          f" {'一致 ✅' if ok else '不一致 ❌'}（CF {len(rec)}手 vs 元 {len(orig)}手）"
          f"  CF末 defeat={st.defeat}")
    if not ok:
        for i, (a, b) in enumerate(zip(rec, orig)):
            if a != b:
                print(f"  最初の差分 #{i}: CF={a}  元={b}")
                break
    return 0 if ok else 1


def cmd_flip(args) -> int:
    from arena.postmortem import _mm_set_of
    from arena.counterfactual import find_flip, find_flip_2days
    state, log, snaps, rngs, seed = _replay(args)
    key = (args.loop, args.day)
    snap = snaps.get(key)
    if snap is None:
        print(f"スナップショット無し: L{args.loop}D{args.day}")
        return 1
    mm_set = _mm_set_of(log, args.loop, args.day)
    with env(args.perm):
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
        print(f"L{args.loop}D{args.day} flip 発見（{tried}試行）: " + " / ".join(
            f"{p['card']}→{p['target']}" for p in R))
        return 0
    lim = (f"上位{tried}打ち切り" if (args.max_candidates and tried >= args.max_candidates)
           else f"全{tried}応手を試行")
    print(f"L{args.loop}D{args.day} 単日の差し替えでは flip なし（{lim}）")
    return 1


# ---------------------------------------------------------------- 族の数え上げ
def cmd_repeat(args) -> int:
    """★行為の数え上げ（列挙順非依存）＝「反復非適応」＝連続敗北ループ対で
    主人公の応手（set_card 列）が完全一致した対の数。B-220 §族A の物差し。"""
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    days_list = [int(d) for d in str(args.days).split(",")]
    for days in days_list:
        for perm in args.perm.split(","):
            tot_pairs = 0
            tot_p2 = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    lost = {e["loop"] for e in st.history
                            if e.get("event") == "loop_result"
                            and "敗北" in str(e.get("result"))}
                    fb = any(e.get("event") == "final_battle" for e in st.history)
                    if fb or st.winner == "mastermind":
                        lost.add(st.loop_no)
                    seq: dict = defaultdict(list)
                    for e in log:
                        if e.get("decision") == "set_card" and e.get("actor") != "mastermind":
                            c = e["chosen"]
                            seq[e["loop"]].append((e["day"], c["card"], c["target"],
                                                   c["target_kind"]))
                    n = 0
                    n2 = 0
                    for lp in sorted(lost):
                        if lp + 1 in lost and seq.get(lp) and seq[lp] == seq.get(lp + 1):
                            n += 1
                        # ★周期2の振り子＝L と L+2 が完全一致（L+1 は別手）
                        if (lp + 2 in lost and seq.get(lp) and seq[lp] == seq.get(lp + 2)
                                and seq.get(lp) != seq.get(lp + 1)):
                            n2 += 1
                    if n or n2:
                        games.append((f"{gname}#{seed}", f"周期1={n}", f"周期2={n2}",
                                      "非防衛" if (fb or st.winner == "mastermind") else "防衛"))
                    tot_pairs += n
                    tot_p2 += n2
            print(f"days={days} perm={perm}: 連続敗北ループ対で応手完全一致(周期1) = {tot_pairs} 対"
                  f"  ／ 周期2の一致 = {tot_p2} 対")
            for g in games:
                print("   ", g)
    return 0


def cmd_selfcancel(args) -> int:
    """★行為の数え上げ＝「分離相殺」席（B-229 §7 の物差しの perm 対応版）。"""
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    moves_set = {"移動←→", "移動↑↓", "移動斜め"}
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot_pairs = tot_sup = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    supply_pairs = set()
                    for e in st.history:
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
                    n_pair = n_sup = 0
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
                        games.append((f"{gname}#{seed}", n_pair, n_sup, st.winner))
            print(f"days={days} perm={perm}: 同室同種ペア移動（相殺）={tot_pairs}"
                  f"  うち供給実績ペア={tot_sup}")
            for g in games:
                print("   ", g)
    return 0


def cmd_boardpick(args) -> int:
    """★行為の数え上げ（列挙順非依存）＝**板ガードの宛先ミス席**。

    定義：同一ターンで
      (1) 主人公が `暗躍禁止` を板 B に置き、B にはその日 暗躍札が無い（＝空振り）、かつ
      (2) 別の板 B' にその日 暗躍札が置かれ、B' には主人公の `暗躍禁止` が無い（＝素通り）
    このとき「持っていたガードを別の板へ向けていれば止められた」＝**宛先ミス席**。
    ★B-210 Phase 1（被覆判定器）の的が実在するかを測る物差し。
    `--games` を渡すとその局だけ、省略でベンチ全局。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    only = set(x for x in (args.games or "").split(",") if x)
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot_miss = tot_whiff = tot_guard = tot_through = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    key = f"{gname}#{seed}"
                    if only and key not in only:
                        continue
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    n_miss = n_whiff = n_guard = n_through = 0
                    for k, t in sorted(_turns_from_log(log).items()):
                        by: dict = defaultdict(list)
                        for c in t["mm"]:
                            by[(c["target_kind"], c["target"])].append(c["card"])
                        kin = {tgt for (kind, tgt) in
                               ((c["target_kind"], c["target"]) for c in t["pro"]
                                if c.get("card") == KINSHI) if kind == "board"}
                        anyaku_boards = {tgt for (kind, tgt), cards in by.items()
                                         if kind == "board"
                                         and any(x in ANYAKU for x in cards)}
                        n_guard += len(kin)
                        whiff = {b for b in kin if b not in anyaku_boards}
                        through = anyaku_boards - kin
                        n_whiff += len(whiff)
                        n_through += len(through)
                        if whiff and through:
                            n_miss += min(len(whiff), len(through))
                    tot_miss += n_miss
                    tot_whiff += n_whiff
                    tot_guard += n_guard
                    tot_through += n_through
                    if n_miss:
                        games.append((key, f"宛先ミス={n_miss}", f"空振り={n_whiff}",
                                      f"素通り={n_through}", f"ガード計={n_guard}",
                                      st.winner))
            print(f"days={days} perm={perm}: 板ガード宛先ミス席={tot_miss}"
                  f"  空振り={tot_whiff} 素通り暗躍板={tot_through} ガード計={tot_guard}")
            for g in sorted(games, key=lambda x: -int(x[1].split("=")[1]))[:args.top_k]:
                print("   ", g)
    return 0


def cmd_ground(args) -> int:
    """★行為の数え上げ（列挙順非依存）＝**板ガードの「過去ループ実績」非接地席**。

    定義：あるターンに主人公が `暗躍禁止` を板 B へ置いたとき、
      - 別の板 B' に**その日 mm の伏せ札がある**（＝二択が実在する）、かつ
      - B' の**過去ループ通算の暗躍配置数**が B より厳密に多い（＝実績で B' が本命）
    なら「実績に接地していれば B' を選べた」席として数える。
    ★L1 は過去実績がゼロ＝定義上カウントされない（初見のループを咎めない）。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    only = set(x for x in (args.games or "").split(",") if x)
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot = tot_hit = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    key = f"{gname}#{seed}"
                    if only and key not in only:
                        continue
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    hist: Counter = Counter()      # 板 → 過去ループ通算の暗躍配置数
                    cur: Counter = Counter()
                    n_bad = n_ok = 0
                    last_loop = None
                    for k, t in sorted(_turns_from_log(log).items()):
                        lp = k[0]
                        if last_loop is not None and lp != last_loop:
                            hist.update(cur)
                            cur = Counter()
                        last_loop = lp
                        mm_boards = {c["target"] for c in t["mm"]
                                     if c["target_kind"] == "board"}
                        for c in t["mm"]:
                            if c["target_kind"] == "board" and c["card"] in ANYAKU:
                                cur[c["target"]] += 1
                        for c in t["pro"]:
                            if c.get("card") != KINSHI or c["target_kind"] != "board":
                                continue
                            b = c["target"]
                            rivals = [x for x in mm_boards
                                      if x != b and hist[x] > hist[b]]
                            if rivals:
                                n_bad += 1
                            elif any(hist[b] >= hist[x] for x in mm_boards if x != b) \
                                    or len(mm_boards) <= 1:
                                n_ok += 1
                    tot += n_bad
                    tot_hit += n_ok
                    if n_bad:
                        games.append((key, f"非接地={n_bad}", f"接地={n_ok}", st.winner))
            print(f"days={days} perm={perm}: 板ガードの実績非接地席={tot}"
                  f"（接地席={tot_hit}）")
            for g in sorted(games, key=lambda x: -int(x[1].split("=")[1]))[:args.top_k]:
                print("   ", g)
    return 0


def cmd_twochoice(args) -> int:
    """★B-210 Phase 0 (d) の窓の検算の**再実装**（原プローブは main に無い＝定義は再構成）。

    「現物（暗躍札）を投じられ、**一度も臨界2に届いていない**板が同時に2枚以上」
    ＝『どちらが本命か』型の二択が実在するループ数を数える。
    ★原典＝§68-20 (d)「3日級 0件／5日級 3件（全て `random_BTX#13`）」。
    ★定義の一致は保証しない＝**要確認**（原プローブ `arena/b210_probe.py` は lane/b210 のみ）。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    # ループ末の板暗躍
                    end_board: dict[int, dict] = {}
                    for e in st.history:
                        if e.get("event") == "loop_board":
                            end_board[e.get("loop")] = dict(e.get("board_anyaku") or {})
                    placed: dict[int, set] = defaultdict(set)
                    for k, t in sorted(_turns_from_log(log).items()):
                        for c in t["mm"]:
                            if c["target_kind"] == "board" and c["card"] in ANYAKU:
                                placed[k[0]].add(c["target"])
                    seen: set = set()
                    reached: set = set()
                    n = 0
                    for lp in sorted(placed):
                        cands = {b for b in seen if b not in reached}
                        if len(cands) >= 2:
                            n += 1
                        seen |= placed[lp]
                        for b, v in (end_board.get(lp) or {}).items():
                            if v >= 2:
                                reached.add(b)
                    tot += n
                    if n:
                        games.append((f"{gname}#{seed}", n, st.winner))
            print(f"days={days} perm={perm}: 『二択』が成立したループ数={tot}")
            for g in games[:args.top_k]:
                print("   ", g)
    return 0


def cmd_killpair(args) -> int:
    """★行為の数え上げ＝**殺害線の分離相殺席**（B-229/B-230 の述語の殺害線版）。

    B-230（分離の幾何検算）は「脚本家能力フェイズの**不安供給**実績ペア」に限って
    同軸両動かし（相殺）を検出する。本指標はそれを **死亡実績ペア** へ広げた版：
      過去ループの `death` イベントの `present`（公開情報）から (被害者, 同席者) を集め、
      同一ターンに**同室の 2 キャラへ同種の移動札**を重ねた組のうち、
      その組が死亡実績ペアであるものを数える。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    moves_set = {"移動←→", "移動↑↓", "移動斜め"}
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot_pairs = tot_kill = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    kill_pairs = set()
                    for e in st.history:
                        if e.get("event") != "death":
                            continue
                        v = e.get("name")
                        for p in (e.get("present") or ()):
                            if p != v:
                                kill_pairs.add((e.get("loop"), v, p))
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
                    n_pair = n_kill = 0
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
                                if any(l0 < lp and (v, p) in ((t1, t2), (t2, t1))
                                       for (l0, v, p) in kill_pairs):
                                    n_kill += 1
                    tot_pairs += n_pair
                    tot_kill += n_kill
                    if n_kill:
                        games.append((f"{gname}#{seed}", n_pair, n_kill, st.winner))
            print(f"days={days} perm={perm}: 同室同種ペア移動={tot_pairs}"
                  f"  うち**死亡実績ペア**={tot_kill}")
            for g in sorted(games, key=lambda x: -x[2])[:args.top_k]:
                print("   ", g)
    return 0


def cmd_converge(args) -> int:
    """★行為の数え上げ＝**「退避したつもりが合流した」席**（列挙順非依存）。

    定義：主人公があるターンに移動札を置いたキャラ X について、
      - そのターン開始時に X は危険役 Y と**別エリア**、
      - 翌日開始時に X と Y が**同エリア**、
      - (X, Y) は**過去ループの死亡実績ペア**（`death` イベントの `present` から作る公開情報）
    のとき1席と数える。★主人公の移動札は mm の同キャラへの伏せ札と**合成**されるため
    （`rules/10` 移動札の合成）、退避が逆に危険エリアへ運ぶことがある＝その実測。
    """
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    moves_set = {"移動←→", "移動↑↓", "移動斜め"}
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot = 0
            games = []
            with env(perm):
                for gname, seed, sc in benchmark_scripts(days=days):
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    log: list[dict] = []
                    st, _ = run_game(replace(sc, loops=args.loops),
                                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                                     log=log)
                    kill_pairs = set()
                    for e in st.history:
                        if e.get("event") != "death":
                            continue
                        v = e.get("name")
                        for p in (e.get("present") or ()):
                            if p != v:
                                kill_pairs.add((e.get("loop"), v, p))
                    moved = defaultdict(set)
                    pos_by_turn: dict = {}
                    for e in log:
                        if e.get("decision") != "set_card" or e.get("actor") == "mastermind":
                            continue
                        c = e["chosen"]
                        key = (e["loop"], e["day"])
                        if c["card"] in moves_set and c["target_kind"] == "character":
                            moved[key].add(c["target"])
                        if key not in pos_by_turn:
                            v = e.get("view") or {}
                            pos_by_turn[key] = {ch.get("name"): ch.get("area")
                                                for ch in (v.get("characters") or [])
                                                if ch.get("alive")}
                    n = 0
                    for (lp, dy), xs in sorted(moved.items()):
                        p0 = pos_by_turn.get((lp, dy))
                        p1 = pos_by_turn.get((lp, dy + 1))
                        if not p0 or not p1:
                            continue
                        for x in xs:
                            for (l0, v, w) in kill_pairs:
                                if l0 >= lp:
                                    continue
                                y = w if v == x else (v if w == x else None)
                                if y is None:
                                    continue
                                if p0.get(x) and p0.get(y) and p0[x] != p0[y] \
                                        and p1.get(x) and p1.get(x) == p1.get(y):
                                    n += 1
                                    break
                    tot += n
                    if n:
                        games.append((f"{gname}#{seed}", n, st.winner))
            print(f"days={days} perm={perm}: 「退避→合流」席={tot}")
            for g in sorted(games, key=lambda x: -x[1])[:args.top_k]:
                print("   ", g)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-238 検死プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["inventory", "run", "overview", "decoys",
                                    "knobs", "classify", "verifycf", "flip",
                                    "repeat", "selfcancel", "boardpick", "ground", "twochoice", "killpair", "converge"])
    ap.add_argument("--game", default="random_BTX#3")
    ap.add_argument("--games", default="", help="boardpick: 対象局のカンマ区切り")
    ap.add_argument("--days", default="5")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--pro-off", dest="pro_off", default="")
    ap.add_argument("--mm-off", dest="mm_off", default="")
    ap.add_argument("--objective", default="survive",
                    choices=["survive", "no_incident", "no_death"])
    ap.add_argument("--victim", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--deep", action="store_true", help="knobs: 旧切替口・囮も見る")
    ap.add_argument("--fresh-rng", action="store_true")
    ap.add_argument("--two-days", action="store_true")
    ap.add_argument("--top-k", type=int, default=40)
    ap.add_argument("--max-candidates", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    if a.cmd not in ("inventory", "repeat", "selfcancel", "boardpick", "ground", "twochoice", "killpair", "converge"):
        a.days = int(a.days)
    return {"inventory": cmd_inventory, "run": cmd_run, "overview": cmd_overview,
            "decoys": cmd_decoys, "knobs": cmd_knobs, "classify": cmd_classify,
            "verifycf": cmd_verifycf, "flip": cmd_flip, "repeat": cmd_repeat,
            "selfcancel": cmd_selfcancel,
            "boardpick": cmd_boardpick, "ground": cmd_ground,
            "twochoice": cmd_twochoice,
            "killpair": cmd_killpair,
            "converge": cmd_converge}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
