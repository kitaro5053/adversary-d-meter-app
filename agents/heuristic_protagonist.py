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
from engine.data import (LADDER_CHARS, ROLE_CLAUSE_ABILITY,
                         UNREFUSABLE_ABILITY_CHARS, ability_class_target_alive,
                         forbidden_of, goodwill_abilities_of, initial_area_of,
                         unrest_threshold_of)

from sim.state import missing_incident_boards_from_view

from .belief import Belief, _cultist_constraints
# ★A-78：敗北板が**KBで固定**されているルールY（主人公も板を知っている）＝
#   `agents.defense_plan._FIXED_DEFEAT_RULE_BOARD` が単一ソース。ボードX系（復讐者の灯火／
#   巨大時限爆弾Xの存在）は「どの板がXか」が非公開なのでここには入らない。
from .defense_plan import _FIXED_DEFEAT_RULE_BOARD as _A78_FIXED_DEFEAT_BOARD
from .soft_evidence import MisleaderUnrestPresence

_MOVE_TOGGLE = {"移動←→": (1, 0), "移動↑↓": (0, 1)}  # 主人公の移動カード（斜めは無い）

# ★B-86'：「自身の役職開示」＝開示されるのが**行使キャラ自身**の役職である能力（KB: 20）。
#   `rules/20:44,167-170`＝サラリーマン「[脚]：サラリーマンの役職を伝える」／
#   `rules/20:57,106-108`＝イレギュラー「[脚]：イレギュラーの役職を伝える」。
#   ∴ この能力の情報価値は**行使キャラ自身の役職の不確かさ**で決まる（他人のではない）。
#   配役はゲーム中固定（非公開シート＝`rules/00:76`）＝一度開示されたら**以後の全ループで**
#   この能力から得られる情報はゼロ。
#   ★アルバイト？（拡張）の「自身役職開示＋友好+2」は**効果（友好+2）が残る**ので含めない。
_SELF_ROLE_REVEAL: frozenset = frozenset({
    ("サラリーマン", "自身の役職開示"),
    ("イレギュラー", "自身の役職開示（第2L以降）"),
})

#: 条文能力に「友好無視／絶対友好無視」を持つ役職（`engine.data.ROLE_CLAUSE_ABILITY` が単一ソース）。
#  FS＝キラー／クロマク／カルティスト(絶対)／マイナス。BTX＝＋ウィッチ(絶対)／ファクター。
_IGNORE_ROLES: frozenset = frozenset(
    r for r, c in ROLE_CLAUSE_ABILITY.items()
    if c in ("友好無視", "絶対友好無視"))


# ★B-28：実質移動不可の判定は agents/card_effect.immobile_static に一本化した
#   （旧 _immobile_of＝同一実装をここに重複保持していた）。空振りゲートの述語は
#   card_effect.noop_reason が単一の真実＝defense_plan の break 生成も同じ物を見る。


def _plan_card_class(card: str) -> str:
    """B-99：防御プランナー加点をカード種別ごとに分けるための粗い分類。

    「移動禁止」は従来どおり単独扱い（呼び出し側で先に分岐）＝ここでは
    移動(退避) / 暗躍禁止 / 不安 / 友好 に畳む。"""
    if card in _MOVE_TOGGLE:
        return "移動"
    if card == "暗躍禁止":
        return "暗躍禁止"
    if card.startswith("不安"):
        return "不安"
    if card.startswith("友好"):
        return "友好"
    return card


def _move_dest(src: str | None, card: str) -> str | None:
    """移動カードで src からどこへ行くか（未登場/非移動カードは None）。"""
    t = _MOVE_TOGGLE.get(card)
    return destination(src, t) if (src and t) else None


def _diag_area(src: str | None) -> str | None:
    """src の**対角**エリア（2×2盤で ↑↓/←→ の1枚では到達できない唯一のエリア）。

    ★B-94 で使う：mm の移動札1枚では届かない位置＝前日に犯人を逃がす行き先。
    （斜め移動は mm の 1/loop 札＝engine.models ONCE_PER_LOOP。）
    """
    if not src:
        return None
    return destination(src, (1, 1))

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
    # ★B-65（5日級検死#0）：実証急所（公開済みフレンド/実証VIP）の幻想をSK2人きり
    #   リスク日に板札で退避（幻想特性＝被セット不可・板経由が唯一の退避手段）。
    #   101＝SK配達ピン_急所単独/KP単独と同格（実証急所の即死線封鎖）。★98（暗躍禁止の
    #   下）は不成立の実測（2026-07-27・#0 L4D4）：退避札の置き場＝幻想のいるボードは
    #   暗躍禁止→同ボード(96-100)と**同一ターゲットで排他**（主人公は重ねられない）＝
    #   下に置くと先席の暗躍禁止が板を塞ぎ退避が構造的に置けない。100超が成立の必要条件。
    "幻想退避_実証急所": 101.0,
    "幻想引き剥がし": 89.0,        # ボード経由（確信）
    "ボード封じ_危険": 88.0,       # 危険ボードへ暗躍禁止（暗躍0でも）
    "クロマク剥がし": 88.0,        # (c3)
    # ★B-59（2026-07-26 検死＝3日級残敗4局 §2）：実証済み敗北ボード上の供給源候補の退避。
    #   87＝FB友好投資(81)/冷却役同行(77)より上・クロマク剥がし(88)/ボード封じ_危険(88)/
    #   カルティストピン(99)/暗躍禁止(100-101)より下＝二段防御の席は奪わず第3席だけ取る。
    "供給源退避_実証": 87.0,
    # ★B-67（2026-07-27 目視検死P1＝A-60新残敗9局の全局共通）：供給役の**復帰・搬入**への
    #   当日応手。既存の剥がし系(c2)/(c3)/B-59は「今ボード上に居る」供給役限定＝mmが
    #   ボード外の供給役に札を伏せて**戻す/運び込む**日（復帰＝A-60の50点）に応手が無く、
    #   確信済みクロマクの最終日復帰(42)が友好filler(64.8)に負けた（FS s0＝同じ最終日を
    #   3回落とす時計仕掛け・classify_dayの防御例＝移動禁止→鑑識官/逸らし移動）。
    #   ピン＝フェリーピン(93)のクロマク版・同格。逸らし＝B-59退避(87)/剥がし(88)の直下
    #   ＝二段防御（暗躍禁止→ボード96-101）の席は奪わず、fillerの席だけ取る。
    "供給役復帰ピン": 93.0,
    "供給役搬入逸らし": 86.0,
    "キラー候補反応封じ": 84.0,    # +16×キラー確率+4×暗躍
    # ★B-77（検死3 §T-B・2026-07-27）：押し切り（A-63）への前日冷却エンジン。
    #   自傷系押し切り事件（蝶/自殺）の当日は完全列挙でmate（btx_future s0 L4D2＝528×22747）
    #   ＝勝負は前日。①不安除去役への♡2（mmの友好禁止が伏さっていない方＝後出し二系統）
    #   ②除去役の犯人同エリア化、のペア採点。83＝TT投資82/冷却役投資81より上
    #   （検死実測＝現AIはTT/FB投資2席で前日を素通り）・同行80＝TT投資+1(79)より上。
    "前日冷却投資_押切": 83.0,
    "前日カバー_押切": 82.5,
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
    # ★B-64（5日級検死#2）：mm移動札のML同ターン寄せを移動禁止で打ち消す（蝶の当日・
    #   ML+1がちょうどピボットの日限定）。79＝ML分離(79)と同格（同じ能力ポンプ断ちの
    #   移動対抗語彙）・当日臨界の照準冷却(85+)より下＝冷却と対の二席目を取る。
    #   ★点の掃引済み負の結果（2026-07-27）＝48（冷却系より下）に下げる案は、5日BTX#2
    #   L4D3で残り1席の冷却済み日に届かず蝶を素通し（probe実測・席の在庫依存で点数では
    #   両立不能）＝79を維持し、代わりに事件種を蝶に限定して二正面日の誤発火を消した。
    "ML寄せブロック": 79.0,
    # ★B-52＝kill事件（KP死）の犯人をMLから引き離す。冷却(≈92当日)＋分離の両輪が必須＝
    #   両方が席を取れる高さに置く（掃引で較正）。fs5_guard の誤配分先 移動禁止→KP(68)・
    #   板暗躍禁止(81) を上回る必要がある。
    "危険犯人_ML分離_kill": 70.0,
    "冷却役同行": 77.0,           # 解禁済み冷却役を危険犯人の同エリアへ（能力は同エリア限定）
    # ★B-58（2026-07-26 検死＝3日級残敗4局 §4）：SK配達ピン_KP単独(101)の急所拡張＝
    #   公開済みフレンド/実証VIP（死亡がループ終了時敗北に直結）が1人きりの日に、
    #   mmがSK候補へ伏せた札（＝配達移動の線）を移動禁止で封じる。同値101＝同格の急所。
    "SK配達ピン_急所単独": 101.0,
    # ★B-81（レース監査T-r1）：押し切り日でない時の状況昇格＝当日冷却(102級)の直上・
    #   VIP注入_実証済(103)未満。公開フレンド死＝ループ喪失確定 vs 非致命冷却の逆転是正。
    "SK配達ピン_急所単独_昇格": 102.5,
    # ★B-71（A-64対応・2026-07-27）：SK配達の被害者側ピン＝急所に伏せ札×SK単独エリア実在×
    #   キラー線が今日死んでいる日の 移動禁止→急所。102＝暗躍禁止(100-101)より上（同一席の
    #   択で配達側を取る）・VIP注入(103)/確信キラー反応封じ(105)より下。
    "SK配達ピン_急所伏せ札": 102.0,
    # ★B-76（検死3 T-A・2026-07-27）：公開フレンド×配達実証×SK単独エリア実在の日の
    #   退避移動（合成で行き先を逸らす）。85＝FB友好投資(81)/冷却役同行(77)より上＝
    #   「運ばれる本人への友好+2」の席を取る。盤ガード(88-101)/B-58(101)より下＝
    #   二段防御の席は奪わない。ピンでなく移動＝B-72の凍結自傷を構造的に回避。
    "フレンド退避_配達実証": 85.0,
    "SKテスト": 75.0,              # 探索テスト・役職テスト給餌
    "危険事件_邪気実証": 74.0,     # 神社敗北の実証あり
    "幻想引き剥がし_候補": 74.0,
    "SK配達ピン": 72.0,            # 最終ループ限定。当日冷却(74)より下
    "カルティスト剥がし_候補": 72.0,   # (c2b)
    # ★B-75/B-74：すり抜け実証の∩（≤2）成員への剥がし昇格。72では冷却役投資(81)に
    #   席負けして盤上カルティスト経由の素通りが止まらなかった（btx5_seal s3 L2D2・
    #   random_BTX s8 L2D3=classify_dayでdefense_exists証明済み）。86＝冷却役投資(81)
    #   より上・確信剥がし(90)/B-59(87)未満。発火は _b75_exposure_ok ゲート必須。
    "カルティスト剥がし_実証": 86.0,
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
    # ★B-57（2026-07-26 検死＝3日級残敗4局）：薄事前のTTハーツ仕上げ（最終日限定）。
    #   69.9＝空振り気味の投機冷却（mm札カバー冷却≈69.5）より上・B-52分離(70.0)/
    #   実犯人の当日冷却(≈70.2+)/SK配達ピン(72)より下＝防御席は奪わない。
    "TT仕上げ_薄事前": 69.9,
    # ★B-66(2)（2026-07-27 5日級検死#13 §6）：敗北ループを2回以上観測し、その全てで
    #   ループ終了時暗躍<2に終わった板＝どの敗北の原因でもなかった実証ゼロの板（囮の公算）。
    #   mmが今置いた札の吸収自体は等価交換で正当（§1i）＝完全な見切り（30）ではなく、
    #   冷却・分離・退避（75〜82級）の実需要に席を譲る高さへの減点。SKテスト(75)/
    #   移動防御(75)より下・危険事件_邪気実証(74)/幻想引き剥がし_候補(74)より上。
    "ボード封じ_未実証": 74.5,
    # ★B-90(a)（2026-07-28・新era検死 T-2/M2）：位置が条件の供給能力（ミスリーダーの同エリア
    #   不安+1）を移動1枚で折る。**当日冷却（≈92）と暗躍禁止（81〜101）の席は奪わない**高さ＝
    #   「冷却＋分離の両輪」の**2席目**だけを取る（btx_bomb L1D3 実測＝3席目に居たのは
    #   クロマク剥がし_候補(71)／実測で無関係と分かった手）。
    # ★掃引（2026-07-28・両ベンチ200局・基準＝origin/main 2d86fa4 の実測 107/62。(c)=89併用）：
    #   68＝クロマク剥がし_候補(71)に席負けし btx_bomb が L1 でなく L7 止まり（3日平均3.200）。
    #   **72 / 76 / 84 / 88 は完全に同一**（3日 防衛127・2.831／5日 62・3.786・退行0）＝プラトー。
    #   91 / 93 / 95 / 99 / 105 も互いに同一で、さらに 5日 random_BTX s8 が fb_win→L8防衛
    #   （5日 63・3.771・退行0）＝**上側にもう1つのプラトー**がある。
    #   ★ただし 91 以上は**当日冷却（≈92）／フェリーピン(93) の席を奪う**領域＝
    #   「既存の高優先手の席を奪わない」という設計方針に反する。∴ 下側プラトーの中央 76 を
    #   採用し、上側プラトーの +1 は採らない（FableA 裁定の材料として報告に回す）。
    # ★B-92（2026-07-29・新era 再掃引＝**負の結果**）：基準 main 1750bf5 の実測に対し
    #   **84 / 88 / 91 / 92 / 93 / 95 / 99 / 101 / 105 / 110 が全て完全に同一**
    #   （3日 127/2.900・5日 59/4.000／per-game も 改善0・退行0）＝
    #   旧era で報告された「91〜105 で 5日級 +1防衛」は**新eraでは再現しない**
    #   （A-73/A-73b 再基準化でその1局が別の理由で決まるようになった）。
    #   ∴ **上げる根拠が無い**＝76 を維持する。監査＝`docs/監査_B92_B90上側プラトー審査_2026-07-29.md`。
    "ML供給隔離_同室単独": 76.0,
    # ★B-94（2026-07-28・手練れユーザー実戦FB）：B-90(a) の**前日**拡張（B-77と同型）。
    #   B-90 は「今日が致死系事件日」が発火条件＝**MLが不安を積む前日に一度も発火しない**
    #   （btx_bomb＝D2 不安拡大〈非致死〉／D3 遠隔殺人〈致死〉）。前日にMLの+1が入ると
    #   翌日は「冷却1枚＋分離」でも mm の 不安+1 に相殺されて臨界を割れない＝手遅れ。
    #   ∴「明日が致死系事件日」×「今日のML+1がちょうど明日の臨界を作る」日に前日で折る。
    #   点＝当日76より**下**（前日手は当日手より価値が低い場面がある＝掃引で較正）。
    # ★掃引（2026-07-28・両ベンチ200局・基準＝origin/main 6594db3 の実測 127/58）：
    #   **45 / 60 / 74 / 84 は完全に同一**（3日 防衛127・2.969／5日 58・4.100・退行0）＝
    #   広いプラトー。30 は発火が席を取れず no-op（＝off と bit 一致）。
    #   ∴ プラトー内で**当日76より下**の中央付近 74 を採用。
    "ML供給隔離_前日": 74.0,
    # ★B-90(c)（2026-07-28）：フレンド退避(85)の**状況昇格**（B-81と同型）。過去の敗北ループで
    #   そのフレンドがターン終了フェイズに死亡＝同型敗北の再履修が実証されている日だけ、
    #   板の引き剥がし（クロマク剥がし88／ボード封じ_危険88）より上へ。カルティスト剥がし(90)
    #   ／暗躍禁止(96-101)の席は奪わない＝二段防御の外側だけを取る。
    # ★掃引（2026-07-28・両ベンチ200局・(a)=76併用・基準＝2d86fa4 の実測 107/62）：
    #   OFF＝3日 117（(a)のぶんだけ）／86.5・89・90・91 は完全に同一
    #   （3日 防衛127・2.831／5日 62・3.786・退行0）＝下側プラトー。
    #   93・95・96 も互いに同一で、さらに **5日が 64・3.543（btx5_seal帯8局＋random_BTX s8 の
    #   計9局が改善・退行0）** ＝上側プラトー。境目は 91→93。
    #   ★上側プラトーは**フェリーピン(93)・当日冷却(≈92)と同格以上**になる領域＝
    #   「既存の高優先手の席を奪わない」方針に反する。∴ 下側プラトーの 89 を採用し、
    #   5日級の +2防衛 は FableA 裁定の材料として報告に回す（本実装では取らない）。
    # ★B-92（2026-07-29・上記の保留を**新eraで再掃引して解除**。監査＝
    #   `docs/監査_B92_B90上側プラトー審査_2026-07-29.md`。基準＝main 1750bf5 の実測
    #   3日 127/2.900・5日 59/4.000）：
    #   下側プラトー＝86.5〜92（ベースラインと bit 一致）／**上側プラトー＝93〜98**＝
    #   **5日級 61/70・平均3.786（改善8局・退行0局）／3日級は完全不変（改善0・退行0）**。
    #   99 以上で崩れる＝3日級 btx_seal が **10 seed 全部 3→4** に退行
    #   （`カルティストを敗北ボードへ運ぶ手の封じ`＝99 の席を奪うため）。
    #   ★押しのけ検死＝**押しのけているのはフェリーピン(93) ちょうど1手**
    #   （対照実験：(c)=89 のまま **フェリーピンだけ 93→88** に下げると、改善8局が内訳まで
    #   完全に一致する）。当日冷却(≈92)・板の引き剥がし(88/90)・暗躍禁止(96-101) は
    #   **1手も押しのけていない**（93〜98 が完全同一＝96 を跨いでも何も動かない）。
    #   ★フェリーピンの現era妥当性も併せて掃引＝**90〜100 が完全に同一**（3日・5日とも
    #   ベースライン）＝旧mm基準で決めた 93 という絶対値は現コーパスでは何も決めていない。
    #   ★正典根拠＝`docs/評価哲学_強さ指標_2026-07-26.md` §6（2026-07-29 ユーザー正典）＝
    #   「**同型の敗北の反復は席を全部使ってでも阻止する＝この一点だけは席の経済に優越する**」。
    #   本項の発火条件はまさに「過去の敗北ループで**このフレンドがターン終了に死んでいる**」
    #   （`_b90_friend_death_proven`）＝§6 が「席を奪わない」一般則を明示的に上書きする場面。
    #   ∴ 上側プラトーの内側で**既存定数と同点にならない** 95 を採用
    #   （93＝フェリーピンと同点・96＝ボード封じ_mm札と同点・98＝崩れる99に隣接＝いずれも避ける）。
    "フレンド退避_配達実証_昇格": 95.0,
}


# ---------------------------------------------------------------------------
# ★B-84（2026-07-28・手練れユーザー実戦FB）＝「ボード封じ_mm札(96)」の板選択を集合依存にする。
#
# 症状：FS（殺人計画×不穏な噂・実戦ログ seed0 L1D1）で mm が神社と学校の両方に札を置いた局面、
#   両者が **96.00 の完全同点** になり、`_AREAS=("病院","神社","都市","学校")` の**列挙順**で
#   神社が選ばれた。神社が固定敗北板なのは **BTX 専用**（封印されしモノ＝50:38）で、
#   **FS の固定敗北板は学校**（守るべき場所＝40:47）＝FSで神社が学校に優先する根拠は無い。
#
# 正体＝B-45 定石1（「BTXなら神社、FSなら学校に伏せ札があれば暗躍禁止」）の未実装部分。
#   従来の Step1 監査は「板に伏せ札があれば暗躍禁止を置いたか」の可否しか数えておらず、
#   **複数板に伏せ札がある時にどちらを選ぶか**＝集合依存部分が素通りしていた。
#
# 設計：セット名で直接分岐する（if FS then 学校）のではなく、既存の
#   `defense_plan._defeat_board_probs`（belief の rule_marginals から {板: P(敗北板)} を出す
#   単一ソース）を**タイブレークに接続**する。FSでは 封印されしモノ が可能世界に存在しない＝
#   P(神社) は 復讐者の灯火×クロマク神社 の分しか立たず、P(学校)＝守るべき場所＋復讐者×学校
#   が必ず上回る。BTXでは逆に P(神社) が立つ＝**集合依存が belief から自動で出る**
#   （board_x型・実証済み敗北板の学習も同じ経路で効く＝決め打ちにならない）。
#
# ★拮抗時の扱い（不安定化の防止）：確率を連続値で加点すると、僅差の板が毎ターン入れ替わって
#   防御が振れる。そこで「**先頭グループ**」という deadband を使う＝最大値から _B84_MARGIN 以内の
#   板は**全て同格**として同じ加点を受け（＝拮抗している板の間の順序は従来どおりに委ねる）、
#   明確に劣る板だけが加点を失う。閾値をまたぐ瞬間の1回きりの切替は起きるが、僅差での
#   往復は構造的に起きない（ヒステリシス相当の効果を状態を持たずに得る）。
# ★掃引（2026-07-28・両ベンチ200局／c28c8ee ベースライン）：
#   - 加点幅 eps ∈ {0.25, 0.5, 1.5, 3.0} は**全て同一結果**（3日 +5改善/0退行・5日 0改善/1退行）
#     ＝board 側の他項が整数段差（暗躍+1.0／danger_board+4.0）なので eps<1.0 なら「同点の時だけ
#     効く」＝プラトー。eps=0 はベースラインに bit 一致（掃引ハーネスの健全性確認）。
#   - 拮抗幅 margin ∈ [0.0, 0.30] も全て同一結果。0.35 で3日の改善5件が消え、0.36 以上で
#     5日の退行1件も消える（＝完全にベースラインへ戻る）＝**改善と退行は同一の決定クラスから
#     出ており、両者を分ける動作点は存在しない**（下記 §退行の検死）。0.10 はプラトーの内側で
#     両端（0＝deadband 無し／0.35＝崖）から離れたロバスト点。
_B84_MARGIN: float = 0.10      # 拮抗の許容幅（この差以内は同格＝先頭グループ）
# 加点幅は **1.0 未満**＝board_anyaku の1段（+1.0）や danger_board（+4.0）を決して覆さない
# ＝純粋なタイブレーク（同点の時だけ効く）。
_B84_TIEBREAK: float = 0.5
#
# ★退行の検死（正直な限界の記録）：5日級 random_FS s7（Y=復讐者の灯火・クロマク=サラリーマン
#   ＝板X は都市）が L2→L3 に1件退行する。L1D1 は mm が学校と都市に伏せた同点局面で、
#   belief は P(学校)=0.414 / P(都市)=0.061（＝守るべき場所が固定で学校を指す分が効く）。
#   従来はここを**列挙順で都市**＝たまたま正解していた（読みではなく偶然）。B-84 は期待値の
#   高い学校を取り、この脚本では外す＝分類は「(c) 読み合い」であって「(a) AIのミス」ではない。
#   ★同じ局面は B-45 定石1（ユーザー正典「FSなら学校」）の literal 実装でも学校を選ぶ
#   ＝この退行は本実装固有ではなく定石1そのものに内在する。


# ---------------------------------------------------------------------------
# B-93：板への暗躍禁止の「空振り(void)」ゲート（2026-07-29・ベースライン 43d33f2）
# ---------------------------------------------------------------------------
# 症状＝脚本家が板に1枚も伏せていないターンに `暗躍禁止→板` が 81点で最上位を取り続ける
# （手練れユーザーが2度指摘）。KB上その手は**確実にゼロ効果**（→ `_b93_void_futile` の docstring）。
# 述語＝void（必要条件）×{(c) 敗北に無接続の板 ／ (d) 今ループ決着済み}（十分条件側）。
#
# _B93_MODE ＝ 発火させる述語。"c"/"d"/"cd"/"off"（"cd"＝(c)と(d)の論理和＝land 値）。
# _B93_SCORE ＝ 降格後のスコア＝**機会費用の閾値**（これを超える代替手が在る席だけ明け渡す）。
#
# ★掃引（5日級70局・mode=cd。3日級130局は下の全動作点で **flip 0件**＝完全同値）：
#     score  30  34  37  38  40  42  45  50  55  65  75
#     防衛   56  56  58  58  58  57  57  57  57  56  57
#     空振り席 62   -   -  86  86   -  111 111 112   -  114(=無効果)
#   ＝**[37, 40] が防衛58のプラトー**（37と40は flip 集合まで完全一致）。38 はその中央。
#   - **45以上は事実上の no-op**：降格しても他席が同じ札を拾い直すだけ（空振り席が減らない）。
#   - **34以下は over-cut**：真の敗北板が育つ前の席まで奪って防衛-1（過去の「素朴な一般化」の再現）。
#   - mode 別（score 30／55 で比較）＝c 単独・d 単独・cd は**同一の flip 集合**に落ちる
#     （fs5_guard では 神社が dead かつ loop_lost の席で両述語が同時に立つため）。
#   - ★負の結果：「同じ札をより良い板に置ける時だけ降格する純タイブレーク版」(mode e) は
#     **両ベンチとも flip 0件・空振り席もほぼ減らない**＝発火が薄すぎる＝不採用（コードは削除）。
_B93_MODE: str = "cd"
_B93_SCORE: float = 38.0

