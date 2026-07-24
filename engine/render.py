"""エンジン裁定の決定的レンダリング（LLM非依存・純粋関数）。

役割:
- LLM の自然文回答とは別に、エンジンの結論を「決定的なテキスト」に起こす。
  これを (a) 回答LLMへ最優先の根拠として注入し、(b) 画面に検算用として表示する。
- 感度フラグがあるときの「確認盤面（2×2＋仮定＋⚠）」も決定的に描画する。
- ここでは推論しない。Outcome / Board を文字列にするだけ＝テスト可能。

盤面レイアウト（board.py と一致）:
    病院  神社
    都市  学校
"""

from __future__ import annotations

from .models import ANRYAKU_PLUS, MOVE_CARDS, Board
from .orchestrate import Outcome

# 2×2 表示順（行ごと）。board.py の座標 (col,row) と一致させる。
_GRID_ROWS = [["病院", "神社"], ["都市", "学校"]]


# ---------- 確認盤面（2×2） ----------

def _cards_on_char(board: Board, name: str) -> list[str]:
    return [p.card for p in board.placements
            if p.target_kind == "character" and p.target == name]


def _board_cards(board: Board, area: str) -> list[str]:
    return [p.card for p in board.placements
            if p.target_kind == "board" and p.target == area]


def _cell(board: Board, area: str) -> str:
    """1エリア分のセル内容（<br>区切り）。"""
    parts = [f"**{area}**"]
    chars = [c for c in board.characters.values() if c.area == area]
    if not chars and not _board_cards(board, area):
        parts.append("（なし）")
    for c in sorted(chars, key=lambda c: c.name):
        mark = "✝" if not c.alive else ""
        cards = _cards_on_char(board, c.name)
        tail = ("　←" + "・".join(cards)) if cards else ""
        parts.append(f"{c.name}[{c.role}]{mark}{tail}")
    bc = _board_cards(board, area)
    if bc:
        parts.append("盤面: " + "・".join(bc))
    return "<br>".join(parts)


def render_board_grid(board: Board) -> str:
    """部分盤面を 2×2 のMarkdown表で描画する。"""
    lines = ["| | |", "|---|---|"]
    for row in _GRID_ROWS:
        left = _cell(board, row[0])
        right = _cell(board, row[1])
        lines.append(f"| {left} | {right} |")
    return "\n".join(lines)


def render_confirmation_board(outcome: Outcome) -> str:
    """感度/要確認フラグありのときの確認表示。フェイズで形を変える。"""
    if outcome.mm_unrest is not None:
        return render_mm_confirmation(outcome)
    return _render_action_confirmation(outcome)


def _render_action_confirmation(outcome: Outcome) -> str:
    """行動解決フェイズの「確認盤面（2×2＋仮定＋⚠）」。"""
    q = outcome.question
    out = ["### ⚠ 確認盤面（この前提で裁定しています）",
           "未指定の裁量カードは「無し(inert)」と仮定しています。"
           "下記の盤面が想定と違う場合は補ってください。",
           "",
           render_board_grid(q.board)]
    if q.assumptions:
        out.append("")
        out.append("**補完した仮定:**")
        out.extend(f"- {a}" for a in q.assumptions)
    if outcome.flags:
        out.append("")
        out.append("**⚠ 結論が仮定に依存しています（反転しうる）:**")
        for f in outcome.flags:
            out.append(
                f"- 対象「{f.target}」：現状の結論 **+{f.current}** ／ "
                f"{f.condition} → **+{f.alternative}** に反転"
            )
    return "\n".join(out)


# ---------- 脚本家能力フェイズ・不安 ----------

def render_mm_verdict(outcome: Outcome) -> str:
    """脚本家能力フェイズに置ける不安の数え上げ（決定的）。"""
    mm = outcome.mm_unrest
    assert mm is not None
    lines = ["**脚本家能力フェイズ・不安の数え上げ:**"]
    if mm.contributions:
        for c in mm.contributions:
            note = f"（{c.note}）" if c.note else ""
            lines.append(f"- {c.character}：{c.source} で **+{c.delta}**{note}")
    else:
        lines.append("- 確定して置けるソースなし")
    lines.append(f"- **最終結論: 脚本家能力フェイズに置ける不安は +{mm.total}（計{mm.total}個）**")
    for n in mm.notes:
        lines.append(f"- ※ {n}")
    if mm.flags:
        lines.append("**⚠ 確認が必要な点（結論が変わりうる）:**")
        for f in mm.flags:
            lines.append(f"- {f.character}：[{f.kind}] {f.detail}")
    return "\n".join(lines)


