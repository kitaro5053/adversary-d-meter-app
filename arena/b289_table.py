# -*- coding: utf-8 -*-
"""B-289 の判定表（`arena/b289_results/*.json` を読むだけ・対局は走らせない）。

物差し＝§72-104 の3層＋番人：
  第1層＝**降りた後の配分のセンサス**（発火した決定の分類・特に (i) alt が増え
          (ii) キャラ暗躍が減ったか）＝並び順に依存しない行為の数え上げ（規約 §11b 段1）
  第2層＝脚本家が取ったループ総数 Σ(min(ltw,9)−1)  ← ゲートの主
  第3層＝perm 4条件での符号一致
  番人＝複線演出の非decoy（no_contribution − designed_decoy）と mm_lint D1〜D6
★`defense` は脚本家側ゲートに使わない（§72-104 ユーザー裁定）＝参考値として並べるだけ。

CLI:
    PYTHONIOENCODING=utf-8 python -m arena.b289_table --days 5 \
        --perms id,rev,h1,rot1 --base cur --cands b20,b40,b60
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

RESULTS = os.path.join(os.path.dirname(__file__), "b289_results")
_CLASSES = ("i_alt", "ii_char_anyaku", "iii_other_board", "iv_non_anyaku",
            "v_same_seat")


def load(days: int, perm: str, mode: str, tag: str = "s") -> dict:
    with open(os.path.join(RESULTS, f"{tag}_d{days}_{perm}_{mode}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def stats(rep: dict) -> dict:
    games = rep["games"]
    rows = [r for g in games for r in g["rows"]]
    red = [r for r in rows if r["redirected"]]
    guard, lint = Counter(), Counter()
    for g in games:
        guard.update(g["guard_a"])
        lint.update(g["lint"])
    return {
        "n": len(games),
        "fired": len(rows),
        "redir": len(red),
        "cls": Counter(r["class"] for r in rows),
        "cls_red": Counter(r["class"] for r in red),
        "turn_alt": sum(1 for r in rows if r.get("turn_alt_pump")),
        "l2": sum(min(g["loops_to_win"], 9) - 1 for g in games),
        "mean": sum(g["loops_to_win"] for g in games) / len(games),
        "oc": Counter(g["outcome"] for g in games),
        "no_contrib": guard["no_contribution"],
        "designed_decoy": guard["designed_decoy"],
        "non_decoy": guard["no_contribution"] - guard["designed_decoy"],
        "board_anyaku": guard["board_anyaku"],
        "lint": lint,
    }


def flips(base: dict, cand: dict) -> list[tuple]:
    b = {(g["script"], g["seed"]): g for g in base["games"]}
    out = []
    for g in cand["games"]:
        k = (g["script"], g["seed"])
        o = b.get(k)
        if o is None:
            continue
        if (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"], g["outcome"]):
            out.append((k[0], k[1], o["loops_to_win"], o["outcome"],
                        g["loops_to_win"], g["outcome"],
                        min(g["loops_to_win"], 9) - min(o["loops_to_win"], 9)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--perms", type=str, default="id,rev,h1,rot1")
    ap.add_argument("--base", type=str, default="cur")
    ap.add_argument("--cands", type=str, default="b20,b40,b60")
    ap.add_argument("--tag", type=str, default="s")
    args = ap.parse_args(argv)
    perms = args.perms.split(",")
    cands = args.cands.split(",")
    d = args.days

    print("=" * 118)
    print(f"{'条件':<12}{'形':<6}{'発火':>6}{'動いた':>7}"
          f"{'(i)alt':>8}{'(ii)キャラ':>10}{'(iii)板':>8}{'(iv)他札':>9}"
          f"{'(v)同席':>8}{'Σmm':>6}{'Δ':>5}{'平均ltw':>9}{'def':>5}"
          f"{'D1':>4}{'D2':>4}{'非decoy':>8}")
    tot = {m: 0 for m in [args.base] + cands}
    signs = {m: [] for m in cands}
    for perm in perms:
        base = stats(load(d, perm, args.base, args.tag))
        for tag in [args.base] + cands:
            s = stats(load(d, perm, tag, args.tag))
            dl = s["l2"] - base["l2"]
            tot[tag] += s["l2"]
            if tag in signs:
                signs[tag].append(dl)
            c = s["cls_red"]
            print(f"d{d}_{perm:<9}{tag:<6}{s['fired']:>6}{s['redir']:>7}"
                  + "".join(f"{c.get(k, 0):>8}" if i != 1 else f"{c.get(k, 0):>10}"
                            for i, k in enumerate(_CLASSES[:4]))
                  + f"{c.get('v_same_seat', 0):>8}"
                  + f"{s['l2']:>6}{dl:>+5}{s['mean']:>9.3f}"
                  f"{s['oc'].get('defense', 0):>5}"
                  f"{s['lint'].get('D1', 0):>4}{s['lint'].get('D2', 0):>4}"
                  f"{s['non_decoy']:>8}")
        print("-" * 118)
    print(f"※ (i)〜(v) は★手が動いた決定（redirected）の内訳＝「降りた後の配分」そのもの")
    print(f"Σ第2層  {args.base}={tot[args.base]}")
    for m in cands:
        sg = signs[m]
        ok = ("YES(全正)" if all(x > 0 for x in sg)
              else ("全負" if all(x < 0 for x in sg)
                    else ("ゼロ含み(非負)" if all(x >= 0 for x in sg)
                          else "NO(混在)")))
        print(f"  {m}: Σ={tot[m]}  Δ合計={tot[m] - tot[args.base]:+d}  "
              f"perm別Δ={sg}  第3層符号一致={ok}")
    print()
    print("== 番人（複線演出の非decoy／mm_lint）==")
    for perm in perms:
        for tag in [args.base] + cands:
            s = stats(load(d, perm, tag, args.tag))
            print(f"  d{d}_{perm} {tag}: 非decoy={s['non_decoy']}"
                  f" ボード暗躍{s['board_anyaku']}席/寄与なし{s['no_contrib']}"
                  f"(ダミー{s['designed_decoy']})"
                  f" lint={dict(sorted(s['lint'].items()))}"
                  f" 結末={dict(sorted(s['oc'].items()))}")
    print()
    print(f"== flip 全数（{args.base}→各形）==")
    for perm in perms:
        for tag in cands:
            fl = flips(load(d, perm, args.base, args.tag),
                       load(d, perm, tag, args.tag))
            txt = "  ".join(f"{a}#{b}:{c}({e2[:4]})→{e}({f[:4]})[{g:+d}]"
                            for a, b, c, e2, e, f, g in fl)
            print(f"d{d}_{perm} {tag}: {txt if txt else '(flip なし)'}")


if __name__ == "__main__":
    main()
