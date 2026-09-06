# -*- coding: utf-8 -*-
"""B-227 集計：`arena/b227_ab.py --out` の JSON 群から報告用の表を作る（オフライン）。

CLI:
    PYTHONIOENCODING=utf-8 python -m arena.b227_report docs/仮_b227_log
"""
from __future__ import annotations

import json
import os
import sys


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _nd(rows):
    return sum(1 for r in rows if r["outcome"] == "defense")


def _key(r):
    return f'{r["script"]}#{r["seed"]}'


def flips(base_rows, other_rows):
    by = {_key(r): r for r in base_rows}
    out = []
    for r in other_rows:
        b = by[_key(r)]
        if (b["loops_to_win"], b["outcome"]) != (r["loops_to_win"], r["outcome"]):
            out.append((_key(r), f'{b["loops_to_win"]}[{b["outcome"]}]',
                        f'{r["loops_to_win"]}[{r["outcome"]}]'))
    return out


def losses(base_rows, other_rows):
    """base で defense だったのに other で defense でなくなった局（防衛喪失）。"""
    return [(k, a, b) for (k, a, b) in flips(base_rows, other_rows)
            if "defense" in a and "defense" not in b]


def main(argv=None) -> int:
    d = (argv or sys.argv[1:])[0] if (argv or sys.argv[1:]) else "docs/仮_b227_log"
    tags = ["d3_id", "d5_id", "d3_rev", "d3_h1", "d5_rev", "d5_h1"]
    print("== 防衛数（off / i015b225 / band015b225 …） ==")
    for tag in tags + [t + "_full" for t in tags]:
        p = os.path.join(d, f"ab_{tag}.json")
        if not os.path.exists(p):
            continue
        data = _load(p)
        cells = []
        for cond, rep in data.items():
            cells.append(f"{cond}={_nd(rep['rows'])}"
                         f"({rep.get('mean', 0):.3f})")
        print(f"  [{tag}] " + "  ".join(cells))
    print("\n== per-game flip / 防衛喪失（基準は各ファイルの先頭条件） ==")
    for tag in tags + [t + "_full" for t in tags]:
        p = os.path.join(d, f"ab_{tag}.json")
        if not os.path.exists(p):
            continue
        data = _load(p)
        conds = list(data)
        base = conds[0]
        for cond in conds[1:]:
            fl = flips(data[base]["rows"], data[cond]["rows"])
            lo = losses(data[base]["rows"], data[cond]["rows"])
            print(f"  [{tag}] {base}→{cond}: flip={len(fl)} 喪失={len(lo)}")
            for k, a, b in fl:
                mark = " ★喪失" if (k, a, b) in lo else ""
                print(f"      {k}: {a} → {b}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
