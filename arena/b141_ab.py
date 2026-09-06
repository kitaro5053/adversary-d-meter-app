# -*- coding: utf-8 -*-
"""B-141 Phase 3 の A/B ハーネス（(a)・(b)・(a+b) を**同一コミット上で**測る）。

前例＝`arena/b138_ab.py`（B-138 の切替口用）。測定の実体は既存の
`arena.benchmark.run_benchmark` と `arena.tie_noise.install_perm`＝**計測器の再実装はしない**。
本モジュールは「B-141 の切替口を版ごとに立てて回す」ための設定表と差分表示だけ。

**切替口の実効値を毎レグ印字**する（規約 §4：測定前に切替口の値を印字）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b141_ab \
        --days 3 --perms id --configs off,a,b,ab --out /tmp/b141_3_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

from agents.heuristic_protagonist import HeuristicProtagonist as HP

_KEYS = ("B141_COOLER_VALUE_FUTURE_ONLY", "B141_COOLER_ALLPAST_ONLY",
         "B141B_UNLOCK_SAME_DAY", "B143_YIELD")

#: 版の定義。off＝**B-141b 以前の挙動**（＝B-144 land 前の origin/main と bit 同値）。
CONFIGS: dict[str, dict] = {
    "off": {},
    "a":   {"B141_COOLER_VALUE_FUTURE_ONLY": True},
    "b":   {"B141B_UNLOCK_SAME_DAY": True},
    "ab":  {"B141_COOLER_VALUE_FUTURE_ONLY": True, "B141B_UNLOCK_SAME_DAY": True},
    # ★狭い述語版（規約 §12）＝未来の危険事件が1つも無い局面だけ日付で絞る
    "a0":  {"B141_COOLER_VALUE_FUTURE_ONLY": True, "B141_COOLER_ALLPAST_ONLY": True},
    "a0b": {"B141_COOLER_VALUE_FUTURE_ONLY": True, "B141_COOLER_ALLPAST_ONLY": True,
            "B141B_UNLOCK_SAME_DAY": True},
    # ★B-144＝(b)（解禁日の off-by-one 是正）と B-143 の守り（押しのけ防止）を
    #   **対で**入れた版。B-143 の実測＝(b) 単独では守りの蓋が外れて悪化するので、
    #   この2つは不可分。値は掃引で決める（FableA が値を選ばない＝B-142 の作法）。
    "by0":  {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 0.0},
    "by3":  {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 3.0},
    "by5":  {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 5.0},
    "by10": {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 10.0},
    "by15": {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 15.0},
    "by20": {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 20.0},
    "by25": {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 25.0},
    "by30": {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 30.0},
    "by40": {"B141B_UNLOCK_SAME_DAY": True, "B143_YIELD": 40.0},
}
#: ★A/B の「off」＝**B-141b 以前の挙動（B141 3つとも False・B143_YIELD=0.0）**を明示的に指す。
#  クラス既定から読んではいけない（どれかが既定 ON になった瞬間に
#  `getattr(HP, k)` だと off が別物になり A/B が成立しない）。
_DEFAULTS = {k: (0.0 if k == "B143_YIELD" else False) for k in _KEYS}


#: ★import 時の**クラス既定**（B-144 で `(b)`/`B143_YIELD` の既定が反転したため、
#  `off`（＝land 前の挙動）とクラス既定は**別物**になった）。後片付けはこちらへ戻す。
_CLASS_DEFAULTS = {k: getattr(HP, k) for k in _KEYS}


def _apply(cfg: str) -> None:
    for k, v in _DEFAULTS.items():
        setattr(HP, k, v)
    for k, v in CONFIGS[cfg].items():
        setattr(HP, k, v)


def _restore_class_defaults() -> None:
    """切替口を出荷時のクラス既定へ戻す（同一プロセスの後続処理を汚さない）。"""
    for k, v in _CLASS_DEFAULTS.items():
        setattr(HP, k, v)


def _leg(days: int, perm: str, cfg: str, loops: int) -> dict:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    _apply(cfg)
    print(f"  [{days}日級 perm={perm} cfg={cfg}] 切替口の実効値="
          + json.dumps({k: getattr(HP, k) for k in _KEYS}), flush=True)
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
    ap = argparse.ArgumentParser(description="B-141 A/B ハーネス")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perms", type=str, default="id")
    ap.add_argument("--configs", type=str, default="off,a,b,ab")
    ap.add_argument("--out", type=str, default="")
    a = ap.parse_args(argv)
    print(f"[b141_ab] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}"
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
        print(f"[b141_ab] 保存 {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
