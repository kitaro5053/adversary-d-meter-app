"""効果の実効（M0台帳＝docs/M0_FS効果インベントリ.md の A/B/C を盤面に適用する）。

現行 engine/ が意図的に裁定しない「効果」をここで実装する。範囲はFSのみ。
- 死亡処理：キーパーソン死亡＝主人公敗北＋直ちにループ終了（40:80）。
  主人公死亡＝敗北＋直ちにループ終了（00:65）。カウンターは死体に残る（00:34）。
- 事件効果7種（40:146-154）。「任意の〜」は脚本家の選択＝decide コールバック経由。
- ターン終了フェイズ：【強制】シリアルキラー（同時解決）→【任意】キラー（00:176-178）。
- ループ終了判定：フレンド死亡公開・ルールYのボード条件・勝敗（00:131-142, 40）。

decide(actor, decision_type, options) -> option は flow が渡す（決定ログに自動記録される）。
history には結果のみ（理由なし＝00:127）、secret_log には理由（死因・能力名）を書く。
"""

from __future__ import annotations

from engine import Board, Character, Placement, resolve_incident
from engine.incident import effective_unrest_for_incident  # W4: A.I. 合算の単一ソース
from engine.turn_end_rules import (
    is_immortal,
    killer_can_kill_protagonist,
    killer_killable_keypersons,
    mainlover_can_kill_protagonist,
    serial_killer_victims,
    timetraveler_can_defeat,
)

from .state import GameState, Incident, current_forbidden


# ---------------------------------------------------------------------------
# engine Board への変換（行動解決・事件判定で使用）
# ---------------------------------------------------------------------------

def to_engine_board(state: GameState) -> Board:
    """盤上のキャラ＋今ターンのセット札を engine の Board に写す。

    ★B-30b：大物の縄張りトークン（盤上の公開情報・KB: 20）も写す＝engine 側の
      カルティスト無視判定がテリトリーを見られるようにする（60 E-3b A2）。
    """
    b = Board()
    b.oomono_territory = getattr(state.script, "oomono_territory", None)
    for c in state.characters.values():
        if not c.on_board:
            continue
        # 医者能力3等でこのループ禁止エリアを失っているキャラは forbidden 空で写す
        # （E-2：判定は state.current_forbidden に一本化＝挙動は従来と同一）
        forb = current_forbidden(state, c.name)
        b.add_character(Character(
            name=c.name, role=c.role, area=c.area,
            forbidden=forb, alive=c.alive,
            goodwill=c.goodwill, unrest=c.unrest,
            anyaku=c.anyaku, guard=c.guard,  # A.I.の発生判定（全カウンター合算・KB:30）等に必要
        ))
    for p in state.turn_placements:
        b.add_placement(Placement(p["owner"], p["card"], p["target"], p["target_kind"]))
    return b


# ---------------------------------------------------------------------------
# 死亡処理
# ---------------------------------------------------------------------------

def _pub(state: GameState, event: dict) -> None:
    state.pub(event)


def _sec(state: GameState, event: dict) -> None:
    state.secret_log.append({"loop": state.loop_no, "day": state.day, **event})


def factor_has_kp_ability(state: GameState, name: str) -> bool:
    """そのキャラが「KPの追加能力を獲得中のファクター」か（B-34・KB: 50:174-176 / 60 C-1 ※整理）。

    ファクターは【強制】**都市に暗躍カウンターが2つ以上**の間、キーパーソンに記載された追加能力
    （＝「死亡した時、主人公は敗北し直ちにループ終了」40:80）を得る＝その状態での死亡はループ終了。
    ★動的＝都市暗躍≥2 の間だけ（暗躍除去で解除されうる）＝ML側の gains_misleader（学校≥2）と同型。
    ★役職参照条件（僕と契約の「KP暗躍≥2」等）には算入しない（60 C-1）＝この関数は死亡トリガ専用。
    """
    c = state.characters.get(name)
    return bool(c is not None and c.role == "ファクター"
                and state.board_anyaku.get("都市", 0) >= 2)


