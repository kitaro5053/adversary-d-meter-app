# -*- coding: utf-8 -*-
"""脚本家AIパラメータの自動チューニング（Bonapeti・2026-07-09 Fable設計）。

手調整してきた agents/heuristic.py のスコア重み（MM_PARAMS）を、ベンチマーク
（主人公が勝利にかかるループ数）を目的関数として機械最適化する。

設計方針:
- **目的関数＝脚本家視点の平均ループ数（最大化）**。相手は現行 HeuristicProtagonist 固定。
  ループ数は防衛の"遅らせ"を連続的に測れる（勝敗2値より感度が高い＝benchmark.py と同思想）。
- **train/holdout 分離**：訓練＝random_script の seed 1000〜（ベンチ標準の seed 0-19 と不交差）。
  最終評価＝標準ベンチ130本（arena.benchmark）。訓練セットへの過学習を標準ベンチで検出する。
- **決定性**：スコア同点タイブレークが set のハッシュ順に依存するため、ワーカーは必ず
  PYTHONHASHSEED=0 で起動する（親プロセスで env を固定してから spawn＝子に継承される。
  親自身は評価しない＝オーケストレーションのみ）。
- **探索は stdlib のみ**（random探索＋CEM）。Optuna等は要求しない（requirements据え置き）。
  乗算的な対数正規ノイズ p' = p·exp(N(0,σ))＝スケールの違う重みを一様に扱える。

使い方（例）:
    # スモーク（動作確認・数分）
    python -m arena.tune --method random --iters 4 --pop 4 --n-scripts 8 --workers 4
    # 本番（CEM・数時間級。Opusはここから回す）
    python -m arena.tune --method cem --iters 20 --pop 24 --n-scripts 40 --workers 8
    # 一部の重みだけ探索（例：不安まわりだけ）
    python -m arena.tune --keys set_unrest_today,set_unrest_future,set_noise_base ...
    # ベスト重みの標準ベンチ評価（ホールドアウト）
    python -m arena.tune --eval logs/tune/best_params.json

出力: logs/tune/tune_<ts>.jsonl（全候補の試行ログ）／logs/tune/best_params.json（暫定ベスト）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from dataclasses import replace

# ★ワーカー起動前に必ずハッシュシードを固定（spawn の子プロセスへ環境で継承される）。
os.environ.setdefault("PYTHONHASHSEED", "0")

from agents import HeuristicMastermind, HeuristicProtagonist  # noqa: E402
from agents.heuristic import MM_PARAMS  # noqa: E402
from sim import run_game  # noqa: E402

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "tune")
_PARAMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params")


def load_tuned_params(name: str = "mm_cem_20260709") -> dict:
    """バージョン管理された学習済み重みを読む（opt-in）。

    使い方: HeuristicMastermind(seed, params=load_tuned_params())
    ＝ホールドアウト（標準ベンチ130本）でΔ+0.346を確認した重み。既定値は手調整のまま
    （既定の切替は主人公ベンチの対戦相手が変わる＝AIトラックと調整の上で行う）。
    """
    path = os.path.join(_PARAMS_DIR, name + ".json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["params"]

# 既定の探索対象：全キー。--keys で絞れる（少数キーほど収束が速い）。
DEFAULT_KEYS = tuple(sorted(MM_PARAMS))


# ---------------------------------------------------------------------------
# 脚本セット（spec＝pickle安全なタプルで渡し、ワーカー内で Script を生成する）
# ---------------------------------------------------------------------------

def train_specs(n_scripts: int, days: int, seed0: int = 1000) -> list[tuple]:
    """訓練用 spec。random_script の seed 1000〜＝標準ベンチ（seed 0-19）と不交差。"""
    out = []
    per = max(1, n_scripts // 2)
    for i in range(per):
        out.append(("random", "FS", seed0 + i, days))
    for i in range(n_scripts - per):
        out.append(("random", "BTX", seed0 + i, days))
    return out


def _build_script(spec: tuple):
    kind = spec[0]
    if kind == "random":
        from sim import random_script
        _, set_name, seed, days = spec
        return random_script(set_name, seed, days=days)
    if kind == "sample":
        from sim.sample_scripts import SAMPLE_SCRIPTS
        _, name, _seed = spec
        return SAMPLE_SCRIPTS[name]()
    raise ValueError(f"unknown spec: {spec}")


# ---------------------------------------------------------------------------
# 評価（ワーカー側で実行）
# ---------------------------------------------------------------------------

def _loops_to_win(script, agent_seed: int, params: dict | None, loops: int) -> int:
    """主人公が防衛勝ちにかかったループ数（打ち切り＝loops+1）。benchmark.py と同義。"""
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(agent_seed, params=params)
    hp = HeuristicProtagonist(agent_seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no
    return loops + 1


def eval_candidate(args: tuple) -> dict:
    """1候補を訓練セット全体で評価して平均ループ数を返す（脚本家は大きいほど良い）。

    ★ゲーム単位の耐障害（レーン規律6・tune_protagonist.py と同型）：並行開発のWIPを
    importして個別ゲームが落ちても、そのゲームだけスキップして走り続ける。スキップは
    脚本起因（決定的）なので同一イテレーション内の全候補が同じ脚本を落とす＝比較は公平。
    スキップ過多（>20%）は評価不能として棄却マークを返す。
    """
    cand_id, params, specs, loops, agent_seed = args
    assert os.environ.get("PYTHONHASHSEED") == "0", "ワーカーのPYTHONHASHSEEDが未固定"
    vals = []
    skipped = 0
    for spec in specs:
        try:
            sc = _build_script(spec)
            vals.append(_loops_to_win(sc, agent_seed, params, loops))
        except Exception:  # noqa: BLE001  # WIP起因の個別失敗はスキップして続行
            skipped += 1
    if not vals or skipped > len(specs) // 5:
        return {"id": cand_id, "mean_loops": -1.0, "n": len(vals),
                "skipped": skipped, "params": params}
    return {"id": cand_id, "mean_loops": sum(vals) / len(vals), "n": len(vals),
            "skipped": skipped, "params": params}


# ---------------------------------------------------------------------------
# 候補生成（log空間の乗算ノイズ／CEM）
# ---------------------------------------------------------------------------

def _perturb(base: dict, keys: tuple, sigma: float, rng: random.Random) -> dict:
    """base の keys だけ対数正規ノイズで揺らした差分dictを返す（0や負は作らない）。"""
    out = {}
    for k in keys:
        v = base.get(k, MM_PARAMS[k])
        out[k] = max(1e-3, v * math.exp(rng.gauss(0.0, sigma)))
    return out

def _cem_sample(mu: dict, sd: dict, keys: tuple, rng: random.Random) -> dict:
    return {k: max(1e-3, math.exp(rng.gauss(mu[k], sd[k]))) for k in keys}


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------

def tune(method: str, iters: int, pop: int, n_scripts: int, days: int, loops: int,
         sigma: float, keys: tuple, workers: int, agent_seed: int, out_dir: str,
         init_params: dict | None = None) -> dict:
    from multiprocessing import Pool

    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(out_dir, f"tune_{ts}.jsonl")
    specs = train_specs(n_scripts, days)
    rng = random.Random(20260709)

    # CEM の分布（log空間）。初期平均＝現行値（--init で学習済み重みから再開可）、初期SD＝sigma。
    start = dict(MM_PARAMS)
    if init_params:
        start.update(init_params)
    mu = {k: math.log(start[k]) for k in keys}
    sd = {k: sigma for k in keys}
    elite_frac = 0.25

    best = {"mean_loops": -1.0, "params": None}
    with Pool(processes=workers) as pool, open(log_path, "w", encoding="utf-8") as lf:
        # 世代0＝ベースライン（現行の手調整値）を必ず測る（比較の物差し）。
        base_res = pool.map(eval_candidate, [(0, None, specs, loops, agent_seed)])[0]
        baseline = base_res["mean_loops"]
        lf.write(json.dumps({"iter": -1, "baseline": baseline, "n_scripts": n_scripts,
                             "days": days, "loops": loops, "keys": list(keys)},
                            ensure_ascii=False) + "\n")
        print(f"baseline（現行手調整）: mean_loops={baseline:.3f}  "
              f"({n_scripts}本・{days}日級・loops={loops})", flush=True)

        for it in range(iters):
            if method == "cem":
                cands = [_cem_sample(mu, sd, keys, rng) for _ in range(pop)]
            else:
                cands = [_perturb(MM_PARAMS if best["params"] is None else best["params"],
                                  keys, sigma, rng) for _ in range(pop)]
            jobs = [(i, c, specs, loops, agent_seed) for i, c in enumerate(cands)]
            results = pool.map(eval_candidate, jobs)
            results.sort(key=lambda r: -r["mean_loops"])
            top = results[0]
            for r in results:
                lf.write(json.dumps({"iter": it, **r}, ensure_ascii=False) + "\n")
            lf.flush()
            if top["mean_loops"] > best["mean_loops"]:
                best = {"mean_loops": top["mean_loops"], "params": top["params"]}
                with open(os.path.join(out_dir, "best_params.json"), "w",
                          encoding="utf-8") as bf:
                    json.dump({"mean_loops_train": best["mean_loops"],
                               "baseline_train": baseline,
                               "params": best["params"]}, bf, ensure_ascii=False, indent=1)
            if method == "cem":  # エリートで分布更新
                n_el = max(2, int(pop * elite_frac))
                elites = results[:n_el]
                for k in keys:
                    logs = [math.log(r["params"][k]) for r in elites]
                    m = sum(logs) / len(logs)
                    var = sum((x - m) ** 2 for x in logs) / max(1, len(logs) - 1)
                    mu[k] = m
                    sd[k] = max(0.02, math.sqrt(var))  # 早期収束しすぎない下限
            print(f"iter {it}: top={top['mean_loops']:.3f} best={best['mean_loops']:.3f} "
                  f"(baseline {baseline:.3f})", flush=True)
    return {"baseline": baseline, "best": best, "log": log_path}


def eval_on_benchmark(params_path: str, days: int, loops: int, n_seeds: int = 1) -> None:
    """best_params.json を標準ベンチ130本（ホールドアウト）で現行値とペア比較する。

    n_seeds>1 でエージェントseedを変えた反復＝Δのノイズ幅（min/max）も出す。
    """
    from arena.benchmark import benchmark_scripts
    with open(params_path, encoding="utf-8") as f:
        payload = json.load(f)
    params = payload.get("params") or payload
    deltas = []
    for s in range(n_seeds):
        rows_base, rows_tuned = [], []
        for name, seed, sc in benchmark_scripts(days=days):
            rows_base.append(_loops_to_win(sc, s * 1000 + seed, None, loops))
            rows_tuned.append(_loops_to_win(sc, s * 1000 + seed, params, loops))
        mb = sum(rows_base) / len(rows_base)
        mt = sum(rows_tuned) / len(rows_tuned)
        deltas.append(mt - mb)
        print(f"[seed{s}] 標準ベンチ{len(rows_base)}本（{days}日級・loops={loops}）: "
              f"現行={mb:.3f} → チューニング後={mt:.3f} （Δ={mt - mb:+.3f}）", flush=True)
    if n_seeds > 1:
        print(f"Δまとめ: mean={sum(deltas)/len(deltas):+.3f} "
              f"min={min(deltas):+.3f} max={max(deltas):+.3f}（全て正なら改善確定）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="脚本家AIパラメータの自動チューニング")
    ap.add_argument("--method", choices=("random", "cem"), default="cem")
    ap.add_argument("--iters", type=int, default=10)
    ap.add_argument("--pop", type=int, default=16)
    ap.add_argument("--n-scripts", type=int, default=24)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--sigma", type=float, default=0.25)
    ap.add_argument("--keys", type=str, default=None,
                    help="探索する重みキー（カンマ区切り）。省略＝全キー")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--agent-seed", type=int, default=0)
    ap.add_argument("--out-dir", type=str, default=_LOG_DIR)
    ap.add_argument("--eval", type=str, default=None,
                    help="best_params.json を標準ベンチで評価して終了")
    ap.add_argument("--init", type=str, default=None,
                    help="CEMの初期平均にする params JSON（第2世代の再走用）")
    ap.add_argument("--eval-seeds", type=int, default=1,
                    help="--eval のエージェントseed反復数（ノイズ幅の測定）")
    args = ap.parse_args(argv)

    if args.eval:
        eval_on_benchmark(args.eval, days=args.days, loops=args.loops,
                          n_seeds=args.eval_seeds)
        return
    keys = tuple(args.keys.split(",")) if args.keys else DEFAULT_KEYS
    unknown = set(keys) - set(MM_PARAMS)
    if unknown:
        raise SystemExit(f"未知のキー: {sorted(unknown)}")
    init_params = None
    if args.init:
        with open(args.init, encoding="utf-8") as f:
            payload = json.load(f)
        init_params = payload.get("params") or payload
    res = tune(args.method, args.iters, args.pop, args.n_scripts, args.days,
               args.loops, args.sigma, keys, args.workers, args.agent_seed,
               args.out_dir, init_params=init_params)
    print(f"完了: baseline={res['baseline']:.3f} → best={res['best']['mean_loops']:.3f}")
    print(f"ログ: {res['log']}  ベスト: {os.path.join(args.out_dir, 'best_params.json')}")
    print("→ 過学習チェック: python -m arena.tune --eval "
          + os.path.join(args.out_dir, "best_params.json"))


if __name__ == "__main__":
    main()
