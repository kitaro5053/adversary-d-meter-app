# -*- coding: utf-8 -*-
"""B-105 計測：**同点帯の解け方**だけを揺らしてベンチの「籤ノイズ」を測る。

背景（起点＝`docs/監査_B101_ON_カルティスト閾値の再較正_2026-07-30.md` §4）：
主人公AIの席選択は最終的に `max(options, key=score)` で決まる。Python の `max` は
**最初の最大値**を返すので、**完全同点の候補が複数ある席では `options` の並び順が勝者を決める**。
並び順は `sim/legal.py` の列挙順＝ルール上の意味を持たない量である。

∴ **採点関数を一切変えずに `options` の並び順だけを変える**と、AIの強さは同一のまま
ベンチの数字だけが動く。その動き幅が「**ベンチのノイズフロア**」＝改善/退行を語れる最小分解能。

本モジュールは計測専用（本番経路には一切触れない）。

    # ノイズフロア（並べ替え9通り × 両ベンチ）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.tie_noise perm \
        --modes id,rev,rot1,rot2,rot3,rot5,h1,h2,h3 --days 3 --out /tmp/perm3.json
    # 同点帯の実態（発生率・幅・どの PRIORITY 値か）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.tie_noise ties --days 3
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

import agents.heuristic_protagonist as _hp_mod
from agents.heuristic_protagonist import PRIORITY, HeuristicProtagonist

# 並べ替えを適用する決定種別（final_battle_guess は options[0] の意味が違うので触らない）
_PERM_DECISIONS = ("set_card", "goodwill_ability")


# ---------------------------------------------------------------------------
# 決定的な並べ替え（採点には一切触れない＝同点帯の解け方だけが変わる）
# ---------------------------------------------------------------------------
def _okey(o: dict) -> str:
    return repr(sorted(o.items(), key=lambda kv: kv[0]))


def permute(options: list[dict], mode: str) -> list[dict]:
    """options の**中身は変えず順序だけ**を決定的に並べ替える。

    ★B-107：`mode` の末尾に `@タグ` を付けると**同じ置換の別名**になる
    （同一プロセス内で同じ条件を2回測って状態漏れが無いことを確認するため）。
    """
    mode = mode.split("@")[0] if isinstance(mode, str) else mode
    if mode in ("id", "", None):
        return options
    if mode == "rev":
        return list(reversed(options))
    if mode.startswith("rot"):
        k = int(mode[3:]) % max(1, len(options))
        return options[k:] + options[:k]
    if mode.startswith("h"):
        salt = mode[1:]
        return sorted(options,
                      key=lambda o: hashlib.md5(
                          (salt + _okey(o)).encode("utf-8")).hexdigest())
    raise ValueError(f"unknown perm mode: {mode}")


_ORIG_DECIDE = HeuristicProtagonist.decide


def install_perm(mode: str) -> None:
    """`HeuristicProtagonist.decide` を並べ替えラッパで包む（採点は無改変）。"""
    if isinstance(mode, str):
        mode = mode.split("@")[0]
    if mode in ("id", "", None):
        HeuristicProtagonist.decide = _ORIG_DECIDE
        return

    def _decide(self, view, decision, options):
        if decision in _PERM_DECISIONS and len(options) > 1:
            options = permute(options, mode)
        return _ORIG_DECIDE(self, view, decision, options)

    HeuristicProtagonist.decide = _decide


def uninstall_perm() -> None:
    HeuristicProtagonist.decide = _ORIG_DECIDE


# ---------------------------------------------------------------------------
# 1) ノイズフロア＝並べ替え別に両ベンチを回す
# ---------------------------------------------------------------------------
def run_perm(modes: list[str], days: int, loops: int = 8) -> dict:
    from arena.benchmark import run_benchmark
    out: dict = {"days": days, "loops": loops, "modes": {}}
    for m in modes:
        install_perm(m)
        try:
            rep = run_benchmark(loops=loops, days=days, verbose=False)
        finally:
            uninstall_perm()
        rows = {f'{r["script"]}#{r["seed"]}': r["loops_to_win"] for r in rep["rows"]}
        outc = {f'{r["script"]}#{r["seed"]}': r["outcome"] for r in rep["rows"]}
        n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
        out["modes"][m] = {
            "defense": n_def,
            "mean": rep["mean_loops_to_win"],
            "outcomes": rep["outcomes"],
            "distribution": rep["distribution"],
            "rows": rows,
            "outcome_by_game": outc,
        }
        print(f"  [{days}日級] perm={m:5s} 防衛={n_def:3d} 平均={rep['mean_loops_to_win']:.3f} "
              f"結末={rep['outcomes']}", flush=True)
    return out


def summarize_perm(res: dict, base: str = "id") -> str:
    ms = res["modes"]
    lines = [f"== 並べ替えノイズフロア（{res['days']}日級 / {len(next(iter(ms.values()))['rows'])}局） =="]
    defs = [v["defense"] for v in ms.values()]
    means = [v["mean"] for v in ms.values()]
    lines.append(f"  防衛数: 最小{min(defs)} 最大{max(defs)} 幅{max(defs) - min(defs)} "
                 f"平均{statistics.mean(defs):.2f} 中央{statistics.median(defs):.1f} "
                 f"標準偏差{statistics.pstdev(defs):.2f}")
    lines.append(f"  平均L : 最小{min(means):.3f} 最大{max(means):.3f} "
                 f"幅{max(means) - min(means):.3f} 標準偏差{statistics.pstdev(means):.3f}")
    b = ms.get(base)
    for m, v in ms.items():
        if b is None or m == base:
            lines.append(f"  {m:5s} 防衛={v['defense']:3d} 平均={v['mean']:.3f}")
            continue
        imp = sum(1 for k, x in v["rows"].items() if x < b["rows"][k])
        reg = sum(1 for k, x in v["rows"].items() if x > b["rows"][k])
        lines.append(f"  {m:5s} 防衛={v['defense']:3d} 平均={v['mean']:.3f} "
                     f"flip(vs {base}): 改善{imp} 退行{reg} 計{imp + reg}")
    return "\n".join(lines)


def pairwise_flips(res: dict) -> str:
    """全ペアの flip 数（ベースライン固有の偏りを除いた「揺れの大きさ」）。"""
    ms = res["modes"]
    keys = list(ms)
    lines = ["  全ペア flip 数（|改善|+|退行|）:"]
    tot = []
    for i, a in enumerate(keys):
        for bq in keys[i + 1:]:
            ra, rb = ms[a]["rows"], ms[bq]["rows"]
            n = sum(1 for k in ra if ra[k] != rb[k])
            tot.append(n)
            lines.append(f"    {a:5s} vs {bq:5s}: {n}")
    if tot:
        lines.append(f"    → 最小{min(tot)} 最大{max(tot)} 平均{statistics.mean(tot):.1f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1b) ★B-107：分割実行した perm 結果（JSON）を束ねて分布と**現行の順位**を出す
# ---------------------------------------------------------------------------
def merge_perm(paths: list[str]) -> dict:
    """複数の `perm --out` JSON を1つに束ねる（modes が重複したら一致を検証）。"""
    out: dict | None = None
    for p in paths:
        with open(p, encoding="utf-8") as f:
            res = json.load(f)
        if out is None:
            out = {"days": res["days"], "loops": res["loops"], "modes": {}}
        elif out["days"] != res["days"]:
            raise ValueError(f"days が混在しています: {out['days']} vs {res['days']}")
        for m, v in res["modes"].items():
            if m in out["modes"]:
                prev = out["modes"][m]
                if prev["defense"] != v["defense"] or prev["rows"] != v["rows"]:
                    raise ValueError(f"同名 mode {m} の結果が食い違います（非決定性の疑い）")
            out["modes"][m] = v
    return out or {"days": 0, "loops": 0, "modes": {}}


def _is_random_perm(mode: str) -> bool:
    """『一様ランダムな列挙順の標本』として扱う族か（rev と md5 置換 h*）。"""
    base = mode.split("@")[0]
    return base == "rev" or (base.startswith("h") and base != "id")


def rank_report(res: dict, base: str = "id") -> str:
    """★本チケットの主結果＝ランダム置換族の分布の中で **現行（id）が何位か**。"""
    ms = res["modes"]
    days = res["days"]
    rnd = {m: v for m, v in ms.items() if _is_random_perm(m)}
    rot = {m: v for m, v in ms.items() if m.split("@")[0].startswith("rot")}
    b = ms.get(base)
    defs = sorted((v["defense"] for v in rnd.values()), reverse=True)
    means = [v["mean"] for v in rnd.values()]
    n = len(defs)
    lines = [f"== ★置換分布と現行の順位（{days}日級・ランダム置換 n={n}） =="]
    if not n:
        return "\n".join(lines + ["  （ランダム置換の標本がありません）"])
    lines.append(
        f"  防衛数: 最小{min(defs)} 最大{max(defs)} 中央{statistics.median(defs):.1f} "
        f"平均{statistics.mean(defs):.2f} 標準偏差{statistics.pstdev(defs):.2f}")
    lines.append(
        f"  平均L : 最小{min(means):.3f} 最大{max(means):.3f} "
        f"中央{statistics.median(means):.3f} 標準偏差{statistics.pstdev(means):.3f}")
    if b is not None:
        d0 = b["defense"]
        better = sum(1 for x in defs if x > d0)
        equal = sum(1 for x in defs if x == d0)
        # 順位＝「現行より良い置換の数 + 1」。同値は同順位。
        lines.append(f"  ★現行({base}) 防衛={d0} 平均={b['mean']:.3f}")
        lines.append(f"    現行より良い置換: {better}/{n}　同値: {equal}/{n}　"
                     f"劣る置換: {n - better - equal}/{n}")
        lines.append(f"    → 現行の順位 = {better + 1} 位 / {n + 1} 条件"
                     f"（percentile {100.0 * (n - better) / (n + 1):.1f}）")
        lines.append(f"    → 『現行がたまたま当たりを引いた』片側p値の目安 = "
                     f"{(better + 1) / (n + 1):.4f}")
        lines.append(f"    現行 − 中央値 = {d0 - statistics.median(defs):+.1f} 局"
                     f"／現行 − 最大 = {d0 - max(defs):+d} 局")
    lines.append("  ランダム置換の防衛数（降順）: " + " ".join(str(x) for x in defs))
    if rot:
        rd = sorted((v["defense"] for v in rot.values()), reverse=True)
        lines.append(f"  （参考）巡回シフト族 n={len(rd)}: " + " ".join(str(x) for x in rd))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1c) ★B-107 Step 2：置換した列挙順の上で PRIORITY を**再較正**できるかを測る
#     （解釈A＝籤／解釈B＝列挙順への過適合 の分離実験）
#     ★実験は **PRIORITY dict の in-place 上書き＋復元**で行う（本番の定数は書き換えない。
#       `arena/tune_protagonist.py` と同じ仕掛け）。
# ---------------------------------------------------------------------------
_ORIG_PRIORITY: dict[str, float] = dict(PRIORITY)


def _bench_point(days: int, loops: int, perm: str, overrides: dict[str, float]) -> dict:
    """PRIORITY を一時的に上書きして1回ベンチを回す（必ず復元する）。"""
    from arena.benchmark import run_benchmark
    _hp_mod.PRIORITY.update(overrides)
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        uninstall_perm()
        _hp_mod.PRIORITY.clear()
        _hp_mod.PRIORITY.update(_ORIG_PRIORITY)
    return {
        "defense": sum(1 for r in rep["rows"] if r["outcome"] == "defense"),
        "mean": rep["mean_loops_to_win"],
        "outcomes": rep["outcomes"],
        "distribution": rep["distribution"],
        "rows": {f'{r["script"]}#{r["seed"]}': r["loops_to_win"] for r in rep["rows"]},
    }


def parse_keyspec(spec: str) -> list[tuple[str, list[float]]]:
    """`key=v1,v2,v3;key2=v1,v2` を [(key, [値...])] に。"""
    out = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        k, _, vs = part.partition("=")
        out.append((k.strip(), [float(x) for x in vs.split(",") if x.strip()]))
    return out


def run_recal(perm: str, days: int, keyspec: str, loops: int = 8,
              rounds: int = 1) -> dict:
    """**貪欲な座標降下**で PRIORITY を再較正する。

    基準＝その置換の既定値（overrides なし）。1定数ずつ全値を振り、
    (防衛数↑, 平均L↓) が最良の値を固定して次の定数へ進む。
    """
    keys = parse_keyspec(keyspec)
    cur: dict[str, float] = {}
    base = _bench_point(days, loops, perm, {})
    print(f"  [{days}日級 perm={perm}] 基準（既定PRIORITY）: "
          f"防衛={base['defense']} 平均={base['mean']:.3f}", flush=True)
    hist = [{"round": 0, "key": None, "value": None, **{k: base[k] for k in
                                                        ("defense", "mean")}}]
    trace: list[dict] = []
    for rd in range(1, rounds + 1):
        for key, vals in keys:
            best = None
            for v in vals:
                ov = dict(cur)
                ov[key] = v
                pt = _bench_point(days, loops, perm, ov)
                trace.append({"round": rd, "key": key, "value": v,
                              "defense": pt["defense"], "mean": pt["mean"],
                              "rows": pt["rows"]})
                print(f"    {key}={v:<8g} 防衛={pt['defense']:3d} "
                      f"平均={pt['mean']:.3f}", flush=True)
                score = (pt["defense"], -pt["mean"])
                if best is None or score > best[0]:
                    best = (score, v, pt)
            if best is not None:
                cur[key] = best[1]
                hist.append({"round": rd, "key": key, "value": best[1],
                             "defense": best[2]["defense"], "mean": best[2]["mean"]})
                print(f"  → {key} := {best[1]}（防衛={best[2]['defense']} "
                      f"平均={best[2]['mean']:.3f}）", flush=True)
    final = _bench_point(days, loops, perm, cur)
    print(f"  [{days}日級 perm={perm}] 再較正後: 防衛={final['defense']} "
          f"平均={final['mean']:.3f}　採用値={cur}", flush=True)
    return {"days": days, "perm": perm, "loops": loops, "keyspec": keyspec,
            "rounds": rounds, "base": base, "best_params": cur,
            "final": final, "history": hist, "trace": trace}


# ---------------------------------------------------------------------------
# 2) 同点帯の実態＝首位が何本並ぶか・どの点で起きるか
# ---------------------------------------------------------------------------
_EPS = 1e-9
FOCUS: float | None = None      # ties --focus <値> で 1つの同点帯を詳細に見る
_PRIO_BY_VAL: dict[float, list[str]] = defaultdict(list)
for _k, _v in PRIORITY.items():
    _PRIO_BY_VAL[round(float(_v), 6)].append(_k)


class _TieTally(HeuristicProtagonist):
    """`max(options, key=score)` を横取りして首位同点の本数を数える（挙動は本体と同一
    ＝`agents.debug.ProbedProtagonist` と同じ仕掛け・戻り値は builtin max のまま）。"""

    def __init__(self, seed: int = 0, sink: dict | None = None):
        super().__init__(seed)
        self.sink = sink if sink is not None else {}

    def decide(self, view, decision, options):
        cap: dict = {}
        _bm = builtins.max

        def spymax(*args, **kw):
            if args and isinstance(args[0], list) and "key" in kw and "scored" not in cap:
                key = kw["key"]
                cap["scored"] = [(key(o), o) for o in args[0]]
            return _bm(*args, **kw)

        had = "max" in _hp_mod.__dict__
        prev = _hp_mod.__dict__.get("max")
        _hp_mod.max = spymax
        try:
            chosen = _ORIG_DECIDE(self, view, decision, options)
        finally:
            if had:
                _hp_mod.max = prev
            else:
                del _hp_mod.max
        sc = cap.get("scored")
        if sc and decision == "set_card":
            top = _bm(s for s, _ in sc)
            width = sum(1 for s, _ in sc if abs(s - top) <= _EPS)
            s = self.sink
            s["seats"] = s.get("seats", 0) + 1
            s.setdefault("width_hist", Counter())[width] += 1
            if width > 1:
                s["tied_seats"] = s.get("tied_seats", 0) + 1
                s.setdefault("top_val_hist", Counter())[round(top, 3)] += 1
                s.setdefault("top_val_width", defaultdict(list))[round(top, 3)].append(width)
                cards = tuple(sorted({o["card"] for sv, o in sc if abs(sv - top) <= _EPS}))
                s.setdefault("tied_cards", Counter())[cards] += 1
                if FOCUS is not None and abs(top - FOCUS) <= 1e-6:
                    band = [o for sv, o in sc if abs(sv - top) <= _EPS]
                    s.setdefault("focus_cards", Counter())[
                        tuple(sorted({o["card"] for o in band}))] += 1
                    s.setdefault("focus_targets", Counter())[
                        len({o.get("target") for o in band})] += 1
                    # 同点帯の駒それぞれのカルティスト周辺確率（＝合理化に使える差の有無）
                    marg = (self._belief.role_marginals()
                            if getattr(self, "_belief", None) is not None else None)
                    if marg:
                        ps = sorted({(o.get("target"),
                                      round(marg.get(o.get("target"), {})
                                            .get("カルティスト", 0.0), 3))
                                     for o in band if o.get("target_kind") == "character"})
                        if ps:
                            s.setdefault("focus_probs", Counter())[
                                tuple(p for _n, p in ps)] += 1
                    # 行き先の板の「敗北板でありうる確率」（B-37 の per-board 単一ソース）
                    bdp = getattr(self, "_board_defeat_probs", None) or {}
                    chars = {c["name"]: c for c in view.get("characters", [])}
                    dests = []
                    for o in band:
                        if o.get("target_kind") != "character":
                            continue
                        c0 = chars.get(o.get("target"))
                        d = _hp_mod._move_dest(c0.get("area") if c0 else None,
                                               o.get("card", ""))
                        if d is not None:
                            dests.append((o.get("target"), d,
                                          round(bdp.get(d, 0.0), 3)))
                    if dests:
                        s.setdefault("focus_dest", Counter())[
                            tuple(sorted({(t, round(p, 3)) for t, _d, p in dests}))] += 1
                        # 同一キャラの2方向タイに限った「行き先の敗北確率が割れているか」
                        per = defaultdict(set)
                        for t, _d, p in dests:
                            per[t].add(p)
                        split = sum(1 for v in per.values() if len(v) > 1)
                        s.setdefault("focus_dir_split", Counter())[
                            (len(per), split)] += 1
        return chosen


def run_ties(days: int, loops: int = 8) -> dict:
    from arena.benchmark import benchmark_scripts
    from dataclasses import replace
    from agents import HeuristicMastermind
    from sim import run_game
    sink: dict = {}
    for name, seed, sc in benchmark_scripts(days=days):
        probe = replace(sc, loops=loops)
        mm = HeuristicMastermind(seed)
        hp = _TieTally(seed, sink=sink)
        run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return sink


def format_ties(sink: dict, days: int) -> str:
    seats = sink.get("seats", 0)
    tied = sink.get("tied_seats", 0)
    lines = [f"== 同点帯の実態（{days}日級・set_card 席） ==",
             f"  観測席数 {seats}　うち**首位が同点で複数**: {tied} 席 "
             f"（{100.0 * tied / max(1, seats):.1f}%）"]
    wh = sink.get("width_hist", Counter())
    lines.append("  同点帯の幅（首位に並ぶ手の本数）の分布:")
    for w in sorted(wh):
        lines.append(f"    幅{w:2d}: {wh[w]:6d} 席"
                     + ("  ← 同点なし" if w == 1 else ""))
    tv = sink.get("top_val_hist", Counter())
    lines.append("  同点帯が起きている**首位スコア値**（全数・多い順）:")
    tvw = sink.get("top_val_width", {})
    for val, n in tv.most_common():
        names = _PRIO_BY_VAL.get(round(float(val), 6), [])
        tag = ("／".join(names)) if names else "（PRIORITY 定数と非一致＝連続値の同点）"
        ws = tvw.get(val, [])
        lines.append(f"    {val:8.3f}: {n:6d} 席  最大幅{max(ws) if ws else 0:2d} "
                     f"平均幅{statistics.mean(ws):.2f}  {tag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="B-105 同点帯／籤ノイズ計測")
    ap.add_argument("cmd", choices=["perm", "ties", "merge", "recal"])
    ap.add_argument("--inputs", type=str, default="",
                    help="merge: カンマ区切りの perm JSON パス")
    ap.add_argument("--perm", type=str, default="id",
                    help="recal: 再較正する列挙順（置換モード）")
    ap.add_argument("--keys", type=str, default="",
                    help="recal: 'key=v1,v2;key2=v1,v2' 形式の掃引指定")
    ap.add_argument("--rounds", type=int, default=1,
                    help="recal: 座標降下の周回数")
    ap.add_argument("--modes", type=str, default="id,rev,rot1,rot2,rot3,rot5,h1,h2,h3")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--focus", type=float, default=None,
                    help="ties: この首位スコア値の同点帯だけ詳細集計する")
    args = ap.parse_args(argv)
    global FOCUS
    FOCUS = args.focus
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    if args.cmd == "perm":
        res = run_perm(args.modes.split(","), days=args.days, loops=args.loops)
        print(summarize_perm(res))
        print(pairwise_flips(res))
        print(rank_report(res))
    elif args.cmd == "merge":
        res = merge_perm([p for p in args.inputs.split(",") if p])
        print(summarize_perm(res))
        print(rank_report(res))
    elif args.cmd == "recal":
        res = run_recal(args.perm, days=args.days, keyspec=args.keys,
                        loops=args.loops, rounds=args.rounds)
    else:
        sink = run_ties(days=args.days, loops=args.loops)
        res = {"days": args.days,
               "seats": sink.get("seats", 0),
               "tied_seats": sink.get("tied_seats", 0),
               "width_hist": dict(sink.get("width_hist", {})),
               "top_val_hist": dict(sink.get("top_val_hist", {})),
               "tied_cards": {"／".join(k): v
                              for k, v in sink.get("tied_cards", Counter()).items()}}
        print(format_ties(sink, args.days))
        if FOCUS is not None:
            res["focus"] = FOCUS
            res["focus_cards"] = {"／".join(k): v for k, v in
                                  sink.get("focus_cards", Counter()).items()}
            res["focus_targets"] = dict(sink.get("focus_targets", Counter()))
            res["focus_probs"] = {str(k): v for k, v in
                                  sink.get("focus_probs", Counter()).items()}
            print(f"\n== 同点帯 {FOCUS} の詳細 ==")
            print("  帯に並ぶカード種:")
            for k, v in sink.get("focus_cards", Counter()).most_common():
                print(f"    {'／'.join(k):40s} {v:5d} 席")
            print("  帯に並ぶ**対象キャラ/板の異なり数**: "
                  + "  ".join(f"{k}種:{v}席" for k, v in
                              sorted(sink.get("focus_targets", Counter()).items())))
            print("  帯の各駒のカルティスト周辺確率（多い順・上位20）:")
            for k, v in sink.get("focus_probs", Counter()).most_common(20):
                flat = "★完全フラット" if len(set(k)) <= 1 else ""
                print(f"    {str(k):45s} {v:5d} 席 {flat}")
            print("  帯の行き先板の P(敗北板)（駒ごと・上位20）:")
            for k, v in sink.get("focus_dest", Counter()).most_common(20):
                print(f"    {str(k):60s} {v:5d} 席")
            print("  （帯の対象キャラ数, 行き先確率が割れているキャラ数）:")
            for k, v in sorted(sink.get("focus_dir_split", Counter()).items()):
                print(f"    {k}: {v} 席")
            res["focus_dest"] = {str(k): v for k, v in
                                 sink.get("focus_dest", Counter()).items()}
            res["focus_dir_split"] = {str(k): v for k, v in
                                      sink.get("focus_dir_split", Counter()).items()}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
