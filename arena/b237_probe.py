# -*- coding: utf-8 -*-
"""B-237 計測プローブ（読み取り専用）：**冗長分離席**の数え上げと、単局の並べ替え掃引。

## 何を測るか（B-237 の主指標＝行為の数え上げ・列挙順非依存）

B-230（`B230_SPLIT_GEOMETRY`）は「供給実績ペアを分離しようとして、同一ターンに2席が
同種の移動札を両方へ置き、合成後も同室＝**自己相殺**」する席を検出して席を譲る。
本レーンが追加で数えるのは、その**すぐ隣の穴**：

- **冗長分離席**＝ある `set_card` 席で、供給実績ペア (T, P) が **現在同室**であり、
  相方 P に**このターンの自席予定移動**（`_planned_moves`）が既にあって、その合成後の
  位置が**共有部屋から出ている**（＝分離は**もう成立している**）にもかかわらず、
  その席が **T への移動札を選んだ**席の数。
  ＝**分離に寄与しない2枚目の移動札**（1枚目で分離は済んでいる）。

B-230 の既存の的（**自己相殺席**＝合成後も同室）と**排他**（合成後の位置が
「共有部屋の中」か「外」かで分かれる）＝両方を同時に数えて突き合わせられる。

## 材料

すべて公開情報：卓上の位置・生死、公開イベント履歴（脚本家能力フェイズの `不安+1` と
その時の `present`）、**自チームの予定表**（`_planned_moves`＝席間協調の合意なので
主人公チーム内では公開）。belief も神視点も使わない。

★測定は**実装（`_b230_selfcancel`）を呼ばない独立再実装**で行う
（的の測定が実装の写しにならないようにする＝B-232 プローブと同じ規律）。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b237_probe seats --days 5
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b237_probe seats --days 3 --on
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b237_probe perm \
        --game random_BTX#1 --days 5 --conds base,base+b237
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from dataclasses import replace

_MOVE_CARDS = ("移動←→", "移動↑↓")


# ---------------------------------------------------------------------------
# 独立再実装（実装の写しにしない）
# ---------------------------------------------------------------------------
def _supply_pairs(view: dict) -> dict:
    """過去ループの能力フェイズ `不安+1` の（受け手×同室 present）実績（無向）。"""
    lp = view.get("loop")
    out: dict[str, set] = {}
    for e in view.get("history", []) or []:
        if (e.get("event") == "unrest"
                and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0
                and e.get("loop") != lp):
            rcv = e.get("target")
            for p in (e.get("present") or ()):
                if p != rcv:
                    out.setdefault(rcv, set()).add(p)
                    out.setdefault(p, set()).add(rcv)
    return out


def classify(view: dict, planned: dict, card: str, tgt: str) -> str | None:
    """移動札 `card`→`tgt` を分類する（★実装を呼ばない独立再実装）。

    - `"selfcancel"`＝相方の合成後の位置がこの移動の行き先と一致（B-230 の的）。
    - `"redundant_all"`＝**同室の供給実績ペア全員**が予定移動で部屋を出る
      ＝この移動は分離に**一切**寄与しない（★B-237 の狭い版＝採用版の的）。
    - `"redundant_any"`＝出る相方と**残る**相方が混在（＝広い版だけが冗長と見なす。
      残る相方に対しては有効な分離＝**潰してはいけない**＝Phase 2-1 の実測で確定）。
    - `"useful"`＝同室の相方が全員残る＝この移動が分離を作る。
    - `None`＝供給実績ペアが絡まない席／空振り移動。
    """
    from engine.data import forbidden_of
    from agents.heuristic_protagonist import _move_dest
    if card not in _MOVE_CARDS:
        return None
    chars = {c.get("name"): c for c in (view.get("characters") or [])}
    tc = chars.get(tgt)
    if tc is None or not tc.get("alive", True) or tc.get("area") is None:
        return None
    src = tc["area"]
    dest = _move_dest(src, card)
    if dest is None or dest in forbidden_of(tgt):
        return None                      # 空振り移動＝別の語彙（noop_reason）の領分
    leave = stay = 0
    for other in sorted(_supply_pairs(view).get(tgt) or ()):
        if other == tgt:
            continue
        oc = chars.get(other)
        if oc is None or not oc.get("alive", True) or oc.get("area") != src:
            continue                     # 「現在同室のペア」だけ
        odest = planned.get(other)
        eff_other = (oc["area"] if (odest is None or odest in forbidden_of(other))
                     else odest)
        if eff_other == dest:
            return "selfcancel"          # 合成後も同室＝B-230 の的
        if eff_other == src:
            stay += 1
        else:
            leave += 1
    if leave == 0 and stay == 0:
        return None
    if stay == 0:
        return "redundant_all"
    if leave == 0:
        return "useful"
    return "redundant_any"


@contextmanager
def instrument(sink: list):
    """`decide` を包んで、選ばれた手の分類を記録する（読み取り専用・復元つき）。"""
    from agents import HeuristicProtagonist as HP
    orig = HP.decide

    def wrapped(self, view, decision, options):
        planned = dict(getattr(self, "_planned_moves", None) or {})
        out = orig(self, view, decision, options)
        if decision == "set_card" and out and out.get("target_kind") == "character":
            kind = classify(view, planned, out.get("card") or "", out.get("target"))
            if kind is not None:
                sink.append({"loop": view.get("loop"), "day": view.get("day"),
                             "seat": view.get("seat"), "kind": kind,
                             "card": out.get("card"), "target": out.get("target")})
        return out

    HP.decide = wrapped
    try:
        yield
    finally:
        HP.decide = orig


# ---------------------------------------------------------------------------
def _play(name: str, seed: int, days: int, loops: int = 8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import random_script, run_game
    from sim.sample_scripts import SAMPLE_SCRIPTS
    if name.startswith("random_"):
        sc = random_script(name.split("_", 1)[1], seed, days=days)
    else:
        sc = SAMPLE_SCRIPTS[name]()
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state


def cmd_seats(args) -> int:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    from agents import HeuristicProtagonist as HP
    from arena.b237_ab import cond_knobs, _apply, _restore
    old = _apply(cond_knobs(args.cond))
    old_cap = (HP.B237_CAP, HP.B237_ALL_PARTNERS)
    if getattr(args, "cap", None) is not None:
        HP.B237_CAP = args.cap
    if getattr(args, "all_partners", None) is not None:
        HP.B237_ALL_PARTNERS = bool(args.all_partners)
    install_perm(args.perm)
    tot = {"selfcancel": 0, "redundant_all": 0, "redundant_any": 0, "useful": 0}
    per_game: dict[str, dict] = {}
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            game = f"{name}#{seed}"
            if args.only and game != args.only:
                continue
            sink: list = []
            with instrument(sink):
                _play(name, seed, args.days, loops=args.loops)
            for rec in sink:
                tot[rec["kind"]] += 1
                per_game.setdefault(game, {}).setdefault(rec["kind"], 0)
                per_game[game][rec["kind"]] += 1
                if args.verbose and rec["kind"] != "useful":
                    print(f"  {game} L{rec['loop']}D{rec['day']} {rec['seat']} "
                          f"{rec['kind']}: {rec['card']}→{rec['target']}", flush=True)
    finally:
        uninstall_perm()
        HP.B237_CAP, HP.B237_ALL_PARTNERS = old_cap
        _restore(old)
    print(f"[{args.days}日級 perm={args.perm} cond={args.cond} "
          f"cap={getattr(args, 'cap', None)} "
          f"all_partners={getattr(args, 'all_partners', None)}] "
          f"★冗長分離席[狭]={tot['redundant_all']} [広]="
          f"{tot['redundant_all'] + tot['redundant_any']} "
          f"/ 自己相殺席={tot['selfcancel']} / 混在席={tot['redundant_any']} "
          f"/ 有効分離席={tot['useful']}")
    for g in sorted(per_game):
        d = per_game[g]
        if d.get("redundant_all") or d.get("selfcancel") or d.get("redundant_any"):
            print(f"    {g}: 冗長[狭]={d.get('redundant_all', 0)} "
                  f"混在={d.get('redundant_any', 0)} "
                  f"自己相殺={d.get('selfcancel', 0)} 有効={d.get('useful', 0)}")
    return 0


def _ltw(state, loops: int = 8):
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def cmd_perm(args) -> int:
    """★単局を多数の並べ替えで回す＝「回収」が機序か籤かの分解能を測る（§11b）。"""
    from arena.tie_noise import install_perm, uninstall_perm
    from arena.b237_ab import cond_knobs, _apply, _restore
    name, seed = args.game.rsplit("#", 1)
    seed = int(seed)
    perms = [p for p in args.perms.split(",") if p]
    conds = [c for c in args.conds.split(",") if c]
    print(f"# {args.game} days={args.days}")
    print("cond".ljust(18) + " ".join(p.rjust(6) for p in perms))
    marks = {"defense": "", "fb_win": "W", "fb_loss": "L", "loss": "X"}
    for cond in conds:
        row = []
        old = _apply(cond_knobs(cond))
        try:
            for perm in perms:
                install_perm(perm)
                try:
                    st = _play(name, seed, args.days, loops=args.loops)
                finally:
                    uninstall_perm()
                n, oc = _ltw(st, loops=args.loops)
                row.append(f"{n}{marks[oc]}")
        finally:
            _restore(old)
        print(cond.ljust(18) + " ".join(v.rjust(6) for v in row))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-237 プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seats", help="冗長分離席／自己相殺席の数え上げ")
    s.add_argument("--days", type=int, default=5)
    s.add_argument("--loops", type=int, default=8)
    s.add_argument("--perm", default="id")
    s.add_argument("--cond", default="base")
    s.add_argument("--only", default="")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--cap", type=float, default=None)
    s.add_argument("--all-partners", type=int, default=None)
    s.set_defaults(func=cmd_seats)
    p = sub.add_parser("perm", help="単局×多数の並べ替え（籤の分解能）")
    p.add_argument("--game", default="random_BTX#1")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--loops", type=int, default=8)
    p.add_argument("--conds", default="base,base+b237")
    p.add_argument("--perms",
                   default="id,rev,rot1,rot2,rot3,rot5,h1,h2,h3,h4,h5,h6")
    p.set_defaults(func=cmd_perm)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
