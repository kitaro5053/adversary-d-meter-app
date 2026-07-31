# -*- coding: utf-8 -*-
"""B-100 Phase 3：**発火面積の全数計測**（ファネル A〜H）。

起点＝ユーザーの指摘（2026-07-29）：
  「200局で5回は少なすぎる。**発動したケースと、元の手と一致していたケースの回数を
    分けて数えて**ほしい」。

`_b100_log` に残るのは **手が実際に変わった席（＝段H）**だけである。
`agents/b100_alloc.allocate` には設計3の短絡

    ikey is not None and any(ikey in c["keys"] for c in cons) -> return None

があり、**B-100 の判断と元の手が一致した席（＝段G）は 1件もログに残らない**。
本CLIはその G を含む全段を数える。

## 測り方（★挙動 bit 不変）

`HeuristicProtagonist._b100_force` を**常に None を返す観測器**に差し替え、
`B100_MIX=True` で回す。`_b100_force` が None を返す限り `decide()` の選択は
`_turn_plan` → 点数最大 の従来経路そのままなので、**軌跡は `off`（既定）と一致する**
（`--verify` で両ベンチの per-game 一致を実測できる）。

観測点は `_b100_force` の入口＝**`_turn_plan.pop(0)` の前**。よって
`allocate` が見るのと**同一の状態**（options / placed / seats_used / 計画3席分）を観測できる。

各席について「生きている制約」を1本ずつ記録し、**段D以降（θ・鉄則・席上限・
REQUIRE_PLAN・SPARE_LAST）は後段のPythonで適用する**＝1回の対局で全ゲートを掃引できる。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_funnel --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_funnel --days 5 \
        --out /tmp/f5.json
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_funnel --days 3 --verify
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.b100_alloc import gate_reason, intended_move, live_constraints
from agents.b100_mix import (SUPPLY_MAX, noop_ctx_for, rumor_prob,
                             supply_candidate_count)
from sim import run_game

#: 収集した席レコード（プロセス内グローバル＝計測専用）
_SEATS: list = []
_ERRORS: list = []


def _key(o) -> tuple:
    if isinstance(o, dict):
        return (o["card"], o["target"], o.get("target_kind"))
    return (o.card, o.target, o.target_kind)


def _observe(agent, view: dict, options: list[dict], score=None):
    """`_b100_force` の差し替え先。**常に None**＝AIの選択に一切触れない。"""
    stash = getattr(agent, "_b100_plan", None)
    if not stash:
        return None
    try:
        _record(agent, view, options, stash[0], score)
    except Exception as e:                      # 計測の失敗で対局を壊さない
        _ERRORS.append(f"{type(e).__name__}: {e}")
    return None


def _record(agent, view: dict, options: list[dict], threats, score) -> None:
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
    placed = getattr(agent, "_b100_placed", set())

    # ★DP-6：B-112 の是正フラグ（repaired_threats）は退役＝本体（defense_plan）の脅威を
    #   そのまま見る。
    stats: dict = {}
    cons = live_constraints(agent, view, options, threats, roles=roles, ctx=ctx,
                            rumor_p=rumor_p, placed=placed, stats=stats,
                            culprits=culprits)

    # ★DP-6 主指標(b)：「現在値0の板敗北が**止まらない供給を理由に**防御不能扱いされている
    #   本数」（是正後＝0 が目標）。手札に折り手が無い（席の在庫）だけの防御不能は正当＝別勘定。
    _banr = view.get("board_anyaku") or {}
    # ★B-113 主指標(a)：「このループの噂消費が belief 上証明済み」（公開情報のみ）。
    try:
        _rumor_spent = bool(agent._belief.rumor_spent_this_loop(view.get("loop")))
    except Exception:
        _rumor_spent = False
    revived: list[str] = []      # 噂消費の証明で防御可能に戻った板（この席の脅威）
    undef_b0 = undef_b0_nocard = undef_b1 = 0
    for t in threats:
        # ★B-113：噂帯（rumor_p>=0.5）×現在値1の板敗北が defendable＝残弾0の注入が
        #   無ければ gap<=0（防御不能）だった帯＝「防御可能に戻った板」を数える。
        if (t.fatal and t.defendable and getattr(t, "kind", None) == "board_defeat"
                and not getattr(t, "breached", False)
                and _rumor_spent and rumor_p >= 0.5):
            _a = next((a for a in ("病院", "神社", "都市", "学校")
                       if a in t.label), None)
            if _a is not None and int(_banr.get(_a, 0) or 0) == 1:
                revived.append(_a)
        if t.fatal and not t.defendable and getattr(t, "kind", None) == "board_defeat" \
                and not getattr(t, "breached", False):
            _area = next((a for a in ("病院", "神社", "都市", "学校")
                          if a in t.label), None)
            _cur = int(_banr.get(_area, 0) or 0) if _area else 0
            _unstop = any("噂" in (c.note or "") and "止まらない" in (c.note or "")
                          for c in t.conditions)   # 噂由来の「止まらない」主張だけを数える
            if _cur >= 1:
                undef_b1 += 1
            elif _unstop:
                undef_b0 += 1
            else:
                undef_b0_nocard += 1

    intent, from_plan = intended_move(agent, options, score)
    ikey = _key(intent) if intent is not None else None
    plan = list(getattr(agent, "_turn_plan", None) or [])
    later_keys = [_key(it) for it in plan[1:]]
    n_prot = sum(1 for p in view.get("placements", []) or []
                 if p.get("owner") != "mastermind")

    rows = []
    for c in cons:
        t = c["threat"]
        n_sup = c.get("n_supply")
        rows.append({
            "n_supply": n_sup,
            "label": t.label, "kind": t.kind, "prob": round(float(t.prob), 4),
            "repeat": int(c["repeat"]),
            "here": bool(c["here"]),
            "n_breaks": len(c["keys"]),
            # この席から打てる最安の折り手が「堅い」か（打ち消されうる＝要2席の疑い）
            "robust": (bool(c["here"][0][1].robust) if c["here"] else None),
            "n_robust_here": sum(1 for _co, b, _o in c["here"] if b.robust),
            "top": (n_sup is not None and n_sup <= SUPPLY_MAX),
            "later": any(k in c["keys"] for k in later_keys),
            "match": (ikey is not None and ikey in c["keys"]),
        })

    _SEATS.append({
        "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
        "n_prot_placed": n_prot, "has_plan": bool(plan), "plan_len": len(plan),
        "from_plan": bool(from_plan),
        "A": stats.get("A", 0), "B": stats.get("B", 0), "C": stats.get("C", 0),
        # ★B-112：段A の手前（脅威モデルが何本立てたか／致命か／防御可か）
        "T": stats.get("T", 0), "T_fatal": stats.get("T_fatal", 0),
        "T_fatal_undef": stats.get("T_fatal_undef", 0),
        "undef_board0": undef_b0, "undef_board0_nocard": undef_b0_nocard,
        "undef_board1": undef_b1,
        "b113_rumor_spent": _rumor_spent, "b113_revived": revived,
        "undef_kinds": stats.get("undef_kinds", []),
        "nonfatal_kinds": stats.get("nonfatal_kinds", []),
        "intent": (None if intent is None else
                   f"{intent['card']}→{intent['target']}"),
        "intent_score": (None if (intent is None or score is None) else
                         round(float(score(intent)), 1)),
        "cons": rows,
    })


# ---------------------------------------------------------------------------
# 対局
# ---------------------------------------------------------------------------
def play(script, seed: int, loops: int = 8):
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _log = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def script_set(days: int, scripts: str = "bench"):
    """脚本コーパスを選ぶ。`"bench"`＝標準130/70局、`"user"`＝ユーザー実戦の脚本（B-112）。"""
    if scripts == "user":
        from arena.b100_scripts import user_script_list
        return user_script_list()
    from arena.benchmark import benchmark_scripts
    return benchmark_scripts(days=days)


def collect(days: int, loops: int = 8, limit=None, verbose: bool = True,
            scripts: str = "bench") -> list:
    """観測モードで全局を回し、席レコードを返す（`(game, seed)` タグつき）。"""
    global _SEATS
    out, games = [], []
    old_mix = HeuristicProtagonist.B100_MIX
    old_force = HeuristicProtagonist._b100_force
    HeuristicProtagonist.B100_MIX = True
    HeuristicProtagonist._b100_force = _observe
    try:
        scripts = script_set(days, scripts)
        if limit:
            scripts = scripts[:limit]
        for gname, seed, sc in scripts:
            _SEATS = []
            ltw, outcome = play(sc, seed, loops=loops)
            for r in _SEATS:
                r["game"], r["seed"] = gname, seed
                r["ltw"], r["outcome"] = ltw, outcome
            out.extend(_SEATS)
            games.append({"game": gname, "seed": seed, "ltw": ltw,
                          "outcome": outcome})
            if verbose:
                print(f"  [funnel] {gname} s{seed}: {ltw} {outcome}"
                      f" 席{len(_SEATS)}", file=sys.stderr, flush=True)
    finally:
        HeuristicProtagonist.B100_MIX = old_mix
        HeuristicProtagonist._b100_force = old_force
    return out, games


# ---------------------------------------------------------------------------
# ファネルの集計（★段D以降は後段で適用＝1回の対局で全ゲートを掃引）
# ---------------------------------------------------------------------------
def funnel(seats: list, *, theta: float = 1.0, iron_prob: float | None = None,
           force_gate: str = "theta", max_seats: int = 2,
           max_seats_2x100: int = 3, spare_last: bool = True,
           require_plan: bool = False, noplan_last: bool = True,
           supply_gate: bool = False, match_gated: bool = False,
           use_later: bool = True) -> dict:
    """段A〜H を数える。ゲート群は引数で切り替えられる。

    ★既定は **main のクラス既定**（Phase 3 land 後＝θ1.0・PLAN不要・NOPLAN_LAST・席2）。
    ★`seats_used`（席上限の消費）は「この shadow 走行で発火したはずの席」を
    ターン単位で数えて再現する（off 軌跡上の推定＝上界。§注意）。

    `supply_gate=True`＝★B-112 の第2軸。ユーザー裁定（Phase 0 §10-1）の
    「確度100%級＝その負け筋に供給している役職の候補が2人以下」を**θとは独立の
    発火経路**として持つ（`row["top"]`＝`supply_candidate_count() <= SUPPLY_MAX`）。
    """
    def _gate(row) -> str | None:
        c = {"threat": _T(row), "repeat": row["repeat"]}
        w = gate_reason(c, theta=theta, iron_prob=iron_prob,
                        force_gate=force_gate)
        if w is None and supply_gate and row.get("top"):
            return "供給候補≤2"
        return w

    n = defaultdict(int)
    fired_by_turn: dict = defaultdict(int)
    fired_rows, g_rows = [], []
    per_turn: dict = defaultdict(list)

    for s in seats:
        tkey = (s["game"], s["seed"], s["loop"], s["day"])
        per_turn[tkey].append(s)
        n["seats"] += 1
        # ---- ★B-112：段A の手前（脅威モデルの産出量そのもの） ----
        n["tT"] += s.get("T", 0)
        n["tT_fatal"] += s.get("T_fatal", 0)
        n["tT_fatal_undef"] += s.get("T_fatal_undef", 0)
        if s.get("T"):
            n["S_T"] += 1
        if s.get("T_fatal"):
            n["S_fatal"] += 1
        # ---- 脅威**本数**でも数える（席単位は「1本でもあれば1」なので減りが見えない） ----
        n["tA"] += s["A"]
        n["tB"] += s["B"]
        n["tC"] += s["C"]
        n["tC_here"] += sum(1 for c in s["cons"] if c["here"])
        n["tD"] += sum(1 for c in s["cons"] if _gate(c))
        n["tD_here"] += sum(1 for c in s["cons"] if c["here"] and _gate(c))
        if s["A"]:
            n["A"] += 1
        if s["B"]:
            n["B"] += 1
        cons = s["cons"]
        if not cons:
            continue
        if any(c["here"] for c in cons):
            n["C"] += 1
        else:
            continue                      # この席からは1本も打てない

        # ---- 段G＝設計3の短絡（この席の予定手が既にどれかの制約を折っている） ----
        matched = [c for c in cons if c["match"]]
        if matched:
            n["G"] += 1
            _mg = any(_gate(c) for c in matched)
            if _mg:
                n["G_gate"] += 1          # ★資格を持つ制約と一致＝「同じ結論に達していた」
            # ★G で捨てられた席のうち、**別の**資格つき制約が未被覆で残っていた数
            #   （＝設計3の短絡が「別の負け筋を覆う機会」を潰している件数）
            if any(_gate(c) and c["here"] and not c["match"] for c in cons):
                n["G_lost_other"] += 1
            g_rows.append((s, matched))
            # ★B-112：`match_gated`＝資格つき制約を折っている席だけ守る
            if not match_gated or _mg:
                continue

        # ---- 後続席の予定手が折る制約を外す（`_turn_plan` があるときのみ既知） ----
        open_ = [c for c in cons if not (use_later and c["later"])]
        if not open_:
            n["B2_later"] += 1
            continue

        elig = [c for c in open_ if _gate(c)]
        if not elig:
            continue
        n["D"] += 1                        # 発火資格を得た席
        if not any(c["here"] for c in elig):
            n["D_nohere"] += 1             # 資格はあるがこの席からは打てない
            continue
        n["D_pay"] += 1

        # ---- 段E＝REQUIRE_PLAN ----
        if require_plan and not s["has_plan"]:
            n["E"] += 1
            continue
        # ---- 段F＝席上限（同一ターンで既に発火した席数） ----
        cap = max_seats_2x100 if sum(1 for c in elig if c["top"]) >= 2 else max_seats
        if fired_by_turn[tkey] >= cap:
            n["F"] += 1
            continue
        # ---- 設計4＝最終席から奪わない（★NOPLAN_LAST は本番と同じ分岐にする） ----
        if noplan_last and not s["has_plan"]:
            if (3 - s["n_prot_placed"]) > 1:
                n["noplan_last"] += 1
                continue
        elif spare_last and (3 - s["n_prot_placed"]) <= 1:
            n["spare_last"] += 1
            continue
        n["H"] += 1
        fired_by_turn[tkey] += 1
        fired_rows.append((s, elig))

    n["turns"] = len(per_turn)
    return {"n": dict(n), "fired": fired_rows, "matched": g_rows,
            "per_turn": per_turn}


class _T:
    """`gate_reason` に渡すための最小の脅威ビュー（label/prob だけ使われる）。"""
    __slots__ = ("prob", "label")

    def __init__(self, row):
        self.prob = row["prob"]
        self.label = row["label"]


# ---------------------------------------------------------------------------
# 「複数の負け筋」「1つの負け筋に2席」の実態
# ---------------------------------------------------------------------------
def multiplicity(seats: list, *, theta: float = 1.0) -> dict:
    """ターン単位で「同時に立っている負け筋の本数」を数える。

    ターンの代表値＝そのターンの**先頭席**（まだ誰も置いていない＝被覆前）の制約本数。
    """
    first: dict = {}
    for s in seats:
        tkey = (s["game"], s["seed"], s["loop"], s["day"])
        cur = first.get(tkey)
        if cur is None or s["n_prot_placed"] < cur["n_prot_placed"]:
            first[tkey] = s
    hist_live, hist_gate, hist_pay = Counter(), Counter(), Counter()
    weak = 0                 # 折り手が全て非robust＝1席では担保しきれない疑い
    weak_labels, weak_gate = Counter(), 0
    n_gate_cons = 0
    for s in first.values():
        cons = s["cons"]
        hist_live[len(cons)] += 1
        hist_gate[sum(1 for c in cons if c["prob"] >= theta - 1e-9)] += 1
        hist_pay[sum(1 for c in cons if c["here"])] += 1
        for c in cons:
            if c["here"] and c["n_robust_here"] == 0:
                weak += 1
                weak_labels[c["kind"]] += 1
                if c["prob"] >= theta - 1e-9:
                    weak_gate += 1
            if c["here"] and c["prob"] >= theta - 1e-9:
                n_gate_cons += 1
    return {"n_turns": len(first),
            "live": dict(sorted(hist_live.items())),
            "gate": dict(sorted(hist_gate.items())),
            "payable": dict(sorted(hist_pay.items())),
            "weak_breaks": weak, "weak_kinds": dict(weak_labels.most_common()),
            "gate_cons": n_gate_cons, "weak_gate": weak_gate}


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
_GATES = [
    ("既定（main＝θ1.0・PLAN不要・NL・席2）", dict()),
    # ★B-112 Phase 4：資格ゲート（θ）の掃引＝ファネルの最大のボトルネックの検証
    ("θ=0.9", dict(theta=0.9)),
    ("θ=0.8", dict(theta=0.8)),
    ("θ=0.67", dict(theta=0.67)),
    ("θ=0.5", dict(theta=0.5)),
    ("θ=0.34", dict(theta=0.34)),
    ("gate=all（全fatal×defendable）", dict(force_gate="all")),
    # ★第2軸＝供給候補≤2（ユーザー裁定 §10-1）
    ("供給候補≤2（θ1.0と併用）", dict(supply_gate=True)),
    ("θ=0.67＋供給候補≤2", dict(theta=0.67, supply_gate=True)),
    # 席・最終席まわり（θを動かした上での効き目）
    ("θ=0.67＋席3", dict(theta=0.67, max_seats=3)),
    ("θ=0.67＋NL解除", dict(theta=0.67, noplan_last=False)),
    ("θ=0.67＋NL解除＋最終席も", dict(theta=0.67, noplan_last=False,
                                      spare_last=False)),
    ("鉄則0.67", dict(iron_prob=0.67)),
    ("鉄則0.5", dict(iron_prob=0.5)),
    # ★設計3の短絡を資格つきだけに絞る（G_lost_other の回収）
    ("段Gを資格つきだけに絞る", dict(match_gated=True)),
    ("同＋θ=0.9", dict(match_gated=True, theta=0.9)),
    ("同＋θ=0.67", dict(match_gated=True, theta=0.67)),
]


def report(seats: list, games: list, days: int) -> str:
    L = [f"B-100 ファネル（Phase 3／B-112 拡張）: {days}日級 {len(games)}局 / 席 {len(seats)}"
         f" / ターン {len({(s['game'], s['seed'], s['loop'], s['day']) for s in seats})}"
         f" ／PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}",
         f"  防衛 {sum(1 for g in games if g['outcome'] == 'defense')}"
         f" 平均 {round(sum(g['ltw'] for g in games) / max(1, len(games)), 3)}",
         ""]
    base = funnel(seats)
    b = base["n"]
    und = Counter(k for s in seats for k in s.get("undef_kinds", []))
    nfk = Counter(k for s in seats for k in s.get("nonfatal_kinds", []))
    L += ["  ── ★段A の手前（脅威モデルの産出量）＝B-112 で追加 ──",
          f"    脅威の総数（席×脅威ののべ）              : {b.get('tT', 0)}"
          f"（脅威が1本でも立った席 {b.get('S_T', 0)} / {b.get('seats', 0)}）",
          f"    うち fatal                                : {b.get('tT_fatal', 0)}"
          f"（fatal が立った席 {b.get('S_fatal', 0)}）",
          f"    うち fatal だが**防御不能**（defendable=False）: {b.get('tT_fatal_undef', 0)}"
          f"（T_u率 {100 * b.get('tT_fatal_undef', 0) / max(1, b.get('tT_fatal', 1)):.1f}%）",
          f"      防御不能の種別: {dict(und.most_common(8))}",
          f"      ★DP-6 主指標(b)＝現在値0の板敗北が**噂由来で**防御不能扱い: "
          f"{sum(s.get('undef_board0', 0) for s in seats)}"
          f"（現在値0・手札切れ等の正当理由: "
          f"{sum(s.get('undef_board0_nocard', 0) for s in seats)}"
          f"／現在値1以上: {sum(s.get('undef_board1', 0) for s in seats)}）",
          f"      ★B-113 主指標(a)＝噂消費が確定して防御可能に戻った板×ターン: "
          f"{len({(s['game'], s['seed'], s['loop'], s['day'], a) for s in seats for a in s.get('b113_revived', ())})}"
          f"（噂消費の証明が立ったターン: "
          f"{len({(s['game'], s['seed'], s['loop'], s['day']) for s in seats if s.get('b113_rumor_spent')})}"
          f" / {len({(s['game'], s['seed'], s['loop'], s['day']) for s in seats})}）",
          f"      非fatal の種別: {dict(nfk.most_common(8))}",
          "",
          "  ── 段A〜H（既定ゲート＝main のクラス既定：θ1.0・PLAN不要・NOPLAN_LAST・席2） ──",
          "    ［席単位］（その席に1本でも該当があれば1と数える）",
          f"    A 致命×防御可の脅威が立った席            : {b.get('A', 0)}",
          f"    B  うち未被覆                             : {b.get('B', 0)}",
          f"    C   うちこの席から空振りでない折り手が打てる: {b.get('C', 0)}",
          f"    G    ★うち予定手が既にその制約を折っている（一致）: {b.get('G', 0)}"
          f"（うち資格つき制約と一致 {b.get('G_gate', 0)}"
          f"／別の資格つき制約を取り残した {b.get('G_lost_other', 0)}）",
          f"    -    うち後続席の予定手が折る                : {b.get('B2_later', 0)}",
          f"    D    うち発火資格を得た（θ／鉄則）          : {b.get('D', 0)}"
          f"（うちこの席から打てる {b.get('D_pay', 0)}）",
          f"    E     うち REQUIRE_PLAN で落ちた           : {b.get('E', 0)}",
          f"    F     うち MAX_SEATS で落ちた              : {b.get('F', 0)}",
          f"    -     うち SPARE_LAST で落ちた             : {b.get('spare_last', 0)}",
          f"    -     うち NOPLAN_LAST で落ちた            : {b.get('noplan_last', 0)}",
          f"    H     ★実際に手が変わった席                : {b.get('H', 0)}",
          "    ［脅威**本数**単位］（席×制約でのべ数える＝席単位では見えない減りを出す）",
          f"    A 致命×防御可                             : {b.get('tA', 0)}",
          f"    B  うち未被覆（自チームの既置で折れていない）: {b.get('tB', 0)}",
          f"    C   うち空振りでない折り手がある            : {b.get('tC', 0)}"
          f"（うちこの席から打てる {b.get('tC_here', 0)}）",
          f"    D    うち発火資格（θ／鉄則）              : {b.get('tD', 0)}"
          f"（うちこの席から打てる {b.get('tD_here', 0)}）",
          ""]
    kinds_g = Counter(c["kind"] for _s, m in base["matched"] for c in m)
    L += [f"    G の内訳（脅威種別・のべ）: {dict(kinds_g.most_common(8))}",
          f"    H の局: {sorted({(s['game'], s['seed']) for s, _e in base['fired']})}",
          ""]
    L += ["  ── 門を緩めた時の H（shadow 上の推定＝off 軌跡） ──",
          "    設定                                   |   G'|   D |   E |   F | 最終席 |  NL |   H",
          "    ---------------------------------------|-----|-----|-----|-----|--------|-----|-----"]
    for name, kw in _GATES:
        f = funnel(seats, **kw)["n"]
        L.append(f"    {name:<38} | {f.get('G_gate', 0):>3} |"
                 f" {f.get('D', 0):>3} | {f.get('E', 0):>3} |"
                 f" {f.get('F', 0):>3} | {f.get('spare_last', 0):>6} |"
                 f" {f.get('noplan_last', 0):>3} | {f.get('H', 0):>3}")
    m = multiplicity(seats)
    tot = max(1, m["n_turns"])
    L += ["", "  ── 同時に立っている負け筋（ターン先頭席で数える） ──",
          f"    ターン数 {m['n_turns']}",
          f"    生きた制約の本数の分布      : {m['live']}",
          f"      2本以上 : {sum(v for k, v in m['live'].items() if k >= 2)}"
          f" ({100 * sum(v for k, v in m['live'].items() if k >= 2) / tot:.1f}%)",
          f"    うち θ=1.0 の本数の分布     : {m['gate']}",
          f"    うちその席から打てる本数    : {m['payable']}",
          f"    ★折り手が全て非robust（＝同ターンで打ち消されうる＝1席では担保しきれない）"
          f"制約: {m['weak_breaks']} 種別={m['weak_kinds']}",
          f"      うち θ=1.0 の制約: {m['weak_gate']} / θ=1.0 かつ打てる制約 {m['gate_cons']}"]
    if _ERRORS:
        L += ["", f"  ⚠ 計測エラー {len(_ERRORS)}件: {Counter(_ERRORS).most_common(3)}"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-100：発火面積の全数計測（Phase 3／B-112）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="観測モードの軌跡が既定OFFと per-game 一致するかを実測する")
    ap.add_argument("--scripts", type=str, default="bench",
                    choices=["bench", "user"],
                    help="bench＝標準130/70局／user＝ユーザー実戦の脚本（B-112）")
    ap.add_argument("--legacy-supply", action="store_true",
                    help="★DP-6 ablation：旧 truly_unstoppable（噂在＝防御不能）で数える"
                         "（B-112 の --repair/--repair-prob は本体修正に伴い退役）")
    args = ap.parse_args(argv)
    if args.legacy_supply:
        import agents.defense_plan as _dp
        _dp.DP6_SUPPLY_LEDGER = False
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    t0 = time.time()
    seats, games = collect(args.days, loops=args.loops, limit=args.limit,
                           verbose=not args.quiet, scripts=args.scripts)
    print(report(seats, games, args.days))
    if args.verify:
        from arena.b100_shadow import run_config
        off = run_config("off", args.days, loops=args.loops, limit=args.limit,
                         verbose=False, scripts=args.scripts)
        omap = {(r["game"], r["seed"]): r["ltw"] for r in off["rows"]}
        diff = [f"{g['game']} s{g['seed']} {omap.get((g['game'], g['seed']))}"
                f"→{g['ltw']}" for g in games
                if omap.get((g["game"], g["seed"])) != g["ltw"]]
        print(f"  [verify] off 防衛{off['defense']} 平均{off['mean']}"
              f" / 観測モードとの per-game 差分 {len(diff)}件: {diff or '—'}")
    print(f"  所要 {round(time.time() - t0, 1)}秒")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"days": args.days, "games": games, "seats": seats},
                      f, ensure_ascii=False)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
