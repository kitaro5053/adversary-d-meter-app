# -*- coding: utf-8 -*-
"""A-78 計測：**行方不明の供給会計の誤り**を「行為の数え上げ」で測る（計測専用）。

背景＝E-2（2026-07-29）で「事件『行方不明』の移動先に犯人の禁止エリアは選べない」
（公式裁定・KB: `rules/00` 禁止エリアの定義／`rules/40` 事件まわりの注意）を実装したが、
**両AIの供給会計は「任意のボードへ+1」のまま**だった＝犯人が禁止エリアを持つ脚本では
**行けない板への供給を数えていた**。

★主指標をベンチの防衛数にしない理由＝`docs/監査_B105_同点帯とベンチの籤ノイズ_2026-07-30.md`：
  AIの採点を1点も変えずに候補の**並び順**を変えるだけで防衛数は 3日級 σ3.5／5日級 σ2.7 動く。
  ∴ ±1〜2局は改善の証拠にも害の証拠にもならない。**並び順に依存しない量**＝
  「誤って数えていた件数」を数える（B-103／B-86' と同じ型）。

計測は本番経路に触れない：`agents.heuristic.A78_PROBE`（既定 None）へコールバックを差し込み、
`_analyze` 1回につき1度だけ「旧会計値 `_inc_board`」と「新会計値 `_inc_board_for(goal_boards)`」を
受け取って差を数える。差>0 の席＝**旧実装が行けない板への供給を数えていた席**。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.a78_audit mm --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.a78_audit static --days 5
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.a78_audit prot --days 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace

import agents.heuristic as _mm_mod
import agents.heuristic_protagonist as _hp_mod
from engine.board import AREAS
from engine.data import forbidden_of


# ---------------------------------------------------------------------------
# ① 脚本家AI側＝供給会計の誤差（席の数え上げ）
# ---------------------------------------------------------------------------
def run_mm(days: int, loops: int = 8, mm_fix: bool = True,
           prot_fix: bool = True) -> dict:
    """`mm_fix=False` / `prot_fix=False` で A-78 の修正を切って旧挙動の対局を回す
    （＝**before の軌跡**で「誤って数えていた席」を数える）。プローブが受け取る新値は
    トグルと無関係に規則どおりの値なので、どちらの設定でも誤差そのものは測れる。"""
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    sink = {"seats": 0, "seats_with_missing": 0, "over_seats": 0, "over_sum": 0.0,
            "games": 0, "games_over": 0, "by_game": Counter(), "detail": Counter()}
    cur = {"key": None}

    def probe(view, goal_boards, old, new):
        sink["seats"] += 1
        # ★ゴール板が無い脚本（僕と契約しようよ！等）は旧実装でもボード供給を数えない
        #   （`_supply_of` の `_has_board` が偽／`path_costs["board"]` が立たない）＝
        #   会計差ではないので数えない。
        if old <= 0 or not goal_boards:
            return
        sink["seats_with_missing"] += 1
        if new >= old:
            return
        sink["over_seats"] += 1
        sink["over_sum"] += float(old) - float(new)
        sink["by_game"][cur["key"]] += 1
        _culps = tuple(sorted({i["culprit"] for i in view["incidents"]
                               if i["name"] == "行方不明"
                               and any(b in (forbidden_of(i["culprit"]) or ())
                                       for b in goal_boards)}))
        sink["detail"][(_culps, tuple(sorted(goal_boards)))] += 1

    _mm_mod.A78_PROBE = probe
    _hp_old = _hp_mod.HeuristicProtagonist.A78_FORBIDDEN_AWARE
    _hp_mod.HeuristicProtagonist.A78_FORBIDDEN_AWARE = bool(prot_fix)
    _mp = None if mm_fix else {"supply_missing_forbidden": 0.0}
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            cur["key"] = f"{name}#{seed}"
            sink["games"] += 1
            probe_sc = replace(sc, loops=loops)
            mm = HeuristicMastermind(seed, params=_mp)
            hp = HeuristicProtagonist(seed)
            run_game(probe_sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        _mm_mod.A78_PROBE = None
        _hp_mod.HeuristicProtagonist.A78_FORBIDDEN_AWARE = _hp_old
    sink["games_over"] = len(sink["by_game"])
    return sink


def format_mm(sink: dict, days: int) -> str:
    out = [f"== 脚本家AI 供給会計（{days}日級・{sink['games']}局） ==",
           f"  `_analyze` 呼び出し（席）           : {sink['seats']}",
           f"  うち行方不明を供給に数えた席        : {sink['seats_with_missing']}",
           f"  ★**旧会計と規則どおりの会計が食い違う席**: {sink['over_seats']}"
           f"（{sink['games_over']}局）",
           f"  ★食い違いの合計（供給カウンタ数）  : {sink['over_sum']:.0f}",
           "  ※ `--no-mm-fix` で回した時＝**旧実装が実際に誤った席の数**。",
           "  ※ 修正版で回した時＝**修正が実際に効いた席の数**（旧実装ならここで誤っていた）。",
           "     修正版の「誤り」は定義上0＝会計値そのものが規則どおりの式（テストで固定）。"]
    if sink["by_game"]:
        out.append("  局別（多い順・上位15）:")
        for k, v in sink["by_game"].most_common(15):
            out.append(f"    {k:24s} {v:5d} 席")
    if sink["detail"]:
        out.append("  内訳（犯人 × ゴール板）:")
        for (cs, gb), v in sink["detail"].most_common():
            out.append(f"    犯人{list(cs)} ゴール板{list(gb)}: {v} 席")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ② 静的スキャン＝コーパスにどれだけ「材料」があるか（対局を回さない＝完全に決定的）
# ---------------------------------------------------------------------------
def _goal_boards_static(sc) -> set:
    from engine.data import initial_area_of
    g = set()
    ry = sc.rule_y
    if ry == "守るべき場所":
        g.add("学校")
    elif ry == "封印されしモノ":
        g.add("神社")
    elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在"):
        role = "クロマク" if ry == "復讐者の灯火" else "ウィッチ"
        who = next((n for n in sc.cast if sc.role_of(n) == role), None)
        if who:
            # 手先/従者はループ毎に脚本家が初期エリアを指定＝カードからは決まらない
            # （`rules/30:23`）ので静的スキャンでは None＝この局は数えない。
            g.add(initial_area_of(who))
    g.discard(None)
    return g


def run_static(days: int) -> dict:
    from arena.benchmark import benchmark_scripts
    res = {"games": 0, "games_missing": 0, "incidents": 0, "with_forbidden": 0,
           "goal_blocked": 0, "games_goal_blocked": 0, "detail": Counter()}
    for name, seed, sc in benchmark_scripts(days=days):
        res["games"] += 1
        ms = [i for i in sc.incidents if i.name == "行方不明"]
        if ms:
            res["games_missing"] += 1
        gb = _goal_boards_static(sc)
        hit = False
        for i in ms:
            res["incidents"] += 1
            fb = forbidden_of(i.culprit) or frozenset()
            if fb:
                res["with_forbidden"] += 1
            if gb and all(b in fb for b in gb):
                res["goal_blocked"] += 1
                hit = True
                res["detail"][(name, i.culprit, tuple(sorted(fb)), tuple(sorted(gb)))] += 1
        if hit:
            res["games_goal_blocked"] += 1
    return res


def format_static(res: dict, days: int) -> str:
    out = [f"== 静的スキャン（{days}日級・{res['games']}局） ==",
           f"  行方不明を持つ局                    : {res['games_missing']}",
           f"  行方不明の事件数                    : {res['incidents']}",
           f"  うち犯人に禁止エリアあり            : {res['with_forbidden']}",
           f"  ★うちゴール板が**全て**犯人の禁止   : {res['goal_blocked']}"
           f"（{res['games_goal_blocked']}局）"]
    for (nm, cu, fb, gb), v in res["detail"].most_common():
        out.append(f"    {nm:12s} 犯人={cu} 禁止={list(fb)} ゴール板={list(gb)} x{v}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ③ 主人公AI側＝「犯人候補の誰も届かない板」がどれだけ観測できるか
# ---------------------------------------------------------------------------
def run_prot(days: int, loops: int = 8) -> dict:
    """主人公は犯人を知らない＝**犯人候補の集合**でしか絞れない。
    候補の**誰も**ゴール板へ行けない日がどれだけあるかを数える（＝主人公側の修正の効き代）。"""
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    sink = {"seats": 0, "missing_days": 0, "narrowed": 0, "defeat_board_excluded": 0,
            "p_board_changed": 0, "gate_flipped": 0, "danger_changed": 0,
            "games": Counter(), "danger_games": Counter()}
    cur = {"key": None}
    _orig = _hp_mod.HeuristicProtagonist._recompute
    _BOARD_RULES = ("守るべき場所", "封印されしモノ", "復讐者の灯火", "巨大時限爆弾Xの存在")
    FIXED = _hp_mod._A78_FIXED_DEFEAT_BOARD

    def _patched(self, view):
        _orig(self, view)
        sink["seats"] += 1
        cands = getattr(self, "_culprit_cands", {}) or {}
        try:
            rules_m = self._belief.rule_marginals()
        except Exception:  # noqa: BLE001
            rules_m = {}
        for inc in view.get("incidents", ()):
            if inc.get("name") != "行方不明":
                continue
            d = inc.get("day")
            sink["missing_days"] += 1
            cs = cands.get(d) or ()
            if not cs:
                continue
            feed = set()
            for cn in cs:
                feed |= _hp_mod_missing_boards(view, cn)
            if feed != set(AREAS):
                sink["narrowed"] += 1
                sink["games"][cur["key"]] += 1
            if any(b not in feed for b in FIXED.values()):
                sink["defeat_board_excluded"] += 1
            # ★実際に危険度の入力（_p_board0）が変わったか＝主人公側の修正が効いた席
            p_old = sum(pv for (ry0, _x0), pv in rules_m.items() if ry0 in _BOARD_RULES)
            p_new = sum(pv for (ry0, _x0), pv in rules_m.items()
                        if (ry0 in ("復讐者の灯火", "巨大時限爆弾Xの存在")
                            or (ry0 in FIXED and FIXED[ry0] in feed)))
            if abs(p_old - p_new) > 1e-9:
                sink["p_board_changed"] += 1
                if (p_old > 0.3) != (p_new > 0.3):
                    sink["gate_flipped"] += 1
        # ★最終的な危険度そのものが変わったか（他の枝＝B-15 再履修ブースト等が
        #   同じ日を拾い直す可能性があるので、`_p_board0` の変化だけでは足りない）。
        #   `_recompute` は view と belief から決まる純粋計算＝旧挙動で撃ち直して比べる。
        _dg_new = dict(getattr(self, "_incident_danger", {}) or {})
        self.A78_FORBIDDEN_AWARE = False
        try:
            _orig(self, view)
            _dg_old = dict(getattr(self, "_incident_danger", {}) or {})
        finally:
            self.A78_FORBIDDEN_AWARE = True
            _orig(self, view)          # 正しい状態へ戻す
        if _dg_new != _dg_old:
            sink["danger_changed"] += 1
            sink["danger_games"][cur["key"]] += 1
        return None

    _hp_mod.HeuristicProtagonist._recompute = _patched
    try:
        for name, seed, sc in benchmark_scripts(days=days):
            cur["key"] = f"{name}#{seed}"
            probe_sc = replace(sc, loops=loops)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(probe_sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        _hp_mod.HeuristicProtagonist._recompute = _orig
    return sink


def _hp_mod_missing_boards(view, name):
    from sim.state import missing_incident_boards_from_view
    return set(missing_incident_boards_from_view(view, name))


def format_prot(sink: dict, days: int) -> str:
    out = [f"== 主人公AI 犯人候補からの絞り込み余地（{days}日級） ==",
           f"  `_recompute` 呼び出し               : {sink['seats']}",
           f"  行方不明の日×呼び出し               : {sink['missing_days']}",
           f"  候補の誰かが行けない板がある回       : {sink['narrowed']}",
           f"  ★固定敗北板（学校/神社）が候補和の外: {sink['defeat_board_excluded']}",
           f"  ★危険度の入力 `_p_board0` が変わった回: {sink['p_board_changed']}",
           f"  ★危険度ゲート（>0.3）の結果が変わった回: {sink['gate_flipped']}",
           f"  ★★**最終的な `_incident_danger` が変わった席**: {sink['danger_changed']}"
           f"（{len(sink['danger_games'])}局）"]
    if sink["danger_games"]:
        out.append("  危険度が変わった局（上位15）:")
        for k, v in sink["danger_games"].most_common(15):
            out.append(f"    {k:24s} {v:5d}")
    if sink["games"]:
        out.append("  局別（上位15）:")
        for k, v in sink["games"].most_common(15):
            out.append(f"    {k:24s} {v:5d}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="A-78 行方不明の供給会計 監査")
    ap.add_argument("cmd", choices=["mm", "static", "prot"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--no-mm-fix", action="store_true",
                    help="mm: 脚本家側のA-78修正を切って（旧挙動で）対局を回す")
    ap.add_argument("--no-prot-fix", action="store_true",
                    help="mm/prot: 主人公側のA-78修正を切って対局を回す")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    if args.cmd == "mm":
        res = run_mm(days=args.days, loops=args.loops,
                     mm_fix=not args.no_mm_fix, prot_fix=not args.no_prot_fix)
        print(format_mm(res, args.days))
    elif args.cmd == "static":
        res = run_static(days=args.days)
        print(format_static(res, args.days))
    else:
        res = run_prot(days=args.days, loops=args.loops)
        print(format_prot(res, args.days))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({k: (dict(v) if isinstance(v, Counter) else v)
                       for k, v in res.items()}, f, ensure_ascii=False,
                      default=str, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
