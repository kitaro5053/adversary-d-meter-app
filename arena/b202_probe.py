# -*- coding: utf-8 -*-
"""B-202 Phase 0：教材棋譜（鈴蘭 BTX3d seed0・build 05f3355）で
**不安チャネルの分離**（受け手＝医者を供給圏の外へ／供給役＝巫女を動かす）が選ばれず
**冷却**（`不安-1→医者`）が選ばれる理由を採点表で確定する読み取り専用プローブ。

★agents/ の判断経路・既定値には一切触れない（読むだけ）。

サブコマンド:
    python -m arena.b202_probe why  [--log PATH]
        # 主人公の全決定を素で再生（bit 一致確認）し、各 set_card ターンで
        #   - 上位候補の採点
        #   - 分離移動（受け手／供給役）と冷却の採点
        #   - B-90/B-94（既存の ML供給隔離）が落ちた条件
    python -m arena.b202_probe replay [--log PATH]
        # 人間 mm の手を再生して主人公AIだけ差し替えた1対局（OFF/ON）
    python -m arena.b202_probe count --days 3|5
        # ベンチ全局で B-202 述語が何席で True になるか（行為の数え上げ）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.debug import ProbedProtagonist

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_ラバーズ連鎖_2026-08-11.jsonl"
LOG2 = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_爆弾X発動後_2026-08-11.jsonl"
_MOVE = ("移動↑↓", "移動←→", "移動↖↘")


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return lines[0], [d for d in lines if d.get("type") == "decision"]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _mm_chars(view: dict) -> list[str]:
    return sorted({p["target"] for p in view.get("placements", [])
                   if p.get("owner") == "mastermind"
                   and p.get("target_kind") == "character"})


def cmd_why(args) -> int:
    from agents.heuristic_protagonist import _move_dest, unrest_threshold_of
    meta, decisions = _load(args.log)
    print(f"===== B-202 Phase 0  {Path(args.log).name}  (build {meta.get('tool_build')}) =====")
    hp = ProbedProtagonist(0, top=400)
    n_same = n_all = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        view, options = d["view"], d["options"]
        chosen = hp.decide(view, d["decision"], options)
        same = _clean(chosen) == _clean(d["chosen"])
        n_all += 1
        n_same += bool(same)
        if d["decision"] != "set_card":
            continue
        rec = hp.records[-1]
        mark = "" if same else f"  ≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        print(f"--- L{d['loop']}D{d['day']} {view.get('seat')}: "
              f"選択={_fmt(_clean(chosen))}{mark}")
        print(f"      mm札(キャラ)={_mm_chars(view)}")
        occ: dict = {}
        for c in view["characters"]:
            if c.get("alive"):
                occ.setdefault(c["area"], []).append(
                    f'{c["name"]}(不{c["unrest"]}/暗{c["anyaku"]})')
        print("      盤面: " + " | ".join(f"{a}:{','.join(v)}" for a, v in sorted(occ.items())))
        for s, o in rec["scored"][:3]:
            print(f"      TOP  {s:8.2f} {_fmt(o)}")
        # 分離移動と冷却の採点
        for s, o in rec["scored"]:
            if o.get("target_kind") != "character":
                continue
            if (o.get("card") in _MOVE and o.get("target") in args.watch) \
                    or (o.get("card") == "不安-1" and o.get("target") in args.watch):
                c = hp._alive(view, o["target"])
                dest = _move_dest(c["area"], o["card"]) if c and o["card"] in _MOVE else "-"
                print(f"      ★    {s:8.2f} {_fmt(o)}  (→{dest})")
        # 既存述語の診断
        print(f"      _lethal_days={sorted(getattr(hp, '_lethal_days', ()))} "
              f"_cooled_days={sorted(getattr(hp, '_cooled_days', ()))} "
              f"culprit_cands={ {k: sorted(v) for k, v in getattr(hp, '_culprit_cands', {}).items()} }")
        try:
            marg = hp._belief.role_marginals()
        except Exception:
            marg = {}
        ml = {n: round(dist.get("ミスリーダー", 0.0), 3) for n, dist in marg.items()
              if dist.get("ミスリーダー", 0.0) > 0.01}
        print(f"      P(ミスリーダー)={json.dumps(dict(sorted(ml.items(), key=lambda x: -x[1])), ensure_ascii=False)}")
        print(f"      _b90_pairs={hp._b90_pairs(view)}")
        print(f"      _b94_pairs={hp._b94_pairs(view)}")
        # なぜ B-90 が落ちたか（条件を素で数える）
        for cn in args.watch:
            c = hp._alive(view, cn)
            if c is None:
                continue
            th = unrest_threshold_of(cn)
            others = [oc["name"] for oc in view["characters"]
                      if oc.get("alive") and oc["name"] != cn and oc.get("area") == c["area"]]
            print(f"      [{cn}] 臨界={th} 不安={c['unrest']} エリア={c['area']} 同室他={others}")
    print(f"[probe] 主人公決定の bit 一致 = {n_same}/{n_all}")
    return 0


class _ReplayMastermind:
    """棋譜の脚本家決定を (loop, day, decision) の出現順で再生する脚本家（b201_probe と同型）。"""

    def __init__(self, decisions, seed: int = 0):
        from agents.heuristic import HeuristicMastermind
        self._queue: dict = {}
        for d in decisions:
            if d.get("actor") != "mastermind":
                continue
            self._queue.setdefault((d["loop"], d["day"], d["decision"]), []).append(d["chosen"])
        self._fallback = HeuristicMastermind(seed)
        self.n_replayed = 0
        self.n_fallback = 0

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        key = (view.get("loop"), view.get("day"), decision)
        q = self._queue.get(key) or []
        while q:
            cand = q.pop(0)
            for o in options:
                if all(o.get(k) == v for k, v in cand.items() if k != "prov"):
                    self.n_replayed += 1
                    return o
        self.n_fallback += 1
        return self._fallback.decide(view, decision, options)


def _run_once(meta, decisions, on: bool, b201: bool = True):
    from sim import run_game
    from sim.state import script_from_dict
    from agents.heuristic_protagonist import HeuristicProtagonist
    import agents.defense_plan as _dp
    script = script_from_dict(meta["script"])
    _old = _dp.B201_MAINLOVER_ANYAKU_PIN
    _dp.B201_MAINLOVER_ANYAKU_PIN = b201
    try:
        hp = HeuristicProtagonist(0)
        hp.B202_UNREST_SUPPLY_SEP = on
        hp.B201_MAINLOVER_PIN = b201
        mm = _ReplayMastermind(decisions, 0)
        state, _log = run_game(script, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        _dp.B201_MAINLOVER_ANYAKU_PIN = _old
    return state, mm


def _incident_rows(state) -> list[str]:
    """公開履歴から事件の発生／不発と、対象キャラの不安推移を抜き出す。"""
    out = []
    for e in state.history:
        if e.get("event") == "incident":
            out.append(f"L{e.get('loop')}D{e.get('day')} {e.get('name')}="
                       f"{'発生' if e.get('occurs') else '不発'}")
    return out


def cmd_replay(args) -> int:
    for path in ([args.log] if args.log else [LOG, LOG2]):
        meta, decisions = _load(path)
        print(f"===== {Path(path).name}  (B-201={'ON' if not args.no_b201 else 'OFF'}) =====")
        for on in (False, True):
            state, mm = _run_once(meta, decisions, on, b201=not args.no_b201)
            print(f"[B-202 {'ON ' if on else 'OFF'}] winner={state.winner} "
                  f"loops_played={state.loop_no} 最終日={state.day} "
                  f"（人間手の再生={mm.n_replayed} / AI委譲={mm.n_fallback}）")
            print("    事件: " + " / ".join(_incident_rows(state)))
    return 0


def cmd_bit(args) -> int:
    """OFF が棋譜と bit 一致することの確認（全決定を素で再生）。"""
    import agents.heuristic_protagonist as hpm
    for path in ([args.log] if args.log else [LOG, LOG2]):
        meta, decisions = _load(path)
        for on in (False, True):
            old = hpm.HeuristicProtagonist.B202_UNREST_SUPPLY_SEP
            hpm.HeuristicProtagonist.B202_UNREST_SUPPLY_SEP = on
            try:
                hp = ProbedProtagonist(0, top=8)
                n_same = n_all = 0
                diffs = []
                for d in decisions:
                    if d.get("actor") == "mastermind":
                        continue
                    chosen = hp.decide(d["view"], d["decision"], d["options"])
                    n_all += 1
                    if _clean(chosen) == _clean(d["chosen"]):
                        n_same += 1
                    else:
                        diffs.append(f"L{d['loop']}D{d['day']}{d['view'].get('seat')}: "
                                     f"{_fmt(_clean(d['chosen']))}→{_fmt(_clean(chosen))}")
            finally:
                hpm.HeuristicProtagonist.B202_UNREST_SUPPLY_SEP = old
            print(f"{Path(path).name}  B-202 {'ON ' if on else 'OFF'}: "
                  f"bit 一致={n_same}/{n_all}  差分={diffs}")
    return 0


def cmd_count(args) -> int:
    """★行為の数え上げ＝ベンチ全局で B-202 の分離が何席で採用値を返すか。"""
    import agents.heuristic_protagonist as hpm
    from arena.benchmark import benchmark_scripts, loops_to_win
    HP = hpm.HeuristicProtagonist
    old = HP.B202_UNREST_SUPPLY_SEP
    HP.B202_UNREST_SUPPLY_SEP = True
    inner = HP._b202_unrest_sep
    stat = {"評価回数": 0, "★発火": 0}
    hits: dict = {}
    cur = {"n": None}

    def counting(self, o, view):
        stat["評価回数"] += 1
        r = inner(self, o, view)
        if r is not None:
            stat["★発火"] += 1
            hits[cur["n"]] = hits.get(cur["n"], 0) + 1
        return r

    HP._b202_unrest_sep = counting
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            cur["n"] = f"{name}#{seed}"
            loops_to_win(sc, seed, loops=8)
    finally:
        HP._b202_unrest_sep = inner
        HP.B202_UNREST_SUPPLY_SEP = old
    print(f"days={args.days}  " + json.dumps(stat, ensure_ascii=False))
    print("  発火局: " + (json.dumps(hits, ensure_ascii=False) if hits else "なし"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-202 Phase 0 プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("why")
    p.add_argument("--log", default=str(LOG))
    p.add_argument("--watch", nargs="*", default=["医者", "巫女"])
    p.set_defaults(func=cmd_why)
    p = sub.add_parser("replay")
    p.add_argument("--log", default=None)
    p.add_argument("--no-b201", action="store_true",
                   help="B-201 を切って元の負け筋（L2 で終わらない）を再現する")
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("bit")
    p.add_argument("--log", default=None)
    p.set_defaults(func=cmd_bit)
    p = sub.add_parser("count")
    p.add_argument("--days", type=int, default=3)
    p.set_defaults(func=cmd_count)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
