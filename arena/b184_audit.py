# -*- coding: utf-8 -*-
"""B-184 計測：**void 折り手の pick が消えたか・代わりに何を pick したか**（衛生の数え上げ）。

発注＝FableA（2026-08-07・§60-9）。実装＝`agents/defense_plan.py`
`B184_VOID_AWARE_BREAKS`（既定 OFF）。門の分解の土台＝`arena/b183_audit.py`（再利用）。

サブコマンド（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行）：

    python -m arena.b184_audit bench --days 3 [--on] [--json out.json]
        ＝標準ベンチ（`arena.benchmark.run_benchmark`）を切替口 {OFF, ON} で回す。
          OFF は素の `python -m arena.benchmark` と同一（フラグを触らないだけ）。
    python -m arena.b184_audit scan  --days 5 [--on] [--json out.json]
        ＝B-183 の門の分解（`b183_audit.scan`）を切替口つきで回す
          （空振りフィルタ門が何ターン減るかの一次指標）。
    python -m arena.b184_audit diff  --days 5 [--json out.json]
        ＝**同一プロセスで各局を OFF→ON の対で再生**し、ターンごとの plan.picks 差分
          （消えた void pick・代わりに立った pick）・実手の差分・結末 flip を数える。

★観測は b176_audit の読み取り専用プローブ＝対局を変えない（b183 §8 verify 済み）。
★「正解の配役」は一切参照しない。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import agents.defense_plan as dp


def _set_flag(on: bool) -> None:
    dp.B184_VOID_AWARE_BREAKS = bool(on)
    print(f"# B184_VOID_AWARE_BREAKS = {dp.B184_VOID_AWARE_BREAKS}", flush=True)


# ---------------------------------------------------------------------------
# bench / scan＝既存の器に切替口を挿すだけ（二重実装しない）
# ---------------------------------------------------------------------------
def bench(days: int, on: bool) -> dict:
    from arena.benchmark import run_benchmark
    _set_flag(on)
    rep = run_benchmark(days=days, verbose=False)
    rep["B184_VOID_AWARE_BREAKS"] = bool(on)
    return rep


def scan(days: int, on: bool, loops: int = 8) -> dict:
    from arena.b183_audit import scan as b183_scan
    _set_flag(on)
    res = b183_scan(days=days, loops=loops)
    res["B184_VOID_AWARE_BREAKS"] = bool(on)
    return res


# ---------------------------------------------------------------------------
# diff＝OFF→ON の対再生（pick の行き先を数える）
# ---------------------------------------------------------------------------
def _ltw(st, loops: int) -> tuple[int, str]:
    """`arena.benchmark.loops_to_win` と同じ読み（防衛ループ数・結末）。"""
    fb = any(e.get("event") == "final_battle" for e in st.history)
    if st.winner == "protagonist" and not fb:
        return st.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if st.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def _turn_picks(hp) -> dict:
    """(loop,day) -> {pick_key: fatal_max}（b176 プローブの全席和集合の写し）。"""
    out = {}
    for k, rec in hp.turns.items():
        out[k] = {pk: ent.get("fatal_max")
                  for pk, ent in (rec.get("picks") or {}).items()}
    return out


def _turn_plays(hp) -> dict:
    return {k: [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                for p in rec.get("plays", [])]
            for k, rec in hp.turns.items()}


def diff(days: int, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    from arena.b176_audit import _play, _analyze_game, _night_losses
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    seal: Counter = Counter()
    removed_rows, flip_rows = [], []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        g = f"{name}#{seed}"
        _set_flag(False)
        hp0, st0, _ = _play(sc, seed, loops)
        _set_flag(True)
        hp1, st1, _ = _play(sc, seed, loops)
        _set_flag(False)
        ltw0, out0 = _ltw(st0, loops)
        ltw1, out1 = _ltw(st1, loops)
        pm0 = {(pm["loop"], pm["day"]) for pm in _analyze_game(hp0, st0)["pickmiss"]}
        nl0 = _night_losses(st0)
        p0, p1 = _turn_picks(hp0), _turn_picks(hp1)
        pl0, pl1 = _turn_plays(hp0), _turn_plays(hp1)
        diverged = None            # 実手が最初に食い違ったターン（以降の差分は下流）
        for k in sorted(set(p0) | set(p1)):
            if diverged is None and pl0.get(k) != pl1.get(k):
                diverged = k
            off_keys, on_keys = set(p0.get(k, ())), set(p1.get(k, ()))
            removed = off_keys - on_keys
            added = on_keys - off_keys
            if not removed and not added:
                continue
            upstream = diverged is None or k <= diverged
            void_kinshi = {x for x in removed
                           if x[0] == "暗躍禁止" and x[2] == "board"}
            if void_kinshi and upstream:
                c["void暗躍禁止pickが消えたターン"] += 1
                fm = max((p0[k][x] or 0.0) for x in void_kinshi)
                c[f"  内数 fatal_max{'≥0.5' if fm >= 0.5 - 1e-9 else '<0.5'}"] += 1
                if k in pm0:
                    c["  内数 offではpick不翻訳ターンだった"] += 1
                if k in nl0:
                    c["  内数 offでその夜に敗北が成立していた"] += 1
                if added:
                    c["  内数 代わりのpickが立った"] += 1
                    for x in added:
                        c[f"  代替pick[{x[0]}]"] += 1
                if pl0.get(k) != pl1.get(k):
                    c["  内数 実手も変わった"] += 1
                if name == "btx5_seal":
                    seal["btx5_seal void暗躍禁止pickが消えたターン"] += 1
                removed_rows.append({
                    "game": g, "loop": k[0], "day": k[1],
                    "removed": sorted(f"{x[0]}→{x[1]}" for x in removed),
                    "fatal_max_off": fm, "pickmiss_off": k in pm0,
                    "night_loss_off": k in nl0,
                    "added": sorted(f"{x[0]}→{x[1]}" for x in added),
                    "added_fatal": {f"{x[0]}→{x[1]}": p1[k][x] for x in added},
                    "plays_off": pl0.get(k), "plays_on": pl1.get(k),
                    "plays_changed": pl0.get(k) != pl1.get(k),
                    "upstream": upstream})
            elif upstream and (removed or added):
                c["その他のpick差分ターン（上流）"] += 1
            else:
                c["下流のpick差分ターン（実手の分岐より後＝機序は問わない）"] += 1
        if name == "btx5_seal":
            seal["btx5_seal 局数"] += 1
            seal["btx5_seal 防衛(off)"] += int(out0 == "defense")
            seal["btx5_seal 防衛(on)"] += int(out1 == "defense")
        if (ltw0, out0) != (ltw1, out1):
            c["結末flip局"] += 1
            flip_rows.append({"game": g, "off": f"{ltw0}/{out0}",
                              "on": f"{ltw1}/{out1}"})
    return {"days": days, "counts": dict(c), "flips": flip_rows,
            "btx5_seal 単独行": dict(seal), "removed_rows": removed_rows}


def main() -> None:
    ap = argparse.ArgumentParser(description="B-184 計測")
    ap.add_argument("cmd", choices=["bench", "scan", "diff"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.cmd == "bench":
        res = bench(a.days, a.on)
    elif a.cmd == "scan":
        res = scan(a.days, a.on, a.loops)
    else:
        res = diff(a.days, a.loops, a.start, a.end)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    hide = {"rows", "removed_rows"}
    print(json.dumps({k: v for k, v in res.items() if k not in hide},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
