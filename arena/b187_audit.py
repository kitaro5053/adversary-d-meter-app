# -*- coding: utf-8 -*-
"""B-187 Phase 1：**僕と契約（kp_anyaku）脅威の少女フィルタ欠落の射程**を数える（計測のみ・`agents/` 非接触）。

## 発端（`docs/バックログ_構想メモ_FableA.md` §61(1)・ユーザー実戦 2026-08-07）

KB＝`rules/50_basic_tragedy_x.md:42`「僕と契約しようよ！のキーパーソンは**必ず少女**」。
現物＝`agents/defense_plan.py` `_threat_kp_anyaku` が `_suspects(roles,"キーパーソン")` を
属性で絞らずに回している。belief 側（`agents/belief.py`）は少女限定を**同時分布として実装済み**
（kp_shoujo の組では非少女のKPカウント=0）だが、脅威列挙側は
`contract_prob × P(KP周辺)` の**独立近似**で掛けるため、非少女の P(KP周辺)>0
（殺人計画など契約以外のルール組由来）が漏れ込む＝**規則上不可能な契約脅威**が立つ。

## 本計測が出す数

- **(1) 射程**＝両ベンチ全数で、主人公の各席決定時に立った kp_anyaku 脅威を
  少女／非少女で層別し、(a) 件数 (b) 折り手（break）が付いたか（defendable）
  (c) プランナーが pick に採ったか (d) **席が実際にその pick と同じ手を打ったか** を数える。
  ★(d) は共起であって単独帰属ではない＝同じ手が他の脅威（kp_killer 等）も同時に
  覆うことがあるため、pick キーを共有する脅威数（pick_shared_n）を併記する。
  単独帰属（pick_shared_n==1）だけが「不可能な脅威**だけ**が席を取った」の強い証拠。
- **(2) mm 側の同型**＝`agents/attack_plan._contract_threats`（防御プランナーの複製＝
  mm の圧力オラクル `defender_pressure` の内側）にも同じ欠落がある＝呼び出し数と
  非少女脅威の件数を数える（mm の意思決定への漏れ込みの射程）。
- **(3) L3D1 機序確認**＝ユーザー実戦（`btx5_seal` と同一脚本）の L3D1
  『暗躍禁止→サラリーマン』を合成盤面（同じ mm 配置）で再演し、
  不可能な契約脅威が席の採点に届いた経路（plan 加点／B100／素点）を分解する。

## 因果の限界（先に書く）

1. (1)(d) は**共起**。真の帰属は Phase 2 の {OFF, ON} 掃引の per-game flip で確定する。
2. L3D1 の合成は **L1D1 の belief（2ループ分の観測履歴なし）**での再演＝
   「同じ機序が同じ脚本で成立する」ことの確認であって、実戦その瞬間の belief の再現ではない
   （棋譜ファイルは環境に無い＝§61 の公開情報から再構成）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b187_audit count --days 3 --json d3.json
    python -m arena.b187_audit count --days 5 --json d5.json
    python -m arena.b187_audit report --inputs d3.json d5.json
    python -m arena.b187_audit l3d1
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.attack_plan as ap
import agents.defense_plan as dp
from agents import HeuristicMastermind, HeuristicProtagonist
from arena.benchmark import benchmark_scripts
from engine.data import SHOUJO
from sim import run_game
from sim.sample_scripts import SAMPLE_SCRIPTS

#: kp_anyaku 脅威ラベルの書式（defense_plan / attack_plan で共通）＝
#: f"僕と契約＝{kp}の暗躍2でループ敗北"。victim 抽出の単一箇所。
_LABEL_HEAD = "僕と契約＝"
_LABEL_TAIL = "の暗躍2"


def _victim(label: str) -> str | None:
    if _LABEL_HEAD not in label:
        return None
    return label.split(_LABEL_HEAD, 1)[1].split(_LABEL_TAIL, 1)[0]


class _Probe(HeuristicProtagonist):
    """`plan_for_belief` の返り値（threats, plan）を控える。決定は super() のまま＝挙動同一。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rows: list[dict] = []
        self.n_seats = 0
        # 表示アーム（display_all=True・min_prob=0＝内省パネル相当）の kp_anyaku 層別
        self.disp: Counter = Counter()
        self.disp_victims_ns: Counter = Counter()

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        orig = dp.plan_for_belief
        cap: dict = {}

        def _rec(*a, **kw):
            r = orig(*a, **kw)
            cap["threats"], cap["plan"] = r[0], r[1]
            return r

        dp.plan_for_belief = _rec
        try:
            chosen = super().decide(view, decision, options)
        finally:
            dp.plan_for_belief = orig
        self.n_seats += 1
        try:
            row = self._observe(view, chosen, cap)
            if row["ct"]:
                self.rows.append(row)
        except Exception as e:  # noqa: BLE001
            self.rows.append({"loop": view.get("loop"), "day": view.get("day"),
                              "seat": view.get("seat"), "error": repr(e), "ct": []})
        return chosen

    def _observe(self, view: dict, chosen: dict, cap: dict) -> dict:
        threats = cap.get("threats") or []
        plan = cap.get("plan")
        ck = (chosen.get("card"), chosen.get("target"), chosen.get("target_kind"))
        row = {"loop": view.get("loop"), "day": view.get("day"),
               "seat": view.get("seat"), "chosen": ck, "ct": []}
        shared: Counter = Counter()
        if plan is not None:
            for t in threats:
                p = dp._pick_for(plan, t)
                if p is not None:
                    shared[(p.card, p.target, p.target_kind)] += 1
        for t in threats:
            if t.kind != "kp_anyaku":
                continue
            v = _victim(t.label)
            pick = dp._pick_for(plan, t) if plan is not None else None
            key = (pick.card, pick.target, pick.target_kind) if pick is not None else None
            row["ct"].append({
                "victim": v, "shoujo": v in SHOUJO,
                "prob": round(t.prob, 4), "defendable": bool(t.defendable),
                "pick": key, "played": key is not None and list(key) == list(ck),
                "pick_shared_n": shared.get(key, 0) if key is not None else 0,
            })
        # --- 表示アーム（display_all=True・min_prob=0＝arena/play_vs_ai の内省パネル相当）---
        #   意思決定には使われない純関数呼び出し＝挙動不変。パネル同様 initial_areas は渡さない。
        if getattr(self, "_belief", None) is not None:
            try:
                dts = dp.plan_for_belief(view, self._belief,
                                         display_all=True, min_prob=0.0)[0]
            except Exception:  # noqa: BLE001
                dts = []
            n_ns = n_sh = 0
            for t in dts:
                if t.kind != "kp_anyaku":
                    continue
                v = _victim(t.label)
                if v in SHOUJO:
                    n_sh += 1
                else:
                    n_ns += 1
                    self.disp_victims_ns[v] += 1
            self.disp["inst_ns"] += n_ns
            self.disp["inst_sh"] += n_sh
            if n_ns:
                self.disp["seat_ns"] += 1
        return row


