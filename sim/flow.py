"""1ターン9フェイズの進行とゲームループ（M1）。

- 行動解決は engine.resolve_action_phase をそのまま呼ぶ（ルールの単一ソース維持）。
  セット札は legal.py の options からしか選べないため、resolve 前の validate_board で
  違反が出たらシミュレータ自体のバグ＝RuntimeError（違反検出をファジングに使う）。
- 全てのAI決定は _decide() を通り、決定ログ（dict列＝JSONL相当）に
  「その actor に見えていた view・合法手・選択」を記録する（AIプレイヤー計画 §3.1）。
  view を必ず views.py 経由で作る＝主人公の決定に秘匿情報が渡らないことを構造で保証。

M1の簡略化（正直に非対応と宣言するもの）:
- 主人公能力フェイズ＝パスのみ（友好能力の効果はM2。パスは常に適法＝任意能力）。
- ブラフ置きなし（legal.py）。医者の脚本家能力フェイズ使用なし（M2）。
- ターン開始フェイズの処理は登場日キャラの配置のみ。
"""

from __future__ import annotations

from engine import resolve_action_phase, validate_board
from engine.board import AREAS, compose_moves, destination
from engine.data import (
    UNREFUSABLE_ABILITY_CHARS,
    goodwill_abilities_of,
    initial_area_of,
    role_absolute_friendship_ignore,
    role_has_friendship_ignore,
)
from engine.models import MOVE_CARDS, ONCE_PER_LOOP

from . import abilities, legal
from .effects import (
    apply_juusha_follow,
    apply_mastermind_ability,
    evaluate_loop_end,
    resolve_incident_phase,
    resolve_turn_end,
    to_engine_board,
)
from .state import ONE_TIME_INITIAL_AREA, PROTAGONIST_SEATS, GameState, Script, validate_script
from .views import mastermind_view, protagonist_view


def log_safe_chosen(chosen):
    """リプレイ素材にする chosen（★B-115）＝表示層の provenance（`prov`）を剥がした写し。

    AI主人公は選んだ option に `prov`（B-100 の上書き席="b100"／一致席="b100_match"）を
    書き込むことがある。決定ログ内の chosen は options 内の**同一オブジェクト**なので
    ライブの整合（`chosen in options`）は保たれるが、**保存ログから再生素材**
    （human_choices／ai_replay）を取り出して**新規列挙の options** と dict 同値で
    照合する時だけ prov が邪魔になる（ReplayDesync）。∴ 剥がすのは取り出し口
    （`arena/gamelog.split_day_tail`）＝本関数はその単一ソース。
    prov が無ければ**同一オブジェクトをそのまま返す**（従来と bit 同一）。
    """
    if isinstance(chosen, dict) and "prov" in chosen:
        return {k: v for k, v in chosen.items() if k != "prov"}
    return chosen


def _make_decider(state: GameState, agents: dict, log: list[dict]):
    """decide(actor, decision_type, options) -> 選択。決定ログに view/options/chosen を記録。"""

    def decide(actor: str, decision: str, options: list[dict]) -> dict:
        if not options:
            raise RuntimeError(f"{actor} の {decision} に合法手が無い（シミュレータバグ）")
        view = mastermind_view(state) if actor == "mastermind" else protagonist_view(state, actor)
        chosen = agents[actor].decide(view, decision, options)
        if chosen not in options:
            raise ValueError(f"{actor} が非合法手を選択: {chosen}（{decision}）")
        log.append({
            "loop": state.loop_no, "day": state.day, "phase": state.phase,
            "actor": actor, "decision": decision,
            "options": options, "chosen": chosen, "view": view,
        })
        return chosen

    return attach_mm_bluff(decide, agents)


