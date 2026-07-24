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
from agents.card_effect import NoopCtx, noop_reason
from engine.data import (LADDER_CHARS, ability_class_target_alive, forbidden_of,
                         goodwill_abilities_of, unrest_threshold_of)

from .belief import Belief
from .soft_evidence import MisleaderUnrestPresence

_MOVE_TOGGLE = {"移動←→": (1, 0), "移動↑↓": (0, 1)}  # 主人公の移動カード（斜めは無い）


# ★B-28：実質移動不可の判定は agents/card_effect.immobile_static に一本化した
#   （旧 _immobile_of＝同一実装をここに重複保持していた）。空振りゲートの述語は
#   card_effect.noop_reason が単一の真実＝defense_plan の break 生成も同じ物を見る。


def _move_dest(src: str | None, card: str) -> str | None:
    """移動カードで src からどこへ行くか（未登場/非移動カードは None）。"""
    t = _MOVE_TOGGLE.get(card)
    return destination(src, t) if (src and t) else None

_KEYPERSON_MIN = 0.5   # この確率以上ならキーパーソンとみなして守る
_KILLER_MIN = 0.4



# ---------------------------------------------------------------------------
# ★手筋の優先度表（単一ソース）：scoreの分岐が返す代表値。
#   関係（どの手筋がどの手筋より強いか）は tests/test_priorities.py の半順序テストが
#   固定する＝1点の調整が席割当を変えるため、変更時はテストの根拠コメントも更新すること。
# ---------------------------------------------------------------------------
PRIORITY: dict[str, float] = {
    "自滅回避": -100.0,            # 暗躍禁止2枚目（絶対禁止）
    "キラー暗躍3封じ": 110.0,      # 次の+1で主人公死
    "確信キラー反応封じ": 105.0,
    "VIP注入_実証済": 103.0,       # SK×KP同居の第三者注入（過去ループで死を観測）
    "クロマク隔離": 100.0,         # (c) ポンプ断ち
    "VIP注入": 99.0,
    "護衛ピン_実証済": 97.0,       # 3人維持の護衛役引き抜き阻止
    "ボード封じ_mm札": 96.0,       # mmが今置いたボードへ暗躍禁止
    "フェリーピン": 93.0,          # カルティスト確信への移動禁止
    "SK配達ピン_KP単独": 101.0,   # KP1人きり×KP暗躍<2＝今日死ぬ線はSK配達だけ（下記分岐）
    "TT仕上げ": 92.0,              # 最終日に+で友好3到達
    "カルティスト剥がし": 90.0,    # (c2)
    "幻想引き剥がし": 89.0,        # ボード経由（確信）
    "ボード封じ_危険": 88.0,       # 危険ボードへ暗躍禁止（暗躍0でも）
    "クロマク剥がし": 88.0,        # (c3)
    "キラー候補反応封じ": 84.0,    # +16×キラー確率+4×暗躍
    "TT投資+2": 82.0,
    "冷却役投資+2": 81.0,          # 危険事件の犯人を毎ターン冷やせる能力の解禁（自己ポンプ対抗）
    "冷却役投資+1": 78.5,
    "危険事件_再履修": 78.0,       # 前ループの死亡系敗北を起こした事件（実証＝同型敗北の再履修根絶・B-15）
    "危険事件_蝶": 80.0,           # ×p_future
    "TT投資+1": 79.0,
    "フェリーピン_コンボ": 82.5,   # 候補でもゴールボード同時札＝今日の着地阻止は明日の冷却役(81)より上
    "KP候補封じ": 78.0,            # +3×暗躍
    "VIP注入_候補": 76.0,
    "危険犯人_ML分離": 79.0,      # ボード供給事件の犯人をMLから引き離す（能力ポンプ断ち）
    # ★B-52＝kill事件（KP死）の犯人をMLから引き離す。冷却(≈92当日)＋分離の両輪が必須＝
    #   両方が席を取れる高さに置く（掃引で較正）。fs5_guard の誤配分先 移動禁止→KP(68)・
    #   板暗躍禁止(81) を上回る必要がある。
    "危険犯人_ML分離_kill": 70.0,
    "冷却役同行": 77.0,           # 解禁済み冷却役を危険犯人の同エリアへ（能力は同エリア限定）
    "SKテスト": 75.0,              # 探索テスト・役職テスト給餌
    "危険事件_邪気実証": 74.0,     # 神社敗北の実証あり
    "幻想引き剥がし_候補": 74.0,
    "SK配達ピン": 72.0,            # 最終ループ限定。当日冷却(74)より下
    "カルティスト剥がし_候補": 72.0,   # (c2b)
    "クロマク剥がし_候補": 71.0,   # (c3b)
    "フェリーピン_候補": 68.0,
    "危険事件_流布TT": 66.0,       # TTガード投資(65相当)より上
    "TTテスト_友好禁止歴": 66.0,
    "護衛ピン": 60.0,
    "SK配達ピン_弱": 58.0,
    # ★B-45（L1D1定石レイヤ）：+2配分の優先表。浄化係(62)/冷却役投資より**下**＝既存の
    #   高優先パスの席を奪わない。一般投資（6+20*inv/mx＝最大26級）より**上**＝定石が効く。
    "定石_不安除去能力者+2": 34.0,   # 定石3前半（医者・ナース・周囲に学生2人以上の学生）
    # ★定石3後半＝準備移動。ユーザー原則「臨界が迫らない限り不安-1は温存＝その席は移動に
    #   使う方が効率的」＝**投機的冷却（不安0×伏せ札への-1）より上**に置く（実測＝そのクラスの
    #   60%が床空振り／得点の主クラスタは30〜46）。実効が見込める冷却（56以上）には譲る。
    "定石_準備移動": 35.0,
    # ★定石6＝散開移動（4人以上の板から友好能力評価が最低のキャラを人数の少ない方へ）。
    #   母数13＝FableA裁定「効果が出なくても負の結果記録で可・深追いしない」。
    "定石_散開移動": 47.0,
    "TTテスト": 58.0,
    "ポンプ見切り": 30.0,          # 暗躍禁止がポンプに勝てない＝引き剥がしに枠を譲る
}

