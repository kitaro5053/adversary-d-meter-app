"""1ループの詰み判定器（Phase 1 試作）。

ユーザー要望 2026-07-06：「そのループで脚本家が惨劇を起こせるか／主人公が生き延びるか」を
（最後の戦いは無しで）判定する。主人公は配役・ルール・犯人を全知だが、脚本家の
**伏せた行動カードの中身は見えない**（置かれた対象＝位置は見える）＝不完全情報。

判定は3値：
- "mastermind"  … 脚本家の確定勝ち（どんな主人公応手でも惨劇に至る）
- "protagonist" … 主人公の確定防衛（配置しか見えなくても、伏せ中身の全通りに耐える応手がある）
- "contested"   … どちらも確定でない＝読み合い（混合戦略の領域）／または探索の深さ上限

★健全性：中身が伏せられる分、主人公はこのモデルで弱い（実物どおり）。よって
  "mastermind"・"protagonist" の確定判定はいずれも現実で成立する（保守的＝嘘をつかない）。

不完全情報の扱い（要）：脚本家のセットは (位置σ, 中身c) に分解できる。主人公はσだけ見て応手rを
選ぶ。位置ごとに束ねて量化する：
- D(σ) = ∃r ∀c: outcome(σ,c,r)=="protagonist"   （見えた位置に堅く守れる単一応手がある）
- W(σ) = ∃c ∀r: outcome(σ,c,r)=="mastermind"     （どの応手でも通る中身がある）
- σの判定：W(σ)→脚本家/ elif D(σ)→主人公 / else 読み合い
脚本家はσを選べる：∃σ:W(σ)→脚本家 / elif ∀σ:D(σ)→主人公 / else 読み合い。

Phase 2（2026-07-06）で入れたもの：
- **状態メモ化**（_canon）：合流する盤面を再利用＝再計算を消す（小規模で brute 22s→pruned 1.1s）。
- **健全側の枝刈り**：防御/攻撃の移動（KP退避・キラー接近・カルティスト退避）・移動禁止・
  友好投資（除去/護衛/不死/TT解禁）を含める。移動は「source か dest が勝敗エリアに触れる」
  手だけ残す（無関係エリア間の移動は当ターンの解決に効かない＝落として健全）。
- **brute（枝刈り無し）基準器**：小局面で全合法手を厳密探索＝枝刈りの健全性検証に使う。
  実測：3キャラ最終日で pruned と brute が一致（両方 mastermind）＝この枝刈りは健全。

★Phase 2 で確定した本質的な制約（正直に）：
- **厳密な全列挙は中盤局面では非現実的**。basic中盤（7キャラ）はセット分岐が mm×prot＝
  10⁶〜10⁷（＝グリッド数百万〜千万）に爆発し、健全な枝刈りでも解けない。
  → **本ソルバが実用になるのは終盤/少数駒（関与3〜4体程度）まで**。それでも秒オーダー。
- ブラフ置き未モデル化（読み合いの一部を過小評価しうる）。深さ予算 max_days で射程を区切る。
- **多日にまたがる"仕込み移動"の健全性は未保証**（移動枝刈りは当ターンの勝敗エリアで判定）。
  最終日(budget=1)の判定は健全だが、複数日先読みは近似。
- 中盤〜全局面を判定したいなら、全列挙ではなく勝ち筋ごとの**レース/被覆解析**（構成的に健全）が
  本命＝今後の方向（difficulty.py の会計の発展形）。
"""

from __future__ import annotations

import copy

from engine.board import AREAS
from engine.data import unrest_threshold_of

from . import flow, legal
from .state import GameState

MASTERMIND = "mastermind"
PROTAGONIST = "protagonist"
CONTESTED = "contested"

# 脚本家が決める（勝ちに寄せる）フェーズ／主人公が決めるフェーズ
_MM_DECISIONS = frozenset({"set_card_mm", "mastermind_ability", "turn_end_ability",
                           "incident_choice", "loop_start_area", "goodwill_refuse"})