def _wrap_mm_contract(counter: Counter):
    """attack_plan._contract_threats を数える（返り値はそのまま＝挙動同一）。"""
    orig = ap._contract_threats

    def w(*a, **kw):
        r = orig(*a, **kw)
        counter["calls"] += 1
        for t in r:
            v = _victim(t.label)
            counter["all"] += 1
            if v not in SHOUJO:
                counter["ns"] += 1
        return r

    return orig, w


def audit_game(name: str, seed: int, sc, loops: int = 8) -> dict:
    probe = _Probe(seed)
    mm = HeuristicMastermind(seed)
    mm_ct: Counter = Counter()
    orig, w = _wrap_mm_contract(mm_ct)
    ap._contract_threats = w
    try:
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": probe, "p2": probe, "p3": probe})
    finally:
        ap._contract_threats = orig
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw, outcome = loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    else:
        ltw, outcome = loops + 1, "loss"
    # 席への影響の要約（少女／非少女で層別）
    agg = {"seats": probe.n_seats,
           "inst_ns": 0, "inst_sh": 0,           # 脅威インスタンス数
           "inst_ns_def": 0,                      # 非少女で折り手あり
           "seat_ns": 0,                          # 非少女脅威が立っていた席決定の数
           "pick_ns": 0, "pick_sh": 0,            # pick に採られた数
           "played_ns": 0, "played_sh": 0,        # 席がその pick と同じ手を打った数
           "played_ns_solo": 0,                   # うち pick を共有する脅威が自分だけ
           "victims_ns": Counter(), "played_cards_ns": Counter(),
           "errors": 0}
    for row in probe.rows:
        if row.get("error"):
            agg["errors"] += 1
            continue
        has_ns = False
        for c in row["ct"]:
            side = "sh" if c["shoujo"] else "ns"
            agg[f"inst_{side}"] += 1
            if not c["shoujo"]:
                has_ns = True
                agg["victims_ns"][c["victim"]] += 1
                if c["defendable"]:
                    agg["inst_ns_def"] += 1
            if c["pick"] is not None:
                agg[f"pick_{side}"] += 1
            if c["played"]:
                agg[f"played_{side}"] += 1
                if not c["shoujo"]:
                    agg["played_cards_ns"][c["pick"][0]] += 1
                    if c["pick_shared_n"] == 1:
                        agg["played_ns_solo"] += 1
        if has_ns:
            agg["seat_ns"] += 1
    agg["victims_ns"] = dict(agg["victims_ns"])
    agg["played_cards_ns"] = dict(agg["played_cards_ns"])
    agg["disp_inst_ns"] = probe.disp.get("inst_ns", 0)
    agg["disp_inst_sh"] = probe.disp.get("inst_sh", 0)
    agg["disp_seat_ns"] = probe.disp.get("seat_ns", 0)
    agg["disp_victims_ns"] = dict(probe.disp_victims_ns)
    return {"name": name, "seed": seed, "rule_y": sc.rule_y,
            "outcome": outcome, "ltw": ltw,
            "mm_ct": {"calls": mm_ct.get("calls", 0), "all": mm_ct.get("all", 0),
                      "ns": mm_ct.get("ns", 0)},
            **agg}


