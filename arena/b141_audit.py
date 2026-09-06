# -*- coding: utf-8 -*-
"""B-141：`_ability_value`「不安除去」が**過去の事件**を根拠に 45.0 を返す射程の数え上げ。

起票＝`docs/バックログ_構想メモ_FableA.md` §14（発見元＝`docs/監査_B139b_友好+2の配り先_2026-08-02.md` §5(1)）。

## 何が問題か（現物）

`agents/heuristic_protagonist.py` の `_ability_value` の「不安除去」分岐は
`self._incident_danger`（**ループ全日**の予定事件から作られる）を
**`d >= view["day"]` で絞らずに**回している。他の参照箇所
（`_b133_gensou_cool_live` の `d >= day`／冷却役投資の `_hot`）は**両方とも絞っている**
＝**この1箇所だけが非対称**。カウンターはループ開始時に全除去（`rules/00:86`）＝
「もう発生しない事件のための冷却役」は無価値。

## 数え上げの2層（★別物なので必ず分けて出す）

1. **「45.0 が返った回数」**（`av_*`）＝ `_ability_value` の呼び出し単位。
   - `av_allpast_ge45` ＝ **バックログ §14 の定義そのまま**
     （`_incident_danger` の事件日が**すべて** `view["day"]` より前 かつ 返り値 >= 45）。
   - `av_changed` ＝ **日付で絞ると返り値が変わる**呼び出し（過去日と未来日が混在し、
     dict の走査順で過去日が先に当たった席も拾う＝こちらが**上限**）。
2. **「意思決定が変わった席数」**（`seat_*`）＝ `set_card` / `goodwill_ability` の席単位。
   - `seat_top1_change` ＝ `_compute_invest` の**投資先1位**が入れ替わった席
     （★指示された「真の射程」）。
   - `seat_choice_change` ＝ **実際に選ぶ手が変わった席**（切替口 ON で `decide` を
     もう一度回して比較＝最強の物証）。

## 妥当性の自己検査（★これが無いとシャドーは信用できない）

各席で `decide` を **3回**回す：control(OFF) → treat(ON) → real(OFF・最終)。
毎回 `__dict__` をスナップショットから復元する。
**control は real と 1手も違ってはならない**（`ctrl_mismatch` が 0 でなければ
ハーネスが壊れている＝結果は破棄）。`_belief` はスナップショット対象外＝
もし `decide` が belief を変えていれば `ctrl_mismatch` に必ず出る。

CLI（前面実行）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b141_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b141_audit --days 5
    ... --json out.json --top 25
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game

#: AI 自身が「投資に値する」とみなす閾値（`heuristic_protagonist.py:5552`/`:5574`）。
V_MEANINGFUL = 45.0

#: スナップショット／復元の対象外（`_belief` は decide で変わらない想定＝
#: 変わっていれば ctrl_mismatch に出る。残りは本監査器の私物）。
_NOCOPY = frozenset({
    "_belief", "B141_COOLER_VALUE_FUTURE_ONLY", "B141B_UNLOCK_SAME_DAY",
    "B141_COOLER_ALLPAST_ONLY",
    "rows", "av_rows", "tally", "mode", "_b141_count",
    "_b141_invest_cur", "_b141_invest_fix", "_b141_view_of_turn",
})

#: 監査モード＝treat 側で入れる切替口の組み合わせ。
#:   "a"  ＝`_ability_value` の日付フィルタのみ（バックログ §14 前半）
#:   "b"  ＝冷却役投資の解禁日 off-by-one のみ（同 §14 後半・「併せて見る疑い」）
#:   "ab" ＝両方（land 候補）
MODES = ("a", "a0", "b", "ab", "a0b")


def _has_a(mode: str) -> bool:
    return mode.startswith("a")


def _has_b(mode: str) -> bool:
    return mode.endswith("b") and mode != "b" or mode == "b"


def _has_a0(mode: str) -> bool:
    return mode.startswith("a0")


def _is_cool(ability: str) -> bool:
    return "不安" in ability and "除去" in ability


def _top1(inv: dict) -> str | None:
    """`_base_score` の混合戦略と**同じ並べ方**の1位（`sorted(..., key=inv.get, reverse=True)`）。"""
    if not inv:
        return None
    return sorted(inv, key=inv.get, reverse=True)[0]


def _top2(inv: dict) -> tuple:
    return tuple(sorted(inv, key=inv.get, reverse=True)[:2])


class _CountOnly(HeuristicProtagonist):
    """★層①専用（`--count-only`）＝`_ability_value` を包むだけ。`decide` も
    `_recompute` も**一切いじらない**＝AI の実行経路は本体と完全同一。

    3回まわしのシャドー（`_B141Tally`）は枝刈りの都合で `decide` を包むため、
    数えられる呼び出しの母集合が mode ごとに僅かにぶれる（実測 ±0.5%）。
    **層①の正典値はこちらで取る**。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.B141_COOLER_VALUE_FUTURE_ONLY = False
        self.tally: Counter = Counter()
        self.av_rows: list[dict] = []
        self.rows: list[dict] = []
        self._b141_count = True

    _ability_value = None      # 下で共通実装を差し込む（定義は _B141Tally と同一）


