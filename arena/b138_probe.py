# -*- coding: utf-8 -*-
"""B-138 Phase 1 の実測ハーネス（**agents/ の挙動には一切触れない＝読むだけ**）。

サブコマンド:
  reasons  … 標準ベンチの全局を走らせ、公開履歴の `loop_end.reason` を全数列挙し、
             秘匿ログ（`defeat` イベント＝真の敗北条件）と対応づける。
  count    … 全 `set_card` 席で
             (a) 帰属を「ループ終了時のボード敗北条件で終わったループ」に絞ると
                 `_observed_defeat_board` の票が減る／odb が変わる席
             (b) +4.0 の宛先を `_b84_top_boards` に合わせると宛先が変わる席
             を数え上げる（重なりも出す）。

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game


# ---------------------------------------------------------------------------
# 共通：標準ベンチと同じ脚本セット
# ---------------------------------------------------------------------------
def _scripts(days: int):
    from arena.benchmark import benchmark_scripts
    return benchmark_scripts(days=days)


# ---------------------------------------------------------------------------
# reasons：公開の `loop_end.reason` を全数列挙し、秘匿の敗北条件と対応づける
# ---------------------------------------------------------------------------
def cmd_reasons(args) -> int:
    print(f"[reasons] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}")
    for days in (3, 5):
        pub_reason = Counter()
        sec_reason = Counter()
        cross = Counter()          # (公開 loop_end.reason or "(なし)", 秘匿 defeat 条件の集合)
        board_when_no_le = Counter()
        n_defeat_loops = 0
        for name, seed, sc in _scripts(days):
            probe = replace(sc, loops=8)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
            # ループごとに集約
            le_by_loop: dict = {}
            for e in state.history:
                if e.get("event") == "loop_end":
                    le_by_loop.setdefault(e.get("loop"), []).append(e.get("reason"))
                    pub_reason[str(e.get("reason"))] += 1
            sec_by_loop: dict = {}
            for e in state.secret_log:
                if e.get("event") == "defeat":
                    sec_by_loop.setdefault(e.get("loop"), []).append(e.get("reason"))
                    sec_reason[str(e.get("reason"))] += 1
                if e.get("event") == "loop_end":
                    pass
            # 敗北したループ＝loop_result(敗北) or 最終ループで defeat
            defeat_lps = {e.get("loop") for e in state.history
                          if e.get("event") == "loop_result"
                          and "敗北" in str(e.get("result", ""))}
            # 最終ループ（loop_result が出ない）も敗北していれば数える
            if state.winner != "protagonist" or any(
                    e.get("event") == "final_battle" for e in state.history):
                defeat_lps.add(state.loop_no)
            for lp in sorted(defeat_lps):
                n_defeat_loops += 1
                pubs = tuple(sorted(set(le_by_loop.get(lp, []))))
                secs = tuple(sorted({str(r).split("（")[0] for r in sec_by_loop.get(lp, [])}))
                cross[(pubs or ("(loop_end なし)",), secs or ("(defeat なし)",))] += 1
                if not pubs:
                    for e in state.history:
                        if e.get("event") == "loop_board" and e.get("loop") == lp:
                            board_when_no_le[tuple(sorted(
                                b for b, v in (e.get("board_anyaku") or {}).items()
                                if v >= 2))] += 1
        print(f"\n=== {days}日級（{len(_scripts(days))}局・loops=8）敗北ループ {n_defeat_loops} ===")
        print("-- 公開履歴 loop_end.reason の全種類（出現回数） --")
        for k, v in pub_reason.most_common():
            print(f"   {v:6d}  {k}")
        print("-- 秘匿ログ defeat.reason の全種類（＝真の敗北条件・参考） --")
        for k, v in sec_reason.most_common():
            print(f"   {v:6d}  {k}")
        print("-- 敗北ループの対応づけ（公開 loop_end × 秘匿 defeat） --")
        for (p, s), v in cross.most_common():
            print(f"   {v:6d}  公開={list(p)}  秘匿={list(s)}")
        print("-- loop_end が無い敗北ループの『暗躍≥2 の板』の分布 --")
        for k, v in board_when_no_le.most_common():
            print(f"   {v:6d}  {list(k)}")
    return 0


# ---------------------------------------------------------------------------
# count：(a)(b) の影響範囲の数え上げ
# ---------------------------------------------------------------------------
def _b138_board_loss_loops(view: dict) -> set:
    """(a) の述語＝「ループ終了時のボード敗北条件で終わったループ」の候補。

    公開情報だけで書ける形＝`loop_end` イベント（＝ループ途中の終了効果：
    主人公の死亡／主人公の敗北（ループ終了効果））が**出ていない**ループ。
    ＝日数を使い切って `evaluate_loop_end` のボード条件で終わったループ。
    """
    ended_by_effect = {e.get("loop") for e in view.get("history", [])
                       if e.get("event") == "loop_end"}
    return ended_by_effect


class _Probe(HeuristicProtagonist):
    """挙動は本体そのまま。席ごとに (a)(b) の差分材料を記録するだけ。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rows: list[dict] = []

    def decide(self, view, decision, options):
        chosen = super().decide(view, decision, options)
        if decision == "set_card" and str(view.get("seat", "")).startswith("p"):
            eff = self._guess_defeat_board(view)
            probs = getattr(self, "_board_defeat_probs", None) or {}
            top = sorted(getattr(self, "_b84_top_boards", ()) or ())
            odb = getattr(self, "_observed_defeat_board", None)
            odb_a = getattr(self, "_b138_odb_a", "(未計算)")
            # (a) を適用したときの実効照準（odb を差し替えて同じ関数を通す）
            _save = self._observed_defeat_board
            try:
                self._observed_defeat_board = odb_a
                eff_a = self._guess_defeat_board(view)
            finally:
                self._observed_defeat_board = _save
            self.rows.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"),
                "eff": eff, "eff_a": eff_a, "odb": odb, "odb_a": odb_a,
                "top": top,
                "p_eff": probs.get(eff, 0.0) if eff else None,
                "probs": {k: round(v, 4) for k, v in probs.items()},
            })
        return chosen

    # ---- (a) の材料：同じ集計を「ボード敗北で終わったループ」だけで回す ----
    def _recompute(self, view: dict) -> None:
        super()._recompute(view)
        from collections import Counter as _C
        defeat_lps = {e.get("loop") for e in view.get("history", [])
                      if e.get("event") == "loop_result"
                      and "敗北" in str(e.get("result", ""))}
        hosp_fired_lps = {e.get("loop") for e in view.get("history", [])
                          if e.get("event") == "incident"
                          and e.get("name") == "病院の事件" and e.get("occurs")}
        butterfly_lps = {e.get("loop") for e in view.get("history", [])
                         if e.get("event") == "incident"
                         and e.get("name") == "蝶の羽ばたき" and e.get("occurs")}
        p_future = sum(p for (ry, _x), p in self._belief.rule_marginals().items()
                       if ry == "未来改変プラン")
        ended_by_effect = _b138_board_loss_loops(view)
        cnt: dict = _C()
        by_loop: dict = {}
        cnt_all: dict = _C()       # ★B-146：絞る**前**の票（枚数）
        all_by_loop: dict = {}     # ★B-146：絞る**前**の票（どのループが何票入れたか）
        for e in view.get("history", []):
            if e.get("event") == "loop_board" and e.get("loop") in defeat_lps:
                if p_future > 0.2 and e.get("loop") in butterfly_lps:
                    continue
                _skip = e.get("loop") in ended_by_effect      # ★(a) の絞り
                for b, v in (e.get("board_anyaku") or {}).items():
                    if v >= 2 and (b != "病院" or e.get("loop") in hosp_fired_lps):
                        cnt_all[b] += 1
                        all_by_loop.setdefault(e.get("loop"), []).append(b)
                        if _skip:
                            continue
                        cnt[b] += 1
                        by_loop.setdefault(e.get("loop"), []).append(b)
        self._b138_odb_a = self._odb_pick(cnt, by_loop)
        # ★B-146：狭い版＝**票は落とさず**同票タイブレークの材料だけを板敗北ループに絞る。
        self._b146_odb_t = self._odb_pick(
            cnt_all, all_by_loop, recent_by_loop=(by_loop or None))
        # ★B-146：票の由来（ループ→投じた板）と、各ループの公開 `loop_end.reason`。
        #   **読むだけ**＝AI の判断経路には入らない（`_b138_odb_a` と同じ扱い）。
        self._b146_vote_by_loop = all_by_loop
        _reason: dict = {}
        for e in view.get("history", []):
            if e.get("event") == "loop_end":
                _reason.setdefault(e.get("loop"), set()).add(str(e.get("reason")))
        self._b146_end_reason = {k: sorted(v) for k, v in _reason.items()}


