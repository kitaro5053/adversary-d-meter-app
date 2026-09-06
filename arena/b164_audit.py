# -*- coding: utf-8 -*-
"""B-164 Phase 1：**板への暗躍禁止 Break の過大主張を数える**（★計測のみ・`agents/` 非接触）。

## 発端

`docs/バックログ_構想メモ_FableA.md` §39（B-164）＝
`docs/監査_B161_冷却が決定へ届かない_2026-08-04.md` §3-3。
`allocate` の設計3短絡で降りた12件の全部が「予定手がその CCT 自身の折り手」で、
**うち6件のカードが `暗躍禁止`**。`rules/10_action_cards.md:65`＝
「暗躍禁止は**行動解決フェイズでのみ**有効」＝事件フェイズの板打点は止まらないのに、
`agents/defense_plan._add_kinshi_break` が同じ条件に暗躍禁止 Break を入れている。

## ★免除が使える狭いスライス＝**void**

「その板に mm が今ターン札を1枚も伏せていない」＝打ち消す暗躍+が存在しない
（`rules/10_action_cards.md:61`＝重なった暗躍+しか無効化しない／`:65`＝行動解決フェイズ限定）
＝**発生の不確実性を含まない純粋な可否**。

★述語は**既に2箇所に実装済み**：
  - `agents/card_effect.noop_reason` の **G7**（B-103・`ctx.mm_boards` が要る）
    ＝B-28 の単一チョークポイント＝**これが正典**
  - `agents/b100_mix.futile_reason` の板分岐（`target not in _mm_boards_now(view)`）
∴ 本計測も**判定を書き写さない**＝`card_effect.noop_reason` を import して使う。

## 本計測が出す数（★定義を先に固定する。B-157 §1-2／B-165 §1 の流儀）

**席（seat）**＝主人公の `set_card` 決定1回で `plan_for_belief` が走った回。
**KB（kinshi board break）**＝`card=="暗躍禁止"` ∧ `target_kind=="board"` の Break。
  ★生成箇所は `agents/defense_plan.py:694,696`（`_add_kinshi_break`）**だけ**＝
  静的走査で確認できる（`sites` サブコマンド）＝この述語で漏れなく拾える。

| 記号 | 定義 |
|---|---|
| **D** | KB を1本以上持つ席 |
| **Dt** | 同・**脅威**の延べ本数（kind 別） |
| **V** | D のうち **void な KB**（mm がその板に今ターン伏せていない）を1本以上含む席 |
| **Vt** | void な KB を持つ脅威の延べ本数（kind 別） |
| **I** | D のうち、その板に**事件フェイズ由来の打点予定**がある席（`_INCIDENT_BOARD_DAMAGE`） |
| **V∩I** | void スライスと事件由来スライスの重なり |
| **P** | void な KB が `plan.picks` に入った席（＝プランナの3席のうち1つを消費した） |
| **M** | P のうち、その plan に「`defendable` なのに `uncovered`」の脅威がある席（＝押し出し） |
| **★E** | void な KB を消すと `Threat.defendable` が **False へ落ちる**脅威（★地雷1 の事前計測） |
| **★Ec** | E のうち、その脅威が **fatal** であるもの（B-100 の対象＝退行が起きうる側） |
| **G** | `b100_alloc.TRACE` で「設計3短絡」で降り、**予定手が板への暗躍禁止**だった `allocate` 呼び出し |
| **G_void** | G のうち、その板が void |

★層別＝上記の席が属する **(局, ループ)** を「そのループを落としたか」で分ける。
★**独立脚本数を必ず併記**する（130局/70局は seed 複製での水増し）。

## 二重実装をしない

- void 判定 ＝ `agents/card_effect.noop_reason`（G7）を import（`_mm_touched` は
  `agents/defense_plan._mm_touched` を import）。
- 結末・敗北ループ ＝ `arena/b145_audit._outcome` / `_lost_loops`。
- 覆った手 ＝ `agents/defense_plan._pick_for`。
- 設計3短絡の段 ＝ `agents/b100_alloc.TRACE`（B-161 が入れた**計測専用フック**・既定 None）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面実行）:
    python -m arena.b164_audit sites
    python -m arena.b164_audit verify --days 3
    python -m arena.b164_audit count  --days 3
    python -m arena.b164_audit why    --days 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.defense_plan as DP
from agents import HeuristicMastermind, HeuristicProtagonist
from agents.card_effect import NoopCtx, noop_reason
from agents.defense_plan import _mm_touched, _pick_for
from agents.heuristic_protagonist import _plan_card_class
from arena.b145_audit import _lost_loops, _outcome
from sim import run_game


# ---------------------------------------------------------------------------
# 述語（★判定は書き写さない＝単一ソースを import して使う）
# ---------------------------------------------------------------------------
def _is_kb(b) -> bool:
    """板への暗躍禁止 Break か（生成箇所は `_add_kinshi_break` の2行だけ）。"""
    return b.card == "暗躍禁止" and b.target_kind == "board"


def _void_boards(view: dict) -> frozenset:
    """今ターン **mm 札が1枚も無い板**＝暗躍禁止が一意に無効な板（`rules/10:61,65`）。

    ★判定そのものは `card_effect.noop_reason`（G7）に委ねる＝写していない。
    """
    _chars, boards = _mm_touched(view)
    ctx = NoopCtx(mm_boards=frozenset(boards))
    return frozenset(a for a in ("病院", "神社", "都市", "学校")
                     if noop_reason(view, "暗躍禁止", a, "board", ctx) is not None)


def _incident_boards(view: dict) -> frozenset:
    """今日以降の事件で**事件フェイズ由来の板打点**が乗る予定の板
    （`_INCIDENT_BOARD_DAMAGE`＝邪気の汚染→神社。`rules/40_first_steps.md`）。"""
    day_now = view.get("day", 1)
    out = set()
    for inc in view.get("incidents", []) or []:
        iday = inc.get("day")
        if iday is None or iday < day_now:
            continue
        area = DP._INCIDENT_BOARD_DAMAGE.get(inc.get("name"))
        if area:
            out.add(area)
    return frozenset(out)


def _defendable_without(t, drop) -> bool:
    """`drop` に入っている Break を消したとき、その脅威がまだ防御可能か。

    ★地雷1（B-165 の事故＝「Break を消したら脅威ごと防御不能になった」）の事前計測。
    """
    return any(any(b for b in (c.breaks or ()) if id(b) not in drop)
               for c in (t.conditions or ()))


# ---------------------------------------------------------------------------
# プローブ（★`super().decide()` の戻り値をそのまま返す＝挙動不変）
# ---------------------------------------------------------------------------
class _B164Probe(HeuristicProtagonist):

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.rows: list = []
        self.cur_view: dict | None = None

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        self.cur_view = view
        if decision != "set_card":
            return super().decide(view, decision, options)
        chosen = super().decide(view, decision, options)
        self.c["席（set_card 決定）"] += 1
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            return chosen
        threats, plan = stash
        void = _void_boards(view)
        # ★F：`allocate`（絶対防御）が void な KB を**既に**捨てているかの直接確認。
        #   `agents/b100_alloc.py:197` の `live_constraints` は折り手ごとに
        #   `agents/b100_mix.futile_reason` を呼び、その板分岐（`b100_mix.py:124`）に
        #   **同じ void 判定**が入っている＝B-164 の狭いスライスは allocate では射程ゼロ。
        try:
            from agents.b100_mix import futile_reason, noop_ctx_for, rumor_prob
            _fctx, _frp = noop_ctx_for(self, view), rumor_prob(self)
            _roles = self._belief.role_marginals()
        except Exception:
            _fctx = None
        inc_b = _incident_boards(view)
        picked = {(b.card, b.target, b.target_kind) for b in plan.picks}

        seat = Counter()
        for t in threats:
            kbs = [b for c in (t.conditions or ()) for b in (c.breaks or ())
                   if _is_kb(b)]
            if not kbs:
                continue
            kind = getattr(t, "kind", "?")
            seat["D"] = 1
            self.c[f"Dt 脅威（kind={kind}）"] += 1
            areas = {b.target for b in kbs}
            if areas & inc_b:
                seat["I"] = 1
                self.c[f"It 脅威（kind={kind}・事件由来の板）"] += 1
            vkbs = [b for b in kbs if b.target in void]
            if not vkbs:
                continue
            seat["V"] = 1
            self.c[f"Vt 脅威（kind={kind}・void な KB を含む）"] += 1
            if _fctx is not None:
                for b in vkbs:
                    r = futile_reason(view, self, _roles, b.card, b.target,
                                      b.target_kind, _fctx, _frp)
                    self.c["★F allocate が既に捨てている void KB（折り手・延べ）"
                           if r else
                           "★F' allocate が捨てていない void KB（★あれば要調査）"] += 1
            if {b.target for b in vkbs} & inc_b:
                seat["V∩I"] = 1
                self.c[f"V∩It 脅威（kind={kind}）"] += 1
            # ★E：void な KB を消すと防御不能へ落ちるか
            drop = {id(b) for b in vkbs}
            if t.defendable and not _defendable_without(t, drop):
                seat["E"] = 1
                self.c[f"Et 脅威（kind={kind}・消すと defendable が落ちる）"] += 1
                if t.fatal:
                    seat["Ec"] = 1
                    self.c[f"Ect 脅威（kind={kind}・fatal）"] += 1
            elif t.defendable:
                # ★N＝**安全に消せるスライス**＝void KB を消しても折り手が残る
                #   （＝地雷1〔B-165 の事故〕を構造的に踏まない最狭スライス）
                seat["N"] = 1
                self.c[f"Nt 脅威（kind={kind}・消しても折り手が残る）"] += 1
                rest = [b for c in t.conditions for b in (c.breaks or ())
                        if id(b) not in drop]
                if rest:
                    alt = min(rest, key=lambda b: b.cost)
                    self.c[f"　Nt 残る最安＝{alt.card}(cost {alt.cost})"] += 1
            if {(b.card, b.target, b.target_kind) for b in vkbs} & picked:
                seat["P"] = 1
                self.c[f"Pt 脅威（kind={kind}・void KB が plan.picks）"] += 1
                cov = _pick_for(plan, t)
                self.rows.append({
                    "loop": view.get("loop"), "day": view.get("day"),
                    "kind": kind, "label": str(t.label),
                    "prob": round(float(t.prob), 4),
                    "pick": (cov.card, cov.target) if cov else None,
                    "n_breaks": sum(len(c.breaks or ()) for c in t.conditions),
                })
        # ★M＝void KB が席を1つ食った結果、`defendable` なのに覆えなかった脅威が残った席。
        #   ★M2＝そのうち、押し出された脅威の最安手が**加点対象**（＝AIの決定に届く経路）
        #   であるもの。判定は `HeuristicProtagonist._plan_coeffs` と `PLAN_CLASS_KINDS`
        #   を**そのまま使う**（採点規則を書き写さない）。
        if seat.get("P"):
            pushed = [x for x in plan.uncovered if x.defendable]
            if pushed:
                seat["M"] = 1
                for x in pushed:
                    cb = x.cheapest_breaks()
                    if not cb:
                        continue
                    b = min(cb, key=lambda z: z.cost)
                    self.c[f"　M内訳 押し出された脅威＝{x.kind}／最安＝{b.card}"] += 1
                    if self._plan_coeffs(b.card) is None:
                        continue          # 加点対象外のカード種別＝決定に届かない
                    allow = self.PLAN_CLASS_KINDS.get(_plan_card_class(b.card))
                    if allow is not None and x.kind not in allow:
                        continue          # 許可した負け筋を折る手ではない＝加点しない
                    seat["M2"] = 1
                    self.c[f"　★M2 加点に届く押し出し＝{x.kind}／{b.card}"] += 1
        for k, v in seat.items():
            self.c[f"[席] {k}"] += v
        if seat:
            self.rows.append({"__seat__": True, "loop": view.get("loop"),
                              "day": view.get("day"), "flags": sorted(seat)})
        return chosen


# ---------------------------------------------------------------------------
def _games(days: int, start: int = 0, end: int | None = None):
    from arena.benchmark import benchmark_scripts
    return list(benchmark_scripts(days=days))[start:end]


def _switches(days: int, loops: int) -> str:
    from agents.heuristic_protagonist import HeuristicProtagonist as H
    return (f"B153_JUUSHA_PAIR_BREAK={DP.B153_JUUSHA_PAIR_BREAK}"
            f" / B165_PAIR_BREAK={DP.B165_PAIR_BREAK}"
            f" / B153_SUICIDE={DP.B153_SUICIDE}"
            f" / B159_MISSING_BOARD={DP.B159_MISSING_BOARD}"
            f" / B161_COOL_COST={DP.B161_COOL_COST}"
            f" / B161_UNREST_COEFF={H.B161_UNREST_COEFF}"
            f" / DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER}"
            f" / B100_MIX={H.B100_MIX} / B100_THETA={H.B100_THETA}"
            f" / days={days} loops={loops}")


def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    """★挙動不変の物証＝プローブ有無で棋譜が完全一致するか（全局）。"""
    bad, n = [], 0
    for name, seed, sc in _games(days, start, end):
        probe = replace(sc, loops=loops)
        a = _B164Probe(seed)
        sa, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": a, "p2": a, "p3": a})
        b = HeuristicProtagonist(seed)
        sb, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": b, "p2": b, "p3": b})
        ha = [(e.get("loop"), e.get("day"), e.get("event")) for e in sa.history]
        hb = [(e.get("loop"), e.get("day"), e.get("event")) for e in sb.history]
        n += 1
        if not (sa.winner == sb.winner and sa.loop_no == sb.loop_no and ha == hb):
            bad.append(f"{name} s{seed}: {_outcome(sa)} vs {_outcome(sb)}")
    return {"days": days, "n": n, "bad": bad}


def count(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    c: Counter = Counter()
    names: set = set()
    rows: list = []
    n = 0
    for name, seed, sc in _games(days, start, end):
        names.add(name)
        hp = _B164Probe(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        lost = set(_lost_loops(state))
        if getattr(state, "defeat", False):
            lost.add(state.loop_no)
        c.update(hp.c)
        c[f"結末＝{_outcome(state)}"] += 1
        for r in hp.rows:
            if not r.get("__seat__"):
                r["game"] = f"{name} s{seed}"
                rows.append(r)
                continue
            tag = "敗北ループ" if r["loop"] in lost else "防衛ループ"
            for f in r["flags"]:
                c[f"[層別] {f}／{tag}"] += 1
        n += 1
    return {"days": days, "n_games": n, "n_scripts": len(names),
            "scripts": sorted(names), "counts": dict(c), "rows": rows[:12]}


def why(days: int = 3, loops: int = 8, start: int = 0,
        end: int | None = None) -> dict:
    """★設計3短絡の予定手が**板への暗躍禁止**である呼び出しを数える（G／G_void）。

    ★`allocate` は書き写さない＝B-161 が入れた計測専用フック `b100_alloc.TRACE` を使う。
    """
    import agents.b100_alloc as ALLOC

    c: Counter = Counter()
    ex: list = []
    holder: dict = {"probe": None}

    def _hook(rec: dict) -> None:
        c[f"allocate 呼び出し／段＝{rec['stage']}"] += 1
        if not rec["stage"].startswith("設計3短絡"):
            return
        it = rec.get("intent")
        if not it:
            return
        card, target = it
        c[f"設計3短絡の予定手＝{card}"] += 1
        if card != "暗躍禁止":
            return
        if target not in ("病院", "神社", "都市", "学校"):
            c["  ↳ 予定手はキャラへの暗躍禁止（本件の対象外）"] += 1
            return
        c["★G 予定手が板への暗躍禁止"] += 1
        pv = holder["probe"].cur_view if holder["probe"] else None
        if pv is None:
            return
        void = _void_boards(pv)
        inc_b = _incident_boards(pv)
        if target in void:
            c["★G_void うちその板が void（mm札なし）"] += 1
        else:
            c["  G_live うちその板に mm 札あり（＝void ではない）"] += 1
        if target in inc_b:
            c["  G_inc うちその板に事件由来の打点予定あり"] += 1
        if len(ex) < 40:
            ex.append({"loop": rec.get("loop"), "day": rec.get("day"),
                       "board": target, "void": target in void,
                       "inc": target in inc_b,
                       "matched": (rec.get("ret") or {}).get("matched")})

    ALLOC.TRACE = _hook
    n = 0
    try:
        for _name, seed, sc in _games(days, start, end):
            hp = _B164Probe(seed)
            holder["probe"] = hp
            run_game(replace(sc, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
            n += 1
    finally:
        ALLOC.TRACE = None
        holder["probe"] = None
    return {"days": days, "n_games": n, "counts": dict(c), "examples": ex}


def sites() -> None:
    """★静的走査＝板への暗躍禁止 Break の**生成箇所を全数**印字する（規約 §3）。"""
    import inspect
    src = inspect.getsource(DP).splitlines()
    print("== `agents/defense_plan.py` の `暗躍禁止` Break 生成箇所（全数）==")
    for i, ln in enumerate(src, 1):
        s = ln.strip()
        if s.startswith("#"):
            continue
        if '"暗躍禁止"' in s and ("Break(" in s or "board" in s or "character" in s):
            print(f"  :{i:5d}  {s}")
    print("\n== `_add_kinshi_break` の呼び出し元（全数）==")
    for i, ln in enumerate(src, 1):
        if "_add_kinshi_break(" in ln and "def " not in ln:
            print(f"  :{i:5d}  {ln.strip()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sites", "verify", "count", "why"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "sites":
        sites()
        return
    print(f"== 切替口 == {_switches(a.days, a.loops)}")
    fn = {"verify": verify, "count": count, "why": why}[a.cmd]
    r = fn(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