def render_mm_anyaku_verdict(outcome: Outcome) -> str:
    """脚本家能力フェイズに置ける暗躍の数え上げ（決定的）。"""
    ana = outcome.mm_anyaku
    assert ana is not None
    lines = ["**脚本家能力フェイズ・暗躍の数え上げ:**"]
    if ana.contributions:
        for c in ana.contributions:
            who = f"[{c.character}]" if c.character else ""
            lines.append(f"- {c.source}{who} で **+{c.delta}**（{c.placement}）")
    else:
        lines.append("- 確定して置けるソースなし")
    lines.append(f"- **最終結論: 脚本家能力フェイズに置ける暗躍は +{ana.total}（計{ana.total}個）**")
    if ana.target is not None and ana.target_total is not None:
        lines.append(
            f"- 対象「{ana.target}」に置ける暗躍は最大 **+{ana.target_total}**"
        )
    for n in ana.notes:
        lines.append(f"- ※ {n}")
    return "\n".join(lines)


def render_incident_verdict(outcome: Outcome) -> str:
    """事件フェイズの発生判定（決定的）。効果は裁定せず発生／非発生のみ。"""
    inc = outcome.incident
    assert inc is not None
    label = f"「{inc.incident_name}」" if inc.incident_name else ""
    lines = [f"**事件の発生判定{label}:**", f"- 犯人〈{inc.culprit}〉"]
    for r in inc.reasons:
        lines.append(f"  - {r}")
    if inc.occurs is None:
        lines.append("- **判定不能（要確認）**：不安臨界が確定しないため発生の有無を断定できない。")
    elif inc.occurs:
        lines.append("- **結論：発生する**（発生条件を両方満たす）。")
        lines.append(
            "- ※ 事件効果（何が起きるか）は脚本・事件種別に依存するためここでは裁定しない。"
            "条件を満たせば盤面が動かなくても『発生』（例＝黒猫の事件は効果が『何も起きない』でも"
            "発生宣言はされる）。個別脚本の犯人・効果はネタバレのため回答しない。"
        )
    else:
        lines.append("- **結論：発生しない**（発生条件を満たさない）。")
    return "\n".join(lines)


def render_goodwill_verdict(outcome: Outcome) -> str:
    """主人公能力フェイズ：友好能力の使用可否・拒否可否（決定的）。効果は裁定しない。"""
    ga = outcome.goodwill_ability
    assert ga is not None
    from .goodwill import REFUSE_FORCED, REFUSE_NONE, REFUSE_OPTIONAL
    label = f"（{ga.ability}）" if ga.ability else ""
    lines = [f"**友好能力の使用可否{label}:**", f"- 行使キャラ〈{ga.character}〉"]
    for r in ga.reasons:
        lines.append(f"  - {r}")
    if ga.hearts is not None and ga.once_per_loop:
        lines.append("  - ※この能力は1ループに1回まで。")

    if ga.usable is None:
        lines.append("- **判定不能（要確認）**：必要友好数や対象能力が確定しないため断定できない。")
    elif ga.usable:
        lines.append("- **使用条件：満たす（使える）**。")
    else:
        lines.append("- **使用条件：満たさない（使えない）**。")

    # 拒否可否（脚本家視点）。使用条件を満たす／満たさないに関わらず整理して示す。
    if ga.refuse == REFUSE_FORCED:
        lines.append(
            f"- 拒否：**脚本家は必ず拒否する**（〈{ga.character}〉の配役『{ga.role}』が絶対友好無視）"
            "＝結果としてこの能力は解決されない。"
        )
    elif ga.refuse == REFUSE_OPTIONAL:
        lines.append(
            f"- 拒否：**脚本家は拒否できる（任意）**（配役『{ga.role}』が友好無視）。拒否するかは脚本家の選択。"
        )
    elif ga.refuse == REFUSE_NONE:
        lines.append(
            "- 拒否：**脚本家は拒否できない**（行使キャラの配役が友好無視を持たない、"
            "またはこの能力は拒否対象外）。"
        )
    lines.append(
        "- ※ 能力の効果（何が起きるか）や対象・エリア条件は脚本・能力ごとに異なるためここでは裁定しない。"
    )
    return "\n".join(lines)


