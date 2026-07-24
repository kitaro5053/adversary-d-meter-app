# -*- coding: utf-8 -*-
"""プレイ2モード共通の「終了画面」部品（主人公プレイ/脚本家プレイで統一感を持たせる）。

両モードとも 4ループ打ち切り（ユーザー/AIC 方針 2026-07-11）で決着し、終了後に
「勝敗バナー ＋ 続行/ホーム等の選択」を同じ体裁で出す。ここは Streamlit 依存の薄い
描画ヘルパーのみ（勝敗ロジックや session_state キーは各モードが持つ）。
"""
from __future__ import annotations

_HOME_MODE = "🏠 ホーム"   # app.py の radio(key="app_mode") のホーム値と一致させること


def go_home(st) -> None:
    """『ホームに戻る』：app.py のモード選択をホームへ戻す（on_click コールバック用）。"""
    st.session_state["app_mode"] = _HOME_MODE


def render_outcome_banner(st, *, won: bool, title: str, detail: str = "") -> None:
    """勝敗の統一バナー。won＝『そのプレイヤー（人間）が勝ったか』。緑=勝ち／赤=負け。

    両モードで同じ体裁（大きめの勝敗見出し＋一言）にする。st.success/st.error は
    ライト/ダーク両テーマに追従する。
    """
    (st.success if won else st.error)(title)
    if detail:
        st.caption(detail)
