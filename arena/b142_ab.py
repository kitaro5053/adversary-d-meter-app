# -*- coding: utf-8 -*-
"""B-142 の A/B・掃引ドライバ（切替口＝`HeuristicProtagonist.B142_RESERVE`）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §1）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**。
- per-game 差分（flip）を出す＝集計値は入れ替わりを隠す（規約 §5）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b142_ab --days 3 \\
        --values 0,10,20,30
    ... --perm id|rev|h1|h2 で `options` の並び順を置換（§11b の較正）
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _rows(days: int, loops: int, value: float, perm: str = "id") -> dict:
    old = HeuristicProtagonist.B142_RESERVE
    HeuristicProtagonist.B142_RESERVE = float(value)
    print(f"  [切替口] B142_RESERVE 既定={old} ／ 実効="
          f"{HeuristicProtagonist.B142_RESERVE}"
          f" ／ LAST_ONLY={HeuristicProtagonist.B142_RESERVE_LAST_ONLY}"
          f" ／ MIN_GAP={HeuristicProtagonist.B142_RESERVE_MIN_GAP}"
          f" ／ days={days} perm={perm}", flush=True)
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B142_RESERVE = old
        uninstall_perm()
    return rep


def count_fires(days: int, loops: int, value: float, perm: str = "id") -> dict:
    """予約述語が**何席で発火したか**を数える（挙動は変えない＝戻り値をそのまま返す）。

    ★§11b の「成果の示し方（強い順）1. 行為の数え上げ」＝並び順に依存しない証拠。
    `_b142_reserve_penalty` を薄いラッパで包み、戻り値が正の回数を数えるだけ。
    """
    from arena.tie_noise import install_perm, uninstall_perm

    orig = HeuristicProtagonist._b142_reserve_penalty
    n = {"fire": 0, "call": 0}

    def wrapped(self, view, tgt, c, th, d, danger):
        out = orig(self, view, tgt, c, th, d, danger)
        n["call"] += 1
        if out:
            n["fire"] += 1
        return out

    old = HeuristicProtagonist.B142_RESERVE
    HeuristicProtagonist.B142_RESERVE = float(value)
    HeuristicProtagonist._b142_reserve_penalty = wrapped
    print(f"  [切替口] B142_RESERVE 実効={HeuristicProtagonist.B142_RESERVE}"
          f" ／ days={days} perm={perm}（発火数の計測）", flush=True)
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B142_RESERVE = old
        HeuristicProtagonist._b142_reserve_penalty = orig
        uninstall_perm()
    return {"fires": n["fire"], "calls": n["call"], "rep": rep}


def _summary(rep: dict) -> str:
    d = rep["outcomes"]
    return (f"防衛={d.get('defense', 0)}/{rep['n_games']}"
            f" 平均={rep['mean_loops_to_win']}"
            f" L1={rep['distribution'].get('1', 0)}"
            f" loss={d.get('loss', 0)} fb_loss={d.get('fb_loss', 0)}"
            f" fb_win={d.get('fb_win', 0)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="0,20")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--json", default=None)
    ap.add_argument("--fires", action="store_true",
                    help="予約述語の発火席数だけを数える（挙動不変）")
    a = ap.parse_args(argv)
    if a.fires:
        for v in [float(x) for x in a.values.split(",") if x.strip()]:
            r = count_fires(a.days, a.loops, v, perm=a.perm)
            print(f"  発火席数={r['fires']} / 述語の呼び出し={r['calls']}"
                  f" / {_summary(r['rep'])}", flush=True)
        return 0
    vals = [float(x) for x in a.values.split(",") if x.strip() != ""]
    out = {}
    base_rows = None
    for v in vals:
        rep = _rows(a.days, a.loops, v, perm=a.perm)
        rows = {(r["script"], r["seed"]): r["loops_to_win"] for r in rep["rows"]}
        print(f"  B142_RESERVE={v}: {_summary(rep)}", flush=True)
        if base_rows is None:
            base_rows = rows
        else:
            flips = sorted((k, base_rows[k], rows[k]) for k in rows
                           if base_rows[k] != rows[k])
            print(f"    flip={len(flips)}: "
                  + " ／ ".join(f"{s}(s{sd}) {a0}→{b0}"
                                for (s, sd), a0, b0 in flips), flush=True)
        out[str(v)] = {"outcomes": rep["outcomes"],
                       "mean": rep["mean_loops_to_win"],
                       "dist": rep["distribution"],
                       "rows": {f"{s}|{sd}": v2 for (s, sd), v2 in rows.items()}}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
