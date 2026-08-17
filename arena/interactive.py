"""人間参加のゲーム進行（M4：人間 vs AI）。

Streamlit はインタラクションのたびにスクリプト全体を再実行するため、進行中の
ゲームオブジェクトを保持し続けるのは相性が悪い。そこで「毎回ゼロから決定的に再実行し、
まだ選んでいない人間の決定に達したら中断する」方式を採る（sim は seed 固定＋決定的なので、
同じ人間の選択列からは必ず同じ局面が再現される＝リプレイと同じ原理）。

使い方（app 側）:
    choices = st.session_state["choices"]          # これまでの人間の選択（順序どおり）
    try:
        state, log = play_interactive(script, ai_agents, {"p1","p2","p3"}, choices)
        # 例外が出なければゲーム終了
    except PendingHuman as p:
        # p.view の盤面と p.options を出し、選ばせて choices に append→再実行

人間には protagonist_view（公開情報のみ）しか渡らない＝配役・犯人は伏せたまま遊べる。
"""

from __future__ import annotations

from sim.flow import run_loop
from sim.state import GameState, Script, validate_script
from sim.views import mastermind_view, protagonist_view


class PendingHuman(Exception):
    """人間の入力待ちで進行を中断する。次に人間が行うべき1決定を運ぶ。"""

    def __init__(self, state: GameState, actor: str, decision: str,
                 options: list[dict], view: dict, log: list[dict] | None = None):
        super().__init__(f"人間({actor})の{decision}待ち")
        self.state = state
        self.actor = actor
        self.decision = decision
        self.options = options
        self.view = view
        self.log = log or []  # ここまでの決定ログ（対局途中でも保存できるように）


class ReplayDesync(Exception):
    """保存済みの人間選択がリプレイ中に非合法になった＝保存後にコード/脚本が変わり局面が
    ずれた（例：生成コーパス変更で同一seedが別脚本になる）。`valid_choices` はそこまで
    合法だった選択列＝ここから安全に再開できる。UI 層はこれを捕えて手数を切り詰め通知する。"""

    def __init__(self, valid_choices: list[dict], decision: str, chosen: dict):
        super().__init__(f"保存選択が現局面で非合法（{decision}）＝{len(valid_choices)}手で打ち切り")
        self.valid_choices = valid_choices
        self.decision = decision
        self.chosen = chosen