_THETAS = (0.4, 0.5, 0.6, 0.8, 0.9, 0.95)


def cmd_count(args) -> int:
    print(f"[count] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}")
    print(f"[count] 切替口 B137_DEFEAT_CAPABLE_ONLY="
          f"{HeuristicProtagonist.B137_DEFEAT_CAPABLE_ONLY} / "
          f"B131_ODB_RECENT_TIEBREAK={HeuristicProtagonist.B131_ODB_RECENT_TIEBREAK}")
    for days in (3, 5):
        n_seat = 0
        a_odb_diff = 0        # (a) で odb が変わる席
        a_odb_none = 0        # うち odb が None 化する席
        a_aim_diff = 0        # ★(a) で**実効照準**が変わる席（行為の数え上げ）
        b1 = 0                # (b) 現在 +4.0 の宛先が無い（None）→ top に付く席
        b2 = 0                # ★(b) 現在の宛先が top の外＝+4.0 が別の板へ移る席
        both = 0
        b2_by_theta = {t: 0 for t in _THETAS}
        a_examples: list = []
        b_examples: list = []
        for name, seed, sc in _scripts(days):
            probe = replace(sc, loops=8)
            mm = HeuristicMastermind(seed)
            hp = _Probe(seed)
            run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
            for r in hp.rows:
                n_seat += 1
                if r["odb"] != r["odb_a"]:
                    a_odb_diff += 1
                    if r["odb"] is not None and r["odb_a"] is None:
                        a_odb_none += 1
                a_hit = (r["eff"] != r["eff_a"])
                if a_hit:
                    a_aim_diff += 1
                    if len(a_examples) < 6:
                        a_examples.append((name, seed, r))
                # (b)：+4.0 の宛先を _b84_top_boards（先頭グループ）に合わせたら変わるか
                tops = r["top"]
                b_hit = bool(tops) and (r["eff"] not in tops)
                if b_hit:
                    if r["eff"] is None:
                        b1 += 1
                    else:
                        b2 += 1
                        if len(b_examples) < 6:
                            b_examples.append((name, seed, r))
                if a_hit and b_hit:
                    both += 1
                if b_hit and r["eff"] is not None:
                    _pt = max((r["probs"].get(t, 0.0) for t in tops), default=0.0)
                    for th in _THETAS:
                        if _pt >= th:
                            b2_by_theta[th] += 1
        print(f"\n=== {days}日級：set_card 席 {n_seat} ===")
        print(f"  (a) odb が変わる席              : {a_odb_diff}（うち None 化 {a_odb_none}）")
        print(f"  (a) ★実効照準が変わる席          : {a_aim_diff}")
        print(f"  (b) 宛先が top の外だった席 合計 : {b1 + b2}")
        print(f"      内訳 b1（現在は宛先なし→付く）: {b1}")
        print(f"      内訳 b2（★宛先が別の板へ移る）: {b2}")
        print(f"  (a)∩(b) の重なり                : {both}")
        print("      b2 のうち P(最上位)>=θ の席: "
              + " ".join(f"θ={t}:{b2_by_theta[t]}" for t in _THETAS))
        for name, seed, r in a_examples:
            print(f"   (a)例 {name} s{seed} L{r['loop']}D{r['day']} {r['seat']}: "
                  f"odb={r['odb']}→{r['odb_a']} 照準 {r['eff']}→{r['eff_a']} "
                  f"probs={r['probs']}")
        for name, seed, r in b_examples:
            print(f"   (b2)例 {name} s{seed} L{r['loop']}D{r['day']} {r['seat']}: "
                  f"eff={r['eff']}(P={r['p_eff']}) top={r['top']} probs={r['probs']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-138 Phase 1 実測ハーネス")
    ap.add_argument("cmd", choices=["reasons", "count"])
    a = ap.parse_args(argv)
    return {"reasons": cmd_reasons, "count": cmd_count}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
