# -*- coding: utf-8 -*-
"""B-198 の A/B・掃引ドライバ（臨界到達済みの犯人候補にも 1/L 冷却札の予約を効かせる）。

切替口＝`agents.heuristic_protagonist.HeuristicProtagonist`
  - `B198_COOL_RESERVE_AT_TH`（bool・既定 False）
  - `B198_RESERVE`（float|None・None＝`B142_RESERVE` と同値。**新しい枝だけ**を振る掃引口）

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires`＝**行為の数え上げ**（規約 §11b）＝新しい枝が発火した席数と、
  その席で `不安-1` が実際に採られたか（＝予約が効いたか）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b198_ab --days 5 \\
        --values off,on --fires
    ... --values off,5,10,15,20,25,30,40      # 点の掃引（B198_RESERVE）
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _parse(v: str):
    s = (v or "").strip().lower()
    if s in ("off", "none", ""):
        return {"on": False, "score": None}
    if s == "on":
        return {"on": True, "score": None}
    return {"on": True, "score": float(s)}


def _apply(val) -> None:
    """★どの点でも全切替口を全部書く（前の点の残留で汚染しない）。"""
    H = HeuristicProtagonist
    H.B198_COOL_RESERVE_AT_TH = bool(val["on"])
    H.B198_RESERVE = val["score"]


def _switch_line(days: int) -> str:
    H = HeuristicProtagonist
    return (f"  [切替口] B198_COOL_RESERVE_AT_TH 実効={H.B198_COOL_RESERVE_AT_TH}"
            f" ／ B198_RESERVE={H.B198_RESERVE}"
            f" ／ _B198_MIN_TH={H._B198_MIN_TH}"
            f" ／ B142_RESERVE={H.B142_RESERVE}"
            f" ／ B142_RESERVE_LAST_ONLY={H.B142_RESERVE_LAST_ONLY}"
            f" ／ B142_RESERVE_MIN_GAP={H.B142_RESERVE_MIN_GAP}"
            f" ／ days={days}")


def _run(days: int, loops: int, val, fires: bool = False) -> dict:
    old = (HeuristicProtagonist.B198_COOL_RESERVE_AT_TH,
           HeuristicProtagonist.B198_RESERVE)
    _apply(val)
    print(_switch_line(days), flush=True)
    n = {"新枝が発火した席": 0, "その席で不安-1を採った": 0}
    orig_pen = HeuristicProtagonist._b142_reserve_penalty
    orig_decide = HeuristicProtagonist.decide
    seen: dict = {}

    def wrap_pen(self, view, tgt, c, th, d, danger, _n=n, _seen=seen):
        got = orig_pen(self, view, tgt, c, th, d, danger)
        if got and c.get("unrest", 0) >= th:
            _seen[(view.get("loop"), view.get("day"), view.get("seat"))] = True
        return got

    def wrap_decide(self, view, decision, options, _n=n, _seen=seen):
        key = (view.get("loop"), view.get("day"), view.get("seat"))
        _seen.pop(key, None)
        best = orig_decide(self, view, decision, options)
        if decision == "set_card" and _seen.pop(key, None):
            _n["新枝が発火した席"] += 1
            if best.get("card") == "不安-1":
                _n["その席で不安-1を採った"] += 1
        return best

    try:
        if fires:
            HeuristicProtagonist._b142_reserve_penalty = wrap_pen
            HeuristicProtagonist.decide = wrap_decide
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if fires:
            HeuristicProtagonist._b142_reserve_penalty = orig_pen
            HeuristicProtagonist.decide = orig_decide
        (HeuristicProtagonist.B198_COOL_RESERVE_AT_TH,
         HeuristicProtagonist.B198_RESERVE) = old
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
    ap = argparse.ArgumentParser(description="B-198 A/B・掃引")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,on")
    ap.add_argument("--fires", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--minth", type=int, default=None,
                    help="`_B198_MIN_TH`（新枝を適用する不安臨界の下限）。"
                         "2＝臨界1の縮退帯を外す")
    ap.add_argument("--perm", type=str, default="id",
                    help="摂動耐性＝options の並べ替え（arena.tie_noise.permute）。"
                         "採点は無改変＝同点帯の解け方だけが変わる")
    a = ap.parse_args(argv)

    if a.minth is not None:
        HeuristicProtagonist._B198_MIN_TH = int(a.minth)
    if a.perm not in ("id", ""):
        from arena.tie_noise import install_perm
        install_perm(a.perm)
        print(f"  [摂動] options 並べ替え perm={a.perm}", flush=True)

    base = None
    dump = {}
    for v in [x for x in a.values.split(",") if x.strip()]:
        rep = _run(a.days, a.loops, _parse(v), fires=a.fires)
        dump[v] = rep.get("rows", [])
        print(f"[{v}] {_summary(rep)}", flush=True)
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
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
