# -*- coding: utf-8 -*-
"""B-136 Step 1：3席一括計画（`_plan_turn`）の再有効化 A/B（計測のみ）。

起票＝`docs/バックログ_構想メモ_FableA.md` §8（B-136）。発端＝B-130。

`agents/heuristic_protagonist.py` の `PLAN_ENABLED`（既定 False）は 2026-07-09 の A/B で
「正味ゲインなし」と判定されて OFF のまま置かれている。本モジュールは**既定値を書き換えず**
（ハーネス側でクラス属性を差し替えて）両ベンチ×perm 4条件の A/B を測り直す。

★このモジュールは AI の採点・挙動を一切変更しない：
  - A/B の切替は `HeuristicProtagonist.PLAN_ENABLED` の**クラス属性の一時差し替え**のみ。
  - Phase 2 の観測は `_plan_turn` のラッパ（戻り値素通し）と
    `agents.heuristic_protagonist.B100_HOOK`（既定 None の計測専用フック・戻り値不使用）だけ。

CLI（前面実行・チャンク分割可）:
    # Phase 1: A/B 本体
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136_ab ab \
        --days 3 --perms id --plan off,on --out /tmp/ab3_id.json
    ... --start 0 --end 65        # チャンク分割（各チャンクの RC を個別確認）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136_ab merge \
        --inputs a.json,b.json    # 束ねて比較表と flip 全数を出す

    # Phase 2: 機序の観測（ON 時の `_plan_turn` 起動数と割当）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136_ab plan \
        --days 3 --perm id --out /tmp/plan3.json

    # Phase 2b: 1局の per-seat トレースを OFF/ON で取り、初分岐を出す
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b136_ab trace \
        --days 3 --perm id --script random_BTX --seed 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace

import agents.heuristic_protagonist as HP
from agents import HeuristicMastermind, HeuristicProtagonist
from arena.benchmark import benchmark_scripts
from arena.tie_noise import install_perm, uninstall_perm
from sim import run_game
from sim.loop_race import analyze_script

PERMS_DEFAULT = "id,rev,h1,h2"


# ---------------------------------------------------------------------------
# 切替（既定値は触らない＝クラス属性の一時差し替え）
# ---------------------------------------------------------------------------
class plan_flag:
    """`HeuristicProtagonist.PLAN_ENABLED` を with 節の間だけ差し替える。"""

    def __init__(self, value: bool):
        self.value = bool(value)
        self._prev = None

    def __enter__(self):
        self._prev = HeuristicProtagonist.PLAN_ENABLED
        HeuristicProtagonist.PLAN_ENABLED = self.value
        # ★事故対策（__pycache__ 再利用で切替わっていなかった実績）＝実効値を毎回印字。
        eff = HeuristicProtagonist(0).PLAN_ENABLED
        print(f"    [PLAN_ENABLED] class={HeuristicProtagonist.PLAN_ENABLED} "
              f"instance={eff} (要求={self.value})", flush=True)
        assert eff is self.value, "PLAN_ENABLED の切替に失敗"
        return self

    def __exit__(self, *a):
        HeuristicProtagonist.PLAN_ENABLED = self._prev
        return False


# ---------------------------------------------------------------------------
# Phase 1: A/B 本体
# ---------------------------------------------------------------------------
def _one_game(sc, seed: int, loops: int) -> tuple[int, str]:
    """`arena.benchmark.loops_to_win` と同一（依存を明示するため呼び直し）。"""
    from arena.benchmark import loops_to_win
    return loops_to_win(sc, seed, loops=loops)


def run_leg(days: int, perm: str, plan_on: bool, loops: int = 8,
            start: int = 0, end: int | None = None) -> dict:
    """1条件（days × perm × PLAN_ENABLED）を回して per-game 行を返す。"""
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    t0 = time.time()
    rows = []
    install_perm(perm)
    try:
        with plan_flag(plan_on):
            for name, seed, sc in scripts[start:end]:
                race = analyze_script(sc).verdict
                ltw, outcome = _one_game(sc, seed, loops)
                rows.append({"script": name, "seed": seed,
                             "loops_to_win": ltw, "outcome": outcome,
                             "race": race})
    finally:
        uninstall_perm()
    return {"days": days, "perm": perm, "plan": bool(plan_on), "loops": loops,
            "start": start, "end": end, "rows": rows,
            "elapsed_sec": round(time.time() - t0, 1),
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)")}


def summarize_rows(rows: list[dict]) -> dict:
    """防衛数・平均・L1・loss（と全 outcome 内訳）。"""
    n = len(rows)
    vals = [r["loops_to_win"] for r in rows]
    outc: dict[str, int] = {}
    for r in rows:
        outc[r["outcome"]] = outc.get(r["outcome"], 0) + 1
    return {
        "n": n,
        "defense": sum(1 for r in rows if r["outcome"] == "defense"),
        "mean": round(sum(vals) / n, 3) if n else None,
        "L1": sum(1 for r in rows if r["loops_to_win"] == 1),
        "loss": outc.get("loss", 0),
        "fb_loss": outc.get("fb_loss", 0),
        "fb_win": outc.get("fb_win", 0),
        "outcomes": outc,
    }


def _key(r: dict) -> str:
    return f'{r["script"]}#{r["seed"]}'


def compare(off_rows: list[dict], on_rows: list[dict]) -> dict:
    """per-game flip の全数（OFF→ON）。"""
    o = {_key(r): r for r in off_rows}
    n = {_key(r): r for r in on_rows}
    flips = []
    for k in sorted(set(o) & set(n)):
        a, b = o[k], n[k]
        if a["loops_to_win"] != b["loops_to_win"] or a["outcome"] != b["outcome"]:
            flips.append({"game": k,
                          "off": [a["loops_to_win"], a["outcome"]],
                          "on": [b["loops_to_win"], b["outcome"]],
                          "delta": b["loops_to_win"] - a["loops_to_win"],
                          "race": a.get("race")})
    return {"n_flip": len(flips),
            "improved": sum(1 for f in flips if f["delta"] < 0),
            "worsened": sum(1 for f in flips if f["delta"] > 0),
            "same_ltw_diff_outcome": sum(1 for f in flips if f["delta"] == 0),
            "flips": flips}


# ---------------------------------------------------------------------------
# Phase 2: `_plan_turn` の起動数と割当の観測（ON 時）
# ---------------------------------------------------------------------------
_ORIG_PLAN_TURN = HeuristicProtagonist._plan_turn


def _step2_candidates(view: dict, opts: list[dict]) -> tuple[list, list]:
    """バックログ §8 Step 2 の2項が、この3手組に**該当するか**を数える（観測のみ）。

    (a) 同じ組に `友好+n→X` があり、X が同じ組の `不安-1→Y` の**抑止役になりうる**
        （`defense_plan.suppressors_for(..., require_funded=False)`＝B-130 の単一ソース）。
    (b) 同じ組に `移動禁止→T` があり、T に**脚本家の札**が置かれている。
    """
    a: list = []
    b: list = []
    try:
        from agents.defense_plan import suppressors_for
    except Exception:
        return a, b
    goods = [o for o in opts if o["card"] in ("友好+1", "友好+2")
             and o.get("target_kind") == "character"]
    cools = [o for o in opts if o["card"] == "不安-1"
             and o.get("target_kind") == "character"]
    for c in cools:
        try:
            sup = suppressors_for(view, c["target"], roles=None,
                                  require_funded=False)
        except Exception:
            continue
        for g in goods:
            if g["target"] in sup:
                a.append((g["card"], g["target"], c["target"]))
    mm_tgts = {p.get("target") for p in (view.get("placements") or [])
               if p.get("owner") == "mastermind"}
    for o in opts:
        if o["card"] == "移動禁止" and o["target"] in mm_tgts:
            b.append((o["card"], o["target"]))
    return a, b


class plan_probe:
    """`_plan_turn` をラップして起動と割当を記録する（戻り値は素通し）。"""

    def __init__(self):
        self.calls: list[dict] = []
        self._ctx: dict | None = None

    def __enter__(self):
        probe = self

        def _wrapped(self_a, view, options, score_fn, vip_risk=None):
            scored = sorted(((score_fn(o), o) for o in options), key=lambda x: -x[0])
            strong = {(o["target"], o.get("target_kind"))
                      for s, o in scored if s >= 72.0}
            out = _ORIG_PLAN_TURN(self_a, view, options, score_fn,
                                  vip_risk=vip_risk)
            # 素の逐次貪欲（対象重複禁止・暗躍禁止1枚のみを課した上位3手）＝
            # 「計画が通常採点と違う手を出したか」の粗い目安（席間フラグは織り込まない）。
            g, used_t, used_k = [], set(), False
            for s, o in scored:
                t = (o["target"], o.get("target_kind"))
                if t in used_t or (o["card"] == "暗躍禁止" and used_k):
                    continue
                g.append((o["card"], o["target"], o.get("target_kind")))
                used_t.add(t)
                used_k = used_k or o["card"] == "暗躍禁止"
                if len(g) == 3:
                    break
            plan = [(o["card"], o["target"], o.get("target_kind")) for o in out]
            s2a, s2b = _step2_candidates(view, out)
            probe.calls.append({
                "step2_redundant_cool": s2a,   # §8 (a)：抑止役投資と 不安-1 の重複
                "step2_pin_on_mm_card": s2b,   # §8 (b)：mm札のある対象への 移動禁止
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"),
                "vip_risk": bool(vip_risk),
                "n_strong_targets": len(strong),
                "n_options": len(options),
                "top_scores": [round(s, 2) for s, _o in scored[:6]],
                "plan": plan,
                "greedy_top3": g,
                "differs": plan != g,
                "plan_len": len(plan),
            })
            return out

        HeuristicProtagonist._plan_turn = _wrapped
        return self

    def __exit__(self, *a):
        HeuristicProtagonist._plan_turn = _ORIG_PLAN_TURN
        return False


def run_plan_probe(days: int, perm: str, plan_on: bool, loops: int = 8,
                   start: int = 0, end: int | None = None) -> dict:
    """ON/OFF で `_plan_turn` の起動を数え上げ、席数も数える。"""
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    games = []
    n_seats_total = n_plan_seats = n_b100 = n_b100_ate = 0
    b100_seatpos: dict[str, int] = {}
    t0 = time.time()
    install_perm(perm)
    try:
        with plan_flag(plan_on):
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
                # B-100 が強制した席が「そのターンの何席目」かの分布
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
    return {"days": days, "perm": perm, "plan": bool(plan_on),
            "start": start, "end": end,
            "n_seats_total": n_seats_total,
            "n_calls": len(calls),
            "n_calls_vip": sum(1 for c in calls if c["vip_risk"]),
            "n_calls_overdemand": sum(1 for c in calls if not c["vip_risk"]),
            "n_calls_differ": sum(1 for c in calls if c["differs"]),
            "n_step2_redundant_cool": sum(1 for c in calls
                                          if c["step2_redundant_cool"]),
            "n_step2_pin_on_mm_card": sum(1 for c in calls
                                          if c["step2_pin_on_mm_card"]),
            "n_plan_seats": n_plan_seats,
            "n_planned_entries": sum(c["plan_len"] for c in calls),
            "n_b100_seats": n_b100,
            "n_b100_ate_plan": n_b100_ate,
            "b100_seat_position": b100_seatpos,
            "games": games,
            "elapsed_sec": round(time.time() - t0, 1),
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)")}


# ---------------------------------------------------------------------------
# Phase 2b: 1局の per-seat トレース（OFF/ON）と初分岐
# ---------------------------------------------------------------------------
# ★`decide` そのものを包む観測（`install_perm` と同型のラッパ・並べ替えはしない）。
#   B100_HOOK は decide の**末尾**で呼ばれる＝席間協調フラグ（`_planned_moves`・
#   `_cooler_invested` 等）が既に更新済みで、そこで再計算した点数は
#   「決定時の点数」ではない。計画駆動の席を正確に数えるには decide の**前後**で
#   `_turn_plan` を見る必要がある。
_ORIG_DECIDE_B136 = HeuristicProtagonist.decide


class decide_probe:
    """`decide` の前後で `_turn_plan` と `_b100_seats` を撮り、席の駆動源を分類する。

    駆動源は3つ（`decide` の選択順序＝`agents/b100_alloc.intended_move` の docstring）：
      ①B-100 の絶対防御による強制 ②`_turn_plan.pop(0)` ③`max(options, key=score)`。
    ★① の検出は `_b100_log` の伸び（`heuristic_protagonist.py:793,877`＝両経路とも
    強制のたびに1件 append・リセットなし）で見る。`_b100_seats` は**ターン境界で 0 に
    戻される**ので、ターン先頭席では前後比較が成立しない（実測で取りこぼした）。
    計画は先頭席の `decide` の**中で**作られるので、入口の長さだけでは足りない
    ＝`plan_probe` と突き合わせて「この decide 中に作られた計画の長さ」を使う。
    """

    def __init__(self, pp: "plan_probe | None" = None):
        self.seats: list[dict] = []
        self.pp = pp

    def __enter__(self):
        probe = self
        # ★`install_perm` も decide を差し替える＝**今の** decide を包んで戻す
        #   （モジュール読込時の原本を掴むと perm が外れる）。
        inner = HeuristicProtagonist.decide
        self._inner = inner

        def _wrapped(self_a, view, decision, options):
            if decision != "set_card":
                return inner(self_a, view, decision, options)
            before = list(getattr(self_a, "_turn_plan", None) or [])
            b100_before = len(getattr(self_a, "_b100_log", ()) or ())
            n_calls_before = len(probe.pp.calls) if probe.pp is not None else 0
            best = inner(self_a, view, decision, options)
            after = list(getattr(self_a, "_turn_plan", None) or [])
            b100_after = len(getattr(self_a, "_b100_log", ()) or ())
            built = None
            if probe.pp is not None and len(probe.pp.calls) > n_calls_before:
                built = probe.pp.calls[-1]["plan_len"]
            eff_before = built if built is not None else len(before)
            forced = b100_after > b100_before
            probe.seats.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"),
                "card": best.get("card"), "target": best.get("target"),
                "kind": best.get("target_kind"),
                "plan_in": eff_before, "plan_out": len(after),
                "plan_built": built,
                "b100_forced": forced,
                # 計画が1件消費され、かつ B-100 の強制ではない＝計画駆動の席
                "from_plan": (eff_before - len(after) == 1) and not forced,
                # B-100 が強制し、その席で計画を1件食った（`B100_CONSUME_PLAN`）
                "b100_ate_plan": forced and (eff_before - len(after) == 1),
            })
            return best

        HeuristicProtagonist.decide = _wrapped
        return self

    def __exit__(self, *a):
        HeuristicProtagonist.decide = self._inner
        return False


def trace_game(sc, seed: int, plan_on: bool, perm: str,
               loops: int = 8) -> dict:
    plan_calls: list[dict] = []

    pp = plan_probe()
    install_perm(perm)
    try:
        with plan_flag(plan_on), pp, decide_probe(pp) as dp:
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
    return {"plan": bool(plan_on), "ltw": ltw, "outcome": outcome,
            "seats": seats, "plan_calls": plan_calls}


def first_divergence(a: dict, b: dict) -> dict:
    """OFF/ON の席列を先頭から突き合わせ、最初に食い違った席を返す。"""
    sa, sb = a["seats"], b["seats"]
    for i, (x, y) in enumerate(zip(sa, sb)):
        kx = (x.get("loop"), x.get("day"), x.get("seat"),
              x.get("card"), x.get("target"), x.get("kind"))
        ky = (y.get("loop"), y.get("day"), y.get("seat"),
              y.get("card"), y.get("target"), y.get("kind"))
        if kx != ky:
            return {"index": i, "off": x, "on": y}
    if len(sa) != len(sb):
        return {"index": min(len(sa), len(sb)), "off": None, "on": None,
                "note": "席数が異なる（分岐なしで長さ差）"}
    return {"index": None, "note": "席列は完全一致"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt(tag: str, s: dict) -> str:
    return (f"{tag}: 防衛={s['defense']:3d}/{s['n']:3d} 平均={s['mean']:.3f} "
            f"L1={s['L1']:2d} loss={s['loss']:2d} fb_loss={s['fb_loss']:2d} "
            f"fb_win={s['fb_win']:2d}")


def cmd_ab(args) -> int:
    perms = [p for p in args.perms.split(",") if p]
    plans = [p.strip().lower() for p in args.plan.split(",") if p.strip()]
    out: dict = {"days": args.days, "legs": {}}
    for perm in perms:
        for pl in plans:
            on = pl in ("on", "true", "1")
            print(f"  [{args.days}日級 perm={perm} PLAN={'ON' if on else 'OFF'}] "
                  f"games[{args.start}:{args.end}] 開始", flush=True)
            leg = run_leg(args.days, perm, on, loops=args.loops,
                          start=args.start, end=args.end)
            s = summarize_rows(leg["rows"])
            leg["summary"] = s
            out["legs"][f"{perm}/{'on' if on else 'off'}"] = leg
            print("    " + _fmt(f"perm={perm} PLAN={'ON ' if on else 'OFF'}", s)
                  + f"  ({leg['elapsed_sec']}s)", flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"  → {args.out}", flush=True)
    print(report_ab(out))
    return 0


def report_ab(res: dict) -> str:
    lines = [f"=== B-136 A/B（{res['days']}日級）==="]
    legs = res["legs"]
    perms = []
    for k in legs:
        p = k.split("/")[0]
        if p not in perms:
            perms.append(p)
    for p in perms:
        off, on = legs.get(f"{p}/off"), legs.get(f"{p}/on")
        if off:
            lines.append("  " + _fmt(f"perm={p:4s} OFF", off["summary"]))
        if on:
            lines.append("  " + _fmt(f"perm={p:4s} ON ", on["summary"]))
        if off and on:
            c = compare(off["rows"], on["rows"])
            lines.append(f"    flip={c['n_flip']} "
                         f"（改善{c['improved']}／退行{c['worsened']}／"
                         f"同値{c['same_ltw_diff_outcome']}）")
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
                    "plan": leg["plan"], "rows": []})
            have = {_key(x) for x in tgt["rows"]}
            for row in leg["rows"]:
                if _key(row) not in have:
                    tgt["rows"].append(row)
    for k, leg in merged["legs"].items():
        leg["summary"] = summarize_rows(leg["rows"])
    print(report_ab(merged))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


def cmd_plan(args) -> int:
    on = args.plan.strip().lower() in ("on", "true", "1")
    res = run_plan_probe(args.days, args.perm, on, loops=args.loops,
                         start=args.start, end=args.end)
    print(f"=== `_plan_turn` 観測（{args.days}日級 perm={args.perm} "
          f"PLAN={'ON' if on else 'OFF'}）===")
    print(f"  総席数={res['n_seats_total']}  起動ターン数={res['n_calls']}"
          f"（VIPリスク経路={res['n_calls_vip']} ／ 過剰需要経路="
          f"{res['n_calls_overdemand']}）")
    print(f"  計画が発行した割当={res['n_planned_entries']}  "
          f"実際に計画駆動だった席={res['n_plan_seats']}  "
          f"素の貪欲上位3と異なる割当={res['n_calls_differ']}")
    print(f"  §8 Step2 候補: (a)抑止役投資と不安-1 の重複={res['n_step2_redundant_cool']} "
          f"ターン ／ (b)mm札のある対象への移動禁止={res['n_step2_pin_on_mm_card']} ターン")
    print(f"  B-100 が強制した席={res['n_b100_seats']}"
          f"（うち計画を食った席={res['n_b100_ate_plan']}）"
          f"  ターン内位置={res['b100_seat_position']}")
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


def cmd_trace(args) -> int:
    target = None
    for name, seed, sc in benchmark_scripts(days=args.days):
        if name == args.script and seed == args.seed:
            target = sc
            break
    if target is None:
        print(f"該当局なし: {args.script}#{args.seed}（{args.days}日級）")
        return 2
    a = trace_game(target, args.seed, False, args.perm, loops=args.loops)
    b = trace_game(target, args.seed, True, args.perm, loops=args.loops)
    print(f"=== トレース {args.script}#{args.seed}（{args.days}日級 "
          f"perm={args.perm}）===")
    print(f"  OFF: ltw={a['ltw']} {a['outcome']} 席数={len(a['seats'])} "
          f"plan起動={len(a['plan_calls'])}")
    print(f"  ON : ltw={b['ltw']} {b['outcome']} 席数={len(b['seats'])} "
          f"plan起動={len(b['plan_calls'])}")
    d = first_divergence(a, b)
    print(f"  初分岐: {json.dumps(d, ensure_ascii=False)}")
    if b["plan_calls"]:
        print("  ON の `_plan_turn` 起動:")
        for c in b["plan_calls"]:
            print(f"    L{c['loop']}D{c['day']} seat={c['seat']} "
                  f"vip={c['vip_risk']} 強需要対象={c['n_strong_targets']} "
                  f"differs={c['differs']}")
            print(f"        plan  ={c['plan']}")
            print(f"        greedy={c['greedy_top3']}")
            print(f"        top   ={c['top_scores']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"off": a, "on": b, "divergence": d}, f,
                      ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


def cmd_flips(args) -> int:
    """A/B の JSON を読み、flip した局の**初分岐**を全数、機序つきで出す。

    機序＝その席の駆動源（`decide_probe`）。OFF/ON の両側を見て
      - `b100_forced`＝B-100 の絶対防御が強制した席
      - `from_plan`  ＝3席一括計画の割当を実行した席
      - どちらでもない＝通常採点（`max(options, key=score)`）
    のどれが入れ替わったかで分類する。
    """
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
    rows = []
    for perm in perms:
        off, on = merged["legs"].get(f"{perm}/off"), merged["legs"].get(f"{perm}/on")
        if not (off and on):
            continue
        c = compare(off["rows"], on["rows"])
        print(f"=== {days}日級 perm={perm}: flip {c['n_flip']} 件 ===", flush=True)
        for f in c["flips"]:
            sc = scripts.get(f["game"])
            seed = int(f["game"].split("#")[1])
            a = trace_game(sc, seed, False, perm, loops=args.loops)
            b = trace_game(sc, seed, True, perm, loops=args.loops)
            d = first_divergence(a, b)
            i = d.get("index")
            so = d.get("off") or {}
            sn = d.get("on") or {}

            def _drv(s):
                if not s:
                    return "-"
                if s.get("b100_forced"):
                    return "B100強制"
                if s.get("from_plan"):
                    return "計画割当"
                return "通常採点"
            row = {"perm": perm, "game": f["game"], "delta": f["delta"],
                   "off": f["off"], "on": f["on"], "index": i,
                   "turn": f"L{sn.get('loop')}D{sn.get('day')}" if sn else None,
                   "seat": sn.get("seat"),
                   "off_drv": _drv(so), "on_drv": _drv(sn),
                   "off_card": f"{so.get('card')}→{so.get('target')}",
                   "on_card": f"{sn.get('card')}→{sn.get('target')}",
                   "on_plan_built": sn.get("plan_built"),
                   "trace_off_ltw": a["ltw"], "trace_on_ltw": b["ltw"],
                   "repro": (a["ltw"] == f["off"][0] and b["ltw"] == f["on"][0])}
            rows.append(row)
            print(f"  {f['game']:16s} Δ{f['delta']:+d} {f['off']}→{f['on']} "
                  f"初分岐 #{i} {row['turn']} {row['seat']}: "
                  f"OFF[{row['off_drv']}] {row['off_card']}  ／  "
                  f"ON[{row['on_drv']}] {row['on_card']}"
                  f"{'' if row['repro'] else '  ※トレース非再現'}", flush=True)
    print("\n=== 初分岐の駆動源クロス集計（OFF側→ON側）===")
    cross: dict[str, dict[str, int]] = {}
    for r in rows:
        k = f"{r['off_drv']}→{r['on_drv']}"
        b = "改善" if r["delta"] < 0 else ("退行" if r["delta"] > 0 else "同値")
        cross.setdefault(k, {}).setdefault(b, 0)
        cross[k][b] += 1
    for k in sorted(cross):
        print(f"  {k:20s} {cross[k]}")
    n_bad = sum(1 for r in rows if not r["repro"])
    print(f"  計 {len(rows)} 件（トレース非再現 {n_bad} 件）")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"days": days, "rows": rows, "cross": cross},
                      f, ensure_ascii=False)
        print(f"  → {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-136 Step 1: PLAN_ENABLED の A/B（計測のみ）")
    ap.add_argument("cmd", choices=["ab", "merge", "plan", "trace", "flips"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perms", type=str, default=PERMS_DEFAULT)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--plan", type=str, default="off,on")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--script", type=str, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--inputs", type=str, default="")
    ap.add_argument("--out", type=str, default=None)
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
