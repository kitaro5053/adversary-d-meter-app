# -*- coding: utf-8 -*-
"""B-285〔臨界0への冷却の空振り〕の計測ハーネス（計測のみ・本番経路は無変更）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない
（読むだけ。切替口はクラス属性の一時上書きのみ・finally で必ず戻す）。対局は
`sim.run_game` の**素の返り値** `(state, log)` だけを使う＝ラッパを挟まない＝ベンチと
**同一の乱数消費**が構造的に保証される（先例＝`arena/b279_probe.py`／`arena/b287_probe.py`）。

------------------------------------------------------------------------------
測るもの（発注＝FableA・B-285 計測フェーズ／出所＝§72-104 (b) 副次・§72-112）
------------------------------------------------------------------------------
**第1層（主）＝行為の数え上げ**

  `dead_cool` 席＝**抑制モードの冷却のうち対象の不安臨界が0のもの**。
  - 「抑制モードの冷却か」の同定は**内容ベース**＝`sys.settrace` で `_score_set` /
    `_score_ability` が実際に通った `return` 行の**中身**をキーにする
    （行番号キー禁止＝`arena/b279_probe.py` `_trace_score` と同じ道具）。
  - 「不安臨界」は `engine.data.unrest_threshold_of`（KB `rules/30_characters.md` の表の
    単一ソース）を読むだけ＝新しい表を発明しない。

  `whiff` 席（★空振りの厳密な定義＝(a)(b) の両方を満たす席）＝
    (a) 対象の不安臨界が **0**（＝`不安-1` では発生条件(2) を偽にできない。
        KB＝`rules/00_rules_core.md:38,119-122`／`rules/30_characters.md:53,77`）
    (b) その冷却が**その事件の発生を1件も止めていない**
        ＝冷却した日以降・同ループ内でその対象が犯人の予定事件について、
          `state.secret_log` の `incident` イベントを全数照合し、
          **不発になったものが1件も無い**（不発があっても理由が不安由来でないなら
          「止めていない」に数えるが、その旨を別カラムで残す＝嘘をつかない）。

  ★(b) は KB から演繹できる（臨界0は不安で偽にできない）が、**演繹を実測で裏書きする**
  ために secret_log の理由文字列まで突き合わせる（規約§3＝不存在の主張は完全列挙で）。

**機会費用（1行）**＝その席の決定における「chosen 以外の最高点」（`_score_set` を
  純関数として再実行）。chosen の点との差＝その札を他へ回したときに諦めた点数。

**第2層（従）**＝脚本家が取ったループ総数 Σ(min(ltw,9)−1)・平均 `loops_to_win`・
  防衛数（★参考値＝`defense` は脚本家側の非退行ゲートに使わない＝§72-104 ユーザー裁定）。

**番人（取引を隠さない）**＝複線演出の非decoy 席（`arena/b279_probe.py` の分類）と
  `mm_lint` D1〜D6 の違反手数を**同じ対局から**数える（別掃引を増やさない）。

------------------------------------------------------------------------------
キー（規約＝監査道具の欠陥4族への対策）
------------------------------------------------------------------------------
行キー＝(days, script, seed, loop, day, 決定index)＝全次元・一意（sweep で番人 assert）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b285_probe \
        --days 3 --perm id --mode off --json out.json
    ... --mode on      # B285_SKIP_DEAD_COOL=True（プロトタイプ ON）
    ... --mode live    # B285_LIVENESS_BREAK=True（liveness 検査＝巨大値）
    ... --diff off.json on.json    # 2つの JSON の突き合わせ（flip 全数）
"""

from __future__ import annotations

import argparse
import json
import linecache
import os
import sys
from collections import Counter
from dataclasses import replace

from engine.data import unrest_threshold_of

# 抑制モードの冷却の return 行（**中身**をキーにする＝行番号は使わない）
_COOL_BRANCHES = {
    'return self.p["set_cool_locked"]  # 臨界間際の犯人を冷やして事件を不発に':
        "set_cool_locked",
    'return self.p["ab_cool_locked"]': "ab_cool_locked",
}
# liveness モードで置き換わる return 行
_LIVE_BRANCH = "return 1e6            # liveness 検査専用"


def _unrest_delta(rec: dict):
    """脚本家の不安操作を (対象, ±1) に正規化（不安操作でなければ None）。

    定義源＝`arena/b279_probe.py::_unrest_delta`（同一定義＝物差しを変えない）。"""
    ch = rec["chosen"]
    if rec["decision"] == "set_card" and ch.get("target_kind") == "character":
        if ch.get("card") == "不安+1":
            return ch["target"], 1
        if ch.get("card") == "不安-1":
            return ch["target"], -1
    if rec["decision"] == "mastermind_ability":
        if ch.get("kind") == "unrest":
            return ch.get("target"), 1
        if ch.get("kind") == "unrest_minus":
            return ch.get("target"), -1
    return None


