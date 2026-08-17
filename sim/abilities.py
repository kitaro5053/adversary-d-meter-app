"""友好能力の効果実装（M5続き）— FS標準能力のうち根拠が明確なものを裁定する。

現行 engine は友好能力の「使用可否／拒否可否」だけを裁定し効果は裁定しなかった（ネタバレ回避）。
シミュレータでは効果まで必要なので、rules/20（実カード表記優先）を根拠にFSで意味のある能力を実装する。
未実装の能力は legal に出さない（＝使えない）。捏造せず、根拠のあるものだけ足す。

各能力: targets(state, user) → 対象候補のリスト（前提を満たさなければ空）。
        apply(state, user, target) → 盤面へ効果適用（公開/神視点ログも書く）。
1体1能力で表現できるよう ability 名は data.GOODWILL_ABILITIES と一致させる。
"""

from __future__ import annotations

from engine.data import is_student, unrest_threshold_of

from .state import FS_RULE_X_ROLES, GameState

_AREAS = ("病院", "神社", "都市", "学校")


def _same_area_others(state: GameState, user: str) -> list[str]:
    u = state.characters[user]
    return [n for n, c in state.characters.items()
            if n != user and c.alive and c.on_board and c.area == u.area]


def _occurred_incidents_this_loop(state: GameState) -> list[int]:
    days = []
    for e in state.history:
        if (e.get("event") == "incident" and e.get("loop") == state.loop_no
                and e.get("occurs")):
            days.append(e["day"])
    return days


# ---------------------------------------------------------------------------
# 能力レジストリ：(character, ability_name) → {"targets": fn, "apply": fn}
# ---------------------------------------------------------------------------
# 効果は rules/20（★実カード表記優先）に準拠。ログは effects の _pub/_sec を使わず
# ここでは state.history/secret_log に直接積む（循環import回避）。

def _pub(state, ev):
    state.pub(ev)


def _reveal_role(state: GameState, name: str) -> None:
    role = state.script.role_of(name)
    if state.revealed_roles.get(name) != role:
        state.revealed_roles[name] = role
        _pub(state, {"event": "role_reveal", "name": name, "role": role})


def _reveal_culprit(state: GameState, day: int) -> None:
    inc = next((i for i in state.script.incidents if i.day == day), None)
    if inc is not None:
        _pub(state, {"event": "culprit_reveal", "day": day, "name": inc.culprit})


# ---- 学生の不安除去（男子学生／女子学生）：同エリアの他の学生から不安1除去 ----
def _student_unrest_targets(state, user):
    return [n for n in _same_area_others(state, user) if is_student(n)]


def _student_unrest_apply(state, user, target):
    c = state.characters[target]
    if c.unrest > 0:
        c.unrest -= 1
        _pub(state, {"event": "unrest", "target": target, "delta": -1})


# ---- 巫女：神社の暗躍除去（巫女が神社にいれば神社の暗躍1除去） ----
def _miko_shrine_targets(state, user):
    return ["神社"] if state.characters[user].area == "神社" else []


def _miko_shrine_apply(state, user, target):
    if state.board_anyaku.get("神社", 0) > 0:
        state.board_anyaku["神社"] -= 1
        _pub(state, {"event": "anyaku", "target": "神社", "delta": -1})


# ---- 巫女：同エリアの役職開示（1/L） ----
def _reveal_same_area_targets(state, user):
    return _same_area_others(state, user)


def _reveal_target_role_apply(state, user, target):
    _reveal_role(state, target)


# ---- サラリーマン／イレギュラー：自身の役職開示 ----
def _self_targets(state, user):
    return [user]


def _reveal_self_apply(state, user, target):
    _reveal_role(state, user)


# ---- イレギュラー：自身の役職開示（第2ループ以降のみ） ----
def _irregular_targets(state, user):
    return [user] if state.loop_no >= 2 else []


# ---- 刑事：このループ発生事件の犯人開示（1/L） ----
def _detective_targets(state, user):
    return [f"事件{d}日目" for d in _occurred_incidents_this_loop(state)]


