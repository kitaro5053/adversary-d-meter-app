"""盤面ビルダー（GUIで盤面を組んでエンジンに直接裁定させる＝翻訳を通さない）。

翻訳ブレをゼロにするモード。特に多キャラ全ボード（PA7/PA12型）はLLM翻訳が苦手なので、
表(st.data_editor)で盤面を組んで adjudicate に直接渡す。結果は裁定テキスト＋盤面ビジュアル、
さらに「scenarios.py 用の engine_input JSON」も出力（テスト設問化に流用できる）。

app.py 専用（streamlit依存）。skill/ には含めない。
"""

from __future__ import annotations

import datetime
import json
import math

import pandas as pd
import streamlit as st

from board_viz import board_html_from_json, board_html_from_outcome
from engine.board import AREAS
from engine.data import (
    CHARACTER_FORBIDDEN,
    GOODWILL_ABILITIES,
    ROLE_CLAUSE_ABILITY,
    initial_area_of,
)
from engine.models import MASTERMIND_CARDS, PROTAGONIST_CARDS
from engine.orchestrate import adjudicate
from engine.render import render_verdict
from engine.translate import TranslationError

_AREAS = list(AREAS.keys())
_ROLES = ["パーソン"] + [r for r in ROLE_CLAUSE_ABILITY if r != "パーソン"]
_CHAR_NAMES = list(CHARACTER_FORBIDDEN.keys())

# ラベル→phase。selectbox は文字列選択肢にする（tuple選択肢はテスト等で扱いにくいため）。
# 並びは1ターン9フェイズの時系列順（4行動解決→5脚本家能力→6主人公能力→7事件→9ターン終了）。
_PHASE_LABELS = {
    "④ 行動解決（暗躍/移動/不安/友好/移動禁止）": "action_resolution",
    "⑤ 脚本家能力フェイズ・不安": "mastermind_unrest",
    "⑤ 脚本家能力フェイズ・暗躍": "mastermind_anyaku",
    "⑥ 主人公能力（友好能力の使用可否・拒否可否）": "goodwill_ability",
    "⑦ 事件（発生判定）": "incident",
    "⑨ ターン終了（死亡判定：シリアルキラー等）": "turn_end",
    "⑨ ループ終了（タイミング裁定）": "loop_end",
}


# --- 値の正規化（data_editor の新規行は空セルが NaN になるため） ---
def _s(v, default: str = "") -> str:
    return v.strip() if isinstance(v, str) and v.strip() else default


def _i(v, default: int = 0) -> int:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _b(v, default: bool = True) -> bool:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return bool(v)


def _empty_df(cols: dict) -> pd.DataFrame:
    """列名→dtype の定義から 0行の DataFrame を作る（data_editor に列スキーマを与える）。"""
    return pd.DataFrame({name: pd.Series([], dtype=dt) for name, dt in cols.items()})


def _char_editor(key: str, with_anyaku: bool = False):
    """キャラ表を編集させ、非空行の list[dict] を返す。

    with_anyaku=True なら暗躍カウンター列も出す（ターン終了の死亡判定で使う）。
    """
    cfg = {
        "name": st.column_config.SelectboxColumn("キャラ", options=_CHAR_NAMES, required=False),
        "role": st.column_config.SelectboxColumn("配役", options=_ROLES, default="パーソン"),
        "area": st.column_config.SelectboxColumn("エリア", options=_AREAS),
        "alive": st.column_config.CheckboxColumn("生存", default=True),
        "goodwill": st.column_config.NumberColumn("友好", min_value=0, default=0, step=1),
        "unrest": st.column_config.NumberColumn("不安", min_value=0, default=0, step=1),
    }
    coldef = {"name": "object", "role": "object", "area": "object",
              "alive": "bool", "goodwill": "int64", "unrest": "int64"}
    if with_anyaku:
        cfg["anyaku"] = st.column_config.NumberColumn("暗躍", min_value=0, default=0, step=1)
        cfg["guard"] = st.column_config.NumberColumn("護衛", min_value=0, default=0, step=1)
        coldef["anyaku"] = "int64"
        coldef["guard"] = "int64"
    # ★ 空リストではなく「列型を定義した0行DataFrame」を渡す（空リストだと列が確定せず入力不能）。
    #   key付きなので追加行は session_state[key] に保持され、返り値に反映される。
    edited = st.data_editor(
        _empty_df(coldef), column_config=cfg, num_rows="dynamic",
        use_container_width=True, key=key,
    )
    out = []
    for r in edited.to_dict("records"):
        name = _s(r.get("name"))
        if not name:
            continue
        # エリア空欄は「そのキャラの初期エリア→無ければ学校」で補完（行動解決はarea必須のため）。
        area = _s(r.get("area")) or initial_area_of(name) or "学校"
        row = {"name": name, "role": _s(r.get("role"), "パーソン"),
               "area": area, "alive": _b(r.get("alive")),
               "goodwill": _i(r.get("goodwill")), "unrest": _i(r.get("unrest"))}
        if with_anyaku:
            row["anyaku"] = _i(r.get("anyaku"))
            row["guard"] = _i(r.get("guard"))
        out.append(row)
    return out


