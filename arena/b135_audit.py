# -*- coding: utf-8 -*-
"""B-135(c)：**板の敗北を折る手（`暗躍禁止→板`）が候補にあるのに席を取られた局面**の
数え上げ（挙動不変・計測のみ＝`arena/b130_audit.py`／`arena/b134_audit.py` と同型）。

問い（バックログ §7・ユーザー裁定 2026-08-02）＝「板の敗北が最上位帯の別脅威と同じ1枚を
奪い合ったとき、板を強制的に勝たせるべきか」。まず **その競合が実在するかを数え上げる**。

## 計測の仕組み（★b130/b132/b134 と違う点）

`B100_HOOK` は**選択の後**に呼ばれる（`heuristic_protagonist.py:5647`）。その手前で
`self._kinshi_used = True`（`:5599-5600`）が立つため、**フックの中で `score()` を
呼び直すと 暗躍禁止 の点数が全部 `自滅回避=-100` に化ける**（初版で実測）。
∴ 本監査は `agents/debug.ProbedProtagonist` と同じ **spy-max**（モジュール名前空間の
`max` を一時差し替えて `max(options, key=score)` を横取り）で、
**決定時点の点数**をそのまま捕まえる。捕まえるのは `args[0] is options` の呼び出しだけ＝
別の `max` を誤って拾わない。`B100_HOOK` は防御プランナーの結果を
`self._b100_plan` に置かせるためだけに no-op で立てる（既存の計測専用フック）。
どちらもAIの選択には一切影響しない（戻り値不使用・状態不変）。

## Phase 1（競合の数え上げ）
  1. 各席で、防御プランナーが立てた `board_defeat` 脅威（`self._b100_plan`）のうち
     **`暗躍禁止→その板` が options に在る**ものを列挙する。
  2. その手の点数と、実際に選ばれた手の点数を記録し、**選ばれなかった＝席を取られた**
     ものを競合として数える（★暗躍禁止が同ターン消費済み＝点数 `自滅回避` の席は
     「折り手が使えない席」として分離する）。
  3. 分類：(A) 勝った相手も板ガード（`暗躍禁止`→別の板）
           (B) 勝った相手はキャラ系の最上位手（target_kind=character かつ点数≥`TOP_BAND`）
           (C) それ以外。
  4. ★**ターン単位の再判定**：主人公の `暗躍禁止` は1ターン1枚（`rules/10`・自滅回避）。
     同ターンの別席が同じ板を守ったなら「席は取られたが板は守られた」＝真の競合ではない。
  5. 結末＝局の outcome ＋ **その板がそのループ終了時に暗躍2へ到達したか**
     （`loop_board` イベント）＋ **その板の敗北で実際に落ちたか**（`defeat` の reason）。

## Phase 2（除去役の勘定）
  - 板の暗躍を除去できる友好能力の担い手（KB接地＝下記 `_BOARD_PURIFIERS`）が
    その板に対して**今**居るか／解禁されているか／拒否されうるかを席ごとに記録する。
  - 実際の使用/拒否（`goodwill_used`/`goodwill_refused`）も局ごとに数える。

CLI（前面実行）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b135_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b135_audit --days 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import replace

import agents.heuristic_protagonist as HP
from agents import HeuristicMastermind
from arena.benchmark import benchmark_scripts
from engine.data import (UNREFUSABLE_ABILITY_CHARS, ability_kind,
                         goodwill_abilities_of)
from sim import run_game

_builtin_max = max

# ---------------------------------------------------------------------------
# KB接地：**ボードの暗躍**を除去できる友好能力（rules/20_goodwill_abilities.md）
# ---------------------------------------------------------------------------
# `ability_kind(...) == "暗躍除去"` は3件（`engine/data.py:390-392`）だが、
# **ボードを対象にできるのは2件だけ**：
#   ・巫女「神社の暗躍除去」  ♡3・回数無制限 … rules/20:40, 129, 131-132
#       [主]巫女が**神社**にいないと使えない ／ [脚]神社に暗躍があるなら1つ取り除く
#   ・神格「暗躍除去（キャラ/ボード）」♡5・回数無制限 … rules/20:61, 148, 153-154
#       [主]神格と同一エリアのキャラ1人か、**神格のいるボード**を選ぶ
#   ・転校生「暗躍除去＋友好付与」♡2 … rules/20:56, 122-125 は **キャラ限定**
#       （「転校生と同一エリアにいる**他のキャラ1人**を選ぶ」）＝ボードには撃てない＝対象外。
# ★この表は監査専用（AIの判断経路は一切読まない）。
_BOARD_PURIFIERS = {
    ("巫女", "神社の暗躍除去"): "神社",          # 発動場所＝神社・対象＝神社（固定）
    ("神格", "暗躍除去（キャラ/ボード）"): None,   # 対象＝神格の居るボード（可変）
}

#: (B) の判定に使う「最上位帯」の下限（`PRIORITY["ボード封じ_危険"]=88.0` と同値＝
#: `agents/heuristic_protagonist.py:134`）。分類の閾値であって挙動には無関係。
TOP_BAND = 88.0

#: 「暗躍禁止が同ターン消費済み」の判定（`PRIORITY["自滅回避"]=-100`）。
_SPENT_SCORE = -50.0

#: 友好無視の疑いの閾値（`defense_plan.SUPPRESSOR_IGNORE_P` と同値の観測用。
#: ★要確認＝この閾値は裁定を経ていない観測用の値・B-130 §申し送りと同じ）。
IGNORE_P = 0.15


def board_purifiers(view: dict, area: str, roles: dict | None) -> list[dict]:
    """`area` の**ボード暗躍**を取り除ける担い手（解禁前・拒否されうる者も明細で返す）。"""
    out: list[dict] = []
    for c in view.get("characters", []) or []:
        if not c.get("alive", True):
            continue
        name = c.get("name")
        for ab in goodwill_abilities_of(name) or []:
            key = (name, ab["name"])
            if key not in _BOARD_PURIFIERS:
                continue
            if ability_kind(name, ab["name"]) != "暗躍除去":
                continue          # KB表と engine 表の齟齬（起きないはずの安全弁）
            fixed = _BOARD_PURIFIERS[key]
            here = c.get("area")
            target_board = fixed if fixed is not None else here
            if target_board != area:
                continue          # この板は撃てない（巫女＝神社限定／神格＝自ボード）
            if fixed is not None and here != fixed:
                continue          # 巫女は神社に居ないと使えない（rules/20:131）
            gw = int(c.get("goodwill", 0) or 0)
            need = int(ab.get("hearts", 99))
            ign = 0.0
            if roles:
                from agents.defense_plan import _friendship_ignore_prob
                ign = float(_friendship_ignore_prob(roles, name))
            out.append({
                "name": name, "ability": ab["name"], "hearts": need,
                "gw": gw, "funded": gw >= need,
                "once": bool(ab.get("once_per_loop")),
                "unrefusable": name in UNREFUSABLE_ABILITY_CHARS,
                "ignore_p": round(ign, 3),
            })
    return out


def _board_of_threat(t) -> str | None:
    """`_threat_board_defeat` のラベル `f"{area}のボード敗北"` から板名を取る。"""
    lab = getattr(t, "label", "") or ""
    suf = "のボード敗北"
    return lab[:-len(suf)] if lab.endswith(suf) else None


def seat_facts(agent, view: dict, options: list, best: dict,
               scored: list) -> dict | None:
    """1席分の事実。板敗北の折り手が候補に在る席だけ dict・それ以外 None。純粋な観測。

    `scored` ＝ spy-max が捕まえた **決定時点の** [(点数, option), ...]。
    """
    plan = getattr(agent, "_b100_plan", None)
    if not plan:
        return None
    threats, _p = plan
    probs = getattr(agent, "_board_defeat_probs", None) or {}
    try:
        marg = agent._belief.role_marginals()
    except Exception:
        marg = {}
    by_key = {(o["card"], o["target"], o.get("target_kind")): s for s, o in scored}
    best_key = (best.get("card"), best.get("target"), best.get("target_kind"))
    best_s = float(by_key.get(best_key, 0.0))
    rows = []
    for t in threats:
        if getattr(t, "kind", "") != "board_defeat" or getattr(t, "breached", False):
            continue
        area = _board_of_threat(t)
        if area is None:
            continue
        gkey = ("暗躍禁止", area, "board")
        if gkey not in by_key:
            continue                      # 折り手が手札に無い＝競合の対象外
        gs = float(by_key[gkey])
        # ★同じ board_defeat 脅威を折る**別の手**（クロマク剥がしの移動・犯人冷却等）を
        #   選んでいないか＝「席を取られた」ではなく「別の折り手で守った」の判別。
        #   単一ソース＝脅威の条件に積まれた Break（`defense_plan.Threat.conditions`）。
        breaks = {(b.card, b.target, b.target_kind)
                  for c in getattr(t, "conditions", []) or []
                  for b in getattr(c, "breaks", []) or []}
        rows.append({
            "board": area,
            "best_is_other_break": best_key in breaks and best_key != gkey,
            "n_breaks": len(breaks),
            "rule_p": round(float(probs.get(area, 0.0)), 4),
            "threat_p": round(float(getattr(t, "prob", 0.0)), 4),
            "race": bool(getattr(t, "race", False)),
            "cur": int((view.get("board_anyaku") or {}).get(area, 0)),
            "guard_score": round(gs, 2),
            "spent": gs <= _SPENT_SCORE,
            "won": gkey == best_key,
            "margin": round(best_s - gs, 2),
            "purifiers": board_purifiers(view, area, marg),
        })
    if not rows:
        return None
    return {
        "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
        "danger_board": agent._guess_defeat_board(view),
        "odb": getattr(agent, "_observed_defeat_board", None),
        "best": f"{best.get('card')}→{best.get('target')}",
        "best_kind": best.get("target_kind"),
        "best_score": round(best_s, 2),
        "rows": rows,
    }


def classify(seat: dict, row: dict) -> str:
    """勝った相手の分類。

    (S) **同じ板の敗北を別の折り手で守った**（クロマク剥がしの移動・犯人冷却等）＝競合でない
    (A) 勝った相手も板ガード（`暗躍禁止`→別の板）
    (B) 勝った相手はキャラ系の最上位手（target_kind=character かつ点数≥`TOP_BAND`）
    (C) それ以外
    """
    if row.get("best_is_other_break"):
        return "S"
    if seat["best"].startswith("暗躍禁止→") and seat["best_kind"] == "board":
        return "A"
    if seat["best_kind"] == "character" and seat["best_score"] >= TOP_BAND:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# 計測用の主人公（挙動は本体そのもの＝`agents/debug.ProbedProtagonist` と同じ仕掛け）
# ---------------------------------------------------------------------------
class ProbeProtagonist(HP.HeuristicProtagonist):
    """`max(options, key=score)` を横取りして**決定時点の全候補点数**を記録する主人公。

    スコア計算・選択は本体そのもの（`_builtin_max` にそのまま委譲）＝挙動同一。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.seats: list[dict] = []
        self.miss = 0          # spy が捕まえられなかった set_card 席（計画/強制経路）

    def decide(self, view: dict, decision: str, options: list) -> dict:
        cap: dict = {}

        def spymax(*args, **kw):
            if args and args[0] is options and "key" in kw and "scored" not in cap:
                # ★点数は**1回だけ**計算して max の結果もここから返す（`score` を二度
                #   走らせない＝副作用の二重適用を構造的に消す）。`max(it, key=f)` は
                #   **最初の**最大要素を返す仕様＝下のループと同値。
                pairs = [(float(kw["key"](o)), o) for o in args[0]]
                cap["scored"] = pairs
                bi = 0
                for i in range(1, len(pairs)):
                    if pairs[i][0] > pairs[bi][0]:
                        bi = i
                return pairs[bi][1]
            return _builtin_max(*args, **kw)

        had = "max" in HP.__dict__
        prev = HP.__dict__.get("max")
        HP.max = spymax
        try:
            chosen = super().decide(view, decision, options)
        finally:
            if had:
                HP.max = prev
            else:
                del HP.max
        if decision == "set_card":
            if "scored" not in cap:
                self.miss += 1
            else:
                f = seat_facts(self, view, options, chosen, cap["scored"])
                if f is not None:
                    self.seats.append(f)
        return chosen


