# -*- coding: utf-8 -*-
"""B-176＋B-181 セットレーン Phase 1：**計測のみ**（`agents/` `engine/` `rules/` `sim/` 非接触）。

発注＝FableA（2026-08-07）。文脈＝`docs/バックログ_構想メモ_FableA.md`
§52（B-176 確実な負け筋優先）・§57（B-181 犯人候補でない相手への不安-1）・
§58（ユーザー裁定＝検出＋配分をセットで実装しセットで測る）・§60-3/§60-4（追加根拠と B-183）。
先行計測＝`arena/b180_audit.py`（DEAD 席）／`arena/rev174_audit.py`（席スナップショット）。

------------------------------------------------------------------------------
## 0. 用語（★略語を使う前に、変数が何を指すかを定義する）
------------------------------------------------------------------------------

| 語 | 何を指すか（現物の場所） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回。1日3席 |
| **ターン** | (loop, day) の1日＝3席の束 |
| **脅威（Threat）** | 主人公自身の防御プランナー `agents/defense_plan.py` が席ごとに列挙する負け筋。`prob`＝belief 上の実在確率（★世界の真値ではない＝運用doc §3-7 の範囲内の量） |
| **pick 済み** | その席の計画 `plan.covered` にその脅威が載っている（＝planner が折り手を採用した）こと。折り手の現物＝`defense_plan._pick_for` |
| **当夜性（tonight）** | その脅威が**今夜（このターンの内に）発火しうる**こと。判定＝`timing=="ターン終了フェイズ"`／`timing=="事件フェイズ"` かつ当日の公開事件名が label に含まれる／`timing=="ループ終了フェイズ"` かつ当日が最終日。★board_defeat の「暗躍≥1＝今夜1枚で不可逆化」は**感度層**として別掲（本体には入れない） |
| **帯（band）** | 実在度 0.5 ≤ p < θ(=0.9)−1e-9。θ の現物＝`agents/heuristic_protagonist.py:558 B100_THETA`・ゲート判定＝`agents/b100_alloc.py:262 gate_reason` |
| **B-176 席不足でない未処置（miss）** | 帯・fatal・当夜・折り手あり（defendable かつ非 breached）・pick 済みの脅威が立ったのに、その日の3席の**誰もその脅威の折り手を打たず**、かつ**少なくとも1席が「より劣後する行き先」**（＝同等以上の確度の当夜 fatal を折っていない手）へ行ったターン |
| **DEAD 席** | `arena/b180_audit._Probe` の述語そのまま＝対象が残る事件のどの犯人候補にも居ない `不安-1`（B-181 の射程） |
| **セット噛み合い** | 同一ターンに DEAD 席（浮く札）と miss 脅威（受け皿）が共存すること |
| **B-183 pick 不翻訳** | その日の**いずれかの席**の計画に載っていた pick が、3席の誰の実手にも現れず、かつその pick が覆っていた fatal 脅威が実手の誰にも折られなかったこと（脅威間の順位でなく**同一脅威内**の翻訳の問題＝§60-4）。★最終席でなく全席の和集合を使う＝席ごとに手札が違い、最終席では折り手が立たないことがある（実測 `random_BTX#2` L7D4＝最終席 p1 の picks は空） |

★**本監査は「正解の配役」を一切参照しない**（運用doc §3-7）。使うのは `protagonist_view`・
公開履歴・主人公AI自身の内部量（belief・脅威リスト・計画）だけ。

------------------------------------------------------------------------------
## 1. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行）
------------------------------------------------------------------------------

    python -m arena.b176_audit verify --days 3 --games random_BTX#2,random_FS#3,...
    python -m arena.b176_audit scan   --days 3 [--json out.json]
    python -m arena.b176_audit scan   --days 5 [--json out.json]
    python -m arena.b176_audit turn   --days 5 --game random_BTX#2 --loop 7 --day 4
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.defense_plan import _pick_for
from arena.b180_audit import _Probe as _DeadProbe
from arena.b180_audit import _cool_target
from sim import run_game

#: θ・ε は実装側と同じ値を参照する（二重定義しない）。
_THETA = float(HeuristicProtagonist.B100_THETA)
_EPS = 1e-9
_BAND_LO = 0.5

#: 事件フェイズ脅威の当夜判定に使う（label ⊇ 事件名。`defense_plan` の label 生成規約）。
_INCIDENT_NAMES = ("殺人事件", "自殺", "行方不明", "蝶の羽ばたき", "不安拡大",
                   "邪気の汚染", "病院の事件", "遠隔殺人", "流布")


def _key_of(card, target, kind) -> tuple:
    return (card, target, kind)


def _tonight(threat, view: dict) -> bool:
    """★当夜性の本体判定（感度層 board_defeat 暗躍≥1 は含めない）。"""
    t = threat.timing
    if t == "ターン終了フェイズ":
        return True
    day = view.get("day")
    if t == "事件フェイズ":
        todays = [i.get("name") for i in view.get("incidents", ())
                  if i.get("day") == day]
        return any(nm and nm in threat.label for nm in todays)
    if t == "ループ終了フェイズ":
        return day == view.get("days_per_loop")
    return False


def _board_imminent(threat, view: dict) -> bool:
    """感度層＝board_defeat で対象板の暗躍が既に臨界-1（今夜1枚で不可逆化）。"""
    if threat.kind != "board_defeat":
        return False
    ba = view.get("board_anyaku") or {}
    for area, v in ba.items():
        if area in threat.label and v >= 1:
            return True
    return False


class _Probe(_DeadProbe):
    """`b180_audit._Probe`（DEAD 席の数え上げ）に、席ごとの脅威/計画スナップショットを足す。

    ★追加はすべて**読み取り**（`self._b100_plan` は本体が既に作っている＝計算を増やさない）。
    挙動不変の物証は `verify`（素の対局との棋譜完全一致）。
    """

    def __init__(self, seed: int = 0, shadow: bool = True):
        super().__init__(seed, shadow=shadow)
        self.turns: dict = {}          # (loop, day) -> ターン記録
        self.n_stashless = 0           # スタッシュ（_b100_plan）が無かった席数

    def _turn_rec(self, view: dict) -> dict:
        k = (view.get("loop"), view.get("day"))
        rec = self.turns.get(k)
        if rec is None:
            rec = {"plays": [], "threats": {}, "picks": {},
                   "dead_opts": set()}
            self.turns[k] = rec
        return rec

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        n_rows0 = len(self.rows)             # ★DEAD 検出の前後差分で「この席が DEAD か」を知る
        b100_0 = getattr(self, "_b100_seats", 0)
        chosen = super().decide(view, decision, options)
        if decision != "set_card" or not self._shadow_on:
            return chosen
        rec = self._turn_rec(view)
        play_key = _key_of(chosen.get("card"), chosen.get("target"),
                           chosen.get("target_kind"))
        is_dead = len(self.rows) > n_rows0
        dead_clean = bool(self.rows[-1].get("clean")) if is_dead else False
        play = {"seat": view.get("seat"), "key": play_key,
                "b100": getattr(self, "_b100_seats", 0) > b100_0,
                "dead": is_dead, "dead_clean": dead_clean,
                "broke": []}                 # [(label, prob, tonight, fatal)]
        if is_dead:
            # セット噛み合いの強い判定用＝DEAD 席の options（浮いた席が受け皿の折り手を
            # 打てたか）。DEAD 席のときだけ記録（メモリ節約）。
            rec["dead_opts"] |= {_key_of(o.get("card"), o.get("target"),
                                         o.get("target_kind")) for o in options}
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            self.n_stashless += 1
            rec["plays"].append(play)
            return chosen
        threats, plan = stash
        for t in threats:
            if not t.fatal:
                continue
            breaks = {_key_of(b.card, b.target, b.target_kind)
                      for c in t.conditions for b in c.breaks}
            covered = id(t) in plan.covered
            pk = _pick_for(plan, t) if covered else None
            tonight = _tonight(t, view)
            tr = rec["threats"].get(t.label)
            if tr is None:
                tr = {"kind": t.kind, "prob": float(t.prob),
                      "tonight": tonight, "defendable": bool(t.defendable),
                      "breached": bool(t.breached),
                      "covered": covered, "breaks": set(),
                      "board_imminent": _board_imminent(t, view),
                      "pick": None}
                rec["threats"][t.label] = tr
            tr["prob"] = max(tr["prob"], float(t.prob))
            tr["tonight"] = tr["tonight"] or tonight
            tr["defendable"] = tr["defendable"] or bool(t.defendable)
            tr["breached"] = tr["breached"] or bool(t.breached)
            tr["covered"] = tr["covered"] or covered
            tr["breaks"] |= breaks
            tr["board_imminent"] = tr["board_imminent"] or _board_imminent(t, view)
            if pk is not None and tr["pick"] is None:
                tr["pick"] = f"{pk.card}→{pk.target}"
            if play_key in breaks:
                play["broke"].append((t.label, float(t.prob), tonight, True))
        # ---- B-183 用＝この席の計画の picks（★全席の和集合＝毎席マージ） ----
        for b in plan.picks:
            bkey = _key_of(b.card, b.target, b.target_kind)
            ent = rec["picks"].setdefault(bkey, {"fatal_max": None,
                                                 "labels": set()})
            for t in threats:
                if not (t.fatal and id(t) in plan.covered):
                    continue
                pf = _pick_for(plan, t)
                if pf is None or _key_of(pf.card, pf.target,
                                         pf.target_kind) != bkey:
                    continue
                ent["labels"].add(t.label)
                if ent["fatal_max"] is None or t.prob > ent["fatal_max"]:
                    ent["fatal_max"] = float(t.prob)
        rec["plays"].append(play)
        return chosen


# ---------------------------------------------------------------------------
# 1局の実行と後処理
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int, shadow: bool = True):
    hp = _Probe(seed, shadow=shadow)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"),
              e.get("target"), e.get("to")) for e in st.history]
    return hp, st, trace


def _night_losses(st) -> set:
    """公開履歴から「その夜にループが敗北で閉じた (loop, day)」を集める。

    - `loop_end`（公開）＝ループが即時終了（主人公の死亡／KP死亡等）＝その夜の敗北。
    - `loop_result` result=主人公の敗北＝最終日夜のループ終了効果（板暗躍≥2等）も拾う。
    """
    out = set()
    for e in st.history:
        if e.get("event") == "loop_end":
            out.add((e.get("loop"), e.get("day")))
        elif (e.get("event") == "loop_result"
              and e.get("result") == "主人公の敗北"):
            out.add((e.get("loop"), e.get("day")))
    return out


def _band(p: float) -> str:
    if p >= _THETA - _EPS:
        return "θ以上"
    if p >= _BAND_LO - _EPS:
        return "帯0.5-0.9"
    if p >= 0.3 - _EPS:
        return "0.3-0.5"
    return "0.3未満"


def _classify_play(play: dict, ref_prob: float) -> str:
    """miss 脅威（確度 ref_prob）に対する、この席の行き先の分類。"""
    if play["b100"]:
        return "B100強制"
    best_tonight = max((p for (_lb, p, tn, _ft) in play["broke"] if tn),
                      default=None)
    if best_tonight is not None and best_tonight >= ref_prob - _EPS:
        return "同等以上の当夜fatalの折り手"          # ＝劣後ではない（席不足の正当な取捨）
    if best_tonight is not None:
        return "より低確度の当夜fatalの折り手"
    if play["broke"]:
        return "非当夜（より遠い）脅威の折り手"
    if play["dead"]:
        return "DEAD冷却（B-181）"
    card = play["key"][0] or ""
    if card == "不安-1":
        return "通常採点の冷却（候補冷却等）"
    if card.startswith("友好"):
        return "投資（友好）"
    return "その他の通常採点"


def _analyze_game(hp: _Probe, st) -> dict:
    """1局のターン束から miss／pick 不翻訳／セット噛み合いを抽出する。"""
    losses = _night_losses(st)
    miss_turns, ctl = [], Counter()
    pickmiss_turns = []
    mesh_turns = []
    dead_turns = []
    for (loop, day), rec in sorted(hp.turns.items(),
                                   key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
        play_keys = {p["key"] for p in rec["plays"]}
        night_loss = (loop, day) in losses
        # ---- DEAD ターン（B-181）×「同ターンの unserved 当夜 fatal（任意確度）」＝緩い噛み合い ----
        dead_plays = [p for p in rec["plays"] if p["dead"]]
        if dead_plays:
            uns = [{"label": lb, "prob": round(tr["prob"], 3),
                    "band": _band(tr["prob"]),
                    "could_break": bool(tr["breaks"] & rec["dead_opts"])}
                   for lb, tr in rec["threats"].items()
                   if tr["tonight"] and tr["defendable"] and not tr["breached"]
                   and tr["covered"] and not (tr["breaks"] & play_keys)]
            dead_turns.append({"loop": loop, "day": day,
                               "clean": any(p["dead_clean"] for p in dead_plays),
                               "night_loss": night_loss,
                               "unserved_tonight": uns})
        # ---- 帯脅威の全数（served/unserved の対照つき） ----
        for label, tr in rec["threats"].items():
            if not (tr["defendable"] and not tr["breached"] and tr["covered"]):
                continue
            served = bool(tr["breaks"] & play_keys)
            bd = _band(tr["prob"])
            if tr["tonight"]:
                ctl[f"当夜fatal[{bd}] {'served' if served else 'unserved'}"] += 1
            elif tr["board_imminent"] and bd == "帯0.5-0.9":
                # ★感度層＝board_defeat・対象板の暗躍≥1（今夜1枚で不可逆化）・非最終日
                ctl[f"感度層board_imminent[帯] {'served' if served else 'unserved'}"] += 1
            if not (tr["tonight"] and bd == "帯0.5-0.9"):
                continue
            if served:
                continue
            dests = Counter(_classify_play(p, tr["prob"]) for p in rec["plays"])
            inferior = any(c != "同等以上の当夜fatalの折り手" and c != "B100強制"
                           for c in dests)
            mesh_opt = bool(tr["breaks"] & rec["dead_opts"])
            miss_turns.append({
                "loop": loop, "day": day, "label": label, "kind": tr["kind"],
                "prob": round(tr["prob"], 3), "pick": tr["pick"],
                "night_loss": night_loss, "inferior_dest": inferior,
                "dests": dict(dests),
                "plays": [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                          for p in rec["plays"]],
                "dead_in_turn": len(dead_plays),
                "dead_clean_in_turn": sum(p["dead_clean"] for p in dead_plays),
                "mesh_dead_could_break": mesh_opt,
            })
            if dead_plays:
                mesh_turns.append({"loop": loop, "day": day, "label": label,
                                   "prob": round(tr["prob"], 3),
                                   "clean": any(p["dead_clean"]
                                                for p in dead_plays),
                                   "could_break": mesh_opt})
        # ---- B-183：pick（全席の和集合）が誰の実手にも現れず、覆っていた fatal 脅威も
        #      実手の誰にも折られなかったターン ----
        served_labels = {lb for lb, tr in rec["threats"].items()
                         if tr["breaks"] & play_keys}
        missing = []
        for k, ent in rec["picks"].items():
            if k in play_keys:
                continue
            if ent["labels"] and ent["labels"] <= served_labels:
                continue      # 覆っていた脅威は別の実手が折った＝翻訳問題ではない
            missing.append({"pick": f"{k[0]}→{k[1]}",
                            "fatal_max": (round(ent["fatal_max"], 3)
                                          if ent["fatal_max"] is not None else None),
                            "labels": sorted(ent["labels"])})
        if missing:
            pickmiss_turns.append({
                "loop": loop, "day": day, "night_loss": night_loss,
                "missing": missing,
                "plays": [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                          for p in rec["plays"]]})
    return {"miss": miss_turns, "control": dict(ctl),
            "pickmiss": pickmiss_turns, "mesh": mesh_turns,
            "dead_turns": dead_turns,
            "n_turns": len(hp.turns), "n_stashless": hp.n_stashless}


# ---------------------------------------------------------------------------
# verify＝観測器が対局を変えていない物証（素の主人公AIと棋譜完全一致）
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None, only: tuple = ()) -> dict:
    from arena.benchmark import benchmark_scripts

    bad = []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        if only and f"{name}#{seed}" not in set(only):
            continue
        _a, _s, t1 = _play(sc, seed, loops, shadow=True)
        _b, _s2, t2 = _play(sc, seed, loops, shadow=False)
        n += 1
        if t1 != t2:
            bad.append(f"{name}#{seed}")
    return {"days": days, "n_games": n, "mismatch": bad}


# ---------------------------------------------------------------------------
# scan＝本体（両ベンチ全数）
# ---------------------------------------------------------------------------
def scan(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.corpus_census import script_signature
    from arena.b145_audit import _outcome

    c: Counter = Counter()
    control: Counter = Counter()
    rows: list = []
    miss_games, miss_sigs = set(), set()
    inf_games, inf_sigs = set(), set()
    pm_games, pm_sigs = set(), set()
    mesh_games, mesh_sigs = set(), set()
    dead_recheck: Counter = Counter()
    seal_line: Counter = Counter()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _t = _play(sc, seed, loops)
        n += 1
        g = f"{name}#{seed}"
        sig = script_signature(sc)
        res = _analyze_game(hp, st)
        # ---- B-181 再現（b180 の counter をそのまま合算） ----
        dead_recheck.update(hp.c)
        if name == "btx5_seal":
            seal_line["btx5_seal 局数"] += 1
            seal_line["btx5_seal 防衛"] += int(_outcome(st) == "defense")
            seal_line["btx5_seal DEAD席"] += sum(
                v for k, v in hp.c.items() if k.startswith("★DEAD 対象"))
            seal_line["btx5_seal miss"] += len(res["miss"])
        c["ターン総数"] += res["n_turns"]
        c["スタッシュ無し席"] += res["n_stashless"]
        control.update(res["control"])
        for m in res["miss"]:
            c["★B-176 unserved（帯・当夜・fatal・pick済みが誰にも折られず）"] += 1
            if m["inferior_dest"]:
                c["★★B-176 miss（うち劣後先の席あり＝本体）"] += 1
            if m["night_loss"]:
                c["  内数 (a) その夜に敗北が成立"] += 1
            else:
                c["  内数 (b) その夜は無事"] += 1
            if m["dead_in_turn"]:
                c["★セット噛み合い（同一ターンに DEAD 席が共存）"] += 1
                if m["mesh_dead_could_break"]:
                    c["  内数 DEAD 席の options に折り手が実在"] += 1
                if m["dead_clean_in_turn"]:
                    c["  内数 DEAD-CLEAN と共存"] += 1
            rows.append({"game": g, "sig": sig, **m})
        if res["miss"]:
            miss_games.add(g); miss_sigs.add(sig)
            if any(m["inferior_dest"] for m in res["miss"]):
                inf_games.add(g); inf_sigs.add(sig)
        if res["mesh"]:
            mesh_games.add(g); mesh_sigs.add(sig)
        for dt in res["dead_turns"]:
            c["★B-181 DEADターン（DEAD 席を含むターン）"] += 1
            if dt["unserved_tonight"]:
                c["  内数 緩い噛み合い＝同ターンに unserved 当夜fatal（任意確度）あり"] += 1
                if any(u["could_break"] for u in dt["unserved_tonight"]):
                    c["    内数 DEAD 席の options に折り手が実在"] += 1
                if dt["clean"]:
                    c["    内数 DEAD-CLEAN"] += 1
            rows.append({"game": g, "sig": sig, "DEAD_TURN": True, **dt})
        for pm in res["pickmiss"]:
            c["★B-183 pick不翻訳ターン"] += 1
            if pm["night_loss"]:
                c["  内数 その夜に敗北が成立"] += 1
            fm = [x["fatal_max"] for x in pm["missing"] if x["fatal_max"] is not None]
            if fm:
                c[f"  内数 fatal確度帯[{_band(max(fm))}]"] += 1
            else:
                c["  内数 fatal被覆なしpick"] += 1
            rows.append({"game": g, "sig": sig, "B183": True, **pm})
        if res["pickmiss"]:
            pm_games.add(g); pm_sigs.add(sig)
    return {"days": days, "n_games": n,
            "θ": _THETA, "帯": f"[{_BAND_LO}, {_THETA})",
            "counts": dict(c),
            "当夜fatal対照(served/unserved×帯)": dict(control),
            "miss 局数": len(miss_games), "miss 独立脚本数": len(miss_sigs),
            "miss(劣後先あり) 局数": len(inf_games),
            "miss(劣後先あり) 独立脚本数": len(inf_sigs),
            "セット噛み合い 局数": len(mesh_games),
            "セット噛み合い 独立脚本数": len(mesh_sigs),
            "B183 局数": len(pm_games), "B183 独立脚本数": len(pm_sigs),
            "B181 DEAD 再現(b180のcounterを合算)": {
                k: v for k, v in dead_recheck.items()
                if "DEAD" in k or "COOL" in k or "席（" in k},
            "btx5_seal 単独行": dict(seal_line),
            "rows": rows}


# ---------------------------------------------------------------------------
# turn＝1ターンの深掘り（doc の物証用）
# ---------------------------------------------------------------------------
def turn(days: int, game: str, loop: int, day: int, loops: int = 8) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.b145_audit import _outcome

    scripts = {f"{n}#{s}": (n, s, sc)
               for n, s, sc in benchmark_scripts(days=days)}
    _nm, seed, sc = scripts[game]
    hp, st, _t = _play(sc, seed, loops)
    rec = hp.turns.get((loop, day))
    if rec is None:
        return {"game": game, "loop": loop, "day": day, "error": "ターン記録なし"}
    return {"game": game, "loop": loop, "day": day,
            "outcome": _outcome(st),
            "night_loss": (loop, day) in _night_losses(st),
            "plays": [{"seat": p["seat"],
                       "play": f"{p['key'][0]}→{p['key'][1]}",
                       "b100": p["b100"], "dead": p["dead"],
                       "broke": [(lb, round(pr, 3), tn)
                                 for (lb, pr, tn, _f) in p["broke"]]}
                      for p in rec["plays"]],
            "threats(fatal)": {
                lb: {k: (sorted(f"{a}→{b}" for (a, b, _c) in v)
                         if k == "breaks" else v)
                     for k, v in tr.items()}
                for lb, tr in sorted(rec["threats"].items(),
                                     key=lambda kv: -kv[1]["prob"])},
            "picks(全席の和集合)": [
                {"pick": f"{k[0]}→{k[1]}",
                 "fatal_max": ent["fatal_max"],
                 "labels": sorted(ent["labels"])}
                for k, ent in rec["picks"].items()]}


def main() -> None:
    ap = argparse.ArgumentParser(description="B-176+B-181 セット Phase 1 計測")
    ap.add_argument("cmd", choices=["verify", "scan", "turn"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--games", default="")
    ap.add_argument("--game", default="")
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--json", default=None)
    # ★B-176 Phase 2：切替口 ON の対局で unserved を数え直すため（既定 None＝Phase 1 と同じ）。
    ap.add_argument("--tonight-prob", type=float, default=None)
    ap.add_argument("--imminent", action="store_true")
    ap.add_argument("--line-cap", type=int, default=None)
    a = ap.parse_args()
    if a.tonight_prob is not None or a.imminent or a.line_cap is not None:
        from agents import HeuristicProtagonist
        HeuristicProtagonist.B176_TONIGHT_PROB = a.tonight_prob
        HeuristicProtagonist.B176_BOARD_IMMINENT = bool(a.imminent)
        if a.line_cap is not None:
            HeuristicProtagonist.B100_LINE_CAP = a.line_cap
        print(f"# ★切替口 ON: B176_TONIGHT_PROB={a.tonight_prob} "
              f"B176_BOARD_IMMINENT={bool(a.imminent)} "
              f"B100_LINE_CAP={HeuristicProtagonist.B100_LINE_CAP}", flush=True)
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end,
                     only=tuple(x for x in a.games.split(",") if x))
    elif a.cmd == "scan":
        res = scan(a.days, a.loops, a.start, a.end)
    else:
        res = turn(a.days, a.game, a.loop, a.day, a.loops)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    out = {k: v for k, v in res.items() if k != "rows"}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
