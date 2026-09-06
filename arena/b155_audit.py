# -*- coding: utf-8 -*-
"""B-155：**mm札の囮で 1/L 冷却札（`不安-1`）が焼かれる**射程を数える（★計測のみ・挙動不変）。

## 発端

ユーザー実戦（2026-08-04・脚本家側・FS 3日級 seed 0）の棋譜で、脚本家が L1D1 に
`不安+1` を1枚も置いていないのに、主人公が **1/L の `不安-1` を初日に2枚**焼いた。

原因＝`agents/heuristic_protagonist.py:4838` の空振りゲート（`card_effect.noop_reason` G2＝
`agents/card_effect.py:220-223`）：

```python
if c and c.get("unrest", 0) == 0 and target not in ctx.mm_chars:
    return Noop("不安0＋mm札なし＝床0で空振り", NOOP_SCORE)
```

★例外条項「`target in ctx.mm_chars` なら通常判定へ」に**距離の判定が無い**＝
伏せ札の中身は見えないので、脚本家の `移動` 札がそのまま冷却札を焼く囮になる。

## 層（★上限値を射程と読まない＝B-139b/B-142/B-149 の教訓）

- **L1**＝`不安-1` を **`unrest==0` の対象**へ置いた席（全数）。
  ★`不安-1` を板へ置いて幻想を冷やす読み替え（B-133・`_b133_cool_target`）も
  「実効的にキャラ冷却」として同じ土俵で数える（b142_audit と同じ作法）。
- **L2**＝そのうち **mm がその対象に札を伏せていた**席（＝例外条項が実際に効いた席）。
  判定材料は**主人公が見える情報**＝`view["placements"]` の位置だけ（中身は伏せ）。
- **L3**＝そのうち **実際にその伏せ札が `不安+1` だった**席（＝先回りが当たった席）。
  ★神視点＝`state.phase_snapshots` の `行動解決中`（`reveal_cards=True`＝`sim/flow.py:183`）の
  `turn_placements` から**事後に**読む（決定時には見ていない＝挙動不変）。
- **L4**＝そのうち **臨界まで遠かった**（`th - unrest >= 2`）席＝当たっても意味が薄い席。
  `unrest==0` なので実質 `th >= 2`。★臨界0（黒猫）は `th` が 0/None ＝別枠に数える。

## ★逆向き（温存していたら後で使えたのに使えなかった席）

B-142（`arena/b142_audit.py`）の「事件当日に札が残っていなかった」の数え方を**再利用**する
（`_incident_facts` / `_lost_loops` / `_outcome` を **import** ＝二重実装しない）。

- **R1**＝L1席のうち、**同じループの後日**に「チームの `不安-1` が0枚」の日が存在する席
  （＝この1枚を温存していれば、その日に打てた）。会計は b142 と同一規約＝
  棋譜の消費台帳から「その日より前に使った席数 ≥ 3」。
- **R2**＝R1 のうち、その札切れの日に事件が**実際に発生**した席。
- **R3**＝R2 のうち、**そのループを落とし**、かつ **`margin == 0`**（真犯人の不安がちょうど臨界＝
  `不安-1` 1枚で止められた）席＝**確実な損**。

## ★Phase 1b＝L1D1 定石（B-45）との競合

`agents/heuristic_protagonist.py:5933-5951` は L1D1 のみ定石レイヤを **floor** で当てる。
- 定石3前半＝`_opening_plus2_rank`（当日発動可の不安除去役へ `友好+2`・34.0）
- 定石3後半＝`_opening_prep_move`（準備移動・35.0）

**L1D1 で `不安-1` を選んだ席**に、これらの定石候補が**候補として立っていたか**を数える
（＝定石が `不安-1` に席を奪われた席）。★判定は**候補の存在**＝`options` を後から
同じヘルパに通すだけ（純関数・rng 非消費）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b155_audit verify --days 3
    python -m arena.b155_audit count  --days 3 --json d3.json
    python -m arena.b155_audit count  --days 5 --json d5.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.heuristic_protagonist as _hp_mod
from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b142_audit import _incident_facts, _lost_loops, _outcome
from engine.data import unrest_threshold_of
from sim import run_game
from sim.state import PROTAGONIST_SEATS

_builtin_max = max

#: 「危険事件」とみなす `_incident_danger` の下限（**b142_audit.DANGER_MIN と同値**＝
#  AI 自身の閾値 `agents/heuristic_protagonist.py:4879` の `danger >= 60.0`）。
#  ★参考指標にしか使わない（層の定義には使わない）。
DANGER_MIN: float = 60.0

#: 是正案の述語で使う「臨界まで近い」の境界（Phase 2 の掃引口の**下見**。
#  ここでは**数え上げの層分けにしか使わない**＝挙動には一切影響しない）。
NEAR_TH_GAP: int = 1


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝`super().decide()` の**戻り値の後で読むだけ**・rng 非消費）
# ---------------------------------------------------------------------------
class _Tally(HeuristicProtagonist):
    """挙動は本体と完全同一（`max` は builtin へ委譲＝選択は不変）。数えるだけ。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.cool_rows: list[dict] = []   # `不安-1`×unrest0 の席（L1）
        self.cool_uses: list[dict] = []   # (loop, day, seat) ＝1/L 札の消費台帳（b142 と同型）
        self.opening_rows: list[dict] = []  # L1D1 の席（Phase 1b）

    # -- 補助（読むだけ） ---------------------------------------------------
    @staticmethod
    def _mm_targets(view: dict) -> set[str]:
        """mm が今ターン**キャラ**に札を伏せた対象（位置は公開・中身は伏せ）。

        ★`agents/heuristic_protagonist.py` が `mm_char_now` を組むのと同じ材料
        （`view["placements"]`＝`sim/views._masked_placements`）。
        """
        return {p["target"] for p in view.get("placements", [])
                if p.get("owner") == "mastermind"
                and p.get("target_kind") == "character"}

    def _unrest_of(self, view: dict, name: str) -> int | None:
        c = next((x for x in view["characters"] if x.get("name") == name), None)
        return None if c is None else int(c.get("unrest", 0) or 0)

    # -- decide -------------------------------------------------------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        cap: dict = {}

        def spymax(*args, **kw):
            # ★`options` そのもの（同一オブジェクト）に対する max だけを捕まえる
            #   ＝最終選択の採点表。`_plan_turn` 内部の max は捕まえない。
            if args and args[0] is options and "key" in kw and "scored" not in cap:
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
        loop, day, seat = view.get("loop"), view.get("day"), view.get("seat")
        card, kind, tgt = (chosen.get("card"), chosen.get("target_kind"),
                           chosen.get("target"))
        top = scored[0][0] if scored else None
        mm_t = self._mm_targets(view)

        # ---- 1/L 札の消費台帳（逆向きの会計に使う。b142 と同一の材料） ----
        if card == "不安-1":
            eff = self._b133_cool_target(view, chosen) if kind == "board" else tgt
            self.cool_uses.append({"loop": loop, "day": day, "seat": seat,
                                   "target": eff, "kind": kind})
            # ---- L1＝unrest==0 の対象への `不安-1` --------------------------
            u = self._unrest_of(view, eff) if eff else None
            if eff is not None and u == 0:
                th = unrest_threshold_of(eff)
                self.cool_rows.append({
                    "loop": loop, "day": day, "seat": seat, "target": eff,
                    "kind": kind, "unrest": u, "threshold": th,
                    "score": round(top, 2) if top is not None else None,
                    "scored_available": bool(scored),
                    # L2＝mm がその対象に札を伏せていた（主人公が見える情報だけ）
                    "L2_mm_card": eff in mm_t,
                    # L4＝臨界まで遠い（unrest==0 ゆえ th>=2）。th 0/None は別枠。
                    "th_none": th in (None, 0),
                    "L4_far": bool(th) and (int(th) - int(u)) >= 2,
                    "near_th": bool(th) and (int(th) - int(u)) <= NEAR_TH_GAP,
                    # 参考＝この対象は今日/未来の危険事件の犯人候補か
                    "cand_days": sorted(
                        d for d, dg in (getattr(self, "_incident_danger", {})
                                        or {}).items()
                        if float(dg) >= DANGER_MIN
                        and eff in (getattr(self, "_culprit_cands", {})
                                    or {}).get(d, ())),
                    "days_per_loop": view.get("days_per_loop"),
                })

        # ---- Phase 1b＝L1D1 の席で定石候補が立っていたか -------------------
        if loop == 1 and day == 1:
            plus2, prep = [], []
            for o in options:
                if o.get("card") == "友好+2":
                    r = self._opening_plus2_rank(o.get("target"), view)
                    if r is not None:
                        plus2.append((o.get("target"), r))
                else:
                    r = self._opening_prep_move(o, view)
                    if r is not None:
                        prep.append((o.get("target"), o.get("card"), r))
            by_opt = {}
            for s, o in scored:
                by_opt[(o["card"], o["target"], o.get("target_kind"))] = s
            self.opening_rows.append({
                "loop": loop, "day": day, "seat": seat,
                "chosen_card": card, "chosen_target": tgt, "chosen_kind": kind,
                "chosen_score": round(top, 2) if top is not None else None,
                "scored_available": bool(scored),
                "n_plus2_book": len(plus2),
                "n_prep_book": len(prep),
                "plus2_book": [
                    {"target": t, "floor": r,
                     "score": (None if (_s := by_opt.get(("友好+2", t, "character")))
                               is None else round(_s, 2))}
                    for t, r in plus2],
                "prep_book": [
                    {"target": t, "card": cd, "floor": r,
                     "score": (None if (_s := by_opt.get((cd, t, "character")))
                               is None else round(_s, 2))}
                    for t, cd, r in prep],
                # この席が `不安-1` を unrest==0 の対象へ置いたか
                "cool_zero": bool(
                    card == "不安-1"
                    and (eff0 := (self._b133_cool_target(view, chosen)
                                  if kind == "board" else tgt)) is not None
                    and self._unrest_of(view, eff0) == 0),
            })


