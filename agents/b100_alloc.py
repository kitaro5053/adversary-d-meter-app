# -*- coding: utf-8 -*-
"""B-100 Phase 2：**席の割り当てを3席まとめて解く**（joint allocation）。

設計の正典＝`docs/監査_B100_Phase1_シャドー実行_2026-07-29.md` §11（FableAの訂正）
＋ Phase 2 の確定設計（ユーザー承認済み）。

## Phase 1 の何が壊れていたか（実測で取れた機序）

`random_FS` s6・3日級 L2D1（席順 p3→p1→p2）の検死：

```
致命脅威（fatal かつ defendable）は2本
  prob=1.00 kp_sk        SKによる巫女（KP）殺害（2人きり・神社）
  prob=0.35 board_defeat 学校のボード敗北
このターンは `_turn_plan`（既存の3席一括計画）が立っている
  [移動←→→ナース, 移動↑↓→お嬢様, 暗躍禁止→学校]
  p3 の予定手 移動←→→ナース    … どの制約も担っていない  ← ここが「空いている席」
  p1 の予定手 移動↑↓→お嬢様    … 学校のボード敗北を折る（お嬢様を学校から動かす）
  p2 の予定手 暗躍禁止→学校     … 学校のボード敗北を折る
```

∴ **p3 に巫女退避を割り当てれば2本とも同時に充足できる**（解は存在する）。
Phase 1 がそれを落としていた原因は2つとも**実装**である：

1. **「押し出す手」を `max(options, key=score)` で見ていた**。ところが `_turn_plan` が
   立っているターンは、その席が実際に打つのは**計画の割当**であって点数最大ではない
   （p1 は点数最大が `暗躍禁止→学校(100)` なのに、実際に打つのは `移動↑↓→お嬢様(1.0)`）。
   ＝Phase 1 の `B100_MAX_DISPLACED`（80点ガード）は**見ている対象そのものが違った**。
2. **強制した席が `_turn_plan` を消費しなかった**。`decide()` は
   「B-100 が強制しなかったときだけ `_turn_plan.pop(0)`」なので、先頭席で強制すると
   **計画全体が1つ後ろへずれ、末尾の1手（＝`暗躍禁止→学校`）が永久に落ちる**。
   ＝§11-2 の「押し出された手を拾える後続席が存在しない」の正体。

## Phase 2 の割り当て（本モジュール）

- **制約**＝`fatal` かつ `defendable` な脅威。**実在度は優先順位にだけ使い、
  「担っているか」の判定には使わない**（prob=0.35 の板敗北も制約として保護される）。
- **その席が今打とうとしている手が既にどれかの制約を折っているなら、その席は使わない**
  （＝支払うのは**何も担っていない席**）。★ここで見るのは `_turn_plan` の割当を含む
  **実際の予定手**であって点数最大ではない（上記1の是正）。
- **後続席の予定手も見る**（`_turn_plan` があるときは3席分が既知）＝
  「後続席が自然に折る筋」を未被覆に数えない。
- **最終席から奪わない**（後続席で回復できる余地を残す・フラグ）。
- **席が足りず制約を落とすときの優先順位（ユーザー裁定 2026-07-29）**：
  ①**未被覆の「反復筋」**（同型の敗北の反復＝正典 §6 の鉄則）→ ②実在度の高い順 → ③安い順。
- 強制した席は `_turn_plan` を**消費する**（上記2の是正）＝計画の残りは後続席へそのまま渡る。

既定は OFF（`HeuristicProtagonist.B100_JOINT = False`）＝挙動 bit 不変。
"""

from __future__ import annotations

from .b100_mix import (SUPPLY_MAX, futile_reason, noop_ctx_for, past_loss_keys,
                       repeat_count, rumor_prob, supply_candidate_count)


# ---------------------------------------------------------------------------
# 「この席が B-100 が無ければ打つ手」＝ decide() の選択順序をそのまま写す
# ---------------------------------------------------------------------------
def intended_move(agent, options: list[dict], score) -> tuple[dict | None, bool]:
    """(この席の予定手, `_turn_plan` 由来か)。

    `decide()` は  ①B-100 ②`_turn_plan.pop(0)` ③`max(options, key=score)`  の順で選ぶ。
    ∴ B-100 が無いときの予定手は「計画があれば計画の先頭（options に無ければ点数最大）」。
    ★計画の先頭が options に無いときでも `decide()` は pop する＝第2要素は True のまま。
    """
    plan = getattr(agent, "_turn_plan", None)
    if plan:
        it = plan[0]
        key = (it["card"], it["target"], it.get("target_kind"))
        for o in options:
            if (o["card"], o["target"], o.get("target_kind")) == key:
                return o, True
        return (max(options, key=score) if score is not None else None), True
    return (max(options, key=score) if score is not None else None), False