def _detective_apply(state, user, target):
    day = int(target.replace("事件", "").replace("日目", ""))
    _reveal_culprit(state, day)


# ---- 神格：事件の犯人開示（公開シートの事件1つ／1L） ----
def _shinkaku_culprit_targets(state, user):
    return [f"事件{i.day}日目" for i in state.script.incidents]


# ---- 神格：暗躍除去（同エリアのキャラ1人 or 自ボード） ----
def _shinkaku_anyaku_targets(state, user):
    u = state.characters[user]
    return _same_area_others(state, user) + [u.area]


def _remove_anyaku_apply(state, user, target):
    if target in _AREAS:
        if state.board_anyaku.get(target, 0) > 0:
            state.board_anyaku[target] -= 1
            _pub(state, {"event": "anyaku", "target": target, "delta": -1})
    else:
        c = state.characters[target]
        if c.anyaku > 0:
            c.anyaku -= 1
            _pub(state, {"event": "anyaku", "target": target, "delta": -1})


# ---- 医者／アイドル：同エリアの他キャラから不安1除去 ----
def _unrest_remove_apply(state, user, target):
    c = state.characters[target]
    if c.unrest > 0:
        c.unrest -= 1
        _pub(state, {"event": "unrest", "target": target, "delta": -1})


# ---- 医者：同エリアの他キャラの不安を1「除去 or 付与」（KB: 20 医者能力1・除去/付与の選択）----
def _doctor_unrest_apply(state, user, target, decide=None, actor=None):
    c = state.characters[target]
    # 除去/付与を主人公が選ぶ（付与は事件の燃料になる＝ルール上できる。実装漏れだった）。
    opts = ([{"mode": "remove"}] if c.unrest > 0 else []) + [{"mode": "add"}]
    if decide is not None and len(opts) > 1:
        mode = decide(actor, "doctor_unrest_mode", opts)["mode"]
    else:
        mode = opts[0]["mode"]
    if mode == "add":
        c.unrest += 1
        _pub(state, {"event": "unrest", "target": target, "delta": 1})
    elif c.unrest > 0:
        c.unrest -= 1
        _pub(state, {"event": "unrest", "target": target, "delta": -1})


# ---- アイドル：同エリアの他キャラに友好1付与 ----
def _goodwill_grant_apply(state, user, target):
    state.characters[target].goodwill += 1
    _pub(state, {"event": "goodwill", "target": target, "delta": 1})


# ---- ナース：同エリアの不安臨界以上の他キャラから不安1除去（拒否不可） ----
def _nurse_targets(state, user):
    out = []
    for n in _same_area_others(state, user):
        th = unrest_threshold_of(n)
        if th is not None and state.characters[n].unrest >= th and th > 0:
            out.append(n)
    return out


# ---- 転校生：同エリアの他キャラの暗躍1を友好1に置換 ----
def _tenkousei_targets(state, user):
    return [n for n in _same_area_others(state, user) if state.characters[n].anyaku > 0]


def _tenkousei_apply(state, user, target):
    c = state.characters[target]
    if c.anyaku > 0:
        c.anyaku -= 1
        c.goodwill += 1
        _pub(state, {"event": "anyaku", "target": target, "delta": -1})
        _pub(state, {"event": "goodwill", "target": target, "delta": 1})


# ---- 刑事：護衛カウンター付与（同エリアの他キャラに護衛1） ----
def _guard_apply(state, user, target):
    state.characters[target].guard += 1
    _pub(state, {"event": "guard", "target": target, "delta": 1})


# ---- 軍人：このループ主人公不死（自身宣言） ----
def _immortal_apply(state, user, target):
    state.protagonist_immortal = True
    _pub(state, {"event": "protagonist_immortal"})


# ---- 委員長：使用済み1/Lカードを手札に戻す（リーダーの使用済み1Lカード） ----
def _committee_targets(state, user):
    # 空撃ち不可：リーダーに使用済みの1ループ1回カードが無いと使えない（KB: 20）
    return list(dict.fromkeys(state.used_cards.get(state.leader, [])))


