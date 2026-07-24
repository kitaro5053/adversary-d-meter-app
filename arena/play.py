"""人間 vs AI 対戦ページ（M4）— 一人回しの練習相手。

起動:  streamlit run arena/play.py

人間が主人公3席（ソロ）を担当し、AI脚本家（当面ヒューリスティック）に挑む。
人間には公開情報のみ（配役・犯人・ルールは伏せる）＝実際のプレイと同じ推理体験。
進行は arena.interactive.play_interactive（毎回ゼロから決定的に再実行し、未選択の
人間決定で PendingHuman を投げて中断）に委ねる。

★この画面は脚本の真実を伏せて遊ぶためのもの。ゲーム終了後の「答え合わせ」でのみ
  配役・犯人を開示する（デバッグ用ビューアは別＝arena/viewer.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from agents import HeuristicMastermind, LLMMastermind  # noqa: E402
from arena.gamelog import game_to_jsonl  # noqa: E402
from arena.interactive import PendingHuman, ReplayDesync, play_interactive  # noqa: E402
from arena.replay import (  # noqa: E402
    board_json_from_snapshot,
    board_json_from_view,
    describe_choice,
)
from board_viz import board_html_from_json  # noqa: E402
from engine.data import (  # noqa: E402
    CHARACTER_FORBIDDEN,
    CHARACTER_INITIAL_AREA,
    CHARACTER_UNREST_THRESHOLD,
    GOODWILL_ABILITIES,
)
from engine.models import ONCE_PER_LOOP  # noqa: E402
from sim.reference import (  # noqa: E402
    CHARACTER_ATTRIBUTES,
    CHARACTER_TRAITS,
    GOODWILL_ABILITY_TEXT,
    INCIDENT_EFFECTS,
    ROLE_REFERENCE,
    RULE_X_REFERENCE,
    RULE_Y_REFERENCE,
    role_placement_summary,
)
from sim import random_script  # noqa: E402
from sim.sample_scripts import SAMPLE_SCRIPTS  # noqa: E402


def _api_key() -> str:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # noqa: BLE001  secrets 未設定環境
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _make_mastermind(kind: str, seed: int):
    if kind == "llm" and _api_key():
        import anthropic
        return LLMMastermind(anthropic.Anthropic(api_key=_api_key()), fallback_seed=seed)
    return HeuristicMastermind(seed)


HUMAN_SEATS = {"p1", "p2", "p3"}
_PHASE_JP = {"mastermind_set": "脚本家行動", "protagonist_set": "主人公行動",
             "goodwill_ability": "主人公能力（友好能力の宣言）",
             "final_battle_guess": "最後の戦い（役職の宣言）",
             "loop_start_area": "ループ開始"}

# ---------- 対局のセットアップ ----------
# ★ラベルはセット（FS/BTX）のみ。脚本名にルールY/Xを匂わせない（ネタバレ防止・2026-07-05）。
# サンプル脚本の内部名（表示順）。表示ラベルは花名（下記 _script_labels）で伏せる。
_SAMPLE_KEYS = ("basic", "guard", "revenge", "shrine",
                "btx_seal", "btx_future", "btx_bomb", "btx_contract", "btx_lovers")


def _script_labels() -> dict[str, str]:
    """脚本セレクタの表示ラベル→内部名。★脚本名マスキング（ネタバレ防止）＝花名で、
    脚本家側(play_vs_ai)の _SCRIPT_ALIASES を import して共有する（＝両モードで同じ花名・
    常に同期。レーン規律で読み取り/importは自由・play_vs_ai の play 参照は全て遅延importで
    循環なし）。ラベルは表示専用＝内部識別子(値)や保存(?g=は内部名)は不変。ユーザー選定 2026-07-10。"""
    from arena.play_vs_ai import _SCRIPT_ALIASES
    labels = {"🔰 メンバー調整FS": "beginner_FS",
              "🔰 メンバー調整BTX": "beginner_BTX",
              "🎲 ランダムFS": "random_FS", "🎲 ランダムBTX": "random_BTX"}
    for k in _SAMPLE_KEYS:
        labels[_SCRIPT_ALIASES.get(k, k)] = k   # 花名が無ければ内部名で伏せず出す（防御）
    return labels


def _load_script(name: str, seed: int):
    if name.startswith("beginner_"):
        # 初心者向け＝複雑な能力・特性のキャラが出にくいメンバー偏り生成（脚本家プレイと同一）。
        return random_script(set_name=name.split("_", 1)[1], seed=seed, beginner=True)
    if name.startswith("random_"):
        return random_script(set_name=name.split("_", 1)[1], seed=seed)
    return SAMPLE_SCRIPTS[name]()


# ---------- 対局の永続化（リロード/ブラウザ復元で途中局面を失わない） ----------
# ★対局は「脚本名・AI種別・seed・人間の選択列(choices)」から決定的に再現できる
#   （interactive.play_interactive が毎回ゼロから再実行）。この4つをURLのクエリ
#   パラメータ ?g= に圧縮して載せておけば、リロードやタブ復元で局面が戻る。
#   フルの basic 対局でも圧縮後 ~370 文字＝URL長に十分収まる。
#   ★Streamlitのバージョン差：1.40+ は st.query_params、1.28 は experimental_* を
#   使う。どちらも無ければ黙って諦める（ローカル1.28では永続化なし＝本番のみ効く）。
_GAME_QP_KEY = "g"
# ★Phase 2（2026-07-17）：オートセーブの復帰トークン（URLは「状態」でなく「ポインタ」＝短い乱数）。
#   ?g=（replay方式・現行）は**そのまま維持**＝Supabase無し環境のフォールバック（条件1）。
#   両方在る時の復元優先は ?s=（snapshot＝replay非依存＝厳密）。
_AUTO_QP_KEY = "s"
_DEFAULT_AUTOSAVE_LIMIT = 400   # 1セッション/日のクラウド書込上限（app_config で上書き可）


@st.cache_data(ttl=120, show_spinner=False)
def _autosave_limit() -> int:
    """オートセーブの日次書込上限（コスト保護・条件3）。app_config の autosave_limit で上書き可。
    ★120秒キャッシュ（app.py の _cloud_config_cached と同型）＝**毎rerunのHTTP GETを避ける**
    （条件2＝rerun非ブロック）。取得失敗時は既定値＝可用性優先（quota自体も cloud 側で fail-open）。"""
    try:
        import cloud as _c
        return int((_c.get_config() or {}).get("autosave_limit", _DEFAULT_AUTOSAVE_LIMIT))
    except Exception:  # noqa: BLE001
        return _DEFAULT_AUTOSAVE_LIMIT


def _encode_game() -> str | None:
    """現在の対局を圧縮base64文字列にする（未開始なら None）。"""
    if "play_script" not in st.session_state:
        return None
    import base64
    import json
    import zlib
    payload = {
        "s": st.session_state["play_script"],
        "m": st.session_state.get("play_mm", "heuristic"),
        "d": int(st.session_state.get("play_seed", 0)),
        "c": st.session_state.get("choices", []),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")


def _decode_game(blob: str) -> dict | None:
    """?g= の文字列を対局ペイロードへ戻す（壊れていたら None）。"""
    try:
        import base64
        import json
        import zlib
        raw = zlib.decompress(base64.urlsafe_b64decode(blob.encode("ascii")))
        from arena.gamelog import normalize_legacy_tokens
        d = normalize_legacy_tokens(json.loads(raw.decode("utf-8")))  # 旧トークンを現行へ
        if isinstance(d, dict) and "s" in d and isinstance(d.get("c"), list):
            return d
    except Exception:  # noqa: BLE001  壊れたURLは無視して新規扱い
        return None
    return None


def _qp_get(key: str) -> str | None:
    """クエリパラメータ取得（1.40+ / 1.28 両対応・無ければ None）。"""
    try:
        return st.query_params.get(key)                       # 1.40+
    except Exception:  # noqa: BLE001
        try:
            v = st.experimental_get_query_params().get(key)    # 1.28
            return v[0] if v else None
        except Exception:  # noqa: BLE001
            return None


def _qp_set(key: str, value: str | None) -> None:
    """クエリパラメータ設定（現在値と同じなら書かない＝再実行ループ防止）。"""
    if _qp_get(key) == value:
        return
    try:
        if value is None:
            try:
                del st.query_params[key]
            except KeyError:
                pass
        else:
            st.query_params[key] = value                      # 1.40+
        return
    except Exception:  # noqa: BLE001
        pass
    try:  # 1.28 フォールバック
        params = st.experimental_get_query_params()
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
        st.experimental_set_query_params(**params)
    except Exception:  # noqa: BLE001
        pass


# ---------- 対局のファイル・セーブ＆ロード（URLに頼らず局面を持ち運ぶ） ----------
# ★セーブ内容は _encode_game と同じ4点（脚本名・AI種別・seed・choices）＝再現に必要十分。
#   URL(?g=)は自動保存・自動復元だが、URLを失っても復元できるようファイルにも出せる。
_SAVE_TYPE = "rooper_play_save"


# ★A-39（2026-07-19）：_game_save_bytes（.rooper.json の**発行**）は撤去した＝
#   「💾 対局を保存」ボタンの専用ヘルパーで、ボタン撤去により未参照になったため（デッドコード）。
#   .rooper.json の**読込**（_apply_loaded_game の①分岐）は当面残す＝既存ファイルの救済（段階廃止）。
#   _SAVE_TYPE は読込側の判定に使うので残す。


# ★A-32（2026-07-17）：**対局に紐づく session_state キーの単一の登録先**。
#   別の対局へ切替える全ての入口（新規/チュートリアル/自作脚本/棋譜/.rooper.json/URL ?g=/
#   クラウド続き）は _reset_play_game() を通す＝ここに1行足せば全経路に効く。
#   ★これが無かったために A-32 が起きた：Phase 1/2 で足した play_snap_state を各掃除サイトへ
#   配線し忘れ、クラウド復元→新規対局で**古い復元局面が再開**された（A-27/A-31と同型＝
#   「機構は正しいが全経路への配線を忘れる」）。登録先を1箇所にして構造的に潰す。
_PLAY_GAME_KEYS: tuple[str, ...] = (
    # 対局の同一性
    "play_script", "play_script_obj", "play_seed", "play_tutorial",
    "choices", "play_ai_replay", "play_load_warning",
    # 進行に紐づくフラグ
    "play_extra_loops", "play_do_final_battle", "_play_end_logged",
    # クラウド続き（Phase 1/2）＝★A-32で登録漏れが露見した群
    "play_snap_state", "play_snap_ai_replay", "play_snap_warn",
    "play_snap_desync", "play_snap_mode_note", "play_snap_token",
    "play_auto_digest",
    # ★A-39：共有コード（棋譜）は撤去＝キーも登録簿から除去（集約簿の簡素化）。
)
# ★意図的に消さないもの（＝対局に紐づかない）：
#   play_auto_token＝1セッション×1 auto スロット＝次の対局も同じ行へ上書きする設計（提案書§1）。
#   play_mm/play_show_mm＝AIや表示の設定（ユーザー設定であって対局の一部ではない）。
#   表示メモ類（ry_/rx_/chardetail_ 等）は _clean_play_carryover() が担当（前方一致で掃除）。


def _normalize_restored_state(_st) -> str:
    """★A-36 条件3（2026-07-19）：壊れトークンの救済。復元 state を正規化し、修復した旨の警告文
    （無ければ空文字）を返す。黙って直さず、呼び出し側がユーザーに表示する（fail-ignore の表示側）。

    (1) mm設置が規定(3)超なら先頭3枚に切り詰める（mm AIは決定的＝先頭3枚が本来の手）。
    (2) phase_snapshots の (loop,day,point) 重複を除去（各キー最後の1つ＝最新の盤面を残す・順序保持）。
    ＝A-36 の累積バグ（同一 state 使い回し）で生成された壊れトークンを、新品 state に載せる前に直す。
    """
    warns: list[str] = []
    mm = [p for p in _st.turn_placements if p.get("owner") == "mastermind"]
    if len(mm) > 3:
        others = [p for p in _st.turn_placements if p.get("owner") != "mastermind"]
        _st.turn_placements = mm[:3] + others
        warns.append(f"脚本家の設置札が壊れていました（{len(mm)}枚→3枚に修復）")
    seen: dict = {}
    for s in _st.phase_snapshots:
        seen[(s.get("loop"), s.get("day"), s.get("point"))] = s
    if len(seen) < len(_st.phase_snapshots):
        n = len(_st.phase_snapshots) - len(seen)
        _st.phase_snapshots = list(seen.values())
        warns.append(f"経過の重複を除去（{n}件）")
    return "／".join(warns)


def _reset_play_game(**overrides) -> None:
    """対局に紐づく状態を全消しし、渡された分だけ設定する（＝別の対局へ切替える入口の共通前処理）。
    表示メモ類の掃除（_clean_play_carryover）も併せて行う＝入口はこれ1つを呼べばよい。"""
    for _k in _PLAY_GAME_KEYS:
        st.session_state.pop(_k, None)
    _clean_play_carryover()
    st.session_state.update(overrides)


def _clean_play_carryover() -> None:
    """前の対局の推理メモ・盤面カーソル・新着追跡・4ループ延長を持ち越さない共通掃除。"""
    st.session_state["play_extra_loops"] = 0
    st.session_state["play_do_final_battle"] = False
    for _k in [k for k in list(st.session_state)
               if k.startswith(("ry_", "rx_", "chardetail_", "_ev_", "_live_",
                                "_log_", "log_frame_", "rolegray_", "rolememo_", "incmemo_"))]:
        del st.session_state[_k]


def _script_from_json(text: str):
    """自作脚本JSON（gamelog.script_to_dict 形式）を Script へ。検証込み。
    返り値 (Script, "") 成功／(None, 理由) 失敗。script_studio/builder の『プレイ用JSON』が対象。"""
    import json
    try:
        d = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return None, f"JSONとして読めません（{e}）。"
    if not isinstance(d, dict):
        return None, "JSONオブジェクト（{ ... }）を貼り付けてください。"
    from sim.state import Incident, Script, validate_script
    sc = None
    # ① 正典形式（gamelog.script_to_dict：set_name/rule_x/rule_x2/incidents=[{day,name,culprit}]）
    try:
        from arena.gamelog import script_from_dict
        sc = script_from_dict(d)
    except Exception:  # noqa: BLE001  次に studio エクスポート形式を試す
        sc = None
    # ② script_studio のエクスポート形式（set / rule_x=list）をフォールバックで受ける
    if sc is None:
        try:
            _rxs = d.get("rule_x") or d.get("rule_xs") or []
            if isinstance(_rxs, str):
                _rxs = [_rxs]
            sc = Script(
                rule_y=d["rule_y"],
                rule_x=(_rxs[0] if len(_rxs) >= 1 else ""),
                rule_x2=(_rxs[1] if len(_rxs) >= 2 else None),
                loops=int(d.get("loops", 4)),
                days_per_loop=int(d.get("days_per_loop", 3)),
                cast=list(d["cast"]),
                roles=dict(d.get("roles", {})),
                incidents=[Incident(**i) for i in d.get("incidents", [])],
                set_name=(d.get("set_name") or d.get("set") or "FS"))
        except Exception as e:  # noqa: BLE001
            return None, (f"脚本の形式が不正です（{type(e).__name__}）。"
                          "script_studio / 脚本ビルダーの『🎮 プレイ用JSON』をそのまま貼ってください。")
    try:
        validate_script(sc)
    except Exception as e:  # noqa: BLE001
        return None, f"脚本が正規ではありません（{e}）。"
    return sc, ""


def _apply_loaded_game(raw: bytes) -> bool:
    """セーブを session_state に適用。2形式対応：
    ①.rooper.json（seed＋選択列＝旧方式・AI/生成器に通し直す）
    ②棋譜 .jsonl（確定脚本＋記録済み決定＝②-2・記録再適用で復元・AI非依存で過去再現）。成功で True。"""
    import json
    text = raw.decode("utf-8", errors="replace")
    _lines = [ln for ln in text.splitlines() if ln.strip()]
    # ②棋譜(.jsonl)判定：1行目が type:meta なら確定脚本＋記録から再開。
    if _lines:
        try:
            _first = json.loads(_lines[0])
        except Exception:  # noqa: BLE001
            _first = None
        if isinstance(_first, dict) and _first.get("type") == "meta":
            try:
                from arena.gamelog import (
                    ai_replay_from_decisions, compat_note,
                    human_choices_from_decisions, load_game_lines, resume_from_meta)
                script_obj, meta, decisions = load_game_lines(_lines)
                _compat = compat_note(meta, st.session_state.get("app_version", ""))
                # ★A-39（2026-07-19）：新形式ログ（resume_snapshot あり）は **snapshot 復元**で
                #   読み込む＝replay（決定の再実行）に依存しない＝ドリフトしない（旧ログの実測＝
                #   9件中5件が現行コードで再生不能）。当日分だけ再生＝A-36/Phase1 と同じ構造。
                _rf = resume_from_meta(meta, decisions, {"p1", "p2", "p3"})
                if _rf is not None:
                    _st0, _hc, _ai, _norm = _rf
                    _reset_play_game(
                        play_script_obj=_st0.script,      # 脚本は snapshot 内蔵（自作脚本もOK）
                        play_script="棋譜（読み込み）", play_seed=0,
                        # ★A-36：snapshot(dict) 保持＝再実行部が毎rerun 新品stateを起点にする
                        play_snap_state=_st0.to_snapshot(),
                        play_snap_ai_replay=(_ai or None), choices=list(_hc),
                        play_extra_loops=max(0, int(_st0.loop_no) - 4),   # A-31
                        play_snap_warn="／".join(x for x in (_norm,) if x),
                        play_load_warning=_compat)
                else:
                    # ★旧形式ログ（resume_snapshot なし）＝従来どおり replay で復元し、
                    #   「ずれる場合あり」（compat_note）を維持する＝後方互換（FableA条件）。
                    human = human_choices_from_decisions(decisions, {"p1", "p2", "p3"})
                    _reset_play_game(
                        play_script_obj=script_obj,        # 確定脚本を直接使う
                        play_script="棋譜（読み込み）", play_seed=0, choices=list(human),
                        # ★AI脚本家の過去決定も記録から再生（AI非依存で過去を再現・②-2）。
                        play_ai_replay=ai_replay_from_decisions(decisions, {"p1", "p2", "p3"}),
                        play_load_warning=_compat)
                st.session_state["play_mm"] = "heuristic"   # AI設定＝対局に紐づかない
            except Exception:  # noqa: BLE001  壊れた棋譜は無視
                return False
            _clean_play_carryover()
            return True
    # ①.rooper.json（seed＋選択列）
    try:
        from arena.gamelog import normalize_legacy_tokens
        d = normalize_legacy_tokens(json.loads(text))  # 旧トークン(anryaku/gonoki)を現行へ
        if not (isinstance(d, dict) and "s" in d and isinstance(d.get("c", []), list)):
            return False
        # ★A-32：対局に紐づく状態は _reset_play_game で全消し（登録先は _PLAY_GAME_KEYS 一箇所）。
        _reset_play_game(play_script=d["s"], play_seed=int(d.get("d", 0)),
                         choices=list(d.get("c", [])))
        st.session_state["play_mm"] = d.get("m", "heuristic")   # AI設定＝対局に紐づかない
    except Exception:  # noqa: BLE001  壊れたファイルは無視
        return False
    return True


def _restore_game_from_url() -> None:
    """URLに対局があり、まだ復元していなければ session_state に戻す（初回ロード時）。

    ★Phase 2（2026-07-17）：**?s=（オートセーブのトークン）を優先**して復元する＝snapshot方式＝
      replay非依存＝ドリフトしない（実測：replay復元は保存済み9件中5件が再生不能）。取得できない
      （Supabase未設定・期限切れ・別環境）場合は従来の ?g=（replay）へフォールバック＝条件1。
    """
    if "play_script" in st.session_state:
        return  # 既に対局中（新規対局直後など）＝URLで上書きしない
    _tok = _qp_get(_AUTO_QP_KEY)
    if _tok:
        try:
            import cloud as _c
            from arena.gamelog import (cloud_payload_day_tail, cloud_payload_to_state,
                                       snapshot_mode_mismatch)
            _pl = _c.load_snapshot(_tok) if _c.enabled() else None
            # ★A-31：URL経由でも**別モードのトークンは開かない**（?s= は両モード共用＝脚本家の
            #   URLを主人公モードで開くと盤面の意味が変わる）。案内を残して ?g= へフォールバック。
            _url_note = snapshot_mode_mismatch(_pl, "solo") if _pl else None
            if _url_note:
                st.session_state["play_snap_mode_note"] = _url_note
                _pl = None
            _st0, _why = cloud_payload_to_state(_pl) if _pl else (None, None)
            if _st0 is not None:
                _hc, _ai = cloud_payload_day_tail(_pl)
                _norm = _normalize_restored_state(_st0)   # ★A-36：壊れトークンを救済（+警告）
                # ★A-32：URL復元も入口＝_reset_play_game を通す（登録先は _PLAY_GAME_KEYS 一箇所）。
                # ★A-36：play_snap_state は snapshot(dict) で保持（毎rerun 新品state＝累積根絶）。
                _reset_play_game(
                    play_snap_state=_st0.to_snapshot(), play_snap_ai_replay=(_ai or None),
                    choices=list(_hc), play_script_obj=_st0.script,   # 脚本はsnapshot内蔵
                    play_script="クラウド（続き）", play_seed=0,
                    play_extra_loops=max(0, int(_st0.loop_no) - 4),   # A-31
                    play_snap_warn="／".join(x for x in (_why, _norm) if x))
                st.session_state["play_auto_token"] = _tok          # 以後も同じ行へ上書き（消さない）
                return
        except Exception:  # noqa: BLE001  クラウド不通＝?g= へフォールバック（壊さない）
            pass
    blob = _qp_get(_GAME_QP_KEY)
    if not blob:
        return
    d = _decode_game(blob)
    if not d:
        return
    st.session_state["play_script"] = d["s"]
    st.session_state["play_mm"] = d.get("m", "heuristic")
    st.session_state["play_seed"] = int(d.get("d", 0))
    st.session_state["choices"] = list(d["c"])


# ---------- 表示ヘルパー ----------
def _render_used_cards(state) -> None:
    """使用済みの1ループ1回カードを盤面わきに表示（🔴使用済 / ⚪未使用）。"""
    st.markdown("**🃏 使用済み（1/ループ）**")
    st.caption("消費すると次のループ開始まで手札に戻りません。")

    def _line(label: str, owner_key: str, cards: list[str]) -> None:
        used = state.used_cards.get(owner_key, [])
        marks = "　".join(f'{"🔴" if c in used else "⚪"}{c}' for c in cards)
        st.markdown(f'<small>{label}：{marks}</small>', unsafe_allow_html=True)

    _line("脚本家", "mastermind", sorted(ONCE_PER_LOOP["mastermind"]))
    for seat in ("p1", "p2", "p3"):
        _line(f"あなた {seat}", seat, sorted(ONCE_PER_LOOP["protagonist"]))


def _char_detail_md(nm: str) -> str:
    """キャラ1体の詳細（属性・エリア・友好能力の原文手順・特性の元テキスト）をmarkdownで返す。"""
    attrs = CHARACTER_ATTRIBUTES.get(nm, "—")
    init = CHARACTER_INITIAL_AREA.get(nm) or "脚本家指定"
    forb = "・".join(sorted(CHARACTER_FORBIDDEN.get(nm, frozenset()))) or "なし"
    th = CHARACTER_UNREST_THRESHOLD.get(nm, "?")
    ab_parts = []
    for ab in GOODWILL_ABILITIES.get(nm, []):
        head = f'{ab["name"]}（♡{ab["hearts"]}{"・1/L" if ab["once_per_loop"] else ""}）'
        body = GOODWILL_ABILITY_TEXT.get((nm, ab["name"]))
        ab_parts.append(f'- **{head}**' + (f'  \n  {body}' if body else ""))
    ab_lines = "\n".join(ab_parts) or "- （友好能力なし）"
    trait = CHARACTER_TRAITS.get(nm)
    trait_block = f'\n\n**特性（元テキスト）**：{trait}' if trait else ""
    return (f'**属性**：{attrs}　｜　**初期エリア**：{init}　｜　**禁止エリア**：{forb}'
            f'　｜　**不安臨界**：{th}\n\n**友好能力（カード原文の手順）**：\n{ab_lines}{trait_block}')


def _rule_excl(changed: str, other: str) -> None:
    """確有/確無は排他：一方をONにしたら他方をOFFにする（on_changeコールバック）。"""
    if st.session_state.get(changed):
        st.session_state[other] = False


def group_gw_options(options: list[dict], goshinboku_jp: dict | None = None) -> dict:
    """友好能力の options を「発動能力（人＋能力）」ごとにまとめる（2段セレクタ①用）。
    返り値 {能力ラベル: [option,...]}（挿入順＝options順）。Streamlit非依存＝テスト可能。"""
    _jp = goshinboku_jp or {"anyaku": "暗躍", "unrest": "不安", "goodwill": "友好"}
    groups: dict[str, list[dict]] = {}
    for o in options:
        if o.get("action") == "pass":
            k = "（使わない・パス）"
        elif "goshinboku" in o:        # ご神木の特性（カウンター移動・ハート不要・拒否対象外）
            k = f'〈ご神木〉『{_jp.get(o["goshinboku"], o["goshinboku"])}カウンターを同エリアへ移す』'
        elif "character" in o and "ability" in o:
            k = f'〈{o["character"]}〉『{o["ability"]}』'
        else:                          # 想定外の選択肢の形でもクラッシュさせない
            k = describe_choice(o)
        groups.setdefault(k, []).append(o)
    return groups


def resolve_gw_choice(groups: dict, ability_key, target_label):
    """①能力ラベル＋②対象ラベルから option を引く。**引けなければ None**。

    ★A-27（2026-07-16）：stale な widget 値（ホット更新・連打・能力切替時の状態競合）でも
    「ユーザーが選んでいない手を黙って打つ」ことは絶対にしない＝None を返し、呼び出し側は
    クリックを無視する。A-19 はここでグループ先頭へフォールバックしており、本番で実害が出た
    （FB3 beginner_FS seed13 L1D2＝ログ実測：〈マスコミ〉『暗躍+1（キャラ/ボード）』→学校 の
    クリックが、能力・対象とも先頭へ落ちて『任意キャラに不安+1』→異世界人 に化けた）。
    クラッシュ根絶（A-19の目的）は None 返しでも達成でき、誤操作は生まない。
    Streamlit非依存＝テスト可能。"""
    grp = groups.get(ability_key)
    if not grp:
        return None
    if len(grp) == 1:                  # 対象が1つ＝②Boxは出さない＝対象ラベルは見ない
        return grp[0]
    labels = [str(o.get("target")) for o in grp]
    if target_label not in labels:
        return None
    return grp[labels.index(target_label)]


def _rule_header() -> None:
    """確有/確無の列ヘッダを1回だけ表示（各行はチェックボックスのみ＝表形式・コンパクト）。"""
    h_y, h_n, h_i = st.columns([1, 1, 12])
    h_y.markdown("<div style='text-align:center;font-size:0.75em;opacity:0.7'>確有</div>",
                 unsafe_allow_html=True)
    h_n.markdown("<div style='text-align:center;font-size:0.75em;opacity:0.7'>確無</div>",
                 unsafe_allow_html=True)


def _rule_row(prefix: str, rule: str, roles: str, text: str) -> None:
    """ルール1行＝端にチェックボックスのみ（ラベルはヘッダ）＋本文を1行にまとめる。"""
    yk, nk = f"{prefix}_yes_{rule}", f"{prefix}_no_{rule}"
    c_y, c_n, c_i = st.columns([1, 1, 12])
    c_y.checkbox("確有", key=yk, on_change=_rule_excl, args=(yk, nk),
                 label_visibility="collapsed", help="確実に使われている（赤枠で強調）")
    c_n.checkbox("確無", key=nk, on_change=_rule_excl, args=(nk, yk),
                 label_visibility="collapsed", help="確実に使われていない（薄く表示）")
    yes, no = st.session_state.get(yk, False), st.session_state.get(nk, False)
    if yes:
        style = "border:2px solid #e2483d;border-radius:5px;padding:2px 8px;"
    elif no:
        style = "opacity:0.28;padding:2px 8px;"
    else:
        style = "padding:2px 8px;border:1px solid rgba(128,128,128,0.2);border-radius:5px;"
    c_i.markdown(
        f'<div style="{style};font-size:0.88em"><b>{rule}</b>'
        f'<span style="opacity:0.6">（{roles}）</span> {text}</div>',
        unsafe_allow_html=True)


def _table_no_index(rows: list[dict]) -> None:
    """表を通し番号（0,1,2…）なしで表示する（先頭列を行ラベルにする・要望 2026-07-10）。"""
    import pandas as pd
    df = pd.DataFrame(rows)
    st.table(df.set_index(df.columns[0]) if len(df.columns) else df)


# ---------- 参照セクション（主人公プレイ／脚本家プレイ共通・B8 2026-07-10） ----------
#   脚本家プレイ側（arena/play_vs_ai）からも同じ描画を呼べるよう関数化。
#   aids=False / memo=False は「推理の書き込み欄（確有/確無・薄く・メモ）」を省く
#   ＝脚本家は真実を知っているので推理メモが要らない（キー衝突回避に key_prefix）。
def render_char_reference(script, mobile: bool = False, *, key_prefix: str = "") -> None:
    """登場キャラクター一覧（カードテキスト）。配役＝裏の役職は含めない。"""
    st.caption("キャラクターカードの表の情報（配役＝裏の役職は含みません）。"
               "友好能力の『♡n』は必要友好カウンター数、(1/L)は1ループ1回。")
    rows = []
    for _nm in script.cast:
        abilities = "／".join(
            f'{ab["name"]} ♡{ab["hearts"]}' + ("(1/L)" if ab["once_per_loop"] else "")
            for ab in GOODWILL_ABILITIES.get(_nm, [])
        ) or "（なし）"
        forb = "・".join(sorted(CHARACTER_FORBIDDEN.get(_nm, frozenset()))) or "なし"
        rows.append({
            "キャラ": _nm,
            "不安臨界": CHARACTER_UNREST_THRESHOLD.get(_nm, "?"),
            "初期": CHARACTER_INITIAL_AREA.get(_nm) or "脚本家指定",
            "禁止": forb,
            "属性": CHARACTER_ATTRIBUTES.get(_nm, "—"),
            "友好能力": abilities,
            "特性": CHARACTER_TRAITS.get(_nm, "—"),
        })
    _table_no_index(rows)
    if any(_n == "大物" for _n in script.cast):
        st.warning("⚠ **大物**の能力（役職開示など）は縄張り（テリトリー）内のキャラだけを対象に"
                   "できます。縄張りは公開情報（📅予定事件の欄に🚩で表示）＝どのキャラが開示され"
                   "うるかを縛る重要情報です。詳細は下のトグルで確認してください。")
    st.markdown("**▼ 各キャラの詳細（元テキスト）** — トグルで開閉")
    _ncols = 1 if mobile else 3
    _dcols = st.columns(_ncols)
    for _i, _nm in enumerate(script.cast):
        with _dcols[_i % _ncols]:
            if st.toggle(_nm, key=f"{key_prefix}chardetail_{_nm}"):
                st.markdown(_char_detail_md(_nm))


def render_incident_reference(script, *, memo: bool = True, key_prefix: str = "") -> None:
    """予定事件の効果。memo=False で自由メモ欄を省く（脚本家プレイ＝推理不要）。"""
    st.caption("公開シートに予定された事件の効果。発生条件＝犯人が生存かつ不安臨界以上の不安"
               "（犯人は非公開）。" + ("左の欄に自由メモ（10文字）を書けます。" if memo else ""))
    _incs = sorted(script.incidents, key=lambda x: x.day)
    if not _incs:
        st.caption("（予定事件なし）")
        return
    if memo:
        _hm, _hc = st.columns([2, 10])
        _hm.markdown("<div style='text-align:center;font-size:0.75em;opacity:0.7'>メモ</div>",
                     unsafe_allow_html=True)
    for _idx, _inc in enumerate(_incs):
        _mh = ""
        if memo:
            cm, cc = st.columns([2, 10])
            _memo = cm.text_input("メモ", key=f"{key_prefix}incmemo_{_idx}_{_inc.day}_{_inc.name}",
                                  max_chars=10, label_visibility="collapsed",
                                  placeholder="メモ")
            _mh = (f'<span style="color:#e2483d;font-weight:600">〔{_memo}〕</span> '
                   if _memo else "")
        else:
            cc = st
        cc.markdown(
            f'<div style="padding:2px 8px;border:1px solid rgba(128,128,128,0.2);'
            f'border-radius:5px;font-size:0.88em">{_mh}<b>{_inc.day}日目 {_inc.name}</b>'
            f'<span style="opacity:0.6">：</span>'
            f'{INCIDENT_EFFECTS.get(_inc.name, "（要確認）")}</div>',
            unsafe_allow_html=True)


def render_rule_reference(script, *, aids: bool = True, key_prefix: str = "") -> None:
    """ルール一覧（効果早見）。aids=True で確有/確無チェック付き（主人公プレイ）。
    aids=False はチェックなしの早見のみ（脚本家プレイ＝真実を知っている）。"""
    st.caption("このセットで使われうるルールの一覧" + (
        "（実際に使われているルールは伏せ＝推理対象）。各行の端『確有』→赤枠で強調／"
        "『確無』→薄く表示。推理を書き込みながら候補を絞れます（この印は推理メモで、"
        "正解判定には影響しません）。" if aids else "（敗北条件と副次ルールの効果早見）。"))
    _in_set = "FS" if script.set_name == "FS" else "BTX"

    def _emit(prefix, ref):
        if aids:
            _rule_header()
        for _k, _v in ref.items():
            if _in_set in _v["set"] or "FS/BTX" in _v["set"]:
                if aids:
                    _rule_row(prefix, _k, _v["roles"], _v["text"])
                else:
                    st.markdown(
                        f'<div style="padding:2px 8px;border:1px solid '
                        f'rgba(128,128,128,0.2);border-radius:5px;font-size:0.88em;'
                        f'margin:2px 0"><b>{_k}</b><span style="opacity:0.6">'
                        f'（{_v["roles"]}）</span> {_v["text"]}</div>',
                        unsafe_allow_html=True)

    st.write("**ルールY（主要な敗北条件）**")
    _emit("ry", RULE_Y_REFERENCE)
    st.write("**ルールX（副次ルール）**")
    _emit("rx", RULE_X_REFERENCE)


def render_role_reference(script, *, aids: bool = True, key_prefix: str = "") -> None:
    """役職能力表。aids=True で薄く/メモのチェック付き（主人公プレイ）。
    aids=False はチェックなしの一覧のみ（脚本家プレイ）。"""
    st.caption("このセットに登場しうる役職の能力と、どのルールで何人配置されるか。" + (
        "左端のチェックで行を薄く（除外メモ）、隣の欄に自由メモ（10文字）を書けます。"
        if aids else ""))
    _placement = role_placement_summary(script.set_name)
    if aids:
        _h0, _h1, _h2 = st.columns([1, 2, 10])
        _h0.markdown("<div style='text-align:center;font-size:0.75em;opacity:0.7'>薄く</div>",
                     unsafe_allow_html=True)
        _h1.markdown("<div style='text-align:center;font-size:0.75em;opacity:0.7'>メモ</div>",
                     unsafe_allow_html=True)
    for _role, _v in ROLE_REFERENCE.items():
        if _role not in _placement and _role != "パーソン":
            continue
        _memo_html = ""
        _style = "padding:2px 8px;border:1px solid rgba(128,128,128,0.2);border-radius:5px;"
        if aids:
            c0, c1, c2 = st.columns([1, 2, 10])
            _gray = c0.checkbox("薄く", key=f"{key_prefix}rolegray_{_role}",
                                label_visibility="collapsed",
                                help="この役職を候補から外したメモ（判定には影響しません）")
            _memo = c1.text_input("メモ", key=f"{key_prefix}rolememo_{_role}", max_chars=10,
                                  label_visibility="collapsed", placeholder="メモ")
            _style = ("opacity:0.28;" if _gray else "") + _style
            _memo_html = (f'<span style="color:#e2483d;font-weight:600">〔{_memo}〕</span> '
                          if _memo else "")
        else:
            c2 = st
        c2.markdown(
            f'<div style="{_style}font-size:0.88em">{_memo_html}<b>{_role}</b>'
            f'<span style="opacity:0.6">（{_v["clause"]}・上限{_v["max"]}）</span> '
            f'配置: {_placement.get(_role, "—")}｜{_v["text"]}</div>',
            unsafe_allow_html=True)



def render_play(mobile: bool = False, stable: bool = False) -> None:
    """一人回しページ本体（app.py からモード切替で呼ぶ／standalone実行も可）。

    stable=True（安定版＝APP_CHANNEL=stable）では C-18 の機能隠蔽が働く＝
    チュートリアル／コーチα版／詰みヒントのトグルを出さない（外部公開でのメンテ困難のため）。
    既定 False＝開発版扱い＝安全側（standalone実行や未指定でも機能は出る）。

    ★set_page_config は呼ばない（app.py 側が既に設定済み。standalone時は
    __main__ ガードで設定してから呼ぶ）。session_stateキーは play_/tgt_/card_/
    move_/ry_/rx_/chardetail_/frame_ 等で app.py と衝突しない。
    mobile=True のときは横並び（盤面＋使用済み欄・ステッパー）を縦積みにして狭幅で見やすくする
    ＝PC版（mobile=False）のコードパスは不変。
    """
    st.title("🎮 主人公としてプレイ（1人回しでAI脚本家と対戦）")
    with st.sidebar:
        st.header("対局設定")
        # ★AppTest互換：options は表示文字列そのもの（format_func＋非文字列は壊れる）
        _SCRIPT_LABELS = _script_labels()   # 花名（脚本家側と共有・遅延import）
        script_label = st.selectbox(
            "脚本", list(_SCRIPT_LABELS),
            help="花の名前のシナリオはLLMが適当に作ったもの、"
                 "それ以外はランダムに機械的に生成したものです。")
        script_name = _SCRIPT_LABELS[script_label]
        # AI脚本家：LLM脚本家は未検証のため現在は選べない（定石AIのみ）。
        _MM_LABELS = {"定石AI（ヒューリスティック）": "heuristic"}
        mm_label = st.selectbox("AI脚本家", list(_MM_LABELS),
                                help="LLM脚本家は未検証です。")
        mm_kind = _MM_LABELS[mm_label]
        st.caption("🚫 LLM脚本家（未検証・現在は選べません）")
        seed = st.number_input("seed（脚本家AI＋ランダム脚本の種）", value=0, step=1)
        if script_name.startswith("beginner_"):
            st.caption("🔰 メンバー調整：複雑な能力・特性のキャラが出にくくなります。"
                       "seedを変えると登場人物・ルール・事件が変わります。")
        elif script_name.startswith("random_"):
            st.caption("🎲 ランダム脚本：seedを変えると登場人物・ルール・事件が変わります。")
        st.caption("脚本の真実（配役・犯人・ルール）は伏せられています。")
        # ★チュートリアル教材：既定は非表示。下の「📖 チュートリアル」トグルON時のみ、
        #   ここ（seed の下）に入門教材の選択を出す（ユーザー要望 2026-07-10）。
        tut_key = None
        if st.session_state.get("play_tut_on"):
            from tutorial import TUTORIALS
            st.caption("惨劇の基本を3本で学ぶ導入教材。ボタンで**即開始**＝脚本が固定され、"
                       "開始後に『学ぶこと・注目ポイント』が本体上部に出ます。")
            # ★導入3部作をワンクリック開始（自作脚本投入口=play_script_obj 経由・確定脚本）。
            for _t in sorted(TUTORIALS, key=lambda x: x.order):
                if st.button(f"▶ {_t.title} を始める", key=f"tut_start_{_t.key}",
                             use_container_width=True):
                    # ★A-32：入口は _reset_play_game を通す（対局キーの全消し＋必要分の設定）。
                    _reset_play_game(play_script_obj=_t.script_factory(),  # 確定脚本を直接使う
                                     play_script=_t.title, play_tutorial=_t.key,
                                     play_seed=0, choices=[])
                    st.session_state["play_mm"] = "heuristic"
                    st.rerun()
        if st.button("この設定で開始", type="primary"):
            # チュートリアル選択時は対応サンプル脚本に差し替え（教材テキストを本体上部に出す）。
            _TUT_SCRIPT = {"t1_mamori": "basic", "t2_jiken": "guard", "t3_jouhou": "revenge"}
            # ★A-32：対局に紐づく状態（棋譜の残骸・4ループ延長・クラウド復元の起点・共有コード・
            #   終局フラグ・推理メモ等）は _reset_play_game が**一括で**消す＝個別に列挙しない
            #   （列挙すると新機構を足した時にここへの追加を忘れる＝A-32の原因そのもの）。
            _reset_play_game(play_script=_TUT_SCRIPT.get(tut_key, script_name),
                             play_tutorial=tut_key, play_seed=int(seed), choices=[])
            st.session_state["play_mm"] = mm_kind
            import cloud as _cloud
            _cloud.log_event("play_start", side="protagonist",  # 共通イベント（メタのみ）
                             app_version=st.session_state.get("app_version", ""))
            st.rerun()
        st.divider()
        # ★C-18（2026-07-24）：外部公開用の機能隠蔽＝安定版（APP_CHANNEL=stable）では
        #   チュートリアル／コーチα版／詰みヒントを**出さない**（メンテ困難のため・ユーザー要望）。
        #   開発版・友人テストでは現状維持。C-12方式（_IS_STABLE）の流用で、判定は app.py から
        #   引数で受ける（standalone実行や未指定は開発版扱い＝安全側＝機能は出る）。
        #   ★トグルを出さない＝session_state のキーも作らない＝下流の参照は .get() 側で False に
        #   落ちる（既存コードは st.session_state.get(...) 参照＝安全）。
        if not stable:
            # ★詰みヒント（レース判定）。真の配役を使う＝ネタバレなので既定OFF・opt-in。
            st.toggle("💡 詰みヒント(α版・⚠神視点)", key="play_hint",
                      help="現局面が『脚本家の確定勝ち／主人公の防衛可／読み合い』かをレース解析で"
                           "表示します。★配役・犯人（伏せ情報）を使うので推理のネタバレになります。")
            # ★コーチ（主人公AIの推奨手＋理由＋開幕方針）。公開情報のみ＝ネタバレではない・既定OFF。
            #   ※LLMは使わず、主人公AI（決定的ヒューリスティック）の評価＋定型文テンプレートの組み合わせ。
            st.toggle("🎓 コーチ(α版)", key="play_coach",
                      help="あなたの手番で、主人公AIの推奨手・理由・開幕方針（冷却役の投資など）を"
                           "表示します。LLMは使わず定型文＋AIのスコアで作成。公開情報だけを使う＝"
                           "ネタバレではありません（α版・正解の保証なし）。")
            # ★チュートリアル：ONにすると seed の下に入門教材の選択が出る（既定OFF）。
            st.toggle("📖 チュートリアル", key="play_tut_on",
                      help="惨劇の基本を3本で学ぶ導入教材。ONにすると『新規対局』の上（seedの下）に"
                           "教材の選択が出ます。")
        # ★脚本家AIの思考表示（#6/#7・デバッグ）：各手のスコアと勝ち筋読みを盤面下に出す。
        #   ★神視点（配役・勝ち筋を使う）＝推理のネタバレになるので既定OFF。ヒューリスティックmm専用。
        st.toggle("🧠 脚本家AIの思考(デバッグ・⚠神視点)", key="play_show_mm",
                  help="AI脚本家がどの勝ち筋（KP暗躍/ボード/主人公殺害）にどれだけ資金を出し、"
                       "各手を何点で評価したかを盤面の下に表示します。★配役・勝ち筋を使うので"
                       "推理のネタバレになります（デバッグ用・ヒューリスティックAI限定）。")
        st.divider()
        # ★対局のロード（局面を復元）。.rooper.json（再開用セーブ）と .jsonl（棋譜＝②-2・
        #   確定脚本＋記録から再開）の両方に対応。セーブは本体側の保存ボタンから。
        _loaded = st.file_uploader(
            "📂 対局を読み込む（.rooper.json / 棋譜.jsonl）", type=["json", "jsonl"],
            key="play_load_file",
            help="『💾 対局を保存』(.rooper.json) か『📼 ログを保存』(.jsonl=棋譜) のファイルを"
                 "選ぶと、その局面から再開できます。棋譜は別バージョンだと局面がずれる場合があります。")
        if _loaded is not None:
            import hashlib
            _raw = _loaded.getvalue()
            _sig = hashlib.md5(_raw).hexdigest()
            if st.session_state.get("_loaded_sig") != _sig:  # 同一ファイルの再読込ループ防止
                st.session_state["_loaded_sig"] = _sig
                if _apply_loaded_game(_raw):
                    st.success("対局を読み込みました。")
                    st.rerun()
                else:
                    st.error("このファイルは対局セーブ／棋譜として読み込めませんでした。")
        # ★自作脚本のプレイ投入口（§2b・2026-07-13）：script_studio/脚本ビルダーで組んだ脚本JSONを
        #   開始脚本として読み込む（ランダム生成でなく自作脚本で一人回し）。
        with st.expander("✍️ 自作脚本で始める（script_studio/脚本ビルダーの『プレイ用JSON』を貼付）"):
            _cj = st.text_area("脚本JSON を貼り付け", key="play_custom_json", height=120,
                               placeholder='{"set_name": "First Steps", "rule_y": "...", ...}')
            st.caption("⚠ 自作脚本の対局は **URL自動保存の対象外**です（リロード/ブラウザ復元で消えます）。"
                       "続きを残すには下部『📼 ログを保存(.jsonl=棋譜)』の**ダウンロード**をご利用ください"
                       "（棋譜.jsonl は脚本を内包＝そのまま読み込めば復帰できます。.rooper.json は seed 基盤"
                       "＝自作脚本は復元できません）。")
            if st.button("この脚本で始める", key="play_custom_start"):
                _sc, _err = _script_from_json(_cj or "")
                if _sc is None:
                    st.error(_err)
                else:
                    # ★A-32：入口は _reset_play_game を通す（対局キーの全消し＋必要分の設定）。
                    _reset_play_game(play_script_obj=_sc,        # 確定脚本を直接使う
                                     play_script="自作脚本", play_seed=0, choices=[])
                    st.session_state["play_mm"] = "heuristic"
                    st.success("自作脚本を読み込みました。")
                    st.rerun()
        # ★A-39（2026-07-19）：「☁ 共有コードから読み込む」は撤去（発行側も同時撤去）。共有コードは
        #   棋譜payload＝replay 復元でドリフトする一方、下の☁トークン（snapshot）が同じ用途を
        #   replay非依存で満たす＝保存方式を2本（☁トークン＋📼ログ）に集約した。
        import cloud as _cloud
        # ★Supabase Phase 1：復帰トークンから「続き」を復元（スナップショット＝replay非依存）。
        if _cloud.enabled():
            _tok_in = st.text_input("☁ 続きを復元（復帰トークン）", key="play_snap_load_tok",
                                    placeholder="復帰トークンを貼り付け",
                                    help="『☁ 続きをクラウドに保存』で発行したトークンから、"
                                         "その局面の続きを再開します。")
            if st.button("このトークンから再開", key="play_snap_load_btn",
                         use_container_width=True) and _tok_in.strip():
                from arena.gamelog import (cloud_payload_day_tail, cloud_payload_to_state,
                                           snapshot_mode_mismatch)
                _pl = _cloud.load_snapshot(_tok_in.strip())
                _mode_note = snapshot_mode_mismatch(_pl, "solo") if _pl else None
                _st0, _why = cloud_payload_to_state(_pl) if _pl else (None, "見つかりません")
                if _mode_note:
                    # ★A-31(b)：脚本家プレイ用のトークンを主人公プレイで**黙って開かない**
                    #   （盤面の意味＝どちらが自分かが変わる）。拒否＋直し方＝fail-ignore。
                    st.error(_mode_note)
                    _cloud.log_event("cloud_snapshot_load", side="protagonist", ok=False,
                                     reason="mode_mismatch")
                elif _st0 is None:
                    st.error(f"復元できませんでした（{_why}）。")
                else:
                    _hc, _ai = cloud_payload_day_tail(_pl)
                    _norm = _normalize_restored_state(_st0)   # ★A-36：壊れトークンを救済（+警告）
                    # ★A-32：復元も「別の対局へ切替える入口」＝_reset_play_game を通す（前の対局の
                    #   残留を持ち込まない）。脚本は snapshot 内蔵＝play_script_obj 機構に乗せる
                    #   （seed再生成では復元できない。自作脚本もこれで復元可＝§2bの制限解消）。
                    # ★A-36：play_snap_state は **snapshot(dict)** で保持＝再実行部が毎rerun 新品stateを
                    #   作り、run_day の破壊的 append による累積（mm 3→6→8・進行不能）を根絶する。
                    _reset_play_game(
                        play_snap_state=_st0.to_snapshot(), play_snap_ai_replay=(_ai or None),
                        choices=list(_hc), play_script_obj=_st0.script,
                        play_script="クラウド（続き）", play_seed=0,
                        # ★A-31：延長ループの復元（総ループ 4+extra が loop_no を下回ると即終局）。
                        play_extra_loops=max(0, int(_st0.loop_no) - 4),
                        play_snap_warn="／".join(x for x in (_why, _norm) if x))
                    _cloud.log_event("cloud_snapshot_load", side="protagonist", ok=True)
                    st.rerun()

    # ★リロード/タブ復元での局面復元：URLの ?g= に対局があれば session_state へ戻す
    #   （新規対局直後は play_script が既にあるので上書きされない）。
    _restore_game_from_url()

    if "play_script" not in st.session_state:
        _qp_set(_GAME_QP_KEY, None)  # 未開始＝URLの残骸を消す
        st.info("← サイドバーで脚本を選び『この設定で開始』を押してください。"
                "あなたは主人公3席を担当し、AI脚本家に挑みます。"
                "🔰メンバー調整は、複雑な能力・特性のキャラが出にくくなります。")
        st.stop()

    # 棋譜（.jsonl）から読み込んだ場合は確定脚本(play_script_obj)を直接使う（seed再生成しない）。
    _obj = st.session_state.get("play_script_obj")
    script = _obj if _obj is not None else _load_script(
        st.session_state["play_script"], st.session_state["play_seed"])
    # ★4ループ打ち切り（ユーザー/AIC 2026-07-11）：脚本の loops に依らず 4 ループで区切る。
    #   「まだループする」を選ぶたび play_extra_loops が増え、その分だけ延長できる。
    from dataclasses import replace as _replace
    _total_loops = 4 + int(st.session_state.get("play_extra_loops", 0))
    script = _replace(script, loops=_total_loops)
    _mm_kind = st.session_state.get("play_mm", "heuristic")
    _show_mm = st.session_state.get("play_show_mm", False)
    # 思考表示ON＋ヒューリスティックmm＝ProbedMastermind（全候補スコア＋勝ち筋分析を記録）。
    #   挙動は本体と完全同一（rng消費も同じ）＝表示のためだけに差し替えても対局は不変。
    if _show_mm and _mm_kind == "heuristic":
        from agents.debug import ProbedMastermind
        _mm_agent = ProbedMastermind(st.session_state["play_seed"])
    else:
        _mm_agent = _make_mastermind(_mm_kind, st.session_state["play_seed"])
    ai_agents = {"mastermind": _mm_agent}
    choices: list[dict] = st.session_state["choices"]
    # ★現局面をURLへ保存（choicesが変わるたびに更新。同値なら書かない＝再実行ループ防止）。
    #   これでリロード・ブラウザ復元でも ?g= から途中局面が戻る。
    #   ★棋譜(play_script_obj)から読み込んだ局面はseed再生成で復元できない＝URL保存しない
    #    （URLに残ると次回 seed から別脚本を組んでしまう。棋譜ファイル自体が持ち運び手段）。
    if _obj is None:
        _qp_set(_GAME_QP_KEY, _encode_game())
    else:
        _qp_set(_GAME_QP_KEY, None)

    # ---------- 現局面まで再実行（未選択の人間決定で中断） ----------
    # ★Supabase Phase 1（2026-07-17）：クラウド復元中は「日境界snapshot＋当日分の再生」で始める
    #   （提案書§1 rev2／§8b裁定2）。play_snap_state が在る間は毎rerunその局面が起点になり、
    #   choices には**その日の分だけ**が積まれる（過去日は snapshot が保持＝replayが1日に縮む）。
    # ★A-36 根治（2026-07-19）：play_snap_state は **snapshot(dict)** で保持し、毎rerun ここで
    #   from_snapshot して**新品の GameState** を起点にする（新規開始が毎rerun GameState(script) を
    #   作り直すのと対称）。以前は復元した GameState を session に持ち回して initial_state に渡して
    #   おり、毎rerun run_day が同一 state に turn_placements/snapshot を破壊的 append＝mm設置が
    #   累積（3→6→8）し snapshot が重複、やがて盤面が壊れて ReplayDesync で進行不能になっていた。
    #   （後方互換：旧 session に GameState が残っていても受ける＝isinstance で分岐。）
    _snap_raw = st.session_state.get("play_snap_state")
    if isinstance(_snap_raw, dict):
        from sim.state import GameState as _GSr
        _snap_state = _GSr.from_snapshot(_snap_raw)
    else:
        _snap_state = _snap_raw          # 後方互換（旧 GameState 直保持）
    _snap_ai = st.session_state.get("play_snap_ai_replay") if _snap_state else None
    # ★UIが日境界を掴むフック（PendingHuman.state は常に mastermind_set 後＝日境界ではない）。
    #   ここで撮った局面が「クラウド保存の起点」になる。
    _day_snaps: dict = {}

    def _grab_day(_s) -> None:
        _day_snaps[(_s.loop_no, _s.day)] = _s.to_snapshot()

    def _autosave_cloud(_state, _log, _finished) -> None:
        """★Phase 2＝案A：フェイズ確定ごとに現局面を game_saves へ upsert（fire-and-forget）。

        材料は手動保存と同一＝**日境界snapshot＋当日分（human_choices＋ai_replay）**（§1 rev2）。
        差分dedupe＝payload の内容ハッシュが前回と同じなら**書かない**（Streamlitは毎操作で
        再実行される＝dedupeしないと同一内容を毎rerun書く）。書込コスト保護はcloud側スレッドで
        check_quota（fail-open）。失敗は握りつぶす＝オートセーブでアプリを壊さない。
        """
        import cloud as _c
        if not _c.enabled() or _finished:
            return
        _k = (_state.loop_no, _state.day)
        _snap = _day_snaps.get(_k)
        if _snap is None:
            return
        try:
            from arena.gamelog import (snapshot_payload_digest, snapshot_to_cloud_payload,
                                       split_day_tail)
            from sim.state import GameState as _GS
            _b, _hc, _ai = split_day_tail(_log, HUMAN_SEATS, _k)
            _pl = snapshot_to_cloud_payload(
                _GS.from_snapshot(_snap), mode="solo",
                app_version=st.session_state.get("app_version", ""),
                human_choices=_hc, ai_replay=_ai, ui={"loop": _k[0], "day": _k[1]})
            _dig = snapshot_payload_digest(_pl)
            if st.session_state.get("play_auto_digest") == _dig:
                return                      # 同じ局面＝書かない（差分dedupe）
            _tok = st.session_state.get("play_auto_token") or _c.new_save_token()
            st.session_state["play_auto_token"] = _tok
            st.session_state["play_auto_digest"] = _dig
            _qp_set(_AUTO_QP_KEY, _tok)     # ★URLは「状態」でなく「ポインタ」＝短いtokenだけ
            _c.autosave_snapshot(_pl, _tok, limit=_autosave_limit())
        except Exception:  # noqa: BLE001  オートセーブの失敗は無害＝アプリを止めない
            pass

    pending: PendingHuman | None = None
    try:
        state, log = play_interactive(
            script, ai_agents, HUMAN_SEATS, choices,
            ai_replay=_snap_ai or st.session_state.get("play_ai_replay"),
            initial_state=_snap_state, on_day_start=_grab_day)
        finished = True
    except PendingHuman as p:
        pending, state, finished, log = p, p.state, False, p.log
    except ReplayDesync as e:
        # ★desyncグレースフル退行（FableA必須条件・2026-07-17）：クラウド復元で「当日分の再生」が
        #   ビルド差でずれたら、**日開始時点へ退行して警告**する。黙って別の局面を出さない
        #   （＝A-27と同じ fail-ignore 原則）。当日分を捨てるだけ＝過去日は snapshot が保持。
        if _snap_state is not None:
            st.session_state["choices"] = []
            st.session_state.pop("play_snap_ai_replay", None)
            st.session_state["play_snap_desync"] = True
            st.rerun()
        # 通常（非クラウド）経路の desync は従来どおり合法だった手まで切り詰める
        st.session_state["choices"] = list(e.valid_choices)
        st.warning("保存されていた選択の一部が現在のルールでは指せないため、"
                   f"{len(e.valid_choices)}手目まで戻して再開します。")
        st.rerun()
    except ValueError as e:
        # ★保存局面の再現に失敗（脚本定義やルールが更新され、保存済みの選択が現局面で
        #   非合法になった等）。壊れた保存を捨てて新規対局に戻す（クラッシュさせない）。
        _qp_set(_GAME_QP_KEY, None)
        for _k in ("play_script", "play_mm", "play_seed", "choices"):
            st.session_state.pop(_k, None)
        st.warning("保存されていた対局を復元できませんでした（脚本の更新などで選択が"
                   f"合致しなくなった可能性があります）。新しく対局を始めてください。\n\n詳細: {e}")
        st.stop()

    # ---------- ★Phase 2：オートセーブ（案A＝フェイズ確定ごと・fire-and-forget・差分dedupe） ----------
    #   ★URL(?g=) は上でそのまま維持（条件1＝Supabase無し環境は現行replayフォールバックのまま）。
    #     クラウドが在る時だけ ?s=<token> を**併記**し、復元は ?s=（snapshot＝厳密）を優先する。
    #   ★rerun 非ブロック：payload組立とhashは同期（軽い）／quota確認とHTTPは別スレッド（cloud側）。
    _autosave_cloud(state, log, finished)

    # ★クラウド復元の通知（1回だけ・try/except の外＝復元後の最初の描画で出す）。
    #   退行＝必ず知らせる（黙って別局面を出さない・FableA必須条件）／ビルド不一致＝情報のみ。
    if st.session_state.pop("play_snap_desync", False):
        st.warning("⚠ アプリの更新により、この日の途中経過を再現できませんでした。"
                   "**この日の頭から再開します**（前日までの盤面はそのまま復元されています）。")
    # ★A-31：URLのトークンが別モード用だった＝黙って落とさず案内する（?g= 側は通常どおり動く）。
    _mode_note = st.session_state.pop("play_snap_mode_note", "")
    if _mode_note:
        st.info(f"ℹ {_mode_note}")
    _snap_warn = st.session_state.pop("play_snap_warn", "")
    if _snap_warn:
        st.info(f"ℹ {_snap_warn}")     # 例＝別ビルドで保存（復元は妨げない・文言は gamelog が持つ）

    # ---------- 4ループ打ち切りの選択画面（AIC ④b・2026-07-11） ----------
    #   4ループ守れず最後の戦いの入口（final_battle_guess）に来たら、まず「まだループする／
    #   最後の戦い／ホーム」を選ばせる（最後の戦いはこの選択後のみ実行＝勝手に始めない）。
    if (not finished and pending is not None
            and pending.decision == "final_battle_guess"
            and not st.session_state.get("play_do_final_battle")):
        from arena.endscreen import go_home, render_outcome_banner
        _nloops = 4 + int(st.session_state.get("play_extra_loops", 0))
        st.title("🎮 主人公としてプレイ")
        render_outcome_banner(
            st, won=False,
            title=f"⏱ {_nloops}ループでは惨劇を回避できませんでした",
            detail="どこかのループを守り切れれば勝ちです。ここからどうしますか？")
        _c1, _c2, _c3 = st.columns(3)
        _c1.button("😖 負けを認めるがまだループする", use_container_width=True,
                   help="この結果を負けとしつつ、もう1ループ挑みます（情報収穫）。",
                   on_click=lambda: st.session_state.update(
                       play_extra_loops=int(st.session_state.get("play_extra_loops", 0)) + 1))
        _c2.button("⚔ ループをやめ最後の戦いに挑む", type="primary",
                   use_container_width=True,
                   help="最後の戦い＝全キャラの役職を宣言。全問正解で逆転勝利・1つでも誤答で敗北。",
                   on_click=lambda: st.session_state.update(play_do_final_battle=True))
        _c3.button("🏠 ホームに戻る", use_container_width=True, on_click=lambda: go_home(st))
        st.stop()

    # ★棋譜ロードの互換警告（②-2・別バージョン保存＝局面がずれうる旨。互換は保証しない方針）。
    if st.session_state.get("play_load_warning"):
        st.warning("⚠ " + str(st.session_state["play_load_warning"]))

    # ---------- 公開シート／進捗 ----------
    # ★D13（2026-07-10）：ループ/日のカウンタは「今見ている盤面の時点」に合わせる。レビュー中
    #   （過去フェイズを閲覧中）は state.day/loop（＝次の決定点＝先の日/ループ）ではなく、
    #   閲覧中スナップショットの loop/day を出す（ターン終了フェイズを見ているのに 2日目/次ループに
    #   カウントが進んでしまう問題の解消）。判定は下の本体カーソルロジックと同じ条件で先読みする。
    _snaps0 = state.phase_snapshots
    _n0 = len(_snaps0)
    if st.session_state.get("_live_choice_len") != len(choices):
        _rev0 = _n0                     # 手が進んだ直後は決定点（＝現在の時点）
    else:
        _rev0 = max(0, min(st.session_state.get("_live_revealed", _n0), _n0))
    if not finished and _rev0 < _n0:
        _disp_loop, _disp_day = _snaps0[_rev0]["loop"], _snaps0[_rev0]["day"]
    else:
        _disp_loop, _disp_day = state.loop_no, state.day
    # ★進捗ヘッダは脚本家プレイと同じコンパクト1行に統一（大きな st.metric は廃止・要望 2026-07-10）。
    #   脚本名は表示ラベル（花名/初心者/ランダム）へ逆引き。ループ/日は D13 同期（閲覧中の時点）。
    _disp_name = {v: k for k, v in _script_labels().items()}.get(
        st.session_state["play_script"], st.session_state["play_script"])
    st.markdown(
        f"**脚本** {_disp_name}（seed {st.session_state['play_seed']}）　"
        f"**ループ** {_disp_loop}/{script.loops}　"
        f"**日** {_disp_day}/{script.days_per_loop}　"
        f"**あなたの手数** {len(choices)}")
    # ★A-37（2026-07-19）：事件名にカードテキストのポップアップ（PC=hover／スマホ=タップ）で
    #   INCIDENT_EFFECTS（効果説明・新規転記なし）を出す。A-34 の popup 機構を board_viz から共用
    #   （CSS-only＝expander不使用＝入れ子問題なし）。効果が無い事件は素の名前（グレースフル退行）。
    import html as _html
    from board_viz import popup_css, popup_span
    from sim.reference import INCIDENT_EFFECTS
    _sched_parts = []
    for i in sorted(script.incidents, key=lambda x: x.day):
        _nm = popup_span(_html.escape(i.name), INCIDENT_EFFECTS.get(i.name, ""))
        _sched_parts.append(f"**{i.day}日目** {_nm}")
    sched = "　／　".join(_sched_parts) or "（公開された予定事件なし）"
    # ★予定事件は重要な公開情報＝グレーのcaptionではなく通常色（白系）ではっきり表示（要望 2026-07-06）
    #   （大物の縄張りは A3 で盤面のすぐ上に移動＝ここには出さない）。
    # ★A-46：popup_css() は1行の <style>…</style>＝直後に本文を同じ行で連結すると CommonMark の
    #   HTMLブロック(type1)の閉じタグ行に本文が巻き込まれ **強調** が生の * のまま表示される。
    #   <style> と本文の間に空行(\n\n)を挟んで本文を独立ブロックにする（** を保持＝テスト非破壊）。
    st.markdown(popup_css() + "\n\n📅 **予定事件（公開シート）**：" + sched,
                unsafe_allow_html=True)

    # ★チュートリアル教材テキスト（選択時のみ・開始前ガイド）。
    _tut_key = st.session_state.get("play_tutorial")
    if _tut_key:
        try:
            from tutorial import format_tutorial_intro, get_tutorial
            with st.expander("📖 チュートリアル：この脚本で学ぶこと", expanded=(len(choices) == 0)):
                st.markdown("\n".join(format_tutorial_intro(get_tutorial(_tut_key))))
        except Exception:  # noqa: BLE001  教材が取れなくても対局は続行
            pass

    def _save_log_button(key: str = "top") -> None:
        # この対局をログ保存＝リプレイビューア（arena/viewer.py・🔁モード）で振り返れる。
        # ★A-39（2026-07-19・保存方式の整理）：保存は**2本**に集約した＝
        #   ☁トークン（主線＝続きから遊ぶ）＋📼ログ（デバッグ/ビューア/ローカル復元）。
        #   撤去＝「💾 対局を保存(.rooper.json)」（seed+選択列＝replay方式でドリフトする。📼ログが
        #   日境界snapshotを内包し上位互換）と「☁ クラウドに保存（共有コード発行）」（棋譜payloadの
        #   共有＝☁トークンが担う）。📂の .rooper.json **読込**は当面残す（段階廃止・既存ファイル救済）。
        if True:
            # ★A-39：ログに「最新の日境界スナップショット」を同梱＝📂読込を replay ではなく
            #   **snapshot 復元**でできるようにする（当日分の決定は split_day_tail で切り出す）。
            #   _day_snaps は on_day_start フックが集めた完全スナップショット（A-36/Phase1 資産）。
            _rs_key = (state.loop_no, state.day)
            _rs = ({"loop": _rs_key[0], "day": _rs_key[1], "snapshot": _day_snaps[_rs_key]}
                   if _day_snaps.get(_rs_key) else None)
            st.download_button(
                "📼 ログを保存（ビューア用）",
                data=game_to_jsonl(script, state, log,
                                   app_version=st.session_state.get("app_version", ""),
                                   resume_snapshot=_rs),
                file_name=f'play_{st.session_state["play_script"]}'
                          f'_seed{st.session_state["play_seed"]}.jsonl',
                mime="application/json",
                help="保存した .jsonl を『🔁 リプレイビューア』モードで開くと日単位で振り返れます。",
                key=f"save_log_btn_{key}",
            )
        # ★A-39：「☁ クラウドに保存（共有コード発行）」は撤去（棋譜payloadの共有＝replay復元で
        #   ドリフトする／☁トークン＝snapshot が上位互換で同じ用途を満たす）。読込側UIも撤去済み。
        import cloud as _cloud
        # ★Supabase Phase 1（2026-07-17）：続きから遊ぶ＝**スナップショット**保存（保存の主線）。
        #   棋譜（決定の列）の復元は replay＝コードが変わるとズレる
        #   （実測：保存済み9件中5件が現行コードで再生不能）。こちらは盤面そのものを保存する。
        #   保存は「日境界snapshot＋その日の分の再生材料」＝§1 rev2（run_day は日の先頭からしか
        #   入れないため。ai_replay まで持つのは、AIが札を再決定すると配置先＝公開情報が変わるから）。
        if _cloud.enabled() and not finished:
            _key = (state.loop_no, state.day)
            _snap = _day_snaps.get(_key)
            if _snap is not None and st.button(
                    "☁ 続きをクラウドに保存", key=f"cloud_snap_{key}", use_container_width=True,
                    help="今の盤面を保存します。発行されるトークンで、別のタブやリロード後でも"
                         "この局面から再開できます。"):
                from arena.gamelog import snapshot_to_cloud_payload, split_day_tail
                from sim.state import GameState as _GS
                _before, _hc, _ai = split_day_tail(log, HUMAN_SEATS, _key)
                _tok = _cloud.save_snapshot(
                    snapshot_to_cloud_payload(
                        _GS.from_snapshot(_snap), mode="solo",
                        app_version=st.session_state.get("app_version", ""),
                        human_choices=_hc, ai_replay=_ai,
                        ui={"loop": _key[0], "day": _key[1]}),
                    slot="manual")
                st.session_state["play_snap_token"] = _tok or ""
                _cloud.log_event("cloud_snapshot_save", side="protagonist", ok=bool(_tok))
                if not _tok:
                    st.warning("クラウド保存に失敗しました（時間をおいて再度お試しください）。")
            if st.session_state.get("play_snap_token"):
                st.success(f"復帰トークン： `{st.session_state['play_snap_token']}`　"
                           "（サイドバーの『☁ 続きを復元』に貼ると、この局面から再開できます）")
                # ★裁定済み（提案書§7 論点1）＝保存データは神視点フル。共有時の注意は必ず出す。
                st.caption("⚠ このデータには**配役・伏せ札の中身（＝答え）が含まれます**。"
                           "他人に渡すとその対局のネタバレになります（自分の再開用です）。")

    # PCの対局中は従来位置（上部）。スマホは下部（下で呼ぶ）。終了時は決着欄でも出す（下記）。
    if not mobile and not finished:
        _save_log_button("top")

    _PHASE_STATE_JP = {
        "mastermind_set": "脚本家行動フェイズ", "protagonist_set": "主人公行動フェイズ",
        "goodwill_ability": "主人公能力フェイズ", "loop_start": "ループ開始",
        "final_battle": "最後の戦い",
    }

    def _decision_phase_label() -> str:
        """現在の人間決定の「〇〇フェイズN人目」ラベル（主人公行動は手番順を付ける）。"""
        ph = _PHASE_STATE_JP.get(state.phase, state.phase)
        if state.phase == "protagonist_set" and str(pending.actor) in ("p1", "p2", "p3"):
            nth = (int(str(pending.actor)[1]) - 1 - state.leader_idx) % 3 + 1
            ph = f"{ph}{nth}人目"
        return ph

    if not finished:
        # ============ 実盤面（今この局面。フェイズを1つずつ進めて観る＝要望 2026-07-06）============
        #   「この手を出す」で次の決定まで一気に飛ばず、自動解決したフェイズ（行動解決→脚本家能力→
        #   主人公能力→事件…）を1つずつ盤面で見せる。カーソル _live_revealed が
        #   phase_snapshots のどこまで見たかを表し、末尾（=決定点）に来たら決定UIを出す。
        snaps = state.phase_snapshots
        n_snaps = len(snaps)
        # 手を進める（choices増加）ときはカーソル据え置き＝新たに解決されたフェイズを順に観る。
        if st.session_state.get("_live_choice_len") != len(choices):
            if "_live_choice_len" not in st.session_state:
                st.session_state["_live_revealed"] = n_snaps  # 開幕は決定点（p1の番）まで
            st.session_state["_live_choice_len"] = len(choices)
        revealed = max(0, min(st.session_state.get("_live_revealed", n_snaps), n_snaps))
        st.session_state["_live_revealed"] = revealed
        review_mode = revealed < n_snaps  # まだ観ていない解決済みフェイズがある

        if review_mode:
            _s = snaps[revealed]
            live_label = f'L{_s["loop"]}・{_s["day"]}日目｜{_s["point"]}'
            live_bjson = board_json_from_snapshot(_s, omniscient=False)
        else:
            live_label = f'▶ L{state.loop_no}・D{state.day}｜{_decision_phase_label()}'
            live_bjson = board_json_from_view(pending.view)  # 脚本家の配置（裏向き）も見える

        # ★スマホの追従ヘッダ：スクロールしても現在地が分かる（要望 2026-07-06）。
        #   ★時点の一致（要望 2026-07-06）：ヘッダ＝盤面のすぐ上の大ラベルと同じ時点を出す
        #   （レビュー中は観覧中のフェイズ、決定中は決定フェイズ）。装飾記号は外して揃える。
        #   position:fixed でビューポート基準に固定（sticky はStreamlitのスクロール構造差で効かない）。
        if mobile:
            _pos = live_label.lstrip("▶ ").replace("日目", "日目")
            # ★z-index は Streamlit ヘッダー(999990)と同値＝DOM順で後のこのバナーが上に描画。
            st.markdown(
                f'<div style="position:fixed;top:2.875rem;left:0;right:0;z-index:999990;'
                f'background:#31333f;color:#fafafa;padding:6px 12px;line-height:1.3;'
                f'font-weight:700;font-size:1.0em;box-shadow:0 2px 6px rgba(0,0,0,.35);">'
                f'📍 {_pos}</div>'
                "<style>div.block-container{padding-top:5.6rem !important;}</style>",
                unsafe_allow_html=True)

        # 日時ラベル（大きめ・盤面のすぐ上）
        st.markdown(
            f'<div style="font-size:1.5em;font-weight:700;margin:0.2em 0 0.1em">'
            f'🕐 {live_label}</div>', unsafe_allow_html=True)
        # ★大物の縄張り（公開情報）は盤面のすぐ上に表示（要望 2026-07-10）。
        if script.oomono_territory and "大物" in script.cast:
            st.markdown(f'🏴 **大物の縄張り**：{script.oomono_territory}')
        # 盤面HTMLは max-width:600px 固定＝左カラムをそれに合わせ、使用済み欄を隣接させる。
        # ★スマホは狭いので盤面付近を重要情報に絞る＝盤面のみ全幅で出し、使用済みカード欄は
        #   決定ボタン（この手を出す／一手戻す）より下へ回す（要望 2026-07-06）。PCは従来どおり隣接。
        html = board_html_from_json(live_bjson, popup=True)  # A-34：実盤面（一人回し）
        if mobile:
            if html:
                st.markdown(html, unsafe_allow_html=True)
        else:
            board_col, used_col = st.columns([1.1, 1], gap="small")
            with board_col:
                if html:
                    st.markdown(html, unsafe_allow_html=True)
            with used_col:
                _render_used_cards(state)

        # ---------- 詰みヒント（レース判定・opt-in／★神視点＝ネタバレ） ----------
        if st.session_state.get("play_hint"):
            from sim.loop_race import analyze_loop, describe_report
            _rep = analyze_loop(state)
            _box = {"mastermind": st.error, "protagonist": st.success,
                    "contested": st.warning}.get(_rep.verdict, st.info)
            _lines = describe_report(_rep)
            _box(_lines[0] if _lines else "（判定なし）")
            if len(_lines) > 1:
                with st.expander("💡 勝ち筋ごとの内訳（⚠配役を使う神視点）", expanded=False):
                    for _l in _lines[1:]:
                        st.markdown(_l)

        # ---------- これまでの経過（フェイズ進行で何が起きたか＝推理の材料） ----------
        #   ★要望 2026-07-06：①各イベントをフェイズ見出しでグルーピング（何フェイズか分かる）
        #   ②レビュー中は「今見ている盤面の時点(hist_len)まで」だけ表示＝盤面と公開情報を一致させる
        #   （先のフェイズの情報を先読みさせない）③行動解決は全カード公開が先頭（sim側で発行順修正）
        #   ④ターン終了フェイズは死者リスト（居なければ「死者なし」）。
        hist = pending.view.get("history", [])
        # 今見ている盤面の時点まで（レビュー中はスナップショットのhist_len、決定中は全部）
        _view_hist_len = (snaps[revealed].get("hist_len", len(hist))
                          if review_mode else len(hist))
        # ★「盤面の時点」順位（テスター指摘 2026-07-11）：能力フェイズ「発動無し」やターン終了の
        #   死者リストの表示可否を hist_len で判定すると、そのフェイズが何も起こさない時に手前の
        #   時点と hist_len が同値になり、行動解決の盤面時点でも「脚本家能力発動無し」等が先読み
        #   表示された。表示中の盤面の point 順で「そのフェイズ後に到達したか」を判定する
        #   （決定中は pending のフェイズ＝直前フェイズ完了時点まで）。
        _POINT_ORD = ["脚本家行動フェイズ前", "主人公行動フェイズ後", "行動解決中",
                      "行動解決フェイズ後", "脚本家能力フェイズ後", "主人公能力フェイズ後",
                      "事件フェイズ後", "ターン終了フェイズ後"]
        _PHASE_TO_PT = {
            "loop_start": "脚本家行動フェイズ前", "loop_start_area": "脚本家行動フェイズ前",
            "mastermind_set": "脚本家行動フェイズ前", "protagonist_set": "主人公行動フェイズ後",
            "action_resolution": "行動解決フェイズ後", "mastermind_ability": "行動解決フェイズ後",
            "goodwill_ability": "脚本家能力フェイズ後", "incident": "主人公能力フェイズ後",
            "final_battle": "ターン終了フェイズ後"}

        def _pt_ord(pt: str) -> int:
            return _POINT_ORD.index(pt) if pt in _POINT_ORD else len(_POINT_ORD)
        _cur_pt_ord = (_pt_ord(snaps[revealed]["point"]) if review_mode
                       else _pt_ord(_PHASE_TO_PT.get(getattr(state, "phase", ""),
                                                     "ターン終了フェイズ後")))
        # ★赤下線(🆕)は「今のフェイズ送りで新たに現れた公開情報だけ」に付ける＝1つ前に観ていた
        #   スナップショット時点(hist_len)より後のイベント。フェイズを進めると前フェイズの赤線は
        #   窓が滑って自然に消える（要望 2026-07-06：赤線はそのフェイズの間だけでよい）。
        #   決定点（レビュー完了後）では最後のスナップショット以降を新規扱い。
        if review_mode:
            _seen_len = snaps[revealed - 1].get("hist_len", 0) if revealed > 0 else 0
        else:
            _seen_len = snaps[-1].get("hist_len", 0) if snaps else 0
        _seen_len = min(_seen_len, len(hist))
        # フェイズ見出し・並び順・イベント整形は共通レンダラ arena/history_view に集約した
        # （経過の表記を脚本家プレイと統一・テスター要望 2026-07-13）。

        def _render_day(lp: int, dy: int) -> int:
            """(lp,dy)の可視イベントを共通レンダラで1回描画（フェイズ見出し〘…〙＋絵文字＋新規🆕）。
            ★経過の表記を脚本家プレイと統一（テスター要望 2026-07-13）：単一 st.markdown で行間を詰め、
            絵文字つき。到達判定 _reached（盤面の point 順・play固有）はそのまま渡す。描画した=1/空=0。"""
            from arena.history_view import render_day_md
            _is_view_day = (lp, dy) == (_view_loop, _view_day)

            def _reached(after_pt: str) -> bool:
                return (_cur_pt_ord >= _pt_ord(after_pt)) if _is_view_day \
                    else ((lp, dy) < (_view_loop, _view_day))
            body = render_day_md(hist, lp, dy, view_len=_view_hist_len,
                                 seen_len=_seen_len, reached=_reached)
            if not body.strip():
                return 0
            st.markdown(body, unsafe_allow_html=True)
            return 1

        # ★経過は情報量が多いのでヘッダを小さめに（大きな subheader は廃止・要望 2026-07-10）。
        st.markdown("**📢 経過（公開情報）**")
        _view_loop, _view_day = ((snaps[revealed]["loop"], snaps[revealed]["day"])
                                 if review_mode else (state.loop_no, state.day))
        _vis = [(i, e) for i, e in enumerate(hist) if i < _view_hist_len]
        # 過去ループは畳む
        prev_loops = sorted({e.get("loop") for i, e in _vis if e.get("loop", 0) < _view_loop})
        if prev_loops:
            with st.expander(f"過去のループ（{len(prev_loops)}ループ分）", expanded=False):
                for lp in prev_loops:
                    for dy in sorted({e.get("day") for i, e in _vis
                                      if e.get("loop") == lp and e.get("day", 0) >= 1}):
                        st.markdown(f"**── L{lp}・{dy}日目 ──**")
                        _render_day(lp, dy)
        # 現ループの各日：観覧中の日は展開、他日は畳む
        cur_days = sorted({e.get("day") for i, e in _vis
                           if e.get("loop") == _view_loop and e.get("day", 0) >= 1})
        for dy in cur_days:
            if dy == _view_day:
                _hd = "🔎 観覧中の日" if review_mode else "🗓 今日"
                st.markdown(f"**{_hd}（L{_view_loop}・D{dy}）の経過**")
                if _render_day(_view_loop, dy) == 0:
                    st.caption("（この時点の公開イベントはまだありません）")
            else:
                with st.expander(f"L{_view_loop}・{dy}日目", expanded=False):
                    _render_day(_view_loop, dy)
        if not cur_days and not prev_loops:
            st.caption("（このループの公開イベントはまだありません）")

        # ★A-6③（2026-07-15）：脚本家AIの思考パネルはページ下部（役職能力表より下）へ移動した
        #   （盤面直下＝経過と決定UIの間は邪魔・ユーザー要望）。観覧中の日(_view_loop/_view_day)を
        #   下部で使うため session_state に退避する（下部は not finished 時のみ描画）。
        st.session_state["_mm_think_ctx"] = (_view_loop, _view_day)

        # ---------- 操作：フェイズを進める／人間の決定を出す ----------
        def _undo_move() -> None:
            if st.session_state["choices"]:
                st.session_state["choices"].pop()
            for _k in ("move_pick", "tgt_pick", "card_pick"):
                st.session_state.pop(_k, None)

        def _step_phase(delta: int) -> None:
            cur = st.session_state.get("_live_revealed", 0)
            st.session_state["_live_revealed"] = max(0, cur + delta)

        if review_mode:
            # 自動で解決したフェイズを1つずつ観る（決定はまだ出さない）。
            # ★主人公行動フェイズ後（6枚裏向き）を見ている時は、次へ進む＝6枚を表にする
            #   ＝「カードを開く」ボタン（ユーザー要望 2026-07-10）。それ以外は「フェイズを進める」。
            _at_set = snaps[revealed]["point"] == "主人公行動フェイズ後"
            _adv_label = "🃏 カードを開く" if _at_set else "▶ フェイズを進める"
            st.caption(f"⏩ このターンで解決したフェイズを確認中（{revealed + 1}/{n_snaps}）。"
                       + ("『カードを開く』で6枚を表にします。"
                          if _at_set else "『フェイズを進める』で次のフェイズへ。")
                       + "末尾まで進むと次の決定になります。")
            b1, b2 = st.columns(2)
            b1.button(_adv_label, type="primary", use_container_width=True,
                      on_click=_step_phase, args=(1,))
            b2.button("◀ フェイズを1つ戻す", use_container_width=True,
                      disabled=(revealed == 0), on_click=_step_phase, args=(-1,))
        else:
            # 人間の決定（時点は大ラベルに出ているので、ここは「誰の番か」だけ）
            st.markdown(f'**あなた（{pending.actor}）の番です。**')
            # ★コーチ（opt-in）：主人公AIの推奨手＋理由＋開幕方針。公開情報のみ＝ネタバレなし。
            if st.session_state.get("play_coach"):
                try:
                    from coach import advise, format_advice, strategy_tips
                    # 自分の他席がこのターン既に置いたカード（暗躍禁止2枚=自滅の回避に渡す）。
                    _team = [p["card"] for p in state.turn_placements
                             if p.get("owner") in ("p1", "p2", "p3")]
                    with st.expander("🎓 コーチの助言", expanded=True):
                        _adv = advise(pending.view, pending.decision, list(pending.options),
                                      team_cards_this_turn=_team)
                        st.markdown("\n".join(format_advice(_adv)))
                        _tips = strategy_tips(pending.view, state.script.set_name)
                        if _tips:
                            st.caption("方針（開幕の考え方）")
                            for _t in _tips:
                                st.markdown(f"- {_t}")
                except Exception as _e:  # noqa: BLE001  コーチ不能でも対局は続行
                    st.caption(f"（コーチ評価を取得できませんでした: {_e}）")
            options_now = list(pending.options)
            if pending.decision == "set_card":
                # ★2段階選択：配置先 → カード（ユーザー要望 2026-07-04）。
                #   スマホは2つのselectboxを横並び2カラム（＝各ラベル1行＋widget1行の計2行）に。
                targets: list[str] = []
                for o in options_now:
                    t = f'{o["target"]}（ボード）' if o["target_kind"] == "board" else o["target"]
                    if t not in targets:
                        targets.append(t)
                if st.session_state.get("tgt_pick") not in targets:
                    st.session_state["tgt_pick"] = targets[0]
                # ★配置先とカードは常に横並び2列（脚本家プレイと統一・ユーザー要望 2026-07-10）。
                _d1, _d2 = st.columns(2)
                tgt_label = _d1.selectbox("① 配置先", targets, key="tgt_pick")
                tgt_name = tgt_label.replace("（ボード）", "")
                tgt_kind = "board" if tgt_label.endswith("（ボード）") else "character"
                cards = [o["card"] for o in options_now
                         if o["target"] == tgt_name and o["target_kind"] == tgt_kind]
                if not cards:
                    # ★A-19同型ガード：widget状態競合で tgt_pick が旧局面のラベルを返すと
                    #   cards が空＝cards[0] でIndexError（脚本家プレイの本番クラッシュと同クラス）。
                    #   先頭対象へローカルにフォールバック（次のrerunで1128行のガードが正す）。
                    tgt_label = targets[0]
                    tgt_name = tgt_label.replace("（ボード）", "")
                    tgt_kind = "board" if tgt_label.endswith("（ボード）") else "character"
                    cards = [o["card"] for o in options_now
                             if o["target"] == tgt_name and o["target_kind"] == tgt_kind]
                if st.session_state.get("card_pick") not in cards:
                    st.session_state["card_pick"] = cards[0]
                _d2.selectbox("② カード", cards, key="card_pick")

                def _commit_move() -> None:
                    t = st.session_state["tgt_pick"].replace("（ボード）", "")
                    k = "board" if st.session_state["tgt_pick"].endswith("（ボード）") else "character"
                    c = st.session_state["card_pick"]
                    chosen = next((o for o in options_now
                                   if o["target"] == t and o["target_kind"] == k
                                   and o["card"] == c), None)
                    # ★A-19同型ガード：stale組合せ（widget状態競合）は無視＝何も置かず再描画に任せる
                    #   （StopIterationでon_click内クラッシュしていた穴）。
                    if chosen is not None:
                        st.session_state["choices"].append(chosen)

                st.button("この手を出す", type="primary", on_click=_commit_move)
            elif pending.decision == "goodwill_ability":
                # ★2段階選択：発動能力（人＋能力）→ 対象（ユーザー要望 2026-07-10）。
                #   例）①「〈女子学生〉『学生の不安除去』」 → ②「巫女」。パスは対象なし。
                _gw_groups = group_gw_options(options_now)
                _ab_keys = list(_gw_groups)
                if st.session_state.get("gw_ability_pick") not in _ab_keys:
                    st.session_state["gw_ability_pick"] = _ab_keys[0]
                _g1, _g2 = st.columns(2) if mobile else (st, st)
                _ab_pick = _g1.selectbox("① 発動能力を選ぶ", _ab_keys, key="gw_ability_pick")
                # ★A-19：ホット更新(Updated app!)や連打の widget 状態競合で selectbox が旧局面の
                #   ラベルを返すと _gw_groups[_ab_pick] が KeyError（本番の脚本家プレイ同型）＝
                #   描画はグループ先頭で代替（次のrerunで上のガードが session_state を正す）。
                #   ※代替するのは「描画」だけ＝実際に打つ手は _commit_gw が stale を拒否する（A-27）。
                _sel = _gw_groups.get(_ab_pick) or _gw_groups[_ab_keys[0]]
                # ★A-27（2026-07-16）：②対象の widget キーを**能力ごとに分離**する。共通キー
                #   （旧 "gw_target_pick"）だと能力を切替えても前の能力の対象値が widget 状態に残り、
                #   別能力の対象（例：『暗躍+1（キャラ/ボード）』の「学校」）が『任意キャラに不安+1』の
                #   選択として生き残る＝本番のA-27（学校を選んだのに異世界人に不安）。キーを分ければ
                #   能力をまたぐ持ち越しは構造的に起こりえない。
                _t_key = f"gw_target_pick::{_ab_pick}"
                if len(_sel) > 1:                       # 対象が複数＝②で選ぶ
                    _t_labels = [str(o.get("target")) for o in _sel]
                    if st.session_state.get(_t_key) not in _t_labels:
                        st.session_state[_t_key] = _t_labels[0]
                    _g2.selectbox("② 対象を選ぶ", _t_labels, key=_t_key)
                if st.session_state.pop("_gw_stale_click", False):
                    st.warning("選択が古くなっていたため、この操作は取り消しました（誤った手を打たない"
                               "ための安全装置です）。もう一度選び直してください。")

                def _commit_gw() -> None:
                    # ★A-27：on_click は再描画前に走る＝widget 状態が stale なことがある。その場合は
                    #   **クリックを無視**する（＝何も置かない・次のrerunでガードが状態を正す）。
                    #   A-19 はここで「グループ先頭」へ落としており、ユーザーが選んでいない手を黙って
                    #   打っていた（本番実害＝A-27）。クラッシュ根絶は None 返しでも達成できる。
                    ab = st.session_state.get("gw_ability_pick")
                    chosen = resolve_gw_choice(_gw_groups, ab,
                                               st.session_state.get(f"gw_target_pick::{ab}"))
                    if chosen is None:
                        st.session_state["_gw_stale_click"] = True
                        return
                    st.session_state["choices"].append(chosen)

                st.button("この能力を発動", type="primary", on_click=_commit_gw)
            else:
                if pending.decision == "final_battle_guess":
                    st.warning(f'⚔ 最後の戦い！ 〈{pending.options[0]["character"]}〉の役職を宣言してください。'
                               "全キャラ正解で逆転勝利、1つでも誤答で敗北です。")

                def _opt_label(o: dict) -> str:
                    if "ability" in o and "target" in o:
                        return f'{o["target"]} ← 〈{o["character"]}〉『{o["ability"]}』'
                    return describe_choice(o)

                labels = [f"{i + 1}. {_opt_label(o)}" for i, o in enumerate(options_now)]
                if st.session_state.get("move_pick") not in labels:
                    st.session_state["move_pick"] = labels[0]
                st.selectbox("出す手を選ぶ", labels, key="move_pick")

                def _commit_move() -> None:
                    lbl = st.session_state.get("move_pick", labels[0])
                    # ★A-19：stale ラベル（widget状態競合）は labels.index で ValueError＝無視して
                    #   何も置かず再描画に任せる（次のrerunで 1213 のガードが正す）。
                    if lbl in labels:
                        st.session_state["choices"].append(options_now[labels.index(lbl)])

                st.button("この手を出す", type="primary", on_click=_commit_move)

            # 戻す：一手戻す（カードの取り消し）／フェイズを見直す（直前フェイズへ）
            u1, u2 = st.columns(2)
            u1.button("← 一手戻す", use_container_width=True, disabled=(not choices),
                      on_click=_undo_move)
            u2.button("◀ フェイズを見直す", use_container_width=True,
                      disabled=(n_snaps == 0), on_click=_step_phase, args=(-1,))

        # ★スマホ：使用済みカード欄は決定ボタン群より下へ（盤面付近は重要情報に絞る）
        if mobile:
            _render_used_cards(state)

        # ★コーチ：ループの振り返り（判明した情報＝coach.review_loop・opt-in・プランⅢ）。
        #   負けても情報は残る（KP確定・拒否＝自白・敗北ボード候補）＝惨劇の学習の核心を体験させる。
        if st.session_state.get("play_coach") and state.loop_no > 1:
            try:
                from coach import review_loop
                with st.expander("🎓 ループの振り返り（これまでに分かったこと）", expanded=False):
                    for _lp in range(1, state.loop_no):
                        _rv = review_loop(state.history, _lp, state.script.set_name)
                        if _rv:          # review_loop は整形済みmarkdown（見出し＋箇条書き）を返す
                            st.markdown("\n".join(_rv))
            except Exception as _e:  # noqa: BLE001  振り返り不能でも対局は続行
                st.caption(f"（振り返りを取得できませんでした: {_e}）")

        # ============ ログ閲覧盤面（過去フェイズを自由に前後・独立カーソル）============
        st.divider()
        st.markdown("**📼 ログ閲覧盤面（過去フェイズの振り返り）**")
        log_frames = [(f'L{s["loop"]}・{s["day"]}日目｜{s["point"]}',
                       board_json_from_snapshot(s, omniscient=False)) for s in snaps]
        log_frames.append((f'▶ 現在（L{state.loop_no}・D{state.day}）',
                           board_json_from_view(pending.view)))
        nf = len(log_frames)
        # ★D11（2026-07-10）：ログ閲覧盤面は「上部の表示盤面」より先へは進めない＝カーソル上限を
        #   revealed（上部盤面の位置）にクランプ（次の決定まで一気に飛ぶのを防ぐ）。log_frames の
        #   index は snapshots 0..n_snaps-1 ＋『現在』が n_snaps＝revealed の取りうる範囲と一致。
        lmax = max(0, min(revealed, nf - 1))
        if st.session_state.get("_log_frame_count") != nf:  # フレーム増加時は表示盤面の位置へ
            st.session_state["_log_frame_count"] = nf
            st.session_state["log_frame_idx"] = lmax
        lidx = max(0, min(st.session_state.get("log_frame_idx", lmax), lmax))
        llabel, lbjson = log_frames[lidx]
        # スマホはラベルを別行に（3ボタン＋長いラベルの横並びは崩れるため）
        lc = st.columns([1, 1, 1] if mobile else [1, 1, 1, 3])
        lc[0].button("◀ 前", use_container_width=True, disabled=(lidx == 0),
                     on_click=lambda lidx=lidx: st.session_state.update(log_frame_idx=lidx - 1))
        lc[1].button("次 ▶", use_container_width=True, disabled=(lidx >= lmax),
                     on_click=lambda lidx=lidx: st.session_state.update(log_frame_idx=lidx + 1))
        lc[2].button("表示盤面へ", use_container_width=True, disabled=(lidx >= lmax),
                     on_click=lambda lmax=lmax: st.session_state.update(log_frame_idx=lmax))
        if mobile:
            st.markdown(f'**{llabel}**（{lidx + 1}/{lmax + 1}）')
        else:
            lc[3].markdown(f'**{llabel}**（{lidx + 1}/{lmax + 1}）')
        lhtml = board_html_from_json(lbjson, popup=True)  # A-34：ログ閲覧盤面
        if lhtml:
            # ★過去フレーム（＝今ではない盤面）を退色させて「今ではない」を一目で示す。lmax＝上部
            #   表示盤面の位置なので、それより手前だけを退色する。
            # ★A-47：旧実装は board ラッパに filter:sepia を掛けていたが、filter は新しい stacking
            #   context を作り＋子孫（キャラ説明ポップアップ .rp-cardtip）まで退色させる＝ポップアップが
            #   半透明に見え z-index:9999 が効かず下のテキストと被った（ログ閲覧盤面・ユーザー報告）。
            #   filter/opacity（どちらも stacking context を作り子孫を巻き込む）を使わず、
            #   pointer-events:none の半透明オーバーレイで盤面だけを退色する（オーバーレイは board の
            #   兄弟＝ポップアップの祖先でない・ラッパは position:relative かつ z-index なし＝stacking
            #   context を作らない＝ポップアップの z-index:9999 は根まで効く）。
            if lidx < lmax:
                lhtml = ('<div style="position:relative;background:rgba(150,110,60,0.14);'
                         'border-radius:8px;padding:5px;">' + lhtml +
                         '<div style="position:absolute;inset:0;border-radius:8px;'
                         'background:rgba(150,110,60,0.34);pointer-events:none;"></div></div>')
            st.markdown(lhtml, unsafe_allow_html=True)
    else:
        # ---------- 決着（統一デザイン：勝敗バナー＋答え合わせ＋選択） ----------
        from arena.endscreen import go_home, render_outcome_banner
        _won = state.winner == "protagonist"
        # 共通イベント：終局（1対局1回・メタのみ）。新規対局で _play_end_logged はクリアされる。
        if not st.session_state.get("_play_end_logged"):
            st.session_state["_play_end_logged"] = True
            import cloud as _cloud
            _cloud.log_event("play_end", side="protagonist", result=state.winner,
                             loops=state.loop_no,
                             app_version=st.session_state.get("app_version", ""))
        render_outcome_banner(
            st, won=_won,
            title=("🎉 あなた（主人公）の勝利！" if _won else "💀 脚本家の勝利…"),
            detail=("どこかのループで惨劇を回避しました。" if _won
                    else "惨劇を回避できませんでした。"))
        # ★A-8＋A-7(2)（2026-07-14）：勝敗の直後に「最終ターンの経過＋最終盤面」を見せる。
        #   最終手（ご神木の特性移動等）を出した瞬間にエンディングへ直行し、その手の結果も
        #   途中フェイズ（事件→ターン終了→ループ終了）の経過も確認できなかった（ユーザー報告 A-7）。
        #   game_over時は if not finished のフェイズ送りが丸ごとスキップされるため、ここで最終日の
        #   経過を共通レンダラ（脚本家プレイと同形式）で1回描き、末尾の確定盤面を添える（純描画）。
        _end_snaps = state.phase_snapshots
        if _end_snaps:
            _es = _end_snaps[-1]
            with st.expander(f"📽 最終ターンの経過（L{_es['loop']}・{_es['day']}日目）",
                             expanded=True):
                from arena.history_view import render_day_md
                _ehist = state.history
                _ebody = render_day_md(_ehist, _es["loop"], _es["day"],
                                       view_len=len(_ehist), seen_len=len(_ehist))
                if _ebody.strip():
                    st.markdown(_ebody, unsafe_allow_html=True)
                st.markdown(f'**最終盤面（{_es["point"]}）**')
                st.markdown(
                    board_html_from_json(board_json_from_snapshot(_es, omniscient=False),
                                         popup=True),  # A-34：最終盤面
                    unsafe_allow_html=True)
        # ★コーチ：終了後の全ループ振り返り（答え合わせ＝真実開示の前に、公開情報から何が
        #   読み取れたかを提示＝プランⅢ・opt-in）。負けても情報が残ることを実感させる学習の核心。
        if st.session_state.get("play_coach"):
            try:
                from coach import review_loop
                with st.expander("🎓 各ループで分かったこと（公開情報からの振り返り）",
                                 expanded=not _won):
                    for _lp in range(1, state.loop_no + 1):
                        _rv = review_loop(state.history, _lp, script.set_name)
                        if _rv:          # review_loop は整形済みmarkdown（見出し＋箇条書き）
                            st.markdown("\n".join(_rv))
            except Exception:  # noqa: BLE001  振り返り不能でも決着表示は続行
                pass
        with st.expander("🗝 答え合わせ（配役・犯人・ルールを開示）", expanded=True):
            st.write(f'**ルールY**: {script.rule_y}　**ルールX**: {" / ".join(script.rule_xs)}')
            _table_no_index([{"キャラ": n, "役職": script.role_of(n)} for n in script.cast])
            _table_no_index([{"日": i.day, "事件": i.name, "犯人": i.culprit}
                             for i in script.incidents])
        # ★終了後もログを保存できるように（PC/スマホ共通。要望 2026-07-06）。
        _save_log_button("end")
        _e1, _e2 = st.columns(2)
        if _e1.button("🔄 もう一度（同じ脚本）", use_container_width=True):
            st.session_state["choices"] = []
            st.session_state["play_extra_loops"] = 0
            st.session_state["play_do_final_battle"] = False
            st.rerun()
        _e2.button("🏠 ホームに戻る", use_container_width=True, on_click=lambda: go_home(st))

    # ============================================================================
    # ページ下部＝リファレンス（カードテキスト・事件効果・ルール・役職）。役職ネタバレなし。
    # ★expanderは再実行で閉じることがあるため、キー付きtoggleで開閉状態を保持
    #   （一度開いたらユーザーが閉じるまで開きっぱなし＝2026-07-05要望）。
    # ============================================================================
    st.divider()

    if st.toggle("📇 登場キャラクター一覧（カードテキスト）", key="ref_chars"):
        render_char_reference(script, mobile)

    if st.toggle("💥 予定事件の効果", key="ref_incidents"):
        render_incident_reference(script, memo=True)

    if st.toggle(f"📖 ルール一覧（{script.set_name}・確有/確無チェック付き）", key="ref_rules"):
        render_rule_reference(script, aids=True)

    if st.toggle(f"🎭 役職能力表（{script.set_name}）", key="ref_roles"):
        render_role_reference(script, aids=True)

    # ★A-6③：脚本家AIの思考パネル（#6/#7・デバッグ・⚠神視点）はここ＝役職能力表より下に出す
    #   （盤面直下は邪魔・ユーザー要望）。決定中（not finished）のみ・観覧中の日の手番を出す。
    _mm_ctx = st.session_state.get("_mm_think_ctx")
    if _show_mm and not finished and _mm_ctx:
        _tl, _td = _mm_ctx
        _recs = getattr(_mm_agent, "records", None)
        with st.expander("🧠 脚本家AIの思考（この日の手番・デバッグ／⚠神視点）", expanded=True):
            if _recs is None:
                st.caption("（LLM脚本家では思考ダンプは出せません。ヒューリスティックAIで"
                           "対局してください。）")
            else:
                st.caption("AI脚本家がどの勝ち筋にどれだけ資金を出し、各手を何点で評価したか"
                           "（★＝実際に伏せた手）。⚠配役・勝ち筋を使う神視点＝ネタバレ注意。")
                from agents.debug import mastermind_mind_md
                st.markdown(mastermind_mind_md(_recs, loop=_tl, day=_td))

    # ★スマホ：ログ保存ボタンはページ最下部に置く（上部＝盤面付近を空ける・要望 2026-07-06）
    if mobile and not finished:
        st.divider()
        _save_log_button("bottom")


if __name__ == "__main__":
    # standalone起動は wide。session_state["_force_mobile_render"] で縦積み表示も検証できる
    # （app.py 経由では render_play(mobile=_MOBILE) が渡る＝本番のモバイル判定はapp.py側）。
    _m = bool(st.session_state.get("_force_mobile_render", False))
    st.set_page_config(page_title="惨劇RoopeR 人間 vs AI",
                       layout="centered" if _m else "wide")
    render_play(mobile=_m)
