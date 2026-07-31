"""盤面のHTML可視化（Streamlit表示用・純関数）。

- 入力盤面 = 翻訳JSON（AIが質問文をどう理解したか）を2×2グリッドで描画。
  テスターが「AIの理解」を目視確認でき、翻訳ミスを発見できる（Bad/Bugボタンと直結）。
- 解説盤面 = エンジン裁定後（移動後の位置・カウンター最終値・打ち消し）を描画。
  行動解決フェイズのみ（他フェイズは裁定テキストで十分なため描画しない）。

streamlit には依存しない（st.markdown(..., unsafe_allow_html=True) に渡すHTML文字列を返すだけ）。
skill/ パッケージには含めない（build.sh/ps1 は engine/ と rules/ のみ同梱）。
ライト/ダークテーマ両対応のため、背景色は使わず枠線＋半透明のみで描く。
"""

from __future__ import annotations

import html as _h

from engine.board import AREAS

_ORDER = ["病院", "神社", "都市", "学校"]  # 2列グリッドの描画順（上段=病院・神社）
_MM = "mastermind"


# ---------- 部品 ----------

def _chip(text: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;border:1px solid {color};'
        f'border-radius:8px;padding:0 5px;margin:1px 2px;font-size:0.78em;'
        f'white-space:nowrap;">{text}</span>'
    )


#: ★B-100 デバッグ表示（2026-07-29・ユーザー要望）：主人公AIの札のうち
#  「B-100 の絶対防御（制約充足）で決まったもの」の枠色（緑）。**開発モードでのみ**渡される
#  ＝呼び出し側が prov を渡さなければ従来色のまま（bit 不変）。
_PROV_COLOR = {"b100": "#3f9e4d"}


def _card(owner: str, card: str, negated: bool = False,
          prov: str | None = None) -> str:
    # 行動カードは「白地・黒字」の実カード風にして、状態チップ（不安/友好/暗躍の枠線チップ）と
    # 一目で区別できるようにする。持ち主は枠色＋先頭ラベル（脚=赤／主=青）で表す。
    # 白地×黒字はライト/ダーク両テーマで可読（board全体の枠線チップ方針の例外＝意図的）。
    # ★prov（provenance）＝その札がどの経路で決まったか。既定 None＝従来色。
    mm = owner == _MM
    color = "#c0504d" if mm else "#4f81bd"
    if not mm and prov:
        color = _PROV_COLOR.get(prov, color)
    owner_label = "脚" if mm else "主"
    text = _h.escape(str(card))
    if negated:
        text = f"<s>{text}</s>"
    return (
        f'<span style="display:inline-block;background:#ffffff;color:#111;'
        f'border:2px solid {color};border-radius:6px;padding:0 5px;margin:1px 2px;'
        f'font-size:0.78em;white-space:nowrap;">'
        f'<span style="color:{color};font-weight:700;">{owner_label}</span> {text}</span>'
    )


def _counter_badges(goodwill: int = 0, unrest: int = 0, anyaku: int = 0) -> str:
    out = []
    if goodwill:
        out.append(_chip(f"友好{goodwill}", "#4f9d69"))
    if unrest:
        out.append(_chip(f"不安{unrest}", "#d0801f"))
    if anyaku:
        out.append(_chip(f"暗躍{anyaku}", "#7d5ba6"))
    return "".join(out)


def _cell(area: str, body: str) -> str:
    return (
        f'<div style="border:1px solid rgba(128,128,128,.5);border-radius:8px;'
        f'padding:5px 7px;min-height:72px;">'
        f'<div style="font-weight:700;font-size:.82em;opacity:.75;">{area}</div>'
        f"{body}</div>"
    )


def _grid(cells: dict[str, str]) -> str:
    inner = "".join(_cell(a, cells.get(a, "")) for a in _ORDER)
    return (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;'
        'max-width:600px;line-height:1.9;">' + inner + "</div>"
    )


