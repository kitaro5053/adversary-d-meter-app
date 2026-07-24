# -*- coding: utf-8 -*-
"""DP-2 Stage 3a：mm本命選定 と 評価器（2M-1）の**食い違いを記録するだけ**の監査＋cost詐称乖離表。

★安全弁＝Stage 3b（初めて mm の本命が変わる）に入る前に、「評価器に替えるとどこが変わるか」
＋「評価器の cost がどのノードで実効難度を過小評価するか（cost詐称）」を先に数える。
decoy Stage 0b と同じ作法＝`agents/heuristic.py` を1行も変更せず `_analyze` を外から読むだけ
＝bit-for-bit 不変。

## cost詐称乖離表（FableA追加要件 2026-07-21・測定可能な3列に限定）
評価器の `WinLineStatus.cost`＝残AND条件数だが、条件の**種類**で実効難度が全く違う。
乖離表は**測定可能なもの**に限る（「mmが実際に決められた率」は mm が追っていない筋には
存在しない＝測定不能＝要求外）：
- **(i) cost**（残AND条件数）＝評価器がそのまま本命の安さに使う値。
- **(ii) 位置条件フラグ**（`has_position`）＝残条件に「同席・present固定・特定エリア誘導」を
  含むか＝cost が過小評価する種別（`fr.incident` の罠の一般化）。
- **(iii) コーパス自然成立率**＝そのノードが対局中に **mm の意図と無関係に**どれだけ「動いた/
  成立した」か。`live_rate`（脚本上ありえて到達しうる＝dormant でない割合）と
  `win_rate`（既に成立＝winning の割合）で測る。追っていない筋の「決めやすさ」の代理指標。
`fr.incident` は cost=1 だが位置条件フラグ立ち＋自然成立率で「安いのに自然には決まらない」が
数字で出る＝3b の追加候補から外す判定軸（この表がその根拠）。

使い方：
    PYTHONHASHSEED=0 PYTHONPATH=. python arena/goal_audit.py --days 3 --seeds 8
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field

# --- conds の種類分類（cost詐称の材料）＝ラベルのキーワード写像（新規判定を作らない）-----
_ANYAKU_KW = ("暗躍≥", "暗躍が2", "暗躍が4", "暗躍2", "暗躍4")
_POSITION_KW = ("同エリア", "2人きり", "同居", "present", "プレゼント", "引き込み", "退避")
_INCIDENT_KW = ("事件", "犯人", "不安臨界", "不安が", "発生")


def _cond_kind(label: str) -> str:
    """1条件を anyaku / position / incident / other に主分類（実効難度の種類）。"""
    if any(k in label for k in _POSITION_KW):
        return "position"       # ★位置細工＝cost に現れない実効難度の主因
    if any(k in label for k in _INCIDENT_KW):
        return "incident"       # 打点会計＋present固定が畳み込まれる
    if any(k in label for k in _ANYAKU_KW):
        return "anyaku"         # 暗躍で素直に進む＝cost が効く
    return "other"


def cond_profile(node_id: str) -> dict[str, int]:
    """ノードの conds を種類別に数える（cost詐称の構造的説明）。"""
    from arena.losstree import LOSS_NODES
    prof: Counter = Counter()
    for c in (LOSS_NODES.get(node_id) or {}).get("conds", []):
        prof[_cond_kind(c.get("label", ""))] += 1
    return dict(prof)


@dataclass
class GoalDisagreement:
    set_name: str
    seed: int
    loop: int
    day: int
    main_current: str            # 現行 funded 代表ノード
    main_eval: str               # 評価器の最安 live ノード
    eval_cost: int | None
    note: str = ""


@dataclass
class GoalAuditResult:
    total: int = 0
    agree: int = 0
    swaps: Counter = field(default_factory=Counter)          # (現行→評価器) の頻度
    eval_picks: Counter = field(default_factory=Counter)     # 評価器が最安に選んだノード頻度
    # ★(iii) コーパス自然成立率の材料＝各ノードの status を全観測局面で数える。
    status_counts: dict = field(default_factory=dict)        # node -> Counter[status]
    samples: list[GoalDisagreement] = field(default_factory=list)

    @property
    def disagree(self) -> int:
        return self.total - self.agree

    def _tally(self, statuses) -> None:
        for w in statuses:
            self.status_counts.setdefault(w.node_id, Counter())[w.status] += 1

    def natural_rates(self, node_id: str) -> tuple[float, float]:
        """(live_rate, win_rate)＝そのノードが動いた/成立した割合（mm意図と無関係）。
        分母は「脚本上ありえた観測局面」（not_in_script は除く＝そもそも存在しない筋）。"""
        c = self.status_counts.get(node_id, Counter())
        seen = sum(n for s, n in c.items() if s != "not_in_script")
        if not seen:
            return 0.0, 0.0
        live = c.get("live", 0) + c.get("winning", 0)
        return round(live / seen, 2), round(c.get("winning", 0) / seen, 2)


def _eval_main(live: list) -> object | None:
    """評価器の本命候補＝最安 cost（同点は counter 低い＝反撃されにくい順）。
    ★これは『素朴な cost 選択』の再現＝この監査が測りたい当の対象（3b はこれを鵜呑みにしない）。"""
    if not live:
        return None
    return min(live, key=lambda w: (w.cost if w.cost is not None else 99, w.counter))


def audit_goal_view(mm, view: dict, set_name: str, seed: int,
                    statuses=None) -> GoalDisagreement | None:
    """1局面を監査（現行 funded 代表 vs 評価器最安 live）。view も mm も変更しない。
    statuses を渡せば evaluate_tree_mm を再呼び出ししない（run 側で1回だけ呼ぶ）。"""
    from arena.cs_plan import main_node_of
    from arena.decoy_audit import _main_path
    from arena.losstree_eval import evaluate_tree_mm

    a = mm._analyze(view)
    funded, path_costs = a.get("funded") or set(), a.get("path_costs") or {}
    if not funded:
        return None
    if statuses is None:
        statuses = evaluate_tree_mm(view)
    live = [w for w in statuses if w.status in ("live", "winning")]
    if not live:
        return None
    main_path = _main_path(funded, path_costs)
    cur = main_node_of(main_path, a.get("goal_boards"), view,
                       {w.node_id for w in live})
    best = _eval_main(live)
    if cur is None:
        return GoalDisagreement(set_name, seed, view.get("loop", 0), view.get("day", 0),
                                f"({main_path})", best.node_id, best.cost,
                                "現行本命が評価器で解決不可")
    if cur == best.node_id:
        return None
    return GoalDisagreement(set_name, seed, view.get("loop", 0), view.get("day", 0),
                            cur, best.node_id, best.cost)


def run_goal_audit(sets=("BTX", "FS"), seeds: int = 8, days: int = 3,
                   loops: int = 3, max_samples: int = 12) -> GoalAuditResult:
    from dataclasses import replace

    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import random_script, run_game
    from sim.views import mastermind_view

    res = GoalAuditResult()
    for set_name in sets:
        for seed in range(seeds):
            sc = replace(random_script(set_name, seed, days=days), loops=loops)
            snaps: list[dict] = []
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                     on_day_start=lambda s, *_a: snaps.append(mastermind_view(s)))
            for v in snaps:
                from arena.losstree_eval import evaluate_tree_mm
                statuses = evaluate_tree_mm(v)
                res._tally(statuses)                       # ★(iii) 自然成立率（funded 有無を問わず）
                a = mm._analyze(v)
                if not (a.get("funded") or set()):
                    continue
                res.total += 1
                try:
                    d = audit_goal_view(mm, v, set_name, seed, statuses=statuses)
                except Exception as e:  # noqa: BLE001
                    res.swaps[f"例外:{type(e).__name__}"] += 1
                    continue
                if d is None:
                    res.agree += 1
                    continue
                res.swaps[f"{d.main_current}→{d.main_eval}"] += 1
                res.eval_picks[d.main_eval] += 1
                if len(res.samples) < max_samples:
                    res.samples.append(d)
    return res


def cost_deception_rows(res: "GoalAuditResult | None" = None) -> list[dict]:
    """全ノードの cost詐称乖離表の行（FableA定義の3列＝測定可能なものに限る）。

    - `has_position`＝(ii) 残条件に位置条件を含むか（cost が過小評価する種別フラグ）。
    - `deception`＝(position+incident)/全cond数（cost が実効を過小評価する度合いの連続版）。
    - `live_rate`/`win_rate`＝(iii) コーパス自然成立率（res を渡した時のみ・mm意図と無関係）。
    - `eval_picks`＝評価器が最安に選んだ回数（res を渡した時のみ）。
    """
    from arena.losstree import LOSS_NODES
    rows = []
    for nid in LOSS_NODES:
        prof = cond_profile(nid)
        total = sum(prof.values()) or 1
        hidden = prof.get("position", 0) + prof.get("incident", 0)
        live_rate, win_rate = (res.natural_rates(nid) if res else (None, None))
        rows.append({
            "node": nid,
            "cost_conds": total,               # (i) 全AND条件数（cost の分母）
            "anyaku": prof.get("anyaku", 0),
            "position": prof.get("position", 0),
            "incident": prof.get("incident", 0),
            "has_position": prof.get("position", 0) > 0,   # (ii) 位置条件フラグ
            "deception": round(hidden / total, 2),
            "live_rate": live_rate,            # (iii) 自然成立率（動いた）
            "win_rate": win_rate,              # (iii) 自然成立率（成立した）
            "eval_picks": (res.eval_picks.get(nid, 0) if res else 0),
        })
    rows.sort(key=lambda r: (-r["eval_picks"], -r["deception"]))
    return rows


def format_goal_audit(res: GoalAuditResult) -> str:
    pct = (res.disagree / res.total * 100) if res.total else 0.0
    out = [f"Stage 3a 監査：{res.total} 局面／一致 {res.agree}／"
           f"食い違い {res.disagree}（{pct:.1f}%）", "", "本命の置き換わり（現行→評価器）："]
    for k, n in res.swaps.most_common(12):
        out.append(f"  - {k}：{n}")
    out += ["", "★cost詐称乖離表（評価器が選んだ順）：",
            "  (i)cost=AND数 (ii)pos=位置条件フラグ (iii)live/win=自然成立率 picks=評価器最安選択",
            "  node                 cost pos decep live  win  picks"]
    for r in cost_deception_rows(res):
        if r["eval_picks"] == 0 and r["deception"] == 0:
            continue
        out.append("  %-20s %4d %3s %5.2f %4.2f %4.2f %5d" % (
            r["node"], r["cost_conds"], "✓" if r["has_position"] else "-",
            r["deception"], r["live_rate"] or 0.0, r["win_rate"] or 0.0,
            r["eval_picks"]))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="DP-2 Stage 3a：本命選定の食い違い＋cost詐称監査")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--loops", type=int, default=3)
    args = ap.parse_args()
    print(format_goal_audit(run_goal_audit(seeds=args.seeds, days=args.days, loops=args.loops)))


if __name__ == "__main__":
    main()
