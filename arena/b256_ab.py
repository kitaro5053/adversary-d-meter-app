# -*- coding: utf-8 -*-
"""B-256 A/B ドライバ（★測定専用・実装も既定値も一切触らない）。

`arena/b247_ab.py` と同型（フラグは実行時に退避→上書き→復元＝ファイルは不変）。

条件に使える切替口：
- `skip` ＝ `HeuristicProtagonist.B256_UNREACHABLE_SKIP`（不安3へ届かない対象を試験候補から外す）
- `mm`   ＝ `HeuristicProtagonist.B256_INCLUDE_MM`（上限に脚本家の供給を織り込む＝`U_pm`）
- `bel`  ＝ `HeuristicProtagonist.B262_BELIEF_BOUND`（★B-262＝**第3の上限**。供給の公開実績と
  今日の伏せ札が揃った対象だけ `U_pm` 側へ倒す）

★`mm`／`bel` は単独では**原理的に何も起こさない**（`skip` が OFF なら `_b256_reachable_unrest`
は呼ばれない）＝条件表には `skip`・`skip+mm`・`skip+bel` を並べる。
★`skip+mm+bel` は `mm` が全対象を倒すので `skip+mm` と同値（`bel` は `mm` の部分集合）。
`--floor` は使えない（B-256／B-262 には float の掃引口が無い＝渡すと落ちる）。

★★**ベースライン条件が `off` から `skip+bel` に変わった**（B-267・2026-08-19）。
  ユーザー裁定で `B256_UNREACHABLE_SKIP` と `B262_BELIEF_BOUND` が**対で既定 ON** になったため、
  `knob_audit` の不変条件「ベースライン条件のフラグ束＝リポジトリ既定」を満たす条件名は
  `skip+bel` になった（＝`BASELINE`）。`off` は今や**反実仮想の条件**（既定を切った盤面）で、
  依然として測れるが「正典＝リポジトリ既定」ではない。
  ★この検査を消してはいけない理由＝B-247 の測定事故（§72-45）＝条件表は全項目を毎回
  書き込むので、既定 ON の切替口を載せたまま `baseline=()` にすると**全条件が汚染**される。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b256_ab \
        --days 3 --conds skip+bel,off,skip,skip+mm --out /tmp/b256/ab_d3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

#: 条件名 → (置き場, 属性名)。
#: ★不変条件は「全項目が既定 False」ではなく「**`BASELINE` のフラグ束＝リポジトリ既定**」
#:   （`arena/knob_audit.py` の一般形）。B-267 で `skip`／`bel` が既定 ON になった。
KNOBS: dict[str, tuple[str, str]] = {
    "skip": ("hp", "B256_UNREACHABLE_SKIP"),
    "mm": ("hp", "B256_INCLUDE_MM"),
    "bel": ("hp", "B262_BELIEF_BOUND"),
}
ORDER = ("skip", "mm", "bel")

#: ★ベースライン条件で ON にする切替口（＝リポジトリ既定と一致していなければならない）。
#:  B-267（2026-08-19 ユーザー裁定）で `skip`＋`bel` が既定 ON になったため `()` から変更。
BASELINE: tuple[str, ...] = ("skip", "bel")


def _holders():
    from agents import HeuristicProtagonist as HP
    return {"hp": HP}


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
    """★不変条件＝**`BASELINE` のフラグ束＝リポジトリ既定**（B-248 の共有検査の一般形）。

    B-267 までは `baseline=()`（＝全項目が既定 False）だったが、ユーザー裁定で
    `skip`／`bel` が既定 ON になったのでベースライン条件は `skip+bel` になった。
    """
    global _DEFAULTS_CHECKED
    if _DEFAULTS_CHECKED:
        return
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), BASELINE, driver="b256_ab")
    _DEFAULTS_CHECKED = True


def _apply(knobs, floor: float | None) -> dict:
    _check_defaults()
    holders = _holders()
    on = set(knobs)
    old = {}
    for k, (where, attr) in KNOBS.items():
        old[k] = getattr(holders[where], attr)
        setattr(holders[where], attr, k in on)
    if floor is not None:                    # ★B-256 には掃引口が無い
        raise SystemExit("★B-256 には float の掃引口が無い（--floor は使えない）")
    return old


def _restore(old: dict):
    holders = _holders()
    for k, (where, attr) in KNOBS.items():
        setattr(holders[where], attr, old[k])


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id",
             floor: float | None = None) -> dict:
    from arena.benchmark import benchmark_scripts, loops_to_win
    from arena.tie_noise import install_perm, uninstall_perm
    old = _apply(cond_knobs(name), floor)
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
    print(f"  [{days}日級 perm={perm} n={len(rows)}] {name}(floor={floor}): "
          f"防衛={n_def} 平均={mean:.3f} 結末={outcomes} "
          f"分布={dict(sorted(dist.items()))} ({time.time() - t0:.0f}s)", flush=True)
    return {"knobs": list(cond_knobs(name)), "floor": floor, "rows": rows,
            "defense": n_def, "mean_loops_to_win": mean, "outcomes": outcomes,
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
    ap = argparse.ArgumentParser(description="B-256 A/B ドライバ（測定専用）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,skip,skip+mm,skip+bel")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--base", default="off")
    ap.add_argument("--floor", type=float, default=None)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    reps = {c: run_cond(c, a.days, loops=a.loops, perm=a.perm, floor=a.floor)
            for c in (c for c in a.conds.split(",") if c)}
    result = {"days": a.days, "perm": a.perm, "loops": a.loops,
              "floor": a.floor, "conds": reps}
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
