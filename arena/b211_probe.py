# -*- coding: utf-8 -*-
"""B-211 Phase 0：`loss_signatures()` の**帰属漏れ**を一次ソースで全数調査する読み取り専用プローブ。

★`agents/` `sim/` `engine/` の判断経路には一切触れない（読むだけ・monkeypatch は計測用の
  ラッパで、返り値も副作用も変えない）。

何を測るか
----------
両ベンチのコーパスを素で回し、**敗北した全ループ**について

1. `loss_signatures()` が付けた `type` と、`past_loss_keys()` 相当の**帰属キー**が
   1つでも立つか（立たない＝`repeat_count` が絶対に 0＝反復脅威として二度と数えられない）。
2. 秘匿側の真の敗北原因（ループ終了評価の直前に state を覗いて記録）と突き合わせ、
   **どの敗北条件が帰属キー無しに落ちるか**を全数で列挙する。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b211_probe --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b211_probe --days 5
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.b100_mix import loss_signatures
from arena.benchmark import benchmark_scripts
from sim import run_game

_BOARDS = ("病院", "神社", "都市", "学校")


def _truth_probe():
    """ループ終了評価の**直前**の state を覗いて真の敗北原因を記録するラッパを仕込む。

    返り値 = (records, restore)。records[loop] = {"friend_dead": [...], "boards2": [...],
    "kp2": [...], "butterfly": bool, "defeat_before": bool}
    """
    from sim import effects, flow

    orig = flow.evaluate_loop_end
    recs: dict[int, dict] = {}

    def wrapper(state):
        ry = state.script.rule_y
        boards2 = [b for b in _BOARDS if state.board_anyaku.get(b, 0) >= 2]
        fired = None
        if ry == "守るべき場所" and state.board_anyaku.get("学校", 0) >= 2:
            fired = "学校"
        elif ry == "封印されしモノ" and state.board_anyaku.get("神社", 0) >= 2:
            fired = "神社"
        elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") \
                and state.rule_y_board_x is not None \
                and state.board_anyaku.get(state.rule_y_board_x, 0) >= 2:
            fired = state.rule_y_board_x
        kp2 = [c.name for c in state.characters.values()
               if c.role == "キーパーソン" and c.anyaku >= 2] \
            if ry == "僕と契約しようよ！" else []
        recs[state.loop_no] = {
            "friend_dead": sorted(n for n, c in state.characters.items()
                                  if c.role == "フレンド" and not c.alive),
            "friend_already_revealed": sorted(state.friend_revealed),
            "boards2": boards2,
            "ry_board": fired,
            "kp2": kp2,
            "butterfly": bool(ry == "未来改変プラン" and state.butterfly_fired),
            "defeat_before": bool(state.defeat),       # 即敗北（KP死/主人公死/TT）が既に成立
            "rule_y": ry,
        }
        return orig(state)

    flow.evaluate_loop_end = wrapper
    return recs, (lambda: setattr(flow, "evaluate_loop_end", orig))


def _keys_for(sig: dict) -> set:
    """`past_loss_keys` が実際に立てる帰属キーの集合（同じ規則をそのまま写す）。"""
    ty = sig["type"]
    out: set = set()
    if ty == "loop_end_cond":
        out |= set(sig["boards"]) | set(sig["anyaku_chars"])
    else:
        out |= set(sig["fatal_deaths"])
    out |= {n for _d, n in sig["incidents"]}
    return out


def run(days: int, loops: int = 8) -> dict:
    rows = []
    for name, seed, sc in benchmark_scripts(days=days):
        recs, restore = _truth_probe()
        try:
            probe = replace(sc, loops=loops)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        finally:
            restore()
        sigs = loss_signatures(state.history)
        # そのループに loop_end（公開）が出たか
        for lp, s in sorted(sigs.items()):
            truth = recs.get(lp, {})
            rows.append({
                "game": f"{name}#{seed}", "set": sc.set_name, "loop": lp,
                "type": s["type"],
                "reasons": sorted(s["reasons"]),
                "keys": sorted(_keys_for(s)),
                "boards": sorted(s["boards"]),
                "anyaku_chars": sorted(s["anyaku_chars"]),
                "fatal_deaths": sorted(s["fatal_deaths"]),
                "incidents": sorted(f"{d}:{n}" for d, n in s["incidents"]),
                "deaths": sorted(f"{d}:{n}" for d, n in s["deaths"]),
                "truth": truth,
                "roles": {n: r for n, r in (sc.roles or {}).items()},
            })
    return {"days": days, "rows": rows}


def summarize(res: dict) -> None:
    rows = res["rows"]
    n = len(rows)
    nokey = [r for r in rows if not r["keys"]]
    print(f"===== B-211 Phase 0  {res['days']}日級 =====")
    print(f"敗北ループ総数 = {n} / 帰属キー無し = {len(nokey)} ({len(nokey)/max(n,1)*100:.1f}%)")
    print(f"型の内訳: {dict(Counter(r['type'] for r in rows))}")
    print(f"帰属キー無しの型: {dict(Counter(r['type'] for r in nokey))}")

    # 真の原因での分類（全数）
    def cause(r):
        t = r["truth"]
        cs = []
        if t.get("defeat_before"):
            cs.append("即敗北(KP死/主死/TT)")
        if t.get("friend_dead"):
            cs.append("フレンド死亡")
        if t.get("ry_board"):
            cs.append(f"ルールY板({t['ry_board']})")
        if t.get("kp2"):
            cs.append("僕と契約(KP暗躍2)")
        if t.get("butterfly"):
            cs.append("未来改変(蝶)")
        return "+".join(cs) if cs else "??不明"

    print("\n-- 真の敗北原因 × 帰属キーの有無（全数） --")
    tab: dict = {}
    for r in rows:
        c = cause(r)
        k = "キー有" if r["keys"] else "キー無"
        tab.setdefault(c, Counter())[k] += 1
    for c in sorted(tab, key=lambda x: -sum(tab[x].values())):
        t = tab[c]
        print(f"  {c:38s} 有={t['キー有']:3d} 無={t['キー無']:3d} 計={sum(t.values()):3d}")

    # フレンド死亡が絡む敗北ループ
    fr = [r for r in rows if r["truth"].get("friend_dead")]
    fr_nokey = [r for r in fr if not r["keys"]]
    print(f"\nフレンド死亡が成立していた敗北ループ = {len(fr)}"
          f"（うち帰属キー無し {len(fr_nokey)}）")
    print(f"帰属キー無しのうちフレンド死亡で説明できる = "
          f"{len(fr_nokey)}/{len(nokey)}")

    # 帰属キー無しでフレンド死亡でもないもの＝残る漏れ
    rest = [r for r in nokey if not r["truth"].get("friend_dead")]
    print(f"\n-- 帰属キー無し かつ フレンド死亡でない = {len(rest)} 件（全数列挙） --")
    for r in rest:
        print(f"  {r['game']:16s} L{r['loop']} type={r['type']:14s} "
              f"reasons={r['reasons']} deaths={r['deaths']} inc={r['incidents']} "
              f"truth={cause(r)} ry={r['truth'].get('rule_y')}")

    # フレンド死亡ループで role_reveal が公開されるか（既公開の再死＝公開なし）
    print("\n-- フレンド死亡ループの公開性（role_reveal が出るか） --")
    seen = Counter()
    for r in fr:
        already = r["truth"].get("friend_already_revealed") or []
        dead = r["truth"].get("friend_dead") or []
        newly = [n for n in dead if n not in already]
        seen["新規公開あり" if newly else "既公開のみ（再公開なし）"] += 1
    print(f"  {dict(seen)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = run(a.days)
    summarize(res)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(f"\n[saved] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
