# -*- coding: utf-8 -*-
"""B-143 の A/B・掃引ドライバ（切替口＝`HeuristicProtagonist.B143_YIELD`）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §1・§4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**。
- per-game 差分（flip）を出す＝集計値は入れ替わりを隠す（規約 §5）。
- `--fires` ＝述語の発火数（行為の数え上げ＝並び順に依存しない証拠・§11b）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b143_ab --days 3 \\
        --values 0,5,10,20
    ... --perm id|rev|h1|h2 で `options` の並び順を置換（§11b の較正）
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _switches() -> str:
    P = HeuristicProtagonist
    return (f"B143_YIELD={P.B143_YIELD}"
            f" / B143_YIELD_FAMS={P.B143_YIELD_FAMS}"
            f" / B142_RESERVE={P.B142_RESERVE}"
            f" / B141B_UNLOCK_SAME_DAY={P.B141B_UNLOCK_SAME_DAY}")


def _rows(days: int, loops: int, value: float, perm: str = "id") -> dict:
    old = HeuristicProtagonist.B143_YIELD
    HeuristicProtagonist.B143_YIELD = float(value)
    print(f"  [切替口] B143_YIELD クラス既定={old} ／ 実効={_switches()}"
          f" ／ days={days} perm={perm}", flush=True)
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B143_YIELD = old
        uninstall_perm()
    return rep


def count_fires(days: int, loops: int, value: float, perm: str = "id") -> dict:
    """減点述語が**何回発火したか**（挙動は変えない＝戻り値をそのまま返す）。"""
    from arena.tie_noise import install_perm, uninstall_perm

    orig = HeuristicProtagonist._b143_yield_penalty
    n = {"fire": 0, "call": 0}

    def wrapped(self, view, tgt, unlock):
        out = orig(self, view, tgt, unlock)
        n["call"] += 1
        if out:
            n["fire"] += 1
        return out

    old = HeuristicProtagonist.B143_YIELD
    HeuristicProtagonist.B143_YIELD = float(value)
    HeuristicProtagonist._b143_yield_penalty = wrapped
    print(f"  [切替口] {_switches()} ／ days={days} perm={perm}（発火数の計測）",
          flush=True)
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B143_YIELD = old
        HeuristicProtagonist._b143_yield_penalty = orig
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
    ap.add_argument("--values", default="0,10")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--fams", default=None,
                    help="発火させる族の部分集合（例 'A' / 'AB' / 'ABC'）")
    ap.add_argument("--json", default=None)
    ap.add_argument("--fires", action="store_true")
    ap.add_argument("--unlock-same-day", action="store_true",
                    help="★測定条件として B-141b の版 (b) を ON にする"
                         "（既定値は変更しない＝land 対象外・報告に必ず明記）")
    a = ap.parse_args(argv)
    if a.unlock_same_day:
        HeuristicProtagonist.B141B_UNLOCK_SAME_DAY = True
    if a.fams is not None:
        HeuristicProtagonist.B143_YIELD_FAMS = a.fams
    if a.fires:
        for v in [float(x) for x in a.values.split(",") if x.strip()]:
            r = count_fires(a.days, a.loops, v, perm=a.perm)
            print(f"  発火={r['fires']} / 呼び出し={r['calls']}"
                  f" / {_summary(r['rep'])}", flush=True)
        return 0
    vals = [float(x) for x in a.values.split(",") if x.strip() != ""]
    out = {}
    base_rows = None
    for v in vals:
        rep = _rows(a.days, a.loops, v, perm=a.perm)
        rows = {(r["script"], r["seed"]): r["loops_to_win"] for r in rep["rows"]}
        print(f"  B143_YIELD={v}: {_summary(rep)}", flush=True)
        if base_rows is None:
            base_rows = rows
        else:
            flips = sorted((k, base_rows[k], rows[k]) for k in rows
                           if base_rows[k] != rows[k])
            good = sum(1 for _k, x, y in flips if y < x)
            bad = sum(1 for _k, x, y in flips if y > x)
            print(f"    flip={len(flips)}（改善{good}／退行{bad}）: "
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