# ---------------------------------------------------------------------------
# B-103 論点B：「この板は敗北条件に絡まない」という**確定情報**の使い方
# ---------------------------------------------------------------------------
# 実装＝`defense_plan._defeat_board_probs` の board_x を**初期エリア**で散らす補正
# （復讐者=クロマクの初期エリア `rules/40:41`／爆弾X=ウィッチの初期エリア `rules/50:52`）。
# ★負の結果（掃引済み・doc §4c）：`ボード封じ_mm札`(96) の `_board_threat_live` ゲートを
#   **病院限定から全板へ広げる**案は、初期エリア補正を入れた後でも 5日級 65→64 で退行。
#   ＝「敗北に無接続と確定した板でも、mm札の吸収（等価交換・§1i）は依然として正当」。
#   2026-07-19 postmortem の結論はこの補正の後も維持される＝**病院限定のまま**。


# ---------------------------------------------------------------------------
# B-100 Phase 0：計測専用フック（2026-07-29・★既定 None＝意思決定に一切触れない）
# ---------------------------------------------------------------------------
# 目的＝「防御プランナーが挙げた脅威を、AIが実際に打った3枚が覆っていたか」を外から観測する。
# 契約：
#   - 既定 `B100_HOOK is None` のとき、追加されるのはモジュール変数の None 判定だけ＝
#     **盤面・スコア・乱数・内部状態のいずれにも触れない**（挙動 bit 不変）。
#   - フックは `set_card` の決定が確定した直後に一度だけ呼ばれる。戻り値は使われない
#     （＝フック側が何を返しても AI の選択は変わらない）。例外は握り潰す。
#   - 署名＝ hook(agent, view, options, chosen, score_fn)。score_fn は decide 内の採点器
#     （＝AI が実際に使った点数）。監査側が「押し出される席の点数帯」を測るために渡す。
# 使用側＝ `arena/b100_audit.py`（監査 CLI）。本番経路からは決して設定しない。
B100_HOOK = None


def b84_top_defeat_boards(probs: dict, margin: float | None = None) -> frozenset:
    """{板: P(敗北板)} から「先頭グループ」（最大値から margin 以内の板）を返す。

    - probs が空 or 全て 0（＝盤面敗北ルールが可能世界に残っていない）なら空集合＝加点なし
      ＝従来挙動に完全に一致する。
    - 拮抗（差 ≤ margin）は**全員同格**＝どちらにも同じ加点＝順序は従来のまま（不安定化しない）。
    """
    if not probs:
        return frozenset()
    mg = _B84_MARGIN if margin is None else margin   # 既定は呼び出し時に読む（掃引可能に）
    top = max(probs.values())
    if top <= 1e-9:
        return frozenset()
    return frozenset(b for b, p in probs.items() if p >= top - mg)


