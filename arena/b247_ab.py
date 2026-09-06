# -*- coding: utf-8 -*-
"""B-247 A/B ドライバ（★測定専用・実装も既定値も一切触らない）。

`arena/b243_ab.py` に **主人公側の既定 OFF ガード**を条件として足しただけ
（フラグは実行時に退避→上書き→復元＝ファイルは不変）。

条件に使える切替口：
- `bx`   ＝ `agents.belief.B243_BOARD_X_ROLE`（B-243・既定 OFF）
- `doc`  ＝ `agents.belief.B243_DOCTOR_SELF_TARGET`（B-243・既定 OFF）
- `b206` ＝ `HeuristicProtagonist.B206_SELF_HARM_MOVE`（自傷手の上限・既定 OFF）

★**ここに載せてよいのは「既定 OFF」の切替口だけ**。`_apply` は KNOBS の全項目を
`k in on` で上書きするので、**既定 ON の切替口を混ぜると `off` 条件でそれを OFF にしてしまう**
（本レーンの実事故＝`B222_FERRY_GEOMETRY` は**既定 ON** なのに載せてしまい、
初回測定の全条件が「B-222 を切った盤面」になっていた。2026-08-17 に自己検出・是正）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b247_ab \
        --days 3 --perm id --conds off,bx,b206,bx+b206,off@2 --out docs/仮_b247_log/ab_d3_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

#: 条件名 → (置き場, 属性名)
KNOBS: dict[str, tuple[str, str]] = {
    "bx": ("bl", "B243_BOARD_X_ROLE"),
    "doc": ("bl", "B243_DOCTOR_SELF_TARGET"),
    "b206": ("hp", "B206_SELF_HARM_MOVE"),
}
#: ★不変条件＝KNOBS の全項目は**既定 False**でなければならない（上の注意書き）。
ORDER = ("bx", "doc", "b206")


def _holders():
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    return {"hp": HP, "bl": bl}


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


_DEFAULTS_CHECKED = False


def _check_defaults() -> None:
    """★不変条件＝KNOBS に載る切替口は**リポジトリ既定が False**でなければならない。

    `_apply` は KNOBS 全項目を `k in on` で上書きするので、既定 ON の切替口が混じると
    `off` 条件がそれを **OFF にした盤面**になり、全条件が汚染される（本レーンの実事故）。
    最初の `_apply` の前に1度だけ検査する。

    ★B-248（2026-08-17）＝検査の実体を `arena.knob_audit` へ移した（他ドライバと共有）。
      本ドライバのベースラインは `off`＝**何も ON にしない条件**なので `baseline=()`。
      ★なお `B243_DOCTOR_SELF_TARGET` は本レーンの測定の**後**に既定 ON 化された
      （`cdfd9dc`／main では `3961c3a`）ため、**現在の main で本ドライバを走らせるとここで落ちる**。
      これは仕様どおり＝「`off` はもう現正典と同じ盤面ではない」ことを黙って通さない。
      当時の測定を再現したいときだけ `KNOB_AUDIT_ALLOW_STALE=1` を付けること。
    """
    global _DEFAULTS_CHECKED
    if _DEFAULTS_CHECKED:
        return
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), (), driver="b247_ab")
    _DEFAULTS_CHECKED = True


def _apply(knobs) -> dict:
    _check_defaults()
    holders = _holders()
    on = set(knobs)
    old = {}
    for k, (where, attr) in KNOBS.items():
        old[k] = getattr(holders[where], attr)
        setattr(holders[where], attr, k in on)
    return old


def _restore(old: dict):
    holders = _holders()
    for k, (where, attr) in KNOBS.items():
        setattr(holders[where], attr, old[k])


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
    ap = argparse.ArgumentParser(description="B-247 A/B ドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,bx,b206,bx+b206,off@2")
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
