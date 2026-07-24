"""対戦・デバッグ基盤（AIプレイヤー計画 M2）。

公開API:
    from arena import save_game, load_game, replay_game, verify_replay, run_batch
"""

from .gamelog import (
    game_to_jsonl,
    load_game,
    load_game_lines,
    save_game,
    script_from_dict,
    script_to_dict,
)
from .insight import build_insight, loop_summaries
from .interactive import PendingHuman, play_interactive
from .replay import (
    ReplayAgent,
    board_json_for_decision,
    board_json_from_snapshot,
    board_json_from_view,
    describe_choice,
    describe_event,
    replay_game,
    verify_replay,
)
from .runner import run_batch

__all__ = [
    "save_game",
    "load_game",
    "load_game_lines",
    "script_to_dict",
    "script_from_dict",
    "ReplayAgent",
    "replay_game",
    "verify_replay",
    "board_json_for_decision",
    "board_json_from_snapshot",
    "board_json_from_view",
    "describe_choice",
    "describe_event",
    "build_insight",
    "loop_summaries",
    "run_batch",
    "PendingHuman",
    "play_interactive",
]