def kill_character(state: GameState, name: str, cause: str) -> None:
    """キャラ死亡。理由は公開しない（00:127）。キーパーソン（またはKP能力獲得中のファクター）なら
    ループ終了効果（B-34）。"""
    c = state.characters[name]
    if not c.alive:
        return
    if is_immortal(c.role):  # タイムトラベラー等は死亡しない（KB: 00/50 記法）
        _sec(state, {"event": "death_prevented", "name": name, "cause": cause})
        return
    if c.guard > 0:  # 護衛カウンターがあれば死亡の代わりに1消費（KB: 20 刑事）
        c.guard -= 1
        _pub(state, {"event": "guard_consumed", "name": name})
        _sec(state, {"event": "guard_consumed", "name": name, "cause": cause})
        return
    # ★従者の身代わり（KB: 30・現物確認 2026-07-23）：お嬢様/大物（＋友好能力で特性対象に
    #   追加された者）が死亡する時、同一エリアに従者が生存していれば、従者が**代わりに**死ぬ
    #   （強制）＝主は生存。従者が死亡済み/別エリア/対象自身が従者なら通常どおり主が死ぬ。
    #   ★主側の死亡回避（不死・ガード）は上で先に判定済み＝主が実際に死ぬ時だけ身代わりが発動。
    #   ★belief含意（§4）＝お嬢様/大物の実質不死壁＝致命局面で主が生き従者が死ぬ（推理材料）。
    if name != "従者" and name in juusha_follow_targets(state):
        ju = state.characters.get("従者")
        if ju is not None and ju.alive and ju.on_board and ju.area == c.area:
            _sec(state, {"event": "juusha_substitute", "protected": name, "cause": cause})
            kill_character(state, "従者", f"従者の身代わり（{name}の代わりに死亡）")
            return
    # ★present＝死の瞬間にそのエリアに居た顔ぶれ（公開情報）。ターン終了フェイズの死は
    #   シリアルキラー（2人きり）かキラー（キーパーソン殺害）に限られる＝強い推理材料。
    #   present_unrest＝その瞬間の各人の不安（卓上カウンター＝公開）。妄想拡大ウイルスの
    #   SK化（不安≥3・≤1で解除）の可否判定に使う。
    #   present_anyaku＝その瞬間の各人の暗躍（卓上カウンター＝公開）。キラーのKP殺害は
    #   KP暗躍≥2 が必須（40:94）＝被害者の暗躍<2なら「キラーによる殺害」は物理的に不可能
    #   ＝2人きり死ならSK確定＝belief推理の材料（AIC診断 2026-07-09）。
    present = _present_in(state, c.area) if c.area else []
    c.alive = False
    _pub(state, {"event": "death", "name": name, "present": present,
                 "present_unrest": {n: state.characters[n].unrest for n in present},
                 "present_anyaku": {n: state.characters[n].anyaku for n in present}})
    _sec(state, {"event": "death", "name": name, "cause": cause})
    # ラバーズ/メインラバーズ：片方が死亡した時、生存している相方に不安+6（50:153,159）。
    # 同時死亡なら相方は既に死体＝置く対象がいない（A19）＝aliveチェックで自然に何も起きない。
    if c.role in ("ラバーズ", "メインラバーズ"):
        partner_role = "メインラバーズ" if c.role == "ラバーズ" else "ラバーズ"
        for o in state.characters.values():
            if o.role == partner_role and o.alive:
                o.unrest += 6
                _pub(state, {"event": "unrest", "target": o.name, "delta": 6})
    # アルバイトの死亡＝次ターン開始に都市へアルバイト？を配置（KB: 30）
    if name == "アルバイト":
        state.alubaito_spawn_pending = True
    # ★従者の身代わりは kill_character 冒頭で処理済み（主の死亡確定より前・KB: 30 現物確認）＝
    #   「追随して一緒に死ぬ」は誤り（旧実装）。ここでは主が実際に死んだ後の処理のみ続ける。
    if c.role == "キーパーソン" or factor_has_kp_ability(state, name):
        # 【強制】死亡した時、主人公は敗北し直ちにループ終了（40:80）。
        # ★B-34（KB: 50:174-176 / 60 C-1 ※整理）：KPの「死亡→敗北+即ループ終了」は追加能力
        #   （条文能力なし・能力欄記載）＝**都市に暗躍≥2 の間のファクターはこれを獲得する**
        #   （50:174・ML側の学校≥2＝ミスリーダー能力と同型の動的live評価）。能力は都市暗躍≥2の
        #   間だけ＝暗躍除去で解除されうる（→factor_has_kp_ability）。C-1 が否定するのは役職参照
        #   条件（僕と契約の「KP暗躍≥2」等）への算入のみ＝この死亡トリガとは別問題。
        # （友好スナップショットはTT宣言と対称に付ける＝フィールドの有無が読み筋にならない）
        state.defeat = True
        state.loop_end_triggered = True
        # ★B-34：都市暗躍を添える（公開情報）＝belief が「この死は KP か、都市暗躍≥2 の
        #   KP能力獲得ファクターか」を精度良く分ける材料（都市<2 なら factor 化不可＝strict KP・
        #   ≥2 なら KP or ファクター）。他の loop_end 読み手には無害な追加フィールド。
        _pub(state, {"event": "loop_end", "reason": "主人公の敗北（ループ終了効果）",
                     "toshi_anyaku": state.board_anyaku.get("都市", 0),
                     "goodwill": {n2: c2.goodwill for n2, c2 in state.characters.items()
                                  if c2.alive and c2.on_board}})
        _kp_label = "キーパーソン" if c.role == "キーパーソン" else "KP能力獲得ファクター"
        _sec(state, {"event": "loop_end", "reason": f"{_kp_label}〈{name}〉死亡"})


def kill_protagonists(state: GameState, cause: str) -> None:
    """主人公死亡＝敗北としてループ直ちに終了（00:65）。"""
    if not state.protagonists_alive:
        return
    if state.protagonist_immortal:  # 軍人の友好能力2：このループ主人公は死亡しない（KB: 20）
        _sec(state, {"event": "protagonist_death_prevented", "cause": cause})
        return
    state.protagonists_alive = False
    state.defeat = True
    state.loop_end_triggered = True
    # ★その瞬間の暗躍・不安（卓上の公開カウンター）を添付：ターン終了の主人公死亡は
    #   キラー（自暗躍≥4・40:95）かメインラバーズ（不安≥3＋暗躍≥1・50:160）に限られる
    #   ＝誰の仕業かをbeliefが絞れる強い証拠になる。
    alive = [(n, c) for n, c in state.characters.items() if c.alive and c.on_board]
    _pub(state, {"event": "protagonist_death",
                 "anyaku": {n: c.anyaku for n, c in alive if c.anyaku > 0},
                 "unrest": {n: c.unrest for n, c in alive if c.unrest > 0}})
    _pub(state, {"event": "loop_end", "reason": "主人公の死亡"})
    _sec(state, {"event": "protagonist_death", "cause": cause})


