# -*- coding: utf-8 -*-
"""B-243 プローブ（読み取り専用）：ボードX演繹／医者自己対象の演繹の健全性と解決速度。

事前登録の的（`docs/仮_b243_log/報告_B243.md` §0-2）：
  的(i)  健全性＝ON の belief が全観測時点で真配役の周辺確率 p>0／可能世界が増えない／
         **制約を受けた本人**の真役職確率が下がらない（＝規則の向き）。
  的(ii) 解決速度＝教材（ユーザー棋譜）で p(サラリーマン=ウィッチ)=1.0・可能世界 7→3 以下。
  的(iii)ベンチ全局で可能世界の純減・p(真役職) の非減を数える。

観測時点＝各ループ終了時（`loop_result` / `loop_board` 直後）＋全履歴。
履歴は**既定 OFF のベンチと同一の対局**から採る＝ON は同じ公開履歴に対する belief の
再計算のみ（演繹の健全性は履歴の由来に依らない＝B-234 プローブと同じ作法）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b243_probe log
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b243_probe probe --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b243_probe census --days 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import agents.belief as bl
from agents.belief import Belief

_LOG = "docs/feedback_logs/鈴蘭_BTX3d_seed0_4つON後検証_2026-08-17.jsonl"
#: 切替口名 → モジュール属性名
KNOBS = {"bx": "B243_BOARD_X_ROLE", "doc": "B243_DOCTOR_SELF_TARGET"}


def _apply(names) -> dict:
    # ★B-248＝ベースライン条件（何も指定しない＝全 OFF）がリポジトリ既定と一致するかを検査（§72-45）。
    #   `B243_DOCTOR_SELF_TARGET` は本レーンの測定の後に既定 ON 化された＝現在は落ちるのが正しい。
    #   当時の再現は `KNOB_AUDIT_ALLOW_STALE=1`。
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, bl, (), driver="b243_probe")
    old = {k: getattr(bl, a) for k, a in KNOBS.items()}
    for k, a in KNOBS.items():
        setattr(bl, a, k in set(names))
    return old


def _restore(old: dict):
    for k, a in KNOBS.items():
        setattr(bl, a, old[k])


def _belief(cast, incs, set_name, pre, knobs):
    old = _apply(knobs)
    try:
        b = Belief(cast, incs, set_name=set_name)
        b.observe(pre)
        return b.n_worlds(), b.role_marginals(), b.rule_marginals()
    finally:
        _restore(old)


# --------------------------------------------------------------------------
# log：教材（ユーザーの実戦棋譜）に対する OFF/ON の比較
# --------------------------------------------------------------------------

def run_log(path: str, knobs) -> int:
    meta = json.loads(open(path, encoding="utf-8").readline())
    sc = meta["script"]
    cast, hist = sc["cast"], meta["history"]
    incs = [{"day": i["day"], "name": i["name"]} for i in sc["incidents"]]
    truth = dict(sc["roles"])                      # ★神視点＝答え合わせにのみ使う
    for c in cast:
        truth.setdefault(c, "パーソン")
    print(f"教材 {os.path.basename(path)} build={meta.get('tool_build')} "
          f"真配役(答え合わせのみ)={sc['roles']} boardX={meta['final_state'].get('rule_y_board_x')}")
    days = sc["days_per_loop"]
    pts = [(lp, dy) for lp in range(1, sc["loops"] + 1) for dy in range(1, days + 1)]
    for lp, dy in pts:
        pre = [e for e in hist
               if e.get("loop") < lp or (e.get("loop") == lp and e.get("day") <= dy)]
        if not pre:
            continue
        w0, m0, _ = _belief(cast, incs, sc.get("set_name", "BTX"), pre, ())
        w1, m1, _ = _belief(cast, incs, sc.get("set_name", "BTX"), pre, knobs)
        bad = [f"{n}={r}" for n, r in truth.items() if m1.get(n, {}).get(r, 0.0) <= 0.0]
        print(f"  L{lp}D{dy} worlds {w0}->{w1}"
              f"  p(サラリーマン=ウィッチ) {m0.get('サラリーマン', {}).get('ウィッチ', 0):.3f}"
              f"->{m1.get('サラリーマン', {}).get('ウィッチ', 0):.3f}"
              f"  p(巫女=ミスリーダー) {m0.get('巫女', {}).get('ミスリーダー', 0):.3f}"
              f"->{m1.get('巫女', {}).get('ミスリーダー', 0):.3f}"
              + (f"  ★真配役消失 {bad}" if bad else ""))
    return 0


# --------------------------------------------------------------------------
# ベンチ（合成対局）に対する健全性・解決速度
# --------------------------------------------------------------------------

def _play(sc, seed: int, loops: int = 8):
    """ベンチ既定と同一条件（B-243 は両方 OFF）で1局回し、(history, true_roles) を返す。"""
    from dataclasses import replace

    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    assert not bl.B243_BOARD_X_ROLE and not bl.B243_DOCTOR_SELF_TARGET, \
        "履歴は既定 OFF で採る（ベンチ同一性）"
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state.history, {n: sc.role_of(n) for n in sc.cast}


def _prefixes(history):
    """観測時点＝ループ終了（`loop_board`）直後 ∪ mm能力フェイズの不安直後 ∪ 全履歴。"""
    idx = {i + 1 for i, e in enumerate(history)
           if e.get("event") in ("loop_board", "loop_result")
           or (e.get("phase") == "mastermind_ability" and e.get("event") == "unrest")}
    idx.add(len(history))
    return sorted(idx)


def probe_game(gname, seed, sc, days, knobs, loops=8) -> dict:
    incs = [{"day": i.day, "name": i.name} for i in sc.incidents]
    history, true_roles = _play(sc, seed, loops=loops)
    res = {"game": f"{gname}#{seed}", "days": days, "rule_y": sc.rule_y,
           "points": 0, "worlds_cut": 0, "p_up": 0, "pre_existing": 0,
           "violations": []}
    bx_role = bl._BOARD_X_ROLE_BY_RULE_Y.get(sc.rule_y)
    holder = next((n for n in sc.cast if sc.role_of(n) == bx_role), None) if bx_role else None
    res["bx_role"] = bx_role
    res["bx_holder"] = holder
    for k in _prefixes(history):
        pre = history[:k]
        w0, m0, _ = _belief(sc.cast, incs, sc.set_name, pre, ())
        w1, m1, _ = _belief(sc.cast, incs, sc.set_name, pre, knobs)
        res["points"] += 1
        # --- 的(i) 健全性 ---------------------------------------------------
        # ★ON が **新たに** 真配役を消したときだけ違反に数える。OFF の時点で既に
        #   消えているもの（＝B-243 と無関係な既存の過剰消去）は `pre_existing` に分けて
        #   記録するだけにする（レーン外の申告材料）。
        for n, r in true_roles.items():
            p_on = m1.get(n, {}).get(r, 0.0)
            p_off = m0.get(n, {}).get(r, 0.0)
            if p_on <= 0.0:
                if p_off > 0.0:
                    res["violations"].append({"at": k, "type": "truth_lost",
                                              "char": n, "role": r})
                else:
                    res["pre_existing"] += 1
        if w1 > w0:
            res["violations"].append({"at": k, "type": "worlds_grew",
                                      "on": w1, "off": w0})
        # ★向き＝**制約を受けた本人**（ボードX役職の真の担い手）の真役職確率は下がらない
        #   （不可能世界の除去は分子を変えず分母だけ減らす）。他キャラの低下は健全な再正規化
        #   ＝B-234 §の自己訂正どおり不変量ではないので数えない。
        if holder is not None:
            p0 = m0.get(holder, {}).get(bx_role, 0.0)
            p1 = m1.get(holder, {}).get(bx_role, 0.0)
            if p1 < p0 - 1e-12:
                res["violations"].append({"at": k, "type": "direction_wrong",
                                          "char": holder, "role": bx_role,
                                          "off": p0, "on": p1})
            if p1 > p0 + 1e-12:
                res["p_up"] += 1
        if w1 < w0:
            res["worlds_cut"] += 1
    return res


def run_probe(days: int, knobs, loops: int = 8) -> int:
    from arena.benchmark import benchmark_scripts
    tot = {"points": 0, "worlds_cut": 0, "p_up": 0, "pre_existing": 0}
    viol: list = []
    for gname, seed, sc in benchmark_scripts(days=days):
        r = probe_game(gname, seed, sc, days, knobs, loops=loops)
        for k in tot:
            tot[k] += r[k]
        viol += [{**v, "game": r["game"]} for v in r["violations"]]
        print(f"[{days}日級] {r['game']:22s} ruleY={r['rule_y']:12s} "
              f"X役={r['bx_role'] or '—'}({r['bx_holder'] or '—'}) "
              f"時点={r['points']:3d} 世界純減={r['worlds_cut']:3d} p上昇={r['p_up']:3d} "
              f"違反={len(r['violations'])} 既存={r['pre_existing']}", flush=True)
    kinds: dict = {}
    for v in viol:
        kinds[v["type"]] = kinds.get(v["type"], 0) + 1
    print(f"  == {days}日級 合計: 時点={tot['points']} 世界純減={tot['worlds_cut']} "
          f"p上昇={tot['p_up']} **違反={len(viol)}** {kinds} "
          f"／OFF時点で既に消えていた真配役（B-243 と無関係）={tot['pre_existing']}",
          flush=True)
    for v in viol[:20]:
        print("   違反:", v)
    return 0


# --------------------------------------------------------------------------
# census：発火面積（ボードX役職のあるルールYの局が何局あるか）
# --------------------------------------------------------------------------

def run_census(days: int) -> int:
    from arena.benchmark import benchmark_scripts
    n = 0
    tot = 0
    for gname, seed, sc in benchmark_scripts(days=days):
        tot += 1
        role = bl._BOARD_X_ROLE_BY_RULE_Y.get(sc.rule_y)
        if role:
            n += 1
            holder = next((c for c in sc.cast if sc.role_of(c) == role), None)
            print(f"[{days}日級] {gname}#{seed} ruleY={sc.rule_y} {role}={holder} "
                  f"初期エリア={__import__('engine.data', fromlist=['x']).initial_area_of(holder)}")
    print(f"  == {days}日級 ボードXルール在={n}/{tot}局")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-243 プローブ（読み取り専用）")
    ap.add_argument("mode", choices=("log", "probe", "census"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--log", default=_LOG)
    ap.add_argument("--knobs", default="bx,doc",
                    help="ON にする切替口（bx / doc のカンマ区切り）")
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    knobs = tuple(k for k in a.knobs.split(",") if k)
    for k in knobs:
        if k not in KNOBS:
            raise SystemExit(f"未知の切替口: {k}")
    if a.mode == "log":
        return run_log(a.log, knobs)
    if a.mode == "census":
        return run_census(a.days)
    return run_probe(a.days, knobs, loops=a.loops)


if __name__ == "__main__":
    sys.exit(main())
