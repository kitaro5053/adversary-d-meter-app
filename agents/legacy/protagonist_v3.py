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

from engine.board import destination
from engine.data import forbidden_of, goodwill_abilities_of, unrest_threshold_of

from .belief_v3 import Belief

_MOVE_TOGGLE = {"移動←→": (1, 0), "移動↑↓": (0, 1)}  # 主人公の移動カード（斜めは無い）


def _move_dest(src: str | None, card: str) -> str | None:
    """移動カードで src からどこへ行くか（未登場/非移動カードは None）。"""
    t = _MOVE_TOGGLE.get(card)
    return destination(src, t) if (src and t) else None

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
            # ループ初日の最初の観測＝初期配置（公開情報）。ボードX推定などに使う。
            if self._turn is None or turn[0] != self._turn[0]:
                self._loop_initial_areas = {c["name"]: c["area"]
                                            for c in view["characters"]}
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
        self._culprit_cands = cand  # 日→犯人候補集合（危険事件の予防冷却の照準）
        # 犯人が1人に確定している日（開示・消去法）→ 事件対策（不安-1・退避）の照準になる
        self._known_culprits = {d: next(iter(s)) for d, s in cand.items() if len(s) == 1}
        # ★危険事件（ルール接地）：事件の効果が敗北条件に直結する日＝候補犯人でも予防冷却する。
        #   蝶の羽ばたき＝未来改変プランなら発生即敗北条件成立／邪気の汚染＝神社+2（封印されしモノ）。
        rules_m = self._belief.rule_marginals()
        p_future = sum(p for (ry, _x), p in rules_m.items() if ry == "未来改変プラン")
        p_seal = sum(p for (ry, _x), p in rules_m.items() if ry == "封印されしモノ")
        self._incident_danger: dict[int, float] = {}
        for inc in view.get("incidents", []):
            nm, d = inc.get("name"), inc.get("day")
            if nm == "蝶の羽ばたき" and p_future > 0.2:
                self._incident_danger[d] = 80.0 * p_future
            elif nm == "邪気の汚染" and p_seal > 0.2:
                self._incident_danger[d] = 65.0 * p_seal
        # ★タイムトラベラーの任意敗北（最終日・友好≤2）封じ：TT疑いの友好を3以上に保てば
        #   ルール上宣言できない。TTへの友好+は友好禁止を無視する（KB: 50）＝止められない対策。
        #   キャラ特定の確信が低くても、ルール側でTT必在がほぼ確定なら最有力候補に張る
        #   （張らなければ毎ループ最終日に確実に負ける＝外れても期待値で勝る）。
        p_tt_rule = p_future  # TTを足すルールは未来改変プラン（Y）のみ
        tt_name, tt_p = self._belief.most_likely_role("タイムトラベラー")
        self._tt_guard = None
        if tt_name and (tt_p >= 0.5 or (tt_p >= 0.15 and p_tt_rule >= 0.7)):
            self._tt_guard = tt_name
        # キラーでありうるキャラ（周辺確率>0）＝暗躍が積まれたら監視対象（ルール：暗躍4で主人公死亡）
        marg = self._belief.role_marginals()
        self._killer_suspects = {n for n, d in marg.items() if d.get("キラー", 0) > 0}
        # 確信度の高い推定（先回り防御用。完全情報ならp=1で初手から発火する）
        self._killer_strong = {n for n, d in marg.items() if d.get("キラー", 0) >= 0.9}
        self._kuromaku_suspects = {n for n, d in marg.items() if d.get("クロマク", 0) >= 0.7}
        # シリアルキラー疑い（移動先の安全判定用＝2人きりに送らない）
        self._sk_suspects = {n for n, d in marg.items() if d.get("シリアルキラー", 0) >= 0.5}
        # カルティスト疑い（ゴールボードの暗躍禁止を無効化される＝引き剥がし対象）
        self._cultist_suspects = {n for n, d in marg.items() if d.get("カルティスト", 0) >= 0.7}
        # ★キーパーソンの暗躍禁止の価値はルール接地で決まる：キラーが居るか「僕と契約」
        #   （KP暗躍≥2で敗北）の可能性がある時だけ意味がある。どちらも消えたら守る価値なし
        #   （guard型でキーパーソンに暗躍禁止を貼り続けてゴールボードを素通しした実測の教訓）。
        p_keiyaku = sum(p for (ry, _rxs), p in self._belief.rule_marginals().items()
                        if ry == "僕と契約しようよ！")
        self._kp_guard = 100.0 if (self._killer_suspects or p_keiyaku > 0) else 5.0
        # ★情報収集プレイ：役職開示の情報価値＝Gini不純度（開示後の期待残存世界がW·Σp²）
        self._gini = {n: self._belief.role_gini(n) for n in self._belief.cast}
        self._top_rule_p = self._belief.top_rule_prob()
        self._culprit_sizes = {d: len(s) for d, s in cand.items()} if cand else {}
        # ★負けループ判定（手練れの知見）：敗北ボードが既に2以上＝このループはほぼ負け
        #   → 勝ちに行くのをやめ、情報収穫モード（殺害で役職確認・事件を意図的に発生）へ。
        db = self._guess_defeat_board(view)
        self._loop_lost = bool(db and view.get("board_anyaku", {}).get(db, 0) >= 2)
        # ★実験モード（手練れの知見）：既にループを落としていて、かつルールXYが未確定なら、
        #   勝ちに行くより情報を取りに行く（負け筋の中では死も事件もタダの情報源）。
        lost_loops = sum(1 for e in view.get("history", [])
                         if e.get("event") == "loop_result"
                         and "敗北" in str(e.get("result", "")))
        self._experiment = self._top_rule_p < 0.999 and lost_loops >= 1
        # SK炙り出しテストの価値：SKを足すルールXの在/不在がまだ割れているか
        #（潜む殺人鬼/切り裂き魔の影＝SK追加。ウイルスは役職でなく状態なので対象外）
        p_sk = sum(p for (_ry, rxs), p in self._belief.rule_marginals().items()
                   if "潜む殺人鬼" in rxs or "切り裂き魔の影" in rxs)
        self._sk_uncertain = 0.02 < p_sk < 0.98
        # テスト相手候補＝SKでありうる（確率>0）未確定のキャラ
        self._sk_mystery = {n for n, d in marg.items()
                            if 0.0 < d.get("シリアルキラー", 0) < 0.999}
        # ★ウイルス試験（BTX）：妄想拡大ウイルスの在/不在が割れているなら、パーソン疑いを
        #   不安3まで上げる価値がある（ウイルス真ならSK化して2人きりで殺す＝確定。
        #   殺さなければ「不安3で無事」の否定形がウイルス側の割当を削る。どちらでも情報）。
        p_virus = sum(p for (_ry, rxs), p in self._belief.rule_marginals().items()
                      if "妄想拡大ウイルス" in rxs)
        self._virus_uncertain = 0.02 < p_virus < 0.98
        # 試験対象＝パーソンでありうる上位2名（絶対閾値だと残存組次第で空になる実測の教訓）。
        # ★既に「不安≥3でペア生存」を観測済みのキャラは試験済み＝情報が涸れている→除外して
        #   次の候補に回す（同じキャラを毎ループ試験して他が手つかずだった実測の教訓）。
        self._virus_test_targets: set = set()
        if self._virus_uncertain:
            tested_hot: set = set()
            for e in view.get("history", []):
                if e.get("event") == "turn_end_pairs":
                    for pr in e.get("pairs", []):
                        for n in pr:
                            if e.get("unrest", {}).get(n, 0) >= 3:
                                tested_hot.add(n)
            cand = [(d.get("パーソン", 0), n) for n, d in marg.items()
                    if d.get("パーソン", 0) > 0.1 and n not in tested_hot]
            self._virus_test_targets = {n for _p, n in sorted(cand, reverse=True)[:2]}
        self._invest = self._compute_invest(view)
        # ★浄化係（危険ボードを剥がせる暗躍除去持ち）＝ハーツ投資の最優先ターゲット。
        #   席間の椅子取りで友好+2が他所に散って3ハーツに届かない事故を防ぐため一元化。
        self._purge_target = None
        danger_b = self._guess_defeat_board(view)
        if danger_b:
            from sim.abilities import is_implemented
            for cv in view["characters"]:
                if not cv["alive"] or cv["area"] is None:
                    continue
                for ab in goodwill_abilities_of(cv["name"]) or []:
                    if ("暗躍除去" in ab["name"]
                            and is_implemented(cv["name"], ab["name"])
                            and cv["goodwill"] < ab["hearts"]
                            and self._ability_value(cv["name"], ab["name"], None, view) >= 60):
                        self._purge_target = cv["name"]
                        break
                if self._purge_target:
                    break
        # このループで脚本家が友好禁止を当てた相手（公開）＝友好投資を分散して回避
        self._gw_blocked = set()
        # このループで脚本家能力フェイズに暗躍を置かれたキャラ＝クロマクが同エリアに居る証拠
        # （能力の対象条件がルールで「同一エリア」だから）。位置戦術のトリガー。
        self._mm_pump_victims: set[str] = set()
        for e in view.get("history", []):
            if e.get("loop") != view["loop"]:
                continue
            if e.get("event") == "cards_revealed":
                for p in e.get("placements", []):
                    if p.get("owner") == "mastermind" and p.get("card") == "友好禁止":
                        self._gw_blocked.add(p.get("target"))
            elif (e.get("event") == "anyaku" and e.get("phase") == "mastermind_ability"
                    and e.get("delta", 0) > 0):
                self._mm_pump_victims.add(e.get("target"))

    # -- 情報収集プレイ（能力の情報価値と友好投資先） ------------------------

    def _ability_value(self, user: str, ability: str, target: str | None, view: dict) -> float:
        """友好能力1件の価値（効果＋情報）。target=None は投資評価用（最良ターゲット想定）。"""
        if "役職開示" in ability:
            # 開示宣言は両取りのプローブ：通れば役職判明、拒否されれば友好無視バレ。
            g = self._gini.get(target, 0.0) if target is not None \
                else max([self._gini.get(n, 0.0) for n in self._gini] or [0.0])
            return 2.0 if g < 0.02 else 30.0 + 90.0 * g
        if "犯人開示" in ability:
            if target and target.startswith("事件"):
                k = self._culprit_sizes.get(int(target.replace("事件", "").replace("日目", "")), 1)
            else:
                k = max(self._culprit_sizes.values() or [1])
            return min(65.0, 20.0 + 15.0 * (k - 1)) if k > 1 else 2.0
        if "ルールX" in ability:
            return 15.0 + 60.0 * (1.0 - self._top_rule_p)
        if "暗躍" in ability and "除去" in ability:
            danger = self._guess_defeat_board(view)
            if user == "巫女":
                return 80.0 if danger == "神社" else 10.0
            return 60.0 if danger or self._keyperson else 20.0
        if "戻す" in ability:
            # ★手札戦術（委員長）：使用済み1/Lカードの回収。価値はカードの強さで決まる。
            #   友好+2＝投資エンジン ＞ 移動禁止＝キラー足止め ＞ 不安-1＝事件抑止。
            if target is None:  # 投資評価：リーダーの使用済み1Lがあれば価値あり
                return 25.0
            return {"友好+2": 35.0, "移動禁止": 22.0, "不安-1": 18.0}.get(target, 10.0)
        if "不安" in ability and "除去" in ability:
            return 15.0
        if "殺害" in ability:
            # ★情報収穫キル（手練れの知見）：負けループ／実験モード（負けが込んでいて
            #   ルールXY未確定）では疑わしいキャラを殺して確認する（キーパーソン疑いなら
            #   即ループ終了で確定、フレンドならループ終了時に公開、ラバーズなら相方に+6）。
            if getattr(self, "_loop_lost", False) or getattr(self, "_experiment", False):
                g = self._gini.get(target, 0.0) if target is not None \
                    else max([v for v in self._gini.values()] or [0.0])
                return 20.0 + 85.0 * g
            return 3.0  # 通常時に味方候補を殺すのは損
        if "不安+1" in ability:
            # ★意図的な事件発生（手練れの知見）：負けループで犯人候補に不安を載せ、
            #   事件の発生/不発から犯人・臨界の情報を取る。
            if (getattr(self, "_loop_lost", False)
                    or getattr(self, "_experiment", False)) and target in self._culprits:
                return 30.0
            return 2.0
        if "暗躍+1" in ability:
            return 1.0  # 自陣に暗躍を足すのは基本損（マスコミ能力2）
        return 6.0

    def _compute_invest(self, view: dict) -> dict[str, float]:
        """友好+カードの投資先スコア＝実装済み能力の（価値 ÷ 残り必要ハート）の最大値。"""
        from sim.abilities import is_implemented
        invest: dict[str, float] = {}
        for c in view["characters"]:
            n = c["name"]
            if not c["alive"] or c["area"] is None:
                continue
            for ab in goodwill_abilities_of(n) or []:
                if not is_implemented(n, ab["name"]):
                    continue
                need = ab["hearts"] - c["goodwill"]
                if need <= 0:
                    continue  # もう使える＝投資不要
                val = self._ability_value(n, ab["name"], None, view)
                invest[n] = max(invest.get(n, 0.0), val / (1.0 + need))
        return invest

    # -- 推定材料 ----------------------------------------------------------

    def _guess_defeat_board(self, view: dict) -> str | None:
        if self._p_guard >= 0.5:
            return "学校"
        # ★ボードX型（復讐者の灯火＝クロマク初期／巨大時限爆弾＝ウィッチ初期）：
        #   初期配置は公開情報なので、役職の推定が立てばボードXを特定できる。
        rules = self._belief.rule_marginals()
        init = getattr(self, "_loop_initial_areas", {})
        for ry_name, role in (("復讐者の灯火", "クロマク"), ("巨大時限爆弾Xの存在", "ウィッチ")):
            p = sum(pv for (ry, _rxs), pv in rules.items() if ry == ry_name)
            if p >= 0.5:
                name, rp = self._belief.most_likely_role(role)
                if name and rp >= 0.7 and init.get(name):
                    return init[name]
        # ★ボード条件ルールの可能性がほぼ消えているなら、ボードは危険ではない
        #   （未来改変プラン確定なのに黒猫の神社暗躍へ暗躍禁止を毎日無駄撃ちした実測の教訓。
        #     僕と契約はキャラ暗躍条件＝キーパーソン防御側で扱う）
        p_board_rules = sum(pv for (ry, _rxs), pv in rules.items()
                            if ry in ("守るべき場所", "封印されしモノ",
                                      "復讐者の灯火", "巨大時限爆弾Xの存在"))
        if p_board_rules < 0.05:
            return None
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
            # 最後の戦い：★逐次条件付け＝これまでの正解宣言（誤答なら戦いは終わっている＝
            # 続いている以上すべて正解）を確定情報として belief に足し、残りを再計算して宣言。
            # 独立argmaxと違い、役職スロット数と整合した一貫性のある宣言列になる。
            if self._belief is None:
                self._belief = Belief([c["name"] for c in view["characters"]],
                                      view.get("incidents", []), set_name=view.get("set", "FS"))
            hist = list(view.get("history", []))
            confirmed = [
                {"loop": e.get("loop"), "day": 0, "event": "role_reveal",
                 "name": e["name"], "role": e["guess"]}
                for e in hist if e.get("event") == "final_battle" and e.get("correct")
            ]
            self._belief.observe(hist + confirmed)
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
                # ★キラー疑いのキャラに暗躍が積まれている＝暗躍4で主人公死亡（ルール）の兆候。
                #   3以上なら次の+1で死＝キーパーソン防御より優先して封じる。
                if kind == "character" and tgt in self._killer_suspects:
                    c = self._alive(view, tgt)
                    if c and c["anyaku"] >= 3:
                        return 110.0
                    # ★先制封鎖：確信キラーがクロマク疑いと同エリア＝ポンプ即開始が読める。
                    #   暗躍0でも暗躍禁止で+2カードを止める（能力+1だけでは4に届かない）。
                    #   ※キーパーソン防御(100)より弱く＝初手の必須防御を乗っ取らない（実測の教訓）。
                    if (c and tgt in self._killer_strong
                            and any((kc := self._alive(view, k)) and kc["area"] == c["area"]
                                    for k in self._kuromaku_suspects if k != tgt)):
                        return 95.0
                    if c and c["anyaku"] >= 2:
                        return 70.0
                if kind == "character" and tgt == keyperson and self._alive(view, keyperson):
                    return self._kp_guard   # キラー/契約が可能な時だけ100（それ以外は無価値）
                # ★危険ボード（敗北条件のボード）は暗躍が0でも最優先で封じる
                #   （黒猫の神社暗躍などの「見えている暗躍」に釣られてボードXを空けない）
                if kind == "board" and tgt == danger_board:
                    return 88.0 + view["board_anyaku"].get(tgt, 0)
                if kind == "board" and view["board_anyaku"].get(tgt, 0) > 0:
                    return 80.0 + view["board_anyaku"][tgt]
                return 4.0
            if card == "不安-1" and kind == "character":
                c = self._alive(view, tgt)
                if not c:
                    return 0.0
                # ★危険事件の候補冷却（ルール接地）：効果が敗北条件に直結する事件
                #   （蝶の羽ばたき×未来改変・邪気の汚染×封印）は候補犯人ごと冷やす
                #   （発生条件は「犯人の不安≥臨界」＝不安を削れば必ず止まる）。
                #   ※確定犯人の一般予防（45）より先に判定＝確定して45に落ちる事故を防ぐ。
                th = unrest_threshold_of(tgt)
                if th:  # 臨界0（黒猫）は不安を下げても必ず発生＝冷却は無意味
                    for d, danger in getattr(self, "_incident_danger", {}).items():
                        if d >= view["day"] and tgt in getattr(self, "_culprit_cands", {}).get(d, ()):
                            # 事件当日＝冷やす最後の（D1事件なら唯一の）機会。臨界間際も同格。
                            if d == view["day"] or c["unrest"] >= th - 1:
                                return max(danger, 45.0)
                            if d - view["day"] <= 1:
                                return max(danger * 0.6, 30.0)
                # ★予防的な事件対策：犯人が確定している未消化の事件は、脚本家が毎ターン
                #   不安を仕込んでくる前提で先回りして冷やす（臨界間際なら最優先級）。
                for d, culp in self._known_culprits.items():
                    if culp == tgt and d >= view["day"] and th is not None:
                        if c["unrest"] >= th - 1:
                            return 70.0   # あと+1で発生＝今冷やす
                        if d - view["day"] <= 2:
                            return 45.0   # 事件日が近い＝仕込みを打ち消し続ける
                s = 30.0 * self._risk(c)
                if tgt in culprits:
                    s += 5.0
                return s
            if card == "移動禁止" and kind == "character" and tgt == killer \
                    and self._alive(view, killer):
                return 50.0
            # ★カルティストの進入ブロック：ゴールボード外に居るカルティスト疑いの移動を封じ、
            #   引き剥がし(c2)と合わせて暗躍禁止の実効を維持する（連れ戻し移動カードを無効化）。
            if card == "移動禁止" and kind == "character" and tgt in self._cultist_suspects \
                    and danger_board:
                cc = self._alive(view, tgt)
                if cc and cc["area"] != danger_board:
                    return 55.0
            # ★位置戦術（ルール接地）：
            #   キラーの殺害＝「同エリア＋キーパーソン暗躍2」／クロマクの能力＝「同エリア」。
            #   同エリア要求は移動で破れる。追跡には脚本家もカードを使う＝消耗戦に持ち込める。
            # ★実験モード：犯人候補に不安を載せて事件の発生/不発を観測する
            #   （発生＝eligible で犯人が絞れる・殺人系なら死からさらに情報が出る。
            #     ウイルス脚本なら不安3でパーソンがSK化して殺す＝それ自体が識別情報）。
            #   席分業：p3=実験係が最優先で担当（p1は防御・p2は投資に席を残す）。
            if card == "不安+1" and kind == "character" \
                    and getattr(self, "_experiment", False):
                in_culp = tgt in culprits
                in_virus = tgt in getattr(self, "_virus_test_targets", ())
                c = self._alive(view, tgt) if (in_culp or in_virus) else None
                if c is not None:
                    # ★実験の交代制：同じ実験の繰り返しは情報が涸れる（犯人ポンプが
                    #   毎ループ勝ってウイルス試験が一度も走らなかった実測の教訓）。
                    #   奇数ループはウイルス試験に高い段、偶数ループは犯人ポンプに高い段。
                    virus_first = (getattr(self, "_virus_uncertain", False)
                                   and view["loop"] % 2 == 1)
                    th = unrest_threshold_of(tgt)
                    culp_near = in_culp and th is not None and c["unrest"] >= th - 2
                    if virus_first and in_virus:
                        base = 60.0 if c["unrest"] >= 2 else 50.0  # 不安3圏内で価値大
                    elif not virus_first and in_culp:
                        base = 58.0 if culp_near else 40.0
                    elif in_virus:
                        base = 44.0 if c["unrest"] >= 2 else 34.0
                    else:  # in_culp（裏番）
                        base = 42.0 if culp_near else 30.0
                    if base > 0:
                        return base if view.get("seat") == "p3" else base - 14.0
            if card in _MOVE_TOGGLE and kind == "character":
                c = self._alive(view, tgt)
                dest = _move_dest(c["area"] if c else None, card)
                # ★実験モード：2人きりテスト。SK（とウイルスSK）の殺害は【強制】＝
                #   候補と2人きりを作れば結果（死/無事）が必ず出る。死ねばSK系ルールが確定し、
                #   無事なら否定形（SK外・不安3のパーソン不在）が世界を削る。
                #   対象＝SK疑い（SK系ルールが割れている時）＋不安の乗ったウイルス試験対象。
                if (getattr(self, "_experiment", False)
                        and c and dest and dest not in forbidden_of(tgt)):
                    test_set = set(self._sk_mystery) \
                        if getattr(self, "_sk_uncertain", False) else set()
                    for n in getattr(self, "_virus_test_targets", ()):
                        vc = self._alive(view, n)
                        if vc and vc["unrest"] >= 2:  # 不安2＝あと+1でSK化圏内
                            test_set.add(n)
                    if test_set:
                        occupants = [o["name"] for o in view["characters"]
                                     if o["alive"] and o["area"] == dest
                                     and o["name"] != tgt]
                        if len(occupants) == 1 and (
                                occupants[0] in test_set      # 候補の巣に送り込む
                                or tgt in test_set):          # 候補を1人の所へ送る
                            return 75.0
                # ★移動先の安全判定：シリアルキラー疑いのいるエリアへ味方候補を送らない
                #   （2人きり→ターン終了で殺される。実測の教訓：クロマク隔離が病院のSKへ直行した）
                sk_at_dest = any((sc := self._alive(view, s)) and sc["area"] == dest
                                 for s in self._sk_suspects if s != tgt)
                if c and dest and dest not in forbidden_of(tgt) and not sk_at_dest:
                    suspects_here = [
                        s for s in self._killer_suspects
                        if s != tgt and (sc := self._alive(view, s)) and sc["area"] == c["area"]
                    ]
                    kp_area = (self._alive(view, keyperson) or {}).get("area")
                    # (a) キーパーソンをキラー疑いから引き離す（暗躍2で殺害圏内＝最優先）
                    if tgt == keyperson and suspects_here:
                        if not any((self._alive(view, s) or {}).get("area") == dest
                                   for s in self._killer_suspects if s != tgt):
                            if c["anyaku"] >= 2:
                                return 120.0
                            if c["anyaku"] >= 1:
                                return 65.0
                    # (a2) ★事件当日、確定犯人がキーパーソンと同エリア＝殺人事件等の的にされる。
                    #      キーパーソンを退避（事件効果の「同一エリア」条件を破る）。
                    if tgt == keyperson:
                        culp_today = self._known_culprits.get(view["day"])
                        cu = self._alive(view, culp_today) if culp_today else None
                        if cu and cu["area"] == c["area"] and dest != cu["area"]:
                            return 115.0
                    # (b) クロマクポンプの犠牲者を逃がす（同エリア要求を破る）。
                    #     ただしキーパーソンのいるエリアへ送らない（キラーなら殺害圏に入る）。
                    if (tgt in self._mm_pump_victims
                            and (tgt == keyperson or tgt in self._killer_suspects)
                            and dest != kp_area):
                        return 95.0 if c["anyaku"] >= 2 else 55.0
                    # (c) ★先回りのポンプ断ち：発生源＝クロマク疑い自身を移動で引き離す。
                    #     被害者でなく源を動かす＝キラーへの暗躍禁止と両立できる（別キャラ＝別席）。
                    #     行き先に別の被害者（キーパーソン/確信キラー）が居るなら送らない。
                    if tgt in self._kuromaku_suspects:
                        victims_here = [v for v in (list(self._killer_strong) +
                                                    ([keyperson] if keyperson else []))
                                        if v != tgt and (vc := self._alive(view, v))
                                        and vc["area"] == c["area"]]
                        victims_dest = [v for v in (list(self._killer_strong) +
                                                    ([keyperson] if keyperson else []))
                                        if v != tgt and (vc := self._alive(view, v))
                                        and vc["area"] == dest]
                        if victims_here and not victims_dest:
                            return 100.0
                    # (c2) ★カルティスト引き剥がし：ゴールボード上のカルティスト疑いは
                    #      暗躍禁止を無効化する（行動解決の同エリア/自ボード無視）。
                    #      移動でボードから外せば暗躍禁止が実効に戻る。
                    if tgt in self._cultist_suspects and danger_board \
                            and c["area"] == danger_board and dest != danger_board:
                        return 90.0
                    # (c3) ★クロマクのボード汲み上げ断ち：クロマクの能力は「自分の立つボード」
                    #      にも暗躍を置ける。ゴールボードに立つクロマク疑いを移動で外せば、
                    #      能力の注ぎ先がゴールから逸れる（能力自体は止められないが的を変える）。
                    if tgt in self._kuromaku_suspects and danger_board \
                            and c["area"] == danger_board and dest != danger_board:
                        return 88.0
            if card in ("友好+1", "友好+2") and kind == "character":
                # ★TT任意敗北の封じ（ルール接地）：TT疑いの友好を3以上に保てば最終日の
                #   任意敗北を宣言できない（50:128）。TTへの友好+は【強制】友好禁止無視
                #   （KB: 50）＝脚本家に止める手が無い、確実に通る防御。
                if getattr(self, "_tt_guard", None) == tgt:
                    cc = self._alive(view, tgt)
                    if cc and cc["goodwill"] < 3:
                        return 64.0 if card == "友好+2" else 54.0
                # ★情報収集プレイ：投資先＝実装済み能力の（価値÷残り必要ハート）が最大のキャラ。
                #   開示能力の価値はGini（誰の役職が一番不確かか）で毎ターン再計算される。
                #   上位ターゲットに集中投資（分散すると誰も閾値に届かない。友好禁止は
                #   1枚/ターンなので2本柱で押せば1本は通る）。
                # ★このループで友好禁止を当てられた相手は避ける（脚本家の1枚を空振りに）
                if tgt in self._gw_blocked:
                    return 4.0
                # ★浄化係への最優先投資（ルール接地）：危険ボードを毎日剥がせる暗躍除去
                #   （巫女の神社暗躍除去等・1/Lでない）は敗北条件を直接削る＝早く3ハーツへ。
                #   投資が未完の間、友好+2（1/L・2ハーツ）を他所で浪費しない（席間の
                #   椅子取りで+2が散って3ハーツに届かず浄化ゼロで負けた実測の教訓）。
                purge = getattr(self, "_purge_target", None)
                if purge:
                    if tgt == purge:
                        return 62.0 if card == "友好+2" else 52.0
                    if card == "友好+2":
                        return 8.0  # 浄化係が3ハーツに届くまで温存
                inv = self._invest.get(tgt, 0.0)
                if inv <= 0:
                    return 6.0
                mx = max(self._invest.values())
                val = 2.0 if card == "友好+2" else 1.0
                score = 6.0 + 20.0 * (inv / mx) + val
                # ★実験モードの席分業：p2=投資係（能力＝開示・殺害・蘇生も情報源）。
                #   全席一律に足すと事件誘発（ウイルス脚本では死＝最良の情報源）を
                #   3席とも締め出す（+28で実測退行）＝1席だけ投資を最優先させる。
                if getattr(self, "_experiment", False) and view.get("seat") == "p2":
                    score += 30.0
                # ★混合戦略：友好禁止は1枚/ターンしか無いので、上位2ターゲットを日替わりで
                #   主役交代させ、ブロックを空振りさせる（進捗が読まれても集中先が読めない）。
                top2 = sorted(self._invest, key=self._invest.get, reverse=True)[:2]
                if len(top2) == 2 and tgt in top2:
                    primary = top2[(view["loop"] + view["day"]) % 2]
                    if tgt == primary:
                        score += 8.0
                return score
            return 1.0

        best = max(options, key=score)
        if best["card"] == "暗躍禁止":
            self._kinshi_used = True
        return best

    def _choose_goodwill(self, view: dict, options: list[dict]) -> dict:
        """使える友好能力から最も有益なものを選ぶ。

        ★情報収集プレイ：開示系の価値は情報量（Gini）で動的に決まる。開示宣言は
        「通れば役職判明・拒否されれば友好無視バレ」の両取りプローブなので、
        不確かなキャラへの開示は暗躍除去に匹敵する価値を持つ。
        """
        danger = self._guess_defeat_board(view)

        def gscore(o: dict) -> float:
            if o.get("action") == "pass":
                return 1.0
            user, ability, tgt = o["character"], o["ability"], o["target"]
            if "暗躍除去" in ability:  # 敗北条件ボード/暗躍持ちの暗躍を剥がす（最優先）
                if tgt in self._AREAS:
                    base = 100.0 + 10.0 * view["board_anyaku"].get(tgt, 0)
                    return base + (20.0 if tgt == danger else 0.0)
                c = self._alive(view, tgt)
                return 60.0 + 10.0 * (c["anyaku"] if c else 0)
            if "不安" in ability and "除去" in ability:
                c = self._alive(view, tgt)
                return 25.0 * self._risk(c) if c else 0.0
            return self._ability_value(user, ability, tgt, view)

        return max(options, key=gscore)