# ---------------------------------------------------------------------------
# 従者／アルバイトの特性（KB: 30・[拡張]）
# ---------------------------------------------------------------------------

def juusha_follow_targets(state: GameState) -> set:
    """従者の追随対象＝お嬢様/大物（基本）＋友好能力で追加した対象（このループ）。"""
    base = {"お嬢様", "大物"} | set(state.juusha_targets)
    return base & set(state.characters)


def apply_juusha_follow(state: GameState, pre_areas: dict) -> dict | None:
    """行動解決の移動後、従者が同エリアだった追随対象の移動に追随する（KB: 30・現物確認 2026-07-23）。

    pre_areas＝移動適用前の {name: area}。従者と同エリアから動いた追随対象へ従者も移す。
    ★spec1「自身への移動を無視して一緒に移動」＝この関数は resolve_move の**後**に呼ばれ、
      従者自身の移動先を主の移動先で上書きする＝自身の移動は無視される（従者は主と同じ先へ）。
    ★spec2「重複する場合はリーダーが選べる」＝お嬢様/大物が別々の先へ動く重複時は、sim は
      決定的タイブレーク（sorted 先頭）で解決しつつ **要確認を申告**して返す（リーダー選択の
      完全実装は未＝FableA裁定の申告方式。sim にリーダー（state.leader）はあるが、この稀な
      移動タイブレークのための決定フックは未配線＝どの選択も合法なので決定的解決で足りる）。

    戻り値＝重複が起きた時だけ {"ambiguous": True, "candidates": [...]}（それ以外は None）。
    """
    ju = state.characters.get("従者")
    if ju is None or not (ju.alive and ju.on_board):
        return None
    ju_start = pre_areas.get("従者")
    moved = []   # 従者の出発地から動いた追随対象の (name, 行き先)
    for t in sorted(juusha_follow_targets(state)):
        if t == "従者":
            continue
        tc = state.characters.get(t)
        if tc is None or not (tc.alive and tc.on_board):
            continue
        if pre_areas.get(t) == ju_start and tc.area != pre_areas.get(t):
            moved.append((t, tc.area))
    if not moved:
        return None
    dests = {area for _n, area in moved}
    first_dest = moved[0][1]
    if ju.area != first_dest:
        ju.area = first_dest
        _pub(state, {"event": "move", "name": "従者", "to": first_dest})
    if len(dests) > 1:
        # ★重複＝別々の先へ動く追随対象が2人以上＝本来はリーダーが選ぶ。決定的に解決したが申告。
        warn = {"ambiguous": True, "candidates": moved, "chosen": first_dest}
        _sec(state, {"event": "juusha_follow_ambiguous", **warn})
        return warn
    return None


def check_alubaito_death(state: GameState) -> None:
    """アルバイトは上のカウンター合計が3以上で死亡（KB: 30）。ターン終了時に判定。"""
    ab = state.characters.get("アルバイト")
    if ab is not None and ab.alive and ab.on_board:
        total = ab.unrest + ab.goodwill + ab.anyaku + ab.guard
        if total >= 3:
            kill_character(state, "アルバイト", f"アルバイト特性（総カウンター{total}≥3）")


# ---------------------------------------------------------------------------
# 脚本家能力フェイズ（legal.mastermind_ability_options で選ばれた1件を適用）
# ---------------------------------------------------------------------------

def _present_in(state: GameState, area: str) -> list[str]:
    """そのエリアに居る生存キャラ（公開情報＝卓上で見えている顔ぶれ）。"""
    return sorted(n for n, c in state.characters.items()
                  if c.alive and c.on_board and c.area == area)


def apply_mastermind_ability(state: GameState, choice: dict) -> None:
    # ★present＝発動時にそのエリアに居た顔ぶれ（公開情報）。能力の「同一エリア」要求から
    #   ミスリーダー/クロマクの候補者集合を絞る推理材料になる（beliefが使う）。
    if choice["kind"] == "anyaku":
        if choice["target_kind"] == "board":
            state.board_anyaku[choice["target"]] += 1
            present = _present_in(state, choice["target"])
        else:
            state.characters[choice["target"]].anyaku += 1
            present = _present_in(state, state.characters[choice["target"]].area)
        _pub(state, {"event": "anyaku", "target": choice["target"], "delta": 1,
                     "present": present})
        if choice["action"] == "不穏な噂":
            state.rumor_used = True
    elif choice["kind"] == "unrest":
        state.characters[choice["target"]].unrest += 1
        _pub(state, {"event": "unrest", "target": choice["target"], "delta": 1,
                     "present": _present_in(state, state.characters[choice["target"]].area)})
    elif choice["kind"] == "unrest_minus":
        # 医者の友好能力（脚本家使用）：不安1除去（0未満にはならない）
        c = state.characters[choice["target"]]
        if c.unrest > 0:
            c.unrest -= 1
            _pub(state, {"event": "unrest", "target": choice["target"], "delta": -1})
    elif choice["kind"] == "goshinboku":
        # ご神木の特性（友好無視時に脚本家能力フェイズで使用・KB: 30）：
        #   ご神木の上のカウンター1つを同エリアの他キャラへ移す。
        apply_goshinboku_move(state, choice["counter"], choice["target"])
    else:
        raise ValueError(f"不明な脚本家能力: {choice}")
    _sec(state, {"event": "mm_ability", "choice": dict(choice)})


