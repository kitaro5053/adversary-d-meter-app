# -*- coding: utf-8 -*-
"""B-241：**板ガード（`暗躍禁止`→board）の席の調停**。

## 起票（バックログ §72-33）＝2本の独立レーンが同じ場所に収束した

- **B-238 の族A**＝板ガードの**宛先ミス**（**実績非接地席**＝過去ループの暗躍実績が
  より多い板を差し置いて別の板を守る席。3日 11/37/11・5日 27/37/29）。
- **B-240 の中核所見**（`docs/仮_b240_log/機序_BTX1_L6.md`）＝`random_BTX#1` L6D4 の p2 は
  `不安-1→従者`（1枚で確実に事件が止まる）を options に持ちながら、板ガードに席を取られる。

∴ 共通の診断＝**板ガードが席を取りすぎている**。

## ★設計の制約（過去レーンが実測で確立した判例）

1. **優先度の重み・床の高さはいじらない**（§72-32 §2-5 実測＝床を高くすると `btx5_seal` の
   10局が一斉に 3/4→5 に壊れる）。∴ 本モジュールは**個別の席の手を差し替える**だけで、
   `PRIORITY` にも採点式にも一切触れない。
2. **「落とす」のではなく「置き換える」**（§72-32 判例1＝候補を落としてもその手は自前の点で
   選び直される＝床の撤回型の介入はほぼ何も動かさない）。
3. **cap／譲歩の根拠は「必要性が算術で確定」だけでは足りず「今日の価値がゼロ」も要る**
   （§72-32 判例2＝「または」ではなく「かつ」）。
4. **「ある語彙から見て無価値」は席を譲らせる根拠にならない**（§72-29 判例）＝
   ∴ 譲らせる条件は「その板ガードが**今日**果たしうる仕事が**算術でゼロ**」に限る。

## 2つの腕（どちらも既定 OFF・独立に着脱できる）

### (A) `B241_GUARD_YIELD`＝**確定冷却へ席を譲る**

板ガードの席を `不安-1→X` に差し替える。条件（すべて公開情報＋belief の当日犯人候補）：

- **需要が算術で確定**＝X は「**今日1枚撃てば確実に事件が止まる**」候補
  （`b240_cool_budget.certain_stop_targets`＝今日に事件が予定 ∧ X ∈ `_culprit_cands[today]` ∧
   `u(X)==th(X)`〔臨界ちょうど〕 ∧ B-232 の算術で冷却が効く）。
- **今日の板ガードの価値が算術でゼロ**＝`board_value_zero_today`（下記）。
- **強制席では発火しない**＝`prov=="b100"`（B-100 の絶対防御の上書き席）は触らない。

### (B) `B241_GUARD_GROUND`＝**宛先を実績へ振り替える**

板ガードの**宛先だけ**を、**過去ループ通算の暗躍実績がより多い板**（かつ今日 mm が伏せ札を
置いている＝二択が実在する板）へ差し替える。カードも席も変えない＝**純粋な宛先の振り替え**。

- 代替が options に無ければ何もしない。
- 代替の**採点が板ガード帯にある**こと（`>= PRIORITY["ボード封じ_mm札"]`）を要求する＝
  B-132／B-189／B-194／B-66 が**証明つきで降格した板**へは振り替えない（安全弁）。
- 許容する採点の落差は `B241_GROUND_MAX_DROP`（既定 1.0＝同点帯のタイブレークだけ）。
  ★これは「重みを変える」のではなく「**どこまでの落差なら宛先を振り替えてよいか**」という
  調停の幅＝掃引口。

## 「今日の板ガードの価値が算術でゼロ」の定義（公開情報のみ）

板の敗北には暗躍 **2** が要る（`rules/40:47`＝`defense_plan.BOARD_DEFEAT_ANYAKU`）。
今日その板に載りうる暗躍の上限は

    現在値 + 止まらない供給（噂 1/loop 等・暗躍禁止では止められない） + カード上限

で、**カード上限は1枚ぶん**＝脚本家は1対象に1枚しか置けない（`sim/legal.py` の
`banned_targets`＝`_own_targets`）ので `暗躍+2` の残弾があれば 2・無ければ 1
（`暗躍+2` は 1/loop＝`engine/models.py:30`、使用済みは表向き＝公開）。
これが **2 未満**なら「**今日はこの板では負けない**」＝今日この席をガードに使う価値は算術ゼロ
（板は消えない＝明日また守れる）。

★安全側の除外（どれかに当たれば「ゼロ」と主張しない）：
- **幻想がその板に居る**＝板に置かれた暗躍は幻想本人にも乗る（`rules/30:55`）＝
  板敗北以外の仕事がある。
- **残日程の事件がその板の暗躍を参照する**（`_b132_incident_feeds`＝既存の単一ソース。
  病院の事件は暗躍 **1** で発火＝閾値が 2 ではない、等をここが吸収する）。
- **クロマクの疑いがその板に居る**／**噂の残弾が読めない**＝`board_unstoppable_today` が
  大きな値を返す＝不等号が立たない。
- belief が未整備・例外＝**ゼロと主張しない**（B-113 の流儀＝証明できる時だけ警戒を下げる）。

## 規律

- 材料＝`view` の公開情報＋belief の `_culprit_cands` / `role_marginals`
  （どちらも B-224／B-132 が既に使っている情報源）＝**追加の情報源を持たない**。
- 既定 OFF（`B241_GUARD_YIELD=False` / `B241_GUARD_GROUND=False`）＝呼ばれない＝挙動 bit 不変。
"""
from __future__ import annotations

