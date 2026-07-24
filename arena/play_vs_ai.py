# -*- coding: utf-8 -*-
"""人間＝脚本家 vs AI主人公モード（2026-07-09）— 主人公AIへのフィードバック強化。

現行の一人回し（arena/play.py＝人間が主人公・AI脚本家）の逆。人間が脚本家として
主人公AI3席に挑む。UIは一人回しと同じ部品（board_viz・replay の盤面JSON）を再利用し、
役職説明より下に**主人公AIの内省パネル**（arena/introspect＝belief可視化）を出す＝
「AIが今どう配役を読み、何を護ろうとしているか」を見ながら脚本家が仕掛けられる。

play_interactive は human_seats が任意＝human_seats={"mastermind"} で成立する（AIB確認）。
脚本家は mastermind_view＝全情報が見える（配役を知って仕掛ける側なので正しい）。

★Streamlit非依存の核（テスト可能）：
- `run_to_pending`：choices を消費して次の脚本家入力待ち／終局まで進める。
- `mind_markdown`：現局面の主人公AI内省を markdown 化。
描画（`render_play_vs_ai`）は薄いラッパ。app.py のモード切替への配線は AIA レーン（申し送り）。
"""

from __future__ import annotations

from dataclasses import replace

from agents import HeuristicMastermind  # noqa: F401  (脚本家UI選択の互換用)
from agents.debug import ProbedProtagonist
from agents.heuristic_protagonist import HeuristicProtagonist
from arena.interactive import PendingHuman, ReplayDesync, play_interactive
from arena.introspect import belief_snapshot, estimates_from_records, render_mind_md

MM_SEAT = {"mastermind"}

# ★4ループ打ち切り（ユーザー/AIC 2026-07-11）の基準ループ数＝ヘッダの分数表示の分母。
#   「もう少し遊んでやる」で実ループ上限は延びるが、分母はこの基準4で固定＝5周目に入ったら
#   5/4 と表示する（ユーザー要望 2026-07-12）。
MMV_BASE_LOOPS = 4


def _ai_protagonists(seed: int, probed: bool = False) -> dict:
    hp = ProbedProtagonist(seed) if probed else HeuristicProtagonist(seed)
    return {"p1": hp, "p2": hp, "p3": hp}


def run_to_pending(script, seed: int, choices: list[dict], loops: int = 4,
                   ai_replay: dict | None = None, *, initial_state=None):
    """人間（脚本家）の選択列 choices を消費し、次の脚本家入力待ち／終局まで進める。

    返り値 (state, pending, log, ai):
      pending＝PendingHuman（脚本家の未入力）or None（終局）。
      ai＝主人公AI（ProbedProtagonist・records に各決定の内部推定/候補スコア）。
      state.day_snaps＝日境界の局面 {(loop,day): snapshot}（クラウド保存の起点・Phase 1）。
      state.snap_desynced＝クラウド復元で当日分の再生がずれ、日開始へ退行したか（UIが警告する）。
    ★initial_state（Phase 1・2026-07-17）＝クラウド復元の起点（日境界snapshot）。既定 None＝不変。
    ★4ループ打ち切り（ユーザー/AIC 2026-07-11）：既定 loops=4。最後の戦いは行わない
     （final_battle=False）＝AI主人公が4ループ内に守れなければ脚本家の勝ちで終える。
     「もう少し遊んでやる」で loops を延長できる（呼び出し側が loops を増やす）。
    """
    probe = replace(script, loops=loops)
    ai = _ai_protagonists(seed, probed=True)
    hp = ai["p1"]                      # 3席は同一インスタンス＝records は全席分
    used = list(choices)
    # ★保存局面のずれ（生成コーパス変更等で同一seedが別脚本になる）に強く：非合法になった
    #   手以降を切り詰めて、合法だった局面から再開する（クラッシュさせない）。切り詰めた手数は
    #   state.replay_dropped に載せ、UI が session の手順を更新＋通知する。
    dropped = 0
    # ★Supabase Phase 1（2026-07-17）：クラウド復元中は日境界snapshotが起点（§1 rev2）。
    #   day_snaps＝UIが「保存の起点」を掴むための日境界局面（PendingHuman.state は日境界でない）。
    day_snaps: dict = {}
    snap_desynced = False

    def _grab_day(_s) -> None:
        day_snaps[(_s.loop_no, _s.day)] = _s.to_snapshot()

    def _tag(_state):
        _state.replay_dropped = dropped
        _state.replay_used_choices = used
        _state.day_snaps = day_snaps
        _state.snap_desynced = snap_desynced
        return _state

    while True:
        try:
            state, log = play_interactive(probe, ai, MM_SEAT, used,
                                          final_battle=False, ai_replay=ai_replay,
                                          initial_state=initial_state, on_day_start=_grab_day)
            return _tag(state), None, log, hp
        except PendingHuman as p:
            return _tag(p.state), p, p.log, hp
        except ReplayDesync as d:
            if initial_state is not None:
                # ★desyncグレースフル退行（FableA必須条件・2026-07-17）：クラウド復元で「当日分の
                #   再生」がビルド差でずれたら**日開始へ退行**（当日分を全部捨てる）＝黙って別の
                #   局面を出さない（A-27と同じ fail-ignore 原則）。当日のAI決定も再現できない＝
                #   ai_replay も捨てて live AI が打ち直す。過去日は snapshot が保持＝失われない。
                dropped = len(choices)
                used, ai_replay = [], None
                snap_desynced = True
                continue
            dropped = len(choices) - len(d.valid_choices)
            used = list(d.valid_choices)   # 合法だった手までで再実行（決定的＝再ずれしない）


def _public_incidents(script):
    return [{"day": i.day, "name": i.name} for i in script.incidents]


def incident_choice_label(state, pending) -> str:
    """事件効果の対象選択に「事件名＋その選択が何か」を添える文言（ユーザー要望 2026-07-11）。

    多段の事件（不安拡大＝不安+2→暗躍+1／流布＝友好-2→友好+2／蝶の羽ばたき＝種別→対象）は
    どの段かを、このターンの事件フェイズで既に適用済みの効果（公開履歴）から判定する。
    sim 側の decide は UI非依存（対象/種別だけ）なので、この対応付けは UI 層で行う。
    """
    inc = next((i for i in state.script.incidents if i.day == state.day), None)
    name = inc.name if inc else "事件"
    opts = pending.options or []
    if opts and "kind" in opts[0]:      # 蝶の羽ばたき 第1段：置くカウンターの種別
        return f"⚡ {name}：置くカウンターの種類を選ぶ（暗躍／不安／友好）"
    ev = [e for e in state.history
          if e.get("loop") == state.loop_no and e.get("day") == state.day
          and e.get("phase") == "incident"]
    if name == "不安拡大":               # 不安+2する対象 → 暗躍+1する対象
        done = any(e.get("event") == "unrest" and e.get("delta") == 2 for e in ev)
        return "⚡ 不安拡大：暗躍+1する対象を選ぶ" if done else "⚡ 不安拡大：不安+2する対象を選ぶ"
    if name == "流布":                   # 友好-2する対象 → 友好+2する対象
        done = any(e.get("event") == "goodwill" and e.get("delta") == -2 for e in ev)
        return "⚡ 流布：友好+2する対象を選ぶ" if done else "⚡ 流布：友好-2する対象を選ぶ"
    if name == "蝶の羽ばたき":            # 第2段：カウンターを置く対象
        return "⚡ 蝶の羽ばたき：カウンターを置く対象を選ぶ（犯人と同エリア）"
    if name == "殺人事件":
        return "⚡ 殺人事件：殺害する対象を選ぶ（犯人と同エリア）"
    if name == "遠隔殺人":
        return "⚡ 遠隔殺人：殺害する対象を選ぶ（暗躍2以上）"
    if name == "行方不明":
        return "⚡ 行方不明：犯人を移動させる先のボードを選ぶ"
    return f"⚡ {name}：事件効果の対象を選ぶ"


# ★A-32（2026-07-17）：**対局に紐づく session_state キーの単一の登録先**（脚本家プレイ）。
#   別の対局へ切替える全ての入口（新規/自作脚本/棋譜/.rooper.json/URL ?g=/クラウド続き）は
#   _reset_mmv_game() を通す＝ここに1行足せば全経路に効く。個別サイトへの配線し忘れ
#   （＝A-32の原因。play.py 側と同型）を構造的に潰す。
_MMV_GAME_KEYS: tuple[str, ...] = (
    # 対局の同一性
    "mmv_script", "mmv_script_obj", "mmv_seed", "mmv_choices",
    "mmv_kifu", "mmv_load_warning", "mmv_ai_replay",
    # 進行に紐づくフラグ・カーソル
    "mmv_extra_loops", "mmv_end_logged", "mmv_revealed", "mmv_choice_len",
    # クラウド続き（Phase 1/2）＝★A-32で登録漏れが露見した群
    "mmv_snap_state", "mmv_snap_ai_replay", "mmv_snap_warn",
    "mmv_snap_desync_shown", "mmv_snap_mode_note", "mmv_snap_token", "mmv_auto_digest",
    # ★A-39：共有コード（棋譜）は撤去＝キーも登録簿から除去（集約簿の簡素化）。
)
# ★意図的に消さないもの：mmv_auto_token＝1セッション×1 auto スロット（提案書§1）／
#   mmv_show_mind 等の表示設定＝対局に紐づかない。


def _reset_mmv_game(**overrides) -> None:
    """対局に紐づく状態を全消しし、渡された分だけ設定する（＝別の対局へ切替える入口の共通前処理）。"""
    import streamlit as st          # このモジュールの作法＝関数内 import（トップレベルに st は無い）
    for _k in _MMV_GAME_KEYS:
        st.session_state.pop(_k, None)
    st.session_state.update(overrides)


# 脚本名マスキング（#4・ネタバレ防止）：内部キー→花名エイリアス。セレクタは花名だけ
# 表示する（btx_future=未来改変プラン等が生キーで露出しないように）。表示専用の安定マップ。
_SCRIPT_ALIASES: dict[str, str] = {
    "basic": "FS・菫（すみれ）", "guard": "FS・向日葵（ひまわり）",
    "revenge": "FS・彼岸花（ひがんばな）", "shrine": "FS・椿（つばき）",
    "fs5_guard": "FS5・水仙（すいせん）",
    "btx_seal": "BTX・薔薇（ばら）", "btx_future": "BTX・胡蝶蘭（こちょうらん）",
    "btx_bomb": "BTX・鈴蘭（すずらん）", "btx_contract": "BTX・桜（さくら）",
    "btx_lovers": "BTX・牡丹（ぼたん）", "btx5_seal": "BTX5・紫陽花（あじさい）",
    "btx5_future": "BTX5・百合（ゆり）",
}


def script_menu(sample_keys) -> dict:
    """脚本セレクタの表示ラベル→内部識別子のマップ（#4）。ランダム2種＋初心者2種＋花名。
    未登録キーは花名が無い＝『その他N』で伏せる（生キーを出さない）。Streamlit非依存。"""
    menu: dict[str, str] = {
        "🔰 メンバー調整FS": "__FS_BEGINNER__",
        "🔰 メンバー調整BTX": "__BTX_BEGINNER__",
        "🎲 ランダムFS": "__FS__", "🎲 ランダムBTX": "__BTX__",
    }
    n = 0
    for k in sample_keys:
        alias = _SCRIPT_ALIASES.get(k)
        if alias is None:
            n += 1
            alias = f"サンプル{n}"
        menu[alias] = k
    return menu


def _build_selected_script(key: str, seed: int, random_script, sample_scripts):
    """内部キー（__FS__/__BTX__/__*_BEGINNER__/サンプル名）から Script を生成。"""
    if key == "__FS__":
        return random_script("FS", seed, days=3)
    if key == "__BTX__":
        return random_script("BTX", seed, days=3)
    if key == "__FS_BEGINNER__":
        return random_script("FS", seed, days=3, beginner=True)
    if key == "__BTX_BEGINNER__":
        return random_script("BTX", seed, days=3, beginner=True)
    return sample_scripts[key]()


def group_mm_set_options(options: list[dict]) -> dict:
    """set_card の options を配置先（対象）ごとにまとめる（#1 カード/対象分離用）。
    返り値 {対象ラベル: [option,...]}。Streamlit非依存＝テスト可能。"""
    groups: dict[str, list[dict]] = {}
    for o in options:
        tgt, kind = o.get("target"), o.get("target_kind")
        key = f"{tgt}（{'ボード' if kind == 'board' else 'キャラ'}）"
        groups.setdefault(key, []).append(o)
    return groups


