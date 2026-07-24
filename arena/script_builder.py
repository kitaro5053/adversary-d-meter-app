"""自作脚本のビルダー＋難易度評価ページ（評価器×ビルダー接続）。

起動:  streamlit run arena/script_builder.py

フォームで脚本（セット・ルールYX・キャスト・配役・事件）を組み、validate_script で
静的検証（違反は創作せず要確認＝エラー表示）→ arena.difficulty.evaluate_script で
3指標（ルール確定ループ数／敗北条件全把握ループ数／完全情報クリアループ数）＋通常勝率を
測って表示する。自作脚本のバランス調整（緩すぎ・キツすぎ・構造的必勝の検出）に使う。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.difficulty import evaluate_script, verdict  # noqa: E402
from sim.generator import SAFE_CAST_POOL  # noqa: E402
from sim.state import (  # noqa: E402
    BTX_INCIDENTS,
    BTX_RULE_X_ROLES,
    BTX_RULE_Y_ROLES,
    FS_INCIDENTS,
    FS_RULE_X_ROLES,
    FS_RULE_Y_ROLES,
    ROLE_MAX,
    Incident,
    Script,
    validate_script,
)

st.set_page_config(page_title="脚本ビルダー＋難易度評価", layout="wide")
st.title("📜 脚本ビルダー＋難易度評価")
st.caption("脚本を組んで「人間同等の主人公にとってどれくらい難しいか」を実測する。"
           "完全情報クリアが平均1前後なら健全、3以上や0%は設計を疑う（AIは人間より弱いので"
           "絶対値は上振れ＝脚本同士の相対比較と構造検出に使う）。")

UNSET = "（選択してください）"

# ---------------------------------------------------------------------------
# サイドバー：ルールと評価設定
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("ルール")
    set_name = st.radio("セット", ["FS", "BTX"], key="sb_set", horizontal=True)
    if set_name == "FS":
        y_tbl, x_tbl, inc_names = FS_RULE_Y_ROLES, FS_RULE_X_ROLES, sorted(FS_INCIDENTS)
    else:
        y_tbl, x_tbl, inc_names = BTX_RULE_Y_ROLES, BTX_RULE_X_ROLES, sorted(BTX_INCIDENTS)

    rule_y = st.selectbox("ルールY", list(y_tbl), key=f"sb_y_{set_name}")
    rule_x = st.selectbox("ルールX", list(x_tbl), key=f"sb_x_{set_name}")
    rule_x2 = None
    if set_name == "BTX":
        x2_opts = [x for x in x_tbl if x != rule_x]
        rule_x2 = st.selectbox("ルールX（2つ目）", x2_opts, key=f"sb_x2_{set_name}")

    loops = int(st.number_input("ループ数", 1, 7, 3, key="sb_loops"))
    days = int(st.number_input("1ループの日数", 1, 7, 3, key="sb_days"))

    st.header("評価設定")
    n_games = int(st.slider("評価ゲーム数", 2, 20, 8, key="sb_games",
                            help="多いほど正確・遅い"))
    probe_loops = int(st.slider("プローブループ数（情報速度測定）", 4, 12, 8, key="sb_probe"))

# ---------------------------------------------------------------------------
# キャストと配役
# ---------------------------------------------------------------------------
st.subheader("キャスト")
cast = st.multiselect("登場キャラクター", list(SAFE_CAST_POOL),
                      default=list(SAFE_CAST_POOL[:7]), key="sb_cast")

rule_xs = [rx for rx in (rule_x, rule_x2) if rx]
slots = Counter(y_tbl[rule_y])
for rx in rule_xs:
    slots += Counter(x_tbl[rx])
required = {r: min(n, ROLE_MAX.get(r, n)) for r, n in slots.items()}

st.subheader("配役")
st.caption(f"選択ルール（{'/'.join([rule_y, *rule_xs])}）が要求する役職スロット。"
           "残りのキャストは全員パーソン。")
role_picks: list[tuple[str, str]] = []   # (キャラ, 役職)
cols = st.columns(3)
i_col = 0
for role, n_req in sorted(required.items()):
    if role == "マイナス":
        n_req = int(cols[i_col % 3].number_input(
            f"マイナスの人数（0〜{n_req}）", 0, n_req, 0, key="sb_minus_n"))
    for i in range(n_req):
        label = f"{role}" + (f" {i + 1}人目" if n_req > 1 else "")
        pick = cols[i_col % 3].selectbox(label, [UNSET] + cast,
                                         key=f"sb_role_{set_name}_{role}_{i}")
        i_col += 1
        if pick != UNSET:
            role_picks.append((pick, role))

# 大物の縄張り（脚本作成時指定・全ループ固定＝現物確認済ルール）
territory = None
if "大物" in cast:
    territory = st.selectbox("大物の縄張りボード（脚本作成時に指定・全ループ固定）",
                             ["病院", "神社", "都市", "学校"], key="sb_territory")

# ★A-58：登場日／登場ループ（脚本作成時に指定）。キャストに該当キャラが居る時だけ出す。
#   転校生＝登場日が2日目以降になりうる（それまで盤上に居ない）／神格＝登場ループが2以降。
#   Script の entry_days / entry_loops に対応（検証は validate_script が範囲チェック）。
entry_days: dict[str, int] = {}
entry_loops: dict[str, int] = {}
if "転校生" in cast:
    _d = st.number_input("転校生の登場日（1＝初日から。2以上ならその日まで盤上に居ない）",
                         min_value=1, max_value=int(days), value=1, step=1,
                         key="sb_entry_day_転校生")
    if int(_d) > 1:
        entry_days["転校生"] = int(_d)
if "神格" in cast:
    _l = st.number_input("神格の登場ループ（1＝最初から。2以上ならそのループまで盤上に居ない）",
                         min_value=1, max_value=int(loops), value=1, step=1,
                         key="sb_entry_loop_神格")
    if int(_l) > 1:
        entry_loops["神格"] = int(_l)

# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------
st.subheader("事件")
n_inc = int(st.number_input("事件の数", 0, days, min(2, days), key="sb_n_inc"))
incidents: list[Incident] = []
for i in range(n_inc):
    c1, c2, c3 = st.columns(3)
    d = int(c1.selectbox(f"事件{i + 1}: 日", list(range(1, days + 1)),
                         index=min(i, days - 1), key=f"sb_inc_day_{i}"))
    nm = c2.selectbox(f"事件{i + 1}: 種類", inc_names, key=f"sb_inc_name_{set_name}_{i}")
    cu = c3.selectbox(f"事件{i + 1}: 犯人", [UNSET] + cast, key=f"sb_inc_culp_{i}")
    if cu != UNSET:
        incidents.append(Incident(day=d, name=nm, culprit=cu))

# ---------------------------------------------------------------------------
# 組み立て → 検証 → 評価
# ---------------------------------------------------------------------------
def _build_script() -> Script:
    """フォーム入力から Script を組む（検証は validate_script に委ねる）。"""
    dup = [n for n, c in Counter(p for p, _r in role_picks).items() if c > 1]
    if dup:
        raise ValueError(f"同じキャラに複数の役職: {dup}")
    unset_inc = n_inc - len(incidents)
    if unset_inc:
        raise ValueError(f"犯人が未選択の事件が{unset_inc}件ある")
    return Script(set_name=set_name, rule_y=rule_y, rule_x=rule_x, rule_x2=rule_x2,
                  loops=loops, days_per_loop=days, cast=list(cast),
                  roles=dict(role_picks), incidents=incidents,
                  # ★A-58：登場日/登場ループを渡す（従来は未接続でビルダーから指定できなかった）
                  entry_days=dict(entry_days), entry_loops=dict(entry_loops),
                  oomono_territory=territory)


try:
    script = _build_script()
    validate_script(script)
    script_ok = True
    st.success("脚本は正規（validate_script 通過）。評価できます。")
    # ★プレイ投入口用JSON（§2b・2026-07-13）：一人回し/脚本家プレイの『✍️ 自作脚本で始める』へ貼付。
    with st.expander("🎮 プレイ用JSON（この脚本で一人回し/脚本家プレイを始める）"):
        import json as _json
        from arena.gamelog import script_to_dict
        st.code(_json.dumps(script_to_dict(script), ensure_ascii=False, indent=1),
                language="json")
        st.caption("※配役・犯人を含む（脚本家専用）。この文字列をプレイ画面の投入口に貼ると自作脚本で遊べます。")
    # ★勝ち筋の即時分析（静的＝ゲーム評価不要）。脚本家の独立した勝ちルート＝主人公が
    #   別々の対策を要する数。1本＝キラー等ひとつ止めれば脚本家が勝てない薄い脚本。
    from sim.script_quality import (balance_grade, describe_win_paths,
                                     incident_feasibility_lines,
                                     incident_feasibility_verdict)
    _bg = balance_grade(script)
    _icon = "⚠" if _bg.n_paths <= 1 else ("△" if _bg.n_paths == 2 else "✓")
    st.markdown(f"**{_icon} 脚本家の勝ち筋（独立した敗北ルート）：{_bg.n_paths}本**")
    for _p in describe_win_paths(script):
        st.markdown(f"- {_p}")
    if _bg.n_paths <= 1:
        st.warning("勝ち筋が実質1本＝主人公が1つの対策で全部防げる薄い脚本です。"
                   "殺害系事件（殺人事件・遠隔殺人）を敗北条件に絡める、盤面敗北ルールを足す、"
                   "シリアルキラー/メインラバーズ等の別の殺害手段を入れる、などで独立ルートを増やせます。")
    # ★事件の発生しやすさ（不安会計）＝脚本家が犯人に臨界まで不安を積めるか。ユーザー要望 2026-07-06。
    _inc_lines = incident_feasibility_lines(script)
    if _inc_lines:
        st.markdown("**🎲 事件の発生しやすさ（不安会計）**　🟢確実／🟡拮抗（主人公の不安-1で止まる）／🔴困難（打点不足）")
        for _l in _inc_lines:
            st.markdown(f"- {_l}")
        _iv = incident_feasibility_verdict(script)
        if _iv:
            (st.warning if _iv.startswith("⚠") else st.info)(_iv)
    # ★詰み判定（レース/被覆解析）：初期局面がこのループで確定勝ち/防衛可/読み合いか。
    from sim.loop_race import analyze_script as _race_script, describe_report as _race_desc
    _rep = _race_script(script)
    _rl = _race_desc(_rep)
    _rbox = {"mastermind": st.error, "protagonist": st.success,
             "contested": st.warning}.get(_rep.verdict, st.info)
    st.markdown("**🎯 詰み判定（レース/被覆解析・初期局面）**")
    _rbox(_rl[0] if _rl else "（判定なし）")
    for _l in _rl[1:]:
        st.markdown(_l)
except ValueError as e:
    script_ok = False
    st.warning(f"脚本が未完成/違反: {e}")

if st.button("🎯 この脚本の難易度を評価する", key="sb_eval",
             disabled=not script_ok, type="primary"):
    with st.spinner(f"評価中（{n_games}ゲーム×3系統＝しばらくかかります）…"):
        report = evaluate_script(script, n_games=n_games, probe_loops=probe_loops)
    st.session_state["sb_report"] = report
    st.session_state["sb_report_label"] = f"{rule_y} × {'/'.join(rule_xs)}"

# ---------------------------------------------------------------------------
# 結果表示（session_state に保持＝再描画で消えない）
# ---------------------------------------------------------------------------
report = st.session_state.get("sb_report")
if report:
    st.divider()
    st.subheader(f"評価結果: {st.session_state.get('sb_report_label', '')}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ルール確定ループ数",
              f"{report['rule_pin_loop_avg'] if report['rule_pin_loop_avg'] is not None else '—'}",
              f"確定率 {report['rule_pin_rate']:.0%}", delta_color="off")
    c2.metric("敗北条件 全把握",
              f"{report['loss_known_loop_avg'] if report['loss_known_loop_avg'] is not None else '—'}",
              f"到達率 {report['loss_known_rate']:.0%}", delta_color="off")
    c3.metric("完全情報クリア",
              f"{report['perfect_clear_loop_avg'] if report['perfect_clear_loop_avg'] is not None else '—'}",
              f"クリア率 {report['perfect_clear_rate']:.0%}", delta_color="off")
    c4.metric("脚本家勝率（通常）", f"{report['mm_winrate_normal']:.0%}",
              f"最後の戦い勝ち {report['perfect_fb_win_rate']:.0%}", delta_color="off")
    if report.get("won_before_pin_rate"):
        st.caption(f"※ルール確定前に主人公が勝ち抜けたゲーム {report['won_before_pin_rate']:.0%}"
                   "（推理不要だった＝確定率とあわせて見る）")
    st.markdown("**読み解き（verdict）**")
    for line in verdict(report).splitlines():
        st.markdown(f"- {line}")
    st.caption("評価AI（belief主人公＋mm_v1/mm_v2）は人間より弱いので絶対値は上振れする。"
               "脚本同士の相対比較・「完全情報でも勝てない＝構造的に脚本家必勝」の検出に使う。")
