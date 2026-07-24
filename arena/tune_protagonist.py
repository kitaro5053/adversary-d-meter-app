# -*- coding: utf-8 -*-
"""主人公AI（PRIORITY表）の自動チューニング — 対gen2共進化（Bonapeti主人公版）。

arena/tune.py（脚本家版CEM・2026-07-09）の主人公側。手作り局所規則の天井と判明した
ケース（BTX_4型・docs/引き継ぎ_詰み判定と学習.md §4/§4c）への正攻法＝
PRIORITY表の実数値を、強い相手（CEM gen2 mm）に対して機械最適化する。

設計方針（tune.py と対になる差分だけ書く）:
- **目的関数＝主人公視点の平均ループ数（最小化）**。相手は gen2 mm 固定
  （arena/params/mm_cem_gen2_20260709.json）＝共進化の1ステップ。
- **二重の制約**（これが本体）:
  1. 半順序制約: tests/test_priorities.py の全テスト関数を候補値で直接実行し、
     AssertionError の候補は即棄却（関係が仕様・数値は自由、の単一ソースを維持）。
  2. 既定mm全緑制約: サンプル9本＋random seed 0-4 を既定mmで対局し、
     防衛に失敗した局ごとに大ペナルティ（既定ベンチの全緑を犠牲にする解を禁止）。
- **PRIORITYの差し替えはワーカープロセス内で in-place**（agents のコード変更ゼロ）。
  評価のたびに update→finally で復元。マルチプロセスの spawn 子はプロセス独立＝安全。
- 訓練 seed は 3000〜（標準ベンチ 0-19・脚本家版訓練 1000〜と不交差）。

使い方:
    # スモーク（数分）
    PYTHONHASHSEED=0 python -m arena.tune_protagonist --iters 2 --pop 4 --n-scripts 8
    # 本番（20〜60分）
    PYTHONHASHSEED=0 python -m arena.tune_protagonist --iters 12 --pop 16 --n-scripts 24
    # ベスト重みの4面ホールドアウト評価（既定mm 3d/5d＋gen2 3d/5d）
    PYTHONHASHSEED=0 python -m arena.tune_protagonist --eval logs/tune_protagonist/best_params.json

★採用は人間の判断：best は arena/params/ へ保存するが既定値は変えない
（既定化＝全ベンチの意味が変わるため、ユーザー承認とドキュメント更新とセットで）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from dataclasses import replace

os.environ.setdefault("PYTHONHASHSEED", "0")

from agents import HeuristicMastermind, HeuristicProtagonist  # noqa: E402
import agents.heuristic_protagonist as _hp_mod  # noqa: E402
from sim import run_game  # noqa: E402

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "tune_protagonist")
_PARAMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params")
_GEN2_PATH = os.path.join(_PARAMS_DIR, "mm_cem_gen2_20260709.json")

_ORIG_PRIORITY = dict(_hp_mod.PRIORITY)
# 探索対象＝正値のキーのみ（自滅回避=-100 は「絶対禁止」の構造値＝据え置き）
DEFAULT_KEYS = tuple(sorted(k for k, v in _ORIG_PRIORITY.items() if v > 0))

_PENALTY_DEFAULT_LOSS = 50.0   # 既定mmでの防衛失敗1局あたり（全緑制約）
_PENALTY_ORDER = 1e9           # 半順序違反＝即棄却


def _gen2_params() -> dict:
    with open(_GEN2_PATH, encoding="utf-8") as f:
        return json.load(f)["params"]


def _halforder_ok() -> bool:
    """tests/test_priorities.py の全テスト関数を現在のPRIORITY値で実行する。

    テストは `from ... import PRIORITY as P`＝同一dictへの束縛なので、
    in-place update がそのまま反映される（関係＝仕様の単一ソースを保つ）。"""
    import tests.test_priorities as tp
    fns = [v for k, v in vars(tp).items() if k.startswith("test_") and callable(v)]
    try:
        for fn in fns:
            fn()
        return True
    except AssertionError:
        return False


def train_specs(n_scripts: int, seed0: int = 3000) -> list[tuple]:
    """訓練 spec：FS/BTX × 3日/5日 を四分割（gen2の残敗は両日級に跨るため混合）。"""
    out = []
    per = max(1, n_scripts // 4)
    for i in range(per):
        out.append(("random", "FS", seed0 + i, 3))
        out.append(("random", "BTX", seed0 + i, 3))
        out.append(("random", "FS", seed0 + 500 + i, 5))
        out.append(("random", "BTX", seed0 + 500 + i, 5))
    return out[:n_scripts] if n_scripts >= 4 else out


def constraint_specs() -> list[tuple]:
    """既定mm全緑制約の対局セット＝サンプル9本＋random両セット seed 0-4。"""
    from sim.sample_scripts import SAMPLE_SCRIPTS
    out = [("sample", name, 0) for name in SAMPLE_SCRIPTS]
    for s in range(5):
        out.append(("random", "FS", s, 3))
        out.append(("random", "BTX", s, 3))
    return out


def _build_script(spec: tuple):
    kind = spec[0]
    if kind == "random":
        from sim import random_script
        _, set_name, seed, days = spec
        return random_script(set_name, seed, days=days,
                             loops=(4 if days == 5 else 3))
    if kind == "sample":
        from sim.sample_scripts import SAMPLE_SCRIPTS
        _, name, _seed = spec
        return SAMPLE_SCRIPTS[name]()
    raise ValueError(f"unknown spec: {spec}")


def _loops_to_win(script, agent_seed: int, mm_params: dict | None,
                  loops: int) -> int:
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(agent_seed, params=mm_params)
    hp = HeuristicProtagonist(agent_seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no
    return loops + 1


_GEN2_CACHE: dict | None = None


def eval_candidate(args: tuple) -> dict:
    """1候補＝PRIORITYをin-place差し替え→半順序→gen2訓練＋既定制約→復元。

    score（小さいほど良い）= gen2平均ループ数 + 50×既定mm防衛失敗数。"""
    global _GEN2_CACHE
    cand_id, prio, specs, cons, loops, agent_seed = args
    assert os.environ.get("PYTHONHASHSEED") == "0", "PYTHONHASHSEED未固定"
    if _GEN2_CACHE is None:
        _GEN2_CACHE = _gen2_params()
    try:
        if prio:
            _hp_mod.PRIORITY.update(prio)
        if not _halforder_ok():
            return {"id": cand_id, "score": _PENALTY_ORDER, "gen2_mean": None,
                    "default_losses": None, "params": prio, "reject": "halforder"}
        # ★ゲーム単位の耐障害：並行開発の共有ツリーでWIPを踏んでも1ゲームの
        #   例外でジョブ全体を殺さない（2026-07-08実測：ご神木実装の編集途中を
        #   夜間CEMが踏んで全滅）。同一イテレーション内は全候補が同じ脚本を
        #   スキップする＝候補間の比較は公平なまま。
        vals = []
        skipped = 0
        for sp in specs:
            try:
                vals.append(_loops_to_win(_build_script(sp), agent_seed,
                                          _GEN2_CACHE, loops))
            except Exception:
                skipped += 1
        if not vals or skipped > len(specs) // 5:
            return {"id": cand_id, "score": _PENALTY_ORDER, "gen2_mean": None,
                    "default_losses": None, "params": prio,
                    "reject": f"sim_error_x{skipped}"}
        gen2_mean = sum(vals) / len(vals)
        losses = 0
        for sp in cons:
            try:
                sc = _build_script(sp)
                lt = _loops_to_win(sc, agent_seed, None, loops)
            except Exception:
                continue
            if lt > loops:
                losses += 1
        return {"id": cand_id, "score": gen2_mean + _PENALTY_DEFAULT_LOSS * losses,
                "gen2_mean": gen2_mean, "default_losses": losses, "params": prio}
    finally:
        _hp_mod.PRIORITY.update(_ORIG_PRIORITY)


def _cem_sample(mu: dict, sd: dict, keys: tuple, rng: random.Random) -> dict:
    """順位保存サンプリング：値をサンプルした後、元のPRIORITY順位に再割当する。

    PRIORITYは0.5%差の順位関係が仕様（例：93.5>93）＝素朴な対数ノイズでは
    ほぼ全候補が半順序違反で棄却される（初回実測：16/16棄却×12世代）。
    順位を保存すれば現行値が満たす任意の半順序を自動で満たし、実際に探索される
    のは「順位間のギャップ」＝PRIORITY外の計算スコア（危険度・近傍ボーナス等）
    との相対関係。それが席割当を変える本体。"""
    vals = sorted(max(1e-3, math.exp(rng.gauss(mu[k], sd[k]))) for k in keys)
    keys_by_orig = sorted(keys, key=lambda k: _ORIG_PRIORITY[k])
    return dict(zip(keys_by_orig, vals))


def tune(iters: int, pop: int, n_scripts: int, loops: int, sigma: float,
         keys: tuple, workers: int, agent_seed: int, out_dir: str,
         init_params: dict | None = None) -> dict:
    from multiprocessing import Pool

    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(out_dir, f"tunep_{ts}.jsonl")
    specs = train_specs(n_scripts)
    cons = constraint_specs()
    rng = random.Random(20260713)

    start = dict(_ORIG_PRIORITY)
    if init_params:
        start.update(init_params)
    mu = {k: math.log(start[k]) for k in keys}
    sd = {k: sigma for k in keys}
    elite_frac = 0.25

    best = {"score": float("inf"), "params": None}
    with Pool(processes=workers) as pool, open(log_path, "w", encoding="utf-8") as lf:
        base = pool.map(eval_candidate,
                        [(0, None, specs, cons, loops, agent_seed)])[0]
        baseline = base["score"]
        lf.write(json.dumps({"iter": -1, "baseline": base, "n_scripts": len(specs),
                             "keys": list(keys)}, ensure_ascii=False) + "\n")
        print(f"baseline（現行手調整）: score={baseline:.3f} "
              f"(gen2_mean={base['gen2_mean']:.3f} 既定損失={base['default_losses']})",
              flush=True)

        for it in range(iters):
            cands = [_cem_sample(mu, sd, keys, rng) for _ in range(pop)]
            jobs = [(i, c, specs, cons, loops, agent_seed) for i, c in enumerate(cands)]
            results = pool.map(eval_candidate, jobs)
            results.sort(key=lambda r: r["score"])
            for r in results:
                lf.write(json.dumps({"iter": it, **r}, ensure_ascii=False) + "\n")
            lf.flush()
            top = results[0]
            if top["score"] < best["score"]:
                best = {"score": top["score"], "params": top["params"],
                        "gen2_mean": top["gen2_mean"],
                        "default_losses": top["default_losses"]}
                with open(os.path.join(out_dir, "best_params.json"), "w",
                          encoding="utf-8") as bf:
                    json.dump({"score_train": best["score"],
                               "baseline_train": baseline,
                               "gen2_mean_train": best["gen2_mean"],
                               "default_losses_train": best["default_losses"],
                               "params": best["params"]},
                              bf, ensure_ascii=False, indent=1)
            valid = [r for r in results if r["score"] < _PENALTY_ORDER]
            n_el = max(2, int(pop * elite_frac))
            elites = valid[:n_el] if valid else []
            for k in keys:
                if not elites:
                    break
                logs = [math.log(r["params"][k]) for r in elites]
                m = sum(logs) / len(logs)
                var = sum((x - m) ** 2 for x in logs) / max(1, len(logs) - 1)
                mu[k] = m
                sd[k] = max(0.02, math.sqrt(var))
            print(f"iter {it}: top={top['score']:.3f} "
                  f"(gen2={top['gen2_mean']} 既定損失={top['default_losses']}) "
                  f"best={best['score']:.3f} baseline={baseline:.3f} "
                  f"棄却={sum(1 for r in results if r['score'] >= _PENALTY_ORDER)}/{pop}",
                  flush=True)
    return {"baseline": baseline, "best": best, "log": log_path}


def eval_on_benchmark(params_path: str, loops: int = 8) -> None:
    """best を4面ホールドアウト（既定mm 3d/5d＋gen2 3d/5d）でペア比較する。"""
    from arena.benchmark import benchmark_scripts
    with open(params_path, encoding="utf-8") as f:
        payload = json.load(f)
    prio = payload.get("params") or payload
    gen2 = _gen2_params()
    for label, mm_params in (("既定mm", None), ("gen2", gen2)):
        for days in (3, 5):
            rows_b, rows_t = [], []
            for name, seed, sc in benchmark_scripts(days=days):
                rows_b.append(_loops_to_win(sc, seed, mm_params, loops))
                try:
                    _hp_mod.PRIORITY.update(prio)
                    rows_t.append(_loops_to_win(sc, seed, mm_params, loops))
                finally:
                    _hp_mod.PRIORITY.update(_ORIG_PRIORITY)
            n = len(rows_b)
            lb = sum(1 for v in rows_b if v > loops)
            lt = sum(1 for v in rows_t if v > loops)
            print(f"[{label} {days}日級 {n}局] 現行: mean={sum(rows_b)/n:.3f} 損失{lb} "
                  f"→ tuned: mean={sum(rows_t)/n:.3f} 損失{lt}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="主人公AI PRIORITYの自動調整（対gen2）")
    ap.add_argument("--iters", type=int, default=12)
    ap.add_argument("--pop", type=int, default=16)
    ap.add_argument("--n-scripts", type=int, default=24)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--sigma", type=float, default=0.12,
                    help="log空間SD。半順序棄却が多ければ下げる")
    ap.add_argument("--keys", type=str, default=None)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--agent-seed", type=int, default=0)
    ap.add_argument("--out-dir", type=str, default=_LOG_DIR)
    ap.add_argument("--eval", type=str, default=None)
    ap.add_argument("--init", type=str, default=None)
    args = ap.parse_args(argv)

    if args.eval:
        eval_on_benchmark(args.eval, loops=args.loops)
        return
    keys = tuple(args.keys.split(",")) if args.keys else DEFAULT_KEYS
    unknown = set(keys) - set(_ORIG_PRIORITY)
    if unknown:
        raise SystemExit(f"未知のキー: {sorted(unknown)}")
    init_params = None
    if args.init:
        with open(args.init, encoding="utf-8") as f:
            payload = json.load(f)
        init_params = payload.get("params") or payload
    res = tune(args.iters, args.pop, args.n_scripts, args.loops, args.sigma,
               keys, args.workers, args.agent_seed, args.out_dir,
               init_params=init_params)
    print(f"完了: baseline={res['baseline']:.3f} → best={res['best']['score']:.3f}")
    print("→ 4面ホールドアウト: python -m arena.tune_protagonist --eval "
          + os.path.join(args.out_dir, "best_params.json"))


if __name__ == "__main__":
    main()
