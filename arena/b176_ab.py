# -*- coding: utf-8 -*-
"""B-176 Phase 2（当夜×確度帯の資格軸）の A/B・掃引ドライバ。

切替口＝`agents.heuristic_protagonist.HeuristicProtagonist.B176_TONIGHT_PROB`
（float|None・既定 None＝OFF）／`B176_BOARD_IMMINENT`（bool・既定 False＝感度層）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- **`btx5_seal` を単独行で必ず印字**（B-66(1) 前科＝Phase 1 §7-3 罠2）。
- `--fires`＝**行為の数え上げ**（規約 §11b 成果の示し方 1）＝当夜×帯の資格で
  席が強制された回数・脅威種別・**同一局×同一ラベルのループ横断反復**（罠1 トレッドミル）・
  強制に使ったカード種。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b176_ab --days 5 \\
        --values off,0.7,0.5,0.3
    ... --values off,0.5,0.5imm   # 0.5imm＝感度層（B176_BOARD_IMMINENT）も ON
    ... --fires
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import agents.b100_alloc as ba
from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark

#: B-176 の資格理由に必ず入る印（`b100_alloc.gate_reason` ④）。
_MARK = "当夜×帯"


#: 本番の (a) 除外集合（対照点 raw で空にする・掃引後に必ず戻す）。
_PROD_SETUP_KINDS = ba.B176_SETUP_KINDS


def _parse(v: str):
    """"off"→None／"0.5"→(prob, imm, raw, disp)。
    suffix: "imm"＝感度層 ON／"raw"＝(a) 仕込み除外を**無効化**（対照）／
    "b"＝(b) 押し出しガード（B176_MAX_DISPLACED=80.0）。例 "0.5rawb"＝(b) 単独。"""
    s = (v or "").strip().lower()
    if s in ("off", "none", ""):
        return None
    imm = raw = disp = False
    changed = True
    while changed:
        changed = False
        for suf, name in (("imm", "imm"), ("raw", "raw"), ("b", "disp")):
            if s.endswith(suf):
                s = s[:-len(suf)]
                if name == "imm":
                    imm = True
                elif name == "raw":
                    raw = True
                else:
                    disp = True
                changed = True
    return (float(s), imm, raw, disp)


def _apply(val):
    """★どの点でも全切替口を全部書く（前の点の残留で汚染しない）。"""
    if val is None:
        HeuristicProtagonist.B176_TONIGHT_PROB = None
        HeuristicProtagonist.B176_BOARD_IMMINENT = False
        HeuristicProtagonist.B176_MAX_DISPLACED = None
        ba.B176_SETUP_KINDS = _PROD_SETUP_KINDS
    else:
        prob, imm, raw, disp = val
        HeuristicProtagonist.B176_TONIGHT_PROB = prob
        HeuristicProtagonist.B176_BOARD_IMMINENT = imm
        HeuristicProtagonist.B176_MAX_DISPLACED = 80.0 if disp else None
        ba.B176_SETUP_KINDS = frozenset() if raw else _PROD_SETUP_KINDS


def _switch_line(days: int) -> str:
    return (f"  [切替口] B176_TONIGHT_PROB 実効="
            f"{HeuristicProtagonist.B176_TONIGHT_PROB}"
            f" ／ B176_BOARD_IMMINENT 実効="
            f"{HeuristicProtagonist.B176_BOARD_IMMINENT}"
            f" ／ ★B176_SETUP_KINDS 実効={sorted(ba.B176_SETUP_KINDS)}"
            f" ／ ★B176_MAX_DISPLACED 実効="
            f"{HeuristicProtagonist.B176_MAX_DISPLACED}"
            f" ／ B100_MIX={HeuristicProtagonist.B100_MIX}"
            f" ／ B100_THETA={HeuristicProtagonist.B100_THETA}"
            f" ／ B100_IRON_PROB={HeuristicProtagonist.B100_IRON_PROB}"
            f" ／ B100_SELF_HARM={HeuristicProtagonist.B100_SELF_HARM}"
            f" ／ B100_LINE_CAP={HeuristicProtagonist.B100_LINE_CAP}"
            f" ／ days={days}")


def _run(days: int, loops: int, val, fires: bool = False,
         line_cap: int | None = None) -> dict:
    """1点ぶんのベンチ実行（fires=True なら allocate をラップして数え上げる）。

    line_cap＝★罠1（トレッドミル）対処の併用検討＝`B100_LINE_CAP`（B-116）を
    この掃引点だけ差し替える（off 点には適用しない＝ベースラインは正典構成のまま）。"""
    old = (HeuristicProtagonist.B176_TONIGHT_PROB,
           HeuristicProtagonist.B176_BOARD_IMMINENT,
           HeuristicProtagonist.B100_LINE_CAP,
           HeuristicProtagonist.B176_MAX_DISPLACED,
           ba.B176_SETUP_KINDS)
    _apply(val)
    if val is not None and line_cap is not None:
        HeuristicProtagonist.B100_LINE_CAP = line_cap
    print(_switch_line(days), flush=True)
    n: Counter = Counter()
    by_line: Counter = Counter()      # (game, 脅威ラベル) -> 発火回数（罠1 の一次データ）
    loops_of: dict = {}               # (game, 脅威ラベル) -> 発火したループ集合
    cur = {"game": None}
    orig_alloc = ba.allocate

    def wrap_alloc(agent, view, options, threats, **kw):
        r = orig_alloc(agent, view, options, threats, **kw)
        if r is not None:
            _opt, threat, reason, _intent = r
            if _MARK in reason:
                n["b176_seats"] += 1
                n[f"kind:{threat.kind}"] += 1
                n[f"card:{_opt['card']}"] += 1
                key = (cur["game"], threat.label)
                by_line[key] += 1
                loops_of.setdefault(key, set()).add(view.get("loop"))
            elif "θ経路" in reason:
                n["theta_seats"] += 1
        return r

    import arena.benchmark as bm
    orig_ltw = bm.loops_to_win

    def wrap_ltw(script, seed, loops=8, mm_params=None):
        # run_benchmark のループが名前を渡してこないため、次善＝直近の呼び出し順で
        # benchmark_scripts の列挙と同じ (name, seed) を復元する。
        cur["game"] = f"{cur.get('_pending_name', '?')}#{seed}"
        return orig_ltw(script, seed, loops=loops, mm_params=mm_params)

    try:
        if fires:
            ba.allocate = wrap_alloc
            # 局名の追跡：benchmark_scripts をラップして名前を控える
            orig_bs = bm.benchmark_scripts

            def wrap_bs(days=3, **kw):
                for name, seed, sc in orig_bs(days=days, **kw):
                    cur["_pending_name"] = name
                    cur["game"] = f"{name}#{seed}"
                    yield name, seed, sc

            bm.benchmark_scripts = wrap_bs
            try:
                rep = run_benchmark(loops=loops, days=days, verbose=False)
            finally:
                bm.benchmark_scripts = orig_bs
        else:
            rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        ba.allocate = orig_alloc
        (HeuristicProtagonist.B176_TONIGHT_PROB,
         HeuristicProtagonist.B176_BOARD_IMMINENT,
         HeuristicProtagonist.B100_LINE_CAP,
         HeuristicProtagonist.B176_MAX_DISPLACED,
         ba.B176_SETUP_KINDS) = old
    if fires:
        rep["_fires"] = dict(n)
        rep["_fires_by_line"] = {f"{g}｜{lb}": c
                                 for (g, lb), c in sorted(by_line.items())}
        rep["_fires_loops"] = {f"{g}｜{lb}": sorted(x for x in v if x is not None)
                               for (g, lb), v in sorted(loops_of.items())}
    return rep


def _key(r: dict) -> tuple:
    return (r["script"], r["seed"])


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない（`outcomes["defense"]` が防衛数）。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def _seal_line(rep: dict) -> str:
    """★`btx5_seal` 単独行（Phase 1 §7-3 罠2＝B-66(1) 前科。現在 10/10）。"""
    rows = [r for r in rep.get("rows", []) if r["script"] == "btx5_seal"]
    if not rows:
        return "  btx5_seal: （このベンチに不在）"
    d = sum(1 for r in rows if r["outcome"] == "defense")
    return (f"  ★btx5_seal 単独: 防衛={d}／{len(rows)}  "
            + " ".join(f"s{r['seed']}:{r['loops_to_win']}"
                       f"{'' if r['outcome'] == 'defense' else '☠'}"
                       for r in sorted(rows, key=lambda x: x['seed'])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-176 Phase 2 A/B・掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,0.7,0.5,0.3")
    ap.add_argument("--fires", action="store_true",
                    help="★行為の数え上げ（当夜×帯の強制席・種別・反復）")
    ap.add_argument("--line-cap", type=int, default=None,
                    help="★罠1対処の併用検討＝ON 点だけ B100_LINE_CAP を差し替える")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    vals = [_parse(v) for v in a.values.split(",")]
    base = None
    out = []
    for v in vals:
        rep = _run(a.days, a.loops, v, fires=a.fires, line_cap=a.line_cap)
        rows = {_key(r): r["loops_to_win"] for r in rep["rows"]}
        line = f"  値={v}: {_summary(rep)}"
        if base is None:
            base = rows
        else:
            flips = [(k, base[k], rows[k]) for k in rows if base[k] != rows[k]]
            imp = [f for f in flips if f[2] < f[1]]
            reg = [f for f in flips if f[2] > f[1]]
            line += f"  flip={len(flips)}（改善{len(imp)}／退行{len(reg)}）"
            for k, b, c2 in sorted(flips):
                line += f"\n      {k[0]} s{k[1]}: {b} → {c2}"
        print(line, flush=True)
        print(_seal_line(rep), flush=True)
        if a.fires and "_fires" in rep:
            f = rep["_fires"]
            print(f"  ★fires: 当夜×帯の強制席={f.get('b176_seats', 0)}"
                  f"（θ経路席={f.get('theta_seats', 0)}） "
                  + " ".join(f"{k}={c}" for k, c in sorted(f.items())
                             if k.startswith(("kind:", "card:"))), flush=True)
            rep_loops = rep.get("_fires_loops", {})
            tread = {k: c for k, c in rep.get("_fires_by_line", {}).items()
                     if c >= 3 or len(rep_loops.get(k, ())) >= 3}
            if tread:
                print("  ★反復（同一局×同一ラベル≥3回 or ≥3ループ＝トレッドミル監視）:",
                      flush=True)
                for k, c in sorted(tread.items(), key=lambda x: -x[1]):
                    print(f"      {k}: {c}回 loops={rep_loops.get(k)}", flush=True)
        out.append({"value": ("off" if v is None else v), "summary": rep})
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
