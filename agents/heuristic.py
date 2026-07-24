"""ヒューリスティック脚本家bot（M3→v2強化 2026-07-05→v3強化 2026-07-06）。

決定的スコアリングbot。ルールはハードコードせず、シミュレータが渡す合法手だけから選ぶ。

勝ち筋（view の rule_y / 配役 / 事件から自動判定）:
1. キーパーソン殺害：キーパーソンに暗躍≥2＋同エリアのキラーでターン終了殺害。
2. ボード暗躍≥2（学校/神社/ボードX/病院の事件）。
3. 主人公殺害（病院の事件・キラー暗躍4・メインラバーズ）。

v2の強化（対belief主人公・対人間）:
- ★暗躍禁止で止まらない経路を主軸に：クロマクもキーパーソンへ寄せ、脚本家能力フェイズの
  暗躍+1（行動解決外＝暗躍禁止無効）でキーパーソンを担ぐ。主人公の毎ターンの暗躍禁止を無力化。
- ★世界線を絞らせない（情報衛生）:
  * 賢い拒否：開示/犯人/ルール系や勝ち筋を壊す能力だけ拒否し、無害な能力は通す
    （拒否＝友好無視バレ＝配役情報。人間・将来のbelief拡張への対策）。
  * 無駄なミスリーダー撃ちをしない（不安+1能力の使用は「同エリアにミスリーダー」の位置情報）。
  * 不安のばらまき：手札の不安+1は犯人以外にも撒いて犯人特定を困難に（カードは役職を明かさない）。
- 友好禁止で脅威キャラ（暗躍除去・開示持ち）の友好を止め、友好能力の解禁を遅らせる。
- 殺人事件の対象：脅威キャラを優先で殺し、自分の未来の犯人は殺さない（事件を維持）。

v3の強化（人間の手筋の翻訳・ユーザー知見 2026-07-06）:
- ★残手数会計（moves-to-win）：勝ち筋ごとの残コスト（あと何個の暗躍が要るか）と
  残供給（カード≒2/ターン）を比べ、届く経路にだけカードを出す（終盤の無駄撃ち防止）。
- ★二正面圧力（カバーシナリオ）：2本が同時に賄えるなら両方生かす。主人公の暗躍禁止は
  1枚/ターン＝2経路を同時には塞げない（1本に絞ると相手のトリアージ一点読みで詰む）。
- ★迷ったら不安+1は臨界の低いキャラへ：臨界以上のキャラは事件のたびに公開の候補者
  リスト（eligible）へ載る＝「霧」要員として犯人特定を長く妨げる。ウイルス自爆は回避。
- ★帰属不能の撃ち（医者同席のミスリーダー撃ち＝医者能力と区別不能で情報コスト0）／
  クロマク能力は2人きりの場では少し控える（顔ぶれ公開で絞られる）。
"""

from __future__ import annotations

import random

from engine.board import AREAS
from engine.data import (ROLE_CLAUSE_ABILITY, forbidden_of, goodwill_abilities_of,
                        unrest_threshold_of)
from engine.models import MOVE_CARDS


def _incident_nullified_by_immortal(inc_name: str, culprit_role: str) -> bool:
    """★A-38（2026-07-19）：この事件の効果が犯人の**不死**で証明可能に無効か。

    自殺（60:60）＝犯人自身を死亡させる事件＝犯人が不死（タイムトラベラー等）なら死なない＝
    効果ゼロ（60:A26「事件は発生したが何も起きなかった」）。＝mmがこの犯人に不安を積んで臨界に
    届かせても打点は0＝純粋な無駄手（かつ発火時に death_prevented で不死＝役職が公開露呈する
    情報リーク）。実測＝棋譜 beginner_BTX seed15 L2D3（妹=TT・自殺犯人=妹に不安3投資）。
    ＝A-25（臨界0への霧）と同型の「透明に死んだ投資」を、不死×自殺の会計として一般化。
    """
    return inc_name == "自殺" and ROLE_CLAUSE_ABILITY.get(culprit_role) == "不死"
from sim.reference import CHARACTER_ATTRIBUTES

_ANRYAKU_VALUE = {"暗躍+1": 1, "暗躍+2": 2}


def _ability_class_target_alive(user: str, ability: str, chars: dict) -> bool:
    """★A-23（2026-07-16）：対象クラスが限定された友好能力に、生存する対象が居るか。

    能力名が「学生の…」なら対象は**学生属性のキャラ**（教師『学生の不安操作/学生の役職開示』＝
    同エリアの学生／男子学生・女子学生『学生の不安除去』＝同エリアの**他の**学生）。該当者が
    誰も生きていなければ能力は空撃ちすらできない＝脅威になりえず、友好禁止は純粋な無駄手。

    実測例（FB3 beginner_FS seed13・キャストの学生は男子学生ただ1人）：
      - L1D2/L1D3：男子学生の死後も教師に友好禁止（対象の学生が全滅済み）
      - L2D1/L3D1：男子学生に友好禁止（「他の学生」が脚本上そもそも存在しない＝永久に対象ゼロ）
    エリア一致までは要求しない（移動で合流しうる＝健全側に保守的）。存在だけを見る。

    ★FI-2（2026-07-18）：判定本体を engine.data.ability_class_target_alive に**共通化**（主人公の
    B-24 対象存在ゲートと実装を一本化＝二重定義を解消）。挙動は不変（同一ロジック＝学生判定は
    is_student≡CHARACTER_ATTRIBUTES『学生』で等価・test が固定）。
    """
    from engine.data import ability_class_target_alive
    return ability_class_target_alive(
        user, ability, [n for n, c in chars.items() if c.get("alive")])
# 通ると脚本家が不利になる友好能力のキーワード（拒否候補）
_DANGEROUS_ABILITY_HINTS = ("開示", "犯人", "ルール", "戻す", "護衛", "不死",
                            "蘇生", "肩代わり", "移し替え")

# ---------------------------------------------------------------------------
# チューニング可能なスコア重み（Bonapeti・2026-07-09）。
# 既定値＝これまで手調整してきた現行値（＝既定のままなら挙動は完全に不変）。
# arena/tune.py がベンチマーク（ループ数）を目的関数にこの重みを機械最適化する。
# ★ゲームルール由来の数（暗躍2でKP殺害可・暗躍4で主人公殺害・友好+2/T等）はルールなので
#   ここに入れない。入れるのは「手の相対的な好み」を表す重みだけ。
# ---------------------------------------------------------------------------
MM_PARAMS: dict[str, float] = {
    # _analyze：相手モデル（guarded検知時の実効コスト増）
    "cost_board_guarded": 2.0, "cost_killer_guarded": 2.0,
    # _score_set (1) ゴールボード暗躍
    "set_board_locked_over": 1.0, "set_board_over_funded": 55.0,   # ★A-21：達成済みゴールへの
    #   over-pile は filler タイブレーク最下位（移動系 set_move_stray=3 未満）＝意味のある手が
    #   あれば譲る。他候補も全て無価値な時だけ選ばれる（必置3枚のfiller用途は保持）。
    "set_board_over_unfunded": 22.0, "set_board_base": 100.0,
    "set_board_unfunded": 30.0, "set_val_mult": 10.0,
    "set_board_decoy": 40.0,  # 偽装ボード（別ルールY候補）への捨て暗躍＝帰属を割るカバー（M1b）
    "set_board_hold": 25.0,   # 偽装が育つ前の本命完成を保留（先に満ちた筋=本命とバレる・M1b）
    # ★A-57：本命貫通手段の優先（§1i）。覆われ続けている本命盤（board_guarded）には、打ち消し
    #   前提の+1連打ではなく**一撃で条件を満たす+2**を使う。set_board_hold(25)を上回る幅にして
    #   「偽装完成前の保留」より貫通を優先させる（実測＝+1が110・+2が95で+2が温存され続けた）。
    #   既定1で有効・0で旧挙動へ完全復帰＝掃引/デバッグ用の切替口。
    "breakthrough_priority": 1.0,
    "breakthrough_bonus": 30.0,
    # ★A-54：暗躍+2（1/loop の切り札）を「通れば実利得になる先」以外に切るのを抑止する規律。
    #   +2は伏せられているだけで「必ず抑えさせる」拘束力を持つ（§1i 未使用+2の脅威価値）＝
    #   実利得ゼロの先へ切ると、切り札と拘束力を同時にゼロ価値で手放す（本棋譜D1＝バックアップ
    #   無しで囮盤へ+2）。非正当先の+2からこの値を引いて +1相当以下へ落とす（＝+1で足りる先へ
    #   散らす or 温存する動機。下限は切らない＝他が全て無価値なら依然置く）。既定1で有効・
    #   0で旧挙動へ完全復帰。判定材料は自明情報（ゴール盤・役職・事件・カルティスト/クロマク位置）。
    "plus2_discipline": 1.0,
    # ★A-54b：例外(4)＝このループの勝ちが**確定級（取り消し不能）**なら +2 の囮/布石転用を解禁。
    #   述語（ユーザー承認 2026-07-24・盤面事実のみ＝誤発動なし）＝①本命ゴール盤が充足済み×
    #   除去者不在 ②フレンド死亡済み（A-49確定弁と同一） ③僕と契約のKPに暗躍≥2（A-54b側のみ）。
    #   既定1で有効・0で A-54 単体（例外4なし）へ戻す＝掃引/デバッグ用の切替口。
    "plus2_win_certain_gate": 1.0,
    "plus2_waste": 22.0,
    # (2) キーパーソン暗躍
    "set_kp_done": 30.0, "set_kp_blocked": 25.0, "set_kp_base": 90.0,
    "set_kp_unfunded": 40.0,
    # (2b) キラー自己暗躍
    "set_killer_done": 15.0, "set_killer_blocked": 95.0,
    "set_killer_funded": 85.0, "set_killer_unfunded": 10.0,
    # (3) 不安の注ぎ先
    "set_cool_locked": 65.0, "set_unrest_locked": 2.0,
    "set_unrest_today": 60.0, "set_unrest_today_far": 12.0,
    "set_unrest_future": 40.0, "set_unrest_future_far": 8.0,
    "set_unrest_virus": 3.0, "set_unrest_sat": 2.0,
    # ★A-25：臨界0（黒猫）への不安+1＝公開情報上ブラフ価値ゼロ＝filler最下位（A-21の
    #   set_board_locked_over と同帯・set_move_stray=3 未満）＝意味のある手が必ず優先される。
    "set_unrest_zero_th": 1.0,
    # ★A-56：事件の価値会計（A-18打点会計の拡張）＝「届くか」だけでなく「発生させて何を得るか」。
    #   ①incident_arith_strict＝**当日**の到達可能性はミスリーダーの同居が確定している時だけ
    #     2/ターンを見込む（mm_rate は存在するだけで2＝楽観的すぎた）。未来日は寄せる時間が
    #     あるので従来どおり（健全側）。②incident_payoff_gate＝発生しても利得が出ない事件
    #     （遠隔殺人で KP/フレンドが eligible に近づいていない）には不安を注がない。
    #   どちらも既定1＝有効。0で旧挙動へ復帰＝掃引/デバッグ用の切替口。
    "incident_arith_strict": 1.0,
    "incident_payoff_gate": 1.0,
    "set_noise_base": 18.0, "set_noise_reach": 8.0, "set_noise_low": 4.0,
    "set_noise_immobile": 12.0,  # 移動不可キャラへの伏せ札は「ほぼ不安」と読まれる＝減点（テスター知見）
    "set_noise_leak": 8.0,   # 不発予定日に臨界へ届かせる＝「臨界者は犯人でない」の消去を与える漏れ
    "set_move_witness": 45.0,  # キラー殺害が近い時、KPエリアへ第三者を寄せる＝偽キラー候補（目撃者）
    # (4) 移動
    "set_move_killer": 50.0, "set_move_kuromaku": 48.0, "set_move_stray": 3.0,
    # ★A-52：**充足済みの勝ち筋の最後の1ピース**＝KP暗躍が既に2以上ある状態でキラーをKPの
    #   エリアへ寄せる移動（これ1枚でターン終了の【任意】KP殺害＝即敗北+ループ終了が成立する）。
    #   検死A-50＝殺人計画のL1敗局 7/7 で同エリアが一度も成立せず・4/7 は移動札ゼロ。真因は
    #   採点で、実測ダンプ（random_FS s9）では毎日 `暗躍+1→キラー`(95=killer4経路・あと3枚必要)が
    #   `移動→キラー`(50=あと1枚で勝ち)に勝っていた＝**残り手数を見ずに素点で比べていた**。
    #   ∴ 完成手は killer4 の積み増し(95)より上に置く。0で旧挙動へ完全復帰。
    #   掃引実測（B-45 Step2c land 後の正典＝3日127/2.385 L1=34・5日64/3.086 の上で再測）＝
    #   3日はどの値も同一（**L1敗局 34→32**・mean 2.385→2.400・防衛不変）だが、5日は
    #   **96 だけが集計非退行**（mean 3.086 のまま／108 は 3.057 へ落ちる）＝96を採用。
    #   ★既知の弱点＝**タイミングを見ない**（充足した瞬間に寄せる）。5日 random_BTX s14 の検死＝
    #   旧挙動は暗躍+1でキラーを拘束し続けD4に不意打ちで寄せて成功、A-52は早く寄せるため主人公に
    #   毎ターンKPを逃がされ空振り＝バックログ§1i「本命は読まれていない方法で急に通す」に反する。
    #   完成手の**不意打ち規律**（遅らせる/移動禁止と合わせる）は follow-up。
    "set_move_kill_complete": 96.0,
    # ★A-24'：board_x の担い手（クロマク/ウィッチ）を board_x へ戻す移動＝暗躍禁止で止まらない
    #   供給線の維持。移動帯と同格＝ゴール盤への暗躍等より良い手があればそちらに譲る（固執しない）。
    "set_move_boardx_return": 50.0,
    "set_move_block_death": 54.0, "set_move_sk_approach": 58.0,
    "set_move_sk_evict": 56.0, "set_move_misleader": 52.0,
    "set_move_cultist_guarded": 56.0, "set_move_cultist": 47.0,
    # ★A-55：カルティスト搬送（§1i「静かな盤移動」＝A-42の足し算side）。カルティストが
    #   ゴール盤エリアに立てば、その盤への暗躍禁止を**ルールとして無効化**できる＝覆いを
    #   突破する唯一の道具（opponent-model不要＝軸予測でなくルールの無効化）。
    #   ★棋譜（封印seed0）の実測＝D1に `移動斜め→従者`(47) が候補にありながら
    #   `移動斜め→異世界人`(52) に負け、**斜めは1/loop**なのでD2以降は従者を神社へ1手で運ぶ
    #   手段が消滅＝3日間一度も近づけなかった（＝移動札の資源計画の失敗）。
    #   ∴ ゴール盤経路が生きている（funded）なら、実績（board_guarded）を待たずに
    #   ML移動(52)・キラー寄せ(50)より上へ。3日級では実績が溜まる頃には手遅れになる。
    #   ★値の順序＝guarded(56) > funded(53) > ML移動(52) > キラー寄せ(50) > 従来(47)。
    #   実績ありの方を強く（53/55/58は実測で完全に同一挙動＝52を超えた時点で決まる）。
    "set_move_cultist_funded": 53.0,
    # (5) 友好禁止
    "set_gwban_hot": 35.0, "set_gwban_light": 22.0,
    "set_gwban_defer": 15.0,  # 事件の仕込み（不安pump）が残る間は友好禁止を1手待つ（テスター知見）
    # 経路選択（M2）：主人公beliefでの「バレ度」を勝ち筋の実効コストに加算する係数
    #   （カード枚数換算＝完全にバレた筋は path_known_cost 枚ぶん高い扱い）。
    "path_known_cost": 2.5,
    # 戦術バリエーション（M3）：過去ループと同じ供給路でのKP暗躍に減点（同じ戦術で2回勝てない）
    "tactic_repeat": 20.0,
    # _score_ability
    # ★役職バレコスト（情報秘匿・テスター知見P1）。本番＝belief.reveal_cost（AIB・B3）で
    #   「この手で主人公の可能世界が何%畳まれるか」collapse_frac を測り ab_reveal_weight を掛ける
    #   （＝AIBコメントの「利得 − λ·reveal_cost」）。belief構築不能時のみ crowd 代理へ退避
    #   （ab_sparse_penalty × max(0, ab_reveal_crowd − crowd)）。
    "ab_pass": 1.0, "ab_reveal_weight": 25.0,
    "ab_sparse_penalty": 9.0, "ab_reveal_crowd": 4.0,  # フォールバック代理（belief構築不能時のみ）
    "ab_anyaku_board": 100.0, "ab_anyaku_kp": 90.0,
    "ab_anyaku_killer_blocked": 92.0, "ab_anyaku_killer": 88.0,
    "ab_anyaku_kp_late": 60.0, "ab_anyaku_other": 20.0,
    # ★A-48：クロマクのキャラ暗躍で「勝ち筋を前進させない発動」を filler化する情報コストゲート
    #   （前進ゼロの発動＝少人数エリアでソースがクロマク確定＝無償の役職リーク）。
    #   既定1＝有効（land済）。0にすると旧挙動へ戻る＝掃引/デバッグ用の切替口。
    "kuromaku_progress_gate": 1.0,
    # ★A-42：カルティストの暗躍禁止無視（＝暗躍を通す任意発動）で「勝ち筋を前進させない無視」を
    #   見送る情報コストゲート。無視すると「このエリアにカルティストが居る」がpresentつきで公開＝
    #   少人数エリアほど確定リーク（観察＝前進ゼロの無視28回／うち少人数22回＝無償のtell）。
    #   前進ゼロなら暗躍を止めても損はない＝見送れば暗躍禁止が実効になりカルティストを隠せる。
    #   既定0＝常に無視（旧挙動・bit-for-bit）。1で情報衛生を有効化。A-48/A-49と同じ引き算。
    "cultist_ignore_gate": 1.0,
    # ★A-49：このループの帰結が既に確定した後の【任意】発動（キラーの主人公殺害・メインラバーズ・
    #   TT敗北・キラーのKP殺害）を見送るゲート。確定後の発動は**このループの結果を変えず**、
    #   発動キャラの役職だけを無償で献上する（次ループ／BTX最後の戦いに響く）。
    #   既定1＝有効（land済）。0にすると旧挙動へ戻る＝掃引/デバッグ用の切替口。
    "loop_decided_optional_gate": 1.0,
    # 帰結確定後にリークする【任意】発動のスコア（pass=0 より下＝見送りが選ばれる）。
    "te_decided_leak": -1.0,
    "ab_rumor_offgoal": 0.5,  # 不穏な噂を利得なきボードへ＝ルールX開示コストだけ払う悪手（テスター知見）
    "ab_cool_locked": 70.0, "ab_weak": 0.5,
    "ab_unrest_today": 80.0, "ab_unrest_today_far": 4.0,
    "ab_unrest_future": 35.0, "ab_unrest_future_far": 3.0,
    "ab_unrest_cover": 22.0,
    # _score_incident
    "inc_board_goal": 100.0, "inc_board_other": 10.0,
    "inc_kp": 100.0, "inc_friend": 70.0, "inc_future_culprit": 1.0,
    "inc_threat": 45.0, "inc_other": 5.0,
}