def attach_mm_bluff(decide, agents: dict):
    """★B-214/B-215：脚本家エージェントの申告（`wants_bluff_options`）を `decide` に載せる。

    脚本家が「板へのダミー配置（複線演出）」を使うなら、脚本家の set_card 候補に
    allow_bluff を開く（KB `rules/10_action_cards.md:70-71`＝脚本家は暗躍以外もボードに
    置ける／それらは**解決されない**）。両切替口 OFF なら False＝候補列は従来と1手も
    変わらない（＝`HeuristicMastermind._pick` の rng 消費数も不変＝両ベンチ bit 不変）。

    ★**自前で `decide` を組んで `run_day` を呼ぶ側は必ずこれを通すこと**（配線の単一ソース）。
      通し忘れると、同じエージェントなのに `run_day` が作る候補列が変わり、rng の消費が
      ずれて**リプレイ／反実仮想が原局面と割れる**＝[[wiring-forgotten-across-paths]]。
      （2026-08-14＝B-214/B-215 の既定 ON 化で `arena/counterfactual.py` が実際に割れた。）
    """
    decide.mm_allow_bluff = bool(
        getattr(agents.get("mastermind"), "wants_bluff_options", False))
    return decide


# ---------------------------------------------------------------------------
# 1日（9フェイズ）
# ---------------------------------------------------------------------------

def _resolve_refusal(state: GameState, user: str, ability: str, decide,
                     target: str | None = None,
                     declared: dict | None = None) -> bool:
    """脚本家が友好能力を拒否するか。True=解決される／False=拒否された（KB: 20/00/60）。

    拒否できるのは使用キャラの役職が友好無視/絶対友好無視を持つ場合のみ。絶対＝必ず拒否。
    例外：イレギュラー・ナースの能力は役職に友好無視があっても拒否できない（KB: 20）。
    options には「誰の・何の能力・対象」を含める＝AIが能力の脅威度で拒否を判断できる
    （拒否は友好無視バレ＝世界線を絞らせる情報になるため、無害な能力は通すのが賢い）。
    """
    if user in UNREFUSABLE_ABILITY_CHARS:
        return True
    role = state.script.role_of(user)
    if role_absolute_friendship_ignore(role):
        state.pub({"event": "goodwill_refused", "character": user, "ability": ability})
        return False
    if role_has_friendship_ignore(role):
        # ★B-278：[主] の宣言（医者の除去/付与）は拒否より前に済んでいる＝脚本家は
        #   「何を宣言されたか」を見て拒否を判断する（KB 20:14-18）。
        ctx = {"character": user, "ability": ability, "target": target,
               **(declared or {})}
        d = decide("mastermind", "goodwill_refuse",
                   [{"refuse": True, **ctx}, {"refuse": False, **ctx}])
        if d["refuse"]:
            state.pub({"event": "goodwill_refused", "character": user, "ability": ability})
            return False
    return True


def _run_goodwill_phase(state: GameState, decide) -> None:
    """使える友好能力を順に宣言→拒否/解決。パスで終了（00:110）。"""
    used_this_turn: set = set()
    while True:
        options = legal.goodwill_ability_options(state, used_this_turn)
        if len(options) == 1:  # pass のみ
            break
        chosen = decide(state.leader, "goodwill_ability", options)
        if chosen.get("action") == "pass":
            break
        # ご神木の特性（主人公が任意で使用・ハート不要・拒否対象外）：カウンター移動。
        if "goshinboku" in chosen:
            from .effects import apply_goshinboku_move
            apply_goshinboku_move(state, chosen["goshinboku"], chosen["target"])
            used_this_turn.add(("ご神木", "trait"))
            continue
        user, ability = chosen["character"], chosen["ability"]
        used_this_turn.add((user, ability))
        for ab in goodwill_abilities_of(user) or []:
            if ab["name"] == ability and ab["once_per_loop"]:
                state.used_goodwill.add((user, ability))
        # ★B-278：KB 20:14-18＝リーダーが [主] を**すべて**行ってから脚本家が拒否を判断する。
        #   医者『不安操作（除去/付与）』の [主] は 20:228＝「対象1人を選び、**取り除くか置くかも
        #   宣言する**」＝除去/付与の宣言はここ（拒否より前）。宣言内容は公開情報として
        #   `goodwill_used` に載せる（旧実装は拒否後の効果解決内で選ばせていた＝拒否されると
        #   宣言の機会が無く、記録にも残らなかった）。
        declared = abilities.declare_ability(state, user, ability, chosen["target"],
                                             decide=decide, actor=state.leader)
        state.pub({"event": "goodwill_used", "character": user, "ability": ability,
                   "target": chosen["target"], **(declared or {})})
        if _resolve_refusal(state, user, ability, decide, chosen["target"], declared):
            abilities.apply_ability(state, user, ability, chosen["target"],
                                    decide=decide, actor=state.leader, declared=declared)
            state.pub({"event": "goodwill_resolved", "character": user, "ability": ability})


