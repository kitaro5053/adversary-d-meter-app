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
    breached: bool = False   # 既に敗北判定域（板暗躍≥2等）＝突破済み。💀 表示・意思決定は覆えない。
    race: bool = False       # 「防御不能」ではなくレース（移動可クロマク供給等＝位置/情報で勝つ）。

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
                     roles: dict | None = None) -> list:
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
        out.append(Break(label_fmt.format(n=name, a=area, how="移動"),
                         mc, name, "character", cost, robust=False))
    if not out and area is not None:              # 幻想＝ボード移動で動かす
        for mc in opts.board_move_cards(area):
            out.append(Break(label_fmt.format(n=name, a=area, how="ボード移動"),
                             mc, area, "board", cost, robust=False))
    return out


def _alive(view: dict, name: str) -> bool:
    c = _char(view, name)
    return bool(c and c.get("alive", True))


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
def _unstoppable_supply(view: dict, area: str, roles: dict) -> bool:
    """暗躍禁止で止まらない暗躍供給が area に効きうるか（保守的：疑いがあれば True）。

    - クロマク容疑者が area または同ボードに居る → 止まらない供給。
    - 黒猫（神社にループ開始時+1）は area=='神社' で常に警戒。
    - 不穏な噂は任意ボード（場所を問わない）＝ルール確有なら全エリアで警戒だが、
      belief では rule_marginals を要するため呼び出し側から supply_rumor で渡す。
    """
    if area == "神社":
        # 黒猫のループ開始時暗躍は神社固定＝供給源として常に警戒（居れば実効）
        for name, p in _suspects(roles, "黒猫", _SUSPECT_P).items():
            if _alive(view, name):
                return True
    for name, p in _suspects(roles, "クロマク", _SUSPECT_P).items():
        c = _char(view, name)
        if c and c.get("alive", True) and c.get("area") == area:
            return True
    return False


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


def _add_kinshi_break(c1: "Condition", opts, area, view, roles, label) -> None:
    """area の行動解決暗躍を断つ『暗躍禁止』Breakを、カルティスト無効化を考慮して足す（B-8）。
    カルティスト疑いが area に居ると暗躍禁止は無視されうる＝robust=False に落とし、
    カルティストを移動で剥がす（暗躍禁止を有効化する）折り手を優先提示する。"""
    if not opts.has_board("暗躍禁止", area):
        return
    cult = _cultists_on_area(view, roles, area)
    if cult:
        for cu in cult:
            c1.breaks.extend(_relocate_breaks(
                opts, cu, area,
                "{n}（カルティスト疑い）を{a}から{how}で剥がす（暗躍禁止を有効化）", 1.4,
                view=view, roles=roles))
        c1.breaks.append(Break(
            f"{area}に暗躍禁止（★カルティスト疑いが同ボードで無視しうる＝堅くない）",
            "暗躍禁止", area, "board", 0.5, robust=False))
    else:
        c1.breaks.append(Break(label, "暗躍禁止", area, "board", 1.0, robust=True))


