# -*- coding: utf-8 -*-
"""主人公AIの「思っていること」を可視化する内省パネル（2026-07-09）。

用途：人間が脚本家として主人公AIと戦うとき（arena/play_vs_ai）に、AIの推理を
デバッグ表示する＝主人公AIへのフィードバック強化（どの読みが甘いか一目で分かる）。

2層で構成する（どちらも公開情報だけから作る＝神視点を使わない）：
1. belief 層（`belief_snapshot`）：可能世界追跡の生の確率。役職の周辺確率・ルール組の
   確率・犯人候補・残存世界数。agents/belief.Belief を直接使う（AIB完全所有）。
2. 戦術読み層（`estimates_from_records`）：AIが実際に行動の根拠にした派生読み
   （keyperson/各疑い集合/loop_lost 等）。閾値の二重定義を避けるため、
   ProbedProtagonist を実際に走らせた記録から取る（agents/debug）。

Streamlit 非依存：純データ＋markdown文字列を返す（描画は薄いラッパ st_render_mind）。
"""

from __future__ import annotations

from agents.belief import Belief

# 表示する役職（確率>0のものだけ後で絞る）
_SHOW_ROLES = ("キーパーソン", "キラー", "クロマク", "カルティスト", "シリアルキラー",
               "ミスリーダー", "フレンド", "タイムトラベラー", "ウィッチ",
               "ラバーズ", "メインラバーズ", "ファクター")


def belief_snapshot(cast, incidents_public, set_name, history,
                    top_roles: int = 5) -> dict:
    """公開履歴 history から主人公AIの belief を再現し、表示用サマリを返す。

    返り値:
      roles: {char: [(role, prob), ...]}（確率降順・上位 top_roles・prob>0のみ）
      rules: [(rule_y, (rule_xs...), prob), ...]（確率降順・上位3）
      culprits: {day: [char, ...]}（犯人候補）
      worlds_remaining / worlds_total
      certain: {char: role}（prob≥0.999 で確定した配役）
    """
    b = Belief(list(cast), list(incidents_public), set_name)
    b.observe(list(history))
    marg = b.role_marginals()
    roles = {}
    certain = {}
    for c in cast:
        d = marg.get(c, {})
        items = sorted(((r, p) for r, p in d.items() if p > 0.001),
                       key=lambda x: -x[1])
        if items:
            roles[c] = items[:top_roles]
            if items[0][1] >= 0.999:
                certain[c] = items[0][0]
    rules = sorted(b.rule_marginals().items(), key=lambda kv: -kv[1])
    rules = [(ry, rxs, p) for (ry, rxs), p in rules[:3] if p > 0.001]
    summ = b.summary()
    return {
        "roles": roles,
        "rules": rules,
        "culprits": {d: sorted(s) for d, s in b.culprit_candidates().items()},
        "worlds_remaining": summ["worlds_remaining"],
        "worlds_total": summ["worlds_total"],
        "certain": certain,
        # 可能世界0＝観測がbeliefモデルの外（イレギュラーの枠外配役=SK等・後述の既知課題）
        # ＝この推定は信頼できない。表示側で「推理不能」を明示する（AIC要望）。
        "unreliable": summ["worlds_remaining"] == 0,
    }


def _pct(p: float) -> str:
    return f"{p * 100:.0f}%"


def render_mind_md(snap: dict, estimates: dict | None = None) -> str:
    """belief_snapshot（＋任意で estimates）を人間可読の markdown にする。"""
    lines: list[str] = []
    wr, wt = snap["worlds_remaining"], snap["worlds_total"]
    if snap.get("unreliable"):
        lines.append("> ⚠️ **推理不能（可能世界0）**：観測がbeliefモデルの外に出ました"
                     "（イレギュラーの枠外配役＝SK等をモデル化していない既知課題）。"
                     "以下の役職/ルール推定は**信頼できません**。")
        lines.append("")
    frac = f"（{_pct(wr / wt)} 残）" if wt else ""
    lines.append(f"**可能世界**：{wr:,} / {wt:,}{frac}"
                 "　←小さいほどAIは配役を絞れている")

    if snap["rules"]:
        lines.append("\n**ルール推定**（AIが最有力と見ているルールY×X）")
        for ry, rxs, p in snap["rules"]:
            rx = "／".join(rxs) if rxs else "—"
            lines.append(f"- {_pct(p)}　{ry}　×　{rx}")

    if snap["roles"]:
        lines.append("\n**役職の読み**（各キャラの確率・上位5位まで）")
        lines.append("| キャラ | 読み（確率降順・上位5） |")
        lines.append("|---|---|")
        for c, items in snap["roles"].items():
            top5 = "／".join(f"{r} {_pct(p)}" for r, p in items[:5]) or "—"
            mark = "🔒" if c in snap["certain"] else ""
            lines.append(f"| {mark}{c} | {top5} |")

    if snap["culprits"]:
        lines.append("\n**犯人候補**（事件日ごと・AIが冷やす照準）")
        for d, cs in sorted(snap["culprits"].items()):
            tag = f"**{cs[0]}に確定**" if len(cs) == 1 else "／".join(cs)
            lines.append(f"- D{d}：{tag}")

    if estimates:
        lines.append("\n**戦術読み**（AIが行動の根拠にした派生値）")
        _labels = [
            ("_keyperson", "護るKP"), ("_killer", "キラー"),
            ("_sk_suspects", "SK疑い"), ("_kuromaku_suspects", "クロマク疑い"),
            ("_cultist_suspects", "カルティスト疑い"),
            ("_friend_guards", "護るフレンド"),
        ]
        for key, label in _labels:
            v = estimates.get(key)
            if v:
                vs = "／".join(v) if isinstance(v, list) else str(v)
                lines.append(f"- {label}：{vs}")
        flags = [lab for key, lab in
                 (("_loop_lost", "⚠負け確ループ（情報収穫モード）"),
                  ("_experiment", "実験モード"),
                  ("_kp_doomed", "KP防衛を諦め（FB勝負）"))
                 if estimates.get(key)]
        if flags:
            lines.append("- " + "／".join(flags))
    return "\n".join(lines)


def estimates_from_records(records: list[dict], loop: int, day: int) -> dict:
    """ProbedProtagonist の記録から、指定 (loop,day) の派生読みを取る（最初の席の分）。"""
    for rec in records:
        if rec.get("loop") == loop and rec.get("day") == day \
                and rec.get("decision") == "set_card":
            return rec.get("estimates", {})
    # 見つからなければ最後の set_card 記録
    for rec in reversed(records):
        if rec.get("decision") == "set_card":
            return rec.get("estimates", {})
    return {}


def st_render_mind(st, snap: dict, estimates: dict | None = None) -> None:
    """Streamlit へ内省パネルを描く薄いラッパ（役職説明より下・デバッグ用）。"""
    st.markdown(render_mind_md(snap, estimates))
