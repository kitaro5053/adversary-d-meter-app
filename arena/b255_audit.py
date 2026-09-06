# -*- coding: utf-8 -*-
"""B-255：`btx_seal_cat` の**7全敗局の検死**（★計測のみ・AI の挙動には触れない）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-54 の申し送り1。
事前登録（発注前の検算）＝`docs/検死_B255_btx_seal_cat_7全敗_2026-08-19.md` §0。

## 対象

`sim.sample_scripts.btx_seal_cat_script`（B-254 でコーパス入り）の **seed 0〜9・loops=8**
＝`arena.benchmark` の 3日級 140 局のうち index 90〜99 と**同一条件**
（`arena/benchmark.py:56-68` の `loops_to_win` と同じ生成手順を使う）。

## この道具がやること

`arena/b249_audit.py` と同じ方式で `HeuristicProtagonist._apply_seat_flags` を
**元の実装を必ず呼ぶラッパ**に一時差し替えし、`decide` の呼び出し元フレームのローカル
（`score` / `options`）を読むだけ。採点器も合法手生成器も**書き写さない**（§72-34）。

記録するもの（席ごと）:
- 全候補の点数（本物のクロージャの値）と選択手
- `role_marginals()` の **P(クロマク=各キャラ)**（＋P(カルティスト)）
- `_purge_target` / `_guess_defeat_board` / `_kuromaku_suspects` / `_kuromaku_cands`
- 巫女の友好カウンター・各キャラのエリア・神社の板暗躍

## 挙動には触れない

`agents/` `sim/` `engine/` `rules/` を1バイトも変更しない。`verify` が
**素の対局とプローブ付き対局の (winner, loop_no, history 全行) の完全一致**を10 seed 全部で
確認する＝差し替えが起きていれば必ず落ちる。切替口は `arena.knob_audit.check_baseline`
（B-248 の一般形）と `arena.b249_audit.check_no_knob_writes` の**二重**で守る。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b255_audit verify
    python -m arena.b255_audit games  --outdir /tmp/b255
    python -m arena.b255_audit report --outdir /tmp/b255            # 全節
    python -m arena.b255_audit report --outdir /tmp/b255 --what belief|pin|purge|miko|adapt|split|supply
"""
from __future__ import annotations

import argparse
import json
import sys as _sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from arena.b249_audit import check_no_knob_writes, defaults_banner

SCRIPT_NAME = "btx_seal_cat"
SEEDS = tuple(range(10))
LOOPS = 8                       # `arena/benchmark.py` の既定
KURO = "クロマク"
CULT = "カルティスト"
SHRINE = "神社"
MIKO = "巫女"
DRIVER = "arena.b255_audit"

#: `_kuromaku_cands` / `_kuromaku_suspects` の床（`agents/heuristic_protagonist.py:1813,1815`）
KM_CAND_FLOOR = 0.3
KM_SURE_FLOOR = 0.7

#: 「黒幕を敗北ボードに近づけない／敗北ボードから剥がす」語彙（`PRIORITY` の実在キー）
KM_SEATS = ("クロマク隔離", "クロマク剥がし", "クロマク剥がし_候補")


# ---------------------------------------------------------------------------
# 健全性検査（★測定の前後で切替口が1bitも動かないこと）
# ---------------------------------------------------------------------------
def knob_audit_baseline() -> str:
    """`arena/knob_audit.check_baseline`（B-248 の一般形）を配線する（チケット要求）。"""
    try:
        from arena import knob_audit as ka
        from arena.b249_audit import BASELINE, KNOBS, _holders
    except Exception as e:                              # pragma: no cover
        return f"（`arena.knob_audit` を import できず: {e}）"
    ka.check_baseline(KNOBS, _holders(), BASELINE, driver=DRIVER)
    return "✅ `arena.knob_audit.check_baseline`（B-248 の一般形）PASS"


def script_of():
    from sim.sample_scripts import btx_seal_cat_script
    return btx_seal_cat_script()


def _okey(o: dict) -> str:
    """option を人間可読の1キーに（`card→target`）。"""
    return f'{o.get("card")}→{o.get("target")}'


def _trace(state) -> list:
    """棋譜の全行（プローブの無害性の照合用）。"""
    return [tuple(sorted((k, repr(v)) for k, v in e.items()))
            for e in state.history]


