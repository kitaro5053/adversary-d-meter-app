# -*- coding: utf-8 -*-
"""B-191 Phase 0：再戦棋譜（鈴蘭 BTX3d seed0 再戦・build 3f930cd＝B-189 入り）で
`B189_KINSHI_FUTILE` が L2D1/L3D1/L4D1 に発火しなかった原因のプローブ（読み取り専用）。

★agents/ の判断経路・既定値には一切触れない（読むだけ）。実装は本レーンでは行わない。

サブコマンド:
    python -m arena.b191_probe why [--log PATH] [--loops 2,3,4]
        # 棋譜の decision 行の view/options を素で与えて、
        #   - 主人公全決定の bit 一致確認
        #   - 対象キーの採点表（上位＋暗躍禁止の板宛先全部）
        #   - `_b189_futile_boards` の各条件①〜⑤の実評価値（板ごと）
        #   - belief/odb/danger/mm_board_now 等の文脈
    python -m arena.b191_probe diff
        # 初戦棋譜と再戦棋譜の L2D1 を同じ形式で並べる（発火/不発火の差分）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.debug import ProbedProtagonist
from engine.board import AREAS

REPO = Path(__file__).resolve().parent.parent
LOG_REMATCH = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_再戦_2026-08-08.jsonl"
LOG_FIRST = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_ユーザー脚本家_2026-08-08.jsonl"
BOARDS = ("病院", "神社", "都市", "学校")


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return lines[0], [d for d in lines if d.get("type") == "decision"]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def pred_breakdown(hp, view: dict) -> None:
    """`_b189_futile_boards` の条件①〜⑤を板ごとに素で再評価して表示する
    （実装と同じ走査。ここは観測専用＝本体は `hp._b189_futile_boards(view)` で別途呼ぶ）。"""
    loop_now = view.get("loop")
    per_loop: dict = {}
    spent_now: set = set()
    raw_events = []
    for e in view.get("history", []) or []:
        if (e.get("event") == "anyaku"
                and e.get("phase") == "mastermind_ability"
                and e.get("target") in AREAS):
            raw_events.append(e)
            if e.get("loop") == loop_now:
                spent_now.add(e.get("target"))
            else:
                k = (e.get("loop"), e.get("target"))
                per_loop[k] = per_loop.get(k, 0) + 1
    print(f"      [述語] loop_now={loop_now}  能力フェイズ板暗躍イベント={len(raw_events)}件")
    for e in raw_events:
        print(f"        - L{e.get('loop')}D{e.get('day')} →{e.get('target')} delta={e.get('delta')}")
    banr = view.get("board_anyaku") or {}
    genso = hp._alive(view, "幻想")
    sig_bad = {b for (_lp, b), n in per_loop.items() if n >= 2}
    proven = {b for (_lp, b) in per_loop} - sig_bad
    for b in BOARDS:
        c1 = any(bb == b for (_lp, bb) in per_loop)          # ①過去ループ実績
        c2 = b not in sig_bad                                # ②毎ループ1発以下
        c3 = b not in spent_now                              # ③今ループ未使用
        cur = int(banr.get(b, 0) or 0)
        c4 = cur + 1 >= 2                                    # ④現在値+1>=臨界2
        c5 = not (genso is not None and genso.get("area") == b)  # ⑤幻想不在
        verdict = "★見切り" if (b in proven and c3 and c4 and c5) else "—"
        print(f"        {b}: ①実績={c1} ②1発以下={c2} ③今ループ未使用={c3} "
              f"④現在値{cur}+1>=2={c4} ⑤幻想不在={c5} → {verdict}")
    print(f"      [述語] 本体評価 _b189_futile_boards = "
          f"{sorted(hp._b189_futile_boards(view))}")


def probe_log(path, target_keys: set[tuple[int, int]], label: str) -> None:
    meta, decisions = _load(path)
    print(f"===== {label}  (build {meta.get('tool_build')}) =====")
    hp = ProbedProtagonist(0, top=300)
    print(f"[probe] B189_KINSHI_FUTILE = {hp.B189_KINSHI_FUTILE}")
    n_same = n_all = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        chosen = hp.decide(d["view"], d["decision"], d["options"])
        same = _clean(chosen) == _clean(d["chosen"])
        n_all += 1
        n_same += bool(same)
        if d["decision"] != "set_card" or (d["loop"], d["day"]) not in target_keys:
            continue
        mark = "＝棋譜どおり" if same else f"≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        print(f"--- L{d['loop']}D{d['day']} {d['actor']}: 選択={_fmt(_clean(chosen))} {mark}")
        view = d["view"]
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
        mm_board_now = sorted({p["target"] for p in view.get("placements", [])
                               if p.get("owner") == "mastermind"
                               and p.get("target_kind") == "board"})
        print(f"      board_anyaku={json.dumps(view.get('board_anyaku'), ensure_ascii=False)}"
              f"  mm_board_now={mm_board_now}")
        print(f"      odb={est.get('_observed_defeat_board')}  "
              f"danger={hp._guess_defeat_board(view)}  "
              f"probs={json.dumps({k: round(v, 4) for k, v in (hp._board_defeat_probs or {}).items()}, ensure_ascii=False)}  "
              f"b84_top={sorted(getattr(hp, '_b84_top_boards', ()) or ())}")
        print(f"      P(ルールY)={json.dumps({k: round(v, 4) for k, v in sorted(agg.items(), key=lambda x: -x[1])}, ensure_ascii=False)}")
        print(f"      decide時 _b189_futile_boards_now={sorted(getattr(hp, '_b189_futile_boards_now', ()) or ())}  "
              f"_b132_dead={sorted(getattr(hp, '_b132_dead_boards_now', ()) or ())}  "
              f"_b66_unproven={sorted(getattr(hp, '_b66_unproven_boards', ()) or ())}")
        pred_breakdown(hp, view)
    print(f"[probe] 主人公決定の bit 一致 = {n_same}/{n_all}")


def cmd_why(args) -> int:
    keys = {(int(l), 1) for l in str(args.loops).split(",")}
    probe_log(args.log, keys, f"再戦棋譜 {Path(args.log).name}")
    return 0


def cmd_diff(args) -> int:
    probe_log(LOG_FIRST, {(2, 1)}, "初戦棋譜（B-189 教材＝発火が期待どおりだった側）")
    print()
    probe_log(LOG_REMATCH, {(2, 1), (3, 1), (4, 1)}, "再戦棋譜（B-189 不発火の側）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-191 Phase 0：B-189 不発火の原因プローブ")
    ap.add_argument("cmd", choices=["why", "diff"])
    ap.add_argument("--log", default=str(LOG_REMATCH))
    ap.add_argument("--loops", default="2,3,4")
    a = ap.parse_args(argv)
    return {"why": cmd_why, "diff": cmd_diff}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
