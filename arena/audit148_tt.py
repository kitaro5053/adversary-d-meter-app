# -*- coding: utf-8 -*-
"""audit148：B-149 の `tt_defeat`「発生」判定の弱さ（B-150 ⑧-3 自己申告）の波及範囲を実測する。

## 何を測るか（監査レーン lane/audit148・運用doc §6b-4）

`arena/b149_audit._realized` は `tt_defeat` を「その (loop,day) に公開履歴の `loop_end` が
あったか」で判定する（`b149_audit.py:296-297`）。しかし公開の `loop_end` は
- 主人公の死亡（`sim/effects.py:172`）
- KP死亡等のループ終了効果（`sim/effects.py:147`）
- タイムトラベラー任意敗北（`sim/effects.py:635`）
のどれでも出る＝**TT が実際に任意敗北を宣言していなくても True になりうる**。

`tt_defeat` 脅威の主張は「TT{名}の任意敗北（最終日）」（`agents/defense_plan.py:2062`）
＝**正しい発生判定は「その (loop,day) に TT任意敗北が宣言されたか」**である。
物証＝`sim/effects.py:638` が secret_log に
`{"event":"loop_end","reason":"タイムトラベラー任意敗北（タイムトラベラー敗北:<名>）"}` を残す。

本計測は判定を3通り並べて数える：
- **old**  ＝現行（公開 `loop_end` がその日にあった）＝B-149/B-150 が使った判定
- **strict**＝その日に TT任意敗北が宣言された（宣言者は問わない）
- **named** ＝strict かつ宣言者が脅威ラベルの名指しキャラと一致

さらに B-149 §4-1（的中/空振り）・§4-2（交絡なし較正表）・§3-1（L2/L3）の
tt_defeat 由来の変化量を出す。**挙動は 1 bit も変えない**（`b149_audit` のプローブを
そのまま使い、判定だけを後段で足す）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.audit148_tt count --days 3 --json d3.json
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.audit148_tt count --days 5 --json d5.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from arena.b145_audit import _lost_loops, _outcome, _snap_index
from arena.b146_probe import _true_boards
from arena.b149_audit import (_BIG_SEATS, _MMProbe, _Probe, _day_end_snap,
                              _deaths_by_day, _loop_end_days, _names_in,
                              _prot_deaths, _realized)
from sim import run_game


class _LiteProbe(_Probe):
    """b149 の `_Probe` から **recs シャドー（3回の `_defense_plan_recs` 再評価）を省いた**
    軽量版。本計測が使うのは threats の (kind/label/sev/defendable/cov/cov_inf/spent) だけで、
    recs 系（L1eff/L1rev）は再計測しない＝B-149 doc の値をそのまま引く。
    ★挙動不変の性質は同じ（`super().decide()` の戻り値の後で純関数 `plan_defenses` を
    1回だけ再評価する。rng 非消費・戻り値に触れない）。
    ★整合検査＝`--check` で b149 のフルプローブと同一局を回し、threats の
    (kind,label,sev,cov,cov_inf,spent) 列が一致することを確認できる（verify_lite）。
    """

    def decide(self, view, decision, options):
        # _Probe.decide を通すと recs シャドーまで走るので、祖父母の decide を直接呼ぶ。
        chosen = super(_Probe, self).decide(view, decision, options)
        if decision != "set_card":
            return chosen
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            return chosen
        import agents.defense_plan as dp

        threats, plan = stash
        row = {"loop": view.get("loop"), "day": view.get("day"),
               "seat": view.get("seat")}
        try:
            plan_inf = dp.plan_defenses(threats, seats=_BIG_SEATS)
        except Exception as e:      # noqa: BLE001
            row["error"] = repr(e)
            self.seats.append(row)
            return chosen
        cov3 = getattr(plan, "covered", {}) or {}
        covI = getattr(plan_inf, "covered", {}) or {}
        ts = []
        for t in threats:
            ts.append({
                "kind": getattr(t, "kind", None),
                "label": str(getattr(t, "label", "")),
                "sev": round(float(getattr(t, "severity", 0.0) or 0.0), 4),
                "defendable": bool(getattr(t, "defendable", False)),
                "cov": id(t) in cov3,
                "cov_inf": id(t) in covI,
                "spent": (id(t) in cov3
                          and not str(cov3.get(id(t), "")).endswith(
                              "（既存手で兼ねる）")),
            })
        row.update({"threats": ts})
        self.seats.append(row)
        return chosen


def _tt_declared(state) -> dict:
    """(loop, day) -> その日に TT任意敗北を宣言したキャラ名の集合（secret_log から）。"""
    out: dict = {}
    for e in state.secret_log:
        if e.get("event") != "loop_end":
            continue
        r = str(e.get("reason", ""))
        if "タイムトラベラー任意敗北" not in r:
            continue
        # reason = "タイムトラベラー任意敗北（タイムトラベラー敗北:<名>）"
        name = None
        if ":" in r:
            name = r.split(":", 1)[1].rstrip("）)")
        out.setdefault((e.get("loop"), e.get("day")), set()).add(name)
    return out


def _bucket(s: float) -> str:
    for hi in (0.05, 0.1, 0.2, 0.4, 0.7):
        if s < hi:
            return f"<{hi}"
    return ">=0.7"


def audit_game(script, seed: int, loops: int = 8, full: bool = False) -> dict:
    """b149_audit.audit_game と同じ流れ＋ state を握って TT 宣言日を追加で控える。"""
    probe = replace(script, loops=loops)
    hp = _Probe(seed) if full else _LiteProbe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(probe, {"mastermind": mm,
                                "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    dayend = _day_end_snap(snaps)
    deaths = _deaths_by_day(state)
    protd = _prot_deaths(state)
    ends = _loop_end_days(state)
    lost = _lost_loops(state)
    ttdays = _tt_declared(state)
    for row in hp.seats:
        key = (row.get("loop"), row.get("day"))
        row["loop_lost"] = row.get("loop") in lost
        strict_b, _wide = _true_boards(mm.rule_y,
                                       mm.board_x_by_loop.get(row.get("loop")),
                                       False)
        for t in row.get("threats", ()):
            t["real"] = _realized(t["kind"], t["label"], key,
                                  deaths, protd, ends, dayend,
                                  strict_boards=strict_b)
            if t["kind"] == "tt_defeat":
                decl = ttdays.get(key)
                t["real_tt_strict"] = bool(decl)
                ns = _names_in(t["label"])
                t["real_tt_named"] = bool(decl) and any(n in decl for n in ns)
    return {"outcome": _outcome(state), "seats": hp.seats,
            "n_tt_days": len(ttdays)}


def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    calib_old = Counter()   # (status, band) -> real 件数（old 判定）
    calib_new = Counter()   # 同（tt を strict に置換した判定）
    calib_n = Counter()     # (status, band) -> 件数
    tt_rows: list[dict] = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = audit_game(sc, seed, loops=loops)
        c["games"] += 1
        c["tt_decl_days"] += int(res.get("n_tt_days") or 0)
        for row in res["seats"]:
            ts = row.get("threats") or []
            starved = [t for t in ts if t.get("cov_inf") and not t.get("cov")]
            miss_old = [t for t in starved if t.get("real") is True]
            # ★L2 への波及＝starved の real を tt だけ strict に置換して数え直す
            miss_new = [t for t in starved
                        if (t.get("real_tt_strict")
                            if t.get("kind") == "tt_defeat" else t.get("real"))
                        is True]
            if miss_old:
                c["L2_old"] += 1
                if row.get("loop_lost"):
                    c["L3_old"] += 1
            if miss_new:
                c["L2_new"] += 1
                if row.get("loop_lost"):
                    c["L3_new"] += 1
            if any(t.get("kind") == "tt_defeat" for t in starved):
                c["starved_tt"] += 1
            whiff_old = hit_old = whiff_new = hit_new = 0
            for t in ts:
                st = ("spent" if t.get("spent")
                      else ("reuse" if t.get("cov") else "uncov"))
                kind = t.get("kind")
                b = _bucket(float(t.get("sev") or 0.0))
                r_old = t.get("real")
                r_new = (t.get("real_tt_strict")
                         if kind == "tt_defeat" else r_old)
                calib_n[(st, b)] += 1
                if r_old is True:
                    calib_old[(st, b)] += 1
                if r_new is True:
                    calib_new[(st, b)] += 1
                if st == "spent":
                    c["n_spent"] += 1
                    if r_old is True:
                        hit_old += 1
                    elif r_old is False:
                        whiff_old += 1
                    if r_new is True:
                        hit_new += 1
                    elif r_new is False:
                        whiff_new += 1
                if kind != "tt_defeat":
                    continue
                c[f"tt_{st}"] += 1
                c[f"tt_{st}_def_{bool(t.get('defendable'))}"] += 1
                for lbl, val in (("old", r_old),
                                 ("strict", t.get("real_tt_strict")),
                                 ("named", t.get("real_tt_named"))):
                    if val is True:
                        c[f"tt_{st}_real_{lbl}"] += 1
                        c[f"tt_real_{lbl}"] += 1
                c["tt_n"] += 1
                tt_rows.append({
                    "script": name, "seed": seed,
                    "loop": row.get("loop"), "day": row.get("day"),
                    "seat": row.get("seat"), "label": t.get("label"),
                    "sev": t.get("sev"), "status": st,
                    "defendable": bool(t.get("defendable")),
                    "real_old": r_old,
                    "real_strict": t.get("real_tt_strict"),
                    "real_named": t.get("real_tt_named"),
                    "loop_lost": row.get("loop_lost")})
            c["hit_old"] += hit_old
            c["whiff_old"] += whiff_old
            c["hit_new"] += hit_new
            c["whiff_new"] += whiff_new
            if whiff_old:
                c["L4_seats_old"] += 1
            if whiff_new:
                c["L4_seats_new"] += 1
        if verbose:
            print(f"  {name} s{seed}: tt_n={c['tt_n']}"
                  f" real_old={c.get('tt_real_old', 0)}"
                  f" strict={c.get('tt_real_strict', 0)}", flush=True)
    return {"days": days, "counts": dict(c),
            "calib_n": {f"{a}|{b}": v for (a, b), v in calib_n.items()},
            "calib_old": {f"{a}|{b}": v for (a, b), v in calib_old.items()},
            "calib_new": {f"{a}|{b}": v for (a, b), v in calib_new.items()},
            "tt_rows": tt_rows}


def check(days: int = 3, loops: int = 8, n: int = 4) -> int:
    """★整合検査＝軽量プローブと b149 フルプローブで threats 列が一致するか。"""
    from arena.benchmark import benchmark_scripts

    bad = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[:n]:
        a = audit_game(sc, seed, loops=loops, full=False)
        b = audit_game(sc, seed, loops=loops, full=True)
        ka = [(r.get("loop"), r.get("day"), r.get("seat"),
               tuple((t["kind"], t["label"], t["sev"], t["cov"],
                      t["cov_inf"], t["spent"], t["real"],
                      t.get("real_tt_strict"), t.get("real_tt_named"))
                     for t in r.get("threats") or ()))
              for r in a["seats"]]
        kb = [(r.get("loop"), r.get("day"), r.get("seat"),
               tuple((t["kind"], t["label"], t["sev"], t["cov"],
                      t["cov_inf"], t["spent"], t["real"],
                      t.get("real_tt_strict"), t.get("real_tt_named"))
                     for t in r.get("threats") or ()))
              for r in b["seats"]]
        ok = ka == kb and a["outcome"] == b["outcome"]
        print(f"  {name} s{seed}: {'一致' if ok else '★不一致'}"
              f"（席 {len(ka)}/{len(kb)}）", flush=True)
        if not ok:
            bad += 1
    print(f"不一致 = {bad} 件")
    return bad


def merge(paths: list[str]) -> dict:
    out: dict = {"counts": Counter(), "calib_n": Counter(),
                 "calib_old": Counter(), "calib_new": Counter(),
                 "tt_rows": []}
    days = None
    for p in paths:
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        days = r.get("days", days)
        for k in ("counts", "calib_n", "calib_old", "calib_new"):
            for kk, v in (r.get(k) or {}).items():
                out[k][kk] += v
        out["tt_rows"].extend(r.get("tt_rows") or ())
    return {"days": days,
            "counts": dict(out["counts"]), "calib_n": dict(out["calib_n"]),
            "calib_old": dict(out["calib_old"]),
            "calib_new": dict(out["calib_new"]), "tt_rows": out["tt_rows"]}


def report(res: dict) -> None:
    c = res["counts"]
    print(f"== audit148：tt_defeat 判定の波及（{res.get('days')}日級・"
          f"{c.get('games', 0)}局）==")
    print(f"  TT任意敗北が実際に宣言された日 = {c.get('tt_decl_days', 0)}")
    print(f"  tt_defeat 脅威 = {c.get('tt_n', 0)} 件"
          f"（spent {c.get('tt_spent', 0)} / reuse {c.get('tt_reuse', 0)}"
          f" / uncov {c.get('tt_uncov', 0)}）")
    for st in ("spent", "reuse", "uncov"):
        print(f"   {st:6s}: real_old={c.get(f'tt_{st}_real_old', 0)}"
              f" → strict={c.get(f'tt_{st}_real_strict', 0)}"
              f" / named={c.get(f'tt_{st}_real_named', 0)}"
              f"  (defendable=True {c.get(f'tt_{st}_def_True', 0)}"
              f" / False {c.get(f'tt_{st}_def_False', 0)})")
    print(f"  tt real 計: old={c.get('tt_real_old', 0)}"
          f" → strict={c.get('tt_real_strict', 0)}"
          f" / named={c.get('tt_real_named', 0)}")
    print(f"★B-149 §4-1 への波及：席を消費した採用（n_spent={c.get('n_spent', 0)}）")
    print(f"   的中 old {c.get('hit_old', 0)} → new {c.get('hit_new', 0)}"
          f" ／ 空振り old {c.get('whiff_old', 0)} → new {c.get('whiff_new', 0)}")
    print(f"   L4席 old {c.get('L4_seats_old', 0)} → new {c.get('L4_seats_new', 0)}")
    print(f"★B-149 §3-1 への波及：L2 old {c.get('L2_old', 0)} → new {c.get('L2_new', 0)}"
          f" ／ L3 old {c.get('L3_old', 0)} → new {c.get('L3_new', 0)}"
          f" ／ 予算で落ちた tt = {c.get('starved_tt', 0)} 件")
    print("★B-149 §4-2 への波及：較正表（uncov＝交絡なし標本・real/n old→new）")
    bks = ["<0.05", "<0.1", "<0.2", "<0.4", "<0.7", ">=0.7"]
    for st in ("spent", "reuse", "uncov"):
        line = f"   {st:6s}"
        for b in bks:
            n = res["calib_n"].get(f"{st}|{b}", 0)
            ro = res["calib_old"].get(f"{st}|{b}", 0)
            rn = res["calib_new"].get(f"{st}|{b}", 0)
            mark = "" if ro == rn else "★"
            line += f" {b}:{ro}→{rn}/{n}{mark}"
        print(line)
    rows = [r for r in res.get("tt_rows", ()) if r.get("real_old")]
    if rows:
        print("  real_old=True の tt_defeat（全件）")
        for r in rows:
            print(f"   {r['script']}(s{r['seed']}) L{r['loop']}D{r['day']}"
                  f"#{r['seat']} {r['status']} def={r['defendable']}"
                  f" sev={r['sev']} strict={r['real_strict']}"
                  f" named={r['real_named']} lost={r['loop_lost']}"
                  f" | {r['label']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "merge", "check"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return 1 if check(days=a.days, loops=a.loops) else 0
    if a.cmd == "merge":
        res = merge(a.inputs or [])
    else:
        res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
                  verbose=a.verbose)
    report(res)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