def _trace_branch(mm, rec: dict, a=None):
    """chosen の採点を再実行し (return 行の中身, 点) を返す（純関数＝対局を汚さない）。"""
    import agents.heuristic as H
    view = rec["view"]
    if a is None:
        a = mm._analyze(view)
    src_file = H.__file__
    hits: list[int] = []

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != src_file:
            return None
        if event == "call":
            return tracer
        if event == "line":
            hits.append(frame.f_lineno)
        return tracer

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        if rec["decision"] == "set_card":
            val = mm._score_set(rec["chosen"], a, view)
        else:
            val = mm._score_ability(rec["chosen"], a)
    finally:
        sys.settrace(old)
    txt = ""
    for ln in reversed(hits):
        t = linecache.getline(src_file, ln).strip()
        if t.startswith("return"):
            txt = t
            break
    if not txt and hits:
        txt = linecache.getline(src_file, hits[-1]).strip()
    return txt, val


def _second_best(mm, rec: dict, a, view: dict):
    """chosen 以外の最高点（機会費用）。set_card のみ（options が同型の決定）。"""
    if rec["decision"] != "set_card":
        return None
    best = None
    for o in rec["options"]:
        if o == rec["chosen"]:
            continue
        try:
            v = mm._score_set(o, a, view)
        except Exception:       # noqa: BLE001  採点不能は嘘をつかず捨てる
            continue
        if best is None or v > best[0]:
            best = (v, o)
    if best is None:
        return None
    return {"score": best[0], "option": best[1]}


# ---------------------------------------------------------------------------
# 1局の走査
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int = 8,
               with_cost: bool = True) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.b279_probe import _classify_board, _is_board_anyaku
    from arena.mm_lint import lint_move
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    mm_recs = [r for r in log if r["actor"] == "mastermind"
               and r["decision"] in ("set_card", "mastermind_ability")]
    attr_mm = HeuristicMastermind(seed)   # 採点用（_analyze/_score_* は純関数・rng 不使用）

    # 事件の実績（secret_log＝理由つき。公開ログは理由を持たない＝00:127）
    inc_log = [e for e in state.secret_log if e.get("event") == "incident"]

    def _incidents_after(lp: int, day: int, culprit: str):
        return [e for e in inc_log
                if e.get("loop") == lp and e.get("day", 0) >= day
                and e.get("culprit") == culprit]

    # -- 冷却席の全数 --------------------------------------------------------
    cool_rows = []
    for i, r in enumerate(mm_recs):
        um = _unrest_delta(r)
        if not um or um[1] >= 0:
            continue
        tgt, view = um[0], r["view"]
        c = next((x for x in view["characters"] if x["name"] == tgt), None)
        if c is None:
            continue
        th = unrest_threshold_of(tgt)
        a = attr_mm._analyze(view)
        branch, score = _trace_branch(attr_mm, r, a)
        kind = _COOL_BRANCHES.get(branch)
        if branch == _LIVE_BRANCH:
            kind = "liveness"
        upcoming = sorted(inc["day"] for inc in view["incidents"]
                          if inc["culprit"] == tgt and inc["day"] >= view["day"])
        seen = _incidents_after(r["loop"], view["day"], tgt)
        row = {
            "key": (days, name, seed, r["loop"], r["day"], i),
            "script": name, "seed": seed, "loop": r["loop"], "day": r["day"],
            "decision": r["decision"], "target": tgt,
            "unrest": c["unrest"], "th": th, "alive": c["alive"],
            "branch": branch, "kind": kind, "score": score,
            "locked_cool": kind in ("set_cool_locked", "ab_cool_locked", "liveness"),
            "th_zero": th == 0,
            "upcoming_inc_days": upcoming,
            # (b) の物証＝この冷却より後の同ループ事件の実績（理由つき）
            "inc_after": [{"day": e.get("day"), "name": e.get("name"),
                           "occurs": e.get("occurs"),
                           "reasons": e.get("reasons")} for e in seen],
            "rule_y": view.get("rule_y"),
        }
        row["n_inc_after"] = len(seen)
        row["n_inc_stopped"] = sum(1 for e in seen if not e.get("occurs"))
        row["stopped_reasons"] = sorted({str(rs) for e in seen
                                         if not e.get("occurs")
                                         for rs in (e.get("reasons") or ["(理由なし)"])})
        # ★空振り＝(a) 臨界0 ∧ (b) 止めた事件が1件も無い
        row["whiff"] = bool(row["locked_cool"] and th == 0
                            and row["n_inc_stopped"] == 0)
        if with_cost and row["locked_cool"] and th == 0:
            sb = _second_best(attr_mm, r, a, view)
            row["second_best"] = sb
        cool_rows.append(row)

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

    # -- 番人 mm_lint D1〜D6 --------------------------------------------------
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

    # 伏せ札列の指紋（道具が触った局の検出＝b287 と同じ考え方）
    fp = [(r["loop"], r["day"], r["decision"],
           r["chosen"].get("card") or r["chosen"].get("kind"),
           r["chosen"].get("target")) for r in mm_recs]

    return {"script": name, "seed": seed, "days": days,
            "outcome": outcome, "loops_to_win": ltw,
            "cool_rows": cool_rows,
            "fingerprint": fp,
            "guard_a": {"board_anyaku": n_board_anyaku,
                        "no_contribution": n_no_contrib,
                        "designed_decoy": n_designed_decoy},
            "lint": dict(lint)}


