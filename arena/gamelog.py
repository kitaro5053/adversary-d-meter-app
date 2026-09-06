"""決定ログの保存・読込（JSONL。AIプレイヤー計画 §3.1）。

1ゲーム＝1ファイル：
- 1行目 meta：脚本・勝敗・最終状態（★神視点＝配役・犯人を含むネタバレデータ。
  テスターに渡さない／skill・デプロイに同梱しない／ローカルのデバッグ専用）。
- 2行目以降 decision：flow が記録した「actor に見えていた view・合法手・選択」1決定1行。

ファイル名は stdlib logging と衝突しないよう gamelog.py。
"""

from __future__ import annotations

import json
from pathlib import Path

# ★script_to_dict/script_from_dict の正典は sim/state（Script/Incident と同じ層）。
#   ここでは後方互換のため再エクスポートする（既存 importer: play.py 等）。
from sim.state import (  # noqa: F401  再エクスポート
    GameState, Incident, Script, script_from_dict, script_to_dict,
)

# ログ書式のバージョン（フィールド構成が変わったら上げる＝古いログの読込側の目印）。
LOG_FORMAT_VERSION = 1


import os as _os  # noqa: E402  ★B-272＝収録時の PYTHONHASHSEED を読む


def _probe_tool_build() -> str:
    """git からコミット短縮ハッシュを読む（取れなければ空）。★import 時に1回だけ呼ぶ。"""
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:  # noqa: BLE001  gitが無い/リポジトリでない等
        return ""


# ★U-14（2026-08-08）：ビルド識別は**モジュールロード時に1回だけ**評価して固定する。
#   保存のたびに git を読むと、アプリ起動後に git が進んだとき「ロードされていない
#   コードのSHA」が棋譜に記録される（B-191 Phase 0 で実害確定＝切り分けを誤らせた）。
_TOOL_BUILD = _probe_tool_build()


# ★B-272（2026-08-20・ユーザー裁定「272やっていいよ」）＝**収録時の `PYTHONHASHSEED` を残す**。
#   出典＝§72-82（B-266）＝`P(パーソン)` の2位/3位が **1 ULP** しか違わない席があり、その符号が
#   **hash 順（＝set/dict の反復順に依存する浮動小数の集計順）** で反転する。
#   ∴ ★**教材棋譜は `tool_build` だけでは再生できない＝収録時の hash seed も要る**。
#   ★**正直な限界**＝`PYTHONHASHSEED` が**未設定のとき、Python は実際の乱数化シードを公開しない**
#     （`sys.hash_info` にも無い）。∴ 記録できるのは「**設定されていたか／その値**」までで、
#     未設定なら `None`＝**再生側は seed 掃引が要る**（`arena/b266_audit.py hashseed`）。
#   ★import 時に1回だけ読む（`_TOOL_BUILD` と同じ理由＝保存のたびに読むと途中で変わりうる）。
_HASH_SEED = _os.environ.get("PYTHONHASHSEED")


def _hash_seed():
    """収録時の `PYTHONHASHSEED`（未設定なら None＝**再生には seed 掃引が要る**）。"""
    return _HASH_SEED


def _tool_build() -> str:
    """再現用のビルド識別＝**ロード時に固定した**コミット短縮ハッシュ（取れなければ空）。"""
    return _TOOL_BUILD


