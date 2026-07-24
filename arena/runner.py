"""自己対戦ハーネス（計画 §3.5）— バッチ実行・回帰指標・ログ保存。

使い方（CLI）:
    python -m arena.runner --script basic --games 50 --out logs/
    python -m arena.runner --script all --games 200        # 統計のみ（ログ保存なし）

指標：勝率／平均使用ループ数／平均決定数。将来 LLMエージェントの
非合法手率（flow の ValueError 検出数）をここに足す（計画 §3.5）。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agents import HeuristicMastermind, HeuristicProtagonist, RandomBot
from sim import run_game
from sim.sample_scripts import SAMPLE_SCRIPTS

from .gamelog import save_game

_MM_AGENTS = {"random": RandomBot, "heuristic": HeuristicMastermind}


def make_agents(seed: int, mastermind: str = "random",
                protagonist: str = "random") -> dict:
    """seed とbot種別からエージェント一式を作る。

    protagonist="heuristic" は推理主人公（belief）を1体で3席に共有
    （暗躍禁止の自滅回避のため席間協調が必要＝同一インスタンス）。
    """
    if protagonist == "heuristic":
        prot = HeuristicProtagonist(seed)
        seats = {"p1": prot, "p2": prot, "p3": prot}
    else:
        seats = {"p1": RandomBot(seed + 1000), "p2": RandomBot(seed + 2000),
                 "p3": RandomBot(seed + 3000)}
    return {"mastermind": _MM_AGENTS[mastermind](seed), **seats}


def default_agents(seed: int) -> dict:
    return make_agents(seed, "random")


def run_batch(script_name: str, n_games: int, base_seed: int = 0,
              out_dir: str | Path | None = None,
              agent_factory=None, mastermind: str = "random",
              protagonist: str = "random") -> dict:
    """同一脚本で n_games 回自己対戦し統計を返す。out_dir 指定でJSONLログも保存。

    mastermind/protagonist: "random"（既定）or "heuristic"。agent_factory を渡せば優先。
    """
    if agent_factory is None:
        def agent_factory(seed: int) -> dict:
            return make_agents(seed, mastermind, protagonist)
    script_fn = SAMPLE_SCRIPTS[script_name]
    stats = {
        "script": script_name, "games": n_games, "base_seed": base_seed,
        "mastermind": mastermind, "protagonist": protagonist,
        "wins": {"protagonist": 0, "mastermind": 0},
        "loops_played_total": 0, "decisions_total": 0,
    }
    t0 = time.perf_counter()
    for i in range(n_games):
        seed = base_seed + i
        script = script_fn()
        state, log = run_game(script, agent_factory(seed))
        stats["wins"][state.winner] += 1
        stats["loops_played_total"] += state.loop_no
        stats["decisions_total"] += len(log)
        if out_dir is not None:
            save_game(Path(out_dir) / f"{script_name}_seed{seed}.jsonl",
                      script, state, log)
    stats["elapsed_sec"] = round(time.perf_counter() - t0, 2)
    stats["avg_loops"] = round(stats["loops_played_total"] / n_games, 2)
    stats["avg_decisions"] = round(stats["decisions_total"] / n_games, 1)
    return stats


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="惨劇RoopeR 自己対戦バッチ")
    ap.add_argument("--script", default="basic",
                    choices=[*SAMPLE_SCRIPTS, "all"], help="サンプル脚本名")
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None, help="決定ログJSONLの出力ディレクトリ")
    ap.add_argument("--mm", default="random", choices=["random", "heuristic"],
                    help="脚本家bot（既定=random）")
    ap.add_argument("--prot", default="random", choices=["random", "heuristic"],
                    help="主人公bot（既定=random／heuristic=belief推理）")
    args = ap.parse_args(argv)

    names = list(SAMPLE_SCRIPTS) if args.script == "all" else [args.script]
    for name in names:
        stats = run_batch(name, args.games, args.seed, args.out,
                          mastermind=args.mm, protagonist=args.prot)
        print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
