"""FSフルシミュレータ — 脚本データとゲーム状態（GameState）。

設計方針（docs/AIプレイヤー計画.md, docs/M0_FS効果インベントリ.md）:
- 主人公は**常に3席（p1/p2/p3）**をモデル化する。基本形＝主人公3人＋脚本家1人で、
  少人数プレイは「1人が複数席を担当」するだけ（KB: 00 2〜3人で遊ぶ場合）。
  重ね置き禁止（MULTI_PROTAGONIST_TARGET）もリーダー交代も3席あってこそ意味を持つ。
- ルールの実装は engine/ と sim/effects.py に集約。このモジュールは状態の器と
  決定的な遷移（ループ準備・リセット・リーダー交代・手札管理）だけを持つ。
- 秘匿情報（配役・犯人・ルールY/X）は Script に閉じ、外に出すのは views.py の責務。
- 範囲は First Steps。不明・KB範囲外は ValueError（要確認）で弾き、創作しない。

KB対応:
- ループ準備（カウンター除去・初期配置・手札分配）: 00 ループの準備
- 手札構成: 00:89-93 / 10。脚本家の暗躍+1は1枚・不安+1が2枚（現物確認済 2026-07-03）。
- FSのルールY/X・役職・事件: 40
- 初期エリア: 30（engine/data.py CHARACTER_INITIAL_AREA に転記済み）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections import Counter

from engine.board import AREAS
from engine.data import (
    CHARACTER_INITIAL_AREA,
    forced_loop_start_anyaku,
    initial_area_of,
    role_has_friendship_ignore,
)
from engine.models import MASTERMIND_HAND, PROTAGONIST_HAND

# ---------------------------------------------------------------------------
# 定数（席・フェイズ・FS構成要素）。手札構成は engine/models.py が単一ソース。
# ---------------------------------------------------------------------------

PROTAGONIST_SEATS: tuple[str, ...] = ("p1", "p2", "p3")

# 1ターン9フェイズ（KB: 00 ループの進行）。engineの question phase 名とは別の進行キー。
PHASES: tuple[str, ...] = (
    "turn_start",         # 1 ターン開始［脚本家］
    "mastermind_set",     # 2 脚本家行動（3枚セット）
    "protagonist_set",    # 3 主人公行動（各席1枚・計3枚）
    "action_resolution",  # 4 行動解決（engine/resolver）
    "mastermind_ability", # 5 脚本家能力（engine/mm_phase）
    "goodwill_ability",   # 6 主人公能力（engine/goodwill）
    "incident",           # 7 事件（engine/incident + sim/effects）
    "leader_change",      # 8 リーダー交代
    "turn_end",           # 9 ターン終了（強制→任意の役職能力・ループ終了処理）
)

# FSのルールが追加する役職スロット（KB: 40）。マイナスのみ0〜2で可変（40:66）。
FS_RULE_Y_ROLES: dict[str, tuple[str, ...]] = {
    "殺人計画": ("キーパーソン", "クロマク", "キラー"),
    "復讐者の灯火": ("クロマク",),
    "守るべき場所": ("キーパーソン", "カルティスト"),
}
FS_RULE_X_ROLES: dict[str, tuple[str, ...]] = {
    "切り裂き魔の影": ("ミスリーダー", "シリアルキラー"),
    "不穏な噂": ("ミスリーダー",),
    "最低の却本": ("ミスリーダー", "マイナス", "マイナス", "フレンド"),
}
FS_INCIDENTS: frozenset[str] = frozenset(
    {"殺人事件", "不安拡大", "自殺", "病院の事件", "遠隔殺人", "行方不明", "流布"}
)
# BTX（→50）：ルールY5種・ルールX7種・事件はFS7種＋邪気の汚染/蝶の羽ばたき。
BTX_RULE_Y_ROLES: dict[str, tuple[str, ...]] = {
    "殺人計画": ("キーパーソン", "クロマク", "キラー"),
    "封印されしモノ": ("クロマク", "カルティスト"),
    "僕と契約しようよ！": ("キーパーソン",),
    "未来改変プラン": ("カルティスト", "タイムトラベラー"),
    "巨大時限爆弾Xの存在": ("ウィッチ",),
}
BTX_RULE_X_ROLES: dict[str, tuple[str, ...]] = {
    "友情サークル": ("フレンド", "フレンド", "ミスリーダー"),
    "恋愛風景": ("ラバーズ", "メインラバーズ"),
    "潜む殺人鬼": ("フレンド", "シリアルキラー"),
    "不穏な噂": ("ミスリーダー",),
    "妄想拡大ウイルス": ("ミスリーダー",),
    "因果の糸": (),
    "不定因子χ": ("ファクター",),
}
BTX_INCIDENTS: frozenset[str] = FS_INCIDENTS | {"邪気の汚染", "蝶の羽ばたき"}
# 最後の戦いで主人公が宣言しうる役職の全体（BTXの全役職＋パーソン）。KB: 50。
BTX_ROLE_UNIVERSE: tuple[str, ...] = (
    "キーパーソン", "クロマク", "キラー", "カルティスト", "タイムトラベラー", "ウィッチ",
    "ミスリーダー", "フレンド", "ラバーズ", "メインラバーズ", "シリアルキラー", "ファクター",
    "パーソン",
)
# FSの役職ユニバース（FS 6ルールが追加しうる役職の全体＝イレギュラーの配役候補判定に使用）。
FS_ROLE_UNIVERSE: tuple[str, ...] = (
    "キーパーソン", "クロマク", "キラー", "カルティスト",
    "ミスリーダー", "シリアルキラー", "マイナス", "フレンド",
)
# 役職の人数上限（KB: 40/50）。上限を超える役職スロットは配役数が上限で頭打ちになる。
ROLE_MAX: dict[str, int] = {"ミスリーダー": 1, "フレンド": 2}
DEFAULT_ROLE = "パーソン"  # 配役されなかったキャラ（KB: 40:136）


# ---------------------------------------------------------------------------
# 脚本（秘匿情報を含む。公開範囲の制御は views.py）
# ---------------------------------------------------------------------------

@dataclass
class Incident:
    """事件予定1件。day と name は公開シート、culprit は非公開（KB: 00 準備）。"""

    day: int
    name: str
    culprit: str


@dataclass
class Script:
    """FS脚本。roles に無いキャストはパーソン扱い。

    entry_days: 登場日が2日目以降のキャラ（転校生等）。省略＝1日目から登場。
    ※ 登場日の進行処理は flow 側（ターン開始フェイズで配置）。state はエリア None＝未登場で表す。
    entry_loops: 登場ループが2以降のキャラ（神格）。脚本作成時に指定＝そのループまでは
      ループの準備でボードに配置しない（現物カード確認・2026-07-09）。省略＝ループ1から登場。
    """

    rule_y: str
    rule_x: str
    loops: int
    days_per_loop: int
    cast: list[str]
    roles: dict[str, str] = field(default_factory=dict)
    incidents: list[Incident] = field(default_factory=list)
    entry_days: dict[str, int] = field(default_factory=dict)
    entry_loops: dict[str, int] = field(default_factory=dict)
    set_name: str = "FS"
    rule_x2: str | None = None  # BTXのみ：2つ目のルールX（FSはNone）
    # 大物の縄張り（脚本作成時に指定・トークン1個・全ループ固定。現物確認済 2026-07-05）。
    # 盤上のトークン＝公開情報（主人公にも見える）。大物がキャストに居るとき必須。
    oomono_territory: str | None = None

    def role_of(self, name: str) -> str:
        return self.roles.get(name, DEFAULT_ROLE)

    @property
    def rule_xs(self) -> tuple[str, ...]:
        return (self.rule_x, self.rule_x2) if self.rule_x2 else (self.rule_x,)


def _rule_tables(set_name: str):
    if set_name == "FS":
        return FS_RULE_Y_ROLES, FS_RULE_X_ROLES, FS_INCIDENTS
    if set_name == "BTX":
        return BTX_RULE_Y_ROLES, BTX_RULE_X_ROLES, BTX_INCIDENTS
    raise ValueError(f"対応セットはFS/BTXのみ（{set_name} は範囲外＝原本確認）")


def _required_role_counts(slots: Counter) -> dict[str, int]:
    """役職スロットから必要配役数を算出（人数上限で頭打ち。KB: 50:21）。"""
    return {r: min(n, ROLE_MAX.get(r, n)) for r, n in slots.items()}


def validate_script(script: Script) -> None:
    """FS/BTX脚本の静的検証。違反・KB範囲外は ValueError（創作しない＝要確認で弾く）。"""
    y_tbl, x_tbl, incidents_set = _rule_tables(script.set_name)
    if script.rule_y not in y_tbl:
        raise ValueError(f"ルールY '{script.rule_y}' は{script.set_name}に無い")
    for rx in script.rule_xs:
        if rx not in x_tbl:
            raise ValueError(f"ルールX '{rx}' は{script.set_name}に無い")
    if script.set_name == "BTX" and script.rule_x2 is None:
        raise ValueError("BTXはルールXを2つ選ぶ（rule_x2 が必要）")
    if script.set_name == "FS" and script.rule_x2 is not None:
        raise ValueError("FSはルールX1つのみ（rule_x2 は指定不可）")
    if script.loops < 1 or script.days_per_loop < 1:
        raise ValueError("ループ回数・日数は1以上")

    # キャスト：既知キャラのみ（初期エリア表に無いキャラは範囲外＝要確認）
    dup = [n for n, c in Counter(script.cast).items() if c > 1]
    if dup:
        raise ValueError(f"キャスト重複: {dup}")
    unknown = [n for n in script.cast if n not in CHARACTER_INITIAL_AREA]
    if unknown:
        raise ValueError(f"未収録キャラ（要確認）: {unknown}")

    # 配役：選ばれたルールが追加する役職スロット（人数上限で頭打ち）と一致（40:28 / 50:20-21）。
    # 例外＝マイナス（FS）は0〜スロット数で可変（40:66）。
    for name in script.roles:
        if name not in script.cast:
            raise ValueError(f"配役対象がキャストに居ない: {name}")
    slots = Counter(y_tbl[script.rule_y])
    for rx in script.rule_xs:
        slots += Counter(x_tbl[rx])
    required = _required_role_counts(slots)
    # ★コピーキャット（他キャラのコピー）とイレギュラー（ルール非追加役職になる）は
    #   ルールスロットを埋めない＝slot集計から除外。
    assigned = Counter(r for r in (script.role_of(n) for n in script.cast
                                   if n not in ("コピーキャット", "イレギュラー"))
                       if r != DEFAULT_ROLE)
    rules_label = "/".join((script.rule_y, *script.rule_xs))

    # ★イレギュラー/A.I. の特性は「スロット要件」より先に検査する：イレギュラーはスロット集計から
    #   除外される（下の assigned）ため、ルール追加役職を渡すと『その役職が0人＝N人必要』という
    #   紛らわしいエラーが特性違反より先に出てしまう（順序で明確なメッセージにする）。
    # イレギュラーの特性（現物カード再確認・2026-07-08）：「ルールより追加された役職とすることが
    #   できない。そしてパーソンとなる代わりに、選ばれたルールにより追加されないいずれかの役職と
    #   なる」＝(1)ルール追加役職は不可 (2)パーソンも不可＝非追加役職が必須。
    universe = set(FS_ROLE_UNIVERSE if script.set_name == "FS" else BTX_ROLE_UNIVERSE) \
        - {DEFAULT_ROLE}
    added_roles = set(slots)  # 人数上限で頭打ちする前の「ルールが追加する役職」全体
    if "イレギュラー" in script.cast:
        irr_role = script.roles.get("イレギュラー")
        if not irr_role:
            raise ValueError(
                "イレギュラーの特性違反：パーソンにできない（選ばれたルールが追加しない"
                f"役職のいずれかを配役する。候補: {sorted(universe - added_roles)}）")
        if irr_role in added_roles:
            raise ValueError(
                f"イレギュラーの特性違反：'{irr_role}' は選択ルール（{rules_label}）が追加する役職"
                "＝イレギュラーには配役できない")
        if irr_role not in universe:
            raise ValueError(
                f"イレギュラーの配役 '{irr_role}' は{script.set_name}の役職に無い（要確認）")

    # A.I.の特性①（現物カード確認・2026-07-08）：脚本作成時に「パーソン」にできない
    #   ＝キャストに入れるなら必ず役職を配る（通常のルール追加役職でよい）。
    if "A.I." in script.cast and not script.roles.get("A.I."):
        raise ValueError("A.I.の特性違反：脚本作成時に「パーソン」にできない（役職を配役する）")

    for role, n_assigned in assigned.items():
        if role not in required:
            raise ValueError(f"役職 '{role}' は選択ルール（{rules_label}）では追加されない")
    for role, n_req in required.items():
        n_assigned = assigned.get(role, 0)
        if role == "マイナス":
            if n_assigned > n_req:
                raise ValueError(f"マイナスは0〜{n_req}人（{n_assigned}人は超過）")
        elif n_assigned != n_req:
            raise ValueError(f"役職 '{role}' は{n_req}人必要（現在{n_assigned}人）")

    # ★妹の特性（現物カード確認・2026-07-09）：友好無視/絶対友好無視を持つ役職に配役できない。
    #   （相談engine goodwill.py と対称。妹はキーパーソン等の非友好無視役職には配役できる。）
    imouto_role = script.roles.get("妹")
    if imouto_role and role_has_friendship_ignore(imouto_role):
        raise ValueError(
            f"妹の特性違反：'{imouto_role}' は友好無視/絶対友好無視を持つ＝妹には配役できない")

    # ★コピーキャットの特性（現物カード確認・2026-07-09）：パーソンとなる代わりに「他キャラ1人と
    #   同じ役職」になる（人数上限無視）。コピー先は脚本家が脚本作成時に設定＝roles に
    #   「コピーした役職」を持たせてよい（上でルールスロットからは除外済み）。ただしコピー先＝
    #   同じ役職のキャラがキャストに実在すること（居なければコピー先未設定＝不正）。
    #   役職依存の効果（キーパーソン死亡→ループ終了等）は role_of 経由で自然に機能する。
    copycat_role = script.roles.get("コピーキャット")
    if copycat_role:
        copy_sources = [n for n in script.cast
                        if n != "コピーキャット" and script.role_of(n) == copycat_role]
        if not copy_sources:
            raise ValueError(
                f"コピーキャットの特性違反：役職'{copycat_role}'は他キャラのコピー＝"
                "同じ役職のキャラがキャストに必要（コピー先が居ない）")

    # 事件：セットの事件のみ・犯人はキャスト内・犯人は全て別キャラ（00:123）・日は範囲内。
    # 同一日の複数事件は非対応（1日1事件前提＝M0要確認①）。
    seen_days: set[int] = set()
    seen_culprits: set[str] = set()
    for inc in script.incidents:
        if inc.name not in incidents_set:
            raise ValueError(f"事件 '{inc.name}' は{script.set_name}に無い")
        if inc.culprit not in script.cast:
            raise ValueError(f"犯人がキャストに居ない: {inc.culprit}")
        if not (1 <= inc.day <= script.days_per_loop):
            raise ValueError(f"事件日 {inc.day} が日数範囲外")
        if inc.day in seen_days:
            raise ValueError(f"同一日に複数事件は非対応（{inc.day}日目・M0要確認①）")
        if inc.culprit in seen_culprits:
            raise ValueError(f"犯人の重複: {inc.culprit}（各事件の犯人は全て別キャラ）")
        seen_days.add(inc.day)
        seen_culprits.add(inc.culprit)

    # 登場ループ指定の検証（神格）：キャスト内・1以上。
    # ★entry_loop > loops は**エラーでなく「登場しない」**（prepare_loop が
    #   loop_no < entry_loop で area=None にする＝無害に処理）。replace(loops=k) で
    #   ループを切り詰める probe/solver が entry_loop を残すのは正当な操作＝上限は課さない。
    for name, lp in script.entry_loops.items():
        if name not in script.cast:
            raise ValueError(f"登場ループ指定がキャスト外: {name}")
        if lp < 1:
            raise ValueError(f"{name} の登場ループ {lp} が不正（1以上）")

    # 登場日指定の検証
    for name, day in script.entry_days.items():
        if name not in script.cast:
            raise ValueError(f"登場日指定がキャスト外: {name}")
        if not (1 <= day <= script.days_per_loop):
            raise ValueError(f"{name} の登場日 {day} が日数範囲外")

    # 大物の縄張り（脚本作成時に指定必須。カード特性・現物確認済）
    if "大物" in script.cast:
        if script.oomono_territory not in ("病院", "神社", "都市", "学校"):
            raise ValueError("大物がキャストに居る場合、oomono_territory（縄張りボード）の指定が必須")
    elif script.oomono_territory is not None:
        raise ValueError("大物がキャストに居ないのに縄張りが指定されている")


# ---------------------------------------------------------------------------
# ゲーム状態
# ---------------------------------------------------------------------------

@dataclass
class CharState:
    """盤上のキャラ1体。area=None は未登場（登場日前）を表す。"""

    name: str
    role: str  # 秘匿。外に出すのは views.py の責務
    area: str | None
    alive: bool = True
    unrest: int = 0
    goodwill: int = 0
    anyaku: int = 0
    guard: int = 0  # 護衛カウンター（刑事の友好能力2）。死亡時に代わりに1消費する（KB: 20）
    virus_serial: bool = False  # 妄想拡大ウイルス：パーソンが不安3以上でシリアルキラー化（KB: 50）

    @property
    def on_board(self) -> bool:
        return self.area is not None


def script_to_dict(script: "Script") -> dict:
    """Script → JSON化可能な dict（棋譜 meta とスナップショット共通の正典形式）。

    ★正典はここ（sim/state）＝Script/Incident の定義と同じ層に置く。arena/gamelog は後方互換の
    ために同名を再エクスポートしている（既存の importer を壊さないため）。以前は gamelog 側が
    実装を持っていたが、sim → arena の逆流依存（循環import）になるため 2026-07-17 に移設。
    """
    d = asdict(script)
    d["incidents"] = [asdict(i) for i in script.incidents]
    return d


def script_from_dict(d: dict) -> "Script":
    """script_to_dict の逆。未知キーがあれば Script(**kw) が TypeError で弾く（捏造防止）。"""
    kw = dict(d)
    kw["incidents"] = [Incident(**i) for i in d.get("incidents", [])]
    return Script(**kw)


@dataclass
class GameState:
    """進行中の1ゲーム。生成後 prepare_loop() で最初のループを準備してから使う。"""

    script: Script
    loop_no: int = 1
    day: int = 1
    phase: str = "turn_start"
    characters: dict[str, CharState] = field(default_factory=dict)
    board_anyaku: dict[str, int] = field(default_factory=dict)
    leader_idx: int = 0  # PROTAGONIST_SEATS のインデックス
    # owner("mastermind"/"p1"/"p2"/"p3") → このループで使用済みの1/loopカード（表向き公開＝00:108）
    used_cards: dict[str, list[str]] = field(default_factory=dict)
    # ループを跨いで保持する情報（KB: 40:131 フレンドの役職公開フラグ）
    friend_revealed: set[str] = field(default_factory=set)
    revealed_roles: dict[str, str] = field(default_factory=dict)  # 能力等で公開済みの役職
    prev_goodwill: dict[str, int] = field(default_factory=dict)  # 前ループ終了時の友好（因果の糸用）
    protagonists_alive: bool = True
    game_over: bool = False
    winner: str | None = None  # "protagonist" | "mastermind"
    final_battle_pending: bool = False  # BTX：全ループ敗北→最後の戦いへ（00:144）
    # 公開イベントログ（両陣営可視。「結果のみ伝える」＝理由を含めない。00:127）
    history: list[dict] = field(default_factory=list)
    # 神視点ログ（処理の理由＝死因・能力名等。脚本家ビューとデバッグ専用）
    secret_log: list[dict] = field(default_factory=list)
    # フェイズ境界の盤面スナップショット（全ループ通算・神視点。ビューアの時点切替用）
    phase_snapshots: list[dict] = field(default_factory=list)
    # 今ターンに裏向きでセットされたカード [{owner,card,target,target_kind}]（位置は公開・中身は持ち主のみ）
    turn_placements: list[dict] = field(default_factory=list)
    # ループ内フラグ（prepare_loopでリセット）
    rumor_used: bool = False           # 不穏な噂(1/loop)使用済み
    used_goodwill: set = field(default_factory=set)  # このループで使用済みの1/L友好能力 (char,ability)
    protagonist_immortal: bool = False  # 軍人の友好能力2：このループ主人公は死亡しない（KB: 20）
    forbidden_lifted: set = field(default_factory=set)  # 医者能力3等：このループ禁止エリアを失うキャラ
    tesaki_suppressed: bool = False     # 手先の友好能力：このループ、手先犯の事件は発生しない
    butterfly_fired: bool = False       # このループで蝶の羽ばたきが発生したか（未来改変プランの敗北条件）
    loop_end_triggered: bool = False   # ループ終了効果が発動（キーパーソン/主人公死亡等）
    defeat: bool = False               # このループの主人公敗北が確定
    rule_y_board_x: str | None = None  # 復讐者の灯火のボードX（クロマク初期エリア・ループ毎確定）
    juusha_targets: set = field(default_factory=set)  # 従者の友好能力で追加された追随対象（このループ）
    alubaito_spawn_pending: bool = False  # アルバイト死亡→次ターン開始に都市へアルバイト？を配置（KB: 30）

    # -- ループ準備（KB: 00 ループの準備） --------------------------------

    def prepare_loop(self, dynamic_areas: dict[str, str] | None = None) -> None:
        """カウンター除去→キャラ初期配置→日数1日目→手札分配（使用済みリセット）。

        dynamic_areas: 初期エリアが脚本家指定のキャラ（手先等）の {名前: エリア}。
        """
        dynamic_areas = dynamic_areas or {}
        self.characters = {}
        placed_areas: dict[str, str] = {}
        for name in self.script.cast:
            area = initial_area_of(name)
            if area is None:
                area = dynamic_areas.get(name)
                if area is None:
                    raise ValueError(f"{name} の初期エリアは脚本家指定が必要（dynamic_areas）")
            if area not in AREAS:
                raise ValueError(f"{name} の初期エリア '{area}' が無効")
            placed_areas[name] = area
            if self.loop_no < self.script.entry_loops.get(name, 1):
                area_or_none: str | None = None  # 登場ループ前＝このループ全体で未登場（神格）
            elif self.script.entry_days.get(name, 1) > 1:
                area_or_none = None  # 登場日まで未登場（転校生・ループ内）
            elif name == "アルバイト？" and "アルバイト" in self.script.cast:
                area_or_none = None  # アルバイトが死ぬまで未登場（死亡時ターン開始に都市へ配置）
            else:
                area_or_none = area
            # ★アルバイト／アルバイト？の役職（B-50・現物確認 2026-07-24）：
            #   ・アルバイト＝「配役を無視してパーソンとなる」＝**振る舞いは常にパーソン**
            #     （配役表上の役職は script.role_of で別途表示＝CharState.role は挙動用なので DEFAULT）。
            #   ・アルバイト？＝**無視特性を持たない**＝アルバイトの配役を**継承して実発揮**する
            #     （ペア在籍時）。単独アルバイト？は自身の配役どおり。
            #   ★段階A（generator が実役職を配る段階B の前）は role_of("アルバイト")=パーソン＝
            #     継承値もパーソン＝現コーパスでは挙動不変（inert）。段階B land で実役職が乗る。
            role = self.script.role_of(name)
            if name == "アルバイト":
                role = DEFAULT_ROLE
            elif name == "アルバイト？" and "アルバイト" in self.script.cast:
                role = self.script.role_of("アルバイト")   # 継承（実発揮）
            self.characters[name] = CharState(name=name, role=role, area=area_or_none)
        self.board_anyaku = {a: 0 for a in AREAS}
        self.day = 1
        self.phase = PHASES[0]
        self.used_cards = {o: [] for o in ("mastermind",) + PROTAGONIST_SEATS}
        self.protagonists_alive = True
        self.turn_placements = []
        self.rumor_used = False
        self.used_goodwill = set()
        self.protagonist_immortal = False
        self.forbidden_lifted = set()
        self.tesaki_suppressed = False
        self.butterfly_fired = False
        self.loop_end_triggered = False
        self.defeat = False
        self.juusha_targets = set()
        self.alubaito_spawn_pending = False
        # ボードX＝特定役職の初期エリア（手先/従者/ウィッチ等でループ毎に変わりうる）。
        #   復讐者の灯火(FS)＝クロマク初期（40:43）／巨大時限爆弾Xの存在(BTX)＝ウィッチ初期（50:52）。
        self.rule_y_board_x = None
        _board_x_role = {"復讐者の灯火": "クロマク", "巨大時限爆弾Xの存在": "ウィッチ"}.get(
            self.script.rule_y)
        if _board_x_role:
            for name in self.script.cast:
                if self.script.role_of(name) == _board_x_role:
                    self.rule_y_board_x = placed_areas[name]

        # ループ開始時に置くべきカウンター（00:87）：
        # 黒猫特性1＝神社に暗躍+1（engine/data の宣言をここで初めて裁定に接続）
        for board, n in forced_loop_start_anyaku(self.characters.values()):
            self.board_anyaku[board] += n
        # フレンド：役職公開済みならループ開始時に友好+1（40:131）
        for name in self.friend_revealed:
            c = self.characters.get(name)
            if c is not None and c.alive:
                c.goodwill += 1
        # 因果の糸（BTX）：前ループ終了時に友好が置かれていた全員に不安+2（50:85）
        # ★卓上では公開のカウンター操作＝イベント発行（【強制】なので在/不在の推理材料。
        #   前ループ終了時の友好は loop_board の char_goodwill として公開済み）
        if "因果の糸" in self.script.rule_xs:
            for name, g in self.prev_goodwill.items():
                c = self.characters.get(name)
                if c is not None and g > 0:
                    c.unrest += 2
                    self.history.append({"loop": self.loop_no, "day": 0,
                                         "phase": "loop_start", "event": "unrest",
                                         "target": name, "delta": 2})

    def start_next_loop(self, dynamic_areas: dict[str, str] | None = None) -> None:
        self.loop_no += 1
        self.prepare_loop(dynamic_areas)

    # -- 手札・リーダー ----------------------------------------------------

    def hand_of(self, owner: str) -> list[str]:
        """現在の手札（multiset）。1/loop使用済みを除いたループ開始時手札。"""
        base = list(MASTERMIND_HAND if owner == "mastermind" else PROTAGONIST_HAND)
        for card in self.used_cards.get(owner, []):  # ループ準備前は空
            base.remove(card)
        return base

    @property
    def leader(self) -> str:
        return PROTAGONIST_SEATS[self.leader_idx]

    def rotate_leader(self) -> None:
        """リーダー交代フェイズ（00:112）：左隣＝次席へ。"""
        self.leader_idx = (self.leader_idx + 1) % len(PROTAGONIST_SEATS)

    def pub(self, event: dict) -> None:
        """公開イベントを loop/day/phase タグ付きで history に積む（イベント発行の単一窓口）。"""
        self.history.append({"loop": self.loop_no, "day": self.day,
                             "phase": self.phase, **event})

    # -- スナップショット（フェイズ境界の盤面。ビューアの時点切替用） --------

    def snapshot(self, point: str, reveal_cards: bool = False) -> None:
        """現在の盤面（神視点）を point ラベル付きで phase_snapshots に記録する。

        裏向きセット中のカード（turn_placements）も含めるので、
        「脚本家行動フェイズ後＝3枚伏せた盤面」といった途中状態も再現できる。

        reveal_cards=True：主人公視点でも turn_placements の中身を表向きで見せてよい時点
        （行動解決フェイズで6枚全公開された直後＝「行動解決中」）に立てる。役職の公開は別（不変）。
        """
        snap = {
            "loop": self.loop_no, "day": self.day, "point": point,
            "reveal_cards": reveal_cards,
            # ★このスナップショット時点までに発行済みの公開イベント数（history[:hist_len]）。
            #   フェイズ送りで「今見ている盤面と一致する公開情報だけ」を出すのに使う。
            "hist_len": len(self.history),
            "characters": {
                n: {"role": c.role, "area": c.area, "alive": c.alive,
                    "unrest": c.unrest, "goodwill": c.goodwill, "anyaku": c.anyaku}
                for n, c in self.characters.items()
            },
            "board_anyaku": dict(self.board_anyaku),
            "turn_placements": [dict(p) for p in self.turn_placements],
            "revealed_roles": dict(self.revealed_roles),
        }
        # ★A-36（2026-07-19・防御）：直近末尾が同一 (loop,day,point) なら**上書き**（append しない）。
        #   正常な進行では各 point は1日1回＝同一 point が連続 append されることは無い（新規開始は
        #   影響ゼロ・冪等）。クラウド復元で同一 state に run_day が二重に走った場合の snapshot 累積
        #   （実測45回重複）への保険。根治は play 側の「毎rerun 新品 state」（A-36）で、これは二次防御。
        if self.phase_snapshots:
            last = self.phase_snapshots[-1]
            if (last["loop"], last["day"], last["point"]) == (self.loop_no, self.day, point):
                self.phase_snapshots[-1] = snap
                return
        self.phase_snapshots.append(snap)

    # -- ログ用シリアライズ（神視点＝全情報。可視性制御は views.py） --------

    def to_dict(self) -> dict:
        return {
            "loop": self.loop_no,
            "day": self.day,
            "phase": self.phase,
            "leader": self.leader,
            "characters": {
                n: {
                    "role": c.role, "area": c.area, "alive": c.alive,
                    "unrest": c.unrest, "goodwill": c.goodwill, "anyaku": c.anyaku,
                } for n, c in self.characters.items()
            },
            "board_anyaku": dict(self.board_anyaku),
            "used_cards": {o: list(v) for o, v in self.used_cards.items()},
            "friend_revealed": sorted(self.friend_revealed),
            "revealed_roles": dict(self.revealed_roles),
            "protagonists_alive": self.protagonists_alive,
            "game_over": self.game_over,
            "winner": self.winner,
            "turn_placements": [dict(p) for p in self.turn_placements],
            "rumor_used": self.rumor_used,
            "loop_end_triggered": self.loop_end_triggered,
            "defeat": self.defeat,
            "rule_y_board_x": self.rule_y_board_x,
        }

    # -- スナップショット（完全復元用・Supabase/URL保存 Phase 0） --------------
    #
    # ★to_dict() とは別物＝役割が違う（意図的に分けている・2026-07-17）：
    #   - to_dict()      ＝棋譜の "final_state"（要約）。**契約が固定**されている
    #                      （arena/replay.py が `state.to_dict() == meta["final_state"]` で
    #                      旧棋譜と照合する＝キーを増やすと過去ログが全て再生不能になる。
    #                      docs/feedback_logs の検死素材もこれで読む）。よって不変で維持する。
    #   - to_snapshot()  ＝**完全復元**用。history/secret_log/phase_snapshots/ループ内フラグ/
    #                      CharState の guard・virus_serial まで含む（to_dict はどれも持たない）。
    #                      棋譜へは入れない（history を二重化して肥大化するため）。
    # 復元は replay（決定の再実行）に依存しない＝ドリフト耐性（提案書§0）。
    SNAPSHOT_VERSION = 1

    def to_snapshot(self) -> dict:
        """神視点フルの状態スナップショット（JSON化可能）。from_snapshot と対。

        ★裁定済み（2026-07-16）：保存は神視点フル（伏せ札の中身・配役・secret_log を含む）。
        共有token発行UIには「神視点データを含む」注意書きが要る（提案書§7 論点1）。
        """
        return {
            "v": self.SNAPSHOT_VERSION,
            "script": script_to_dict(self.script),
            "loop_no": self.loop_no,
            "day": self.day,
            "phase": self.phase,
            "leader_idx": self.leader_idx,      # ★to_dict は派生値 leader（席名）しか持たない
            "characters": {
                n: {"name": c.name, "role": c.role, "area": c.area, "alive": c.alive,
                    "unrest": c.unrest, "goodwill": c.goodwill, "anyaku": c.anyaku,
                    "guard": c.guard, "virus_serial": c.virus_serial}
                for n, c in self.characters.items()
            },
            "board_anyaku": dict(self.board_anyaku),
            "used_cards": {o: list(v) for o, v in self.used_cards.items()},
            "friend_revealed": sorted(self.friend_revealed),
            "revealed_roles": dict(self.revealed_roles),
            "prev_goodwill": dict(self.prev_goodwill),
            "protagonists_alive": self.protagonists_alive,
            "game_over": self.game_over,
            "winner": self.winner,
            "final_battle_pending": self.final_battle_pending,
            "history": [dict(e) for e in self.history],
            "secret_log": [dict(e) for e in self.secret_log],
            "phase_snapshots": [dict(s) for s in self.phase_snapshots],
            "turn_placements": [dict(p) for p in self.turn_placements],
            "rumor_used": self.rumor_used,
            # set は JSON に無い＝list 化（used_goodwill は (char, ability) のタプル集合＝
            # 復元時に tuple へ戻す。JSONを経由すると list になるため要変換）。
            "used_goodwill": sorted([list(x) for x in self.used_goodwill]),
            "protagonist_immortal": self.protagonist_immortal,
            "forbidden_lifted": sorted(self.forbidden_lifted),
            "tesaki_suppressed": self.tesaki_suppressed,
            "butterfly_fired": self.butterfly_fired,
            "loop_end_triggered": self.loop_end_triggered,
            "defeat": self.defeat,
            "rule_y_board_x": self.rule_y_board_x,
            "juusha_targets": sorted(self.juusha_targets),
            "alubaito_spawn_pending": self.alubaito_spawn_pending,
        }

    @classmethod
    def from_snapshot(cls, d: dict, script: Script | None = None) -> "GameState":
        """to_snapshot() の出力から GameState を復元する（prepare_loop は呼ばない＝上書き禁止）。

        script: 明示指定があればそれを使う（自作脚本の実体を持っている場合）。無ければ
                スナップショット内蔵の script を復元する（自己完結＝URL/クラウド保存はこちら）。
        ★将来 v が上がったら、ここで旧版を読み替える（後方互換の責務はこのメソッド）。
        """
        v = d.get("v", 1)
        if v > cls.SNAPSHOT_VERSION:
            raise ValueError(
                f"このスナップショットは新しい版（v{v}）です。アプリを更新してください"
                f"（対応v{cls.SNAPSHOT_VERSION}）")
        sc = script if script is not None else script_from_dict(d["script"])
        st = cls(script=sc)
        st.loop_no = d["loop_no"]
        st.day = d["day"]
        st.phase = d["phase"]
        st.leader_idx = d["leader_idx"]
        st.characters = {
            n: CharState(name=c.get("name", n), role=c["role"], area=c["area"],
                         alive=c["alive"], unrest=c["unrest"], goodwill=c["goodwill"],
                         anyaku=c["anyaku"], guard=c.get("guard", 0),
                         virus_serial=c.get("virus_serial", False))
            for n, c in d["characters"].items()
        }
        st.board_anyaku = dict(d["board_anyaku"])
        st.used_cards = {o: list(v_) for o, v_ in d["used_cards"].items()}
        st.friend_revealed = set(d["friend_revealed"])
        st.revealed_roles = dict(d["revealed_roles"])
        st.prev_goodwill = dict(d.get("prev_goodwill", {}))
        st.protagonists_alive = d["protagonists_alive"]
        st.game_over = d["game_over"]
        st.winner = d["winner"]
        st.final_battle_pending = d.get("final_battle_pending", False)
        st.history = [dict(e) for e in d.get("history", [])]
        st.secret_log = [dict(e) for e in d.get("secret_log", [])]
        st.phase_snapshots = [dict(s) for s in d.get("phase_snapshots", [])]
        st.turn_placements = [dict(p) for p in d.get("turn_placements", [])]
        st.rumor_used = d["rumor_used"]
        st.used_goodwill = {tuple(x) for x in d.get("used_goodwill", [])}
        st.protagonist_immortal = d.get("protagonist_immortal", False)
        st.forbidden_lifted = set(d.get("forbidden_lifted", ()))
        st.tesaki_suppressed = d.get("tesaki_suppressed", False)
        st.butterfly_fired = d.get("butterfly_fired", False)
        st.loop_end_triggered = d["loop_end_triggered"]
        st.defeat = d["defeat"]
        st.rule_y_board_x = d["rule_y_board_x"]
        st.juusha_targets = set(d.get("juusha_targets", ()))
        st.alubaito_spawn_pending = d.get("alubaito_spawn_pending", False)
        return st


def new_game(script: Script, dynamic_areas: dict[str, str] | None = None) -> GameState:
    """検証済み脚本から第1ループ準備済みの GameState を作る。"""
    validate_script(script)
    state = GameState(script=script)
    state.prepare_loop(dynamic_areas)
    return state
