"""合成サンプル脚本（テスト・自己対戦・デモ用）。

★公式収録脚本の配役は使わない（ネタバレ回避）。ここにあるのは自作の合成脚本のみで、
FS全6ルール・事件7種・手先の動的初期エリアをカバーする。公式脚本をデータ化する場合は
docs/AIプレイヤー計画.md §6 のネタバレ分離ルールに従い、別ディレクトリ（sim/scripts/）へ。
"""

from __future__ import annotations

from .state import Incident, Script


def basic_script() -> Script:
    """殺人計画×切り裂き魔の影（キーパーソン・キラー・シリアルキラーの基本形）。"""
    return Script(
        rule_y="殺人計画",
        rule_x="切り裂き魔の影",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={
            "女子学生": "キーパーソン",
            "サラリーマン": "クロマク",
            "刑事": "キラー",
            "巫女": "ミスリーダー",
            "医者": "シリアルキラー",
        },
        incidents=[
            Incident(day=2, name="殺人事件", culprit="男子学生"),
            Incident(day=3, name="自殺", culprit="巫女"),
        ],
    )


def guard_script() -> Script:
    """守るべき場所×不穏な噂（ボード暗躍の敗北条件＋1/loopルール暗躍）。"""
    return Script(
        rule_y="守るべき場所",
        rule_x="不穏な噂",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"女子学生": "キーパーソン", "医者": "カルティスト", "巫女": "ミスリーダー"},
        incidents=[
            Incident(day=2, name="病院の事件", culprit="男子学生"),
            Incident(day=3, name="行方不明", culprit="刑事"),
        ],
    )


def revenge_script() -> Script:
    """復讐者の灯火×最低の却本（手先クロマク＝動的初期エリア＋フレンド）。"""
    return Script(
        rule_y="復讐者の灯火",
        rule_x="最低の却本",
        loops=3,
        days_per_loop=3,
        cast=["手先", "男子学生", "女子学生", "巫女", "刑事", "医者", "黒猫"],
        roles={"手先": "クロマク", "巫女": "ミスリーダー", "男子学生": "フレンド"},
        incidents=[
            Incident(day=1, name="不安拡大", culprit="女子学生"),
            Incident(day=2, name="流布", culprit="医者"),
            Incident(day=3, name="遠隔殺人", culprit="刑事"),
        ],
    )


def shrine_script() -> Script:
    """復讐者の灯火×切り裂き魔の影。クロマク=サラリーマン（都市初期）＝敗北ボードX=都市。

    ★2026-07-08：クロマク=黒猫（神社初期）はボードX=神社に黒猫のループ開始強制+1が
    重なり、詰みソルバ（solve_script）で1ループ防衛不能（mastermind・2日厳密でも確認）
    ＝FSはFBが無いので主人公に勝ち筋ゼロの設計事故だった。クロマクをサラリーマンへ
    移動（ボードX=都市）。黒猫の神社+1はゴール外＝デコイノイズとして残る。
    """
    return Script(
        rule_y="復讐者の灯火",
        rule_x="切り裂き魔の影",
        loops=3,
        days_per_loop=3,
        cast=["巫女", "黒猫", "男子学生", "女子学生", "刑事", "サラリーマン", "医者"],
        roles={"サラリーマン": "クロマク", "男子学生": "ミスリーダー", "医者": "シリアルキラー"},
        incidents=[Incident(day=2, name="殺人事件", culprit="刑事")],
    )


# --- BTX（合成。belief はBTX対応済みなので推理インスペクタ・最後の戦いも動く） ---

def btx_seal_script() -> Script:
    """BTX：封印されしモノ（神社暗躍≥2で敗北）× 潜む殺人鬼 × 因果の糸。邪気の汚染で神社を汚す。

    ★2026-07-08：FB専用（1ループ防衛不能）だったため2段階で是正（ユーザー方針：
    最後の戦いでしか勝てないシナリオは基本NG）。
    (1) 黒猫→入院患者：黒猫のループ開始強制+1（神社）が止まらない供給だった。
    (2) 不穏な噂→因果の糸：封印はクロマク＋カルティスト必須のため、フリーの
        止まらない+1（噂）まで持つと供給4系統 vs 主人公3席で構造的に防衛不能
        （詰みソルバ solve_script が mastermind を検出）。"""
    return Script(
        set_name="BTX",
        rule_y="封印されしモノ",
        rule_x="潜む殺人鬼",
        rule_x2="因果の糸",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "入院患者"],
        roles={"サラリーマン": "クロマク", "医者": "カルティスト",
               "刑事": "シリアルキラー", "男子学生": "フレンド"},
        incidents=[Incident(day=1, name="邪気の汚染", culprit="巫女"),
                   Incident(day=3, name="殺人事件", culprit="刑事")],
    )


def btx_future_script() -> Script:
    """BTX：未来改変プラン（蝶の羽ばたき発生で敗北・タイムトラベラー不死）× 潜む殺人鬼 × 不穏な噂。"""
    return Script(
        set_name="BTX",
        rule_y="未来改変プラン",
        rule_x="潜む殺人鬼",
        rule_x2="不穏な噂",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"医者": "カルティスト", "サラリーマン": "タイムトラベラー",
               "刑事": "シリアルキラー", "男子学生": "フレンド", "巫女": "ミスリーダー"},
        incidents=[Incident(day=2, name="蝶の羽ばたき", culprit="巫女"),
                   Incident(day=3, name="殺人事件", culprit="刑事")],
    )


