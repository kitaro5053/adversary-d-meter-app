# -*- coding: utf-8 -*-
"""開発者用：負け筋ネットワーク（arena/losstree.py）のレビュー閲覧ページ（DP-1 Stage 1）。

目的＝ユーザー/手練れ協力者がスマホからネットワークの完全性レビューをできるようにする
（抜けている負け筋・供給源・折り手の指摘を集める）。表示のみ・ゲーム状態には触れない。
"""
from __future__ import annotations

import streamlit as st

from arena.losstree import (LOSS_NODES, SUPPLY_SOURCES, UNREST_SOURCES,
                            SK_SOURCES, REMOVAL_TOOLS, GOODWILL_DEFENSE_NOTES,
                            ANYAKU_COUNTER_ABILITIES, UNREST_COUNTER_ABILITIES,
                            RESOURCE_LIMITS, TOP_CLASSES, to_mermaid)

_DIFF_MARK = {"易": "🟢易", "中": "🟡中", "難": "🟠難", "不可": "⛔不可"}
_PROG_MARK = {"恒久": "🩹恒久", "改善": "📈改善", "当日": "⏳当日"}


def _eval_current_position(state, script):
    """現局面（state, script）を evaluate_tree に通して list[NodeStatus] を返す。
    ゲーム状態が無い/失敗したら None（＝呼び出し側は静的表示にフォールバック）。DP-1 Stage 3。"""
    try:
        from agents.belief import Belief
        from arena.losstree_eval import evaluate_tree
        from sim.legal import set_card_options
        from sim.views import protagonist_view
        view = protagonist_view(state, "p1")
        opts: list = []
        for s in ("p1", "p2", "p3"):
            opts += set_card_options(state, s)
        bel = Belief(script.cast, [{"day": i.day, "name": i.name} for i in script.incidents],
                     script.set_name)
        bel.observe(state.history)
        return evaluate_tree(view, bel, options=opts)
    except Exception:  # noqa: BLE001  評価不能でも静的表示は出す（DP-1は表示専用＝壊さない）
        return None


def render_losstree_statuses(statuses, *, mobile: bool = False, key_prefix: str = "") -> None:
    """★DP-1 Stage 3（全数表示）：全負け筋ノードを status 付きで表示する（両表示側で共用）。

    - status→色：script_impossible/pruned/gap→**0️⃣ゼロ**（reason をツールチップ）／active→実在度で
      濃淡／covered→緑／**dormant→⚪未活性**（B-39）。
      **ゼロも消さず表示**（穴の可視化が DP-1 の本丸）＝トグルで畳めるだけ。
    - ★A-45（B-39のview側追随）＝`dormant`（まだ立っていない＝監視のみ）は
      **gray=False の既定表示のまま**にする：ゼロ系（もう起きない）とも covered（折れている）とも
      違い「これから立ちうる」＝畳んで隠すと監視対象を見失う。畳みたい要望が出たら
      専用トグルを足す（今回は不要＝FableA指示）。
    - フッター自己開示＝全ノード中の各 status 件数を常時表示。
      ★A-43②＝`gap` はユーザー向けフッターから外した（内部語「未接続」を見せないため）。
      件数は `status_counts` で引き続き取得可能＝開発側の穴の可視化は失われていない。
    - race 枠は先行実装（2a は常に None）＝AIB の 2b で自動有効化。
    statuses＝evaluate_tree(view, belief, options) の返り値（list[NodeStatus]）。
    """
    import streamlit as st

    from arena.losstree_eval import (node_status_line, race_badge,
                                     self_disclosure_line, status_vis)
    by_id = {ns.node_id: ns for ns in statuses}
    show_gray = st.checkbox(
        "0️⃣ ゼロ（消えた／脚本上ありえない）も表示", value=True,
        key=f"{key_prefix}lt_show_gray",
        help="OFFにすると、この脚本ではありえない・beliefで消えたノードを畳みます。"
             "★既定ON＝ゼロになった負け筋も隠さないのが DP-1 の目的。")
    for cls_id, cls in TOP_CLASSES.items():
        nodes = [(nid, n) for nid, n in LOSS_NODES.items() if nid.split(".")[0] == cls_id]
        if not nodes:
            continue
        _rows = []
        for nid, n in nodes:
            ns = by_id.get(nid)
            if ns is None:
                continue
            if not show_gray and status_vis(ns.status)["gray"]:
                continue
            _rows.append((nid, n, ns))
        if not _rows:
            continue
        st.markdown(f"**{cls['label']}**")
        for nid, n, ns in _rows:
            line = node_status_line(ns, n["label"])
            # reason はツールチップ相当＝caption で近接表示（Streamlit に hover tooltip が無いため）。
            with st.expander(line):
                st.caption(f"{race_badge(ns.race)}status：`{ns.status}`　"
                           f"実在度：{ns.prob:.0%}　／　{ns.reason}")
                st.markdown(_node_md(nid, n))
    # ★フッター自己開示（DP-1 §3 の本丸）。
    st.info("🔎 " + self_disclosure_line(statuses))


def _def_line(d: dict) -> str:
    return f"🛡 {d['label']}　`{_PROG_MARK.get(d['prognosis'], d['prognosis'])}`"


