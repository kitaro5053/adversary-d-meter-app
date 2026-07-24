"""ヒューリスティック脚本家bot（M3）。

脚本家の勝ち筋（FS）を定石化して、ランダム相手のベースライン（勝率ほぼ0%）から
どれだけ上げられるかを測るための、決定的スコアリングbot。ルールはハードコードせず、
シミュレータが渡す合法手（options）だけから選ぶ点は他エージェントと同じ。

狙う勝ち筋（view の rule_y / 配役 / 事件から自動判定）:
1. キーパーソン殺害＝主人公敗北＋ループ終了（殺人計画・守るべき場所）。
   → キーパーソンに暗躍を積み（≥2）、同エリアにキラーを寄せ、ターン終了で殺す。
2. ボード暗躍≥2（守るべき場所＝学校／復讐者の灯火＝クロマク初期ボード）。
   → クロマク・不穏な噂（暗躍禁止で止まらない）＋暗躍カードで積む。
3. 主人公殺害（病院の事件＝病院暗躍≥2／キラー自身に暗躍≥4）＝どのルールでも勝ち。
   → 病院に暗躍を積み、その日の事件犯人の不安を臨界へ運んで事件を発生させる。

主人公側は当面ランダム（M3は脚本家優先）。主人公ヒューリスティックはbelief.pyと対で後段。
"""

from __future__ import annotations

import random

from engine.board import AREAS
from engine.data import unrest_threshold_of
from engine.models import MOVE_CARDS

_ANRYAKU_VALUE = {"暗躍+1": 1, "暗躍+2": 2}


def _single_move_card(src: str, dst: str) -> str | None:
    """src→dst へ1枚で移せる移動カード名。同エリア/到達不能なら None。"""
    sx, sy = AREAS[src]
    dx, dy = AREAS[dst]
    toggle = (sx ^ dx, sy ^ dy)
    return {(1, 0): "移動←→", (0, 1): "移動↑↓", (1, 1): "移動斜め"}.get(toggle)


