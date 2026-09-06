# -*- coding: utf-8 -*-
"""B-227 計測プローブ：鉄則①資格（iron）の発火の解剖（読み取り専用）。

問い（チケット Phase 0）＝iron015 の広帯域被害（FS#3 3日級・FS#16 5日級の退行）は
**どのチャネル・どの p 帯で誤発火しているか**。BTX#12 の正当な発火と判別可能な
公開情報の差を特定する。

出すもの（1局・条件つき再生）：
  - `強制した` 席の全数＝(loop, day, 資格種別, threat kind/label/prob/repeat,
    強制手, 押しのけた intent)
  - 資格種別ごとの席数（θ／鉄則①／当夜×帯／供給）
  - 鉄則①席の帰属文脈＝敗北署名の型・一致キー・**同一キーで資格を得た制約の本数**
    （チャネル曖昧度）
  - 各ループの敗北署名（公開情報＝`loss_signatures`）＝実際の負けチャネルの推移

計測は `B100_HOOK`／`b100_alloc.TRACE`（計測専用・既定 None・挙動非接触）経由。
条件は `arena.b224_ab.CONDS`＋`arena.b225_ab.CONDS`（＋あれば本モジュール追加分）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b227_probe why \
        --game random_FS:3:3 --cond iron015
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b227_probe scan \
        --days 3 --cond iron015
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
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
    try:
        from arena.b227_ab import CONDS as C227
        out.update(C227)
    except ImportError:
        pass
    return out


def _attrs(conds: dict) -> tuple:
    keys = set()
    for d in conds.values():
        keys |= set(d)
    return tuple(sorted(keys))


def _gate_kind(gate: str | None) -> str:
    if gate is None:
        return "なし"
    for k in ("θ経路", "当夜×帯", "供給候補", "鉄則①", "制約"):
        if gate.startswith(k):
            return k
    return "その他"


def probe_game(name: str, seed: int, days: int, cond: str = "off",
               loops: int = 8, perm: str = "id") -> dict:
    import agents.heuristic_protagonist as HPmod
    from agents import b100_alloc
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.b100_mix import (B211_FRIEND_DEATH_ATTR_DEFAULT,
                                 loss_signatures, past_loss_keys,
                                 repeat_break_keys)
    from arena.tie_noise import install_perm, uninstall_perm
    from sim import run_game

    conds = _conds()
    attrs = _attrs(conds)
    old = {a: getattr(HeuristicProtagonist, a, None) for a in attrs}
    had = {a: hasattr(HeuristicProtagonist, a) for a in attrs}
    for k, v in conds[cond].items():
        setattr(HeuristicProtagonist, k, v)

    traces: list[dict] = []
    loop_ctx: dict[int, tuple] = {}

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
    install_perm(perm)
    try:
        sc = make_script(name, seed, days)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        uninstall_perm()
        HPmod.B100_HOOK, b100_alloc.TRACE = prev_hook, prev_trace
        for a in attrs:
            if had[a]:
                setattr(HeuristicProtagonist, a, old[a])
            elif hasattr(HeuristicProtagonist, a):
                delattr(HeuristicProtagonist, a)

    lost = sorted({e["loop"] for e in state.history
                   if e.get("event") == "loop_result"
                   and "敗北" in str(e.get("result"))})

    # ---- 各ループの敗北署名（公開情報のみ） ----
    sigs = loss_signatures(state.history)
    sig_rows = []
    for lp in sorted(sigs):
        s = sigs[lp]
        sig_rows.append({
            "loop": lp, "type": s["type"],
            "reasons": sorted(s["reasons"]),
            "end_days": sorted(d for d in s["end_days"] if d is not None),
            "fatal_deaths": sorted(s["fatal_deaths"]),
            "friend_deaths": sorted(s.get("friend_deaths", ())),
            "incidents": sorted(f"D{d}:{n}" for d, n in s["incidents"]),
            "boards": sorted(s["boards"]),
            "anyaku_chars": sorted(s["anyaku_chars"]),
        })

    # ---- 強制席の全数（資格種別つき） ----
    forced: list[dict] = []
    gate_counts: Counter = Counter()
    for t in traces:
        if str(t.get("stage", "")) != "強制した":
            continue
        lp, day = t.get("loop"), t.get("day")
        ret = t.get("ret") or {}
        cons = t.get("cons") or []
        c = next((c for c in cons if c.get("label") == ret.get("label")), None)
        gate = (c or {}).get("gate")
        gk = _gate_kind(gate)
        gate_counts[gk] += 1
        ctx = loop_ctx.get(lp)
        rkeys, ty, ambig = [], None, None
        if c is not None and ctx is not None:
            past, cast, inc = ctx
            from agents.b100_mix import KIND_LOSS_TYPE
            ty = KIND_LOSS_TYPE.get(c.get("kind"))
            rkeys = sorted(repeat_break_keys(c.get("kind"), c.get("label"),
                                             past, cast, inc))
            # チャネル曖昧度＝同席 cons のうち鉄則資格を得た制約の本数と kind 一覧
            iron_cons = [x for x in cons
                         if (x.get("gate") or "").startswith("鉄則①")]
            ambig = {"n_iron": len(iron_cons),
                     "kinds": sorted({x.get("kind") for x in iron_cons}),
                     "labels": sorted({x.get("label") for x in iron_cons})}
        forced.append({"loop": lp, "day": day, "gate": gate, "gate_kind": gk,
                       "kind": (c or {}).get("kind"),
                       "label": ret.get("label"),
                       "prob": (c or {}).get("prob"),
                       "repeat": (c or {}).get("repeat"),
                       "chosen": (ret.get("card"), ret.get("target")),
                       "b225": ret.get("b225"),
                       "intent": t.get("intent"),
                       "loss_type": ty, "rep_keys": rkeys, "ambig": ambig})
    return {"game": f"{name}#{seed}", "days": days, "cond": cond,
            "winner": state.winner, "lost": lost, "n_lost": len(lost),
            "gate_counts": dict(gate_counts), "forced": forced,
            "loss_sigs": sig_rows}


def cmd_why(args) -> int:
    name, seed, days = args.game
    r = probe_game(name, seed, days, cond=args.cond, loops=args.loops,
                   perm=args.perm)
    print(f"=== {r['game']} d{days} cond={r['cond']} perm={args.perm} "
          f"winner={r['winner']} 敗北ループ={r['lost']} ===")
    print(f"強制席の資格内訳: {r['gate_counts']}")
    print("\n-- 各ループの敗北署名（公開情報） --")
    for s in r["loss_sigs"]:
        print(f"  L{s['loop']} type={s['type']} 終了日={s['end_days']} "
              f"理由={s['reasons']} 決定打死者={s['fatal_deaths']} "
              f"フレンド死亡={s['friend_deaths']} 事件={s['incidents']} "
              f"板={s['boards']} 暗躍キャラ={s['anyaku_chars']}")
    print("\n-- 強制席の全数 --")
    for f in r["forced"]:
        am = f.get("ambig") or {}
        print(f"  L{f['loop']}D{f['day']} [{f['gate_kind']}] "
              f"{f['chosen'][0]}→{f['chosen'][1]}"
              f"{'（b225 ' + f['b225'] + '）' if f.get('b225') else ''} "
              f"intent={f['intent']}")
        print(f"    制約={f['label']} kind={f['kind']} p={f['prob']} "
              f"rep={f['repeat']} 型={f['loss_type']} 帰属キー={f['rep_keys']}")
        if am:
            print(f"    同席の鉄則資格={am.get('n_iron')}本 "
                  f"kinds={am.get('kinds')}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump(r, fp, ensure_ascii=False, indent=1)
    return 0


def cmd_scan(args) -> int:
    """ベンチ全局で鉄則①発火席を数える（素の並び）。"""
    from arena.benchmark import benchmark_scripts
    rows = []
    tot = Counter()
    for name, seed, _sc in benchmark_scripts(days=args.days):
        r = probe_game(name, seed, args.days, cond=args.cond, loops=args.loops)
        n_iron = r["gate_counts"].get("鉄則①", 0)
        tot.update(r["gate_counts"])
        if n_iron:
            kinds = Counter(f["kind"] for f in r["forced"]
                            if f["gate_kind"] == "鉄則①")
            probs = sorted({round(f["prob"], 2) for f in r["forced"]
                            if f["gate_kind"] == "鉄則①"})
            print(f"{r['game']}: iron席={n_iron} kinds={dict(kinds)} "
                  f"p={probs} lost={r['n_lost']} winner={r['winner']}",
                  flush=True)
            rows.append(r)
    print(f"\n[days={args.days} cond={args.cond}] 資格内訳合計={dict(tot)}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump({"days": args.days, "cond": args.cond,
                       "totals": dict(tot), "games": rows},
                      fp, ensure_ascii=False, indent=1)
    return 0


def _parse_game(s: str):
    name, seed, days = s.split(":")
    return name, int(seed), int(days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-227 鉄則①発火の解剖（読み取り専用）")
    ap.add_argument("cmd", choices=["why", "scan"])
    ap.add_argument("--game", type=_parse_game, default=None)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--cond", default="off")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"why": cmd_why, "scan": cmd_scan}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
