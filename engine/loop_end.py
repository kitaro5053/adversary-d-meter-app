"""ループ終了/終了フェイズのタイミング裁定（フェーズD）。

範囲（KB: 00 ループの終了と勝敗判定／敗北条件成立とループ継続）:
- **敗北条件が途中で成立してもループは中断しない**（最終日まで進む）。
- 例外＝**ループ終了効果**（主人公死亡・キーパーソン死亡 等）は**その時点で即ループ終了**。
- **敗北判定はループ終了時**に行う。
- 主人公が死亡した場合は主人公の敗北として直ちにループ終了。

敗北条件そのものは脚本依存（ネタバレ）＝engineは成否を判定しない（ユーザー入力）。
ここではタイミング（今ループが終了するか／敗北判定をいつ行うか）だけを決定的に裁定する。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoopEndResult:
    loop_ends_now: bool          # この時点でループが終了するか
    defeat: bool | None          # 主人公の敗北か（None＝継続中/敗北条件の入力待ちで未判定）
    reasons: list[str] = field(default_factory=list)
    needs_confirmation: bool = False


def resolve_loop_end(spec: dict) -> LoopEndResult:
    """ループ終了のタイミングと敗北判定を裁定する。

    spec: {
        "protagonist_death": bool,   # 主人公が死亡したか（＝無条件で敗北・即終了）
        "loop_end_effect": bool,     # ループ終了効果（キーパーソン死亡等）が発生したか
        "is_final_day_end": bool,    # 最終日のターン終了フェイズか
        "defeat_condition_met": bool | None,  # ループ終了時点で敗北条件が成立しているか（脚本依存）
    }
    """
    ped = bool(spec.get("protagonist_death", False))
    lee = bool(spec.get("loop_end_effect", False))
    fde = bool(spec.get("is_final_day_end", False))
    dcm = spec.get("defeat_condition_met")  # None なら未指定

    reasons: list[str] = []

    # 主人公死亡 → 無条件で即終了・敗北。
    if ped:
        reasons.append("主人公が死亡 → その時点で即ループ終了。主人公の敗北（KB: 00）。")
        return LoopEndResult(True, True, reasons)

    # ループ終了効果でも最終日終了でもないなら、ループは中断しない。
    if not lee and not fde:
        reasons.append(
            "ループ終了効果は無い。**敗北条件が途中で成立してもループは中断せず最終日まで進む**"
            "（KB: 00）。敗北判定はループ終了時。"
        )
        return LoopEndResult(False, None, reasons)

    # ここからループ終了（ループ終了効果 or 最終日終了）。
    if lee:
        reasons.append("ループ終了効果（キーパーソン死亡等）が発生 → その時点で即ループ終了（KB: 00）。")
    else:
        reasons.append("最終日のターン終了フェイズ → ループ終了。")

    # 敗北判定はループ終了時。敗北条件の成否は脚本依存＝入力。
    if dcm is None:
        reasons.append("敗北判定はループ終了時に行う。敗北条件の成否（脚本依存）が不明＝要確認。")
        return LoopEndResult(True, None, reasons, needs_confirmation=True)
    if dcm:
        reasons.append("ループ終了時に敗北条件が成立 → 主人公の敗北。")
        return LoopEndResult(True, True, reasons)
    reasons.append("ループ終了時に敗北条件は未成立 → このループは主人公の勝利。")
    return LoopEndResult(True, False, reasons)