# ---------------------------------------------------------------------------
# 掃引（perm＝検問3。mode＝B-285 の切替口の組）
# ---------------------------------------------------------------------------
_MODES = {
    "off":  {"B285_SKIP_DEAD_COOL": False, "B285_LIVENESS_BREAK": False},
    "on":   {"B285_SKIP_DEAD_COOL": True, "B285_LIVENESS_BREAK": False},
    "live": {"B285_SKIP_DEAD_COOL": False, "B285_LIVENESS_BREAK": True},
}
_MODE_FLAGS = ("B285_SKIP_DEAD_COOL", "B285_LIVENESS_BREAK")


def sweep(days: int = 3, loops: int = 8, perm: str = "id", mode: str = "off",
          verbose: bool = True, with_cost: bool = True) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f in _MODE_FLAGS:
            setattr(HM, f, _MODES[mode][f])
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days, loops=loops, with_cost=with_cost)
            games.append(g)
            if verbose:
                nd = sum(1 for r in g["cool_rows"] if r["whiff"])
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                      f"空振り{nd}", flush=True)
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    keys = [tuple(r["key"]) for g in games for r in g["cool_rows"]]
    assert len(keys) == len(set(keys)), "冷却席の行キーが一意でない"
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "b285_mode": mode,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


def summarize(rep: dict) -> str:
    games = rep["games"]
    rows = [r for g in games for r in g["cool_rows"]]
    locked = [r for r in rows if r["locked_cool"]]
    dead = [r for r in locked if r["th_zero"]]
    whiff = [r for r in rows if r["whiff"]]
    oc = Counter(g["outcome"] for g in games)
    mean = round(sum(g["loops_to_win"] for g in games) / len(games), 3)
    mm_total = sum(min(g["loops_to_win"], 9) - 1 for g in games)
    guard_a = Counter()
    lint = Counter()
    for g in games:
        guard_a.update(g["guard_a"])
        lint.update(g["lint"])
    L = [f"# B-285 計測（{rep['n_games']}局・{rep['days']}日級・perm={rep['perm']}・"
         f"mode={rep['b285_mode']}・HASHSEED={rep['hashseed']}）", "",
         "## 第1層＝行為の数え上げ",
         f"- 脚本家の冷却席（不安-1/能力 unrest_minus）総数: {len(rows)}",
         f"- うち抑制モードの冷却（set_cool_locked / ab_cool_locked）: {len(locked)}",
         f"- うち **対象の不安臨界が0**: {len(dead)}",
         f"- ★**空振り席**（臨界0 ∧ その事件を1件も止めていない）: **{len(whiff)}**"]
    if dead:
        L.append(f"- 臨界0席のうち「止めた事件が1件でもある」: "
                 f"{sum(1 for r in dead if r['n_inc_stopped'] > 0)}")
        stopped = sorted({s for r in dead for s in r["stopped_reasons"]})
        if stopped:
            L.append(f"  - 不発の理由（実測・全数）: {stopped}")
        L.append("")
        L.append("### 内訳（脚本別）")
        by_s = Counter((r["script"], r["seed"]) for r in dead)
        for (s, sd), n in sorted(by_s.items(), key=lambda kv: -kv[1]):
            L.append(f"- {s} s{sd}: {n}")
        L.append("### 内訳（ループ/日）")
        L.append("- " + ", ".join(f"L{lp}D{d}:{n}" for (lp, d), n in
                                  sorted(Counter((r["loop"], r["day"])
                                                 for r in dead).items())))
        L.append("### 内訳（対象キャラ）")
        L.append("- " + ", ".join(f"{k}:{v}" for k, v in
                                  Counter(r["target"] for r in dead).most_common()))
        L.append("### 内訳（分岐）")
        L.append("- " + ", ".join(f"{k}:{v}" for k, v in
                                  Counter(r["kind"] for r in dead).most_common()))
        costs = [r["second_best"]["score"] - (r["score"] or 0)
                 for r in dead if r.get("second_best")]
        if costs:
            L.append(f"### 機会費用（次点 − chosen の点差）")
            L.append(f"- n={len(costs)} 平均{sum(costs)/len(costs):+.1f} "
                     f"最小{min(costs):+.1f} 最大{max(costs):+.1f} "
                     f"／次点が上回る席={sum(1 for c in costs if c > 0)}")
            L.append("- 次点の中身（上位）: " + ", ".join(
                f"{k}:{v}" for k, v in Counter(
                    (r["second_best"]["option"].get("card")
                     or r["second_best"]["option"].get("kind"),
                     r["second_best"]["option"].get("target_kind")
                     or r["second_best"]["option"].get("target"))
                    for r in dead if r.get("second_best")).most_common(8)))
    L += ["", "## 第2層＝脚本家が取ったループ総数（上がれば脚本家が強い）",
          f"- Σ(min(ltw,9)−1) = **{mm_total}**／平均 loops_to_win = {mean}",
          f"- 帰結: {dict(oc)}",
          "", "## 番人",
          f"- 複線演出（勝利条件に寄与しない板暗躍）: {guard_a['no_contribution']} "
          f"（うち設計上のダミー配置 {guard_a['designed_decoy']}／"
          f"非decoy {guard_a['no_contribution'] - guard_a['designed_decoy']}）",
          f"- 板暗躍の総席数: {guard_a['board_anyaku']}",
          f"- mm_lint: {dict(sorted(lint.items()))}"]
    return "\n".join(L)