class _Frontier(Exception):
    """計画が尽きた最初の多肢決定で中断し、その決定を運ぶ。"""

    def __init__(self, actor: str, decision: str, options: list[dict]):
        self.actor, self.decision, self.options = actor, decision, options


def _plan_decider(plan: list[dict]):
    cur = [0]

    def decide(actor: str, decision: str, options: list[dict]) -> dict:
        if len(options) == 1:
            return options[0]                       # 強制手は探索ノードにしない
        if cur[0] < len(plan):
            ch = plan[cur[0]]
            cur[0] += 1
            return ch
        raise _Frontier(actor, decision, options)

    return decide


def _run_day(state: GameState, plan: list[dict]) -> GameState:
    """state のコピーで1日を plan どおり進める。未計画の多肢決定で _Frontier を送出。"""
    st = copy.deepcopy(state)
    flow.run_day(st, _plan_decider(plan))
    return st


def _canon(state: GameState) -> tuple:
    """1ループの勝敗に効く状態を漏れなく拾った正準キー（メモ化用・ハッシュ可能）。

    ★役職は脚本固定なのでキーに含めない。含めるのは可変で outcome に効くもの全部：
      各キャラの area/alive/不安/友好/暗躍/護衛/ウイルスSK化、盤面暗躍、使用済み1Lカード・
      能力、噂使用、蝶発生、ループ終了/敗北フラグ、主人公生存、日、リーダー。
    """
    chars = tuple(sorted(
        (n, c.area, c.alive, c.unrest, c.goodwill, c.anyaku, c.guard, c.virus_serial)
        for n, c in state.characters.items()))
    used = tuple((o, tuple(sorted(state.used_cards.get(o, []))))
                 for o in ("mastermind", "p1", "p2", "p3"))
    return (state.day, state.leader_idx, chars,
            tuple(sorted(state.board_anyaku.items())), used,
            tuple(sorted(state.used_goodwill)), state.rumor_used,
            state.butterfly_fired, state.loop_end_triggered, state.defeat,
            state.protagonists_alive)


class _Ctx:
    """1回の solve_loop 全体で共有するコンテキスト（メモ化＋モード）。"""

    __slots__ = ("memo", "brute", "clairvoyant", "cap_sets", "race_tail", "depth")

    def __init__(self, brute: bool = False, clairvoyant: bool = False,
                 cap_sets: int | None = None, race_tail: bool = False):
        self.memo: dict = {}
        self.brute = brute              # True＝枝刈りせず全合法手（小局面の厳密基準）
        self.clairvoyant = clairvoyant  # True＝主人公が中身も見える上界（検証用）
        # ★上位Nセットキャップ（2026-07-08・脚本チェッカー用）：各日の3枚セットの
        #   組合せを片側N通りまでに制限。両側を刈るため確定判定は「刈られた手の中に
        #   反例が無い」場合に嘘をつきうる＝結果は目安（読み合い扱いが安全側）。
        #   None＝無制限。int＝全深さ同値。tuple＝深さ別（先頭=初日、以降は末尾値を継続）
        #   ＝2日厳密の掛け算爆発を「初日広く・2日目狭く」で抑える。
        self.cap_sets = cap_sets
        # ★レース葉評価（ハイブリッド）：射程（budget）が尽きた局面を contested で
        #   打ち切る代わりに loop_race の3値で評価する。厳密な全列挙は再帰の掛け算で
        #   発散する（実測：cap=40でも3日は不可）＝初日だけ厳密な量化（σ束ね）を行い、
        #   残り日はレース/被覆の構成的判定に接続する。語彙は同一の3値。
        self.race_tail = race_tail
        self.depth = 0   # 現在の深さ（0=初日）。_solve_day_uncached が管理


