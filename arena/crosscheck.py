# -*- coding: utf-8 -*-
"""レース解析（loop_race）×詰みソルバ（loop_solver）の突き合わせ健診。

レースは高速な近似・ソルバは「厳密日数＋レース葉」のハイブリッド。不一致は
レース会計の甘い/辛い箇所の候補で、**裁定は深い方が勝つ**：
    exact_days=2（2日厳密） ＞ exact_days=1 ＞ レース単体

★2026-07-08の教訓（レースへの安易なパターン還元は危険）：
- 初回の突き合わせで「レースが甘い」12件を検出→2パターン（カルティスト犯人の
  同一枠ジレンマ・カルティスト＋クロマク需要超過）をレースへ還元したが、
  exact_days=2 の検証で random_FS_17 / random_BTX_4 が **protagonist**
  （＝パターンは偽NG）と判明し撤回（git revert 94361ac）。
  見落とし＝脚本家も同一対象に1枚制限（不安+1とフェリーは排他）＋ML移動禁止で
  1応手が複数の中身解釈を同時にカバーできる。席数の静的会計は日をまたぐ
  амortization（毎ターン要る需要と一度きりの需要の混同）で嘘をつきやすい。
- 一方 random_FS_6 / random_BTX_13 は exact2 でも mastermind（SK位置ゲームの
  強制ライン）＝レースの楽観は残るが、これをレースに健全に符号化するのは筋が悪い。

→ **脚本のNG判定はソルバ（solve_script）を正とする**（生成器・脚本健診）。
  レースは「全局面で瞬時」が取り柄の下界ヒント（playの詰みヒント・葉評価）に徹する。

CLI: PYTHONHASHSEED=0 python -m arena.crosscheck [--n-random 20] [--days5 10]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from sim import random_script
from sim.loop_race import analyze_script
from sim.loop_solver import solve_script
from sim.sample_scripts import SAMPLE_SCRIPTS


def build_corpus(n_random: int = 20, n_days5: int = 10):
    corpus = [(name, f()) for name, f in SAMPLE_SCRIPTS.items()]
    for setn in ("FS", "BTX"):
        for seed in range(n_random):
            corpus.append((f"random_{setn}_{seed}", random_script(setn, seed)))
    for setn in ("FS", "BTX"):
        for seed in range(n_days5):
            corpus.append((f"random5d_{setn}_{seed}",
                           random_script(setn, seed, loops=4, days=5)))
    return corpus


def run_crosscheck(n_random: int = 20, n_days5: int = 10, verbose: bool = True):
    rows = []
    t0 = time.time()
    for name, sc in build_corpus(n_random, n_days5):
        r = analyze_script(sc).verdict
        try:
            s = solve_script(sc)
        except Exception as e:   # 未対応ケースは記録して続行
            s = f"ERR:{type(e).__name__}"
        rows.append((name, r, s))
        if verbose:
            tag = "" if r == s else "  ★不一致"
            print(f"{name}: race={r} solve={s}{tag}", flush=True)
    dis = [(n, r, s) for n, r, s in rows if r != s]
    print(f"\n計{len(rows)}本 / 不一致{len(dis)}本 / {time.time() - t0:.0f}s")
    for n, r, s in dis:
        print(f"  {n}: race={r} → solve={s}")
    return rows, dis


def main(argv=None):
    ap = argparse.ArgumentParser(description="レース×ソルバ突き合わせ健診")
    ap.add_argument("--n-random", type=int, default=20)
    ap.add_argument("--days5", type=int, default=10)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 での実行を推奨します。", file=sys.stderr)
    run_crosscheck(args.n_random, args.days5, verbose=not args.quiet)


if __name__ == "__main__":
    main()