def utc_saved_at() -> str:
    """meta の `saved_at` 文字列（UTC・秒精度）を作る。

    ★U-4：ダウンロード名の日時と meta の日時を**同じ値**にするために関数化した
    （呼び出し側で1回作り、`game_to_jsonl` と `log_file_name` の両方へ渡す）。
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ascii_token(s) -> str:
    """ファイル名に使える ASCII だけ残す（英数字とハイフン）。非ASCIIは捨てる。"""
    return "".join(ch for ch in str(s) if ch.isascii() and (ch.isalnum() or ch == "-"))


def stamp_from_saved_at(saved_at: str | None) -> str:
    """`saved_at`（"%Y-%m-%d %H:%M:%S UTC"）→ ファイル名用の分単位スタンプ "20260727-1643"。

    未指定・解析不能なら現在時刻（UTC）で作る（欠落させるより一意性を優先）。
    """
    from datetime import datetime, timezone
    if saved_at:
        try:
            dt = datetime.strptime(str(saved_at).replace("UTC", "").strip(),
                                   "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y%m%d-%H%M")
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")


def log_file_name(prefix: str, script: Script | None = None, seed=None,
                  saved_at: str | None = None) -> str:
    """棋譜ダウンロードのファイル名を作る純関数（Streamlit非依存＝テスト可能。B-51原則）。

    例）`log_file_name("mmv", btx_3日脚本, 0, "2026-07-27 16:43:07 UTC")`
        → `mmv_20260727-1643_BTX3d_seed0.jsonl`

    ★U-4（2026-07-27）：従来は `mmv_{脚本名}_seed{seed}.jsonl` だったが、**日本語の脚本名は
      ブラウザ/OSでアンダースコアに潰れる**ため別の対局が同名ファイルになり、取り違えて
      アップロードする事故が実際に起きた。∴ **ASCIIだけで一意に見分けられる**名前にする＝
      日時（UTC・分単位）＋セット（FS/BTX）＋1ループの日数＋seed。日時は meta の `saved_at`
      と同じ値を渡して整合させる。
    ★読込側はファイル名を一切見ない（中身の meta 行で判定する）＝改名しても復元は壊れない。
    """
    parts = [_ascii_token(prefix) or "log", stamp_from_saved_at(saved_at)]
    if script is not None:
        set_name = _ascii_token(getattr(script, "set_name", "")) or "SET"
        try:
            days = int(getattr(script, "days_per_loop", 0) or 0)
        except (TypeError, ValueError):
            days = 0
        parts.append(f"{set_name}{days}d" if days > 0 else set_name)
    if seed is not None:
        try:
            parts.append(f"seed{int(seed)}")
        except (TypeError, ValueError):
            parts.append("seed" + (_ascii_token(seed) or "NA"))
    return "_".join(parts) + ".jsonl"


# ---------------------------------------------------------------------------
# ★U-8（2026-07-29・ユーザー実戦FB「ログを保存でhtmlが保存される／JSONが保存できない」）
#
# 機序（Streamlit 1.4x を実測して特定）：
#  (1) `st.download_button` は data を **メディアファイル**として登録し、フロントには
#      `/media/<file_id><拡張子>` のURLを渡す。file_id は **data＋mime＋file_name のハッシュ**。
#  (2) スクリプト実行が終わるたびに GC（clear_session_refs → remove_orphaned_files）が走り、
#      **前回のURLの実体は削除される**（DOWNLOADABLE は2パスで削除＝実測で「1回rerunすると
#      前のrunのURLは404」）。同じ file_id が毎runぶら下がっていれば消えない。
#  (3) 従来は `saved_at` を毎run `utc_saved_at()` で作っていた＝**秒ごとにdataが変わる**＝
#      毎run file_id が変わる＝直前に描画したURLが失効する。ダウンロードボタンのクリックは
#      既定で rerun も起こすので、クリックと失効が競合する。
#  (4) 失効すると 404（tornadoのHTMLエラーページ）が返る。フロントの `<a download="">` は
#      **降ってきた中身をそのまま保存**するので、HTMLがファイルとして落ちてくる。
#      さらに mime が `application/x-ndjson` だと `mimetypes.guess_extension` が None ＝
#      URLに拡張子が付かない（`/media/<hash>`）ため、ブラウザは Content-Type: text/html から
#      **拡張子 .html を付けて保存**する＝報告どおりの「htmlが保存されてしまう」。
#
# 対処＝(a) 局面が変わらない限り `saved_at` を固定して **dataとファイル名をrun間で不変**にする
#        （＝file_idが変わらない＝URLが失効しない）＝`stable_saved_at`。
#      (b) mime は拡張子に写るものを使う（`LOG_DOWNLOAD_MIME`）＝万一失敗しても .html にならない。
# ---------------------------------------------------------------------------

#: 棋譜ダウンロードに使う MIME。`application/x-ndjson` は拡張子に写らない（上記 (4)）ので使わない。
LOG_DOWNLOAD_MIME = "application/json"


def script_fingerprint(script: Script | None) -> str:
    """脚本の同一性を表す短いASCII（内容ハッシュ）。**別の対局を同一視しない**ために使う。"""
    import hashlib
    if script is None:
        return "none"
    try:
        s = json.dumps(script_to_dict(script), ensure_ascii=False, sort_keys=True)
    except Exception:  # noqa: BLE001  未知の形でも署名は返す（欠落させない）
        s = repr(script)
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def log_position_key(state: GameState, decisions: list[dict] | None) -> tuple:
    """棋譜ダウンロードの「局面の署名」。**同じ対局の同じ局面なら同じ値**を返す純関数。

    `stable_saved_at` のキーに使う。局面が進めば／別の対局に切り替われば値が変わる＝
    ファイル名の日時も更新される（U-4の「別の対局・別の局面が同名にならない」を壊さない）。
    ★脚本の指紋を混ぜる理由＝自作脚本は脚本名も seed も同じ（"自作脚本"/0）ため、これが無いと
      **別の自作脚本の開幕どうしが同じ署名**になり、前の対局の日時を使い回してしまう。
    """
    return (script_fingerprint(getattr(state, "script", None)),
            int(getattr(state, "loop_no", 0) or 0),
            int(getattr(state, "day", 0) or 0),
            len(decisions or []),
            len(getattr(state, "history", None) or []),
            str(getattr(state, "winner", "") or ""))


def stable_saved_at(store, slot: str, position, now: str | None = None) -> str:
    """`saved_at` を**局面ごとに1回だけ**決め、以後は同じ値を返す（Streamlit非依存の純関数）。

    store＝`st.session_state` のような dict ライク（get/[]= が使えれば何でもよい）。
    slot＝ボタン群の識別子（同じ画面の複数ボタンは同じ slot にする＝dataも名前も一致させる）。
    position＝`log_position_key()` の戻り値。

    これが無いと毎runで `saved_at` が変わり、ダウンロードURLが1回のrerunで失効する（上記機序 (3)）。
    """
    key = f"_dl_saved_at_{slot}"
    sig = repr(tuple(position))
    cur = store.get(key)
    if isinstance(cur, (tuple, list)) and len(cur) == 2 and cur[0] == sig:
        return str(cur[1])
    val = now or utc_saved_at()
    store[key] = (sig, val)
    return val


def game_to_jsonl(script: Script, state: GameState, decisions: list[dict],
                  app_version: str = "", resume_snapshot: dict | None = None,
                  saved_at: str | None = None) -> str:
    """1ゲームをJSONL文字列にする（保存・ダウンロード共通）。

    app_version＝呼び出し側（app.py/play.py）のアプリ版数（任意）。ログにビルド識別を
    残すと「どのバージョンで起きた不具合か」を後から特定できる（ユーザー要望 2026-07-06）。

    ★A-39（2026-07-19）：resume_snapshot＝**復元用の日境界スナップショット1つ**
    `{"loop": L, "day": D, "snapshot": GameState.to_snapshot()}`。📂ログ読込を replay（決定の
    再実行＝ドリフトする）から **snapshot 復元**へ移すために足す（保存方式の整理・snapshot化）。
    ★1つだけ保存する理由：to_snapshot は history/secret_log を内包＝日境界ごとに持つとログが
    二乗的に膨らむ。「続きから」に要るのは**最新の日境界＋当日分の決定**だけ（当日分は decisions
    から split_day_tail で取れる＝A-36/Phase1 と同じ構造）。過去日の閲覧は既存の `snapshots`
    （phase_snapshots＝表示用）が担う＝役割分担。
    ★省略可＝旧形式（resume_snapshot なし）のログは従来どおり replay で復元する（後方互換）。

    ★U-4：`saved_at` を外から渡せる（省略＝現在時刻）。ダウンロード名（`log_file_name`）と
    meta の日時を一致させるため、呼び出し側で `utc_saved_at()` を1回作って両方へ渡す。
    """
    meta = {
        "type": "meta",
        "log_format_version": LOG_FORMAT_VERSION,
        "app_version": app_version,
        "tool_build": _tool_build(),
        # ★B-272＝収録時の hash seed（未設定なら None）。§72-82／§72-87。
        "hash_seed": _hash_seed(),
        "saved_at": saved_at or utc_saved_at(),
        "script": script_to_dict(script),
        "winner": state.winner,
        "loops_played": state.loop_no,
        "n_decisions": len(decisions),
        "final_state": state.to_dict(),
        "history": list(state.history),
        "secret_log": list(state.secret_log),
        "snapshots": list(state.phase_snapshots),
    }
    if resume_snapshot:                      # ★A-39：snapshot復元用（無ければ旧形式＝replay）
        meta["resume_snapshot"] = resume_snapshot
    lines = [json.dumps(meta, ensure_ascii=False)]
    lines += [json.dumps({"type": "decision", **d}, ensure_ascii=False) for d in decisions]
    return "\n".join(lines) + "\n"


def save_game(path: str | Path, script: Script, state: GameState,
              decisions: list[dict]) -> Path:
    """1ゲームをJSONLで保存。meta行＋decision行。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(game_to_jsonl(script, state, decisions), encoding="utf-8")
    return path


