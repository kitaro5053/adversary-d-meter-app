"""合法手ランダムbot（M1）。

用途:
- シミュレータのファジングテスター（合法手だけで1ゲームが必ず完走することの検証）
- 上位エージェント（heuristic/LLM）の対戦相手・回帰ベースライン

seed 固定で決定的（flow の決定順序も決定的なので、同じ seed → 同じゲーム）。
"""

from __future__ import annotations

import random


class RandomBot:
    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        return self.rng.choice(options)
