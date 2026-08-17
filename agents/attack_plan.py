# -*- coding: utf-8 -*-
"""belief駆動の攻撃プランナー（脚本家側・2026-07-12・発案=ユーザー）。

防御プランナー `agents/defense_plan.py` の**双対**。防御が「真の脅威を最安の折り手で
覆う hitting set」なら、攻撃は「**主人公の被覆枠を超える"疑わしい候補集合"を、真相を
割らずに作る**」＝カバーストーリー(CS)による二者/三者択一の強制。

考え方（脚本家側の詰将棋）:
  1. 主人公の公開履歴だけから belief（役職/ルールの推定）を再構成する
     ＝**脚本家は"主人公が何を疑っているか"をモデル化する**（神視点の真相は別に持つ）。
  2. その belief に対して防御プランナーを回すと、`plan.uncovered`＝
     **主人公が今ターン覆え切れない致命脅威**が出る（暗躍禁止1枚/ターン＝二正面で溢れる）。
     これが攻撃の**目的関数**＝この数を増やす配置が「二者択一を迫る」手。
  3. 脚本家の配置候補（どのボード/キャラに暗躍を積むか）を、この uncovered を最大化する
     方向に選ぶ。真相ボードだけを積むと belief が真相へ収束して decoy が無駄になるので、
     **主人公が belief 上で消せない候補**（rule_marginals が生かしているボード）を優先して arm する。

★オラクルの妙：防御プランナーの `_defeat_board_probs` は belief の rule_marginals で
  各ボードの脅威度を重み付けする＝**主人公がルールを見切ったボードは脅威度0**になる。
  よって「belief が既に収束した decoy を arm しても uncovered は増えない」ことが数値で出る
  ＝CSが生きているか死んでいるかがオラクル1回で分かる。CSが死んでいれば真相の効率
  （早撃ち）や真相二正面へ切り替える判断材料になる。

現段階＝**助言専用(advisory)**。決定ロジックには未配線（defense_plan と同じ measure-
before-rewrite の作法）。まず本モジュールで「今の脚本家がどれだけ uncovered を作れて
いるか」を実測し、段階的に heuristic.py へ配線して勾配(p混合)で測る。

Streamlit非依存・engine非依存（view dict と belief だけで動く純関数群。defense_plan を再利用）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from agents import defense_plan as dp

_AREAS = ("病院", "神社", "都市", "学校")
# 主人公が1ターンに置ける被覆資源のモデル（暗躍禁止=実効1枚/ターン＝二正面の律速）。
_DEFENDER_SEATS = 3
_DEFENDER_CARD_CAPS = dp.ANRYAKU_KINSHI_TURN_CAP   # {"暗躍禁止": 1}


# ---------------------------------------------------------------------------
# 主人公 belief の再構成（脚本家は"相手が何を疑っているか"を公開情報だけからモデル化）
# ---------------------------------------------------------------------------
def protagonist_belief(view: dict):
    """脚本家view（公開履歴を含む）から、主人公の belief を再構成して返す。

    真相（roles/rule_y）は使わない＝主人公が持つのと同じ公開情報だけから作る。
    belief不能（拡張キャスト等でモデル外）なら None（呼び側は圧力0扱いで退化）。
    """
    try:
        from agents.belief import Belief
    except Exception:
        return None
    try:
        cast = [c["name"] for c in view["characters"]]
        inc = [{"day": i["day"], "name": i["name"]} for i in view.get("incidents", [])]
        set_name = "BTX" if view.get("rule_x2") else "FS"
        b = Belief(cast, inc, set_name)
        b.observe(list(view.get("history", [])))
        return b
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 防御射影した曖昧さ（＝価値のある世界線だけを bit で測る）
#
# ユーザー知見 2026-07-12：世界線の"数"には価値の差がある。KP が女学生か男子学生か
# 分からない曖昧さは高価値（暗躍禁止/移動で"その駒"を狙う要がある）だが、初期位置が同じ
# ミスリーダーの正体が分からない曖昧さは低価値（可視の不安pumpに反応すれば足りる＝正体を
# 知らずとも必要防御が同じ）。よって"生の world-count"でなく、**「正体を知らないと守れない
# 潜在変数」だけ**をエントロピー(bit)で数える＝防御に効く曖昧さだけを勘定する。
# ---------------------------------------------------------------------------

# 正体を知る必要がある＝暗躍禁止/移動で"その駒"を狙う防御標的ロール（＝価値のある曖昧さ）。
# ミスリーダー/カルティスト/クロマク等は「可視の効果に反応」できる or 位置で対処＝低価値
# なので含めない（正体が割れても必要防御は概ね変わらない）。
_DEFENSE_ROLES = ("キーパーソン", "キラー", "シリアルキラー",
                  "メインラバーズ", "タイムトラベラー", "フレンド")


def _entropy_bits(probs) -> float:
    s = sum(p for p in probs if p > 0)
    if s <= 0:
        return 0.0
    return -sum((p / s) * math.log2(p / s) for p in probs if p > 0)


def defense_ambiguity(belief, view: dict | None = None) -> dict:
    """主人公の"必要防御"を変える曖昧さだけを bit で測る（無価値な曖昧さは除外）。

    components:
      board   … どの板を守るか（ゴール板ルールの分布のエントロピー）
      <role>  … その防御標的ロールが誰か（KP/キラー/SK/MainLovers/TT/フレンド）
      culprit … 各事件の犯人候補数の log2 合計（どこで事件を止めるか）
    total = これらの和（bit）。大きいほど主人公は"守る先"を絞れていない＝CSが生きている。
    """
    if belief is None:
        return {"total": 0.0}
    view = view or {"characters": [], "rule_y_board_x": None}
    comp: dict = {}
    # ゴール板（どの板に暗躍禁止を置くか）＝board_x隠れを反映（穴②）
    try:
        bp = _belief_defeat_board_probs(belief, view)
        mass = sum(bp.values())
        comp["board"] = _entropy_bits(list(bp.values()) + [max(0.0, 1.0 - mass)])
    except Exception:
        comp["board"] = 0.0
    # 防御標的ロールの正体（誰を暗躍禁止/移動で守るか）
    try:
        rm = belief.role_marginals()
        for role in _DEFENSE_ROLES:
            h = _entropy_bits([d.get(role, 0.0) for d in rm.values()])
            if h > 1e-9:
                comp[role] = h
    except Exception:
        pass
    # 犯人（どこで事件を止めるか）
    try:
        cc = belief.culprit_candidates()
        cul = sum(math.log2(len(s)) for s in cc.values() if len(s) > 1)
        if cul > 1e-9:
            comp["culprit"] = cul
    except Exception:
        pass
    comp["total"] = round(sum(v for k, v in comp.items() if k != "total"), 4)
    return comp


def ambiguity_cost(base_belief, view: dict, hypo_events: list[dict], *,
                   cache: dict | None = None) -> float:
    """base_belief に hypo_events（その手が卓上に発行する公開イベント列）を仮想追加した
    ときの **defense_ambiguity の減少量(bit・≥0)** ＝ generalized reveal_cost の価値重み版。

    既存 belief.reveal_cost は生の世界線崩壊(collapse_frac)を返すので、無価値な曖昧さ
    （ミスリーダー正体など）の崩壊まで等価にコスト化する。本関数は「必要防御を変える
    曖昧さ」の崩壊だけを測る＝脚本家が"守るべき秘密"だけを守れる。

    cache＝1decide内で使い回す dict（同一 present/board の after を再利用）。base の
    defense_ambiguity は "__before__" キーで1回だけ計算する。belief不能時は 0.0 に退化。
    """
    if base_belief is None or not hypo_events:
        return 0.0
    try:
        if cache is not None and "__before__" in cache:
            before = cache["__before__"]
        else:
            before = defense_ambiguity(base_belief, view)["total"]
            if cache is not None:
                cache["__before__"] = before
        from engine.board import AREAS
        e0 = hypo_events[0]
        key = (e0.get("target") in AREAS,
               frozenset(tuple(sorted(e.get("present") or ())) for e in hypo_events))
        if cache is not None and key in cache:
            return cache[key]
        from agents.belief import Belief
        after = Belief(base_belief.cast, base_belief.incidents, base_belief.set_name)
        after.observe(base_belief.history() + list(hypo_events))
        val = max(0.0, round(before - defense_ambiguity(after, view)["total"], 4))
    except Exception:
        val = 0.0
    if cache is not None:
        cache[key] = val
    return val


# ---------------------------------------------------------------------------
# 防御側ツールキット（主人公の手札は脚本家には見えない＝標準の被覆能力を仮定する）
# ---------------------------------------------------------------------------
def defender_toolkit(view: dict) -> list[dict]:
    """主人公が持ちうる被覆手を網羅的に合成する（＝個々の脅威は"折れる"状態にして、
    uncovered が出るのは席3枚/暗躍禁止1枚の**枚数制約**（二正面）だけに由来させる）。

    脚本家は主人公の手札を知らないので、標準の道具（各ボードに暗躍禁止／各生存キャラに
    移動・移動禁止・不安-1・友好+）を仮定する＝**保守的**（主人公は少なくともこれを
    持ちうる）。実手札より豊富なら圧力は過小評価＝安全側（脚本家が過信しない）。
    """
    opts: list[dict] = []
    for a in _AREAS:
        opts.append({"card": "暗躍禁止", "target": a, "target_kind": "board"})
        opts.append({"card": "移動", "target": a, "target_kind": "board"})  # 幻想用ボード移動
    for c in view.get("characters", []):
        if not c.get("alive", True) or c.get("area") is None:
            continue
        n = c["name"]
        for card in ("移動縦", "移動横", "移動禁止", "不安-1", "友好+2", "友好+1"):
            opts.append({"card": card, "target": n, "target_kind": "character"})
    return opts


# ---------------------------------------------------------------------------
# 仮配置の注入（「このボード/キャラを暗躍+dだけ育てたら主人公はどう困るか」の評価用）
# ---------------------------------------------------------------------------
def _inject_arming(view: dict, arming: dict | None) -> dict:
    """view を浅くコピーし、arming={(target,kind): delta暗躍} を board_anyaku/キャラ暗躍へ加える。
    返り値は defense_plan が読む範囲（board_anyaku・characters）だけ差し替えた新dict。"""
    if not arming:
        return view
    ba = dict(view.get("board_anyaku", {}) or {})
    chars = [dict(c) for c in view.get("characters", [])]
    by_name = {c.get("name"): c for c in chars}
    for (tgt, kind), delta in arming.items():
        if kind == "board":
            ba[tgt] = ba.get(tgt, 0) + delta
        else:
            c = by_name.get(tgt)
            if c is not None:
                c["anyaku"] = c.get("anyaku", 0) + delta
    nv = dict(view)
    nv["board_anyaku"] = ba
    nv["characters"] = chars
    return nv


# ---------------------------------------------------------------------------
# ★ループ終了時「評価敗北ファミリー」の統一モデル（ユーザー知見 2026-07-12・現物 effects.py:542）
#   同型＝ループ終了時に暗躍≥2で敗北・**原因(どのルール)は非公開**・全て暗躍禁止でしか
#   止まらない（KP暗躍は位置無関係＝移動で逃げられない）：
#     守るべき場所(学校) / 封印(神社) / 爆弾X・復讐者(board_x=隠れ) / 僕と契約(KPキャラ)
#   → 主人公は"どれが本命か分からず"、暗躍禁止1枚/ターンでは複数を止められない＝多front CS。
#   defense_plan 本体は変えず（＝主人公AIの挙動＝ベンチ不変）、攻撃プランナー側で組み立てる。
# ---------------------------------------------------------------------------
def _belief_defeat_board_probs(belief, view: dict) -> dict:
    """主人公視点の {板: P(その板が敗北ボード)}。board_x系(爆弾X/復讐者)は "どの板がXか
    主人公は知らない" を反映し、belief の ウィッチ/クロマク 居場所分布で全板へ散らす（穴②）。
    ★共通化（AIB・task_11a54cc5）：本ロジックは defense_plan._defeat_board_probs に集約した
    （主人公の防御プランナーも同じ belief-aware 板脅威を見る）ので、ここは薄い委譲。"""
    return dp._defeat_board_probs(belief, view)


def _contract_threats(view, belief, roles, opts, supply_rumor) -> list:
    """僕と契約しようよ！(KP暗躍≥2単独勝ち)＝ボード敗北と同格のループ終了時評価敗北（穴①）。
    キラー不要・位置無関係＝暗躍禁止/クロマク剥がし/供給停止でしか止まらない。"""
    try:
        rm = belief.rule_marginals()
    except Exception:
        return []
    contract_p = sum(p for (ry, _r), p in rm.items() if ry == "僕と契約しようよ！")
    if contract_p < 0.05:
        return []
    out = []
    for kp, pkp in dp._suspects(roles, "キーパーソン").items():
        # ★B-187：契約のキーパーソンは必ず少女（rules/50:42）＝非少女は候補にしない。
        #   切替口は dp 側の単一ソース（dp.B187_CONTRACT_SHOUJO_ONLY）を共有＝
        #   片方だけ ON になる事故を構造で消す。既定 OFF＝従来どおり素通し。
        if dp.B187_CONTRACT_SHOUJO_ONLY and kp not in dp.SHOUJO:
            continue
        kc = dp._char(view, kp)
        if not kc or not kc.get("alive", True) or kc.get("area") is None:
            continue
        cur = kc.get("anyaku", 0)
        if cur >= 2:
            continue   # 既に敗北域
        c1 = dp.Condition("KPの暗躍が2に届く（僕と契約）")
        dp._add_anyaku_supply_breaks(c1, view, roles, opts, kp, kc["area"], supply_rumor)
        progress = 0.9 if cur >= 1 else 0.35
        out.append(dp.Threat("kp_anyaku", f"僕と契約＝{kp}の暗躍2でループ敗北",
                             contract_p * pkp * progress, True,
                             "ループ終了フェイズ", [c1]))
    return out


def _family_plan(view: dict, belief, options, *, seats, card_turn_caps, min_prob):
    """defense_plan の enumerate_threats/plan_defenses を使いつつ、評価敗北ファミリーの
    穴①(僕と契約)②(board_x散らし)を補って脅威列挙＋被覆判定する（defense_plan は不変）。"""
    roles = belief.role_marginals()
    try:
        culprits = belief.culprit_candidates()
    except Exception:
        culprits = {}
    day, dpl = view.get("day"), view.get("days_per_loop")
    final_day = (day is not None and dpl is not None and day >= dpl)
    supply_rumor = dp._rumor_active(belief)
    threats = list(dp.enumerate_threats(
        view, roles, supply_rumor=supply_rumor, options=options, culprits=culprits,
        criticals=dp._criticals_for(view), final_day=final_day,
        defeat_board_probs=_belief_defeat_board_probs(belief, view),
        virus_p=dp._rulex_prob(belief, "妄想拡大ウイルス"), min_prob=min_prob))
    threats += _contract_threats(view, belief, roles, dp._Opts(options or []), supply_rumor)
    threats.sort(key=lambda t: t.severity, reverse=True)
    return threats, dp.plan_defenses(threats, seats=seats, card_turn_caps=card_turn_caps)


# ---------------------------------------------------------------------------
# 圧力＝主人公の uncovered 致命脅威の数（防御プランナーをオラクルに使う）
# ---------------------------------------------------------------------------
@dataclass
class Pressure:
    n_uncovered: int                 # 覆え切れない致命脅威の総数（＝二者択一の強さ）
    n_shortfall: int                 # うち「折り手はあるが枚数不足」＝CS二正面の本体
    n_race: int                      # うち「暗躍禁止で止まらない供給」＝レース
    uncovered_labels: list           # 覆え切れなかった脅威のラベル
    n_threats: int                   # belief 上で火が点きうる致命脅威の総数
    severity: float = 0.0            # 覆え切れない脅威の重症度合計（prob×致死＝連続の圧力）


def defender_pressure(view: dict, belief, *, arming: dict | None = None,
                      options: list[dict] | None = None) -> Pressure:
    """主人公 belief に対し（arming を仮に積んだ局面で）防御プランナーを回し、
    覆え切れない致命脅威を数える。belief=None は圧力0（推定不能）で退化。

    ★severity＝uncovered 脅威の severity 合計。数(count)は空ボードも1と数えて arming の
    増分が飽和するので、arm で進行度が上がる＝重症度が上がる連続量を主目的関数にする。"""
    if belief is None:
        return Pressure(0, 0, 0, [], 0, 0.0)
    v = _inject_arming(view, arming)
    opts = options if options is not None else defender_toolkit(v)
    try:
        threats, plan = _family_plan(
            v, belief, opts, seats=_DEFENDER_SEATS,
            card_turn_caps=_DEFENDER_CARD_CAPS, min_prob=0.02)
    except Exception:
        return Pressure(0, 0, 0, [], 0, 0.0)
    fatal_threats = [t for t in threats if t.fatal]
    unc = [t for t in plan.uncovered if t.fatal]
    race = [t for t in unc if not t.defendable]
    shortfall = [t for t in unc if t.defendable]
    sev = sum(t.severity for t in unc)
    return Pressure(len(unc), len(shortfall), len(race),
                    [t.label for t in unc], len(fatal_threats), round(sev, 4))


# ---------------------------------------------------------------------------
# 攻撃候補：どのボード/キャラを arm すれば主人公の被覆枠を超えるか
# ---------------------------------------------------------------------------
@dataclass
class OffenseTarget:
    target: str
    target_kind: str
    role: str                        # "real"（真相の勝ち筋）/ "decoy"（CSの偽装先）
    belief_prob: float               # 主人公 belief 上でこの標的が敗北条件である確率
    cur_anyaku: int                 # 現在の暗躍
    pressure_gain: float             # ここを暗躍+1したときの uncovered 重症度の増分
    note: str = ""


# 真相ルール→敗北ボードの対応（defense_plan と共有の定数）。
_FIXED_DEFEAT_BOARD = dict(dp._FIXED_DEFEAT_RULE_BOARD)   # 守るべき→学校・封印→神社


def _true_goal_targets(view: dict) -> list[tuple[str, str]]:
    """脚本の真相から、arm すべき本命の敗北標的 [(target, kind)] を返す（神視点）。"""
    out: list[tuple[str, str]] = []
    ry = view.get("rule_y")
    if ry in _FIXED_DEFEAT_BOARD:
        out.append((_FIXED_DEFEAT_BOARD[ry], "board"))
    if ry in dp._BOARDX_DEFEAT_RULES and view.get("rule_y_board_x"):
        out.append((view["rule_y_board_x"], "board"))
    for inc in view.get("incidents", []):
        if inc.get("name") == "病院の事件":
            out.append(("病院", "board"))
    # KP暗躍（僕と契約＝単独勝ち／殺人計画＝キラーと同エリアで殺害）
    roles = view.get("roles", {})
    kp = next((n for n, r in roles.items() if r == "キーパーソン"), None)
    if kp is not None:
        out.append((kp, "character"))
    return out


def _belief_board_probs(view: dict, belief) -> dict:
    """主人公 belief 上での {board: P(その板を敗北にするルールが実在)}。board_x は隠れを反映。"""
    return _belief_defeat_board_probs(belief, view)


def offense_candidates(view: dict, belief) -> list[OffenseTarget]:
    """arm 候補（ボード＋KP）を「暗躍+1の圧力増分」降順で返す。real/decoy を区別して付す。"""
    if belief is None:
        return []
    base = defender_pressure(view, belief)
    board_probs = _belief_board_probs(view, belief)
    true_targets = set(_true_goal_targets(view))
    ba = view.get("board_anyaku", {}) or {}
    chars = {c.get("name"): c for c in view.get("characters", [])}
    cands: list[OffenseTarget] = []

    # ボード候補（4エリア）
    for a in _AREAS:
        cur = ba.get(a, 0)
        if cur >= 2:
            continue   # 既に敗北域＝これ以上積んでも圧力は増えない
        gain = round(defender_pressure(view, belief,
                     arming={(a, "board"): 1}).severity - base.severity, 4)
        role = "real" if (a, "board") in true_targets else "decoy"
        cands.append(OffenseTarget(
            a, "board", role, round(board_probs.get(a, 0.0), 3), cur, gain))

    # KP暗躍候補（真相のKP）
    roles = view.get("roles", {})
    kp = next((n for n, r in roles.items() if r == "キーパーソン"), None)
    if kp and chars.get(kp, {}).get("alive", True):
        cur = chars[kp].get("anyaku", 0)
        if cur < 2:
            # KP役の belief 確率（主人公がKPをどれだけ特定しているか）
            try:
                _n, kp_p = belief.most_likely_role("キーパーソン")
                kp_p = kp_p if _n == kp else 0.0
            except Exception:
                kp_p = 0.0
            gain = round(defender_pressure(view, belief,
                         arming={(kp, "character"): 1}).severity - base.severity, 4)
            cands.append(OffenseTarget(kp, "character", "real", round(kp_p, 3), cur, gain,
                                       note="KP暗躍（僕と契約=単独勝ち／殺人計画=同エリア殺害）"))

    cands.sort(key=lambda t: (t.pressure_gain, t.belief_prob), reverse=True)
    return cands


# ---------------------------------------------------------------------------
# 攻撃計画：真相を割らずに被覆枠を超える arm 集合を組む
# ---------------------------------------------------------------------------
@dataclass
class AttackPlan:
    current: Pressure                # 現局面で主人公が既に覆え切れていない致命脅威
    candidates: list                 # OffenseTarget（圧力増分降順）
    recommended: list                # arm を勧める OffenseTarget 集合（貪欲）
    cs_live: bool                    # decoy を arm して圧力が増える＝CSが生きている
    projected: Pressure              # recommended を全部 arm した後の予測圧力
    note: str = ""


def _best_arming(view, belief, cands, base_n, *, max_size=3, top_k=6):
    """arm 候補の部分集合を探索し、主人公 uncovered を最大化する arming 集合を返す。

    ★貪欲でなく部分集合探索なのは**相乗**を捉えるため：神社ボード単独も KP暗躍単独も
    主人公は覆えるが、両方 arm すると暗躍禁止1枚/ターンで溢れる（二正面）＝
    どちらの単独増分も0でも、ペアで初めて uncovered が生まれる。候補は belief 確率上位
    top_k・置く枚数は max_size まで（脚本家も1ターン有限）。同点は arm 枚数の少ない方を優先。
    """
    from itertools import combinations
    pool = [t for t in cands if (t.cur_anyaku < 2)][:top_k]
    best_arm: dict = {}
    best_sev = base_n
    for size in range(1, min(max_size, len(pool)) + 1):
        for combo in combinations(pool, size):
            arming = {(t.target, t.target_kind): 1 for t in combo}
            sev = defender_pressure(view, belief, arming=arming).severity
            if sev > best_sev + 1e-9 or (
                    abs(sev - best_sev) <= 1e-9 and best_arm and size < len(best_arm)):
                best_sev, best_arm = sev, arming
    return best_arm, best_sev


def plan_attack(view: dict, belief=None) -> AttackPlan:
    """現局面の攻撃計画を作る。belief 未指定なら view から再構成。

    arm 候補の**部分集合探索**で「主人公が覆え切れない致命脅威(uncovered)を最大化する
    arm 集合」を求める（相乗を捉える＝真の二正面は種類をまたぐ arm の同時成立で生まれる）。
    真相ボードだけでなく decoy（belief が生かしている板）も候補に含む。decoy を混ぜると
    圧力が増えるなら CS が生きている。増分ゼロ（belief 収束）なら note で真相効率へ促す。
    """
    if belief is None:
        belief = protagonist_belief(view)
    current = defender_pressure(view, belief)
    cands = offense_candidates(view, belief)

    best_arm, best_sev = _best_arming(view, belief, cands, current.severity)
    armed_keys = set(best_arm)
    recommended = [t for t in cands if (t.target, t.target_kind) in armed_keys]
    projected = defender_pressure(view, belief, arming=best_arm)

    improved = projected.severity > current.severity + 1e-9
    # CSが生きている＝推奨 arm に decoy が含まれ、かつ圧力（重症度）が現状を上回る
    cs_live = improved and any(t.role == "decoy" for t in recommended)
    if not improved:
        note = ("今ターン arm で主人公の被覆枠を超えられない"
                "（belief 収束済 or ボード敗北ルールが1本＝二正面の材料不足）"
                "＝真相の効率/早撃ち・情報衛生で belief 収束を遅らせる方が先")
    elif cs_live:
        note = "CSが生きている＝真相＋偽装の同時 arm で二者択一を強制できる"
    else:
        note = "真相内の二正面（種類をまたぐ arm）で被覆枠を超えられる"
    return AttackPlan(current, cands, recommended, cs_live, projected, note)


# ---------------------------------------------------------------------------
# markdown レンダ（人間=脚本家モードの内省パネルに『AIの攻撃計画』を出す）
# ---------------------------------------------------------------------------
def render_attack_md(view: dict, plan: AttackPlan) -> str:
    lines = ["##### ⚔️ 攻撃プランナー（主人公の被覆枠を超える二者択一を組む）"]
    cur = plan.current
    lines.append("")
    lines.append(f"**現在の圧力**：主人公が今ターン覆え切れない致命脅威 "
                 f"**{cur.n_uncovered}**（二正面 {cur.n_shortfall}／レース {cur.n_race}"
                 f"・重症度 {cur.severity:.2f}）／点火中の致命脅威 {cur.n_threats}")
    if cur.uncovered_labels:
        lines.append("　┗ 覆え切れない：" + "／".join(cur.uncovered_labels))
    if plan.note:
        lines.append(f"> ⚠ {plan.note}")
    lines.append("")
    lines.append("| arm候補 | 種別 | 主人公が敗北条件と疑う確率 | 現暗躍 | +1で重症度増分 |")
    lines.append("|---|---|---|---|---|")
    for t in plan.candidates[:8]:
        role = "🎯真相" if t.role == "real" else "🃏偽装(CS)"
        star = " ⭐" if t in plan.recommended else ""
        lines.append(f"| {t.target}({t.target_kind}){star} | {role} | "
                     f"{t.belief_prob:.0%} | {t.cur_anyaku} | +{t.pressure_gain:.2f} |")
    lines.append("")
    if plan.recommended:
        arm = "／".join(f"{t.target}" for t in plan.recommended)
        lines.append(f"_推奨 arm：{arm} → 予測圧力 重症度 {plan.projected.severity:.2f}"
                     f"（現在 {cur.severity:.2f}・uncovered {cur.n_uncovered}→{plan.projected.n_uncovered}）_")
    else:
        lines.append("_今ターンに圧力を増やす arm 候補なし（belief収束済 or 供給不足）_")
    return "\n".join(lines)
