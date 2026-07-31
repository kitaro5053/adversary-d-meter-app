# -*- coding: utf-8 -*-
"""B-103：板への「空振り暗躍禁止」の席数を数える計測ハーネス（本番経路は無変更）。

★何を数えるか＝**打っても算術的にゼロの暗躍禁止**。
KB 接地：
  - 暗躍禁止は「**重なった**暗躍+1/+2 を無効化」（`rules/10_cards_and_placement.md:61`）
    ＝**同じ対象に今ターン置かれた札**しか消せない。既にボードに載っている
    暗躍カウンターは除去しない。
  - 暗躍禁止は**行動解決フェイズでのみ**有効＝クロマク／不穏な噂／黒猫のループ開始／
    事件由来の暗躍は止まらない（`rules/10:65`・`rules/60:105 C-5`・`rules/40:62,88`）。
∴ **脚本家がそのターンにその板へ札を1枚も置いていない（void）** なら、
   その板への暗躍禁止が打ち消せる暗躍+ は**存在しえない**＝空振り（＝算術的にゼロ）。

★ただし例外が2つある（数えるときに区別する）：
  - **カルティスト無効化**：板に mm 札があってもカルティストがその板に居れば無視されうる
    （`rules/40:101`）＝void ではないが効かない可能性がある。ここでは数えない（推定が要る）。
  - **複数主人公の暗躍禁止は自滅**（`rules/10:61`・`60 C-10 ②`）＝同一ターンに2枚以上の
    暗躍禁止が置かれると全部無効。これは公開情報だけで確定できる**第2の空振り**
    ＝`self_destruct` として別に数える。

使い方:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.void_audit          # 3日級130局
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.void_audit --days 5 # 5日級70局
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.void_audit --json out.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game


import agents.heuristic_protagonist as _hp_mod

_builtin_max = max


class _VoidTally(HeuristicProtagonist):
    """挙動は本体と完全同一。`decide` の返り値と `view` を見て空振り席を数えるだけ。

    ★スコアの捕捉は `agents.debug.ProbedProtagonist` と同じ仕掛け（モジュール
    グローバル `max` の一時差し替え）。`max` 自体は builtin へ委譲＝**選択は不変**。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rows: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        cap: dict = {}

        def spymax(*args, **kw):
            if (args and isinstance(args[0], list) and "key" in kw
                    and "scored" not in cap):
                key = kw["key"]
                cap["scored"] = sorted(((key(o), o) for o in args[0]),
                                       key=lambda x: -x[0])
            return _builtin_max(*args, **kw)

        had = "max" in _hp_mod.__dict__
        prev = _hp_mod.__dict__.get("max")
        _hp_mod.max = spymax
        try:
            chosen = super().decide(view, decision, options)
        finally:
            if had:
                _hp_mod.max = prev
            else:
                del _hp_mod.max
        if decision == "set_card" and chosen.get("card") == "暗躍禁止":
            pl = view.get("placements", [])
            mm_board = {p["target"] for p in pl
                        if p.get("owner") == "mastermind"
                        and p.get("target_kind") == "board"}
            mm_char = {p["target"] for p in pl
                       if p.get("owner") == "mastermind"
                       and p.get("target_kind") == "character"}
            # 既に置かれた（＝先の席の）暗躍禁止の枚数（自滅ルールの判定用）
            prior_void_cards = sum(
                1 for p in pl
                if p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止")
            tgt, kind = chosen.get("target"), chosen.get("target_kind")
            covered = (tgt in mm_board) if kind == "board" else (tgt in mm_char)
            scored = cap.get("scored", [])
            top = scored[0][0] if scored else None
            # 「もし空振り札を捨てたら次に選ばれる手」＝機会費用の実測
            nxt = None
            for s, o in scored:
                if not (o.get("card") == chosen.get("card")
                        and o.get("target") == tgt
                        and o.get("target_kind") == kind):
                    nxt = (round(s, 2), o.get("card"), o.get("target"))
                    break
            self.rows.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"), "kind": kind, "target": tgt,
                "void": not covered,
                "score": round(top, 2) if top is not None else None,
                "next": nxt,
                "board_anyaku": dict(view.get("board_anyaku", {})).get(tgt, 0)
                if kind == "board" else None,
                "self_destruct": prior_void_cards >= 1,
            })
        return chosen


def audit_game(script, seed: int, loops: int = 8) -> tuple[str, list[dict]]:
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = _VoidTally(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome = "defense"
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        outcome = "loss"
    return outcome, hp.rows


def run(days: int = 3, loops: int = 8, verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts
    total = Counter()
    per_script: list[dict] = []
    by_board = Counter()
    for name, seed, sc in benchmark_scripts(days=days):
        outcome, rows = audit_game(sc, seed, loops=loops)
        b = [r for r in rows if r["kind"] == "board"]
        v = [r for r in b if r["void"]]
        sd = [r for r in rows if r["self_destruct"]]
        total["board_seats"] += len(b)
        total["board_void"] += len(v)
        total["char_seats"] += len(rows) - len(b)
        total["self_destruct"] += len(sd)
        for r in v:
            by_board[r["target"]] += 1
            total[f"score_{r['score']}"] += 1
        per_script.append({"script": name, "seed": seed, "outcome": outcome,
                           "board": len(b), "void": len(v),
                           "void_rows": v})
        if verbose:
            print(f"  {name} s{seed}: board={len(b)} void={len(v)} [{outcome}]",
                  flush=True)
    return {"days": days, "totals": dict(total),
            "void_by_board": dict(by_board), "per_script": per_script}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    res = run(days=a.days, loops=a.loops, verbose=a.verbose)
    t = res["totals"]
    print(f"== 空振り監査（{a.days}日級） ==")
    print(f"板への暗躍禁止   : {t['board_seats']} 席")
    print(f"うち void（空振り）: {t['board_void']} 席 "
          f"({100.0 * t['board_void'] / max(1, t['board_seats']):.1f}%)")
    print(f"キャラへの暗躍禁止 : {t['char_seats']} 席")
    print(f"自滅（同ターン2枚目以降）: {t['self_destruct']} 席")
    print("void の板別:", dict(sorted(res["void_by_board"].items(),
                                      key=lambda kv: -kv[1])))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
