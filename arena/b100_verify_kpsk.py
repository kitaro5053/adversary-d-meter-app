# -*- coding: utf-8 -*-
"""B-100 Phase 1 事前検証：`kp_sk` の prob=1.0 が belief の過信でないかの**全数検証**。

各ターンの `plan_for_belief` の出力から kind=="kp_sk" かつ prob>=1.0-1e-9 の脅威を全部拾い、
**脚本の真相（非公開）**と突き合わせる：
  - ラベルから VIP 名とエリアを取り出し、VIP の真の役職が キーパーソン/フレンド か
  - その時点でそのエリアに**真の**シリアルキラーが居るか（＝2人きり殺害が本当に成立しうるか）
  - belief が P(SK)=1.0 と見なしていたキャラ（同エリア）の真の役職
  - そのループが実際に敗北したか／その VIP がそのループで死んだか（死亡日）

★AIの意思決定には触れない（B100_HOOK は記録のみ・戻り値不使用）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_verify_kpsk 5
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_verify_kpsk 3
（OUT=path.json を環境変数で渡すと全観測行をJSONに落とす）
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents import heuristic_protagonist as _hp
from sim import run_game

_LABEL_RE = re.compile(r"^SKによる(.+?)（(.+?)）殺害（2人きり・(.+?)）$")


class Rec:
    def __init__(self, game, seed, script):
        self.game, self.seed, self.script = game, seed, script
        self.rows = []
        self._seen = set()

    def __call__(self, agent, view, options, chosen, score_fn):
        stash = getattr(agent, "_b100_plan", None)
        if not stash:
            return
        threats, _plan = stash
        loop, day = view.get("loop"), view.get("day")
        try:
            roles_m = agent._belief.role_marginals()
        except Exception:
            roles_m = {}
        chars = {c["name"]: c for c in view.get("characters", [])}
        for t in threats:
            if t.kind != "kp_sk" or t.prob < 1.0 - 1e-9:
                continue
            m = _LABEL_RE.match(t.label)
            if not m:
                key = (loop, day, t.label)
                if key in self._seen:
                    continue
                self._seen.add(key)
                self.rows.append({"game": self.game, "seed": self.seed, "loop": loop,
                                  "day": day, "label": t.label, "parse": False})
                continue
            vip, vrole_guess, area = m.group(1), m.group(2), m.group(3)
            key = (loop, day, vip, area)
            if key in self._seen:
                continue
            self._seen.add(key)
            occupants = [n for n, c in chars.items()
                         if c.get("alive", True) and c.get("area") == area]
            true_sk_here = [n for n in occupants
                            if self.script.role_of(n) == "シリアルキラー"]
            belief_sk1 = [n for n in occupants
                          if (roles_m.get(n, {}) or {}).get("シリアルキラー", 0.0) >= 1.0 - 1e-9]
            belief_sk_max = max([(roles_m.get(n, {}) or {}).get("シリアルキラー", 0.0)
                                 for n in occupants if n != vip] or [0.0])
            self.rows.append({
                "game": self.game, "seed": self.seed, "loop": loop, "day": day,
                "vip": vip, "vip_role_guess": vrole_guess, "area": area,
                "vip_true_role": self.script.role_of(vip),
                "vip_ok": self.script.role_of(vip) in ("キーパーソン", "フレンド"),
                "occupants": sorted(occupants),
                "true_sk_here": sorted(true_sk_here),
                "sk_ok": bool(true_sk_here),
                "belief_sk_p1": sorted(belief_sk1),
                "belief_sk_max": round(belief_sk_max, 4),
                "belief_sk_true_roles": {n: self.script.role_of(n) for n in belief_sk1},
                "parse": True,
            })


def run(days: int, limit=None):
    from arena.benchmark import benchmark_scripts
    all_rows = []
    for name, seed, sc in benchmark_scripts(days=days)[: (limit or 10**9)]:
        probe = replace(sc, loops=8)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        rec = Rec(name, seed, probe)
        prev = _hp.B100_HOOK
        _hp.B100_HOOK = rec
        try:
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        finally:
            _hp.B100_HOOK = prev
        lost = {e.get("loop") for e in state.history
                if e.get("event") == "loop_result" and "敗北" in str(e.get("result", ""))}
        deaths = {}          # (loop, name) -> day
        endinfo = {}         # loop -> (day, reason)
        for e in state.history:
            if e.get("event") == "death":
                deaths.setdefault((e.get("loop"), e.get("name")), e.get("day"))
            elif e.get("event") == "loop_end":
                endinfo[e.get("loop")] = (e.get("day"), e.get("reason"))
        for r in rec.rows:
            r["loop_lost"] = r["loop"] in lost
            r["vip_death_day"] = deaths.get((r["loop"], r.get("vip")))
            r["loop_end"] = endinfo.get(r["loop"])
        all_rows.extend(rec.rows)
        print(f"  {name} s{seed}: kp_sk1.0={len(rec.rows)}", file=sys.stderr, flush=True)
    return all_rows


def summarize(rows, tag):
    ok = [r for r in rows if r.get("parse")]
    bad_parse = len(rows) - len(ok)
    n = len(ok)
    vip_ok = sum(1 for r in ok if r["vip_ok"])
    sk_ok = sum(1 for r in ok if r["sk_ok"])
    both = sum(1 for r in ok if r["vip_ok"] and r["sk_ok"])
    print(f"\n=== {tag}: kp_sk prob=1.0 の全数検証 ===")
    print(f"  観測（loop,day,vip,area 単位）: {n} 件（ラベル解析不能 {bad_parse}）")
    print(f"  VIP の真の役職が KP/フレンド : {vip_ok}/{n}")
    print(f"  同エリアに真のSKが居る       : {sk_ok}/{n}")
    print(f"  両方成立（＝過信でない）     : {both}/{n}")
    print(f"  VIP真役職の内訳: {Counter(r['vip_true_role'] for r in ok).most_common()}")
    print("  belief P(SK)=1.0 とされたキャラの真の役職: "
          f"{Counter(v for r in ok for v in r['belief_sk_true_roles'].values()).most_common()}")
    print(f"  敗北ループ内の件数: {sum(1 for r in ok if r['loop_lost'])}/{n}")
    print(f"  その VIP が同ループで実際に死亡: {sum(1 for r in ok if r['vip_death_day'])}/{n}")
    print("  局数: " + str(len({(r['game'], r['seed']) for r in ok})))
    bad = [r for r in ok if not (r["vip_ok"] and r["sk_ok"])]
    if bad:
        print(f"  ★過信の疑い {len(bad)} 件（先頭20）:")
        for r in bad[:20]:
            print(f"    {r['game']} s{r['seed']} L{r['loop']}D{r['day']} vip={r['vip']}"
                  f"(真={r['vip_true_role']}) area={r['area']} 真SK={r['true_sk_here']}"
                  f" beliefSK1.0={r['belief_sk_p1']}→{r['belief_sk_true_roles']}")
    else:
        print("  ★過信ゼロ（全件で VIP も SK も真相と一致）")


def main(argv=None):
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    rows = run(days, limit)
    summarize(rows, f"{days}日級")
    out = os.environ.get("OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"→ {out}")


if __name__ == "__main__":
    main()
