# -*- coding: utf-8 -*-
"""B-252：崖(B)＝カルティスト移動封じピンの**床の再較正**と**型IIの一意化条件**（計測＋掃引）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-49。事前登録＝
`docs/測定_B252_崖Bの床の再較正と型IIの一意化_2026-08-18.md` §0。

## B-249／B-250 との関係

★**`arena/b249_audit.py` と `arena/b250_audit.py` は1バイトも変更していない**。
本モジュールは両者のヘルパを import して使う（`_cult_p` / `_pin_at` / `_okey` / `apply_cond` /
`check_baseline` / `check_no_knob_writes` / `parse_pick` / `_truth_of` ...）。

## 本モジュールが**足した**もの（＝B-250 のデータでは答えられなかった問い）

| 追加 | 何のため |
|---|---|
| ★**キャスト全員の p(カルティスト) を席ごとに記録** | **フェーズ0**＝`role_marginals` が「カルティスト**不在**」に質量を割いているかを実データで見る。B-250 の `gate_rows` は `mm_char_now` の候補しか持っていない＝**不在質量が計算できなかった** |
| ★**`exists_mass` ＝ Σ_{cast} p(カルティスト)** | 役職スロットは1組あたりカルティスト**高々1**（`sim/state.py:68-82` のY表にのみ在／X表に無い／`_expand_irregular` は既存スロットに無い役職しか足さない）＝**Σ = P(カルティストが配役されている)**、`1-Σ = P(不在)` |
| ★**述語の後付け評価を席レコードに載せる** | (a) 床θ・(b1) 不在ゲート・(b2) 残り物ゲート を**同一の席集合**の上で比較する（掃引） |
| `--days 3` も同じドライバで | 規律4＝3日級と5日級で p 分布の位置が違う＝片方だけで較正しない |

## 挙動には触れない

`agents/` `sim/` `engine/` の既定値を一切書き換えない。`--cond` / `--perm` は**測定条件**であって
ベースラインではない。切替口の健全性検査（`arena/knob_audit.check_baseline` の一般形）を必ず配線する。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b252_audit seats  --days 5 --pick all --outdir /tmp/b252/off_id
    python -m arena.b252_audit seats  --days 3 --pick all --cond bx --perm rev --outdir /tmp/b252/bx_rev_3d
    python -m arena.b252_audit report --outdir /tmp/b252/off_id
    python -m arena.b252_audit phase0 --outdir /tmp/b252/off_id
    python -m arena.b252_audit sweep  --outdir /tmp/b252/off_id
"""
from __future__ import annotations

import argparse
import json
import os
import sys as _sys
from collections import Counter, defaultdict
from dataclasses import replace

from arena.b249_audit import (  # noqa: F401  （★B-249/B-250 を書き換えずに使う）
    MOVE_BAN, _EPS, _cult_p, _okey, _pin_at,
    apply_cond, check_no_knob_writes, defaults_banner, restore_cond,
)
from arena.b250_audit import _truth_of, knob_audit_baseline, parse_pick

CULTIST = "カルティスト"

#: (a) 絶対床の掃引点（既定 0.1 を含む。入口＝チケットの {0.15,0.2,0.25}）
THETA_FLOOR = (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5)
#: (b1) 「不在」ゲートの掃引点＝`exists_mass`（＝P(カルティストが居る)）の下限
BETA_EXISTS = (0.0, 0.3, 0.5, 0.7, 0.9, 0.999)
#: (b2) 「証拠が1人を指している」ゲートの掃引点＝候補に載っている質量の下限
#:   `cand_mass` ＝ Σ_{n∈cand} p(n) ＝ **真のカルティストが候補の中に居る確率**
GAMMA_CAND = (0.0, 0.3, 0.5, 0.7, 0.9)


