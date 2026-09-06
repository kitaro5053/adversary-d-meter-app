# -*- coding: utf-8 -*-
"""B-243 A/B ドライバ（測定専用・実装は一切触らない）＝`arena/b236_ab.py` と同型。

対象の切替口（どちらも既定 OFF）：
- `agents.belief.B243_BOARD_X_ROLE`（bx）＝ボードX → ウィッチ／クロマクの初期エリア絞り込み
- `agents.belief.B243_DOCTOR_SELF_TARGET`（doc）＝医者は自分自身に不安を置けない

条件名＝ON にする切替口を `+` で連結（`off` / `bx` / `doc` / `bx+doc`）。
`--perm`（id/rev/h1）は `arena.tie_noise.install_perm`（規約 §11b＝perm 3条件）。
★フラグは実行時に退避→上書き→復元するだけ（ファイル不変）。
★同一プロセス内で全条件を連続測定する（同一機械・同一セッション）。`off@2` で状態漏れ検査。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b243_ab \
        --days 3 --perm id --conds off,bx,doc,bx+doc,off@2 --out docs/仮_b243_log/ab_d3_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

KNOBS = {"bx": "B243_BOARD_X_ROLE", "doc": "B243_DOCTOR_SELF_TARGET"}
ORDER = ("bx", "doc")


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


def _apply(knobs) -> dict:
    import agents.belief as bl
    # ★B-248＝ベースライン条件（`off`）がリポジトリ既定と一致するかを実行時検査する（§72-45）。
    #   本レーンの測定（2026-08-17 17時台）当時は2つとも既定 OFF だったが、同日 `cdfd9dc` で
    #   `B243_DOCTOR_SELF_TARGET` が既定 ON になったので `off` はもう現正典ではない。
    #   当時の測定を再現する目的なら `KNOB_AUDIT_ALLOW_STALE=1` を付ける。
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, bl, (), driver="b243_ab")
    on = set(knobs)
    old = {k: getattr(bl, a) for k, a in KNOBS.items()}
    for k, a in KNOBS.items():
        setattr(bl, a, k in on)
    return old


def _restore(old: dict):
    import agents.belief as bl
    for k, a in KNOBS.items():
        setattr(bl, a, old[k])


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id") -> dict:
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    old = _apply(cond_knobs(name))
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
    return {"knobs": list(cond_knobs(name)), "rows": rows, "defense": n_def,
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
    ap = argparse.ArgumentParser(description="B-243 A/B ドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,bx,doc,bx+doc,off@2")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="off")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    reps = {c: run_cond(c, a.days, loops=a.loops, perm=a.perm)
            for c in (c for c in a.conds.split(",") if c)}
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
