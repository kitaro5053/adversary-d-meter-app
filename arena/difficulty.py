"""脚本の難易度評価（ユーザー要望 2026-07-05）— どちらが優位かを勝率以外の軸で数値化する。

3つの指標（いずれも決定的シード・複数ゲームの平均／分布で出す）:
1. **ルール確定ループ数**: ループ数を長め（probe_loops）に取って対戦させ、主人公の belief が
   ルールY×Xの組を確定（最有力確率≥99.9%）した最初のループ。
2. **敗北条件全把握ループ数**: ルール確定に加えて、敗北に直結する配役
   （キーパーソン・フレンド・キラー・メインラバーズ・タイムトラベラー・ウィッチ）の
   キャラ確定までかかったループ。
3. **完全情報クリアループ数**: 主人公に全情報（全配役・ルール・フレンド公開＝初期友好）を
   与えてからゲーム開始し、何ループ目でクリアするか。
   ★人間同等の知性なら**ほぼ1**になるはず。**3以上は特殊シナリオか設計ミスの疑い**（ユーザー基準）。
   ※本評価のAI主人公はヒューリスティック＝人間より弱いので、絶対値は上振れする。
     脚本間の**相対比較**と「完全情報でも勝てない＝構造的に脚本家有利」の検出に使う。

CLI:  python -m arena.difficulty --script basic
      python -m arena.difficulty --script random_FS --seed 7
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.belief import Belief
from agents.legacy import HeuristicMastermindV1
from sim import random_script, run_game
from sim.flow import _make_decider, run_loop
from sim.sample_scripts import SAMPLE_SCRIPTS
from sim.state import GameState, validate_script

# 敗北に直結する役職（キャラ確定が「負け条件の把握」に必須のもの）
LOSS_ROLES = frozenset({"キーパーソン", "フレンド", "キラー", "メインラバーズ",
                        "タイムトラベラー", "ウィッチ"})


def _agents(seed: int, probe: bool = False) -> dict:
    """probe=True は情報速度測定用＝mm_v1（ループは勝つが情報衛生・友好禁止キャンプの無い
    『標準的な脚本家』）。通常評価と完全情報評価は現行最強の mm_v2。"""
    hp = HeuristicProtagonist(seed)
    mm = HeuristicMastermindV1(seed) if probe else HeuristicMastermind(seed)
    return {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}


def _pin_loops(script, history) -> tuple[int | None, int | None]:
    """(ルール確定ループ, 敗北条件全把握ループ)。確定しなかったら None。"""
    required = {n: script.role_of(n) for n in script.cast
                if script.role_of(n) in LOSS_ROLES}
    b = Belief(script.cast, [{"day": i.day, "name": i.name} for i in script.incidents],
               set_name=script.set_name)
    loops = sorted({e.get("loop", 0) for e in history if e.get("loop")})
    rule_loop = loss_loop = None
    for lp in loops:
        b.observe([e for e in history if e.get("loop", 0) <= lp])
        rule_pinned = b.top_rule_prob() >= 0.999
        if rule_pinned and rule_loop is None:
            rule_loop = lp
        if rule_pinned and loss_loop is None:
            marg = b.role_marginals()
            if all(marg.get(n, {}).get(r, 0) >= 0.999 for n, r in required.items()):
                loss_loop = lp
        if rule_loop and loss_loop:
            break
    return rule_loop, loss_loop


def run_perfect_info_game(script, seed: int) -> GameState:
    """主人公に全情報＋フレンド公開（初期友好）を与えてゲームを回す。"""
    validate_script(script)
    state = GameState(script=script)
    # 全配役・ルールXを最初から公開履歴に注入（beliefが即座に確定＝完全情報）
    for n in script.cast:
        state.history.append({"loop": 0, "day": 0, "event": "role_reveal",
                              "name": n, "role": script.role_of(n)})
        state.revealed_roles[n] = script.role_of(n)
    state.history.append({"loop": 0, "day": 0, "event": "rule_reveal",
                          "rule_x": script.rule_x})
    # 犯人も全公開（「全ての情報」＝配役＋ルール＋犯人。事件対策の判断材料）
    for inc in script.incidents:
        state.history.append({"loop": 0, "event": "culprit_reveal",
                              "day": inc.day, "name": inc.culprit})
    # フレンドは公開済み扱い＝ループ開始時に友好+1（初期友好）
    state.friend_revealed = {n for n in script.cast if script.role_of(n) == "フレンド"}
    log: list[dict] = []
    decide = _make_decider(state, _agents(seed), log)
    run_loop(state, decide)
    return state


def evaluate_script(script, n_games: int = 10, probe_loops: int = 8,
                    base_seed: int = 0) -> dict:
    """3指標＋通常勝率をまとめて評価する。"""
    # --- 指標1・2：長尺プローブ（ループ数を伸ばして情報速度を測る。相手はmm_v1＝
    #     ループは勝つが情報抑制をしない「標準的な脚本家」） ---
    probe = replace(script, loops=probe_loops)
    rule_loops: list[int | None] = []
    loss_loops: list[int | None] = []
    won_before_pin = 0
    for s in range(n_games):
        state, _ = run_game(probe, _agents(base_seed + s, probe=True))
        rl, ll = _pin_loops(probe, state.history)
        # ★確定前に主人公が勝った場合は「推理失敗」ではない（勝てば推理は不要）＝別枠で数える
        if rl is None and state.winner == "protagonist":
            won_before_pin += 1
        rule_loops.append(rl)
        loss_loops.append(ll)

    # --- 指標3：完全情報クリア（相手は現行最強のmm_v2。ループ生存クリアのみ数える。
    #     BTXの最後の戦いは完全情報なら自明に勝てるため別枠で報告） ---
    clear_loops: list[int | None] = []
    fb_wins = 0
    for s in range(n_games):
        state = run_perfect_info_game(replace(script, loops=probe_loops), base_seed + s)
        via_fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not via_fb:
            clear_loops.append(state.loop_no)
        else:
            clear_loops.append(None)
            fb_wins += (state.winner == "protagonist" and via_fb)

    # --- 参考：通常設定での勝率 ---
    mm_wins = 0
    for s in range(n_games):
        state, _ = run_game(script, _agents(base_seed + s))
        mm_wins += (state.winner == "mastermind")

    def _avg(vals):
        got = [v for v in vals if v is not None]
        return round(sum(got) / len(got), 2) if got else None

    def _rate(vals):
        return sum(1 for v in vals if v is not None) / len(vals)

    from sim.loop_race import analyze_script as _race_script
    from sim.loop_race import describe_report as _race_desc
    from sim.script_quality import (balance_grade, incident_feasibility_lines,
                                     incident_feasibility_verdict)
    bg = balance_grade(script)
    _race = _race_script(script)

    return {
        "script": {"set": script.set_name, "rule_y": script.rule_y,
                   "rule_x": "/".join(script.rule_xs), "cast": script.cast,
                   "loops": script.loops, "days": script.days_per_loop},
        "win_paths": bg.n_paths, "win_path_groups": sorted(bg.groups),
        "balance_verdict": bg.verdict,
        "incident_feasibility": incident_feasibility_lines(script),
        "incident_verdict": incident_feasibility_verdict(script),
        "race_verdict": _race.verdict,
        "race_lines": _race_desc(_race),
        "games": n_games, "probe_loops": probe_loops,
        "rule_pin_loop_avg": _avg(rule_loops), "rule_pin_rate": _rate(rule_loops),
        "won_before_pin_rate": won_before_pin / n_games,  # 確定前に勝ち抜け＝推理不要だった
        "loss_known_loop_avg": _avg(loss_loops), "loss_known_rate": _rate(loss_loops),
        "perfect_clear_loop_avg": _avg(clear_loops),
        "perfect_clear_rate": _rate(clear_loops),
        "perfect_clear_min": min((v for v in clear_loops if v is not None), default=None),
        "perfect_fb_win_rate": fb_wins / n_games,  # 最後の戦い経由の勝ち（BTX・完全情報なら自明）
        "mm_winrate_normal": mm_wins / n_games,
    }


def verdict(report: dict) -> str:
    """評価の読み解き（ユーザー基準：完全情報クリア1がほぼ正・3以上は設計ミスの疑い）。"""
    lines = []
    # ★勝ち筋の本数（脚本家の独立した勝ちルート＝主人公が別々の対策を要する数）。
    #   1本＝キラー等ひとつを止めれば脚本家が勝てない薄い脚本（ユーザー指摘 2026-07-06）。
    if "balance_verdict" in report:
        lines.append(report["balance_verdict"])
    # ★事件の発生しやすさ（不安会計）の弱点があれば添える（ユーザー要望 2026-07-06）。
    if report.get("incident_verdict"):
        lines.append(report["incident_verdict"])
    pc = report["perfect_clear_loop_avg"]
    pr = report["perfect_clear_rate"]
    if pr == 0:
        lines.append("⚠ 完全情報でも一度もクリアできない＝情報以外の構造で脚本家必勝（設計ミスの疑い・強）")
    elif pr < 0.5:
        lines.append(f"⚠ 完全情報でもクリア率{pr:.0%}＝情報を全て与えても防げない負け筋が主"
                     "（テンポ・打点の構造で脚本家有利）")
    elif pc is not None and pc >= 3:
        lines.append(f"⚠ 完全情報クリアが平均{pc}ループ＝特殊シナリオか設計ミスの疑い（基準:1がほぼ正）")
    elif pc is not None and pc <= 1.5:
        lines.append(f"✓ 完全情報なら平均{pc}ループ・クリア率{pr:.0%}＝負け筋は把握すれば防げる健全な構造")
    solved = report["rule_pin_rate"] + report["won_before_pin_rate"]
    if report["rule_pin_loop_avg"] is not None:
        lines.append(f"・ルール確定まで平均{report['rule_pin_loop_avg']}ループ"
                     f"（確定{report['rule_pin_rate']:.0%}＋確定前に勝ち抜け"
                     f"{report['won_before_pin_rate']:.0%}）")
    elif solved < 0.5:
        lines.append(f"・ルール確定率{report['rule_pin_rate']:.0%}＝情報の出にくい脚本（推理難）")
    if report["loss_known_rate"] > 0 and report["loss_known_loop_avg"] is not None:
        lines.append(f"・敗北条件の全把握まで平均{report['loss_known_loop_avg']}ループ"
                     f"（到達率{report['loss_known_rate']:.0%}）")
    elif report["loss_known_rate"] == 0:
        lines.append("・敗北条件の全把握には一度も到達せず（キーパーソン等のキャラ特定が困難）")
    lines.append(f"・通常設定の脚本家勝率 {report['mm_winrate_normal']:.0%}"
                 f"（ループ{report['script']['loops']}・{report['script']['days']}日）")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="脚本の難易度評価（3指標）")
    ap.add_argument("--script", default="basic",
                    help=f"{list(SAMPLE_SCRIPTS)} または random_FS / random_BTX")
    ap.add_argument("--seed", type=int, default=0, help="ランダム脚本の種")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--probe-loops", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.script.startswith("random_"):
        script = random_script(args.script.split("_", 1)[1], seed=args.seed)
    else:
        script = SAMPLE_SCRIPTS[args.script]()
    t0 = time.perf_counter()
    report = evaluate_script(script, n_games=args.games, probe_loops=args.probe_loops)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        sc = report["script"]
        print(f"脚本: {sc['rule_y']} × {sc['rule_x']}（{sc['set']}・"
              f"ループ{sc['loops']}×{sc['days']}日）")
        print(f"キャスト: {'、'.join(sc['cast'])}")
        print("-" * 60)
        print(f"ルール確定ループ数     : 平均 {report['rule_pin_loop_avg']}"
              f"（確定率 {report['rule_pin_rate']:.0%}＋確定前勝ち抜け "
              f"{report['won_before_pin_rate']:.0%}・{report['probe_loops']}Lプローブ）")
        print(f"敗北条件 全把握ループ数: 平均 {report['loss_known_loop_avg']}"
              f"（到達率 {report['loss_known_rate']:.0%}）")
        print(f"完全情報クリアループ数 : 平均 {report['perfect_clear_loop_avg']}"
              f"（クリア率 {report['perfect_clear_rate']:.0%}・"
              f"最短 {report['perfect_clear_min']}）")
        print("-" * 60)
        if report.get("race_lines"):
            print("詰み判定（レース/被覆解析・初期局面）:")
            for _l in report["race_lines"]:
                print("  " + _l)
            print("-" * 60)
        if report.get("incident_feasibility"):
            print("事件の発生しやすさ（不安会計）:")
            for _l in report["incident_feasibility"]:
                print("  " + _l)
            print("-" * 60)
        print(verdict(report))
        print(f"({time.perf_counter() - t0:.0f}秒)")


if __name__ == "__main__":
    main()
