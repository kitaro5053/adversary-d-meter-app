# -*- coding: utf-8 -*-
"""B-275 フェーズ1：**完全同点の第2キー＝友好+2/+1 の対象の到達可能価値**（★既定 OFF）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-97（フェーズ0 実測）・§72-98（ユーザー裁定「進む」）。
フェーズ0 の確定事実＝perm flip 319件の 96% が `decide` の `max(options, key=score)` の
**完全同点の列挙順決着**。本丸＝`btx_seal_cat` の 45件（L1D2p1 set_card@13＝友好+2 の
対象4〜5択同点 8.0。id は列挙順先頭のご神木＝**友好能力なし**へ2日連続投資して敗北）。

## ★事前登録（実装コミットに固定・2026-08-23）

- **評価**＝perm 4条件（id/rev/h1/h5）× 両日級の8条件で防衛数・平均・per-game flip。
  合格基準（規約 §11b 段2）＝**8条件で防衛数の向きが揃う（悪化ゼロ・改善1条件以上）**。
- **特に見る局**＝(a) `btx_seal_cat`（id 45件＝直撃対象・平均 6.9 が下がるか）
  (b) `btx_seal`（籤勝ち側＝id で20件 perm悪化＝壊していないか）
  (c) 籤勝ち130件（フェーズ0 で dir=perm悪化 だった局）の防衛数。
- **flip の分散**＝ON 時の id vs rev/h1/h5 の flip 件数が減ること（分散の縮小）。
- 悪化が出たら掃引の前に**機序を1局精読**（B-107b＝掃引で戻る保証なし）。
  向きが揃わなければ**負の結果として OFF のまま報告**。

## 設計＝同点のときだけ効く第2キー（非同点席の挙動は構造的に不変）

`decide` の `best = max(options, key=score)` の**直後**で、切替口 ON のときだけ：

1. `best` が **友好+2/+1（キャラ対象）**でなければ何もしない（狭い述語＝第1弾は
   友好系カードの同点だけ。暗躍禁止・移動の同点には触れない）。
2. `score(o) == score(best)`（float の**完全等値**＝フェーズ0 の cls=exact と同じ定義）
   かつ **同一カード・キャラ対象**の候補集合（同点集合）を作る。1件なら何もしない
   ＝**非同点席は差し替えが起きない**。★1 ULP 差は同点でない（B-273ph0＝実害0の帯に触れない）。
3. 同点集合を**第2キー＝対象の到達可能価値**（下記）で解く。第2キーも同点なら
   列挙順先頭（＝従来の勝者）のまま＝**第2キーが語らない席も不変**。

★§72-97 の例示（key をタプル `(score, tiebreak)` にする）から**形だけ**変えた理由：
タプル化は ON 時に**友好系と非友好系が同点の席でカード種まで動きうる**
（第2キーが非友好系に 0 を返すため、列挙順で先だった非友好系の手が友好系に負ける）。
後処理形なら「同一カードの対象選択だけが動く」ことを**コードの形で保証**できる。
OFF 時 bit 不変の保証も強い（分岐1つ＝本モジュールは import すらされない）。

## 第2キー＝到達可能価値（既存の単一ソースのみで合成・新しい価値表は発明しない）

対象キャラ `tgt` の各友好能力（`engine.data.goodwill_abilities_of`＝閾値ハート数の単一
ソース・KB `rules/20_goodwill_abilities.md` 転記済み）について：

- (i) **届くか**＝`agent._b178_reachable_this_loop(view, need)`（B-178 の既存述語＝
  `_cooler_unlock_day` の +2/ターン×1席 の算術。友好カウンターはループ開始時に全除去
  ＝`rules/00_rules_core.md:86`＝このループ中に閾値へ届かない能力は数えない）。
  `sim.abilities.is_implemented` で実装済み能力に限る（`_compute_invest` と同じ絞り）。
- (ii) **能力の盤面価値**＝`agent._ability_value(tgt, 能力, None, view)`（既存の語彙）を
  `/(1+need)` で割る（`_compute_invest` と同じ正規化＝近い解禁を優先）。
- (iii) **友好無視の疑い**＝belief の役職周辺確率で、友好無視/絶対友好無視を持つ役職
  （`_IGNORE_ROLES`＝`engine.data.ROLE_CLAUSE_ABILITY` 由来）の合計 `p_ign` を割引
  `×(1 - _B275_IGNORE_COEFF × p_ign)`。疑いが濃い対象への投資は空振りやすい。

∴ ご神木（友好能力なし＝`goodwill_abilities_of == []`）の到達可能価値は 0
＝seal_cat の穴（列挙順先頭のご神木へ投資し続ける）を第2キーが直接ふさぐ。
"""
from __future__ import annotations

from engine.data import goodwill_abilities_of

from .heuristic_protagonist import _IGNORE_ROLES

GOODWILL_CARDS = ("友好+2", "友好+1")


def reach_value(agent, view, tgt: str) -> float:
    """対象 `tgt` の**到達可能価値**（第2キー・大きいほど良い投資先）。

    既存の単一ソースだけで合成する（docstring の (i)(ii)(iii)）。
    判定不能（belief 無し等）は割引しない側に倒す＝fail-ignore。
    """
    from sim.abilities import is_implemented
    cv = agent._alive(view, tgt)
    if not cv:
        return 0.0
    base = 0.0
    for ab in goodwill_abilities_of(tgt) or []:
        if not is_implemented(tgt, ab["name"]):
            continue
        need = ab["hearts"] - cv["goodwill"]
        if need <= 0:
            continue      # もう使える＝追加友好の到達価値ゼロ（_compute_invest と同義）
        if not agent._b178_reachable_this_loop(view, need):
            continue      # (i) このループ中に閾値へ届かない（rules/00:86）
        base = max(base, agent._ability_value(tgt, ab["name"], None, view)
                   / (1.0 + need))                       # (ii)
    if base <= 0.0:
        return 0.0
    p_ign = 0.0
    bel = getattr(agent, "_belief", None)
    if bel is not None:
        marg = bel.role_marginals().get(tgt, {})
        p_ign = min(1.0, sum(marg.get(r, 0.0) for r in _IGNORE_ROLES))
    return base * (1.0 - float(agent._B275_IGNORE_COEFF) * p_ign)   # (iii)


def goodwill_tiebreak(agent, view, options, best, score):
    """`best`（`max(options, key=score)` の勝者）の**完全同点集合**を第2キーで解く。

    返り値＝差し替え後の option（差し替え無し＝None）。
    ★同点集合は「best と**同一カード**・キャラ対象・score が**完全等値**」だけ
    ＝カード種は絶対に動かない・非同点席では集合が {best} になり必ず None。
    """
    if best.get("card") not in GOODWILL_CARDS \
            or best.get("target_kind") != "character":
        return None
    s_best = score(best)
    tied = [o for o in options
            if o.get("card") == best["card"]
            and o.get("target_kind") == "character"
            and score(o) == s_best]
    if len(tied) < 2:
        return None
    # ★max は同値なら先頭を返す＝第2キーまで同点なら tied[0]（＝best＝列挙順の
    #   従来勝者。best より前の option は score < s_best ゆえ tied に入らない）。
    top = max(tied, key=lambda o: reach_value(agent, view, o["target"]))
    return None if top is best else top