def _add_anyaku_supply_breaks(c1: "Condition", view, roles, opts, victim,
                               area, supply_rumor) -> None:
    """area の暗躍供給を止める折り手を c1 に全部足す：
      (1) その盤面に暗躍禁止（行動解決の暗躍カードを断つ）
      (2) クロマク疑いを移動で的エリアから剥がす（同エリア/自ボードにしか置けない）
      (3) victim を的エリアから移動で逃がす（クロマクの射程外へ）
    真に止まらない（不穏な噂／黒猫のみ）＆折る手が無いときだけ note（レース対象）。"""
    rumor = supply_rumor and area != "都市"          # 任意ボード（暗躍禁止で止まらない）
    kuroneko = area == "神社" and any(               # 神社固定（暗躍禁止で止まらない）
        _alive(view, n) for n in _suspects(roles, "黒猫", _SUSPECT_P))
    cultists = [n for n in _suspects(roles, "クロマク", _SUSPECT_P)
                if (_char(view, n) or {}).get("area") == area and _alive(view, n)]
    # クロマク/噂/黒猫は mm能力フェイズ＝暗躍禁止で止まらない。移動でしか対処できない。
    if cultists:
        for cu in cultists:                          # クロマクを的から剥がす（幻想はボード移動）
            c1.breaks.extend(_relocate_breaks(
                opts, cu, area, "{n}（クロマク疑い）を{a}から{how}で剥がす", 1.6,
                view=view, roles=roles))
        if victim:                                    # victim をクロマクの射程外へ（A.I.は不可）
            c1.breaks.extend(_relocate_breaks(
                opts, victim, area, "{n}を{a}から{how}でクロマクの射程外へ逃がす", 1.5,
                view=view, roles=roles))
    elif not rumor and not kuroneko and area is not None:
        # 行動解決の暗躍カードのみ＝暗躍禁止で断てる（★カルティスト無効化を考慮＝B-8）
        _add_kinshi_break(c1, opts, area, view, roles,
                          f"{area}に暗躍禁止（行動解決の暗躍を断つ）")
    if not c1.breaks:
        c1.note = ("不穏な噂/黒猫＝暗躍禁止でも止まらない供給（レース対象）"
                   if (rumor or kuroneko) else
                   "クロマク供給を今ターン折る移動札が手札に無い（クロマクは暗躍禁止で止まらない）"
                   if cultists else
                   "この供給を今ターン折る手が手札に無い（暗躍禁止/移動を確保）")


def _threat_factor_kp(view, roles, opts, mm_chars: set = frozenset()) -> list[Threat]:
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
        if toshi < 2 and opts.has_board("暗躍禁止", "都市"):
            c_acq.breaks.append(Break("都市に暗躍禁止＝ファクターのKP能力獲得を止める",
                                      "暗躍禁止", "都市", "board", 1.0, robust=True))
        c_kill = Condition("KP能力獲得ファクターがキラーに殺される（同エリア）")
        for mc in opts.move_cards_for(fa):
            if not _noop_char(view, mc, fa, roles):
                c_kill.breaks.append(Break(f"{fa}をキラーから移動で引き離す",
                                           mc, fa, "character", 1.3, robust=False))
        for k in near:
            if k in mm_chars and opts.has_char("移動禁止", k) \
                    and not _cannot_move_now(view, k):
                c_kill.breaks.append(Break(f"{k}を移動禁止で固定（追撃を封じる）",
                                           "移動禁止", k, "character", 1.1, robust=True))
        out.append(Threat("factor_kp", f"KP能力獲得ファクター{fa}＝死亡でループ終了",
                          prob, True, "ターン終了フェイズ", [c_acq, c_kill]))
    return out