def resolve_mm_choice(options: list[dict], tgt_key: str, card_label: str):
    """配置先ラベル＋カードラベルから option を引く。**引けなければ None**（#5クラッシュ堅牢化）。

    ★A-27（2026-07-16）：配置先/カードのラベルが stale（ホット更新・連打・①切替直後の widget
    状態競合）なら None を返す＝呼び出し側はクリックを無視する。A-19/#5 はここで「グループ先頭」へ
    フォールバックしており、ユーザーが選んでいない手を黙って打っていた（主人公プレイ側の同型が
    本番で実害＝FB3 seed13 L1D2 のA-27）。index の ValueError 根絶（元の目的）は None 返しでも
    達成でき、誤操作は生まない。Streamlit非依存＝テスト可能。"""
    g = group_mm_set_options(options).get(tgt_key)
    if not g:
        return None
    # ★A-17：②Boxはカード名のみ（配置先は①Boxで選択済み＝重複表示を除去・主人公プレイと統一）。
    labels = [o["card"] for o in g]
    if card_label not in labels:
        return None
    return g[labels.index(card_label)]


def _label_index(labels: list, lbl) -> int | None:
    """ラベル lbl の index。labels 外（stale＝ホット更新や連打の widget 状態競合）なら **None**。

    ★A-27：A-19 は 0 へフォールバックしていたが、それは「ユーザーが選んでいない選択肢を黙って
    確定する」＝拒否/解決やカルティスト無視のような不可逆な決定まで勝手に決まる。None を返し、
    呼び出し側がクリックを無視すれば ValueError 根絶（A-19の目的）と誤操作防止を両立できる。
    純関数＝Streamlit非依存＝テスト可能。"""
    return labels.index(lbl) if labels and lbl in labels else None


def _pick_by_label(options: list, labels: list, key: str):
    """selectbox/radio の widget 値（st.session_state[key]）から option を引く。stale なら None。
    決定UIの各 commit（refuse/cultist/move2）で共用（A-19の堅牢化＋A-27の誤操作防止）。"""
    import streamlit as st
    i = _label_index(labels, st.session_state.get(key))
    return None if i is None else options[i]


def _commit_mm_choice(options: list, labels: list, key: str) -> None:
    """widget 値の option を mmv_choices へ。stale ならクリックを無視（A-27）＝何も確定しない。
    次の rerun で各描画側のガードが session_state を正すので、押し直せば通る（一過性）。"""
    import streamlit as st
    o = _pick_by_label(options, labels, key)
    if o is None:
        st.session_state["_mmv_stale_click"] = True
        return
    st.session_state["mmv_choices"].append(o)


def _warn_stale_click() -> None:
    """直前のクリックが stale で無視された時だけ注意を出す（A-27）。ボタンが無反応に見えるのを防ぐ。"""
    import streamlit as st
    if st.session_state.pop("_mmv_stale_click", False):
        st.warning("選択が古くなっていたため、この操作は取り消しました（誤った手を打たないための"
                   "安全装置です）。もう一度選び直してください。")


def mm_snapshot_json(snap: dict) -> dict:
    """フェイズ境界スナップショット→脚本家視点の盤面JSON。役職・全カードを見せる。
    ★主人公の伏せ札も中身を表示する（ユーザー要望 2026-07-10）：人間=脚本家の研究ツール
      では、AI主人公が何を置いたかを見られる方が学びになる（ビューアの神視点と同じ扱い）。
      ＝旧「視点の健全性」の再マスクは廃止。omniscient スナップショットをそのまま使う。
      ※対AIの公平性は sim/views._masked_placements（AI脚本家の view）が別途担保しており不変。"""
    from arena.replay import board_json_from_snapshot
    return board_json_from_snapshot(snap, omniscient=True)


def history_days_md(history: list[dict], *,
                    seen_len: int | None = None, reached=None) -> list[tuple]:
    """対局履歴を [(loop, day, その日のmarkdown), ...] に分けて返す（空の日は除外）。

    ★A-2（2026-07-13）：呼び出し側が日毎 expander で畳めるよう、日単位で返す共通形。
    表記・フェイズ見出し〘…〙・絵文字・新規🆕は共通レンダラ render_day_md に委譲（history_md と同一）。
    Streamlit非依存＝テスト可能。seen_len/reached の意味は history_md と同じ。
    """
    from arena.history_view import render_day_md
    if not history:
        return []
    # (loop, day) を出現順に（day=None のイベント＝loop_result 等は直前の日にぶら下げる）。
    days: list[tuple] = []
    seen: set = set()
    for e in history:
        ld = (e.get("loop"), e.get("day"))
        if ld[1] is None or ld in seen:
            continue
        seen.add(ld)
        days.append(ld)
    _sl = len(history) if seen_len is None else seen_len

    def _mk_reached(lp, dy):
        """その日の『発動無し／死者リスト』の出し方（reached 未指定時の既定）。
        行動解決(cards_revealed)が済めば能力フェイズに到達＝発動無しを出す。日が完了なら
        ターン終了フェイズ（死者リスト）も出す。★day0（ループ開始）や、事件/主人公能力フェイズで
        loop_end した日（ターン終了に到達していない）は死者リストを出さない。"""
        de = [e for e in history if (e.get("loop"), e.get("day")) == (lp, dy)]
        if dy is None or dy < 1:
            return lambda after: False   # ループ開始日は能力/ターン終了を出さない
        has_ar = any(e.get("event") == "cards_revealed" for e in de)
        has_te = any(e.get("phase") == "turn_end" for e in de)
        # 事件/主人公能力フェイズで loop_end＝ターン終了に到達せず終わった（KP即敗北等）。
        early_end = any(e.get("event") == "loop_end"
                        and e.get("phase") not in ("turn_end", "", None) for e in de)
        later = any((e.get("loop"), e.get("day")) > (lp, dy)
                    for e in history if e.get("day") is not None)
        complete = later or has_te or any(
            e.get("event") in ("loop_end", "loop_result", "final_battle") for e in de)

        def _r(after):
            if after in ("脚本家能力フェイズ後", "主人公能力フェイズ後"):
                return has_ar
            if after == "ターン終了フェイズ後":
                return has_te or (complete and not early_end)
            return True
        return _r

    out: list[tuple] = []
    for lp, dy in days:
        _rf = reached if reached is not None else _mk_reached(lp, dy)
        body = render_day_md(history, lp, dy, seen_len=_sl, reached=_rf)
        if body.strip():
            out.append((lp, dy, body))
    return out


def history_md(history: list[dict], max_events: int = 40, *,
               seen_len: int | None = None, reached=None) -> str:
    """対局履歴を「日ごと＋フェイズ見出し〘…〙＋絵文字＋新規🆕」の統一表記で markdown 化する（平置き）。

    ★2026-07-13：主人公プレイ(play.py)と経過の表記を統一（テスター要望）＝共通レンダラ
    arena/history_view.render_day_md に委譲。日毎に畳みたい呼び出し側は history_days_md を使う（A-2）。
    Streamlit非依存＝テスト可能。seen_len＝これ以降のイベントに🆕。reached＝空能力フェイズの発動無し表示可否。
    """
    parts = history_days_md(history, seen_len=seen_len, reached=reached)
    if not parts:
        return "_まだ記録がありません。_"
    out: list[str] = []
    for lp, dy, body in parts:
        out.append(f"**L{lp}D{dy}**")
        out.append("")
        out.append(body)
        out.append("")
    return "\n".join(out).strip("\n")


def _render_history_folded(history: list[dict]) -> None:
    """★A-2：経過（公開情報）を日毎 expander で畳む（主人公プレイ play.py と統一）。
    過去ループ→1つの expander にまとめ、現ループ→最新日は開き・それ以前の日は畳む。"""
    import streamlit as st
    st.markdown("**📢 経過（公開情報）**")
    parts = history_days_md(history)
    if not parts:
        st.markdown("_まだ記録がありません。_")
        return
    cur_loop = parts[-1][0]
    prev = [(lp, dy, b) for lp, dy, b in parts if (lp or 0) < (cur_loop or 0)]
    curr = [(lp, dy, b) for lp, dy, b in parts if (lp or 0) == (cur_loop or 0)]
    if prev:
        n_loops = len({lp for lp, _dy, _b in prev})
        with st.expander(f"過去のループ（{n_loops}ループ分）", expanded=False):
            for lp, dy, b in prev:
                st.markdown(f"**── L{lp}・{dy}日目 ──**")
                st.markdown(b, unsafe_allow_html=True)
    for i, (lp, dy, b) in enumerate(curr):
        if i == len(curr) - 1:   # 最新日は開いて見せる
            st.markdown(f"**🗓 L{lp}・{dy}日目 の経過**")
            st.markdown(b, unsafe_allow_html=True)
        else:                    # 現ループの過去日は畳む
            with st.expander(f"L{lp}・{dy}日目", expanded=False):
                st.markdown(b, unsafe_allow_html=True)


# 1ターンで刻まれるフェイズスナップショット数（脚本家行動前/主人公行動後/行動解決後/
# 脚本家能力後/主人公能力後/事件後/ターン終了後＝sim/flow.py の snapshot() 呼び出し）。
_REVIEW_BACK = 7

_PHASE_JP = {
    "mastermind_set": "脚本家行動フェイズ（3枚を伏せる）",
    "protagonist_set": "主人公行動フェイズ", "action_resolution": "行動解決フェイズ",
    "mastermind_ability": "脚本家能力フェイズ", "goodwill_ability": "主人公能力フェイズ",
    "goodwill_refuse": "主人公能力フェイズ（友好能力の拒否判断）",
    "incident": "事件フェイズ", "loop_start": "ループ開始",
    "loop_end": "ループ終了フェイズ", "turn_end": "ターン終了フェイズ",
    "final_battle": "最後の戦い", "loop_start_area": "ループ開始（登場位置の指定）",
    "scholar_counter": "ループ開始（学者のカウンター選択）",
}


def phase_label(state, pending) -> str:
    """現在どのフェイズかを人間可読で返す（#6）。pending の decision を優先し、
    無ければ state.phase から引く。Streamlit非依存＝テスト可能。"""
    dec = getattr(pending, "decision", None) if pending is not None else None
    ph = getattr(state, "phase", None)
    key = dec or ph
    jp = _PHASE_JP.get(key) or _PHASE_JP.get(ph) or (key or "—")
    return jp


def rules_reference_md(set_name: str, active_rules: set | None = None) -> str:
    """そのセットで使われうるルール一覧（役職追加＋効果）を markdown 化（#2）。
    active_rules に入っているルールは ★ で強調（脚本家は自分のルールが分かる）。
    play.py の RULE_Y_REFERENCE/RULE_X_REFERENCE を流用（無ければ簡易表示）。"""
    active = active_rules or set()
    try:
        from arena.play import RULE_X_REFERENCE, RULE_Y_REFERENCE
    except Exception:
        return "_（ルール一覧の参照に失敗）_"
    inset = "FS" if set_name == "FS" else "BTX"

    def _rows(ref, head):
        out = [f"**{head}**", "", "| ルール | 追加役職 | 効果 |", "|---|---|---|"]
        for name, v in ref.items():
            if inset in v.get("set", "") or "FS/BTX" in v.get("set", ""):
                star = "★" if name in active else ""
                roles = v.get("roles", "") or "—"
                out.append(f"| {star}{name} | {roles} | {v.get('text', '')} |")
        return out

    lines = _rows(RULE_Y_REFERENCE, "ルールY（主要な敗北条件）")
    lines += [""] + _rows(RULE_X_REFERENCE, "ルールX（副次ルール）")
    lines.append("\n_★＝この脚本で実際に使われているルール。_")
    return "\n".join(lines)


def encode_mmv_save(pick: str, seed: int, choices: list[dict]) -> str:
    """一人回し(脚本家)の局面を再現4点（脚本名・seed・choices）でJSON文字列化（#8）。
    AI種別は現状HeuristicProtagonist固定＝seedと脚本で完全再現できる。"""
    import json
    return json.dumps({"v": 1, "pick": pick, "seed": int(seed),
                       "choices": choices}, ensure_ascii=False)


def decode_mmv_save(blob: str) -> dict | None:
    """encode_mmv_save の逆。壊れていたら None（#8）。"""
    import json
    try:
        from arena.gamelog import normalize_legacy_tokens
        d = normalize_legacy_tokens(json.loads(blob))  # 旧トークン(anryaku/gonoki)を現行へ
        if not isinstance(d, dict) or "choices" not in d:
            return None
        return d
    except (ValueError, TypeError):
        return None