def run_count(days: int, loops: int = 8, out_json: str | None = None) -> dict:
    rows = [audit_game(name, seed, sc, loops=loops)
            for name, seed, sc in benchmark_scripts(days=days)]
    res = {"days": days, "rows": rows}
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return res


def _script_key(row: dict) -> tuple:
    """独立脚本の単位＝サンプルは name（seed は AI 乱数のみ）／random_* は (name, seed)。"""
    if row["name"].startswith("random_"):
        return (row["name"], row["seed"])
    return (row["name"],)


def report(results: list[dict]) -> None:
    for res in results:
        days = res["days"]
        rows = res["rows"]
        n_scripts = len({_script_key(r) for r in rows})
        print(f"\n===== {days}日級ベンチ（{len(rows)}局・独立脚本 {n_scripts} 本） =====")
        tot = Counter()
        vict = Counter()
        dvict = Counter()
        cards = Counter()
        hit_scripts = set()
        disp_hit_scripts = set()
        for r in rows:
            for k in ("seats", "inst_ns", "inst_sh", "inst_ns_def", "seat_ns",
                      "pick_ns", "pick_sh", "played_ns", "played_sh",
                      "played_ns_solo", "errors"):
                tot[k] += r[k]
            for k in ("disp_inst_ns", "disp_inst_sh", "disp_seat_ns"):
                tot[k] += r.get(k, 0)
            dvict.update(r.get("disp_victims_ns", {}))
            if r.get("disp_inst_ns"):
                disp_hit_scripts.add(_script_key(r))
            tot["mm_ns"] += r["mm_ct"]["ns"]
            tot["mm_all"] += r["mm_ct"]["all"]
            tot["mm_calls"] += r["mm_ct"]["calls"]
            vict.update(r["victims_ns"])
            cards.update(r["played_cards_ns"])
            if r["inst_ns"]:
                hit_scripts.add(_script_key(r))
        print(f"席決定（set_card）総数           : {tot['seats']}")
        print(f"kp_anyaku 脅威インスタンス        : 少女 {tot['inst_sh']} ／ "
              f"★非少女 {tot['inst_ns']}（うち折り手あり {tot['inst_ns_def']}）")
        print(f"非少女脅威が立っていた席決定       : {tot['seat_ns']}")
        print(f"pick に採られた                  : 少女 {tot['pick_sh']} ／ ★非少女 {tot['pick_ns']}")
        print(f"席が pick と同じ手を打った（共起） : 少女 {tot['played_sh']} ／ "
              f"★非少女 {tot['played_ns']}（うち単独帰属 {tot['played_ns_solo']}）")
        print(f"打たれた手の内訳（非少女・共起）   : {dict(cards)}")
        print(f"非少女 victim 内訳（上位10）      : {dict(vict.most_common(10))}")
        print(f"mm 側 _contract_threats          : 呼出 {tot['mm_calls']} ／ 脅威 {tot['mm_all']} "
              f"／ ★非少女 {tot['mm_ns']}")
        print(f"表示アーム（内省パネル相当）       : 少女 {tot['disp_inst_sh']} ／ "
              f"★非少女 {tot['disp_inst_ns']}（出た席決定 {tot['disp_seat_ns']}）")
        print(f"表示アーム 非少女 victim（上位10） : {dict(dvict.most_common(10))}")
        print(f"★表示アームで非少女が出た独立脚本 : {len(disp_hit_scripts)} / {n_scripts} 本"
              f"（出現率 {len(disp_hit_scripts) / n_scripts:.0%}）")
        print(f"観測エラー席                     : {tot['errors']}")
        print(f"★非少女脅威が出た独立脚本         : {len(hit_scripts)} / {n_scripts} 本"
              f"（出現率 {len(hit_scripts) / n_scripts:.0%}）")
        # btx5_seal 単独行（発注指定）
        seal = [r for r in rows if r["name"] == "btx5_seal"]
        if seal:
            print("--- btx5_seal 単独行 ---")
            for r in sorted(seal, key=lambda x: x["seed"]):
                print(f"  seed {r['seed']}: 結末={r['outcome']}/{r['ltw']}  "
                      f"非少女inst={r['inst_ns']} pick={r['pick_ns']} "
                      f"played={r['played_ns']}(solo {r['played_ns_solo']}) "
                      f"victims={r['victims_ns']} mm_ns={r['mm_ct']['ns']} "
                      f"表示ns={r.get('disp_inst_ns', 0)}"
                      f"({r.get('disp_victims_ns', {})})")
        # 層別（脚本行・非少女があったものだけ）
        print("--- 脚本別（非少女脅威が出た行のみ・inst_ns 降順・上位20） ---")
        hits = [r for r in rows if r["inst_ns"]]
        for r in sorted(hits, key=lambda x: -x["inst_ns"])[:20]:
            print(f"  {r['name']}#{r['seed']}({r['rule_y']}): inst_ns={r['inst_ns']} "
                  f"pick={r['pick_ns']} played={r['played_ns']}(solo {r['played_ns_solo']}) "
                  f"結末={r['outcome']}/{r['ltw']}")
        if len(hits) > 20:
            print(f"  ...（他 {len(hits) - 20} 行）")


