# -*- coding: utf-8 -*-
"""B-289 の番人（`mm_lint` D1／D4）増分の全数検死（計測のみ・本番経路は無変更）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない。
対局は `sim.run_game` の素の返り値だけを使う（先例＝`arena/b289_probe.py`）。

------------------------------------------------------------------------------
問い（発注＝FableA・B-289 追加発注）
------------------------------------------------------------------------------
b40（`B289_ALT_BONUS=40`）で `mm_lint` D1 が増えた。その増分は

  (a) **決定数の増加による従属現象**（脚本家が長く生き延びる＝打つ手数が増えた分）
  (b) **加点が押し出した手が実際に無効だった**（＝加点の副作用＝本当の害）
  (c) その他（分類器の定義側・既存の帯）

のどれか。★(a) と (b) を分ける物証＝**同一ループ数に揃えた密度比較**：
各局を loop 単位で切り、cur と b40 の**共通ループ**（両条件がともに到達した loop 番号）
だけで「D1 件数 / mm 決定数」を比べる。密度が不変なら (a)、上がっていれば (b)。

★併せて **`flip しなかった局だけ**の密度**も出す（帰結が同じ＝道筋の変化が最も小さい部分集合）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_d1_audit \\
        --days 5 --perm id --mode cur --json out.json
    ... --diff cur.json b40.json      # 全数表と密度の突き合わせ
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import replace

from arena.b289_probe import _MODES, _apply_mode


def audit_game(name: str, seed: int, script, days: int, loops: int = 8) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.mm_lint import lint_move
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    attr = HeuristicMastermind(seed)

    per_loop_dec: Counter = Counter()
    rows = []
    for i, r in enumerate(x for x in log
                          if x["actor"] == "mastermind"
                          and x["decision"] in ("set_card", "mastermind_ability")):
        lp = r["loop"]
        per_loop_dec[lp] += 1
        dets = lint_move(r["view"], r["chosen"])
        if not dets:
            continue
        ch = r["chosen"]
        # ★`mastermind_ability` の options は (card, target) 形ではない＝
        #   `_b286_penalty` に掛けられない。減点の発火は set_card だけで見る。
        is_set = r["decision"] == "set_card"
        a = attr._analyze(r["view"])
        info = a.get("b286") or {}
        fired = bool(is_set and any(attr._b286_penalty(o, a) > 0
                                    for o in r["options"]))
        for det, reason in dets:
            rows.append({
                "key": (days, name, seed, lp, r["day"], i),
                "loop": lp, "day": r["day"], "idx": i,
                "decision": r["decision"], "det": det, "reason": reason,
                "card": ch.get("card"), "target": ch.get("target"),
                "kind": ch.get("target_kind"),
                # ★その手が「乗り換え先（alt_lines）を狙った手」かどうか＝(b) の直接証拠
                "on_alt": ch.get("target") in (info.get("alt_lines") or ()),
                # ★その決定で B-286 の減点が発火していたか（＝加点が効きうる決定か）
                "b286_fired": fired,
            })

    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome, ltw = "defense", state.loop_no
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
        ltw = loops + 1
    else:
        outcome, ltw = "loss", loops + 1
    return {"script": name, "seed": seed, "days": days,
            "outcome": outcome, "loops_to_win": ltw,
            "last_loop": state.loop_no,
            "dec_by_loop": {str(k): v for k, v in sorted(per_loop_dec.items())},
            "n_dec": sum(per_loop_dec.values()),
            "rows": rows}


def sweep(days: int = 5, perm: str = "id", mode: str = "cur",
          loops: int = 8, verbose: bool = True) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    old = _apply_mode(HM, mode)
    try:
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = audit_game(name, seed, sc, days, loops)
            games.append(g)
            if verbose:
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                      f"決定{g['n_dec']} 違反{len(g['rows'])}", flush=True)
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    keys = [(tuple(r["key"]), r["det"]) for g in games for r in g["rows"]]
    assert len(keys) == len(set(keys)), "違反行のキーが一意でない"
    return {"days": days, "perm": perm, "mode": mode,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


# ---------------------------------------------------------------------------
# 突き合わせ
# ---------------------------------------------------------------------------
def _by_game(rep):
    return {(g["script"], g["seed"]): g for g in rep["games"]}


def _det_rows(g, det):
    return [r for r in g["rows"] if r["det"] == det]


def diff(a: dict, b: dict, det: str = "D1") -> str:
    ga, gb = _by_game(a), _by_game(b)
    L = [f"# B-289 番人 {det} 増分の全数検死"
         f"（{a['days']}日級 perm={a['perm']}: {a['mode']} → {b['mode']}）", ""]

    # ---- 1. 全体 ----------------------------------------------------------
    na = sum(len(_det_rows(g, det)) for g in a["games"])
    nb = sum(len(_det_rows(g, det)) for g in b["games"])
    da = sum(g["n_dec"] for g in a["games"])
    db = sum(g["n_dec"] for g in b["games"])
    L += ["## 1. 全体（コーパス全局）",
          f"  {det}: {na} → {nb}（{nb - na:+d}）",
          f"  mm 決定数: {da} → {db}（{db - da:+d}・{100 * (db - da) / da:+.1f}%）",
          f"  ★密度 {det}/決定: {1000 * na / da:.3f}‰ → {1000 * nb / db:.3f}‰"
          f"（{1000 * nb / db - 1000 * na / da:+.3f}‰）", ""]

    # ---- 2. flip しなかった局だけ ------------------------------------------
    same = [k for k in ga if k in gb
            and (ga[k]["loops_to_win"], ga[k]["outcome"])
            == (gb[k]["loops_to_win"], gb[k]["outcome"])]
    flip = [k for k in ga if k in gb and k not in same]
    na_s = sum(len(_det_rows(ga[k], det)) for k in same)
    nb_s = sum(len(_det_rows(gb[k], det)) for k in same)
    da_s = sum(ga[k]["n_dec"] for k in same)
    db_s = sum(gb[k]["n_dec"] for k in same)
    L += [f"## 2. flip しなかった局だけ（{len(same)}局／flip {len(flip)}局）",
          f"  {det}: {na_s} → {nb_s}（{nb_s - na_s:+d}）",
          f"  mm 決定数: {da_s} → {db_s}（{db_s - da_s:+d}）",
          f"  ★密度: {1000 * na_s / da_s:.3f}‰ → {1000 * nb_s / db_s:.3f}‰"
          f"（{1000 * nb_s / db_s - 1000 * na_s / da_s:+.3f}‰）", ""]
    na_f = sum(len(_det_rows(ga[k], det)) for k in flip)
    nb_f = sum(len(_det_rows(gb[k], det)) for k in flip)
    da_f = sum(ga[k]["n_dec"] for k in flip)
    db_f = sum(gb[k]["n_dec"] for k in flip)
    if flip:
        L += [f"  （参考）flip した局だけ: {det} {na_f}→{nb_f}"
              f"／決定 {da_f}→{db_f}"
              f"／密度 {1000 * na_f / da_f:.3f}‰ → {1000 * nb_f / db_f:.3f}‰", ""]

    # ---- 3. 共通ループだけ -------------------------------------------------
    ca = cb = dca = dcb = 0
    for k in ga:
        if k not in gb:
            continue
        lim = min(ga[k]["last_loop"], gb[k]["last_loop"])
        ca += sum(1 for r in _det_rows(ga[k], det) if r["loop"] <= lim)
        cb += sum(1 for r in _det_rows(gb[k], det) if r["loop"] <= lim)
        dca += sum(v for lp, v in ga[k]["dec_by_loop"].items() if int(lp) <= lim)
        dcb += sum(v for lp, v in gb[k]["dec_by_loop"].items() if int(lp) <= lim)
    L += ["## 3. 共通ループだけ（両条件がともに到達した loop 番号に限る）",
          f"  {det}: {ca} → {cb}（{cb - ca:+d}）",
          f"  mm 決定数: {dca} → {dcb}（{dcb - dca:+d}）",
          f"  ★密度: {1000 * ca / dca:.3f}‰ → {1000 * cb / dcb:.3f}‰"
          f"（{1000 * cb / dcb - 1000 * ca / dca:+.3f}‰）", ""]

    # ---- 4. 全数表（局ごとの増減） -----------------------------------------
    L += [f"## 4. 局ごとの {det} 増減（0 でない局のみ・全数）",
          f"  {'局':<22}{'flip':>5}{det + '(cur→b)':>12}{'決定(cur→b)':>14}"]
    for k in sorted(ga, key=lambda x: (x[0], x[1])):
        if k not in gb:
            continue
        x, y = len(_det_rows(ga[k], det)), len(_det_rows(gb[k], det))
        if x == y:
            continue
        dec = f"{ga[k]['n_dec']}→{gb[k]['n_dec']}"
        L.append(f"  {k[0] + '#' + str(k[1]):<22}"
                 f"{('Y' if k in flip else '-'):>5}"
                 f"{f'{x}→{y}':>12}{dec:>14}")
    L.append("")

    # ---- 5. 増えた席の全数列挙（b にだけ在る違反行） ------------------------
    def _sig(r):
        return (r["loop"], r["day"], r["decision"], r["card"], r["target"],
                r["kind"])
    L += [f"## 5. b40 側にだけ在る {det} 違反手の全数（局・ループ・日・席）"]
    n_only = 0
    on_alt = 0
    fired = 0
    for k in sorted(ga, key=lambda x: (x[0], x[1])):
        if k not in gb:
            continue
        ca_ = Counter(_sig(r) for r in _det_rows(ga[k], det))
        cb_ = Counter(_sig(r) for r in _det_rows(gb[k], det))
        only = cb_ - ca_
        if not only:
            continue
        for sig, n in sorted(only.items()):
            n_only += n
            rr = next(r for r in _det_rows(gb[k], det) if _sig(r) == sig)
            on_alt += n if rr["on_alt"] else 0
            fired += n if rr["b286_fired"] else 0
            L.append(f"  {k[0]}#{k[1]} L{sig[0]}D{sig[1]} {sig[2]}"
                     f" {sig[3]}→{sig[4]}({sig[5]}) ×{n}"
                     f"  [alt対象={'Y' if rr['on_alt'] else 'N'}"
                     f" 減点発火={'Y' if rr['b286_fired'] else 'N'}]"
                     f"  {rr['reason'][:60]}")
    L += ["",
          f"  ★b40 側にだけ在る手 = {n_only}"
          f"（うち **alt_lines を対象にした手 = {on_alt}**"
          f"／減点が発火していた決定 = {fired}）", ""]

    # ---- 6. cur 側にだけ在る違反行（消えた分） -----------------------------
    n_gone = 0
    L += [f"## 6. cur 側にだけ在る {det} 違反手の総数（b40 で消えた分）"]
    for k in sorted(ga, key=lambda x: (x[0], x[1])):
        if k not in gb:
            continue
        ca_ = Counter(_sig(r) for r in _det_rows(ga[k], det))
        cb_ = Counter(_sig(r) for r in _det_rows(gb[k], det))
        n_gone += sum((ca_ - cb_).values())
    L += [f"  = {n_gone}（差引き {n_only - n_gone:+d} が全体の増分）", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-289 番人 D1/D4 の全数検死")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--mode", type=str, default="cur", choices=tuple(_MODES))
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--diff", nargs=2, metavar=("A.json", "B.json"), default=None)
    ap.add_argument("--det", type=str, default="D1")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.diff:
        with open(args.diff[0], encoding="utf-8") as f:
            ra = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            rb = json.load(f)
        for det in args.det.split(","):
            print(diff(ra, rb, det))
        return
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED 未固定。測定は PYTHONHASHSEED=0 で。", file=sys.stderr)
    rep = sweep(days=args.days, perm=args.perm, mode=args.mode,
                verbose=not args.quiet)
    n = Counter(r["det"] for g in rep["games"] for r in g["rows"])
    print(f"# {args.days}日級 perm={args.perm} mode={args.mode}: "
          f"{dict(sorted(n.items()))} 決定{sum(g['n_dec'] for g in rep['games'])}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, default=list)
        print(f"→ {args.json}")


if __name__ == "__main__":
    main()
