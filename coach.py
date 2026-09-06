# -*- coding: utf-8 -*-
"""先生AI（コーチ）— 主人公AIの評価を初心者向けの日本語アドバイスに翻訳する。

オンボーディング（プランⅢ）の中核部品。一人回し（arena/play.py）の人間の手番で
「AIの推奨手＋理由＋AIの推理」を表示するために使う。streamlit非依存の純関数＝テスト可能。

仕組み：agents.debug.ProbedProtagonist（挙動同一の全候補スコア記録）を局面単位で単発起動。
主人公AIは view（公開情報のみ）から信念を再構築するため、進行中の対局の任意の
決定点 (view, decision, options) を渡すだけでよい＝ゲーム側の状態を汚さない。

正直さの原則：
- 推奨は「現行ヒューリスティックAIの評価」であり正解の保証はない（表示にも明記）。
- 理由テンプレートが無い手は無理にこじつけず、スコア順位だけを示す。
- AIの推理（容疑者リスト等）は公開情報からの推定＝ネタバレではない。

統合（play.py 側・並行作業の終息後に1〜3行）:
    from coach import advise, format_advice
    adv = advise(p.view, p.decision, p.options)
    st.markdown("\\n".join(format_advice(adv)))
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.board import AREAS
from engine.data import goodwill_abilities_of, unrest_threshold_of

_CARD_TOGGLE = {"移動←→": (1, 0), "移動↑↓": (0, 1), "移動斜め": (1, 1)}


def _move_dest(area: str | None, card: str) -> str | None:
    t = _CARD_TOGGLE.get(card)
    if not t or area not in AREAS:
        return None
    ax, ay = AREAS[area]
    want = (ax ^ t[0], ay ^ t[1])
    return next((a for a, xy in AREAS.items() if xy == want), None)


@dataclass
class CoachAdvice:
    decision: str
    recommended: dict | None                 # AIが選ぶ手（optionsの要素）
    ranked: list = field(default_factory=list)   # [(score, option)] 降順・上位のみ
    reasons: list[str] = field(default_factory=list)   # 推奨手の理由（テンプレート）
    insights: list[str] = field(default_factory=list)  # AIの推理（容疑者等・公開情報由来）


# ---------------------------------------------------------------------------
# 理由テンプレート（推奨手 → 初心者向けの日本語）
# ---------------------------------------------------------------------------

def _char_of(view: dict, name: str) -> dict | None:
    return next((c for c in view.get("characters", []) if c["name"] == name), None)


def _reasons_for(view: dict, decision: str, opt: dict, est: dict) -> list[str]:
    out: list[str] = []
    kp = est.get("_keyperson")
    defeat_board = est.get("_observed_defeat_board")

    if decision == "set_card" and "card" in opt:
        card, tgt = opt["card"], opt.get("target")
        if card == "暗躍禁止":
            out.append(f"「{tgt}」に置かれる暗躍カウンターを全て打ち消します（守りの要）。")
            if tgt == defeat_board:
                out.append(f"{tgt}は前のループで敗北に関わったボード＝最優先の守り所です。")
            if tgt == kp:
                out.append(f"{tgt}はキーパーソン候補（AIの推理）＝暗躍2で殺害されうるため守ります。")
        elif card in ("友好+1", "友好+2"):
            c = _char_of(view, tgt)
            abilities = goodwill_abilities_of(tgt) or []
            if c is not None and abilities:
                g = c.get("goodwill", 0)
                nxt = min((a for a in abilities if a["hearts"] > g),
                          key=lambda a: a["hearts"], default=None)
                if nxt:
                    gain = int(card[-1])
                    left = max(0, nxt["hearts"] - g - gain)
                    out.append(
                        f"{tgt}の友好能力『{nxt['name']}』（必要友好{nxt['hearts']}）の"
                        f"解禁に近づきます（この手の後 残り{left}）。")
            # ★冷却役への投資＝事件を止める定石（テスター知見・手練れの開幕）。安い不安除去を
            #   早めに解禁すると、以後毎ターン犯人を冷やして事件を止められる。
            if _is_cooler(tgt) and view.get("incidents"):
                out.append(f"〈{tgt}〉の不安除去は犯人を冷やして事件を止める『冷却役』＝"
                           "早めに解禁するのが手練れの定石です（安い冷却役への投資が効きます）。")
        elif card == "不安-1":
            th = unrest_threshold_of(tgt)
            c = _char_of(view, tgt)
            if th is not None and c is not None:
                out.append(
                    f"{tgt}の不安を臨界（{th}）から遠ざけます。犯人の不安が臨界以上だと"
                    "事件が発生するため、事件を止める基本手です。")
        elif card == "移動禁止":
            out.append(f"{tgt}に置かれた移動カードを打ち消し、その場に留めます"
                       "（危険地帯への連行やシリアルキラーとの2人きりを防ぐ）。")
        elif card in _CARD_TOGGLE:
            c = _char_of(view, tgt)
            dest = _move_dest(c.get("area") if c else None, card)
            if dest:
                out.append(f"{tgt}を{dest}へ動かします（退避・隔離・能力の射程合わせ）。")
        elif card == "不安+1":
            out.append("不安を足すのは珍しい手ですが、反応を見る実験や霧まきに使われます。")
    elif decision == "goodwill_ability":
        if opt.get("action") == "pass":
            out.append("今は友好能力を使わず温存します。")
        elif "ability" in opt:
            ch, ab = opt.get("character"), opt.get("ability")
            info = next((a for a in (goodwill_abilities_of(ch) or [])
                         if a["name"] == ab), None)
            if info:
                opl = "・1ループ1回" if info["once_per_loop"] else ""
                tail = f"（必要友好{info['hearts']}{opl}）"
            else:
                tail = ""
            out.append(f"{ch}の『{ab}』を使います{tail}。")
            if "開示" in (ab or "") or "犯人" in (ab or "") or "ルール" in (ab or ""):
                out.append("情報を開く能力＝配役・脚本を絞る最重要リソースです。")
            if "除去" in (ab or ""):
                out.append("カウンターを取り除き、敗北条件や殺害条件の進行を巻き戻します。")
    elif decision == "final_battle_guess":
        out.append("最後の戦い：全キャラの役職を当てれば逆転勝利。"
                   "AIの推理（下の容疑者リスト）を参考に。")
    return out


def _insights(est: dict) -> list[str]:
    """AIの内部推定（公開情報からの推理）を初心者向けの行に。"""
    label = {
        "_keyperson": "キーパーソン候補", "_killer_suspects": "キラー容疑",
        "_kuromaku_suspects": "クロマク容疑", "_cultist_suspects": "カルティスト容疑",
        "_sk_suspects": "シリアルキラー容疑",
        "_observed_defeat_board": "前ループの敗北関連ボード",
    }
    out = []
    for k, name in label.items():
        v = est.get(k)
        if v in (None, [], False, ""):
            continue
        if isinstance(v, list):
            if len(v) >= 6:  # 絞れていない容疑者リストはノイズ＝要約
                v_txt = f"まだ絞れていない（{len(v)}人）"
            else:
                v_txt = "・".join(v)
        else:
            v_txt = str(v)
        out.append(f"{name}: {v_txt}")
    return out


def _is_cooler(name: str) -> bool:
    """安い冷却役＝不安除去/操作の友好能力を必要友好2以下で持つ（事件を止める定石の的）。"""
    for ab in goodwill_abilities_of(name) or []:
        if ("不安" in ab["name"] and ("除去" in ab["name"] or "操作" in ab["name"])
                and ab["hearts"] <= 2):
            return True
    return False


def strategy_tips(view: dict, set_name: str = "BTX") -> list[str]:
    """脚本の顔ぶれ（公開情報）から、主人公の開幕方針の助言を返す（テスター知見の主人公版）。

    ネタバレなし＝登場キャラの能力・予定事件だけから。play のループ開始時に一度出すと良い。
    """
    tips: list[str] = []
    chars = view.get("characters") or []
    incidents = view.get("incidents") or []

    # ① 冷却役の早期投資（cooling_pressure の主人公版）：安い不安除去は事件の直接対抗手段。
    coolers = [c["name"] for c in chars if c.get("alive", True) and _is_cooler(c["name"])]
    if coolers and incidents:
        tips.append("🧊 **冷却役の早期投資**：〈" + "〉〈".join(coolers) + "〉は友好+2一発で"
                    "解禁でき、以後毎ターン犯人を冷やして事件を止められます"
                    "（手練れの定石＝ここに早めに友好を注ぐ）。")

    # ② 情報源（役職開示）を並行で育てる：友好禁止で1つ遅らされても別の源が残るように。
    revealers = [c["name"] for c in chars if c.get("alive", True)
                 and any(("開示" in ab["name"] or "犯人" in ab["name"] or "ルール" in ab["name"])
                         for ab in (goodwill_abilities_of(c["name"]) or []))]
    if len(revealers) >= 2:
        tips.append("🔎 **情報源は並行で**：〈" + "〉〈".join(revealers) + "〉が開示系＝"
                    "脚本家は友好禁止で1つずつしか止められません。複数を同時に育てると"
                    "配役を絞り切れます。")

    # ③ 事件の予定（公開）を意識：近い事件日は犯人候補の不安を先回りで冷やす。
    if incidents:
        days = "・".join(f"{i['day']}日目「{i['name']}」" for i in sorted(
            incidents, key=lambda x: x.get("day", 0)))
        tips.append(f"📅 **予定事件**：{days}。その日の直前に不安の高いキャラが犯人候補＝"
                    "不安-1で先回りして止めます。")
    return tips


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

def advise(view: dict, decision: str, options: list[dict],
           seed: int = 0, top: int = 3,
           team_cards_this_turn: list[str] | None = None) -> CoachAdvice:
    """決定点 (view, decision, options) にAIコーチの評価を返す。

    view は主人公視点（公開情報のみ）であること＝arena.interactive の PendingHuman.view や
    play.py の protagonist_view をそのまま渡す。ゲーム状態は変更しない。

    team_cards_this_turn: このターンに味方（他の主人公席）が既にセットしたカード名。
    人間が3席を全部操作する一人回しでは人間はこれを知っている（viewは席間で伏せるが）。
    ★渡さないと「暗躍禁止を2枚＝自滅」を席をまたいで推奨しうるので、play統合時は必ず渡す。
    """
    from agents.debug import ProbedProtagonist
    bot = ProbedProtagonist(seed, top=max(top, 3))
    # ★B-115：AIは選んだ option に provenance（`prov`）を書き込むことがある
    #   （B-100 の上書き席＝"b100"／一致席＝"b100_match"）。coach は人間の options を
    #   預かる立場＝**コピーを渡して呼び出し元の dict を汚さない**（tests/test_coach.py の
    #   非変異契約）。推奨として返す時は prov を剥がす＝options の要素と同値に戻す。
    chosen = bot.decide(view, decision, [dict(o) for o in options])
    if isinstance(chosen, dict):
        chosen.pop("prov", None)
    rec = bot.records[-1] if bot.records else {}
    est = rec.get("estimates", {})
    reasons = _reasons_for(view, decision, chosen, est)

    # ★席またぎの自滅回避：味方が既に暗躍禁止を出しているのに更に暗躍禁止を推奨したら、
    #   暗躍禁止を除いた選択肢でAIに選び直させる（2枚の暗躍禁止は自滅＝両方不発。KB:10）。
    if (decision == "set_card" and team_cards_this_turn
            and "暗躍禁止" in team_cards_this_turn
            and isinstance(chosen, dict) and chosen.get("card") == "暗躍禁止"):
        rest = [dict(o) for o in options if o.get("card") != "暗躍禁止"]
        if rest:
            bot2 = ProbedProtagonist(seed, top=max(top, 3))
            chosen = bot2.decide(view, decision, rest)
            if isinstance(chosen, dict):
                chosen.pop("prov", None)
            rec = bot2.records[-1] if bot2.records else rec
            est = rec.get("estimates", est)
            reasons = ["⚠ 味方が既に暗躍禁止をセット済み＝2枚出すと自滅（両方不発）なので、"
                       "暗躍禁止以外から選びます。"] + _reasons_for(view, decision, chosen, est)

    return CoachAdvice(
        decision=decision,
        recommended=chosen,
        ranked=rec.get("scored", [])[:top],
        reasons=reasons,
        insights=_insights(est),
    )


def _fmt_option(o: dict) -> str:
    if "card" in o:
        return f'{o["card"]} → {o.get("target")}'
    if o.get("action") == "pass":
        return "パス"
    if "ability" in o:
        return f'{o.get("character")}の「{o.get("ability")}」'
    if "role" in o:
        return f'{o.get("character")}＝{o.get("role")}'
    if "action" in o:
        return str(o["action"]) + (f' → {o["target"]}' if o.get("target") else "")
    return str(o)


# ---------------------------------------------------------------------------
# 敗因の振り返り（ループ検死・公開情報のみ）
# ---------------------------------------------------------------------------

# 友好無視/絶対友好無視を条文能力に持つ役職（拒否＝この中のどれかが確定する）。KB: 40/50。
_IGNORE_ROLES_BTX = "キラー／クロマク／カルティスト／ウィッチ／ファクター"
_IGNORE_ROLES_FS = "キラー／クロマク／カルティスト／マイナス"


def review_loop(history: list[dict], loop: int, set_name: str = "BTX") -> list[str]:
    """終わったループの公開イベント列から「何が起きたか」と「確定した情報」を初心者向けに要約。

    入力は GameState.history（両陣営に公開のイベントのみ）＝ネタバレなし。
    負けても情報は残る——それを回収するのが惨劇の学習の核心、を体験させる。
    """
    ev = [e for e in history if e.get("loop") == loop]
    if not ev:
        return [f"ループ{loop}のイベントがありません。"]
    lines: list[str] = [f"### 🔎 ループ{loop}の振り返り"]

    # --- 時系列ダイジェスト ---
    deaths_by_day: dict[int, list[str]] = {}
    for e in ev:
        d = e.get("day", 0)
        if e.get("event") == "death":
            deaths_by_day.setdefault(d, []).append(e.get("name", "?"))
    for e in ev:
        d = e.get("day", 0)
        k = e.get("event")
        if k == "incident":
            mark = "発生" if e.get("occurs") else "不発"
            lines.append(f"- {d}日目：事件「{e.get('name')}」＝**{mark}**")
        elif k == "death":
            lines.append(f"- {d}日目：〈{e.get('name')}〉が死亡")
        elif k == "guard_consumed":
            lines.append(f"- {d}日目：〈{e.get('name')}〉の護衛カウンターが消費された"
                         "（＝死亡を1回防いだ）")
        elif k == "goodwill_refused":
            lines.append(f"- {d}日目：〈{e.get('character')}〉の友好能力"
                         f"「{e.get('ability')}」を脚本家が**拒否**")
        elif k == "role_reveal":
            lines.append(f"- {d}日目：〈{e.get('name')}〉の役職が"
                         f"**{e.get('role')}** と公開")
        elif k == "rule_reveal":
            # 情報屋の友好能力（KB: 20:176）＝開示された名前だけを出す（他の秘匿は漏らさない）
            lines.append(f"- {d}日目：ルールXの一つが **{e.get('rule_x')}** と公開")
        elif k == "protagonist_death":
            lines.append(f"- {d}日目：**主人公が死亡**")
        elif k == "loop_end":
            lines.append(f"- {d}日目：ループ終了（{e.get('reason', '')}）")

    # --- 確定した情報（次のループへ持ち越せる知識） ---
    facts: list[str] = []
    # キーパーソン確定：ループ終了効果（＝KP死亡）の同日死亡者
    for e in ev:
        if e.get("event") == "loop_end" and "ループ終了効果" in str(e.get("reason", "")):
            same_day = deaths_by_day.get(e.get("day", -1), [])
            if len(same_day) == 1:
                facts.append(f"**〈{same_day[0]}〉はキーパーソン**（死亡が即ループを"
                             "終わらせた＝ループ終了効果。次ループは全力で守る）")
            elif same_day:
                facts.append("キーパーソンはこの日に死亡した "
                             + "・".join(f"〈{n}〉" for n in same_day) + " のいずれか")
    # 拒否＝友好無視系の確定
    ignore_roles = _IGNORE_ROLES_FS if set_name == "FS" else _IGNORE_ROLES_BTX
    refused = {e.get("character") for e in ev if e.get("event") == "goodwill_refused"}
    for ch in sorted(x for x in refused if x):
        facts.append(f"**〈{ch}〉の役職は友好無視/絶対友好無視を持つ**"
                     f"（{ignore_roles} のどれか）。拒否は貴重な自白です")
    # 役職公開
    for e in ev:
        if e.get("event") == "role_reveal":
            facts.append(f"**〈{e.get('name')}〉＝{e.get('role')}**（公開済み・確定）")
    # ルールX公開（情報屋）
    for e in ev:
        if e.get("event") == "rule_reveal" and e.get("rule_x"):
            facts.append(f"**ルールXの一つ＝{e.get('rule_x')}**（公開済み・確定）")
    # ループ終了時のボード暗躍（敗北条件の候補）
    for e in ev:
        if e.get("event") == "loop_board":
            hot = {b: n for b, n in (e.get("board_anyaku") or {}).items() if n >= 2}
            if hot:
                boards = "・".join(f"{b}（暗躍{n}）" for b, n in sorted(hot.items()))
                facts.append(f"終了時に {boards} ＝**ボードの敗北条件だった可能性**。"
                             "次ループはそのボードの暗躍を2未満に抑える/暗躍禁止で守る")
    # 発生した事件＝犯人のヒント
    occurred = [(e.get("day"), e.get("name")) for e in ev
                if e.get("event") == "incident" and e.get("occurs")]
    for d, nm in occurred:
        facts.append(f"{d}日目の「{nm}」は発生した＝**犯人は{d}日目に生存し、"
                     "不安が臨界以上だった**。その日不安の高かったキャラが犯人候補")

    if facts:
        lines.append("**📌 このループで確定・示唆された情報（次に持ち越す）:**")
        lines += [f"- {f}" for f in facts]
    else:
        lines.append("**📌 確定情報は取れませんでした。** 拒否を誘う・役職開示能力を"
                     "解禁するなど「情報の出る負け方」を意識しましょう。")
    lines.append("※負けたループも情報収穫の場です。同じ負け筋を繰り返さないことが上達の近道。")
    return lines


def format_advice(adv: CoachAdvice) -> list[str]:
    """UI表示用のMarkdown行リスト。"""
    lines: list[str] = []
    if adv.recommended is None:
        return ["（この決定にはコーチ評価がありません）"]
    lines.append(f"🎓 **AIコーチの推奨: {_fmt_option(adv.recommended)}**")
    for r in adv.reasons:
        lines.append(f"- {r}")
    if len(adv.ranked) >= 2:
        alts = "／".join(f"{_fmt_option(o)}({s:.0f})" for s, o in adv.ranked)
        lines.append(f"- 候補の評価順: {alts}")
    if adv.insights:
        lines.append("🔍 **AIの推理（公開情報から）:** " + "　".join(adv.insights))
    lines.append("※現行AIの評価であり正解の保証はありません（β）。あえて違う手を試すのも学びです。")
    return lines