def render_loop_end_verdict(outcome: Outcome) -> str:
    """ループ終了フェイズ：タイミング裁定（決定的）。敗北条件そのものは脚本依存＝入力。"""
    le = outcome.loop_end
    assert le is not None
    lines = ["**ループ終了の裁定（タイミング）:**"]
    for r in le.reasons:
        lines.append(f"- {r}")
    if le.loop_ends_now:
        lines.append("- **この時点でループは終了する。**")
    else:
        lines.append("- **この時点ではループは終了しない（最終日まで継続）。**")
    if le.defeat is True:
        lines.append("- 勝敗：**このループは主人公の敗北**。")
    elif le.defeat is False:
        lines.append("- 勝敗：**このループは主人公の勝利**（敗北条件未達で終える）。")
    elif le.needs_confirmation:
        lines.append("- 勝敗：敗北条件の成否（脚本依存）が要確認＝ここでは断定しない。")
    return "\n".join(lines)


def render_mm_confirmation(outcome: Outcome) -> str:
    """MMフェイズ（不安）で条件依存/KB未確定があるときの確認表示。"""
    q = outcome.question
    mm = outcome.mm_unrest
    assert mm is not None
    out = ["### ⚠ 確認（この前提で数えています）",
           f"確定して置ける不安は現状 **+{mm.total}** です。下記の点が変わると結論も変わります。",
           "",
           render_board_grid(q.board)]
    if q.board_anyaku:
        out.append("")
        out.append("**前提のボード暗躍:** " + " / ".join(
            f"{a}={n}" for a, n in q.board_anyaku.items()))
    if mm.flags:
        out.append("")
        out.append("**⚠ 確認事項:**")
        for f in mm.flags:
            out.append(f"- {f.character}：[{f.kind}] {f.detail}")
    if q.assumptions:
        out.append("")
        out.append("**補完した仮定:**")
        out.extend(f"- {a}" for a in q.assumptions)
    return "\n".join(out)


# ---------- 裁定サマリ（回答LLMへ注入＆検算表示） ----------

def _moved_chars(board: Board) -> list[str]:
    names: list[str] = []
    for p in board.placements:
        if p.target_kind == "character" and p.card in MOVE_CARDS:
            if p.target not in names:
                names.append(p.target)
    return names


def _counter_line(kind: str, name: str, r) -> str:
    """不安／友好カウンターの集計行（最終値＋増減）。kind='不安' or '友好'。"""
    if r.invalid_reason:
        return f"- 「{name}」：{r.invalid_reason} → {kind}は変化なし（**{r.final}**）"
    raw = f"＋{r.plus} / −{r.minus}" if kind == "不安" else f"＋{r.plus}"
    parts = [f"初期{r.initial} / {raw}"]
    if getattr(r, "ban_ignored", False):
        parts.append(f"{kind}禁止あり（タイムトラベラーの【強制】で無視→不発）")
    elif r.ban_active:
        parts.append(f"{kind}禁止が実効（重なった{kind}カードを打ち消し）")
    elif kind == "不安" and r.initial + r.plus - r.minus < 0:
        parts.append("不安は0未満にできない（無い不安への−1は空振り）")
    elif kind == "不安" and r.plus > 0 and r.minus > 0:
        parts.append("不安+1が不安-1より先に解決")
    return f"- 「{name}」：{' / '.join(parts)} → 最終 **{r.final}**（増減 {r.delta:+d}）"


def render_verdict(outcome: Outcome) -> str:
    """エンジンの決定的結論を日本語テキストにする（違反 or 裁定）＋未実装特性の⚠。"""
    body = _render_verdict_body(outcome)
    warns = getattr(outcome, "trait_warnings", None)
    if warns:
        body += "\n**⚠ 未実装の特性あり（結果が変わりうる・要確認）:**\n" + "\n".join(
            f"- {w}" for w in warns)
    return body


