# -*- coding: utf-8 -*-
"""経過（公開情報）の共通レンダラ（テスター要望 2026-07-13：両モードで表記を統一）。

主人公プレイ(arena/play.py)・脚本家プレイ(arena/play_vs_ai.py)の「経過」を同一表記にする：
- **フェイズ見出し** `〘…〙`（どのフェイズの出来事か）＋空の能力フェイズは「発動無し」。
- **絵文字**つき1行（💀死亡/⚡事件/💚友好能力…）。
- **新規イベントの赤下線** 🆕（フェイズ送りで新たに現れた公開情報だけ）。
- **tight spacing**：1日分を単一 markdown 文字列で返す（st.markdown を1回で描く＝行間が詰まる）。

Streamlit非依存＝テスト可能。1日分（render_day_md）を単位にして、呼び出し側は過去ループの
畳み込み（expander）や日ごとのループ/日見出しを自由に組める（play.py の構造を壊さない）。
"""

from __future__ import annotations

from arena.replay import describe_event

_EV_PHASE_JP = {
    "loop_start": "ループ開始", "loop_start_area": "ループ開始",
    "mastermind_set": "脚本家行動フェイズ", "protagonist_set": "主人公行動フェイズ",
    "action_resolution": "行動解決フェイズ", "mastermind_ability": "脚本家能力フェイズ",
    "goodwill_ability": "主人公能力フェイズ", "goodwill_refuse": "主人公能力フェイズ",
    "incident": "事件フェイズ", "leader_change": "リーダー交代",
    "turn_end": "ターン終了フェイズ", "final_battle": "最後の戦い", "": "ループ終了",
}
_PHASE_ORD = ["loop_start", "loop_start_area", "mastermind_set", "protagonist_set",
              "action_resolution", "mastermind_ability", "goodwill_ability",
              "goodwill_refuse", "incident", "leader_change", "turn_end",
              "final_battle", ""]

# phase 欄が無いイベント（loop_start/loop_result 等）のフェイズ推定。
_EVENT_PHASE = {"loop_start": "loop_start", "scholar_trait": "loop_start",
                "final_battle": "final_battle"}


def _phase_of(e: dict) -> str:
    return e.get("phase") or _EVENT_PHASE.get(e.get("event"), "")

# 経過に出す公開イベント（生の不安/暗躍デルタ等の冗長イベントは出さない＝tight）。
_SHOWN = {"loop_start", "cards_revealed", "move_blocked", "death", "protagonist_death",
          "death_prevented", "protagonist_death_prevented", "protagonist_immortal",
          "guard_consumed", "revive", "goodwill_used", "goodwill_refused",
          "goodwill_resolved", "incident", "incident_effect",
          "role_reveal", "culprit_reveal", "rule_reveal",
          "loop_end", "loop_result", "final_battle",
          "entry", "scholar_trait", "goshinboku_move"}


