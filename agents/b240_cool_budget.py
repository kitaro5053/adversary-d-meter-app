# -*- coding: utf-8 -*-
"""B-240：**冷却札の家計**（cooling-card budget）＝`B224_COOL_FLOOR` の床が
そのループ最後の `不安-1` を今日の候補に使い切ることを止める。

## 何をするモジュールか（起票源＝B-237 §0-6・§72-29「次の起票の芽」）

B-237 が `random_BTX#1`（5日級 id）の実際の敗因を盤面の実数で特定した：

  L6D3 で `B224_COOL_FLOOR` の床が `不安-1→教師` に当たり、**そのループ最後の
  `不安-1` 札**を今日の候補に使い切る。床は**その日には正しく効いている**
  （教師 0/2＝D3 の事件は不発）。しかし **L6D4 は3席とも `不安-1` の選択肢が空**で、
  脚本家はポンプ先を **従者（臨界3）** へ振り替え、**1日で 1→3** に押し上げて D4 の
  事件を通す。＝**札の家計**の問題であって、床の当日判断の問題ではない。

本モジュールは、その席を**公開情報だけで**識別して**床を立てない**（＝他の手に席を譲る）。
B-232（`agents/b232_cool_math.py`）が「**今日**冷やしても届く＝証明可能な空振り」を
落とすのに対し、本語彙は「**明日**冷やす必要が算術で確定している＝最後の1枚を今日
使ってはいけない」を落とす＝**B-232 の算術を1日先へ延ばしたもの**。

## 家計の算術（材料はすべて公開情報＝神視点は使わない）

床の対象 `C` を今日冷やしてよいのは、次の3つが**同時に成り立たない**ときだけ：

  (A) **最後の1枚**＝このループにチームで残る `不安-1` が `B240_LAST_N`（既定1）枚以下。
      `不安-1` は 1/loop 札（`engine/models.py:30` ONCE_PER_LOOP）でチーム3枚/ループ、
      使用済み札は表向き＝公開（`rules/00_rules_core.md:108`）＝`used_cards` から数える。
      ★今ターン伏せられた札はまだ `used_cards` に載らない＝**多めに数える＝発火を渋る安全側**
      （`HeuristicProtagonist._b198_cool_left_loop` と同じ会計＝単一ソースとして再利用）。
  (B) **脚本家が再ポンプ可能**＝`不安+1` が脚本家の手札に残っている
      （`MASTERMIND_HAND` は `不安+1` を2枚・使用済みは `used_cards` で公開）。
      B-142 の予約条件(3)と同じ材料。0枚なら明日の押し上げは起こらない＝家計の心配も要らない。
  (C) **翌日の確定需要**＝今日より後 `B240_MAX_GAP` 日以内（既定1＝翌日）に事件が
      **予定**されており（脚本の公開情報＝`view["incidents"]`）、その日の犯人候補
      （belief `_culprit_cands[d]`＝B-224 が既に使っている情報源）に **X ≠ C** があって

          u(X) >= B240_NEED_MIN_UNREST  かつ  u(X) + SUPPLY*(d-today) >= th(X)

      を満たす。`SUPPLY`（`B240_SUPPLY_PER_DAY`・既定 2）＝脚本家の**1日あたり最大供給**
      ＝同じ対象へカード1枚（重ね置き不可）＋能力フェイズ +1（ミスリーダー系＝`rules/40:105-110`）。
      事件効果など他の供給源は数えない＝**過小＝安全側**。
      `B240_NEED_MIN_UNREST`（既定 1）＝需要と認める X を「**既に不安が1以上**」＝
      **脚本家が投資を始めている証拠がある個体**に限る絞り（Phase 0 の census で
      `u>=0` は 5日級 19 席＝ほとんどの盤面で常真に近いと実測されたため）。

## なぜ「床を立てない」だけなのか（★B-237 の判例）

B-237 の判例＝**「ある語彙から見て無価値」は「席を譲らせる根拠にならない」**。
本語彙の既定の梃子は **B-224 が立てた人工の床（103.0）を撤回する**ことだけで、
その手の**自前の点は一切下げない**（＝他の語彙が付けた価値はそのまま残り、
B-142 の予約減点など既存の家計語彙が改めて判断する）。
`B240_CAP`（既定 None＝無効）は掃引口としてのみ用意し、使う場合は flip 全数検死で
「潰した手に別の語彙の価値が付いていなかったか」を確認する義務を負う。

## 規律

- 材料＝`view` の公開情報（`used_cards`／`placements`／`incidents`／カウンター・位置）＋
  belief の `_culprit_cands`（B-224 が既に使っている情報源）。**追加の情報源を持たない**。
- 既定 OFF（`B240_COOL_BUDGET=False`）＝呼ばれない＝挙動 bit 不変。
"""
from __future__ import annotations