def encode_mmv_url(pick: str, seed: int, choices: list[dict]) -> str:
    """局面を URL ?g= 用の圧縮base64にする（#6・リロード/タブ復元で局面維持）。"""
    import base64
    import zlib
    raw = encode_mmv_save(pick, seed, choices).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")


def decode_mmv_url(blob: str) -> dict | None:
    """encode_mmv_url の逆。壊れていたら None（#6）。"""
    import base64
    import zlib
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(blob.encode("ascii")))
        return decode_mmv_save(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001  壊れたURLは無視して新規扱い
        return None


def _threshold_tag(name: str) -> str:
    """犯人の不安臨界の短縮表記（例 '女子学生:不臨3'）。取得不能なら名前だけ。"""
    try:
        from engine.data import unrest_threshold_of
        th = unrest_threshold_of(name)
    except Exception:
        th = None
    return f"{name}:不臨{th}" if th is not None else name


def _ref_tip(table: dict, key: str) -> str:
    """参照テーブル（RULE_Y/RULE_X/ROLE_REFERENCE）の1件をポップアップ本文へ整形。

    ★新規転記なし＝`sim/reference.py`（KB転記済み）の値をそのまま並べるだけ。
    キーが無ければ空文字＝`popup_span` 側が素のラベルに退行する（対局を壊さない）。
    """
    d = table.get(key)
    if not isinstance(d, dict):
        return ""
    order = ("text", "roles", "clause", "max", "set")
    labels = {"text": "", "roles": "関連役職", "clause": "条文", "max": "上限", "set": "セット"}
    out = []
    for k in order:
        v = d.get(k)
        if not v or v == "—":
            continue
        out.append(str(v) if not labels[k] else f"**{labels[k]}**：{v}")
    return "\n\n".join(out)


def _popup_css() -> str:
    """ポップアップ用CSS（A-34機構）。取得不能なら空＝素の表示に退行。"""
    try:
        from board_viz import popup_css
        return popup_css()
    except Exception:  # noqa: BLE001
        return ""


def _role_tip(role: str) -> str:
    """役職名＋条文ポップアップ。参照不能なら素の役職名（対局を壊さない）。"""
    try:
        from board_viz import popup_span
        from sim.reference import ROLE_REFERENCE
        return popup_span(role, _ref_tip(ROLE_REFERENCE, role))
    except Exception:  # noqa: BLE001
        return role


def _char_tip(name: str) -> str:
    """キャラ名＋カードテキストポップアップ（A-34の `_char_popup_span` を共用）。"""
    try:
        from board_viz import _char_popup_span
        return _char_popup_span(name, name)
    except Exception:  # noqa: BLE001
        return name


def script_sheet_md(script, *, popup: bool = False,
                    include_roles: bool = True) -> str:
    """脚本の内訳（ルールY/X・予定事件＋犯人:不安臨界・配役）を markdown 化。脚本家は全情報が
    見える側なので犯人・配役まで表示する。Streamlit非依存＝テスト可能。

    ★A-44：`popup=True` で各項目に説明ポップアップを付ける（A-34/A-37 と同じ `board_viz` 機構）。
    既定 False＝素のmarkdown（既存の呼び出し・テストの契約を変えない）。呼ぶ側は
    `popup_css()` の注入と `unsafe_allow_html=True` が要る。
    """
    if popup:
        try:
            from board_viz import popup_span
            from sim.reference import (ROLE_REFERENCE, RULE_X_REFERENCE,
                                       RULE_Y_REFERENCE)
            from sim.reference import INCIDENT_EFFECTS
        except Exception:  # noqa: BLE001  参照不能＝素の表示へ退行（A-44①の再発防止）
            popup = False

    def _y(s):
        return popup_span(s, _ref_tip(RULE_Y_REFERENCE, s)) if popup else s

    def _x(s):
        return popup_span(s, _ref_tip(RULE_X_REFERENCE, s)) if popup else s

    def _inc(s):
        return popup_span(s, INCIDENT_EFFECTS.get(s, "")) if popup else s

    def _role(s):
        return popup_span(s, _ref_tip(ROLE_REFERENCE, s)) if popup else s

    rxs = list(script.rule_xs)
    lines = [f"- **ルールY**：{_y(script.rule_y)}",
             "- **ルールX**：" + ("／".join(_x(r) for r in rxs) if rxs else "（なし）")]
    incs = sorted(script.incidents, key=lambda i: i.day)
    if incs:
        lines.append("- **予定事件**：")
        for i in incs:
            lines.append(f"    - Day{i.day}：{_inc(i.name)}"
                         f"（犯人：{_threshold_tag(i.culprit)}）")
    else:
        lines.append("- **予定事件**：（なし）")
    roles = (getattr(script, "roles", None) or {}) if include_roles else {}
    if roles:
        lines.append("- **配役**：")
        for n, r in sorted(roles.items(), key=lambda kv: kv[0]):
            lines.append(f"    - {n}：{_role(r)}")
    return "\n".join(lines)


def incident_effects_md(script) -> str:
    """予定事件の効果一覧（発生条件つき）を markdown 化（キャラ一覧の直下に表示する用）。
    効果テキストは sim/reference.INCIDENT_EFFECTS（KB転記）。Streamlit非依存＝テスト可能。"""
    try:
        from sim.reference import INCIDENT_EFFECTS
    except Exception:
        return ""
    incs = sorted(script.incidents, key=lambda i: i.day)
    if not incs:
        return "_予定事件なし。_"
    lines = ["**💥 予定事件の効果**（発生＝犯人が生存＋不安臨界以上）", ""]
    for i in incs:
        eff = INCIDENT_EFFECTS.get(i.name, "（要確認）")
        lines.append(f"- **Day{i.day}『{i.name}』**（犯人：{_threshold_tag(i.culprit)}）：{eff}")
    return "\n".join(lines)


def _defense_plan_md(state, script) -> str:
    """現局面で主人公AIが直面する脅威と最安の防御計画を markdown 化（advisory）。

    protagonist_view（p1）＋3席の手札在庫＋belief から脅威を列挙し、
    『負け筋→折る手／防御不能はレース対象』を表にする。engine の状態が無い
    （ループ準備前など）ときは空文字を返す。
    """
    try:
        from agents.belief import Belief
        from agents.defense_plan import (
            ANRYAKU_KINSHI_TURN_CAP, plan_for_belief, render_plan_md)
        from sim.legal import set_card_options
        from sim.views import protagonist_view
        view = protagonist_view(state, "p1")
        opts: list = []
        for s in ("p1", "p2", "p3"):
            opts += set_card_options(state, s)
        bel = Belief(script.cast, _public_incidents(script), script.set_name)
        bel.observe(state.history)
        # ★#B11：負け筋を実在度1%以下の低いものまで広く表示（表示専用＝AIの意思決定既定は不変）。
        #   card_turn_caps＝暗躍禁止は1枚/ターン＝二正面（暗躍禁止を要する脅威が2本）を
        #   「枚数不足で未防御」として正しく表示する（表示専用・AIの配線は既定 None のまま）。
        # ★全件表示（ユーザー要望 2026-07-13）：min_prob=0＝実在度が極小の負け筋まで全部出す。
        threats, plan = plan_for_belief(view, bel, options=opts, min_prob=0.0,
                                        card_turn_caps=ANRYAKU_KINSHI_TURN_CAP)
        md = render_plan_md(threats, plan, detailed=True, max_rows=None)
        if bel.n_worlds() == 0:   # 可能世界0＝役職推定が信頼できない（イレギュラー枠外配役等）
            md = ("> ⚠️ **belief推理不能（可能世界0）**：役職ベースの脅威（KP/キラー等）は"
                  "信頼できません。以下は盤面カウンター由来の脅威のみ有効です。\n\n" + md)
        return md
    except Exception as e:   # 握り潰さず理由を出す（#6・AIC要望）
        import traceback
        return ("##### 🛡 防御プランナー\n"
                f"_計算に失敗：{type(e).__name__}: {e}_\n\n"
                f"```\n{traceback.format_exc()[-600:]}\n```")


def _defense_plan_compute(state, script, *, min_prob: float = 0.0,
                          include_breached: bool = False, display_all: bool = False):
    """防御プランナの脅威列挙＋防御計画を計算し (threats, plan, unreliable) を返す（A-1共通計算部）。

    ロジックは agents/defense_plan（AIB所有）。include_breached＝突破済み（既に敗北域）の脅威も
    表示用に返す＝AIBの plan_for_belief(include_breached=…) API。**未着版では TypeError を捕えて
    従来シグネチャで呼ぶ**＝UI骨格を先行実装し、API landing 後に自動で有効化される。
    """
    from agents.belief import Belief
    from agents.defense_plan import ANRYAKU_KINSHI_TURN_CAP, plan_for_belief
    from sim.legal import set_card_options
    from sim.views import protagonist_view
    view = protagonist_view(state, "p1")
    opts: list = []
    for s in ("p1", "p2", "p3"):
        opts += set_card_options(state, s)
    bel = Belief(script.cast, _public_incidents(script), script.set_name)
    bel.observe(state.history)
    kw = dict(options=opts, min_prob=min_prob,
              card_turn_caps=ANRYAKU_KINSHI_TURN_CAP)
    try:
        # ★A-9：display_all＝生成時のノイズ抑制ガード（rule_p<0.05 等の continue 群）を実在度極小化に
        #   置換＝min_prob(最終フィルタ)の手前で脅威生成自体が止まる問題を解く（AIB API・0c815cc）。
        threats, plan = plan_for_belief(view, bel, include_breached=include_breached,
                                        display_all=display_all, **kw)
    except TypeError:   # include_breached/display_all API 未着＝従来どおり（突破済み/極小は出ない）
        threats, plan = plan_for_belief(view, bel, **kw)
    return threats, plan, (bel.n_worlds() == 0)


def defense_plan_rows(threats, plan) -> list[dict]:
    """★A-1(b)：防御プランナの脅威を dataframe 行（dict）に整える（表示ロジック＝AIA所有）。
    Threat/Plan（agents/defense_plan＝AIB）から表示値を組む。Streamlit非依存＝テスト可能。
    Threat.breached（AIB API）が True なら💀突破済み。無い版では通常状態。"""
    from agents.defense_plan import _break_basis, _pick_for
    rows: list[dict] = []
    for t in threats:
        chosen = _pick_for(plan, t)
        if getattr(t, "breached", False):        # AIB API：既に敗北域に達した負け筋
            status = "💀突破済み"
        elif id(t) in plan.covered:
            status = "✅覆えた"
        elif not t.defendable:
            status = "⛔防御不能（レース）"
        else:
            status = "⚠枚数不足で未防御"
        rows.append({
            "負け筋": t.label,
            "実在度%": round(t.prob * 100, 1),
            "タイミング": t.timing,
            "状態": status,
            "折り手": (chosen.card if chosen else "—"),
            "対象": (chosen.target if chosen else "—"),
            "根拠": (_break_basis(chosen.card) if chosen else "—"),
            "堅牢": ("堅" if (chosen and chosen.robust)
                     else ("追撃可" if chosen else "—")),
            "コスト": (round(chosen.cost, 1) if chosen else None),
        })
    return rows


def _group_rows_by_break(rows: list[dict]) -> list[dict]:
    """★A-1(c)：同じ折り手（折り手×対象）で覆える負け筋を1行に畳む。"""
    groups: dict = {}
    order: list = []
    for r in rows:
        key = (r["折り手"], r["対象"])
        if key not in groups:
            g = dict(r)
            g["負け筋"] = [r["負け筋"]]
            groups[key] = g
            order.append(key)
        else:
            groups[key]["負け筋"].append(r["負け筋"])
            groups[key]["実在度%"] = max(groups[key]["実在度%"], r["実在度%"])
    out: list[dict] = []
    for key in order:
        g = groups[key]
        names = g["負け筋"]
        g["負け筋"] = (f"（{len(names)}件）" + "／".join(names)) if len(names) > 1 else names[0]
        out.append(g)
    return out


def render_defense_planner(state, script, *, reviewing: bool = False) -> None:
    """★A-1：防御プランナのデバッグ表示（st.dataframe＝ソート/フィルタ可能＋チェックボックス）。
    脚本家プレイ用。(d)時点ヘッダ・(c)足切り/突破済み/畳みの表示制御。表示層＝AIA所有。
    reviewing＝過去フェイズをレビュー中（cursorが後ろ）＝上の盤面は過去だが、この脅威表は state
    （現決定局面）から計算するライブ助言＝時点差をキャプションで明示する（A-15・2026-07-16）。"""
    import streamlit as st
    # ★A-10（2026-07-14・ユーザー裁定(ii)）：セット途中でも暫定計算を出し続けるが、ラベルを
    #   正確に＝カード配置ごとの再描画で「セット後」と貼っていたのを、実際の置いた枚数
    #   （mastermind の turn_placements）で「セット途中 n/3枚」「セット完了後」を出し分ける。
    _mm_placed = sum(1 for p in getattr(state, "turn_placements", [])
                     if isinstance(p, dict) and p.get("owner") == "mastermind")
    _stage = (f"セット途中・{_mm_placed}/3枚 置いた時点の暫定計算"
              if getattr(state, "phase", "") == "mastermind_set"
              else "セット完了後の盤面")
    st.markdown(f"##### 🛡 防御プランナー（L{state.loop_no}・D{state.day}・{_stage}）")
    if reviewing:
        st.caption("🔎 この脅威表は**現在の決定局面**のもの（上の盤面はレビュー中の過去フェイズ＝"
                   "時点が異なります）。フェイズを最後まで進めると盤面と一致します。")
    st.caption("主人公AIが直面する負け筋→最安の折り手。盤面＝脚本家が今ターン伏せた札の位置を"
               "含む（あなたの見ている『日の頭』より先の情報）。列見出しでソート・右上の🔍でフィルタ可。")
    c1, c2, c3 = st.columns(3)
    show_all = c1.checkbox("実在度0%まで表示", value=True, key="mmv_dp_all",
                           help="ONで実在度が極小の負け筋まで全て表示（生成時の抑制ガードも解除）。"
                                "OFFで実在度1%未満を隠す。")
    show_breached = c2.checkbox("💀突破済みも表示", value=True, key="mmv_dp_breached",
                                help="既に敗北域に達した負け筋も残す（AIBのAPI対応後に有効化）。")
    group_breaks = c3.checkbox("同じ折り手でまとめる", value=False, key="mmv_dp_group",
                               help="1枚で複数の負け筋を折れる手を1行に畳む。")
    try:
        threats, plan, unreliable = _defense_plan_compute(
            state, script, min_prob=(0.0 if show_all else 0.01),
            include_breached=show_breached, display_all=show_all)
    except Exception as e:   # 握り潰さず理由を出す（#6・AIC要望）
        st.error(f"防御プランナー計算に失敗：{type(e).__name__}: {e}")
        return
    if unreliable:
        st.warning("⚠️ belief推理不能（可能世界0）：役職ベースの脅威（KP/キラー等）は"
                   "信頼できません。盤面カウンター由来の脅威のみ有効です。")
    rows = defense_plan_rows(threats, plan)
    if group_breaks:
        rows = _group_rows_by_break(rows)
    if not rows:
        st.caption("いま火の点きうる致命的な負け筋は検出なし。")
        return
    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception:
        st.table(rows)   # pandas不在時のフォールバック
    st.caption(f"脅威 {len(rows)} 件／防御 採用 {len(plan.picks)} 枚・"
               f"総コスト {plan.total_cost:.1f}（席3枚・暗躍禁止1枚/ターン）。"
               "凡例：✅覆えた（＝**現在列挙済みの脅威を全て手当てできた**＝この表の負け筋は折れている。"
               "検出器がまだ拾えていない脅威は含みません＝安全確定の意味ではない）／⚠枚数不足（二正面）／"
               "⛔レース（暗躍禁止で止まらない供給）／💀突破済み。")

    # ★DP-1 Stage 3（全数表示）：上の表は「発火した脅威」だけ＝拾えていない負け筋は見えない。
    #   evaluate_tree で**全負け筋ノード**を status 付き（グレー含む）で出し、フッターで自己開示する
    #   ＝「この対局で今どの負け筋が生きて／折れて／消えて／未接続か」を穴ごと可視化する。
    # ★ホットフィックス（2026-07-19・FableA）：外枠は expander でなく toggle にする。
    #   render_losstree_statuses はノードごとに st.expander を開く＝expander の中で呼ぶと
    #   **入れ子expander禁止**で StreamlitAPIException（本番の脚本家プレイで実発生・
    #   🛠開発者ページはタブ配置のため発症せず＝テストをすり抜けた）。
    # ★A-45：B-39（dormant＝まだ立っていない）とA-43③（グレー→0️⃣ゼロ）を文言に反映。
    if st.toggle("📊 全負け筋の判定（active / covered / ⚪未活性 / 0️⃣ゼロ も表示）",
                 key="mmv_lt_all_toggle",
                 help="全負け筋ノードを status 付きで表示します。"
                      "⚪未活性＝まだ立っていない（監視のみ）／0️⃣ゼロ＝消えた・脚本上ありえない。"
                      "どれも隠さず全数を出します。"):
        try:
            from arena.losstree_eval import evaluate_tree
            from arena.losstree_view import render_losstree_statuses
            _sts = evaluate_tree(_dp_view(state), _dp_belief(state, script),
                                 options=_dp_options(state))
            render_losstree_statuses(_sts, key_prefix="mmv_")
        except Exception as e:  # noqa: BLE001  表示専用＝失敗してもプレイを止めない
            st.caption(f"（全数判定は計算できませんでした：{type(e).__name__}）")

    # ★DP-2 2M-2（CS選定・advisory）：上は「守る側の穴」＝ここは**攻める側の二者択一**。
    #   本命の勝ち筋に対し、どの勝ち筋を囮（カバーストーリー）として立てるのが安いかを出す。
    #   ★mm の意思決定には未配線（接続は 2M-3）＝表示専用。expander の中では呼ばない（A-40作法）。
    # ★DP-2 doc §6（2026-07-20）＝この機構は「見せ球（decoy）」＝本命以外の別ルートを立てて
    #   相手の防御資源を吸う手。ユーザー本来の「CS＝可能世界の激減を和らげる作り話」は別概念
    #   （2M-4で独立設計）＝表示でも語を混ぜない。
    if st.toggle("🎭 見せ球（decoy）選定の内訳", key="mmv_cs_toggle",
                 help="本命とは別ルートを立てて相手の防御資源を吸う手を、偽装コスト（第1キー）と"
                      "資源競合（第2キー）で選びます。advisory＝AIの手には影響しません。"):
        try:
            from arena.cs_plan import (choose_cover_story, node_label,
                                       render_cs_md)
            from arena.losstree_eval import evaluate_tree_mm
            from sim.views import mastermind_view
            _v = mastermind_view(state)
            _live = [w for w in evaluate_tree_mm(_v) if w.status in ("live", "winning")]
            if not _live:
                st.caption("（生きている勝ち筋がありません＝見せ球を立てる対象がない局面です）")
            else:
                # ★A-43④：内部ノードIDでなく losstree のラベルを見せる。
                _main = st.selectbox("本命の勝ち筋", [w.node_id for w in _live],
                                     format_func=node_label, key="mmv_cs_main")
                _p = choose_cover_story(_v, _dp_belief(state, script), _main)
                st.markdown(render_cs_md(_p))
                if _p.rejected:
                    st.caption("落選：" + "／".join(f"{node_label(n)}（{r}）"
                                                   for n, r in _p.rejected[:6])
                               + ("…" if len(_p.rejected) > 6 else ""))
        except Exception as e:  # noqa: BLE001  表示専用＝失敗してもプレイを止めない
            st.caption(f"（CS選定は計算できませんでした：{type(e).__name__}）")


def _dp_view(state):
    from sim.views import protagonist_view
    return protagonist_view(state, "p1")


def _dp_options(state):
    from sim.legal import set_card_options
    opts: list = []
    for s in ("p1", "p2", "p3"):
        opts += set_card_options(state, s)
    return opts


def _dp_belief(state, script):
    from agents.belief import Belief
    bel = Belief(script.cast, _public_incidents(script), script.set_name)
    bel.observe(state.history)
    return bel


def ai_response_md(ai) -> str:
    """★#2：脚本家が3枚伏せた後、主人公AIが実際に打った応手（3席）を可視化する。
    run_to_pending は主人公席をAIが自動で打つ＝records に応手が既にある。最新の
    set_card ターン（loop,day）の3席の選択＋上位候補＋理由を markdown 化。"""
    recs = [r for r in getattr(ai, "records", [])
            if r.get("decision") == "set_card"]
    if not recs:
        return ""
    last = recs[-1]
    lp, dy = last.get("loop"), last.get("day")
    turn = [r for r in recs if r.get("loop") == lp and r.get("day") == dy]
    lines = [f"##### 🃏 主人公AIの応手（L{lp}D{dy}・脚本家が伏せた3枚への対応）"]
    lines.append("| 席 | 打った手 | 次点候補（点差で迷い度が分かる） |")
    lines.append("|---|---|---|")
    for r in turn:
        ch = r["chosen"]
        chosen = f'{ch.get("card")}→{ch.get("target")}'
        alts = []
        for s, o in (r.get("scored") or [])[:3]:
            mk = "◀採用" if o == ch else ""
            alts.append(f'{s:.0f}:{o.get("card")}→{o.get("target")}{mk}')
        lines.append(f'| {r.get("seat")} | **{chosen}** | {" / ".join(alts)} |')
    lines.append("\n_点差が小さい＝AIが迷った局面（そこを突くと崩れやすい）。"
                 "採用手が下位でも高得点＝確信を持って打っている。_")
    return "\n".join(lines)


def mind_markdown(state, script, ai: HeuristicProtagonist, *,
                  hist_len: int | None = None, disp_loop: int | None = None,
                  disp_day: int | None = None, stage_override: str | None = None,
                  live: bool = True) -> str:
    """現局面の主人公AI内省（belief＋戦術読み＋防御プランナー）を markdown 化する。

    belief は state.history（公開イベント）から再現＝現局面そのもの。
    戦術読み（護るKP/疑い集合/loop_lost）は AI の最新 set_card 記録から取る。
    防御プランナー＝負け筋→最安の折り手（docs/負け筋防御ツリー を実行可能化）。

    B9：レビュー中は表示盤面の時点まで巻き戻して見せる（先読みさせない）。
      hist_len＝その時点までの公開履歴長で belief を再現、disp_loop/day＝ヘッダ表示、
      live=False＝ライブ専用（防御プランナー/AIの実応手＝今の盤面依存）は隠す。
    """
    hist = state.history if hist_len is None else state.history[:hist_len]
    snap = belief_snapshot(script.cast, _public_incidents(script),
                           script.set_name, hist)
    _dl = disp_loop if disp_loop is not None else state.loop_no
    _dd = disp_day if disp_day is not None else state.day
    est = estimates_from_records(getattr(ai, "records", []), _dl, _dd)
    md = render_mind_md(snap, est)
    if live:   # ライブ専用＝今の盤面（脚本家が今ターン伏せた札の位置）に依存する読み
        resp_md = ai_response_md(ai)   # ★#2：AIが実際に打った応手（3席）
        if resp_md:
            md += "\n\n" + resp_md
        # ★防御プランナーは思考パネルのON/OFFに依らず常時表示する（ユーザー要望 2026-07-13）＝
        #   render_play_vs_ai 側で別途出す（ここでは重複を避けて出さない）。
    # ★#1：この思考がいつ時点か明示。belief は公開イベント（＝そのターン開始まで）から
    #   再現、防御プランナーは盤面（脚本家が今ターン伏せた札の位置を含む）を見ている。
    if stage_override is not None:
        stage = stage_override
    else:
        phase = getattr(state, "phase", "")
        stage = "脚本家が3枚伏せた後（主人公が応じる直前）" if phase == "mastermind_set" \
            else {"protagonist_set": "主人公が応じる直前", "loop_start": "ループ開始時",
                  "action_resolution": "行動解決の直前"}.get(phase, phase or "現局面")
    _plan_note = ("／防御プランナーは盤面＝脚本家が今ターン伏せた札の位置を反映"
                  if live else "／レビュー中＝防御プランナー等ライブ専用の読みは非表示")
    header = (f"> 🕒 **この思考の時点**：L{_dl}・D{_dd}／{stage}"
              "（役職・ルールの読みは公開情報＝このターン開始時点まで"
              f"{_plan_note}）\n\n")
    return header + md


# ---------------------------------------------------------------------------
# Streamlit 描画（薄いラッパ）。app.py 側で render_play_vs_ai() を呼ぶ。
# ---------------------------------------------------------------------------

def territory_note_md(script) -> str:
    """大物の縄張り（テリトリー）トークンの表示（公開情報・全ループ固定）。無ければ空。
    盤面のすぐ上に出す（テスター指摘 2026-07-09：大物の縄張りが表示されない）。
    Streamlit非依存＝テスト可能。"""
    terr = getattr(script, "oomono_territory", None)
    if not terr or "大物" not in getattr(script, "cast", ()):
        return ""
    return f"🏴 **大物の縄張り**：{terr}"


def _territory_note(script) -> str:
    terr = getattr(script, "oomono_territory", None)
    if not terr or "大物" not in getattr(script, "cast", ()):
        return ""
    return ('<div style="opacity:.85;margin:.1em 0 .3em">'
            f'🏴 <b>大物の縄張り</b>：{terr}</div>')


def _render_used_cards_inline(st, state) -> None:
    """消費カード（1/ループ）を畳まずインライン表示（#3）。🔴使用済 / ⚪未使用。"""
    try:
        from engine.models import ONCE_PER_LOOP
        st.caption("🃏 消費カード（1/ループ・消費すると次ループ開始まで戻りません）")
        for owner, label in [("mastermind", "脚本家")] + \
                [(s, f"主人公 {s}") for s in ("p1", "p2", "p3")]:
            grp = "protagonist" if owner != "mastermind" else "mastermind"
            used = state.used_cards.get(owner, [])
            marks = "　".join(f'{"🔴" if c in used else "⚪"}{c}'
                              for c in sorted(ONCE_PER_LOOP[grp]))
            st.markdown(f"<small>{label}：{marks}</small>", unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"（消費カード表示に失敗：{e}）")


def render_play_vs_ai(mobile: bool = False) -> None:
    import streamlit as st

    import cloud   # Supabase（未設定でno-op・失敗握りつぶし・UIを壊さない）
    from arena.replay import board_json_from_view, describe_choice
    from board_viz import board_html_from_json
    from sim import random_script
    from sim.sample_scripts import SAMPLE_SCRIPTS

    st.subheader("🎭 脚本家としてプレイ（AI主人公と対戦）")
    st.caption("あなたが脚本家として3枚を伏せ、AI主人公が防ぎます。"
               "下部で『AIが今どう読んでいるか』を見ながら仕掛けられます。")

    # -- セットアップ（脚本の選択）＝#3 サイドバー・#4 花名マスキング --
    menu = script_menu(list(SAMPLE_SCRIPTS))
    labels = list(menu)

    # -- 対局の読み込み（.rooper.json）＝主人公プレイと同じサイドバー機能（ユーザー要望 2026-07-11） --
    def _apply_mmv_save(raw: bytes) -> bool:
        """脚本家プレイのセーブを session_state に適用。2形式対応：
        ①.rooper.json（encode_mmv_save＝脚本名+seed+選択＝旧方式）
        ②棋譜 .jsonl（確定脚本＋記録済み決定＝②-2・記録再適用で復元・AI非依存で過去再現）。成功で True。"""
        text = raw.decode("utf-8", errors="replace")
        _lines = [ln for ln in text.splitlines() if ln.strip()]
        # ②棋譜(.jsonl)判定：1行目が type:meta なら確定脚本＋記録から再開（脚本家=mastermind の選択）。
        if _lines:
            import json as _json
            try:
                _first = _json.loads(_lines[0])
            except Exception:  # noqa: BLE001
                _first = None
            if isinstance(_first, dict) and _first.get("type") == "meta":
                try:
                    from arena.gamelog import (
                        ai_replay_from_decisions, compat_note,
                        human_choices_from_decisions, load_game_lines, resume_from_meta)
                    script_obj, meta, decisions = load_game_lines(_lines)
                    _compat = compat_note(meta, st.session_state.get("app_version", ""))
                    # ★A-39：新形式ログ（resume_snapshot あり）は **snapshot 復元**で読み込む
                    #   ＝replay に依存しない＝ドリフトしない。当日分だけ再生（A-36と同じ構造）。
                    _mrf = resume_from_meta(meta, decisions, {"mastermind"})
                    if _mrf is not None:
                        _ms0, _mhc, _mai, _mnorm = _mrf
                        _reset_mmv_game(
                            mmv_script_obj=_ms0.script,     # 脚本は snapshot 内蔵（自作脚本もOK）
                            mmv_script="棋譜（読み込み）", mmv_seed=0,
                            # ★A-36：snapshot(dict) 保持＝毎rerun 新品stateを起点にする
                            mmv_snap_state=_ms0.to_snapshot(),
                            mmv_snap_ai_replay=(_mai or None),
                            mmv_choices=list(_mhc), mmv_kifu=True,
                            mmv_extra_loops=max(
                                0, int(getattr(_ms0, "loop_no", 1)) - MMV_BASE_LOOPS),   # A-31
                            mmv_snap_warn=_mnorm, mmv_load_warning=_compat)
                    else:
                        # ★旧形式ログ＝従来どおり replay＋「ずれる場合あり」を維持（後方互換）。
                        mm_choices = human_choices_from_decisions(decisions, {"mastermind"})
                        _reset_mmv_game(
                            mmv_script_obj=script_obj,          # 確定脚本を直接使う
                            mmv_script="棋譜（読み込み）", mmv_seed=0,
                            mmv_choices=list(mm_choices), mmv_kifu=True,
                            # ★AI主人公の過去決定も記録から再生（AI非依存で過去を再現・②-2）。
                            mmv_ai_replay=ai_replay_from_decisions(decisions, {"mastermind"}),
                            mmv_load_warning=_compat)
                except Exception:  # noqa: BLE001  壊れた棋譜は無視
                    return False
                st.session_state["mmv_extra_loops"] = 0
                for _k in ("mmv_revealed", "mmv_choice_len"):
                    st.session_state.pop(_k, None)
                return True
        # ①.rooper.json（脚本名+seed+選択）
        d = decode_mmv_save(text)
        if not (d and d.get("pick") in menu):
            return False
        try:
            _sc_obj = _build_selected_script(
                menu[d["pick"]], int(d.get("seed", 0)), random_script, SAMPLE_SCRIPTS)
        except Exception:  # noqa: BLE001  壊れた/未知の脚本は無視
            return False
        # ★A-32：入口は _reset_mmv_game を通す（対局キーの全消し＋必要分の設定）。カーソル
        #   （mmv_revealed/mmv_choice_len）も _MMV_GAME_KEYS に入っている＝個別に消さない。
        _reset_mmv_game(mmv_script_obj=_sc_obj, mmv_script=d["pick"],
                        mmv_seed=int(d.get("seed", 0)),
                        mmv_choices=list(d.get("choices", [])), mmv_extra_loops=0)
        return True

    def _render_load_widget() -> None:
        """サイドバーの『📂 対局を読み込む』。.rooper.json（再開用）と 棋譜.jsonl（②-2）の両対応。"""
        up = st.file_uploader(
            "📂 対局を読み込む（.rooper.json / 棋譜.jsonl）", type=["json", "jsonl"],
            key="mmv_load_file",
            help="『💾 対局を保存』(.rooper.json) か『📼 ログを保存』(.jsonl=棋譜) を選ぶと、その局面から"
                 "再開できます。棋譜は別バージョンだと局面がずれる場合があります。")
        if up is not None:
            import hashlib
            raw = up.getvalue()
            sig = hashlib.md5(raw).hexdigest()
            if st.session_state.get("_mmv_loaded_sig") != sig:  # 同一ファイルの再読込ループ防止
                st.session_state["_mmv_loaded_sig"] = sig
                if _apply_mmv_save(raw):
                    st.session_state.pop("mmv_end_logged", None)   # 終局イベント再武装
                    cloud.log_event("play_start", side="mastermind", source="load")
                    st.success("対局を読み込みました。")
                    st.rerun()
                else:
                    st.error("このファイルは脚本家プレイの対局セーブ／棋譜として読み込めませんでした。")

        # ★A-39（2026-07-19）：「☁️ 共有コードで読み込む」は撤去（発行側も同時撤去）。共有コードは
        #   棋譜payload＝replay 復元でドリフトする一方、下の☁トークン（snapshot）が同じ用途を
        #   replay非依存で満たす＝保存方式を2本（☁トークン＋📼ログ）に集約した。

            # ★Supabase Phase 1：復帰トークンから「続き」を復元（スナップショット＝replay非依存）。
            _tok = st.text_input("☁ 続きを復元（復帰トークン）", key="mmv_snap_tok_in",
                                 placeholder="復帰トークンを貼り付け",
                                 help="『☁ 続きをクラウドに保存』で発行したトークンから再開します。")
            if st.button("トークンから再開", key="mmv_snap_load_btn") and _tok.strip():
                from arena.gamelog import (cloud_payload_day_tail, cloud_payload_to_state,
                                           snapshot_mode_mismatch)
                _pl = cloud.load_snapshot(_tok.strip())
                _mm_note = snapshot_mode_mismatch(_pl, "mastermind") if _pl else None
                _st0, _why = cloud_payload_to_state(_pl) if _pl else (None, "見つかりません")
                if _mm_note:
                    # ★A-31(b)：別モードのトークンは**黙って開かない**（盤面の意味＝どちらが自分かが
                    #   変わる）。拒否して直し方を出す＝fail-ignore（A-27/A-19の教訓）。
                    st.error(_mm_note)
                    cloud.log_event("cloud_snapshot_load", side="mastermind", ok=False,
                                    reason="mode_mismatch")
                elif _st0 is None:
                    st.error(f"復元できませんでした（{_why}）。")
                    cloud.log_event("cloud_snapshot_load", side="mastermind", ok=False)
                else:
                    _hc, _ai = cloud_payload_day_tail(_pl)
                    from arena.play import _normalize_restored_state
                    _norm = _normalize_restored_state(_st0)   # ★A-36：壊れトークン救済（+警告）
                    # ★A-32：復元も「別の対局へ切替える入口」＝_reset_mmv_game を通す
                    #   （前の対局の残留を持ち込まない。登録先は _MMV_GAME_KEYS 一箇所）。
                    # ★A-36：mmv_snap_state は snapshot(dict) で保持（毎rerun 新品state＝累積根絶）。
                    _reset_mmv_game(
                        mmv_snap_state=_st0.to_snapshot(), mmv_snap_ai_replay=(_ai or None),
                        mmv_choices=list(_hc),
                        # 脚本は snapshot 内蔵（seed再生成では復元できない＝棋譜ロードと同型）
                        mmv_script_obj=_st0.script, mmv_kifu=True,   # ?g= 保存を止める（既存機構）
                        # ★A-31 根治：mmv_script/mmv_seed が無いと rerun 後に設定画面へ落ちる。
                        mmv_script="クラウド（続き）", mmv_seed=0,
                        # ★A-31：延長ループの復元（総ループが loop_no を下回ると即終局する）。
                        mmv_extra_loops=max(0, int(getattr(_st0, "loop_no", 1)) - MMV_BASE_LOOPS),
                        mmv_snap_warn="／".join(x for x in (_why, _norm) if x))
                    cloud.log_event("cloud_snapshot_load", side="mastermind", ok=True)
                    st.rerun()

        # ★自作脚本のプレイ投入口（§2b・2026-07-13）：script_studio/脚本ビルダーで組んだ脚本JSONを
        #   開始脚本として読み込む（ランダム生成でなく自作脚本と対戦）。
        with st.expander("✍️ 自作脚本で始める（script_studio/脚本ビルダーの『プレイ用JSON』を貼付）"):
            _cj = st.text_area("脚本JSON を貼り付け", key="mmv_custom_json", height=120,
                               placeholder='{"set_name": "First Steps", "rule_y": "...", ...}')
            st.caption("⚠ 自作脚本の対局は **URL自動保存の対象外**です（リロード/ブラウザ復元で消えます）。"
                       "続きを残すには『📼 ログを保存(.jsonl=棋譜)』の**ダウンロード**をご利用ください"
                       "（棋譜.jsonl は脚本を内包＝そのまま読み込めば復帰できます）。")
            if st.button("この脚本で始める", key="mmv_custom_start"):
                from arena.play import _script_from_json   # 遅延import（play↔play_vs_ai 循環回避）
                _sc, _err = _script_from_json(_cj or "")
                if _sc is None:
                    st.error(_err)
                else:
                    # ★A-32：入口は _reset_mmv_game を通す（個別列挙をやめる＝登録漏れを防ぐ）。
                    _reset_mmv_game(mmv_script_obj=_sc,          # 確定脚本を直接使う
                                    mmv_script="自作脚本", mmv_seed=0,
                                    mmv_choices=[], mmv_extra_loops=0)
                    cloud.log_event("play_start", side="mastermind", source="custom")
                    st.success("自作脚本を読み込みました。")
                    st.rerun()

    # -- #6 URL からの局面復元：リロード/タブ復元でも局面が戻る --
    #    ★Phase 2（2026-07-17）：**?s=（オートセーブのトークン）を優先**＝snapshot方式＝replay
    #      非依存＝ドリフトしない。取れなければ従来の ?g=（replay）へフォールバック（条件1）。
    if "mmv_script" not in st.session_state:
        from arena.play import _AUTO_QP_KEY, _qp_get
        _tok0 = _qp_get(_AUTO_QP_KEY)
        if _tok0 and cloud.enabled():
            try:
                from arena.gamelog import (cloud_payload_day_tail, cloud_payload_to_state,
                                           snapshot_mode_mismatch)
                _pl0 = cloud.load_snapshot(_tok0)
                # ★A-31：モード照合は共通関数に統一（旧：mode直書き比較＝案内が出ず黙って落ちていた）。
                _note0 = snapshot_mode_mismatch(_pl0, "mastermind") if _pl0 else None
                if _note0:
                    st.session_state["mmv_snap_mode_note"] = _note0
                    _pl0 = None
                _s0, _why0 = cloud_payload_to_state(_pl0) if _pl0 else (None, None)
                if _s0 is not None:
                    _hc0, _ai0 = cloud_payload_day_tail(_pl0)
                    from arena.play import _normalize_restored_state
                    _norm0 = _normalize_restored_state(_s0)   # ★A-36：壊れトークン救済（+警告）
                    # ★A-32：URL復元も入口＝_reset_mmv_game を通す（登録先は _MMV_GAME_KEYS 一箇所）。
                    # ★A-36：mmv_snap_state は snapshot(dict) で保持（毎rerun 新品state＝累積根絶）。
                    _reset_mmv_game(
                        mmv_snap_state=_s0.to_snapshot(), mmv_snap_ai_replay=(_ai0 or None),
                        mmv_choices=list(_hc0), mmv_script_obj=_s0.script,  # 脚本はsnapshot内蔵
                        mmv_script="クラウド（続き）", mmv_seed=0,
                        mmv_extra_loops=max(                          # 延長ループの復元（A-31）
                            0, int(getattr(_s0, "loop_no", 1)) - MMV_BASE_LOOPS),
                        mmv_kifu=True,                                # ?g= の再生成を止める
                        mmv_snap_warn="／".join(x for x in (_why0, _norm0) if x))
                    st.session_state["mmv_auto_token"] = _tok0        # 以後も同じ行へ上書き（消さない）
            except Exception:  # noqa: BLE001  クラウド不通＝?g= へフォールバック（壊さない）
                pass
    if "mmv_script" not in st.session_state:
        from arena.play import _qp_get
        blob = _qp_get("g")
        d = decode_mmv_url(blob) if blob else None
        if d and d.get("pick") in menu:
            try:
                rsc = _build_selected_script(menu[d["pick"]], int(d.get("seed", 0)),
                                             random_script, SAMPLE_SCRIPTS)
                # ★A-32：URL(?g=)復元も入口＝_reset_mmv_game を通す。
                _reset_mmv_game(mmv_script_obj=rsc, mmv_script=d["pick"],
                                mmv_seed=int(d.get("seed", 0)),
                                mmv_choices=list(d.get("choices", [])))
            except Exception:  # noqa: BLE001  壊れたURLは新規扱い
                pass

    if "mmv_script" not in st.session_state:
        with st.sidebar:
            st.markdown("### 🎭 脚本家プレイ設定")
            st.caption("脚本名は花の名前で伏せています（配役・ルールのネタバレ防止）。")
            label = st.selectbox(
                "脚本", labels, key="mmv_pick",
                help="花の名前のシナリオはLLMが適当に作ったもの、"
                     "それ以外はランダムに機械的に生成したものです。")
            seed = int(st.number_input("seed", 0, 9999, 0, key="mmv_seed_in"))
            start = st.button("この設定で開始", type="primary",
                              use_container_width=True)
            # ★β-FB（2026-07-24・ユーザー指示）：既定OFF（初見に情報過多＝わかりにくい。
            #   公開情報ベース＝ネタバレではないので表示自体は維持・見たい人がONにする）。
            st.toggle("🧠 主人公AIの思考表示", key="mmv_show_mind", value=False,
                      help="ONで、対局中に主人公AIが公開情報からどう推理しているか"
                           "（内省パネル）を下部に表示します。")
            st.divider()
            _render_load_widget()   # 📂 対局を読み込む（.rooper.json）＝保存局面から開始
        if start:
            # ★A-32：対局キーは _reset_mmv_game が一括で消してから設定する（個別列挙をやめる＝
            #   新機構を足した時の登録漏れを防ぐ。登録先は _MMV_GAME_KEYS 一箇所）。
            _reset_mmv_game(
                mmv_script_obj=_build_selected_script(menu[label], seed,
                                                      random_script, SAMPLE_SCRIPTS),
                mmv_script=label,        # 表示名（花名）で保持＝以降もマスク
                mmv_seed=seed, mmv_choices=[], mmv_extra_loops=0)
            cloud.log_event("play_start", side="mastermind", source="new")
            st.rerun()
        st.info("👈 サイドバーで脚本と seed を選び「この設定で開始」を押してください。"
                "🔰メンバー調整は、複雑な能力・特性のキャラが出にくくなります。")
        return

    sc = st.session_state["mmv_script_obj"]
    seed = st.session_state["mmv_seed"]
    choices = st.session_state["mmv_choices"]

    # -- 対局中サイドバー：脚本の再選択（ユーザー要望 2026-07-10） --
    with st.sidebar:
        st.markdown("### 🎭 脚本家プレイ")
        st.caption(f"現在：{st.session_state['mmv_script']}（seed {seed}）")
        relabel = st.selectbox(
            "別の脚本を選ぶ", labels, key="mmv_repick",
            help="花の名前のシナリオはLLMが適当に作ったもの、"
                 "それ以外はランダムに機械的に生成したものです。")
        reseed = int(st.number_input("seed", 0, 9999, seed, key="mmv_reseed"))
        if st.button("🔄 この設定で最初から", use_container_width=True):
            # ★A-32：同上（一括で消してから設定＝登録先は _MMV_GAME_KEYS 一箇所）。
            _reset_mmv_game(
                mmv_script_obj=_build_selected_script(menu[relabel], reseed,
                                                      random_script, SAMPLE_SCRIPTS),
                mmv_script=relabel, mmv_seed=reseed, mmv_choices=[], mmv_extra_loops=0)
            cloud.log_event("play_start", side="mastermind", source="restart")
            st.rerun()
        st.toggle("🧠 主人公AIの思考表示", key="mmv_show_mind", value=False,
                  help="ONで、対局中に主人公AIの推理（内省パネル）を下部に表示します。")
        st.divider()
        _render_load_widget()   # 📂 対局を読み込む（.rooper.json）＝別の保存局面へ切替

    # -- #6 現局面を URL(?g=) に自動保存（クラッシュ/リロード/タブ復元で局面が戻る） --
    #   ★棋譜(mmv_kifu)から読み込んだ局面は seed+名前で復元できない＝URL保存しない（棋譜ファイルが
    #    持ち運び手段）。URLに残ると次回 名前/seed から別脚本を組んでしまう。
    try:
        from arena.play import _qp_set
        if st.session_state.get("mmv_kifu"):
            _qp_set("g", None)
        else:
            _qp_set("g", encode_mmv_url(st.session_state["mmv_script"], seed, choices))
    except Exception:  # noqa: BLE001  URL書き込み失敗は無害（ファイルセーブが代替）
        pass

    # ★棋譜ロードの互換警告（②-2・別バージョン保存＝局面がずれうる旨。互換は保証しない方針）。
    if st.session_state.get("mmv_load_warning"):
        st.warning("⚠ " + str(st.session_state["mmv_load_warning"]))

    # ★4ループ打ち切り（ユーザー/AIC 2026-07-11）：既定4ループ。「もう少し遊んでやる」で延長。
    _mmv_loops = MMV_BASE_LOOPS + int(st.session_state.get("mmv_extra_loops", 0))
    # ★Supabase Phase 1：クラウド復元中は日境界snapshotが起点（§1 rev2・§8b裁定2）。
    # ★A-36 根治（2026-07-19）：mmv_snap_state は **snapshot(dict)** で保持し、毎rerun ここで
    #   from_snapshot して新品 state を起点にする（主人公プレイ play.py と同じ・復元の累積を根絶）。
    _snap_raw = st.session_state.get("mmv_snap_state")
    if isinstance(_snap_raw, dict):
        from sim.state import GameState as _GSr
        _snap_state = _GSr.from_snapshot(_snap_raw)
    else:
        _snap_state = _snap_raw          # 後方互換（旧 GameState 直保持）
    state, pending, _log, ai = run_to_pending(
        sc, seed, choices, loops=_mmv_loops,
        ai_replay=(st.session_state.get("mmv_snap_ai_replay") if _snap_state
                   else st.session_state.get("mmv_ai_replay")),
        initial_state=_snap_state)
    # ★desync退行の通知（必須条件）：黙って別の局面を出さない＝必ず知らせる。
    if getattr(state, "snap_desynced", False) and not st.session_state.get("mmv_snap_desync_shown"):
        st.session_state["mmv_snap_desync_shown"] = True
        st.session_state["mmv_choices"] = []
        st.session_state.pop("mmv_snap_ai_replay", None)
        st.warning("⚠ アプリの更新により、この日の途中経過を再現できませんでした。"
                   "**この日の頭から再開します**（前日までの盤面はそのまま復元されています）。")
    _snap_warn = st.session_state.pop("mmv_snap_warn", "")
    if _snap_warn:
        st.info(f"ℹ {_snap_warn}")
    # ★A-31：URLのトークンが別モード用だった＝黙って落とさず案内する。
    _mode_note0 = st.session_state.pop("mmv_snap_mode_note", "")
    if _mode_note0:
        st.info(f"ℹ {_mode_note0}")

    # ---------- ★Phase 2：オートセーブ（案A・fire-and-forget・差分dedupe） ----------
    #   材料は手動保存と同一＝日境界snapshot＋当日分（§1 rev2）。URL(?g=) は上でそのまま維持し
    #   （条件1）、クラウドが在る時だけ ?s=<token> を併記＝復元は ?s= を優先（snapshot＝厳密）。
    if cloud.enabled() and pending is not None:
        _ds = getattr(state, "day_snaps", {}) or {}
        _k2 = (state.loop_no, state.day)
        if _k2 in _ds:
            try:
                from arena.gamelog import (snapshot_payload_digest, snapshot_to_cloud_payload,
                                           split_day_tail)
                from arena.play import _AUTO_QP_KEY, _autosave_limit, _qp_set
                from sim.state import GameState as _GS2
                _b2, _hc2, _ai2 = split_day_tail(_log, MM_SEAT, _k2)
                _pl2 = snapshot_to_cloud_payload(
                    _GS2.from_snapshot(_ds[_k2]), mode="mastermind",
                    app_version=st.session_state.get("app_version", ""),
                    human_choices=_hc2, ai_replay=_ai2, ui={"loop": _k2[0], "day": _k2[1]})
                _dg2 = snapshot_payload_digest(_pl2)
                if st.session_state.get("mmv_auto_digest") != _dg2:
                    _tk2 = st.session_state.get("mmv_auto_token") or cloud.new_save_token()
                    st.session_state["mmv_auto_token"] = _tk2
                    st.session_state["mmv_auto_digest"] = _dg2
                    _qp_set(_AUTO_QP_KEY, _tk2)
                    cloud.autosave_snapshot(_pl2, _tk2, limit=_autosave_limit())
            except Exception:  # noqa: BLE001  オートセーブの失敗は無害＝アプリを止めない
                pass

    # -- 保存局面のずれを吸収（生成コーパス変更等で同一seedが別脚本になったケース）：
    #    合法だった手までで再開し、session/URL の手順もそれに合わせて通知する。 --
    if getattr(state, "replay_dropped", 0):
        st.session_state["mmv_choices"] = list(getattr(state, "replay_used_choices", []))
        choices = st.session_state["mmv_choices"]
        st.warning(
            f"⚠ 保存された局面が現在のバージョン（キャラ追加等）と一致しないため、合法だった"
            f"{len(choices)}手までで復元しました（{state.replay_dropped}手を破棄）。"
            "この局面から続けられます。最初からやり直すにはサイドバーの「🔄 この設定で最初から」を。")

    # -- フェイズ送りカーソル（主人公モードと同型）：手を進めたら新たに解決された
    #    フェイズ（主人公の配置→行動解決→…）を1つずつ観てから次の決定へ。
    snaps = getattr(state, "phase_snapshots", []) or []
    n_snaps = len(snaps)
    if st.session_state.get("mmv_choice_len") != len(choices):
        if "mmv_choice_len" not in st.session_state:
            st.session_state["mmv_revealed"] = n_snaps  # 開幕は決定点まで飛ばす
        st.session_state["mmv_choice_len"] = len(choices)
    revealed = max(0, min(st.session_state.get("mmv_revealed", n_snaps), n_snaps))
    st.session_state["mmv_revealed"] = revealed
    review_mode = pending is not None and revealed < n_snaps

    # -- ヘッダ（進行状況）。B7：ループ/日は「表示中の盤面の時点」に留める（先読みさせない）。 --
    if pending is not None and review_mode:
        _hs = snaps[revealed]
        _disp_loop, _disp_day = _hs["loop"], _hs["day"]
    else:
        _disp_loop, _disp_day = state.loop_no, state.day
    st.markdown(f"**脚本** {st.session_state['mmv_script']}（seed {seed}）　"
                f"**ループ** {_disp_loop}/{MMV_BASE_LOOPS}　"
                f"**日** {_disp_day}/{sc.days_per_loop}　"
                f"**あなたの手数** {len(choices)}")

    # -- B2：対局を保存 / ログを保存（現在フェイズを撤去した位置＝ヘッダ直下）。
    #    ロード（アップロード）は下の「💾 セーブ / ロード」に残す。 --
    # ★A-39：「💾 対局を保存(.rooper.json)」は撤去（脚本名+seed+手順＝replay方式でドリフトする。
    #   📼ログが日境界snapshotを内包＝上位互換）。📂の読込は当面残す（段階廃止・既存ファイル救済）。
    _sv2 = st.container()
    try:
        from arena.gamelog import game_to_jsonl
        # ★A-39：ログに「最新の日境界スナップショット」を同梱＝📂読込を replay でなく snapshot
        #   復元でできるようにする（当日分は decisions から split_day_tail）。day_snaps は
        #   run_to_pending の on_day_start が集めた完全スナップショット（A-36/B-30③ 資産）。
        _mrs_key = (state.loop_no, state.day)
        _mrs_snaps = getattr(state, "day_snaps", {}) or {}
        _mrs = ({"loop": _mrs_key[0], "day": _mrs_key[1], "snapshot": _mrs_snaps[_mrs_key]}
                if _mrs_snaps.get(_mrs_key) else None)
        _sv2.download_button(
            "📼 ログを保存（ビューア用）",
            data=game_to_jsonl(sc, state, _log, resume_snapshot=_mrs),
            file_name=f'mmv_{st.session_state["mmv_script"]}_seed{seed}.jsonl',
            mime="application/x-ndjson", key="mmv_savelog_top",
            help="『🔁 リプレイビューア』で開くと日単位で振り返れます。")
    except Exception:
        pass

    # ★A-39：「☁️ クラウドに保存（共有コード発行）」は撤去（読込側も同時撤去）＝保存は
    #   ☁トークン（snapshot・下記）＋📼ログの2本に集約。
    if cloud.enabled():
        # ★Supabase Phase 1（2026-07-17）：続きから遊ぶ＝**スナップショット**保存（上の共有コード＝
        #   棋譜＝replay復元とは別物。棋譜はビルドが変わるとズレる＝実測で9件中5件が再生不能）。
        #   保存は「日境界snapshot＋当日分の再生材料（human_choices＋ai_replay）」＝§1 rev2。
        _dsnaps = getattr(state, "day_snaps", {}) or {}
        _key = (state.loop_no, state.day)
        if pending is not None and _key in _dsnaps:
            if st.button("☁ 続きをクラウドに保存", key="mmv_snap_save",
                         help="今の盤面を保存します。発行トークンで別タブ/リロード後も再開できます。"):
                from arena.gamelog import snapshot_to_cloud_payload, split_day_tail
                from sim.state import GameState as _GS
                _b, _hc, _ai = split_day_tail(_log, MM_SEAT, _key)
                _tok = cloud.save_snapshot(
                    snapshot_to_cloud_payload(
                        _GS.from_snapshot(_dsnaps[_key]), mode="mastermind",
                        app_version=st.session_state.get("app_version", ""),
                        human_choices=_hc, ai_replay=_ai,
                        ui={"loop": _key[0], "day": _key[1]}),
                    slot="manual")
                st.session_state["mmv_snap_token"] = _tok or ""
                cloud.log_event("cloud_snapshot_save", side="mastermind", ok=bool(_tok))
                if not _tok:
                    st.warning("クラウド保存に失敗しました（時間をおいて再度お試しください）。")
            if st.session_state.get("mmv_snap_token"):
                st.success(f"復帰トークン：`{st.session_state['mmv_snap_token']}`　"
                           "サイドバー『☁ 続きを復元』に貼るとこの局面から再開できます。")
                st.caption("⚠ このデータには**配役・伏せ札の中身（＝答え）が含まれます**"
                           "（脚本家プレイでは元々あなたが知っている情報ですが、他人に渡すと"
                           "その脚本のネタバレになります）。")

    # -- 脚本の内訳（ルールY/X・予定事件＋犯人・配役。脚本家は全情報が見える）。
    #    B3：配役はルール/事件の「右」に並べる（縦積みにしない）。 --
    with st.expander("📋 脚本の内訳（ルール・予定事件・配役／あなたは脚本家なので見えます）",
                     expanded=True):
        _lc, _rc = st.columns([3, 2])
        with _lc:
            # ★A-44：ポップアップ機構（A-34/A-37）を脚本家プレイにも配線。
            #   従来ここは素のmarkdown＝主人公プレイだけ予定事件の説明が出ていた（配線忘れ型）。
            #   配役は右カラム（_rc）で出すので左では省く（B3の左右並びを崩さない）。
            # ★A-46：_popup_css() は1行の <style>…</style>＝直後に本文を連結すると HTMLブロックに
            #   巻き込まれ script_sheet_md の **強調** が生表示になる。空行(\n\n)で本文を独立ブロックに。
            st.markdown(_popup_css() + "\n\n" + script_sheet_md(sc, popup=True,
                                                                include_roles=False),
                        unsafe_allow_html=True)
        with _rc:
            st.markdown("**配役**\n\n" + "\n".join(
                f"- **{_char_tip(n)}**：{_role_tip(sc.role_of(n))}" for n in sc.cast),
                unsafe_allow_html=True)

    # ルール一覧・役職能力表・登場キャラ一覧・予定事件効果は B8 でページ下部の
    # 参照セクション（主人公プレイと同一デザイン）へ集約した。

    # -- 盤面（脚本家視点。レビュー中＝フェイズ送り／決定中＝現局面） --
    if pending is not None and review_mode:
        _s = snaps[revealed]
        st.markdown(f'<div style="font-size:1.4em;font-weight:700;margin:0.2em 0">'
                    f'🕐 L{_s["loop"]}・{_s["day"]}日目｜{_s["point"]}</div>',
                    unsafe_allow_html=True)
        _bh = board_html_from_json(mm_snapshot_json(_s), popup=True)  # A-34：ログ閲覧盤面
        if _bh:  # ★st.markdown＝Streamlitのテーマを継承（ダークで白文字）。
            st.markdown(_territory_note(sc) + _bh, unsafe_allow_html=True)
        st.caption(f"⏩ 解決したフェイズを確認中（{revealed + 1}/{n_snaps}）。"
                   "脚本家視点＝配役も主人公の伏せ札も中身が見えます。")

        def _step(delta: int) -> None:
            st.session_state["mmv_revealed"] = max(
                0, st.session_state.get("mmv_revealed", 0) + delta)

        b1, b2, b3 = st.columns(3)
        b1.button("▶ フェイズを進める", type="primary", on_click=_step, args=(1,))
        b2.button("◀ 1つ戻す", disabled=(revealed == 0), on_click=_step, args=(-1,))
        b3.button("⏭ 決定点まで飛ぶ", on_click=lambda: st.session_state.update(
            {"mmv_revealed": n_snaps}))
        # レビュー中の経過＝今見ている盤面の時点まで（先読みさせない）。
        #   ★このフェイズ送りで新たに現れた公開情報だけ赤下線🆕（1つ前のスナップショット以降）。
        st.markdown("**📢 経過（公開情報・この時点まで）**")   # B6：主人公プレイと統一
        _seen = snaps[revealed - 1].get("hist_len", 0) if revealed > 0 else 0
        st.markdown(history_md(state.history[:_s.get("hist_len", 0)], seen_len=_seen),
                    unsafe_allow_html=True)
    elif pending is not None:
        # B4：現局面の盤面にも時点ヘッダを出す（主人公プレイと同じ「🕐 ▶ L・D｜フェイズ」）。
        st.markdown(f'<div style="font-size:1.4em;font-weight:700;margin:0.2em 0">'
                    f'🕐 ▶ L{state.loop_no}・{state.day}日目｜'
                    f'{phase_label(state, pending)}</div>', unsafe_allow_html=True)
        # ★iframe(components.v1.html)はテーマ非継承で黒潰れ＝markdownで継承させる
        _bh = board_html_from_json(board_json_from_view(pending.view), popup=True)  # A-34：実盤面
        if mobile:   # スマホは盤面のみ全幅（使用済み札は下・主人公プレイと同じ扱い）
            if _bh:
                st.markdown(_territory_note(sc) + _bh, unsafe_allow_html=True)
        else:        # B5：PCは盤面の右に「🃏 使用済み（1/ループ）」を隣接表示
            _bc, _uc = st.columns([1.1, 1], gap="small")
            with _bc:
                if _bh:
                    st.markdown(_territory_note(sc) + _bh, unsafe_allow_html=True)
            with _uc:
                _render_used_cards_inline(st, state)
        if n_snaps:
            st.button("⏪ このターンの解決フェイズを見返す",
                      on_click=lambda: st.session_state.update(
                          {"mmv_revealed": max(0, n_snaps - _REVIEW_BACK)}))

        # -- 脚本家の手を選ぶ（3枚を順に）＝#1 配置先→カードの2段セレクタ --
        if pending.actor == "mastermind" and pending.decision == "set_card":
            opts = pending.options
            groups = group_mm_set_options(opts)
            tgt_keys = list(groups)
            d1, d2 = st.columns(2)
            if st.session_state.get("mmv_tgt") not in tgt_keys:
                st.session_state["mmv_tgt"] = tgt_keys[0]
            tgt_key = d1.selectbox("① 配置先を選ぶ", tgt_keys, key="mmv_tgt")
            # ★A-19クラッシュ堅牢化：1094行のガード後でも、ホット更新（Updated app!）や連打の
            #   widget状態競合で selectbox が旧局面のラベルを返すことがある（本番実績＝KeyError:
            #   '転校生（キャラ）'）。resolve_mm_choice と同じくグループ先頭へフォールバック。
            #   （widgetキーはinstantiate後に代入不可＝ローカルにフォールバックし、次のrerunで
            #   1094行のガードが session_state を正す）
            sub = groups.get(tgt_key)
            if sub is None:
                tgt_key = tgt_keys[0]
                sub = groups[tgt_key]
            card_labels = [o["card"] for o in sub]   # ★A-17：カード名のみ（配置先は①で選択済み）
            if st.session_state.get("mmv_card") not in card_labels:
                st.session_state["mmv_card"] = card_labels[0]
            card_label = d2.selectbox("② 伏せるカードを選ぶ", card_labels, key="mmv_card")

            def _commit() -> None:
                # ★A-27：配置先を切替えた直後で mmv_card が旧グループのラベルのまま等、widget 状態が
                #   stale なら resolve_mm_choice は None を返す＝クリックを無視する（on_click は再描画
                #   前に走る＝上のガードの再同期を待てないStreamlitの罠。旧#5はここでグループ先頭へ
                #   落としており、選んでいないカード/配置先を黙って伏せていた）。
                o = resolve_mm_choice(opts, st.session_state.get("mmv_tgt"),
                                      st.session_state.get("mmv_card"))
                if o is None:
                    st.session_state["_mmv_stale_click"] = True
                    return
                st.session_state["mmv_choices"].append(o)

            def _undo_mm() -> None:
                # ★A-14（2026-07-15）：一手戻したら配置先/カードのセレクタ状態もクリアする。
                #   pop だけだと mmv_tgt/mmv_card が旧局面の値を保持し、選択肢が変わっても widget が
                #   古いラベルを出す＝「ラベルと実際に置くカードがずれる」誤操作を誘発（主人公プレイの
                #   _undo_move は move_pick/tgt_pick/card_pick を消しており、脚本家プレイに欠けていた）。
                if st.session_state["mmv_choices"]:
                    st.session_state["mmv_choices"].pop()
                for _k in ("mmv_tgt", "mmv_card"):
                    st.session_state.pop(_k, None)

            _warn_stale_click()
            cc1, cc2 = st.columns(2)
            cc1.button("この手を伏せる", type="primary", on_click=_commit)
            cc2.button("一手戻す", disabled=(not choices), on_click=_undo_mm)
            # -- #3 消費カード（1/ループ）。B5でPCは盤面の右へ移動＝スマホのみここに出す --
            if mobile:
                _render_used_cards_inline(st, state)
        elif pending.decision == "goodwill_refuse":
            # -- #5 友好能力の拒否/解決：誰の・何の能力・対象と、どちらが拒否かを明示 --
            o0 = pending.options[0]
            st.markdown(f"**主人公が友好能力を使おうとしています**：〈{o0.get('character', '')}〉"
                        f"の『{o0.get('ability', '')}』"
                        + (f" → {o0.get('target')}" if o0.get("target") else ""))
            st.caption("この能力の使用キャラは友好無視の役職＝あなたは拒否できます"
                       "（拒否すると友好無視バレ＝AIに役職を絞られます）。")
            labels = [describe_choice(o) for o in pending.options]
            st.radio("どうする？", labels, key="mmv_refuse")
            _warn_stale_click()
            st.button("決定", type="primary",
                      on_click=lambda: _commit_mm_choice(pending.options, labels, "mmv_refuse"))
        elif pending.decision == "cultist_ignore":
            # -- カルティストの暗躍禁止無視（任意発動・KB: 40）。無視するかを都度選ぶ --
            o0 = pending.options[0]
            tgt = o0.get("target", "")
            st.markdown(f"**🕵 カルティストの任意発動**：〈{tgt}〉に暗躍禁止が置かれています。"
                        "同エリア／自ボードのカルティストは暗躍禁止を**無視してもよい**（無視すると"
                        "暗躍が通る／無視しなければ暗躍が打ち消される）。")
            st.caption("★無視して暗躍が通ると、AI主人公に『そこにカルティストが居る』と"
                       "露見しやすくなります（無視しなければ隠せる）。")
            labels = [describe_choice(o) for o in pending.options]
            st.radio("どうする？", labels, key="mmv_cultist")
            _warn_stale_click()
            st.button("決定", type="primary",
                      on_click=lambda: _commit_mm_choice(pending.options, labels, "mmv_cultist"))
        else:
            # 脚本家能力フェイズ等（set_card/refuse以外）：選んで進める
            # ★どのフェイズの選択かを選択欄のすぐ上に明示（ユーザー要望 2026-07-10）。
            #   事件効果の選択は「事件名＋何の対象か」まで具体的に出す（要望 2026-07-11）。
            if pending.decision == "incident_choice":
                st.info(f"**{incident_choice_label(state, pending)}**")
            else:
                _kind = {
                    "mastermind_ability": "🧙 脚本家能力フェイズ（不安/暗躍などの能力）",
                    "goodwill_ability": "💚 主人公能力フェイズ（友好能力）",
                    "scholar_counter": "🎓 ループ開始（学者のカウンター選択）",
                    "loop_start_area": "🚩 ループ開始（登場位置の指定）",
                    "final_battle": "⚔ 最後の戦い（役職宣言）",
                }.get(pending.decision, f"フェイズ：{phase_label(state, pending)}")
                st.info(f"**{_kind}** の選択です。")
            opts = pending.options
            labels = [f"{i + 1}. {describe_choice(o)}" for i, o in enumerate(opts)]
            st.selectbox("脚本家の選択", labels, key="mmv_move2")
            _warn_stale_click()
            st.button("決定", type="primary",
                      on_click=lambda: _commit_mm_choice(opts, labels, "mmv_move2"))

        # -- #4/B6/A-2 経過（公開情報）を盤面のすぐ下に（主人公プレイと統一・日毎expanderで畳む） --
        _render_history_folded(state.history)
    else:
        # ---------- 終局（統一デザイン：勝敗バナー＋選択。主人公プレイと体裁を揃える） ----------
        from arena.endscreen import go_home, render_outcome_banner
        won = state.winner
        _mm_won = won == "mastermind"   # 脚本家（人間）が勝ったか
        _nloops = MMV_BASE_LOOPS + int(st.session_state.get("mmv_extra_loops", 0))
        if not st.session_state.get("mmv_end_logged"):   # 終局イベントは1対局1回だけ
            cloud.log_event("play_end", side="mastermind", won=_mm_won,
                            loops=_nloops, set=sc.set_name, days=sc.days_per_loop)
            st.session_state["mmv_end_logged"] = True
        if _mm_won:
            render_outcome_banner(
                st, won=True, title="🎉 主人公AIに勝利した！",
                detail=f"{_nloops}ループ以内にAI主人公は惨劇を防げませんでした（脚本家の勝ち）。")
        else:
            render_outcome_banner(
                st, won=False, title="🛡 主人公AIに惨劇を防がれた（あなたの負け）",
                detail="AI主人公がどこかのループで惨劇を回避しました。")
        # 選択：脚本家が勝ったら「もう少し遊んでやる（ループ継続）」＋ホーム。負けたら もう一度＋ホーム。
        _q1, _q2 = st.columns(2)
        if _mm_won:
            _q1.button(
                "😏 もう少し遊んでやる（ループ継続）", type="primary",
                use_container_width=True,
                help="AI主人公にもう1ループ挑ませます。",
                on_click=lambda: st.session_state.update(
                    mmv_extra_loops=int(st.session_state.get("mmv_extra_loops", 0)) + 1))
        else:
            if _q1.button("🔄 もう一度（同じ脚本）", use_container_width=True):
                st.session_state["mmv_choices"] = []
                st.session_state["mmv_extra_loops"] = 0
                st.rerun()
        _q2.button("🏠 ホームに戻る", use_container_width=True, on_click=lambda: go_home(st))
        _render_history_folded(state.history)   # A-2：主人公プレイと統一（日毎expander）

    # -- B8：参照セクション（登場キャラ一覧／予定事件効果／ルール一覧／役職能力表）を
    #    主人公プレイと同一デザインで表示。脚本家は真実を知っているので推理の書き込み欄
    #    （確有/確無・薄く・メモ）は省く（aids=False / memo=False）。key_prefix でキー衝突回避。 --
    st.divider()
    from arena.play import (
        render_char_reference, render_incident_reference,
        render_role_reference, render_rule_reference,
    )
    if st.toggle("📇 登場キャラクター一覧（カードテキスト）", key="mmv_ref_chars"):
        render_char_reference(sc, mobile, key_prefix="mmv_")
    if st.toggle("💥 予定事件の効果", key="mmv_ref_incidents"):
        render_incident_reference(sc, memo=False, key_prefix="mmv_")
    if st.toggle(f"📖 ルール一覧（{sc.set_name}・効果早見）", key="mmv_ref_rules"):
        render_rule_reference(sc, aids=False, key_prefix="mmv_")
    if st.toggle(f"🎭 役職能力表（{sc.set_name}）", key="mmv_ref_roles"):
        render_role_reference(sc, aids=False, key_prefix="mmv_")

    # -- ロードはサイドバーの『📂 対局を読み込む（.rooper.json）』へ統一（主人公プレイと同型・
    #    別脚本のセーブも切り替えて読み込める・2026-07-11）。ここでの重複UIは撤去。 --

    # -- ★防御プランナーは pending 中は常時表示（A-15・2026-07-16）：3枚伏せた直後は新フェイズの
    #    snapshot で cursor が後ろ（review_mode=True）になるが、planner は state（＝現決定局面）から
    #    計算するライブ助言＝盤面レビュー位置に依らず「今の脅威」を出す。以前は not review_mode で
    #    弾いており、一番大事な「3枚伏せた段階」でブランクになっていた（生成は全時点T≥1＝表示側症状）。
    #    レビュー中は時点差（上の盤面＝過去／脅威表＝現決定局面）をキャプションで明示する。 --
    # ★β-FB（2026-07-24・ユーザー指示）：防御プランナーもAI思考の一部＝
    #   「🧠 主人公AIの思考表示」トグルと連動（既定OFF＝非表示）。旧「常時表示」
    #   （2026-07-13要望・A-15）はこの指示で上書き。pending中の挙動（A-15）はトグルON時に維持。
    if pending is not None and st.session_state.get("mmv_show_mind", False):
        st.divider()
        render_defense_planner(state, sc, reviewing=review_mode)

    # -- ★役職説明より下：主人公AIの内省パネル（サイドバー「🧠 主人公AIの思考表示」でON/OFF） --
    if st.session_state.get("mmv_show_mind", False):
        st.divider()
        st.markdown("#### 🧠 主人公AIの思考")
        st.caption("AIが公開情報だけからどう配役・ルールを推理し、何を護ろうとしているか。"
                   "甘い読みが見えたらそこを突ける＝主人公AIへのフィードバック源。")
        # ★A-3：戦術読みに出る内部用語の凡例（実験モード等）。折り畳みで邪魔しない。
        with st.expander("ℹ️ 戦術読みの用語（実験モード・負け確ループ・FB勝負）", expanded=False):
            st.markdown(
                "- **実験モード**：負けが込む／情報不足のループで、AIが**勝ちより役職情報の収穫を優先**"
                "する内部モード（無理に守らず、次ループのために情報を引き出しに行く打ち方）。\n"
                "- **⚠負け確ループ（情報収穫モード）**：このループは守り切れないと判断した状態"
                "＝上記モードに切替（次ループへの情報投資を優先）。\n"
                "- **KP防衛を諦め（FB勝負）**：キーパーソンを守り切れないと見て、"
                "**最後の戦い（全役職を当てれば勝ち）**に賭ける方針に切替。")
        if pending is not None and review_mode:   # B9：表示盤面の時点まで巻き戻して見せる
            _rs = snaps[revealed]
            st.markdown(mind_markdown(
                state, sc, ai, hist_len=_rs.get("hist_len"),
                disp_loop=_rs["loop"], disp_day=_rs["day"],
                stage_override=_rs["point"], live=False))
        else:
            st.markdown(mind_markdown(state, sc, ai))
        # ★A-40（2026-07-19・FI-6の画面接続）：友好投資スコアの内訳を🛠開発者用に出す。
        #   計算は AIB の agents.debug.invest_breakdown（_compute_invest と同一計算＝一致テスト
        #   固定済み）を**そのまま呼ぶだけ**＝表示専用・別式を作らない（ドリフト防止）。
        #   ★配置＝この節は `if mmv_show_mind:`（トグル配下）で expander ではない＝ここで
        #   st.toggle を使えば**入れ子 expander にならない**（Stage 3 の回帰と同じ轍を踏まない）。
        if st.toggle("🧮 友好投資スコアの内訳（value/need/reach/tempo）", key="mmv_invest_bd"):
            st.caption("主人公AIが「どの能力にハートを積むか」の採点内訳。"
                       "score = value/(1+need) + tie（tempo はタイブレーク限定）。"
                       "value には reach（居場所・到達可能性）が畳み込み済みで、内訳では係数も併記。")
            # ★実測（A-40）：invest_breakdown → hp._ability_value は主人公AIの内部状態 `_gini`
            #   （役職不確実性）に依存し、**AIがまだ1手も決めていない局面では未設定**＝
            #   AttributeError になる（脚本家が3枚伏せている set_card 待ちは毎ターン該当）。
            #   ＝計算できない局面では理由を出す（黙って空にしない）。AIBへ申し送り済み。
            if not hasattr(ai, "_gini"):
                st.caption("（この局面ではまだ内訳を出せません：主人公AIが今ループでまだ手を"
                           "決めておらず、役職の不確実性（内部状態）が未算出のためです。"
                           "主人公が1手打った後＝行動解決以降のフェイズで表示されます。）")
            else:
                try:
                    from agents.debug import format_invest_breakdown, invest_breakdown
                    from sim.views import protagonist_view
                    _iv = protagonist_view(state, state.leader)
                    st.code(format_invest_breakdown(invest_breakdown(ai, _iv)), language="text")
                except Exception as _e:  # noqa: BLE001  デバッグ表示の失敗で対局を壊さない
                    st.caption(f"（内訳を取得できませんでした：{type(_e).__name__}: {_e}）")

    if st.button("最初から（同じ脚本）"):
        st.session_state["mmv_choices"] = []
        st.session_state["mmv_extra_loops"] = 0
        st.rerun()
