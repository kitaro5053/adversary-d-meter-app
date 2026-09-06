# -*- coding: utf-8 -*-
"""B-240：**冷却札の家計**（cooling-card budget）の計測プローブ（読み取り専用）。

起点＝`docs/仮_b237_log/報告_B237.md` §0-6（B-237 が特定した `random_BTX#1` の実際の敗因）：

  L6D3 で `B224_COOL_FLOOR` の床が **そのループ最後の `不安-1` 札** を今日の候補
  （教師）に使い切る → **L6D4 は3席とも `不安-1` の選択肢が空** → その隙に
  従者（臨界3）が1日で 1→3 に押し上げられて負ける。

＝床は**その日には正しく効いている**（教師は 0/2＝D3 の事件は不発）。問題は
**札の家計＝翌日の分を残していない**こと。

## 用語（本プローブが数える行為）

- **冷却席**＝`decide` が実際に `不安-1→X`（character）を選んだ席。
- **最後の1枚**＝その決定時点で、このループにチームで残る `不安-1` が **1枚以下**
  （`used_cards` から数える＝公開情報。★今ターン伏せられた札はまだ載らない＝
  多めに数える＝「最後の1枚」を渋る安全側。`_b198_cool_left_loop` と同じ会計）。
- **翌日の確定需要**＝翌日（既定 gap=1）に事件が予定されており、その日の犯人候補
  （belief `_culprit_cands[d]`＝B-224 が既に使っている情報源）のうち、
  **今日の最大供給で臨界に届く**キャラ X が実在すること：

      u(X) + SUPPLY_PER_DAY * (d - today) >= th(X)      # SUPPLY_PER_DAY 既定 2

  `2` の根拠＝脚本家は1日に **同じ対象へカードを1枚**（重ね置き不可）＋
  **能力フェイズで +1**（ミスリーダー系）＝最大 +2/日。事件効果等は数えない＝過小＝安全側。
- ★**浪費席（本チケットの的）**＝上記3つが同時に成り立ち、かつ **X ≠ 今日の冷却先** の席。
  ＝「翌日に冷やす必要が算術で確定しているのに、最後の `不安-1` を今日使ってしまった席」。
  - `[床]` ＝そのうち **B-224 の床（`_b224_cool_today`）が当たっていた**席
    ＝本チケットの梃子（床を立てない）で**触れる**部分。
  - `[全]` ＝床の有無を問わない全部。差＝**梃子の届かない面積**。

## 規律

- AI・エンジン（`agents/ sim/ engine/`）には**触れない**（フラグはクラス属性を実行時に
  退避→復元するだけ）。
- 算術は**プローブ側の独立実装**（`agents/b240_cool_budget.py` を呼ばない）＝
  的の測定が実装の写しにならないようにする。
- 材料は**公開情報のみ**（`used_cards`／`placements`／`incidents`／カウンター・位置）＋
  belief の `_culprit_cands`（B-224 が既に使っている情報源）。神視点は使わない。
  `--truth` を付けた時だけ、**答え合わせ用**に「その事件が実際に発生したか」を
  事後の公開イベント（`incident` の発生ログ）から突き合わせる（判定には使わない）。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b240_probe seats --days 5
    PYTHONHASHSEED=0 ... python -m arena.b240_probe seats --days 3 --all
    PYTHONHASHSEED=0 ... python -m arena.b240_probe bench --days 5 --perm id --on
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from dataclasses import replace


@contextmanager
def pro_flags(**attrs):
    """HeuristicProtagonist のクラス属性を一時的に上書き（退避→復元）。"""
    from agents import HeuristicProtagonist as HP
    saved = []
    try:
        for k, v in attrs.items():
            saved.append((k, getattr(HP, k)))
            setattr(HP, k, v)
        yield
    finally:
        for k, v in reversed(saved):
            setattr(HP, k, v)


# ---------------------------------------------------------------- 独立実装 --
def cool_left_loop(view: dict) -> int:
    """このループでチームに残る `不安-1` の枚数（公開情報のみ・独立実装）。"""
    used = view.get("used_cards", {}) or {}
    return sum(1 for s in ("p1", "p2", "p3") if "不安-1" not in used.get(s, ()))


def mm_unrest_plus_left(view: dict) -> int:
    """脚本家の手札に残る `不安+1` の枚数（公開情報＝使用済みは表向き）。"""
    from engine.models import MASTERMIND_HAND
    used = list((view.get("used_cards", {}) or {}).get("mastermind", []))
    return MASTERMIND_HAND.count("不安+1") - used.count("不安+1")


def signature_incident_days(agent, view: dict) -> dict:
    """同署名の敗北 run の署名に載っている {日: {事件名}}（B-224 と同じ公開チャネル）。

    ★プローブ側は `agents/b224_channel.evidenced_incident_days` を**呼ぶ**
    （B-224 の発火チャネルそのものの定義であり、本チケットの算術ではないため）。
    """
    from agents.b224_channel import evidenced_incident_days
    try:
        return evidenced_incident_days(view.get("history", []) or [],
                                       view.get("loop"))
    except Exception:
        return {}


def future_need(agent, view: dict, gap: int, supply: int,
                require_sig: bool, exclude: str | None,
                min_u: int = 0) -> list[dict]:
    """「翌日以降 gap 日以内に、算術で確定する冷却需要」の一覧（独立実装）。

    `min_u`＝需要と認めるキャラの**現在不安の下限**（0＝制限なし／1＝脚本家が
    既にポンプを始めている証拠がある個体だけ）。
    """
    from engine.data import unrest_threshold_of
    today = view.get("day")
    if today is None:
        return []
    chars = {c.get("name"): c for c in (view.get("characters") or [])}
    sig = signature_incident_days(agent, view) if require_sig else None
    cands_by_day = getattr(agent, "_culprit_cands", None) or {}
    out = []
    for inc in (view.get("incidents") or []):
        d = inc.get("day")
        if d is None or not (today < d <= today + gap):
            continue
        if sig is not None and inc.get("name") not in (sig.get(d) or ()):
            continue
        for x in sorted(cands_by_day.get(d) or ()):
            if exclude is not None and x == exclude:
                continue
            c = chars.get(x)
            if c is None or not c.get("alive", True):
                continue
            th = unrest_threshold_of(x)
            if not th:
                continue                      # 臨界0（黒猫）は冷却で止まらない
            u = int(c.get("unrest", 0) or 0)
            if u < min_u:
                continue
            if u + supply * (d - today) >= th:
                out.append({"day": d, "name": inc.get("name"), "x": x,
                            "u": u, "th": th})
    return out


# ------------------------------------------------------------- 計測ラッパ --
@contextmanager
def instrument(sink: list, args):
    """decide を包んで「実際に打った `不安-1`」を記録する（読み取り専用・復元つき）。"""
    from agents import HeuristicProtagonist as HP
    orig = HP.decide

    def wrapped(self, view, decision, options):
        out = orig(self, view, decision, options)
        if decision == "set_card" and out.get("card") == "不安-1" \
                and out.get("target_kind") == "character":
            tgt = out.get("target")
            floor_set = frozenset(getattr(self, "_b224_cool_today", ()) or ())
            rec = {
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"), "target": tgt,
                "left": cool_left_loop(view),
                "mm_plus": mm_unrest_plus_left(view),
                "on_floor": tgt in floor_set,
                "floor_set": sorted(floor_set),
            }
            # 4通りの絞り（署名要求 × 需要側の最低不安）を同時に数える
            for sig in (True, False):
                for mu in (0, 1):
                    rec[("need", sig, mu)] = future_need(
                        self, view, args.gap, args.supply, sig, tgt, min_u=mu)
            rec["need_sig"] = rec[("need", True, 1)]
            rec["need_any"] = rec[("need", False, 1)]
            # ★対照群のための素の情報＝「翌日に事件が予定されているか」だけ
            today = view.get("day")
            rec["tomorrow"] = [(i.get("day"), i.get("name"))
                               for i in (view.get("incidents") or [])
                               if i.get("day") is not None
                               and today < i["day"] <= today + args.gap]
            sink.append(rec)
        return out

    HP.decide = wrapped
    try:
        yield
    finally:
        HP.decide = orig


def _play(name: str, seed: int, days: int, loops: int = 8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import random_script, run_game
    from sim.sample_scripts import SAMPLE_SCRIPTS
    if name.startswith("random_"):
        sc = random_script(name.split("_", 1)[1], seed, days=days)
    else:
        sc = SAMPLE_SCRIPTS[name]()
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state


def _occurred(state) -> set:
    """(loop, day, 事件名) の**実際に発生した**集合（答え合わせ専用）。"""
    out = set()
    for e in getattr(state, "history", []) or []:
        if e.get("event") == "incident" and e.get("occurs"):
            out.add((e.get("loop"), e.get("day"), e.get("name")))
    return out


def _flags(args) -> dict:
    f: dict = {}
    if args.on:
        f["B240_COOL_BUDGET"] = True
    for attr, val in (("B240_MAX_GAP", args.set_gap),
                      ("B240_SUPPLY_PER_DAY", args.set_supply),
                      ("B240_LAST_N", args.last_n),
                      ("B240_NEED_MIN_UNREST", args.set_min_u),
                      ("B240_CAP", args.cap),
                      ("B240_SCOPE", args.scope)):
        if val is not None:
            f[attr] = val
    if args.require_sig is not None:
        f["B240_REQUIRE_SIGNATURE"] = bool(args.require_sig)
    if args.require_mm is not None:
        f["B240_REQUIRE_MM_CARD"] = bool(args.require_mm)
    if args.aim:
        f["B240_AIM_FLOOR"] = True
    if args.aim_only:
        f["B240_AIM_FLOOR"] = True
        f["B240_COOL_BUDGET"] = False
    if args.aim_value is not None:
        f["B240_AIM_FLOOR_VALUE"] = args.aim_value
    return f


# ------------------------------------------------------------------ census --
def cmd_seats(args) -> int:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(args.perm)
    try:
        return _seats(args, benchmark_scripts)
    finally:
        uninstall_perm()


def _seats(args, benchmark_scripts) -> int:
    tot = {"cool": 0, "last": 0, "floor": 0, "hit": 0, "hit_n": 0,
           "ctrl": 0, "ctrl_n": 0}
    variants = [(sig, mu) for sig in (True, False) for mu in (0, 1)]
    for sig, mu in variants:
        tot[("floor", sig, mu)] = 0
        tot[("all", sig, mu)] = 0
    with pro_flags(**_flags(args)):
        for name, seed, sc in benchmark_scripts(days=args.days):
            if args.only and f"{name}#{seed}" != args.only:
                continue
            sink: list = []
            with instrument(sink, args):
                state = _play(name, seed, args.days, loops=args.loops)
            occ = _occurred(state) if args.truth else set()
            for r in sink:
                tot["cool"] += 1
                if r["on_floor"]:
                    tot["floor"] += 1
                last = r["left"] <= args.last_n_probe
                if last:
                    tot["last"] += 1
                for sig, mu in variants:
                    if last and r[("need", sig, mu)]:
                        tot[("all", sig, mu)] += 1
                        if r["on_floor"]:
                            tot[("floor", sig, mu)] += 1
                w_sig = bool(last and r["need_sig"])
                w_any = bool(last and r["need_any"])
                if args.truth and last and r["tomorrow"]:
                    # ★答え合わせ＝「翌日に事件が予定されている最後の1枚席」を
                    #   予言あり／なしの2群に分け、翌日の事件が**実際に発生**した率を比べる。
                    grp = "hit" if r["need_any"] else "ctrl"
                    for d, nm in r["tomorrow"]:
                        tot[grp + "_n"] += 1
                        if (r["loop"], d, nm) in occ:
                            tot[grp] += 1
                if args.all or w_sig or (args.wide and w_any):
                    need = r["need_any"] or r["need_sig"]
                    ns = ",".join(f"{n['x']}({n['u']}/{n['th']})D{n['day']}"
                                  for n in need) or "-"
                    print(f"{name}#{seed} L{r['loop']}D{r['day']} {r['seat']} "
                          f"不安-1→{r['target']} "
                          f"{'★床' if r['on_floor'] else '  素'} "
                          f"残弾={r['left']} mm不安+1={r['mm_plus']} "
                          f"翌日需要[署名]={len(r['need_sig'])} "
                          f"[全]={len(r['need_any'])} {ns}"
                          + (f" 発生={[(n['day'], (r['loop'], n['day'], n['name']) in occ) for n in need]}"
                             if args.truth and need else ""))
    head = (f"days={args.days} perm={args.perm} on={args.on} gap={args.gap} "
            f"supply={args.supply} last<={args.last_n_probe}: "
            f"冷却席={tot['cool']}（うち床={tot['floor']}） 最後の1枚={tot['last']}")
    parts = []
    for sig, mu in variants:
        tag = f"署名{'要' if sig else '不問'}/u>={mu}"
        parts.append(f"浪費席[床|{tag}]={tot[('floor', sig, mu)]} "
                     f"[全|{tag}]={tot[('all', sig, mu)]}")
    print(head + " ｜ " + " ｜ ".join(parts)
          + (f" ｜答え合わせ（最後の1枚席・翌日に事件が予定された件）:"
             f" 予言あり {tot['hit']}/{tot['hit_n']} 発生 ／"
             f" 対照（予言なし） {tot['ctrl']}/{tot['ctrl_n']} 発生"
             if args.truth else ""))
    return 0


def cmd_bench(args) -> int:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    with pro_flags(**_flags(args)):
        install_perm(args.perm)
        try:
            rep = run_benchmark(loops=args.loops, days=args.days, verbose=False)
        finally:
            uninstall_perm()
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"days={args.days} perm={args.perm} on={args.on} flags={_flags(args)}: "
          f"防衛={n_def} 平均={rep['mean_loops_to_win']} 結末={rep['outcomes']}")
    for r in rep["rows"]:
        print(f"  {r['script']}#{r['seed']}: {r['loops_to_win']} {r['outcome']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-240 計測プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["seats", "bench"])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--on", action="store_true", help="B240_COOL_BUDGET=True で測る")
    # --- census 側の算術（プローブ独自・実装フラグとは独立に振れる） ---
    ap.add_argument("--gap", type=int, default=1, help="何日先まで需要を見るか")
    ap.add_argument("--supply", type=int, default=2, help="1日あたりの最大供給")
    ap.add_argument("--last-n-probe", type=int, default=1,
                    help="「最後の1枚」とみなす残弾の上限")
    ap.add_argument("--all", action="store_true", help="全冷却席を表示")
    ap.add_argument("--wide", action="store_true", help="署名不問の浪費席も表示")
    ap.add_argument("--truth", action="store_true",
                    help="★答え合わせのみ：需要日の事件が実際に発生したか")
    ap.add_argument("--only", default=None, help="1局だけ測る（例 random_BTX#1）")
    # --- 実装側フラグの上書き（掃引用） ---
    ap.add_argument("--set-gap", type=int, default=None)
    ap.add_argument("--set-supply", type=int, default=None)
    ap.add_argument("--last-n", type=int, default=None)
    ap.add_argument("--set-min-u", type=int, default=None)
    ap.add_argument("--cap", type=float, default=None)
    ap.add_argument("--aim", action="store_true", help="第2腕（需要日の照準）も ON")
    ap.add_argument("--aim-only", action="store_true", help="第2腕だけ ON（家計は OFF）")
    ap.add_argument("--aim-value", type=float, default=None)
    ap.add_argument("--scope", default=None, choices=["floor", "both"])
    ap.add_argument("--require-sig", type=int, default=None)
    ap.add_argument("--require-mm", type=int, default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"seats": cmd_seats, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