from engine.data import unrest_threshold_of
from engine.models import MASTERMIND_HAND


def mm_unrest_plus_left(view: dict) -> int:
    """脚本家の手札に残る `不安+1` の枚数（公開情報＝使用済み札は表向き）。"""
    used = list((view.get("used_cards", {}) or {}).get("mastermind", []))
    return MASTERMIND_HAND.count("不安+1") - used.count("不安+1")


def future_need(agent, view: dict, exclude: str | None = None) -> frozenset:
    """翌日以降（`B240_MAX_GAP` 日以内）の**算術で確定した冷却需要**のキャラ集合。

    `exclude`＝今日の冷却先（同一キャラなら「明日の分」ではない＝除く）。
    """
    today = view.get("day")
    if today is None:
        return frozenset()
    gap = int(getattr(agent, "B240_MAX_GAP", 1) or 1)
    supply = int(getattr(agent, "B240_SUPPLY_PER_DAY", 2) or 0)
    min_u = int(getattr(agent, "B240_NEED_MIN_UNREST", 1) or 0)
    sig = None
    if getattr(agent, "B240_REQUIRE_SIGNATURE", False):
        from .b224_channel import evidenced_incident_days
        sig = evidenced_incident_days(view.get("history", []) or [],
                                      view.get("loop"))
    cands_by_day = getattr(agent, "_culprit_cands", None) or {}
    out: set = set()
    for inc in (view.get("incidents") or []):
        d = inc.get("day")
        if d is None or not (today < d <= today + gap):
            continue
        if sig is not None and inc.get("name") not in (sig.get(d) or ()):
            continue
        for x in (cands_by_day.get(d) or ()):
            if x == exclude or x in out:
                continue
            th = unrest_threshold_of(x)
            if not th:
                continue                  # 臨界0（黒猫）は冷却で止まらない
            c = agent._alive(view, x)
            if c is None:
                continue
            u = int(c.get("unrest", 0) or 0)
            if u < min_u:
                continue
            if u + supply * (d - today) >= th:
                out.add(x)
    return frozenset(out)


def budget_blocked_targets(agent, view: dict, cands) -> frozenset:
    """`cands` のうち「最後の1枚を今日ここへ使うと翌日の確定需要を賄えない」候補。

    返した候補は呼び手（`heuristic_protagonist.decide`）が `_b224_cool_today`
    （＝床の対象集合）から落とす＝**床を立てない**（他の手に席を譲る）。
    """
    if not getattr(agent, "B240_COOL_BUDGET", False):
        return frozenset()
    if not cands:
        return frozenset()
    # (A) 最後の1枚か
    if agent._b198_cool_left_loop(view) > int(getattr(agent, "B240_LAST_N", 1)):
        return frozenset()
    # (B) 脚本家が再ポンプ可能か
    if getattr(agent, "B240_REQUIRE_MM_CARD", True) and mm_unrest_plus_left(view) < 1:
        return frozenset()
    # (C) 翌日の確定需要（今日の冷却先そのものは除く＝候補ごとに判定）
    need = future_need(agent, view)
    if not need:
        return frozenset()
    return frozenset(c for c in cands if need - {c})


