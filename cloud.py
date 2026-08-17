"""cloud.py — Supabase(REST/PostgREST)経由の永続機能。AIC infra（presence.py と同じ安全設計）。

★未設定（Secrets/環境変数に SUPABASE_URL / SUPABASE_ANON_KEY が無い）なら全て no-op。
  失敗は握りつぶし、UIを絶対に壊さない（telemetry/quota の障害でアプリが落ちない）。

Streamlit Secrets（アプリごと）:
    SUPABASE_URL      = "https://xxxx.supabase.co"
    SUPABASE_ANON_KEY = "eyJ...(anon 公開キー・RLSで挿入/読取のみ)"
    APP_CHANNEL       = "stable" | "nightly"

対応テーブル/関数（SQLは docs/引き継ぎ_Supabase機能ABCD.md）:
  A コスト保護 : rpc bump_usage(p_session,p_limit) → check_quota()
  B フィードバック集約 : table feedback → log_feedback()
  (共通) 匿名イベント : table events → log_event()
  C クラウド棋譜 : table games → save_game()/load_game()
  D 告知/設定 : table app_config / announcements → get_config()/get_announcement()
"""

from __future__ import annotations

import os
import threading
import uuid

try:
    import httpx  # anthropic 経由で既に依存にある（新規依存ゼロ）
except Exception:  # noqa: BLE001
    httpx = None  # type: ignore

_TIMEOUT = 3.0
_SID_KEY = "_anon_sid"


def _secret(name: str) -> str:
    """st.secrets → 環境変数の順で読む（どちらも無ければ空文字）。"""
    try:
        import streamlit as st
        if name in st.secrets:            # type: ignore[operator]
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get(name, "")


def _conf() -> tuple[str, str, str]:
    return (_secret("SUPABASE_URL").rstrip("/"),
            _secret("SUPABASE_ANON_KEY"),
            _secret("APP_CHANNEL") or "unknown")


def enabled() -> bool:
    url, key, _ = _conf()
    return bool(httpx and url and key)


def channel() -> str:
    return _conf()[2]


def session_id() -> str:
    """匿名セッションID（個人特定なし）。presence の id が在れば再利用して1本化。"""
    try:
        import streamlit as st
        sid = st.session_state.get(_SID_KEY) or st.session_state.get("_presence_sid")
        if not sid:
            sid = uuid.uuid4().hex
            st.session_state[_SID_KEY] = sid
        return sid
    except Exception:  # noqa: BLE001
        return "no-session"


