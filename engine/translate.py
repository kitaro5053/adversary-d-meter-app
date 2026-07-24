"""翻訳層（NL→形式表現）。

役割分担（このスレッドでの合意）:
- LLM は「自然文 → 構造化JSON（盤面の部分指定）」だけを担う（PROMPT 参照）。
- ここはその JSON を検証して Board に変換する純粋関数。LLMは呼ばない＝テスト可能。
- 変換時にスキーマ違反・未知エリア・未知カードを弾く（捏造させない）。

JSONスキーマ（LLMの出力契約）:
{
  "set": "FS" | "BTX",                      # 任意。既定 BTX
  "question_target": {"name": "神社", "kind": "board"},  # 何を問われているか
  "characters": [
    {"name": "カルティスト", "role": "カルティスト", "area": "神社",
     "alive": true, "forbidden": ["病院"]}   # forbidden 省略時は data.py から補完
  ],
  "placements": [
    {"owner": "mastermind", "card": "暗躍+2", "target": "神社", "target_kind": "board"},
    {"owner": "p1", "card": "暗躍禁止", "target": "神社", "target_kind": "board"}
  ],
  "assumptions": ["未指定カードは無し(inert)"]   # 任意。AIが補完した仮定の説明
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .board import AREAS
from .data import forbidden_of
from .models import (
    MASTERMIND_CARDS,
    PROTAGONIST_CARDS,
    Board,
    Character,
    Placement,
)

VALID_KINDS = {"board", "character"}
VALID_SETS = {"FS", "BTX"}
# 質問対象 question_target.kind は board/character に加え、フェイズ全体を問う "phase" を許可。
VALID_QT_KINDS = VALID_KINDS | {"phase"}
# 解決フェイズ。既定は行動解決（暗躍/移動）。脚本家能力フェイズの不安/暗躍、事件発生判定を追加。
VALID_PHASES = {
    "action_resolution", "mastermind_unrest", "mastermind_anyaku", "incident",
    "goodwill_ability", "loop_end", "turn_end",
    "connected",  # ★連結裁定：行動解決→事件→ループ終了を1問で追う（2026-07-06）
}


class TranslationError(ValueError):
    """LLMの出力JSONがスキーマ／ルール語彙に反する場合。"""


@dataclass
class Question:
    board: Board
    target: str
    target_kind: str
    set_name: str
    assumptions: list[str]
    phase: str = "action_resolution"             # action_resolution | mastermind_unrest | mastermind_anyaku | incident
    board_anyaku: dict[str, int] = field(default_factory=dict)  # ファクター条件等で使用
    rules: list[str] = field(default_factory=list)  # 使用中ルール（"不穏な噂" 等）
    incident: dict = field(default_factory=dict)  # 事件発生判定（{culprit, name?, unrest_threshold?}）
    goodwill_ability: dict = field(default_factory=dict)  # 友好能力の使用可否（{character, ability?, hearts?}）
    loop_end: dict = field(default_factory=dict)  # ループ終了のタイミング裁定（{protagonist_death?, ...}）
    turn_end: dict = field(default_factory=dict)  # ターン終了の死亡判定（{is_final_day?, protagonist_immortal?}）


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise TranslationError(msg)


def load_question(data: dict | str) -> Question:
    """LLM出力（dict もしくは JSON文字列）を検証して Question に変換する。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise TranslationError(f"JSONとして解釈できません: {e}") from e
    _require(isinstance(data, dict), "トップレベルはオブジェクトである必要があります")

    set_name = data.get("set", "BTX")
    _require(set_name in VALID_SETS, f"未知のセット: {set_name}")

    phase = data.get("phase", "action_resolution")
    _require(phase in VALID_PHASES, f"未知のphase: {phase}")
    # 行動解決は盤面幾何（移動）を使うのでエリア必須。脚本家能力フェイズは
    # 質問にエリアが無いことが多いので、空/未指定を許容する。
    area_required = (phase == "action_resolution")

    board = Board()

    for cd in data.get("characters", []):
        _require("name" in cd, "characters は name が必須")
        area = cd.get("area", "")
        if area_required:
            _require(area in AREAS, f"未知のエリア: {area}")
        elif area:  # MM系：空は許容、書くなら正しいエリア
            _require(area in AREAS, f"未知のエリア: {area}")
        forbidden = cd.get("forbidden")
        # 空配列や未指定は「不明」とみなしキャラ既定値で補完する
        # （LLMが forbidden:[] を出して 異世界人の禁止エリア病院を消す事故を防ぐ）。
        if not forbidden:
            forbidden = forbidden_of(cd["name"])  # data.py から補完
        else:
            for f in forbidden:
                _require(f in AREAS, f"未知の禁止エリア: {f}")
            forbidden = frozenset(forbidden)
        board.add_character(Character(
            name=cd["name"],
            role=cd.get("role", "パーソン"),
            area=area,
            forbidden=forbidden,
            alive=cd.get("alive", True),
            goodwill=int(cd.get("goodwill", 0)),
            unrest=int(cd.get("unrest", 0)),
            anyaku=int(cd.get("anyaku", 0)),
            guard=int(cd.get("guard", 0)),
        ))

    for pd in data.get("placements", []):
        for k in ("owner", "card", "target", "target_kind"):
            _require(k in pd, f"placements は {k} が必須")
        kind = pd["target_kind"]
        _require(kind in VALID_KINDS, f"未知の target_kind: {kind}")
        owner, card = pd["owner"], pd["card"]
        # カード語彙チェック（持ち主側の手札に存在するか）
        if owner == "mastermind":
            _require(card in MASTERMIND_CARDS, f"脚本家手札に無いカード: {card}")
        else:
            _require(card in PROTAGONIST_CARDS, f"主人公手札に無いカード: {card}")
        if kind == "board":
            _require(pd["target"] in AREAS, f"未知のボード: {pd['target']}")
        else:
            _require(
                pd["target"] in board.characters,
                f"未知のキャラ（characters に未定義）: {pd['target']}",
            )
        board.add_placement(Placement(owner, card, pd["target"], kind))

    # ボード暗躍（ファクターの『学校に暗躍2以上』判定等に使用）。任意。
    board_anyaku: dict[str, int] = {}
    for area_name, n in (data.get("board_anyaku") or {}).items():
        _require(area_name in AREAS, f"未知のボード(board_anyaku): {area_name}")
        board_anyaku[area_name] = int(n)

    # 使用中ルール（"不穏な噂" 等。暗躍/敗北条件の判定に使用）。任意・文字列リスト。
    rules = data.get("rules", [])
    _require(isinstance(rules, list), "rules はリストである必要があります")
    rules = [str(r) for r in rules]

    # 事件フェイズ：犯人指定（ネタバレ回避のためユーザー入力）。
    incident = data.get("incident") or {}
    _require(isinstance(incident, dict), "incident はオブジェクトである必要があります")
    # 事件フェイズ（単独）は犯人必須。連結裁定は犯人が指定されていれば事件も裁定する（任意）。
    if phase == "incident" or (phase == "connected" and incident.get("culprit")):
        if phase == "incident":
            _require("culprit" in incident and incident["culprit"],
                     "incident.culprit（犯人キャラ名）が必須です")
        _require(incident["culprit"] in board.characters,
                 f"未知の犯人キャラ（characters に未定義）: {incident['culprit']}")
        # ★犯人の不安が明示されたか記録（未指定→0扱いで「発生しない」と断定する事故を防ぐ）。
        #   ただし連結裁定では行動解決後の不安を使うので省略検知は無効化（明示扱い）。
        _culprit_cd = next((cd for cd in data.get("characters", [])
                            if cd.get("name") == incident["culprit"]), {})
        incident["unrest_specified"] = (phase == "connected") or ("unrest" in _culprit_cd)

    # 主人公能力フェイズ：友好能力の使用可否。
    goodwill_ability = data.get("goodwill_ability") or {}
    _require(isinstance(goodwill_ability, dict), "goodwill_ability はオブジェクトである必要があります")
    if phase == "goodwill_ability":
        _require("character" in goodwill_ability and goodwill_ability["character"],
                 "goodwill_ability.character（行使キャラ名）が必須です")
        _require(goodwill_ability["character"] in board.characters,
                 f"未知の行使キャラ（characters に未定義）: {goodwill_ability['character']}")

    # ループ終了フェイズ：タイミング裁定（キャラ・盤面は不要）。
    loop_end = data.get("loop_end") or {}
    _require(isinstance(loop_end, dict), "loop_end はオブジェクトである必要があります")

    # ターン終了フェイズ：死亡判定（キャラは盤面から読む）。
    turn_end = data.get("turn_end") or {}
    _require(isinstance(turn_end, dict), "turn_end はオブジェクトである必要があります")

    qt = data.get("question_target")
    _require(isinstance(qt, dict) and "name" in qt and "kind" in qt,
             "question_target は {name, kind} が必須")
    _require(qt["kind"] in VALID_QT_KINDS, f"未知の question_target.kind: {qt['kind']}")

    return Question(
        board=board,
        target=qt["name"],
        target_kind=qt["kind"],
        set_name=set_name,
        assumptions=data.get("assumptions", []),
        phase=phase,
        board_anyaku=board_anyaku,
        rules=rules,
        incident=incident,
        goodwill_ability=goodwill_ability,
        loop_end=loop_end,
        turn_end=turn_end,
    )
