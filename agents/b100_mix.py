# -*- coding: utf-8 -*-
"""B-100：混合AI（防御プランナーを**制約＝絶対防御**として使う）の中核。

設計の正典＝`docs/監査_B100_Phase0_介入率_2026-07-29.md` §10（ユーザー裁定を反映した確定仕様）。

## 何をするモジュールか

現行AI（`agents/heuristic_protagonist`）は「全部の手を点数で比較して高い順に3席を埋める」。
本モジュールは**その点数体系に一切触れず**、席を埋める前に
「**この負け筋だけは絶対に覆う**」という制約を1本（例外的に最大3本）差し込む。
制約で埋まらなかった席は従来どおり点数で決まる（＝混合）。

## 発火条件（Phase 0 の実測に基づく＝広げない）

どの経路でも**必須の前提**（§10-3-2）：

    fatal × defendable × 未被覆 × 折り手が空振りでない（void の暗躍禁止等を弾く）

その上で、独立した2本の発火経路：

1. **θ経路**＝`prob >= θ`（既定 θ=1.0）。Phase 0 §8-1＝この帯は空振り0%・席1枚・
   未被覆種別が `kp_sk` 一本＝退行面積が構造的に小さい。
2. **鉄則①経路**（§10-2 ユーザー裁定）＝**同型の敗北を1回以上経験しており**、
   かつ `prob >= IRON_PROB`。「一度同じ負け方をしたら、以後その筋の同じ入り方は避ける」。

## 席の上限（§10-1 ユーザー裁定）

既定1席。**「確度100%級」が2本立つときだけ最大3席**まで許す。
「確度100%級」＝**その負け筋に供給している役職の候補が2人以下**
（事件系＝犯人候補／板敗北＝クロマク候補／KP系＝キラー・SK候補）。
★型→供給役職集合の写像は `KIND_SUPPLY_ROLES` **1か所だけ**に持つ（散らさない）。
※Phase 0 実測では3席強制は両ベンチ0件＝**当面デッドコードになる見込み**。

## 既定OFF

`HeuristicProtagonist.B100_MIX = False`＝本モジュールの関数は呼ばれない＝挙動 bit 不変。
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import defense_plan as _dp
from .card_effect import NoopCtx, noop_reason
from .defense_plan import (BOARD_DEFEAT_ANYAKU, _kuromaku_supply_here, _suspects,
                           _unstoppable_supply, rumor_left_for,
                           unstoppable_supply_gap)

_BOARDS = ("病院", "神社", "都市", "学校")

#: 脅威 kind → 「どの型の敗北になるか」（公開情報で観測できる敗北の型と突き合わせる）。
#:   loop_end_cond ＝ ループ終了時判定（板の暗躍≥2・僕と契約・未来改変）
#:   instant       ＝ 即ループ終了（KP/フレンド死亡・TT任意敗北）
#:   prot_death    ＝ 主人公の死亡
#: ★B-100 Phase 0 から移設（単一ソース化）。`arena/b100_audit.py` はここを import する。
KIND_LOSS_TYPE = {
    "board_defeat": "loop_end_cond", "kp_anyaku": "loop_end_cond",
    "butterfly": "loop_end_cond",
    "factor_kp": "instant", "kp_killer": "instant", "kp_sk": "instant",
    "virus_sk": "instant", "sk_setup": "instant", "incident_vip": "instant",
    "remote_murder_vip": "instant", "tt_defeat": "instant",
    "killer_protagonist": "prot_death", "mainlover_protagonist": "prot_death",
    "mainlover_chain": "prot_death", "hospital_protagonist": "prot_death",
}

#: ★型 → その負け筋に**供給している役職**の集合（§10-1 ユーザー裁定の操作化）。
#:  `"CULPRIT"` は特別扱い＝役職ではなく **その事件の犯人候補集合**を数える。
#:  ★ここが写像の**唯一の置き場**。他所で kind→役職 を書かないこと。
#:  未登録の kind は「供給役職が特定できない」＝**確度100%級に数えない**（健全側）。
KIND_SUPPLY_ROLES = {
    # 事件系＝犯人候補（ユーザー裁定の元の形）
    "incident_vip": ("CULPRIT",),
    "remote_murder_vip": ("CULPRIT",),
    "butterfly": ("CULPRIT",),
    "hospital_protagonist": ("CULPRIT",),
    # 板敗北＝その板に暗躍を供給しうる役職
    "board_defeat": ("クロマク", "黒猫"),
    "tt_defeat": ("クロマク", "黒猫"),
    "kp_anyaku": ("クロマク", "黒猫"),
    # KP系＝KPを落とす手を持つ役職
    "kp_sk": ("シリアルキラー",),
    "sk_setup": ("シリアルキラー",),
    "kp_killer": ("キラー",),
    "killer_protagonist": ("キラー",),
}


# ---------------------------------------------------------------------------
# 空振り判定（B-93／B-28 の接地。判定不能は「空振りでない」側＝健全側に倒す）
# ---------------------------------------------------------------------------
def _mm_boards_now(view: dict) -> set:
    return {p["target"] for p in view.get("placements", []) or []
            if p.get("owner") == "mastermind" and p.get("target_kind") == "board"}


def _mm_chars_now(view: dict) -> set:
    return {p["target"] for p in view.get("placements", []) or []
            if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}


def rumor_prob(agent) -> float:
    """belief 上の「不穏な噂」実在度（行動解決フェイズ外の暗躍供給＝暗躍禁止で止まらない）。"""
    try:
        rm = agent._belief.rule_marginals()
    except Exception:
        return 0.0
    return sum(w for (ry, rxs), w in rm.items()
               if ry == "不穏な噂" or "不穏な噂" in (rxs or ()))


def futile_reason(view: dict, agent, roles: dict, card: str, target: str,
                  kind: str, noop_ctx: NoopCtx, rumor_p: float) -> str | None:
    """その折り手が「公開情報から確実にゼロ効果」なら理由文字列、そうでなければ None。

    - 板への暗躍禁止：mm札なし(void)＝今ターン打ち消す暗躍+が無い（KB 10:61）／
      クロマク疑いが同エリア（毎ターン供給・KB 40:86）／★DP-6＝止まらない供給の
      **カウンタ収支**（`defense_plan.unstoppable_supply_gap`＝単一ソース）で
      「噂1/loop だけで暗躍2に届く」ときだけ空振り（現在値0の板は空振りにしない）。
    - キャラ対象：`card_effect.noop_reason`（B-28＝heuristic と plan の単一ソース）。
    - それ以外（板への移動＝幻想の板札退避など）は判定不能＝None（健全側）。
    """
    if kind == "board":
        if card == "暗躍禁止":
            if target not in _mm_boards_now(view):
                return "void（mmがその板に伏せていない＝打ち消す暗躍+が無い）"
            # ★DP-6（2026-07-31）：B-112 §3-2 が特定した「同じ過大主張の2つ目の実装」
            #   （`rumor_p >= 0.5` だけで空振り扱い）を**本体と同じ単一ソース**
            #   （`defense_plan.unstoppable_supply_gap`＝カウンタ収支）へ置き換える。
            #   B-112 の是正フラグ `B100_REPAIR_BOARD` は退役（本体が直った＝写しの是正は不要）。
            if not _dp.DP6_SUPPLY_LEDGER:
                # ── ablation（A-78）：旧挙動を bit 再現（CF帰属・変異テスト用） ──
                if _unstoppable_supply(view, target, roles):
                    return "供給がクロマク/黒猫由来＝暗躍禁止では止まらない"
                if rumor_p >= 0.5:
                    return "供給が不穏な噂由来でありうる＝暗躍禁止では止まらない"
                return None
            if _kuromaku_supply_here(view, target, roles):
                # クロマク＝毎ターン供給できる（rules/40:86）＝維持
                return "供給がクロマク由来＝暗躍禁止では止まらない"
            cur = int((view.get("board_anyaku") or {}).get(target, 0) or 0)
            # ★B-113：噂の残弾を belief から注入（単一ソース＝rumor_left_for）。
            #   このループで噂消費が証明済みなら残弾0＝現在値1の板への暗躍禁止は
            #   空振りでなくなる（本体 _threat_board_defeat と同じ収支）。
            if unstoppable_supply_gap(view, target, cur, BOARD_DEFEAT_ANYAKU,
                                      supply_rumor=rumor_p >= 0.5,
                                      roles=roles,
                                      rumor_left=rumor_left_for(
                                          getattr(agent, "_belief", None),
                                          view)) <= 0:
                return "止まらない供給（噂1/loop）だけで暗躍2に届く＝暗躍禁止では防げない"
        return None
    try:
        n = noop_reason(view, card, target, kind, noop_ctx)
    except Exception:
        return None
    return n.reason if n is not None else None


def noop_ctx_for(agent, view: dict) -> NoopCtx:
    """agent の持ち物から `NoopCtx` を組む（監査側と同じ材料＝単一ソース）。"""
    return NoopCtx(
        mm_chars=frozenset(_mm_chars_now(view)),
        keyperson=getattr(agent, "_keyperson", None),
        kill_zone=None,
        kuromaku_suspects=frozenset(getattr(agent, "_kuromaku_suspects", ()) or ()),
        killer_suspects=frozenset(getattr(agent, "_killer_suspects", ()) or ()),
        friend_guards=frozenset(getattr(agent, "_friend_guards", ()) or ()),
    )


# ---------------------------------------------------------------------------
# 敗北の署名（B-95 の再実装＝**公開情報のみ**。B-100 Phase 0 から移設）
# ---------------------------------------------------------------------------
def loss_signatures(history: list[dict]) -> dict[int, dict]:
    """公開履歴から「敗北したループ」の署名を作る。

    署名の材料（すべて主人公ビューに載る公開情報）：
      - `loop_board` の board_anyaku から **暗躍≥2 の板集合**／char_anyaku から暗躍≥2 のキャラ
      - `loop_end` の **公開理由と日**／`incident`(occurs) の **(日,事件名)**／`death` の **(日,名前)**
    ★秘匿情報（配役・犯人・ルール）は一切参照しない。
    """
    lost = {e.get("loop") for e in history
            if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}
    sigs: dict[int, dict] = {lp: {"boards": set(), "anyaku_chars": set(),
                                  "reasons": set(), "incidents": set(),
                                  "deaths": set(), "end_days": set(), "type": None}
                             for lp in lost if lp is not None}
    for e in history:
        lp = e.get("loop")
        if lp not in sigs:
            continue
        ev = e.get("event")
        if ev == "loop_board":
            for b, v in (e.get("board_anyaku") or {}).items():
                if v >= 2:
                    sigs[lp]["boards"].add(b)
            for n, v in (e.get("char_anyaku") or {}).items():
                if v >= 2:
                    sigs[lp]["anyaku_chars"].add(n)
        elif ev == "loop_end":
            sigs[lp]["reasons"].add(str(e.get("reason", "")))
            sigs[lp]["end_days"].add(e.get("day"))
        elif ev == "incident" and e.get("occurs"):
            sigs[lp]["incidents"].add((e.get("day"), e.get("name")))
        elif ev == "death":
            sigs[lp]["deaths"].add((e.get("day"), e.get("name")))
    for s in sigs.values():
        if "主人公の死亡" in s["reasons"]:
            s["type"] = "prot_death"
        elif s["reasons"]:
            s["type"] = "instant"
        else:
            s["type"] = "loop_end_cond"
        # 決定打の死者＝loop_end と同じ日に死んだ者（公開情報だけで絞れる）
        s["fatal_deaths"] = {n for (d, n) in s["deaths"] if d in s["end_days"]}
    return sigs


def threat_keys(label: str, cast, inc_names) -> dict:
    """脅威ラベルから公開情報の帰属キーを抽出（板／登場キャラ／事件名）。"""
    return {
        "boards": {b for b in _BOARDS if b in label},
        "chars": {n for n in cast if n and n in label},
        "incidents": {n for n in inc_names if n and n in label},
    }


def past_loss_keys(history: list[dict], loop: int | None) -> dict:
    """今ループより**前**の敗北ループから「型 → {帰属キー: 敗北ループ数}」を作る。

    帰属キー＝板（loop_end_cond）／決定打の死者（instant・prot_death）／発生した事件名。
    """
    out: dict = defaultdict(Counter)
    for lp, s in loss_signatures(history).items():
        if lp is None or lp >= (loop or 0):
            continue                      # 未来を見ない
        ty = s["type"]
        if ty == "loop_end_cond":
            for b in s["boards"]:
                out[ty][b] += 1
            for n in s["anyaku_chars"]:
                out[ty][n] += 1
        else:
            for n in s["fatal_deaths"]:
                out[ty][n] += 1
        for _d, n in s["incidents"]:
            out[ty][n] += 1
    return out


def repeat_count(threat, past_keys: dict, cast, inc_names) -> int:
    """その脅威と「同型・同じ帰属キー」で過去に何ループ負けたか（0＝反復なし）。"""
    ty = KIND_LOSS_TYPE.get(threat.kind)
    if ty is None:
        return 0
    ctr = past_keys.get(ty)
    if not ctr:
        return 0
    keys = threat_keys(threat.label, cast, inc_names)
    cand = ([ctr[b] for b in keys["boards"]] if ty == "loop_end_cond" else [])
    cand += [ctr[n] for n in keys["chars"]] + [ctr[n] for n in keys["incidents"]]
    return max(cand + [0])


# ---------------------------------------------------------------------------
# 「確度100%級」＝その負け筋に供給している役職の候補が2人以下（§10-1）
# ---------------------------------------------------------------------------
SUPPLY_MAX = 2


def supply_candidate_count(threat, roles: dict, culprits: dict,
                           view: dict) -> int | None:
    """その負け筋に供給しうる**候補の人数**。特定できなければ None（＝100%級に数えない）。

    - `KIND_SUPPLY_ROLES` が `"CULPRIT"` の型＝その事件の犯人候補集合の大きさ。
    - それ以外＝該当役職の容疑者（belief marginal ≥ _SUSPECT_P）の**和集合**の大きさ。
    """
    roles_wanted = KIND_SUPPLY_ROLES.get(threat.kind)
    if not roles_wanted:
        return None
    if roles_wanted == ("CULPRIT",):
        inc_names = {i.get("name"): i.get("day")
                     for i in view.get("incidents", []) or []}
        day = view.get("day") or 0
        best = None
        for nm, d in inc_names.items():
            if nm and nm in threat.label and d is not None and d >= day:
                if best is None or d < best:
                    best = d
        if best is None:
            return None
        cands = (culprits or {}).get(best)
        return len(cands) if cands is not None else None
    names: set = set()
    for r in roles_wanted:
        names |= set(_suspects(roles, r).keys())
    return len(names)


# ---------------------------------------------------------------------------
# 制約充足（絶対防御）＝この席で必ず打つ1手を返す
# ---------------------------------------------------------------------------
def forced_pick(agent, view: dict, options: list[dict], threats,
                *, theta: float, iron_prob: float | None,
                placed: set, seats_used: int, n_prot_placed: int = 0,
                max_seats: int = 1, max_seats_2x100: int = 3,
                seat_policy: str = "last", blocked_labels=()):
    """この席で「絶対防御」として強制する option を1つ返す（無ければ None）。

    返り値 `(option, threat, reason)`／`None`。**現行AIの点数体系には触らない**＝
    ここで None を返した席は従来どおり点数で決まる。

    placed ＝ このターンに自チームが既に置いた (card,target,target_kind) の集合
             （同じ agent インスタンスが3席を打つ＝席間協調の既存の作法と同じ）。
    n_prot_placed ＝ このターンに自チームが既に置いた枚数（0/1/2）。
    blocked_labels ＝ このループで既に規定回数だけ覆った負け筋のラベル集合
                    （★トレッドミル対策＝同じ2人きりを毎ターン作り直されて席を吸われ続ける形を切る）。

    ★`seat_policy="last"`（既定）＝**制約は後ろの席から埋める**。理由＝Phase 0 の
    「未被覆」は**3席すべてを見たあと**の判定なので、先頭席で強制すると
    「後の席が自然に折っていたはずの筋」まで奪ってしまう（＝介入率が実測より跳ね上がる）。
    許す席数 cap のぶんだけ**後ろから**開ける（cap=1 なら最終席のみ）。
    `"any"` はこのゲートを外す版（掃引用の対照）。
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
    by_key = {}
    for o in options:
        by_key.setdefault((o["card"], o["target"], o.get("target_kind")), o)

    past_keys = past_loss_keys(view.get("history", []) or [], view.get("loop"))
    cast = [c.get("name") for c in view.get("characters", []) or []]
    inc_names = {i.get("name") for i in view.get("incidents", []) or []}

    live = []          # [(prob, cost, threat, option, reason)]
    n_top = 0          # 「確度100%級」の本数（席上限の判定に使う）
    for t in threats:
        if not t.fatal or not t.defendable:
            continue
        if t.label in blocked_labels:
            continue          # ★このループで既に規定回数だけ覆った負け筋（トレッドミル対策）
        # ---- 必須の前提：未被覆（自チームが既に折っていない） ----
        keys = [(b.card, b.target, b.target_kind)
                for c in t.conditions for b in c.breaks]
        if any(k in placed for k in keys):
            continue
        # ---- 必須の前提：この席から打てて、かつ空振りでない折り手があるか ----
        cands = []
        for c in t.conditions:
            for b in c.breaks:
                o = by_key.get((b.card, b.target, b.target_kind))
                if o is None:
                    continue
                if futile_reason(view, agent, roles, b.card, b.target,
                                 b.target_kind, ctx, rumor_p):
                    continue
                cands.append((b.cost, b, o))
        if not cands:
            continue
        # ---- 発火経路（θ／鉄則①）----
        why = None
        if t.prob >= theta - 1e-9:
            why = f"θ経路（実在度{t.prob:.2f}≥{theta}）"
        elif iron_prob is not None and t.prob >= iron_prob - 1e-9:
            rep = repeat_count(t, past_keys, cast, inc_names)
            if rep >= 1:
                why = f"鉄則①（同型の敗北{rep}回・実在度{t.prob:.2f}）"
        if why is None:
            continue
        n = supply_candidate_count(t, roles, culprits, view)
        if n is not None and n <= SUPPLY_MAX:
            n_top += 1
        cands.sort(key=lambda x: x[0])
        cost, brk, opt = cands[0]
        live.append((t.prob, cost, t, opt, why, brk))
    if not live:
        return None
    cap = max_seats_2x100 if n_top >= 2 else max_seats
    if seats_used >= cap:
        return None
    if seat_policy == "last" and n_prot_placed < 3 - cap:
        return None                      # まだ後ろの席が残っている＝自然に折られる余地を残す
    live.sort(key=lambda x: (-x[0], x[1]))
    prob, _cost, t, opt, why, brk = live[0]
    return opt, t, f"{why}／折り手＝{brk.label}"
