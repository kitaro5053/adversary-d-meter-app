# -*- coding: utf-8 -*-
"""B-139：「期限までに届かない 友好+1」の席を数えるシャドー集計（挙動不変・計測のみ）。

問い（バックログ §11・手練れユーザー 2026-08-02）＝「3日間で友好4ためるには全日友好を
置かなければいけない。それであればけちらず 友好+2 を置くべきだった」。
＝**増分（+1/+2）の選択が「期限までに必要数へ届くか」を見ていない**のではないか。

## 数え上げの定義（ルール接地・すべて公開情報）

対象＝主人公が**キャラへ置いた `友好+1`** の席。その対象について：

- `g` ＝置く前の友好カウンター。
- `min_need` ＝**実装済み・未解禁・このループ未使用**の友好能力の**最小ハート数**
  （`rules/20:20`＝ハート数**以上**で使用可／`rules/20:22`＝1ループ1回の能力は1回のみ）。
  該当が無ければ「そもそも解禁の余地なし」＝別分類（`no_unreached`）＝本件の対象外。
- `days_left` ＝ `days_per_loop - day`（**今日を除く**残り日数）。ループ開始時に
  カウンターは全除去（`rules/00:86`）＝**期限はループ終了**。
- 1ターンに1キャラへ乗る友好の上限＝**+2**（主人公は同一対象に札を重ねられない＝
  `rules/00` セット手順。`arena/goodwill_audit.py:150-153` が既に同じ前提）。
- 友好付与能力（お嬢様♡3／アイドル♡4／転校生♡2＝`rules/20:38,49,56`）が
  **既に解禁済み**のキャラが生存していれば、供給を **+1/日** 上乗せする（＝安全側＝
  「届かない」と言いにくい側）。

分類：

- `unreach_p1` ＝ `g + 1 + supply * days_left < min_need`
  ＝**この +1 では期限までに1つも解禁できない**（＝死に札）。
- `fixable_p2` ＝ `unreach_p1` かつ `g + 2 + supply * days_left >= min_need` かつ
  **その席の options に同じ対象への `友好+2` が実在**
  ＝**その席が +2 を選んでいれば届いた**（＝B-139 の是正対象・**自席で完結**する）。
- `unreach_both` ＝ `unreach_p1` だが +2 でも届かない（＝増分の選び分けでは直らない）。
- `ok_tt` ＝ TT の可能性が残る対象（`rules/50:127-128`＝友好3以上は敗北条件の封じ手）
  ＝**切ってはいけない**ので数えない。

★**安全側**：`supply` は「他席も含めて最大限に積んだ場合」を仮定する＝
`unreach_p1` は「**どうやっても届かないと確定できる**席」だけを数える。

## B-139b（2026-08-02 追加）＝**1/L の `友好+2` の配り先**を価値つきで数え直す

B-139 の結論＝「増分（+1/+2）の刻み方」の射程は実質ゼロ。副産物として
`p2_spare_alt_needs`（+1 で足りる先へ 1/L の +2 を使い、同じ席に「+2 でしか届かない」
対象が居た席）が 3日級38席／5日級10席 見つかった。
★ただしそれは**上限値**（「+2 でしか届かない対象が居た」≠「そこへ投資する価値がある」）。
∴ 本モジュールは各席で **`_ability_value` による限界価値**を測り、
`delta_raw = alt の限界価値 − 置いた先の限界価値` で並べ替える（定義は `p2_only_abilities` の
直上コメント）。**分類そのものは B-139 から一切変えていない**（フィールドの追加のみ）。

CLI（前面実行）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b139_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b139_audit --days 5
    ... --json out.json --top 20
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.heuristic_protagonist as _hp_mod
from agents import HeuristicMastermind, HeuristicProtagonist
from engine.data import goodwill_abilities_of
from sim import run_game

from arena.goodwill_audit import used_abilities_this_loop

_builtin_max = max

#: 友好+1 を**付与できる**能力（`rules/20:38,49,56`）。解禁済みなら供給 +1/日 とみなす。
GRANTERS: dict[str, str] = {
    "お嬢様": "友好+1付与（学校/都市）",
    "アイドル": "友好+1付与",
    "転校生": "暗躍除去＋友好付与",
}


def _char(view: dict, name: str) -> dict | None:
    for c in view.get("characters", []):
        if c.get("name") == name:
            return c
    return None


def daily_supply(view: dict) -> int:
    """1日あたり1キャラへ積める友好の上限（安全側＝過大評価する）。"""
    s = 2   # 主人公の札（同一対象へ重ねられない＝1ターン最大 +2）
    for n, ab in GRANTERS.items():
        c = _char(view, n)
        if not c or not c.get("alive") or c.get("area") is None:
            continue
        hearts = next((a["hearts"] for a in (goodwill_abilities_of(n) or [])
                       if a["name"] == ab), None)
        if hearts is not None and (c.get("goodwill", 0) or 0) >= hearts:
            s += 1
            break   # 供給の上乗せは1日1つまで（同一対象へ複数の付与が集中する仮定はしない）
    return s


def min_unreached_need(view: dict, tgt: str) -> int | None:
    """対象の**実装済み・未解禁・このループ未使用**の友好能力の最小ハート数。"""
    from sim.abilities import is_implemented

    c = _char(view, tgt)
    if c is None or not c.get("alive"):
        return None
    g = c.get("goodwill", 0) or 0
    used = used_abilities_this_loop(view)
    need = [a["hearts"] for a in (goodwill_abilities_of(tgt) or [])
            if is_implemented(tgt, a["name"]) and a["hearts"] > g
            and (tgt, a["name"]) not in used]
    return min(need) if need else None


def classify(view: dict, tgt: str, agent, options: list[dict]) -> dict:
    """`友好+1` を tgt へ置いた席1つ分の分類（公開情報＋belief のみ）。"""
    c = _char(view, tgt)
    g = (c or {}).get("goodwill", 0) or 0
    tt_p = agent._belief.role_marginals().get(tgt, {}).get("タイムトラベラー", 0.0)
    base = {"goodwill": g, "tt_p": round(tt_p, 3)}
    if tt_p > 0.0 or tgt in (getattr(agent, "_tt_guards", ()) or ()):
        return {"cat": "ok_tt", **base}
    need = min_unreached_need(view, tgt)
    if need is None:
        return {"cat": "no_unreached", **base}
    day, dpl = view.get("day", 1), view.get("days_per_loop", 1)
    days_left = max(0, dpl - day)
    sup = daily_supply(view)
    reach1 = g + 1 + sup * days_left
    reach2 = g + 2 + sup * days_left
    base.update({"need": need, "days_left": days_left, "supply": sup,
                 "reach_p1": reach1, "reach_p2": reach2})
    # ★副指標：AI 自身が狙っている能力（`_compute_invest` が選んだ最良能力）の残り必要数で
    #   同じ算術を回す。`min_need`（最小ハートの能力）は「何かは解禁できる」を見るのに対し、
    #   こちらは「**AI の狙いが期限までに実現するか**」を見る（手練れ指摘に近い側）。
    base["p2_in_hand"] = any(
        o.get("card") == "友好+2" and o.get("target") == tgt
        and o.get("target_kind") == "character" for o in options)
    aim = agent._invest_need.get(tgt)
    if aim is not None:
        base["aim_need"] = aim + g          # 狙っている能力のハート数
        base["aim_unreach_p1"] = (g + 1 + sup * days_left) < aim + g
        base["aim_fix_p2"] = (base["aim_unreach_p1"]
                              and (g + 2 + sup * days_left) >= aim + g)
    if reach1 >= need:
        return {"cat": "ok_reachable", **base}
    has_p2 = base["p2_in_hand"]
    if reach2 >= need and has_p2:
        return {"cat": "fixable_p2", **base}
    if reach2 >= need:
        return {"cat": "unreach_p1_no_card", **base}
    return {"cat": "unreach_both", **base}


def _p2_required(view: dict, tgt: str, agent, sup: int, days_left: int) -> bool:
    """その対象は「**+2 でなければ期限までに1つも解禁できない**」か（+1 では届かない）。"""
    tt_p = agent._belief.role_marginals().get(tgt, {}).get("タイムトラベラー", 0.0)
    if tt_p > 0.0:
        return False
    need = min_unreached_need(view, tgt)
    if need is None:
        return False
    c = _char(view, tgt)
    g = (c or {}).get("goodwill", 0) or 0
    return (g + 1 + sup * days_left) < need <= (g + 2 + sup * days_left)


# ---------------------------------------------------------------------------
# B-139b：**価値つき**の数え直し（Phase 1）
# ---------------------------------------------------------------------------
# ★留保（バックログ §12）＝`p2_spare_alt_needs` の 38/10席は**上限値**。
#   「+2 でしか届かない別対象が**居た**」ことは「**その対象へ投資する価値がある**」を
#   意味しない。能力が無価値なら、+1 で足りる先へ +2 を使うのはむしろ正しい。
#   ∴ 席ごとに「その +2 が**追加で**買う価値」を `_ability_value` で測り、差で並べ替える。
#
# ★指標の定義（本レーンが根拠つきで決めた・下記「素の val を使う理由」参照）
#   任意の対象 T について
#       reach1(T) = g + 1 + supply * days_left ／ reach2(T) = reach1(T) + 1
#   のとき、**+1 では期限内に届かず +2 なら届く**能力＝ハート数 h が
#   `reach1(T) < h <= reach2(T)` を満たすもの（＝h == reach1+1 の1本の帯）だけ。
#   `p2_margin(T)` ＝その帯にある実装済み・このループ未使用の能力の `_ability_value` の最大値
#   （帯が空なら 0.0）＝**この席で +1 でなく +2 を選ぶことの限界価値**。
#   取り逃した価値の差 `delta_raw = max_alt p2_margin(alt) − p2_margin(placed)`。
#
# ★**素の val で比べる理由**（`val / (1.0 + need)` ではない）
#   `_compute_invest`(`heuristic_protagonist.py:3811`) の `val/(1+need)` は
#   「**あと何ハート積む必要があるか**で割った単価（＝投資効率）」であり、
#   *これから投じる資源量が対象ごとに違う*状況で投資先を並べるための量。
#   本件の反実仮想は「**同じ1枚の 1/L 札を、同じ1席で、A に置くか B に置くか**」＝
#   **コストが両辺で同一**。ここで need で割ると、
#   (a) 既に払い終えたコストまで二重計上し、
#   (b) **まさに +2 を要する（need が大きい）遠い対象だけを機械的に減点する**＝
#   測りたいもの（遠い対象を取り逃していないか）を潰す。
#   ∴ 主指標は**素の val**。ただし `delta_disc`（`val/(1+need)` 版）も併記して
#   結論が指標の選択に依存しないことを確認する。


#: 「投資に値する能力」とみなす価値の下限。★AI 自身が既に使っている閾値を借りる
#  （`agents/heuristic_protagonist.py:5552` / `:5574` ＝ `_ability_value(...) >= 45.0`）。
#  これ未満は `_ability_value` の「無価値マーカー」帯（犯人開示 k<=1 の 2.0／通常時の殺害 3.0／
#  未知能力のフォールバック 6.0 等）に該当する＝**取り逃しても損ではない**。
V_MEANINGFUL: float = 45.0


def p2_only_abilities(view: dict, tgt: str, agent, sup: int,
                      days_left: int) -> list[dict]:
    """`+1` では期限内に届かず `+2` なら届く能力の一覧（帯 `reach1 < h <= reach2`）。"""
    from sim.abilities import is_implemented

    c = _char(view, tgt)
    if c is None or not c.get("alive"):
        return []
    g = c.get("goodwill", 0) or 0
    used = used_abilities_this_loop(view)
    lo = g + 1 + sup * days_left      # +1 で届く上限
    hi = lo + 1                       # +2 で届く上限
    out = []
    for a in (goodwill_abilities_of(tgt) or []):
        h = a["hearts"]
        if h <= g or not is_implemented(tgt, a["name"]):
            continue
        if (tgt, a["name"]) in used:
            continue
        if lo < h <= hi:
            val = float(agent._ability_value(tgt, a["name"], None, view))
            out.append({"ability": a["name"], "hearts": h, "need": h - g,
                        "val": round(val, 2),
                        "disc": round(val / (1.0 + (h - g)), 2)})
    return out


def p2_margin(view: dict, tgt: str, agent, sup: int, days_left: int) -> dict:
    """`p2_only_abilities` の最良1本（素の val で最大）。空なら val=disc=0.0。"""
    abs_ = p2_only_abilities(view, tgt, agent, sup, days_left)
    if not abs_:
        return {"val": 0.0, "disc": 0.0, "ability": None, "hearts": None,
                "need": None}
    return _builtin_max(abs_, key=lambda a: a["val"])


def classify_p2(view: dict, tgt: str, agent, options: list[dict]) -> dict:
    """`友好+2`（1/L の希少札）を tgt へ置いた席の分類。

    - `p2_necessary` ＝その対象は +2 でなければ期限までに解禁できない＝正しい使い方。
    - `p2_spare_alt_needs` ＝**+1 でも間に合う対象**へ +2 を使い、かつ**同じ席で
      +2 を置けた別の対象が「+2 でなければ届かない」**＝★手練れ指摘の型
      （L3D1 に `友好+2→男子学生`(♡2) を使い、L3D2 の `友好+1→異世界人`(♡4) が死に札になった）。
    - `p2_spare` ＝上記以外（+1 でも間に合うが、+2 を要する別対象も無い）。

    ★B-139b：`*_alt_needs` の席には**価値つきの追加フィールド**を載せる（分類自体は不変）：
    `placed_val` / `alt_val` / `delta_raw`（素の val の差）と `delta_disc`（need 割引版）。
    """
    day, dpl = view.get("day", 1), view.get("days_per_loop", 1)
    days_left = max(0, dpl - day)
    sup = daily_supply(view)
    if _p2_required(view, tgt, agent, sup, days_left):
        return {"cat": "p2_necessary"}
    # ★テンポの但し書き：+2 なら**今日**解禁できて +1 では今日解禁できない席は、
    #   「+1 でも期限には間に合う」としても**当日1回分の能力使用を買っている**＝
    #   単純な浪費とは呼べない（L1D1 定石3＝当日発動可の不安除去への +2 がこの型）。
    need_t = min_unreached_need(view, tgt)
    g_t = (_char(view, tgt) or {}).get("goodwill", 0) or 0
    tempo = bool(need_t is not None and g_t + 1 < need_t <= g_t + 2)
    alts = sorted({o["target"] for o in options
                   if o.get("card") == "友好+2"
                   and o.get("target_kind") == "character"
                   and o.get("target") != tgt
                   and _p2_required(view, o["target"], agent, sup, days_left)})
    if alts:
        placed = p2_margin(view, tgt, agent, sup, days_left)
        alt_rows = sorted(
            ({"target": a, **p2_margin(view, a, agent, sup, days_left)}
             for a in alts), key=lambda r: -r["val"])
        best = alt_rows[0]
        return {"cat": ("p2_tempo_alt_needs" if tempo else "p2_spare_alt_needs"),
                "alts": alts, "days_left": days_left, "supply": sup,
                "tempo_today": tempo, "need": need_t, "goodwill": g_t,
                # ★価値つき（B-139b Phase 1）
                "placed_val": placed["val"], "placed_disc": placed["disc"],
                "placed_ability": placed["ability"], "placed_need": placed["need"],
                "alt_best": best["target"], "alt_val": best["val"],
                "alt_disc": best["disc"], "alt_ability": best["ability"],
                "alt_need": best["need"], "alt_hearts": best["hearts"],
                "delta_raw": round(best["val"] - placed["val"], 2),
                "delta_disc": round(best["disc"] - placed["disc"], 2),
                "alt_rows": alt_rows}
    return {"cat": "p2_tempo" if tempo else "p2_spare"}


class _Tally(HeuristicProtagonist):
    """挙動は本体と完全同一（`max` は builtin へ委譲＝選択は不変）。数えるだけ。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rows: list[dict] = []
        self.purge_dead_seats: list[dict] = []
        #: (loop, キャラ名) -> そのループ中に**観測できた**友好の最大値（裏取り用・読むだけ）。
        self.gw_max: dict[tuple, int] = {}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        cap: dict = {}
        _lp = view.get("loop")
        for _c in view.get("characters", []):
            _k = (_lp, _c.get("name"))
            _g = _c.get("goodwill", 0) or 0
            if _g > self.gw_max.get(_k, -1):
                self.gw_max[_k] = _g

        def spymax(*args, **kw):
            if (args and isinstance(args[0], list) and "key" in kw
                    and "scored" not in cap):
                key = kw["key"]
                cap["scored"] = sorted(((key(o), o) for o in args[0]),
                                       key=lambda x: -x[0])
            return _builtin_max(*args, **kw)

        had = "max" in _hp_mod.__dict__
        prev = _hp_mod.__dict__.get("max")
        _hp_mod.max = spymax
        try:
            chosen = super().decide(view, decision, options)
        finally:
            if had:
                _hp_mod.max = prev
            else:
                del _hp_mod.max
        # ★副次観測（B-139 の射程外・別チケット候補）＝「浄化係の温存」(`_base_score` #10)が
        #   **G8 で拒否が証明済みの対象**に対して立ちっぱなしの席。この間、その席の
        #   `友好+2` は全対象で 8.0 に抑えられる（＝+2 が事実上撃てない）。
        if decision == "set_card":
            pt = getattr(self, "_purge_target", None)
            dead = (set(getattr(self, "_b86_gw_refused", ()) or ())
                    | set(getattr(self, "_b86_gw_ignore_certain", ()) or ()))
            if pt and pt in dead and any(
                    o.get("card") == "友好+2" and o.get("target_kind") == "character"
                    for o in options):
                self.purge_dead_seats.append(
                    {"loop": view.get("loop"), "day": view.get("day"),
                     "seat": view.get("seat"), "purge": pt})
        if decision == "set_card" and chosen.get("target_kind") == "character" \
                and chosen.get("card") in ("友好+1", "友好+2"):
            tgt = chosen["target"]
            row = {"loop": view.get("loop"), "day": view.get("day"),
                   "seat": view.get("seat"), "card": chosen["card"],
                   "target": tgt}
            if chosen["card"] == "友好+1":
                row.update(classify(view, tgt, self, options))
            else:
                row.update(classify_p2(view, tgt, self, options))
            scored = cap.get("scored", [])
            if scored:
                row["score"] = round(scored[0][0], 2)
            self.rows.append(row)
        return chosen


