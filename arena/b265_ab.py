# -*- coding: utf-8 -*-
"""B-265 A/B ドライバ（★測定専用・実装も既定値も一切触らない）。

`arena/b253_ab.py` と同型（フラグは実行時に退避→上書き→復元＝ファイルは不変）。

条件に使える切替口（★**既定 OFF のものだけ**を載せる）：
- `act` ＝ `HeuristicProtagonist.B265_KURO_ACTIONABLE`
  ＝クロマク移動語彙（隔離／剥がし／剥がし_候補）の候補集合を
  「**手が実際に打てる相手**」に条件付ける（B-265・§72-73）。

`--floor` は `_B265_CAND_FLOOR`（float＝掃引口・既定 0.3）を**非 off 条件にだけ**適用する。
★§72-61 の教訓＝**一意化条件は床について単調ではない**ので、床は必ず掃引する。
★脚本別平均を必ず別掲する（`btx_seal_cat`／`btx5_seal_cat` が本チケットの直接の的）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b265_ab \
        --days 3 --conds off,act --out /tmp/b265/ab_d3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

#: 条件名 → (置き場, 属性名)。★全項目のリポジトリ既定は False でなければならない。
KNOBS: dict[str, tuple[str, str]] = {
    "act": ("hp", "B265_KURO_ACTIONABLE"),
}
ORDER = ("act",)
_FLOOR_ATTR = "_B265_CAND_FLOOR"


def _holders():
    from agents import HeuristicProtagonist as HP
    return {"hp": HP}


def cond_knobs(name: str) -> tuple[str, ...]:
    base = name.split("@")[0]
    if base in ("off", "none"):
        return ()
    if base == "all":
        return ORDER
    parts = [p for p in base.split("+") if p]
    for p in parts:
        if p not in KNOBS:
            raise SystemExit(f"未知の切替口: {p}（条件名 {name}）")
    return tuple(k for k in ORDER if k in parts)


_DEFAULTS_CHECKED = False


def _check_defaults() -> None:
    """★不変条件＝KNOBS に載る切替口は**リポジトリ既定が False**（B-248 の共有検査）。"""
    global _DEFAULTS_CHECKED
    if _DEFAULTS_CHECKED:
        return
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), (), driver="b265_ab")
    _DEFAULTS_CHECKED = True


def _apply(knobs, floor: float | None) -> dict:
    _check_defaults()
    holders = _holders()
    on = set(knobs)
    old = {}
    for k, (where, attr) in KNOBS.items():
        old[k] = getattr(holders[where], attr)
        setattr(holders[where], attr, k in on)
    old[_FLOOR_ATTR] = getattr(holders["hp"], _FLOOR_ATTR)
    if on and floor is not None:            # ★off 条件には触らない（ベースライン保護）
        setattr(holders["hp"], _FLOOR_ATTR, float(floor))
    return old


def _restore(old: dict):
    holders = _holders()
    for k, (where, attr) in KNOBS.items():
        setattr(holders[where], attr, old[k])
    setattr(holders["hp"], _FLOOR_ATTR, old[_FLOOR_ATTR])


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id",
             floor: float | None = None) -> dict:
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    old = _apply(cond_knobs(name), floor)
    install_perm(perm)
    t0 = time.time()
    rows = []
    try:
        for gname, seed, sc in benchmark_scripts(days=days):
            n, outcome = loops_to_win(sc, seed, loops=loops)
            rows.append({"name": gname, "seed": seed, "loops_to_win": n,
                         "outcome": outcome})
    finally:
        uninstall_perm()
        _restore(old)
    by_script: dict = {}
    for r in rows:
        by_script.setdefault(r["name"], []).append(r["loops_to_win"])
    by_script = {k: round(sum(v) / len(v), 3) for k, v in sorted(by_script.items())}
    n_def = sum(1 for r in rows if r["outcome"] == "defense")
    mean = (sum(r["loops_to_win"] for r in rows) / len(rows)) if rows else 0.0
    outcomes = dict(Counter(r["outcome"] for r in rows))
    dist = dict(Counter(r["loops_to_win"] for r in rows))
    print(f"  [{days}日級 perm={perm} n={len(rows)}] {name}(floor={floor}): "
          f"防衛={n_def} 平均={mean:.3f} 結末={outcomes} "
          f"分布={dict(sorted(dist.items()))} ({time.time() - t0:.0f}s)", flush=True)
    print(f"    脚本別平均: {by_script}", flush=True)
    return {"knobs": list(cond_knobs(name)), "floor": floor, "rows": rows,
            "by_script": by_script,
            "defense": n_def, "mean_loops_to_win": mean, "outcomes": outcomes,
            "dist": {str(k): v for k, v in sorted(dist.items())},
            "secs": round(time.time() - t0, 1)}


def flips(base_rep: dict, rep: dict) -> list[dict]:
    b = {(r["name"], r["seed"]): r for r in base_rep["rows"]}
    out = []
    for r in rep["rows"]:
        rb = b.get((r["name"], r["seed"]))
        if rb and (rb["loops_to_win"] != r["loops_to_win"]
                   or rb["outcome"] != r["outcome"]):
            out.append({"game": f"{r['name']}#{r['seed']}",
                        "base": f"{rb['loops_to_win']}({rb['outcome']})",
                        "cond": f"{r['loops_to_win']}({r['outcome']})"})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-265 A/B ドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,act")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="off")
    ap.add_argument("--floor", type=float, default=None)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    reps = {c: run_cond(c, a.days, loops=a.loops, perm=a.perm, floor=a.floor)
            for c in (c for c in a.conds.split(",") if c)}
    result = {"days": a.days, "perm": a.perm, "loops": a.loops,
              "floor": a.floor, "conds": reps}
    if a.base in reps:
        for c in reps:
            if c != a.base:
                fl = flips(reps[a.base], reps[c])
                result.setdefault("flips", {})[c] = fl
                print(f"  flips {a.base}->{c}: {len(fl)} {fl}", flush=True)
    if a.out:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
