# -*- coding: utf-8 -*-
"""B-138 Phase 2 の A/B ハーネス（(a)・(b)・(a+b) を**同一コミット上で**測る）。

`arena.tie_noise.install_perm` で列挙順条件（id/rev/h1/h2）を掛け、
`HeuristicProtagonist` の切替口を版ごとに立てて標準ベンチを回す。
**切替口の実効値を毎レグ印字**する（規約：測定前の確認）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b138_ab \
        --days 3 --perms id --configs off,a,b,ab --out /tmp/b138_3_id.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

from agents.heuristic_protagonist import HeuristicProtagonist as HP

#: 版の定義（切替口の組）。off＝既定（＝origin/main と bit 同値）。
CONFIGS: dict[str, dict] = {
    "off":  {"B138_ODB_BOARD_LOSS_ONLY": False, "B138_AIM_TOP_PROBS": False},
    "a":    {"B138_ODB_BOARD_LOSS_ONLY": True,  "B138_AIM_TOP_PROBS": False},
    "a0":   {"B138_ODB_BOARD_LOSS_ONLY": True,  "B138_AIM_TOP_PROBS": False,
             "B138_ODB_FALLBACK": False},
    "b":    {"B138_ODB_BOARD_LOSS_ONLY": False, "B138_AIM_TOP_PROBS": True},
    "bw":   {"B138_ODB_BOARD_LOSS_ONLY": False, "B138_AIM_TOP_PROBS": True,
             "B138_BONUS_WHEN_NO_AIM": True},
    "ab":   {"B138_ODB_BOARD_LOSS_ONLY": True,  "B138_AIM_TOP_PROBS": True},
}
# ★(b) の閾値掃引（`P(照準) < 比 × P(最上位)` のときだけ +4.0 を付け替える）
for _r in (0.02, 0.05, 0.1, 0.2, 0.5):
    CONFIGS[f"b{_r}"] = {"B138_ODB_BOARD_LOSS_ONLY": False,
                         "B138_AIM_TOP_PROBS": True, "B138_AIM_MAX_RATIO": _r}
    CONFIGS[f"ab{_r}"] = {"B138_ODB_BOARD_LOSS_ONLY": True,
                          "B138_AIM_TOP_PROBS": True, "B138_AIM_MAX_RATIO": _r}
# ★(b) の「確率が優勢なときだけ付け替える」掃引（`P(最上位) >= θ`）
for _t in (0.4, 0.5, 0.6, 0.8, 0.9, 0.95):
    CONFIGS[f"bt{_t}"] = {"B138_ODB_BOARD_LOSS_ONLY": False,
                          "B138_AIM_TOP_PROBS": True, "B138_AIM_MIN_TOP": _t}
    CONFIGS[f"abt{_t}"] = {"B138_ODB_BOARD_LOSS_ONLY": True,
                           "B138_AIM_TOP_PROBS": True, "B138_AIM_MIN_TOP": _t}
# ★B-146（2026-08-03）＝同じ土俵で測るため**同じハーネスに版を足す**（二重実装しない）。
#   `t`＝票は落とさず B-131 の同票タイブレークの材料からだけ途中終了ループを外す狭い版。
#   `at`＝(a) と併用（絞り＋タイブレーク材料の両方）。
CONFIGS["t"] = {"B146_ODB_TIEBREAK_BOARD_LOSS_ONLY": True}
CONFIGS["at"] = {"B138_ODB_BOARD_LOSS_ONLY": True,
                 "B146_ODB_TIEBREAK_BOARD_LOSS_ONLY": True}
_KEYS = ("B138_ODB_BOARD_LOSS_ONLY", "B138_ODB_FALLBACK",
         "B138_AIM_TOP_PROBS", "B138_BONUS_WHEN_NO_AIM", "B138_AIM_MAX_RATIO",
         "B138_AIM_MIN_TOP", "B146_ODB_TIEBREAK_BOARD_LOSS_ONLY")
_DEFAULTS = {k: getattr(HP, k) for k in _KEYS}
# ★B-146 land 後（既定 True）も `off` は**land 前の挙動**を指す必要がある
#   （クラス既定から読むと off が別物になる＝`arena/b141_ab.py` の `_DEFAULTS` と同じ事故対策）。
_DEFAULTS["B146_ODB_TIEBREAK_BOARD_LOSS_ONLY"] = False


def _apply(cfg: str) -> None:
    for k, v in _DEFAULTS.items():
        setattr(HP, k, v)
    for k, v in CONFIGS[cfg].items():
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
        _apply("off")
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
    ap = argparse.ArgumentParser(description="B-138 A/B ハーネス")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perms", type=str, default="id")
    ap.add_argument("--configs", type=str, default="off,a,b,ab")
    ap.add_argument("--out", type=str, default="")
    a = ap.parse_args(argv)
    print(f"[b138_ab] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}"
          f" days={a.days} loops={a.loops}", flush=True)
    res: dict = {"days": a.days, "loops": a.loops, "legs": {}}
    for perm in a.perms.split(","):
        for cfg in a.configs.split(","):
            res["legs"][f"{perm}/{cfg}"] = _leg(a.days, perm, cfg, a.loops)
    # 差分（各 perm の off を基準に）
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
        print(f"[b138_ab] 保存 {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
