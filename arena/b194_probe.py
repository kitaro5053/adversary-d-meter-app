# -*- coding: utf-8 -*-
"""B-194／B-195：D1 の板封じ札の宛先（初見ループの可能性ベース劣後＋前ループ敗北盤の優先）
の Phase 0 検死と対照測定ドライバ。

★このモジュールは agents/ の判断経路・既定値には触れない（読む＋切替口のON/OFFだけ）。

サブコマンド:
    python -m arena.b194_probe d1 --script btx_bomb --seed 0            # Phase 0＝D1 の採点表と述語の卓上値
    python -m arena.b194_probe d1 --script fs5_guard --seed 0 --days 5
    python -m arena.b194_probe single [--b194] [--b195]                 # 単局＝btx_bomb s0・4L（鈴蘭 seed0 同一脚本）
    python -m arena.b194_probe bench --days 3 [--b194] [--b195] [--out r.json]

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行。
--b194/--b195＝該当フラグを True にして実行（既定＝クラス既定値のまま）。
per-game 突合＝bench を条件別に別プロセス実行し、--out の rows を突き合わせる。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace

from agents.debug import ProbedProtagonist
from agents.heuristic_protagonist import HeuristicProtagonist

BOARDS = ("病院", "神社", "都市", "学校")


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


# ---------------------------------------------------------------------------
# 卓上述語（Phase 0 用）：実装前に「何が公開情報から言えるか」を数える
# ---------------------------------------------------------------------------
def desk_b194(view: dict) -> dict:
    """B-194 候補述語の卓上値：札ゼロ供給が**可能**な盤（黒猫 preload×噂ルール未排除）。

    D1（このループの実績ゼロの日）に、現在値+噂1で臨界2に届く盤は
    「封じ札の限界効用が低い可能性がある」＝同点帯での劣後候補。
    """
    banr = view.get("board_anyaku") or {}
    kuro = any(c.get("name") == "黒猫" for c in view.get("characters", []))
    out = {}
    for b in BOARDS:
        cur = int(banr.get(b, 0) or 0)
        preload = 1 if (b == "神社" and kuro) else 0
        out[b] = {"cur": cur, "preload": preload,
                  "fire": bool(preload >= 1 and cur >= 1 and cur + 1 >= 2)}
    return out


def desk_b195(view: dict) -> dict:
    """B-195 候補述語の卓上値：板敗北で終わった過去ループで、
    臨界2到達が**札ゼロ供給では説明できない**盤（＝札由来の実績がある盤）。"""
    hist = view.get("history", []) or []
    defeat_lps = {e.get("loop") for e in hist
                  if e.get("event") == "loop_result"
                  and "敗北" in str(e.get("result", ""))}
    le_lps = {e.get("loop") for e in hist if e.get("event") == "loop_end"}
    board_loss_lps = defeat_lps - le_lps
    kuro = any(c.get("name") == "黒猫" for c in view.get("characters", []))
    detail = {}
    for e in hist:
        if e.get("event") != "loop_board" or e.get("loop") not in board_loss_lps:
            continue
        lp = e.get("loop")
        for b, v in (e.get("board_anyaku") or {}).items():
            if v < 2:
                continue
            noncard = sum(int(x.get("delta", 0) or 0) for x in hist
                          if x.get("event") == "anyaku" and x.get("loop") == lp
                          and x.get("target") == b
                          and x.get("phase") != "action_resolution")
            noncard += 1 if (b == "神社" and kuro) else 0
            detail.setdefault(b, []).append(
                {"loop": lp, "final": v, "noncard": noncard,
                 "card_needed": bool(noncard < 2)})
    fires = sorted(b for b, rows in detail.items()
                   if any(r["card_needed"] for r in rows))
    return {"board_loss_lps": sorted(board_loss_lps), "detail": detail,
            "fires": fires}


# ---------------------------------------------------------------------------
# d1：1局をプローブ付きで回し、各ループ D1 の板封じ採点と述語卓上値を出す
# ---------------------------------------------------------------------------
class _D1Probe(ProbedProtagonist):
    def decide(self, view, decision, options):
        chosen = super().decide(view, decision, options)
        if decision == "set_card":
            rec = self.records[-1]
            rec["view"] = view
            try:
                rec["danger"] = self._guess_defeat_board(view)
            except Exception:
                rec["danger"] = "(err)"
            rec["b189"] = sorted(getattr(self, "_b189_futile_boards_now", ()) or ())
            rec["b132"] = sorted(getattr(self, "_b132_dead_boards_now", ()) or ())
            rec["odb"] = getattr(self, "_observed_defeat_board", None)
            rec["b195impl"] = sorted(getattr(self, "_b195_card_proven", ()) or ())
        return chosen


def _make_script(name: str, days: int, seed: int = 0):
    from sim import random_script
    from sim.sample_scripts import SAMPLE_SCRIPTS
    if name in SAMPLE_SCRIPTS:
        return SAMPLE_SCRIPTS[name]()
    if name.startswith("random_"):
        return random_script(name.split("_", 1)[1], seed, days=days)
    raise SystemExit(f"unknown script: {name}")


def cmd_d1(args) -> int:
    from agents import HeuristicMastermind
    from sim import run_game

    sc = replace(_make_script(args.script, args.days, args.seed), loops=args.loops)
    hp = _D1Probe(args.seed, top=400)
    mm = HeuristicMastermind(args.seed)
    state, _ = run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    print(f"[d1] {args.script} s{args.seed} days={sc.days_per_loop} loops={args.loops}"
          f"  B189={hp.B189_KINSHI_FUTILE}"
          f"  B194={getattr(hp, 'B194_L1_FUTILE_POSSIBLE', '(未実装)')}"
          f"  B195={getattr(hp, 'B195_PRIOR_DEFEAT_BOARD', '(未実装)')}")
    for rec in hp.records:
        if rec["decision"] != "set_card" or rec["day"] != args.day:
            continue
        view = rec["view"]
        kinshi = [(s, o) for s, o in rec["scored"]
                  if o.get("card") == "暗躍禁止" and o.get("target_kind") == "board"]
        mm_board = sorted({p["target"] for p in view.get("placements", [])
                           if p.get("owner") == "mastermind"
                           and p.get("target_kind") == "board"})
        print(f"--- L{rec['loop']}D{rec['day']} seat={rec['seat']}"
              f"  選択={_fmt(rec['chosen'])}"
              f"  mm板札={mm_board}  盤面={view.get('board_anyaku')}")
        print(f"    odb={rec['odb']}  danger={rec['danger']}"
              f"  b189={rec['b189']}  b132={rec['b132']}"
              f"  b195impl={rec.get('b195impl')}")
        for s, o in rec["scored"][:4]:
            print(f"    top {s:8.2f} {_fmt(o)}")
        for s, o in kinshi:
            print(f"    [暗躍禁止] {s:8.2f} →{o.get('target')}")
        d194 = desk_b194(view)
        print(f"    卓上B194: " + "  ".join(
            f"{b}(cur={d['cur']},pre={d['preload']},fire={'○' if d['fire'] else '×'})"
            for b, d in d194.items()))
        d195 = desk_b195(view)
        print(f"    卓上B195: 板敗北lp={d195['board_loss_lps']}  fires={d195['fires']}")
        for b, rows in sorted(d195["detail"].items()):
            print(f"      {b}: " + "  ".join(
                f"L{r['loop']} final={r['final']} noncard={r['noncard']}"
                f" 札由来={'○' if r['card_needed'] else '×'}" for r in rows))
    for e in state.history:
        if e.get("event") in ("loop_board", "loop_result"):
            body = (e.get("board_anyaku") if e["event"] == "loop_board"
                    else e.get("result"))
            print(f"  L{e.get('loop')} {e['event']}: {json.dumps(body, ensure_ascii=False)}")
    print(f"  winner={state.winner}  loop_no={state.loop_no}")
    return 0


# ---------------------------------------------------------------------------
# single：btx_bomb s0（鈴蘭 seed0 と同一脚本）・4L・live mm（B193 既定ON）vs live 主人公
# ---------------------------------------------------------------------------
def cmd_single(args) -> int:
    from agents import HeuristicMastermind, HeuristicProtagonist as HP
    from sim import run_game
    from sim.sample_scripts import SAMPLE_SCRIPTS

    sc = replace(SAMPLE_SCRIPTS["btx_bomb"](), loops=args.loops)
    mm = HeuristicMastermind(0)
    hp = HP(0)
    log: list[dict] = []
    state, _ = run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp}, log=log)
    print(f"[single] btx_bomb s0 loops={args.loops}"
          f"  B194={getattr(hp, 'B194_L1_FUTILE_POSSIBLE', '(未実装)')}"
          f"  B195={getattr(hp, 'B195_PRIOR_DEFEAT_BOARD', '(未実装)')}")
    for e in log:
        if e.get("decision") not in ("set_card", "mastermind_ability"):
            continue
        ch = {k: v for k, v in e["chosen"].items() if k != "prov"}
        print(f'  L{e["loop"]}D{e["day"]} {e["actor"]:10s}: '
              + (f'{ch.get("card")}→{ch.get("target")}' if "card" in ch
                 else f'{ch.get("action")}→{ch.get("target")}'))
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

    print(f"[bench] days={args.days}"
          f"  B194={getattr(HeuristicProtagonist, 'B194_L1_FUTILE_POSSIBLE', '(未実装)')}"
          f"  B195={getattr(HeuristicProtagonist, 'B195_PRIOR_DEFEAT_BOARD', '(未実装)')}")
    rep = run_benchmark(days=args.days, verbose=not args.quiet)
    print(format_report(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-194/B-195：D1 板封じ宛先の検死・対照測定")
    ap.add_argument("cmd", choices=["d1", "single", "bench"])
    ap.add_argument("--script", default="btx_bomb")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--day", type=int, default=1)
    ap.add_argument("--loops", type=int, default=4)
    ap.add_argument("--b194", action="store_true")
    ap.add_argument("--b195", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.b194:
        HeuristicProtagonist.B194_L1_FUTILE_POSSIBLE = True
    if a.b195:
        HeuristicProtagonist.B195_PRIOR_DEFEAT_BOARD = True
    return {"d1": cmd_d1, "single": cmd_single, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