# ---------------------------------------------------------------------------
# 枝刈り（勝敗に関わる手だけ残す）
# ---------------------------------------------------------------------------
def _relevant_targets(state: GameState) -> dict:
    """勝敗に絡む対象（枝刈りの土台）。★健全性のため防御/攻撃で動きうる役を広めに拾う。"""
    roles = {n: c.role for n, c in state.characters.items()}

    def all_of(role):
        return {n for n, r in roles.items() if r == role}

    goal: set[str] = set()
    ry = state.script.rule_y
    if ry == "守るべき場所":
        goal.add("学校")
    elif ry == "封印されしモノ":
        goal.add("神社")
    elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") and state.rule_y_board_x:
        goal.add(state.rule_y_board_x)
    for inc in state.script.incidents:
        if inc.name == "病院の事件":
            goal.add("病院")
    keyperson = next((n for n, r in roles.items() if r == "キーパーソン"), None)
    killer = next((n for n, r in roles.items() if r == "キラー"), None)
    culprits = {inc.culprit for inc in state.script.incidents if inc.day >= state.day}
    # ★監査追補（2026-07-07・引き継ぎ§3）：欠けていた勝敗チャネルを条件付きで拾う。
    virus_persons: set[str] = set()
    if "妄想拡大ウイルス" in state.script.rule_xs:
        # パーソンへの不安±がSK化（不安≥3）/解除（≤1）に直結
        virus_persons = {n for n, r in roles.items() if r == "パーソン"}
    lovers = all_of("ラバーズ") | all_of("メインラバーズ")
    friend = all_of("フレンド")
    remote_pending = any(i.name == "遠隔殺人" and i.day >= state.day
                         for i in state.script.incidents)
    # 不安の攻防対象（mm不安+1/不安禁止・prot不安-1）：犯人＋ウイルスパーソン＋ML
    unrest_war = culprits | virus_persons | all_of("メインラバーズ")
    # キャラ暗躍の攻防対象（mm暗躍+・prot暗躍禁止）：KP/キラー＋ML（殺害条件:暗躍≥1）
    # ＋遠隔殺人が残る場合のフレンド（暗躍≥2で殺害対象になる）
    anyaku_chars = ({keyperson, killer} | all_of("メインラバーズ")
                     | (friend if remote_pending else set())) - {None}
    # 位置で勝敗に効く役（同エリア要求＝移動/移動禁止が効く）。妄想拡大ウイルスのSK化も含める。
    sk = all_of("シリアルキラー") | {
        n for n, c in state.characters.items() if c.virus_serial}
    movers = (all_of("キラー") | all_of("クロマク") | all_of("カルティスト")
              | all_of("ミスリーダー") | sk
              | lovers | friend | virus_persons)   # 死が敗北に繋がる者＋SK化候補
    if keyperson:
        movers.add(keyperson)
    movers |= culprits
    # 友好投資で勝敗を動かす役（暗躍/不安除去・護衛・不死・友好で止めるTT）。
    from engine.data import goodwill_abilities_of
    protectors: set[str] = set(all_of("タイムトラベラー"))
    for n in state.characters:
        for ab in goodwill_abilities_of(n) or []:
            nm = ab["name"]
            if ("暗躍除去" in nm or ("不安" in nm and "除去" in nm)
                    or "護衛" in nm or "不死" in nm or "蘇生" in nm):
                protectors.add(n)
    # ★勝敗に効く「エリア」（同エリア判定＝キラー殺害/SK/クロマク能力/ゴールボード）。
    #   移動は source か dest がこの集合に触れる時だけ当ターンの結果を変えうる＝それ以外は落とす。
    relevant_areas: set[str] = set(goal)
    for n in ({keyperson, killer} | sk | friend | lovers):
        if n and state.characters[n].alive and state.characters[n].area:
            relevant_areas.add(state.characters[n].area)
    # SKの現在地（SKは「2人きり」でだけ殺す＝第三者の送り込み/引き抜きが手になる）
    sk_areas = {state.characters[n].area for n in sk
                if state.characters[n].alive and state.characters[n].area}
    return {"goal": goal, "keyperson": keyperson, "killer": killer,
            "culprits": culprits, "movers": movers, "protectors": protectors,
            "sk": sk, "relevant_areas": relevant_areas,
            "unrest_war": unrest_war, "anyaku_chars": anyaku_chars,
            "sk_areas": sk_areas}