def _key(o) -> tuple:
    if isinstance(o, dict):
        return (o["card"], o["target"], o.get("target_kind"))
    return (o.card, o.target, o.target_kind)


# ---------------------------------------------------------------------------
# ★Phase 3：折り手そのものが「別の2人きり」を自作していないか（自傷の判定）
# ---------------------------------------------------------------------------
#: SK系の役職名（`agents/defense_plan._sk_pair_threats` が脅威を立てる役職）。
_SK_ROLE = "シリアルキラー"
#: 「SK疑い」とみなす周辺確率のしきい値（`defense_plan._SUSPECT_P` と同じ思想）。
_SK_P = 0.5


def self_harm_reason(view: dict, roles: dict, card: str, target: str,
                     target_kind: str) -> str | None:
    """その折り手を打つと**別の致命局面（2人きり）を自作する**なら理由を返す。

    ★実測（B-100 Phase 3 の検死・`random_BTX` s4 L3D2＝3日級/5日級とも同じ機序）：
      病院に {刑事, 男子学生, SK候補} の3人。防御プランナは
      「SKによる刑事殺害（2人きり・病院）」と「SKによる男子学生殺害（2人きり・病院）」を
      **2本とも** `fatal` で挙げる。B-100 は前者を `移動←→→刑事`（刑事の退避）で折るが、
      **残った {男子学生, SK} がちょうど2人きりになり、同ターンに男子学生が死ぬ**
      （`off` では起きない＝B-100 が作った死である）。
      ＝`live_constraints` の「空振りでない」判定は**ゼロ効果**しか見ておらず、
        **負の効果（自傷）**を見ていなかった。

    判定は公開情報＋belief の周辺確率だけを使う（盤面の先読みはしない）：
      移動でキャラXをエリアAから出す時、**A の生存者がちょうど3人**で、
      残る2人が「SK疑い（周辺確率 ≥ 0.5）」と「VIP（KP∪フレンド容疑者）」なら自傷。
      ★VIP を要求するのが要点＝SK と**ただの通行人**が2人きりになっても
        ループ敗北にはならない（`defense_plan._sk_pair_threats` が脅威を立てる条件と揃える）。
        VIP を要求しない粗い版は 3日級の介入 20→2 と潰しすぎた（§Step3 の実測）。
    """
    if target_kind != "character" or not card.startswith("移動") \
            or card == "移動禁止":
        return None
    chars = [c for c in (view.get("characters") or []) if c.get("alive", True)]
    me = next((c for c in chars if c.get("name") == target), None)
    if me is None:
        return None
    area = me.get("area")
    rest = [c for c in chars if c.get("area") == area and c.get("name") != target]
    if len(rest) != 2:
        return None                        # 3人でない＝退避で2人きりにはならない
    try:
        from .defense_plan import _vip_suspects
        vips = _vip_suspects(view, roles)   # 死亡でループ敗北になる守るべき対象
    except Exception:
        return None
    for i, c in enumerate(rest):
        p_sk = float((roles.get(c.get("name")) or {}).get(_SK_ROLE, 0.0))
        other = rest[1 - i].get("name")
        if p_sk >= _SK_P and other in vips:
            return (f"{area}にVIP {other} と {c.get('name')}(SK疑い{p_sk:.2f})の"
                    f"2人きりを作る")
    return None