def _noop_hook(*_a, **_k):
    """`_b100_plan` のスタッシュを有効にするためだけの no-op（戻り値不使用）。"""
    return None


# ---------------------------------------------------------------------------
# 対局の実行
# ---------------------------------------------------------------------------
def run_audit(days: int, loops: int = 8, start: int = 0,
              end: int | None = None) -> list[dict]:
    rows = []
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    for name, seed, sc in scripts[start:end]:
        probe = replace(sc, loops=loops)
        mm = HeuristicMastermind(seed)
        hp = ProbeProtagonist(seed)
        HP.B100_HOOK = _noop_hook
        try:
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                        "p2": hp, "p3": hp})
        finally:
            HP.B100_HOOK = None
        fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not fb:
            outcome, ltw = "defense", state.loop_no
        elif fb:
            outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            ltw = loops + 1
        else:
            outcome, ltw = "loss", loops + 1
        defeats = [{"loop": e.get("loop"), "reason": e.get("reason")}
                   for e in state.secret_log if e.get("event") == "defeat"]
        loop_boards = [{"loop": e.get("loop"),
                        "board_anyaku": dict(e.get("board_anyaku") or {})}
                       for e in state.history if e.get("event") == "loop_board"]
        gw = [{"loop": e.get("loop"), "day": e.get("day"),
               "event": e.get("event"), "character": e.get("character"),
               "ability": e.get("ability")}
              for e in state.history
              if e.get("event") in ("goodwill_used", "goodwill_refused",
                                    "goodwill_resolved")
              and "暗躍除去" in str(e.get("ability", ""))]
        rows.append({"script": name, "seed": seed, "days": days,
                     "outcome": outcome, "loops_to_win": ltw, "miss": hp.miss,
                     "defeats": defeats, "loop_boards": loop_boards,
                     "purify_events": gw, "seats": hp.seats})
        n_conf = sum(1 for s in hp.seats for r in s["rows"]
                     if not r["won"] and not r["spent"])
        print(f"  {name} s{seed}: {outcome} ltw={ltw} 折り手在席{len(hp.seats)}"
              f" 席落ち{n_conf} miss={hp.miss}", flush=True)
    return rows


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def _bucket(rows: list[dict], p_min: float, g_min: float,
            t_min: float = 0.0) -> dict:
    """絞り条件（P(敗北板)>=p_min・板ガード点数>=g_min・threat.prob>=t_min）での集計。

    `t_min` ＝ B-100 の θ（`HeuristicProtagonist.B100_THETA=0.9`・判定は
    `b100_alloc.gate_reason` の `t.prob >= theta - 1e-9`）で絞るための口。
    """
    st = {"tot": 0, "won": 0, "lost": 0, "spent": 0,
          "S": 0, "A": 0, "B": 0, "C": 0, "turn_saved": 0, "turn_lost": 0,
          "reached2": 0, "lost_loop": 0, "detail": []}
    for g in rows:
        bd = [d for d in g["defeats"] if "暗躍≥2" in str(d.get("reason", ""))]
        reach2 = {(lb["loop"], b) for lb in g["loop_boards"]
                  for b, v in lb["board_anyaku"].items() if v >= 2}
        guarded: dict = defaultdict(set)
        for s in g["seats"]:
            if s["best"].startswith("暗躍禁止→") and s["best_kind"] == "board":
                guarded[(s["loop"], s["day"])].add(s["best"].split("→", 1)[1])
        for s in g["seats"]:
            for r in s["rows"]:
                if (r["rule_p"] < p_min or r["guard_score"] < g_min
                        or r["threat_p"] < t_min - 1e-9):
                    continue
                if r["spent"]:
                    st["spent"] += 1
                    continue          # 折り手が同ターン消費済み＝競合の対象外
                st["tot"] += 1
                if r["won"]:
                    st["won"] += 1
                    continue
                st["lost"] += 1
                c = classify(s, r)
                st[c] += 1
                if c == "S":
                    continue          # 同じ板を別の折り手で守った＝競合でない
                if r["board"] in guarded.get((s["loop"], s["day"]), ()):
                    st["turn_saved"] += 1
                    continue
                st["turn_lost"] += 1
                if (s["loop"], r["board"]) in reach2:
                    st["reached2"] += 1
                if any(r["board"] in str(d.get("reason", ""))
                       and d.get("loop") == s["loop"] for d in bd):
                    st["lost_loop"] += 1
                st["detail"].append((g, s, r, c))
    return st


