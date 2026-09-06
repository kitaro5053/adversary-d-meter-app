# -*- coding: utf-8 -*-
"""B-208 Phase 0：`random_FS#16`（5日級ベンチの loss 局）で
**能力供給役（ミスリーダー＝情報屋）を動かす／釘付けにする**手が選ばれない理由を
採点表で確定する読み取り専用プローブ。

★agents/ の判断経路・既定値には一切触れない（読むだけ）。

サブコマンド:
    python -m arena.b208_probe supply [--set FS --seed 16 --days 5 --loops 8]
        # 能力供給（脚本家能力フェイズの不安+1）が通った席を全数列挙し、
        #   同じターンに主人公が打てた「供給役への移動／移動禁止」の採点を並べる
    python -m arena.b208_probe window [...]
        # 述語の連言が「時間的に排他になっていないか」の検算（日ごとに条件の真偽表）
    python -m arena.b208_probe count --days 3|5
        # ベンチ全局で B-208 述語が何席で True になるか（行為の数え上げ）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json

from agents.debug import ProbedProtagonist, ProbedMastermind
from sim import random_script, run_game

_MOVE = ("移動↑↓", "移動←→")
_PIN = ("移動禁止",)


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _run(set_name: str, seed: int, days: int, loops: int):
    from dataclasses import replace
    sc = replace(random_script(set_name, seed, days=days), loops=loops)
    hp = ProbedProtagonist(seed, top=400)
    mm = ProbedMastermind(seed, top=40)
    state, _ = run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return sc, state, hp, mm


def cmd_supply(args) -> int:
    from agents.heuristic_protagonist import _move_dest
    sc, state, hp, mm = _run(args.set, args.seed, args.days, args.loops)
    print(f"===== B-208 Phase 0  random_{args.set}#{args.seed} "
          f"days={args.days} loops={args.loops} =====")
    print(f"配役(神視点・診断表示のみ)={sc.roles}")
    print(f"winner={state.winner} loop_no={state.loop_no}")
    # 1) 能力フェイズの不安+1（＝供給）を全数
    feeds = [e for e in state.history
             if e.get("event") == "unrest" and e.get("phase") == "mastermind_ability"
             and int(e.get("delta", 0) or 0) > 0]
    print(f"\n--- 能力供給（脚本家能力フェイズの不安+1）＝{len(feeds)} 件")
    for e in feeds:
        print(f"  L{e['loop']}D{e['day']} → {e['target']} present={e.get('present')}")
    # 2) その日の主人公3席で「供給役を動かす／止める」候補がいくらだったか
    watch = set(args.watch)
    days_with_feed = {(e["loop"], e["day"]) for e in feeds}
    print(f"\n--- 供給が通った (loop, day) の主人公席の採点 "
          f"（watch={sorted(watch)}）")
    for rec in hp.records:
        if rec["decision"] != "set_card":
            continue
        key = (rec["loop"], rec["day"])
        if args.all_days is False and key not in days_with_feed:
            continue
        print(f"  L{rec['loop']}D{rec['day']} {rec['seat']}: 選択={_fmt(rec['chosen'])}")
        top = rec["scored"][:3]
        for s, o in top:
            print(f"        TOP  {s:8.2f} {_fmt(o)}")
        for s, o in rec["scored"]:
            if o.get("target_kind") != "character" or o.get("target") not in watch:
                continue
            if o.get("card") not in _MOVE + _PIN:
                continue
            print(f"        ★    {s:8.2f} {_fmt(o)}")
    # 3) 行為の数え上げ
    print("\n--- 行為の数え上げ（主人公の実際の設置）")
    cnt: dict = {}
    for rec in hp.records:
        if rec["decision"] != "set_card":
            continue
        o = rec["chosen"]
        k = _fmt(o)
        cnt[k] = cnt.get(k, 0) + 1
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"  {v:3d}  {k}")
    return 0


def cmd_window(args) -> int:
    """述語の連言が「時間的に排他になっていないか」の検算（§69-3 の教訓）。

    B-208 の連言＝①過去ループの能力供給の観測 ②受け手が移動不能 ③受け手が残る致死日の
    犯人候補で算術上臨界へ届きうる ④供給役が一意 ⑤（手の選択時）供給圏内 or mm の伏せ札。
    ①〜④が同時に立つ日が 0 なら設計をやり直す＝ここでその日を全数印字する。
    """
    from agents.heuristic_protagonist import HeuristicProtagonist
    from agents import HeuristicMastermind
    from dataclasses import replace

    rows: list = []

    class _Win(HeuristicProtagonist):
        def decide(self, view, decision, options):
            out = super().decide(view, decision, options)
            if decision == "set_card" and view.get("seat") == "p1":
                pairs = self._b208_pairs(view)
                mm_on = sorted({p.get("target") for p in view.get("placements", [])
                                if p.get("owner") == "mastermind"
                                and p.get("target_kind") == "character"})
                rows.append((view.get("loop"), view.get("day"), pairs, mm_on,
                             {c["name"]: (c["area"], c["unrest"])
                              for c in view["characters"] if c.get("alive")}))
            return out

    sc = replace(random_script(args.set, args.seed, days=args.days),
                 loops=args.loops)
    hp = _Win(args.seed)
    run_game(sc, {"mastermind": HeuristicMastermind(args.seed),
                  "p1": hp, "p2": hp, "p3": hp})
    print(f"===== B-208 window 検算  random_{args.set}#{args.seed} =====")
    n_live = 0
    for loop, day, pairs, mm_on, board in rows:
        live = "○" if pairs else "×"
        n_live += bool(pairs)
        print(f"  L{loop}D{day} 連言={live} pairs={pairs} mm札={mm_on}")
    print(f"[window] ①〜④が同時に立った (loop,day) = {n_live}/{len(rows)}")
    return 0


class _ShiftMastermind:
    """摂動①＝**供給役への移動札を1日ずらす**脚本家（§69-3 の提案③）。

    各ループで最初に「供給役へ移動カード」を置こうとした日だけ、その手を**捨てて**
    次善手（供給役への移動札でない候補のうち素点最大）に差し替える＝1日遅れる。
    """

    def __init__(self, inner, supplier: str):
        self._inner = inner
        self._sup = supplier
        self._skipped: set = set()
        self.records: list = []

    def __getattr__(self, k):
        return getattr(self._inner, k)

    def decide(self, view, decision, options):
        chosen = self._inner.decide(view, decision, options)
        if decision not in ("set_card", "set_card_mm"):
            return chosen
        loop = view.get("loop")
        if (chosen.get("target") == self._sup
                and (chosen.get("card") or "").startswith("移動")
                and loop not in self._skipped):
            self._skipped.add(loop)
            alt = [o for o in options
                   if not (o.get("target") == self._sup
                           and (o.get("card") or "").startswith("移動"))]
            if alt:
                self.records.append((loop, view.get("day"), chosen, alt[0]))
                return self._inner.decide(view, decision, alt)
        return chosen


def cmd_perturb(args) -> int:
    """摂動耐性＝(a) mm の移動札を1日ずらす (b) options の席順入替。"""
    from dataclasses import replace
    from agents import HeuristicMastermind
    from agents.heuristic_protagonist import HeuristicProtagonist
    from arena.tie_noise import install_perm, uninstall_perm

    def _one(perm: str, shift: bool, on: bool) -> tuple:
        HeuristicProtagonist.B208_SUPPLIER_LOCK = on
        install_perm(perm)
        try:
            sc = replace(random_script(args.set, args.seed, days=args.days),
                         loops=args.loops)
            mm = HeuristicMastermind(args.seed)
            if shift:
                mm = _ShiftMastermind(mm, args.supplier)
            hp = HeuristicProtagonist(args.seed)
            st, _ = run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        finally:
            uninstall_perm()
            HeuristicProtagonist.B208_SUPPLIER_LOCK = True
        feeds = sum(1 for e in st.history
                    if e.get("event") == "unrest"
                    and e.get("phase") == "mastermind_ability"
                    and int(e.get("delta", 0) or 0) > 0)
        fb = any(e.get("event") == "final_battle" for e in st.history)
        outcome = ("defense" if (st.winner == "protagonist" and not fb)
                   else ("fb_win" if fb and st.winner == "protagonist"
                         else ("fb_loss" if fb else "loss")))
        loops = st.loop_no if outcome == "defense" else args.loops + 1
        return loops, outcome, feeds

    print(f"===== B-208 摂動耐性  random_{args.set}#{args.seed} "
          f"（供給役={args.supplier}） =====")
    print(f"{'摂動':22s} {'OFF':>22s}   {'ON':>22s}")
    cases = [("なし（基準）", "id", False)]
    cases += [(f"席順入替 perm={m}", m, False)
              for m in args.perms.split(",") if m.strip()]
    cases += [("mm移動札を1日ずらす", "id", True)]
    cases += [(f"1日ずらす＋perm={m}", m, True)
              for m in args.perms.split(",")[:2] if m.strip()]
    for label, perm, shift in cases:
        a = _one(perm, shift, False)
        b = _one(perm, shift, True)
        print(f"{label:22s} {a[0]:2d}[{a[1]:8s}] 供給{a[2]:3d}   "
              f"{b[0]:2d}[{b[1]:8s}] 供給{b[2]:3d}")
    return 0


def cmd_count(args) -> int:
    """ベンチ全局で B-208 述語が何席で True になるか（行為の数え上げ）。"""
    from arena.benchmark import benchmark_scripts
    from dataclasses import replace
    from agents import HeuristicMastermind
    from agents.heuristic_protagonist import HeuristicProtagonist

    class _Counting(HeuristicProtagonist):
        fires: list = []

        def decide(self, view, decision, options):
            out = super().decide(view, decision, options)
            if decision == "set_card":
                p = self._b208_pairs(view) if hasattr(self, "_b208_pairs") else ()
                if p:
                    _Counting.fires.append(
                        (view.get("loop"), view.get("day"), view.get("seat"), p,
                         f'{out.get("card")}→{out.get("target")}'))
            return out

    tot = 0
    for name, seed, sc in benchmark_scripts(days=args.days):
        _Counting.fires = []
        probe = replace(sc, loops=8)
        mm = HeuristicMastermind(seed)
        hp = _Counting(seed)
        run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        if _Counting.fires:
            print(f"{name}#{seed}: {len(_Counting.fires)} 席")
            for f in _Counting.fires:
                print(f"    L{f[0]}D{f[1]} {f[2]} pairs={f[3]} 選択={f[4]}")
            tot += len(_Counting.fires)
    print(f"[count] days={args.days} 合計 {tot} 席")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-208 プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm, fn in (("supply", cmd_supply), ("window", cmd_window)):
        p = sub.add_parser(nm)
        p.add_argument("--set", default="FS")
        p.add_argument("--seed", type=int, default=16)
        p.add_argument("--days", type=int, default=5)
        p.add_argument("--loops", type=int, default=8)
        p.add_argument("--watch", nargs="*", default=["情報屋", "入院患者"])
        p.add_argument("--all-days", action="store_true", default=False)
        p.set_defaults(func=fn)
    p = sub.add_parser("count")
    p.add_argument("--days", type=int, default=5)
    p.set_defaults(func=cmd_count)
    p = sub.add_parser("perturb")
    p.add_argument("--set", default="FS")
    p.add_argument("--seed", type=int, default=16)
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--loops", type=int, default=8)
    p.add_argument("--supplier", default="情報屋")
    p.add_argument("--perms", default="rev,rot1,h1,h2")
    p.set_defaults(func=cmd_perturb)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