# ---------------------------------------------------------------------------
# ★B-112 の是正フラグ（`B100_REPAIR_BOARD` / `B100_REPAIR_PROB`）は **DP-6 で退役**
#   （2026-07-31）。B-112 は本体（`agents/defense_plan._threat_board_defeat`）に触れない
#   制約の下で「B-100 の入口で脅威の写しを作る」是正（`board_repair_reason` /
#   `repaired_threats`）を実装したが、DP-6 が本体をカウンタ収支
#   （`defense_plan.unstoppable_supply_gap`＝単一ソース）に直した＝写しの是正は不要。
#   検証していた性質は本体側のテストへ移し替えた（tests/test_defense_plan.py の DP-6 節・
#   tests/test_b100_alloc.py §12）。
# ---------------------------------------------------------------------------
# 制約の列挙（fatal かつ defendable かつ 既置で未被覆）
# ---------------------------------------------------------------------------
def live_constraints(agent, view: dict, options: list[dict], threats, *,
                     roles: dict, ctx, rumor_p: float, placed: set,
                     blocked_labels=(), stats: dict | None = None,
                     culprits: dict | None = None,
                     supply_max: int = SUPPLY_MAX) -> list[dict]:
    """制約1本ぶんの情報をまとめて返す（実在度での足切りはしない＝設計2）。

    - `keys`  ＝ **空振りでない**折り手のキー集合（席を問わない）＝
                「その席の予定手がこの制約を担っているか」の判定に使う。
    - `here`  ＝ **この席から実際に打てる**空振りでない折り手 [(cost, break, option)]（安い順）。

    `stats`（既定 None＝**何もしない**）を渡すと、ふるい落としの各段の本数を記録する
    （B-100 Phase 3 の発火面積の計測＝`arena/b100_funnel.py`）。既定では 1回の
    `is not None` 判定が増えるだけ＝**挙動 bit 不変**。
    """
    by_key: dict = {}
    for o in options:
        by_key.setdefault(_key(o), o)
    past = past_loss_keys(view.get("history", []) or [], view.get("loop"))
    cast = [c.get("name") for c in view.get("characters", []) or []]
    inc_names = {i.get("name") for i in view.get("incidents", []) or []}

    out: list[dict] = []
    for t in threats:
        if stats is not None:              # ★B-112：段A の**手前**も数える
            stats["T"] = stats.get("T", 0) + 1
            if t.fatal:
                stats["T_fatal"] = stats.get("T_fatal", 0) + 1
                if not t.defendable:
                    stats["T_fatal_undef"] = stats.get("T_fatal_undef", 0) + 1
                    stats.setdefault("undef_kinds", []).append(t.kind)
            else:
                stats.setdefault("nonfatal_kinds", []).append(t.kind)
        if not t.fatal or not t.defendable:
            continue
        if stats is not None:              # 段A＝fatal かつ defendable
            stats["A"] = stats.get("A", 0) + 1
        if t.label in blocked_labels:
            continue
        raw = [(b.card, b.target, b.target_kind)
               for c in t.conditions for b in c.breaks]
        if any(k in placed for k in raw):
            continue                       # 自チームが既に折った＝被覆済み
        if stats is not None:              # 段B＝そのうち未被覆
            stats["B"] = stats.get("B", 0) + 1
        keys, here = set(), []
        for c in t.conditions:
            for b in c.breaks:
                if futile_reason(view, agent, roles, b.card, b.target,
                                 b.target_kind, ctx, rumor_p):
                    continue               # 公開情報から確実にゼロ効果＝折り手に数えない
                k = (b.card, b.target, b.target_kind)
                keys.add(k)
                o = by_key.get(k)
                if o is not None:
                    # ★Phase 3：自傷（別の2人きりを自作する）折り手はこの席から打たない。
                    #   既定 OFF（`B100_SELF_HARM` 属性が無ければ False）＝挙動 bit 不変。
                    _sh = getattr(agent, "B100_SELF_HARM", False)
                    if _sh and self_harm_reason(view, roles, b.card, b.target,
                                                b.target_kind):
                        if stats is not None:
                            stats["self_harm"] = stats.get("self_harm", 0) + 1
                        # ★"strict"＝自傷の折り手が1つでもあるなら**この制約自体を
                        #   この席から払わない**（別の折り手へ振り替えない）。
                        #   実測：振り替え（True）は「安い退避を潰して高い代替を打つ」形になり
                        #   介入が増えて悪化した（3日級 防衛128→127）＝§Step3。
                        if _sh == "strict":
                            here = None
                            break
                        continue
                    here.append((b.cost, b, o))
            if here is None:
                break
        if here is None:                   # "strict"＝自傷を含む制約はこの席から払わない
            here = []
        if not keys:
            continue                       # 折り手が全部空振り＝制約として扱えない
        if stats is not None:              # 段C＝そのうち空振りでない折り手が存在
            stats["C"] = stats.get("C", 0) + 1
            if here:
                stats["C_here"] = stats.get("C_here", 0) + 1
        here.sort(key=lambda x: (x[0], x[1].card, x[1].target))
        # ★B-112：供給候補数（ユーザー裁定 Phase 0 §10-1 の「確度100%級」の第2軸）。
        #   `culprits` 未指定なら計算しない（None＝100%級に数えない＝従来どおり）。
        n_sup = (supply_candidate_count(t, roles, culprits or {}, view)
                 if culprits is not None else None)
        out.append({"threat": t, "keys": keys, "here": here,
                    "prob": float(t.prob),
                    "n_supply": n_sup,
                    "top": (n_sup is not None and n_sup <= supply_max),
                    "repeat": repeat_count(t, past, cast, inc_names)})
    return out


