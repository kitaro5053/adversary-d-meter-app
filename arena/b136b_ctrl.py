# -*- coding: utf-8 -*-
"""B-136b：交絡を外して「3席一括計画だけ」を測る対照（計測のみ）。

前レーン＝`docs/監査_B136_3席一括計画のAB_2026-08-02.md`（ハーネス＝`arena/b136_ab.py`）。
そこでの最大の発見＝**`PLAN_ENABLED` は単一の機序ではない**：
`agents/b100_alloc.py:397-401` の `B100_NOPLAN_LAST` 分岐により、計画の有無が
「B-100（絶対防御）がターンの何席目を奪えるか」を同時に切り替えてしまう。

本モジュールは **`B100_MIX`（絶対防御）を切った条件を共通土台にして** `PLAN_ENABLED` の
True/False を測る＝**計画だけの効果**を出す。さらに 2×2（`B100_MIX` × `PLAN_ENABLED`）を
測れるようにして交互作用まで見る。

★AI の採点・挙動・既定値は一切変更しない：
  - 条件の切替は `HeuristicProtagonist` の**クラス属性の一時差し替え**のみ（with 節内だけ）。
  - 観測は `arena.b136_ab` の `plan_probe`／`decide_probe`（戻り値素通し・記録のみ）を**再利用**する
    ＝二重実装をしない。

条件名（`--cond`）:
    m1p0 = B100_MIX=True , PLAN_ENABLED=False  ＝ ①既定（正典値と一致するはず）
    m0p1 = B100_MIX=False, PLAN_ENABLED=True   ＝ ②計画のみ
    m1p1 = B100_MIX=True , PLAN_ENABLED=True   ＝ ③両方（＝B-136 の「ON」）
    m0p0 = B100_MIX=False, PLAN_ENABLED=False  ＝ ④どちらも無し（計画だけの対照の基準）

CLI（前面実行・チャンク分割可）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136b_ctrl ab \
        --days 3 --perms id --conds m0p0,m0p1 --out /tmp/x.json
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136b_ctrl merge \
        --inputs a.json,b.json --pairs m0p0:m0p1
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136b_ctrl plan \
        --days 3 --perm id --cond m0p1 --out /tmp/pl.json
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136b_ctrl flips \
        --inputs a.json --pairs m0p0:m0p1 --out /tmp/fl.json
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136b_ctrl trace \
        --days 3 --perm id --script random_FS --seed 11 --pair m0p0:m0p1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.benchmark import benchmark_scripts
from arena.tie_noise import install_perm, uninstall_perm
from sim import run_game
from sim.loop_race import analyze_script

# ★前レーンの計測部品を再利用（二重実装を避ける）
from arena.b136_ab import (compare, decide_probe, first_divergence, plan_probe,
                           summarize_rows, _key)

PERMS_DEFAULT = "id,rev,h1,h2"

#: 条件名 → クラス属性の差し替え表
CONDS: dict[str, dict] = {
    "m1p0": {"B100_MIX": True,  "PLAN_ENABLED": False},   # ①既定
    "m0p1": {"B100_MIX": False, "PLAN_ENABLED": True},    # ②計画のみ
    "m1p1": {"B100_MIX": True,  "PLAN_ENABLED": True},    # ③両方（B-136 の ON）
    "m0p0": {"B100_MIX": False, "PLAN_ENABLED": False},   # ④どちらも無し
}
COND_LABEL = {"m1p0": "①既定(MIX=T,PLAN=F)", "m0p1": "②計画のみ(MIX=F,PLAN=T)",
              "m1p1": "③両方(MIX=T,PLAN=T)", "m0p0": "④なし(MIX=F,PLAN=F)"}


class cond_flags:
    """条件のクラス属性を with 節の間だけ差し替える（既定値そのものは触らない）。"""

    def __init__(self, cond: str):
        if cond not in CONDS:
            raise SystemExit(f"未知の条件: {cond}（{list(CONDS)}）")
        self.cond = cond
        self.attrs = CONDS[cond]
        self._prev: dict = {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self._prev[k] = getattr(HeuristicProtagonist, k)
            setattr(HeuristicProtagonist, k, v)
        # ★事故対策（`__pycache__` 再利用で切替わっていなかった実績）＝実効値を毎回印字＋assert。
        inst = HeuristicProtagonist(0)
        eff = {k: getattr(inst, k) for k in self.attrs}
        cls = {k: getattr(HeuristicProtagonist, k) for k in self.attrs}
        print(f"    [条件 {self.cond}] class={cls} instance={eff} "
              f"(要求={self.attrs})", flush=True)
        for k, v in self.attrs.items():
            assert eff[k] is v, f"{k} の切替に失敗（実効={eff[k]} 要求={v}）"
        return self

    def __exit__(self, *a):
        for k, v in self._prev.items():
            setattr(HeuristicProtagonist, k, v)
        return False


# ---------------------------------------------------------------------------
# Phase 1: 2×2 の A/B 本体
# ---------------------------------------------------------------------------
def run_leg(days: int, perm: str, cond: str, loops: int = 8,
            start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import loops_to_win
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    t0 = time.time()
    rows = []
    install_perm(perm)
    try:
        with cond_flags(cond):
            for name, seed, sc in scripts[start:end]:
                race = analyze_script(sc).verdict
                ltw, outcome = loops_to_win(sc, seed, loops=loops)
                rows.append({"script": name, "seed": seed,
                             "loops_to_win": ltw, "outcome": outcome,
                             "race": race})
    finally:
        uninstall_perm()
    return {"days": days, "perm": perm, "cond": cond, "loops": loops,
            "start": start, "end": end, "rows": rows,
            "elapsed_sec": round(time.time() - t0, 1),
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)")}


def _fmt(tag: str, s: dict) -> str:
    return (f"{tag}: 防衛={s['defense']:3d}/{s['n']:3d} 平均={s['mean']:.3f} "
            f"L1={s['L1']:2d} loss={s['loss']:2d} fb_loss={s['fb_loss']:2d} "
            f"fb_win={s['fb_win']:2d}")


def cmd_ab(args) -> int:
    perms = [p for p in args.perms.split(",") if p]
    conds = [c.strip() for c in args.conds.split(",") if c.strip()]
    out: dict = {"days": args.days, "legs": {}}
    for perm in perms:
        for cond in conds:
            print(f"  [{args.days}日級 perm={perm} cond={cond}"
                  f"={COND_LABEL[cond]}] games[{args.start}:{args.end}] 開始",
                  flush=True)
            leg = run_leg(args.days, perm, cond, loops=args.loops,
                          start=args.start, end=args.end)
            leg["summary"] = summarize_rows(leg["rows"])
            out["legs"][f"{perm}/{cond}"] = leg
            print("    " + _fmt(f"perm={perm} {cond}", leg["summary"])
                  + f"  ({leg['elapsed_sec']}s)", flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"  → {args.out}", flush=True)
    print(report(out, args.pairs))
    return 0


def report(res: dict, pairs_spec: str = "") -> str:
    legs = res["legs"]
    lines = [f"=== B-136b 対照（{res['days']}日級）==="]
    perms: list[str] = []
    for k in legs:
        p = k.split("/")[0]
        if p not in perms:
            perms.append(p)
    pairs = [tuple(p.split(":")) for p in pairs_spec.split(",") if ":" in p]
    for p in perms:
        for cond in CONDS:
            leg = legs.get(f"{p}/{cond}")
            if leg:
                lines.append("  " + _fmt(f"perm={p:4s} {cond} {COND_LABEL[cond]:24s}",
                                         leg["summary"]))
        for a, b in pairs:
            la, lb = legs.get(f"{p}/{a}"), legs.get(f"{p}/{b}")
            if not (la and lb):
                continue
            c = compare(la["rows"], lb["rows"])
            d_def = lb["summary"]["defense"] - la["summary"]["defense"]
            d_mean = round(lb["summary"]["mean"] - la["summary"]["mean"], 3)
            lines.append(f"    [{a}→{b}] 防衛Δ{d_def:+d} 平均Δ{d_mean:+.3f} "
                         f"flip={c['n_flip']}（改善{c['improved']}／"
                         f"退行{c['worsened']}／同値{c['same_ltw_diff_outcome']}）")
            for f in c["flips"]:
                lines.append(f"      {f['game']}: {f['off']} → {f['on']} "
                             f"(Δ{f['delta']:+d})")
    return "\n".join(lines)


def cmd_merge(args) -> int:
    merged: dict = {"days": None, "legs": {}}
    for path in [p for p in args.inputs.split(",") if p]:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        merged["days"] = merged["days"] or r["days"]
        if r["days"] != merged["days"]:
            print(f"警告: days が混在（{r['days']} vs {merged['days']}）")
        for k, leg in r["legs"].items():
            tgt = merged["legs"].setdefault(
                k, {"days": r["days"], "perm": leg["perm"],
                    "cond": leg["cond"], "rows": []})
            have = {_key(x) for x in tgt["rows"]}
            for row in leg["rows"]:
                if _key(row) not in have:
                    tgt["rows"].append(row)
    for k, leg in merged["legs"].items():
        leg["summary"] = summarize_rows(leg["rows"])
    print(report(merged, args.pairs))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


# ---------------------------------------------------------------------------
# Phase 2: `_plan_turn` の観測（条件ごと）
# ---------------------------------------------------------------------------
def run_plan_probe(days: int, perm: str, cond: str, loops: int = 8,
                   start: int = 0, end: int | None = None) -> dict:
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    games = []
    n_seats_total = n_plan_seats = n_b100 = n_b100_ate = 0
    b100_seatpos: dict[str, int] = {}
    t0 = time.time()
    install_perm(perm)
    try:
        with cond_flags(cond):
            for name, seed, sc in scripts[start:end]:
                probe = replace(sc, loops=loops)
                pp = plan_probe()
                with pp, decide_probe(pp) as dp:
                    mm = HeuristicMastermind(seed)
                    hp = HeuristicProtagonist(seed)
                    run_game(probe, {"mastermind": mm, "p1": hp,
                                     "p2": hp, "p3": hp})
                n_seats_total += len(dp.seats)
                n_plan_seats += sum(1 for s in dp.seats if s["from_plan"])
                n_b100 += sum(1 for s in dp.seats if s["b100_forced"])
                n_b100_ate += sum(1 for s in dp.seats if s["b100_ate_plan"])
                pos = 0
                turn = None
                for s in dp.seats:
                    t = (s["loop"], s["day"])
                    pos = 1 if t != turn else pos + 1
                    turn = t
                    if s["b100_forced"]:
                        k = f"{pos}席目"
                        b100_seatpos[k] = b100_seatpos.get(k, 0) + 1
                games.append({"script": name, "seed": seed,
                              "seats": len(dp.seats),
                              "plan_seats": sum(1 for s in dp.seats
                                                if s["from_plan"]),
                              "b100_seats": sum(1 for s in dp.seats
                                                if s["b100_forced"]),
                              "calls": pp.calls})
    finally:
        uninstall_perm()
    calls = [c for g in games for c in g["calls"]]
    # 計画が「素の貪欲上位3」と異なる割当を出した席の、落とした札／拾った札の種類
    dropped: dict[str, int] = {}
    picked: dict[str, int] = {}
    for c in calls:
        if not c["differs"]:
            continue
        pl = {(x[0], x[1], x[2]) for x in c["plan"]}
        gr = {(x[0], x[1], x[2]) for x in c["greedy_top3"]}
        for x in gr - pl:
            dropped[x[0]] = dropped.get(x[0], 0) + 1
        for x in pl - gr:
            picked[x[0]] = picked.get(x[0], 0) + 1
    return {"days": days, "perm": perm, "cond": cond,
            "start": start, "end": end,
            "n_seats_total": n_seats_total,
            "n_calls": len(calls),
            "n_calls_vip": sum(1 for c in calls if c["vip_risk"]),
            "n_calls_overdemand": sum(1 for c in calls if not c["vip_risk"]),
            "n_calls_differ": sum(1 for c in calls if c["differs"]),
            "n_strong_hist": _hist([c["n_strong_targets"] for c in calls]),
            "dropped": dropped, "picked": picked,
            "n_plan_seats": n_plan_seats,
            "n_planned_entries": sum(c["plan_len"] for c in calls),
            "n_b100_seats": n_b100,
            "n_b100_ate_plan": n_b100_ate,
            "b100_seat_position": b100_seatpos,
            "games": games,
            "elapsed_sec": round(time.time() - t0, 1),
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)")}


def _hist(vals) -> dict:
    h: dict[str, int] = {}
    for v in vals:
        h[str(v)] = h.get(str(v), 0) + 1
    return dict(sorted(h.items(), key=lambda kv: int(kv[0])))


def cmd_plan(args) -> int:
    res = run_plan_probe(args.days, args.perm, args.cond, loops=args.loops,
                         start=args.start, end=args.end)
    print(f"=== `_plan_turn` 観測（{args.days}日級 perm={args.perm} "
          f"cond={args.cond}={COND_LABEL[args.cond]}）===")
    print(f"  総席数={res['n_seats_total']}  起動ターン数={res['n_calls']}"
          f"（VIPリスク経路={res['n_calls_vip']} ／ 過剰需要経路="
          f"{res['n_calls_overdemand']}）")
    print(f"  強需要対象数の分布={res['n_strong_hist']}")
    print(f"  計画が発行した割当={res['n_planned_entries']}  "
          f"実際に計画駆動だった席={res['n_plan_seats']}  "
          f"素の貪欲上位3と異なる割当={res['n_calls_differ']}")
    print(f"  落とした札={res['dropped']}")
    print(f"  拾った札  ={res['picked']}")
    print(f"  B-100 が強制した席={res['n_b100_seats']}"
          f"（うち計画を食った席={res['n_b100_ate_plan']}）"
          f"  ターン内位置={res['b100_seat_position']}")
    if args.verbose:
        for g in res["games"]:
            for c in g["calls"]:
                print(f"    {g['script']}#{g['seed']} L{c['loop']}D{c['day']} "
                      f"seat={c['seat']} vip={c['vip_risk']} "
                      f"強需要対象={c['n_strong_targets']} differs={c['differs']}")
                print(f"        plan  ={c['plan']}")
                print(f"        greedy={c['greedy_top3']}")
                print(f"        top   ={c['top_scores']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


# ---------------------------------------------------------------------------
# Phase 2b: 1局のトレースと初分岐（条件ペア）
# ---------------------------------------------------------------------------
def trace_game(sc, seed: int, cond: str, perm: str, loops: int = 8) -> dict:
    pp = plan_probe()
    install_perm(perm)
    try:
        with cond_flags(cond), pp, decide_probe(pp) as dp:
            probe = replace(sc, loops=loops)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                        "p2": hp, "p3": hp})
            plan_calls = pp.calls
            seats = dp.seats
    finally:
        uninstall_perm()
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw, outcome = loops + 1, ("fb_win" if state.winner == "protagonist"
                                   else "fb_loss")
    else:
        ltw, outcome = loops + 1, "loss"
    return {"cond": cond, "ltw": ltw, "outcome": outcome,
            "seats": seats, "plan_calls": plan_calls}


def _drv(s) -> str:
    if not s:
        return "-"
    if s.get("b100_forced"):
        return "B100強制"
    if s.get("from_plan"):
        return "計画割当"
    return "通常採点"


def cmd_trace(args) -> int:
    a_cond, b_cond = args.pair.split(":")
    target = None
    for name, seed, sc in benchmark_scripts(days=args.days):
        if name == args.script and seed == args.seed:
            target = sc
            break
    if target is None:
        print(f"該当局なし: {args.script}#{args.seed}（{args.days}日級）")
        return 2
    a = trace_game(target, args.seed, a_cond, args.perm, loops=args.loops)
    b = trace_game(target, args.seed, b_cond, args.perm, loops=args.loops)
    print(f"=== トレース {args.script}#{args.seed}（{args.days}日級 "
          f"perm={args.perm} {a_cond}→{b_cond}）===")
    print(f"  {a_cond}: ltw={a['ltw']} {a['outcome']} 席数={len(a['seats'])} "
          f"plan起動={len(a['plan_calls'])}")
    print(f"  {b_cond}: ltw={b['ltw']} {b['outcome']} 席数={len(b['seats'])} "
          f"plan起動={len(b['plan_calls'])}")
    d = first_divergence(a, b)
    i = d.get("index")
    print(f"  初分岐 #{i}: {a_cond}[{_drv(d.get('off'))}] "
          f"{json.dumps(d.get('off'), ensure_ascii=False)}")
    print(f"             {b_cond}[{_drv(d.get('on'))}] "
          f"{json.dumps(d.get('on'), ensure_ascii=False)}")
    if i is not None and d.get("on"):
        t = (d["on"].get("loop"), d["on"].get("day"))
        for c in b["plan_calls"]:
            if (c["loop"], c["day"]) == t:
                print(f"  その ターン の計画（{b_cond}）: L{c['loop']}D{c['day']} "
                      f"seat={c['seat']} vip={c['vip_risk']} "
                      f"強需要対象={c['n_strong_targets']} differs={c['differs']}")
                print(f"        plan  ={c['plan']}")
                print(f"        greedy={c['greedy_top3']}")
                print(f"        top   ={c['top_scores']}")
        # 分岐ターンの席列を両条件で並べる
        print(f"  分岐ターン L{t[0]}D{t[1]} の席列:")
        for tag, r in ((a_cond, a), (b_cond, b)):
            row = [f"{s['seat']}:{s['card']}→{s['target']}[{_drv(s)}]"
                   for s in r["seats"] if (s["loop"], s["day"]) == t]
            print(f"    {tag}: {row}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"a": a, "b": b, "divergence": d}, f, ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


def cmd_flips(args) -> int:
    """条件ペアで flip した局の**初分岐**を全数、機序つきで出す。"""
    merged: dict = {"legs": {}}
    days = None
    for path in [p for p in args.inputs.split(",") if p]:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        days = days or r["days"]
        for k, leg in r["legs"].items():
            merged["legs"].setdefault(k, leg)
    scripts = {f"{n}#{s}": sc for n, s, sc in benchmark_scripts(days=days)}
    perms = sorted({k.split("/")[0] for k in merged["legs"]})
    pairs = [tuple(p.split(":")) for p in args.pairs.split(",") if ":" in p]
    rows = []
    for perm in perms:
        for ca, cb in pairs:
            la = merged["legs"].get(f"{perm}/{ca}")
            lb = merged["legs"].get(f"{perm}/{cb}")
            if not (la and lb):
                continue
            c = compare(la["rows"], lb["rows"])
            print(f"=== {days}日級 perm={perm} [{ca}→{cb}]: flip {c['n_flip']} 件 ===",
                  flush=True)
            for f in c["flips"]:
                sc = scripts.get(f["game"])
                seed = int(f["game"].split("#")[1])
                a = trace_game(sc, seed, ca, perm, loops=args.loops)
                b = trace_game(sc, seed, cb, perm, loops=args.loops)
                d = first_divergence(a, b)
                i = d.get("index")
                so = d.get("off") or {}
                sn = d.get("on") or {}
                # ★分岐ターンの計画が「素の貪欲上位3」と違う組だったか
                #   （＝真に「4需要 vs 3席」の取捨が結果を変えたのか、それとも
                #     計画が同点帯の解け方を先頭席で固定しただけなのか）を記録する。
                turn_call = None
                if sn:
                    for cc in b["plan_calls"]:
                        if (cc["loop"], cc["day"]) == (sn.get("loop"),
                                                       sn.get("day")):
                            turn_call = cc
                            break
                row = {"perm": perm, "pair": f"{ca}->{cb}", "game": f["game"],
                       "delta": f["delta"], "a": f["off"], "b": f["on"],
                       "index": i,
                       "turn": f"L{sn.get('loop')}D{sn.get('day')}" if sn else None,
                       "seat": sn.get("seat"),
                       "a_drv": _drv(so), "b_drv": _drv(sn),
                       "a_card": f"{so.get('card')}→{so.get('target')}",
                       "b_card": f"{sn.get('card')}→{sn.get('target')}",
                       "b_plan_built": sn.get("plan_built"),
                       "turn_differs": (turn_call or {}).get("differs"),
                       "turn_nstrong": (turn_call or {}).get("n_strong_targets"),
                       "turn_plan": (turn_call or {}).get("plan"),
                       "turn_greedy": (turn_call or {}).get("greedy_top3"),
                       "turn_top": (turn_call or {}).get("top_scores"),
                       "trace_a_ltw": a["ltw"], "trace_b_ltw": b["ltw"],
                       "repro": (a["ltw"] == f["off"][0]
                                 and b["ltw"] == f["on"][0])}
                rows.append(row)
                print(f"  {f['game']:18s} Δ{f['delta']:+d} {f['off']}→{f['on']} "
                      f"初分岐 #{i} {row['turn']} {row['seat']}: "
                      f"{ca}[{row['a_drv']}] {row['a_card']}  ／  "
                      f"{cb}[{row['b_drv']}] {row['b_card']}"
                      f"  [分岐ターンの計画 differs={row['turn_differs']} "
                      f"強需要={row['turn_nstrong']}]"
                      f"{'' if row['repro'] else '  ※トレース非再現'}", flush=True)
    print("\n=== 分岐ターンの計画が『素の貪欲上位3』と違ったか ===")
    dz: dict[str, dict[str, int]] = {}
    for r in rows:
        k = f"differs={r['turn_differs']}"
        b = "改善" if r["delta"] < 0 else ("退行" if r["delta"] > 0 else "同値")
        dz.setdefault(k, {}).setdefault(b, 0)
        dz[k][b] += 1
    for k in sorted(dz, key=str):
        print(f"  {k:16s} {dz[k]}")
    print("\n=== 初分岐の駆動源クロス集計 ===")
    cross: dict[str, dict[str, int]] = {}
    for r in rows:
        k = f"{r['pair']} {r['a_drv']}→{r['b_drv']}"
        b = "改善" if r["delta"] < 0 else ("退行" if r["delta"] > 0 else "同値")
        cross.setdefault(k, {}).setdefault(b, 0)
        cross[k][b] += 1
    for k in sorted(cross):
        print(f"  {k:32s} {cross[k]}")
    n_bad = sum(1 for r in rows if not r["repro"])
    print(f"  計 {len(rows)} 件（トレース非再現 {n_bad} 件）")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"days": days, "rows": rows, "cross": cross},
                      f, ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="B-136b: 計画だけの対照（B100_MIX × PLAN_ENABLED の 2×2・計測のみ）")
    ap.add_argument("cmd", choices=["ab", "merge", "plan", "trace", "flips"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perms", type=str, default=PERMS_DEFAULT)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--conds", type=str, default="m0p0,m0p1")
    ap.add_argument("--cond", type=str, default="m0p1")
    ap.add_argument("--pairs", type=str, default="m0p0:m0p1")
    ap.add_argument("--pair", type=str, default="m0p0:m0p1")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--script", type=str, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--inputs", type=str, default="")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "ab":
        return cmd_ab(args)
    if args.cmd == "merge":
        return cmd_merge(args)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "flips":
        return cmd_flips(args)
    return cmd_trace(args)


if __name__ == "__main__":
    sys.exit(main())
