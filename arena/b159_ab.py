# -*- coding: utf-8 -*-
"""B-159 の A/B・掃引ドライバ（切替口＝`agents.defense_plan.B159_MISSING_BOARD`／
`B159_MISSING_COST`）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**
  （perm は既存 `arena.tie_noise.install_perm` を再利用）。作りは `arena/b155_ab.py` と同型。
- per-game 差分（flip）を出す＝集計値は入れ替わりを隠す（規約 §5）。
- `--fires` ＝**行為の数え上げ**（§11b 成果の示し方 1）＝B-159 の折り手が
  「生成された回数」「`plan_defenses` に採られた回数」「**既存の折り手を押しのけた回数**（L4）」。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b159_ab --days 3 \\
        --values off,1.0,1.5,2.0
    ... --perm id|rev|h1|h2
    ... --fires
"""

from __future__ import annotations

import argparse
import json

import agents.defense_plan as dp
from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _parse(v: str):
    s = (v or "").strip().lower()
    return None if s in ("off", "none", "") else float(s)


def _switch_line(days: int, perm: str, cost) -> str:
    return (f"  [切替口] B159_MISSING_BOARD 実効={dp.B159_MISSING_BOARD}"
            f" ／ B159_MISSING_COST 実効={dp.B159_MISSING_COST}"
            f" ／ B100_MIX={HeuristicProtagonist.B100_MIX}"
            f" ／ B100_THETA={HeuristicProtagonist.B100_THETA}"
            f" ／ B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
            f" ／ days={days} perm={perm}")


def _rows(days: int, loops: int, cost, perm: str = "id") -> dict:
    old_f, old_c = dp.B159_MISSING_BOARD, dp.B159_MISSING_COST
    dp.B159_MISSING_BOARD = cost is not None
    if cost is not None:
        dp.B159_MISSING_COST = cost
    print(_switch_line(days, perm, cost), flush=True)
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        dp.B159_MISSING_BOARD, dp.B159_MISSING_COST = old_f, old_c
        uninstall_perm()
    return rep


def count_fires(days: int, loops: int, cost, perm: str = "id") -> dict:
    """★行為の数え上げ（挙動は変えない＝ラッパは戻り値をそのまま返す）。

    - `gen`  ＝B-159 の折り手が **生成** された回数（席×板×事件日）
    - `pick` ＝その折り手が `plan_defenses` に **採られた** 回数
    - `displace` ＝★**L4**＝B-159 の折り手を採ったことで、
      **OFF なら採られていた別の手**が `plan.picks` から消えた回数
      （同一席で ON/OFF の `plan_defenses` を二重に解いて比較する。
      ON 側の結果だけを本番へ返す＝OFF 側は捨てる＝挙動不変）。
    """
    from arena.tie_noise import install_perm, uninstall_perm

    old_f, old_c = dp.B159_MISSING_BOARD, dp.B159_MISSING_COST
    dp.B159_MISSING_BOARD = cost is not None
    if cost is not None:
        dp.B159_MISSING_COST = cost
    n = {"gen": 0, "pick": 0, "displace": 0, "seats": 0}

    orig_cool = dp._cooling_breaks
    orig_plan = dp.plan_defenses

    def cool(cond, view, opts, live, criticals, label_fmt, cost_):
        r = orig_cool(cond, view, opts, live, criticals, label_fmt, cost_)
        if r and "行方不明" in label_fmt:
            n["gen"] += 1
        return r

    def plan(threats, **kw):
        p = orig_plan(threats, **kw)
        n["seats"] += 1
        mine = [b for b in (p.picks or []) if "行方不明" in (b.label or "")]
        if mine:
            n["pick"] += len(mine)
            # ★L4＝B-159 の折り手を外した同じ脅威表を解き直し、消えた手を数える
            import copy
            t2 = copy.deepcopy(list(threats))
            for t in t2:
                for c in (t.conditions or ()):
                    c.breaks = [b for b in (c.breaks or ())
                                if "行方不明" not in (b.label or "")]
            p0 = orig_plan(t2, **kw)
            k1 = {(b.card, b.target, b.target_kind) for b in (p.picks or [])}
            k0 = {(b.card, b.target, b.target_kind) for b in (p0.picks or [])}
            n["displace"] += len(k0 - k1)
        return p

    dp._cooling_breaks = cool
    dp.plan_defenses = plan
    install_perm(perm)
    try:
        print(_switch_line(days, perm, cost), flush=True)
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        dp._cooling_breaks = orig_cool
        dp.plan_defenses = orig_plan
        dp.B159_MISSING_BOARD, dp.B159_MISSING_COST = old_f, old_c
        uninstall_perm()
    return {**n, "rep": rep}


def _key(r: dict) -> tuple:
    return (r["script"], r["seed"])


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない（`outcomes["defense"]` が防衛数）。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-159 A/B・掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,1.5")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--fires", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    vals = [_parse(v) for v in a.values.split(",")]
    if a.fires:
        for v in vals:
            r = count_fires(a.days, a.loops, v, perm=a.perm)
            print(f"  値={v}: 生成={r['gen']} 採用={r['pick']} "
                  f"★押しのけ(L4)={r['displace']} プラン解いた席={r['seats']} "
                  f"／{_summary(r['rep'])}", flush=True)
        return 0

    base = None
    out = []
    for v in vals:
        rep = _rows(a.days, a.loops, v, perm=a.perm)
        rows = {_key(r): r["loops_to_win"] for r in rep["rows"]}
        line = f"  値={v}: {_summary(rep)}"
        if base is None:
            base = rows
        else:
            flips = [(k, base[k], rows[k]) for k in rows if base[k] != rows[k]]
            imp = [f for f in flips if f[2] < f[1]]
            reg = [f for f in flips if f[2] > f[1]]
            line += f"  flip={len(flips)}（改善{len(imp)}／退行{len(reg)}）"
            for k, b, c in sorted(flips):
                line += f"\n      {k[0]} s{k[1]}: {b} → {c}"
        print(line, flush=True)
        out.append({"value": v, "summary": rep})
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