# ---------- A-34：キャラのカードテキストポップアップ（CSS-only・表示専用） ----------
# ★方式（FableA条件2）：st.markdown は JS 不可＝**CSSのみ**で。PC=hover／スマホ=tabindex+:focus-within
#   （タップで開き、他所タップでフォーカスが外れて閉じる）。効かない環境ではポップアップが出ない
#   だけ＝グレースフル退行（対局を壊す失敗モードを作らない）。内容は _char_detail_md の流用。
_CARD_POPUP_CSS = (
    "<style>"
    ".rp-charpop{position:relative;cursor:help;border-bottom:1px dotted rgba(128,128,128,.7);}"
    ".rp-charpop:focus{outline:2px solid #4f81bd;outline-offset:1px;}"       # タップ可を示す
    ".rp-cardtip{display:none;position:absolute;left:0;top:1.6em;z-index:9999;"
    "width:min(78vw,320px);max-height:60vh;overflow:auto;background:#fffef7;color:#111;"
    "border:1px solid #b9a44a;border-radius:8px;padding:8px 10px;line-height:1.5;"
    "font-size:.82em;box-shadow:0 4px 16px rgba(0,0,0,.28);white-space:normal;text-align:left;}"
    ".rp-cardtip b{color:#7a5c00;}"
    # PC=hover／スマホ=tabindex+:focus-within（タップ）。どちらかが効けば出る＝OR で堅牢。
    ".rp-charpop:hover>.rp-cardtip,.rp-charpop:focus-within>.rp-cardtip{display:block;}"
    # ★スマホ（狭幅）＝右列のキャラでも画面外にはみ出さないよう、ツールチップを画面下部に固定する
    #   （left:0 の近接表示だと右列で右端を超える＝見切れる。狭幅では位置を親から切り離す）。
    "@media (max-width:480px){.rp-cardtip{position:fixed;left:8px;right:8px;bottom:8px;top:auto;"
    "width:auto;max-height:46vh;}}"
    "</style>"
)


def _md_to_tip_html(md: str) -> str:
    """_char_detail_md の markdown を、ツールチップ用の最小HTMLに変換（**太字**・改行・箇条書き）。
    ★新規のルール転記はしない＝内容は _char_detail_md のまま（整形だけ・FableA条件1）。"""
    import re
    # まずHTMLエスケープ（カードテキストに < > & が入っても壊れない）。
    s = _h.escape(md)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)     # **太字** → <b>
    lines = []
    for ln in s.split("\n"):
        t = ln.strip()
        if t.startswith("- "):
            lines.append("・" + t[2:])               # 箇条書き → ・
        else:
            lines.append(t)
    return "<br>".join(lines)


def popup_css() -> str:
    """カードテキスト/事件効果ポップアップのCSS（A-34/A-37共通）。呼び出し側が1回だけ注入する。
    ★board_html_from_json(popup=True) は自動で注入する。board を通さず popup_span を直接使う箇所
    （play.py の📅予定事件欄など）は、この関数の戻り値を先頭に一度 st.markdown する。"""
    return _CARD_POPUP_CSS


def popup_span(inner_html: str, tip_md: str) -> str:
    """★A-37（2026-07-19）：任意ラベル inner_html を、CSSポップアップ（PC=hover／スマホ=タップ）で
    包む汎用ヘルパー（A-34のキャラ名専用を一般化）。tip_md は markdown（_char_detail_md や
    INCIDENT_EFFECTS の文字列＝新規転記なし）＝最小HTML化して表示。tip が空なら素のラベルを返す
    ＝グレースフル退行（対局を壊さない）。CSS は呼び出し側が popup_css() で注入する。"""
    tip = _md_to_tip_html(tip_md) if tip_md else ""
    if not tip:
        return inner_html
    return (f'<span class="rp-charpop" tabindex="0">{inner_html}'
            f'<span class="rp-cardtip">{tip}</span></span>')