def render_turn_end_verdict(outcome: Outcome) -> str:
    """ターン終了フェイズ・死亡判定の裁定テキスト。"""
    te = outcome.turn_end
    lines = ["**ターン終了フェイズ・死亡判定:**"]
    if te.forced_deaths:
        lines.append("**確定する死亡【強制】:**")
        for d in te.forced_deaths:
            lines.append(f"- 〈{d['name']}〉（{d['role']}）が死亡 — {d['cause']}")
    else:
        lines.append("- 強制の死亡（シリアルキラー等）は発生しません。")
    for r in te.reasons:
        if r.startswith("〈"):  # 不死で死ななかった等の補足
            lines.append(f"- {r}")
    if te.chains:
        lines.append("**連鎖する強制効果:**")
        lines += [f"- {c}" for c in te.chains]
    if te.loop_ends_now:
        v = "主人公の敗北" if te.defeat else "—"
        lines.append(f"**→ ループ終了が誘発されます（{v}）。**")
    if te.optional_actions:
        lines.append("**脚本家が選べば発動できる効果【任意】（自動では起きない）:**")
        lines += [f"- {o}" for o in te.optional_actions]
        lines.append("※【任意】なので最終盤面は脚本家の選択次第＝断定しない（要確認）。")
    if te.warnings:
        lines.append("**⚠ 未モデル・要確認:**")
        lines += [f"- {w}" for w in te.warnings]
    return "\n".join(lines)


def render_connected_verdict(outcome: Outcome) -> str:
    """連結裁定（行動解決→事件→ループ終了）を1つのテキストに。事件効果は裁定しない。"""
    if outcome.violations:
        lines = ["**前提がルール違反です（エンジンは数え上げを行いません）：**"]
        for v in outcome.violations:
            lines.append(f"- [{v.code}] {v.message}　（参照: {v.kb_ref}）")
        return "\n".join(lines)
    adj = outcome.adjudication
    lines = ["**連結裁定（行動解決 → 事件 → ループ終了）**", "", "**① 行動解決フェイズ:**"]
    changed = False
    _board = outcome.question.board
    for name, area in sorted(adj.moves.items()):
        _orig = _board.characters.get(name)
        _orig_area = _orig.area if _orig else None
        if name in adj.blocked_moves:
            lines.append(f"- 〈{name}〉は移動できず {area} に留まる（禁止エリア）")
            changed = True
        elif area != _orig_area:  # 実際に移動したキャラだけ表示
            lines.append(f"- 〈{name}〉が {area} へ移動")
            changed = True
    for tgt, tr in sorted(adj.targets.items()):
        if tr.delta or tr.has_kinshi:
            note = "（暗躍禁止で打ち消し）" if (tr.has_kinshi and tr.kinshi_active) else ""
            lines.append(f"- {tgt} の暗躍：+{tr.delta}{note}")
            changed = True
    for name, cr in sorted(adj.unrest.items()):
        if cr.initial != cr.final:
            lines.append(f"- 〈{name}〉の不安：{cr.initial} → {cr.final}")
            changed = True
    for name, cr in sorted(adj.goodwill.items()):
        if cr.initial != cr.final:
            lines.append(f"- 〈{name}〉の友好：{cr.initial} → {cr.final}")
            changed = True
    if not changed:
        lines.append("- （解決対象の変化なし）")
    lines.append("")
    lines.append("**② 事件フェイズ:**")
    if outcome.incident is not None:
        lines.append(render_incident_verdict(outcome))
    else:
        lines.append("- 犯人が指定されていないため事件の発生判定は行いません。")
    lines.append("")
    lines.append("**③ ループ終了フェイズ（タイミング）:**")
    if outcome.loop_end is not None:
        lines.append(render_loop_end_verdict(outcome))
    else:
        lines.append(
            "- 敗北条件が途中で成立してもループは最終日まで中断しません（00:68）。"
            "事件の効果でキーパーソン/主人公が死亡した場合のみ、その時点でループ終了効果が発生します"
            "（効果はネタバレ回避のため裁定しません）。")
    return "\n".join(lines)