class HeuristicProtagonist:
    # ★計算スコア係数（連続量・2026-07-09抽出）：PRIORITY（順位アンカー・離散）と別。
    #   arena/tune_coeffs.py が --coeff COEFF.<key> でスイープできる連続係数の単一ソース。
    #   PRIORITYの一様CEMは汎化しなかったが、これら連続量はベンチ監督で過学習せず効く
    #   （実証：_VIP_RISK_ROOM_MAX 3→2 で gen4-5d +1）。抽出値は挙動保存（byte-identical）。
    COEFF: dict[str, float] = {
        # 事件危険度（_incident_danger の代入＝冷却/退避の照準を駆動）
        "danger_seal_slope": 65.0,        # 邪気の汚染：65×P(封印)
        # ★S-1（ゲート値ジョイント掃引 2026-07-26）：68.0 → 63.0。
        #   68 では殺人事件の犯人冷却が **70.8**（danger+2.8）で、B-57「TT仕上げ_薄事前」
        #   (69.9) の席をわずかに上回り、最終日の TT ハーツ仕上げ（友好+1→TT）を空振り気味の
        #   冷却に置き換えていた（btx_future#3 L1D3 の probe で実測＝B-57 が #0 で潰した
        #   誤配分と同型の残り）。TT への友好+は【強制】友好禁止無視＝必ず通る確定防御。
        #   プラトー＝[60, 67]（両端とも同値：60 は `danger >= 60.0` ゲート・67.1 は
        #   69.9-2.8＝B-57 の席）。63.0 はその**ロバスト中心**（両側に約4の余裕）。
        #   58（ゲート割れ）は既定mm 3日 defense 129→126 の崖＝下限は硬い。
        "danger_murder": 63.0,            # 殺人事件（犯人と同エリアの1人を殺す）
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
    # ★B-99（2026-07-29）：カード種別ごとに分離した加点係数（移動禁止 以外）。
    #   値＝ 種別 -> (bonus, hot, hot_p, weak_scale)。weak_scale は robust=False
    #   （＝脚本家に同ターンで追撃されうる折り手）に掛ける倍率。空＝加点対象は
    #   移動禁止のみ（B-31の実効範囲）＝2026-07-29 以前と bit-for-bit 一致。
    # ★掃引の結論（B-99・両ベンチ130+70局の per-game 全数差分・PYTHONHASHSEED=0）：
    #   - **移動(退避)を無条件に加点するのは不可**。+8 でも 3日 btx_future が 8局とも
    #     4→9（全ループ落ち）に崩れる。機序＝押し出される席が「暗躍禁止」ではなく
    #     **友好投資／冷却**で、その投資が翌ターン以降の防御の前提だった（B-94型の状態依存）。
    #     脅威種別で切り分けると犯人は **incident_vip**（事件のVIP退避）＝これ単独でも
    #     3日 127→119・btx_future 4.1→8.0。逆に 5日は incident_vip が最大の稼ぎ頭
    #     （3日 -8 と引き換えに 5日 +2〜+3）＝**同じ信号が3日で毒・5日で薬**。
    #   - **board_defeat（敗北板への暗躍供給を断つ＝クロマク剥がし）限定なら非退行**。
    #     ここは「折り手がその退避しか無い」＝押し出す席との競合が起きにくい。
    #     採用値 (8, 88, 0.7)：3日 2.969→2.908（改善6・退行0）／5日 58→59・4.129→4.000
    #     （改善2・退行0）。★崖＝cold側を 16 以上にすると 5日 random_FS#14 が 2→9
    #     （12 までは非退行・24 で 3日は更に改善するが 5日が割れる）。
    #   - 友好（＝TT任意敗北の封じ手・最終日・友好≤2）は +8〜+120 の全点で
    #     3日 改善1（random_BTX#11 3→2）・5日 ±0＝**プラトー**（干渉が構造的に小さい）。
    #   - 不安／暗躍禁止は非退行点なし（不安は 3日 -10〜-13 と大きく壊す）。
    #     ★この「暗躍禁止は非退行点なし」は **DP-5（2026-07-29）で覆した**＝下記。
    # ★DP-5（2026-07-29・`docs/監査_DP5_プランナ推奨の配線_2026-07-29.md`）：
    #   B-99 が「暗躍禁止に非退行点なし」と結論したのは (a) 種別で絞らず全負け筋へ配ったから、
    #   (b) DP-4 前＝**折り手の過大主張が残っていた**（同エリア条件を追撃者のピンで折れると
    #   主張していた等）ため、プランナーが出す暗躍禁止の pick 自体が今と違ったから。
    #   DP-4 で折り手を是正した後は、**KP暗躍族（kp_killer / kp_anyaku）に限れば**
    #   両ベンチ per-game 退行ゼロで 5日級 +1防衛（`random_BTX` s19・s7）。
    #   掃引＝(bonus,hot) を 1:1 / 4:4 / 5:5 / 6:6 / 8:8 / 8:10 / 8:12 / 8:14 / 8:16 / 8:24 /
    #   8:48 / 8:88 / 24:88 / 8:120 で実測（両ベンチ200局・per-game 全数差分）。
    #   ★両ベンチは hot≥6 の全点で完全に同一＝**ベンチだけでは hot を決められない**。
    #   決め手は**完全情報ゲーム**（`arena/difficulty.run_perfect_info_game`・
    #   `tests/test_difficulty.py::test_perfect_info_clears_fs_scripts_in_one_loop`）：
    #   **hot ≥ 16 で basic_script が1ループクリアを落とす（1→3）**。∴ 許容窓は
    #   **6 ≤ hot ≤ 14**（下端＝s19 の分岐に要る >5・上端＝完全情報の1ループクリア）。
    #   採用 10 はその中央。★同点タイの籤を避けるため bonus=3 は不可
    #   （`random_BTX` s7 L2D2 に 81.00 の3つ巴の同点帯があり、78+3 でそこへ飛び込む）。
    PLAN_CLASS_COEFFS: dict = {
        "移動": (8.0, 88.0, 0.7, 1.0),    # board_defeat 限定（下の KINDS）
        "友好": (24.0, 88.0, 0.7, 1.0),   # TT任意敗北の封じ手（数が少なく干渉が小さい）
        "暗躍禁止": (8.0, 10.0, 0.7, 1.0),  # ★DP-5：KP暗躍族 限定（下の KINDS）・窓 6〜14
    }
    # ★B-99：加点を**脅威種別で絞る**（narrow predicate）。種別 -> 許可する Threat.kind。
    #   未登録の種別は絞らない。移動(退避)は board_defeat 以外へ配ると必ず壊れる（上記）。
    # ★DP-5：暗躍禁止は **KPに載る暗躍を止める** 負け筋だけ（kp_killer の条件2＝KPの暗躍が
    #   2に届く／kp_anyaku＝僕と契約）。board_defeat（板の暗躍）と killer_protagonist
    #   （キラーの暗躍4）は**既存採点に専用の高優先語彙があり底上げ不要**＝配ると席の
    #   奪い合いになる（B-99 の壊れ方）。
    PLAN_CLASS_KINDS: dict = {"移動": ("board_defeat",),
                              "暗躍禁止": ("kp_killer", "kp_anyaku")}

    # ------------------------------------------------------------------
    # ★B-100（混合AI・2026-07-29）：防御プランナーを「制約＝絶対防御」として使う経路。
    #   仕様の正典＝`docs/監査_B100_Phase0_介入率_2026-07-29.md` §10、実装＝`agents/b100_mix.py`。
    #   **既定 OFF**（B100_MIX=False）＝decide の属性1つの分岐だけ＝挙動 bit 不変。
    #   点数体系には一切触れない（B-99 の壊れ方＝既存の高優先手を剥がす形を避ける）。
    # ------------------------------------------------------------------
    # ★Phase 3（2026-07-30・ユーザー指摘「発動が少なすぎる」を受けた発火面積の実測後）：
    #   ファネル実測＝B-100 が関与しうる席 163 に対し発火は **5席（3.1%）**、
    #   最大の門は `REQUIRE_PLAN`（99席中78席を落とす）。門を機序で潰したうえで**既定ONにする**。
    #   監査＝`docs/監査_B100_Phase3_発火面積_2026-07-29.md`。
    B100_MIX: bool = True           # ★Phase 3 で既定ON（両ベンチ退行ゼロ・5日級+2防衛）
    #: θ経路の実在度しきい値。旧＝1.0（Phase 0 §8-1＝入口は 1.0 の一点）。
    #  ★B-114（2026-07-31）＝DP-6/B-113 後の新風景で再掃引し **1.0→0.9**：
    #   3日級は並べ替え4条件（id/rev/h1/h2）すべてで防衛+1（θ=1.0 の kp_sk 逃がし
    #   トレッドミルで落ちる random_FS s10 が防衛に戻る）・5日級は4条件すべてで防衛不変。
    #   θ=0.8 以下は h1/h2 で basic 群が崩れる＝不採用。
    #   監査＝`docs/監査_B114_θ再掃引_新風景_2026-07-31.md`。
    B100_THETA: float = 0.9
    B100_IRON_PROB: float | None = None   # 鉄則①の実在度しきい値（None＝鉄則OFF・掃引対象）
    #: 通常の席上限。★Phase 3 で 1→2（ユーザー指摘「1つの負け筋に対しても2席発動させないと
    #  防御失敗するケースはたくさんある」＝`random_BTX` s16 が **1で6→8（退行）・2で6→5（改善）**
    #  と同一局で符号反転する実測で裏づけ）。両ベンチ退行ゼロのまま介入面積が増える。
    B100_MAX_SEATS: int = 2
    B100_MAX_SEATS_2X100: int = 3   # 「確度100%級」が2本立つときの席上限（§10-1）
    #: 席の開け方。"any"＝どの席でも（下の2つのガードと組んで「**安い席**を奪う」形にする）／
    #  "last"＝最終席のみ。★既定 "any" は掃引で選んだ値（§Phase 1 掃引C＝g80anyFU）。
    B100_SEAT_POLICY: str = "any"
    # ★トレッドミル対策（Phase 1 の実測で判明）：同じ「2人きり」を毎ターン作り直されると
    #   毎ターン1席を吸われ続ける（random_BTX s4 で L3〜L6 の全日に発火し 3→7 に退行）。
    #   同一ループ内で同じ負け筋を覆う回数の上限。None＝無制限（＝初版の挙動）。
    B100_MAX_PER_LOOP: int | None = None
    # ★席の経済のガード（Phase 1 の検死で判明・FableA所見(2)の実装）：
    #   Phase 0 §4 は「押し出される席」を**3席のうち最も点数の低い席**として測っていた。
    #   ところが逐次決定では「その席が今打とうとしていた手」しか押し出せない＝実測では
    #   **100点の暗躍禁止（敗北板の防御）を1.0点の退避で押し出していた**（random_FS s6/s10）。
    #   この値を超える手は押し出さない（None＝ガードなし＝初版の挙動）。
    #: ★掃引で選んだ既定＝80.0（None＝ガードなし）。
    B100_MAX_DISPLACED: float | None = 80.0
    #: ★押し出してはいけないカード種別（B-99/B-94 の「状態依存」＝点数が安くても
    #   **翌ターン以降の防御の前提**になっている投資）。空＝ガードなし。
    #   実測（Phase 1 の検死）：ガードを付けてなお残る退行は全て
    #   **友好+1（18〜26点）を押し出した局**だった＝点数はその札の option value を測っていない。
    B100_PROTECT_CLASSES: tuple = ("友好", "不安")

    # ------------------------------------------------------------------
    # ★B-100 Phase 2（2026-07-29）：席の割り当てを**3席まとめて解く**経路。
    #   正典＝`docs/監査_B100_Phase2_席の同時割り当て_2026-07-29.md`、実装＝`agents/b100_alloc.py`。
    #   Phase 1 の席ごとの貪欲な上書き（`b100_mix.forced_pick`）を置き換える。
    #   **既定 OFF**＝`B100_MIX` が False なので、この経路も呼ばれない＝挙動 bit 不変。
    # ------------------------------------------------------------------
    #: ★Phase 2 の掃引で選んだ既定＝True（`B100_MIX` が False なので**発火はしない**）。
    #  False にすると Phase 1 の席ごとの貪欲な上書き（`b100_mix.forced_pick`）に戻る。
    B100_JOINT: bool = True         # True＝Phase 2 の同時割り当てを使う（False＝Phase 1 経路）
    #: 強制の資格。"theta"＝従来どおり θ／鉄則①で絞る（保護は全 fatal×defendable に効く）／
    #  "all"＝設計2の literal＝fatal×defendable なら実在度に関わらず強制対象。
    B100_FORCE_GATE: str = "theta"
    B100_SPARE_LAST: bool = True    # 設計4＝最終席から奪わない
    #: 強制した席は `_turn_plan` の割当を**消費する**（Phase 1 の欠陥2の是正）。
    #  False＝Phase 1 と同じ＝計画が1つ後ろへずれて末尾の1手が落ちる。
    B100_CONSUME_PLAN: bool = True
    #: Phase 2 では Phase 1 のガード2本（MAX_DISPLACED／PROTECT_CLASSES）を**使わない**
    #  （FableA所見＝あれは「別の制約を覆っている手を避ける」の粗い代理であり、
    #   設計3が正しく効けば不要になるはず）。True にすると対照として復活する。
    B100_JOINT_GUARDS: bool = False
    #: ★1ループで絶対防御に使ってよい席の総数（None＝無制限）。Phase 2 の検死で、
    #  残った退行は「同じループで2席」または「毎ループ1席を延々と払い続ける」形だった
    #  ＝`B100_MAX_PER_LOOP`（負け筋**ラベル別**の回数上限）では切れない帯を狙う狭い述語。
    B100_MAX_SEATS_PER_LOOP: int | None = None
    #: ★「3席まとめて解ける」のは **3席分の意図が既知のとき**だけ、という制限。
    #  `_turn_plan` が無いターンは各席が**逐次に貪欲**に決めるので、先の席の対象を変えると
    #  後続席の再採点が変わる（実測 `random_BTX` s4 L3D2＝p3 を 神社 から外した結果、
    #  最終席 p1 が 神社 を取り、`移動禁止→教師` が落ちて別のキャラが死んだ）。
    #  True＝`_turn_plan` があるターンだけ強制する（＝後続席の意図が読めるときだけ払う）。
    #  ★Phase 2 の掃引で選んだ既定＝True（両ベンチ per-game 退行ゼロの唯一の点）。
    #  ★Phase 3 で **False**：この門は「取る席＝落ちる需要」が成立する条件そのものだったが、
    #  99席中78席を落とす最大の門でもあった。機序側で潰す述語（`B100_NOPLAN_LAST`）を入れて解放する。
    B100_REQUIRE_PLAN: bool = False
    #: ★Phase 3：計画が無いターンは**最終席でだけ払う**（最終席には後続席が無い＝
    #  「取った席と落ちる需要がずれる」カスケードが定義上起きない）。門で隠すのでなく機序で潰す側。
    B100_NOPLAN_LAST: bool = True
    #: ★Phase 3：折り手そのものが**別の2人きりを自作する**形を弾く（新発見の欠陥）。
    #  実測＝病院に{刑事,男子学生,SK候補}の3人。`移動←→→刑事` は kp_sk(刑事) を折るが、
    #  残った{男子学生,SK}が2人きりになり**同ターンに男子学生が死ぬ**（off では起きない）。
    #  ＝従来の「空振りでない」判定は**ゼロ効果しか見ておらず負の効果を見ていなかった**。
    B100_SELF_HARM: str | bool = "strict"

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
        self._b100_placed: set = set()   # ★B-100：このターンに自チームが置いた手（既定OFF）
        self._b100_seats: int = 0        # ★B-100：このターンに絶対防御で使った席数
        self._b100_log: list = []        # ★B-100：介入の記録（シャドー実行の per-game 集計用）
        self._b100_loop_hits: dict = {}  # ★B-100：(loop, 負け筋ラベル) -> 覆った回数

    def _plan_coeffs(self, card: str) -> tuple | None:
        """B-99：そのカードの加点係数 (bonus, hot, hot_p, weak_scale)。None＝加点対象外。

        移動禁止＝従来の PLAN_BONUS/PLAN_HOT/PLAN_HOT_P（weak_scale=1）。
        それ以外は `PLAN_CLASS_COEFFS`（既定 空＝加点しない＝現行と一致）。"""
        if card == "移動禁止":
            return (self.PLAN_BONUS, self.PLAN_HOT, self.PLAN_HOT_P, 1.0)
        return self.PLAN_CLASS_COEFFS.get(_plan_card_class(card))

    def _defense_plan_recs(self, view: dict, options: list) -> dict:
        """防御プランナー（agents/defense_plan）が「負け筋を最安で折る手」に選んだ
        options の {(card,target,target_kind): 加点値}（種別ごとの係数を適用済み）。
        加点対象が無ければ計算を省く。belief 未整備/例外時は空＝加点なし（advisory）。"""
        if (not self.PLAN_BONUS and not self.PLAN_CLASS_COEFFS) or self._belief is None:
            return {}
        try:
            from .defense_plan import plan_for_belief
            _threats, plan = plan_for_belief(
                view, self._belief, options=options,
                initial_areas=self._initial_area_map(view))   # ★B-103 論点B
            if B100_HOOK is not None or self.B100_MIX:
                # ★B-100：Phase 0 は計測専用（既定 None＝不実行）／Phase 1 は混合AIの入力。
                self._b100_plan = (_threats, plan)
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
            #   従来の実効範囲に保つ。移動への加点は独立の調整課題として別途掃引する
            #   （B-99・2026-07-29 に実施＝下の PLAN_CLASS_COEFFS 経由・既定は空のまま）。
            # ★B-99：脅威種別で絞る種別があるときだけ「その手が覆った脅威」を引く
            #   （_pick_for＝plan.covered のラベル照合＝表示層と同じ単一ソース）。
            kinds_of: dict = {}
            if self.PLAN_CLASS_KINDS:
                from .defense_plan import _pick_for
                for _t in _threats:
                    _b = _pick_for(plan, _t)
                    if _b is not None:
                        kinds_of.setdefault(
                            (_b.card, _b.target, _b.target_kind), set()).add(_t.kind)
            recs: dict = {}
            for b in plan.picks:
                coef = self._plan_coeffs(b.card)
                if coef is None:
                    continue
                # ★DP-5：自滅回避のガード。暗躍禁止は1ターン1枚（2枚目は自滅＝KB: 10）で、
                #   `_base_score` が2枚目に PRIORITY["自滅回避"]=-100 を返して弾いている。
                #   プランナー加点はその素点に**足し算**で乗るため、加点対象に暗躍禁止を
                #   入れると「2枚目の暗躍禁止」を押し上げてしまう経路が生まれる。
                #   この席が既に暗躍禁止を使い切っているターンでは加点しない
                #   （加点対象に暗躍禁止が無い設定では no-op＝挙動 bit 不変）。
                if b.card == "暗躍禁止" and self._kinshi_used:
                    continue
                key = (b.card, b.target, b.target_kind)
                allow = self.PLAN_CLASS_KINDS.get(_plan_card_class(b.card))
                if allow is not None and not (kinds_of.get(key, ()) & set(allow)):
                    continue          # 許可した負け筋を折る手ではない＝加点しない
                bonus, hot, hot_p, weak = coef
                v = hot if plan.pick_heat.get(key, 0.0) >= hot_p else bonus
                if not b.robust:
                    v *= weak
                recs[key] = max(recs.get(key, v), v)
            return recs
        except Exception:
            return {}

    # -- ★B-100：制約充足（絶対防御）の1本道 --------------------------------

    def _b100_force(self, view: dict, options: list[dict],
                    score=None) -> dict | None:
        """この席で「絶対防御」として強制する option（無ければ None＝従来どおり点数で決まる）。

        入力は `_defense_plan_recs` が既に作った脅威リスト（＝**計算を増やさない**）。
        判定の中身は `agents/b100_mix.forced_pick`（Phase 0 の実測に基づく述語の単一ソース）。
        ★provenance＝採用した option に `prov="b100"` を1つ立てるだけ（表示層まで1本道で運ぶ）。
        例外は握り潰して None＝**絶対防御が失敗しても対局は従来どおり進む**（advisory 退行）。
        """
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            return None
        if self.B100_JOINT:      # ★Phase 2：席の割り当てを3席まとめて解く
            return self._b100_force_joint(view, options, stash[0], score)
        # ★席の経済のガード：この席が今打とうとしている手が高価なら、絶対防御でも奪わない。
        _nat = _nat_s = None
        if score is not None:
            try:
                _nat = max(options, key=score)
                _nat_s = float(score(_nat))
            except Exception:
                _nat = _nat_s = None
        if (self.B100_MAX_DISPLACED is not None and _nat_s is not None
                and _nat_s > self.B100_MAX_DISPLACED):
            return None
        if (self.B100_PROTECT_CLASSES and _nat is not None
                and _plan_card_class(_nat["card"]) in self.B100_PROTECT_CLASSES):
            return None
        try:
            from .b100_mix import forced_pick
            n_prot = sum(1 for p in view.get("placements", []) or []
                         if p.get("owner") != "mastermind")
            _lim = self.B100_MAX_PER_LOOP
            _loop = view.get("loop")
            blocked = (frozenset() if _lim is None else
                       frozenset(lb for (lp, lb), c in self._b100_loop_hits.items()
                                 if lp == _loop and c >= _lim))
            got = forced_pick(
                self, view, options, stash[0],
                theta=self.B100_THETA, iron_prob=self.B100_IRON_PROB,
                placed=getattr(self, "_b100_placed", set()),
                seats_used=getattr(self, "_b100_seats", 0),
                n_prot_placed=n_prot,
                max_seats=self.B100_MAX_SEATS,
                max_seats_2x100=self.B100_MAX_SEATS_2X100,
                seat_policy=self.B100_SEAT_POLICY,
                blocked_labels=blocked)
        except Exception:
            return None
        if got is None:
            return None
        opt, threat, reason = got
        self._b100_seats = getattr(self, "_b100_seats", 0) + 1
        _k = (view.get("loop"), threat.label)
        self._b100_loop_hits[_k] = self._b100_loop_hits.get(_k, 0) + 1
        # ★CF帰属の材料（B-92 の作法）：この席で**押し出した手**とその点数を必ず記録する。
        #   「反転した」ことと「何が効いたか」は別＝差分で確かめるための1次データ。
        _natlab = _forced_s = None
        if _nat is not None:
            try:
                _natlab = f"{_nat['card']}→{_nat['target']}"
                _forced_s = round(float(score(opt)), 1)
            except Exception:
                pass
        self._b100_log.append({"loop": view.get("loop"), "day": view.get("day"),
                               "seat": view.get("seat"), "kind": threat.kind,
                               "label": threat.label, "prob": round(threat.prob, 4),
                               "card": opt["card"], "target": opt["target"],
                               "target_kind": opt.get("target_kind"),
                               "displaced": _natlab,
                               "displaced_score": (None if _nat_s is None
                                                   else round(_nat_s, 1)),
                               "forced_score": _forced_s,
                               "reason": reason})
        opt["prov"] = "b100"     # ★provenance（表示層＝board_viz の緑枠）へ運ぶ唯一のタグ
        return opt

    # -- ★B-100 Phase 2：席の割り当てを3席まとめて解く ----------------------

    def _b100_force_joint(self, view: dict, options: list[dict], threats,
                          score=None) -> dict | None:
        """Phase 2 経路。判定の中身は `agents/b100_alloc.allocate`（述語の単一ソース）。

        Phase 1 との違いは3点（いずれも §11 の機序に対応）：
          1. 「押し出す手」を **`_turn_plan` の割当を含む実際の予定手**で見る（点数最大ではない）。
          2. **予定手が既に制約を折っている席は使わない**（支払うのは何も担っていない席）。
          3. 強制した席は **`_turn_plan` を消費する**（計画が後ろへずれて末尾が落ちるのを止める）。
        """
        _nat = _nat_s = None
        if self.B100_JOINT_GUARDS and score is not None:
            # 対照実験用にだけ Phase 1 のガード2本を復活させる（既定は使わない）。
            try:
                _g = max(options, key=score)
                if (self.B100_MAX_DISPLACED is not None
                        and float(score(_g)) > self.B100_MAX_DISPLACED):
                    return None
                if (self.B100_PROTECT_CLASSES
                        and _plan_card_class(_g["card"]) in self.B100_PROTECT_CLASSES):
                    return None
            except Exception:
                pass
        if self.B100_REQUIRE_PLAN and not getattr(self, "_turn_plan", None):
            return None
        if self.B100_MAX_SEATS_PER_LOOP is not None:
            _seen = getattr(self, "_b100_loop_seats", None)
            if _seen is None:
                _seen = self._b100_loop_seats = {}
            if _seen.get(view.get("loop"), 0) >= self.B100_MAX_SEATS_PER_LOOP:
                return None
        try:
            from .b100_alloc import allocate
            n_prot = sum(1 for p in view.get("placements", []) or []
                         if p.get("owner") != "mastermind")
            _lim = self.B100_MAX_PER_LOOP
            _loop = view.get("loop")
            blocked = (frozenset() if _lim is None else
                       frozenset(lb for (lp, lb), c in self._b100_loop_hits.items()
                                 if lp == _loop and c >= _lim))
            got = allocate(
                self, view, options, threats,
                theta=self.B100_THETA, iron_prob=self.B100_IRON_PROB,
                force_gate=self.B100_FORCE_GATE,
                placed=getattr(self, "_b100_placed", set()),
                seats_used=getattr(self, "_b100_seats", 0),
                n_prot_placed=n_prot,
                max_seats=self.B100_MAX_SEATS,
                max_seats_2x100=self.B100_MAX_SEATS_2X100,
                spare_last=self.B100_SPARE_LAST,
                score=score, blocked_labels=blocked)
        except Exception:
            return None
        if got is None:
            return None
        opt, threat, reason, intent = got
        # ★欠陥2の是正：強制した席は自分の計画割当を消費する＝残りは後続席へそのまま渡る。
        if self.B100_CONSUME_PLAN and getattr(self, "_turn_plan", None):
            self._turn_plan.pop(0)
        self._b100_seats = getattr(self, "_b100_seats", 0) + 1
        _k = (view.get("loop"), threat.label)
        self._b100_loop_hits[_k] = self._b100_loop_hits.get(_k, 0) + 1
        _seen = getattr(self, "_b100_loop_seats", None)
        if _seen is None:
            _seen = self._b100_loop_seats = {}
        _seen[view.get("loop")] = _seen.get(view.get("loop"), 0) + 1
        _natlab = _forced_s = None
        if intent is not None:
            try:
                _natlab = f"{intent['card']}→{intent['target']}"
                _nat_s = round(float(score(intent)), 1) if score else None
                _forced_s = round(float(score(opt)), 1) if score else None
            except Exception:
                pass
        self._b100_log.append({"loop": view.get("loop"), "day": view.get("day"),
                               "seat": view.get("seat"), "kind": threat.kind,
                               "label": threat.label, "prob": round(threat.prob, 4),
                               "card": opt["card"], "target": opt["target"],
                               "target_kind": opt.get("target_kind"),
                               "displaced": _natlab, "displaced_score": _nat_s,
                               "forced_score": _forced_s, "reason": reason})
        opt["prov"] = "b100"     # ★provenance（緑枠）＝新経路でも同じタグ1本道
        return opt

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
            # ★B-65：このターンに自チームが移動禁止でピンしたキャラ（席間協調）。
            #   ピン済み＝mmの引き抜き移動は打ち消し済み＝退避判定の「動かされうる
            #   同居者」から除外する（3日BTX#0実測＝護衛ピンで固定済みの巫女を
            #   引き抜きリスク扱いして幻想を退避→自分たちで巫女2人きりを作った）。
            self._pinned_today: set[str] = set()
            self._genso_evac_today = False  # B-65退避もターン1席まで＋護衛ピンと排他
            # ★2段化（2026-07-09）：ターン先頭席が3席分の手を一括計画する。
            #   貪欲な逐次maxは「4需要vs3席」の取捨（どの需要を今日捨てるか）を
            #   調停できない（5日級FS_6実測＝ピン/冷却/分離/暗躍禁止のナイフエッジ）。
            self._turn_plan: list | None = None
            # ★B-100（既定OFF）：このターンに自チームが置いた手と、絶対防御で使った席数。
            self._b100_placed: set = set()
            self._b100_seats = 0
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

    #: カルティスト系の閾値ゲート（B-101 較正チケットで定数化＝掃引可能にした。
    #:   既定値は定数化前のリテラルと同一＝bit-for-bit 不変）。
    #:   `_CULT_SUSPECT_P` ＝確信（引き剥がし・搬入路の本命）／
    #:   `_CULT_CAND_P` ＝弱候補（(c2b) カルティスト剥がし_候補 72.0 の発火条件）／
    #:   `_CULT_MAYBE_P` ＝「beliefで除外されていない」の下限（移動封じピンの母集合。strict >）。
    _CULT_SUSPECT_P = 0.7
    _CULT_CAND_P = 0.25
    _CULT_MAYBE_P = 0.1

    #: ★A-78 アブレーション用トグル。False＝旧挙動へ完全復帰（行方不明を「任意ボードへ+1」と数える）。
    #:   B-105 の要求（mm側と主人公側の寄与を分離して計上する）に応えるための切替口。
    A78_FORBIDDEN_AWARE = True

    def _a78_missing_feed_boards(self, view: dict, day: int) -> frozenset | None:
        """★A-78：`day` の事件「行方不明」が暗躍1を置き**うる**ボードの集合。

        条文＝「犯人を任意のボードに移動させる。その後、犯人のいるボードに暗躍1」（KB: 40:153/50）。
        E-2 の公式裁定により、この移動先に**犯人の禁止エリアは選べない**（KB: 00 禁止エリアの定義／
        40 事件まわりの注意）＝供給先は犯人が行けるボードに限られる。

        ★主人公は**犯人を知らない**（犯人は非公開シート＝KB: 00:76）＝
          `belief.culprit_candidates()`（公開情報だけから作られる候補集合）の**和**を取る＝
          「候補が1人でも行けるボードは脅威のまま」＝**安全側**（脅威を過小評価しない）。
        ★候補が空／取得不能＝**絞れない**＝`None` を返し、呼び出し側は従来どおり全板を数える。
        ★禁止エリアの解除（医者能力3・女の子能力1）は公開履歴から反映される
          （`sim.state.missing_incident_boards_from_view` が単一ソース）＝神視点は使わない。
        """
        if not self.A78_FORBIDDEN_AWARE:
            return None
        cands = (getattr(self, "_culprit_cands", None) or {}).get(day) or ()
        if not cands:
            return None
        feed: set = set()
        for cn in cands:
            feed |= set(missing_incident_boards_from_view(view, cn))
        return frozenset(feed)

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
        # ★B-66(1)：因果の糸（ルールX・KB 50:85）＝「各ループの開始時、ひとつ前のループ
        #   終了時に友好カウンターが置かれていたキャラクター全員に不安カウンターを2つ置く」。
        #   【強制】効果＝ループ開始の不安+2の観測/不発で在/不在が確定する（belief._ito_signal）
        #   ＝濃厚なら「ループ終了時に友好が残る」投資は次ループ開始+2の自傷を伴う。
        self._ito_p = sum(p for (_ry, _rxs), p in rules_m.items()
                          if "因果の糸" in _rxs)
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
                # ★A-78（2026-07-30）：行方不明が暗躍を置けるのは**犯人が移動できるボードだけ**
                #   （E-2 公式裁定／KB: 00 禁止エリアの定義・40 事件まわりの注意）＝
                #   「任意のボードへ+1」ではない。∴ 敗北板が**その日の犯人候補の誰も行けない板**
                #   なら、この事件はその板の脅威に**ならない**（例＝犯人サラリーマン（禁止＝学校）
                #   × 守るべき場所（学校≥2））。
                #   ★主人公は犯人を知らない＝**候補が1人でも行けるなら脅威のまま**（安全側）。
                #     候補が取れない日（＝絞れていない）も従来どおり全板を脅威と見なす。
                #   ★ボードX系（復讐者の灯火／巨大時限爆弾X）は「どの板がXか」を主人公が
                #     知らない（rule_y_board_x は脚本家view専用）＝絞れない＝従来どおり数える。
                _feed0 = self._a78_missing_feed_boards(view, d)
                _p_board0 = sum(
                    pv for (ry0, _x0), pv in rules_m.items()
                    if (ry0 in ("復讐者の灯火", "巨大時限爆弾Xの存在")
                        or (ry0 in _A78_FIXED_DEFEAT_BOARD
                            and (_feed0 is None
                                 or _A78_FIXED_DEFEAT_BOARD[ry0] in _feed0))))
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
        # ★B-77（検死3 §T-B）：押し切りへの前日冷却エンジンの文脈＝「明日が自傷系押し切り
        #   事件（蝶の羽ばたき/自殺）×犯人が1人に確定×臨界≤3×ML自己ポンプ圏×危険実証」。
        #   当日は完全列挙でmate（A-63の会計＝臨界＋当日冷却容量まで積まれる）＝前日に
        #   ①不安除去役の♡2解禁（友好禁止の裏＝二系統の後出し）②除去役の犯人同エリア化
        #   ③犯人の前日カバー（既存76）で -2/日の冷却圏を作るのが唯一の対抗。
        self._b77_prep: str | None = None   # 明日の押し切り犯人（成立時のみ）
        _tmrw = view.get("day", 0) + 1
        if any(i.get("day") == _tmrw and i.get("name") in ("蝶の羽ばたき", "自殺")
               for i in view.get("incidents", [])) \
                and self._incident_danger.get(_tmrw, 0.0) >= 60.0:
            _c77 = cand.get(_tmrw, set())
            if len(_c77) == 1:
                _culp77 = next(iter(_c77))
                _th77 = unrest_threshold_of(_culp77)
                _cc77 = self._alive(view, _culp77)
                if _cc77 and _th77 and _th77 <= 3:
                    _ml77, _mlp77 = self._belief.most_likely_role("ミスリーダー")
                    _mlc77 = self._alive(view, _ml77) if _ml77 else None
                    if _mlp77 >= 0.55 and (_ml77 == _culp77 or (
                            _mlc77 and _mlc77["area"] == _cc77["area"])):
                        self._b77_prep = _culp77
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
        # ★B-57（2026-07-26 検死＝3日級残敗4局）：TTハーツ仕上げの「薄事前」証拠。
        #   L1 は p_future が事前(≈0.24)のままでガード（p≥0.7 or tt_p≥0.5）が組めず、
        #   最終日の TT 任意敗北（友好≤2・50:128）を無防備に受けていた
        #   （btx_future#0 L1D3 検死＝友好+1 一枚の defense_exists を不安-1 の空振り席に
        #   誤配分）。証拠＝「蝶の羽ばたきが事件表にある」（公開情報。蝶の発生が敗北条件に
        #   なるのは未来改変プランのみ＝TT必在の強い示唆）or p_future≥0.5（観測後ループ）。
        self._tt_thin_evidence = self._B57_THIN_FINISH and (
            p_tt_rule >= self._B57_PFUTURE_MIN
            or any(i.get("name") == "蝶の羽ばたき"
                   for i in view.get("incidents", [])))
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
        self._cultist_suspects = {n for n, d in marg.items()
                                  if d.get("カルティスト", 0) >= self._CULT_SUSPECT_P}
        # 弱い候補（すり抜け観測の絞り込み用＝剥がして次のすり抜けを見れば候補が割れる）
        self._cultist_cands = {n for n, d in marg.items()
                               if d.get("カルティスト", 0) >= self._CULT_CAND_P}
        # ★B-75/B-74：すり抜け実証の交差集合。各すり抜け（自陣の暗躍禁止1枚を暗躍+が
        #   素通り）＝「その顔ぶれの中にカルティストが居る」**証明**（belief.
        #   _cultist_constraints と同一の健全条件＝自滅ルール除外済み）。全事象の∩が
        #   2人以下に絞れた時だけ有効化（広い∩は誤爆源＝空のまま）。巫女の神社常駐で
        #   ∩が{医者,巫女}から割れず周辺確率0.7に恒久に届かないプラトー（B-52/55/59と
        #   同族）の解毒。**使用側は必ず「盤ロック外×当日冷却非競合」ゲートを通す**
        #   （露見実験の設計＝下の _b75_exposure_ok）。
        self._b74_smuggle_inter: frozenset = frozenset()
        if self._B74_SMUGGLE_PROOF:
            _b74_sets = _cultist_constraints(view.get("history", []))
            if _b74_sets:
                _b74_inter = frozenset.intersection(*_b74_sets)
                if 0 < len(_b74_inter) <= 2:
                    self._b74_smuggle_inter = _b74_inter
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
        # ★B-71：キラー疑いが**全員死亡**しているループではキラー線は死んでいる
        #   （死亡のリセットはループ開始時＝このループ中は復活しない）＝KPガードは空振り。
        #   完全情報basic実測＝D2にキラー刑事が死んだ後もD3の暗躍禁止→KPが100のままで、
        #   先席が急所を消費し（主人公は同一対象に1枚）、後席の配達ピンが打てなかった。
        _killer_live = (any(self._alive(view, k) for k in self._killer_suspects)
                        if self._B71_KP_GUARD_LIVENESS
                        else bool(self._killer_suspects))
        self._kp_guard = 100.0 if (_killer_live or p_keiyaku > 0
                                   or _remote_pending) else 5.0
        # ★情報収集プレイ：役職開示の情報価値＝Gini不純度（開示後の期待残存世界がW·Σp²）
        self._gini = {n: self._belief.role_gini(n) for n in self._belief.cast}
        self._b86_build_goodwill_void(view)
        self._b109_build_futile(view)     # ★B-109 論点①（`_b86_gw_keep` の後で組む）
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
        # ★B-66(2)：観測実績ゼロが続く板（5日級検死#13 §6＝囮吸引）。板系の敗北条件
        #   （守るべき場所/封印されしモノ/board_x）は全て「ループ終了時に暗躍≥2」（KB 40/50）
        #   ＝敗北ループ終了時に<2だった板はその敗北の原因ではなかった、が公開情報から言える。
        #   敗北2回以上の全てで<2が続く板は減点対象（ボード封じ_未実証）。
        self._b66_unproven_boards = self._b66_unproven_from_history(view)
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
        # ★B-78（検死3 T-C・2026-07-27）：kp_doomed は「最後の戦いの弾込め」を正当化する
        #   プロキシだが、**FSには最後の戦いが無い**（40:21-23＝全ループ敗北で直ちに脚本家
        #   勝利）＝FSでの弾込め姿勢は無価値どころか防衛放棄。set は公開情報＝FSでは常にFalse
        #   （random_FS#4実測＝L3以降 kp_doomed=True で犠牲者保護が緩み、KP防衛を組めず唯一のloss）。
        if view.get("set", "FS") == "FS":
            self._kp_doomed = False
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
        # ★B-59：ボードへのmm能力供給イベントの「その場に居た顔ぶれ」（公開・全ループ累積）
        #   ＝クロマクの同エリア要求から供給源候補を絞る自明情報（belief と同じ材料）。
        #   ★B-59b（5日級検死 random_BTX#10・2026-07-27）：不穏な噂（1/loop・任意ボード・
        #   同エリア要求なし）由来の観測を供給源の実証に混ぜない。能力名は非公開だが、
        #   **同一ループ×同一ボードに2回以上**の供給があれば少なくとも1回は噂でない
        #   （噂は1/loopで1回きり）＝クロマク型（毎日再現可能な能力供給）の実証とみなせる。
        #   1回きりの観測は噂と識別不能＝証拠にしない（健全側＝B-59が沈黙してB-59以前の
        #   動作に戻るだけ）。#10実測＝噂→神社(1回/loop)の誤実証で毎ループ2席を
        #   無関係キャラ（うち1人は真フレンド妹）の退避87に浪費し、必須の犯人冷却76が席負け。
        #   ※`_mm_pump_boards`（暗躍禁止の実効割引に使用）は従来どおり噂も含む＝意味が別。
        self._mm_pump_board_present: dict[str, set] = {}
        _AREAS = ("病院", "神社", "都市", "学校")
        _pump_evs: dict[tuple, list] = {}   # (loop, board) -> [present顔ぶれ, ...]
        for e in view.get("history", []):
            if (e.get("event") == "anyaku" and e.get("phase") == "mastermind_ability"
                    and e.get("delta", 0) > 0 and e.get("target") in _AREAS):
                self._mm_pump_boards.add(e.get("target"))
                _pump_evs.setdefault((e.get("loop"), e.get("target")), []).append(
                    tuple(e.get("present", ())))
            if e.get("loop") != view["loop"]:
                continue
            if e.get("event") == "cards_revealed":
                for p in e.get("placements", []):
                    if p.get("owner") == "mastermind" and p.get("card") == "友好禁止":
                        self._gw_blocked.add(p.get("target"))
            elif (e.get("event") == "anyaku" and e.get("phase") == "mastermind_ability"
                    and e.get("delta", 0) > 0):
                self._mm_pump_victims.add(e.get("target"))
        self._mm_pump_board_present = self._b59b_pump_present(
            _pump_evs,
            rumor_possible=self._b70_rumor_possible(),
            rumor_spent_loops=self._b70_rumor_spent_loops(view))

    def _b70_rumor_possible(self) -> bool:
        """B-70(1)：belief上「不穏な噂∈ルールX」の組が生き残っているか。

        mm能力フェイズの板暗躍ソースはクロマクと不穏な噂の2つだけ（00:109・10:65・
        60:A16）＝噂の組が全滅していれば単発の板供給観測もクロマク由来と確定できる。
        健全側＝belief不整合/未構築時は True（噂ありうる＝従来どおり2回実証を要求）。"""
        if not self._B70_XLOOP or self._belief is None:
            return True
        try:
            rm = self._belief.rule_marginals()
        except Exception:
            return True
        if not rm:
            return True   # 可能世界0（belief崩壊）＝判定不能＝健全側
        return sum(p for (_ry, rxs), p in rm.items()
                   if "不穏な噂" in rxs) > self._B70_RUMOR_P_MAX

    def _b70_rumor_spent_loops(self, view: dict) -> set:
        """B-70(2)：不穏な噂の消費が**確定**したループ集合。

        present==[]（盤面に生存キャラ不在）の板供給はクロマク（同エリア/自ボード要求）では
        説明不能＝噂で確定（belief の AIC seed8 証拠と同根・公開情報）。噂は1ループ1回制限
        （40:§不穏な噂／50:§不穏な噂）＝消費確定ループの**他の板供給（present非空）は
        クロマク由来と確定**する。"""
        if not self._B70_XLOOP:
            return set()
        _AREAS = ("病院", "神社", "都市", "学校")
        return {e.get("loop") for e in view.get("history", [])
                if e.get("event") == "anyaku"
                and e.get("phase") == "mastermind_ability"
                and e.get("delta", 0) > 0 and e.get("target") in _AREAS
                and e.get("present") == []}

    @staticmethod
    def _b59b_pump_present(pump_evs: dict, rumor_possible: bool = True,
                           rumor_spent_loops: set = frozenset()) -> dict:
        """B-59b：噂と識別可能な板供給観測だけを供給源実証に採用する。

        pump_evs＝{(loop, board): [present顔ぶれtuple, ...]}。同一ループ×同一ボードに
        2回以上の供給がある組だけ採用（不穏な噂は1/loop＝2回以上は噂だけでは説明できない
        ＝クロマク型の実証）。1回きりは噂と識別不能＝証拠にしない（健全側）。

        ★B-70（クロスループ集約・2026-07-27）＝単発観測を実証に昇格できる2ケースを追加：
          (1) rumor_possible=False＝belief上「不穏な噂∈ルールX」の組が全滅＝mm能力フェイズの
              板暗躍ソースはクロマクのみ（00:109）＝単発でもクロマク確定。
          (2) loop ∈ rumor_spent_loops＝そのループの噂消費が確定済み（present==[]の板供給＝
              クロマクでは説明不能）＝同ループの**他の**板供給（present非空）はクロマク確定。
              空presentイベント自身は噂側＝候補集合には何も足さない（union(空)=∅）。
        ★「同一盤×別ループの単発反復」だけでは昇格しない：噂は**1ループ1回制限**
          （40:§不穏な噂／50:§不穏な噂）＝毎ループ同じ盤に打ち続けられる＝ループ跨ぎの
          反復は噂だけで説明可能＝条文上識別不能（B-59bの教訓＝偶然の的中を採らない）。"""
        out: dict[str, set] = {}
        for (_lp, _bd), _plist in pump_evs.items():
            if len(_plist) >= 2 or not rumor_possible:
                for _pr in _plist:
                    out.setdefault(_bd, set()).update(_pr)
            elif _lp in rumor_spent_loops:
                # 噂消費確定ループ＝present非空の板供給はクロマク由来と確定
                for _pr in _plist:
                    if _pr:
                        out.setdefault(_bd, set()).update(_pr)
        return out

    #: B-70トグル（Falseで B-59b の「同一ループ2回」実証のみ＝旧挙動へ完全復帰）
    _B70_XLOOP = True
    #: B-70(1)＝噂ルール組の残存確率がこの値以下なら「噂消滅」とみなす（0＝完全消滅のみ）
    _B70_RUMOR_P_MAX = 1e-9

    # -- 情報収集プレイ（能力の情報価値と友好投資先） ------------------------

    #: ★B-86'：「友好無視を持つ配役だと判明した」とみなす周辺確率の下限。
    #  ★確率で切らない：既定は**実質確定**（0.999）。`rules/20:25`＝空撃ちは絶対友好無視の
    #  判別に使える定番テク＝**未確定の相手への友好投資には情報価値がある**＝確定まで切らない。
    _B86_IGNORE_P: float = 0.999
    #: 論点C（医者×友好無視＝相手に不安+1を渡す・`rules/60:81` B-8）の閾値。
    _B86_DOCTOR_P: float = 0.999
    #: 「役職はもう分かっている」とみなす Gini の上限（`_ability_value` の val=2.0 側と同値）。
    _B86_GINI_EPS: float = 0.02
    #: ★論点別トグル（掃引・切り分け用。False で当該論点だけ旧挙動へ戻る）。
    _B86_A_ON: bool = True       # A  自身開示済み（情報が枯れている）
    _B86_A_VALUE_FIX: bool = True  # A  _ability_value の「自身開示は自分の Gini」修正
    _B86_B1_ON: bool = True      # B-1 拒否を観測した相手
    _B86_B2_ON: bool = True      # B-2 友好無視を持つ配役が確定
    _B86_C_ON: bool = True       # C  医者×友好無視＝相手に不安+1を渡す（有害）

    # -- B-109 論点①：このターン何も変えない手 --------------------------------
    #: ★論点別トグル（掃引・切り分け用。False で当該論点だけ旧挙動へ戻る）。
    _B109_F1_ON: bool = True     # F1 最終日の友好+ で新規解禁が無い
    _B109_F2_ON: bool = True     # F2 このループに残り事件が無い不安-1
    _B109_F3_ON: bool = True     # F3 不安臨界0のキャラへの「事件を起こす実験」
    #: 不安の閾値を参照する役職（BTX のみ・`rules/50:159-160`＝メインラバーズ）。
    _B109_UNREST_ROLES: frozenset = frozenset({"メインラバーズ"})
    #: 不安の閾値を参照するルールX（BTX のみ・`rules/50:79-81`＝妄想拡大ウイルス）。
    _B109_UNREST_RULES: frozenset = frozenset({"妄想拡大ウイルス"})
    #: ループ終了時の友好を参照するルールX（`rules/50:85`＝因果の糸＝次ループに不安+2）。
    _B109_GW_END_RULES: frozenset = frozenset({"因果の糸"})

    @classmethod
    def _b109_experiment_is_void(cls, tgt: str) -> bool:
        """★B-109 F3：不安を積んで事件の発生/不発を観測する実験（B-16）が
        **原理的に情報ゼロ**な対象か。

        KB 接地（一字一句）：
          - `rules/00_rules_core.md:38`「**不安臨界**：この値**以上**の不安カウンターが
            置かれていると、そのキャラが犯人の事件を起こす可能性がある（＝事件発生条件の一つ）」
          - `rules/30_characters.md:18` 同義／`rules/30:53` 黒猫の不安臨界＝**0**／
            `rules/30:77`「不安臨界0なので、**不安カウンターが無くても**自身が犯人の事件の
            発生条件を満たす」。
        ∴ 不安臨界0＝発生条件が**不安の値に依存しない**＝不安を1つ積んでも
           「発生した／しなかった」の観測は**1ビットも変わらない**＝実験として情報ゼロ。

        ★**過剰に切らない**：本判定が言うのは「**B-16 の実験としての**情報価値がゼロ」
          だけである。不安そのものが無価値という主張ではない
          （妄想拡大ウイルス(X)＝不安3でパーソンがSK化／メインラバーズ＝不安3+暗躍1で
          主人公死亡＝`rules/50:79-81,159-160`。∴ ウイルス試験 `in_virus` は**切らない**）。
        """
        if not cls._B109_F3_ON:
            return False
        return unrest_threshold_of(tgt) == 0

    # -- B-109 論点②／B-110 論点③：手詰まり時の振る舞い ----------------------
    #
    # ★ユーザーFB（手練れ・2026-07-30）：
    #   「負けが決まっているのであれば確実に無意味な行動よりも可能性がある行動をとってほしい。
    #     でたらめにやるなら移動を置いた方が脚本家の事故を誘発できる」（論点②）
    #   「移動禁止が出尽くしてしまったら確率的に移動を置くが正解である。……
    #     焦点の学校にいる人を減らしておくことでカルティスト候補も絞りやすくなる。
    #     やることがないのなら情報がでるようにふるまうべき」（論点③）
    #
    # ★設計＝**床（floor）としてのみ当てる**。定数の合計上限（_B110_IDLE_CAP）は
    #   `_base_score` が返す最小の「実効手」の点（8.0＝B-63 冷却席の譲り）より**厳密に低い**。
    #   ∴ **どんな実効手も押しのけない**＝「負け確判定が誤っていても防衛を捨てない」という
    #   FableA の要件を、係数の上限で**構造的に**保証する（発火条件の巧拙に依存しない）。
    #   発火するのは「盤上の全候補が空振り床(3.5)級」＝**本当にやることが無い席だけ**。
    _B110_IDLE_ON: bool = True
    _B110_IDLE_BASE: float = 4.0        # 空振り床(3.5)のすぐ上＝「無意味より可能性がある」
    _B110_IDLE_LOST: float = 1.0        # 負け確ループ＝脚本家の事故を誘発する価値（論点②）
    _B110_IDLE_FOCUS_OUT: float = 1.5   # 焦点の板から人を1人減らす（防御＋候補の絞り込み）
    _B110_IDLE_INTERFERE: float = 1.0   # mm が今ターン札を置いた対象＝移動の邪魔（確率的）
    _B110_IDLE_INFO: float = 0.5        # 焦点の板に残る役職未確定者1人あたり（情報獲得の近似）
    _B110_IDLE_CAP: float = 7.5         # ★上限（< 8.0＝最小の実効手）

    def _b110_idle_move(self, o: dict, view: dict, ctx, danger_board) -> float | None:
        """手詰まり席で「移動を置く」ことの床。移動カード以外／空振りの移動は None。

        ★情報獲得の項は**近似**である（doc に明記する義務がある）。
        本来は「その手を打った後の可能世界数／エントロピーの減少」を測るべきだが、
        belief の全数再評価を候補×席×ターンで回すのは重い。ここでは
        **焦点の板に立っている役職未確定者の人数**を代理量として使う
        （＝ユーザーの言う「焦点の板にいる人を減らすと候補が絞りやすくなる」）。
        カルティスト／クロマクは「その板に居ること」が能力の前提（`rules/40:86,101`）なので、
        焦点の板の在室者が減るほど**板の暗躍供給に説明を付けられる容疑者が減る**＝
        次の観測で候補が割れやすくなる、という関係に接地している。
        """
        if not self._B110_IDLE_ON:
            return None
        card, tgt, kind = o.get("card"), o.get("target"), o.get("target_kind")
        if kind != "character" or card not in _MOVE_TOGGLE:
            return None
        c = self._alive(view, tgt)
        if not c:
            return None
        # ★空振り／自滅の移動は絶対に持ち上げない（G4 行き先禁止／G5 KPをkill zoneへ／G6 自滅）
        if noop_reason(view, card, tgt, kind, ctx) is not None:
            return None
        dest = _move_dest(c.get("area"), card)
        if dest is None or dest == c.get("area"):
            return None
        s = self._B110_IDLE_BASE
        if getattr(self, "_loop_lost", False):
            s += self._B110_IDLE_LOST
        # mm が今ターン札を伏せた対象＝移動札かもしれない＝重ねれば着地点が変わる
        # （`rules/10`＝同一対象の移動は合成される）。中身は伏せなので**確率的な手**。
        if any(p.get("owner") == "mastermind"
               and p.get("target_kind") == "character"
               and p.get("target") == tgt for p in view.get("placements", [])):
            s += self._B110_IDLE_INTERFERE
        focus = danger_board or getattr(self, "_observed_defeat_board", None)
        if focus and c.get("area") == focus and dest != focus:
            s += self._B110_IDLE_FOCUS_OUT
            # 情報獲得の近似＝焦点の板に残る「役職未確定者」の人数
            n_unknown = sum(1 for x in view.get("characters", [])
                            if x.get("alive") and x.get("area") == focus
                            and x.get("name") != tgt
                            and self._gini.get(x.get("name"), 0.0) > 0.02)
            s += min(1.5, self._B110_IDLE_INFO * n_unknown)
        return min(s, self._B110_IDLE_CAP)

    def _b109_build_futile(self, view: dict) -> None:
        """★B-109 論点①：**このターン置いても帰結が何も変わらない**対象を組む。

        材料はすべて公開情報（事件の予定日・盤面・履歴）＋belief の周辺確率。
        `agents/card_effect.noop_reason` の G9/G10 が単一チョークポイントとして消費する
        （B-28／B-103／B-86' と同じ構造）。

        - **F1**（`gw_final_void`）＝**最終日**に友好+ を置いても、その日の主人公能力フェイズで
          **新たに使える能力が1つも解禁されない**組 (キャラ, step)。
          `rules/20:20,22`（ハート数以上で使用可・使っても減らない・1ループ1回）＋
          `rules/00:86`（ループ開始時に全カウンター除去）から、価値は「今日の解禁」だけ。
          ★TT 可能性が残る対象（`_b86_gw_keep`）は**絶対に入れない**（`rules/50:127-128`）。
          ★因果の糸(X) の可能性があり、かつ**現在の友好が0**の対象は `gw_final_harm`
          ＝「無駄」ではなく**有害**（`rules/50:85`＝ループ終了時に友好があると次ループ不安+2）。
          既に友好1以上なら「置かれていた」判定は変わらない＝害は増えない＝通常の空振り扱い。
        - **F2**（`unrest_void`）＝**このループに残り事件（day ≥ 今日）が1件も無く**、
          不安の閾値を参照する役職/ルールの可能性も無い対象。
          `rules/00:30,38`・`rules/40:157`＝不安の効果は事件の発生条件だけ。FS の役職には
          不安閾値を見るものが無く、BTX だけが妄想拡大ウイルス(X)とメインラバーズを持つ。
        """
        gw_void: set = set()
        gw_harm: set = set()
        unrest_void: set = set()
        marg = self._belief.role_marginals()

        # --- F2 -------------------------------------------------------------
        if self._B109_F2_ON:
            day = int(view.get("day", 1))
            remaining = [i for i in (view.get("incidents") or [])
                         if int(i.get("day", 0)) >= day]
            if not remaining:
                is_btx = view.get("set") == "BTX"
                p_virus = (sum(p for (_ry, rxs), p in self._belief.rule_marginals().items()
                               if any(n in rxs for n in self._B109_UNREST_RULES))
                           if is_btx else 0.0)
                if p_virus <= 0.0:
                    for c in view.get("characters", []):
                        n = c.get("name")
                        if not c.get("alive"):
                            continue
                        # メインラバーズは**自分の**不安3で主人公を殺せる＝本人だけ除外
                        if is_btx and sum(p for r, p in marg.get(n, {}).items()
                                          if r in self._B109_UNREST_ROLES) > 0.0:
                            continue
                        unrest_void.add(n)

        # --- F1 -------------------------------------------------------------
        if self._B109_F1_ON \
                and int(view.get("day", 1)) >= int(view.get("days_per_loop", 99)):
            from sim.abilities import is_implemented
            used = set()
            hist = view.get("history", []) or []
            start = 0
            for i, e in enumerate(hist):
                if e.get("event") in ("loop_start", "loop_begin", "loop_setup"):
                    start = i
            for e in hist[start:]:
                if e.get("event") == "goodwill_used":
                    used.add((e.get("character"), e.get("ability")))
            p_ito = sum(p for (_ry, rxs), p in self._belief.rule_marginals().items()
                        if any(n in rxs for n in self._B109_GW_END_RULES))
            keep = getattr(self, "_b86_gw_keep", set())
            for c in view.get("characters", []):
                n = c.get("name")
                if not c.get("alive") or n in keep:
                    continue
                g = c.get("goodwill", 0) or 0
                abil = [a for a in (goodwill_abilities_of(n) or [])
                        if is_implemented(n, a["name"])]
                for step in (1, 2):
                    if any(g < a["hearts"] <= g + step and (n, a["name"]) not in used
                           for a in abil):
                        continue     # 新規解禁がある＝空振りではない
                    gw_void.add((n, step))
                if p_ito > 0.0 and g == 0:
                    gw_harm.add(n)
        self._b109_gw_final_void = gw_void
        self._b109_gw_final_harm = gw_harm
        self._b109_unrest_void = unrest_void

    def _b86_build_goodwill_void(self, view: dict) -> None:
        """★B-86'：友好+ が**将来にわたって価値ゼロ／有害**と公開情報から示せる対象を組む。

        材料はすべて公開情報（履歴の拒否観測・盤面）＋belief の周辺確率。
        `agents/card_effect.noop_reason` の G8 が単一チョークポイントとして消費する
        （`defense_plan` の折り手生成も同じ述語を見る＝B-28 の構造を踏襲）。

        - **keep**＝タイムトラベラーの可能性が残る対象（`rules/50:127-128`＝最終日に友好2以下だと
          任意敗北を宣言されうる＝友好3以上は**敗北条件の封じ手**）＝**絶対に切らない**。
        - **refused**（論点B-1）＝拒否を観測したキャラ。`rules/20:24`＝拒否できるのは
          **能力を使うキャラ**が友好無視／絶対友好無視を持つ場合のみ＝拒否の観測は保持の**証明**。
          配役はゲーム中固定（`rules/00:76`）＝以後の全ループで同じ。
          ★例外＝`UNREFUSABLE_ABILITY_CHARS`（イレギュラー/ナース/妹/コピーキャット）は
          カード文で「拒否されない」＝そもそも拒否の観測が起きない／起きても意味が違う。
        - **certain**（論点B-2）＝友好無視を持つ配役の周辺確率が実質1。
          ★強さの区別：**絶対**友好無視は【強制】＝規則上ゼロ／通常の友好無視は【任意】＝
          「有能な相手なら必ず拒否する」＝**実質**ゼロ（doc に正直に書くこと）。
        - **exhausted**（論点A）＝自身の役職開示しか持たず、その役職を既に知っているキャラ。
        - **arms**（論点C）＝医者が友好無視を持つ場合、友好2以上で脚本家が脚本家能力フェイズに
          医者の能力（不安±1）を使えるようになる（`rules/60:81` B-8・`rules/20:230-232`）
          ＝**無駄ではなく有害**。標準では医者だけ（軍人・教師は不可）。
        """
        marg = self._belief.role_marginals()
        keep = {n for n, d in marg.items() if d.get("タイムトラベラー", 0.0) > 0.0}
        keep |= set(getattr(self, "_tt_guards", ()) or ())
        refused = {e.get("character") for e in (view.get("history") or [])
                   if e.get("event") == "goodwill_refused" and e.get("character")}
        refused -= UNREFUSABLE_ABILITY_CHARS
        p_ign = {n: sum(p for r, p in d.items() if r in _IGNORE_ROLES)
                 for n, d in marg.items()}
        certain = {n for n, p in p_ign.items()
                   if p >= self._B86_IGNORE_P
                   and n not in UNREFUSABLE_ABILITY_CHARS}
        exhausted = set()
        for n in self._belief.cast:
            abs_n = goodwill_abilities_of(n) or []
            if abs_n and all((n, a["name"]) in _SELF_ROLE_REVEAL for a in abs_n) \
                    and self._gini.get(n, 1.0) < self._B86_GINI_EPS:
                exhausted.add(n)
        arms = ({"医者"} if p_ign.get("医者", 0.0) >= self._B86_DOCTOR_P else set())
        self._b86_gw_keep = keep
        self._b86_gw_refused = (refused - keep) if self._B86_B1_ON else set()
        self._b86_gw_ignore_certain = (certain - keep) if self._B86_B2_ON else set()
        self._b86_gw_info_exhausted = (exhausted - keep) if self._B86_A_ON else set()
        self._b86_gw_arms_mm = (arms - keep) if self._B86_C_ON else set()

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
            # ★B-86'（手練れユーザー指摘）：**自身の役職開示**（サラリーマン／イレギュラー）は
            #   開示対象が**行使キャラ自身**に固定される（`rules/20:167-170` / `20:106-108`）。
            #   ところが投資評価（target=None）は「**全キャラの Gini の最大値**」を使っていたため、
            #   **役職が既に開示されて Gini=0 になったサラリーマンにも、他人の不確かさぶんの
            #   高い価値が付き続けていた**（実測＝gini 0.0 なのに invest 25〜50）。
            #   ＝ユーザーが2度指摘した「すでに能力発動しているサラリーマンへの友好+」の機序。
            #   配役はゲーム中固定＝一度開示されたら以後の全ループで情報はゼロ。
            #   ※対象指定つきの評価（target is not None）は sim 側の対象が user 自身なので既に正しい
            #     （`sim/abilities._self_targets` は `[user]` を返す）＝ここは投資評価だけを直す。
            if self._B86_A_VALUE_FIX and (user, ability) in _SELF_ROLE_REVEAL:
                g = self._gini.get(user, 0.0)
            elif target is not None:
                g = self._gini.get(target, 0.0)
            else:
                g = max([self._gini.get(n, 0.0) for n in self._gini] or [0.0])
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
            # ★B-109 F3：不安臨界0の対象では発生条件が不安に依存しない（`rules/00:38`・
            #   `rules/30:18,53,77`）＝積んでも「発生した/しなかった」の観測が1ビットも
            #   変わらない＝**実験として情報ゼロ**（FableA 起票・B-108 検死の所見）。
            if (getattr(self, "_loop_lost", False)
                    or getattr(self, "_experiment", False)) \
                    and target in getattr(self, "_future_culprits", self._culprits) \
                    and not self._b109_experiment_is_void(target):
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
    #: B-56＝ML周辺確率の「同率タイ」とみなす許容差。belief の重みはソフト証拠の浮動小数点
    #  積で計算される＝真に対称な2候補でも 1e-16 級のノイズで argmax が揺れる（fs5_guard s7
    #  L4D4 実測＝刑事0.5/サラリーマン0.5000000000000001）。1e-9 はノイズより十分大きく
    #  実確率差（可能世界1個分＝通常 1e-2 級）より十分小さい。
    _B56_TIE_EPS = 1e-9

    def _b56_ml_ties(self, view: dict, ml_name: str | None,
                     ml_p: float) -> tuple[str, ...]:
        """B-56＝argmax と同率タイ（ε内）の他の ML 候補を返す（fs5_guard s7 ミニ検死）。

        `most_likely_role` の argmax は同率タイを浮動小数点ノイズで倒す＝B-52 が
        誤対象を分離した実測（s7 L4D4：真ML刑事でなくサラリーマンを引き離し、刑事の
        +1で遠隔殺人が発生しKP死）。タイの2候補に確率的な優劣は無い＝**分離対象の
        候補集合**として扱い、B-52 の条件⑤で「犯人と同エリアに居るタイ候補の引き離し」
        も同点で許可する（採用は option 列の決定的順序＝キャスト順で先の候補）。

        ★発火判定（条件①〜④）は従来どおり argmax の ML で行い、ここでは触らない：
        タイ候補側だけが犯人と同エリアの局面まで発火させると、真MLが別エリア＝
        ポンプ無しの確率50%に分離席を張る無駄手拡大になる（v1実装で fs5_guard s1
        L2D4 に発火して defense→loss を実測＝B-35型）。発火日を1日も増やさない。

        戻り値＝argmax を除く同率タイ候補（view["characters"] 順＝決定的）。
        タイでなければ空。自明情報のみ（公開の belief 周辺確率と生存/位置）。
        """
        if not ml_name or ml_p < self._B52_ML_MIN:
            return ()
        # 席内で不変＝1回だけ計算（score は option 毎に呼ばれる）
        _key = (view.get("loop"), view.get("day"), view.get("seat"), ml_name)
        _cached = getattr(self, "_b56_cache", None)
        if _cached is not None and _cached[0] == _key:
            return _cached[1]
        marg = self._belief.role_marginals()
        ties = tuple(
            c["name"] for c in view["characters"]
            if c["name"] != ml_name and c.get("alive") and c.get("area") is not None
            and abs(marg.get(c["name"], {}).get("ミスリーダー", 0.0) - ml_p)
            <= self._B56_TIE_EPS)
        self._b56_cache = (_key, ties)
        return ties

    #: B-55＝ボード供給ML分離（feed経路＝行方不明/邪気の汚染）の ML 確度下限。
    #  旧値0.55（2026-07-09掃引＝0.5〜0.65はプラトーで0.55を「頑健な中央」に採用）を
    #  B-52 と同じ 0.5 へ引き下げ（提案②案(i)）：fs5_guard の対称2候補タイ＝ml_p0.5 が
    #  0.55 に恒久的に弾かれ、D2行方不明の分離（全seedの最多 defense_exists 日）が
    #  発火しなかった。★実測（2026-07-25）＝D2の danger は 75（≥60通過）＝塞いでいた
    #  ゲートはこの下限のみ（B-52 doc の「danger55」は kill 日の値）。
    #  案(ii)（B-52のfeed事件拡張＝新設70点）は却下＝当該seatの競合相手が72（散開/退避
    #  移動）で 70 は席を取れない実測。既存feed経路の較正済み当日crit=93.5 を活かす。
    _B55_FEED_ML_MIN = 0.5

    #: B-54＝当日臨界の分離とカルティストピン(99)の席競合の調停（fs5_guard s3/s5 ミニ検死）。
    #  ピン99が分離（kill=70/feedの当日crit=93.5）と同日に競合すると、3席が
    #  暗躍禁止(101)＋ピン(99)＋冷却(92-97)で埋まり分離が締め出される実測
    #  （s3/s5 L2D4＝KP死・s3 L6D2＝ボード線）。恒常の点上げはB-52掃引でL1退行が
    #  実証済み＝**狭い述語が成立した日だけピンが譲る**（99→_B54_YIELD_SCORE）。
    _B54_PIN_YIELD = True
    _B54_YIELD_SCORE = 68.0   # 分離(70)の直下・汎用移動(42)より上＝分離不在なら席を保てる
    _B54_INCIDENTS = _B52_KILL_INCIDENTS + ("行方不明", "邪気の汚染")

    #: B-57＝薄事前のTTハーツ仕上げ（2026-07-26 検死＝3日級残敗4局・提案①の最小実装）。
    #  未来改変プランでは最終日に TT の友好≤2 だと任意敗北（50:128）＝蝶を完封しても
    #  ループを落とす。TTへの友好+は【強制】友好禁止無視＝投資は必ず通る確実な防御。
    #  既存の TT ガード（_tt_guards）は p_future≥0.7 or tt_p≥0.5 が必要で、L1（事前
    #  ≈0.24・周辺フラット）では組めない＝btx_future#0 L1D3 の「友好+1 一枚の
    #  defense_exists」を空振り冷却に誤配分した（検死doc §3）。
    #  最小実装＝**最終日限定・今日3に届く候補限定・証拠ゲート付き**の仕上げ点。
    #  序盤の席・開幕定石・ガード確立後（_tt_guards あり＝TT仕上げ92の領分）は不変。
    _B57_THIN_FINISH = True
    _B57_PFUTURE_MIN = 0.5    # 蝶が事件表に無くても観測でp_futureが立てば証拠成立
    _B57_TT_MARG_MIN = 0.02   # 開示/死亡/矛盾でTT除外済みの候補には張らない
    _B57_GWBAN_BONUS = 0.05   # 友好禁止歴＝TT示唆tell（既存+0.35先例）の同点タイブレーク
                              # （69.9+0.05=69.95＜B-52分離70.0＝防御席は跨がない）

    #: B-58＝mm移動札の同ターン寄せブロック（2026-07-26 検死＝3日級残敗4局・提案②）。
    #  (1) SK配達ピン_急所単独＝公開済みフレンド/実証VIPが1人きり×mmがSK候補に伏せ札×
    #      別エリア → 移動禁止101（KP単独形の対称拡張。§5b-1の同族＝寄せは置かれた時点の
    #      同エリア判定〈B-52〉のスコープ外だった）。
    #  (2) SKテストの犠牲者安全のフレンド対称化＝フレンド候補（周辺確率≥下限）を序盤
    #      ループでも囮にしない（KP候補は既に除外済み＝非対称だった）。
    _B58_VIP_DELIVERY_PIN = True
    _B58_FRIEND_VICTIM_P = 0.3

    def _b71_victim_delivery_pin(self, view: dict, tgt: str,
                                 keyperson: str | None, killer: str | None) -> bool:
        """B-71＝SK配達の被害者側ピン（A-64 sk_deliver への防御語彙）。

        成立条件（すべて・呼び出し前提＝card=移動禁止×tgt in mm_char_now）：
          (1) tgt が急所＝KP（確度≥0.7）or 公開フレンド/実証VIP
          (2) SK候補（_sk_suspects|_sk_strong）が**1人きり**で居るエリアが実在し、
              急所と別エリア × 急所の禁止エリアでない（＝移動1枚の配達で2人きり
              →ターン終了【強制】殺害が成立しうる盤面）
          (3) キラー線が**今日**死んでいる＝believedキラーが不在/死亡 or
              （急所と別エリア かつ mmの伏せ札なし＝今日は近づけない）。
              キラー線が生きている日は既存の暗躍禁止側の択を変えない（誤爆防止）。
        """
        if not self._B71_VICTIM_PIN:
            return False
        tc = self._alive(view, tgt)
        if not tc:
            return False
        # ★KP限定（v1掃引の実測）：フレンドにも張る版は btx5_future×4局 defense→fb_loss
        #   ＝TT系でmmがフレンドへ伏せる札（友好禁止/不安+1が本線）を配達と誤読し、
        #   毎ターン移動禁止＋フレンドの対象枠を焼いて蝶冷却/ハーツ投資が飢餓した。
        #   フレンド側の配達はB-58（SK側ピン）の領分に残す。gwbanデコイ除外も5日を
        #   治せず3日回収を削った（118→116）＝対象の絞りが正着（掃引が設計を導いた）。
        if not (tgt == keyperson
                and getattr(self, "_kp_prob", {}).get(tgt, 0.0) >= 0.7):
            return False
        mm_char_now = {p.get("target") for p in view.get("placements", [])
                       if p.get("owner") == "mastermind"
                       and p.get("target_kind") == "character"}
        # (3) キラー線が今日死んでいるか
        if killer and killer != tgt:
            kc = self._alive(view, killer)
            if kc and (kc["area"] == tc["area"] or killer in mm_char_now):
                return False
        # (2) 1人きりのSK候補エリア（配達1枚で2人きり）
        sk_pool = (getattr(self, "_sk_suspects", set())
                   | getattr(self, "_sk_strong", set()))
        for s in sk_pool:
            if s == tgt:
                continue
            sc2 = self._alive(view, s)
            if not (sc2 and sc2.get("area") and sc2["area"] != tc["area"]):
                continue
            if sc2["area"] in forbidden_of(tgt):
                continue   # 禁止エリアへは配達不能
            if not any(o["alive"] and o.get("area") == sc2["area"]
                       and o["name"] != s for o in view["characters"]):
                return True
        return False

    #: B-76トグル＝公開フレンドの配達実証つき退避移動（Falseで旧挙動へ完全復帰）
    _B76_FRIEND_EVADE = True

    #: B-78トグル＝配達先への第三者注入（Falseで旧挙動へ完全復帰）
    _B78_DEST_INJECT = True

    #: B-81トグル＝急所単独ピンの状況昇格（Falseで従来101のみへ完全復帰）
    _B81_PIN_PROMOTE = True

    #: B-82トグル＝B-76期待札の1/loop残存会計（Falseで従来のm_last期待へ完全復帰）
    _B82_EXPECT_AVAIL = True

    def _b82_mm_move_available(self, view: dict, card: str) -> bool:
        """mmの移動札 card が今ループまだ使えるか（公開事実のみ）。
        ←→/↑↓＝再利用可。移動斜め＝mm専用の1/loop札（engine.models ONCE_PER_LOOP）
        ＝今ループの公開済み札（cards_revealed）に出ていれば消費済み。"""
        if card != "移動斜め":
            return True
        lp = view.get("loop")
        for e in view.get("history", []):
            if e.get("event") != "cards_revealed" or e.get("loop") != lp:
                continue
            if any(p.get("owner") == "mastermind" and p.get("card") == "移動斜め"
                   for p in e.get("placements", [])):
                return False
        return True

    def _b81_push_culprit(self, view: dict, day: int) -> bool:
        """B-81＝dayが「押し切り日」（B-77述語）か：蝶/自殺×danger≥60×単独犯人×
        臨界≤3×ML（確度≥0.55）が犯人本人or同エリア（＝自己ポンプで冷却容量を
        越えてくる日＝102帯の冷却が生命線）。"""
        if not any(i.get("day") == day and i.get("name") in ("蝶の羽ばたき", "自殺")
                   for i in view.get("incidents", [])):
            return False
        if getattr(self, "_incident_danger", {}).get(day, 0.0) < 60.0:
            return False
        cands = getattr(self, "_culprit_by_day", {}).get(day, set())
        if len(cands) != 1:
            return False
        culp = next(iter(cands))
        th = unrest_threshold_of(culp)
        cc = self._alive(view, culp)
        if not (cc and th and th <= 3):
            return False
        ml, mlp = self._belief.most_likely_role("ミスリーダー")
        mlc = self._alive(view, ml) if ml else None
        return mlp >= 0.55 and (ml == culp or (mlc and mlc["area"] == cc["area"]))

    def _b81_promote_ok(self, view: dict) -> bool:
        """B-81＝急所単独ピンを102.5へ昇格してよい日か＝今日も明日も押し切り日でない
        （押し切り日はA-63対応で較正済みの当日/前日冷却102帯が生命線＝席を守る）。"""
        if not self._B81_PIN_PROMOTE:
            return False
        if getattr(self, "_b77_prep", None) is not None:
            return False    # 明日の押し切り＝前日冷却(82.5-102)の席を守る
        return not self._b81_push_culprit(view, view.get("day", 0))

    def _b78_inject_at_delivery_dest(self, view: dict, vip: str, vip_c0: dict,
                                     tgt: str, dest: str | None) -> bool:
        """B-78(2)＝dest が「VIPの配達先になりうるSK候補の単独エリア」か。

        条件：dest に SK候補（_sk_suspects|_sk_strong）が**1人きり**× dest ≠ VIPの現エリア
        × dest が VIP の禁止エリアでない（禁止なら配達自体が不成立）× tgt はSK候補でない
        × **VIPのturn_end死の実証**（過去ループ・公開履歴＝犠牲リスクの正当化ゲート）。
        呼び側条件＝VIP本人にmm伏せ札×tgtは非VIP（KP/公開フレンドでない）。"""
        if not (self._B78_DEST_INJECT and dest and dest != vip_c0.get("area")):
            return False
        if not any(e.get("event") == "death" and e.get("phase") == "turn_end"
                   and e.get("name") == vip for e in view.get("history", [])):
            return False
        if dest in forbidden_of(vip):
            return False
        if tgt in (getattr(self, "_sk_suspects", set())
                   | getattr(self, "_sk_strong", set())):
            return False
        occ = [o["name"] for o in view["characters"]
               if o["alive"] and o.get("area") == dest]
        if len(occ) != 1:
            return False
        return occ[0] in (getattr(self, "_sk_suspects", set())
                          | getattr(self, "_sk_strong", set()))

    def _b76_delivery_proven(self, view: dict, tgt: str) -> set:
        """B-76＝mmが tgt に移動札（移動禁止以外）を置いた公開実績の**習慣集合**
        （cards_revealed・全ループ累積）。空集合＝実証なし。

        ★v3（3日random_BTX#4の実測）＝「最新1枚」を期待札にすると、mmが斜め/↑↓を
        交互に使う角詰め型で読みが外れ、退避が沈黙した翌日に配達が通る。習慣集合の
        **全てに対して安全なCだけ**を退避と認める＝#4型（2種以上の交互）では幾何的に
        安全Cが存在せず正しく沈黙（帯の単調癖＝seal型でのみ発火）＝公開実績の分布への
        頑健化であり相手モデルの予測ではない。"""
        habit: list = []
        for e in view.get("history", []):
            if e.get("event") != "cards_revealed":
                continue
            for p in e.get("placements", []):
                if (p.get("owner") == "mastermind" and p.get("target") == tgt
                        and str(p.get("card", "")).startswith("移動")
                        and p.get("card") != "移動禁止"):
                    habit.append(p.get("card"))
        return habit

    def _b76_friend_evade_ok(self, view: dict, tgt: str, card: str,
                             c: dict, mm_char_now: set) -> bool:
        """B-76＝公開フレンドへの退避移動の成立判定。

        成立条件（すべて・公開事実のみ）：
          (1) tgt が公開フレンド × mmが今日 tgt に伏せ札（配達/干渉の当日形）
          (2) 配達の実証＝mmが tgt に移動札を置いた公開実績（B-72(a)ゲート＝これが無い
              相手への伏せ札は友好禁止/不安+1が本線＝B-71 v1退行の機序）
          (3) SK候補が1人きりのエリアが実在（配達の着地点＝脅威が生きている）
          (4) 行き先の安全＝〈mm札が実証済みの移動M〉→合成 C∘M の実効先（禁止で留まる＝
              配達不成立＝安全）と〈mm札が非移動〉→C単独の実効先の**両方**が
              SK単独エリアでない（伏せ札の中身に依存しない両面安全＝B-72二正面の教訓）。
        """
        if not (self._B76_FRIEND_EVADE and card in ("移動←→", "移動↑↓")):
            return False
        if tgt not in getattr(self, "_friend_guards", set()):
            return False
        # ★掃引v4（3日random_BTX#4＝3(defense)→fb_loss 実測）：公開フレンドが2人以上
        #   （友情サークル型）では発火しない。mmはフレンド線自体を二正面化（斜め/↑↓の
        #   交互herding＝角詰め）でき、片方への退避は幾何的に翌日の角（危険域が直交隣接＝
        #   どのCでも回避不能）へ誘導される（v3の全習慣頑健化でも#4は治らずseal#8を失った
        #   ＝述語でなく対象の絞りが正着＝B-71 v1→KP限定と同じ教訓）。
        if len(getattr(self, "_friend_guards", set())) >= 2:
            return False
        if tgt not in mm_char_now:
            return False
        # ★掃引v2（btx5_future#0/#1/#3/#9 defense→fb_loss 実測）：未来改変プランの世界では
        #   ループの主戦線は蝶/TTハーツ＝フレンド退避に席とフレンド対象枠（友好+2の投資先）を
        #   割いても蝶発生で同じくループを失う＝機会費用が常に上回る。B-57の証拠フラグ
        #   （蝶が事件表 or p_future≥0.5）が立つ世界では発火しない。
        if getattr(self, "_tt_thin_evidence", False):
            return False
        m_habit = self._b76_delivery_proven(view, tgt)
        if not m_habit:
            return False
        # (3) SK単独エリア（危険な着地点）の集合
        sk_pool = (getattr(self, "_sk_suspects", set())
                   | getattr(self, "_sk_strong", set()))
        danger_areas = set()
        for s in sk_pool:
            if s == tgt:
                continue
            sc2 = self._alive(view, s)
            if (sc2 and sc2.get("area") and sc2["area"] != c["area"]
                    and not any(o["alive"] and o.get("area") == sc2["area"]
                                and o["name"] != s for o in view["characters"])):
                danger_areas.add(sc2["area"])
        if not danger_areas:
            return False
        # (4) 両面安全（禁止エリアで留まる＝配達不成立＝安全側に数える）
        from engine.board import compose_moves, destination
        fb = forbidden_of(tgt)

        def _eff(cards: list) -> str:
            t = compose_moves([x for x in cards if x in
                               ("移動←→", "移動↑↓", "移動斜め")])
            if t == (0, 0):
                return c["area"]
            d0 = destination(c["area"], t)
            return c["area"] if d0 in fb else d0

        if _eff([card]) in danger_areas:      # mm札が非移動の場合のC単独の実効先
            return False
        # ★B-82（レース監査T-r2・5日random_BTX#8実測）：期待札に1/loop会計を入れる。
        #   mmの移動斜め（mm専用・対角）は1/loopで消費が公開される＝消費済みの斜めを
        #   期待し続けると「↑↓は斜めと合成で危険」型の誤棄却で退避が沈黙し、mmの
        #   再利用可能な←→の2手目配達（L7D2/L8D2実測）が素通りした。
        #   期待札＝**今ループ使用可能な**最新の公開実績。使用可能な実績が無い
        #   （＝斜め消費済み×他の習慣なし）日は、デッキ公開知識（MASTERMIND_HAND）で
        #   残る移動2種（←→/↑↓）の**両方**に対して安全なCのみ退避と認める
        #   （v3の全習慣頑健化とは別物＝分布でなくルール上の残存集合）。
        if not self._B82_EXPECT_AVAIL:
            return _eff([card, m_habit[-1]]) not in danger_areas
        m_avail = [m for m in m_habit if self._b82_mm_move_available(view, m)]
        if m_avail:
            return _eff([card, m_avail[-1]]) not in danger_areas
        return all(_eff([card, m]) not in danger_areas
                   for m in ("移動←→", "移動↑↓"))

    # ★B-90(c)（2026-07-28・新era検死 T-2/M2 の(c)＝SKの「2人きり」条件を移動で折る）：
    #   btx_seal 帯の前線B＝mmが毎ループ同じ日に フレンドを SK の単独エリアへ運び、
    #   ターン終了フェイズの強制殺害でループを落とす（L2〜L8 が同一手順・検死doc §2-2）。
    #   ★probe実測（2026-07-28・btx_seal s0 L3D2）＝**折り手は既に在り、発火もしていた**：
    #     `移動↑↓→男子学生`（B-76 フレンド退避＝85.0）が候補に立ち、mmの←→と合成されて
    #     斜め＝都市でなく病院へ逸れる。落ちていたのは**席**で、同ターンの
    #     クロマク剥がし(88)・カルティスト剥がし_実証(86) の2枚が先に3席を埋めていた
    #     （4需要 vs 3席）。同ループの実測ではボード側は 神社=1 で持ちこたえており、
    #     ループを落とした唯一の原因はフレンド死亡＝**取捨の誤り**。
    #   ∴ 新しい語彙ではなく**状況昇格**（B-81 と同型）を入れる：
    #     「そのフレンドが過去の**敗北ループ**でターン終了フェイズに死んでいる」＝
    #     同型敗北の再履修が実証されている時だけ、退避を板の引き剥がし(88)より上へ。
    _B90_FRIEND_PROMOTE: bool = True

    def _b90_friend_death_proven(self, view: dict, name: str) -> bool:
        """過去の敗北ループで name がターン終了フェイズに死亡した（＝SK殺害の実証）。"""
        if not self._B90_FRIEND_PROMOTE:
            return False
        hist = view.get("history", [])
        lost = {e.get("loop") for e in hist
                if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}
        if not lost:
            return False
        return any(e.get("event") == "death" and e.get("name") == name
                   and e.get("phase") == "turn_end" and e.get("loop") in lost
                   for e in hist)

    #: B-71トグル（Falseで旧挙動へ完全復帰）
    _B71_VICTIM_PIN = True
    #: B-71＝_kp_guard のキラー生存チェック（死亡キラーへのKPガード空振り是正）のトグル
    _B71_KP_GUARD_LIVENESS = True

    def _b58_vip_solo_deliverable(self, view: dict, tgt: str,
                                  keyperson: str | None) -> bool:
        """B-58(1)：SK候補 tgt をピンすべき「1人きりの急所」が居るか。

        呼び出し前提（score側で検査済み）＝card=移動禁止・tgt in mm_char_now・
        tgt が SK候補（_sk_cands|_sk_strong）。ここでは急所側の条件だけを見る：
        公開済みフレンド（_friend_guards＝p≥0.95）/実証VIP（_fatal_guards）が
        1人きり × SK候補と別エリア（＝配達1移動で2人きり→ターン終了【強制】殺害の線）。
        KPは既存のKP単独形(101)の領分＝除外。SK候補の暗躍≥3はキラー封じの領分＝除外。
        """
        c0 = self._alive(view, tgt)
        if not (self._B58_VIP_DELIVERY_PIN and c0 and c0["anyaku"] < 3):
            return False
        for _vip in sorted(getattr(self, "_friend_guards", set())
                           | getattr(self, "_fatal_guards", set())):
            if _vip == tgt or _vip == keyperson:
                continue
            _vip_c = self._alive(view, _vip)
            if (_vip_c and _vip_c["area"] != c0["area"]
                    and not any(o["alive"] and o.get("area") == _vip_c["area"]
                                and o["name"] != _vip
                                for o in view["characters"])):
                return True
        return False

    #: B-59＝実証済み敗北ボード上の供給源退避（2026-07-26 検死＝3日級残敗4局・提案③）。
    #  クロマク能力（+1/日・脚本家能力フェイズ）は暗躍禁止で止まらない＝退避（的を逸らす）が
    #  唯一の対抗（fs5_guard B-52「冷却だけではMLポンプに負ける」のボード供給版）。
    #  ★B-35（退避加点の負の結果・バックログ§1e）との違い＝あちらは**自駒（KP等）の退避**を
    #  レース条件で加点＝逃げ先を追撃されて退行（相手の実反応依存）。こちらは**敵駒（供給源）の
    #  排除**で、判定は既に起きた公開事実のみ（敗北ボード実証＋mm能力供給の観測＋その場の顔ぶれ）
    #  ＝相手反応の予測を含まない。mmの対抗（戻し移動）は毎日1枚の手を消費させる拘束交換。
    #  既存の(c3)クロマク剥がし（確度0.7・land済み・非退行）と同じ行動クラス＝ゲート緩和のみ。
    #: B-75＝露見実験の明示的設計＋B-74述語の同梱（2026-07-27）。
    #  露見実験（B-68/B-74で機序特定）＝「盤がロック済み（当ループ敗着確定）のループでは、
    #  カルティスト候補への応手（ピン/剥がし/逸らし）を**意図して控え**、暗躍禁止→盤
    #  （96-101＝従来どおり置かれる）への素通りを観測してカルティストを割り出す」。
    #  従来は偶然の均衡（応手語彙が無いから起きていた）＝B-74の語彙追加で壊れた。
    #  本トグルは∩実証語彙の解禁（Falseで旧挙動へ完全復帰）。
    _B74_SMUGGLE_PROOF = True

    def _b77_cooler_need(self, view: dict, tgt: str,
                         mm_char_now: set[str]) -> int:
        """B-77＝前日冷却エンジンの除去役候補なら残り必要ハーツ(1-2)、非該当なら0。

        条件：明日の押し切り犯人が確定（_b77_prep）×tgtが不安除去系能力を♡2圏で
        未解禁×拒否役職疑い<0.4×**mmが今ターン札を伏せていない**（友好禁止の裏＝
        二系統から後出しで選ぶ・検死3 §T-B）×犯人エリアへ同エリア or 自移動1枚で到達可。"""
        culp = getattr(self, "_b77_prep", None)
        if not culp or tgt == culp or tgt in mm_char_now:
            return 0
        c = self._alive(view, tgt)
        cc = self._alive(view, culp)
        if not (c and cc):
            return 0
        marg = self._belief.role_marginals().get(tgt, {})
        if sum(marg.get(r, 0.0) for r in
               ("カルティスト", "クロマク", "キラー", "ウィッチ")) >= 0.4:
            return 0
        for ab in goodwill_abilities_of(tgt) or []:
            if "不安" not in ab["name"] or "除去" not in ab["name"]:
                continue
            need = ab["hearts"] - c["goodwill"]
            if not 1 <= need <= 2:
                continue
            if c["area"] == cc["area"]:
                return need
            if cc["area"] in forbidden_of(tgt):
                continue
            if any(_move_dest(c["area"], mv) == cc["area"]
                   for mv in ("移動↑↓", "移動←→")):
                return need
        return 0

    def _b75_exposure_ok(self, view: dict, tgt: str | None = None) -> bool:
        """B-75＝∩実証語彙（剥がし_実証/B-67搬入路）を今日使ってよいか。

        (1) 盤ロック外（_loop_lost＝盤≥2等の敗着確定でない）：ロック済みは応手を控えて
            素通り観測を取る（露見実験＝負けているループで情報を買う。FS s13実測＝
            ロック日の応手が露見軌道を壊して3→9 loss）。
        (2) 当日冷却が優先（席の経済）：今日の事件危険×犯人候補が臨界-1以上なら、
            応手(86)が冷却席(76.5級)を押し出して事件を発火させる
            （FS s13 L2D1実測＝評価86が不安-1→医者を席から弾き行方不明が発生）＝譲る。
        """
        if getattr(self, "_loop_lost", False):
            return False
        today = view.get("day", 0)
        if getattr(self, "_incident_danger", {}).get(today, 0.0) >= 45.0:
            _mm_chars_today = {p.get("target") for p in view.get("placements", [])
                               if p.get("owner") == "mastermind"
                               and p.get("target_kind") == "character"}
            for cn in getattr(self, "_culprit_cands", {}).get(today, ()):
                # ★応手対象そのものは除外：同一対象への配置は席排他＝この席が応手を
                #   置く時点で「その対象への冷却」は誰も置けない（両立不能な択一）。
                #   mm札の「不安+1仮説」と「運搬移動仮説」も同一札の排他＝対象外へ
                #   広げると運搬札そのものが応手を封じる自縄自縛（s8 L2D3実測＝
                #   教師th=1×mm運搬札で剥がし_実証が恒久沈黙）。
                if cn == tgt:
                    continue
                cc = self._alive(view, cn)
                th = unrest_threshold_of(cn)
                if not (cc and th):
                    continue
                # 当日到達算術：現在値＋mmの伏せ札（不安+1の可能性）が臨界に届く
                # 候補だけ「冷却優先」＝臨界-1の静的判定は臨界1キャラで常時発火する
                # 過広（s8 L2D3実測＝お嬢様th=1が全日ブロックした）。
                if cc["unrest"] + (1 if cn in _mm_chars_today else 0) >= th:
                    return False
        return True

    _B59_SOURCE_EVICT = True
    _B59_KM_MIN = 0.15    # フラット事前（1/キャスト数≈0.1前後）では発火しない下限

    def _b59_source_evict_ok(self, view: dict, tgt: str, c: dict | None,
                             dest: str | None, danger_board: str | None) -> bool:
        """B-59＝供給源退避の成立判定（すべて公開情報＋belief argmax）。

        成立条件：
          (1) danger_board が**実証済み**敗北ボード（過去の敗北ループで暗躍≥2＝公開）
          (2) そのボードへ**mm能力フェイズの供給を観測済み**（暗躍禁止で止まらない供給の実証）
              かつ tgt がその供給時に同エリアに居た（クロマクの同エリア要求＝公開の顔ぶれ）
          (3) tgt が今そのボード上に居て dest はボード外
          (4) tgt のクロマク周辺確率が argmax かつ ≥ _B59_KM_MIN（除外済み/フラットは弾く）
        """
        if not (self._B59_SOURCE_EVICT and c and dest and danger_board):
            return False
        if tgt in getattr(self, "_kuromaku_suspects", ()):
            return False   # 確度0.7＝(c3)クロマク剥がし(88)の領分＝1点降格させない
        if tgt == "巫女" and danger_board == "神社":
            # ★巫女は神社暗躍除去（♡3）の担い手＝神社が敗北ボードの時に神社から
            #   退避させるのは自傷（B-13「巫女の除去は神社に居てこそ」の裏面。
            #   random_BTX#8実測＝誤argmaxで巫女を退避し防衛が3→5に遅延）。
            return False
        if danger_board != getattr(self, "_observed_defeat_board", None):
            return False
        if not (c["area"] == danger_board and dest != danger_board):
            return False
        if tgt not in getattr(self, "_mm_pump_board_present", {}).get(
                danger_board, ()):
            return False
        marg = self._belief.role_marginals()
        p = marg.get(tgt, {}).get("クロマク", 0.0)
        if p < self._B59_KM_MIN:
            return False
        best = max((d.get("クロマク", 0.0) for d in marg.values()), default=0.0)
        return p >= best - 1e-9

    #: B-67＝供給役の復帰・搬入デフレクト（2026-07-27 目視検死T1）。発火は狭い：
    #  実証済み敗北ボード（observed_defeat_board）×盤面が閾値-1（board+1≥2）×
    #  確信0.7の供給役（クロマク/カルティスト）×mmが今ターンその供給役に伏せ札。
    #  ピンは移動禁止（全内容に有効）、逸らしは「mmの札がどの移動でも/移動でなくても
    #  敗北ボードへ着地しない」strict-safe な自移動札のみ（同種2枚＝その移動1回・KB:10、
    #  斜めの消費は used_cards＝公開で追跡）＝当て推量の逸らしは撃たない。
    _B67_RETURN_DEFLECT = True

    def _b67_deflect_ok(self, view: dict, card: str, tgt: str, c: dict | None,
                        danger_board: str | None) -> bool:
        """B-67＝逸らし移動の strict-safe 判定（すべて公開情報）。

        mmの伏せ札の内容ケース＝{移動でない} ∪ {mmが今ループまだ出せる移動札}。
        全ケースで合成後の着地が敗北ボードにならない自移動札だけ True。
        （同種2枚＝その移動1回：engine.board.compose_moves が正典＝自前で場合分けしない）"""
        if not (self._B67_RETURN_DEFLECT and c and danger_board):
            return False
        if tgt not in getattr(self, "_b67_return_targets", ()):
            return False
        from engine.board import compose_moves, destination
        area = c["area"]
        mm_used = set((view.get("used_cards") or {}).get("mastermind", ()))
        cases = [[card]]                         # mmの札が移動でない場合＝自札のみ
        for m in ("移動↑↓", "移動←→", "移動斜め"):
            if m not in mm_used:                 # 斜め＝1/loop消費のみ used_cards に載る
                cases.append([m, card])
        return all(destination(area, compose_moves(ms)) != danger_board
                   for ms in cases)

    #: B-63＝1/loop防御資源の会計（5日級検死・2026-07-27）。主人公の防御中核カード
    #  （移動禁止/不安-1/友好+2＝ONCE_PER_LOOP）はチーム3枚/ループ・mmの脅威カード
    #  （移動↑↓/←→・不安+1×2）は毎日再利用可＝5日級では枯渇レースになる（3日級は
    #  「3枚=3日」で顕在化しない）。判定材料はすべて自明情報＝**自チームの手札残
    #  （view.used_cards/hand）と今日の公開配置**のみ＝相手反応の予測を含まない
    #  （B-35型リスクの限定）。切替先は既存の採点項（VIP注入/冷却）のみ＝新語彙はB-64。
    _B63_SEAT_RESERVE = True       # (2) 冷却席の予約（#8形＝席の食い合い）
    _B63_PIN_EXHAUST_SWITCH = True  # (3) ピン枯渇時のVIP注入切替（#16形）

    def _b63_seat_can_play(self, view: dict, card: str) -> bool:
        """今日この後の自チームの席（現席含む）のどこかで card を出せるか。

        現席＝view.hand（1/loop消費済みは既に除外）。未行動席＝used_cards に card が
        無い席（今日の配置はまだ used_cards に載らないため、行動済み席は placements の
        owner で除外）。すべて公開/自明情報。"""
        if card in view.get("hand", ()):
            return True
        acted = {p.get("owner") for p in view.get("placements", [])
                 if p.get("owner") not in (None, "mastermind")}
        used = view.get("used_cards", {})
        for s in ("p1", "p2", "p3"):
            if s == view.get("seat") or s in acted:
                continue
            if card not in used.get(s, ()):
                return True
        return False

    def _b63_cooling_reserved(self, view: dict, tgt: str, c: dict | None) -> bool:
        """B-63(2)：今日の事件の犯人候補 tgt（不安が臨界-1以上）の1席を冷却に予約する。

        5日級検死#8（L4D4）＝p2の`友好+1→巫女`（除去チャージ52）が巫女の1席を先取し、
        不安-1を残していたp3が1席ルールで冷却を置けず邪気の汚染が発生（classify_dayの
        防御例＝`不安-1→巫女`）。今日まだ誰かが不安-1を出せるなら、友好+でその席を
        消費しない（チャージは明日でよい・冷却は今日が期限）。"""
        if not (self._B63_SEAT_RESERVE and c is not None):
            return False
        th = unrest_threshold_of(tgt)
        if th is None or th <= 0 or c["unrest"] < th - 1:
            return False
        cands = getattr(self, "_culprit_cands", {}) or {}
        if tgt not in cands.get(view["day"], ()):
            return False   # 今日の事件の犯人候補だけ（明日以降は予約しない＝狭く）
        # ★狭め（v2）：mmが今日 tgt に札を伏せている（不安+1の線＝#8形）か、既に臨界到達
        #   済みの時だけ。臨界1×広い犯人候補集合で予約が乱発し、btx_future の L1 防衛
        #   （B-57仕上げ等の友好+）を4局潰した実測（3日 L1→L4/L5×4）への対処。
        mm_char_now = {p.get("target") for p in view.get("placements", [])
                      if p.get("owner") == "mastermind"
                      and p.get("target_kind") == "character"}
        if not (tgt in mm_char_now or c["unrest"] >= th):
            return False
        return self._b63_seat_can_play(view, "不安-1")

    def _b63_delivery_inject_sk(self, view: dict, vip: str, vip_c0: dict,
                                tgt: str, occ0: list, mm_char_now) -> str | None:
        """B-63(3)：ピン枯渇時の配達形VIP注入。成立ならSK候補名・非成立はNone。

        5日級検死#16＝B-58ピン（移動禁止）はD1-D3に正しく発火して3日守ったが、
        移動禁止＝1/loop×3席 vs mmの移動↑↓/←→（毎日再利用可）の枯渇レースでD4に弾切れ
        →SK配達成立×毎ループ。切替先＝既存のVIP注入（第三者を送り込んで2人きりを崩す＝
        再利用可能な移動札）。成立条件（すべて自明情報）：
          (1) VIP（推定KP/公開済みフレンド）が1人きり（occ0空）
          (2) 今日どの席も移動禁止を出せない（現席hand＋未行動席のused_cards）＝枯渇時のみ
              ＝ピン(101)が使える日はピンの領分（3日級では通常枯渇しない＝発火しない）
          (3) SK候補にmmが今日札を伏せ（配達移動の線＝B-58と同形）×VIPと別エリア×
              VIPのエリアへ1移動で到達可×暗躍<3（キラー封じの領分は除外）
        """
        if not (self._B63_PIN_EXHAUST_SWITCH and not occ0):
            return None
        if self._b63_seat_can_play(view, "移動禁止"):
            return None
        for _sk in sorted(self._sk_suspects | getattr(self, "_sk_cands", set())):
            _skc = self._alive(view, _sk)
            if (_skc and _sk != vip and _sk != tgt
                    and _sk in mm_char_now
                    and _skc["area"] != vip_c0["area"]
                    and vip_c0["area"] not in forbidden_of(_sk)
                    and _skc["anyaku"] < 3):
                return _sk
        return None

    #: B-64＝mm移動札のML同ターン寄せブロック（5日級検死#2・3日級検死§5-2 提案②の実装版）。
    #  B-52は「置かれた時点で同エリア」の分離のみ＝mmがML候補を移動札で犯人へ寄せる
    #  （同ターン寄せ）と冷却1席では 2+1(札)+1(ML)-1(冷却)=臨界 で届く（#2 L4D3実測）。
    #  `移動禁止→ML候補` で寄せ移動を打ち消せばML+1が届かない＝冷却と対の二席防御。
    #  B-58の設計思想を踏襲＝狭い述語（当日の蝶×ML確度ゲート×mm伏せ札×
    #  別エリア×ML+1がちょうどピボットの算術）。B-63の資源会計と連動＝冷却が出せない
    #  枯渇日はブロックしても臨界に届く＝発火しない（算術が自然に落とす）。
    #  ★事件種は蝶のみ（起票の「蝶/供給」から狭めた・2026-07-27実測）：供給事件
    #  （行方不明/邪気の汚染）を含めた初版は、3日BTX#2の二正面日（邪気実証74×
    #  メインラバーズ死線）で唯一残った冷却席（不安-1→女子学生50）をピン79が奪い
    #  防衛L5→L8に遅延（probe実測）。供給側のML寄せ実敗例は検死に無い（#10はB-59b
    #  で解消済み）＝証拠のある蝶に限定（拡張は実敗例とセットで）。
    _B64_ML_APPROACH = True
    _B64_INCIDENTS = ("蝶の羽ばたき",)

    def _b64_ml_approach_block(self, view: dict, tgt: str, mm_char_now) -> bool:
        """B-64＝移動禁止→ML候補（同ターン寄せブロック）の成立判定（自明情報のみ）。

        成立条件（すべて）：
          (1) 今日 _B64_INCIDENTS の事件が予定されている
          (2) ML候補（argmax・確度≥_B52_ML_MIN。B-56同率タイ候補も対象）
          (3) mmが今ターン tgt に札を伏せた（寄せ移動の線＝札の無い所へのピンは空振り）
          (4) 今日の犯人候補が tgt と別エリア（同エリアはB-52分離の領分）×寄せ1移動で
              成立（犯人のエリアが tgt の禁止エリアでない）
          (5) ML+1がちょうどピボット＝unrest＋mm伏せ札＋ML+1−冷却が臨界ちょうど
              （それ未満＝冷却だけで止まる／超過＝ブロックしても届く＝どちらも発火しない。
               冷却はB-63会計＝今日どこかの席で不安-1が出せる or 冷却済みの日のみ算入）
        """
        if not (self._B64_ML_APPROACH and tgt in mm_char_now):
            return False
        tc = self._alive(view, tgt)
        if tc is None or tc.get("area") is None:
            return False
        today = view["day"]
        if not any(i.get("day") == today and i.get("name") in self._B64_INCIDENTS
                   for i in view.get("incidents", [])):
            return False
        ml_name, ml_p = self._belief.most_likely_role("ミスリーダー")
        if not ml_name or ml_p < self._B52_ML_MIN:
            return False
        if tgt != ml_name and tgt not in self._b56_ml_ties(view, ml_name, ml_p):
            return False
        cool = 1 if (today in getattr(self, "_cooled_days", ())
                     or self._b63_seat_can_play(view, "不安-1")) else 0
        for cn in getattr(self, "_culprit_cands", {}).get(today, ()):
            if cn == tgt:
                continue   # 犯人=ML候補本人＝寄せは無い（自己ポンプは冷却の領分）
            cc = self._alive(view, cn)
            th = unrest_threshold_of(cn)
            if not (cc and th):
                continue   # 臨界0（黒猫）は止められない＝対象外
            if cc["area"] == tc["area"] or cc["area"] in forbidden_of(tgt):
                continue   # 同エリア＝B-52の領分／禁止エリアへは寄せられない
            supply = cc["unrest"] + (1 if cn in mm_char_now else 0) + 1
            if supply - cool == th:
                return True
        return False

    #: B-65＝幻想の板札退避（5日級検死#0・2026-07-27）。幻想特性（KB:30）＝行動カード
    #  被セット不可・同エリアのボードに置かれた非暗躍カードの効果を受ける＝退避手段は
    #  「幻想のいるボードへの移動カード」のみ（simの合法手には既にあるがAI採点がゼロ
    #  だった＝#0でフレンド幻想がSKご神木との2人きり死×6）。発火は幻想の居る局限定＝
    #  過剰発火リスク小（検死 提案③）。
    _B65_GENSO_EVAC = True

    def _b65_genso_board_evac(self, view: dict, card: str, tgt: str,
                              gc: dict, mm_char_now) -> bool:
        """B-65＝幻想の板札退避の成立判定（すべて自明情報）。

        成立条件：
          (1) 幻想が実証急所（公開済みフレンド _friend_guards / 実証VIP _fatal_guards）
          (2) tgt＝幻想のいるボード（幻想が効果を受ける唯一の置き場）
          (3) SK候補が幻想と同エリアに生存（2人きり→ターン終了【強制】殺害の線）
          (4) 2人きりリスク日＝SK候補・幻想以外の同居者が居ない（既に2人きり）or
              全員にmmの伏せ札（引き抜き移動の線＝#0 L4D4の`移動←→→巫女`）。
              ★自チームが今日ピン済み（_pinned_today）の同居者は引き抜き済み扱いに
              **しない**＝動かされない（3日BTX#0実測＝護衛ピンと退避の重複発火で
              巫女2人きりを自作しSKに献上→defense(2)→fb_loss(9)の退行。ピンが
              出せる日はピン＝3人維持の領分・退避はピン枯渇/不在日の代替）
          (5) 退避先が幻想の禁止エリアでない × 退避先で新たにSK候補と2人きりを
              作らない（1人だけ居てそれがSK候補なら退避先として不成立）
        """
        if not (self._B65_GENSO_EVAC and gc.get("area") == tgt):
            return False
        if "幻想" not in (getattr(self, "_friend_guards", set())
                        | getattr(self, "_fatal_guards", set())):
            return False
        sk_pool = (self._sk_suspects | getattr(self, "_sk_cands", set())
                   | getattr(self, "_sk_strong", set()))
        if not any(o["alive"] and o.get("area") == tgt and o["name"] in sk_pool
                   and o["name"] != "幻想" for o in view["characters"]):
            return False
        others = [o for o in view["characters"]
                  if o["alive"] and o.get("area") == tgt
                  and o["name"] != "幻想" and o["name"] not in sk_pool]
        pinned = getattr(self, "_pinned_today", set())
        if not all(o["name"] in mm_char_now and o["name"] not in pinned
                   for o in others):
            return False   # mm札なし/ピン済みの同居者が残る＝今日は2人きりにならない
        dest = _move_dest(tgt, card)
        if dest is None or dest in forbidden_of("幻想"):
            return False
        dest_occ = [o for o in view["characters"]
                    if o["alive"] and o.get("area") == dest]
        if len(dest_occ) == 1 and dest_occ[0]["name"] in sk_pool:
            return False   # 退避先でSK候補と新たな2人きり＝別の死線
        return True

    def _b58_friend_victim_block(self, v: str, marg_fr: dict) -> bool:
        """B-58(2)：SKテストの犠牲者 v がフレンド候補（周辺確率≥下限）なら囮にしない。

        KP候補の既存除外（序盤ループでも絶対に囮にしない）との対称化＝フレンド死亡も
        ループ終了時敗北で同格。下限0.3＝フラット事前（≈0.15-0.22・情報皆無の序盤）では
        発火しない＝「1ループの損で情報を買う」SKテストの情報源価値（load-bearing）を保つ。
        future0 L3D2 の自走（男子学生 0.221＝フラット事前）は意図的に対象外＝情報限界
        （引き継ぎdoc §5b-2）の領分。"""
        return marg_fr.get(v, {}).get("フレンド", 0.0) >= self._B58_FRIEND_VICTIM_P

    def _b57_tt_thin_finish(self, card: str, tgt: str, view: dict) -> float | None:
        """B-57＝薄事前のTTハーツ仕上げ。該当時 score・非該当は None。

        成立条件（すべて）：
          (1) 証拠（_tt_thin_evidence）＝蝶の羽ばたきが事件表にある or p_future≥0.5
          (2) _tt_guards が空（＝確度ゲート通過済みなら既存 TT投資/TT仕上げの領分）
          (3) 最終日（宣言判定はこのターン終了時＝今日が最後の機会）
          (4) 対象の友好が「今日 3 に届く」（+1はg2・+2はg1以上。届かない+は
              宣言を止められない＝席の無駄）
          (5) 対象が TT として除外されていない（周辺確率 ≥ _B57_TT_MARG_MIN）
        """
        if not (self._B57_THIN_FINISH and getattr(self, "_tt_thin_evidence", False)):
            return None
        if getattr(self, "_tt_guards", None):
            return None
        if view["day"] < view.get("days_per_loop", 99):
            return None
        cc = self._alive(view, tgt)
        if not cc:
            return None
        step = 2 if card == "友好+2" else 1
        if not (cc["goodwill"] < 3 <= cc["goodwill"] + step):
            return None
        if self._belief.role_marginals().get(tgt, {}).get(
                "タイムトラベラー", 0.0) < self._B57_TT_MARG_MIN:
            return None
        return PRIORITY["TT仕上げ_薄事前"] + (
            self._B57_GWBAN_BONUS
            if tgt in getattr(self, "_gwban_hist", ()) else 0.0)

    def _b54_pin_should_yield(self, view: dict, options: list[dict]) -> bool:
        """B-54＝カルティストピンが「当日臨界の分離」に席を譲るべき日か（狭い述語）。

        成立条件（すべて当日・自明情報のみ）：
          (1) 今日 kill/feed 事件が予定されている
          (2) ML候補（argmax）の確度 ≥ _B52_ML_MIN
          (3) 犯人候補が ML と同エリア かつ unrest==臨界ちょうど（＝冷却1枚では割れない
              ＝B-52④/feed経路の当日critと同じ判定）
          (4) 分離を実行できる移動 option が実在する（対象が席で塞がれていれば譲らない
              ＝無駄なdemoteをしない。B-52⑤と同型＝B-56タイ候補も対象）
        ピンは「二段防御の片翼」＝価値は残るが、当日の確定打点（事件発生）より後回しで
        よい：ボード線の一段目（暗躍禁止101）は譲らない＝二正面の片面だけを明け渡す。
        """
        if not self._B54_PIN_YIELD:
            return False
        today = view["day"]
        if not any(i.get("day") == today and i.get("name") in self._B54_INCIDENTS
                   for i in view.get("incidents", [])):
            return False
        ml_name, ml_p = self._belief.most_likely_role("ミスリーダー")
        if not ml_name or ml_p < self._B52_ML_MIN:
            return False
        mlc = self._alive(view, ml_name)
        if mlc is None:
            return False
        ties = self._b56_ml_ties(view, ml_name, ml_p)
        for cn in getattr(self, "_culprit_cands", {}).get(today, ()):
            if cn == ml_name:
                continue
            cc = self._alive(view, cn)
            th = unrest_threshold_of(cn)
            if not (cc and th is not None and cc["area"] == mlc["area"]
                    and cc["unrest"] == th):
                continue
            for o in options:
                if o.get("target_kind") != "character" \
                        or (o.get("card") or "") not in _MOVE_TOGGLE:
                    continue
                tgt = o.get("target")
                dest = _move_dest(cc["area"], o.get("card"))
                if dest is None or dest == cc["area"]:
                    continue
                if tgt == cn and dest not in forbidden_of(cn):
                    return True
                if tgt in (ml_name, *ties):
                    tc = self._alive(view, tgt)
                    if (tc is not None and tc["area"] == cc["area"]
                            and dest not in forbidden_of(tgt)):
                        return True
        return False

    # ------------------------------------------------------------------
    # ★B-90(a)：位置が発動条件の供給能力を「移動」で物理的に折る
    #            （同室の他人が1人＝移動1枚で条件が確実に外れる時だけ発火）
    #
    #   機序（新era検死 §2-1 btx_bomb）＝今日の致死事件の犯人の不安が**臨界ちょうど**の日、
    #   主人公の「不安-1」は行動解決フェイズ＝その**後**の脚本家能力フェイズでミスリーダーの
    #   +1 が入って臨界へ戻る＝**冷却1枚では算術的に止まらない**。既存の分離語彙（B-52／B-55）は
    #   ML の**同定**（確度0.5〜0.55）を前提にしているため、ML がまだ割れていない序盤ループでは
    #   一度も発火しない（btx_bomb L1D3 の ML argmax＝0.13・probe実測）。
    #
    #   本述語は ML の同定を要求せず、代わりに**位置の狭さ**で誤爆を抑える（house rule §12）：
    #     ①その能力が今日の脅威に実際に効いている＝犯人の不安が臨界ちょうど（-1しても能力+1で
    #       戻る）× 事件が致死系（_lethal_days）＝素通りするとその日にキャラが死ぬ。
    #     ②**移動1枚で条件を外せる**＝犯人の部屋の他の生存キャラが**ちょうど1人**
    #       （2人以上なら1枚では供給源を消せない＝席の浪費＝発火しない）。
    #       その1人が belief 上ミスリーダーでありうる（周辺確率 > _B90_ML_EPS）ことも要求。
    #     ③**復元コストが高い**＝当日限定。脚本家の札はもう伏せ終えていて、同じ日に同室を
    #       復元する手段が無い（翌日以降の復元は翌日の応手で見る）。
    _B90_ML_ISOLATE: bool = True      # 掃引・A/B用のスイッチ（False で従来挙動に bit 一致）
    _B90_ML_EPS: float = 0.05         # 「ミスリーダーでありうる」の下限（0＝可能世界から消えている）
    _B90_NEED_COOL: bool = True       # 両輪ゲート＝先席が今日の事件を冷やした時だけ発火
    _B90_UNIQUE_CRIT: bool = True     # 臨界犯人が1人の日だけ（＝移動1枚で経路が全部折れる）

    def _b90_ml_isolate(self, o: dict, view: dict) -> float | None:
        """今日の致死事件の犯人と**同室の唯一の他人**を移動で引き剥がす（B-90(a)）。

        返り値＝PRIORITY["ML供給隔離_同室単独"]（成立時）／None（不成立）。
        ★犯人自身を動かす形も許すが、その場合は**行き先に ML でありうる者が居ない**ことを
        要求する（供給源の部屋へ自分から飛び込む自滅を防ぐ）。
        """
        if not self._B90_ML_ISOLATE:
            return None
        if o.get("target_kind") != "character" or (o.get("card") or "") not in _MOVE_TOGGLE:
            return None
        # ★両輪ゲート（B-90b・random_BTX s9 の退行検死 2026-07-28）：分離は**冷却の2席目**で
        #   しか意味を持たない（分離だけでは既に臨界に居る不安を下げられない＝事件は起きる）。
        #   ∴ このターンの先席が既に今日の事件を冷やしている時だけ発火する。
        #   ゲート無しでは先席の冷却そのものを分離が押しのけ、L1防衛を落とした（s9 L1D2 実測）。
        if self._B90_NEED_COOL and view.get("day") not in getattr(self, "_cooled_days", ()):
            return None
        pairs = self._b90_pairs(view)
        if not pairs:
            return None
        tgt = o.get("target")
        for cn, area, om, ml_free in pairs:
            dest = _move_dest(area, o.get("card"))
            if dest is None or dest == area:
                continue
            # (i) 供給源側を動かす
            if tgt == om and dest not in forbidden_of(om):
                return PRIORITY["ML供給隔離_同室単独"]
            # (ii) 犯人側を動かす（行き先に ML でありうる者が居ない時だけ）
            if tgt == cn and dest in ml_free and dest not in forbidden_of(cn):
                return PRIORITY["ML供給隔離_同室単独"]
        return None

    def _b90_pairs(self, view: dict) -> tuple:
        """B-90(a) の成立ペア (犯人, 部屋, 同室の唯一の他人, MLフリーな行き先集合) 一覧。

        ターン内で不変（盤面・belief・犯人候補はターン境界でしか動かない）＝
        **(loop, day) をキーに1回だけ計算する**（option 毎に belief の周辺確率を組み直すと
        ベンチ所要が4倍になる実測 2026-07-28）。
        """
        key = (view.get("loop"), view.get("day"))
        if getattr(self, "_b90_key", None) == key:
            return self._b90_cache
        self._b90_key = key
        self._b90_cache: tuple = ()
        today = view.get("day")
        if today not in getattr(self, "_lethal_days", ()) or self._belief is None:
            return self._b90_cache
        cands = getattr(self, "_culprit_cands", {}).get(today, ())
        if not cands:
            return self._b90_cache
        # ★臨界犯人の一意性（B-90c・random_BTX s9 の退行検死 2026-07-28）：
        #   「冷却1枚では止まらない臨界」の犯人候補が**2人以上**居る日は、移動1枚で折れるのは
        #   そのうち1人だけ＝残りの経路がそのまま通る＝席の浪費になる（s9 L1D2＝手先と
        #   アルバイトが同時に unrest1/th1・折れるのは手先側だけで事件は素通り、実測）。
        #   ∴ 脅威の実現経路がちょうど1本の日だけ発火する（＝「移動1枚で条件を外せる」の厳密化）。
        crit = [cn for cn in cands
                if (cc0 := self._alive(view, cn)) is not None
                and (th0 := unrest_threshold_of(cn)) is not None and th0 >= 1
                and cc0["unrest"] >= th0 and cc0["unrest"] - 1 < th0]
        if self._B90_UNIQUE_CRIT and len(crit) != 1:
            return self._b90_cache
        out = []
        marg = None
        for cn in crit:
            cc = self._alive(view, cn)
            # ②同室の他の生存キャラがちょうど1人（＝移動1枚で同エリア条件が確実に外れる）
            others = [oc["name"] for oc in view["characters"]
                      if oc.get("alive") and oc["name"] != cn
                      and oc.get("area") == cc["area"]]
            if len(others) != 1:
                continue
            if marg is None:
                marg = self._belief.role_marginals()
            om = others[0]
            if marg.get(om, {}).get("ミスリーダー", 0.0) <= self._B90_ML_EPS:
                continue        # 供給源になりえない＝引き剥がす価値が無い
            _ml_areas = {oc.get("area") for oc in view["characters"]
                         if oc.get("alive") and oc["name"] != cn
                         and marg.get(oc["name"], {}).get(
                             "ミスリーダー", 0.0) > self._B90_ML_EPS}
            out.append((cn, cc["area"], om,
                        frozenset(a for a in ("病院", "神社", "都市", "学校")
                                  if a not in _ml_areas)))
        self._b90_cache = tuple(out)
        return self._b90_cache

    # ------------------------------------------------------------------
    # ★B-94（2026-07-28）：B-90(a)（ML供給隔離）の**前日**拡張。B-77（押し切りへの
    #   前日冷却投資）と同型の「前日に投資する」語彙。
    #
    #   機序（手練れユーザーの実戦筋・btx_bomb 帯で再現）＝脚本は
    #   **D2＝不安拡大（非致死）／D3＝遠隔殺人（致死）**。B-90 は「今日が致死系事件日」を
    #   発火条件に持つため、**MLが不安を積む D2 に一度も発火しない**。D3 に発火しても
    #   犯人の不安は既に臨界＝mm の 不安+1 が冷却 -1 を相殺して分離だけでは割れない
    #   （btx_bomb s0 L1D3 実測＝分離済みでも 遠隔殺人 が発生）。
    #
    #   ∴ 前日の算術（今日の不安＋今日の供給 vs 明日の臨界）で折る：
    #     base = 今日の不安 ＋ mm伏せ札(0/1) − 冷却(0/1)     … MLを除いた今日の到達点
    #     **base == 臨界−1** の時だけ発火＝「今日のML+1がちょうど明日の臨界を作る」
    #       （base < 臨界−1 ＝ML+1が入っても明日は冷却で足りる／base ≥ 臨界＝MLを
    #        止めても明日は臨界＝前日手が空振り＝どちらも発火しない）。
    #   供給経路の形（位置の狭さでML同定を代替＝B-90と同じ思想）：
    #     (same) 犯人と同室の生存キャラが**ちょうど1人**でMLでありうる → どちらかを動かす
    #     (adj)  MLでありうるキャラが**1移動で届く隣室**に居て**mmが今ターン札を伏せている**
    #            （＝寄せの線）→ 移動禁止で寄せを打ち消す／犯人を**その隣室の対角**
    #            （＝1移動では届かないエリア）へ逃がす
    #
    #   ★同日の穴（probe 実測 2026-07-28）＝前日に折っただけでは反転しない。前日で不安を
    #     1下げると、翌日は「不安＝臨界−1」になり **B-90 の発火条件（不安＝臨界ちょうど）を
    #     外れる**＝翌日は無防備になり、mm が 不安+1 を伏せて再度MLを寄せれば臨界に届く
    #     （btx_bomb s0 L1D2 に前日手を入れた実測＝L1D3 で 遠隔殺人 が発生）。
    #     ∴ 本述語は **今日／明日の両方**の致死系事件日に同じ算術（mm伏せ札込み）を当てる
    #     ＝B-90 が見ていない「mm札が冷却を相殺する」ケースを埋める（B-64 と同じ算術）。
    _B94_ML_PREDAY: bool = True      # 掃引・A/B用（False で B-94 導入前に bit 一致）
    _B94_ADJACENT: bool = True       # (adj) 経路（隣室＋mm札）も見るか
    _B94_NEED_COOL: bool = True      # 両輪ゲート＝先席が既にその日の犯人を冷やした日だけ
    _B94_SAMEDAY: bool = True        # 今日の致死日にも同じ算術を当てる（上記「同日の穴」）
    _B94_TOMORROW: bool = True       # 明日の致死日（＝本来の前日拡張）を見るか
    #: 冷却1枚を「まだ席で出せる」だけで数えるか（B-64 と同じ資源会計）。
    #  ★掃引で **False（＝実際に置かれた冷却しか数えない）を採用**：True は btx_bomb 帯を
    #  さらに縮める（5.20→3.00）一方で btx_future 帯を 4.10→6.30 に崩し、防衛が 127→123
    #  へ落ちた（3日級・2026-07-28 実測）。「先席が実際に冷やした日だけ2席目を使う」という
    #  B-90 の両輪設計をそのまま踏襲する。
    _B94_COOL_SEAT: bool = False
    #: 同上を**当日枝だけ**に許すか。掃引実測（3日級）は _B94_COOL_SEAT=True と
    #  **完全に同一**（btx_future 帯の崩れは当日枝が原因と判明）＝こちらも不採用。
    _B94_COOL_SEAT_TODAY: bool = False
    _B94_PIN: bool = True            # (adj) を 移動禁止 で折ってよいか（1/loop資源）
    #: (adj) の「犯人を対角へ逃がす」枝で、行き先の ML フリー要求を外すか。
    #  ★薄い事前（序盤ループ）では**全ての有人エリアが「MLでありうる」**＝ml_free が
    #  実質 空 になり、移動枝が構造的に死ぬ（btx_bomb L1D2 実測＝ml_free={'病院'} のみ）。
    _B94_DIAG_LOOSE: bool = False
    #: 発火を許す最小ループ番号（1＝制限なし）。
    #  ★掃引で **2 を採用**（3日級・2026-07-28 実測）：1（制限なし）は btx_bomb 帯 7局を
    #  7→4 に改善する一方、L1 で守れていた 3局（s7-s9）を 1→4 に落とす（＝per-game 退行3）。
    #  2 にすると **改善7局・退行0局**（btx_bomb 帯 5.20→3.10・3日平均 3.131→2.969）。
    #  理由づけ＝L1 は情報ゼロ＝belief が一様で「MLでありうる」が全員に立つ日＝位置の狭さ
    #  だけを根拠にした本述語は L1 では賭けになる（評価哲学＝L1防衛は主として脚本家事故）。
    #  ★ユーザー実戦の症例もL5/L6＝**情報が溜まった後**の穴＝適用場面と一致する。
    _B94_MIN_LOOP: int = 2

    def _b94_ml_isolate_preday(self, o: dict, view: dict) -> float | None:
        """致死事件へ向けた**前日／当日（mm札込み算術）**のML供給隔離（B-94）。

        返り値＝PRIORITY["ML供給隔離_前日"]（成立時）／None（不成立）。
        受ける option＝移動カード（犯人 or 供給源を動かす）／移動禁止（寄せの打ち消し）。
        """
        if not self._B94_ML_PREDAY or o.get("target_kind") != "character":
            return None
        card = o.get("card") or ""
        if card not in _MOVE_TOGGLE and card != "移動禁止":
            return None
        pairs = self._b94_pairs(view)
        if not pairs:
            return None
        tgt = o.get("target")
        for cn, c_area, om, om_area, mode, ml_free in pairs:
            if card == "移動禁止":
                # (adj) だけ＝寄せ移動の打ち消し（mm札のある供給源にしか意味が無い）
                if self._B94_PIN and mode == "adj" and tgt == om:
                    return PRIORITY["ML供給隔離_前日"]
                continue
            dest = _move_dest(c_area if tgt == cn else om_area, card)
            if dest is None or dest == (c_area if tgt == cn else om_area):
                continue
            if mode == "same":
                # (i) 供給源側を動かす（犯人の部屋から出す）
                if tgt == om and dest != c_area and dest not in forbidden_of(om):
                    return PRIORITY["ML供給隔離_前日"]
                # (ii) 犯人側を動かす（MLでありうる者の居ないエリアへ）
                if tgt == cn and dest in ml_free and dest not in forbidden_of(cn):
                    return PRIORITY["ML供給隔離_前日"]
            elif tgt == cn and dest == _diag_area(om_area) \
                    and (dest in ml_free or self._B94_DIAG_LOOSE) \
                    and dest not in forbidden_of(cn):
                # (adj) 犯人を**寄せ元の対角**へ＝mmの移動札1枚では届かないエリア
                #   （2×2盤で↑↓/←→の1枚では対角に行けない。斜めはmmの1/loop札）。
                return PRIORITY["ML供給隔離_前日"]
        return None

    def _b94_pairs(self, view: dict) -> tuple:
        """B-94 の成立経路 (犯人, 犯人エリア, 供給源, 供給源エリア, mode, MLフリー) 一覧。

        ★キャッシュ鍵に**冷却フラグ**を含める（B-90 との差）：本述語の「冷却」は先席が
        置いたかどうかで席ごとに変わる＝(loop, day) だけを鍵にすると先席（未冷却＝不成立）の
        結果を後席が引いて**恒久沈黙**する（probe 実測 2026-07-28）。
        """
        # ★両輪ゲート（B-90b と同型）：分離は**冷却の2席目**でしか意味を持たない。
        #   「冷却」＝今日どこかの席がその事件日の犯人を冷やしたこと（_cooled_days は
        #   冷却対象が犯人候補である**事件日**を記録する＝前日の予防冷却でも入る）。
        today = view.get("day") or 0
        _cd = getattr(self, "_cooled_days", ())
        _lethal = getattr(self, "_lethal_days", ())
        # 明日を先に見る（前日手＝本チケットの主目的）→ 次に今日（mm札込みの同日拡張）
        days = ([today + 1] if self._B94_TOMORROW else []) \
            + ([today] if self._B94_SAMEDAY else [])
        # ★冷却の会計＝既定は「実際に置かれた冷却だけ」（B-90 の両輪と同じ席順ゲート）。
        #   B-64 と同じ**資源会計**（今日この後どこかの席が 不安-1 を出せるなら1枚数える）は
        #   _B94_COOL_SEAT* で試験できるが、掃引では **btx_future 帯を崩して不採用**
        #   （席順ゲートだと冷却が最終席に来た日に発火できない＝btx_bomb s7 L1D3 実測。
        #    それを直す代わりに別帯を壊す＝トレードオフとして負けている）。
        _seat_cool = ((self._B94_COOL_SEAT or self._B94_COOL_SEAT_TODAY)
                      and self._b63_seat_can_play(view, "不安-1"))
        key = (view.get("loop"), today, _seat_cool, tuple(d in _cd for d in days))
        if getattr(self, "_b94_key", None) == key:
            return self._b94_cache
        self._b94_key = key
        self._b94_cache: tuple = ()
        if not self._B94_ML_PREDAY or self._belief is None:
            return self._b94_cache
        if (view.get("loop") or 1) < self._B94_MIN_LOOP:
            return self._b94_cache
        mm_now = {p.get("target") for p in view.get("placements", [])
                  if p.get("owner") == "mastermind"
                  and p.get("target_kind") == "character"}
        crit_all: list[str] = []
        for _d in days:
            if _d not in _lethal:
                continue
            cands = getattr(self, "_culprit_cands", {}).get(_d, ())
            if not cands:
                continue
            cooled = (_d in _cd) or (_seat_cool and (
                self._B94_COOL_SEAT or _d == today))
            if self._B94_NEED_COOL and not cooled:
                continue
            cool = 1 if cooled else 0
            for cn in cands:
                cc0 = self._alive(view, cn)
                th0 = unrest_threshold_of(cn)
                if cc0 is None or th0 is None or th0 < 1:
                    continue
                # base＝MLを除いたその日の到達点（mm伏せ札は不安+1でありうる／冷却は先席）
                if cc0["unrest"] + (1 if cn in mm_now else 0) - cool == th0 - 1:
                    crit_all.append(cn)
            if crit_all:
                break      # 近い方の脅威日を優先（前日＞当日の順で1日だけ見る）
        # ★臨界一意性（B-90c と同型）：経路が2本以上ある日は移動1枚では折れない＝席の浪費。
        if len(set(crit_all)) != 1:
            return self._b94_cache
        cn = crit_all[0]
        cc = self._alive(view, cn)
        marg = self._belief.role_marginals()
        ml_areas = {oc.get("area") for oc in view["characters"]
                    if oc.get("alive") and oc["name"] != cn
                    and marg.get(oc["name"], {}).get(
                        "ミスリーダー", 0.0) > self._B90_ML_EPS}
        ml_free = frozenset(a for a in ("病院", "神社", "都市", "学校")
                            if a not in ml_areas)
        out = []
        others = [oc["name"] for oc in view["characters"]
                  if oc.get("alive") and oc["name"] != cn
                  and oc.get("area") == cc["area"]]
        if len(others) == 1 and marg.get(others[0], {}).get(
                "ミスリーダー", 0.0) > self._B90_ML_EPS:
            out.append((cn, cc["area"], others[0], cc["area"], "same", ml_free))
        elif self._B94_ADJACENT and cc["area"] not in ml_areas:
            # (adj) 犯人の部屋に供給源が居ない日＝mmの寄せ移動が唯一の供給線。
            #   1移動で届く隣室（＝対角以外）に居て、mmが今ターン札を伏せた者だけ。
            _diag = _diag_area(cc["area"])
            for oc in view["characters"]:
                if not oc.get("alive") or oc["name"] == cn:
                    continue
                if oc["name"] not in mm_now or oc.get("area") in (cc["area"], _diag):
                    continue
                if marg.get(oc["name"], {}).get(
                        "ミスリーダー", 0.0) <= self._B90_ML_EPS:
                    continue
                if cc["area"] in forbidden_of(oc["name"]):
                    continue      # そのエリアへは寄せられない＝供給線が無い
                out.append((cn, cc["area"], oc["name"], oc["area"], "adj", ml_free))
        self._b94_cache = tuple(out)
        return self._b94_cache

    def _b52_kill_ml_separation(self, o: dict, view: dict,
                                ml_name: str | None, ml_p: float,
                                ml_ties: tuple[str, ...] = ()) -> float | None:
        """kill事件の犯人を同エリアの ML から引き離す（B-52・fs5_guard 検死）。

        条件（FableA裁定＝自明情報のみ・B-35型回避で絞る）：
          ①今日/近接に kill事件が予定（_lethal_days）で **KP が生存**（KP死＝即敗北）
          ②ML 候補の推定確度 ≥ _B52_ML_MIN（掃引で較正）
          ③犯人候補が ML と同エリア（＝ML の +1 で不安が積まれる）
          ④**冷却1枚では臨界を割れない**（unrest==臨界ちょうど＝-1で床に落ちても ML+1 で戻る）
          ⑤option が「犯人 or ML を相手のエリアから引き離す移動」
        ★B-56：ml_ties＝argmax と同率タイ（ε内）の他の ML 候補。①〜④の発火判定は
          従来どおり argmax（ml_name）で行い、⑤の引き離し対象だけを「犯人と同エリアに
          居るタイ候補」へ拡張する（浮動小数点ノイズで argmax が誤った側に倒れても、
          正対象の分離が同点候補に載る＝採用は option 列の決定的順序）。
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
                if tgt == cn and dest != mlc["area"] and dest not in forbidden_of(cn):
                    return PRIORITY["危険犯人_ML分離_kill"]
                # ★B-56：引き離し対象＝argmax ML ＋ 同率タイ候補のうち犯人と同エリアの者
                #   （タイ候補は同エリア要件をここで課す＝発火③は argmax のまま）
                for _m2 in (ml_name, *ml_ties):
                    if tgt != _m2 or _m2 == cn:
                        continue
                    _m2c = self._alive(view, _m2)
                    if (_m2c is not None and _m2c["area"] == cc["area"]
                            and dest != cc["area"]
                            and dest not in forbidden_of(_m2)):
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

    def _initial_area_map(self, view: dict) -> dict:
        """★B-103 論点B：各キャラの**初期エリア**（公開情報）の写像。

        ボードX は「クロマク／ウィッチの**初期エリア**」で決まる
        （`rules/40_first_steps.md:41`・`rules/50_basic_tragedy_x.md:52`）。
        優先順（すべて公開情報・推定を混ぜない）：
          1. **ループ初日に観測した盤面**（`_loop_initial_areas`）＝手先/従者のように
             脚本家がループ毎に初期エリアを指定するキャラを含めて正しい（`rules/60: A11`）。
          2. **キャラクターカードの初期エリア**（`engine.data.initial_area_of`）＝
             まだ登場していない（観測時 area=None）キャラの補完。
          3. どちらも無ければ**現在地**（＝従来動作＝健全側フォールバック）。
        """
        obs = getattr(self, "_loop_initial_areas", None) or {}
        out: dict = {}
        for c in view.get("characters", []):
            n = c.get("name")
            out[n] = obs.get(n) or initial_area_of(n) or c.get("area")
        return out

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
            probs = _defeat_board_probs(
                self._belief, view,
                initial_areas=self._initial_area_map(view))
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

    def _b93_void_futile(self, view: dict, tgt: str,
                         danger_board: str | None) -> bool:
        """B-93：**void（mmが今ターンその板に札を置いていない）板への暗躍禁止**のうち、
        「盤面の理由からも無駄」と公開情報だけで言える2ケース。呼び出し側が void を保証する。

        ルール接地（なぜ void の板暗躍禁止が構造的にゼロ効果か）：
          - 暗躍禁止は「**重なった**暗躍+1/+2を無効化」＝同じ対象に置かれた札しか消せない
            （KB 10:61）。既にボードに載っているカウンターは除去しない。
          - 暗躍禁止は**行動解決フェイズでのみ**有効＝クロマク等（脚本家能力フェイズ）や
            事件由来の暗躍は止まらない（KB 10:65／60 C-5＝書 Q16／50 クロマク）。
          ∴ mm札が無い板への暗躍禁止は「今ターン打ち消せる暗躍+」が存在しない＝空振り。

        ★ただし **void だけを理由に一律降格するのは過去に退行済み**（コメント：FSの
          黒猫/クロマクで育つ真の敗北ボードを降格し def→loss）＝void は必要条件に留め、
          「その板が敗北に接続しない」or「そのループが既に決着済み」を**併せて**要求する：

          (c) `_board_threat_live(view, tgt) is False`＝可能世界に残る敗北ルール・予定事件・
              ファクター状態能力のどの経路にも接続しない板（＝ユーザー指摘「神社はすでに
              敗北条件でないと分かっている」）。**void 限定で使う**＝mm札のある板に同じ
              判定を広げた版は 5日級 def→loss の退行実績あり（`_board_threat_live` docstring）。
          (d) `_loop_lost`＝推定敗北板が既に暗躍≥2で今ループの盤面敗北は成立済み。別の板の
              空振り札を足しても何も変わらない（fs5_guard s8＝学校2到達後に神社へ3枚）。
              tgt が推定敗北板そのものの時は既存の `_board_too_late` が拾う＝ここは対象外。
        """
        if _B93_MODE == "off":
            return False
        if _B93_MODE in ("c", "cd") and not self._board_threat_live(view, tgt):
            return True
        if _B93_MODE in ("d", "cd") and getattr(self, "_loop_lost", False) \
                and tgt != danger_board:
            return True
        return False

    @staticmethod
    def _b66_unproven_from_history(view: dict) -> set[str]:
        """B-66(2)：敗北ループを2回以上観測し、その全てでループ終了時暗躍<2だった板の集合。

        根拠（ルール接地）＝板系の敗北条件（守るべき場所=学校/封印されしモノ=神社/
        復讐者の灯火・巨大時限爆弾X=ボードX）は全て「ループ終了時に暗躍カウンター2つ以上」
        （KB 40:42-47・50:36-52）＝<2で終わった板はその敗北の原因ではありえない。
        loop_result（敗北）と loop_board（ループ終了時の板暗躍）はどちらも公開情報。
        敗北1回では帰属が曖昧＝2回以上を要求（_observed_defeat_board の≥2と同水準の保守性）。
        ★これは「囮の実証」ではなく「実証ゼロの継続」＝減点にだけ使い、mm札の吸収
        （等価交換＝§1i）自体は ボード封じ_未実証(74.5) で引き続き許す。"""
        hist = view.get("history", [])
        defeat_lps = {e.get("loop") for e in hist
                      if e.get("event") == "loop_result"
                      and "敗北" in str(e.get("result", ""))}
        bd_end = {e.get("loop"): e.get("board_anyaku", {})
                  for e in hist if e.get("event") == "loop_board"}
        seen = [lp for lp in defeat_lps if lp in bd_end]
        if len(seen) < 2:
            return set()
        return {b for b in ("病院", "神社", "都市", "学校")
                if all(bd_end[lp].get(b, 0) < 2 for lp in seen)}

    def _b66_ito_selfharm_invest(self, view: dict, tgt: str) -> bool:
        """B-66(1)：因果の糸下の友好投資が自傷になるか（5日級検死#13 §6・提案⑤）。

        因果の糸（KB 50:85）＝各ループ開始時、前ループ終了時に友好が置かれていた全員に
        不安+2。糸が濃厚（p≥0.7・【強制】効果の観測で belief が確定に至る）な非最終
        ループでは、友好投資はループ終了時に友好が残り次ループ開始+2の自傷になる。
        特に対象が事件犯人候補（臨界≥1）だと mm の不安供給に+2の頭金を渡す＝1/loopの
        不安-1（チーム3枚）では取り返せない恒久赤字（#13＝糸+2で 2+5-3=4≥臨界3・検死§6。
        ※公開済みフレンドはループ開始の自動友好+1で糸+2が投資ゼロでも残る＝ゲートの
        効果は「回避可能な頭金の停止」＋「自傷投資に浪費していた席の防衛への再配分」）。
        最終ループは次ループが無い＝自傷なし（FBタメ投資は最終ループに寄せる＝検死提案⑤）。
        TTガード（【強制】友好禁止無視の確定防御）・冷却役解禁・浄化係は呼び出し側で
        この判定より先に return 済み＝対象外。

        ★発火は「観測された敗北が全てキャラ線」の実証つき（全4板が _b66_unproven＝
        敗北2回以上の全てで全板<2）に限定する：板線（封印等）が敗北実証に混じる脚本で
        候補集合（序盤ほぼ全員）に発火させると、糸脚本の情報収集投資（開示で敵役職を
        特定して勝つ筋）が全面停止し壊滅する（btx5_seal 実測＝防衛7→0）。逆に#13は
        契約・自殺・SK＝キャラ線敗北のみ＝全板無実証が2敗目で確定し、以後の投資は
        「mmが実際に勝ち続けている線への頭金」＝止めてよいことが公開情報から言える。
        """
        if getattr(self, "_ito_p", 0.0) < 0.7:
            return False
        if "loops_total" not in view or view["loops_total"] - view["loop"] < 1:
            return False   # 最終ループ or loops_total 不明（合成view）＝発火しない（健全側）
        if len(getattr(self, "_b66_unproven_boards", ())) < 4:
            return False   # 板線の敗北実証（または敗北2回未満）＝キャラ線帰責が立たない
        if tgt not in self._culprits:
            return False   # 自傷が敗北算数に直結するのは犯人候補（臨界到達の頭金）のみ
        return bool(unrest_threshold_of(tgt))   # 臨界0（黒猫）は+2で何も変わらない

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
        if B100_HOOK is not None or self.B100_MIX:
            self._b100_plan = None        # ★B-100：この席の計画スタッシュを毎回作り直す
        self._plan_recs = self._defense_plan_recs(view, options)
        keyperson = self._keyperson
        killer = self._killer
        # ★B-37：per-board「そのボードが敗北ボードでありうるか」を belief から先に算出
        #   （_defeat_board_probs＝per-board 単一ソース）。暗躍禁止設置ゲートが共用。
        from agents.defense_plan import _defeat_board_probs
        # ★B-103 論点B：board_x（復讐者=クロマク／爆弾X=ウィッチ）の板は**初期エリア**で
        #   決まる（rules/40:41・50:52）。手先/従者はカードから決まらない＝ループ初日に
        #   観測した初期配置（公開情報）を渡す。他は engine.data の静的初期エリアが使われる。
        self._board_defeat_probs = _defeat_board_probs(
            self._belief, view, initial_areas=self._initial_area_map(view))
        # ★B-84：同じ per-board 確率から「先頭グループ」を作る（96点の板選択タイブレーク用）。
        self._b84_top_boards = b84_top_defeat_boards(self._board_defeat_probs)
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
                          if _cult_marg.get(n, {}).get("カルティスト", 0.0)
                          > self._CULT_MAYBE_P}
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
        # ★B-54（2026-07-25 ミニ検死 提案①）：当日臨界の犯人×同エリアMLの分離とピンが
        #   席競合する日は、ピンが譲る（99→68）。fs5_guard s3/s5 L2D4＝ピン99が分離70を
        #   締め出してKP死・s3 L6D2＝同型でボード線、を実測（詳細＝監査doc§7）。
        _b54_pin_yield = (_cultist_pin_target is not None
                          and self._b54_pin_should_yield(view, options))
        # ★B-67（目視検死T1）：供給役の復帰・搬入の当日標的＝「実証済み敗北ボード×
        #   盤面が閾値-1（あと1点で敗北）×確信0.7の供給役がボード外×mmが今ターン
        #   その供給役に伏せ札」。クロマク＝復帰すれば能力+1が届く（伏せ札だけで成立）。
        #   カルティスト＝搬入は暗躍禁止の無効化＝mmが同時にそのボードへ暗躍札を
        #   置いた日のみ脅威（フェリーピン_コンボと同じ二条件）。
        self._b67_return_targets: set[str] = set()
        self._b67_kuro_targets: set[str] = set()
        if (self._B67_RETURN_DEFLECT and danger_board is not None
                and danger_board == getattr(self, "_observed_defeat_board", None)
                and (view.get("board_anyaku") or {}).get(danger_board, 0) + 1 >= 2):
            # クロマク側＝確信0.7 か、B-59と同じ公開実証（噂と識別可能な板供給の顔ぶれ
            #   ×クロマク周辺 argmax≥0.15）。FS s12実測＝委員長が0.3の壁に恒久に届かず、
            #   B-59の退避(87)はボード上でしか撃てない＝復帰日（ボード外）に応手ゼロだった。
            _marg67 = self._belief.role_marginals()
            _best67 = max((d.get("クロマク", 0.0) for d in _marg67.values()),
                          default=0.0)
            _pump67 = getattr(self, "_mm_pump_board_present", {}).get(
                danger_board, ())

            def _b67_kuro_ok(n: str) -> bool:
                if n in self._kuromaku_suspects:
                    return True
                if n == "巫女" and danger_board == "神社":
                    return False   # 浄化役の復帰は封じない（B-59と同じ例外）
                _p = _marg67.get(n, {}).get("クロマク", 0.0)
                return (n in _pump67 and _p >= self._B59_KM_MIN
                        and _p >= _best67 - 1e-9)

            # ★B-75/B-74：カルティスト搬入路のゲートに∩実証成員を追加（確信0.7が
            #   巫女常駐プラトーで恒久に立たない脚本の解毒）。露見実験ゲート必須
            #   （per-target＝応手対象自身は冷却優先スキャンから除外）。
            _b74_cult = set(self._cultist_suspects)
            for _si in getattr(self, "_b74_smuggle_inter", ()):
                if self._b75_exposure_ok(view, _si):
                    _b74_cult.add(_si)
            for _s in set(_marg67) | _b74_cult:
                _is_kuro = _b67_kuro_ok(_s)
                if not _is_kuro and _s not in _b74_cult:
                    continue
                if not _is_kuro and danger_board not in mm_board_now:
                    continue   # カルティスト搬入は「ボードへの同時札」がある日のみ
                _cs = self._alive(view, _s)
                if (_cs and _cs["area"] != danger_board and _s in mm_char_now
                        and danger_board not in forbidden_of(_s)):
                    self._b67_return_targets.add(_s)
                    if _is_kuro:
                        self._b67_kuro_targets.add(_s)

        # ★B-28：card_effect.noop_reason（単一チョークポイント）に渡す文脈。
        #   defense_plan の break 生成も同じ述語＋同じ材料を見る＝PLAN_HOT(+88) が heuristic の
        #   空振りゲートを構造的に上書きする事故（B-21/B-21b/B-26）を封鎖する。
        #   ★このターン内で不変＝**1回だけ構築**する（option毎に組むと frozenset 4個の割当が
        #   全候補×全席×全ターンに乗る＝素直に無駄。※性能上の必須ではない：ベンチ所要は
        #   hoist 前後とも 22秒台で有意差なし＝「候補数ぶん同じ物を作らない」という形の問題）。
        _noop_ctx = NoopCtx(
            mm_chars=frozenset(mm_char_now),
            mm_boards=frozenset(mm_board_now),   # ★B-103：板の空振り判定(G7)の材料
            keyperson=keyperson,
            kill_zone=_today_kill_zone,
            kuromaku_suspects=frozenset(getattr(self, "_kuromaku_suspects", ())),
            killer_suspects=frozenset(getattr(self, "_killer_suspects", ())),
            friend_guards=frozenset(getattr(self, "_friend_guards", ())),
            # ★B-86'：友好+ の空振り判定(G8)の材料（すべて公開情報＋belief）
            gw_keep=frozenset(self._b86_gw_keep),
            gw_refused=frozenset(self._b86_gw_refused),
            gw_ignore_certain=frozenset(self._b86_gw_ignore_certain),
            gw_info_exhausted=frozenset(self._b86_gw_info_exhausted),
            gw_arms_mm=frozenset(self._b86_gw_arms_mm),
            # ★B-109：このターン何も変えない手（G9/G10）の材料
            gw_final_void=frozenset(getattr(self, "_b109_gw_final_void", ())),
            gw_final_harm=frozenset(getattr(self, "_b109_gw_final_harm", ())),
            unrest_void=frozenset(getattr(self, "_b109_unrest_void", ())),
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
                    # ★B-66(2)：敗北2回以上の全てで暗躍<2に終わった板（実証ゼロの継続）は
                    #   96→74.5 に減点＝mm札の吸収（等価交換・§1i）は引き続き許しつつ、
                    #   冷却・分離・退避（75〜82級）の実需要があれば席を譲る（#13＝契約敗北を
                    #   2回観測した後も神社囮へ毎日96で吸われた・検死§6）。実証済み危険板
                    #   （danger_board）は対象外＝真のゴール防衛は降格しない。
                    if tgt != danger_board \
                            and tgt in getattr(self, "_b66_unproven_boards", ()):
                        return PRIORITY["ボード封じ_未実証"]
                    # 複数ボードに置かれた時は危険ボード（実証済み敗北ボード等）を優先
                    # （同点rngで病院＝事件専用ボードに流れて真のゴールを素通しした実測）
                    # ★B-84：それでも同点になる局面（danger_board が未確定＝FS初日など）は
                    #   **列挙順（病院→神社→都市→学校）**で決まっていた＝BTX固定の神社が
                    #   FSでも学校に先行した（実戦FB）。belief の per-board 敗北確率の
                    #   先頭グループにだけ +_B84_TIEBREAK（<1.0＝暗躍段差を覆さない純タイブレーク）。
                    #   **板が2枚以上置かれた時だけ**発火＝1枚しか無い局面の挙動は完全に不変。
                    _b84 = (_B84_TIEBREAK
                            if (len(mm_board_now) >= 2
                                and tgt in getattr(self, "_b84_top_boards", ()))
                            else 0.0)
                    return PRIORITY["ボード封じ_mm札"] + view["board_anyaku"].get(tgt, 0) \
                        + (4.0 if tgt == danger_board else 0.0) + _b84
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
                    # ★★B-103（2026-07-30・手練れユーザーが2度指摘した疑問手）：
                    #   void な板への暗躍禁止は **KB から算術的にゼロ**＝B-28 の単一
                    #   チョークポイント（`card_effect.noop_reason` G7）で一括して no-op
                    #   にする。キャラ版 G1（`return _np.score`＝3.5）と同じ扱いに揃える。
                    #   ★なぜ「見えている暗躍>0」でも守る価値が無いか：暗躍禁止が消せるのは
                    #     **今ターン重なった暗躍+カード**だけ（`rules/10:61`）＝既に載っている
                    #     カウンター（黒猫のループ開始 神社+1＝`rules/30:75` 等）は減らない。
                    #   ★実測（`arena/void_audit.py`・掃引 doc §3c）＝この1行で
                    #     空振り席 3日級 27→0／5日級 30→1。防衛は 128→129／66→66
                    #     （降格先 3.5〜30 で防衛はプラトー＝籤の出目ではない）。
                    #   ★下の3つの void 限定ゲート（邪気×封印／B-3／B-93）は **void を
                    #     必要条件にしている**＝本ゲートに吸収され到達しない。統合（削除）の
                    #     可否はFableA裁定＝ここでは既存コードを残す（可逆にするため）。
                    if (_np := noop_reason(view, card, tgt, kind,
                                           _noop_ctx)) is not None:
                        return _np.score
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
                    # ★B-93：void かつ「敗北に接続しない板」or「今ループ決着済み」なら降格。
                    #   降格先は _B93_SCORE＝**機会費用の閾値**（これを超える代替手がある席
                    #   だけ明け渡す＝他に打つ手が無ければ従来どおりここに置く）。
                    if _board_void and self._b93_void_futile(view, tgt, danger_board):
                        return _B93_SCORE
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
                                # ★B-77：押し切り前日の犯人カバー＝82.5へ昇格。76のままだと
                                #   TT/FB投資(82/81)に席負けし、前日+2の頭金（札+ML自己）を
                                #   許して当日がmateになる（s0実測＝D2開始時に臨界2）。
                                if tgt == getattr(self, "_b77_prep", None):
                                    return max(danger * 0.8,
                                               PRIORITY["前日カバー_押切"]) - dup
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
                if _b54_pin_yield:
                    # ★B-54：当日臨界の分離（70/93.5）に席を譲る（分離不在なら従来99）
                    return self._B54_YIELD_SCORE
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
                # ★B-58：急所（公開済みフレンド/実証VIP）版＝KP単独形の対称拡張。
                #   フレンド死亡＝ループ終了時敗北＝KP死と同格なのに、汎用SK配達ピン(72)は
                #   最終ループ限定・KP単独(101)はKP専用＝公開済みフレンドが対象外だった。
                #   random_BTX#12実測（検死doc §4）：mm 移動↑↓→神格（SK）でフレンド
                #   男子学生（L2ループ終了で公開済み）が1人きりの学校へ配達され7ループ即死＝
                #   classify_day の防御例は 移動禁止→神格 1枚。発火は狭い＝
                #   「公開/実証済みの急所 × 1人きり × mmがSK候補に伏せ札 × 別エリア」のみ。
                if self._b58_vip_solo_deliverable(view, tgt, keyperson):
                    # ★B-81（レース監査T-r1・2026-07-27）：公開フレンド死＝ループ喪失確定
                    #   なのに、当日冷却(102級)×3に**1点差で3席連続締め出し**される逆転が
                    #   3日BTX#12で実測（L2-L4のD1×3回＝フレンド即死）。押し切り日
                    #   （B-77述語＝当日/翌日の蝶・自殺×単独犯人×ML自己ポンプ×臨界≤3）
                    #   **でない**時だけ102.5へ状況昇格＝A-63対応で較正済みの102帯冷却の
                    #   席は押し切り日には従来どおり守る（場合分け掃引の採用形）。
                    if self._b81_promote_ok(view):
                        return PRIORITY["SK配達ピン_急所単独_昇格"]
                    return PRIORITY["SK配達ピン_急所単独"]
            # ★B-71：SK配達の**被害者側**ピン（A-64対応・2026-07-27）。B-58はmmが
            #   SK候補に伏せた札（SK側の配達）を封じるが、A-64のsk_deliverは**急所側に
            #   移動札を伏せて運ぶ**＝主人公は急所への伏せ札を暗躍と読み暗躍禁止(100-101)を
            #   当て続け、同一席の択（主人公は同一対象に1枚）で移動禁止(89)が負けていた
            #   （完全情報basic L1D3実測＝KPがSK単独エリアへ配達され即死）。
            #   「SK単独エリアが実在×急所に伏せ札×キラー線が今日死んでいる」の狭い述語が
            #   成立する日だけ、移動禁止→急所を暗躍禁止(100-101)より上げる（102）。
            if card == "移動禁止" and kind == "character" and tgt in mm_char_now \
                    and self._b71_victim_delivery_pin(view, tgt, keyperson, killer):
                return PRIORITY["SK配達ピン_急所伏せ札"]
            # ★B-64：mm移動札のML同ターン寄せブロック（5日級検死#2＝蝶の当日に
            #   mmがML候補を移動札で犯人へ寄せ、冷却1席では 2+1(札)+1(ML)-1=臨界 到達。
            #   `移動禁止→ML候補` で寄せを打ち消せばML+1が届かない＝冷却と対の二席防御。
            #   ピボット算術＋確度ゲート＋mm伏せ札の狭い述語＝_b64_ml_approach_block）。
            if card == "移動禁止" and kind == "character" \
                    and self._b64_ml_approach_block(view, tgt, mm_char_now):
                return PRIORITY["ML寄せブロック"]
            # ★B-94：B-64 の**前日**版（ML同定を要求しない代わりに位置と算術で絞る）。
            #   明日が致死系事件日で、mmが今ターンML候補に伏せた札で犯人へ寄せると
            #   明日の臨界が完成する日＝寄せを 移動禁止 で打ち消す。
            if card == "移動禁止" and kind == "character":
                _b94p = self._b94_ml_isolate_preday(o, view)
                if _b94p is not None:
                    return _b94p
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
                    # ★B-65排他：幻想VIPを今日すでに板札で退避済みなら護衛ピンは不要
                    #   （退避後にピンで護衛役を3人形の外に固定＝自作2人きりの逆順封鎖）
                    if vip == "幻想" and getattr(self, "_genso_evac_today", False):
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
            # ★B-67（目視検死T1）：供給役＝クロマク確信の復帰・搬入ピン。mmが今ターン
            #   伏せた札（＝復帰移動の線）を移動禁止で封じ、能力+1/日の的を敗北ボードから
            #   逸らす。フェリーピン(93)のクロマク版・同格＝暗躍禁止→ボード(96-101)の
            #   二段防御の片翼（FS s0実測＝確信済みクロマクの最終日復帰への応手42が
            #   友好filler64.8に負けて同じ最終日を3回落とした）。カルティスト側の
            #   復帰・搬入は下のフェリーピン系が受け持つ（重複新設しない）。
            if card == "移動禁止" and kind == "character" \
                    and tgt in getattr(self, "_b67_kuro_targets", ()):
                return PRIORITY["供給役復帰ピン"]
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
                # ★B-65：幻想の板札退避（5日級検死#0＝フレンド幻想のSK2人きり死×6）。
                #   幻想は行動カード被セット不可＝退避は「幻想のいるボードへの移動カード」
                #   しかない（幻想特性・KB:30。simは gensou_boards で合法手化済み＝採点
                #   だけ欠落していた）。実証急所（公開済みフレンド/実証VIP）の幻想が
                #   SK候補と同エリア×2人きりリスク日（既に2人きり or 他の同居者全員に
                #   mm伏せ札＝引き抜きの線）に、板札で幻想を退避（classify_dayの防御例＝
                #   移動←→→神社ボード）。判定は成立関数 _b65_genso_board_evac に集約。
                if gc and self._b65_genso_board_evac(view, card, tgt, gc,
                                                     mm_char_now):
                    return PRIORITY["幻想退避_実証急所"]
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
                # ★B-109 F3：不安臨界0＝発生条件が不安の値に依存しない（`rules/00:38`
                #   「この値**以上**の不安カウンターが置かれていると…」／`rules/30:53,77`
                #   ＝黒猫は臨界0で「不安カウンターが無くても発生条件を満たす」）。
                #   ∴ 不安を積んでも観測は1ビットも変わらない＝**実験として情報ゼロ**。
                #   ★ウイルス試験（`in_virus`）は切らない＝妄想拡大ウイルス(X) は不安3で
                #     パーソンをSK化する（`rules/50:79-81`）＝臨界0でも意味がある。
                if in_culp and self._b109_experiment_is_void(tgt):
                    in_culp = False
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
                                # ★B-58：フレンド候補の対称化＝フレンド死亡もループ終了時
                                #   敗北（KP死と同格）なのに、この安全確認は最終ループしか
                                #   フレンドを見ていなかった（btx_future#0 L3D2 実測＝
                                #   SKテストがフレンド候補を自らSKへ配達して殺させた・
                                #   検死doc §3「SKへの自走」）。確度ゲート付き＝低疑いの
                                #   犠牲テスト（情報源として load-bearing）は殺さない。
                                if self._b58_friend_victim_block(v, marg_fr):
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
                        # ★B-78(2)（検死3 T-C・random_FS#4実測）：**配達先への注入**＝
                        #   VIP本人にmmの伏せ札×destがSK候補の単独エリア（VIPの1移動圏＝
                        #   mmは斜め持ちで実質全エリア・VIPの禁止エリアは配達不能で対象外）
                        #   ×VIPのturn_end死の実証、の日に第三者をdestへ注入する。
                        #   VIPと同エリアのキラー疑いには暗躍禁止→VIP（別席・別対象）が
                        #   立つ＝**2席で「暗躍+2→キラー殺害」「移動→SK配達」の二枝を
                        #   同時に覆える**（同一対象1枚制約の下で単席では不可能＝s4の構造。
                        #   B-71のKPピンはキラー同居×中身の二枝で正しく沈黙する日）。
                        #   注入先が空振り（配達が来ない）ならSKと2人きり＝犠牲リスクは
                        #   実証ゲート（毎ループ死んでいるVIP線）で正当化（SKテスト同経済）。
                        #   ★KP限定（掃引の実測）：フレンドにも張る版は te_death 実証が
                        #   SKテスト死でも立ち、btx5_future#0/#3/#9・BTX#17が defense→fb_loss
                        #   （B-72/B-76 v1と同じフレンド席経済の毒）＝フレンド側はB-76/B-58の領分。
                        if (vip == keyperson and vip_c0 and tgt != vip
                                and vip in mm_char_now
                                and tgt not in getattr(self, "_friend_guards", set())
                                and self._b78_inject_at_delivery_dest(
                                    view, vip, vip_c0, tgt, dest)):
                            return PRIORITY["VIP注入_実証済"]
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
                        # ★B-63(3) 配達形への切替（5日級検死#16）：VIPが1人きり×mmがSK候補に
                        #   伏せ札（1移動で2人きり配達の線＝B-58ピンの形）×今日どの席も
                        #   移動禁止を出せない（1/loop×3枚がmmの再利用可能な移動札との
                        #   枯渇レースで尽きた）＝ピンの代替として第三者注入（再利用可能な
                        #   移動札）に切替える。#16実測＝ピンD1-D3発火→D4弾切れ→配達成立。
                        #   判定材料は自チーム手札残＋今日の公開配置のみ（B-35型リスク限定）。
                        _b63sk = self._b63_delivery_inject_sk(
                            view, vip, vip_c0, tgt, occ0, mm_char_now)
                        if _b63sk is not None:
                            if vip_te_death:
                                return PRIORITY["VIP注入_実証済"]
                            return (PRIORITY["VIP注入"]
                                    if _b63sk in self._sk_suspects
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
                    # ★B-76（検死3 T-A・2026-07-27）：被害者側配達への**移動対抗**＝
                    #   公開フレンド本人にmmが伏せ札×配達実証（mm移動札の公開実績）×
                    #   SK単独エリア実在の日に、フレンドへ**再利用可能移動**を重ねる。
                    #   ピン（B-72負の結果＝退避合成まで封じる凍結自傷）と違い、移動は
                    #   (i) mm札が移動なら**合成**で行き先を逸らし (ii) mm札が非移動なら
                    #   退避になり (iii) mmがSK側を動かす形なら空けた場所にSKが来るだけ＝
                    #   二正面のどの形でも自傷しない（凍結自傷回避の論証＝B-72 §3の裏面）。
                    if self._b76_friend_evade_ok(view, tgt, card, c, mm_char_now):
                        # ★B-90(c)：同型敗北の再履修が実証されている（過去の敗北ループで
                        #   このフレンドがターン終了に死んでいる）なら板の引き剥がしより上へ。
                        if self._b90_friend_death_proven(view, tgt):
                            return PRIORITY["フレンド退避_配達実証_昇格"]
                        return PRIORITY["フレンド退避_配達実証"]
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
                    # ★B-59：実証ゲート付きの供給源退避＝(c3)の確度0.7（と(c3b)の0.3）に
                    #   届かないクロマク候補でも、**公開の実証**（過去の敗北ループでその
                    #   ボード≥2 × mm能力フェイズのボード供給をその場で観測）が揃った
                    #   ボード上の argmax 候補は退避する。random_BTX#18実測（検死doc §2）＝
                    #   異世界人のクロマク周辺0.23-0.27はフラット上限で0.3ゲートに恒久に
                    #   届かず、+1/日の能力供給が8ループ素通りした（B-52/B-55と同じ
                    #   「ゲート値が対称候補のプラトーの直上」故障モード）。
                    #   ※(c2b)カルティスト剥がし_候補(72)より前に置く（両候補を兼ねる駒＝
                    #   #18の異世界人は(c2b)が先に72で拾って87に届かない）。確度0.7の
                    #   クロマク疑いはヘルパー側で除外＝(c3)の88を保つ。
                    if self._b59_source_evict_ok(view, tgt, c, dest, danger_board) \
                            and not leaves_deadly_pair:
                        return PRIORITY["供給源退避_実証"]
                    # (c2b) ★候補の引き剥がし＝実験を兼ねる：すり抜けが観測されている時、
                    #      候補（P≥0.25）を1人ずつ外す。外しても通る＝残りが候補、
                    #      通らなくなる＝そいつがカルティスト。どちらでも絞れて防御にもなる。
                    if tgt in getattr(self, "_cultist_cands", ()) and danger_board \
                            and c["area"] == danger_board and dest != danger_board \
                            and not leaves_deadly_pair:
                        # ★B-75/B-74：∩実証成員は86へ昇格（露見実験ゲート必須＝ロック済み
                        #   ループでは応手を控えて素通り観測を取る／当日冷却には席を譲る）。
                        #   ★テンションゲート（盤≥1）＝露見設計の核：盤0の素通りは
                        #   「授業料1点」（ループはまだ落ちない×観測が入る）＝応手しない。
                        #   盤1からは次の素通り＝敗着＝応手必須（s13 L2D1盤0で剥がして
                        #   軌道崩壊・s8 L2D3盤1が本来の発火日、の対を分離する）。
                        if tgt in getattr(self, "_b74_smuggle_inter", ()) \
                                and (view.get("board_anyaku") or {}).get(
                                    danger_board, 0) >= 1 \
                                and self._b75_exposure_ok(view, tgt):
                            return PRIORITY["カルティスト剥がし_実証"]
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
                    # ★B-67（目視検死T1）：供給役の復帰・搬入デフレクト＝ボード**外**の
                    #   供給役（確信0.7）にmmが伏せた札を、自移動札の合成で逸らす。
                    #   ピン（移動禁止93）が席・手札の都合で出せない日の再利用可能な代替
                    #   （btx5_seal実測＝D1-D3の連続配達で移動禁止3枚が枯れ、D4の搬入に
                    #   応手ゼロ）。strict-safe（全内容ケースで敗北ボードへ着地しない）
                    #   のみ＝当て推量では撃たない（同種2枚＝その移動1回・KB:10）。
                    if self._b67_deflect_ok(view, card, tgt, c, danger_board) \
                            and not leaves_deadly_pair:
                        return PRIORITY["供給役搬入逸らし"]
                    # ★B-77：前日の同行は不要＝解禁済み冷却役の当日移動は既存の
                    #   冷却役同行(77)が担う（実測＝L3D2に77で自然発火・移動→能力の
                    #   フェイズ順で同日に間に合う）。前日の席は投資(83)＋前日カバー
                    #   (82.5)に充てる（同行80の新設はs0実測で拒否役職の医者を運ぶ
                    #   誤発火＋前日カバーの席剥奪になった＝撤去）。
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
                    #   ★B-56（2026-07-25 ミニ検死）：ML周辺確率のε同率タイが浮動小数点
                    #   ノイズで誤った側に倒れ、B-52 が誤対象を分離した（fs5_guard s7 L4D4＝
                    #   刑事/サラリーマン 0.5/0.5 タイ）。タイ候補集合を⑤の分離対象に加える
                    #   （発火判定①〜④は argmax のまま＝発火日を増やさない。詳細は
                    #   _b56_ml_ties）。ボード供給路（下の 0.55 ゲート）は argmax のまま非接触。
                    _b56_ties = self._b56_ml_ties(view, _ml_name, _ml_p)
                    _b52 = self._b52_kill_ml_separation(o, view, _ml_name, _ml_p,
                                                        ml_ties=_b56_ties)
                    if _b52 is not None:
                        return _b52
                    # ★B-55（2026-07-25 ミニ検死 提案②）：0.55 → _B55_FEED_ML_MIN（0.5）。
                    #   経緯と実測はクラス属性のコメント参照。
                    if _ml_name and _ml_p >= self._B55_FEED_ML_MIN:
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
                    # ★B-90(a)：ML が同定できていない（B-52/B-55 のゲートに届かない）日の
                    #   位置折り。より情報のある上の分離が先に return する＝残差だけを拾う。
                    _b90 = self._b90_ml_isolate(o, view)
                    if _b90 is not None and not leaves_deadly_pair:
                        return _b90
                    # ★B-94：同じ折り方の**前日**版（明日が致死系事件日）。B-90 が
                    #   「今日が致死系事件日」でしか発火しない穴を埋める＝MLが不安を
                    #   積む前日に折る。当日(76)より下＝当日手の席は奪わない。
                    _b94 = self._b94_ml_isolate_preday(o, view)
                    if _b94 is not None and not leaves_deadly_pair:
                        return _b94
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
                # ★B-63(2) 冷却席の予約（5日級検死#8）：今日の事件の犯人候補（臨界-1以上×
                #   mm伏せ札 or 臨界到達済み）の1席は、今日まだ誰かが不安-1を出せるなら冷却に
                #   譲る（友好チャージは明日でよい・冷却は今日が期限）。例外＝TTガードと
                #   B-57薄事前仕上げ（いずれも【強制】友好禁止無視の確定防御＝S-1掃引で
                #   冷却より上と較正済みの席）。
                if tgt not in getattr(self, "_tt_guards", ()) \
                        and self._b57_tt_thin_finish(card, tgt, view) is None \
                        and self._b63_cooling_reserved(view, tgt, self._alive(view, tgt)):
                    return 8.0
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
                # ★B-86' G8：友好+ の空振り／有害を**単一チョークポイント**で一括判定する
                #   （`agents/card_effect.noop_reason`＝defense_plan の折り手生成も同じ述語）。
                #   TT ガード（上の分岐）を通り抜けた後に置く＝TT封じは絶対に潰さない。
                #   ここより下の分岐（冷却役投資81・前日冷却83・投資フォールスルー）は
                #   **拒否される相手／情報が枯れた相手**には意味がない＝先に落とす。
                if (_np := noop_reason(view, card, tgt, kind, _noop_ctx)) is not None:
                    return _np.score
                # ★危険事件ガード投資（2026-07-08）：敗北条件に直結する危険事件
                #   （蝶・邪気・流布TT等）の犯人を毎ターン冷やせる能力（学生の不安除去♡2
                #   等）の解禁は、自己ポンプ犯人（ML本人）への唯一の対抗＝カード冷却
                #   （-1/日）だけでは +2/日 に必ず負ける（btx_future実測：蝶が8/8発生）。
                #   価値は _ability_value がルール接地済み（危険事件犯人で45〜72）＝
                #   それが高い未解禁キャラへの友好+を高優先で通す。
                # ★B-77：押し切り前日の冷却エンジン投資＝明日がmate級の自傷系事件なら、
                #   友好禁止の裏の不安除去役へ♡2解禁を最優先（TT投資82/冷却役投資81より上
                #   ＝検死3実測：TT/FB投資2席が前日を素通りして当日mateへ入っていた）。
                if card in ("友好+2", "友好+1") \
                        and not getattr(self, "_cooler_invested", False):
                    _need77 = self._b77_cooler_need(view, tgt, mm_char_now)
                    if _need77 and (card == "友好+2" or _need77 == 1):
                        # 拒否役職疑いの薄い方を選ぶ（0.4未満どうしのタイ割り＝
                        # s0実測：医者=カルティストと女子学生が83で並んだ）
                        _ref77 = sum(self._belief.role_marginals().get(tgt, {})
                                     .get(r, 0.0) for r in
                                     ("カルティスト", "クロマク", "キラー",
                                      "ウィッチ"))
                        _base77 = (PRIORITY["前日冷却投資_押切"]
                                   if card == "友好+2"
                                   else PRIORITY["前日冷却投資_押切"] - 3.5)
                        return _base77 - 2.0 * _ref77
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
                # ★B-57：薄事前のTTハーツ仕上げ（最終日限定・詳細は _b57_tt_thin_finish）。
                #   高優先の投資（TT投資82/冷却役投資81）はこの上の分岐が先に取る＝
                #   ここに来るのはガード不成立時のみ。mm札カバーの友好+2減点(5.0)より
                #   前に置く（TTへの友好+は友好禁止無視＝カバーは減点理由にならない）。
                _b57 = self._b57_tt_thin_finish(card, tgt, view)
                if _b57 is not None:
                    return _b57
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
                    # ★B-66(1)：糸下の犯人候補は自傷＝5.0のままだと選択肢の乏しい席で
                    #   フィラーとして拾われ続ける（#13 L2D4実測）＝移動フィラー(1.0-3.5)未満
                    #   ではなく2.0（不安+1→犯人(1.0)との rng タイで供給加担を拾わない高さ）。
                    return 2.0 if self._b66_ito_selfharm_invest(view, tgt) else 5.0
                # ★情報収集プレイ：投資先＝実装済み能力の（価値÷残り必要ハート）が最大のキャラ。
                #   開示能力の価値はGini（誰の役職が一番不確かか）で毎ターン再計算される。
                #   上位ターゲットに集中投資（分散すると誰も閾値に届かない。友好禁止は
                #   1枚/ターンなので2本柱で押せば1本は通る）。
                # ★このループで友好禁止を当てられた相手は避ける（脚本家の1枚を空振りに）
                if tgt in self._gw_blocked:
                    return 2.0 if self._b66_ito_selfharm_invest(view, tgt) else 4.0
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
                # ★B-66(1)：因果の糸が濃厚な非最終ループでは、事件犯人候補への友好投資は
                #   次ループ開始 不安+2 の自傷（mmの不安供給への頭金）＝投資禁止側へ
                #   （#13＝毎ループ友好+→自殺犯人で臨界レースを自ら負けに固定した・検死§6）。
                #   TTガード/冷却役解禁/浄化係は上の分岐で先に return 済み＝確定防御は不変。
                #   2.0＝移動フィラー(3.5)未満・対象が1人しか居ない席では 不安+1→犯人(1.0)
                #   より上（rngタイで供給加担を拾わない）。
                if self._b66_ito_selfharm_invest(view, tgt):
                    return 2.0
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
            # ★B-99：加点値は _defense_plan_recs が種別ごとの係数を適用済み
            #   （移動禁止＝PLAN_HOT/PLAN_BONUS・他は PLAN_CLASS_COEFFS＝既定 加点なし）。
            s += self._plan_recs.get(key, 0.0)
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
            # ★B-109 論点②／B-110 論点③＝**手詰まり時の振る舞い**（手練れユーザーFB）。
            #   「やることが無いなら、確実に無意味な札より**可能性のある札**（＝移動）を置く」。
            #   **床（floor）としてのみ当てる**＝どんな実効手も押しのけない（§下の定数上限＜8.0）。
            _idle = self._b110_idle_move(o, view, _noop_ctx, danger_board)
            if _idle is not None:
                s = max(s, _idle)
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
        # ★B-100（混合AI・**既定OFF**）：点数で席を埋める**前に**「絶対防御」の制約を差し込む。
        #   B100_MIX=False（既定）ならここは属性1つの分岐だけ＝挙動 bit 不変。
        if self.B100_MIX:
            best = self._b100_force(view, options, score)
        if best is None and self._turn_plan:
            intent = self._turn_plan.pop(0)
            key = (intent["card"], intent["target"], intent.get("target_kind"))
            best = next((o for o in options
                         if (o["card"], o["target"], o.get("target_kind")) == key), None)
        if best is None:
            best = max(options, key=score)
        if self.B100_MIX:     # ★B-100：この席の手を「自チームが既に折った手」として記録
            self._b100_placed.add((best["card"], best["target"],
                                   best.get("target_kind")))
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
        # 席間協調（B-65）：このターンの移動禁止ピンを記録（退避判定の除外に使う）
        if best["card"] == "移動禁止" and best.get("target_kind") == "character":
            self._pinned_today.add(best["target"])
        # 席間協調（B-65）：幻想の板札退避を記録（後席の護衛ピン/重複退避と排他＝
        # 「退避で3人を崩した後にピンで引き抜きを止める」自作2人きりの逆順を封鎖）
        if best["card"] in _MOVE_TOGGLE and best.get("target_kind") == "board":
            _gc65 = self._alive(view, "幻想")
            if _gc65 and _gc65.get("area") == best["target"]:
                self._genso_evac_today = True
        # 席間協調：このターンの自分の移動を予定表に記録（後席の占有計算が織り込む）
        if best["card"] in _MOVE_TOGGLE and best.get("target_kind") == "character":
            mc = self._alive(view, best["target"])
            md = _move_dest(mc["area"] if mc else None, best["card"])
            if md:
                self._planned_moves[best["target"]] = md
        if B100_HOOK is not None:     # ★B-100 Phase 0：計測専用（既定 None＝不実行・戻り値不使用）
            try:
                B100_HOOK(self, view, options, best, score)
            except Exception:
                pass
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
