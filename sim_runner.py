"""通しシミュレーション（連結裁定）— 開発者モード限定の実験機能。

別AIが作った sim/（フルゲームシミュレータ）を薄く叩き、ユーザーが組んだ FS/BTX 脚本を
自動ポリシー（ランダムbot）で1ゲーム流し、フェイズ境界ごとの盤面スナップショットと
公開ログを閲覧できるようにする。単一フェイズ裁定の builder とは別物＝行動解決→事件→
ターン終了→ループ終了の「連結」を目で追うための道具。

依存は sim の安定な公開API＋arena.replay の描画ブリッジだけに留める（sim の内部変更で
壊れにくくする。sim は別AIの現在進行形WIPのため）。app.py 専用・skill 非同梱。
"""

from __future__ import annotations

import random

import pandas as pd
import streamlit as st

from board_viz import board_html_from_json

# sim / arena / agents は別AIのモジュール（安定公開APIのみ使う）。
from sim import run_game
from sim.state import (
    BTX_INCIDENTS,
    BTX_RULE_X_ROLES,
    BTX_RULE_Y_ROLES,
    FS_INCIDENTS,
    FS_RULE_X_ROLES,
    FS_RULE_Y_ROLES,
    Incident,
    Script,
    validate_script,
)
from sim.sample_scripts import BTX_SAMPLE_SCRIPTS, SAMPLE_SCRIPTS
from agents.random_bot import RandomBot
from arena.interactive import PendingHuman, play_interactive
from arena.replay import (
    board_json_from_snapshot,
    board_json_from_view,
    describe_choice,
)

from engine.data import CHARACTER_INITIAL_AREA
from engine.data import ROLE_CLAUSE_ABILITY

_ALL_CHARS = list(CHARACTER_INITIAL_AREA.keys())
_ALL_ROLES = ["パーソン"] + [r for r in ROLE_CLAUSE_ABILITY if r != "パーソン"]


def _sample_options() -> dict:
    """{ラベル: () -> Script}。FS/BTXのサンプルをまとめる。"""
    out = {f"FS: {k}": v for k, v in SAMPLE_SCRIPTS.items()}
    out.update({f"BTX: {k}": v for k, v in BTX_SAMPLE_SCRIPTS.items()})
    return out


def _script_from_form(base: Script) -> Script:
    """base サンプルを起点に、フォームで編集した Script を組み立てて返す。"""
    set_name = st.radio("セット", ["FS", "BTX"], horizontal=True,
                        index=0 if base.set_name == "FS" else 1, key="sr_set")
    # セット切替でルール/事件のselectboxが旧セットの値を保持して壊れないよう、該当キーをクリア
    # （keyウィジェットは index= を無視して session_state を優先するため）。
    if st.session_state.get("_sr_prev_set") != set_name:
        for k in ("sr_ry", "sr_rx", "sr_rx2", "sr_incs"):
            st.session_state.pop(k, None)
        st.session_state["_sr_prev_set"] = set_name
    y_tbl = FS_RULE_Y_ROLES if set_name == "FS" else BTX_RULE_Y_ROLES
    x_tbl = FS_RULE_X_ROLES if set_name == "FS" else BTX_RULE_X_ROLES
    inc_set = sorted(FS_INCIDENTS if set_name == "FS" else BTX_INCIDENTS)

    # selectbox の初期選択はサンプル脚本の値に合わせる（無ければ先頭）。
    def _idx(seq, val):
        return seq.index(val) if val in seq else 0

    ys, xs = list(y_tbl), list(x_tbl)
    c1, c2, c3 = st.columns(3)
    ry = c1.selectbox("ルールY", ys, index=_idx(ys, base.rule_y), key="sr_ry")
    rx = c2.selectbox("ルールX", xs, index=_idx(xs, base.rule_x), key="sr_rx")
    rx2 = None
    if set_name == "BTX":
        rx2 = c3.selectbox("ルールX2（BTXは2つ）", xs,
                           index=_idx(xs, base.rule_x2 or base.rule_x), key="sr_rx2")

    c4, c5 = st.columns(2)
    days = int(c4.number_input("1ループの日数", 1, 10, value=base.days_per_loop, key="sr_days"))
    loops = int(c5.number_input("ループ回数", 1, 10, value=base.loops, key="sr_loops"))

    cast = st.multiselect("登場キャラ（キャスト）", _ALL_CHARS,
                          default=base.cast, key="sr_cast")

    st.caption("配役（役職を割り当てるキャラだけ行を作る。空欄＝パーソン）")
    role_rows = [{"キャラ": n, "役職": base.role_of(n)}
                 for n in cast if base.role_of(n) != "パーソン"]
    role_df = pd.DataFrame(role_rows or [], columns=["キャラ", "役職"])
    role_edit = st.data_editor(
        role_df, num_rows="dynamic", use_container_width=True, key="sr_roles",
        column_config={
            "キャラ": st.column_config.SelectboxColumn("キャラ", options=cast or _ALL_CHARS),
            "役職": st.column_config.SelectboxColumn("役職", options=_ALL_ROLES),
        },
    )
    roles = {}
    for r in role_edit.to_dict("records"):
        nm, rl = r.get("キャラ"), r.get("役職")
        if isinstance(nm, str) and nm and isinstance(rl, str) and rl and rl != "パーソン":
            roles[nm] = rl

    st.caption("事件（日・事件名・犯人）")
    inc_rows = [{"日": i.day, "事件": i.name, "犯人": i.culprit} for i in base.incidents]
    inc_df = pd.DataFrame(inc_rows or [], columns=["日", "事件", "犯人"])
    inc_edit = st.data_editor(
        inc_df, num_rows="dynamic", use_container_width=True, key="sr_incs",
        column_config={
            "日": st.column_config.NumberColumn("日", min_value=1, max_value=days, step=1),
            "事件": st.column_config.SelectboxColumn("事件", options=inc_set),
            "犯人": st.column_config.SelectboxColumn("犯人", options=cast or _ALL_CHARS),
        },
    )
    incidents = []
    for r in inc_edit.to_dict("records"):
        d, nm, cp = r.get("日"), r.get("事件"), r.get("犯人")
        if d is not None and isinstance(nm, str) and nm and isinstance(cp, str) and cp:
            incidents.append(Incident(day=int(d), name=nm, culprit=cp))

    return Script(rule_y=ry, rule_x=rx, rule_x2=rx2, loops=loops, days_per_loop=days,
                  cast=cast, roles=roles, incidents=incidents, set_name=set_name)


