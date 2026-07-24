# -*- coding: utf-8 -*-
"""ループ回数の見積り（KB: 70_script_creation_guide.md の見積り表・p48）を決定的に計算する。

脚本工房（script_studio.py）の一部品。streamlit非依存の純関数＝テスト可能。
★表はBTX基準（脚本家の書 p48）。表に無いルール（FSの復讐者の灯火・守るべき場所・
切り裂き魔の影・最低の却本）は捏造せず None（要確認）を返し、内訳にその旨を記す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.data import is_shoujo

# ルールY → 基本値（70の表）。少女/蝶の加算は estimate() 内で処理。
_Y_VALUES = {
    "殺人計画": 1.8,
    "封印されしモノ": 1.5,
    "僕と契約しようよ！": 1.0,   # +0.4/盤面の少女
    "未来改変プラン": 1.3,       # +0.5/蝶の羽ばたき
    "巨大時限爆弾Xの存在": 1.0,
}
_X_VALUES = {
    "友情サークル": 1.0,
    "恋愛風景": 1.0,
    "潜む殺人鬼": 0.8,
    "不穏な噂": 0.5,
    "妄想拡大ウイルス": 0.0,
    "因果の糸": 0.5,
    "不定因子χ": 0.8,
}


@dataclass
class LoopEstimate:
    total: float | None                 # 目安ループ数（不明成分があれば None）
    parts: list[str] = field(default_factory=list)   # 内訳（人間可読）
    unknown: list[str] = field(default_factory=list)  # 表に無い（要確認）成分


def estimate_loops(script) -> LoopEstimate:
    """sim.state.Script からループ回数の目安を計算する（KB: 70 p48 の表）。

    合計値＝目安ループ数（70「これらの値を合計した値をループ回数の目安とする」）。
    """
    parts: list[str] = []
    unknown: list[str] = []
    total = 0.0

    # ルールY
    yv = _Y_VALUES.get(script.rule_y)
    if yv is None:
        unknown.append(f"ルールY「{script.rule_y}」は見積り表（BTX基準）に無い＝要確認")
    else:
        total += yv
        parts.append(f"ルールY {script.rule_y}: +{yv}")
        if script.rule_y == "僕と契約しようよ！":
            n_girls = sum(1 for n in script.cast if is_shoujo(n))
            add = 0.4 * n_girls
            total += add
            parts.append(f"　盤面の少女 {n_girls}人 × +0.4 = +{add:.1f}")
        if script.rule_y == "未来改変プラン":
            n_bf = sum(1 for i in script.incidents if i.name == "蝶の羽ばたき")
            add = 0.5 * n_bf
            total += add
            parts.append(f"　蝶の羽ばたき {n_bf}件 × +0.5 = +{add:.1f}")

    # ルールX（FSは1つ・BTXは2つ）
    for rx in script.rule_xs:
        xv = _X_VALUES.get(rx)
        if xv is None:
            unknown.append(f"ルールX「{rx}」は見積り表（BTX基準）に無い＝要確認")
        else:
            total += xv
            parts.append(f"ルールX {rx}: +{xv}")

    # 事件
    if any(i.name == "病院の事件" for i in script.incidents):
        total += 0.4
        parts.append("病院の事件あり: +0.4")
    n_inc = len(script.incidents)
    if n_inc <= 3:
        total -= 0.4
        parts.append(f"事件{n_inc}件（3以下）: -0.4")
    elif n_inc >= 5:
        total += 0.4
        parts.append(f"事件{n_inc}件（5以上）: +0.4")

    # 日数
    if script.days_per_loop <= 5:
        total -= 0.6
        parts.append(f"{script.days_per_loop}日（5日以下）: -0.6")
    elif script.days_per_loop == 6:
        total -= 0.2
        parts.append("6日: -0.2")

    if unknown:
        return LoopEstimate(total=None, parts=parts, unknown=unknown)
    return LoopEstimate(total=round(total, 2), parts=parts, unknown=unknown)


def describe_estimate(est: LoopEstimate, planned_loops: int | None = None) -> list[str]:
    """見積りを人間可読の行リストへ（studioの表示用）。"""
    lines: list[str] = []
    if est.total is None:
        lines.append("**ループ回数の目安：算出不能（要確認）**")
        lines += [f"- ⚠ {u}" for u in est.unknown]
        if est.parts:
            lines.append("（判明している成分）")
            lines += [f"- {p}" for p in est.parts]
        return lines
    lines.append(f"**ループ回数の目安（KB:70 p48の見積り表）: {est.total}**")
    lines += [f"- {p}" for p in est.parts]
    if planned_loops is not None:
        d = planned_loops - est.total
        if d >= 1.0:
            lines.append(f"- 予定ループ{planned_loops}は目安より多め（+{d:.1f}）＝易しめに寄る")
        elif d <= -1.0:
            lines.append(f"- ⚠ 予定ループ{planned_loops}は目安より少なめ（{d:.1f}）＝難しめ。"
                         "タブー（勝ち筋なし）に近づいていないか要点検")
        else:
            lines.append(f"- 予定ループ{planned_loops}は目安と概ね整合")
    lines.append("- ※あくまで目安（KB:70）。実測は下のAI自己対戦βで。")
    return lines
