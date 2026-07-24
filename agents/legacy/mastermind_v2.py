"""ヒューリスティック脚本家bot（M3→v2強化 2026-07-05）。

決定的スコアリングbot。ルールはハードコードせず、シミュレータが渡す合法手だけから選ぶ。

勝ち筋（view の rule_y / 配役 / 事件から自動判定）:
1. キーパーソン殺害：キーパーソンに暗躍≥2＋同エリアのキラーでターン終了殺害。
2. ボード暗躍≥2（学校/神社/ボードX/病院の事件）。
3. 主人公殺害（病院の事件・キラー暗躍4・メインラバーズ）。

v2の強化（対belief主人公・対人間）:
- ★暗躍禁止で止まらない経路を主軸に：クロマクもキーパーソンへ寄せ、脚本家能力フェイズの
  暗躍+1（行動解決外＝暗躍禁止無効）でキーパーソンを担ぐ。主人公の毎ターンの暗躍禁止を無力化。
- ★世界線を絞らせない（情報衛生）:
  * 賢い拒否：開示/犯人/ルール系や勝ち筋を壊す能力だけ拒否し、無害な能力は通す
    （拒否＝友好無視バレ＝配役情報。人間・将来のbelief拡張への対策）。
  * 無駄なミスリーダー撃ちをしない（不安+1能力の使用は「同エリアにミスリーダー」の位置情報）。
  * 不安のばらまき：手札の不安+1は犯人以外にも撒いて犯人特定を困難に（カードは役職を明かさない）。
- 友好禁止で脅威キャラ（暗躍除去・開示持ち）の友好を止め、友好能力の解禁を遅らせる。
- 殺人事件の対象：脅威キャラを優先で殺し、自分の未来の犯人は殺さない（事件を維持）。
"""

from __future__ import annotations

import random

from engine.board import AREAS
from engine.data import forbidden_of, goodwill_abilities_of, unrest_threshold_of
from engine.models import MOVE_CARDS

