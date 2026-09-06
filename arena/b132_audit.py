# -*- coding: utf-8 -*-
"""B-132：主人公の板ガードが「mm の暗躍+2 の残弾」を数えずに脅威を見積もっている席の
シャドー集計（挙動不変・計測のみ＝`arena/plus2_audit.py` と同じ後処理型）。

問い（トリアージ 2026-08-01・手練れ指摘）＝「L4D3 神社への暗躍禁止。**すでに暗躍+2は
出ている**ので最終日にここで暗躍を止める必要はない」。

計測（`HeuristicProtagonist.B100_HOOK`＝既定 None の計測専用フック経由。フックの戻り値は
使われず例外も握り潰される＝AIの選択に一切影響しない）：
  1. `暗躍禁止 → 板` を打った席の総数。
  2. うち **暗躍+2 が今ループ既に公開消費済み**（`view["used_cards"]["mastermind"]`）の席数。
  3. うち **札だけでは臨界2に届かない**席数＝
     `gap > card_supply_max`（gap＝`defense_plan.unstoppable_supply_gap`／
      card_supply_max＝`残り日数 + (2 if 暗躍+2 未消費)`＝`agents/heuristic.py:1399` の
      mm 側 `_card_supply` と同じ式）。
     さらに「+2 が残っていれば届いた」＝**残弾会計が決定的**な席を分けて数える。
  4. 押し出される（次点の）手＝その席が何に使われうるか。

CLI（前面実行）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b132_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b132_audit --days 5
    ... --start 0 --end 65 --out b3a.json   # チャンク分割
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


def yield_facts(agent, view: dict, best: dict) -> dict | None:
    """★行為の数え上げ用（B-132 land 後）：この席が「見切った板」に mm の札があるターンで
    何を打ったか。ターン単位の集計は `summarize_yield` が行う（席は3つあるため）。"""
    dead = getattr(agent, "_b132_dead_boards_now", frozenset()) or frozenset()
    if not dead:
        return None
    mmb = {p["target"] for p in view.get("placements", []) or []
           if p.get("owner") == "mastermind" and p.get("target_kind") == "board"}
    hit = sorted(dead & mmb)
    if not hit:
        return None
    return {"loop": view.get("loop"), "day": view.get("day"),
            "seat": view.get("seat"), "dead_with_card": hit,
            "card": best.get("card"), "target": best.get("target"),
            "target_kind": best.get("target_kind")}


def summarize_yield(rows: list[dict]) -> str:
    """「残弾0で板ガードを見送ったターン」の数え上げ（並び順に依存しない主指標）。"""
    turns = 0
    yielded = 0
    used_for: Counter = Counter()
    for g in rows:
        by_turn: dict = {}
        for y in g.get("yields", []):
            by_turn.setdefault((y["loop"], y["day"], tuple(y["dead_with_card"])),
                               []).append(y)
        for (_lp, _d, boards), seats in by_turn.items():
            turns += 1
            guarded = any(s["card"] == "暗躍禁止" and s["target"] in boards
                          for s in seats)
            if not guarded:
                yielded += 1
                for s in seats:
                    used_for[f'{s["card"]}→{s.get("target_kind")}'] += 1
    lines = [f"見切った板に mm 札があったターン: {turns}",
             f"  うち **板ガードを見送った**ターン: {yielded}",
             "  その席が使われた先: " + "  ".join(
                 f"{k}:{v}" for k, v in used_for.most_common())]
    return "\n".join(lines)


def seat_facts(agent, view: dict, options: list, best: dict, score) -> dict | None:
    """1席分の事実（板への暗躍禁止のときだけ dict・それ以外 None）。純粋な観測。"""
    if best.get("card") != "暗躍禁止" or best.get("target_kind") != "board":
        return None
    # ★フックは `_kinshi_used=True` を立てた**後**に呼ばれる＝そのまま再採点すると
    #   自滅回避(-100) が返る。採点時点（この席の選択前）の状態に戻して読む。
    _ku = agent._kinshi_used
    agent._kinshi_used = False
    try:
        return _seat_facts_inner(agent, view, options, best, score)
    finally:
        agent._kinshi_used = _ku


def _seat_facts_inner(agent, view, options, best, score) -> dict | None:
    area = best["target"]
    cur = int((view.get("board_anyaku") or {}).get(area, 0) or 0)
    days_left = int(view.get("days_per_loop", view.get("day", 1))) - int(view["day"]) + 1
    p2_left = "暗躍+2" not in ((view.get("used_cards") or {}).get("mastermind") or [])
    b = agent._belief
    try:
        marg = b.role_marginals()
        rumor = DP._rumor_active(b)
        rleft = DP.rumor_left_for(b, view)
        gap = DP.unstoppable_supply_gap(view, area, cur, DP.BOARD_DEFEAT_ANYAKU,
                                        supply_rumor=rumor, roles=marg,
                                        rumor_left=rleft)
        kuro_here = DP._kuromaku_supply_here(view, area, marg)
    except Exception:
        return None
    card_max = days_left + (2 if p2_left else 0)
    card_max_if_p2 = days_left + 2
    # 次点（この席が板ガードを譲ったら何に回るか）
    alt = sorted(((float(score(o)), o) for o in options
                  if (o["card"], o["target"], o.get("target_kind"))
                  != (best["card"], best["target"], best.get("target_kind"))),
                 key=lambda t: -t[0])
    alt_s, alt_o = (alt[0] if alt else (0.0, {}))
    return {
        "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
        "area": area, "cur": cur, "days_left": days_left, "p2_left": p2_left,
        "gap": gap, "kuro_here": kuro_here,
        "card_max": card_max, "card_max_if_p2": card_max_if_p2,
        "unreachable": gap > card_max,
        "ammo_decisive": (gap > card_max) and (gap <= card_max_if_p2),
        "unreachable_even_with_p2": gap > card_max_if_p2,
        "score": round(float(score(best)), 2),
        "alt": f"{alt_o.get('card')}→{alt_o.get('target')}" if alt_o else None,
        "alt_score": round(alt_s, 2),
    }


def run_audit(days: int, loops: int = 8, start: int = 0,
              end: int | None = None) -> list[dict]:
    rows = []
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    for name, seed, sc in scripts[start:end]:
        probe = replace(sc, loops=loops)
        seats: list[dict] = []
        yields: list[dict] = []

        def _hook(agent, view, options, best, score, _s=seats, _y=yields):
            f = seat_facts(agent, view, options, best, score)
            if f is not None:
                _s.append(f)
            g = yield_facts(agent, view, best)
            if g is not None:
                _y.append(g)

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
                     "outcome": outcome, "loops_to_win": ltw, "seats": seats,
                     "yields": yields})
        print(f"  {name} s{seed}: {outcome} ltw={ltw} 板ガード席{len(seats)}",
              flush=True)
    return rows


def summarize(rows: list[dict]) -> str:
    tot = p2_spent = unreachable = decisive = even_p2 = 0
    dec_rows = []
    alt_kinds: Counter = Counter()
    outcome_c: Counter = Counter()
    for g in rows:
        outcome_c[g["outcome"]] += 1
        for s in g["seats"]:
            tot += 1
            if not s["p2_left"]:
                p2_spent += 1
            if s["unreachable"]:
                unreachable += 1
            if s["unreachable_even_with_p2"]:
                even_p2 += 1
            if s["ammo_decisive"]:
                decisive += 1
                alt_kinds[str(s["alt"]).split("→")[0]] += 1
                dec_rows.append((g["script"], g["seed"], s))
    lines = [
        f"局数 {len(rows)}  outcome={dict(outcome_c)}",
        f"板への暗躍禁止 席数 合計 {tot}",
        f"  うち 暗躍+2 を今ループ消費済み            : {p2_spent}",
        f"  うち 札だけでは臨界2に届かない(gap>card_max): {unreachable}",
        f"    うち **残弾会計が決定的**(+2が残れば届いた): {decisive}",
        f"    うち +2 が残っていても届かない            : {even_p2}",
        "  決定的席の次点カード種: " + "  ".join(
            f"{k}:{v}" for k, v in alt_kinds.most_common()),
    ]
    lines.append(summarize_yield(rows))
    lines.append("  決定的席の明細（最大30件）:")
    for name, seed, s in dec_rows[:30]:
        lines.append(
            f"    {name} s{seed} L{s['loop']}D{s['day']} {s['seat']} "
            f"{s['area']}(cur={s['cur']} 残{s['days_left']}日 gap={s['gap']} "
            f"kuro={s['kuro_here']}) score={s['score']} 次点={s['alt']}({s['alt_score']})")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-132 板ガードの残弾会計 シャドー集計")
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
    rows = run_audit(args.days, loops=args.loops, start=args.start, end=args.end)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"→ {args.out}")
    print(summarize(rows))


if __name__ == "__main__":
    main()