def diff(rep_off: dict, rep_on: dict) -> str:
    ga = {(g["script"], g["seed"]): g for g in rep_off["games"]}
    gb = {(g["script"], g["seed"]): g for g in rep_on["games"]}
    L = [f"# B-285 diff（{rep_off['days']}日級 perm={rep_off['perm']}／"
         f"{rep_off['b285_mode']} → {rep_on['b285_mode']}）", ""]
    flips, touched = [], 0
    for k in sorted(ga):
        a, b = ga[k], gb[k]
        if a["fingerprint"] != b["fingerprint"]:
            touched += 1
        if a["loops_to_win"] != b["loops_to_win"] or a["outcome"] != b["outcome"]:
            flips.append((k, a, b))
    L.append(f"- 伏せ札列が動いた局: **{touched}/{len(ga)}**")
    L.append(f"- 帰結が動いた局（flip）: **{len(flips)}**")
    for k, a, b in flips:
        L.append(f"  - {k[0]} s{k[1]}: {a['loops_to_win']}({a['outcome']}) → "
                 f"{b['loops_to_win']}({b['outcome']})")
    for tag, rep in (("OFF", rep_off), ("ON", rep_on)):
        rows = [r for g in rep["games"] for r in g["cool_rows"]]
        w = sum(1 for r in rows if r["whiff"])
        mm_total = sum(min(g["loops_to_win"], 9) - 1 for g in rep["games"])
        oc = Counter(g["outcome"] for g in rep["games"])
        ga_ = Counter()
        li = Counter()
        for g in rep["games"]:
            ga_.update(g["guard_a"])
            li.update(g["lint"])
        L.append(f"- [{tag}] 空振り席={w} 第2層={mm_total} 帰結={dict(oc)} "
                 f"非decoy={ga_['no_contribution'] - ga_['designed_decoy']} "
                 f"lint={dict(sorted(li.items()))}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--mode", type=str, default="off", choices=tuple(_MODES))
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-cost", action="store_true")
    ap.add_argument("--diff", nargs=2, default=None,
                    metavar=("OFF.json", "ON.json"))
    args = ap.parse_args(argv)
    if args.diff:
        with open(args.diff[0], encoding="utf-8") as f:
            ro = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            rn = json.load(f)
        print(diff(ro, rn))
        return 0
    rep = sweep(days=args.days, loops=args.loops, perm=args.perm,
                mode=args.mode, verbose=not args.quiet,
                with_cost=not args.no_cost)
    print(summarize(rep))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
