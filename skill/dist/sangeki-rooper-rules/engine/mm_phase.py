"""脚本家能力フェイズの決定的リゾルバ（不安カウンターの付与数）。

範囲（このスレッドでの合意・C案）:
- 「脚本家能力フェイズに不安を何個置けるか」を、KBに明文があるソースだけで決定的に数える。
- ソース（KB: 20/40/50/60）:
  (1) ミスリーダーの追加能力 …… 同エリア1人に不安+1（自身可・C-4／友好無視は不要）。
  (2) ファクター（学校に暗躍2以上）…… ミスリーダー能力を得る → 不安+1。KB: 50。
  (3) 医者の友好能力 …… +1。ただし「友好無視を持ち、かつ友好2以上」＋同エリアに他キャラ。KB: 60 B-4。
- KBに明文が無い不安付与（軍人・教師）は確定に数えず「要確認」フラグにする（捏造しない）。
- マスコミは脚本家使用不可が確定（KB: B-6）なので寄与しない。
- 1キャラが役職能力とキャラ友好能力の両方を満たせば両方加算（B-5：医者＝ファクター等で計2）。

ここでは推論せず、Board（友好カウンタ込み）から決定的に数えるだけ＝テスト可能。
事件・暗躍の生成や勝敗は範囲外。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import (
    FACTOR_GAINS_MISLEADER_AREA,
    MM_ANRYAKU_RULES,
    MM_UNREST_CHAR_ABILITY,
    MM_UNREST_UNCONFIRMED,
    ROLE_MM_ANRYAKU,
    ROLE_MM_UNREST,
    role_has_friendship_ignore,
)
from .board import AREAS
from .models import Board


@dataclass
class UnrestContribution:
    """確定して数えた不安付与1件。"""

    character: str
    source: str   # 例: "ミスリーダー(役職能力)" / "医者(友好能力)"
    delta: int
    note: str = ""


@dataclass
class MMUnrestFlag:
    """結論が条件に依存／KB未確定で確認が要る点。"""

    character: str
    kind: str     # "条件依存" | "要確認(KB未確定)"
    detail: str


@dataclass
class MMUnrestResult:
    contributions: list[UnrestContribution] = field(default_factory=list)
    flags: list[MMUnrestFlag] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 「使えない理由」等の断定的な補足

    @property
    def total(self) -> int:
        return sum(c.delta for c in self.contributions)

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.flags)


def _alive_same_area_others(board: Board, name: str, area: str) -> list[str]:
    return [
        o.name for o in board.characters.values()
        if o.alive and o.name != name and o.area == area
    ]


def resolve_mm_phase_unrest(
    board: Board,
    board_anyaku: dict[str, int] | None = None,
) -> MMUnrestResult:
    """盤面から、脚本家能力フェイズに付与できる不安の総数を決定的に数える。

    board_anyaku: ボード→暗躍カウンタ数（ファクターの条件判定に使用。省略時は0扱い）。
    """
    board_anyaku = board_anyaku or {}
    res = MMUnrestResult()

    for c in board.characters.values():
        if not c.alive:
            continue

        # (1) 役職追加能力：ミスリーダー（同エリア1人・自身可 → 必ず対象あり）
        if c.role in ROLE_MM_UNREST:
            spec = ROLE_MM_UNREST[c.role]
            res.contributions.append(UnrestContribution(
                c.name, f"{c.role}(役職追加能力)", spec["delta"],
                "同エリアのキャラ1人に不安+1（自身を対象に取れる）",
            ))
        # (1b) ファクター：学校に暗躍2以上のときミスリーダー能力（不安+1）
        elif c.role == "ファクター":
            anyaku = board_anyaku.get(FACTOR_GAINS_MISLEADER_AREA, 0)
            if anyaku >= 2:
                res.contributions.append(UnrestContribution(
                    c.name, "ファクター→ミスリーダー能力", 1,
                    f"{FACTOR_GAINS_MISLEADER_AREA}に暗躍{anyaku}（2以上）のため不安+1を得る",
                ))
            else:
                res.flags.append(MMUnrestFlag(
                    c.name, "条件依存",
                    f"ファクターは{FACTOR_GAINS_MISLEADER_AREA}に暗躍2以上のときミスリーダー能力(不安+1)を得る。"
                    f"現状の{FACTOR_GAINS_MISLEADER_AREA}暗躍は{anyaku}（未充足/未指定）。",
                ))

        # (2) キャラ友好能力（確定ソース＝医者）。役職能力とは独立に加算しうる。
        if c.name in MM_UNREST_CHAR_ABILITY:
            spec = MM_UNREST_CHAR_ABILITY[c.name]
            has_ignore = role_has_friendship_ignore(c.role)
            enough = c.goodwill >= spec["hearts"]
            needs_target = spec["target"] == "other_same_area"
            has_target = (
                bool(_alive_same_area_others(board, c.name, c.area))
                if needs_target else True
            )
            qualifies = (not spec["requires_ignore"] or has_ignore) and enough
            if qualifies:
                # capability質問（「何個置けるか」）なので、能力条件を満たせば計上する。
                # 同エリア対象が盤面に未記載なら注記で明示（実プレイでは対象が要る）。
                note = f"友好無視＋友好{spec['hearts']}以上"
                if needs_target and not has_target:
                    note += "（同エリアに対象キャラ1人が必要）"
                res.contributions.append(UnrestContribution(
                    c.name, f"{c.name}(友好能力)", spec["delta"], note,
                ))
            else:
                reasons: list[str] = []
                if spec["requires_ignore"] and not has_ignore:
                    reasons.append("配役が友好無視を持たない")
                if not enough:
                    reasons.append(f"友好{spec['hearts']}未満（現在{c.goodwill}）")
                if reasons:
                    res.notes.append(
                        f"{c.name}の友好能力は脚本家能力フェイズで使えない（{'／'.join(reasons)}）"
                    )

        # (3) KB未確定の不安付与（軍人・教師）が条件を満たしうる → 要確認フラグ
        if c.name in MM_UNREST_UNCONFIRMED:
            spec = MM_UNREST_UNCONFIRMED[c.name]
            if role_has_friendship_ignore(c.role) and c.goodwill >= spec["hearts"]:
                res.flags.append(MMUnrestFlag(
                    c.name, "要確認(KB未確定)", spec["note"],
                ))

    return res


# ===========================================================================
# 脚本家能力フェイズ：暗躍(anyaku)の数え上げ
# ===========================================================================
# ソース（KB: 40/50/60）:
#   - クロマク（役職追加能力）…… 同エリアのキャラ1人 or 自ボードに暗躍+1。
#   - 不穏な噂（ルールX）……… 任意のボード1つに暗躍+1（1ループ1回）。
# これらは「脚本家能力フェイズ」の暗躍なので、行動解決フェイズ専用の暗躍禁止では止まらない
# （KB: 60 A16/Q3）。マスコミの暗躍(能力2)は脚本家使用不可（B-6）なので含めない。


@dataclass
class AnryakuContribution:
    source: str                      # 例: "クロマク(役職追加能力)" / "不穏な噂(ルールX)"
    character: str | None            # クロマクのキャラ名 / ルール由来なら None
    delta: int
    placement: str                   # どこに置けるかの説明
    can_target_boards: frozenset[str]  # このソースがボード暗躍を置けるボード集合
    can_target_chars: frozenset[str]   # このソースがキャラ暗躍を置けるキャラ集合


@dataclass
class MMAnryakuResult:
    contributions: list[AnryakuContribution] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    target: str | None = None
    target_kind: str | None = None   # "board" | "character" | None(総数)

    @property
    def total(self) -> int:
        """このフェイズに置ける暗躍の最大総数（各ソース独立）。"""
        return sum(c.delta for c in self.contributions)

    @property
    def target_total(self) -> int | None:
        """特定の対象（ボード/キャラ）に置ける最大暗躍。target未指定なら None。"""
        if self.target is None or self.target_kind is None:
            return None
        if self.target_kind == "board":
            return sum(c.delta for c in self.contributions if self.target in c.can_target_boards)
        if self.target_kind == "character":
            return sum(c.delta for c in self.contributions if self.target in c.can_target_chars)
        return None

    @property
    def needs_confirmation(self) -> bool:
        return False  # 暗躍は条件依存の確認フラグを持たない（クロマク/不穏な噂は無条件）


def resolve_mm_phase_anyaku(
    board: Board,
    rules: list[str] | None = None,
    target: str | None = None,
    target_kind: str | None = None,
) -> MMAnryakuResult:
    """脚本家能力フェイズに置ける暗躍を決定的に数える。

    rules: 脚本で使用中のルール名（"不穏な噂" を含むかで判定）。
    target/target_kind: 特定ボード/キャラに置ける数を知りたいとき指定。
    """
    rules = rules or []
    res = MMAnryakuResult(target=target, target_kind=target_kind)

    # 役職：クロマク（同エリアのキャラ or 自ボード）
    for c in board.characters.values():
        if not c.alive or c.role not in ROLE_MM_ANRYAKU:
            continue
        same_area_chars = frozenset(
            o.name for o in board.characters.values() if o.alive and o.area == c.area
        )
        res.contributions.append(AnryakuContribution(
            source="クロマク(役職追加能力)",
            character=c.name,
            delta=ROLE_MM_ANRYAKU[c.role]["delta"],
            placement=f"{c.area}（自ボード）または{c.area}にいるキャラ1人に暗躍+1",
            can_target_boards=frozenset({c.area}),
            can_target_chars=same_area_chars,
        ))

    # ルール：不穏な噂（任意のボード1つ・1/L）
    for r in rules:
        if r in MM_ANRYAKU_RULES:
            res.contributions.append(AnryakuContribution(
                source="不穏な噂(ルールX)",
                character=None,
                delta=MM_ANRYAKU_RULES[r]["delta"],
                placement="任意のボード1つに暗躍+1（1ループ1回）",
                can_target_boards=frozenset(AREAS.keys()),
                can_target_chars=frozenset(),
            ))

    if res.contributions:
        res.notes.append(
            "これらは脚本家能力フェイズの暗躍なので、行動解決フェイズ専用の暗躍禁止では止まらない"
            "（→60 A16/Q3）。"
        )
    return res
