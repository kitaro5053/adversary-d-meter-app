"""B-199 計測ハーネス：切替口の ON/OFF で両ベンチを回し per-game 行を JSON で出す。

  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b199_bench --days 5 --toggle 0 --out x.json

★本番経路は変更しない（`sim.loop_race.B199_MISSING_INCIDENT_FEED` を実行前に差し替えるだけ）。
"""
from __future__ import annotations

import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--toggle", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import sim.loop_race as lr
    lr.B199_MISSING_INCIDENT_FEED = bool(args.toggle)

    from arena.benchmark import run_benchmark
    res = run_benchmark(days=args.days, verbose=False)
    rows = [{k: r[k] for k in ("script", "seed", "loops_to_win", "outcome", "race")}
            for r in res["rows"]]
    summary = {k: v for k, v in res.items() if k != "rows"}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"days": args.days, "toggle": args.toggle,
                   "summary": summary, "rows": rows}, f, ensure_ascii=False, default=str)
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
