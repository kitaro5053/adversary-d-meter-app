# -*- coding: utf-8 -*-
"""DP-2 2M-3 **Stage 0b**：現行decoy選定 と 評価器（2M-1/2M-2）の**食い違いを記録するだけ**の監査器。

★安全弁＝Stage 1（初めて mm の手が変わる）に入る前に、「評価器に替えると**どこが**変わるのか」を
先に数える。手が変わってから帰属を探すのは不可能に近い（B-35/B-37 の検死が高くついた教訓）。

## bit-for-bit 不変の担保（設計上の要）
`agents/heuristic.py` を**1行も変更しない**。監査は `HeuristicMastermind._analyze(view)` を
**外から読むだけ**（`_analyze` は `funded`/`decoy_board`/`path_costs` を返す純粋な解析）で、
対局経路には一切入らない＝計装による分岐・乱数消費・順序変化が原理的に起きない。
（`_analyze` 内にフラグを足す実装だと「未使用でも計算が走る」形になり、不変の証明が弱くなる。）

## 使い方
    PYTHONHASHSEED=0 PYTHONPATH=. python arena/decoy_audit.py --days 3 --seeds 8
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Disagreement:
    """1局面ぶんの食い違い記録。"""
    set_name: str
    seed: int
    loop: int
    day: int
    main_current: str                 # 現行の本命（funded の代表＝board/kp/killer4）
    decoy_current: str | None         # 現行の decoy_board（ボード名）
    main_node: str | None             # 評価器での本命ノード
    decoy_node: str | None            # 評価器が選ぶ decoy ノード
    decoy_eval_board: str | None      # それをボードに落としたもの（Stage 1 の比較対象）
    kind: str                         # 食い違いの型
    note: str = ""


@dataclass
class AuditResult:
    total: int = 0                    # decoy 選定が問題になる局面の総数
    agree: int = 0
    kinds: Counter = field(default_factory=Counter)
    nodes: Counter = field(default_factory=Counter)
    samples: list[Disagreement] = field(default_factory=list)

    @property
    def disagree(self) -> int:
        return self.total - self.agree


# --- 現行の3経路 ⇄ losstree ノードの対応（Stage 1 の `_funded_to_node` の下敷き）-------
# ★暫定写像＝Stage 0b は「どれくらい割れるか」を数えるのが目的なので、粗い対応で足りる。
#   Stage 1 で実配線する時に、この表が妥当かをここでの実測結果で検証する。
_PATH_TO_NODES: dict[str, tuple[str, ...]] = {
    "board": ("board.school", "board.shrine", "board.x", "board.kp_anyaku", "jaki.seal"),
    "kp": ("board.kp_anyaku", "kp.killer"),
    "killer4": ("pro.killer4",),
}


def _main_path(funded: set[str], path_costs: dict[str, int]) -> str:
    """現行 funded の代表経路＝残コストが最小のもの（同点は board 優先＝現行の並びに合わせる）。"""
    if not funded:
        return ""
    return min(sorted(funded), key=lambda p: (path_costs.get(p, 99), p != "board"))


def _path_to_node(path: str, view: dict, live: set[str],
                  goal_boards=None) -> str | None:
    """現行の経路名 → 現局面で live な losstree ノード。

    ★Stage 1 の切り分けで `cs_plan.main_node_of` に正典を移した（goal_boards を見る＝
    `goal_boards={病院}` を拾えなかった写像の穴を修正済み）。ここはその薄い委譲。
    """
    from arena.cs_plan import main_node_of
    if goal_boards is not None:
        return main_node_of(path, goal_boards, view, live)
    for nid in _PATH_TO_NODES.get(path, ()):     # 旧経路（goal_boards 不明時のフォールバック）
        if nid in live:
            return nid
    return None


def _node_to_board(node_id: str | None, view: dict) -> str | None:
    """ノード → 偽装先ボード。★正典は `cs_plan.decoy_board_of`（病院を除く）。"""
    from arena.cs_plan import decoy_board_of
    return decoy_board_of(node_id, view)


def audit_view(mm, view: dict, belief, set_name: str, seed: int) -> Disagreement | None:
    """1局面を監査。食い違いが無ければ None。★view も mm も**変更しない**。"""
    from arena.cs_plan import choose_cover_story
    from arena.losstree_eval import evaluate_tree_mm

    a = mm._analyze(view)                       # 読むだけ（現行の解析結果をそのまま使う）
    funded, path_costs = a.get("funded") or set(), a.get("path_costs") or {}
    decoy_cur = a.get("decoy_board")
    if not funded:
        return None                             # 賄える勝ち筋が無い＝decoy の話にならない

    live = {w.node_id for w in evaluate_tree_mm(view) if w.status in ("live", "winning")}
    main_path = _main_path(funded, path_costs)
    main_node = _path_to_node(main_path, view, live, a.get("goal_boards"))

    if main_node is None:
        # ★現行が賄っている勝ち筋を評価器が live と見ていない＝Stage 1 で本命が決まらない型。
        return Disagreement(set_name, seed, view.get("loop", 0), view.get("day", 0),
                            main_path, decoy_cur, None, None, None,
                            "本命が評価器でliveでない",
                            f"funded={sorted(funded)} live={sorted(live)}")

    plan = choose_cover_story(view, belief, main_node)
    decoy_eval_board = _node_to_board(plan.cs, view)

    if decoy_cur == decoy_eval_board and plan.cs is not None:
        return None                             # 一致（ボードとして同じ結論）
    if decoy_cur is None and plan.cs is None:
        return None                             # どちらも立てない＝一致

    if plan.cs is None:
        kind = "評価器はdecoyなし・現行はあり"
    elif decoy_eval_board is None:
        kind = "評価器のdecoyがボードでない"     # ★Stage 1 で board_only が要る型
    elif decoy_cur is None:
        kind = "現行はdecoyなし・評価器はあり"
    else:
        kind = "別のボードを選ぶ"
    return Disagreement(set_name, seed, view.get("loop", 0), view.get("day", 0),
                        main_path, decoy_cur, main_node, plan.cs, decoy_eval_board,
                        kind, plan.reason)


def run_audit(sets=("BTX", "FS"), seeds: int = 8, days: int = 3,
              loops: int = 3, max_samples: int = 12) -> AuditResult:
    """通常生成の脚本を実対局させ、各日開始局面で食い違いを数える（対局は素の設定＝計装なし）。"""
    from dataclasses import replace

    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.belief import Belief
    from sim import random_script, run_game
    from sim.views import mastermind_view

    res = AuditResult()
    for set_name in sets:
        for seed in range(seeds):
            sc = replace(random_script(set_name, seed, days=days), loops=loops)
            snaps: list[dict] = []
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                     on_day_start=lambda s, *_a: snaps.append(mastermind_view(s)))
            bel = Belief(sc.cast, [{"day": i.day, "name": i.name} for i in sc.incidents],
                         sc.set_name)
            for v in snaps:
                try:
                    d = audit_view(mm, v, bel, set_name, seed)
                except Exception as e:  # noqa: BLE001  監査は落とさない（記録だけの道具）
                    res.total += 1
                    res.kinds[f"例外:{type(e).__name__}"] += 1
                    continue
                res.total += 1
                if d is None:
                    res.agree += 1
                    continue
                res.kinds[d.kind] += 1
                if d.decoy_node:
                    res.nodes[d.decoy_node] += 1
                if len(res.samples) < max_samples:
                    res.samples.append(d)
    return res


def format_audit(res: AuditResult) -> str:
    pct = (res.disagree / res.total * 100) if res.total else 0.0
    out = [f"Stage 0b 監査：{res.total} 局面／一致 {res.agree}／"
           f"食い違い {res.disagree}（{pct:.1f}%）", "", "食い違いの型："]
    for k, n in res.kinds.most_common():
        out.append(f"  - {k}：{n}")
    if res.nodes:
        out.append("")
        out.append("評価器が選んだ decoy ノード：")
        for k, n in res.nodes.most_common():
            out.append(f"  - {k}：{n}")
    if res.samples:
        out.append("")
        out.append("実例：")
        for d in res.samples:
            out.append(f"  [{d.set_name} s{d.seed} L{d.loop}D{d.day}] {d.kind}"
                       f"｜現行 main={d.main_current} decoy={d.decoy_current}"
                       f"｜評価器 main={d.main_node} decoy={d.decoy_node}"
                       f"（board={d.decoy_eval_board}）")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="DP-2 2M-3 Stage 0b：decoy選定の食い違い監査")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--loops", type=int, default=3)
    args = ap.parse_args()
    print(format_audit(run_audit(seeds=args.seeds, days=args.days, loops=args.loops)))


if __name__ == "__main__":
    main()