# ---------------------------------------------------------------------------
# 観測（★読み取りのみ）
# ---------------------------------------------------------------------------
#: `decide` のローカルから読む値（★読むだけ＝挙動に触れない）。
#:   `_cultist_pin_target` ＝`agents/heuristic_protagonist.py:7415-7437` の
#:   「カルティストを敗北ボードへ運ぶ手の封じ」＝**99.0 の唯一の出所**。
LOCALS = ("_cultist_pin_target", "_cultist_maybe", "danger_board", "keyperson")


def _observe(agent, view, options, best, score, rows: list, seed: int,
             loc: dict | None = None) -> None:
    marg = {}
    b = getattr(agent, "_belief", None)
    if b is not None:
        marg = b.role_marginals() or {}
    chars = {c["name"]: c for c in view["characters"]}
    scores = {}
    for o in options:
        try:
            scores[_okey(o)] = round(float(score(o)), 3)
        except Exception:                               # pragma: no cover
            pass
    rows.append({
        "seed": seed,
        "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
        "n_opt": len(options),
        "chosen": _okey(best),
        "chosen_score": scores.get(_okey(best)),
        "scores": scores,
        "km": {n: round(d.get(KURO, 0.0), 4) for n, d in marg.items()},
        "cult": {n: round(d.get(CULT, 0.0), 4) for n, d in marg.items()},
        "purge": getattr(agent, "_purge_target", None),
        "danger": _safe(agent, view),
        "km_sure": sorted(getattr(agent, "_kuromaku_suspects", ()) or ()),
        "km_cand": sorted(getattr(agent, "_kuromaku_cands", ()) or ()),
        "gw": {n: c["goodwill"] for n, c in chars.items()},
        "area": {n: c["area"] for n, c in chars.items()},
        "alive": {n: bool(c["alive"]) for n, c in chars.items()},
        "shrine": (view.get("board_anyaku") or {}).get(SHRINE, 0),
        "hand": list(view.get("hand") or []),
        "loc": {k: (sorted(v) if isinstance(v, (set, frozenset)) else v)
                for k, v in ((k, (loc or {}).get(k)) for k in LOCALS)},
        "mm_char_now": sorted((loc or {}).get("mm_char_now") or ()),
        "mm_board_now": sorted((loc or {}).get("mm_board_now") or ()),
    })


def _safe(agent, view):
    try:
        return agent._guess_defeat_board(view)
    except Exception as e:                              # pragma: no cover
        return f"ERR {type(e).__name__}: {e}"


