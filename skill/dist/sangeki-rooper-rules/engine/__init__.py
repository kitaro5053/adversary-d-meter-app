"""惨劇RoopeR 行動解決リゾルバ（First Steps + Basic Tragedy X）。

公開API:
    from engine import Board, Character, Placement
    from engine import resolve_action_phase, validate_board, sensitivity_check
"""

from .goodwill import GoodwillAbilityResult, resolve_goodwill_ability
from .incident import IncidentResult, resolve_incident
from .loop_end import LoopEndResult, resolve_loop_end
from .models import Board, Character, Placement
from .orchestrate import TRANSLATION_PROMPT, Outcome, adjudicate
from .resolver import (
    Adjudication,
    CounterResult,
    SensitivityFlag,
    TargetResult,
    Violation,
    resolve_action_phase,
    sensitivity_check,
    validate_board,
)
from .translate import Question, TranslationError, load_question

__all__ = [
    "Board",
    "Character",
    "Placement",
    "Adjudication",
    "TargetResult",
    "CounterResult",
    "Violation",
    "SensitivityFlag",
    "resolve_action_phase",
    "validate_board",
    "sensitivity_check",
    # 翻訳層・オーケストレーション
    "Question",
    "TranslationError",
    "load_question",
    "Outcome",
    "adjudicate",
    "TRANSLATION_PROMPT",
    # 事件フェイズ（発生判定）
    "IncidentResult",
    "resolve_incident",
    # 主人公能力フェイズ（友好能力の使用可否）
    "GoodwillAbilityResult",
    "resolve_goodwill_ability",
    # ループ終了フェイズ（タイミング裁定）
    "LoopEndResult",
    "resolve_loop_end",
]