class _B141Tally(HeuristicProtagonist):
    """挙動は本体と完全同一（real の決定だけを返す）。数えるだけ。"""

    def __init__(self, seed: int = 0, mode: str = "a"):
        super().__init__(seed)
        assert mode in MODES, mode
        self.mode = mode
        self.B141_COOLER_VALUE_FUTURE_ONLY = False   # インスタンス属性で明示管理
        self.B141_COOLER_ALLPAST_ONLY = False
        self.B141B_UNLOCK_SAME_DAY = False
        self.tally: Counter = Counter()
        self.av_rows: list[dict] = []
        self.rows: list[dict] = []
        self._b141_count = True         # `_ability_value` を数えてよい局面か
        self._b141_invest_cur: tuple | None = None
        self._b141_invest_fix: tuple | None = None
        self._b141_view_of_turn: dict | None = None

    # -- スナップショット ---------------------------------------------------
    def _snap(self) -> dict:
        out = {}
        for k, v in self.__dict__.items():
            if k in _NOCOPY:
                continue
            try:
                out[k] = copy.deepcopy(v)
            except Exception:            # 復元できないものは参照のまま（実績なし）
                out[k] = v
        return out

    def _restore(self, snap: dict) -> None:
        for k in [k for k in self.__dict__ if k not in _NOCOPY and k not in snap]:
            del self.__dict__[k]
        for k, v in snap.items():
            try:
                self.__dict__[k] = copy.deepcopy(v)
            except Exception:
                self.__dict__[k] = v

    # -- `_recompute`：ターン1回。ここで両版の `_invest` を作る -------------
    def _recompute(self, view: dict) -> None:
        super()._recompute(view)         # ★切替口 OFF＝本物（以後こちらを使う）
        cur = (dict(self._invest), dict(self._invest_need),
               dict(self._invest_has_tgt))
        prev_count = self._b141_count
        self._b141_count = False         # 影の再計算は数えない
        # 切替口(b)は `_compute_invest` を通らない＝mode に "a" が無いなら再計算不要
        self.B141_COOLER_VALUE_FUTURE_ONLY = _has_a(self.mode)
        self.B141_COOLER_ALLPAST_ONLY = _has_a0(self.mode)
        try:
            fix_inv = self._compute_invest(view)
            fix = (dict(fix_inv), dict(self._invest_need),
                   dict(self._invest_has_tgt))
        finally:
            self.B141_COOLER_VALUE_FUTURE_ONLY = False
            self.B141_COOLER_ALLPAST_ONLY = False
            self._b141_count = prev_count
        # 本物の値へ戻す（`_compute_invest` は `_invest_need`/`_has_tgt` を書き換える）
        self._invest, self._invest_need, self._invest_has_tgt = (
            cur[0], cur[1], cur[2])
        self._b141_invest_cur, self._b141_invest_fix = cur, fix
        self._b141_view_of_turn = {"loop": view.get("loop"), "day": view.get("day")}

    # -- `_ability_value`：呼び出し単位の数え上げ ---------------------------
    def _ability_value(self, user, ability, target, view) -> float:
        v = HeuristicProtagonist._ability_value(self, user, ability, target, view)
        if not (self._b141_count and _is_cool(ability)
                and not self.B141_COOLER_VALUE_FUTURE_ONLY):
            return v
        self._b141_count = False
        self.B141_COOLER_VALUE_FUTURE_ONLY = True
        try:
            v_fix = HeuristicProtagonist._ability_value(
                self, user, ability, target, view)
        finally:
            self.B141_COOLER_VALUE_FUTURE_ONLY = False
            self._b141_count = True
        days = list(getattr(self, "_incident_danger", {}) or {})
        day = view.get("day", 0)
        all_past = bool(days) and all(d < day for d in days)
        t = self.tally
        t["av_total"] += 1
        if v >= V_MEANINGFUL:
            t["av_cur_ge45"] += 1
            if all_past:
                t["av_allpast_ge45"] += 1                      # ★§14 の定義
                t["av_allpast_ge45_" + ("use" if target else "invest")] += 1
        if abs(v - v_fix) > 1e-9:
            t["av_changed"] += 1
            if v >= V_MEANINGFUL:
                t["av_changed_from_ge45"] += 1
            if all_past:
                t["av_changed_allpast"] += 1
            else:
                t["av_changed_mixed"] += 1                      # 過去/未来 混在
            self.av_rows.append({
                "loop": view.get("loop"), "day": day, "seat": view.get("seat"),
                "user": user, "ability": ability, "target": target,
                "cur": round(v, 2), "fix": round(v_fix, 2),
                "all_past": all_past, "danger_days": sorted(days),
            })
        return v

    # -- decide：control / treat / real の3回まわし ------------------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision not in ("set_card", "goodwill_ability"):
            return super().decide(view, decision, options)
        # ★`_recompute`（ターン1回・belief.observe を含む）は3回まわしの**外**で
        #   1回だけ走らせる。`_sync` はターン内では冪等な no-op になる。
        self._sync(view)
        t0 = self.tally
        key0 = "seat" if decision == "set_card" else "gw"
        # ★厳密な枝刈り（モード別・どちらも「切替口が1つも効かない」の**十分条件**）：
        #   (a) `_incident_danger` に `view["day"]` より前の日が1つも無い
        #       ＝`continue` が一度も走らない＝`_ability_value` は bit 一致。
        #   (b) `_hot`（危険>=60 かつ 今日以降）が空
        #       ＝`if not _hot or ...: continue` が `_unlock` の値によらず必ず成立。
        #   （省いた席も分母 `*_total` には数える。）
        _dg = getattr(self, "_incident_danger", {}) or {}
        _day = view.get("day", 0)
        _live_a = (_has_a(self.mode) and any(d < _day for d in _dg)
                   and (not _has_a0(self.mode)
                        or not any(d >= _day for d in _dg)))
        _live_b = (_has_b(self.mode)
                   and any(g >= 60.0 and d >= _day for d, g in _dg.items()))
        if not (_live_a or _live_b):
            t0[f"{key0}_total"] += 1
            t0[f"{key0}_pruned_no_stale_day"] += 1
            return super().decide(view, decision, options)
        snap = self._snap()          # ★`_turn` は既に現ターン＝以後の `_sync` は no-op
        prev_count = self._b141_count

        self._b141_count = False
        ctrl = super().decide(view, decision, options)
        self._restore(snap)

        # treat＝切替口 ON ＋ `_invest` 系も修正版へ差し替え（`_recompute` は
        # 同一ターンでは再走しないので、ここで明示的に入れ替える必要がある）
        self.B141_COOLER_VALUE_FUTURE_ONLY = _has_a(self.mode)
        self.B141_COOLER_ALLPAST_ONLY = _has_a0(self.mode)
        self.B141B_UNLOCK_SAME_DAY = _has_b(self.mode)
        fix = self._b141_invest_fix
        if fix is not None:
            self._invest, self._invest_need, self._invest_has_tgt = (
                dict(fix[0]), dict(fix[1]), dict(fix[2]))
        try:
            treat = super().decide(view, decision, options)
        finally:
            self.B141_COOLER_VALUE_FUTURE_ONLY = False
            self.B141_COOLER_ALLPAST_ONLY = False
            self.B141B_UNLOCK_SAME_DAY = False
        self._restore(snap)

        self._b141_count = prev_count
        real = super().decide(view, decision, options)   # ★これだけが本物

        t = self.tally
        key = "seat" if decision == "set_card" else "gw"
        t[f"{key}_total"] += 1
        if ctrl != real:
            t[f"{key}_ctrl_mismatch"] += 1
        if decision == "set_card":
            cur_i = (self._b141_invest_cur or ({}, {}, {}))
            fix_i = (self._b141_invest_fix or ({}, {}, {}))
            if cur_i[0] != fix_i[0]:
                t["seat_invest_diff"] += 1
            if _top1(cur_i[0]) != _top1(fix_i[0]):
                t["seat_top1_change"] += 1                      # ★真の射程
            if _top2(cur_i[0]) != _top2(fix_i[0]):
                t["seat_top2_change"] += 1
            if cur_i[1] != fix_i[1]:
                t["seat_need_change"] += 1
        if treat != real:
            t[f"{key}_choice_change"] += 1                      # ★最強の物証
            self.rows.append({
                "decision": decision,
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"),
                "real": {k: real.get(k) for k in
                         ("card", "target", "target_kind", "character", "ability")
                         if k in real},
                "treat": {k: treat.get(k) for k in
                          ("card", "target", "target_kind", "character", "ability")
                          if k in treat},
                "top1_cur": _top1((self._b141_invest_cur or ({},))[0]),
                "top1_fix": _top1((self._b141_invest_fix or ({},))[0]),
            })
        return real


