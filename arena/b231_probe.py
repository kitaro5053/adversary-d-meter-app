# -*- coding: utf-8 -*-
"""B-231 プローブ（読み取り専用）：妹在ベンチ局での演繹の健全性と解決速度を測る。

事前登録の的（報告_B231.md §2）：
  1. 健全性＝ON の belief が全観測時点で真配役（神視点）の周辺確率 p>0 を保つ。
  2. 解決速度＝(i) p(妹∈友好無視役職)=0（ON） (ii) n_worlds(ON) ≤ n_worlds(OFF)
     (iii) p(妹=真役職) ON ≥ OFF。strict な時点数も数える。
  3. `random_BTX#10`（5日級）＝ON で p(妹=ファクター)=0（§67-5 の2択が演繹で潰れる）。

観測時点＝各ループ終了時（loop_result イベント直後の prefix）＋全履歴。
履歴は**既定 OFF のベンチと同一の対局**（同 seed・同 agents）から採る＝ON は同じ公開履歴に
対する belief の再計算のみ（演繹の健全性は履歴の由来に依らない）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b231_probe --days 3
"""
from __future__ import annotations

import argparse
import sys

import agents.belief as bl
from agents.belief import Belief, _IGNORE_ROLES


def _play(sc, seed: int, loops: int = 8):
    """ベンチ既定（OFF）と同一条件で1局回し、(history, true_roles) を返す。"""
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    assert bl.B231_IMOUTO_TRAIT is False, "履歴は既定 OFF で採る（ベンチ同一性）"
    probe = replace(sc, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    true_roles = {n: sc.role_of(n) for n in sc.cast}
    return state.history, true_roles


def _prefixes(history):
    """各ループ終了時点（loop_result 直後）＋全履歴の prefix 一覧。"""
    idx = [i + 1 for i, e in enumerate(history) if e.get("event") == "loop_result"]
    if not idx or idx[-1] != len(history):
        idx.append(len(history))
    return idx


def probe_game(gname, seed, sc, days):
    incs = [{"day": i.day, "name": i.name} for i in sc.incidents]
    history, true_roles = _play(sc, seed)
    res = {"game": f"{gname}#{seed}", "days": days,
           "imouto_true": true_roles.get("妹"),
           "points": 0, "strict_points": 0,
           "violations": [], "trajectory": []}
    for k in _prefixes(history):
        pre = history[:k]
        b_off = Belief(sc.cast, incs, set_name=sc.set_name)
        b_off.observe(pre)
        bl.B231_IMOUTO_TRAIT = True
        try:
            b_on = Belief(sc.cast, incs, set_name=sc.set_name)
            b_on.observe(pre)
        finally:
            bl.B231_IMOUTO_TRAIT = False
        res["points"] += 1
        m_on = b_on.role_marginals()
        m_off = b_off.role_marginals()
        w_on, w_off = b_on.n_worlds(), b_off.n_worlds()
        # 的1 健全性：真配役が消えない（全キャラ）
        for n, r in true_roles.items():
            if m_on.get(n, {}).get(r, 0.0) <= 0.0:
                res["violations"].append(
                    {"at": k, "type": "truth_lost", "char": n, "role": r})
        # 的2(i)：p(妹∈友好無視役職)=0
        p_ign = sum(p for r, p in m_on.get("妹", {}).items() if r in _IGNORE_ROLES)
        if p_ign > 0.0:
            res["violations"].append({"at": k, "type": "ignore_leak", "p": p_ign})
        # 的2(ii)(iii)
        if w_on > w_off:
            res["violations"].append({"at": k, "type": "worlds_grew"})
        p_true_on = m_on.get("妹", {}).get(true_roles.get("妹"), 0.0)
        p_true_off = m_off.get("妹", {}).get(true_roles.get("妹"), 0.0)
        if p_true_on < p_true_off - 1e-9:
            res["violations"].append({"at": k, "type": "true_role_dropped",
                                      "on": p_true_on, "off": p_true_off})
        if w_on < w_off:
            res["strict_points"] += 1
        res["trajectory"].append(
            {"at": k, "w_off": w_off, "w_on": w_on,
             "p_true_off": round(p_true_off, 4), "p_true_on": round(p_true_on, 4),
             "p_factor_off": round(m_off.get("妹", {}).get("ファクター", 0.0), 4),
             "p_factor_on": round(m_on.get("妹", {}).get("ファクター", 0.0), 4),
             "gini_off": round(b_off.role_gini("妹"), 4),
             "gini_on": round(b_on.role_gini("妹"), 4)})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    from arena.benchmark import benchmark_scripts
    out = []
    for gname, seed, sc in benchmark_scripts(days=a.days):
        if "妹" not in sc.cast:
            continue
        r = probe_game(gname, seed, sc, a.days)
        out.append(r)
        traj_last = r["trajectory"][-1] if r["trajectory"] else {}
        print(f"[{a.days}日級] {r['game']} 妹={r['imouto_true']} "
              f"時点={r['points']} strict={r['strict_points']} "
              f"違反={len(r['violations'])} 終点={traj_last}", flush=True)
        for v in r["violations"]:
            print(f"  ★違反: {v}", flush=True)
    if a.out:
        import json, os
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
