# -*- coding: utf-8 -*-
"""B-224：敗因チャネル直結の応手（B-221 の残件のうち「加点側」＝切替口②）。

## 何をするモジュールか（起票源＝B-221 Phase 0・§72-8 残件）

B-221 Phase 0 の一次データ（`docs/仮_b221_log/why_*.json`・本レーンで再実測＝
`docs/仮_b224_log/why_*.txt`）が特定した穴：

  **先席のピン（`移動禁止→犯人候補` 102.5／185.0）が対象キャラを占有**し
  （重ね置き不可＝`sim/legal.py`）、後席の**犯人当日冷却 `不安-1→犯人候補`**
  （＝B-220 検死で flip の実効部と確定した手）が **options から構造的に消える**。
  ピンは同じ負け方で 5〜6 回落ち続けても点が変わらない＝敗北の実績が採点に還流しない。

本モジュールは**公開情報のみ**から

  「直前ループが敗北で（同署名 run≥1＝★**1敗発火**＝ユーザーの
   『L1 で負けて L2 で応手を変える』への接近。応手一致は要求しない）、
   その敗北署名に**今日と同じ日の事件発生**が含まれる」

ときに、その事件の**当日犯人候補の集合**を返す。呼び手（`heuristic_protagonist`）は

  1. **floor**＝候補への `不安-1` に採点床 `B224_FLOOR` を立てる（加点側）
  2. **yield**＝冷却札を持たない席の同候補への手（ピン等）に上限 `B224_YIELD_CAP`
     を被せ、後席の冷却へ**対象を譲らせる**（ピンより先に・またはピンと共存する形）

の2腕で「敗因チャネルに直結する1手」だけを通す。

## B-221 run 型（棄却済み）との違い＝なぜ広域引き直しにならないか

- run 型は決定打日の**応手の和集合を全部 cap**した（45+30 flip＝B-218 教訓の再演）。
- 本語彙は (i) 効く先が「**今日**の事件の犯人候補への札」だけ（日も対象も絞る）
  (ii) 発火条件に「その事件が過去の敗北署名に**発生として**載っている」を要求
  （＝敗因チャネルの実測）(iii) 応手全体は散らさない＝floor は1種の手を**足す**側。

## 設計の規律

- 材料＝`view["history"]` の公開イベント（`loss_signatures`＝B-211 是正済み）と
  `view["incidents"]`（事件の日と名前＝脚本の公開情報）と belief の犯人候補
  （`culprit_candidates`＝公開情報から構成）。神視点は使わない。
- 発火は b221_breaker の**末尾同署名 run**（勝ち／署名変化で自然リセット）に限る。
- 既定 OFF（`B224_COOL_FLOOR=False`）＝呼ばれない＝挙動 bit 不変。
"""
from __future__ import annotations

from .b221_breaker import loss_run, team_response_by_day


def evidenced_incident_days(history: list[dict],
                            loop: int | None) -> dict[int, frozenset]:
    """同署名の敗北 run の署名に載っている事件発生 {day: {事件名}}（無ければ {}）。

    run は `b221_breaker.loss_run`（末尾の連続同署名敗北・1敗から）。
    同署名 run なので incidents は全ループでほぼ同一だが、和集合を取る
    （署名キーは frozenset 一致＝実質同一集合）。
    """
    run, sigs = loss_run(history, loop)
    if not run:
        return {}
    out: dict[int, set] = {}
    for lp in run:
        for d, name in (sigs[lp].get("incidents") or ()):
            if d is None:
                continue
            out.setdefault(d, set()).add(name)
    return {d: frozenset(v) for d, v in out.items()}


