# -*- coding: utf-8 -*-
"""B-120：暗躍+2（1/loopの切り札）の使用タイミングのシャドー集計（挙動不変・計測のみ）。

問い（トリアージ 2026-07-31b・手練れ指摘）＝「脚本家は暗躍+2を早く切りすぎ。
抑止力（まだ持っているかもという圧力）が消えて主人公が動きやすくなる」。

集計内容（すべて対局後の state.history からの後処理＝AIの挙動に一切影響しない）：
  1. ループごとの暗躍+2 の使用日（未使用は None）と使用先。
  2. 当日負け（同日・同一先への主人公の暗躍禁止＝打ち消し）の率を使用日別に。
  3. +2 使用前／使用後での主人公の暗躍禁止の的中/空振り
     （的中＝同日に mm が同一先へ暗躍札を置いていた。空振り＝置いていない）。
     ★抑止力が AI 世界に実在するなら「使用後は空振りが減る（守りを緩める）」はず。
  4. ループの勝敗（loop_result）との突き合わせ＝「早切り×打ち消し×そのループ敗北(mm視点)」
     の共起。

CLI（前面実行・チャンク推奨）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.plus2_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.plus2_audit --days 5
    ... --start 0 --end 65 --out p3a.json   # チャンク分割
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game
from arena.benchmark import benchmark_scripts

_ANYAKU_CARDS = ("暗躍+1", "暗躍+2")


def audit_history(history: list[dict]) -> list[dict]:
    """対局履歴から、ループごとの暗躍+2 会計を抽出する（純関数）。

    返り値＝ループごとの dict:
      loop, plus2_day (None=未使用), plus2_target, plus2_kind,
      plus2_negated (同日・同一先に主人公の暗躍禁止),
      kinshi_pre / kinshi_post  = +2使用前/後の主人公の暗躍禁止の枚数
      kinshi_pre_hit / kinshi_post_hit = うち的中（同日に mm が同一先へ暗躍札）
      loop_result ("mm_win"=主人公の敗北イベントあり / None)
    ★loop_result イベントは sim/effects.py が**主人公の敗北時のみ**発行する＝
      防衛して終わった最終ループには出ない。防衛ループの標識は run_audit 側で
      outcome=="defense" の最終ループに "protagonist_win" を付与する。
    """
    loops: dict[int, dict] = {}

    def _rec(lp: int) -> dict:
        return loops.setdefault(lp, {
            "loop": lp, "plus2_day": None, "plus2_target": None,
            "plus2_kind": None, "plus2_negated": False,
            "kinshi_pre": 0, "kinshi_post": 0,
            "kinshi_pre_hit": 0, "kinshi_post_hit": 0,
            "loop_result": None,
        })

    for e in history:
        ev = e.get("event")
        lp = e.get("loop")
        if ev == "cards_revealed":
            r = _rec(lp)
            pls = e.get("placements", [])
            mm_anyaku = [p for p in pls if p.get("owner") == "mastermind"
                         and p.get("card") in _ANYAKU_CARDS]
            plus2 = [p for p in mm_anyaku if p.get("card") == "暗躍+2"]
            kinshi = [p for p in pls if p.get("owner") != "mastermind"
                      and p.get("card") == "暗躍禁止"]
            if plus2 and r["plus2_day"] is None:
                p = plus2[0]
                r["plus2_day"] = e.get("day")
                r["plus2_target"] = p.get("target")
                r["plus2_kind"] = p.get("target_kind")
                r["plus2_negated"] = any(k.get("target") == p.get("target")
                                         for k in kinshi)
            # 主人公の暗躍禁止の的中/空振り（+2使用「前/当日」と「後」で区分。
            # 当日は pre に数える＝抑止力仮説は「翌日以降に緩むか」を見るため）
            spent = (r["plus2_day"] is not None
                     and e.get("day") > r["plus2_day"])
            key = "kinshi_post" if spent else "kinshi_pre"
            for k in kinshi:
                r[key] += 1
                if any(p.get("target") == k.get("target") for p in mm_anyaku):
                    r[key + "_hit"] += 1
        elif ev == "loop_result":
            r = _rec(lp)
            res = str(e.get("result", ""))
            if "主人公の敗北" in res:
                r["loop_result"] = "mm_win"
    return [loops[k] for k in sorted(loops)]


class HoldPlus2Mastermind(HeuristicMastermind):
    """反実仮想実験専用（land対象ではない）：day < hold_until の間、set_card の
    候補から 暗躍+2 を除いて温存を強制する。採点・パラメータは無変更＝
    「温存に AI 対 AI で利得が実在するか」の粗い上界/下界を測る道具。
    ★候補除去は rng の消費数を変える＝同点タイブレークが引き直される
    （§11b：±1〜2局の差はノイズ。読みは per-game flip と分布で行う）。"""

    def __init__(self, seed: int, hold_until: int, params: dict | None = None):
        super().__init__(seed, params=params)
        self._hold_until = hold_until

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if (decision == "set_card"
                and view.get("day", 1) < self._hold_until):
            rest = [o for o in options if o.get("card") != "暗躍+2"]
            if rest:
                options = rest
        return super().decide(view, decision, options)


def run_audit(days: int, loops: int = 8, start: int = 0,
              end: int | None = None, hold_until: int = 0) -> list[dict]:
    rows = []
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    for name, seed, sc in scripts[start:end]:
        probe = replace(sc, loops=loops)
        mm = (HoldPlus2Mastermind(seed, hold_until) if hold_until
              else HeuristicMastermind(seed))
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                    "p2": hp, "p3": hp})
        fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not fb:
            outcome, ltw = "defense", state.loop_no
        elif fb:
            outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            ltw = loops + 1
        else:
            outcome, ltw = "loss", loops + 1
        loop_rows = audit_history(state.history)
        # 防衛ループの標識（loop_result イベントは敗北時のみ＝docstring参照）
        if outcome == "defense" and loop_rows:
            loop_rows[-1]["loop_result"] = "protagonist_win"
        rows.append({"script": name, "seed": seed, "days": days,
                     "outcome": outcome, "loops_to_win": ltw,
                     "loops": loop_rows})
        print(f"  {name} s{seed}: {outcome} ltw={ltw}", flush=True)
    return rows


def summarize(rows: list[dict]) -> str:
    day_dist: Counter = Counter()          # +2使用日の分布（ループ単位）
    day_negated: Counter = Counter()       # 使用日別の当日打ち消し
    unused = 0
    n_loops = 0
    pre = post = pre_hit = post_hit = 0
    early_negated_mm_loss = []             # D1-D2切り×打ち消し×そのループmm敗北
    for g in rows:
        for r in g["loops"]:
            n_loops += 1
            d = r["plus2_day"]
            if d is None:
                unused += 1
            else:
                day_dist[d] += 1
                if r["plus2_negated"]:
                    day_negated[d] += 1
                    if d <= 2 and r["loop_result"] == "protagonist_win":
                        early_negated_mm_loss.append(
                            (g["script"], g["seed"], r["loop"]))
            pre += r["kinshi_pre"]
            post += r["kinshi_post"]
            pre_hit += r["kinshi_pre_hit"]
            post_hit += r["kinshi_post_hit"]
    # 勝敗×使用状況のクロス集計（防衛ループ＝mmがそのループを落とした）
    cross: Counter = Counter()
    for g in rows:
        for r in g["loops"]:
            res = r["loop_result"] or "last_or_na"
            d = r["plus2_day"]
            if d is None:
                use = "未使用"
            elif d <= 2:
                use = "D1-2切り・打消" if r["plus2_negated"] else "D1-2切り・通過"
            else:
                use = "D3+切り・打消" if r["plus2_negated"] else "D3+切り・通過"
            cross[(res, use)] += 1
    lines = [
        f"ループ総数 {n_loops}（+2未使用 {unused}）",
        "勝敗×+2使用のクロス: " + "  ".join(
            f"{res}/{use}:{c}" for (res, use), c in sorted(cross.items())),
        "+2使用日の分布: " + "  ".join(
            f"D{d}:{day_dist[d]}" for d in sorted(day_dist)),
        "使用日別の当日打ち消し(暗躍禁止的中): " + "  ".join(
            f"D{d}:{day_negated[d]}/{day_dist[d]}" for d in sorted(day_dist)),
        f"主人公の暗躍禁止 +2使用前/当日: {pre}枚（的中{pre_hit}＝"
        f"{100.0 * pre_hit / pre if pre else 0:.1f}%）",
        f"主人公の暗躍禁止 +2使用後:      {post}枚（的中{post_hit}＝"
        f"{100.0 * post_hit / post if post else 0:.1f}%）",
        f"D1-D2切り×打ち消し×mmループ敗北: {len(early_negated_mm_loss)}件 "
        f"{early_negated_mm_loss[:10]}",
    ]
    return "\n".join(lines)


def compare(base_paths: list[str], exp_paths: list[str]) -> str:
    """ベースラインと実験（強制遅延）を per-game で突き合わせる。"""
    def _load(paths):
        rows = []
        for p in paths:
            with open(p, encoding="utf-8") as f:
                rows.extend(json.load(f))
        return {(r["script"], r["seed"]): r for r in rows}
    base, exp = _load(base_paths), _load(exp_paths)
    keys = sorted(base)
    assert set(keys) == set(exp), "対局セット不一致"
    def _stat(m):
        vals = [m[k]["loops_to_win"] for k in keys]
        defense = sum(1 for k in keys if m[k]["outcome"] == "defense")
        oc = Counter(m[k]["outcome"] for k in keys)
        return defense, round(sum(vals) / len(vals), 3), dict(oc)
    bd, bm, boc = _stat(base)
    ed, em, eoc = _stat(exp)
    lines = [f"base: 防衛{bd} 平均{bm} {boc}",
             f"exp : 防衛{ed} 平均{em} {eoc}",
             "per-game flip（loops_to_win が変わった局）:"]
    n_flip = 0
    for k in keys:
        b, e = base[k], exp[k]
        if (b["loops_to_win"], b["outcome"]) != (e["loops_to_win"], e["outcome"]):
            n_flip += 1
            lines.append(f"  {k[0]} s{k[1]}: {b['loops_to_win']}({b['outcome']})"
                         f" -> {e['loops_to_win']}({e['outcome']})")
    lines.append(f"flip 合計 {n_flip} 局")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-120 暗躍+2 シャドー集計")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--hold-until", type=int, default=0,
                    help="反実仮想：day < N の間 暗躍+2 を候補から除く（0=無効）")
    ap.add_argument("--summarize", nargs="+", default=None,
                    help="既存の out JSON 群を結合して集計のみ行う")
    ap.add_argument("--compare", nargs="+", default=None,
                    help="--compare base.json... VS exp.json...（'VS' 区切り）")
    args = ap.parse_args(argv)
    if args.compare:
        i = args.compare.index("VS")
        print(compare(args.compare[:i], args.compare[i + 1:]))
        return
    if args.summarize:
        rows = []
        for path in args.summarize:
            with open(path, encoding="utf-8") as f:
                rows.extend(json.load(f))
        print(summarize(rows))
        return
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    rows = run_audit(args.days, loops=args.loops, start=args.start,
                     end=args.end, hold_until=args.hold_until)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"→ {args.out}")
    print(summarize(rows))


if __name__ == "__main__":
    main()