def _char_popup_span(name: str, inner_label: str) -> str:
    """キャラ名 inner_label を、カードテキストのCSSポップアップで包む（A-34）。
    _char_detail_md（play.py）を遅延importで流用＝循環importを避ける（play が board_viz を import）。
    詳細が取れない/空なら包まず素のラベルを返す＝グレースフル退行（対局を壊さない）。"""
    try:
        from arena.play import _char_detail_md
        return popup_span(inner_label, _char_detail_md(name))
    except Exception:  # noqa: BLE001  詳細生成に失敗してもラベルは出す
        return inner_label


def _char_label(name: str, role: str | None, alive: bool, *, popup: bool = False) -> str:
    label = ("💀" if not alive else "") + _h.escape(str(name))
    if role and role != "パーソン":
        label += f'<span style="opacity:.65;font-size:.8em;">({_h.escape(str(role))})</span>'
    # ★A-34：popup=True（一人回し両モードの盤面）のときだけカードテキストポップアップで包む。
    #   相談AI/builder/viewer 等は既定 False＝従来どおり（様子見・FableA条件5）。
    if popup and name:
        return _char_popup_span(str(name), label)
    return label


# ---------- 入力盤面（翻訳JSONから） ----------

def board_html_from_json(data: dict | None, *, popup: bool = False,
                         dev: bool = False) -> str | None:
    """翻訳JSON（AIの理解）を盤面HTMLに。盤面情報が無い質問（フェイズ全般等）は None。

    popup=True（A-34・一人回し両モードの盤面）＝キャラ名にカードテキストのCSSポップアップを付ける
    （PC=hover／スマホ=タップ）。既定 False＝従来どおり（相談AI/builder/viewer 等は様子見）。

    ★dev=True（B-100・2026-07-29・ユーザー要望）＝**開発モード限定**のデバッグ表示。
      placement の `prov`（provenance）を札チップに渡す＝B-100 の絶対防御で決まった
      主人公AIの札の枠を緑にする。既定 False＝prov を一切見ない＝従来と bit 不変
      （安定版 `APP_CHANNEL=stable` では呼び出し側が dev を立てない）。
    """
    if not isinstance(data, dict):
        return None
    chars = data.get("characters") or []
    placements = data.get("placements") or []
    board_anyaku = data.get("board_anyaku") or {}

    cards_by_char: dict[str, list] = {}
    cards_by_board: dict[str, list] = {}
    for p in placements:
        if not isinstance(p, dict):
            continue
        bucket = cards_by_board if p.get("target_kind") == "board" else cards_by_char
        bucket.setdefault(str(p.get("target", "")), []).append(p)

    has_area = any((c.get("area") or "") in AREAS for c in chars if isinstance(c, dict))
    has_board_info = bool(cards_by_board) or any(int(v or 0) for v in board_anyaku.values())
    if not has_area and not has_board_info:
        return None  # エリアの無い質問（脚本家能力フェイズの総数・ループ終了等）は描かない

    cells: dict[str, str] = {}
    for a in AREAS:
        parts: list[str] = []
        head = _counter_badges(anyaku=int(board_anyaku.get(a) or 0))
        head += "".join(_card(p.get("owner", ""), p.get("card", ""),
                              prov=(p.get("prov") if dev else None))
                        for p in cards_by_board.get(a, []))
        if head:
            parts.append(f"<div>{head}</div>")
        for c in chars:
            if not isinstance(c, dict) or (c.get("area") or "") != a:
                continue
            line = _char_label(c.get("name", ""), c.get("role"), bool(c.get("alive", True)),
                               popup=popup)
            line += _counter_badges(int(c.get("goodwill") or 0), int(c.get("unrest") or 0),
                                    int(c.get("anyaku") or 0))
            line += "".join(_card(p.get("owner", ""), p.get("card", ""),
                                  prov=(p.get("prov") if dev else None))
                            for p in cards_by_char.get(str(c.get("name", "")), []))
            parts.append(f"<div>{line}</div>")
        cells[a] = "".join(parts)
    html = _grid(cells)
    return (_CARD_POPUP_CSS + html) if popup else html   # ★A-34：CSSは popup 時だけ1回注入


