"""合法手（セット可能なカード×対象、脚本家能力）の列挙。

エージェントはここが返す options からしか選べない＝ルールをAI側にハードコードしない。
生成した手は engine.validate_board を必ず通る（通らなければシミュレータのバグ＝flowでRuntimeError）。

M1の簡略化（正直に非対応と宣言するもの）:
- ブラフ置きは生成しない。ボードにセットして実際に解決されるのは
  脚本家＝暗躍+1/+2、主人公＝暗躍禁止のみ（KB: 10:70）で、実ゲームでは解決されない
  カードを囮としてボードに置くことも可能だが、random/heuristic bot には不要。
  LLMエージェント導入時（M4）に再検討する。
"""

from __future__ import annotations

from engine.board import AREAS
from engine.data import role_has_friendship_ignore
from engine.models import ANRYAKU_PLUS

from .state import GameState

# ボードを対象にセットして解決されるカード（KB: 10:70）
MASTERMIND_BOARD_CARDS = frozenset(ANRYAKU_PLUS)  # 暗躍+1／暗躍+2
PROTAGONIST_BOARD_CARDS = frozenset({"暗躍禁止"})


def remaining_hand(state: GameState, owner: str) -> list[str]:
    """手札から「今ターン既にセットした分」を除いた残り（multiset）。"""
    hand = state.hand_of(owner)
    for p in state.turn_placements:
        if p["owner"] == owner:
            hand.remove(p["card"])
    return hand


def _alive_char_targets(state: GameState) -> list[str]:
    """カードをセットできるキャラ＝生存かつ盤上（死体・未登場は不可。00 死体）。
    幻想は行動カードを直接セットできない（代わりに幻想のボードへ置く。KB: 30 幻想特性）。"""
    return [n for n, c in state.characters.items()
            if c.alive and c.on_board and n != "幻想"]


def _own_targets(state: GameState, owner: str) -> set[str]:
    return {p["target"] for p in state.turn_placements if p["owner"] == owner}


def _other_protagonist_targets(state: GameState, seat: str) -> set[str]:
    """他の主人公が既にセットした対象（重ね置き不可＝00 フェイズ3）。"""
    return {
        p["target"] for p in state.turn_placements
        if p["owner"] != "mastermind" and p["owner"] != seat
    }


def set_card_options(state: GameState, owner: str,
                     allow_bluff: bool = False) -> list[dict]:
    """行動カード1枚のセット候補 [{card,target,target_kind}]。

    allow_bluff＝ボードに「実際には解決されないカード（ブラフ）」も置ける候補として出す
    （KB: 10:70＝脚本家は暗躍以外もボードに置けるが解決されない）。engine は既にブラフを
    board_bluffs として無害に処理する（前提違反ではない）。★既定 False＝AI/bot は従来どおり
    （ブラフはbot に不要・決定空間を膨らませない＝ベンチ/belief 不変）。人間プレイ（脚本家UI）
    が True で呼び、人間が囮を置けるようにする（テスター要望 2026-07-12）。"""
    cards = sorted(set(remaining_hand(state, owner)))
    banned_targets = _own_targets(state, owner)  # 同一プレイヤー1対象1枚（DUP_TARGET）
    if owner != "mastermind":
        banned_targets |= _other_protagonist_targets(state, owner)
    board_cards = MASTERMIND_BOARD_CARDS if owner == "mastermind" else PROTAGONIST_BOARD_CARDS

    # 幻想がいるボードには、非暗躍カード（移動/不安/友好/各禁止）も置ける
    #   ＝幻想が同エリアのボードのカード効果を受けるため（KB: 30 幻想特性）。
    gensou_boards = {c.area for n, c in state.characters.items()
                     if n == "幻想" and c.alive and c.on_board}

    options: list[dict] = []
    char_targets = [t for t in _alive_char_targets(state) if t not in banned_targets]
    board_targets = [a for a in AREAS if a not in banned_targets]
    for card in cards:
        for t in char_targets:
            options.append({"card": card, "target": t, "target_kind": "character"})
        for a in board_targets:
            if card in board_cards or a in gensou_boards or allow_bluff:
                options.append({"card": card, "target": a, "target_kind": "board"})
    return options