class HeuristicMastermind:
    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    # -- 盤面分析（view から勝ち筋の材料を取り出す） -----------------------

    def _analyze(self, view: dict) -> dict:
        roles = view["roles"]
        chars = {c["name"]: c for c in view["characters"]}
        keyperson = next((n for n, r in roles.items() if r == "キーパーソン"), None)
        killer = next((n for n, r in roles.items() if r == "キラー"), None)
        friends = {n for n, r in roles.items() if r == "フレンド"}

        goal_boards: set[str] = set()
        ry = view["rule_y"]
        if ry == "守るべき場所":            # FS：学校≥2
            goal_boards.add("学校")
        elif ry == "封印されしモノ":         # BTX：神社≥2
            goal_boards.add("神社")
        elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") and view.get("rule_y_board_x"):
            goal_boards.add(view["rule_y_board_x"])  # FS/BTX：ボードX（クロマク/ウィッチ初期）≥2
        # 病院の事件があれば病院暗躍≥2＝主人公殺害の勝ち筋（どのルールでも）
        for inc in view["incidents"]:
            if inc["name"] == "病院の事件":
                goal_boards.add("病院")

        day = view["day"]
        today_culprit = next((inc["culprit"] for inc in view["incidents"]
                              if inc["day"] == day), None)
        return {"chars": chars, "keyperson": keyperson, "killer": killer,
                "friends": friends, "goal_boards": goal_boards,
                "today_culprit": today_culprit}

    def _pick(self, options: list[dict], score) -> dict:
        """最大スコアの手を返す（同点は seed 固定の rng でタイブレーク）。"""
        best, best_key = None, None
        for o in options:
            key = (score(o), self.rng.random())
            if best_key is None or key > best_key:
                best, best_key = o, key
        return best

    # -- 決定 --------------------------------------------------------------

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision == "goodwill_refuse":
            # 拒否できる友好能力は主人公に有利（暗躍除去・情報開示）＝常に拒否して妨害する
            return next(o for o in options if o.get("refuse") is True)
        a = self._analyze(view)
        if decision == "set_card":
            return self._pick(options, lambda o: self._score_set(o, a, view))
        if decision == "mastermind_ability":
            return self._pick(options, lambda o: self._score_ability(o, a))
        if decision == "turn_end_ability":
            return self._pick(options, lambda o: self._score_turn_end(o))
        if decision == "incident_choice":
            return self._pick(options, lambda o: self._score_incident(o, a))
        if decision == "loop_start_area":
            return self._pick(options, lambda o: self._score_loop_area(o, view))
        # goodwill_ability 等（脚本家に選択肢がある場面は現状ほぼ pass のみ）
        return options[0]

    def _score_set(self, o: dict, a: dict, view: dict) -> float:
        card, tgt, kind = o["card"], o["target"], o["target_kind"]
        val = _ANRYAKU_VALUE.get(card, 0)
        chars = a["chars"]
        # (1) ゴールボードに暗躍を積む（学校/ボードX/病院）
        if kind == "board" and val and tgt in a["goal_boards"]:
            return 100 + val * 10
        # (2) キーパーソンに暗躍（キラー殺害の前提＝暗躍≥2）
        if kind == "character" and val and tgt == a["keyperson"]:
            return 30 if chars[tgt]["anyaku"] >= 2 else 90 + val * 10
        # (3) その日の事件犯人の不安を臨界へ（事件を発生させる）
        if card == "不安+1" and kind == "character" and tgt == a["today_culprit"]:
            c = chars.get(tgt)
            th = unrest_threshold_of(tgt)
            if c and c["alive"] and th is not None and c["unrest"] < th:
                return 60
        # (4) キラーをキーパーソンのエリアへ寄せる移動
        if card in MOVE_CARDS and kind == "character" and tgt == a["killer"] \
                and a["keyperson"]:
            kp, kl = chars.get(a["keyperson"]), chars.get(a["killer"])
            if kp and kl and kp["area"] and kl["area"] and kp["area"] != kl["area"]:
                if _single_move_card(kl["area"], kp["area"]) == card:
                    return 50
            return 3
        return 0

    def _score_ability(self, o: dict, a: dict) -> float:
        if o.get("action") == "pass":
            return 1  # 何も得が無ければパス（負スコアは作らない）
        if o["kind"] == "anyaku":
            if o["target_kind"] == "board" and o["target"] in a["goal_boards"]:
                return 100
            if o["target_kind"] == "character" and o["target"] == a["keyperson"]:
                return 90
            return 20
        if o["kind"] == "unrest":
            # ミスリーダー：その日の事件犯人の不安を上げて事件を発生させる
            if o["target"] == a["today_culprit"]:
                return 80
            return 10
        return 0

    def _score_turn_end(self, o: dict) -> float:
        # キラーの殺害は全て勝ちに直結（主人公殺害＞キーパーソン殺害）。パスは避ける。
        if o.get("action") == "pass":
            return 0
        return 100 if o.get("target") == "主人公" else 90

    def _score_incident(self, o: dict, a: dict) -> float:
        tgt = o.get("target")
        # 行方不明などボード選択：ゴールボードへ動かして暗躍を足す
        if tgt in AREAS:
            return 100 if tgt in a["goal_boards"] else 10
        # キーパーソン殺害（勝ち）＞フレンド殺害（ループ終了時敗北）＞その他
        if tgt == a["keyperson"]:
            return 100
        if tgt in a["friends"]:
            return 70
        return 5

    def _score_loop_area(self, o: dict, view: dict) -> float:
        # 手先の初期エリア。復讐者の灯火で手先＝クロマクならボードXになるので、
        # 病院の事件があれば病院（勝ち筋を集中）、無ければ学校を選ぶ。
        area = o.get("area")
        if view["rule_y"] == "復讐者の灯火":
            want = "病院" if any(i["name"] == "病院の事件" for i in view["incidents"]) else "学校"
            return 10 if area == want else 1
        return 1