# ---------------------------------------------------------------------------
# ご神木の特性：上のカウンター1つを同エリアの他キャラ1人へ移す（KB: 30）。
# ・主人公能力フェイズ：主人公が任意で使用（カード「移してもよい」＝任意。ハート不要）。
# ・脚本家能力フェイズ：ご神木が友好無視を持つ場合、脚本家は**強制で使用**
#   （現物カード確認 2026-08-16＝「脚本家能力フェイズに脚本家もこの特性を用いる（強制）」。
#   ★2026-08-16 まで sim は「脚本家の選択肢」として実装していた＝KB 逸脱。当時の言い訳
#   「ご神木はAI生成対象外の[拡張]」は `sim/generator.py:39-40`（2026-07-10）で失効済み。B-233）。
#   強制なのは**使用すること**で、どのカウンターを誰へ移すかは脚本家が選ぶ。
#   実行できる組み合わせが1つも無い場合（カウンター0／同エリアに他キャラ無し）は不発。
# 医者/鑑識官のカウンター操作と同じ単一フェイズ処理（フェイズ跨ぎではない）。
# ---------------------------------------------------------------------------

_GONOKI_COUNTERS = ("anyaku", "unrest", "goodwill")
_GONOKI_JP = {"anyaku": "暗躍", "unrest": "不安", "goodwill": "友好"}


def goshinboku_move_options(state: GameState, goshinboku: str = "ご神木") -> list[tuple[str, str]]:
    """ご神木の上のカウンター1つを移せる (counter属性, 移動先キャラ名) の一覧。前提未達なら空。"""
    c = state.characters.get(goshinboku)
    if not (c is not None and c.alive and c.on_board):
        return []
    others = _alive_others(state, c.area, goshinboku)
    if not others:
        return []
    return [(attr, t) for attr in _GONOKI_COUNTERS if getattr(c, attr) > 0 for t in others]


def apply_goshinboku_move(state: GameState, counter: str, target: str,
                      goshinboku: str = "ご神木") -> None:
    c = state.characters[goshinboku]
    if getattr(c, counter) <= 0:
        return
    setattr(c, counter, getattr(c, counter) - 1)
    tc = state.characters[target]
    setattr(tc, counter, getattr(tc, counter) + 1)
    jp = _GONOKI_JP[counter]
    _pub(state, {"event": "goshinboku_move", "counter": jp, "from": goshinboku, "to": target})
    _sec(state, {"event": "goshinboku_move", "counter": jp, "from": goshinboku, "to": target})


# ---------------------------------------------------------------------------
# 事件フェイズ（発生判定＝engine.resolve_incident、効果＝ここで適用）
# ---------------------------------------------------------------------------

def _alive_others(state: GameState, area: str, exclude: str) -> list[str]:
    return [
        n for n, c in state.characters.items()
        if c.alive and c.on_board and c.area == area and n != exclude
    ]


def _alive_all(state: GameState) -> list[str]:
    return [n for n, c in state.characters.items() if c.alive and c.on_board]


def _effective_culprit(state: GameState, culprit: str) -> str:
    """犯人の実効解決（B-50・現物確認 2026-07-24）：犯人がアルバイトで盤上に居らず
    （死亡）、後継のアルバイト？が登場していれば、犯人性はアルバイト？が継承する
    （アルバイト？特性「犯人かどうかはアルバイトと一致」＝無視特性なし＝実発揮）。
    ★段階A（inert）＝generator がアルバイトを犯人に指定しない現状では発火しない。"""
    if culprit == "アルバイト":
        ab = state.characters.get("アルバイト")
        q = state.characters.get("アルバイト？")
        if (ab is None or not (ab.alive and ab.on_board)) and \
           q is not None and q.alive and q.on_board:
            return "アルバイト？"
    return culprit


