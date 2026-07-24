"""盤面の隣接と移動解決。

盤面レイアウト（2×2、KB: 00 コンポーネント）:
    病院(0,0)  神社(1,0)
    都市(0,1)  学校(1,1)
- 移動←→ : 列を反転（病院↔神社 / 都市↔学校）
- 移動↑↓ : 行を反転（病院↔都市 / 神社↔学校）
- 移動斜め: 両方反転（病院↔学校 / 神社↔都市）

移動合成（KB: 10）:
- 異種2枚は表どおり（↑↓+←→=斜め など）＝トグルのXOR。
- 同種2枚は「その移動が通常通り1回」＝元に戻らない（XOR=0にしない）。
- 1キャラに乗る移動カードは最大2枚（脚本家1＋主人公1）。

禁止先（KB: 10 FAQ A2 / 60 A-2）:
- 合成後の移動先がそのキャラの禁止エリアなら、移動は成立せず元エリアに留まる
  （片方だけ解決はされない）。
"""

from __future__ import annotations

from .models import MOVE_CARDS, Character

AREAS: dict[str, tuple[int, int]] = {
    "病院": (0, 0),
    "神社": (1, 0),
    "都市": (0, 1),
    "学校": (1, 1),
}
_COORD_TO_AREA = {v: k for k, v in AREAS.items()}

# 各移動カードのトグル（col反転, row反転）。
_MOVE_TOGGLE: dict[str, tuple[int, int]] = {
    "移動←→": (1, 0),
    "移動↑↓": (0, 1),
    "移動斜め": (1, 1),
}


def compose_moves(moves: list[str]) -> tuple[int, int]:
    """移動カード（0〜2枚）を合成し、(col反転, row反転) のトグルを返す。"""
    moves = [m for m in moves if m in MOVE_CARDS]
    if not moves:
        return (0, 0)
    if len(moves) == 1:
        return _MOVE_TOGGLE[moves[0]]
    if len(moves) == 2:
        a, b = moves
        if a == b:
            # 同種2枚＝その移動1回（KB: 10）。元に戻さない。
            return _MOVE_TOGGLE[a]
        ta, tb = _MOVE_TOGGLE[a], _MOVE_TOGGLE[b]
        return (ta[0] ^ tb[0], ta[1] ^ tb[1])
    raise ValueError("1キャラに乗る移動カードは最大2枚（脚本家1＋主人公1）")


def destination(area: str, toggle: tuple[int, int]) -> str:
    col, row = AREAS[area]
    dc, dr = toggle
    return _COORD_TO_AREA[(col ^ dc, row ^ dr)]


def resolve_move(char: Character, moves: list[str]) -> tuple[str, bool]:
    """キャラの移動を解決し、(移動後エリア, 移動が阻まれたか) を返す。

    阻まれたか = 移動先が禁止エリアで留まった場合 True。
    """
    toggle = compose_moves(moves)
    if toggle == (0, 0):
        return char.area, False
    dest = destination(char.area, toggle)
    if dest in char.forbidden:
        # 禁止先 → 移動せず留まる（KB: 60 A-2）。
        return char.area, True
    return dest, False
