# -*- coding: utf-8 -*-
"""B-287 4日級 flip の機序検死（計測のみ・実装は1バイトも触らない）。

同じ (脚本, seed, days, perm) を `cur5`（現行既定＝4日級では日数ゲートが閉＝不発）と
`on4`（`B287_MIN_DAYS=4` へ広げた対照）で走らせ、

  1. 脚本家の伏せ札列を**全ループ分**並べて最初の食い違い（分岐点）を出す。
  2. 分岐点の席が「前ループで折られた線」だったか（＝B-287 が降りた／温存した席か）を
     `b286_line_signals`（`loop_memory=False` 明示＝検出定義の固定）で判定する。
  3. 3日級で観測された退行の型＝**「降りたが乗り換え先が1ループで完成しない」**かを見るため、
     ループごとの「事件の成立／不成立」と `mm_lint` D2（期限切れ犯人への不安）を並べる。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b287_d4_flip \
        --script random_BTX --seed 8 --days 4 --perm id
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from arena.b287_probe import _MODES, _MODE_DEFAULTS, _MODE_FLAGS

_ANYAKU = {"暗躍+1", "暗躍+2"}


def run_one(name: str, seed: int, script, days: int, mode: str, perm: str,
            loops: int = 8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.heuristic import HeuristicMastermind as HM
    from arena.tie_noise import install_perm, uninstall_perm
    from sim import run_game

    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f in _MODE_FLAGS:
            setattr(HM, f, _MODES[mode].get(f, _MODE_DEFAULTS.get(f, False)))
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, log = run_game(replace(script, loops=loops),
                              {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    return state, log


def _seq(log):
    """脚本家の伏せ札列＝(loop, day, card, kind, target) の並び。"""
    return [(r["loop"], r["day"], r["chosen"].get("card"),
             r["chosen"].get("target_kind"), r["chosen"].get("target"))
            for r in log
            if r["actor"] == "mastermind" and r["decision"] == "set_card"]


def _views(log):
    return [r for r in log
            if r["actor"] == "mastermind" and r["decision"] == "set_card"]


def _outcome(state, log, loops=8):
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return "defense", state.loop_no
    if fb:
        return ("fb_win" if state.winner == "protagonist" else "fb_loss"), loops + 1
    return "loss", loops + 1


def _incidents(state):
    """(loop, day) → 事件が起きたか（発生/不発）。"""
    out = {}
    for ev in state.history:
        if ev.get("event") == "incident":
            out[(ev.get("loop"), ev.get("day"))] = (
                out.get((ev.get("loop"), ev.get("day")), False)
                or bool(ev.get("occurs")))
    return out


def _lint_by_loop(log):
    from arena.mm_lint import lint_move
    from collections import Counter
    out: dict = {}
    for r in log:
        if r["actor"] != "mastermind" or r["decision"] not in (
                "set_card", "mastermind_ability"):
            continue
        for det, reason in lint_move(r["view"], r["chosen"]):
            out.setdefault(r["loop"], Counter())[det] += 1
    return out


def _broken_at(view, loop):
    from agents.heuristic import b286_line_signals
    return set(b286_line_signals({**view, "loop": loop},
                                 loop_memory=False)["broken_seats"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--base", type=str, default="cur5")
    ap.add_argument("--cand", type=str, default="on4")
    ap.add_argument("--loops", type=int, default=8)
    args = ap.parse_args(argv)

    from arena.benchmark import benchmark_scripts
    pool = {(n, s): sc for n, s, sc in benchmark_scripts(days=args.days)}
    sc = pool[(args.script, args.seed)]

    res = {}
    for mode in (args.base, args.cand):
        st, lg = run_one(args.script, args.seed, sc, args.days, mode,
                         args.perm, args.loops)
        res[mode] = (st, lg)

    print(f"# flip 検死 {args.script}#{args.seed} "
          f"（{args.days}日級・perm={args.perm}）")
    for mode in (args.base, args.cand):
        st, lg = res[mode]
        oc, ltw = _outcome(st, lg, args.loops)
        print(f"  {mode}: ltw={ltw} outcome={oc}")

    sa, sb = _seq(res[args.base][1]), _seq(res[args.cand][1])
    va, vb = _views(res[args.base][1]), _views(res[args.cand][1])
    n = min(len(sa), len(sb))
    idx = next((i for i in range(n) if sa[i] != sb[i]), None)
    print()
    if idx is None:
        print("  ★伏せ札列に食い違いなし（差は脚本家能力フェイズ側）")
    else:
        lp, day = sa[idx][0], sa[idx][1]
        print(f"## 分岐点＝第{idx}席（ループ{lp}・{day}日目）")
        print(f"  {args.base}: {sa[idx][2]} → {sa[idx][4]}({sa[idx][3]})")
        print(f"  {args.cand}: {sb[idx][2]} → {sb[idx][4]}({sb[idx][3]})")
        if lp >= 2:
            broken = set()
            for k in range(1, lp):
                broken |= _broken_at(va[idx]["view"], k)
            for tag, s in ((args.base, sa[idx]), (args.cand, sb[idx])):
                seat = (s[3], s[4])
                print(f"  {tag} の席は「以前のループで折られた線」か: "
                      f"{seat in broken}"
                      + ("（＝B-287 の対象席）" if seat in broken else "（＝別の線）"))
            print(f"  折られた線（累積）= {sorted(broken)}")

    print()
    print("## 脚本家の伏せ札列（ループ別・食い違い箇所に *）")
    for mode, seq in ((args.base, sa), (args.cand, sb)):
        print(f"  -- {mode} --")
        cur = None
        line = []
        other = sb if mode == args.base else sa
        for i, t in enumerate(seq):
            if t[0] != cur:
                if line:
                    print(f"    L{cur}: " + "  ".join(line))
                cur, line = t[0], []
            mark = "*" if i >= len(other) or other[i] != t else " "
            line.append(f"{mark}D{t[1]}:{t[2]}→{t[4]}")
        if line:
            print(f"    L{cur}: " + "  ".join(line))

    print()
    print("## mm_lint（ループ別）")
    for mode in (args.base, args.cand):
        lb = _lint_by_loop(res[mode][1])
        print(f"  {mode}: " + "  ".join(
            f"L{lp}:{dict(sorted(c.items()))}" for lp, c in sorted(lb.items())))

    print()
    print("## 事件の成否（ループ・日別／発生=1 不発=0）")
    for mode in (args.base, args.cand):
        inc = _incidents(res[mode][0])
        print(f"  {mode}: " + "  ".join(
            f"L{k[0]}D{k[1]}:{int(v)}" for k, v in sorted(inc.items())))


if __name__ == "__main__":
    main()