def resolve_incident_phase(state: GameState, decide) -> None:
    """その日の予定事件の発生判定＋効果適用。発生/非発生は必ずアナウンス（00:118）。"""
    inc = next((i for i in state.script.incidents if i.day == state.day), None)
    if inc is None:
        return
    # ★犯人性の継承（アルバイト→アルバイト？）を先に解決してから以降を判定する。
    culprit = _effective_culprit(state, inc.culprit)
    # 手先の友好能力：このループ中、犯人が手先である事件は発生しない（KB: 20）
    if state.tesaki_suppressed and culprit == "手先":
        _pub(state, {"event": "incident", "name": inc.name, "occurs": False})
        _sec(state, {"event": "incident", "name": inc.name, "occurs": False,
                     "culprit": culprit, "reasons": ["手先の友好能力で不発"]})
        return
    # 犯人が盤上に居ない（死亡・未登場＝アルバイト？がアルバイト死亡前／転校生・神格の登場前）
    #   なら事件は発生しない（犯人が居なければ犯行できない・発生条件「犯人が生存」を満たさない）。
    cc = state.characters.get(culprit)
    if cc is None or not (cc.alive and cc.on_board):
        _pub(state, {"event": "incident", "name": inc.name, "occurs": False})
        _sec(state, {"event": "incident", "name": inc.name, "occurs": False,
                     "culprit": culprit, "reasons": ["犯人が盤上に居ない（未登場/死亡）"]})
        return
    res = resolve_incident(to_engine_board(state), {"culprit": culprit, "name": inc.name},
                           set_name=state.script.set_name)
    if res.occurs is None:
        raise RuntimeError(f"事件判定が要確認になった（シミュレータ前提バグ）: {res.reasons}")
    # ★eligible＝この瞬間「生存かつ不安臨界以上」のキャラ（公開情報＝卓上の不安カウンターから
    #   誰でも数えられる）。発生→犯人∈eligible／不発→犯人∉eligible の強い絞り込みに使う。
    #   ★W4（2026-09-04・§72-135）：A.I. だけは特性②（KB 30:50＝全カウンターを不安として扱う）
    #   で臨界判定する＝上の resolve_incident と**同じ関数**（engine 側が単一ソース）を通す。
    #   A.I. 以外は effective_unrest_for_incident が素の unrest を返す＝従来と同一式。
    #   卓上の友好・暗躍・護衛カウンターも公開情報なので、主人公が数えられる範囲は変わらない。
    from engine.data import unrest_threshold_of as _th
    eligible = sorted(
        n for n, c in state.characters.items()
        if c.alive and c.on_board and _th(n) is not None
        and effective_unrest_for_incident(c) >= _th(n))
    _pub(state, {"event": "incident", "name": inc.name, "occurs": res.occurs,
                 "eligible": eligible})
    _sec(state, {"event": "incident", "name": inc.name, "occurs": res.occurs,
                 "culprit": culprit, "reasons": list(res.reasons)})
    if not res.occurs:
        return
    if culprit == "黒猫":
        # 黒猫特性2：事件効果は「何も起きない」に変更（30 特性・発生宣言はされる）
        _pub(state, {"event": "incident_effect", "name": inc.name, "note": "何も起きなかった"})
        return
    _apply_incident_effect(state, inc, decide)
    # 教祖の特性（KB: 30）：自身が犯人の事件は効果をもう一度解決する。ただし1回目で
    # ループ終了（KP死等）が起きたら以降の処理は行われない＝2回目は解決しない。
    if culprit == "教祖" and not state.loop_end_triggered:
        _sec(state, {"event": "guru_double_resolve", "name": inc.name})
        _apply_incident_effect(state, inc, decide)


