# -*- coding: utf-8 -*-
"""B-146 Phase 1 の実測ハーネス（**読むだけ＝挙動不変**）。

チケット＝「主人公死亡でループが途中終了すると `_observed_defeat_board`（odb）が
誤った板にロックされる」（発端＝`docs/監査_B141b_主人公側rule-rational免除の第1号_2026-08-03.md`
の検死／足場＝`docs/監査_B138_実証票の帰属と宛先_2026-08-02.md`）。

**既存の計測器を再利用する**（二重実装しない）：
  - `arena.b138_probe._Probe`＝odb と「(a) で絞ったときの odb」を席ごとに記録する土台。
    本 doc のために `_b146_vote_by_loop`（絞る前の票の由来）と `_b146_end_reason`
    （公開 `loop_end.reason`）の記録を足した（**どちらも読むだけ**）。
  - `arena.b138_probe._scripts`＝標準ベンチと同一の脚本セット。

★**真の敗北板（秘匿情報）は計測器の中だけで使う**。主人公 AI の判断経路には流し込まない
  （プローブは `super().decide()` の**戻り値の後**に属性を読むだけ）。
  取得元＝脚本家ビュー（`sim/views.py:86` `mastermind_view`＝`rule_y` / `rule_y_board_x`）。

サブコマンド:
  truth  … 標準ベンチ2本の全 `set_card` 席を、**層に分けて**数え上げる
           （①票に途中終了ループが入っている席 ②そのうち推定が実際に誤っていた席
             ③そのうち (a) の絞りで直る席／照準を失う席／直らない席）。
  case   … 1局を席ごとに印字（`random_FS` s0／s12 の独立再現用）。

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b138_probe import _Probe, _scripts
from sim import run_game

#: ループ終了時の**盤面**敗北条件を持つルールY（`sim/effects.evaluate_loop_end`）。
_BOARD_RULE_FIXED = {"守るべき場所": "学校", "封印されしモノ": "神社"}
_BOARD_RULE_X = ("復讐者の灯火", "巨大時限爆弾Xの存在")


class _TruthMastermind(HeuristicMastermind):
    """脚本家ビューから**真の敗北板**をループごとに控えるだけ（挙動不変）。

    `rule_y_board_x`（復讐者の灯火＝クロマク初期／巨大時限爆弾X＝ウィッチ初期）は
    **ループごとに変わりうる**（`sim/state.py:510-516`）ので loop 別に持つ。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rule_y: str | None = None
        self.board_x_by_loop: dict[int, str | None] = {}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        # ★super() より前に**読むだけ**（rng は消費しない）。
        self.rule_y = view.get("rule_y")
        self.board_x_by_loop[view.get("loop")] = view.get("rule_y_board_x")
        return super().decide(view, decision, options)


class _B146Probe(_Probe):
    """`_Probe` の記録に「票の由来」「途中終了の理由」を足すだけ。"""

    def decide(self, view, decision, options):
        chosen = super().decide(view, decision, options)
        if self.rows and decision == "set_card" \
                and str(view.get("seat", "")).startswith("p"):
            r = self.rows[-1]
            if r.get("seat") == view.get("seat") \
                    and r.get("loop") == view.get("loop") \
                    and r.get("day") == view.get("day"):
                r["vote_by_loop"] = {k: sorted(v) for k, v in
                                     (getattr(self, "_b146_vote_by_loop", {}) or {}).items()}
                r["end_reason"] = dict(getattr(self, "_b146_end_reason", {}) or {})
                r["odb_t"] = getattr(self, "_b146_odb_t", None)
        return chosen


def _true_boards(rule_y: str | None, board_x: str | None, has_hosp_incident: bool):
    """(狭義, 広義) の**真の敗北板**集合を返す。

    - 狭義＝ルールYの盤面敗北条件の板だけ（`sim/effects.evaluate_loop_end`＝
      守るべき場所→学校／封印されしモノ→神社／復讐者の灯火・巨大時限爆弾X→ボードX）。
      ★ここが「ループ終了時のボード敗北条件」＝odb が本来指すべきもの。
    - 広義＝狭義 ∪ {病院}（脚本に病院の事件が予定されている場合のみ）。
      病院の事件は暗躍≥2 で主人公死亡＝**板が起点の実在の脅威**（`rules/40:151`）だが
      「ループ終了時の盤面敗北条件」ではない＝両方の定義で数え、読み手が選べるようにする。
    """
    strict: set[str] = set()
    if rule_y in _BOARD_RULE_FIXED:
        strict.add(_BOARD_RULE_FIXED[rule_y])
    elif rule_y in _BOARD_RULE_X and board_x:
        strict.add(board_x)
    wide = set(strict) | ({"病院"} if has_hosp_incident else set())
    return strict, wide


