# -*- coding: utf-8 -*-
"""B-235 再測ドライバ：**2つの切替口の 2×2 A/B**（測定専用・実装は一切触らない）。

対象の切替口（どちらも main 既定 OFF・既に land 済み）：
- `agents.heuristic_protagonist.HeuristicProtagonist.B230_SPLIT_GEOMETRY`（クラス属性）
- `agents.belief.B231_IMOUTO_TRAIT`（モジュールフラグ）

条件＝`off` / `b230` / `b231` / `both`。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm`（B-218 教訓＝perm 3条件）。

★フラグは実行時に退避→上書き→復元するだけ（ファイル不変・既定値のコミットはしない）。
★同一プロセス内で全条件を連続測定する（同一機械・同一セッション）。状態漏れの検査用に
  条件名の末尾へ `@タグ` を付けると同じ条件を別名で2回測れる（例 `off,off@2`）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b235_ab \
        --days 3 --perm id --conds off,b230,b231,both --out docs/仮_b235_log/ab_d3_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

CONDS: dict[str, dict] = {
    "off": {},
    "b230": {"b230": True},
    "b231": {"b231": True},
    "both": {"b230": True, "b231": True},
}


#: 条件名 → (置き場, 属性名)。★B-248＝`knob_audit` の健全性検査に渡すための宣言。
KNOBS: dict[str, tuple[str, str]] = {
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
}


def _holders() -> dict:
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    return {"hp": HP, "bl": bl}


def _apply(b230: bool, b231: bool):
    """フラグを上書きして (復元用の元値) を返す。"""
    # ★B-248＝ベースライン条件（`off`）がリポジトリ既定と一致するかを実行時検査する（§72-45）。
    #   本ドライバの測定（2026-08-17 01:31）当時は2つとも既定 OFF だったが、
    #   同日 `8fcbf07` で **4つとも既定 ON** になったので `off` はもう現正典ではない。
    #   当時の測定を再現する目的なら `KNOB_AUDIT_ALLOW_STALE=1` を付ける。
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), (), driver="b235_ab")
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    old = (HP.B230_SPLIT_GEOMETRY, bl.B231_IMOUTO_TRAIT)
    HP.B230_SPLIT_GEOMETRY = b230
    bl.B231_IMOUTO_TRAIT = b231
    return old


def _restore(old):
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    HP.B230_SPLIT_GEOMETRY, bl.B231_IMOUTO_TRAIT = old


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id") -> dict:
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    base = name.split("@")[0]
    spec = CONDS[base]
    old = _apply(bool(spec.get("b230")), bool(spec.get("b231")))
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
    return {"rows": rows, "defense": n_def, "mean_loops_to_win": mean,
            "outcomes": outcomes, "dist": {str(k): v for k, v in sorted(dist.items())},
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
    ap = argparse.ArgumentParser(description="B-235 再測ドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,b230,b231,both")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="off")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    reps = {}
    for c in a.conds.split(","):
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