def _committee_apply(state, user, target):
    used = state.used_cards.get(state.leader, [])
    if target in used:
        used.remove(target)  # 手札に戻る＝使用済みから外す
        _pub(state, {"event": "card_returned", "owner": state.leader, "card": target})


# ---- お嬢様：友好+1付与（お嬢様が学校/都市にいれば同エリアの他キャラに友好1） ----
def _ojo_targets(state, user):
    return _same_area_others(state, user) if state.characters[user].area in ("学校", "都市") else []


# ---- 情報屋：ルールX開示（宣言と異なる実ルールXを開示。FSはルールX1つ） ----
def _joho_targets(state, user):
    return list(FS_RULE_X_ROLES)  # 宣言候補＝FSのルールX3種


def _joho_apply(state, user, target):
    actual = state.script.rule_x
    if target != actual:  # 宣言が外れれば実ルールXが開示される（KB: 20）
        _pub(state, {"event": "rule_reveal", "rule_x": actual})


# ---- 教師：学生の不安操作（同エリアの学生から不安1除去） ----
def _teacher_student_targets(state, user):
    return [n for n in _same_area_others(state, user) if is_student(n)]


# ---- 異世界人：同エリアの他キャラを殺害（主人公専用・拒否不可でない＝役職次第） ----
def _isekai_apply(state, user, target):
    from .effects import kill_character
    kill_character(state, target, f"異世界人の友好能力（{user}）")


# ---- 異世界人：同エリアの死体を蘇生 ----
def _isekai_revive_targets(state, user):
    u = state.characters[user]
    return [n for n, c in state.characters.items()
            if n != user and not c.alive and c.area == u.area]


def _isekai_revive_apply(state, user, target):
    c = state.characters[target]
    if not c.alive:
        c.alive = True
        _pub(state, {"event": "revive", "name": target})


# ---- 鑑識官：死体の役職開示（任意の死体1つ） ----
def _corpse_targets(state, user):
    return [n for n, c in state.characters.items() if not c.alive]


# ---- 鑑識官：カウンター移し替え（同エリアの他キャラ間で任意カウンター1つ移動） ----
_CTR_ATTR = {"友好": "goodwill", "不安": "unrest", "暗躍": "anyaku"}


def _forensic_move_targets(state, user):
    others = _same_area_others(state, user)
    out = []
    for frm in others:
        cf = state.characters[frm]
        for label, attr in _CTR_ATTR.items():
            if getattr(cf, attr) > 0:
                for to in others:
                    if to != frm:
                        out.append(f"{frm}:{label}:{to}")
    return out


def _forensic_move_apply(state, user, target):
    frm, label, to = target.split(":")
    attr = _CTR_ATTR[label]
    cf, ct = state.characters[frm], state.characters[to]
    if getattr(cf, attr) > 0:
        setattr(cf, attr, getattr(cf, attr) - 1)
        setattr(ct, attr, getattr(ct, attr) + 1)
        _pub(state, {"event": attr, "target": frm, "delta": -1})
        _pub(state, {"event": attr, "target": to, "delta": 1})


# ---- 医者能力3：このループ中、入院患者は禁止エリアを失う ----
def _doctor_lift_targets(state, user):
    return ["入院患者"] if "入院患者" in state.characters else []


def _doctor_lift_apply(state, user, target):
    state.forbidden_lifted.add("入院患者")
    _pub(state, {"event": "forbidden_lifted", "name": "入院患者"})


# ---- マスコミ：自身以外の任意キャラ1人に不安+1（エリア不問）／同エリアキャラor自ボードに暗躍+1 ----
def _media_unrest_targets(state, user):
    return [n for n, c in state.characters.items()
            if n != user and c.alive and c.on_board]


def _media_unrest_apply(state, user, target):
    state.characters[target].unrest += 1
    _pub(state, {"event": "unrest", "target": target, "delta": 1})


def _media_anyaku_targets(state, user):
    u = state.characters[user]
    return _same_area_others(state, user) + [user, u.area]


