# -*- coding: utf-8 -*-
"""B-189：教材棋譜（鈴蘭 BTX3日 seed0・ユーザー=脚本家 vs AI主人公・2026-08-08）の
「L2 の暗躍禁止が3日とも神社に吸われ、真の敗北板（都市）を素通しした」機序の実測と、
是正（`B189_KINSHI_FUTILE`）の単局再現ゲート。

★このモジュールは agents/ の判断経路・既定値には触れない（読む＋切替口のON/OFFだけ）。

サブコマンド:
    python -m arena.b189_probe why  [--off]   # Phase 0＝棋譜の view を素で与えて採点表・belief・DP-6 gap を出す
    python -m arena.b189_probe replay [--off] # 単局再現＝L1 は棋譜どおり強制・L2 の主人公は live AI
                                              # （脚本家は全日とも棋譜どおり＝人間手順の強制）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
--off＝`HeuristicProtagonist.B189_KINSHI_FUTILE=False`（旧挙動へ bit 復帰）でのA/B。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.debug import ProbedProtagonist
from agents.defense_plan import (BOARD_DEFEAT_ANYAKU, _kuromaku_supply_here,
                                 _rumor_active, rumor_left_for,
                                 unstoppable_supply_gap)
from agents.heuristic_protagonist import HeuristicProtagonist

REPO = Path(__file__).resolve().parent.parent
LOG_DEFAULT = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_ユーザー脚本家_2026-08-08.jsonl"
KEY = (2, 1)          # 是正の照準＝L2D1（暗躍禁止の宛先 神社→都市）
LOOPS = 4             # 棋譜の view.loops_total（アプリ既定＝4ループ打ち切り）
MM_SEAT = {"mastermind"}
BOARDS = ("病院", "神社", "都市", "学校")


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    meta = lines[0]
    return meta, [d for d in lines if d.get("type") == "decision"]


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


# ---------------------------------------------------------------------------
# why：棋譜の view を素で与えて採点表・belief・DP-6 gap を出す（Phase 0 の物証）
# ---------------------------------------------------------------------------
def cmd_why(args) -> int:
    _meta, decisions = _load(args.log)
    hp = ProbedProtagonist(0, top=300)
    print(f"[why] B189_KINSHI_FUTILE = {hp.B189_KINSHI_FUTILE}")
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        chosen = hp.decide(d["view"], d["decision"], d["options"])
        same = _clean(chosen) == _clean(d["chosen"])
        if d["decision"] != "set_card":
            continue
        mark = "＝棋譜どおり" if same else f"≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        print(f"--- L{d['loop']}D{d['day']} {d['actor']}: 選択={_fmt(_clean(chosen))} {mark}")
        if d["loop"] != 2:
            continue
        rec = hp.records[-1]
        for s, o in rec["scored"][:6]:
            print(f"      {s:8.2f} {_fmt(o)}")
        for s, o in rec["scored"]:
            if o.get("card") == "暗躍禁止" and o.get("target_kind") == "board":
                print(f"      [暗躍禁止] {s:8.2f} →{o.get('target')}")
        est = rec.get("estimates", {})
        b = hp._belief
        agg: dict = {}
        for (ry, _rxs), p in b.rule_marginals().items():
            agg[ry] = agg.get(ry, 0.0) + p
        view = d["view"]
        marg = b.role_marginals()
        rumor = _rumor_active(b)
        rleft = rumor_left_for(b, view)
        gaps = {}
        for a in BOARDS:
            cur = int((view.get("board_anyaku") or {}).get(a, 0) or 0)
            gaps[a] = unstoppable_supply_gap(view, a, cur, BOARD_DEFEAT_ANYAKU,
                                             supply_rumor=rumor, roles=marg,
                                             rumor_left=rleft)
        print(f"      odb={est.get('_observed_defeat_board')}  "
              f"danger={hp._guess_defeat_board(view)}  "
              f"probs={json.dumps({k: round(v, 4) for k, v in (hp._board_defeat_probs or {}).items()}, ensure_ascii=False)}")
        print(f"      P(ルールY)={json.dumps({k: round(v, 4) for k, v in sorted(agg.items(), key=lambda x: -x[1])}, ensure_ascii=False)}  "
              f"rumor_active={rumor} rumor_left={rleft}")
        print(f"      DP-6 gap={json.dumps(gaps, ensure_ascii=False)}  "
              f"b189_futile={sorted(getattr(hp, '_b189_futile_boards_now', ()) or ())}  "
              f"kuromaku_here={{ {', '.join(a for a in BOARDS if _kuromaku_supply_here(view, a, marg))} }}")
    return 0


# ---------------------------------------------------------------------------
# replay：単局再現＝L1 は棋譜どおり強制・L2 以降の主人公は live AI・脚本家は棋譜どおり
# ---------------------------------------------------------------------------
def cmd_replay(args) -> int:
    from arena.gamelog import load_game
    from arena.play_vs_ai import run_to_pending
    from sim.flow import log_safe_chosen

    script, _meta, decisions = load_game(args.log)
    mm_all = [log_safe_chosen(d["chosen"]) for d in decisions
              if d["actor"] == "mastermind"]
    # L1（KEY より前）だけ棋譜どおり強制＝以降は live AI（同一インスタンスが続きを打つ）
    ai_replay: dict[str, list[dict]] = {}
    for d in decisions:
        if d["actor"] == "mastermind" or (d["loop"], d["day"]) >= KEY:
            continue
        ai_replay.setdefault(d["actor"], []).append(log_safe_chosen(d["chosen"]))
    st, pend, log, hp = run_to_pending(script, 0, mm_all, loops=LOOPS,
                                       ai_replay=ai_replay)
    print(f"[replay] B189_KINSHI_FUTILE = {HeuristicProtagonist.B189_KINSHI_FUTILE}")
    for e in log:
        if e.get("actor") == "mastermind" or e.get("decision") != "set_card":
            continue
        tag = "（棋譜強制）" if (e["loop"], e["day"]) < KEY else "（live AI）"
        print(f"  L{e['loop']}D{e['day']} {e['actor']}: {_fmt(_clean(e['chosen']))} {tag}")
    for e in st.history:
        if e.get("event") in ("loop_board", "loop_result"):
            body = (e.get("board_anyaku") if e["event"] == "loop_board"
                    else e.get("result"))
            print(f"  L{e.get('loop')} {e['event']}: {json.dumps(body, ensure_ascii=False)}")
    print(f"  winner={st.winner}  loop_no={st.loop_no}  day={st.day}"
          f"  pending={'なし（終局）' if pend is None else '脚本家入力待ち'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-189：教材棋譜の機序実測と単局再現")
    ap.add_argument("cmd", choices=["why", "replay"])
    ap.add_argument("--log", default=str(LOG_DEFAULT))
    ap.add_argument("--off", action="store_true",
                    help="B189_KINSHI_FUTILE=False（旧挙動へ bit 復帰）で実行")
    a = ap.parse_args(argv)
    if a.off:
        HeuristicProtagonist.B189_KINSHI_FUTILE = False
    return {"why": cmd_why, "replay": cmd_replay}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
