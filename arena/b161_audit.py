# -*- coding: utf-8 -*-
"""B-161 Phase 1：**`board_defeat` の犯人冷却が決定へ届かない2段の門を数える**（★計測のみ）。

## 発端

`docs/バックログ_構想メモ_FableA.md` §37（B-161）＝`docs/監査_B160_..._2026-08-04.md` §8 の実測
「B-10（`agents/defense_plan._add_incident_board_cooling`）は両ベンチで一度も
AI の決定に影響していない」（Break 生成 4/36 → plan採用 3/28 → **加点 0・絶対防御 0**）。

## 2段の門（現物・行番号は本レーン基点 `2e0d6e5`）

| 門 | 実装 | 何が起きるか |
|---|---|---|
| **(α) 加点経路** | `heuristic_protagonist.PLAN_CLASS_COEFFS` に「不安」種別が無い→`_plan_coeffs` が None→`_defense_plan_recs` の `continue` | `plan.picks` に入っても**素点が1点も動かない** |
| **(β) 最安が採られる** | `defense_plan.Threat.cheapest_breaks`（条件ごとに `min(cost)` を1本）／`b100_alloc.py:230` `here.sort(key=(cost,card,target))` | 冷却 1.5 は**同じ条件に並ぶ**暗躍禁止 1.0 に必ず負ける |

★**(β) は `cheapest_breaks` の段で効く**＝`board_defeat` の条件は1本（`c1`）なので、
その条件に暗躍禁止 Break があると冷却 Break は **`plan.picks` の候補にすら入らない**。

★**(γ)＝`truly_unstoppable` 限定**は**構造的にほぼ空振り**である見込み＝
`_threat_board_defeat` は `truly_unstoppable` のとき `_add_kinshi_break` を**呼ばない**
（`if truly_unstoppable: … elif cultists_here: … else: _add_kinshi_break`）＝
その板では冷却が**唯一の Break**＝既に最安。∴ (γ) はコストの門を動かさない。
本計測は `(a2内訳)` で「代わりに覆った手」を印字してこれを実証する。

## 本計測が出す数（★定義を先に固定する）

**席（seat）** ＝ 主人公の `set_card` 決定1回。
**CCT（cooling-carrying board threat）** ＝ その席の脅威リストにある `kind=="board_defeat"` の
`Threat` で、`_add_incident_board_cooling` が作った冷却 Break（`card=="不安-1"` かつ
label に「を不安-1で冷やし」を含む）を**1本以上持つ**もの。source は label 頭で
「邪気の汚染」（B-10）／「行方不明」（B-159・切替口 ON のときだけ生成）に分ける。
★1席に複数の CCT があっても**席は1回だけ数える**（source ごとに独立に数える）。

| 段 | 定義 |
|---|---|
| (a) 生成 | CCT が1本以上ある席 |
| **(a2) ★(β)の射程** | 冷却 Break が `cheapest_breaks` に**入っていない**席（＝同条件の安い折り手に負けた） |
| (a3) | 冷却が `cheapest_breaks` に残った席 |
| (b) plan採用 | 冷却 Break が `plan.picks` に入った席 |
| (c) ★加点 | 冷却 Break のキーが `_plan_recs` にある席 |
| (e) ★絶対防御が冷却を強制 | `_b100_log` の新規エントリの (card,target) が冷却 Break |
| **(e2) ★(β)が届きうる席** | `_b100_log` の新規エントリの脅威 label が CCT と一致し、強制手が冷却**でない**席 |
| (d) 実際に打たれた | その席の決定手が冷却 Break（★別の理由＝偶然でも数える） |
| (g) θ を通る CCT | `t.prob >= B100_THETA`（`b100_alloc.gate_reason` のθ経路と同じ述語） |

★**「動く席」と「守れる席」は別物**（B-160 の流儀）。本計測が言えるのは**席数の上限**まで。
実際に敗北条件の成立を防いだかは `ab` の per-game 差分でしか言えない。

## 二重実装をしない

- 「どの Break がその脅威を覆ったか」は `agents/defense_plan._pick_for`（表示層と同じ単一ソース）。
- 結末の判定は `arena/b145_audit._outcome` を import（B-157/B-159/B-160 と同じ）。
- θ経路の述語は `b100_alloc.gate_reason` と同一の式（`prob >= theta - 1e-9`）を使う。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b161_audit verify  --days 3 --end 8
    python -m arena.b161_audit funnel  --days 3
    python -m arena.b161_audit funnel  --days 5 --b159
    python -m arena.b161_audit ab      --days 3 --alpha 8,88,0.7,1.0 --beta 0.5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.defense_plan as DP
from agents import HeuristicMastermind, HeuristicProtagonist
from agents.defense_plan import _pick_for
from arena.b145_audit import _outcome
from sim import run_game

#: 冷却 Break の識別（`_add_incident_board_cooling` の label_fmt）。
_COOL_MARK = "を不安-1で冷やし"


def _cool_breaks(t) -> list:
    """脅威 t が持つ「事件由来の板打点を止める犯人冷却」Break（0本以上）。"""
    out = []
    for cd in (t.conditions or ()):
        for b in (cd.breaks or ()):
            if b.card == "不安-1" and _COOL_MARK in str(b.label):
                out.append(b)
    return out


def _source_of(b) -> str:
    return "行方不明" if str(b.label).startswith("行方不明") else "邪気の汚染"


def _bkey(b) -> tuple:
    return (b.card, b.target, b.target_kind)


class _FunnelProbe(HeuristicProtagonist):
    """CCT の (a)(a2)(a3)(b)(c)(d)(e)(e2)(g) を数える。

    ★`super().decide()` の戻り値をそのまま返す＝**挙動不変**（B-151 `_Probe` と同じ作法）。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.probs: list = []
        self.rows: list = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        n_log = len(getattr(self, "_b100_log", []) or [])
        chosen = super().decide(view, decision, options)
        self.c["席（set_card 決定）"] += 1
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            return chosen
        threats, plan = stash
        ccts = [(t, cb) for t in threats
                if getattr(t, "kind", "") == "board_defeat" and (cb := _cool_breaks(t))]
        if not ccts:
            return chosen
        picked = {_bkey(b) for b in plan.picks}
        recs = set((getattr(self, "_plan_recs", None) or {}).keys())
        new_log = (getattr(self, "_b100_log", []) or [])[n_log:]
        forced_keys = {(e.get("card"), e.get("target"), e.get("target_kind"))
                       for e in new_log}
        forced_labels = {str(e.get("label")) for e in new_log}
        ckey = (chosen.get("card"), chosen.get("target"), chosen.get("target_kind"))
        theta = self.B100_THETA
        seen: set = set()

        def _hit(src: str, name: str) -> None:
            if (src, name) in seen:
                return
            seen.add((src, name))
            self.c[f"[{src}] {name}"] += 1

        for t, cb in ccts:
            src = _source_of(cb[0])
            keys = {_bkey(b) for b in cb}
            cheap = {_bkey(b) for b in t.cheapest_breaks()}
            covering = _pick_for(plan, t)
            _hit(src, "(a) 冷却 Break が生成された席")
            self.probs.append((src, round(float(t.prob), 4)))
            if t.prob >= theta - 1e-9:
                _hit(src, "(g) θ経路を通る実在度の CCT がある席")
            if keys & cheap:
                _hit(src, "(a3) 冷却が cheapest_breaks に残った席")
            else:
                _hit(src, "(a2) ★(β)の射程＝冷却が最安に負けて候補から落ちた席")
                if covering is not None:
                    self.c[f"[{src}] (a2内訳) 代わりに覆った手＝{covering.card}"] += 1
            if keys & picked:
                _hit(src, "(b) plan.picks に入った席")
            if keys & recs:
                _hit(src, "(c) ★加点された席")
            if keys & forced_keys:
                _hit(src, "(e) ★絶対防御が冷却を強制した席")
            elif str(t.label) in forced_labels:
                _hit(src, "(e2) ★(β)が届きうる席＝同じ脅威で別の折り手を強制した席")
            if ckey in keys:
                _hit(src, "(d) 実際にその手が打たれた席")
                self.rows.append({"loop": view.get("loop"), "day": view.get("day"),
                                  "src": src, "card": ckey[0], "target": ckey[1],
                                  "prob": round(float(t.prob), 4)})
        return chosen