#: 層① の数え上げ本体は1つだけ（二重実装を避ける＝`_CountOnly` へ差し込む）。
_CountOnly._ability_value = _B141Tally._ability_value


def audit_game(script, seed: int, loops: int = 8, mode: str = "a",
               count_only: bool = False) -> tuple:
    probe = replace(script, loops=loops)
    hp = _CountOnly(seed) if count_only else _B141Tally(seed, mode=mode)
    state, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome = "defense"
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        outcome = "loss"
    return outcome, hp.tally, hp.rows, hp.av_rows


def run(days: int = 3, loops: int = 8, verbose: bool = False,
        start: int = 0, end: int | None = None, mode: str = "a",
        count_only: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    total: Counter = Counter()
    outcomes: Counter = Counter()
    rows: list[dict] = []
    av_rows: list[dict] = []
    per_script: list[dict] = []
    scripts = list(benchmark_scripts(days=days))[start:end]
    for name, seed, sc in scripts:
        outcome, tal, r, av = audit_game(sc, seed, loops=loops, mode=mode,
                                         count_only=count_only)
        total.update(tal)
        outcomes[outcome] += 1
        for x in r:
            x.update({"script": name, "seed": seed, "outcome": outcome})
        for x in av:
            x.update({"script": name, "seed": seed})
        rows.extend(r)
        av_rows.extend(av)
        per_script.append({"script": name, "seed": seed, "outcome": outcome,
                           "av_allpast_ge45": tal.get("av_allpast_ge45", 0),
                           "av_changed": tal.get("av_changed", 0),
                           "seat_top1_change": tal.get("seat_top1_change", 0),
                           "seat_choice_change": tal.get("seat_choice_change", 0)
                           + tal.get("gw_choice_change", 0),
                           "ctrl_mismatch": tal.get("seat_ctrl_mismatch", 0)
                           + tal.get("gw_ctrl_mismatch", 0)})
        if verbose:
            print(f"  {name} s{seed} [{outcome}]: allpast>=45="
                  f"{tal.get('av_allpast_ge45', 0)} changed={tal.get('av_changed', 0)}"
                  f" top1={tal.get('seat_top1_change', 0)}"
                  f" choice={tal.get('seat_choice_change', 0)}"
                  f"+{tal.get('gw_choice_change', 0)}"
                  f" mismatch={tal.get('seat_ctrl_mismatch', 0)}"
                  f"+{tal.get('gw_ctrl_mismatch', 0)}", flush=True)
    return {"days": days, "mode": mode, "tally": dict(total), "outcomes": dict(outcomes),
            "rows": rows, "av_rows": av_rows, "per_script": per_script}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--mode", default="a", choices=list(MODES),
                    help="treat 側で入れる切替口: a=_ability_value 日付フィルタ / b=解禁日 off-by-one / ab=両方")
    ap.add_argument("--count-only", action="store_true",
                    help="層①だけを AI の実行経路そのままで数える（席単位の②は出ない）")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    print(f"== B-141 過去事件での冷却役過大評価 射程の数え上げ（{a.days}日級）==")
    print(f"mode={a.mode}  切替口のクラス既定（＝real/control 側の値）: "
          f"B141_COOLER_VALUE_FUTURE_ONLY="
          f"{HeuristicProtagonist.B141_COOLER_VALUE_FUTURE_ONLY} / "
          f"B141B_UNLOCK_SAME_DAY="
          f"{HeuristicProtagonist.B141B_UNLOCK_SAME_DAY}")
    print(f"  treat 側で ON にする切替口: "
          f"B141_COOLER_VALUE_FUTURE_ONLY={_has_a(a.mode)} / "
          f"B141_COOLER_ALLPAST_ONLY={_has_a0(a.mode)} / "
          f"B141B_UNLOCK_SAME_DAY={_has_b(a.mode)}")
    res = run(days=a.days, loops=a.loops, verbose=a.verbose,
              start=a.start, end=a.end, mode=a.mode,
              count_only=a.count_only)
    t = res["tally"]
    mism = t.get("seat_ctrl_mismatch", 0) + t.get("gw_ctrl_mismatch", 0)
    print("")
    print("対局結果:", dict(sorted(res["outcomes"].items())))
    print(f"★ハーネス妥当性 ctrl_mismatch = {mism}（0 でなければ結果は無効）")
    print("")
    print("== ① 呼び出し単位（「45.0 が返った回数」）==")
    print(f"  `_ability_value(不安除去)` 呼び出し総数 = {t.get('av_total', 0)}")
    print(f"  うち現行の返り値 >= {V_MEANINGFUL} = {t.get('av_cur_ge45', 0)}")
    print(f"  ★§14 の定義（事件日が**すべて過去** かつ >= 45）= "
          f"{t.get('av_allpast_ge45', 0)}"
          f"（投資評価 {t.get('av_allpast_ge45_invest', 0)} / "
          f"使用評価 {t.get('av_allpast_ge45_use', 0)}）")
    print(f"  ★日付で絞ると返り値が変わる呼び出し = {t.get('av_changed', 0)}"
          f"（全過去 {t.get('av_changed_allpast', 0)} / 混在 "
          f"{t.get('av_changed_mixed', 0)} / うち現行>=45 "
          f"{t.get('av_changed_from_ge45', 0)}）")
    print("")
    print("== ② 席単位（意思決定が変わった数）==")
    print(f"  set_card 席数 = {t.get('seat_total', 0)} / "
          f"goodwill_ability 席数 = {t.get('gw_total', 0)}")
    print(f"  （うち枝刈り＝過去日ゼロで ON/OFF が bit 一致と確定: "
          f"set_card {t.get('seat_pruned_no_stale_day', 0)} / "
          f"goodwill_ability {t.get('gw_pruned_no_stale_day', 0)}）")
    print(f"  `_invest` の中身が変わった席 = {t.get('seat_invest_diff', 0)}")
    print(f"  ★投資先1位が入れ替わった席 = {t.get('seat_top1_change', 0)}"
          f"（上位2位まで {t.get('seat_top2_change', 0)} / "
          f"`_invest_need` {t.get('seat_need_change', 0)}）")
    print(f"  ★★実際に選ぶ手が変わった席 = set_card {t.get('seat_choice_change', 0)}"
          f" / goodwill_ability {t.get('gw_choice_change', 0)}")
    rows = res["rows"]
    if rows and a.top:
        print("")
        print("  # | 局(seed) | L/D/席 | 種別 | 現行の手 → 切替口ONの手 | invest1位 cur→fix")
        for i, r in enumerate(rows[:a.top], 1):
            print(f"  {i:>2} | {r['script']}(s{r['seed']}) | "
                  f"L{r['loop']}D{r['day']}/{r['seat']} | {r['decision']} | "
                  f"{r['real']} → {r['treat']} | "
                  f"{r['top1_cur']}→{r['top1_fix']} [{r['outcome']}]")
    av = res["av_rows"]
    if av and a.top:
        print("")
        print(f"  （返り値が変わった呼び出しの上位{min(a.top, len(av))}）")
        for i, r in enumerate(av[:a.top], 1):
            print(f"  {i:>2} | {r['script']}(s{r['seed']}) | "
                  f"L{r['loop']}D{r['day']}/{r['seat']} | {r['user']}"
                  f"「{r['ability']}」tgt={r['target']} | "
                  f"{r['cur']} → {r['fix']} | 事件日={r['danger_days']}"
                  f" | 全過去={r['all_past']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in res.items()}, f,
                      ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
