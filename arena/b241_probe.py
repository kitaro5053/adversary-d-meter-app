# -*- coding: utf-8 -*-
"""B-241：**板ガードの席の調停**の計測プローブ（読み取り専用）。

起点＝バックログ §72-33（FableA 整理）＝**2本の独立レーンが同じ場所に収束した**：

- **B-238 の族A**＝板ガードの**宛先ミス**（**実績非接地席**＝過去ループの実績に接地して
  いない板へガードを置く席）。
- **B-240 の中核所見**（`docs/仮_b240_log/機序_BTX1_L6.md`）＝`random_BTX#1` L6D4 で p2 は
  `不安-1→従者`（1枚で確実に事件が止まる）を options に持ちながら、B-100 の強制折り手
  （`prov: b100_match` の板ガード）に席を取られて撃たない。

∴ 共通の診断＝**板ガードが席を取りすぎている**。本プローブはその「席」を数える。

## 数える行為（すべて公開情報＋belief `_culprit_cands`＝B-224 が既に使っている情報源）

1. ★**`aim_lost`（照準を失った席）**＝ある席の decide で
   (a) **今日1枚撃てば算術で確実に事件が止まる**冷却先 X が `不安-1→X` として options に実在し、
   (b) 実手が **板ガード**（`暗躍禁止`→board）だった席。
   - (a) の算術＝B-240 の `aim_floor_targets` と同じ述語の**独立実装**：
     今日に事件が予定 ∧ X ∈ belief の当日犯人候補 ∧ `th(X)>=1` ∧ **`u(X)==th(X)`**（臨界ちょうど）
     ∧ B-232 の算術で冷却が効く（mm の伏せ札が X に重なっていない ∧ 能力供給の実績ペアが同室にいない）。
2. ★**`aim_lost_zero`（本チケットの主指標(i)）**＝`aim_lost` のうち、
   **その板ガードの「今日の価値」が算術でゼロ**と公開情報で言える席。
   - 板の敗北は暗躍2が要る（`rules/40:47`＝`defense_plan.BOARD_DEFEAT_ANYAKU`）。
   - 今日その板に載りうる暗躍の上限＝**カード1枚**（脚本家は1対象1枚＝`sim/legal.py:66`）
     ＝`暗躍+2` の残弾があれば 2、無ければ 1（`暗躍+2` は 1/loop＝`engine/models.py:30`、
     使用済みは表向き＝公開）＋**止まらない供給**（噂 1/loop 等・暗躍禁止では止まらない）。
   - `現在値 + 止まらない供給 + カード上限 < 2` なら**今日はこの板では負けない**
     ＝**今日この席をガードに使う価値は算術でゼロ**（明日また守ればよい）。
   - ★安全側の除外＝**幻想がその板に居る**（板の暗躍が幻想本人にも乗る＝`rules/30:55`）／
     **今日の事件がその板の暗躍を参照する**（`_b132_incident_feeds` と同じ材料）。
   - ★§72-32 判例3＝cap の根拠は「必要性が算術で確定」**かつ**「今日の価値がゼロ」。
     ∴ 主指標は**この連言**に置く（`aim_lost` 全体の 0 化は的として誤り）。
3. ★**`ungrounded`（主指標(ii)＝B-238 族A の実績非接地席）**＝主人公が `暗躍禁止` を板 B へ
   置いた席のうち、**その日 mm の伏せ札がある別の板 B'** で **過去ループ通算の暗躍配置数が
   B より厳密に多い**ものが存在する席。★定義は `arena/b238_probe.py cmd_ground` と同じ
   （こちらは decide 時点で数える＝実装側と同じ材料であることを確認するため）。

## 規律

- AI・エンジン（`agents/ sim/ engine/`）には**触れない**（フラグはクラス属性の退避→復元のみ）。
- 算術は**プローブ側の独立実装**（`agents/b241_seat_arb.py` を呼ばない）＝的の測定が実装の写しに
  ならないようにする。
- 神視点は使わない（配役・rule Y・race を参照する経路が無い）。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b241_probe seats --days 5 --perm id
    PYTHONHASHSEED=0 ... python -m arena.b241_probe seats --days 5 --perm id --detail
    PYTHONHASHSEED=0 ... python -m arena.b241_probe bench  --days 5 --perm id --on yield,ground
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import replace

KINSHI = "暗躍禁止"
ANYAKU = {"暗躍+1", "暗躍+2"}
BOARD_DEFEAT = 2          # rules/40:47（板敗北の暗躍閾値）


# ---------------------------------------------------------------- 環境の着脱
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


#: ★ノイズ対照点用＝既に land 済みの主人公側の切替口（B-239 §7 と同じ顔ぶれ）。
CTRL_KNOBS = {
    "b221": ("hp", "B221_BREAKER"),
    "b222": ("hp", "B222_FERRY_GEOMETRY"),
    "b224": ("hp", "B224_COOL_FLOOR"),
    "b225": ("hp", "B225_REPEAT_AWARE_BREAK"),
    "b227": ("hp", "B227_IRON_BAND"),
    "b205": ("hp", "B205_KINSHI_GUARD"),
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
    "b232": ("hp", "B232_COOL_MATH"),
    "b234": ("bl", "B234_GOSHINBOKU_TRAIT"),
}


@contextmanager
def ctrl_off(names: tuple):
    """対照点＝無関係な既 land 切替口を OFF にする（退避→復元）。"""
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    saved = []
    try:
        for n in names:
            if not n:
                continue
            if n not in CTRL_KNOBS:
                raise SystemExit(f"未知の対照切替口: {n}")
            where, attr = CTRL_KNOBS[n]
            obj = holders[where]
            saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, False)
        yield
    finally:
        for obj, attr, val in reversed(saved):
            setattr(obj, attr, val)


# ---------------------------------------------------------------- 独立実装 --
def _chars(view: dict) -> dict:
    return {c.get("name"): c for c in (view.get("characters") or [])}


def supply_pair_present(view: dict, x: str) -> bool:
    """過去ループの脚本家能力フェイズ `不安+1` の実績ペアの相方が、今 X と同室か。

    ★`agents/b232_cool_math.supply_obs` の**独立実装**（同じ公開イベントを読む）。
    """
    chars = _chars(view)
    c = chars.get(x)
    if c is None:
        return False
    area = c.get("area")
    lp = view.get("loop")
    partners: set = set()
    for e in (view.get("history") or []):
        if (e.get("event") == "unrest"
                and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0
                and e.get("loop") != lp):
            rcv = e.get("target")
            present = e.get("present") or ()
            if rcv == x:
                partners |= {p for p in present if p != x}
            elif x in present:
                partners.add(rcv)
    for p in partners:
        pc = chars.get(p)
        if pc is not None and pc.get("alive", True) and pc.get("area") == area:
            return True
    return False


def aim_targets(agent, view: dict) -> set:
    """今日「`不安-1` を1枚撃てば算術で確実に事件が止まる」犯人候補（独立実装）。"""
    from engine.data import unrest_threshold_of
    today = view.get("day")
    if today is None:
        return set()
    if not any(i.get("day") == today for i in (view.get("incidents") or [])):
        return set()
    cands = (getattr(agent, "_culprit_cands", None) or {}).get(today) or ()
    chars = _chars(view)
    mm_on = {p.get("target") for p in (view.get("placements") or [])
             if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}
    out = set()
    for x in cands:
        th = unrest_threshold_of(x)
        if not th:
            continue                       # 臨界0（黒猫）＝冷却では止まらない
        c = chars.get(x)
        if c is None or not c.get("alive", True) or c.get("area") is None:
            continue
        if int(c.get("unrest", 0) or 0) != th:     # ★臨界ちょうどだけ
            continue
        if x in mm_on:                     # 相殺／不安禁止で潰される
            continue
        if supply_pair_present(view, x):   # 能力フェイズで +1 されうる
            continue
        out.add(x)
    return out


def mm_plus2_left(view: dict) -> bool:
    """脚本家の `暗躍+2`（1/loop）がこのループにまだ残っているか（公開情報）。"""
    used = list((view.get("used_cards", {}) or {}).get("mastermind", []))
    return "暗躍+2" not in used


def board_today_cap(view: dict) -> int:
    """今日その板に**カードで**載りうる暗躍の上限（脚本家は1対象1枚＝最大1枚）。"""
    return 2 if mm_plus2_left(view) else 1


def board_unstoppable_today(agent, view: dict, b: str, strict: bool = True) -> int:
    """今日その板に**暗躍禁止では止められない**経路で載りうる暗躍の上限（安全側の見積り）。

    材料＝`agents/defense_plan` の DP-6 ヘルパ（噂 1/loop・クロマク）。
    例外が出たら「1以上ある」と見なす（＝安全側＝価値ゼロと主張しない）。

    `strict=True`（★本レーンの規定）＝噂は**確率がゼロでない限り数える**
    （`_rumor_active` の閾値 0.5 は使わない）。`strict=False`＝閾値 0.5 の緩い版
    （初版の実装。**自己訂正の対照として残す**＝報告 §自己申告）。
    """
    try:
        from agents.defense_plan import (_kuromaku_supply_here, _rumor_active,
                                         rumor_left_for, rumor_p)
        bel = getattr(agent, "_belief", None)
        if bel is None:
            return 9
        marg = bel.role_marginals()
        if _kuromaku_supply_here(view, b, marg):
            return 9
        if strict:
            if rumor_p(bel) > 0.0 and rumor_left_for(bel, view) != 0:
                return 1
        elif _rumor_active(bel) and (rumor_left_for(bel, view) or 0) > 0:
            return 1
    except Exception:
        return 9
    return 0


def board_value_zero_today(agent, view: dict, b: str, strict: bool = True) -> bool:
    """「今日この板を守らなくても、今日この板では負けない」＝今日の板ガードの価値は算術ゼロ。"""
    cur = int((view.get("board_anyaku") or {}).get(b, 0) or 0)
    if cur + board_unstoppable_today(agent, view, b, strict) \
            + board_today_cap(view) >= BOARD_DEFEAT:
        return False
    # ★安全側の除外1＝幻想がこの板に居る（板の暗躍が幻想本人にも乗る＝rules/30:55）
    g = _chars(view).get("幻想")
    if g is not None and g.get("alive", True) and g.get("area") == b:
        return False
    # ★安全側の除外2＝今日の事件がこの板の暗躍を参照する
    try:
        if agent._b132_incident_feeds(view, b, int(view.get("day", 1))):
            return False
    except Exception:
        return False
    return True


def past_loop_board_anyaku(view: dict) -> Counter:
    """板 → **過去ループ通算**の暗躍カード配置数（公開情報＝解決時に全カードが公開される）。

    ★`arena/b238_probe.py cmd_ground` の `hist` と同じ量を、**decide 時点で読める公開
    イベント**（`cards_revealed`＝`sim/flow.py` が行動解決の冒頭に発行）から再構成する。
    現ループは含めない（＝L1 は常に空＝初見のループを咎めない）。
    """
    lp = view.get("loop")
    out: Counter = Counter()
    for e in (view.get("history") or []):
        if e.get("event") != "cards_revealed" or e.get("loop") == lp:
            continue
        for p in (e.get("placements") or []):
            if (p.get("owner") == "mastermind" and p.get("target_kind") == "board"
                    and p.get("card") in ANYAKU):
                out[p.get("target")] += 1
    return out


def rival_grounded_boards(view: dict, hist: Counter, b: str) -> list:
    """B-238 族A の「実績で本命」＝今日 mm の伏せ札があり、過去ループ通算暗躍が B より多い板。"""
    mm_boards = {p.get("target") for p in (view.get("placements") or [])
                 if p.get("owner") == "mastermind" and p.get("target_kind") == "board"}
    return sorted(x for x in mm_boards if x != b and hist[x] > hist[b])


# ------------------------------------------------------------- 計測ラッパ --
@contextmanager
def instrument(sink: list):
    """decide を包んで、板ガード席／照準席を記録する（読み取り専用・復元つき）。"""
    from agents import HeuristicProtagonist as HP
    orig = HP.decide

    def wrapped(self, view, decision, options):
        out = orig(self, view, decision, options)
        if decision != "set_card" or view.get("seat") == "mastermind":
            return out
        hist = past_loop_board_anyaku(view)
        aims = aim_targets(self, view)
        cool_opts = {o.get("target") for o in options
                     if o.get("card") == "不安-1" and o.get("target_kind") == "character"}
        aim_avail = sorted(aims & cool_opts)
        is_guard = (out.get("card") == KINSHI and out.get("target_kind") == "board")
        rec = {
            "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
            "card": out.get("card"), "target": out.get("target"),
            "kind": out.get("target_kind"), "prov": out.get("prov"),
            "guard": is_guard, "aim_avail": aim_avail,
        }
        if is_guard:
            b = out.get("target")
            rec["board_anyaku"] = int((view.get("board_anyaku") or {}).get(b, 0) or 0)
            rec["plus2_left"] = mm_plus2_left(view)
            rec["unstop"] = board_unstoppable_today(self, view, b)
            rec["zero"] = board_value_zero_today(self, view, b)          # ★厳格版（規定）
            rec["zero_loose"] = board_value_zero_today(self, view, b, strict=False)
            rec["rivals"] = rival_grounded_boards(view, hist, b)
            rec["hist_b"] = hist[b]
            rec["hist_rivals"] = {x: hist[x] for x in rec["rivals"]}
            rec["alt_in_opts"] = sorted(
                x for x in rec["rivals"]
                if any(o.get("card") == KINSHI and o.get("target_kind") == "board"
                       and o.get("target") == x for o in options))
        sink.append(rec)
        return out

    HP.decide = wrapped
    try:
        yield
    finally:
        HP.decide = orig


def _play(name: str, seed: int, days: int, loops: int):
    """1局打つ（ベンチと同一条件）。"""
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


# ------------------------------------------------------------------ census --
def cmd_seats(args) -> int:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    ctrl = tuple(x for x in (args.ctrl_off or "").split(",") if x)
    tot = Counter()
    per_game: dict = {}
    bench: dict = {}
    diag: list = []
    if args.why:                    # ★計測専用フック（採点・選択には影響しない）
        import agents.b241_seat_arb as _arb
        _arb.DIAG = diag
    install_perm(args.perm)
    try:
        with ctrl_off(ctrl), pro_flags(**_flags(args)):
            for name, seed, sc in benchmark_scripts(days=args.days):
                key = f"{name}#{seed}"
                if args.games and key not in set(args.games.split(",")):
                    continue
                sink: list = []
                with instrument(sink):
                    st = _play(name, seed, args.days, args.loops)
                # ★同じ1局から per-game のベンチ結果も取る（`arena/benchmark.loops_to_win`
                #   と同一の判定＝同じ脚本・同じ seed・同じエージェント構成）。
                fb = any(e.get("event") == "final_battle" for e in st.history)
                if st.winner == "protagonist" and not fb:
                    ltw, outcome = st.loop_no, "defense"
                elif fb:
                    ltw = args.loops + 1
                    outcome = "fb_win" if st.winner == "protagonist" else "fb_loss"
                else:
                    ltw, outcome = args.loops + 1, "loss"
                bench[key] = (ltw, outcome)
                g = Counter()
                for r in sink:
                    g["seat"] += 1
                    if r["aim_avail"]:
                        g["aim_seat"] += 1
                    if not r["guard"]:
                        continue
                    g["guard"] += 1
                    if r["zero"]:
                        g["guard_zero"] += 1
                    if r["zero_loose"]:
                        g["guard_zero_loose"] += 1
                    if r["rivals"]:
                        g["ungrounded"] += 1
                        if r["alt_in_opts"]:
                            g["ungrounded_alt"] += 1
                    else:
                        g["grounded"] += 1
                    if r["aim_avail"]:
                        g["aim_lost"] += 1
                        if r["zero"]:
                            g["aim_lost_zero"] += 1
                        if r["zero_loose"]:
                            g["aim_lost_zero_loose"] += 1
                    if args.detail and (r["aim_avail"] or (args.detail > 1 and r["rivals"])):
                        print(f"  {key} L{r['loop']}D{r['day']} {r['seat']} "
                              f"{r['card']}→{r['target']} prov={r['prov']} "
                              f"照準={r['aim_avail']} 板暗躍={r.get('board_anyaku')} "
                              f"+2残={r.get('plus2_left')} 止まらぬ供給={r.get('unstop')} "
                              f"価値ゼロ={r.get('zero')} 実績={r.get('hist_b')} "
                              f"競合={r.get('hist_rivals')}")
                tot.update(g)
                per_game[key] = dict(g)
    finally:
        uninstall_perm()
        if args.why:
            import agents.b241_seat_arb as _arb
            _arb.DIAG = None
    print(f"days={args.days} perm={args.perm} ctrl_off={ctrl or '-'} flags={_flags(args)}: "
          f"席={tot['seat']} 板ガード席={tot['guard']}"
          f" ｜★aim_lost={tot['aim_lost']} **aim_lost_zero={tot['aim_lost_zero']}**"
          f" ｜★ungrounded={tot['ungrounded']}（うち代替が options に有る={tot['ungrounded_alt']}）"
          f" 接地={tot['grounded']}"
          f" ｜（参考）照準席={tot['aim_seat']} 板の今日価値ゼロ席={tot['guard_zero']}"
          f" ｜（緩い版＝自己訂正の対照）aim_lost_zero_loose={tot['aim_lost_zero_loose']}"
          f" 価値ゼロ席={tot['guard_zero_loose']}")
    if diag:
        rc = Counter(d["reason"] for d in diag)
        print(f"  [腕(B) の見送り理由] {dict(rc)}")
        drops = [round(d["s_best"] - d["s"], 1) for d in diag if d["reason"] == "drop"]
        if drops:
            print(f"  [drop で見送った落差の分布] {dict(Counter(drops))}")
        bands = [round(d["s"], 1) for d in diag if d["reason"] == "below_band"]
        if bands:
            print(f"  [below_band の代替の点] {dict(Counter(bands))}")
    if bench:
        n_def = sum(1 for v in bench.values() if v[1] == "defense")
        mean = round(sum(v[0] for v in bench.values()) / len(bench), 3)
        oc = Counter(v[1] for v in bench.values())
        print(f"  [同一走行の per-game ベンチ] 防衛={n_def}/{len(bench)} 平均={mean} "
              f"結末={dict(oc)}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"total": dict(tot), "per_game": per_game,
                       "bench": {k: list(v) for k, v in bench.items()}}, f,
                      ensure_ascii=False, indent=1)
    return 0


def _flags(args) -> dict:
    f: dict = {}
    on = set(x for x in (getattr(args, "on", "") or "").split(",") if x)
    if "yield" in on:
        f["B241_GUARD_YIELD"] = True
    if "ground" in on:
        f["B241_GUARD_GROUND"] = True
    if getattr(args, "min_hist_gap", None) is not None:
        f["B241_GROUND_MIN_GAP"] = args.min_hist_gap
    if getattr(args, "ground_max_drop", None) is not None:
        f["B241_GROUND_MAX_DROP"] = args.ground_max_drop
    return f


def cmd_bench(args) -> int:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    ctrl = tuple(x for x in (args.ctrl_off or "").split(",") if x)
    with ctrl_off(ctrl), pro_flags(**_flags(args)):
        install_perm(args.perm)
        try:
            rep = run_benchmark(loops=args.loops, days=args.days, verbose=False)
        finally:
            uninstall_perm()
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"days={args.days} perm={args.perm} ctrl_off={ctrl or '-'} flags={_flags(args)}: "
          f"防衛={n_def} 平均={rep['mean_loops_to_win']} 結末={rep['outcomes']}")
    for r in rep["rows"]:
        print(f"  {r['script']}#{r['seed']}: {r['loops_to_win']} {r['outcome']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-241 計測プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["seats", "bench"])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--games", default="")
    ap.add_argument("--on", default="", help="yield,ground（既定 OFF）")
    ap.add_argument("--min-hist-gap", type=int, default=None)
    ap.add_argument("--ground-max-drop", type=float, default=None)
    ap.add_argument("--ctrl-off", default="", help="ノイズ対照点＝無関係な既 land 切替口を OFF")
    ap.add_argument("--detail", type=int, default=0)
    ap.add_argument("--why", action="store_true",
                    help="腕(B) が振り替えを見送った理由を集計（計測専用フック）")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    return {"seats": cmd_seats, "bench": cmd_bench}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