def _run(script: Script, seed: int):
    """検証済み脚本をランダムbotで1ゲーム流す。決定的（seed固定）。"""
    random.seed(seed)
    agents = {"mastermind": RandomBot(seed)}
    for i, s in enumerate(("p1", "p2", "p3"), start=1):
        agents[s] = RandomBot(seed + i)
    return run_game(script, agents)


_DECISION_LABEL = {
    "set_card": "行動カードをセット", "mastermind_ability": "脚本家能力",
    "goodwill_ability": "友好能力の使用", "goodwill_refuse": "友好能力への対応",
    "incident_choice": "事件効果の選択", "turn_end_ability": "ターン終了の能力",
    "loop_start_area": "初期エリアの指定", "final_battle_guess": "最後の戦い・役職宣言",
}


def _result_browser(snapshots: list, history: list, log: list, winner, loops: int) -> None:
    """ゲーム終了後：勝敗＋フェイズ送りの盤面ビューア（自動/対話で共用）。"""
    win = {"protagonist": "主人公の勝利", "mastermind": "脚本家の勝利"}.get(winner, "—")
    st.success(f"結果：**{win}**（{loops}ループ／フェイズ記録 {len(snapshots)}件）")
    if not snapshots:
        return
    labels = [f"{i:02d}. L{s['loop']}D{s['day']}：{s['point']}" for i, s in enumerate(snapshots)]
    pick = st.select_slider("フェイズを選ぶ（行動解決後→事件後→ターン終了後…）",
                            options=list(range(len(snapshots))),
                            format_func=lambda i: labels[i], key="sr_pick")
    snap = snapshots[pick]
    st.markdown(f"**{labels[pick]}**")
    st.markdown(board_html_from_json(board_json_from_snapshot(snap, omniscient=True)),
                unsafe_allow_html=True)
    lp, dy = snap["loop"], snap["day"]
    evs = [h for h in history if h.get("loop") == lp and h.get("day") == dy]
    if evs:
        with st.expander("この日の公開イベント"):
            for h in evs:
                st.write(h)
    decs = [d for d in log if d.get("loop") == lp and d.get("day") == dy]
    if decs:
        with st.expander("この日の決定ログ（誰が何を選んだか）"):
            for d in decs:
                st.caption(f"[{d['phase']}] {d['actor']}／{d['decision']}："
                           f"{describe_choice(d['chosen'])}")


