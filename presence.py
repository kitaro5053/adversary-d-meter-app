"""presence.py — アプリの「今の同時接続数」を外部ストレージなしで数える。

なぜ外部ストレージ（Google Sheets 等）が不要か:
  Streamlit Community Cloud はアプリを単一コンテナ＝単一プロセスで動かすため、
  全ユーザーのセッションが同じ Python プロセスのメモリを共有する。よって
  st.cache_resource でプロセス内に共有レジストリを1つ持ち、各セッションが自分の
  UUID と最終ハートビート時刻を書き込めば、「直近 window 秒以内に更新のある
  セッション数」＝実質的な同時接続数になる。外部API・認証・依存追加・レート制限は不要。

  再起動でレジストリは消えるが、「今この瞬間の接続数」には無害
  （各セッションは次のハートビートで再登録される。再起動判断はする前に見る数字）。

使い方（app.py）:
  import presence
  presence.render_heartbeat()   # 全ユーザー・全モードで1回。画面には何も出さず生存を刻む。
  presence.render_count()       # 接続数を表示（開発者モードのときだけ呼ぶ想定）。
"""

from __future__ import annotations

import threading
import time
import uuid

import streamlit as st

_WINDOW_SEC = 300      # この秒数（=5分）以内に更新のあるセッションを「接続中」とみなす
_INTERVAL = "60s"      # ハートビート/表示の自動更新間隔（_WINDOW_SEC より十分短く。
                       # 背景タブはブラウザがタイマーを間引くため、窓に対し数倍の余裕を取る）
_SID_KEY = "_presence_sid"


@st.cache_resource
def _registry() -> dict:
    """全セッションで共有される {session_id: last_seen_epoch} ＋ Lock。
    st.cache_resource はプロセス内シングルトン＝これが「共有」の肝。"""
    return {"seen": {}, "lock": threading.Lock()}


def _session_id() -> str:
    sid = st.session_state.get(_SID_KEY)
    if not sid:
        sid = uuid.uuid4().hex
        st.session_state[_SID_KEY] = sid
    return sid


def beat() -> None:
    """このセッションの生存を記録する（毎回呼んでよい・軽量・スレッド安全）。"""
    reg = _registry()
    with reg["lock"]:
        reg["seen"][_session_id()] = time.time()


def active_count(window_sec: int = _WINDOW_SEC) -> int:
    """直近 window_sec 秒以内に更新のあるセッション数。古い記録は掃除する。"""
    reg = _registry()
    now = time.time()
    with reg["lock"]:
        seen = reg["seen"]
        for sid in [s for s, t in seen.items() if now - t > window_sec]:
            del seen[sid]
        return len(seen)


# --- 自動更新フラグメント（Streamlit 1.37+ の st.fragment(run_every=)）---------
# run_every で「フラグメントだけ」を定期再実行し、idle のユーザーも刻み続ける。
# 古い Streamlit（run_every 非対応）では普通の関数にフォールバック＝
# 自然な再実行のたびに1回刻む（idle の取りこぼしはあるが破綻しない）。
try:
    @st.fragment(run_every=_INTERVAL)
    def _hb_fragment() -> None:
        beat()

    @st.fragment(run_every=_INTERVAL)
    def _count_fragment() -> None:
        beat()
        st.metric("🟢 現在の同時接続", f"{active_count()} 人")
        st.caption(f"直近{_WINDOW_SEC // 60}分に通信のあったセッション数。約{_INTERVAL}ごとに自動更新。")
except Exception:  # noqa: BLE001  # st.fragment 非対応 or run_every 未サポート
    def _hb_fragment() -> None:
        beat()

    def _count_fragment() -> None:
        beat()
        st.metric("🟢 現在の同時接続", f"{active_count()} 人")
        st.caption(f"直近{_WINDOW_SEC // 60}分に通信のあったセッション数。")


def render_heartbeat() -> None:
    """全ユーザー・全モードで呼ぶ。画面には何も出さず、定期的に生存を刻む。"""
    _hb_fragment()


def render_count() -> None:
    """同時接続数を表示（自動更新つき）。開発者モードのときだけ呼ぶ想定。"""
    _count_fragment()