def _node_md(nid: str, n: dict) -> str:
    lines = ["**成立条件（AND＝全成立で発火・どれか1つ折れば防げる）**"]
    for c in n["conds"]:
        lines.append(f"- {c['label']}")
        for d in c["defenses"]:
            lines.append(f"    - {_def_line(d)}")
        _REF_LINES = {
            "SUPPLY": "🔻 →『§A 暗躍供給マップ』で供給を断つ",
            "UNREST": "🔻 →『§B 不安供給マップ』で臨界到達を防ぐ（冷却/供給断）",
            "SK": "🔻 →『SK供給源』（配役SK／ウイルスSK化）",
            "REMOVAL": "🧰 →『排除ツール箱』（事件兵器化/A.I./SK圏送り/異世界人/幻想/隔離/ピン）",
        }
        for ref in c.get("refs", []):
            lines.append(f"    - {_REF_LINES.get(ref, ref)}")
        if c.get("situations"):
            lines.append("    - 📐 **位置関係→折り手**：")
            for sit in c["situations"]:
                lines.append(f"        - **{sit['pos']}**：{sit['tools']}")
    if n.get("note"):
        lines.append(f"\n📝 note：{n['note']}")
    g = n.get("gate", {})
    if g:
        lines.append(f"\n🚪 この脚本でありうる条件：`{g}`")
    lines.append(f"\n出典KB：`{n['kb']}`　／　ノードID：`{nid}`")
    return "\n".join(lines)