# ---------------------------------------------------------------------------
# L3D1 機序確認（ユーザー実戦＝btx5_seal と同一脚本・同じ mm 配置を合成）
# ---------------------------------------------------------------------------

class _Stop(Exception):
    pass


class _ScriptedMM(HeuristicMastermind):
    """指定の (loop, day) の set_card だけ指定の (card, target) を強制し、残りは本体 AI。
    forced_by＝{(loop, day): [(card, target), ...]}。"""

    def __init__(self, seed: int, forced_by: dict[tuple[int, int], list]):
        super().__init__(seed)
        self._forced_by = {k: list(v) for k, v in forced_by.items()}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        key = (view.get("loop"), view.get("day"))
        forced = self._forced_by.get(key)
        if decision == "set_card" and forced:
            card, tgt = forced[0]
            hit = [o for o in options
                   if o.get("card") == card and o.get("target") == tgt]
            if hit:
                forced.pop(0)
                return hit[0]
        return super().decide(view, decision, options)


class _L3D1Probe(_Probe):
    """指定 (loop, day) の3席の決定＋全候補スコア（spymax）を控え、3席目の後で停止する。
    それ以外の日は素の decide（_Probe の plan 捕捉は全席で有効＝集計にも使える）。"""

    def __init__(self, seed: int = 0, at: tuple[int, int] = (3, 1)):
        super().__init__(seed)
        self.detail: list[dict] = []
        self._at = at

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card" \
                or (view.get("loop"), view.get("day")) != self._at:
            return _Probe.decide(self, view, decision, options)
        import agents.heuristic_protagonist as _hp_mod
        cap_sc: dict = {}
        _builtin_max = max

        def spymax(*args, **kw):
            if args and isinstance(args[0], list) and "key" in kw and "scored" not in cap_sc:
                key = kw["key"]
                cap_sc["scored"] = sorted(((key(o), o) for o in args[0]),
                                          key=lambda x: -x[0])
            return _builtin_max(*args, **kw)

        _hp_mod.max = spymax
        try:
            chosen = super().decide(view, decision, options)
        finally:
            del _hp_mod.max
        self.last_view = view
        self.last_options = list(options)
        self.detail.append({
            "seat": view.get("seat"), "chosen": chosen,
            "scored_top": [(round(s, 2), f'{o.get("card")}→{o.get("target")}')
                           for s, o in cap_sc.get("scored", [])[:6]],
            "plan_recs": {f"{k[0]}→{k[1]}": v
                          for k, v in (getattr(self, "_plan_recs", None) or {}).items()},
            "prov": chosen.get("prov"),
        })
        if len(self.detail) >= 3:
            raise _Stop
        return chosen