def _threat_kp_killer(view, roles, opts, supply_rumor,
                      mm_chars: set = frozenset()) -> list[Threat]:
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
        if not _cannot_move_now(view, kp):   # B-21b：動けないKPは移動で引き離せない
            for mc in opts.move_cards_for(kp):
                if _noop_char(view, mc, kp, roles):
                    continue    # B-31/G4/2c：空振り or 危険な退避先（致死zone/送り込み）
                c1.breaks.append(Break(f"{kp}を{area}から移動で引き離す", mc, kp,
                                       "character", 1.3, robust=False))  # 追撃されうる
        for k in near:
            # ★B-26：移動禁止は「そのキャラに載った今ターンの移動カード」しか打ち消せない＝
            #   mmがそのキラーに札を伏せていなければ（k not in mm_chars）追撃移動そのものが存在せず
            #   ピンは証明可能な空振り（mmのセット位置は公開情報）。heuristic 側は自前の killer pin に
            #   同ゲート（tgt in mm_char_now）を持つが、planの折り手にゲートが無く PLAN_HOT 加点が
            #   上書きしていた（BTX seed14 実測：mm札の無い転校生への移動禁止が 89.0 で採用）。
            if k in mm_chars and opts.has_char("移動禁止", k) \
                    and not _cannot_move_now(view, k):
                c1.breaks.append(Break(f"{k}を移動禁止で固定（追撃を封じる）",
                                       "移動禁止", k, "character", 1.1, robust=True))
        # 条件2：KP暗躍が2に届く（暗躍禁止／クロマク剥がし／KP退避で供給を断つ）
        c2 = Condition("KPの暗躍が2に届く")
        cur = kc.get("anyaku", 0)
        if cur < 2:
            _add_anyaku_supply_breaks(c2, view, roles, opts, kp, area, supply_rumor)
        note = "KP暗躍が既に2以上＝供給側は折れない。引き離しで防ぐ" if cur >= 2 else ""
        out.append(Threat("kp_killer", f"キラーによる{kp}殺害（{area}）", prob,
                          True, "ターン終了フェイズ", [c1, c2], note))
    return out


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
        # VIPを移動で逃がす（A.I.等の実質移動不可キャラは提示しない）
        c1.breaks.extend(_relocate_breaks(opts, vip, area, "{n}を{a}から逃がす", 1.2,
                                          view=view, roles=roles))
        for sk in sk_here:                     # SK本体を移動で剥がす
            c1.breaks.extend(_relocate_breaks(
                opts, sk, area, "{n}(" + suspect_word + ")を{a}から動かす", 1.3,
                view=view, roles=roles))
        for other in occupants:                # 第三者を留めて2人きりを崩す
            # ★B-26：mmがその第三者に札を伏せていなければ引き抜き移動そのものが無い＝
            #   留めるピンは証明可能な空振り（mmのセット位置は公開情報）。
            if other not in (vip,) and other not in sk_here \
                    and other in mm_chars \
                    and opts.has_char("移動禁止", other) \
                    and not _cannot_move_now(view, other):   # B-21：動けない駒の留めは無意味
                c1.breaks.append(Break(f"{other}を移動禁止で{area}に留め2人きりを崩す",
                                       "移動禁止", other, "character", 1.1, robust=True))
        role = "KP" if vip in _suspects(roles, "キーパーソン") else "フレンド"
        out.append(Threat(kind, f"{label_word}による{vip}（{role}）殺害（2人きり・{area}）",
                          prob, True, "ターン終了フェイズ", [c1]))
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
            if sk_t and opts.has_char("移動禁止", sk) and not _cannot_move_now(view, sk):
                c1.breaks.append(Break(f"{sk}を移動禁止（SKへの伏せ札の移動を打ち消す）",
                                       "移動禁止", sk, "character", 1.0, robust=True))
            if kp_t and opts.has_char("移動禁止", kp) and not _cannot_move_now(view, kp):
                c1.breaks.append(Break(f"{kp}を移動禁止（KPへの伏せ札の移動を打ち消す）",
                                       "移動禁止", kp, "character", 1.0, robust=True))
            # ★移動禁止を持っていなくても、移動で自ら引き離す/送り返す（#5・移動でがんばる）。
            #   ★B-21b：動けない駒（恒久移動不能・当ループ未解除）は移動で崩せない＝出さない。
            if not _cannot_move_now(view, kp):
                for mc in opts.move_cards_for(kp):
                    if _noop_char(view, mc, kp, roles):
                        continue    # B-31/G4/2c：空振り/危険
                    c1.breaks.append(Break(f"{kp}を移動で{sk}から離す（2人きりを崩す）",
                                           mc, kp, "character", 1.3, robust=False))
            if not _cannot_move_now(view, sk):
                for mc in opts.move_cards_for(sk):
                    if _noop_char(view, mc, sk, roles):
                        continue    # B-31/G4/2c：空振り/危険
                    c1.breaks.append(Break(f"{sk}(SK疑い)を移動で動かす（2人きりを崩す）",
                                           mc, sk, "character", 1.4, robust=False))
            for m in sorted(touched & (kp_mates | sk_mates)):
                if opts.has_char("移動禁止", m) and not _cannot_move_now(view, m):
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
                note="mmの今ターン配置が脅威の関係者と一致"))
    return out