# ---------------------------------------------------------------------------
# 発火の資格（θ経路／鉄則①）＝`allocate` と計測（Phase 3）の**単一ソース**
# ---------------------------------------------------------------------------
def gate_reason(c: dict, *, theta: float, iron_prob: float | None,
                force_gate: str, supply_gate: bool = False,
                supply_max: int = SUPPLY_MAX) -> str | None:
    """この制約が「席を使ってでも折る」資格を持つか。持たなければ None。

    ★B-112（Phase 4）＝**資格は2軸**にできる：
      ① θ経路＝実在度（確率）が θ 以上。
      ② **供給候補数**＝ユーザー裁定（Phase 0 §10-1）「確度100%級＝その負け筋に
         供給している役職の候補が2人以下」。**確率とは独立の軸**であり、
         「実在度は 0.5 でも、供給しうる役職の候補が2人以下なら筋が見えている」
         という実戦的な線引き（`supply_gate=True` で有効）。
      ③ 鉄則①＝同型の敗北の反復（正典 §6）。
    """
    t = c["threat"]
    if force_gate == "all":
        return "制約（fatal×defendable）"
    if t.prob >= theta - 1e-9:
        return f"θ経路（実在度{t.prob:.2f}≥{theta}）"
    if supply_gate and c.get("top"):
        return (f"供給候補≤{supply_max}（{c.get('n_supply')}人"
                f"・実在度{t.prob:.2f}）")
    if iron_prob is not None and t.prob >= iron_prob - 1e-9 and c["repeat"] >= 1:
        return f"鉄則①（同型の敗北{c['repeat']}回・実在度{t.prob:.2f}）"
    return None