def render_losstree_view(mobile: bool = False, *, state=None, script=None) -> None:
    st.title("🛠 開発者用：負け筋ネットワーク（レビュー版）")
    st.caption(
        "惨劇RoopeR FS/BTX の全負け筋→成立条件→折り手のネットワーク（DP-1 Stage 1 v0.2）。"
        "**レビュー観点＝抜けている負け筋・供給源・折り手はないか。**"
        "指摘は「ノードID＋内容」の形で開発者チャットへ（例：`kp.killer に◯◯の折り手が抜けている`）。"
        "予後タグ＝🩹恒久（抜本治療）／📈改善（状況が良くなる）／⏳当日（その日を凌ぐだけ）。")

    # ★DP-1 Stage 3：現局面（state/script）が渡されていれば evaluate_tree で status を出す。
    _statuses = _eval_current_position(state, script) if (state is not None and script) else None

    _tabs = ["🟥 負け筋", "🧰 SK供給・排除", "🔻 供給（§A/§B）", "💚 友好能力（§D）", "📏 資源", "🗺 全体図"]
    if _statuses is not None:
        _tabs.insert(0, "📊 現局面の判定")     # 現局面があるときだけ先頭に status タブ
    _t = st.tabs(_tabs)
    if _statuses is not None:
        with _t[0]:
            st.caption("この対局の**現在の局面**で、各負け筋が active（実在）／covered（折れている）／"
                       "⚪未活性＝まだ立っていない（監視のみ）／"
                       "0️⃣ゼロ（消えた／脚本上ありえない）のどれか＝**全数**を出す（隠さない）。")
            render_losstree_statuses(_statuses, mobile=mobile, key_prefix="dev_")
        tab_loss, tab_tool, tab_sup, tab_gw, tab_res, tab_map = _t[1:]
    else:
        tab_loss, tab_tool, tab_sup, tab_gw, tab_res, tab_map = _t

    with tab_loss:
        shown: set[str] = set()
        for cls_id, cls in TOP_CLASSES.items():
            nodes = {k: v for k, v in LOSS_NODES.items() if k.split(".")[0] == cls_id}
            st.subheader(cls["label"])
            for nid, n in nodes.items():
                shown.add(nid)
                diff = _DIFF_MARK.get(n["difficulty"], n["difficulty"])
                with st.expander(f"{diff}　{n['label']}"):
                    st.markdown(_node_md(nid, n))
        rest = {k: v for k, v in LOSS_NODES.items() if k not in shown}
        if rest:
            st.subheader("BTX固有の連鎖（§C）")
            for nid, n in rest.items():
                diff = _DIFF_MARK.get(n["difficulty"], n["difficulty"])
                with st.expander(f"{diff}　{n['label']}"):
                    st.markdown(_node_md(nid, n))

    with tab_tool:
        st.subheader("SK供給源（kp.sk / fr.sk が参照）")
        st.caption("★ユーザーレビュー反映＝配役SKとウイルスSK化を別立て（冷却で解除できるのが決定的な違い）。")
        for nid, s in SK_SOURCES.items():
            with st.expander(s["label"]):
                body = [f"- {_def_line(d)}" for d in s["defenses"]]
                body.append(f"- 📝 {s['note']}")
                body.append(f"- 出典KB：`{s['kb']}`　／　ID：`{nid}`")
                st.markdown("\n".join(body))
        st.subheader("🧰 排除ツール箱（脅威役の排除・隔離＝複数の負け筋で使い回す）")
        st.caption("★ユーザーレビュー反映＝排除系の共通整理。旧表記の「黒猫」は誤解を招くため削除"
                   "（排除手段はSK圏送り＝強制殺害の利用）。")
        for nid, t in REMOVAL_TOOLS.items():
            with st.expander(f"{t['label']}　`{_PROG_MARK[t['prognosis']]}`"):
                st.markdown(f"- 📝 {t['note']}\n- 出典KB：`{t['kb']}`　／　ID：`{nid}`")

    with tab_sup:
        st.subheader("§A 暗躍供給マップ（目標地点に暗躍を載せる経路）")
        st.caption("★暗躍禁止で止まるのは行動解決の暗躍カードだけ。")
        for nid, s in SUPPLY_SOURCES.items():
            diff = _DIFF_MARK.get(s["difficulty"], s["difficulty"])
            body = []
            if s.get("defense"):
                body.append(f"- 🛡 {s['defense']}")
            if s.get("na"):
                body.append(f"- ⛔ {s['na']}")
            body.append(f"- 出典KB：`{s['kb']}`　／　ID：`{nid}`")
            with st.expander(f"{diff}　{s['label']}"):
                st.markdown("\n".join(body))
        st.subheader("🛡 暗躍を取り除ける友好能力（網羅・発動条件つき）")
        st.caption("★ボード暗躍を除去できるのは下記boardの2つ**だけ**（v0.10監査で確定）。")
        st.markdown("**ボード暗躍：**")
        for a in ANYAKU_COUNTER_ABILITIES["board"]:
            st.markdown(f"- {a}")
        st.markdown("**キャラ暗躍：**")
        for a in ANYAKU_COUNTER_ABILITIES["character"]:
            st.markdown(f"- {a}")
        st.subheader("§B 不安供給マップ（犯人を臨界に届かせる経路）")
        st.caption("★冷却（不安-1）は最大3枚/ループ＝連続供給には数で負けうる。"
                   "★BTXでは不安≥3がメインラバーズ解禁・ウイルスSK化も起動。")
        for nid, u in UNREST_SOURCES.items():
            diff = _DIFF_MARK.get(u["difficulty"], u["difficulty"])
            with st.expander(f"{diff}　{u['label']}"):
                st.markdown(f"- 🛡 {u['defense']}\n- 出典KB：`{u['kb']}`　／　ID：`{nid}`")
        st.subheader("🛡 不安を取り除ける友好能力（網羅・発動条件つき）")
        for a in UNREST_COUNTER_ABILITIES:
            st.markdown(f"- {a}")

    with tab_gw:
        st.subheader("§D 友好能力マップ（防御資源としての友好能力・網羅）")
        st.caption("コスト（♡）と1/L制限は engine.data.GOODWILL_ABILITIES（単一ソース）から表示。"
                   "★拒否ルール＝脚本家が拒否できるのは行使キャラの役職が友好無視/絶対友好無視のときのみ。"
                   "イレギュラー・ナースの能力は拒否不可（KB: 20）。")
        try:
            from engine.data import GOODWILL_ABILITIES
        except Exception:  # noqa: BLE001
            GOODWILL_ABILITIES = {}
        for chara, note in GOODWILL_DEFENSE_NOTES.items():
            abilities = GOODWILL_ABILITIES.get(chara, [])
            cost = "・".join(
                f"『{a.get('name', '?')}』♡{a.get('hearts', '?')}"
                + ("（1/L）" if a.get("once_per_loop") else "")
                for a in abilities) or "（engine.dataに未登録）"
            with st.expander(f"〈{chara}〉　`{_PROG_MARK.get(note['prognosis'], '')}`"):
                st.markdown(f"- 🛡 防御用途：{note['use']}\n- ♡コスト：{cost}\n"
                            f"- 関連ノード：`{'、'.join(note['nodes'])}`")

    with tab_res:
        st.subheader("継続効果・排他（防御の資源制約）")
        for k, v in RESOURCE_LIMITS.items():
            st.markdown(f"- **{k}**：{v}")
        st.caption("正典＝docs/負け筋防御ツリー.md の制約表（KB出典付き完全版）。")

    with tab_map:
        st.caption("mermaid描画（クライアント側CDN）。重い場合は負け筋タブの一覧で確認を。"
                   + ("　★現局面の判定で 0️⃣ゼロ（消えた／ありえない）を反映。"
                      if _statuses is not None else ""))
        # ★DP-1 Stage 3：現局面があれば status をグレーアウトに反映（list→dict 橋渡し）。
        if _statuses is not None:
            from arena.losstree_eval import mermaid_status_map
            _mermaid = to_mermaid(statuses=mermaid_status_map(_statuses))
        else:
            _mermaid = to_mermaid()
        try:
            import streamlit.components.v1 as components
            components.html(
                '<script type="module">'
                'import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";'
                'mermaid.initialize({startOnLoad:true, securityLevel:"loose"});'
                "</script>"
                f'<pre class="mermaid">{_mermaid}</pre>',
                height=380 if mobile else 720, scrolling=True)
        except Exception:  # noqa: BLE001  CDN不可でもソース表示で用は足す
            pass
        with st.expander("mermaidソース（mermaid.live に貼って拡大表示できます）"):
            st.code(_mermaid, language="text")
