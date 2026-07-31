"""対戦・デバッグ基盤（AIプレイヤー計画 M2）。

公開API:
    from arena import save_game, load_game, replay_game, verify_replay, run_batch
"""

from .gamelog import (
    LOG_DOWNLOAD_MIME,
    game_to_jsonl,
    load_game,
    load_game_lines,
    log_file_name,
    log_position_key,
    save_game,
    script_fingerprint,
    script_from_dict,
    script_to_dict,
    stable_saved_at,
    utc_saved_at,
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
    "log_file_name",
    "utc_saved_at",
    # ★U-8：棋譜ダウンロードのURL失効対策（saved_at固定・mimeの単一ソース）
    "LOG_DOWNLOAD_MIME",
    "log_position_key",
    "script_fingerprint",
    "stable_saved_at",
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