def _placement_editor(key: str, target_opts: list[str], owner_fixed: str | None):
    """カード配置の表。owner_fixed=脚本家名なら脚本家カードのみ・owner列なし／
    None なら主人公(p1..)選択＋主人公カードのみ。対象はキャラ名/エリア名の選択式。"""
    cards = sorted(MASTERMIND_CARDS) if owner_fixed else sorted(PROTAGONIST_CARDS)
    cfg: dict = {}
    coldef: dict = {}
    if owner_fixed is None:
        cfg["owner"] = st.column_config.SelectboxColumn(
            "主人公", options=["p1", "p2", "p3"], default="p1")
        coldef["owner"] = "object"
    cfg["card"] = st.column_config.SelectboxColumn("カード", options=cards)
    cfg["target"] = st.column_config.SelectboxColumn("対象", options=target_opts)
    coldef["card"] = "object"
    coldef["target"] = "object"
    edited = st.data_editor(
        _empty_df(coldef), column_config=cfg, num_rows="dynamic",
        use_container_width=True, key=key,
    )
    out = []
    for r in edited.to_dict("records"):
        card, target = _s(r.get("card")), _s(r.get("target"))
        if not card or not target:
            continue
        out.append({"owner": owner_fixed or _s(r.get("owner"), "p1"), "card": card,
                    "target": target, "target_kind": "board" if target in AREAS else "character"})
    return out


def _target_answer(outcome, name: str) -> str:
    """行動解決後の、指定対象（ボード/キャラ）の要点を1行にまとめる。"""
    adj = outcome.adjudication
    if adj is None:
        return f"**{name}**：—"
    parts: list[str] = []
    if name in AREAS:
        tr = adj.targets.get(name)
        parts.append(f"暗躍 +{tr.delta if tr else 0}")
    else:
        if name in adj.moves:
            note = ("・移動不成立で留まる" if name in adj.blocked_moves else
                    "・移動禁止で留まる" if name in adj.move_banned else "")
            parts.append(f"位置 {adj.moves[name]}{note}")
        tr = adj.targets.get(name)
        if tr is not None and tr.target_kind == "character":
            parts.append(f"暗躍 +{tr.delta}")
        if name in adj.unrest:
            parts.append(f"不安 {adj.unrest[name].final}")
        if name in adj.goodwill:
            parts.append(f"友好 {adj.goodwill[name].final}")
    return f"**{name}**：" + "／".join(parts) if parts else f"**{name}**：変化なし"


def _record_result(data: dict, ask: list[str], app_version: str, build_info: str) -> int:
    """組んだ盤面を裁定し、テストログ(qa_log)に1件記録して index を返す。

    チャットの回答と同じ entry 形式で記録＝ダウンロードログ・フィードバックUIをそのまま使える。
    """
    try:
        outcome = adjudicate(data)
        verdict = render_verdict(outcome)
        engaged = True
        board_out = board_html_from_outcome(outcome, data)
        parts: list[str] = []
        if ask and outcome.adjudication is not None:
            parts.append("**🎯 問うた対象:**")
            parts += ["- " + _target_answer(outcome, n) for n in ask]
        answer_md = ("\n".join(parts) + "\n\n" if parts else "") + verdict
        err = None
    except TranslationError as e:
        verdict, board_out, engaged = "", None, False
        answer_md = f"入力が不正です: {e}"
        err = str(e)
    except Exception as e:  # noqa: BLE001
        verdict, board_out, engaged = "", None, False
        answer_md = f"裁定エラー: {type(e).__name__}: {e}"
        err = answer_md

    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "question": "【盤面ビルダー】\n" + json.dumps(data, ensure_ascii=False),
        "model": "盤面ビルダー（翻訳なし・直接裁定）",
        "app_version": app_version, "build": build_info,
        "engaged": engaged, "engine_error": err,
        "translation": data, "translation_raw": "",
        "verdict": verdict,
        "board_in": board_html_from_json(data),
        "board_out": board_out,
        "answer": answer_md,
        "feedback": None, "fb_tags": [], "fb_comment": "",
        "source": "builder",
    }
    log = st.session_state.setdefault("qa_log", [])
    log.append(entry)
    return len(log) - 1