KINSHI = "暗躍禁止"
ANYAKU = ("暗躍+1", "暗躍+2")

#: ★計測専用フック（既定 None＝不実行）。`arena/b241_probe.py` が list を差し込むと、
#  腕(B) が振り替えを見送った席の**理由**が積まれる（`B100_HOOK` と同じ流儀＝
#  戻り値は使わない・採点にも選択にも一切影響しない）。
DIAG: list | None = None


def _diag(reason: str, **kw) -> None:
    if DIAG is not None:
        DIAG.append({"reason": reason, **kw})


# --------------------------------------------------------- 板の今日の価値 --
def mm_plus2_left(view: dict) -> bool:
    """脚本家の `暗躍+2`（1/loop）がこのループにまだ残っているか（公開情報）。"""
    used = list((view.get("used_cards", {}) or {}).get("mastermind", []))
    return "暗躍+2" not in used


def board_card_cap_today(view: dict) -> int:
    """今日その板に**カード**で載りうる暗躍の上限（脚本家は1対象1枚＝最大1枚）。"""
    return 2 if mm_plus2_left(view) else 1


def board_unstoppable_today(agent, view: dict, area: str) -> int:
    """今日その板に**暗躍禁止では止められない**経路で載りうる暗躍の上限（安全側）。

    例外・belief 未整備・クロマクの疑いが同エリア＝**大きい値**を返す（＝ゼロと主張しない）。
    """
    try:
        from .defense_plan import (_kuromaku_supply_here, rumor_left_for,
                                   rumor_p)
        bel = getattr(agent, "_belief", None)
        if bel is None:
            return 9
        marg = bel.role_marginals()
        # クロマク疑いが同エリア＝毎ターン能力フェイズ +1（rules/40:86）＝止められない
        if _kuromaku_supply_here(view, area, marg):
            return 9
        # ★噂は**確率がゼロでない限り数える**（`_rumor_active` の閾値 0.5 は使わない）＝
        #   本述語は「今日は負けない」を**主張する**側なので、DP-6 の既定より厳しく取る。
        #   残弾＝`rumor_left_for` が 0 を返した時だけ「使い切り済み」と見なす
        #   （None＝未証明＝未消費と仮定＝安全側。`agents/defense_plan.py:2390` の規約）。
        if rumor_p(bel) > 0.0 and rumor_left_for(bel, view) != 0:
            return 1
    except Exception:
        return 9
    return 0


def board_value_zero_today(agent, view: dict, area: str) -> bool:
    """「今日この板を守らなくても、今日この板では負けない」＝今日のガードの価値は算術ゼロ。"""
    try:
        from .defense_plan import BOARD_DEFEAT_ANYAKU
    except Exception:
        return False
    cur = int((view.get("board_anyaku") or {}).get(area, 0) or 0)
    reach = cur + board_unstoppable_today(agent, view, area) \
        + board_card_cap_today(view)
    if reach >= BOARD_DEFEAT_ANYAKU:
        return False
    # 幻想がこの板に居る＝板の暗躍が幻想本人にも乗る（rules/30:55）
    g = next((c for c in (view.get("characters") or [])
              if c.get("name") == "幻想"), None)
    if g is not None and g.get("alive", True) and g.get("area") == area:
        return False
    # 残日程の事件がこの板の暗躍を参照する（既存の単一ソース）
    try:
        if agent._b132_incident_feeds(view, area, int(view.get("day", 1))):
            return False
    except Exception:
        return False
    return True


