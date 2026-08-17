# -*- coding: utf-8 -*-
"""B-221：反復非適応ブレーカ（同型敗北を同じ応手でなぞり続ける形を切る）。

## 何をするモジュールか（起票源＝B-220 検死・§72-5／§72-6）

主人公AIは「フレンド死亡線を 6〜8 ループ同じ応手でなぞって全敗」する
（B-220 の行為指標＝連続敗北ループ対で応手完全一致＝3日級7対・5日級6対、
うち 11/13 対が非防衛局に集中）。これはユーザー（人間）が毎回実証してきた
「L1 で負けて L2 で応手を変える」の欠落そのもの（§70）。

本モジュールは**公開情報のみ**から

  「直前ループが敗北で、同じ署名（公開の敗北の型）の敗北が続いており、
   （既定では）その中で応手の完全一致＝反復が既に実測されている」

ときに、**その敗北の決定打日に自チームが置いた手（＝負け続けた応手）の集合**を返す。
呼び手（`heuristic_protagonist`）はこの集合の手に採点上限（`B221_CAP`）を被せる＝
「敗因に寄与した自手の点を下げる」（チケット §3-2 の設計方針）。

## Phase 0 の一次データが示した4局の機序（`docs/仮_b221_log/why_*.json`）

- `btx_future#4`／`random_BTX#0`＝**先席のピン（移動禁止→犯人候補 102.5／185.0）が
  対象キャラを占有**し（重ね置き不可＝`sim/legal.py`）、後席の犯人当日冷却
  （`不安-1→犯人候補`＝B-220 の flip の中身）を構造的に締め出す。ピンは同じ負け方で
  5〜6回落ち続けても 185.0 のまま＝**「負けた実績」が採点に一切還流しない**。
- `random_FS#10`＝同じ脆い折り手（SK退避 75.0）を毎ループ選び、mm に毎回追われる
  （flip＝その手をやめるだけ＝1試行）。
- `random_BTX#12`＝脅威は立ち repeat=6・def=True だが p=0.18＜θ＝0.9 で
  **鉄則①経路（`B100_IRON_PROB`）が既定 None＝死んでいる**ため資格ゼロ
  （`b100_alloc.TRACE` の実測）。→ こちらは本モジュールでは直接救えない
  （ピンではなく「札を出さない」形）＝回避集合が応手を散らす効果に期待する側。

## 設計の規律

- 材料＝`view["history"]` の公開イベントのみ：`loop_result`（敗北）・
  `cards_revealed`（全配置の公開＝`sim/flow.py:223`）・`loss_signatures`
  （`agents/b100_mix`＝B-211 是正済み・公開情報のみ）。神視点は使わない。
- **未来を見ない**＝現在ループより前の敗北ループだけを読む。
- 発火は**末尾の連続同署名敗北 run** に限る（勝ったループ・署名が変わったループで
  自然にリセット）。`require_pair=True`（既定）は run 内に応手完全一致の隣接対が
  **実測されたときだけ**発火＝発火面積を「既に反復が起きた局」に限定する。
- 回避集合は run の**決定打日**（フレンド死亡日・決定打死者の日・発生事件日）の
  自チーム配置だけ＝負け筋に無関係な日の手は散らさない（B-217/B-218 の教訓＝
  同点帯の無差別な引き直しは信号を生まない）。
"""
from __future__ import annotations

from collections import defaultdict

from .b100_mix import loss_signatures


def team_response_by_day(history: list[dict], loop: int) -> dict[int, frozenset]:
    """ループ `loop` の**自チーム（主人公側）**の配置 {day: {(card,target,kind)}}。

    一次情報＝`cards_revealed`（両陣営の全配置が毎日公開される・`sim/flow.py:223`）。
    mastermind の配置は含めない。
    """
    out: dict[int, set] = defaultdict(set)
    for e in history:
        if e.get("event") != "cards_revealed" or e.get("loop") != loop:
            continue
        for p in e.get("placements", []) or []:
            if p.get("owner") == "mastermind":
                continue
            out[e.get("day")].add(
                (p.get("card"), p.get("target"), p.get("target_kind")))
    return {d: frozenset(s) for d, s in out.items()}


def _sig_key(s: dict) -> tuple:
    """敗北署名の同型判定キー（すべて `loss_signatures` の公開フィールド）。"""
    return (s.get("type"),
            frozenset(s.get("friend_deaths") or ()),
            frozenset(s.get("incidents") or ()),
            frozenset(s.get("boards") or ()),
            frozenset(s.get("anyaku_chars") or ()),
            frozenset(s.get("fatal_deaths") or ()))


def _decisive_days(s: dict) -> set[int]:
    """その敗北ループの**決定打日**（公開情報だけで絞れる日）。

    - フレンド死亡日（`rules/40:130`／`50:146` の敗北条件そのもの）
    - 決定打死者の日（`loop_end` と同日の死＝`fatal_deaths`）
    - 発生した事件の日
    板暗躍のみの敗北（deaths も incidents も無い）は空＝発火しない（狭く保つ）。
    """
    fd = s.get("friend_deaths") or set()
    fatal = s.get("fatal_deaths") or set()
    days = {d for (d, n) in (s.get("deaths") or ()) if n in fd or n in fatal}
    days |= {d for (d, _n) in (s.get("incidents") or ())}
    return {d for d in days if d is not None}


