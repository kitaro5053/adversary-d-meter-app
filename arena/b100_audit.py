# -*- coding: utf-8 -*-
"""B-100 Phase 0：混合AI（防御プランナーを制約として使う設計）の**介入率**を実測する監査。

★本モジュールは**計測専用**。AIの意思決定には一切触れない
（`agents.heuristic_protagonist.B100_HOOK` は既定 None＝本モジュールを import しない限り
 呼ばれない。フックは決定が確定した後に呼ばれ、戻り値は使われない）。

## 何を測るか（チケット B-100 Phase 0）

各ターン（loop, day）について：

1. **θ別の致命脅威本数**＝`defense_plan.plan_for_belief` が挙げた脅威のうち
   `fatal` かつ `prob >= θ`（θ∈{0.5,0.67,0.8,0.9,1.0}）。
2. **「確度100%級」**＝ユーザー裁定の操作的定義
   ＝「実在確度が最大級」×「関連する事件の**犯人候補が2人以下**」。
   ここでは *実在確度が最大級* を **prob ≥ 0.9** と読み、犯人候補数は
   `belief.culprit_candidates()` の該当日の候補集合サイズで測る
   （事件に紐づかない脅威＝KP/SK/ラバーズ系は「関連する事件」が無い＝
    別枠 `no_incident` として集計し、100%級には数えない）。
3. **AIが実際に打った3枚がそれを覆っていたか**
   ＝脅威の「折り手（Break）」のいずれかと (card,target,target_kind) が一致するか。
   覆っていない致命脅威が残る＝**介入が発生するターン**。
4. **強制で奪われる席数**＝残った脅威を貪欲 hitting set で覆うのに要する席数（上限3）。
5. **奪われる席に何が入っていたか**＝そのターンにAIが実際に打った手のうち、
   どの致命脅威も覆っていない手を**点数の低い順**に必要枚数だけ取り、
   カード種別×点数帯で集計する（★B-99の3日級崩壊の機序＝押し出された席の中身）。
6. **空振り率**＝介入で打つことになる「覆う手」が、公開情報だけで**確実にゼロ効果**と
   言えるか（B-93 の接地＝板への暗躍禁止で mm札が無い(void) or 供給がクロマク/黒猫/噂由来／
   B-28 の `card_effect.noop_reason`＝キャラ対象の空振り）。
7. **鉄則（同型の敗北の反復）の発火率**＝B-95 の署名機構（公開情報のみ）を再実装し、
   「過去に同型の敗北を経験しており、今まさにその筋が立っている」ターンを数える。

## 既知の限界（推測を断定にしないための明示）

- 折り手（Break）は**その席の手札**から生成される。3席分のスナップショットを
  (kind,label) で束ねて union を取っているが、「3席の手札を合わせても打てない手」は
  そもそも Break にならない＝**打てる手だけを数えている**。
- 席数は「その脅威を1つ折る最安手」の貪欲被覆＝**下界**（実戦では折り手が空振り・
  追撃されうるので、真に必要な席はこれ以上になりうる）。
- 「確度100%級」の *最大級* を prob≥0.9 と読んだのは本監査の操作化であって、
  ユーザー裁定の文言そのものではない（θ表と併記して読み替えられるようにしてある）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_audit --days 3 --out r3.json
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_audit --days 5 --out r5.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents import heuristic_protagonist as _hp
# ★B-100 Phase 1（2026-07-29）：述語・署名機構・型の写像は `agents/b100_mix.py` へ移設して
#   **単一ソース**にした（混合AI本体と監査が同じ判定を使う）。ここは薄い別名だけ持つ。
from agents.b100_mix import (
    KIND_LOSS_TYPE as _KIND_LOSS_TYPE,
)
from agents.b100_mix import (
    futile_reason as _futile,
)
from agents.b100_mix import (
    loss_signatures,
    noop_ctx_for,
)
from agents.b100_mix import (
    rumor_prob as _rumor_p,
)
from agents.b100_mix import (
    threat_keys as _threat_keys,
)
from sim import run_game

_BOARDS = ("病院", "神社", "都市", "学校")
THETAS = (0.5, 0.67, 0.8, 0.9, 1.0)
#: 「実在確度が最大級」の操作化（本監査の読み替え。§既知の限界）。
TOP_PROB = 0.9
#: 「関連する事件の犯人候補が N 人以下」（ユーザー裁定＝2）。
CULPRIT_MAX = 2

#: 事件に紐づく脅威の kind（＝「関連する事件」が定義できる＝犯人候補数を問える）。
_INCIDENT_KINDS = ("incident_vip", "remote_murder_vip", "butterfly",
                   "hospital_protagonist")

def _card_class(card: str) -> str:
    if card == "移動禁止":
        return "移動禁止"
    if card == "暗躍禁止":
        return "暗躍禁止"
    if card.startswith("移動"):
        return "移動"
    if card.startswith("友好"):
        return "友好"
    if card.startswith("不安"):
        return "不安"
    return card


def _score_band(s: float) -> str:
    for lo in (100, 90, 80, 70, 60, 50, 40):
        if s >= lo:
            return f"{lo}+"
    return "<40"


# ---------------------------------------------------------------------------
# 記録器（フック本体）
# ---------------------------------------------------------------------------
class Recorder:
    """`set_card` の決定確定直後に呼ばれ、ターン単位の観測行を積む。"""

    def __init__(self, game: str, seed: int):
        self.game, self.seed = game, seed
        self.turns: list[dict] = []
        self._cur_key = None
        self._cur = None

    # -- フック署名 --------------------------------------------------------
    def __call__(self, agent, view, options, chosen, score_fn):
        key = (view.get("loop"), view.get("day"))
        if key != self._cur_key:
            self._flush()
            self._cur_key = key
            self._cur = self._new_turn(agent, view)
        cur = self._cur
        if cur is None:
            return
        # 折り手の union（席ごとに手札が違う＝(kind,label) で束ねる）
        stash = getattr(agent, "_b100_plan", None)
        if stash is not None:
            for t in stash[0]:
                tk = (t.kind, t.label)
                rec = cur["threats"].get(tk)
                if rec is None:
                    rec = {"kind": t.kind, "label": t.label, "prob": round(t.prob, 4),
                           "fatal": bool(t.fatal), "timing": t.timing,
                           "breaks": {}, "n_conditions": len(t.conditions)}
                    cur["threats"][tk] = rec
                for c in t.conditions:
                    for b in c.breaks:
                        rec["breaks"][(b.card, b.target, b.target_kind)] = {
                            "cost": b.cost, "robust": bool(b.robust)}
        try:
            sc = float(score_fn(chosen))
        except Exception:
            sc = float("nan")
        cur["actual"].append({"seat": view.get("seat"), "card": chosen["card"],
                              "target": chosen["target"],
                              "target_kind": chosen.get("target_kind"),
                              "score": round(sc, 2)})

    # -- 内部 --------------------------------------------------------------
    def _new_turn(self, agent, view) -> dict:
        try:
            roles = agent._belief.role_marginals()
        except Exception:
            roles = {}
        try:
            culprits = agent._belief.culprit_candidates()
        except Exception:
            culprits = {}
        return {
            "game": self.game, "seed": self.seed,
            "loop": view.get("loop"), "day": view.get("day"),
            "threats": {}, "actual": [],
            "_view": view, "_agent": agent, "_roles": roles,
            "culprits": {int(d): sorted(s) for d, s in (culprits or {}).items()},
            "cast": [c.get("name") for c in view.get("characters", [])],
            "incident_days": {int(i["day"]): i["name"]
                              for i in view.get("incidents", []) or []},
            "sigs": loss_signatures(view.get("history", []) or []),
        }

    def _flush(self):
        if self._cur is None:
            return
        self.turns.append(_finalize_turn(self._cur))
        self._cur = None

    def close(self):
        self._flush()


def _finalize_turn(cur: dict) -> dict:
    """ターンを「素の観測行」に落とす（θ非依存＝後段でθを振れる形にする）。"""
    view, agent, roles = cur["_view"], cur["_agent"], cur["_roles"]
    noop_ctx = noop_ctx_for(agent, view)
    rumor_p = _rumor_p(agent)
    inc_names = set(cur["incident_days"].values())
    cast = cur["cast"]
    # 過去の敗北署名（今ループより前のみ＝未来を見ない）
    past = {lp: s for lp, s in cur["sigs"].items()
            if lp is not None and lp < (cur["loop"] or 0)}
    # 敗北の型ごとに「キー→その型で何ループ負けたか」を数える（同型の反復＝鉄則の材料）。
    past_keys: dict[str, Counter] = defaultdict(Counter)
    for s in past.values():
        ty = s["type"]
        if ty == "loop_end_cond":
            for b in s["boards"]:
                past_keys[ty][b] += 1
            for n in s["anyaku_chars"]:
                past_keys[ty][n] += 1
        else:
            for n in s["fatal_deaths"]:
                past_keys[ty][n] += 1
        # 事件名は型に依らず「同じ筋の入り方」の材料（発生した事件は公開）
        for _d, n in s["incidents"]:
            past_keys[ty][n] += 1

    actual_keys = {(a["card"], a["target"], a["target_kind"]) for a in cur["actual"]}
    out_threats = []
    for (_k, _l), t in cur["threats"].items():
        brks = [{"card": c, "target": tg, "target_kind": tk, **v}
                for (c, tg, tk), v in t["breaks"].items()]
        for b in brks:
            b["futile"] = _futile(view, agent, roles, b["card"], b["target"],
                                  b["target_kind"], noop_ctx, rumor_p)
        covered_by = [b for b in brks
                      if (b["card"], b["target"], b["target_kind"]) in actual_keys]
        keys = _threat_keys(t["label"], cast, inc_names)
        # 関連する事件の犯人候補数（事件系の脅威のみ）
        n_culprit = None
        inc_day = None
        if t["kind"] in _INCIDENT_KINDS:
            for d, nm in cur["incident_days"].items():
                if nm in keys["incidents"] or (not keys["incidents"] and d == cur["day"]):
                    if d >= (cur["day"] or 0):
                        inc_day = d if inc_day is None else min(inc_day, d)
            if inc_day is not None:
                n_culprit = len(cur["culprits"].get(inc_day, []))
        # ★鉄則の材料＝「同じ敗北の型」×「同じキー（板／決定打の死者／事件名）」が
        #   過去に何ループ出たか。型が違えば同型とは呼ばない（＝過剰検出を抑える）。
        ty = _KIND_LOSS_TYPE.get(t["kind"])
        ctr = past_keys.get(ty, Counter())
        cand = ([ctr[b] for b in keys["boards"]] if ty == "loop_end_cond" else [])
        cand += [ctr[n] for n in keys["chars"]] + [ctr[n] for n in keys["incidents"]]
        rep = max(cand + [0])
        out_threats.append({
            "kind": t["kind"], "label": t["label"], "prob": t["prob"],
            "fatal": t["fatal"], "timing": t["timing"],
            "defendable": bool(brks), "n_breaks": len(brks),
            "breaks": brks, "covered": bool(covered_by),
            "n_culprit": n_culprit, "inc_day": inc_day, "repeat": rep,
        })
    return {
        "game": cur["game"], "seed": cur["seed"], "loop": cur["loop"],
        "day": cur["day"], "n_past_losses": len(past),
        "actual": cur["actual"], "threats": out_threats,
    }


# ---------------------------------------------------------------------------
# 1局を監査
# ---------------------------------------------------------------------------
def audit_game(script, seed: int, name: str, loops: int = 8) -> tuple[list[dict], int, str]:
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    rec = Recorder(name, seed)
    prev = _hp.B100_HOOK
    _hp.B100_HOOK = rec
    try:
        state, _log = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        _hp.B100_HOOK = prev
    rec.close()
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw = loops + 1
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        ltw, outcome = loops + 1, "loss"
    lost = {e.get("loop") for e in state.history
            if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}
    for t in rec.turns:
        t["loop_lost"] = t["loop"] in lost
    return rec.turns, ltw, outcome


# ---------------------------------------------------------------------------
# 集計（θを振る）
# ---------------------------------------------------------------------------
def _greedy_seats(threats: list[dict], seats: int = 3) -> tuple[int, list[dict]]:
    """未被覆の脅威を最安手で貪欲に覆う（同一 target は1手・暗躍禁止は1枚）。"""
    picks: list[dict] = []
    used_keys: set = set()
    used_tgt: set = set()
    kinshi = 0
    for t in sorted(threats, key=lambda x: -x["prob"]):
        cheap = sorted([b for b in t["breaks"]], key=lambda b: b["cost"])
        if any((b["card"], b["target"], b["target_kind"]) in used_keys for b in cheap):
            continue
        for b in cheap:
            if (b["target"], b["target_kind"]) in used_tgt:
                continue
            if b["card"] == "暗躍禁止" and kinshi >= 1:
                continue
            if len(picks) >= seats:
                break
            picks.append(b)
            used_keys.add((b["card"], b["target"], b["target_kind"]))
            used_tgt.add((b["target"], b["target_kind"]))
            if b["card"] == "暗躍禁止":
                kinshi += 1
            break
        if len(picks) >= seats:
            break
    return len(picks), picks


def aggregate(turns: list[dict], thetas=THETAS) -> dict:
    n_turns = len(turns)
    out: dict = {"n_turns": n_turns, "theta": {}}
    # 鉄則（同型の敗北の反復）
    iron = {"turns_with_past_loss": 0}
    iron_kinds = Counter()
    for lv, tag in ((1, "rep1"), (2, "rep2"), (3, "rep3")):
        iron[tag + "_live"] = 0
        iron[tag + "_uncovered"] = 0
        iron[tag + "_uncov_p50"] = 0
    for t in turns:
        if t["n_past_losses"] > 0:
            iron["turns_with_past_loss"] += 1
        f = [x for x in t["threats"] if x["fatal"]]
        for lv, tag in ((1, "rep1"), (2, "rep2"), (3, "rep3")):
            hit = [x for x in f if x["repeat"] >= lv]
            if hit:
                iron[tag + "_live"] += 1
                unc = [x for x in hit if not x["covered"] and x["defendable"]]
                if unc:
                    iron[tag + "_uncovered"] += 1
                if any(x["prob"] >= 0.5 for x in unc):
                    iron[tag + "_uncov_p50"] += 1
        for x in f:
            if x["repeat"] >= 1:
                iron_kinds[x["kind"]] += 1
    iron["live_kinds"] = dict(iron_kinds.most_common())
    out["iron_rule"] = iron

    # 素の分布（θに依らない診断）
    kind_all = Counter()
    kind_top = Counter()
    prob_hist = Counter()
    culp_hist = Counter()
    for t in turns:
        for x in t["threats"]:
            if not x["fatal"]:
                continue
            kind_all[x["kind"]] += 1
            prob_hist[f"{int(x['prob'] * 10) / 10:.1f}"] += 1
            if x["prob"] >= TOP_PROB - 1e-9:
                kind_top[x["kind"]] += 1
            if x["kind"] in _INCIDENT_KINDS:
                culp_hist[(x["kind"], "なし" if x["n_culprit"] is None
                           else str(x["n_culprit"]))] += 1
    out["diag"] = {
        "fatal_kind": dict(kind_all.most_common()),
        "fatal_prob_hist": {k: prob_hist[k] for k in sorted(prob_hist)},
        "top_prob_kind": dict(kind_top.most_common()),
        "incident_culprit_hist": {f"{k}/{n}": v
                                  for (k, n), v in culp_hist.most_common()},
    }

    for th in thetas:
        n_th = Counter()          # ターンあたりの本数分布
        turns_with = 0
        turns_intervene = 0
        seat_dist = Counter()
        displaced = Counter()     # (card_class, band) -> 枚
        displaced_class = Counter()
        forced_breaks = Counter()
        forced_futile = Counter()
        futile_by_class = Counter()
        n100_dist = Counter()
        turns_2x100 = 0
        n100b_dist = Counter()
        turns_2x100b = 0
        kinds_uncov = Counter()
        no_incident = 0
        undefendable = 0
        n_uncov_dist = Counter()
        uncov_prob = Counter()
        culprit_dist = Counter()
        no_room = 0
        in_lost = 0
        rep1 = rep2 = 0
        rep_games: set = set()
        samples: list = []
        for t in turns:
            sel = [x for x in t["threats"] if x["fatal"] and x["prob"] >= th - 1e-9]
            n_th[len(sel)] += 1
            if sel:
                turns_with += 1
            undefendable += sum(1 for x in sel if not x["defendable"])
            # 確度100%級
            top = [x for x in sel if x["prob"] >= TOP_PROB - 1e-9]
            n100 = sum(1 for x in top
                       if x["n_culprit"] is not None and x["n_culprit"] <= CULPRIT_MAX)
            # 定義B（相対）＝「最大級」をそのターンの致命脅威の最大 prob と読む
            if sel:
                _mx = max(x["prob"] for x in sel)
                n100b = sum(1 for x in sel if x["prob"] >= _mx - 1e-9
                            and x["n_culprit"] is not None
                            and x["n_culprit"] <= CULPRIT_MAX)
            else:
                n100b = 0
            n100b_dist[n100b] += 1
            if n100b >= 2:
                turns_2x100b += 1
            no_incident += sum(1 for x in top if x["n_culprit"] is None)
            for x in top:
                culprit_dist[("なし" if x["n_culprit"] is None
                              else ("3+" if x["n_culprit"] > 2 else str(x["n_culprit"])))] += 1
            n100_dist[n100] += 1
            if n100 >= 2:
                turns_2x100 += 1
            uncov = [x for x in sel if x["defendable"] and not x["covered"]]
            n_uncov_dist[len(uncov)] += 1
            if not uncov:
                continue
            turns_intervene += 1
            if t.get("loop_lost"):
                in_lost += 1
            _mr = max((x["repeat"] for x in uncov), default=0)
            if _mr >= 1:
                rep1 += 1
                rep_games.add((t["game"], t["seed"]))
            if _mr >= 2:
                rep2 += 1
            if len(samples) < 40:
                samples.append(f"{t['game']} s{t['seed']} L{t['loop']}D{t['day']}"
                               f" {'敗' if t.get('loop_lost') else '防'}"
                               f" [{','.join(x['kind'] for x in uncov)}]")
            for x in uncov:
                kinds_uncov[x["kind"]] += 1
                uncov_prob[f"{int(x['prob'] * 10) / 10:.1f}"] += 1
            k, picks = _greedy_seats(uncov, seats=3)
            seat_dist[k] += 1
            for b in picks:
                forced_breaks[_card_class(b["card"])] += 1
                if b["futile"]:
                    forced_futile[_card_class(b["card"])] += 1
                    futile_by_class[b["futile"][:24]] += 1
            # 押し出される席＝致命脅威を1本も覆っていない実手を点数の低い順に k 枚
            cov_keys = set()
            for x in t["threats"]:
                if not x["fatal"]:
                    continue
                for b in x["breaks"]:
                    cov_keys.add((b["card"], b["target"], b["target_kind"]))
            free = [a for a in t["actual"]
                    if (a["card"], a["target"], a["target_kind"]) not in cov_keys]
            free.sort(key=lambda a: a["score"])
            if len(free) < k:
                no_room += 1      # 3席とも致命脅威を覆っており、空けられる席が足りない
            for a in free[:k]:
                displaced[(_card_class(a["card"]), _score_band(a["score"]))] += 1
                displaced_class[_card_class(a["card"])] += 1
        out["theta"][str(th)] = {
            "turns_with_threat": turns_with,
            "turns_intervene": turns_intervene,
            "intervene_rate": round(turns_intervene / n_turns, 4) if n_turns else 0.0,
            "n_threat_dist": {str(k): v for k, v in sorted(n_th.items())},
            "seat_dist": {str(k): v for k, v in sorted(seat_dist.items())},
            "seats_total": sum(k * v for k, v in seat_dist.items()),
            "undefendable_threats": undefendable,
            "uncovered_kinds": dict(kinds_uncov.most_common()),
            "n100_dist": {str(k): v for k, v in sorted(n100_dist.items())},
            "turns_2x100": turns_2x100,
            "n100b_dist": {str(k): v for k, v in sorted(n100b_dist.items())},
            "turns_2x100b": turns_2x100b,
            "top_prob_no_incident": no_incident,
            "top_prob_culprit_dist": dict(culprit_dist.most_common()),
            "n_uncovered_dist": {str(k): v for k, v in sorted(n_uncov_dist.items())},
            "uncovered_prob_hist": {k: uncov_prob[k] for k in sorted(uncov_prob)},
            "turns_no_room": no_room,
            "intervene_in_lost_loop": in_lost,
            "intervene_repeat1": rep1,
            "intervene_repeat2": rep2,
            "intervene_repeat_games": len(rep_games),
            "samples": samples,
            "forced_breaks": dict(forced_breaks.most_common()),
            "forced_futile": dict(forced_futile.most_common()),
            "futile_reasons": dict(futile_by_class.most_common()),
            "futile_rate": (round(sum(forced_futile.values())
                                  / sum(forced_breaks.values()), 4)
                            if forced_breaks else 0.0),
            "displaced_class": dict(displaced_class.most_common()),
            "displaced": {f"{c}/{b}": v for (c, b), v in
                          sorted(displaced.items(), key=lambda kv: -kv[1])},
        }
    return out


def run_audit(days: int = 3, loops: int = 8, verbose: bool = True,
              limit: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts
    t0 = time.time()
    turns: list[dict] = []
    rows = []
    scripts = benchmark_scripts(days=days)
    if limit:
        scripts = scripts[:limit]
    for name, seed, sc in scripts:
        ts, ltw, outcome = audit_game(sc, seed, name, loops=loops)
        turns.extend(ts)
        rows.append({"script": name, "seed": seed, "loops_to_win": ltw,
                     "outcome": outcome})
        if verbose:
            print(f"  {name} s{seed}: ltw={ltw} {outcome} turns={len(ts)}",
                  file=sys.stderr, flush=True)
    agg = aggregate(turns)
    vals = [r["loops_to_win"] for r in rows]
    agg["days"] = days
    agg["n_games"] = len(rows)
    agg["mean_loops_to_win"] = round(sum(vals) / len(vals), 3) if vals else None
    agg["outcomes"] = dict(Counter(r["outcome"] for r in rows))
    agg["distribution"] = {str(k): v for k, v in sorted(Counter(vals).items())}
    agg["hashseed"] = os.environ.get("PYTHONHASHSEED", "(未固定!)")
    agg["elapsed_sec"] = round(time.time() - t0, 1)
    agg["rows"] = rows
    return agg


def format_report(rep: dict) -> str:
    L = [f"B-100 Phase 0 介入率監査: {rep['days']}日級 {rep['n_games']}局 "
         f"／観測ターン {rep['n_turns']} ／PYTHONHASHSEED={rep['hashseed']}",
         f"  自測ベンチ: 平均{rep['mean_loops_to_win']} "
         f"結末={rep['outcomes']} 分布={rep['distribution']}",
         "",
         "  θ | 脅威在ターン | 介入ターン | 介入率 | 席1 | 席2 | 席3 | 総席 | 空振り率",
         "  --|--------------|-----------|--------|-----|-----|-----|------|--------"]
    for th, d in rep["theta"].items():
        sd = d["seat_dist"]
        L.append(f"  {th:>4} | {d['turns_with_threat']:>12} | {d['turns_intervene']:>9}"
                 f" | {d['intervene_rate']:>6.1%} | {sd.get('1',0):>3} | {sd.get('2',0):>3}"
                 f" | {sd.get('3',0):>3} | {d['seats_total']:>4} | {d['futile_rate']:>6.1%}"
                 f" | 敗ループ内 {d['intervene_in_lost_loop']}"
                 f" | 反復≥1 {d['intervene_repeat1']}（{d['intervene_repeat_games']}局）"
                 f" 反復≥2 {d['intervene_repeat2']}")
    dg = rep["diag"]
    L += ["",
          f"  致命脅威の種別: {dg['fatal_kind']}",
          f"  致命脅威の実在度ヒスト: {dg['fatal_prob_hist']}",
          f"  実在度≥{TOP_PROB} の種別: {dg['top_prob_kind']}",
          f"  事件系脅威の犯人候補数: {dg['incident_culprit_hist']}"]
    ir = rep["iron_rule"]
    L += ["",
          f"  鉄則（同型敗北の反復）: 過去敗北のあるターン {ir['turns_with_past_loss']}"
          f"／{rep['n_turns']}",
          f"    1回以上: 立っている {ir['rep1_live']}（未被覆 {ir['rep1_uncovered']}"
          f"／うち prob≥0.5 {ir['rep1_uncov_p50']}）",
          f"    2回以上: 立っている {ir['rep2_live']}（未被覆 {ir['rep2_uncovered']}"
          f"／うち prob≥0.5 {ir['rep2_uncov_p50']}）",
          f"    3回以上: 立っている {ir['rep3_live']}（未被覆 {ir['rep3_uncovered']}"
          f"／うち prob≥0.5 {ir['rep3_uncov_p50']}）",
          f"    種別: {ir['live_kinds']}"]
    for key in ("0.5", "0.67"):
        d = rep["theta"].get(key, {})
        if not d:
            continue
        L += ["",
              f"  θ={key} の内訳: 押し出される席={d['displaced_class']}"
              f"（点数帯 {d['displaced']}）",
              f"    席を空けられなかったターン={d['turns_no_room']}",
              f"    覆う手の種別={d['forced_breaks']} うち空振り={d['forced_futile']}",
              f"    空振りの理由={d['futile_reasons']}",
              f"    未被覆脅威の種別={d['uncovered_kinds']}",
              f"    未被覆脅威の実在度ヒスト={d['uncovered_prob_hist']}",
              f"    未被覆本数の分布={d['n_uncovered_dist']}",
              f"    防御不能な脅威（折り手なし）={d['undefendable_threats']}",
              f"    確度最大級(prob≥{TOP_PROB})の犯人候補数={d['top_prob_culprit_dist']}",
              f"    100%級A(絶対 prob≥{TOP_PROB})の本数分布={d['n100_dist']}"
              f"（2本以上={d['turns_2x100']}）",
              f"    100%級B(そのターンの最大prob)の本数分布={d['n100b_dist']}"
              f"（2本以上={d['turns_2x100b']}）"]
    L.append(f"  所要 {rep['elapsed_sec']}秒")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-100 Phase 0：介入率の実測（計測専用）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="先頭N局のみ（デバッグ用）")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    rep = run_audit(days=args.days, loops=args.loops, verbose=not args.quiet,
                    limit=args.limit)
    print(format_report(rep))
    if args.out:
        slim = {k: v for k, v in rep.items()}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
