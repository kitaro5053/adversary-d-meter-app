# -*- coding: utf-8 -*-
"""B-253：`不安+1`（キャラ対象）の席を `友好` へ振り替える（★既定 OFF の切替口）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-51／事前登録＝
`docs/測定_B253_不安プラス1の使い所_2026-08-18.md` §0。
出典＝ユーザー実戦フィードバック（2026-08-18）
「`不安+1` を打つぐらいなら友好を打つ、というルールはあっても良い」。

## 設計＝採点にも床にも触れない（B-241／B-246 と同じ型）

`_base_score` を書き換えず、**選ばれた後の手だけ**を差し替える。
∴ 切替口が OFF なら `decide()` に分岐が1つ増えるだけ＝**挙動 bit-for-bit 不変**。

## 実測の根拠（3日級130局・5日級70局＝測定doc §1〜§3）

- `不安+1`（キャラ）の実打席で **役職の事後確率が動いた席は 0**。
  機序＝belief の不安チャネルは**しきい値型**（`agents/belief.py:1165`／`:1138` の不安≥3）で、
  実測の**観測時の不安の最大値は 1**＝**届いていない**。
  ★計器の盲目性は変異テストで否定済み（3 に跨がせれば 42% の席で事後が動く）。
- `友好`（キャラ）は打った席の約7割がそのループ中に必要友好数へ到達し、
  到達した席の約3/4で実際に能力が使われた。
- `不安+1` 席では **100%** の席で `友好`（キャラ）が legal に存在した＝振り替え先は常にある。

★ただし **「情報が取れない＝無駄」とは決めつけない**（B-252 の教訓）。
採否はベンチ（3日級／5日級）と per-game flip で決める＝**既定 ON はユーザー裁定事項**。
"""
from __future__ import annotations

UNREST_PLUS = "不安+1"
GOODWILL_CARDS = ("友好+1", "友好+2")


def goodwill_swap(agent, view, options, best, score):
    """`不安+1`(キャラ) の手を、同じ席の最良の `友好`(キャラ) 手へ差し替える。

    返り値＝差し替え後の option（無ければ `None`＝現状維持）。
    - `agent._B253_GW_FLOOR`＝振り替え先に要求するスコアの下限（掃引口）。
    - `agent.B253_VIRUS_ONLY`＝True ならウイルス試験の席だけに限定する（狭い述語）。
    """
    if best.get("card") != UNREST_PLUS or best.get("target_kind") != "character":
        return None
    if getattr(agent, "B253_VIRUS_ONLY", False):
        if best.get("target") not in (getattr(agent, "_virus_test_targets", ()) or ()):
            return None
    floor = float(getattr(agent, "_B253_GW_FLOOR", 0.0))
    cands = [o for o in options
             if o.get("card") in GOODWILL_CARDS and o.get("target_kind") == "character"]
    if not cands:
        return None
    top = max(cands, key=score)
    return top if float(score(top)) >= floor else None
