# -*- coding: utf-8 -*-
"""B-262：**belief から脚本家の不安供給を見積もる「第3の上限」**（★フェーズ0＝実装前の検算）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-68 の未解決1（B-256 が残した穴）。
方針＝§72-43「席の点数いじりではなく belief（推論）の質に寄せる」。

## 何を測るか（フェーズ0）

B-256 は到達可能上限を2つ定義した：

- `U_p  = u + R`  ……主人公陣営のみ（1キャラ1日1枚＝`rules/00:104`＋`rules/10:81`）
  ＝**止める面積は広いが破れる**（5日級 3/126）。
- `U_pm = u + 2R` ……脚本家も毎日1枚重ねる仮定を置いた版
  ＝**破れないが面積は半分以下・利得もほぼ消える**。

本モジュールは**その中間**を作るための材料を数える：

1. ★**`U_p` が破れた (局, L, 対象) を全部特定し、その破れの内訳を席まで下ろす**
   （＝主人公陣営が2枚置いた〔＝上限定義のバグ〕のか／脚本家の札か／脚本家能力か／事件か）。
2. ★**belief／公開情報から「脚本家がこの対象に不安を供給しそうか」を推し量る候補述語**を
   席ごとに記録し、**(a) 破れの被覆率**と **(b) 巻き添え（＝止める価値のある席を何席戻すか）**の
   両方を出す。★E=0 は `U_pm` で既に達成できる＝**的は「`U_pm` より利得を落とさずに E=0」**。

## 挙動には触れない

`agents/` `sim/` `engine/` の既定値を一切書き換えない。観測は
`agents.heuristic_protagonist.B100_HOOK`（計測専用フック・戻り値不使用）と
`sim.flow.run_day` の**読み取り専用ラッパ**（不安のスナップショット）だけ。
`arena/b256_audit.py`・`arena/b249_audit.py`・`arena/b250_audit.py`・`arena/knob_audit.py` は
**1バイトも変更しない**（import して使う）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b262_audit seats  --days 5 --pick all --outdir /tmp/b262/d5
    python -m arena.b262_audit report --outdir /tmp/b262/d5
    python -m arena.b262_audit verify --days 5 --pick random_BTX:0-2
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import replace

from arena.b249_audit import check_no_knob_writes, defaults_banner
from arena.b250_audit import _truth_of, parse_pick
from arena.b256_audit import UNREST_GOAL, UNREST_PLUS, bounds, knob_baseline_banner

#: 「不安を増やしうる」事件（`sim/effects.py` の現物＝公開の事件リストから見える名前）。
#: 不安拡大＝任意1人に不安+2（`:449`）／ご神木・蝶の羽ばたき＝犯人と同エリアの1人に選択+1（`:435`）。
UNREST_INCIDENTS = ("不安拡大", "ご神木の怪", "蝶の羽ばたき")


class Rec:
    """1局分の記録。"""

    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.rows: list[dict] = []     # `U_p<3` と判定された (L,D,席,対象) の延べ
        self.snap: dict = {}           # "L:D" -> {name: unrest}（その日のターン終了後）
        self.hist: list[dict] = []     # 局終了時の公開履歴（＝主人公が見られる情報のみ）
        self.err: list[str] = []
        self.meta: dict = {}

    def to_json(self) -> dict:
        return {"counts": dict(self.c), "rows": self.rows, "snap": self.snap,
                "hist": self.hist, "err": self.err, "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> "Rec":
        r = cls()
        r.c.update(d.get("counts", {}))
        r.rows = list(d.get("rows", []))
        r.snap = dict(d.get("snap", {}))
        r.hist = list(d.get("hist", []))
        r.err = list(d.get("err", []))
        r.meta = dict(d.get("meta", {}))
        return r


# ---------------------------------------------------------------------------
# 候補述語の材料（★すべて主人公ビューの公開情報 or belief から取る）
# ---------------------------------------------------------------------------
def mm_signals(view: dict, belief, name: str) -> dict:
    """対象 `name` について「脚本家が不安を供給しそうか」の材料を公開情報から数える。

    出典＝`sim/flow.py:223` の `cards_revealed`（全6枚が毎日公開＝`rules/00:106`）と
    `sim/effects.py:260-263`（脚本家能力フェイズの不安＝公開イベント）。
    ★どれも主人公ビューに載っている情報だけ（`sim/views.py` の `_common`）。
    """
    lp = view.get("loop")
    sig = Counter()
    for e in view.get("history", []):
        if e.get("event") == "cards_revealed":
            for p in e.get("placements", []):
                if p.get("owner") != "mastermind":
                    continue
                if p.get("target_kind") != "character":
                    continue
                if p.get("card") == UNREST_PLUS:
                    sig["mm_unrest_any"] += 1
                    if p.get("target") == name:
                        sig["mm_unrest_c"] += 1
                        if e.get("loop") == lp:
                            sig["mm_unrest_c_loop"] += 1
                if p.get("target") == name:
                    sig["mm_card_c"] += 1
                    if e.get("loop") == lp:
                        sig["mm_card_c_loop"] += 1
        elif (e.get("event") == "unrest" and e.get("delta", 0) > 0
                and e.get("phase") == "mastermind_ability"):
            sig["mm_ab_unrest_any"] += 1
            if e.get("target") == name:
                sig["mm_ab_unrest_c"] += 1
    # 今日の伏せ札（`sim/views.py` の `_masked_placements`＝位置と持ち主は公開）
    sig["mm_face_down_today_c"] = sum(
        1 for p in view.get("placements", [])
        if p.get("owner") == "mastermind" and p.get("target") == name
        and p.get("target_kind") == "character")
    # 残り日に不安を増やしうる予定事件があるか（事件リストは日＋名前が公開）
    day = int(view.get("day") or 0)
    dpl = int(view.get("days_per_loop") or 0)
    sig["inc_unrest_left"] = sum(
        1 for i in view.get("incidents", [])
        if day <= int(i.get("day", 0)) <= dpl and i.get("name") in UNREST_INCIDENTS)
    out = dict(sig)
    # belief 側の材料＝ミスリーダー／ファクター（脚本家能力フェイズの不安の供給源）の事後。
    try:
        marg = belief.role_marginals()
        out["p_ml_alive"] = round(sum(
            d.get("ミスリーダー", 0.0) + d.get("ファクター", 0.0)
            for n, d in marg.items()
            if (next((o for o in view["characters"] if o["name"] == n), {}) or {}).get("alive")
        ), 4)
        out["p_ml_c"] = round(marg.get(name, {}).get("ミスリーダー", 0.0)
                              + marg.get(name, {}).get("ファクター", 0.0), 4)
    except Exception as exc:      # noqa: BLE001
        out["p_ml_alive"] = -1.0
        out["p_ml_c"] = -1.0
        out["belief_err"] = str(exc)[:80]
    return out


def _hook_factory(rec: Rec, script_obj, name: str, seed: int):
    """`B100_HOOK` に挿す観測子（★戻り値は使われない＝棋譜に影響しない）。"""
    def _hook(agent, view, options, best, score):
        try:
            rec.c["席（set_card 決定）"] += 1
            vt = sorted(getattr(agent, "_virus_test_targets", ()) or ())
            if not vt:
                return
            lp, dy = view.get("loop"), view.get("day")
            dpl = view.get("days_per_loop")
            alive = {o["name"]: o for o in view["characters"] if o.get("alive")}
            played_here = (best["card"] == UNREST_PLUS
                           and best.get("target_kind") == "character")
            for n in vt:
                c = alive.get(n)
                if c is None:
                    continue
                u = int(c.get("unrest", 0))
                r, up, upm = bounds(u, dy, dpl)
                if up >= UNREST_GOAL:
                    rec.c["U_p>=3（そもそも外さない席）"] += 1
                    continue
                row = {"script": name, "seed": seed, "loop": lp, "day": dy,
                       "seat": view.get("seat"), "target": n, "u": u, "R": r,
                       "U_p": up, "U_pm": upm,
                       "played": bool(played_here and best.get("target") == n),
                       "true_role": _truth_of(script_obj, n)}
                row.update(mm_signals(view, agent._belief, n))
                rec.rows.append(row)
        except Exception as exc:   # noqa: BLE001  （hook 側の except に食われる前に記録）
            rec.err.append(f"{name}#{seed}: {type(exc).__name__}: {exc}")
    return _hook


def _run(script_obj, name: str, seed: int, loops: int, rec: Rec | None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hp_mod
    from sim import flow as flow_mod
    from sim import run_game

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
        rec.hist = [dict(e) for e in state.history]
        rec.meta.update({"winner": state.winner, "loop_no": state.loop_no,
                         "days_per_loop": script_obj.days_per_loop})
    return state


# ---------------------------------------------------------------------------
# 破れの内訳（★上限定義のバグかどうかの切り分け）
# ---------------------------------------------------------------------------
def unrest_ledger(hist: list[dict], loop: int, target: str, from_day: int) -> list[dict]:
    """(loop, target) の `from_day` 以降の**不安が増えた経路**を公開履歴から起こす。

    - 行動解決フェイズの `不安+1` は**イベントを発行しない**（`sim/flow.py` は
      `adj.unrest` を直接代入する）＝`cards_revealed` の**配置**から起こす。
      ★**持ち主も公開**なので「主人公陣営が2枚置いた」なら必ずここに出る。
    - それ以外（脚本家能力・事件・ラバーズ・因果の糸）は `unrest` イベントで出る。
    """
    out: list[dict] = []
    for e in hist:
        if e.get("loop") != loop or int(e.get("day", 0)) < from_day:
            continue
        if e.get("event") == "cards_revealed":
            for p in e.get("placements", []):
                if (p.get("card") == UNREST_PLUS
                        and p.get("target_kind") == "character"
                        and p.get("target") == target):
                    out.append({"day": e.get("day"), "src": "card",
                                "owner": p.get("owner"), "delta": 1})
        elif (e.get("event") == "unrest" and e.get("target") == target
                and e.get("delta", 0) > 0):
            out.append({"day": e.get("day"), "src": e.get("phase") or "?",
                        "owner": "-", "delta": e.get("delta")})
    return out


def _break_map(rec: Rec) -> dict:
    """(loop, target) -> そのループ中 `from_day` 以降に到達した不安の最大値。"""
    first_day: dict = {}
    for r in rec.rows:
        key = (r["loop"], r["target"])
        d = int(r["day"])
        if key not in first_day or d < first_day[key]:
            first_day[key] = d
    out: dict = {}
    for (lp, tgt), d0 in first_day.items():
        m = 0
        for k, snap in rec.snap.items():
            klp, _, kdy = k.partition(":")
            if int(klp) == lp and int(kdy) >= d0:
                m = max(m, int(snap.get(tgt, 0)))
        out[(lp, tgt)] = (d0, m)
    return out


# ---------------------------------------------------------------------------
# 候補述語（★belief／公開情報から「脚本家が供給しそうか」を判定する案）
# ---------------------------------------------------------------------------
def predicates(row: dict) -> dict:
    """席1件について候補述語の真偽を返す（True＝「供給しそう」＝`U_pm` 側に倒す＝外さない）。"""
    return {
        "P0_never": False,                                   # ＝`U_p` そのもの（対照）
        "P1_hist_c": row.get("mm_unrest_c", 0) > 0,          # その対象へ過去に不安札の実績
        "P2_hist_any": row.get("mm_unrest_any", 0) > 0,      # 誰かへ不安札の実績（相手の芸風）
        "P3_facedown": row.get("mm_face_down_today_c", 0) > 0,   # 今日その対象に伏せ札
        "P4_card_c": row.get("mm_card_c", 0) > 0,            # その対象へ何かの札の実績
        "P5_ab_any": row.get("mm_ab_unrest_any", 0) > 0,     # 能力フェイズの不安の実績
        "P6_inc": row.get("inc_unrest_left", 0) > 0,         # 残り日に不安を増やす予定事件
        "P7_ml": row.get("p_ml_alive", 0.0) > 0.5,           # ML/ファクター在の事後 > 0.5
        "P8_any_evi": (row.get("mm_unrest_any", 0) > 0
                       or row.get("mm_ab_unrest_any", 0) > 0),   # 不安供給の実績（札 or 能力）
        "P9_hist_or_fd": (row.get("mm_unrest_c", 0) > 0
                          or row.get("mm_face_down_today_c", 0) > 0),
        # ★本命候補＝「その対象へ不安札を置く**実績**」×「今日その対象に**伏せ札がある**」の連言。
        #   前者＝相手の実証済みの癖（`_b76_delivery_proven` と同じ形の公開実績）／
        #   後者＝今日の供給の物理的な可能性（伏せ札の位置と持ち主は公開＝`sim/views.py`）。
        "P11_hist_and_fd": (row.get("mm_unrest_c", 0) > 0
                            and row.get("mm_face_down_today_c", 0) > 0),
        "P12_histloop_and_fd": (row.get("mm_unrest_c_loop", 0) > 0
                                and row.get("mm_face_down_today_c", 0) > 0),
        "P13_histany_and_fd": (row.get("mm_unrest_any", 0) > 0
                               and row.get("mm_face_down_today_c", 0) > 0),
        "P10_always": True,                                  # ＝`U_pm` そのもの（対照）
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_seats(a) -> int:
    before = defaults_banner()
    print(f"[b262] {knob_baseline_banner()}")
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
              f"U_p<3 延べ{len(rec.rows)} err{len(rec.err)} "
              f"winner={st.winner} L{st.loop_no}", flush=True)
    check_no_knob_writes(before, driver="arena.b262_audit")
    print("[b262] 切替口の健全性検査 ✅（測定前後で1bitも動いていない）")
    return 0


def cmd_verify(a) -> int:
    """★プローブの無害性＝観測あり／なしで棋譜が1ビットも動かないことを確認する。"""
    games = parse_pick(a.pick, a.days)
    bad = 0
    for name, seed, sc in games:
        s0 = _run(sc, name, seed, a.loops, None)
        r1 = Rec()
        s1 = _run(sc, name, seed, a.loops, r1)
        h0 = json.dumps(s0.history, ensure_ascii=False, sort_keys=True)
        h1 = json.dumps(s1.history, ensure_ascii=False, sort_keys=True)
        ok = (h0 == h1 and s0.winner == s1.winner and s0.loop_no == s1.loop_no)
        print(f"  {name}#{seed}: 棋譜一致={ok} フック例外={len(r1.err)}")
        bad += 0 if ok else 1
        bad += len(r1.err)
    print(f"[b262] プローブの無害性: 不一致 {bad} 件")
    return 1 if bad else 0


def _load(outdir: str) -> list[Rec]:
    out = []
    for fn in sorted(os.listdir(outdir)):
        if fn.endswith(".json"):
            with open(os.path.join(outdir, fn), encoding="utf-8") as f:
                out.append(Rec.from_json(json.load(f)))
    return out


def cmd_report(a) -> int:
    recs = _load(a.outdir)
    rows = [r for rec in recs for r in rec.rows]
    errs = [e for rec in recs for e in rec.err]
    print(f"== {a.outdir} ==  局数 {len(recs)} ／ フック例外 {len(errs)} 件")
    if errs:
        print("  " + "\n  ".join(errs[:5]))
    print(f"[N] `U_p<3` と判定された席の延べ = {len(rows)}"
          f" ／ うち `不安+1` 実打 = {sum(1 for r in rows if r['played'])}"
          f" ／ うち `U_pm<3`（＝`U_pm` でも外す）= {sum(1 for r in rows if r['U_pm'] < UNREST_GOAL)}")

    # ---- 破れ E の特定と内訳 -------------------------------------------
    n_key = 0
    broke: list[tuple] = []
    for rec in recs:
        bm = _break_map(rec)
        n_key += len(bm)
        for (lp, tgt), (d0, mx) in bm.items():
            if mx >= UNREST_GOAL:
                led = unrest_ledger(rec.hist, lp, tgt, d0)
                broke.append((rec.meta.get("script"), rec.meta.get("seed"),
                              lp, tgt, d0, mx, led))
    print(f"[E] ★上限の破れ＝`U_p<3` と判定した (局,L,対象) {n_key} 件のうち "
          f"実際に不安3以上へ達した = **{len(broke)}**")
    own = Counter()
    for sc, sd, lp, tgt, d0, mx, led in broke:
        print(f"  ★{sc}#{sd} L{lp} D{d0}〜 {tgt}: 最大不安={mx}")
        for x in led:
            print(f"      D{x['day']} {x['src']} owner={x['owner']} +{x['delta']}")
            own[(x["src"], x["owner"])] += x["delta"]
    if broke:
        print("  [内訳合計] " + " ".join(f"{s}/{o}:+{v}" for (s, o), v in own.most_common()))

    # ---- ★上限定義のバグ検査＝主人公陣営が同じ日に2枚置いていないか ----
    dup = 0
    for rec in recs:
        per: Counter = Counter()
        for e in rec.hist:
            if e.get("event") != "cards_revealed":
                continue
            for p in e.get("placements", []):
                if (p.get("card") == UNREST_PLUS and p.get("target_kind") == "character"
                        and p.get("owner") != "mastermind"):
                    per[(e.get("loop"), e.get("day"), p.get("target"))] += 1
        dup += sum(1 for v in per.values() if v >= 2)
    print(f"[★上限定義の検査] 主人公陣営が同一 (L,D,キャラ) に `不安+1` を2枚以上置いた回数 = **{dup}**"
          f"（>0 なら `U_p = u+R` の定義が誤り＝最優先で報告）")

    # ---- 候補述語の被覆率と巻き添え ------------------------------------
    brk_keys = {(sc, sd, lp, tgt) for sc, sd, lp, tgt, _d, _m, _l in broke}
    names = list(predicates(rows[0] if rows else {}).keys())
    print("[述語] True＝「脚本家が供給しそう」＝**外さない**（`U_pm` 側に倒す）")
    print("  ★被覆(全席)＝破れた (局,L,対象) の**全席**でフラグが立つ＝その帯で1席も外さない"
          "（E=0 の十分条件）／被覆(一部)＝少なくとも1席で立つ")
    n_played = sum(1 for r in rows if r["played"])
    # ★★第3の上限が**実際に働ける帯**＝`U_p < 3 <= U_pm`。
    #   `U_pm < 3` の席は「脚本家が毎日置いても届かない」＝述語が立っても外れたまま
    #   ＝述語の発火数をそのまま巻き添えと読むと**過大評価**になる（フェーズ0 の自己訂正）。
    band = [r for r in rows if r["U_p"] < UNREST_GOAL <= r["U_pm"]]
    print(f"[帯] ★第3の上限が働ける帯（`U_p<3<=U_pm`）= {len(band)}"
          f" ／ うち `不安+1` 実打 = {sum(1 for r in band if r['played'])}"
          f"（＝`U_pm` が取りこぼす利得の上限）")
    print(f"{'述語':<20}{'帯内発火':>7}{'巻添(実打)':>11}{'巻添率':>8}"
          f"{'被覆(全席)':>11}{'被覆(一部)':>11}{'全発火':>9}{'(実打)':>7}")
    for nm in names:
        fire = 0
        fire_played = 0
        per_key: dict = {}
        band_fire = 0
        band_played = 0
        for rec in recs:
            for r in rec.rows:
                key = (rec.meta.get("script"), rec.meta.get("seed"),
                       r["loop"], r["target"])
                hit = bool(predicates(r)[nm])
                a, b = per_key.get(key, (0, 0))
                per_key[key] = (a + (1 if hit else 0), b + 1)
                if hit:
                    fire += 1
                    if r["played"]:
                        fire_played += 1
                    if r["U_p"] < UNREST_GOAL <= r["U_pm"]:
                        band_fire += 1
                        if r["played"]:
                            band_played += 1
        full = sum(1 for k in brk_keys if per_key.get(k, (0, 1))[0] == per_key.get(k, (0, 1))[1])
        part = sum(1 for k in brk_keys if per_key.get(k, (0, 0))[0] > 0)
        rate = f"{100.0*band_played/n_played:.1f}%" if n_played else "—"
        print(f"{nm:<20}{band_fire:>7}{band_played:>11}{rate:>8}"
              f"{full:>8}/{len(brk_keys)}{part:>8}/{len(brk_keys)}"
              f"{fire:>9}{fire_played:>7}")
    # ★本命候補の巻き添えの脚本別内訳
    tgt_pred = "P11_hist_and_fd"
    coll = Counter((rec.meta.get("script"))
                   for rec in recs for r in rec.rows
                   if r["played"] and predicates(r)[tgt_pred]
                   and r["U_p"] < UNREST_GOAL <= r["U_pm"])
    base = Counter(rec.meta.get("script") for rec in recs for r in rec.rows if r["played"])
    print(f"[巻き添えの脚本別内訳・{tgt_pred}] "
          + " ".join(f"{k}:{coll.get(k, 0)}/{v}" for k, v in base.most_common()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="arena.b262_audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("seats", "verify"):
        p = sub.add_parser(nm)
        p.add_argument("--days", type=int, default=5)
        p.add_argument("--loops", type=int, default=8)
        p.add_argument("--pick", default="all")
        p.add_argument("--outdir", default="/tmp/b262")
        p.add_argument("--force", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--outdir", default="/tmp/b262")
    a = ap.parse_args(argv)
    return {"seats": cmd_seats, "verify": cmd_verify, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
