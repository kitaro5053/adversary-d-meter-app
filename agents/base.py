"""AIプレイヤーの共通インターフェース。

方針（AIプレイヤー計画 §4）:
- エージェントは decide() 1メソッドだけを実装する。合法手の列挙・裁定・効果適用は
  すべてシミュレータ側（sim/legal.py, sim/effects.py）＝AIはルールをハードコードしない。
- view はそのエージェントに「見えている情報」だけ（sim/views.py が生成）。
  decision は決定種別（"set_card" / "mastermind_ability" / "goodwill_ability" /
  "incident_choice" / "turn_end_ability" / "loop_start_area"）。
- 返り値は options の要素そのもの（同一性でなく等価で判定）。options 外を返すと
  flow が ValueError＝非合法手として検出する（LLMエージェントの健全性指標）。
"""

from __future__ import annotations

from typing import Protocol


class Agent(Protocol):
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        """options から1つ選んで返す。"""
        ...
