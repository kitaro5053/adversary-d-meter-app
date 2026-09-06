# -*- coding: utf-8 -*-
"""B-232：`B224_COOL_FLOOR` への冷却算術ゲートの計測プローブ（読み取り専用）。

B-229 §7-1「案1」の的を測る道具。AI・エンジン（`agents/` `sim/` `engine/`）には
**触れない**（フラグはクラス属性を実行時に退避→復元するだけ＝ファイル不変）。
計測は `HeuristicProtagonist.decide` を包む read-only ラッパで行う。

## 用語（B-229 §2 の発火算術）

- **floor 席**＝その席の decide で `_b224_cool_today` が非空で、かつ
  options に `不安-1→候補`（候補＝`_b224_cool_today` の要素）が実在した席
  ＝**B-224 の床が実際に当たった席**。
- **供給実績ペア**＝過去ループの脚本家能力フェイズ 不安+1（公開イベント）の
  （受け手×同室 present）の組（B-208 ①／B-230 と同じ公開チャネル）。
- **冷却算術**（材料は全て公開情報）＝事件チェック時の不安の下界：

      final = u                                   # 今の不安（公開カウンター）
            + (mm が今日この候補に札を伏せている ? 0 : -1)   # 冷却1枚の実効
              ↑ mm 札が重なると 不安+1 で相殺／不安禁止 で無効＝**冷却は効かない**
            + (供給実績ペアの相方が今**同室**にいる ? +1 : 0)  # 能力フェイズの供給

  `final >= 臨界` なら**この候補を今日冷やしても事件は止まらない**
  ＝**発火時点で証明可能な空振り**＝これが「算術上勝てない floor 席」。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b232_probe seats --days 5
    PYTHONHASHSEED=0 ... python -m arena.b232_probe seats --days 3 --on
    PYTHONHASHSEED=0 ... python -m arena.b232_probe bench --days 5 --perm id --on
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from dataclasses import replace


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


def _supply_partners(view: dict, same_day: bool = False) -> dict:
    """過去ループの能力フェイズ 不安+1 の（受け手×同室 present）実績（無向）。

    ★プローブ側の**独立実装**（実装（`agents/b232_cool_math.py`）を呼ばない）＝
    的の測定が実装の写しにならないようにする。B-230 `_b230_pairs` と同じ公開チャネル。
    """
    lp = view.get("loop")
    out: dict[str, set] = {}
    for e in view.get("history", []) or []:
        if (e.get("event") == "unrest"
                and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0
                and e.get("loop") != lp
                and (not same_day or e.get("day") == view.get("day"))):
            rcv = e.get("target")
            for p in (e.get("present") or ()):
                if p != rcv:
                    out.setdefault(rcv, set()).add(p)
                    out.setdefault(p, set()).add(rcv)
    return out


def analyze_seat(view: dict, cands) -> list[dict]:
    """floor 対象候補ごとの冷却算術（公開情報のみ）。"""
    from engine.data import unrest_threshold_of
    chars = {c.get("name"): c for c in (view.get("characters") or [])}
    pairs = _supply_partners(view)
    pairs_day = _supply_partners(view, same_day=True)
    mm_on = {p.get("target") for p in (view.get("placements") or [])
             if p.get("owner") == "mastermind"
             and p.get("target_kind") == "character"}
    rows = []
    for cand in sorted(cands):
        c = chars.get(cand)
        if c is None or not c.get("alive", True):
            continue
        th = unrest_threshold_of(cand)
        if not th:
            continue                     # 臨界0（黒猫）は B-224 条件6 が既に除外
        u = int(c.get("unrest", 0) or 0)
        area = c.get("area")
        partners = sorted(p for p in (pairs.get(cand) or ())
                          if (chars.get(p) or {}).get("area") == area
                          and (chars.get(p) or {}).get("alive", True)
                          and area is not None)
        supply = 1 if partners else 0
        partners_day = sorted(p for p in (pairs_day.get(cand) or ())
                              if (chars.get(p) or {}).get("area") == area
                              and (chars.get(p) or {}).get("alive", True)
                              and area is not None)
        blocked = cand in mm_on          # mm 札が重なる＝冷却が相殺/無効化されうる
        cool = 0 if blocked else -1
        final = u + cool + supply
        rows.append({"cand": cand, "u": u, "th": th, "area": area,
                     "partners": partners, "supply": supply,
                     "mm_card_on_cand": blocked, "final": final,
                     "hopeless": final >= th,
                     # 掃引軸＝供給実績を「同じ日」に限る版（B232_DAY_MATCHED）
                     "partners_day": partners_day,
                     "day_supply": 1 if partners_day else 0})
    return rows


@contextmanager
def instrument(sink: list):
    """decide を包んで floor 席を記録する（読み取り専用・復元つき）。"""
    from agents import HeuristicProtagonist as HP
    orig = HP.decide

    def wrapped(self, view, decision, options):
        out = orig(self, view, decision, options)
        if decision == "set_card":
            cands = frozenset(getattr(self, "_b224_cool_today", ()) or ())
            if cands:
                hit = sorted({o["target"] for o in options
                              if o.get("card") == "不安-1"
                              and o.get("target_kind") == "character"
                              and o.get("target") in cands})
                if hit:
                    sink.append({
                        "loop": view.get("loop"), "day": view.get("day"),
                        "seat": view.get("seat"),
                        "chosen": (out.get("card"), out.get("target")),
                        "rows": analyze_seat(view, hit),
                    })
        return out

    HP.decide = wrapped
    try:
        yield
    finally:
        HP.decide = orig


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


def _flags(args) -> dict:
    f: dict = {}
    if args.on:
        f["B232_COOL_MATH"] = True
    if args.require_supply is not None:
        f["B232_REQUIRE_SUPPLY"] = bool(args.require_supply)
    if args.scope is not None:
        f["B232_SCOPE"] = args.scope
    if args.min_obs is not None:
        f["B232_SUPPLY_MIN_OBS"] = args.min_obs
    return f


def cmd_seats(args) -> int:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(args.perm)
    try:
        return _seats(args, benchmark_scripts)
    finally:
        uninstall_perm()


def _seats(args, benchmark_scripts) -> int:
    tot_seat = tot_row = 0
    tot_hopeless_seat = tot_hopeless_row = 0
    tot_narrow_row = tot_narrow_seat = tot_day_row = 0
    with pro_flags(**_flags(args)):
        for name, seed, sc in benchmark_scripts(days=args.days):
            if args.only and f"{name}#{seed}" != args.only:
                continue
            sink: list = []
            with instrument(sink):
                _play(name, seed, args.days, loops=args.loops)
            for rec in sink:
                rows = rec["rows"]
                if not rows:
                    continue
                tot_seat += 1
                tot_row += len(rows)
                hop = [r for r in rows if r["hopeless"]]
                narrow = [r for r in hop if r["supply"]]
                tot_day_row += sum(1 for r in hop if r["day_supply"])
                tot_hopeless_row += len(hop)
                tot_narrow_row += len(narrow)
                if hop and len(hop) == len(rows):
                    tot_hopeless_seat += 1
                if narrow and len(narrow) == len(rows):
                    tot_narrow_seat += 1
                if args.all or hop:
                    for r in rows:
                        mark = "★空振り" if r["hopeless"] else "        "
                        print(f"{name}#{seed} L{rec['loop']}D{rec['day']} "
                              f"{rec['seat']} {mark} {r['cand']} "
                              f"u={r['u']}/th={r['th']} @{r['area']} "
                              f"mm札={'有' if r['mm_card_on_cand'] else '無'} "
                              f"供給={r['supply']}{r['partners']} "
                              f"同日供給={r['day_supply']}{r['partners_day']} "
                              f"→ final={r['final']} "
                              f"(選択={rec['chosen'][0]}→{rec['chosen'][1]})")
    print(f"days={args.days} on={args.on} require_supply={args.require_supply} "
          f"scope={args.scope} min_obs={args.min_obs}: floor席={tot_seat} floor対象={tot_row} "
          f"★空振り対象[広]={tot_hopeless_row}（席={tot_hopeless_seat}） "
          f"★空振り対象[狭=供給実績つき]={tot_narrow_row}（席={tot_narrow_seat}） "
          f"★空振り対象[最狭=同日供給実績つき]={tot_day_row}")
    return 0


def cmd_bench(args) -> int:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    with pro_flags(**_flags(args)):
        install_perm(args.perm)
        try:
            rep = run_benchmark(loops=args.loops, days=args.days, verbose=False)
        finally:
            uninstall_perm()
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"days={args.days} perm={args.perm} on={args.on} "
          f"require_supply={args.require_supply} scope={args.scope} "
          f"min_obs={args.min_obs}: "
          f"防衛={n_def} 平均={rep['mean_loops_to_win']} 結末={rep['outcomes']}")
    for r in rep["rows"]:
        print(f"  {r['script']}#{r['seed']}: {r['loops_to_win']} {r['outcome']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-232 計測プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["seats", "bench"])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--on", action="store_true", help="B232_COOL_MATH=True で測る")
    ap.add_argument("--require-supply", type=int, default=None,
                    help="B232_REQUIRE_SUPPLY の上書き（0/1・掃引用）")
    ap.add_argument("--scope", default=None, choices=["floor", "both"],
                    help="B232_SCOPE の上書き（掃引用）")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--all", action="store_true", help="seats: 空振りでない席も表示")
    ap.add_argument("--min-obs", type=int, default=None,
                    help="B232_SUPPLY_MIN_OBS の上書き（掃引用）")
    ap.add_argument("--only", default=None, help="seats: 1局だけ測る（例 btx_future#4）")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"seats": cmd_seats, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
