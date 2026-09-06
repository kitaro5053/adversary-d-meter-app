# -*- coding: utf-8 -*-
"""B-228 Phase 0 プローブ：D（`B224_WEAK_MATCH`）が壊す局と効く局の判別材料を実測する。

問い（チケット §2 Phase 0）：
  - `random_BTX#16`（5日級・perm rev/h1 で D が防衛→喪失）＝何が cap され、
    どの手に化け、なぜ負けるのか。
  - `random_BTX#0`（5日級・treadmill＝cap が正しく効く局）との
    **判別可能な公開情報の差**は何か。
    候補＝ピン対象の役職 belief（SK 配達ピン）／ピン対象が敗因チャネルの供給側か
    受け手側か／run 中の敗北署名の変化／B-222 幾何語彙の再利用。

計測は `B100_HOOK`（計測専用・挙動非接触）＋対局後の履歴の後解析
（`avoid_moves` は「loop より前の公開履歴」だけを読む＝全履歴からの再構成が正確）。
perm は `arena.tie_noise.install_perm`（採点無改変・並び順のみ）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b228_probe why \
        --game random_BTX:16:5 --perm h1 --conds off,D --out docs/仮_b228_log/why_BTX16_h1.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import replace

from arena.b221_probe import make_script


def _sig_dump(s: dict) -> dict:
    """loss_signatures の1件を JSON 化（set→sorted list）。"""
    out = {}
    for k, v in s.items():
        if isinstance(v, (set, frozenset)):
            out[k] = sorted(map(str, v)) if v and not isinstance(
                next(iter(v)), tuple) else sorted([list(t) for t in v])
        else:
            out[k] = v
    return out


def run_why(name: str, seed: int, days: int, perm: str = "id",
            cond: str = "off", loops: int = 8) -> dict:
    import agents.heuristic_protagonist as HPmod
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.b226_ab import CONDS, _ATTRS
    from arena.tie_noise import install_perm, uninstall_perm
    from sim import run_game

    old = {a: getattr(HeuristicProtagonist, a) for a in _ATTRS}
    for k, v in CONDS[cond].items():
        setattr(HeuristicProtagonist, k, v)

    seats: list[dict] = []

    def hook(agent, view, options, best, score):
        av = getattr(agent, "_b221_avoid_today", None) or frozenset()
        day = view.get("day")
        # 回避集合の手が options に在ればその素点（cap 適用後の score()）も見る
        av_opts = []
        for o in options:
            key = (o.get("card"), o.get("target"), o.get("target_kind"))
            if key in av:
                av_opts.append({"move": list(key), "score": round(score(o), 2)})
        seats.append({
            "loop": view.get("loop"), "day": day, "seat": view.get("seat"),
            "chosen": [best.get("card"), best.get("target"),
                       best.get("target_kind")],
            "chosen_score": round(score(best), 2),
            "avoid_today": sorted([list(m) for m in av]),
            "avoid_in_options": av_opts,
            "sk_cands": sorted(getattr(agent, "_sk_cands", ()) or ()),
            "sk_suspects": sorted(getattr(agent, "_sk_suspects", ()) or ()),
            "sk_strong": sorted(getattr(agent, "_sk_strong", ()) or ()),
            "culprit_cands_today": sorted(
                (getattr(agent, "_culprit_cands", {}) or {}).get(day, ()) or ()),
        })

    prev_hook = HPmod.B100_HOOK
    HPmod.B100_HOOK = hook
    install_perm(perm)
    try:
        sc = make_script(name, seed, days)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        uninstall_perm()
        HPmod.B100_HOOK = prev_hook
        for a, v in old.items():
            setattr(HeuristicProtagonist, a, v)

    hist = state.history
    from agents.b100_mix import loss_signatures
    from agents.b221_breaker import (_decisive_days, avoid_moves, loss_run,
                                     team_response_by_day)
    sigs = loss_signatures(hist)
    loops_seen = sorted({e.get("loop") for e in hist
                         if e.get("event") == "cards_revealed"})
    per_loop = []
    for lp in loops_seen:
        # そのループ開始時点で agent が見たものの再構成（loop より前だけ読む）
        run, _s = loss_run(hist, lp)
        weak = avoid_moves(hist, lp, weak=True)
        team = team_response_by_day(hist, lp)
        mm_pl: dict[int, list] = defaultdict(list)
        for e in hist:
            if e.get("event") != "cards_revealed" or e.get("loop") != lp:
                continue
            for p in e.get("placements", []) or []:
                if p.get("owner") == "mastermind":
                    mm_pl[e.get("day")].append(
                        [p.get("card"), p.get("target"), p.get("target_kind")])
        events = [
            {"day": e.get("day"), "event": e.get("event"),
             "name": e.get("name"), "occurs": e.get("occurs"),
             "reason": e.get("reason"), "result": e.get("result")}
            for e in hist if e.get("loop") == lp
            and e.get("event") in ("incident", "death", "loop_end",
                                   "loop_result", "final_battle")]
        per_loop.append({
            "loop": lp,
            "run_at_entry": run,
            "weak_avoid_at_entry": {str(d): sorted([list(m) for m in v])
                                    for d, v in weak.items()},
            "team_by_day": {str(d): sorted([list(m) for m in v])
                            for d, v in team.items()},
            "mm_by_day": {str(d): v for d, v in sorted(mm_pl.items())},
            "sig": _sig_dump(sigs[lp]) if lp in sigs else None,
            "decisive_days": sorted(_decisive_days(sigs[lp])) if lp in sigs else [],
            "events": events,
        })
    return {"game": f"{name}#{seed}", "days": days, "perm": perm, "cond": cond,
            "winner": state.winner, "loop_no": state.loop_no,
            "final_battle": any(e.get("event") == "final_battle" for e in hist),
            "per_loop": per_loop, "seats": seats}


def cmd_why(a) -> int:
    name, seed, days = a.game.split(":")
    out = {}
    for cond in a.conds.split(","):
        r = run_why(name, int(seed), int(days), perm=a.perm, cond=cond,
                    loops=a.loops)
        out[cond] = r
        lost = [pl["loop"] for pl in r["per_loop"] if pl["sig"]]
        print(f"[{a.perm}/{cond}] {r['game']} d{r['days']}: winner={r['winner']} "
              f"loop_no={r['loop_no']} fb={r['final_battle']} lost={lost}",
              flush=True)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"→ {a.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-228 Phase 0 プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["why"])
    ap.add_argument("--game", required=True, help="name:seed:days")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--conds", default="off,D")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"why": cmd_why}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
