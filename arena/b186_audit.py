# -*- coding: utf-8 -*-
"""B-186 Phase 0：**「鏡の歪み」と「忠実化のコスト」の計測**（計測のみ・`arena/` のみ）。

発注＝FableA（2026-08-07・ユーザー裁定 §62-2「投資していいよ・まず Phase 0」）。
文脈＝`docs/バックログ_構想メモ_FableA.md` §60-10（B-186 起票）・§62／
`docs/監査_B100_Phase3_発火面積_2026-07-29.md` §5-3（`B100_MAKE_PLAN` の負の結果）／
`lane/b183p2` の監査（案A＝3日級 127→124・「介入0回の退行」の再現）。

------------------------------------------------------------------------------
## 0. 対象の機構（現物・main `ad18c49` 時点の行番号）
------------------------------------------------------------------------------

3席一括計画 `_plan_turn`（`agents/heuristic_protagonist.py:6398`）は、採用判定で
「計画の調停値 vs **逐次貪欲の粗い写し（鏡）**」を比べる（`PLAN_ADOPT_MARGIN=10.0`・`:6561`）。

鏡（`:6478-6510`）が写しているもの＝**席1の静的スコア**＋2項の調停
（冷却重複 −8・投資重複 −70・`:6496-6501`）だけ。写していないもの＝
  (i) 実物の席別フラグ再採点（席が置くたび `:6258-6309` で
      `_kinshi_used/_pins_spent/_cooler_invested/_vip_injected/_cooled_days/
      _pinned_today/_genso_evac_today/_planned_moves` が更新され、採点器が
      `:3623/:3676/:3874/:4876/:5120/:5312/:5535/:5631/:6034/:6048` で読む）
  (ii) 席ごとの options の違い（手札が席ごとに別・重ね置き禁止＝`sim/legal.py:64-66`）
  (iii) 席2/3 での `_plan_recs` 再計算・placements の増分。

本 Phase 0 が測るもの：
  1. **鏡の歪み**＝鏡の「貪欲の点数」G~ と、(a) 同じ盤面・同じ候補プールから
     **実採点器＋フラグ実更新**で3席を貪欲確定した点数 G*（シャドー内再現）、
     (b) **実対局の3席が実際に選んだ手の実測点**（base 世界＝素の逐次貪欲そのもの）
     との差の分布。＋採用判定（margin 10.0）が反転するターン数。
  2. **忠実化のコスト**＝(a) の再現1回の実測時間・計画候補側も忠実に測る場合のコスト。
  3. **賞金の上限**＝忠実な比較で「計画が貪欲に margin 10 以上勝つ」ターン数と、
     そのうち資格つき fatal 脅威の折り手が計画側だけに含まれるターン数。

------------------------------------------------------------------------------
## 1. 作法（★B-148 `KEEP_SK_SETUP_SHADOW` と同じ＝判断経路は変えない）
------------------------------------------------------------------------------

- 観測は **base 世界（既定フラグ）の対局**の上で行う。`b100_alloc.allocate` の
  読み取り専用ラッパー＝**本体を呼んだ後**にシャドー計算を行い、返り値は1バイトも変えない。
- シャドー内の「実採点器での再現」は agent の席間フラグを**一時的に書き換えて score を
  呼び直し、finally で完全復元**する。復元対象＝`_SNAP_*`（単一エントリキャッシュ
  `_b56/_b90/_b94` も含めて復元）。物証＝`verify`（計測 ON と素の対局の棋譜完全一致・両ベンチ全数）。
- 鏡の再実装は `_plan_turn` 本体との**突き合わせ**（返り値の3手組が再実装の予測と一致するか）を
  全ターンで行い、食い違いは「分類不能」として正直に数える。
- ★正解の配役は一切参照しない。★測定は `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行。

`mech` サブコマンドだけは**別建ての診断実行**（`B100_MAKE_PLAN=True` を立てた対局を
base と比較する＝挙動が変わるのは意図・シャドーではない）。「介入0回の退行」の機序の
実物確認（設置→追従の連鎖）に使う。

------------------------------------------------------------------------------
## 2. CLI
------------------------------------------------------------------------------

    python -m arena.b186_audit verify --days 3 [--start 0 --end 10]
    python -m arena.b186_audit scan   --days 3 [--json out.json] [--no-best]
    python -m arena.b186_audit mech   --days 3 [--game random_BTX#0] [--all]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import replace
from itertools import combinations

from agents import HeuristicMastermind, HeuristicProtagonist
from agents import b100_alloc
from agents.heuristic_protagonist import PRIORITY
from engine.data import goodwill_abilities_of
from arena.benchmark import benchmark_scripts
from sim import run_game

_EPS = 1e-9
_MISSING = object()
_MARGIN = float(HeuristicProtagonist.PLAN_ADOPT_MARGIN)   # 10.0
_TOPK = int(HeuristicProtagonist._PLAN_TOPK)              # 14

_ORIG_ALLOC = b100_alloc.allocate
_ORIG_LC = b100_alloc.live_constraints


def _key(o) -> tuple:
    return (o.get("card"), o.get("target"), o.get("target_kind"))


# ---------------------------------------------------------------------------
# 席間フラグのスナップショット／復元（シャドーの副作用ゼロ化の要）
# ---------------------------------------------------------------------------
#: 値をそのまま保存してよい属性（不変値 or 上書きで戻せるもの）。
_SNAP_SIMPLE = ("_kinshi_used", "_pins_spent", "_cooler_invested",
                "_vip_injected", "_genso_evac_today", "_b100_match_note",
                "_b56_cache", "_b90_key", "_b90_cache", "_b94_key", "_b94_cache")
#: set をコピーして保存する属性。
_SNAP_SET = ("_cooled_days", "_pinned_today", "_b100_placed")
#: dict をコピーして保存する属性。
_SNAP_DICT = ("_planned_moves",)


def _snapshot(agent) -> dict:
    snap = {}
    for a in _SNAP_SIMPLE:
        snap[a] = getattr(agent, a, _MISSING)
    for a in _SNAP_SET:
        v = getattr(agent, a, _MISSING)
        snap[a] = set(v) if v is not _MISSING else _MISSING
    for a in _SNAP_DICT:
        v = getattr(agent, a, _MISSING)
        snap[a] = dict(v) if v is not _MISSING else _MISSING
    return snap


def _restore(agent, snap: dict) -> None:
    for a, v in snap.items():
        if v is _MISSING:
            if hasattr(agent, a):
                delattr(agent, a)
        else:
            setattr(agent, a, v)


def _apply_play(agent, view: dict, best: dict) -> None:
    """decide() の席間協調フラグ更新。

    ★Phase 1b で本体 `HeuristicProtagonist._apply_seat_flags` に単一ソース化した
    （Phase 0 §9-2 の「写し＝二重実装」の解消）。audit 独自分＝`_b100_placed` の
    追記（scan の追加会計）のみ。B100_HOOK / prov タグ等の表示層は含めない。
    """
    agent._b100_placed.add((best["card"], best["target"],
                            best.get("target_kind")))
    agent._apply_seat_flags(view, best)


def _faithful(agent, view: dict, options: list[dict], score,
              picks: list[dict] | None = None) -> tuple[float, list]:
    """実採点器＋フラグ実更新での逐次評価（シャドー・finally で完全復元）。

    picks=None＝貪欲（毎席、残り候補の score 最大を取る）。picks 指定＝その3手を
    その順で置いたときの逐次値。候補プール＝席1の options（★限界＝実対局の席2/3は
    自分の手札から選ぶ。プール差は real 観測との突き合わせ d2 で別途測る）。
    """
    snap = _snapshot(agent)
    try:
        avail = list(options)
        total = 0.0
        out = []
        n = 3 if picks is None else len(picks)
        for i in range(n):
            if picks is None:
                if not avail:
                    break
                o = max(avail, key=score)
            else:
                o = picks[i]
            v = float(score(o))
            total += v
            out.append((v, _key(o)))
            _apply_play(agent, view, o)
            tk = (o["target"], o.get("target_kind"))
            avail = [x for x in avail
                     if (x["target"], x.get("target_kind")) != tk]
        return total, out
    finally:
        _restore(agent, snap)


# ---------------------------------------------------------------------------
# 鏡（_plan_turn の採用判定）の再実装＝greedy_val / best_val / 採用可否を取り出す
# ---------------------------------------------------------------------------
def _cool_day(agent, view: dict, o: dict):
    ct = agent._b133_cool_target(view, o)
    if ct is None:
        return None
    for d in getattr(agent, "_incident_danger", {}):
        if d >= view["day"] and ct in \
                getattr(agent, "_culprit_cands", {}).get(d, ()):
            return d
    return None


def _is_cooler_invest(agent, view: dict, o: dict) -> bool:
    if o["card"] not in ("友好+1", "友好+2") or o.get("target_kind") != "character":
        return False
    c = agent._alive(view, o["target"])
    return bool(c and any(
        "不安" in ab["name"] and "除去" in ab["name"] and c["goodwill"] < ab["hearts"]
        for ab in goodwill_abilities_of(o["target"]) or []))


def _mirror(agent, view: dict, options: list[dict], score) -> dict | None:
    """`_plan_turn`（vip_risk=None 経路）の写し。greedy_val/best_val/採用可否を返す。

    ★本体との突き合わせ＝呼び出し側が `agent._plan_turn(...)` の返り値と
    expected_keys を比較する（食い違い＝分類不能）。
    """
    scored = sorted(((score(o), o) for o in options), key=lambda x: -x[0])
    scored = [(s, o) for s, o in scored[:_TOPK] if s > 0]
    if len(scored) < 3:
        return {"degenerate": True, "scored": scored,
                "expected_keys": [_key(o) for _s, o in scored]}

    days_left_after = view.get("days_per_loop", 3) - view["day"]
    pins_left = max(0, 3 - getattr(agent, "_pins_spent", 0))
    pins_scarce = pins_left < days_left_after + 1

    cdays = [_cool_day(agent, view, o) for _s, o in scored]
    cinv = [_is_cooler_invest(agent, view, o) for _s, o in scored]

    # -- 逐次貪欲のシミュレーション（本体 `:6478-6510` の写し） --
    greedy_idx: list[int] = []
    used_t: set = set()
    used_kinshi = False
    g_days: set = set()
    g_invested = False
    for _pick in range(3):
        best_j, best_s = None, None
        for j, (s, o) in enumerate(scored):
            if j in greedy_idx:
                continue
            t = (o["target"], o.get("target_kind"))
            if t in used_t:
                continue
            if o["card"] == "暗躍禁止" and used_kinshi:
                continue
            eff = s
            cd = cdays[j]
            if cd is not None and cd in g_days:
                eff -= 8.0
            if cinv[j] and g_invested:
                eff -= 70.0
            if best_s is None or eff > best_s:
                best_j, best_s = j, eff
        if best_j is None:
            break
        o = scored[best_j][1]
        greedy_idx.append(best_j)
        used_t.add((o["target"], o.get("target_kind")))
        used_kinshi = used_kinshi or o["card"] == "暗躍禁止"
        cd = cdays[best_j]
        if cd is not None:
            g_days.add(cd)
        g_invested = g_invested or cinv[best_j]

    # -- 組の調停値（本体 `:6512-6559` の写し・vip_risk=None） --
    feed_days = {i2["day"] for i2 in view.get("incidents", [])
                 if i2.get("name") in ("行方不明", "邪気の汚染")}
    _ml_p = PRIORITY.get("危険犯人_ML分離")

    def _val(idx: tuple) -> float | None:
        opts = [scored[i][1] for i in idx]
        tgts = [(o["target"], o.get("target_kind")) for o in opts]
        if len(set(tgts)) < 3:
            return None
        if sum(1 for o in opts if o["card"] == "暗躍禁止") > 1:
            return None
        val = sum(scored[i][0] for i in idx)
        days = [cdays[i] for i in idx]
        days = [d for d in days if d is not None]
        if len(days) > len(set(days)):
            val -= 8.0 * (len(days) - len(set(days)))
        inv = sum(1 for i in idx if cinv[i])
        if inv > 1:
            val -= 70.0 * (inv - 1)
        if pins_scarce:
            pin_scores = [scored[i][0] for i in idx
                          if scored[i][1]["card"] == "移動禁止"
                          and scored[i][0] >= 70.0]
            if pin_scores:
                excluded_feed = any(
                    j not in idx and s >= 72.0
                    and ((cdays[j] in feed_days)
                         or (o2["card"].startswith("移動") and s == _ml_p))
                    for j, (s, o2) in enumerate(scored))
                if excluded_feed:
                    val -= 18.0 * len(pin_scores)
        return val

    best_val, best_idx, greedy_val = None, None, None
    for idx in combinations(range(len(scored)), 3):
        val = _val(idx)
        if val is None:
            continue
        if tuple(idx) == tuple(sorted(greedy_idx)):
            greedy_val = val
        if best_val is None or val > best_val:
            best_val, best_idx = val, idx

    if best_idx is None:
        return {"degenerate": True, "scored": scored,
                "expected_keys": [_key(o) for _s, o in scored[:3]]}
    adopted = not (greedy_val is not None and len(greedy_idx) == 3
                   and best_val < greedy_val + _MARGIN)
    chosen = list(best_idx) if adopted else list(greedy_idx)
    chosen_sorted = sorted(chosen, key=lambda i: -scored[i][0])
    return {
        "degenerate": False, "scored": scored,
        "greedy_idx": greedy_idx, "greedy_val": greedy_val,
        "greedy_keys_seq": [_key(scored[i][1]) for i in greedy_idx],
        "best_idx": list(best_idx), "best_val": best_val,
        "best_keys_desc": [_key(scored[i][1])
                           for i in sorted(best_idx, key=lambda i: -scored[i][0])],
        "adopted": adopted,
        "expected_keys": [_key(scored[i][1]) for i in chosen_sorted],
        "val_fn": _val,
    }


# ---------------------------------------------------------------------------
# 観測プローブ（base 世界・読み取り専用）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    def __init__(self, seed: int = 0, shadow: bool = True,
                 faithful_best: bool = True):
        super().__init__(seed)
        self._shadow_on = shadow
        self._faithful_best = faithful_best
        self.seat_rows: dict = {}     # (loop,day,seat) -> 席の記録
        self.turn_rows: dict = {}     # (loop,day) -> 適格ターンのシャドー結果
        self.cost = Counter()         # 時間（秒）と件数
        self.n_xcheck_fail = 0        # 鏡再実装と本体 _plan_turn の食い違い
        self.n_hook_err = 0
        self.n_stashless = 0          # allocate 不到達の席（stash 無し等）
        self._cur = None
        self._cur_trace: list = []
        self._last_cons = None

    # -- decide：席の識別と実手の記録だけ（判断経路は素通し） ---------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision == "set_card" and self._shadow_on:
            self._cur = (view.get("loop"), view.get("day"), view.get("seat"))
            self._cur_trace = []
            self._n_log0 = len(self._b100_log)
        chosen = super().decide(view, decision, options)
        if decision == "set_card" and self._shadow_on:
            row = self.seat_rows.get(self._cur)
            if row is None:
                self.n_stashless += 1
            else:
                row["play"] = _key(chosen)
                row["forced"] = len(self._b100_log) > self._n_log0
        return chosen

    def _on_trace(self, rec: dict) -> None:
        try:
            self._cur_trace.append(rec)
        except Exception:
            pass

    # -- allocate ラッパーから呼ばれる本体（読み取り専用） -------------------
    def _after_alloc(self, view: dict, options: list[dict], kw: dict, res) -> None:
        score = kw.get("score")
        key = self._cur
        if key is None or score is None:
            return
        t0 = time.perf_counter()
        intent, from_plan = b100_alloc.intended_move(self, options, score)
        row = {"n_prot": kw.get("n_prot_placed"),
               "from_plan": bool(from_plan),
               "alloc_forced": res is not None,
               "intent": (_key(intent) if intent is not None else None),
               "intent_score": (float(score(intent)) if intent is not None
                                else None)}
        self.seat_rows[key] = row
        self.cost["t_seat_rec"] += time.perf_counter() - t0

        # ---- 適格ターン（MAKE_PLAN が計画比較を走らせる条件の写し＝ :394-397） ----
        if kw.get("n_prot_placed") != 0 or getattr(self, "_turn_plan", None):
            return
        cons_rows = (self._cur_trace[-1].get("cons")
                     if self._cur_trace else None) or []
        if not any((c.get("n_here") or 0) > 0 and c.get("gate")
                   for c in cons_rows):
            return

        turn = (key[0], key[1])
        rec: dict = {"seat1": key[2], "seat1_forced": res is not None}
        self.turn_rows[turn] = rec
        try:
            # ---- 1. 鏡（再実装）＋本体 _plan_turn との突き合わせ ----
            t0 = time.perf_counter()
            m = _mirror(self, view, options, score)
            self.cost["t_mirror"] += time.perf_counter() - t0
            t0 = time.perf_counter()
            real_plan = self._plan_turn(view, options, score)
            self.cost["t_plan_turn"] += time.perf_counter() - t0
            self.cost["n_eligible"] += 1
            real_keys = [_key(o) for o in real_plan]
            rec["xcheck_ok"] = (m is not None
                                and real_keys == m.get("expected_keys"))
            if not rec["xcheck_ok"]:
                self.n_xcheck_fail += 1
                return
            if m["degenerate"]:
                rec["degenerate"] = True
                return
            rec["degenerate"] = False
            # 席1の options 全体の静的点（実対局の席2/3の実手が「プール外」か
            # 「プール内だが静的点で下位」かの層別用）
            rec["opt_static"] = {str(_key(o)): round(float(score(o)), 2)
                                 for o in options}
            rec["greedy_val_mirror"] = m["greedy_val"]
            rec["best_val_mirror"] = m["best_val"]
            rec["adopted_mirror"] = m["adopted"]
            rec["greedy_keys_mirror"] = m["greedy_keys_seq"]
            rec["best_keys_mirror"] = m["best_keys_desc"]

            # ---- 2. 忠実な貪欲（実採点器＋フラグ実更新・シャドー） ----
            t0 = time.perf_counter()
            g_total, g_seq = _faithful(self, view, options, score)
            dt = time.perf_counter() - t0
            self.cost["t_faith_greedy"] += dt
            self.cost["n_faith_greedy"] += 1
            rec["faith_greedy_total"] = g_total
            rec["faith_greedy_keys"] = [k for _v, k in g_seq]
            rec["t_faith_greedy"] = dt

            # ---- 3. 鏡の best 組・greedy 組の忠実値 ----
            t0 = time.perf_counter()
            scored = m["scored"]
            best_opts = [scored[i][1]
                         for i in sorted(m["best_idx"],
                                         key=lambda i: -scored[i][0])]
            bf_total, _ = _faithful(self, view, options, score, picks=best_opts)
            g_opts = [scored[i][1] for i in m["greedy_idx"]]
            gf_total, _ = _faithful(self, view, options, score, picks=g_opts)
            self.cost["t_faith_pair"] += time.perf_counter() - t0
            rec["faith_of_mirror_best"] = bf_total
            rec["faith_of_mirror_greedy"] = gf_total

            # ---- 4. 忠実な最良組の探索（賞金の上限・--no-best で省略可） ----
            if self._faithful_best:
                t0 = time.perf_counter()
                fb_val, fb_keys, n_comb = None, None, 0
                for idx in combinations(range(len(scored)), 3):
                    opts = [scored[i][1] for i in idx]
                    tgts = {(o["target"], o.get("target_kind")) for o in opts}
                    if len(tgts) < 3:
                        continue
                    if sum(1 for o in opts if o["card"] == "暗躍禁止") > 1:
                        continue
                    n_comb += 1
                    order = sorted(idx, key=lambda i: -scored[i][0])
                    tot, _seq = _faithful(self, view, options, score,
                                          picks=[scored[i][1] for i in order])
                    if fb_val is None or tot > fb_val:
                        fb_val = tot
                        fb_keys = [_key(scored[i][1]) for i in order]
                dt = time.perf_counter() - t0
                self.cost["t_faith_best"] += dt
                self.cost["n_faith_best_comb"] += n_comb
                rec["faith_best_val"] = fb_val
                rec["faith_best_keys"] = fb_keys
                rec["t_faith_best"] = dt

            # ---- 資格つき fatal 脅威の折り手キー（賞金の fatal 判定用） ----
            gated = {}
            if self._last_cons:
                by_label = {c.get("label"): c for c in cons_rows}
                for c in self._last_cons:
                    t = c.get("threat")
                    if t is None:
                        continue
                    tr = by_label.get(t.label)
                    if not tr or not tr.get("gate"):
                        continue
                    gated[t.label] = {"prob": float(t.prob),
                                      "keys": sorted(map(str, c.get("keys") or ())),
                                      "raw_keys": list(c.get("keys") or ())}
            rec["gated"] = {lb: {"prob": g["prob"], "keys": g["raw_keys"]}
                            for lb, g in gated.items()}
        except Exception:
            self.n_hook_err += 1
            self.turn_rows.pop(turn, None)


def _install(probe: _Probe):
    def wrapped_alloc(agent, view, options, threats, **kw):
        res = _ORIG_ALLOC(agent, view, options, threats, **kw)
        if agent is probe and probe._shadow_on:
            try:
                probe._after_alloc(view, options, kw, res)
            except Exception:
                probe.n_hook_err += 1
        return res

    def wrapped_lc(agent, view, options, threats, **kw):
        cons = _ORIG_LC(agent, view, options, threats, **kw)
        if agent is probe and probe._shadow_on:
            probe._last_cons = cons
        return cons

    b100_alloc.allocate = wrapped_alloc
    b100_alloc.live_constraints = wrapped_lc
    b100_alloc.TRACE = probe._on_trace


def _uninstall():
    b100_alloc.allocate = _ORIG_ALLOC
    b100_alloc.live_constraints = _ORIG_LC
    b100_alloc.TRACE = None


# ---------------------------------------------------------------------------
# 1局の実行
# ---------------------------------------------------------------------------
def _trace_of(st) -> list:
    return [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"),
             e.get("target"), e.get("to")) for e in st.history]


def _outcome(st, loops: int) -> tuple[int, str]:
    fb = any(e.get("event") == "final_battle" for e in st.history)
    if st.winner == "protagonist" and not fb:
        return st.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if st.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def _play_probed(script, seed: int, loops: int, faithful_best: bool = True):
    hp = _Probe(seed, shadow=True, faithful_best=faithful_best)
    _install(hp)
    try:
        st, _ = run_game(replace(script, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": hp, "p2": hp, "p3": hp})
    finally:
        _uninstall()
    return hp, st


def _play_plain(script, seed: int, loops: int):
    hp = HeuristicProtagonist(seed)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    return hp, st


# ---------------------------------------------------------------------------
# verify＝観測が対局を変えていない物証（両ベンチ全数）
# ---------------------------------------------------------------------------
def verify(days: int, loops: int = 8, start: int = 0, end: int | None = None,
           games: set | None = None) -> dict:
    rows = benchmark_scripts(days=days)
    if end is not None:
        rows = rows[start:end]
    elif start:
        rows = rows[start:]
    bad = []
    n = 0
    for name, seed, sc in rows:
        gid = f"{name}#{seed}"
        if games and gid not in games:
            continue
        n += 1
        _hp, st_p = _play_probed(sc, seed, loops)
        _h2, st_b = _play_plain(sc, seed, loops)
        if _trace_of(st_p) != _trace_of(st_b):
            bad.append(gid)
        print(f"  {gid}: {'MISMATCH' if bad and bad[-1] == gid else 'ok'}"
              f" (eligible={len(_hp.turn_rows)}, xfail={_hp.n_xcheck_fail},"
              f" hook_err={_hp.n_hook_err})", flush=True)
    return {"days": days, "n_games": n, "mismatch": bad}


# ---------------------------------------------------------------------------
# scan＝本計測
# ---------------------------------------------------------------------------
def _quantiles(vals: list[float]) -> dict:
    if not vals:
        return {}
    v = sorted(vals)
    n = len(v)

    def q(p):
        i = min(n - 1, max(0, int(round(p * (n - 1)))))
        return round(v[i], 2)
    return {"n": n, "min": round(v[0], 2), "p10": q(0.10), "p25": q(0.25),
            "med": q(0.50), "p75": q(0.75), "p90": q(0.90),
            "max": round(v[-1], 2)}


def scan(days: int, loops: int = 8, start: int = 0, end: int | None = None,
         faithful_best: bool = True, json_path: str | None = None) -> dict:
    from arena.corpus_census import script_signature
    rows = benchmark_scripts(days=days)
    if end is not None:
        rows = rows[start:end]
    elif start:
        rows = rows[start:]

    per_turn: list[dict] = []
    cost = Counter()
    agg = Counter()
    t_bench0 = time.perf_counter()
    game_sigs: dict = {}
    for name, seed, sc in rows:
        gid = f"{name}#{seed}"
        game_sigs[gid] = script_signature(sc)
        hp, st = _play_probed(sc, seed, loops, faithful_best=faithful_best)
        cost.update(hp.cost)
        agg["n_stashless_seats"] += hp.n_stashless
        agg["n_xcheck_fail"] += hp.n_xcheck_fail
        agg["n_hook_err"] += hp.n_hook_err
        for (loop, day), rec in sorted(hp.turn_rows.items()):
            r = dict(rec)
            r["game"] = gid
            r["loop"], r["day"] = loop, day
            # 実対局の3席（base 世界の素の逐次貪欲）の実測
            seats = sorted((k, v) for k, v in hp.seat_rows.items()
                           if k[0] == loop and k[1] == day)
            r["n_seats_rec"] = len(seats)
            r["turn_forced"] = any(v.get("forced") or v.get("alloc_forced")
                                   for _k, v in seats)
            r["turn_from_plan"] = any(v.get("from_plan") for _k, v in seats)
            pure = (len(seats) == 3 and not r["turn_forced"]
                    and not r["turn_from_plan"]
                    and all(v.get("play") == v.get("intent")
                            and v.get("intent_score") is not None
                            for _k, v in seats))
            r["pure_greedy_turn"] = pure
            if pure:
                r["real_greedy_total"] = sum(v["intent_score"]
                                             for _k, v in seats)
                r["real_greedy_keys"] = [v["intent"] for _k, v in seats]
            per_turn.append(r)
        print(f"  {gid}: eligible={len(hp.turn_rows)}", flush=True)
    bench_wall = time.perf_counter() - t_bench0

    # ---- 集計 ----
    ok = [r for r in per_turn if r.get("xcheck_ok") and not r.get("degenerate")]
    d1, d2, flips_a1, flips_ab = [], [], [], []
    comp_mismatch_sim = comp_mismatch_real = 0
    n_pure = 0
    adopt_mirror = 0
    prize_rows = []
    for r in ok:
        gm = r["greedy_val_mirror"]
        if gm is None:
            continue
        d1.append(gm - r["faith_greedy_total"])
        if set(map(str, r["greedy_keys_mirror"])) != \
                set(map(str, r["faith_greedy_keys"])):
            comp_mismatch_sim += 1
        if r.get("pure_greedy_turn"):
            n_pure += 1
            d2.append(gm - r["real_greedy_total"])
            if set(map(str, r["greedy_keys_mirror"])) != \
                    set(map(str, r["real_greedy_keys"])):
                comp_mismatch_real += 1
        if r["adopted_mirror"]:
            adopt_mirror += 1
        # 採用判定の反転（A1＝鏡の best 組を忠実値で再判定）
        a1 = (r["faith_of_mirror_best"]
              >= r["faith_greedy_total"] + _MARGIN - _EPS)
        if a1 != r["adopted_mirror"]:
            flips_a1.append(r)
        # 賞金（忠実な最良組 vs 忠実な貪欲・margin 10）
        if r.get("faith_best_val") is not None:
            win = (r["faith_best_val"]
                   >= r["faith_greedy_total"] + _MARGIN - _EPS)
            ab = win
            if ab != r["adopted_mirror"]:
                flips_ab.append(r)
            if win:
                gset = set(map(str, r["faith_greedy_keys"]))
                pset = set(map(str, r["faith_best_keys"]))
                fatal_only_plan = []
                for lb, g in (r.get("gated") or {}).items():
                    keys = set(map(str, map(tuple, g["keys"])))
                    if keys & pset and not (keys & gset):
                        fatal_only_plan.append((lb, g["prob"]))
                prize_rows.append({
                    "game": r["game"], "loop": r["loop"], "day": r["day"],
                    "margin": round(r["faith_best_val"]
                                    - r["faith_greedy_total"], 1),
                    "fatal_only_plan": fatal_only_plan})

    def _games_of(rows_):
        gs = {r["game"] for r in rows_}
        return {"局数": len(gs),
                "独立脚本数": len({game_sigs[g] for g in gs})}

    seal = [r for r in per_turn if r["game"].startswith("btx5_seal")]
    n_score_calls_fg = cost.get("n_faith_greedy", 0) * 3
    rep = {
        "days": days, "n_games": len(rows),
        "適格ターン（計画比較が走るターン）": len(per_turn),
        "適格ターンの局": _games_of(per_turn),
        "内訳": {
            "xcheck_ok": sum(1 for r in per_turn if r.get("xcheck_ok")),
            "分類不能（鏡再実装と本体の食い違い）": agg["n_xcheck_fail"],
            "degenerate（候補3未満）": sum(1 for r in per_turn
                                        if r.get("degenerate")),
            "hook_err": agg["n_hook_err"],
            "seat1がb100強制と同席": sum(1 for r in per_turn
                                    if r.get("seat1_forced")),
        },
        "鏡の歪み_d1（鏡G~ − 忠実シャドーG*・同一プール）": _quantiles(d1),
        "鏡の歪み_d2（鏡G~ − 実対局3席の実測・純貪欲ターンのみ）": _quantiles(d2),
        "純貪欲ターン数（d2の分母）": n_pure,
        "貪欲組の構成不一致（鏡 vs 忠実シャドー）": comp_mismatch_sim,
        "貪欲組の構成不一致（鏡 vs 実対局）": comp_mismatch_real,
        "採用（鏡の判定でadopt）": adopt_mirror,
        "採用反転_A1（鏡best組を忠実値で再判定）": {
            "件数": len(flips_a1), **_games_of(flips_a1)},
        "採用反転_AB（忠実最良組 vs 忠実貪欲）": {
            "件数": len(flips_ab), **_games_of(flips_ab)},
        "賞金（忠実比較で計画がmargin10以上勝つ）": {
            "件数": len(prize_rows), **_games_of(prize_rows),
            "うち資格つきfatalの折り手が計画側のみ":
                sum(1 for p in prize_rows if p["fatal_only_plan"]),
            "rows": prize_rows},
        "btx5_seal（単独行）": {"適格ターン": len(seal)},
        "コスト": {
            "bench壁時計_s": round(bench_wall, 1),
            "鏡（再実装）_s": round(cost["t_mirror"], 3),
            "本体_plan_turn_s": round(cost["t_plan_turn"], 3),
            "忠実貪欲1回_s計": round(cost["t_faith_greedy"], 3),
            "忠実貪欲の回数": cost["n_faith_greedy"],
            "忠実貪欲1回あたり_ms": round(
                1000 * cost["t_faith_greedy"] / max(1, cost["n_faith_greedy"]), 2),
            "忠実ペア再評価_s計": round(cost["t_faith_pair"], 3),
            "忠実最良組探索_s計": round(cost["t_faith_best"], 3),
            "忠実最良組の組合せ数計": cost["n_faith_best_comb"],
            "参考_score呼び出し数（忠実貪欲のみ・概算）": n_score_calls_fg,
        },
        "stashless席数": agg["n_stashless_seats"],
        "rows": per_turn,
    }
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    return rep


# ---------------------------------------------------------------------------
# mech＝「介入0回の退行」の機序の実物確認（★診断実行＝挙動が変わるのは意図）
# ---------------------------------------------------------------------------
class _MechProbe(HeuristicProtagonist):
    """B100_MAKE_PLAN=True の世界で、設置→採用→追従の連鎖を数える。"""

    def __init__(self, seed: int = 0, make_plan: bool = True):
        super().__init__(seed)
        if make_plan:
            self.B100_MAKE_PLAN = True
        self.events: list[dict] = []
        self._mech_faith_log: list[dict] = []   # ★1b：忠実比較の記録（対鏡の照合）
        self._cur = None

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        self._cur = (view.get("loop"), view.get("day"), view.get("seat"))
        pre_plan = [(_key(it)) for it in (self._turn_plan or [])] \
            if getattr(self, "_turn_plan", None) else []
        n_log0 = len(self._b100_log)
        self._mech_install = None      # allocate ラッパーが埋める
        chosen = super().decide(view, decision, options)
        forced = len(self._b100_log) > n_log0
        inst = self._mech_install
        followed = False
        if not forced and inst and inst.get("plan_after"):
            followed = _key(chosen) == inst["plan_after"][0]
        elif not forced and pre_plan:
            followed = _key(chosen) == pre_plan[0]
        self.events.append({
            "key": self._cur, "play": _key(chosen), "forced": forced,
            "installed": bool(inst), "adopted": (inst or {}).get("adopted"),
            "plan": (inst or {}).get("plan_after"),   # ★設置された計画の中身
            "pre_plan": bool(pre_plan), "followed": followed})
        return chosen


class _CleanProbe(_MechProbe):
    """★診断＝副作用ゼロ化の試算：MAKE_PLAN 世界で、採用ゲートに**負けた**計画を
    `_turn_plan` に残さない（[] を返す＝席は素の逐次貪欲へ戻る）。
    採用された計画だけが席に入る＝「介入0ターン」は base と bit 一致するはずの世界。
    ★arena 内のサブクラス上書きのみ＝`agents/` 非接触。鏡再実装（_mirror）で採否を
    先に判定する（verify で xcheck 食い違い 0 を全数確認済みの写し）。"""

    def _plan_turn(self, view: dict, options: list[dict], score_fn,
                   vip_risk=None) -> list[dict]:
        if vip_risk is not None:
            return super()._plan_turn(view, options, score_fn,
                                      vip_risk=vip_risk)
        m = _mirror(self, view, options, score_fn)
        if m is None or m.get("degenerate") or m["adopted"]:
            return super()._plan_turn(view, options, score_fn)
        return []


def _mech_wrap(probe: _MechProbe):
    def wrapped_alloc(agent, view, options, threats, **kw):
        before = bool(getattr(agent, "_turn_plan", None))
        cmp0 = getattr(agent, "_b186_last_cmp", None)
        res = _ORIG_ALLOC(agent, view, options, threats, **kw)
        if agent is probe:
            after = list(getattr(agent, "_turn_plan", None) or [])
            cmp1 = getattr(agent, "_b186_last_cmp", None)
            faith_ran = cmp1 is not None and cmp1 is not cmp0
            if faith_ran:
                # ★1b：忠実比較が走った＝設置の有無に関わらず、鏡なら何と判定したかを
                #   同じ盤面で照合して記録（誤採用/誤棄却の数え上げ・読み取り専用）
                try:
                    m = _mirror(agent, view, options, kw.get("score"))
                    mir = (None if m is None or m.get("degenerate")
                           else m["adopted"])
                except Exception:
                    mir = None
                agent._mech_faith_log.append(
                    {"turn": cmp1["turn"], "faith": cmp1["adopted"],
                     "mirror": mir, "greedy_val": cmp1["greedy_val"],
                     "best_val": cmp1["best_val"]})
            if not before and after:
                info = {"plan_after": [_key(it) for it in after]}
                if faith_ran:
                    info["adopted"] = cmp1["adopted"]   # 実装本体の判定そのもの
                else:
                    try:
                        m = _mirror(agent, view, options, kw.get("score"))
                        info["adopted"] = (None if m is None or m.get("degenerate")
                                           else m["adopted"])
                    except Exception:
                        info["adopted"] = None
                agent._mech_install = info
        return res
    b100_alloc.allocate = wrapped_alloc


def _mech_play(script, seed: int, loops: int, make_plan: bool,
               clean: bool = False, impl: bool = False,
               faithful: bool = False):
    # impl＝★B-186 Phase 1a の**実装本体**（`B186_CLEAN_REJECT=True`）で測る。
    #   clean（_CleanProbe＝鏡再実装で採否を先読みする arena 内の試算装置）との
    #   相互検証用＝両者が一致すれば実装が試算どおりであることの物証。
    # faithful＝★Phase 1b の実装本体（`B186_FAITHFUL_COMPARE=True`・1a 込み）。
    cls = _CleanProbe if (clean and not impl and not faithful) else _MechProbe
    hp = cls(seed, make_plan=make_plan)
    if impl or faithful:
        hp.B186_CLEAN_REJECT = True
    if faithful:
        hp.B186_FAITHFUL_COMPARE = True
    if make_plan:
        _mech_wrap(hp)
    try:
        st, _ = run_game(replace(script, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": hp, "p2": hp, "p3": hp})
    finally:
        b100_alloc.allocate = _ORIG_ALLOC
    return hp, st


def mech(days: int, loops: int = 8, game: str | None = None,
         all_games: bool = False, clean: bool = False,
         impl: bool = False, faithful: bool = False,
         json_path: str | None = None) -> dict:
    rows = benchmark_scripts(days=days)
    out = []
    for name, seed, sc in rows:
        gid = f"{name}#{seed}"
        if game and gid != game:
            continue
        if not game and not all_games:
            continue
        hp_a, st_a = _play_plain(sc, seed, loops)
        ltw_a, oc_a = _outcome(st_a, loops)
        hp_b, st_b = _mech_play(sc, seed, loops, make_plan=True, clean=clean,
                                impl=impl, faithful=faithful)
        ltw_b, oc_b = _outcome(st_b, loops)
        ev = hp_b.events
        n_install = sum(1 for e in ev if e["installed"])
        n_adopt = sum(1 for e in ev if e["installed"] and e["adopted"] is True)
        n_reject = sum(1 for e in ev if e["installed"] and e["adopted"] is False)
        n_follow = sum(1 for e in ev if e["followed"] and not e["forced"])
        n_forced = sum(1 for e in ev if e["forced"])
        tr_a, tr_b = _trace_of(st_a), _trace_of(st_b)
        div = None
        for i, (a, b) in enumerate(zip(tr_a, tr_b)):
            if a != b:
                div = {"i": i, "base": a, "makeplan": b}
                break
        if div is None and len(tr_a) != len(tr_b):
            div = {"i": min(len(tr_a), len(tr_b)), "base": "(end)",
                   "makeplan": "(end)"}
        row = {"game": gid, "base": (ltw_a, oc_a), "makeplan": (ltw_b, oc_b),
               "flip": (ltw_a, oc_a) != (ltw_b, oc_b),
               "installs": n_install, "adopts": n_adopt, "rejects": n_reject,
               "follows_no_force": n_follow, "b100_forced_seats": n_forced,
               "first_divergence": div}
        if faithful:
            # ★1b：忠実比較 vs 鏡（同一盤面での判定照合＝誤採用/誤棄却の数え上げ）
            fl = hp_b._mech_faith_log
            row["faith_cmp"] = len(fl)
            row["faith_adopt"] = sum(1 for e in fl if e["faith"])
            row["fix_wrong_adopt"] = sum(1 for e in fl
                                         if e["mirror"] and not e["faith"])
            row["fix_wrong_reject"] = sum(1 for e in fl
                                          if e["mirror"] is False and e["faith"])
            row["agree"] = sum(1 for e in fl if e["mirror"] is not None
                               and e["mirror"] == e["faith"])
        out.append(row)
        extra = (f" cmp={row['faith_cmp']} 誤採用是正={row['fix_wrong_adopt']}"
                 f" 誤棄却是正={row['fix_wrong_reject']}" if faithful else "")
        print(f"  {gid}: base={ltw_a}/{oc_a} makeplan={ltw_b}/{oc_b}"
              f" installs={n_install} adopts={n_adopt} rejects={n_reject}"
              f" follows={n_follow} forced={n_forced}{extra}"
              f"{' FLIP' if row['flip'] else ''}", flush=True)
        if game:
            row["events"] = ev
            if faithful:
                row["faith_log"] = hp_b._mech_faith_log
    rep = {"days": days, "rows": out,
           "flips": [r["game"] for r in out if r["flip"]],
           "介入0でflip": [r["game"] for r in out
                         if r["flip"] and r["b100_forced_seats"] == 0]}
    if faithful:
        rep["忠実比較の数え上げ"] = {
            "比較回数": sum(r.get("faith_cmp", 0) for r in out),
            "採用": sum(r.get("faith_adopt", 0) for r in out),
            "誤採用の是正（鏡adopt→忠実reject）":
                sum(r.get("fix_wrong_adopt", 0) for r in out),
            "誤棄却の是正（鏡reject→忠実adopt）":
                sum(r.get("fix_wrong_reject", 0) for r in out),
            "鏡と一致": sum(r.get("agree", 0) for r in out)}
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    return rep


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "scan", "mech"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--games", type=str, default=None)
    ap.add_argument("--game", type=str, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--impl", action="store_true")   # ★Phase 1a 実装本体で clean を測る
    ap.add_argument("--faithful", action="store_true")  # ★Phase 1b 実装本体（1a込み）
    ap.add_argument("--no-best", action="store_true")
    ap.add_argument("--json", type=str, default=None)
    a = ap.parse_args()
    if a.cmd == "verify":
        games = set(a.games.split(",")) if a.games else None
        res = verify(a.days, a.loops, a.start, a.end, games)
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif a.cmd == "scan":
        res = scan(a.days, a.loops, a.start, a.end,
                   faithful_best=not a.no_best, json_path=a.json)
        show = {k: v for k, v in res.items() if k != "rows"}
        show["賞金（忠実比較で計画がmargin10以上勝つ）"] = {
            k: v for k, v in
            res["賞金（忠実比較で計画がmargin10以上勝つ）"].items()
            if k != "rows"}
        print(json.dumps(show, ensure_ascii=False, indent=1, default=str))
    else:
        res = mech(a.days, a.loops, a.game, a.all, clean=a.clean,
                   impl=a.impl, faithful=a.faithful, json_path=a.json)
        show = {k: v for k, v in res.items() if k != "rows"}
        print(json.dumps(show, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
