# -*- coding: utf-8 -*-
"""B-66 再設計の A/B ドライバ（囮判定を結果カウンター→供給の現物へ）。

切替口＝`agents.heuristic_protagonist.HeuristicProtagonist.B66_SUPPLY_OBSERVED`
（bool・既定 False＝OFF）。ON で `_b66_decoy_from_supply` が
「過去ループに mm が暗躍札を投じた実績のある板」を囮集合から外す。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires`＝行為の数え上げ（規約 §11b）＝減点の対象になった席数と、
  そのうち**実際に板ガードを選べるようになった席**（＝手が変わった席）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b66_ab --days 3 \\
        --values off,on --fires
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _switch_line(days: int) -> str:
    H = HeuristicProtagonist
    return (f"  [切替口] B66_SUPPLY_OBSERVED 実効={H.B66_SUPPLY_OBSERVED}"
            f" ／ _B66_RESCUE_SCORE={H._B66_RESCUE_SCORE}"
            f" ／ B204_ZERO_ALLOWANCE_GUARD={H.B204_ZERO_ALLOWANCE_GUARD}"
            f" ／ B205_KINSHI_GUARD={H.B205_KINSHI_GUARD}"
            f" ／ B206_TELEPORT_BAIT={H.B206_TELEPORT_BAIT}"
            f" ／ days={days}")


def _parse(v: str) -> dict:
    """点の指定。"off"＝両方 OFF ／ "on"＝B-66 再設計のみ ON ／
    "on-nb204"＝B-66 再設計 ON かつ B-204 撤去相当（冗長性の実証）。"""
    s = (v or "").strip().lower()
    if s in ("off", "none", ""):
        return {"b66": False, "b204": True, "score": None}
    if s == "on":
        return {"b66": True, "b204": True, "score": None}
    if s in ("on-nb204", "on_nb204"):
        return {"b66": True, "b204": False, "score": None}
    if s == "off-nb204":
        return {"b66": False, "b204": False, "score": None}
    if s.startswith("on"):        # on80 / on86.5 … 救済点の掃引
        try:
            return {"b66": True, "b204": True, "score": float(s[2:])}
        except ValueError:
            pass
    raise SystemExit(f"未知の点: {v}")


def _run(days: int, loops: int, val: dict, fires: bool = False) -> dict:
    H = HeuristicProtagonist
    old = (H.B66_SUPPLY_OBSERVED, H.B204_ZERO_ALLOWANCE_GUARD, H._B66_RESCUE_SCORE)
    H.B66_SUPPLY_OBSERVED = bool(val["b66"])
    H.B204_ZERO_ALLOWANCE_GUARD = bool(val["b204"])
    H._B66_RESCUE_SCORE = val.get("score")
    print(_switch_line(days), flush=True)
    n = {"囮集合が非空のターン": 0, "旧集合で囮判定された板×ターン": 0,
         "新集合で囮判定された板×ターン": 0, "★救済された板×ターン": 0}
    orig = H._b66_decoy_from_supply

    def wrap(self, view, unproven, _n=n):
        r = orig(self, view, unproven)
        if unproven:
            _n["囮集合が非空のターン"] += 1
            _n["旧集合で囮判定された板×ターン"] += len(unproven)
            _n["新集合で囮判定された板×ターン"] += len(r)
            _n["★救済された板×ターン"] += len(set(unproven) - set(r))
        return r

    try:
        if fires:
            H._b66_decoy_from_supply = wrap
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if fires:
            H._b66_decoy_from_supply = orig
        (H.B66_SUPPLY_OBSERVED, H.B204_ZERO_ALLOWANCE_GUARD,
         H._B66_RESCUE_SCORE) = old
    if fires:
        rep["_fires"] = dict(n)
    return rep


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def _rows(rep: dict) -> dict:
    return {(r["script"], r["seed"]): r for r in rep.get("rows", [])}


def _flips(base: dict, new: dict) -> list[str]:
    a, b = _rows(base), _rows(new)
    out = []
    for k in sorted(a.keys() | b.keys()):
        ra, rb = a.get(k), b.get(k)
        if ra is None or rb is None:
            out.append(f"    {k[0]}#{k[1]}: 片側のみ")
            continue
        if (ra["loops_to_win"], ra["outcome"]) != (rb["loops_to_win"], rb["outcome"]):
            mark = "改善" if rb["loops_to_win"] < ra["loops_to_win"] else "退行"
            out.append(f"    [{mark}] {k[0]}#{k[1]}: {ra['loops_to_win']}"
                       f"[{ra['outcome']}] → {rb['loops_to_win']}[{rb['outcome']}]")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-66 再設計 A/B")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,on")
    ap.add_argument("--fires", action="store_true")
    a = ap.parse_args(argv)

    base = None
    for v in [x for x in a.values.split(",") if x.strip()]:
        rep = _run(a.days, a.loops, _parse(v), fires=a.fires)
        print(f"[{v}] {_summary(rep)}")
        if a.fires:
            print("  行為の数え上げ: "
                  + json.dumps(rep.get("_fires", {}), ensure_ascii=False))
        if base is None:
            base = rep
            print("  （基準点）")
        else:
            fl = _flips(base, rep)
            print(f"  flip={len(fl)}件")
            for line in fl:
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
