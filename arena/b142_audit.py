# -*- coding: utf-8 -*-
"""B-142：**1/L 冷却札（`不安-1`）の予約**の射程を数える（挙動不変・計測のみ）。

発端＝`docs/監査_B140fu_randomFS_s3の検死_2026-08-03.md`（5日級 `random_FS` s3 の検死）。
実測で確定した機序：

- 主人公の `不安-1` は **1ループ1回**（`engine/models.py:29` `ONCE_PER_LOOP`）＝チーム3枚/ループ。
  脚本家の `不安+1` は手札に**2枚**（`engine/models.py:38`）で、**1/loop ではない**＝
  毎日最大2枚まで置き直せる（`sim/flow.py:251-255` が手札に戻さないのは 1/loop 札だけ）。
  ∴ 事件日より前に撃った冷却は**翌日以降のポンプで戻される**前提で考えるほかない。
- 当該局＝旧は D3/D4/**D5** に1枚ずつ撃ち D5 の1枚で閾値を割って事件が不発。
  新は D2/D3 で3枚使い切り → D5 に札なし → 事件が発生して敗北。

★**§11b の鉄則＝1局から一般則は作れない**。本モジュールは是正を実装する前に
**両ベンチで席を数える**（Phase 1）。数え方は3層に分ける（B-139b の教訓＝
「置いた席数」と「実際に損をした席数」は別物。B-139b では上限38席が価値つきで0席に縮んだ）。

## 3層の定義（すべて実測。推定を混ぜない）

- **L1＝射程候補**（主人公の公開情報＋自分の belief だけで判定できる層）
  `不安-1` を**キャラへ**置いた席のうち、
  (a) **今日ではない**未発生の**危険事件** `d > today`（`_incident_danger[d] >= DANGER_MIN`）があり、
  (b) その事件の**犯人候補**に置いた対象が入っており（`_culprit_cands[d]`・主人公は犯人を
      知らない＝候補集合が単一ソース）、
  (c) **脚本家が再ポンプ可能**（`不安+1` の残り＝公開 `used_cards` から数える。1/loop 札では
      ないので通常は 2 枚／`d > today` なので最低1回はポンプの機会がある）、かつ
  (d) その席が**今日の危険事件の当日冷却を兼ねていない**（兼ねているなら温存の議論は成り立たない）。
- **L2＝真の射程**（棋譜で裏取り）＝L1 のうち、**その事件当日にチームの `不安-1` が
  1枚も残っていなかった**席（＝3席とも `d` より前に使い切った）。
- **L3＝確実な損**＝L2 のうち、その事件が **実際に発生**し、**そのループを落とし**、
  かつ **当日の1枚で止められた**（真犯人の不安が事件フェイズ直前に**ちょうど臨界**＝
  `unrest - threshold == 0`）席。★`margin==0` は神視点スナップショット
  （`主人公能力フェイズ後`＝事件フェイズの直前）から読む。

副題（B-140fu §6c）＝`暗躍禁止` が当日冷却を押しのけた席を、その日のキラー暗躍の遠さで数える。

★プローブは**挙動不変**＝`super().decide()` の**戻り値の後で属性を読むだけ**（rng 非消費）。
`verify` サブコマンドが「素の対局」と「プローブ付き対局」の結末一致で毎回確認する。

CLI（前面実行・測定は必ず PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8）:
    python -m arena.b142_audit --days 3
    python -m arena.b142_audit --days 5 --top 20 --json out.json
    python -m arena.b142_audit verify --days 3       # プローブの挙動不変チェック
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.heuristic_protagonist as _hp_mod
from agents import HeuristicMastermind, HeuristicProtagonist
from engine.data import unrest_threshold_of
from engine.models import MASTERMIND_HAND
from sim import run_game
from sim.state import PROTAGONIST_SEATS

_builtin_max = max

#: 「危険事件」とみなす `_incident_danger` の下限。★AI 自身が使っている閾値を借りる
#  （`agents/heuristic_protagonist.py:4646` の `danger >= 60.0`＝当日ロック20点の条件・
#   `:1159` の `_b77_prep` 成立条件も 60.0）。
DANGER_MIN: float = 60.0

#: キラーが主人公を殺す暗躍数（`agents/heuristic_protagonist.py:102` のコメント
#  「キラー暗躍3封じ＝次の+1で主人公死」＝4 で死）。副題の「遠い」の基準に使う。
KILLER_LETHAL_ANYAKU: int = 4


# ---------------------------------------------------------------------------
# プローブ（挙動不変）
# ---------------------------------------------------------------------------
class _Tally(HeuristicProtagonist):
    """挙動は本体と完全同一（`max` は builtin へ委譲＝選択は不変）。数えるだけ。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.cool_rows: list[dict] = []     # `不安-1` を置いた席（全数）
        self.cool_uses: list[dict] = []     # (loop, day, seat) ＝1/L 札の消費台帳
        self.rival_rows: list[dict] = []    # 副題＝`暗躍禁止` が当日冷却を押しのけた席

    # -- 補助（読むだけ） ---------------------------------------------------
    def _mm_pump_left(self, view: dict) -> int:
        """脚本家の手札に残る `不安+1` の枚数（公開情報＝`used_cards`）。

        `不安+1` は 1/loop 札ではない（`engine/models.py:29`）＝毎ターン手札に戻る。
        ∴ 通常は常に 2（`engine/models.py:38` の `MASTERMIND_HAND` に2枚）。
        """
        base = MASTERMIND_HAND.count("不安+1")
        used = list(view.get("used_cards", {}).get("mastermind", []))
        return base - used.count("不安+1")

    def _future_hits(self, view: dict, tgt: str) -> list[dict]:
        """対象が犯人候補になっている **今日より後**の事件日の一覧（危険度つき）。"""
        day = view.get("day", 1)
        out = []
        for d, danger in (getattr(self, "_incident_danger", {}) or {}).items():
            if d <= day:
                continue
            cands = (getattr(self, "_culprit_cands", {}) or {}).get(d, ())
            if tgt in cands:
                out.append({"day": d, "danger": round(float(danger), 2),
                            "n_cands": len(cands)})
        return sorted(out, key=lambda r: r["day"])

    def _today_hit(self, view: dict, tgt: str) -> dict | None:
        """対象が **今日**の危険事件の犯人候補か（＝当日冷却の席）。"""
        day = view.get("day", 1)
        danger = float((getattr(self, "_incident_danger", {}) or {}).get(day, 0.0))
        cands = (getattr(self, "_culprit_cands", {}) or {}).get(day, ())
        if tgt in cands:
            return {"day": day, "danger": round(danger, 2), "n_cands": len(cands)}
        return None

    # -- decide（super の戻り値の後で読むだけ） -----------------------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        cap: dict = {}

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
        if decision == "set_card":
            self._record(view, options, chosen, cap.get("scored", []))
        return chosen

    def _record(self, view: dict, options: list[dict], chosen: dict,
                scored: list) -> None:
        loop, day = view.get("loop"), view.get("day")
        seat = view.get("seat")
        card, kind = chosen.get("card"), chosen.get("target_kind")
        tgt = chosen.get("target")
        by_key = {(round(s, 4), o["card"], o["target"], o.get("target_kind"))
                  for s, o in scored}
        top = scored[0][0] if scored else None

        # ---- `不安-1` を置いた席 -----------------------------------------
        if card == "不安-1":
            # 幻想の板置き（B-133）は実効的にキャラ冷却＝同じ土俵で数える。
            eff = self._b133_cool_target(view, chosen) if kind == "board" else tgt
            self.cool_uses.append({"loop": loop, "day": day, "seat": seat,
                                   "target": eff, "kind": kind})
            if eff is not None:
                th = unrest_threshold_of(eff)
                fut = self._future_hits(view, eff)
                today = self._today_hit(view, eff)
                pump = self._mm_pump_left(view)
                danger_fut = [f for f in fut if f["danger"] >= DANGER_MIN]
                row = {"loop": loop, "day": day, "seat": seat, "target": eff,
                       "kind": kind, "threshold": th, "score": round(top, 2)
                       if top is not None else None,
                       "future_hits": fut, "today_hit": today,
                       "mm_pump_left": pump,
                       "days_per_loop": view.get("days_per_loop"),
                       "unrest": next((c.get("unrest") for c in view["characters"]
                                       if c.get("name") == eff), None)}
                # ★L1 判定（主人公が見える情報だけ）
                row["L1"] = bool(danger_fut and pump >= 1 and th
                                 and (today is None or today["danger"] < DANGER_MIN))
                row["L1_days"] = [f["day"] for f in danger_fut]
                self.cool_rows.append(row)

        # ---- 副題＝`暗躍禁止` が **当日**の冷却を押しのけた席 ---------------
        if card == "暗躍禁止" and kind == "character":
            day_i = view.get("day")
            danger_today = float((getattr(self, "_incident_danger", {})
                                  or {}).get(day_i, 0.0))
            cands_today = (getattr(self, "_culprit_cands", {}) or {}).get(day_i, ())
            if danger_today >= DANGER_MIN and cands_today and scored:
                cool = [(s, o) for s, o in scored
                        if o["card"] == "不安-1" and o.get("target_kind") == "character"
                        and o["target"] in cands_today]
                if cool and cool[0][0] < top:
                    c_t = next((c for c in view["characters"]
                                if c.get("name") == tgt), None)
                    any_t = (c_t or {}).get("anyaku", 0) or 0
                    self.rival_rows.append({
                        "loop": loop, "day": day_i, "seat": seat,
                        "ban_target": tgt, "ban_score": round(top, 2),
                        "ban_anyaku": any_t,
                        "ban_is_killer_strong": tgt in (
                            getattr(self, "_killer_strong", set()) or set()),
                        "killer_gap": KILLER_LETHAL_ANYAKU - any_t,
                        "cool_target": cool[0][1]["target"],
                        "cool_score": round(cool[0][0], 2),
                        "danger_today": round(danger_today, 2),
                        "n_cands_today": len(cands_today),
                    })
        _ = by_key   # （将来の突き合わせ用に確保。現在は未使用）


