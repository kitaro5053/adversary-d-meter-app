# -*- coding: utf-8 -*-
"""B-247 検死プローブ（★読み取り専用・実装も既定値も一切触らない）。

目的＝B-243 の `B243_BOARD_X_ROLE`（`bx`）が既定 ON にできない理由になっている
**per-game 退行局**を1局ずつ機序で説明する。問いは1つ：

    「演繹が正しくなったのに、なぜ負けるのか」
      (前者) belief が鋭くなった結果、**下流の席の取り合い**が引き直された
             ＝§11b の「同点帯の引き直し」型（＝ ±1〜2 は証拠にしない範囲）
      (後者) **実際に悪い手を選ぶようになった**＝評価器が確定情報を活かせていない

判別の材料（本プローブが出すもの）：
  1. **最初に決定が割れた席**（それ以前の履歴は完全に同一＝原因は belief だけ）。
  2. その席での **A/B 双方の採点器による相互採点**
     ＝`sA(手A) sA(手B) sB(手A) sB(手B)`。
     - 手Aと手Bの点差が**両採点器で 0**なら、割れたのは列挙順ではなく **prov（B-100 の
       強制席）**などスコア外の経路（→ 席の取り合い）。
     - 片側でだけ順位が入れ替わる場合、その **Δ の出どころ**（どの語彙が belief を読むか）
       を見る。
  3. **割れた手が「鋭くなった役職」に触れているか**（＝演繹の対象そのものを撃つ手か、
     まったく別の対象か）。別対象なら下流の再配分＝(前者) の強い証拠。
  4. **単発の因果**＝割れた席の手を B 条件のまま A の手に**1手だけ**差し戻すと
     結末が戻るか（`arena.counterfactual.continue_loop` 相当・mm rng 復元つき）。
     戻るなら「その1手が悪い」、戻らないなら「その先で無数に割れている」＝再配分。

★神視点（真配役）は **答え合わせの表示のみ**に使う（判定には使わない）。使用箇所は
  `--roles` の表示と `bxcand` の「真の担い手が候補に残っているか」の欄だけ。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b247_probe diff \
        --game random_BTX#1 --days 5 --perm rev --a off --b bx --roles
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b247_probe bxcand \
        --game revenge#0 --days 3 --perm rev
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b247_probe cf \
        --game random_BTX#1 --days 5 --perm rev --loop 1 --day 2
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

import agents.belief as bl
import agents.heuristic_protagonist as hp_mod

KNOBS = {"bx": "B243_BOARD_X_ROLE", "doc": "B243_DOCTOR_SELF_TARGET"}
ORDER = ("bx", "doc")


def cond_knobs(name: str) -> tuple[str, ...]:
    base = name.split("@")[0]
    if base in ("off", "none"):
        return ()
    if base == "all":
        return ORDER
    parts = [p for p in base.split("+") if p]
    for p in parts:
        if p not in KNOBS:
            raise SystemExit(f"未知の切替口: {p}（条件名 {name}）")
    return tuple(k for k in ORDER if k in parts)


def _apply(knobs) -> dict:
    # ★B-248＝ベースライン条件（`off`）がリポジトリ既定と一致するかを実行時検査する（§72-45）。
    #   `B243_DOCTOR_SELF_TARGET` は本レーンの測定の後に既定 ON 化された＝現在は落ちるのが正しい。
    #   当時の再現は `KNOB_AUDIT_ALLOW_STALE=1`。
    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, bl, (), driver="b247_probe")
    on = set(knobs)
    old = {k: getattr(bl, a) for k, a in KNOBS.items()}
    for k, a in KNOBS.items():
        setattr(bl, a, k in on)
    return old


def _restore(old: dict):
    for k, a in KNOBS.items():
        setattr(bl, a, old[k])


def _script(game: str, days: int):
    from sim import random_script
    from sim.sample_scripts import SAMPLE_SCRIPTS
    name, seed = game.rsplit("#", 1)
    seed = int(seed)
    if name.startswith("random_"):
        sc = random_script(name.split("_", 1)[1], seed,
                           loops=(4 if days == 5 else 3), days=days)
    else:
        sc = SAMPLE_SCRIPTS[name]()
    return name, seed, sc


def _okey(o: dict):
    return (o.get("card"), o.get("target"), o.get("target_kind"))


def _play_traced(game: str, days: int, cond: str, perm: str, loops: int = 8,
                 topn: int = 6):
    """1局を回し、各 set_card 決定で「選んだ手・全候補の点・belief 要約」を記録する。

    ★採点の採り方＝`HeuristicProtagonist._apply_seat_flags` を**記録ラッパで包み**、
      呼び出し元フレーム（`decide`）のローカル `score` / `options` / `best` を読む。
      `B100_HOOK`（計測専用フック）は `_apply_seat_flags` の**後**に呼ばれるので、
      そこで `score(o)` を再評価すると **席間協調フラグが既に立った後**の点になる
      （例＝`暗躍禁止` が `_kinshi_used` により -100 に落ちる）＝相互採点が歪む。
      本ラッパは**フラグ適用前**に採点する＝実際に `max()` が見た点と一致する。
      ★ラッパは記録のみ（引数も返り値も素通し）＝AI の意思決定には触れない。
    """
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from arena.tie_noise import install_perm, uninstall_perm
    import sys as _sys
    name, seed, sc = _script(game, days)
    recs: list[dict] = []
    _orig_flags = HeuristicProtagonist._apply_seat_flags

    def _wrapped(agent, view, best):
        try:
            fr = _sys._getframe(1)
            if fr.f_code.co_name == "decide":
                score = fr.f_locals.get("score")
                base_score = fr.f_locals.get("_base_score")
                options = fr.f_locals.get("options")
                if score is not None and options is not None:
                    scored = sorted(
                        ((round(float(score(o)), 3), _okey(o)) for o in options),
                        key=lambda t: -t[0])
                    b = getattr(agent, "_belief", None)
                    summary = {}
                    if b is not None:
                        try:
                            _ry: dict = {}
                            for _combo, _p in (b.rule_marginals() or {}).items():
                                for _nm in (_combo if isinstance(_combo, (tuple, list))
                                            else (_combo,)):
                                    _ry[_nm] = _ry.get(_nm, 0.0) + _p
                            summary = {
                                "worlds": b.n_worlds(),
                                "top": {r: b.most_likely_role(r)
                                        for r in ("ウィッチ", "クロマク", "キーパーソン",
                                                  "ミスリーダー", "シリアルキラー")},
                                "cult": {n: round(d.get("カルティスト", 0.0), 4)
                                         for n, d in (b.role_marginals() or {}).items()
                                         if d.get("カルティスト", 0.0) > 0},
                                "ry": {k: round(v, 4) for k, v in
                                       sorted(_ry.items(), key=lambda t: -t[1])[:4]}}
                        except Exception:
                            summary = {}
                    _plan = getattr(agent, "_b100_plan", None)
                    _thr = []
                    if _plan:
                        try:
                            _ts, _pl = _plan
                            _picks = {(b.card, b.target, b.target_kind)
                                      for b in getattr(_pl, "picks", ())}
                            for _t in _ts:
                                _thr.append((getattr(_t, "kind", "?"),
                                             getattr(_t, "label", "?"),
                                             round(float(getattr(_t, "prob", 0.0)), 4),
                                             bool(getattr(_t, "fatal", False))))
                        except Exception:
                            _picks = set()
                    else:
                        _picks = set()
                    recs.append({"loop": view.get("loop"), "day": view.get("day"),
                                 "seat": view.get("seat"),
                                 "threats": _thr,
                                 "picks": sorted(_fmt(p) for p in _picks),
                                 "recs": {json.dumps(k, ensure_ascii=False): round(float(v), 3)
                                          for k, v in (getattr(agent, "_plan_recs", {}) or {}).items()},
                                 "hand": list(view.get("hand", ())),
                                 "chosen": _okey(best), "prov": best.get("prov"),
                                 "n_opts": len(options),
                                 "scored": scored[:topn],
                                 "all_scored": {json.dumps(k, ensure_ascii=False): s
                                                for s, k in scored},
                                 "base": ({json.dumps(_okey(o), ensure_ascii=False):
                                           round(float(base_score(o)), 3) for o in options}
                                          if base_score is not None else {}),
                                 "belief": summary,
                                 "areas": {c.get("name"): (c.get("area"), c.get("unrest"),
                                                           c.get("goodwill"))
                                           for c in view.get("characters", [])
                                           if c.get("alive", True)},
                                 "locals": {k: (sorted(v) if isinstance(v, (set, frozenset))
                                                else v)
                                            for k, v in fr.f_locals.items()
                                            if k in ("danger_board", "_cultist_maybe",
                                                     "_cultist_pin_target", "mm_char_now",
                                                     "board_rules_possible", "keyperson",
                                                     "killer", "_today_kill_zone")}})
        except Exception:
            pass
        return _orig_flags(agent, view, best)

    old = _apply(cond_knobs(cond))
    install_perm(perm)
    HeuristicProtagonist._apply_seat_flags = _wrapped
    log: list[dict] = []
    try:
        mm = HeuristicMastermind(seed)
        php = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": php, "p2": php, "p3": php},
                            log=log)
    finally:
        HeuristicProtagonist._apply_seat_flags = _orig_flags
        uninstall_perm()
        _restore(old)
    return sc, state, recs


def _outcome(state, loops: int):
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def _loop_results(state):
    return [(e.get("loop"), str(e.get("result") or e.get("winner")))
            for e in state.history if e.get("event") in ("loop_result", "game_over")]


def _boards(state):
    return [(e.get("loop"), e.get("board_anyaku"), e.get("char_anyaku"))
            for e in state.history if e.get("event") == "loop_board"]


def _deaths(state):
    return [f"L{e.get('loop')}D{e.get('day')}:{e.get('name')}({e.get('phase')})"
            for e in state.history if e.get("event") == "death"]


def _incidents(state):
    return [f"L{e.get('loop')}D{e.get('day')}:{e.get('name')}"
            f"{'発生' if e.get('occurs') else '不発'}"
            for e in state.history if e.get("event") == "incident"]


def _fmt(k):
    return f"{k[0]}→{k[1]}" + (f"[{k[2]}]" if k[2] and k[2] != "character" else "")


# --------------------------------------------------------------------------
# diff：最初に決定が割れた席の相互採点
# --------------------------------------------------------------------------

def cmd_diff(a) -> int:
    sc, sa, ra = _play_traced(a.game, a.days, a.a, a.perm, loops=a.loops, topn=a.topn)
    _, sb, rb = _play_traced(a.game, a.days, a.b, a.perm, loops=a.loops, topn=a.topn)
    la, oa = _outcome(sa, a.loops)
    lb, ob = _outcome(sb, a.loops)
    print(f"# {a.game} days={a.days} perm={a.perm}  A={a.a} vs B={a.b}")
    print(f"  結末 A: {la}({oa})   B: {lb}({ob})")
    if a.roles:
        print("  [神視点] 配役: " + " ".join(f"{n}={sc.role_of(n)}" for n in sc.cast))
        print("  [神視点] ルールY: " + str(getattr(sc, "rule_y", None))
              + " / ルールX: " + str(getattr(sc, "rule_x", None)))
    print(f"  A loop_result: {_loop_results(sa)}")
    print(f"  B loop_result: {_loop_results(sb)}")
    print(f"  A 盤面(ループ末): {_boards(sa)}")
    print(f"  B 盤面(ループ末): {_boards(sb)}")
    print(f"  A 事件: {_incidents(sa)}")
    print(f"  B 事件: {_incidents(sb)}")
    print(f"  A 死: {_deaths(sa)}")
    print(f"  B 死: {_deaths(sb)}")

    n = min(len(ra), len(rb))
    i = 0
    while i < n and ra[i]["chosen"] == rb[i]["chosen"]:
        i += 1
    print(f"\n  一致した主人公セット決定数={i} / A={len(ra)} B={len(rb)}")
    if i >= n:
        print("  ★決定は完全一致（差は主人公の手ではない）")
        return 0
    shown = 0
    for j in range(i, n):
        if ra[j]["chosen"] == rb[j]["chosen"] and shown:
            continue
        if ra[j]["chosen"] == rb[j]["chosen"]:
            continue
        ea, eb = ra[j], rb[j]
        ka, kb = ea["chosen"], eb["chosen"]
        sa_a = ea["all_scored"].get(json.dumps(ka, ensure_ascii=False))
        sa_b = ea["all_scored"].get(json.dumps(kb, ensure_ascii=False))
        sb_a = eb["all_scored"].get(json.dumps(ka, ensure_ascii=False))
        sb_b = eb["all_scored"].get(json.dumps(kb, ensure_ascii=False))
        same_pos = (ea["loop"], ea["day"], ea["seat"]) == (eb["loop"], eb["day"], eb["seat"])
        print(f"\n  === 割れ #{shown} [idx {j}] "
              f"A:L{ea['loop']}D{ea['day']}{ea['seat']} / "
              f"B:L{eb['loop']}D{eb['day']}{eb['seat']}"
              f"{'' if same_pos else '  ★席位置もズレている'}")
        print(f"    A の手 {_fmt(ka)}  prov={ea['prov']}   "
              f"B の手 {_fmt(kb)}  prov={eb['prov']}")
        print(f"    ★相互採点  sA(A手)={sa_a} sA(B手)={sa_b}   "
              f"sB(A手)={sb_a} sB(B手)={sb_b}")
        _bka = ea.get("base", {}).get(json.dumps(ka, ensure_ascii=False))
        _bkb = ea.get("base", {}).get(json.dumps(kb, ensure_ascii=False))
        _bkа2 = eb.get("base", {}).get(json.dumps(ka, ensure_ascii=False))
        _bkb2 = eb.get("base", {}).get(json.dumps(kb, ensure_ascii=False))
        print(f"    ★素点(_base_score)  A: A手={_bka} B手={_bkb}   "
              f"B: A手={_bkа2} B手={_bkb2}   （素点が動く＝belief が採点語彙を変えた／"
              f"素点同じで総点が動く＝プランナ加点）")
        if None not in (sa_a, sa_b, sb_a, sb_b):
            print(f"    ★点差    A採点器: {round(sa_a - sa_b, 3):+}   "
                  f"B採点器: {round(sb_b - sb_a, 3):+}"
                  f"   （0 なら採点は同点＝スコア外経路 or 列挙順）")
        print(f"    A 上位: {[(s, _fmt(k)) for s, k in ea['scored']]}")
        print(f"    B 上位: {[(s, _fmt(k)) for s, k in eb['scored']]}")
        for tag, e in (("A", ea), ("B", eb)):
            bs = e.get("belief") or {}
            if bs:
                print(f"    {tag} belief: worlds={bs.get('worlds')} "
                      f"top={ {r: (v[0], round(v[1], 3)) for r, v in (bs.get('top') or {}).items() if v[0]} }"
                      f" ruleY={bs.get('ry')}")
            if bs and a.threats:
                print(f"    {tag} p(カルティスト)={bs.get('cult')}")
            if a.threats:
                print(f"    {tag} 脅威: {e.get('threats')}")
                print(f"    {tag} plan.picks: {e.get('picks')}")
                print(f"    {tag} plan加点(_plan_recs): {e.get('recs')}")
            if a.threats:
                print(f"    {tag} decide局所: {e.get('locals')}")
                print(f"    {tag} 生存キャラ(エリア,不安,友好): {e.get('areas')}")
        shown += 1
        if shown >= a.limit:
            break
    return 0


# --------------------------------------------------------------------------
# bxcand：bx の演繹が実際に何を絞ったか（ループごと）
# --------------------------------------------------------------------------

def cmd_bxcand(a) -> int:
    from agents.belief import _board_x_evidence, _board_x_role_candidates
    from agents.belief import _BOARD_X_ROLE_BY_RULE_Y
    sc, sb, rb = _play_traced(a.game, a.days, a.b, a.perm, loops=a.loops, topn=1)
    hist = list(sb.history)
    truth = {n: sc.role_of(n) for n in sc.cast}
    print(f"# {a.game} days={a.days} perm={a.perm} cond={a.b}")
    print("  [神視点] 配役: " + " ".join(f"{n}={r}" for n, r in truth.items()))
    print("  [神視点] ルールY: " + str(getattr(sc, "rule_y", None)))
    # 観測時点＝各 loop_board の直後
    seen = set()
    for idx, e in enumerate(hist):
        if e.get("event") != "loop_board":
            continue
        lp = e.get("loop")
        if lp in seen:
            continue
        seen.add(lp)
        pre = hist[:idx + 1]
        old = _apply(("bx",))
        try:
            boards = _board_x_evidence(pre)
            cands = (_board_x_role_candidates(list(sc.cast), boards)
                     if boards is not None else None)
        finally:
            _restore(old)
        print(f"  L{lp} 盤面={e.get('board_anyaku')}  → ボードX候補={sorted(boards) if boards is not None else None}"
              f"  担い手候補={sorted(cands) if cands is not None else None}")
        if cands is not None:
            for ry, role in _BOARD_X_ROLE_BY_RULE_Y.items():
                holder = [n for n, r in truth.items() if r == role]
                if holder:
                    ok = all(h in cands for h in holder)
                    print(f"      [答え合わせ] {ry}→{role}の真の担い手={holder} "
                          f"候補に残存={ok}")
    return 0


# --------------------------------------------------------------------------
# cf：割れた1手だけを差し戻して結末が戻るか
# --------------------------------------------------------------------------

def cmd_cf(a) -> int:
    """B 条件（bx ON）のまま、指定 (loop, day) の主人公セットだけを A の手に差し戻す。"""
    from arena.postmortem import replay_with_snapshots, _mm_set_of
    from arena.counterfactual import continue_loop
    from arena.tie_noise import install_perm, uninstall_perm
    name, seed, sc = _script(a.game, a.days)
    # A 条件でその日の主人公セットを取る
    _, sa, ra = _play_traced(a.game, a.days, a.a, a.perm, loops=a.loops, topn=1)
    days = [int(x) for x in str(a.day).split(",") if x != ""]
    forced_map = {}
    for dy in days:
        want = [r["chosen"] for r in ra if r["loop"] == a.loop and r["day"] == dy]
        if not want:
            print(f"A 条件に L{a.loop}D{dy} のセット記録が無い")
            return 1
        forced_map[(a.loop, dy)] = [{"card": c, "target": t, "target_kind": k}
                                    for c, t, k in want]
    print(f"# {a.game} days={a.days} perm={a.perm}: B={a.b} のまま "
          f"L{a.loop}D{days} を A={a.a} の手へ差し戻す")
    for (lp, dy), w in sorted(forced_map.items()):
        print(f"  L{lp}D{dy} 差し戻す手: "
              + " / ".join(_fmt((x['card'], x['target'], x['target_kind']))
                           for x in w))
    old = _apply(cond_knobs(a.b))
    install_perm(a.perm)
    try:
        rngs: dict = {}
        state, log, snaps = replay_with_snapshots(replace(sc, loops=a.loops), seed,
                                                  loops=a.loops, rng_states=rngs)
        d0 = min(days)
        snap = snaps.get((a.loop, d0))
        if snap is None:
            print(f"  スナップショット無し L{a.loop}D{d0}")
            return 1
        held = continue_loop(snap, seed, forced_map,
                             mm_rng_state=rngs.get((a.loop, d0)))
    finally:
        uninstall_perm()
        _restore(old)
    print(f"  → このループを守れたか: {held}")
    return 0


# --------------------------------------------------------------------------
# census：flip 局を一括で1行にまとめる（機序が同型かを数える）
# --------------------------------------------------------------------------

def cmd_census(a) -> int:
    """`--games "revenge#0:3:rev,shrine#4:3:id"` を1行ずつ要約する。

    出す欄＝結末A/B・最初の割れ・A/B の `_plan_recs` の当該手加点（8→88 の段差か）・
    その席の board_defeat 脅威の実在度（A→B）。
    """
    rows = []
    for spec in [x for x in a.games.split(",") if x]:
        game, days, perm = spec.split(":")
        days = int(days)
        sc, sa, ra = _play_traced(game, days, a.a, perm, loops=a.loops, topn=1)
        _, sb, rb = _play_traced(game, days, a.b, perm, loops=a.loops, topn=1)
        la, oa = _outcome(sa, a.loops)
        lb, ob = _outcome(sb, a.loops)
        n = min(len(ra), len(rb))
        i = 0
        while i < n and ra[i]["chosen"] == rb[i]["chosen"]:
            i += 1
        if i >= n:
            rows.append(f"{game}/{days}日/{perm}: {la}({oa})->{lb}({ob})  割れなし")
            continue
        ea, eb = ra[i], rb[i]
        ka, kb = ea["chosen"], eb["chosen"]
        jb = json.dumps(kb, ensure_ascii=False)
        reca = (ea.get("recs") or {}).get(jb)
        recb = (eb.get("recs") or {}).get(jb)

        def _bd(e):
            return [round(t[2], 3) for t in (e.get("threats") or [])
                    if t[0] == "board_defeat"]
        rows.append(
            f"{game}/{days}日/{perm}: {la}({oa})->{lb}({ob})"
            f"  初割れ L{ea['loop']}D{ea['day']}{ea['seat']}"
            f"  A手={_fmt(ka)} B手={_fmt(kb)}"
            f"  B手のplan加点 A={reca} B={recb}"
            f"  board_defeat実在度 A={_bd(ea)} B={_bd(eb)}"
            f"  素点 A={ea.get('base', {}).get(jb)} B={eb.get('base', {}).get(jb)}")
    for r in rows:
        print(r, flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-247 検死プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("census")
    pc.add_argument("--games", required=True,
                    help="game#seed:days:perm をカンマ区切り")
    pc.add_argument("--a", default="off")
    pc.add_argument("--b", default="bx")
    pc.add_argument("--loops", type=int, default=8)
    for nm in ("diff", "bxcand", "cf"):
        p = sub.add_parser(nm)
        p.add_argument("--game", required=True)
        p.add_argument("--days", type=int, default=5)
        p.add_argument("--perm", default="id")
        p.add_argument("--loops", type=int, default=8)
        p.add_argument("--a", default="off")
        p.add_argument("--b", default="bx")
        p.add_argument("--roles", action="store_true")
        p.add_argument("--limit", type=int, default=3)
        p.add_argument("--topn", type=int, default=6)
        p.add_argument("--threats", action="store_true")
        if nm == "cf":
            p.add_argument("--loop", type=int, required=True)
            p.add_argument("--day", type=str, required=True, help="差し戻す日（カンマ区切り可）")
    a = ap.parse_args(argv)
    return {"diff": cmd_diff, "bxcand": cmd_bxcand, "cf": cmd_cf,
            "census": cmd_census}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
