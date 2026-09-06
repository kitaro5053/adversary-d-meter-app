# -*- coding: utf-8 -*-
"""B-230：分離の幾何検算（相殺検出）の計測プローブ（読み取り専用）。

B-229 の `arena/b229_probe.py cmd_selfcancel`（数え上げ）を詳細化したもの。
AI・エンジン（sim/ engine/）には触れない。フラグはクラス属性を実行時に
退避→復元するだけ（ファイル不変）。

用語（B-229 §7-2）：
- **相殺席**＝主人公チームが同一ターンに、同じエリアにいる2キャラへ同種の移動札を
  置いた組（合成後も同室＝分離になっていない）。
- **供給実績ペア**＝過去ループの脚本家能力フェイズ不安+1（公開イベント）の
  （受け手×同室 present）の組。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b230_probe seats --days 5
    PYTHONHASHSEED=0 ... python -m arena.b230_probe seats --days 5 --on   # B230 ON で再計測
    PYTHONHASHSEED=0 ... python -m arena.b230_probe bench --days 5 --on --perm id
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace

MOVES = frozenset({"移動←→", "移動↑↓", "移動斜め"})


@contextmanager
def pro_flags(**attrs):
    """HeuristicProtagonist のクラス属性を一時的に上書き（退避→復元）。"""
    from agents import HeuristicProtagonist as HP
    saved = []
    try:
        for k, v in attrs.items():
            saved.append((k, getattr(HP, k)))
            setattr(HP, k, v)
        yield
    finally:
        for k, v in reversed(saved):
            setattr(HP, k, v)


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
    log: list[dict] = []
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}, log=log)
    return state, log


def _supply_pairs(state):
    """(loop, 受け手, 供給役候補) の集合＝能力フェイズ不安+1 の公開実績。"""
    pairs = set()
    for e in state.history:
        if (e.get("event") == "unrest"
                and e.get("phase") == "mastermind_ability"
                and (e.get("delta") or 0) > 0):
            rcv = e.get("target")
            for p in (e.get("present") or ()):
                if p != rcv:
                    pairs.add((e.get("loop"), rcv, p))
    return pairs


def _cancel_seats(state, log):
    """1局の相殺席の詳細リスト。(loop, day, card, t1, t2, area, 供給実績loops)。"""
    supply = _supply_pairs(state)
    turns = defaultdict(list)
    pos_by_turn: dict = {}
    order = defaultdict(list)          # ターン内の主人公の手（席順）
    for e in log:
        if e.get("decision") != "set_card" or e.get("actor") == "mastermind":
            continue
        c = e["chosen"]
        key = (e["loop"], e["day"])
        order[key].append((c["card"], c["target"], c["target_kind"]))
        if c["card"] in MOVES and c["target_kind"] == "character":
            turns[key].append((c["card"], c["target"]))
        if key not in pos_by_turn:
            v = e.get("view") or {}
            pos_by_turn[key] = {ch.get("name"): ch.get("area")
                                for ch in (v.get("characters") or [])}
    rows = []
    for (lp, dy), mv in sorted(turns.items()):
        pos = pos_by_turn.get((lp, dy), {})
        for i in range(len(mv)):
            for j in range(i + 1, len(mv)):
                (c1, t1), (c2, t2) = mv[i], mv[j]
                if c1 != c2 or t1 == t2:
                    continue
                if pos.get(t1) is None or pos.get(t1) != pos.get(t2):
                    continue
                sup_loops = sorted(l0 for (l0, r, p) in supply
                                   if l0 < lp and (r, p) in ((t1, t2), (t2, t1)))
                rows.append({"loop": lp, "day": dy, "card": c1,
                             "pair": (t1, t2), "area": pos.get(t1),
                             "supply_loops": sup_loops,
                             "seat_moves": order[(lp, dy)]})
    return rows


def cmd_seats(args) -> int:
    from arena.benchmark import benchmark_scripts
    flags = {"B230_SPLIT_GEOMETRY": True} if args.on else {}
    tot_pairs = tot_sup = 0
    with pro_flags(**flags):
        for name, seed, sc in benchmark_scripts(days=args.days):
            state, log = _play(name, seed, args.days, loops=args.loops)
            rows = _cancel_seats(state, log)
            tot_pairs += len(rows)
            sup_rows = [r for r in rows if r["supply_loops"]]
            tot_sup += len(sup_rows)
            for r in (rows if args.all else sup_rows):
                mark = "★供給実績" if r["supply_loops"] else ""
                print(f"{name}#{seed} L{r['loop']}D{r['day']} {mark} "
                      f"{r['card']} → {r['pair'][0]}×{r['pair'][1]} "
                      f"@{r['area']} 供給実績L={r['supply_loops']}")
                if args.verbose:
                    print("   席順: " + " / ".join(
                        f"{c}→{t}" for c, t, k in r["seat_moves"]))
    print(f"days={args.days} on={args.on}: 相殺席={tot_pairs} "
          f"うち供給実績ペア={tot_sup}")
    return 0


def cmd_bench(args) -> int:
    """両ベンチ（perm 指定つき）を B230 ON/OFF で回して per-game を出す。"""
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    flags = {"B230_SPLIT_GEOMETRY": True} if args.on else {}
    if args.cap is not None:
        flags["B230_CAP"] = args.cap
    with pro_flags(**flags):
        install_perm(args.perm)
        try:
            rep = run_benchmark(loops=args.loops, days=args.days, verbose=False)
        finally:
            uninstall_perm()
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"days={args.days} perm={args.perm} on={args.on} cap={args.cap}: "
          f"防衛={n_def} 平均={rep['mean_loops_to_win']} 結末={rep['outcomes']}")
    for r in rep["rows"]:
        print(f"  {r['script']}#{r['seed']}: {r['loops_to_win']} {r['outcome']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-230 計測プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["seats", "bench"])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--on", action="store_true", help="B230_SPLIT_GEOMETRY=True で測る")
    ap.add_argument("--cap", type=float, default=None, help="B230_CAP の上書き（掃引用）")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--all", action="store_true", help="seats: 供給実績なしの相殺席も表示")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"seats": cmd_seats, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