def audit_game(script, seed: int, loops: int = 8) -> tuple[str, list[dict]]:
    probe = replace(script, loops=loops)
    hp = _Tally(seed)
    state, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome = "defense"
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        outcome = "loss"
    _attach_followup(hp.rows, hp.gw_max, state, outcome)
    return outcome, hp.rows, hp.purge_dead_seats


def _attach_followup(rows: list[dict], gw_max: dict, state, outcome: str) -> None:
    """★裏取り（B-139b Phase 1-4）＝「取り逃した」が本当に損だったかを棋譜から確認する。

    各 `*_alt_needs` 席について、**見送った対象**がそのループ中に
      - `alt_reached` ＝必要ハート数に**実際に到達したか**（観測できた友好の最大値で判定。
        観測は各 decide 時点の view＝**その日の解決後の値**を含む）。
      - `alt_used` ＝その能力を**実際に宣言したか**（`goodwill_used` は公開情報）。
      - `loop_defended` ＝そのループを守り切って勝ったか（＝終局ループかつ `defense`）。
    を付ける。**推定を混ぜない＝すべて棋譜の実イベント**。
    """
    used_by_loop: dict = {}
    for e in state.history:
        if e.get("event") == "goodwill_used":
            used_by_loop.setdefault(e.get("loop"), set()).add(
                (e.get("character"), e.get("ability")))
    final_loop = state.loop_no
    for r in rows:
        if not str(r.get("cat", "")).endswith("_alt_needs"):
            continue
        lp, alt = r.get("loop"), r.get("alt_best")
        hearts, ab = r.get("alt_hearts"), r.get("alt_ability")
        r["alt_reached"] = bool(hearts is not None
                                and gw_max.get((lp, alt), -1) >= hearts)
        r["alt_gw_max"] = gw_max.get((lp, alt))
        r["alt_used"] = bool((alt, ab) in used_by_loop.get(lp, set()))
        r["loop_defended"] = bool(outcome == "defense" and lp == final_loop)
        r["game_outcome"] = outcome
        r["final_loop"] = final_loop