# 事件→打点が乗る敗北ボード（暗躍禁止/移動で止まらない＝犯人冷却が唯一の折り手・B-10）。
_INCIDENT_BOARD_DAMAGE = {"邪気の汚染": "神社"}   # 封印×邪気＝神社+2（負け筋防御ツリー §C-3）


def _add_incident_board_cooling(c1: "Condition", view, opts, area,
                                culprits, criticals) -> None:
    """area に事件由来の打点（邪気の汚染=神社+2 等）が乗る予定なら、その犯人候補を不安-1で
    冷やす折り手を c1 に足す（B-10）。事件打点は暗躍禁止/移動で止まらない＝犯人冷却が唯一。"""
    day_now = view.get("day", 1)
    for inc in view.get("incidents", []):
        nm, iday = inc.get("name"), inc.get("day")
        if _INCIDENT_BOARD_DAMAGE.get(nm) != area or iday is None or iday < day_now:
            continue
        added = False
        for cand in (culprits or {}).get(iday, set()):
            if _alive(view, cand) and opts.has_char("不安-1", cand) \
                    and not _noop_char(view, "不安-1", cand):   # B-28 G2：床0＋mm札なしは空振り
                c1.breaks.append(Break(
                    f"{nm}の犯人候補{cand}を不安-1で冷やし{area}への事件打点を止める",
                    "不安-1", cand, "character", 1.5, robust=True))
                added = True
        if not added and not c1.breaks and not c1.note:
            c1.note = (f"{nm}の事件打点（{area}+2）＝暗躍禁止/移動で止まらない・"
                       "犯人冷却が唯一だが今ターン折り手が手札に無い")


def _threat_board_defeat(view, roles, opts, supply_rumor,
                         defeat_board_probs, culprits=None, criticals=None,
                         include_breached=False, display_all=False) -> list[Threat]:
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
            if include_breached:
                out.append(Threat("board_defeat", f"{area}のボード敗北（突破済み・暗躍{cur}）",
                                  rule_p, True, "ループ終了フェイズ",
                                  [Condition(f"{area}の暗躍が既に{cur}（≥2）")],
                                  note="既に敗北判定域＝この板はこのループ突破済み",
                                  breached=True))
            continue
        c1 = Condition(f"{area}の暗躍が2に届く")
        # ★真に止まらない供給＝不穏な噂（任意ボード）／黒猫（神社固定・移動対象でない）。
        truly_unstoppable = (supply_rumor and area != "都市") or (
            area == "神社" and any(
                _alive(view, n) for n in _suspects(roles, "黒猫", _SUSPECT_P)))
        # クロマクは「同エリア/自ボード」にしか置けない＝的エリアに居るクロマク疑いを
        # 移動で剥がせば供給が止まる（#7・負け筋防御ツリー§A「クロマクを的外へ〔難〕」）。
        cultists_here = [n for n in _suspects(roles, "クロマク", _SUSPECT_P)
                         if (_char(view, n) or {}).get("area") == area
                         and _alive(view, n)]
        race = False
        if truly_unstoppable:
            c1.note = "不穏な噂/黒猫＝暗躍禁止でも移動でも止まらない供給（レース対象）"
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
            # ★カルティスト無効化を考慮した暗躍禁止Break（B-8）
            _add_kinshi_break(c1, opts, area, view, roles,
                              f"{area}に暗躍禁止（行動解決の暗躍供給を断つ）")
        # ★B-10：事件由来のボード打点（邪気の汚染=神社+2）＝暗躍禁止/移動で止まらない＝犯人冷却を足す
        _add_incident_board_cooling(c1, view, opts, area, culprits, criticals)
        # 実在度＝P(敗北ルール) × ボードの育ち具合（暗躍0なら遠い）
        progress = 0.9 if cur >= 1 else 0.35
        threat_p = rule_p * progress
        out.append(Threat("board_defeat", f"{area}のボード敗北", threat_p,
                          True, "ループ終了フェイズ", [c1], race=race))
    return out


