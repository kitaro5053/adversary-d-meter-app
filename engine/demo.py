"""エンジンのデモ実行: python -m engine.demo  （プロジェクト直下から）

Q13を入力に、決定的な裁定と「感度フラグ（仮定が結論を左右する）」を表示する。
"""

from engine import Board, Character, Placement, resolve_action_phase, sensitivity_check, validate_board


def demo_q13() -> None:
    b = Board()
    b.add_character(Character("カルティスト", role="カルティスト", area="神社"))
    b.add_placement(Placement("mastermind", "移動←→", "カルティスト", "character"))
    b.add_placement(Placement("mastermind", "暗躍+2", "神社", "board"))
    b.add_placement(Placement("p1", "暗躍禁止", "神社", "board"))

    print("【Q13】神社のカルティストが横移動で病院へ／神社に暗躍+2＋暗躍禁止1枚")
    print("- 前提検証:", "違反なし" if not validate_board(b) else validate_board(b))

    adj = resolve_action_phase(b)
    print(f"- 移動結果: カルティスト → {adj.moves['カルティスト']}")
    t = adj.targets["神社"]
    print(f"- 神社の暗躍増加: +{t.delta}（暗躍+2={t.anyaku_plus} / 暗躍禁止実効={t.kinshi_active}）")

    for f in sensitivity_check(b, adj, "神社"):
        print(f"- ⚠ 感度: 現在 +{f.current} だが、{f.condition} → +{f.alternative} に反転")

    print("\n→ 回答例（AIが返す形）:")
    print("  神社は +0 です（カルティストは病院へ抜け、神社の暗躍禁止が暗躍+2を打ち消すため）。")
    print("  ただしこの結論は『他に主人公の暗躍禁止が無い』前提に依存します。")
    print("  もし別の主人公が暗躍禁止をもう1枚出していれば、自滅により神社は +2 に反転します。")


if __name__ == "__main__":
    demo_q13()
