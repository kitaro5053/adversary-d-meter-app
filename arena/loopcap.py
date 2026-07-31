# -*- coding: utf-8 -*-
"""総ループ数（4ループ打ち切り＋延長）の**単一ソース**。U-10（2026-07-30）。

両モードとも「4ループで打ち切って勝敗を出し、選択でループを延長できる」
（ユーザー/AIC 方針 2026-07-11）。その総ループ数は
    総ループ数 = BASE_LOOPS + 延長回数（session_state の *_extra_loops）
だが、これが **3か所で別々に計算されていた**のが U-10 の温床だった：

1. 実際にゲームを走らせる上限（`replace(script, loops=...)`）
2. ヘッダの分数表示の分母
3. 終局バナーの説明文（「Nループ以内に…」）

★U-10 の本丸＝**復元経路（クラウド続き/棋譜）では 1 が効かない**：
  `play_interactive(initial_state=...)` は `state = initial_state` ＝
  **復元した局面が持つ `state.script`（＝snapshot に入っていた当時の loops）**で
  走るため、呼び出し側が `replace(script, loops=総ループ数)` しても無視される。
  ＝「もう少し遊んでやる」を押しても総ループ数が増えず、押した分だけ
  `*_extra_loops` と説明文の数字だけが伸びていた（ユーザー報告：ループ4/4 なのに
  「26ループ以内に…」）。`apply_loop_cap()` がその一点を塞ぐ。

この module は Streamlit 非依存（両UIから import して使う）。
"""

from __future__ import annotations

from dataclasses import replace

#: 4ループ打ち切りの基準ループ数（延長していない対局の総ループ数）。
BASE_LOOPS = 4


def total_loops(extra) -> int:
    """総ループ数＝BASE_LOOPS＋延長回数（負値・None は 0 扱い）。表示も実行もこれ1本で出す。"""
    try:
        _e = int(extra or 0)
    except (TypeError, ValueError):
        _e = 0
    return BASE_LOOPS + max(0, _e)


def restored_extra_loops(state) -> int:
    """復元した局面（snapshot 由来の GameState）から**延長回数**を復元する。

    材料は2つ。どちらか大きい方を採る（＝復元後に総ループ数が現在ループを下回らない）：
      - `state.script.loops`＝保存時に実際に効いていた総ループ数（延長分を含む・厳密）
      - `state.loop_no`     ＝いま何ループ目か（A-31 の保険。script.loops が無い/壊れた時用）
    """
    _sc = getattr(state, "script", None)
    try:
        _loops = int(getattr(_sc, "loops", 0) or 0)
    except (TypeError, ValueError):
        _loops = 0
    try:
        _loop_no = int(getattr(state, "loop_no", 1) or 1)
    except (TypeError, ValueError):
        _loop_no = 1
    return max(0, _loops - BASE_LOOPS, _loop_no - BASE_LOOPS)


def apply_loop_cap(state, loops: int):
    """復元局面の `state.script.loops` を「いま有効な総ループ数」に合わせる（U-10 の修正点）。

    `play_interactive(initial_state=state)` は state の script で走る＝ここを合わせないと
    UI が計算した総ループ数（延長を含む）が**まるごと無視される**。state が None／script を
    持たない場合は何もしない（新規開始の経路は素通り＝挙動不変）。
    """
    if state is None:
        return state
    _sc = getattr(state, "script", None)
    if _sc is None:
        return state
    if int(getattr(_sc, "loops", 0) or 0) != int(loops):
        state.script = replace(_sc, loops=int(loops))
    return state
