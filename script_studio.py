# -*- coding: utf-8 -*-
"""脚本工房（Script Studio）— 人間の脚本家のためのワンストップ品質チェック。

脚本を組む→その場でレポート：
  ① 正規性（sim.validate_script＝ルールが要求する配役・事件・犯人重複の検査）
  ② 勝ち筋の多様性（sim.script_quality＝独立した敗北ルートの本数・薄い脚本の検出）
  ③ 事件の発生可能性（打点会計＝死に事件の検出）
  ④ 初期局面のレース判定（sim.loop_race＝構造的に防衛不能/勝ち筋なしの検出≒タブー①の近似）
  ⑤ ループ回数の目安（script_estimate＝KB:70 p48 の見積り表）
  ⑥ AI自己対戦の実測（arena.difficulty＝平均ループ数・任意実行）

起動（スタンドアロン・共有ファイル無編集の設計）:
    streamlit run script_studio.py
app.py への組み込みは1行（開発者モード等に from script_studio import render_studio）。

権利注意：出力にカード画像・カード全文は含めない（名前と自作テキストのみ）。
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from engine.data import CHARACTER_INITIAL_AREA
from script_estimate import describe_estimate, estimate_loops
from sim.sample_scripts import SAMPLE_SCRIPTS
from sim.script_quality import (
    balance_grade,
    cast_coherence,
    cooling_pressure,
    describe_win_paths,
    incident_feasibility_lines,
    incident_feasibility_verdict,
    probe_difficulty,
)
from sim.loop_race import analyze_script, describe_report
from sim.state import (
    BTX_INCIDENTS,
    BTX_ROLE_UNIVERSE,
    BTX_RULE_X_ROLES,
    BTX_RULE_Y_ROLES,
    FS_INCIDENTS,
    FS_ROLE_UNIVERSE,
    FS_RULE_X_ROLES,
    FS_RULE_Y_ROLES,
    Incident,
    Script,
    validate_script,
)

_ALL_CHARS = list(CHARACTER_INITIAL_AREA.keys())


def role_options_for(set_name: str, y_roles: set[str], cast: list[str]) -> list[str]:
    """役職セレクタの選択肢（A-35・Streamlit非依存＝テスト可能）。

    通常はルールが追加する役職（y_roles）のみ。★イレギュラーがキャストに居るときだけ、特性上
    「ルールが追加しない役職」を配役する必要がある（パーソン不可・追加役職も不可＝KB: 30）ため、
    列全体をユニバース（FS/BTX_ROLE_UNIVERSE − パーソン）へ広げる（data_editor は行別 options 不可）。
    他キャラの誤選択は保存時の validate_script の明確エラーに任せる（判定側は正＝触らない）。
    """
    opts = set(y_roles)
    if "イレギュラー" in (cast or []):
        universe = FS_ROLE_UNIVERSE if set_name == "FS" else BTX_ROLE_UNIVERSE
        opts |= set(universe) - {"パーソン"}
    return sorted(opts) or ["パーソン"]


def _sample_options() -> dict:
    """サンプル脚本の {表示ラベル: 生成関数}。ラベルは各脚本の**実セット**（Script.set_name）で付ける。
    ★C-7：旧実装は SAMPLE_SCRIPTS（歴史的に FS/BTX 混在の全12本）へ一律「FS: 」を付け、さらに
    その部分集合 BTX_SAMPLE_SCRIPTS を「BTX: 」で重ねて追加＝『FS: btx_seal』の誤表記＋同一脚本の
    FS/BTX 重複表示になっていた。SAMPLE_SCRIPTS だけを実セットで並べれば誤表記も重複も解消する。"""
    out: dict = {}
    for k, factory in SAMPLE_SCRIPTS.items():
        try:
            set_name = factory().set_name
        except Exception:  # noqa: BLE001  生成失敗時もキー名で表示（工房を落とさない）
            set_name = "?"
        out[f"{set_name}: {k}"] = factory
    return out


def _blank_script(set_name: str) -> Script:
    """C-6：「ゼロから組む」用の空テンプレ（セット選択→最小雛形）。ルールY/Xは各表の先頭・
    1ループ3日・ループ1・キャスト空・配役/事件なし＝_script_form でユーザーが埋める土台。
    正規性は通らない前提（C-8＝シナリオチェッカーは既定オフ・診断/自己対戦はガードで落ちない）。"""
    y_tbl = FS_RULE_Y_ROLES if set_name == "FS" else BTX_RULE_Y_ROLES
    x_tbl = FS_RULE_X_ROLES if set_name == "FS" else BTX_RULE_X_ROLES
    ys, xs = list(y_tbl), list(x_tbl)
    return Script(rule_y=ys[0], rule_x=xs[0], loops=1, days_per_loop=3,
                  cast=[], roles={}, incidents=[], set_name=set_name,
                  rule_x2=(xs[0] if set_name == "BTX" else None))


def _script_form(base: Script) -> Script:
    """脚本編集フォーム（キーは ss_*＝他モードと非衝突）。"""
    set_name = st.radio("セット", ["FS", "BTX"], horizontal=True,
                        index=0 if base.set_name == "FS" else 1, key="ss_set")
    if st.session_state.get("_ss_prev_set") != set_name:
        for k in ("ss_ry", "ss_rx", "ss_rx2", "ss_incs"):
            st.session_state.pop(k, None)
        st.session_state["_ss_prev_set"] = set_name
    y_tbl = FS_RULE_Y_ROLES if set_name == "FS" else BTX_RULE_Y_ROLES
    x_tbl = FS_RULE_X_ROLES if set_name == "FS" else BTX_RULE_X_ROLES
    inc_set = sorted(FS_INCIDENTS if set_name == "FS" else BTX_INCIDENTS)

    def _idx(seq, val):
        return seq.index(val) if val in seq else 0

    ys, xs = list(y_tbl), list(x_tbl)
    c1, c2, c3 = st.columns(3)
    ry = c1.selectbox("ルールY", ys, index=_idx(ys, base.rule_y), key="ss_ry")
    rx = c2.selectbox("ルールX", xs, index=_idx(xs, base.rule_x), key="ss_rx")
    rx2 = None
    if set_name == "BTX":
        rx2 = c3.selectbox("ルールX2", xs, index=_idx(xs, base.rule_x2 or base.rule_x),
                           key="ss_rx2")
    c4, c5 = st.columns(2)
    days = int(c4.number_input("1ループの日数", 1, 10, value=base.days_per_loop, key="ss_days"))
    loops = int(c5.number_input("ループ回数", 1, 10, value=base.loops, key="ss_loops"))
    cast = st.multiselect("登場キャラ", _ALL_CHARS, default=base.cast, key="ss_cast")

    st.caption("配役（役職を割り当てるキャラだけ。空欄＝パーソン）")
    y_roles = set()
    for tbl in (y_tbl.get(ry, ()), x_tbl.get(rx, ()), x_tbl.get(rx2, ()) if rx2 else ()):
        y_roles |= set(tbl)
    # ★A-35（2026-07-18）：イレギュラーは特性上「ルールが追加**しない**役職」を配役する必要がある
    #   （パーソン不可・ルール追加役職も不可＝KB: 30 特性）。役職 options が y_roles（＝ルール追加役職）
    #   のみだと、イレギュラーに配れる役職が1つも選べず validate が必ず拒否＝**構造的デッドロック**
    #   （中堅プレイヤー報告）。data_editor の SelectboxColumn は行別 options 不可＝イレギュラーが
    #   キャストに居るときは**列全体**をユニバース（FS/BTX_ROLE_UNIVERSE − パーソン）へ広げる。
    #   他キャラが非追加役職を誤選択したら既存 validate の明確エラーに任せる（判定側は正＝触らない）。
    _role_opts = role_options_for(set_name, y_roles, cast)
    if "イレギュラー" in cast:
        st.caption("👤 **イレギュラー**は「選んだルールが**追加しない**役職」を選びます"
                   "（特性：パーソン不可・ルール追加役職も不可）。そのため役職欄の選択肢を全役職に"
                   "広げています。他キャラに非追加役職を選ぶと保存時にエラーになります。")
    role_rows = [{"キャラ": n, "役職": base.role_of(n)}
                 for n in cast if base.role_of(n) != "パーソン"]
    role_edit = st.data_editor(
        pd.DataFrame(role_rows or [], columns=["キャラ", "役職"]),
        num_rows="dynamic", use_container_width=True, key="ss_roles",
        column_config={
            "キャラ": st.column_config.SelectboxColumn("キャラ", options=cast or _ALL_CHARS),
            "役職": st.column_config.SelectboxColumn("役職", options=_role_opts),
        })
    roles = {}
    for r in role_edit.to_dict("records"):
        nm, rl = r.get("キャラ"), r.get("役職")
        if isinstance(nm, str) and nm and isinstance(rl, str) and rl and rl != "パーソン":
            roles[nm] = rl

    st.caption("事件（日・事件名・犯人）")
    inc_rows = [{"日": i.day, "事件": i.name, "犯人": i.culprit} for i in base.incidents]
    inc_edit = st.data_editor(
        pd.DataFrame(inc_rows or [], columns=["日", "事件", "犯人"]),
        num_rows="dynamic", use_container_width=True, key="ss_incs",
        column_config={
            "日": st.column_config.NumberColumn("日", min_value=1, max_value=days, step=1),
            "事件": st.column_config.SelectboxColumn("事件", options=inc_set),
            "犯人": st.column_config.SelectboxColumn("犯人", options=cast or _ALL_CHARS),
        })
    incidents = []
    for r in inc_edit.to_dict("records"):
        d, nm, cp = r.get("日"), r.get("事件"), r.get("犯人")
        if d is not None and isinstance(nm, str) and nm and isinstance(cp, str) and cp:
            incidents.append(Incident(day=int(d), name=nm, culprit=cp))
    return Script(rule_y=ry, rule_x=rx, rule_x2=rx2, loops=loops, days_per_loop=days,
                  cast=cast, roles=roles, incidents=incidents, set_name=set_name)


def _static_report(script: Script) -> None:
    """②〜⑤の静的レポート（一瞬で出る・API不要）。"""
    # ② 勝ち筋の多様性
    g = balance_grade(script)
    st.markdown(f"**② 勝ち筋の多様性：{g.verdict}**（独立ルート {g.n_paths}本）")
    for p in describe_win_paths(script):
        st.markdown(f"- {p}")
    # ③ 事件の発生可能性（死に事件の検出）
    st.markdown("**③ 事件の発生可能性（打点会計）**")
    for ln in incident_feasibility_lines(script):
        st.markdown(f"- {ln}")
    v = incident_feasibility_verdict(script)
    if v:
        st.markdown(v)
    # ④ 初期局面のレース判定
    rep = analyze_script(script)
    st.markdown("**④ 初期局面のレース判定（神視点・構造）**")
    for ln in describe_report(rep):
        st.markdown(f"- {ln}")
    # ⑤ ループ回数の目安（KB:70）
    st.markdown("---")
    for ln in describe_estimate(estimate_loops(script), planned_loops=script.loops):
        st.markdown(ln)
    # ⑤b 難度下限プローブ（AI主人公オラクル・数秒・テスター知見 2026-07-09）
    st.markdown("---")
    p = probe_difficulty(script)
    st.markdown(f"**⑤b 難度下限プローブ（AI主人公×{len(p.per_seed)}局）**")
    st.markdown(p.verdict)
    st.caption(f"seed別の突破ループ数: {p.per_seed}"
               "（AI主人公は目安＝人間の中級者と完全一致はしない）")
    # ⑦ 生成品質チェック（冷却圧・キャラ選択の整合＝テスター知見）
    cool = cooling_pressure(script)
    coh = cast_coherence(script)
    if cool or coh:
        st.markdown("**⑦ 追加チェック（テスター知見）**")
        for ln in cool:
            st.markdown(f"- {ln}")
        for ln in coh:
            st.markdown(f"- ⚠ 整合: {ln}")


def render_studio() -> None:
    st.caption("脚本を組む→正規性・勝ち筋・死に事件・詰み・推奨ループ数をその場で診断。"
               "仕上げにAI自己対戦で難易度を実測（⑥）。")
    # ★C-6：脚本の元＝「サンプルを編集して組む／ゼロから組む」の2択（旧「サンプルから＝そのまま
    #   遊ぶ」は廃止＝工房は常に編集フォームを通す）。ゼロから＝セット選択→空テンプレ（_blank_script）。
    samples = _sample_options()
    src = st.radio("脚本の元", ["サンプルを編集して組む", "ゼロから組む"],
                   horizontal=True, key="ss_src")
    if src == "ゼロから組む":
        zset = st.radio("セット", ["FS", "BTX"], horizontal=True, key="ss_zset")
        base = _blank_script(zset)
        _base_sig = f"blank|{zset}"
    else:
        label = st.selectbox("サンプル脚本", list(samples), key="ss_sample")
        base = samples[label]()
        _base_sig = f"sample|{label}"
    # base の由来（サンプル切替・ゼロからのセット切替・元の切替）が変わったら、フォームの widget
    #   キーを一旦クリアして新しい base を反映させる（Streamlit は widget の session_state 値を
    #   value/index より優先するため、クリアしないと前の脚本の値が残る）。
    if st.session_state.get("_ss_base_sig") != _base_sig:
        for k in ("ss_set", "ss_ry", "ss_rx", "ss_rx2", "ss_days", "ss_loops",
                  "ss_cast", "ss_roles", "ss_incs"):
            st.session_state.pop(k, None)
        st.session_state["_ss_base_sig"] = _base_sig
    script = _script_form(base)

    # ① シナリオチェッカーβ（★C-8：既定オフ＝オリジナル脚本がチェッカーの厳格さで弾かれて遊べない
    #   事態を防ぐ）。オンのときだけ validate_script で弾く。オフでも「エラーで落ちない」は別責務＝
    #   下の診断・自己対戦を try/except で包む（キャスト空・犯人不在等で sim が落ちる類の最低限ガード）。
    if st.toggle("① シナリオチェッカーβ（ランダム生成が使っている検査）",
                 value=False, key="ss_check"):
        try:
            validate_script(script)
        except ValueError as e:
            st.error(f"シナリオチェッカーβ：不正です — {e}")
            return
        st.success("シナリオチェッカーβ：OK（ルールが要求する配役・事件・犯人の検査を通過）")

    try:
        _static_report(script)
    except Exception as e:  # noqa: BLE001  未完成の脚本でも工房を落とさない（C-8 最低限ガード）
        st.info("この脚本ではまだ②〜⑤の診断を出せません（配役・事件・犯人などが不足している"
                f"可能性）。①シナリオチェッカーβをオンにすると不足点が分かります。〔{type(e).__name__}〕")

    # ⑥ AI自己対戦β（任意・時間がかかる）
    # ★C-9：負荷対策（Streamlit Cloud 共有1インスタンス＝同時複数ユーザーの長時間計算で全員が固まる）。
    #   局数上限を 30→10（既定6）に下げ、1セッション累計の実行回数にも上限（超過は実行不可の案内）。
    st.markdown("---")
    _SELFPLAY_SESSION_LIMIT = 10
    if st.session_state.get("ss_ngames", 6) > 10:   # 旧既定(30等)が session に残っていてもクランプ
        st.session_state["ss_ngames"] = 10
    n_games = int(st.slider("⑥ AI自己対戦β の局数（実測難易度）", 4, 10, 6, key="ss_ngames"))
    _runs = st.session_state.get("ss_selfplay_runs", 0)
    _over = _runs >= _SELFPLAY_SESSION_LIMIT
    if _over:
        st.info(f"β期間は回数制限中です（1セッション {_SELFPLAY_SESSION_LIMIT} 回まで）。"
                "ページを開き直すと再度使えます。")
    if st.button("▶ AI自己対戦βで実測する", key="ss_run", disabled=_over):
        st.session_state["ss_selfplay_runs"] = _runs + 1   # 実行のたびに1消費（成否問わず＝計算は走る）
        from arena.difficulty import evaluate_script, verdict
        try:
            with st.spinner(f"AI同士で{n_games}局対戦中…"):
                rep = evaluate_script(script, n_games=n_games)
            st.session_state["ss_diff"] = {"rep": rep, "verdict": verdict(rep)}
        except Exception as e:  # noqa: BLE001  未完成の脚本での自己対戦クラッシュを防ぐ
            st.session_state.pop("ss_diff", None)
            st.warning("この脚本ではAI自己対戦を実測できませんでした（配役・事件などが未完成の"
                       f"可能性）。①シナリオチェッカーβで不足点を確認してください。〔{type(e).__name__}〕")
    diff = st.session_state.get("ss_diff")
    if diff:
        st.markdown(f"**実測難易度：{diff['verdict']}**")
        st.json({k: v for k, v in diff["rep"].items() if k != "rows"})

    # 出力（卓で使える形）
    with st.expander("📤 この脚本のエクスポート"):
        payload = {
            "set": script.set_name, "rule_y": script.rule_y,
            "rule_x": list(script.rule_xs), "loops": script.loops,
            "days_per_loop": script.days_per_loop, "cast": script.cast,
            "roles": script.roles,
            "incidents": [{"day": i.day, "name": i.name, "culprit": i.culprit}
                          for i in script.incidents],
        }
        st.code(json.dumps(payload, ensure_ascii=False, indent=1), language="json")
        st.caption("※configはネタバレを含む（配役・犯人）＝脚本家専用。公開シートは日と事件名のみ。")
        # ★プレイ投入口用JSON（§2b・2026-07-13）：正典形式（全フィールド）。上の payload でも
        #   読めるが、大物の縄張り等も含むこちらが確実。プレイ画面の『✍️ 自作脚本で始める』へ貼付。
        st.markdown("**🎮 プレイ用JSON**（一人回し/脚本家プレイの『✍️ 自作脚本で始める』へ貼付）")
        from arena.gamelog import script_to_dict
        st.code(json.dumps(script_to_dict(script), ensure_ascii=False, indent=1),
                language="json")


if __name__ == "__main__":
    st.set_page_config(page_title="惨劇RoopeR 脚本工房", page_icon="📜", layout="wide")
    st.title("📜 惨劇RoopeR 脚本工房（β）")
    render_studio()