# ---------- 後方互換：ローマ字修正（2026-07-12）以前のセーブ/棋譜の識別子を正規化 ----------
# 暗躍=anryaku→anyaku・ご神木=gonoki→goshinboku へリネームした（誤読の訂正）。旧セーブの
# choices・イベント名（"event":"anryaku"）・ボードキー（board_anryaku）・カウンター属性名
# （kind/gonoki の値）等に残る旧トークンを、読込時に現行へ揃える。★漢字名（暗躍・ご神木）は
# ASCIIトークンではないので置換されない（無変更）。純関数（dict/list/strを再帰的に写す）。
_LEGACY_TOKEN_MAP = (("anryaku", "anyaku"), ("gonoki", "goshinboku"))


def normalize_legacy_tokens(obj):
    """旧ローマ字トークン（anryaku/gonoki）を現行（anyaku/goshinboku）へ再帰置換して返す。"""
    if isinstance(obj, dict):
        return {normalize_legacy_tokens(k): normalize_legacy_tokens(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_legacy_tokens(v) for v in obj]
    if isinstance(obj, str):
        s = obj
        for old, new in _LEGACY_TOKEN_MAP:
            if old in s:
                s = s.replace(old, new)
        return s
    return obj


def load_game_lines(raw_lines) -> tuple[Script, dict, list[dict]]:
    """JSONL行の列から (脚本, meta, decisions) を組み立てる（アップロード対応）。
    ★旧トークン(anryaku/gonoki)を含む旧棋譜も normalize_legacy_tokens で現行へ揃えて読む。"""
    lines = [normalize_legacy_tokens(json.loads(line)) for line in raw_lines if str(line).strip()]
    if not lines or lines[0].get("type") != "meta":
        raise ValueError("先頭がmeta行でない（決定ログ形式でない）")
    meta = lines[0]
    decisions = [{k: v for k, v in d.items() if k != "type"}
                 for d in lines[1:] if d.get("type") == "decision"]
    return script_from_dict(meta["script"]), meta, decisions


def load_game(path: str | Path) -> tuple[Script, dict, list[dict]]:
    """(脚本, meta, decisions) を返す。"""
    with Path(path).open("r", encoding="utf-8") as f:
        return load_game_lines(f)


# ---------- 棋譜方式セーブからの再開（②-2・AIC/ユーザー方針 2026-07-11） ----------
# seed＋選択列を AI/生成器に通し直す旧方式と違い、確定脚本＋記録済み決定を再適用して
# 局面を復元する（AIの決定性に非依存）。過去は完全再現・以降の未来は現行AIが打つ。
def human_choices_from_decisions(decisions: list[dict],
                                 human_seats) -> list[dict]:
    """記録済み決定列から、人間席（human_seats）の選択だけを順に取り出す（棋譜からの再開用）。

    play_interactive はこの choices を順に消費して過去を再現し、選択が尽きたら以降は
    現行AIが打つ（＝過去は記録どおり・未来は現行挙動）。actor が human_seats に含まれる
    decision の chosen を出現順に並べる。
    """
    seats = set(human_seats)
    return [d["chosen"] for d in decisions if d.get("actor") in seats]


def ai_replay_from_decisions(decisions: list[dict], human_seats) -> dict[str, list[dict]]:
    """記録決定から、AI席（human_seats 以外）の選択を席ごとに順に取り出す（棋譜再開用）。

    play_interactive の ai_replay に渡すと、AI席も記録どおり再生して過去をAI非依存で再現する
    （現行AIのseed/実装に依らず過去が一致）。尽きたら現行AIが続きを打つ。
    """
    seats = set(human_seats)
    out: dict[str, list[dict]] = {}
    for d in decisions:
        a = d.get("actor")
        if a is not None and a not in seats:
            out.setdefault(a, []).append(d["chosen"])
    return out


# ---------- クラウド棋譜（Supabase games・②-2の共有スキーマ・両モード共通） ----------
# cloud.save_game/load_game は dict(jsonb) を出し入れする。棋譜(JSONL文字列)を1フィールドに
# 収めるだけ＝主人公/脚本家どちらのプレイでも同一スキーマで、既存の load_game_lines /
# UI側の _apply_mmv_save の解釈をそのまま無損失で再利用できる（cloud は詳細を知らない）。
CLOUD_PAYLOAD_FORMAT = "gamelog-jsonl-v1"


def game_to_cloud_payload(script: Script, state: GameState, decisions: list[dict],
                          app_version: str = "") -> dict:
    """クラウド保存用 dict（cloud.save_game に渡す）。両モード共通スキーマ。"""
    return {
        "format": CLOUD_PAYLOAD_FORMAT,
        "app_version": app_version,
        "jsonl": game_to_jsonl(script, state, decisions, app_version),
    }


# --- スナップショット保存（Supabase Phase 1・2026-07-17）---------------------------
#
# ★棋譜(jsonl)payload とは別形式＝役割が違う：
#   - CLOUD_PAYLOAD_FORMAT（棋譜）＝決定の列＝**replay で復元**する（＝ドリフトする。実測：
#     docs/feedback_logs の9件中5件が現行コードで再生不能）。共有・検死・持ち運び用に残す。
#   - SNAPSHOT_PAYLOAD_FORMAT（下記）＝**盤面そのもの**＝replay非依存で復元（Phase 0 の to_snapshot）。
#     「続きから遊ぶ」はこちらが正典。
SNAPSHOT_PAYLOAD_FORMAT = "gamestate-snapshot-v1"


def resume_from_meta(meta: dict, decisions: list[dict], human_seats):
    """★A-39：ログmeta の resume_snapshot から「続きから」の復元材料を作る。

    返り値 (state, human_choices, ai_replay, warn) ／ 復元不能なら None（＝呼び出し側は従来の
    replay へフォールバックし「ずれる場合あり」を表示する＝旧形式ログの互換）。
    - state＝日境界の GameState（from_snapshot＝replay非依存）。A-36 の壊れトークン救済も通す。
    - human_choices/ai_replay＝**その日の分だけ**（split_day_tail）＝replay が1日に縮む。
    - warn＝救済で修復した旨（無ければ空文字）。
    """
    rs = (meta or {}).get("resume_snapshot")
    if not isinstance(rs, dict) or not isinstance(rs.get("snapshot"), dict):
        return None
    try:
        state = GameState.from_snapshot(rs["snapshot"])
    except Exception:  # noqa: BLE001  未来版/壊れ＝フォールバック（捏造しない）
        return None
    warn = ""
    try:
        from arena.play import _normalize_restored_state   # A-36 資産（壊れトークン救済）
        warn = _normalize_restored_state(state)
    except Exception:  # noqa: BLE001  救済が使えなくても復元自体は続行
        pass
    key = (rs.get("loop", state.loop_no), rs.get("day", state.day))
    _before, hc, ai = split_day_tail(decisions, human_seats, key)
    return state, hc, ai, warn


def split_day_tail(log: list[dict], human_seats, day_key: tuple[int, int]
                   ) -> tuple[int, list[dict], dict]:
    """決定ログを「day_key の日より前／その日」に分け、(その日より前の人間手数, 当日の人間手,
    当日のAI決定 {actor:[chosen,...]}) を返す。Streamlit非依存＝テスト可能。

    ★rev2仕様（提案書§1・§8b裁定2）：クラウド保存は「日境界snapshot＋**その日の分だけ**の再生」。
      当日のAI決定（ai_replay）も要る＝日境界から再開するとAIがその日の札を再決定し、伏せ札でも
      **配置先は公開情報**＝ユーザーの盤面が目に見えて変わってしまうため。

    ★A-39（2026-07-19）：**run_day の外で起きる決定は当日分に含めない**。final_battle（最後の戦い＝
      ループ終了後）や loop_start/loop_start_area（ループ準備＝日境界より前）は run_day のフェイズ順に
      乗らないため、日境界から再生すると順序が合わず ReplayDesync になる（実測：完了ログの復元で
      final_battle_guess が3手目で非合法）。これらは再生せず、到達時に改めて決定する（＝最後の戦いは
      ユーザーがもう一度宣言する＝正しい挙動）。
    """
    from sim.flow import log_safe_chosen              # ★B-115：prov 剥がしの単一ソース
    _OUT_OF_DAY = {"final_battle", "loop_start"}      # run_day の外＝日単位の再生対象外
    before_human = 0
    human_day: list[dict] = []
    ai_day: dict[str, list[dict]] = {}
    for e in log:
        k = (e.get("loop"), e.get("day"))
        is_human = e.get("actor") in human_seats
        if k < day_key:
            if is_human:
                before_human += 1
        elif k == day_key:
            if e.get("phase") in _OUT_OF_DAY:
                continue                              # ★A-39：日外の決定は再生しない
            # ★B-115：再生素材から表示層の prov（B-100 上書き席="b100"／一致席=
            #   "b100_match"）を剥がす。ログ内の chosen は options 内の同一オブジェクト＝
            #   ライブ整合は identity で保たれるが、復元の合法手照合は**新規列挙の options**
            #   と dict 同値で行われる＝prov が残ると ReplayDesync になる（実測＝
            #   tests/test_log_snapshot_restore.py）。
            if is_human:
                human_day.append(log_safe_chosen(e["chosen"]))
            else:
                ai_day.setdefault(e["actor"], []).append(log_safe_chosen(e["chosen"]))
    return before_human, human_day, ai_day


def snapshot_to_cloud_payload(state: GameState, *, mode: str, app_version: str = "",
                              ui: dict | None = None,
                              human_choices: list[dict] | None = None,
                              ai_replay: dict | None = None) -> dict:
    """クラウド保存用のスナップショット dict（cloud.save_snapshot に渡す）。両モード共通スキーマ。

    mode: "solo"（主人公プレイ）/ "mastermind"（脚本家プレイ）＝復帰先の画面を決める。
    ui:   復帰時のカーソル等（任意・表示層の都合＝復元の必須要素ではない）。
    ★state は**日境界**の局面（on_day_start で掴む）＝GameState.to_snapshot()＝**神視点フル**
      （伏せ札の中身・配役・secret_log を含む。裁定済み＝提案書§7 論点1）。共有token発行UIには
      「神視点データを含む」注意書きが要る。
    human_choices/ai_replay: **その日の分だけ**（split_day_tail の返り値）。日境界から現在局面
      までを再生して戻す（rev2仕様＝§8b裁定2）。
    """
    return {
        "format": SNAPSHOT_PAYLOAD_FORMAT,
        "mode": mode,
        "app_version": app_version,
        "tool_build": _tool_build(),      # ★ビルド識別（復元時の不一致警告に使う）
        "snapshot": state.to_snapshot(),  # v付き・script内蔵で自己完結（Phase 0）＝日境界の局面
        "human_choices": [dict(c) for c in (human_choices or [])],   # 当日分のみ
        "ai_replay": {a: [dict(c) for c in seq] for a, seq in (ai_replay or {}).items()},
        "ui": dict(ui or {}),
    }


_SNAPSHOT_MODE_JP = {"solo": "🎮 主人公プレイ", "mastermind": "🎭 脚本家プレイ"}


def snapshot_mode_mismatch(payload, current_mode: str) -> str | None:
    """★A-31（2026-07-17）：payload の mode と現在モードが違えば**案内文**を返す（一致なら None）。

    mode は §1 rev2 で「復帰先の画面」と定義済み（solo＝主人公プレイ／mastermind＝脚本家プレイ）。
    別モードのトークンを**黙って開かない**＝開いても盤面の意味（どちらが自分か）が変わってしまう。
    A-27/A-19 と同じ fail-ignore 原則＝拒否して理由と直し方を出す。
    mode が無い payload（旧形式）は判定材料が無い＝None（＝呼び出し側の従来判定に委ねる）。
    Streamlit非依存＝テスト可能。
    """
    m = payload.get("mode") if isinstance(payload, dict) else None
    if not m or m == current_mode:
        return None
    return (f"このトークンは{_SNAPSHOT_MODE_JP.get(m, m)}用です。"
            f"モードを切り替えてから復元してください。")


def snapshot_payload_digest(payload: dict) -> str:
    """スナップショット payload の内容ハッシュ（オートセーブの差分dedupe用・Phase 2）。

    ★ビルド識別（tool_build/app_version）と ui は**除いて**比較する＝盤面と当日分の再生材料が
    同じなら「同じ局面」＝書かない（Streamlit は毎操作で再実行される＝dedupe しないと同一内容を
    毎rerun書いてしまう）。Streamlit非依存＝テスト可能。
    """
    import hashlib
    core = {k: payload.get(k) for k in ("format", "mode", "snapshot", "human_choices", "ai_replay")}
    blob = json.dumps(core, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def cloud_payload_day_tail(payload) -> tuple[list[dict], dict]:
    """スナップショット payload から「当日分の人間手・AI決定」を取り出す（rev2）。
    旧payload（当日分なし）でも空で返す＝日境界から再開する（後方互換）。"""
    if not isinstance(payload, dict):
        return [], {}
    hc = payload.get("human_choices")
    ar = payload.get("ai_replay")
    return (list(hc) if isinstance(hc, list) else [],
            dict(ar) if isinstance(ar, dict) else {})


def cloud_payload_to_state(payload) -> tuple[GameState | None, str | None]:
    """スナップショット payload → (GameState, 警告文字列)。読めなければ (None, 理由)。

    捏造しない＝未知形式・壊れた payload・未来版は None＋理由を返す（UI側は要確認にできる）。
    警告＝保存時と現在でビルド/版が違う（スナップショットは状態なので基本は復元可。ルール解釈が
    変わった稀ケースを可視化するだけ＝復元は妨げない）。
    """
    if not isinstance(payload, dict) or payload.get("format") != SNAPSHOT_PAYLOAD_FORMAT:
        return None, "このデータは対局スナップショットではありません（形式不明）。"
    snap = payload.get("snapshot")
    if not isinstance(snap, dict):
        return None, "スナップショットが壊れています。"
    try:
        state = GameState.from_snapshot(snap)
    except Exception as e:  # noqa: BLE001  未来版/欠損＝理由を見せて要確認にする
        return None, f"復元できません（{e}）。"
    saved_build = payload.get("tool_build") or ""
    cur_build = _tool_build()
    if saved_build and cur_build and saved_build != cur_build:
        return state, (f"この対局は別ビルドで保存されています（保存 {saved_build} / 現在 {cur_build}）。"
                       "盤面はそのまま復元しますが、ルール解釈の更新があった場合は結果が変わりえます。")
    return state, None


def cloud_payload_to_jsonl(payload) -> str | None:
    """クラウド棋譜 dict → JSONL文字列（load_game_lines/_apply_mmv_save に渡せる）。
    未知/旧形式や壊れた payload は None（UI側は要確認扱いにできる）。"""
    if isinstance(payload, dict) and payload.get("format") == CLOUD_PAYLOAD_FORMAT:
        j = payload.get("jsonl")
        return j if isinstance(j, str) else None
    return None


def compat_note(meta: dict, app_version: str = "") -> str | None:
    """棋譜ロード時の互換チェック（互換は"保証しない"方針＝版違いは警告文字列を返すだけ）。

    返り値：互換の懸念があれば警告文字列／問題なければ None。ログ形式バージョン不一致・
    アプリ版数不一致を見る（既存の版スタンプ基盤 log_format_version / app_version を使う）。
    """
    parts: list[str] = []
    lfv = meta.get("log_format_version")
    if lfv != LOG_FORMAT_VERSION:
        parts.append(f"ログ形式の版が異なります（保存 {lfv} / 現在 {LOG_FORMAT_VERSION}）")
    saved_av = meta.get("app_version") or ""
    if app_version and saved_av and saved_av != app_version:
        parts.append(f"アプリの版が異なります（保存 {saved_av} / 現在 {app_version}）")
    if not parts:
        return None
    return ("この棋譜は別バージョンで保存されています（互換は保証していません）。"
            "局面がずれる場合があります：" + "／".join(parts))
