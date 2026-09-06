# -*- coding: utf-8 -*-
"""B-245：census の JSON 2本を per-game で突き合わせる（flip 検死の入口）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 \
        python -m arena.b245_diff docs/仮_b245_log/base2_d3_id.json \
                                  docs/仮_b245_log/on_d3_id.json

`--metric` を付けると主指標の卓（6条件）を作る（複数の JSON をまとめて表にする）。
"""
from __future__ import annotations

import argparse
import json

KEYS = ("open", "meet", "open_ml", "open_ml2", "gate2", "gate2_meet",
        "fc", "fc_merge", "obs", "obs_int1", "raw", "raw_meet")


def _load(p: str) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cmd_diff(a: str, b: str) -> int:
    da, db = _load(a), _load(b)
    ra = {(r["script"], r["seed"]): r for r in da["rows"]}
    rb = {(r["script"], r["seed"]): r for r in db["rows"]}
    assert ra.keys() == rb.keys(), "母集団が違う（days/perm が食い違っている）"
    flips = []
    for k in sorted(ra, key=lambda k: (k[0], k[1])):
        if ra[k]["ltw"] != rb[k]["ltw"] or ra[k]["outcome"] != rb[k]["outcome"]:
            flips.append((k, ra[k], rb[k]))
    print(f"A={a}\nB={b}")
    print(f"  防衛 {sum(1 for r in da['rows'] if r['outcome']=='defense')}"
          f" → {sum(1 for r in db['rows'] if r['outcome']=='defense')}"
          f" ／ 平均 {round(sum(r['ltw'] for r in da['rows'])/len(da['rows']),3)}"
          f" → {round(sum(r['ltw'] for r in db['rows'])/len(db['rows']),3)}")
    for k in KEYS:
        va, vb = da["grand"].get(k), db["grand"].get(k)
        if va != vb:
            print(f"  {k}: {va} → {vb}  (Δ={None if va is None or vb is None else vb-va})")
    print(f"  ★per-game flip = {len(flips)}")
    for (nm, sd), x, y in flips:
        print(f"    {nm}#{sd}: {x['ltw']} {x['outcome']} → {y['ltw']} {y['outcome']}")
    # 指標だけが動いた局（flip していないが行為が変わった＝機序の手がかり）
    moved = [k for k in sorted(ra, key=lambda k: (k[0], k[1]))
             if any(ra[k].get(m) != rb[k].get(m) for m in KEYS)]
    print(f"  指標が動いた局 = {len(moved)}: "
          + ", ".join(f"{n}#{s}" for n, s in moved[:20]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    return cmd_diff(*vars(ap.parse_args(argv)).values())


if __name__ == "__main__":
    raise SystemExit(main())
