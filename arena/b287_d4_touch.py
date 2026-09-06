# -*- coding: utf-8 -*-
"""B-287 4日級：**そもそも道具が何局・何席を動かしているか**の数え上げ（計測のみ）。

規約§11b「成果の示し方（強い順）」の 1.＝**並び順に依存しない行為の数え上げ**を
4日級で作るための道具。`cur5`（現行既定＝日数ゲートが閉）と `on4`（ゲートを4に緩めた対照）で
同じ局を走らせ、脚本家の伏せ札列が

  - 1席でも変わった局数（＝道具が触った局）
  - 最初に食い違った席のループ番号（＝どのループ頭で降りたか）

を数える。★対局を走らせるので**単独実行**（規約§4）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b287_d4_touch \
        --days 4 --perms id,rev,h1,rot1
"""

from __future__ import annotations

import argparse
from collections import Counter

from arena.b287_d4_flip import run_one, _seq, _outcome


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--perms", type=str, default="id,rev,h1,rot1")
    ap.add_argument("--base", type=str, default="cur5")
    ap.add_argument("--cand", type=str, default="on4")
    ap.add_argument("--loops", type=int, default=8)
    args = ap.parse_args(argv)

    from arena.benchmark import benchmark_scripts
    pool = benchmark_scripts(days=args.days)

    for perm in args.perms.split(","):
        touched, same_seq, flipped = [], 0, []
        first_loops = Counter()
        n_seat_diff = 0
        n_seat_tot = 0
        for name, seed, sc in pool:
            sa = _seq(run_one(name, seed, sc, args.days, args.base, perm,
                              args.loops)[1])
            stb, lgb = run_one(name, seed, sc, args.days, args.cand, perm,
                               args.loops)
            sb = _seq(lgb)
            n_seat_tot += len(sa)
            if sa == sb:
                same_seq += 1
                continue
            n = min(len(sa), len(sb))
            idx = next((i for i in range(n) if sa[i] != sb[i]), n)
            n_seat_diff += sum(1 for i in range(n) if sa[i] != sb[i]) \
                + abs(len(sa) - len(sb))
            touched.append(f"{name}#{seed}")
            first_loops[sa[idx][0] if idx < len(sa) else -1] += 1
        print(f"[{args.days}日級 perm={perm}] 伏せ札列が変わった局 "
              f"{len(touched)}/{len(pool)}（不変 {same_seq}）")
        print(f"  最初の食い違いが起きたループ番号の分布: "
              f"{dict(sorted(first_loops.items()))}")
        print(f"  触れた局: {'  '.join(touched)}")


if __name__ == "__main__":
    main()