def _single_move_card(src: str, dst: str) -> str | None:
    """src→dst へ1枚で移せる移動カード名。同エリア/到達不能なら None。"""
    sx, sy = AREAS[src]
    dx, dy = AREAS[dst]
    toggle = (sx ^ dx, sy ^ dy)
    return {(1, 0): "移動←→", (0, 1): "移動↑↓", (1, 1): "移動斜め"}.get(toggle)


_CARD_TOGGLE = {"移動←→": (1, 0), "移動↑↓": (0, 1), "移動斜め": (1, 1)}


def _move_dest(area: str, card: str) -> str | None:
    """area のキャラに card を置いたときの行き先エリア名。"""
    t = _CARD_TOGGLE.get(card)
    if not t or area not in AREAS:
        return None
    tx, ty = t
    ax, ay = AREAS[area]
    want = (ax ^ tx, ay ^ ty)
    return next((a for a, xy in AREAS.items() if xy == want), None)


def _perp_block_card(area: str, board: str) -> str | None:
    """area のキャラを board へ絶対に入れないため、脚本家が置くべき移動カード。

    ★移動トグル封鎖（ユーザー知見 2026-07-06）：board が area の隣接(1ビット)なら、その
    移動と直交する1ビット移動を置くと、主人公が何を足しても（斜めは持たない）board に届かない。
    board が area の対角(1,1)なら、主人公は斜めを持たないので放っておいても届かない＝ None。
    """
    if area == board or area not in AREAS or board not in AREAS:
        return None
    ax, ay = AREAS[area]
    bx, by = AREAS[board]
    t = (ax ^ bx, ay ^ by)
    if t == (1, 1):
        return None  # 対角＝主人公は届かない（斜め不所持）＝ブロック不要
    perp = (t[0] ^ 1, t[1] ^ 1)  # 直交する1ビット
    return {(1, 0): "移動←→", (0, 1): "移動↑↓"}.get(perp)


