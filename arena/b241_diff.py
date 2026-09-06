# -*- coding: utf-8 -*-
"""B-241 flip 検死ヘルパ（読み取り専用）：2条件で同一局を回して**最初の差分**を出す。

`arena/b240_diff.py` の 8切替口版（`b241g`／`b241y` を足しただけ）。フラグは実行時に
退避→上書き→復元するだけ（ファイル不変）。条件名の書式は b236/b237 と同じ。
`base`＝現行既定（`b230`/`b231`/`b232`/`b234` の4つ ON）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b241_diff \
        --game random_BTX#1 --days 5 --perm id --a base --b base+b241g --limit 8
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

KNOBS: dict[str, tuple[str, str]] = {
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
    "b232": ("hp", "B232_COOL_MATH"),
    "b234": ("bl", "B234_GOSHINBOKU_TRAIT"),
    "b237": ("hp", "B237_SPLIT_REDUNDANT"),
    "b240": ("hp", "B240_COOL_BUDGET"),
    "b241g": ("hp", "B241_GUARD_GROUND"),
    "b241y": ("hp", "B241_GUARD_YIELD"),
}
ORDER = ("b230", "b231", "b232", "b234", "b237", "b240", "b241g", "b241y")
BASE = ("b230", "b231", "b232", "b234")


def cond_knobs(name: str) -> tuple[str, ...]:
    parts: set = set()
    for p in [x for x in name.split("+") if x]:
        if p in ("off", "none"):
            continue
        if p == "base":
            parts |= set(BASE)
        elif p == "all":
            parts |= set(ORDER)
        elif p in KNOBS:
            parts.add(p)
        else:
            raise SystemExit(f"未知の切替口: {p}（条件名 {name}）")
    return tuple(k for k in ORDER if k in parts)


def _holders():
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    return {"hp": HP, "bl": bl}


def _apply(knobs, extra: dict | None = None) -> dict:
    # ★B-248＝ベースライン条件（`base`）がリポジトリ既定と一致するかを実行時検査する（§72-45）
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), BASE, driver="b241_diff")
    holders = _holders()
    on = set(knobs)
    old = {}
    for k, (where, attr) in KNOBS.items():
        old[k] = getattr(holders[where], attr, False)
        setattr(holders[where], attr, k in on)
    for a, v in (extra or {}).items():
        old["#" + a] = getattr(holders["hp"], a)
        setattr(holders["hp"], a, v)
    return old


def _restore(old: dict):
    holders = _holders()
    for k, v in old.items():
        if k.startswith("#"):
            setattr(holders["hp"], k[1:], v)
        else:
            where, attr = KNOBS[k]
            setattr(holders[where], attr, v)


def _play(game: str, days: int, cond: str, perm: str, loops: int = 8,
          extra: dict | None = None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import random_script, run_game
    from sim.sample_scripts import SAMPLE_SCRIPTS
    from arena.tie_noise import install_perm, uninstall_perm
    name, seed = game.rsplit("#", 1)
    seed = int(seed)
    if name.startswith("random_"):
        sc = random_script(name.split("_", 1)[1], seed, days=days)
    else:
        sc = SAMPLE_SCRIPTS[name]()
    old = _apply(cond_knobs(cond), extra)
    install_perm(perm)
    log: list[dict] = []
    try:
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                            log=log)
    finally:
        uninstall_perm()
        _restore(old)
    return sc, state, log


def _key(e: dict):
    c = e.get("chosen") or {}
    return (e.get("loop"), e.get("day"), e.get("actor"), e.get("decision"),
            json.dumps(c, ensure_ascii=False, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-241 flip 検死（読み取り専用）")
    ap.add_argument("--game", required=True)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--a", default="base")
    ap.add_argument("--b", default="base+b241g")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--cap", type=float, default=None)
    ap.add_argument("--drop", type=float, default=None,
                    help="B241_GROUND_MAX_DROP（腕(B) の掃引口）")
    ap.add_argument("--min-gap", type=int, default=None,
                    help="B241_GROUND_MIN_GAP（腕(B) の掃引口）")
    ap.add_argument("--roles", action="store_true", help="★神視点（真配役・答え合わせ専用）")
    ap.add_argument("--incidents", action="store_true")
    a = ap.parse_args(argv)
    extra = {}
    if a.cap is not None:
        extra["B240_CAP"] = a.cap
    if a.drop is not None:
        extra["B241_GROUND_MAX_DROP"] = a.drop
    if a.min_gap is not None:
        extra["B241_GROUND_MIN_GAP"] = a.min_gap
    extra = extra or None

    sc, sa, la = _play(a.game, a.days, a.a, a.perm, loops=a.loops)
    _, sb, lb = _play(a.game, a.days, a.b, a.perm, loops=a.loops, extra=extra)
    print(f"# {a.game} days={a.days} perm={a.perm}: A={a.a} vs B={a.b} cap={a.cap}")
    print(f"  A: winner={sa.winner} loop={sa.loop_no} / "
          f"B: winner={sb.winner} loop={sb.loop_no}")
    if a.roles:
        print("  ★神視点 配役: " + " ".join(f"{n}={sc.role_of(n)}" for n in sc.cast))
        print("  ★神視点 事件: " + " ".join(f"D{i.day}:{i.name}({i.culprit})"
                                             for i in sc.incidents))
    ka = [_key(e) for e in la]
    kb = [_key(e) for e in lb]
    i = 0
    while i < min(len(ka), len(kb)) and ka[i] == kb[i]:
        i += 1
    print(f"  一致した決定数={i} / A={len(la)} B={len(lb)}")
    for j in range(i, min(i + a.limit, max(len(la), len(lb)))):
        ea = la[j] if j < len(la) else {}
        eb = lb[j] if j < len(lb) else {}
        print(f"  [{j}] A L{ea.get('loop')}D{ea.get('day')} {ea.get('actor')}"
              f" {ea.get('decision')} "
              f"{json.dumps(ea.get('chosen'), ensure_ascii=False)}")
        print(f"      B L{eb.get('loop')}D{eb.get('day')} {eb.get('actor')}"
              f" {eb.get('decision')} "
              f"{json.dumps(eb.get('chosen'), ensure_ascii=False)}")

    def _loops(st):
        return [(e.get("loop"), e.get("winner") or e.get("result"))
                for e in st.history if e.get("event") == "loop_result"]
    print(f"  A loop_result: {_loops(sa)}")
    print(f"  B loop_result: {_loops(sb)}")
    if a.incidents:
        def _inc(st):
            return [f"L{e.get('loop')}D{e.get('day')}:{e.get('name')}"
                    f"={e.get('occurs')}"
                    for e in st.history if e.get("event") == "incident"]
        print(f"  A incidents: {_inc(sa)}")
        print(f"  B incidents: {_inc(sb)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
