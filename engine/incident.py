"""事件フェイズの発生判定（フェーズB）。

範囲（KB: 00 事件の発生／30 不安臨界）:
- 「予定された事件が発生するか」を、決定的な2条件だけで判定する。
    1. 犯人となるキャラクターが生存している。
    2. 犯人に、その犯人の不安臨界以上の不安カウンターが置かれている。
  両方を満たすと必ず発生（KB: 00）。
- ネタバレ方針：犯人はユーザー入力。事件効果（誰が死ぬ等）は脚本・事件種別依存なので
  ここでは一切裁定せず、発生／非発生だけを返す（効果は回答層で一般説明に留める）。
- ★「発生したが何も起きなかった」区別：条件を満たせば盤面が動かなくても『発生』（KB: 00, 60）。
  例＝黒猫は不安臨界0かつ特性で事件効果が「何も起きない」＝発生宣言はされるが盤面は動かない。
- 犯人の不安臨界がKB範囲外（未収録キャラ）なら判定せず「要確認」を返す（捏造しない）。

推論せず Board（不安カウンタ込み）＋犯人指定から決定的に判定するだけ＝テスト可能。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import unrest_threshold_of
from .models import Board


@dataclass
class IncidentResult:
    culprit: str
    incident_name: str | None
    alive: bool
    unrest: int
    threshold: int | None          # 犯人の不安臨界（Noneなら要確認＝KB範囲外）
    occurs: bool | None            # 発生するか（判定不能なら None）
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.occurs is None


def effective_unrest_for_incident(c) -> int:
    """発生判定で「不安」として数える値（★A.I. 合算の**単一ソース**・W4 2026-09-04）。

    - 通常キャラ＝素の `unrest`。
    - **A.I.**＝特性②（KB `rules/30_characters.md:50`・現物カード確認 2026-07-08）＝自身が犯人の
      事件の発生判定では置かれている**全てのカウンター**（友好・暗躍・護衛も）を不安として扱う
      ＝ `unrest + goodwill + anyaku + guard`。
    - `c` は `name/unrest/goodwill/anyaku/guard` 属性を持つ任意のオブジェクト（engine の
      `Character` でも sim の `CharState` でも可）。`guard` は無ければ 0 扱い。
    - `resolve_incident`（製品トラック）と `sim/effects.py` の公開 `eligible`（AIトラック）が
      **同じ関数**を使う＝合算の算術を二重定義しない（`docs/バックログ_構想メモ_FableA.md` §72-135）。
    """
    if getattr(c, "name", None) == "A.I.":
        return (c.unrest + c.goodwill + c.anyaku + getattr(c, "guard", 0))
    return c.unrest


def resolve_incident(board: Board, incident: dict, set_name: str = "BTX") -> IncidentResult:
    """事件の発生判定。

    incident: {"culprit": <キャラ名>, "name": <事件名(任意・表示用)>,
               "unrest_threshold": <任意・不安臨界の明示上書き>}。
    犯人キャラは board.characters に居る前提（translate 側で検証）。
    """
    culprit = incident["culprit"]
    name = incident.get("name")
    c = board.char(culprit)

    # ★A.I.の特性②（現物カード確認 2026-07-08）：自身が犯人の事件の発生判定では、
    #   置かれている全てのカウンター（友好・暗躍・護衛も）を不安カウンターとしても扱う。
    #   算術は effective_unrest_for_incident が単一ソース（sim 側の公開 eligible も同じ関数）。
    ai_total = None
    if c is not None and culprit == "A.I.":
        ai_total = effective_unrest_for_incident(c)

    # 犯人が盤面未定義（通常 translate で弾くが、防御的に扱う）。
    if c is None:
        return IncidentResult(
            culprit=culprit, incident_name=name, alive=False, unrest=0,
            threshold=None, occurs=None,
            reasons=[f"犯人〈{culprit}〉が盤面に定義されていない（要確認）"],
        )

    alive = c.alive
    unrest = c.unrest
    if ai_total is not None and ai_total != unrest:
        unrest = ai_total  # A.I.は全カウンター合算で臨界判定（理由行は下で追記）
    # 不安臨界：明示指定があれば優先、無ければ data.py（キャラ名）から。
    threshold = incident.get("unrest_threshold")
    if threshold is None:
        threshold = unrest_threshold_of(culprit)

    reasons: list[str] = []

    if threshold is None:
        reasons.append(
            f"犯人〈{culprit}〉の不安臨界がKB範囲外（未収録）＝発生判定は要確認"
        )
        return IncidentResult(
            culprit=culprit, incident_name=name, alive=alive, unrest=unrest,
            threshold=None, occurs=None, reasons=reasons,
        )

    # 条件1：生存
    if alive:
        reasons.append(f"犯人〈{culprit}〉は生存 ✓")
    else:
        reasons.append(f"犯人〈{culprit}〉は死亡（死体）✗ → 発生条件を満たさない")

    if ai_total is not None and ai_total != c.unrest:
        reasons.append(
            f"A.I.の特性：発生判定では全カウンターを不安として扱う＝"
            f"不安{c.unrest}＋友好{c.goodwill}＋暗躍{c.anyaku}＋護衛{getattr(c, 'guard', 0)}"
            f" → 実効不安 {ai_total}（KB: 30）"
        )

    # ★不安の省略検知：犯人の不安が未指定（＝0と仮定）で、臨界>0 かつ 0<臨界のため
    #   「発生しない」と断定してしまう場合は、実際の不安次第で発生しうる＝要確認にする
    #   （翻訳がunrestを書き忘れて誤って『発生しない』と断定する事故を防ぐ。unrest_specifiedは
    #   load_question が付与。明示された不安・臨界0（黒猫）・既に臨界以上の場合は通常どおり裁定）。
    unrest_specified = incident.get("unrest_specified", True)
    if alive and threshold > 0 and unrest < threshold and not unrest_specified:
        reasons.append(
            f"犯人〈{culprit}〉の不安が未指定＝0と仮定したが不安臨界{threshold}未満。"
            "実際の不安次第で発生しうる＝要確認（不安を明示してください）"
        )
        return IncidentResult(
            culprit=culprit, incident_name=name, alive=alive, unrest=unrest,
            threshold=threshold, occurs=None, reasons=reasons,
        )

    # 条件2：不安 ≥ 不安臨界
    if unrest >= threshold:
        reasons.append(f"不安 {unrest} ≥ 不安臨界 {threshold} ✓")
    else:
        reasons.append(f"不安 {unrest} < 不安臨界 {threshold} ✗")

    occurs = alive and (unrest >= threshold)
    return IncidentResult(
        culprit=culprit, incident_name=name, alive=alive, unrest=unrest,
        threshold=threshold, occurs=occurs, reasons=reasons,
    )