#: ★B-224 weak 専用＝**予防札**（毎ループ払い直すのが正しい常設防御）＝
#  手単位の反復を「非適応の証拠」に数えない（`avoid_moves(weak=True)` の絞り）。
#  完全一致（pair）側の回避集合には影響しない（応手全体の一致は依然として信号）。
_WEAK_KEEP_CARDS = frozenset({"不安-1", "暗躍禁止"})


def loss_run(history: list[dict], loop: int | None) -> tuple[list[int], dict]:
    """末尾の**連続同署名敗北 run** と署名 dict を返す（無ければ ([], sigs)）。

    ★B-224 で `avoid_moves` から切り出した共通部（挙動は同一＝純リファクタ）。
    run＝直前ループ L-1 が敗北しており、同じ署名（`_sig_key`）の敗北が
    末尾で連続している範囲 [L-1, L-2, ...]（勝ち／署名変化で自然リセット）。
    """
    if loop is None or loop < 2:
        return [], {}
    sigs = loss_signatures(history)
    prev = loop - 1
    if prev not in sigs:
        return [], sigs                # 直前ループは敗北していない（勝ち＝リセット）
    key = _sig_key(sigs[prev])
    run: list[int] = []
    lp = prev
    while lp in sigs and _sig_key(sigs[lp]) == key and lp >= 1:
        run.append(lp)
        lp -= 1
    return run, sigs


#: ★B-228：主人公側の移動札（位置系＝「同じ退避で負け続けた」の見かけを持つが、
#  犠牲者退避は常設防御でありうる＝陽性証拠が要る側）。
_MOVE_CARDS = frozenset({"移動↑↓", "移動←→", "移動斜め"})