def _headers(key: str, prefer: str | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def _post(table: str, row: dict, prefer: str = "return=minimal") -> httpx.Response | None:
    url, key, _ = _conf()
    if not enabled():
        return None
    try:
        return httpx.post(f"{url}/rest/v1/{table}", headers=_headers(key, prefer),
                          json=row, timeout=_TIMEOUT)
    except Exception:  # noqa: BLE001
        return None


def _get(table: str, params: dict, key: str | None = None) -> list:
    url, anon, _ = _conf()
    if not enabled():
        return []
    try:
        r = httpx.get(f"{url}/rest/v1/{table}", headers=_headers(key or anon),
                      params=params, timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else []
    except Exception:  # noqa: BLE001
        return []


def _patch(table: str, params: dict, row: dict) -> httpx.Response | None:
    """既存行の更新（PostgREST PATCH）。params で対象行を絞る（例 {"token": "eq.xxx"}）。"""
    url, key, _ = _conf()
    if not enabled():
        return None
    try:
        return httpx.patch(f"{url}/rest/v1/{table}", headers=_headers(key),
                           params=params, json=row, timeout=_TIMEOUT)
    except Exception:  # noqa: BLE001
        return None


def _rpc(fn: str, args: dict) -> list | dict | None:
    url, key, _ = _conf()
    if not enabled():
        return None
    try:
        r = httpx.post(f"{url}/rest/v1/rpc/{fn}", headers=_headers(key),
                       json=args, timeout=_TIMEOUT)
        return r.json() if r.status_code < 300 else None
    except Exception:  # noqa: BLE001
        return None


# --- 管理者読取（dev_mode 限定）---------------------------------------------
#   RLS：feedback/events は anon SELECT 不可（insert専用ポリシー）＝読むには service-role
#   キーが要る。games は anon read ポリシーがあるので anon でも読める。
def _admin_key() -> str | None:
    """管理者読取用の service-role キー（Secretsに SUPABASE_SERVICE_KEY があれば）。無ければ None。
    ★service キーは RLS を迂回する強力なキー＝**dev/管理用の Secrets にのみ置く**
    （Streamlit の Secrets はサーバ側でクライアントには出ない）。無ければ feedback/events は
    読めず games のみ（anon read）になる＝graceful degrade。"""
    return _secret("SUPABASE_SERVICE_KEY") or None


def admin_list(table: str, *, limit: int = 200, order: str = "created_at.desc",
               select: str = "*") -> list:
    """【管理者用・dev_mode限定】テーブルの行を新しい順に読む。service キーがあればそれで
    （feedback/events も可）、無ければ anon キー（games のみ・RLS読取ポリシー依存）。

    ★戻り値には配役/犯人（games の payload）や自由記述コメント（feedback の payload）が
    含まれうる＝**表示側で個人情報・ネタバレのマスキングを検討すること**（本関数は生データを返す）。
    """
    if not enabled():
        return []
    return _get(table, {"select": select, "order": order, "limit": str(limit)},
                key=_admin_key())


# --- (共通) 匿名イベント / B フィードバック -------------------------------------
def log_event(event: str, **props) -> None:
    """機能利用の匿名イベント。fire-and-forget（別スレッド）＝UIを待たせない。
    ★内容（質問文・配役・犯人）は渡さないこと。event名とメタ（mode/result等）だけ。"""
    if not enabled():
        return
    _, _, ch = _conf()
    row = {"session_id": session_id(), "event": event,
           "props": props or None, "app_version": props.pop("app_version", None),
           "channel": ch}
    threading.Thread(target=_post, args=("events", row), daemon=True).start()


def log_feedback(entry: dict) -> None:
    """👍/👎/🐛 のフィードバックを feedback テーブルへ（B：scenarios半自動化の素材）。
    entry は qa_log の1件（translation/verdict/feedback 等）。内容はルール質問の構造化データのみ。"""
    if not enabled():
        return
    _, _, ch = _conf()
    row = {"session_id": session_id(), "channel": ch, "payload": entry}
    threading.Thread(target=_post, args=("feedback", row), daemon=True).start()


# --- A コスト保護（レート制限/日次クォータ）------------------------------------
def check_quota(limit: int) -> tuple[bool, int]:
    """このセッションの本日利用数を+1し、(許可bool, 使用数) を返す。
    SQL側の SECURITY DEFINER 関数 bump_usage で原子的にインクリメント（RLS安全）。
    ★未設定/障害時は fail-open（True）＝可用性優先（コスト保護は best-effort）。"""
    res = _rpc("bump_usage", {"p_session": session_id(), "p_limit": limit})
    if not res:
        return True, 0
    row = res[0] if isinstance(res, list) and res else res
    try:
        return bool(row.get("allowed", True)), int(row.get("used", 0))
    except Exception:  # noqa: BLE001
        return True, 0


# --- C クラウド棋譜セーブ／共有コード -------------------------------------------
def save_game(payload: dict) -> str | None:
    """棋譜(payload＝確定脚本＋公開履歴)を games へ保存し、短い共有コードを返す。"""
    if not enabled():
        return None
    _, _, ch = _conf()
    code = uuid.uuid4().hex[:10]
    r = _post("games", {"code": code, "payload": payload, "channel": ch})
    return code if (r is not None and r.status_code < 300) else None


def load_game(code: str) -> dict | None:
    """共有コードから棋譜を取得（無ければ None）。"""
    rows = _get("games", {"code": f"eq.{code}", "select": "payload", "limit": "1"})
    return rows[0]["payload"] if rows else None


# --- C2 対局スナップショット保存（続きから遊ぶ・Supabase Phase 1・2026-07-17）--------
#   games（共有コードの棋譜＝完走した対局の配布用）とは別テーブル game_saves を使う＝用途が違う
#   （こちらは「このセッションの続き」＝上書き前提・TTLあり）。SQL/RLSは提案書§9（人間側で実行）。
#   ★保存されるのは神視点フル（伏せ札の中身・配役）＝裁定済み（提案書§7 論点1）。トークンを
#   知る人は誰でも読める＝共有UIには「神視点データを含む」注意書きを必ず出すこと。
def save_snapshot(payload: dict, *, slot: str = "auto", token: str | None = None) -> str | None:
    """対局スナップショットを game_saves へ保存し、復帰トークンを返す（失敗/未設定は None）。

    token を渡すとその行を更新（＝オートセーブの上書き。Phase 2 で使う）。無ければ新規発行。
    ★TTL＝保存のたびに古い auto スロットを掃除する RPC を best-effort で叩く（裁定＝論点5・
      pg_cron 非依存の lazy 掃除）。失敗しても保存自体は妨げない。
    """
    if not enabled():
        return None
    _, _, ch = _conf()
    row = {"session_id": session_id(), "slot": slot, "snapshot": payload, "channel": ch}
    if token:
        r = _patch("game_saves", {"token": f"eq.{token}"}, row)
        ok = r is not None and r.status_code < 300
        return token if ok else None
    tok = uuid.uuid4().hex[:12]
    row["token"] = tok
    r = _post("game_saves", row)
    if r is not None and r.status_code == 409:
        # ★U-13（2026-08-08）：(session_id, slot) の一意制約と衝突（提案書§9のSQL＝
        #   1セッション×1スロットは1行）。呼び出し側がトークンを失っていても2度目の保存を
        #   救う＝既存行のトークンを (session_id, slot) で引いて PATCH（上書き）に回す。
        #   token 列は書き換えない＝発行済みトークンを無効化しない（返り値も既存トークン）。
        rows = _get("game_saves", {"session_id": f"eq.{row['session_id']}",
                                   "slot": f"eq.{slot}", "select": "token", "limit": "1"})
        old = str(rows[0].get("token") or "") if rows and isinstance(rows[0], dict) else ""
        if not old:
            return None
        row.pop("token", None)
        r2 = _patch("game_saves", {"token": f"eq.{old}"}, row)
        return old if (r2 is not None and r2.status_code < 300) else None
    if r is None or r.status_code >= 300:
        return None
    threading.Thread(target=_rpc, args=("purge_old_saves", {}), daemon=True).start()
    return tok


def load_snapshot(token: str) -> dict | None:
    """復帰トークンから対局スナップショットを取得（無ければ None）。"""
    rows = _get("game_saves", {"token": f"eq.{token}", "select": "snapshot", "limit": "1"})
    return rows[0]["snapshot"] if rows else None


def new_save_token() -> str:
    """復帰トークンを発行する（12桁の乱数）。★同期＝URL(?s=)に即載せられる＝オートセーブは
    このトークンで upsert する（Phase 2）。推測困難＝token-as-capability（提案書§4(i)）。"""
    return uuid.uuid4().hex[:12]


def upsert_snapshot(payload: dict, token: str, *, slot: str = "auto") -> bool:
    """token 主キーで upsert（在れば更新・無ければ挿入）。オートセーブ用（Phase 2）。

    PostgREST の `Prefer: resolution=merge-duplicates`＝主キー衝突で更新（＝先にトークンを
    発行してURLへ載せ、実際の行はこの upsert が作る／上書きする）。
    """
    if not enabled():
        return False
    _, _, ch = _conf()
    row = {"token": token, "session_id": session_id(), "slot": slot,
           "snapshot": payload, "channel": ch}
    r = _post("game_saves", row, prefer="resolution=merge-duplicates")
    return r is not None and r.status_code < 300


def autosave_snapshot(payload: dict, token: str, *, limit: int = 0) -> None:
    """★オートセーブ（案A・fire-and-forget）：**UIを待たせない**（別スレッド・戻り値なし）。

    - **書込コスト保護**（提案書§4・条件3）：スレッド内で先に check_quota（bump_usage RPC）を
      叩き、上限超過なら**書かない**。未設定/障害時は fail-open＝可用性優先（既存 A 機構と同型）。
      ★quota も upsert もスレッド内＝rerun は一切ブロックしない（条件2）。
    - 失敗は握りつぶす（オートセーブの失敗でアプリを壊さない）。確実に残したいユーザーには
      手動保存（save_snapshot）と棋譜ダウンロードが別に在る。
    - **呼ぶ側が差分（snapshot hash）で dedupe すること**＝同一内容の再送はここでは防がない
      （Streamlit は毎操作で再実行される＝dedupe しないと同じ内容を毎rerun書いてしまう）。
    """
    if not enabled():
        return

    def _work() -> None:
        try:
            if limit > 0:
                ok, _used = check_quota(limit)
                if not ok:
                    return          # 上限超過＝今日はもう書かない（手動保存は別途できる）
            upsert_snapshot(payload, token)
            _rpc("purge_old_saves", {})     # TTL＝古い auto を掃除（裁定＝論点5・best-effort）
        except Exception:  # noqa: BLE001  オートセーブの失敗は無害＝握りつぶす
            pass

    threading.Thread(target=_work, daemon=True).start()


# --- D 告知バナー／フィーチャーフラグ -------------------------------------------
def get_config() -> dict:
    """app_config（key,value）を1辞書で返す。再デプロイ無しの設定切替に使う。"""
    rows = _get("app_config", {"select": "key,value"})
    return {r["key"]: r["value"] for r in rows if "key" in r}


def ping() -> dict:
    """疎通診断（同期）。events へ1件テスト挿入し、結果を返す。?cloudtest=1 用。"""
    url, key, ch = _conf()
    info: dict = {"httpx導入": bool(httpx), "URL設定": bool(url),
                  "KEY設定": bool(key), "channel": ch, "enabled": enabled(),
                  "URL先頭": (url[:30] + "…") if url else ""}
    if not enabled():
        info["結果"] = "無効（SecretsのURL/KEY未設定 or httpx無し）"
        return info
    try:
        r = httpx.post(f"{url}/rest/v1/events",
                       headers=_headers(key, "return=minimal"),
                       json={"session_id": session_id(), "event": "ping",
                             "props": {"diag": True}, "channel": ch},
                       timeout=_TIMEOUT)
        info["HTTP"] = r.status_code
        info["結果"] = "OK（挿入成功）" if r.status_code < 300 else "NG"
        if r.status_code >= 300:
            info["エラー本文"] = r.text[:300]
    except Exception as e:  # noqa: BLE001
        info["結果"] = "例外"
        info["エラー本文"] = repr(e)[:300]
    return info


def get_announcement() -> dict | None:
    """現チャンネル向けの有効な告知を1件返す（無ければ None）。
    行: {active,channel,level,message}。channel は自チャンネル or 'all' を対象。"""
    _, _, ch = _conf()
    rows = _get("announcements", {
        "select": "level,message,channel,active",
        "active": "eq.true",
        "channel": f"in.(all,{ch})",
        "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None
