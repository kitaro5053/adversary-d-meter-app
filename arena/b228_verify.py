# -*- coding: utf-8 -*-
"""B-228 検算（読み取り専用）：保全 JSON から per-game flip 全数・防衛数・的の達成を判定。

- 的①（D 単独・絞りあり）＝5日級 rev/h1 の random_BTX#16 の D 由来喪失 → 0
- 的②（D 単独・絞りあり）＝id の取り分維持（FS#10・BTX#0・future#4）
- 的③（本丸）＝B+C+D（絞りあり）が 6測定で防衛数非退行
- ablation＝D（絞りあり） vs Dx（絞りなし）の per-game 差分の全数

実行（リポジトリ直下から・読み取り専用＝測定はしない）：
    PYTHONIOENCODING=utf-8 python -m arena.b228_verify
"""
import json
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "docs" / "仮_b228_log"

FILES = {
    ("d3", "id"): "ab_d3_id.json", ("d3", "rev"): "ab_d3_rev.json",
    ("d3", "h1"): "ab_d3_h1.json", ("d5", "id"): "ab_d5_id.json",
    ("d5", "rev"): "ab_d5_rev.json", ("d5", "h1"): "ab_d5_h1.json",
}
# 再走で埋めた欠け（d3 rev/h1 の Dx）
EXTRA = {("d3", "rev"): "ab_d3_rev_dx.json", ("d3", "h1"): "ab_d3_h1_dx.json"}

CANON = {"d3": (128, 2.762), "d5": (66, 3.386)}          # id の新正典値
OFF_DEF = {("d3", "id"): 128, ("d3", "rev"): 118, ("d3", "h1"): 119,
           ("d5", "id"): 66, ("d5", "rev"): 62, ("d5", "h1"): 67}


def label(r):
    return f"{r['script']}#{r['seed']}"


def key(r):
    return (r["script"], r["seed"])


def summarize(rows):
    n_def = sum(1 for r in rows if r["outcome"] == "defense")
    mean = sum(r["loops_to_win"] for r in rows) / len(rows)
    return n_def, round(mean, 3)


def flips(rows_a, rows_b):
    a = {key(r): r for r in rows_a}
    b = {key(r): r for r in rows_b}
    assert set(a) == set(b), "game set mismatch"
    out = []
    for k in sorted(a, key=lambda k: (k[0], k[1])):
        ra, rb = a[k], b[k]
        if (ra["loops_to_win"], ra["outcome"]) != (rb["loops_to_win"], rb["outcome"]):
            out.append((f"{k[0]}#{k[1]}",
                        f"{ra['loops_to_win']}[{ra['outcome']}]",
                        f"{rb['loops_to_win']}[{rb['outcome']}]"))
    return out