def _render_auto(script: Script, seed: int) -> None:
    """自動ポリシー（ランダムbot）で1ゲーム流して結果を閲覧。"""
    if st.button("▶ 1ゲーム流す", key="sr_run"):
        try:
            state, log = _run(script, seed)
            st.session_state["sr_result"] = {
                "snapshots": state.phase_snapshots, "history": state.history,
                "log": log, "winner": state.winner, "loops": state.loop_no,
            }
        except Exception as e:  # noqa: BLE001  # sim側WIPの想定外は握って表示
            st.session_state.pop("sr_result", None)
            st.error(f"シミュレータで例外: {type(e).__name__}: {e}")
    res = st.session_state.get("sr_result")
    if res:
        _result_browser(res["snapshots"], res["history"], res["log"],
                        res["winner"], res["loops"])


def _render_interactive(script: Script, seed: int) -> None:
    """対話プレイ：ユーザーが主人公3席を操作し、脚本家はランダムbot。

    play_interactive は決定的に毎回ゼロから再実行し、未選択の人間決定で PendingHuman を送出する
    （sim/arena の設計）。人間の選択列 sr_choices を貯めながら前進する。
    """
    if st.button("🔄 対話を最初からやり直す", key="sr_reset"):
        st.session_state["sr_choices"] = []
    choices = st.session_state.setdefault("sr_choices", [])

    try:
        state, log = play_interactive(script, {"mastermind": RandomBot(seed)},
                                      {"p1", "p2", "p3"}, choices)
    except PendingHuman as p:
        st.info(f"**あなた（{p.actor}）の番** — L{p.state.loop_no}D{p.state.day}／"
                f"{p.state.phase}：{_DECISION_LABEL.get(p.decision, p.decision)}")
        # 主人公視点の盤面（配役・犯人は伏せ＝公開情報のみ）。
        st.markdown(board_html_from_json(board_json_from_view(p.view)),
                    unsafe_allow_html=True)
        st.caption(f"手を選んでください（これまでの選択 {len(choices)} 手）：")
        for i, opt in enumerate(p.options):
            if st.button(describe_choice(opt), key=f"sr_opt{len(choices)}_{i}"):
                choices.append(opt)
                st.rerun()
        return
    except Exception as e:  # noqa: BLE001
        st.error(f"シミュレータで例外: {type(e).__name__}: {e}")
        return
    # ゲーム終了＝人間の全決定が済んだ。結果をブラウズ。
    st.balloons()
    _result_browser(state.phase_snapshots, state.history, log, state.winner, state.loop_no)


def render_sim_runner() -> None:
    """通しシミュレーション本体（app.py の開発者モード内から呼ぶ）。"""
    st.caption(
        "別AIのフルゲームシミュレータ(sim/)で、組んだ FS/BTX 脚本を最後まで進めます。"
        "⚠実験・FS中心／sim側はWIP。開発者モード限定。"
    )

    samples = _sample_options()
    src = st.radio("脚本の元", ["サンプルから", "サンプルを編集して組む"],
                   horizontal=True, key="sr_src")
    sample_label = st.selectbox("サンプル脚本", list(samples), key="sr_sample")
    # サンプルを切替えたら、フォームの派生ウィジェット状態をクリアして新サンプルから作り直す
    # （keyウィジェットは session_state を優先し index=/default を無視するため）。
    if st.session_state.get("_sr_prev_sample") != sample_label:
        for k in ("sr_set", "sr_ry", "sr_rx", "sr_rx2", "sr_days", "sr_loops",
                  "sr_cast", "sr_roles", "sr_incs", "sr_result", "sr_choices"):
            st.session_state.pop(k, None)
        st.session_state["_sr_prev_sample"] = sample_label
    base = samples[sample_label]()

    script = base if src == "サンプルから" else _script_from_form(base)

    # 検証（sim.validate_script が違反・KB範囲外を弾く＝そのままエラー表示）。
    try:
        validate_script(script)
    except ValueError as e:
        st.error(f"脚本が不正です（要修正）: {e}")
        return

    mode = st.radio("進め方", ["自動で流す（観る）", "自分で主人公を操作（対話）"],
                    horizontal=True, key="sr_mode")
    seed = int(st.number_input("乱数シード（脚本家の手。同じ値なら同じ展開）",
                               0, 9999, value=0, key="sr_seed"))

    if mode == "自動で流す（観る）":
        _render_auto(script, seed)
    else:
        _render_interactive(script, seed)