def _parse_switch(spec: str) -> dict:
    """`--switch "KEY=True,KEY2=1.5"` を主人公側クラス属性の上書き辞書にする。

    ★存在しないキーは**黙って足さない**（打ち間違いで「効いていない A/B」を作らない）。
    """
    out: dict = {}
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if not hasattr(HeuristicProtagonist, k):
            raise KeyError(f"未知の切替口: {k}")
        if v in ("True", "False"):
            out[k] = (v == "True")
        else:
            out[k] = float(v)
    return out


class _Switches:
    """切替口を一時的に立てる（**毎回実効値を印字**＝規約 §4）。"""

    def __init__(self, over: dict):
        self.over = over
        self.saved: dict = {}

    def __enter__(self):
        for k, v in self.over.items():
            self.saved[k] = getattr(HeuristicProtagonist, k)
            setattr(HeuristicProtagonist, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(HeuristicProtagonist, k, v)
        return False


def _print_switches(over: dict) -> None:
    keys = ["B138_ODB_BOARD_LOSS_ONLY", "B138_ODB_FALLBACK",
            "B137_DEFEAT_CAPABLE_ONLY", "B131_ODB_RECENT_TIEBREAK",
            "B141_COOLER_VALUE_FUTURE_ONLY", "B141_COOLER_ALLPAST_ONLY",
            "B141B_UNLOCK_SAME_DAY", "B143_YIELD"]
    keys += [k for k in over if k not in keys]
    print("[切替口の実効値] " + json.dumps(
        {k: getattr(HeuristicProtagonist, k) for k in keys}, ensure_ascii=False))


def _run(name: str, seed: int, sc, loops: int = 8):
    probe = replace(sc, loops=loops)
    mm = _TruthMastermind(seed)
    hp = _B146Probe(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state, hp, mm


def _classify(r: dict, true_set: set[str]):
    """1席を層に分ける。戻り＝(早期終了票あり?, 死亡票あり?, odb正誤, odb_a0の行方)。"""
    end = r.get("end_reason") or {}
    votes = r.get("vote_by_loop") or {}
    early = [int(lp) for lp in votes if int(lp) in {int(k) for k in end}]
    death = [lp for lp in early
             if any("主人公の死亡" in x for x in end.get(lp, end.get(str(lp), [])))]
    return early, death


# ---------------------------------------------------------------------------
# truth：層に分けた数え上げ
# ---------------------------------------------------------------------------
def cmd_truth(args) -> int:
    print(f"[truth] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}")
    over = _parse_switch(args.switch)
    out: dict = {}
    from arena.tie_noise import install_perm, uninstall_perm
    print(f"[truth] perm={args.perm}")
    with _Switches(over):
        _print_switches(over)
        install_perm(args.perm)
        try:
            return _truth_body(args, out)
        finally:
            uninstall_perm()


def _truth_body(args, out: dict) -> int:
    for days in [int(x) for x in args.days.split(",")]:
        c = Counter()
        ex: list = []
        rows_out: dict = {}
        for name, seed, sc in _scripts(days):
            has_hosp = any(i.name == "病院の事件" for i in sc.incidents)
            state, hp, mm = _run(name, seed, sc, loops=args.loops)
            # ★挙動不変の物証＝`arena.benchmark.loops_to_win` と同じ規約で結末を控え、
            #   素のベンチ（プローブ無し）と一致することを外部で照合できるようにする。
            _fb = any(e.get("event") == "final_battle" for e in state.history)
            if state.winner == "protagonist" and not _fb:
                rows_out[f"{name}#{seed}"] = [state.loop_no, "defense"]
            elif _fb:
                rows_out[f"{name}#{seed}"] = [
                    args.loops + 1, "fb_win" if state.winner == "protagonist" else "fb_loss"]
            else:
                rows_out[f"{name}#{seed}"] = [args.loops + 1, "loss"]
            for r in hp.rows:
                strict, wide = _true_boards(mm.rule_y,
                                            mm.board_x_by_loop.get(r["loop"]), has_hosp)
                early, death = _classify(r, strict)
                odb, odb_a, odb_t = r["odb"], r["odb_a"], r.get("odb_t")
                eff, eff_a = r["eff"], r["eff_a"]
                c["席"] += 1
                if odb is not None:
                    c["odb あり"] += 1
                if not early:
                    continue
                # === 層1＝票に途中終了ループの観測が入っている席 ===
                c["層1 早期終了票あり"] += 1
                if death:
                    c["層1a うち主人公の死亡"] += 1
                if len(early) > len(death):
                    c["層1b うち終了効果(KP/TT)"] += 1
                if odb is None:
                    c["層1 odb=None"] += 1
                    continue
                ok_s, ok_w = odb in strict, odb in wide
                c["層2 odb 誤（狭義）" if not ok_s else "層2 odb 正（狭義）"] += 1
                c["層2 odb 誤（広義）" if not ok_w else "層2 odb 正（広義）"] += 1
                # === 層3＝(a) の絞りで何が起きるか（誤っていた席のみ） ===
                if not ok_s:
                    if odb_a == odb:
                        c["層3 誤＋絞っても同じ"] += 1
                    elif odb_a is None:
                        c["層3 誤＋絞ると None（照準喪失）"] += 1
                    elif odb_a in strict:
                        c["層3 ★誤＋絞ると正解になる"] += 1
                        if len(ex) < args.examples:
                            ex.append((name, seed, r["loop"], r["day"], r["seat"],
                                       odb, odb_a, sorted(strict), eff, eff_a))
                    else:
                        c["層3 誤＋絞っても別の誤り"] += 1
                else:
                    if odb_a == odb:
                        c["層3 正＋絞っても同じ"] += 1
                    elif odb_a is None:
                        c["層3 ★正＋絞ると None（照準喪失）"] += 1
                    else:
                        c["層3 ★正＋絞ると誤りになる"] += 1
                # === 層5＝B-146 の狭い版（票は落とさずタイブレーク材料だけ絞る）===
                if odb_t != odb:
                    c["層5 t で odb が変わる"] += 1
                    if odb not in strict and odb_t in strict:
                        c["層5 ★t で 誤→正"] += 1
                    elif odb in strict and odb_t not in strict:
                        c["層5 ★t で 正→誤"] += 1
                    else:
                        c["層5 t で 誤→誤/その他"] += 1
                # === 層4＝実効照準（`_guess_defeat_board`）レベル ===
                if eff != eff_a:
                    c["層4 実効照準が変わる"] += 1
                    if eff not in strict and eff_a in strict:
                        c["層4 ★照準 誤→正"] += 1
                    elif eff in strict and eff_a not in strict:
                        c["層4 ★照準 正→誤"] += 1
                    else:
                        c["層4 照準 誤→誤/その他"] += 1
        print(f"\n=== {days}日級（{len(_scripts(days))}局・loops={args.loops}） ===")
        for k in ("席", "odb あり", "層1 早期終了票あり", "層1a うち主人公の死亡",
                  "層1b うち終了効果(KP/TT)", "層1 odb=None",
                  "層2 odb 正（狭義）", "層2 odb 誤（狭義）",
                  "層2 odb 正（広義）", "層2 odb 誤（広義）",
                  "層3 誤＋絞っても同じ", "層3 誤＋絞ると None（照準喪失）",
                  "層3 ★誤＋絞ると正解になる", "層3 誤＋絞っても別の誤り",
                  "層3 正＋絞っても同じ", "層3 ★正＋絞ると None（照準喪失）",
                  "層3 ★正＋絞ると誤りになる",
                  "層4 実効照準が変わる", "層4 ★照準 誤→正", "層4 ★照準 正→誤",
                  "層4 照準 誤→誤/その他",
                  "層5 t で odb が変わる", "層5 ★t で 誤→正", "層5 ★t で 正→誤",
                  "層5 t で 誤→誤/その他"):
            print(f"  {k:<34s}: {c[k]}")
        for e in ex:
            print(f"   例 {e[0]} s{e[1]} L{e[2]}D{e[3]} {e[4]}: "
                  f"odb={e[5]}→(絞り){e[6]} 真={e[7]} 照準 {e[8]}→{e[9]}")
        _nd = sum(1 for v in rows_out.values() if v[1] == "defense")
        print(f"  [挙動不変の照合用] 防衛={_nd} "
              f"平均={sum(v[0] for v in rows_out.values()) / len(rows_out):.3f}")
        out[days] = {"counts": dict(c), "rows": rows_out}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\n[truth] 保存 {args.out}")
    return 0


# ---------------------------------------------------------------------------
# case：1局の席ごとの明細（B-141b の記述の独立再現）
# ---------------------------------------------------------------------------
def cmd_case(args) -> int:
    print(f"[case] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}")
    with _Switches(_parse_switch(args.switch)):
        _print_switches(_parse_switch(args.switch))
        return _case_body(args)


def _case_body(args) -> int:
    from arena.b140fu_audit import build_bench_script
    from arena.tie_noise import install_perm, uninstall_perm
    print(f"[case] perm={args.perm}")
    install_perm(args.perm)
    try:
        return _case_body2(args, build_bench_script)
    finally:
        uninstall_perm()


def _case_body2(args, build_bench_script) -> int:
    sc = build_bench_script(args.script, args.seed, args.days)
    has_hosp = any(i.name == "病院の事件" for i in sc.incidents)
    print(f"[script] {args.script} s{args.seed} days={sc.days_per_loop} "
          f"loops={sc.loops}→{args.loops}")
    print(f"  roles={sc.roles}")
    print(f"  incidents={[(i.day, i.name, i.culprit) for i in sc.incidents]}")
    state, hp, mm = _run(args.script, args.seed, sc, loops=args.loops)
    print(f"  ルールY={mm.rule_y} rule_xs={list(sc.rule_xs)} "
          f"／ ボードX(ループ別)={mm.board_x_by_loop}")
    print("  --- ループの終わり方（公開 loop_end ＋ 公開 loop_board ＋ 秘匿 defeat） ---")
    for e in state.history:
        if e.get("event") in ("loop_end", "loop_board", "loop_result"):
            body = {k: v for k, v in e.items()
                    if k in ("reason", "result", "board_anyaku")}
            print(f"    L{e.get('loop')}D{e.get('day')} {e['event']}: {body}")
    for e in state.secret_log:
        if e.get("event") == "defeat":
            print(f"    L{e.get('loop')} 秘匿 defeat: {e.get('reason')}")
    print("  --- 席ごとの odb（真の板との突き合わせ） ---")
    for r in hp.rows:
        strict, _wide = _true_boards(mm.rule_y, mm.board_x_by_loop.get(r["loop"]),
                                     has_hosp)
        early, death = _classify(r, strict)
        mark = "★" if (r["odb"] is not None and r["odb"] not in strict) else " "
        print(f"   {mark}L{r['loop']}D{r['day']} {r['seat']}: odb={r['odb']} "
              f"→(絞り){r['odb_a']} 真={sorted(strict) or '-'} "
              f"照準 {r['eff']}→{r['eff_a']} "
              f"票={r.get('vote_by_loop')} 終了={r.get('end_reason')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-146 Phase 1 実測ハーネス")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("truth")
    p.add_argument("--days", type=str, default="3,5")
    p.add_argument("--loops", type=int, default=8)
    p.add_argument("--examples", type=int, default=12)
    p.add_argument("--out", type=str, default="")
    p.add_argument("--switch", type=str, default="",
                   help='例: --switch "B138_ODB_BOARD_LOSS_ONLY=True,B138_ODB_FALLBACK=False"')
    p.add_argument("--perm", type=str, default="id")
    p.set_defaults(fn=cmd_truth)
    q = sub.add_parser("case")
    q.add_argument("--script", type=str, default="random_FS")
    q.add_argument("--seed", type=int, default=0)
    q.add_argument("--days", type=int, default=5)
    q.add_argument("--loops", type=int, default=8)
    q.add_argument("--switch", type=str, default="")
    q.add_argument("--perm", type=str, default="id")
    q.set_defaults(fn=cmd_case)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
