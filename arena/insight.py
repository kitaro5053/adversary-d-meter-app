"""リプレイの俯瞰情報（決定的・Streamlit非依存＝テスト可能）。

ビューアの「上に全貌・下に詳細」構成（ユーザー要望 2026-07-04）の材料を作る:
- loop_summaries: 各ループが何日目に何起因で終わったか（＋神視点の真因）。
- build_insight: 日単位の belief 推移（ルールY/X確率・可能世界数）と、
  判明タイムライン（何が確定/消去され、その根拠は何か・事件の発生経過）。

すべて公開イベント履歴（history）だけから計算する（beliefと同じ規律）。
神視点情報は omniscient=True のときだけ secret_log から付け足す。
"""

from __future__ import annotations

from agents.belief import Belief

_EPS = 1e-9


def _day_points(history: list[dict]) -> list[tuple[int, int]]:
    """日境界 (loop, day) の時系列（day=0のループ開始は前日の点に吸収）。"""
    return sorted({(e.get("loop", 0), e.get("day", 0)) for e in history
                   if e.get("loop") and e.get("day", 0) >= 1})


def _point_label(lp: int, dy: int) -> str:
    return f"L{lp}・{dy}日目"


# ---------------------------------------------------------------------------
# ループ経過サマリ（全貌）
# ---------------------------------------------------------------------------

def loop_summaries(script, meta: dict, omniscient: bool = False) -> list[dict]:
    """各ループの終了日・終了のしかた・結果。神視点なら真因（死因・敗北条件）も。"""
    history = meta.get("history", [])
    secret = meta.get("secret_log", [])
    loops = sorted({e["loop"] for e in history if e.get("loop")})
    rows: list[dict] = []
    for lp in loops:
        evs = [e for e in history if e.get("loop") == lp]
        end_day = max((e.get("day", 0) for e in evs), default=0)
        loop_end = next((e for e in evs if e.get("event") == "loop_end"), None)
        how = loop_end.get("reason", "") if loop_end else "最終日まで進行"
        go = next((e for e in evs if e.get("event") == "game_over"), None)
        if go:
            result = "主人公の勝利 🎉" if go["winner"] == "protagonist" else "脚本家の勝利 💀"
        elif any(e.get("event") == "loop_result" for e in evs):
            result = "主人公の敗北 → 次ループへ"
        else:
            result = "—"
        n_incidents = sum(1 for e in evs if e.get("event") == "incident" and e.get("occurs"))
        row = {"ループ": lp, "終了日": end_day or "—", "終了のしかた": how,
               "発生事件": n_incidents, "結果": result}
        if omniscient:
            reasons = [str(e.get("reason", "")) for e in secret
                       if e.get("loop") == lp and e.get("event") in ("loop_end", "defeat")]
            causes = [str(e.get("cause", "")) for e in secret
                      if e.get("loop") == lp and e.get("event") == "protagonist_death"]
            row["真因（神視点）"] = "／".join([r for r in reasons + causes if r]) or "—"
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# belief 推移＋判明タイムライン
# ---------------------------------------------------------------------------

def _rule_probs(belief: Belief) -> tuple[dict, dict]:
    """(ルールYごとの確率, ルールXごとの確率) を rule_marginals から集計。"""
    y: dict[str, float] = {}
    x: dict[str, float] = {}
    for (ry, rxs), p in belief.rule_marginals().items():
        y[ry] = y.get(ry, 0.0) + p
        for rx in rxs:
            x[rx] = x.get(rx, 0.0) + p
    return y, x


def _pinned_roles(belief: Belief) -> dict[str, str]:
    """確率1.0で確定しているキャラ→役職（パーソン確定も含む）。"""
    out: dict[str, str] = {}
    for c, marg in belief.role_marginals().items():
        for r, p in marg.items():
            if p >= 1.0 - _EPS:
                out[c] = r
    return out