def goshinboku_forced_options(state: GameState, used: set[str]) -> list[dict]:
    """ご神木の特性で脚本家が**強制で**使用する手の一覧（B-233・現物カード 2026-08-16）。

    カード＝「このキャラクターが友好無視を持つ場合、脚本家能力フェイズに脚本家もこの特性を
    用いる（強制）」。★強制なのは「使用すること」で、どのカウンターを誰へ移すかは脚本家が選ぶ
    ＝pass を含めない選択肢集合として返す。実行できる組み合わせが無ければ空（＝不発）。
    """
    if not role_has_friendship_ignore(state.script.role_of("ご神木")):
        return []
    if "ご神木:move" in used:
        return []
    from .effects import goshinboku_move_options
    return [{"action": "ご神木:move", "kind": "goshinboku", "counter": counter, "target": t}
            for counter, t in goshinboku_move_options(state)]


def goshinboku_idle_observed(state: GameState, used: set[str]) -> bool:
    """★B-234：脚本家能力フェイズの末尾で「**不発生**」が観測できるか（公開情報のみで判定）。

    True ＝「ご神木の上にカウンターがあり、同エリアに生存する他キャラが居るのに、この
    脚本家能力フェイズで ご神木の特性が使われなかった」。B-233 の是正（強制段）により、
    **ご神木の役職が友好無視を持つならこの状態にはなり得ない**（`goshinboku_forced_options`
    が非空 → `sim/flow.py` の強制段が必ず1回使う → `used` に載る）。
    ∴ True は「ご神木の役職 ∉ 友好無視系」と**同値**＝主人公側の演繹の材料になる。

    ★材料は全て公開情報（生死・エリア・カウンター＝盤上の事実／`sim/views.py` の
    `_public_char` が主人公ビューへそのまま載せている量）。役職は一切参照しない。
    ★呼び出し位置は `flow.py` の強制段の**直後**＝`forced` を評価したのと同じ盤面
    （強制段が発火した局面では `"ご神木:move" in used` で False になる）。
    """
    if "ご神木:move" in used:
        return False
    from .effects import goshinboku_move_options
    return bool(goshinboku_move_options(state))


