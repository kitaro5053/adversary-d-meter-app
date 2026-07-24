"""LLM脚本家AI（M4）。

view（脚本家＝全情報）と合法手を構造化プロンプトで渡し、LLMに「番号で1手選ばせる」。
本体アプリと同じ思想＝**LLMは手の選択（判断）だけ、合法手の列挙・裁定は決定的コード**。
非合法・パース失敗は決定的にリカバリ：規定回数リトライ→なお失敗ならヒューリスティック脚本家に
フォールバック（ゲームを止めない＝決定ログには llm.retries/fallback を残して健全性を測る）。

依存はAnthropicクライアント（app.py と同じ `client.messages.create` パターン。temperatureは
新世代モデルで廃止のため渡さない＝v0.16.1の教訓）。API keyが無い場面ではインスタンス化しない。
"""

from __future__ import annotations

import json

from .heuristic import HeuristicMastermind

_SYSTEM = (
    "あなたは惨劇RoopeR（First Steps）の脚本家AIです。目的は全ループで主人公を敗北させること。"
    "与えられる board（あなたは全情報を持つ）と candidates（合法手）から、勝ち筋に最も資する手を"
    "1つ選びます。勝ち筋＝キーパーソン殺害／敗北条件ボードの暗躍を2以上／主人公殺害。"
    "必ず candidates の index を1つ、JSONで {\"choice\": <整数>, \"reason\": \"<短い理由>\"} と"
    "だけ出力してください。前置き・コードフェンスは付けない。"
)


def _describe(o: dict) -> str:
    if o.get("action") == "pass":
        return "パス"
    if "card" in o and "target" in o:
        return f'{o["card"]}を{o["target"]}に'
    if "action" in o:
        t = f'→{o["target"]}' if "target" in o else ""
        return f'{o["action"]}{t}'
    if "target" in o:
        return f'対象:{o["target"]}'
    if "name" in o and "area" in o:
        return f'{o["name"]}を{o["area"]}へ'
    return json.dumps(o, ensure_ascii=False)


def _board_brief(view: dict) -> str:
    """トークン節約のため view を要点だけの文字列に圧縮（脚本家視点＝配役つき）。"""
    roles = view.get("roles", {})
    lines = []
    for c in view["characters"]:
        if c["area"] is None:
            continue
        tags = []
        if c["unrest"]:
            tags.append(f'不安{c["unrest"]}')
        if c["goodwill"]:
            tags.append(f'友好{c["goodwill"]}')
        if c["anyaku"]:
            tags.append(f'暗躍{c["anyaku"]}')
        alive = "" if c["alive"] else "(死体)"
        role = roles.get(c["name"], "?")
        lines.append(f'{c["name"]}[{role}]{alive}@{c["area"]} {" ".join(tags)}'.rstrip())
    boards = " ".join(f'{a}:暗躍{n}' for a, n in view["board_anyaku"].items() if n)
    incs = " ".join(f'{i["day"]}日目{i["name"]}(犯人{i.get("culprit","?")})'
                    for i in view["incidents"])
    return (f'ループ{view["loop"]}/{view["loops_total"]} {view["day"]}日目'
            f'（ルールY:{view.get("rule_y")} X:{view.get("rule_x")}）\n'
            f'キャラ: {" / ".join(lines)}\n'
            f'ボード暗躍: {boards or "なし"}\n事件: {incs}')


class LLMMastermind:
    def __init__(self, client, model: str = "claude-sonnet-5",
                 max_retries: int = 2, fallback_seed: int = 0):
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self._fallback = HeuristicMastermind(fallback_seed)
        # 健全性メトリクス（決定ログ・runnerで参照）
        self.stats = {"calls": 0, "retries": 0, "fallbacks": 0, "parse_errors": 0}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        self.stats["calls"] += 1
        prompt = (
            f"# 局面（board）\n{_board_brief(view)}\n\n"
            f"# 今の意思決定: {decision}\n# 合法手（candidates）\n"
            + "\n".join(f'{i}: {_describe(o)}' for i, o in enumerate(options))
            + "\n\ncandidates の index を1つ JSON で返してください。"
        )
        for attempt in range(self.max_retries + 1):
            idx = self._ask(prompt)
            if idx is not None and 0 <= idx < len(options):
                return options[idx]
            if attempt < self.max_retries:
                self.stats["retries"] += 1
        # 規定回数失敗 → 決定的フォールバック（ゲームを止めない）
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
        except Exception:  # noqa: BLE001  API失敗はフォールバックへ（健全性はstatsで見る）
            return None
        return _parse_choice(text)


def _parse_choice(text: str) -> int | None:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj.get("choice"), int):
                return obj["choice"]
        except (json.JSONDecodeError, AttributeError):
            pass
    return None