def _media_anyaku_apply(state, user, target):
    if target in _AREAS:
        state.board_anyaku[target] += 1
    else:
        state.characters[target].anyaku += 1
    _pub(state, {"event": "anyaku", "target": target, "delta": 1})


# ---- 手先：このループ中、自身が犯人の事件は発生しない ----
def _tesaki_apply(state, user, target):
    state.tesaki_suppressed = True
    _pub(state, {"event": "incident_suppressed", "name": "手先"})


# ---- 学者：自身の全カウンター除去（Exゲージは本KB範囲外＝未使用時は変化なし） ----
def _scholar_apply(state, user, target):
    c = state.characters[user]
    for attr, ev in (("unrest", "unrest"), ("goodwill", "goodwill"),
                     ("anyaku", "anyaku"), ("guard", "guard")):
        v = getattr(c, attr)
        if v > 0:
            setattr(c, attr, 0)
            _pub(state, {"event": ev, "target": user, "delta": -v})


# ---- 幻想：同エリアのキャラ1人を任意ボードへ移動／自身をこのループから除去 ----
def _genso_move_targets(state, user):
    """[脚]「選ばれたキャラを選ばれたボードに移動させる」（KB: 20 幻想・能力1）。

    ★E-2（2026-07-29）：**対象キャラの禁止エリアは選べない**。行方不明（KB: 40:153／50）の
      公式裁定＝「効果による移動でも禁止エリアへは移動できない」と**同じ条文型**
      （「〜を〜ボードに移動させる」）なので同じ扱いにする。根拠＝禁止エリアの定義
      「そのキャラが移動できないボード」（KB: 00）＝移動の出所を問わない。
      ★行方不明の裁定そのものは公式回答（ユーザー確認）だが、**幻想への適用は同型からの
        推論**＝原本での再確認が望ましい（監査doc §7 の要確認事項）。
      ★このループの禁止解除（医者能力3・女の子能力1）は current_forbidden が反映する。
    """
    from .state import current_forbidden
    u = state.characters[user]
    out = []
    for n, c in state.characters.items():
        if c.alive and c.on_board and c.area == u.area:
            forb = current_forbidden(state, n)
            for a in _AREAS:
                if a != c.area and a not in forb:
                    out.append(f"{n}:{a}")
    return out


def _genso_move_apply(state, user, target):
    name, area = target.split(":")
    state.characters[name].area = area
    _pub(state, {"event": "move", "name": name, "to": area})


def _genso_vanish_apply(state, user, target):
    # このループ中、幻想はキャラクターでも死体でもなくなる（次ループ準備で再配置）。KB: 20
    state.characters[user].area = None
    _pub(state, {"event": "vanish", "name": user})


# ---- 大物：テリトリー（縄張りボード）にいる自身以外のキャラ1人の役職を知る ----
#      縄張りは脚本作成時指定・全ループ固定（カード特性・現物確認済 2026-07-05）。
def _oomono_targets(state, user):
    terr = state.script.oomono_territory
    if not terr:
        return []
    return [n for n, c in state.characters.items()
            if n != user and c.alive and c.on_board and c.area == terr]


# ---- A.I.：公開シートの事件1つの効果を解決（犯人=A.I.扱い・選択はリーダー・発生とはみなさない） ----
def _ai_targets(state, user):
    return sorted({i.name for i in state.script.incidents})


def _ai_apply(state, user, target, decide=None, actor=None):
    from .effects import _apply_incident_effect
    from .state import Incident
    if decide is None:
        return  # decide必須（flow経由でのみ使用可）
    _pub(state, {"event": "ai_incident_effect", "name": target})
    pseudo = Incident(day=state.day, name=target, culprit=user)
    # 「発生した」とはみなさない（実カード表記）＝incidentイベントは出さず効果のみ解決
    _apply_incident_effect(state, pseudo, decide, chooser=actor or "mastermind")


# ---- 女の子：このループ中、自身の禁止エリア（学校以外）を失う（KB: 30・友好1） ----
def _onnanoko_lift_apply(state, user, target):
    state.forbidden_lifted.add(user)
    _pub(state, {"event": "forbidden_lifted", "name": user})