def run_day(state: GameState, decide, human_seats=frozenset()) -> None:
    # 1. ターン開始：登場日を迎えたキャラを初期エリアに配置（30:89 転校生等）
    state.phase = "turn_start"
    for name, day in state.script.entry_days.items():
        c = state.characters[name]
        if day == state.day and c.area is None:
            c.area = initial_area_of(name)
            state.pub({"event": "entry", "name": name, "area": c.area})
    # アルバイト？の登場（アルバイト死亡→次ターン開始に都市へ配置。KB: 30）
    if state.alubaito_spawn_pending:
        q = state.characters.get("アルバイト？")
        if q is not None and q.area is None:
            q.area = "都市"
            state.pub({"event": "entry", "name": "アルバイト？", "area": "都市"})
        state.alubaito_spawn_pending = False
    state.snapshot("脚本家行動フェイズ前")

    # 2. 脚本家行動：3枚を裏向きセット（00:102）
    #    ※この時点の盤面スナップショットは撮らない（主人公行動フェイズ後＝6枚伏せた盤面で
    #      十分に読めるため冗長。ユーザー要望・2026-07-03）。
    state.phase = "mastermind_set"
    _mm_bluff = bool(getattr(decide, "mm_allow_bluff", False))   # ★B-214（既定 False）
    for _ in range(3):
        _opts = legal.set_card_options(state, "mastermind", allow_bluff=_mm_bluff)
        if not _opts:
            # ★縮退ケース（2026-07-08実測）：キャラがほぼ全滅した終盤、対象を取れる
            #   カードが尽きて置ける手が無いことがある（死体セット不可・暗躍+2は1/L・
            #   同一対象の重ね制限）。ルール外の縮退＝置ける分だけセットして続行。
            break
        chosen = decide("mastermind", "set_card", _opts)
        state.turn_placements.append({"owner": "mastermind", **chosen})

    # 3. 主人公行動：リーダーから時計回りに各1枚（00:103）
    state.phase = "protagonist_set"
    for i in range(3):
        seat = PROTAGONIST_SEATS[(state.leader_idx + i) % 3]
        _opts = legal.set_card_options(state, seat)
        if not _opts:
            continue   # 縮退ケース：この席は置けるカードが無い（上記と同じ）
        chosen = decide(seat, "set_card", _opts)
        state.turn_placements.append({"owner": seat, **chosen})
    state.snapshot("主人公行動フェイズ後")

    # 4. 行動解決：全6枚公開→engineで決定的に解決（00:106-108）
    state.phase = "action_resolution"
    # ★「行動解決中」スナップショット（ユーザー要望 2026-07-10）：6枚のカードが全て表になった
    #   ＝解決前の盤面。主人公プレイの「カードを開く」で、主人公行動フェイズ後（裏向き）と
    #   行動解決フェイズ後（解決済み）の間に、6枚公開の局面を1ステップ挟んで見せる。
    #   ★sim/flow.py は共有レーン＝AIB申し送り（reveal_cards付きスナップショットを1つ追加）。
    state.snapshot("行動解決中", reveal_cards=True)
    board = to_engine_board(state)
    violations = validate_board(board)
    if violations:
        raise RuntimeError(f"非合法盤面が生成された（legal.pyのバグ）: "
                           f"{[v.code for v in violations]}")
    # ★カルティストの暗躍禁止無視は任意発動（KB: 40）。無視の有無で暗躍が通るかが変わる対象を
    #   脚本家（人間 or AI）に選ばせる。人間＝脚本家プレイはUIで選択（テスター要望 2026-07-12）。
    #   ★A-42（2026-07-23）：AI対局でも cb を有効化し、脚本家AI（HeuristicMastermind.decide）に
    #   委ねる。脚本家AIは既定で常に True（無視）を返す＝挙動・belief・ベンチは bit-for-bit 不変。
    #   cultist_ignore_gate を有効にすると「勝ち筋を前進させない無視は見送る（＝暗躍禁止を受けて
    #   カルティストを隠す）」情報衛生が働く（A-48/A-49と同じ引き算）。カルティスト不在の局では
    #   resolve_action_phase が cb を呼ばない＝decision も増えない。
    def _cultist_cb(target, target_kind):   # noqa: E306  (True=無視して暗躍を通す)
        opts = [{"action": "cultist_ignore", "target": target,
                 "target_kind": target_kind, "ignore": ig} for ig in (True, False)]
        return bool(decide("mastermind", "cultist_ignore", opts)["ignore"])
    adj = resolve_action_phase(board, cultist_ignore=_cultist_cb)
    # ★まず「全カード公開」を発行（行動解決フェイズのログの先頭＝6枚が公開され、それから解決結果）。
    #   暗躍+2などの解決結果イベントより上に来るようにする（要望 2026-07-06）。
    state.pub({"event": "cards_revealed",
               "placements": [dict(p) for p in state.turn_placements]})
    # ★禁止エリアはじかれの公開イベント化（AIC要望#1/#2・2026-07-10）：移動が禁止先で不成立に
    #   なったキャラを卓上イベントとして残す（silent cancel を可視化）。engine が blocked_moves
    #   で既に判定済み＝ここは写すだけ。行き先エリアは移動札＋出発地（＝留まった＝現在地）から
    #   engine helper で再計算（不能時は area 省略）。UI表示は各プレイモード（play.py=#1・
    #   play_vs_ai.py=#2/AIB）が describe_event 経由で行う。
    #   ★sim/flow.py は共有レーン＝AIB申し送り（history に move_blocked を1種追加のみ）。
    for name in adj.blocked_moves:
        ev = {"event": "move_blocked", "name": name}
        try:
            mv = [p["card"] for p in state.turn_placements
                  if p.get("target") == name and p.get("target_kind") == "character"
                  and p.get("card") in MOVE_CARDS]
            start = adj.moves.get(name) or state.characters[name].area
            if mv and start in AREAS:
                ev["area"] = destination(start, compose_moves(mv))
        except Exception:  # noqa: BLE001  行き先不明でもイベント自体は残す
            pass
        state.pub(ev)
    _pre_areas = {n: c.area for n, c in state.characters.items()}
    for name, area in adj.moves.items():
        state.characters[name].area = area
    # 従者：同エリアだった追随対象（お嬢様/大物/能力追加）の移動に追随（KB: 30）
    apply_juusha_follow(state, _pre_areas)
    for name, cr in adj.unrest.items():
        state.characters[name].unrest = cr.final
    for name, cr in adj.goodwill.items():
        # ★友好の解決結果を公開（卓上でカウンター増減は全員に見える）：
        #   友好禁止が置かれたのに増えた＝タイムトラベラー【強制】の禁止無視＝TT確定、
        #   友好+が置かれたのに増えない＝禁止が実効＝そのキャラはTTでない（推理材料）。
        if cr.final != state.characters[name].goodwill:
            state.pub({"event": "goodwill", "target": name,
                       "delta": cr.final - state.characters[name].goodwill})
        state.characters[name].goodwill = cr.final
    for tgt, tr in adj.targets.items():
        if tgt in AREAS:
            state.board_anyaku[tgt] += tr.delta
            # ★ボード暗躍の解決結果を公開（卓上で見える）：暗躍禁止が置かれたのに
            #   通った＝そのエリアのカルティストが無視した、の推理材料（presentつき）。
            if tr.delta > 0:
                state.pub({"event": "anyaku", "target": tgt, "delta": tr.delta,
                           "present": sorted(
                               n for n, c in state.characters.items()
                               if c.alive and c.on_board and c.area == tgt)})
        else:
            state.characters[tgt].anyaku += tr.delta
    # 1/loopカードは手札に戻らない（00:108）
    for p in state.turn_placements:
        side = "mastermind" if p["owner"] == "mastermind" else "protagonist"
        if p["card"] in ONCE_PER_LOOP[side]:
            state.used_cards[p["owner"]].append(p["card"])
    state.turn_placements = []
    state.snapshot("行動解決フェイズ後")

    # 5. 脚本家能力：使用可能な能力を自由順で各1回（00:109）
    state.phase = "mastermind_ability"
    used: set[str] = set()
    while True:
        options = legal.mastermind_ability_options(state, used)
        if len(options) == 1:  # passのみ
            break
        chosen = decide("mastermind", "mastermind_ability", options)
        if chosen["action"] == "pass":
            break
        apply_mastermind_ability(state, chosen)
        used.add(chosen["action"])
    # ★ご神木の強制（B-233・現物カード 2026-08-16「脚本家もこの特性を用いる（強制）」）。
    #   自由順のループで脚本家が使わずに pass しても、使える組み合わせが残っているなら
    #   必ず1回使わせる（どのカウンターを誰へ移すかの選択だけは脚本家に残る＝pass 無しで decide）。
    #   他能力の適用で対象が消えた場合は options が空＝不発（できないことは強制されない）。
    forced = legal.goshinboku_forced_options(state, used)
    if forced:
        chosen = decide("mastermind", "mastermind_ability", forced)
        apply_mastermind_ability(state, chosen)
        used.add(chosen["action"])
    # ★B-234：「不発生」の観測を公開イベント化する。ご神木にカウンターがあり同エリアに
    #   生存他キャラが居るのに、この脚本家能力フェイズで特性が使われなかった＝上の強制段が
    #   空だった＝**ご神木の役職は友好無視を持たない**（B-233 の是正で初めて健全になった演繹）。
    #   ★これは卓上では誰にでも見えている公開事実（カウンター・エリア・生死）だが、
    #     移動とキャラ暗躍の増減は公開イベント列に残らないため `history` から復元できない
    #     ＝判定した当人（sim）が書き出す。先例＝`anyaku` の `present`／`loop_end` の
    #     `toshi_anyaku`（どちらも「卓上では見えるが復元が面倒な公開事実」の書き出し）。
    #   ★秘匿情報は載せない（役職も、友好無視の有無も、イベント名以外の何も出さない）。
    if legal.goshinboku_idle_observed(state, used):
        state.pub({"event": "goshinboku_idle"})
    state.snapshot("脚本家能力フェイズ後")

    # 6. 主人公能力：リーダーが友好能力の使用を宣言→脚本家が拒否/解決（00:110 / 20）。
    state.phase = "goodwill_ability"
    _run_goodwill_phase(state, decide)
    state.snapshot("主人公能力フェイズ後")
    # ★ループ終了効果（KP/主人公死亡等）は即終了＝事件フェイズ以降を走らせない
    #   （ルール: ループ終了効果は即終了。テスター指摘 2026-07-11：goodwill フェイズで
    #   異世界人の殺害能力によりKPが死亡した後も事件フェイズが走っていた＝犯人の不安が
    #   臨界以上なら終了後に事件が発火する潜在バグ）。敗北判定/loop_board/loop_result は
    #   run_loop の evaluate_loop_end が別途発行する（このreturnでは落ちない）。
    #   ★sim/flow.py は共有レーン＝AIB申し送り（goodwillフェイズ後にloop_end短絡を1つ追加）。
    if state.loop_end_triggered:
        return

    # 7. 事件（00:115-123 / 40）
    state.phase = "incident"
    resolve_incident_phase(state, decide)
    state.snapshot("事件フェイズ後")
    if state.loop_end_triggered:
        return

    # 8. リーダー交代（00:112）
    state.phase = "leader_change"
    state.rotate_leader()

    # 9. ターン終了：【強制】→【任意】の役職能力（00:113, 176-178）
    state.phase = "turn_end"
    resolve_turn_end(state, decide)
    state.snapshot("ターン終了フェイズ後")  # ★死亡判定後の盤面（フェイズ送り・死者リスト用）