# ---------------------------------------------------------------------------
# 集計器
# ---------------------------------------------------------------------------
class Tally252:
    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.seats: list[dict] = []      # ★ゲート3条件を満たした席の全数（キャスト全員のp込み）
        self.mismatch: list[dict] = []

    def merge(self, other: "Tally252") -> None:
        self.c.update(other.c)
        self.seats.extend(other.seats)
        self.mismatch.extend(other.mismatch)

    def to_json(self) -> dict:
        return {"counts": dict(self.c), "seats": self.seats,
                "mismatch": self.mismatch}

    @classmethod
    def from_json(cls, d: dict) -> "Tally252":
        t = cls()
        t.c.update(d.get("counts", {}))
        t.seats = list(d.get("seats", []))
        t.mismatch = list(d.get("mismatch", []))
        return t


# ---------------------------------------------------------------------------
# 観測（★読み取りと自前の再計算のみ）
# ---------------------------------------------------------------------------
def _observe(agent, view, options, best, score, fr, t: Tally252,
             script_obj, name: str, seed: int) -> None:
    t.c["席（set_card 決定）"] += 1
    mm_char_now = fr.get("mm_char_now") or set()
    mm_board_now = fr.get("mm_board_now") or set()
    danger_board = fr.get("danger_board")
    board_rules_possible = bool(fr.get("board_rules_possible"))
    pin_real = fr.get("_cultist_pin_target")
    maybe_real = fr.get("_cultist_maybe")
    if maybe_real is None:
        t.c["★ローカルを取れなかった席（計数から除外）"] += 1
        return
    b = getattr(agent, "_belief", None)
    if b is None:                                    # pragma: no cover
        return
    marg = b.role_marginals() or {}
    # ★B-252 の切替口が ON でも自己検証が成り立つよう、**実効の床**を読む
    #   （既定 OFF なら `_CULT_MAYBE_P` と同値）。
    thr = (agent._cult_maybe_floor() if hasattr(agent, "_cult_maybe_floor")
           else agent._CULT_MAYBE_P)

    # (0) 自己検証＝母集合とピン対象の再現（★1件でも食い違えば RC=1）
    repro_maybe = {n for n in mm_char_now if _cult_p(marg, n) > thr}
    repro_pin = _pin_at(agent, view, marg, mm_char_now, mm_board_now,
                        danger_board, board_rules_possible, thr)
    if repro_pin is not None and hasattr(agent, "_b252_cand_mass_ok"):
        if not agent._b252_cand_mass_ok(marg, [repro_pin]):
            repro_pin = None
    if repro_maybe != set(maybe_real) or repro_pin != pin_real:
        t.mismatch.append({"script": name, "seed": seed, "loop": view.get("loop"),
                           "day": view.get("day"),
                           "maybe_real": sorted(maybe_real),
                           "maybe_repro": sorted(repro_maybe),
                           "pin_real": pin_real, "pin_repro": repro_pin})
        t.c["★再現がローカルと食い違った席"] += 1
        return

    gate_ok = (danger_board is not None and danger_board in mm_board_now
               and board_rules_possible)
    if not (gate_ok and mm_char_now):
        return
    t.c["ゲート3条件を満たした席"] += 1

    cast = list(getattr(b, "cast", []))
    alive = {c["name"]: c for c in view.get("characters", []) if c.get("alive", True)}
    cast_rows = []
    for n in cast:
        cs = alive.get(n)
        cast_rows.append({"name": n, "p": round(_cult_p(marg, n), 6),
                          "true": _truth_of(script_obj, n),
                          "area": (cs or {}).get("area"),
                          "alive": n in alive,
                          "mm": n in mm_char_now})
    exists_mass = sum(_cult_p(marg, n) for n in cast)
    cult_in_cast = any(_truth_of(script_obj, n) == CULTIST for n in cast)
    t.seats.append({
        "script": name, "seed": seed, "loop": view.get("loop"),
        "day": view.get("day"), "danger_board": danger_board, "thr": thr,
        "pin": pin_real, "played": _okey(best),
        "played_pin": (_okey(best) == (MOVE_BAN, pin_real, "character")
                       if pin_real else False),
        "exists_mass": round(exists_mass, 6),
        "cult_in_cast": cult_in_cast,
        "cast": cast_rows,
    })


