"""情報可視性 — 脚本家ビュー（全情報）と主人公ビュー（公開情報のみ）。

AIエージェントと決定ログにはこのモジュールの view だけを渡す。
主人公ビューに配役・犯人・ルールY/Xが混入したら即カンニングバグ
（tests/test_sim_state.py のリークテストで固定）。

公開情報の根拠（KB: 00）:
- 公開シート＝セット名・ループ回数・日数・登場キャラ・事件リスト（日＋事件名）。
  配役・犯人・ルールY/Xは非公開シート（00:76）。
- カウンター（不安・友好・暗躍）と生死・位置は盤上の事実＝両陣営に見える。
- 1/loopの使用済みカードは表向きで手前に置く＝全員分公開（00:108）。
- 能力等で公開された役職（revealed_roles）は以後主人公にも見える。
- 裏向きセット中のカードは「誰がどこに置いたか」は公開、中身は持ち主のみ
  （行動解決フェイズの公開時に history へ全カードが記録される＝00:106）。
"""

from __future__ import annotations

from .state import PROTAGONIST_SEATS, GameState


def _public_char(state: GameState, name: str) -> dict:
    c = state.characters.get(name)
    if c is None:
        # ループ準備前（loop_start_area 決定時点等）＝盤面未構築
        return {"name": name, "area": None, "alive": True,
                "unrest": 0, "goodwill": 0, "anyaku": 0}
    d = {
        "name": c.name,
        "area": c.area,       # None＝未登場
        "alive": c.alive,
        "unrest": c.unrest,
        "goodwill": c.goodwill,
        "anyaku": c.anyaku,
    }
    if name in state.revealed_roles:
        d["revealed_role"] = state.revealed_roles[name]
    return d


def _masked_placements(state: GameState, seat: str) -> list[dict]:
    """裏向きセット札：位置と持ち主は公開、カード名は自分の分だけ。"""
    out = []
    for p in state.turn_placements:
        d = {"owner": p["owner"], "target": p["target"], "target_kind": p["target_kind"]}
        if p["owner"] == seat:
            d["card"] = p["card"]
        out.append(d)
    return out


def _common(state: GameState, seat: str) -> dict:
    """両陣営共通の公開情報。"""
    return {
        "set": state.script.set_name,
        "loop": state.loop_no,
        "loops_total": state.script.loops,
        "day": state.day,
        "days_per_loop": state.script.days_per_loop,
        "phase": state.phase,
        "leader": state.leader,
        "characters": [_public_char(state, n) for n in state.script.cast],
        "board_anyaku": dict(state.board_anyaku),
        # 事件リストは日＋事件名のみ公開（犯人は非公開シート）
        "incidents": [{"day": i.day, "name": i.name} for i in state.script.incidents],
        "used_cards": {o: list(v) for o, v in state.used_cards.items()},
        # 大物の縄張りトークン＝盤上に置かれる公開情報（居なければNone）
        "oomono_territory": state.script.oomono_territory,
        "placements": _masked_placements(state, seat),
        "history": list(state.history),
        "game_over": state.game_over,
        "winner": state.winner,
    }


def protagonist_view(state: GameState, seat: str) -> dict:
    """主人公1席分のビュー。自席の手札＋公開情報のみ（秘匿情報は一切含めない）。"""
    if seat not in PROTAGONIST_SEATS:
        raise ValueError(f"不明な席: {seat}")
    view = _common(state, seat)
    view["seat"] = seat
    view["hand"] = state.hand_of(seat)
    return view


def mastermind_view(state: GameState) -> dict:
    """脚本家ビュー＝公開情報＋脚本の真実（配役・犯人・ルール）＋自手札＋神視点ログ。"""
    view = _common(state, "mastermind")
    view["seat"] = "mastermind"
    view["hand"] = state.hand_of("mastermind")
    view["rule_y"] = state.script.rule_y
    view["rule_x"] = state.script.rule_x
    view["rule_x2"] = state.script.rule_x2  # BTXの2枚目（脚本家は自分の脚本を知っている）
    view["rule_y_board_x"] = state.rule_y_board_x
    view["roles"] = {n: state.script.role_of(n) for n in state.script.cast}
    # ★B-50 段階C（ユーザー正典 2026-07-24）：登場が遅れるキャラの**登場日／登場ループ**は
    #   脚本家に最初から明示される（脚本家は自分の脚本を知っている）。転校生＝登場日・
    #   神格＝登場ループ。未登場そのものは characters[].area=None で表現済み（cast由来なので
    #   一覧からは消えない）＝ここで「いつ出るか」を補う。
    #   ※アルバイト？はアルバイトの死亡が条件＝日付で決まらないため entry_* には載らない。
    view["entry_days"] = dict(state.script.entry_days)
    view["entry_loops"] = dict(state.script.entry_loops)
    view["incidents"] = [
        {"day": i.day, "name": i.name, "culprit": i.culprit} for i in state.script.incidents
    ]
    view["secret_log"] = list(state.secret_log)
    return view