# ---------------------------------------------------------------------------
# ループとゲーム
# ---------------------------------------------------------------------------

def _dynamic_areas_for_loop(state: GameState, decide) -> dict[str, str]:
    """初期エリアが脚本家指定のキャラ（手先・従者）を決めさせる（KB: 30:23）。

    ★KB: 30:23 は2体を**別扱い**にしている：
      - **手先**＝「脚本家が**各ループで**初期エリアを指定する」（カードは4枠点灯）＝毎ループ選ぶ。
      - **従者**＝「配置時に脚本家が都市か学校のどちらかを選ぶ（カードは2枠点灯。
        **ループごとには変えない一度きりの選択**）」＝**初回の1回だけ**選び、以後は再利用する。
    ★B-29x（2026-09-02・トリアージ A-3）：以前は両者を同じ扱いにして**毎ループ選び直して**いた。
      一度きりの選択は `state.fixed_initial_areas` に記録される（prepare_loop が実際に使った値）。
    """
    areas: dict[str, str] = {}
    for name in state.script.cast:
        if initial_area_of(name) is None:
            if name in ONE_TIME_INITIAL_AREA:
                fixed = state.fixed_initial_areas.get(name)
                if fixed is not None:
                    areas[name] = fixed   # 2ループ目以降＝選び直さない（決定も発生しない）
                    continue
            # 従者は都市/学校の2択（カードの2枠点灯・KB: 30）／手先は全4エリア。
            opts = ["都市", "学校"] if name in ONE_TIME_INITIAL_AREA else list(AREAS)
            chosen = decide("mastermind", "loop_start_area",
                            [{"name": name, "area": a} for a in opts])
            areas[name] = chosen["area"]
    return areas