# ---------------------------------------------------------------------------
# 棋譜からの裏取り（L2 / L3）
# ---------------------------------------------------------------------------
def _incident_facts(state) -> dict:
    """(loop, day) -> 事件の実測（発生したか・真犯人・臨界からの差）。

    - 発生/不発＝公開イベント `incident`（`sim/effects.py:352` 他）。
    - 真犯人＝脚本（神視点＝**監査ハーネスだけが見る**）。
    - `margin` ＝**事件フェイズ直前**（スナップショット `主人公能力フェイズ後`＝
      `sim/flow.py:276`／事件は `:287-289`）の 真犯人の `不安 - 臨界`。
      `margin == 0` なら **`不安-1` 1枚で発生を止められた**。
    """
    culprit_of = {i.day: i.culprit for i in state.script.incidents}
    snaps: dict[tuple, dict] = {}
    for s in state.phase_snapshots:
        if s.get("point") == "主人公能力フェイズ後":
            snaps[(s.get("loop"), s.get("day"))] = s
    out: dict[tuple, dict] = {}
    for e in state.history:
        if e.get("event") != "incident":
            continue
        key = (e.get("loop"), e.get("day"))
        culp = culprit_of.get(e.get("day"))
        th = unrest_threshold_of(culp) if culp else None
        snap = snaps.get(key)
        margin = None
        if snap and culp and th is not None:
            c = snap.get("characters", {}).get(culp)
            if c and c.get("alive"):
                margin = int(c.get("unrest", 0)) - int(th)
        out[key] = {"occurs": bool(e.get("occurs")), "name": e.get("name"),
                    "culprit": culp, "threshold": th, "margin": margin}
    return out