def _key_chars(rel: dict) -> set:
    return ({rel["keyperson"], rel["killer"]} | set(rel["culprits"])
            | rel["movers"] | rel["protectors"]) - {None}


_TOGGLE = {"移動↑↓": (0, 1), "移動←→": (1, 0), "移動斜め": (1, 1)}


def _move_touches_relevant(state: GameState, tgt: str, card: str, rel: dict) -> bool:
    """キャラ tgt を card で動かすと、source か dest が勝敗エリアに触れるか（当ターン有効か）。"""
    from engine.board import destination
    c = state.characters.get(tgt)
    if not c or not c.area:
        return False
    dest = destination(c.area, _TOGGLE[card])
    return c.area in rel["relevant_areas"] or dest in rel["relevant_areas"]


def _filler(options: list[dict], rel: dict, keych: set) -> dict | None:
    """勝敗に絡まない「1手潰し」（3枚セット強制を満たす無害手）。
    非関連キャラへの移動禁止など効果の薄いものを1つ選ぶ（無ければ末尾）。"""
    for o in options:  # 非関連キャラへの移動禁止＝ほぼ無効（動かないカードを止める）
        if o["card"] == "移動禁止" and o["target_kind"] == "character" \
                and o["target"] not in keych:
            return o
    for o in options:
        if o["target_kind"] == "character" and o["target"] not in keych \
                and o["card"] in ("移動↑↓", "移動←→", "移動斜め", "友好+1"):
            return o
    return options[-1] if options else None


_MOVE_CARDS = ("移動↑↓", "移動←→", "移動斜め")


def _prune_mm_set(state: GameState, options: list[dict], rel: dict,
                  brute: bool = False) -> list[dict]:
    """脚本家のセット候補を勝ち筋に関わる手＋フィラー1手へ絞る（brute＝全合法手）。"""
    if brute:
        return options
    keych = _key_chars(rel)
    keep = []
    for o in options:
        card, tgt, kind = o["card"], o["target"], o["target_kind"]
        if card.startswith("暗躍"):
            if (kind == "board" and tgt in rel["goal"]) or \
               (kind == "character" and tgt in rel["anyaku_chars"]):
                keep.append(o)
        elif card == "不安+1" and kind == "character" and tgt in rel["unrest_war"]:
            keep.append(o)   # 犯人＋ウイルスパーソン（SK化）＋ML（監査追補）
        elif card == "不安禁止" and kind == "character" and tgt in rel["unrest_war"]:
            keep.append(o)   # ★主人公の冷却（不安-1）を打ち消す攻め手（監査追補）
        elif (card in _MOVE_CARDS and kind == "character"
              and (tgt in rel["movers"] or rel["sk_areas"])
              and _move_touches_relevant(state, tgt, card, rel)):
            keep.append(o)   # 勝敗エリアへ/から寄せる。SKが居る時は第三者の引き抜きも攻め手
        elif card == "友好禁止" and kind == "character" and tgt in rel["protectors"]:
            keep.append(o)   # 除去/護衛/不死/TT等の解禁を遅らせる
    fill = _filler(options, rel, keych)
    if fill is not None and fill not in keep:
        keep.append(fill)
    return keep