def describe_event_emoji(e: dict) -> str:
    """1イベントを絵文字つき短文に（脚本家プレイの表記を正準に統一）。

    ★A-26（2026-07-16）：死亡行に「→ 即ループ終了」等の因果注記は付けない（end_effect 引数は廃止）。
    ループが終わった事実は 🔚 行が単独で担う＝原因の推論材料をUIが与えない。
    未対応イベントは describe_event（絵文字なし）にフォールバック。"""
    ev = e.get("event", "")
    nm = e.get("name", "")
    if ev == "loop_start":
        return f"ループ{e.get('loop')}開始"
    if ev == "cards_revealed":
        parts = [f'{"脚" if p.get("owner") == "mastermind" else p.get("owner")}:'
                 f'{p.get("card", "？")}→{p.get("target")}'
                 for p in e.get("placements", [])]
        return "🃏 全カード公開：" + "　".join(parts)
    if ev == "move_blocked":
        where = f"禁止エリア〈{e['area']}〉へ" if e.get("area") else "禁止エリアへ"
        return f"🚧 {nm} は{where}移動できず不成立（その場に留まった）"
    if ev == "death":
        # ★A-26（差し戻し反映 2026-07-16）：「（→ 即ループ終了・巻き戻り）」の因果注記は全廃。
        #   病院の事件で「病院全員死亡＋主人公殺害」の日、実ゲームではKPの在不在が分からなくなるのが
        #   正しいのに、注記は推論材料を余計に与える。かつ同日の非同時死亡にも一律に付く因果誤表示。
        #   ループが終わった事実は 🔚 単独行だけで足りる（単一死亡からの推論はプレイヤーが行う＝UIは代行しない）。
        return f"💀 {nm} 死亡"
    if ev == "protagonist_death":
        # ★A-26：公開イベントは cause 無し設計＝空括弧「（）」を出さない。
        return "☠️ 主人公死亡"
    if ev == "death_prevented":
        return f"🛡 {nm} は死亡しなかった（不死）"
    if ev == "protagonist_death_prevented":
        return "🛡 主人公は死亡しなかった（軍人の不死）"
    if ev == "protagonist_immortal":
        return "🛡 このループ中、主人公は死亡しなくなった（軍人）"
    if ev == "guard_consumed":
        return f"🛡 {nm} の護衛カウンターが身代わりになった"
    if ev == "revive":
        return f"✨ {nm} が蘇生した"
    if ev == "goodwill_used":
        who, ab = e.get("character", ""), e.get("ability", "")
        tgt = e.get("target", "")
        # ★B-278：医者『不安操作（除去/付与）』は [主] の宣言に除去/付与が含まれる（KB 20:228）＝
        #   拒否より前の公開情報なので経過にも出す。
        _md = {"remove": "除去", "add": "付与"}.get(e.get("mode"))
        return (f"💚 {who} が友好能力『{ab}』" + (f" → {tgt}" if tgt else "")
                + (f"（宣言：不安を1つ{_md}）" if _md else ""))
    if ev == "goodwill_refused":
        # A-13：拒否は役職推理の一次公開情報（拒否できる＝友好無視系の役職の証拠）。
        return f"　└ ✗ 脚本家が拒否（{e.get('character', '')}は友好無視系の役職＝拒否できる）"
    if ev == "goodwill_resolved":
        # A-13：解決＝拒否されず効果適用（＝その役職は友好無視を持たない、の証拠）。
        return "　└ ✓ 解決（脚本家は拒否せず・効果が適用された）"
    if ev == "incident":
        return f"⚡ 事件『{nm}』{'発生' if e.get('occurs') else '不発'}"
    if ev == "incident_effect":
        return f"⚡ 事件『{nm}』：{e.get('note', '')}"
    if ev == "role_reveal":
        return f"🔓 {nm}＝{e.get('role', '')} が判明"
    if ev == "culprit_reveal":
        return f"🕵 {e.get('day')}日目の事件の犯人＝{nm} が判明"
    if ev == "rule_reveal":
        # ★B-29x（2026-09-02・トリアージ A-2(b)）：情報屋の友好能力でルールXが1つ開示される
        #   （KB: 20:176）。履歴には載っていたのに**両モードの経過に出ていなかった**（実バグ）。
        #   出すのは「開示された名前」だけ＝脚本の真実のうち**公開された分**（他の秘匿は漏らさない）。
        return f"📜 ルールXの一つ＝{e.get('rule_x', '')} が判明"
    if ev == "loop_end":
        # ★A-26：即時終了（ループ終了効果の成立＝KP/主人公死亡・TT任意敗北等）。原因のキャラ名・
        #   役職・条件名は絶対に出さない（reason は神視点＝同時複数死亡時にKP特定情報が漏れる）。
        #   タイミング（この位置＝ループが即時に打ち切られた）だけで区別する（FableA裁定 2026-07-16）。
        return "🔚 ループ即時終了（ループ終了効果の成立）"
    if ev == "loop_result":
        # ★A-26：ボード系＝最終日まで進みループ終了時の敗北判定で敗北（原因＝ボード/条件は伏せる）。
        #   即時終了(loop_end)と同ターンに出る場合は render_day_md で抑制＝二重表示を避ける。
        return "🔚 ループ終了時の敗北判定：敗北"
    if ev == "final_battle":
        return f"⚔ {e.get('result', e.get('winner', ''))}"
    if ev == "entry":
        return f"🚪 {nm} が {e.get('area')} に登場"
    if ev == "scholar_trait":
        return f"🎓 学者に{e.get('counter', '')}カウンター（【強制】）"
    if ev == "goshinboku_move":
        # A-6②：ご神木の特性（主人公能力フェイズ・カウンターを同エリアへ移す）を経過に載せる。
        return (f"🌳 ご神木の特性：{e.get('counter', '')}カウンターを "
                f"{e.get('from', 'ご神木')} → {e.get('to', '')}（同エリア内）")
    if ev == "unrest":
        # A-16：脚本家能力フェイズの結果（結果のみ・能力名/役職は伏せる＝役職バレ回避）。
        return f"😰 {e.get('target', '')} に不安{e.get('delta', 0):+d}"
    if ev == "anyaku":
        # A-16：脚本家能力フェイズの結果（結果のみ・能力名/役職は伏せる）。
        return f"🕶 {e.get('target', '')} に暗躍{e.get('delta', 0):+d}"
    return describe_event(e)   # 未対応は絵文字なしフォールバック


