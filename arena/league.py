"""リーグ戦評価（過学習対策）— 全バージョン総当たり×複数脚本で非退行を確認する。

軍拡競争でAIを更新するとき、「最新の相手にだけ強い（＝過学習）」を防ぐため:
- 旧版（agents/legacy）とランダムを凍結プールとして残す。
- 新版は プール全員 × （固定脚本＋ランダム脚本）で評価し、旧版比の非退行を見る。
- ヒューリスティックは相手botの癖でなく「ルールに接地した公開情報」だけを根拠にする（設計規律）。

CLI:  python -m arena.league --games 40
"""

from __future__ import annotations

import argparse
import json
import time

from agents import HeuristicMastermind, HeuristicProtagonist, RandomBot
from agents.belief import Belief
from agents.legacy import (
    HeuristicMastermindV1,
    HeuristicMastermindV2,
    HeuristicProtagonistV1,
    HeuristicProtagonistV2,
    HeuristicProtagonistV3,
)
from sim import random_script, run_game
from sim.sample_scripts import SAMPLE_SCRIPTS

MASTERMINDS = {
    "mm_random": lambda seed: RandomBot(seed),
    "mm_v1": lambda seed: HeuristicMastermindV1(seed),
    "mm_v2": lambda seed: HeuristicMastermindV2(seed),
    "mm_v3": lambda seed: HeuristicMastermind(seed),
}


def _prot_seats(agent) -> dict:
    return {"p1": agent, "p2": agent, "p3": agent}


PROTAGONISTS = {
    "prot_random": lambda seed: {"p1": RandomBot(seed + 1), "p2": RandomBot(seed + 2),
                                 "p3": RandomBot(seed + 3)},
    "prot_v1": lambda seed: _prot_seats(HeuristicProtagonistV1(seed)),
    "prot_v2": lambda seed: _prot_seats(HeuristicProtagonistV2(seed)),
    "prot_v3": lambda seed: _prot_seats(HeuristicProtagonistV3(seed)),
    "prot_v4": lambda seed: _prot_seats(HeuristicProtagonist(seed)),
}


def league_scripts(n_random: int = 4):
    """評価用脚本＝固定サンプル全部＋ランダム脚本（FS/BTX各n_random）＝脚本過学習の防止。"""
    scripts = {name: fn for name, fn in SAMPLE_SCRIPTS.items()}
    for i in range(n_random):
        scripts[f"rand_fs_{i}"] = (lambda i=i: random_script("FS", seed=1000 + i))
        scripts[f"rand_btx_{i}"] = (lambda i=i: random_script("BTX", seed=2000 + i))
    return scripts


def _deduction_metrics(script, state, worlds0: int) -> tuple[float, float]:
    """1ゲームの推理精度：(世界残存率, 役職的中率)。公開履歴だけからbeliefを再構成して測る。"""
    b = Belief(script.cast, [{"day": i.day, "name": i.name} for i in script.incidents],
               set_name=script.set_name)
    b.observe(state.history)
    worlds_frac = b.summary()["worlds_remaining"] / (worlds0 or 1)
    marg = b.role_marginals()
    hits = sum(1 for n in script.cast
               if marg.get(n) and max(marg[n], key=marg[n].get) == script.role_of(n))
    return worlds_frac, hits / len(script.cast)


def run_league(n_games: int = 30, n_random_scripts: int = 4,
               masterminds: dict | None = None, protagonists: dict | None = None) -> dict:
    """総当たり評価。返り値: {(mm名, prot名): {"win": 脚本家勝率,
    "worlds": 平均世界残存率(低いほど推理が進んだ), "acc": 平均役職的中率}}（全脚本合算）。"""
    mms = masterminds or MASTERMINDS
    prots = protagonists or PROTAGONISTS
    scripts = league_scripts(n_random_scripts)
    # 各脚本の観測前世界数（正規化の分母）を1回だけ計算
    worlds0: dict[str, int] = {}
    for name, fn in scripts.items():
        sc = fn()
        worlds0[name] = Belief(sc.cast, [{"day": i.day, "name": i.name} for i in sc.incidents],
                               set_name=sc.set_name).summary()["worlds_remaining"]
    result: dict = {}
    for mm_name, mm_fn in mms.items():
        for pr_name, pr_fn in prots.items():
            wins = games = 0
            wsum = asum = 0.0
            for sc_name, sc_fn in scripts.items():
                for seed in range(n_games):
                    script = sc_fn()
                    agents = {"mastermind": mm_fn(seed), **pr_fn(seed)}
                    state, _ = run_game(script, agents)
                    wins += (state.winner == "mastermind")
                    wf, acc = _deduction_metrics(script, state, worlds0[sc_name])
                    wsum += wf
                    asum += acc
                    games += 1
            result[(mm_name, pr_name)] = {"win": wins / games, "worlds": wsum / games,
                                          "acc": asum / games}
    return result


def format_matrix(result: dict, key: str = "win", title: str | None = None) -> str:
    titles = {"win": "脚本家勝率（行=脚本家, 列=主人公）",
              "worlds": "平均世界残存率（低いほど主人公の推理が進んだ）",
              "acc": "役職的中率（最有力役職が正解の割合＝最後の戦いの素点）"}
    mm_names = sorted({k[0] for k in result})
    pr_names = sorted({k[1] for k in result})
    lines = [title or titles.get(key, key),
             "  " + "".join(f"{p:>14s}" for p in pr_names)]
    for m in mm_names:
        lines.append(f"{m:>10s}" + "".join(f"{result[(m, p)][key]:>13.1%} "
                                           for p in pr_names))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AIバージョン総当たりリーグ（過学習チェック）")
    ap.add_argument("--games", type=int, default=30, help="1組合せ・1脚本あたりの対戦数")
    ap.add_argument("--random-scripts", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    result = run_league(args.games, args.random_scripts)
    if args.json:
        print(json.dumps({f"{m}|{p}": v for (m, p), v in result.items()},
                         ensure_ascii=False, indent=2))
    else:
        print(format_matrix(result, "win"))
        print()
        print(format_matrix(result, "acc"))
        print()
        print(format_matrix(result, "worlds"))
        print(f"({time.perf_counter() - t0:.0f}秒)")


if __name__ == "__main__":
    main()
