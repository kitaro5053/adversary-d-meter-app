# -*- coding: utf-8 -*-
"""B-208 の A/B・掃引ドライバ（能力供給役の固定・分離）。

切替口＝`agents.heuristic_protagonist.HeuristicProtagonist`
  - `B208_SUPPLIER_LOCK`（bool・既定 True）／`_B208_SAVE_PIN`（bool）／
    `PRIORITY["能力供給役_締め出し"]`（点）
- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires`＝**行為の数え上げ**（規約 §11b）＝述語が発火した席数と実際に採られた席数。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b208_ab --days 3 \\
        --values off,60,70,80 --fires
    ... --values off,60,60nopin      # nopin＝`_B208_SAVE_PIN=False`
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from agents.heuristic_protagonist import PRIORITY
from arena.benchmark import run_benchmark

_KEY = "能力供給役_締め出し"
_DEFAULT = PRIORITY[_KEY]


def _parse(v: str):
    s = (v or "").strip().lower()
    if s in ("off", "none", ""):
        return {"on": False, "score": _DEFAULT, "save_pin": True}
    save_pin = True
    if s.endswith("nopin"):
        save_pin = False
        s = s[:-5]
    return {"on": True, "score": float(s) if s else _DEFAULT,
            "save_pin": save_pin}


def _apply(val) -> None:
    """★どの点でも全切替口を全部書く（前の点の残留で汚染しない）。"""
    H = HeuristicProtagonist
    H.B208_SUPPLIER_LOCK = bool(val["on"])
    H._B208_SAVE_PIN = bool(val["save_pin"])
    PRIORITY[_KEY] = float(val["score"])


def _switch_line(days: int) -> str:
    H = HeuristicProtagonist
    return (f"  [切替口] B208_SUPPLIER_LOCK 実効={H.B208_SUPPLIER_LOCK}"
            f" ／ _B208_SAVE_PIN={H._B208_SAVE_PIN}"
            f" ／ PRIORITY[{_KEY}]={PRIORITY[_KEY]}"
            f" ／ _B208_MIN_OBS={H._B208_MIN_OBS}"
            f" ／ _B208_ML_EPS={H._B208_ML_EPS}"
            f" ／ B202_UNREST_SUPPLY_SEP={H.B202_UNREST_SUPPLY_SEP}"
            f" ／ B196_ABILITY_SUPPLY_SEP={H.B196_ABILITY_SUPPLY_SEP}"
            f" ／ B206_TELEPORT_BAIT={H.B206_TELEPORT_BAIT}"
            f" ／ days={days}")


def _run(days: int, loops: int, val, fires: bool = False) -> dict:
    old = (HeuristicProtagonist.B208_SUPPLIER_LOCK,
           HeuristicProtagonist._B208_SAVE_PIN, PRIORITY[_KEY])
    _apply(val)
    print(_switch_line(days), flush=True)
    n = {"述語が発火した席": 0, "その手を採った席": 0, "発火した手の総数": 0}
    orig_lock = HeuristicProtagonist._b208_supplier_lock
    orig_decide = HeuristicProtagonist.decide

    def wrap_decide(self, view, decision, options, _n=n):
        best = orig_decide(self, view, decision, options)
        if decision == "set_card" and self.B208_SUPPLIER_LOCK:
            hit = [o for o in options if orig_lock(self, o, view) is not None]
            if hit:
                _n["述語が発火した席"] += 1
                _n["発火した手の総数"] += len(hit)
                if any((best["card"], best["target"]) == (o["card"], o["target"])
                       for o in hit):
                    _n["その手を採った席"] += 1
        return best

    try:
        if fires:
            HeuristicProtagonist.decide = wrap_decide
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if fires:
            HeuristicProtagonist.decide = orig_decide
        (HeuristicProtagonist.B208_SUPPLIER_LOCK,
         HeuristicProtagonist._B208_SAVE_PIN, PRIORITY[_KEY]) = old
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
            out.append(f"    {k[0]}#{k[1]}: {ra['loops_to_win']}"
                       f"[{ra['outcome']}] → {rb['loops_to_win']}[{rb['outcome']}]")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-208 A/B・掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,60")
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
