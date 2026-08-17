# -*- coding: utf-8 -*-
"""脅威ベースの防御プランナー（2026-07-09・発案=ユーザー）。

`docs/負け筋防御ツリー.md` の攻撃-防御ツリーを**実行可能な探索**にしたもの。

考え方（主人公側の詰将棋）:
  1. 現局面の公開情報＋belief（役職/犯人の推定）から、いま火が点きうる負け筋
     （脅威 Threat）を列挙する。各脅威は AND 条件の集まり＝全部満たすと発火。
  2. 1つの AND 条件を折れば、その脅威は防げる（OR の防御手）。
  3. 手札（options）から、各条件を折る**最安**の防御を引く。難易度＝コスト。
  4. 全脅威を1つずつ折る手の**最小コスト集合**（重み付き hitting set）を貪欲に組む。
  5. どの条件も折れない脅威＝**防御不能**（クロマク/不穏な噂/黒猫の暗躍供給等・
     ツリーの〔不可〕）＝「防御でなくレースで勝つ」対象として分離して報告する。

★sim/mate.py（厳密・全知・遅い）との違い：こちらは **高速・belief（推定）ベース・
  説明可能**＝実対局でAIが使える／人間=脚本家モードの内省パネルに『AIの防御計画』を
  出せる。両者は相補的（mate＝地面の真値、planner＝現場で回せる方針）。

現段階＝**助言専用（advisory）**。AIの決定ロジックには未配線（いきなり置換すると
退行リスク大）。まず本モジュールで概念を検証し、段階的に heuristic_protagonist へ
配線して4面ベンチで測る（＝measure-before-rewrite の作法）。

Streamlit非依存・engine非依存（view dict と belief marginals だけで動く純関数群）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.card_effect import NoopCtx, noop_reason
from engine.data import (SHOUJO, UNREFUSABLE_ABILITY_CHARS, ability_kind,
                         goodwill_abilities_of, initial_area_of, is_student,
                         role_has_friendship_ignore, unrest_threshold_of)

# 盤面ジオメトリ（上段=病院/神社・下段=都市/学校）
_AREAS = ("病院", "神社", "都市", "学校")
# 主人公の移動カード＝**sim の実カード名**（sim.legal.set_card_options が返す option の card）。
# ★B-31（2026-07-17）：初回コミット(562e646)から ("移動縦","移動横","移動") だったが実カード名は
#   "移動←→"/"移動↑↓" ＝一つも一致せず、move_cards_for/move_on/_relocate_breaks が実戦で常に
#   空を返していた（＝退避・引き剥がし系の折り手が丸ごと不在）。テストが同じ架空名を使っていた
#   ため通り続けていた。カード名は sim を単一の真実として合わせる。
_MOVE_CARDS = ("移動←→", "移動↑↓")

# 敗北ボード（そのエリアの暗躍が2以上でループ敗北。sim/effects.py で確認済み）
_DEFEAT_BOARDS = ("学校", "神社")

# 役職を疑う確率のしきい値（belief marginals を「容疑者集合」に落とす）
_SUSPECT_P = 0.15         # これ以上なら容疑者として扱う（甘めに拾う＝見落とし回避）
_LIKELY_P = 0.45          # これ以上なら「濃厚」

# 暗躍禁止は同ターン2枚目が自滅＝実効1枚/ターン（継続効果表・KB:10）。二正面
# （キラー線＋KP暗躍線が両方 暗躍禁止 を要求）を「枚数不足で未防御」と正しく検出させる
# ための per-card 上限。plan_defenses に opt-in で渡す（既定 None＝従来どおり seats だけ）。
ANRYAKU_KINSHI_TURN_CAP = {"暗躍禁止": 1}


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Break:
    """1つの AND 条件を折る具体手（＝手札から置ける1枚）。"""
    label: str                       # 人間可読（"KPを病院から移動させる"）
    card: str
    target: str
    target_kind: str                 # "character" / "board"
    cost: float                      # 席1枚=1.0＋難易度加算（低いほど安い/易しい）
    robust: bool = True              # 脚本家が同ターンで打ち消せない手か（移動禁止/暗躍禁止=堅い）


@dataclass
class Condition:
    """脅威の AND 条件1つ。折れる手（OR）を breaks に持つ。空＝この条件は折れない。"""
    label: str
    breaks: list[Break] = field(default_factory=list)
    note: str = ""                   # 折れない理由等（防御不能な供給の説明）

    @property
    def breakable(self) -> bool:
        return bool(self.breaks)


@dataclass
class Threat:
    """発火すると負ける負け筋1本。conditions を全部満たすと発火＝1つ折れば防げる。"""
    kind: str
    label: str
    prob: float                      # belief 上でこの負け筋が実在する確率（0..1）
    fatal: bool                      # 成立でループ敗北か
    timing: str                      # 効くタイミング（説明用）
    conditions: list[Condition] = field(default_factory=list)
    note: str = ""
    # ★表示用フラグ（B-1・2026-07-13・AIA表示層との結合点）：
    # 既に敗北判定域（板暗躍≥2等）＝突破済み。💀 表示・意思決定は覆えない。
    # ★B-170：板暗躍の場合だけ例外がある＝巫女/神格の友好能力なら剥がせる（実測では射程ほぼ空）。
    breached: bool = False
    race: bool = False       # 「防御不能」ではなくレース（移動可クロマク供給等＝位置/情報で勝つ）。
    # ★B-176（2026-08-07）：**発火予定日**＝この脅威が最初に発火しうる日（同ループ内・1始まり）。
    #   ターン終了フェイズ系＝当日／事件フェイズ系＝その事件の日／ループ終了フェイズ系＝最終日。
    #   None＝不明（当夜資格の対象にしない安全側）。Phase 1 計測の label 文字列照合を
    #   実装に持ち込まないためのフィールド化（監査 B176 Phase1 §7-1 の前提部品）。
    due_day: int | None = None
    # ★B-176 感度層：board_defeat で対象板の暗躍が既に臨界-1＝**今夜1枚で不可逆化**しうる印
    #   （発火＝敗北判定は最終日でも、覆える最後の夜は今日かもしれない）。
    imminent: bool = False

    @property
    def severity(self) -> float:
        """優先度＝実在確率×致死度。高いほど先に防ぐ。"""
        return self.prob * (1.0 if self.fatal else 0.5)

    @property
    def defendable(self) -> bool:
        """1つでも折れる条件があれば防御可能。全条件が折れない＝防御不能（レース対象）。"""
        return any(c.breakable for c in self.conditions)

    def cheapest_breaks(self) -> list[Break]:
        """折れる条件それぞれの最安手を集める（貪欲プランナーの入力）。"""
        out: list[Break] = []
        for c in self.conditions:
            if c.breakable:
                out.append(min(c.breaks, key=lambda b: b.cost))
        return out


@dataclass
class Plan:
    """防御計画。picks＝採用した防御（脅威被覆つき）。uncovered＝防御不能な脅威。"""
    picks: list[Break]
    covered: dict[int, str]          # id(threat) -> break.label（何でどの脅威を折ったか）
    uncovered: list[Threat]          # 防御不能（クロマク供給等）＝レースで勝つ対象
    total_cost: float
    # (card,target,target_kind) -> その手が覆う脅威の最大実在度（fatalのみ）。
    # AI加点のホット判定（致命脅威の折り手は冷却等より優先させる）に使う。
    pick_heat: dict = field(default_factory=dict)
    # ★計画の時点情報（B-1・2026-07-13）：どの盤面を見た計画か（AIA表示ヘッダ用）。
    loop: int | None = None
    day: int | None = None
    stage: str = ""                  # 例「脚本家セット後」（脚本家が今ターン伏せた札を含む盤面）


# ---------------------------------------------------------------------------
# belief → 容疑者集合
# ---------------------------------------------------------------------------
def _suspects(role_marginals: dict, role: str, thresh: float = _SUSPECT_P) -> dict:
    """役職 role を thresh 以上の確率で持つキャラ -> 確率。"""
    out = {}
    for name, dist in (role_marginals or {}).items():
        p = dist.get(role, 0.0)
        if p >= thresh:
            out[name] = p
    return out


def _char(view: dict, name: str) -> dict | None:
    for c in view.get("characters", []):
        if c.get("name") == name:
            return c
    return None


def _immobile(name: str) -> bool:
    """そのキャラが実質移動不可か（禁止エリアが3つ＝1エリアにしか居られない・A.I.等）。
    移動/移動禁止を提示・加点しても無駄になる（テスター指摘 2026-07-10）。"""
    try:
        from engine.data import forbidden_of
        return len(forbidden_of(name) or ()) >= 3
    except Exception:
        return False


def _lifted_this_loop(view: dict, name: str) -> bool:
    """医者能力3（入院患者）／女の子の友好1能力で、当ループ中に禁止エリアが解除されたか
    （公開履歴 forbidden_lifted・KB:20/30）。forbidden_lifted は『このループ中』＝ループ毎に
    リセットされる＝当ループ（view['loop']）のイベントだけ数える。"""
    loop = view.get("loop")
    return any(e.get("event") == "forbidden_lifted" and e.get("name") == name
               and e.get("loop") == loop for e in view.get("history", ()))


def _cannot_move_now(view: dict, name: str) -> bool:
    """B-21：このターン実際に動けない＝移動禁止（対象に載った移動カードの打ち消し）が
    確実に空振りになる。★恒久移動不能（A.I./ご神木＝FS/BTXに移動解放能力なし）と、
    条件付き移動不能（入院患者＝医者能力3で解除／女の子＝自身の友好1能力で解除）を区別する
    （ユーザー知見 2026-07-15：女の子・入院患者は移動できるようになることがある）：条件付きは
    『当ループ中に解除済み』なら動ける＝移動禁止の対象価値が戻るので False（＝ゲートしない）。"""
    if not _immobile(name):
        return False
    return not _lifted_this_loop(view, name)


def _relocate_breaks(opts, name: str, area: str, label_fmt: str,
                     cost: float, view: dict | None = None,
                     roles: dict | None = None,
                     avoid_areas: frozenset | tuple = ()) -> list:
    """name を area から動かす折り手（Break）を返す。キャラ移動が無理なキャラ（幻想＝
    直接セット不可）はボード移動（幻想が受ける）で動かす。実質移動不可（A.I.）は空。

    ★B-31/G4（2026-07-17）：view を渡すと**その移動札で実際に動けるか**（行き先が禁止エリア＝
      移動不成立＝空振り）を card_effect.noop_reason で判定し、空振り札は折り手にしない。
      判定は heuristic の空振りゲートと同一述語＝PLAN_HOT(+88) が上書きする事故を防ぐ
      （実測 random_BTX s0：異世界人(禁止=病院)へ神社から 移動←→＝病院行き＝不成立なのに
       91.5＝3.5+88 で採用され、防衛を落としていた）。view=None は従来どおり（判定しない）。
    ★2c（roles も渡すと G5/G6＝退避先の安全も見る）：致死zoneへのKP退避・キラー+VIP同居への
      クロマク送り込みを折り手にしない＝「安全な退避だけ提示」。
    """
    if _immobile(name):
        return []
    out = []
    for mc in opts.move_cards_for(name):          # 通常のキャラ移動
        if view is not None and _noop_char(view, mc, name, roles):
            continue                              # 行き先が禁止/危険＝この札は出さない（空振り/自滅）
        if view is not None and avoid_areas and _own_move_dest(view, name, mc) in avoid_areas:
            continue                              # ★DP-4：自分の札だけで解決すると的エリアへ入る
        out.append(Break(label_fmt.format(n=name, a=area, how="移動"),
                         mc, name, "character", cost, robust=False))
    # ★DP-4（2026-07-29）：ボード移動フォールバックは**幻想専用**。
    #   `board_move_cards(area)` は「そのボードに置ける移動札」＝幻想が居るボードでのみ存在し、
    #   実際に動くのは**幻想**（KB: 30 幻想特性）。従来は name のキャラ移動札が手札に無いだけの
    #   ケース（他席が既にその対象に置いた等）でもここへ落ち、**name ではなく幻想を動かす札**を
    #   「name を剥がす折り手」として登録していた＝対象違いの過大主張。
    if not out and area is not None and name == "幻想":
        for mc in opts.board_move_cards(area):
            if avoid_areas:
                continue          # 幻想のボード移動は行き先が札依存＝的回避を保証できない
            out.append(Break(label_fmt.format(n=name, a=area, how="ボード移動"),
                             mc, area, "board", cost, robust=False))
    return out


# ---------------------------------------------------------------------------
# ★DP-4（2026-07-29）：位置条件の折り手を「本当にその条件を崩す手」に限定するための述語群。
#
# 背景＝B-100 Phase 2 の要確認事項：`移動禁止→(被害者)` が「2人きり」の折り手として
# 被覆済みに数えられたが、脚本家は**相手の側を動かして**2人きりを作り成功した。
# 位置の条件は「片方を固定する」だけでは崩れない＝**相手側の到達可能性**まで見る必要がある。
#
# 判定材料は公開情報のみ：
#   - 移動できる駒＝脚本家が今ターン札を伏せた対象（`_mm_touched`。位置は公開・中身は伏せ）。
#     幻想は直接セット不可＝**幻想が居るボード**に札があれば動きうる（KB: 30）。
#   - 2×2盤＝1枚の移動札で他の3エリアのどこへでも行ける（↑↓／←→／斜め）。行き先が
#     禁止エリアなら移動不成立＝その場に留まる（KB: 60 A-2）。
#   - 脚本家の1ターンのセットは3枚＝**移動に使えるのは高々3枚**。
# ★健全側の既定＝「判定できない／情報不足」は **成立しうる（＝折れていない）** と返す
#   ＝折り手を過剰に消さない（過小主張に振り切らないため）。
# ---------------------------------------------------------------------------
_MM_SET_PER_TURN = 3          # 脚本家が1ターンにセットする枚数（＝移動に使える上限）
_PROT_MOVE_TOGGLE = {"移動←→": (1, 0), "移動↑↓": (0, 1)}


def _forbidden_areas(name: str) -> frozenset:
    try:
        from engine.data import forbidden_of
        return frozenset(forbidden_of(name) or ())
    except Exception:
        return frozenset()


def _own_move_dest(view: dict, name: str, card: str) -> str | None:
    """**自分の移動札だけ**が解決したときの行き先（禁止先なら現在地＝留まる）。

    脚本家は主人公より先に伏せる＝主人公の移動を見てから合わせることはできない。よって
    「自分の札がそもそも正しい方角を向いているか」は自分だけで決まる決定的な性質＝ここで見る。
    """
    from engine.board import destination
    t = _PROT_MOVE_TOGGLE.get(card)
    c = _char(view, name)
    cur = (c or {}).get("area")
    if t is None or cur is None:
        return None
    try:
        dest = destination(cur, t)
    except Exception:
        return None
    return cur if dest in _forbidden_areas(name) else dest


def _mm_movers(view: dict) -> set:
    """脚本家が今ターン動かしうる駒（＝札を伏せた対象。幻想はボードの札で動く）。"""
    chars, boards = _mm_touched(view)
    movers = {n for n in chars if _alive(view, n)}
    gc = _char(view, "幻想")
    if gc and gc.get("alive", True) and gc.get("area") in boards:
        movers.add("幻想")
    return movers


def _can_reach(view: dict, name: str, area: str, movers: set) -> int | None:
    """name を area に居させるのに要する脚本家の移動枚数（0＝既に居る／1＝動かす）。
    不可能なら None。"""
    c = _char(view, name)
    if not c or not c.get("alive", True) or c.get("area") is None:
        return None
    if c["area"] == area:
        return 0
    if name not in movers or _immobile(name) or area in _forbidden_areas(name):
        return None
    return 1


def _can_leave(view: dict, name: str, area: str, movers: set) -> bool:
    """name を area から追い出せるか（行き先が1つでも禁止でなければ可）。"""
    if name not in movers or _immobile(name):
        return False
    forb = _forbidden_areas(name)
    return any(a != area and a not in forb for a in _AREAS)


def _pair_possible(view: dict, movers: set, a: str, b: str) -> bool:
    """脚本家が movers だけを動かして、行動解決後に **a と b をそのエリアに2人だけ**に
    できるか。False＝その位置条件はこの盤面では作れない＝折れている。"""
    if not _alive(view, a) or not _alive(view, b):
        return False
    for area in _AREAS:
        ca, cb = _can_reach(view, a, area, movers), _can_reach(view, b, area, movers)
        if ca is None or cb is None:
            continue
        need = ca + cb
        ok = True
        for c in view.get("characters", []):
            n = c.get("name")
            if n in (a, b) or not c.get("alive", True) or c.get("area") != area:
                continue
            if not _can_leave(view, n, area, movers):
                ok = False
                break
            need += 1
        if ok and need <= _MM_SET_PER_TURN:
            return True
    return False


def _alone_with(view: dict, name: str, area: str, other: str) -> bool:
    """name が area へ動いたとき、そこが other との2人きりになるか（自分の札だけの結果）。"""
    occ = [c.get("name") for c in view.get("characters", [])
           if c.get("alive", True) and c.get("area") == area and c.get("name") != name]
    return occ == [other]


def _alive(view: dict, name: str) -> bool:
    c = _char(view, name)
    return bool(c and c.get("alive", True))


# ---------------------------------------------------------------------------
# ★B-130（2026-08-02）：抑止役（不安カウンターを取り除ける友好能力の担い手）の単一ソース
# ---------------------------------------------------------------------------
# 起票＝トリアージ 2026-08-01 §B-130（手練れ指摘）「男子学生に友好+2 が載っているので、
# 学生であるお嬢様がその場にとどまるなら不安+1 を載せられても事件発生を抑止できる」。
#
# KB 接地（`rules/20_goodwill_abilities.md`。行番号は 07a7b33 時点）：
#   男子学生／女子学生『学生の不安除去』♡2・回数無制限・**同一エリアの自身以外の学生1人**
#       ＝`:36`,`:37`（一覧）／`:89-92`（本文）／`:292`,`:293`（実カード書き起こし）
#   医者『不安操作（除去/付与）』♡2・無制限・同一エリアの自身以外1人 ＝`:46`／`:225-229`／`:297`
#   ナース『不安臨界以上のキャラの不安除去』♡2・無制限・**不安が臨界以上**の同エリア1人
#       ＝`:66`／`:238-241`／`:306`（★友好無視/絶対友好無視で拒否されない＝`:242`）
#   アイドル『不安除去』♡**3**・無制限・同一エリアの自身以外1人 ＝`:48`／`:178-182`／`:303`
#   教師『学生の不安操作』♡**3**・無制限・同一エリアの学生1人 ＝`:52`／`:112-116`／`:316`
# ＝「どの能力が不安除去か」の単一ソースは `engine.data.ABILITY_KINDS` の `軽量不安除去`
#   （キャラ名の羅列を本モジュールに複製しない＝二重実装の禁止）。
#
# ★安全側の既定＝**使えることが確定できる時だけ True**（過大主張を作らない）。
_SUPPRESSOR_KIND = "軽量不安除去"
#: 抑止役の役職が友好無視/絶対友好無視である疑いがこれ以上なら「拒否されうる」＝数えない。
SUPPRESSOR_IGNORE_P = 0.15


def _friendship_ignore_prob(roles: dict | None, name: str) -> float:
    """belief 上で name が friendship-ignore 系の役職である確率（roles=None なら 0.0）。"""
    dist = (roles or {}).get(name) or {}
    return sum(p for r, p in dist.items() if role_has_friendship_ignore(r))


def suppressors_for(view: dict, target: str, *, roles: dict | None = None,
                    require_funded: bool = True) -> list[str]:
    """`target` の不安を**今ターンの主人公能力フェイズで**1つ取り除ける同エリアの抑止役。

    すべて公開情報（位置・友好カウンター・キャラ名）だけで確定する条件：
      (1) 抑止役が生存し `target` と**同一エリア**・`target` 自身ではない
      (2) その友好能力の kind が `軽量不安除去`（`engine.data.ABILITY_KINDS`）
      (3) **回数無制限**（1/L は「もう使ったか」を公開情報から確定できない＝安全側で除外）
      (4) 友好カウンターが**必要友好数以上**（＝今ターン既に解禁されている）
      (5) 対象クラスの限定を満たす（能力名に「学生」＝`target` が学生・`engine.data.is_student`）
      (6) 「不安臨界以上」限定（ナース）＝`target` の不安が臨界以上（臨界不明なら不可）
      (7) 脚本家に拒否されない見込み（役職が友好無視/絶対友好無視である belief 上の確率が
          `SUPPRESSOR_IGNORE_P` 未満。ただし `UNREFUSABLE_ABILITY_CHARS` は拒否不可＝常に可）
    `roles=None` は (7) を判定しない＝**緩い**列挙（監査の対照用）。
    `require_funded=False` は (4) を判定しない＝「投資すれば抑止役になりうる同席者」の列挙
    （監査の参考値専用。防御判断には使わない）。
    """
    tc = _char(view, target)
    if not tc or not tc.get("alive", True):
        return []
    area = tc.get("area")
    if area is None:
        return []
    out: list[str] = []
    for c in view.get("characters", []) or []:
        name = c.get("name")
        if name == target or not c.get("alive", True) or c.get("area") != area:
            continue
        abilities = goodwill_abilities_of(name)
        if not abilities:
            continue                      # 未収録(None)も能力なし([])も対象外
        gw = int(c.get("goodwill", 0) or 0)
        for ab in abilities:
            if ability_kind(name, ab["name"]) != _SUPPRESSOR_KIND:
                continue
            if ab.get("once_per_loop"):
                continue                  # (3) 使用済みか確定できない＝安全側で除外
            if require_funded and gw < int(ab.get("hearts", 99)):
                continue                  # (4) 未解禁
            if "学生" in ab["name"] and not is_student(target):
                continue                  # (5) 対象クラス限定
            if "臨界以上" in ab["name"]:
                th = unrest_threshold_of(target)
                if th is None or int(tc.get("unrest", 0) or 0) < th:
                    continue              # (6) ナース＝臨界未満には使えない
            if roles is not None and name not in UNREFUSABLE_ABILITY_CHARS \
                    and _friendship_ignore_prob(roles, name) >= SUPPRESSOR_IGNORE_P:
                continue                  # (7) 拒否されうる
            out.append(name)
            break
    return out


# ---------------------------------------------------------------------------
# options インデックス（「この防御が手札にあるか」を高速に引く）
# ---------------------------------------------------------------------------
class _Opts:
    def __init__(self, options: list[dict]):
        self._chr: dict = {}   # (card, target) -> True （target_kind=character）
        self._brd: dict = {}   # (card, area)   -> True （target_kind=board）
        for o in options or []:
            key = (o.get("card"), o.get("target"))
            if o.get("target_kind") == "board":
                self._brd[key] = True
            else:
                self._chr[key] = True

    def move_on(self, name: str) -> bool:
        return any((c, name) in self._chr for c in _MOVE_CARDS)

    def move_cards_for(self, name: str) -> list:
        """name を動かせる実カード名（"移動縦"/"移動横" 等）。AI加点が実 option と
        一致するよう、ハードコードの "移動" ではなく実在カードを返す。"""
        return [c for c in _MOVE_CARDS if (c, name) in self._chr]

    def board_move_cards(self, area: str) -> list:
        """area のボードに置ける移動カード（幻想＝直接セット不可だがボード経由で動く）。"""
        return [c for c in _MOVE_CARDS if (c, area) in self._brd]

    def has_char(self, card: str, name: str) -> bool:
        return (card, name) in self._chr

    def has_board(self, card: str, area: str) -> bool:
        return (card, area) in self._brd


# ---------------------------------------------------------------------------
# 供給判定：暗躍禁止で止まらない供給がそのエリアにあるか
# （クロマク/不穏な噂/黒猫＝§A の〔不可〕。ここでは盤面の可視情報だけで保守的に判定）
# ---------------------------------------------------------------------------
# ★DP-6（2026-07-31）ablation トグル（A-78 の作法・既定＝修正後）：
#   False＝旧挙動（B-112 §3 が特定した過大主張＝「不穏な噂が場に在るだけで板の暗躍到達を
#   丸ごと防御不能に落とす」）に戻す。CF帰属・変異テスト専用。本番は必ず True。
DP6_SUPPLY_LEDGER = True

#: 板敗北の閾値＝**暗躍2**（ループ終了時判定）。KB裏取り（DP-6・2026-07-31）：
#:   守るべき場所＝学校（rules/40:47）／復讐者の灯火＝ボードX（rules/40:42）／
#:   封印されしモノ＝神社（rules/50:38）／巨大時限爆弾X＝ボードX（rules/50:52）。
#:   4ルールとも「暗躍カウンターが2つ以上」＝閾値2以外の板敗北ルールは FS/BTX に無い。
BOARD_DEFEAT_ANYAKU = 2

#: 遠隔殺人の的化の閾値＝**キャラの暗躍2**（`rules/40_first_steps.md:152`／
#:   `rules/50_basic_tragedy_x.md:209`＝「暗躍カウンターが2つ以上置かれているキャラクターが
#:   いる場合、その中から任意の1人を死亡させる」）。板敗北(BOARD_DEFEAT_ANYAKU)とは別の条文。
REMOTE_MURDER_ANYAKU = 2

# ★B-184（2026-08-07・§60-9）：planner の break 生成に void 判定を共有させる切替口。
#   True＝board_defeat の「今夜打つ前提」の文脈（最終日 or 板の暗躍≥1＝今夜1枚で
#   不可逆化）で、mm がその板に今ターン伏せていない（void）暗躍禁止 break を生成しない。
#   B-183 §5-2 の実測＝空振りフィルタ門 63/63T すべて void＝pick 側の過大主張。
#   ★非当夜（非最終日かつ暗躍0）は従来どおり break を残す＝「暗躍禁止を保持して
#   mm の伏せに備える」複数夜の計画価値を殺さない（監査 B-183 §6-2 の狭め）。
#   既定 False＝挙動 bit 不変。テスト＝tests/test_b184_void_breaks.py。
B184_VOID_AWARE_BREAKS = True


def board_kinshi_void(view: dict, area: str) -> bool:
    """★B-184：板への暗躍禁止が**今夜確実にゼロ効果（void）**か＝mm がその板に
    今ターン伏せていない＝打ち消す暗躍+が無い（`rules/10_action_cards.md:61`＝
    暗躍禁止は当ターンの札・盤に残らない。mm の配置位置は公開情報）。

    ★void 判定の**単一ソース**＝席側 `b100_mix.futile_reason` の板枝もここを呼ぶ
    （B-183 §5-2 が特定した「planner だけが void を見ない」乖離の是正）。
    """
    return area not in _mm_touched(view)[1]

# ★B-187（2026-08-07・§61(1) ユーザー実戦）：僕と契約しようよ！の脅威候補を
#   **少女（engine.data.SHOUJO）との交差に限る**切替口。
#   KB＝`rules/50_basic_tragedy_x.md:42`「キーパーソンは**必ず少女**」＝
#   非少女（学者・サラリーマン等＝大人・男性）は契約の敗北対象になりえない（KBが一意に決める可否）。
#   機序＝belief 側は少女限定を同時分布で実装済み（`agents/belief.py:163-215`）だが、
#   `_threat_kp_anyaku` は `contract_prob × P(KP周辺)` の**独立近似**で掛けるため、
#   殺人計画など契約以外のルール組由来の非少女 P(KP周辺)>0 が漏れ込んでいた
#   （正しい同時確率 P(契約 ∧ KP=非少女) は 0）。
#   ★同型の複製列挙器 `agents/attack_plan._contract_threats`（mm の圧力オラクル）も
#   **この同じフラグ**を読む（写しのフラグを作らない）。
#   既定 False＝挙動 bit 不変。テスト＝tests/test_b187_contract_shoujo.py。
B187_CONTRACT_SHOUJO_ONLY: bool = True

# ★B-134（2026-08-01）ablation トグル（DP-6 の作法・既定＝修正後）：
#   False＝旧挙動（距離を**カウンタ個数**だけで測る＝`暗躍+2` が1枚で2個載ることを
#   数えない）に bit 復帰する。CF帰属・変異テスト専用。本番は True。
B134_CARD_DISTANCE = True

#: ★B-134：距離の是正を適用してよい `rule_p`（＝P(その板が敗北ボード)）の下限。
#:   1.0（＝可能世界が全てその板を指す＝**確定**）だけに絞る＝
#:   「**確定できる時だけ距離を詰める**」（B-113／B-132 の安全側の流儀）を
#:   〈残弾〉と〈敗北板の同定〉の**両方**へ適用する。掃引用に定数として置く。
#:   実測（§5b）＝この絞りを外すと 3日級 id で per-game 退行1（`random_FS#0` 3→4・
#:   rule_p=0.978 の板が `PLAN_HOT` を跨いで移動3席を食う）。
B134_RULEP_CERTAIN = 1.0 - 1e-9


def mm_plus2_spent(view: dict) -> bool | None:
    """脚本家の **`暗躍+2`（1/loop）がこのループで既に消費されたか**（公開情報）。

    - `True`  ＝消費済み（もう出せない）
    - `False` ＝未消費（まだ出せる）
    - `None`  ＝**判定材料が無い**（view に `used_cards` が無い）＝主張しない

    根拠＝`view["used_cards"]["mastermind"]` は公開情報（`sim/views.py` `_common`）。
    1/loop 札は解決後に手札へ戻らない（`rules/00_rules_core.md:108`／
    `engine/models.py` ONCE_PER_LOOP／`sim/flow.py:251-255`）。暗躍禁止で打ち消されても
    消費される（B-132 §1-a-3 の実測）。

    ★**単一ソース**＝`card_supply_max`（B-132 の残弾会計）と `anyaku_card_reach`
      （B-134 の距離会計）の両方がここを読む。**不明（None）のときの倒し方は
      呼び出し側が決める**（両者で安全側の向きが逆＝各関数の docstring 参照）。
    """
    uc = view.get("used_cards")
    if not isinstance(uc, dict) or "mastermind" not in uc:
        return None
    return "暗躍+2" in (uc.get("mastermind") or [])


def days_left_in_loop(view: dict) -> int:
    """このループに残っている日数（今日を含む）。"""
    dpl = int(view.get("days_per_loop") or view.get("day", 1) or 1)
    return max(0, dpl - int(view.get("day", 1)) + 1)


def card_supply_max(view: dict) -> int:
    """★B-132：mm が**行動解決の暗躍カード**で1つの的に積める、このループの残り上限。

    `残り日数 ＋ (2 if 暗躍+2 が未消費)`（暗躍+1 は毎日再利用・暗躍+2 は 1/loop）。
    ★mm 側 `agents/heuristic.py:1398-1399` の `_card_supply` と**同じ式の鏡像**。
    ★不明（`mm_plus2_spent` が None）＝**未消費と仮定**＝上限を大きく見る＝
      「札では届かない」と主張しない側＝B-132 の安全側（見切らない）。
    """
    return days_left_in_loop(view) + (0 if mm_plus2_spent(view) is True else 2)


def anyaku_card_reach(view: dict) -> int:
    """★B-134：mm が**1枚の暗躍カード**で1つの的へ載せられるカウンタの最大値。

    - `暗躍+1` ＝ 1（毎日出せる）
    - `暗躍+2` ＝ **2**（1/loop・`rules/10_action_cards.md:39`「セットされたキャラまたは
      **ボード**に暗躍+2」・`engine/models.py:43` `ANRYAKU_PLUS`）

    ∴ 返り値は **2（`暗躍+2` の残弾が公開情報から確定でき、かつ出す日が残っているとき）**
    か **1**。★**安全側の原則（B-113／B-132 の流儀）＝「確定できる時だけ距離を詰める」**：
    `mm_plus2_spent` が `None`（判定材料なし）や `True`（消費済み）なら **1**＝従来どおり。
    """
    if not B134_CARD_DISTANCE:
        return 1
    if mm_plus2_spent(view) is False and days_left_in_loop(view) >= 1:
        return 2
    return 1


def unstoppable_supply_gap(view: dict, area: str, cur: int,
                           threshold: int = BOARD_DEFEAT_ANYAKU, *,
                           supply_rumor: bool = False, roles: dict | None = None,
                           rumor_left: int | None = None) -> int:
    """★DP-6（2026-07-31）：「止まらない供給」判定の**単一ソース**（カウンタ収支）。

    返り値＝ **gap** ＝『止まらない供給を全部使っても、閾値 threshold に届くには
    **行動解決フェイズの暗躍カード（＝暗躍禁止で断てる供給）があと何個**要るか』：

        gap = threshold - cur - (このループ残りの止まらない供給の寄与)

    - **gap <= 0** ＝止まらない供給**だけ**で届く＝真に防御不能（暗躍禁止でも移動でも不可）。
    - **gap == 1** ＝行動解決の暗躍1個で届く＝暗躍禁止1枚が実効（実在度も cur>=1 と同等）。
    - **gap >= 2** ＝まだ遠い。

    止まらない供給の会計（KB裏取り済み＝DP-6監査doc §1）：
    - **不穏な噂**＝【任意】脚本家能力フェイズに任意のボード1つへ暗躍+1・**1/loop**
      （rules/40:61・rules/50:75）→ 寄与は **高々1**。rumor_left＝このループの残弾
      （★B-113〔噂の残弾を公開情報から数える・belief レーン〕の注入用の継ぎ目。
       None＝未消費と仮定＝1個。本チケットでは常に None）。
    - **黒猫**＝特性1「各ループ開始時に神社へ暗躍+1」（rules/30 黒猫特性）＝
      その+1は**ループ開始時点で既に現在値 cur に入っている**＝ループ中の追加供給は **0**
      ＝収支に現れない（旧実装の神社条項は同型の過大主張＝B-112 未確認#4。
      なお旧条項は belief の役職キーに「黒猫」が存在しないため実際には死文だった）。
    - **クロマク**＝毎ターン脚本家能力フェイズに+1（rules/40:86）＝本当に止まらないが、
      **移動で剥がせる**（同エリア/自ボードにしか置けない）＝この関数の対象外
      （呼び出し側がレース／剥がしの折り手として別扱いする。`_kuromaku_supply_here`）。

    ★判定不能側の既定＝噂の残弾は「未消費（=1）」と仮定＝防御不能側に倒す（健全側）。
    """
    if not DP6_SUPPLY_LEDGER:
        # ── ablation：旧 truly_unstoppable（B-112 §3 の過大主張）を bit そのまま再現 ──
        #   ・噂が在るだけで（現在値を見ずに）防御不能／都市だけ無条件に除外
        #   ・黒猫条項＝roles に役職キー「黒猫」は現れない＝常に False（死文）
        old = (supply_rumor and area != "都市") or (
            area == "神社" and any(_alive(view, n)
                                   for n in _suspects(roles or {}, "黒猫", _SUSPECT_P)))
        return 0 if old else max(0, threshold - int(cur))
    contrib = 0
    if supply_rumor:
        contrib += 1 if rumor_left is None else max(0, min(1, int(rumor_left)))
    return threshold - int(cur) - contrib


def _kuromaku_supply_here(view: dict, area: str, roles: dict) -> bool:
    """クロマク疑い（belief・≥_SUSPECT_P）が area に生存して居るか＝毎ターン脚本家能力
    フェイズに+1を供給しうる（rules/40:86）＝暗躍禁止では止まらない（移動で剥がすレース対象）。"""
    for name, _p in _suspects(roles, "クロマク", _SUSPECT_P).items():
        c = _char(view, name)
        if c and c.get("alive", True) and c.get("area") == area:
            return True
    return False


def _unstoppable_supply(view: dict, area: str, roles: dict) -> bool:
    """★旧版（DP-6 で legacy 化・ablation 経路専用）：クロマク疑い同エリア or 黒猫@神社。

    黒猫条項はカウンタ収支として誤り（ループ開始時+1＝既に現在値に入っている）かつ
    belief の役職キーに「黒猫」は無い＝死文。新経路は `_kuromaku_supply_here`＋
    `unstoppable_supply_gap` を使う。ここは ablation（DP6_SUPPLY_LEDGER=False）の
    bit 再現のためだけに残す。
    """
    if area == "神社":
        for name, p in _suspects(roles, "黒猫", _SUSPECT_P).items():
            if _alive(view, name):
                return True
    return _kuromaku_supply_here(view, area, roles)


# ---------------------------------------------------------------------------
# 脅威ディテクタ（負け筋ごと）。view + belief + options から Threat を作る。
# ---------------------------------------------------------------------------
def _mm_touched(view) -> tuple[set, set]:
    """mmが今ターン伏せた札の対象（キャラ集合, ボード集合）。位置は公開・中身は伏せ。
    「脅威の関係者に mm が触れた」＝仕込みの徴候（バグ6対応 2026-07-09）。"""
    chars: set = set()
    boards: set = set()
    for p in view.get("placements", []) or []:
        if p.get("owner") != "mastermind":
            continue
        if p.get("target_kind") == "board":
            boards.add(p.get("target"))
        else:
            chars.add(p.get("target"))
    return chars, boards


def _today_kill_zone(view) -> str | None:
    """今日の致死事件のゾーン（病院の事件＝病院に暗躍≥1で病院の全員死亡）。無ければ None。"""
    day = view.get("day", 1)
    for i in view.get("incidents", []):
        if i.get("day") == day and i.get("name") == "病院の事件":
            return "病院"
    return None


def _noop_char(view, card: str, target: str, roles: dict | None = None) -> bool:
    """★B-28：その札がその対象に『公開情報から証明可能に無効（空振り）／自滅』か。

    判定の実体は `agents/card_effect.noop_reason`＝**heuristic の空振りゲートと同一述語**
    （plan にゲートが無いと PLAN_HOT(+88) が heuristic の判断を上書きする＝B-21/B-21b/B-26/G1）。
    mm札は view の placements から導出する（`_mm_touched`）＝**関数ごとの引数配線を増やさない**。
    ★判定不能は False（＝無効とは言えない）＝健全側＝折り手を過剰に消さない。

    ★2c（B-28 Step3・roles を渡すと G5/G6 も有効）：移動 break の退避先安全を見る＝
      G5＝KPを今日の致死事件の kill zone へ退避させない／G6＝確定クロマクをキラー疑い＋
      KP/フレンド疑いが同居するエリアへ送り込まない（heuristic 側は B-22 で実装済み・plan にも
      同述語で入れる＝「安全な退避だけ提示」）。keyperson は対象がKP疑いならその対象（G5用）、
      それ以外は最有力KP疑い（G6のVIP位置用）にする＝移動対象で使い分ける。
    """
    mm_chars, _boards = _mm_touched(view)
    if roles is None:
        ctx = NoopCtx(mm_chars=frozenset(mm_chars))
    else:
        kps = _suspects(roles, "キーパーソン")
        kp = target if target in kps else (max(kps, key=kps.get) if kps else None)
        ctx = NoopCtx(
            mm_chars=frozenset(mm_chars),
            keyperson=kp,
            kill_zone=_today_kill_zone(view),
            kuromaku_suspects=frozenset(_suspects(roles, "クロマク")),
            killer_suspects=frozenset(_suspects(roles, "キラー")),
            friend_guards=frozenset(_suspects(roles, "フレンド")),
        )
    return noop_reason(view, card, target, "character", ctx) is not None


def _cultists_on_area(view, roles, area) -> list:
    """area に居るカルティスト疑い（役職・≥_SUSPECT_P）。カルティストは移動後の現在位置の
    同エリア/自ボードの暗躍禁止を無視する（KB:00/60）＝そのボードの暗躍禁止Breakを破る。"""
    return [n for n in _suspects(roles, "カルティスト", _SUSPECT_P)
            if (_char(view, n) or {}).get("area") == area and _alive(view, n)]


def _add_kinshi_break(c1: "Condition", opts, area, view, roles, label,
                      tonight: bool = False) -> None:
    """area の行動解決暗躍を断つ『暗躍禁止』Breakを、カルティスト無効化を考慮して足す（B-8）。
    カルティスト疑いが area に居ると暗躍禁止は無視されうる＝robust=False に落とす。

    ★B-184：`tonight=True`（呼び出し側が「今夜打つ前提」の文脈だと宣言した時）かつ
      切替口 ON かつ void（mm がその板に今ターン伏せていない＝`board_kinshi_void`＝
      席側 futile_reason と同じ単一ソース）なら break を**生成しない**（今夜確実に
      ゼロ効果の札を pick させない＝B-183 §5-2）。既定 `tonight=False`＝従来どおり
      ＝非当夜の計画文脈・他の呼び出し元（病院アーム等）は触らない。

    ★DP-4（2026-07-29）の是正2点：
      (1) 「カルティストを移動で剥がす」を**単独の折り手にしない**。カルティストを剥がしても
          脚本家の暗躍+札そのものは残る＝『暗躍が2に届く』条件は崩れない（暗躍禁止を別席で
          重ねて初めて効く＝2枚コンボであって OR の折り手ではない）。プランナーは条件ごとに
          **1枚**しか採らないため、剥がしを折り手に数えると被覆の過大主張になる。
      (2) カルティスト同居時の暗躍禁止のコストを 0.5→1.8 に上げる。従来は**無視されうる
          （＝効かないかもしれない）暗躍禁止の方が、確実に効く暗躍禁止(1.0)より安い**という
          逆転が起きており、プランナーが好んでカルティストの居る板へ暗躍禁止を回していた。
    """
    if B184_VOID_AWARE_BREAKS and tonight and board_kinshi_void(view, area):
        c1.note = c1.note or (
            f"mmが{area}に今ターン伏せていない＝暗躍禁止は今夜空振り（void）"
            "＝今夜打つ折り手にしない（mmが伏せた夜に改めて折る）")
        return
    if not opts.has_board("暗躍禁止", area):
        return
    cult = _cultists_on_area(view, roles, area)
    if cult:
        c1.breaks.append(Break(
            f"{area}に暗躍禁止（★カルティスト疑いが同ボードで無視しうる＝堅くない）",
            "暗躍禁止", area, "board", 1.8, robust=False))
    else:
        c1.breaks.append(Break(label, "暗躍禁止", area, "board", 1.0, robust=True))


def _add_anyaku_supply_breaks(c1: "Condition", view, roles, opts, victim,
                               area, supply_rumor, rumor_left=None) -> None:
    """暗躍供給を止める折り手を c1 に全部足す：
      (1) 暗躍禁止（行動解決の暗躍カードを断つ）
      (2) クロマク疑いを移動で的エリアから剥がす（同エリア/自ボードにしか置けない）
      (3) victim を的エリアから移動で逃がす（クロマクの射程外へ）
    真に止まらない（不穏な噂／黒猫のみ）＆折る手が無いときだけ note（レース対象）。

    ★DP-4（2026-07-29）の是正＝**暗躍禁止の対象**：本関数の呼び出しには2種類ある。
      - `victim=None`（病院の事件の主人公死亡アーム）＝**ボードの暗躍**が的
      - `victim=<キャラ名>`（KP暗躍2／僕と契約／キラー自身の暗躍4）＝**そのキャラの暗躍**が的
      暗躍禁止は engine/resolver で **(target, target_kind) 単位**に集計される＝
      *ボード*に置いた暗躍禁止は*キャラ*に載った暗躍+を1つも打ち消さない（実測 §1 再現3）。
      従来はどちらの場合もボードへの暗躍禁止を折り手にしていた＝**対象違いの過大主張**。
      キャラが的のときはキャラへの暗躍禁止（G1＝mm札がある時のみ実効）だけを折り手にする。
      ★例外＝幻想：直接セット不可で、居るボードの暗躍+/暗躍禁止が本人に複製される（E-1）
        ＝幻想が的のときはボードへの暗躍禁止が正しい折り手。

    ★DP-4 の是正2＝**逆向きの誤り（過小主張）も同時に直す**：不穏な噂（【任意】脚本家能力
      フェイズに**任意のボード1つ**に暗躍+1・KB: 40:61）と黒猫（各ループ開始時に**神社ボード**へ
      暗躍+1・KB: 30 特性1）は、どちらも**ボードにしか置かれない**。よって
      **キャラの暗躍が的のときは、この2つは供給源になりえない**（キャラの暗躍を暗躍禁止で
      止められない供給は**クロマク**＝同エリアのキャラ1人 だけ・KB: 40:86）。
      従来はキャラが的でも噂/黒猫を理由に「止まらない供給＝折れない」と note を付けて
      条件を丸ごと諦めていた＝**本当は折れる手（本人への暗躍禁止）を落としていた**。
      実測（random_BTX s19 L7D5）：この過小主張のせいで KP（暗躍0・mm札あり）への暗躍禁止が
      折り手から消え、席が別の的へ回って L7 を落としていた。
    """
    _board_target = victim is None or victim == "幻想"   # ボードの暗躍が的か
    # ★DP-6（2026-07-31）：噂/黒猫は**ボード供給**＝キャラの暗躍が的のときは無関係（DP-4 是正2）。
    #   ボードが的のときも「噂が在るだけで諦める」のではなく**カウンタ収支**で判定する
    #   （単一ソース＝unstoppable_supply_gap。噂＝+1/loop・黒猫＝ループ中の追加0）。
    #   現在の board 的条件は全て閾値2（factor都市の獲得・病院の主人公アーム・板到達）。
    unstop = False
    _cur_b = 0
    if _board_target and area is not None:
        if DP6_SUPPLY_LEDGER:
            _cur_b = int((view.get("board_anyaku") or {}).get(area, 0) or 0)
            # ★gap<=0 には「既に暗躍が閾値以上」も含まれる＝既に真の到達条件は供給停止では
            #   偽にできない（暗躍カウンターを減らす手段は主人公の手札に無い＝KB:00・
            #   DP-4 #22 と同じ理屈）。
            # ★B-170（2026-08-05）＝**「手札に無い」は正しいが「手段が無い」は誤り**：
            #   板の暗躍は**友好能力**で剥がせる（`rules/20_goodwill_abilities.md:129` 巫女
            #   「神社の暗躍除去」♡3・神社に居るときのみ・回数無制限／`:153` 神格
            #   「暗躍除去（キャラ/ボード）」♡5・自ボード・回数無制限）。しかも
            #   主人公能力フェイズ(6) は事件(7)・ループ終了処理(9) より前
            #   （`rules/00_rules_core.md:97-115`）＝**板敗北の判定前に間に合う**。
            #   ★それでも折り手（Break）を足していないのは**射程が実測でほぼ空だから**：
            #     「その板の上に浄化係が立っていて友好も足りている席」は
            #     3日級130局・5日級70局とも **0**（`arena/b170_audit.py count`・
            #     `docs/監査_B170_板の暗躍は剥がせる_2026-08-05.md` §3）。
            #     巫女は**神社しか**剥がせず、神格はキャスト入りが 3日級5局／5日級5局のみ。
            #   旧実装は cur を見ずに暗躍禁止を折り手にしていた
            #   ＝防御可能側の過大主張＝同時に是正。
            unstop = unstoppable_supply_gap(
                view, area, _cur_b, BOARD_DEFEAT_ANYAKU,
                supply_rumor=supply_rumor, roles=roles,
                rumor_left=rumor_left) <= 0
        else:
            # ── ablation（A-78）：旧の真偽（cur を見ない）を bit 再現 ──
            unstop = (supply_rumor and area != "都市") or (
                area == "神社" and any(
                    _alive(view, n) for n in _suspects(roles, "黒猫", _SUSPECT_P)))
    cultists = [n for n in _suspects(roles, "クロマク", _SUSPECT_P)
                if (_char(view, n) or {}).get("area") == area and _alive(view, n)]
    # クロマク/噂は mm能力フェイズ＝暗躍禁止で止まらない。移動でしか対処できない。
    if cultists:
        for cu in cultists:                          # クロマクを的から剥がす（幻想はボード移動）
            c1.breaks.extend(_relocate_breaks(
                opts, cu, area, "{n}（クロマク疑い）を{a}から{how}で剥がす", 1.6,
                view=view, roles=roles))
        if victim:                                    # victim をクロマクの射程外へ（A.I.は不可）
            c1.breaks.extend(_relocate_breaks(
                opts, victim, area, "{n}を{a}から{how}でクロマクの射程外へ逃がす", 1.5,
                view=view, roles=roles))
    elif not unstop and area is not None:
        # 行動解決の暗躍カードのみ＝暗躍禁止で断てる（★カルティスト無効化を考慮＝B-8）
        if _board_target:
            _add_kinshi_break(c1, opts, area, view, roles,
                              f"{area}に暗躍禁止（行動解決の暗躍を断つ）")
        elif opts.has_char("暗躍禁止", victim) and not _noop_char(view, "暗躍禁止", victim):
            # ★キャラが的＝キャラへの暗躍禁止のみ（G1：mm札が無ければ打ち消す暗躍が無い＝空振り）
            c1.breaks.append(Break(
                f"{victim}に暗躍禁止（本人に載る暗躍を断つ）",
                "暗躍禁止", victim, "character", 1.0, robust=True))
    if not c1.breaks:
        c1.note = ("既に暗躍が2以上＝供給を止めても戻せない"
                   "（板の暗躍を減らす行動カードは無い。剥がせるのは巫女♡3〔神社限定〕/"
                   "神格♡5〔自ボード〕の友好能力だけ＝B-170）"
                   if unstop and _cur_b >= BOARD_DEFEAT_ANYAKU and DP6_SUPPLY_LEDGER
                   else
                   "止まらない供給（不穏な噂1/loop）だけで暗躍2に届く＝"
                   "暗躍禁止でも移動でも止まらない（レース対象）"
                   if unstop else
                   "クロマク供給を今ターン折る移動札が手札に無い（クロマクは暗躍禁止で止まらない）"
                   if cultists else
                   "この供給を今ターン折る手が手札に無い（暗躍禁止/移動を確保）")


def _threat_factor_kp(view, roles, opts, supply_rumor: bool = False,
                      mm_chars: set = frozenset(),
                      rumor_left=None) -> list[Threat]:
    """KP能力を獲得したファクターの死亡＝KPと同じ即ループ終了（B-34・KB: 50:174 / 40:80 / 60 C-1 ※整理）。

    ファクターは【強制】都市に暗躍≥2 の間、KPの「死亡→敗北+即ループ終了」を得る＝その死は敗北。
    火が点く条件：都市暗躍が2に近い（獲得中/寸前）＋ファクター疑いが**同エリアのキラーで殺せる**。
    折り手：(a)都市に暗躍禁止＝今ターンのKP能力『獲得』を止める（都市<2 に保つ）／
            (b)ファクターをキラーから引き離す・キラーを移動禁止で固定（KP同様の位置防御）。
    ★都市暗躍の除去（神格の暗躍除去等）で状態解除できる＝§排除ツール（表示はnote）。
    ★kp.factor ノードの検出器（DP-1 Stage2・_PENDING_DETECTOR 解消）。
    """
    out = []
    factors = _suspects(roles, "ファクター")
    if not factors:
        return out
    toshi = view.get("board_anyaku", {}).get("都市", 0)
    if toshi < 1:                       # 2まで遠い＝ノイズ抑制（kp_killer の暗躍<1 と同基準）
        return out
    killers = _suspects(roles, "キラー")
    acquire_p = 1.0 if toshi >= 2 else 0.5   # 既に獲得(≥2) or あと1(=1)
    for fa, pfa in factors.items():
        fc = _char(view, fa)
        if not fc or not fc.get("alive", True) or fc.get("area") is None:
            continue
        area = fc["area"]
        near = {k: pk for k, pk in killers.items()
                if k != fa and _alive(view, k)
                and (_char(view, k) or {}).get("area") == area}
        if not near:
            continue                    # 同エリアに殺し手なし＝この死亡経路は火が点かない
        prob = pfa * acquire_p * max(near.values())
        if mm_chars & ({fa} | set(near)):   # mmが関係者に伏せた＝仕込み濃厚
            prob = min(0.95, prob * 1.4)
        c_acq = Condition("都市に暗躍2＝ファクターがKPの死亡→ループ終了効果を獲得")
        if toshi < 2:
            # ★DP-4：暗躍禁止で止まらない供給（クロマク/不穏な噂/黒猫＝脚本家能力フェイズ）を
            #   考慮せず無条件に折り手にしていた＝過大主張。共通ヘルパへ委譲して供給種別を見る。
            _add_anyaku_supply_breaks(c_acq, view, roles, opts, None, "都市",
                                      supply_rumor, rumor_left=rumor_left)
        c_kill = Condition("KP能力獲得ファクターがキラーに殺される（同エリア）")
        # ★B-165（2026-08-05）：ファクターがお嬢様/大物（＋友好4の追加対象）で、
        #   **同エリアのキラー疑いが従者**なら、ファクターを動かしても従者が追随する
        #   （`rules/30_characters.md:65`）＝同エリアは崩れない＝この移動は折り手にならない。
        #   B-153b が `_sk_pair_threats` に入れたのと**同じ述語・同じ機序**（§B-165 doc §4-1）。
        #   ★Phase 2b（FableA 差し戻し・2026-08-05）＝**消すだけでは c_kill が折れなくなる**ので、
        #     追随が発火しない側の有効な手＝**従者（キラー疑い）を単独で動かす**を足す
        #     （`rules/30:65` の追随は「主が移動する場合」だけ＝従者単独の移動は成立する）。
        #     ★足すのは従者が同エリアの**唯一の**キラー疑いのときだけ（狭い述語）。
        _b165_drag = (B165_PAIR_BREAK and "従者" in near and juusha_drags(view, fa))
        if not _b165_drag:
            for mc in opts.move_cards_for(fa):
                if not _noop_char(view, mc, fa, roles):
                    c_kill.breaks.append(Break(f"{fa}をキラーから移動で引き離す",
                                               mc, fa, "character", 1.3, robust=False))
        elif set(near) == {"従者"}:
            _bad_for_ju = frozenset(
                a for f2 in factors
                if f2 != fa and _alive(view, f2)
                and (a := (_char(view, f2) or {}).get("area")))
            c_kill.breaks.extend(_relocate_breaks(
                opts, "従者", area,
                "★{n}（キラー疑い）を{a}から{how}で動かす（ファクターを動かすと追随＝30:65）",
                B165_JUUSHA_MOVE_COST, view=view, roles=roles,
                avoid_areas=_bad_for_ju))
        # ★DP-4：『キラーを移動禁止で固定』は**この条件を折らない**（→ _threat_kp_killer の
        #   同型と同じ機序＝§2 過大主張A）。ファクターとキラーは既に同エリア＝ピンは同居を
        #   維持するだけで、引き離しにはならない。折り手から外す。
        out.append(Threat("factor_kp", f"KP能力獲得ファクター{fa}＝死亡でループ終了",
                          prob, True, "ターン終了フェイズ", [c_acq, c_kill],
                          due_day=view.get("day")))
    return out


def _threat_kp_killer(view, roles, opts, supply_rumor,
                      mm_chars: set = frozenset(),
                      rumor_left=None) -> list[Threat]:
    """キラーによるKP殺害：KP容疑者と同エリアにキラー容疑者・KP暗躍が2に届く。
    mm が今ターン キラー/KP に札を伏せていたら実在度を昇格（仕込みの徴候）。"""
    out = []
    kps = _suspects(roles, "キーパーソン")
    killers = _suspects(roles, "キラー")
    for kp, pkp in kps.items():
        kc = _char(view, kp)
        if not kc or not kc.get("alive", True) or kc.get("area") is None:
            continue
        area = kc["area"]
        # 同エリアの生存キラー容疑者
        near = {k: pk for k, pk in killers.items()
                if k != kp and _alive(view, k)
                and (_char(view, k) or {}).get("area") == area}
        if not near:
            continue
        pk_max = max(near.values())
        prob = pkp * pk_max
        if mm_chars & ({kp} | set(near)):   # mmが関係者に伏せた＝仕込み濃厚
            prob = min(0.95, prob * 1.4)
        # 条件1：キラーとKPが同エリア（引き離す or キラーを固定）
        c1 = Condition("キラーとKPが同エリア")
        # ★B-165（2026-08-05）：KP がお嬢様/大物（＋友好4の追加対象）で、**同エリアのキラー疑いが
        #   従者**なら、KP を動かしても従者が追随する（`rules/30_characters.md:65`）＝
        #   「キラーとKPが同エリア」は崩れない＝**この条件の唯一の折り手が空振り**になる。
        #   ★c1 には KP 側の移動しか無い（DP-4 が『キラーを移動禁止で固定』を消したため）＝
        #     **消すだけだと c1 が丸ごと折れなくなる**（c2 は KP暗躍が既に2以上なら折れない
        #     ＝`Threat.defendable` が False へ落ちうる。★2026-08-05 FableA の差し戻しで現物確認）。
        #   ★★そこで**有効な折り手を足す**（Phase 2b）＝`rules/30_characters.md:65` の追随は
        #     「同一エリアのお嬢様か大物が**移動する場合**」＝**主が動かなければ発火しない**
        #     （`sim/effects.apply_juusha_follow` は主が実際に動いた時だけ従者の行き先を上書き）
        #     ∴ **従者（キラー疑い）を単独で動かせば同エリアは崩れる**＝これは有効な手。
        #     ＝B-153b が `_sk_pair_threats` で保った非対称（主を消し・従者側は残す）と同じ形。
        #   ★足すのは **従者が同エリアの唯一のキラー疑いのとき**だけ（他にキラー疑いが残るなら
        #     従者を動かしても同エリアは崩れない＝それこそ過大主張＝規約 §12 の狭い述語）。
        _b165_drag = (B165_PAIR_BREAK and "従者" in near and juusha_drags(view, kp))
        if not _b165_drag and not _cannot_move_now(view, kp):   # B-21b：動けないKPは引き離せない
            for mc in opts.move_cards_for(kp):
                if _noop_char(view, mc, kp, roles):
                    continue    # B-31/G4/2c：空振り or 危険な退避先（致死zone/送り込み）
                c1.breaks.append(Break(f"{kp}を{area}から移動で引き離す", mc, kp,
                                       "character", 1.3, robust=False))  # 追撃されうる
        elif _b165_drag and set(near) == {"従者"}:
            # ★退避先の安全＝別のKP容疑者が居るエリアへ送り込むと同じ負け筋を作り直す
            #   （DP-4 の過大主張C と同型）。`_relocate_breaks` に view/roles も渡して
            #   B-31/G4/2c（空振り札・危険な退避先）も従来どおり通す。
            _bad_for_ju = frozenset(
                a for k in kps
                if k != kp and _alive(view, k)
                and (a := (_char(view, k) or {}).get("area")))
            c1.breaks.extend(_relocate_breaks(
                opts, "従者", area,
                "★{n}（キラー疑い）を{a}から{how}で動かす（KPを動かすと追随＝30:65）",
                B165_JUUSHA_MOVE_COST, view=view, roles=roles,
                avoid_areas=_bad_for_ju))
        # ★DP-4（2026-07-29）＝**過大主張A**：従来ここに『キラーを移動禁止で固定（追撃を封じる）』
        #   を条件1の折り手として置いていた（B-26 で mm札ゲートは付いた）。しかし**この条件は
        #   「キラーとKPが同エリア」＝いま既に真**であり、ピンは相手をその場に留める手＝条件を
        #   偽にする働きが**構造的に無い**。実測（§1 再現2）：脚本家がキラーに移動札を伏せた局面で
        #     ピン無し → キラーは神社へ移動＝同エリア解消／ピン有り → 打ち消されて残留＝**同エリア維持**
        #   ＝折り手どころか脅威を固定する手だった。しかもコスト1.1＜引き離し1.3のため
        #   cheapest_breaks は**常にピンを選ぶ**＝この負け筋は「毎ターン被覆済み」と誤認され続けた。
        #   ※ピンが常に無意味という意味ではない＝「KPを引き離す**＋**キラーをピン」の2枚なら追撃を
        #     断てる。ただしそれは OR の折り手ではなく AND のコンボで、条件ごとに1枚しか採らない
        #     現行プランナーでは表現できない（コンボの表現＝別チケット・§7）。
        # 条件2：KP暗躍が2に届く（暗躍禁止／クロマク剥がし／KP退避で供給を断つ）
        c2 = Condition("KPの暗躍が2に届く")
        cur = kc.get("anyaku", 0)
        if cur < 2:
            _add_anyaku_supply_breaks(c2, view, roles, opts, kp, area,
                                      supply_rumor, rumor_left=rumor_left)
        note = "KP暗躍が既に2以上＝供給側は折れない。引き離しで防ぐ" if cur >= 2 else ""
        out.append(Threat("kp_killer", f"キラーによる{kp}殺害（{area}）", prob,
                          True, "ターン終了フェイズ", [c1, c2], note,
                          due_day=view.get("day")))
    return out


# ---------------------------------------------------------------------------
# ★B-153b（2026-08-05・**本レーンの検死で見つかった過大主張**）：
#   「A と B を**引き離す**」条件の折り手として `A を移動させる` を出すとき、
#   **A がお嬢様/大物（＋友好4の追加対象）で B が同エリアの従者**なら、
#   `rules/30_characters.md:65`「同一エリアのお嬢様か大物が**移動する場合、
#   （従者は）自身への移動を無視して一緒に移動する**」により**2人は一緒に動く**
#   ＝**引き離せない**（DP-4 が消したのと同じ「条件を偽にできない手」＝過大主張）。
#   ★実測（`docs/監査_B153_従者と自殺_2026-08-05.md` §5-2）＝`random_FS` s4 5日級 L5D1 で
#     `kp_sk|SKによる従者（KP）殺害（2人きり・学校）` severity **1.000** に対し
#     p3 が `移動←→→従者`・p1 が `移動←→→お嬢様` を**同時に**打ち、
#     従者は自身への移動を無視してお嬢様に追随＝**2人とも都市へ移動して同室のまま**
#     ＝2枚使って引き離しに失敗した。
#   ★これは **KB が可否を一意に決める量**（規約 §7 判例1）だが、**免除は自称しない**
#     （裁定は FableA）。★**免除を持ち出す必要が無い**＝主人公側の**通常ゲート**を素通しした：
#     3perm×2ベンチで**防衛数の非退行**（3日級 129=129=129／5日級 61→62・55→56・64→65）・
#     **per-game 退行ゼロ**・flip は改善1（`random_FS` s4）のみ。
#   ★**2026-08-05 FableA 裁定＝既定 True へ land**（B-153・バックログ §29）。
#     3日級は `juusha_drags` の判定が **0回**＝非退行が構造的に保証。
#     5日級は判定6回すべてで折り手を消した＝**空振り率100%の折り手6本**を落としただけ。
#     `B153_JUUSHA_PAIR_BREAK=False` で **land 前の挙動へ bit 復帰**（ablation・変異テスト用）。
B153_JUUSHA_PAIR_BREAK: bool = True

# ---------------------------------------------------------------------------
# ★B-165（2026-08-05・バックログ §41）＝**同型の過大主張の横断点検**の Phase 2。
#   B-153b は `_sk_pair_threats`（2人きり族）**だけ**を直した。全 Break 生成箇所（26箇所）を
#   走査した結果、**同型が残っているのは2箇所だけ**だった（`docs/監査_B165_*` §4）：
#     - `_threat_kp_killer` の条件1「キラーとKPが同エリア」＝折り手は **KP側の移動のみ**
#     - `_threat_factor_kp` の c_kill「ファクターがキラーに殺される（同エリア）」＝同上
#   どちらも **KP/ファクターがお嬢様・大物で、同エリアのキラー疑いが従者**のとき、
#   `rules/30_characters.md:65`（追随）により**引き離せない**＝条件を偽にできない手。
#   ★合成 fixture で現に生成されることを確認済み（doc §4-2＝sev 0.810 の脅威で唯一の折り手）。
#   ★★**ただし両ベンチのコーパスでは発火が 0 回**（3日級 0／5日級 0＝doc §3）＝
#     **land しても挙動 bit は動かない**＝強さの改善ではなく**規則上の保険**。
#     ∴ レーンは既定 **False** で提出した。
#   ★★**2026-08-05 FableA 裁定＝既定 True へ land**（Phase 2b 後）。理由3つ：
#     (1) **現状のコードは `rules/30:65` が偽と定める主張を既定で持っている**（KB との食い違い）。
#     (2) **両ベンチで発火 0**（builder 呼び出し 3039／3411 回に対し**消した0本・足した0本**）
#         ＝**挙動 bit 不変**＝退行しえない。
#     (3) ★**Phase 2b で両肺になった**＝消すだけでなく**有効な折り手（従者を単独で動かす）を足した**
#         ＝`Threat.defendable` が保たれる（B-148/B-160 型の「防御を丸ごと撤去する」退行が起きない）。
#         ★**初版（削除のみ）では `defendable` が False へ落ちていた**＝FableA の差し戻しで現物確認。
#     ★**ベンチのコーパスは薄い**（従者は `random_FS`/`random_BTX` にしか出ず、本件の組み合わせは
#       両ベンチ合わせて実質1〜2局）＝**測れないことは「起きない」ことではない**。
#       ユーザーの実戦はコーパス外の脚本で行われる（B-153 の唯一の改善はそこから出た）。
#   `B165_PAIR_BREAK=False` で land 前の挙動へ **bit 復帰**（ablation・変異テスト用）。
#   ★FableA の起票文の訂正＝「`_threat_kp_killer` の条件1 は DP-4 が相方側を既に消しているから
#     該当しない」は**論理が逆**。相方（キラー）側を動かす折り手が**元から無い**からこそ、
#     残った唯一の手（KP側の移動）が追随で空振りになる＝**該当する**（doc §4-1）。
#   ★★Phase 2b（2026-08-05・**FableA の差し戻しで是正**）＝**削除だけでは片肺**だった。
#     `_threat_kp_killer` の c1 は KP 側の移動**ただ1本**なので、それを消すと条件が丸ごと折れず、
#     KP 暗躍が既に2以上（＝c2 も折れない）の局面で **`Threat.defendable` が False へ落ちる**
#     （FableA が現物で確認＝「c2 が残るので defendable は True」という land 裁定は**誤り**だった）。
#     ∴ **有効な折り手を足す**：`rules/30_characters.md:65` の追随は
#     「同一エリアのお嬢様か大物が**移動する場合**」＝**主が動かなければ発火しない**
#     ∴ **従者（キラー疑い）を単独で動かす**手は規則上成立する＝これを Break にする。
#     ★足すのは**従者が同エリアの唯一のキラー疑いのとき**だけ（他にキラー疑いが残るなら
#       従者を動かしても同エリアは崩れない＝新たな過大主張になる）。
#     ★退避先の安全（`_relocate_breaks` の `avoid_areas` ＋ `_noop_char` の B-31/G4/2c）を通す。
B165_PAIR_BREAK: bool = True
#: ★従者（キラー疑い）を単独で動かす Break のコスト（KP 引き離し 1.3 が比較対象・B-153 と同値）。
B165_JUUSHA_MOVE_COST: float = 1.3


def juusha_drags(view: dict, name: str) -> bool:
    """`name` を動かしても**従者は付いてくる**か（`rules/30_characters.md:65` 追随）。

    True＝「`name` を動かして従者から引き離す」は**規則上成立しない**。
    ★従者が死亡/未登場、`name` が特性対象でない、別エリア＝False（従来どおり）。
    """
    if name == "従者":
        return False
    ju = _char(view, "従者")
    if not (ju and ju.get("alive", True) and ju.get("area")):
        return False
    if name not in juusha_targets_from_view(view):
        return False
    c = _char(view, name)
    return bool(c and c.get("alive", True) and c.get("area") == ju.get("area"))


def _sk_pair_threats(view, roles, opts, sks, *, kind: str,
                     label_word: str, suspect_word: str,
                     mm_chars: set = frozenset()) -> list[Threat]:
    """★VIP（KP∪フレンド）がSK系キャラと『2人きり』（同エリアに2体のみ）で殺される脅威。
    SKは2人きりの相手を殺す＝フレンドが相手でもループ敗北（#6・フレンド殺害を拾う）。

    sks={name: そのキャラがアクティブSKである実在度}＝本物のSK容疑と、妄想拡大ウイルスで
    SK化したパーソン（§C-1）の両方をこの共通コアで扱う。label_word/suspect_word は表示名。"""
    out = []
    if not sks:
        return out
    vips = _vip_suspects(view, roles)   # KP∪フレンド容疑者
    if not vips:
        return out
    by_area: dict = {}
    for c in view.get("characters", []):
        if c.get("alive", True) and c.get("area") in _AREAS:
            by_area.setdefault(c["area"], []).append(c["name"])
    for vip, pv in vips.items():
        vc = _char(view, vip)
        if not vc or not vc.get("alive", True):
            continue
        area = vc.get("area")
        occupants = by_area.get(area, [])
        sk_here = {s: ps for s, ps in sks.items()
                   if s != vip and _alive(view, s)
                   and (_char(view, s) or {}).get("area") == area}
        if not sk_here or len(occupants) > 3:
            continue
        prob = pv * max(sk_here.values())
        c1 = Condition("VIPとSKが同エリアで2人きり")
        # ★DP-4：退避先が「別のSK疑いと2人きり」になる移動は折り手にしない（同じ負け筋を
        #   自分の手で作り直すだけ＝過大主張C）。avoid＝自分の札だけで解決した行き先の禁止集合。
        _bad_for_vip = frozenset(
            a for a in _AREAS
            if any(_alone_with(view, vip, a, s) for s in sks if _alive(view, s)))
        # VIPを移動で逃がす（A.I.等の実質移動不可キャラは提示しない）
        # ★B-153b：VIP がお嬢様/大物で相方が同エリアの従者なら、VIP を動かしても
        #   従者が追随する＝2人きりは崩れない（`rules/30:65`）＝折り手にしない。
        if not (B153_JUUSHA_PAIR_BREAK and "従者" in sk_here
                and juusha_drags(view, vip)):
            c1.breaks.extend(_relocate_breaks(opts, vip, area, "{n}を{a}から逃がす", 1.2,
                                              view=view, roles=roles,
                                              avoid_areas=_bad_for_vip))
        for sk in sk_here:                     # SK本体を移動で剥がす
            # ★B-153b：SK 側がお嬢様/大物で VIP が同エリアの従者なら、SK を動かしても
            #   従者が追随する＝同じく折り手にならない（`rules/30:65`）。
            if (B153_JUUSHA_PAIR_BREAK and vip == "従者"
                    and juusha_drags(view, sk)):
                continue
            _bad_for_sk = frozenset(
                a for a in _AREAS
                if any(_alone_with(view, sk, a, v) for v in vips if _alive(view, v)))
            c1.breaks.extend(_relocate_breaks(
                opts, sk, area, "{n}(" + suspect_word + ")を{a}から動かす", 1.3,
                view=view, roles=roles, avoid_areas=_bad_for_sk))
        for other in occupants:                # 第三者を留めて2人きりを崩す
            # ★B-26：mmがその第三者に札を伏せていなければ引き抜き移動そのものが無い＝
            #   留めるピンは証明可能な空振り（mmのセット位置は公開情報）。
            # ★DP-4（過大主張B）：第三者を留めても、脚本家が**VIPかSKの側を別エリアへ動かして
            #   そこで2人きりを作れる**なら条件は崩れない。伏せ札の位置（公開情報）から
            #   「2人きりを作れるか」を全数で見て、作れないときだけ折り手にする。
            if other not in (vip,) and other not in sk_here \
                    and other in mm_chars \
                    and opts.has_char("移動禁止", other) \
                    and not _cannot_move_now(view, other):   # B-21：動けない駒の留めは無意味
                if any(_pair_possible(view, _mm_movers(view) - {other}, vip, s)
                       for s in sk_here):
                    continue      # ピンしても別ルートで2人きりが作れる＝折れていない
                c1.breaks.append(Break(f"{other}を移動禁止で{area}に留め2人きりを崩す",
                                       "移動禁止", other, "character", 1.1, robust=True))
        role = "KP" if vip in _suspects(roles, "キーパーソン") else "フレンド"
        out.append(Threat(kind, f"{label_word}による{vip}（{role}）殺害（2人きり・{area}）",
                          prob, True, "ターン終了フェイズ", [c1],
                          due_day=view.get("day")))
    return out


def _threat_kp_sk(view, roles, opts, mm_chars: set = frozenset()) -> list[Threat]:
    """シリアルキラー（配役）：VIPがSK容疑者と2人きり。"""
    return _sk_pair_threats(view, roles, opts, _suspects(roles, "シリアルキラー"),
                            kind="kp_sk", label_word="SK", suspect_word="SK疑い",
                            mm_chars=mm_chars)


def _threat_virus_sk(view, roles, opts, virus_p: float,
                     display_all: bool = False,
                     mm_chars: set = frozenset()) -> list[Threat]:
    """§C-1 妄想拡大ウイルス：SK役が脚本に無くても、不安≥3のパーソンがSK化して2人きりの
    相手を殺す（ヒステリシス：≤1で解除・2は維持＝`sim/effects._update_virus_serial`）。
    「潜む殺人鬼が無い＝SK不在」と決めつけない（60 C-3）。不安を注がれたパーソンは全員SK候補。

    virus_p＝ルールX『妄想拡大ウイルス』が実在する確率（rule_marginals から）。0なら不発。"""
    if virus_p < 0.05 and not display_all:
        return []
    sks: dict = {}
    for c in view.get("characters", []):
        if not c.get("alive", True):
            continue
        name = c.get("name")
        pperson = (roles.get(name, {}) or {}).get("パーソン", 0.0)
        if pperson < _SUSPECT_P:
            continue
        u = c.get("unrest", 0)
        if u >= 3:
            sks[name] = virus_p * pperson * 0.9   # 既にSK化域（不安≥3）
        elif u >= 2:
            sks[name] = virus_p * pperson * 0.4   # あと1点でSK化（ヒステリシス維持域）
    return _sk_pair_threats(view, roles, opts, sks, kind="virus_sk",
                            label_word="ウイルスSK化パーソン", suspect_word="ウイルスSK化疑い",
                            mm_chars=mm_chars)


def _threat_sk_setup(view, roles, opts, mm_chars: set) -> list[Threat]:
    """★SK2人きりの仕込み検出（バグ6・2026-07-09）：SKがKPと別エリアでも、mmが今ターン
    「KP・SK・KPの同室者」に札を伏せた＝移動でKPの部屋を空ける/SKを送り込む多手仕込みの
    徴候。位置防御（移動禁止）で先回りして固める。

    実例（ユーザーログ FS seed0）：KP=A.I.(都市)・SK=幻想(神社) 100%確定で、mmが都市の
    同室者2人（情報屋/アイドル）に伏せ札→従来は同エリア要件で脅威化されず放置していた。
    """
    out = []
    # 確定級（≥_LIKELY_P）のみ＝薄い疑いで毎ターン鳴らさない（ノイズ抑制）
    kps = _suspects(roles, "キーパーソン", _LIKELY_P)
    sks = _suspects(roles, "シリアルキラー", _LIKELY_P)
    if not kps or not sks or not mm_chars:
        return out
    for kp, pkp in kps.items():
        kc = _char(view, kp)
        if not kc or not kc.get("alive", True) or kc.get("area") is None:
            continue
        kp_area = kc["area"]
        kp_mates = {c["name"] for c in view.get("characters", [])
                    if c.get("alive", True) and c.get("area") == kp_area
                    and c["name"] != kp}
        for sk, psk in sks.items():
            if sk == kp or not _alive(view, sk):
                continue
            sk_area = (_char(view, sk) or {}).get("area")
            if sk_area == kp_area:
                continue  # 同エリアは既存 _threat_kp_sk が扱う（二重報知を避ける）
            # ★両側の同室者が関係者：SKをKPの部屋へ送り込む型（KP同室者を退室）と、
            #   KPをSKの部屋へ送り込む型（SK同室者を退室）の両方がある（実ログ両型確認）。
            sk_mates = {c["name"] for c in view.get("characters", [])
                        if c.get("alive", True) and c.get("area") == sk_area
                        and c["name"] not in (sk, kp)}
            sk_t, kp_t = sk in mm_chars, kp in mm_chars
            touched = ({kp, sk} | kp_mates | sk_mates) & mm_chars
            if not touched:
                continue
            c1 = Condition("mmの伏せ札でKPがSKと2人きりにされうる（位置の仕込み）")
            # ★折り手＝「mmが実際に触った駒」のピンに限定（触っていない駒のピンは
            #   mmの移動を打ち消せない＝空振り。実ログ検死 2026-07-09）：
            # ★DP-4（2026-07-29）＝**過大主張B（本チケットの起点）**：ピンは「その駒が動かない」
            #   ことしか保証しない。**相手側の駒を動かして 2人きり を作る**経路が残っていれば
            #   条件は崩れていない。実測（§1 再現1）：KP(病院・単独)／SK(神社・単独)で mm が
            #   両方に伏せた局面に『KPへ移動禁止』を打ったところ、mm は SK を病院へ動かして
            #   2人きりを完成させた（engine 実測で確認）。にもかかわらずプランナーは
            #   covered=True としていた＝B-100 はこの被覆を信じて席を他へ回す。
            #   → `_pair_possible`（伏せ札の位置＝公開情報から、残った駒だけで2人きりを
            #     作れるかを全エリア全数で判定）が False のときだけ折り手にする。
            if sk_t and opts.has_char("移動禁止", sk) and not _cannot_move_now(view, sk) \
                    and not _pair_possible(view, _mm_movers(view) - {sk}, kp, sk):
                c1.breaks.append(Break(f"{sk}を移動禁止（SKへの伏せ札の移動を打ち消す）",
                                       "移動禁止", sk, "character", 1.0, robust=True))
            if kp_t and opts.has_char("移動禁止", kp) and not _cannot_move_now(view, kp) \
                    and not _pair_possible(view, _mm_movers(view) - {kp}, kp, sk):
                c1.breaks.append(Break(f"{kp}を移動禁止（KPへの伏せ札の移動を打ち消す）",
                                       "移動禁止", kp, "character", 1.0, robust=True))
            # ★移動禁止を持っていなくても、移動で自ら引き離す/送り返す（#5・移動でがんばる）。
            #   ★B-21b：動けない駒（恒久移動不能・当ループ未解除）は移動で崩せない＝出さない。
            #   ★DP-4＝**過大主張C**：KPとSKは**別エリア**なので、自分の移動札の向きによっては
            #     KPを**SKのエリアへ送り込む**（＝2人きりを自分で作る）。2×2盤では ←→/↑↓ の
            #     どちらかは必ず相手側の列/行を向く＝方角を見ないと自滅手になる。実測（§1 再現4）：
            #     KP=病院・SK=神社で『KPに移動←→』が「2人きりを崩す」折り手として登録され、
            #     engine 実測では KP が神社（SKの居場所）へ移動した。
            _sk_area = sk_area
            _kp_area = kp_area
            if not _cannot_move_now(view, kp):
                for mc in opts.move_cards_for(kp):
                    if _noop_char(view, mc, kp, roles):
                        continue    # B-31/G4/2c：空振り/危険
                    if _own_move_dest(view, kp, mc) == _sk_area:
                        continue    # DP-4：SKのエリアへ送り込む＝2人きりを自分で作る手
                    c1.breaks.append(Break(f"{kp}を移動で{sk}から離す（2人きりを崩す）",
                                           mc, kp, "character", 1.3, robust=False))
            if not _cannot_move_now(view, sk):
                for mc in opts.move_cards_for(sk):
                    if _noop_char(view, mc, sk, roles):
                        continue    # B-31/G4/2c：空振り/危険
                    if _own_move_dest(view, sk, mc) == _kp_area:
                        continue    # DP-4：SKをKPのエリアへ送り込む＝2人きりを自分で作る手
                    c1.breaks.append(Break(f"{sk}(SK疑い)を移動で動かす（2人きりを崩す）",
                                           mc, sk, "character", 1.4, robust=False))
            for m in sorted(touched & (kp_mates | sk_mates)):
                # ★DP-4：第三者ピンも同じ＝留めても別ルートで2人きりが作れるなら折れていない。
                if opts.has_char("移動禁止", m) and not _cannot_move_now(view, m) \
                        and not _pair_possible(view, _mm_movers(view) - {m}, kp, sk):
                    c1.breaks.append(Break(
                        f"{m}を移動禁止で留める（2人きりを崩す第三者）",
                        "移動禁止", m, "character", 1.1, robust=True))
            if sk_t or kp_t:
                # 今ターン成立しうる：触れた枚数が多いほど濃厚（1枚0.55・2枚0.75・3枚0.9）
                prob = pkp * psk * min(0.9, 0.35 + 0.2 * len(touched))
                # ★即成立型：KPが既に1人きり＋SKに伏せ札／SKの部屋がSKだけ＋KPに伏せ札
                #   （1手で2人きり完成）は1枚でも濃厚。
                if (sk_t and not kp_mates) or (kp_t and not sk_mates):
                    prob = max(prob, pkp * psk * 0.85)
            else:
                # SKもKPも動かない＝今ターンは死なないが、同室者の退室＝多手仕込みの前段。
                # 低確度（cap 0.5＝ホット加点の対象外）で警告し、同室者ピンを提示する。
                prob = pkp * psk * min(0.5, 0.2 + 0.15 * len(touched))
            out.append(Threat(
                "sk_setup",
                f"SK{sk}による{kp}の2人きり仕込み（mmが関係者{len(touched)}枚に伏せ札）",
                prob, True, "ターン終了フェイズ", [c1],
                note="mmの今ターン配置が脅威の関係者と一致",
                due_day=view.get("day")))
    return out


# 事件→打点が乗る敗北ボード（暗躍禁止/移動で止まらない＝犯人冷却が唯一の折り手・B-10）。
_INCIDENT_BOARD_DAMAGE = {"邪気の汚染": "神社"}   # 封印×邪気＝神社+2（負け筋防御ツリー §C-3）

# ---------------------------------------------------------------------------
# ★B-159（2026-08-04）：事件「行方不明」の**板への暗躍供給**を board_defeat の折り手へ。
#   KB＝`rules/40_first_steps.md:153`／`rules/50_basic_tragedy_x.md:211-212`
#     「犯人を任意のボードに移動させる。その後、**犯人のいるボードに暗躍カウンターを1つ置く**」
#   ＝**板の敗北条件（ループ終了時に暗躍2以上）への直通の供給線**。
#   ★E-2（公式裁定・`rules/40:160`）＝移動先に犯人の**禁止エリアは選べない**
#     ＝供給先は「犯人が行けるボード」に限られる。判定は既存の単一ソース
#     `sim.state.missing_incident_boards_from_view`（A-78 が使っているもの）を**再利用**する
#     （二重実装しない）。
#   ★折り手は**犯人冷却のみ**＝`rules/10_action_cards.md:65`「暗躍禁止は**行動解決フェイズ
#     でのみ**有効」／事件はフェイズ7（`rules/00_rules_core.md`）＝**事件効果の暗躍は
#     暗躍禁止では止まらない**（クロマク・不穏な噂と同じ理屈＝`rules/40:62,88`）。
#   ★述語は最狭＝**その板の暗躍が既にちょうど1**の時だけ（1つ足されると2＝敗北条件成立）。
#   既定 False＝**挙動 bit 不変**（この分岐に入らない）。
B159_MISSING_BOARD: bool = False
#: 行方不明の犯人冷却 Break のコスト（掃引で決める。邪気の汚染の 1.5 が比較対象）。
B159_MISSING_COST: float = 1.5

# ---------------------------------------------------------------------------
# ★B-161（2026-08-04）：`board_defeat` の犯人冷却が**決定へ届かない**2段の門のうち、
#   **(β)＝折り手は最安が採られる**。
#   - `plan_defenses` は条件ごとに `Threat.cheapest_breaks`（＝`min(cost)`）を1本だけ採る
#     （`agents/defense_plan.py:107-113,2103`）。
#   - `agents/b100_alloc.py:230` の `here.sort(key=(cost, card, target))` も同じ順序。
#   ∴ 冷却 cost 1.5 は**同じ条件に並ぶ**暗躍禁止 1.0（`_add_kinshi_break`）に必ず負け、
#     冷却 Break は `plan.picks` の候補にすら入らない。
#   ★これは**価値・重みの量**であって KB が一意に決める量ではない（規約 §7 判例1）＝
#     rule-rational 免除の対象外＝**通常ゲート**。
#   既定 None＝**従来値をそのまま使う＝挙動 bit 不変**（この行を通らない）。
B161_COOL_COST: float | None = None


def _b159_missing_feeds(view, area: str, iday, culprits) -> bool:
    """`iday` 日の事件「行方不明」が `area` へ暗躍1を置き**うる**か（E-2 を通した到達判定）。

    ★主人公は犯人を知らない＝候補集合の**和**を取る（1人でも行けるなら到達扱い＝安全側）。
    ★候補が空／取得不能＝**絞れない**＝到達扱い（脅威を過小評価しない側）。
    """
    from sim.state import missing_incident_boards_from_view
    cands = [n for n in ((culprits or {}).get(iday) or ()) if _alive(view, n)]
    if not cands:
        return True
    return any(area in missing_incident_boards_from_view(view, n) for n in cands)


def _cooling_breaks(cond: "Condition", view, opts, live_culprits, criticals,
                    label_fmt: str, cost: float) -> bool:
    """『犯人を不安-1で冷やして事件の発生を止める』折り手を cond に足す（足したら True）。

    ★DP-4（2026-07-29）＝**過大主張D（不完全被覆）／E（到達不能）**の是正。従来は
      犯人候補を1人ずつループして全員分の Break を並べていたが、プランナーは条件ごとに
      **最安の1枚しか採らない**（`Threat.cheapest_breaks`）＝候補が複数居るとき
      「1人を冷やした」だけで発生条件が折れたことにしていた。事件の発生判定は
      **真の犯人**の不安だけを見る（KB: 40/50 事件）＝別人を冷やしても発生は止まらない。
      → 生存候補が**ちょうど1人**に絞れているときだけ折り手にする。
    ★到達性：不安-1 は1しか下げない。`unrest - 1 < 臨界` が成り立たない（＝既に臨界を
      2以上超えている）候補を冷やしても発生は止まらない＝折り手にしない。臨界が取得
      できない場合は健全側＝従来どおり折り手にする（過剰に消さない）。
    """
    live = [c for c in (live_culprits or []) if _alive(view, c)]
    if len(live) != 1:
        return False
    cand = live[0]
    if not opts.has_char("不安-1", cand) or _noop_char(view, "不安-1", cand):
        return False           # B-28 G2：床0＋mm札なしは空振り
    th = (criticals or {}).get(cand)
    cc = _char(view, cand)
    if th is not None and cc is not None and cc.get("unrest", 0) > th:
        return False           # 冷却1点では臨界を割れない＝発生は止まらない
    cond.breaks.append(Break(label_fmt.format(c=cand), "不安-1", cand,
                             "character", cost, robust=True))
    return True


def _add_incident_board_cooling(c1: "Condition", view, opts, area,
                                culprits, criticals) -> None:
    """area に事件由来の打点（邪気の汚染=神社+2 等）が乗る予定なら、その犯人候補を不安-1で
    冷やす折り手を c1 に足す（B-10）。事件打点は暗躍禁止/移動で止まらない＝犯人冷却が唯一。"""
    day_now = view.get("day", 1)
    cur = int((view.get("board_anyaku") or {}).get(area, 0) or 0)
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if iday is None or iday < day_now:
            continue
        if _INCIDENT_BOARD_DAMAGE.get(nm) == area:
            label = f"{nm}の犯人候補{{c}}を不安-1で冷やし{area}への事件打点を止める"
            cost = 1.5
        elif (B159_MISSING_BOARD and nm == "行方不明" and cur == 1
              and _b159_missing_feeds(view, area, iday, culprits)):
            # ★B-159：暗躍が既に1＝行方不明の+1で2＝板の敗北条件が成立する（最狭の述語）。
            label = (f"行方不明の犯人候補{{c}}を不安-1で冷やし"
                     f"{area}への暗躍供給（+1で2）を止める")
            cost = B159_MISSING_COST
        else:
            continue
        if B161_COOL_COST is not None:
            cost = B161_COOL_COST      # ★B-161(β)：既定 None＝この行を通らない
        added = _cooling_breaks(
            c1, view, opts, (culprits or {}).get(iday, set()), criticals,
            label, cost)
        if (nm in _INCIDENT_BOARD_DAMAGE and not added
                and not c1.breaks and not c1.note):
            c1.note = (f"{nm}の事件打点（{area}+2）＝暗躍禁止/移動で止まらない・"
                       "犯人冷却が唯一だが今ターン折り手が手札に無い")


def _threat_board_defeat(view, roles, opts, supply_rumor,
                         defeat_board_probs, culprits=None, criticals=None,
                         include_breached=False, display_all=False,
                         rumor_left=None) -> list[Threat]:
    """ボード敗北：**敗北ボードはルール依存**（守るべき場所→学校・封印→神社・ボードX）。
    defeat_board_probs={board: P(その敗北ルールが実在)}。空/確率0のボードは脅威化しない
    （FSで神社を無条件に出す等のバグ修正 2026-07-09）。
    include_breached=True＝既に暗躍≥2で突破済みの板も breached=True 付きで返す（表示用・B-1）。"""
    out = []
    banr = view.get("board_anyaku", {}) or {}
    for area, rule_p in (defeat_board_probs or {}).items():
        if rule_p < 0.05 and not display_all:
            continue  # そのボードを敗北にするルールがほぼ無い＝脅威でない（display_all＝極小でも表示）
        cur = banr.get(area, 0)
        if cur >= 2:
            # 既に敗北判定域＝突破済み。表示用に breached で残す（意思決定側は覆えない）。
            # ★B-170（2026-08-05）＝「覆えない」には**KB上の例外**がある：巫女♡3（神社のみ・
            #   回数無制限）／神格♡5（自ボード・回数無制限）の友好能力は、主人公能力
            #   フェイズ(6)＝ループ終了処理(9) より前に板の暗躍を1つ剥がせる
            #   （`rules/20_goodwill_abilities.md:129`,`:153`／`rules/00_rules_core.md:97-115`）。
            #   ★実測では、この早期 continue が起きた条件（3日級246・5日級597・基点 df454d8）のうち
            #   「浄化係がその板に立っていて友好も足りている」は**両ベンチとも0**、
            #   「今日 友好+1 を1枚置けば今日剥がせる」も 3日級0・5日級10（1局1ループに集中）
            #   ＝射程が薄いので**あえて continue のままにしてある**
            #   （`docs/監査_B170_板の暗躍は剥がせる_2026-08-05.md` §3・§6 の再訪条件）。
            if include_breached:
                out.append(Threat("board_defeat", f"{area}のボード敗北（突破済み・暗躍{cur}）",
                                  rule_p, True, "ループ終了フェイズ",
                                  [Condition(f"{area}の暗躍が既に{cur}（≥2）")],
                                  note="既に敗北判定域＝この板はこのループ突破済み",
                                  breached=True,
                                  due_day=view.get("days_per_loop")))
            continue
        c1 = Condition(f"{area}の暗躍が2に届く")
        # ★DP-6（2026-07-31・B-112 §9 申し送り1）：「真に止まらない供給」を**カウンタ収支**で
        #   判定する（単一ソース＝unstoppable_supply_gap）。
        #   旧実装＝`(supply_rumor and area != "都市") or (黒猫@神社)`＝**噂が場に在るだけで
        #   （現在値を見ずに）板敗北を丸ごと防御不能に落とす過大主張**（B-112 §3 が特定・
        #   標準ベンチで立った負け筋の46〜56%が防御不能扱い・最大種別=board_defeat）。
        #   収支：板敗北の閾値＝暗躍2（rules/40:47 等）／噂＝+1/loop（rules/40:61）／
        #   黒猫＝ループ開始時+1＝既に cur に入っている（rules/30 特性1）。
        #   ∴ **gap = 2 - cur - 噂残弾** が 0 以下のときだけ真に防御不能：
        #   - cur=0＋噂 → gap=1 ＝2本目は行動解決の暗躍カード＝**暗躍禁止で断てる（防御可能）**
        #   - cur≥1＋噂 → gap≤0 ＝噂だけで2に届く＝防御不能（維持）
        # ★B-113（2026-07-31）：噂の残弾（rumor_left）を belief から注入。噂は 1/loop＝
        #   このループで噂由来の+1が既に確定（belief.rumor_spent_this_loop）なら残弾0＝
        #   現在値1の板も gap=1（暗躍禁止で防御可能）に戻る。証明できなければ None＝
        #   未消費と仮定（防御不能側に倒す健全側・DP-6 の既定のまま）。
        gap = unstoppable_supply_gap(view, area, cur, BOARD_DEFEAT_ANYAKU,
                                     supply_rumor=supply_rumor, roles=roles,
                                     rumor_left=rumor_left)
        truly_unstoppable = gap <= 0
        # クロマクは「同エリア/自ボード」にしか置けない＝的エリアに居るクロマク疑いを
        # 移動で剥がせば供給が止まる（#7・負け筋防御ツリー§A「クロマクを的外へ〔難〕」）。
        # ★クロマク＝毎ターン供給できる＝収支 1/loop に収まらない＝gap では扱わず従来どおり
        #   「移動で剥がすレース」（真に止まらない扱いにしない・維持）。
        cultists_here = [n for n in _suspects(roles, "クロマク", _SUSPECT_P)
                         if (_char(view, n) or {}).get("area") == area
                         and _alive(view, n)]
        race = False
        if truly_unstoppable:
            c1.note = ("止まらない供給（不穏な噂1/loop）だけで暗躍2に届く＝"
                       "暗躍禁止でも移動でも止まらない（レース対象）")
        elif cultists_here:
            # クロマクを的エリアから剥がす（幻想＝ボード移動で動かす・難・追撃されうる）。
            for cu in cultists_here:
                c1.breaks.extend(_relocate_breaks(
                    opts, cu, area,
                    "{n}（クロマク疑い）を{a}から{how}で剥がす（供給を断つ）", 1.6,
                    view=view, roles=roles))
            # ★B-1（2026-07-13）：移動可のクロマクが的に居る＝「防御不能」ではなくレース。
            #   今ターン移動札が無くても、移動で剥がせば止まる供給＝別席/別ターンで対処できる
            #   （真の防御不能＝移動不可クロマク・不穏な噂・黒猫と区別する）。
            movable = [cu for cu in cultists_here if not _immobile(cu)]
            if movable:
                race = True
                if not c1.breaks:
                    c1.note = (
                        "クロマク疑いが的エリアに居る＝移動で剥がせば供給が止まる（防御可能）が、"
                        "今ターンはその移動札が手札に無い＝移動札を待つ／別席で対処するレース"
                        "（暗躍禁止ではクロマク能力供給を止められない）")
            elif not c1.breaks:  # 移動不可クロマクだけ＝真に剥がせない
                c1.note = ("移動不可のクロマク疑いが的に張り付き＝移動でも暗躍禁止でも止まらない"
                           "供給（防御不能）")
        else:
            # ★カルティスト無効化を考慮した暗躍禁止Break（B-8）。
            # ★B-184：board_defeat の「今夜打つ前提」＝最終日（発火＝ループ終了フェイズ）
            #   or 板の暗躍≥1（imminent＝今夜1枚で不可逆化しうる夜）。この文脈でだけ
            #   void（mm がその板に今ターン伏せていない）の暗躍禁止を生成しない
            #   （切替口 B184_VOID_AWARE_BREAKS・既定 OFF＝bit 不変）。
            _add_kinshi_break(c1, opts, area, view, roles,
                              f"{area}に暗躍禁止（行動解決の暗躍供給を断つ）",
                              tonight=(view.get("day") == view.get("days_per_loop")
                                       or cur >= 1))
        # ★B-10：事件由来のボード打点（邪気の汚染=神社+2）＝暗躍禁止/移動で止まらない＝犯人冷却を足す
        _add_incident_board_cooling(c1, view, opts, area, culprits, criticals)
        # 実在度＝P(敗北ルール) × ボードの育ち具合。
        # ★DP-6：距離は cur でなく **gap（実効距離）** で測る＝止まらない供給（噂1/loop）が
        #   効く板は cur=0 でも実効距離1（行動解決の暗躍が1つ通れば、あとは噂だけで2に届く）
        #   ＝cur>=1 と同等（B-112 の `B100_REPAIR_PROB` の本体化）。
        #   ablation（DP6_SUPPLY_LEDGER=False）では旧式（cur のみ）を bit 再現する。
        # ★B-134（2026-08-01）：`gap` は「あと何**個**要るか」であって「あと何**枚**要るか」
        #   ではない。`暗躍+2` は **1枚で2個**（`rules/10_action_cards.md:39`）＝
        #   残弾が公開情報から確定できるなら **gap=2 の空の板も「1枚で届く」**＝距離1と同じ。
        #   ＝DP-6 の「行動解決の暗躍が1つ通れば届く＝実効距離1」という枠組みを、
        #     カウンタ個数から**カード枚数**へ揃えるだけ（新しい会計は増やさない）。
        #   安全側（B-113/B-132 の流儀）＝**確定できる時だけ距離を詰める**：
        #     (1) `暗躍+2` の残弾が公開情報から確定できる（`anyaku_card_reach`）
        #     (2) **その板が敗北ボードであることが確定している**（`rule_p ≥ B134_RULEP_CERTAIN`）
        #   どちらかが確定できなければ **reach=1＝従来どおり**（既存の較正を乱さない）。
        if DP6_SUPPLY_LEDGER:
            reach = (anyaku_card_reach(view)
                     if rule_p >= B134_RULEP_CERTAIN else 1)
            progress = 0.9 if gap <= reach else 0.35
        else:
            progress = 0.9 if cur >= 1 else 0.35
        threat_p = rule_p * progress
        out.append(Threat("board_defeat", f"{area}のボード敗北", threat_p,
                          True, "ループ終了フェイズ", [c1], race=race,
                          due_day=view.get("days_per_loop"),
                          imminent=(cur >= 1)))
    return out


def _threat_kp_anyaku(view, roles, opts, supply_rumor, contract_prob,
                      display_all: bool = False, rumor_left=None) -> list[Threat]:
    """僕と契約しようよ！：KP暗躍≥2 単独でループ終了時敗北（キラー不要・位置無関係）。
    ボード敗北と同型の「暗躍≥2の評価敗北」＝暗躍禁止/クロマク剥がし/供給停止でしか止まらない
    （移動でKPを逃がしても暗躍は乗る＝位置防御は無効）。contract_prob＝P(rule=僕と契約)。"""
    if contract_prob < 0.05 and not display_all:
        return []
    out = []
    for kp, pkp in _suspects(roles, "キーパーソン").items():
        # ★B-187：契約のキーパーソンは必ず少女（rules/50:42）＝非少女は候補にしない。
        #   pkp（KP周辺確率）には契約以外のルール組の寄与が混ざるが、
        #   P(契約 ∧ KP=非少女)=0 は KB が一意に決める（既定 OFF＝従来どおり素通し）。
        if B187_CONTRACT_SHOUJO_ONLY and kp not in SHOUJO:
            continue
        kc = _char(view, kp)
        if not kc or not kc.get("alive", True) or kc.get("area") is None:
            continue
        cur = kc.get("anyaku", 0)
        if cur >= 2:
            continue  # 既に敗北域＝供給停止では折れない
        c1 = Condition("KPの暗躍が2に届く（僕と契約）")
        _add_anyaku_supply_breaks(c1, view, roles, opts, kp, kc["area"],
                                  supply_rumor, rumor_left=rumor_left)
        progress = 0.9 if cur >= 1 else 0.35
        out.append(Threat("kp_anyaku", f"僕と契約＝{kp}の暗躍2でループ敗北",
                          contract_prob * pkp * progress, True,
                          "ループ終了フェイズ", [c1],
                          due_day=view.get("days_per_loop")))
    return out


def _vip_suspects(view, roles) -> dict:
    """VIP＝KP容疑者∪フレンド容疑者（生存）。死亡でループ敗北になる守るべき対象。"""
    out = {}
    for role in ("キーパーソン", "フレンド"):
        for n, p in _suspects(roles, role).items():
            if _alive(view, n):
                out[n] = max(out.get(n, 0.0), p)
    return out


def _threat_killer_protagonist(view, roles, opts, supply_rumor,
                              display_all: bool = False,
                              rumor_left=None) -> list[Threat]:
    """キラーによる主人公殺害：キラー自身の暗躍が4に届く（位置非依存・暗躍禁止で断つ）。"""
    out = []
    for k, pk in _suspects(roles, "キラー", 1e-6 if display_all else _SUSPECT_P).items():
        c = _char(view, k)
        if not c or not c.get("alive", True):
            continue
        cur = c.get("anyaku", 0)
        if cur < 2:
            continue  # 4は遠い＝まだ脅威化しない（ノイズ抑制）
        c1 = Condition("キラー自身の暗躍が4に届く")
        # ★暗躍禁止だけでなく「クロマクから逃げる／クロマクを剥がす」も提示（#7・テスター
        #   指摘 2026-07-10：クロマク供給を一律防御不能にするな）。
        _add_anyaku_supply_breaks(c1, view, roles, opts, k, c.get("area"),
                                  supply_rumor, rumor_left=rumor_left)
        prob = pk * (0.7 if cur >= 3 else 0.4)
        out.append(Threat("killer_protagonist", f"キラー{k}の暗躍4＝主人公死亡", prob,
                          True, "ターン終了フェイズ", [c1],
                          due_day=view.get("day")))
    return out


def _threat_mainlover_protagonist(view, roles, opts, supply_rumor,
                                  display_all: bool = False) -> list[Threat]:
    """メインラバーズによる主人公殺害：不安3以上＋暗躍1以上（冷却で不安を落とせば止まる）。"""
    out = []
    for m, pm in _suspects(roles, "メインラバーズ", 1e-6 if display_all else _SUSPECT_P).items():
        c = _char(view, m)
        if not c or not c.get("alive", True):
            continue
        unrest, anr = c.get("unrest", 0), c.get("anyaku", 0)
        if unrest < 2 and anr < 1:
            continue
        c1 = Condition("メインラバーズの不安3以上")
        # ★DP-4（過大主張E＝到達不能）：不安-1 は1点しか下げない＝**不安が4以上**なら
        #   冷やしても3未満にならず、この条件は折れない（従来は不安に関係なく折り手にしていた）。
        if unrest <= 3 and opts.has_char("不安-1", m) and not _noop_char(view, "不安-1", m):
            c1.breaks.append(Break(f"{m}を不安-1で冷やし3未満に保つ",
                                   "不安-1", m, "character", 1.2, robust=True))
        prob = pm * min(1.0, (unrest / 3.0)) * (1.0 if anr >= 1 else 0.4)
        out.append(Threat("mainlover_protagonist", f"メインラバーズ{m}＝主人公死亡", prob,
                          True, "ターン終了フェイズ", [c1],
                          due_day=view.get("day")))
        # ★G6（B-11）：恋愛連鎖の予防。ラバーズ死亡→メインラバーズに不安+6（KB:50）→暗躍≥1で
        #   主人公殺害。+6は大きく冷却(-1)で追いつけない＝現在不安が低くても危険。メインラバーズが
        #   暗躍≥1（＝+6で即臨界域）かつラバーズ疑いが生存している時、予防的に脅威化する。
        #   折り手＝メインラバーズの暗躍を0に保つ（+6でも暗躍0なら殺害不成立）／ラバーズを死の
        #   危険（病院の事件ゾーン等）から守る（ラバーズはVIP集合外＝従来保護されない）。
        lovers = [n for n in _suspects(roles, "ラバーズ") if _alive(view, n)]
        if anr >= 1 and lovers:
            c2 = Condition("ラバーズ死亡→メインラバーズ+6→暗躍≥1で主人公殺害（恋愛連鎖）")
            # ★DP-4（過大主張F＝既に成立している条件は供給停止では折れない）：この脅威は
            #   `anr >= 1`（メインラバーズの暗躍が既に1以上）のときだけ作られる。暗躍禁止や
            #   クロマク剥がしは**これから載る暗躍**しか止められず、既に載っている暗躍は
            #   減らせない（暗躍カウンターを除去する手段は主人公の手札に無い＝KB:00 手札）。
            #   よって供給停止系は「暗躍≥1」を偽にできない＝折り手から外す。
            #   （残るのは『ラバーズを死の危険域から退避させる』＝連鎖の入口を断つ手のみ。）
            has_hosp_inc = any(i.get("name") == "病院の事件"
                               for i in view.get("incidents", []))
            for lv in lovers:                       # ラバーズを死の危険域から退避
                lc = _char(view, lv)
                if lc and lc.get("area") == "病院" and has_hosp_inc:
                    for mc in opts.move_cards_for(lv):
                        if _noop_char(view, mc, lv, roles):
                            continue    # B-31/G4：行き先が禁止＝空振り札
                        c2.breaks.append(Break(
                            f"ラバーズ疑い{lv}を病院から退避（病院の事件死→連鎖を断つ）",
                            mc, lv, "character", 1.6, robust=False))
            _lov_p = _suspects(roles, "ラバーズ")
            pl = max((_lov_p.get(n, 0.0) for n in lovers), default=0.0)
            out.append(Threat("mainlover_chain",
                              f"恋愛連鎖（ラバーズ死亡→メインラバーズ{m}主人公殺害）",
                              pm * pl * 0.4, True, "ターン終了フェイズ", [c2],
                              due_day=view.get("day")))
    return out


def _threat_incident_vip(view, roles, opts, culprits, criticals) -> list[Threat]:
    """事件によるVIP殺害：犯人が不安臨界に届き、その事件がVIP（KP/フレンド）を殺す。

    - 病院の事件＝病院のVIPを殺す → VIPを病院外へ移動（★位置防御）or 犯人を冷却。
    - 殺人事件＝犯人と同エリアのVIPを殺す → VIPをそのエリア外へ移動 or 犯人を冷却。
    発生条件（犯人の不安≥臨界）を冷却で折る／効果（VIPの立ち位置）を移動で折る。
    """
    out = []
    vips = _vip_suspects(view, roles)
    if not vips:
        return out
    day_now = view.get("day", 1)
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if nm not in ("病院の事件", "殺人事件") or iday is None or iday < day_now:
            continue
        cset = (culprits or {}).get(iday) or set()
        # 犯人候補で生存かつ不安が臨界に届きうる者
        live_culprits = [c for c in cset if _alive(view, c)]
        if not live_culprits and cset:
            continue
        # 効果でVIPが死ぬ立ち位置か
        for vip, pv in vips.items():
            vc = _char(view, vip)
            if not vc:
                continue
            varea = vc.get("area")
            in_danger = False
            danger_area = None
            if nm == "病院の事件" and varea == "病院":
                in_danger, danger_area = True, "病院"
            elif nm == "殺人事件":
                # 犯人と同エリアのVIP（犯人未特定なら「VIPと同室に犯人候補が居るか」）
                for cand in (live_culprits or []):
                    cc = _char(view, cand)
                    if cc and cc.get("area") == varea and cand != vip:
                        in_danger, danger_area = True, varea
                        break
            if not in_danger:
                continue
            # 発生確率＝犯人の臨界到達の近さ（不安0で臨界が遠いなら低い＝ノイズ抑制）。
            occ = 0.12  # 既定：犯人候補が皆冷たい＝発生は遠い
            for cand in (live_culprits or [vip]):
                th = criticals.get(cand)
                cc = _char(view, cand)
                if th is not None and cc is not None:
                    u = cc.get("unrest", 0)
                    if u >= th:
                        occ = max(occ, 0.9)      # 既に臨界＝ほぼ発生
                    elif th <= 1:
                        occ = max(occ, 0.6)      # 臨界≤1＝低い不安で発生しうる
                    elif u >= th - 1:
                        occ = max(occ, 0.45)     # 臨界間際
            # 切迫度：事件が数日先なら冷却/退避の猶予がある＝脅威を割り引く
            days_away = (iday or day_now) - day_now
            if days_away >= 2:
                occ *= 0.5
            elif days_away == 1:
                occ *= 0.8
            # 犯人不確定の割引：殺人事件は「犯人と同室」が要件＝候補が多いほど当該室の
            # 犯人である確率は下がる（VIPと同室の候補数 / 生存候補総数）。
            if nm == "殺人事件" and live_culprits:
                here = sum(1 for c in live_culprits
                           if (_char(view, c) or {}).get("area") == varea)
                if here:
                    occ *= here / len(live_culprits)
            prob = pv * occ
            c_eff = Condition(f"VIP{vip}が{danger_area}に居る（効果で死ぬ立ち位置）")
            # ★DP-4（過大主張C）：殺人事件は「犯人と同エリアのVIPが死ぬ」＝**別の犯人候補が
            #   居るエリアへ退避しても死ぬ**。自分の札だけで解決した行き先がそういうエリアなら
            #   折り手にしない（病院の事件は病院を出れば良い＝的は病院のみ）。
            _bad = frozenset({danger_area}) if nm == "病院の事件" else frozenset(
                {danger_area} | {(_char(view, c) or {}).get("area")
                                 for c in live_culprits if c != vip})
            for mc in opts.move_cards_for(vip):
                if _noop_char(view, mc, vip, roles):
                    continue    # B-31/G4：行き先が禁止＝空振り札
                if _own_move_dest(view, vip, mc) in _bad:
                    continue    # DP-4：退避先が同じ事件の的＝退避になっていない
                c_eff.breaks.append(Break(f"{vip}を{danger_area}から移動で退避",
                                          mc, vip, "character", 1.3, robust=False))
            c_occ = Condition("犯人の不安が臨界に届く（発生条件）")
            _cooling_breaks(c_occ, view, opts, live_culprits, criticals,
                            "犯人候補{c}を不安-1で冷やし発生を止める", 1.4)
            out.append(Threat("incident_vip", f"{nm}による{vip}殺害（{danger_area}）",
                              prob, True, "事件フェイズ", [c_eff, c_occ],
                              due_day=iday))
    return out


# ---------------------------------------------------------------------------
# ★B-153 ＋ B-157 Phase 2「自殺」（2026-08-05）：**事件「自殺」の脅威判定が存在しない**。
#
#   KB＝`rules/40_first_steps.md:150` 自殺＝「**犯人は死亡する**」。
#   ∴ 敗北へ届く経路は2本しかない（どちらも KB が**可否**を一意に決める構造）：
#     (1) **犯人自身が VIP（KP/フレンド）**＝発生＝そのまま VIP 死亡＝ループ敗北。
#     (2) ★**従者の身代わり**（`rules/30_characters.md:65` 現物カード転記 2026-07-23）＝
#         「同一エリアのお嬢様か大物が**死亡する場合、代わりに死亡する**」＝**強制・主は生存**。
#         ∴ 犯人がお嬢様/大物（＋友好4 `rules/20_goodwill_abilities.md:323` で追加された者）で、
#         **従者が同エリアに生存**しているなら、死ぬのは犯人ではなく**従者**。
#         **従者が VIP なら、犯人を殺す事件がそのまま主人公の敗北になる**。
#   ★★折り手の非対称（**KB が可否を一意に決める**）＝同条の
#     「同一エリアのお嬢様か大物が**移動する場合、自身への移動を無視して一緒に移動する**」
#     ＝**主へ移動札を置いても引き剥がせない**（従者が追随する）。**動かすのは従者側**。
#   ★暗躍禁止・移動禁止はこの経路を止めない（`rules/10_action_cards.md:65`＝暗躍禁止は
#     行動解決フェイズのみ／事件はフェイズ7＝`rules/00_rules_core.md`）。
#
#   実測（`docs/監査_B153_従者と自殺_2026-08-05.md`）＝自殺の D（直結）は 3日級2／5日級4
#   （B-157 §2 を独立に再現して完全一致）。うち**5日級3件が (2) の身代わり経路**
#   （`random_FS` s4・L3D4/L4D4/L8D4）。**`enumerate_threats` はこの事件を一度も名指ししない**。
#
#   ★これは「値踏みの結果として無視している」のではなく**判定器が無い**。ただし本チケットが
#     足すのは Threat の **prob（価値）と Break の cost（重み）**でもある＝
#     規約 §7 判例1 の「KB が一意に決める量」だけではない ∴ **rule-rational 免除は自称しない
#     ＝通常ゲート**（迷ったら「言えない」側に倒す）。
#   既定 False＝**挙動 bit 不変**（`enumerate_threats` は呼ぶが即 return []）。
B153_SUICIDE: bool = False
#: ★従者を主から引き剥がす移動 Break のコスト（`_threat_kp_killer` の引き離し 1.3 が比較対象）。
B153_JUUSHA_MOVE_COST: float = 1.3
#: ★自殺の犯人候補を冷やす Break のコスト（`_threat_incident_vip` の 1.4 が比較対象）。
B153_COOL_COST: float = 1.4


def juusha_targets_from_view(view: dict) -> frozenset:
    """従者の特性の対象（`rules/30_characters.md:65`）＝お嬢様/大物 ＋ 友好4の追加対象。

    ★追加対象は**公開イベント**（`sim/abilities.py:488` `juusha_target_added`）＝
      主人公も読める（カンニングではない）。効果は**そのループ中**だけ
      （`rules/20_goodwill_abilities.md:323`／`sim/state.py:506` がループ開始で捨てる）＝
      **同じループのイベントだけ**を拾う。
    """
    loop = view.get("loop")
    added = {e.get("target") for e in (view.get("history") or ())
             if e.get("event") == "juusha_target_added" and e.get("loop") == loop}
    return frozenset({"お嬢様", "大物"} | {a for a in added if a})


def _threat_incident_suicide(view, roles, opts, culprits, criticals) -> list[Threat]:
    """★B-153/B-157：事件「自殺」（`rules/40:150`＝犯人は死亡する）による VIP 死亡。

    2本の腕（上のコメント参照）。**どちらも発生条件は同じ**（犯人の不安が臨界）なので
    発生条件の折り手（犯人冷却）は共通、効果側の折り手だけが腕ごとに違う。
    """
    if not B153_SUICIDE:
        return []
    out: list[Threat] = []
    vips = _vip_suspects(view, roles)
    if not vips:
        return out
    day_now = view.get("day", 1)
    ju = _char(view, "従者")
    ju_alive = bool(ju and ju.get("alive", True) and ju.get("area"))
    ju_p = vips.get("従者", 0.0)          # 従者が VIP である確度（0なら腕2は立たない）
    masters = juusha_targets_from_view(view)
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if nm != "自殺" or iday is None or iday < day_now:
            continue
        cset = (culprits or {}).get(iday) or set()
        live = [c for c in cset if _alive(view, c)]
        if cset and not live:
            continue                      # 候補が全員死亡＝発生しない
        # ---- 発生確率＝犯人が不安臨界に届く近さ（`_threat_incident_vip` と同じ物差し）----
        occ = 0.12
        for cand in live:
            th = (criticals or {}).get(cand)
            cc = _char(view, cand)
            if th is None or cc is None:
                continue
            u = cc.get("unrest", 0)
            if u >= th:
                occ = max(occ, 0.9)
            elif th <= 1:
                occ = max(occ, 0.6)
            elif u >= th - 1:
                occ = max(occ, 0.45)
        days_away = (iday or day_now) - day_now
        if days_away >= 2:
            occ *= 0.5
        elif days_away == 1:
            occ *= 0.8

        # ---- 腕1＝犯人候補自身が VIP 疑い ------------------------------------
        vip_cands = {c: vips[c] for c in live if c in vips}
        if vip_cands:
            best = max(vip_cands, key=lambda n: vip_cands[n])
            # 候補が複数居るなら「その1人が真犯人である」不確かさで割り引く。
            share = (1.0 / len(live)) if len(live) > 1 else 1.0
            c_occ = Condition("犯人の不安が臨界に届く（発生条件）")
            _cooling_breaks(c_occ, view, opts, live, criticals,
                            "自殺の犯人候補{c}を不安-1で冷やし発生を止める",
                            B153_COOL_COST)
            out.append(Threat(
                "incident_suicide", f"自殺による{best}死亡（犯人自身がVIP）",
                vips[best] * occ * share, True, "事件フェイズ", [c_occ],
                "自殺の効果＝犯人が死亡（40:150）＝効果側は折れない。犯人冷却が唯一の折り手",
                due_day=iday))

        # ---- 腕2＝★従者の身代わり（`rules/30:65`）----------------------------
        if not (ju_alive and ju_p > 0.0):
            continue
        jarea = ju.get("area")
        # 犯人候補のうち「従者と同エリアに居る特性対象（お嬢様/大物/追加対象）」
        shielded = [c for c in live
                    if c in masters and c != "従者"
                    and (_char(view, c) or {}).get("area") == jarea]
        if not shielded:
            continue
        share = (len(shielded) / len(live)) if live else 1.0
        # 条件1（効果側）＝従者が犯人候補と同エリア＝**身代わりで死ぬ立ち位置**。
        #   ★折り手は**従者側の移動だけ**（主へ置くと追随される＝同条・KB が可否を決める）。
        #   ★退避先に別の特性対象（＝別の犯人候補）が居るエリアは選ばない（退避になっていない）。
        c_eff = Condition(
            f"従者が{jarea}で犯人候補{'/'.join(sorted(shielded))}と同エリア"
            f"（身代わりで死ぬ立ち位置）")
        _avoid = frozenset({jarea} | {(_char(view, c) or {}).get("area")
                                      for c in live if c in masters})
        c_eff.breaks.extend(_relocate_breaks(
            opts, "従者", jarea,
            "★従者を{a}から{how}で引き剥がす（主へ置くと追随＝30:65）",
            B153_JUUSHA_MOVE_COST, view, roles, avoid_areas=_avoid))
        c_occ2 = Condition("犯人の不安が臨界に届く（発生条件）")
        _cooling_breaks(c_occ2, view, opts, live, criticals,
                        "自殺の犯人候補{c}を不安-1で冷やし発生を止める",
                        B153_COOL_COST)
        out.append(Threat(
            "incident_suicide_juusha",
            f"自殺→従者の身代わりによる従者死亡（{jarea}）",
            ju_p * occ * share, True, "事件フェイズ", [c_eff, c_occ2],
            "従者は主の代わりに強制で死ぬ（30:65）＝主を動かしても追随する＝従者側を動かす",
            due_day=iday))
    return out


#: ★B-127（B-83②・2026-08-01）：暗躍による的化への対抗の**マスタートグル**。
#   False で `remote_murder_pin_live` が常に False を返す＝プランナーも主人公AIも旧挙動へ復帰。
B127_ANYAKU_TARGETING: bool = True
#: ★B-127 論点別トグル：プランナー側だけ（`_threat_remote_murder` の「暗躍0では脅威化しない」
#   1日遅れの是正）を切る。False＝脅威化は従来どおり暗躍≥1から・主人公AIの採点だけが新挙動。
B127_PLAN_EARLY_THREAT: bool = True


def remote_murder_pin_live(view, name, mm_chars, day_now=None) -> bool:
    """`name` が「**遠隔殺人の的にされつつある**」＝今日の暗躍禁止で的化を止められる状態か。

    ★B-127（B-83②）＝A-67 が名指しした欠落語彙「フレンド／急所への伏せ札は、配達だけでなく
      **的化（暗躍2）**もありうる」の二択読みの、**単一ソース**の述語。
      プランナー（`_threat_remote_murder`）と主人公AIの採点（`heuristic_protagonist._base_score`）が
      **同じこの関数**を見る（二重実装の禁止）。

    条件（すべて公開情報だけで確定する）：
      (1) このループの**残り日程**（事件日 ≥ 今日）に **遠隔殺人** が予定されている
          ＝`rules/40_first_steps.md:152`／`rules/50_basic_tragedy_x.md:209`
            「暗躍カウンターが2つ以上置かれているキャラクターがいる場合、その中から
             任意の1人を死亡させる」＝**位置非依存**（退避では守れない）。
      (2) `name` が生存し、**現在の暗躍が 2 未満**。
          暗躍2以上＝既に的＝暗躍禁止では戻せない（`rules/10_action_cards.md:61`＝
          暗躍禁止が無効化するのは「**重なった**暗躍+1/+2」だけ＝載ったカウンターは減らない）。
      (3) mm が**今日 `name` に札を伏せている**（`view["placements"]` は公開情報＝
          主人公は脚本家のセット位置を見てから置く＝`rules/00_rules_core.md:105-106`）。
          ＝今日重なる暗躍+ が存在しうる＝暗躍禁止が空振りでない
          （空振り判定の単一ソース＝`card_effect.noop_reason` G1 と同一の述語）。

    ★**中身は伏せなので「その札が暗躍か」は分からない**。本述語が主張するのは
      「**暗躍だった場合に的化が完成しうる位置に札がある**」という公開事実だけであり、
      相手の手札の予測ではない（B-76 の「配達実証」と同じ性格の、位置に基づく読み）。
    """
    if not B127_ANYAKU_TARGETING:
        return False
    if name not in mm_chars:
        return False
    c = _char(view, name)
    if not c or not c.get("alive", True):
        return False
    if c.get("anyaku", 0) >= REMOTE_MURDER_ANYAKU:
        return False
    d0 = view.get("day", 1) if day_now is None else day_now
    return any(i.get("name") == "遠隔殺人" and i.get("day") is not None
               and i.get("day") >= d0 for i in view.get("incidents", []) or [])


# ---------------------------------------------------------------------------
# ★B-201（2026-08-11・ユーザー実戦第3弾 §68-2）＝恋愛連鎖の「要衝拒否」
# ---------------------------------------------------------------------------
#: マスタートグル。False（既定）で `mainlover_anyaku_pin_live` が常に False を返す
#  ＝プランナーも主人公AIも旧挙動へ bit 復帰。
#: ★既定 ON（2026-08-11・OpusA 裁定）＝両ベンチ flip 0（bit 一致）で退行なし・単局2局で
#  winner=mastermind → protagonist（L2 防衛）＝ユーザー実戦由来の穴の是正。
B201_MAINLOVER_ANYAKU_PIN: bool = True
#: メインラバーズ疑いを「要衝」とみなす実在度の下限（確信帯＝B-100 の θ と同じ 0.9）。
B201_MAINLOVER_P: float = 0.9

#: ★B-207 Phase 1 #2（2026-08-12・原因＝バックログ §69-3／Phase 0 報告 §2-1）切替口。
#: False で条件 (4) は旧挙動（「既に暗躍2」or「**今日**的にされつつある」）へ **bit 復帰**。
#: 機序＝教材では相手が **D1 に `暗躍+1→メインラバーズ疑い`（この時 (4) は未成立）／
#:   D2 に `暗躍+2→ラバーズ疑い`（この時 (2)「暗躍ちょうど0」が既に壊れている）** と
#:   2つの事象を**別の日に**置いたため、両方を同じ日に要求する連言が**恒久的に空**になった
#:   （旧教材では 9 席 True だった同じ述語が、要衝札を **1日ずらされただけで 0 席**）。
#: ∴ (4) を「**残り日程で暗躍2に届きうる**」（＝mm の暗躍札の残弾会計による上限評価）へ緩和する。
B201_LOVER_REACH: bool = True


def lover_reaches_remote_target(view, lover: str, day_now=None) -> bool:
    """★B-207 #2＝ラバーズ疑い `lover` が「**残り日程で暗躍2に届きうる**」か。

    「届きうる」の会計は**新設しない**＝既存の残弾会計（`mm_plus2_spent`＝
    `暗躍+2` は 1/loop・`暗躍+1` は毎日再利用＝`card_supply_max` と同じ式）を、
    **ループ末ではなく遠隔殺人の事件日まで**で切って使う：

        必要数   = `REMOTE_MURDER_ANYAKU`(2) − 現在の暗躍
        供給上限 = (事件日 − 今日 + 1) + (2 if `暗躍+2` 未消費 else 0)

    - 事件日までの日数を数えるのは、`rules/00_rules_core.md` のフェイズ順が
      〔4 行動解決 → 7 事件〕＝**事件日当日の行動解決で載った暗躍も間に合う**ため。
    - `mm_plus2_spent` が `None`（判定材料なし）＝**未消費と仮定**＝上限を大きく見る
      ＝「届かない」と主張しない側（B-132 と同じ安全側の倒し方）。

    ★これは**上限評価**（mm がその的だけに全弾を注ぐ最良ケース）であって予測ではない。
      狭さは呼び出し側の (1)(2)(3)（今日の伏せ札・暗躍ちょうど0・実在度≥0.9）が担う。
    """
    c = _char(view, lover)
    if not c or not c.get("alive", True):
        return False
    need = REMOTE_MURDER_ANYAKU - int(c.get("anyaku", 0) or 0)
    if need <= 0:
        return True                                    # 既に合法な対象
    d0 = int(view.get("day", 1)) if day_now is None else int(day_now)
    plus2 = 0 if mm_plus2_spent(view) is True else 2
    for inc in view.get("incidents", []) or []:
        if inc.get("name") != "遠隔殺人":
            continue
        di = inc.get("day")
        if di is None or int(di) < d0:
            continue
        if need <= (int(di) - d0 + 1) + plus2:
            return True
    return False


def mainlover_anyaku_pin_live(view, name, mm_chars, roles, day_now=None) -> bool:
    """`name`（メインラバーズ疑い）に**今日 暗躍1が載ろうとしている**か
    ＝**今日の `暗躍禁止` だけ**がこのループの恋愛連鎖を恒久停止できる状態か。

    機序（KB 突合済み・教材＝`docs/feedback_logs/鈴蘭_BTX3d_seed0_ラバーズ連鎖_2026-08-11.jsonl`）：
      ラバーズ死亡 → メインラバーズに不安+6（`rules/50_basic_tragedy_x.md:159`）→
      メインラバーズは**不安3以上かつ暗躍1以上**でターン終了フェイズに主人公を死亡させる
      （`:160`）。∴ **暗躍を1つも載せなければ、ラバーズが死んでも主人公は死なない**。
      冷却（不安-1）は +6 に追いつけない（1ループ3枚 vs +6）＝折れる条件は**暗躍側だけ**。

    条件（すべて公開情報だけで確定する。役職は belief の周辺確率＝公開情報から導く）：
      (1) mm が**今日 `name` に札を伏せている**（`view["placements"]` は公開＝主人公は
          脚本家のセット位置を見てから置く＝`rules/00_rules_core.md:105-106`）
          ＝今日重なる暗躍+ が存在しうる＝暗躍禁止が空振りでない
          （空振り判定の単一ソース＝`card_effect.noop_reason` G1 と同じ述語）。
      (2) `name` が生存し、**現在の暗躍が 0**。1以上＝**もう手遅れ**
          （`rules/10_action_cards.md:61`＝暗躍禁止が無効化するのは「重なった暗躍+1/+2」だけ
           ＝**既に載ったカウンターは減らない**。主人公の手札に暗躍除去は存在しない）。
          ＝この 0→1 の一手だけが可逆／不可逆の境目＝**要衝**。
      (3) `name` のメインラバーズ実在度が `B201_MAINLOVER_P` 以上。
      (4) **連鎖の入口（ラバーズの死）がこのループの残り日程に立つ**：ラバーズ疑いが生存し、
          残り日程に **遠隔殺人**（位置非依存＝退避で守れない・`rules/40_first_steps.md:152`／
          `rules/50_basic_tragedy_x.md:209`）が予定され、そのラバーズが
          **既に暗躍2以上（＝既に合法な対象）** か **今日 的にされつつある**
          （`remote_murder_pin_live`＝B-127 の単一ソース）。

    ★本述語が主張するのは「**暗躍だった場合に連鎖の要件が完成しうる位置に札がある**」という
      公開事実だけであり、伏せ札の中身の予測ではない（B-127 と同じ性格）。
    ★(4) を要求する理由＝**狭くするため**。ラバーズの死が立たないループでメインラバーズの
      暗躍を毎日拒否すると板ガード・冷却の席を恒常的に奪う（B-189/B-195 v1 の教訓）。
    """
    if not B201_MAINLOVER_ANYAKU_PIN:
        return False
    if name not in mm_chars:
        return False                                   # (1)
    c = _char(view, name)
    if not c or not c.get("alive", True):
        return False
    if c.get("anyaku", 0) != 0:
        return False                                   # (2) 1以上＝手遅れ
    if (roles or {}).get(name, {}).get("メインラバーズ", 0.0) < B201_MAINLOVER_P:
        return False                                   # (3)
    d0 = view.get("day", 1) if day_now is None else day_now
    if not any(i.get("name") == "遠隔殺人" and i.get("day") is not None
               and i.get("day") >= d0 for i in view.get("incidents", []) or []):
        return False                                   # (4) 残り日程に遠隔殺人が無い
    for lv in _suspects(roles, "ラバーズ"):
        if lv == name or not _alive(view, lv):
            continue
        lc = _char(view, lv)
        if lc and lc.get("anyaku", 0) >= REMOTE_MURDER_ANYAKU:
            return True                                # 既に合法な対象
        if remote_murder_pin_live(view, lv, mm_chars, day_now):
            return True                                # 今日 的にされつつある
        # ★B-207 #2＝「今日的」ではなく「**残り日程で暗躍2に届きうる**」へ緩和。
        #   相手が要衝札を1日ずらすだけで死ぬ連言（Phase 0 §2-1）を、日をまたいだ
        #   到達可能性で置き換える。上限評価＝`lover_reaches_remote_target`。
        if B201_LOVER_REACH and lover_reaches_remote_target(view, lv, day_now):
            return True
    return False


def _threat_remote_murder(view, roles, opts, culprits, criticals,
                          mm_chars: set = frozenset()) -> list[Threat]:
    """遠隔殺人：犯人が不安臨界に届くと、暗躍≥2 のキャラを1体殺す（位置非依存・負け筋ツリー§1 RM）。
    VIP（KP/フレンド）に暗躍≥2 が載る（or 載りつつある）なら殺害対象＝ループ敗北。
    防御は位置でなく、(a) VIPの暗躍を2未満に抑える（暗躍禁止・preventive）／(b) 犯人を冷却して
    発生阻止。★従来 incident_vip は病院/殺人（位置型）しか見ておらず遠隔殺人を取りこぼしていた
    （テスター実測 2026-07-13：遠隔殺人でKPが毎ループ死ぬのにプランナーが脅威化しなかった）。"""
    out = []
    vips = _vip_suspects(view, roles)
    if not vips:
        return out
    day_now = view.get("day", 1)
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if nm != "遠隔殺人" or iday is None or iday < day_now:
            continue
        cset = (culprits or {}).get(iday) or set()
        live_culprits = [c for c in cset if _alive(view, c)]
        if not live_culprits and cset:
            continue
        # 発生の近さ（犯人の不安臨界到達）＝incident_vip と同じ会計。
        occ = 0.12
        for cand in (live_culprits or list(vips)):
            th, cc = criticals.get(cand), _char(view, cand)
            if th is not None and cc is not None:
                u = cc.get("unrest", 0)
                if u >= th:
                    occ = max(occ, 0.9)
                elif th <= 1:
                    occ = max(occ, 0.6)
                elif u >= th - 1:
                    occ = max(occ, 0.45)
        days_away = (iday or day_now) - day_now
        if days_away >= 2:
            occ *= 0.5
        elif days_away == 1:
            occ *= 0.8
        for vip, pv in vips.items():
            vc = _char(view, vip)
            if not vc:
                continue
            anr = vc.get("anyaku", 0)
            # ★B-127（B-83②）：従来は「暗躍0＝2まで遠い」で切っていたが、mmの `暗躍+2` は
            #   **1枚で 0→2 を作る**（`rules/40:152` の条件を1手で満たす）ため、この帯では
            #   breakable な窓が一度も開かなかった（実測＝`random_BTX` s12 の 3日/5日は
            #   毎ループ D-1 に `暗躍+2→フレンド` が伏せられ、翌日には既に手遅れ）。
            #   ∴ **今日その VIP に mm の札が伏せてある日だけ**、暗躍0でも脅威化する
            #   （判定は単一ソース `remote_murder_pin_live`）。
            _pin_live = (B127_PLAN_EARLY_THREAT
                         and remote_murder_pin_live(view, vip, mm_chars, day_now))
            if anr < 1 and not _pin_live:
                continue  # 暗躍0×mm札なし＝2まで遠い（ノイズ抑制）。
            # 既に殺害圏(≥2) / あと1(=1) / 今日の伏せ札で 0→2 がありうる(=0×mm札)
            prog = 1.0 if anr >= 2 else (0.5 if anr >= 1 else 0.25)
            prob = pv * occ * prog
            c_eff = Condition(f"VIP{vip}に暗躍2が載る（遠隔殺人の対象）")
            # ★B-28 Step1（G1）：暗躍禁止は「そのキャラに載った今ターンの暗躍+」しか
            #   打ち消せない＝mmがそのVIPに札を伏せていなければ証明可能な空振り（mmのセット位置は
            #   公開情報）。既に載っている暗躍(anr>=1)は過去の蓄積＝今ターン打ち消せない。
            #   判定は card_effect.noop_reason（heuristic の空振りゲートと同一述語＝PLAN_HOT が
            #   heuristic の判断を上書きする事故の構造封鎖）。
            # ★DP-4：暗躍禁止は**行動解決フェイズでのみ**有効（KB: 10）＝同エリアのクロマク疑いが
            #   脚本家能力フェイズに +1 を載せれば暗躍2に届く（暗躍禁止では止まらない・KB: 60 C-5）。
            #   その位置関係のときは「暗躍禁止で断つ」は条件を折れない＝折り手にしない。
            _kuromaku_here = any(
                (_char(view, n) or {}).get("area") == vc.get("area") and _alive(view, n)
                for n in _suspects(roles, "クロマク", _SUSPECT_P) if n != vip)
            if anr < 2 and not _kuromaku_here and opts.has_char("暗躍禁止", vip) \
                    and noop_reason(view, "暗躍禁止", vip, "character",
                                    NoopCtx(mm_chars=frozenset(mm_chars))) is None:
                c_eff.breaks.append(Break(
                    f"{vip}に暗躍禁止で暗躍供給を断つ（殺害対象化を防ぐ）",
                    "暗躍禁止", vip, "character", 1.0, robust=True))
            c_occ = Condition("犯人の不安が臨界に届く（発生条件）")
            _cooling_breaks(c_occ, view, opts, live_culprits, criticals,
                            "犯人候補{c}を不安-1で冷やし遠隔殺人を止める", 1.4)
            out.append(Threat("remote_murder_vip", f"遠隔殺人による{vip}殺害（暗躍2）",
                              prob, True, "事件フェイズ", [c_eff, c_occ],
                              due_day=iday))
    return out


def _threat_butterfly(view, roles, opts, culprits, criticals,
                      butterfly_prob, display_all: bool = False) -> list[Threat]:
    """蝶の羽ばたき×未来改変プラン（B-9・btx_future族）：蝶の羽ばたきが発生（犯人の不安が臨界到達）
    すると未来改変プランが敗北へ進む。VIP殺害でなく**ルールY進行**＝位置防御は無効・**犯人冷却が
    唯一の折り手**（負け筋防御ツリー §butterfly／loop_race の butterfly パスに対応）。
    butterfly_prob＝P(rule_Y=未来改変プラン)。0なら不発。"""
    if butterfly_prob < 0.05 and not display_all:
        return []
    day_now = view.get("day", 1)
    out: list[Threat] = []
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if nm != "蝶の羽ばたき" or iday is None or iday < day_now:
            continue
        cset = (culprits or {}).get(iday) or set()
        live_culprits = [c for c in cset if _alive(view, c)]
        if not live_culprits and cset:
            continue
        occ = 0.12
        for cand in (live_culprits or []):
            th = criticals.get(cand)
            cc = _char(view, cand)
            if th is not None and cc is not None:
                u = cc.get("unrest", 0)
                if u >= th:
                    occ = max(occ, 0.9)
                elif th <= 1:
                    occ = max(occ, 0.6)
                elif u >= th - 1:
                    occ = max(occ, 0.45)
        days_away = (iday or day_now) - day_now
        if days_away >= 2:
            occ *= 0.5
        elif days_away == 1:
            occ *= 0.8
        prob = butterfly_prob * occ
        c_occ = Condition("蝶の羽ばたきの犯人の不安が臨界に届く（発生＝未来改変が敗北へ進む）")
        _cooling_breaks(c_occ, view, opts, live_culprits, criticals,
                        "犯人候補{c}を不安-1で冷やし蝶の羽ばたきの発生を止める", 1.4)
        out.append(Threat("butterfly", "蝶の羽ばたき×未来改変プラン（発生で敗北へ）",
                          prob, True, "事件フェイズ", [c_occ],
                          due_day=iday))
    return out


def _threat_hospital_protagonist_death(view, roles, opts, supply_rumor,
                                       culprits, criticals,
                                       rumor_left=None) -> list[Threat]:
    """病院の事件の主人公死亡アーム（G1・B-11）：病院暗躍≥2 のとき事件発生で**主人公が死亡＝即敗北**
    （rules/40:151・reference/effects＝病院暗躍≥1は病院全員死亡=incident_vip担当／≥2は主人公）。
    2条件AND＝(事件が発生)∧(病院暗躍が2に届く)＝どちらか折れば防げる。折り手＝犯人冷却（発生を止める）
    ／病院暗躍を2未満に保つ（暗躍禁止=行動解決供給／クロマク剥がし）。"""
    day_now = view.get("day", 1)
    banr = view.get("board_anyaku", {}) or {}
    out: list[Threat] = []
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if nm != "病院の事件" or iday is None or iday < day_now:
            continue
        cset = (culprits or {}).get(iday) or set()
        live_culprits = [c for c in cset if _alive(view, c)]
        if not live_culprits and cset:
            continue
        occ = 0.12
        for cand in (live_culprits or []):
            th = criticals.get(cand)
            cc = _char(view, cand)
            if th is not None and cc is not None:
                u = cc.get("unrest", 0)
                if u >= th:
                    occ = max(occ, 0.9)
                elif th <= 1:
                    occ = max(occ, 0.6)
                elif u >= th - 1:
                    occ = max(occ, 0.45)
        days_away = (iday or day_now) - day_now
        if days_away >= 2:
            occ *= 0.5
        elif days_away == 1:
            occ *= 0.8
        cur = banr.get("病院", 0)
        board_progress = 0.9 if cur >= 2 else (0.5 if cur == 1 else 0.2)
        prob = occ * board_progress
        # 発生条件（犯人冷却で折る）
        c_occ = Condition("病院の事件の犯人の不安が臨界に届く（発生条件）")
        _cooling_breaks(c_occ, view, opts, live_culprits, criticals,
                        "病院の事件の犯人候補{c}を不安-1で冷やし発生を止める", 1.4)
        # 病院暗躍が2に届く条件（暗躍禁止/クロマク剥がしで折る）
        c_board = Condition("病院暗躍が2に届く（主人公死亡条件）")
        _add_anyaku_supply_breaks(c_board, view, roles, opts, None, "病院",
                                  supply_rumor, rumor_left=rumor_left)
        out.append(Threat("hospital_protagonist",
                          "病院の事件による主人公死亡（病院暗躍≥2）",
                          prob, True, "事件フェイズ", [c_occ, c_board],
                          due_day=iday))
    return out


def _threat_tt_defeat(view, roles, opts, final_day) -> list[Threat]:
    """タイムトラベラー任意敗北：最終日＋TTの友好2以下。友好+で封じる（友好禁止を無視）。"""
    if not final_day:
        return []
    out = []
    for t, pt in _suspects(roles, "タイムトラベラー").items():
        c = _char(view, t)
        if not c or not c.get("alive", True) or c.get("goodwill", 0) > 2:
            continue
        c1 = Condition("TTの友好が2以下（最終日）")
        # ★DP-4（過大主張E＝到達不能）：折るには友好を**3以上**にする必要がある。従来は
        #   手札にある方の札を無条件に折り手にしていたため、友好0に 友好+2 を載せて 2 のまま
        #   （＝TTは依然として任意敗北を宣言できる）でも「折れた」と数えていた。
        #   到達する札だけを折り手にする（+2 を優先＝安い順ではなく確実な順）。
        _gw = c.get("goodwill", 0)
        for card, delta in (("友好+2", 2), ("友好+1", 1)):
            if _gw + delta >= 3 and opts.has_char(card, t):
                c1.breaks.append(Break(f"{t}に{card}（友好禁止を無視して載る）",
                                       card, t, "character", 1.1, robust=True))
                break
        out.append(Threat("tt_defeat", f"TT{t}の任意敗北（最終日）", pt,
                          True, "ループ終了フェイズ", [c1],
                          due_day=view.get("day")))
    return out


def enumerate_threats(view: dict, role_marginals: dict, *,
                      supply_rumor: bool = False,
                      rumor_left: int | None = None,
                      options: list[dict] | None = None,
                      culprits: dict | None = None,
                      criticals: dict | None = None,
                      final_day: bool = False,
                      defeat_board_probs: dict | None = None,
                      virus_p: float = 0.0,
                      contract_prob: float = 0.0,
                      butterfly_prob: float = 0.0,
                      include_breached: bool = False,
                      display_all: bool = False,
                      min_prob: float = 0.01) -> list[Threat]:
    """現局面で火が点きうる負け筋を列挙（severity 降順）。

    role_marginals＝belief.role_marginals()（name -> {role: prob}）。
    supply_rumor＝「不穏な噂」ルールが確有/濃厚か（rule_marginals から呼び出し側で判定）。
    rumor_left＝★B-113：このループの噂の残弾（0＝消費済みが証明できた／None＝不明＝
    未消費と仮定＝防御不能側）。`rumor_left_for(belief, view)` で作る。
    options＝自チームが置ける手札候補（防御手の在庫。None なら折る手なし＝全部要防御表示）。
    culprits＝belief.culprit_candidates()（{day: set}）。criticals＝{name: 不安臨界}。
    final_day＝このターンがループ最終日か（TT任意敗北の判定用）。
    """
    opts = _Opts(options or [])
    threats: list[Threat] = []
    mm_chars, _mm_boards = _mm_touched(view)
    try:  # mmが今ターン キラー/KP に伏せた札で実在度昇格（バグ6）
        threats.extend(_threat_kp_killer(view, role_marginals, opts,
                                         supply_rumor, mm_chars,
                                         rumor_left=rumor_left))
    except Exception:
        pass
    try:  # B-34：KP能力獲得ファクター（都市暗躍≥2）の死亡＝KPと同じ即ループ終了
        threats.extend(_threat_factor_kp(view, role_marginals, opts,
                                         supply_rumor, mm_chars,
                                         rumor_left=rumor_left))
    except Exception:
        pass
    try:
        threats.extend(_threat_killer_protagonist(view, role_marginals, opts,
                                                  supply_rumor,
                                                  display_all=display_all,
                                                  rumor_left=rumor_left))
    except Exception:
        pass
    try:
        threats.extend(_threat_mainlover_protagonist(view, role_marginals, opts,
                                                     supply_rumor,
                                                     display_all=display_all))
    except Exception:
        pass
    try:  # ボード敗北はルール依存＝defeat_board_probs（空なら1件も出ない＝安全）
        threats.extend(_threat_board_defeat(view, role_marginals, opts,
                                            supply_rumor, defeat_board_probs or {},
                                            culprits=culprits or {},
                                            criticals=criticals or {},
                                            include_breached=include_breached,
                                            display_all=display_all,
                                            rumor_left=rumor_left))
    except Exception:
        pass
    try:  # ★僕と契約（KP暗躍≥2単独勝ち）＝ボード敗北と同型の評価敗北（contract_prob=0なら出ない）
        threats.extend(_threat_kp_anyaku(view, role_marginals, opts,
                                          supply_rumor, contract_prob,
                                          display_all=display_all,
                                          rumor_left=rumor_left))
    except Exception:
        pass
    try:
        threats.extend(_threat_kp_sk(view, role_marginals, opts, mm_chars))
    except Exception:
        pass
    try:  # ★§C-1：妄想拡大ウイルスで不安≥3のパーソンがSK化＝SK役不在でも2人きり殺害を警戒
        threats.extend(_threat_virus_sk(view, role_marginals, opts, virus_p,
                                        display_all=display_all,
                                        mm_chars=mm_chars))
    except Exception:
        pass
    try:  # ★バグ6：mmの配置が脅威の関係者と一致＝SK2人きりの仕込みを先回りで固める
        threats.extend(_threat_sk_setup(view, role_marginals, opts, mm_chars))
    except Exception:
        pass
    try:
        threats.extend(_threat_incident_vip(view, role_marginals, opts,
                                            culprits or {}, criticals or {}))
    except Exception:
        pass
    try:  # ★B-153/B-157：自殺（犯人自身がVIP／従者の身代わり）＝既定 OFF で即 return []
        threats.extend(_threat_incident_suicide(view, role_marginals, opts,
                                                culprits or {}, criticals or {}))
    except Exception:
        pass
    try:  # ★遠隔殺人（暗躍2のVIP殺害）＝位置型でないので incident_vip とは別枠で検出
        threats.extend(_threat_remote_murder(view, role_marginals, opts,
                                             culprits or {}, criticals or {},
                                             mm_chars))
    except Exception:
        pass
    try:  # ★蝶の羽ばたき×未来改変プラン（B-9・btx_future族＝犯人冷却が唯一の折り手）
        threats.extend(_threat_butterfly(view, role_marginals, opts,
                                         culprits or {}, criticals or {}, butterfly_prob,
                                         display_all=display_all))
    except Exception:
        pass
    try:  # ★病院の事件の主人公死亡アーム（G1・B-11＝病院暗躍≥2で即敗北）
        threats.extend(_threat_hospital_protagonist_death(
            view, role_marginals, opts, supply_rumor, culprits or {}, criticals or {},
            rumor_left=rumor_left))
    except Exception:
        pass
    try:
        threats.extend(_threat_tt_defeat(view, role_marginals, opts, final_day))
    except Exception:
        pass
    # 突破済み（breached）は min_prob に関わらず表示用に残す（B-1）。
    threats = [t for t in threats if t.severity >= min_prob or t.breached]
    threats.sort(key=lambda t: (t.breached, t.severity), reverse=True)
    return threats


def rumor_left_for(belief, view: dict | None = None) -> int | None:
    """★B-113（2026-07-31）：噂の残弾を belief から作る（`unstoppable_supply_gap` の
    `rumor_left` 注入の**単一ソース**＝defense_plan と b100_mix の両方がこれを使う）。

    - belief が「このループで噂（1/loop）が既に消費された」ことを公開情報から証明できた
      （`belief.rumor_spent_this_loop`）→ **0**（残弾なし＝現在値1の板が防御可能に戻る）。
    - 証明できない／belief が無い／例外 → **None**（未消費と仮定＝防御不能側に倒す健全側
      ＝DP-6 の既定挙動そのまま）。
    """
    try:
        fn = getattr(belief, "rumor_spent_this_loop", None)
        if fn is not None and fn((view or {}).get("loop")):
            return 0
    except Exception:
        return None
    return None


def _rumor_active(belief, thresh: float = 0.5) -> bool:
    """『不穏な噂』ルール（任意ボード暗躍+1・暗躍禁止で止まらない）が濃厚か。

    belief.rule_marginals()＝{(rule_y, rule_xs): prob}。rule_y/rule_xs に含む確率を合算。
    """
    try:
        rm = belief.rule_marginals()
    except Exception:
        return False
    p = 0.0
    for (ry, rxs), w in rm.items():
        if ry == "不穏な噂" or "不穏な噂" in (rxs or ()):
            p += w
    return p >= thresh


def rumor_p(belief) -> float:
    """★B-204：P(不穏な噂 ∈ ルール)＝**能力供給チャネルの事後確率**の取得口（単一ソース）。

    `_rumor_active` が内部で計算していた合算を、閾値判定と分離して**値として**返すだけの
    読み取り専用ヘルパ（belief 側は無改変）。取得不能は 0.0（＝チャネル無しと見なす＝
    本チケットの述語は「発火しない」側へ倒れる＝安全側）。
    """
    try:
        rm = belief.rule_marginals()
    except Exception:
        return 0.0
    p = 0.0
    for (ry, rxs), w in rm.items():
        if ry == "不穏な噂" or "不穏な噂" in (rxs or ()):
            p += w
    return p


# ---------------------------------------------------------------------------
# ★B-204（2026-08-12・起票＝バックログ §68-6／実演＝§68-7）：
#   板ガードの「許容量」＝**カードであと何枚まで通してよいか**。
# ---------------------------------------------------------------------------
#: 能力供給チャネル（不穏な噂）を「生きている」と見なす事後確率の下限。
#: ★0.5（`_rumor_active` の既定）ではなく 0.40 を採るのは、この判定が**守りの側**
#:   だから＝外した時の損は「板ガードを1枚打つ」（その日 mm が札を置いている板＝
#:   等価交換は成立する）で、当てなかった時の損は**ループ敗北**（教材 §68-7 の L5）。
#:   掃引口＝ベンチで振れる。
B204_RUMOR_P: float = 0.40


def board_card_allowance(view: dict, area: str, *, supply_rumor: bool,
                         rumor_left: int | None = None,
                         threshold: int = BOARD_DEFEAT_ANYAKU) -> int:
    """★B-204：その板に**カードで許してよい残量**（許容量）。

        許容量 = 臨界 − 現在値 − k − 1 = `unstoppable_supply_gap(...) − 1`

    - `unstoppable_supply_gap` が返す gap＝「止まらない供給を使い切ってなお、臨界に届くには
      行動解決フェイズの暗躍カードがあと何個要るか」（DP-6・単一ソース）。
    - ∴ **許容量 = gap − 1**＝「そのうち何個までなら通してよいか」。
      - 許容量 <= 0（gap<=1）＝**1枚も通せない**（通した瞬間、止まらない供給が最後の1つを
        無料で供給しうる）。★教材＝§68-7 の L5＝D1 のカード1枚が素通り（都市 0→1）→
        D3 はカードを阻止したが脚本家能力フェイズの +1 で 2 到達＝板敗北。
      - 許容量 >= 1＝1枚通しても翌日以降にまだ守る手が残る。
    - ★ gap<=0（＝許容量<=-1）は **B-189 の管轄**（札と無関係に届く＝暗躍禁止は空振り）。
      本チケットの述語（`board_zero_allowance`）は **gap==1 ちょうど**に限る＝両者は
      構造的に排他（テストで固定）。

    ルール接地：暗躍禁止が消せるのは**行動解決フェイズに重なった暗躍+カード**だけ
    （`rules/10_action_cards.md:61,65`）。不穏な噂＝【任意】脚本家能力フェイズに任意の
    ボード1つへ暗躍+1・1/loop（`rules/40_first_steps.md:61`／`rules/50_basic_tragedy_x.md:75`）
    ＝**阻止不能**。しかも `rules/00_rules_core.md:109`「複数可能なら自由順で各1回ずつ」
    ＝ミスリーダー等と**同じフェイズに併用できる**。
    """
    cur = int((view.get("board_anyaku") or {}).get(area, 0) or 0)
    return unstoppable_supply_gap(view, area, cur, threshold,
                                  supply_rumor=supply_rumor,
                                  rumor_left=rumor_left) - 1


_ANYAKU_CARD_VALUE = {"暗躍+1": 1, "暗躍+2": 2}


def board_card_supply_placed(view: dict, area: str) -> bool:
    """★B-66 再設計：**過去ループに mm がその板へ暗躍カードを伏せた公開実績**があるか。

    `board_card_supply_proven`（下）との違いが本チケットの核心：

    | 述語 | 見る観測 | 主人公の防御に汚染されるか |
    |---|---|---|
    | `board_card_supply_proven` | 行動解決フェイズの `anyaku` イベント＝**乗った量** | ★される（阻止すると観測が消える） |
    | `board_card_supply_placed`  | `cards_revealed` の**配置そのもの** | されない（伏せ札は毎ターン全枚数が公開） |

    ★なぜ配置を見るのか＝結果カウンター（乗った量・ループ終了時の値）は**こちらが阻止すると
    減る**＝「阻止したから少ない」と「そもそも狙われていない」が同じ見た目になる。
    投資の有無を知りたいなら**投資の現物（伏せ札の公開）**を見るのが筋で、これはルール上
    つねに公開される（`rules/00_rules_core.md:105-107`＝行動解決フェイズの頭で全カードを公開・
    実装＝`sim/flow.py:204` の `cards_revealed`）。

    ∴ `board_card_supply_proven(view, a)` ⇒ `board_card_supply_placed(view, a)`
    （乗ったなら必ず置かれている）。逆は成り立たない＝**その差が「阻止できた分」**。
    """
    loop_now = view.get("loop")
    for e in view.get("history", []) or []:
        if e.get("event") != "cards_revealed" or e.get("loop") == loop_now:
            continue
        for p in e.get("placements", []) or []:
            if (p.get("owner") == "mastermind"
                    and p.get("target_kind") == "board"
                    and p.get("target") == area
                    and p.get("card") in _ANYAKU_CARD_VALUE):
                return True
    return False


def board_card_supply_proven(view: dict, area: str) -> bool:
    """★B-204：**過去ループにその板へ行動解決フェイズの暗躍（＝mm のカード供給）が
    公開観測されている**か（`rules/10:65`＝暗躍禁止が効くのはこのフェイズだけ）。

    ★なぜ要るか（§68-6 の教訓の実装）＝「**結果カウンターから防御の成否を推測しない**」。
    B-66(2) は「敗北ループの全てでループ終了時 暗躍<2 に終わった板＝実証ゼロ＝囮の公算」
    として板ガードを 96→74.5 に減点するが、**その板を毎日阻止していたから <2 で終わった**
    場合も同じ見た目になる（教材2局とも真の敗北板〔都市〕がこれで降格され、
    §68-7 の L5D1 で `暗躍禁止→医者 84.73` に席を奪われてカード1枚が素通りした）。
    行動解決フェイズの暗躍イベント（公開）が過去ループに在る＝**mm は実際にその板へ
    カードを投じている**＝「実証ゼロ」の前提は反証済み。

    ★黒猫のループ開始時+1（`rules/30` 特性1）は行動解決フェイズのイベントではない
    ＝この述語には**入らない**（無料 preload の板を投資実績と誤認しない）。
    """
    loop_now = view.get("loop")
    for e in view.get("history", []) or []:
        if (e.get("event") == "anyaku"
                and e.get("phase") == "action_resolution"
                and e.get("target") == area
                and e.get("loop") != loop_now
                and int(e.get("delta", 0) or 0) > 0):
            return True
    return False


def board_zero_allowance(view: dict, area: str, belief, defeat_probs: dict | None,
                         *, rumor_p_min: float | None = None) -> bool:
    """★B-204 の狭い述語＝「この板は**カードを1枚でも通したら負ける**」と言えるか。

    条件（全て公開情報＋belief。★神視点は一切見ない＝§68-11）：
      1. 板敗北ルールがその板で**生きている**（`defeat_probs[area] > 0`）。
      2. **能力供給チャネルが生きている**＝P(不穏な噂) >= `B204_RUMOR_P`
         かつ このループの残弾あり（`rumor_left_for` が 0 を返さない）。
      3. **許容量 == 0**（`board_card_allowance` == 0 ＝ gap==1）。
         gap<=0（許容量<0）は B-189 の管轄＝除外（構造的に排他）。
      4. **公開のカード供給実績**（`board_card_supply_proven`）＝§68-6 の教訓。

    ★2 と 3 は独立ではない：k=1（噂が生きている）で gap==1 ⇔ **現在値0**。
      ∴ 本述語が真になるのは「現在値0・噂が生きている・敗北ルールが生きている・
      過去にカード供給の実績がある板」＝**臨界2 に対して残り1枚も余裕が無い盤**。
    """
    if defeat_probs is not None and float(defeat_probs.get(area, 0.0) or 0.0) <= 0.0:
        return False
    thresh = B204_RUMOR_P if rumor_p_min is None else rumor_p_min
    if rumor_p(belief) < thresh:
        return False
    rleft = rumor_left_for(belief, view)
    if rleft is not None and int(rleft) <= 0:
        return False
    if board_card_allowance(view, area, supply_rumor=True, rumor_left=rleft) != 0:
        return False
    return board_card_supply_proven(view, area)


def _criticals_for(view) -> dict:
    """各キャラの不安臨界（engine.data）。取得不能なら空dict（事件ディテクタが退化）。"""
    try:
        from engine.data import unrest_threshold_of
    except Exception:
        return {}
    out = {}
    for c in view.get("characters", []):
        n = c.get("name")
        try:
            th = unrest_threshold_of(n)
        except Exception:
            th = None
        if th is not None:
            out[n] = th
    return out


def _rulex_prob(belief, name: str) -> float:
    """ルールX name（例『妄想拡大ウイルス』）が実在する確率（rule_marginals から合算）。"""
    try:
        rm = belief.rule_marginals()
    except Exception:
        return 0.0
    return sum(w for (_ry, rxs), w in rm.items() if name in (rxs or ()))


def plan_for_belief(view: dict, belief, options: list[dict] | None = None, *,
                    seats: int = 3, card_turn_caps: dict | None = None,
                    include_breached: bool = False, stage: str = "",
                    display_all: bool = False, min_prob: float = 0.01,
                    initial_areas: dict | None = None):
    """belief（role_marginals/rule_marginals/culprit_candidates を持つ）から
    脅威列挙＋防御計画を一括生成。

    返り値 (threats, plan)。belief には duck-typing 依存のみ。criticals は engine.data
    （静的な参照データ）を使うが、取得不能でも事件ディテクタが退化するだけで無害。
    ★card_turn_caps は既定 None＝従来どおり（AI配線 heuristic_protagonist は既定で呼ぶ＝不変）。
    助言表示（内省パネル）は ANRYAKU_KINSHI_TURN_CAP を渡して二正面を正しく表示できる。
    ★include_breached=True＝突破済み(暗躍≥2等)の負け筋も breached=True 付きで表示用に返す。
      stage＝この計画がどの時点の盤面か（例「脚本家セット後」）＝plan.loop/day と共にヘッダ表示用。
      ★意思決定既定（include_breached=False）は挙動不変（B-1・2026-07-13・AIA表示層との結合点）。
    """
    roles = belief.role_marginals() if belief is not None else {}
    try:
        culprits = belief.culprit_candidates() if belief is not None else {}
    except Exception:
        culprits = {}
    day = view.get("day")
    dpl = view.get("days_per_loop")
    final_day = (day is not None and dpl is not None and day >= dpl)
    virus_p = _rulex_prob(belief, "妄想拡大ウイルス") if belief is not None else 0.0
    contract_prob = 0.0
    butterfly_prob = 0.0
    if belief is not None:
        try:
            _rm = belief.rule_marginals()
            contract_prob = sum(p for (ry, _rxs), p in _rm.items()
                                if ry == "僕と契約しようよ！")
            butterfly_prob = sum(p for (ry, _rxs), p in _rm.items()
                                 if ry == "未来改変プラン")
        except Exception:
            contract_prob = butterfly_prob = 0.0
    threats = enumerate_threats(view, roles, supply_rumor=_rumor_active(belief),
                                rumor_left=rumor_left_for(belief, view),
                                options=options, culprits=culprits,
                                criticals=_criticals_for(view), final_day=final_day,
                                defeat_board_probs=_defeat_board_probs(
                                    belief, view, initial_areas=initial_areas),
                                virus_p=virus_p, contract_prob=contract_prob,
                                butterfly_prob=butterfly_prob,
                                include_breached=include_breached,
                                display_all=display_all, min_prob=min_prob)
    plan = plan_defenses(threats, seats=seats, card_turn_caps=card_turn_caps)
    plan.loop, plan.day, plan.stage = view.get("loop"), day, stage
    return threats, plan


# 敗北ボード↔ルールの対応（sim/effects.py で確認）。ボードXは動的（rule_y_board_x）。
_FIXED_DEFEAT_RULE_BOARD = {"守るべき場所": "学校", "封印されしモノ": "神社"}
_BOARDX_DEFEAT_RULES = ("復讐者の灯火", "巨大時限爆弾Xの存在")
# board_x系ルール → その板を決める役職（初期エリア）。主人公は「どの板がXか」を知らない
# （rule_y_board_x は脚本家view専用）＝belief の該当役職の居場所分布で全板へ散らす。
_BOARDX_ROLE = {"巨大時限爆弾Xの存在": "ウィッチ", "復讐者の灯火": "クロマク"}


def _defeat_board_probs(belief, view, initial_areas: dict | None = None) -> dict:
    """{board: P(その板が敗北ボード)}。belief の rule_marginals から算出。
    ルールが敗北ボードを持たない（切り裂き魔の影等）なら空＝ボード敗北脅威は出ない。

    ★board_x系（復讐者=クロマク／爆弾X=ウィッチ の初期エリアが敗北板）は「どの板がXか
    主人公は知らない」＝rule_y_board_x は脚本家view専用（AIA起票 task_11a54cc5・穴②）。
    従来は主人公view に無いので board_x 系の板脅威が丸ごと脱落し、暗躍禁止の空振り・反復板負けの
    根本原因だった。ここを **belief の該当役職(ウィッチ/クロマク)の居場所分布で全板へ散らす**＝
    主人公視点でも board_x 板脅威が見えるようにする。役職候補が消えていれば真相板(rule_y_board_x)へ。
    ★共通ヘルパ＝attack_plan._belief_defeat_board_probs もこれに委譲（二重定義を排除）。

    ★★B-103 論点B（2026-07-30・ユーザー実戦FB「その板が敗北ボードでないことは確定している」）：
    board_x を散らす先は**現在地ではなく「初期エリア」**である。
      - 復讐者の灯火＝「X は**クロマクの初期エリア**に等しい」（`rules/40_first_steps.md:41`）
      - 巨大時限爆弾Xの存在＝「X は**ウィッチの初期エリア**に等しい」（`rules/50:52`）
    現在地を使うと (a) 容疑者が歩いて入っただけの板に**幻の P>0** が立ち（＝
    「この板は敗北条件に絡まない」という**確定情報を打ち消してしまう**）、
    (b) 容疑者が離れた本物のボードXの P が**0 に落ちる**。
    初期エリアは**キャラクターカードに書かれた公開情報**（`engine.data.initial_area_of`）＝
    推定ではない。手先/従者だけは脚本家がループ毎に指定＝カードからは決まらないので、
    ループ初日の盤面観測（これも公開）で補う。
    ※同じ根拠は `heuristic_protagonist._guess_defeat_board`（点推定）が既に使っている
      （`_loop_initial_areas`）＝分布側だけが取り残されていた。

    ★**B-106（2026-07-30）＝この補正は既定で適用される**（全ての呼び出し側に効く）。
      優先順（すべて公開情報・推定を混ぜない）：
        1. `initial_areas`（呼び出し側が観測した初期配置）＝**手先/従者**のように脚本家が
           ループ毎/配置時に初期エリアを指定するキャラ（`rules/30:23`・`rules/60: A11`）を拾う。
           主人公AIは `HeuristicProtagonist._initial_area_map()` が作った写像を渡す。
        2. `engine.data.initial_area_of`＝**キャラクターカード記載の初期エリア**（公開情報）。
        3. どちらも無ければ**現在地**（＝判定不能時の健全側フォールバック）。
      B-103 の初版（`f792e22`）はこれを opt-in にしていたが、その**唯一の理由は
      `tests/test_attack_plan.py` の合成 fixture が KB違反（ボードX＝現在地）を前提に
      していたこと**だった（規約§8「レーン外のテストは勝手に直さない」に従った回避）。
      B-106 で fixture を KB 準拠に直したので、**規則どおり既定で適用する**。
      `attack_plan`（脚本側の助言ツール）も「**主人公の belief**」をモデル化する側であり、
      初期エリアはカードに書かれた公開情報＝主人公も知っている＝初期エリアで散らすのが正しい。
    """
    if belief is None:
        return {}
    try:
        rm = belief.rule_marginals()
    except Exception:
        return {}
    try:
        rolem = belief.role_marginals()
    except Exception:
        rolem = {}
    areas_of = {}
    for c in view.get("characters", []):
        n = c.get("name")
        # 観測した初期配置 → カードの初期エリア → 現在地（健全側フォールバック）
        areas_of[n] = ((initial_areas or {}).get(n) or initial_area_of(n)
                       or c.get("area"))
    probs: dict = {}
    for (ry, _rxs), p in rm.items():
        fixed = _FIXED_DEFEAT_RULE_BOARD.get(ry)
        if fixed is not None:
            probs[fixed] = probs.get(fixed, 0.0) + p
            continue
        role = _BOARDX_ROLE.get(ry)
        if not role:
            continue   # 敗北ボードを持たないルール
        loc: dict = {}
        tot = 0.0
        for c, dist in (rolem or {}).items():
            pr = dist.get(role, 0.0)
            a = areas_of.get(c)
            if pr > 0 and a in _AREAS:
                loc[a] = loc.get(a, 0.0) + pr
                tot += pr
        if tot > 0:                                  # 役職の居場所分布で板へ散らす
            for a, w in loc.items():
                probs[a] = probs.get(a, 0.0) + p * (w / tot)
        elif view.get("rule_y_board_x"):             # 候補が消えた/脚本家view＝真相板へ
            bx = view["rule_y_board_x"]
            probs[bx] = probs.get(bx, 0.0) + p
    return probs


# ---------------------------------------------------------------------------
# プランナー：全脅威を1つずつ折る最小コスト集合（貪欲 hitting set）
# ---------------------------------------------------------------------------
def plan_defenses(threats: list[Threat], *, seats: int = 3,
                  card_turn_caps: dict | None = None) -> Plan:
    """severity 降順に、各脅威を最安の折る手で覆う。既に置いた手が別の脅威も折るなら再利用。

    seats＝この局面で置ける防御枚数の上限（主人公3席＝既定3）。
    card_turn_caps＝カード別の同ターン設置上限（例 {"暗躍禁止": 1}）。二正面（暗躍禁止を
      要する脅威が2本）を「枚数不足で未防御」と正しく検出させる opt-in の資源制約。
      ★既定 None＝従来どおり seats のみ（AI決定への配線を不変に保つ＝挙動完全一致）。
    """
    picks: list[Break] = []
    covered: dict[int, str] = {}
    uncovered: list[Threat] = []
    placed_keys: set = set()   # (card,target,target_kind) 既に置いた手＝再利用可
    card_counts: dict = {}     # card -> このターンに置いた枚数（caps 判定用）
    pick_heat: dict = {}

    def _key(b: Break):
        return (b.card, b.target, b.target_kind)

    def _cap_ok(b: Break) -> bool:
        """このカードを更に1枚置けるか（caps 未指定 or 上限未設定なら常に可）。"""
        if not card_turn_caps:
            return True
        cap = card_turn_caps.get(b.card)
        return cap is None or card_counts.get(b.card, 0) < cap

    def _heat(key, t: Threat) -> None:
        if t.fatal:
            pick_heat[key] = max(pick_heat.get(key, 0.0), t.prob)

    for t in threats:
        if not t.defendable:
            uncovered.append(t)
            continue
        cheap = t.cheapest_breaks()
        # 既に置いた手でこの脅威を折れるなら追加コストゼロ
        reuse = next((b for b in cheap if _key(b) in placed_keys), None)
        if reuse is not None:
            covered[id(t)] = reuse.label + "（既存手で兼ねる）"
            _heat(_key(reuse), t)
            continue
        # caps 未指定なら cheap 全体が候補（＝従来と完全一致）。指定時はカード上限で絞る。
        feasible = [b for b in cheap if _cap_ok(b)]
        if not feasible or len(placed_keys) >= seats:
            uncovered.append(t)          # 枚数/カード上限が尽きた＝守り切れない（二正面）
            continue
        best = min(feasible, key=lambda b: b.cost)
        picks.append(best)
        placed_keys.add(_key(best))
        card_counts[best.card] = card_counts.get(best.card, 0) + 1
        covered[id(t)] = best.label
        _heat(_key(best), t)

    total = sum(b.cost for b in picks)
    return Plan(picks, covered, uncovered, total, pick_heat)


# ---------------------------------------------------------------------------
# markdown レンダ（内省パネル／人間=脚本家モードのデバッグ表示に差し込む）
# ---------------------------------------------------------------------------
def _break_basis(card: str) -> str:
    """折り手の根拠カテゴリ（位置/暗躍/不安/友好）＝主人公AIがどの軸で折るか。"""
    if card == "移動禁止" or card.startswith("移動"):
        return "位置"
    if card == "暗躍禁止":
        return "暗躍供給"
    if card in ("不安-1", "不安+1"):
        return "不安（冷却）"
    if card.startswith("友好"):
        return "友好"
    return card


def _pick_for(plan: Plan, t: Threat):
    """この脅威を覆った採用手（Break）を返す（既存手の兼用も拾う）。無ければ None。"""
    if id(t) not in plan.covered:
        return None
    for b in t.cheapest_breaks():
        if b.label in plan.covered[id(t)] or plan.covered[id(t)].startswith(b.label):
            return b
    return t.cheapest_breaks()[0] if t.cheapest_breaks() else None


def render_plan_md(threats: list[Threat], plan: Plan, *, detailed: bool = True,
                   max_rows: int | None = 20) -> str:
    """防御プランナーの結果を markdown 化。detailed=True で各脅威の
    使用カード・対象・コスト・根拠（位置/暗躍/不安/友好）・堅牢性まで全表示する（#6）。
    max_rows＝表の最大行数（None＝無制限＝全件表示。ユーザー要望 2026-07-13）。"""
    _cap = max_rows if max_rows is not None else 10 ** 9
    lines = ["##### 🛡 防御プランナー（脅威→最安の折り手）"]
    # ★どの時点の盤面を見た計画か（B-1・2026-07-13）。プランナーは脚本家が今ターン伏せた札を
    #   含む盤面を見る＝ユーザーの「日の頭」とはズレるので明記する。
    if plan.loop is not None and plan.day is not None:
        _st = f"・{plan.stage}" if plan.stage else ""
        lines.append(f"_この計画の時点：L{plan.loop}・D{plan.day}{_st}"
                     "（脚本家が今ターン伏せた札の位置を反映した盤面）_")
    if not threats:
        lines.append("_いま火の点きうる致命的な負け筋は検出なし。_")
        return "\n".join(lines)
    lines.append("")
    if detailed:
        # ★#4：実在度が低くても候補を広く表示（脅威ごとに代替の折り手も行展開）。
        lines.append("| 負け筋 | 実在度 | タイミング | 折り手 | 対象 | 根拠 | 堅牢 | コスト |")
        lines.append("|---|---|---|---|---|---|---|---|")
        rows = 0
        for t in threats:
            if rows >= _cap:
                break
            p = f"{t.prob:.0%}"
            chosen = _pick_for(plan, t)
            # 全ての折り手（採用手を先頭に・重複除去）を候補として並べる
            all_breaks: list[Break] = []
            seen_keys: set = set()
            for c in t.conditions:
                for b in c.breaks:
                    k = (b.card, b.target, b.target_kind)
                    if k not in seen_keys:
                        seen_keys.add(k)
                        all_breaks.append(b)
            all_breaks.sort(key=lambda b: (0 if chosen and (b.card, b.target,
                            b.target_kind) == (chosen.card, chosen.target,
                            chosen.target_kind) else 1, b.cost))
            if not all_breaks:
                if t.breached:
                    mark, reason = "💀突破済み", "このループ突破済み"
                elif t.race:
                    mark, reason = "🏃レース", "移動可クロマク＝剥がせば止まる（今ターン折り手なし）"
                elif not t.defendable:
                    mark, reason = "⛔防御不能", "レース対象（止まらない供給）"
                else:
                    mark, reason = "⚠枚数不足で未防御", "—"
                lines.append(f"| {t.label} | {p} | {t.timing} | {mark} | — | "
                             f"{reason} | — | — |")
                rows += 1
                continue
            for i, b in enumerate(all_breaks[:3]):   # 代替を最大3つまで
                if rows >= _cap:
                    break
                is_pick = chosen and (b.card, b.target, b.target_kind) == (
                    chosen.card, chosen.target, chosen.target_kind)
                head = t.label if i == 0 else "　└ 別の折り手"
                pp = p if i == 0 else ""
                tm = t.timing if i == 0 else ""
                act = ("✅" if is_pick else "◽") + b.card
                robust = "堅" if b.robust else "追撃可"
                lines.append(f"| {head} | {pp} | {tm} | {act} | {b.target} | "
                             f"{_break_basis(b.card)} | {robust} | {b.cost:.1f} |")
                rows += 1
    else:
        lines.append("| 負け筋 | 実在度 | 折り手（防御） |")
        lines.append("|---|---|---|")
        for t in threats:
            p = f"{t.prob:.0%}"
            if t.breached:
                act = "💀 突破済み"
            elif id(t) in plan.covered:
                act = "✅ " + plan.covered[id(t)]
            elif t.race:
                act = "🏃 レース（移動で剥がす）"
            elif not t.defendable:
                act = "⛔ 防御不能（レースで勝つ）"
            else:
                act = "⚠ 枚数不足で未防御"
            lines.append(f"| {t.label} | {p} | {act} |")
    # ★uncovered を「突破済み(💀)」「真に防御不能(⛔)」「レース＝位置/移動で勝てる(🏃)」
    #   「枚数不足で未防御(⚠・折り手はあるが席不足)」に分けて表示する（B-1・2026-07-13）。
    def _reason(t):
        rs = [c.note for c in t.conditions if c.note]
        return ("｜" + " / ".join(rs)) if rs else ""
    breached_t = [t for t in threats if t.breached]
    unstoppable = [t for t in plan.uncovered
                   if not t.defendable and not t.race and not t.breached]
    race_t = [t for t in plan.uncovered if t.race and not t.breached]
    shortfall = [t for t in plan.uncovered if t.defendable]
    if breached_t:
        lines.append("")
        lines.append("**💀 突破済みの負け筋**（既に暗躍≥2等＝このループは成立・もう防げない）：")
        for t in breached_t:
            lines.append(f"- {t.label}")
    if unstoppable:
        lines.append("")
        lines.append("**⛔ 防御不能な脅威**（暗躍禁止でも移動でも止まらない供給）＝"
                     "守るのでなく事件抑制/情報収穫で**レースを勝つ**対象：")
        for t in unstoppable:
            lines.append(f"- {t.label}{_reason(t)}")
    if race_t:
        lines.append("")
        lines.append("**🏃 レース（位置/移動で勝てる）**（移動可クロマク供給等＝今ターン折り手が"
                     "無くても、移動で剥がせば止まる／別席・別ターンで対処）：")
        for t in race_t:
            lines.append(f"- {t.label}{_reason(t)}")
    if shortfall:
        lines.append("")
        lines.append("**⚠ 枚数不足で未防御**（折り手はあるが席・暗躍禁止1枚/ターン等で"
                     "二正面を今ターン覆え切れない）＝優先度の低い方を捨てるかレースで勝つ：")
        for t in shortfall:
            lines.append(f"- {t.label}")
    lines.append("")
    lines.append(f"_採用防御 {len(plan.picks)} 枚・総コスト {plan.total_cost:.1f}"
                 f"（席3枚まで／脅威 {len(threats)} 件中 覆えた {len(plan.covered)}・"
                 f"防御不能 {len(plan.uncovered)}）_")
    return "\n".join(lines)