# ---------------------------------------------------------------------------
# 割り当て本体
# ---------------------------------------------------------------------------
def allocate(agent, view: dict, options: list[dict], threats, *,
             theta: float, iron_prob: float | None, force_gate: str,
             placed: set, seats_used: int, n_prot_placed: int,
             max_seats: int, max_seats_2x100: int, spare_last: bool,
             score, blocked_labels=()):
    """この席で強制する option を返す `(option, threat, reason, intent)`／None。

    `intent` ＝ B-100 が無ければこの席が打っていた手（介入ログ用＝CF帰属の1次データ）。
    """
    if not threats:
        return None
    try:
        roles = agent._belief.role_marginals()
    except Exception:
        roles = {}
    try:
        culprits = agent._belief.culprit_candidates() or {}
    except Exception:
        culprits = {}
    ctx = noop_ctx_for(agent, view)
    rumor_p = rumor_prob(agent)

    # ★B-112：資格の第2軸（供給候補数）。既定 OFF＝`culprits=None` を渡して
    #   `supply_candidate_count` を呼ばない＝**挙動 bit 不変**（呼び出しコストも増えない）。
    supply_gate = bool(getattr(agent, "B100_SUPPLY_GATE", False))
    supply_max = getattr(agent, "B100_SUPPLY_MAX", None) or SUPPLY_MAX
    # ★DP-6：B-112 の写しの是正（repaired_threats）は退役＝本体（defense_plan）が直った。
    cons = live_constraints(agent, view, options, threats, roles=roles, ctx=ctx,
                            rumor_p=rumor_p, placed=placed,
                            blocked_labels=blocked_labels,
                            culprits=(culprits if supply_gate else None),
                            supply_max=supply_max)
    if not cons:
        return None

    def _gate(c):
        return gate_reason(c, theta=theta, iron_prob=iron_prob,
                           force_gate=force_gate, supply_gate=supply_gate,
                           supply_max=supply_max)

    # ---- Phase 3：計画が無いターンは「どの席を取るか」と「どの需要を落とすか」が別物 ----
    # 検死（`random_BTX` s4 L3D2・3日級／5日級とも同一）：
    #   off  : p2 移動禁止→男子学生 / p3 暗躍禁止→神社(100) / p1 移動禁止→教師
    #   強制 : p2 移動禁止→男子学生 / p3 移動←→→刑事(強制)  / p1 暗躍禁止→神社
    #   ＝B-100 が奪ったのは p3 の `暗躍禁止→神社` だが、実際に落ちたのは
    #     **p1 の `移動禁止→教師`**（p1 が貪欲に再採点して 神社 を拾い直したため）。
    #   ∴ B-100 は「取る席」しか選べず「落ちる需要」を選べていない。
    # ★是正＝払う気になったターンだけ、AI本体の3席一括計画（`_plan_turn`）を立てて
    #   後続席の意図を確定させる。計画は「逐次貪欲の再現」を基準に持つので、
    #   計画ゲート未達なら旧挙動と同じ3手組が返る（＝落ちる需要が B-100 の選択と一致する）。
    # 既定 OFF（`B100_MAKE_PLAN` 属性が無ければ False）＝挙動 bit 不変。
    if (getattr(agent, "B100_MAKE_PLAN", False) and n_prot_placed == 0
            and not getattr(agent, "_turn_plan", None) and score is not None
            and any(c["here"] and _gate(c) for c in cons)):
        try:
            _p = agent._plan_turn(view, options, score)
        except Exception:
            _p = None
        if _p:
            agent._turn_plan = list(_p)

    intent, _from_plan = intended_move(agent, options, score)

    # ---- 設計3：この席の予定手が既にどれかの制約を折っているなら、この席は使わない ----
    # ★B-112：この短絡は**実在度を見ない**＝実在度0.05の制約を折っているだけの席も
    #   「担っている席」として除外される。Phase 3 のファネル実測では
    #   **3日級 765席・5日級 775席がここで落ち**、うち **10席／35席は
    #   『別の資格つき制約を未被覆のまま取り残していた』**（G_lost_other）。
    #   `B100_MATCH_GATED`（既定 OFF）＝**資格つき制約を折っている席だけを守る**
    #   ＝辞書式の優先順位（資格つき ＞ 資格なし）に揃える。
    ikey = _key(intent) if intent is not None else None
    if ikey is not None:
        _matched = [c for c in cons if ikey in c["keys"]]
        if _matched and (not getattr(agent, "B100_MATCH_GATED", False)
                         or any(_gate(c) for c in _matched)):
            return None

    # ---- 後続席の予定手（`_turn_plan` があるときだけ既知）で未被覆を絞る ----
    plan = getattr(agent, "_turn_plan", None) or []
    later_keys = [_key(it) for it in plan[1:]]
    n_seats_left = max(0, 3 - n_prot_placed)          # この席を含む残り席数
    # 後続席のうち「何も担っていない＝支払える席」の数（計画が無ければ不明＝空きとみなす）
    if plan:
        n_free_later = sum(1 for k in later_keys
                           if not any(k in c["keys"] for c in cons))
    else:
        n_free_later = max(0, n_seats_left - 1)

    open_ = [c for c in cons
             if not any(k in c["keys"] for k in later_keys)]   # 後続席が折らない制約
    if not open_:
        return None

    # ---- 発火の資格（設計2の literal＝"all" ／ 従来どおり θ・鉄則で絞る＝"theta"） ----
    elig = [(c, w) for c in open_ if (w := _gate(c)) is not None]
    if not elig:
        return None

    # ---- 席の上限（§10-1「確度100%級が2本→3席」はそのまま引き継ぐ） ----
    n_top = sum(1 for c, _w in elig
                if (n := supply_candidate_count(c["threat"], roles, culprits,
                                                view)) is not None and n <= SUPPLY_MAX)
    cap = max_seats_2x100 if n_top >= 2 else max_seats
    if seats_used >= cap:
        return None

    # ---- 設計4：最終席から奪わない（後続席で回復できる余地を残す） ----
    # ★Phase 3：計画が無いターンは**最終席でだけ払う**（`B100_NOPLAN_LAST`・既定OFF）。
    #   機序＝カスケードが起きるのは「後続席が貪欲に再採点する」からで、
    #   最終席には後続席が無い＝「取る席」と「落ちる需要」が必ず一致する。
    #   ∴ 計画あり＝どの席でもよい（後続の意図が既知）／計画なし＝最終席のみ、が
    #   カスケードを構造的にゼロにする最小の述語になる。
    if getattr(agent, "B100_NOPLAN_LAST", False) and not plan:
        if n_seats_left > 1:
            return None
    elif spare_last and n_seats_left <= 1:
        return None

    # ---- 設計5：席が足りないときの落とし方＝①反復筋 ②実在度 ③安さ ----
    elig.sort(key=lambda x: (0 if x[0]["repeat"] >= 1 else 1,
                             -x[0]["prob"],
                             x[0]["here"][0][0] if x[0]["here"] else 9e9,
                             x[0]["threat"].label))
    # 支払える席の総数（この席＋後続の空き席）。cap を超えては使わない。
    n_pay = min(cap - seats_used, 1 + n_free_later)
    for c, why in elig[:max(1, n_pay)]:
        if not c["here"]:
            continue                       # この席からは打てない＝後続の空き席に委ねる
        _cost, brk, opt = c["here"][0]
        return opt, c["threat"], f"{why}／折り手＝{brk.label}", intent
    return None
