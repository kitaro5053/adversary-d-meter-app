"""行動解決フェイズの決定的リゾルバ。

解決順は「移動 → その他」（KB: 00, 10）。
1. 移動を全解決（合成・禁止先で留まる）。
2. その他＝暗躍の解決：
   - 暗躍禁止は重なった暗躍+を無効化（KB: 10）。
   - ただし複数の主人公が暗躍禁止を出すと暗躍禁止自体が自滅（KB: 10）。
   - カルティスト（移動後の現在位置）の同エリア／自ボードの暗躍禁止は無視されうる（KB: 40）。
     脚本家は暗躍最大化を選ぶ前提なので、無視できる暗躍禁止は無視する。
   - 死体に置かれたカードは無効（KB: 00, 10）。

前提検証（premise validation）と感度チェック（sensitivity）も提供する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .board import resolve_move
from .data import ROLE_IGNORES_ANRYAKU_KINSHI
from .models import (
    ANRYAKU_PLUS,
    GOODWILL_BAN,
    GOODWILL_PLUS,
    MASTERMIND_HAND,
    MOVE_BAN,
    MOVE_CARDS,
    ONCE_PER_LOOP,
    PROTAGONIST_HAND,
    UNREST_BAN,
    UNREST_DELTA,
    Board,
    Placement,
)


# ---------- 結果型 ----------

@dataclass
class TargetResult:
    target: str
    target_kind: str
    delta: int  # この対象に最終的に増える暗躍
    anyaku_plus: int  # セットされた暗躍+の素の量（無効化前）
    has_kinshi: bool
    kinshi_active: bool  # 暗躍禁止が実効しているか
    ignored_by_cultist: bool
    invalid_reason: str | None = None  # 死体等で無効化された場合


@dataclass
class CounterResult:
    """1キャラへの不安／友好カウンターの行動解決結果（増減＋最終値）。"""
    target: str
    initial: int         # 行動解決前に載っていたカウンター数
    final: int           # 行動解決後のカウンター数（0未満にはならない）
    delta: int           # 実際に適用された純増減（final - initial）
    plus: int            # 打ち消し前の＋合計
    minus: int           # 打ち消し前の−合計（友好は常に0）
    ban_active: bool     # 不安禁止／友好禁止が実効して打ち消したか
    ban_ignored: bool = False  # 禁止はあったが役職能力で無視された（例: タイムトラベラーの友好禁止無視）
    invalid_reason: str | None = None  # 死体等で無効


@dataclass
class Violation:
    code: str
    message: str
    kb_ref: str


@dataclass
class SensitivityFlag:
    target: str
    current: int
    alternative: int
    condition: str


@dataclass
class Adjudication:
    moves: dict[str, str] = field(default_factory=dict)  # char -> 移動後エリア
    blocked_moves: list[str] = field(default_factory=list)  # 禁止先で留まったキャラ
    move_banned: list[str] = field(default_factory=list)  # 移動禁止で移動を打ち消されたキャラ
    targets: dict[str, TargetResult] = field(default_factory=dict)  # 暗躍（キャラ/ボード）
    unrest: dict[str, CounterResult] = field(default_factory=dict)  # 不安（キャラのみ）
    goodwill: dict[str, CounterResult] = field(default_factory=dict)  # 友好（キャラのみ）
    board_bluffs: list = field(default_factory=list)  # ボードに置いたが解決されない非暗躍カード (owner,card,board)
    n_protagonist_kinshi: int = 0

    def total_anyaku_gain(self) -> int:
        return sum(t.delta for t in self.targets.values())


# ---------- 前提検証 ----------

def validate_board(board: Board) -> list[Violation]:
    """前提自体がルール違反でないかを検査（指摘優先・自動補正しない）。"""
    v: list[Violation] = []

    # 同一プレイヤーが1対象に2枚以上（KB: 00 フェイズ2-3）
    seen: dict[tuple[str, str], int] = {}
    for p in board.placements:
        key = (p.owner, p.target)
        seen[key] = seen.get(key, 0) + 1
    for (owner, target), n in seen.items():
        if n >= 2:
            v.append(Violation(
                "DUP_TARGET",
                f"{owner} が {target} に {n}枚セット：同一プレイヤーは1対象に2枚以上不可。",
                "00 / ループの進行（フェイズ2-3）",
            ))

    # 複数の主人公が同一対象に重ねてセット（KB: 00 フェイズ3）。
    # 「他の主人公が既にセットした対象には重ねられない」＝1対象に主人公は高々1人。
    # （脚本家がセットした対象への重ねは可。よって最大＝脚本家1＋主人公1。）
    protag_owners: dict[str, set[str]] = {}
    for p in board.placements:
        if p.is_protagonist:
            protag_owners.setdefault(p.target, set()).add(p.owner)
    for target, owners in protag_owners.items():
        if len(owners) >= 2:
            v.append(Violation(
                "MULTI_PROTAGONIST_TARGET",
                f"複数の主人公（{'/'.join(sorted(owners))}）が {target} に重ねてセット："
                f"他の主人公が既にセットした対象には重ねられない。",
                "00 / ループの進行（フェイズ3）",
            ))

    # 1ループに1回のカードを同一プレイヤーが複数（KB: 10）。暗躍+2の二重置き等。
    once_count: dict[tuple[str, str], int] = {}
    for p in board.placements:
        side = "mastermind" if p.owner == "mastermind" else "protagonist"
        if p.card in ONCE_PER_LOOP[side]:
            k = (p.owner, p.card)
            once_count[k] = once_count.get(k, 0) + 1
    for (owner, card), n in once_count.items():
        if n >= 2:
            v.append(Violation(
                "ONCE_PER_LOOP",
                f"{owner} が「{card}」を{n}枚使用：このカードは1ループに1回（手札1枚）。",
                "10 / 1ループに1回まで",
            ))

    # 手札の同名カード枚数を超えるセット（KB: 00 手札の分配 / 10。現物確認済 2026-07-03）。
    # 脚本家で2枚あるのは不安+1のみ＝暗躍+1等の同一ターン2枚は物理的に不可能。
    # 1/loopカードの重複は上のONCE_PER_LOOPで指摘済みのため除外（二重指摘を避ける）。
    card_count: dict[tuple[str, str], int] = {}
    for p in board.placements:
        side = "mastermind" if p.owner == "mastermind" else "protagonist"
        if p.card in ONCE_PER_LOOP[side]:
            continue
        k = (p.owner, p.card)
        card_count[k] = card_count.get(k, 0) + 1
    for (owner, card), n in card_count.items():
        hand = MASTERMIND_HAND if owner == "mastermind" else PROTAGONIST_HAND
        limit = hand.count(card)
        if limit and n > limit:
            v.append(Violation(
                "HAND_LIMIT",
                f"{owner} が「{card}」を{n}枚セット：このカードは手札に{limit}枚しか無い。",
                "00 / 手札の分配・10",
            ))

    for p in board.placements:
        # 主人公が暗躍+を置く（KB: 00 カウンター表 / 10）
        if p.is_protagonist and p.card in ANRYAKU_PLUS:
            v.append(Violation(
                "PROTAGONIST_ANRYAKU",
                f"主人公が「{p.card}」をセット：暗躍カウンターを置けるのは脚本家のみ。",
                "00 / カウンター表・10 / 主人公手札",
            ))
        # 幻想は行動カード被セット不可（KB: 30 特性）。直接対象にする配置は違反。
        # （幻想への効果は「同エリアのボードに置いたカード」経由で受ける＝下の resolve で読み替える。）
        if p.target_kind == "character" and p.target == "幻想":
            v.append(Violation(
                "SET_ON_GENSO",
                f"幻想に「{p.card}」：幻想は行動カードを被セットできない"
                "（効果はいるエリアのボード経由で受ける）。",
                "30 / 幻想の特性",
            ))
        # ★ ボードへ暗躍以外を置くのは「ブラフ（解決されない）」＝前提違反ではない（KB: 10）。
        #   幻想がいるエリアのボードだけは幻想が効果を受ける（resolve側で処理）。ここでは弾かない。
        # 死体にセット（KB: 00 記法 / 10）
        if p.target_kind == "character":
            c = board.char(p.target)
            if c is not None and c.is_corpse:
                v.append(Violation(
                    "SET_ON_CORPSE",
                    f"死体({p.target})に「{p.card}」：死体には行動カードをセットできない。",
                    "00 / キャラクターと死体・10",
                ))
    return v


# ---------- 解決 ----------

def _cultist_covers(board: Board, target: str, target_kind: str) -> bool:
    """移動後のカルティストが、その対象の暗躍禁止を無視できる位置にいるか。

    ★大物のテリトリー投射（B-30b・KB: 20 特性 / 60 E-3b A2・ユーザー現物確認 2026-07-17）：
      大物＝カルティストは「テリトリーに置かれた暗躍禁止と、現在地に置かれた暗躍禁止の**両方**」を
      無視できる（「テリトリーにいるものとして能力を使用してもよい」＝両エリアを覆う）。
      board.oomono_territory が None（大物不在/未指定）なら従来と完全に同一。
    """
    if target_kind == "board":
        area = target
    else:
        c = board.char(target)
        if c is None:
            return False
        area = c.area
    terr = getattr(board, "oomono_territory", None)
    for ch in board.characters.values():
        if not (ch.alive and ch.role in ROLE_IGNORES_ANRYAKU_KINSHI):
            continue
        if ch.area == area:
            return True
        # 大物＝カルティストはテリトリーの暗躍禁止も無視できる（現在地と両方＝E-3b A2）
        if ch.name == "大物" and terr is not None and area == terr:
            return True
    return False


def resolve_action_phase(board: Board, cultist_ignore=None) -> Adjudication:
    """行動解決を裁定する。

    cultist_ignore＝カルティストの「暗躍禁止を無視してもよい」任意発動の判定コールバック
    `(target, target_kind) -> bool`（True=無視して暗躍を通す）。None（既定）なら従来どおり
    常に無視する（脚本家は暗躍最大化＝暗躍が通る）。相談エンジン/テスト/AI対局は None で不変。
    人間=脚本家プレイだけコールバックを渡し、無視するかを都度選ばせる（テスター要望 2026-07-12・
    KB: 40 カルティスト特性は任意発動）。★2枚以上の暗躍禁止（自滅）や暗躍+が無い対象では
    無視の有無で結果が変わらない＝コールバックは呼ばない。"""
    adj = Adjudication()

    # 幻想（生存）は「同エリアのボードのカード効果を受ける」特性（KB: 30 一覧表・幻想の特性）。
    # 幻想のいるエリアのボードに置かれたカードは幻想が受ける。
    #
    # ★実装が2経路に分かれる理由（KB: 10:67-71 が根拠・E-1で明文化 2026-07-27）：
    #   「**ボードには暗躍カウンターのみ置かれる（友好・不安は不可）**。ボード自体は移動しない。
    #     よってボードにセットして実際に解決されるのは 主人公＝暗躍禁止のみ／
    #     脚本家＝暗躍+1・暗躍+2のみ。それ以外はブラフ」
    #   ＝**不安/友好/移動はボード自身が元々何も受けない**ので、幻想への「対象の置き換え」と
    #     「効果の複製」は**結果が同一**＝従来の読み替え（_eff_at）のままで正しい（変更不要）。
    #   ＝**置き換えと加算で差が出るのは暗躍系だけ**。
    #
    # ★E-1（2026-07-27・ユーザー裁定＝原本保持者による確定）：暗躍系は「**両方に乗る**」。
    #   裁定文言＝「ボードに暗躍載せたら不安や友好と同じように幻想にも暗躍がのります」
    #            ＋「両方に乗るが正しいです」。
    #   ＝ボードの暗躍カウンターは従来どおり増え、**加えて幻想にも同量が乗る**（効果の複製）。
    #   旧実装は「暗躍系はボード本来のカードだから読み替えない」として幻想側を落としていたが、
    #   これは**ユーザー裁定を経ていない実装側の仮定**で、KB本体（30 幻想の特性）に
    #   除外の記述は無い＝誤りだった。
    #   ★暗躍禁止も同様に両方へ効く：板の暗躍禁止はボードの暗躍を止め、**同時に幻想への
    #   複製分も止める**（幻想は行動カード被セット不可＝主人公は幻想へ直接置けないので、
    #   これが唯一の防御手段。届かないと一方通行のポンプになる）。
    genso = next((c for c in board.characters.values()
                  if c.name == "幻想" and c.alive), None)
    genso_area = genso.area if genso else None

    # ★A-33（2026-07-17）：読み替えは**解決順に従って2段階**で行う。
    #   旧実装は移動解決の**前**に初期位置で一括読み替えしていた＝移動カードで幻想が別エリアへ
    #   移っても、行き先ボードのカードを受けず、出発地ボードのカードを受け続けていた（バグ）。
    #   行動解決は「移動 → その他」（KB: 00）なので：
    #     (1) 移動カード/移動禁止 ＝ **初期位置**のボードが基準（移動はここで決まる）
    #     (2) 非移動カード（不安/友好/不安禁止/友好禁止）＝ **移動解決後の幻想の位置**で再評価
    #         ＝移動先ボードのカードを受ける／出発地ボードのカードはもう受けない（対称）
    #   暗躍系（E-1の複製）も (2) と同じ**移動解決後**の位置を基準にする（下の _genso_anyaku_dup）。
    _move_like = MOVE_CARDS | {MOVE_BAN}
    _post_move_redirectable = {UNREST_BAN, GOODWILL_BAN} | set(UNREST_DELTA) | set(GOODWILL_PLUS)

    def _eff_at(p: Placement, area: str | None, cards: set) -> tuple[str, str]:
        """幻想が area に居る時、そのエリアのボードに置かれた cards は幻想が受ける（読み替え）。"""
        if (area and p.target_kind == "board" and p.target == area and p.card in cards):
            return ("幻想", "character")
        return (p.target, p.target_kind)

    # (1) 移動解決用＝初期位置基準（移動カード/移動禁止のみ読み替える）。
    eff_move = [Placement(p.owner, p.card, *_eff_at(p, genso_area, _move_like))
                for p in board.placements]

    # --- 1) 移動（先に全解決） ---
    moves_by_char: dict[str, list[str]] = {}
    for p in eff_move:
        if p.target_kind == "character" and p.card in MOVE_CARDS:
            moves_by_char.setdefault(p.target, []).append(p.card)
    # 移動禁止（主人公）：重なった移動カードを打ち消す。自滅ルールは無い（KB: 10）。
    move_banned_chars = {
        p.target for p in eff_move
        if p.target_kind == "character" and p.card == MOVE_BAN
    }
    for name, char in board.characters.items():
        if not char.alive:
            continue
        moves = moves_by_char.get(name, [])
        # 移動禁止が重なっていれば移動カードは無効化＝その場に留まる。
        # （打ち消す移動が無い＝ブラフの移動禁止は結果に影響しないので記録しない。）
        if name in move_banned_chars and moves:
            adj.move_banned.append(name)
            moves = []
        new_area, blocked = resolve_move(char, moves)
        char.area = new_area
        adj.moves[name] = new_area
        if blocked:
            adj.blocked_moves.append(name)

    # (2) ★A-33：移動解決**後**の幻想の位置で、非移動カード（不安/友好/各禁止）を再読み替えする。
    #   char.area は上のループで更新済み＝幻想が移動していれば行き先ボードのカードを受ける（到着側）／
    #   出発地ボードのカードはもう受けない（離脱側）＝両方向を1つの位置参照で実現。暗躍系は下で
    #   別途集計するので、eff は「暗躍以外」の解決に使う。移動カード/移動禁止は eff_move で解決済み
    #   ＝ここでは初期位置基準の読み替え結果をそのまま引き継ぐ（二重読み替えしない）。
    genso_area_after = genso.area if (genso and genso.alive) else None
    eff = []
    for p in board.placements:
        if p.card in _move_like:
            eff.append(Placement(p.owner, p.card, *_eff_at(p, genso_area, _move_like)))
        else:
            eff.append(Placement(p.owner, p.card,
                                 *_eff_at(p, genso_area_after, _post_move_redirectable)))

    # ボードに置いた非暗躍カードで、幻想へ読み替えられなかったもの＝ブラフ（解決されない）。
    #   ★読み替えは移動後の位置で確定済み＝ここで初めて board_bluffs が正しく出る（A-33）。
    adj.board_bluffs = [
        (p.owner, p.card, p.target)
        for p, ep in zip(board.placements, eff)
        if p.target_kind == "board" and ep.target_kind == "board"
        and p.card not in ({"暗躍禁止"} | set(ANRYAKU_PLUS))
    ]

    # --- 2) その他＝暗躍の解決 ---
    # 複数主人公の暗躍禁止 → 自滅（KB: 10）。
    # 主人公の暗躍禁止は各自1枚なので、盤面上の暗躍禁止の「枚数」＝出した主人公の人数。
    # owner名（p1/p2）は翻訳の振り方で揺れるため、枚数で数える方が頑健。
    n_kinshi = sum(
        1 for p in eff
        if p.is_protagonist and p.card == "暗躍禁止"
    )
    adj.n_protagonist_kinshi = n_kinshi
    self_negate = adj.n_protagonist_kinshi >= 2

    # ★E-1（2026-07-27・ユーザー裁定「両方に乗る」）：幻想が居るエリアのボードに置かれた
    #   暗躍+/暗躍禁止は、**ボードに従来どおり効いた上で、幻想にも同じ効果が複製される**。
    #   位置の基準は (2) と同じ**移動解決後**（genso_area_after）。
    #   ★対象の書き換え（_eff_at）ではなく**効果の複製**なので、暗躍系だけこの別経路を通す。
    #   ★n_kinshi（複数の暗躍禁止＝自滅）は**複製前の実カード枚数**で数える（上で算出済み）＝
    #     複製を数えると1枚の暗躍禁止が2枚に見えて誤って自滅する。
    _genso_anyaku_dup = [
        Placement(p.owner, p.card, "幻想", "character")
        for p in board.placements
        if (genso_area_after and p.target_kind == "board"
            and p.target == genso_area_after
            and (p.card in ANRYAKU_PLUS or p.card == "暗躍禁止"))
    ]

    # 対象ごとに暗躍+と暗躍禁止を集計
    targets: dict[tuple[str, str], dict] = {}
    for p in eff + _genso_anyaku_dup:
        if p.card in ANRYAKU_PLUS or p.card == "暗躍禁止":
            key = (p.target, p.target_kind)
            t = targets.setdefault(key, {"plus": 0, "kinshi": False, "invalid": None})
            # 死体上のカードは無効
            if p.target_kind == "character":
                c = board.char(p.target)
                if c is not None and c.is_corpse:
                    t["invalid"] = "死体にはセット不可（無効）"
                    continue
            if p.card in ANRYAKU_PLUS:
                t["plus"] += ANRYAKU_PLUS[p.card]
            elif p.card == "暗躍禁止":
                t["kinshi"] = True

    for (target, kind), t in targets.items():
        invalid = t["invalid"]
        plus = 0 if invalid else t["plus"]
        has_kinshi = t["kinshi"] and not invalid
        cultist_here = _cultist_covers(board, target, kind) if has_kinshi else False
        # ★カルティストの暗躍禁止無視は任意発動（KB: 40）。無視の有無で暗躍が通るかが
        #   変わる状況（暗躍禁止1枚が実効＝not self_negate ＋ 暗躍+が有る）だけコールバックで
        #   脚本家に選ばせる。それ以外・コールバック無しは従来どおり常に無視。
        if cultist_here and (not self_negate) and plus > 0 and cultist_ignore is not None:
            ignored = bool(cultist_ignore(target, kind))
        else:
            ignored = cultist_here
        kinshi_active = has_kinshi and (not self_negate) and (not ignored)
        delta = 0 if (kinshi_active) else plus
        adj.targets[target] = TargetResult(
            target=target, target_kind=kind, delta=delta, anyaku_plus=plus,
            has_kinshi=has_kinshi, kinshi_active=kinshi_active,
            ignored_by_cultist=ignored, invalid_reason=invalid,
        )

    # --- 3) その他＝不安／友好の集計（キャラのみ。禁止は単純打ち消し・自滅無し） ---
    adj.unrest = _resolve_counters(eff, board, UNREST_DELTA, UNREST_BAN, "unrest")
    # 友好禁止のみ、タイムトラベラー【強制】が無視する（KB: 50。不安禁止は対象外）。
    adj.goodwill = _resolve_counters(eff, board, GOODWILL_PLUS, GOODWILL_BAN, "goodwill",
                                     ban_immune_role="タイムトラベラー")
    return adj


def _resolve_counters(
    placements: list, board: Board, delta_cards: dict[str, int],
    ban_card: str, initial_attr: str, ban_immune_role: str | None = None,
) -> dict[str, CounterResult]:
    """不安／友好カウンターをキャラ単位で集計する（KB: 10）。

    - delta_cards: 増減カード→値（不安 {不安+1:+1, 不安-1:-1} / 友好 {友好+1:1, 友好+2:2}）。
    - ban_card: 対応する禁止カード。重なると＋も−も全打ち消し（0）。暗躍禁止と違い
      複数出しても自滅しない（＝1枚でも実効）。KB: 10。
    - initial_attr: 初期カウンター数を持つ Character の属性名（"unrest"/"goodwill"）。
    - 死体上のカードは無効（→00, 10）。ボード対象は KB上そもそも不正なので character のみ扱う。
    - カウンターは0未満にならない：不安+1が不安-1より先に解決するため
      final = max(0, 初期 + ＋合計 − −合計)。無い不安を−1しても空振り（KB: 10, カウンター）。
    """
    agg: dict[str, dict] = {}
    for p in placements:
        if p.target_kind != "character":
            continue
        if p.card not in delta_cards and p.card != ban_card:
            continue
        a = agg.setdefault(p.target, {"plus": 0, "minus": 0, "ban": False, "invalid": None})
        c = board.char(p.target)
        if c is not None and c.is_corpse:
            a["invalid"] = "死体にはセット不可（無効）"
            continue
        if p.card == ban_card:
            a["ban"] = True
        else:
            d = delta_cards[p.card]
            if d >= 0:
                a["plus"] += d
            else:
                a["minus"] += -d

    results: dict[str, CounterResult] = {}
    for name, a in agg.items():
        c = board.char(name)
        initial = getattr(c, initial_attr, 0) if c is not None else 0
        invalid = a["invalid"]
        # 受けるキャラの役職が禁止を無視する場合（タイムトラベラー×友好禁止【強制】）。
        ban_ignored = bool(
            a["ban"] and not invalid and ban_immune_role
            and c is not None and c.role == ban_immune_role
        )
        ban_active = a["ban"] and not invalid and not ban_ignored
        plus = 0 if invalid else a["plus"]
        minus = 0 if invalid else a["minus"]
        if invalid or ban_active:
            final = initial            # 無効／禁止で打ち消し → 変化なし
        else:
            final = max(0, initial + plus - minus)  # 0未満にはならない
        results[name] = CounterResult(
            target=name, initial=initial, final=final, delta=final - initial,
            plus=plus, minus=minus, ban_active=ban_active, ban_ignored=ban_ignored,
            invalid_reason=invalid,
        )
    return results


# ---------- 感度チェック ----------

def sensitivity_check(board: Board, adj: Adjudication, target: str) -> list[SensitivityFlag]:
    """その対象の結論が inert補完（＝未指定カードは無し）に依存しているか検出。

    現状モデルが扱う load-bearing な軸：
    「他に主人公の暗躍禁止がもう1枚あるか」。暗躍禁止が1枚のとき、
    もう1枚増えると自滅して結論が反転する。
    """
    flags: list[SensitivityFlag] = []
    tr = adj.targets.get(target)
    if tr is None:
        return flags

    # 暗躍禁止が1枚だけ、かつそれが実効して暗躍+を抑えている → 反転しうる
    if adj.n_protagonist_kinshi == 1 and tr.kinshi_active and tr.anyaku_plus > 0:
        flags.append(SensitivityFlag(
            target=target,
            current=tr.delta,
            alternative=tr.anyaku_plus,
            condition="他に主人公の暗躍禁止がもう1枚でもあれば、自滅で暗躍禁止が無効化され反転",
        ))
    return flags