def _prune_prot_set(state: GameState, options: list[dict], rel: dict,
                    brute: bool = False) -> list[dict]:
    """主人公のセット候補（防御に関わる手＋フィラー1手）へ絞る（brute＝全合法手）。"""
    if brute:
        return options
    keych = _key_chars(rel)
    keep = []
    for o in options:
        card, tgt, kind = o["card"], o["target"], o["target_kind"]
        if card == "暗躍禁止":
            if (kind == "board" and tgt in rel["goal"]) or \
               (kind == "character" and tgt in rel["anyaku_chars"]):
                keep.append(o)
        elif card == "不安-1" and kind == "character" and tgt in rel["unrest_war"]:
            keep.append(o)   # 犯人＋ウイルスパーソン（SK化解除側）＋ML（監査追補）
        elif (card in _MOVE_CARDS and kind == "character"
              and (tgt in keych or rel["sk_areas"])
              and _move_touches_relevant(state, tgt, card, rel)):
            keep.append(o)   # KP退避/キラー引き離し。SKが居る時は第三者の送り込み＝ブロックも防御
        elif card == "移動禁止" and kind == "character" and tgt in rel["movers"]:
            keep.append(o)   # キラー/SK/クロマク/カルティストの接近・連れ戻しを止める
        elif card in ("友好+1", "友好+2") and kind == "character" and tgt in rel["protectors"]:
            keep.append(o)   # 除去/護衛/不死/TT等を解禁して勝ち筋を潰す
    fill = _filler(options, rel, keych)
    if fill is not None and fill not in keep:
        keep.append(fill)
    return keep


def _prune_open(decision: str, options: list[dict]) -> list[dict]:
    """開フェーズ（脚本家能力・事件・ターン終了・友好）の候補を絞る（passは常に残す）。"""
    passes = [o for o in options if o.get("action") == "pass"]
    rest = [o for o in options if o.get("action") != "pass"]
    return rest + passes if rest else passes or options


# ---------------------------------------------------------------------------
# 葉・日進行
# ---------------------------------------------------------------------------
def _leaf(state: GameState) -> str:
    """このループ内で惨劇（defeat）が起きたか。起きた→脚本家、なし→主人公。"""
    return MASTERMIND if state.defeat else PROTAGONIST


def _after_day(state: GameState, budget: int, ctx: "_Ctx") -> str:
    """1日終了後：ループ終了なら葉、続くなら次日へ再帰。
    予算切れは race_tail ならレース3値で評価、さもなくば contested。"""
    if state.loop_end_triggered or state.day >= state.script.days_per_loop:
        flow.evaluate_loop_end(state)
        return _leaf(state)
    if budget <= 1:
        if ctx.race_tail:
            from .loop_race import analyze_loop
            nxt = copy.deepcopy(state)
            nxt.day += 1
            return analyze_loop(nxt).verdict           # 同一3値（近似・健全側志向）
        return CONTESTED                               # 射程外＝確定できない
    nxt = copy.deepcopy(state)
    nxt.day += 1
    ctx.depth += 1
    try:
        return _solve_day(nxt, budget - 1, ctx)
    finally:
        ctx.depth -= 1


# ---------------------------------------------------------------------------
# 3値ミニマックスの結合
# ---------------------------------------------------------------------------
def _combine(controller: str, results: list[str]) -> str:
    """controller が MASTERMIND なら惨劇を、PROTAGONIST なら防衛を選べる。"""
    if controller == MASTERMIND:
        if MASTERMIND in results:
            return MASTERMIND
        return CONTESTED if CONTESTED in results else PROTAGONIST
    if PROTAGONIST in results:
        return PROTAGONIST
    return CONTESTED if CONTESTED in results else MASTERMIND


