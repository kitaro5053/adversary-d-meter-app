# -*- coding: utf-8 -*-
"""B-155 の A/B・掃引ドライバ（切替口＝`HeuristicProtagonist.B155_DECOY_GAP`）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**
  （perm は既存 `arena.tie_noise.install_perm` を再利用）。作りは `arena/b142_ab.py` と同型。
- per-game 差分（flip）を出す＝集計値は入れ替わりを隠す（規約 §5）。

値の意味（`agents/card_effect.noop_reason` G2 の例外条項に足す距離の条件）:
    off … 現行（例外は「mm札がある」だけで通る）
    1   … `th - unrest <= 1`＝今日の `不安+1` が当たれば臨界に届く対象だけ例外
    2,3 … より緩い（掃引用）

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b155_ab --days 3 \\
        --values off,1,2,3
    ... --perm id|rev|h1|h2 で `options` の並び順を置換（§11b の較正）
    ... --fires で「距離ゲートが no-op を返した席数」だけを数える（挙動は変えない）
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _parse(v: str) -> int | None:
    return None if v.strip().lower() in ("off", "none", "") else int(v)


def _rows(days: int, loops: int, value: int | None, perm: str = "id",
          opening_only: bool = False) -> dict:
    old = HeuristicProtagonist.B155_DECOY_GAP
    old_op = HeuristicProtagonist.B155_DECOY_OPENING_ONLY
    HeuristicProtagonist.B155_DECOY_GAP = value
    HeuristicProtagonist.B155_DECOY_OPENING_ONLY = opening_only
    print(f"  [切替口] B155_DECOY_GAP 既定={old} ／ 実効="
          f"{HeuristicProtagonist.B155_DECOY_GAP}"
          f" ／ B155_DECOY_OPENING_ONLY 既定={old_op} ／ 実効="
          f"{HeuristicProtagonist.B155_DECOY_OPENING_ONLY}"
          f" ／ B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
          f" ／ days={days} perm={perm}", flush=True)
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B155_DECOY_GAP = old
        HeuristicProtagonist.B155_DECOY_OPENING_ONLY = old_op
        uninstall_perm()
    return rep


def count_fires(days: int, loops: int, value: int | None, perm: str = "id",
                opening_only: bool = False) -> dict:
    """距離ゲートが **no-op を返した席数**（＝行為の数え上げ・§11b の証拠1）。

    `agents.card_effect.noop_reason` を薄いラッパで包み、B-155 の理由文字列で
    返った回数だけを数える（**戻り値はそのまま返す＝挙動不変**）。
    """
    import agents.card_effect as _ce
    import agents.defense_plan as _dp
    import agents.heuristic_protagonist as _hp
    from arena.tie_noise import install_perm, uninstall_perm

    orig = _ce.noop_reason
    n = {"fire": 0, "call": 0}

    def wrapped(view, card, target, target_kind, ctx):
        out = orig(view, card, target, target_kind, ctx)
        n["call"] += 1
        if out is not None and out.reason.startswith("不安0＋mm札はあるが臨界まで遠い"):
            n["fire"] += 1
        return out

    old = HeuristicProtagonist.B155_DECOY_GAP
    old_op = HeuristicProtagonist.B155_DECOY_OPENING_ONLY
    HeuristicProtagonist.B155_DECOY_GAP = value
    HeuristicProtagonist.B155_DECOY_OPENING_ONLY = opening_only
    # ★import 済みの参照をすべて差し替える（`from ... import noop_reason` 形式のため）。
    _ce.noop_reason = wrapped
    _hp.noop_reason = wrapped
    _dp.noop_reason = wrapped
    print(f"  [切替口] B155_DECOY_GAP 実効={HeuristicProtagonist.B155_DECOY_GAP}"
          f" ／ OPENING_ONLY 実効={HeuristicProtagonist.B155_DECOY_OPENING_ONLY}"
          f" ／ days={days} perm={perm}（発火数の計測）", flush=True)
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        HeuristicProtagonist.B155_DECOY_GAP = old
        HeuristicProtagonist.B155_DECOY_OPENING_ONLY = old_op
        _ce.noop_reason = orig
        _hp.noop_reason = orig
        _dp.noop_reason = orig
        uninstall_perm()
    return {"fires": n["fire"], "calls": n["call"], "rep": rep}


def _summary(rep: dict) -> str:
    d = rep["outcomes"]
    return (f"防衛={d.get('defense', 0)}/{rep['n_games']}"
            f" 平均={rep['mean_loops_to_win']}"
            f" L1={rep['distribution'].get('1', 0)}"
            f" loss={d.get('loss', 0)} fb_loss={d.get('fb_loss', 0)}"
            f" fb_win={d.get('fb_win', 0)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,1")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--json", default=None)
    ap.add_argument("--fires", action="store_true")
    ap.add_argument("--opening-only", action="store_true",
                    help="距離の条件を L1D1 だけに限る（最も狭い述語の変種）")
    a = ap.parse_args(argv)
    vals = [_parse(x) for x in a.values.split(",") if x.strip() != ""]
    if a.fires:
        for v in vals:
            r = count_fires(a.days, a.loops, v, perm=a.perm,
                            opening_only=a.opening_only)
            print(f"  発火席数={r['fires']} / 述語の呼び出し={r['calls']}"
                  f" / {_summary(r['rep'])}", flush=True)
        return 0
    out = {}
    base_rows = None
    for v in vals:
        rep = _rows(a.days, a.loops, v, perm=a.perm,
                    opening_only=a.opening_only)
        rows = {(r["script"], r["seed"]): r["loops_to_win"] for r in rep["rows"]}
        print(f"  B155_DECOY_GAP={v}: {_summary(rep)}", flush=True)
        if base_rows is None:
            base_rows = rows
        else:
            flips = sorted((k, base_rows[k], rows[k]) for k in rows
                           if base_rows[k] != rows[k])
            print(f"    flip={len(flips)}: "
                  + " ／ ".join(f"{s}(s{sd}) {a0}→{b0}"
                                for (s, sd), a0, b0 in flips), flush=True)
        out[str(v)] = {"outcomes": rep["outcomes"],
                       "mean": rep["mean_loops_to_win"],
                       "dist": rep["distribution"],
                       "rows": {f"{s}|{sd}": v2 for (s, sd), v2 in rows.items()}}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