def _apply_incident_effect(state: GameState, inc: Incident, decide,
                           chooser: str = "mastermind") -> None:
    """事件効果を適用。chooser＝対象選択を行う手番（通常は脚本家。A.I.の友好能力では
    リーダーが選択する＝KB: 20 A.I.）。"""
    name = inc.name
    # ★B-50：犯人性の継承（アルバイト→アルバイト？）は効果適用側でも同じ解決を使う
    #   （A.I.の友好能力経由の解決も同一経路＝二重定義しない）。
    culprit = state.characters[_effective_culprit(state, inc.culprit)]

    if name == "殺人事件":
        # 可能ならば犯人と同エリアの犯人以外1人を死亡（40:148）
        candidates = _alive_others(state, culprit.area, culprit.name)
        if not candidates:
            _pub(state, {"event": "incident_effect", "name": name, "note": "何も起きなかった"})
            return
        chosen = decide(chooser, "incident_choice",
                        [{"target": t} for t in candidates])
        kill_character(state, chosen["target"], f"殺人事件（犯人:{culprit.name}）")

    elif name == "自殺":
        kill_character(state, culprit.name, "自殺（犯人自身）")

    elif name == "邪気の汚染":
        # 神社に暗躍カウンターを2つ置く（50:199）
        state.board_anyaku["神社"] += 2
        _pub(state, {"event": "anyaku", "target": "神社", "delta": 2})

    elif name == "蝶の羽ばたき":
        # 友好/不安/暗躍から1種を選び、犯人と同エリアの1人に1つ置く（50:218）。
        # 未来改変プランの敗北条件トリガー（発生した事実を記録）。
        state.butterfly_fired = True
        same_area = _alive_others(state, culprit.area, "")
        if same_area:
            kind = decide(chooser, "incident_choice",
                          [{"kind": k} for k in ("暗躍", "不安", "友好")])["kind"]
            tgt = decide(chooser, "incident_choice",
                         [{"target": t} for t in same_area])["target"]
            c = state.characters[tgt]
            setattr(c, {"暗躍": "anyaku", "不安": "unrest", "友好": "goodwill"}[kind],
                    getattr(c, {"暗躍": "anyaku", "不安": "unrest", "友好": "goodwill"}[kind]) + 1)
            _pub(state, {"event": {"暗躍": "anyaku", "不安": "unrest", "友好": "goodwill"}[kind],
                         "target": tgt, "delta": 1})
            # ★効果は「犯人と同エリアの1人」＝着地エリアの顔ぶれが犯人候補（公開情報：
            #   卓上で効果の対象と立ち位置は全員に見える）。belief の犯人絞り込みに使う
            #   （テスター指摘 2026-07-09：蝶の羽ばたきの犯人を盤面から絞れていない）。
            _pub(state, {"event": "incident_effect", "name": name, "target": tgt,
                         "present": sorted(same_area)})

    elif name == "不安拡大":
        # 任意1人に不安+2、別の任意1人に暗躍+1（40:149）
        alive = _alive_all(state)
        first = decide(chooser, "incident_choice", [{"target": t} for t in alive])
        state.characters[first["target"]].unrest += 2
        _pub(state, {"event": "unrest", "target": first["target"], "delta": 2})
        rest = [t for t in alive if t != first["target"]]
        if rest:
            second = decide(chooser, "incident_choice", [{"target": t} for t in rest])
            state.characters[second["target"]].anyaku += 1
            _pub(state, {"event": "anyaku", "target": second["target"], "delta": 1})

    elif name == "病院の事件":
        # 病院暗躍≥1→病院の全員死亡／≥2→主人公も死亡（40:151）
        n = state.board_anyaku["病院"]
        if n < 1:
            _pub(state, {"event": "incident_effect", "name": name, "note": "何も起きなかった"})
            return
        for t in list(_alive_others(state, "病院", exclude="")):
            kill_character(state, t, "病院の事件")
        if n >= 2:
            kill_protagonists(state, "病院の事件（暗躍2以上）")

    elif name == "遠隔殺人":
        # 暗躍≥2のキャラから任意の1人を死亡（40:152）
        candidates = [n2 for n2, c in state.characters.items()
                      if c.alive and c.on_board and c.anyaku >= 2]
        if not candidates:
            _pub(state, {"event": "incident_effect", "name": name, "note": "何も起きなかった"})
            return
        chosen = decide(chooser, "incident_choice", [{"target": t} for t in candidates])
        kill_character(state, chosen["target"], "遠隔殺人")

    elif name == "行方不明":
        # 犯人を任意ボードへ移動→犯人のいるボードに暗躍+1（40:153）
        # ★E-2（ユーザー実戦報告＋公式裁定 2026-07-29）：この移動も**犯人の禁止エリアへは
        #   行えない**（KB: 40 事件表の注／50 「行方不明」節）。禁止エリア＝「そのキャラが
        #   移動できないボード」（KB: 00）＝移動の出所（行動カード／事件効果）を問わない。
        #   修正前は全4ボードを候補にしていたため、犯人サラリーマン（禁止＝学校）が学校へ
        #   移動できていた（実戦報告の再現）。
        #   ★このループの禁止解除（医者能力3等）は current_forbidden が反映する。
        #   ★現在地は常に候補に残す＝「任意のボード」には今いるボードも含まれ、結果として
        #     「移動しない」を選べる（現在地は定義上そのキャラが居られるボード）。これにより
        #     候補が空になることは無い（入院患者/A.I.＝4枠中3枠禁止でも現在地は残る）。
        #     暗躍+1 は条文どおり「その後、**犯人のいるボード**に」＝移動しなくても必ず置かれる。
        _forb = current_forbidden(state, culprit.name)
        _dests = [a for a in state.board_anyaku
                  if a == culprit.area or a not in _forb]
        if not _dests:  # 理論上到達しない（犯人は生存かつ盤上＝現在地がある）。安全側＝移動しない。
            _dests = [culprit.area]
        chosen = decide(chooser, "incident_choice",
                        [{"target": a} for a in _dests])
        culprit.area = chosen["target"]
        state.board_anyaku[chosen["target"]] += 1
        _pub(state, {"event": "move", "name": culprit.name, "to": chosen["target"]})
        _pub(state, {"event": "anyaku", "target": chosen["target"], "delta": 1})

    elif name == "流布":
        # 任意1人から友好-2、別の任意1人に友好+2（40:154。カウンターは0未満にならない＝00）
        alive = _alive_all(state)
        first = decide(chooser, "incident_choice", [{"target": t} for t in alive])
        c1 = state.characters[first["target"]]
        c1.goodwill = max(0, c1.goodwill - 2)
        _pub(state, {"event": "goodwill", "target": first["target"], "delta": -2})
        rest = [t for t in alive if t != first["target"]]
        if rest:
            second = decide(chooser, "incident_choice", [{"target": t} for t in rest])
            state.characters[second["target"]].goodwill += 2
            _pub(state, {"event": "goodwill", "target": second["target"], "delta": 2})

    else:
        raise ValueError(f"未実装の事件: {name}")  # validate_script が弾くため到達しない


# ---------------------------------------------------------------------------
# ターン終了フェイズ：【強制】→【任意】（00:176-178）
# ---------------------------------------------------------------------------

def _update_virus_serial(state: GameState) -> None:
    """妄想拡大ウイルス（BTX）：元パーソンは不安3以上でシリアルキラー化、1以下で解除
    （不安2は直前状態を維持＝ヒステリシス。50:81 / 60 C-3）。"""
    if "妄想拡大ウイルス" not in state.script.rule_xs:
        return
    for c in state.characters.values():
        if state.script.role_of(c.name) != "パーソン":
            continue
        if c.unrest >= 3:
            c.virus_serial = True
        elif c.unrest <= 1:
            c.virus_serial = False


def _is_serial(state: GameState, c) -> bool:
    return c.role == "シリアルキラー" or c.virus_serial