def avoid_moves(history: list[dict], loop: int | None,
                *, require_pair: bool = True,
                weak: bool = False,
                pin_cands: dict | None = None,
                move_sk: frozenset | set | None = None) -> dict[int, frozenset]:
    """反復非適応ブレーカの回避集合 {day: {(card,target,kind)}}（発火しない時 {}）。

    発火条件：
      1. 直前ループ L-1 が敗北している。
      2. L-1 と同じ署名（`_sig_key`）の敗北が末尾で連続している（run＝L-1, L-2, ...）。
      3. `require_pair=True` のとき＝run 内の**隣接ループ対で応手が完全一致**
         （全席・全日）した対が1つ以上ある（＝反復の実測）。False＝run だけで発火。
    回避集合＝run の各ループの決定打日に自チームが置いた手の**和集合**
    （★和集合＝「前回の手だけ避ける」だと A→B→A… の振動で同じ2手を往復する。
    run が続く限り過去に負けた手を全部避ける＝ユーザーの「試していない手を試す」）。

    ★B-224 `weak=True`（既定 False＝従来と bit 同一）＝**弱い一致定義**：
      応手の完全一致（全席・全日）ではなく「**決定打日の同一手が隣接敗北ループ対の
      両方に出現**」（手単位の反復の実測）で発火し、回避集合も**その反復した手だけ**
      （隣接対の積集合の和）に絞る。`require_pair` は無視される（weak が自前の
      実測条件を持つため）。
      ★棄却済み run 型（45+30 flip）との違い＝run 型は反復の実測ゼロでも
      決定打日の手の**和集合を全部** cap する（広域引き直し）。weak は
      (i) 手単位の反復が実測された手だけを cap（⊆ run 型の集合）
      (ii) 発火にその実測を要求する。BTX#0 型（微差の応手違いで完全一致が
      立たない反復）を、敗因チャネルに載り続けた手だけ切る形で拾う。

    ★B-228 `pin_cands`（B-224 の残余＝BTX#16 絞り。None＝絞りなし＝B-224 と同一）＝
      **ピン（`移動禁止`）の cap には treadmill の陽性証拠を要求する**：
      `pin_cands`＝{day: 当該日の犯人候補集合}（belief `_culprit_cands`＝公開情報由来）
      が渡されたとき、`(移動禁止, tgt, character)` を回避集合に残すのは
      「run 署名にその日 `d` の事件**発生**が載っており、かつ tgt ∈ pin_cands[d]」
      のときだけ。機序（Phase 0 実測＝`docs/仮_b228_log/why_*.json`）：
      - `BTX#0`/`future#4`＝ピン対象（巫女/刑事）が**署名事件日の犯人候補**＝
        ピンでは事件チャネルが止まらない（ピンを置いたのに事件が発生し続けた）うえ
        当日冷却の対象を占有する＝treadmill＝cap が正解。
      - `BTX#16`＝ピン対象（教祖）は犯人候補でない（D1 の候補＝黒猫）＝ピンは
        SK 配達（mm の 移動↑↓/←→/斜め→教祖）を毎日消す**位置系の常設防御**＝
        予防札と同類＝cap すると mm が即 `移動斜め→教祖` で友好死線を通す
        （rev: 6[def]→9[fb_win]・h1: 6[def]→9[fb_loss]）。
      ★SK belief では判別**できない**（教祖も刑事も SK 候補）＝判別するのは
      「犯人候補×署名事件日」の交差だけ（事前登録＝`docs/仮_b228_log/事前登録_B228.md`）。

    ★B-228 `move_sk`（同上・None＝絞りなし）＝**移動札の cap にも陽性証拠を要求**：
      `(移動↑↓/←→/斜め, tgt, character)` を回避集合に残すのは tgt ∈ move_sk
      （belief の SK 候補 `_sk_cands`＝p≥0.15）のときだけ。機序（Phase 0 追実測）：
      - `FS#10`＝毎ループ反復した移動の対象（男子学生）は **SK 候補**＝動かすたびに
        2人きりを自作して追われる treadmill＝cap が正解（weak の回収の実体）。
      - `BTX#16`＝反復移動の対象（アルバイト）は SK 候補でない**反復犠牲者**（run 署名の
        deaths に毎ループ載る）＝退避の常設防御。score 同点（5.5）でも回避集合に
        載ると B-100 の折り手遮断（`b221_break_blocked`）で退避の向きが化け
        （移動↑↓→移動←→）、off が L6 で防衛する軌道に戻れない（実測）。
    """
    run, sigs = loss_run(history, loop)
    if not run:
        return {}
    resp = {l: team_response_by_day(history, l) for l in run}
    if weak:
        # ★B-228：run 署名に発生として載っている事件の日（ピン絞りの証拠側）。
        inc_days = {dd for lp2 in run
                    for (dd, _n) in (sigs[lp2].get("incidents") or ())
                    if dd is not None}
        out_w: dict[int, set] = defaultdict(set)
        for a in run:
            if (a - 1) not in run:
                continue
            days = _decisive_days(sigs[a]) & _decisive_days(sigs[a - 1])
            for d in days:
                both = (set(resp[a].get(d, ()))
                        & set(resp[a - 1].get(d, ())))
                # ★予防札（`_WEAK_KEEP_CARDS`）は手単位の反復を「非適応の証拠」に
                #   数えない＝回避集合に入れない。機序（B-224 d5 実測）＝板ガード
                #   `暗躍禁止→神社`（BTX#18）や冷却 `不安-1→手先`（BTX#11/18）は
                #   **毎ループ払い直すのが正しい常設防御**で、反復して当然。cap すると
                #   防衛ごと失う（6[def]→9）。対して位置系（移動禁止ピン・移動）と
                #   注入・投資は「同じ手で負け続けた」が信号になる（BTX#0 のピン・
                #   FS#10 の自傷移動＝weak の回収はどちらも位置系）。
                both = {m for m in both if m[0] not in _WEAK_KEEP_CARDS}
                # ★B-228：ピン（移動禁止）は treadmill の陽性証拠（署名事件日×
                #   犯人候補）があるときだけ cap＝それ以外は常設防御として残す。
                if pin_cands is not None:
                    both = {m for m in both
                            if m[0] != "移動禁止"
                            or (d in inc_days
                                and m[1] in (pin_cands.get(d) or ()))}
                # ★B-228：移動札は対象が SK 候補（自作 treadmill の陽性証拠）の
                #   ときだけ cap＝犠牲者退避（対象が SK 候補でない）は切らない。
                if move_sk is not None:
                    both = {m for m in both
                            if m[0] not in _MOVE_CARDS or m[1] in move_sk}
                if both:
                    out_w[d] |= both
        return {d: frozenset(v) for d, v in out_w.items() if v}
    if require_pair:
        ok = any(resp.get(a) and resp.get(a) == resp.get(a - 1)
                 for a in run if (a - 1) in run)
        if not ok:
            return {}
    out: dict[int, set] = defaultdict(set)
    for l in run:
        days = _decisive_days(sigs[l])
        if not days:
            continue
        for d in days:
            out[d] |= set(resp[l].get(d, ()))
    return {d: frozenset(v) for d, v in out.items() if v}


def b221_break_blocked(agent, card: str, target: str, kind: str) -> bool:
    """B-100 の折り手選択用：この折り手が回避集合に入っているか（既定 OFF＝False）。

    `keys`（被覆判定）からは外さない＝「誰かが打ったら被覆」は従来どおり。
    この席から**同じ負け手を強制しない**だけ（`here`／cands 側だけを絞る）。

    ★`B221_BLOCK_BREAKS`（既定 True）＝この遮断そのものの切替口。
    対照実測（`docs/仮_b221_log/abS_d[35].txt`）＝False（採点 cap のみ）でも
    両ベンチ per-game は True と**完全同一**＝現行ベンチでは挙動を分けない。
    True を既定に保つ理由＝「負け続けた手を B-100 が最安 break として強制し直す」
    逆流（原理上ありうる形）を構造的に塞ぐ保険。
    """
    if not getattr(agent, "B221_BREAKER", False):
        return False
    if not getattr(agent, "B221_BLOCK_BREAKS", True):
        return False
    av = getattr(agent, "_b221_avoid_today", None)
    return bool(av) and (card, target, kind) in av
