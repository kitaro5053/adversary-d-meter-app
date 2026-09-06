# -*- coding: utf-8 -*-
"""B-236 相互作用の検死プローブ（読み取り専用・実装を呼ばない）。

`arena.b232_probe` の `instrument` / `analyze_seat`（**実装の独立再実装**）を、
`arena.b236_ab` の 4切替口の条件名で回すだけの薄いラッパ。

用途＝`B230_SPLIT_GEOMETRY` を ON にすると `B232_COOL_MATH` の前提
（供給実績ペアの相方が**今日同室**）が崩れるかを、盤面の実数で示す。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b236_probe \
        --game random_BTX#1 --days 5 --perm id --conds off,b230 --loop 6
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from arena.b232_probe import instrument
from arena.b236_ab import cond_knobs, _apply, _restore


def run(game: str, days: int, cond: str, perm: str, loops: int = 8) -> list:
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
    sink: list = []
    try:
        with instrument(sink):
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(replace(sc, loops=loops),
                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        uninstall_perm()
        _restore(old)
    return sink


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-236 相互作用の検死プローブ")
    ap.add_argument("--game", required=True)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--conds", default="off,b230")
    ap.add_argument("--loop", type=int, default=0, help="このループだけ表示（0=全部）")
    ap.add_argument("--loops", type=int, default=8)
    a = ap.parse_args(argv)
    for cond in a.conds.split(","):
        sink = run(a.game, a.days, cond, a.perm, loops=a.loops)
        print(f"\n### {a.game} days={a.days} perm={a.perm} cond={cond}"
              f" : floor 席 {len(sink)} 件")
        for rec in sink:
            if a.loop and rec["loop"] != a.loop:
                continue
            print(f"  L{rec['loop']}D{rec['day']} seat={rec['seat']} "
                  f"選択={rec['chosen']}")
            for r in rec["rows"]:
                print(f"    候補={r['cand']} area={r['area']} u={r['u']} "
                      f"臨界={r['th']} 相方(同室)={r['partners']} 供給={r['supply']} "
                      f"mm札重なり={r['mm_card_on_cand']} final={r['final']} "
                      f"★算術上勝てない={r['hopeless']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
