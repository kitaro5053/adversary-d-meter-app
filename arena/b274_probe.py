# -*- coding: utf-8 -*-
"""B-274 プローブ（★フェーズ0＝計測のみ。`agents/` `sim/` `engine/` `rules/` は1バイトも触らない）。

チケット＝`_B64_INCIDENTS` の事件種ゲートを広げられるか。
前提＝B-269 レーンの副産物（手筋は実装に在る＝`_b64_ml_approach_block`／`ML寄せブロック`=79.0。
到達しない理由は事件種ゲート `_B64_INCIDENTS = ("蝶の羽ばたき",)`）。

反実仮想は**評価器を書き換えず**、`_b64_ml_approach_block` を包んで
「同じ席で `_B64_INCIDENTS` だけ差し替えて呼び直す」影計測で行う（既定は素の結果を返す＝
挙動 bit 不変。`--apply` を付けた時だけ差し替えた結果を返して再生する）。

================================================================================
★事前登録（測る前に書く・後から的を動かさない）
================================================================================

## 測る量

| # | 的 | 形 |
|---|---|---|
| P0-a | **現状の射程** | コーパス全体（3日級140局・5日級80局）で `_b64_ml_approach_block` が **True を返した席数**（分母＝`set_card` の総席数） |
| P0-b | **広げた場合の射程** | 各候補ゲート集合で True になる席数（**素の対局軌道上での影計測**＝baseline の play を1bitも変えずに数える） |
| P0-c | **退行の再現** | ソース註記の「供給事件（行方不明/邪気の汚染）を含めた初版が 3日級 BTX#2 で防衛 L5→L8 に遅延」が**現在のコードで再現するか**（`--apply` で実際に再生し `loops_to_win` を比較） |
| P0-d | 述語の素案 | (c) の機序から「席の在庫と対の会計」を条件に入れた素案（**実装しない**） |
| P0-e | **上限の見積り** | (b) の席で実際に `ML寄せブロック` を通した時、**その日の事件が発生しなくなる席数** |

## ★着手可／不可の判定基準（フェーズ1へ進むか）

以下を**すべて**満たしたときのみ「フェーズ1へ進むことを推奨」と書く。
1つでも欠けたら**負の結果**として報告する（忖度しない）。

- **(判定1／§72-48 原理的な非ゼロ)**：どれかの候補ゲート集合で
  **(b) の席数 ≥ 1**（コーパス220局で）。**0 なら着手不可**（構造的に届かない）。
- **(判定2／§72-53 強さへの接続)**：(e) の「事件が発生しなくなる席数 ≥ 1」
  かつ **`--apply` 再生で `loops_to_win` が改善する局が 1 局以上**存在する。
  **決着先（outcome）が入れ替わるだけ／平均だけが動く**なら着手不可。
- **(判定3／§72-78 較正条件に固有でない)**：改善が出た候補集合について
  **`perm` 4条件（id / rev / h1 / h5）で防衛数の向きが揃う**
  （＝どの条件でも 防衛数が baseline 以上）。**`id` だけで利得が出たら負けと判定する**。
- **(判定4／§72-88 動機を捕まえるか)**：(b) で拾える席に
  **`btx_lovers` 族（動機の脚本）の席が含まれる**。含まれないなら
  「動機を1つも捕まえていない」＝着手不可（B-269 と同じ負け方）。
- **(判定5／(c) の退行)**：`--apply` 再生で **3日級・5日級のどちらでも防衛数が baseline を下回らない**。
  下回るなら、その候補集合は**そのままでは**着手不可（＝(d) の述語が必要）。

★**±1〜2 を効果の証拠にしない**（規約§11b）。判定2 の「改善」は per-game flip で数え、
判定3 の「向きが揃う」を満たして初めて効果と呼ぶ。

## 候補ゲート集合の根拠（KB とソースの実態から列挙）

`_b64_ml_approach_block` が止めているのは「事件そのもの」ではなく
**犯人の不安が臨界に届くこと**（`supply - cool == th` のピボット算術）＝
∴ ゲートに載せるべきは「**その日に発生させたくない事件**」であって
「不安を供給する事件」ではない（★FableA の見立ての語彙をそのまま受け取らない）。

- KB で「不安カウンターを**同一エリア条件つき**でキャラに置く」事件は
  **蝶の羽ばたきだけ**（`rules/50_basic_tragedy_x.md:218-220`）。
  不安拡大（`:190` 相当）は「**任意の**キャラ1人に不安2」＝エリア条件が無い。
  ∴「不安の能力供給が同一エリア条件つきで起きる事件種」で集合を作ると**蝶しか入らない**＝現行と同じ。
- ソースには既に2つの事件種集合があり、これが実態上の分類である：
  - `_B52_KILL_INCIDENTS = ("遠隔殺人", "殺人事件", "病院の事件")`（`agents/heuristic_protagonist.py:3422`）＝**直接死亡**系。
  - `_B54_INCIDENTS = _B52_KILL_INCIDENTS + ("行方不明", "邪気の汚染")`（同 `:3480`）＝上記＋**板への暗躍供給**系。
- 註記に残る「初版」は**供給事件（行方不明/邪気の汚染）**を含んでいた（同 `:4664-4667`）。

∴ 候補集合：

| キー | 集合 | 根拠 |
|---|---|---|
| `cur` | 蝶 | 現行（対照） |
| `kill` | 蝶＋殺人事件/遠隔殺人/病院の事件 | `_B52_KILL_INCIDENTS`＝直接死亡 |
| `board` | 蝶＋行方不明/邪気の汚染 | ★**註記の「初版」＝(c) の再現対象** |
| `b54` | 蝶＋`_B54_INCIDENTS` | 既存の広い集合 |
| `unrest` | 蝶＋不安拡大 | KB で不安を供給する唯一の他事件（エリア条件なし） |
| `all` | BTX 全9種＋FS 7種 | 上限（射程の天井を知るため） |

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b274_probe knobs                       # 切替口の健全性
    python -m arena.b274_probe scope --days 3 --out s3.json # (a)(b) 影計測
    python -m arena.b274_probe scope --days 5 --out s5.json
    python -m arena.b274_probe apply --gate board --days 3  # (c)(e) 再生
    python -m arena.b274_probe perm  --gate kill --modes id,rev,h1,h5 --days 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

from agents.heuristic_protagonist import PRIORITY, HeuristicProtagonist

DRIVER = "arena.b274_probe"

#: 現行のゲート（対照）。
CUR = ("蝶の羽ばたき",)
KILL = ("遠隔殺人", "殺人事件", "病院の事件")
BOARD = ("行方不明", "邪気の汚染")
UNREST = ("不安拡大",)
ALL_INC = ("殺人事件", "不安拡大", "自殺", "病院の事件", "遠隔殺人",
           "行方不明", "流布", "邪気の汚染", "蝶の羽ばたき")

GATES: dict[str, tuple[str, ...]] = {
    "cur": CUR,
    "kill": CUR + KILL,
    "board": CUR + BOARD,
    "b54": CUR + KILL + BOARD,
    "unrest": CUR + UNREST,
    "all": ALL_INC,
}

#: ★(d) の述語素案（**測定用の追加条件**＝`agents/` には入れない）。
#  キー＝`GATES` のキー＋"@" ＋素案名。`_guard_ok` が False を返す席では
#  ゲートが通っていても発火させない＝「席の在庫と対の会計」を入れた版の反実仮想。
#
#  - `D1`（席の在庫）＝**この席がチーム最後の冷却席**（手札に `不安-1` があり、
#    今日この後の他席では出せない）なら譲る。退行の機序（唯一残った守りの札を
#    79 のピンが奪う）をそのまま条件にしたもの。
#  - `D1n6`（在庫＋対の会計）＝`D1` に加え、**当日の犯人候補が6人以下**の日だけ通す。
#    ★これは 5日級の唯一の退行日（候補8人＝belief が何も絞れていない日）を外す
#    ために置いた床であり、**n=1 の退行から引いた線＝過適合の疑いが濃い**（報告に明記）。
GUARDS: dict[str, object] = {
    "D1": (lambda c: not (c["hand_cool"] and not c["other_cool"])),
    "D1n6": (lambda c: not (c["hand_cool"] and not c["other_cool"])
             and c["n_cands"] <= 6),
}

_ORIG_BLOCK = HeuristicProtagonist._b64_ml_approach_block
_ORIG_DECIDE = HeuristicProtagonist.decide

#: 影計測の記録（席ごと）。
_REC: list[dict] = []
#: 現在の席の識別子（`decide` ラッパが更新）。
_SEAT: dict = {}
#: `set_card` の総席数（分母）。
_SEAT_N = [0]


# ---------------------------------------------------------------------------
# 健全性検査
# ---------------------------------------------------------------------------
def knobs() -> str:
    ok = []
    ok.append(f"_B64_ML_APPROACH = {HeuristicProtagonist._B64_ML_APPROACH}")
    ok.append(f"_B64_INCIDENTS   = {HeuristicProtagonist._B64_INCIDENTS}")
    ok.append(f"_B52_ML_MIN      = {HeuristicProtagonist._B52_ML_MIN}")
    ok.append(f"PRIORITY[ML寄せブロック] = {PRIORITY['ML寄せブロック']}")
    assert HeuristicProtagonist._B64_INCIDENTS == CUR, "現行ゲートが想定と違う"
    assert HeuristicProtagonist._B64_ML_APPROACH is True
    return "\n".join("  " + s for s in ok)


# ---------------------------------------------------------------------------
# 影計測のインストール
# ---------------------------------------------------------------------------
def install(gates: dict[str, tuple[str, ...]], apply_key: str | None = None,
            record: bool = True, perm_mode: str | None = None) -> None:
    """`_b64_ml_approach_block` を影計測ラッパで包む。

    apply_key=None＝**素の結果を返す**（挙動 bit 不変）。
    apply_key="board" 等＝そのゲートでの結果を返す（＝実際に広げて再生する）。

    ★perm_mode＝`arena.tie_noise.permute` の並べ替えを**このラッパの中で**掛ける。
      `tie_noise.install_perm` を併用してはいけない（あちらも `decide` を差し替えるので
      後勝ちで一方が消える＝実際に消えて perm 4条件が全部同じ値になる事故を起こした）。
    """
    keys = list(gates)
    _pm = None if perm_mode in (None, "", "id") else perm_mode
    _gk, _guard = (apply_key.split("@") + [None])[:2] if apply_key else (None, None)
    _gf = GUARDS[_guard] if _guard else None

    def _block(self, view, tgt, mm_char_now):
        res = {}
        _saved = self.__dict__.pop("_B64_INCIDENTS", None)
        try:
            for k in keys:
                self._B64_INCIDENTS = gates[k]
                res[k] = bool(_ORIG_BLOCK(self, view, tgt, mm_char_now))
        finally:
            self.__dict__.pop("_B64_INCIDENTS", None)
            if _saved is not None:
                self._B64_INCIDENTS = _saved
        ctx = _ctx(self, view) if (record and any(res.values())) or _gf else None
        if record and any(res.values()):
            _REC.append({**_SEAT, "tgt": tgt, **res, **ctx})
        if not _gk:
            return res["cur"]
        out = res[_gk]
        if out and _gf is not None and not _gf(ctx):
            out = res["cur"]   # 素案が譲ると言った席＝現行ゲートの答えに戻す
        return out

    def _decide(self, view, decision, options):
        if _pm and decision in ("set_card", "goodwill_ability") \
                and len(options) > 1:
            from arena.tie_noise import permute
            options = permute(options, _pm)
        if decision != "set_card":
            return _ORIG_DECIDE(self, view, decision, options)
        _SEAT_N[0] += 1
        _SEAT.clear()
        _SEAT.update(loop=view.get("loop"), day=view.get("day"),
                     seat=view.get("seat"))
        n0 = len(_REC)
        chosen = _ORIG_DECIDE(self, view, decision, options)
        for r in _REC[n0:]:
            r["chosen_card"] = chosen.get("card")
            r["chosen_target"] = chosen.get("target")
            r["hand"] = list(view.get("hand", ()))
        return chosen

    HeuristicProtagonist._b64_ml_approach_block = _block
    HeuristicProtagonist.decide = _decide


def _ctx(self, view: dict) -> dict:
    """(d) 用のセンサス項目（すべて読み取りのみ・自明情報）。

    - `hand_cool`  ＝この席の手札に `不安-1` があるか
    - `team_cool`  ＝今日この後の自チーム席のどこかで `不安-1` を出せるか
                     （`_b63_seat_can_play`＝B-64 の算術が使っているのと同じ会計）
    - `other_cool` ＝**この席を除いて**出せるか（＝この席が「最後の冷却席」かの判定）
    - `n_cands`    ＝今日の犯人候補の数（＝守るべき対の数の代理）
    - `danger`     ＝`_incident_danger[today]`（当日の事件危険度）
    - `inc_today`  ＝今日予定されている事件名
    """
    today = view.get("day")
    hand = tuple(view.get("hand", ()))
    try:
        team_cool = bool(self._b63_seat_can_play(view, "不安-1"))
    except Exception:
        team_cool = None
    # この席を除いた在庫＝手札から `不安-1` を抜いた view で同じ会計を回す
    other = None
    try:
        v2 = dict(view)
        v2["hand"] = tuple(c for c in hand if c != "不安-1")
        other = bool(self._b63_seat_can_play(v2, "不安-1"))
    except Exception:
        other = None
    cands = tuple((getattr(self, "_culprit_cands", {}) or {}).get(today, ()))
    return {
        "hand_cool": "不安-1" in hand,
        "team_cool": team_cool,
        "other_cool": other,
        "n_cands": len(cands),
        "cands": list(cands),
        "danger": float((getattr(self, "_incident_danger", {}) or {}).get(today, 0.0)),
        "inc_today": sorted({i.get("name") for i in view.get("incidents", [])
                             if i.get("day") == today}),
    }


def uninstall() -> None:
    HeuristicProtagonist._b64_ml_approach_block = _ORIG_BLOCK
    HeuristicProtagonist.decide = _ORIG_DECIDE


def reset() -> None:
    _REC.clear()
    _SEAT.clear()
    _SEAT_N[0] = 0


# ---------------------------------------------------------------------------
# コーパス走査
# ---------------------------------------------------------------------------
_CORPUS: dict[int, list] = {}
_RACE: dict[tuple[int, str, int], str] = {}


def _corpus(days: int):
    """`benchmark_scripts` は乱数脚本の生成に約50秒かかる＝1プロセス内で1回だけ作る。

    ★`Script` は frozen ではないので**毎回 deepcopy して渡す**（対局が脚本を書き換えても
    次の条件に漏れない＝条件間の独立を構造で担保する）。
    """
    if days not in _CORPUS:
        from arena.benchmark import benchmark_scripts
        _CORPUS[days] = benchmark_scripts(days=days)
    return _CORPUS[days]


def _run_corpus(days: int, loops: int = 8, gates=None, apply_key=None,
                record=True, verbose=False, perm_mode=None):
    """コーパス全局を回し、(rows, 席記録) を返す。"""
    import copy
    from dataclasses import replace

    from agents import HeuristicMastermind
    from sim import run_game
    from sim.loop_race import analyze_script

    gates = gates or GATES
    reset()
    install(gates, apply_key=apply_key, record=record, perm_mode=perm_mode)
    rows = []
    per_game_rec: dict[str, list[dict]] = {}
    incidents_by_game: dict[str, dict] = {}
    try:
        for name, seed, _sc0 in _corpus(days):
            sc = copy.deepcopy(_sc0)
            key = f"{name}#{seed}"
            n0 = len(_REC)
            probe = replace(sc, loops=loops)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp,
                                        "p2": hp, "p3": hp})
            fb = any(e.get("event") == "final_battle" for e in state.history)
            if state.winner == "protagonist" and not fb:
                ltw, outcome = state.loop_no, "defense"
            elif fb:
                ltw = loops + 1
                outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
            else:
                ltw, outcome = loops + 1, "loss"
            _rk = (days, name, seed)
            if _rk not in _RACE:
                _RACE[_rk] = analyze_script(sc).verdict
            race = _RACE[_rk]
            rows.append({"script": name, "seed": seed, "loops_to_win": ltw,
                         "outcome": outcome, "race": race})
            for r in _REC[n0:]:
                r["game"] = key
            per_game_rec[key] = [dict(r) for r in _REC[n0:]]
            # 事件の発生/不発（(e) 用）＝secret_log から (loop, day, name) → occurs
            occ = {}
            for e in state.secret_log:
                if e.get("event") == "incident":
                    occ[(e.get("loop"), e.get("day"))] = (e.get("name"),
                                                          bool(e.get("occurs")))
            incidents_by_game[key] = occ
            if verbose:
                print(f"  {key}: {ltw} {outcome}", flush=True)
    finally:
        uninstall()
    return rows, per_game_rec, incidents_by_game, _SEAT_N[0]


def _bench_summary(rows):
    out = Counter(r["outcome"] for r in rows)
    vals = [r["loops_to_win"] for r in rows]
    return {"n": len(rows), "outcomes": dict(out),
            "defense": out["defense"],
            "mean": round(sum(vals) / len(vals), 3)}


# ---------------------------------------------------------------------------
# (a)(b) scope
# ---------------------------------------------------------------------------
def cmd_scope(days: int, out: str | None) -> None:
    rows, rec, occ, nseat = _run_corpus(days, gates=GATES, apply_key=None,
                                        record=True)
    summ = _bench_summary(rows)
    print(f"== B-274 (a)(b) 影計測 / {days}日級 ==")
    print(f"  ベンチ: 局数{summ['n']} 防衛{summ['defense']} 平均{summ['mean']} "
          f"結末{summ['outcomes']}")
    print(f"  set_card 総席数（分母）: {nseat}")
    flat = [r for v in rec.values() for r in v]
    print(f"  述語が True になった (席,対象) 行: {len(flat)}")
    for k in GATES:
        hits = [r for r in flat if r.get(k)]
        seats = {(r["game"], r["loop"], r["day"], r["seat"]) for r in hits}
        games = {r["game"] for r in hits}
        print(f"    gate={k:7s} 行={len(hits):4d} 席={len(seats):4d} "
              f"局={len(games):3d}/{summ['n']}")
        if hits:
            bys = Counter(r["game"].split("#")[0] for r in hits)
            print(f"              脚本別: {dict(sorted(bys.items()))}")
    # 動機の脚本（判定4）
    for k in GATES:
        hits = [r for r in flat if r.get(k) and r["game"].startswith("btx_lovers")]
        if hits:
            print(f"  ★判定4: gate={k} に btx_lovers の行 {len(hits)} "
                  f"（局={sorted({r['game'] for r in hits})}）")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"days": days, "bench": summ, "seats": nseat,
                       "rows": rows, "rec": flat}, f, ensure_ascii=False)
        print(f"  → {out}")


# ---------------------------------------------------------------------------
# (c)(e) apply
# ---------------------------------------------------------------------------
def cmd_apply(gate: str, days: int, out: str | None, _base=None) -> None:
    base_rows, base_rec, base_occ, base_n = (
        _base if _base is not None else _run_corpus(days, apply_key=None))
    app_rows, app_rec, app_occ, app_n = _run_corpus(days, apply_key=gate)
    b, a = _bench_summary(base_rows), _bench_summary(app_rows)
    _gk = gate.split("@")[0]
    print(f"== B-274 (c)(e) 再生 / gate={gate} ({GATES[_gk]}"
          f"{' ＋素案' + gate.split('@')[1] if '@' in gate else ''}) / {days}日級 ==")
    print(f"  baseline : 防衛{b['defense']} 平均{b['mean']} 結末{b['outcomes']}")
    print(f"  applied  : 防衛{a['defense']} 平均{a['mean']} 結末{a['outcomes']}")
    bm = {f"{r['script']}#{r['seed']}": r for r in base_rows}
    am = {f"{r['script']}#{r['seed']}": r for r in app_rows}
    imp, reg = [], []
    for k in bm:
        d = am[k]["loops_to_win"] - bm[k]["loops_to_win"]
        if d < 0:
            imp.append((k, bm[k]["loops_to_win"], am[k]["loops_to_win"]))
        elif d > 0:
            reg.append((k, bm[k]["loops_to_win"], am[k]["loops_to_win"]))
    print(f"  per-game flip: 改善{len(imp)} 退行{len(reg)}")
    for k, x, y in imp:
        print(f"    改善 {k}: {x} → {y}")
    for k, x, y in reg:
        print(f"    退行 {k}: {x} → {y}")
    # (e)：applied 側で述語が通った席の当日事件が不発になったか
    flat_a = [r for v in app_rec.values() for r in v if r.get(_gk)]
    fired_seats = {(r["game"], r["loop"], r["day"]) for r in flat_a}
    stopped, occurred, missing = 0, 0, 0
    for g, lp, dy in sorted(fired_seats):
        rec = app_occ.get(g, {}).get((lp, dy))
        if rec is None:
            missing += 1
        elif rec[1]:
            occurred += 1
        else:
            stopped += 1
    print(f"  (e) 述語が通った日 {len(fired_seats)}: 事件が不発 {stopped} / "
          f"発生 {occurred} / 事件記録なし {missing}")
    # baseline の同じ (game,loop,day) で事件が発生していたか（対照）
    base_fire = 0
    for g, lp, dy in sorted(fired_seats):
        rec = base_occ.get(g, {}).get((lp, dy))
        if rec and rec[1]:
            base_fire += 1
    print(f"      うち baseline で発生していた日: {base_fire}")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"gate": gate, "days": days, "base": b, "applied": a,
                       "improved": imp, "regressed": reg,
                       "e": {"days_fired": len(fired_seats), "stopped": stopped,
                             "occurred": occurred, "base_fire": base_fire}},
                      f, ensure_ascii=False)
        print(f"  → {out}")


# ---------------------------------------------------------------------------
# perm（判定3／§72-78）
# ---------------------------------------------------------------------------
def cmd_perm(gate: str, modes: list[str], days: int, out: str | None) -> None:
    res = {}
    for m in modes:
        for label, key in (("base", None), ("app", gate)):
            rows, _rec, _occ, _n = _run_corpus(days, apply_key=key, perm_mode=m)
            res[(m, label)] = _bench_summary(rows)
            s = res[(m, label)]
            print(f"  perm={m:4s} {label:4s} 防衛{s['defense']:3d} "
                  f"平均{s['mean']:.3f} {s['outcomes']}", flush=True)
    print(f"== 判定3（向きが揃うか）/ gate={gate} / {days}日級 ==")
    signs = []
    for m in modes:
        d = res[(m, "app")]["defense"] - res[(m, "base")]["defense"]
        signs.append(d)
        print(f"  perm={m:4s} 防衛差 {d:+d}")
    print(f"  → 符号: {signs} "
          f"{'（向きが揃う）' if all(s >= 0 for s in signs) else '（揃わない＝負け）'}")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"gate": gate, "days": days,
                       "res": {f"{m}|{l}": v for (m, l), v in res.items()}},
                      f, ensure_ascii=False)



# ---------------------------------------------------------------------------
# (d) センサス＝発火席の内訳（退行の機序を数字で押さえる）
# ---------------------------------------------------------------------------
def cmd_census(days: int, gate: str, out: str | None) -> None:
    rows, rec, occ, nseat = _run_corpus(days, gates=GATES, apply_key=None)
    flat = [r for v in rec.values() for r in v if r.get(gate)]
    new = [r for r in flat if not r.get("cur")]
    print(f"== B-274 (d) センサス / gate={gate} / {days}日級 ==")
    print(f"  発火行 {len(flat)}（うち現行では発火しない新規 {len(new)}）")
    print(f"  当日事件の内訳: "
          f"{dict(Counter(tuple(r['inc_today']) for r in new))}")
    print(f"  脚本別: {dict(Counter(r['game'].split('#')[0] for r in new))}")
    last_seat = [r for r in new if r["hand_cool"] and not r["other_cool"]]
    cool_disp = [r for r in new if r.get("chosen_card") == "不安-1"]
    two_front = [r for r in new if r["n_cands"] >= 2]
    print(f"  ★この席が『最後の冷却席』（手札に不安-1 ∧ 他席に無し）: {len(last_seat)}")
    print(f"  ★baseline でこの席が実際に打っていた手が 不安-1: {len(cool_disp)}")
    print(f"  ★今日の犯人候補が2人以上（二正面の代理）: {len(two_front)}")
    print(f"  chosen_card の内訳: {dict(Counter(r.get('chosen_card') for r in new))}")
    print(f"  danger の分布: {dict(Counter(round(r['danger']) for r in new))}")
    # 述語素案の反実仮想（数だけ）
    d1 = [r for r in new if not (r["hand_cool"] and not r["other_cool"])]
    d2 = [r for r in new if r["n_cands"] <= 1]
    d12 = [r for r in new if not (r["hand_cool"] and not r["other_cool"])
           and r["n_cands"] <= 1]
    print(f"  素案D1（最後の冷却席では譲る）を通す行: {len(d1)}/{len(new)}")
    print(f"  素案D2（今日の犯人候補が1人の日だけ）を通す行: {len(d2)}/{len(new)}")
    print(f"  素案D1∧D2 を通す行: {len(d12)}/{len(new)}")
    for r in new[:40]:
        print(f"    {r['game']} L{r['loop']}D{r['day']} {r['seat']} tgt={r['tgt']} "
              f"inc={r['inc_today']} cands={r['cands']} danger={r['danger']} "
              f"hand_cool={r['hand_cool']} other_cool={r['other_cool']} "
              f"chosen={r.get('chosen_card')}->{r.get('chosen_target')}")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"days": days, "gate": gate, "rows": new}, f, ensure_ascii=False)
        print(f"  → {out}")


# ---------------------------------------------------------------------------
# 判定4（§72-88）＝動機の脚本で述語が「どの段で落ちるか」を数える
# ---------------------------------------------------------------------------
#: `_b64_ml_approach_block`（`agents/heuristic_protagonist.py:4672-4714`）の条件を
#  **段ごとに**なぞった診断用の写し。★本体は1バイトも変えない＝ここは観測専用で、
#  写しが本体とズレたら診断が嘘になるので `stage=通過` の件数を本体の返り値と突き合わせる。
_STAGES = ("①mm伏せ札なし", "②対象が盤上に居ない", "③当日の事件がゲート外",
           "④ML確度が床未満", "⑤対象がML候補でない", "⑥ピボット算術が合わない",
           "通過")


def _why_stage(self, view, tgt, mm_char_now, gate: tuple[str, ...]) -> str:
    from engine.data import unrest_threshold_of
    from engine.data import forbidden_of
    if not (self._B64_ML_APPROACH and tgt in mm_char_now):
        return _STAGES[0]
    tc = self._alive(view, tgt)
    if tc is None or tc.get("area") is None:
        return _STAGES[1]
    today = view["day"]
    if not any(i.get("day") == today and i.get("name") in gate
               for i in view.get("incidents", [])):
        return _STAGES[2]
    ml_name, ml_p = self._belief.most_likely_role("ミスリーダー")
    if not ml_name or ml_p < self._B52_ML_MIN:
        return _STAGES[3]
    if tgt != ml_name and tgt not in self._b56_ml_ties(view, ml_name, ml_p):
        return _STAGES[4]
    cool = 1 if (today in getattr(self, "_cooled_days", ())
                 or self._b63_seat_can_play(view, "不安-1")) else 0
    for cn in getattr(self, "_culprit_cands", {}).get(today, ()):
        if cn == tgt:
            continue
        cc = self._alive(view, cn)
        th = unrest_threshold_of(cn)
        if not (cc and th):
            continue
        if cc["area"] == tc["area"] or cc["area"] in forbidden_of(tgt):
            continue
        supply = cc["unrest"] + (1 if cn in mm_char_now else 0) + 1
        if supply - cool == th:
            return _STAGES[6]
    return _STAGES[5]


def cmd_why(script: str, days: int, gate: str) -> None:
    """指定脚本（既定＝動機の `btx_lovers`）で、述語が落ちる段を数える。"""
    import copy
    from collections import Counter as _C
    from dataclasses import replace as _rep

    from agents import HeuristicMastermind
    from sim import run_game

    g = GATES[gate]
    tally = _C()
    calls = [0]
    _orig = HeuristicProtagonist._b64_ml_approach_block

    def _blk(self, view, tgt, mm_char_now):
        calls[0] += 1
        tally[_why_stage(self, view, tgt, mm_char_now, g)] += 1
        return _orig(self, view, tgt, mm_char_now)

    HeuristicProtagonist._b64_ml_approach_block = _blk
    try:
        games = [(n, s, sc) for n, s, sc in _corpus(days) if n == script]
        for name, seed, _sc in games:
            sc = copy.deepcopy(_sc)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(_rep(sc, loops=8), {"mastermind": mm, "p1": hp,
                                         "p2": hp, "p3": hp})
    finally:
        HeuristicProtagonist._b64_ml_approach_block = _orig
    print(f"== 判定4 / script={script} ({len(games)}局) / gate={gate} ({g}) ==")
    print(f"  述語が呼ばれた回数（＝この分岐まで到達した option 数）: {calls[0]}")
    for s in _STAGES:
        print(f"    {s}: {tally.get(s, 0)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="B-274 フェーズ0 プローブ（計測のみ）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("knobs")
    s = sub.add_parser("scope")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--out")
    s = sub.add_parser("apply")
    s.add_argument("--gate", default="board")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--out")
    s = sub.add_parser("census")
    s.add_argument("--gate", default="all", choices=sorted(GATES))
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--out")
    s = sub.add_parser("why")
    s.add_argument("--script", default="btx_lovers")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--gate", default="all", choices=sorted(GATES))
    s = sub.add_parser("perm")
    s.add_argument("--gate", default="kill")
    s.add_argument("--modes", default="id,rev,h1,h5")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--out")
    a = p.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("⚠ PYTHONHASHSEED=0 を付けてください", file=sys.stderr)
    if a.cmd == "knobs":
        print(knobs())
    elif a.cmd == "scope":
        cmd_scope(a.days, a.out)
    elif a.cmd == "apply":
        # ★ベースラインは1回だけ実測して全ゲートで共有する（同一プロセス・同一コーパス）
        _base = _run_corpus(a.days, apply_key=None)
        for _g in a.gate.split(","):
            cmd_apply(_g, a.days,
                      (a.out.replace(".json", f".{_g}.json") if a.out else None),
                      _base=_base)
    elif a.cmd == "census":
        cmd_census(a.days, a.gate, a.out)
    elif a.cmd == "why":
        cmd_why(a.script, a.days, a.gate)
    elif a.cmd == "perm":
        cmd_perm(a.gate, a.modes.split(","), a.days, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
