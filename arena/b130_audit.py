# -*- coding: utf-8 -*-
"""B-130：**抑止役（不安を取り除ける友好能力の担い手）が犯人候補と同エリアに居るのに、
なお 不安-1 を撃っている席**の数え上げ（挙動不変・計測のみ＝`arena/b132_audit.py` と同型）。

問い（トリアージ 2026-08-01・手練れ指摘）＝「L3D1 男子学生に友好+2 が載っているので、
学生であるお嬢様がその場にとどまるなら不安+1 を載せられても事件発生を抑止できる。
∴ お嬢様には不安-1 ではなく移動禁止／移動を置くべき」。

計測（`agents.heuristic_protagonist.B100_HOOK`＝既定 None の計測専用フック経由。
フックの戻り値は使われず例外も握り潰される＝AIの選択に一切影響しない）：
  1. `不安-1 → キャラ` を打った席の総数。
  2. うち **同エリアに抑止役が居て、今ターンの主人公能力フェイズに実際に使える**席数
     （判定＝`agents.defense_plan.suppressors_for`＝KB接地の単一ソース）。
  3. さらにそのうち **引き剥がしのリスクが無い**席数（脚本家が対象にも抑止役にも
     札を伏せていない＝ユーザー注記「ミスリーダー候補に移動が伏せられている場合は
     そんな簡単な話でもない」の裏返し）。
  4. その席の次点（＝降格したら何に回るか）の内訳。

CLI（前面実行）:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b130_audit --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b130_audit --days 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.heuristic_protagonist as HP
from agents import HeuristicMastermind, HeuristicProtagonist
from agents import defense_plan as DP
from arena.benchmark import benchmark_scripts
from sim import run_game


def seat_facts(agent, view: dict, options: list, best: dict, score) -> dict | None:
    """1席分の事実（不安-1→キャラ のときだけ dict・それ以外 None）。純粋な観測。"""
    if best.get("card") != "不安-1" or best.get("target_kind") != "character":
        return None
    tgt = best["target"]
    try:
        marg = agent._belief.role_marginals()
    except Exception:
        marg = {}
    sup = DP.suppressors_for(view, tgt, roles=marg)
    sup_loose = DP.suppressors_for(view, tgt, roles=None)
    ign = {n: round(DP._friendship_ignore_prob(marg, n), 3) for n in sup_loose}
    # 参考＝友好が足りていないだけの「未解禁の抑止役」（＝投資すれば抑止役になりうる同席者）
    unfunded = [n for n in DP.suppressors_for(view, tgt, roles=None, require_funded=False)
                if n not in sup_loose]
    mm_chars = {p["target"] for p in view.get("placements", []) or []
                if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}
    # 引き剥がしリスク＝脚本家が対象または抑止役に札を伏せている
    detach_risk = (tgt in mm_chars) or any(s in mm_chars for s in sup)
    detach_risk_loose = (tgt in mm_chars) or any(s in mm_chars for s in sup_loose)
    # ★抑止役側だけの引き剥がしリスク（対象側は自席の移動禁止で留められる＝ユーザーの模範解答）
    sup_free = [s for s in sup if s not in mm_chars]
    sup_free_loose = [s for s in sup_loose if s not in mm_chars]
    alt = sorted(((float(score(o)), o) for o in options
                  if (o["card"], o["target"], o.get("target_kind"))
                  != (best["card"], best["target"], best.get("target_kind"))),
                 key=lambda t: -t[0])
    alt_s, alt_o = (alt[0] if alt else (0.0, {}))
    # 対象キャラへの移動制御札が手札にあるか（降格したら回せる先）
    pin_avail = any(o["card"] == "移動禁止" and o["target"] == tgt
                    and o.get("target_kind") == "character" for o in options)
    move_avail = any(o["card"] in ("移動←→", "移動↑↓") and o["target"] == tgt
                     and o.get("target_kind") == "character" for o in options)
    return {
        "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
        "target": tgt,
        "sup": sorted(sup), "sup_loose": sorted(sup_loose), "ign": ign,
        "detach_risk": detach_risk, "detach_risk_loose": detach_risk_loose,
        "sup_free": sup_free, "sup_free_loose": sup_free_loose,
        "unfunded": unfunded,
        "mm_on_target": tgt in mm_chars,
        "pin_avail": pin_avail, "move_avail": move_avail,
        "score": round(float(score(best)), 2),
        "alt": f"{alt_o.get('card')}→{alt_o.get('target')}" if alt_o else None,
        "alt_score": round(alt_s, 2),
    }


def run_audit(days: int, loops: int = 8, start: int = 0,
              end: int | None = None) -> list[dict]:
    rows = []
    scripts = benchmark_scripts(days=days)
    if end is None:
        end = len(scripts)
    for name, seed, sc in scripts[start:end]:
        probe = replace(sc, loops=loops)
        seats: list[dict] = []

        def _hook(agent, view, options, best, score, _s=seats):
            f = seat_facts(agent, view, options, best, score)
            if f is not None:
                _s.append(f)

        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        HP.B100_HOOK = _hook
        try:
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                        "p2": hp, "p3": hp})
        finally:
            HP.B100_HOOK = None
        fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not fb:
            outcome, ltw = "defense", state.loop_no
        elif fb:
            outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            ltw = loops + 1
        else:
            outcome, ltw = "loss", loops + 1
        rows.append({"script": name, "seed": seed, "days": days,
                     "outcome": outcome, "loops_to_win": ltw, "seats": seats})
        print(f"  {name} s{seed}: {outcome} ltw={ltw} 不安-1席{len(seats)}"
              f" 抑止役同席{sum(1 for s in seats if s['sup'])}", flush=True)
    return rows


def summarize(rows: list[dict]) -> str:
    tot = with_sup = with_sup_loose = safe = safe_loose = 0
    pinnable = pinnable_loose = unfunded_n = 0
    ign_hist: Counter = Counter()
    alt_kinds: Counter = Counter()
    sup_chars: Counter = Counter()
    outcome_c: Counter = Counter()
    detail = []
    for g in rows:
        outcome_c[g["outcome"]] += 1
        for s in g["seats"]:
            tot += 1
            if s["sup_loose"]:
                with_sup_loose += 1
                for n, p in (s.get("ign") or {}).items():
                    ign_hist[f"{n}:{p:.2f}"] += 1
                if not s["detach_risk_loose"]:
                    safe_loose += 1
            if s.get("unfunded"):
                unfunded_n += 1
            if s["sup_free_loose"] and (not s["mm_on_target"] or s["pin_avail"]):
                pinnable_loose += 1
            if s["sup_free"] and (not s["mm_on_target"] or s["pin_avail"]):
                pinnable += 1
                alt_kinds[str(s["alt"]).split("→")[0]] += 1
                detail.append((g["script"], g["seed"], s))
            if not s["sup"]:
                continue
            with_sup += 1
            for n in s["sup"]:
                sup_chars[n] += 1
            if not s["detach_risk"]:
                safe += 1
    lines = [
        f"局数 {len(rows)}  outcome={dict(outcome_c)}",
        f"不安-1→キャラ の席数 合計 {tot}",
        f"  うち 同エリアに抑止役（belief で友好無視の疑い薄）: {with_sup}",
        f"  うち 同エリアに抑止役（友好無視を問わない緩い判定）: {with_sup_loose}",
        f"  うち **引き剥がしリスク無し**（対象にも抑止役にも mm 札なし）: {safe}",
        f"  （緩い判定での 引き剥がしリスク無し席）: {safe_loose}",
        f"  うち **抑止役に mm 札が無く、対象は自席の移動禁止で留められる**: {pinnable}"
        f"（緩い判定 {pinnable_loose}）",
        f"  （参考）同席に**未解禁**の抑止役候補が居た席: {unfunded_n}",
        "  緩い判定の抑止役×友好無視疑い: " + ("  ".join(
            f"{k}×{v}" for k, v in ign_hist.most_common(20)) or "(なし)"),
        "  抑止役の内訳: " + ("  ".join(f"{k}:{v}" for k, v in sup_chars.most_common())
                              or "(なし)"),
        "  留められる席の次点: " + ("  ".join(f"{k}:{v}" for k, v in alt_kinds.most_common())
                                    or "(なし)"),
    ]
    for name, seed, s in detail[:40]:
        lines.append(f"    {name} s{seed} L{s['loop']}D{s['day']} {s['seat']} "
                     f"不安-1→{s['target']}({s['score']}) 抑止役={s['sup']} "
                     f"次点={s['alt']}({s['alt_score']}) "
                     f"移動禁止手札={s['pin_avail']} 移動手札={s['move_avail']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = run_audit(a.days, a.loops, a.start, a.end)
    print(summarize(rows))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