def build_insight(script, history: list[dict]) -> dict:
    """日単位の belief 推移と判明タイムラインを1パスで構築する。

    返り値:
      timeline: [{label, loop, day, worlds, y_probs, x_probs}]（先頭は観測前＝「開始」）
      deductions: [{時点, 種別, 内容}]（事件の発生経過・公開・確定・消去・世界数減少）
      worlds_total: 観測前の可能世界数
    """
    incidents_pub = [{"day": i.day, "name": i.name} for i in script.incidents]
    belief = Belief(script.cast, incidents_pub, set_name=script.set_name)

    worlds_total = belief.summary()["worlds_remaining"]
    y0, x0 = _rule_probs(belief)
    timeline = [{"label": "開始", "loop": 0, "day": 0, "worlds": worlds_total,
                 "y_probs": y0, "x_probs": x0}]
    deductions: list[dict] = []

    prev_worlds = worlds_total
    prev_pinned = _pinned_roles(belief)
    prev_y_alive = {ry for ry, p in y0.items() if p > _EPS}
    prev_x_alive = {rx for rx, p in x0.items() if p > _EPS}
    prev_culprit_single: set[int] = set()

    for lp, dy in _day_points(history):
        label = _point_label(lp, dy)
        day_evs = [e for e in history if (e.get("loop"), e.get("day")) == (lp, dy)]
        prefix = [e for e in history if (e.get("loop", 0), e.get("day", 0)) <= (lp, dy)]
        belief.observe(prefix)
        y, x = _rule_probs(belief)
        worlds = belief.summary()["worlds_remaining"]
        timeline.append({"label": label, "loop": lp, "day": dy, "worlds": worlds,
                         "y_probs": y, "x_probs": x})

        # --- 事件の発生経過 ---
        for e in day_evs:
            if e.get("event") == "incident":
                mark = "発生" if e.get("occurs") else "発生せず"
                deductions.append({"時点": label, "種別": "事件",
                                   "内容": f'『{e.get("name")}』{mark}'})
        # --- 公開（観測の根拠） ---
        for e in day_evs:
            ev = e.get("event")
            if ev == "role_reveal":
                deductions.append({"時点": label, "種別": "公開",
                                   "内容": f'〈{e["name"]}〉の役職＝{e["role"]}'})
            elif ev == "culprit_reveal":
                deductions.append({"時点": label, "種別": "公開",
                                   "内容": f'{e["day"]}日目の事件の犯人＝〈{e["name"]}〉'})
            elif ev == "rule_reveal":
                deductions.append({"時点": label, "種別": "公開",
                                   "内容": f'ルールXの一つ＝{e["rule_x"]}'})
        # --- 推理による確定（役職） ---
        pinned = _pinned_roles(belief)
        for c, r in pinned.items():
            if prev_pinned.get(c) != r:
                revealed = any(e.get("event") == "role_reveal" and e.get("name") == c
                               for e in day_evs)
                src = "（公開）" if revealed else "（推理で確定）"
                deductions.append({"時点": label, "種別": "確定",
                                   "内容": f'〈{c}〉＝{r} {src}'})
        prev_pinned = pinned
        # --- ルールの消去 ---
        y_alive = {ry for ry, p in y.items() if p > _EPS}
        for ry in sorted(prev_y_alive - y_alive):
            deductions.append({"時点": label, "種別": "消去",
                               "内容": f'ルールY『{ry}』の可能性が消えた'})
        prev_y_alive = y_alive
        x_alive = {rx for rx, p in x.items() if p > _EPS}
        gone_x = sorted(prev_x_alive - x_alive)
        if gone_x:
            deductions.append({"時点": label, "種別": "消去",
                               "内容": "ルールX『" + "』『".join(gone_x) + "』の可能性が消えた"})
        prev_x_alive = x_alive
        # --- 犯人の確定 ---
        for d, cands in belief.culprit_candidates().items():
            if len(cands) == 1 and d not in prev_culprit_single:
                prev_culprit_single.add(d)
                deductions.append({"時点": label, "種別": "確定",
                                   "内容": f'{d}日目の事件の犯人＝〈{next(iter(cands))}〉'})
        # --- 可能世界の減少 ---
        if worlds != prev_worlds:
            deductions.append({"時点": label, "種別": "世界",
                               "内容": f'可能世界 {prev_worlds:,} → {worlds:,}'})
            prev_worlds = worlds

    return {"timeline": timeline, "deductions": deductions, "worlds_total": worlds_total}