def _lost_loops(state) -> set[int]:
    """敗北した（守れなかった）ループ番号の集合＝公開イベント `loop_result`。"""
    return {e.get("loop") for e in state.history
            if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}


def _outcome(state) -> str:
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return "defense"
    if fb:
        return "fb_win" if state.winner == "protagonist" else "fb_loss"
    return "loss"


def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Tally(seed)
    state, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                "p1": hp, "p2": hp, "p3": hp})
    facts = _incident_facts(state)
    lost = _lost_loops(state)
    outcome = _outcome(state)
    # 1/L 台帳＝(loop) -> {day: {seat,...}}
    used_by_loop: dict[int, list[dict]] = {}
    for u in hp.cool_uses:
        used_by_loop.setdefault(u["loop"], []).append(u)
    reached = {(e.get("loop"), e.get("day")) for e in state.history}

    for r in hp.cool_rows:
        if not r["L1"]:
            continue
        lp = r["loop"]
        uses = used_by_loop.get(lp, [])
        hits = []
        for d in r["L1_days"]:
            if (lp, d) not in reached:
                hits.append({"day": d, "reached": False})
                continue
            # その事件日より**前**に札を使った席の数（3席使い切り＝当日に札なし）
            spent_before = {u["seat"] for u in uses if u["day"] < d}
            used_on_d = {u["seat"] for u in uses if u["day"] == d}
            f = facts.get((lp, d), {})
            hits.append({
                "day": d, "reached": True,
                "n_spent_before": len(spent_before),
                "no_card_on_day": len(spent_before) >= len(PROTAGONIST_SEATS),
                "cooled_on_day": sorted(used_on_d),
                # 参考＝当日、実際に**真犯人**を冷やした席があったか（札はあったのに
                # 別の対象へ置いた＝席負け／照準ミスの分離用。L2 の定義には使わない）。
                "cooled_culprit_on_day": any(
                    u["day"] == d and u["target"] == f.get("culprit")
                    for u in uses),
                "occurs": f.get("occurs"), "incident": f.get("name"),
                "culprit": f.get("culprit"), "margin": f.get("margin"),
                "loop_lost": lp in lost,
                "target_is_culprit": r["target"] == f.get("culprit"),
            })
        r["hits"] = hits
        r["L2"] = any(h.get("no_card_on_day") for h in hits)
        r["L2_occurred"] = any(h.get("no_card_on_day") and h.get("occurs")
                               for h in hits)
        r["L2_occ_lost"] = any(h.get("no_card_on_day") and h.get("occurs")
                               and h.get("loop_lost") for h in hits)
        r["L3"] = any(h.get("no_card_on_day") and h.get("occurs")
                      and h.get("loop_lost") and h.get("margin") == 0
                      and h.get("target_is_culprit") for h in hits)
        # ★広い版（参考）＝「当日に真犯人を冷やせなかった」（札切れ **または** 席負け）。
        r["L2b"] = any(h.get("reached") and not h.get("cooled_culprit_on_day")
                       for h in hits)
        r["L3b"] = any(h.get("reached") and not h.get("cooled_culprit_on_day")
                       and h.get("occurs") and h.get("loop_lost")
                       and h.get("margin") == 0 for h in hits)
        r["L3_loose"] = any(h.get("no_card_on_day") and h.get("occurs")
                            and h.get("loop_lost") and h.get("margin") == 0
                            for h in hits)
        r["game_outcome"] = outcome
    return {"outcome": outcome, "cool_rows": hp.cool_rows,
            "rival_rows": hp.rival_rows, "n_cool_uses": len(hp.cool_uses),
            "final_loop": state.loop_no}


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, verbose: bool = False,
        start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    hits: list[dict] = []
    rivals: list[dict] = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = audit_game(sc, seed, loops=loops)
        c["games"] += 1
        c["cool_seats"] += len(res["cool_rows"])
        for r in res["cool_rows"]:
            if r["today_hit"] and r["today_hit"]["danger"] >= DANGER_MIN:
                c["dayof_seats"] += 1
            if not r["L1"]:
                continue
            c["L1"] += 1
            if r.get("L2"):
                c["L2"] += 1
            if r.get("L2_occurred"):
                c["L2_occurred"] += 1
            if r.get("L2_occ_lost"):
                c["L2_occ_lost"] += 1
            if r.get("L3_loose"):
                c["L3_loose"] += 1
            if r.get("L3"):
                c["L3"] += 1
            if r.get("L2b"):
                c["L2b_no_culprit_cool"] += 1
            if r.get("L3b"):
                c["L3b"] += 1
            hits.append({"script": name, "seed": seed, **r})
        for rv in res["rival_rows"]:
            c["rival_seats"] += 1
            if rv["killer_gap"] >= 3:      # 暗躍≤1＝`暗躍+2` 1枚でも致死に届かない
                c["rival_far"] += 1
            if rv["ban_is_killer_strong"]:
                c["rival_killer_strong"] += 1
                if rv["killer_gap"] >= 3:
                    c["rival_strong_far"] += 1
            rivals.append({"script": name, "seed": seed, **rv})
        if verbose:
            print(f"  {name} s{seed}: cool={len(res['cool_rows'])} "
                  f"L1={sum(1 for r in res['cool_rows'] if r['L1'])} "
                  f"[{res['outcome']}]", flush=True)
    return {"days": days, "counts": dict(c), "hits": hits, "rivals": rivals}


