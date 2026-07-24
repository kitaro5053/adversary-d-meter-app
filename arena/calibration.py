# -*- coding: utf-8 -*-
"""belief（可能世界追跡）の較正監査 — 推理の解像度を数字にする。

主人公AIの敗因の多く（ML未特定で供給を読み違える・犯人候補が広くて冷却が
誤照準する）は belief の解像度の問題。ここでは標準ベンチのリプレイから、
各ループ・各日の belief 出力を真実（script.roles/incidents）と突き合わせて集計する：

1. 犯人照準: 事件日 d の犯人候補集合に真犯人が入っている率／集合サイズ／単独確定率
2. 役職特定: 真のキラー/クロマク/カルティスト/SK/ミスリーダー/フレンドに割り当てた
   周辺確率の平均（ループ番号別）＝「何ループ目でどこまで見えているか」の較正曲線

使い方:
    PYTHONHASHSEED=0 python -m arena.calibration                # 3日級130局
    PYTHONHASHSEED=0 python -m arena.calibration --days 5       # 5日級70局
    PYTHONHASHSEED=0 python -m arena.calibration --mm-params arena/params/mm_cem_gen2_20260709.json

出力は表テキスト（docsへの転記用）。改善対象の選定材料（どの推理を強化すると
どのゲート（悲観供給・照準冷却・分離）が効き始めるか）に使う。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game

ROLES_OF_INTEREST = ("キラー", "クロマク", "カルティスト", "シリアルキラー",
                     "ミスリーダー", "フレンド", "キーパーソン")


class _CalibratedProtagonist(HeuristicProtagonist):
    """decide のたびに belief 出力を記録する（挙動同一・観測のみ）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.calib: list[dict] = []
        self._seen: set = set()

    def decide(self, view, decision, options):
        chosen = super().decide(view, decision, options)
        key = (view.get("loop"), view.get("day"))
        if decision == "set_card" and key not in self._seen:
            self._seen.add(key)   # 3席で1回だけ記録
            marg = self._belief.role_marginals()
            self.calib.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "culprit_cands": {d: sorted(s) for d, s in
                                  getattr(self, "_culprit_cands", {}).items()},
                "marginals": {n: {r: round(d.get(r, 0.0), 3)
                                  for r in ROLES_OF_INTEREST if d.get(r, 0) > 0}
                              for n, d in marg.items()},
            })
        return chosen


def audit_game(script, seed: int, loops: int = 8,
               mm_params: dict | None = None) -> list[dict]:
    """1ゲームを回し、(記録, 真実) を突き合わせた行を返す。"""
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed, params=mm_params)
    hp = _CalibratedProtagonist(seed)
    run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    truth_roles = {r: [n for n, ro in script.roles.items() if ro == r]
                   for r in ROLES_OF_INTEREST}
    cast = list(script.cast)
    incidents = {i.day: i.culprit for i in script.incidents}
    rows = []
    for rec in hp.calib:
        row = {"loop": rec["loop"], "day": rec["day"]}
        # 犯人照準（未来の事件日だけ＝過ぎた事件の照準は無意味）
        aim = {}
        for d, culp in incidents.items():
            if d < rec["day"]:
                continue
            cands = rec["culprit_cands"].get(d, [])
            aim[d] = {"hit": culp in cands, "size": len(cands),
                      "unique_hit": cands == [culp]}
        row["aim"] = aim
        # 役職特定（真の担い手に割り当てた周辺確率）
        probs = {}
        for r, holders in truth_roles.items():
            for h in holders:
                probs.setdefault(r, []).append(
                    rec["marginals"].get(h, {}).get(r, 0.0))
        row["probs"] = {r: sum(v) / len(v) for r, v in probs.items() if v}
        # ★Brier（適正スコア則・低いほど良い）：役職 r を「cast上の確率分類」とみなし、
        #   Σ_c (p(c=r) − 1{c=真の担い手})² を全キャラで取る。解像度（真への確率）だけでなく
        #   過信（偽担い手への確率上昇）も罰する＝ソフト層の較正が「鋭利化」でなく「改善」かを判定。
        brier = {}
        for r, holders in truth_roles.items():
            if len(holders) != 1:
                continue   # 単独役職のみ（キラー/クロマク/ML/カルティスト/SK/フレンド/KP）
            h = holders[0]
            b = 0.0
            for c in cast:
                p = rec["marginals"].get(c, {}).get(r, 0.0)
                b += (p - (1.0 if c == h else 0.0)) ** 2
            brier[r] = b
        row["brier"] = brier
        rows.append(row)
    return rows


