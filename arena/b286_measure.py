# -*- coding: utf-8 -*-
"""B-286〔読まれた線から降りる〕の測定ハーネス（計測のみ・本番経路は無変更）。

★対局と分類は **B-282 の分類器をそのまま**呼ぶ（`arena.b282_classify.sweep`）＝
「折られた」の判定の単一ソースを共有する（§72-114 の指示）。本モジュールが足すのは
(1) `HeuristicMastermind.B286_LINE_SWITCH` の ON/OFF 切替（クラス属性・掃引口）
(2) B-286 専用の数え上げ（事前登録＝`agents/heuristic.b286_line_signals` docstring）：
    - 第1層(i) **ループ内再置席**＝防衛されたループの nullified 席（b282 の
      `nullified_seats`）を (kind,target) で束ね、各束の2席目以降を数える
      （＝「折られた席と同一対象への再置がまた折られた席」）。基準＝3日級130／5日級139。
    - 第1層(ii) **s1a 該当ループ数**（未使用拮抗線を残した負けループ）。基準＝104／38。
    - 第2層 **脚本家が取ったループ総数** Σ(min(ltw,9)−1)。
      基準＝3日級 id285/rev250/h1225/rot1288・5日級 id197/rev174/h1224/rot1201。
(3) 検問4＝実戦棋譜のリプレイ再採点（`scan_log`）：記録局の脚本家 set_card 局面を
    再構成し、「折られた線への再置」だった手が ON で乗り換わるかを見る。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b286_measure \
        --days 3 --perm id --mode on --json out.json
    ... --mode off                       # ベースライン（OFF）
    ... --penalty 90                     # 掃引（B286_REPLACE_PENALTY の上書き）
    ... --log docs/feedback_logs/xxx.jsonl   # 検問4（リプレイ再採点）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
# B-286 専用の数え上げ（b282 の JSON 行から導出＝対局を増やさない）
# ---------------------------------------------------------------------------
def replace_seats(rep: dict, include_decoy: bool = False) -> int:
    """第1層(i)＝ループ内再置席。

    防衛されたループ（各負け局に1つ）の nullified 席を (kind,target) で束ね、
    各束の **2席目以降**（＝一度折られた対象への再置がまた折られた席）を合計する。
    b282 の `nullified_seats` は null_rel＋null_decoy の順で入っている＝
    既定ではダミー板（null_decoy）を含めない（複線演出は「折られた」ではない）。
    """
    n = 0
    for g in rep["games"]:
        row = g.get("b282")
        if not row:
            continue
        seats = row.get("nullified_seats") or []
        if not include_decoy:
            # null_rel の判定を再現：キャラ席は常に対象・板席は「勝ち筋の板」のみ。
            # b282 行には relevant boards が直接載らないため、シグナルの実数から
            # null_rel の件数を取り、リストの先頭から同数を採る（生成順＝rel→decoy）。
            k = row["signals"]["s2ii_null_relevant"]
            seats = seats[:k]
        groups = Counter((s["kind"], s["target"]) for s in seats)
        n += sum(c - 1 for c in groups.values() if c >= 2)
    return n


def s1a_loops(rep: dict) -> int:
    """第1層(ii)＝s1a（未使用拮抗線を残した負けループ）の該当ループ数。"""
    return sum(1 for g in rep["games"]
               if g.get("b282") and g["b282"]["signals"]["s1a"] >= 1)


def mm_loops_total(rep: dict) -> int:
    """第2層＝脚本家が取ったループ総数 Σ(min(ltw,9)−1)。上がれば脚本家が強い。"""
    return sum(min(g["loops_to_win"], 9) - 1 for g in rep["games"])


def cause_hist(rep: dict) -> dict:
    return dict(Counter(g["b282"]["cause"] for g in rep["games"] if g.get("b282")))


def funnel_hist(rep: dict) -> dict:
    return dict(Counter(r["stage"] for g in rep["games"] for r in g["b283"]))


def per_game(rep: dict) -> list[tuple]:
    return [(g["script"], g["seed"], g["loops_to_win"], g["outcome"],
             (g.get("b282") or {}).get("sub", "-")) for g in rep["games"]]


def b286_summary(rep: dict, tag: str) -> str:
    oc = Counter(g["outcome"] for g in rep["games"])
    mean = round(sum(g["loops_to_win"] for g in rep["games"])
                 / len(rep["games"]), 3)
    L = [f"## B-286 数え上げ [{tag}] days={rep['days']} perm={rep['perm']} "
         f"(n={rep['n_games']}, HASHSEED={rep['hashseed']})",
         f"- 結末: {dict(sorted(oc.items()))} / 平均ループ {mean}",
         f"- 第1層(i) ループ内再置席: {replace_seats(rep)}"
         f"（decoy込み参考: {replace_seats(rep, include_decoy=True)}）",
         f"- 第1層(ii) s1a該当ループ: {s1a_loops(rep)}",
         f"- 第2層 脚本家が取ったループ総数: {mm_loops_total(rep)}",
         f"- 主因: {dict(sorted(cause_hist(rep).items()))}",
         f"- B-283漏斗: {dict(sorted(funnel_hist(rep).items()))}"]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 実行（ON/OFF は HeuristicMastermind のクラス属性で切替）
# ---------------------------------------------------------------------------
def run(days: int, perm: str, mode: str, penalty: float | None = None) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena import b282_classify as B
    old_flag = HM.B286_LINE_SWITCH
    old_pen = HM.B286_REPLACE_PENALTY
    try:
        HM.B286_LINE_SWITCH = (mode == "on")
        if penalty is not None:
            HM.B286_REPLACE_PENALTY = float(penalty)
        rep = B.sweep(days=days, perm=perm, verbose=False)
        rep["b286_mode"] = mode
        rep["b286_penalty"] = HM.B286_REPLACE_PENALTY if mode == "on" else None
        return rep
    finally:
        HM.B286_LINE_SWITCH = old_flag
        HM.B286_REPLACE_PENALTY = old_pen


def diff_games(rep_off: dict, rep_on: dict) -> list[str]:
    """flip（per-game の ltw 変化）を全数列挙する。"""
    off = {(g["script"], g["seed"]): g for g in rep_off["games"]}
    out = []
    for g in rep_on["games"]:
        k = (g["script"], g["seed"])
        o = off.get(k)
        if o and (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"],
                                                       g["outcome"]):
            out.append(f"{k[0]}#{k[1]}: {o['loops_to_win']}({o['outcome']}) → "
                       f"{g['loops_to_win']}({g['outcome']})")
    return out


# ---------------------------------------------------------------------------
# 検問4：実戦棋譜のリプレイ再採点（記録は変えず「この局面で乗り換えたか」を見る）
# ---------------------------------------------------------------------------
def scan_log(path: str, penalty: float | None = None) -> dict:
    """棋譜を b282 と同じシムでリプレイし、脚本家の set_card 局面を再採点する。

    出す数字＝
    - broken_at: b286_line_signals が「折られた線」を検出していた脚本家手番の数
    - replace_chosen: 記録の手がその線への再置だった手番の数
    - switch_on: ON の採点（rng 抜きの argmax 帯）で最上位帯から再置が外れた手番の数
    """
    from agents.heuristic import HeuristicMastermind as HM
    from agents.heuristic import _ANRYAKU_VALUE, b286_line_signals
    from arena.b282_classify import _ReplayWithB278Shim
    from sim import run_game
    from sim.state import script_from_dict

    meta, decisions = None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") == "meta":
                meta = d
            elif d.get("type") == "decision":
                decisions.append(d)
    script = script_from_dict(meta["script"])
    seqs = defaultdict(list)
    for d in decisions:
        seqs[d["actor"]].append((d["decision"], d["chosen"]))
    agents = {a: _ReplayWithB278Shim(seqs.get(a, []))
              for a in ("mastermind", "p1", "p2", "p3")}
    agents["mastermind"].wants_bluff_options = True
    log: list[dict] = []
    state, _ = run_game(script, agents, log=log)

    mm = HM(0)
    old_flag, old_pen = HM.B286_LINE_SWITCH, HM.B286_REPLACE_PENALTY
    rows = []
    try:
        if penalty is not None:
            HM.B286_REPLACE_PENALTY = float(penalty)
        for rec in log:
            if rec["actor"] != "mastermind" or rec["decision"] != "set_card":
                continue
            v = rec["view"]
            sig = b286_line_signals(v)
            if not sig["broken_seats"] and not sig["cooled"]:
                continue
            ch = rec["chosen"]
            is_replace = (ch.get("card") in _ANRYAKU_VALUE
                          and (ch.get("target_kind"), ch.get("target"))
                          in sig["broken_seats"])

            def _tops(flag: bool) -> list:
                HM.B286_LINE_SWITCH = flag
                a = mm._analyze(v)
                sc = [(mm._score_set(o, a, v) - mm._plus2_penalty(o, a, v)
                       - mm._b286_penalty(o, a), i)
                      for i, o in enumerate(rec["options"])]
                m = max(s for s, _ in sc)
                return [rec["options"][i] for s, i in sc if s == m]

            top_off, top_on = _tops(False), _tops(True)
            rows.append({
                "loop": rec["loop"], "day": rec["day"],
                "broken": sorted(sig["broken_seats"]),
                "cooled": sorted(sig["cooled"]),
                "chosen": ch, "chosen_is_replace": is_replace,
                "top_off": top_off, "top_on": top_on,
                "switched": ([o for o in top_on] != [o for o in top_off]),
            })
    finally:
        HM.B286_LINE_SWITCH, HM.B286_REPLACE_PENALTY = old_flag, old_pen
    n_replace = sum(1 for r in rows if r["chosen_is_replace"])
    n_switch = sum(1 for r in rows if r["chosen_is_replace"] and r["switched"])
    return {"path": path, "winner": state.winner,
            "loops_played": state.loop_no,
            "replay_matches_meta": (state.winner == meta.get("winner")
                                    and state.loop_no == meta.get("loops_played")),
            "mm_turns_with_signal": len(rows),
            "replace_chosen": n_replace, "switch_on": n_switch,
            "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-286 測定")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--mode", type=str, default="off", choices=("off", "on"))
    ap.add_argument("--penalty", type=float, default=None)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--from-json", type=str, default=None,
                    help="既存の JSON から数え上げだけやり直す")
    ap.add_argument("--log", type=str, default=None, help="検問4＝棋譜の再採点")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED 未固定。測定は PYTHONHASHSEED=0 で。",
              file=sys.stderr)
    if args.log:
        rep = scan_log(args.log, penalty=args.penalty)
        brief = {k: v for k, v in rep.items() if k != "rows"}
        print(json.dumps(brief, ensure_ascii=False, indent=1))
        for r in rep["rows"]:
            mark = "★" if r["chosen_is_replace"] else " "
            print(f"{mark} L{r['loop']}D{r['day']} broken={r['broken']} "
                  f"cooled={r['cooled']} chosen={r['chosen']} "
                  f"switched={r['switched']} top_on={r['top_on'][:2]}")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, default=list)
        return
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            rep = json.load(f)
        print(b286_summary(rep, rep.get("b286_mode", "?")))
        return
    rep = run(args.days, args.perm, args.mode, args.penalty)
    print(b286_summary(rep, args.mode
                       + (f"(pen={args.penalty})" if args.penalty else "")))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, default=list)
        print(f"→ {args.json}")


if __name__ == "__main__":
    main()