def play_interactive(script: Script, ai_agents: dict, human_seats: set[str],
                     human_choices: list[dict],
                     final_battle: bool = True,
                     ai_replay: dict[str, list[dict]] | None = None,
                     *, initial_state: GameState | None = None,
                     on_day_start=None
                     ) -> tuple[GameState, list[dict]]:
    """人間の選択列 human_choices を消費しつつゲームを再実行する。

    ai_agents: 人間以外の席（通常 mastermind）の Agent。
    human_seats: 人間が担当する席の集合（例 {"p1","p2","p3"}）。
    未選択の人間決定に達したら PendingHuman を送出（human_choices に append→再呼び出しで前進）。
    選択肢が1つだけの人間決定（強制・パスのみ等）は自動消化して足止めしない。
    ai_replay={actor: [chosen,...]}＝棋譜方式セーブからの再開用（②-2）。AI席も記録済み決定を
    先に再生して過去を「AIの決定性に非依存」で再現し、尽きたら現行AI(ai_agents)が続きを打つ。
    記録済み決定がコード/脚本変更で非合法化したら、その席はそこから live AI に切り替える（best-effort）。

    ★initial_state（Supabase Phase 1・2026-07-17・FableA裁定(a)）：
      復元したスナップショット（**日の開始前**の局面）から再開する。指定時は「ループの準備」を
      飛ばしてその日から進める（run_loop(resume_from_state=True)）。human_choices は
      **その日の分だけ**を渡す＝過去日は snapshot が保持済み＝replay がその日1日に縮む
      （＝過去日はドリフトしない。ドリフトの実害＝提案書§5補足2）。
      ★既定 None＝**新規開始の経路は一切変わらない**（additive）。
    ★on_day_start(state)＝各日の run_day 直前フック（run_loop へそのまま渡す）。UI がここで
      **日境界の局面**を掴む（＝クラウド保存はこの粒度でしか厳密に戻せない：run_day は日の先頭から
      線形に走るため。PendingHuman.state は常に mastermind_set 後＝日境界ではない）。既定 None＝不変。
    """
    validate_script(script)
    state = initial_state if initial_state is not None else GameState(script=script)
    log: list[dict] = []
    idx = [0]  # human_choices の消費位置
    _ai_replay = ai_replay or {}
    _ai_idx: dict[str, int] = {a: 0 for a in _ai_replay}

    def decide(actor: str, decision: str, options: list[dict]) -> dict:
        if not options:
            raise RuntimeError(f"{actor} の {decision} に合法手が無い（シミュレータバグ）")
        view = mastermind_view(state) if actor == "mastermind" \
            else protagonist_view(state, actor)
        # ★人間=脚本家の set_card には「ブラフ」（暗躍以外のカードのボード置き＝解決されない囮）も
        #   候補に含める（テスター要望 2026-07-12）。engine はブラフを board_bluffs として無害処理
        #   （前提違反ではない・validate_board も通す）。AI席は run_game 等が play_interactive を
        #   通らない＝この分岐に来ず、決定空間・belief・ベンチは不変。
        if actor == "mastermind" and decision == "set_card" and actor in human_seats:
            from sim.legal import set_card_options
            options = set_card_options(state, "mastermind", allow_bluff=True)
        if actor in human_seats:
            if len(options) == 1:
                chosen = options[0]  # 強制/パスのみは足止めしない（再実行でも同一）
            elif idx[0] < len(human_choices):
                chosen = human_choices[idx[0]]
                idx[0] += 1
                if chosen not in options:
                    # 保存局面のずれ（コード/脚本の変更）＝合法だった手までで再開できるよう
                    #   valid_choices を運ぶ（UI層が捕えて手数を切り詰め通知する）。
                    raise ReplayDesync(list(human_choices[:idx[0] - 1]), decision, chosen)
            else:
                raise PendingHuman(state, actor, decision, options, view, log)
        else:
            # ②-2 棋譜再開：AI席も記録済み決定を先に再生（過去をAI非依存で再現）、尽きたら live AI。
            _seq = _ai_replay.get(actor)
            if _seq is not None and _ai_idx[actor] < len(_seq) \
                    and _seq[_ai_idx[actor]] in options:
                chosen = _seq[_ai_idx[actor]]
                _ai_idx[actor] += 1
            else:
                if _seq is not None and _ai_idx[actor] < len(_seq):
                    # 記録がコード/脚本変更で非合法＝以降この席は live に切替（best-effort）。
                    _ai_idx[actor] = len(_seq)
                chosen = ai_agents[actor].decide(view, decision, options)
            if chosen not in options:
                raise ValueError(f"{actor} が非合法手を選択: {chosen}（{decision}）")
        log.append({
            "loop": state.loop_no, "day": state.day, "phase": state.phase,
            "actor": actor, "decision": decision,
            "options": options, "chosen": chosen, "view": view,
        })
        return chosen

    # ★B-214：AI が脚本家席のときも、その AI が複線演出（板へのダミー配置）を使うなら
    #   `sim/flow` 側の候補生成に allow_bluff を開く（既定 False＝従来と1手も変わらない）。
    #   人間=脚本家席では上の分岐が options を作り直すので、この値は無関係。
    decide.mm_allow_bluff = bool(
        getattr(ai_agents.get("mastermind"), "wants_bluff_options", False))

    # human_seats を run_loop へ渡す＝人間=脚本家プレイでカルティストの暗躍禁止無視を
    #   任意発動（都度選択）にする（AI対局は human_seats に mastermind 無し＝従来どおり常に無視）。
    run_loop(state, decide, on_day_start=on_day_start, final_battle=final_battle,
             human_seats=human_seats, resume_from_state=initial_state is not None)
    return state, log
