# -*- coding: utf-8 -*-
"""B-284：暗躍札の供給会計（`_card_supply` / `_cap_reaching`）の**精査＋席数の計測**。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で触らない（読むだけ）。
対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う（ラッパを挟まない＝
ベンチと同一の乱数消費が構造的に保証される＝`arena/b279_probe.py` と同じ作法）。

------------------------------------------------------------------------------
測るもの
------------------------------------------------------------------------------
**(0) liveness**＝`agents/heuristic.py` の `_card_supply`（A-73 のスカラー供給）が
  既定パラメータで**意思決定に到達しているか**。`_supply_of` を呼ぶのは
  `_fits` / `_decoy_fits` の `not _alloc` 分岐と、ctx の報告値 `supply` だけ。
  既定は `supply_model=2 → _alloc=int(supply_alloc)=1` なので `not _alloc` は偽＝
  **到達しない**。`--liveness` はこれを実測で確かめる（`_card_supply` を巨大値に
  差し替えて全席の決定が1つも動かないことを確認する）。

**(1) 席数**＝`_cap_reaching` の `暗躍+2` 項を KB 厳密値に締めた時に**判断が変わる席**。
  - `exact` ＝ sink が1本の部分集合だけ `min(2,dmax)→min(1,dmax)`（＝上限 `days_left+1`）
  - `flat1` ＝ 部分集合の大きさに関係なく一律 `min(1,dmax)`（起票文 (A) の字義どおり・対照）
  変化を3段で数える：
    L-a: `funded`（資金化する勝ち筋の集合）が変わった席
    L-b: `reachable_culprits` / `decoy_board` / `decoy_funded` が変わった席
    L-c: **最上位手が入れ替わった**席（options 全部を採点し直して argmax を比較）

**(2) 落差**＝「`暗躍+2` を消費した前後で会計が2点跳ねる」ことの直接計測。
  `used_cards` に `暗躍+2` を足した反実仮想（＝今この瞬間に +2 を切った盤面）と
  現局面で L-a/L-c を比べる。`exact` ON では落差が1点になるので、
  同じ計測を ON でも回して**跳ねる席が減るか**を見る。

------------------------------------------------------------------------------
CLI
------------------------------------------------------------------------------
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b284_probe --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b284_probe --days 5
    ... --mode exact|flat1|drop|exact_drop|liveness   # 既定＝exact
    ... --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace


# ---------------------------------------------------------------------------
# 採点ユーティリティ（本番の `decide` と同じ出口を使う／rng は使わない）
# ---------------------------------------------------------------------------
def _score_all(mm, rec: dict, a: dict) -> list[float]:
    """その席の全 option を `a`（分析結果）で採点した並び。

    `decide` の出口と同じ式を使う（`_score_set` −`_plus2_penalty` −`_b286_penalty`）。
    rng は消費しない＝反実仮想の再採点が原局面の乱数列を汚さない。
    """
    view, dec = rec["view"], rec["decision"]
    out = []
    for o in rec["options"]:
        if dec == "set_card":
            v = (mm._score_set(o, a, view)
                 - mm._plus2_penalty(o, a, view)
                 - mm._b286_penalty(o, a))
        else:
            v = mm._score_ability(o, a)
        out.append(float(v))
    return out


def _argmax_set(scores: list[float]) -> frozenset:
    """最大点を取る option の添字集合（同点は全部入れる＝タイブレーク非依存）。"""
    if not scores:
        return frozenset()
    m = max(scores)
    return frozenset(i for i, s in enumerate(scores) if s >= m - 1e-9)


def _a_digest(a: dict) -> tuple:
    """`_analyze` の結果のうち、供給会計が動かしうる部分の指紋。"""
    return (
        tuple(sorted(a.get("funded") or ())),
        tuple(sorted(a.get("reachable_culprits") or ())),
        a.get("decoy_board"),
        bool(a.get("decoy_funded")),
    )


# ---------------------------------------------------------------------------
# 1対局
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int,
               mode: str) -> dict:
    import agents.heuristic as H
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    recs = [r for r in log if r["actor"] == "mastermind"
            and r["decision"] in ("set_card", "mastermind_ability")]

    base_mm = HeuristicMastermind(seed)     # 採点専用（_analyze/_score_* は rng 不使用）
    cf_mm = HeuristicMastermind(seed)

    rows = []
    n_seat = 0
    n_p2_left = 0
    n_seat_hit = 0
    for i, r in enumerate(recs):
        view = r["view"]
        n_seat += 1
        if mode == "subset":
            used0 = (view.get("used_cards", {}) or {}).get("mastermind", []) or []
            if "暗躍+2" not in used0:
                n_p2_left += 1
            _SUB["seat_hit"] = False
            H.B284_PROBE = _hook
            try:
                base_mm._analyze(view)
            finally:
                H.B284_PROBE = None
            if _SUB["seat_hit"]:
                n_seat_hit += 1
                rows.append({
                    "script": name, "seed": seed, "days": days,
                    "loop": r["loop"], "day": r["day"], "decision": r["decision"],
                    "idx": i, "p2_left": "暗躍+2" not in used0,
                    "funded_flip": False, "reach_flip": False,
                    "decoy_flip": False, "top_flip": False,
                    "chosen": _opt_str(r["chosen"]),
                    "days_left": max(1, view.get("days_per_loop", view["day"])
                                     - view["day"] + 1),
                })
            continue
        used = (view.get("used_cards", {}) or {}).get("mastermind", []) or []
        p2_left = "暗躍+2" not in used
        if p2_left:
            n_p2_left += 1

        # -- ベースライン（既定 OFF）
        for k in ("B284_SUPPLY_EXACT", "B284_SUPPLY_FLAT1"):
            setattr(base_mm, k, False)
        a0 = base_mm._analyze(view)
        s0 = _score_all(base_mm, r, a0)

        # -- 反実仮想
        cf_view = view
        for k in ("B284_SUPPLY_EXACT", "B284_SUPPLY_FLAT1"):
            setattr(cf_mm, k, False)
        if mode == "exact":
            cf_mm.B284_SUPPLY_EXACT = True
        elif mode == "flat1":
            cf_mm.B284_SUPPLY_FLAT1 = True
        elif mode == "exact_drop":
            base_mm.B284_SUPPLY_EXACT = True
            cf_mm.B284_SUPPLY_EXACT = True
            a0 = base_mm._analyze(view)
            s0 = _score_all(base_mm, r, a0)
            cf_view = json.loads(json.dumps(view))
            cf_view.setdefault("used_cards", {}).setdefault("mastermind", [])
            if "暗躍+2" not in cf_view["used_cards"]["mastermind"]:
                cf_view["used_cards"]["mastermind"].append("暗躍+2")
        elif mode == "drop":
            # 「今この瞬間に +2 を切った」盤面との差＝落差そのもの
            cf_view = json.loads(json.dumps(view))
            cf_view.setdefault("used_cards", {}).setdefault("mastermind", [])
            if "暗躍+2" not in cf_view["used_cards"]["mastermind"]:
                cf_view["used_cards"]["mastermind"].append("暗躍+2")
        elif mode == "liveness":
            cf_mm.B284_LIVENESS_BREAK = True   # `_card_supply` を巨大値へ壊す
        else:
            raise ValueError(f"unknown mode: {mode}")

        if mode in ("drop", "exact_drop") and not p2_left:
            continue        # 既に消費済み＝落差の測りようがない

        cf_r = dict(r, view=cf_view)
        a1 = cf_mm._analyze(cf_view)
        s1 = _score_all(cf_mm, cf_r, a1)

        d0, d1 = _a_digest(a0), _a_digest(a1)
        am0, am1 = _argmax_set(s0), _argmax_set(s1)
        if d0 == d1 and am0 == am1:
            continue
        ch = r["chosen"]
        rows.append({
            "script": name, "seed": seed, "days": days,
            "loop": r["loop"], "day": r["day"], "decision": r["decision"],
            "idx": i, "p2_left": p2_left,
            "funded0": sorted(a0.get("funded") or ()),
            "funded1": sorted(a1.get("funded") or ()),
            "reach0": sorted(a0.get("reachable_culprits") or ()),
            "reach1": sorted(a1.get("reachable_culprits") or ()),
            "decoy0": [a0.get("decoy_board"), bool(a0.get("decoy_funded"))],
            "decoy1": [a1.get("decoy_board"), bool(a1.get("decoy_funded"))],
            "funded_flip": d0[0] != d1[0],
            "reach_flip": d0[1] != d1[1],
            "decoy_flip": d0[2:] != d1[2:],
            "top_flip": am0 != am1,
            "top0": [_opt_str(r["options"][j]) for j in sorted(am0)],
            "top1": [_opt_str(r["options"][j]) for j in sorted(am1)],
            "chosen": _opt_str(ch),
            "days_left": max(1, view.get("days_per_loop", view["day"]) - view["day"] + 1),
            # ★構造コスト（実際に置く暗躍の個数）と実効コスト（リスク割増込み）を併記する。
            #   資金ゲートが比べているのは**実効コスト**なので、供給を KB 厳密値に締めた時に
            #   落ちた筋が「構造コストでは届いていた」のかを後から判定できる。
            "raw0": {k: float(v) for k, v in (a0.get("path_costs_raw") or {}).items()},
            "cost0": {k: float(v) for k, v in (a0.get("path_costs") or {}).items()},
        })
    return {"rows": rows, "n_seat": n_seat, "n_p2_left": n_p2_left,
            "n_seat_hit": n_seat_hit}


# ---------------------------------------------------------------------------
# subset モード＝`_cap_reaching` の部分集合ごとの判定を直接数える（B284_PROBE）
# ---------------------------------------------------------------------------
_SUB: dict = {"n": 0, "single": 0, "single_p2": 0, "decisive": 0, "seat_hit": False}


def _hook(view, n_sinks, dmax, demand, days_left, p2_left, cap_now, cap_exact):
    _SUB["n"] += 1
    if n_sinks == 1:
        _SUB["single"] += 1
        if p2_left:
            _SUB["single_p2"] += 1
    if cap_exact < cap_now - 1e-9 and cap_exact + 1e-9 < demand <= cap_now + 1e-9:
        # ★現行は「届く」・KB 厳密値では「届かない」＝過大評価が判定を分けた部分集合
        _SUB["decisive"] += 1
        _SUB["seat_hit"] = True


def _opt_str(o: dict) -> str:
    if "card" in o:
        return f"{o.get('card')}→{o.get('target')}"
    return f"{o.get('kind') or o.get('action')}→{o.get('target')}"


# ---------------------------------------------------------------------------
def sweep(days: int, loops: int, mode: str) -> dict:
    from arena.benchmark import benchmark_scripts
    scripts = benchmark_scripts(days=days)
    rows, n_seat, n_p2, n_hit = [], 0, 0, 0
    for name, seed, sc in scripts:
        rep = probe_game(name, seed, sc, days, loops, mode)
        rows.extend(rep["rows"])
        n_seat += rep["n_seat"]
        n_p2 += rep["n_p2_left"]
        n_hit += rep.get("n_seat_hit", 0)
    return {"days": days, "mode": mode, "n_games": len(scripts),
            "n_seat": n_seat, "n_p2_left": n_p2, "n_seat_hit": n_hit,
            "subset_stats": dict(_SUB), "rows": rows}


def summarize(rep: dict) -> str:
    rows = rep["rows"]
    out = [f"[{rep['days']}日級 mode={rep['mode']}] "
           f"{rep['n_games']}局／脚本家の席 {rep['n_seat']}"
           f"（うち 暗躍+2 未消費 {rep['n_p2_left']}）"]
    if rep["mode"] == "subset":
        st = rep["subset_stats"]
        out.append(f"  部分集合の判定 {st['n']} 回（うち sink 1本 {st['single']}／"
                   f"そのうち 暗躍+2 未消費 {st['single_p2']}）")
        out.append(f"  ★過大評価が『届く/届かない』を分けた部分集合: {st['decisive']}")
        out.append(f"  ★その席数: {rep['n_seat_hit']}")
        return "\n".join(out)
    out.append(f"  会計が判断を変えた席: {len(rows)}")
    out.append(f"    うち funded が変わった席 : "
               f"{sum(1 for r in rows if r['funded_flip'])}")
    out.append(f"    うち reachable が変わった: "
               f"{sum(1 for r in rows if r['reach_flip'])}")
    out.append(f"    うち decoy が変わった席  : "
               f"{sum(1 for r in rows if r['decoy_flip'])}")
    out.append(f"    ★うち最上位手が入れ替わった席: "
               f"{sum(1 for r in rows if r['top_flip'])}")
    by = {}
    for r in rows:
        if r["top_flip"]:
            by[r["script"]] = by.get(r["script"], 0) + 1
    if by:
        out.append("  最上位手が動いた席の脚本内訳: "
                   + " ".join(f"{k}:{v}" for k, v in sorted(by.items())))
    for r in rows[:20]:
        if not r["top_flip"]:
            continue
        out.append(f"   - {r['script']}#{r['seed']} L{r['loop']}D{r['day']} "
                   f"({r['decision']}) dl={r['days_left']} "
                   f"{r['top0']} → {r['top1']}  [実選択 {r['chosen']}]")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--mode", type=str, default="exact",
                    choices=["exact", "flat1", "drop", "exact_drop",
                             "liveness", "subset"])
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    rep = sweep(args.days, args.loops, args.mode)
    print(summarize(rep))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
