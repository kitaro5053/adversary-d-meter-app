# -*- coding: utf-8 -*-
"""B-143：**冷却役投資(81.0/78.5) が「その日に効く手」を押しのける**射程を数える。

★挙動不変・計測のみ（Phase 1）。

発端＝`docs/監査_B141b_主人公側rule-rational免除の第1号_2026-08-03.md` §3-2。
版 `(b)`（`_unlock` の off-by-one 是正＝**規則上正しい修正**）で退行した6局が
**同じ1つの原因に収束**した：

> `PRIORITY["冷却役投資+2"]`(81.0) は「その日に効く手」より上にある。
> 押しのけられた手＝**当日の冷却**／**敗北板からの引き剥がし移動**／**TTハーツのガード**。

★**B-139b/B-142 の教訓＝上限値を射程と読まない**。最初から層に分けて数える。

## 層の定義（すべて主人公の可視情報＋棋譜の実測。推定を混ぜない）

- **L0**＝`_base_score` が `PRIORITY["冷却役投資+2"]`(81.0) / `["冷却役投資+1"]`(78.5) を
  返した候補が、その席の**最上位**になった席（＝投資が席を取った席）。
  ★分岐の同定は**点数の一致では行わない**（`前日冷却投資_押切`(83)−2*ref が 81.0 に
  なりうる等の衝突がある）。`agents.heuristic_protagonist.PRIORITY` を**読みを記録する
  dict 副本**に差し替え、`_base_score` がどのキーを読んだかで同定する
  （値は完全同一＝挙動 bit 不変。`verify` サブコマンドで毎回確認）。
- **L1**＝L0 のうち「その日に効く手」が**同じ席の候補に存在し**、投資に**僅差で負けた**席。
  - 「その日に効く手」の3族（B-141b §3-2 が名指しした手だけ。拡張しない）：
    - `A 当日冷却`＝`不安-1` → **今日**の事件の犯人候補（`_culprit_cands[today]`）。
    - `B 引き剥がし`＝`移動*` → **敗北板**（`_guess_defeat_board`）に立つキャラを板の外へ。
    - `C TTガード`＝`友好+` → `_tt_guards` の友好3未満のキャラ（`rules/50:128` の任意敗北封じ）。
  - ★**同じターンに別席がその手を実際に打っていたら L1 に数えない**
    （席が3つある＝押しのけても他席が拾えば損は出ない）。
  - **僅差の閾値** `--gap`（既定 **12.0**）＝投資の点と、その手の**最良候補**の点の差。
    根拠＝この帯に実在する対抗手の点（`TT投資+1`=79／`TT投資+2`−5*rank=77/72／
    `カルティスト剥がし_候補`=72／`クロマク剥がし_候補`=71）が 81.0 から見て
    2.0〜10.0 の位置にある＝**10 を含み、当日冷却の下限帯（45+）を含まない**幅。
    掃引用に `--gap` で振れる（層別の内訳も常に印字する）。
- **L2＝真の射程**＝L1 のうち、**投資が「守るべき今日の脅威」に間に合わなかった**席
  （＝投資しても無駄で、当日の手を打つべきだった席）。**判定は棋譜で行う**：
  - `A`＝**その日に、その投資先が、押しのけられた冷却対象へ不安除去能力を実際に撃てた**
    なら**代替が成立**＝L2 でない。撃てていなければ L2。
    ★**現行 `_unlock` は1日ぶん悲観的**（B-141b §1-2＝`rules/00_rules_core.md:101-113` の
    フェイズ順。切替口 `B141B_UNLOCK_SAME_DAY` は**既定 False のまま**＝本レーンは触らない）
    が、本判定は算術ではなく**実際に撃てたか**を見るので、どちらの算術でも同じ土俵で数えられる。
  - `B`/`C`＝冷却能力は**板の暗躍も友好も作れない**＝種類が違い**常に代替不能**＝L2。
- **L2b（参考・より厳しい層）**＝L2 かつ、その投資先が**そのループ中に一度も**
  不安除去能力を撃てなかった席（＝投資が最後まで実らなかった）。
- **L3＝確実な損**＝L2 のうち、**実際にその事件/敗北が起きてループを落とした**席。
  - A＝その日の事件が**発生**し、そのループを**落とした**。
  - B/C＝そのループを**落とした**（板/宣言の別は `loop_result` の文言で併記）。

CLI（前面実行・測定は必ず PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8）:
    python -m arena.b143_audit --days 3
    python -m arena.b143_audit --days 5 --gap 12 --json out.json
    python -m arena.b143_audit verify --days 3      # 差し替えの挙動不変チェック
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.heuristic_protagonist as _hp_mod
from agents import HeuristicMastermind, HeuristicProtagonist
from agents.heuristic_protagonist import _move_dest
from engine.data import goodwill_abilities_of
from sim import run_game

_builtin_max = max

#: 「僅差」の既定閾値（投資の点 − 対抗手の最良点）。docstring の根拠を参照。
GAP_DEFAULT: float = 12.0

#: 投資分岐を同定する PRIORITY キー（この2つは `_base_score` の当該分岐でしか読まれない
#  ＝`grep -n '冷却役投資' agents/heuristic_protagonist.py` で確認済み）。
INVEST_KEYS = ("冷却役投資+2", "冷却役投資+1")

#: TT ガードの友好しきい値（`rules/50:128`＝友好3以上なら任意敗北を宣言できない）。
TT_GUARD_GW: int = 3


class _TapPriority(dict):
    """`PRIORITY` の**読みを記録する**副本（値は完全同一＝挙動 bit 不変）。"""

    def __init__(self, src: dict):
        super().__init__(src)
        self.reads: list[str] = []

    def __getitem__(self, k):
        self.reads.append(k)
        return super().__getitem__(k)


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝`super().decide()` の戻り値を素通し。読むだけ）
# ---------------------------------------------------------------------------
class _Tally(HeuristicProtagonist):
    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rows: list[dict] = []          # L0 席（投資が席を取った席）
        self.plays: list[dict] = []         # 全 set_card 席の台帳（他席の応手判定用）

    # -- 「その日に効く手」の族判定（すべて主人公の可視情報だけ） -------------
    def _b143_family(self, view: dict, o: dict, danger_board: str | None) -> str | None:
        card, tgt, kind = o["card"], o["target"], o.get("target_kind")
        day = view.get("day", 1)
        # A 当日冷却（幻想の板置き＝B-133 の読み替えも同じ土俵で数える）
        if card == "不安-1":
            eff = self._b133_cool_target(view, o) if kind == "board" else tgt
            cands = (getattr(self, "_culprit_cands", {}) or {}).get(day, ())
            if eff is not None and eff in cands:
                return "A"
            return None
        # B 敗北板からの引き剥がし移動
        if card.startswith("移動") and card != "移動禁止" and kind == "character":
            if not danger_board:
                return None
            c = self._alive(view, tgt)
            if not c or c.get("area") != danger_board:
                return None
            dest = _move_dest(c.get("area"), card)
            if dest is not None and dest != danger_board:
                return "B"
            return None
        # C TTハーツのガード（幻想はボード置きで届く＝`_base_score` の board 分岐と同型）
        if card in ("友好+1", "友好+2"):
            guards = getattr(self, "_tt_guards", None) or ()
            if kind == "character" and tgt in guards:
                c = self._alive(view, tgt)
                if c and c.get("goodwill", 0) < TT_GUARD_GW:
                    return "C"
            if kind == "board" and "幻想" in guards:
                gc = self._alive(view, "幻想")
                if gc and gc.get("area") == tgt \
                        and gc.get("goodwill", 0) < TT_GUARD_GW:
                    return "C"
        return None

    def _b143_need(self, view: dict, tgt: str) -> int | None:
        """投資先の「冷却能力の解禁に足りないハート数」（AI の分岐と同じ数え方）。"""
        c = self._alive(view, tgt)
        if not c:
            return None
        for ab in goodwill_abilities_of(tgt) or []:
            if "不安" not in ab["name"] or "除去" not in ab["name"]:
                continue
            if c["goodwill"] >= ab["hearts"]:
                continue
            return int(ab["hearts"]) - int(c["goodwill"])
        return None

    # -- decide ------------------------------------------------------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        cap: dict = {}
        tap = _TapPriority(_hp_mod.PRIORITY)

        def spymax(*args, **kw):
            if (args and args[0] is options and "key" in kw
                    and "scored" not in cap):
                key = kw["key"]
                rows = []
                for o in args[0]:
                    del tap.reads[:]
                    s = key(o)
                    rows.append((s, o, tuple(tap.reads)))
                cap["scored"] = sorted(rows, key=lambda x: -x[0])
            return _builtin_max(*args, **kw)

        had = "max" in _hp_mod.__dict__
        prev = _hp_mod.__dict__.get("max")
        _hp_mod.max = spymax
        _hp_mod.PRIORITY = tap
        try:
            chosen = super().decide(view, decision, options)
        finally:
            if had:
                _hp_mod.max = prev
            else:
                del _hp_mod.max
            _hp_mod.PRIORITY = dict(tap)   # 元の素の dict に戻す（値は同一）
        if decision == "set_card":
            self._record(view, options, chosen, cap.get("scored", []))
        return chosen

    def _record(self, view, options, chosen, scored) -> None:
        loop, day, seat = view.get("loop"), view.get("day"), view.get("seat")
        danger_board = self._guess_defeat_board(view)
        self.plays.append({
            "loop": loop, "day": day, "seat": seat,
            "card": chosen.get("card"), "target": chosen.get("target"),
            "kind": chosen.get("target_kind"),
            "family": self._b143_family(view, chosen, danger_board),
        })
        if not scored:
            return
        top_s, top_o, top_reads = scored[0]
        # 席の実選択が argmax と違う（＝計画・B-100 経由）なら数えない（別勘定）
        if (chosen.get("card"), chosen.get("target"), chosen.get("target_kind")) != \
                (top_o.get("card"), top_o.get("target"), top_o.get("target_kind")):
            return
        hit = next((k for k in INVEST_KEYS if k in top_reads), None)
        if hit is None:
            return
        # ---- L0 席 -------------------------------------------------------
        need = self._b143_need(view, top_o["target"])
        unlock = (self._cooler_unlock_day(day, need) if need else None)
        hot = sorted(d for d, g in (getattr(self, "_incident_danger", {}) or {}).items()
                     if g >= 60.0 and d >= day)
        rivals = []
        for s, o, _rd in scored[1:]:
            fam = self._b143_family(view, o, danger_board)
            if fam is None:
                continue
            rivals.append({"family": fam, "score": round(float(s), 3),
                           "card": o["card"], "target": o["target"],
                           "kind": o.get("target_kind"),
                           "gap": round(float(top_s) - float(s), 3)})
        best: dict = {}
        for r in rivals:
            if r["family"] not in best or r["gap"] < best[r["family"]]["gap"]:
                best[r["family"]] = r
        self.rows.append({
            "loop": loop, "day": day, "seat": seat,
            "key": hit, "score": round(float(top_s), 3),
            "invest_target": top_o["target"], "invest_card": top_o["card"],
            "need": need, "unlock": unlock,
            "hot_days": hot, "first_hot": (hot[0] if hot else None),
            "danger_board": danger_board,
            "days_per_loop": view.get("days_per_loop"),
            "best_rival": best,
        })


# ---------------------------------------------------------------------------
# 棋譜からの裏取り
# ---------------------------------------------------------------------------
def _incident_facts(state) -> dict:
    """(loop, day) -> {occurs, name}（公開イベント）。"""
    out = {}
    for e in state.history:
        if e.get("event") == "incident":
            out[(e.get("loop"), e.get("day"))] = {
                "occurs": bool(e.get("occurs")), "name": e.get("name")}
    return out


def _lost_loops(state) -> dict:
    """敗北したループ -> `loop_result` の文言。"""
    out = {}
    for e in state.history:
        if e.get("event") == "loop_result" and "敗北" in str(e.get("result", "")):
            out[e.get("loop")] = str(e.get("result", ""))
    return out


def _cool_ability_used(state) -> tuple[set, set]:
    """不安除去の友好能力が**実際に通った**（拒否されなかった）記録。

    公開イベント `goodwill_used`（`sim/flow.py:128`）＋直後の `goodwill_resolved`
    （`:133`。拒否されると `goodwill_refused`＝`:95/:102` が出て resolved は出ない）。

    返り値：
      - `by_loop` ＝ {(loop, character)} ＝そのループ中に一度でも撃てた（＝投資が実った）
      - `by_day`  ＝ {(loop, day, character, target)} ＝その日に**誰に**撃てたか
    """
    by_loop, by_day = set(), set()
    pending = None
    for e in state.history:
        ev = e.get("event")
        if ev == "goodwill_used":
            ab = str(e.get("ability", ""))
            pending = e if ("不安" in ab and "除去" in ab) else None
        elif ev == "goodwill_resolved" and pending is not None:
            if (e.get("character") == pending.get("character")
                    and e.get("ability") == pending.get("ability")):
                by_loop.add((pending.get("loop"), pending.get("character")))
                by_day.add((pending.get("loop"), pending.get("day"),
                            pending.get("character"), pending.get("target")))
            pending = None
        elif ev in ("goodwill_refused", "goodwill_used"):
            pending = None
    return by_loop, by_day


def _outcome(state) -> str:
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return "defense"
    if fb:
        return "fb_win" if state.winner == "protagonist" else "fb_loss"
    return "loss"


def audit_game(script, seed: int, loops: int = 8) -> dict:
    """1局を回し、L0 席に**層判定の材料**まで積んで返す（gap には依存しない）。"""
    probe = replace(script, loops=loops)
    hp = _Tally(seed)
    state, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                "p1": hp, "p2": hp, "p3": hp})
    inc = _incident_facts(state)
    lost = _lost_loops(state)
    used, used_day = _cool_ability_used(state)
    outcome = _outcome(state)
    # 同一ターンに実際に打たれた「その日に効く手」の族
    played: dict[tuple, set] = {}
    for p in hp.plays:
        if p["family"]:
            played.setdefault((p["loop"], p["day"]), set()).add(p["family"])
    for r in hp.rows:
        lp, dy = r["loop"], r["day"]
        r["played_fams"] = sorted(played.get((lp, dy), set()))
        r["invest_paid_off"] = (lp, r["invest_target"]) in used
        # ★族A の代替可否は**棋譜で**判定する（算術の推定に頼らない）＝
        #   「投資先がその日に、押しのけられた冷却対象へ不安除去を実際に撃てたか」。
        rvA = (r.get("best_rival") or {}).get("A")
        r["substituted_today"] = bool(
            rvA and (lp, dy, r["invest_target"], rvA["target"]) in used_day)
        r["loop_lost"] = lp in lost
        r["loop_result"] = lost.get(lp)
        r["incident_today"] = inc.get((lp, dy))
        r["game_outcome"] = outcome
    return {"outcome": outcome, "rows": hp.rows, "n_plays": len(hp.plays)}


def classify(rows: list[dict], gap: float) -> None:
    """L0 席に L1/L2/L3 を付ける（`gap` だけを変えて何度でも呼べる＝掃引用）。"""
    for r in rows:
        dy = r["day"]
        dpl = r.get("days_per_loop") or dy
        done = set(r.get("played_fams") or ())
        l1: dict = {}
        for fam, rv in (r["best_rival"] or {}).items():
            if rv["gap"] > gap:
                continue
            if fam in done:          # 他席が実際に打っている＝損は出ていない
                continue
            l1[fam] = rv
        r["L1_fams"] = sorted(l1)
        r["L1"] = bool(l1)
        # ---- L2＝真の射程 -------------------------------------------------
        # ★チケットの定義に忠実に＝「投資の解禁が**守るべき事件/敗北に間に合わなかった**席」。
        #   守るべき事件/敗北＝押しのけられた手が守っていた**今日の脅威**。
        #   A＝**棋譜で**判定（その日にその投資先が、押しのけられた冷却対象へ
        #      不安除去を実際に撃てたなら代替が成立した＝L2 でない）。
        #   B・C＝冷却能力は板の暗躍も友好も作れない＝**種類が違い常に代替不能**。
        # ★`L2b`＝さらに厳しい参考層（L2 かつ**そのループ中に一度も投資が実らなかった**）。
        l2 = []
        for fam in r["L1_fams"]:
            if fam == "A" and r["substituted_today"]:
                continue
            l2.append(fam)
        r["L2_fams"], r["L2"] = l2, bool(l2)
        r["L2b"] = bool(l2) and not r["invest_paid_off"]
        _ = dpl
        # L3＝実際にその事件/敗北が起きてループを落とした
        l3 = []
        for fam in l2:
            if not r["loop_lost"]:
                continue
            if fam == "A":
                f = r.get("incident_today")
                if not (f and f["occurs"]):
                    continue
            l3.append(fam)
        r["L3_fams"], r["L3"] = l3, bool(l3)


def run(days: int = 3, loops: int = 8, gaps: tuple = (GAP_DEFAULT,),
        verbose: bool = False, start: int = 0, end: int | None = None) -> dict:
    """1回だけ対局を回し、`gaps` の各値で層を数える（掃引の測定汚染を構造的に消す）。"""
    from arena.benchmark import benchmark_scripts

    allrows: list[dict] = []
    base = Counter()
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = audit_game(sc, seed, loops=loops)
        base["games"] += 1
        for r in res["rows"]:
            base["L0"] += 1
            base[f"L0_{r['key']}"] += 1
            for fam in (r["best_rival"] or {}):
                base[f"rival_present_{fam}"] += 1
            allrows.append({"script": name, "seed": seed, **r})
        if verbose:
            print(f"  {name} s{seed}: L0={len(res['rows'])} [{res['outcome']}]",
                  flush=True)
    out = {"days": days, "base": dict(base), "by_gap": {}, "hits": {}}
    for g in gaps:
        classify(allrows, g)
        c = Counter()
        hits = []
        for r in allrows:
            if r["L1"]:
                c["L1"] += 1
                for f in r["L1_fams"]:
                    c[f"L1_{f}"] += 1
                if r.get("invest_paid_off"):
                    c["L1_paid_off"] += 1
                hits.append(dict(r))
            if r["L2"]:
                c["L2"] += 1
                for f in r["L2_fams"]:
                    c[f"L2_{f}"] += 1
            if r["L2b"]:
                c["L2b"] += 1
            if r["L3"]:
                c["L3"] += 1
                for f in r["L3_fams"]:
                    c[f"L3_{f}"] += 1
        out["by_gap"][str(g)] = dict(c)
        out["hits"][str(g)] = hits
    return out


def verify(days: int = 3, loops: int = 8, n: int = 12) -> int:
    """差し替え（PRIORITY 副本＋spymax）の挙動不変を確認する。"""
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


_FAMNAME = {"A": "当日冷却", "B": "敗北板からの引き剥がし", "C": "TTハーツのガード"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count", choices=["count", "verify"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--gaps", default="2,4,8,12,20,40,999",
                    help="僅差の閾値の掃引（1回の対局から全部数える）")
    ap.add_argument("--gap", type=float, default=GAP_DEFAULT,
                    help="一覧を出す閾値（--gaps に含まれない値でも可）")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--unlock-same-day", action="store_true",
                    help="★測定条件としてのみ B-141b の切替口 (b) を ON にする"
                         "（既定値は変更しない＝land 対象外・報告に必ず明記）")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.unlock_same_day:
        HeuristicProtagonist.B141B_UNLOCK_SAME_DAY = True
    if a.cmd == "verify":
        return verify(days=a.days, loops=a.loops)
    gaps = [float(x) for x in a.gaps.split(",") if x.strip() != ""]
    if a.gap not in gaps:
        gaps.append(a.gap)
    gaps.sort()
    print(f"[切替口] gaps={gaps} / 一覧gap={a.gap} / TT_GUARD_GW={TT_GUARD_GW}"
          f" / B141B_UNLOCK_SAME_DAY={HeuristicProtagonist.B141B_UNLOCK_SAME_DAY}"
          f" / B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
          f" / days={a.days} loops={a.loops}", flush=True)
    res = run(days=a.days, loops=a.loops, gaps=tuple(gaps), verbose=a.verbose,
              start=a.start, end=a.end)
    b = res["base"]
    print(f"== B-143：冷却役投資が『その日に効く手』を押しのける — 射程"
          f"（{a.days}日級 {b.get('games', 0)}局）==")
    print(f"★L0 投資が席を取った席 = {b.get('L0', 0)}"
          f"（+2:{b.get('L0_冷却役投資+2', 0)} / +1:{b.get('L0_冷却役投資+1', 0)}）")
    for f in "ABC":
        print(f"   ├ 同席に『{_FAMNAME[f]}』候補が在った席 = "
              f"{b.get(f'rival_present_{f}', 0)}")
    print("")
    print("  僅差の閾値ごとの層（★上限値を射程と読まない＝B-139b/B-142 の教訓）")
    print("   gap |  L1 (A/B/C) | 投資が実った | L2 (A/B/C) | L2b | L3 (A/B/C)")
    for g in gaps:
        c = res["by_gap"][str(g)]
        print(f"   {g:>5} | {c.get('L1', 0):>3} "
              f"({c.get('L1_A', 0)}/{c.get('L1_B', 0)}/{c.get('L1_C', 0)})"
              f" | {c.get('L1_paid_off', 0):>10} | {c.get('L2', 0):>2} "
              f"({c.get('L2_A', 0)}/{c.get('L2_B', 0)}/{c.get('L2_C', 0)})"
              f" | {c.get('L2b', 0):>3}"
              f" | {c.get('L3', 0):>3} "
              f"({c.get('L3_A', 0)}/{c.get('L3_B', 0)}/{c.get('L3_C', 0)})",
              flush=True)
    hits = res["hits"][str(a.gap)]
    if a.top and hits:
        print("")
        print(f"  L1 席の一覧（gap={a.gap}）"
              f"（局 / L D 席 / 投資先(need,解禁) / 押しのけた手 / 差 / 層）")
        for h in hits[:a.top]:
            for f in h["L1_fams"]:
                rv = h["best_rival"][f]
                lv = "L3" if f in h["L3_fams"] else (
                    "L2" if f in h["L2_fams"] else "L1")
                print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                      f"/{h['seat']} → {h['invest_target']}"
                      f"(need={h['need']},解禁D{h['unlock']}) | "
                      f"{_FAMNAME[f]}={rv['card']}→{rv['target']}"
                      f"({rv['score']}) | 差={rv['gap']} | {lv} | "
                      f"{'敗' if h['loop_lost'] else '防衛'}"
                      f" | [{h.get('game_outcome')}]", flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
