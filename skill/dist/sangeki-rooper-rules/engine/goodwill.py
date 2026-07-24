"""主人公能力フェイズの決定的リゾルバ（フェーズC＝友好能力の使用可否・拒否可否）。

範囲（KB: 00 フェイズ6／20 友好能力）:
- 使用可否＝「行使キャラが生存」かつ「そのキャラに必要友好数（ハート）以上の友好」。
- 拒否可否＝脚本家が拒否できるのは、**能力を使うキャラの役職**が友好無視/絶対友好無視を持つ時のみ。
    絶対友好無視 → 必ず拒否（強制）。友好無視 → 脚本家の任意で拒否可。それ以外 → 拒否できない。
    例外：イレギュラー（自身役職開示）・ナースの能力は、役職に友好無視があっても拒否できない（KB: 20）。
- 能力の「効果」は裁定しない（対象条件・エリア制約は能力ごとに異なる＝一般説明に留める）。
- 必要友好数がKB範囲外／能力が特定できない場合は要確認（捏造しない）。

推論せず Board（友好カウンタ・役職込み）＋能力指定から決定的に判定するだけ＝テスト可能。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import (
    UNREFUSABLE_ABILITY_CHARS,
    goodwill_abilities_of,
    role_absolute_friendship_ignore,
    role_has_friendship_ignore,
)
from .models import Board

# 拒否可否の区分。
REFUSE_NONE = "不可"      # 脚本家は拒否できない
REFUSE_OPTIONAL = "任意"  # 友好無視 → 脚本家が拒否するか選べる
REFUSE_FORCED = "強制"    # 絶対友好無視 → 必ず拒否される


@dataclass
class GoodwillAbilityResult:
    character: str
    role: str
    ability: str | None            # 評価した能力名（None＝能力なし/特定不能）
    hearts: int | None             # 必要友好数（Noneなら要確認）
    goodwill: int
    alive: bool
    once_per_loop: bool
    usable: bool | None            # 使用条件を満たすか（要確認なら None）
    refuse: str                    # REFUSE_NONE / REFUSE_OPTIONAL / REFUSE_FORCED
    reasons: list[str] = field(default_factory=list)
    needs_confirmation: bool = False


def _refuse_kind(character: str, role: str) -> str:
    # 能力単位の拒否不可例外（イレギュラー・ナース）が最優先。
    if character in UNREFUSABLE_ABILITY_CHARS:
        return REFUSE_NONE
    if role_absolute_friendship_ignore(role):
        return REFUSE_FORCED
    if role_has_friendship_ignore(role):
        return REFUSE_OPTIONAL
    return REFUSE_NONE


def resolve_goodwill_ability(board: Board, spec: dict) -> GoodwillAbilityResult:
    """友好能力の使用可否・拒否可否を判定する。

    spec: {"character": <キャラ名>, "ability": <能力名(任意)>, "hearts": <必要友好数の明示上書き(任意)>}。
    """
    character = spec["character"]
    c = board.char(character)
    role = c.role if c is not None else spec.get("role", "パーソン")
    alive = c.alive if c is not None else True
    goodwill = c.goodwill if c is not None else 0
    refuse = _refuse_kind(character, role)

    def _confirm(reason: str, ability: str | None = None) -> GoodwillAbilityResult:
        return GoodwillAbilityResult(
            character=character, role=role, ability=ability, hearts=None,
            goodwill=goodwill, alive=alive, once_per_loop=False,
            usable=None, refuse=refuse, reasons=[reason], needs_confirmation=True,
        )

    # 妹は特性により友好無視/絶対友好無視の役職に配役できない（KB: 30）＝前提の配役が不正。
    if character == "妹" and role_has_friendship_ignore(role):
        return _confirm(
            f"〈妹〉は特性により友好無視/絶対友好無視の役職に配役できない"
            f"（前提の配役『{role}』が不正・KB: 30）＝拒否可否の前提から要確認"
        )

    abilities = goodwill_abilities_of(character)
    if abilities is None:
        return _confirm(f"〈{character}〉の友好能力はKB範囲外（未収録）＝使用可否は要確認")

    # 使う能力を特定する。
    ability_name = spec.get("ability")
    hearts_override = spec.get("hearts")
    chosen: dict | None = None
    if hearts_override is not None:
        chosen = {"name": ability_name or "(指定能力)", "hearts": int(hearts_override),
                  "once_per_loop": False}
    elif ability_name:
        # ★完全一致を最優先（部分一致は曖昧＝「開示」「不安除去」等が複数能力にヒットする）。
        #   完全一致が1つならそれ。無ければ部分一致にフォールバックし、複数一致は要確認。
        exact = [a for a in abilities if a["name"] == ability_name]
        if len(exact) == 1:
            chosen = exact[0]
        else:
            matches = [a for a in abilities
                       if ability_name in a["name"] or a["name"] in ability_name]
            if len(matches) == 1:
                chosen = matches[0]
            elif not matches:
                return _confirm(
                    f"〈{character}〉に一致する友好能力が見つからない（要確認）: {ability_name}")
            else:
                _names = " / ".join(a["name"] for a in matches)
                return _confirm(
                    f"〈{character}〉の能力指定が曖昧（『{ability_name}』が複数に部分一致：{_names}）"
                    "＝能力名を正確に指定してください（要確認）")
    else:
        if len(abilities) == 0:
            return GoodwillAbilityResult(
                character=character, role=role, ability=None, hearts=None,
                goodwill=goodwill, alive=alive, once_per_loop=False,
                usable=False, refuse=REFUSE_NONE,
                reasons=[f"〈{character}〉は友好能力を持たない"], needs_confirmation=False,
            )
        if len(abilities) == 1:
            chosen = abilities[0]
        else:
            names = " / ".join(a["name"] for a in abilities)
            return _confirm(f"〈{character}〉は複数の友好能力を持つ＝どれかを指定要（{names}）")

    hearts = chosen["hearts"]
    reasons: list[str] = []
    if alive:
        reasons.append(f"〈{character}〉は生存 ✓")
    else:
        reasons.append(f"〈{character}〉は死亡（死体）✗ → 友好能力は使えない")
    if goodwill >= hearts:
        reasons.append(f"友好 {goodwill} ≥ 必要友好 {hearts} ✓")
    else:
        reasons.append(f"友好 {goodwill} < 必要友好 {hearts} ✗")

    usable = alive and (goodwill >= hearts)
    return GoodwillAbilityResult(
        character=character, role=role, ability=chosen["name"], hearts=hearts,
        goodwill=goodwill, alive=alive, once_per_loop=chosen["once_per_loop"],
        usable=usable, refuse=refuse, reasons=reasons, needs_confirmation=False,
    )