def _threat_kp_anyaku(view, roles, opts, supply_rumor, contract_prob,
                      display_all: bool = False) -> list[Threat]:
    """僕と契約しようよ！：KP暗躍≥2 単独でループ終了時敗北（キラー不要・位置無関係）。
    ボード敗北と同型の「暗躍≥2の評価敗北」＝暗躍禁止/クロマク剥がし/供給停止でしか止まらない
    （移動でKPを逃がしても暗躍は乗る＝位置防御は無効）。contract_prob＝P(rule=僕と契約)。"""
    if contract_prob < 0.05 and not display_all:
        return []
    out = []
    for kp, pkp in _suspects(roles, "キーパーソン").items():
        kc = _char(view, kp)
        if not kc or not kc.get("alive", True) or kc.get("area") is None:
            continue
        cur = kc.get("anyaku", 0)
        if cur >= 2:
            continue  # 既に敗北域＝供給停止では折れない
        c1 = Condition("KPの暗躍が2に届く（僕と契約）")
        _add_anyaku_supply_breaks(c1, view, roles, opts, kp, kc["area"], supply_rumor)
        progress = 0.9 if cur >= 1 else 0.35
        out.append(Threat("kp_anyaku", f"僕と契約＝{kp}の暗躍2でループ敗北",
                          contract_prob * pkp * progress, True,
                          "ループ終了フェイズ", [c1]))
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
                              display_all: bool = False) -> list[Threat]:
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
        _add_anyaku_supply_breaks(c1, view, roles, opts, k, c.get("area"), supply_rumor)
        prob = pk * (0.7 if cur >= 3 else 0.4)
        out.append(Threat("killer_protagonist", f"キラー{k}の暗躍4＝主人公死亡", prob,
                          True, "ターン終了フェイズ", [c1]))
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
        if unrest < 3 or opts.has_char("不安-1", m):
            if opts.has_char("不安-1", m) and not _noop_char(view, "不安-1", m):
                c1.breaks.append(Break(f"{m}を不安-1で冷やし3未満に保つ",
                                       "不安-1", m, "character", 1.2, robust=True))
        prob = pm * min(1.0, (unrest / 3.0)) * (1.0 if anr >= 1 else 0.4)
        out.append(Threat("mainlover_protagonist", f"メインラバーズ{m}＝主人公死亡", prob,
                          True, "ターン終了フェイズ", [c1]))
        # ★G6（B-11）：恋愛連鎖の予防。ラバーズ死亡→メインラバーズに不安+6（KB:50）→暗躍≥1で
        #   主人公殺害。+6は大きく冷却(-1)で追いつけない＝現在不安が低くても危険。メインラバーズが
        #   暗躍≥1（＝+6で即臨界域）かつラバーズ疑いが生存している時、予防的に脅威化する。
        #   折り手＝メインラバーズの暗躍を0に保つ（+6でも暗躍0なら殺害不成立）／ラバーズを死の
        #   危険（病院の事件ゾーン等）から守る（ラバーズはVIP集合外＝従来保護されない）。
        lovers = [n for n in _suspects(roles, "ラバーズ") if _alive(view, n)]
        if anr >= 1 and lovers:
            c2 = Condition("ラバーズ死亡→メインラバーズ+6→暗躍≥1で主人公殺害（恋愛連鎖）")
            _add_anyaku_supply_breaks(c2, view, roles, opts, m, c.get("area"), supply_rumor)
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
                              pm * pl * 0.4, True, "ターン終了フェイズ", [c2]))
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
            for mc in opts.move_cards_for(vip):
                if _noop_char(view, mc, vip, roles):
                    continue    # B-31/G4：行き先が禁止＝空振り札
                c_eff.breaks.append(Break(f"{vip}を{danger_area}から移動で退避",
                                          mc, vip, "character", 1.3, robust=False))
            c_occ = Condition("犯人の不安が臨界に届く（発生条件）")
            for cand in live_culprits:
                if opts.has_char("不安-1", cand) \
                        and not _noop_char(view, "不安-1", cand):
                    c_occ.breaks.append(Break(f"犯人候補{cand}を不安-1で冷やし発生を止める",
                                              "不安-1", cand, "character", 1.4, robust=True))
            out.append(Threat("incident_vip", f"{nm}による{vip}殺害（{danger_area}）",
                              prob, True, "事件フェイズ", [c_eff, c_occ]))
    return out


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
            if anr < 1:
                continue  # 暗躍0＝2まで遠い（ノイズ抑制）。1以上で警戒。
            prog = 1.0 if anr >= 2 else 0.5   # 既に殺害圏(≥2) or あと1(=1)
            prob = pv * occ * prog
            c_eff = Condition(f"VIP{vip}に暗躍2が載る（遠隔殺人の対象）")
            # ★B-28 Step1（G1）：暗躍禁止は「そのキャラに載った今ターンの暗躍+」しか
            #   打ち消せない＝mmがそのVIPに札を伏せていなければ証明可能な空振り（mmのセット位置は
            #   公開情報）。既に載っている暗躍(anr>=1)は過去の蓄積＝今ターン打ち消せない。
            #   判定は card_effect.noop_reason（heuristic の空振りゲートと同一述語＝PLAN_HOT が
            #   heuristic の判断を上書きする事故の構造封鎖）。
            if anr < 2 and opts.has_char("暗躍禁止", vip) and noop_reason(
                    view, "暗躍禁止", vip, "character",
                    NoopCtx(mm_chars=frozenset(mm_chars))) is None:
                c_eff.breaks.append(Break(
                    f"{vip}に暗躍禁止で暗躍供給を断つ（殺害対象化を防ぐ）",
                    "暗躍禁止", vip, "character", 1.0, robust=True))
            c_occ = Condition("犯人の不安が臨界に届く（発生条件）")
            for cand in live_culprits:
                if opts.has_char("不安-1", cand) \
                        and not _noop_char(view, "不安-1", cand):
                    c_occ.breaks.append(Break(
                        f"犯人候補{cand}を不安-1で冷やし遠隔殺人を止める",
                        "不安-1", cand, "character", 1.4, robust=True))
            out.append(Threat("remote_murder_vip", f"遠隔殺人による{vip}殺害（暗躍2）",
                              prob, True, "事件フェイズ", [c_eff, c_occ]))
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
        for cand in live_culprits:
            if opts.has_char("不安-1", cand) \
                    and not _noop_char(view, "不安-1", cand):
                c_occ.breaks.append(Break(
                    f"犯人候補{cand}を不安-1で冷やし蝶の羽ばたきの発生を止める",
                    "不安-1", cand, "character", 1.4, robust=True))
        out.append(Threat("butterfly", "蝶の羽ばたき×未来改変プラン（発生で敗北へ）",
                          prob, True, "事件フェイズ", [c_occ]))
    return out


