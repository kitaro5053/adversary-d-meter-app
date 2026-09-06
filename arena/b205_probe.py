# -*- coding: utf-8 -*-
"""B-205 Phase 0：**暗躍禁止の自滅（1ターン2枚）**の再現と規模の確定（読み取り専用）。

一次ソース＝`rules/10_action_cards.md:25,61`＝「複数の主人公が暗躍禁止を出している場合、
暗躍禁止自体が無効化される」。実装＝`engine/resolver.py:331`（自滅）・`:345`（複製前の
実カード枚数で計数）・`engine/render.py:438`（表示）。

サブコマンド:
    python -m arena.b205_probe scan  --days {3,5}
        # 両ベンチ全局を回し、「同ターンに主人公が暗躍禁止を2枚以上出した」ターンを
        #   全数列挙する。各枚の `prov`（b100＝強制割当／b100_match／無印＝通常採点）と
        #   その日の mm の暗躍+ 配置（神視点）を出す。
    python -m arena.b205_probe why --script random_FS --seed 6 [--days 3]
        # 当該局を再生し、自滅ターンについて
        #   (a) どの席が先に暗躍禁止を置いたか（席順・prov）
        #   (b) 2枚目の席で B-100 割当がどの段を通ったか（`b100_alloc.TRACE`）
        #   (c) 結果として engine が両方を無効化したか（adjudication の公開イベント）
        #   を席ごとの内部状態（`_kinshi_used`・`_b100_placed`）とともに出す。

★agents/ の判断経路・既定値には触れない（読むだけ）。
測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace


def _kinshi(pls) -> list[dict]:
    return [p for p in pls if p.get("owner") != "mastermind"
            and p.get("card") == "暗躍禁止"]


def _prov_of(p: dict) -> str:
    return str(p.get("prov") or "通常")


def _classify(kin: list[dict]) -> str:
    """自滅ターンの由来。b100 が1枚でも噛んでいれば b100 由来とする。"""
    provs = {_prov_of(p) for p in kin}
    if "b100" in provs:
        return "b100由来"
    return "通常採点由来"


def _run(name: str, seed: int, sc, loops: int = 8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _log = run_game(replace(sc, loops=loops),
                           {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state


def cmd_scan(args) -> int:
    from arena.benchmark import benchmark_scripts
    total_turns = 0
    hits: list[dict] = []
    for name, seed, sc in benchmark_scripts(days=args.days):
        state = _run(name, seed, sc)
        for e in state.history:
            if e.get("event") != "cards_revealed":
                continue
            loop, day = e.get("loop"), e.get("day")   # pub が付ける loop/day タグ
            total_turns += 1
            pls = e.get("placements") or []
            kin = _kinshi(pls)
            if len(kin) < 2:
                continue
            hits.append({
                "局": f"{name}#{seed}",
                "loop": loop, "day": day,
                "由来": _classify(kin),
                "暗躍禁止": [f'{p.get("owner")}:{p.get("target")}'
                             f'[{_prov_of(p)}]' for p in kin],
                "mm暗躍+": [f'{p.get("card")}→{p.get("target")}' for p in pls
                            if p.get("owner") == "mastermind"
                            and str(p.get("card", "")).startswith("暗躍+")],
            })
    print(f"[days={args.days}] 総ターン数={total_turns}  自滅ターン={len(hits)}")
    by = {}
    for h in hits:
        by[h["由来"]] = by.get(h["由来"], 0) + 1
    print("  由来別: " + (json.dumps(by, ensure_ascii=False) if by else "なし"))
    for h in hits:
        print("  " + json.dumps(h, ensure_ascii=False))
    return 0


def cmd_why(args) -> int:
    """当該局を再生し、自滅ターンの席ごとの内部状態と B-100 の段を出す。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import b100_alloc
    from arena.benchmark import benchmark_scripts
    from sim import run_game

    target = None
    for name, seed, sc in benchmark_scripts(days=args.days):
        if name == args.script and seed == args.seed:
            target = sc
            break
    if target is None:
        print(f"該当の局が無い: {args.script}#{args.seed} days={args.days}")
        return 1

    trace: list[dict] = []
    seat_log: list[dict] = []

    hp = HeuristicProtagonist(args.seed)
    mm = HeuristicMastermind(args.seed)
    orig_decide = hp.decide

    def _traced(view, decision, options):
        b100_alloc.TRACE = lambda d: trace.append(
            {"loop": view.get("loop"), "day": view.get("day"),
             "seat": view.get("seat"), **d})
        pre_kinshi = getattr(hp, "_kinshi_used", None)
        pre_placed = sorted(str(k) for k in getattr(hp, "_b100_placed", set()))
        try:
            best = orig_decide(view, decision, options)
        finally:
            b100_alloc.TRACE = None
        if decision == "set_card":
            seat_log.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"),
                "_kinshi_used(前)": pre_kinshi,
                "_b100_placed(前)": pre_placed,
                "選択": f'{best.get("card")}→{best.get("target")}',
                "prov": best.get("prov"),
                "暗躍禁止候補あり": any(o["card"] == "暗躍禁止" for o in options),
            })
        return best

    hp.decide = _traced
    state, _log = run_game(replace(target, loops=8),
                           {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    bad: list[tuple] = []
    for e in state.history:
        if e.get("event") == "cards_revealed":
            kin = _kinshi(e.get("placements") or [])
            if len(kin) >= 2:
                bad.append((e.get("loop"), e.get("day"), e.get("placements")))
    print(f"=== {args.script}#{args.seed} days={args.days}：自滅ターン {len(bad)} 件")
    for loop, day, pls in bad:
        print(f"\n--- L{loop}D{day} 全6枚（席順）")
        for p in pls:
            print("   " + json.dumps(p, ensure_ascii=False))
        print("  [席ごとの内部状態]")
        for s in seat_log:
            if s["loop"] == loop and s["day"] == day:
                print("   " + json.dumps(s, ensure_ascii=False))
        print("  [B-100 割当の段（TRACE）]")
        for t in trace:
            if t["loop"] == loop and t["day"] == day:
                print("   " + json.dumps(
                    {k: v for k, v in t.items() if k != "cons"},
                    ensure_ascii=False))
                for c in t.get("cons") or []:
                    print("      cons " + json.dumps(c, ensure_ascii=False))
        print("  [engine の解決（当該ターンの公開イベント全部）]")
        for e in state.history:
            if e.get("loop") == loop and e.get("day") == day:
                print("   " + json.dumps(e, ensure_ascii=False)[:400])
        break
    return 0


def cmd_cmp(args) -> int:
    """★flip の検死＝同じ局を ON／OFF で回し、**分岐点**とループ結末の差を出す。"""
    from agents import heuristic_protagonist as hpm
    from arena.benchmark import benchmark_scripts

    target = None
    for name, seed, sc in benchmark_scripts(days=args.days):
        if name == args.script and seed == args.seed:
            target = sc
            break
    if target is None:
        print(f"該当の局が無い: {args.script}#{args.seed} days={args.days}")
        return 1

    runs = {}
    for label, guard in (("OFF(是正前)", False), ("ON(是正後)", True)):
        hpm.HeuristicProtagonist.B205_KINSHI_GUARD = guard
        st = _run(args.script, args.seed, target)
        runs[label] = st
    hpm.HeuristicProtagonist.B205_KINSHI_GUARD = True

    for label, st in runs.items():
        ends = [(e.get("loop"), e.get("event"))
                for e in st.history
                if e.get("event") in ("loop_end", "game_end", "loop_result")]
        print(f"[{label}] winner={getattr(st, 'winner', None)} "
              f"loop={getattr(st, 'loop_no', None)} 終端イベント={ends[-3:]}")

    a = [e for e in runs["OFF(是正前)"].history if e.get("event") == "cards_revealed"]
    b = [e for e in runs["ON(是正後)"].history if e.get("event") == "cards_revealed"]
    for i, (x, y) in enumerate(zip(a, b)):
        if x.get("placements") != y.get("placements"):
            print(f"\n★最初に分岐したターン＝L{x.get('loop')}D{x.get('day')}")
            print("  OFF:")
            for p in x["placements"]:
                print("    " + json.dumps(p, ensure_ascii=False))
            print("  ON :")
            for p in y["placements"]:
                print("    " + json.dumps(p, ensure_ascii=False))
            break
    else:
        print("\n分岐なし（両者の配置は完全一致）")

    for label, st in runs.items():
        print(f"\n[{label}] ループ別の結末")
        for e in st.history:
            ev = str(e.get("event") or "")
            if ev in ("death", "loop_lost", "loop_won", "defeat", "game_over",
                      "incident", "loop_end"):
                print("   " + json.dumps(e, ensure_ascii=False)[:200])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-205 Phase 0：暗躍禁止の自滅プローブ")
    ap.add_argument("cmd", choices=["scan", "why", "cmp"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--script", default="random_FS")
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--off", action="store_true",
                    help="B-205 のガードを OFF（＝是正前の挙動に bit 復帰）して測る")
    a = ap.parse_args(argv)
    if a.off:
        from agents import heuristic_protagonist as hpm
        hpm.HeuristicProtagonist.B205_KINSHI_GUARD = False
        print("[B205_KINSHI_GUARD=False＝是正前の挙動]")
    return {"scan": cmd_scan, "why": cmd_why, "cmp": cmd_cmp}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
