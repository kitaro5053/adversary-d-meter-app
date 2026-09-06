# -*- coding: utf-8 -*-
"""B-234 プローブ（読み取り専用）：ご神木の両方向の演繹の健全性と解決速度を測る。

事前登録の的（`docs/仮_b234_log/報告_B234.md` §2）：
  的1 健全性＝ON の belief が全観測時点で真配役（神視点）の周辺確率 p>0 を保つ／可能世界が
       増えない／真役職の確率が下がらない／演繹の**向き**が正しい／発生と不発生は両立しない。
  的2 解決速度＝p(ご神木 ∈ 友好無視役職) が 0 or 1 へ寄る・可能世界の単調な純減。
  的3 発火面積＝実対局で観測が何局・何時点で出たか（census サブコマンド）。

観測時点＝各ループ終了時（`loop_result` 直後）＋**ご神木の観測イベント直後**（演繹が動く瞬間）
＋全履歴。履歴は**既定 OFF のベンチと同一の対局**から採る＝ON は同じ公開履歴に対する
belief の再計算のみ（演繹の健全性は履歴の由来に依らない）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b234_probe census --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b234_probe probe  --days 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import agents.belief as bl
from agents.belief import _GOSHINBOKU, _IGNORE_ROLES, Belief

_MM_PHASE = "mastermind_ability"


def _play(sc, seed: int, loops: int = 8):
    """ベンチ既定（両フラグ OFF）と同一条件で1局回し、(history, true_roles) を返す。"""
    from dataclasses import replace

    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    assert bl.B234_GOSHINBOKU_TRAIT is False, "履歴は既定 OFF で採る（ベンチ同一性）"
    probe = replace(sc, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state.history, {n: sc.role_of(n) for n in sc.cast}


def _is_mm_move(e) -> bool:
    return (e.get("event") == "goshinboku_move" and e.get("phase") == _MM_PHASE
            and e.get("from") == _GOSHINBOKU)


def _is_idle(e) -> bool:
    return e.get("event") == "goshinboku_idle"


def _prefixes(history):
    """観測時点＝ループ終了直後 ∪ ご神木の観測イベント直後 ∪ 全履歴（昇順・重複排除）。"""
    idx = {i + 1 for i, e in enumerate(history)
           if e.get("event") == "loop_result" or _is_mm_move(e) or _is_idle(e)}
    idx.add(len(history))
    return sorted(idx)


def _p_ignore(marg, name) -> float:
    return sum(p for r, p in marg.get(name, {}).items() if r in _IGNORE_ROLES)


# --------------------------------------------------------------------------
# census：発火面積（実対局で観測が実際に出た局・時点を数える）
# --------------------------------------------------------------------------

def census(days: int, loops: int = 8) -> list[dict]:
    from arena.benchmark import benchmark_scripts
    out = []
    for gname, seed, sc in benchmark_scripts(days=days):
        if _GOSHINBOKU not in sc.cast:
            continue
        history, true_roles = _play(sc, seed, loops=loops)
        n_move = sum(1 for e in history if _is_mm_move(e))
        n_idle = sum(1 for e in history if _is_idle(e))
        first = next(((e.get("loop"), e.get("day"),
                       "move" if _is_mm_move(e) else "idle")
                      for e in history if _is_mm_move(e) or _is_idle(e)), None)
        # 主人公が主人公能力フェイズに任意で使った分（証拠にならない＝数だけ記録）
        n_pmove = sum(1 for e in history if e.get("event") == "goshinboku_move"
                      and e.get("phase") != _MM_PHASE)
        r = {"game": f"{gname}#{seed}", "days": days,
             "true_role": true_roles.get(_GOSHINBOKU),
             "is_ignore": true_roles.get(_GOSHINBOKU) in _IGNORE_ROLES,
             "n_mm_move": n_move, "n_idle": n_idle, "n_prot_move": n_pmove,
             "first": first, "n_events": len(history)}
        out.append(r)
        print(f"[{days}日級] {r['game']} 役職={r['true_role']}"
              f"{'（友好無視）' if r['is_ignore'] else ''} "
              f"発生={n_move} 不発生={n_idle} 主人公任意={n_pmove} 初観測={first}",
              flush=True)
    n_g = len(out)
    n_ign = sum(1 for r in out if r["is_ignore"])
    n_fire = sum(1 for r in out if r["n_mm_move"] or r["n_idle"])
    print(f"  == {days}日級 ご神木在={n_g}局（うち友好無視役職={n_ign}局）／"
          f"観測が出た局={n_fire}", flush=True)
    return out


# --------------------------------------------------------------------------
# probe：健全性と解決速度（OFF/ON 並走再計算）
# --------------------------------------------------------------------------

def probe_game(gname, seed, sc, days, loops=8) -> dict:
    incs = [{"day": i.day, "name": i.name} for i in sc.incidents]
    history, true_roles = _play(sc, seed, loops=loops)
    truth = true_roles.get(_GOSHINBOKU)
    res = {"game": f"{gname}#{seed}", "days": days, "true_role": truth,
           "is_ignore": truth in _IGNORE_ROLES,
           "points": 0, "strict_points": 0, "decided_points": 0,
           "renorm_drops": 0, "violations": [], "trajectory": []}
    for k in _prefixes(history):
        pre = history[:k]
        seen_move = any(_is_mm_move(e) for e in pre)
        seen_idle = any(_is_idle(e) for e in pre)
        b_off = Belief(sc.cast, incs, set_name=sc.set_name)
        b_off.observe(pre)
        bl.B234_GOSHINBOKU_TRAIT = True
        try:
            b_on = Belief(sc.cast, incs, set_name=sc.set_name)
            b_on.observe(pre)
        finally:
            bl.B234_GOSHINBOKU_TRAIT = False
        res["points"] += 1
        m_on, m_off = b_on.role_marginals(), b_off.role_marginals()
        w_on, w_off = b_on.n_worlds(), b_off.n_worlds()
        # --- 的1 健全性 -----------------------------------------------------
        for n, r in true_roles.items():          # 真配役が消えない（全キャラ）
            if m_on.get(n, {}).get(r, 0.0) <= 0.0:
                res["violations"].append({"at": k, "type": "truth_lost",
                                          "char": n, "role": r})
        if w_on > w_off:
            res["violations"].append({"at": k, "type": "worlds_grew",
                                      "on": w_on, "off": w_off})
        # ★真役職の確率が下がらない＝**制約を受けた本人（ご神木）にだけ成り立つ定理**。
        #   p(ご神木=r) = W_r / W_total で、不可能世界の除去は分子を変えず分母だけ減らす
        #   ＝真役職（観測と両立する）の確率は必ず上がるか等しい。
        #   ★一方 **他キャラでは成り立たない**（＝事前登録の的1-3 を全キャラに広げたのは
        #   登録側の誤り。§5 に正直に記載）。ご神木からカルティスト枠が外れれば、その枠は
        #   他の誰かに回る＝他キャラの「別の役職」の確率は当然下がる＝健全な再正規化であって
        #   誤演繹ではない（真配役が消えていないこと＝`truth_lost` が本来の健全性の判定）。
        for n, r in true_roles.items():
            if m_on.get(n, {}).get(r, 0.0) < m_off.get(n, {}).get(r, 0.0) - 1e-9:
                if n == _GOSHINBOKU:
                    res["violations"].append({"at": k, "type": "true_role_dropped",
                                              "char": n, "role": r})
                else:
                    res["renorm_drops"] += 1   # 統計として数えるだけ（違反ではない）
        if seen_move and seen_idle:              # 両立＝どちらかが壊れている
            res["violations"].append({"at": k, "type": "both_signals"})
        # --- 的1(4) 演繹の向き ----------------------------------------------
        p_ign_on = _p_ignore(m_on, _GOSHINBOKU)
        if seen_move and abs(p_ign_on - 1.0) > 1e-9:
            res["violations"].append({"at": k, "type": "direction_wrong",
                                      "signal": "move", "p_ign": p_ign_on})
        if seen_idle and not seen_move and p_ign_on > 1e-12:
            res["violations"].append({"at": k, "type": "direction_wrong",
                                      "signal": "idle", "p_ign": p_ign_on})
        # 観測が出ているのに真値と食い違う向きへ寄ったら誤演繹（神視点＝答え合わせのみ）
        if seen_move and truth not in _IGNORE_ROLES:
            res["violations"].append({"at": k, "type": "signal_vs_truth",
                                      "signal": "move", "truth": truth})
        if seen_idle and truth in _IGNORE_ROLES:
            res["violations"].append({"at": k, "type": "signal_vs_truth",
                                      "signal": "idle", "truth": truth})
        # --- 的2 解決速度 ---------------------------------------------------
        if w_on < w_off:
            res["strict_points"] += 1
        if seen_move or seen_idle:
            res["decided_points"] += 1
        res["trajectory"].append(
            {"at": k, "move": seen_move, "idle": seen_idle,
             "w_off": w_off, "w_on": w_on,
             "p_ign_off": round(_p_ignore(m_off, _GOSHINBOKU), 4),
             "p_ign_on": round(p_ign_on, 4),
             "p_true_off": round(m_off.get(_GOSHINBOKU, {}).get(truth, 0.0), 4),
             "p_true_on": round(m_on.get(_GOSHINBOKU, {}).get(truth, 0.0), 4),
             "gini_off": round(b_off.role_gini(_GOSHINBOKU), 4),
             "gini_on": round(b_on.role_gini(_GOSHINBOKU), 4)})
    return res


def probe(days: int, loops: int = 8) -> list[dict]:
    from arena.benchmark import benchmark_scripts
    out = []
    for gname, seed, sc in benchmark_scripts(days=days):
        if _GOSHINBOKU not in sc.cast:
            continue
        r = probe_game(gname, seed, sc, days, loops=loops)
        out.append(r)
        last = r["trajectory"][-1] if r["trajectory"] else {}
        print(f"[{days}日級] {r['game']} 役職={r['true_role']}"
              f"{'（友好無視）' if r['is_ignore'] else ''} "
              f"時点={r['points']} 演繹あり={r['decided_points']} "
              f"strict={r['strict_points']} 違反={len(r['violations'])} "
              f"再正規化で下がった他キャラ役職={r['renorm_drops']} 終点={last}",
              flush=True)
        for v in r["violations"]:
            print(f"  ★違反: {v}", flush=True)
    nv = sum(len(r["violations"]) for r in out)
    print(f"  == {days}日級 局={len(out)} 時点={sum(r['points'] for r in out)} "
          f"演繹あり={sum(r['decided_points'] for r in out)} "
          f"strict={sum(r['strict_points'] for r in out)} **違反総数={nv}** "
          f"（参考）他キャラの再正規化での低下"
          f"={sum(r['renorm_drops'] for r in out)}", flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("census", "probe"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    out = census(a.days, loops=a.loops) if a.mode == "census" else probe(a.days, loops=a.loops)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