def _threat_hospital_protagonist_death(view, roles, opts, supply_rumor,
                                       culprits, criticals) -> list[Threat]:
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
        for cand in live_culprits:
            if opts.has_char("不安-1", cand) \
                    and not _noop_char(view, "不安-1", cand):
                c_occ.breaks.append(Break(
                    f"病院の事件の犯人候補{cand}を不安-1で冷やし発生を止める",
                    "不安-1", cand, "character", 1.4, robust=True))
        # 病院暗躍が2に届く条件（暗躍禁止/クロマク剥がしで折る）
        c_board = Condition("病院暗躍が2に届く（主人公死亡条件）")
        _add_anyaku_supply_breaks(c_board, view, roles, opts, None, "病院", supply_rumor)
        out.append(Threat("hospital_protagonist",
                          "病院の事件による主人公死亡（病院暗躍≥2）",
                          prob, True, "事件フェイズ", [c_occ, c_board]))
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
        for card in ("友好+2", "友好+1"):
            if opts.has_char(card, t):
                c1.breaks.append(Break(f"{t}に{card}（友好禁止を無視して載る）",
                                       card, t, "character", 1.1, robust=True))
                break
        out.append(Threat("tt_defeat", f"TT{t}の任意敗北（最終日）", pt,
                          True, "ループ終了フェイズ", [c1]))
    return out