def l3d1(seed: int = 0, at: tuple[int, int] = (3, 1), sk_early: bool = False) -> None:
    """§61(1) 仮説の機序確認。btx5_seal＝ユーザー実戦と同一脚本
    （封印されしモノ×潜む殺人鬼×因果の糸／サラリーマン:クロマク・医者:カルティスト・
    刑事:SK・男子学生:フレンド・学者キャスト入り）。L1〜L2 は AI mm に普通に進めさせ
    （＝belief に2ループ分の敗北観測を持たせ）、L3D1 だけ実戦の公開配置
    （移動←→→男子学生・移動↑↓→サラリーマン）を強制して3席の決定を分解する。
    ★限界＝L1〜L2 の mm 手はユーザーの実手ではなく AI 手（棋譜は環境に無い）。
    sk_early=True＝L1D1・L2D1 にも同じ組成（都市に刑事＋男子学生の2人きり→SK殺）を
    強制する＝rule_y を明かさない勝ち方（ユーザー型）で belief の未収束を再現する。"""
    sc = SAMPLE_SCRIPTS["btx5_seal"]()
    probe = _L3D1Probe(seed, at=at)
    pair = [("移動←→", "男子学生"), ("移動↑↓", "サラリーマン")]
    forced_by = {at: pair}
    if sk_early:
        for lp in range(1, at[0]):
            forced_by[(lp, 1)] = pair
    mm = _ScriptedMM(seed, forced_by)
    try:
        run_game(replace(sc, loops=8),
                 {"mastermind": mm, "p1": probe, "p2": probe, "p3": probe})
    except _Stop:
        pass
    print(f"=== L{at[0]}D{at[1]} 機序確認（btx5_seal・"
          "mm=移動←→→男子学生／移動↑↓→サラリーマン を強制） ===")
    print(f"切替口 B187_CONTRACT_SHOUJO_ONLY = "
          f"{getattr(dp, 'B187_CONTRACT_SHOUJO_ONLY', '（未実装）')}")
    # rows は ct が立った席のみ＝当日の席番号で突き合わせる
    ct_by_seat = {r["seat"]: r for r in probe.rows
                  if not r.get("error") and (r["loop"], r["day"]) == at}
    for det in probe.detail:
        seat = det["seat"]
        print(f"\n--- 席 {seat} ---")
        print(f"  選択: {det['chosen'].get('card')}→{det['chosen'].get('target')}"
              f"（prov={det['prov']}）")
        print(f"  上位スコア: {det['scored_top']}")
        print(f"  plan加点: {det['plan_recs']}")
        r = ct_by_seat.get(seat)
        if r:
            for c in r["ct"]:
                tag = "少女" if c["shoujo"] else "★非少女（規則上不可能）"
                print(f"  kp_anyaku脅威: {c['victim']}（{tag}）prob={c['prob']} "
                      f"pick={c['pick']} played={c['played']} "
                      f"shared_n={c['pick_shared_n']}")
        else:
            print("  kp_anyaku脅威: なし")

    # --- belief 内訳（意思決定経路に脅威が出なかった理由の検分） ---
    bel = getattr(probe, "_belief", None)
    if bel is not None:
        rm = bel.rule_marginals()
        by_y: Counter = Counter()
        for (ry, _rxs), p in rm.items():
            by_y[ry] += p
        print("\n--- belief（当該時点） ---")
        print("  rule_y 周辺:", {k: round(v, 3) for k, v in by_y.most_common()})
        roles = bel.role_marginals()
        kp = {n: round(d.get("キーパーソン", 0.0), 3) for n, d in roles.items()
              if d.get("キーパーソン", 0.0) > 0.001}
        print("  P(キーパーソン) 周辺:", kp)
        print("  契約×KP の正しい同時分布は belief 側で少女限定済み＝"
              "非少女の (契約∧KP) は 0（漏れは独立近似 contract_prob×P(KP周辺) 側）")
    # --- 表示アーム（display_all=True・min_prob=0＝arena/play_vs_ai の内省パネル相当） ---
    view = getattr(probe, "last_view", None)
    if view is not None and bel is not None:
        threats, plan = dp.plan_for_belief(
            view, bel, options=getattr(probe, "last_options", None),
            display_all=True, min_prob=0.0)
        cts = [t for t in threats if t.kind == "kp_anyaku"]
        print("\n--- 表示アーム（display_all=True＝内省パネル相当）の kp_anyaku ---")
        for t in cts:
            v = _victim(t.label)
            tag = "少女" if v in SHOUJO else "★非少女（規則上不可能・ユーザーが見た表示）"
            print(f"  {t.label}  prob={t.prob:.4f}（{tag}）")
        if not cts:
            print("  なし")


def main(argv=None) -> int:
    pa = argparse.ArgumentParser()
    sub = pa.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count")
    c.add_argument("--days", type=int, default=3)
    c.add_argument("--json", default=None)
    r = sub.add_parser("report")
    r.add_argument("--inputs", nargs="+", required=True)
    ll = sub.add_parser("l3d1")
    ll.add_argument("--seed", type=int, default=0)
    ll.add_argument("--sk-early", action="store_true")
    a = pa.parse_args(argv)
    if a.cmd == "count":
        res = run_count(a.days, out_json=a.json)
        report([res])
    elif a.cmd == "report":
        results = []
        for p in a.inputs:
            with open(p, encoding="utf-8") as f:
                results.append(json.load(f))
        report(results)
    elif a.cmd == "l3d1":
        l3d1(a.seed, sk_early=a.sk_early)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
