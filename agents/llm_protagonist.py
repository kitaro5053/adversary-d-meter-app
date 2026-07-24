"""LLM主人公AI（M5）— belief の可能世界サマリを注入して判断させる。

設計（本体の思想＝「LLMは判断・計算は決定的」）:
- **推理そのものは belief.py が決定的に行う**（可能世界の全列挙→観測で絞り込み）。
  LLMには生の推理をさせず、絞り込み済みの summary（役職確率・ルール候補・犯人候補）を渡し、
  「どの手を出すか」だけ選ばせる（翻訳器とengineの関係と同じ）。
- 非合法/パース失敗/API例外はリトライ→**ヒューリスティック主人公にフォールバック**（belief駆動）。
- 主人公は3席を1体で担当。暗躍禁止の自滅回避（1ターン1枚）は options を絞って構造的に担保
  ＝LLMにもフォールバックにも2枚目の暗躍禁止を見せない。

★LLMに渡すのは公開情報（protagonist_view）＋belief summary のみ。配役・犯人は伏せたまま。
"""

from __future__ import annotations

import json

from engine.data import unrest_threshold_of

from .belief import Belief
from .heuristic_protagonist import HeuristicProtagonist
from .llm_mastermind import _parse_choice

_SYSTEM = (
    "あなたは惨劇RoopeR（First Steps）の主人公AIです。目的は脚本家の敗北条件成立と主人公の死を"
    "防ぎ、いずれかのループを生き延びること。あなたは脚本家の手を見ずに（伏せて同時に）カードを"
    "置きます。推理結果（belief）は与えられるので、それを踏まえて最善の防御手を選びます。"
    "守りの要点＝推定キーパーソンの暗躍を溜めさせない／敗北条件ボードの暗躍を除去・抑制／"
    "不安を下げて事件を防ぐ／推定キラーの接近を止める／友好を貯めて有益な友好能力を使う。"
    "必ず candidates の index を1つ、{\"choice\": <整数>, \"reason\": \"<短い理由>\"} の"
    "JSONだけで出力してください。前置き・コードフェンスは付けない。"
)


def _describe(o: dict) -> str:
    if o.get("action") == "pass":
        return "パス"
    if "card" in o and "target" in o:
        return f'{o["card"]}を{o["target"]}に'
    if "ability" in o:
        return f'{o["character"]}の{o["ability"]}（対象:{o["target"]}）'
    if "target" in o:
        return f'対象:{o["target"]}'
    return json.dumps(o, ensure_ascii=False)


def _belief_brief(summary: dict) -> str:
    rt = summary.get("role_targets", {})
    roles = " / ".join(f'{r}={v["name"]}({v["prob"]:.0%})' for r, v in rt.items())
    rules = " ".join(f'{x["rule_y"]}×{x["rule_x"]}({x["prob"]:.0%})'
                     for x in summary.get("rule_top", []))
    culp = " ".join(f'{d}日目={"/".join(c)}'
                    for d, c in summary.get("culprit_candidates", {}).items())
    return (f'推理（可能世界 残{summary.get("worlds_remaining","?")}）\n'
            f'役職推定: {roles or "不明"}\nルール候補: {rules or "不明"}\n'
            f'犯人候補: {culp or "不明"}')


def _board_brief(view: dict) -> str:
    lines = []
    for c in view["characters"]:
        if c["area"] is None:
            continue
        tags = []
        if c["unrest"]:
            th = unrest_threshold_of(c["name"])
            tags.append(f'不安{c["unrest"]}' + (f'/臨界{th}' if th else ''))
        if c["goodwill"]:
            tags.append(f'友好{c["goodwill"]}')
        if c["anyaku"]:
            tags.append(f'暗躍{c["anyaku"]}')
        rr = f'[{c["revealed_role"]}]' if c.get("revealed_role") else ""
        alive = "" if c["alive"] else "(死体)"
        lines.append(f'{c["name"]}{rr}{alive}@{c["area"]} {" ".join(tags)}'.rstrip())
    boards = " ".join(f'{a}:暗躍{n}' for a, n in view["board_anyaku"].items() if n)
    incs = " ".join(f'{i["day"]}日目{i["name"]}' for i in view["incidents"])
    return (f'ループ{view["loop"]}/{view["loops_total"]} {view["day"]}日目\n'
            f'キャラ: {" / ".join(lines)}\nボード暗躍: {boards or "なし"}\n事件予定: {incs}')


class LLMProtagonist:
    def __init__(self, client, model: str = "claude-sonnet-5",
                 max_retries: int = 2, fallback_seed: int = 0):
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self._fallback = HeuristicProtagonist(fallback_seed)
        self._belief: Belief | None = None
        self._turn = None
        self._kinshi_used = False
        self.stats = {"calls": 0, "retries": 0, "fallbacks": 0}

    def _sync(self, view: dict) -> None:
        if self._belief is None:
            cast = [c["name"] for c in view["characters"]]
            self._belief = Belief(cast, view.get("incidents", []),
                                  set_name=view.get("set", "FS"))
        turn = (view["loop"], view["day"])
        if turn != self._turn:
            self._turn = turn
            self._kinshi_used = False
            self._belief.observe(view.get("history", []))

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        self._sync(view)
        # 暗躍禁止の自滅回避：2枚目は候補から外す（LLMにもフォールバックにも見せない）
        opts = options
        if decision == "set_card" and self._kinshi_used:
            opts = [o for o in options if o.get("card") != "暗躍禁止"] or options

        chosen = self._decide_inner(view, decision, opts)
        if decision == "set_card" and chosen.get("card") == "暗躍禁止":
            self._kinshi_used = True
        return chosen

    def _decide_inner(self, view: dict, decision: str, options: list[dict]) -> dict:
        if len(options) == 1:
            return options[0]
        self.stats["calls"] += 1
        prompt = (
            f"# 盤面\n{_board_brief(view)}\n\n# {_belief_brief(self._belief.summary())}\n\n"
            f"# 意思決定: {decision}\n# 合法手（candidates）\n"
            + "\n".join(f'{i}: {_describe(o)}' for i, o in enumerate(options))
            + "\n\ncandidates の index を1つ JSON で返してください。"
        )
        for attempt in range(self.max_retries + 1):
            idx = self._ask(prompt)
            if idx is not None and 0 <= idx < len(options):
                return options[idx]
            if attempt < self.max_retries:
                self.stats["retries"] += 1
        self.stats["fallbacks"] += 1
        return self._fallback.decide(view, decision, options)

    def _ask(self, prompt: str) -> int | None:
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=256,
                system=[{"type": "text", "text": _SYSTEM}],
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text")
        except Exception:  # noqa: BLE001  API失敗はフォールバックへ
            return None
        return _parse_choice(text)