def _display_result(idx: int, feedback_ui) -> None:
    """記録済みの盤面ビルダー結果を再描画（毎回呼ぶ。フィードバックボタン付き）。"""
    log = st.session_state.get("qa_log", [])
    if not (isinstance(idx, int) and 0 <= idx < len(log)):
        return
    entry = log[idx]
    st.markdown("#### ⚖️ 裁定結果")
    st.caption("⚙️ エンジン裁定（決定的・翻訳を通していません）")
    bi, bo = entry.get("board_in"), entry.get("board_out")
    if bi or bo:
        c1, c2 = st.columns(2)
        if bi:
            c1.caption("📥 入力盤面")
            c1.markdown(bi, unsafe_allow_html=True)
        if bo:
            c2.caption("📤 裁定後の盤面")
            c2.markdown(bo, unsafe_allow_html=True)
    st.markdown(entry["answer"])
    # ★ビルダーは app.py 側で expander 内に描画されるため、ここで expander を入れ子にできない
    #   （Streamlitは入れ子expander禁止）。JSONはチェックボックスで開閉する。
    if st.checkbox("📋 この盤面のJSONを表示（scenarios.py の engine_input に流用可）",
                   key=f"bld_json{idx}"):
        st.code(json.dumps(entry["translation"], ensure_ascii=False, indent=2), language="json")
    # チャットと同じ Good/Bad/Bug を付ける（テストログに記録される）。
    if feedback_ui is not None:
        feedback_ui(idx)