def _run_final_battle(state: GameState, decide) -> None:
    """最後の戦い（BTX）：主人公が全キャラの役職を宣言。1つでも誤答→脚本家勝利、
    全問正解→主人公勝利（00:153-156）。盤面はループ開始状態に戻す（00:148）が役職当てには不要。"""
    from .state import BTX_ROLE_UNIVERSE

    state.phase = "final_battle"

    def _fb(ev):  # 最後の戦いは day=0（時点は「最後の戦い」）
        state.history.append({"loop": state.loop_no, "day": 0,
                              "phase": "final_battle", **ev})

    for name in state.script.cast:
        options = [{"character": name, "role": r} for r in BTX_ROLE_UNIVERSE]
        guess = decide(state.leader, "final_battle_guess", options)
        truth = state.script.role_of(name)
        correct = guess["role"] == truth
        _fb({"event": "final_battle", "name": name, "guess": guess["role"], "correct": correct})
        if not correct:
            state.game_over = True
            state.winner = "mastermind"
            state.final_battle_pending = False
            _fb({"event": "game_over", "winner": "mastermind"})
            return
    state.game_over = True
    state.winner = "protagonist"
    state.final_battle_pending = False
    _fb({"event": "game_over", "winner": "protagonist"})


def run_loop(state: GameState, decide, on_day_start=None,
             final_battle: bool = True, human_seats=frozenset(),
             *, resume_from_state: bool = False) -> None:
    """状態と decide コールバックを受け取り、ゲームを最後まで進める（進行ロジックの単一実装）。

    decide(actor, decision, options)->option。AI対戦も人間参加もこの関数を共用し、
    違いは decide の中身だけ（人間参加は決定待ちで例外を投げて中断する＝arena/interactive）。
    on_day_start(state)＝各日の run_day 直前フック（詰み分類の局面スナップショット用。
    ループ開始処理・学者特性の後＝mate.classify_day が想定する「日開始局面」）。
    final_battle=False＝最後の戦いを行わず、最終ループを守れなければ脚本家の勝ちで終える
    （脚本家プレイの4ループ打ち切り用。ユーザー/AIC方針 2026-07-11）。

    ★resume_from_state=True（Supabase Phase 1・2026-07-17・FableA裁定(a)）：
      **復元したスナップショットから続きを進める**入口。最初のループだけ「ループの準備」
      （prepare_loop＋loop_start イベント＋学者特性）を**飛ばし**、state.loop_no/state.day の
      その日から進める（それらは復元前に既に実行済み＝もう一度やると盤面を壊す）。
      2ループ目以降は通常どおり準備する＝以降の意味論は完全に同じ。
      ★既定は False＝**新規開始の経路は一切変わらない**（additive）。
      ★前提＝state は「日の開始前」（run_day 実行前＝on_day_start フックの時点）であること。
      run_day は日の先頭（ターン開始フェイズ）から線形に走る＝日の途中からは再開できない
      （途中局面は「日境界snapshot＋その日の human_choices の再生」で表す＝arena/interactive）。
    """
    if resume_from_state:
        # ★前提条件ガード（AIBレビュー指摘・2026-07-17）：resume は「ループ準備済みの局面」を
        #   前提に準備を飛ばす。未準備の state を渡すと、飛ばした prepare_loop が埋めるはずの
        #   characters/board_anyaku/used_cards が空のまま進み、**意味の分からない場所で**落ちる
        #   （実測：KeyError: 'p1'（used_cards）や KeyError: '学校'（board_anyaku））。
        #   復元経路が増える今、ここで「意味の分かる失敗」に変えておくのが最安。
        _missing = [n for n, v in (("characters", state.characters),
                                   ("board_anyaku", state.board_anyaku),
                                   ("used_cards", state.used_cards)) if not v]
        if _missing:
            raise ValueError(
                f"resume_from_state=True には『ループ準備済みの局面』が必要です"
                f"（未設定: {'/'.join(_missing)}）。GameState.from_snapshot() で復元した局面か、"
                f"prepare_loop() 済みの局面を渡してください。新規開始なら resume_from_state を"
                f"外してください（＝run_loop がループの準備から始めます）。")
    _skip_prepare = resume_from_state
    while not state.game_over:
        if _skip_prepare:
            _skip_prepare = False       # 復元した最初のループだけ準備を飛ばす
        else:
            _prepare_new_loop(state, decide)
        while True:
            if on_day_start is not None:
                on_day_start(state)
            run_day(state, decide, human_seats=human_seats)
            if state.loop_end_triggered or state.day >= state.script.days_per_loop:
                break
            state.day += 1
        evaluate_loop_end(state)
        if state.final_battle_pending:
            if final_battle:
                _run_final_battle(state, decide)
            else:
                # 最後の戦いを行わない＝最終ループを守れなかった＝脚本家の勝ちで終える。
                state.final_battle_pending = False
                state.game_over = True
                state.winner = "mastermind"
        if not state.game_over:
            state.loop_no += 1