def enumerate_threats(view: dict, role_marginals: dict, *,
                      supply_rumor: bool = False,
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
    options＝自チームが置ける手札候補（防御手の在庫。None なら折る手なし＝全部要防御表示）。
    culprits＝belief.culprit_candidates()（{day: set}）。criticals＝{name: 不安臨界}。
    final_day＝このターンがループ最終日か（TT任意敗北の判定用）。
    """
    opts = _Opts(options or [])
    threats: list[Threat] = []
    mm_chars, _mm_boards = _mm_touched(view)
    try:  # mmが今ターン キラー/KP に伏せた札で実在度昇格（バグ6）
        threats.extend(_threat_kp_killer(view, role_marginals, opts,
                                         supply_rumor, mm_chars))
    except Exception:
        pass
    try:  # B-34：KP能力獲得ファクター（都市暗躍≥2）の死亡＝KPと同じ即ループ終了
        threats.extend(_threat_factor_kp(view, role_marginals, opts, mm_chars))
    except Exception:
        pass
    for det in (_threat_killer_protagonist, _threat_mainlover_protagonist):
        try:
            threats.extend(det(view, role_marginals, opts, supply_rumor,
                               display_all=display_all))
        except Exception:
            continue
    try:  # ボード敗北はルール依存＝defeat_board_probs（空なら1件も出ない＝安全）
        threats.extend(_threat_board_defeat(view, role_marginals, opts,
                                            supply_rumor, defeat_board_probs or {},
                                            culprits=culprits or {},
                                            criticals=criticals or {},
                                            include_breached=include_breached,
                                            display_all=display_all))
    except Exception:
        pass
    try:  # ★僕と契約（KP暗躍≥2単独勝ち）＝ボード敗北と同型の評価敗北（contract_prob=0なら出ない）
        threats.extend(_threat_kp_anyaku(view, role_marginals, opts,
                                          supply_rumor, contract_prob,
                                          display_all=display_all))
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
            view, role_marginals, opts, supply_rumor, culprits or {}, criticals or {}))
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
                    display_all: bool = False, min_prob: float = 0.01):
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
                                options=options, culprits=culprits,
                                criticals=_criticals_for(view), final_day=final_day,
                                defeat_board_probs=_defeat_board_probs(belief, view),
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


def _defeat_board_probs(belief, view) -> dict:
    """{board: P(その板が敗北ボード)}。belief の rule_marginals から算出。
    ルールが敗北ボードを持たない（切り裂き魔の影等）なら空＝ボード敗北脅威は出ない。

    ★board_x系（復讐者=クロマク／爆弾X=ウィッチ の初期エリアが敗北板）は「どの板がXか
    主人公は知らない」＝rule_y_board_x は脚本家view専用（AIA起票 task_11a54cc5・穴②）。
    従来は主人公view に無いので board_x 系の板脅威が丸ごと脱落し、暗躍禁止の空振り・反復板負けの
    根本原因だった。ここを **belief の該当役職(ウィッチ/クロマク)の居場所分布で全板へ散らす**＝
    主人公視点でも board_x 板脅威が見えるようにする。役職候補が消えていれば真相板(rule_y_board_x)へ。
    ★共通ヘルパ＝attack_plan._belief_defeat_board_probs もこれに委譲（二重定義を排除）。"""
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
    areas_of = {c.get("name"): c.get("area") for c in view.get("characters", [])}
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