# ---------------------------------------------------------------------------
# 述語（★後付け評価の単一ソース。実装側 `heuristic_protagonist` と同じ形にする）
# ---------------------------------------------------------------------------
def pin_of(seat: dict, theta: float, beta: float = 0.0,
           gamma: float = 0.0) -> str | None:
    """席レコードから「述語 (θ, β, γ) の下でピンが立つか」を再計算する。

    - θ ＝ (a) 絶対床（`p > θ` で母集合に入る。既定 0.1）
    - β ＝ (b1) **不在ゲート**：`exists_mass ≦ β` なら母集合を空にする（既定 0.0＝無効）
    - γ ＝ (b2) **候補質量ゲート**：`Σ_{n∈cand} p(n) ≦ γ` なら立てない（既定 0.0＝無効）
      ＝「証拠が候補の中の1人を指している」ことを要求する（"残り物" を弾く）
    """
    if seat["exists_mass"] <= beta + _EPS:
        return None
    db = seat["danger_board"]
    cand = [c for c in seat["cast"]
            if c["mm"] and c["p"] > theta and c["alive"] and c["area"] != db]
    if len(cand) != 1:
        return None
    if sum(c["p"] for c in cand) <= gamma + _EPS:
        return None
    return cand[0]["name"]


def _score(seats: list[dict], theta: float, beta: float = 0.0,
           gamma: float = 0.0, only=None) -> dict:
    """指標一式（主指標＝適合率／副指標 S1・S2）。"""
    base = {"pins": 0, "ok": 0, "ng": 0, "played_ng": 0, "lost_ok": 0,
            "lost_ng": 0, "typeII": 0}
    for s in seats:
        if only and not only(s):
            continue
        p0 = pin_of(s, s["thr"])            # 既定述語
        p1 = pin_of(s, theta, beta, gamma)  # 新述語
        t1 = None
        if p1 is not None:
            t1 = _true_of_seat(s, p1)
            base["pins"] += 1
            if t1 == CULTIST:
                base["ok"] += 1
            else:
                base["ng"] += 1
                if s["played_pin"] and p0 == p1:
                    base["played_ng"] += 1
                if _p_of_seat(s, p1) > 0.25:
                    base["typeII"] += 1
        if p0 is not None and p1 != p0:
            if _true_of_seat(s, p0) == CULTIST:
                base["lost_ok"] += 1
            else:
                base["lost_ng"] += 1
    base["prec"] = (100.0 * base["ok"] / base["pins"]) if base["pins"] else float("nan")
    return base


def _true_of_seat(seat: dict, name: str) -> str:
    for c in seat["cast"]:
        if c["name"] == name:
            return c["true"]
    return "?"


def _p_of_seat(seat: dict, name: str) -> float:
    for c in seat["cast"]:
        if c["name"] == name:
            return c["p"]
    return 0.0


