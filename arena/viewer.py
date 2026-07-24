"""リプレイビューア（計画 §3.2）— 決定ログJSONLを日単位でステップ再生する。

起動:  streamlit run arena/viewer.py
入力:  arena/runner.py --out で保存した決定ログ（1ゲーム=1ファイル）

★神視点モードは配役・犯人・secret_log（＝ネタバレ）を表示する。ローカルのデバッグ専用で、
デプロイアプリ（app.py）には組み込まない。主人公視点モードは「そのAIに見えていた情報」
だけを描く＝人間がAIと同じ土俵で手の妥当性を検証するためのモード（計画 §3.2）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402  streamlitの依存に含まれる

from agents.belief import Belief  # noqa: E402
from arena.gamelog import load_game, load_game_lines  # noqa: E402
from arena.insight import build_insight, loop_summaries  # noqa: E402
from arena.replay import (  # noqa: E402
    board_json_from_snapshot,
    describe_choice,
    describe_event,
)
from board_viz import board_html_from_json  # noqa: E402
from sim.reference import (  # noqa: E402
    INCIDENT_EFFECTS,
    ROLE_REFERENCE,
    RULE_X_REFERENCE,
    RULE_Y_REFERENCE,
    role_placement_summary,
)



def render_viewer(mobile: bool = False, embedded: bool = False) -> None:
    """リプレイビューア本体（standalone実行 or app.py からモード切替で呼ぶ）。

    embedded=True（app.py 埋込）ではファイルアップロード専用（ローカルの logs/ 走査は無効）、
    神視点は既定OFF（身内公開でも推理のネタバレを避け、opt-inで開く）。standalone は従来どおり。
    set_page_config は呼ばない（app.py が設定済み・standalone は __main__ ガードで設定）。
    """
    st.title("🔁 リプレイビューア（AI自己対戦ログ）")

    # ---------- ログの読み込み ----------
    with st.sidebar:
        st.header("ログ")
        uploaded = st.file_uploader(
            "決定ログ (.jsonl)", type=["jsonl"],
            help="一人回し（🎮）の『💾 対局のログを保存』や自己対戦ランナーで出力した .jsonl。")
        picked = None
        if not embedded:
            # standalone のみ：ローカル logs/ ディレクトリからの選択（デプロイ環境には無い）
            log_dir = st.text_input("またはディレクトリから", value="logs")
            if uploaded is None:
                files = {p.name: p for p in sorted(Path(log_dir).glob("*.jsonl"))} \
                    if Path(log_dir).is_dir() else {}
                if files:
                    # ★AppTest互換のため options は文字列（Path等はformat_funcとの併用で壊れる）
                    picked = files[st.selectbox("ファイル", list(files))]
                else:
                    st.caption("`python -m arena.runner --out logs/` でログを生成できます")
        # ★神視点＝配役・犯人・裏向き札のネタバレ。埋込（身内公開）では既定OFF・opt-in。
        omniscient = st.toggle("神視点（配役・犯人・裏向き札＝ネタバレ）",
                               value=not embedded)

    if uploaded is not None:
        script, meta, decisions = load_game_lines(
            uploaded.getvalue().decode("utf-8").splitlines())
    elif picked is not None:
        script, meta, decisions = load_game(picked)
    else:
        st.info("左のサイドバーから決定ログを選んでください。")
        st.stop()

    # ============================================================================
    # 上段＝全貌（勝敗・各ループの終わり方・初期盤面・推理の収束）。下へ行くほど詳細。
    # ============================================================================

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("勝者", {"protagonist": "主人公", "mastermind": "脚本家"}.get(meta["winner"], "?"))
    c2.metric("使用ループ", f'{meta["loops_played"]} / {script.loops}')
    c3.metric("決定数", meta["n_decisions"])
    c4.metric("キャスト", len(script.cast))

    # ---------- ループ経過＋初期盤面（このリプレイの全貌） ----------
    ov_left, ov_right = st.columns([3, 2])
    with ov_left:
        st.caption("各ループの終わり方")
        st.table(loop_summaries(script, meta, omniscient=omniscient))
    with ov_right:
        st.caption("初期盤面（ループ1・1日目 開始時）")
        _init_snap = next((s for s in meta.get("snapshots", [])
                           if s["loop"] == 1 and s["day"] == 1), None)
        if _init_snap:
            html = board_html_from_json(board_json_from_snapshot(_init_snap, omniscient=omniscient))
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.caption("（スナップショットなし）")

    if omniscient:
        with st.expander("📜 脚本（神視点）", expanded=False):
            _rx = " / ".join(script.rule_xs)
            st.write(f"**ルールY**: {script.rule_y}　**ルールX**: {_rx}")
            st.table([{"キャラ": n, "役職": script.role_of(n)} for n in script.cast])
            st.table([{"日": i.day, "事件": i.name, "犯人": i.culprit}
                      for i in script.incidents])

    # ---------- 推理の収束グラフ＋判明タイムライン ----------
    _insight = build_insight(script, meta.get("history", []))
    _tl = _insight["timeline"]
    # ★連番プレフィクスで辞書順＝時系列を保証（st.line_chartは文字列indexを辞書順に並べるため）
    _labels = [f'{i:02d} {r["label"]}' for i, r in enumerate(_tl)]
    _wt = _insight["worlds_total"] or 1

    st.subheader("📈 推理の収束")
    st.caption("横軸＝時点（ループ・日）で共通。上＝ルールY、下＝ルールX を上下に並べて時間軸で比較できる。"
               "ルールの確率が0%/100%へ収束し、可能世界（配役×ルールの組合せ）が減っていく様子。")
    # ★ルールY/Xを上下に並べて時間軸を揃える（ユーザー要望 2026-07-05）
    y_names = sorted({ry for r in _tl for ry in r["y_probs"]})
    df_y = pd.DataFrame(
        {f'Y:{ry}': [r["y_probs"].get(ry, 0.0) for r in _tl] for ry in y_names}
        | {"可能世界(比率)": [r["worlds"] / _wt for r in _tl]},
        index=_labels)
    st.caption("ルールY の確率＋可能世界(比率)")
    st.line_chart(df_y, height=240)
    x_names = sorted({rx for r in _tl for rx in r["x_probs"]})
    df_x = pd.DataFrame(
        {f'X:{rx}': [r["x_probs"].get(rx, 0.0) for r in _tl] for rx in x_names},
        index=_labels)
    st.caption("ルールX の確率")
    st.line_chart(df_x, height=240)

    with st.expander(f'🧭 判明タイムライン（事件・公開・確定・消去：{len(_insight["deductions"])}件）',
                     expanded=True):
        if _insight["deductions"]:
            st.table(_insight["deductions"])
        else:
            st.caption("（このリプレイでは推理が動く公開イベントがありませんでした）")

    st.divider()
    # ============================================================================
    # 下段＝日単位の詳細（決定・盤面遷移・経過・推理インスペクタ）
    # ============================================================================

    # ---------- 日程表（全日の事件を常時表示＝選択外の日も見える） ----------
    incident_by_day = {i.day: i for i in script.incidents}


    def _incident_text(dy: int) -> str:
        inc = incident_by_day.get(dy)
        if inc is None:
            return "事件なし"
        # 犯人はネタバレのため神視点のときだけ併記
        return f"{inc.name}（{inc.culprit}）" if omniscient else inc.name


    schedule = "　／　".join(f"**{dy}日目** {_incident_text(dy)}"
                            for dy in range(1, script.days_per_loop + 1))
    st.markdown("📅 " + schedule)

    # ---------- 日の選択（L×D の表形式ボタングリッド） ----------
    _PHASE_JP = {
        "turn_start": "ターン開始", "mastermind_set": "脚本家行動", "protagonist_set": "主人公行動",
        "action_resolution": "行動解決", "mastermind_ability": "脚本家能力",
        "goodwill_ability": "主人公能力", "incident": "事件", "leader_change": "リーダー交代",
        "turn_end": "ターン終了", "loop_start": "ループ開始", "final_battle": "最後の戦い",
    }


    def _actor_jp(actor: str) -> str:
        return "脚本家" if actor == "mastermind" else actor  # p1/p2/p3 はそのまま


    # ---------- 日の選択（サイドバーの L×D グリッド＝どこを見ていても操作できる） ----------
    days = sorted({(d["loop"], d["day"]) for d in decisions})
    if "sel_ld" not in st.session_state or tuple(st.session_state["sel_ld"]) not in set(days):
        st.session_state["sel_ld"] = list(days[0])

    _loops = sorted({lp for lp, _dy in days})
    _day_vals = sorted({dy for _lp, dy in days})
    _present = set(days)
    with st.sidebar:
        st.markdown("---")
        st.caption("ループ / 日 を選択")
        for lp in _loops:
            # 先頭のラベル列を狭く＝間延び防止（横＝日、縦＝ループ）
            cols = st.columns([0.45] + [1] * len(_day_vals))
            cols[0].markdown(f"**L{lp}**")
            for i, dy in enumerate(_day_vals):
                if (lp, dy) in _present:
                    sel = st.session_state["sel_ld"] == [lp, dy]
                    cols[i + 1].button(
                        "始" if dy == 0 else f"D{dy}", key=f"ld_{lp}_{dy}",
                        type=("primary" if sel else "secondary"),
                        use_container_width=True,
                        on_click=lambda l=lp, d=dy: st.session_state.update(sel_ld=[l, d]))
                else:
                    cols[i + 1].write("")
    loop_no, day_no = st.session_state["sel_ld"]
    _ld = f"L{loop_no}・{'開始' if day_no == 0 else f'D{day_no}'}"
    day_decisions = [d for d in decisions if (d["loop"], d["day"]) == (loop_no, day_no)]
    snapshots = [s for s in meta.get("snapshots", [])
                 if (s["loop"], s["day"]) == (loop_no, day_no)]

    # ---------- この日の決定 ----------
    st.caption(f"この日の決定（{_ld}）")
    st.table([
        {"#": i + 1,
         "フェイズ": _PHASE_JP.get(d["phase"], d["phase"]),
         "手番": _actor_jp(d["actor"]),
         "選択": describe_choice(d["chosen"]),
         "合法手": len(d["options"])}
        for i, d in enumerate(day_decisions)
    ])

    # ---------- この日の経過（フェイズごと・公開イベント＋神視点ログ） ← 盤面より先 ----------
    st.subheader(f"この日の経過　　🗓 {_ld}")
    day_hist = [e for e in meta.get("history", [])
                if (e.get("loop"), e.get("day")) == (loop_no, day_no)]


    def _render_phase_grouped(events, secret=False):
        cur = object()
        for e in events:
            ph = e.get("phase")
            if ph != cur:
                st.markdown(f'**〔{_PHASE_JP.get(ph, ph or "—")}〕**')
                cur = ph
            st.markdown(f"- {describe_event(e, secret=secret)}")


    if day_hist:
        with st.expander(f"📢 公開イベント（{len(day_hist)}件・フェイズ順）", expanded=True):
            _render_phase_grouped(day_hist)
    else:
        st.caption("（公開イベントなし）")
    if omniscient:
        secrets = [e for e in meta.get("secret_log", [])
                   if (e.get("loop", 0), e.get("day", 0)) == (loop_no, day_no)]
        if secrets:
            with st.expander(f"🗝 神視点ログ（{len(secrets)}件）", expanded=False):
                for e in secrets:
                    st.markdown(f"- {describe_event(e, secret=True)}")

    # ---------- 盤面：フェイズ後スナップショットを縦に全部並べる ----------
    st.subheader(f'盤面（{"神視点" if omniscient else "主人公視点"}）　　🗓 {_ld}')
    if snapshots:
        for snap in snapshots:
            st.markdown(f'**― {snap["point"]} ―**')
            html = board_html_from_json(board_json_from_snapshot(snap, omniscient=omniscient))
            if html:
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.caption("（盤面情報なし）")
    else:
        st.caption("（この日のフェイズ盤面スナップショットはありません）")

    if not omniscient:
        st.caption("盤面は各AIに見えた公開情報のみ（配役・裏向き札は伏せています）")

    # ---------- 推理インスペクタ（この時点で主人公が公開情報から絞り込める範囲・§3.3） ----------
    st.subheader("🔍 推理インスペクタ")
    _point_jp = f"ループ{loop_no}開始時点" if day_no == 0 else f"ループ{loop_no}・{day_no}日目の終了時点"
    st.caption(f"**{_point_jp}**（その日までの全公開イベントを反映）における主人公の推理。"
               "神視点ONなら真の配役と照合（✓＝最有力が正解）。")
    _bel = Belief(script.cast, [{"day": i.day, "name": i.name} for i in script.incidents],
                  set_name=script.set_name)
    _bel.observe([e for e in meta.get("history", [])
                  if (e.get("loop", 0), e.get("day", 0)) <= (loop_no, day_no)])
    _summ = _bel.summary()

    insp_left, insp_right = st.columns([2, 3])
    with insp_left:
        st.caption(f"この時点の盤面（{_point_jp}）")
        if snapshots:  # この日の最後のフェイズ盤面＝日の終了時点
            _last = snapshots[-1]
            html = board_html_from_json(board_json_from_snapshot(_last, omniscient=omniscient))
            st.markdown(html, unsafe_allow_html=True)
            st.caption(f"（{_last['point']}）")
        else:
            st.caption("（この日の盤面スナップショットなし）")

    with insp_right:
        bc1, bc2 = st.columns(2)
        bc1.metric("可能世界 残", f'{_summ["worlds_remaining"]:,} / {_summ["worlds_total"]:,}')
        _top = _summ["rule_top"][0] if _summ["rule_top"] else None
        bc2.metric("最有力ルール",
                   f'{_top["rule_y"]}×{_top["rule_x"]}' if _top else "—",
                   f'{_top["prob"]:.0%}' if _top else None)

        # 役職推定の表（神視点なら正誤つき）
        role_rows = []
        for role, info in _summ["role_targets"].items():
            row = {"役職": role, "最有力キャラ": info["name"], "確率": f'{info["prob"]:.0%}'}
            if omniscient:
                truth = next((n for n in script.cast if script.role_of(n) == role), None)
                row["正解"] = truth or "（不在）"
                row["的中"] = "✓" if truth == info["name"] and info["prob"] > 0 else ""
            role_rows.append(row)
        if role_rows:
            st.table(role_rows)

        # 犯人候補
        culp = _summ.get("culprit_candidates", {})
        if culp:
            st.write("**犯人候補（事件ごと）**")
            st.table([{"日": d, "候補数": len(c),
                       "候補": "／".join(c) if len(c) <= 6 else f"{len(c)}人（多数）",
                       **({"正解": next((i.culprit for i in script.incidents if i.day == d), "?")}
                          if omniscient else {})}
                      for d, c in sorted(culp.items())])

    # ---------- リファレンス（FS/BTX ルール一覧・事件効果） ----------
    st.divider()
    _set = "FS" if script.set_name == "FS" else "BTX"
    with st.expander(f"📖 ルールY/X 一覧（{_set}）", expanded=False):
        st.write("**ルールY（主要な敗北条件）**")
        st.table([{"ルールY": k, "追加役職": v["roles"], "効果": v["text"]}
                  for k, v in RULE_Y_REFERENCE.items() if _set in v["set"]])
        st.write("**ルールX（副次ルール）**")
        st.table([{"ルールX": k, "追加役職": v["roles"], "効果": v["text"]}
                  for k, v in RULE_X_REFERENCE.items() if _set in v["set"]])
    with st.expander(f"💥 事件の効果一覧（{_set}）", expanded=False):
        st.table([{"事件": k, "効果": v} for k, v in INCIDENT_EFFECTS.items()
                  if _set == "BTX" or "（BTX）" not in v])
    with st.expander(f"🎭 役職能力表（{_set}）", expanded=False):
        _placement = role_placement_summary(_set)
        st.table([{"役職": role, "条文能力": v["clause"], "上限": v["max"],
                   "配置されるルール": _placement.get(role, "—"), "能力": v["text"]}
                  for role, v in ROLE_REFERENCE.items()
                  if role in _placement or role == "パーソン"])



if __name__ == "__main__":
    st.set_page_config(page_title="惨劇RoopeR リプレイビューア", layout="wide")
    render_viewer()