# ---------------------------------------------------------------------------
# 開フェーズ（セット後）＝完全情報の逐次ミニマックス
# ---------------------------------------------------------------------------
def _solve_open(state: GameState, plan: list[dict], budget: int, ctx: "_Ctx") -> str:
    try:
        done = _run_day(state, plan)
    except _Frontier as f:
        controller = MASTERMIND if f.actor == "mastermind" else PROTAGONIST
        opts = f.options if ctx.brute else _prune_open(f.decision, f.options)
        results = []
        for o in opts:
            r = _solve_open(state, plan + [o], budget, ctx)
            # ★短絡：controllerが望む結果が出たら即確定（他の枝は見ない）。
            if (controller == MASTERMIND and r == MASTERMIND) or \
               (controller == PROTAGONIST and r == PROTAGONIST):
                return r
            results.append(r)
        return _combine(controller, results)
    return _after_day(done, budget, ctx)


# ---------------------------------------------------------------------------
# セットフェーズ（不完全情報：位置は見え・中身は伏せ）
# ---------------------------------------------------------------------------
def _enum_set(state: GameState, actor_is_mm: bool, count: int, rel: dict,
              prefix: list[dict], ctx: "_Ctx") -> list[list[dict]]:
    """actor のセット count 枚の合法な組合せを、枝刈りしつつ列挙（prefix＝手前の計画）。

    ★キャップは列挙**中**に適用する（2026-07-07修繕）：旧実装は全組合せを列挙して
    から先頭Nを切っていた＝枝刈り監査でkeepを広げた後、SKを含むサンプル脚本で
    C(60,3)×再帰の列挙自体が爆発し、pytestが21CPU時間・8.5GB走る事故になった。
    ユニークな多重集合がキャップに達したら再帰を打ち切る。保持集合は旧実装と同一
    （順序重複排除後の列挙順＝keep順の先頭N）＝判定の意味論は不変。
    重複順列の走査は打ち切りまでに最大6倍（3枚の順列数）で有界。
    """
    cap = ctx.cap_sets
    if isinstance(cap, (tuple, list)):
        cap = cap[min(ctx.depth, len(cap) - 1)]
    seen: set = set()
    results: list[list[dict]] = []

    def add(chosen: list[dict]) -> None:
        # ★順序重複排除：3枚のセットは置く順序に意味が無い（解決は全公開後）＝
        #   同じ配置多重集合の順列を1つに束ねる（C(n,3)へ縮約＝約6倍の削減）。
        key = tuple(sorted(tuple(sorted(o.items())) for o in chosen))
        if key not in seen:
            seen.add(key)
            results.append(chosen)

    def rec(chosen: list[dict]) -> bool:
        """False＝キャップ到達（全体の打ち切り）。"""
        if cap is not None and len(results) >= cap:
            return False
        if len(chosen) == count:
            add(chosen)
            return True
        try:
            _run_day(state, prefix + chosen)
        except _Frontier as f:
            want_mm = (f.actor == "mastermind")
            if f.decision != "set_card" or want_mm != actor_is_mm:
                # 目的の actor のセット決定に達していない（想定外）＝この枝は捨てる
                return True
            opts = (_prune_mm_set(state, f.options, rel, ctx.brute) if actor_is_mm
                    else _prune_prot_set(state, f.options, rel, ctx.brute))
            for o in opts:
                if not rec(chosen + [o]):
                    return False
            return True
        # _Frontier が出ない＝1日完走（count 枚に満たないのに決定が尽きた）＝ここまでで確定
        add(chosen)
        return True

    rec([])
    return results


def _sigma(placements: list[dict]) -> tuple:
    """配置の「位置シグネチャ」＝対象の多重集合（中身は伏せるので含めない）。"""
    return tuple(sorted((p["target"], p["target_kind"]) for p in placements))


def _solve_day(state: GameState, budget: int, ctx: "_Ctx") -> str:
    _cap = ctx.cap_sets if not isinstance(ctx.cap_sets, list) else tuple(ctx.cap_sets)
    key = (_canon(state), budget, ctx.brute, ctx.clairvoyant,
           _cap, ctx.race_tail, ctx.depth)  # ★状態メモ化（合流盤面を再利用）
    cached = ctx.memo.get(key)
    if cached is not None:
        return cached
    result = _solve_day_uncached(state, budget, ctx)
    ctx.memo[key] = result
    return result


