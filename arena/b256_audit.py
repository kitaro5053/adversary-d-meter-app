# -*- coding: utf-8 -*-
"""B-256：不安3に**算術的に届かない**ウイルス試験席の数え上げ（★フェーズ0＝実装前の検算）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-58／事前登録＝
`docs/測定_B256_不安3に届かない席_2026-08-19.md` §0。

## 何を測るか（事前登録 §0-2）

| 記号 | 定義 |
|---|---|
| **N_tgt** | `_virus_test_targets` に載った (局, L, D, 対象) の延べ数 |
| **N_lt3** | そのうち到達可能上限 < 3 の数（`U_p`＝主人公のみ／`U_pm`＝脚本家込みの2版） |
| **N_played** | そのうち**実際に `不安+1`(キャラ) が当該対象へ打たれた**席＝止める価値のある席 |
| **E** | `U_p < 3` と判定した (局, L, 対象) で、**その後そのループ内で不安が実際に3以上へ達した**回数（＝上限の破れ） |
| **S** | N_played の席で、その対象を `_virus_test_targets` から外して**本物の採点器**で argmax を取り直した手（＝★空いた席の次点） |

到達可能上限（§0-1）：`R = days_per_loop − day + 1`（今日を含む残り日数）に対し
`U_p = u + R`（主人公陣営は1キャラ1日1枚まで＝`rules/00:104`＋`rules/10:81`）／
`U_pm = u + 2R`（脚本家も毎日1枚重ねる仮定を「置いた」版）。

## 挙動には触れない

`agents/` `sim/` `engine/` の既定値を一切書き換えない。観測は
`agents.heuristic_protagonist.B100_HOOK`（計測専用フック・戻り値不使用）と
`sim.flow.run_day` の**読み取り専用ラッパ**（不安のスナップショット）だけで行う。
★次点 S の算出は `_virus_test_targets` を**フック内で一時的に退避→復元**する
（フックは `_apply_seat_flags` の**後**＝手は既に確定済み＝棋譜に影響しない。
`verify` サブコマンドで棋譜 bit 一致を実測する）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b256_audit seats  --days 3 --pick all --outdir /tmp/b256/d3
    python -m arena.b256_audit report --outdir /tmp/b256/d3
    python -m arena.b256_audit verify --days 3 --pick random_BTX:0-2
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import replace

from arena.b249_audit import (  # noqa: F401  （★B-249/B-250 を書き換えずに使う）
    _okey, check_no_knob_writes, defaults_banner,
)
from arena.b250_audit import _truth_of, parse_pick

UNREST_PLUS = "不安+1"
#: belief が不安を情報に使う経路のしきい値（`agents/belief.py:1157-1168` / `:1131-1141`）。
UNREST_GOAL = 3


def knob_baseline_banner() -> str:
    """`arena/knob_audit.check_baseline` の一般形（条件表なし＝退化形）を配線する。"""
    from arena import knob_audit
    knob_audit.check_baseline({}, {}, (), driver="arena.b256_audit")
    return "knob_audit.check_baseline（空の条件表＝退化形）✅"


class Rec:
    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.tgt: list[dict] = []      # `_virus_test_targets` に載った延べ (L,D,対象)
        self.un: list[dict] = []       # ★`不安+1`(キャラ) の実打席（vt の在否を問わない）
        self.snap: dict = {}           # "L:D" -> {name: unrest}（ターン終了後）
        self.mismatch: list[dict] = []
        self.meta: dict = {}

    def to_json(self) -> dict:
        return {"counts": dict(self.c), "tgt": self.tgt, "un": self.un, "snap": self.snap,
                "mismatch": self.mismatch, "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> "Rec":
        r = cls()
        r.c.update(d.get("counts", {}))
        r.tgt = list(d.get("tgt", []))
        r.un = list(d.get("un", []))
        r.snap = dict(d.get("snap", {}))
        r.mismatch = list(d.get("mismatch", []))
        r.meta = dict(d.get("meta", {}))
        return r


def bounds(u: int, day: int, days_per_loop: int) -> tuple[int, int, int]:
    """(R, U_p, U_pm) を返す＝事前登録 §0-1 の到達可能上限。"""
    r = max(0, days_per_loop - day + 1)
    return r, u + r, u + 2 * r


def _hook_factory(rec: Rec, script_obj, name: str, seed: int):
    """`B100_HOOK` に挿す観測子（★戻り値は使われない＝棋譜に影響しない）。"""
    def _hook(agent, view, options, best, score):
        rec.c["席（set_card 決定）"] += 1
        vt = sorted(getattr(agent, "_virus_test_targets", ()) or ())
        if best["card"] == UNREST_PLUS and best.get("target_kind") == "character":
            rec.c["不安+1(キャラ)の実打席（全体）"] += 1
            _c = next((o for o in view["characters"]
                       if o["name"] == best.get("target")), None)
            _u = int((_c or {}).get("unrest", 0))
            _r, _up, _upm = bounds(_u, view.get("day"), view.get("days_per_loop"))
            rec.un.append({"script": name, "seed": seed, "loop": view.get("loop"),
                           "day": view.get("day"), "target": best.get("target"),
                           "u": _u, "U_p": _up, "U_pm": _upm,
                           "in_vt": best.get("target") in vt})
        if not vt:
            return
        lp, dy = view.get("loop"), view.get("day")
        dpl = view.get("days_per_loop")
        alive = {o["name"]: o for o in view["characters"] if o.get("alive")}
        card, tgt, kind = best["card"], best.get("target"), best.get("target_kind")
        played_here = (card == UNREST_PLUS and kind == "character")
        # ★実装の意味論と揃えた「外す集合」＝**U_p<3 の対象を全部**外す
        drop_all = set()
        for n in vt:
            c0 = alive.get(n)
            if c0 is not None and bounds(int(c0.get("unrest", 0)), dy, dpl)[1] < UNREST_GOAL:
                drop_all.add(n)
        for n in vt:
            c = alive.get(n)
            if c is None:
                rec.c["対象が死亡/盤外"] += 1
                continue
            u = int(c.get("unrest", 0))
            r, up, upm = bounds(u, dy, dpl)
            row = {"script": name, "seed": seed, "loop": lp, "day": dy,
                   "seat": view.get("seat"), "target": n, "u": u, "R": r,
                   "U_p": up, "U_pm": upm,
                   "played": bool(played_here and tgt == n),
                   "true_role": _truth_of(script_obj, n)}
            if row["played"]:
                row.update(_next_best(rec, agent, options, best, score, {n}))
                if drop_all != {n}:
                    row.update({("ALL_" + k): v for k, v in _next_best(
                        rec, agent, options, best, score, drop_all).items()})
                else:
                    row.update({("ALL_" + k): row[k] for k in
                                ("S_card", "S_target", "S_kind", "S_score",
                                 "S_is_same")})
                row["drop_all"] = sorted(drop_all)
                row["vt"] = list(vt)
            rec.tgt.append(row)
    return _hook


def _next_best(rec: Rec, agent, options, best, score, drop: set) -> dict:
    """★空いた席の次点＝`drop` を `_virus_test_targets` から外して**本物の採点器**で argmax。

    フックは手が確定した**後**に呼ばれる（`_apply_seat_flags` の後）ので、ここでの
    一時的な属性差し替えは棋譜に影響しない（`verify` で bit 一致を実測する）。
    """
    out: dict = {}
    orig = getattr(agent, "_virus_test_targets", set())
    try:
        base = {_okey(o): float(score(o)) for o in options}
        # ★自己検証＝本物の採点器の再入性（席の外から呼び直すので、同値が返ることが前提）
        for o in options:
            if float(score(o)) != base[_okey(o)]:
                rec.mismatch.append({"opt": list(_okey(o))})
                rec.c["★採点器が再入で食い違った席"] += 1
                break
        agent._virus_test_targets = {x for x in orig if x not in drop}
        after = [(o, float(score(o))) for o in options]
    finally:
        agent._virus_test_targets = orig
    # ★復元の検証＝復元後の採点がベースラインと完全一致すること（副作用ゼロの確認）
    for o in options:
        if float(score(o)) != base[_okey(o)]:
            rec.mismatch.append({"restore": list(_okey(o))})
            rec.c["★退避→復元で採点が戻らなかった席"] += 1
            break
    top = max(after, key=lambda t: t[1])
    played_after = next(s for o, s in after if _okey(o) == _okey(best))
    out["S_card"] = top[0]["card"]
    out["S_target"] = top[0].get("target")
    out["S_kind"] = top[0].get("target_kind")
    out["S_score"] = round(top[1], 4)
    out["played_score"] = round(base[_okey(best)], 4)
    out["played_score_after"] = round(played_after, 4)
    out["S_is_same"] = bool(_okey(top[0]) == _okey(best))
    return out


def _run(script_obj, name: str, seed: int, loops: int, rec: Rec | None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hp_mod
    from sim import run_game
    from sim import flow as flow_mod

    orig_hook = hp_mod.B100_HOOK
    orig_day = flow_mod.run_day

    def _day(state, decide, human_seats=frozenset()):
        out = orig_day(state, decide, human_seats)
        rec.snap[f"{state.loop_no}:{state.day}"] = {
            n: c.unrest for n, c in state.characters.items()}
        return out

    if rec is not None:
        hp_mod.B100_HOOK = _hook_factory(rec, script_obj, name, seed)
        flow_mod.run_day = _day
    try:
        agent = HeuristicProtagonist(seed)
        state, _ = run_game(replace(script_obj, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": agent, "p2": agent, "p3": agent})
    finally:
        hp_mod.B100_HOOK = orig_hook
        flow_mod.run_day = orig_day
    if rec is not None:
        rec.meta.update({"winner": state.winner, "loop_no": state.loop_no,
                         "days_per_loop": script_obj.days_per_loop})
    return state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_seats(a) -> int:
    before = defaults_banner()
    print(f"[b256] {knob_baseline_banner()}")
    from arena import b256_ab
    old = b256_ab._apply(b256_ab.cond_knobs(a.on), None)
    print(f"[b256] 条件＝{a.on}（切替口は測定の最後に必ず復元する）")
    games = parse_pick(a.pick, a.days)
    os.makedirs(a.outdir, exist_ok=True)
    for name, seed, sc in games:
        p = os.path.join(a.outdir, f"{name}__{seed}.json")
        if os.path.exists(p) and not a.force:
            continue
        rec = Rec()
        st = _run(sc, name, seed, a.loops, rec)
        d = rec.to_json()
        d["meta"].update({"script": name, "seed": seed, "days": a.days,
                          "loops": a.loops, "winner": st.winner,
                          "loop_no": st.loop_no})
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        print(f"  {name}#{seed}: 席{rec.c['席（set_card 決定）']} "
              f"対象延べ{len(rec.tgt)} 食い違い{len(rec.mismatch)} "
              f"winner={st.winner} L{st.loop_no}")
    b256_ab._restore(old)
    check_no_knob_writes(before, driver="arena.b256_audit")
    print("[b256] 切替口の健全性検査 ✅（測定前後で1bitも動いていない）")
    return 0


def cmd_verify(a) -> int:
    """★プローブの無害性＝観測あり／なしで棋譜が1ビットも動かないことを確認する。"""
    games = parse_pick(a.pick, a.days)
    bad = 0
    for name, seed, sc in games:
        s0 = _run(sc, name, seed, a.loops, None)
        s1 = _run(sc, name, seed, a.loops, Rec())
        h0 = json.dumps(s0.history, ensure_ascii=False, sort_keys=True)
        h1 = json.dumps(s1.history, ensure_ascii=False, sort_keys=True)
        ok = (h0 == h1 and s0.winner == s1.winner and s0.loop_no == s1.loop_no)
        print(f"  {name}#{seed}: 棋譜一致={ok}")
        bad += 0 if ok else 1
    print(f"[b256] プローブの無害性: 不一致 {bad} 件")
    return 1 if bad else 0


def _load(outdir: str) -> list[Rec]:
    out = []
    for fn in sorted(os.listdir(outdir)):
        if fn.endswith(".json"):
            with open(os.path.join(outdir, fn), encoding="utf-8") as f:
                out.append(Rec.from_json(json.load(f)))
    return out


def _pct(a: int, b: int) -> str:
    return f"{100.0*a/b:.1f}%" if b else "—"


def cmd_report(a) -> int:
    recs = _load(a.outdir)
    rows = [r for rec in recs for r in rec.tgt]
    mism = sum(len(r.mismatch) for r in recs)
    print(f"== {a.outdir} ==  局数 {len(recs)} ／ 自己検証の食い違い {mism} 件")
    print(f"[N_tgt] `_virus_test_targets` に載った延べ (局,L,D,席,対象) = {len(rows)}")
    lt_p = [r for r in rows if r["U_p"] < UNREST_GOAL]
    lt_pm = [r for r in rows if r["U_pm"] < UNREST_GOAL]
    print(f"[N_lt3] ★U_p<3（主人公のみ・仮定なし） = {len(lt_p)}（{_pct(len(lt_p), len(rows))}）"
          f" ／ U_pm<3（脚本家込みの仮定を置いた版） = {len(lt_pm)}"
          f"（{_pct(len(lt_pm), len(rows))}）")
    played = [r for r in rows if r["played"]]
    pl_p = [r for r in played if r["U_p"] < UNREST_GOAL]
    pl_pm = [r for r in played if r["U_pm"] < UNREST_GOAL]
    print(f"[N_played] `不安+1`(キャラ) が当該対象へ実打された席 = {len(played)}")
    print(f"    ★★うち U_p<3（＝止める価値のある席） = {len(pl_p)}"
          f"（{_pct(len(pl_p), len(played))}） ／ U_pm<3 = {len(pl_pm)}")
    # 内訳（日別・現在不安別）
    print("    内訳(U_p<3 の実打席): "
          + " ".join(f"D{d}:{n}" for d, n in sorted(Counter(
              r["day"] for r in pl_p).items())))
    print("    内訳(現在の不安 u): "
          + " ".join(f"u={u}:{n}" for u, n in sorted(Counter(
              r["u"] for r in pl_p).items())))
    print("    ★対象の真の役職: "
          + " ".join(f"{k}:{v}" for k, v in Counter(
              r["true_role"] for r in pl_p).most_common()))
    # E＝上限の破れ
    brk = 0
    per_loop: dict = {}
    for rec in recs:
        snaps = rec.snap
        for r in rec.tgt:
            if r["U_p"] >= UNREST_GOAL:
                continue
            key = (id(rec), r["loop"], r["target"])
            if key in per_loop:
                continue
            mx = 0
            for k, m in snaps.items():
                lp, _, dy = k.partition(":")
                if int(lp) == r["loop"] and int(dy) >= r["day"]:
                    mx = max(mx, int(m.get(r["target"], 0)))
            per_loop[key] = mx
            if mx >= UNREST_GOAL:
                brk += 1
    print(f"[E] ★上限の破れ＝U_p<3 と判定した (局,L,対象) {len(per_loop)} 件のうち、"
          f"その後そのループ内で実際に不安3以上へ達した = **{brk}**")
    # S＝空いた席の次点
    print(f"[S] ★★空いた席の次点（U_p<3 の実打席 {len(pl_p)} 件で `_virus_test_targets` から外した）")
    same = sum(1 for r in pl_p if r.get("S_is_same"))
    print(f"    次点も同じ手（＝外しても変わらない・別経路で選ばれている） = {same}")
    cn = Counter((r.get("S_card"), r.get("S_kind")) for r in pl_p if not r.get("S_is_same"))
    for (cd, kd), n in cn.most_common():
        print(f"      {cd}({kd}): {n}")
    d = sorted(r["played_score"] - r["S_score"] for r in pl_p if not r.get("S_is_same"))
    if d:
        print(f"    Δ＝(打たれた手の点)−(次点の点): 中央 {d[len(d)//2]:.2f} ／ "
              f"最小 {d[0]:.2f} ／ 最大 {d[-1]:.2f}")
    tp = Counter(r.get("S_target") for r in pl_p if not r.get("S_is_same"))
    print(f"    次点の対象 上位: " + " ".join(f"{k}:{v}" for k, v in tp.most_common(6)))
    # ★★実装の意味論＝U_p<3 の対象を**全部**外した版
    print(f"[S_all] ★★実装と同じ意味論（U_p<3 の対象を全部外す）での次点")
    same2 = sum(1 for r in pl_p if r.get("ALL_S_is_same"))
    print(f"    次点も同じ手 = {same2}")
    cn2 = Counter((r.get("ALL_S_card"), r.get("ALL_S_kind"))
                  for r in pl_p if not r.get("ALL_S_is_same"))
    for (cd, kd), n in cn2.most_common():
        print(f"      {cd}({kd}): {n}")
    d2 = sorted(r["played_score"] - r["ALL_S_score"]
                for r in pl_p if not r.get("ALL_S_is_same"))
    if d2:
        print(f"    Δ: 中央 {d2[len(d2)//2]:.2f} ／ 最小 {d2[0]:.2f} ／ 最大 {d2[-1]:.2f}／"
              f" Δ=0（同点）の席 {sum(1 for x in d2 if abs(x) < 1e-9)}")
    tp2 = Counter(r.get("ALL_S_target") for r in pl_p if not r.get("ALL_S_is_same"))
    print("    次点の対象 上位: " + " ".join(f"{k}:{v}" for k, v in tp2.most_common(6)))
    # ★行為指標＝`不安+1`(キャラ) 実打席のうち到達可能上限 < 3 のもの
    uns = [u for rec in recs for u in rec.un]
    lt = [u for u in uns if u["U_p"] < UNREST_GOAL]
    ltv = [u for u in lt if u["in_vt"]]
    print(f"[★行為指標] `不安+1`(キャラ) 実打席 = {len(uns)} ／ うち U_p<3 = **{len(lt)}**"
          f"（うちウイルス試験対象 **{len(ltv)}**）")
    # 局あたりの内訳
    tot_un = sum(rec.c.get("不安+1(キャラ)の実打席（全体）", 0) for rec in recs)
    print(f"[参考] `不安+1`(キャラ) の実打席（ウイルス試験の有無を問わない全体） = {tot_un}")
    sc = Counter(r["script"] for r in pl_p)
    print("    U_p<3 実打席の脚本別: " + " ".join(f"{k}:{v}" for k, v in sc.most_common()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="arena.b256_audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("seats", "verify"):
        p = sub.add_parser(nm)
        p.add_argument("--days", type=int, default=3)
        p.add_argument("--loops", type=int, default=8)
        p.add_argument("--pick", default="all")
        p.add_argument("--outdir", default="/tmp/b256")
        p.add_argument("--force", action="store_true")
        p.add_argument("--on", default="off")
    p = sub.add_parser("report")
    p.add_argument("--outdir", default="/tmp/b256")
    a = ap.parse_args(argv)
    return {"seats": cmd_seats, "verify": cmd_verify, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
