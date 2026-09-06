# -*- coding: utf-8 -*-
"""B-287〔ループ間反復〕の計測ハーネス（計測のみ・本番経路は無変更）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない
（読むだけ）。対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う＝ラッパを
挟まない＝ベンチと**同一の乱数消費**が構造的に保証される（先例＝`arena/b279_probe.py`／
`arena/b282_classify.py`）。

------------------------------------------------------------------------------
測るもの（発注＝FableA・B-287 計測フェーズ）
------------------------------------------------------------------------------
**計測1（現状の残数）**＝「ループNで折られた線へループN+1 で再置した席」。
  「折られた線」の判定＝**b286 と同じ内容ベース同定**＝`agents.heuristic.b286_line_signals`
  を（検出定義を固定するため）`loop_memory=False` 明示・`loop=N` 上書きで呼ぶ
  ＝同一対象への直撃 **B286_BREAK_MIN(=2) 回以上**（閾値はクラス属性経由＝定数直読みしない）。
  再置＝ループN+1 の脚本家の伏せ札（暗躍+1/+2）で (target_kind, target) が一致する席。
  ★併記＝B-279(c) の定義（「前ループの**同じ日**に暗躍禁止で潰された板へ同じ暗躍札を再置」
  ＝361席の物差し）も同じ対局から数える（定義源＝`arena/b279_probe.py` rows_c。
  B-279(c) は直撃**1回**でも数える＝b286 系の「直撃2回以上」より広い）。

**計測2（機会の現存率）**＝計測1の再置席のうち、
  (i) **そのループ開始時点**（＝そのループ最初の脚本家 set_card 決定の view）で
  (ii) その席の決定時点で
  乗り換え先（`alt_lines`＝b286 が _analyze で作るものを**そのまま**読む）が非空だった割合。

**第2層**＝脚本家が取ったループ総数 Σ(min(ltw,9)−1)・平均 loops_to_win・防衛数（参考値。
  ★defense は脚本家側の非退行ゲートに使わない＝§72-104 ユーザー裁定）。

**番人（取引を隠さない）**＝
  (a) 勝利条件に寄与しないボード暗躍の席数（複線演出143席の物差し＝`arena/b279_probe.py`
      の分類をそのまま使う）と、mm_lint D1〜D6 の違反手数（`arena.mm_lint.lint_move`）を
      **同じ対局から**数える（別掃引を増やさない＝測定汚染を避ける）。

------------------------------------------------------------------------------
キー（規約＝監査道具の欠陥4族への対策）
------------------------------------------------------------------------------
- 行キー＝(days, script, seed, loop, day, 決定index)＝全次元・一意（sweep で番人 assert）。
- 同定は内容ベース（`b286_line_signals`／`b282_classify._anyaku_seats` のスナップショット
  前後差）＝行番号キー・実行時キャッシュ・定数直読みを使わない。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b287_probe \
        --days 3 --perm id --mode off --json out.json
    ... --mode on            # B287_LOOP_MEMORY=True の対照
    ... --diff off.json on.json   # 2つの JSON の突き合わせ（flip 全数）
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
    from arena.b282_classify import _anyaku_seats
    from arena.b279_probe import _classify_board, _is_board_anyaku
    from arena.mm_lint import lint_move
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    mm_recs = [r for r in log if r["actor"] == "mastermind"
               and r["decision"] in ("set_card", "mastermind_ability")]
    set_recs = [(i, r) for i, r in enumerate(mm_recs)
                if r["decision"] == "set_card"]

    attr_mm = HeuristicMastermind(seed)   # 帰属用（_analyze は純関数・rng 不使用）

    # 無効化席（内容ベース＝b282 の同一関数・最終 state から遅延構築）
    _null_rows: dict[int, list[dict]] = {}

    def _nullified(lp: int, day: int, tgt: str, kind: str) -> bool:
        rows = _null_rows.setdefault(lp, _anyaku_seats(state, lp))
        return any(s["day"] == day and s["target"] == tgt
                   and s["kind"] == kind and s["nullified"] for s in rows)

    # そのループ最初の脚本家 set_card の view（＝「ループ開始時点」の代理。
    # D1 の第1席＝カウンター全除去後・置き札ゼロの時点）
    first_view: dict[int, dict] = {}
    for _i, r in set_recs:
        first_view.setdefault(r["loop"], r["view"])

    def _alt_lines(view: dict):
        """b286 が _analyze に載せる乗り換え先（alt_lines）をそのまま読む。"""
        a = attr_mm._analyze(view)
        b = a.get("b286")
        return sorted(b["alt_lines"]) if b else None

    _alt_start_cache: dict[int, list | None] = {}

    def _alt_at_loop_start(lp: int):
        if lp not in _alt_start_cache:
            v = first_view.get(lp)
            _alt_start_cache[lp] = _alt_lines(v) if v is not None else None
        return _alt_start_cache[lp]

    # -- 計測1＋計測2：前ループで折られた線への再置席 ------------------------
    _broken_cache: dict[int, set] = {}

    def _broken_of_loop(view: dict, lp: int) -> set:
        """ループ lp 単独の broken（検出定義の固定＝loop_memory=False 明示）。"""
        if lp not in _broken_cache:
            _broken_cache[lp] = set(b286_line_signals(
                {**view, "loop": lp}, loop_memory=False)["broken_seats"])
        return _broken_cache[lp]

    rows_r = []
    for i, r in set_recs:
        ch, lp = r["chosen"], r["loop"]
        if lp < 2 or ch.get("card") not in _ANYAKU_CARDS:
            continue
        view = r["view"]
        seat = (ch.get("target_kind"), ch.get("target"))
        prev = _broken_of_loop(view, lp - 1)
        union_earlier = set().union(*(_broken_of_loop(view, k)
                                      for k in range(1, lp))) if lp > 1 else set()
        if seat not in union_earlier:
            continue
        rows_r.append({
            "key": (days, name, seed, lp, r["day"], i),
            "loop": lp, "day": r["day"], "card": ch["card"],
            "target": ch["target"], "kind": ch["target_kind"],
            "from_prev_loop": seat in prev,          # ★主計＝直前ループ N→N+1
            "blocked_again": _nullified(lp, r["day"], ch["target"],
                                        ch["target_kind"]),
            "alt_at_start": _alt_at_loop_start(lp),  # 計測2(i)
            "alt_at_seat": _alt_lines(view),         # 計測2(ii)
        })

    # -- B-279(c) の物差し（361席の定義＝b279_probe rows_c と同一・継続比較用） --
    by_turn: dict[tuple[int, int], list[tuple]] = {}
    for _i, r in set_recs:
        ch = r["chosen"]
        by_turn.setdefault((r["loop"], r["day"]), []).append(
            (ch["card"], ch["target"], ch["target_kind"]))
    kinshi: dict[tuple[int, int], set[str]] = {}
    for ev in state.history:
        if ev.get("event") != "cards_revealed":
            continue
        k = (ev["loop"], ev["day"])
        for p in ev.get("placements", ()):
            if (p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止"
                    and p.get("target_kind") == "board"):
                kinshi.setdefault(k, set()).add(p["target"])
    c279_repeat_after_block = 0
    c279_blocked_again = 0
    for (lp, day), seats in sorted(by_turn.items()):
        prev_seats = by_turn.get((lp - 1, day))
        if prev_seats is None:
            continue
        prev_ms = Counter(prev_seats)
        for s in seats:
            rep = (prev_ms.get(s, 0) > 0 and s[2] == "board"
                   and s[0] in _ANYAKU_CARDS
                   and s[1] in kinshi.get((lp - 1, day), set()))
            if rep:
                c279_repeat_after_block += 1
                if s[1] in kinshi.get((lp, day), set()):
                    c279_blocked_again += 1

    # -- 番人 (a)：勝利条件に寄与しないボード暗躍（b279 の分類をそのまま） ----
    n_board_anyaku = 0
    n_no_contrib = 0
    n_designed_decoy = 0
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
            "replace_rows": rows_r,
            "c279_repeat_after_block": c279_repeat_after_block,
            "c279_blocked_again": c279_blocked_again,
            "guard_a": {"board_anyaku": n_board_anyaku,
                        "no_contribution": n_no_contrib,
                        "designed_decoy": n_designed_decoy},
            "lint": dict(lint)}


# ---------------------------------------------------------------------------
# 掃引（perm＝検問3。mode＝B-287 の切替口の組）
# ---------------------------------------------------------------------------
# ★第2陣（§72-118 ユーザー裁定）＝3形の対照。いずれも LOOP_MEMORY=True の下で
#   (a)=B287_DESCEND_FEASIBLE／(b)=B287_SPARE_TRUMP を切り替える。
# ★§72-119 の 5日級限定 ON（B287_MIN_DAYS=5・既定 True）後も第1陣/第2陣の
#   コマンドを bit 再現できるよう、ON 系モードは MIN_DAYS を 0 に倒して測る
#   （＝日数ゲート無し＝land 前の計測条件そのもの）。"off" は3口とも False＝
#   全 OFF ベースライン（現行既定とは違う＝比較の物差しを変えないため）。
_MODES = {
    "off": {"B287_LOOP_MEMORY": False, "B287_DESCEND_FEASIBLE": False,
            "B287_SPARE_TRUMP": False, "B287_MIN_DAYS": 5},
    "on":  {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": False,
            "B287_SPARE_TRUMP": False, "B287_MIN_DAYS": 0},   # 第1陣の現形
    "a":   {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
            "B287_SPARE_TRUMP": False, "B287_MIN_DAYS": 0},
    "b":   {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": False,
            "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0},
    "ab":  {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
            "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0},
    # ★第3陣（B287_SPARE_VALUE_CHECK＝(b) 温存述語の狭め）＝b/ab に重ねた2形
    "bv":  {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": False,
            "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0,
            "B287_SPARE_VALUE_CHECK": True},
    "abv": {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
            "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0,
            "B287_SPARE_VALUE_CHECK": True},
    # ★4日級の適用域計測（§72-120 の後続＝`docs/提案_4日級ベンチ_2026-08-31.md` 案A）。
    #   既定値（agents/ 側）は一切書き換えず、ここのクラス属性上書きだけで対照を作る。
    #   cur5 = 現行既定そのもの（3口 True・MIN_DAYS=5）＝4日級では日数ゲートが閉＝不発。
    #          ∴ 4日級では "off"（3口 False）と bit 一致するはず（ゲートの番人＝実測で確認する）。
    #   on4  = 日数ゲートだけを 4 に広げた対照（3口 True・MIN_DAYS=4）。
    "cur5": {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
             "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 5},
    "on4":  {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
             "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 4},
    # ★B-288〔「降り」の席側の会計〕（2026-09-01・計測フェーズ）＝(ab) の上に
    #   `B288_SEAT_COMPLETION_GUARD` を重ねた3形。述語の違いは `B288_GUARD_MODE`。
    #   abg  = "prev"（直前ループ終了時にその板が ≥2 だった席は B-287 を掛けない）
    #   abge = "ever"（過去いずれかのループで ≥2）
    #   abgs = "static"（★対照＝発注どおりの静的会計＝母集団の 100% が通る＝
    #          off と bit 一致するはずの検問。負の結果の物証）
    "abg":  {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
             "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0,
             "B288_SEAT_COMPLETION_GUARD": True, "B288_GUARD_MODE": "prev"},
    "abge": {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
             "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0,
             "B288_SEAT_COMPLETION_GUARD": True, "B288_GUARD_MODE": "ever"},
    "abgs": {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
             "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 0,
             "B288_SEAT_COMPLETION_GUARD": True, "B288_GUARD_MODE": "static"},
    # 4日級（日数ゲートを 4 へ）／5日級（現行既定＝MIN_DAYS 5）に guard を重ねた形
    "on4g": {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
             "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 4,
             "B288_SEAT_COMPLETION_GUARD": True, "B288_GUARD_MODE": "prev"},
    "cur5g": {"B287_LOOP_MEMORY": True, "B287_DESCEND_FEASIBLE": True,
              "B287_SPARE_TRUMP": True, "B287_MIN_DAYS": 5,
              "B288_SEAT_COMPLETION_GUARD": True, "B288_GUARD_MODE": "prev"},
}
_MODE_FLAGS = ("B287_LOOP_MEMORY", "B287_DESCEND_FEASIBLE",
               "B287_SPARE_TRUMP", "B287_MIN_DAYS", "B287_SPARE_VALUE_CHECK",
               "B288_SEAT_COMPLETION_GUARD", "B288_GUARD_MODE")
#: 各切替口の「そのモードで指定が無いときの値」＝既存モードの挙動を bit で保つための既定。
#  ★`B288_GUARD_MODE` は文字列＝一律 False で埋めると述語が壊れるのでここで分ける。
_MODE_DEFAULTS = {"B288_GUARD_MODE": "prev"}


def sweep(days: int = 3, loops: int = 8, perm: str = "id", mode: str = "off",
          verbose: bool = True) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f in _MODE_FLAGS:
            setattr(HM, f, _MODES[mode].get(f, _MODE_DEFAULTS.get(f, False)))
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days)
            games.append(g)
            if verbose:
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                      f"再置{len(g['replace_rows'])}", flush=True)
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    # ★一意性の番人（結合キーの次元不足の検出）
    keys = [tuple(r["key"]) for g in games for r in g["replace_rows"]]
    assert len(keys) == len(set(keys)), "再置席の行キーが一意でない"
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "b287_mode": mode,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


def summarize(rep: dict) -> str:
    games = rep["games"]
    rows = [r for g in games for r in g["replace_rows"]]
    prev_rows = [r for r in rows if r["from_prev_loop"]]
    oc = Counter(g["outcome"] for g in games)
    mean = round(sum(g["loops_to_win"] for g in games) / len(games), 3)
    mm_total = sum(min(g["loops_to_win"], 9) - 1 for g in games)
    guard_a = Counter()
    lint = Counter()
    for g in games:
        guard_a.update(g["guard_a"])
        lint.update(g["lint"])
    c279 = sum(g["c279_repeat_after_block"] for g in games)
    c279b = sum(g["c279_blocked_again"] for g in games)

    def _alt_frac(key):
        n = sum(1 for r in prev_rows if r[key])            # 非空（None も偽）
        return f"{n}/{len(prev_rows)}" + (
            f" ({100 * n / len(prev_rows):.1f}%)" if prev_rows else "")

    L = [f"# B-287 計測（{rep['n_games']}局・{rep['days']}日級・perm={rep['perm']}・"
         f"B287={rep['b287_mode']}・PYTHONHASHSEED={rep['hashseed']}）",
         f"照合: 結末 {dict(sorted(oc.items()))} ／ 平均ループ {mean}",
         "",
         f"## 計測1 前ループで折られた線（b286 同定・直撃{_break_min()}回以上）への再置席",
         f"  - ★直前ループ N→N+1 の再置 = {len(prev_rows)}"
         f"（それ以前のループ由来も含む累積 = {len(rows)}）",
         f"  - うち再置がまた無効化された（blocked_again）= "
         f"{sum(1 for r in prev_rows if r['blocked_again'])}",
         f"  - 内訳（kind別）: " + "  ".join(
             f"{k}:{n}" for k, n in Counter(
                 r['kind'] for r in prev_rows).most_common()),
         f"  - 内訳（板/対象別）: " + "  ".join(
             f"{t}:{n}" for t, n in Counter(
                 r['target'] for r in prev_rows).most_common(8)),
         "",
         f"## 計測2 乗り換え先（alt_lines）の現存率（分母＝N→N+1 再置席）",
         f"  - (i) そのループ開始時点で非空 = {_alt_frac('alt_at_start')}",
         f"  - (ii) その席の決定時点で非空 = {_alt_frac('alt_at_seat')}",
         "",
         f"## B-279(c) の物差し（同日・直撃1回・板のみ＝361席の定義）",
         f"  - 前ループ同日に潰された板へ同じ札を再置 = {c279}"
         f" ／ うち再び守られた = {c279b}",
         "",
         f"## 第2層（従）",
         f"  - 脚本家が取ったループ総数 Σ(min(ltw,9)−1) = {mm_total}",
         f"  - 平均 loops_to_win = {mean} ／ 防衛数（参考値・ゲート不使用）= "
         f"{oc.get('defense', 0)}",
         "",
         f"## 番人（取引を隠さない）",
         f"  - (a) ボード暗躍 {guard_a['board_anyaku']} 席中 寄与しない "
         f"{guard_a['no_contribution']}"
         f"（うち設計上のダミー配置 {guard_a['designed_decoy']}）",
         f"  - mm_lint: " + ("  ".join(
             f"{d}:{lint.get(d, 0)}" for d in ("D1", "D2", "D3", "D4", "D5", "D6"))),
         ]
    return "\n".join(L)


def _break_min() -> int:
    from agents.heuristic import HeuristicMastermind as HM
    return HM.B286_BREAK_MIN


# ---------------------------------------------------------------------------
# OFF/ON の突き合わせ（flip 全数・第1層の前後）
# ---------------------------------------------------------------------------
def diff(rep_off: dict, rep_on: dict) -> str:
    off = {(g["script"], g["seed"]): g for g in rep_off["games"]}
    L = [f"# B-287 diff（{rep_off['days']}日級 perm={rep_off['perm']}）",
         f"OFF: 再置 {sum(len(g['replace_rows']) for g in rep_off['games'])} → "
         f"ON: {sum(len(g['replace_rows']) for g in rep_on['games'])}（累積定義）"]
    for g in rep_on["games"]:
        k = (g["script"], g["seed"])
        o = off.get(k)
        if o and (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"],
                                                       g["outcome"]):
            L.append(f"  flip {k[0]}#{k[1]}: {o['loops_to_win']}({o['outcome']})"
                     f" → {g['loops_to_win']}({g['outcome']})")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-287 計測")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--mode", type=str, default="off",
                    choices=tuple(_MODES))
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--diff", nargs=2, metavar=("OFF.json", "ON.json"),
                    default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.diff:
        with open(args.diff[0], encoding="utf-8") as f:
            rep_off = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            rep_on = json.load(f)
        print(diff(rep_off, rep_on))
        return
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED 未固定。測定は PYTHONHASHSEED=0 で。",
              file=sys.stderr)
    rep = sweep(days=args.days, loops=args.loops, perm=args.perm,
                mode=args.mode, verbose=not args.quiet)
    print(summarize(rep))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, default=list)
        print(f"→ {args.json}")


if __name__ == "__main__":
    main()
