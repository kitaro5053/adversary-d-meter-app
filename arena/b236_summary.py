# -*- coding: utf-8 -*-
"""B-236 集計＋相互作用の同定（読み取り専用・対局を回さない）。

`arena.b236_ab` が出した json を読み、
(1) 条件×(ベンチ,perm) の防衛数／平均の表
(2) **相互作用の全数**＝「組の flip 集合」対「単独 flip の和集合」のズレ
    ＋「組の防衛数」対「単独の効き（off 基準の差）の和」のズレ
を出す。★±1 の解釈はしない（数え上げるだけ）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b236_summary docs/仮_b236_log
"""
from __future__ import annotations

import glob
import json
import os
import sys
from itertools import combinations

ORDER = ("b230", "b231", "b232", "b234")


def load(dirpath: str) -> dict:
    out = {}
    for p in sorted(glob.glob(os.path.join(dirpath, "ab_d*_*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        out[(d["days"], d["perm"])] = d
    return out


def knobs_of(cond: str) -> frozenset:
    b = cond.split("@")[0]
    if b == "off":
        return frozenset()
    if b == "all":
        return frozenset(ORDER)
    return frozenset(b.split("+"))


def name_of(ks) -> str:
    ks = frozenset(ks)
    if not ks:
        return "off"
    if len(ks) == 4:
        return "all"
    return "+".join(k for k in ORDER if k in ks)


def flipset(d: dict, cond: str) -> dict:
    """cond の off 基準 flip を {game: (base, cond)} で返す。"""
    if cond == "off":
        return {}
    fl = d.get("flips", {}).get(cond, [])
    return {f["game"]: (f["base"], f["cond"]) for f in fl}


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    dirpath = argv[0] if argv else "docs/仮_b236_log"
    data = load(dirpath)
    if not data:
        print(f"json が見つかりません: {dirpath}")
        return 1
    keys = sorted(data, key=lambda k: (k[0], ["id", "rev", "h1"].index(k[1])))
    conds = [c for c in data[keys[0]]["conds"] if "@" not in c]

    # (1) 表
    hdr = "条件".ljust(20) + "".join(f"{d}日{p}".rjust(12) for d, p in keys)
    print("## 防衛数（平均 ltw）")
    print(hdr)
    for c in conds:
        row = c.ljust(20)
        for k in keys:
            r = data[k]["conds"].get(c)
            row += (f"{r['defense']} ({r['mean_loops_to_win']:.3f})".rjust(12)
                    if r else "-".rjust(12))
        print(row)

    print("\n## 合算（防衛数の 6条件合計 / 5日級3条件 / 3日級3条件）")
    for c in conds:
        tot = sum(data[k]["conds"][c]["defense"] for k in keys if c in data[k]["conds"])
        d5 = sum(data[k]["conds"][c]["defense"] for k in keys
                 if k[0] == 5 and c in data[k]["conds"])
        d3 = sum(data[k]["conds"][c]["defense"] for k in keys
                 if k[0] == 3 and c in data[k]["conds"])
        print(f"  {c.ljust(20)} 合計={tot}  5日級計={d5}  3日級計={d3}")

    # (2) 相互作用
    print("\n## 相互作用の全数（対・3つ組・4つ組／条件ごと）")
    n_int = 0
    for k in keys:
        d = data[k]
        for r in range(2, 5):
            for combo in combinations(ORDER, r):
                cn = name_of(combo)
                if cn not in d["conds"]:
                    continue
                # 期待＝構成要素（単独）の flip の和集合
                union = {}
                clash = {}
                for s in combo:
                    for g, v in flipset(d, s).items():
                        if g in union and union[g] != v:
                            clash[g] = (union[g], v)
                        union[g] = v
                actual = flipset(d, cn)
                miss = {g: v for g, v in union.items() if g not in actual}
                extra = {g: v for g, v in actual.items() if g not in union}
                diff = {g: (union[g], actual[g]) for g in actual
                        if g in union and union[g] != actual[g]}
                exp_def = (d["conds"]["off"]["defense"]
                           + sum(d["conds"][s]["defense"] - d["conds"]["off"]["defense"]
                                 for s in combo))
                got_def = d["conds"][cn]["defense"]
                if miss or extra or diff or clash or exp_def != got_def:
                    n_int += 1
                    print(f"  [{k[0]}日{k[1]}] {cn}: 防衛 期待(加法)={exp_def} 実測={got_def}")
                    if clash:
                        print(f"      ★単独どうしが同じ局を触っている: {clash}")
                    if miss:
                        print(f"      消えた flip: {miss}")
                    if extra:
                        print(f"      増えた flip: {extra}")
                    if diff:
                        print(f"      値が違う flip(単独和→実測): {diff}")
    if not n_int:
        print("  なし＝全条件で加法的")
    else:
        print(f"  → 非加法の組 {n_int} 件")

    # (3) 状態漏れ検査
    print("\n## 状態漏れ検査（off@2）")
    for k in keys:
        d = data[k]
        if "off@2" in d.get("flips", {}):
            print(f"  [{k[0]}日{k[1]}] off@2 の flip={len(d['flips']['off@2'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
