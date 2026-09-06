# -*- coding: utf-8 -*-
"""B-134：`board_defeat` の距離モデル（`progress`）が「暗躍+2 は1枚で2個載る」を
数えていないことの影響範囲を数え上げるシャドー集計（挙動不変・計測のみ）。

起票元＝`docs/監査_B131_絶対防御の確率合算_2026-08-01.md` §8 申し送り1。

機序（B-131 §2 の引用）：
    `agents/defense_plan.py` `_threat_board_defeat`
        progress = 0.9 if gap <= 1 else 0.35
        threat_p = rule_p * progress
    `gap`（`unstoppable_supply_gap`）＝「止まらない供給を全部使ってもなお
    **行動解決の暗躍カードが何個要るか**」＝**個数**であって**枚数**ではない。
    脚本家の `暗躍+2`（1/loop）は **1枚で2個** 載せる＝ gap=2 の板も**1枚で届く**。

計測（`HeuristicProtagonist.B100_HOOK`＝既定 None の計測専用フック経由。戻り値は
使われず例外も握り潰される＝AIの選択に一切影響しない＝`arena/b132_audit.py` と同型）：
  - 席ごとに、その席の防御プランナーが立てた `board_defeat` 脅威を全数記録する
    （`agent._b100_plan[0]`＝**本番と同じ脅威リスト**＝再計算しない）。
  - 各脅威について cur／gap／`暗躍+2` の残弾／rule_p（＝prob を progress で割り戻す）／
    現行 prob ／**是正後 prob**（1枚で届くなら 0.9）を出す。
  - θ=0.9（B-100 の絶対防御の資格）と 0.7（`PLAN_HOT_P`＝プランナー加点のホット閾値）を
    跨ぐ本数を数える。

CLI（前面実行）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b134_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b134_audit --days 5
    ... --start 0 --end 65 --out b3a.json      # チャンク分割
    ... --summarize b3a.json b3b.json          # 集計だけ
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace

import agents.heuristic_protagonist as HP
from agents import HeuristicMastermind, HeuristicProtagonist
from agents import defense_plan as DP
from sim import run_game
from arena.benchmark import benchmark_scripts

THETA = 0.9        # HeuristicProtagonist.B100_THETA（絶対防御の資格）
HOT_P = 0.7        # HeuristicProtagonist.PLAN_HOT_P（プランナー加点のホット閾値）
_SUFFIX = "のボード敗北"


def _p2_left(view: dict) -> bool:
    return "暗躍+2" not in ((view.get("used_cards") or {}).get("mastermind") or [])


def seat_facts(agent, view: dict, options: list, best: dict, score) -> list[dict]:
    """この席の `board_defeat` 脅威を全数（無ければ空）。純粋な観測。"""
    stash = getattr(agent, "_b100_plan", None)
    if not stash:
        return []
    threats = stash[0] or []
    banr = view.get("board_anyaku") or {}
    p2 = _p2_left(view)
    dpl = int(view.get("days_per_loop") or view.get("day", 1) or 1)
    days_left = max(0, dpl - int(view.get("day", 1)) + 1)
    out = []
    live = [t for t in threats
            if t.kind == "board_defeat" and not getattr(t, "breached", False)
            and t.label.endswith(_SUFFIX)]
    if not live:
        return []
    try:      # 席あたり1回だけ（belief の周辺化は高価＝脅威ごとに呼ばない）
        marg = agent._belief.role_marginals()
        rumor = DP._rumor_active(agent._belief)
        rleft = DP.rumor_left_for(agent._belief, view)
    except Exception:
        return []
    for t in live:
        area = t.label[: -len(_SUFFIX)]
        cur = int(banr.get(area, 0) or 0)
        try:
            gap = DP.unstoppable_supply_gap(
                view, area, cur, DP.BOARD_DEFEAT_ANYAKU,
                supply_rumor=rumor, roles=marg, rumor_left=rleft)
        except Exception:
            continue
        prog_now = 0.9 if gap <= 1 else 0.35
        rule_p = float(t.prob) / prog_now
        # ★是正後＝「1枚で届く」なら距離1と同じ扱い（暗躍+2 の残弾があり、出す日が残る時だけ）
        reach = 2 if (p2 and days_left >= 1) else 1
        prog_fix = 0.9 if gap <= reach else 0.35
        out.append({
            "loop": view.get("loop"), "day": view.get("day"),
            "seat": view.get("seat"), "area": area, "cur": cur, "gap": gap,
            "p2_left": p2, "days_left": days_left,
            "rule_p": round(rule_p, 6),
            "prob_now": round(float(t.prob), 6),
            "prob_fix": round(rule_p * prog_fix, 6),
            "defendable": bool(t.defendable),
            "card": best.get("card"), "target": best.get("target"),
            "target_kind": best.get("target_kind"),
        })
    return out


def run_audit(days: int, loops: int = 8, start: int = 0,
              end: int | None = None) -> list[dict]:
    rows = []
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    for name, seed, sc in scripts[start:end]:
        probe = replace(sc, loops=loops)
        seats: list[dict] = []
        n_seats = [0]

        def _hook(agent, view, options, best, score, _s=seats, _n=n_seats):
            _n[0] += 1
            _s.extend(seat_facts(agent, view, options, best, score))

        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        HP.B100_HOOK = _hook
        try:
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                        "p2": hp, "p3": hp})
        finally:
            HP.B100_HOOK = None
        fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not fb:
            outcome, ltw = "defense", state.loop_no
        elif fb:
            outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            ltw = loops + 1
        else:
            outcome, ltw = "loss", loops + 1
        rows.append({"script": name, "seed": seed, "days": days,
                     "outcome": outcome, "loops_to_win": ltw,
                     "n_seats": n_seats[0], "threats": seats})
        print(f"  {name} s{seed}: {outcome} ltw={ltw} 席{n_seats[0]} "
              f"board_defeat脅威{len(seats)}", flush=True)
    return rows


def summarize(rows: list[dict]) -> str:
    outcome_c: Counter = Counter()
    n_seats = tot = 0
    gap_c: Counter = Counter()
    far = far_p2 = 0                 # gap>=2（progress=0.35 に落ちている）
    far_hi = far_hi5 = 0             # うち rule_p>=0.9 / >=0.5
    cross_theta_now = cross_theta_fix = 0
    cross_hot_now = cross_hot_fix = 0
    seats_far: set = set()
    seats_far_hi: set = set()
    area_c: Counter = Counter()
    rulep_max = 0.0
    detail = []
    for g in rows:
        outcome_c[g["outcome"]] += 1
        n_seats += g.get("n_seats", 0)
        for s in g["threats"]:
            tot += 1
            gap_c[s["gap"]] += 1
            key = (g["script"], g["seed"], s["loop"], s["day"], s["seat"])
            if s["prob_now"] >= THETA - 1e-9:
                cross_theta_now += 1
            if s["prob_fix"] >= THETA - 1e-9:
                cross_theta_fix += 1
            if s["prob_now"] >= HOT_P - 1e-9:
                cross_hot_now += 1
            if s["prob_fix"] >= HOT_P - 1e-9:
                cross_hot_fix += 1
            if s["prob_fix"] > s["prob_now"] + 1e-9:
                far += 1
                seats_far.add(key)
                area_c[s["area"]] += 1
                rulep_max = max(rulep_max, s["rule_p"])
                if s["p2_left"]:
                    far_p2 += 1
                if s["rule_p"] >= 0.9:
                    far_hi += 1
                    seats_far_hi.add(key)
                    if len(detail) < 25:
                        detail.append(
                            f"    {g['script']} s{g['seed']} L{s['loop']}D{s['day']} "
                            f"{s['seat']} {s['area']}(cur={s['cur']} gap={s['gap']}) "
                            f"rule_p={s['rule_p']:.4f} prob {s['prob_now']:.4f}"
                            f"→{s['prob_fix']:.4f}")
                if s["rule_p"] >= 0.5:
                    far_hi5 += 1
    lines = [
        f"局数 {len(rows)}  outcome={dict(outcome_c)}",
        f"観測した席 {n_seats}／立った board_defeat 脅威 {tot}",
        "  gap の分布: " + "  ".join(f"gap={k}:{v}" for k, v in sorted(gap_c.items())),
        f"  ★是正で距離が縮む脅威（gap=2 かつ 暗躍+2 の残弾あり）: {far}"
        f"（うち p2_left={far_p2}）",
        f"     うち rule_p>=0.5: {far_hi5}   rule_p>=0.9: {far_hi}"
        f"   （rule_p の最大 {rulep_max:.4f}）",
        f"     板の内訳: " + "  ".join(f"{k}:{v}" for k, v in area_c.most_common()),
        f"     関係した席（重複排除）: {len(seats_far)}"
        f"（うち rule_p>=0.9 を含む席 {len(seats_far_hi)}）",
        f"  θ={THETA} を跨ぐ脅威: 現行 {cross_theta_now} → 是正後 {cross_theta_fix}",
        f"  PLAN_HOT_P={HOT_P} を跨ぐ脅威: 現行 {cross_hot_now} → 是正後 {cross_hot_fix}",
        "  明細（rule_p>=0.9・最大25件）:",
    ]
    lines.extend(detail)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ★Phase 3：絶対防御（B-100）の**発火数**と、その席が押し出した手の数え上げ
#   （§11b「成果の示し方」1＝行為の数え上げ＝並び順に依存しない主指標）
# ---------------------------------------------------------------------------
def run_fire(days: int, gate: bool, loops: int = 8, start: int = 0,
             end: int | None = None) -> list[dict]:
    """`_b100_log`（B-100 が実際に席を強制した記録）を全局ぶん集める。"""
    import agents.defense_plan as _dp
    _dp.B134_CARD_DISTANCE = gate
    print(f"★切替口 defense_plan.B134_CARD_DISTANCE = {_dp.B134_CARD_DISTANCE}"
          f"  B134_RULEP_CERTAIN = {_dp.B134_RULEP_CERTAIN}", flush=True)
    rows = []
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    for name, seed, sc in scripts[start:end]:
        probe = replace(sc, loops=loops)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                    "p2": hp, "p3": hp})
        fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not fb:
            outcome, ltw = "defense", state.loop_no
        elif fb:
            outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            ltw = loops + 1
        else:
            outcome, ltw = "loss", loops + 1
        rows.append({"script": name, "seed": seed, "days": days,
                     "outcome": outcome, "loops_to_win": ltw,
                     "fires": list(getattr(hp, "_b100_log", []) or [])})
    return rows


def summarize_fire(rows: list[dict]) -> str:
    kind_c: Counter = Counter()
    disp_c: Counter = Counter()
    n = 0
    for g in rows:
        for f in g["fires"]:
            n += 1
            kind_c[f.get("kind")] += 1
            d = f.get("displaced")
            disp_c[str(d).split("→")[0]] += 1
    return "\n".join([
        f"局数 {len(rows)}／★絶対防御（B-100）が席を強制した回数: {n}",
        "  負け筋の種別: " + "  ".join(f"{k}:{v}" for k, v in kind_c.most_common()),
        "  押し出された手の種別: " + "  ".join(
            f"{k}:{v}" for k, v in disp_c.most_common()),
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-134 板敗北の距離会計 シャドー集計")
    ap.add_argument("--fire", action="store_true",
                    help="B-100 の発火数と押し出された手を数える（--gate on/off）")
    ap.add_argument("--gate", type=str, default="on", choices=("on", "off"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--summarize", nargs="+", default=None)
    args = ap.parse_args(argv)
    if args.summarize:
        rows = []
        for path in args.summarize:
            with open(path, encoding="utf-8") as f:
                rows.extend(json.load(f))
        print(summarize(rows))
        return
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    if args.fire:
        rows = run_fire(args.days, args.gate == "on", loops=args.loops,
                        start=args.start, end=args.end)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
            print(f"→ {args.out}")
        print(summarize_fire(rows))
        return
    rows = run_audit(args.days, loops=args.loops, start=args.start, end=args.end)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"→ {args.out}")
    print(summarize(rows))


if __name__ == "__main__":
    main()