def _oomono_territory_for(state, c) -> str | None:
    """そのキャラが大物なら脚本のテリトリー（縄張りボード）を返す（KB: 20 大物特性・現物確認済）。

    「脚本家がこのキャラクターの能力を使う場合、テリトリーにいるものとして能力を使用してもよい」
    ＝**任意**なので、呼び側は 実エリア/テリトリー の両方を選択肢に出す（選ぶのは脚本家）。
    大物以外は None＝従来と完全に同一。テリトリーは脚本作成時指定・全ループ固定・公開情報。
    ★事件は大物の能力ではない＝適用外（60 E-3）。SK【強制】/カルティスト無視も投射する（60 E-3b・B-30b）。
    """
    if getattr(c, "name", None) != "大物":
        return None
    return getattr(state.script, "oomono_territory", None)


def _alive_on_board(state: GameState) -> list:
    return [c for c in state.characters.values() if c.alive and c.on_board]


def resolve_turn_end(state: GameState, decide) -> None:
    _update_virus_serial(state)
    # 【強制】シリアルキラー：同時解決（相打ち対応）。犠牲者算定は engine.turn_end_rules に集約。
    # ★B-30b（E-3b A1）：大物SKはテリトリーの単独キャラを「いるものとして」殺害しうる。
    # ★T14（ユーザー裁定 2026-09-06）：アルバイトの「合計カウンター3以上で死亡」（KB: 30）も
    #   ターン終了の【強制】＝SK の殺害と**同時解決**（00:176）。旧実装はアルバイトの死亡を先に
    #   解決していたので、ウイルスSK化したアルバイトが2人きりの相手を殺せなかった
    #   （seed11 L2D4＝相手が TT〔不死〕だったので結果は同じだったが、死ねる相手なら差が出る）。
    #   ∴ 犠牲者は**アルバイト生存のまま**算定し、アルバイトの死亡と犠牲者の死亡を続けて適用する。
    victims = serial_killer_victims(
        _alive_on_board(state), lambda c: _is_serial(state, c),
        oomono_territory=getattr(state.script, "oomono_territory", None))
    # アルバイトの総カウンター3以上で死亡（KB: 30）。死亡なら次ターン開始にアルバイト？配置。
    check_alubaito_death(state)
    if state.loop_end_triggered:
        return
    for v in sorted(victims):
        kill_character(state, v, "シリアルキラー【強制】")
        if state.loop_end_triggered:
            # キーパーソンが犠牲になった場合、以降の処理は行われない
            return

    # ★生存ペアの公開（SK解決直後の卓上盤面＝公開情報）：2人きりで無事だった＝
    #   このペアに活動中のシリアルキラーは居ない、という強い否定形（SK殺害は【強制】）。
    #   護衛カウンター消費（公開）があった名前を含むペアは除外（死を防いだだけかもしれない）。
    guarded = {e.get("name") for e in state.history
               if (e.get("loop"), e.get("day"), e.get("phase"))
               == (state.loop_no, state.day, "turn_end")
               and e.get("event") == "guard_consumed"}
    by_area: dict[str, list[str]] = {}
    for c in _alive_on_board(state):
        by_area.setdefault(c.area, []).append(c.name)
    pairs = [sorted(ns) for ns in by_area.values()
             if len(ns) == 2 and not (set(ns) & guarded)]
    if pairs:
        _pub(state, {"event": "turn_end_pairs", "pairs": pairs,
                     "unrest": {n: state.characters[n].unrest
                                for p in pairs for n in p}})

    # 【任意】キラー：各能力1回ずつ・自由順（M0要確認②＝解決順の細部は当面この実装）
    # 発動条件は engine.turn_end_rules の述語（相談AI側と単一ソース）。
    is_final_day = state.day >= state.script.days_per_loop
    used: set[str] = set()
    while not state.loop_end_triggered:
        options: list[dict] = [{"action": "pass"}]
        on = _alive_on_board(state)
        for c in on:
            if c.role != "キラー":
                continue
            key_kp = f"キラー殺害:{c.name}"
            if key_kp not in used:  # キーパーソンに暗躍≥2＆同エリア→死亡させてもよい（40:94）
                # ★B-30：大物＝テリトリーに居るものとして能力を使ってもよい（KB: 20 特性）＝
                #   キラー【任意】の「同エリア」は 実エリア∪テリトリー（選ぶのは脚本家＝option化）。
                for o in killer_killable_keypersons(
                        c, on, territory=_oomono_territory_for(state, c)):
                    options.append({"action": key_kp, "target": o.name})
            key_pr = f"キラー主人公:{c.name}"
            if key_pr not in used and killer_can_kill_protagonist(c):
                # 自身に暗躍≥4→主人公を死亡させてもよい（40:95）
                options.append({"action": key_pr, "target": "主人公"})
        # 【任意】メインラバーズ：不安≥3かつ暗躍≥1→主人公を死亡させてもよい（50:160）
        for c in on:
            if c.role == "メインラバーズ" and mainlover_can_kill_protagonist(c):
                key = f"メインラバーズ主人公:{c.name}"
                if key not in used:
                    options.append({"action": key, "target": "主人公"})
        # 【任意】タイムトラベラー：最終日、友好≤2→主人公を敗北させてもよい（50:128）
        for c in on:
            if c.role == "タイムトラベラー" and timetraveler_can_defeat(c, is_final_day):
                key = f"タイムトラベラー敗北:{c.name}"
                if key not in used:
                    options.append({"action": key, "target": "主人公敗北"})
        if len(options) == 1:
            break
        chosen = decide("mastermind", "turn_end_ability", options)
        if chosen["action"] == "pass":
            break
        used.add(chosen["action"])
        if chosen["target"] == "主人公敗北":
            # タイムトラベラー：主人公を「敗北」させる（死亡とは別＝軍人不死では防げない。50:129）
            # ★公開の理由は「ループ終了効果」まで（役職名は明かさない＝00:127 結果のみ。
            #   ただし「死者なしのループ終了効果」からTTの存在は推理できる＝beliefが使う）
            state.defeat = True
            state.loop_end_triggered = True
            # ★その瞬間の友好カウンター（公開）を添付：TTの任意敗北は「友好≤2」が条件
            #   （50:128）＝友好3以上のキャラはTTでない、の消去材料になる。
            _pub(state, {"event": "loop_end", "reason": "主人公の敗北（ループ終了効果）",
                         "goodwill": {n: c.goodwill for n, c in state.characters.items()
                                      if c.alive and c.on_board}})
            _sec(state, {"event": "loop_end", "reason": f"タイムトラベラー任意敗北（{chosen['action']}）"})
        elif chosen["target"] == "主人公":
            kill_protagonists(state, f"ターン終了【任意】（{chosen['action']}）")
        else:
            kill_character(state, chosen["target"], f"ターン終了【任意】（{chosen['action']}）")