def render_builder(feedback_ui=None, app_version: str = "", build_info: str = "") -> None:
    """盤面ビルダー本体（app.py の expander から呼ぶ）。

    feedback_ui: 回答1件へのGood/Bad/Bug UI（app.py側の関数）。結果に付けてテストログに記録する。
    app_version/build_info: ログエントリのメタ情報。
    """
    st.caption(
        "盤面を表で組んで、翻訳を通さずエンジンに直接裁定させます（翻訳ブレゼロ）。"
        "多キャラ・全ボードの質問はこちらが確実です。結果には 👍/👎/🐛 が付き、テストログに記録されます。"
    )
    # .get フォールバック：ラベル改名でセッションに古い値が残っても落ちないように。
    phase = _PHASE_LABELS.get(
        st.selectbox("フェイズ", list(_PHASE_LABELS), key="bld_phase"), "action_resolution")

    # ループ終了は盤面不要（フラグのみ）。
    if phase == "loop_end":
        st.markdown("**状況（当てはまるものを選ぶ）**")
        ped = st.checkbox("主人公が死亡した", key="bld_ped")
        lee = st.checkbox("ループ終了効果が発生した（キーパーソン死亡等）", key="bld_lee")
        fde = st.checkbox("最終日のターン終了フェイズである", key="bld_fde")
        dcm = st.selectbox("敗北条件は成立しているか（脚本依存）",
                           ["不明（要確認）", "成立している", "成立していない"], key="bld_dcm")
        if st.button("⚖️ 裁定する", key="bld_run_loop"):
            le = {"protagonist_death": ped, "loop_end_effect": lee, "is_final_day_end": fde}
            if dcm != "不明（要確認）":
                le["defeat_condition_met"] = (dcm == "成立している")
            st.session_state["bld_last_idx"] = _record_result(
                {"set": "BTX", "phase": "loop_end",
                 "question_target": {"name": "ループ終了", "kind": "phase"},
                 "loop_end": le},
                [], app_version, build_info)
        _display_result(st.session_state.get("bld_last_idx"), feedback_ui)
        return

    # ターン終了（死亡判定）：キャラ表（暗躍列付き）＋最終日/主人公不死フラグ。配置カードは不要。
    if phase == "turn_end":
        st.markdown("**① キャラを置く**（役職・エリア・生存・各カウンターを入れる。"
                    "死亡判定は**シリアルキラー・キラー・ラバーズ・キーパーソン**等の役職と"
                    "暗躍/不安カウンターで決まる）")
        chars_te = _char_editor("bld_chars_te", with_anyaku=True)
        if chars_te:
            st.markdown(board_html_from_json({"characters": chars_te}), unsafe_allow_html=True)
        st.markdown("**② 状況**")
        fd = st.checkbox("最終日のターン終了フェイズである（タイムトラベラー判定に使用）",
                         key="bld_te_fd")
        pi = st.checkbox("主人公不死が有効（軍人友好5等）", key="bld_te_pi")
        vr = st.checkbox("妄想拡大ウイルスが脚本に入っている（パーソンが不安3以上でシリアルキラー化）",
                         key="bld_te_vr")
        if st.button("⚖️ 裁定する", key="bld_run_te"):
            st.session_state["bld_last_idx"] = _record_result(
                {"set": "BTX", "phase": "turn_end",
                 "question_target": {"name": "ターン終了", "kind": "phase"},
                 "characters": chars_te,
                 "turn_end": {"is_final_day": fd, "protagonist_immortal": pi,
                              "virus_rule": vr}},
                [], app_version, build_info)
        _display_result(st.session_state.get("bld_last_idx"), feedback_ui)
        return

    set_name = st.radio("セット", ["FS", "BTX"], horizontal=True, key="bld_set")

    st.markdown("**① キャラを置く**（キャラ名を選ぶと行が有効／末尾の＋で追加。"
                "エリア空欄は初期エリアで自動補完）")
    chars = _char_editor("bld_chars")

    target_opts = [c["name"] for c in chars] + _AREAS  # 対象選択肢＝置いたキャラ＋4エリア

    placements = []
    ask: list[str] = []
    if phase == "action_resolution":
        st.markdown("**② カードをセットする**（対象は選択式。脚本家と主人公で別の表）")
        st.caption("脚本家がセットするカード（暗躍±・移動斜め・友好禁止・不安禁止・移動・不安 等）")
        mm = _placement_editor("bld_mm", target_opts, owner_fixed="mastermind")
        st.caption("主人公がセットするカード（暗躍禁止・友好±・移動禁止・移動・不安 等。出す人を選ぶ）")
        pr = _placement_editor("bld_pr", target_opts, owner_fixed=None)
        placements = mm + pr

    # 現在の盤面プレビュー（キャラ/カードを組むたびに更新）。裁定前の確認用。
    _preview_html = board_html_from_json({"characters": chars, "placements": placements})
    if _preview_html:
        st.markdown("**🗺️ 現在の盤面（プレビュー）**")
        st.markdown(_preview_html, unsafe_allow_html=True)

    # フェイズ別の追加入力＋質問対象。
    extra: dict = {}
    q_target = {"name": "フェイズ", "kind": "phase"}

    if phase == "action_resolution":
        ask = st.multiselect("③ 何を問う？（複数選べます）", target_opts or _AREAS,
                             default=[], key="bld_qt_ar")
        first = ask[0] if ask else "病院"
        q_target = {"name": first, "kind": "board" if first in AREAS else "character"}

    elif phase == "incident":
        names = [c["name"] for c in chars]
        culprit = st.selectbox("犯人（生存・不安を上の表で設定）", names or ["（キャラを追加）"],
                               key="bld_culprit")
        extra["incident"] = {"culprit": culprit}
        q_target = {"name": "事件", "kind": "phase"}

    elif phase == "goodwill_ability":
        names = [c["name"] for c in chars]
        actor = st.selectbox("友好能力を使うキャラ（配役・友好を上の表で設定）",
                             names or ["（キャラを追加）"], key="bld_actor")
        abilities = [a["name"] for a in GOODWILL_ABILITIES.get(actor, [])]
        ga = {"character": actor}
        if len(abilities) > 1:
            ga["ability"] = st.selectbox("どの友好能力？", abilities, key="bld_gwab")
        extra["goodwill_ability"] = ga
        q_target = {"name": "友好能力", "kind": "phase"}

    elif phase == "mastermind_unrest":
        sc = st.number_input("学校の暗躍カウンター数（ファクター判定用）", 0, 9, 0, key="bld_sch")
        extra["board_anyaku"] = {"学校": int(sc)}
        q_target = {"name": "脚本家能力フェイズ(不安)", "kind": "phase"}

    elif phase == "mastermind_anyaku":
        if st.checkbox("ルール『不穏な噂』を使用", key="bld_rumor"):
            extra["rules"] = ["不穏な噂"]
        opts = ["（総数）"] + _AREAS + [c["name"] for c in chars]
        tgt = st.selectbox("対象（総数 or 特定ボード/キャラ）", opts, key="bld_qt_mm")
        q_target = ({"name": "脚本家能力フェイズ(暗躍)", "kind": "phase"} if tgt == "（総数）"
                    else {"name": tgt, "kind": "board" if tgt in AREAS else "character"})

    if st.button("⚖️ 裁定する", key="bld_run"):
        data = {"set": set_name, "phase": phase, "question_target": q_target,
                "characters": chars, "placements": placements}
        data.update(extra)
        st.session_state["bld_last_idx"] = _record_result(data, ask, app_version, build_info)
    # 直近の裁定結果を常に再描画（フィードバックボタンのrerunでも消えないように）。
    _display_result(st.session_state.get("bld_last_idx"), feedback_ui)
