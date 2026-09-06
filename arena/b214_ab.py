# -*- coding: utf-8 -*-
"""B-214 A/B・掃引ハーネス：複線演出（板へのダミー配置）の主指標と副指標を同時に測る。

★評価軸が通常と違う（発注書 §5）＝これは**脚本家側**の施策なので、主人公側ベンチの
防衛数が下がることは「主人公が弱くなった」ではなく**物差しが変わった**の意。
∴ §11b の作法を脚本家側に適用し、**行為の数え上げ**を主指標に置く：

  (i)   ダミーが実際に主人公の札を吸った席数（主人公が `暗躍禁止` をダミー板へ置いた回数）
  (ii)  主人公の板ガード命中率（`暗躍禁止→板` のうち、実際にその板へ暗躍札があった割合）
  (iii) 同ターンに通った本命の暗躍の枚数（`暗躍禁止` に消されなかった暗躍札）

副指標＝防衛数・平均ループ数・per-game の全数（flip 判定用）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b214_ab run --days 3 --decoy off
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b214_ab sweep --days 3 \
        --values 0,20,30,40,45,50,60,75,95,115
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

ANYAKU = frozenset({"暗躍+1", "暗躍+2"})
KINSHI = "暗躍禁止"


def _count_from_log(log: list[dict], acc: Counter) -> None:
    """1局の決定ログから主指標(i)(ii)(iii)を数える。"""
    turns: dict = defaultdict(lambda: {"mm": [], "pro": [], "pv": None})
    for d in log:
        if d.get("decision") != "set_card":
            continue
        k = (d.get("loop"), d.get("day"))
        if d.get("actor") == "mastermind":
            turns[k]["mm"].append(d["chosen"])
        else:
            turns[k]["pro"].append(d["chosen"])
            if turns[k]["pv"] is None:
                turns[k]["pv"] = d.get("view")
    acc["turns"] += len(turns)
    acc["mm_cards_set"] += sum(len(t["mm"]) for t in turns.values())
    for _k, t in turns.items():
        gen = {c.get("area") for c in ((t["pv"] or {}).get("characters") or [])
               if c.get("name") == "幻想" and c.get("alive") and c.get("area")}
        by: dict = defaultdict(list)
        for c in t["mm"]:
            by[(c["target_kind"], c["target"])].append(c["card"])
        kin = {(c["target_kind"], c["target"]) for c in t["pro"]
               if c.get("card") == KINSHI}
        # mm の板配置の内訳
        dummy_boards = set()
        for (kind, tgt), cards in by.items():
            if kind != "board":
                continue
            for card in cards:
                acc["mm_board_placements"] += 1
                if card in ANYAKU:
                    acc["mm_board_anyaku"] += 1
                elif tgt in gen:
                    acc["mm_board_gensou_real"] += 1
                else:
                    acc["mm_board_dummy"] += 1
                    acc[f"dummy_card:{card}"] += 1
                    acc[f"dummy_board:{tgt}"] += 1
                    dummy_boards.add(tgt)
        # (ii) 主人公の板ガードの命中／空振り
        for (kind, tgt) in kin:
            if kind != "board":
                acc["kinshi_char_total"] += 1
                continue
            acc["kinshi_board_total"] += 1
            cards_here = by.get(("board", tgt), [])
            if any(x in ANYAKU for x in cards_here):
                acc["kinshi_board_hit"] += 1
            elif tgt in dummy_boards:
                acc["kinshi_board_whiff_dummy"] += 1      # ★(i) ダミーが吸った席
            elif cards_here:
                acc["kinshi_board_whiff_gensou"] += 1
            else:
                acc["kinshi_board_whiff_empty"] += 1
        # (iii) 通った暗躍札／(i) の相方＝ダミーが吸ったターンに通った本命
        for (kind, tgt), cards in by.items():
            for card in cards:
                if card not in ANYAKU:
                    continue
                acc["mm_anyaku_total"] += 1
                if (kind, tgt) in kin:
                    acc["mm_anyaku_blocked"] += 1
                else:
                    acc["mm_anyaku_through"] += 1
        if dummy_boards & {t for k, t in kin if k == "board"}:
            acc["turns_dummy_sucked"] += 1
            acc["through_on_sucked_turns"] += sum(
                1 for (kind, tgt), cards in by.items() for card in cards
                if card in ANYAKU and (kind, tgt) not in kin)


def run_one(days: int, loops: int, decoy_on: bool, value: float | None,
            verbose: bool = False) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from sim.loop_race import analyze_script
    from arena.benchmark import benchmark_scripts

    old_flag = HeuristicMastermind.B214_BOARD_DECOY
    # ★2026-08-14：B-215（先置きダミー）が既定 ON になったので、**B-214 単独**を測る
    #   本ハーネスは B-215 を**明示的に False** に置く（規約§3-6＝他フラグの既定値に
    #   暗黙依存しない）。3条件（OFF／B-214／B-214+B-215）の比較は
    #   `arena/b215_ab.py` が単一ソース。
    old_b215 = HeuristicMastermind.B215_DECOY_FIRST
    HeuristicMastermind.B215_DECOY_FIRST = False
    HeuristicMastermind.B214_BOARD_DECOY = decoy_on
    params = None if value is None else {"b214_decoy_board": float(value)}
    t0 = time.time()
    rows, acc = [], Counter()
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            race = analyze_script(sc).verdict
            mm = HeuristicMastermind(seed, params=params)
            hp = HeuristicProtagonist(seed)
            state, log = run_game(replace(sc, loops=loops),
                                  {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
            fb = any(e.get("event") == "final_battle" for e in state.history)
            if state.winner == "protagonist" and not fb:
                ltw, outcome = state.loop_no, "defense"
            elif fb:
                ltw = loops + 1
                outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            else:
                ltw, outcome = loops + 1, "loss"
            rows.append({"script": name, "seed": seed, "loops_to_win": ltw,
                         "outcome": outcome, "race": race})
            _count_from_log(log, acc)
            if verbose:
                print(f"  {name} s{seed}: {ltw} {outcome}", flush=True)
    finally:
        HeuristicMastermind.B214_BOARD_DECOY = old_flag
        HeuristicMastermind.B215_DECOY_FIRST = old_b215
    vals = [r["loops_to_win"] for r in rows]
    defensible = [r["loops_to_win"] for r in rows if r["race"] != "mastermind"]
    return {
        "days": days, "loops": loops, "decoy": decoy_on, "value": value,
        "n_games": len(rows),
        "defense": sum(1 for r in rows if r["outcome"] == "defense"),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "mean": round(sum(vals) / len(vals), 3),
        "mean_defensible": round(sum(defensible) / len(defensible), 3),
        "distribution": {str(k): v for k, v in sorted(Counter(vals).items())},
        "acc": dict(acc), "rows": rows,
        "elapsed_sec": round(time.time() - t0, 1),
        "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
    }


def summary(r: dict) -> str:
    a = Counter(r["acc"])
    kb = a["kinshi_board_total"]
    hit = a["kinshi_board_hit"] / kb if kb else 0.0
    return (
        f"days={r['days']} decoy={'ON' if r['decoy'] else 'OFF'} value={r['value']}"
        f" | 防衛 {r['defense']}/{r['n_games']} 平均 {r['mean']}"
        f"（防衛可能のみ {r['mean_defensible']}）\n"
        f"    (i)  ダミーが吸った席 {a['kinshi_board_whiff_dummy']}"
        f"（ダミー総数 {a['mm_board_dummy']} 枚／板配置 {a['mm_board_placements']} 枚）\n"
        f"    (ii) 板ガード命中率 {hit:.1%}"
        f"（{a['kinshi_board_hit']}/{kb}／空振り: ダミー {a['kinshi_board_whiff_dummy']}"
        f"・幻想 {a['kinshi_board_whiff_gensou']}・空 {a['kinshi_board_whiff_empty']}）\n"
        f"    (iii) 暗躍 通過 {a['mm_anyaku_through']}/{a['mm_anyaku_total']}"
        f"（吸われたターンに通った本命 {a['through_on_sucked_turns']}"
        f"／吸ったターン数 {a['turns_dummy_sucked']}）\n"
        f"    所要 {r['elapsed_sec']}秒"
    )


def flips(base: dict, other: dict) -> list[str]:
    b = {(r["script"], r["seed"]): r["loops_to_win"] for r in base["rows"]}
    out = []
    for r in other["rows"]:
        k = (r["script"], r["seed"])
        if b.get(k) != r["loops_to_win"]:
            out.append(f"{k[0]}#{k[1]}: {b.get(k)} -> {r['loops_to_win']}"
                       f" [{r['outcome']}]")
    return out


def cmd_run(args) -> None:
    r = run_one(args.days, args.loops, args.decoy == "on",
                None if args.value is None else args.value, args.verbose)
    print(summary(r))
    if args.out:
        Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


def cmd_sweep(args) -> None:
    base = run_one(args.days, args.loops, False, None)
    print("=== ベースライン（OFF・同一コミット・同一セッション）===")
    print(summary(base))
    results = {"off": base, "on": {}}
    for v in [float(x) for x in args.values.split(",")]:
        r = run_one(args.days, args.loops, True, v)
        print(f"\n=== ON value={v} ===")
        print(summary(r))
        fl = flips(base, r)
        print(f"    per-game flip {len(fl)} 件"
              + ("" if not fl else "：" + " / ".join(fl[:40])))
        results["on"][str(v)] = r
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="B-214 A/B・掃引")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--loops", type=int, default=8)
    p.add_argument("--decoy", choices=("on", "off"), default="off")
    p.add_argument("--value", type=float, default=None)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=cmd_run)
    q = sub.add_parser("sweep")
    q.add_argument("--days", type=int, default=3)
    q.add_argument("--loops", type=int, default=8)
    q.add_argument("--values", default="0,20,30,40,45,50,60,75,95,115")
    q.add_argument("--out")
    q.set_defaults(func=cmd_sweep)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    args.func(args)


if __name__ == "__main__":
    main()
