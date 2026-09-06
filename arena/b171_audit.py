# -*- coding: utf-8 -*-
"""B-171 Phase 1：**事件「流布」×タイムトラベラーの射程を数える**（★計測のみ・`agents/` 非接触）。

## 用語（★記号を使う前に、必ず日本語で定義する）

- **流布**＝事件の1つ。効果は「**任意のキャラクター1人から友好カウンターを2つ取り除き、
  別の任意のキャラクター1人に友好カウンターを2つ置く**」（`rules/40_first_steps.md:154`／
  `rules/50_basic_tragedy_x.md:215-216`）。**対象を選ぶのは脚本家**
  （`rules/00_rules_core.md:123`「各事件の犯人は全て別キャラ。**対象指定は基本的に脚本家**」）。
- **TT**＝役職タイムトラベラー（`rules/50_basic_tragedy_x.md:124-128`）。
  【任意】**最終日のターン終了フェイズ**に、そのキャラの**友好カウンターが2つ以下**なら
  主人公を敗北させてよい／【強制】自分にセットされた友好禁止は無視される。
  閾値の単一ソース＝`engine.turn_end_rules.TT_DEFEAT_GOODWILL_MAX`（=2）。
- **危険な友好帯**＝TT の友好が **3 または 4**。ここに流布の −2 が当たると 1 または 2 になり、
  **その日のターン終了フェイズで敗北条件が復活する**。**友好5以上は −2 では 2以下に落ちない**
  ＝**射程外**（過大主張しない）。
- **席（seat）**＝主人公の `set_card` 決定1回（1ターンに3席）。b151/b161/b163 監査と同じ定義。
- **脅威列挙層**＝`agents/defense_plan.enumerate_threats`（負け筋を `Threat` として列挙し
  折り手 `Break` を付ける層）。**採点層**＝`agents/heuristic_protagonist` の
  `_incident_danger`（事件日ごとの危険度＝カードの素点を動かす層）。

## 本計測が出す数（チケット §5 Phase 1）

- **(a)** TT が配役に居る局／そのうち**流布が事件表にある**局／そのうち**流布が最終日**の局。
- **(b)** その最終日に **TT の友好が 3 または 4** だった席（＝−2 で 2以下に落ちる範囲）。
- **(c)** B-157 §4-2 の**射程O**（`random_BTX` s11）を**独立に再現**する。
- **(d)** 主人公が打てたはずの手が**現行 AI の候補（options）に立っているか**を席単位で実測。
- **(e)** ★**採点層（`危険事件_流布TT` ＝ `PRIORITY` 66.0）が既に発火していたか**、
  発火した席で**実際に何が打たれたか**（加点・絶対防御の強制・実手まで印字）。

## 二重実装をしない（★規約 §6-4）

- 結末の判定は `arena.b145_audit._outcome`／敗北ループは `arena.b145_audit._lost_loops` を import。
- TT の閾値は `engine.turn_end_rules.TT_DEFEAT_GOODWILL_MAX` を import（定数を書き写さない）。
- 採点層の値は `agents.heuristic_protagonist.PRIORITY` を import（66.0 を書き写さない）。
- 脅威・加点・絶対防御の強制は、`agents` の**実インスタンスの内部状態をそのまま読む**
  （`_incident_danger` / `_plan_recs` / `_b100_log`）＝判定を書き写していない。
- ★**唯一の例外＝`_proto_threat_tt_defeat`**（`cf2` 用の改造コピー）。これは
  「B-157 §7-3 の提案を実装したら棋譜がどう変わるか」を測るためだけのもので、
  **既定では一切呼ばれない**（`cf2` の実行中だけ差し替え、終了時に必ず戻す）。
- ★(c)（B-157 §4-2 の射程O の独立再現）は **B-157 の判定器を使わず**、
  公開ログ（`_rufu_firings`）と席の記録から**独立に**組み立てている＝突合が意味を持つようにした。

## 挙動不変（★物証）

プローブは `super().decide()` の**返り値をそのまま返す**だけ＝決定に一切触れない。
`verify` サブコマンドが「プローブ有り／無しで**棋譜（公開ログ）が完全一致**」を突き合わせる。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b171_audit census  --days 3
    python -m arena.b171_audit seatlog --days 3 --script random_BTX --seed 11
    python -m arena.b171_audit count   --days 3 --json d3.json
    python -m arena.b171_audit verify  --days 3 --end 12
    python -m arena.b171_audit cf      --days 3 --script random_BTX --seed 11 --value 0.0
    python -m arena.b171_audit cf2     --days 3 --script random_BTX --seed 11
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.defense_plan as DP
from agents import HeuristicMastermind, HeuristicProtagonist
from agents.heuristic_protagonist import PRIORITY
from arena.b145_audit import _lost_loops, _outcome
from engine.turn_end_rules import TT_DEFEAT_GOODWILL_MAX
from sim import run_game

#: 流布の友好除去量（`rules/40_first_steps.md:154`＝「友好カウンターを**2つ**取り除き」）。
RUFU_MINUS = 2

#: 採点層の値（`agents/heuristic_protagonist.PRIORITY["危険事件_流布TT"]`＝66.0）。
DANGER_RUFU_TT = PRIORITY["危険事件_流布TT"]


def gw_band_at_risk(gw: int) -> bool:
    """★危険な友好帯か＝**今 3以上（＝宣言不可）だが、流布の −2 で 2以下に落ちる**。

    `TT_DEFEAT_GOODWILL_MAX`（=2）を単一ソースにする＝定数 2/3/4 を書き写さない。
    gw=3 → 1、gw=4 → 2 が該当。gw=5 は 3 で落ちない＝**射程外**。
    """
    return gw > TT_DEFEAT_GOODWILL_MAX and (gw - RUFU_MINUS) <= TT_DEFEAT_GOODWILL_MAX


# ---------------------------------------------------------------------------
# 脚本の国勢調査（対局不要）
# ---------------------------------------------------------------------------
def census(days: int) -> dict:
    from arena.benchmark import benchmark_scripts

    rows = list(benchmark_scripts(days=days))
    out = {"days": days, "games": len(rows),
           "script_names": sorted({n for n, _, _ in rows}),
           "n_script_names": len({n for n, _, _ in rows}),
           # ★「独立脚本」＝生成物が違うもの（random_* は seed ごとに別脚本）
           "n_distinct_scripts": len({(n, s) if n.startswith("random_") else (n,)
                                      for n, s, _ in rows}),
           "tt_games": 0, "tt_rufu_games": 0, "tt_rufu_final_games": 0,
           "rufu_games": 0, "detail": []}
    for name, seed, sc in rows:
        roles = {n: sc.role_of(n) for n in sc.cast}
        tt = sorted(n for n, r in roles.items() if r == "タイムトラベラー")
        rufu_days = sorted(i.day for i in sc.incidents if i.name == "流布")
        if rufu_days:
            out["rufu_games"] += 1
        if tt:
            out["tt_games"] += 1
        if tt and rufu_days:
            out["tt_rufu_games"] += 1
            fin = [d for d in rufu_days if d >= sc.days_per_loop]
            if fin:
                out["tt_rufu_final_games"] += 1
            out["detail"].append({
                "script": name, "seed": seed, "tt": tt, "rufu_days": rufu_days,
                "days_per_loop": sc.days_per_loop, "rule_y": sc.rule_y,
                "final_day_rufu": bool(fin),
                "incidents": [(i.day, i.name, i.culprit) for i in sc.incidents],
                "roles": {n: r for n, r in sorted(roles.items()) if r != "パーソン"},
            })
    return out


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝super() の戻り値をそのまま返す）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """席ごとに「流布×TT について AI が何を見て何を打ったか」を控える。

    控えるのは**主人公が公開情報として知りうるもの**＋**AI 自身の内部状態**だけ。
    脚本の真の配役（ground truth）は**一切参照しない**（カンニング防止）。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.seats: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        orig = DP.enumerate_threats
        cap: dict = {}

        def _rec(*a, **kw):
            r = orig(*a, **kw)
            cap["threats"] = list(r)      # ★本番が実際に立てた脅威
            cap["kw"] = dict(kw)
            return r

        DP.enumerate_threats = _rec
        n_b100 = len(getattr(self, "_b100_log", ()) or ())
        try:
            chosen = super().decide(view, decision, options)
        finally:
            DP.enumerate_threats = orig
        try:
            self.seats.append(self._observe(view, options, chosen, cap, n_b100))
        except Exception as e:            # noqa: BLE001  観測失敗は黙らせない
            self.seats.append({"loop": view.get("loop"), "day": view.get("day"),
                               "seat": view.get("seat"), "error": repr(e)})
        return chosen

    # -- 観測 -----------------------------------------------------------------
    def _observe(self, view, options, chosen, cap, n_b100) -> dict:
        chars = {c.get("name"): c for c in (view.get("characters") or [])}
        day = int(view.get("day", 1) or 1)
        incs = list(view.get("incidents") or ())
        rufu_days = sorted(i.get("day") for i in incs
                           if i.get("name") == "流布" and i.get("day") is not None)
        final_day = bool(view.get("final_day"))
        if not final_day:                 # view に無い実装差を吸収（最終日＝日数）
            dpl = view.get("days_per_loop") or view.get("days")
            final_day = bool(dpl and day >= int(dpl))
        marg = {}
        try:
            marg = self._belief.role_marginals()
        except Exception:                 # noqa: BLE001
            marg = {}
        # ★TT 疑い＝belief の周辺確率（AI が実際に使っている量。真の配役は見ない）
        tt_susp = {n: round(float(m.get("タイムトラベラー", 0.0)), 4)
                   for n, m in marg.items()
                   if float(m.get("タイムトラベラー", 0.0)) > 0.0}
        row = {
            "loop": view.get("loop"), "day": day, "seat": view.get("seat"),
            "final_day": final_day, "rufu_days": rufu_days,
            "rufu_today": day in rufu_days,
            "danger_of_day": {int(k): round(float(v), 1)
                              for k, v in (getattr(self, "_incident_danger", {}) or {}).items()},
            # ★(e) 採点層が流布×TT で発火したか＝その事件日の危険度が 66.0 に達しているか
            "e_rufu_tt_danger": [d for d in rufu_days
                                 if abs(float((getattr(self, "_incident_danger", {}) or {})
                                              .get(d, 0.0)) - DANGER_RUFU_TT) < 1e-9],
            "tt_suspects": dict(sorted(tt_susp.items(), key=lambda kv: -kv[1])[:4]),
            "tt_goodwill": {n: int((chars.get(n) or {}).get("goodwill", 0) or 0)
                            for n in tt_susp},
            "tt_alive": {n: bool((chars.get(n) or {}).get("alive", True))
                         for n in tt_susp},
            "chosen": {"card": chosen.get("card"), "target": chosen.get("target"),
                       "prov": chosen.get("prov")},
        }
        # ★(b) 危険な友好帯に居る TT 疑い（確度0.05超だけ＝ノイズを落とす）
        row["b_at_risk"] = sorted(
            n for n, p in tt_susp.items()
            if p > 0.05 and row["tt_alive"].get(n)
            and gw_band_at_risk(row["tt_goodwill"].get(n, 0)))
        # ★脅威列挙層が TT を名指ししたか（kind=="tt_defeat"）
        th = cap.get("threats") or []
        row["tt_threats"] = [{"label": t.label, "prob": round(float(t.prob), 4),
                              "n_breaks": sum(len(cd.breaks or ()) for cd in (t.conditions or ()))}
                             for t in th if getattr(t, "kind", "") == "tt_defeat"]
        # ★(d) 主人公が打てたはずの手が **options に立っているか**
        #   (d1) TT 疑いの友好をさらに上げる（−2 されても3以上を保つ＝gw+delta−2 ≥ 3）
        #   (d2) 今日の流布の犯人候補を冷やす（不安-1）／発生自体を止める
        # ★対象の流布日＝今日が流布ならその日、そうでなければ**今日以降で最も近い流布日**。
        _fut = [d for d in rufu_days if d >= day]
        rufu_target_day = _fut[0] if _fut else None
        row["rufu_target_day"] = rufu_target_day
        cands_today: set = set()
        try:
            cands_today = set((self._culprit_cands or {}).get(
                rufu_target_day if rufu_target_day is not None else -1, ()) or ())
        except Exception:                 # noqa: BLE001
            cands_today = set()
        d1, d2 = [], []
        for o in options:
            if o.get("target_kind") not in (None, "character"):
                continue
            card, tgt = o.get("card"), o.get("target")
            if card in ("友好+1", "友好+2") and tgt in row["b_at_risk"]:
                gw = row["tt_goodwill"].get(tgt, 0)
                delta = 2 if card == "友好+2" else 1
                d1.append({"card": card, "target": tgt, "gw": gw,
                           "after_rufu": gw + delta - RUFU_MINUS,
                           "safe": (gw + delta - RUFU_MINUS) > TT_DEFEAT_GOODWILL_MAX})
            if card == "不安-1" and tgt in cands_today:
                d2.append({"card": card, "target": tgt})
        row["d1_goodwill_options"] = d1
        # ★(d1') 決定的な数＝**TT 疑い全員**（危険帯に限らない）について、
        #   「今日 流布 が起きた後もなお友好3以上を保てる友好+札」が options にあるか。
        #   `gw + delta - 2 >= 3`。★これが無い席では、脅威を足しても**折り手が生まれない**
        #   ＝「折り手が立つ」と「折り手が買われる」は別（B-159/B-161/B-170 の教訓）。
        d1_all = []
        for o in options:
            if o.get("target_kind") not in (None, "character"):
                continue
            card, tgt = o.get("card"), o.get("target")
            if card not in ("友好+1", "友好+2"):
                continue
            p = tt_susp.get(tgt)
            if not p or p <= 0.05 or not row["tt_alive"].get(tgt):
                continue
            gw = row["tt_goodwill"].get(tgt, 0)
            delta = 2 if card == "友好+2" else 1
            d1_all.append({"card": card, "target": tgt, "p_tt": p, "gw": gw,
                           # 流布が今日起きて、その −2 が当たった場合の最終友好
                           "after_rufu": gw + delta - RUFU_MINUS,
                           "safe_vs_rufu": (gw + delta - RUFU_MINUS) > TT_DEFEAT_GOODWILL_MAX,
                           # 流布が当たらない前提（＝現行 `_threat_tt_defeat` の基準）
                           "safe_now": (gw + delta) > TT_DEFEAT_GOODWILL_MAX})
        row["d1_all_options"] = d1_all
        # ★友好+2 は **1ループに1回**（`engine/models.ONCE_PER_LOOP["protagonist"]`）＝
        #   最終日には既に使い切られている可能性が高い。この席の手札に残っているか。
        row["has_gw2"] = any(o.get("card") == "友好+2" for o in options)
        row["has_gw1"] = any(o.get("card") == "友好+1" for o in options)
        row["d2_cool_options"] = d2
        row["d2_culprit_cands"] = sorted(cands_today)
        # ★加点（`_plan_recs`）と絶対防御の強制（`_b100_log` の新規分）
        row["plan_recs"] = {f"{k[0]}→{k[1]}": round(float(v), 1)
                            for k, v in (getattr(self, "_plan_recs", {}) or {}).items()}
        row["b100_new"] = list((getattr(self, "_b100_log", ()) or ())[n_b100:])
        return row