_ANRYAKU_VALUE = {"暗躍+1": 1, "暗躍+2": 2}
# 通ると脚本家が不利になる友好能力のキーワード（拒否候補）
_DANGEROUS_ABILITY_HINTS = ("開示", "犯人", "ルール", "戻す", "護衛", "不死",
                            "蘇生", "肩代わり", "移し替え")


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
        kuromaku = next((n for n, r in roles.items() if r == "クロマク"), None)
        cultist = next((n for n, r in roles.items() if r == "カルティスト"), None)
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
        culprits_all = {inc["culprit"] for inc in view["incidents"]}
        future_culprits = {inc["culprit"] for inc in view["incidents"] if inc["day"] > day}

        # 脅威キャラ＝友好能力で勝ち筋を崩す/情報を開示するキャラ（友好禁止・殺人事件の的）。
        # ★threat_hearts＝その脅威能力の必要友好数（最小）。友好禁止の要否判断に使う
        #   （残日数で解禁不能な相手＝ハート0の大物♡5等に無駄撃ちしないため）。
        threats: set[str] = set()
        threat_hearts: dict[str, int] = {}
        for n in chars:
            for ab in goodwill_abilities_of(n) or []:
                nm, hearts = ab["name"], ab["hearts"]
                is_threat = False
                if "開示" in nm or "犯人" in nm or "ルール" in nm:
                    is_threat = True
                elif "暗躍" in nm and "除去" in nm:
                    if n == "巫女":  # 巫女の除去は神社限定
                        is_threat = "神社" in goal_boards
                    else:
                        is_threat = bool(goal_boards or keyperson)
                if is_threat:
                    threats.add(n)
                    threat_hearts[n] = min(threat_hearts.get(n, 99), hearts)

        # ★キーパーソン防御の先読み：一度キーパーソンが死ぬと（死＝ループ終了効果で公開）、
        #   主人公は以後のループで毎ターン暗躍禁止を当ててくる。実際に2回塞がれた場合も同様。
        #   → どちらかを検知したら、カード暗躍はキラー自身の暗躍4（主人公殺害）経路へ切替。
        kp_kinshi = 0
        kp_died_before = False
        for e in view.get("history", []):
            if e.get("event") == "cards_revealed":
                for p in e.get("placements", []):
                    if (p.get("card") == "暗躍禁止" and p.get("owner") != "mastermind"
                            and p.get("target") == keyperson):
                        kp_kinshi += 1
            elif e.get("event") == "death" and e.get("name") == keyperson:
                kp_died_before = True
        kp_blocked = kp_died_before or kp_kinshi >= 2
        # キラー経路が使えない（キラー不在/死亡）なら切替しない＝キーパーソン経路を維持
        if not killer or not chars.get(killer, {}).get("alive", False):
            kp_blocked = False

        # ★勝ち確定＝抑制モード（手練れの知見）：敗北ボードが既に2以上なら勝ちはほぼ固い。
        #   以後は事件の発生を抑えて情報流出（犯人・臨界の手がかり）を防ぐ。
        locked = any(view.get("board_anyaku", {}).get(b, 0) >= 2 for b in goal_boards)

        return {"chars": chars, "keyperson": keyperson, "killer": killer,
                "kuromaku": kuromaku, "cultist": cultist, "friends": friends,
                "goal_boards": goal_boards,
                "today_culprit": today_culprit, "culprits_all": culprits_all,
                "future_culprits": future_culprits, "threats": threats,
                "threat_hearts": threat_hearts,
                "days_left": max(1, view.get("days_per_loop", view["day"]) - view["day"] + 1),
                "kp_blocked": kp_blocked, "locked": locked}

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
            return self._decide_refuse(view, options)
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

    # -- 友好能力の拒否（賢い拒否＝危険な能力だけ拒否して友好無視を隠す） ----

    def _decide_refuse(self, view: dict, options: list[dict]) -> dict:
        a = self._analyze(view)
        ctx = options[0]
        ability = ctx.get("ability") or ""
        target = ctx.get("target")
        dangerous = any(k in ability for k in _DANGEROUS_ABILITY_HINTS)
        if "暗躍" in ability and "除去" in ability:
            # 勝ち筋の暗躍（ゴールボード/キーパーソン/キラー）を剥がされるなら拒否
            dangerous = dangerous or target in a["goal_boards"] \
                or target == a["keyperson"] or (a["killer"] and target == a["killer"])
        if "不安" in ability and ("除去" in ability or "操作" in ability):
            # 犯人の不安（事件の燃料）を消されるなら拒否
            dangerous = dangerous or target in a["culprits_all"]
        # ★bool正規化：`x and y` はキラー不在時に None を返しうる（is比較がStopIterationになる）
        dangerous = bool(dangerous)
        return next(o for o in options if o.get("refuse") is dangerous)

    # -- 行動カードのセット --------------------------------------------------

    def _score_set(self, o: dict, a: dict, view: dict) -> float:
        card, tgt, kind = o["card"], o["target"], o["target_kind"]
        val = _ANRYAKU_VALUE.get(card, 0)
        chars = a["chars"]
        # (1) ゴールボードに暗躍を積む（学校/ボードX/病院）
        if kind == "board" and val and tgt in a["goal_boards"]:
            return 100 + val * 10
        # (2) キーパーソンに暗躍（キラー殺害の前提＝暗躍≥2）。
        #     ★塞がれ検知後はピン留め価値だけ残して優先度を下げ、(2b)のキラー自己暗躍へ切替。
        if kind == "character" and val and tgt == a["keyperson"]:
            if chars[tgt]["anyaku"] >= 2:
                return 30
            return 25 if a["kp_blocked"] else 90 + val * 10
        # (2b) キラー自身に暗躍4（主人公殺害）。キーパーソンが暗躍禁止で守られている時**だけ**
        #     本命に切替（普段は低スコア＝プラン混線でキラー殺害経路を壊さない）。
        if kind == "character" and val and tgt == a["killer"] and chars[tgt]["alive"]:
            if chars[tgt]["anyaku"] >= 4:
                return 15
            return (95 + val * 10) if a["kp_blocked"] else 10
        # (3) その日の事件犯人の不安を臨界へ（事件を発生させる）。
        #     ★抑制モード（勝ち確定時）：事件は情報流出＝発生させない・不安-1で犯人を冷やす。
        if card == "不安-1" and kind == "character" and a["locked"]:
            c = chars.get(tgt)
            th = unrest_threshold_of(tgt)
            if (c and c["alive"] and tgt in a["culprits_all"]
                    and th is not None and c["unrest"] >= max(1, th - 1)):
                return 65  # 臨界間際の犯人を冷やして事件を不発に
        if card == "不安+1" and kind == "character":
            if a["locked"]:
                return 2    # 勝ち確定時は不安を増やさない（事件＝情報流出）
            c = chars.get(tgt)
            th = unrest_threshold_of(tgt)
            if c and c["alive"]:
                if tgt == a["today_culprit"] and th is not None and c["unrest"] < th:
                    return 60
                if tgt in a["future_culprits"] and th is not None and c["unrest"] < th:
                    return 40   # 未来の犯人の仕込み
                return 18       # ★ばらまき：犯人以外にも不安を撒いて犯人特定を困難に
        # (4) キラー／クロマクをキーパーソンのエリアへ寄せる移動
        #     （クロマクの脚本家能力フェイズ暗躍+1は暗躍禁止で止められない＝本命経路）。
        #     行き先が禁止エリアのキャラは動かさない（無駄カード防止）。
        if card in MOVE_CARDS and kind == "character" and a["keyperson"]:
            for mover, base in ((a["killer"], 50), (a["kuromaku"], 48)):
                if tgt == mover:
                    kp, mv = chars.get(a["keyperson"]), chars.get(mover)
                    if kp and mv and kp["area"] and mv["area"] and kp["area"] != mv["area"]:
                        if kp["area"] in forbidden_of(mover):
                            return 0  # 到達不能（例: サラリーマンは学校禁止）
                        if _single_move_card(mv["area"], kp["area"]) == card:
                            return base
                    return 3
        # (4b) カルティストをゴールボードへ寄せる移動：カルティストが居るボードの暗躍禁止は
        #      無視できる（行動解決）＝ゴールボードへのカード暗躍が通るようになる。
        if card in MOVE_CARDS and kind == "character" and tgt == a["cultist"] \
                and a["goal_boards"]:
            cu = chars.get(a["cultist"])
            if cu and cu["area"]:
                for gb in a["goal_boards"]:
                    if cu["area"] != gb and gb not in forbidden_of(a["cultist"]) \
                            and _single_move_card(cu["area"], gb) == card:
                        return 47
            return 3
        # (5) 友好禁止：脅威キャラ（暗躍除去/開示持ち）の友好を止め、能力解禁を遅らせる。
        #   ★残日数で解禁不能な相手には置かない（無駄撃ち防止・ユーザー指摘 2026-07-06）。
        #   主人公が1ターンに1キャラへ現実的に盛れる友好は最大2（友好+2）とみなし、
        #   「現在友好＋2×残日数 ≥ 必要友好」でなければ意味がない。重い能力（♡4+）は
        #   ハートが乗り始める（現在友好≥1）まで待つ＝脚本家の手数を温存する。
        if card == "友好禁止" and kind == "character" and tgt in a["threats"]:
            c = chars.get(tgt)
            if not (c and c["alive"]):
                return 0
            hearts = a["threat_hearts"].get(tgt, 3)
            if c["goodwill"] + 2 * a["days_left"] < hearts:
                return 0            # 残り日数では解禁不能＝無駄
            if c["goodwill"] >= 1:
                return 35           # 友好が乗り始めた＝止める価値大
            if hearts <= 3:
                return 22           # 軽い能力は先回りでもよい
            return 0                # 重い能力でハート0＝溜まり始めるまで待つ
        return 0

    # -- 脚本家能力フェイズ ---------------------------------------------------

    def _score_ability(self, o: dict, a: dict) -> float:
        if o.get("action") == "pass":
            return 1  # 何も得が無ければパス（負スコアは作らない）
        if o["kind"] == "anyaku":
            if o["target_kind"] == "board" and o["target"] in a["goal_boards"]:
                return 100
            if o["target_kind"] == "character":
                if o["target"] == a["keyperson"] and not a["kp_blocked"]:
                    return 90   # ★暗躍禁止で止められないキーパーソン暗躍（本命）
                if o["target"] == a["killer"] and a["kp_blocked"]:
                    return 92   # ★切替後はキラー自身へ（暗躍4→主人公殺害を加速）
                if o["target"] == a["keyperson"]:
                    return 60   # 切替後もキーパーソン暗躍は無駄ではない（能力は止められない）
            return 20
        if o["kind"] == "unrest_minus":
            # 医者（脚本家使用）：抑制モードで臨界間際の犯人を冷やす（事件＝情報流出の防止）
            tgt = o["target"]
            c = a["chars"].get(tgt)
            th = unrest_threshold_of(tgt)
            if (a["locked"] and c and tgt in a["culprits_all"]
                    and th is not None and c["unrest"] >= max(1, th - 1)):
                return 70
            return 0.5
        if o["kind"] == "unrest":
            # ミスリーダー：犯人の不安を臨界へ運ぶときだけ使う。
            # ★無駄撃ちは「同エリアにミスリーダー」の位置情報＝世界線を絞らせるのでパスに劣後。
            # ★抑制モード（勝ち確定時）は事件を起こさない＝常にパス劣後。
            if a["locked"]:
                return 0.5
            tgt = o["target"]
            c = a["chars"].get(tgt)
            th = unrest_threshold_of(tgt)
            below = c and th is not None and c["unrest"] < th
            if tgt == a["today_culprit"] and below:
                return 80
            if tgt in a["future_culprits"] and below:
                return 35
            return 0.5
        return 0

    # -- ターン終了・事件・ループ開始 -----------------------------------------

    def _score_turn_end(self, o: dict) -> float:
        # 殺害系は全て勝ちに直結（主人公殺害/敗北＞キーパーソン殺害）。パスは避ける。
        if o.get("action") == "pass":
            return 0
        return 100 if o.get("target") in ("主人公", "主人公敗北") else 90

    def _score_incident(self, o: dict, a: dict) -> float:
        tgt = o.get("target")
        # 行方不明などボード選択：ゴールボードへ動かして暗躍を足す
        if tgt in AREAS:
            return 100 if tgt in a["goal_boards"] else 10
        # キーパーソン殺害（勝ち）＞フレンド殺害（ループ終了時敗北）＞脅威キャラ排除。
        # ★未来の犯人は脅威でも殺さない（自分の事件を消してしまう）＝回避を脅威より先に判定。
        if tgt == a["keyperson"]:
            return 100
        if tgt in a["friends"]:
            return 70
        if tgt in a["future_culprits"]:
            return 1   # 自分の未来の犯人は殺さない（事件を維持）
        if tgt in a["threats"]:
            return 45  # 巫女・神格・刑事など、主人公の対抗手段を先に消す
        return 5

    def _score_loop_area(self, o: dict, view: dict) -> float:
        # 手先の初期エリア。復讐者の灯火で手先＝クロマクならボードXになるので、
        # 病院の事件があれば病院（勝ち筋を集中）、無ければ学校を選ぶ。
        area = o.get("area")
        if view["rule_y"] == "復讐者の灯火":
            want = "病院" if any(i["name"] == "病院の事件" for i in view["incidents"]) else "学校"
            return 10 if area == want else 1
        return 1