class HeuristicProtagonist:
    # ★計算スコア係数（連続量・2026-07-09抽出）：PRIORITY（順位アンカー・離散）と別。
    #   arena/tune_coeffs.py が --coeff COEFF.<key> でスイープできる連続係数の単一ソース。
    #   PRIORITYの一様CEMは汎化しなかったが、これら連続量はベンチ監督で過学習せず効く
    #   （実証：_VIP_RISK_ROOM_MAX 3→2 で gen4-5d +1）。抽出値は挙動保存（byte-identical）。
    COEFF: dict[str, float] = {
        # 事件危険度（_incident_danger の代入＝冷却/退避の照準を駆動）
        "danger_seal_slope": 65.0,        # 邪気の汚染：65×P(封印)
        "danger_murder": 68.0,            # 殺人事件（犯人と同エリアの1人を殺す）
        "danger_remote_kp_today": 70.0,   # 遠隔殺人×当日×KP在＝即敗北級
        "danger_remote_base": 55.0,       # 遠隔殺人/病院の事件（上記以外）
        "danger_board_supply_slope": 63.0,   # 行方不明/不安拡大：63×P(盤面ルール)+12
        "danger_board_supply_base": 12.0,
    }

    # ★防御プランナー加点（2026-07-09）：agents/defense_plan が「負け筋を最安で折る手」に
    #   選んだ options に一律加点する（既存の離散PRIORITYは早期returnで通らない＝加点は
    #   fall-through/中優先の手だけを押す＝主に位置防御(移動禁止でキラー固定/移動でKP引離)を
    #   底上げ）。0で無効。tune_coeffs で --coeff PLAN_BONUS を掃引できる連続係数。
    #   ★採用値24（2026-07-09・flip-aware実測）：位置防御のみ加点なら3日級+1（random_FS10
    #   がL5→L3）・5日級±0で200局退行ゼロ。40超で5日級が崩れる（崖）＝24は安全域。
    #   暗躍禁止/ボードにも加点する版は btx_contract 等の1手勝ちを崩した（=位置限定にした）。
    PLAN_BONUS: float = 24.0
    # ★ホット加点（2026-07-09・実ログ検死）：致命脅威（fatal）かつ実在度≥PLAN_HOT_P の
    #   折り手は冷却(75-82)より優先させる。SK=イレギュラーの2人きり仕込み（75%）に対し
    #   PLAN_BONUS=24 では『不安-1で犯人冷却』に席を全部食われ、正しい折り手
    #   （移動禁止でSKの移動打ち消し）を打てなかった実ログが根拠。KP緊急防御(100)よりは下。
    #   P=0.7（flip-aware実測）：0.55だと誤検出局面で冷却を奪い def→loss 2件。0.7で解消
    #   （3日級 FS10 のL3→L4遅延1件のみ・5日級+2改善）。実ログの仕込み（2枚接触=0.75）は守れる。
    PLAN_HOT: float = 88.0
    PLAN_HOT_P: float = 0.7

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
        self._plan_recs: dict = {}   # 防御プランナー推奨手 {(card,target,kind): 脅威度}

    def _defense_plan_recs(self, view: dict, options: list) -> dict:
        """防御プランナー（agents/defense_plan）が「負け筋を最安で折る手」に選んだ
        options の {(card,target,target_kind): 覆う致命脅威の最大実在度}。
        PLAN_BONUS=0 なら計算を省く。belief 未整備/例外時は空＝加点なし（advisory）。"""
        if not self.PLAN_BONUS or self._belief is None:
            return {}
        try:
            from .defense_plan import plan_for_belief
            _threats, plan = plan_for_belief(view, self._belief, options=options)
            # ★位置防御のみ加点：既存スコアは暗躍禁止/ボード系を精緻に扱えている（そこへ
            #   加点すると btx_contract 等の1手勝ちを崩す実測）。プランナーの純益は
            #   既存で弱い「移動禁止でキラー固定／移動でKP引き離し」の底上げに限る。
            # ★B-31（2026-07-17）：加点対象は **移動禁止 のみ**。
            #   この係数群（PLAN_BONUS=24／PLAN_HOT=88／PLAN_HOT_P=0.7）は、移動(退避)breakが
            #   カード名バグ（_MOVE_CARDS）で**一度も生成されなかった世界**で較正された＝実質
            #   「移動禁止だけが加点対象」。名前修正で退避breakが復活した今、同じ+88を移動にも
            #   与えると弱い折り手（robust=False＝追撃されうる）が暗躍禁止(100-110)を押しのけ
            #   防衛を落とす（実測：名前修正のみで 129→128／+G4 で 125・btx_future 3.6→5.9）。
            #   ＝**復活（プランナ表示・DP-1の被覆）と加点（AIの意思決定）を分離**し、加点は
            #   従来の実効範囲に保つ。移動への加点は独立の調整課題として別途掃引する。
            return {(b.card, b.target, b.target_kind):
                    plan.pick_heat.get((b.card, b.target, b.target_kind), 0.0)
                    for b in plan.picks
                    if b.card == "移動禁止"}
        except Exception:
            return {}

    # -- ターン境界で belief を更新し、推定をキャッシュ ----------------------

    #: belief に載せるソフト証拠のファクトリ列（各要素は () -> SoftEvidence）。
    #: ★稼働中（2026-07-14・設計提案 phase-2）：ミスリーダーの不安痕跡（present回数）を μ・λ0.4 で載せる。
    #:   MI検証で present が chance の1.8倍（0.574 vs 0.312）＝有意な公開信号／λ0.4は L4+ Brier が
    #:   悪化しない最大値（0.371→0.359・解像度0.544→0.557・照準100%・防衛127/62不変）。空にすれば現行と
    #:   bit-for-bit（μ≡1）＝A/B測定で差し替え可。カルティストは情報床＝ソフト弾を撃たない。
    SOFT_EVIDENCE: list = [lambda: MisleaderUnrestPresence(lam=0.4)]

    # -- ①tempo はタイブレーク限定（FI-5 v3骨子1・2026-07-19） -----------------------
    #: ★val を割らない（打ち切りは全スコープで退行＝§8-1）。同値〜近接スコアの投資先間の
    #  優先度だけを動かす微小項（tempo∈[0,1] × 係数）。掃引較正の初期値。
    _TEMPO_TIEBREAK = 0.5

    def _sync(self, view: dict) -> None:
        if self._belief is None:
            cast = [c["name"] for c in view["characters"]]
            self._belief = Belief(cast, view.get("incidents", []),
                                  set_name=view.get("set", "FS"))
            for _factory in self.SOFT_EVIDENCE:
                self._belief.register_soft_evidence(_factory())
        turn = (view["loop"], view["day"])
        if turn != self._turn:
            # このターンに自席が既に置いた移動（席間協調：ペア構築は2席がかりで行う）
            self._planned_moves: dict[str, str] = {}
            # ★席分業（明示的協調）：このターンに冷却済みの事件日。後席は同じ事件日の
            #   候補冷却を減点され、別の需要（TT投資・ボード防衛等）へ回る
            #   （seed0型の実測：2席が同系の冷却に吸われTTガードが痩せた）。
            self._cooled_days: set[int] = set()
            self._cooler_invested = False   # 冷却役投資はターン1席まで
            self._vip_injected = False      # VIP注入（第三者送り込み）もターン1席まで
            # ★2段化（2026-07-09）：ターン先頭席が3席分の手を一括計画する。
            #   貪欲な逐次maxは「4需要vs3席」の取捨（どの需要を今日捨てるか）を
            #   調停できない（5日級FS_6実測＝ピン/冷却/分離/暗躍禁止のナイフエッジ）。
            self._turn_plan: list | None = None
            # ループ境界で移動禁止の残数を追跡（席計3枚/ループ＝5日級では希少資源）
            if self._turn is None or turn[0] != (self._turn or (0,))[0]:
                pass
            self._pins_spent = getattr(self, "_pins_spent", 0)
            if not hasattr(self, "_pins_loop") or self._pins_loop != turn[0]:
                self._pins_loop = turn[0]
                self._pins_spent = 0
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
        # ★B-16：意図的事件発生の実験は「今日以降の未来イベント」の犯人候補にのみ意味がある。
        #   過去に発生済みの事件（今ループの過ぎた日）や、その事件から公開確定で除外された候補に
        #   不安+1を注いでも情報は出ない（例：D3犯人でないと確定した男子学生へL4D2に不安+1＝無意味）。
        _dnow = view.get("day", 1)
        self._future_culprits = set().union(
            *(s for d, s in cand.items() if d >= _dnow)) if cand else set()
        # 犯人が1人に確定している日（開示・消去法）→ 事件対策（不安-1・退避）の照準になる
        self._known_culprits = {d: next(iter(s)) for d, s in cand.items() if len(s) == 1}
        self._culprit_by_day = dict(cand)   # 日→犯人候補集合（敗北トリガー犯人の実験除外用）
        # ★危険事件（ルール接地）：事件の効果が敗北条件に直結する日＝候補犯人でも予防冷却する。
        #   蝶の羽ばたき＝未来改変プランなら発生即敗北条件成立／邪気の汚染＝神社+2（封印されしモノ）。
        rules_m = self._belief.rule_marginals()
        p_future = sum(p for (ry, _x), p in rules_m.items() if ry == "未来改変プラン")
        p_seal = sum(p for (ry, _x), p in rules_m.items() if ry == "封印されしモノ")
        self._incident_danger: dict[int, float] = {}
        # ★邪気の汚染が神社を+2する脚本（封印×邪気）＝神社への暗躍は事件で供給され暗躍禁止で
        #   止まらない。この時、mm札の無いターンの神社ボード暗躍禁止は空振り＝犯人冷却に席を譲る。
        self._shrine_seal_incident = False
        self._lethal_days: set[int] = set()   # 人が死ぬ事件の日（KP退避・実験ポンプ禁止）
        # 神社≥2の敗北ループを観測済みか（封印の実証＝邪気の汚染の危険を引き上げる材料。
        # _observed_defeat_board はこの後で計算されるため、ここでは軽い直接スキャン）
        _defeat_lps0 = {e.get("loop") for e in view.get("history", [])
                        if e.get("event") == "loop_result"
                        and "敗北" in str(e.get("result", ""))}
        _shrine_defeat_seen = any(
            e.get("event") == "loop_board" and e.get("loop") in _defeat_lps0
            and e.get("board_anyaku", {}).get("神社", 0) >= 2
            for e in view.get("history", []))
        # ★蝶ヘッジ（テスター検死 2026-07-09）：敗北ループで蝶の羽ばたきが発生していた＝
        #   「未来改変プランで負けた」仮説が実証圏。mmはボード暗躍を同時に育てて敗因を
        #   曖昧化できる（p_future が 1/3 程度に薄まり冷却が席を取れない実測＝8/8全敗）が、
        #   ボード供給は止められない一方で蝶は犯人冷却1枚で確実に折れる＝ヘッジは常に安い。
        _butterfly_lost = any(
            e.get("event") == "incident" and e.get("name") == "蝶の羽ばたき"
            and e.get("occurs") and e.get("loop") in _defeat_lps0
            for e in view.get("history", []))
        # ★B-15（2026-07-14c）：前ループの死亡系敗北を起こした致死事件（公開情報）を「実証済みの
        #   再履修敗因」として危険度ブースト＝毎ループ同じ事件で死ぬ（同型敗北の再履修）を根絶する。
        #   蝶と同型の loop-loss 学習を死亡系（病院の事件/遠隔殺人/殺人事件等）に広げる。判定＝
        #   敗北ループで occurs かつ同日に死亡/主人公死亡が起きた事件（＝敗因と強く結びつく）。
        _death_days0 = {(e.get("loop"), e.get("day")) for e in view.get("history", [])
                        if e.get("event") in ("death", "protagonist_death")}
        # ★蝶で説明のつく敗北ループ（未来改変プラン）は病院/殺人事件に帰責しない（coincidental
        #   な事件の誤ブースト＝btx_future で蝶より病院の事件を優先し方式劣化した実測 2026-07-14c）。
        _butterfly_lps0 = {e.get("loop") for e in view.get("history", [])
                           if e.get("event") == "incident"
                           and e.get("name") == "蝶の羽ばたき" and e.get("occurs")}
        self._recurring_loss_incidents = {
            e.get("name") for e in view.get("history", [])
            if e.get("event") == "incident" and e.get("occurs")
            and e.get("loop") in _defeat_lps0
            and (e.get("loop"), e.get("day")) in _death_days0
            and not (p_future > 0.2 and e.get("loop") in _butterfly_lps0)}
        for inc in view.get("incidents", []):
            nm, d = inc.get("name"), inc.get("day")
            if nm == "蝶の羽ばたき" and (p_future > 0.2 or _butterfly_lost):
                self._incident_danger[d] = (PRIORITY["危険事件_蝶"] if _butterfly_lost
                                            else PRIORITY["危険事件_蝶"] * p_future)
            elif nm == "邪気の汚染" and (p_seal > 0.2 or _shrine_defeat_seen):
                # 邪気の汚染＝神社+2：封印なら事件1発で敗北条件成立。神社≥2の敗北を
                # 実際に見ているなら確率が薄くても最優先で冷やす（実験系71より上）
                self._incident_danger[d] = max(
                    self.COEFF["danger_seal_slope"] * p_seal,
                    PRIORITY["危険事件_邪気実証"] if _shrine_defeat_seen else 0.0)
                self._shrine_seal_incident = True   # 神社供給は事件由来＝ボード暗躍禁止では止まらない
            elif nm == "殺人事件":
                # 犯人と同エリアの1人を殺す＝キーパーソン（居れば）や味方の直接脅威
                self._lethal_days.add(d)
                self._incident_danger[d] = max(self._incident_danger.get(d, 0.0),
                                               self.COEFF["danger_murder"])
            elif nm in ("遠隔殺人", "病院の事件"):
                self._lethal_days.add(d)
                # ★遠隔殺人はKPが居る限り即敗北級（暗躍+2一枚でKPが射程2に入り、
                #   位置防御が効かない）＝55だと照準計算で他の事件・囮に負ける
                #   （vs gen2実測：真犯人の前日冷却が33点＝囮の76.5に完敗）。
                _alive0 = {o["name"] for o in view["characters"] if o["alive"]}
                _marg0 = self._belief.role_marginals()
                _kp_alive0 = any(_marg0.get(n0, {}).get("キーパーソン", 0) > 0.05
                                 for n0 in _alive0)
                # 近接（今日/明日）だけ70：遠い遠隔殺人まで70にすると5日級で
                #   序盤の席が冷却に吸われ盤面レースが崩れる（vs gen2実測 60→51）
                _lethal0 = (self.COEFF["danger_remote_kp_today"]
                            if (nm == "遠隔殺人" and _kp_alive0
                                and d == view["day"])
                            else self.COEFF["danger_remote_base"])
                self._incident_danger[d] = max(self._incident_danger.get(d, 0.0), _lethal0)
            elif nm == "行方不明":
                # ★ボード供給事件：発生するとmmが選んだボードに暗躍が乗る（行方不明=
                # 移動+ボード暗躍1・40:153）。盤面敗北ルールが生きているなら噂と合わせて
                # 「止まらない2点」を作る燃料＝犯人を冷やして止める
                # （5日級FS17実測：行方不明(学校+1)+噂で毎ループ守るべき場所が成立）。
                # ★B-36：不安拡大はここから除外＝その暗躍+1は**ボードでなくキャラに付く**
                # （40:149・sim/effects.py確認済）＝盤面敗北条件を直接育てない（誤分類是正）。
                _p_board0 = sum(pv for (ry0, _x0), pv in rules_m.items()
                                if ry0 in ("守るべき場所", "封印されしモノ",
                                           "復讐者の灯火", "巨大時限爆弾Xの存在"))
                if _p_board0 > 0.3:
                    self._incident_danger[d] = max(
                        self._incident_danger.get(d, 0.0),
                        self.COEFF["danger_board_supply_slope"] * _p_board0
                        + self.COEFF["danger_board_supply_base"])
            elif nm == "流布" and p_future > 0.5:
                # ★TT脚本の流布＝TTガードの天敵：友好-2でガード投資を剥がし、最終日の
                #   TT任意敗北（友好≤2）を成立させる（AI対AI実測：毎ループこれで負けた）。
                #   犯人を冷やして発生自体を止めれば、友好3の防壁が最終日まで残る。
                # 66＝TTガード投資(65)より上：ガード候補と犯人が同一人物のとき、同じ席は
                # 投資より冷却を選ぶ（主人公は同一対象に重ねられない＝両立は別席の仕事）
                self._incident_danger[d] = max(self._incident_danger.get(d, 0.0), PRIORITY["危険事件_流布TT"])
            # ★B-15：この事件が前ループの死亡系敗因なら危険度をブースト（同型敗北の再履修根絶）。
            if nm in self._recurring_loss_incidents:
                self._incident_danger[d] = max(self._incident_danger.get(d, 0.0),
                                               PRIORITY["危険事件_再履修"])
        # 殺害系事件の犯人候補＝実験ポンプ禁止（発生させると味方が死ぬ。負け確ループは除く）
        self._lethal_culprits = set().union(
            *(cand.get(d, set()) for d in self._lethal_days)) if self._lethal_days else set()
        # ★タイムトラベラーの任意敗北（最終日・友好≤2）封じ：TT疑いの友好を3以上に保てば
        #   ルール上宣言できない。TTへの友好+は友好禁止を無視する（KB: 50）＝止められない対策。
        #   キャラ特定の確信が低くても、ルール側でTT必在がほぼ確定なら最有力候補に張る
        #   （張らなければ毎ループ最終日に確実に負ける＝外れても期待値で勝る）。
        # ★分散ガード（AI対AI実測の教訓）：単一候補ガードは (1)候補が5人前後で絞れず
        #   高確率で外れる (2)脚本家が流布(-2)/友好禁止で剥がして実TTを友好0に保つ、の
        #   二重で機能しなかった。→ 上位2候補に張る＋既に友好3の候補は次点へ回す。
        #   主人公は同一対象に重ねられない＝1ターン最大+2 → 最終日だけでは0→3に届かない
        #   ＝事前投資が必須（最終日は仕上げの+2）。
        p_tt_rule = p_future  # TTを足すルールは未来改変プラン（Y）のみ
        tt_name, tt_p = self._belief.most_likely_role("タイムトラベラー")
        self._tt_guard = None
        if tt_name and (tt_p >= 0.5 or (tt_p >= 0.15 and p_tt_rule >= 0.7)):
            self._tt_guard = tt_name
        marg_tt = self._belief.role_marginals()
        # ★相手モデル：脚本家が友好禁止を当ててきた相手（全ループ累積）＝友好を積まれると
        #   困るキャラ＝TT/フレンド系の疑いが濃い。フラットな周辺確率のタイブレークに使う。
        #   さらにTTへの友好+は友好禁止を無視する（KB: 50）＝禁止された相手に友好+を
        #   ぶつければ「通る＝TT確定／止まる＝TT除外」の公開テストになる（belief側で消費）。
        # ※符号（+0.35）の是非＝wip/tt-suspect-sign-flip 検証記録（2026-07-14）：KB:50 的には
        #   「合理的脚本家はTTに友好禁止を置かない→符号-」が対人間で正しいが、-0.35 に反転すると
        #   AI脚本家相手で3日級 defense 125→123 退行（btx_future/random_BTX）＝AI mmの友好禁止配置が
        #   人間合理モデルと相関しない。ハード反転は保留し、beliefソフト重み層の opponent-prior
        #   （λ較正）として載せるのが正着（設計提案 P1・phase2/(a)(c)）。
        gwban_hist: set = set()
        for e in view.get("history", []):
            if e.get("event") == "cards_revealed":
                for p in e.get("placements", []):
                    if p.get("owner") == "mastermind" and p.get("card") == "友好禁止":
                        gwban_hist.add(p.get("target"))
        self._tt_guards: list[str] = []
        if p_tt_rule >= 0.7 or tt_p >= 0.5:
            ranked = sorted(((d.get("タイムトラベラー", 0.0)
                              + (0.35 if n in gwban_hist else 0.0), n)
                             for n, d in marg_tt.items()
                             if d.get("タイムトラベラー", 0.0) >= 0.10), reverse=True)
            # 上位3候補まで張る（宣言のたびに友好≥3の候補が消去される＝ループを跨いで
            # 候補プールが縮む。3人カバーはハーツ供給的に成立：+2×3席＋仕上げ+1）
            self._tt_guards = [n for _p, n in ranked[:3]]
        self._gwban_hist = gwban_hist
        # キラーでありうるキャラ（周辺確率>0）＝暗躍が積まれたら監視対象（ルール：暗躍4で主人公死亡）
        marg = self._belief.role_marginals()
        self._killer_suspects = {n for n, d in marg.items() if d.get("キラー", 0) > 0}
        # ★役職の周辺確率（脚本家が札を伏せた複数候補のうち"最も可能性の高い"対象を優先するため）。
        self._killer_prob = {n: d.get("キラー", 0.0) for n, d in marg.items()}
        self._kp_prob = {n: d.get("キーパーソン", 0.0) for n, d in marg.items()}
        # 確信度の高い推定（先回り防御用。完全情報ならp=1で初手から発火する）
        self._killer_strong = {n for n, d in marg.items() if d.get("キラー", 0) >= 0.9}
        self._kuromaku_suspects = {n for n, d in marg.items() if d.get("クロマク", 0) >= 0.7}
        # 弱い候補（引き剥がし実験用＝剥がして能力の出所を見れば確定する）
        self._kuromaku_cands = {n for n, d in marg.items() if d.get("クロマク", 0) >= 0.3}
        # シリアルキラー疑い（移動先の安全判定用＝2人きりに送らない）
        self._sk_suspects = {n for n, d in marg.items() if d.get("シリアルキラー", 0) >= 0.5}
        self._sk_strong = {n for n, d in marg.items() if d.get("シリアルキラー", 0) >= 0.9}
        # カルティスト疑い（ゴールボードの暗躍禁止を無効化される＝引き剥がし対象）
        self._cultist_suspects = {n for n, d in marg.items() if d.get("カルティスト", 0) >= 0.7}
        # 弱い候補（すり抜け観測の絞り込み用＝剥がして次のすり抜けを見れば候補が割れる）
        self._cultist_cands = {n for n, d in marg.items()
                               if d.get("カルティスト", 0) >= 0.25}
        self._kp_suspects = {n for n, d in marg.items() if d.get("キーパーソン", 0) >= 0.3}
        self._sk_cands = {n for n, d in marg.items()
                          if d.get("シリアルキラー", 0) >= 0.15}
        # ★フレンド護衛（SKに殺されるとループ終了時に敗北＝KPと同格の護衛対象）。
        #   ★公開済み（役職開示・死亡公開でp≈1）のフレンドだけ：疑い0.5での護衛は
        #   位置資源を誤配して他の防衛を崩した（seed12実測）。候補ベースの保護は
        #   犠牲者保護・SK配達ピンが受け持つ。
        # ★公開済みフレンド（p≥0.95＝死亡公開/開示済み）だけ護衛（2026-07-09 再有効化）：
        #   フレンド死＝ループ終了時敗北＝KPと同格のVIP。候補(0.5)護衛は誤配で退行した
        #   実測があるため公開済み限定（BTX_16実測：アイドル=フレンドがL1死亡で公開された
        #   のにL2-L8も毎ループSKに殺され続けた＝知っているのに守らない状態だった）。
        self._friend_guards: set = {n for n, d in marg.items()
                                    if d.get("フレンド", 0) >= 0.95}
        # ★経験的VIP（同じ負け筋回避）：過去ループでその死が直後にループ終了効果を
        #   引いたキャラ＝脚本家の勝ち筋の標的。役職確率に依らず守る（keypersonが
        #   0.5未満でコミットされない穴を「前に殺された事実」で埋める）。
        self._fatal_guards: set = set()
        _fh = view.get("history", [])
        for _i, _e in enumerate(_fh):
            if _e.get("event") == "loop_end" \
                    and "ループ終了効果" in str(_e.get("reason", "")):
                for _j in range(_i - 1, -1, -1):
                    _d = _fh[_j]
                    if (_d.get("loop"), _d.get("day")) != (_e.get("loop"), _e.get("day")):
                        break
                    if _d.get("event") == "death":
                        self._fatal_guards.add(_d["name"])
                        break
        # ★キーパーソンの暗躍禁止の価値はルール接地で決まる：キラーが居るか「僕と契約」
        #   （KP暗躍≥2で敗北）の可能性がある時だけ意味がある。どちらも消えたら守る価値なし
        #   （guard型でキーパーソンに暗躍禁止を貼り続けてゴールボードを素通しした実測の教訓）。
        p_keiyaku = sum(p for (ry, _rxs), p in self._belief.rule_marginals().items()
                        if ry == "僕と契約しようよ！")
        # ★遠隔殺人が残っている間もKP暗躍は命取り（暗躍≥2の任意1人を殺害＝mmは
        #   暗躍+2一枚でKPを射程に入れ、事件日に射殺する）。vs CEM gen2実測：
        #   FS×守るべき場所の3日級4敗すべてがこのライン＝キラー不在で kp_guard が
        #   5.0に落ち、KPへの暗躍ポンプが素通りだった。
        _remote_pending = any(i.get("name") == "遠隔殺人"
                              and i.get("day", 0) >= view["day"]
                              for i in view.get("incidents", []))
        self._kp_guard = 100.0 if (self._killer_suspects or p_keiyaku > 0
                                   or _remote_pending) else 5.0
        # ★情報収集プレイ：役職開示の情報価値＝Gini不純度（開示後の期待残存世界がW·Σp²）
        self._gini = {n: self._belief.role_gini(n) for n in self._belief.cast}
        self._top_rule_p = self._belief.top_rule_prob()
        self._culprit_sizes = {d: len(s) for d, s in cand.items()} if cand else {}
        # ★負けループ判定（手練れの知見）：敗北ボードが既に2以上＝このループはほぼ負け
        #   → 勝ちに行くのをやめ、情報収穫モード（殺害で役職確認・事件を意図的に発生）へ。
        # ★実証済みの敗北条件ボード：過去の敗北ループ終了時に暗躍≥2だったボード。
        #   ルールが未確定でも「脚本家がどこで勝ったか」は観測できる＝反応型の危険特定
        #   （guard型：ルール未特定→黒猫の神社+1にフォールバックが釣られた実測の教訓）。
        from collections import Counter as _Counter
        defeat_lps = {e.get("loop") for e in view.get("history", [])
                      if e.get("event") == "loop_result"
                      and "敗北" in str(e.get("result", ""))}
        # ★病院はルールYのゴールボードにならない（脅威は病院の事件のみ）：事件が実際に
        #   発生したループ以外では病院≥2は敗因ではない＝誤カウントすると翌ループの
        #   暗躍禁止が全部病院に吸われる（random_BTX seed1：真の敗因＝封印(神社)の実測）。
        hosp_fired_lps = {e.get("loop") for e in view.get("history", [])
                          if e.get("event") == "incident"
                          and e.get("name") == "病院の事件" and e.get("occurs")}
        # ★蝶の羽ばたきが発生したループの敗北は「未来改変プラン（=犯人冷却で防げる）」で
        #   説明がつく＝そのループのボード暗躍は敗因ではない（デコイ）。未来改変が濃厚な間は
        #   蝶発生ループを敗北ボードの帰属から除外する（テスター検死 2026-07-10：mmが
        #   暗躍+2→学校を囮に置き、AIが学校を敗北ボードと誤認→_loop_lostで防衛放棄した）。
        butterfly_lps = {e.get("loop") for e in view.get("history", [])
                         if e.get("event") == "incident"
                         and e.get("name") == "蝶の羽ばたき" and e.get("occurs")}
        db_cnt: dict = _Counter()
        for e in view.get("history", []):
            if e.get("event") == "loop_board" and e.get("loop") in defeat_lps:
                if p_future > 0.2 and e.get("loop") in butterfly_lps:
                    continue   # 蝶で説明のつく敗北＝ボードは無罪（デコイ）
                for b, v in e.get("board_anyaku", {}).items():
                    if v >= 2 and (b != "病院" or e.get("loop") in hosp_fired_lps):
                        db_cnt[b] += 1
        self._observed_defeat_board = db_cnt.most_common(1)[0][0] if db_cnt else None
        db = self._guess_defeat_board(view)
        self._loop_lost = bool(db and view.get("board_anyaku", {}).get(db, 0) >= 2)
        # ★蝶の羽ばたきが今ループ発生済み＝未来改変プランならループ終了時に敗北確定。
        #   盤面暗躍だけ見ていると「まだ勝てる」と誤認し、最後の戦いの弾込め（SKテスト等）を
        #   犠牲者保護が止めてしまう（btx_future実測：全敗→最後の戦い勝ちの筋が痩せた）。
        if p_future > 0.3 and any(
                e.get("event") == "incident" and e.get("name") == "蝶の羽ばたき"
                and e.get("occurs") and e.get("loop") == view["loop"]
                for e in view.get("history", [])):
            self._loop_lost = True
        # ★KP陥落の常態化＝推定KPが過去の全ループで死んでいる（防げた試しがない）なら、
        #   最終ループも高確率で落ちる＝最後の戦いの弾込め（テスト）を優先してよい、の
        #   プロキシ（seed14実測：遠隔殺人でKPが毎ループ死亡→FB勝負なのにテストが
        #   犠牲者保護で止まりFB敗北）。挙動への影響は犠牲者保護の緩和に限定する。
        _past_loops = set(range(1, view["loop"]))
        _kp_death_loops = {e.get("loop") for e in view.get("history", [])
                           if e.get("event") == "death"
                           and e.get("name") == self._keyperson}
        self._kp_doomed = bool(_past_loops) and _past_loops <= _kp_death_loops
        # ★実験モード（手練れの知見）：既にループを落としていて、かつルールXYが未確定なら、
        #   勝ちに行くより情報を取りに行く（負け筋の中では死も事件もタダの情報源）。
        lost_loops = sum(1 for e in view.get("history", [])
                         if e.get("event") == "loop_result"
                         and "敗北" in str(e.get("result", "")))
        self._lost_loops = lost_loops
        # ★FB準備モード（BTX）：全ループ敗北なら最後の戦い（全役職宣言）で決まる＝
        #   ルールが確定していても役職が不確かなら実験（開示・死・ペアテスト）を続ける。
        mean_gini = (sum(self._gini.values()) / len(self._gini)) if self._gini else 0.0
        # ★FB弾込めは「最後の戦いが現実的な射程」の時だけ（残り2ループ以内）。
        #   ループ数が長い形式（loops=8等）で無条件に続けると、ルール確定後も
        #   防衛より実験を選び続けて勝てるループを7回捨てる（loops=8実測：
        #   btx_seal/btx_futureがL1-L7全敗→L8防衛勝ちの先延ばし）。loops=3では
        #   lost_loops≥1の時点で常に残り2以内＝従来と同一挙動。
        fb_prep = (view.get("set") == "BTX" and mean_gini > 0.08
                   and view["loop"] >= view.get("loops_total", 3) - 1)
        self._experiment = lost_loops >= 1 and (self._top_rule_p < 0.999 or fb_prep)
        # SK炙り出しテストの価値：SKを足すルールXの在/不在がまだ割れているか
        #（潜む殺人鬼/切り裂き魔の影＝SK追加。ウイルスは役職でなく状態なので対象外）
        p_sk = sum(p for (_ry, rxs), p in self._belief.rule_marginals().items()
                   if "潜む殺人鬼" in rxs or "切り裂き魔の影" in rxs)
        sk_strong_now = {n for n, d in marg.items()
                         if d.get("シリアルキラー", 0) >= 0.9}
        # SKの在/不在が割れている or 「居るのは確実だが誰かが不明」＝どちらもテスト価値あり
        # （後者＝死ねばSK特定＋ループ終了時のフレンド公開/否定形も収穫＝最後の戦いの弾）
        self._sk_uncertain = (0.02 < p_sk < 0.98
                              or (p_sk >= 0.98 and not sk_strong_now))
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
        # ★脚本家のキラー路線の既観測（ループを跨ぐ反応材料。カウンターは毎ループ
        #   リセットされるので「今の暗躍」はD1の先制判断に使えない＝過去ループから読む）：
        #   過去の主人公死亡（キラー暗躍4/メインラバーズ）or キラー疑いへの暗躍カード。
        self._killer_plan_seen = False
        for e in view.get("history", []):
            if (e.get("event") == "loop_end"
                    and "主人公の死亡" in str(e.get("reason", ""))):
                self._killer_plan_seen = True
            elif e.get("event") == "cards_revealed":
                for p in e.get("placements", []):
                    if (p.get("owner") == "mastermind"
                            and str(p.get("card", "")).startswith("暗躍+")
                            and p.get("target") in self._killer_strong):
                        self._killer_plan_seen = True
        # このループで脚本家が友好禁止を当てた相手（公開）＝友好投資を分散して回避
        self._gw_blocked = set()
        # このループで脚本家能力フェイズに暗躍を置かれたキャラ＝クロマクが同エリアに居る証拠
        # （能力の対象条件がルールで「同一エリア」だから）。位置戦術のトリガー。
        self._mm_pump_victims: set[str] = set()
        # mm能力フェイズでボードに暗躍が置かれた観測（全ループ累積）＝クロマクの汲み上げ or
        # 不穏な噂。どちらも暗躍禁止では止まらない供給（60 A16/Q3）＝暗躍禁止の実効を割り引く材料。
        self._mm_pump_boards: set[str] = set()
        _AREAS = ("病院", "神社", "都市", "学校")
        for e in view.get("history", []):
            if (e.get("event") == "anyaku" and e.get("phase") == "mastermind_ability"
                    and e.get("delta", 0) > 0 and e.get("target") in _AREAS):
                self._mm_pump_boards.add(e.get("target"))
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
        if "カウンター除去" in ability and user in getattr(self, "_tt_guards", ()):
            # ★TTガードの自壊禁止：ガード候補の友好を消すとTT任意敗北（友好≤2）が
            #   再成立する（実測：最終日に学者が全カウンター除去で友好3→0＝ガード瓦解）
            cu = self._alive(view, user)
            if cu and cu["goodwill"] >= 1:
                return 0.5
        if "役職開示" in ability:
            # 開示宣言は両取りのプローブ：通れば役職判明、拒否されれば友好無視バレ。
            g = self._gini.get(target, 0.0) if target is not None \
                else max([self._gini.get(n, 0.0) for n in self._gini] or [0.0])
            val = 2.0 if g < 0.02 else 30.0 + 90.0 * g
            # ★FI-5 step2a：発動場所条件（大物=テリトリー内に対象が居るか）を A(2) reach で割引。
            #   非大物（自身開示・同エリア開示等）は _location_reach=1.0＝挙動不変。
            return val * self._location_reach(user, ability, view)
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
                if danger != "神社":
                    return 10.0
                # ★B-13→FI-5 step1：巫女は神社に居てこそ神社の暗躍を除去できる（発動場所条件）を
                #   base × _location_reach（A(2)）に因数分解。神社在=80×1.0=80／移動可=80×0.5=40／
                #   神社禁止=80×0.125=10（既存値を厳密再現＝挙動同値の起点・巫女は神社非禁止で実発生せず）。
                return 80.0 * self._location_reach(user, ability, view)
            return 60.0 if danger or self._keyperson else 20.0
        if "戻す" in ability:
            # ★手札戦術（委員長）：使用済み1/Lカードの回収。価値はカードの強さで決まる。
            #   友好+2＝投資エンジン ＞ 移動禁止＝キラー足止め ＞ 不安-1＝事件抑止。
            if target is None:  # 投資評価：リーダーの使用済み1Lがあれば価値あり
                return 25.0
            return {"友好+2": 35.0, "移動禁止": 22.0, "不安-1": 18.0}.get(target, 10.0)
        if "不安" in ability and "除去" in ability:
            # ★ルール接地（2026-07-08）：敗北条件に直結する危険事件（蝶の羽ばたき・
            #   邪気の汚染・流布TT等＝_incident_danger）の犯人候補をこの能力で冷やせる
            #   なら、毎ターンの追加冷却＝自己ポンプ犯人（ML本人等）への唯一の対抗。
            #   （btx_future実測：学生冷却に投資せず蝶が毎ループ発生した）
            if getattr(self, "_incident_danger", None):
                from engine.data import is_student
                for d, danger in self._incident_danger.items():
                    for cn in getattr(self, "_culprit_cands", {}).get(d, ()):
                        if target is not None and cn != target:
                            continue   # 使用評価（対象指定）はその相手が犯人の時だけ
                        cc = self._alive(view, cn)
                        if not cc:
                            continue
                        # 学生限定能力（男子学生/女子学生/教師）は犯人が学生の時だけ
                        if user in ("男子学生", "女子学生", "教師") and not is_student(cn):
                            continue
                        # ★FI-5 step2b：バースト会計 A(3) で割引＝犯人が残日数×最大バースト供給で
                        #   臨界に届くほど価値（届かない=床0.3／手遅れ=0.2）。届く犯人は reach=1.0＝挙動不変。
                        return max(45.0, danger * 0.9) \
                            * self._threat_reach(user, ability, view, culprit=cn)
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
                    or getattr(self, "_experiment", False)) \
                    and target in getattr(self, "_future_culprits", self._culprits):
                return 30.0
            return 2.0
        if "暗躍+1" in ability:
            return 1.0  # 自陣に暗躍を足すのは基本損（マスコミ能力2）
        return 6.0

    def _ability_has_target(self, user: str, ability: str, view: dict) -> bool:
        """その友好能力が今この盤面で発動対象を持つか（投資の即時有用性・B-4b/c）。
        ★保守的＝判定不能な能力は True（過剰減点しない）。危険事件冷却の前倒し投資は
        _ability_value 側が将来犯人を織り込むので magnitude は触らない＝ここは即時対象の
        有無だけを返し、card スコアのタイブレーク／過剰量判定に使う。"""
        if "暗躍" in ability and "除去" in ability:
            if any(view["board_anyaku"].get(a, 0) > 0 for a in self._AREAS):
                return True
            return any((c.get("anyaku", 0) or 0) > 0
                       for c in view["characters"] if c.get("alive"))
        if "不安" in ability and "除去" in ability:
            from engine.data import is_student
            for c in view["characters"]:
                if not c.get("alive") or (c.get("unrest", 0) or 0) <= 0:
                    continue
                nm = c["name"]
                if user in ("男子学生", "女子学生", "教師") and not is_student(nm):
                    continue
                if "臨界以上" in ability:  # ナース＝不安が臨界に達したキャラのみ対象
                    th = unrest_threshold_of(nm)
                    if th is None or (c.get("unrest", 0) or 0) < th:
                        continue
                return True
            return False
        # ★B-24/FI-2：対象クラス限定（学生系＝教師『学生の役職開示』等）は生存対象が居なければ
        #   空撃ち＝False（A-23 と同一判定の共通ヘルパ engine.data.ability_class_target_alive を共用）。
        #   非クラス限定（自身開示・犯人開示・殺害・戻す・同エリア/テリトリー開示 等）は True＝既存
        #   value に委ねる（helper が "学生" 非該当能力に True を返す）。
        alive_names = [c["name"] for c in view["characters"] if c.get("alive")]
        return ability_class_target_alive(user, ability, alive_names)

    # -- プリミティブA(2)/A(3)：reach（FI-3・standalone＝_ability_value 未接続・統合は FI-5） --
    #: 巫女ゲートの移動可/禁止の割引（既存 620-632 の 80/40/10 比＝1.0/0.5/0.125 に対応）。
    _LOC_REACH_MOVABLE = 0.5     # 発動場所に居ないが移動で届く（移動コスト分の割引）
    _LOC_REACH_BLOCKED = 0.125   # 発動場所が禁止エリアで届かない（ほぼ無駄）＝巫女 80×0.125=10 再現
                                 # （FI-5 step1：既存 80/40/10 の 10/80。巫女は神社非禁止＝実発生せず）
    _LOC_REACH_TERRITORY_EMPTY = 0.3  # 大物テリトリーに現状対象なし（移動で入りうる＝0にしない）

    def _location_reach(self, user: str, ability: str, view: dict) -> float:
        """A(2) 居場所（B-13 一般化・FI-3 standalone）：能力の発動に**位置条件**があるとき、user が
        その場所に居る(1.0)／移動で届く(割引)／届かない(低) を 0..1 で返す。位置条件が無い能力は
        1.0（健全側）。★FI-3 では _ability_value 未接続＝挙動同値（統合・係数較正は FI-5）。
        既存の巫女ゲート（神社在=満額）の一般化＝発動場所の到達可能性を単一関数に。
        """
        ch = self._alive(view, user)
        if ch is None:
            return 0.0                       # 死亡/盤外＝発動不能
        area = ch.get("area")
        # 巫女『神社の暗躍除去』＝神社に居てこそ発動（B-13）。神社在=1.0／移動可=0.5／神社禁止=0.15。
        if user == "巫女" and "暗躍" in ability and "除去" in ability:
            if area == "神社":
                return 1.0
            return self._LOC_REACH_MOVABLE if "神社" not in forbidden_of(user) \
                else self._LOC_REACH_BLOCKED
        # 大物『テリトリー内の役職開示』＝対象は縄張り内限定。縄張りに生存対象が居れば発動可(1.0)、
        #   居なければ割引（対象が移動で入りうる＝0にしない）。★対象クラス存在は A(1) が別途見る＝
        #   ここは位置条件（縄張りに誰か居るか）だけ。縄張り情報が無ければ健全側 1.0。
        if user == "大物" and "テリトリー" in ability:
            terr = view.get("oomono_territory")
            if not terr:
                return 1.0
            has_target = any(c["name"] != user and c.get("alive") and c.get("area") == terr
                             for c in view["characters"])
            return 1.0 if has_target else self._LOC_REACH_TERRITORY_EMPTY
        return 1.0                           # 位置条件が無い能力（自身開示・冷却・殺害 等）

    # -- A(3) 到達可能性（B-23・per-state・バースト会計・FI-3 standalone） -------------
    #: バースト供給の係数＝**ユーザー実感値の初期値**（FI-5 で運用表を単一ソースに掃引較正）。
    #  供給源（v2訂正1の正しいリスト）＝不安+1カード×2・ミスリーダー人数・事件効果・医者友好能力。
    #  ★FableA アンカー「ML2人脚本＝+3/日」に整合：カード持続1/日 + ML人数（2ML→1+2=3）。
    #  （不安+1カードは現物2枚＝1ターンの spike は +2 だが、持続供給の日率アンカーは +1/日/対象）。
    _BURST_CARDS_PER_DAY = 1     # 不安+1カードの持続供給（1/日/対象・deckに2枚＝spike時 +1 余地）
    _BURST_PER_ML = 1            # ミスリーダー1人あたり +1/日（mm能力フェイズ）
    _BURST_PER_DOCTOR = 1        # 医者友好能力（脚本家使用＝友好無視+友好2で不安+1・60 B-8）
    _THREAT_REACH_FLOOR = 0.3    # belief依存で「臨界に届かなさそう」でも 0 にしない下限（§規約）
    _LATE_REACH_FLOOR = 0.2      # 手遅れ（算術で臨界未満へ戻せない）側の下限

    @classmethod
    def _max_burst_unrest(cls, current: int, days_left: int, ml_count: int,
                          doctor: bool = False, incident_bonus: int = 0) -> int:
        """残日数 × 最大バースト供給で犯人が到達しうる不安の上限（バースト会計・追加要件2）。

        ＝不安0からでも数ターンで臨界に届きうるかを予期＝「今は不安0だから安全」で
        不安除去投資を殺さない（保守側＝過小に消さない）。事件効果(不安拡大等)は incident_bonus。
        """
        per_day = (cls._BURST_CARDS_PER_DAY + cls._BURST_PER_ML * max(0, ml_count)
                   + (cls._BURST_PER_DOCTOR if doctor else 0) + max(0, incident_bonus))
        return current + per_day * max(0, days_left)

    @staticmethod
    def _coolable_below_threshold(current: int, threshold: int, days_left: int,
                                  removal_per_day: int = 1) -> bool:
        """除去量×残日数で臨界未満へ戻せるか（手遅れ判定・算術＝ハード割引可・追加要件3）。"""
        return (current - removal_per_day * max(0, days_left)) < threshold

    def _plausible_ml_count(self, view: dict) -> int:
        """belief から「ミスリーダーでありうる」生存キャラ数（保守側＝供給を過小評価しない）。

        ＝P(ミスリーダー) が無視できない（>0.15）生存キャラを数える。可能世界に ML が
        残る限りバースト供給を高めに見積もる＝冷却投資を belief 依存で 0 にしない（§規約）。

        ★B-38 の実測結論（2026-07-19・ここに供給漏れは無い）：**学校に暗躍2以上のとき、
        ファクターはミスリーダーの追加能力（不安+1）を得る**（KB 50:174 / 60 A10・`sim/legal.py`
        `gains_misleader`）。この供給を明示的に足す実装を試したが、**コーパス全域で一度も発火しない**
        （3日 呼出9746/加算0・5日 10125/加算0）。理由＝「学校≥2 かつ ファクター候補あり」の局面
        （3日23回・5日64回）では、**その候補が常に ML 候補としても計上済み**（23/23・64/64）で、
        1キャラは最大 +1/日 しか供給できない以上、二重計上しないのが正しいため。
        ＝ファクター経路の供給は、この**保守的な ML 計数に既に吸収されている**（実害なし）。
        belief が「ML は否定したがファクターは残る」状態を作れるようになったら再検討する。
        """
        marg = self._belief.role_marginals()
        alive = {c["name"] for c in view["characters"] if c.get("alive")}
        return sum(1 for n in alive if marg.get(n, {}).get("ミスリーダー", 0.0) > 0.15)

    def _threat_reach(self, user: str, ability: str, view: dict,
                      culprit: str | None = None) -> float:
        """A(3) 到達可能性（standalone・FI-3）：不安除去系が実際に脅威へ届くか＝犯人が残日数×
        バースト供給で臨界に届きうるか（届くほど価値・薄いほど割引）＋手遅れ側。冷却系でない能力は 1.0。

        ★belief 依存部（犯人が臨界に届きうるか）は**割引に留め 0.0 にしない**（§規約・Stage 2b同型）。
          手遅れ側（算術で臨界未満へ戻せない）だけハード割引可。★FI-3 では _ability_value 未接続＝
          挙動同値（統合・係数較正は FI-5）。culprit 未指定なら危険事件の犯人候補を走査し最大 reach。
        """
        if not ("不安" in ability and "除去" in ability):
            return 1.0
        days_left = view.get("days_per_loop", view["day"]) - view["day"] + 1
        ml_count = self._plausible_ml_count(view)
        doctor = any(c["name"] == "医者" and c.get("alive") for c in view["characters"])
        cands = [culprit] if culprit else self._threat_culprits(view)
        if not cands:
            return 1.0                       # 具体的な脅威犯人が見えない＝健全側（冷却を殺さない）
        best = 0.0                           # 犯人間は max（最も冷却価値のある脅威）＝単一犯人は素の値
        for cn in cands:
            c = self._alive(view, cn)
            th = unrest_threshold_of(cn) if cn else None
            if c is None or th is None:
                best = max(best, 1.0)        # 情報不足＝健全側
                continue
            reachable = self._max_burst_unrest(c.get("unrest", 0), days_left, ml_count, doctor)
            if reachable < th:
                # バースト会計でも臨界に届かない＝脅威が実体化しない＝低価値。ただし belief 依存
                #   （ml_count が belief 由来）＝床までの割引で 0 にしない。
                best = max(best, self._THREAT_REACH_FLOOR)
            elif (c.get("unrest", 0) >= th
                  and not self._coolable_below_threshold(c.get("unrest", 0), th, days_left)):
                # 手遅れ：既に臨界以上で、残日数×除去では臨界未満へ戻せない（算術）＝価値減。
                best = max(best, self._LATE_REACH_FLOOR)
            else:
                best = max(best, 1.0)        # 届きうる & 冷却で止められる＝満額
        return best

    def _threat_culprits(self, view: dict) -> list[str]:
        """危険事件（_incident_danger）の犯人候補を集約（冷却で止めたい相手・_ability_value と同源）。"""
        out: set[str] = set()
        for d in getattr(self, "_incident_danger", {}):
            out |= set(getattr(self, "_culprit_cands", {}).get(d, ()))
        return sorted(out)

    # -- プリミティブB tempo（FI-4・standalone＝_ability_value 未接続・統合は FI-5） -----------
    #: ギリギリ発動（発動可能日=ループ最終日ちょうど）の割引＝運用表 原則1（ユーザー実感値・FI-5掃引可）。
    #  (単独, 二正面)。相手/場所指定なし型（情報屋ルールX開示）は指定困難デメリット無し＝二正面残存が高い。
    _TEMPO_JUST_IN_TIME = {"指定なし": (0.25, 0.85), "一般": (0.25, 0.50)}
    #: ★情報系の価値カーブ＝**2軸分離**（運用表 原則4・FableA申し送り 2026-07-19）。
    #  (絶対軸) _INFO_FRESHNESS＝経過ループでの**情報の鮮度**（第1ループ最良・緩やかに減衰・intrinsic）。
    #   ★手遅れの cliff を持たない（旧 _INFO_LOOP_CURVE は絶対loop番号に手遅れを焼き込んでいた＝
    #    3L脚本のL2や5L脚本のL3で誤る＝FableA検出）。「late」＝大きいループ番号のフォールバック。
    _INFO_FRESHNESS = {
        "ルールX開示": {1: 1.0, 2: 0.90, 3: 0.80, "late": 0.65},
        "役職開示":    {1: 1.0, 2: 0.95, 3: 0.90, "late": 0.75},
        "犯人開示":    {1: 0.80, 2: 0.70, 3: 0.60, "late": 0.45},  # 神格L1/L2寄り（刑事は低value側）
    }
    #  (相対軸) _INFO_TIMELINESS＝**残ループでの手遅れ**（loops_left 既知時のみ適用・キー=残ループ数）。
    #   最終L(loops_left=1)/最終L-1(=2)。最終L の FB 復活は is_fb で上書き。最終L-1 の機会コスト減衰は
    #   FI-5 の統合（他脅威の押し出し）が担う＝ここは intrinsic に手遅れな kind のみ入れる（ルールX等）。
    _INFO_TIMELINESS = {
        "ルールX開示": {1: 0.05, 2: 0.40},   # 最終L≒0・最終L-1減衰（ルールXは時間敏感＝原則4）
        "役職開示":    {1: 0.10},             # 最終L手遅れ（FB復活）・最終L-1は機会コスト(FI-5)＝1.0
        "犯人開示":    {1: 0.10, 2: 0.60},
    }
    _TEMPO_LADDER_RELIEF = 0.5   # 梯子（LADDER_CHARS）は減衰を緩和（下位段が積み上げ途中で発動可）
    _INFO_KINDS = frozenset({"軽量情報開示", "重量情報開示", "情報回収"})

    def _activatable_this_loop(self, user: str, ability: str, view: dict) -> bool:
        """当該ループで発動可能か（B-33）：イレギュラーの自身役職開示は第2ループ以降のみ。"""
        if user == "イレギュラー" and "第2L" in ability and view.get("loop", 1) < 2:
            return False
        return True

    @staticmethod
    def _info_curve_key(ability: str) -> str | None:
        if "ルールX" in ability:
            return "ルールX開示"
        if "犯人開示" in ability:
            return "犯人開示"
        if "役職開示" in ability:
            return "役職開示"
        return None

    def invest_tempo(self, user: str, ability: str, need: int, view: dict, kind: str,
                     *, two_front: bool = False, has_plus2: bool = False,
                     is_fb: bool = False, loops_left: int | None = None) -> float:
        """B プリミティブ（FI-4・standalone）：損益分岐日までに投資を完了・発動できるかの割引(0..1)。

        運用表 原則1（最遅開始日＝算術で当該ループ内に間に合うか・ギリギリ割引25/50/85%）＋原則4
        （情報系の価値＝**鮮度(絶対)×手遅れ(相対)** の2軸）を**単一ソースにデータ化**。★_ability_value
        未接続＝挙動同値（統合・係数較正は FI-5）。tt_guard は防御必須で 1.0（テンポ割引の対象外）。
        need＝現在友好差引後の残り必要数／has_plus2＝友好+2札が手札に実在（半減でなく-1ターン・原則1）。
        two_front＝二正面（別席で同時に重量投資）／is_fb＝最終決戦にかける（開示価値復活・原則4②）。
        loops_left＝残ループ数（既知時＝相対軸の手遅れを適用・None＝相対軸は FI-5 機会コストに委ねる）。
        """
        if kind == "tt_guard":
            return 1.0
        if not self._activatable_this_loop(user, ability, view):
            return 0.0                    # 当該ループで発動不能（イレギュラー第2L等・B-33）
        days_left = view.get("days_per_loop", view["day"]) - view["day"] + 1
        turns_to_ready = max(1, need - (1 if has_plus2 else 0))   # +2札は1ターン短縮（表=+2札前提）
        if turns_to_ready > days_left:
            return 0.0                    # 算術的に当該ループで発動が間に合わない（原則1・ハード0可）
        tempo = 1.0
        if turns_to_ready == days_left:   # ギリギリ発動（発動可能日=最終日ちょうど）の割引
            single, tf = self._TEMPO_JUST_IN_TIME["指定なし" if "ルールX" in ability else "一般"]
            tempo = tf if two_front else single
        if kind in self._INFO_KINDS:      # 情報系の価値＝鮮度(絶対)×手遅れ(相対)（原則4・2軸）
            ck = self._info_curve_key(ability)
            if ck:
                fresh = self._INFO_FRESHNESS[ck].get(view.get("loop", 1),
                                                     self._INFO_FRESHNESS[ck]["late"])
                if is_fb:                 # 最終決戦にかける＝開示価値復活（原則4②・相対軸のFB復活）
                    v = 1.0
                else:                     # 相対軸の手遅れ（残ループ既知時のみ・None は FI-5 機会コスト）
                    timeliness = 1.0
                    if loops_left is not None:
                        timeliness = self._INFO_TIMELINESS[ck].get(loops_left, 1.0)
                    v = fresh * timeliness
                if user in LADDER_CHARS:  # 梯子＝減衰を緩和（下位段が途中で発動可・FableA裁定＝全体）
                    v = v + (1.0 - v) * self._TEMPO_LADDER_RELIEF
                tempo *= v
        return tempo

    # -- B-45：L1D1 定石レイヤ（opening book）--------------------------------
    #: 定石3前半＝1日目に不安除去を発動できるキャラ（♡2＝+2一枚で当日発動可）。
    #  医者・ナース＝無条件／男子学生・女子学生＝「周囲に学生2人以上」＝同エリアに他の学生が居ること。
    _OPENING_UNREST_ABLE = ("医者", "ナース")
    _OPENING_STUDENT_ABLE = ("男子学生", "女子学生")
    #: ★定石5（情報系＝情報屋/巫女/学生のいる教師/サラリーマン）の層は**実装しない**
    #  （B-45 Step 2b-2/2b-3 の負の結果・FableA裁定 2026-07-23）。理由＝**一般投資の採点が
    #  既に定石5を実行している**（L1D1 600手の逆向き監査＝定石5対象への友好+2 が65手・+1 が10手。
    #  guard系3日級のサラリーマン＋2＝「サラリーマンだけ3日でも可」の特例にも自然一致）。
    #  層を足しても点28では 200局中0局しか手が変わらず（inert）、点を上げると定石3との
    #  優先順が壊れ、さらに上げると**定石5対象への投資自体を潰して退行**する
    #  （定石3=40 の掃引で btx5_future 7seed が L1 を落とした真因＝サラリーマンへの+2の押しのけ）。
    #  ＝A-41／DP-2 Stage 3b と同型の「現主人公AI＋現コーパスには余地が無い」。

    def _opening_plus2_rank(self, tgt: str, view: dict) -> float | None:
        """L1D1 の友好+2 配分の優先表（該当なしは None＝通常採点へフォールスルー）。

        ★戻り値は**下限**として使う（呼び出し側で max）＝通常採点の方が高い対象は下げない。
        ★定石5原文の「カードを伏せられていない」＝ここで明示的に除外する（floor は
        呼び出し側の mm札回避（5.0）より後に当たるため、ヘルパ側で守らないと 1/L の +2 を
        友好禁止かもしれない対象へ撃ってしまう＝ユーザー知見 2026-07-07 の退行になる）。
        """
        c = self._alive(view, tgt)
        if c is None:
            return None
        if any(p.get("owner") == "mastermind" and p.get("target_kind") == "character"
               and p.get("target") == tgt for p in view.get("placements", [])):
            return None
        # ① 定石3前半＝当日発動可の不安除去能力者
        if tgt in self._OPENING_UNREST_ABLE:
            return PRIORITY["定石_不安除去能力者+2"]
        if tgt in self._OPENING_STUDENT_ABLE:
            from engine.data import is_student
            area = c.get("area")
            if any(o.get("alive") and o.get("area") == area and o["name"] != tgt
                   and is_student(o["name"]) for o in view["characters"]):
                return PRIORITY["定石_不安除去能力者+2"]
        # ② 定石5（情報系）は層を持たない＝上のクラス変数コメント参照（通常採点が既に実行済み）。
        return None

    def _unrest_able_areas(self, view: dict) -> set[str]:
        """1日目に不安除去を発動できるキャラが居るエリア（定石3の能力者と同一判定）。"""
        from engine.data import is_student
        out: set[str] = set()
        for c in view["characters"]:
            if not c.get("alive") or c.get("area") is None:
                continue
            n = c["name"]
            if n in self._OPENING_UNREST_ABLE:
                out.add(c["area"])
            elif n in self._OPENING_STUDENT_ABLE and any(
                    o.get("alive") and o.get("area") == c["area"] and o["name"] != n
                    and is_student(o["name"]) for o in view["characters"]):
                out.add(c["area"])
        return out

    #: 定石3後半＝「不安臨界2以下でカードを伏せられているキャラ」が準備移動の対象。
    _PREP_MOVE_MAX_TH = 2

    # ★B-47（合成不成立の回避）は**保留＝負の結果**（2026-07-23・B-35/A-5(e)型の罠）。
    #   「自札×mmの各移動札の合成先が禁止＝壊れる準備移動」をゲートで外したところ、5日 defense
    #   64→63 が退行（random_FS#3 が L3 defense→L9 loss）。機序＝合成先が禁止だと**両方の移動が
    #   void**＝壊れる準備移動は**mm の移動札を打ち消す防御手**だった（probe実測：p3が準備移動35で
    #   mm の 移動斜め→男子学生 を pin・代替の 友好+2→医者 は34＝skip すると mm移動が通り負ける）。
    #   ＝「無駄に見える手が相手を縛る」B-35/A-5(e) と同型＝現主人公AI相手では**撤去が正**。
    #   復活の条件＝opponent-model 時代（mm札の中身推定つき）で「pin にならない壊れる手」だけを
    #   選別できるようになった時。詳細＝docs/監査_B47_合成不成立回避は退行_AIB_2026-07-23.md。

    def _opening_prep_move(self, o: dict, view: dict) -> float | None:
        """L1D1 準備移動（定石3後半）＝臨界2以下でmm札のキャラを不安除去能力者のボードへ寄せる。

        ★ユーザー原則（FableA裁定 2026-07-23）＝「臨界が迫らない限り不安-1は温存＝その席は
        移動に使う方が効率的」。点は**投機的冷却（不安0×伏せ札への-1＝実測60%が床空振り）**の
        上・実効が見込める冷却の下を狙う（掃引で較正）。戻り値は下限（呼び出し側で max）。
        """
        if o.get("target_kind") != "character":
            return None
        card = o.get("card") or ""
        if card not in _MOVE_TOGGLE:
            return None
        tgt = o.get("target")
        c = self._alive(view, tgt)
        if c is None:
            return None
        # 「カードを伏せられている」＝mmが今ターン札を置いた対象（定石3後半の原文）
        if not any(p.get("owner") == "mastermind" and p.get("target_kind") == "character"
                   and p.get("target") == tgt for p in view.get("placements", [])):
            return None
        th = unrest_threshold_of(tgt)
        if th is None or th > self._PREP_MOVE_MAX_TH:
            return None
        dst = _move_dest(c.get("area"), card)
        # ★禁止エリアへは動かない（移動が不成立＝準備にならない）
        if dst is None or dst == c.get("area") or dst in forbidden_of(tgt):
            return None
        if dst not in self._unrest_able_areas(view):
            return None
        # ★B-47（合成不成立の回避）は退行のため入れない＝上のクラスコメント参照。
        #   「壊れる準備移動」は mm の移動を打ち消す防御手＝skip すると 5日 defense 退行。
        s = PRIORITY["定石_準備移動"]
        # ★ML候補からの引き離し（FableA指定の第2要素）＝タイブレークの加点のみ。
        #   出発地に ML 候補が居て移動先に居ないなら、寄せと同時に不安源から離せる。
        try:
            ml = {n for n, d in self._belief.role_marginals().items()
                  if d.get("ミスリーダー", 0.0) >= 0.2}
        except Exception:
            ml = set()
        if ml:
            here = {x["name"] for x in view["characters"]
                    if x.get("alive") and x.get("area") == c.get("area")}
            there = {x["name"] for x in view["characters"]
                     if x.get("alive") and x.get("area") == dst}
            if (ml & here) and not (ml & there):
                s += 1.0
        return s

    def _opening_higher_present(self, options: list[dict], view: dict) -> bool:
        """この席に**定石6より上位の定石**（定石3＝+2配分／定石3後半＝準備移動）の候補が在るか。

        定石は列挙順＝優先順位なので、上位が在る席では定石6を発火させない（点の大小だけで
        順序を表すと将来の較正で反転しうる＝2026-07-22 に定石3/定石5 で実際に起きた事故）。
        """
        # ★メモは **id() でなくオブジェクト参照** で持つ（id は解放後に再利用され、
        #   別の options が同じ id を得て古い結果を返す＝テストが実際に踏んだ）。
        #   参照を保持すること自体が id の再利用を防ぐ。
        if getattr(self, "_op_hi_src", None) is not options:
            self._op_hi_src = options
            self._op_hi = any(
                (self._opening_plus2_rank(o.get("target"), view) is not None)
                if o.get("card") == "友好+2"
                else (self._opening_prep_move(o, view) is not None)
                for o in options)
        return self._op_hi

    #: 定石6＝「キャラが4人以上固まっているボード」。
    _CROWD_MIN = 4

    def _n_at(self, view: dict, area: str) -> int:
        return sum(1 for x in view["characters"]
                   if x.get("alive") and x.get("area") == area)

    def _opening_removal_protected(self, view: dict) -> set[str]:
        """定石3の**除去対象**（不安除去を受ける側）＝定石6の mover から外すキャラ。

        FableA補足（ユーザー確認 2026-07-23）＝定石3で能力者エリアへ寄せた/寄せる予定の
        キャラを定石6が引き剥がして計画を壊すのを防ぐ。対象＝mm札あり×臨界2以下で、
        (a) 既に不安除去能力者のエリアに居る＝現に受けている、または
        (b) 準備移動でそのエリアへ動かす予定になっている。※能力者自身ではない。
        """
        able = self._unrest_able_areas(view)
        carded = {p.get("target") for p in view.get("placements", [])
                  if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}
        out: set[str] = set()
        for c in view["characters"]:
            n = c["name"]
            if not c.get("alive") or n not in carded:
                continue
            th = unrest_threshold_of(n)
            if th is None or th > self._PREP_MOVE_MAX_TH:
                continue
            if c.get("area") in able:
                out.add(n)                       # (a) 現に受けている
                continue
            for mc in _MOVE_TOGGLE:              # (b) 準備移動の行き先になっている
                d = _move_dest(c.get("area"), mc)
                if d and d != c.get("area") and d not in forbidden_of(n) and d in able:
                    out.add(n)
                    break
        return out

    def _opening_spread_mover(self, view: dict, area: str) -> str | None:
        """area の 4人以上の群から**動かす1人**を選ぶ（定石6の mover 選定・B-45b）。

        原文＝友好能力の評価（self._invest）が一番低いキャラ。同率のタイブレークは
        **mm札を伏せられているキャラ優先**。ただし定石3の除去対象（受ける側）は候補から除く。
        """
        here = [x for x in view["characters"]
                if x.get("alive") and x.get("area") == area]
        if len(here) < self._CROWD_MIN:
            return None
        protected = self._opening_removal_protected(view)
        cands = [x["name"] for x in here if x["name"] not in protected]
        if not cands:
            return None
        inv = getattr(self, "_invest", None) or {}
        carded = {p.get("target") for p in view.get("placements", [])
                  if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}
        # キー＝(評価の低さ, mm札を持つ方を優先, 名前で決定的)
        return min(cands, key=lambda n: (inv.get(n, 0.0), 0 if n in carded else 1, n))

    def _opening_spread_move(self, o: dict, view: dict) -> float | None:
        """L1D1 散開移動（定石6）＝4人以上の板から1人を**最小人数のボード**へ動かす（B-45b）。

        ①移動先＝この mover が到達できる有効ボードのうち**最小人数**（同数タイは可）。
          「出発地より少ない先しか無ければ妥協」＝厳密に最小のみ発火＝候補が1つならそれ。
        ②動かすキャラ＝`_opening_spread_mover`（評価最低・mm札優先・定石3除去対象は除外）。
        戻り値は下限（呼び出し側で max）。
        """
        if o.get("target_kind") != "character":
            return None
        card = o.get("card") or ""
        if card not in _MOVE_TOGGLE:
            return None
        tgt = o.get("target")
        c = self._alive(view, tgt)
        if c is None:
            return None
        area = c.get("area")
        if tgt != self._opening_spread_mover(view, area):
            return None
        n_here = self._n_at(view, area)
        # この mover の全有効移動先（禁止・自エリア・非「少ない方向」を除く）と人数
        dests: dict[str, int] = {}
        for mc in _MOVE_TOGGLE:
            d = _move_dest(area, mc)
            if d is None or d == area or d in forbidden_of(tgt):
                continue
            nd = self._n_at(view, d)
            if nd < n_here:                       # 少ない方向のみ
                dests[mc] = nd
        if card not in dests:
            return None
        # ①最小人数の移動先のみ発火（複数カードが有効な時は少ない板を選ぶ）
        if dests[card] != min(dests.values()):
            return None
        return PRIORITY["定石_散開移動"]

    #: B-52＝kill事件の ML 分離が発火する ML 推定確度の下限（掃引で較正）。
    #  ★ボード供給路（0.55）より低くてよい理由＝KP死は即敗北＝分離1枚のコストに対し損失が桁違い。
    _B52_ML_MIN = 0.5
    _B52_KILL_INCIDENTS = ("遠隔殺人", "殺人事件", "病院の事件")

    def _b52_kill_ml_separation(self, o: dict, view: dict,
                                ml_name: str | None, ml_p: float) -> float | None:
        """kill事件の犯人を同エリアの ML から引き離す（B-52・fs5_guard 検死）。

        条件（FableA裁定＝自明情報のみ・B-35型回避で絞る）：
          ①今日/近接に kill事件が予定（_lethal_days）で **KP が生存**（KP死＝即敗北）
          ②ML 候補の推定確度 ≥ _B52_ML_MIN（掃引で較正）
          ③犯人候補が ML と同エリア（＝ML の +1 で不安が積まれる）
          ④**冷却1枚では臨界を割れない**（unrest==臨界ちょうど＝-1で床に落ちても ML+1 で戻る）
          ⑤option が「犯人 or ML を相手のエリアから引き離す移動」
        """
        if o.get("target_kind") != "character" or (o.get("card") or "") not in _MOVE_TOGGLE:
            return None
        if not ml_name or ml_p < self._B52_ML_MIN:
            return None
        mlc = self._alive(view, ml_name)
        if mlc is None:
            return None
        # ① KP 生存（belief）
        marg = self._belief.role_marginals()
        kp_alive = any(marg.get(oc["name"], {}).get("キーパーソン", 0.0) > 0.05
                       for oc in view["characters"] if oc.get("alive"))
        if not kp_alive:
            return None
        kill_days = {i["day"] for i in view.get("incidents", [])
                     if i.get("name") in self._B52_KILL_INCIDENTS}
        tgt = o.get("target")
        for _d in kill_days:
            if not (0 <= _d - view["day"] <= 1):     # 今日/明日の kill事件のみ（近接に絞る）
                continue
            for cn in getattr(self, "_culprit_cands", {}).get(_d, ()):
                if cn == ml_name:
                    continue                          # 犯人=ML本人は分離不能
                cc = self._alive(view, cn)
                if not (cc and cc["area"] == mlc["area"]):
                    continue                          # ③同エリアでなければ ML ポンプが無い
                th = unrest_threshold_of(cn)
                if th is None:
                    continue
                # ④冷却1枚では止まらない＝unrest が臨界ちょうど（-1で床に落ちるが ML+1 で戻る）。
                #   unrest-1≥th（冷却しても臨界以上）は分離でも救えない＝対象外。
                if cc["unrest"] - 1 >= th or cc["unrest"] < th:
                    continue
                dest = _move_dest(cc["area"], o.get("card"))
                if dest is None or dest == cc["area"]:
                    continue
                # ⑤犯人を ML エリアから／ML を犯人エリアから引き離す移動のみ
                if (tgt == cn and dest != mlc["area"] and dest not in forbidden_of(cn)) or \
                   (tgt == ml_name and dest != cc["area"] and dest not in forbidden_of(ml_name)):
                    return PRIORITY["危険犯人_ML分離_kill"]
        return None

    def _compute_invest(self, view: dict) -> dict[str, float]:
        """友好+カードの投資先スコア＝実装済み能力の（価値 ÷ 残り必要ハート）の最大値。
        あわせて最良能力の残り必要ハート（_invest_need）と即時対象の有無（_invest_has_tgt）を
        キャッシュする（B-4a 過剰量ペナルティ・B-4b/c 即時対象タイブレーク用）。"""
        from sim.abilities import is_implemented
        from engine.data import ability_kind
        invest: dict[str, float] = {}
        self._invest_need: dict[str, int] = {}
        self._invest_has_tgt: dict[str, bool] = {}
        # ★①tempo タイブレークの文脈（decision path は loops_total 常在＝None率0%を実測済み）。
        _loops_left = (view["loops_total"] - view["loop"] + 1) if "loops_total" in view else None
        _is_fb = bool(getattr(self, "_loop_lost", False))
        _used = view.get("used_cards", {})
        # ★形が dict でない view（合成/簡易 view）は「+2札の実在を確認できない」＝仮定しない
        #   （v2規約 fail-ignore＝無い札で短縮を数えない）。実 sim の view は dict（sim/views.py）。
        _has_plus2 = (isinstance(_used, dict)
                      and any("友好+2" not in _used.get(_s, []) for _s in ("p1", "p2", "p3")))
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
                # ★①tempo はタイブレーク限定：val を割らず、同値〜近接スコア間の優先度のみ動かす
                #   （打ち切り＝val乗数は全スコープで退行＝§8-1）。loops_left は view から明示計算、
                #   has_plus2 は実手札（used_cards）から＝v2規約（fail-ignore）遵守。
                _tempo = self.invest_tempo(
                    n, ab["name"], need, view, ability_kind(n, ab["name"]) or "",
                    has_plus2=_has_plus2, is_fb=_is_fb, loops_left=_loops_left)
                s = val / (1.0 + need) + self._TEMPO_TIEBREAK * _tempo
                if s > invest.get(n, 0.0):
                    invest[n] = s
                    self._invest_need[n] = need
                    self._invest_has_tgt[n] = self._ability_has_target(
                        n, ab["name"], view)
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
        # ★実証済みの敗北ボード（過去の敗北ループで≥2）が最優先＝ルール未確定でも守る
        odb = getattr(self, "_observed_defeat_board", None)
        if odb:
            return odb
        # 暗躍が積まれているボードを危険とみなす。★黒猫の強制+1（神社・ループ開始）は
        # 脚本家の意図ではないノイズ＝差し引いて判断（デコイに釣られない）
        ba = dict(view.get("board_anyaku", {}))
        if self._alive(view, "黒猫") and "神社" in ba:
            ba["神社"] = max(0, ba["神社"] - 1)
        top = max(ba, key=lambda a: ba[a]) if ba else None
        return top if top and ba.get(top, 0) > 0 else None

    def _board_threat_live(self, view: dict, area: str) -> bool:
        """★B-37（postmortem 後の非退行版）：そのボードの暗躍が『敗北につながる経路』に接続するか。
        どれにも接続しなければ**確定で死んだボード**＝暗躍禁止は証明可能な無駄手（人間mmの釣りに
        吸われる病院＝棋譜2026-07-18）。belief 健全側＝可能世界に経路が残る限り live（未確定期の防御は維持）。

        ★★このゲートは呼び出し側で**病院に限定**して適用する（下記 mm_board_now ゲート）。
        postmortem（2026-07-19）＝一般適用は5日防衛-1退行（random_FS#5/#11 が def→loss）：
        board_x（復讐者/爆弾）は _defeat_board_probs がクロマク/ウィッチ位置分布で板へ散らす＝
        belief 未確定期に真の敗北板の P が薄まり dead 誤判定＝真の防御を降格した（1134の警告の再現）。
        **病院は FS/BTX で唯一どの敗北ルールも指さない板**（board_x が病院なら probs>0・病院の事件は
        (b)で拾う）＝病院限定なら board_x/学校/神社の over-cut を避けつつ棋譜の釣りを切れる
        （実測＝5日防衛 64→65・loss 1→0・3日中立）。他板への拡張は _defeat_board_probs の
        board_x/factor 寄与を精緻化してから（別チケット）。

        経路（列挙・全板で正しい判定＝将来ゲートを広げる時のため）：
          (a) 可能世界に残る敗北ルール（守る=学校/封印=神社/board_x）＝_defeat_board_probs（単一ソース）
          (b) 予定事件のボード参照（病院の事件=病院・邪気の汚染=神社）
          (c) ★ファクター状態能力の板寄与（都市≥2=KP能力/学校≥2=ML能力・B-34）＝_defeat_board_probs
              に無い経路（都市/学校）。postmortem 点①の監査で発見＝DP-1 losstree にも波及する別チケット。
        """
        probs = getattr(self, "_board_defeat_probs", None)
        if probs is None:
            from agents.defense_plan import _defeat_board_probs
            probs = _defeat_board_probs(self._belief, view)
        if probs.get(area, 0.0) > 1e-9:                     # (a) 敗北ルール（P>0＝可能世界に残る）
            return True
        incs = {i.get("name") for i in view.get("incidents", [])}
        if area == "病院" and "病院の事件" in incs:            # (b) 病院の事件＝病院暗躍≥1で致死
            return True
        if area == "神社" and "邪気の汚染" in incs:            # (b) 邪気の汚染＝神社+2（保守側）
            return True
        if area in ("都市", "学校"):                          # (c) ファクター状態能力（都市/学校）
            marg = self._belief.role_marginals()
            if any(m.get("ファクター", 0.0) > 0.0 for m in marg.values()):
                return True
        return False

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
        self._plan_recs = self._defense_plan_recs(view, options)
        keyperson = self._keyperson
        killer = self._killer
        # ★B-37：per-board「そのボードが敗北ボードでありうるか」を belief から先に算出
        #   （_defeat_board_probs＝per-board 単一ソース）。暗躍禁止設置ゲートが共用。
        from agents.defense_plan import _defeat_board_probs
        self._board_defeat_probs = _defeat_board_probs(self._belief, view)
        danger_board = self._guess_defeat_board(view)
        culprits = self._culprits

        # ★脚本家が"今ターン"ボードへ置いた札の位置（主人公は配置位置を見られる＝中身は伏せ）。
        #   脚本家がボードにカードを置く＝ほぼ暗躍。盤面敗北ルール（守る/封印/復讐者/巨大時限爆弾）や
        #   病院の事件が未否定なら、そのボードは負け筋になりうる＝暗躍禁止でほぼ確実に潰す
        #   （ユーザー知見 2026-07-07：人間はここをほぼ確実に守る。ブラフ空振りのリスクは許容）。
        mm_board_now = {p["target"] for p in view.get("placements", [])
                        if p.get("owner") == "mastermind" and p.get("target_kind") == "board"}
        # 脚本家が"今ターン"キャラへ伏せた札の位置（中身は伏せ）。友好禁止の可能性があるので
        #   1/Lの友好+2をここへ撃たない（ユーザー知見 2026-07-07）。
        mm_char_now = {p["target"] for p in view.get("placements", [])
                       if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}
        # ★通常クロマクの void ボード見切り（B-3・2026-07-14）：mmが今ターン札を置いていない
        #   ボード（void）に通常クロマク疑い（幻想除く）が座り、かつそのクロマクを動かす移動札が
        #   options にある時、そのボードへの暗躍禁止は空振り＝クロマク供給はmm能力フェイズで
        #   暗躍禁止免疫（60 A16/Q3）・void なので今ターン打ち消せる暗躍+札も無い。クロマク引き剥がし
        #   移動に席を譲る。★移動札がある局面に限定＝札が無ければ従来値（proxy防御）を保ち、
        #   幻想限定demotionを全クロマクに広げてrevenge/shrine全敗した回帰を避ける（line 833-834）。
        # ★確信度≥0.7（_kuromaku_suspects）に限定：候補どまり（≥0.3）まで広げると
        #   引き剥がしが不確実な相手にも席を割きBTXの1脚本で+3ループ回帰した実測（2026-07-14）。
        _move_targets = {o.get("target") for o in options
                         if str(o.get("card", "")).startswith("移動")}
        self._relocatable_kuro_void: set[str] = set()
        for _s in getattr(self, "_kuromaku_suspects", ()):
            if _s == "幻想":
                continue
            _cs = self._alive(view, _s)
            if _cs and _cs["area"] not in mm_board_now and _s in _move_targets:
                self._relocatable_kuro_void.add(_cs["area"])
        # ★カルティストの可能性が残るキャラ（belief で除外されていない＝prob>0.1）。カルティストは
        #   公開情報から特定困難（多くの脚本で候補が横並び＝0.2前後）＝≥0.7の確信を待つと永遠に
        #   発火しない。tellベースの「カルティスト移動封じ」用に、除外されていない候補で判定する。
        _cult_marg = self._belief.role_marginals()
        _cultist_maybe = {n for n in mm_char_now
                          if _cult_marg.get(n, {}).get("カルティスト", 0.0) > 0.1}
        _rules_m = self._belief.rule_marginals()
        _p_board = sum(pv for (ry, _x), pv in _rules_m.items()
                       if ry in ("守るべき場所", "封印されしモノ",
                                 "復讐者の灯火", "巨大時限爆弾Xの存在"))
        board_rules_possible = (_p_board > 0.05
                                or any(i.get("name") == "病院の事件"
                                       for i in view.get("incidents", [])))
        # ★今日の致死事件の kill zone（テスター検死 FS s9・2026-07-11）：病院の事件は「病院に
        #   暗躍≥1なら病院の全員死亡」＝病院がゾーン。今日 病院の事件が予定されているなら、KPや
        #   クロマク（病院で自ら暗躍を汲んで致死化させる）をそこへ動かすのは自滅＝移動先で回避する。
        _today_kill_zone = None
        for _inc in view.get("incidents", []):
            if _inc.get("day") == view["day"] and _inc.get("name") == "病院の事件":
                _today_kill_zone = "病院"
                break
        # ★カルティスト移動封じの標的（テスター検死 BTX s3・2026-07-11）：敗北ボードにmm暗躍札が
        #   あり、そのボードへ運ばれうるカルティスト候補（除外されてない・敗北ボードにまだ居ない・
        #   mm札あり）が**ちょうど1人**の時だけ、その1人を移動禁止でピンする。2人以上＝どれが
        #   カルティストの移動か読めず複数ピンは席の浪費（fs5_guardで刑事/医者を二重ピンして
        #   防衛を潰した実測）＝発火しない。1人に絞れる時だけの二段防御（暗躍禁止→ボードの片翼）。
        _cultist_pin_target = None
        if danger_board is not None and danger_board in mm_board_now and board_rules_possible:
            _cand = [n for n in _cultist_maybe
                     if (cc := self._alive(view, n)) and cc["area"] != danger_board]
            if len(_cand) == 1:
                _cultist_pin_target = _cand[0]

        # ★B-28：card_effect.noop_reason（単一チョークポイント）に渡す文脈。
        #   defense_plan の break 生成も同じ述語＋同じ材料を見る＝PLAN_HOT(+88) が heuristic の
        #   空振りゲートを構造的に上書きする事故（B-21/B-21b/B-26）を封鎖する。
        #   ★このターン内で不変＝**1回だけ構築**する（option毎に組むと frozenset 4個の割当が
        #   全候補×全席×全ターンに乗る＝素直に無駄。※性能上の必須ではない：ベンチ所要は
        #   hoist 前後とも 22秒台で有意差なし＝「候補数ぶん同じ物を作らない」という形の問題）。
        _noop_ctx = NoopCtx(
            mm_chars=frozenset(mm_char_now),
            keyperson=keyperson,
            kill_zone=_today_kill_zone,
            kuromaku_suspects=frozenset(getattr(self, "_kuromaku_suspects", ())),
            killer_suspects=frozenset(getattr(self, "_killer_suspects", ())),
            friend_guards=frozenset(getattr(self, "_friend_guards", ())),
        )

        def _base_score(o: dict) -> float:
            card, tgt, kind = o["card"], o["target"], o.get("target_kind")
            if card == "暗躍禁止":
                if self._kinshi_used:
                    return PRIORITY["自滅回避"]  # 1ターン2枚目は自滅（絶対に避ける）
                # ★キャラへの暗躍禁止は「そのキャラに載った今ターンの暗躍+」しか打ち消せない
                #   ＝mmが今ターンそのキャラに札を伏せていなければ確実に空振り（テスター指摘
                #   2026-07-10：mm札の無いサラリーマンへの暗躍禁止は無意味）。中身は伏せなので
                #   位置（mm_char_now）だけで判定＝置いていない＝暗躍でもない＝no-op。
                # ★B-28：空振り判定は card_effect.noop_reason（単一チョークポイント）へ集約。
                #   plan の break 生成も同じ述語を見る（＝PLAN_HOT がゲートを潰す事故の構造封鎖）。
                if (_np := noop_reason(view, card, tgt, kind, _noop_ctx)) is not None:
                    return _np.score
                # ★キラー疑いのキャラに暗躍が積まれている＝暗躍4で主人公死亡（ルール）の兆候。
                #   3以上なら次の+1で死＝キーパーソン防御より優先して封じる。
                if kind == "character" and tgt in self._killer_suspects:
                    c = self._alive(view, tgt)
                    if c and c["anyaku"] >= 3:
                        return PRIORITY["キラー暗躍3封じ"]
                    # ★脚本家が今ターン札を伏せたキラー候補＝ほぼ暗躍（暗躍4＝主人公死へ向かう）。
                    #   暗躍0の初手でも反応して封じる（閾値待ちだとクロマク併用で暗躍4に届く前に
                    #   止められない＝実測 basic/btx_lovers L2 の敗因。ユーザー知見 2026-07-07）。
                    #   ★複数のキラー候補に札が伏せられたら、キラー確率が高い方を優先（重み付け）。
                    # ★脅威トリアージ（反応型）：キラー暗躍4＝主人公死亡は位置に依存しない
                    #   ＝暗躍禁止で断つ。ただしKP防御と競合するので、mmが実際にキラー路線を
                    #   始動した（暗躍が乗った or 過去ループで路線を観測）時だけKP防御(100)より
                    #   上げる。（一律キラー優先は完全情報basicの1Lクリアを壊した実測の教訓）
                    # ★分岐順バグ修正（2026-07-07・vs CEM gen2 mm実測）：以前は下の
                    #   「候補反応封じ(100)」が早期returnでこの105を影に隠し、D1暗躍0では
                    #   KP防御(100)と同点タイ→basicで毎ループD1の暗躍+2→キラーが素通り
                    #   →D2にクロマク併用で暗躍4＝主人公死。確信＋路線観測は先に判定する。
                    if c and tgt in self._killer_strong and (
                            c["anyaku"] >= 1 or getattr(self, "_killer_plan_seen", False)):
                        return PRIORITY["確信キラー反応封じ"]
                    if c and tgt in mm_char_now and c["anyaku"] < 4:
                        return PRIORITY["キラー候補反応封じ"] + 16.0 * self._killer_prob.get(tgt, 0.0) + c["anyaku"] * 4.0
                    # 先制封鎖：確信キラーがクロマク疑いと同エリア＝ポンプ即開始が読める。
                    if (c and tgt in self._killer_strong
                            and any((kc := self._alive(view, k)) and kc["area"] == c["area"]
                                    for k in self._kuromaku_suspects if k != tgt)):
                        return 95.0
                    if c and c["anyaku"] >= 2:
                        return 70.0
                if kind == "character" and tgt == keyperson and self._alive(view, keyperson):
                    # キラー/契約が可能な時だけ100。殺害圏に近づくほど優先（暗躍1で+6）。
                    # ★ただし脚本家がKPに札を伏せた or 既に暗躍が乗っている時だけ高優先で封じる。
                    #   札も暗躍も無いKPへの暗躍禁止は空振り（今ターン暗躍が乗らない）＝脚本家が
                    #   実際に札を置いた対象（キラー等）を優先させる（実測 L2D1 の空振りの教訓）。
                    kc = self._alive(view, keyperson)
                    if keyperson in mm_char_now or (kc and kc["anyaku"] >= 1):
                        return self._kp_guard + 6.0 * min(kc["anyaku"], 2) if kc else self._kp_guard
                    return 20.0  # 札も暗躍も無い＝空振り。他に脅威が無いときの保険程度
                # ★脚本家が今ターン札を伏せたキーパーソン"候補"（belief不確定・確定KPではない）＝
                #   暗躍を仕込んでいる可能性が高い。暗躍が2に溜まる前に暗躍禁止で潰す
                #   （ユーザー知見 2026-07-07）。KP殺害が live（キラー/契約）なときだけ。中優先。
                #   ※完全情報ならKPが1人に確定＝候補=確定KP自身＝この分岐は発火しない（回帰なし）。
                if kind == "character" and tgt in mm_char_now and tgt != keyperson \
                        and tgt in getattr(self, "_kp_suspects", ()) and self._kp_guard >= 50:
                    cc = self._alive(view, tgt)
                    if cc and cc["anyaku"] < 2:
                        return PRIORITY["KP候補封じ"] + cc["anyaku"] * 3.0
                # ★脚本家が今ターン置いたボードが負け筋になりうる＝ほぼ確実に暗躍禁止で潰す
                #   （キラー暗躍4＝即死 105-110 の次に高い優先。KP防御や既存危険ボードより上）。
                #   ただしカルティストが同ボードに居ると暗躍禁止は無視される＝ここでは打たず、
                #   カルティスト引き剥がし(c2)に任せる（無駄撃ち防止・完全情報で回帰した教訓）。
                # ★暗躍禁止で止まらない供給の見切り（幻想限定）：幻想＝クロマク疑いがこの
                #   ボードに立ち、mm能力フェイズのボード汲み上げが観測済み＝+1/日は暗躍禁止を
                #   素通りする（60 A16/Q3）。幻想は直接移動できず、引き剥がしはボード経由＝
                #   暗躍禁止と同じボード枠を食い合う→引き剥がし(89/74)に枠を譲る。
                #   通常キャラのクロマクは(c3)のキャラ直接移動と両立できるので降格しない
                #   （降格を全クロマク疑いに広げたらrevenge/shrine全敗の実測＝幻想限定が正）。
                # ★降格の判定（レース算術）：幻想クロマクのポンプ（+1/日・暗躍禁止免疫）が
                #   このボードに付いている時、
                #   (a) mmが置いていないターン＝暗躍禁止は完全空振り→常に降格（引き剥がし89へ）
                #   (b) mmが置いたターンでも「現在値＋残日数×1 ≥ 臨界2」＝暗躍禁止で全カードを
                #       止めてもポンプだけで負けが確定→降格（カードを通されるリスクを取ってでも
                #       引き剥がしが唯一の勝ち筋。random_BTX seed1 L2：D1/D2に置かれ続けて
                #       枠を吸われ、ポンプでD2に2到達した実測）。
                #   (c) レースが負けでない＋mmが置いたターン＝暗躍禁止でカードを止める（96）。
                _gc = self._alive(view, "幻想")
                _days_left = view.get("days_per_loop", view["day"]) - view["day"] + 1
                _pump_race_lost = (view["board_anyaku"].get(tgt, 0) + _days_left >= 2)
                kuro_pump_here = (kind == "board"
                                  and (tgt not in mm_board_now or _pump_race_lost)
                                  and tgt in getattr(self, "_mm_pump_boards", ())
                                  and _gc is not None and _gc["area"] == tgt
                                  and ("幻想" in self._kuromaku_suspects
                                       or "幻想" in getattr(self, "_kuromaku_cands", ())))
                # ★手遅れボードの見切り（テスター検死 2026-07-09）：そのボードの暗躍が
                #   既に2以上＝ボード敗北条件は今ループ成立済み。暗躍禁止を置いても
                #   何も変わらない（8/8全敗ログでD2/D3の神社封じに毎回席を浪費した実測）。
                _board_too_late = (kind == "board"
                                   and view["board_anyaku"].get(tgt, 0) >= 2)
                # ★空振りボードの見切り（同検死）：主人公はmmの配置位置を見てから置く＝
                #   mmが今ターンそのボードに札を置いていなければ、ボード暗躍禁止が
                #   打ち消せる札は存在しない（黒猫/クロマク/噂は別タイミング＝どのみち
                #   止まらない）。確実な空振りに高優先を与えない。
                _board_void = (kind == "board" and tgt not in mm_board_now)
                # ★B-37：mmが今ターン置いた板でも、病院が「確定で死んだ板」（敗北ルール/予定事件に
                #   無接続）なら暗躍禁止を高評価しない（人間mmの病院釣り＝棋譜2026-07-18）。病院限定
                #   ＝board_x/学校/神社の over-cut 退行を避ける（postmortem・_board_threat_live 参照）。
                if kind == "board" and tgt in mm_board_now \
                        and (tgt != "病院" or self._board_threat_live(view, "病院")) \
                        and not kuro_pump_here and not _board_too_late \
                        and not any((cc := self._alive(view, s)) and cc["area"] == tgt
                                    for s in self._cultist_suspects):
                    # 複数ボードに置かれた時は危険ボード（実証済み敗北ボード等）を優先
                    # （同点rngで病院＝事件専用ボードに流れて真のゴールを素通しした実測）
                    return PRIORITY["ボード封じ_mm札"] + view["board_anyaku"].get(tgt, 0) \
                        + (4.0 if tgt == danger_board else 0.0)
                # ★危険ボード（敗北条件のボード）は暗躍が0でも最優先で封じる
                #   （黒猫の神社暗躍などの「見えている暗躍」に釣られてボードXを空けない）
                if kind == "board" and tgt == danger_board and not _board_too_late \
                        and not _board_void:
                    if kuro_pump_here:
                        return PRIORITY["ポンプ見切り"]  # 札は止まるが供給素通り＝引き剥がしに譲る
                    return PRIORITY["ボード封じ_危険"] + view["board_anyaku"].get(tgt, 0)
                if kind == "board" and view["board_anyaku"].get(tgt, 0) > 0 \
                        and board_rules_possible and not _board_too_late:
                    # ★盤面敗北ルールの可能性が消えているなら、見えている暗躍はデコイ
                    #   （黒猫の神社+1等）＝置かない（btx_future実測：存在しないボード
                    #   条件へ81点で毎ターン1席を浪費し、蝶/TTの防衛が痩せた）。
                    #   ★手遅れ（暗躍≥2）は置かない（テスター検死 2026-07-10：神社=2に
                    #   80+2=82で毎ターン席を浪費）。※空振り(void)ゲートはここには付けない
                    #   （FSの黒猫/クロマクで育つ真の敗北ボードを降格し def→loss を招いた実測）。
                    if kuro_pump_here:
                        return PRIORITY["ポンプ見切り"]
                    # ★邪気の汚染（事件）が神社を+2する脚本で、mmが今ターン神社に暗躍+札を
                    #   置いていない（_board_void）なら、神社ボード暗躍禁止は空振り：暗躍禁止は
                    #   「今ターン重なった暗躍+カード」しか消せず、既存カウンター（黒猫のループ
                    #   開始時神社+1等）や事件の神社+2は止まらない。犯人冷却（邪気の汚染犯）に
                    #   席を譲る（検死 2026-07-13 seed3：81点の空振りが76.5点の犯人冷却を席から
                    #   追い出し封印敗北。mm札のあるターンは line 858 で従来どおり有効に止める）。
                    if _board_void and tgt == "神社" \
                            and getattr(self, "_shrine_seal_incident", False):
                        return PRIORITY["ポンプ見切り"]
                    # ★通常クロマクの void ボード見切り（B-3）：引き剥がし移動が可能なら空振り降格。
                    if _board_void and tgt in getattr(self, "_relocatable_kuro_void", ()):
                        return PRIORITY["ポンプ見切り"]
                    return 80.0 + view["board_anyaku"][tgt]
                return 4.0
            if card == "不安-1" and kind == "character":
                c = self._alive(view, tgt)
                if not c:
                    return 0.0
                # ★不安0のキャラに mm札が無いのに不安-1は床0で空振り（テスター指摘
                #   2026-07-10：既に不安が積まれているキャラへの予防冷却はOK＝不安≥1 or
                #   mmが今ターン札を伏せた対象なら通常判定へ）。
                if (_np := noop_reason(view, card, tgt, kind, _noop_ctx)) is not None:
                    return _np.score   # B-28：床0＋mm札なし＝空振り（単一チョークポイント）
                # ★危険事件の候補冷却（ルール接地）：効果が敗北条件に直結する事件
                #   （蝶の羽ばたき×未来改変・邪気の汚染×封印）は候補犯人ごと冷やす
                #   （発生条件は「犯人の不安≥臨界」＝不安を削れば必ず止まる）。
                #   ※確定犯人の一般予防（45）より先に判定＝確定して45に落ちる事故を防ぐ。
                th = unrest_threshold_of(tgt)
                if th:  # 臨界0（黒猫）は不安を下げても必ず発生＝冷却は無意味
                    for d, danger in getattr(self, "_incident_danger", {}).items():
                        if d >= view["day"] and tgt in getattr(self, "_culprit_cands", {}).get(d, ()):
                            # ★候補が広い時の照準：mmが今ターン札を伏せた候補（＝不安を
                            #   仕込んでいる公算大）か臨界間際の候補だけ満額。それ以外は半額
                            #   （全候補に満額を配ると同点だらけでrng任せになる実測の教訓）。
                            # ★臨界1の縮退回避：th=1だと不安0でも「th-1=0以上」が常真になり
                            #   全候補が照準扱い＝同点タイを無関係候補が奪う（fs5_guard実測：
                            #   D2犯人サラリーマン(臨界到達済)より男子学生(不安0)が先に冷却された）
                            focused = (tgt in mm_char_now
                                       or c["unrest"] >= max(1, th - 1)
                                       or len(getattr(self, "_culprit_cands", {}).get(d, ())) <= 3)
                            # 同一事件日を別席が冷却済みなら減点（重複より別需要を優先）
                            dup = 8.0 if d in getattr(self, "_cooled_days", ()) else 0.0
                            # ★同日候補内のタイブレーク（最大+2）：臨界に近い候補・mm札の
                            #   乗った候補から冷やす（同点75が不安0の別候補に流れ、臨界到達済みの
                            #   真犯人を放置した実測 fs5_guard D2）。順位を跨がない微小ボーナス。
                            near = (2.0 * min(1.0, c["unrest"] / max(1, th))
                                    + (1.5 if tgt in mm_char_now else 0.0))
                            # 事件当日＝冷やす最後の（D1事件なら唯一の）機会。臨界間際も同格。
                            if d == view["day"] or c["unrest"] >= max(1, th - 1):
                                # ★当日×犯人単独確定＝最後の機会の照準。将来犯人のmm札
                                #   ボーナス(+1.5)に僅差で負けない下駄（BTX_15実測：当日の
                                #   情報屋76.0が翌々日犯人マスコミ76.5に0.5差で席を奪われ、
                                #   邪気の汚染=神社+2が毎ループ発生した）
                                _today_locked = 2.5 if (
                                    d == view["day"]
                                    and getattr(self, "_known_culprits", {}).get(d)
                                    == tgt) else 0.0
                                # ★当日×臨界到達済み（unrest≥th）×危険60+＝冷却が発生を
                                #   止める唯一の減算＝ピン(93)にも席を譲らない（BTX_15実測：
                                #   情報屋 不安3=th3 が冷却されず邪気=神社+2が毎ループ発生。
                                #   th=1はML分離83.5の領分＝th≥2に限定して誤爆を防ぐ）
                                if (d == view["day"] and th >= 2
                                        and c["unrest"] >= th and danger >= 60.0):
                                    _today_locked = 20.0
                                    # 臨界候補が複数なら各自に冷却が要る＝重複減点を免除
                                    # （BTX_12実測：因果の糸で2人が臨界・1人目冷却の−8で
                                    #   真犯人が暗躍禁止96に負け、蝶=即敗北条件が発生）
                                    dup = 0.0
                                return (max(danger, 45.0) if focused
                                        else max(danger * 0.5, 37.0))                                     - dup + near + _today_locked
                            if d - view["day"] <= 1:
                                # ★自己ポンプ犯人の前日冷却（2026-07-08）：犯人がML本人
                                #   なら+2/日 vs カード-1/日＝前日から冷やさないと算術的に
                                #   間に合わない（btx_future実測：D1を実験(75)に取られ
                                #   蝶が8/8発生）。ML疑い0.4以上で実験より上の76に。
                                _mlp = self._belief.role_marginals().get(tgt, {}).get(
                                    "ミスリーダー", 0.0)
                                if _mlp >= 0.55 and danger >= 60.0:
                                    return max(danger * 0.8, 76.0) - dup
                                return (max(danger * 0.6, 30.0) if focused
                                        else max(danger * 0.35, 24.0)) - dup
                # ★予防的な事件対策：犯人が確定している未消化の事件は、脚本家が毎ターン
                #   不安を仕込んでくる前提で先回りして冷やす（臨界間際なら最優先級）。
                for d, culp in self._known_culprits.items():
                    if culp == tgt and d >= view["day"] and th is not None:
                        if c["unrest"] >= th - 1:
                            return 70.0   # あと+1で発生＝今冷やす
                        if d - view["day"] <= 2:
                            return 45.0   # 事件日が近い＝仕込みを打ち消し続ける
                # ★脚本家が今ターン札を伏せたキャラが「あと1で不安臨界」＝その札が不安+1なら
                #   事件が発生する。先回りで不安-1を置いて臨界到達を防ぐ（ユーザー知見 2026-07-07）。
                #   犯人未確定でも、脚本家が札を伏せている＝不安を仕込んでいる可能性が高い。
                if tgt in mm_char_now and th is not None and c["unrest"] == th - 1:
                    return 50.0
                s = 30.0 * self._risk(c)
                if tgt in culprits:
                    s += 5.0
                return s
            # ★SK配達ピン（KP単独・ルール接地の優先逆転）：mmが殺し屋疑い（SK候補）に
            #   札を伏せ、推定KPが1人きりの時——KPの暗躍<2ならキラーは今日KPを殺せない
            #   （殺害条件＝KP暗躍≥2）が、SKなら配達→2人きりで今日殺せる。つまり
            #   「今日ループが終わる線」はSK配達だけ＝移動禁止が暗躍禁止（キラー解釈）に
            #   勝つ。SK/キラーの解釈が割れて sk_strong に届かない実測（5日級FS15：
            #   D1斜め配達でKP毎ループ即死・席は暗躍禁止97が浪費）のため候補0.15で発火。
            # ★移動禁止は「そのキャラに載った今ターンの移動カード」しか打ち消せない＝
            #   実質移動不可キャラ（A.I.＝病院/神社/学校が全て禁止で都市固定）への移動禁止は
            #   絶対に空振り（テスター指摘 2026-07-10）。mmが札を伏せていても動けない。
            if card == "移動禁止" and kind == "character" and (
                    _np := noop_reason(view, card, tgt, kind, _noop_ctx)) is not None:
                return _np.score   # B-28：移動不能への移動禁止＝空振り（単一チョークポイント）
            # ★カルティストを敗北ボードへ運ぶ手の封じ（テスター検死 BTX s3/s18・2026-07-11）：
            #   封印/守るべき場所等の敗北ボードにmmが今ターン暗躍札を置き、かつカルティスト疑いに
            #   mmが移動札を伏せている＝そのカルティストを敗北ボードへ運んで暗躍禁止を無視させる筋
            #   （2×2盤は1移動で全エリアへ行ける）。移動禁止でカルティストの移動を封じてボードへ
            #   来させない＝暗躍禁止→敗北ボードを実効化する二段防御の片翼（両方置いて初めて守れる）。
            #   ★既にカルティストがボードに居るなら移動禁止では剥がせない＝発火しない（別手が要る）。
            if card == "移動禁止" and kind == "character" \
                    and tgt == _cultist_pin_target:
                return 99.0   # 暗躍禁止→敗北ボード(96-101)と並ぶ二段防御の片翼
            if card == "移動禁止" and kind == "character" and tgt in mm_char_now \
                    and tgt in (getattr(self, "_sk_cands", set())
                                | getattr(self, "_sk_strong", set())):
                c0 = self._alive(view, tgt)
                kp_c = self._alive(view, keyperson)
                if (c0 and kp_c and kp_c["area"] != c0["area"]
                        and kp_c["anyaku"] < 2 and c0["anyaku"] < 3
                        and not any(o["alive"] and o.get("area") == kp_c["area"]
                                    and o["name"] != keyperson
                                    for o in view["characters"])):
                    return PRIORITY["SK配達ピン_KP単独"]
            # ★空振り防止（ユーザー指摘 2026-07-12）：移動禁止は「そのキャラに載った今ターンの
            #   移動カード」しか打ち消せない。脚本家が今ターンそのキラーに札を伏せていなければ
            #   （tgt not in mm_char_now）、キラーは今ターン移動しない＝ピンは確実に空振り。
            #   脚本家の配置位置は公開＝札の無い所へのピンは無意味なので発火させない。
            if card == "移動禁止" and kind == "character" and tgt == killer \
                    and tgt in mm_char_now and self._alive(view, killer):
                # ★KPが殺害圏（暗躍≥2）ならキラーの接近そのものを断つ（位置脅威は位置資源で）
                kp_c = self._alive(view, keyperson)
                if kp_c and kp_c["anyaku"] >= 2:
                    return 85.0
                return 50.0
            # ★殺人事件の犯人ピン＝KPの同ターン隣接化を封じる（2026-07-09・FS_0 5d vs gen2
            #   検死＝§5b-1）：殺人事件は「犯人と同エリアの1人を殺す」＝KPと同室なら殺される。
            #   既存のKP退避(a2/115)は「犯人が"今"KPと同室」の現在位置反応型で、犯人がまだ
            #   別エリアの時（＝脚本家が同ターンに犯人をKPのエリアへ移動して隣接化する手）を
            #   先読みできない。犯人を移動禁止でピンすれば隣接化そのものを封じる（犯人を
            #   動かすより堅い＝2×2盤は全エリア1移動で隣接可能）。発火条件を厳しく絞り
            #   過剰発火を防ぐ：KP・犯人とも高確度で特定・当日の致死同エリア事件・犯人が
            #   KPと別エリア（＝1移動で隣接されうる）・KP暗躍<2（暗躍系はキラーピンの領分）。
            # ★空振り防止（同上）：犯人の隣接化は脚本家が犯人に移動札を伏せて初めて起きる＝
            #   tgt in mm_char_now を要求（札の無い犯人へのピンは今ターン空振り）。
            if card == "移動禁止" and kind == "character" and keyperson \
                    and tgt in mm_char_now \
                    and self._known_culprits.get(view["day"]) == tgt \
                    and view["day"] in getattr(self, "_lethal_days", ()) \
                    and self._kp_prob.get(keyperson, 0.0) >= 0.7:
                _kpc = self._alive(view, keyperson)
                _cuc = self._alive(view, tgt)
                _inc_name = next((i.get("name") for i in view.get("incidents", [])
                                  if i.get("day") == view["day"]), None)
                if (_inc_name == "殺人事件" and _kpc and _cuc
                        and _cuc["area"] != _kpc["area"]
                        and _kpc["anyaku"] < 2):
                    return 111.0   # KP退避(115)の直下・キラー暗躍3封じ(110)の直上
            # ★護衛ピン（SK×KP同居の維持防衛）：KPのエリアが「KP＋SK疑い＋護衛役1人」の
            #   3人で保たれている時、脚本家が護衛役に札を伏せた＝引き抜き移動がほぼ確実
            #   （抜かれると2人きり→ターン終了で殺害→即ループ終了）。移動禁止でピンして
            #   3人を維持する（実測：D1注入をD2に斜め移動で引き抜かれて殺された教訓）。
            if card == "移動禁止" and kind == "character":
                for vip in ([keyperson] if keyperson else []) + \
                        sorted(getattr(self, "_friend_guards", ())):
                    if tgt == vip:
                        continue
                    vip_c0 = self._alive(view, vip)
                    if not (vip_c0 and tgt in mm_char_now):
                        continue
                    occ0 = [o["name"] for o in view["characters"]
                            if o["alive"] and o.get("area") == vip_c0["area"]
                            and o["name"] != vip]
                    sk_pool = self._sk_suspects | getattr(self, "_sk_cands", set())
                    if (len(occ0) == 2 and tgt in occ0 and tgt not in sk_pool
                            and any(n in sk_pool for n in occ0 if n != tgt)):
                        vip_te_death = any(
                            e.get("event") == "death" and e.get("name") == vip
                            and e.get("phase") == "turn_end"
                            for e in view.get("history", []))
                        if vip_te_death:
                            return PRIORITY["護衛ピン_実証済"]
                        return PRIORITY["護衛ピン"]
            # ★SK配達阻止：脚本家がSK強疑いに札を伏せた＝標的への移動（配達）の公算大。
            #   移動は同種コピーでは打ち消せない（KB: 10 同種2枚＝1回）＝移動禁止が唯一の対抗。
            #   （random_BTX seed4実測：mmがSKを1人きりのフレンドへ↑↓で配達→ターン終了殺害）
            # ★最終ループ限定：mmのSKへの札は「配達移動」とは限らない（犯人ポンプの不安+1
            #   等）。序盤ループで一律ピンすると冷却の席を奪い邪気の汚染等を素通しする
            #   （btx_seal実測）。負けたら終わりの最終ループだけ、SK配達のリスクを最優先で消す。
            if card == "移動禁止" and kind == "character" \
                    and tgt in getattr(self, "_sk_strong", ()) and tgt in mm_char_now:
                c0 = self._alive(view, tgt)
                if c0 and view["loop"] >= view.get("loops_total", 3):
                    # 1人きりのキャラ（=配達されたら2人きり）が盤上に居るなら緊急度高
                    solo_exists = any(
                        sum(1 for o2 in view["characters"]
                            if o2["alive"] and o2.get("area") == o["area"]) == 1
                        for o in view["characters"]
                        if o["alive"] and o["name"] != tgt)
                    # 72＝事件当日の照準つき冷却(74)より下：SKが犯人候補で不安を仕込まれて
                    # いるだけの可能性があり、冷却の席を奪わない（btx_seal実測の教訓）
                    return PRIORITY["SK配達ピン"] if solo_exists else PRIORITY["SK配達ピン_弱"]
            # ★カルティストの進入ブロック：ゴールボード外に居るカルティスト疑いの移動を封じ、
            #   引き剥がし(c2)と合わせて暗躍禁止の実効を維持する（連れ戻し移動カードを無効化）。
            if card == "移動禁止" and kind == "character" and danger_board \
                    and (tgt in self._cultist_suspects
                         or tgt in getattr(self, "_cultist_cands", ())):
                cc = self._alive(view, tgt)
                if cc and cc["area"] != danger_board:
                    # ★フェリー阻止（最優先級）：脚本家が"今ターン"カルティスト疑いに札を
                    #   伏せた＝ゴールボードへの移動（フェリー）がほぼ確実。ピンで移動を
                    #   打ち消せば、別席の暗躍禁止が実効に戻る＝2席で完封できる
                    #   （btx_seal実測：ピン不在でD1のフェリー暗躍+2が通り即負け確の教訓）。
                    if tgt in mm_char_now:
                        # ★誤読抑制：対象が"今日の事件"の犯人候補で臨界間際なら、mmの札は
                        #   フェリー移動より不安+1の公算が大＝ピンで枠を奪わず冷却に譲る
                        #   （主人公は同一対象に重ねられない＝ピンと冷却は排他。5日級FS17
                        #   実測：ピンが医者の枠を奪い行方不明(学校+1)が毎ループ発生）。
                        _th0 = unrest_threshold_of(tgt)
                        _cc0 = self._alive(view, tgt)
                        # mmの当日供給力＝置き札1＋同エリアのML疑いの能力+1。供給が臨界に
                        # 届く犯人はピンで枠を奪うと冷却が置けない（同一対象は排他）。
                        # 新FS_17実測：不安0・臨界2でも置き札+ML同居の+2でD1に行方不明が
                        # 発生＝旧条件（unrest≥th-1固定）では取りこぼす。
                        _sup0 = 1
                        _cands0 = getattr(self, "_culprit_cands", {}).get(
                            view["day"], ())
                        if _cc0 and set(_cands0) == {tgt}:
                            # ML未特定でも「排除されていない同居者」が居れば悲観的に+1
                            # （新FS_17実測：真ML=刑事がp0.2のまま同居ポンプ＝特定を
                            #   待つと毎ループ発生。ゲートは当日犯人限定なので誤爆は狭い）
                            for _n0, _d0 in self._belief.role_marginals().items():
                                if _n0 == tgt or _d0.get("ミスリーダー", 0) <= 0.05:
                                    continue
                                _oc0 = self._alive(view, _n0)
                                if _oc0 and _oc0["area"] == _cc0["area"]:
                                    _sup0 += 1
                                    break
                        if (_th0 and _cc0 and _cc0["unrest"] >= _th0 - _sup0
                                and tgt in _cands0
                                and view["day"] in getattr(self, "_incident_danger", {})):
                            pass  # 冷却分岐（不安-1）がこの枠を取る
                        elif tgt in self._cultist_suspects:
                            return PRIORITY["フェリーピン"]
                        # 候補(0.25)でも「同ターンにゴールボードへも札」＝フェリー+カードの
                        # コンボがほぼ確定＝冷却(74)より上げる（random_BTX seed4の実測：
                        # ピン不在でD1に+2を通され即負け確）
                        elif danger_board in mm_board_now:
                            return PRIORITY["フェリーピン_コンボ"]
                        else:
                            return PRIORITY["フェリーピン_候補"]
                        # 冷却分岐(pass)はここへ落ちる＝従来どおりピン55/48（mmが札を伏せた対象）。
                        return 55.0 if tgt in self._cultist_suspects else 48.0
                    # ★空振り防止（ユーザー指摘 2026-07-12）：脚本家が今ターンこのカルティストに
                    #   札を伏せていない（tgt not in mm_char_now）＝今ターンは移動しない＝先制ピンは
                    #   空振り。脚本家は主人公より先に配置する（フェイズ2→3）＝主人公は札の位置を見て
                    #   から置ける＝「フェリー戦争で消耗を寄せる」先制ピンは成立しない（mmは既にcommit）。
                    #   フェリーは mm が実際に移動札を伏せた時（上の mm_char_now 分岐）に反応して止める。
                    #   札の無い所へのピンは無意味なので発火させない（他の実効手に席を回す）。
            # ★幻想の読み替え防御：幻想は行動カードを直接セットできず、同エリアのボードに
            #   置いた非暗躍カードの効果を受ける（KB: 30 幻想特性）。幻想がクロマク/カルティスト
            #   疑いでゴールボードに立つ場合、ボード経由の移動で引き剥がすのが唯一の手段
            #   （クロマクのボード汲み上げ＝+1/日は暗躍禁止で止まらない）。
            if card.startswith("移動") and card != "移動禁止" and kind == "board":
                gc = self._alive(view, "幻想")
                _genso_kuro = gc and gc["area"] == tgt and (
                    "幻想" in self._kuromaku_suspects
                    or "幻想" in getattr(self, "_kuromaku_cands", ())
                    or "幻想" in self._cultist_suspects)
                if _genso_kuro and danger_board and tgt == danger_board:
                    # 確信度で段階付け（疑い0.3でも、放置＝毎日+1で確実に負けるなら動かす価値大）
                    return PRIORITY["幻想引き剥がし"] if ("幻想" in self._kuromaku_suspects
                                    or "幻想" in self._cultist_suspects) else PRIORITY["幻想引き剥がし_候補"]
                # ★キャラ暗躍供給への幻想引き剥がし（テスター指摘 2026-07-10）：クロマクは
                #   同エリアのキャラにも暗躍+1できる（40:86）。幻想=クロマクがキラー（暗躍≥2→
                #   放置で4＝主人公死）やKP（暗躍≥1→2で殺害圏）と同室なら、ボード移動で幻想を
                #   その部屋から外せば供給が止まる（暗躍禁止では止まらない）。danger_board 不要。
                if _genso_kuro:
                    _strong = ("幻想" in self._kuromaku_suspects
                               or "幻想" in self._cultist_suspects)
                    for _o in view["characters"]:
                        if not (_o["alive"] and _o.get("area") == gc["area"]):
                            continue
                        if _o["name"] in self._killer_suspects and _o["anyaku"] >= 2:
                            return 84.0 if _strong else 70.0   # 暗躍4＝即死圏を断つ
                        if (_o["name"] == keyperson and _o["anyaku"] >= 1
                                and self._kp_guard >= 50):
                            return 80.0 if _strong else 66.0   # KP殺害圏の供給を断つ
            # ★位置戦術（ルール接地）：
            #   キラーの殺害＝「同エリア＋キーパーソン暗躍2」／クロマクの能力＝「同エリア」。
            #   同エリア要求は移動で破れる。追跡には脚本家もカードを使う＝消耗戦に持ち込める。
            # ★実験モード：犯人候補に不安を載せて事件の発生/不発を観測する
            #   （発生＝eligible で犯人が絞れる・殺人系なら死からさらに情報が出る。
            #     ウイルス脚本なら不安3でパーソンがSK化して殺す＝それ自体が識別情報）。
            #   席分業：p3=実験係が最優先で担当（p1は防御・p2は投資に席を残す）。
            if card == "不安+1" and kind == "character" \
                    and getattr(self, "_experiment", False):
                # ★B-16：未来イベントの犯人候補にのみ実験＝過去/公開確定除外(男子学生 D3除外)を弾く
                in_culp = tgt in getattr(self, "_future_culprits", culprits)
                # ★殺害系事件（殺人事件等）の犯人候補は、負け確ループ以外ではポンプ禁止
                #  （自分の実験がキーパーソン殺害の引き金になった実測の教訓）
                if (in_culp and tgt in getattr(self, "_lethal_culprits", ())
                        and not getattr(self, "_loop_lost", False)):
                    in_culp = False
                # ★敗北トリガー事件（蝶の羽ばたき×未来改変・邪気の汚染×封印＝danger≥70）の
                #   犯人候補はポンプ禁止（loop_lost でも）：ボード仮説で「負け確」に見えても
                #   その仮説が偽なら蝶が真の敗因＝ポンプは自滅、真ならポンプは何も変えない
                #   ＝どちらでも得しない（テスター検死 2026-07-10：神社=2でloop_lost誤認→
                #   実験58が犯人異世界人に不安+1を注ぎ蝶を自分で発生させていた）。
                if in_culp:
                    for _d0, _dn0 in getattr(self, "_incident_danger", {}).items():
                        if _dn0 >= 70.0 and tgt in getattr(
                                self, "_culprit_by_day", {}).get(_d0, ()):
                            in_culp = False
                            break
                in_virus = (tgt in getattr(self, "_virus_test_targets", ())
                            and not (tgt in getattr(self, "_lethal_culprits", ())
                                     and not getattr(self, "_loop_lost", False)))
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
                # ★B-28（単一チョークポイント）：移動の空振り/自滅をここで一括判定する。
                #   G4 行き先が禁止＝移動不成立（空振り・テスター指摘 2026-07-10。A.I.は全方向禁止）／
                #   G5 KPを今日の致死事件の kill zone へ送る＝自滅（検死 FS s9・2026-07-11。penalty は
                #      KP限定＝クロマク等の移動は引き剥がし等の正当用途がある）／
                #   G6 確定クロマクをキラー疑い＋KP/フレンド同居エリアへ送り込む＝自滅（B-22・
                #      FS seed11実測。引き離す移動は dest に victim が居ない＝不発火）。
                #   判定の実体は agents/card_effect.noop_reason（defense_plan の break 生成も同じ
                #   述語を見る＝PLAN_HOT がこのゲートを上書きする事故の構造封鎖）。
                if (_np := noop_reason(view, card, tgt, kind, _noop_ctx)) is not None:
                    return _np.score
                # ★B-29（2026-07-17・削除済み）：かつてここに「黒猫を確定SKと2人きりにして
                #   始末させる」手筋（黒猫SK処分・92.0）があったが、その正当化
                #   「黒猫が死ねば以降のループは噂+1だけでは神社2に届かない」は**規則誤り**：
                #     ・黒猫の神社+1は**ループ開始時**に置かれる（sim/state.prepare_loop＝
                #       forced_loop_start_anyaku）＝殺した時点で当ループ分は既に盤面にある。
                #     ・死亡は**ループでリセット**＝次ループは黒猫が復活して再び+1が入る。
                #   ＝殺しても供給は当ループも次ループ以降も一切止まらない（実測で確認）。
                #   同ファイル 1405 のSKテスト側は既に正しく「※黒猫を殺しても無意味（毎ループ復活・
                #   強制暗躍はループ開始時）＝価値は死んだ時に出る情報だけ」と記していた＝内部矛盾を解消。
                #   残る情報価値（生存ペアの否定形）は下のSKテスト（gini駆動・情報限定）の領分。
                # ※占有計算は自席の予定移動込み（席間協調＝2席がかりのペア構築）
                planned = getattr(self, "_planned_moves", {})

                def _eff_area(name: str) -> str | None:
                    if name in planned:
                        return planned[name]
                    cc2 = self._alive(view, name)
                    return cc2["area"] if cc2 else None

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
                    # ★役職テスト給餌：確定SKと「役職不明（gini高）」キャラを2人きりに。
                    #   SK殺害は【強制】＝必ず死ぬ＝ループ終了時のフレンド公開/否定形で
                    #   フレンド/ミスリーダーの対称性が割れる（最後の戦いの弾になる）。
                    #   ※黒猫を殺しても無意味（毎ループ復活・強制暗躍はループ開始時＝実測の教訓）。
                    #   価値は「死んだ時に出る情報」＝役職の不確かさに限る。
                    feed_sk = set(getattr(self, "_sk_strong", ()))
                    if test_set or feed_sk:
                        occupants = [o["name"] for o in view["characters"]
                                     if o["alive"] and o["name"] != tgt
                                     and _eff_area(o["name"]) == dest]
                        if len(occupants) == 1:
                            other = occupants[0]
                            # ★犠牲者の安全確認：SKテストで死ぬのは非SK側。その犠牲者が
                            #   フレンド/KPだった場合、死＝ループ喪失（フレンド死亡はループ
                            #   終了時敗北）。まだ生きているループでは、犠牲者になりうる側が
                            #   フレンド外とほぼ確定している時だけテストする
                            #   （実測 random_BTX seed4：SK探索テストが自らSKをフレンドへ
                            #   配達して殺させ、防衛成功盤面を敗北にした教訓）。
                            marg_fr = self._belief.role_marginals()

                            def _safe_victim(v: str) -> bool:
                                # 負け確ループ／最終ループ以外＝死もタダの情報源（1ループの
                                # 損で情報を買う）。★最終ループ（負けたら即ゲーム敗北）だけは
                                # フレンド/KPかもしれない犠牲を出さない（実測 random_BTX seed4：
                                # 最終ループでSKテストがフレンドを殺し防衛成功盤面を敗北にした。
                                # 逆に全ループでテスト禁止にしたらbasic/btx_future全敗＝
                                # テストは防御の情報源として load-bearing）。
                                if getattr(self, "_loop_lost", False):
                                    return True
                                # ★推定KP/KP候補は序盤ループでも絶対に囮にしない：
                                #   KP死＝即ループ喪失＝脚本家の勝ち筋そのもの＝情報の
                                #   対価にならない（BTX_8実測：SKテストがKPを囮にして
                                #   毎ループ献上した）
                                if v == keyperson or v in getattr(self, "_kp_suspects", ()):
                                    return False
                                if getattr(self, "_kp_doomed", False):
                                    return True   # KP防衛が一度も成功していない＝FB勝負
                                if view["loop"] < view.get("loops_total", 3):
                                    return True
                                return marg_fr.get(v, {}).get("フレンド", 0.0) < 0.1

                            if other in test_set and _safe_victim(tgt):
                                return PRIORITY["SKテスト"]   # SK候補の探索テスト（自分が囮）
                            if tgt in test_set and _safe_victim(other):
                                return PRIORITY["SKテスト"]   # SK候補の探索テスト
                            if ((other in feed_sk
                                 and self._gini.get(tgt, 0) > 0.15
                                 and _safe_victim(tgt))
                                    or (tgt in feed_sk
                                        and self._gini.get(other, 0) > 0.15
                                        and _safe_victim(other))):
                                return PRIORITY["SKテスト"]   # 役職テスト給餌
                        # ★席間協調の前段：候補の居る3人部屋から自分が抜けて
                        #   「候補＋1人」のペアを残す（ターン終了のSK解決がテストになる）
                        src_occ = [o["name"] for o in view["characters"]
                                   if o["alive"] and o["name"] != tgt
                                   and _eff_area(o["name"]) == c["area"]]
                        if (tgt not in test_set and len(src_occ) == 2
                                and sum(1 for n in src_occ if n in test_set) == 1):
                            left = [n for n in src_occ if n not in test_set][0]
                            if left not in getattr(self, "_kp_suspects", ()):
                                return 58.0
                # ★第三者注入（SK×KPの初期同居対策）：キーパーソンがSK疑いと"2人きり"の
                #   エリアに居る＝ターン終了で殺害→即ループ終了。KP自身の退避は暗躍禁止
                #   （契約Yの暗躍+2ブロック）と同一ターゲットで競合するため、第三者を
                #   送り込んで3人にする方が両立できる（SKの殺害条件＝2人きりを崩す）。
                #   sk_at_dest の安全判定より先に置く（3人になるなら送り込んでも殺されない）。
                if c and dest and dest not in forbidden_of(tgt) \
                        and not getattr(self, "_vip_injected", False):
                    # 護衛対象＝推定KP＋フレンド疑い（SK殺害＝ループ喪失に直結）
                    # ★注入はターン1席まで：全キャラ移動に103が付いて3席が殺到し
                    #   冷却が飢餓した実測（BTX_16・公開フレンド護衛の再有効化直後）
                    for vip in ([keyperson] if keyperson else []) + \
                            sorted(getattr(self, "_friend_guards", ())):
                        vip_c0 = self._alive(view, vip)
                        if not (vip_c0 and dest == vip_c0["area"] and tgt != vip
                                and tgt not in self._sk_suspects):
                            continue
                        occ0 = [o["name"] for o in view["characters"]
                                if o["alive"] and o.get("area") == vip_c0["area"]
                                and o["name"] != vip]
                        # 過去ループでこの護衛対象がターン終了に死んでいる＝この2人きりは
                        # 実証済みの死の配置。疑い確率が低くても最優先で崩す。
                        vip_te_death = any(
                            e.get("event") == "death" and e.get("name") == vip
                            and e.get("phase") == "turn_end"
                            for e in view.get("history", []))
                        if (len(occ0) == 1
                                and occ0[0] in (self._sk_suspects
                                                | getattr(self, "_sk_cands", set()))):
                            if vip_te_death:
                                return PRIORITY["VIP注入_実証済"]
                            return (PRIORITY["VIP注入"] if occ0[0] in self._sk_suspects
                                    else PRIORITY["VIP注入_候補"])
                # ★移動先の安全判定：シリアルキラー疑いのいるエリアへ味方候補を送らない
                #   （2人きり→ターン終了で殺される。実測の教訓：クロマク隔離が病院のSKへ直行した）
                sk_at_dest = any((sc := self._alive(view, s)) and sc["area"] == dest
                                 for s in self._sk_suspects if s != tgt)
                # ★敵駒（クロマク/カルティスト確信）はSK圏へ送ってよい：SKの殺害は【強制】＝
                #   その供給源は**当ループの残りターン**止まる（クロマクの供給は mm能力フェイズの
                #   毎ターン＋1＝殺せば以降の日は注げない）。
                #   ★B-29（2026-07-17）：旧コメントは「永久に止まる」と書いていたが規則誤り＝
                #   死亡はループでリセット＝次ループは復活する。価値は**当ループ内**に限る
                #   （黒猫は供給がループ開始時の1回＝殺しても当ループ分は既に置かれ済み＝
                #   価値ゼロ＝黒猫SK処分は削除済み。クロマク/カルティストは毎ターン供給＝別物）。
                #   shrine v2実測：唯一の引き剥がし先にSKが居て安全弁が防衛を封殺した。
                if tgt in self._kuromaku_suspects or tgt in self._cultist_suspects:
                    sk_at_dest = False
                # ★出発地の安全判定：この移動で出発地が「SK疑い＋1人」の2人きりになるなら
                #   引き剥がし系の高得点手でも撃たない（クロマク引き剥がしがSKとフレンドの
                #   2人きりを作り、ターン終了殺害→フレンド死亡敗北した実測 random_BTX seed4）。
                _src_left = [o["name"] for o in view["characters"]
                             if o["alive"] and o["name"] != tgt
                             and o.get("area") == (c["area"] if c else None)]
                leaves_deadly_pair = (
                    len(_src_left) == 2
                    and any(n in self._sk_suspects for n in _src_left)
                    and not all(n in self._sk_suspects for n in _src_left))
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
                        # ★退避先の安全判定（vs CEM gen2実測・basic L3D2）：殺人事件の
                        #   犯人から逃げた先がキラーの部屋では自殺行為（KP暗躍≥2＋同エリア
                        #   ＝ターン終了で殺害）。上の(a)分岐には安全判定があるのに(a2)に
                        #   無かった＝←→と↑↓が同点タイでキラーの部屋へ逃げた。
                        #   KPに暗躍が乗っている/mmがKPに札を伏せた（+1で殺害圏）時は、
                        #   キラー疑いの居る退避先を選ばない（暗躍禁止→KP等に席を譲る）。
                        _killer_at_dest = any(
                            (kc := self._alive(view, k)) and kc["area"] == dest
                            for k in self._killer_suspects if k != tgt)
                        _kp_anyaku_risk = c["anyaku"] >= 1 or tgt in mm_char_now
                        culp_today = self._known_culprits.get(view["day"])
                        cu = self._alive(view, culp_today) if culp_today else None
                        if cu and cu["area"] == c["area"] and dest != cu["area"]                                 and not (_killer_at_dest and _kp_anyaku_risk):
                            return 115.0
                        # 犯人が未確定でも、殺害系事件の日で候補が同エリアなら退避
                        # （殺人事件は「犯人と同エリアの1人を殺す」＝KPが的にされる）
                        if view["day"] in getattr(self, "_lethal_days", ()):
                            cands_today = getattr(self, "_culprit_cands", {}).get(view["day"], set())
                            here = any((cc := self._alive(view, cn)) and cc["area"] == c["area"]
                                       for cn in cands_today if cn != tgt)
                            away = any((cc := self._alive(view, cn)) and cc["area"] == dest
                                       for cn in cands_today if cn != tgt)
                            if here and not away                                     and not (_killer_at_dest and _kp_anyaku_risk):
                                return 100.0
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
                        if victims_here and not victims_dest and not leaves_deadly_pair:
                            return PRIORITY["クロマク隔離"]
                    # (c2) ★カルティスト引き剥がし：ゴールボード上のカルティスト疑いは
                    #      暗躍禁止を無効化する（行動解決の同エリア/自ボード無視）。
                    #      移動でボードから外せば暗躍禁止が実効に戻る。
                    #      ※(c)〜(c3b)共通：出発地に「SK疑い＋1人」を残す移動は撃たない
                    #      （SKターン終了殺害でフレンド死亡敗北した実測 random_BTX seed4）。
                    if tgt in self._cultist_suspects and danger_board \
                            and c["area"] == danger_board and dest != danger_board \
                            and not leaves_deadly_pair:
                        return PRIORITY["カルティスト剥がし"]
                    # (c2b) ★候補の引き剥がし＝実験を兼ねる：すり抜けが観測されている時、
                    #      候補（P≥0.25）を1人ずつ外す。外しても通る＝残りが候補、
                    #      通らなくなる＝そいつがカルティスト。どちらでも絞れて防御にもなる。
                    if tgt in getattr(self, "_cultist_cands", ()) and danger_board \
                            and c["area"] == danger_board and dest != danger_board \
                            and not leaves_deadly_pair:
                        return PRIORITY["カルティスト剥がし_候補"]
                    # (c3) ★クロマクのボード汲み上げ断ち：クロマクの能力は「自分の立つボード」
                    #      にも暗躍を置ける。ゴールボードに立つクロマク疑いを移動で外せば、
                    #      能力の注ぎ先がゴールから逸れる（能力自体は止められないが的を変える）。
                    if tgt in self._kuromaku_suspects and danger_board \
                            and c["area"] == danger_board and dest != danger_board \
                            and not leaves_deadly_pair:
                        return PRIORITY["クロマク剥がし"]
                    # (c3b) ★候補の引き剥がし＝実験兼防御：候補（P≥0.3）を1人ずつ外す。
                    #      外しても能力がゴールに届く＝残りがクロマク、届かなくなる＝そいつ。
                    #      （shrine型：候補2人が両方ゴールボード初期でP=0.5ずつ→0.7に届かず
                    #        隔離が沈黙して能力+1/turnが素通りした実測の教訓）
                    if tgt in getattr(self, "_kuromaku_cands", ()) and danger_board \
                            and c["area"] == danger_board and dest != danger_board \
                            and not leaves_deadly_pair:
                        return PRIORITY["クロマク剥がし_候補"]
                    # ★危険犯人のML分離（2026-07-09・5日級FS_6実測）：ミスリーダーの
                    #   不安+1能力は同エリア限定＝ボード供給事件（行方不明/邪気）の犯人と
                    #   分離すればポンプが断てる。カード冷却は解決フェイズ＝その後のML能力
                    #   フェイズ+1で臨界到達する低臨界犯人はこれでしか守れない。
                    #   犯人=ML本人は分離不能＝対象外。殺人系に広げると席経済を壊す実測。
                    _ml_name, _ml_p = self._belief.most_likely_role("ミスリーダー")
                    # 0.4→0.55（2026-07-09・A1後世界のworktree隔離4面実測）：0.4だと
                    # コイントス級（2択0.5前後）のML推定で分離席を張って浪費する。
                    # 0.5〜0.65はプラトー（全て122/59/122/48）＝0.55が頑健な中央。
                    # gen3-3dで+2・全面非退行（off 120→122）。belief-v2でも同傾向。
                    # ★B-52（2026-07-24・fs5_guard s0-s9 検死）：kill事件（遠隔殺人/殺人事件/
                    #   病院の事件）が KP を殺す時の ML 分離。既存のボード供給用 ML分離は
                    #   _feed_days（行方不明/邪気）限定＋しきい値（ML≥0.55・danger≥60）が
                    #   kill事件に合わず発火しなかった（fs5_guard は ML推定0.5・danger55 で二重に弾かれた）。
                    #   機序は同型＝「同エリアMLの+1で犯人が臨界に残り、1枚の冷却では止まらない」＝
                    #   find_defenses が示した必須の対（不安-1＋移動→ML）を採点に載せる。専用の
                    #   自前しきい値（掃引で較正）＝ボード供給路のチューニングは触らない。
                    _b52 = self._b52_kill_ml_separation(o, view, _ml_name, _ml_p)
                    if _b52 is not None:
                        return _b52
                    if _ml_name and _ml_p >= 0.55:
                        _mlc = self._alive(view, _ml_name)
                        _feed_days = {i["day"] for i in view.get("incidents", [])
                                      if i.get("name") in ("行方不明", "邪気の汚染")}
                        for _d3, _dg3 in getattr(self, "_incident_danger", {}).items():
                            # ★当日は原則冷却の領分：分離は置かれ済みの不安+1を止められない
                            #   （同一対象の枠を分離が奪い当日冷却を弾いた実測 BTX_16）。
                            #   ★例外＝臨界1の犯人：冷却で床0にしてもML能力+1（冷却より後の
                            #   フェイズ）で臨界到達＝冷却では算術的に止まらない→当日でも
                            #   分離が唯一の防御（FS_6実測：学者th=1×ML同席で毎ループ発生）。
                            if _d3 not in _feed_days or _dg3 < 60.0:
                                continue
                            _sameday_th1 = (_d3 == view["day"] and any(
                                (unrest_threshold_of(_c5) or 9) <= 1
                                for _c5 in getattr(self, "_culprit_cands",
                                                   {}).get(_d3, ())))
                            # ★当日×臨界到達ちょうど（unrest==th相当）：冷却-1しても
                            #   ML+1で臨界に戻る＝冷却と分離の両輪が必須（BTX_15実測：
                            #   情報屋 不安3=th3）。
                            _sameday_crit = (_d3 == view["day"] and any(
                                (_a5 := self._alive(view, _c5)) is not None
                                and (unrest_threshold_of(_c5) or 99) >= 2
                                and _a5["unrest"] >= (unrest_threshold_of(_c5) or 99)
                                and _a5["unrest"] - 1 < (unrest_threshold_of(_c5) or 99)
                                for _c5 in getattr(self, "_culprit_cands",
                                                   {}).get(_d3, ())))
                            if not (1 <= _d3 - view["day"] <= 2 or _sameday_th1
                                    or _sameday_crit):
                                continue
                            for _cn3 in getattr(self, "_culprit_cands", {}).get(_d3, ()):
                                if _cn3 == _ml_name:
                                    continue
                                _cc4 = self._alive(view, _cn3)
                                if not (_cc4 and _mlc and _cc4["area"] == _mlc["area"]):
                                    continue
                                _th4 = unrest_threshold_of(_cn3)
                                if _th4 is not None and _cc4["unrest"] - 1 >= _th4:
                                    continue   # 冷却-1でも臨界以上＝分離では止まらない
                                # unrest==th ちょうどは「冷却で下げ・分離で戻させない」両輪の対象
                                if (tgt == _cn3 and dest != _mlc["area"]) or                                         (tgt == _ml_name and dest != _cc4["area"]):
                                    # 当日th<=1＝冷却で算術的に止まらない唯一の防御＝投資(81)より上
                                    # 当日crit＝冷却(当日ブースト≈96)と両輪＝ピン(93)の直上
                                    if _sameday_crit:
                                        return 93.5
                                    return 83.5 if _sameday_th1 else PRIORITY["危険犯人_ML分離"]
                    # ★冷却役の同行（2026-07-08）：解禁済みの不安除去能力は同エリア限定
                    #   （学生の不安除去等）＝危険事件の犯人と同エリアに保たないと毎ターンの
                    #   追加冷却（自己ポンプ対抗）が使えない。冷却役を犯人のエリアへ寄せる
                    #   （btx_future実測：男子学生を解禁したのに巫女と別エリアで能力が死蔵）。
                    for _d2, _dg in getattr(self, "_incident_danger", {}).items():
                        if _dg < 60.0 or _d2 < view["day"]:
                            continue
                        for _cn in getattr(self, "_culprit_cands", {}).get(_d2, ()):
                            _cc3 = self._alive(view, _cn)
                            if not _cc3 or _cc3["area"] == (c["area"] if c else None):
                                pass
                            if not _cc3:
                                continue
                            if tgt == _cn:
                                continue
                            # tgt が解禁済み冷却役で、行き先が犯人のエリア
                            if dest == _cc3["area"] and any(
                                    "不安" in _a4["name"] and "除去" in _a4["name"]
                                    and c["goodwill"] >= _a4["hearts"]
                                    and self._ability_value(tgt, _a4["name"], _cn,
                                                            view) >= 45.0
                                    for _a4 in goodwill_abilities_of(tgt) or []):
                                return PRIORITY["冷却役同行"]
                    # ★脚本家が札を伏せたキャラ（不安+1かもしれない）を不安低減キャラ
                    #   （医者/アイドル/ナース）のエリアへ寄せ、次の主人公能力フェイズで冷やせる
                    #   ようにする（ユーザー知見 2026-07-07）。KP/キラー疑いは専用の位置戦術が
                    #   あるので除外。低優先（危険ボード防衛やSK処理が優先）。
                    if tgt in mm_char_now and tgt != keyperson \
                            and tgt not in self._killer_suspects \
                            and any((cl := self._alive(view, n)) and cl["area"] == dest
                                    for n in ("医者", "アイドル", "ナース")):
                        return 42.0
            if card in ("友好+1", "友好+2") and kind == "board":
                # ★幻想の読み替え投資：幻想＝TT疑いには友好+を直接置けない（被セット不可）。
                #   幻想のいるボードに置けば幻想が効果を受ける（KB: 30 幻想特性）＝TTガード可能。
                gc = self._alive(view, "幻想")
                if gc and gc["area"] == tgt and "幻想" in getattr(self, "_tt_guards", ()) \
                        and gc["goodwill"] < 3:
                    rank = self._tt_guards.index("幻想")
                    base = (PRIORITY["TT投資+2"] if card == "友好+2" else PRIORITY["TT投資+1"]) - rank * 5.0
                    if view["day"] >= view.get("days_per_loop", 99):
                        step = 2 if card == "友好+2" else 1
                        if gc["goodwill"] + step >= 3:
                            return PRIORITY["TT仕上げ"] - rank * 3.0
                        return base + 8.0
                    return base
            if card in ("友好+1", "友好+2") and kind == "character":
                # ★TT任意敗北の封じ（ルール接地）：TT疑いの友好を3以上に保てば最終日の
                #   任意敗北を宣言できない（50:128）。TTへの友好+は【強制】友好禁止無視
                #   （KB: 50）＝脚本家に止める手が無い、確実に通る防御。
                if tgt in getattr(self, "_tt_guards", ()):
                    cc = self._alive(view, tgt)
                    if cc and cc["goodwill"] < 3:
                        # TT任意敗北＝確実なループ喪失（守り切れば即ゲーム勝利もある）。
                        # 事件冷却（≤68）・実験モードの役職テスト給餌（75）より優先。
                        # 候補順位で段差（席が2枚あれば両候補に分散される）。
                        rank = self._tt_guards.index(tgt)
                        base = (PRIORITY["TT投資+2"] if card == "友好+2" else PRIORITY["TT投資+1"]) - rank * 5.0
                        # ★最終日ブースト：宣言判定はこのターン終了時＝今が最後の機会。
                        #   +2で3に届く候補は最優先で仕上げる（1で+2、2で+1でも届く）。
                        if view["day"] >= view.get("days_per_loop", 99):
                            step = 2 if card == "友好+2" else 1
                            if cc["goodwill"] + step >= 3:
                                return PRIORITY["TT仕上げ"] - rank * 3.0
                            return base + 8.0  # 届かなくても宣言側の読みを圧迫
                        return base
                # ★危険事件ガード投資（2026-07-08）：敗北条件に直結する危険事件
                #   （蝶・邪気・流布TT等）の犯人を毎ターン冷やせる能力（学生の不安除去♡2
                #   等）の解禁は、自己ポンプ犯人（ML本人）への唯一の対抗＝カード冷却
                #   （-1/日）だけでは +2/日 に必ず負ける（btx_future実測：蝶が8/8発生）。
                #   価値は _ability_value がルール接地済み（危険事件犯人で45〜72）＝
                #   それが高い未解禁キャラへの友好+を高優先で通す。
                if getattr(self, "_incident_danger", None) \
                        and not getattr(self, "_cooler_invested", False):
                    cc2 = self._alive(view, tgt)
                    # 拒否疑い（友好無視系役職の疑いが濃い）には投資しない：解禁しても
                    # 脚本家が拒否できる（医者=カルティスト疑いへ投資して無駄だった実測）
                    _marg2 = self._belief.role_marginals().get(tgt, {})
                    _refusable_p = sum(_marg2.get(r, 0.0) for r in
                                       ("カルティスト", "クロマク", "キラー", "ウィッチ"))
                    # ★1人で十分：解禁済みの冷却役が既に居るなら追加投資しない
                    _have_cooler = any(
                        (oc := self._alive(view, n2)) and any(
                            "不安" in a2["name"] and "除去" in a2["name"]
                            and oc["goodwill"] >= a2["hearts"]
                            for a2 in goodwill_abilities_of(n2) or [])
                        and self._ability_value(n2, "不安除去", None, view) >= 45.0
                        for n2 in self._belief.cast if n2 != tgt)
                    if cc2 and _refusable_p < 0.4 and not _have_cooler:
                        for _ab in goodwill_abilities_of(tgt) or []:
                            if "不安" not in _ab["name"] or "除去" not in _ab["name"]:
                                continue
                            if cc2["goodwill"] >= _ab["hearts"]:
                                continue   # 解禁済み
                            # ★解禁日算術（FS_6実測）：+2/ターン×1席で解禁できる日が
                            #   守りたい事件の日に間に合わないなら投資しない（アイドル♡3は
                            #   D3解禁＝D2の行方不明に無力なのに2日分の席を吸い、冷却/分離が
                            #   飢餓した）。間に合う事件が1つでもあれば投資する。
                            _need = _ab["hearts"] - cc2["goodwill"]
                            _unlock = view["day"] + max(0, -(-_need // 2))
                            _hot = [_du for _du, _dgu in getattr(
                                        self, "_incident_danger", {}).items()
                                    if _dgu >= 60.0 and _du >= view["day"]]
                            # ★「最初に燃える日」に間に合う投資だけ：遠い事件を口実に
                            #   目前の事件の防衛席を吸う投資を防ぐ（FS_6実測：D5の殺人を
                            #   口実にアイドル♡3へD1-D2投資し、D2の行方不明が素通り）
                            if not _hot or _unlock > min(_hot):
                                continue
                            if self._ability_value(tgt, _ab["name"], None, view) >= 45.0:
                                return (PRIORITY["冷却役投資+2"] if card == "友好+2"
                                        else PRIORITY["冷却役投資+1"])
                # ★TT公開テスト：脚本家が今ターン札を伏せたTT候補へ友好+1をぶつける。
                #   札が友好禁止なら「通る＝TT確定／止まる＝TT除外」の確定情報（belief消費）。
                #   友好禁止でなくても+1は普通に乗る＝損しない実験。過去に友好禁止を
                #   当てられた相手なら再度来る公算が高い＝優先度アップ。
                if card == "友好+1" and getattr(self, "_tt_guards", None) \
                        and tgt in mm_char_now:
                    cc = self._alive(view, tgt)
                    tt_p_tgt = self._belief.role_marginals().get(tgt, {}).get(
                        "タイムトラベラー", 0.0)
                    if cc and tt_p_tgt > 0.05:
                        return PRIORITY["TTテスト_友好禁止歴"] if tgt in getattr(self, "_gwban_hist", ()) else PRIORITY["TTテスト"]
                # ★脚本家が対象に札を伏せている＝友好禁止の可能性（ユーザー知見 2026-07-07）。
                #   1/Lの友好+2を無駄にしないよう避ける（友好+1や別対象へ回す）。TTへの友好+は
                #   友好禁止を無視するので上のTT分岐で別扱い＝ここには来ない。
                if card == "友好+2" and tgt in mm_char_now:
                    return 5.0
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
                # ★B-4a 過剰量ペナルティ：最良能力の解禁に残り1ハートで足りるなら、1/Lで希少な
                #   友好+2は1枚無駄（+1で解禁・+2は1点分こぼれる）＝+1に劣後させる（+2の val 加点を
                #   剥がし更に減点）。次の能力へ積む含みは +1 と大差なく、希少札の温存を優先。
                need = self._invest_need.get(tgt, 99)
                if card == "友好+2" and need <= 1:
                    val = -2.0
                score = 6.0 + 20.0 * (inv / mx) + val
                # ★B-4b/c 即時対象タイブレーク：最良能力が今この盤面で発動対象を持つ投資先を
                #   僅かに優先（例：女子学生「学生の不安除去」は不安1の学生が居る＝即有用／
                #   ナース「不安臨界以上の不安除去」は臨界到達キャラ不在なら即時対象ゼロ）。
                #   magnitude は動かさず順位内の同点だけを崩す（危険事件の前倒し投資は不変）。
                if not self._invest_has_tgt.get(tgt, True):
                    score -= 3.0
                # ★実験モードの席分業：p2=投資係（能力＝開示・殺害・蘇生も情報源）。
                #   全席一律に足すと事件誘発（ウイルス脚本では死＝最良の情報源）を
                #   3席とも締め出す（+28で実測退行）＝1席だけ投資を最優先させる。
                if getattr(self, "_experiment", False) and view.get("seat") == "p2":
                    score += 30.0
                # ★混合戦略：友好禁止は1枚/ターンしか無いので、上位2ターゲットを日替わりで
                #   主役交代させ、ブロックを空振りさせる（進捗が読まれても集中先が読めない）。
                #   ★即時対象ゼロの能力は混合戦略の主役にしない（B-4c）：友好禁止を空振り
                #   させる主役交代は「今解禁したら使える能力」を押す時だけ意味がある。
                top2 = sorted(self._invest, key=self._invest.get, reverse=True)[:2]
                if len(top2) == 2 and tgt in top2 \
                        and self._invest_has_tgt.get(tgt, True):
                    primary = top2[(view["loop"] + view["day"]) % 2]
                    if tgt == primary:
                        score += 8.0
                return score
            return 1.0

        def score(o: dict) -> float:
            # ★防御プランナー加点：負け筋を最安で折る手として選ばれた option を底上げ。
            #   既存の高優先（早期return）はそのまま・fall-through/中優先だけが押される。
            #   致命脅威（実在度≥PLAN_HOT_P）の折り手はホット加点＝冷却より優先させる。
            s = _base_score(o)
            key = (o["card"], o["target"], o.get("target_kind"))
            if self.PLAN_BONUS and key in self._plan_recs:
                heat = self._plan_recs[key]
                s += self.PLAN_HOT if heat >= self.PLAN_HOT_P else self.PLAN_BONUS
            # ★B-45：L1D1 定石レイヤ（ユーザー手練れ知見・loop==1&&day==1 限定）。情報ゼロで
            #   採点器が最も弱い場面に手練れ事前知識をハード化（B-37先例と同型）。
            #   ★【+2配分の優先表】＝①定石3対象（当日発動可の不安除去）→②定石5対象（情報系）。
            #   ★**下限（floor）として当てる**＝通常採点が定石点より高い対象を**減点しない**
            #   （旧実装は絶対値で置き換え＝実測 random_FS#9 で 友好+2→巫女 36→28 に**下げて**
            #   いた＝定石を実装したつもりで逆効果だった。probe で発見・2026-07-22）。
            #   ★定石1/2（ボード伏せ札→暗躍禁止 96点級）は上位＝この層は暗躍禁止の席を奪わない。
            if view.get("loop") == 1 and view.get("day") == 1:
                if o["card"] == "友好+2":
                    _bk = self._opening_plus2_rank(o.get("target"), view)
                    if _bk is not None:
                        s = max(s, _bk)
                else:
                    # ★定石3後半＝準備移動（Step 2c）。同じく floor＝通常採点を下げない。
                    _pm = self._opening_prep_move(o, view)
                    if _pm is not None:
                        s = max(s, _pm)
                    # ★定石6＝散開移動（Step 2d）。定石は**列挙順＝優先順位**なので定石6は最下位＝
                    #   同じ席に定石3（+2配分）や定石3後半（準備移動）の候補が在るなら**譲る**。
                    #   点(47)自体は上位定石(34/35)より高いが、それは「投機的冷却(38〜47)に勝つ」
                    #   ためであり、上位定石と競らせるためではない＝順序は下の弁で保証する
                    #   （現コーパスでは同席競合は実測0席＝弁は将来のための不変条件）。
                    elif not self._opening_higher_present(options, view):
                        _sm = self._opening_spread_move(o, view)
                        if _sm is not None:
                            s = max(s, _sm)
            return s

        # ★2段化（過剰需要ターン限定）：72点以上の需要が4対象以上ある＝3席で賄えない
        #   ターンだけ、先頭席が3席分を一括計画して取捨を調停する（貪欲比+6点ゲート）。
        #   通常ターンは旧来の逐次貪欲そのまま＝挙動ドリフトを構造的にゼロにする
        #   （計画常時適用はナイフエッジseedを無差別に揺らした実測）。
        if self._turn_plan is None:
            # ★VIPリスクゲート（P4・2026-07-09）：VIPがSK疑いと同室で部屋が薄い
            #   （≤3人＝mmが数手で2人きりに絞れる）＝多ターンSK位置エンジニアリングの
            #   標的。この時だけ計画を起動し、退避/ピン/注入の「JOINT救済」を席割当の
            #   目的関数で調停する（P3が per-card 点で失敗＝ナイフエッジ席を奪った教訓：
            #   救済は組全体で評価し、押し出す需要より価値が高い時だけ採る）。
            _vip_risk = self._vip_sk_risk(view)
            if _vip_risk:
                self._turn_plan = self._plan_turn(view, options, score,
                                                  vip_risk=_vip_risk)
            elif not self.PLAN_ENABLED:
                self._turn_plan = []
            else:
                _scored_all = [(score(o), o) for o in options]
                _strong_tgts = {(o["target"], o.get("target_kind"))
                                for s, o in _scored_all if s >= 72.0}
                if len(_strong_tgts) >= 4:
                    self._turn_plan = self._plan_turn(view, options, score)
                else:
                    self._turn_plan = []   # このターンは計画なし＝旧挙動
        best = None
        if self._turn_plan:
            intent = self._turn_plan.pop(0)
            key = (intent["card"], intent["target"], intent.get("target_kind"))
            best = next((o for o in options
                         if (o["card"], o["target"], o.get("target_kind")) == key), None)
        if best is None:
            best = max(options, key=score)
        if best["card"] == "暗躍禁止":
            self._kinshi_used = True
        if best["card"] == "移動禁止":
            self._pins_spent = getattr(self, "_pins_spent", 0) + 1
        # 席間協調：VIP注入はターン1席まで（選択後にフラグ。全キャラ移動に103が付いて
        # 3席が注入に殺到し冷却が飢餓した実測 BTX_16）
        if best["card"].startswith("移動") and best.get("target_kind") == "character":
            _bt2 = self._alive(view, best["target"])
            _dest2 = _move_dest(_bt2["area"] if _bt2 else None, best["card"])
            if _dest2 is not None:
                for _vip in ([self._keyperson] if self._keyperson else []) + \
                        sorted(getattr(self, "_friend_guards", ())):
                    _vc2 = self._alive(view, _vip)
                    if _vc2 and _vc2["area"] == _dest2 and best["target"] != _vip:
                        self._vip_injected = True
                        break
        # 席間協調：冷却役投資はターン1席まで（複数席が別々の冷却役へ投資して
        # 冷却カードの席まで食い潰した実測 btx_future の教訓）
        if best["card"] in ("友好+1", "友好+2") and best.get("target_kind") == "character":
            _bt = self._alive(view, best["target"])
            if _bt and any("不安" in a3["name"] and "除去" in a3["name"]
                           and _bt["goodwill"] < a3["hearts"]
                           for a3 in goodwill_abilities_of(best["target"]) or []):
                self._cooler_invested = True
        # 席間協調：冷却した事件日を記録（後席の重複冷却を減点）
        if best["card"] == "不安-1" and best.get("target_kind") == "character":
            for _d, _cands in getattr(self, "_culprit_cands", {}).items():
                if _d >= view["day"] and best["target"] in _cands \
                        and _d in getattr(self, "_incident_danger", {}):
                    self._cooled_days.add(_d)
        # 席間協調：このターンの自分の移動を予定表に記録（後席の占有計算が織り込む）
        if best["card"] in _MOVE_TOGGLE and best.get("target_kind") == "character":
            mc = self._alive(view, best["target"])
            md = _move_dest(mc["area"] if mc else None, best["card"])
            if md:
                self._planned_moves[best["target"]] = md
        return best

    # -- ★2段化：ターン計画（3席分の割当を一括で決める） ----------------------

    # ★既定OFF（2026-07-09 A/B実測）：現行の調停ルール（重複減点・ピン残数）では
    #   計画ONの正味ゲインがない（OFF 2.077/3.15 vs ON 2.085/3.325。初期の
    #   btx_future 1.0 は常時計画の実験値で、実行時フラグが既に重複投資を防いでいた）。
    #   機構は「4需要vs3席」の取捨をルール接地で表現できる唯一の足場なので保持し、
    #   調停ルールを強化してA/Bで勝ってから再有効化する。
    PLAN_ENABLED = False
    PLAN_ADOPT_MARGIN = 10.0
    _PLAN_TOPK = 14

    # VIP保護のJOINT救済の設計値（P4）。救済が押し出す需要の価値を上回る時だけ採るよう
    # 中庸に置く：ピン(93)や高危険冷却(≈96)は押し出さず、中位需要とだけ交換される。
    _VIP_SAFE_BONUS = 22.0
    _VIP_RISK_ROOM_MAX = 2   # VIP薄部屋リスクとみなす部屋人口の上限（係数）

    def _vip_set(self) -> set:
        return (({self._keyperson} if self._keyperson else set())
                | getattr(self, "_friend_guards", set())
                | getattr(self, "_fatal_guards", set()))

    def _vip_sk_risk(self, view: dict) -> list[tuple]:
        """VIPがSK疑いと同室で部屋が薄い（≤3人）＝2人きり絞りの標的の一覧。

        返り値: [(vip名, エリア, その部屋のSK疑い集合), ...]。空なら非リスク。
        """
        sk_pool = self._sk_suspects | getattr(self, "_sk_cands", set())
        if not sk_pool:
            return []
        by_area: dict = {}
        for o in view["characters"]:
            if o["alive"] and o.get("on_board", True) and o.get("area"):
                by_area.setdefault(o["area"], []).append(o["name"])
        out = []
        for vip in self._vip_set():
            vc = self._alive(view, vip)
            if not vc:
                continue
            occ = by_area.get(vc["area"], [])
            sk_here = {n for n in occ if n in sk_pool and n != vip}
            # 薄い部屋（≤3人）にVIPとSK疑いが同居＝数手で2人きりに絞られうる
            if sk_here and len(occ) <= self._VIP_RISK_ROOM_MAX:
                out.append((vip, vc["area"], sk_here))
        return out

    def _plan_vip_safe(self, view: dict, opts: list[dict],
                       vip_risk: list[tuple]) -> int:
        """この3手組が VIPリスクを何件「救済」するか（退避/SK移動/第三者注入のいずれか）。

        救済＝プランの主人公移動を適用した後、そのVIPが「SK疑いなし部屋」に居るか、
        部屋人口が4以上（薄さ解消）になる。脚本家の同時移動は読めないので保証はしないが、
        少なくとも主人公の手で薄い同室を解消する組を優先する（P3の per-card 点でなく
        組全体の副作用として評価＝押し出す需要とのトレードオフを val で自動調停）。
        """
        # プランの主人公移動を仮適用した配置を作る
        pos = {o["name"]: o.get("area") for o in view["characters"]
               if o["alive"] and o.get("on_board", True)}
        for o in opts:
            if o["card"] in _MOVE_TOGGLE and o.get("target_kind") == "character":
                d = _move_dest(pos.get(o["target"]), o["card"])
                if d:
                    pos[o["target"]] = d
        # 移動禁止でSK疑いをピン＝そのSKはリスク部屋から動けない（脅威は残るが、mmの
        #   「SKを別のVIPへ配達」を封じる）＝ピン対象がリスクのSKなら救済扱い。
        pinned = {o["target"] for o in opts
                  if o["card"] == "移動禁止" and o.get("target_kind") == "character"}
        saved = 0
        for vip, _area0, sk_here in vip_risk:
            vip_area = pos.get(vip)
            if vip_area is None:
                continue
            occ = [n for n, a in pos.items() if a == vip_area]
            sk_now = [n for n in occ if n in sk_here and n != vip]
            if not sk_now:
                saved += 1                      # VIPがSK疑いと別部屋へ＝救済
            elif len(occ) >= 4:
                saved += 1                      # 部屋が厚くなった＝2人きり困難
            elif all(s in pinned for s in sk_now):
                saved += 1                      # 同室SKを全ピン＝配達・追随を封じた
        return saved

    def _plan_turn(self, view: dict, options: list[dict], score_fn,
                   vip_risk: list[tuple] | None = None) -> list[dict]:
        """トップK候補から制約つき3手組を全列挙し、調停値が最大の組を返す（decide順）。

        制約：同一対象は1手まで（重ね置き禁止）・暗躍禁止は1枚（自滅）。
        調停（貪欲逐次maxとの差分）：
        - 同一事件日への冷却の重複 −8/枚（_cooled_days と同じ思想を組内で適用）
        - 冷却役投資の重複 −70/枚（1人解禁すれば十分）
        - ★ピン残数会計：移動禁止は席計3枚/ループ。残ピン<残日数（毎日は張れない）で、
          かつ組から押し出された75点以上の需要があるなら、ピンに−18
          （＝今日はピンを捨てて別需要を拾う取捨が選べるようになる）。
        """
        from itertools import combinations
        scored = sorted(((score_fn(o), o) for o in options), key=lambda x: -x[0])
        scored = [(s, o) for s, o in scored[: self._PLAN_TOPK] if s > 0]
        if len(scored) < 3:
            return [o for _s, o in scored]

        days_left_after = view.get("days_per_loop", 3) - view["day"]
        pins_left = max(0, 3 - getattr(self, "_pins_spent", 0))
        pins_scarce = pins_left < days_left_after + 1

        def _cool_day(o: dict):
            if o["card"] != "不安-1" or o.get("target_kind") != "character":
                return None
            for d in getattr(self, "_incident_danger", {}):
                if d >= view["day"] and o["target"] in                         getattr(self, "_culprit_cands", {}).get(d, ()):
                    return d
            return None

        def _is_cooler_invest(o: dict) -> bool:
            if o["card"] not in ("友好+1", "友好+2") or o.get("target_kind") != "character":
                return False
            c = self._alive(view, o["target"])
            return bool(c and any(
                "不安" in ab["name"] and "除去" in ab["name"] and c["goodwill"] < ab["hearts"]
                for ab in goodwill_abilities_of(o["target"]) or []))

        # ★アンサンブル：貪欲（逐次max相当）の3手組を基準にし、計画はそれを
        #   +6点以上上回る「明確な調停ゲイン」がある時だけ採用する。
        #   計画を常時使うと全ターンの配分が微妙に変わり、ナイフエッジのseedが
        #   ランダムに揺れる（FS_17が1→9に崩れた実測＝挙動拡散の抑制）。
        # 逐次貪欲のシミュレーション＝旧挙動の再現。★静的スコア順では不十分：
        #   実際の旧挙動は席ごとにフラグ（冷却済み日−8・投資済みで投資分岐消滅）で
        #   スコアが動く＝同じ調整を逐次に適用して選ぶ（5日級のゲート不全の実測修正）。
        # ★即敗北ボードの死守（2026-07-11・FS s6検死）：vip_risk 起動時、danger_board に
        #   mmが今ターン暗躍札を置き、封じないと今ループで盤面敗北が確定する場合、その
        #   暗躍禁止を組に必ず含める（VIP救済ボーナスで即敗北カバーを取引きさせない）。
        #   二正面（board即敗北＋VIP-SK同室）が3席で賄えるなら両方守れる。★カルティスト
        #   同室で暗躍禁止が無視される時は、同ターンに剥がせる移動が scored にある場合のみ
        #   死守（剥がせない＝封じ空振り＝強制しない）。gate は score≥80（真の敗北ボードに限定）。
        _must_block_idx = None
        if vip_risk:
            _db = self._guess_defeat_board(view)
            _mm_bnow = {p["target"] for p in view.get("placements", [])
                        if p.get("owner") == "mastermind"
                        and p.get("target_kind") == "board"}
            if _db is not None and _db in _mm_bnow:
                _cult_here = [c for c in self._cultist_suspects
                              if (cc := self._alive(view, c)) and cc["area"] == _db]
                _can_relocate = (not _cult_here) or any(
                    o2["card"].startswith("移動")
                    and o2.get("target_kind") == "character"
                    and o2["target"] in _cult_here for _s2, o2 in scored)
                if _can_relocate:
                    for _i, (_s, _o) in enumerate(scored):
                        if (_o["card"] == "暗躍禁止" and _o["target"] == _db
                                and _o.get("target_kind") == "board" and _s >= 80.0):
                            _must_block_idx = _i
                            break

        greedy_idx: list[int] = []
        used_t: set = set()
        used_kinshi = False
        g_days: set = set()
        g_invested = False
        if _must_block_idx is not None:   # 死守手を greedy 基準にも種として先着させる
            _o0 = scored[_must_block_idx][1]
            greedy_idx.append(_must_block_idx)
            used_t.add((_o0["target"], _o0.get("target_kind")))
            used_kinshi = True   # 暗躍禁止は1枚（自滅回避）
        for _pick in range(3 - len(greedy_idx)):
            best_j, best_s = None, None
            for j, (s, o) in enumerate(scored):
                if j in greedy_idx:
                    continue
                t = (o["target"], o.get("target_kind"))
                if t in used_t:
                    continue
                if o["card"] == "暗躍禁止" and used_kinshi:
                    continue
                eff = s
                cd = _cool_day(o)
                if cd is not None and cd in g_days:
                    eff -= 8.0
                if _is_cooler_invest(o) and g_invested:
                    eff -= 70.0
                if best_s is None or eff > best_s:
                    best_j, best_s = j, eff
            if best_j is None:
                break
            o = scored[best_j][1]
            greedy_idx.append(best_j)
            used_t.add((o["target"], o.get("target_kind")))
            used_kinshi = used_kinshi or o["card"] == "暗躍禁止"
            cd = _cool_day(o)
            if cd is not None:
                g_days.add(cd)
            g_invested = g_invested or _is_cooler_invest(o)

        best_val, best = None, None
        top_scores = [s for s, _o in scored]
        for idx in combinations(range(len(scored)), 3):
            if _must_block_idx is not None and _must_block_idx not in idx:
                continue                       # 即敗北ボードの死守手を必ず含める
            opts = [scored[i][1] for i in idx]
            tgts = [(o["target"], o.get("target_kind")) for o in opts]
            if len(set(tgts)) < 3:
                continue                       # 重ね置き禁止
            if sum(1 for o in opts if o["card"] == "暗躍禁止") > 1:
                continue                       # 暗躍禁止の自滅
            val = sum(scored[i][0] for i in idx)
            days = [_cool_day(o) for o in opts]
            days = [d for d in days if d is not None]
            if len(days) > len(set(days)):
                val -= 8.0 * (len(days) - len(set(days)))
            inv = sum(1 for o in opts if _is_cooler_invest(o))
            if inv > 1:
                val -= 70.0 * (inv - 1)
            if vip_risk:
                # ★JOINT救済ボーナス（P4）：この組がVIPのSK薄部屋リスクを解消する数×ボーナス。
                #   per-card点でなく組全体の副作用＝押し出す需要とのトレードオフをvalが調停。
                val += self._VIP_SAFE_BONUS * self._plan_vip_safe(view, opts, vip_risk)
            if pins_scarce:
                pin_scores = [scored[i][0] for i in idx
                              if scored[i][1]["card"] == "移動禁止"
                              and scored[i][0] >= 70.0]
                if pin_scores:
                    # 交換先＝ボード供給事件の冷却 or ML分離（79）だけ。
                    # 汎用の75点（実験等）とピンを交換すると5日級が総崩れした実測。
                    feed_days = {i2["day"] for i2 in view.get("incidents", [])
                                 if i2.get("name") in ("行方不明", "邪気の汚染")}
                    excluded_feed = any(
                        j not in idx and s >= 72.0
                        and ((_cool_day(o2) in feed_days)
                             or (o2["card"].startswith("移動")
                                 and s == PRIORITY.get("危険犯人_ML分離")))
                        for j, (s, o2) in enumerate(scored))
                    if excluded_feed:
                        val -= 18.0 * len(pin_scores)
            if tuple(idx) == tuple(sorted(greedy_idx)):
                greedy_val = val
            if best_val is None or val > best_val:
                best_val, best = val, [scored[i] for i in idx]
        if best is None:
            return [o for _s, o in scored[:3]]
        # 貪欲組の調停値を計算していなければここで評価（組合せ順で必ず通る想定だが保険）
        try:
            greedy_val
        except NameError:
            greedy_val = None
        if greedy_val is not None and len(greedy_idx) == 3                 and best_val < greedy_val + self.PLAN_ADOPT_MARGIN:
            best = [scored[i] for i in greedy_idx]
        # decide順＝スコア降順（強い手から確定させる＝後席のフォールバックに強い）
        best.sort(key=lambda x: -x[0])
        return [o for _s, o in best]

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
            # ご神木の特性（{"goshinboku":..,"target":..}）等、character/ability を持たない
            # 非標準オプション＝主人公にとっては低価値（カウンター移動）。passより下に置く。
            if "character" not in o:
                return 0.5
            user, ability, tgt = o["character"], o["ability"], o["target"]
            if "暗躍除去" in ability:  # 敗北条件ボード/暗躍持ちの暗躍を剥がす（最優先）
                if tgt in self._AREAS:
                    base = 100.0 + 10.0 * view["board_anyaku"].get(tgt, 0)
                    return base + (20.0 if tgt == danger else 0.0)
                c = self._alive(view, tgt)
                return 60.0 + 10.0 * (c["anyaku"] if c else 0)
            if "不安" in ability and "除去" in ability:
                c = self._alive(view, tgt)
                if not c:
                    return 0.0
                # ★危険事件の犯人への使用はルール接地の価値（45〜72）を採用
                #   （一律25×riskでは自己ポンプ犯人の冷却が発火しない実測）
                base = self._ability_value(user, ability, tgt, view)
                return max(base, 25.0 * self._risk(c))
            return self._ability_value(user, ability, tgt, view)

        return max(options, key=gscore)
