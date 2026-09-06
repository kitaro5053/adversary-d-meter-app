# -*- coding: utf-8 -*-
"""B-293〔動けない対象への移動札を候補から外す〕の計測ハーネス（計測のみ）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない
（読むだけ）。対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う＝ラッパを
挟まない＝ベンチと**同一の乱数消費**が構造的に保証される
（先例＝`arena/b291_probe.py`／`arena/b287_probe.py`／`arena/b285_probe.py`）。

------------------------------------------------------------------------------
測るもの（発注＝FableA・`docs/作業計画_Fable5.1期間_2026-09-02.md` §5）
------------------------------------------------------------------------------
★**第1層（主）＝「規則上いっさい動かない移動札が置かれた席」の全数**（→ ON で 0 が期待値）
  2つの定義を併記する（述語を狭めた経緯＝`tests/test_b293.py` の docstring）：
  - `void_immobile`（**狭い＝land 候補**）＝対象の**現在地以外の3エリアが全部禁止**
    ＝どの合成でも留まる＝KB が一意に「盤面を動かさない」と決める席。
  - `void_dest`（**広い＝発注書原文・計測専用**）＝その札**単独の**行き先が禁止エリア。
    合成すると盤面が動きうる＝空振りとは言えない席を含む（＝狭い版の上位集合）。
  併記＝`blocked_mm`＝実際に `move_blocked`（`sim/flow.py`）が起きた席のうち
  脚本家の移動札が乗っていたもの＝**実戦で観測された物証と同じ数え方**。

第2層＝`Σ(min(ltw,9)−1)`（脚本家が取ったループ総数）／平均 ltw／防衛数
（★参考値＝脚本家側のゲートには使わない＝§72-104 ユーザー裁定）。
番人＝複線演出の非decoy（`arena/b279_probe.py` の分類）／`mm_lint` D1〜D6
（いずれも**同じ対局から**数える＝別掃引を増やさない）。

------------------------------------------------------------------------------
キー（規約＝監査道具の欠陥4族への対策）
------------------------------------------------------------------------------
- 行キー＝(days, script, seed, loop, day, 種別, index)＝**全次元・一意**（sweep で assert）。
- 同定は内容ベース＝`sim.state.current_forbidden_from_view` と
  `agents.heuristic._move_dest` を**そのまま**引く（禁止エリアを probe 側で再実装しない）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b293_probe \
        --days 5 --perm id --mode off --json out.json
    ... --mode on     # B293_SKIP_IMMOBILE_MOVE=True（狭い＝land 候補）
    ... --mode onb    # B293_SKIP_BLOCKED_DEST=True（広い＝発注書原文・計測専用）
    ... --diff off.json on.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace


# ---------------------------------------------------------------------------
# 席の分類（AI 側の単一ソースをそのまま引く）
# ---------------------------------------------------------------------------
def _void_kinds(view: dict, chosen: dict) -> tuple[bool, bool]:
    """(狭い版で空振り, 広い版で空振り) を返す。移動札×キャラ以外は (False, False)。"""
    from agents.heuristic import _move_dest
    from engine.models import MOVE_CARDS
    from sim.state import current_forbidden_from_view
    if (chosen.get("target_kind") != "character"
            or chosen.get("card") not in MOVE_CARDS):
        return False, False
    tgt = chosen.get("target")
    area = next((c.get("area") for c in (view.get("characters") or ())
                 if c.get("name") == tgt), None)
    if not area:
        return False, False
    forb = current_forbidden_from_view(view, tgt)
    if not forb:
        return False, False
    narrow = all(_move_dest(area, c) in forb for c in MOVE_CARDS)
    broad = _move_dest(area, chosen["card"]) in forb
    return narrow, broad


# ---------------------------------------------------------------------------
# 1局の走査
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int = 8) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.b279_probe import _classify_board, _is_board_anyaku
    from arena.mm_lint import lint_move
    from engine.models import MOVE_CARDS
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

    # -- 第1層＝脚本家が選んだ set_card 1件ごとの分類 -------------------------
    seat_rows = []
    for i_r, r in enumerate(set_recs):
        narrow, broad = _void_kinds(r["view"], r["chosen"])
        if not broad and not narrow:
            continue
        # ★併記＝その席の素点（`_score_set`）。狭い版で外れる席が本当に filler 帯
        #   （`set_move_stray`=3.0 以下）だけかを実測で確かめる＝「積極的な移動の枝を
        #   壊していない」の物証（静的読みだけで済ませない）。
        _a = attr_mm._analyze(r["view"])
        seat_rows.append({
            "key": (days, name, seed, r["loop"], r["day"], "void", i_r),
            "loop": r["loop"], "day": r["day"],
            "card": r["chosen"].get("card"), "target": r["chosen"].get("target"),
            "narrow": narrow, "broad": broad,
            "score": attr_mm._score_set(r["chosen"], _a, r["view"]),
        })

    # -- 併記＝実際に `move_blocked` が起きた席（実戦の物証と同じ数え方） -------
    blocked_any = blocked_mm = 0
    last_pls: tuple = ()
    for ev in state.history:
        if ev.get("event") == "cards_revealed":
            last_pls = tuple(ev.get("placements") or ())
        elif ev.get("event") == "move_blocked":
            blocked_any += 1
            if any(p.get("owner") == "mastermind"
                   and p.get("target_kind") == "character"
                   and p.get("target") == ev.get("name")
                   and p.get("card") in MOVE_CARDS for p in last_pls):
                blocked_mm += 1

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
            "n_set": len(set_recs),
            "seat_rows": seat_rows,
            "blocked_any": blocked_any, "blocked_mm": blocked_mm,
            "guard_a": {"board_anyaku": n_board_anyaku,
                        "no_contribution": n_no_contrib,
                        "designed_decoy": n_designed_decoy},
            "lint": dict(lint)}


# ---------------------------------------------------------------------------
# 掃引（perm＝検問3。mode＝B-293 の切替口の組）
# ---------------------------------------------------------------------------
#: ★2026-09-03 の land（既定 ON）以降も**計測の意味が変わらない**よう、各 mode は
#  切替口を**明示的に**両方セットする（クラス既定に依存しない＝過去の測定値と比較可能）。
#  `def` だけは何もセットしない＝**そのときのクラス既定**を測る（land 後の抜き取り検算用）。
_MODES = {
    "off": {"B293_SKIP_IMMOBILE_MOVE": False,       # B-293 以前（land 前の既定）
            "B293_SKIP_BLOCKED_DEST": False},
    "on":  {"B293_SKIP_IMMOBILE_MOVE": True,        # 狭い＝land 済み（現在の既定）
            "B293_SKIP_BLOCKED_DEST": False},
    "onb": {"B293_SKIP_IMMOBILE_MOVE": False,       # 広い＝発注書原文・計測専用
            "B293_SKIP_BLOCKED_DEST": True},
    "def": {},                                      # ★クラス既定そのまま（検算用）
}
_MODE_FLAGS = ("B293_SKIP_IMMOBILE_MOVE", "B293_SKIP_BLOCKED_DEST")


def sweep(days: int = 5, loops: int = 8, perm: str = "id", mode: str = "off",
          verbose: bool = True) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f, v in _MODES[mode].items():
            setattr(HM, f, v)
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days)
            games.append(g)
            if verbose:
                nv = sum(1 for r in g["seat_rows"] if r["narrow"])
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                      f"空振り(狭){nv} 空振り(広){len(g['seat_rows'])}", flush=True)
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    # ★一意性の番人（結合キーの次元不足の検出）
    keys = [tuple(r["key"]) for g in games for r in g["seat_rows"]]
    assert len(keys) == len(set(keys)), "行キーが一意でない"
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "mode": mode,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


def _agg(rep: dict) -> dict:
    games = rep["games"]
    rows = [r for g in games for r in g["seat_rows"]]
    oc = Counter(g["outcome"] for g in games)
    guard_a, lint = Counter(), Counter()
    for g in games:
        guard_a.update(g["guard_a"])
        lint.update(g["lint"])
    return {
        "sets": sum(g["n_set"] for g in games),
        "void_immobile": sum(1 for r in rows if r["narrow"]),
        "void_dest": sum(1 for r in rows if r["broad"]),
        "blocked_any": sum(g["blocked_any"] for g in games),
        "blocked_mm": sum(g["blocked_mm"] for g in games),
        "targets": dict(sorted(Counter(r["target"] for r in rows
                                       if r["narrow"]).items())),
        "score_max_narrow": max([r["score"] for r in rows if r["narrow"]],
                                default=None),
        "score_hist": dict(sorted(Counter(r["score"] for r in rows
                                          if r["narrow"]).items())),
        "targets_broad": dict(sorted(Counter(
            r["target"] for r in rows if r["broad"] and not r["narrow"]).items())),
        "mm_total": sum(min(g["loops_to_win"], 9) - 1 for g in games),
        "mean": round(sum(g["loops_to_win"] for g in games) / len(games), 3),
        "outcome": dict(sorted(oc.items())),
        "guard_a": dict(guard_a),
        "lint": {d: lint.get(d, 0) for d in
                 ("D1", "D2", "D3", "D4", "D5", "D6")},
    }


def summarize(rep: dict) -> str:
    s = _agg(rep)
    return "\n".join([
        f"# B-293 計測（{rep['n_games']}局・{rep['days']}日級・perm={rep['perm']}・"
        f"mode={rep['mode']}・PYTHONHASHSEED={rep['hashseed']}）",
        f"照合: 結末 {s['outcome']} ／ 平均ループ {s['mean']}",
        "",
        f"## 第1層（主）＝空振りの移動札が置かれた席（脚本家の set_card 総数 {s['sets']}）",
        f"  - ★狭い版（完全不動＝KB 一意）= {s['void_immobile']}  内訳 {s['targets']}"
        f"  素点の分布 {s['score_hist']}（最大 {s['score_max_narrow']}）",
        f"  - 広い版（札単独の行き先が禁止）= {s['void_dest']}"
        f"  うち狭い版に含まれない分の内訳 {s['targets_broad']}",
        f"  - 併記 `move_blocked` 総数 {s['blocked_any']}"
        f"（うち脚本家の移動札が乗っていた {s['blocked_mm']}）",
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
    L = [f"# B-293 diff（{rep_a['days']}日級 perm={rep_a['perm']}）"
         f" {rep_a['mode']} → {rep_b['mode']}"]
    for k in ("void_immobile", "void_dest", "blocked_mm", "blocked_any",
              "mm_total"):
        L.append(f"  {k}: {a[k]} → {b[k]}  (Δ{b[k] - a[k]:+d})")
    L.append(f"  防衛数(参考): {a['outcome'].get('defense', 0)} → "
             f"{b['outcome'].get('defense', 0)} ／ 平均 {a['mean']} → {b['mean']}")
    L.append(f"  番人 非decoy: {a['guard_a']['no_contribution']} → "
             f"{b['guard_a']['no_contribution']} ／ lint {a['lint']} → {b['lint']}")
    for g in rep_b["games"]:
        o = off.get((g["script"], g["seed"]))
        if o and (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"],
                                                      g["outcome"]):
            L.append(f"  flip {g['script']}#{g['seed']}: "
                     f"{o['loops_to_win']}({o['outcome']}) → "
                     f"{g['loops_to_win']}({g['outcome']})  "
                     f"Δ第2層 {min(g['loops_to_win'], 9) - min(o['loops_to_win'], 9):+d}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-293 計測")
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