# ---------------------------------------------------------------------------
# ループ終了の判定（00:131-142 / 40）
# ---------------------------------------------------------------------------

def evaluate_loop_end(state: GameState) -> None:
    """ループ終了時の敗北判定と勝敗確定。呼び出し後 game_over でなければ次ループへ。"""
    # ループ終了時のボード暗躍＋キャラ暗躍を公開記録（beliefのルール敗北条件推論に使う。
    # どちらも卓上のカウンター＝公開情報。キャラ暗躍は僕と契約の消去法に必要）
    _pub(state, {"event": "loop_board", "board_anyaku": dict(state.board_anyaku),
                 "char_anyaku": {n: c.anyaku for n, c in state.characters.items()
                                  if c.anyaku > 0},
                 "char_goodwill": {n: c.goodwill for n, c in state.characters.items()
                                   if c.goodwill > 0}})
    # 因果の糸用：この終了時に友好が置かれているキャラを記録（次ループ開始で不安+2）
    state.prev_goodwill = {n: c.goodwill for n, c in state.characters.items() if c.goodwill > 0}
    # フレンド：ループ終了時に死亡→役職公開＋敗北（40:130。主人公死亡終了でも起こる＝60:A17）
    for n, c in state.characters.items():
        if c.role == "フレンド" and not c.alive:
            if n not in state.friend_revealed:
                state.friend_revealed.add(n)
                state.revealed_roles[n] = "フレンド"
                _pub(state, {"event": "role_reveal", "name": n, "role": "フレンド"})
            state.defeat = True

    # ルールYの敗北条件（ループ終了時判定＝途中成立ではループは止まらない。00:68 / 40 / 50）
    ry = state.script.rule_y
    if ry == "守るべき場所" and state.board_anyaku["学校"] >= 2:              # FS
        state.defeat = True
        _sec(state, {"event": "defeat", "reason": "守るべき場所（学校の暗躍≥2）"})
    if ry == "封印されしモノ" and state.board_anyaku["神社"] >= 2:            # BTX
        state.defeat = True
        _sec(state, {"event": "defeat", "reason": "封印されしモノ（神社の暗躍≥2）"})
    if ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") and state.rule_y_board_x is not None \
            and state.board_anyaku[state.rule_y_board_x] >= 2:                # FS/BTX ボードX
        state.defeat = True
        _sec(state, {"event": "defeat", "reason": f"{ry}（{state.rule_y_board_x}の暗躍≥2）"})
    if ry == "僕と契約しようよ！":                                            # BTX：キーパーソン暗躍≥2
        for c in state.characters.values():
            if c.role == "キーパーソン" and c.anyaku >= 2:
                state.defeat = True
                _sec(state, {"event": "defeat", "reason": "僕と契約しようよ！（キーパーソン暗躍≥2）"})
    if ry == "未来改変プラン" and state.butterfly_fired:                       # BTX：蝶の羽ばたき発生
        state.defeat = True
        _sec(state, {"event": "defeat", "reason": "未来改変プラン（蝶の羽ばたき発生）"})

    if not state.defeat:
        # 敗北条件未達でループを終えた＝主人公の勝利でゲーム終了（00:139）
        state.game_over = True
        state.winner = "protagonist"
        _pub(state, {"event": "game_over", "winner": "protagonist"})
    elif state.loop_no >= state.script.loops:
        if state.script.set_name == "BTX":
            # BTX：全ループ敗北→最後の戦いへ（主人公が全役職を当てれば勝利。00:144 / 50）
            state.final_battle_pending = True
        else:
            # FSは最後の戦いが無い＝全ループ敗北で直ちに脚本家勝利（40:23）
            state.game_over = True
            state.winner = "mastermind"
            _pub(state, {"event": "game_over", "winner": "mastermind"})
    else:
        _pub(state, {"event": "loop_result", "loop": state.loop_no, "result": "主人公の敗北"})
