# -*- coding: utf-8 -*-
"""B-109／B-110：「このターン何も変えない手」の席数を数える計測ハーネス（本番経路は無変更）。

★何を数えるか＝**打っても、このループの帰結が KB 上ひとつも変わらない札**。
B-103（板への空振り暗躍禁止）・B-86'（友好+ の無駄打ち）と同じ「無駄な手の席数」型の主指標。
**それらが既に潰した種別は二重に数えない**（`existing_noop` として別枠で表示するだけ）。

------------------------------------------------------------------------------
KB 接地（`rules/` から一意に読める部分だけを使う）
------------------------------------------------------------------------------

**前提1：カウンターはループを跨がない**
  `rules/00_rules_core.md:86`「カウンターの除去と配置＝**全カード・全ボードのカウンターを除去**
  → ループ開始時に置くべきカウンターがあれば置く（例: 因果の糸）」
  ∴ **最終日に置いたカウンターは、そのループ内で使われなければ何も生まない。**

**前提2：不安カウンターの効果は「事件の発生条件」だけ（FS）**
  `rules/00:30,38`「不安臨界以上で事件発生の条件」／`rules/40:157`「発生条件2つ（犯人が生存／
  犯人に不安臨界以上の不安）を両方満たすと必ず発生」。
  FS の役職（`rules/40:80-140`）に**不安の閾値を参照するものは1つも無い**。
  BTX だけが例外を2つ持つ：
    - **妄想拡大ウイルス (X)**（`rules/50:79-81`）＝不安3以上でパーソンがSK化／1以下で解除。
    - **メインラバーズ**（`rules/50:159-160`）＝不安3以上＋暗躍1以上でターン終了フェイズに主人公死亡。
  ∴ **このループに残り事件が1件も無く、上記2例外の可能性も無い**なら、不安を1つ減らしても
     **算術的に何も変わらない**（ループ終了で消える）。

**前提3：友好カウンターの効果は「友好能力の使用可否」だけ**
  `rules/20:20`「必要友好数＝各能力に記載されたハート数**以上**の友好がそのキャラに置かれていれば使用可」／
  `rules/20:22`「能力を使っても友好カウンターは**減らない**」「1ループ1回制限の能力はそのループ中1回のみ」。
  友好能力を使うのは**主人公能力フェイズ**＝行動解決フェイズの**後・同じ日**（`rules/00:113` 付近の
  ターン進行）。∴ **最終日**に友好+ を置く価値は「**その日の主人公能力フェイズで新たに使える
  能力を解禁すること**」に限られる。解禁が無ければループ終了でカウンターごと消える。
  ★例外1＝**タイムトラベラー**（`rules/50:127-128`）＝最終日のターン終了フェイズに友好2以下だと
    任意敗北を宣言されうる＝**友好3以上は敗北条件の封じ手**＝切ってはいけない（B-86' の `gw_keep`）。
  ★例外2＝**因果の糸 (X)**（`rules/50:85`）＝ループ終了時に友好が置かれていたキャラ全員に
    次ループ開始時 不安2。∴ 最終日の友好+ は**無駄ではなく有害**になりうる（友好0→1 の時だけ。
    既に1以上なら「置かれていた」判定は変わらない＝害も増えない）。

------------------------------------------------------------------------------
分類
------------------------------------------------------------------------------
- **F1_gw_final_no_unlock**＝最終日の 友好+ で、置いても**新たに解禁される未使用の実装済み能力が無い**。
  （TT 可能性が残る対象は除外＝`ok_tt`。因果の糸の可能性×友好0 は `F1b_gw_final_ito_harm` に分けて計上）
- **F2_unrest_no_incident_left**＝不安-1 で、**このループに残り事件が無く**、不安を参照する
  役職/ルール（妄想拡大ウイルス・メインラバーズ）の可能性も無い。
- **existing_noop**＝B-28/B-103/B-86' の `noop_reason` が既に空振りと判定している席（**二重計上しない**）。

使い方:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.futile_audit
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.futile_audit --days 5
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.futile_audit --days 5 --json out.json
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

#: 不安の閾値を参照する役職（BTX のみ）＝`rules/50:159-160`（メインラバーズ）。
UNREST_SENSITIVE_ROLES: frozenset = frozenset({"メインラバーズ"})
#: 不安の閾値を参照するルールX（BTX のみ）＝`rules/50:79-81`。
UNREST_SENSITIVE_RULES: frozenset = frozenset({"妄想拡大ウイルス"})
#: ループ終了時の友好を参照するルールX＝`rules/50:85`（次ループ開始時に不安+2＝**有害**）。
GOODWILL_END_RULES: frozenset = frozenset({"因果の糸"})


def remaining_incidents(view: dict) -> list[dict]:
    """このループでまだ解決していない事件（日付は公開情報＝`sim/views.py`）。"""
    return [i for i in (view.get("incidents") or [])
            if int(i.get("day", 0)) >= int(view.get("day", 1))]


def _rule_x_prob(agent, names: frozenset) -> float:
    """ルールXに `names` のいずれかが含まれる周辺確率（belief）。"""
    return sum(p for (_ry, rxs), p in agent._belief.rule_marginals().items()
               if any(n in rxs for n in names))


def unrest_matters_beyond_incidents(view: dict, agent, tgt: str) -> bool:
    """事件以外に不安の値が帰結を変えうるか（True＝切ってはいけない＝健全側）。"""
    if view.get("set") != "BTX":
        return False       # FS の役職に不安閾値を見るものは無い（`rules/40`）
    if _rule_x_prob(agent, UNREST_SENSITIVE_RULES) > 0.0:
        return True
    marg = agent._belief.role_marginals()
    for n, d in marg.items():
        if sum(p for r, p in d.items() if r in UNREST_SENSITIVE_ROLES) > 0.0:
            # メインラバーズは**自分の**不安3で発動＝対象本人だけが問題になる
            if n == tgt:
                return True
    return False


def used_abilities_this_loop(view: dict) -> set:
    """このループ中に**宣言された**友好能力 (キャラ, 能力名)（公開情報）。"""
    hist = view.get("history", []) or []
    start = 0
    for i, e in enumerate(hist):
        if e.get("event") in ("loop_start", "loop_begin", "loop_setup"):
            start = i
    return {(e.get("character"), e.get("ability")) for e in hist[start:]
            if e.get("event") == "goodwill_used"}


def classify_unrest_down(view: dict, agent, tgt: str) -> dict | None:
    """不安-1 の1席を分類（None＝無駄と言えない）。"""
    if remaining_incidents(view):
        return None
    if unrest_matters_beyond_incidents(view, agent, tgt):
        return None
    c = next((x for x in view.get("characters", []) if x.get("name") == tgt), None)
    return {"cat": "F2_unrest_no_incident_left",
            "unrest": (c or {}).get("unrest", 0),
            "detail": "残り事件0＋不安参照役職/ルールの可能性0＝ループ終了で消える"}


def classify_goodwill_final(view: dict, agent, tgt: str, card: str) -> dict | None:
    """最終日の 友好+ の1席を分類（None＝無駄と言えない）。"""
    from sim.abilities import is_implemented
    if int(view.get("day", 1)) != int(view.get("days_per_loop", 1)):
        return None
    # ★TT の可能性が残る対象は**絶対に切らない**（`rules/50:127-128`）
    marg = agent._belief.role_marginals().get(tgt, {})
    if marg.get("タイムトラベラー", 0.0) > 0.0 \
            or tgt in (getattr(agent, "_tt_guards", ()) or ()):
        return {"cat": "ok_tt"}
    c = next((x for x in view.get("characters", []) if x.get("name") == tgt), None)
    if c is None or not c.get("alive"):
        return None
    g = c.get("goodwill", 0) or 0
    step = 2 if card == "友好+2" else 1
    used = used_abilities_this_loop(view)
    abilities = [a for a in (goodwill_abilities_of(tgt) or [])
                 if is_implemented(tgt, a["name"])]
    # 「新たに解禁される」＝今は届かないが、置けば届く。かつこのループ未使用。
    unlocked = [a for a in abilities
                if g < a["hearts"] <= g + step and (tgt, a["name"]) not in used]
    if unlocked:
        return None
    ito = _rule_x_prob(agent, GOODWILL_END_RULES) > 0.0
    return {"cat": ("F1b_gw_final_ito_harm" if (ito and g == 0)
                    else "F1_gw_final_no_unlock"),
            "goodwill": g, "hearts": [a["hearts"] for a in abilities],
            "detail": "最終日＋新規解禁なし＝ループ終了でカウンターごと消える"}


def classify_unrest_up(view: dict, agent, tgt: str) -> dict | None:
    """不安+1（B-16 の実験）の1席を分類（None＝無駄と言えない）。

    ★KB 接地＝`rules/00:38`「**不安臨界**：この値**以上**の不安カウンターが置かれていると、
    そのキャラが犯人の事件を起こす可能性がある」／`rules/30:53,77`＝黒猫は不安臨界**0**で
    「**不安カウンターが無くても**自身が犯人の事件の発生条件を満たす」。
    ∴ 不安臨界0のキャラでは、不安を積んでも「発生した／しなかった」の観測が**1ビットも
    変わらない**＝**B-16 の実験としては情報ゼロ**。
    ★これは「不安そのものが無価値」という主張ではない（妄想拡大ウイルス(X)＝不安3で
    パーソンがSK化／メインラバーズ＝不安3+暗躍1で主人公死亡）＝**ウイルス試験は数えない**。
    """
    from engine.data import unrest_threshold_of
    if unrest_threshold_of(tgt) != 0:
        return None
    # ウイルス試験の対象なら情報ゼロではない（不安3でSK化＝`rules/50:79-81`）
    if tgt in (getattr(agent, "_virus_test_targets", ()) or ()):
        return {"cat": "ok_virus_test"}
    # 実験モード／負けループでのみ B-16 は発火する（それ以外は通常の採点）
    if not (getattr(agent, "_experiment", False)
            or getattr(agent, "_loop_lost", False)):
        return None
    if tgt not in (getattr(agent, "_future_culprits", None)
                   or getattr(agent, "_culprits", ()) or ()):
        return None
    return {"cat": "F3_experiment_zero_threshold",
            "detail": "不安臨界0＝発生条件が不安に依存しない＝実験として情報ゼロ"}


class _FutileTally(HeuristicProtagonist):
    """挙動は本体と完全同一。選ばれた席を分類して数えるだけ。

    スコア捕捉は `arena.void_audit` / `arena.goodwill_audit` と同じ仕掛け
    （モジュールグローバル `max` の一時差し替え）。`max` は builtin へ委譲＝**選択は不変**。
    """

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
        if decision != "set_card":
            return chosen
        card, tgt = chosen.get("card"), chosen.get("target")
        kind = chosen.get("target_kind")
        info = None
        if kind == "character" and card == "不安-1":
            info = classify_unrest_down(view, self, tgt)
        elif kind == "character" and card in ("友好+1", "友好+2"):
            info = classify_goodwill_final(view, self, tgt, card)
        elif kind == "character" and card == "不安+1":
            info = classify_unrest_up(view, self, tgt)
        # 既存ゲート（B-28/B-103/B-86'）が既に空振りと判定している席は二重計上しない
        existing = self._futile_existing_noop(view, card, tgt, kind)
        scored = cap.get("scored", [])
        top = scored[0][0] if scored else None
        nxt = None
        for s, o in scored:
            if not (o.get("card") == card and o.get("target") == tgt
                    and o.get("target_kind") == kind):
                nxt = (round(s, 2), o.get("card"), o.get("target"))
                break
        # 「移動札に切り替えたら何点だったか」＝②③の機会費用
        best_move = None
        for s, o in scored:
            if str(o.get("card", "")).startswith("移動") \
                    and o.get("card") != "移動禁止":
                best_move = (round(s, 2), o.get("card"), o.get("target"))
                break
        self.rows.append({
            "loop": view.get("loop"), "day": view.get("day"),
            "seat": view.get("seat"), "card": card, "target": tgt, "kind": kind,
            "score": round(top, 2) if top is not None else None,
            "next": nxt, "best_move": best_move,
            "loop_lost": bool(getattr(self, "_loop_lost", False)),
            "existing_noop": existing,
            "cat": (info or {}).get("cat"),
            "info": info,
        })
        return chosen

    def _futile_existing_noop(self, view, card, tgt, kind) -> str | None:
        """B-28 の単一チョークポイントが既に空振りと言っているか（理由文字列）。

        ★`_base_score` 内のローカル `_noop_ctx` と**同じ材料**を view と自分の属性から組む
        （本番経路は読むだけ・一切変更しない）。kill_zone だけは局所変数のため省略＝
        G5（KP を kill zone へ送る）は数え落とす側＝**健全側**（過大計上しない）。
        """
        from agents.card_effect import NoopCtx, noop_reason
        pl = view.get("placements", []) or []
        mm_chars = frozenset(p["target"] for p in pl
                             if p.get("owner") == "mastermind"
                             and p.get("target_kind") == "character")
        mm_boards = frozenset(p["target"] for p in pl
                              if p.get("owner") == "mastermind"
                              and p.get("target_kind") == "board")
        ctx = NoopCtx(
            mm_chars=mm_chars, mm_boards=mm_boards,
            keyperson=getattr(self, "_keyperson", None),
            kill_zone=None,
            kuromaku_suspects=frozenset(getattr(self, "_kuromaku_suspects", ())),
            killer_suspects=frozenset(getattr(self, "_killer_suspects", ())),
            friend_guards=frozenset(getattr(self, "_friend_guards", ())),
            gw_keep=frozenset(getattr(self, "_b86_gw_keep", ())),
            gw_refused=frozenset(getattr(self, "_b86_gw_refused", ())),
            gw_ignore_certain=frozenset(getattr(self, "_b86_gw_ignore_certain", ())),
            gw_info_exhausted=frozenset(getattr(self, "_b86_gw_info_exhausted", ())),
            gw_arms_mm=frozenset(getattr(self, "_b86_gw_arms_mm", ())),
        )
        r = noop_reason(view, card, tgt, kind, ctx)
        return r.reason if r is not None else None


def lost_loops(state) -> set:
    """実際に**敗北した**ループ番号の集合（`loop_result` は敗北時のみ出る＝
    `sim/effects.py:705`。∴ 記録の無いループ＝主人公が守り切ったループ）。"""
    return {e.get("loop") for e in state.history
            if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}


def audit_game(script, seed: int, loops: int = 8) -> tuple[str, list[dict], dict]:
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = _FutileTally(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome = "defense"
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
    else:
        outcome = "loss"
    return outcome, hp.rows, lost_loops(state)


#: 主指標に数える分類（`ok_*` と None は数えない）。
WASTE_CATS = ("F1_gw_final_no_unlock", "F1b_gw_final_ito_harm",
              "F2_unrest_no_incident_left", "F3_experiment_zero_threshold")

#: 「手詰まり席」の判定点＝`_base_score` が返す最小の**実効手**（8.0＝B-63 冷却席の譲り）。
#  これ未満しか無い席＝確定の模範手が1つも無い席（論点②③の対象）。
IDLE_BAND: float = 8.0


def run(days: int = 3, loops: int = 8, verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts
    total = Counter()
    by_cat = Counter()
    by_target = Counter()
    ll = Counter()          # _loop_lost の判定精度
    by_idle = Counter()     # 手詰まり席で選ばれた札
    fp_loops: set = set()   # 誤判定（負けと言ったが守り切った）ループ
    per_script: list[dict] = []
    for name, seed, sc in benchmark_scripts(days=days):
        outcome, rows, lost = audit_game(sc, seed, loops=loops)
        waste = [r for r in rows if r["cat"] in WASTE_CATS
                 and not r["existing_noop"]]
        total["seats"] += len(rows)
        total["waste"] += len(waste)
        total["existing_noop"] += sum(1 for r in rows if r["existing_noop"])
        for r in rows:
            if r["cat"]:
                by_cat[r["cat"]] += 1
        for r in waste:
            by_target[f"{r['card']}→{r['target']}"] += 1
            if r["best_move"]:
                total["waste_with_move_option"] += 1
        # ★論点②③の副指標＝**手詰まり席**（盤上の最良候補が「実効手の最小点」8.0 未満
        #   ＝確定の模範手が1つも無い席）と、そこで何を選んだかの内訳。
        for r in rows:
            if r["score"] is not None and r["score"] < IDLE_BAND:
                total["idle_seats"] += 1
                by_idle[str(r["card"])] += 1
        # ★_loop_lost の判定精度（席単位）：宣言したループが実際に敗北したか。
        #   ground truth＝`loop_result`（敗北時のみ出る）＝記録の無いループは守り切った。
        for r in rows:
            is_lost = r["loop"] in lost
            key = ("TP" if (r["loop_lost"] and is_lost) else
                   "FP" if (r["loop_lost"] and not is_lost) else
                   "FN" if (not r["loop_lost"] and is_lost) else "TN")
            ll[key] += 1
            if r["loop_lost"] and not is_lost:
                fp_loops.add(f"{name}_s{seed}_L{r['loop']}")
        per_script.append({"script": name, "seed": seed, "outcome": outcome,
                           "seats": len(rows), "waste": len(waste),
                           "waste_rows": waste})
        if verbose:
            print(f"  {name} s{seed}: seats={len(rows)} waste={len(waste)} [{outcome}]",
                  flush=True)
    return {"days": days, "totals": dict(total), "by_cat": dict(by_cat),
            "waste_by_move": dict(by_target), "idle_by_card": dict(by_idle),
            "loop_lost": dict(ll),
            "loop_lost_fp_loops": sorted(fp_loops), "per_script": per_script}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    res = run(days=a.days, loops=a.loops, verbose=a.verbose)
    t, ll = res["totals"], res["loop_lost"]
    print(f"== 何も変えない手の監査（{a.days}日級） ==")
    print(f"set_card 席     : {t.get('seats', 0)}")
    print(f"★主指標＝何も変えない手: {t.get('waste', 0)} 席 "
          f"({100.0 * t.get('waste', 0) / max(1, t.get('seats', 0)):.1f}%)")
    print(f"  うち移動札が選べた席 : {t.get('waste_with_move_option', 0)}")
    print(f"既存ゲートが空振り判定済 : {t.get('existing_noop', 0)} 席（二重計上しない）")
    print("分類:", dict(sorted(res["by_cat"].items(), key=lambda kv: -kv[1])))
    print("内訳(上位):", dict(sorted(res["waste_by_move"].items(),
                                     key=lambda kv: -kv[1])[:12]))
    print(f"★手詰まり席（最良候補 < {IDLE_BAND}）: {t.get('idle_seats', 0)} 席"
          "  選ばれた札:",
          dict(sorted(res["idle_by_card"].items(), key=lambda kv: -kv[1])))
    tp, fp = ll.get("TP", 0), ll.get("FP", 0)
    fn, tn = ll.get("FN", 0), ll.get("TN", 0)
    print(f"_loop_lost 席単位: TP={tp} FP={fp} FN={fn} TN={tn} "
          f"適合率={100.0 * tp / max(1, tp + fp):.1f}% "
          f"再現率={100.0 * tp / max(1, tp + fn):.1f}%")
    if res["loop_lost_fp_loops"]:
        print("★誤判定ループ（負けと言ったが守り切った）:",
              res["loop_lost_fp_loops"][:20],
              f"... 計{len(res['loop_lost_fp_loops'])}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