def _render_verdict_body(outcome: Outcome) -> str:
    # ★連結裁定（行動解決＋事件＋ループ終了を同時に持つ）＝専用レンダラを最優先。
    if outcome.question.phase == "connected":
        return render_connected_verdict(outcome)
    # ターン終了フェイズ・死亡判定
    if outcome.turn_end is not None:
        return render_turn_end_verdict(outcome)
    # 脚本家能力フェイズ・不安の数え上げ
    if outcome.mm_unrest is not None:
        return render_mm_verdict(outcome)
    # 脚本家能力フェイズ・暗躍の数え上げ
    if outcome.mm_anyaku is not None:
        return render_mm_anyaku_verdict(outcome)
    # 事件フェイズ・発生判定
    if outcome.incident is not None:
        return render_incident_verdict(outcome)
    # 主人公能力フェイズ・友好能力の使用可否
    if outcome.goodwill_ability is not None:
        return render_goodwill_verdict(outcome)
    # ループ終了フェイズ・タイミング裁定
    if outcome.loop_end is not None:
        return render_loop_end_verdict(outcome)

    q = outcome.question
    lines: list[str] = []

    if outcome.violations:
        lines.append("**前提がルール違反です（エンジンは数え上げを行いません）：**")
        for v in outcome.violations:
            lines.append(f"- [{v.code}] {v.message}　（参照: {v.kb_ref}）")
        lines.append("→ 誤った前提のまま計算せず、上記の違反を指摘してください。")
        return "\n".join(lines)

    adj = outcome.adjudication
    assert adj is not None  # 違反なしならadjは必ず存在

    # 移動結果
    moved = _moved_chars(q.board)
    if moved:
        from .data import ROLE_IGNORES_ANRYAKU_KINSHI
        lines.append("**移動の解決:**")
        for name in moved:
            dest = adj.moves.get(name, "?")
            if name in adj.blocked_moves:
                # 移動できず留まった。回答LLMが「移動した前提」で考え直して誤らないよう、
                # 結果と帰結（暗躍禁止無視がどこまで及ぶか）を決定的に書き切る。
                lines.append(
                    f"- ⚠ {name} は移動できず {dest} に留まる"
                    f"（移動先が {name} の禁止エリアのため移動不成立）。"
                )
                ch = q.board.characters.get(name)
                if ch is not None and ch.role in ROLE_IGNORES_ANRYAKU_KINSHI:
                    lines.append(
                        f"  → {name} は {dest} に留まったので、暗躍禁止の無視が及ぶのは {dest} だけ。"
                        f"移動しようとした先のボードの暗躍禁止は**無視されず実効**する"
                        f"（＝そのボードの暗躍+は打ち消される）。"
                        f"「カルティストが病院へ移動して無視した」前提で考え直さないこと。"
                    )
            elif name in adj.move_banned:
                lines.append(
                    f"- {name} は移動せず {dest} に留まる"
                    f"（主人公の『移動禁止』が重なり、移動カードが打ち消されたため）。"
                )
            else:
                lines.append(f"- {name} → {dest}")

    # 暗躍の数え上げ（質問対象が複数ボードの場合に備え、暗躍が関わる全ボードを列挙）
    def _detail_line(name: str, tr) -> str:
        detail = [f"素の暗躍+ = {tr.anyaku_plus}"]
        if tr.invalid_reason:
            detail.append(f"無効化: {tr.invalid_reason}")
        if tr.has_kinshi:
            if tr.ignored_by_cultist:
                detail.append("暗躍禁止あり（カルティストが無視→不発）")
            elif not tr.kinshi_active:
                detail.append("暗躍禁止あり（主人公2枚で自滅→不発）")
            else:
                detail.append("暗躍禁止が実効（暗躍+を打ち消し）")
        return f"- 「{name}」：{' / '.join(detail)} → 最終 **+{tr.delta}**"

    from .board import AREAS as _AREAS
    board_targets = {
        name: tr for name, tr in adj.targets.items() if tr.target_kind == "board"
    }
    question_is_board = q.target_kind == "board" and q.target in _AREAS
    qtr = adj.targets.get(q.target)
    # 暗躍節は「暗躍カードがある」または「ボードについて問われている」ときのみ出す
    # （不安/友好だけの質問で誤って暗躍+0を表示しないため）。
    if adj.targets or question_is_board:
        lines.append("**暗躍の数え上げ:**")
        # ボードを先に、その後キャラ（全ボード質問でも各キャラの暗躍を漏らさない）。
        for name, tr in board_targets.items():
            lines.append(_detail_line(name, tr))
        # 質問対象が実在ボードなのに暗躍+カードが無い→明示的に+0（引っかけ対策）。
        if question_is_board and q.target not in adj.targets:
            lines.append(
                f"- 対象「{q.target}」には暗躍+カードが無い → **{q.target} は +0**"
            )
        for name, tr in adj.targets.items():
            if tr.target_kind != "board":
                lines.append(_detail_line(name, tr))

    # 不安の集計（キャラのみ）
    if adj.unrest:
        lines.append("**不安の集計:**")
        for name, r in adj.unrest.items():
            lines.append(_counter_line("不安", name, r))

    # 友好の集計（キャラのみ）
    if adj.goodwill:
        lines.append("**友好の集計:**")
        for name, r in adj.goodwill.items():
            lines.append(_counter_line("友好", name, r))

    # 幻想がいれば「同エリアのボードのカード効果を受ける」特性を明示（数値は上に反映済み）。
    if any(c.name == "幻想" and c.alive for c in q.board.characters.values()):
        lines.append(
            "**※幻想の特性:** 幻想はいるエリアのボードに置かれたカード（移動/不安/友好等）の効果を"
            "**受ける**（幻想自身は行動カードを被セットできない）。上記の幻想の移動/カウンターはこれを反映済み。"
        )

    # ボードに置いたが解決されない非暗躍カード＝ブラフ（無効）。前提違反ではない（KB: 10）。
    if getattr(adj, "board_bluffs", None):
        lines.append("**ボードのブラフ（解決されない）:**")
        for owner, card, bd in adj.board_bluffs:
            who = "脚本家" if owner == "mastermind" else "主人公"
            lines.append(
                f"- {who}が {bd} ボードに置いた〈{card}〉は、ボードでは解決されない"
                "（暗躍＋／暗躍禁止以外はボードで効果を持たない＝ブラフ・無効）。"
            )

    # 質問対象に関係する行動カードが1枚も無い場合の中立フォールバック
    # （adj.moves は生存キャラ全員を含むため、移動カードの有無は moved で判定する）。
    # ただし対象がフェイズ全体（kind="phase"。例「行動解決フェイズ」）のときは、
    # 上で全キャラ・全ボードの結果を列挙済みなので、この紛らわしい行は出さない。
    target_covered = (
        q.target in adj.targets or q.target in adj.unrest or q.target in adj.goodwill
        or q.target in moved or question_is_board
    )
    if not target_covered and q.target_kind != "phase":
        lines.append(
            f"- 対象「{q.target}」に該当する行動カード（移動/暗躍/不安/友好）はありません（増減なし）。"
        )

    if q.assumptions:
        lines.append("**仮定:** " + " / ".join(q.assumptions))

    if outcome.needs_confirmation:
        lines.append("**※感度フラグあり**：この結論は inert 仮定に依存。確認盤面を提示済み。")

    return "\n".join(lines)


# ---------- 翻訳プロンプト用の語彙表（エンジン定数から自動生成） ----------

def vocab_cheatsheet() -> str:
    """翻訳LLMに渡す語彙（この語彙以外はload_questionが弾く）。"""
    from .board import AREAS
    from .data import CHARACTER_FORBIDDEN, ROLE_CLAUSE_ABILITY
    from .models import MASTERMIND_CARDS, PROTAGONIST_CARDS

    areas = " / ".join(AREAS.keys())
    mm = " / ".join(sorted(MASTERMIND_CARDS))
    pr = " / ".join(sorted(PROTAGONIST_CARDS))
    roles = " / ".join(ROLE_CLAUSE_ABILITY.keys())
    chars = " / ".join(CHARACTER_FORBIDDEN.keys())
    return (
        f"- エリア（board）: {areas}\n"
        f"- 脚本家(mastermind)カード: {mm}\n"
        f"- 主人公(p1/p2/...)カード: {pr}\n"
        f"- 役職(role): {roles}\n"
        f"- キャラ(name): {chars}\n"
        "（暗躍+/暗躍禁止を置けるのは board か character。暗躍+は脚本家のみ。"
        "移動カードは character に置く。forbidden は分かる時のみ・省略可）"
    )