def run(days: int = 3, loops: int = 8, verbose: bool = False,
        start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    by_cat = Counter()
    per_script: list[dict] = []
    gaps: list[dict] = []
    scripts = list(benchmark_scripts(days=days))[start:end]
    for name, seed, sc in scripts:
        outcome, rows, pdead = audit_game(sc, seed, loops=loops)
        by_cat["_purge_dead_seats"] += len(pdead)
        for r in rows:
            by_cat[r["cat"]] += 1
            if r.get("aim_unreach_p1"):
                by_cat["_aim_unreach_p1"] += 1
            if r.get("aim_fix_p2"):
                by_cat["_aim_fix_p2"] += 1
                if r.get("p2_in_hand"):
                    by_cat["_aim_fix_p2_in_hand"] += 1
            # ★B-139b：価値つきの数え直し（`*_alt_needs` の席だけが対象）
            if "delta_raw" in r:
                tag = "spare" if r["cat"] == "p2_spare_alt_needs" else "tempo"
                by_cat[f"_v_{tag}_total"] += 1
                if r["delta_raw"] > 0:
                    by_cat[f"_v_{tag}_delta_pos"] += 1
                    if r["delta_disc"] > 0:
                        by_cat[f"_v_{tag}_delta_pos_both"] += 1
                    # ★段階的な絞り込み（監査doc の層）
                    #   L2＝見送った能力が AI 自身の「投資に値する」閾値を超える。
                    #   L3＝**自席の1枚で完結**（`alt_need <= 2`＝今日の +2 だけで解禁）。
                    #       alt_need = 2 + supply*days_left ＝ days_left>=1 なら
                    #       **翌日に別席の 1/L +2 がもう1枚要る**（手札は1席1枚＝
                    #       `engine.models.PROTAGONIST_HAND`）＝自席で完結しない。
                    _l2 = r["alt_val"] >= V_MEANINGFUL
                    _l3 = (r.get("alt_need") or 99) <= 2
                    if _l2:
                        by_cat[f"_v_{tag}_pos_val{int(V_MEANINGFUL)}"] += 1
                    if _l3:
                        by_cat[f"_v_{tag}_pos_selfcontained"] += 1
                    if _l2 and _l3:
                        by_cat[f"_v_{tag}_pos_val_and_self"] += 1
                    gaps.append({"script": name, "seed": seed,
                                 "L2_val": _l2, "L3_self": _l3, **r})
                elif r["delta_raw"] == 0:
                    by_cat[f"_v_{tag}_delta_zero"] += 1
                else:
                    by_cat[f"_v_{tag}_delta_neg"] += 1
        hits = [r for r in rows if r["cat"] in ("fixable_p2", "unreach_p1_no_card",
                                                "unreach_both",
                                                "p2_spare_alt_needs", "p2_tempo_alt_needs")]
        per_script.append({"script": name, "seed": seed, "outcome": outcome,
                           "n_gw": len(rows), "hits": hits})
        if verbose:
            print(f"  {name} s{seed}: gw={len(rows)} hits={len(hits)} [{outcome}]",
                  flush=True)
    gaps.sort(key=lambda r: (-r["delta_raw"], r["script"], r["seed"],
                             r.get("loop") or 0, r.get("day") or 0))
    return {"days": days, "by_cat": dict(by_cat), "per_script": per_script,
            "gaps": gaps}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=20,
                    help="B-139b：取り逃した価値の差で並べた上位N席を表示")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    res = run(days=a.days, loops=a.loops, verbose=a.verbose,
              start=a.start, end=a.end)
    c = res["by_cat"]
    print(f"== B-139 友好+1 の到達可能性監査（{a.days}日級） ==")
    print("分類:", dict(sorted(c.items(), key=lambda kv: -kv[1])))
    print(f"★fixable_p2（+2 を選んでいれば届いた席）= {c.get('fixable_p2', 0)}")
    print(f"  unreach_p1_no_card（届かないが +2 札が席に無い）= "
          f"{c.get('unreach_p1_no_card', 0)}")
    print(f"  unreach_both（+2 でも届かない）= {c.get('unreach_both', 0)}")
    print(f"★p2_spare_alt_needs（+1 で足りる先へ +2 を使い、+2 必須の別対象が居た席）= "
          f"{c.get('p2_spare_alt_needs', 0)} / p2_necessary={c.get('p2_necessary', 0)}"
          f" / p2_spare={c.get('p2_spare', 0)}")
    print("")
    print("== B-139b：価値つきの数え直し（取り逃した価値の差 delta = alt − placed）==")
    for tag, label in (("spare", "p2_spare_alt_needs"),
                       ("tempo", "p2_tempo_alt_needs（テンポ購入＝潰してはいけない）")):
        tot = c.get(f"_v_{tag}_total", 0)
        print(f"  {label}: 席数={tot} / delta>0={c.get(f'_v_{tag}_delta_pos', 0)}"
              f"（うち割引版でも正={c.get(f'_v_{tag}_delta_pos_both', 0)}）"
              f" / delta=0={c.get(f'_v_{tag}_delta_zero', 0)}"
              f" / delta<0={c.get(f'_v_{tag}_delta_neg', 0)}")
        print(f"      └ L2 見送った能力の価値>={V_MEANINGFUL}: "
              f"{c.get(f'_v_{tag}_pos_val{int(V_MEANINGFUL)}', 0)}"
              f" / L3 自席1枚で完結(alt_need<=2): "
              f"{c.get(f'_v_{tag}_pos_selfcontained', 0)}"
              f" / ★L2∧L3: {c.get(f'_v_{tag}_pos_val_and_self', 0)}")
    gaps = res["gaps"]
    print(f"★真の射程（delta_raw > 0 の席）= {len(gaps)}")
    if gaps and a.top:
        print("")
        print("  # | 局(seed) | L/D/席 | 置いた先(能力/need/val) | 見送った先(能力/need/val)"
              " | 残日 | Δraw | Δdisc | 裏取り(到達/使用/そのL防衛)")
        for i, r in enumerate(gaps[:a.top], 1):
            print(f"  {i:>2} | {r['script']}(s{r['seed']}) | "
                  f"L{r.get('loop')}D{r.get('day')}/{r.get('seat')} | "
                  f"{r.get('target')}({r.get('placed_ability')}/"
                  f"{r.get('placed_need')}/{r.get('placed_val')}) | "
                  f"{r.get('alt_best')}({r.get('alt_ability')}/"
                  f"{r.get('alt_need')}/{r.get('alt_val')}) | "
                  f"{r.get('days_left')} | {r.get('delta_raw')} | "
                  f"{r.get('delta_disc')} | "
                  f"{'到達' if r.get('alt_reached') else '未到達'}/"
                  f"{'使用' if r.get('alt_used') else '未使用'}/"
                  f"{'防衛' if r.get('loop_defended') else '落'}"
                  f"[{r.get('game_outcome')}]")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