def btx_bomb_script() -> Script:
    """BTX：巨大時限爆弾Xの存在（ウィッチ初期エリア＝都市の暗躍≥2で敗北）× 恋愛風景 × 不穏な噂。"""
    return Script(
        set_name="BTX",
        rule_y="巨大時限爆弾Xの存在",
        rule_x="恋愛風景",
        rule_x2="不穏な噂",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"サラリーマン": "ウィッチ", "男子学生": "ラバーズ",
               "刑事": "メインラバーズ", "巫女": "ミスリーダー"},
        incidents=[Incident(day=2, name="不安拡大", culprit="女子学生"),
                   Incident(day=3, name="遠隔殺人", culprit="医者")],
    )


def btx_contract_script() -> Script:
    """BTX：僕と契約しようよ！（キーパーソン＝少女の暗躍≥2で敗北）× 友情サークル × 不定因子χ。"""
    return Script(
        set_name="BTX",
        rule_y="僕と契約しようよ！",
        rule_x="友情サークル",
        rule_x2="不定因子χ",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"女子学生": "キーパーソン", "男子学生": "フレンド", "医者": "フレンド",
               "巫女": "ミスリーダー", "刑事": "ファクター"},
        incidents=[Incident(day=1, name="不安拡大", culprit="サラリーマン"),
                   Incident(day=3, name="行方不明", culprit="巫女")],
    )


def btx_lovers_script() -> Script:
    """BTX：殺人計画（役職効果が中心）× 恋愛風景 × 妄想拡大ウイルス。恋人の相打ち・パーソンのSK化。"""
    return Script(
        set_name="BTX",
        rule_y="殺人計画",
        rule_x="恋愛風景",
        rule_x2="妄想拡大ウイルス",
        loops=3,
        days_per_loop=3,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"女子学生": "キーパーソン", "サラリーマン": "クロマク", "刑事": "キラー",
               "男子学生": "ラバーズ", "医者": "メインラバーズ", "巫女": "ミスリーダー"},
        incidents=[Incident(day=2, name="殺人事件", culprit="男子学生"),
                   Incident(day=3, name="不安拡大", culprit="サラリーマン")],
    )


# --- 5日級（4ループ×5日＝スタンダード形式。ユーザー方針 2026-07-08） ---

def fs5_guard_script() -> Script:
    """FS 5日級：守るべき場所×不穏な噂の耐久戦。

    3日級との違い＝供給が2枚/日×5日で、暗躍禁止・ピンの回し方（移動禁止は席計3枚/
    ループ）と行方不明（犯人サラリーマン＝カルティストではない）の冷却が5日間持つかを問う。"""
    return Script(
        rule_y="守るべき場所",
        rule_x="不穏な噂",
        loops=4,
        days_per_loop=5,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"女子学生": "キーパーソン", "医者": "カルティスト", "刑事": "ミスリーダー"},
        incidents=[Incident(day=2, name="行方不明", culprit="サラリーマン"),
                   Incident(day=4, name="遠隔殺人", culprit="男子学生")],
    )


def btx5_seal_script() -> Script:
    """BTX 5日級：封印されしモノ×潜む殺人鬼×因果の糸＋学者（ループ開始カウンター特性）。

    フェリー戦争（カルティスト医者）とクロマクポンプを5日間さばく耐久戦。学者の特性
    【強制】（各ループ開始時にカウンター1つ）のショーケース。"""
    return Script(
        set_name="BTX",
        rule_y="封印されしモノ",
        rule_x="潜む殺人鬼",
        rule_x2="因果の糸",
        loops=4,
        days_per_loop=5,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "学者"],
        roles={"サラリーマン": "クロマク", "医者": "カルティスト",
               "刑事": "シリアルキラー", "男子学生": "フレンド"},
        incidents=[Incident(day=1, name="邪気の汚染", culprit="女子学生"),
                   Incident(day=4, name="殺人事件", culprit="刑事")],
    )


def btx5_future_script() -> Script:
    """BTX 5日級：未来改変プラン×潜む殺人鬼×不穏な噂。

    TTハーツ戦（最終日友好3）と蝶の羽ばたきの冷却が5日スパンでどう変わるかを問う。"""
    return Script(
        set_name="BTX",
        rule_y="未来改変プラン",
        rule_x="潜む殺人鬼",
        rule_x2="不穏な噂",
        loops=4,
        days_per_loop=5,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"サラリーマン": "タイムトラベラー", "医者": "カルティスト",
               "刑事": "シリアルキラー", "男子学生": "フレンド", "巫女": "ミスリーダー"},
        incidents=[Incident(day=3, name="蝶の羽ばたき", culprit="女子学生"),
                   Incident(day=5, name="殺人事件", culprit="刑事")],
    )


BTX_SAMPLE_SCRIPTS = {
    "btx_seal": btx_seal_script, "btx_future": btx_future_script,
    "btx_bomb": btx_bomb_script, "btx_contract": btx_contract_script,
    "btx_lovers": btx_lovers_script,
}

# 全サンプル（FS 4 ＋ BTX 5 ＋ 5日級 3）。runner/viewer/play で共用。
SAMPLE_SCRIPTS = {
    "basic": basic_script,
    "guard": guard_script,
    "revenge": revenge_script,
    "shrine": shrine_script,
    "btx_seal": btx_seal_script,
    "btx_future": btx_future_script,
    "btx_bomb": btx_bomb_script,
    "btx_contract": btx_contract_script,
    "btx_lovers": btx_lovers_script,
    "fs5_guard": fs5_guard_script,
    "btx5_seal": btx5_seal_script,
    "btx5_future": btx5_future_script,
}
