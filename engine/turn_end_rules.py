"""ターン終了フェイズの死亡判定「ルール」の単一ソース（純粋述語）。

engine/turn_end.py（相談AI・結果を"提示"する）と sim/effects.py（AIプレイヤー・盤面に"適用"する）の
両方がここを呼ぶ＝シリアルキラーの犠牲者算定・不死・各役職の【任意】発動条件を二重実装しない。

キャラ引数は .name/.role/.area/.alive/.unrest/.anyaku/.goodwill を持つ任意オブジェクト
（engine.models.Character と sim.state.CharState の両方が該当＝ダックタイピング）。
「生存かつ盤上」の絞り込みは各呼び出し側の責務（データモデルが違うため）。
"""

from __future__ import annotations

from .data import ROLE_CLAUSE_ABILITY


def is_immortal(role: str) -> bool:
    """不死（死亡効果を受けても死亡しない）。KB: 50 タイムトラベラー。"""
    return ROLE_CLAUSE_ABILITY.get(role) == "不死"


def serial_killer_victims(alive_on_board, is_serial, *,
                          oomono_territory: str | None = None) -> dict:
    """シリアルキラー【強制】の犠牲者を同時解決で算定（KB: 40:117 / 60 D-2）。

    alive_on_board: 生存かつ盤上のキャラのリスト。is_serial(char)->bool（妄想ウイルス化を含めるかは呼び側）。
    返り値: {犠牲者名: 加害シリアルキラー名}。シリアルキラー同士が2人きりなら両者が入る（相打ち）。

    ★oomono_territory（B-30b・2026-07-17）＝大物のテリトリー（KB: 20 特性 / 60 E-3b）：
      「脚本家がこのキャラクターの能力を使う場合、テリトリーにいるものとして能力を使用してもよい」
      ＝**大物SKはテリトリー外に居ても、テリトリーの単独キャラを殺害しうる**
      （E-3b A1：「2人きり」判定は テリトリー内の実在1人＋大物（いるものとして）＝2人で成立。
       ユーザー現物確認 2026-07-17）。
      判定順＝実エリア→テリトリー（実エリアで既に2人きりが成立していればそちらを優先＝
      従来の解決と一致させる。両方成立する稀な局面でどちらを使うかは脚本家の選択＝
      E-3b 補足「毎回選べる」だが、AIの選択接続は mm AI 側の課題＝③）。
      大物以外／テリトリー未指定は None＝**従来と完全に同一**（additive）。
    """
    chars = list(alive_on_board)
    victims: dict[str, str] = {}
    for c in chars:
        if not is_serial(c):
            continue
        areas = [c.area]
        if (getattr(c, "name", None) == "大物" and oomono_territory
                and oomono_territory != c.area):
            areas.append(oomono_territory)   # 「テリトリーにいるものとして」（E-3b A1）
        for a in areas:
            others = [o for o in chars if o.name != c.name and o.area == a]
            if len(others) == 1 and others[0].name not in victims:
                victims[others[0].name] = c.name
                break        # 1体＝1回の解決で犠牲者は1人
    return victims


def killer_killable_keypersons(killer, alive_on_board, *,
                               territory: str | None = None) -> list:
    """キラーが【任意】で殺せるキーパーソン（同エリア・暗躍2以上）。KB: 40:94 / 50:109。

    ★territory（B-30・2026-07-17）＝大物のテリトリー（縄張りボード。KB: 20 大物特性・現物確認済）：
      「脚本家がこのキャラクターの能力を使う場合、テリトリーにいるものとして能力を使用してもよい」
      ＝キラーの【任意】殺害（00:176-178＝脚本家が"使う"能力）の「同エリア」条件を
      **実エリア ∪ テリトリー**で判定する。どちらから殺すかは脚本家の選択＝呼び側が option 化。
      大物以外／テリトリー未指定は territory=None＝**従来と完全に同一**（additive）。

    ★適用範囲（KBに無いものは実装しない＝捏造禁止）：
      ・事件は大物の"能力"ではない＝テリトリー適用外（60 E-3＝書 Q25 で明示）。
      ・シリアルキラー【強制】・カルティストの暗躍禁止無視への適用は**要確認**
        （20:203 は「→60: A22」を参照するが 60 に該当項が無い＝KBギャップ）。確認まで未実装。
    """
    areas = {killer.area} | ({territory} if territory else set())
    return [o for o in alive_on_board
            if o.role == "キーパーソン" and o.area in areas and o.anyaku >= 2]


def killer_can_kill_protagonist(killer) -> bool:
    """キラーが【任意】で主人公を殺せるか（自身に暗躍4以上）。KB: 40:95 / 50:110。"""
    return killer.anyaku >= 4


def mainlover_can_kill_protagonist(c) -> bool:
    """メインラバーズが【任意】で主人公を殺せるか（不安3以上＋暗躍1以上）。KB: 50:160。"""
    return c.unrest >= 3 and c.anyaku >= 1


def timetraveler_can_defeat(c, is_final_day: bool) -> bool:
    """タイムトラベラーが【任意】で主人公を敗北させられるか（最終日・友好2以下）。KB: 50:128。"""
    return is_final_day and c.goodwill <= 2
