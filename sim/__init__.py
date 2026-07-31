"""FSフルシミュレータ（AIプレイヤー計画 M1）。

公開API:
    from sim import Script, Incident, GameState, new_game, validate_script
    from sim import protagonist_view, mastermind_view
"""

from .state import (
    FS_INCIDENTS,
    FS_RULE_X_ROLES,
    FS_RULE_Y_ROLES,
    MASTERMIND_HAND,
    PHASES,
    PROTAGONIST_HAND,
    PROTAGONIST_SEATS,
    CharState,
    GameState,
    Incident,
    Script,
    new_game,
    validate_script,
)
from .flow import run_day, run_game
from .generator import (
    DAY_OPTIONS,
    DEFAULT_DAYS,
    STANDARD_CAST_POOL,
    incident_count_range,
    random_script,
    recommended_loops,
)
from .views import mastermind_view, protagonist_view

__all__ = [
    "FS_INCIDENTS",
    "FS_RULE_X_ROLES",
    "FS_RULE_Y_ROLES",
    "MASTERMIND_HAND",
    "PHASES",
    "PROTAGONIST_HAND",
    "PROTAGONIST_SEATS",
    "CharState",
    "GameState",
    "Incident",
    "Script",
    "new_game",
    "validate_script",
    "mastermind_view",
    "protagonist_view",
    "run_day",
    "run_game",
    "random_script",
    "incident_count_range",
    "recommended_loops",
    "STANDARD_CAST_POOL",
    "DAY_OPTIONS",
    "DEFAULT_DAYS",
]