def mastermind_ability_options(state: GameState, used: set[str]) -> list[dict]:
    """脚本家能力フェイズの選択肢。各能力は1ターン各1回・自由順（00:109）＋パス。

    根拠テーブルは engine/data.py（ROLE_MM_ANRYAKU/ROLE_MM_UNREST/MM_ANRYAKU_RULES）。
    M1ではFSの3ソース（クロマク・ミスリーダー・不穏な噂）のみ。医者の友好能力使用はM2。
    """
    options: list[dict] = []
    for name, c in state.characters.items():
        if not (c.alive and c.on_board):
            continue
        # ★B-30（大物のテリトリー投射・KB: 20 特性・現物確認済）：「脚本家がこのキャラクターの
        #   能力を使う場合、テリトリーにいるものとして能力を使用してもよい」＝**任意**＝
        #   実エリアとテリトリーの**両方**を「居るものとして扱えるエリア」に含め、どちらから
        #   使うかは脚本家の選択＝option として並べる（選択肢化）。大物以外は実エリアのみ＝不変。
        #   ★事件は大物の能力ではない＝適用外（60 E-3）。SK【強制】/カルティストの暗躍禁止無視への
        #     適用は要確認（20:203 が指す 60:A22 が KB に不在＝ギャップ）＝確認まで未実装。
        act_areas = [c.area]
        if name == "大物":
            _terr = getattr(state.script, "oomono_territory", None)
            if _terr and _terr != c.area:
                act_areas.append(_terr)
        # 能力の対象になりうるキャラ＝「居るものとして扱えるエリア」の住人（大物のみ拡張）。
        reachable = [
            n for n, o in state.characters.items()
            if o.alive and o.on_board and o.area in act_areas
        ]
        if c.role == "クロマク":
            key = f"クロマク:{name}"
            if key not in used:
                # 同エリアのキャラ1人 or 自ボードに暗躍+1（40:86）
                for t in reachable:
                    options.append({"action": key, "kind": "anyaku",
                                    "target": t, "target_kind": "character"})
                for _a in act_areas:      # 「自ボード」＝大物はテリトリーも選べる
                    options.append({"action": key, "kind": "anyaku",
                                    "target": _a, "target_kind": "board"})
        # ファクター（BTX）：学校に暗躍2以上でミスリーダーの追加能力を得る（不安+1）。50:174 / 60 A10。
        #   （都市に暗躍2以上でキーパーソン能力を得るが、不安には無関係＝ここでは扱わない。）
        gains_misleader = (c.role == "ファクター"
                           and state.board_anyaku.get("学校", 0) >= 2)
        if c.role == "ミスリーダー" or gains_misleader:
            key = f"ミスリーダー:{name}"
            if key not in used:
                # 同エリアのキャラ1人に不安+1（自身可＝40:110）。★B-30：大物はテリトリーの住人も可。
                for t in reachable:
                    options.append({"action": key, "kind": "unrest",
                                    "target": t, "target_kind": "character"})
    if "不穏な噂" in state.script.rule_xs and not state.rumor_used and "不穏な噂" not in used:
        # 任意のボード1つに暗躍+1（1/loop。40:61。暗躍禁止では止まらない＝40:62）
        for a in AREAS:
            options.append({"action": "不穏な噂", "kind": "anyaku",
                            "target": a, "target_kind": "board"})
    # ご神木の特性：友好無視を持つ場合、脚本家能力フェイズに**強制で**使用（KB: 30・現物カード
    # 確認 2026-08-16。上のカウンター1つを同エリアの他キャラへ）。ご神木の初期＝神社固定。
    # 1ターン1回（keyで抑止）。★強制の担保は `flow.py` の脚本家能力フェイズ末尾（pass 後の
    # 取りこぼしを `goshinboku_forced_options` で拾う）＝ここでは自由順の選択肢として出すだけ。
    options.extend(goshinboku_forced_options(state, used))
    # 医者の友好能力（現物確認済＝★友好無視を持ち、かつ友好2以上のときだけ脚本家能力フェイズに
    # 使用可。KB: 20 医者能力2 / 60 B-4）。同エリアの他キャラから不安1除去 or 付与。拒否は発生しない。
    doc = state.characters.get("医者")
    if (doc is not None and doc.alive and doc.on_board and doc.goodwill >= 2
            and role_has_friendship_ignore(state.script.role_of("医者"))
            and "医者:医者" not in used):
        doc_same = [n for n, o in state.characters.items()
                    if n != "医者" and o.alive and o.on_board and o.area == doc.area]
        for t in doc_same:
            options.append({"action": "医者:医者", "kind": "unrest",
                            "target": t, "target_kind": "character"})
            options.append({"action": "医者:医者", "kind": "unrest_minus",
                            "target": t, "target_kind": "character"})
    options.append({"action": "pass"})
    return options


def goodwill_ability_options(state: GameState, used_this_turn: set) -> list[dict]:
    """主人公能力フェイズ：使用可能な友好能力の選択肢（実装済み能力のみ）＋パス。

    使用可否＝生存＋盤上＋友好≥必要ハート数（KB: 20/00）。効果実装は sim/abilities。
    1/L能力は state.used_goodwill、同一ターンの再使用は used_this_turn で抑止。
    """
    from engine.data import goodwill_abilities_of
    from .abilities import ability_targets, is_implemented

    options: list[dict] = []
    for name, c in state.characters.items():
        if not (c.alive and c.on_board):
            continue
        for ab in goodwill_abilities_of(name) or []:
            aname = ab["name"]
            if not is_implemented(name, aname) or c.goodwill < ab["hearts"]:
                continue
            key = (name, aname)
            if key in used_this_turn:
                continue
            if ab["once_per_loop"] and key in state.used_goodwill:
                continue
            for t in ability_targets(state, name, aname):
                options.append({"character": name, "ability": aname, "target": t})
    # ご神木の特性（主人公能力フェイズ・任意・ハート不要）：上のカウンター1つを同エリアの他キャラへ。
    # 1ターン1回（("ご神木","trait") を used_this_turn で抑止）。KB: 30。
    if ("ご神木", "trait") not in used_this_turn:
        from .effects import goshinboku_move_options
        for counter, t in goshinboku_move_options(state):
            options.append({"goshinboku": counter, "target": t})
    options.append({"action": "pass"})
    return options
