# -*- coding: utf-8 -*-
"""B-253：`不安+1` の使い所の実測（A/B/C/D の計測＋反実仮想）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-51。事前登録＝
`docs/測定_B253_不安プラス1の使い所_2026-08-18.md` §0。

## 先行レーンとの関係

★**`arena/b249_audit.py` / `arena/b250_audit.py` は1バイトも変更していない**。
本モジュールは両者のヘルパ（`defaults_banner` / `check_no_knob_writes` / `parse_pick` /
`_truth_of` / `_okey`）を import して使う。切替口の健全性検査は `arena/knob_audit.py`
（main 着地済み）の一般形 `check_baseline` を配線する（B-252 の申し送り）。

## 本モジュールが測るもの（事前登録 §0-3）

| 量 | 定義 |
|---|---|
| **A** | `不安+1`（キャラ対象）を打った席のうち、**belief 水準の厳密な反実仮想**で役職の事後確率が動いた席。H'＝そのカウンター1個だけを履歴から取り除いた履歴。閾値 ε=1e-6 |
| **B** | `友好+1/+2`（キャラ対象）を打った席のうち、そのループ中に必要友好数へ到達させた席。**B_same（同日集中）／B_acc（積む）／B_miss** に3分割し、**B_used**（実際に能力が使われた）を別掲 |
| **C** | ウイルス自傷＝`不安+1` が「妄想拡大ウイルスがルールX候補に残っている」状態でキャラへ打たれた席（露出）／実際に不安3へ届いた席（実現）／その後に死者が出た席 |
| **D1** | フィラー＝その席の `不安+1` を除いた次善手のスコアと、**`友好` オプションが legal に在るか** |
| **D2** | `不安+1` が事件の eligible 判定に pivotal だった席（＝事件を起こしうる／起こした） |
| **D4** | 対象の占有＝同ターンの他席が同じ対象へ打ちたかった回数 |

★**D3（相手AIの反応を誘う）は構造的に 0**＝測らない（事前登録 §0-0）。

## 挙動には触れない

`agents/` `sim/` `engine/` の既定値を一切書き換えない。観測は
`agents.heuristic_protagonist.B100_HOOK`（計測専用フック・戻り値不使用）と、
`sim.flow` の**読み取り専用ラッパ**（カウンターのスナップショットを取るだけ）で行う。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b253_audit seats  --days 5 --pick all --outdir /tmp/b253/d5
    python -m arena.b253_audit seats  --days 3 --pick all --outdir /tmp/b253/d3
    python -m arena.b253_audit report --outdir /tmp/b253/d5
    python -m arena.b253_audit verify --days 3 --pick random_BTX:0-2
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import replace

from arena.b249_audit import (  # noqa: F401  （★B-249/B-250 を書き換えずに使う）
    _EPS, _okey, check_no_knob_writes, defaults_banner,
)
from arena.b250_audit import _truth_of, parse_pick

VIRUS = "妄想拡大ウイルス"
UNREST_PLUS = "不安+1"
GOODWILL_CARDS = ("友好+1", "友好+2")
#: A の判定閾値（事前登録＝§0-3。role_marginals の L1 総変動）。
A_EPS = 1e-6


# ---------------------------------------------------------------------------
# 切替口の健全性検査（B-248 の一般形を配線する）
# ---------------------------------------------------------------------------
def knob_baseline_banner() -> str:
    """`arena/knob_audit.check_baseline` の一般形＝**条件表なし＝空のベースライン**。

    本ドライバは A/B ドライバではなく条件表を持たない（既定のまま1本回す）ので、
    「ベースライン条件のフラグ束 == リポジトリ既定」は**空の条件表**に対して検査する
    ＝自明に成り立つことを明示的に確かめる（退化形の健全性）。
    """
    from arena import knob_audit
    knob_audit.check_baseline({}, {}, (), driver="arena.b253_audit")
    return "knob_audit.check_baseline（空の条件表＝退化形）✅"


# ---------------------------------------------------------------------------
# 観測（★読み取りのみ）
# ---------------------------------------------------------------------------
class Rec:
    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.seats: list[dict] = []      # 不安+1(キャラ) と 友好(キャラ) の実打席
        self.turn_opts: dict = {}        # (loop,day) -> [ {seat, card, target, kind, score} ]
        self.snap6: dict = {}            # "L:D" -> {name: [unrest, goodwill]}（主人公能力フェイズ直前）
        self.snap7: dict = {}            # "L:D" -> {name: unrest}（事件フェイズ直前）
        self.snap9: dict = {}            # "L:D" -> {name: unrest}（ターン終了後）
        self.mismatch: list[dict] = []
        self.meta: dict = {}

    def to_json(self) -> dict:
        return {"counts": dict(self.c), "seats": self.seats,
                "snap6": self.snap6, "snap7": self.snap7, "snap9": self.snap9,
                "mismatch": self.mismatch, "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> "Rec":
        r = cls()
        r.c.update(d.get("counts", {}))
        r.seats = list(d.get("seats", []))
        r.snap6 = dict(d.get("snap6", {}))
        r.snap7 = dict(d.get("snap7", {}))
        r.snap9 = dict(d.get("snap9", {}))
        r.mismatch = list(d.get("mismatch", []))
        r.meta = dict(d.get("meta", {}))
        return r


def _virus_mass(belief) -> float:
    """belief 上の P(ルールX に妄想拡大ウイルスが含まれる)。"""
    try:
        rm = belief.rule_marginals()
    except Exception:                                # pragma: no cover
        return float("nan")
    return sum(v for k, v in rm.items() if VIRUS in (k[1] or ()))


def _hook_factory(rec: Rec, script_obj, name: str, seed: int):
    """`B100_HOOK` に挿す観測子（★戻り値は使われない＝棋譜に影響しない）。"""
    def _hook(agent, view, options, best, score):
        rec.c["席（set_card 決定）"] += 1
        lp, dy = view.get("loop"), view.get("day")
        key = f"{lp}:{dy}"
        card, tgt, kind = best["card"], best.get("target"), best.get("target_kind")
        # ★採点の再実行は**関係する席だけ**（本物の score は重い）。他席は候補キーのみ記録
        #   （D4＝「他席も同じ対象へ置けたか」の判定に必要なのは対象の在否だけ）。
        if kind != "character" or card not in (UNREST_PLUS,) + GOODWILL_CARDS:
            rec.turn_opts.setdefault(key, []).append(
                {"seat": view.get("seat"), "best": list(_okey(best)),
                 "opts": [[o["card"], o.get("target"), o.get("target_kind"), None]
                          for o in options]})
            return
        # ★本物の採点器で全オプションを採点し直す（写経しない）。
        scores = [(o, float(score(o))) for o in options]
        # ★自己検証＝**本物の採点器の再入性**（本測定は score を席の外から呼び直すので、
        #   同じ席で2回呼んで同じ値が返ることが前提。1件でも食い違えば測定は無効）。
        for o, v in scores:
            if float(score(o)) != v:
                rec.mismatch.append({"loop": lp, "day": dy, "seat": view.get("seat"),
                                     "opt": list(_okey(o)), "v1": v,
                                     "v2": float(score(o))})
                rec.c["★採点器が再入で食い違った席"] += 1
                break
        # ★参考統計＝`best` は必ずしも argmax ではない（計画割当・B-241/B-246 の振替が
        #   `max(options, key=score)` を迂回する）。**食い違いではない**ので別勘定にする。
        top = max(scores, key=lambda t: t[1])
        if _okey(top[0]) != _okey(best):
            rec.c["best≠argmax（計画割当・振替）"] += 1
        rec.turn_opts.setdefault(key, []).append(
            {"seat": view.get("seat"),
             "best": list(_okey(best)),
             "opts": [[o["card"], o.get("target"), o.get("target_kind"), round(s, 4)]
                      for o, s in scores]})
        b = getattr(agent, "_belief", None)
        marg = (b.role_marginals() if b is not None else {}) or {}
        # D1＝この席から「その札」を除いた次善手／`友好` オプションの在否と最高点
        alt = [(o, s) for o, s in scores if _okey(o) != _okey(best)]
        alt_top = max(alt, key=lambda t: t[1]) if alt else None
        gw = [(o, s) for o, s in scores
              if o["card"] in GOODWILL_CARDS and o.get("target_kind") == "character"]
        gw_top = max(gw, key=lambda t: t[1]) if gw else None
        un = [(o, s) for o, s in scores
              if o["card"] == UNREST_PLUS and o.get("target_kind") == "character"]
        un_top = max(un, key=lambda t: t[1]) if un else None
        rec.seats.append({
            "script": name, "seed": seed, "loop": lp, "day": dy,
            "seat": view.get("seat"), "card": card, "target": tgt,
            "played_score": round(float(score(best)), 4),
            "n_options": len(options),
            "alt_top": ([*_okey(alt_top[0])], round(alt_top[1], 4)) if alt_top else None,
            "gw_top": ([*_okey(gw_top[0])], round(gw_top[1], 4)) if gw_top else None,
            "un_top": ([*_okey(un_top[0])], round(un_top[1], 4)) if un_top else None,
            "p_virus": round(_virus_mass(b), 6) if b is not None else None,
            "p_person": round(float(marg.get(tgt, {}).get("パーソン", 0.0)), 6),
            "true_role": _truth_of(script_obj, tgt),
            "experiment": bool(getattr(agent, "_experiment", False)),
            "virus_uncertain": bool(getattr(agent, "_virus_uncertain", False)),
            "virus_targets": sorted(getattr(agent, "_virus_test_targets", ()) or ()),
        })
    return _hook


# ---------------------------------------------------------------------------
# 対局（★読み取り専用ラッパでカウンターのスナップショットを取る）
# ---------------------------------------------------------------------------
def _run(script_obj, name: str, seed: int, loops: int, rec: Rec | None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hp_mod
    from sim import run_game
    from sim import flow as flow_mod

    orig_hook = hp_mod.B100_HOOK
    orig_gw = flow_mod._run_goodwill_phase
    orig_inc = flow_mod.resolve_incident_phase
    orig_day = flow_mod.run_day

    def _snap(state, which, with_gw=False):
        k = f"{state.loop_no}:{state.day}"
        if with_gw:
            which[k] = {n: [c.unrest, c.goodwill] for n, c in state.characters.items()}
        else:
            which[k] = {n: c.unrest for n, c in state.characters.items()}

    def _gw(state, decide):
        _snap(state, rec.snap6, with_gw=True)
        return orig_gw(state, decide)

    def _inc(state, decide):
        _snap(state, rec.snap7)
        return orig_inc(state, decide)

    def _day(state, decide, human_seats=frozenset()):
        out = orig_day(state, decide, human_seats)
        _snap(state, rec.snap9)
        return out

    if rec is not None:
        hp_mod.B100_HOOK = _hook_factory(rec, script_obj, name, seed)
        flow_mod._run_goodwill_phase = _gw
        flow_mod.resolve_incident_phase = _inc
        flow_mod.run_day = _day
    try:
        agent = HeuristicProtagonist(seed)
        state, _ = run_game(replace(script_obj, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": agent, "p2": agent, "p3": agent})
    finally:
        hp_mod.B100_HOOK = orig_hook
        flow_mod._run_goodwill_phase = orig_gw
        flow_mod.resolve_incident_phase = orig_inc
        flow_mod.run_day = orig_day
    if rec is not None:
        _postprocess(rec, state, script_obj, agent)
    return state


# ---------------------------------------------------------------------------
# 後処理＝A（belief 反実仮想）・B（友好の到達）・C（ウイルス自傷）・D2/D4
# ---------------------------------------------------------------------------
def _cards_revealed_index(history: list[dict]) -> dict:
    out = {}
    for i, e in enumerate(history):
        if e.get("event") == "cards_revealed":
            out[(e.get("loop"), e.get("day"))] = i
    return out


def _placements_at(history: list[dict], lp: int, dy: int) -> list[dict]:
    for e in history:
        if (e.get("event") == "cards_revealed"
                and e.get("loop") == lp and e.get("day") == dy):
            return list(e.get("placements") or ())
    return []


def _rewrite_history(history, idx0, lp, tgt, snap7, thr_of):
    """H'＝その `不安+1` が置いたカウンター1個だけを取り除いた履歴。

    返り値 (H', status)。status:
      - "ok"       ＝厳密に書き換えられた
      - "clamp"    ＝同ループ内で対象の不安が 0 に触れる（1下げが定義できない）＝判定不能
      - "branch"   ＝事件が **発生**していて、1下げると対象が eligible から落ちる
                     ＝**対局が分岐する**（occurs を書き換えるのは捏造）＝判定不能
    """
    out = []
    status = "ok"
    for i, e in enumerate(history):
        if i <= idx0 or e.get("loop") != lp:
            out.append(e)
            continue
        e2 = e
        for fld in ("unrest", "present_unrest"):
            m = e.get(fld)
            if isinstance(m, dict) and tgt in m:
                v = m[tgt]
                if v <= 0:
                    status = "clamp"
                e2 = dict(e2)
                m2 = dict(m)
                m2[tgt] = max(0, v - 1)
                e2[fld] = m2
        if e.get("event") == "incident" and isinstance(e.get("eligible"), list) \
                and tgt in e["eligible"]:
            th = thr_of(tgt)
            u = (snap7.get(f"{lp}:{e.get('day')}") or {}).get(tgt)
            if th is not None and u is not None and (u - 1) < th:
                if e.get("occurs"):
                    status = "branch"
                else:
                    e2 = dict(e2)
                    e2["eligible"] = [n for n in e["eligible"] if n != tgt]
        out.append(e2)
    return out, status


def _force_history(history, idx0, lp, tgt, value: int):
    """★計器の感度検査用（変異テスト）＝対象の不安を**同ループ内で全部 `value` にした**履歴。

    A が 0 と出たとき「情報が無かった」のか「計器が盲目」なのかを分ける
    （§72-48 教訓1 の実務版）。★belief の不安チャネルは**しきい値型**
    （`agents/belief.py:1165` の不安≥3／`:1138` の不安≥3）なので、
    **0 側へ振る摂動はしきい値を跨がず必ず 0 が出る**＝盲目性の検査にならない。
    ∴ 検査は **3 側へ振る**（＝「もし届いていたら情報が出たのか」）で行う。
    """
    out = []
    for i, e in enumerate(history):
        if i <= idx0 or e.get("loop") != lp:
            out.append(e)
            continue
        e2 = e
        for fld in ("unrest", "present_unrest"):
            m = e.get(fld)
            if isinstance(m, dict) and tgt in m:
                e2 = dict(e2)
                m2 = dict(m)
                m2[tgt] = value
                e2[fld] = m2
        out.append(e2)
    return out


def _obs_census(history, idx0, lp, tgt):
    """belief が**不安の値を条件に使う**観測のうち、対象が登場するものを数え上げる。"""
    rows = []
    for i, e in enumerate(history):
        if i <= idx0 or e.get("loop") != lp:
            continue
        ev = e.get("event")
        if ev == "turn_end_pairs" and tgt in (e.get("unrest") or {}):
            rows.append(["turn_end_pairs", e.get("day"), (e.get("unrest") or {})[tgt]])
        elif ev == "death" and tgt in (e.get("present_unrest") or {}):
            rows.append(["death.present", e.get("day"),
                         (e.get("present_unrest") or {})[tgt]])
        elif ev == "protagonist_death" and tgt in (e.get("unrest") or {}):
            rows.append(["protagonist_death", e.get("day"), (e.get("unrest") or {})[tgt]])
        elif ev == "incident" and tgt in (e.get("eligible") or ()):
            rows.append(["incident.eligible", e.get("day"), None])
    return rows


def _l1(m0: dict, m1: dict) -> float:
    keys = set(m0) | set(m1)
    tot = 0.0
    for c in keys:
        a, b = m0.get(c, {}), m1.get(c, {})
        for r in set(a) | set(b):
            tot += abs(a.get(r, 0.0) - b.get(r, 0.0))
    return tot


def _mk_belief(cast, incidents, set_name):
    from agents.belief import Belief
    from agents.heuristic_protagonist import HeuristicProtagonist
    b = Belief(list(cast), list(incidents), set_name=set_name)
    for f in HeuristicProtagonist.SOFT_EVIDENCE:
        b.register_soft_evidence(f())
    return b


def _postprocess(rec: Rec, state, script_obj, agent) -> None:
    from engine.data import goodwill_abilities_of, unrest_threshold_of
    history = [dict(e) for e in state.history]
    b0 = getattr(agent, "_belief", None)
    cast = list(getattr(b0, "cast", []) or [c for c in state.characters])
    incidents = list(getattr(b0, "incidents", []) or [])
    set_name = getattr(b0, "set_name", state.script.set_name)
    idxs = _cards_revealed_index(history)
    base = _mk_belief(cast, incidents, set_name)
    base.observe(history)
    M0 = base.role_marginals()
    rec.meta["n_worlds_final"] = base.n_worlds()

    true_virus = VIRUS in (state.script.rule_xs or ())
    # 事件の日→そのループの発生有無
    for s in rec.seats:
        lp, dy, tgt = s["loop"], s["day"], s["target"]
        pls = _placements_at(history, lp, dy)
        mm_on_tgt = [p["card"] for p in pls
                     if p.get("owner") == "mastermind"
                     and p.get("target") == tgt and p.get("target_kind") == "character"]
        s["mm_cards_on_target"] = mm_on_tgt
        # D4＝同ターンの他席が同じ対象へ打ちたかった（options に在った）最高点
        d4 = None
        for row in rec.turn_opts.get(f"{lp}:{dy}", []):
            if row["seat"] == s["seat"]:
                continue
            for _cd, t2, k2, sc in row["opts"]:
                if t2 != tgt or k2 != "character":
                    continue
                v = -1.0 if sc is None else float(sc)
                d4 = max(d4, v) if d4 is not None else v
        s["d4_other_seat_best_on_target"] = d4

        if s["card"] == UNREST_PLUS:
            if "不安禁止" in mm_on_tgt:
                s["A"] = "blocked"      # ★カードが解決していない＝空振り
                continue
            i0 = idxs.get((lp, dy))
            if i0 is None:              # pragma: no cover
                s["A"] = "no_reveal"
                continue
            H1, st = _rewrite_history(history, i0, lp, tgt, rec.snap7,
                                      unrest_threshold_of)
            s["A_status"] = st
            if st != "ok":
                s["A"] = st
                continue
            b1 = _mk_belief(cast, incidents, set_name)
            b1.observe(H1)
            d = _l1(M0, b1.role_marginals())
            s["A_l1"] = round(d, 9)
            s["A"] = "hit" if d > A_EPS else "no"
            # ★観測の数え上げ＝A=0 の原因を「情報が無い」と「計器が盲目」に分ける材料
            obs = _obs_census(history, i0, lp, tgt)
            s["A_obs"] = obs
            s["A_obs_hot"] = any(r[2] is not None and r[2] >= 3 for r in obs)
            s["A_obs_max"] = max([r[2] for r in obs if r[2] is not None] or [None]) \
                if any(r[2] is not None for r in obs) else None
            # ★計器の感度検査（変異テスト）＝対象の不安を同ループ内で全部 3 にする
            #   （しきい値を**跨がせる**方向。0 側はしきい値を跨がないので検査にならない）
            for _v, _k in ((3, "A_probe_hot_l1"), (0, "A_probe_zero_l1")):
                if not obs:
                    s[_k] = None
                    continue
                H2 = _force_history(history, i0, lp, tgt, _v)
                b2 = _mk_belief(cast, incidents, set_name)
                b2.observe(H2)
                s[_k] = round(_l1(M0, b2.role_marginals()), 9)
            # D2＝事件の eligible に pivotal（発生・不発の両方）。
            #   ★「発生した事件の**真の犯人が対象本人**」なら、その事件は
            #     **自分の `不安+1` が起こした**＝情報以外の効能ではなく**自傷**。
            culp_by_day = {i.day: i.culprit for i in (script_obj.incidents or ())}
            piv_days, caused = [], []
            for e in history:
                if (e.get("event") != "incident" or e.get("loop") != lp
                        or not isinstance(e.get("eligible"), list)
                        or tgt not in e["eligible"]):
                    continue
                th = unrest_threshold_of(tgt)
                u = (rec.snap7.get(f"{lp}:{e.get('day')}") or {}).get(tgt)
                if th is None or u is None or (u - 1) >= th:
                    continue
                piv_days.append(e.get("day"))
                if e.get("occurs") and culp_by_day.get(e.get("day")) == tgt:
                    caused.append(e.get("day"))
            s["D2_elig_pivot"] = bool(piv_days)
            s["D2_pivot_days"] = piv_days
            s["D2_caused_days"] = caused      # ★自分の 不安+1 が発生させた事件の日
            # C＝ウイルス自傷
            s["C_exposed"] = bool(s.get("p_virus", 0) and s["p_virus"] > 0
                                  and s.get("p_person", 0) > 0)
            fired = False
            if true_virus and s["true_role"] == "パーソン":
                for k, m in rec.snap9.items():
                    l2, d2 = k.split(":")
                    if int(l2) == lp and int(d2) >= dy and m.get(tgt, 0) >= 3:
                        fired = True
                        break
            s["C_fired"] = fired
            s["C_death"] = bool(fired and any(
                e.get("event") == "death" and e.get("loop") == lp
                and (e.get("day") or 0) >= dy
                and len(e.get("present") or ()) == 2 and tgt in (e.get("present") or ())
                for e in history))
        else:
            # ---- B＝友好の到達 ----
            abs_ = goodwill_abilities_of(tgt) or []
            hearts = sorted({a["hearts"] for a in abs_})
            s["hearts"] = hearts
            if not hearts:
                s["B"] = "no_ability"
                continue
            h = hearts[0]
            n = 2 if s["card"] == "友好+2" else 1
            s["B_need"] = h
            got = None
            for k in sorted(rec.snap6, key=lambda x: (int(x.split(":")[0]),
                                                      int(x.split(":")[1]))):
                l2, d2 = (int(v) for v in k.split(":"))
                if l2 != lp or d2 < dy:
                    continue
                g = (rec.snap6[k].get(tgt) or [0, 0])[1]
                if g >= h and (g - n) < h:
                    got = d2
                    break
            if got is None:
                s["B"] = "miss"
            elif got == dy:
                s["B"] = "same"     # ★同日集中＝その日のうちに閾値へ届かせた
            else:
                s["B"] = "acc"      # ★積む＝同ループの後日に到達
                s["B_day"] = got
            s["B_used"] = any(e.get("event") == "goodwill_resolved"
                              and e.get("loop") == lp and e.get("character") == tgt
                              for e in history)
    rec.meta.update({"winner": state.winner, "loop_no": state.loop_no,
                     "true_rule_xs": list(state.script.rule_xs or ()),
                     "true_virus": true_virus})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _shard(outdir: str, name: str, seed: int) -> str:
    return os.path.join(outdir, f"{name}__{seed}.json")


def cmd_seats(a) -> int:
    before = defaults_banner()
    print(f"[b253] {knob_baseline_banner()}")
    games = parse_pick(a.pick, a.days)
    os.makedirs(a.outdir, exist_ok=True)
    for name, seed, sc in games:
        p = _shard(a.outdir, name, seed)
        if os.path.exists(p) and not a.force:
            continue
        rec = Rec()
        st = _run(sc, name, seed, a.loops, rec)
        d = rec.to_json()
        d["meta"].update({"script": name, "seed": seed, "days": a.days,
                          "loops": a.loops, "winner": st.winner,
                          "loop_no": st.loop_no})
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        print(f"  {name}#{seed}: 席{rec.c['席（set_card 決定）']} "
              f"記録{len(rec.seats)} 再入食い違い{len(rec.mismatch)} "
              f"winner={st.winner} L{st.loop_no}")
    check_no_knob_writes(before, driver="arena.b253_audit")
    print("[b253] 切替口の健全性検査 ✅（測定前後で1bitも動いていない）")
    return 0


def cmd_verify(a) -> int:
    """★プローブの無害性＝観測あり／なしで棋譜が1ビットも動かないことを確認する。"""
    games = parse_pick(a.pick, a.days)
    bad = 0
    for name, seed, sc in games:
        s0 = _run(sc, name, seed, a.loops, None)
        s1 = _run(sc, name, seed, a.loops, Rec())
        h0 = json.dumps(s0.history, ensure_ascii=False, sort_keys=True)
        h1 = json.dumps(s1.history, ensure_ascii=False, sort_keys=True)
        ok = (h0 == h1 and s0.winner == s1.winner and s0.loop_no == s1.loop_no)
        print(f"  {name}#{seed}: 棋譜一致={ok}")
        bad += 0 if ok else 1
    print(f"[b253] プローブの無害性: 不一致 {bad} 件")
    return 1 if bad else 0


def _load(outdir: str) -> list[Rec]:
    out = []
    for fn in sorted(os.listdir(outdir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(outdir, fn), encoding="utf-8") as f:
            out.append(Rec.from_json(json.load(f)))
    return out


def _pct(a: int, b: int) -> str:
    return f"{100.0*a/b:.1f}%" if b else "—"


def cmd_report(a) -> int:
    recs = _load(a.outdir)
    seats = [s for r in recs for s in r.seats]
    mism = sum(len(r.mismatch) for r in recs)
    un = [s for s in seats if s["card"] == UNREST_PLUS]
    gw = [s for s in seats if s["card"] in GOODWILL_CARDS]
    print(f"== {a.outdir} ==  局数 {len(recs)} ／ 自己検証（採点器の再入性）の食い違い {mism} 件")
    print(f"[A] `不安+1`(キャラ) の実打席 = {len(un)}")
    cnt = Counter(s.get("A") for s in un)
    hit = cnt.get("hit", 0)
    judged = hit + cnt.get("no", 0)
    print(f"    判定可 {judged}／うち **事後確率が動いた（A-hit）= {hit}"
          f"（{_pct(hit, judged)}）**")
    print(f"    判定不能: blocked(不安禁止で空振り) {cnt.get('blocked', 0)} ／ "
          f"clamp {cnt.get('clamp', 0)} ／ branch(事件が発生＝分岐) {cnt.get('branch', 0)}")
    if hit:
        ds = sorted(s["A_l1"] for s in un if s.get("A") == "hit")
        print(f"    A-hit の L1 総変動: 中央 {ds[len(ds)//2]:.4f} ／ 最大 {ds[-1]:.4f}")
    ok = [s for s in un if s.get("A") in ("hit", "no")]
    with_obs = [s for s in ok if s.get("A_obs")]
    hot = [s for s in ok if s.get("A_obs_hot")]
    sens = [s for s in with_obs if (s.get("A_probe_hot_l1") or 0.0) > A_EPS]
    mx = [s.get("A_obs_max") for s in with_obs if s.get("A_obs_max") is not None]
    print(f"    ★内訳: 対象が不安条件つき観測に登場した席 {len(with_obs)}／{len(ok)}"
          f"（{_pct(len(with_obs), len(ok))}）／ うち**不安3以上で登場** {len(hot)}"
          f"／ 観測時の不安の最大値 {max(mx) if mx else '—'}")
    print(f"    ★計器の感度検査（変異テスト＝対象の不安を同ループ全部**3**にする＝"
          f"しきい値を跨がせる）で事後が動く席 = {len(sens)}／{len(with_obs)}"
          f"（{_pct(len(sens), len(with_obs))}）＝**0でなければ計器は盲目ではない**")
    print(f"[B] `友好`(キャラ) の実打席 = {len(gw)}")
    bc = Counter(s.get("B") for s in gw)
    reach = bc.get("same", 0) + bc.get("acc", 0)
    den = reach + bc.get("miss", 0)
    print(f"    到達 {reach}／{den}（{_pct(reach, den)}）＝ "
          f"**同日集中 {bc.get('same', 0)}（{_pct(bc.get('same', 0), den)}）** ／ "
          f"積む {bc.get('acc', 0)}（{_pct(bc.get('acc', 0), den)}）／ "
          f"未到達 {bc.get('miss', 0)}／能力なし {bc.get('no_ability', 0)}")
    used = sum(1 for s in gw if s.get("B") in ("same", "acc") and s.get("B_used"))
    print(f"    到達したうえで **実際に能力が使われた** = {used}"
          f"（到達の {_pct(used, reach)}）")
    print(f"[C] ウイルス自傷: 露出 {sum(1 for s in un if s.get('C_exposed'))} ／ "
          f"実現(不安3到達) {sum(1 for s in un if s.get('C_fired'))} ／ "
          f"直後に2人きり死 {sum(1 for s in un if s.get('C_death'))}")
    print(f"[D1] `不安+1` 席で `友好`(キャラ) オプションが legal に在った = "
          f"{sum(1 for s in un if s.get('gw_top'))}／{len(un)}")
    gaps = [s["played_score"] - s["gw_top"][1] for s in un if s.get("gw_top")]
    if gaps:
        gaps.sort()
        print(f"     その差（不安+1 − 友好最高点）: 中央 {gaps[len(gaps)//2]:.2f} ／ "
              f"最小 {gaps[0]:.2f} ／ 最大 {gaps[-1]:.2f}")
    alt = [s["played_score"] - s["alt_top"][1] for s in un if s.get("alt_top")]
    if alt:
        alt.sort()
        print(f"     次善手との差: 中央 {alt[len(alt)//2]:.2f} ／ 最大 {alt[-1]:.2f}")
    piv = [s for s in un if s.get("D2_elig_pivot")]
    print(f"[D2] `不安+1` が事件の eligible に pivotal だった席 = {len(piv)}"
          f"（うち事件が実際に発生＝branch {cnt.get('branch', 0)}）")
    if a.days:
        from arena.benchmark import benchmark_scripts
        pool = {(n, sd): sc for n, sd, sc in benchmark_scripts(days=a.days)}
        self_caused = 0
        for s in un:
            if s.get("A") != "branch":
                continue
            sc = pool.get((s["script"], s["seed"]))
            if sc is None:
                continue
            if any(i.culprit == s["target"] for i in sc.incidents):
                self_caused += 1
        print(f"     ★（参考・脚本全体での粗い判定）対象が脚本のどこかの犯人"
              f" = {self_caused}／{cnt.get('branch', 0)}")
    caused = [s for s in un if s.get("D2_caused_days")]
    print(f"     ★★**自分の `不安+1` が実際に事件を発生させた席（対象＝その日の真の犯人）**"
          f" = {len(caused)}／{len(un)}")
    d4 = [s for s in un if s.get("d4_other_seat_best_on_target") is not None]
    print(f"[D4] 同ターンの他席も同じ対象へ打てた席 = {len(d4)}／{len(un)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="arena.b253_audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("seats", "verify"):
        p = sub.add_parser(nm)
        p.add_argument("--days", type=int, default=5)
        p.add_argument("--loops", type=int, default=8)
        p.add_argument("--pick", default="all")
        p.add_argument("--outdir", default="/tmp/b253")
        p.add_argument("--force", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--outdir", default="/tmp/b253")
    p.add_argument("--days", type=int, default=0)
    a = ap.parse_args(argv)
    return {"seats": cmd_seats, "verify": cmd_verify, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
