# -*- coding: utf-8 -*-
"""B-236 組み合わせドライバ：**主人公側 4切替口の 2^4 全数 A/B**（測定専用・実装は一切触らない）。

対象の切替口（4つとも main 既定 OFF・既に land 済み）：
- `agents.heuristic_protagonist.HeuristicProtagonist.B230_SPLIT_GEOMETRY`（クラス属性）
- `agents.belief.B231_IMOUTO_TRAIT`（モジュールフラグ）
- `agents.heuristic_protagonist.HeuristicProtagonist.B232_COOL_MATH`（クラス属性）
- `agents.belief.B234_GOSHINBOKU_TRAIT`（モジュールフラグ）

条件名＝ON にする切替口の番号を `+` で連結（例 `b232+b234`）。`off`＝全 OFF、`all`＝全 ON。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm`（B-218 教訓＝perm 3条件）。

★フラグは実行時に退避→上書き→復元するだけ（ファイル不変・既定値のコミットはしない）。
★同一プロセス内で全条件を連続測定する（同一機械・同一セッション）。状態漏れの検査用に
  条件名の末尾へ `@タグ` を付けると同じ条件を別名で2回測れる（例 `off,off@2`）。
★belief 側の再計算キャッシュ署名は `_recompute_sig` が B231/B234 を含む（agents/belief.py:1880-1881）
  ＝実行時トグルでの取り違えは構造的に塞がれている（B-101 の器）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b236_ab \
        --days 5 --perm id --conds off,b232,b234,b232+b234 --out docs/仮_b236_log/ab_d5_id.json
"""
from __future__ import annotations

import argparse
import itertools
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
}
ORDER = ("b230", "b231", "b232", "b234")


def cond_knobs(name: str) -> tuple[str, ...]:
    """条件名 → ON にする切替口のタプル（正規順）。"""
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


def cond_name(knobs) -> str:
    ks = tuple(k for k in ORDER if k in set(knobs))
    if not ks:
        return "off"
    if len(ks) == len(ORDER):
        return "all"
    return "+".join(ks)


def all_conds() -> list[str]:
    """2^4 = 16 条件を ON 数の昇順で並べる（off → 単独4 → 対6 → 3つ組4 → all）。"""
    out = []
    for r in range(len(ORDER) + 1):
        for combo in itertools.combinations(ORDER, r):
            out.append(cond_name(combo))
    return out


def _apply(knobs) -> dict:
    """フラグを上書きして (復元用の元値) を返す。"""
    # ★B-248＝ベースライン条件（`off`）がリポジトリ既定と一致するかを実行時検査する（§72-45）。
    #   本ドライバの測定当時は4つとも既定 OFF だったが、同日 `8fcbf07` で **4つとも既定 ON**
    #   になったので `off` はもう現正典ではない。当時の再現は `KNOB_AUDIT_ALLOW_STALE=1`。
    from arena import knob_audit
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    knob_audit.check_baseline(KNOBS, holders, (), driver="b236_ab")
    on = set(knobs)
    old = {}
    for k, (where, attr) in KNOBS.items():
        obj = holders[where]
        old[k] = getattr(obj, attr)
        setattr(obj, attr, k in on)
    return old


def _restore(old: dict):
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    for k, v in old.items():
        where, attr = KNOBS[k]
        setattr(holders[where], attr, v)


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id") -> dict:
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    knobs = cond_knobs(name)
    old = _apply(knobs)
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
    n_def = sum(1 for r in rows if r["outcome"] == "defense")
    mean = (sum(r["loops_to_win"] for r in rows) / len(rows)) if rows else 0.0
    outcomes = dict(Counter(r["outcome"] for r in rows))
    dist = dict(Counter(r["loops_to_win"] for r in rows))
    print(f"  [{days}日級 perm={perm} n={len(rows)}] {name}: 防衛={n_def} "
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
    ap = argparse.ArgumentParser(description="B-236 組み合わせドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="ALL16",
                    help="条件のカンマ区切り。ALL16 で 2^4 全数")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="off")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    if a.conds.strip() == "ALL16":
        conds = all_conds() + ["off@2"]
    else:
        conds = [c for c in a.conds.split(",") if c]
    reps = {}
    for c in conds:
        reps[c] = run_cond(c, a.days, loops=a.loops, perm=a.perm)
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