class HeuristicMastermind:
    def __init__(self, seed: int = 0, params: dict[str, float] | None = None):
        """params: MM_PARAMS の部分上書き（チューニング用）。未指定＝従来と同一挙動。"""
        self.rng = random.Random(seed)
        self.p = dict(MM_PARAMS)
        if params:
            unknown = set(params) - set(MM_PARAMS)
            if unknown:
                raise ValueError(f"未知のパラメータ: {sorted(unknown)}")
            self.p.update(params)

    # -- 盤面分析（view から勝ち筋の材料を取り出す） -----------------------

    def _analyze(self, view: dict) -> dict:
        roles = view["roles"]
        chars = {c["name"]: c for c in view["characters"]}
        keyperson = next((n for n, r in roles.items() if r == "キーパーソン"), None)
        killer = next((n for n, r in roles.items() if r == "キラー"), None)
        kuromaku = next((n for n, r in roles.items() if r == "クロマク"), None)
        # ★A-48：KP等のキャラ暗躍を除去/移動できる友好能力を持つキャラが生存しているか
        #   （転校生♡2=他キャラの暗躍1除去／神格♡5=同エリアのキャラ1人の暗躍1除去／鑑識官♡2=カウンター
        #   移し替え）。生存中は「暗躍≥2で充足＝過剰」の前提が崩れる＝除去を1回耐える保険としてKP暗躍3まで
        #   正当（バッファ1）。KB確認済（rules/20・巫女は神社ボード限定・学者は自身のみ＝対象外）。
        kp_anyaku_remover_alive = any(
            chars.get(n, {}).get("alive") for n in ("転校生", "神格", "鑑識官"))
        cultist = next((n for n, r in roles.items() if r == "カルティスト"), None)
        friends = {n for n, r in roles.items() if r == "フレンド"}
        # ★A-49：このループの帰結（主人公敗北）が**不可逆に確定**しているか。健全側＝
        #   「取り消しようがない」2条件だけを確定とみなす（sim/effects.evaluate_loop_end と同一述語）：
        #   (1) フレンドが死亡＝ループ終了時に無条件で敗北（40:130）。死は不可逆＝確実。
        #   (2) 未来改変プラン＋蝶の羽ばたきが発生済み＝発生した事実がそのまま敗北条件。同じく不可逆。
        #   ★除外（＝疑わしきは従来どおり発動）：ボード暗躍≥2系（守るべき場所／封印されしモノ／
        #     ボードX）と 僕と契約のKP暗躍≥2 は、主人公の友好能力で**暗躍を除去されうる**＝
        #     ループ終了時まで確定しない。ここを確定扱いにすると勝ち筋を自分で捨てる。
        loop_defeat_certain = any(
            not chars.get(n, {}).get("alive", True) for n in friends)
        if not loop_defeat_certain and view.get("rule_y") == "未来改変プラン":
            loop_defeat_certain = any(
                e.get("event") == "incident" and e.get("name") == "蝶の羽ばたき"
                and e.get("occurs") and e.get("loop") == view.get("loop")
                for e in view.get("history", ()))
        # ★シリアルキラー（妄想拡大ウイルスのSK化含む）と、SKが殺せば勝ちに繋がる標的
        #   （キーパーソン＝殺害でループ終了／フレンド＝ループ終了時敗北）。ユーザー知見 2026-07-06。
        sk = next((n for n, r in roles.items() if r == "シリアルキラー"), None)
        tt = next((n for n, r in roles.items() if r == "タイムトラベラー"), None)
        killable = ({keyperson} | friends) - {None}

        goal_boards: set[str] = set()
        ry = view["rule_y"]
        if ry == "守るべき場所":            # FS：学校≥2
            goal_boards.add("学校")
        elif ry == "封印されしモノ":         # BTX：神社≥2
            goal_boards.add("神社")
        elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") and view.get("rule_y_board_x"):
            goal_boards.add(view["rule_y_board_x"])  # FS/BTX：ボードX（クロマク/ウィッチ初期）≥2
        # ★A-18：病院ゴールボード（病院の事件で病院暗躍≥2＝主人公殺害）は reachable_culprits の
        #   算出後に、事件日ゲート＋打点会計つきで追加する（下・従来はここで無条件に恒常ゴール化し、
        #   届かない/事件日が過ぎた病院に毎ループD3暗躍を無駄置きしていた）。

        day = view["day"]
        today_culprit = next((inc["culprit"] for inc in view["incidents"]
                              if inc["day"] == day), None)
        culprits_all = {inc["culprit"] for inc in view["incidents"]}
        future_culprits = {inc["culprit"] for inc in view["incidents"] if inc["day"] > day}
        misleader = next((n for n, r in roles.items() if r == "ミスリーダー"), None)

        # ★今ターン死亡するボード（病院の事件が発生＝病院暗躍≥1で病院全員死亡）。
        #   生かしたい自陣キャラ（未来の犯人・キラー・SK・クロマク・ミスリーダー）を入れないよう
        #   移動封鎖する材料（ユーザー知見 2026-07-06）。
        death_board_today = None
        for inc in view["incidents"]:
            if inc["day"] == day and inc["name"] == "病院の事件":
                cu = chars.get(inc["culprit"])
                cth = unrest_threshold_of(inc["culprit"])
                bh = view.get("board_anyaku", {}).get("病院", 0)
                if (cu and cu["alive"] and cth is not None and cu["unrest"] >= cth
                        and bh >= 1):
                    death_board_today = "病院"
        protect = (set(future_culprits) | {killer, kuromaku, misleader, sk}) - {None}

        # ★事件発生の打点会計（ユーザー知見 2026-07-06）：脚本家は犯人に不安を1/ターン
        #   （ミスリーダーを犯人と同居させれば2/ターン）しか積めない。現在不安＋打点×残ターンが
        #   臨界に届かない事件は「困難」＝そこへ不安+1やミスリーダー能力を注いでも無駄。
        #   届く事件（reachable）にだけ資金を出し、届かないものは霧まき等へ資金を回す。
        #   pump_targets＝届く犯人を事件日の近い順に（ミスリーダーの寄せ先・不安の照準に使う）。
        mm_rate = 2 if misleader else 1
        reachable_culprits: set[str] = set()
        pump_targets: list[tuple[int, str]] = []
        for inc in view["incidents"]:
            cn, cd = inc["culprit"], inc["day"]
            if cd < day:
                continue
            # ★A-38：事件効果が犯人の不死で証明可能に無効（自殺×不死犯人）なら、臨界に届かせても
            #   打点0＝この犯人に不安を積む動機が無い＝reachable に入れない（打点会計から除外）。
            #   ＝mmが「発火しても何も起きない＋不死露呈する」事件のために不安を投資するのを止める。
            if _incident_nullified_by_immortal(inc["name"], roles.get(cn, "")):
                continue
            # ★A-56②：**発生しても利得が出ない事件**には不安を注がない（A-38「不死で無効」と同型）。
            #   遠隔殺人＝「暗躍が2つ以上のキャラの中から任意の1人を死亡させる」（40:152/50:209）＝
            #   殺して得があるのは KP（即敗北）か フレンド（ループ終了時敗北）だけ。利得対象が
            #   1人も eligible（暗躍≥2）に近づいていない＝発生させても「何も起きなかった」になる。
            #   棋譜（封印seed0）D3＝eligible[従者,手先] にフレンドもKPも入らず、カード2＋能力2の
            #   計4手を投じて効果ゼロ。★健全側＝暗躍が1つでも乗っていれば「積み増しで届く」＝除外しない。
            if inc["name"] == "遠隔殺人" and self.p["incident_payoff_gate"]:
                _payoff_targets = (friends | {keyperson}) - {None}
                if not any(chars.get(t, {}).get("alive")
                           and chars.get(t, {}).get("anyaku", 0) >= 1
                           for t in _payoff_targets):
                    continue
            cc = chars.get(cn)
            th = unrest_threshold_of(cn)
            if not cc or not cc["alive"] or th is None:
                continue
            if th == 0:                       # 臨界0（黒猫）は常に発生＝常に「届く」
                reachable_culprits.add(cn)
                pump_targets.append((cd, cn))
                continue
            turns = cd - day + 1              # 今日を含む、事件日までに積めるターン数
            # ★A-56①：**当日**（turns==1）の到達可能性は今日の配置で確定する＝ミスリーダーの
            #   2/ターンは「犯人と同エリアに居る」時しか見込めない（mm_rate は存在するだけで2＝
            #   楽観的すぎた）。棋譜（封印seed0）D2＝女子学生の臨界3に対し朝0・ML別エリアで
            #   実際は+1しか積めず「1<3」の確定ミス投資だった。未来日（turns≥2）はミスリーダーを
            #   寄せる時間があるので従来どおり mm_rate を使う（健全側＝可能性を残す）。
            rate = mm_rate
            if turns == 1 and misleader and self.p["incident_arith_strict"]:
                mlc = chars.get(misleader)
                if not (mlc and mlc.get("alive") and cc.get("area")
                        and mlc.get("area") == cc["area"]):
                    rate = 1
            if cc["unrest"] < th and cc["unrest"] + rate * turns >= th:
                reachable_culprits.add(cn)
                pump_targets.append((cd, cn))
        pump_targets.sort()

        # ★A-18：病院ゴールボード（病院の事件で病院暗躍≥2＝主人公殺害）は「これから発生しうる」
        #   時だけ有効化。(i)事件日ゲート inc.day>=day（loop_solver:155 の inc.day>=state.day 先例）／
        #   (ii)打点会計：犯人が臨界に届く（reachable）or 既に臨界以上＝発生見込みがある時だけ。
        #   従来は無条件で恒常ゴール化し、届かない/事件日が過ぎた病院に毎ループ暗躍を無駄置きしていた。
        for inc in view["incidents"]:
            if inc["name"] != "病院の事件" or inc["day"] < day:
                continue
            cu = chars.get(inc["culprit"])
            cth = unrest_threshold_of(inc["culprit"])
            if cu and cu["alive"] and cth is not None and (
                    inc["culprit"] in reachable_culprits or cu["unrest"] >= cth):
                goal_boards.add("病院")
                break

        # ★事件の発火見込み（今日）＝霧（偽犯人候補）の日付設計に使う（ユーザー知見 2026-07-09）。
        #   発火する日の霧は eligible（公開候補者リスト）に紛れて犯人特定を妨げる＝有効。
        #   発火しない予定日に臨界の者を立てるのは「不発＝臨界だった者は犯人でない」という
        #   消去法を主人公に与える漏れ＝逆効果。
        fires_today = False
        if today_culprit:
            _tc = chars.get(today_culprit)
            _tth = unrest_threshold_of(today_culprit)
            if _tc and _tc["alive"] and _tth is not None and (
                    _tc["unrest"] >= _tth or today_culprit in reachable_culprits):
                fires_today = True

        # 脅威キャラ＝友好能力で勝ち筋を崩す/情報を開示するキャラ（友好禁止・殺人事件の的）。
        # ★threat_hearts＝その脅威能力の必要友好数（最小）。友好禁止の要否判断に使う
        #   （残日数で解禁不能な相手＝ハート0の大物♡5等に無駄撃ちしないため）。
        threats: set[str] = set()
        threat_hearts: dict[str, int] = {}
        # 事件計画の有無（冷却解禁者を脅威と見なす条件に使う。犯人リストは非公開＝自陣情報）。
        has_incident_plan = any(inc["day"] >= day for inc in view["incidents"])
        for n in chars:
            for ab in goodwill_abilities_of(n) or []:
                nm, hearts = ab["name"], ab["hearts"]
                # ★A-5(c) subcase：この能力が今ループ発動可能か。ループ制限つき能力（「第2L以降」＝
                #   イレギュラーの役職開示）は、その前のループでは発動不可＝脅威でない（友好禁止も
                #   友好+も無意味・ユーザー報告：L1のイレギュラー）。
                if "第2" in nm and "以降" in nm and view.get("loop", 1) < 2:
                    continue
                # ★A-23（2026-07-16）：対象クラス（学生等）に生存者が居ない能力は空＝脅威でない。
                #   A-5(c)の「対象存在チェック」の残穴（ナースの臨界キャラ不在は見ていたが、
                #   能力の対象クラス×生存は見ていなかった）＝友好禁止の無駄撃ちの源。
                if not _ability_class_target_alive(n, nm, chars):
                    continue
                is_threat = False
                if "開示" in nm or "犯人" in nm or "ルール" in nm:
                    is_threat = True
                elif "暗躍" in nm and "除去" in nm:
                    if n == "巫女":  # 巫女の除去は神社限定
                        is_threat = "神社" in goal_boards
                    else:
                        is_threat = bool(goal_boards or keyperson)
                elif "不安" in nm and ("除去" in nm or "操作" in nm):
                    # ★冷却解禁者（テスター知見 2026-07-09）：医者（友好2）等の安価な不安除去は
                    #   事件計画の直接対抗手段＝解禁される前に友好禁止で遅らせる価値がある。
                    # ★A-5(c)：ただし「臨界へ届く犯人（reachable）」が居て初めて冷やす対象が生まれる。
                    #   ナースの「不安臨界以上の不安除去」は臨界キャラが居ないと能力が空＝友好禁止で
                    #   止めるものが無い（ユーザー報告：L1D3ナース・臨界キャラ不在）。事件計画があっても
                    #   誰も臨界に届かないなら冷却は無害＝脅威でない。
                    is_threat = has_incident_plan and bool(reachable_culprits)
                if is_threat:
                    threats.add(n)
                    threat_hearts[n] = min(threat_hearts.get(n, 99), hearts)

        # ★キーパーソン防御の先読み：一度キーパーソンが死ぬと（死＝ループ終了効果で公開）、
        #   主人公は以後のループで毎ターン暗躍禁止を当ててくる。実際に2回塞がれた場合も同様。
        #   → どちらかを検知したら、カード暗躍はキラー自身の暗躍4（主人公殺害）経路へ切替。
        kp_kinshi = 0
        kp_died_before = False
        # ★軽い相手モデル（2026-07-07）：暗躍禁止の当て先の実績だけを数える（反応型）。
        #   固定スコアで「賢い相手」を仮定すると受け身な相手に取りこぼす（guard 5%退行の
        #   教訓）＝観測ゼロならデフォルト挙動のまま、実際に2回塞がれた経路だけ切り替える。
        board_kinshi = 0
        killer_kinshi = 0
        for e in view.get("history", []):
            if e.get("event") == "cards_revealed":
                for p in e.get("placements", []):
                    if p.get("card") != "暗躍禁止" or p.get("owner") == "mastermind":
                        continue
                    if p.get("target") == keyperson:
                        kp_kinshi += 1
                    if p.get("target") in goal_boards:
                        board_kinshi += 1
                    if killer and p.get("target") == killer:
                        killer_kinshi += 1
            elif e.get("event") == "death" and e.get("name") == keyperson:
                kp_died_before = True
        kp_blocked = kp_died_before or kp_kinshi >= 2
        board_guarded = board_kinshi >= 2   # 相手はボードをほぼ毎回守る打ち手
        killer_guarded = killer_kinshi >= 2
        # キラー経路が使えない（キラー不在/死亡）なら切替しない＝キーパーソン経路を維持
        if not killer or not chars.get(killer, {}).get("alive", False):
            kp_blocked = False

        # ★勝ち確定＝抑制モード（手練れの知見）：敗北ボードが既に2以上なら勝ちはほぼ固い。
        #   以後は事件の発生を抑えて情報流出（犯人・臨界の手がかり）を防ぐ。
        locked = any(view.get("board_anyaku", {}).get(b, 0) >= 2 for b in goal_boards)

        days_left = max(1, view.get("days_per_loop", view["day"]) - view["day"] + 1)

        # ★残手数会計（moves-to-winの帳簿・ユーザー知見 2026-07-06）：
        #   勝ち筋ごとの「残コスト（あと何個の暗躍が要るか）」と「残供給（カード≒2/ターン。
        #   暗躍+1毎T＋暗躍+2が1/L）」を比べ、届く経路にだけ資金を出す（届かない経路への
        #   カードは無駄＝終盤に especially 効く）。
        #   ★二正面圧力＝カバーシナリオ：2本が同時に賄えるなら両方生かす。主人公の暗躍禁止は
        #   1枚/ターン＝2経路を同時には塞げない（1本に絞ると相手のトリアージ一点読みで詰む）。
        ba = view.get("board_anyaku", {})
        supply = days_left * 2
        path_costs: dict[str, int] = {}
        if goal_boards:
            path_costs["board"] = max(0, min(2 - ba.get(b, 0) for b in goal_boards))
            if board_guarded:
                path_costs["board"] += self.p["cost_board_guarded"]  # 守られ＝実効コスト増
        # ★KP暗躍パス：殺人計画（KP暗躍≥2＋キラーが同エリアでKP殺害）はキラー必須。だが
        #   僕と契約しようよ！は「KP暗躍≥2」だけで即勝ち＝キラー不要（テスター指摘 2026-07-11：
        #   キラー不在の僕と契約でmmがKPに暗躍せずボード暗躍に固執していた。sim/effects.py:574）。
        _contract_win = view.get("rule_y") == "僕と契約しようよ！"   # KP暗躍≥2単独で勝ち
        if keyperson and not kp_blocked and (
                (killer and chars.get(killer, {}).get("alive")) or _contract_win):
            path_costs["kp"] = max(0, 2 - chars[keyperson]["anyaku"])
        if killer and chars.get(killer, {}).get("alive"):
            path_costs["killer4"] = max(0, 4 - chars[killer]["anyaku"])
            if killer_guarded:
                path_costs["killer4"] += self.p["cost_killer_guarded"]
        # ★M2 相手が知らない筋で決める（ユーザー知見 2026-07-09）：主人公のbelief（公開情報
        #   のみ＝神視点不使用）で、各勝ち筋の要役職・ルールがどれだけ特定されているかを測り、
        #   バレ度を実効コストへ加算（バレた筋は守られる＝高くつく）。funded選択が
        #   「安くて・バレてない」筋になる。guarded（暗躍禁止の当て先実績）が2回塞がれて
        #   からの事後反応なのに対し、こちらは推理された時点で先回りで効く。
        belief_base = None
        if path_costs and view.get("history"):
            try:
                from agents.belief import Belief
                belief_base = Belief(
                    [c["name"] for c in view["characters"]],
                    [{"day": i["day"], "name": i["name"]} for i in view["incidents"]],
                    "BTX" if view.get("rule_x2") else "FS")
                belief_base.observe(view["history"])
                w = self.p["path_known_cost"]
                if "board" in path_costs:
                    ry_p = sum(p for (ry, _rxs), p in belief_base.rule_marginals().items()
                               if ry == view["rule_y"])
                    path_costs["board"] += w * ry_p
                if "kp" in path_costs and keyperson:
                    _n, _p = belief_base.most_likely_role("キーパーソン")
                    path_costs["kp"] += w * (_p if _n == keyperson else 0.0)
                if "killer4" in path_costs and killer:
                    _n, _p = belief_base.most_likely_role("キラー")
                    path_costs["killer4"] += w * (_p if _n == killer else 0.0)
            except Exception:  # noqa: BLE001  belief不能（拡張キャスト等）＝補正なしで続行
                belief_base = None

        _ranked = [p for p, c in sorted(path_costs.items(), key=lambda kv: kv[1])
                   if c <= supply]
        funded: set[str] = set(_ranked[:1])
        if (len(_ranked) >= 2
                and path_costs[_ranked[0]] + path_costs[_ranked[1]] <= supply):
            funded.add(_ranked[1])

        # ★勝ち筋の曖昧性分類（カバーストーリー統一モデル・ユーザー共同設計 2026-07-09）：
        #   評価敗北系（board＝ボード条件。病院KP死も同日複数死で紛れる＝boardに含む）＝
        #   ループ終了時に他の評価敗北と区別がつかない＝カバー並行育成・漏洩抑制の対象。
        #   効果終了系（kp=キラーKP殺害・killer4=主人公殺害）＝宣言カテゴリで即バレ
        #   （KP死はターン終了・最終日でも区別がつく＝ユーザー確認済）＝カバー無意味・
        #   効率＋早撃ちが正（早期ループ終了は主人公の観測日数を奪う情報兵糧攻め）。
        ambiguous_paths = {p for p in path_costs if p == "board"}

        # ★キラーによるKP殺害が近い（暗躍1以上・キラー生存・切替前）＝殺害時の present に
        #   第三者を残す価値（偽キラー候補＝殺害は自己開示でもキラーの特定は防げる）。
        kp_kill_near = bool(
            keyperson and killer and chars.get(killer, {}).get("alive")
            and not kp_blocked
            and chars.get(keyperson, {}).get("anyaku", 0) >= 1)

        # ★M3 ループ間の戦術バリエーション（ユーザー知見 2026-07-09）：手練れ相手に同じ戦術で
        #   2回は勝てない。過去ループでKP殺害を決めた際の暗躍供給路を推定し、同じ供給路に減点＝
        #   対策される前に先回りで変える（例: L1カード不意打ち→L2は噂+クロマク→L3はカルティスト
        #   同伴カード）。供給路の推定＝殺害ループの能力由来KP暗躍回数（history・公開）：
        #   2以上＝能力戦術／0＝カード戦術（殺害には暗躍2が要るので残りはカード由来）。
        kp_used_tactics: set[str] = set()
        if keyperson:
            _kill_loops = {e.get("loop") for e in view.get("secret_log", [])
                           if e.get("event") == "loop_end"
                           and "キーパーソン" in str(e.get("reason", ""))}
            for _lp in _kill_loops:
                _n_ab = sum(1 for e in view.get("history", [])
                            if e.get("loop") == _lp and e.get("event") == "anyaku"
                            and e.get("phase") == "mastermind_ability"
                            and e.get("target") == keyperson and e.get("delta", 0) > 0)
                if _n_ab >= 1:
                    kp_used_tactics.add("ability")
                if _n_ab < 2:
                    kp_used_tactics.add("card")

        # ★カバーストーリー＝偽装ボード（M1b・ユーザー共同設計 2026-07-09）：本命が評価敗北系
        #   （board）のとき、別のルールY候補に見えるボードを序盤から並行で育てる。ループ終了時に
        #   両ボード≥2なら、主人公はどのルールYで負けたか帰属できない（評価敗北同士のみ有効＝
        #   宣言カテゴリの制約）。捨て暗躍は主人公の暗躍禁止/除去を空費させる罠も兼ねる。
        #   選定＝ルール連想の強い順（BTX=神社[封印]／FS=学校[守るべき場所]→他）。病院は
        #   病院の事件で実効果が出るため偽装には使わない（予定があるなら本物の第2経路）。
        decoy_board = None
        decoy_funded = False
        _setname = "BTX" if view.get("rule_x2") else "FS"
        _prefs = ("神社", "学校", "都市") if _setname == "BTX" else ("学校", "神社", "都市")
        # ★DP-2 2M-3 Stage 1（2026-07-20）＝**保留**（配線しない）。理由を残す：
        #   「decoy_board を評価器（cs_plan.choose_cover_story）の選択に置換する」は
        #   **原理的に成立しない**ことが実装中の検証で判明した。
        #   偽装ボードは「この脚本では**負け筋でない**板」＝捨て暗躍（現行は _prefs で
        #   ゴールでない板を選ぶ）。一方 evaluate_tree_mm の live ノードは「**実在する**勝ち筋」。
        #   ボード敗北ノードは各々が単一の rule_y に gate されており（board.school=守るべき場所／
        #   board.shrine・jaki.seal=封印／board.x=復讐者・爆弾X／board.kp_anyaku=僕と契約）、
        #   rule_y は脚本に1つ＝**非病院ボードを指す live ノードは常にゴール板そのもの1つだけ**
        #   （実測：異なり数は 0 or 1／143局面。0件も73件）。
        #   ＝live から選ぶ限り「ゴールでない板」は決して出てこない＝board_only decoy ≡ ∅。
        #   実測でも decoy 変化 0/146 局面＝ベンチ一致は「効果ゼロ」の別名だった。
        #   ★正しい定式化は Stage 1' として再提案（下記2案）＝FableA/ユーザー裁定待ち。
        if "board" in funded and goal_boards:
            decoy_board = next((b for b in _prefs if b not in goal_boards), None)
            if decoy_board is not None:
                _dcost = max(0, 2 - ba.get(decoy_board, 0))
                # 予算：本命の残コスト＋偽装の残コストが残供給に収まるときだけ育てる
                decoy_funded = path_costs.get("board", 99) + _dcost <= supply
        # ★KP勝ち筋のカバー（ユーザー知見 2026-07-12）：本命がKP暗躍（僕と契約＝KP暗躍≥2単独／
        #   殺人計画＝KPをキラーが殺害＝KP暗躍≥2必須）のとき、主人公に「板で来る」と誤認させる
        #   偽装ボードを並行育成し、暗躍禁止1枚/ターンを板へ誘導してKP暗躍を通す。単線のKP暗躍は
        #   反応的に潰される（僕と契約12台本がループ1全滅の実測）＝KP暗躍とボードの二者択一を作る。
        #   偽装先＝評価敗北を連想させる標準板（BTX神社[封印]/FS学校[守るべき]）で、主人公beliefが
        #   板敗北と疑う先。病院（病院の事件の実効果）は避ける。
        elif "kp" in funded:
            # ★主人公beliefが板敗北ルールを疑っている時だけ偽装が効く（疑いの無い板へ暗躍しても
            #   暗躍禁止を誘導できず自陣カードの無駄）。belief不能時は連想板で保守的に立てる。
            _board_plausible = True
            if belief_base is not None:
                try:
                    _bp = sum(p for (ry, _r), p in belief_base.rule_marginals().items()
                              if ry in ("封印されしモノ", "守るべき場所",
                                        "復讐者の灯火", "巨大時限爆弾Xの存在"))
                    _board_plausible = _bp >= 0.15
                except Exception:  # noqa: BLE001
                    pass
            if _board_plausible:
                decoy_board = next((b for b in _prefs
                                    if b not in goal_boards and b != "病院"), None)
                if decoy_board is not None:
                    _dcost = max(0, 2 - ba.get(decoy_board, 0))
                    decoy_funded = path_costs.get("kp", 99) + _dcost <= supply

        # ★ゴールボードの暗躍を剥がせる主人公側の役が居るか（居なければ2で敗北確定＝
        #   それ以上（暗躍4等）積むのは無駄。ユーザー指摘 2026-07-06）。
        # ★暗躍を剥がせる役が居る「ボード」の集合（board別）。居ないボードは2で敗北確定＝
        #   それ以上積むのは無駄。能力名でボードを判別：「神社の暗躍除去」等エリア名入り＝その
        #   ボード限定（巫女）／エリア名の無い汎用除去（転校生・神格）＝全ゴールボード。
        #   ★A-5(d)：以前はグローバルboolで、巫女の神社除去が病院の過剰置き抑制まで無効化していた
        #   （除去できない病院を2超で積み続け、真の敗北ボード神社と同点＝タイブレークで病院を選び敗北）。
        board_removal_boards: set[str] = set()
        for n, cc in chars.items():
            if not cc["alive"]:
                continue
            for ab in goodwill_abilities_of(n) or []:
                if "暗躍除去" not in ab["name"]:
                    continue
                named = {b for b in AREAS if b in ab["name"]}
                board_removal_boards |= named if named else set(goal_boards)
        board_removal = bool(board_removal_boards)  # 後方互換の派生bool

        # ★A-24'（2026-07-16・FableA承認）：board_x への暗躍供給線を維持する＝board_x を決めている
        #   クロマクを board_x に留める/戻す。供給の本命はクロマクの脚本家能力フェイズの暗躍+1
        #   （行動解決の外＝**暗躍禁止で止まらない**）＝クロマクが board_x を離れると、その勝ち筋は
        #   供給線ごと死ぬ（能力の射程は自エリア/自ボードのみ）。
        #   実測（FB3 beginner_FS seed13・復讐者の灯火/board_x=都市）：mmはL1D1に都市へ正しく
        #   積んだが、主人公が移動←→をクロマクに貼るだけで都市から退去させ（L1D2/L3D1/L4D1）、
        #   mmは一度も戻さなかった＝勝ち筋を無料で無効化されていた。KP不在のため(4)の
        #   set_move_kuromaku（KPのエリアへ寄せる）が発火しないのが構造的な穴。
        # ★★ウィッチ（巨大時限爆弾Xの存在）は**対象外**＝規則上の理由（FableA条件(a)の訂正）：
        #   ウィッチは「追加能力：なし」（rules/50:132・条文能力は絶対友好無視のみ）＝board_x へ
        #   暗躍を供給できない。かつ board_x はループ開始時の初期エリアで確定
        #   （sim/state.prepare_loop・rules/50:52）＝ウィッチのその後の位置は敗北条件に無関係。
        #   よってウィッチを board_x へ戻す移動は純粋な札の無駄＝この選好はクロマク限定にする。
        board_x = view.get("rule_y_board_x") if ry == "復讐者の灯火" else None
        boardx_owner = kuromaku if board_x else None
        if not boardx_owner:
            board_x = None

        # ★B-30③（2026-07-17・FableA承認・大物のテリトリー投射のmm活用）：大物は「テリトリーに
        #   いるものとして能力を使ってもよい」（KB: 20・現物確認済）＝**任意投射**。sim は実エリア∪
        #   テリトリーを能力の選択肢に並べ（sim/legal.py）、mm能力フェイズの投射（クロマク/ML）は
        #   既存 _score_ability がゴール優先で正しく選ぶ（実測確認済）。残る穴＝**移動選好が投射を
        #   知らない**：大物がキラー/クロマクで、能力の対象（KP等）が**テリトリーに居れば、大物を
        #   その対象へ物理移動させる必要がない**（投射で同エリア扱い＝turn_end殺害/KP暗躍が成立）。
        #   ＝A-24' と同型の「特性/投射で足りるなら移動は無駄手」（他の有効手を押しのける）。
        oomono = next((n for n, r in roles.items() if n == "大物"), None) \
            or ("大物" if "大物" in chars else None)
        oomono_territory = view.get("oomono_territory") if oomono else None

        out = {"chars": chars, "keyperson": keyperson, "killer": killer,
               "oomono": oomono, "oomono_territory": oomono_territory,
               "boardx_owner": boardx_owner, "board_x": board_x,
               "kuromaku": kuromaku, "kp_anyaku_remover_alive": kp_anyaku_remover_alive,
               "cultist": cultist, "friends": friends,
               "sk": sk, "tt": tt, "killable": killable,
               "death_board_today": death_board_today, "protect": protect,
               "goal_boards": goal_boards, "board_removal": board_removal,
               "board_removal_boards": board_removal_boards,
               "today_culprit": today_culprit, "culprits_all": culprits_all,
               "future_culprits": future_culprits, "threats": threats,
               "threat_hearts": threat_hearts,
               "misleader": misleader, "reachable_culprits": reachable_culprits,
               "pump_targets": pump_targets, "fires_today": fires_today,
               "ambiguous_paths": ambiguous_paths, "kp_kill_near": kp_kill_near,
               "decoy_board": decoy_board, "decoy_funded": decoy_funded,
               "kp_used_tactics": kp_used_tactics,
               "days_left": days_left, "mm_rate": mm_rate,
               "funded": funded, "path_costs": path_costs, "supply": supply,
               "kp_blocked": kp_blocked, "locked": locked,
               "board_guarded": board_guarded, "killer_guarded": killer_guarded,
               # 役職バレコスト（belief.reveal_cost）用の材料。クロマク居るときだけ格納し、
               # Belief 本体は _score_ability で初めてクロマク手を採点する時に遅延構築する。
               "belief_mat": ({
                   "cast": [c["name"] for c in view["characters"]],
                   "inc": [{"day": i["day"], "name": i["name"]}
                           for i in view["incidents"]],
                   "set_name": "BTX" if view.get("rule_x2") else "FS",
                   "history": view.get("history", []),
               } if kuromaku else None),
               "loop_defeat_certain": loop_defeat_certain,
               # ★A-49専用のbelief材料＝クロマク有無に関係なく常に格納する（【任意】発動の
               #   担い手はキラー/メインラバーズ/TT＝クロマクとは別）。既存の belief_mat には
               #   一切触れない＝_reveal_cost 側の挙動は bit-for-bit で不変。構築は
               #   _belief_for_leak の遅延（帰結確定局面でしか呼ばれない＝ほぼゼロコスト）。
               "belief_mat_all": {
                   "cast": [c["name"] for c in view["characters"]],
                   "inc": [{"day": i["day"], "name": i["name"]}
                           for i in view["incidents"]],
                   "set_name": "BTX" if view.get("rule_x2") else "FS",
                   "history": view.get("history", []),
               }}
        if belief_base is not None:
            # M2で構築済みのbeliefをA1（_belief_base の遅延構築）と共有＝二重構築防止。
            out["belief_base"] = belief_base
        return out

    def _pick(self, options: list[dict], score) -> dict:
        """最大スコアの手を返す（同点は seed 固定の rng でタイブレーク）。"""
        best, best_key = None, None
        for o in options:
            key = (score(o), self.rng.random())
            if best_key is None or key > best_key:
                best, best_key = o, key
        return best

    # -- 決定 --------------------------------------------------------------

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision == "goodwill_refuse":
            return self._decide_refuse(view, options)
        a = self._analyze(view)
        if decision == "set_card":
            # ★A-54：暗躍+2の非正当先への減点を採点の出口1箇所で引く（_score_set は勝ち筋ごとに
            #   早期returnが多く、各分岐へ差し込むと配線漏れを招く＝[[wiring-forgotten-across-paths]]）。
            return self._pick(options, lambda o: self._score_set(o, a, view)
                              - self._plus2_penalty(o, a, view))
        if decision == "mastermind_ability":
            return self._pick(options, lambda o: self._score_ability(o, a))
        if decision == "turn_end_ability":
            return self._pick(options, lambda o: self._score_turn_end(o, a))
        if decision == "incident_choice":
            return self._pick(options, lambda o: self._score_incident(o, a))
        if decision == "loop_start_area":
            return self._pick(options, lambda o: self._score_loop_area(o, view))
        if decision == "cultist_ignore":
            return self._decide_cultist_ignore(a, options)
        if decision == "scholar_counter":
            # ★学者の特性（各ループ開始時カウンター1つ）：
            #   学者が犯人→不安（事件燃料）／キラー or 契約Y→暗躍（勝ち筋前進）／
            #   それ以外→不安（霧まき＝eligible要員）。友好は主人公を利するだけ＝選ばない。
            role = view["roles"].get("学者", "パーソン")
            culprits = {i["culprit"] for i in view["incidents"]}
            want = "不安"
            if role == "キラー" or (view["rule_y"] == "僕と契約しようよ！"
                                    and role == "キーパーソン"):
                want = "暗躍"
            elif "学者" in culprits:
                want = "不安"
            return next((o for o in options if o["counter"] == want), options[0])
        # goodwill_ability 等（脚本家に選択肢がある場面は現状ほぼ pass のみ）
        return options[0]

    # -- 友好能力の拒否（賢い拒否＝危険な能力だけ拒否して友好無視を隠す） ----

    def _decide_refuse(self, view: dict, options: list[dict]) -> dict:
        a = self._analyze(view)
        ctx = options[0]
        ability = ctx.get("ability") or ""
        target = ctx.get("target")
        dangerous = any(k in ability for k in _DANGEROUS_ABILITY_HINTS)
        if "暗躍" in ability and "除去" in ability:
            # 勝ち筋の暗躍（ゴールボード/キーパーソン/キラー）を剥がされるなら拒否
            dangerous = dangerous or target in a["goal_boards"] \
                or target == a["keyperson"] or (a["killer"] and target == a["killer"])
        if "不安" in ability and ("除去" in ability or "操作" in ability):
            # 犯人の不安（事件の燃料）を消されるなら拒否
            dangerous = dangerous or target in a["culprits_all"]
        # ★bool正規化：`x and y` はキラー不在時に None を返しうる（is比較がStopIterationになる）
        dangerous = bool(dangerous)
        return next(o for o in options if o.get("refuse") is dangerous)

    # -- カルティストの暗躍禁止無視（A-42：勝ち筋を前進させない無視は見送る） --

    def _cultist_unrevealed(self, a: dict, cult: str) -> bool:
        """カルティストの役職がまだ確定していないか（role_gini>0）。

        確定済みなら見送っても隠すものが無い＝旧挙動（無視）へ。belief 不能なら判別できない＝
        旧挙動（＝A-49 の _optional_leaks_role と同じ健全側：判別不能なら抑制しない）。
        """
        b = self._belief_for_leak(a)
        if b is None:
            return False
        try:
            return b.role_gini(cult) > 0.0
        except Exception:  # noqa: BLE001  判別不能＝旧挙動
            return False

    def _decide_cultist_ignore(self, a: dict, options: list[dict]) -> dict:
        """A-42：無視（暗躍を通す）が勝ち筋を前進させるなら無視、させないなら見送る。

        見送り＝暗躍禁止が実効になって暗躍が止まる（＝前進ゼロなので損は無い）＋「このエリアに
        カルティストが居る」の公開を避けられる（＝tell を出さない）。A-48/A-49 と同じ弁3つ：
          確定弁＝モデルできる勝ち筋（KP/キラー/ゴール盤）がある文脈でだけ効かせる。
          前進判定＝ボードはゴール盤か・キャラは _advances_win_char（A-48再利用）。
          リーク弁＝カルティストが未確定の時だけ見送る（確定済み・belief不能なら旧挙動）。
        """
        ig_true = next(o for o in options if o["ignore"])
        if not self.p["cultist_ignore_gate"]:
            return ig_true                       # 既定＝常に無視（旧挙動・bit-for-bit）
        ig_false = next(o for o in options if not o["ignore"])
        tgt, kind = ig_true["target"], ig_true["target_kind"]
        # 確定弁：勝ち筋をモデルできない脚本（KP・キラー不在＋ゴール盤なし）では効かせない
        if not (a["keyperson"] or a["killer"] or a["goal_boards"]):
            return ig_true
        advance = (tgt in a["goal_boards"]) if kind == "board" \
            else self._advances_win_char({"target": tgt}, a)
        if advance:
            return ig_true                       # 勝ち筋前進＝無視して暗躍を通す
        cult = a["cultist"]
        if cult and self._cultist_unrevealed(a, cult):
            return ig_false                      # 前進ゼロ×未確定＝見送ってカルティストを隠す
        return ig_true

    # -- 暗躍+2 の使用会計（A-54：切り札を実利得ゼロの先へ切らない） ----------

    def _loop_win_certain(self, a: dict, view: dict) -> bool:
        """A-54b：このループの勝ちが**確定級（取り消し不能）**か（ユーザー承認述語・盤面事実のみ）。

        確定級なら +2 を囮/次ループの布石へ転用してよい（例外4）。3述語（いずれか成立）：
          ① 本命ゴール盤が充足済み（≥2）× そのボードの暗躍**除去者が不在**＝取り消せない
          ② フレンドが既に死亡＝ループ終了時に無条件で敗北（A-49の確定弁と同一述語）
          ③ 僕と契約のKPに暗躍≥2＝ループ終了時の敗北条件が成立済み
        ★③は A-54b 側のみ（A-49の確定弁は「KP暗躍は除去されうる」として僕と契約を除外した
          経緯があり現状維持）＝除去者が動ける局面での挙動は掃引で観測して報告する。
        """
        if not self.p["plus2_win_certain_gate"]:
            return False
        chars = a["chars"]
        # ① ゴール盤充足 × 除去者不在（取り消し不能）
        for gb in a["goal_boards"]:
            if (view.get("board_anyaku", {}).get(gb, 0) >= 2
                    and gb not in a.get("board_removal_boards", ())):
                return True
        # ② フレンド死亡＝ループ終了時に無条件敗北（A-49の確定弁と同一）
        if any(not chars.get(n, {}).get("alive", True) for n in a["friends"]):
            return True
        # ③ 僕と契約のKPに暗躍≥2＝敗北条件が成立済み
        kp = a["keyperson"]
        if (view.get("rule_y") == "僕と契約しようよ！" and kp
                and chars.get(kp, {}).get("anyaku", 0) >= 2):
            return True
        return False

    def _plus2_justified(self, o: dict, a: dict, view: dict) -> bool:
        """暗躍+2 を「通れば実利得になる先」に切っているか（docの会計表・自明情報のみ）。"""
        tgt, kind = o["target"], o["target_kind"]
        chars = a["chars"]
        if kind == "board":
            # ★A-54b（例外4）：勝ちが確定級なら +2 を**囮/次ループの布石**へ転用してよい
            #   （FableA裁定＝btx5_seal の囮+2の偽装価値は「本命充足済み＝確定級での転用」）。
            #   ★解禁は**盤**に限る：ループ終了で盤はリセットされるので「布石」の実体は
            #   偽装（主人公のルールY推定を誤らせる）＝盤の帰属を割ること。キャラへの+2は
            #   確定級でも実利得ゼロのまま（検死＝3日 random_BTX s12：未来改変プランで
            #   フレンド死亡→確定級と判定→+2をTT(入院患者)へ浪費し mm勝ち(L8)を落とした）。
            if self._loop_win_certain(a, view):
                return True
            # ゴール盤（敗北条件盤）＝**+2が条件充足に寄与する時だけ**正当（docの「充足or確定圏」）。
            #   ★既に≥2（充足済み）への+2は過剰＝実利得ゼロ＝切り札の浪費（btx5_seal s0 の検死＝
            #   神社が既に充足圏なのに+2を足し、囮盤の偽装を捨てていた）。
            if tgt in a["goal_boards"]:
                return view.get("board_anyaku", {}).get(tgt, 0) < 2
            # 囮盤（例外3）：本命ゴールにバックアップ供給（生存クロマク/カルティスト）が
            #   そのエリアに立っている時だけ「本命+1＋囮+2」の2枚伏せが成立＝許容。
            for gb in a["goal_boards"]:
                for role in (a["kuromaku"], a["cultist"]):
                    c = chars.get(role) if role else None
                    if c and c.get("alive") and c.get("area") == gb:
                        return True
            return False
        # --- キャラへの +2 の正当先リスト（KB全数・docの会計表） ---
        # 殺人計画KP（暗躍≥2＋キラー殺害）・キラー自身（≥4への加速）＝_advances_win_char に集約
        if self._advances_win_char({"target": tgt}, a):
            return True
        # 僕と契約のKP＝ループ終了時暗躍≥2単独で勝ち＝+2一撃の最良形
        if (tgt == a["keyperson"] and view.get("rule_y") == "僕と契約しようよ！"
                and chars.get(tgt, {}).get("anyaku", 0) < 2):
            return True
        # 遠隔殺人の対象化＝条件上は任意キャラだが利得会計で KP/フレンド に絞る
        if (tgt in a["friends"] or tgt == a["keyperson"]) and any(
                i.get("name") == "遠隔殺人" for i in view.get("incidents", ())):
            return True
        # メインラバーズ／ファクター／その他＝+1で足りる or 実利得ゼロ＝非正当（過剰）。
        #   ★メインラバーズ例外（ラバーズ死亡済み×二正面）は保守的に非採用＝稀ケースで
        #   +2が減点されるだけの安全側（掃引で影響が出れば追加検討）。
        return False

    def _plus2_penalty(self, o: dict, a: dict, view: dict) -> float:
        """非正当先への暗躍+2 を +1相当以下へ落とす減点（切り札の浪費抑止）。"""
        if not self.p["plus2_discipline"] or o.get("card") != "暗躍+2":
            return 0.0
        if self._plus2_justified(o, a, view):
            return 0.0
        return self.p["plus2_waste"]

    # -- 行動カードのセット --------------------------------------------------

    def _score_set(self, o: dict, a: dict, view: dict) -> float:
        card, tgt, kind = o["card"], o["target"], o["target_kind"]
        val = _ANRYAKU_VALUE.get(card, 0)
        chars = a["chars"]
        # (1) ゴールボードに暗躍を積む（学校/ボードX/病院）。敗北条件は「≥2」。
        #     ★残手数会計：残供給で届かない（funded外）なら大きく下げる＝終盤の無駄カード防止。
        #     ★過剰置き防止（ユーザー指摘 2026-07-06）：除去役が居なければ2で敗北確定＝
        #     それ以上（暗躍3/4）は無駄。ちょうど2に届く手を優先し、超過は減点。
        if kind == "board" and val and tgt in a["goal_boards"]:
            cur = view["board_anyaku"].get(tgt, 0)
            if cur >= 2 and tgt not in a["board_removal_boards"]:
                return self.p["set_board_locked_over"]  # 既に達成・このボードの除去役なし＝無駄
            need = 2 - cur
            # ★暗躍+2の温存（抑止力・ユーザー知見 2026-07-06）は、超過置きの減点で実現：
            #   1→3のように臨界を超える置きは1枚損＝下げる（+1で足りるなら+1で、+2は温存）。
            #   ※「ボードに単独で伏せるのは暗躍禁止で消される悪手」という規律は、相手が暗躍禁止を
            #   打つ賢者か受け身かを固定スコアでは区別できず、受け身相手に取りこぼす（guard 5%）ため
            #   ヒューリスティックには入れない。カルティスト移動(4b)で盤面暗躍を通す形は既に実装済み。
            if tgt not in a["board_removal_boards"] and cur >= 1 and val > max(need, 0):
                return (self.p["set_board_over_funded"] if "board" in a["funded"]
                        else self.p["set_board_over_unfunded"])  # 過剰（例:1→3）＝1枚損
            score = ((self.p["set_board_base"] + val * self.p["set_val_mult"])
                     if "board" in a["funded"] else self.p["set_board_unfunded"])
            # ★完成順の規律（M1b）：偽装ボードが育つ前に本命を2へ満たすと「先に満ちた筋＝本命」と
            #   バレる＋除去/暗躍禁止の的として日数分さらされる。最終日以外は、偽装が未完のうちの
            #   本命完成を保留（減点）＝両ボードを終盤に揃えて帰属を割る。
            if (cur + val >= 2 and a["days_left"] >= 2 and a["decoy_funded"]
                    and view["board_anyaku"].get(a["decoy_board"], 0) < 2):
                score = max(1.0, score - self.p["set_board_hold"])
            # ★A-57：本命貫通手段の優先（§1i 正典原則・ユーザー裁定 2026-07-24）＝
            #   「打ち消されることを目的とした拘束は、その筋を捨ててから。**活きている本命には
            #   通す手段を使う**」。実測（BTX seed16＝ボードX都市・KP/キラー不在＝勝ち筋は盤と
            #   SKの2本）＝mmは D1/D2 に `暗躍+1→都市` を置いて主人公の暗躍禁止に全部食われ、
            #   `暗躍+2→都市`(95) は候補にありながら +1(110) に負けて温存→D3に切って打ち消され、
            #   L1盤面は都市0＝**1歩も進まないまま L8 まで負け続けた**（+1連打＝最弱手段）。
            #   ∴ その盤が**毎ターン覆われている**（board_guarded＝暗躍禁止の観測実績）なら、
            #   **一撃で条件を満たす +2**（cur+val>=2）を +1 より上へ。判定材料は公開情報のみ
            #   （暗躍禁止の実績・盤面カウンタ）＝opponent-model不要。
            #   ★A-54（+2の使用会計）とは非干渉＝ゴール盤への+2は A-54 でも正当先。
            #   ★貫通が要るのは「**+1では届かない**」時だけ（need>=2）。+1で足りる盤（need==1）へ
            #   +2を切るのは 1/loop の切り札の浪費＝検死（5日 random_BTX s0＝ボードX病院）で
            #   L2D1に `+2→病院` を切って盤面4＝条件2を大幅超過し、mmがループを取り損ねた。
            #   ＝A-54「充足に寄与する時のみ」と同じ精神。
            if (self.p["breakthrough_priority"] and a.get("board_guarded")
                    and val >= 2 and need >= 2 and cur + val >= 2
                    and "board" in a["funded"]):
                score += self.p["breakthrough_bonus"]
            return score
        # (1b) ★偽装ボード（M1b・カバーストーリー）：本命が評価敗北系のとき、別のルールY候補に
        #      見えるボードへ捨て暗躍＝ループ終了時の帰属を割る＋主人公の守りを空費させる罠。
        #      2で完成（それ以上は積まない＝過剰は本命同様に無駄）。
        if kind == "board" and val and tgt == a["decoy_board"] and a["decoy_funded"]:
            cur = view["board_anyaku"].get(tgt, 0)
            if cur >= 2 or (cur >= 1 and val > 2 - cur):
                return self.p["set_board_locked_over"]   # 偽装は2で十分・超過は無駄
            return self.p["set_board_decoy"]
        # (2) キーパーソンに暗躍（キラー殺害の前提＝暗躍≥2）。
        #     ★塞がれ検知後はピン留め価値だけ残して優先度を下げ、(2b)のキラー自己暗躍へ切替。
        if kind == "character" and val and tgt == a["keyperson"]:
            if chars[tgt]["anyaku"] >= 2:
                return self.p["set_kp_done"]
            if a["kp_blocked"]:
                return self.p["set_kp_blocked"]
            score = ((self.p["set_kp_base"] + val * self.p["set_val_mult"])
                     if "kp" in a["funded"] else self.p["set_kp_unfunded"])
            # ★M3 戦術の反復回避：前ループもカードでKP暗躍を積んで勝った＝主人公は
            #   「KPの伏せ札→暗躍禁止」を学習済み。同じカード戦術は減点。ただし
            #   カルティスト同伴（同エリア＝暗躍禁止を無視で貫通）は対策が効かない別戦術＝免除。
            if "card" in a["kp_used_tactics"]:
                _cul = a["cultist"]
                _kpc = chars.get(a["keyperson"])
                _escort = (_cul and chars.get(_cul, {}).get("alive")
                           and chars[_cul].get("area") and _kpc
                           and chars[_cul]["area"] == _kpc["area"])
                if not _escort:
                    score = max(1.0, score - self.p["tactic_repeat"])
            return score
        # (2b) キラー自身に暗躍4（主人公殺害）。
        #     ★二正面圧力：残手数会計が「kp/boardと同時に賄える」と言うなら、塞がれる前から
        #     副軸として積む（主人公の暗躍禁止は1枚/T＝両方は塞げない）。
        #     塞がれ検知後は従来どおり本命へ昇格。
        if kind == "character" and val and tgt == a["killer"] and chars[tgt]["alive"]:
            if chars[tgt]["anyaku"] >= 4:
                return self.p["set_killer_done"]
            if "killer4" in a["funded"]:
                base = (self.p["set_killer_blocked"] if a["kp_blocked"]
                        else self.p["set_killer_funded"])
                return base + val * self.p["set_val_mult"]
            return self.p["set_killer_unfunded"]
        # (3) その日の事件犯人の不安を臨界へ（事件を発生させる）。
        #     ★抑制モード（勝ち確定時）：事件は情報流出＝発生させない・不安-1で犯人を冷やす。
        if card == "不安-1" and kind == "character" and a["locked"]:
            c = chars.get(tgt)
            th = unrest_threshold_of(tgt)
            # ★A-5(b)：冷やす価値があるのは「今日以降に事件がある犯人」だけ。事件日が過ぎた犯人
            #   （例：L2D2で Day1 事件のお嬢様）は防ぐ事件が無く、確定済みで情報流出も既に済＝
            #   冷やしても無意味（公開確定情報と矛盾する操作）。culprits_all（全日）→ upcoming に。
            if (c and c["alive"]
                    and (tgt == a["today_culprit"] or tgt in a["future_culprits"])
                    and th is not None and c["unrest"] >= max(1, th - 1)):
                return self.p["set_cool_locked"]  # 臨界間際の犯人を冷やして事件を不発に
        if card == "不安+1" and kind == "character":
            if a["locked"]:
                return self.p["set_unrest_locked"]  # 勝ち確定時は不安を増やさない
            c = chars.get(tgt)
            th = unrest_threshold_of(tgt)
            if c and c["alive"]:
                # ★A-38（2026-07-19）：対象が「不死で効果が無効な事件」の犯人（自殺×TT等）なら、
                #   犯人扱い（today/future の本命価値）より**先に** filler化する。臨界に届かせても
                #   打点0（不死で自殺が無効＝60:A26）＋発火すると death_prevented で役職が公開露呈
                #   （棋譜：妹=TT の自殺に不安3投資→TT バレ）＝A-25（臨界0の透明な霧）と同型の純損。
                #   ★ウイルスガードは**不要**：ウイルスSK化はパーソン限定（KB: 50）＝不死キャラ（TT等・
                #   非パーソン）は SK 化しない＝ウイルスがあっても不安は純損。_incident_nullified_by_
                #   immortal が「対象が不死」を要求する＝この分岐は非パーソン確定＝無条件で filler化。
                if any(inc["culprit"] == tgt and inc["day"] >= view["day"]
                       and _incident_nullified_by_immortal(
                           inc["name"], view["roles"].get(tgt, ""))
                       for inc in view["incidents"]):
                    return self.p["set_unrest_zero_th"]
                # ★発生の打点会計：臨界に届かない事件の犯人へは不安+1を注がない（無駄撃ち）。
                #   届く犯人（reachable）だけ本命価値、届かないなら霧まきに劣後させる。
                if tgt == a["today_culprit"] and th is not None and c["unrest"] < th:
                    return (self.p["set_unrest_today"] if tgt in a["reachable_culprits"]
                            else self.p["set_unrest_today_far"])
                if tgt in a["future_culprits"] and th is not None and c["unrest"] < th:
                    return (self.p["set_unrest_future"] if tgt in a["reachable_culprits"]
                            else self.p["set_unrest_future_far"])  # 未来の犯人の仕込み
                # ★迷ったら不安+1は臨界の低いキャラへ（ユーザー知見 2026-07-06）：
                #   臨界以上のキャラは事件のたびに公開の候補者リスト（eligible）へ載る＝
                #   「霧」要員。安く臨界に届くキャラほど犯人特定を長く妨げる。
                #   ※自分のルールに妄想拡大ウイルスがある時は、パーソンを不安3に上げない
                #   （本物のSK化＝制御外の殺しで自陣の勝ち筋が事故る）。
                rxs = {view.get("rule_x"), view.get("rule_x2")}
                if ("妄想拡大ウイルス" in rxs
                        and view["roles"].get(tgt, "パーソン") == "パーソン"
                        and c["unrest"] + 1 >= 3):
                    return self.p["set_unrest_virus"]
                # ★A-25（2026-07-16）：不安臨界0のキャラ（黒猫）への不安+1＝「透明に死んだ霧」。
                #   臨界0は**公開情報**（キャラカードの表）＝主人公にも「このキャラが犯人でも不安は
                #   要らない」が自明。かつ不安0でも事件は発生＝最初から eligible（公開候補者リスト）に
                #   載っており、不安を足しても候補者リストは1ミリも動かない＝ブラフ価値ゼロ。
                #   実測＝FB3 beginner_FS seed13 L1D3 黒猫。旧実装は下の「既に臨界以上」ゲート
                #   （0>=0）に当たり set_unrest_sat=2.0＝filler最下位(1.0)より上のため、過剰置きしか
                #   残っていない場面で黒猫が勝っていた＝A-21と同じく最下位へ落とす。
                #   ★ウイルスガード（A-22と同型・必須）：妄想拡大ウイルス（不安3でSK化）では不安は
                #   実弾＝臨界0キャラでも意味がある＝停止しない。
                if th == 0 and "妄想拡大ウイルス" not in rxs:
                    return self.p["set_unrest_zero_th"]
                # ★A-22：ループ内に残り事件がゼロなら、不安+1の霧まきは「何もブラフしていない」純無駄
                #   （犯人は秘匿でも、隠すべき未来の事件が無い＝最終盤の見えるfiller）。
                #   ★必須ガード＝不安が事件以外で効くルール（妄想拡大ウイルスのSK化等）では停止しない
                #   （残り事件ゼロでも不安は実弾）。ウイルスは上の分岐で扱い済み＝ここは非在のみ対象。
                if ("妄想拡大ウイルス" not in rxs
                        and not any(inc["day"] >= view["day"] for inc in view["incidents"])):
                    return self.p["set_unrest_sat"]
                # ★過剰な不安の抑制（ユーザー指摘 2026-07-06）：既に不安臨界以上のキャラは
                #   もう eligible（霧要員）＝これ以上の不安は無駄。臨界未満のキャラだけ霧に足す。
                if th is not None and c["unrest"] >= th:
                    return self.p["set_unrest_sat"]
                # ★A-5(a)：残り解決回数で臨界に届かないキャラへのノイズは見透かされる（霧にならない）。
                #   最大限pumpしても臨界未満＝「この者は今ループ犯人になれない」と読まれ消去される
                #   （例：最終日に臨界4のご神木へ不安+1＝無意味）。「届く犯人だけ注ぐ」の霧版。
                #   ※reach分岐（unrest+1≥th）とは排他（届く者はこの手前で扱う）。低臨界も残日数が
                #   足りなければ同様に見透かされる（最終日ガード）。
                if th is not None and c["unrest"] + a["mm_rate"] * a["days_left"] < th:
                    return self.p["set_unrest_sat"]
                noise = self.p["set_noise_base"]
                if th is not None:
                    if c["unrest"] + 1 >= th:
                        # ★霧の日付設計（ユーザー知見 2026-07-09）：臨界到達の霧は
                        #   「発火する日」にだけ有効（eligibleに紛れる）。発火しない予定日に
                        #   臨界の者を立てると「不発＝臨界だった者は犯人でない」の消去法を
                        #   主人公に与える＝逆効果なので減点。事件の無い日は将来の霧の仕込み。
                        if a["fires_today"] or a["today_culprit"] is None:
                            noise += self.p["set_noise_reach"]  # 発火日/事件なし日＝霧要員
                        else:
                            noise = max(1.0, noise - self.p["set_noise_leak"])
                    elif th <= 2:
                        noise += self.p["set_noise_low"]    # 低臨界＝安く霧にできる
                # ★可読性コスト（テスター知見 2026-07-09）：移動不可キャラ（入院患者等＝
                #   禁止3エリア）への伏せ札は「移動ではない＝ほぼ不安」と読まれ、不安-1で
                #   即対処される。ブラフ価値が低い先には撒かない（照準は動けるキャラへ分散）。
                if len(forbidden_of(tgt)) >= 3:
                    noise = max(1.0, noise - self.p["set_noise_immobile"])
                return noise
        # (4-bx) ★A-24'（2026-07-16・FableA承認）：board_x の担い手（クロマク/ウィッチ）を board_x へ
        #        戻す／board_x から引き剥がさない。担い手の能力＝暗躍禁止で止まらない唯一の供給線。
        #        (b)打点会計ガード＝ゴール未達（not locked）かつボード筋に資金がある（"board" in funded）
        #          時だけ。届かないなら戻しても filler＝置かない。
        #        (c)固執しない＝スコアは移動帯（killer50/kuromaku48）と同格に留め、より良い手
        #          （ゴール盤への暗躍 set_board_base=100 等）があればそちらが勝つ。主人公が毎ターン
        #          移動札を貼ってくるなら、それは1:1のカード交換＝こちらの損ではない。
        if (card in MOVE_CARDS and kind == "character" and a["board_x"]
                and tgt == a["boardx_owner"] and not a["locked"] and "board" in a["funded"]):
            mv = chars.get(tgt)
            if mv and mv["alive"] and mv["area"] and mv["area"] != a["board_x"]:
                if a["board_x"] in forbidden_of(tgt):
                    return 0                    # 到達不能＝無駄カード
                if _single_move_card(mv["area"], a["board_x"]) == card:
                    return self.p["set_move_boardx_return"]
        # (4) キラー／クロマクをキーパーソンのエリアへ寄せる移動
        #     （クロマクの脚本家能力フェイズ暗躍+1は暗躍禁止で止められない＝本命経路）。
        #     行き先が禁止エリアのキャラは動かさない（無駄カード防止）。
        if card in MOVE_CARDS and kind == "character" and a["keyperson"]:
            for mover, base in ((a["killer"], self.p["set_move_killer"]),
                                (a["kuromaku"], self.p["set_move_kuromaku"])):
                if tgt == mover:
                    # ★A-24'「留める」：資金の付いた board_x に居る担い手は、KP寄せで引き剥がさない
                    #   （board_x への供給線を自分から切る自滅手＝KP経路より board_x が近い）。
                    if (tgt == a["boardx_owner"] and a["board_x"] and not a["locked"]
                            and "board" in a["funded"]
                            and chars.get(tgt, {}).get("area") == a["board_x"]):
                        return self.p["set_move_stray"]
                    # ★B-30③：mover が大物で、KP が大物のテリトリーに居るなら、大物をKPへ寄せる移動は
                    #   不要＝**テリトリー投射で同エリア扱い**になり turn_end 殺害/KP暗躍が成立する
                    #   （投射で足りるのに物理移動＝A-24'型の無駄手）。実測（大物=キラー・KP=テリトリー
                    #   神社）＝斜め移動が set_move_killer=50 で選ばれていた。
                    _kp = chars.get(a["keyperson"])
                    if (tgt == a["oomono"] and a["oomono_territory"]
                            and _kp and _kp.get("area") == a["oomono_territory"]):
                        return self.p["set_move_stray"]
                    kp, mv = chars.get(a["keyperson"]), chars.get(mover)
                    if kp and mv and kp["area"] and mv["area"] and kp["area"] != mv["area"]:
                        if kp["area"] in forbidden_of(mover):
                            return 0  # 到達不能（例: サラリーマンは学校禁止）
                        if _single_move_card(mv["area"], kp["area"]) == card:
                            # ★A-52：KP暗躍が既に2以上＝殺害条件のカウンター側は充足済み＝
                            #   この移動1枚で勝ち（ターン終了の【任意】KP殺害→即敗北+ループ終了）。
                            #   「あと1手で勝つ手」を「あと3手要る手」より安く見積もらない。
                            if (mover == a["killer"] and mv["alive"]
                                    and kp.get("anyaku", 0) >= 2
                                    and self.p["set_move_kill_complete"]):
                                return self.p["set_move_kill_complete"]
                            return base
                    return self.p["set_move_stray"]
        # (4a-1) ★偽キラー候補＝目撃者（ユーザー知見 2026-07-09）：キラーのKP殺害が近いとき、
        #        KPのエリアへ第三者を寄せる＝殺害時の present が増え「同エリアに居た誰か」まで
        #        しかキラーが絞れない（殺害は自己開示系だが、キラーの特定は防げる＝役職レベルの
        #        曖昧性）。第三者が居ないままの殺害は killer を present 消去でほぼ確定させる。
        if card in MOVE_CARDS and kind == "character" and a["kp_kill_near"] \
                and not a["locked"] and tgt not in (a["killer"], a["keyperson"]):
            kpc = chars.get(a["keyperson"])
            tc3 = chars.get(tgt)
            if (kpc and tc3 and tc3["alive"] and tc3["area"] and kpc["area"]
                    and tc3["area"] != kpc["area"]
                    and kpc["area"] not in forbidden_of(tgt)
                    and _single_move_card(tc3["area"], kpc["area"]) == card):
                return self.p["set_move_witness"]
        # (4a0) ★移動封鎖（ユーザー知見 2026-07-06）：今ターン死亡ボード（病院の事件が発生）へ、
        #       生かしたい自陣キャラ（未来の犯人・キラー・SK等）を主人公に入れさせない。
        #       対象が死亡ボードの隣接に居るなら、直交移動を置けば主人公が何を足しても入れない
        #       （主人公は斜めを持たない）。行き先が禁止エリアだと移動が不成立＝封鎖にならない。
        if card in MOVE_CARDS and kind == "character" and a["death_board_today"] \
                and not a["locked"] and tgt in a["protect"]:
            db = a["death_board_today"]
            tc = chars.get(tgt)
            if tc and tc["alive"] and tc["area"] and tc["area"] != db:
                block = _perp_block_card(tc["area"], db)
                dest = _move_dest(tc["area"], card) if block == card else None
                if block == card and dest and dest not in forbidden_of(tgt):
                    return self.p["set_move_block_death"]  # 直交移動で死亡ボードへ入れない＝生存確保
        # (4a1) ★シリアルキラーの位置作り（ユーザー知見 2026-07-06：SKをもっと使う）：
        #       SK＋標的（キーパーソン/フレンド）を2人きりにすればターン終了に【強制】で殺せる。
        #       (i)SK自身を「標的だけが居るエリア」へ寄せる／(ii)SK＋標的の部屋の3人目を追い出す。
        if card in MOVE_CARDS and kind == "character" and a["sk"] and a["killable"] \
                and not a["locked"]:
            _sk = a["sk"]
            _skc = chars.get(_sk)

            def _alive_in(area, exclude=()):
                return [n for n, cc in chars.items()
                        if cc["alive"] and cc.get("area") == area and n not in exclude]

            if _skc and _skc["alive"] and _skc["area"]:
                # (i) SKを、標的1人だけが居るエリアへ寄せる＝寄れば2人きり
                if tgt == _sk:
                    for _t in a["killable"]:
                        _tc = chars.get(_t)
                        if not (_tc and _tc["alive"] and _tc["area"]):
                            continue
                        if (_tc["area"] not in forbidden_of(_sk)
                                and _single_move_card(_skc["area"], _tc["area"]) == card
                                and not _alive_in(_tc["area"], exclude=(_t,))):
                            return self.p["set_move_sk_approach"]
                # (ii) SK＋標的が居る3人部屋の"3人目"を別エリアへ追い出す＝残りが2人きり
                elif tgt != _sk and tgt not in a["killable"]:
                    _tc = chars.get(tgt)
                    if _tc and _tc["alive"] and _tc["area"] == _skc["area"]:
                        _here = _alive_in(_skc["area"])
                        _tg_here = [t for t in a["killable"] if t in _here]
                        _dest = _move_dest(_tc["area"], card)
                        if (len(_here) == 3 and len(_tg_here) == 1 and _dest
                                and _dest != _skc["area"] and _dest not in forbidden_of(tgt)):
                            return self.p["set_move_sk_evict"]
        # (4a2) ★ミスリーダーを（届く）犯人のエリアへ寄せる移動：同居させれば脚本家能力
        #       フェイズの不安+1で犯人の不安を日数×2で稼げる（ユーザー知見 2026-07-06）。
        #       届かない事件には寄せない（無駄移動）。抑制モード時も不要。
        if card in MOVE_CARDS and kind == "character" and tgt == a["misleader"] \
                and not a["locked"] and a["pump_targets"]:
            ml = chars.get(a["misleader"])
            if ml and ml["area"]:
                for _d, culp in a["pump_targets"]:
                    cc = chars.get(culp)
                    if (cc and cc["area"] and cc["area"] != ml["area"]
                            and cc["area"] not in forbidden_of(a["misleader"])
                            and _single_move_card(ml["area"], cc["area"]) == card):
                        return self.p["set_move_misleader"]
            return self.p["set_move_stray"]
        # (4b) カルティストをゴールボードへ寄せる移動：カルティストが居るボードの暗躍禁止は
        #      無視できる（行動解決）＝ゴールボードへのカード暗躍が通るようになる。
        if card in MOVE_CARDS and kind == "character" and tgt == a["cultist"] \
                and a["goal_boards"]:
            cu = chars.get(a["cultist"])
            if cu and cu["area"]:
                for gb in a["goal_boards"]:
                    if cu["area"] != gb and gb not in forbidden_of(a["cultist"]) \
                            and _single_move_card(cu["area"], gb) == card:
                        # ★相手モデル：ボードに暗躍禁止を毎回当ててくる相手には、
                        #   カルティスト同席＝暗躍禁止の無効化が最良の回答＝優先を上げる
                        if a.get("board_guarded"):
                            return self.p["set_move_cultist_guarded"]
                        # ★A-55：実績（guarded＝暗躍禁止2回）を待つと3日級では手遅れ＝
                        #   ゴール盤経路が生きているなら搬送を先に通す（§1i 静かな盤移動）。
                        if "board" in a["funded"]:
                            return self.p["set_move_cultist_funded"]
                        return self.p["set_move_cultist"]
            return self.p["set_move_stray"]
        # (5) 友好禁止：脅威キャラ（暗躍除去/開示持ち）の友好を止め、能力解禁を遅らせる。
        #   ★残日数で解禁不能な相手には置かない（無駄撃ち防止・ユーザー指摘 2026-07-06）。
        #   主人公が1ターンに1キャラへ現実的に盛れる友好は最大2（友好+2）とみなし、
        #   「現在友好＋2×残日数 ≥ 必要友好」でなければ意味がない。重い能力（♡4+）は
        #   ハートが乗り始める（現在友好≥1）まで待つ＝脚本家の手数を温存する。
        if card == "友好禁止" and kind == "character" and tgt in a["threats"]:
            # ★A-4（2026-07-14）：TTへの友好禁止は自滅手＝置かない。TTへの友好+は友好禁止を
            #   【強制】無視する（KB:50）ので友好禁止は無効（能力解禁を遅らせられない）＋「なぜ
            #   このキャラに友好禁止？」で主人公にTTを確定リークする。脚本家はTTを知っている(roles)。
            if tgt == a["tt"]:
                return 0
            c = chars.get(tgt)
            if not (c and c["alive"]):
                return 0
            hearts = a["threat_hearts"].get(tgt, 3)
            if c["goodwill"] >= hearts:
                return 0            # 既に解禁済み＝友好禁止（付与を止めるだけ）は無意味
                #   （テスター指摘 2026-07-11：友好満タン=能力解禁済のナースへの友好禁止は無駄）。
            if c["goodwill"] + 2 * a["days_left"] < hearts:
                return 0            # 残り日数では解禁不能＝無駄
            if c["goodwill"] >= 1:
                score = self.p["set_gwban_hot"]    # 友好が乗り始めた＝止める価値大
            elif hearts <= 3:
                score = self.p["set_gwban_light"]  # 軽い能力は先回りでもよい
            else:
                return 0            # 重い能力でハート0＝溜まり始めるまで待つ
            # ★タイミング（テスター知見 2026-07-09）：事件の仕込み（届く犯人への不安pump）が
            #   残っている間、友好禁止は1手早い＝事件発生を優先し、解禁間際
            #   （次の友好+2で必要数に到達）でなければ friendly-ban を後ろへ回す。
            if a["pump_targets"] and c["goodwill"] + 2 < hearts:
                score = max(1.0, score - self.p["set_gwban_defer"])
            return score
        return 0

    # -- 脚本家能力フェイズ ---------------------------------------------------

    def _belief_base(self, a: dict):
        """観測済み Belief を遅延構築してキャッシュ（1 decide につき1回・0.8ms程度）。"""
        if "belief_base" not in a:
            mat = a.get("belief_mat")
            a["belief_base"] = None
            if mat:
                try:
                    from agents.belief import Belief
                    b = Belief(mat["cast"], mat["inc"], mat["set_name"])
                    b.observe(mat["history"])
                    a["belief_base"] = b
                except Exception:  # noqa: BLE001  belief不能なら crowd 代理へ退避
                    a["belief_base"] = None
        return a["belief_base"]

    def _reveal_cost(self, o: dict, a: dict, reveal_area: str | None) -> float:
        """クロマク暗躍の役職バレコスト。本番＝belief.reveal_cost の collapse_frac×重み。

        クロマク能力は発動エリアの present（顔ぶれ）を公開し、主人公の可能世界を畳む。
        collapse_frac＝畳まれる割合（初回暴露で大・暴露済みなら≈0＝以降は無料）。
        belief構築不能時のみ crowd 代理（present人数に反比例）へフォールバック。
        """
        if not reveal_area:
            return 0.0
        present = sorted(n for n, c in a["chars"].items()
                         if c["alive"] and c["area"] == reveal_area)
        base = self._belief_base(a)
        if base is not None and present:
            try:
                from agents.belief import reveal_cost_from
                hypo = [{"event": "anyaku", "phase": "mastermind_ability",
                         "target": o["target"], "delta": 1, "present": present}]
                frac = reveal_cost_from(base, hypo)["collapse_frac"]
                return self.p["ab_reveal_weight"] * frac
            except Exception:  # noqa: BLE001  想定外は代理へ
                pass
        crowd = sum(1 for c in a["chars"].values()
                    if c["alive"] and c["area"] == reveal_area)
        return self.p["ab_sparse_penalty"] * max(0.0, self.p["ab_reveal_crowd"] - crowd)

    def _advances_win_char(self, o: dict, a: dict) -> bool:
        """A-48：クロマクのキャラ暗躍（o=character target）が**生きた勝ち筋を前進させるか**。
        前進＝KP暗躍が未達（<2・除去者生存なら<3のバッファ）／キラー暗躍<4（killer4本命 or 切替後）。
        前進ゼロ（過剰・非勝ち筋キャラ）は False＝_score_ability で filler化＝ソース確定リーク回避。"""
        tgt = o.get("target")
        chars = a["chars"]
        kp, killer = a["keyperson"], a["killer"]
        if tgt == kp and "kp" in a["funded"] and not a["kp_blocked"]:
            cap = 3 if a.get("kp_anyaku_remover_alive") else 2
            return chars.get(kp, {}).get("anyaku", 0) < cap
        if (tgt == killer and killer and chars.get(killer, {}).get("alive")
                and ("killer4" in a["funded"] or a["kp_blocked"])):
            return chars.get(killer, {}).get("anyaku", 0) < 4
        return False   # 非勝ち筋キャラ・過剰・塞がれ後のKP等＝前進ゼロ

    def _score_ability(self, o: dict, a: dict) -> float:
        if o.get("action") == "pass":
            return self.p["ab_pass"]  # 何も得が無ければパス（負スコアは作らない）
        if o["kind"] == "anyaku":
            # ★不穏な噂の情報衛生（テスター知見 2026-07-09）：使用した瞬間に「ルールX＝
            #   不穏な噂」が公開される（1/loopの固有ソース）。ゴールボード前進（かつ未達成）
            #   でなければ、ルール開示コストだけ払う悪手＝パス未満に落とす。
            if o.get("action") == "不穏な噂":
                on_goal = (o["target_kind"] == "board"
                           and o["target"] in a["goal_boards"])
                if not on_goal or a["locked"]:
                    return self.p["ab_rumor_offgoal"]
                # ★噂は同エリア不問＝クロマクのような役職バレを起こさない（下の reveal_cost は
                #   適用しない）。ゴール前進なら満額。
                return self.p["ab_anyaku_board"]
            # ★役職バレコスト（情報秘匿・テスター知見P1・本番版 2026-07-09）：クロマク能力は
            #   発動エリアの present（顔ぶれ）を公開して主人公の可能世界を畳む。belief.reveal_cost
            #   で「畳まれる割合 collapse_frac」を測り重みを掛ける（初回暴露で大・暴露済みは≈0＝
            #   以降は無料）。KP暗躍等の高価値手は基礎点が大きいのでコストを引いても実行される＝
            #   「勝ちに必要なら使う」。低価値な ab_anyaku_other/非ゴール盤は割に合わなくなる。
            if o["target_kind"] == "board":
                reveal_area = o["target"]
            else:
                _tc = a["chars"].get(o["target"])
                reveal_area = _tc["area"] if _tc else None
            reveal_cost = self._reveal_cost(o, a, reveal_area)
            if o["target_kind"] == "board":
                if o["target"] in a["goal_boards"]:
                    return self.p["ab_anyaku_board"] - reveal_cost
                # ★A-20：非ゴールボードへのmm能力暗躍は無価値＝不使用（pass未満）。mm能力はカード枠を
                #   消費しない＝filler理論の適用外で、価値ゼロの発動は暗躍出現＝クロマク位置シグナルを
                #   無償で漏らす純リーク（例：病院の事件が無い脚本で病院に居るクロマクの自ボード暗躍）。
                return self.p["ab_rumor_offgoal"]
            # ★M3 戦術の反復回避（能力側）：前ループも能力（噂/クロマク）でKP暗躍を積んで
            #   勝った＝主人公はクロマク警戒・KP周辺の顔ぶれ監視を学習済み。同じ能力戦術は減点。
            _t_rep = (self.p["tactic_repeat"]
                      if ("ability" in a.get("kp_used_tactics", ())
                          and o.get("target") == a["keyperson"]) else 0.0)
            if o["target_kind"] == "character":
                # ★A-48：勝ち筋を前進させないキャラ暗躍（KP過剰・非勝ち筋キャラ・塞がれ後）は filler化＝
                #   少人数エリアでのソース確定リークを避ける（発動を我慢＝reveal_cost系の情報衛生）。
                #   cap は除去者生存でKP暗躍3まで（バッファ1）。gate=0 で旧挙動へ戻せる。
                #   ★安全弁（健全側・B-42「判別不能は課金しない」/C-15「判別不能は一時側」と同原則）：
                #   ゲートは**自分がモデルしている勝ち筋**（ゴール盤／KP／キラー4）で判定できる文脈でだけ
                #   効かせる。どれも無い脚本（例：ルールY=未来改変プランでKP・キラー不在＝キャラ暗躍自体が
                #   勝ち筋）では判定不能＝抑制しない（旧挙動）＝偽陰性でmmのengineを奪わない。
                #   判定は**脚本の構造**で見る（配役にKP/キラーが居るか・ゴール盤があるか）＝資金の有無は
                #   前進判定側（_advances_win_char）の仕事。構造的にモデル勝ち筋ゼロの脚本だけ弁を閉じる。
                #   ★もう1つの健全側条件＝**実際にリークする発動だけ**抑える（reveal_cost > 0）。
                #   クロマクが暴露済み／大人数エリアでは collapse_frac≈0＝reveal_cost 0＝守るものが無い
                #   ＝抑制する理由がない（A-48は情報コスト会計＝コストゼロなら会計対象外）。
                if (self.p["kuromaku_progress_gate"]
                        and reveal_cost > 0
                        and (a["keyperson"] or a["killer"] or a["goal_boards"])
                        and not self._advances_win_char(o, a)):
                    return self.p["ab_rumor_offgoal"] - reveal_cost
                if o["target"] == a["keyperson"] and not a["kp_blocked"]:
                    return self.p["ab_anyaku_kp"] - reveal_cost - _t_rep  # 暗躍禁止で止まらないKP暗躍（本命）
                if o["target"] == a["killer"] and a["kp_blocked"]:
                    return self.p["ab_anyaku_killer_blocked"] - reveal_cost  # 切替後はキラーへ
                if o["target"] == a["killer"] and "killer4" in a["funded"]:
                    return self.p["ab_anyaku_killer"] - reveal_cost  # 二正面圧力の副軸
                if o["target"] == a["keyperson"]:
                    return self.p["ab_anyaku_kp_late"] - reveal_cost - _t_rep  # 切替後もKP暗躍は無駄でない
            # ★A-20：char-other（非KP/キラー）は reveal_cost 設計で既にリーク会計済み＝据置
            #   （D5違反はボード暗躍のみ。char anyaku を落とすと reveal_cost 機構のテスト前提を壊す）。
            return self.p["ab_anyaku_other"] - reveal_cost
        if o["kind"] == "unrest_minus":
            # 医者（脚本家使用）：抑制モードで臨界間際の犯人を冷やす（事件＝情報流出の防止）
            tgt = o["target"]
            c = a["chars"].get(tgt)
            th = unrest_threshold_of(tgt)
            # ★A-5(b)：医者の冷やしも upcoming 犯人だけ（事件日が過ぎた確定犯人は無意味・上と同根）。
            if (a["locked"] and c
                    and (tgt == a["today_culprit"] or tgt in a["future_culprits"])
                    and th is not None and c["unrest"] >= max(1, th - 1)):
                return self.p["ab_cool_locked"]
            return self.p["ab_weak"]
        if o["kind"] == "unrest":
            # ミスリーダー：犯人の不安を臨界へ運ぶときだけ使う。
            # ★無駄撃ちは「同エリアにミスリーダー」の位置情報＝世界線を絞らせるのでパスに劣後。
            # ★抑制モード（勝ち確定時）は事件を起こさない＝常にパス劣後。
            if a["locked"]:
                return self.p["ab_weak"]
            tgt = o["target"]
            c = a["chars"].get(tgt)
            th = unrest_threshold_of(tgt)
            below = c and th is not None and c["unrest"] < th
            # ★打点会計：臨界に届かない事件の犯人へは注がない（無駄＝位置情報だけ漏らす）。
            if tgt == a["today_culprit"] and below:
                return (self.p["ab_unrest_today"] if tgt in a["reachable_culprits"]
                        else self.p["ab_unrest_today_far"])
            if tgt in a["future_culprits"] and below:
                return (self.p["ab_unrest_future"] if tgt in a["reachable_culprits"]
                        else self.p["ab_unrest_future_far"])
            # ★帰属不能の撃ち得（カバーシナリオ・2026-07-06）：医者が対象と同エリアに
            #   居るなら、この不安+1は「医者の脚本家フェイズ能力」と区別できない＝
            #   ミスリーダーの位置情報にならない。情報コスト0なので霧まき（eligible拡張）に使う。
            doc = a["chars"].get("医者")
            if (doc and doc["alive"] and c and c["alive"] and doc["area"]
                    and doc["area"] == c["area"]
                    and th is not None and c["unrest"] + 1 >= th):
                return self.p["ab_unrest_cover"]
            return self.p["ab_weak"]
        return 0

    # -- ターン終了・事件・ループ開始 -----------------------------------------

    def _belief_for_leak(self, a: dict):
        """A-49用 Belief（クロマク非依存）を遅延構築してキャッシュ。

        既存の `_belief_base`（クロマクの reveal_cost 用・belief_mat 由来）とはキーを分ける＝
        既存経路のキャッシュを汚さない。帰結確定局面でしか呼ばれないので実質ゼロコスト。
        """
        if "belief_leak" not in a:
            a["belief_leak"] = a.get("belief_base")
            if a["belief_leak"] is None:
                mat = a.get("belief_mat_all")
                try:
                    from agents.belief import Belief
                    b = Belief(mat["cast"], mat["inc"], mat["set_name"])
                    b.observe(mat["history"])
                    a["belief_leak"] = b
                except Exception:  # noqa: BLE001  belief不能＝リーク判定不能
                    a["belief_leak"] = None
        return a["belief_leak"]

    def _optional_leaks_role(self, o: dict, a: dict) -> bool:
        """A-49：この【任意】発動が**発動キャラの役職を新たに漏らすか**。

        主人公死亡は「その瞬間の暗躍/不安」を公開で添えて解決される（sim/effects.kill_protagonists）＝
        belief はそこからキラー（自暗躍≥4）／メインラバーズ（不安≥3＋暗躍≥1）を絞り込む。よって
        発動＝ほぼ役職の献上。ただし**既に役職が確定済み（role_gini==0）なら漏らすものが無い**＝
        抑制しない（A-48のリーク弁と同型＝情報コスト会計はコストゼロを会計しない）。
        belief 構築不能でも抑制しない＝判別できない文脈では旧挙動（健全側）。
        """
        action = o.get("action") or ""
        if ":" not in action:
            return False
        actor = action.split(":", 1)[1]
        b = self._belief_for_leak(a)
        if b is None:
            return False
        try:
            return b.role_gini(actor) > 0.0
        except Exception:  # noqa: BLE001  判定不能＝抑制しない
            return False

    def _score_turn_end(self, o: dict, a: dict) -> float:
        # 殺害系は全て勝ちに直結（主人公殺害/敗北＞キーパーソン殺害）。パスは避ける。
        if o.get("action") == "pass":
            return 0
        # ★A-49：このループの帰結が既に確定しているなら、【任意】発動は**結果を1ミリも動かさず**
        #   発動キャラの役職だけを献上する（棋譜実測＝殺人計画seed0 L2D2：フレンド死亡で敗北確定後に
        #   キラーの主人公殺害を発動→委員長＝キラーを主人公に確定させ、残り2ループを丸裸で戦った）。
        #   得られるのは「ループを数日早く畳む」だけ＝残り日数の観測を減らす微益で、恒久的な役職
        #   バレとは釣り合わない。健全側の弁2つ（A-48で確立した型）：
        #   (1) 確定弁＝「確実に確定」と言える時だけ（_analyze の loop_defeat_certain＝不可逆な2条件）
        #   (2) リーク弁＝役職が未確定の時だけ（確定済みなら漏らすものが無い＝早期終了の微益を取る）
        #   ※(1) を先に見る＝belief の遅延構築は帰結確定局面でしか走らない。
        if (self.p["loop_decided_optional_gate"]
                and a["loop_defeat_certain"]
                and self._optional_leaks_role(o, a)):
            return self.p["te_decided_leak"]
        return 100 if o.get("target") in ("主人公", "主人公敗北") else 90

    def _score_incident(self, o: dict, a: dict) -> float:
        tgt = o.get("target")
        # 行方不明などボード選択：ゴールボードへ動かして暗躍を足す
        if tgt in AREAS:
            return (self.p["inc_board_goal"] if tgt in a["goal_boards"]
                    else self.p["inc_board_other"])
        # キーパーソン殺害（勝ち）＞フレンド殺害（ループ終了時敗北）＞脅威キャラ排除。
        # ★未来の犯人は脅威でも殺さない（自分の事件を消してしまう）＝回避を脅威より先に判定。
        if tgt == a["keyperson"]:
            return self.p["inc_kp"]
        if tgt in a["friends"]:
            return self.p["inc_friend"]
        if tgt in a["future_culprits"]:
            return self.p["inc_future_culprit"]  # 自分の未来の犯人は殺さない（事件を維持）
        if tgt in a["threats"]:
            return self.p["inc_threat"]  # 巫女・神格・刑事の対抗手段を先に消す
        return self.p["inc_other"]

    def _score_loop_area(self, o: dict, view: dict) -> float:
        # 手先の初期エリア。復讐者の灯火で手先＝クロマクならボードXになるので、
        # 病院の事件があれば病院（勝ち筋を集中）、無ければ学校を選ぶ。
        area = o.get("area")
        if view["rule_y"] == "復讐者の灯火":
            want = "病院" if any(i["name"] == "病院の事件" for i in view["incidents"]) else "学校"
            return 10 if area == want else 1
        return 1
