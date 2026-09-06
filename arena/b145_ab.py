# -*- coding: utf-8 -*-
"""B-145 の A/B ハーネス（`B145_EVADE_MAX_FRIENDS` の掃引）。

前例＝`arena/b141_ab.py`／`arena/b142_ab.py`。測定の実体は既存の
`arena.benchmark.run_benchmark` と `arena.tie_noise.install_perm`＝**計測器の再実装はしない**。
本モジュールは「B-145 の切替口を版ごとに立てて回す」ための設定表と差分表示だけ。

**切替口の実効値を毎レグ印字**する（規約 §4：測定前に切替口の値を印字）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b145_ab \
        --days 3 --perms id,rev,h1,h2 --configs off,c2,c3,c4,c99 --out /tmp/b145_3.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

from agents.heuristic_protagonist import HeuristicProtagonist as HP

_KEYS = ("B145_EVADE_MAX_FRIENDS",)

#: 版の定義。off＝**land 前の origin/main と bit 同値**（None＝緩和なし）。
CONFIGS: dict[str, dict] = {
    "off": {"B145_EVADE_MAX_FRIENDS": None},
    "c2":  {"B145_EVADE_MAX_FRIENDS": 2},
    "c3":  {"B145_EVADE_MAX_FRIENDS": 3},
    "c4":  {"B145_EVADE_MAX_FRIENDS": 4},
    "c99": {"B145_EVADE_MAX_FRIENDS": 99},
}
#: `off` はクラス既定から読まない（既定が反転した瞬間に A/B が壊れるため）。
_DEFAULTS = {"B145_EVADE_MAX_FRIENDS": None}
#: import 時のクラス既定（後片付け先）。
_CLASS_DEFAULTS = {k: getattr(HP, k) for k in _KEYS}


def _apply(cfg: str) -> None:
    for k, v in _DEFAULTS.items():
        setattr(HP, k, v)
    for k, v in CONFIGS[cfg].items():
        setattr(HP, k, v)


def _restore_class_defaults() -> None:
    for k, v in _CLASS_DEFAULTS.items():
        setattr(HP, k, v)


def _leg(days: int, perm: str, cfg: str, loops: int) -> dict:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    _apply(cfg)
    print(f"  [{days}日級 perm={perm} cfg={cfg}] 切替口の実効値="
          + json.dumps({k: getattr(HP, k) for k in _KEYS})
          + f" / _B76_FRIEND_EVADE={HP._B76_FRIEND_EVADE}"
          + f" / _B90_FRIEND_PROMOTE={HP._B90_FRIEND_PROMOTE}"
          + f" / B141B_UNLOCK_SAME_DAY={HP.B141B_UNLOCK_SAME_DAY}"
          + f" / B143_YIELD={HP.B143_YIELD}", flush=True)
    t0 = time.time()
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        uninstall_perm()
        _restore_class_defaults()
    rows = {f'{r["script"]}#{r["seed"]}': r["loops_to_win"] for r in rep["rows"]}
    outc = {f'{r["script"]}#{r["seed"]}': r["outcome"] for r in rep["rows"]}
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    n_l1 = sum(1 for r in rep["rows"] if r["loops_to_win"] == 1)
    n_loss = rep["outcomes"].get("loss", 0)
    print(f"  [{days}日級 perm={perm} cfg={cfg}] 防衛={n_def} 平均="
          f"{rep['mean_loops_to_win']:.3f} L1={n_l1} loss={n_loss} "
          f"結末={rep['outcomes']} 所要={time.time() - t0:.0f}s", flush=True)
    return {"defense": n_def, "mean": rep["mean_loops_to_win"], "l1": n_l1,
            "loss": n_loss, "outcomes": rep["outcomes"], "rows": rows,
            "outcome_by_game": outc}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-145 A/B ハーネス")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perms", type=str, default="id")
    ap.add_argument("--configs", type=str, default="off,c2,c3,c4,c99")
    ap.add_argument("--out", type=str, default="")
    a = ap.parse_args(argv)
    print(f"[b145_ab] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}"
          f" days={a.days} loops={a.loops}", flush=True)
    res: dict = {"days": a.days, "loops": a.loops, "legs": {}}
    for perm in a.perms.split(","):
        for cfg in a.configs.split(","):
            res["legs"][f"{perm}/{cfg}"] = _leg(a.days, perm, cfg, a.loops)
    for perm in a.perms.split(","):
        base = res["legs"].get(f"{perm}/off")
        if not base:
            continue
        for cfg in a.configs.split(","):
            if cfg == "off":
                continue
            cur = res["legs"][f"{perm}/{cfg}"]
            imp = [(g, base["rows"][g], cur["rows"][g]) for g in base["rows"]
                   if cur["rows"][g] < base["rows"][g]]
            reg = [(g, base["rows"][g], cur["rows"][g]) for g in base["rows"]
                   if cur["rows"][g] > base["rows"][g]]
            print(f"  == {a.days}日級 perm={perm} off→{cfg}: 防衛 "
                  f"{base['defense']}→{cur['defense']} 平均 {base['mean']}→{cur['mean']} "
                  f"L1 {base['l1']}→{cur['l1']} 改善{len(imp)}／退行{len(reg)}", flush=True)
            for g, x, y in imp:
                print(f"       改善 {g}: {x}→{y}", flush=True)
            for g, x, y in reg:
                print(f"       退行 {g}: {x}→{y}", flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(f"[b145_ab] 保存 {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