# ---- 女の子：隣接ボードへ移動（1/L・友好3。学校固定を越える移動能力＝禁止エリアに縛られない） ----
#      ★KB(30)は「隣接ボードへ移動」の一行のみ。このカードの主眼は学校固定の脱出＝移動能力
#      なので、自身の禁止エリア（病院/神社/都市）にも移動できる解釈を採る（要確認だが最も自然）。
def _onnanoko_move_targets(state, user):
    from engine.board import destination
    u = state.characters[user]
    if u.area is None:
        return []
    adj = {destination(u.area, (1, 0)), destination(u.area, (0, 1))}
    return sorted(adj - {u.area})


def _onnanoko_move_apply(state, user, target):
    state.characters[user].area = target
    _pub(state, {"event": "move", "name": user, "to": target})


# ---- 教祖：同エリアの不安臨界以上の他キャラに友好+1／役職開示（KB: 30） ----
def _guru_targets(state, user):
    out = []
    for n in _same_area_others(state, user):
        th = unrest_threshold_of(n)
        if th is not None and state.characters[n].unrest >= th:
            out.append(n)
    return out


# ---- コピーキャット：第2ループ以降、自身と同じ役職のキャラ全員の役職を開示（拒否不可・KB: 30） ----
def _copycat_targets(state, user):
    return [user] if state.loop_no >= 2 else []


def _copycat_apply(state, user, target):
    role = state.script.role_of(user)
    for n in state.characters:
        if state.script.role_of(n) == role:
            _reveal_role(state, n)


# ---- 妹：同エリアの大人1人に、その大人の友好能力を肩代わり使用させる（拒否不可・回数制限は参照。KB: 30） ----
#      target は "大人名|能力名|その能力の対象" を "|" で連結（内側の対象は ":" 区切りを含みうる）。
def _imouto_targets(state, user):
    from engine.data import goodwill_abilities_of, is_adult
    out: list[str] = []
    for adult in _same_area_others(state, user):
        if not is_adult(adult):
            continue
        for ab in goodwill_abilities_of(adult) or []:
            aname = ab["name"]
            if not is_implemented(adult, aname):
                continue
            if ab["once_per_loop"] and (adult, aname) in state.used_goodwill:
                continue
            for t in ability_targets(state, adult, aname):
                out.append(f"{adult}|{aname}|{t}")
    return out


def _imouto_apply(state, user, target, decide=None, actor=None):
    from engine.data import goodwill_abilities_of
    adult, aname, t = target.split("|", 2)
    # 大人本人の友好コストは不要（妹が友好5で肩代わり）。効果はそのまま解決＝拒否不可。
    apply_ability(state, adult, aname, t, decide=decide, actor=actor)
    for ab in goodwill_abilities_of(adult) or []:  # 借りた能力が1/Lなら回数制限を消費（参照）
        if ab["name"] == aname and ab["once_per_loop"]:
            state.used_goodwill.add((adult, aname))
    _pub(state, {"event": "imouto_proxy", "adult": adult, "ability": aname, "target": t})


# ---- 従者：ボードの自身以外1人を選び、このループ中その者を特性（追随）の対象に追加（KB: 20/30） ----
def _juusha_add_targets(state, user):
    return [n for n, c in state.characters.items()
            if n != "従者" and c.alive and c.on_board and n not in state.juusha_targets]


def _juusha_add_apply(state, user, target):
    state.juusha_targets.add(target)
    _pub(state, {"event": "juusha_target_added", "target": target})


# ---- アルバイト？：自身の役職を開示＋同エリアのキャラ1人に友好+2（KB: 20/30） ----
def _alubaito_q_apply(state, user, target):
    _reveal_role(state, user)
    c = state.characters[target]
    c.goodwill += 2
    _pub(state, {"event": "goodwill", "target": target, "delta": 2})


