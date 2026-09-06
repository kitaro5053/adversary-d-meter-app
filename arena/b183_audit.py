# -*- coding: utf-8 -*-
"""B-183 Phase 1：**「pick が席に翻訳されない」の門の分解**（計測のみ・`arena/` のみ）。

発注＝FableA（2026-08-07）。文脈＝`docs/バックログ_構想メモ_FableA.md` §60-4（起票）・
§60-7（B-176 land＝基点 main `9064ee7`・`B176_TONIGHT_PROB=0.65` 既定 ON）／
`docs/監査_B176_181_セットPhase1_2026-08-07.md` §5（門の候補）／
`docs/監査_B176_Phase2_当夜×確度帯_2026-08-07.md` §3（TRACE で実物確認済みの門2件）。
土台＝`arena/b176_audit.py`（pick 不翻訳の述語はそちらの `_analyze_game` を**そのまま再利用**
＝二重実装しない）。

------------------------------------------------------------------------------
## 0. 何を測るか
------------------------------------------------------------------------------

B-183 pick 不翻訳ターン（＝いずれかの席の計画の pick が3席の誰の実手にも現れず、
その pick が覆っていた fatal 脅威も誰にも折られなかったターン。定義の正典＝
`arena/b176_audit.py` §0）のうち **fatal 実在度 ≥ 0.5** のものを全数、
**「どの門で pick が落ちたか」で排他分類**する。

★分類は label 照合ではなく**コードの分岐**で行う（発注指示）：
`agents/b100_alloc.py` の TRACE フック（`:312`・計測専用・本体が既設）と、
`live_constraints` の**読み取り専用ラッパー**（本体を呼んだ後、脱落した脅威の理由を
本体と同じ順序・同じ述語で再判定し、食い違えば「分類不能」に落とす）で、
席ごとに「その脅威が allocate のどの return で消えたか」を取る。

------------------------------------------------------------------------------
## 1. 門（＝コードの分岐）の一覧と行番号（main `9064ee7` 時点の `agents/b100_alloc.py`）
------------------------------------------------------------------------------

| 門 | 分岐の現物 | 意味 |
|---|---|---|
| `LC:非defendable` | `:182` | 折れる条件が無い＝制約にならない |
| `LC:blocked` | `:186` | `B100_LINE_CAP`/`B100_MAX_PER_LOOP`（既定はどちらも OFF） |
| `LC:placed済み` | `:190` | 自チームが既に折った扱い（＝本来 served のはず） |
| `LC:空振りフィルタ` | `:224` | **全折り手が `futile_reason` で落ちた**＝planner の pick と空振り判定の乖離（BTX#16 型） |
| `資格ゼロ` | `:452`（個別判定は `gate_reason :263`） | θ/当夜×帯/供給/鉄則のどの資格軸も通らない |
| `設計3短絡` | `:428` | この席の予定手が既に**どれかの**制約（実在度不問）を折っている＝席を使わない |
| `open_除外` | `:444` 前後の `later_keys` フィルタ | 後続席の予定手が折るはず、として外された |
| `席の上限` | `:461` | `seats_used >= cap`（B100_MAX_SEATS=2/3） |
| `NOPLAN_LAST` | `:472` | 計画なしターンは最終席でしか払わない（FS#18 型） |
| `spare_last` | `:475` | 最終席から奪わない |
| `here空` | `:498` | 資格はあるが**この席の手札から打てる折り手が無い**（strict 自傷除去を含む） |
| `n_pay切り` | `:497` の slice ／ソート `:479` | 資格はあるが優先順位の slice に入らなかった |
| `別脅威が席取り` | `:504` | 同じ席で**別の**資格つき脅威が強制された |
| `allocate不到達` | `_b100_force :794`（stash 無し）／`allocate :326`（threats 空）／例外 | TRACE が1件も出ていない席 |

------------------------------------------------------------------------------
## 2. ターンへの排他分類（★決め方を先に固定し、後から変えない）
------------------------------------------------------------------------------

1. 対象ターンの代表脅威＝missing pick が覆っていた fatal 脅威のうち**実在度最大**のもの。
2. 3席それぞれで代表脅威の「席運命（fate）」を取る（§1 の門のどれか）。
3. ターンの門＝**最も先まで進んだ席の運命**（＝pick が「打たれる」に最も近づいた地点。
   同順位なら後の席）。進み順は §1 の表の上から下（allocate の実行順そのまま）。
4. 再判定が本体の結果と食い違った席・TRACE の欠けたターンは**「分類不能」**として残す。

★観測は**読み取り専用**：TRACE コールバックと LC ラッパーは例外を内部で握り潰し、
本体の返り値を1バイトも変えない。物証＝`verify`（計測 ON と素の対局の棋譜完全一致）。
★「正解の配役」は一切参照しない（使うのは主人公AI自身の内部量と公開履歴だけ）。

------------------------------------------------------------------------------
## 3. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行）
------------------------------------------------------------------------------

    python -m arena.b183_audit verify --days 5 --games random_FS#3,random_FS#18
    python -m arena.b183_audit scan   --days 5 [--start 0 --end 35] [--json out.json]
    python -m arena.b183_audit turn   --days 5 --game random_FS#3 --loop 4 --day 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from agents import HeuristicMastermind, HeuristicProtagonist
from agents import b100_alloc
from agents.b100_mix import futile_reason
from arena.b176_audit import (_Probe as _B176Probe, _analyze_game,
                              _night_losses, _key_of)
from dataclasses import replace

from sim import run_game

#: 確度帯（B-176 land 後の切替口の実値で切る＝0.65 はコードから取る）。
_THETA = float(HeuristicProtagonist.B100_THETA)
_TONIGHT = float(HeuristicProtagonist.B176_TONIGHT_PROB or 0.65)
_EPS = 1e-9

#: 門の「進み順」（§2-3）。値が大きいほど pick が打たれるのに近い地点まで進んだ。
GATE_ORDER = [
    "分類不能",
    "allocate不到達",
    "threats不在",
    "LC:非defendable",
    "LC:blocked",
    "LC:placed済み",
    "LC:paid済み(B185)",
    "LC:空振りフィルタ",
    "資格ゼロ",
    "設計3短絡",
    "open_除外",
    "席の上限",
    "NOPLAN_LAST",
    "spare_last",
    "n_pay切り",
    "here空",
    "別脅威が席取り",
]
_RANK = {g: i for i, g in enumerate(GATE_ORDER)}


def _band(p: float) -> str:
    if p >= _THETA - _EPS:
        return "θ以上(≥0.9)"
    if p >= _TONIGHT - _EPS:
        return f"帯{_TONIGHT}-0.9"
    return f"帯0.5-{_TONIGHT}"


# ---------------------------------------------------------------------------
# 観測器：TRACE コールバック＋ live_constraints ラッパー（読み取り専用）
# ---------------------------------------------------------------------------
_ORIG_LC = b100_alloc.live_constraints


class _Probe(_B176Probe):
    """`b176_audit._Probe` に「席ごとの allocate 運命」の記録を足す。

    - `alloc_trace[(loop,day,seat)]`＝allocate の終端 `_emit` 1件（stage・cons・ret）
      ＋その瞬間の `_turn_plan` 写し（open_ 除外の再判定用）。
    - `lc_info[(loop,day,seat)]`＝live_constraints の入出力差分：
      kept（label→keys/prob/kind/due_day/n_here）／drops（label→脱落理由）／
      all_labels（stash の fatal 脅威全ラベル）。
    ★どちらも読み取りのみ。コールバック内の例外は握り潰す（`_b100_force_joint` の
    try/except に例外を渡すと behaviour が変わるため＝§2）。
    """

    def __init__(self, seed: int = 0, shadow: bool = True):
        super().__init__(seed, shadow=shadow)
        self.alloc_trace: dict = {}
        self.lc_info: dict = {}
        self._cur_seat_key = None

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision == "set_card" and self._shadow_on:
            self._cur_seat_key = (view.get("loop"), view.get("day"),
                                  view.get("seat"))
        return super().decide(view, decision, options)

    # -- TRACE コールバック（b100_alloc.TRACE に据える） --------------------
    def _on_trace(self, rec: dict) -> None:
        try:
            rec = dict(rec)
            rec["plan_rest"] = [
                (it.get("card"), it.get("target"), it.get("target_kind"))
                for it in (getattr(self, "_turn_plan", None) or [])]
            self.alloc_trace.setdefault(self._cur_seat_key, []).append(rec)
        except Exception:
            pass

    # -- live_constraints ラッパー（本体を呼んだ後、脱落理由を再判定） ------
    def _lc_wrapper(self, agent, view, options, threats, *, roles, ctx,
                    rumor_p, placed, blocked_labels=(), stats=None,
                    culprits=None, supply_max=None):
        kw = dict(roles=roles, ctx=ctx, rumor_p=rumor_p, placed=placed,
                  blocked_labels=blocked_labels, stats=stats,
                  culprits=culprits)
        if supply_max is not None:
            kw["supply_max"] = supply_max
        out = _ORIG_LC(agent, view, options, threats, **kw)
        try:
            self._lc_record(agent, view, threats, out, roles=roles, ctx=ctx,
                            rumor_p=rumor_p, placed=placed,
                            blocked_labels=blocked_labels)
        except Exception:
            pass
        return out

    def _lc_record(self, agent, view, threats, out, *, roles, ctx, rumor_p,
                   placed, blocked_labels) -> None:
        kept_ids = {id(c["threat"]): c for c in out}
        kept, drops, all_labels, futile = {}, {}, set(), {}
        for t in threats:
            if not t.fatal:
                continue                       # pick 不翻訳の対象は fatal のみ
            all_labels.add(t.label)
            c = kept_ids.get(id(t))
            if c is not None:
                ent = kept.get(t.label)
                info = {"keys": set(c["keys"]), "prob": float(t.prob),
                        "kind": t.kind, "due_day": getattr(t, "due_day", None),
                        "imminent": bool(getattr(t, "imminent", False)),
                        "n_here": len(c["here"])}
                if ent is None or info["prob"] > ent["prob"]:
                    kept[t.label] = info
                continue
            # ---- 脱落理由の再判定（本体 `live_constraints` と同じ順序・同じ述語） ----
            if not t.defendable:
                r = "LC:非defendable"                       # b100_alloc.py:182
            elif t.label in blocked_labels:
                r = "LC:blocked"                            # :186
            elif any((b.card, b.target, b.target_kind) in placed
                     for c_ in t.conditions for b in c_.breaks):
                r = "LC:placed済み"                          # :190
            elif (getattr(agent, "B185_PAID_LABELS", False)
                  and (getattr(agent, "_b100_paid", None) or {}).get(t.label)):
                r = "LC:paid済み(B185)"    # ★B-185 Phase 2（既定 OFF＝この枝は不到達）
            else:
                futile_all = True
                reasons = []
                for c_ in t.conditions:
                    for b in c_.breaks:
                        fr = futile_reason(view, agent, roles, b.card,
                                           b.target, b.target_kind, ctx,
                                           rumor_p)
                        if fr:
                            reasons.append(f"{b.card}→{b.target}: {fr}")
                        else:
                            futile_all = False
                            break
                    if not futile_all:
                        break
                if futile_all:
                    r = "LC:空振りフィルタ"                   # :224（keys 空）
                    futile.setdefault(t.label, reasons)
                else:
                    # 本体では脱落したが再判定では折り手が残る＝食い違い＝正直に残す
                    r = "分類不能"
            if t.label not in drops or r != "分類不能":
                drops[t.label] = r
        self.lc_info[self._cur_seat_key] = {
            "kept": kept, "drops": drops, "all_labels": all_labels,
            "futile": futile}


# ---------------------------------------------------------------------------
# 1局の実行（TRACE/LC ラッパーの据え付けと復元）
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int, instrument: bool = True):
    hp = _Probe(seed, shadow=instrument)
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
# 席運命（fate）＝§1 の門への対応づけ
# ---------------------------------------------------------------------------
def _seat_fate(hp: _Probe, loop, day, seat, label: str) -> dict:
    """(loop,day,seat) の席で、脅威 label が allocate のどの分岐で消えたか。"""
    key = (loop, day, seat)
    recs = hp.alloc_trace.get(key) or []
    lc = hp.lc_info.get(key) or {}
    if not recs:
        return {"gate": "allocate不到達", "detail": "TRACEなし（stash無し/threats空/例外）"}
    rec = recs[-1]
    stage = rec.get("stage") or ""
    cons = rec.get("cons") or []
    ent = next((c for c in cons if c.get("label") == label), None)
    if ent is None:
        drop = (lc.get("drops") or {}).get(label)
        if drop:
            return {"gate": drop, "detail": stage}
        if label in (lc.get("all_labels") or set()):
            return {"gate": "分類不能", "detail": f"stashに在るがLC差分に無い/{stage}"}
        return {"gate": "threats不在", "detail": "この席のstashに脅威が無い"}
    # ---- cons に居る＝gate_reason の個別判定が TRACE に載っている ----
    gate = ent.get("gate")
    kept = (lc.get("kept") or {}).get(label) or {}
    n_here = ent.get("n_here", kept.get("n_here"))
    if gate is None:
        return {"gate": "資格ゼロ",
                "detail": _noelig_detail(ent, kept),
                "n_here": n_here}
    # ---- 資格あり：allocate のどの段で消えたか ----
    if stage.startswith("設計3短絡"):
        return {"gate": "設計3短絡", "detail": str((rec.get("ret") or {}).get("matched")),
                "n_here": n_here, "why": gate}
    # open_ 除外の個別再判定（後続席の予定手キーが折り手キーに載っているか）
    later = [tuple(k) for k in (rec.get("plan_rest") or [])[1:]]
    keys = kept.get("keys") or set()
    if later and any(tuple(k) in keys for k in later):
        return {"gate": "open_除外", "detail": "後続席の予定手が折るはず",
                "n_here": n_here, "why": gate}
    if stage.startswith("後続席が全部折る"):
        return {"gate": "分類不能", "detail": "stage=open_空だが個別再判定は不一致",
                "n_here": n_here, "why": gate}
    if stage.startswith("資格ゼロ"):
        return {"gate": "分類不能", "detail": "stage=資格ゼロだが個別gateあり",
                "n_here": n_here, "why": gate}
    if stage.startswith("席の上限"):
        return {"gate": "席の上限", "detail": stage, "n_here": n_here, "why": gate}
    if stage.startswith("計画なしターンは最終席のみ"):
        return {"gate": "NOPLAN_LAST", "detail": stage, "n_here": n_here, "why": gate}
    if stage.startswith("最終席から奪わない"):
        return {"gate": "spare_last", "detail": stage, "n_here": n_here, "why": gate}
    if stage.startswith("強制した"):
        winner = (rec.get("ret") or {}).get("label")
        if winner == label:
            return {"gate": "分類不能", "detail": "強制されたのに不翻訳（矛盾）",
                    "n_here": n_here, "why": gate}
        return {"gate": "別脅威が席取り", "detail": f"勝者={winner}",
                "n_here": n_here, "why": gate}
    if stage.startswith("優先順位で上位に負けた"):
        if not n_here:
            return {"gate": "here空", "detail": "この席の手札から打てる折り手なし",
                    "n_here": 0, "why": gate}
        return {"gate": "n_pay切り", "detail": "sliceに入らず（他候補もhere空等）",
                "n_here": n_here, "why": gate}
    return {"gate": "分類不能", "detail": f"未知のstage={stage}",
            "n_here": n_here, "why": gate}


def _noelig_detail(ent: dict, kept: dict) -> str:
    """資格ゼロの内訳（gate_reason :263-303 の条件を kept のフィールドで読み解く）。"""
    p = float(ent.get("prob") or 0.0)
    kind = ent.get("kind") or kept.get("kind")
    due = kept.get("due_day")
    # today は kept を取った席の view["day"]＝lc_info のキーに含まれる day と同じ
    if p >= _TONIGHT - _EPS:
        if kind in b100_alloc.B176_SETUP_KINDS:
            return f"仕込み型除外（kind={kind}・p={p:.2f}）"
        return f"非当夜（due_day={due}≠今日・p={p:.2f}）"
    return f"閾値未満（p={p:.2f}<{_TONIGHT}・due_day={due}）"


def _classify_turn(hp: _Probe, loop, day, seats: list, label: str) -> dict:
    """§2 の排他分類＝最も先まで進んだ席の運命を採る（同順位なら後の席）。"""
    fates = []
    for s in seats:
        f = _seat_fate(hp, loop, day, s, label)
        f["seat"] = s
        fates.append(f)
    best = max(fates, key=lambda f: _RANK.get(f["gate"], 0))
    return {"gate": best["gate"], "detail": best.get("detail"),
            "seat": best["seat"], "n_here": best.get("n_here"),
            "per_seat": [{"seat": f["seat"], "gate": f["gate"],
                          "n_here": f.get("n_here"),
                          "detail": f.get("detail")} for f in fates]}


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def scan(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.corpus_census import script_signature
    from arena.b145_audit import _outcome

    c: Counter = Counter()
    layer: Counter = Counter()          # (band, gate, night_loss) -> turns
    gate_sigs: dict = {}
    gate_games: dict = {}
    g05_games, g05_sigs = set(), set()
    nl_games = set()
    rows: list = []
    seal_line: Counter = Counter()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _t = _play(sc, seed, loops)
        n += 1
        g = f"{name}#{seed}"
        sig = script_signature(sc)
        res = _analyze_game(hp, st)
        if name == "btx5_seal":
            seal_line["btx5_seal 局数"] += 1
            seal_line["btx5_seal 防衛"] += int(_outcome(st) == "defense")
        # ---- b176_audit と同じ土俵の B183 全数（差分報告用に再掲） ----
        for pm in res["pickmiss"]:
            c["B183 pick不翻訳ターン（全数）"] += 1
            fm = [x["fatal_max"] for x in pm["missing"]
                  if x["fatal_max"] is not None]
            if not fm or max(fm) < 0.5 - _EPS:
                continue
            # ---- 本命＝fatal≥0.5 の門分解 ----
            c["★B183 fatal≥0.5 ターン"] += 1
            g05_games.add(g); g05_sigs.add(sig)
            if pm["night_loss"]:
                c["  内数 その夜に敗北が成立"] += 1
                nl_games.add(g)
            c[f"  内数 確度帯[{_band(max(fm))}]"] += 1
            loop, day = pm["loop"], pm["day"]
            rec = hp.turns.get((loop, day)) or {}
            seats = [p["seat"] for p in rec.get("plays", [])]
            # 代表脅威＝missing の labels のうち実在度最大
            cand = []
            for x in pm["missing"]:
                for lb in x["labels"]:
                    tr = (rec.get("threats") or {}).get(lb)
                    if tr is not None:
                        cand.append((float(tr["prob"]), lb, x["pick"]))
            if not cand:
                cls = {"gate": "分類不能", "detail": "代表脅威なし",
                       "per_seat": []}
                p_rep, lb_rep, pick_rep = None, None, None
            else:
                cand.sort(reverse=True)
                p_rep, lb_rep, pick_rep = cand[0]
                cls = _classify_turn(hp, loop, day, seats, lb_rep)
            gate = cls["gate"]
            if gate == "LC:空振りフィルタ" and lb_rep is not None:
                for s in seats:
                    fr = (hp.lc_info.get((loop, day, s)) or {}).get(
                        "futile", {}).get(lb_rep)
                    if fr:
                        cls["futile_reasons"] = fr
                        break
            band = _band(p_rep) if p_rep is not None else "?"
            nl = bool(pm["night_loss"])
            c[f"門[{gate}]"] += 1
            layer[(band, gate, "当夜敗北" if nl else "無事")] += 1
            gate_sigs.setdefault(gate, set()).add(sig)
            gate_games.setdefault(gate, set()).add(g)
            if name == "btx5_seal":
                seal_line[f"btx5_seal B183fatal≥0.5 門[{gate}]"] += 1
            rows.append({"game": g, "sig": sig, "loop": loop, "day": day,
                         "label": lb_rep, "prob": p_rep, "pick": pick_rep,
                         "band": band, "night_loss": nl, **cls,
                         "plays": pm["plays"],
                         "n_missing_fatal05": sum(1 for x in pm["missing"]
                                                  if (x["fatal_max"] or 0)
                                                  >= 0.5 - _EPS)})
    return {"days": days, "n_games": n,
            "θ": _THETA, "B176_TONIGHT_PROB": _TONIGHT,
            "fatal≥0.5 局数": len(g05_games),
            "fatal≥0.5 独立脚本数": len(g05_sigs),
            "fatal≥0.5 当夜敗北の局数": len(nl_games),
            "counts": dict(c),
            "層別(band×門×当夜敗北)": {f"{b}|{g2}|{nl}": v
                                     for (b, g2, nl), v in sorted(layer.items())},
            "門ごとの局数": {g2: len(v) for g2, v in gate_games.items()},
            "門ごとの独立脚本数": {g2: len(v) for g2, v in gate_sigs.items()},
            "btx5_seal 単独行": dict(seal_line),
            "rows": rows}


# ---------------------------------------------------------------------------
# verify＝観測器（TRACE＋LCラッパー）が対局を変えていない物証
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None, only: tuple = ()) -> dict:
    from arena.benchmark import benchmark_scripts

    bad = []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        if only and f"{name}#{seed}" not in set(only):
            continue
        _a, _s, t1 = _play(sc, seed, loops, instrument=True)
        _b, _s2, t2 = _play(sc, seed, loops, instrument=False)
        n += 1
        if t1 != t2:
            bad.append(f"{name}#{seed}")
    return {"days": days, "n_games": n, "mismatch": bad}


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
    seats = [p["seat"] for p in rec["plays"]]
    fatal = {lb: {"prob": tr["prob"], "kind": tr["kind"],
                  "covered": tr["covered"], "pick": tr["pick"]}
             for lb, tr in sorted(rec["threats"].items(),
                                  key=lambda kv: -kv[1]["prob"])}
    out = {"game": game, "loop": loop, "day": day,
           "outcome": _outcome(st),
           "night_loss": (loop, day) in _night_losses(st),
           "plays": [f"{p['seat']}:{p['key'][0]}→{p['key'][1]}"
                     for p in rec["plays"]],
           "threats(fatal)": fatal,
           "picks(全席の和集合)": [
               {"pick": f"{k[0]}→{k[1]}", "fatal_max": e["fatal_max"],
                "labels": sorted(e["labels"])}
               for k, e in rec["picks"].items()],
           "per_seat_trace": {}}
    for s in seats:
        key = (loop, day, s)
        recs = hp.alloc_trace.get(key) or []
        lc = hp.lc_info.get(key) or {}
        out["per_seat_trace"][s] = {
            "stage": (recs[-1].get("stage") if recs else None),
            "ret": (recs[-1].get("ret") if recs else None),
            "cons": [{"label": cc.get("label"), "prob": cc.get("prob"),
                      "gate": cc.get("gate"), "n_here": cc.get("n_here")}
                     for cc in ((recs[-1].get("cons") or []) if recs else [])],
            "lc_drops": lc.get("drops"),
        }
    for lb in fatal:
        if fatal[lb]["covered"]:
            out.setdefault("fate_by_label", {})[lb] = _classify_turn(
                hp, loop, day, seats, lb)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="B-183 Phase 1 門の分解")
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
    # ★同一コミット上の対照＝B-176 の資格軸④を OFF（land 前相当）にして測る。
    ap.add_argument("--b176-off", action="store_true")
    a = ap.parse_args()
    if a.b176_off:
        HeuristicProtagonist.B176_TONIGHT_PROB = None
        print("# ★対照: B176_TONIGHT_PROB=None（land 前相当）", flush=True)
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end,
                     only=tuple(x for x in a.games.split(",") if x))
    elif a.cmd == "scan":
        res = scan(a.days, a.loops, a.start, a.end)
    else:
        res = turn(a.days, a.game, a.loop, a.day, a.loops)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    out = {k: v for k, v in res.items() if k != "rows"}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
