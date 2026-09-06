# -*- coding: utf-8 -*-
"""B-231 A/B ドライバ：妹型の特性演繹（`agents.belief.B231_IMOUTO_TRAIT`）を比較する。

`arena/b227_ab.py` と同じ思想。ただし切替口は HeuristicProtagonist のクラス属性でなく
`agents.belief` の**モジュールフラグ**＝退避→上書き→復元で扱う。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm`（B-218 教訓＝perm 3条件）。
`--only`（`name#seed` のカンマ区切り）で対象局を絞れる＝発火局（妹∈cast）は両ベンチで
11局だけで、フラグは `"妹" in cast` で短絡するため**非該当局は構造的に bit 不変**
（`agents/belief.py` `_combo_weight_full` の分岐参照）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b231_ab --days 3 \
        --conds off,on --perm id --out docs/仮_b231_log/ab_d3_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import agents.belief as bl

CONDS: dict[str, dict] = {
    "off": {},
    "on": {"B231_IMOUTO_TRAIT": True},
}
_ATTRS = ("B231_IMOUTO_TRAIT",)


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id",
             only: set[str] | None = None) -> dict:
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    old = {a: getattr(bl, a) for a in _ATTRS}
    for k, v in CONDS[name].items():
        setattr(bl, k, v)
    install_perm(perm)
    rows = []
    try:
        for gname, seed, sc in benchmark_scripts(days=days):
            gid = f"{gname}#{seed}"
            if only is not None and gid not in only:
                continue
            n, outcome = loops_to_win(sc, seed, loops=loops)
            rows.append({"name": gname, "seed": seed, "loops_to_win": n,
                         "outcome": outcome})
    finally:
        uninstall_perm()
        for a, v in old.items():
            setattr(bl, a, v)
    n_def = sum(1 for r in rows if r["outcome"] == "defense")
    mean = (sum(r["loops_to_win"] for r in rows) / len(rows)) if rows else 0.0
    from collections import Counter
    outcomes = dict(Counter(r["outcome"] for r in rows))
    print(f"  [{days}日級 perm={perm} n={len(rows)}] {name}: 防衛={n_def} "
          f"平均={mean:.3f} 結末={outcomes}", flush=True)
    return {"rows": rows, "defense": n_def, "mean_loops_to_win": mean,
            "outcomes": outcomes}


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,on")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="off")
    ap.add_argument("--only", default="",
                    help="name#seed のカンマ区切り（空=全局）")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    only = set(x for x in a.only.split(",") if x) or None
    reps = {}
    for c in a.conds.split(","):
        reps[c] = run_cond(c, a.days, loops=a.loops, perm=a.perm, only=only)
    result = {"days": a.days, "perm": a.perm, "only": sorted(only or []),
              "conds": reps}
    if a.base in reps:
        for c in reps:
            if c != a.base:
                fl = flips(reps[a.base], reps[c])
                result.setdefault("flips", {})[c] = fl
                print(f"  flips {a.base}->{c}: {len(fl)} {fl}", flush=True)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