def _solve_day_uncached(state: GameState, budget: int, ctx: "_Ctx") -> str:
    rel = _relevant_targets(state)
    # 脚本家セットの全通り（中身つき）＝M、主人公応手の全通り＝R（Mの中身に依存しない）
    mm_sets = _enum_set(state, True, 3, rel, [], ctx)
    if not mm_sets:
        mm_sets = [[]]  # 関連手なし＝空セット扱い（全て強制/無関係）
    # R は Mの中身に依らないので、代表 M を1つ使って列挙
    prot_sets = _enum_set(state, False, 3, rel, mm_sets[0], ctx)
    if not prot_sets:
        prot_sets = [[]]

    ocache: dict = {}

    def outcome(M: list[dict], R: list[dict]) -> str:
        okey = (tuple(map(_od, M)), tuple(map(_od, R)))
        if okey not in ocache:
            ocache[okey] = _solve_open(state, M + R, budget, ctx)
        return ocache[okey]

    if ctx.clairvoyant:
        # 透視モデル（上界）：主人公は中身を見て応手＝逐次完全情報ミニマックス。
        mm_results = []
        for M in mm_sets:
            prot_results = [outcome(M, R) for R in prot_sets]
            mm_results.append(_combine(PROTAGONIST, prot_results))
        return _combine(MASTERMIND, mm_results)

    # 不完全情報モデル：位置σで束ねて D(σ)/W(σ)。★短絡：W(σ)が1つ出たら即・脚本家勝ち。
    groups: dict = {}
    for M in mm_sets:
        groups.setdefault(_sigma(M), []).append(M)

    all_D = True
    for sig, Ms in groups.items():
        # W(σ)=∃M ∀R: outcome=="mastermind"（どの応手でも通る中身がある）＝即・脚本家確定
        if any(all(outcome(M, R) == MASTERMIND for R in prot_sets) for M in Ms):
            return MASTERMIND
        # D(σ)=∃R ∀M: outcome=="protagonist"（位置しか見えなくても堅く守れる応手がある）
        D = any(all(outcome(M, R) == PROTAGONIST for M in Ms) for R in prot_sets)
        if not D:
            all_D = False
    return PROTAGONIST if all_D else CONTESTED


def _od(o: dict) -> tuple:
    return tuple(sorted(o.items()))


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------
def solve_loop(state: GameState, max_days: int = 3, clairvoyant: bool = False,
               brute: bool = False, cap_sets: int | None = None,
               race_tail: bool = False) -> str:
    """現在の局面（day開始時）から、このループの詰みを3値判定する。

    state は run_day の直前（day開始）を想定。max_days＝先読みする日数（射程）。
    clairvoyant=True は主人公が中身も見える上界モデル（検証・比較用）。
    brute=True は枝刈りせず全合法手を探索＝小局面の厳密基準（健全な枝刈りの検証用・重い）。
    cap_sets=N は各日のセット組合せを片側N通りに制限（脚本チェッカー用の実用モード。
    両側を刈るため確定判定は目安＝§solve_script のドキュメント参照）。
    """
    st = copy.deepcopy(state)
    return _solve_day(st, max_days,
                      _Ctx(brute=brute, clairvoyant=clairvoyant, cap_sets=cap_sets,
                           race_tail=race_tail))


# ---------------------------------------------------------------------------
# ディスクキャッシュ（2026-07-08）：solve_script は1〜5秒/本＝generator検証や
# テスト群で数十回呼ばれると分オーダーに積み上がる（実測：pytest 65秒→28分）。
# 判定は決定的なので、脚本の内容フィンガープリント＋関連コードのハッシュを鍵に
# .cache/solver_verdicts.json へ永続化する（コード変更で自動無効化・git管理外）。
# ---------------------------------------------------------------------------
_CACHE_FILE = None
_CACHE_DATA: dict | None = None
_CODE_HASH: str | None = None


