# -*- coding: utf-8 -*-
"""B-193：脚本家AIの「D1 本命盤 暗躍+2 プッシュ×囮チャネル維持」
（`HeuristicMastermind.B193_MM_BOARD_RUSH`・既定OFF）の対照測定ドライバ。

★このモジュールは agents/ の判断経路・既定値には触れない（読む＋切替口のON/OFFだけ）。

サブコマンド:
    python -m arena.b193_probe why  [--on]        # Phase 0＝教材棋譜のL1D1/L1D3のmm採点表
    python -m arena.b193_probe single [--on]      # 単局確認＝btx_bomb s0・4L・live mm vs live 主人公
    python -m arena.b193_probe bench --days 3 [--on] [--out r.json]   # 両ベンチの対照測定

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行。
--on＝`HeuristicMastermind.B193_MM_BOARD_RUSH=True`（既定は OFF のまま実行）。
per-game 突合＝bench を OFF/ON で別プロセス実行し、--out の rows を突き合わせる。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from agents.heuristic import HeuristicMastermind

REPO = Path(__file__).resolve().parent.parent
LOG_DEFAULT = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_ユーザー脚本家_2026-08-08.jsonl"


def _mm_rows(path):
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return [d for d in lines if d.get("type") == "decision"
            and d.get("actor") == "mastermind"]


def _fmt(o: dict) -> str:
    if "card" in o:
        return f'{o.get("card")}→{o.get("target")}'
    return f'{o.get("action")}' + (f'→{o.get("target")}' if o.get("target") else "")


# ---------------------------------------------------------------------------
# why：教材棋譜の mm view を素で与えて採点表を出す（Phase 0 の物証）
# ---------------------------------------------------------------------------
def cmd_why(args) -> int:
    rows = _mm_rows(args.log)
    print(f"[why] B193_MM_BOARD_RUSH = {HeuristicMastermind.B193_MM_BOARD_RUSH}")
    for loop, day, decision in ((1, 1, "set_card"), (1, 3, "mastermind_ability")):
        d = next(r for r in rows if r["loop"] == loop and r["day"] == day
                 and r["decision"] == decision)
        view = d["view"]
        mm = HeuristicMastermind(0)
        a = mm._analyze(view)
        if decision == "set_card":
            score = lambda o: mm._score_set(o, a, view) - mm._plus2_penalty(o, a, view)  # noqa: E731
        else:
            score = lambda o: mm._score_ability(o, a)  # noqa: E731
        scored = sorted(((score(o), o) for o in d["options"]), key=lambda x: -x[0])
        print(f"--- L{loop}D{day} {decision}  b193_rush={a.get('b193_rush')}  "
              f"funded={sorted(a['funded'])}  goal={sorted(a['goal_boards'])}  "
              f"decoy={a.get('decoy_board')}({a.get('decoy_funded')})  "
              f"ユーザーの実手={_fmt({k: v for k, v in d['chosen'].items() if k != 'prov'})}")
        for s, o in scored[:10]:
            print(f"   {s:8.2f} {_fmt(o)}")
    return 0


# ---------------------------------------------------------------------------
# single：btx_bomb s0（鈴蘭 seed0 と同一脚本）・4L・live mm vs live 主人公
# ---------------------------------------------------------------------------
def cmd_single(args) -> int:
    from sim import run_game
    from agents import HeuristicProtagonist
    from sim.sample_scripts import SAMPLE_SCRIPTS

    sc = replace(SAMPLE_SCRIPTS["btx_bomb"](), loops=args.loops)
    mm = HeuristicMastermind(0)
    hp = HeuristicProtagonist(0)
    log: list[dict] = []
    state, _ = run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}, log=log)
    print(f"[single] B193_MM_BOARD_RUSH = {HeuristicMastermind.B193_MM_BOARD_RUSH}"
          f"  btx_bomb s0 loops={args.loops}")
    for e in log:
        if e.get("decision") not in ("set_card", "mastermind_ability"):
            continue
        ch = {k: v for k, v in e["chosen"].items() if k != "prov"}
        print(f'  L{e["loop"]}D{e["day"]} {e["actor"]:10s}: {_fmt(ch)}'
              + ("" if "card" in ch else f'  ({ch.get("action")})'))
    for e in state.history:
        if e.get("event") in ("loop_board", "loop_result"):
            body = (e.get("board_anyaku") if e["event"] == "loop_board"
                    else e.get("result"))
            print(f'  L{e.get("loop")} {e["event"]}: {json.dumps(body, ensure_ascii=False)}')
    print(f"  winner={state.winner}  loop_no={state.loop_no}")
    return 0


# ---------------------------------------------------------------------------
# bench：ベンチ本体をこのプロセスの切替口で回す（per-game 突合は --out の rows で）
# ---------------------------------------------------------------------------
def cmd_bench(args) -> int:
    from arena.benchmark import format_report, run_benchmark

    print(f"[bench] B193_MM_BOARD_RUSH = {HeuristicMastermind.B193_MM_BOARD_RUSH}"
          f"  days={args.days}")
    rep = run_benchmark(days=args.days, verbose=not args.quiet)
    print(format_report(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-193：mm軍拡の対照測定ドライバ")
    ap.add_argument("cmd", choices=["why", "single", "bench"])
    ap.add_argument("--log", default=str(LOG_DEFAULT))
    ap.add_argument("--on", action="store_true",
                    help="B193_MM_BOARD_RUSH=True で実行（既定＝OFF のまま）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=4)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.on:
        HeuristicMastermind.B193_MM_BOARD_RUSH = True
    return {"why": cmd_why, "single": cmd_single, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
