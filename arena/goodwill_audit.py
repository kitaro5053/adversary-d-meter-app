# -*- coding: utf-8 -*-
"""B-86'：友好+カードの「無駄打ち席」を数える計測ハーネス（本番経路は無変更）。

★何を数えるか＝**打っても算術的にゼロの 友好+1/+2**。

KB 接地（`rules/20_goodwill_abilities.md`・`rules/00_rules_core.md`）:
  - 友好能力は「記載されたマーク（ハート）の個数**以上**の友好がそのキャラに置かれて
    いれば使用可」（`rules/20:20`）＝友好+ の価値は**次の閾値へ到達させること**にある。
  - 「能力を使っても友好カウンターは**減らない**」（`rules/20:22`）
    ＝**閾値を超えた分の友好は、より高い閾値の能力が無い限り何も生まない**。
  - 「1ループ1回制限の能力はそのループ中1回のみ」（`rules/20:22`）。
  - 友好能力の**使用/解決/拒否は公開情報**（`sim/flow.py` の `goodwill_used` /
    `goodwill_resolved` イベント＝両陣営が見ている宣言）。

分類（FableA 指示 2026-07-30・A / B-1 / B-2 / C を分けて計上）:
  - **A_role_known**＝実装済み能力が**自身の役職開示だけ**のキャラ（サラリーマン／イレギュラー）で、
    その**役職を既に知っている**。配役はゲーム中固定＝以後の全ループで情報価値ゼロ。
  - **B1_refused**＝**過去に拒否を観測した**キャラ。`rules/20:24`＝拒否できるのは
    能力を使うキャラが友好無視／絶対友好無視を持つ場合のみ＝拒否の観測は**証明**。
  - **B2_ignore_certain**＝拒否は未観測だが**友好無視を持つ配役が確定的**。
  - **C_doctor_arms_mm**＝★**無駄ではなく有害**。医者が友好無視を持つ場合、友好2以上で
    **脚本家が脚本家能力フェイズに医者の能力（不安+1）を使えるようになる**（`rules/60:81` B-8）。
  - **D_no_ability**＝実装済み友好能力を1つも持たないキャラ（入院患者・黒猫 等）。
  - **E_***＝到達済みしか無い／このループの残り日数では次の閾値に到達不能。
  - **ok_tt**＝TT の可能性が残るキャラ（`rules/50:127-128`＝友好3以上は敗北条件の封じ手）
    ＝**切ってはいけない**ので無駄に数えない。

使い方:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.goodwill_audit
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.goodwill_audit --days 5
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.goodwill_audit --json out.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from engine.data import goodwill_abilities_of
from sim import run_game

import agents.heuristic_protagonist as _hp_mod

_builtin_max = max


def used_abilities_this_loop(view: dict) -> set:
    """このループ中に**宣言された**友好能力 (キャラ, 能力名) の集合。

    公開情報のみ（`goodwill_used` は宣言＝両陣営が見ている）。ループ跨ぎを避けるため
    直近の `loop_start` 以降の履歴だけを見る。
    """
    hist = view.get("history", []) or []
    start = 0
    for i, e in enumerate(hist):
        if e.get("event") in ("loop_start", "loop_begin", "loop_setup"):
            start = i
    out = set()
    for e in hist[start:]:
        if e.get("event") == "goodwill_used":
            out.add((e.get("character"), e.get("ability")))
    return out


#: 「自身の役職開示」＝開示されるのは**行使キャラ自身**の役職。配役はゲーム中固定
#  （`rules/00_rules_core.md` 非公開シート／`rules/20:44,167-170`＝サラリーマン、
#   `rules/20:57,106-108`＝イレギュラー）＝**一度開示されたら情報は永久に得られている**。
#  ★アルバイト？（拡張）は「自身役職開示**＋友好+2**」＝効果が残る＝含めない。
SELF_ROLE_REVEAL: frozenset = frozenset({
    ("サラリーマン", "自身の役職開示"),
    ("イレギュラー", "自身の役職開示（第2L以降）"),
})


def refused_chars(view: dict) -> set:
    """このゲーム中に友好能力を**拒否された**キャラ（公開情報・全ループ累積）。

    `rules/20:24`＝拒否できるのは**能力を使うキャラ**が友好無視／絶対友好無視を持つ場合のみ。
    ∴ 拒否の観測＝そのキャラの配役が友好無視系である**証明**。配役はゲーム中固定。
    """
    return {e["character"] for e in (view.get("history") or [])
            if e.get("event") == "goodwill_refused" and e.get("character")}


def ignore_prob(agent, tgt: str) -> tuple[float, float]:
    """(友好無視系役職の周辺確率, うち絶対友好無視の周辺確率)。"""
    from engine.data import ROLE_CLAUSE_ABILITY
    marg = agent._belief.role_marginals().get(tgt, {})
    p_any = sum(p for r, p in marg.items()
                if ROLE_CLAUSE_ABILITY.get(r) in ("友好無視", "絶対友好無視"))
    p_abs = sum(p for r, p in marg.items()
                if ROLE_CLAUSE_ABILITY.get(r) == "絶対友好無視")
    return p_any, p_abs


def classify_goodwill(view: dict, tgt: str, agent, card: str,
                      theta: float = 0.999) -> dict:
    """1席分の分類（公開情報＋belief のみで判定）。"""
    from engine.data import UNREFUSABLE_ABILITY_CHARS
    from sim.abilities import is_implemented

    ch = None
    for c in view.get("characters", []):
        if c.get("name") == tgt:
            ch = c
            break
    if ch is None or not ch.get("alive"):
        return {"cat": "other", "detail": "対象不在"}
    g = ch.get("goodwill", 0) or 0
    step = 2 if card == "友好+2" else 1
    abs_all = [a for a in (goodwill_abilities_of(tgt) or [])
               if is_implemented(tgt, a["name"])]
    p_any, p_abs = ignore_prob(agent, tgt)
    tt_p = agent._belief.role_marginals().get(tgt, {}).get("タイムトラベラー", 0.0)
    base = {"goodwill": g, "p_ignore": round(p_any, 3),
            "p_abs_ignore": round(p_abs, 3), "tt_p": round(tt_p, 3)}
    # ★TT（`rules/50:127-128`）＝友好3以上は敗北条件の封じ手＝**切ってはいけない**。
    if tt_p > 0.0 or tgt in (getattr(agent, "_tt_guards", ()) or ()):
        return {"cat": "ok_tt", **base}
    # --- C：医者に友好2を積むと脚本家に弾を渡す（`rules/60:81` B-8・友好無視＋友好2）---
    if (tgt == "医者" and p_any >= theta and g + step >= 2
            and tgt not in UNREFUSABLE_ABILITY_CHARS):
        return {"cat": "C_doctor_arms_mm", **base}
    # --- B-1：拒否を観測（配役はゲーム中固定＝以後の全ループで無意味）---
    if tgt in refused_chars(view) and tgt not in UNREFUSABLE_ABILITY_CHARS:
        return {"cat": "B1_refused", **base}
    # --- B-2：友好無視を持つ配役が確定的 ---
    if p_any >= theta and tgt not in UNREFUSABLE_ABILITY_CHARS:
        return {"cat": "B2_ignore_certain", **base}
    if not abs_all:
        return {"cat": "D_no_ability", **base, "detail": "実装済み友好能力なし"}
    # --- A：自身開示しか持たず、その役職を既に知っている ---
    if all((tgt, a["name"]) in SELF_ROLE_REVEAL for a in abs_all) \
            and agent._gini.get(tgt, 1.0) < 0.02:
        return {"cat": "A_role_known", **base,
                "detail": "自身開示のみ＋役職既知＝得られる情報ゼロ"}
    used = used_abilities_this_loop(view)
    unreached = [a for a in abs_all if a["hearts"] - g > 0]
    if not unreached:
        any_used = all((tgt, a["name"]) in used for a in abs_all)
        return {"cat": ("E_all_used" if any_used else "E_opens_nothing"),
                **base, "hearts": [a["hearts"] for a in abs_all]}
    # 未到達能力がある：このループの残り日数で届くか
    #   ★主人公は同一対象に重ねられない＝1ターン最大+2（KB: 00 セット手順）。
    #     ∴ 今日この札を置いた後に足せるのは 残り日数×2 が上限。
    #     さらに友好付与能力（お嬢様/アイドル/転校生 各+1）を楽観的に日1つ足して +3/日 とする
    #     ＝**「届かない」と言い切りにくい側**（保守側）。
    day, dpl = view.get("day", 1), view.get("days_per_loop", 1)
    days_left = max(0, dpl - day)
    reach = g + step + 3 * days_left
    min_need = min(a["hearts"] for a in unreached)
    if min_need > reach:
        return {"cat": "E_unreachable", **base, "need": min_need,
                "reach": reach, "days_left": days_left}
    if not agent._invest_has_tgt.get(tgt, True):
        return {"cat": "ok_no_immediate_target", **base}
    return {"cat": "ok", **base}


class _GwTally(HeuristicProtagonist):
    """挙動は本体と完全同一。選ばれた 友好+ 席を分類して数えるだけ。

    ★スコア捕捉は `agents.debug.ProbedProtagonist` / `arena.void_audit` と同じ仕掛け
    （モジュールグローバル `max` の一時差し替え）。`max` は builtin へ委譲＝**選択は不変**。
    """

    #: 「確定」とみなす周辺確率の閾値（監査の分類のみ・本番AIの挙動には影響しない）。
    THETA: float = 0.999

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rows: list[dict] = []

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
        if decision == "set_card" and chosen.get("card") in ("友好+1", "友好+2") \
                and chosen.get("target_kind") == "character":
            tgt = chosen.get("target")
            info = classify_goodwill(view, tgt, self, chosen.get("card"),
                                     theta=self.THETA)
            scored = cap.get("scored", [])
            top = scored[0][0] if scored else None
            nxt = None
            for s, o in scored:
                if not (o.get("card") == chosen.get("card")
                        and o.get("target") == tgt
                        and o.get("target_kind") == chosen.get("target_kind")):
                    nxt = (round(s, 2), o.get("card"), o.get("target"))
                    break
            self.rows.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"), "card": chosen.get("card"),
                "target": tgt,
                "score": round(top, 2) if top is not None else None,
                "next": nxt,
                "tt_guard": tgt in (getattr(self, "_tt_guards", ()) or ()),
                "tt_p": round(self._belief.role_marginals().get(tgt, {})
                              .get("タイムトラベラー", 0.0), 3),
                "gini": round(self._gini.get(tgt, 0.0), 3),
                "gini_max": round(max(self._gini.values() or [0.0]), 3),
                "inv": round(self._invest.get(tgt, 0.0), 2),
                **info,
            })
        return chosen


def audit_game(script, seed: int, loops: int = 8) -> tuple[str, list[dict]]:
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = _GwTally(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome = "defense"
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        outcome = "loss"
    return outcome, hp.rows


#: 「無駄／有害」として主指標に数える分類（`ok*` は数えない）。
WASTE_CATS = ("A_role_known", "B1_refused", "B2_ignore_certain",
              "C_doctor_arms_mm", "D_no_ability",
              "E_all_used", "E_opens_nothing", "E_unreachable")


def run(days: int = 3, loops: int = 8, verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts
    total = Counter()
    by_cat = Counter()
    by_target = Counter()
    per_script: list[dict] = []
    for name, seed, sc in benchmark_scripts(days=days):
        outcome, rows = audit_game(sc, seed, loops=loops)
        waste = [r for r in rows if r["cat"] in WASTE_CATS]
        total["gw_seats"] += len(rows)
        total["waste"] += len(waste)
        for r in rows:
            by_cat[r["cat"]] += 1
        for r in waste:
            by_target[r["target"]] += 1
        per_script.append({"script": name, "seed": seed, "outcome": outcome,
                           "gw": len(rows), "waste": len(waste),
                           "waste_rows": waste})
        if verbose:
            print(f"  {name} s{seed}: gw={len(rows)} waste={len(waste)} [{outcome}]",
                  flush=True)
    return {"days": days, "totals": dict(total), "by_cat": dict(by_cat),
            "waste_by_target": dict(by_target), "per_script": per_script}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    res = run(days=a.days, loops=a.loops, verbose=a.verbose)
    t = res["totals"]
    print(f"== 友好+ 無駄打ち監査（{a.days}日級） ==")
    print(f"友好+（キャラ対象）: {t['gw_seats']} 席")
    print(f"うち無駄打ち      : {t['waste']} 席 "
          f"({100.0 * t['waste'] / max(1, t['gw_seats']):.1f}%)")
    print("分類:", dict(sorted(res["by_cat"].items(), key=lambda kv: -kv[1])))
    print("無駄打ちの対象別:", dict(sorted(res["waste_by_target"].items(),
                                           key=lambda kv: -kv[1])))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
