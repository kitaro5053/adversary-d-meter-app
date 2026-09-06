# -*- coding: utf-8 -*-
"""B-288〔「降り」の席側の会計〕の機序検分（計測のみ・本番経路は無変更）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない。
対局は `sim.run_game` の素の返り値だけを使う（先例＝`arena/b287_probe.py`／`b287_d4_flip.py`）。

------------------------------------------------------------------------------
測るもの（発注＝FableA・B-288 (1) 機序の検分）
------------------------------------------------------------------------------
仮説＝「折られた線は**今ループ中に完成しうる**のに、まだ完成しない乗り換え先のために
降りてしまう」。∴ 退行局の**降りた時点**で

  (A) 決定時点の一次情報（`_analyze` の返り値のみ・新式を作らない）
      ba／goal_boards／days_left／path_costs_raw／path_costs／funded／board_guarded／
      alt_lines とその完成会計（臨界−不安 ≤ 事件日−今日+1）
  (B) ★反実の真値＝**降りなかった側（base）でその線が実際に完成したか**
      （`loop_board` イベントのループ終了時ボード暗躍＋`secret_log` の敗北理由）

を並べる。(B) が「完成した」なら仮説は生きている／「完成していない」なら仮説は外れ
（＝ただの二正面不足）＝実装せず負の結果として報告する。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b288_probe \
        --script random_BTX --seed 8 --days 4 --perm id --base cur5 --cand on4
    ... --games "revenge#1,revenge#2" --days 3 --base off --cand ab   # まとめて
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from arena.b287_probe import _MODES, _MODE_DEFAULTS, _MODE_FLAGS

_ANYAKU = {"暗躍+1", "暗躍+2"}


def run_one(name: str, seed: int, script, days: int, mode: str, perm: str,
            loops: int = 8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.heuristic import HeuristicMastermind as HM
    from arena.tie_noise import install_perm, uninstall_perm

    from sim import run_game

    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f in _MODE_FLAGS:
            setattr(HM, f, _MODES[mode].get(f, _MODE_DEFAULTS.get(f, False)))
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, log = run_game(replace(script, loops=loops),
                              {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    return state, log


def _set_recs(log):
    return [r for r in log
            if r["actor"] == "mastermind" and r["decision"] == "set_card"]


def _seq(log):
    return [(r["loop"], r["day"], r["chosen"].get("card"),
             r["chosen"].get("target_kind"), r["chosen"].get("target"))
            for r in _set_recs(log)]


def _outcome(state, loops=8):
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return "defense", state.loop_no
    if fb:
        return ("fb_win" if state.winner == "protagonist" else "fb_loss"), loops + 1
    return "loss", loops + 1


def _loop_board(state):
    """ループ番号 → ループ終了時のボード暗躍（`loop_board` イベント＝公開情報）。"""
    return {e["loop"]: dict(e.get("board_anyaku") or {})
            for e in state.history if e.get("event") == "loop_board"}


def _defeat_reasons(state):
    """ループ番号 → そのループで脚本家が取った理由（secret_log の defeat/loop_end 行）。

    ★敗北は板条件だけでなく主人公死亡・KP死亡・TT 等でも成立する＝両方拾う
    （最初の版で板条件だけを見て「取れず」と誤読した＝この関数がその訂正）。
    """
    out: dict[int, list[str]] = {}
    for e in getattr(state, "secret_log", ()) or ():
        if e.get("event") in ("defeat", "loop_end"):
            out.setdefault(e.get("loop"), []).append(
                f"{e.get('event')}:{e.get('reason', '')}")
    return out


def _mm_took(state):
    """ループ番号 → 脚本家がそのループを取ったか（公開 loop_result／最終ループ）。"""
    took = {e["loop"] for e in state.history
            if e.get("event") == "loop_result"
            and e.get("result") == "主人公の敗北"}
    # 最終ループ（loop_result は次ループがある時だけ出る）＝勝敗から判定
    last = state.loop_no
    if state.winner == "mastermind" or any(
            e.get("event") == "final_battle" for e in state.history):
        took.add(last)
    return took


def _incidents_by_loop(state):
    out: dict[int, list[str]] = {}
    for e in state.history:
        if e.get("event") == "incident":
            out.setdefault(e.get("loop"), []).append(
                f"D{e.get('day')}:{e.get('name', '?')}"
                f"{'発生' if e.get('occurs') else '不発'}")
    return out


def _kinshi_by_loop(state):
    """主人公の暗躍禁止の当て先（ループ→[(day,target)]）＝二正面の圧力の受け皿。"""
    out: dict[int, list[str]] = {}
    for e in state.history:
        if e.get("event") != "cards_revealed":
            continue
        for p in e.get("placements", ()) or ():
            if p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止":
                out.setdefault(e["loop"], []).append(
                    f"D{e['day']}:{p.get('target')}")
    return out


def _cool_by_loop(state):
    """主人公の不安-1 の当て先（ループ→[(day,target)]）。"""
    out: dict[int, list[str]] = {}
    for e in state.history:
        if e.get("event") != "cards_revealed":
            continue
        for p in e.get("placements", ()) or ():
            if p.get("owner") != "mastermind" and p.get("card") == "不安-1":
                out.setdefault(e["loop"], []).append(
                    f"D{e['day']}:{p.get('target')}")
    return out


def _broken_sets(view):
    """(mem＝ループ間累積, now＝今ループ単独, mem_extra＝実装と同じ差集合)。

    ★実装（`agents/heuristic._analyze_impl`）と**同じ呼び方**で引く＝
    `b286_line_signals(view, loop_memory=True/False)`。
    ループ単独 broken の合併ではない（合併は「各ループ内で2回」を要求する別物＝
    最初の版はこれを取り違えて mem 由来席を False と誤判定した）。
    """
    from agents.heuristic import b286_line_signals
    mem = set(b286_line_signals(view, loop_memory=True)["broken_seats"])
    now = set(b286_line_signals(view, loop_memory=False)["broken_seats"])
    return mem, now, mem - now


def _alt_table(mm, view):
    """乗り換え先（alt_lines）と、その完成会計の材料を並べる（新式なし）。"""
    from agents.heuristic import unrest_threshold_of
    a = mm._analyze(view)
    b = a.get("b286") or {}
    day = view["day"]
    rows = []
    for cn in sorted(b.get("alt_lines") or ()):
        th = unrest_threshold_of(cn) or 0
        u = a["chars"].get(cn, {}).get("unrest", 0)
        days = sorted(d for d, c in a["pump_targets"] if c == cn)
        rows.append((cn, th, u, days,
                     [f"need{th - u}<= {d - day + 1}" for d in days]))
    return a, rows


def inspect(name, seed, script, days, perm, base, cand, loops=8):
    from agents import HeuristicMastermind

    L = [f"################ {name}#{seed} days={days} perm={perm} "
         f"({base} → {cand})"]
    res = {m: run_one(name, seed, script, days, m, perm, loops)
           for m in (base, cand)}
    for m in (base, cand):
        oc, ltw = _outcome(res[m][0], loops)
        L.append(f"  {m}: ltw={ltw} outcome={oc}")

    sa, sb = _seq(res[base][1]), _seq(res[cand][1])
    ra, rb = _set_recs(res[base][1]), _set_recs(res[cand][1])
    n = min(len(sa), len(sb))
    idx = next((i for i in range(n) if sa[i] != sb[i]), None)
    if idx is None:
        L.append("  ★伏せ札列に食い違いなし")
        return "\n".join(L)
    lp, day = sa[idx][0], sa[idx][1]
    view = ra[idx]["view"]
    L.append(f"\n## 分岐点＝第{idx}席（ループ{lp}・{day}日目）")
    L.append(f"  {base}: {sa[idx][2]} → {sa[idx][4]}({sa[idx][3]})")
    L.append(f"  {cand}: {sb[idx][2]} → {sb[idx][4]}({sb[idx][3]})")
    mem, now, extra = _broken_sets(view)
    seat_a = (sa[idx][3], sa[idx][4])
    L.append(f"  折られた線 mem（ループ間累積）= {sorted(mem)}")
    L.append(f"  折られた線 now（今ループ単独）= {sorted(now)}")
    L.append(f"  ★mem_extra（B-287 由来のみ）= {sorted(extra)}")
    L.append(f"  {base} の席: mem={seat_a in mem} now={seat_a in now} "
             f"mem_extra={seat_a in extra}")

    mm = HeuristicMastermind(seed)
    a, alts = _alt_table(mm, view)
    L.append(f"\n## (A) 決定時点の一次情報（_analyze の返り値）")
    L.append(f"  day={view['day']} days_left={a['days_left']} "
             f"ba={a['ba']} goal_boards={sorted(a['goal_boards'])}")
    L.append(f"  path_costs_raw={ {k: round(v, 2) for k, v in a['path_costs_raw'].items()} }")
    L.append(f"  path_costs    ={ {k: round(v, 2) for k, v in a['path_costs'].items()} }")
    L.append(f"  funded={sorted(a['funded'])} board_guarded={a['board_guarded']} "
             f"supply={a.get('supply')}")
    L.append(f"  reachable_culprits={sorted(a['reachable_culprits'])} "
             f"pump_targets={a['pump_targets']}")
    L.append(f"  alt_lines（乗り換え先）:")
    for cn, th, u, ds, chk in alts:
        L.append(f"    - {cn}: 臨界{th} 現不安{u} 事件日{ds} → {chk}")
    if not alts:
        L.append("    （空）")

    L.append(f"\n## (A2) 脚本家の伏せ札列（分岐ループ以降・食い違いに *）")
    for tag, seq, other in ((base, sa, sb), (cand, sb, sa)):
        L.append(f"  -- {tag} --")
        cur, line = None, []
        for i, t in enumerate(seq):
            if t[0] < lp:
                continue
            if t[0] != cur:
                if line:
                    L.append(f"    L{cur}: " + "  ".join(line))
                cur, line = t[0], []
            mark = "*" if i >= len(other) or other[i] != t else " "
            line.append(f"{mark}D{t[1]}:{t[2]}→{t[4]}")
        if line:
            L.append(f"    L{cur}: " + "  ".join(line))

    L.append(f"\n## (B) ★反実の真値＝{base}（降りなかった側）でその線は完成したか")
    for m in (base, cand):
        st = res[m][0]
        lb, dr = _loop_board(st), _defeat_reasons(st)
        took, inc = _mm_took(st), _incidents_by_loop(st)
        kin, cool = _kinshi_by_loop(st), _cool_by_loop(st)
        L.append(f"  -- {m} --")
        for k in sorted(lb):
            mark = "★" if k == lp else " "
            L.append(f"   {mark}L{k} 取った={'YES' if k in took else 'no '} "
                     f"終了時板={ {b: v for b, v in lb[k].items() if v} } "
                     f"理由={dr.get(k, [])}")
            L.append(f"      事件={inc.get(k, [])}")
            L.append(f"      主人公 暗躍禁止={kin.get(k, [])} 不安-1={cool.get(k, [])}")
    return "\n".join(L)


def census_summary(rep) -> str:
    from collections import Counter
    rows = [r for g in rep["games"] for r in g["rows"]]
    assert len({tuple(r["key"]) for r in rows}) == len(rows), "行キーが一意でない"
    goal = [r for r in rows if r["is_goal"]]
    _as = Counter()
    for g in rep["games"]:
        _as.update(g.get("allseat") or {})
    L = [f"# B-288 センサス（{rep['days']}日級・perm={rep['perm']}・mode={rep['mode']}）",
         f"★全決定での mem_extra 板席 = {_as['n']}（うちループ頭 {_as['head']}）"
         f" → 述語の通過率: static {_as['static']}"
         f"（{100*_as['static']/max(_as['n'],1):.1f}%）"
         f" / prev {_as['prev']}（{100*_as['prev']/max(_as['n'],1):.1f}%）"
         f" / ever {_as['ever']}（{100*_as['ever']/max(_as['n'],1):.1f}%）",
         f"母集団＝ループ開始時点の mem_extra 板席（B-287 が降りる席）= {len(rows)}"
         f"（うちゴール板 {len(goal)}）", ""]

    def _blk(tag, rs):
        if not rs:
            return [f"## {tag}: 0席"]
        can = sum(1 for r in rs if r["can_complete"])
        got = sum(1 for r in rs if r["reached2"])
        took = sum(1 for r in rs if r["mm_took"])
        cross = Counter((r["can_complete"], r["reached2"]) for r in rs)
        return [
            f"## {tag}: {len(rs)}席",
            f"  (i)  決定時点の静的会計『今ループ完成しうる』= {can}/{len(rs)}"
            f" ({100*can/len(rs):.1f}%)",
            f"  (ii) ★反実の真値『実際にその板が ≥2 に届いた』= {got}/{len(rs)}"
            f" ({100*got/len(rs):.1f}%)",
            f"  (参考) その席のループを脚本家が取った = {took}/{len(rs)}",
            f"  クロス表 (会計, 真値) = "
            + "  ".join(f"{k}:{v}" for k, v in sorted(cross.items())),
            f"  終了時の板の値の分布 = "
            + "  ".join(f"{v}:{n}" for v, n in sorted(
                Counter(r["end_val"] for r in rs).items())),
            f"  ★席側の公開実績『直前ループ終了時に ≥2 だった』= "
            f"{sum(1 for r in rs if r['prev_end_ok'])}/{len(rs)}",
            f"    クロス表 (直前≥2, 今ループ真値) = "
            + "  ".join(f"{k}:{v}" for k, v in sorted(Counter(
                (r["prev_end_ok"], r["reached2"]) for r in rs).items())),
            f"  ★席側の公開実績『過去いずれかのループで ≥2』= "
            f"{sum(1 for r in rs if r['ever_end_ok'])}/{len(rs)}",
            f"    クロス表 (過去≥2, 今ループ真値) = "
            + "  ".join(f"{k}:{v}" for k, v in sorted(Counter(
                (r["ever_end_ok"], r["reached2"]) for r in rs).items())),
            f"  乗り換え先ありの席（alt_n>0）= "
            f"{sum(1 for r in rs if r['alt_n'] > 0)}/{len(rs)}"
            f"（うち直前≥2 = {sum(1 for r in rs if r['alt_n'] > 0 and r['prev_end_ok'])}"
            f"／今ループ真値 = {sum(1 for r in rs if r['alt_n'] > 0 and r['reached2'])}）",
        ]
    L += _blk("ゴール板席", goal)
    L += _blk("非ゴール板席", [r for r in rows if not r["is_goal"]])
    L += ["", "## 全席の明細（alt_n>0＝全面降りが起きうる席のみ）"]
    for r in rows:
        if r["alt_n"] > 0:
            L.append(f"  {r['key'][1]}#{r['key'][2]} L{r['loop']} {r['target']}"
                     f" 直前={r['prev_end']} 今end={r['end_val']}"
                     f" 真値={int(r['reached2'])} 取={int(r['mm_took'])}"
                     f" alt={r['alt_lines']}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=str, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--games", type=str, default=None,
                    help='"revenge#1,random_BTX#8" 形式でまとめて')
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--base", type=str, default="off")
    ap.add_argument("--cand", type=str, default="ab")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--census", action="store_true",
                    help="コーパス全数で『降りる母集団』の会計と反実の真値を数える")
    ap.add_argument("--mode", type=str, default="off")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    if args.census:
        import json as _json
        rep = census(days=args.days, perm=args.perm, mode=args.mode,
                     loops=args.loops)
        print(census_summary(rep))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                _json.dump(rep, f, ensure_ascii=False, default=list)
            print(f"→ {args.json}")
        return

    from arena.benchmark import benchmark_scripts
    pool = {(n, s): sc for n, s, sc in benchmark_scripts(days=args.days)}
    if args.games:
        targets = []
        for tok in args.games.split(","):
            nm, _, sd = tok.strip().rpartition("#")
            targets.append((nm, int(sd)))
    else:
        targets = [(args.script, args.seed)]
    for nm, sd in targets:
        print(inspect(nm, sd, pool[(nm, sd)], args.days, args.perm,
                      args.base, args.cand, args.loops), flush=True)
        print()



# ---------------------------------------------------------------------------
# 全数センサス（(1) 機序の検分の分母つき版）
# ---------------------------------------------------------------------------
def census_game(name, seed, script, days, perm, mode="off", loops=8):
    """base（降りない側）の対局から、B-287 が降りる**母集団**を全数で拾う。

    母集団＝各ループ L(≥2) の**最初の脚本家 set_card 決定**（＝ループ開始時点）で
    `mem_extra`（ループ間の実績だけで broken になった席）に入っている板席。
    ★ここは B-287 の (a)/(b) が実際に掛かる席そのもの＝射程と一致する。

    各席について
      (i)  決定時点の静的会計（新式なし＝`_analyze` の一次情報だけ）
           can_complete ⇔ 2 − ba[席] ≤ days_left（1枚/ターン。暗躍+2 があれば1ターンで2）
      (ii) ★反実の真値＝**降りなかったこの対局**で、その板がループ終了時に ≥2 に届いたか
      (iii) その板がゴール板か／そのループを脚本家が取ったか／取った理由
      (iv) 乗り換え先（alt_lines）と、そのループで実際に発生した事件
    """
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents.heuristic import HeuristicMastermind as HM
    from arena.tie_noise import install_perm, uninstall_perm
    from sim import run_game

    install_perm(perm)
    old = {f: getattr(HM, f) for f in _MODE_FLAGS}
    try:
        for f in _MODE_FLAGS:
            setattr(HM, f, _MODES[mode].get(f, _MODE_DEFAULTS.get(f, False)))
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, log = run_game(replace(script, loops=loops),
                              {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        recs = _set_recs(log)
        first = {}
        for r in recs:
            first.setdefault(r["loop"], r)
        lb = _loop_board(state)
        took = _mm_took(state)
        inc = _incidents_by_loop(state)
        dr = _defeat_reasons(state)
        att = HeuristicMastermind(seed)     # _analyze は純関数
        # ★全決定での述語の通過率（ループ頭だけでなく mem_extra が立つ全席）。
        #   発注の静的会計が「何も弾かない」ことの分母つきの証拠。
        allseat = {"n": 0, "static": 0, "prev": 0, "ever": 0, "head": 0}
        for r in recs:
            if r["loop"] < 2:
                continue
            view = r["view"]
            _m, _n, extra = _broken_sets(view)
            extra = [s for s in extra if s[0] == "board"]
            if not extra:
                continue
            a = att._analyze(view)
            for seat in extra:
                allseat["n"] += 1
                if r is first.get(r["loop"]):
                    allseat["head"] += 1
                for md in ("static", "prev", "ever"):
                    att.B288_GUARD_MODE = md
                    if att._b288_seat_completes(view, seat, a["days_left"]):
                        allseat[md] += 1
            att.B288_GUARD_MODE = "prev"
        rows = []
        for lp in sorted(first):
            if lp < 2:
                continue
            r = first[lp]
            view = r["view"]
            _mem, _now, extra = _broken_sets(view)
            if not extra:
                continue
            a = att._analyze(view)
            b = a.get("b286") or {}
            # ★席側の公開履歴＝過去ループ終了時のその板の値（`loop_board` イベント）。
            #   決定時点の view["history"] に既載＝新しい情報源を作っていない。
            prev_end = {}
            for e in view.get("history", ()) or ():
                if e.get("event") == "loop_board":
                    prev_end[e.get("loop")] = dict(e.get("board_anyaku") or {})
            for kind, tgt in sorted(extra):
                if kind != "board":
                    continue
                ba0 = a["ba"].get(tgt, 0)
                _pv = prev_end.get(lp - 1, {}).get(tgt, 0)
                rows.append({
                    "key": (days, name, seed, perm, lp, kind, tgt),
                    "loop": lp, "target": tgt,
                    "is_goal": tgt in a["goal_boards"],
                    "ba_now": ba0, "days_left": a["days_left"],
                    # ★席側の会計の材料（すべて決定時点の公開情報）
                    "prev_end": _pv,                       # 直前ループ終了時の値
                    "prev_end_ok": _pv >= 2,               # 直前ループで完成していた
                    "ever_end_ok": any(v.get(tgt, 0) >= 2
                                       for v in prev_end.values()),
                    "alt_n": len(b.get("alt_lines") or ()),
                    "board_guarded": bool(a.get("board_guarded")),
                    "pc_raw": a["path_costs_raw"].get("board"),
                    "supply": a.get("supply"),
                    "funded": sorted(a.get("funded") or ()),
                    # (i) 決定時点の静的会計（(a) と同じ流儀の楽観会計）
                    "can_complete": (2 - ba0) <= a["days_left"],
                    # (ii) ★反実の真値
                    "reached2": lb.get(lp, {}).get(tgt, 0) >= 2,
                    "end_val": lb.get(lp, {}).get(tgt, 0),
                    "mm_took": lp in took,
                    "reason": dr.get(lp, []),
                    "alt_lines": sorted(b.get("alt_lines") or ()),
                    "incidents": inc.get(lp, []),
                })
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    oc, ltw = _outcome(state, loops)
    return {"script": name, "seed": seed, "days": days, "perm": perm,
            "outcome": oc, "loops_to_win": ltw, "rows": rows,
            "allseat": allseat}


def census(days=3, perm="id", mode="off", loops=8, verbose=True):
    from arena.benchmark import benchmark_scripts
    out = []
    for name, seed, sc in benchmark_scripts(days=days):
        g = census_game(name, seed, sc, days, perm, mode, loops)
        out.append(g)
        if verbose:
            print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                  f"席{len(g['rows'])}", flush=True)
    return {"days": days, "perm": perm, "mode": mode, "games": out}


if __name__ == "__main__":
    main()
