# -*- coding: utf-8 -*-
"""B-245：**供給ペアの合流放置**（族）の計測プローブ（読み取り専用）。

起点＝`docs/仮_b244_log/報告_B244.md` §5-1／§6 的A。
B-244 が「事前登録できる行為指標」として提案した **「供給ペア同室ターン」** を、
教材1局から**ベンチコーパス（3日級130局・5日級70局）へ一般化**する。

## 用語（本プローブが数える行為・すべて公開情報）

- **供給実績**＝公開イベント `unrest` × `phase=="mastermind_ability"` × `delta>0`。
  `target` が **受け手**、`present` がその瞬間の同室者（★卓上で見えている）。
  `_b208_pairs`／`_b230_pairs` が読むのと**同じ一次情報**。
- **供給役候補（交差）**＝**過去ループ**の観測の `present` の**共通部分 − 受け手**。
  ★B-208 が `common = frozenset.intersection(*presents) - {rcv}` として既に計算している量。
- **窓（`open`）**＝ある受け手 R について、**主人公が札を置く時点**で次が全部成り立つターン：
  1. R について過去ループの供給実績があり、交差が空でない。
  2. **ポンプ実証**＝同一ループ内の R への能力フェイズ不安+1 が **2回以上**観測されている
     （`_B202_PUMP_MIN` と同じ健全側の識別＝1回は単発と区別できない）。
  3. R が **残っている致死系事件日（殺人事件／遠隔殺人／病院の事件）の犯人候補ただ1人**。
  4. **不安臨界に未達**（`u < th`）かつ **算術で冷却では負ける**（`u + (due-today+1) >= th`）。
  5. 交差の誰かが **R と同室**（＝分離すべき物が実際にそこに在る）。
- **`meet`（★主指標）**＝窓が開いていたのに、**行動解決フェイズ後も**その供給ペアが
  **同室のまま**残ったターン。＝「合流を放置した席」。
- **`sep`**＝窓が開いていて、行動解決後には分離できていたターン（`open = meet + sep`）。
- **`gate2`**＝窓のうち、**受け手と同室の「ML でありうる者」が2人以上**居て、
  かつ**交差がその中のただ1人を指す**部分集合＝**B-245 の的Aが新たに開ける形**
  （現行 `_b202_pairs` は `len(supp)==1` でここを落としている）。
- **`raw_meet`**＝B-244 §5-1 の素の定義（窓の条件 2〜4 を課さず、受け手が
  belief の犯人候補でありさえすればよい）＝**副指標**（教材の 7/9 と同じ数え方）。

## 規律

- AI・エンジン（`agents/ sim/ engine/`）には**触れない**。フラグはクラス属性を
  実行時に退避→復元するだけ（`pro_flags`）。
- 窓の算術は**プローブ側の独立実装**（`_b202_pairs` を呼ばない）＝
  的の測定が実装の写しにならないようにする。
- 材料は**公開情報のみ**＝`protagonist_view`（公開履歴・公開の事件日程・卓上のカウンター・
  位置・生死）＋ belief の `_culprit_cands`／`role_marginals`
  （★B-240 プローブと同じ扱い＝「AI が既に持っている情報源」であって神視点ではない）。
  行動解決後の位置は `state.phase_snapshots` の「行動解決フェイズ後」から取るが、
  **`area`／`alive` しか読まない**（`role` は読まない＝神視点を使わない）。
- 挙動は変えない＝`decide` のラッパは元の関数をそのまま呼んで返り値を素通しする。
  ★同一走行で per-game の `loops_to_win`／`outcome` も出す（census と bench を1回で取る）。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b245_probe census --days 3 --perm id
    PYTHONHASHSEED=0 ... python -m arena.b245_probe census --days 5 --perm rev --on
    PYTHONHASHSEED=0 ... python -m arena.b245_probe census --days 3 --perm id --noise B205_KINSHI_GUARD=0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace

# 致死系事件＝殺人事件／遠隔殺人／病院の事件（`_lethal_days` の中身＝
# `agents/heuristic_protagonist.py` の `_sync` が公開の事件日程から立てる）。
PUMP_MIN = 2          # ②ポンプ実証の下限（`_B202_PUMP_MIN` と同値）
ML_EPS = 0.05         # 「ミスリーダーでありうる」の下限（`_B202_ML_EPS` と同値）


@contextmanager
def pro_flags(**attrs):
    """HeuristicProtagonist のクラス属性を一時的に上書き（退避→復元）。"""
    from agents import HeuristicProtagonist as HP
    saved = []
    try:
        for k, v in attrs.items():
            if not hasattr(HP, k):
                raise SystemExit(f"!! 未知の切替口 {k}")
            saved.append((k, getattr(HP, k)))
            setattr(HP, k, v)
        yield
    finally:
        for k, v in reversed(saved):
            setattr(HP, k, v)


# --------------------------------------------------------------- 計測本体 --
def _mm_char_cards(view: dict) -> set:
    """このターン脚本家がキャラに伏せた札の対象（★卓上で見えている＝公開）。"""
    return {p.get("target") for p in view.get("placements", []) or ()
            if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}


def _turn_records(view: dict, cands: dict, lethal: set, marg: dict) -> list[dict]:
    """この決定時点の view から「窓」を全部取り出す（プローブ側の独立実装）。"""
    from engine.data import unrest_threshold_of
    cur = view.get("loop")
    today = int(view.get("day", 1) or 1)
    alive = {c["name"]: c for c in view.get("characters", []) if c.get("alive")}
    mm_cards = _mm_char_cards(view)

    seen: dict = {}      # 受け手 -> [過去ループの present 集合, ...]
    pump: dict = {}      # (loop, 受け手) -> 回数（当ループも含む）
    for e in view.get("history", []) or []:
        if (e.get("event") == "unrest" and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0):
            k = (e.get("loop"), e.get("target"))
            pump[k] = pump.get(k, 0) + 1
            if e.get("loop") != cur:
                pres = frozenset(e.get("present") or ())
                if pres:
                    seen.setdefault(e.get("target"), []).append(pres)
    if not seen:
        return []

    lethal_left = sorted(d for d in lethal if d >= today)
    out: list[dict] = []
    # ★`_b202_pairs` は過去ループの観測を要求しない（②ポンプは当ループでも成立する）ので、
    #   「供給実績のある受け手」だけでなく**全生存キャラ**を母集団として `open_ml` を数える。
    for rcv, rc in sorted(alive.items()):
        if rc.get("area") is None:
            continue
        presents = seen.get(rcv) or []
        common = frozenset(n for n in
                           (frozenset.intersection(*presents) - {rcv} if presents
                            else frozenset()) if n in alive)
        # 交差が空になったら和で代用する（B-244 `cmd_supply` と同じ扱い）
        union = frozenset(n for n in ((frozenset().union(*presents) - {rcv})
                                      if presents else frozenset()) if n in alive)
        cand_sup = common or union
        here = sorted(n for n in cand_sup if alive[n].get("area") == rc["area"])
        # ★診断＝公開の `present` 交差がどれだけ一意に収束するか。
        #   教材（人間の脚本家）では L3 以降ずっと 1人だった。AI 脚本家では？
        obs = bool(presents)
        obs_int1 = bool(presents) and len(common) == 1
        # --- 副指標（B-244 §5-1 の素の定義）---
        is_culp = any(rcv in (cands.get(d) or ()) for d in cands)
        raw = bool(here) and is_culp
        # --- ③④＝B-202 の状況条件（両輪ゲート ⑤ は入れない）---
        th = unrest_threshold_of(rcv)
        pumped = max((v for (lp, n), v in pump.items() if n == rcv), default=0)
        due = None
        if th is not None and th >= 1 and pumped >= PUMP_MIN and rc["unrest"] < th:
            for d in lethal_left:
                cs = [n for n in (cands.get(d) or ()) if n in alive]
                if len(cs) == 1 and cs[0] == rcv \
                        and rc["unrest"] + (d - today + 1) >= th:
                    due = d
                    break
        if due is None:
            if raw:
                out.append({"rcv": rcv, "raw": True, "here": tuple(here),
                            "supp": (), "inter": (), "due": None,
                            "open": False, "open_ml": False,
                            "open_ml2": False, "gate2": False,
                            "fc": False, "out_sup": (),
                            "obs": obs, "obs_int1": obs_int1})
            continue
        # 同室の「ML でありうる者」（＝`_b202_pairs` の supp と同じ材料）
        supp = [n for n, c in sorted(alive.items())
                if n != rcv and c.get("area") == rc["area"]
                and marg.get(n, {}).get("ミスリーダー", 0.0) > ML_EPS]
        inter_here = [n for n in sorted(common) if n in supp]
        # ★的B（＝B-208 のゲート②「受け手が移動不能」を外した形）の発火面積。
        #   ★2×2 盤ではどのエリアも互いに1トグルで到達できる（`engine/board.destination`）
        #   ＝「1手で同室になれる」は**常に真**＝的Bの幾何条件には識別力が無い。
        #   ∴ 実質の述語＝「供給役が供給圏の**外**に居て、脚本家がその供給役に札を伏せた」
        #   ＝**B-208 の分岐 (ii)（`移動禁止` で連れ戻しの線を折る）そのもの**。
        out_sup = sorted(n for n in common
                         if alive[n].get("area") != rc["area"]
                         and marg.get(n, {}).get("ミスリーダー", 0.0) > ML_EPS)
        fc = len(out_sup) == 1 and out_sup[0] in mm_cards and not supp
        out.append({
            "rcv": rcv, "area": rc["area"], "here": tuple(here),
            "supp": tuple(supp), "inter": tuple(inter_here), "due": due, "raw": raw,
            # `open`＝供給実績（過去ループ）由来の同室が実在するターン（主指標の母集団）
            "open": bool(here),
            # `open_ml`＝`_b202_pairs` が pairs を立てる状況（供給実績の有無を問わない）
            "open_ml": bool(supp),
            # ★`open_ml2`＝そのうち supp が2人以上＝**分岐(ii) が `len(supp)==1` で死ぬ形**
            "open_ml2": len(supp) >= 2,
            # ★`gate2`＝そのうち公開の交差が supp の中のただ1人を指す＝**本改修が開ける形**
            "gate2": len(supp) >= 2 and len(inter_here) == 1,
            # 的B の発火面積（供給役は圏外・mm が伏せ札を置いた・受け手は移動可）
            "fc": bool(fc), "out_sup": tuple(out_sup),
            "obs": obs, "obs_int1": obs_int1,
        })
    return out


def _play_and_count(sc, seed: int, days: int, loops: int) -> dict:
    """1局を回して族を数える（挙動には触れない）。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    rec: dict = {}          # (loop, day) -> [窓レコード]
    orig = type(hp).decide

    def probed(self, view, decision, options):
        chosen = orig(self, view, decision, options)
        if decision == "set_card":
            k = (view.get("loop"), view.get("day"))
            if k not in rec:
                marg = {}
                try:
                    marg = self._belief.role_marginals() if self._belief else {}
                except Exception:                       # 計測が挙動を壊さないよう保険
                    marg = {}
                rec[k] = _turn_records(view,
                                       getattr(self, "_culprit_cands", {}) or {},
                                       getattr(self, "_lethal_days", set()) or set(),
                                       marg)
        return chosen

    type(hp).decide = probed
    try:
        state, _log = run_game(replace(sc, loops=loops),
                               {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        type(hp).decide = orig

    # 行動解決フェイズ後の位置（★area/alive しか読まない）
    post: dict = {}
    for s in state.phase_snapshots:
        if s.get("point") == "行動解決フェイズ後":
            post[(s.get("loop"), s.get("day"))] = {
                n: c.get("area") for n, c in s.get("characters", {}).items()
                if c.get("alive")}

    tot = {"open": 0, "meet": 0, "gate2": 0, "gate2_meet": 0,
           "open_ml": 0, "ml_meet": 0, "open_ml2": 0, "ml2_meet": 0,
           "fc": 0, "fc_merge": 0, "obs": 0, "obs_int1": 0,
           "raw": 0, "raw_meet": 0, "turns": 0}
    seats: list = []
    for k, rows in sorted(rec.items()):
        tot["turns"] += 1
        area_after = post.get(k) or {}

        def _still(names) -> bool:
            """行動解決後もその誰かが受け手と同室で残っているか（公開の位置のみ）。"""
            a = area_after.get(r["rcv"])
            return a is not None and any(area_after.get(n) == a for n in names)

        for r in rows:
            tot["obs"] += bool(r.get("obs"))
            tot["obs_int1"] += bool(r.get("obs_int1"))
            if r["raw"]:
                tot["raw"] += 1
                tot["raw_meet"] += _still(r["here"])
            if r["open"]:
                tot["open"] += 1
                tot["meet"] += _still(r["here"])
            if r["open_ml"]:
                tot["open_ml"] += 1
                tot["ml_meet"] += _still(r["supp"])
            if r["open_ml2"]:
                tot["open_ml2"] += 1
                tot["ml2_meet"] += _still(r["supp"])
            if r["gate2"]:
                tot["gate2"] += 1
                tot["gate2_meet"] += _still(r["inter"])
            if r["fc"]:
                tot["fc"] += 1
                tot["fc_merge"] += _still(r["out_sup"])
            if r["open"] or r["open_ml"]:
                seats.append({"loop": k[0], "day": k[1], "rcv": r["rcv"],
                              "here": list(r["here"]), "supp": list(r["supp"]),
                              "inter": list(r["inter"]), "due": r["due"],
                              "open": r["open"], "ml2": r["open_ml2"],
                              "gate2": r["gate2"], "meet": _still(r["supp"] or r["here"])})

    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw, outcome = loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    else:
        ltw, outcome = loops + 1, "loss"
    return {"tot": tot, "seats": seats, "ltw": ltw, "outcome": outcome}


def cmd_census(args) -> int:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm

    flags: dict = {}
    if args.on:
        flags["B245_SUPPLIER_INTERSECT"] = True
    if args.on_b:
        flags["B245_MERGE_FORECAST"] = True
    for kv in args.noise or ():
        k, _, v = kv.partition("=")
        flags[k] = {"0": False, "1": True, "False": False, "True": True}.get(v, v)

    grand = {"open": 0, "meet": 0, "gate2": 0, "gate2_meet": 0,
             "open_ml": 0, "ml_meet": 0, "open_ml2": 0, "ml2_meet": 0,
             "fc": 0, "fc_merge": 0, "obs": 0, "obs_int1": 0,
           "raw": 0, "raw_meet": 0, "turns": 0}
    rows = []
    with pro_flags(**flags):
        install_perm(args.perm)
        try:
            for name, seed, sc in benchmark_scripts(days=args.days):
                if args.only and f"{name}#{seed}" != args.only:
                    continue
                res = _play_and_count(sc, seed, args.days, args.loops)
                for k in grand:
                    grand[k] += res["tot"][k]
                rows.append({"script": name, "seed": seed, "ltw": res["ltw"],
                             "outcome": res["outcome"], **res["tot"]})
                if args.seats:
                    for s in res["seats"]:
                        print(f"  {name}#{seed} L{s['loop']}D{s['day']} 受け手={s['rcv']} "
                              f"同室={s['here']} due=D{s['due']} "
                              f"{'gate2' if s['gate2'] else '    '} "
                              f"{'★同室のまま' if s['meet'] else '分離済み'}")
        finally:
            uninstall_perm()
    n_def = sum(1 for r in rows if r["outcome"] == "defense")
    mean = round(sum(r["ltw"] for r in rows) / len(rows), 3) if rows else 0.0
    head = (f"days={args.days} perm={args.perm} flags={flags or '{}'}: "
            f"防衛={n_def}/{len(rows)} 平均={mean} ｜ "
            f"窓open={grand['open']} **meet={grand['meet']}** sep={grand['open']-grand['meet']} "
            f"｜open_ml={grand['open_ml']}(meet={grand['ml_meet']}) "
            f"｜open_ml2={grand['open_ml2']}(meet={grand['ml2_meet']}) "
            f"｜gate2={grand['gate2']}(meet={grand['gate2_meet']}) "
            f"｜fc={grand['fc']}(合流={grand['fc_merge']}) "
            f"｜交差一意={grand['obs_int1']}/{grand['obs']} "
            f"｜raw={grand['raw']}(meet={grand['raw_meet']}) ターン={grand['turns']}")
    print(head)
    for r in rows:
        print(f"  {r['script']}#{r['seed']}: {r['ltw']} {r['outcome']} "
              f"open={r['open']} meet={r['meet']} ml={r['open_ml']}/{r['ml_meet']} "
              f"ml2={r['open_ml2']}/{r['ml2_meet']} g2={r['gate2']}/{r['gate2_meet']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"head": head, "grand": grand, "rows": rows}, f,
                      ensure_ascii=False, indent=1)
    return 0


def _decisions(sc, seed: int, days: int, loops: int) -> list:
    """1局の主人公 `set_card` 決定列（(loop, day, 席, 選択) の並び）。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    _state, log = run_game(replace(sc, loops=loops),
                           {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return [(r["loop"], r["day"], r["actor"],
             (r["chosen"].get("card"), r["chosen"].get("target")))
            for r in log
            if r["decision"] == "set_card" and r["actor"] != "mastermind"]


def cmd_seatdiff(args) -> int:
    """★OFF/ON で**どの席の選択が変わったか**を1局ずつ突き合わせる（flip 検死の道具）。

    ★決定列は分岐後にずれるので、**最初に割れた席**が「直接の効果」で、
    それ以降は**下流の引き直し**（§11b）。両者を区別して印字する。
    """
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm

    flags = {"B245_SUPPLIER_INTERSECT": True}
    install_perm(args.perm)
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            tag = f"{name}#{seed}"
            if args.only and tag != args.only:
                continue
            base = _decisions(sc, seed, args.days, args.loops)
            with pro_flags(**flags):
                on = _decisions(sc, seed, args.days, args.loops)
            n = 0
            for x, y in zip(base, on):
                if x != y:
                    n += 1
                    mark = "★最初の分岐" if n == 1 else "（下流）"
                    print(f"  {tag} L{x[0]}D{x[1]} {x[2]}: "
                          f"{x[3][0]}→{x[3][1]} ⇒ {y[3][0]}→{y[3][1]} {mark}")
            print(f"{tag}: 決定 {len(base)}/{len(on)} 席・割れ={n}"
                  + ("" if len(base) == len(on) else "（局の長さが変わった）"))
    finally:
        uninstall_perm()
    return 0


def cmd_why(args) -> int:
    """★「窓が開いたのに何も変わらなかった」席の**内訳**を出す（負の結果の機序特定）。

    `_b202_pairs` と `_b202_unrest_sep` の呼び出しを記録し、ON の局面で
    (a) pairs が立ったか (b) 両輪ゲート `_cooled_days` を通ったか
    (c) 分岐 (ii) が実際に 73.0 を返した席があるか (d) その席で何が選ばれたか、を印字する。
    """
    from agents import HeuristicMastermind, HeuristicProtagonist as HP
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    from sim import run_game

    pairs_log: dict = {}
    sep_log: list = []
    o_pairs, o_sep = HP._b202_pairs, HP._b202_unrest_sep

    def p_pairs(self, view):
        out = o_pairs(self, view)
        pairs_log[(view.get("loop"), view.get("day"))] = (
            out, tuple(sorted(getattr(self, "_cooled_days", ()) or ())))
        return out

    def p_sep(self, o, view):
        r = o_sep(self, o, view)
        if r is not None:
            sep_log.append((view.get("loop"), view.get("day"),
                            o.get("card"), o.get("target"), r))
        return r

    install_perm(args.perm)
    HP._b202_pairs, HP._b202_unrest_sep = p_pairs, p_sep
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            if args.only and f"{name}#{seed}" != args.only:
                continue
            for on in (False, True):
                pairs_log.clear()
                sep_log.clear()
                with pro_flags(**({"B245_SUPPLIER_INTERSECT": True} if on else {})):
                    mm, hp = HeuristicMastermind(seed), HP(seed)
                    run_game(replace(sc, loops=args.loops),
                             {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
                print(f"--- {name}#{seed} B245={'ON' if on else 'OFF'}")
                for k, (out, cooled) in sorted(pairs_log.items()):
                    if out:
                        print(f"   L{k[0]}D{k[1]} pairs={out} _cooled_days={cooled}")
                print(f"   分岐が値を返した席={len(sep_log)}: {sep_log}")
    finally:
        HP._b202_pairs, HP._b202_unrest_sep = o_pairs, o_sep
        uninstall_perm()
    return 0


def cmd_material(args) -> int:
    """★教材（ユーザー実戦棋譜）で OFF/ON の選択差を見る（盤面は棋譜に固定）。

    B-244 §9-1 の `whatif`（プローブ側の一時パッチ）を、**本物の切替口**で再現する。
    ★これは**1局・1席の観測**＝§11b により採否の根拠にはならない（当たりの確認だけ）。
    """
    from arena.b244_probe import load_log, rerun_forced, _fmt

    meta, _dec = load_log(args.log)
    base = {}
    _state, _log, hp = rerun_forced(meta, args.seed)
    for r in hp.records:
        if r["decision"] == "set_card":
            base[(r["loop"], r["day"], r["seat"])] = r["chosen"]
    flags = {"B245_SUPPLIER_INTERSECT": True} if not args.on_b else {
        "B245_SUPPLIER_INTERSECT": True, "B245_MERGE_FORECAST": True}
    with pro_flags(**flags):
        _state2, _log2, hp2 = rerun_forced(meta, args.seed)
    n = 0
    for r in hp2.records:
        if r["decision"] != "set_card":
            continue
        k = (r["loop"], r["day"], r["seat"])
        if base.get(k) != r["chosen"]:
            n += 1
            print(f"L{k[0]}D{k[1]} {k[2]}: 現行={_fmt(base.get(k))} → ON={_fmt(r['chosen'])}")
    print(f"--- 変化した席={n}（棋譜の盤面は固定＝同一局面での比較） flags={flags}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-245 計測プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["census", "material", "seatdiff", "why"])
    ap.add_argument("--log", default="docs/feedback_logs/"
                                     "鈴蘭_BTX3d_seed0_4つON後検証_2026-08-17.jsonl")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--on", action="store_true", help="的A（B245_SUPPLIER_INTERSECT）を ON")
    ap.add_argument("--on-b", action="store_true", help="的B（B245_MERGE_FORECAST）を ON")
    ap.add_argument("--noise", action="append",
                    help="無関係な切替口のノイズ対照点（例 B205_KINSHI_GUARD=0）")
    ap.add_argument("--only", default="", help="1局だけ（例 random_BTX#3）")
    ap.add_argument("--seats", action="store_true", help="窓の席を1件ずつ印字")
    ap.add_argument("--json", default="", help="結果を JSON へ書き出す")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("!! PYTHONHASHSEED=0 で実行すること", file=sys.stderr)
    return {"material": cmd_material, "seatdiff": cmd_seatdiff,
            "why": cmd_why, "census": cmd_census}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