# ---------------------------------------------------------------------------
# 対局
# ---------------------------------------------------------------------------
def _run(script_obj, name: str, seed: int, loops: int, t: Tally252 | None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    HP = HeuristicProtagonist
    orig = HP._apply_seat_flags

    def _wrapped(agent, view, best):
        try:
            fr = _sys._getframe(1)
            if fr.f_code.co_name == "decide":
                loc = fr.f_locals
                score = loc.get("score")
                options = loc.get("options")
                if score is not None and options:
                    _observe(agent, view, options, best, score, loc, t,
                             script_obj, name, seed)
        except Exception as e:                       # pragma: no cover
            t.c[f"★観測例外 {type(e).__name__}"] += 1
        return orig(agent, view, best)

    if t is not None:
        HP._apply_seat_flags = _wrapped
    try:
        hp = HP(seed)
        state, _ = run_game(replace(script_obj, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
    finally:
        if t is not None:
            HP._apply_seat_flags = orig
    return state


def _install_perm(perm: str):
    if perm in ("id", "", None):
        return None
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    return uninstall_perm


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _shard_path(outdir: str, cond: str, perm: str, name: str, seed: int) -> str:
    return os.path.join(outdir, f"{cond}__{perm}__{name}__{seed}.json")


def cmd_seats(a) -> int:
    before = defaults_banner()
    print(f"[b252] {knob_audit_baseline(a.cond)}")
    games = parse_pick(a.pick, a.days)
    os.makedirs(a.outdir, exist_ok=True)
    old = apply_cond(a.cond)
    un = _install_perm(a.perm)
    try:
        for name, seed, sc in games:
            p = _shard_path(a.outdir, a.cond, a.perm, name, seed)
            if os.path.exists(p) and not a.force:
                continue
            t = Tally252()
            st = _run(sc, name, seed, a.loops, t)
            d = t.to_json()
            d["meta"] = {"script": name, "seed": seed, "days": a.days,
                         "loops": a.loops, "cond": a.cond, "perm": a.perm,
                         "winner": st.winner, "loop_no": st.loop_no,
                         "final_battle": any(e.get("event") == "final_battle"
                                             for e in st.history)}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            print(f"  done {name}#{seed}: ゲート席={t.c['ゲート3条件を満たした席']} "
                  f"winner={st.winner}/L{st.loop_no}", flush=True)
    finally:
        if un:
            un()
        restore_cond(old)
    check_no_knob_writes(before, driver="arena.b252_audit")
    print("[b252] ✅ 切替口の束は測定の前後で不変")
    return _report(a)


def _load(outdir: str, cond=None, perm=None):
    total, metas = Tally252(), []
    for fn in sorted(os.listdir(outdir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(outdir, fn), encoding="utf-8") as f:
            d = json.load(f)
        m = d.get("meta", {})
        if cond and m.get("cond") != cond:
            continue
        if perm and m.get("perm") != perm:
            continue
        total.merge(Tally252.from_json(d))
        metas.append(m)
    return total, metas


def _report(a) -> int:
    total, metas = _load(a.outdir, a.cond, a.perm)
    ndef = sum(1 for m in metas if m.get("winner") == "protagonist"
               and not m.get("final_battle"))
    print(f"\n[b252] 集計＝{len(metas)}局 cond={a.cond} perm={a.perm} "
          f"防衛勝ち {ndef}/{len(metas)}")
    for k, v in sorted(total.c.items()):
        print(f"  {v:>7}  {k}")
    if total.mismatch:
        print(f"★★自己検証の食い違い {len(total.mismatch)} 件")
        for m in total.mismatch[:3]:
            print("   ", json.dumps(m, ensure_ascii=False))
    return 1 if total.mismatch else 0


def cmd_report(a) -> int:
    return _report(a)


# --- ★フェーズ0＝「不在」に質量を割いているか ------------------------------
def cmd_phase0(a) -> int:
    total, metas = _load(a.outdir, a.cond, a.perm)
    seats = total.seats
    print(f"\n[b252 phase0] ゲート席 {len(seats)}（{len(metas)}局・cond={a.cond} perm={a.perm}）")
    print("問い＝`role_marginals` は「カルティストが1人も配役されていない」世界に質量を割いているか。")
    print("計器＝`exists_mass` ＝ Σ_{cast} p(カルティスト)（役職スロットは高々1＝これが P(在)）。")
    ex = sorted(s["exists_mass"] for s in seats)
    if not ex:
        print("★席が0＝判定不能")
        return 1
    print(f"  exists_mass: min={ex[0]:.4f} 中央={ex[len(ex)//2]:.4f} max={ex[-1]:.4f}")
    edges = (0.0, 0.001, 0.2, 0.4, 0.6, 0.8, 0.95, 0.999, 1.001)
    for lo, hi in zip(edges, edges[1:]):
        k = sum(1 for x in ex if lo - _EPS <= x < hi)
        print(f"   在の質量 [{lo:.3f}, {hi:.3f}) : {k:>6} 席 ({100.0*k/len(ex):5.1f}%)")
    strict = sum(1 for x in ex if x < 1.0 - 1e-6)
    print(f"  ★不在に**非ゼロの質量**を割いている席＝{strict}/{len(ex)}"
          f"（{100.0*strict/len(ex):.1f}%）＝答え: "
          f"{'YES' if strict else 'NO'}")
    # 真値との突き合わせ（校正）
    print("\n【校正】真に不在（脚本にカルティストが居ない）席 vs exists_mass")
    for flag in (False, True):
        sub = sorted(s["exists_mass"] for s in seats if s["cult_in_cast"] is flag)
        lab = "真に**在**" if flag else "真に**不在**"
        if sub:
            print(f"  {lab}: {len(sub)}席  min={sub[0]:.4f} 中央={sub[len(sub)//2]:.4f} "
                  f"max={sub[-1]:.4f}")
        else:
            print(f"  {lab}: 0席")
    # ピン席に絞る
    print("\n【ピンが立った席（既定述語）だけに絞る】")
    for lab, sel in (("正対象", lambda s, p: _true_of_seat(s, p) == CULTIST),
                     ("★誤対象", lambda s, p: _true_of_seat(s, p) != CULTIST)):
        vals, incast = [], Counter()
        for s in seats:
            p = pin_of(s, s["thr"])
            if p is not None and sel(s, p):
                vals.append(s["exists_mass"])
                incast[s["cult_in_cast"]] += 1
        vals.sort()
        if vals:
            print(f"  {lab}: {len(vals)}席  exists_mass min={vals[0]:.4f} "
                  f"中央={vals[len(vals)//2]:.4f} max={vals[-1]:.4f}  "
                  f"真の在/不在={dict(incast)}")
    return 0


# --- 掃引 -------------------------------------------------------------------
def _table(seats, rows, only=None, title="") -> str:
    out = [title,
           f"{'述語':<34}{'ピン席':>8}{'正':>7}{'誤':>7}{'適合率':>9}"
           f"{'実打誤':>8}{'消えた正':>9}{'型II残':>8}"]
    out.append("-" * len(out[-1]))
    for lab, (th, be, ga) in rows:
        r = _score(seats, th, be, ga, only=only)
        out.append(f"{lab:<34}{r['pins']:>8}{r['ok']:>7}{r['ng']:>7}"
                   f"{r['prec']:>8.1f}%{r['played_ng']:>8}{r['lost_ok']:>9}"
                   f"{r['typeII']:>8}")
    return "\n".join(out)


def _rows():
    rows = [("既定 θ=0.1", (0.1, 0.0, 0.0))]
    rows += [(f"(a) 床 θ={th}", (th, 0.0, 0.0)) for th in THETA_FLOOR if th != 0.1]
    rows += [(f"(b1) 不在ゲート β={be}", (0.1, be, 0.0)) for be in BETA_EXISTS if be > 0]
    rows += [(f"(b2) 候補質量 γ={ga}", (0.1, 0.0, ga)) for ga in GAMMA_CAND if ga > 0]
    rows += [(f"(a)+(b2) θ=0.2 γ={ga}", (0.2, 0.0, ga)) for ga in GAMMA_CAND if ga > 0]
    rows += [(f"(a)+(b2) θ=0.25 γ={ga}", (0.25, 0.0, ga)) for ga in GAMMA_CAND if ga > 0]
    return rows


def cmd_sweep(a) -> int:
    total, metas = _load(a.outdir, a.cond, a.perm)
    seats = total.seats
    print(f"\n[b252 sweep] ゲート席 {len(seats)}（{len(metas)}局・cond={a.cond} perm={a.perm}）")
    print(_table(seats, _rows(), title="\n【全脚本】"))
    print(_table(seats, _rows(), only=lambda s: s["script"].startswith("random"),
                 title="\n【★独立推定＝random_* のみ（規律5）】"))
    # 残った誤対象の供給源
    print("\n【既定述語での誤対象席の供給源】")
    src: Counter = Counter()
    for s in seats:
        p = pin_of(s, s["thr"])
        if p is not None and _true_of_seat(s, p) != CULTIST:
            src[(s["script"], s["seed"])] += 1
    for k, v in src.most_common(10):
        print(f"   {k}: {v}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="arena.b252_audit", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for nm in ("seats", "report", "phase0", "sweep"):
        q = sub.add_parser(nm)
        q.add_argument("--days", type=int, default=5)
        q.add_argument("--loops", type=int, default=8)
        q.add_argument("--pick", default="all")
        q.add_argument("--cond", default="off")
        q.add_argument("--perm", default="id")
        q.add_argument("--outdir", required=True)
        if nm == "seats":
            q.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    return {"seats": cmd_seats, "report": cmd_report,
            "phase0": cmd_phase0, "sweep": cmd_sweep}[a.cmd](a)


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
