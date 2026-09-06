# -*- coding: utf-8 -*-
"""B-291〔二正面の数え上げ＋拘束 `暗躍+1` の減点除外〕の計測ハーネス（計測のみ）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない
（読むだけ）。対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う＝ラッパを
挟まない＝ベンチと**同一の乱数消費**が構造的に保証される
（先例＝`arena/b287_probe.py`／`arena/b288_probe.py`／`arena/b279_probe.py`）。

------------------------------------------------------------------------------
測るもの（発注＝FableA・§3 B-291／出典＝§72-129・§1i 二正面ドクトリン）
------------------------------------------------------------------------------
★**第1層(a) 二正面率**＝**同一ターンに2つ以上の「板」へ暗躍札を置いた脚本家ターン**の割合。
  分母＝脚本家の手番（＝`cards_revealed` が立ったターン数＝(loop, day) の異なり数）。
  ★§1i の「正しい二正面」＝バレた板に圧（拘束）を残しつつ本命は別の板へ通す形。
  併記＝席単位（板＋キャラ）の版と、「**折られた板 × それ以外の板**」に分かれた
  ターン数（＝囮と本命が同時に立っている、より厳しい版）。

★**第1層(b) 囮の吸収率**＝主人公の `暗躍禁止` が
  「**囮**＝その決定時点で `broken_seats` に入っていた席」に当たった数 vs
  「**本命**＝broken でない板」に当たった数。
  ★「決定時点」の定義＝**そのターンの脚本家の最初の `set_card` 決定の view**
  （＝ターン頭・当ターンの伏せ札が公開される前）で `b286_line_signals` を引いた結果。
  内容ベース同定（行番号キー・実行時キャッシュ・定数直読みを使わない）。
  併記＝「実吸収」＝同ターンに脚本家の暗躍札が**実際にその席にあった**ものだけ。

第2層＝`Σ(min(ltw,9)−1)`（脚本家が取ったループ総数）／平均 ltw／防衛数（★参考値＝
脚本家側のゲートには使わない＝§72-104 ユーザー裁定）。
番人＝複線演出の非decoy（`arena/b279_probe.py` の分類）／`mm_lint` D1〜D6
（いずれも**同じ対局から**数える＝別掃引を増やさない）。

------------------------------------------------------------------------------
キー（規約＝監査道具の欠陥4族への対策）
------------------------------------------------------------------------------
- 行キー＝(days, script, seed, loop, day, 種別, index)＝**全次元・一意**（sweep で assert）。
- 同定は内容ベース＝`b286_line_signals`（`agents/heuristic.py`）をそのまま引く。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b291_probe \
        --days 5 --perm id --mode off --json out.json
    ... --mode on      # B291_BAIT_EXEMPT=True
    ... --mode n289    # ★対照＝B289_ALT_BONUS=0.0（B-289 が増幅要因かの切り分け）
    ... --mode on289   # B-291 ON かつ B-289 OFF
    ... --diff off.json on.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace

_ANYAKU_CARDS = {"暗躍+1", "暗躍+2"}


# ---------------------------------------------------------------------------
# 1局の走査
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int = 8) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.heuristic import b286_line_signals
    from arena.b279_probe import _classify_board, _is_board_anyaku
    from arena.mm_lint import lint_move
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    mm_recs = [r for r in log if r["actor"] == "mastermind"
               and r["decision"] in ("set_card", "mastermind_ability")]
    set_recs = [r for r in log if r["actor"] == "mastermind"
                and r["decision"] == "set_card"]
    attr_mm = HeuristicMastermind(seed)   # 帰属用（_analyze は純関数・rng 不使用）

    # そのターンの脚本家の最初の set_card の view（＝ターン頭の公開情報）
    turn_view: dict[tuple[int, int], dict] = {}
    for r in set_recs:
        turn_view.setdefault((r["loop"], r["day"]), r["view"])

    _broken_cache: dict[tuple[int, int], frozenset] = {}

    def _broken_at(lp: int, day: int) -> frozenset:
        """ターン頭の `broken_seats`（AI 自身の検出定義をそのまま引く）。"""
        k = (lp, day)
        if k not in _broken_cache:
            v = turn_view.get(k)
            _broken_cache[k] = (frozenset(b286_line_signals(v)["broken_seats"])
                                if v is not None else frozenset())
        return _broken_cache[k]

    # -- 第1層(a) 二正面率 ／ (b) 囮の吸収率（materials＝cards_revealed） -------
    tf_rows = []          # 脚本家ターンごとの二正面の数え上げ
    ab_rows = []          # 暗躍禁止1枚ごとの帰属
    for idx_ev, ev in enumerate(state.history):
        if ev.get("event") != "cards_revealed":
            continue
        lp, day = ev.get("loop"), ev.get("day")
        pls = ev.get("placements", ()) or ()
        broken = _broken_at(lp, day)
        mm_any = [(p.get("target_kind"), p.get("target")) for p in pls
                  if p.get("owner") == "mastermind"
                  and p.get("card") in _ANYAKU_CARDS]
        boards = {t for k, t in mm_any if k == "board"}
        seats = set(mm_any)
        tf_rows.append({
            "key": (days, name, seed, lp, day, "tf", idx_ev),
            "loop": lp, "day": day,
            "n_boards": len(boards), "n_seats": len(seats),
            # ★厳しい版＝囮（折られた板）と本命（それ以外の板）が同時に立っている
            "split": bool(boards & {t for k, t in broken if k == "board"})
                     and bool(boards - {t for k, t in broken if k == "board"}),
        })
        n_kin = sum(1 for p in pls if p.get("owner") != "mastermind"
                    and p.get("card") == "暗躍禁止")
        for i_p, p in enumerate(pls):
            if p.get("owner") == "mastermind" or p.get("card") != "暗躍禁止":
                continue
            seat = (p.get("target_kind"), p.get("target"))
            ab_rows.append({
                "key": (days, name, seed, lp, day, "kin", i_p),
                "loop": lp, "day": day,
                "kind": seat[0], "target": seat[1],
                "decoy": seat in broken,          # ★囮＝ターン頭に broken だった席
                "hit": seat in seats,             # 同ターンに脚本家の暗躍札が実在
                "self_kill": n_kin >= 2,          # 暗躍禁止2枚以上＝自滅（resolver）
            })

    # -- 番人 (a)：勝利条件に寄与しないボード暗躍（b279 の分類をそのまま） ----
    n_board_anyaku = n_no_contrib = n_designed_decoy = 0
    for r in mm_recs:
        if not _is_board_anyaku(r):
            continue
        n_board_anyaku += 1
        view, ch = r["view"], r["chosen"]
        board = ch.get("target")
        if _classify_board(view, board) == "no_contribution":
            n_no_contrib += 1
            a_att = attr_mm._analyze(view)
            if (board == a_att.get("decoy_board")
                    and bool(a_att.get("decoy_funded"))):
                n_designed_decoy += 1

    # -- 番人 mm_lint D1〜D6 ---------------------------------------------------
    lint = Counter()
    for r in mm_recs:
        for det, _reason in lint_move(r["view"], r["chosen"]):
            lint[det] += 1

    # 対局の帰結（ベンチと同じ定義）
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
            "tf_rows": tf_rows, "ab_rows": ab_rows,
            "guard_a": {"board_anyaku": n_board_anyaku,
                        "no_contribution": n_no_contrib,
                        "designed_decoy": n_designed_decoy},
            "lint": dict(lint)}


# ---------------------------------------------------------------------------
# 掃引（perm＝検問3。mode＝B-291／B-289 の切替口の組）
# ---------------------------------------------------------------------------
_MODES = {
    "off":   {},                                    # 現行既定（B-291 OFF・B-289 40）
    "on":    {"B291_BAIT_EXEMPT": True},
    "n289":  {"B289_ALT_BONUS": 0.0},               # ★B-289 が増幅要因かの切り分け
    "on289": {"B291_BAIT_EXEMPT": True, "B289_ALT_BONUS": 0.0},
    # ★A-57 のガードを外した対照（＝折られた席への `暗躍+1` を何枚でも免除）。
    #   ガードが射程をどれだけ削っているかの物差し（land 候補ではない）。
    "ong":   {"B291_BAIT_EXEMPT": True, "B291_BAIT_MAX": 10 ** 9},
}
_MODE_FLAGS = ("B291_BAIT_EXEMPT", "B291_BAIT_MAX", "B289_ALT_BONUS")
#: 指定が無いときの値＝**現行既定そのもの**（agents/ 側を書き換えない）。
_MODE_DEFAULTS = {"B291_BAIT_EXEMPT": False, "B291_BAIT_MAX": 1,
                  "B289_ALT_BONUS": 40.0}


def sweep(days: int = 5, loops: int = 8, perm: str = "id", mode: str = "off",
          verbose: bool = True) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f in _MODE_FLAGS:
            setattr(HM, f, _MODES[mode].get(f, _MODE_DEFAULTS[f]))
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days)
            games.append(g)
            if verbose:
                tf = sum(1 for r in g["tf_rows"] if r["n_boards"] >= 2)
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                      f"二正面{tf}/{len(g['tf_rows'])}", flush=True)
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    # ★一意性の番人（結合キーの次元不足の検出）
    keys = [tuple(r["key"]) for g in games
            for r in (g["tf_rows"] + g["ab_rows"])]
    assert len(keys) == len(set(keys)), "行キーが一意でない"
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "mode": mode,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


def _agg(rep: dict) -> dict:
    games = rep["games"]
    tf = [r for g in games for r in g["tf_rows"]]
    ab = [r for g in games for r in g["ab_rows"]]
    oc = Counter(g["outcome"] for g in games)
    guard_a, lint = Counter(), Counter()
    for g in games:
        guard_a.update(g["guard_a"])
        lint.update(g["lint"])
    return {
        "turns": len(tf),
        "tf_boards": sum(1 for r in tf if r["n_boards"] >= 2),
        "tf_seats": sum(1 for r in tf if r["n_seats"] >= 2),
        "tf_split": sum(1 for r in tf if r["split"]),
        "kin": len(ab),
        "kin_decoy": sum(1 for r in ab if r["decoy"]),
        "kin_main_board": sum(1 for r in ab
                              if not r["decoy"] and r["kind"] == "board"),
        "kin_char": sum(1 for r in ab
                        if not r["decoy"] and r["kind"] == "character"),
        "kin_hit_decoy": sum(1 for r in ab if r["decoy"] and r["hit"]),
        "kin_hit_main": sum(1 for r in ab if not r["decoy"] and r["hit"]),
        "mm_total": sum(min(g["loops_to_win"], 9) - 1 for g in games),
        "mean": round(sum(g["loops_to_win"] for g in games) / len(games), 3),
        "outcome": dict(sorted(oc.items())),
        "guard_a": dict(guard_a),
        "lint": {d: lint.get(d, 0) for d in
                 ("D1", "D2", "D3", "D4", "D5", "D6")},
    }


def summarize(rep: dict) -> str:
    s = _agg(rep)

    def pc(n, d):
        return f"{n}/{d}" + (f" ({100 * n / d:.1f}%)" if d else "")

    return "\n".join([
        f"# B-291 計測（{rep['n_games']}局・{rep['days']}日級・perm={rep['perm']}・"
        f"mode={rep['mode']}・PYTHONHASHSEED={rep['hashseed']}）",
        f"照合: 結末 {s['outcome']} ／ 平均ループ {s['mean']}",
        "",
        "## 第1層(a) 二正面率（分母＝脚本家の手番）",
        f"  - ★板が2つ以上 = {pc(s['tf_boards'], s['turns'])}",
        f"  - 席（板＋キャラ）が2つ以上 = {pc(s['tf_seats'], s['turns'])}",
        f"  - ★囮（折られた板）と本命（それ以外の板）が同時 = "
        f"{pc(s['tf_split'], s['turns'])}",
        "",
        "## 第1層(b) 囮の吸収率（分母＝主人公の暗躍禁止の総数）",
        f"  - ★囮（ターン頭に broken だった席）に当たった = "
        f"{pc(s['kin_decoy'], s['kin'])}",
        f"  - 本命（broken でない板）に当たった = {pc(s['kin_main_board'], s['kin'])}",
        f"  - broken でないキャラ席 = {pc(s['kin_char'], s['kin'])}",
        f"  - 実吸収（同ターンに脚本家の暗躍札が実在）: 囮 {s['kin_hit_decoy']} ／ "
        f"本命 {s['kin_hit_main']}",
        "",
        "## 第2層（従）",
        f"  - 脚本家が取ったループ総数 Σ(min(ltw,9)−1) = {s['mm_total']}",
        f"  - 平均 loops_to_win = {s['mean']} ／ 防衛数（参考値・ゲート不使用）= "
        f"{s['outcome'].get('defense', 0)}",
        "",
        "## 番人（取引を隠さない）",
        f"  - (a) ボード暗躍 {s['guard_a']['board_anyaku']} 席中 寄与しない "
        f"{s['guard_a']['no_contribution']}"
        f"（うち設計上のダミー配置 {s['guard_a']['designed_decoy']}）",
        "  - mm_lint: " + "  ".join(f"{d}:{n}" for d, n in s["lint"].items()),
    ])


def diff(rep_a: dict, rep_b: dict) -> str:
    a, b = _agg(rep_a), _agg(rep_b)
    off = {(g["script"], g["seed"]): g for g in rep_a["games"]}
    L = [f"# B-291 diff（{rep_a['days']}日級 perm={rep_a['perm']}）"
         f" {rep_a['mode']} → {rep_b['mode']}"]
    for k in ("tf_boards", "tf_split", "kin_decoy", "kin_main_board",
              "kin_hit_decoy", "kin_hit_main", "mm_total"):
        L.append(f"  {k}: {a[k]} → {b[k]}  (Δ{b[k] - a[k]:+d})")
    L.append(f"  番人 非decoy: {a['guard_a']['no_contribution']} → "
             f"{b['guard_a']['no_contribution']} ／ lint {a['lint']} → {b['lint']}")
    for g in rep_b["games"]:
        o = off.get((g["script"], g["seed"]))
        if o and (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"],
                                                       g["outcome"]):
            L.append(f"  flip {g['script']}#{g['seed']}: "
                     f"{o['loops_to_win']}({o['outcome']}) → "
                     f"{g['loops_to_win']}({g['outcome']})")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-291 計測")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--mode", default="off", choices=sorted(_MODES))
    ap.add_argument("--json", default=None)
    ap.add_argument("--diff", nargs=2, default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    ns = ap.parse_args(argv)
    if ns.diff:
        a = json.load(open(ns.diff[0], encoding="utf-8"))
        b = json.load(open(ns.diff[1], encoding="utf-8"))
        print(diff(a, b))
        return 0
    rep = sweep(days=ns.days, loops=ns.loops, perm=ns.perm, mode=ns.mode,
                verbose=not ns.quiet)
    if ns.json:
        with open(ns.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
    print(summarize(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
