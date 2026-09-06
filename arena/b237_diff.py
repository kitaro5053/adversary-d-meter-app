# -*- coding: utf-8 -*-
"""B-237 flip 検死ヘルパ（読み取り専用）：2条件で同一局を回して**最初の差分**を出す。

`arena/b236_diff.py` の 5切替口版（条件名は `arena.b237_ab` と同じ書式＝`b232+b234` 等）。
フラグは実行時に退避→上書き→復元するだけ（ファイル不変）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b237_diff \
        --game random_BTX#3 --days 5 --perm id --a base --b base+b237 --limit 12 --roles
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from arena.b237_ab import cond_knobs, _apply, _restore


def _play(game: str, days: int, cond: str, perm: str, loops: int = 8):
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
    old = _apply(cond_knobs(cond))
    install_perm(perm)
    log: list[dict] = []
    try:
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}, log=log)
    finally:
        uninstall_perm()
        _restore(old)
    return sc, state, log


def _key(e: dict):
    """決定ログの比較キー（席の同一性＋選んだ手）。"""
    c = e.get("chosen") or {}
    return (e.get("loop"), e.get("day"), e.get("actor"), e.get("decision"),
            json.dumps(c, ensure_ascii=False, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-236 flip 検死（読み取り専用）")
    ap.add_argument("--game", required=True)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--a", default="base")
    ap.add_argument("--b", default="base+b237")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--roles", action="store_true", help="神視点（真配役）を表示")
    ap.add_argument("--incidents", action="store_true",
                    help="実際に発生した事件イベントを並べる")
    a = ap.parse_args(argv)

    sc, sa, la = _play(a.game, a.days, a.a, a.perm, loops=a.loops)
    _, sb, lb = _play(a.game, a.days, a.b, a.perm, loops=a.loops)
    print(f"# {a.game} days={a.days} perm={a.perm}: A={a.a} vs B={a.b}")
    print(f"  A: winner={sa.winner} loop={sa.loop_no} / "
          f"B: winner={sb.winner} loop={sb.loop_no}")
    if a.roles:
        print("  神視点 配役: " + " ".join(f"{n}={sc.role_of(n)}" for n in sc.cast))
        print("  事件: " + " ".join(f"D{i.day}:{i.name}({i.culprit})"
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
              f" {ea.get('decision')} {json.dumps(ea.get('chosen'), ensure_ascii=False)}")
        print(f"      B L{eb.get('loop')}D{eb.get('day')} {eb.get('actor')}"
              f" {eb.get('decision')} {json.dumps(eb.get('chosen'), ensure_ascii=False)}")

    def _loops(st):
        return [(e.get("loop"), e.get("winner") or e.get("result"))
                for e in st.history if e.get("event") == "loop_result"]
    print(f"  A loop_result: {_loops(sa)}")
    print(f"  B loop_result: {_loops(sb)}")
    if a.incidents:
        def _inc(st):
            return [f"L{e.get('loop')}D{e.get('day')}:{e.get('incident') or e.get('name')}"
                    for e in st.history if e.get("event") in ("incident", "incident_fired")]
        print(f"  A incidents: {_inc(sa)}")
        print(f"  B incidents: {_inc(sb)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
