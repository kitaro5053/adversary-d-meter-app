"""ヒューリスティック主人公bot（M5）— belief.py の推理を使って脚本家の勝ち筋を妨害する。

主人公は脚本家の手を見ずに（伏せて同時に）カードを置くので、belief の可能世界追跡で
「誰がキーパーソン／キラーか」「どのボードが危ないか」を推定し、防御的に置く。
1体で3席を担当し、席間の協調（暗躍禁止の自滅回避＝1ターン1枚だけ）を内部状態で行う。

守りの定石（主人公が使える8枚＝移動/移動禁止/友好±/不安±/暗躍禁止）:
- 暗躍禁止：推定キーパーソン（キラー殺害の前提＝暗躍≥2を封じる）か、暗躍が積まれたボードへ。
  ★複数主人公が暗躍禁止を出すと自滅（KB）→ 1ターンに1枚だけ。
- 不安-1：不安臨界に近いキャラの不安を下げて事件発生を防ぐ。
- 移動禁止：推定キラーの移動を止め、キーパーソンへ寄せない。
"""

from __future__ import annotations

from engine.data import unrest_threshold_of

from .belief_v1 import Belief

_KEYPERSON_MIN = 0.5   # この確率以上ならキーパーソンとみなして守る
_KILLER_MIN = 0.4


class HeuristicProtagonist:
    def __init__(self, seed: int = 0):
        self.seed = seed
        self._belief: Belief | None = None
        self._turn = None            # (loop, day) 現ターン識別
        self._kinshi_used = False    # このターンに暗躍禁止を出したか（自滅回避）
        # ターン単位でキャッシュする推定（可能世界の走査は1ターン1回に抑える）
        self._keyperson: str | None = None
        self._killer: str | None = None
        self._p_guard = 0.0
        self._culprits: set[str] = set()

    # -- ターン境界で belief を更新し、推定をキャッシュ ----------------------

    def _sync(self, view: dict) -> None:
        if self._belief is None:
            cast = [c["name"] for c in view["characters"]]
            self._belief = Belief(cast, view.get("incidents", []),
                                  set_name=view.get("set", "FS"))
        turn = (view["loop"], view["day"])
        if turn != self._turn:
            self._turn = turn
            self._kinshi_used = False
            self._belief.observe(view.get("history", []))
            self._recompute(view)

    def _recompute(self, view: dict) -> None:
        """belief の周辺確率から、判断に使う推定をまとめて作る（decideでは走査しない）。"""
        def believed(role: str, min_p: float) -> str | None:
            name, p = self._belief.most_likely_role(role)
            return name if (name and p >= min_p) else None

        self._keyperson = believed("キーパーソン", _KEYPERSON_MIN)
        self._killer = believed("キラー", _KILLER_MIN)
        self._p_guard = sum(p for (ry, _rxs), p in self._belief.rule_marginals().items()
                            if ry == "守るべき場所")
        cand = self._belief.culprit_candidates()
        self._culprits = set().union(*cand.values()) if cand else set()

    # -- 推定材料 ----------------------------------------------------------

    def _guess_defeat_board(self, view: dict) -> str | None:
        if self._p_guard >= 0.5:
            return "学校"
        # 暗躍が積まれているボードを危険とみなす
        ba = view.get("board_anyaku", {})
        top = max(ba, key=lambda a: ba[a]) if ba else None
        return top if top and ba.get(top, 0) > 0 else None

    def _alive(self, view: dict, name: str | None) -> dict | None:
        if not name:
            return None
        for c in view["characters"]:
            if c["name"] == name and c["alive"] and c["area"] is not None:
                return c
        return None

    def _risk(self, c: dict) -> float:
        """事件発生の近さ（不安/臨界）。不安0や臨界不明は0。"""
        th = unrest_threshold_of(c["name"])
        if not th or c["unrest"] <= 0:
            return 0.0
        return c["unrest"] / th

    # -- 決定 --------------------------------------------------------------

    _AREAS = ("病院", "神社", "都市", "学校")

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision == "final_battle_guess":
            # 最後の戦い：全履歴で belief を更新し、そのキャラの最有力役職を宣言する
            if self._belief is None:
                self._belief = Belief([c["name"] for c in view["characters"]],
                                      view.get("incidents", []), set_name=view.get("set", "FS"))
            self._belief.observe(view.get("history", []))
            name = options[0]["character"]
            marg = self._belief.role_marginals().get(name, {})
            best = max(marg, key=marg.get) if marg else None
            return next((o for o in options if o["role"] == best), options[0])
        if decision == "goodwill_ability":
            self._sync(view)
            return self._choose_goodwill(view, options)
        if decision != "set_card":
            return options[0]  # その他（現状は該当なし）
        self._sync(view)
        keyperson = self._keyperson
        killer = self._killer
        danger_board = self._guess_defeat_board(view)
        culprits = self._culprits

        def score(o: dict) -> float:
            card, tgt, kind = o["card"], o["target"], o.get("target_kind")
            if card == "暗躍禁止":
                if self._kinshi_used:
                    return -100.0  # 1ターン2枚目は自滅（絶対に避ける）
                if kind == "character" and tgt == keyperson and self._alive(view, keyperson):
                    return 100.0   # キラー殺害の前提（暗躍≥2）を封じる
                if kind == "board" and view["board_anyaku"].get(tgt, 0) > 0:
                    return 80.0 + view["board_anyaku"][tgt]
                if kind == "board" and tgt == danger_board:
                    return 60.0
                return 4.0
            if card == "不安-1" and kind == "character":
                c = self._alive(view, tgt)
                if not c:
                    return 0.0
                s = 30.0 * self._risk(c)
                if tgt in culprits:
                    s += 5.0
                return s
            if card == "移動禁止" and kind == "character" and tgt == killer \
                    and self._alive(view, killer):
                return 50.0
            if card in ("友好+1", "友好+2") and kind == "character":
                # 有益な友好能力（暗躍除去・情報開示）を使えるよう友好を貯める
                val = 2.0 if card == "友好+2" else 1.0
                if tgt == "巫女" and danger_board == "神社":
                    return 22.0 + val   # 神社の暗躍除去を解禁（敗北ボードが神社のとき）
                if tgt in ("サラリーマン", "巫女", "刑事", "神格", "転校生", "医者"):
                    return 12.0 + val   # 開示/除去で belief と守りを厚くする
                return 8.0
            return 1.0

        best = max(options, key=score)
        if best["card"] == "暗躍禁止":
            self._kinshi_used = True
        return best

    def _choose_goodwill(self, view: dict, options: list[dict]) -> dict:
        """使える友好能力から最も有益なものを選ぶ（暗躍除去＞情報開示＞不安除去）。"""
        danger = self._guess_defeat_board(view)

        def gscore(o: dict) -> float:
            if o.get("action") == "pass":
                return 1.0
            ability, tgt = o["ability"], o["target"]
            if "暗躍除去" in ability:  # 敗北条件ボード/暗躍持ちの暗躍を剥がす（最優先）
                if tgt in self._AREAS:
                    base = 100.0 + 10.0 * view["board_anyaku"].get(tgt, 0)
                    return base + (20.0 if tgt == danger else 0.0)
                c = self._alive(view, tgt)
                return 60.0 + 10.0 * (c["anyaku"] if c else 0)
            if "役職開示" in ability or "犯人開示" in ability:
                return 40.0  # belief を絞る＝以後の守りが正確に
            if "不安" in ability and "除去" in ability:
                c = self._alive(view, tgt)
                return 25.0 * self._risk(c) if c else 0.0
            if "友好" in ability:
                return 6.0
            return 3.0

        return max(options, key=gscore)