# ------------------------------------------------------ (A) 席を譲る調停 --
def yield_swap(agent, view: dict, options: list, best: dict):
    """板ガードの席を「今日1枚で確実に止まる冷却」へ差し替える手（無ければ None）。"""
    if not getattr(agent, "B241_GUARD_YIELD", False):
        return None
    if best.get("card") != KINSHI or best.get("target_kind") != "board":
        return None
    if best.get("prov") == "b100":        # B-100 の絶対防御の上書き席は触らない
        return None
    if not board_value_zero_today(agent, view, best.get("target")):
        return None
    from .b240_cool_budget import certain_stop_targets
    aims = certain_stop_targets(agent, view)
    if not aims:
        return None
    cand = [o for o in options
            if o.get("card") == "不安-1" and o.get("target_kind") == "character"
            and o.get("target") in aims]
    if not cand:
        return None
    # 決定的なタイブレーク＝臨界の高い順→名前順（列挙順に依存しない）
    from engine.data import unrest_threshold_of
    cand.sort(key=lambda o: (-(unrest_threshold_of(o["target"]) or 0), o["target"]))
    return cand[0]


# --------------------------------------------------- (B) 宛先の振り替え --
def past_loop_board_anyaku(view: dict) -> dict:
    """板 → **過去ループ通算**の暗躍カード配置数（公開情報）。

    解決時に全カードが公開される（`sim/flow.py` の `cards_revealed`）＝過去ループの
    暗躍配置は卓上で見えている。現ループは含めない＝**L1 は常に空**（初見を咎めない）。
    """
    lp = view.get("loop")
    out: dict = {}
    for e in (view.get("history") or []):
        if e.get("event") != "cards_revealed" or e.get("loop") == lp:
            continue
        for p in (e.get("placements") or []):
            if (p.get("owner") == "mastermind" and p.get("target_kind") == "board"
                    and p.get("card") in ANYAKU):
                out[p.get("target")] = out.get(p.get("target"), 0) + 1
    return out


def ground_redirect(agent, view: dict, options: list, best: dict, score):
    """板ガードの**宛先だけ**を実績接地した板へ振り替えた手（無ければ None）。"""
    if not getattr(agent, "B241_GUARD_GROUND", False):
        return None
    if best.get("card") != KINSHI or best.get("target_kind") != "board":
        return None
    if best.get("prov") == "b100":
        return None
    b = best.get("target")
    hist = past_loop_board_anyaku(view)
    gap = int(getattr(agent, "B241_GROUND_MIN_GAP", 1) or 1)
    mm_boards = {p.get("target") for p in (view.get("placements") or [])
                 if p.get("owner") == "mastermind" and p.get("target_kind") == "board"}
    rivals = sorted(x for x in mm_boards
                    if x != b and hist.get(x, 0) - hist.get(b, 0) >= gap)
    if not rivals:
        return None
    try:
        from .heuristic_protagonist import PRIORITY
        band = PRIORITY["ボード封じ_mm札"]
        s_best = float(score(best))
    except Exception:
        _diag("score_error", board=b)
        return None
    drop = float(getattr(agent, "B241_GROUND_MAX_DROP", 1.0) or 0.0)
    pool = []
    seen = 0
    for o in options:
        if (o.get("card") != KINSHI or o.get("target_kind") != "board"
                or o.get("target") not in rivals):
            continue
        seen += 1
        s = float(score(o))
        if s < band:                       # 証明つきで降格された板（B-132/189/194/66）
            _diag("below_band", board=b, alt=o["target"], s=s, s_best=s_best)
            continue
        if s < s_best - drop:              # 採点の落差が許容幅を超える
            _diag("drop", board=b, alt=o["target"], s=s, s_best=s_best)
            continue
        pool.append((hist.get(o["target"], 0), s, o["target"], o))
    if not pool:
        if not seen:
            _diag("no_option", board=b, rivals=rivals)
        return None
    _diag("swap", board=b, alt=pool and sorted(pool, key=lambda t: (-t[0], -t[1], t[2]))[0][2],
          s_best=s_best)
    # 決定的なタイブレーク＝実績の多い順→点の高い順→名前順
    pool.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return pool[0][3]
