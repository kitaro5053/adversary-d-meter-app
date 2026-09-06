# -*- coding: utf-8 -*-
"""B-185 Phase 1：**「同一ターン同一脅威への重複支払い」の発火面の数え上げ**（計測のみ・`arena/` のみ）。

発注＝FableA（2026-08-07）。文脈＝`docs/バックログ_構想メモ_FableA.md` §60-10（B-183 Phase 2 の
副産物として起票）。B-183 案B ON の世界で観測された機序＝**複数席の強制発火が再開けされたとき、
先席が脅威を折った後も、後席の折り手リストが後席の options から再生成されるため
`placed済み` 判定（`agents/b100_alloc.py:188-191`）を素通りし、同じ脅威に2席目が強制される**。
本レーンの問い＝**base（現行 main・既定フラグ）で実害が出る経路があるか**。

------------------------------------------------------------------------------
## 0. 述語（★先に固定し、後から変えない）
------------------------------------------------------------------------------

- **支払い**＝その席の**実手**（実際に置いた1枚）のキーが、その席の時点の脅威スナップショット
  （`_b100_plan` の threats＝allocate に渡る現物と同一）の **fatal 脅威の折り手キー集合**に
  含まれること（判定は席ごと＝`arena/b176_audit._Probe` の `play["broke"]` をそのまま再利用）。
- **重複支払いターン**＝同一 (loop, day) で、**同一ラベルの fatal 脅威**に2席以上が支払ったターン。
  主計数は **prob≥0.5**（いずれかの支払い席の視点での最大値）。参考に全確度も数える。
- **経路（via）**＝各支払い席について
  - `forced`＝B-100 が**この脅威を理由に**強制した（`_b100_log` の label 一致）
  - `forced_other`＝B-100 強制だが理由は別脅威（実手が偶然この脅威も折る）
  - `natural`＝強制なし（計画/貪欲の実手が折り手に重なった）
  ★B-185 の発火面（`placed済み` 素通りの実害候補）＝**2席目以降に `forced` がいるターン**。
  それ以外（natural 同士等）は重複被覆の文脈情報として層別のみ。
- **層別 (a)/(b)/(c)**（発注 §2）＝**先席の折り手の robust**（`defense_plan.Break.robust`＝
  脚本家が同ターンで打ち消せない手か）で決める。脅威の conditions は AND（1条件折れば防げる＝
  `defense_plan.Threat` docstring）なので：
  - (a) **冗長**＝先席の折り手が robust=True＝条件は確実に折れていた＝2席目は冗長。
  - (b) **独立の意味**＝先席の折り手が全て robust=False（移動退避等＝mm が同ターンに
    打ち消しうる）＝2席目は保険として独立の意味を持ちうる。
  - (c) **判定不能**＝先席の実手キーが折り手表に見つからない等（正直に残す）。
- **素通りの検算**＝2席目以降の `forced` 席について、先行支払い席のキーが**その席の**
  raw（折り手キー全集合＝`live_constraints :188` と同じ集合）に**残っていない**ことを確認する
  （残っていれば `:190` で落ちるはず＝矛盾＝`bypass_ok=False` で報告に残す）。
- **機会費用（存在証明まで）**＝冗長な `forced` 席について、(i) 押し出した予定手
  （`_b100_log` の displaced／displaced_score）、(ii) その席の TRACE cons に居た
  「資格あり・n_here≥1・このターン誰も折っていない」他の脅威（あれば具体名）。

★観測は**読み取り専用**（TRACE＋LC ラッパー＋decide 内の記録のみ）。物証＝`verify`
（計測 ON と素の対局の棋譜完全一致）。★正解の配役は一切参照しない。
測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行。

------------------------------------------------------------------------------
## 1. CLI
------------------------------------------------------------------------------

    python -m arena.b185_audit verify --days 5 [--games random_FS#3]
    python -m arena.b185_audit scan   --days 3 [--json out.json]
    python -m arena.b185_audit turn   --days 3 --game basic#1 --loop 4 --day 3
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind
from agents import b100_alloc
from arena.b176_audit import _night_losses, _key_of
from arena.b183_audit import _Probe as _B183Probe, _ORIG_LC

from sim import run_game

_EPS = 1e-9


# ---------------------------------------------------------------------------
# 観測器：b183 の _Probe（TRACE＋LC ラッパー＋plays/broke）に
# 「席ごとの脅威スナップショット（raw キー・折り手表）」の記録を足す（読み取りのみ）。
# ---------------------------------------------------------------------------
class _Probe(_B183Probe):
    def __init__(self, seed: int = 0, shadow: bool = True):
        super().__init__(seed, shadow=shadow)
        #: (loop, day, seat) -> {label: {"prob","kind","n_cond","raw":set,
        #:                              "by_key": {key: [(cond_label, robust, cost)]}}}
        self.seat_threats: dict = {}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision != "set_card" or not self._shadow_on:
            return chosen
        try:
            stash = getattr(self, "_b100_plan", None)
            if stash:
                threats, _plan = stash
                det: dict = {}
                for t in threats:
                    if not t.fatal:
                        continue
                    ent = det.setdefault(t.label, {
                        "prob": float(t.prob), "kind": t.kind,
                        "n_cond": len(t.conditions),
                        "raw": set(), "by_key": {}})
                    ent["prob"] = max(ent["prob"], float(t.prob))
                    for c in t.conditions:
                        for b in c.breaks:
                            k = (b.card, b.target, b.target_kind)
                            ent["raw"].add(k)
                            ent["by_key"].setdefault(k, []).append(
                                (c.label, bool(b.robust), float(b.cost)))
                self.seat_threats[(view.get("loop"), view.get("day"),
                                   view.get("seat"))] = det
        except Exception:
            pass
        return chosen


def _play(script, seed: int, loops: int, instrument: bool = True,
          paid: bool = False):
    hp = _Probe(seed, shadow=instrument)
    if paid:                     # ★Phase 2：切替口 ON の世界を測る（既定 False＝base）
        hp.B185_PAID_LABELS = True
    if instrument:
        b100_alloc.TRACE = hp._on_trace
        b100_alloc.live_constraints = hp._lc_wrapper
    try:
        st, _ = run_game(replace(script, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": hp, "p2": hp, "p3": hp})
    finally:
        b100_alloc.TRACE = None
        b100_alloc.live_constraints = _ORIG_LC
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"),
              e.get("target"), e.get("to")) for e in st.history]
    return hp, st, trace


# ---------------------------------------------------------------------------
# 1局の後処理＝重複支払いターンの抽出と層別
# ---------------------------------------------------------------------------
def _analyze_game(hp: _Probe, st) -> list[dict]:
    nls = _night_losses(st)
    forced = {}
    for r in getattr(hp, "_b100_log", []) or []:
        forced[(r.get("loop"), r.get("day"), r.get("seat"))] = r
    out: list[dict] = []
    for (loop, day), rec in sorted(hp.turns.items()):
        by_label: dict = {}
        for idx, play in enumerate(rec["plays"]):
            for (lb, _prob, _tonight, _fatal) in play.get("broke", []):
                by_label.setdefault(lb, []).append((idx, play))
        served = set(by_label)                 # このターン誰かが折ったラベル全部
        for lb, payers in by_label.items():
            if len(payers) < 2:
                continue
            payers.sort(key=lambda x: x[0])    # 実手の置かれた順
            det_rows, probs = [], []
            for _idx, play in payers:
                seat, key = play["seat"], tuple(play["key"])
                sti = (hp.seat_threats.get((loop, day, seat)) or {}).get(lb) or {}
                entries = (sti.get("by_key") or {}).get(key) or []
                fr = forced.get((loop, day, seat))
                via = ("forced" if (play.get("b100") and fr
                                    and fr.get("label") == lb)
                       else "forced_other" if play.get("b100") else "natural")
                if sti:
                    probs.append(float(sti.get("prob") or 0.0))
                det_rows.append({
                    "seat": seat, "key": key, "via": via,
                    "robust": (any(r for (_c, r, _co) in entries)
                               if entries else None),
                    "conds": sorted({c for (c, _r, _co) in entries}),
                    "n_cond": sti.get("n_cond"),
                    "raw": sti.get("raw") or set(),
                    "forced_log": fr})
            prob = max(probs) if probs else float(
                (rec["threats"].get(lb) or {}).get("prob") or 0.0)
            # ---- 層別 (a)/(b)/(c)＝先席（最初の支払い）の robust ----
            first = det_rows[0]
            if first["robust"] is True:
                cls = "(a)冗長"
            elif first["robust"] is False:
                cls = "(b)独立の意味(先席robust=False)"
            else:
                cls = "(c)判定不能"
            # ---- 素通りの検算＋機会費用（2席目以降の forced のみ） ----
            checks, opp = [], []
            for i in range(1, len(det_rows)):
                d = det_rows[i]
                if d["via"] != "forced":
                    continue
                earlier = [det_rows[j]["key"] for j in range(i)]
                d_bypass = all(k not in d["raw"] for k in earlier)
                checks.append({"seat": d["seat"], "bypass_ok": d_bypass,
                               "earlier_keys": [list(k) for k in earlier]})
                fr = d["forced_log"] or {}
                alt = []
                recs = hp.alloc_trace.get((loop, day, d["seat"])) or []
                if recs:
                    for c in (recs[-1].get("cons") or []):
                        if (c.get("gate") and c.get("n_here")
                                and c.get("label") != lb
                                and c.get("label") not in served):
                            alt.append({"label": c.get("label"),
                                        "prob": c.get("prob"),
                                        "gate": c.get("gate"),
                                        "n_here": c.get("n_here")})
                opp.append({"seat": d["seat"],
                            "displaced": fr.get("displaced"),
                            "displaced_score": fr.get("displaced_score"),
                            "forced_score": fr.get("forced_score"),
                            "alt_unserved_gated": alt})
            out.append({
                "loop": loop, "day": day, "label": lb, "prob": prob,
                "cls": cls, "night_loss": (loop, day) in nls,
                "vias": [d["via"] for d in det_rows],
                "forced_second": any(d["via"] == "forced"
                                     for d in det_rows[1:]),
                "payers": [{"seat": d["seat"], "key": list(d["key"]),
                            "via": d["via"], "robust": d["robust"],
                            "conds": d["conds"], "n_cond": d["n_cond"]}
                           for d in det_rows],
                "bypass_checks": checks,
                "opportunity": opp})
    return out


def _lc_placed_drops(hp: _Probe) -> int:
    """`:190` が**実際に効いた**回数（席×ラベル）＝素通りの対照（検算の文脈）。"""
    n = 0
    for info in hp.lc_info.values():
        n += sum(1 for r in (info.get("drops") or {}).values()
                 if r == "LC:placed済み")
    return n


def _lc_paid_drops(hp: _Probe) -> int:
    """★Phase 2：`paid` 判定（B-185）が効いた回数（席×ラベル・ON の世界のみ非ゼロ）。"""
    n = 0
    for info in hp.lc_info.values():
        n += sum(1 for r in (info.get("drops") or {}).values()
                 if r == "LC:paid済み(B185)")
    return n


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def scan(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None, paid: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.corpus_census import script_signature
    from arena.b145_audit import _outcome

    c: Counter = Counter()
    rows: list = []
    seal: Counter = Counter()
    disp: list = []                        # ★Phase 2：全 forced 席の押し出し点数（罠(ii)）
    g_dup, s_dup = set(), set()            # prob≥0.5 の重複支払い（局/独立脚本）
    g_f2, s_f2 = set(), set()              # うち2席目以降 forced（B-185 発火面）
    all_sigs = set()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _t = _play(sc, seed, loops, paid=paid)
        n += 1
        g = f"{name}#{seed}"
        sig = script_signature(sc)
        all_sigs.add(sig)
        c["placed済み判定が効いた回数(:190・席×ラベル)"] += _lc_placed_drops(hp)
        c["paid済み判定が効いた回数(B185・席×ラベル)"] += _lc_paid_drops(hp)
        # ★罠(ii) の併記材料＝強制の総数と押し出し点数の分布（発火面に限らない全 forced 席）
        for r in getattr(hp, "_b100_log", []) or []:
            c["強制席の総数(_b100_log)"] += 1
            if r.get("displaced_score") is not None:
                disp.append(float(r["displaced_score"]))
        dups = _analyze_game(hp, st)
        if name == "btx5_seal":
            seal["btx5_seal 局数"] += 1
            seal["btx5_seal 防衛"] += int(_outcome(st) == "defense")
        for d in dups:
            c["重複支払いターン（全確度）"] += 1
            if d["prob"] < 0.5 - _EPS:
                continue
            c["★重複支払いターン（prob≥0.5）"] += 1
            g_dup.add(g); s_dup.add(sig)
            c[f"  層別{d['cls']}"] += 1
            via = "+".join(sorted(set(d["vias"])))
            c[f"  経路[{via}]"] += 1
            if d["forced_second"]:
                c["  ★2席目以降がforced（B-185発火面）"] += 1
                g_f2.add(g); s_f2.add(sig)
                for ck in d["bypass_checks"]:
                    c["    素通り検算=OK" if ck["bypass_ok"]
                      else "    素通り検算=矛盾(raw残存)"] += 1
            if d["night_loss"]:
                c["  内数 その夜に敗北"] += 1
            if name == "btx5_seal":
                seal[f"btx5_seal 重複支払い prob≥0.5 [{d['cls']}]"] += 1
            rows.append({"game": g, "sig": sig, **{k: v for k, v in d.items()
                                                   if k != "payers"},
                         "payers": d["payers"]})
    denom = len(all_sigs)
    disp.sort()
    _mid = (disp[len(disp) // 2] if disp else None)
    return {"days": days, "n_games": n, "独立脚本数（分母）": denom,
            "paid(B185) ON": paid,
            "押し出し点数の分布(全forced席)": {
                "n": len(disp), "median": _mid,
                "max": (disp[-1] if disp else None),
                "≥50点": sum(1 for x in disp if x >= 50.0)},
            "counts": dict(c),
            "重複支払い prob≥0.5": {
                "局数": len(g_dup), "独立脚本数": len(s_dup),
                "出現率(独立脚本)": (f"{len(s_dup)}/{denom}"
                                     f"={len(s_dup) / max(denom, 1):.1%}")},
            "うち2席目以降forced（発火面）": {
                "局数": len(g_f2), "独立脚本数": len(s_f2),
                "出現率(独立脚本)": (f"{len(s_f2)}/{denom}"
                                     f"={len(s_f2) / max(denom, 1):.1%}")},
            "btx5_seal 単独行": dict(seal),
            "rows": rows}


# ---------------------------------------------------------------------------
# verify＝観測器が対局を変えていない物証
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None, only: tuple = (), paid: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    bad, n = [], 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        if only and f"{name}#{seed}" not in set(only):
            continue
        _a, _s, t1 = _play(sc, seed, loops, instrument=True, paid=paid)
        _b, _s2, t2 = _play(sc, seed, loops, instrument=False, paid=paid)
        n += 1
        if t1 != t2:
            bad.append(f"{name}#{seed}")
    return {"days": days, "n_games": n, "paid(B185) ON": paid, "mismatch": bad}


# ---------------------------------------------------------------------------
# ★Phase 2：released＝OFF の発火面ターンで「解放された席が ON で何を打ったか」
# ---------------------------------------------------------------------------
def released(days: int = 3, loops: int = 8, start: int = 0,
             end: int | None = None) -> dict:
    """主指標 (b) の1次データ＝OFF の発火面（2席目以降 forced・prob≥0.5）の各ターンを、
    ON の世界の同一 (loop,day) と突き合わせる。

    ★棋譜は最初の挙動差で分岐する＝分岐位置（div_at）より前のターンは厳密比較・
    以後は参考（`diverged=True` を付す）。判定はすべて主人公AIの内部量と公開棋譜のみ。
    """
    from arena.benchmark import benchmark_scripts
    from arena.b145_audit import _outcome

    def _seq(hp):
        seq = []
        for (lp, dy), rec in sorted(hp.turns.items()):
            for p in rec.get("plays", []):
                seq.append((lp, dy, p["seat"], tuple(p["key"])))
        return seq

    rows: list = []
    c: Counter = Counter()
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        g = f"{name}#{seed}"
        hp0, st0, _t0 = _play(sc, seed, loops, paid=False)
        dups0 = [d for d in _analyze_game(hp0, st0)
                 if d["prob"] >= 0.5 - _EPS and d["forced_second"]]
        if not dups0:
            continue
        hp1, st1, _t1 = _play(sc, seed, loops, paid=True)
        s0, s1 = _seq(hp0), _seq(hp1)
        div = next((i for i, (a, b) in enumerate(zip(s0, s1)) if a != b), None)
        if div is None and len(s0) != len(s1):
            div = min(len(s0), len(s1))
        div_at = (s0[div][:2] if div is not None and div < len(s0) else None)
        on_fire = {(d["loop"], d["day"], d["label"])
                   for d in _analyze_game(hp1, st1)
                   if d["prob"] >= 0.5 - _EPS and d["forced_second"]}
        for d in dups0:
            key_t = (d["loop"], d["day"])
            plays1 = (hp1.turns.get(key_t) or {}).get("plays", [])
            served1 = {lb for p in plays1 for (lb, _p, _tn, _f)
                       in p.get("broke", [])}
            keys1 = [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                     f"{'（b100強制）' if p.get('b100') else ''}" for p in plays1]
            disp_restored, alt_served = [], []
            for o in d.get("opportunity", []):
                lab = o.get("displaced")
                if lab and "→" in lab:
                    card, tgt = lab.split("→", 1)
                    disp_restored.append(
                        {"displaced": lab, "score": o.get("displaced_score"),
                         "played_on": any(p["key"][0] == card
                                          and p["key"][1] == tgt
                                          for p in plays1)})
                for a in o.get("alt_unserved_gated", []):
                    alt_served.append({"label": a["label"], "prob": a["prob"],
                                       "served_on": a["label"] in served1})
            diverged = bool(div_at is not None and tuple(key_t) >= tuple(div_at))
            still = (d["loop"], d["day"], d["label"]) in on_fire
            c["OFF発火面ターン"] += 1
            c["  ONでも同一ラベルが発火面のまま"] += int(still)
            c["  ONで解消"] += int(not still)
            c["  分岐後（参考扱い）"] += int(diverged)
            for r_ in disp_restored:
                c["  押し出されていた予定手がONで打たれた"] += int(bool(r_["played_on"]))
                c["  （押し出し予定手の総数）"] += 1
            for a_ in alt_served:
                c["  放置されていた資格つき別脅威がONで拾われた"] += int(bool(a_["served_on"]))
                c["  （放置別脅威の総数）"] += 1
            rows.append({"game": g, "loop": d["loop"], "day": d["day"],
                         "label": d["label"], "cls": d["cls"],
                         "still_fire_on": still, "diverged": diverged,
                         "div_at": (list(div_at) if div_at else None),
                         "off_plays": [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                                       f"{'（b100強制）' if p.get('b100') else ''}"
                                       for p in (hp0.turns.get(key_t) or {})
                                       .get("plays", [])],
                         "on_plays": keys1,
                         "displaced_restored": disp_restored,
                         "alt_served": alt_served,
                         "outcome_off": _outcome(st0),
                         "outcome_on": _outcome(st1)})
    return {"days": days, "counts": dict(c), "rows": rows}


# ---------------------------------------------------------------------------
# turn＝1ターンの深掘り（doc の物証用）
# ---------------------------------------------------------------------------
def turn(days: int, game: str, loop: int, day: int, loops: int = 8,
         paid: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.b145_audit import _outcome

    scripts = {f"{n}#{s}": (n, s, sc)
               for n, s, sc in benchmark_scripts(days=days)}
    _nm, seed, sc = scripts[game]
    hp, st, _t = _play(sc, seed, loops, paid=paid)
    dups = [d for d in _analyze_game(hp, st)
            if d["loop"] == loop and d["day"] == day]
    rec = hp.turns.get((loop, day)) or {}
    out = {"game": game, "loop": loop, "day": day, "outcome": _outcome(st),
           "plays": [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                     f"{'（b100強制）' if p.get('b100') else ''}"
                     for p in rec.get("plays", [])],
           "dups": dups, "per_seat": {}}
    for p in rec.get("plays", []):
        s = p["seat"]
        sti = hp.seat_threats.get((loop, day, s)) or {}
        recs = hp.alloc_trace.get((loop, day, s)) or []
        out["per_seat"][s] = {
            "stage": (recs[-1].get("stage") if recs else None),
            "ret": (recs[-1].get("ret") if recs else None),
            "fatal_threats": {lb: {"prob": e["prob"], "kind": e["kind"],
                                   "raw": sorted(f"{k[0]}→{k[1]}"
                                                 for k in e["raw"])}
                              for lb, e in sorted(sti.items(),
                                                  key=lambda kv: -kv[1]["prob"])}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="B-185 重複支払いの数え上げ（Phase 1/2）")
    ap.add_argument("cmd", choices=["verify", "scan", "turn", "released"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--games", default="")
    ap.add_argument("--game", default="")
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--paid", action="store_true",
                    help="★Phase 2：B185_PAID_LABELS=ON の世界を測る")
    a = ap.parse_args()
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end,
                     only=tuple(x for x in a.games.split(",") if x), paid=a.paid)
    elif a.cmd == "scan":
        res = scan(a.days, a.loops, a.start, a.end, paid=a.paid)
    elif a.cmd == "released":
        res = released(a.days, a.loops, a.start, a.end)
    else:
        res = turn(a.days, a.game, a.loop, a.day, a.loops, paid=a.paid)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    out = {k: v for k, v in res.items() if k != "rows"}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
