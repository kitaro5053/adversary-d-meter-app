# -*- coding: utf-8 -*-
"""B-165 Phase 3：`agents.defense_plan.B165_PAIR_BREAK` の A/B（切替口＝bool・既定 False）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**
  （作りは `arena/b153_ab.py` と同型）。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires` ＝★**行為の数え上げ**（規約 §11b 成果の示し方1）。数え方は b153_ab の
  「押しのけ」と同じ手＝**同じ引数で builder を2回解いて差を取る**（ON 側だけを本番へ返す
  ＝挙動不変）。∴ builder の内部条件（`near` の作り方など）を**書き写していない**。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b165_ab --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b165_ab --days 5 --fires
"""

from __future__ import annotations

import argparse

import agents.defense_plan as dp
from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _switch_line(days: int) -> str:
    return (f"  [切替口] ★B165_PAIR_BREAK 実効={dp.B165_PAIR_BREAK}"
            f" ／ ★B165_JUUSHA_MOVE_COST 実効={dp.B165_JUUSHA_MOVE_COST}"
            f" ／ B153_JUUSHA_PAIR_BREAK={dp.B153_JUUSHA_PAIR_BREAK}"
            f" ／ B153_SUICIDE={dp.B153_SUICIDE}"
            f" ／ B153_PLAN_MOVE_KIND={HeuristicProtagonist.B153_PLAN_MOVE_KIND}"
            f" ／ B159_MISSING_BOARD={dp.B159_MISSING_BOARD}"
            f" ／ B161_COOL_COST={dp.B161_COOL_COST}"
            f" ／ B161_UNREST_COEFF={HeuristicProtagonist.B161_UNREST_COEFF}"
            f" ／ DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" ／ B100_MIX={HeuristicProtagonist.B100_MIX}"
            f" ／ B100_THETA={HeuristicProtagonist.B100_THETA}"
            f" ／ days={days}")


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない（`outcomes["defense"]` が防衛数）。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def _run(days: int, loops: int, on: bool) -> dict:
    old = dp.B165_PAIR_BREAK
    dp.B165_PAIR_BREAK = on
    print(_switch_line(days), flush=True)
    try:
        return run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        dp.B165_PAIR_BREAK = old


def count_fires(days: int, loops: int) -> dict:
    """★ON で走らせつつ、同じ引数で OFF も解いて **消えた折り手の数**を数える。"""
    old = dp.B165_PAIR_BREAK
    dp.B165_PAIR_BREAK = True
    n = {"kp_killer_calls": 0, "factor_kp_calls": 0, "dropped_breaks": 0,
         "added_breaks": 0, "changed_threats": 0}
    orig = {"kp_killer": dp._threat_kp_killer, "factor_kp": dp._threat_factor_kp}

    def _nb(ts) -> dict:
        """脅威ラベル -> その脅威が持つ Break のキー集合（★カード・対象で同定）。"""
        return {t.label: {(b.card, b.target, b.target_kind)
                          for c in t.conditions for b in c.breaks} for t in ts}

    def _wrap(key):
        f = orig[key]

        def _w(*a, **kw):
            on = f(*a, **kw)                      # ★本番（ON）＝この戻り値だけを返す
            n[f"{key}_calls"] += 1
            dp.B165_PAIR_BREAK = False
            try:
                off = f(*a, **kw)                 # 比較用（捨てる）
            finally:
                dp.B165_PAIR_BREAK = True
            a_on, a_off = _nb(on), _nb(off)
            for lab, koff in a_off.items():
                kon = a_on.get(lab, set())
                if kon == koff:
                    continue
                n["changed_threats"] += 1
                n["dropped_breaks"] += len(koff - kon)
                n["added_breaks"] += len(kon - koff)
            return on
        return _w

    dp._threat_kp_killer = _wrap("kp_killer")
    dp._threat_factor_kp = _wrap("factor_kp")
    print(_switch_line(days), flush=True)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        dp._threat_kp_killer = orig["kp_killer"]
        dp._threat_factor_kp = orig["factor_kp"]
        dp.B165_PAIR_BREAK = old
    return {**n, "rep": rep}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-165 A/B")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--fires", action="store_true")
    a = ap.parse_args(argv)

    if a.fires:
        r = count_fires(a.days, a.loops)
        print(f"  B-165: builder 呼び出し kp_killer={r['kp_killer_calls']}"
              f" factor_kp={r['factor_kp_calls']}"
              f" ／★追随で崩れない折り手を消した={r['dropped_breaks']}"
              f" ／★従者側の折り手を足した={r['added_breaks']}"
              f"（折り手集合が変わった脅威 {r['changed_threats']} 本）"
              f"／{_summary(r['rep'])}", flush=True)
        return 0

    base = None
    for on in (False, True):
        rep = _run(a.days, a.loops, on)
        rows = {(r["script"], r["seed"]): r["loops_to_win"] for r in rep["rows"]}
        line = f"  B165_PAIR_BREAK={on}: {_summary(rep)}"
        if base is None:
            base = rows
        else:
            flips = [(k, base[k], rows[k]) for k in rows if base[k] != rows[k]]
            imp = [f for f in flips if f[2] < f[1]]
            reg = [f for f in flips if f[2] > f[1]]
            line += f"  ★flip={len(flips)}（改善{len(imp)}／退行{len(reg)}）"
            for k, b, c in sorted(flips):
                line += f"\n      {k[0]} s{k[1]}: {b} → {c}"
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