def verify(days: int = 3, loops: int = 8, n: int = 12) -> int:
    """プローブの挙動不変（素の対局と結末・棋譜長が一致するか）を確認する。"""
    from arena.benchmark import benchmark_scripts

    bad = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[:n]:
        probe = replace(sc, loops=loops)
        s0, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": (h0 := HeuristicProtagonist(seed)),
                                 "p2": h0, "p3": h0})
        s1, _ = run_game(replace(sc, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": (h1 := _Tally(seed)), "p2": h1, "p3": h1})
        ok = (s0.winner == s1.winner and s0.loop_no == s1.loop_no
              and len(s0.history) == len(s1.history)
              and [e.get("event") for e in s0.history]
              == [e.get("event") for e in s1.history])
        print(f"  {name} s{seed}: {'一致' if ok else '★不一致'}"
              f" winner={s0.winner}/{s1.winner} loop={s0.loop_no}/{s1.loop_no}"
              f" hist={len(s0.history)}/{len(s1.history)}", flush=True)
        bad += 0 if ok else 1
    print(f"不一致 = {bad} 件")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count", choices=["count", "verify"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        return verify(days=a.days, loops=a.loops)
    print(f"[切替口] DANGER_MIN={DANGER_MIN} / KILLER_LETHAL_ANYAKU="
          f"{KILLER_LETHAL_ANYAKU} / days={a.days} loops={a.loops}")
    res = run(days=a.days, loops=a.loops, verbose=a.verbose,
              start=a.start, end=a.end)
    c = res["counts"]
    print(f"== B-142：1/L 冷却札の予約 — 射程の数え上げ（{a.days}日級 "
          f"{c.get('games', 0)}局）==")
    print(f"  `不安-1` を置いた席（全数）= {c.get('cool_seats', 0)}"
          f"（うち当日冷却＝{c.get('dayof_seats', 0)}）")
    print(f"★L1 射程候補（未発生の危険事件・犯人候補・再ポンプ可・当日冷却でない）"
          f"= {c.get('L1', 0)}")
    print(f"★L2 真の射程（その事件当日にチームの札が0枚）= {c.get('L2', 0)}")
    print(f"    ├ うち事件が実際に発生 = {c.get('L2_occurred', 0)}")
    print(f"    ├ うちそのループを落とした = {c.get('L2_occ_lost', 0)}")
    print(f"    ├ うち当日1枚で止まった（margin==0）= {c.get('L3_loose', 0)}")
    print(f"★L3 確実な損（＋温存した対象が真犯人）= {c.get('L3', 0)}")
    print(f"  （参考・広い版）当日に真犯人を冷やせなかった席（札切れ or 席負け）"
          f"= {c.get('L2b_no_culprit_cool', 0)}"
          f" / うち 発生∧敗ループ∧margin==0 = {c.get('L3b', 0)}")
    print("")
    print(f"== 副題＝`暗躍禁止` が当日冷却を押しのけた席 ==")
    print(f"  席数 = {c.get('rival_seats', 0)}"
          f" / うちキラー暗躍が遠い(≤1) = {c.get('rival_far', 0)}"
          f" / うち確信キラー = {c.get('rival_killer_strong', 0)}"
          f" / ★確信キラー∧遠い = {c.get('rival_strong_far', 0)}")
    hits = [h for h in res["hits"] if h.get("L2")]
    if hits and a.top:
        print("")
        print("  L2 席の一覧（局 / L D 席 / 対象 / 事件日 / 発生 / margin / 敗ループ）")
        for h in hits[:a.top]:
            for d in h.get("hits", []):
                if not d.get("no_card_on_day"):
                    continue
                print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                      f"/{h['seat']} → {h['target']} | 事件D{d['day']}"
                      f"({d.get('incident')}) | "
                      f"{'発生' if d.get('occurs') else '不発'} | "
                      f"margin={d.get('margin')} | "
                      f"{'敗' if d.get('loop_lost') else '防衛'} | "
                      f"真犯人={d.get('culprit')} | [{h.get('game_outcome')}]")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