# --------------------------------------------------------------------------
# ★第2腕＝**需要日の照準**（`B240_AIM_FLOOR`・既定 OFF）
#
# 機序（`docs/仮_b240_log/機序_BTX1_L6.md` の実測）＝家計ゲートで最後の `不安-1` を
# 温存しても、**需要日にその札が撃たれない**：`random_BTX#1` L6D4 の p2 は
# `不安-1→従者`（従者 3/3＝1枚下げれば臨界未満）を options に持ちながら、
# B-100 の強制折り手（板ガード）に席を取られていた。
# ∴ 温存だけでは結末が動かない（実測＝両ベンチ×perm3条件で per-game 完全一致）。
#
# 本腕は「**今日その1枚を撃てば事件が確実に止まる**」ことが公開情報の算術で言える
# ときだけ床を立てる（B-224 の床と同じ高さ・同じ位置）：
#
#   (1) 今日、脚本に事件が予定されている（公開）。
#   (2) X は belief の**当日犯人候補**（`_culprit_cands[today]`＝B-224 と同じ情報源）。
#   (3) `th(X) >= 1` かつ **`u(X) == th(X)`**＝**臨界ちょうど**
#       ＝放置すれば事件が起き、`不安-1` 1枚でちょうど臨界未満に落ちる
#       （`u > th` なら1枚では足りない／`u < th` なら今日は起きない＝どちらも対象外）。
#   (4) ★**B-232 の算術で「この冷却は効く」と言えること**＝`cool_math_final < th`
#       （mm の伏せ札が重なっていない＝相殺・不安禁止で潰されない、かつ
#        能力フェイズの供給実績ペアが同室にいない）。＝B-232 の述語を**逆向き**に使う。
#
# ★B-224 の床（敗北署名チャネル）とは**独立**＝署名に載っていない事件日でも立つ。
#   それが本腕の狙い（`BTX#1` の D4 はまさに署名外）だが、**発火面積は広がる**＝
#   既定 OFF で掃引口として置き、測定で採否を決める。
# --------------------------------------------------------------------------
def aim_floor_targets(agent, view: dict) -> frozenset:
    """今日「1枚撃てば確実に事件が止まる」犯人候補の集合（`B240_AIM_FLOOR` の対象）。"""
    if not getattr(agent, "B240_AIM_FLOOR", False):
        return frozenset()
    return certain_stop_targets(agent, view)


def certain_stop_targets(agent, view: dict) -> frozenset:
    """★述語の単一ソース（切替口に依らない）＝今日「1枚撃てば確実に事件が止まる」候補。

    `aim_floor_targets`（B-240 の第2腕）と B-241 の席の調停（`agents/b241_seat_arb.py`）が
    **同じ述語**を使うための抽出（2026-08-17・B-241）。上の関数から本体を切り出しただけで
    中身は一字も変えていない＝`B240_AIM_FLOOR` の挙動は bit 不変。
    """
    today = view.get("day")
    if today is None:
        return frozenset()
    if not any(i.get("day") == today for i in (view.get("incidents") or [])):
        return frozenset()
    cands = (getattr(agent, "_culprit_cands", None) or {}).get(today) or ()
    if not cands:
        return frozenset()
    from .b232_cool_math import cool_math_final
    out = set()
    for x in cands:
        th = unrest_threshold_of(x)
        if not th:
            continue
        c = agent._alive(view, x)
        if c is None:
            continue
        if int(c.get("unrest", 0) or 0) != th:      # ★臨界ちょうどだけ
            continue
        got = cool_math_final(agent, view, x)       # B-232 の算術を逆向きに使う
        if got is None:
            continue
        final, th2, _u, _cool, _sup = got
        if final < th2:                             # ＝この冷却は確実に効く
            out.add(x)
    return frozenset(out)
