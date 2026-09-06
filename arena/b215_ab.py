# -*- coding: utf-8 -*-
"""B-215 A/B・掃引ハーネス：**先置きダミー**（席順の入れ替え）を B-214 単独と比較する。

`arena/b214_ab.py` の主指標 (i)(ii)(iii) をそのまま引き継ぎ、B-215 の発注書が要求する
2つの軸を足す：

  ★A. **3条件の比較**＝(1) B-214 OFF / (2) B-214 ON / (3) B-214+B-215 ON
     （加えて (0) B-215 の対照＝`b215_decoy_first=0`＝候補は開くが一度も入れ替えない）。
  ★B. **`暗躍+2` が守られる価値の分離**（B-214 弱点3）＝1ループ1回の切り札が
     `暗躍禁止` に潰される率を **単独で** 数える。per-turn のスループット (iii) には
     現れない量なので、これを別枠で出さないと「(iii) が動かない理由」を分離できない。
  ★C. **複線ターン**＝同ターンに「ダミー板」と「本命の板暗躍」が**両方**あるターン数
     （＝B-214 が構造的に作れなかった形。主人公の板ガードが2択になる）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b215_ab cond --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b215_ab sweep --days 3 \
        --values 0,60,80,95,105,110,130,200
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
PLUS2 = "暗躍+2"
KINSHI = "暗躍禁止"


def _count_from_log(log: list[dict], acc: Counter) -> None:
    """1局の決定ログから主指標を数える（b214_ab の計数＋B-215 の追加軸）。"""
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
        dummy_boards, anyaku_boards = set(), set()
        for (kind, tgt), cards in by.items():
            if kind != "board":
                continue
            for card in cards:
                acc["mm_board_placements"] += 1
                if card in ANYAKU:
                    acc["mm_board_anyaku"] += 1
                    anyaku_boards.add(tgt)
                elif tgt in gen:
                    acc["mm_board_gensou_real"] += 1
                else:
                    acc["mm_board_dummy"] += 1
                    acc[f"dummy_card:{card}"] += 1
                    acc[f"dummy_board:{tgt}"] += 1
                    dummy_boards.add(tgt)
        # ★C 複線ターン＝ダミー板と本命の板暗躍が同ターンに両方ある
        if dummy_boards and anyaku_boards:
            acc["turns_two_board_lines"] += 1
            if any(("board", b) in kin for b in dummy_boards):
                acc["two_line_guard_to_dummy"] += 1      # 主人公が囮側を選んだ
            elif any(("board", b) in kin for b in anyaku_boards):
                acc["two_line_guard_to_real"] += 1       # 本命側を選ばれた
            else:
                acc["two_line_guard_elsewhere"] += 1     # 板を守らなかった
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
        # (iii) 通った暗躍札／★B 暗躍+2 の被ブロックを単独で数える
        for (kind, tgt), cards in by.items():
            for card in cards:
                if card not in ANYAKU:
                    continue
                acc["mm_anyaku_total"] += 1
                blocked = (kind, tgt) in kin
                acc["mm_anyaku_blocked" if blocked else "mm_anyaku_through"] += 1
                if card == PLUS2:
                    acc["plus2_total"] += 1
                    acc["plus2_blocked" if blocked else "plus2_through"] += 1
                    acc[f"plus2_{kind}_total"] += 1
                    if blocked:
                        acc[f"plus2_{kind}_blocked"] += 1
                else:
                    acc["plus1_total"] += 1
                    acc["plus1_blocked" if blocked else "plus1_through"] += 1
                # 板暗躍だけの被ブロック（複線の直接の受益者）
                if kind == "board":
                    acc["board_anyaku_total"] += 1
                    if blocked:
                        acc["board_anyaku_blocked"] += 1
        if dummy_boards & {t for k, t in kin if k == "board"}:
            acc["turns_dummy_sucked"] += 1
            acc["through_on_sucked_turns"] += sum(
                1 for (kind, tgt), cards in by.items() for card in cards
                if card in ANYAKU and (kind, tgt) not in kin)


def run_one(days: int, loops: int, b214: bool, b215: bool,
            v214: float | None = None, v215: float | None = None,
            verbose: bool = False) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from sim.loop_race import analyze_script
    from arena.benchmark import benchmark_scripts

    old14 = HeuristicMastermind.B214_BOARD_DECOY
    old15 = HeuristicMastermind.B215_DECOY_FIRST
    HeuristicMastermind.B214_BOARD_DECOY = b214
    HeuristicMastermind.B215_DECOY_FIRST = b215
    params = {}
    if v214 is not None:
        params["b214_decoy_board"] = float(v214)
    if v215 is not None:
        params["b215_decoy_first"] = float(v215)
    t0 = time.time()
    rows, acc, swaps = [], Counter(), Counter()
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            race = analyze_script(sc).verdict
            mm = HeuristicMastermind(seed, params=params or None)
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
            swaps.update(mm._b215_stats)
            if verbose:
                print(f"  {name} s{seed}: {ltw} {outcome}", flush=True)
    finally:
        HeuristicMastermind.B214_BOARD_DECOY = old14
        HeuristicMastermind.B215_DECOY_FIRST = old15
    vals = [r["loops_to_win"] for r in rows]
    defensible = [r["loops_to_win"] for r in rows if r["race"] != "mastermind"]
    return {
        "days": days, "loops": loops, "b214": b214, "b215": b215,
        "v214": v214, "v215": v215, "n_games": len(rows),
        "defense": sum(1 for r in rows if r["outcome"] == "defense"),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "mean": round(sum(vals) / len(vals), 3),
        "mean_defensible": round(sum(defensible) / len(defensible), 3),
        "distribution": {str(k): v for k, v in sorted(Counter(vals).items())},
        "acc": dict(acc), "swaps": dict(swaps), "rows": rows,
        "elapsed_sec": round(time.time() - t0, 1),
        "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
    }


def _pct(n, d):
    return f"{n/d:.1%}" if d else "—"


def summary(r: dict) -> str:
    a = Counter(r["acc"])
    s = Counter(r["swaps"])
    kb = a["kinshi_board_total"]
    T = max(a["turns"], 1)
    return (
        f"days={r['days']} B214={'ON' if r['b214'] else 'OFF'}({r['v214']})"
        f" B215={'ON' if r['b215'] else 'OFF'}({r['v215']})"
        f" | 防衛 {r['defense']}/{r['n_games']} 平均 {r['mean']}"
        f"（防衛可能のみ {r['mean_defensible']}）\n"
        f"    (i)   ダミーが吸った席 {a['kinshi_board_whiff_dummy']}"
        f"（ダミー {a['mm_board_dummy']} 枚／板配置 {a['mm_board_placements']} 枚"
        f"／席順入れ替え {s['swap']} 回）\n"
        f"    (ii)  板ガード命中率 {_pct(a['kinshi_board_hit'], kb)}"
        f"（{a['kinshi_board_hit']}/{kb}／空振り: ダミー {a['kinshi_board_whiff_dummy']}"
        f"・幻想 {a['kinshi_board_whiff_gensou']}・空 {a['kinshi_board_whiff_empty']}）\n"
        f"    (iii) 暗躍 通過 {a['mm_anyaku_through']}/{a['mm_anyaku_total']}"
        f"＝{a['mm_anyaku_through']/T:.3f}/ターン"
        f"（置いた {a['mm_anyaku_total']/T:.3f}/ターン）\n"
        f"    ★B 暗躍+2 被ブロック {a['plus2_blocked']}/{a['plus2_total']}"
        f"＝{_pct(a['plus2_blocked'], a['plus2_total'])}"
        f"（+1 は {a['plus1_blocked']}/{a['plus1_total']}"
        f"＝{_pct(a['plus1_blocked'], a['plus1_total'])}）"
        f"／板暗躍 被ブロック {a['board_anyaku_blocked']}/{a['board_anyaku_total']}"
        f"＝{_pct(a['board_anyaku_blocked'], a['board_anyaku_total'])}\n"
        f"    ★C 複線ターン {a['turns_two_board_lines']}"
        f"（囮へ誘導 {a['two_line_guard_to_dummy']}"
        f"／本命を当てられた {a['two_line_guard_to_real']}"
        f"／板を守らず {a['two_line_guard_elsewhere']}）\n"
        f"    所要 {r['elapsed_sec']}秒 turns={a['turns']}"
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


CONDS = {
    #  名前            b214,  b215
    "off":            (False, False),
    "b214":           (True,  False),
    "b215only":       (False, True),
    "both":           (True,  True),
}


def cmd_cond(args) -> None:
    """★3条件（＋対照）を同一セッションで連続測定する。"""
    names = args.conds.split(",")
    results, base = {}, None
    for nm in names:
        b214, b215 = CONDS[nm]
        r = run_one(args.days, args.loops, b214, b215,
                    args.v214 if b214 else None,
                    (args.v215 if args.v215 is not None else None) if b215 else None)
        results[nm] = r
        if base is None:
            base = r
        print(f"\n=== 条件 {nm} ===")
        print(summary(r))
        if r is not base:
            fl = flips(base, r)
            print(f"    per-game flip（vs {names[0]}）{len(fl)} 件"
                  + ("" if not fl else "：" + " / ".join(fl)))
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


def cmd_sweep(args) -> None:
    """B-215 の点（`b215_decoy_first`）の掃引。ベースライン＝B-214 単独（同一セッション実測）。"""
    base = run_one(args.days, args.loops, True, False, args.v214, None)
    print("=== ベースライン＝B-214 単独 ON（同一コミット・同一セッション）===")
    print(summary(base))
    results = {"b214": base, "both": {}}
    for v in [float(x) for x in args.values.split(",")]:
        r = run_one(args.days, args.loops, True, True, args.v214, v)
        print(f"\n=== B-214+B-215 ON  b215_decoy_first={v} ===")
        print(summary(r))
        fl = flips(base, r)
        print(f"    per-game flip（vs B-214 単独）{len(fl)} 件"
              + ("" if not fl else "：" + " / ".join(fl)))
        results["both"][str(v)] = r
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="B-215 A/B・掃引")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cond")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--loops", type=int, default=8)
    p.add_argument("--conds", default="off,b214,both")
    p.add_argument("--v214", type=float, default=45.0)
    p.add_argument("--v215", type=float, default=None)
    p.add_argument("--out")
    p.set_defaults(func=cmd_cond)
    q = sub.add_parser("sweep")
    q.add_argument("--days", type=int, default=3)
    q.add_argument("--loops", type=int, default=8)
    q.add_argument("--v214", type=float, default=45.0)
    q.add_argument("--values", default="0,60,80,95,105,110,130,200")
    q.add_argument("--out")
    q.set_defaults(func=cmd_sweep)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    args.func(args)


if __name__ == "__main__":
    main()