def cool_targets_today(agent, view: dict) -> frozenset:
    """今日 floor/yield の対象にする**当日犯人候補**の集合（発火しない時は空）。

    発火条件（すべて公開情報＋belief）：
      1. `B224_COOL_FLOOR` が ON。
      2. 同署名の敗北 run（1敗から）の署名に (今日, 事件名) の**発生**が載っている。
      3. 今日この脚本に同名の事件が予定されている（`view["incidents"]`＝公開）。
      4. belief の犯人候補（`_culprit_cands[today]`）が非空。
      5. ★**冷却が既に試されて負けた候補は除外**＝run 内のいずれかの敗北ループで
         自チームが同じ日に `不安-1→候補` を置いたのに（署名同一＝その事件は
         そのループでも発生した＝）敗北した実績があるなら、その候補への当日冷却は
         **証明済みの空振り筋**＝floor を立てない。
         （実測の教材＝`random_BTX#1`＝邪気の汚染（板供給事件・犯人 女の子）は
          臨界を毎ループ超えて冷却1枚では止まらず、floor が毎ループ1席を
          灰にして 6[defense]→9[fb_loss] に落とした。「負けた応手を繰り返さない」
          という本チケットの原則そのもの＝floor 自身にも適用する。）
    """
    if not getattr(agent, "B224_COOL_FLOOR", False):
        return frozenset()
    today = view.get("day")
    history = view.get("history", []) or []
    run, sigs = loss_run(history, view.get("loop"))
    if not run:
        return frozenset()
    names: set = set()
    for lp in run:
        for d, name in (sigs[lp].get("incidents") or ()):
            if d == today:
                names.add(name)
    if not names:
        return frozenset()
    scheduled = {i.get("name") for i in view.get("incidents", []) or []
                 if i.get("day") == today}
    if not (names & scheduled):
        return frozenset()
    cands = set((getattr(agent, "_culprit_cands", None) or {}).get(today) or ())
    if not cands:
        return frozenset()
    # ★条件6＝臨界0の候補（黒猫）は除外＝不安を下げても必ず発生（ルール接地・
    #   既存の当日冷却語彙と同じ判定「臨界0（黒猫）は不安を下げても必ず発生」）。
    #   実測の教材＝`random_BTX#16` 5日級 L2D1（黒猫 th=0 への floor＝証明可能な空振り）。
    from engine.data import unrest_threshold_of
    cands = {c for c in cands if unrest_threshold_of(c)}
    for lp in run:                          # ★条件5＝証明済みの空振り筋を除外
        resp = team_response_by_day(history, lp).get(today, ())
        for card, tgt, kind in resp:
            if card == "不安-1" and kind == "character":
                cands.discard(tgt)
    return frozenset(cands)


def later_seat_can_cool(view: dict) -> bool:
    """今日この後の**他席**（現席を除く未行動席）が `不安-1` を出せるか。

    会計は `heuristic_protagonist._b63_seat_can_play` と同じ規約（公開/自明情報のみ）
    だが、**現席の hand は数えない**（現席が自分で冷やせるなら yield は不要＝
    floor の領分）。行動済み席は中身が見えない＝出せない側（安全側）。
    """
    acted = {p.get("owner") for p in view.get("placements", []) or []
             if p.get("owner") not in (None, "mastermind")}
    used = view.get("used_cards", {}) or {}
    for s in ("p1", "p2", "p3"):
        if s == view.get("seat") or s in acted:
            continue
        if "不安-1" not in used.get(s, ()):
            return True
    return False


def b224_yield_blocked(agent, card: str, target: str, kind: str) -> bool:
    """B-100 の折り手選択用：この折り手が yield 対象（当日犯人候補への非冷却札）か。

    採点側の yield cap と同じ述語（`_b224_yield_today`＝decide() がターン頭に計算）。
    ★B-221 の `b221_break_blocked` と同じ配線位置＝「score で下げたピンを B-100 が
    最安 break として強制し直す」逆流を塞ぐ。`keys`（被覆判定）からは外さない。
    """
    if not getattr(agent, "B224_COOL_FLOOR", False):
        return False
    if card == "不安-1":
        return False
    av = getattr(agent, "_b224_yield_today", None)
    return bool(av) and kind == "character" and target in av