def _solver_code_hash() -> str:
    global _CODE_HASH
    if _CODE_HASH is None:
        import hashlib
        import os
        h = hashlib.sha1()
        base = os.path.dirname(__file__)
        for fn in ("loop_solver.py", "loop_race.py", "script_quality.py",
                   "legal.py", "effects.py", "state.py"):
            with open(os.path.join(base, fn), "rb") as f:
                h.update(f.read())
        _CODE_HASH = h.hexdigest()[:12]
    return _CODE_HASH


def _script_fingerprint(script, cap_sets, exact_days) -> str:
    parts = (script.set_name, script.rule_y, script.rule_x, script.rule_x2,
             script.loops, script.days_per_loop, tuple(script.cast),
             tuple(sorted(script.roles.items())),
             tuple((i.day, i.name, i.culprit) for i in script.incidents),
             tuple(sorted(script.entry_days.items())),
             getattr(script, "oomono_territory", None),
             str(cap_sets), exact_days, _solver_code_hash())
    import hashlib
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()


def _cache_load() -> dict:
    global _CACHE_DATA, _CACHE_FILE
    if _CACHE_DATA is None:
        import json
        import os
        _CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   ".cache", "solver_verdicts.json")
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                _CACHE_DATA = json.load(f)
        except (OSError, ValueError):
            _CACHE_DATA = {}
    return _CACHE_DATA


def _cache_save() -> None:
    import json
    import os
    if _CACHE_FILE is None or _CACHE_DATA is None:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE_DATA, f)
    except OSError:
        pass   # キャッシュ書き込み失敗は無害（次回また計算するだけ）


def solve_script(script, cap_sets=None, clairvoyant: bool = False,
                 exact_days: int = 1, use_cache: bool = True) -> str:
    """脚本の初期局面（ループ開始）から1ループの詰みを判定する（脚本チェッカー）。

    ★ユーザー方針（2026-07-08）：情報オープン（配役・ルール全知）の主人公が
    そのループで勝てないのは脚本の作りとしておかしい＝mastermind判定はNG脚本。
    脚本家側は「意味のある手」（勝ち筋に関わる手）に限られる前提で全探索する。

    判定の意味（両側カテゴリ枝刈り＋上位Nキャップの下で）：
    - "mastermind"  … 刈った範囲内で脚本家の確定勝ち＝NG脚本の強い証拠
      （脚本家の手は勝ち筋関連に絞っても勝てている）。
    - "protagonist" … 刈った範囲内で主人公の確定防衛。
    - "contested"   … 読み合い（2択を連続で通される類は許容＝ユーザー方針）
      または探索範囲の限界。

    実装＝ハイブリッド：初日（exact_days日）はセットの量化（σ束ね）を厳密に行い、
    以降はレース/被覆解析（loop_race）で評価する。厳密全列挙は再帰の掛け算で
    発散する（実測：cap=40でも3日再帰は不可）ため、この構成が実用点。
    """
    from engine.data import initial_area_of
    if cap_sets is None:
        # 既定：2日厳密は初日24・2日目以降10（深さ別＝掛け算爆発の抑制。
        #   protagonist確定はフルスキャンが要るため2日厳密は分オーダー＝深掘り用途）
        cap_sets = (24, 10) if exact_days >= 2 else 60
    fp = None
    if use_cache and not clairvoyant:
        fp = _script_fingerprint(script, cap_sets, exact_days)
        hit = _cache_load().get(fp)
        if hit is not None:
            return hit
    st = GameState(script=script)
    dyn = {n: "都市" for n in script.cast if initial_area_of(n) is None}
    st.prepare_loop(dyn)
    v = solve_loop(st, max_days=exact_days,
                   clairvoyant=clairvoyant, cap_sets=cap_sets, race_tail=True)
    if fp is not None:
        _cache_load()[fp] = v
        _cache_save()
    return v