def _run(seed: int, rows: list | None):
    """1局を回す（`arena/benchmark.loops_to_win` と同一手順）。rows=None なら素の対局。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    HP = HeuristicProtagonist
    orig = HP._apply_seat_flags

    def _wrapped(agent, view, best):
        try:
            fr = _sys._getframe(1)
            if fr.f_code.co_name == "decide":
                loc = fr.f_locals
                sc, op = loc.get("score"), loc.get("options")
                if sc is not None and op:
                    _observe(agent, view, op, best, sc, rows, seed, loc)
        except Exception as e:                          # pragma: no cover
            rows.append({"seed": seed, "★観測例外": f"{type(e).__name__}: {e}"})
        return orig(agent, view, best)

    if rows is not None:
        HP._apply_seat_flags = _wrapped
    try:
        hp = HP(seed)
        state, _ = run_game(replace(script_of(), loops=LOOPS),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
    finally:
        if rows is not None:
            HP._apply_seat_flags = orig
    return state


def outcome_of(state) -> tuple[int, str]:
    """`arena/benchmark.loops_to_win` と同じ判定（★書き写しでなく同じ式）。"""
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return LOOPS + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return LOOPS + 1, "loss"


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------
def cmd_defaults(a) -> int:
    before = defaults_banner()
    print(f"[b255] {knob_audit_baseline()}")
    check_no_knob_writes(before, driver=DRIVER)
    print("[b255] ✅ 切替口の束は不変（まだ何も回していない＝自明ケース）")
    return 0


def cmd_verify(a) -> int:
    """★プローブの無害性＝素の対局とプローブ付き対局が全 bit 一致することの証明。"""
    before = defaults_banner()
    print(f"[b255] {knob_audit_baseline()}")
    bad = 0
    print(f"{'seed':>4} {'素':>16} {'プローブ':>16}  一致")
    for seed in SEEDS:
        s0 = _run(seed, None)
        rows: list = []
        s1 = _run(seed, rows)
        a0, a1 = outcome_of(s0), outcome_of(s1)
        same = (a0 == a1 and s0.winner == s1.winner
                and s0.loop_no == s1.loop_no and _trace(s0) == _trace(s1))
        bad += 0 if same else 1
        print(f"{seed:>4} {str(a0):>16} {str(a1):>16}  {'✅' if same else '★不一致'}"
              f"  席{len(rows)}")
    check_no_knob_writes(before, driver=DRIVER)
    print(f"[b255] 切替口の束は測定の前後で不変 ✅／食い違い {bad} 件")
    return 0 if bad == 0 else 1


def cmd_games(a) -> int:
    before = defaults_banner()
    print(f"[b255] {knob_audit_baseline()}")
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    for seed in SEEDS:
        rows: list = []
        st = _run(seed, rows)
        ltw, oc = outcome_of(st)
        hist = [dict(e) for e in st.history]
        (out / f"g{seed}.json").write_text(
            json.dumps({"seed": seed, "loops_to_win": ltw, "outcome": oc,
                        "rows": rows, "history": hist},
                       ensure_ascii=False), encoding="utf-8")
        summary.append({"seed": seed, "loops_to_win": ltw, "outcome": oc,
                        "n_seat": len(rows)})
        print(f"  seed{seed}: {ltw} {oc}  席{len(rows)}", flush=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False),
                                      encoding="utf-8")
    check_no_knob_writes(before, driver=DRIVER)
    n_def = sum(1 for s in summary if s["outcome"] == "defense")
    print(f"[b255] 防衛 {n_def}/10 ／ 平均 loops_to_win "
          f"{sum(s['loops_to_win'] for s in summary) / len(summary):.2f}")
    print("[b255] ✅ 切替口の束は測定の前後で不変")
    return 0


def _load(outdir: str):
    out = Path(outdir)
    games = []
    for seed in SEEDS:
        p = out / f"g{seed}.json"
        if p.exists():
            games.append(json.loads(p.read_text(encoding="utf-8")))
    if not games:
        raise SystemExit(f"★{outdir} に g*.json が無い（先に games を回す）")
    return games


# --------------------------------------------------------------- 節 (1a) belief
def rep_belief(games) -> str:
    L = ["## (1a) P(クロマク=ご神木) 対 P(クロマク=男子学生)：ループごとの推移",
         "",
         "各ループの**最終席**での値（そのループで AI が最後に見た推定）。真値＝**男子学生**。",
         "",
         "| seed | 結末 | " + " | ".join(f"L{i}" for i in range(1, LOOPS + 1)) + " |",
         "|---|---|" + "---|" * LOOPS]
    top_counter = Counter()
    over_cand = Counter()
    for g in games:
        last = {}
        for r in g["rows"]:
            if "km" in r:
                last[r["loop"]] = r
        cells = []
        for lp in range(1, LOOPS + 1):
            r = last.get(lp)
            if r is None:
                cells.append("—")
                continue
            km = r["km"]
            gs, ds = km.get("ご神木", 0.0), km.get("男子学生", 0.0)
            top = max(km, key=lambda n: (km[n], n)) if km else "—"
            top_counter[top] += 1
            for n, p in km.items():
                if p >= KM_CAND_FLOOR:
                    over_cand[n] += 1
            cells.append(f"ご{gs:.2f}/男{ds:.2f}" + ("★" if gs > ds else ""))
        L.append(f"| {g['seed']} | {g['outcome']} | " + " | ".join(cells) + " |")
    L += ["", f"★=P(クロマク=ご神木) が P(クロマク=男子学生) を**上回った**ループ。", "",
          "**ループ末の argmax（全 seed×ループ）**＝" +
          "／".join(f"{n}:{c}" for n, c in top_counter.most_common()),
          "",
          f"**p ≥ {KM_CAND_FLOOR}（`_kuromaku_cands` の床）に届いたループ数**＝" +
          ("／".join(f"{n}:{c}" for n, c in over_cand.most_common()) or "**0件（誰も届かない）**")]
    # 席単位の全数（ループ末に限らない）
    tot = Counter()
    win = Counter()
    for g in games:
        for r in g["rows"]:
            km = r.get("km") or {}
            if not km:
                continue
            tot["席"] += 1
            gs, ds = km.get("ご神木", 0.0), km.get("男子学生", 0.0)
            if gs > ds:
                win["ご神木>男子学生"] += 1
            elif ds > gs:
                win["男子学生>ご神木"] += 1
            else:
                win["同値"] += 1
            top = max(km, key=lambda n: (km[n], n))
            win[f"argmax={top}"] += 1
    L += ["", "**席単位の全数**（10局・全席）＝" + "／".join(
        f"{k}:{v}" for k, v in [("席", tot['席'])] + sorted(win.items()))]
    return "\n".join(L)


# --------------------------------------------------------------- 節 (1b) 席
def rep_pin(games) -> str:
    from agents.heuristic_protagonist import PRIORITY
    L = ["## (1b) 「黒幕を敗北ボードに近づけない手」の席ごとの点数と順位", ""]
    # 1. クロマク語彙の席が1枚でも立ったか（PRIORITY 値との一致で数える）
    vals = {k: PRIORITY[k] for k in KM_SEATS if k in PRIORITY}
    L.append("**クロマク向け語彙の点数（`agents/heuristic_protagonist.py` の `PRIORITY`）**＝"
             + "／".join(f"{k}={v}" for k, v in vals.items()))
    hit = Counter()
    for g in games:
        for r in g["rows"]:
            for key, sc in (r.get("scores") or {}).items():
                for name, v in vals.items():
                    if abs(sc - v) < 1e-9:
                        hit[f"{name}: {key}"] += 1
    L += ["", "**その点数がついた (席, 手) の全数**＝"
          + (("\n" + "\n".join(f"- {k}: {c} 席" for k, c in hit.most_common()))
             if hit else "**0件＝クロマク向けの席は10局のどこにも1枚も立たなかった**")]
    # 2. 移動禁止 の全数（床の確認＝副指標）
    pin = defaultdict(Counter)
    rank_rows = []
    for g in games:
        for r in g["rows"]:
            sc = r.get("scores") or {}
            if not sc:
                continue
            order = sorted(sc.items(), key=lambda kv: -kv[1])
            best = order[0][1]
            for key, v in sc.items():
                if key.startswith("移動禁止→"):
                    pin[key][round(v, 2)] += 1
                    rk = 1 + sum(1 for _k, w in sc.items() if w > v)
                    rank_rows.append((key, v, rk, len(sc), best))
    L += ["", "### 副指標＝`移動禁止→*` の点数分布（★§0 検算1(1b) で「構造的にほぼ床固定」と予測した量）", "",
          "| 手 | 点数の分布（点:席数） | 最良順位 |", "|---|---|---|"]
    for key in sorted(pin):
        rk = min((r for k, _v, r, _n, _b in rank_rows if k == key), default=None)
        L.append(f"| {key} | " + "  ".join(f"{p}:{c}" for p, c in sorted(pin[key].items()))
                 + f" | {rk} |")
    n_played = sum(1 for g in games for r in g["rows"]
                   if str(r.get("chosen", "")).startswith("移動禁止→"))
    L += ["", f"**実際に `移動禁止` が打たれた席**＝{n_played} 件"]
    played = Counter(r["chosen"] for g in games for r in g["rows"]
                     if str(r.get("chosen", "")).startswith("移動禁止→"))
    if played:
        L.append("（内訳＝" + "／".join(f"{k}:{c}" for k, c in played.most_common()) + "）")
    return "\n".join(L)


# --------------------------------------------------------------- 節 (1c) purge
def rep_purge(games) -> str:
    from engine.data import GOODWILL_ABILITIES
    from sim.abilities import ABILITY_IMPL
    L = ["## (1c) `_purge_target` は誰を選んでいるか", "",
         "**キャストの友好能力と `board_scope`**（`sim/abilities.py:500-502` が単一ソース）：", "",
         "| キャラ | 能力 | ♡ | 実装 | `board_scope` | 板に触れるか |", "|---|---|---|---|---|---|"]
    cast = script_of().cast
    for n in cast:
        for ab in GOODWILL_ABILITIES.get(n, []):
            impl = ABILITY_IMPL.get((n, ab["name"]))
            scope = (impl or {}).get("board_scope")
            L.append(f"| {n} | {ab['name']} | {ab.get('hearts')} | "
                     f"{'○' if impl else '×'} | {scope or '—'} | "
                     f"{'★○' if scope else '×'} |")
    by = Counter()
    by_day = defaultdict(Counter)
    for g in games:
        for r in g["rows"]:
            if "purge" not in r:
                continue
            by[str(r["purge"])] += 1
            by_day[r["day"]][str(r["purge"])] += 1
    L += ["", "**`_purge_target` の全数（10局・全席）**＝"
          + "／".join(f"{k}:{v}" for k, v in by.most_common()), "",
          "**日別**＝" + "／".join(
              f"D{d}[" + " ".join(f"{k}:{v}" for k, v in c.most_common()) + "]"
              for d, c in sorted(by_day.items()))]
    # 巫女が purge から外れている席で 友好+2→巫女 が 8.0 に落ちているか
    starve = Counter()
    for g in games:
        for r in g["rows"]:
            sc = r.get("scores") or {}
            v = sc.get(f"友好+2→{MIKO}")
            if v is None:
                continue
            starve[f"purge={r.get('purge')} → 友好+2→巫女={v}"] += 1
    L += ["", "**`友好+2→巫女` の点数（`_purge_target` 別・`agents/heuristic_protagonist.py:8444-8449`）**："]
    for k, c in starve.most_common(12):
        L.append(f"- {k}  … {c} 席")
    return "\n".join(L)


# --------------------------------------------------------------- 節 (3) 巫女
def rep_miko(games) -> str:
    L = ["## (3) 巫女の♡3 到達（`神社の暗躍除去` の解禁）", "",
         "| seed | 結末 | ループ別の巫女♡最大値 | ♡3 到達L | 神社に居た日/全日 | 除去の実行回数 |",
         "|---|---|---|---|---|---|"]
    agg = Counter()
    for g in games:
        mx = {}
        shrine_days = set()
        all_days = set()
        for r in g["rows"]:
            gw = (r.get("gw") or {}).get(MIKO)
            if gw is None:
                continue
            lp, d = r["loop"], r["day"]
            mx[lp] = max(mx.get(lp, 0), gw)
            all_days.add((lp, d))
            if (r.get("area") or {}).get(MIKO) == SHRINE:
                shrine_days.add((lp, d))
        reach = [lp for lp, v in sorted(mx.items()) if v >= 3]
        used = sum(1 for e in g["history"]
                   if e.get("event") == "anyaku" and e.get("target") == SHRINE
                   and (e.get("delta") or 0) < 0)
        agg["♡3 到達ループ"] += len(reach)
        agg["全ループ"] += len(mx)
        agg["除去実行"] += used
        L.append(f"| {g['seed']} | {g['outcome']} | "
                 + " ".join(f"L{lp}:{v}" for lp, v in sorted(mx.items()))
                 + f" | {reach or '**なし**'} | {len(shrine_days)}/{len(all_days)} | {used} |")
    L += ["", f"**合計**＝♡3 到達 {agg['♡3 到達ループ']} / {agg['全ループ']} ループ"
          f"（{100 * agg['♡3 到達ループ'] / max(agg['全ループ'], 1):.0f}%）／"
          f"神社の暗躍除去の実行 {agg['除去実行']} 回"]
    # 未到達の原因分類
    L += ["", "### ♡3 未到達の原因（内訳）", "",
          "| seed | loop | 巫女♡の推移(日別) | 友好投資が巫女へ向いた席 | 他所へ向いた席 |",
          "|---|---|---|---|---|"]
    for g in games:
        per = defaultdict(list)
        to_miko = Counter()
        to_other = Counter()
        for r in g["rows"]:
            gw = (r.get("gw") or {}).get(MIKO)
            if gw is not None:
                per[r["loop"]].append((r["day"], gw))
            ch = str(r.get("chosen", ""))
            if ch.startswith("友好+"):
                (to_miko if ch.endswith(f"→{MIKO}") else to_other)[r["loop"]] += 1
        for lp in sorted(per):
            vals = dict(per[lp])
            if max(vals.values(), default=0) >= 3:
                continue
            L.append(f"| {g['seed']} | L{lp} | "
                     + " ".join(f"D{d}:{v}" for d, v in sorted(vals.items()))
                     + f" | {to_miko[lp]} | {to_other[lp]} |")
    return "\n".join(L)


# --------------------------------------------------------------- 節 (4) 適応
def rep_adapt(games) -> str:
    L = ["## (4) L2 以降で AI の手は変化しているか（適応の有無）", "",
         "各ループの主人公の手を `(day, seat, card→target)` の列にして、L1 と比較する。", "",
         "| seed | 結末 | L1 と完全一致したループ | 相異なる手順の種類数 |", "|---|---|---|---|"]
    same_tot = 0
    loops_tot = 0
    for g in games:
        seq = defaultdict(list)
        for r in g["rows"]:
            if "chosen" in r:
                seq[r["loop"]].append((r["day"], r["seat"], r["chosen"]))
        base = tuple(seq.get(1, ()))
        same = [lp for lp in sorted(seq) if lp != 1 and tuple(seq[lp]) == base]
        kinds = len({tuple(v) for v in seq.values()})
        same_tot += len(same)
        loops_tot += max(len(seq) - 1, 0)
        L.append(f"| {g['seed']} | {g['outcome']} | {same or 'なし'} | {kinds} |")
    L += ["", f"**合計**＝L1 と完全一致したループ {same_tot} / {loops_tot}"
          "（L1 を除く全ループ）"]
    return "\n".join(L)


# --------------------------------------------------------------- 節 (2) 3勝7敗
def rep_split(games) -> str:
    L = ["## (2) 防衛できた3局と全敗7局の差", "",
         "| seed | 結末 | L | 神社の暗躍(ループ末) | 巫女♡max | 巫女神社日 | 除去 | 邪気の汚染の発生 | 男子学生が神社に居た日 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for g in games:
        # ループ1のみ（防衛局は L2 で終わる＝L1/L2 だけ見る）
        per = defaultdict(dict)
        for r in g["rows"]:
            lp = r["loop"]
            per[lp].setdefault("miko", 0)
            per[lp]["miko"] = max(per[lp]["miko"], (r.get("gw") or {}).get(MIKO, 0))
            per[lp]["shrine_days"] = per[lp].get("shrine_days", set()) | (
                {r["day"]} if (r.get("area") or {}).get(MIKO) == SHRINE else set())
            per[lp]["ds_days"] = per[lp].get("ds_days", set()) | (
                {r["day"]} if (r.get("area") or {}).get("男子学生") == SHRINE else set())
        inc = Counter()
        rm = Counter()
        endv = {}
        for e in g["history"]:
            lp = e.get("loop")
            if e.get("event") == "incident" and e.get("name") == "邪気の汚染":
                inc[lp] += 1
            if e.get("event") == "anyaku" and e.get("target") == SHRINE \
                    and (e.get("delta") or 0) < 0:
                rm[lp] += 1
            if e.get("event") == "loop_end":
                endv[lp] = e.get("board_anyaku", {}).get(SHRINE) if isinstance(
                    e.get("board_anyaku"), dict) else None
        for lp in sorted(per):
            L.append(f"| {g['seed']} | {g['outcome'] if lp == min(per) else ''} | L{lp} | "
                     f"{endv.get(lp, '—')} | {per[lp]['miko']} | "
                     f"{sorted(per[lp].get('shrine_days', ()))} | {rm[lp]} | "
                     f"{inc[lp]} | {sorted(per[lp].get('ds_days', ()))} |")
    return "\n".join(L)


# --------------------------------------------------------------- 節 供給路
def rep_supply(games) -> str:
    """神社への暗躍供給を**事象ごと**に数え上げる（供給路の実測内訳）。

    ★注意＝**黒猫の特性1（ループ開始時に神社へ暗躍+1）はイベントを出さない**
    （`sim/state.py:519-520` が `prepare_loop` の中で `board_anyaku` に直接足す）。
    ∴ 履歴の `anyaku` イベントを数えるだけでは**1/ループ取りこぼす**。
    ここでは各ループ D1 の席が見た `board_anyaku["神社"]`（＝ループ開始値）で補う。
    """
    L = ["## 供給路の数え上げ（神社への暗躍の出入り）", "",
         "★黒猫の特性1（ループ開始 +1）は**履歴イベントを出さない**"
         "（`sim/state.py:519-520`）＝D1 開始時の板の値で読む。", "",
         "| seed | 結末 | ループ開始+1(黒猫) | 行動解決(札) | 脚本家能力(クロマク) | "
         "邪気の汚染(発生) | 除去(−) | ループ末の神社(平均) |",
         "|---|---|---|---|---|---|---|---|"]
    tot = Counter()
    for g in games:
        c = Counter()
        for r in g["rows"]:
            if r.get("day") == 1 and r.get("seat") == "p1":
                c["黒猫"] += r.get("shrine", 0)
        for e in g["history"]:
            if e.get("event") == "anyaku" and e.get("target") == SHRINE:
                d = e.get("delta") or 0
                ph = e.get("phase")
                if d < 0:
                    c["−"] += -d
                elif ph == "mastermind_ability":
                    c["mm能力"] += d
                elif ph == "action_resolution":
                    c["行動解決"] += d
                else:
                    c[f"+{ph}"] += d
            if e.get("event") == "incident" and e.get("name") == "邪気の汚染" \
                    and e.get("occurs"):
                c["邪気"] += 1
        ends = [e["board_anyaku"][SHRINE] for e in g["history"]
                if e.get("event") == "loop_board"]
        for k in c:
            tot[k] += c[k]
        L.append(f"| {g['seed']} | {g['outcome']} | {c['黒猫']} | {c['行動解決']} | "
                 f"{c['mm能力']} | {c['邪気']} | {c['−']} | "
                 f"{(sum(ends) / len(ends)):.1f}" if ends else "—")
        L[-1] += " |"
    L += ["", "**合計**＝" + "／".join(f"{k}:{v}" for k, v in sorted(tot.items()))]
    return "\n".join(L)


# --------------------------------------------------------------- 節 カルティスト経路
def rep_cult(games) -> str:
    """★99.0（`移動禁止→男子学生`）の**唯一の出所**＝カルティスト経路の数え上げ。

    `agents/heuristic_protagonist.py:7415-7437`＝「カルティストを敗北ボードへ運ぶ手の封じ」。
    発火条件は `tgt == _cultist_pin_target`＝**カルティスト疑い**であって**クロマク疑いではない**。
    """
    L = ["## ★99.0 の出所＝カルティスト経路（クロマク経路ではない）", "",
         "`agents/heuristic_protagonist.py:7415-7437`（`return 99.0` は `:7437`）。",
         "発火条件＝`tgt == _cultist_pin_target`。真のカルティスト＝**転校生**（D3 登場）。", "",
         "| seed | 結末 | `_cultist_pin_target` の全数 | L2D1 の値 | L2D1 `移動禁止→男子学生` |",
         "|---|---|---|---|---|"]
    for g in games:
        c = Counter()
        first = None
        for r in g["rows"]:
            t = (r.get("loc") or {}).get("_cultist_pin_target")
            c[str(t)] += 1
            if r["loop"] == 2 and r["day"] == 1 and first is None:
                first = (t, (r.get("scores") or {}).get("移動禁止→男子学生"))
        L.append(f"| {g['seed']} | {g['outcome']} | "
                 + "／".join(f"{k}:{v}" for k, v in c.most_common())
                 + f" | {first[0] if first else '—'} | {first[1] if first else '—'} |")
    # P(カルティスト) の推移
    L += ["", "**P(カルティスト=各キャラ) のループ末**（真値＝転校生）", "",
          "| seed | " + " | ".join(f"L{i}" for i in range(1, LOOPS + 1)) + " |",
          "|---|" + "---|" * LOOPS]
    for g in games:
        last = {}
        for r in g["rows"]:
            if r.get("cult"):
                last[r["loop"]] = r["cult"]
        cells = []
        for lp in range(1, LOOPS + 1):
            d = last.get(lp)
            if not d:
                cells.append("—")
                continue
            top = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
            cells.append(" ".join(f"{n}{p:.2f}" for n, p in top))
        L.append(f"| {g['seed']} | " + " | ".join(cells) + " |")
    return "\n".join(L)


SECTIONS = {"belief": rep_belief, "pin": rep_pin, "purge": rep_purge,
            "miko": rep_miko, "adapt": rep_adapt, "split": rep_split,
            "supply": rep_supply, "cult": rep_cult}


def cmd_report(a) -> int:
    games = _load(a.outdir)
    want = a.what.split(",") if a.what else list(SECTIONS)
    outs = []
    for w in want:
        if w not in SECTIONS:
            raise SystemExit(f"★未知の節: {w}（{'/'.join(SECTIONS)}）")
        outs.append(SECTIONS[w](games))
    print("\n\n".join(outs))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-255：btx_seal_cat 7全敗局の検死（計測のみ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("defaults").set_defaults(fn=cmd_defaults)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    p = sub.add_parser("games"); p.add_argument("--outdir", required=True)
    p.set_defaults(fn=cmd_games)
    p = sub.add_parser("report"); p.add_argument("--outdir", required=True)
    p.add_argument("--what", default="")
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