def render_day_md(hist: list[dict], lp: int, dy: int, *,
                  view_len: int | None = None, seen_len: int = 0,
                  reached=None) -> str:
    """(lp,dy) の可視イベントを、フェイズ見出し〘…〙＋絵文字＋新規🆕赤下線つきの
    単一 markdown 文字列にする（両モード共通・tight spacing）。

    view_len＝この index 未満のイベントだけ可視（レビュー中の先読み防止・None=全部）。
    seen_len＝この index 以降が「新規」＝🆕赤下線を付ける（フェイズ送りの新着表示）。
    reached(after_point)->bool＝空の能力フェイズを「発動無し」と出すか（そのフェイズに到達済みか）。
      None なら到達済み扱い（全能力フェイズの発動無しを出す）。
    """
    _vl = len(hist) if view_len is None else view_len
    day_evs = [(i, e) for i, e in enumerate(hist)
               if (e.get("loop"), e.get("day")) == (lp, dy) and i < _vl]
    # ★フェイズが「活動したか」は全イベントで判定（生の暗躍/不安デルタ等・非表示も含む）。
    #   一方で表示は _SHOWN のみ（tight）＝暗躍-only の能力フェイズは無音（「発動無し」も出さない）。
    active_phases = {_phase_of(e) for _i, e in day_evs}
    # ★A-16：脚本家能力フェイズの結果（不安/暗躍のカウンター変化）だけは経過に出す＝盤上で見える
    #   公開情報。能力名・使用役職は絶対に出さない（役職バレ）＝対象＋結果のみ（describe_event_emoji
    #   の unrest/anyaku は能力名を含まない）。他フェイズの unrest/anyaku（行動解決＝カード公開で
    #   既出・冗長）は従来どおり無音＝mastermind_ability に限って表示する。
    # ★A-26：この日に即時終了(loop_end＝ループ終了効果)があるか。ある日は loop_result（ボード系の
    #   敗北判定行）を抑制＝「🔚 ループ即時終了」と「🔚 ループ終了時の敗北判定」の二重表示を避け、
    #   (a)即時／(b)ボード を相互排他に見せる（原因は両方とも伏せる）。
    _le_idx = [i for i, e in day_evs if e.get("event") == "loop_end"]
    _has_loop_end = bool(_le_idx)
    # ★A-26（差し戻し反映 2026-07-16）：loop_end は1日に複数発火しうる（例：病院の事件で
    #   「KP死亡→loop_end」「主人公死亡→loop_end」＝simが事件効果の死亡を逐次解決するため）。
    #   全部出すと 🔚 の挿入位置が「どの死亡がループを終わらせたか」を名指しする＝除去した因果注記と
    #   同じ漏洩になる（実卓では事件の死亡は同時＝どれが原因かは分からないのが正しい）。
    #   その日の最後の1本だけ残す＝フェイズ（観測可能なタイミング）は保ちつつ、位置による特定を消す。
    _le_keep = _le_idx[-1] if _le_idx else None
    evs = [(i, e) for i, e in day_evs
           if (e.get("event") in _SHOWN
               or (e.get("event") in ("unrest", "anyaku")
                   and e.get("phase") == "mastermind_ability"))
           and not (e.get("event") == "loop_result" and _has_loop_end)
           and not (e.get("event") == "loop_end" and i != _le_keep)]
    groups: dict = {}
    for i, e in evs:
        groups.setdefault(_phase_of(e), []).append((i, e))

    def _reached(after: str) -> bool:
        return True if reached is None else bool(reached(after))

    # 空でも「到達済みなら発動無し」を出す能力フェイズ＝**全く活動が無かった**時だけ（暗躍-only等で
    #   活動があった場合は「発動無し」を出さない＝従来 play_vs_ai の mm_active/gw_active と同義）。
    phases = set(groups)
    for _ap, _after in (("mastermind_ability", "脚本家能力フェイズ後"),
                        ("goodwill_ability", "主人公能力フェイズ後")):
        if _ap not in active_phases and _reached(_after):
            phases.add(_ap)

    def _bullet(i: int, e: dict) -> str:
        body = describe_event_emoji(e)
        if i >= seen_len:   # 新規＝赤下線＋🆕（Streamlit は unsafe_allow_html で描く）
            return f'- <span style="border-bottom:2px solid #e2483d;">{body}</span> 🆕'
        return f"- {body}"

    out: list[str] = []
    for ph in sorted(phases, key=lambda p: _PHASE_ORD.index(p)
                     if p in _PHASE_ORD else 90):
        if ph == "turn_end":
            continue   # ターン終了は死者リストとして下でまとめる
        out.append("")                                   # 見出し前の空行（tightだが独立ブロック）
        out.append(f"**〘{_EV_PHASE_JP.get(ph, ph)}〙**")
        out.append("")
        if ph in groups:
            out += [_bullet(i, e) for i, e in groups[ph]]
        elif ph == "mastermind_ability":
            out.append("- 🧙 脚本家能力発動無し")
        elif ph == "goodwill_ability":
            out.append("- 💚 主人公能力発動無し")
    # ターン終了フェイズ：個別イベント＋死者リスト（到達済みなら死者なしも明示）。
    te = groups.get("turn_end", [])
    if te or _reached("ターン終了フェイズ後"):
        out.append("")
        out.append("**〘ターン終了フェイズ〙**")
        out.append("")
        out += [_bullet(i, e) for i, e in te]
        names = [f'〈{e.get("name")}〉' for _i, e in te if e.get("event") == "death"]
        if any(e.get("event") == "protagonist_death" for _i, e in te):
            names.append("主人公")
        out.append(f'- 死者：{("、".join(names)) if names else "死者なし"}')
    return "\n".join(out).strip("\n")