# ---------- 解説盤面（裁定後。行動解決フェイズのみ） ----------

def board_html_from_outcome(outcome, input_data: dict | None = None) -> str | None:
    """裁定後の盤面HTML。移動後の位置・カウンター最終値・打ち消しを表示。

    input_data（翻訳JSON）を渡すと移動元を「←〇〇から」注記できる
    （エンジンは盤面を移動後の状態に更新するため、移動元はJSON側にしか残らない）。
    """
    if outcome is None or outcome.adjudication is None:
        return None
    adj = outcome.adjudication
    q = outcome.question

    origins: dict[str, str] = {}
    for c in (input_data or {}).get("characters", []) or []:
        if isinstance(c, dict) and c.get("name"):
            origins[str(c["name"])] = str(c.get("area") or "")

    parts_by_area: dict[str, list[str]] = {a: [] for a in AREAS}

    # ボードの暗躍結果（打ち消しは取り消し線で見せる）
    for name, tr in adj.targets.items():
        if tr.target_kind != "board" or name not in AREAS:
            continue
        if tr.has_kinshi and tr.delta != tr.anyaku_plus:
            txt = f"暗躍 <s>+{tr.anyaku_plus}</s> → <b>+{tr.delta}</b>（禁止実効）"
        elif tr.has_kinshi:
            txt = f"暗躍 <b>+{tr.delta}</b>（禁止は不発）"
        else:
            txt = f"暗躍 <b>+{tr.delta}</b>"
        parts_by_area[name].append(f'<div>{_chip(txt, "#7d5ba6")}</div>')

    # キャラ（移動後の位置に描く）
    for name, ch in q.board.characters.items():
        area = adj.moves.get(name, ch.area)
        if area not in AREAS:
            continue
        line = _char_label(name, ch.role, ch.alive)

        notes: list[str] = []
        if name in adj.blocked_moves:
            notes.append("⚠禁止エリアで移動不成立")
        elif name in adj.move_banned:
            notes.append("移動禁止で留まる")
        else:
            org = origins.get(name)
            if org and org in AREAS and org != area:
                notes.append(f"←{org}から移動")
        for n in notes:
            line += _chip(_h.escape(n), "#888888")

        # カウンター（初期→最終。変化が無ければ現在値のみ）
        if name in adj.unrest:
            r = adj.unrest[name]
            txt = (f"不安 {r.initial}→<b>{r.final}</b>"
                   + ("（禁止実効）" if r.ban_active else ""))
            line += _chip(txt, "#d0801f")
        elif ch.unrest:
            line += _counter_badges(unrest=ch.unrest)
        if name in adj.goodwill:
            r = adj.goodwill[name]
            txt = (f"友好 {r.initial}→<b>{r.final}</b>"
                   + ("（禁止実効）" if r.ban_active else ""))
            line += _chip(txt, "#4f9d69")
        elif ch.goodwill:
            line += _counter_badges(goodwill=ch.goodwill)
        ctr = adj.targets.get(name)
        if ctr is not None and ctr.target_kind == "character":
            if ctr.has_kinshi and ctr.delta != ctr.anyaku_plus:
                line += _chip(f"暗躍 <s>+{ctr.anyaku_plus}</s>→<b>+{ctr.delta}</b>", "#7d5ba6")
            else:
                line += _chip(f"暗躍 <b>+{ctr.delta}</b>", "#7d5ba6")

        parts_by_area[area].append(f"<div>{line}</div>")

    cells = {a: "".join(v) for a, v in parts_by_area.items()}
    return _grid(cells)