# ability名は data.GOODWILL_ABILITIES と一致。target_kind は表示用。
# ★B-140：`board_scope` ＝その能力が**板（ボード）の暗躍**を剥がせる範囲の宣言。
#   ("神社",) 等の固定タプル／"self_board"＝能力者のいるボード（＝どの板にもなりうる）。
#   キーが無い能力は**板を対象に取れない**（例＝転校生は同一エリアの他キャラ限定）。
#   宣言は targets 実装と同じ行に置く＝単一ソース。targets の実挙動との一致は
#   tests/test_b140_board_removal_scope.py が機械的に検証する（宣言の独り歩きを防ぐ）。
ABILITY_IMPL: dict[tuple[str, str], dict] = {
    ("男子学生", "学生の不安除去"): {"targets": _student_unrest_targets, "apply": _student_unrest_apply},
    ("女子学生", "学生の不安除去"): {"targets": _student_unrest_targets, "apply": _student_unrest_apply},
    ("巫女", "神社の暗躍除去"): {"targets": _miko_shrine_targets, "apply": _miko_shrine_apply,
                                 "board_scope": ("神社",)},   # KB rules/20:129 神社限定
    ("巫女", "同エリアの役職開示"): {"targets": _reveal_same_area_targets, "apply": _reveal_target_role_apply},
    ("サラリーマン", "自身の役職開示"): {"targets": _self_targets, "apply": _reveal_self_apply},
    ("イレギュラー", "自身の役職開示（第2L以降）"): {"targets": _irregular_targets, "apply": _reveal_self_apply},
    ("刑事", "このループ発生事件の犯人開示"): {"targets": _detective_targets, "apply": _detective_apply},
    ("神格", "事件の犯人開示"): {"targets": _shinkaku_culprit_targets, "apply": _detective_apply},
    ("神格", "暗躍除去（キャラ/ボード）"): {"targets": _shinkaku_anyaku_targets,
                                            "apply": _remove_anyaku_apply,
                                            "board_scope": "self_board"},  # KB rules/20:153 自ボード
    ("医者", "不安操作（除去/付与）"): {"targets": _same_area_others,
                                       "apply": _doctor_unrest_apply, "needs_decide": True},
    ("アイドル", "不安除去"): {"targets": _same_area_others, "apply": _unrest_remove_apply},
    ("アイドル", "友好+1付与"): {"targets": _same_area_others, "apply": _goodwill_grant_apply},
    ("ナース", "不安臨界以上のキャラの不安除去"): {"targets": _nurse_targets, "apply": _unrest_remove_apply},
    ("転校生", "暗躍除去＋友好付与"): {"targets": _tenkousei_targets, "apply": _tenkousei_apply},
    ("刑事", "護衛カウンター付与"): {"targets": _same_area_others, "apply": _guard_apply},
    ("軍人", "このループ主人公不死"): {"targets": _self_targets, "apply": _immortal_apply},
    ("委員長", "使用済み1/Lカードを手札に戻す"): {"targets": _committee_targets, "apply": _committee_apply},
    ("お嬢様", "友好+1付与（学校/都市）"): {"targets": _ojo_targets, "apply": _goodwill_grant_apply},
    ("情報屋", "ルールX開示"): {"targets": _joho_targets, "apply": _joho_apply},
    ("教師", "学生の不安操作"): {"targets": _teacher_student_targets, "apply": _unrest_remove_apply},
    ("教師", "学生の役職開示"): {"targets": _teacher_student_targets, "apply": _reveal_target_role_apply},
    ("異世界人", "同エリアのキャラ殺害"): {"targets": _same_area_others, "apply": _isekai_apply},
    ("異世界人", "死体蘇生"): {"targets": _isekai_revive_targets, "apply": _isekai_revive_apply},
    ("鑑識官", "カウンター移し替え"): {"targets": _forensic_move_targets, "apply": _forensic_move_apply},
    ("鑑識官", "死体の役職開示"): {"targets": _corpse_targets, "apply": _reveal_target_role_apply},
    ("医者", "入院患者の禁止エリア解除"): {"targets": _doctor_lift_targets, "apply": _doctor_lift_apply},
    ("マスコミ", "任意キャラに不安+1"): {"targets": _media_unrest_targets, "apply": _media_unrest_apply},
    ("マスコミ", "暗躍+1（キャラ/ボード）"): {"targets": _media_anyaku_targets, "apply": _media_anyaku_apply},
    ("手先", "自身犯の事件を不発に"): {"targets": _self_targets, "apply": _tesaki_apply},
    ("学者", "全カウンター除去＋Exゲージ操作"): {"targets": _self_targets, "apply": _scholar_apply},
    ("幻想", "キャラをボード移動"): {"targets": _genso_move_targets, "apply": _genso_move_apply},
    ("幻想", "自身をゲームから除去"): {"targets": _self_targets, "apply": _genso_vanish_apply},
    ("A.I.", "事件効果を解決"): {"targets": _ai_targets, "apply": _ai_apply, "needs_decide": True},
    ("大物", "テリトリー内の役職開示"): {"targets": _oomono_targets, "apply": _reveal_target_role_apply},
    ("女の子", "禁止エリア解除"): {"targets": _self_targets, "apply": _onnanoko_lift_apply},
    ("女の子", "隣接ボードへ移動"): {"targets": _onnanoko_move_targets, "apply": _onnanoko_move_apply},
    ("教祖", "不安臨界以上のキャラに友好+1"): {"targets": _guru_targets, "apply": _goodwill_grant_apply},
    ("教祖", "不安臨界以上のキャラの役職開示"): {"targets": _guru_targets, "apply": _reveal_target_role_apply},
    ("コピーキャット", "同役職キャラ全員の名前開示"): {"targets": _copycat_targets, "apply": _copycat_apply},
    ("妹", "大人に友好能力を肩代わり使用"): {"targets": _imouto_targets, "apply": _imouto_apply,
                                          "needs_decide": True},
    ("従者", "特性対象を追加"): {"targets": _juusha_add_targets, "apply": _juusha_add_apply},
    ("アルバイト？", "自身役職開示＋友好+2"): {"targets": _same_area_others, "apply": _alubaito_q_apply},
}


