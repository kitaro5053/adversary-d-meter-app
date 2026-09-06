# -*- coding: utf-8 -*-
"""B-245：census の JSON 群から 6条件の卓を作る（報告用の集計・読み取り専用）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b245_table docs/仮_b245_log
"""
from __future__ import annotations

import argparse
import glob
import json
import os

CONDS = [("d3", "id"), ("d3", "rev"), ("d3", "h1"),
         ("d5", "id"), ("d5", "rev"), ("d5", "h1")]
KEYS = ("open", "meet", "open_ml", "open_ml2", "gate2", "gate2_meet",
        "fc", "fc_merge", "obs_int1", "obs", "raw", "raw_meet")


def _load(d: str) -> dict:
    out: dict = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        base = os.path.basename(p)[:-5]
        parts = base.split("_")
        if len(parts) < 3:
            continue
        tag, days, perm = "_".join(parts[:-2]), parts[-2], parts[-1]
        with open(p, encoding="utf-8") as f:
            out[(tag, days, perm)] = json.load(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default="docs/仮_b245_log")
    args = ap.parse_args(argv)
    data = _load(args.dir)
    tags = sorted({t for t, _d, _p in data})
    for k in ("防衛", "平均") + KEYS:
        print(f"\n--- {k}")
        print(f"{'tag':22}" + "".join(f"{d}/{p:>4}" for d, p in CONDS))
        for tag in tags:
            row = f"{tag:22}"
            for d, p in CONDS:
                r = data.get((tag, d, p))
                if r is None:
                    row += f"{'-':>8}"
                elif k == "防衛":
                    row += f"{sum(1 for x in r['rows'] if x['outcome']=='defense'):>8}"
                elif k == "平均":
                    row += f"{round(sum(x['ltw'] for x in r['rows'])/len(r['rows']),3):>8}"
                else:
                    row += f"{r['grand'].get(k, '-'):>8}"
            print(row)
    # ★ノイズ対照点の |Δ|（base2 を基準に、noise_* の主指標の振れ幅）
    print("\n--- ★ノイズ対照点の |Δ|（主指標 meet／基準＝base2）")
    for tag in tags:
        if not tag.startswith("noise"):
            continue
        ds = []
        for d, p in CONDS:
            b, n = data.get(("base2", d, p)), data.get((tag, d, p))
            if b and n:
                ds.append(n["grand"]["meet"] - b["grand"]["meet"])
        if ds:
            print(f"  {tag:24} Δmeet={ds} |Δ|max={max(abs(x) for x in ds)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