# ---------------------------------------------------------------------------
# 神視点の裏取り（L3＝伏せ札の中身／逆向きの R2/R3）
# ---------------------------------------------------------------------------
def _revealed_mm_cards(state) -> dict[tuple, list[dict]]:
    """(loop, day) -> その日 mm が伏せた札の一覧（**表向き**）。

    出典＝`sim/flow.py:183` の `state.snapshot("行動解決中", reveal_cards=True)`
    （6枚が全公開された直後の盤面）。★決定時には見ていない＝事後の裏取り専用。
    """
    out: dict[tuple, list[dict]] = {}
    for s in state.phase_snapshots:
        if s.get("point") != "行動解決中":
            continue
        out[(s.get("loop"), s.get("day"))] = [
            dict(p) for p in s.get("turn_placements", [])
            if p.get("owner") == "mastermind"]
    return out


def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Tally(seed)
    state, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                "p1": hp, "p2": hp, "p3": hp})
    facts = _incident_facts(state)
    lost = _lost_loops(state)
    outcome = _outcome(state)
    mm_cards = _revealed_mm_cards(state)
    reached = {(e.get("loop"), e.get("day")) for e in state.history}

    used_by_loop: dict[int, list[dict]] = {}
    for u in hp.cool_uses:
        used_by_loop.setdefault(u["loop"], []).append(u)

    for r in hp.cool_rows:
        key = (r["loop"], r["day"])
        placed = [p for p in mm_cards.get(key, [])
                  if p.get("target_kind") == "character"
                  and p.get("target") == r["target"]]
        r["mm_cards_on_target"] = sorted(p.get("card") for p in placed)
        r["L3_hit"] = "不安+1" in r["mm_cards_on_target"]

        # ---- 逆向き（R1/R2/R3）＝b142 の「当日に札が残っていたか」の会計 ----
        uses = used_by_loop.get(r["loop"], [])
        later: list[dict] = []
        for d in range(int(r["day"]) + 1, int(r.get("days_per_loop") or 0) + 1):
            if (r["loop"], d) not in reached:
                continue
            spent_before = {u["seat"] for u in uses if u["day"] < d}
            if len(spent_before) < len(PROTAGONIST_SEATS):
                continue          # その日はまだ札が残っている＝温存の議論にならない
            f = facts.get((r["loop"], d), {})
            later.append({
                "day": d, "no_card_on_day": True,
                "occurs": f.get("occurs"), "incident": f.get("name"),
                "culprit": f.get("culprit"), "margin": f.get("margin"),
                "loop_lost": r["loop"] in lost,
            })
        r["dry_days"] = later
        r["R1"] = bool(later)
        r["R2"] = any(h.get("occurs") for h in later)
        r["R3"] = any(h.get("occurs") and h.get("loop_lost")
                      and h.get("margin") == 0 for h in later)
        r["game_outcome"] = outcome

    for r in hp.opening_rows:
        r["game_outcome"] = outcome
    return {"outcome": outcome, "cool_rows": hp.cool_rows,
            "opening_rows": hp.opening_rows, "cool_uses": hp.cool_uses,
            "mm_days": sorted(mm_cards.keys()),
            "n_cool_uses": len(hp.cool_uses), "final_loop": state.loop_no}


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, verbose: bool = False,
        start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    per_script: dict[str, Counter] = {}
    mm_card_mix: Counter = Counter()
    rows: list[dict] = []
    op_rows: list[dict] = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = audit_game(sc, seed, loops=loops)
        c["games"] += 1
        ps = per_script.setdefault(name, Counter())
        ps["games"] += 1
        c["cool_uses"] += res["n_cool_uses"]
        ps["cool_uses"] += res["n_cool_uses"]
        for r in res["cool_rows"]:
            for k in ("L1",):
                c[k] += 1
                ps[k] += 1
            if r["L2_mm_card"]:
                c["L2"] += 1
                ps["L2"] += 1
                for cd in r["mm_cards_on_target"]:
                    mm_card_mix[cd] += 1
                if r["L3_hit"]:
                    c["L3"] += 1
                    ps["L3"] += 1
                if r["L4_far"]:
                    c["L4"] += 1
                    ps["L4"] += 1
                    if r["L3_hit"]:
                        c["L3_and_L4"] += 1
                if r["near_th"]:
                    c["L2_near"] += 1
                if r["th_none"]:
                    c["L2_th_none"] += 1
            else:
                c["L1_no_mm_card"] += 1
            if r["th_none"]:
                c["L1_th_none"] += 1
            if r["R1"]:
                c["R1"] += 1
                ps["R1"] += 1
                if r["L2_mm_card"] and r["L4_far"]:
                    c["R1_and_L4"] += 1
            if r["R2"]:
                c["R2"] += 1
                ps["R2"] += 1
            if r["R3"]:
                c["R3"] += 1
                ps["R3"] += 1
                if r["L2_mm_card"] and r["L4_far"]:
                    c["R3_and_L4"] += 1
            rows.append({"script": name, "seed": seed, **r})
        for r in res["opening_rows"]:
            c["op_seats"] += 1
            has_book = (r["n_plus2_book"] + r["n_prep_book"]) > 0
            if has_book:
                c["op_book_present"] += 1
            if r["chosen_card"] == "不安-1":
                c["op_chose_cool"] += 1
                if r["cool_zero"]:
                    c["op_chose_cool_zero"] += 1
                if has_book:
                    c["op_book_lost_to_cool"] += 1
                    ps["op_book_lost_to_cool"] += 1
                    if r["cool_zero"]:
                        c["op_book_lost_to_cool_zero"] += 1
            op_rows.append({"script": name, "seed": seed, **r})
        if verbose:
            print(f"  {name} s{seed}: L1={len(res['cool_rows'])} "
                  f"[{res['outcome']}]", flush=True)
    return {"days": days, "counts": dict(c),
            "per_script": {k: dict(v) for k, v in per_script.items()},
            "mm_card_mix": dict(mm_card_mix), "rows": rows, "op_rows": op_rows}


