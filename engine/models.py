"""惨劇RoopeR 判定エンジン — データモデル（部分盤面）。

設計方針（このスレッドでの合意）:
- 自然文から「部分盤面」を組み、未指定の裁量カードは inert（＝無し）と仮定して
  決定的に解く。ただし強制・自動で必ず起きる効果（黒猫の毎ループ暗躍など）は補完する。
- 不明値・未収録ルールは創作しない。範囲は First Steps + Basic Tragedy X の行動解決フェイズ。

KB対応:
- エリア（病院/神社/都市/学校）と移動: 00, 10
- 役職→条文能力: 40/50
- キャラ禁止エリア: 30
"""

from __future__ import annotations

from dataclasses import dataclass, field


# 行動解決フェイズで主人公／脚本家が出しうるカード名（KB: 10_action_cards.md）。
MASTERMIND_CARDS = {
    "移動↑↓", "移動←→", "移動斜め", "友好禁止",
    "不安+1", "不安-1", "不安禁止", "暗躍+1", "暗躍+2",
}
PROTAGONIST_CARDS = {
    "移動↑↓", "移動←→", "移動禁止", "友好+1", "友好+2",
    "不安+1", "不安-1", "暗躍禁止",
}
# 1ループに1回（解決後は手札に戻らない）。KB: 10。
ONCE_PER_LOOP = {
    "mastermind": {"移動斜め", "暗躍+2"},
    "protagonist": {"移動禁止", "友好+2", "不安-1"},
}
# ループ開始時手札（multiset。KB: 00 ループの準備 / 10）。
# 現物確認済 2026-07-03：脚本家10枚で複数あるのは不安+1のみ（暗躍+1は1枚）。主人公は8種各1枚。
PROTAGONIST_HAND: tuple[str, ...] = (
    "移動↑↓", "移動←→", "移動禁止", "友好+1", "友好+2", "不安+1", "不安-1", "暗躍禁止",
)
MASTERMIND_HAND: tuple[str, ...] = (
    "移動↑↓", "移動←→", "移動斜め", "友好禁止",
    "不安+1", "不安+1", "不安-1", "不安禁止", "暗躍+1", "暗躍+2",
)
MOVE_CARDS = {"移動↑↓", "移動←→", "移動斜め"}
ANRYAKU_PLUS = {"暗躍+1": 1, "暗躍+2": 2}
# 不安／友好カウンターの増減カード（行動解決フェイズ「その他」で解決。KB: 10）。
# 不安は主人公・脚本家の両方が置ける。友好は主人公のみ（→00 カウンター表）。
UNREST_DELTA = {"不安+1": 1, "不安-1": -1}
GOODWILL_PLUS = {"友好+1": 1, "友好+2": 2}
# 禁止系（脚本家のみ）。重なった対応カードを打ち消す。暗躍禁止と違い自滅ルールは無い（KB: 10）。
UNREST_BAN = "不安禁止"     # 重なった不安+1／不安-1を無効化
GOODWILL_BAN = "友好禁止"   # 重なった友好+1／+2を無効化
MOVE_BAN = "移動禁止"       # 主人公：重なった移動カード（↑↓／←→／斜め）を無効化


@dataclass
class Character:
    """盤面上のキャラクター。role は配役された役職名。"""

    name: str
    role: str  # 例: "カルティスト", "ミスリーダー", "パーソン"
    area: str  # 現在エリア（病院/神社/都市/学校）
    forbidden: frozenset[str] = frozenset()  # 禁止エリア（KB: 30）
    alive: bool = True
    goodwill: int = 0  # 置かれている友好カウンター数（脚本家能力フェイズ判定に使用。KB: 20）
    unrest: int = 0  # 置かれている不安カウンター数（初期値。行動解決の増減や事件判定に使用。KB: 00）
    anyaku: int = 0  # 置かれている暗躍カウンター数（ターン終了の死亡判定に使用。KB: 50）
    guard: int = 0  # 護衛カウンター数（刑事の友好能力2）。死亡時に代わりに1消費する（KB: 20）

    @property
    def is_corpse(self) -> bool:
        return not self.alive


@dataclass
class Placement:
    """裏向きでセットされた行動カード1枚。

    owner: "mastermind" もしくは主人公ID（"p1","p2",...）。
    target_kind: "board"（エリア名）または "character"（キャラ名）。
    """

    owner: str
    card: str
    target: str
    target_kind: str  # "board" | "character"

    @property
    def is_protagonist(self) -> bool:
        return self.owner != "mastermind"


@dataclass
class Board:
    """1ターン分の部分盤面（明示された情報のみ＋強制補完）。"""

    characters: dict[str, Character] = field(default_factory=dict)
    placements: list[Placement] = field(default_factory=list)
    #: 大物の縄張りトークンが置かれたボード名（KB: 20 特性＝脚本作成時指定・全ループ固定・
    #: **盤上に置かれる公開情報**）。None＝大物不在/未指定＝従来と完全に同一。
    #: 大物は「テリトリーにいるものとして能力を使用してもよい」＝カルティストの暗躍禁止無視は
    #: テリトリーと現在地の**両方**に及ぶ（60 E-3b A2・ユーザー現物確認 2026-07-17）。
    oomono_territory: str | None = None

    def char(self, name: str) -> Character | None:
        return self.characters.get(name)

    def add_character(self, c: Character) -> None:
        self.characters[c.name] = c

    def add_placement(self, p: Placement) -> None:
        self.placements.append(p)
