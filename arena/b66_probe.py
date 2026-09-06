# -*- coding: utf-8 -*-
"""B-66(2) 再設計 Phase 0：「囮（実証ゼロ）」判定の**誤判定率**を公開情報だけで数える読み取り専用プローブ。

★agents/ の判断経路・既定値には一切触れない（読むだけ／挙動 bit 不変）。

背景（バックログ §68-14 の申し送り）＝`_b66_unproven_from_history` は
**ループ終了時カウンター（`loop_board`）だけ**で「その板は敗北の原因ではなかった＝囮の公算」と判定し、
`ボード封じ_mm札`(96) を `ボード封じ_未実証`(74.5) へ減点する。しかし終了時 <2 には
少なくとも3つの別々の理由があり、**終了時カウンターだけでは見分けがつかない**：

  (A) mm がその板に札を1枚も投じていない          … 本当に囮／狙われていない
  (B) mm は投じたが**主人公が阻止した**            … 防御が効いた結果＝囮ではない（★誤判定）
  (C) mm は投じて通ったが臨界(2)に届かなかった      … 投資不足（灰色）

(A)(B)(C) はいずれも**公開情報だけで区別できる**＝`cards_revealed`（毎ターン全6枚が公開＝
`sim/flow.py:204`）に mm の板への暗躍札が載り、行動解決フェイズの `anyaku` イベント
（`sim/flow.py:245`）に**実際に乗った分**が載る。差＝阻止された分。

サブコマンド:
    python -m arena.b66_probe census --days {3,5}
        # ベンチ全局で「囮と判定された板」を (A)(B)(C) に分類し、誤判定率を出す
    python -m arena.b66_probe seats --days {3,5}
        # 減点が実際に効いた席（`tgt in _b66_unproven_boards` が True を返した席）の数と内訳

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

_AREAS = ("病院", "神社", "都市", "学校")
_ANYAKU_CARDS = {"暗躍+1": 1, "暗躍+2": 2}


# ---------------------------------------------------------------------------
# 公開情報からの「板×ループ」会計（本プローブの核）
# ---------------------------------------------------------------------------
def board_loop_ledger(view: dict, area: str, loop: int) -> dict:
    """公開情報だけで (loop, area) の**カード会計**を作る。

    返り値＝{"placed": mm が投じた暗躍札の合計値, "landed": 行動解決フェイズで実際に乗った量,
             "blocked": placed-landed（>0＝阻止された）, "days_placed": 投札日数,
             "days_blocked": 阻止できた日数}
    """
    placed = 0
    landed = 0
    days_placed: set = set()
    days_blocked: set = set()
    per_day_placed: dict = {}
    per_day_landed: dict = {}
    for e in view.get("history", []) or []:
        if e.get("loop") != loop:
            continue
        if e.get("event") == "cards_revealed":
            for p in e.get("placements", []) or []:
                if (p.get("owner") == "mastermind"
                        and p.get("target_kind") == "board"
                        and p.get("target") == area
                        and p.get("card") in _ANYAKU_CARDS):
                    v = _ANYAKU_CARDS[p["card"]]
                    placed += v
                    days_placed.add(e.get("day"))
                    per_day_placed[e.get("day")] = per_day_placed.get(e.get("day"), 0) + v
        elif (e.get("event") == "anyaku" and e.get("phase") == "action_resolution"
                and e.get("target") == area):
            d = int(e.get("delta", 0) or 0)
            if d > 0:
                landed += d
                per_day_landed[e.get("day")] = per_day_landed.get(e.get("day"), 0) + d
    for day, v in per_day_placed.items():
        if v > per_day_landed.get(day, 0):
            days_blocked.add(day)
    return {"placed": placed, "landed": landed, "blocked": max(0, placed - landed),
            "days_placed": len(days_placed), "days_blocked": len(days_blocked)}


def classify(view: dict, area: str, loops: list) -> str:
    """観測済み敗北ループ全体での板の分類＝'A_未投資' / 'B_阻止' / 'C_投資不足'。"""
    tot = {"placed": 0, "blocked": 0}
    for lp in loops:
        led = board_loop_ledger(view, area, lp)
        tot["placed"] += led["placed"]
        tot["blocked"] += led["blocked"]
    if tot["placed"] == 0:
        return "A_未投資"
    if tot["blocked"] > 0:
        return "B_阻止"
    return "C_投資不足"


def _defeat_loops(view: dict) -> list:
    """`_b66_unproven_from_history` が見ている敗北ループ集合（同じ述語）。"""
    hist = view.get("history", []) or []
    defeat_lps = {e.get("loop") for e in hist
                  if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}
    bd_end = {e.get("loop") for e in hist if e.get("event") == "loop_board"}
    return sorted(lp for lp in defeat_lps if lp in bd_end)


# ---------------------------------------------------------------------------
# census：ベンチ全局で「囮と判定された板」を分類する
# ---------------------------------------------------------------------------
def cmd_census(args) -> int:
    import agents.heuristic_protagonist as hpm
    from arena.benchmark import benchmark_scripts, loops_to_win

    inner = hpm.HeuristicProtagonist._b66_unproven_from_history
    cur = {"n": None}
    #: (局, 板, 分類) -> ターン数
    turns: Counter = Counter()
    #: (局, 板) -> 分類（最後に観測されたもの）
    pairs: dict = {}
    n_turn_fire = 0
    n_turn_all = 0

    def probing(view):
        nonlocal n_turn_fire, n_turn_all
        r = inner(view)
        n_turn_all += 1
        if r:
            n_turn_fire += 1
            lps = _defeat_loops(view)
            for b in sorted(r):
                cl = classify(view, b, lps)
                turns[(cur["n"], b, cl)] += 1
                pairs[(cur["n"], b)] = cl
        return r

    hpm.HeuristicProtagonist._b66_unproven_from_history = staticmethod(probing)
    try:
        for name, seed, script in benchmark_scripts(days=args.days):
            cur["n"] = f"{name}#{seed}"
            loops_to_win(script, seed=seed, loops=8)
    finally:
        hpm.HeuristicProtagonist._b66_unproven_from_history = staticmethod(inner)

    by_cls = Counter(cl for cl in pairs.values())
    by_cls_turn: Counter = Counter()
    for (_g, _b, cl), n in turns.items():
        by_cls_turn[cl] += n
    print(f"===== B-66(2) 囮判定の分類（{args.days}日級） =====")
    print(f"  turn 評価回数={n_turn_all}  うち囮集合が非空={n_turn_fire}")
    print(f"  ユニーク (局,板) = {len(pairs)}")
    for cl in ("A_未投資", "B_阻止", "C_投資不足"):
        n = by_cls[cl]
        pct = 100.0 * n / max(1, len(pairs))
        print(f"    {cl}: {n} ({pct:.1f}%)   [turn×板 = {by_cls_turn[cl]}]")
    print("  --- B_阻止（★誤判定）の内訳 ---")
    for (g, b), cl in sorted(pairs.items()):
        if cl == "B_阻止":
            print(f"    {g}  板={b}")
    if args.verbose:
        print("  --- C_投資不足 の内訳 ---")
        for (g, b), cl in sorted(pairs.items()):
            if cl == "C_投資不足":
                print(f"    {g}  板={b}")
    return 0


# ---------------------------------------------------------------------------
# seats：減点が実際に問い合わせられた席を数える（set の __contains__ を計測）
# ---------------------------------------------------------------------------
class _CountingSet(frozenset):
    """`tgt in _b66_unproven_boards` の True 問い合わせを記録する frozenset（値は同一）。"""
    sink: list = []

    def __contains__(self, x):  # noqa: D105
        r = frozenset.__contains__(self, x)
        if r:
            type(self).sink.append(x)
        return r


def cmd_seats(args) -> int:
    import agents.heuristic_protagonist as hpm
    from arena.benchmark import benchmark_scripts, loops_to_win

    inner = hpm.HeuristicProtagonist._b66_unproven_from_history
    cur = {"n": None, "view": None}
    hits: Counter = Counter()          # (局, 板, 分類) -> 問い合わせ True 回数

    def _drain():
        """直前ターンの問い合わせを、そのターンの view で分類して確定する。"""
        if not _CountingSet.sink:
            _CountingSet.sink = []
            return
        view = cur["view"] or {}
        lps = _defeat_loops(view)
        for b in _CountingSet.sink:
            hits[(cur["n"], b, classify(view, b, lps))] += 1
        _CountingSet.sink = []

    def probing(view):
        _drain()               # ★先に前ターン分を確定してから sink を空にする
        r = inner(view)
        cur["view"] = view
        return _CountingSet(r) if r else r

    hpm.HeuristicProtagonist._b66_unproven_from_history = staticmethod(probing)
    try:
        for name, seed, script in benchmark_scripts(days=args.days):
            cur["n"] = f"{name}#{seed}"
            _CountingSet.sink = []
            loops_to_win(script, seed=seed, loops=8)
            _drain()
    finally:
        hpm.HeuristicProtagonist._b66_unproven_from_history = staticmethod(inner)

    by_cls: Counter = Counter()
    for (_g, _b, cl), n in hits.items():
        by_cls[cl] += n
    tot = sum(by_cls.values())
    print(f"===== B-66(2) 減点の問い合わせ席（{args.days}日級） =====")
    print(f"  合計 True 問い合わせ={tot}")
    for cl in ("A_未投資", "B_阻止", "C_投資不足"):
        print(f"    {cl}: {by_cls[cl]} ({100.0*by_cls[cl]/max(1,tot):.1f}%)")
    print("  --- 局別（B_阻止のみ）---")
    for (g, b, cl), n in sorted(hits.items()):
        if cl == "B_阻止":
            print(f"    {g}  板={b}  ×{n}")
    if args.verbose:
        print("  --- 局別（全分類）---")
        print(json.dumps({f"{g}|{b}|{cl}": n for (g, b, cl), n in sorted(hits.items())},
                         ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena.b66_probe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("census", cmd_census), ("seats", cmd_seats)):
        p = sub.add_parser(name)
        p.add_argument("--days", type=int, default=3)
        p.add_argument("--verbose", action="store_true")
        p.set_defaults(func=fn)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