def verify(days: int = 3, loops: int = 8, n: int = 12) -> int:
    """プローブの挙動不変（素の対局と結末・棋譜長・イベント列が一致するか）。"""
    from arena.benchmark import benchmark_scripts

    bad = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[:n]:
        s0, _ = run_game(replace(sc, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
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


def selfcheck(days: int = 3, loops: int = 8, n: int = 20) -> int:
    """★計測器の自己検査（B-149/B-150 の `verify` 相当＝**自分のバグを捕まえる**）。

    1. **L2 の内部整合**＝`L2_mm_card`（主人公が見た「位置」）が真なら、神視点の
       `mm_cards_on_target` は**必ず非空**（見えている位置に札があるのは同義）。逆も真。
    2. **L3 ⊆ L2**、**L4 ⊆ L2**、**R2 ⊆ R1**、**R3 ⊆ R2**（層の包含）。
    3. **L1 の定義**＝記録された行はすべて `unrest == 0`。
    4. **台帳の健全性**＝`cool_uses` は (loop, seat) につき**1回以下**
       （`不安-1` は 1/loop 札＝`engine/models.py:29`）。
    5. **`行動解決中` スナップショットの網羅**＝L1 行の (loop, day) は必ず存在する。
    """
    from arena.benchmark import benchmark_scripts

    bad = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[:n]:
        res = audit_game(sc, seed, loops=loops)
        mm_days = set(res["mm_days"])
        errs: list[str] = []
        # 4. 1/loop 台帳
        seen: Counter = Counter()
        for u in res["cool_uses"]:
            seen[(u["loop"], u["seat"])] += 1
        over = [k for k, v in seen.items() if v > 1]
        if over:
            errs.append(f"1/L 台帳が二重消費: {over[:3]}")
        for r in res["cool_rows"]:
            if r["unrest"] != 0:
                errs.append(f"L1 定義違反 unrest={r['unrest']}")
            has_god = bool(r["mm_cards_on_target"])
            if bool(r["L2_mm_card"]) != has_god:
                errs.append(f"L2 の見え/神視点が不一致 {r['loop']}D{r['day']}"
                            f" {r['target']} view={r['L2_mm_card']} god={has_god}")
            if r["L3_hit"] and not r["L2_mm_card"]:
                errs.append("L3 ⊄ L2")
            if r["L4_far"] and r["th_none"]:
                errs.append("L4 と th_none が同時成立")
            if r["R2"] and not r["R1"]:
                errs.append("R2 ⊄ R1")
            if r["R3"] and not r["R2"]:
                errs.append("R3 ⊄ R2")
            if (r["loop"], r["day"]) not in mm_days:
                errs.append(f"行動解決中スナップショット欠落 L{r['loop']}D{r['day']}")
        print(f"  {name} s{seed}: L1={len(res['cool_rows'])}"
              f" {'OK' if not errs else '★' + ' / '.join(errs[:3])}", flush=True)
        bad += 1 if errs else 0
    print(f"自己検査 NG = {bad} 局")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "selfcheck"])
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
    if a.cmd == "selfcheck":
        return selfcheck(days=a.days, loops=a.loops)
    print(f"[切替口] DANGER_MIN={DANGER_MIN} / NEAR_TH_GAP={NEAR_TH_GAP} "
          f"/ days={a.days} loops={a.loops}")
    res = run(days=a.days, loops=a.loops, verbose=a.verbose,
              start=a.start, end=a.end)
    c = res["counts"]
    n_scripts = len(res["per_script"])
    print(f"== B-155：mm札の囮で焼かれる冷却札 — 射程の数え上げ"
          f"（{a.days}日級 {c.get('games', 0)}局／独立脚本名 {n_scripts}本）==")
    print(f"  `不安-1` を置いた席（全数）= {c.get('cool_uses', 0)}")
    print(f"★L1 `unrest==0` の対象へ置いた席 = {c.get('L1', 0)}"
          f"（うち臨界0/None＝{c.get('L1_th_none', 0)}）")
    print(f"  ├ mm札**なし**（＝空振りゲートを潜っていない席）= "
          f"{c.get('L1_no_mm_card', 0)}")
    print(f"★L2 mm がその対象に札を伏せていた（例外条項が効いた席）= {c.get('L2', 0)}")
    print(f"★L3 うち実際にその伏せ札が `不安+1` だった = {c.get('L3', 0)}")
    print(f"★L4 うち臨界まで遠い（th-unrest>=2）= {c.get('L4', 0)}"
          f"／L3∧L4 = {c.get('L3_and_L4', 0)}")
    print(f"  （参考）L2 のうち臨界まで近い（th-unrest<={NEAR_TH_GAP}）"
          f"= {c.get('L2_near', 0)}／臨界0/None = {c.get('L2_th_none', 0)}")
    print("")
    print("== ★逆向き＝温存していたら後で使えたのに使えなかった席 ==")
    print(f"★R1 同ループの後日に「チームの札0枚」の日がある = {c.get('R1', 0)}"
          f"（うち L2∧L4 = {c.get('R1_and_L4', 0)}）")
    print(f"★R2 うちその日に事件が実際に発生 = {c.get('R2', 0)}")
    print(f"★R3 うちループを落とし margin==0（1枚で止まった）= {c.get('R3', 0)}"
          f"（うち L2∧L4 = {c.get('R3_and_L4', 0)}）")
    print("")
    print("== 伏せ札の中身の内訳（L2 席で対象に載っていた mm札）==")
    for cd, n in sorted(res["mm_card_mix"].items(), key=lambda x: -x[1]):
        print(f"   {cd}: {n}")
    print("")
    print("== ★Phase 1b＝L1D1 定石（B-45）との競合 ==")
    print(f"  L1D1 の席（全数）= {c.get('op_seats', 0)}"
          f"／うち定石候補が立っていた席 = {c.get('op_book_present', 0)}")
    print(f"  うち `不安-1` を選んだ席 = {c.get('op_chose_cool', 0)}"
          f"（うち対象 unrest==0 = {c.get('op_chose_cool_zero', 0)}）")
    print(f"★定石が候補に立ちながら `不安-1` に負けた席 = "
          f"{c.get('op_book_lost_to_cool', 0)}"
          f"（うち対象 unrest==0 = {c.get('op_book_lost_to_cool_zero', 0)}）")
    print("")
    print("== 脚本名別（★seed 複製の水増しを結論に使わないための内訳）==")
    print("   脚本名                局   L1   L2   L3   L4   R1   R3  定石負け")
    for nm, p in sorted(res["per_script"].items()):
        print(f"   {nm:<20} {p.get('games',0):>3} {p.get('L1',0):>4} "
              f"{p.get('L2',0):>4} {p.get('L3',0):>4} {p.get('L4',0):>4} "
              f"{p.get('R1',0):>4} {p.get('R3',0):>4} "
              f"{p.get('op_book_lost_to_cool',0):>6}")
    if a.top:
        r3 = [r for r in res["rows"] if r.get("R3")]
        if r3:
            print("")
            print("  R3 席の一覧（局 / L D 席 / 対象 / 臨界 / mm札 / 札切れ日）")
            for r in r3[:a.top]:
                dry = ",".join(f"D{h['day']}({h.get('incident')},"
                               f"margin={h.get('margin')})"
                               for h in r["dry_days"] if h.get("occurs"))
                print(f"   {r['script']}(s{r['seed']}) L{r['loop']}D{r['day']}"
                      f"/{r['seat']} → {r['target']} | th={r['threshold']}"
                      f" | mm={r['mm_cards_on_target']} | {dry}"
                      f" | [{r.get('game_outcome')}]")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