def is_implemented(character: str, ability: str) -> bool:
    return (character, ability) in ABILITY_IMPL


def board_anyaku_removal_scope(character: str, ability: str,
                               boards=None) -> frozenset:
    """★B-140：(キャラ, 能力) が**板（ボード）の暗躍**を剥がせる板の集合。

    剥がせないなら空集合。KB（rules/20_goodwill_abilities.md）の現物：
      - 巫女「神社の暗躍除去」:129  ＝「巫女が**神社**にいないと使えない」＝神社のみ
      - 神格「暗躍除去（キャラ/ボード）」:153 ＝「同一エリアにいるキャラ1人か、**神格のいる
        ボード**」＝立っている板（どの板にもなりうる）
      - 転校生「暗躍除去＋友好付与」:123 ＝「同一エリアにいる**他のキャラ1人**」
        ＝**板は対象に取れない**（実カード表記も「暗躍カウンター1つを友好カウンターに置き換える」）
    ∴ 板の暗躍を剥がせるのは巫女（神社のみ）と神格（自ボード）の2人だけ。

    boards＝"self_board" を解決する候補板（省略＝全4エリア）。呼び出し側が
    「ゴール板のうちどれが剥がされうるか」を知りたい時は goal_boards を渡す。
    """
    impl = ABILITY_IMPL.get((character, ability))
    scope = impl.get("board_scope") if impl else None
    if scope is None:
        return frozenset()
    if scope == "self_board":
        return frozenset(_AREAS if boards is None else boards)
    return frozenset(scope)


def ability_targets(state: GameState, character: str, ability: str) -> list[str]:
    impl = ABILITY_IMPL.get((character, ability))
    return impl["targets"](state, character) if impl else []


def apply_ability(state: GameState, character: str, ability: str, target: str,
                  decide=None, actor: str | None = None) -> None:
    """能力効果を適用。decide/actor は効果内で追加選択が要る能力（A.I.）にのみ使う。"""
    impl = ABILITY_IMPL.get((character, ability))
    if impl is None:
        return
    if impl.get("needs_decide"):
        impl["apply"](state, character, target, decide=decide, actor=actor)
    else:
        impl["apply"](state, character, target)