def summarize(rows: list[dict], detail_n: int = 40) -> str:
    outcome_c: Counter = Counter()
    pur_any = pur_funded = pur_ok = pur_blocked = 0
    pur_chars: Counter = Counter()
    gw_used = gw_ref = gw_res = 0
    miss = 0
    bd_games = 0
    for g in rows:
        outcome_c[g["outcome"]] += 1
        miss += g.get("miss", 0)
        if any("暗躍≥2" in str(d.get("reason", "")) for d in g["defeats"]):
            bd_games += 1
        for e in g["purify_events"]:
            gw_used += e["event"] == "goodwill_used"
            gw_ref += e["event"] == "goodwill_refused"
            gw_res += e["event"] == "goodwill_resolved"
        for s in g["seats"]:
            for r in s["rows"]:
                ps = r["purifiers"]
                if not ps:
                    continue
                pur_any += 1
                for p in ps:
                    pur_chars[f"{p['name']}({'解禁' if p['funded'] else '未解禁'})"] += 1
                if any(p["funded"] for p in ps):
                    pur_funded += 1
                    if any(p["funded"] and (p["unrefusable"]
                                            or p["ignore_p"] < IGNORE_P) for p in ps):
                        pur_ok += 1
                    else:
                        pur_blocked += 1
    lines = [
        f"局数 {len(rows)}  outcome={dict(outcome_c)}"
        f"  （板の敗北で落ちたループがある局 {bd_games}）  spy未捕捉席 {miss}",
        "",
        "[Phase 1] 板ガード（暗躍禁止→板）が候補に在った脅威×席の内訳",
        "  絞り                | 対象 | 勝 |席落ち|  S  |  A  |  B  |  C  |"
        "別席が守|ターンでも守れず|板が2到達|その板で敗北|消費済",
    ]
    for label, p_min, g_min, t_min in (
            ("全件", 0.0, -1e9, 0.0),
            ("P>=0.5", 0.5, -1e9, 0.0),
            ("P>=0.9", 0.9, -1e9, 0.0),
            ("ガード>=88", 0.0, 88.0, 0.0),
            ("P>=0.5 かつ >=88", 0.5, 88.0, 0.0),
            ("P>=0.9 かつ >=88", 0.9, 88.0, 0.0),
            ("★θ到達(th>=0.9)", 0.0, -1e9, 0.9),
            ("★θ到達 かつ >=88", 0.0, 88.0, 0.9)):
        st = _bucket(rows, p_min, g_min, t_min)
        lines.append(
            f"  {label:<18} | {st['tot']:>4} | {st['won']:>3} | {st['lost']:>4} |"
            f" {st['S']:>3} | {st['A']:>3} | {st['B']:>3} | {st['C']:>3} |"
            f" {st['turn_saved']:>6} | {st['turn_lost']:>13} |"
            f" {st['reached2']:>7} | {st['lost_loop']:>10} | {st['spent']:>5}")
    lines += [
        "",
        f"[Phase 2] 除去役（ボード暗躍を剥がせる友好能力）が同板に居た脅威×席: {pur_any}"
        f"／うち解禁済み: {pur_funded}"
        f"（拒否されない見込み {pur_ok}／拒否されうる {pur_blocked}）",
        "  除去役の内訳: " + ("  ".join(f"{k}:{v}" for k, v in pur_chars.most_common())
                              or "(なし)"),
        f"  暗躍除去の友好能力の実使用 {gw_used}件"
        f"（解決 {gw_res}／**拒否 {gw_ref}**）",
        "",
        "[代表例] P>=0.5 かつ ガード>=88 の『ターンでも守れなかった』席",
    ]
    st = _bucket(rows, 0.5, 88.0)
    for g, s, r, c in st["detail"][:detail_n]:
        lines.append(
            f"  ({c}) {g['script']} s{g['seed']} L{s['loop']}D{s['day']} {s['seat']}"
            f" 板={r['board']}(現在{r['cur']}) P={r['rule_p']} threat={r['threat_p']}"
            f" 暗躍禁止→{r['board']}={r['guard_score']}"
            f" ／選択={s['best']}({s['best_score']}) 差{r['margin']}"
            f" danger={s['danger_board']} 結末={g['outcome']}"
            f" 除去役={[p['name'] for p in r['purifiers']] or None}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = run_audit(a.days, a.loops, a.start, a.end)
    print(summarize(rows))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
