# -*- coding: utf-8 -*-
"""B-242：B-238「族B＝計9日」と B-239「型(i)＝0（完全飽和）」の矛盾を決着させるプローブ。

★読み取り専用＝`agents/` `sim/` `engine/` `rules/` には一切触れない
（perm の着脱だけ `arena.b238_probe.env` を借用＝ファイル不変）。

サブコマンド:
  actual   … 実対局のその日の主人公3席の実手を印字（`暗躍禁止` の実数つき）
  dupscan  … 全局×perm を回し、主人公が同ターンに `暗躍禁止` を2枚以上置いたターンを数える
             （B-239 型(i) の独立再現。**完全列挙＝不存在の主張に足る**）
  order    … `sim.mate.enum_prot_sets`＋`_order_responses` の探索順で、
             「`暗躍禁止` 2枚以上を含む応手」が上位に何個並ぶかを数える
             （B-238 の flip に `暗躍禁止` 2枚が頻出する理由＝探索順の偏りかを検定）

CLI 例:
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b242_probe actual \
      --game btx5_future#8 --days 5 --perm rev --loop 8
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b242_probe dupscan --days 3,5 --perm id,rev,h1
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b242_probe order \
      --game btx5_future#8 --days 5 --perm rev --loop 8 --day 1
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from arena.b238_probe import env, make_script, parse_game

KINSHI = "暗躍禁止"


def _replay(spec: str, days: int, loops: int = 8):
    """ベンチと同一条件で1局打ち、(state, log, snaps, rngs)。"""
    from arena.postmortem import replay_with_snapshots
    _, seed = parse_game(spec)
    sc = make_script(spec, days)
    rngs: dict = {}
    state, log, snaps = replay_with_snapshots(sc, seed, loops=loops, rng_states=rngs)
    return state, log, snaps, rngs


def _prot_set_of(log, loop: int, day: int) -> list[dict]:
    return [dict(e["chosen"], _seat=e["actor"]) for e in log
            if e["actor"] != "mastermind" and e["decision"] == "set_card"
            and e["loop"] == loop and e["day"] == day]


def _mm_set_of(log, loop: int, day: int) -> list[dict]:
    return [e["chosen"] for e in log
            if e["actor"] == "mastermind" and e["decision"] == "set_card"
            and e["loop"] == loop and e["day"] == day]


# ------------------------------------------------------------------ actual
def cmd_actual(args) -> int:
    for p in args.perm.split(","):
        with env(p):
            state, log, _snaps, _r = _replay(args.game, args.days, args.loops)
        loops = ([args.loop] if args.loop
                 else sorted({e["loop"] for e in log if e["decision"] == "set_card"}))
        print(f"=== {args.game} days={args.days} perm={p} "
              f"winner={state.winner} last_loop={state.loop_no}")
        for lp in loops:
            for d in range(1, args.days + 1):
                ps = _prot_set_of(log, lp, d)
                if not ps:
                    continue
                nk = sum(1 for x in ps if x["card"] == KINSHI)
                ms = _mm_set_of(log, lp, d)
                print(f"  L{lp}D{d} 主人公: " +
                      " / ".join(f"{x['_seat']}:{x['card']}→{x['target']}" for x in ps) +
                      f"   [暗躍禁止={nk}]")
                print(f"        脚本家: " +
                      " / ".join(f"{x['card']}→{x['target']}" for x in ms))
    return 0


# ----------------------------------------------------------------- dupscan
def cmd_dupscan(args) -> int:
    """全局・全ターンで「主人公の `暗躍禁止` が同ターンに2枚以上」を完全列挙する。"""
    from arena.benchmark import benchmark_scripts
    from arena.postmortem import replay_with_snapshots
    for days in [int(d) for d in str(args.days).split(",")]:
        for p in args.perm.split(","):
            n_turn = n_dup = n_seat_kinshi = 0
            hist = Counter()
            dups: list[str] = []
            with env(p):
                for gname, seed, _sc in benchmark_scripts(days=days):
                    sc = make_script(f"{gname}#{seed}", days)
                    _st, log, _s = replay_with_snapshots(sc, seed, loops=args.loops)
                    turns = sorted({(e["loop"], e["day"]) for e in log
                                    if e["decision"] == "set_card"})
                    for lp, d in turns:
                        ps = _prot_set_of(log, lp, d)
                        if not ps:
                            continue
                        n_turn += 1
                        nk = sum(1 for x in ps if x["card"] == KINSHI)
                        n_seat_kinshi += nk
                        hist[nk] += 1
                        if nk >= 2:
                            n_dup += 1
                            dups.append(f"{gname}#{seed} L{lp}D{d} 暗躍禁止={nk}")
            print(f"=== days={days} perm={p}: ターン数={n_turn} "
                  f"／`暗躍禁止`席={n_seat_kinshi} ／**2枚以上のターン={n_dup}**")
            print(f"    ターンあたり枚数の分布 {dict(sorted(hist.items()))}")
            for s in dups[:20]:
                print("    " + s)
    return 0


# ------------------------------------------------------------------- order
def cmd_order(args) -> int:
    """探索順の偏りを測る＝`暗躍禁止`2枚以上の応手が上位に何個並ぶか。"""
    from sim.mate import _order_responses, _slim, enum_prot_sets
    from arena.counterfactual import continue_loop
    _, seed = parse_game(args.game)
    for p in args.perm.split(","):
        with env(p):
            _st, log, snaps, rngs = _replay(args.game, args.days, args.loops)
            snap = snaps.get((args.loop, args.day))
            if snap is None:
                print(f"スナップショット無し L{args.loop}D{args.day}")
                return 1
            mm_set = _mm_set_of(log, args.loop, args.day)
            base = _slim(snap)
            responses = enum_prot_sets(base, mm_set)
            if responses is None:
                print("列挙が cap 超過＝unknown")
                return 1
            ordered = _order_responses(base, responses)
            n = len(ordered)
            def nk(R):
                return sum(1 for x in R if x["card"] == KINSHI)
            dup_ranks = [i for i, R in enumerate(ordered, 1) if nk(R) >= 2]
            act = _prot_set_of(log, args.loop, args.day)
            print(f"=== {args.game} days={args.days} perm={p} L{args.loop}D{args.day}")
            print(f"  実対局の主人公3席: " +
                  " / ".join(f"{x['card']}→{x['target']}" for x in act) +
                  f"   [暗躍禁止={sum(1 for x in act if x['card']==KINSHI)}]")
            print(f"  合法応手（多重集合）総数 = {n}")
            print(f"  うち `暗躍禁止`2枚以上 = {len(dup_ranks)}"
                  f"（{100*len(dup_ranks)/n:.1f}%）")
            for topn in (50, 100, 200, 300, 500):
                if topn <= n:
                    c = sum(1 for r in dup_ranks if r <= topn)
                    print(f"    探索順 上位{topn:>4}: {c:>4} 個が2枚以上"
                          f"（{100*c/topn:.0f}%）")
            if args.flip:
                key = (args.loop, args.day)
                rng_state = rngs.get(key)
                found = None
                pool = ordered
                if args.skip_dup:
                    pool = [R for R in ordered if nk(R) < 2]
                    print(f"  （`暗躍禁止`2枚以上を**除外**して探索＝候補 {len(pool)}）")
                for i, R in enumerate(pool, 1):
                    if args.max_candidates and i > args.max_candidates:
                        break
                    if continue_loop(snap, seed, {key: R}, mm_rng_state=rng_state):
                        found = (i, R)
                        break
                if found:
                    i, R = found
                    print(f"  ★flip 発見＝探索順 {i} 番目: " +
                          " / ".join(f"{x['card']}→{x['target']}" for x in R) +
                          f"   [暗躍禁止={nk(R)}]")
                    # 同じ flip から `暗躍禁止` 2枚を除いた「1枚だけ」の応手が
                    # 存在するか（＝2枚は本当に「実質パス」か）は order では測らない。
                else:
                    print(f"  flip なし（上位{args.max_candidates}）")
    return 0


def main(argv=None) -> int:
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    ap = argparse.ArgumentParser(description="B-242 決着プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(q):
        q.add_argument("--days", default="3")
        q.add_argument("--perm", default="id")
        q.add_argument("--loops", type=int, default=8)

    a = sub.add_parser("actual"); common(a)
    a.add_argument("--game", required=True)
    a.add_argument("--loop", type=int, default=0)
    a.set_defaults(func=cmd_actual, _days_int=True)

    b = sub.add_parser("dupscan"); common(b)
    b.set_defaults(func=cmd_dupscan)

    c = sub.add_parser("order"); common(c)
    c.add_argument("--game", required=True)
    c.add_argument("--loop", type=int, required=True)
    c.add_argument("--day", type=int, required=True)
    c.add_argument("--flip", action="store_true")
    c.add_argument("--max-candidates", type=int, default=2500)
    c.add_argument("--skip-dup", action="store_true",
                   help="`暗躍禁止`2枚以上（＝実質パス）の応手を除外して flip を探す")
    c.set_defaults(func=cmd_order)

    args = ap.parse_args(argv)
    if getattr(args, "_days_int", False) or args.cmd == "order":
        args.days = int(args.days)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