# ---------------------------------------------------------------------------
# ★why：θ を通した CCT が **どの段で** 絶対防御に採られなかったのかを数える
#   （`b100_alloc.TRACE`＝計測専用フックを使う＝`allocate` を書き写さない）
# ---------------------------------------------------------------------------
def why(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        b159: bool = False) -> dict:
    import agents.b100_alloc as ALLOC
    from arena.benchmark import benchmark_scripts

    if b159:
        DP.B159_MISSING_BOARD = True
    c: Counter = Counter()
    ex: list = []

    def _hook(rec: dict) -> None:
        ccts = [x for x in rec["cons"]
                if x["kind"] == "board_defeat"
                and any(_COOL_MARK in lb for _cd, _co, lb in x["breaks"])]
        if not ccts:
            return
        c["CCT を含む allocate 呼び出し"] += 1
        c[f"  降りた段＝{rec['stage']}"] += 1
        gated = [x for x in ccts if x["gate"]]
        if not gated:
            c["  ★CCT が資格ゼロ（θ経路を通らない）"] += 1
            return
        c["★CCT が資格を持っていた呼び出し"] += 1
        c[f"  ★資格つき CCT の降り段＝{rec['stage']}"] += 1
        x = gated[0]
        c[f"  ★資格つき CCT の here 先頭＝{x['here0']}"] += 1
        _it = rec.get("intent")
        if rec["stage"].startswith("設計3短絡"):
            _iskey = _it and any(_it[0] == cd and _it[1] in str(lb)
                                 for cd, _co, lb in x["breaks"])
            c["    ↳ 予定手が★この CCT 自身の折り手" if _iskey
              else "    ↳ 予定手は別の制約の折り手"] += 1
            c[f"    ↳ 予定手のカード＝{_it[0] if _it else None}"] += 1
        if rec["stage"] == "強制した" and rec.get("ret"):
            same = rec["ret"]["label"] == x["label"]
            c[("  ★強制されたのは CCT 本人" if same
               else "  ★強制されたのは別の脅威（優先順位で負けた）")] += 1
            if not same and len(ex) < 40:
                ex.append({"loop": rec["loop"], "day": rec["day"],
                           "cct": x["label"], "cct_prob": round(x["prob"], 4),
                           "won": rec["ret"]["label"],
                           "won_card": rec["ret"]["card"]})
        elif len(ex) < 40:
            ex.append({"loop": rec["loop"], "day": rec["day"],
                       "cct": x["label"], "cct_prob": round(x["prob"], 4),
                       "won": f"（{rec['stage']}）", "won_card": None})

    ALLOC.TRACE = _hook
    n = 0
    try:
        for _name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
            hp = HeuristicProtagonist(seed)
            run_game(replace(sc, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
            n += 1
    finally:
        ALLOC.TRACE = None
        DP.B159_MISSING_BOARD = False
    return {"days": days, "n_games": n, "counts": dict(c), "examples": ex}


def _switches(days: int) -> str:
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    return (f"B161_COOL_COST={DP.B161_COOL_COST} / "
            f"B161_UNREST_COEFF={HP.B161_UNREST_COEFF} / "
            f"B161_UNREST_KINDS={HP.B161_UNREST_KINDS} / "
            f"B159_MISSING_BOARD={DP.B159_MISSING_BOARD} / "
            f"B159_MISSING_COST={DP.B159_MISSING_COST} / "
            f"DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER} / "
            f"B100_MIX={HP.B100_MIX} / B100_THETA={HP.B100_THETA} / "
            f"B100_IRON_PROB={HP.B100_IRON_PROB} / "
            f"B141B_UNLOCK_SAME_DAY={HP.B141B_UNLOCK_SAME_DAY} / days={days}")


def funnel(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    probs: list = []
    rows: list = []
    names: set = set()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp = _FunnelProbe(seed)
        run_game(replace(sc, loops=loops),
                 {"mastermind": HeuristicMastermind(seed),
                  "p1": hp, "p2": hp, "p3": hp})
        c.update(hp.c)
        probs.extend(hp.probs)
        for r in hp.rows:
            rows.append({"script": name, "seed": seed, **r})
        names.add(name)
        n += 1
    pv = {}
    for src in ("邪気の汚染", "行方不明"):
        v = sorted(p for s, p in probs if s == src)
        pv[src] = {"n": len(v), "min": (v[0] if v else None),
                   "max": (v[-1] if v else None),
                   "ge_theta": sum(1 for x in v if x >= 0.9 - 1e-9)}
    return {"days": days, "n_games": n, "n_scripts": len(names),
            "scripts": sorted(names), "counts": dict(c), "probs": pv,
            "rows": rows[:60]}


# ---------------------------------------------------------------------------
# A/B（門を開けたら棋譜が動くか）＝**per-game 差分の全数**
# ---------------------------------------------------------------------------
def _one(script, seed: int, loops: int) -> tuple:
    hp = HeuristicProtagonist(seed)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return _outcome(st), st.loop_no, trace


def _parse_config(spec: str) -> tuple:
    """'a:8,88,0.7,1.0|b:0.5' → (alpha_tuple|None, beta_float|None)。'off' → (None, None)。"""
    alpha = beta = None
    if spec.strip().lower() in ("off", ""):
        return None, None
    for part in spec.split("|"):
        part = part.strip()
        if part.startswith("a:"):
            alpha = tuple(float(x) for x in part[2:].split(","))
        elif part.startswith("b:"):
            beta = float(part[2:])
        else:
            raise SystemExit(f"不正な config: {part}")
    return alpha, beta


def ab(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
       configs=("off",), b159: bool = False) -> dict:
    """off（現行）を**1回だけ実測**し、各 config を同一プロセス・同一コミットで連続比較。

    ★`off` は必ず先頭で毎回実測する（正典値の引用をベースライン扱いしない＝規約 §4）。
    """
    from arena.benchmark import benchmark_scripts

    games = list(benchmark_scripts(days=days))[start:end]

    def _run(alpha, beta, use_b159, tag):
        DP.B161_COOL_COST = beta
        HeuristicProtagonist.B161_UNREST_COEFF = alpha
        DP.B159_MISSING_BOARD = bool(use_b159)
        print(f"  [切替口:{tag}] {_switches(days)}", flush=True)
        return {f"{name}#{seed}": _one(sc, seed, loops) for name, seed, sc in games}

    base = _run(None, None, False, "off")
    rows = []
    for spec in configs:
        alpha, beta = _parse_config(spec)
        if alpha is None and beta is None and not b159:
            continue                       # 'off' は既に測った
        cur = _run(alpha, beta, b159, spec)
        flips, moved = [], []
        for k in base:
            o, n = base[k], cur[k]
            if o[2] != n[2]:
                moved.append(k)          # ★棋譜（手順）が動いた局＝発火の物証
            if (o[0], o[1]) != (n[0], n[1]):
                flips.append({"game": k, "off": [o[0], o[1]], "on": [n[0], n[1]]})
        rows.append({"config": spec, "agg": _agg(cur),
                     "trace_moved": len(moved), "moved": moved, "flips": flips})
    DP.B161_COOL_COST = None
    HeuristicProtagonist.B161_UNREST_COEFF = None
    DP.B159_MISSING_BOARD = False
    return {"days": days, "b159": bool(b159), "off": _agg(base), "rows": rows}


def _agg(d) -> dict:
    """★`_outcome`（`arena/b145_audit.py`）の語彙＝defense / fb_win / fb_loss / loss。
    規約 §5＝**`fb_win` は防衛に数えない**（ループを1つも守れていない扱い）。
    """
    from collections import Counter as _C
    k = _C(v[0] for v in d.values())
    return {"防衛(defense)": k.get("defense", 0), "fb_win": k.get("fb_win", 0),
            "fb_loss": k.get("fb_loss", 0), "loss": k.get("loss", 0),
            "n": len(d),
            "平均ループ": round(sum(v[1] for v in d.values()) / max(1, len(d)), 3)}


# ---------------------------------------------------------------------------
def _verify_game(script, seed: int, loops: int = 8) -> bool:
    """プローブ有無で棋譜・結末・ループ数が完全一致するか（挙動不変の物証）。"""
    probe = replace(script, loops=loops)
    base = HeuristicProtagonist(seed)
    s0, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": base, "p2": base, "p3": base})
    hp = _FunnelProbe(seed)
    s1, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
    k = [[(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
          for e in s.history] for s in (s0, s1)]
    return k[0] == k[1] and _outcome(s0) == _outcome(s1) and s0.loop_no == s1.loop_no


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-161 Phase 1 計測")
    ap.add_argument("cmd", choices=("funnel", "verify", "ab", "why"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--b159", action="store_true",
                    help="B159_MISSING_BOARD=True（行方不明の冷却 Break も生成する）")
    ap.add_argument("--alpha", default=None,
                    help="(α) B161_UNREST_COEFF＝'bonus,hot,hot_p,weak'")
    ap.add_argument("--beta", type=float, default=None,
                    help="(β) B161_COOL_COST＝冷却 Break のコスト")
    ap.add_argument("--configs", default=None,
                    help="ab の掃引＝';' 区切り（例 'a:8,88,0.0,1.0;a:8,88,0.0,1.0|b:0.5'）")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    alpha = tuple(float(x) for x in a.alpha.split(",")) if a.alpha else None
    if a.cmd == "ab":
        cfgs = ([s for s in a.configs.split(";") if s.strip()] if a.configs
                else [("a:" + a.alpha if a.alpha else "")
                      + ("|b:%g" % a.beta if a.beta is not None else "") or "off"])
        r = ab(days=a.days, loops=a.loops, start=a.start, end=a.end,
               configs=cfgs, b159=a.b159)
        print(f"=== B-161 A/B（{a.days}日級・B159_MISSING_BOARD={r['b159']}）===")
        print(f"  off（実測ベースライン） {r['off']}")
        for row in r["rows"]:
            print(f"  --- config = {row['config']}")
            print(f"      {row['agg']}")
            print(f"      ★棋譜が動いた局 = {row['trace_moved']} {row.get('moved') or ''}"
                  f" ／ ★per-game flip = {len(row['flips'])} 件")
            for f in row["flips"]:
                print(f"        {f['game']}: {f['off']} → {f['on']}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    if a.cmd == "why":
        print(f"[切替口] {_switches(a.days)}（+ b159={a.b159}）", flush=True)
        r = why(days=a.days, loops=a.loops, start=a.start, end=a.end, b159=a.b159)
        print(f"=== B-161 why：θ を通した CCT が絶対防御に採られない段（{a.days}日級 "
              f"{r['n_games']}局・b159={a.b159}）===")
        for k, v in sorted(r["counts"].items()):
            print(f"  {k:<58} {v}")
        print("--- 現物（最大40件） ---")
        for e in r["examples"]:
            print(f"    L{e['loop']}D{e['day']} CCT={e['cct']}({e['cct_prob']}) "
                  f"→ 実際に強制されたのは {e['won']} {e['won_card'] or ''}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    if a.b159:
        DP.B159_MISSING_BOARD = True
    if a.beta is not None:
        DP.B161_COOL_COST = a.beta
    if alpha is not None:
        HeuristicProtagonist.B161_UNREST_COEFF = alpha
    print(f"[切替口] {_switches(a.days)}", flush=True)

    from arena.benchmark import benchmark_scripts
    if a.cmd == "verify":
        bad = n = 0
        for name, seed, sc in list(benchmark_scripts(days=a.days))[a.start:a.end]:
            n += 1
            if not _verify_game(sc, seed, loops=a.loops):
                bad += 1
                print(f"  ★不一致: {name} s{seed}")
        print(f"棋譜の不一致 = {bad} 件 / {n}局")
        return 1 if bad else 0

    r = funnel(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(f"=== B-161 Phase 1：冷却が決定へ届くまでのふるい（{a.days}日級 "
          f"{r['n_games']}局・独立脚本{r['n_scripts']}本）===")
    for k, v in sorted(r["counts"].items()):
        print(f"  {k:<62} {v}")
    for src, pv in r["probs"].items():
        print(f"  [{src}] CCT 実在度 n={pv['n']} min={pv['min']} max={pv['max']} "
              f"／θ(0.9)以上 {pv['ge_theta']}")
    for h in r["rows"]:
        print(f"    {h['script']} s{h['seed']} L{h['loop']}D{h['day']} "
              f"[{h['src']}] {h['card']}→{h['target']} 実在度{h['prob']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
