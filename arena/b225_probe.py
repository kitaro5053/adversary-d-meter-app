# -*- coding: utf-8 -*-
"""B-225 計測プローブ：B-100 折り手選択の解け方を数える（読み取り専用）。

問い（チケット Phase 0）＝B-100 が break を選ぶとき「どの脅威の帰属キーに触れる
break か」を考慮していない穴の実測：
  - 発火した席（stage=強制した）で、**反復敗北の帰属キー（`repeat_break_keys`）に
    触れる break がこの席の候補（here）にありながら、選ばれなかった**回数（的A）。
  - 同じく「触れる break が候補に無い」（＝選好では直せない）回数（文脈）。
  - 資格ゼロで沈黙した rep≥1 制約（B-224 的①の文脈）。

計測は `agents.heuristic_protagonist.B100_HOOK`／`agents.b100_alloc.TRACE`
（どちらも計測専用・既定 None・挙動非接触）経由。条件は `arena.b224_ab.CONDS`
（＋あれば `arena.b225_ab.CONDS`）の名前で与える（off＝挙動不変）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b225_probe why \
        --game random_BTX:12:3 --cond iron015
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b225_probe scan \
        --days 3 --cond off
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from arena.b221_probe import make_script


def _conds() -> dict:
    from arena.b224_ab import CONDS as C224
    out = dict(C224)
    try:
        from arena.b225_ab import CONDS as C225
        out.update(C225)
    except ImportError:
        pass
    return out


def _attrs(conds: dict) -> tuple:
    """条件が触りうる属性の全集合（退避対象）。"""
    keys = set()
    for d in conds.values():
        keys |= set(d)
    return tuple(sorted(keys))


def probe_game(name: str, seed: int, days: int, cond: str = "off",
               loops: int = 8) -> dict:
    """1局を条件つきで再生し、allocate の解け方（席単位）と的の数え上げを返す。"""
    import agents.heuristic_protagonist as HPmod
    from agents import b100_alloc
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.b100_mix import (B211_FRIEND_DEATH_ATTR_DEFAULT, past_loss_keys,
                                 repeat_break_keys)
    from sim import run_game

    conds = _conds()
    attrs = _attrs(conds)
    old = {a: getattr(HeuristicProtagonist, a, None) for a in attrs}
    had = {a: hasattr(HeuristicProtagonist, a) for a in attrs}
    for k, v in conds[cond].items():
        setattr(HeuristicProtagonist, k, v)

    traces: list[dict] = []
    loop_ctx: dict[int, tuple] = {}   # loop -> (past_keys, cast, inc_names)

    def hook(agent, view, options, best, score):
        lp = view.get("loop")
        if lp not in loop_ctx:
            past = past_loss_keys(
                view.get("history", []) or [], lp,
                friend_attr=getattr(agent, "B211_FRIEND_DEATH_ATTR",
                                    B211_FRIEND_DEATH_ATTR_DEFAULT))
            cast = [c.get("name") for c in view.get("characters", []) or []]
            inc = {i.get("name") for i in view.get("incidents", []) or []}
            loop_ctx[lp] = (past, cast, inc)

    def trace(d):
        traces.append(dict(d))

    prev_hook, prev_trace = HPmod.B100_HOOK, b100_alloc.TRACE
    HPmod.B100_HOOK, b100_alloc.TRACE = hook, trace
    try:
        sc = make_script(name, seed, days)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        HPmod.B100_HOOK, b100_alloc.TRACE = prev_hook, prev_trace
        for a in attrs:
            if had[a]:
                setattr(HeuristicProtagonist, a, old[a])
            elif hasattr(HeuristicProtagonist, a):
                delattr(HeuristicProtagonist, a)

    lost = sorted({e["loop"] for e in state.history
                   if e.get("event") == "loop_result"
                   and "敗北" in str(e.get("result"))})

    # ---- 数え上げ（帰属キー照合の単一ソース＝repeat_break_keys） ----
    fired: list[dict] = []          # stage=強制した の席の解け方
    defers: list[dict] = []         # ★先送り段で「触れる折り手」を失いかけた席
    n_missed = 0                    # 的（選好）＝触れる break が here に有るのに選ばれなかった
    n_hit = 0                       # 触れる break が選ばれた
    n_unavail = 0                   # 触れる break がこの席の here に無い（選好では直せない）
    n_silent_rep = 0                # 資格ゼロで沈黙した rep≥1 制約つき席（B-224 的①の文脈）
    n_defer_occupied = 0            # ★的（先送り）＝先送り段で、資格つき×rep≥1 の帰属キー
    #   に触れる折り手が here に在り、かつ**自席の予定手（intent）がその対象を占有**
    #   ＝先送りするとその折り手が後続席から構造的に消える（重ね置き不可）席数
    n_defer_touch = 0               # 同・触れる折り手は在るが intent は占有しない（先送り可）
    for t in traces:
        lp = t.get("loop")
        ctx = loop_ctx.get(lp)
        stage = str(t.get("stage", ""))
        cons = t.get("cons") or []
        if stage.startswith("資格ゼロ"):
            if any(c.get("repeat", 0) >= 1 for c in cons):
                n_silent_rep += 1
            continue
        if (stage.startswith("計画なしターンは最終席のみ")
                or stage.startswith("最終席から奪わない")):
            if ctx is None:
                continue
            past, cast, inc = ctx
            intent = t.get("intent") or (None, None)
            occupied = False
            touch = False
            for c in cons:
                if c.get("gate") is None or c.get("repeat", 0) < 1:
                    continue
                rkeys = repeat_break_keys(c.get("kind"), c.get("label"),
                                          past, cast, inc)
                hits = [(cd, card, tg) for (cd, card, tg, tk)
                        in (c.get("here") or []) if tg in rkeys]
                if not hits:
                    continue
                touch = True
                if any(tg == intent[1] for _cd, _card, tg in hits):
                    occupied = True
                    defers.append({"loop": lp, "day": t.get("day"),
                                   "stage": stage, "label": c.get("label"),
                                   "repeat": c.get("repeat"),
                                   "rkeys": sorted(rkeys), "intent": intent,
                                   "hits": hits})
            if occupied:
                n_defer_occupied += 1
            elif touch:
                n_defer_touch += 1
            continue
        if stage != "強制した":
            continue
        ret = t.get("ret") or {}
        c = next((c for c in cons if c.get("label") == ret.get("label")), None)
        if c is None or ctx is None:
            continue
        past, cast, inc = ctx
        rkeys = repeat_break_keys(c.get("kind"), c.get("label"), past, cast, inc)
        here = c.get("here") or []
        touch_in_here = sorted({(cd, card, tg) for (cd, card, tg, tk) in here
                                if tg in rkeys})
        chosen_touches = ret.get("target") in rkeys
        row = {"loop": lp, "day": t.get("day"), "label": ret.get("label"),
               "repeat": c.get("repeat"), "rkeys": sorted(rkeys),
               "chosen": (ret.get("card"), ret.get("target")),
               "chosen_touches": chosen_touches,
               "here": here, "touch_in_here": touch_in_here}
        fired.append(row)
        if not rkeys or c.get("repeat", 0) < 1:
            continue
        if chosen_touches:
            n_hit += 1
        elif touch_in_here:
            n_missed += 1
        else:
            n_unavail += 1
    return {"game": f"{name}#{seed}", "days": days, "cond": cond,
            "winner": state.winner, "lost": lost, "n_lost": len(lost),
            "fired": fired, "defers": defers, "missed": n_missed, "hit": n_hit,
            "unavail": n_unavail, "silent_rep": n_silent_rep,
            "defer_occupied": n_defer_occupied, "defer_touch": n_defer_touch}


def cmd_why(args) -> int:
    name, seed, days = args.game
    r = probe_game(name, seed, days, cond=args.cond, loops=args.loops)
    print(f"=== {r['game']} d{days} cond={r['cond']} winner={r['winner']} "
          f"敗北ループ={r['lost']} ===")
    print(f"選好の的(触れるbreakが候補に有るのに選ばれず)={r['missed']}  "
          f"触れるbreakを選択={r['hit']}  触れるbreakが候補に無い={r['unavail']}  "
          f"資格ゼロ沈黙(rep≥1)={r['silent_rep']}")
    print(f"先送りの的(占有で消える折り手を先送り)={r['defer_occupied']}  "
          f"先送り(占有なし)={r['defer_touch']}")
    for d in r["defers"]:
        print(f"  [先送り占有] L{d['loop']}D{d['day']} intent={d['intent']}"
              f" rep={d['repeat']} 帰属キー={d['rkeys']} 消える折り手={d['hits']}")
    for f in r["fired"]:
        mark = ("○" if f["chosen_touches"] else
                ("★missed" if f["touch_in_here"] else
                 ("×unavail" if f["rkeys"] and (f["repeat"] or 0) >= 1 else "-")))
        print(f"\nL{f['loop']}D{f['day']} 強制={f['chosen'][0]}→{f['chosen'][1]}"
              f" {mark} rep={f['repeat']} 帰属キー={f['rkeys']}")
        print(f"  {f['label']}")
        for cd, card, tg, tk in f["here"]:
            t = " ←キーに触れる" if tg in f["rkeys"] else ""
            print(f"    here cost={cd:.1f} {card}→{tg}{t}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump(r, fp, ensure_ascii=False, indent=1)
    return 0


def cmd_scan(args) -> int:
    """ベンチ全局で的Aを数える（列挙順は触らない＝素の並び）。"""
    from arena.benchmark import benchmark_scripts
    tot = {"missed": 0, "hit": 0, "unavail": 0, "silent_rep": 0,
           "defer_occupied": 0, "defer_touch": 0}
    rows = []
    for name, seed, _sc in benchmark_scripts(days=args.days):
        r = probe_game(name, seed, args.days, cond=args.cond, loops=args.loops)
        for k in tot:
            tot[k] += r[k]
        if (r["missed"] or r["hit"] or r["unavail"] or r["defer_occupied"]
                or r["defer_touch"]):
            rows.append(r)
            print(f"{r['game']}: missed={r['missed']} hit={r['hit']} "
                  f"unavail={r['unavail']} defer_occ={r['defer_occupied']} "
                  f"defer_touch={r['defer_touch']} "
                  f"silent_rep={r['silent_rep']} "
                  f"lost={r['n_lost']} winner={r['winner']}", flush=True)
    print(f"\n[days={args.days} cond={args.cond}] missed={tot['missed']} "
          f"hit={tot['hit']} unavail={tot['unavail']} "
          f"defer_occ={tot['defer_occupied']} defer_touch={tot['defer_touch']} "
          f"silent_rep={tot['silent_rep']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump({"days": args.days, "cond": args.cond, "totals": tot,
                       "games": rows}, fp, ensure_ascii=False, indent=1)
    return 0


def _parse_game(s: str):
    name, seed, days = s.split(":")
    return name, int(seed), int(days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-225 折り手選択の計測（読み取り専用）")
    ap.add_argument("cmd", choices=["why", "scan"])
    ap.add_argument("--game", type=_parse_game, default=None)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--cond", default="off")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"why": cmd_why, "scan": cmd_scan}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