def main():
    data = {}
    for (bench, perm), fn in FILES.items():
        d = json.load(open(LOG / fn))
        if (bench, perm) in EXTRA and (LOG / EXTRA[(bench, perm)]).exists():
            dx = json.load(open(LOG / EXTRA[(bench, perm)]))
            # 再走 off と保全 off の bit 一致を検証してから Dx を採用
            f = flips(d["off"]["rows"], dx["off"]["rows"])
            print(f"[{bench} {perm}] 再走off vs 保全off flip={len(f)} "
                  f"{'★bit一致' if not f else '★★不一致: ' + str(f)}")
            d["Dx"] = dx["Dx"]
        data[(bench, perm)] = d

    ok = []
    print("\n== 集計値の検算（rows から再計算 vs 保全 txt/正典） ==")
    for (bench, perm), d in sorted(data.items()):
        line = [f"[{bench} {perm}]"]
        for cond in ("off", "D", "Dx", "BCD"):
            if cond not in d:
                line.append(f"{cond}=欠")
                continue
            nd, mean = summarize(d[cond]["rows"])
            rep_out = d[cond].get("outcomes", {})
            rep_nd = rep_out.get("defense")
            tag = "" if rep_nd == nd else f"★★JSON内outcomes({rep_nd})と不一致"
            line.append(f"{cond}={nd}({mean}){tag}")
        print("  " + " ".join(line))
        nd_off, mean_off = summarize(d["off"]["rows"])
        ok.append((f"off防衛[{bench} {perm}]", nd_off == OFF_DEF[(bench, perm)]))
        if perm == "id":
            c_nd, c_mean = CANON[bench]
            ok.append((f"off正典一致[{bench} id]",
                       nd_off == c_nd and abs(mean_off - c_mean) < 5e-4))

    print("\n== per-game flip 全数（off→D／off→Dx／off→BCD） ==")
    store = {}
    for (bench, perm), d in sorted(data.items()):
        for cond in ("D", "Dx", "BCD"):
            if cond not in d:
                continue
            fl = flips(d["off"]["rows"], d[cond]["rows"])
            store[(bench, perm, cond)] = fl
            print(f"  [{bench} {perm}] off→{cond}（{len(fl)}件）")
            for name, a, b in fl:
                print(f"    {name}: {a} → {b}")

    print("\n== ablation：D（絞りあり） vs Dx（絞りなし）の per-game 差分 ==")
    for (bench, perm), d in sorted(data.items()):
        if "Dx" not in d:
            print(f"  [{bench} {perm}] Dx 欠＝比較不能")
            continue
        fl = flips(d["D"]["rows"], d["Dx"]["rows"])
        print(f"  [{bench} {perm}] D vs Dx 差分（{len(fl)}件）")
        for name, a, b in fl:
            print(f"    {name}: D={a} / Dx={b}")

    print("\n== 的の判定 ==")
    # 的①：5日級 rev/h1 の BTX#16 が D（絞りあり）の flip に現れない（＝D由来喪失0）
    for perm in ("rev", "h1"):
        fl = store[("d5", perm, "D")]
        bad = [f for f in fl if f[0] == "random_BTX#16"]
        ok.append((f"的①[d5 {perm}] BTX#16 のD由来喪失0", not bad))
        # 対照：Dx では喪失が再現する（絞りの効果の実証）
        flx = store.get(("d5", perm, "Dx"))
        if flx is not None:
            badx = [f for f in flx if f[0] == "random_BTX#16"]
            ok.append((f"的①対照[d5 {perm}] Dx では BTX#16 喪失が再現", bool(badx)))
    # 的②：id の取り分維持
    fl5 = {f[0]: f for f in store[("d5", "id", "D")]}
    ok.append(("的②[d5 id] FS#10 9[loss]→4[def]",
               fl5.get("random_FS#10", ("", "", ""))[1:] == ("9[loss]", "4[defense]")))
    ok.append(("的②[d5 id] BTX#0 9[fb_loss]→4[def]",
               fl5.get("random_BTX#0", ("", "", ""))[1:] == ("9[fb_loss]", "4[defense]")))
    fl3 = {f[0]: f for f in store[("d3", "id", "D")]}
    ok.append(("的②[d3 id] future#4 改善flip維持",
               "btx_future#4" in fl3 and fl3["btx_future#4"][1] == "9[fb_win]"
               and "defense" in fl3["btx_future#4"][2]))
    # 的③：BCD が 6測定で防衛数非退行
    for (bench, perm), d in sorted(data.items()):
        nd_off, _ = summarize(d["off"]["rows"])
        nd_bcd, _ = summarize(d["BCD"]["rows"])
        ok.append((f"的③[{bench} {perm}] BCD {nd_bcd} ≥ off {nd_off}", nd_bcd >= nd_off))

    print()
    n_ng = 0
    for name, passed in ok:
        print(f"  {'PASS' if passed else '★★FAIL'}  {name}")
        n_ng += 0 if passed else 1
    print(f"\n判定合計: {len(ok)} 件中 FAIL {n_ng} 件")
    return 1 if n_ng else 0


if __name__ == "__main__":
    sys.exit(main())