# ---------------------------------------------------------------------------
def run_probe(script, seed: int, loops: int = 8):
    probe = replace(script, loops=loops)
    hp = _Probe(seed)
    mm = HeuristicMastermind(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state, hp


def _rufu_firings(state) -> list[dict]:
    """公開ログから「流布が発生した回」と友好の付け替え先を読む（推定を混ぜない）。"""
    out: list[dict] = []
    cur = None
    for e in state.history:
        if e.get("phase") != "incident":
            continue
        if e.get("event") == "incident":
            cur = {"loop": e.get("loop"), "day": e.get("day"),
                   "name": e.get("name"), "occurs": bool(e.get("occurs")),
                   "minus": None, "plus": None}
            if cur["name"] == "流布" and cur["occurs"]:
                out.append(cur)
            else:
                cur = None
        elif cur is not None and e.get("event") == "goodwill":
            d = int(e.get("delta", 0) or 0)
            if d < 0:
                cur["minus"] = e.get("target")
            elif d > 0:
                cur["plus"] = e.get("target")
    return out


# ---------------------------------------------------------------------------
def cmd_seatlog(days: int, script_name: str, seed: int, loops: int) -> None:
    """1局を丸ごと解剖して**棋譜**を印字する（★地雷2＝実際に何が打たれたかまで見る）。"""
    from arena.benchmark import benchmark_scripts

    hit = [(n, s, sc) for n, s, sc in benchmark_scripts(days=days)
           if n == script_name and s == seed]
    if not hit:
        raise SystemExit(f"該当局なし: {script_name} seed={seed} days={days}")
    name, sd, sc = hit[0]
    roles = {n: sc.role_of(n) for n in sc.cast}
    tt_true = sorted(n for n, r in roles.items() if r == "タイムトラベラー")
    print(f"■ {name} seed={sd} days={sc.days_per_loop} rule_y={sc.rule_y} "
          f"rule_x={getattr(sc, 'rule_x', None)}")
    print(f"  cast={list(sc.cast)}")
    _rl = ", ".join(f"{k}:{v}" for k, v in sorted(roles.items()) if v != "パーソン")
    print(f"  roles(真)={{{_rl}}}")
    print(f"  incidents={[(i.day, i.name, i.culprit) for i in sc.incidents]}")
    print(f"  TT(真)={tt_true}")
    state, hp = run_probe(sc, sd, loops=loops)
    print(f"  結末={_outcome(state)} 敗北ループ={sorted(_lost_loops(state))} "
          f"総ループ={state.loop_no}")
    print("\n▼ 流布の発生（公開ログ）")
    for f in _rufu_firings(state):
        tt_hit = "★TTから剥がした" if f["minus"] in tt_true else "（TT以外から剥がした）"
        print(f"  L{f['loop']}D{f['day']} 流布 発生: −2→{f['minus']} / +2→{f['plus']}  {tt_hit}")
    print("\n▼ 最終日の席（主人公の set_card 決定）")
    for r in hp.seats:
        if r.get("error"):
            print(f"  !! {r}")
            continue
        if not r["final_day"]:
            continue
        ch = r["chosen"]
        print(f"  L{r['loop']}D{r['day']} seat{r['seat']}: 打った手={ch['card']}→{ch['target']}"
              f" prov={ch['prov']}")
        print(f"      TT疑い={r['tt_suspects']} 友好={r['tt_goodwill']} 生存={r['tt_alive']}")
        print(f"      (b)危険帯(友好3or4)のTT疑い={r['b_at_risk']}")
        print(f"      (e)流布TT採点層66.0が乗った事件日={r['e_rufu_tt_danger']}"
              f"  事件日別危険度={r['danger_of_day']}")
        print(f"      脅威列挙層 tt_defeat={r['tt_threats']}")
        print(f"      (d1)危険帯への友好+ の候補={r['d1_goodwill_options']}")
        print(f"      (d1')TT疑い全員への友好+ の候補={r['d1_all_options']}")
        print(f"      (d2)冷却の候補={r['d2_cool_options']} 犯人候補={r['d2_culprit_cands']}")
        if r["plan_recs"]:
            print(f"      加点(_plan_recs)={r['plan_recs']}")
        for b in r["b100_new"]:
            print(f"      ★絶対防御の強制: {b}")


# ---------------------------------------------------------------------------
def cmd_count(days: int, loops: int, start: int, end, out_json) -> None:
    """全数（両ベンチ）で (a)(b)(d)(e) を数える。"""
    from arena.benchmark import benchmark_scripts

    c = Counter()
    rows = list(benchmark_scripts(days=days))[start:end]
    names = sorted({n for n, _, _ in rows})
    examples: list = []
    for name, seed, sc in rows:
        c["局"] += 1
        roles = {n: sc.role_of(n) for n in sc.cast}
        tt_true = sorted(n for n, r in roles.items() if r == "タイムトラベラー")
        rufu_days = sorted(i.day for i in sc.incidents if i.name == "流布")
        if tt_true:
            c["(a) TTが配役に居る局"] += 1
        if rufu_days:
            c["(a) 流布が事件表にある局"] += 1
        if tt_true and rufu_days:
            c["(a) TT×流布の局"] += 1
            if any(d >= sc.days_per_loop for d in rufu_days):
                c["(a) うち流布が最終日"] += 1
        state, hp = run_probe(sc, seed, loops=loops)
        for f in _rufu_firings(state):
            c["流布の発生"] += 1
            if tt_true:
                c["流布の発生（TTが配役に居る局）"] += 1
                if f["minus"] in tt_true:
                    c["★流布の−2がTTに当たった"] += 1
        for r in hp.seats:
            if r.get("error"):
                c["!!観測エラー"] += 1
                continue
            if r["rufu_days"]:
                c["(母数) 流布が事件表にある局の席"] += 1
                if r["e_rufu_tt_danger"]:
                    c["(e) 採点層 危険事件_流布TT が発火した席"] += 1
            if not r["final_day"]:
                continue
            c["(母数) 最終日の席"] += 1
            if not r["rufu_days"]:
                continue
            c["(母数) 最終日の席×流布が事件表"] += 1
            if r["rufu_today"]:
                c["(母数) 最終日の席×今日が流布"] += 1
            if r["b_at_risk"]:
                c["(b) 危険帯(友好3or4)のTT疑いが居る最終日の席"] += 1
                if r["rufu_today"]:
                    c["(b) ★うち今日が流布＝射程"] += 1
                if r["tt_threats"]:
                    c["(b) うち脅威列挙層が tt_defeat を立てた"] += 1
                if any(o["safe"] for o in r["d1_goodwill_options"]):
                    c["(d1) −2されても3以上を保てる友好+札が options にある"] += 1
                if r["d2_cool_options"]:
                    c["(d2) 流布の犯人候補への 不安-1 が options にある"] += 1
                if len(examples) < 40:
                    examples.append({"script": name, "seed": seed, **r})
            if r["rufu_today"]:
                c["(母数) 今日が流布の最終日の席"] += 1
                # ★折り手の在庫＝友好+2 は1ループに1回（最終日には枯れている可能性）
                if r["has_gw2"]:
                    c["(在庫) 友好+2 がこの席の手札に残っている"] += 1
                if r["has_gw1"]:
                    c["(在庫) 友好+1 がこの席の手札に残っている"] += 1
                if r["d2_cool_options"]:
                    c["(d2') 今日の流布の犯人候補への 不安-1 が options にある席"] += 1
                # ★決定的な数＝脅威を足したときに**折り手が生まれるか**
                if any(o["safe_vs_rufu"] for o in r["d1_all_options"]):
                    c["(d1') 流布後も友好3以上を保てる友好+札が options にある席"] += 1
                if any(o["safe_now"] and not o["safe_vs_rufu"]
                       for o in r["d1_all_options"]):
                    c["(d1') ★現行基準では足りるが流布後は足りない札しか無い席"] += 1
    print(f"=== {days}日級 局数={len(rows)} 脚本名={len(names)}本 {names}")
    for k, v in sorted(c.items()):
        print(f"  {k:52s} = {v}")
    if out_json:
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump({"days": days, "counts": dict(c), "examples": examples},
                      fh, ensure_ascii=False, indent=1)
        print(f"  -> {out_json}")


# ---------------------------------------------------------------------------
def cmd_cf(days: int, script_name: str, seed: int, loops: int, value: float) -> None:
    """★反実仮想＝採点層の `危険事件_流布TT` の値だけを差し替えて棋譜を比べる。

    「66.0 が実際に手を買っていたのか」は、**値を落として棋譜が変わるか**でしか言えない
    （規約 §4「実測してから結論」・地雷1「加点・強制・実手まで印字」）。
    ★`agents/` のファイルは変更しない＝**この測定プロセスの中でだけ**辞書を書き換える。
    """
    from arena.benchmark import benchmark_scripts

    hit = [(n, s, sc) for n, s, sc in benchmark_scripts(days=days)
           if n == script_name and s == seed]
    if not hit:
        raise SystemExit(f"該当局なし: {script_name} seed={seed} days={days}")
    _n, sd, sc = hit[0]

    def _moves(hp):
        """主人公が実際に打った手（プローブが席ごとに控えた `chosen`）。

        ★`state.history` には `set_card` イベントが無い（`sim/flow.py:174` は
          `state.turn_placements` に積むだけ）＝プローブの記録が一次データになる。
        """
        return [(r.get("loop"), r.get("day"), r.get("seat"),
                 (r.get("chosen") or {}).get("card"),
                 (r.get("chosen") or {}).get("target")) for r in hp.seats]

    base_state, base_hp = run_probe(sc, sd, loops=loops)
    old = PRIORITY["危険事件_流布TT"]
    PRIORITY["危険事件_流布TT"] = value
    try:
        cf_state, cf_hp = run_probe(sc, sd, loops=loops)
    finally:
        PRIORITY["危険事件_流布TT"] = old
    b, c = _moves(base_hp), _moves(cf_hp)
    print(f"  公開棋譜が一致={base_state.history == cf_state.history}")
    print(f"■ 反実仮想: 危険事件_流布TT {old} → {value}  ({script_name} s{sd} days={days})")
    print(f"  結末 base={_outcome(base_state)}(L{base_state.loop_no}) "
          f"cf={_outcome(cf_state)}(L{cf_state.loop_no})")
    print(f"  棋譜（set_card）が一致={b == c}  手数 base={len(b)} cf={len(c)}")
    n = 0
    for i, (x, y) in enumerate(zip(b, c)):
        if x != y:
            n += 1
            if n <= 20:
                print(f"   差分#{n} L{x[0]}D{x[1]} {x[2]}: base={x[3]}→{x[4]}"
                      f" / cf={y[3]}→{y[4]}")
    print(f"  相違した手＝{n}（先頭の一致部分は省略。長さが違う場合は分岐後は比較不能）")


# ---------------------------------------------------------------------------
# ★Phase 2 のプロトタイプ（**本採用しない**・反実仮想を測るためだけの差し替え）
# ---------------------------------------------------------------------------
def _proto_threat_tt_defeat(view, roles, opts, final_day):
    """B-157 §7-3 の提案を**素直に実装したらどうなるか**を測るためだけの版。

    ★これは `agents/defense_plan._threat_tt_defeat` の**改造コピー**である。
      通常であれば二重実装は禁止（規約 §6-4）だが、本関数は
      「**その改造を入れたら棋譜がどう変わるか**」を測る反実仮想のためだけに存在し、
      **既定では一切呼ばれない**（`cf2` サブコマンドの実行中だけ差し替える）。
    改造点は2つだけ（他は原本と同一）：
      (A) 足切り `goodwill > 2` を、**今日が最終日かつ今日の事件が流布**なら `> 4` に緩める。
      (B) 折り手の到達条件 `_gw + delta >= 3` を、同じ文脈では **`>= 3 + 2`**（流布の −2 を見込む）にする。
    """
    from agents.defense_plan import Break, Condition, Threat, _char, _suspects

    if not final_day:
        return []
    day = view.get("day")
    rufu_today = any(i.get("name") == "流布" and i.get("day") == day
                     for i in (view.get("incidents") or ()))
    cut = (TT_DEFEAT_GOODWILL_MAX + RUFU_MINUS) if rufu_today else TT_DEFEAT_GOODWILL_MAX
    need = (TT_DEFEAT_GOODWILL_MAX + 1 + RUFU_MINUS) if rufu_today \
        else (TT_DEFEAT_GOODWILL_MAX + 1)
    out = []
    for t, pt in _suspects(roles, "タイムトラベラー").items():
        c = _char(view, t)
        if not c or not c.get("alive", True) or c.get("goodwill", 0) > cut:
            continue
        c1 = Condition("TTの友好が2以下（最終日）")
        _gw = c.get("goodwill", 0)
        for card, delta in (("友好+2", 2), ("友好+1", 1)):
            if _gw + delta >= need and opts.has_char(card, t):
                c1.breaks.append(Break(f"{t}に{card}（友好禁止を無視して載る）",
                                       card, t, "character", 1.1, robust=True))
                break
        out.append(Threat("tt_defeat", f"TT{t}の任意敗北（最終日）", pt,
                          True, "ループ終了フェイズ", [c1]))
    return out


def cmd_cf2(days: int, script_name: str, seed: int, loops: int) -> None:
    """★B-157 §7-3 の提案を素直に実装した版で棋譜を比べる（**予測でなく実測**）。"""
    from arena.benchmark import benchmark_scripts

    hit = [(n, s, sc) for n, s, sc in benchmark_scripts(days=days)
           if n == script_name and s == seed]
    if not hit:
        raise SystemExit(f"該当局なし: {script_name} seed={seed} days={days}")
    _n, sd, sc = hit[0]

    def _moves(hp):
        return [(r.get("loop"), r.get("day"), r.get("seat"),
                 (r.get("chosen") or {}).get("card"),
                 (r.get("chosen") or {}).get("target"),
                 (r.get("chosen") or {}).get("prov")) for r in hp.seats]

    base_state, base_hp = run_probe(sc, sd, loops=loops)
    orig = DP._threat_tt_defeat
    DP._threat_tt_defeat = _proto_threat_tt_defeat
    try:
        cf_state, cf_hp = run_probe(sc, sd, loops=loops)
    finally:
        DP._threat_tt_defeat = orig
    b, c = _moves(base_hp), _moves(cf_hp)
    print(f"■ 反実仮想2: _threat_tt_defeat を B-157 §7-3 の素直な実装に差し替え "
          f"({script_name} s{sd} days={days})")
    print(f"  結末 base={_outcome(base_state)}(L{base_state.loop_no}) "
          f"cf={_outcome(cf_state)}(L{cf_state.loop_no})")
    print(f"  公開棋譜が一致={base_state.history == cf_state.history}")
    print(f"  主人公の手が一致={b == c}  手数 base={len(b)} cf={len(c)}")
    n = 0
    for x, y in zip(b, c):
        if x != y:
            n += 1
            if n <= 20:
                print(f"   差分#{n} L{x[0]}D{x[1]} {x[2]}: "
                      f"base={x[3]}→{x[4]}(prov={x[5]}) / cf={y[3]}→{y[4]}(prov={y[5]})")
    print(f"  相違した手＝{n}")


# ---------------------------------------------------------------------------
def cmd_verify(days: int, loops: int, end) -> None:
    """★挙動不変の物証＝プローブ有／無で**公開棋譜が完全一致**することを突き合わせる。"""
    from arena.benchmark import benchmark_scripts

    bad = 0
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[:end]:
        n += 1
        # ★`arena.benchmark.loops_to_win` と**同じ組み方**にする＝主人公は
        #   **1インスタンスを p1/p2/p3 で共有**する（3つ別々に作ると belief 等の状態が
        #   分かれて**別のAIになる**＝実測で全130局が食い違った。ここを間違えると
        #   「プローブが挙動を変えた」と誤読する）。
        probe = replace(sc, loops=loops)
        base = HeuristicProtagonist(seed)
        s1, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": base, "p2": base, "p3": base})
        hp = _Probe(seed)
        s2, _ = run_game(replace(sc, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": hp, "p2": hp, "p3": hp})
        same = (s1.history == s2.history) and (_outcome(s1) == _outcome(s2))
        if not same:
            bad += 1
            print(f"  !! 不一致 {name} seed={seed}")
    print(f"verify days={days}: {n}局 中 不一致 {bad} 件"
          f"（0＝プローブは棋譜を1手も変えていない）")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="B-171 流布×タイムトラベラー 計測")
    ap.add_argument("cmd", choices=["census", "seatlog", "count", "verify", "cf", "cf2"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--script", default="random_BTX")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--json", dest="out_json", default=None)
    ap.add_argument("--value", type=float, default=0.0,
                    help="cf: 危険事件_流布TT に差し替える値（既定 0.0＝無効化）")
    a = ap.parse_args()
    HP = HeuristicProtagonist
    print("[切替口] "
          f"B141_COOLER_VALUE_FUTURE_ONLY={getattr(HP, 'B141_COOLER_VALUE_FUTURE_ONLY', 'n/a')} "
          f"B153_JUUSHA_PAIR_BREAK={getattr(DP, 'B153_JUUSHA_PAIR_BREAK', 'n/a')} "
          f"B165_PAIR_BREAK={getattr(DP, 'B165_PAIR_BREAK', 'n/a')} "
          f"| B163_KURO_IN_CAST={getattr(HP, 'B163_KURO_IN_CAST', 'n/a')} "
          f"B153_SUICIDE={DP.B153_SUICIDE} "
          f"B159_MISSING_BOARD={getattr(DP, 'B159_MISSING_BOARD', 'n/a')} "
          f"B161_UNREST_COEFF={getattr(HP, 'B161_UNREST_COEFF', 'n/a')} "
          f"b152b_final_day_payoff={HeuristicMastermind(0).p.get('b152b_final_day_payoff', 'n/a')} "
          f"| TT_DEFEAT_GOODWILL_MAX={TT_DEFEAT_GOODWILL_MAX} "
          f"危険事件_流布TT={DANGER_RUFU_TT} "
          "| B171 切替口＝無し（計測のみ・agents/ 非接触）")
    if a.cmd == "census":
        r = census(a.days)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif a.cmd == "seatlog":
        cmd_seatlog(a.days, a.script, a.seed, a.loops)
    elif a.cmd == "count":
        cmd_count(a.days, a.loops, a.start, a.end, a.out_json)
    elif a.cmd == "cf":
        cmd_cf(a.days, a.script, a.seed, a.loops, a.value)
    elif a.cmd == "cf2":
        cmd_cf2(a.days, a.script, a.seed, a.loops)
    else:
        cmd_verify(a.days, a.loops, a.end)


if __name__ == "__main__":
    main()
