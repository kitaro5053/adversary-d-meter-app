# -*- coding: utf-8 -*-
"""B-237 A/B ドライバ：`B237_SPLIT_REDUNDANT` を足した5切替口版（測定専用）。

`arena/b236_ab.py` の 4切替口版に **5つ目の切替口**（本レーンの新設・既定 OFF）を足す。
条件名の書式は b236 と同じ（`b230+b232` 等）。★本レーンの現行既定は
**b230/b231/b232/b234 が ON**（§72-28）なので、条件を明示しない `base` は
「4つ ON ＋ b237 OFF」を指す（＝現行正典のベースライン）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b237_ab \
        --days 5 --perm id --conds base,base+b237 --out docs/仮_b237_log/ab_d5_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

# 切替口 → (モジュール/クラスのどちら側か, 属性名)
KNOBS: dict[str, tuple[str, str]] = {
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
    "b232": ("hp", "B232_COOL_MATH"),
    "b234": ("bl", "B234_GOSHINBOKU_TRAIT"),
    "b237": ("hp", "B237_SPLIT_REDUNDANT"),
}
ORDER = ("b230", "b231", "b232", "b234", "b237")
#: `base`＝現行正典の既定（4つ ON・b237 OFF）
BASE = ("b230", "b231", "b232", "b234")


def cond_knobs(name: str) -> tuple[str, ...]:
    """条件名 → ON にする切替口のタプル（正規順）。

    `base` は現行既定の4つ、`all` は5つ全部。`base+b237` のように加算できる。
    """
    base = name.split("@")[0]
    parts: set = set()
    for p in [x for x in base.split("+") if x]:
        if p in ("off", "none"):
            continue
        elif p == "base":
            parts |= set(BASE)
        elif p == "all":
            parts |= set(ORDER)
        elif p in KNOBS:
            parts.add(p)
        else:
            raise SystemExit(f"未知の切替口: {p}（条件名 {name}）")
    return tuple(k for k in ORDER if k in parts)


def _holders() -> dict:
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    return {"hp": HP, "bl": bl}


def _apply(knobs) -> dict:
    # ★B-248＝ベースライン条件（`base`）がリポジトリ既定と一致するかを実行時検査する
    #   （B-247 の測定事故＝§72-45 の一般形。既定値が動いたのに BASE を直し忘れると落ちる）
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), BASE, driver="b237_ab")
    holders = _holders()
    on = set(knobs)
    old = {}
    for k, (where, attr) in KNOBS.items():
        obj = holders[where]
        old[k] = getattr(obj, attr, False)   # 未実装フラグでも Phase 0 で使える
        setattr(obj, attr, k in on)
    return old


def _restore(old: dict):
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    for k, v in old.items():
        where, attr = KNOBS[k]
        setattr(holders[where], attr, v)


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id",
             cap: float | None = None, allp: bool | None = None) -> dict:
    from agents import HeuristicProtagonist as HP
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    knobs = cond_knobs(name)
    old = _apply(knobs)
    old_cap = (HP.B237_CAP, HP.B237_ALL_PARTNERS)
    if cap is not None:
        HP.B237_CAP = cap
    if allp is not None:
        HP.B237_ALL_PARTNERS = allp
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
        HP.B237_CAP, HP.B237_ALL_PARTNERS = old_cap
        _restore(old)
    n_def = sum(1 for r in rows if r["outcome"] == "defense")
    mean = (sum(r["loops_to_win"] for r in rows) / len(rows)) if rows else 0.0
    outcomes = dict(Counter(r["outcome"] for r in rows))
    dist = dict(Counter(r["loops_to_win"] for r in rows))
    tag = name + ("" if cap is None else f"(cap={cap})") \
               + ("" if allp is None else f"(all_partners={allp})")
    print(f"  [{days}日級 perm={perm} n={len(rows)}] {tag}: 防衛={n_def} "
          f"平均={mean:.3f} 結末={outcomes} 分布={dict(sorted(dist.items()))} "
          f"({time.time() - t0:.0f}s)", flush=True)
    return {"knobs": list(knobs), "rows": rows, "defense": n_def,
            "mean_loops_to_win": mean, "outcomes": outcomes,
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
    ap = argparse.ArgumentParser(description="B-237 A/B ドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="base,base+b237")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="base")
    ap.add_argument("--out", default="")
    ap.add_argument("--cap", type=float, default=None,
                    help="B237_CAP の掃引値（既定 None＝クラス既定 6.0 のまま）")
    ap.add_argument("--all-partners", type=int, default=None,
                    help="B237_ALL_PARTNERS の掃引値（1=狭い版〔既定〕/ 0=広い版）")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    conds = [c for c in a.conds.split(",") if c]
    reps = {}
    for c in conds:
        reps[c] = run_cond(c, a.days, loops=a.loops, perm=a.perm,
                           cap=(None if c == a.base else a.cap),
                           allp=(None if c == a.base or a.all_partners is None
                                 else bool(a.all_partners)))
    result = {"days": a.days, "perm": a.perm, "loops": a.loops, "conds": reps}
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