def _prepare_new_loop(state: GameState, decide) -> None:
    """1ループ分の準備（カウンター除去・初期配置・手札・学者特性）。run_loop から切り出しただけ
    ＝挙動は不変（★resume 入口が「準備だけ飛ばす」ために関数境界が要る・2026-07-17）。"""
    state.phase = "loop_start"
    state.prepare_loop(_dynamic_areas_for_loop(state, decide))
    state.history.append({"loop": state.loop_no, "day": 0,
                          "phase": "loop_start", "event": "loop_start"})
    # ★学者の特性【強制】（現物確認 2026-07-09）：各ループ開始時、学者に
    #   友好/不安/暗躍カウンターのいずれか1つを置く（種類は脚本家が選択）。
    #   イベントは専用名で公開（因果の糸検出は unrest delta==2 限定＝混同しない）。
    _sch = state.characters.get("学者")
    if _sch is not None and _sch.alive and _sch.on_board:
        _c = decide("mastermind", "scholar_counter",
                    [{"counter": k} for k in ("友好", "不安", "暗躍")])
        if _c["counter"] == "友好":
            _sch.goodwill += 1
        elif _c["counter"] == "不安":
            _sch.unrest += 1
        else:
            _sch.anyaku += 1
        state.history.append({"loop": state.loop_no, "day": 0,
                              "phase": "loop_start", "event": "scholar_trait",
                              "counter": _c["counter"]})


def run_game(script: Script, agents: dict, log: list[dict] | None = None,
             on_day_start=None) -> tuple[GameState, list[dict]]:
    """1ゲームを最後まで回す。agents: {"mastermind": Agent, "p1": ..., "p2": ..., "p3": ...}。

    返り値: (最終状態, 決定ログ)。決定ログはリプレイ・デバッグの一次資料（計画 §3）。
    """
    validate_script(script)
    state = GameState(script=script)
    log = [] if log is None else log
    decide = _make_decider(state, agents, log)
    run_loop(state, decide, on_day_start=on_day_start)
    return state, log