def run_audit(days: int = 3, loops: int = 8, mm_params: dict | None = None,
              verbose: bool = True) -> dict:
    from arena.benchmark import benchmark_scripts
    # ループ番号別の集計
    aim_hit = defaultdict(list)      # loop -> [bool]（事件日照準の的中）
    aim_size = defaultdict(list)     # loop -> [int]
    aim_unique = defaultdict(list)   # loop -> [bool]
    role_p = defaultdict(lambda: defaultdict(list))   # role -> loop -> [p]
    role_brier = defaultdict(lambda: defaultdict(list))  # role -> loop -> [brier]
    games_at_loop = defaultdict(int)  # loop -> その局に到達した局数（生存者バイアス可視化）
    n_games = 0
    for name, seed, sc in benchmark_scripts(days=days):
        n_games += 1
        rows_g = audit_game(sc, seed, loops=loops, mm_params=mm_params)
        for lp in {r["loop"] for r in rows_g}:
            games_at_loop[lp] += 1
        for row in rows_g:
            lp = row["loop"]
            for d, a in row["aim"].items():
                aim_hit[lp].append(a["hit"])
                aim_size[lp].append(a["size"])
                aim_unique[lp].append(a["unique_hit"])
            for r, p in row["probs"].items():
                role_p[r][lp].append(p)
            for r, bval in row.get("brier", {}).items():
                role_brier[r][lp].append(bval)
        if verbose and n_games % 20 == 0:
            print(f"  ... {n_games}局", file=sys.stderr, flush=True)

    report = {"days": days, "n_games": n_games, "loops": loops}
    lines = [f"belief較正監査: {days}日級 {n_games}局 loops={loops} "
             f"mm={'tuned' if mm_params else '既定'}",
             "",
             "■ 犯人照準（未来の事件日の候補集合）: ループ別",
             "  loop | 的中率 | 単独確定率 | 平均候補数"]
    for lp in sorted(aim_hit):
        h = aim_hit[lp]; u = aim_unique[lp]; s = aim_size[lp]
        lines.append(f"  L{lp}   | {sum(h)/len(h):5.1%} | {sum(u)/len(u):5.1%}      "
                     f"| {sum(s)/len(s):.2f}")
    lines += ["", "■ 役職特定（真の担い手への周辺確率の平均）: ループ別",
              "  ★到達局数（生存者バイアス注意＝易しい脚本は早く勝って退出し、後半ループの平均は"
              "難脚本に偏る。ループ跨ぎの低下は劣化でなく標本の入れ替わりのことが多い）:"]
    loops_seen = sorted({lp for r in role_p.values() for lp in r})
    header = "  役職            | " + " | ".join(f"L{lp}" for lp in loops_seen)
    lines.append("  到達局数        | "
                 + " | ".join(f"{games_at_loop.get(lp, 0):>2}" for lp in loops_seen))
    lines.append(header)
    for r in ROLES_OF_INTEREST:
        if r not in role_p:
            continue
        cells = []
        for lp in loops_seen:
            v = role_p[r].get(lp)
            cells.append(f"{sum(v)/len(v):.2f}" if v else "  - ")
        lines.append(f"  {r:<14}| " + " | ".join(cells))
    lines += ["", "■ Brier（役職特定の適正スコア＝低いほど良い・過信も罰する）: ループ別"]
    lines.append(header)
    for r in ROLES_OF_INTEREST:
        if r not in role_brier:
            continue
        cells = []
        for lp in loops_seen:
            v = role_brier[r].get(lp)
            cells.append(f"{sum(v)/len(v):.2f}" if v else "  - ")
        lines.append(f"  {r:<14}| " + " | ".join(cells))
    report["text"] = "\n".join(lines)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="belief較正監査")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--mm-params", type=str, default=None)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    mm_params = None
    if args.mm_params:
        with open(args.mm_params, encoding="utf-8") as f:
            mm_params = json.load(f).get("params")
    rep = run_audit(days=args.days, loops=args.loops, mm_params=mm_params)
    print(rep["text"])


if __name__ == "__main__":
    main()
