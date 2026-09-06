# -*- coding: utf-8 -*-
"""B-162：**belief がクロマクを名指しする速度**（★計測のみ・`agents/` 非接触）。

## 発端

`docs/監査_B160_止まらない供給に事件フェイズ_2026-08-04.md` §3（層別）＝
真の敗北板へ暗躍禁止を置いたのに止まらない供給だけで既に2へ届いていた
**丸損 145枚**の内訳は **層A（真に止まらない）107枚（74%）**／層B（事件フェイズ由来）38枚。
★**層Aの供給源はほぼ全部クロマク**（3日級 37/37・5日級 23/23）。
∴ 問いは「**クロマクをもっと早く名指しできるか**」。

## KB（★可否＝一意に決まる量）

- クロマクの暗躍は**脚本家能力フェイズ**発動＝`rules/40_first_steps.md:86,88`／
  `rules/50_basic_tragedy_x.md:113-116`。**暗躍禁止は行動解決フェイズでのみ有効**
  （`rules/10_action_cards.md:65`／`rules/60_faq_rulings.md:105-106`）＝**止まらない**。
- クロマクが置ける先は「**同一エリアのキャラ1人 or 自分のいるボード**」だけ
  （`rules/40:86`／`rules/50:115`）＝**位置に強く結合したチャネル**。
  ∴ フレンド（B-147＝痕跡が原理的に出ない）とは**構造が違う**。
- ★**フェイズ順**＝`rules/00_rules_core.md:103`（3 主人公行動）→`:104`（4 行動解決・移動が先）
  →`:109`（5 脚本家能力＝クロマクの暗躍）。∴ **主人公が札を伏せる時点で見える位置は
  「移動前」**であり、クロマクは**そのあと移動してから供給できる**。

## 何を測るか

1. `curve`＝**ループごとの P(真のクロマク) の推移**（★独立脚本数つき）。
   下流のしきい値は `_SUSPECT_P=0.15`（`agents/defense_plan.py:48`）＝
   `_threat_board_defeat:1241` の `cultists_here` の足切り。
2. `seats`＝**丸損席との対応**＝丸損が起きた席で、その時点の P(真のクロマク) はいくつか。
   ★**P が既に高い（0.15以上）かつ真のクロマクがその板に居た**なら
   `cultists_here` は非空になるはずで、それでも暗躍禁止が置かれていた＝**判断の問題**。
   **P が低い**なら**推理の問題**。この切り分けが本チケットの核心。
3. `seats` は同時に **B-147 の作法（観測側の対称性）** も出す＝
   真のクロマクと**役職周辺確率ベクトルがまるごと一致する生存者**が居れば、
   **その観測集合ではどんな推論器でも区別できない**。
4. `kb`＝KB からクロマクに結合した公開チャネルの完全列挙（B-151 の C1〜C11 と同じ作法）。

## 二重実装をしない

- 対局・丸損の層別・供給の相分解・真の敗北板・敗北ループは **`arena.b160_audit` /
  `arena.b146_probe` / `arena.b149_audit` / `arena.b157_audit` / `arena.b145_audit`
  から import**（1つも書き直していない）。
- ループ別の較正曲線は **`arena.calibration` の観測プローブをそのまま使う**。
- 本器が足すのは「belief の分布をどう読むか」と「丸損席へどう結合するか」だけ。

## 因果の限界（★先に書く）

1. **ex-post の観測**＝1手変えれば以降の全系列が変わる＝反実仮想は取れない
   （B-157／B-159／B-160 と同じ）。
2. 独立脚本は **3日級11本／5日級5本**しかない（seed 複製）＝件数を独立標本数として読まない。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b162_audit verify --days 3 --end 8
    python -m arena.b162_audit curve  --days 3
    python -m arena.b162_audit seats  --days 3
    python -m arena.b162_audit kb
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
#: 下流のしきい値の単一ソース＝`agents/defense_plan.py:48-49`。
from agents.defense_plan import _LIKELY_P, _SUSPECT_P
from arena.b145_audit import _outcome, _snap_index
from arena.b146_probe import _true_boards
from arena.b149_audit import _MMProbe
from arena.b157_audit import _lost_loops_exact
from arena.b160_audit import (_SeatProbe, _board_supply, _incident_name_by_day,
                              _kinshi_seats, _loop_start_boards,
                              _mm_ability_anyaku)
from arena.calibration import _CalibratedProtagonist
from sim import run_game

_AREAS = ("病院", "神社", "都市", "学校")
#: 同値（＝観測がその2人を区別していない）とみなす確率差（B-147 §3-2 と同じ）。
_EPS = 1e-5


def _true_kuromaku(script) -> list:
    """脚本の真のクロマク（`script.roles`＝非公開の割り当て。**計測器の中だけ**で使う）。"""
    return sorted(n for n, r in (script.roles or {}).items() if r == "クロマク")


# ---------------------------------------------------------------------------
# Phase 1-1：ループ別 P(真のクロマク) の推移
# ---------------------------------------------------------------------------
def curve_game(script, seed: int, loops: int = 8) -> list:
    """`arena.calibration._CalibratedProtagonist` をそのまま使う（★二重実装なし）。"""
    hp = _CalibratedProtagonist(seed)
    run_game(replace(script, loops=loops),
             {"mastermind": HeuristicMastermind(seed), "p1": hp, "p2": hp, "p3": hp})
    kuro = _true_kuromaku(script)
    if not kuro:
        return []
    rows = []
    for rec in hp.calib:
        marg = rec["marginals"]
        ps = [float((marg.get(n) or {}).get("クロマク", 0.0)) for n in kuro]
        # 席内の最大値（＝belief が「最もクロマクらしい」と見ていた確率）
        top = max((float((d or {}).get("クロマク", 0.0)) for d in marg.values()),
                  default=0.0)
        rows.append({"loop": rec["loop"], "day": rec["day"],
                     "p_true": max(ps), "p_top": top,
                     "top_is_true": bool(max(ps) >= top - _EPS and top > 0.0)})
    return rows


def curve(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    per_loop: dict = {}
    scripts_with: set = set()
    scripts_all: set = set()
    games_with = 0
    games_all = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts_all.add(name)
        games_all += 1
        rows = curve_game(sc, seed, loops=loops)
        if not rows:
            continue
        games_with += 1
        scripts_with.add(name)
        for r in rows:
            d = per_loop.setdefault(r["loop"], {"p": [], "sus": 0, "likely": 0,
                                                "top": 0, "n_games": set()})
            d["p"].append(r["p_true"])
            d["sus"] += int(r["p_true"] >= _SUSPECT_P)
            d["likely"] += int(r["p_true"] >= _LIKELY_P)
            d["top"] += int(r["top_is_true"])
            d["n_games"].add((name, seed))
    out = {}
    for lp, d in sorted(per_loop.items()):
        n = len(d["p"])
        out[lp] = {"n_seats": n, "n_games": len(d["n_games"]),
                   "mean_p": round(sum(d["p"]) / n, 4),
                   "ge_suspect": d["sus"], "ge_suspect_rate": round(d["sus"] / n, 4),
                   "ge_likely": d["likely"], "ge_likely_rate": round(d["likely"] / n, 4),
                   "top_is_true": d["top"], "top_rate": round(d["top"] / n, 4)}
    return {"days": days, "n_games_all": games_all, "n_games_kuromaku": games_with,
            "n_scripts_all": len(scripts_all), "n_scripts_kuromaku": len(scripts_with),
            "scripts_kuromaku": sorted(scripts_with), "by_loop": out}


# ---------------------------------------------------------------------------
# Phase 1-2 / Phase 2：丸損席との対応 ＋ 観測側の対称性
# ---------------------------------------------------------------------------
class _KProbe(_SeatProbe):
    """`_SeatProbe`（B-160）に **belief の役職周辺分布と盤面** を1行足すだけのプローブ。

    ★挙動不変＝`_SeatProbe.decide` は `super().decide()` の戻り値をそのまま返す。
      本クラスは `_b160_gaps`（`_SeatProbe.decide` の内側から呼ばれる）で
      **本番が `enumerate_threats` に渡した kw の `roles`** を控えるだけ。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self._k_roles = None

    def _b160_gaps(self, view: dict, kw: dict) -> dict:
        self._k_roles = {n: dict(d) for n, d in (kw.get("roles") or {}).items()}
        return super()._b160_gaps(view, kw)

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        n0 = len(self.seats)
        self._k_roles = None
        chosen = super().decide(view, decision, options)
        if len(self.seats) > n0:
            row = self.seats[-1]
            row["roles"] = self._k_roles or {}
            row["chars"] = {c.get("name"): (c.get("area"), bool(c.get("alive", True)))
                            for c in (view.get("characters") or ())}
            # ★その手が「防御プランが選んだ折り手」なのか「素点で選ばれた手」なのか
            #   （`_b100_plan` は本番が stash する (threats, plan)＝`_B10Probe` と同じ読み方）。
            stash = getattr(self, "_b100_plan", None)
            key = (chosen.get("card"), chosen.get("target"),
                   chosen.get("target_kind"))
            row["in_picks"] = None
            row["kinshi_break"] = None
            if stash:
                threats, plan = stash
                row["in_picks"] = any(
                    (b.card, b.target, b.target_kind) == key for b in plan.picks)
                # その手を**折り手として提案した脅威**が1つでもあったか
                row["kinshi_break"] = any(
                    (b.card, b.target, b.target_kind) == key
                    for t in threats for cd in (t.conditions or ())
                    for b in (cd.breaks or ()))
        return chosen


def _kuromaku_supply_days(state) -> dict:
    """(loop, board) -> クロマク能力でその板へ供給した日のリスト（**秘匿ログ＝計測器の中だけ**）。

    出典＝`sim/effects.apply_mastermind_ability`（`choice["action"]="クロマク:{名前}"`）。
    """
    out: dict = {}
    for e in state.secret_log:
        if e.get("event") != "mm_ability":
            continue
        ch = e.get("choice") or {}
        if ch.get("kind") != "anyaku" or ch.get("target_kind") != "board":
            continue
        if not str(ch.get("action") or "").startswith("クロマク:"):
            continue
        out.setdefault((e.get("loop"), ch.get("target")), []).append(e.get("day"))
    return out


def _exchangeable(roles: dict, target: str, alive: set) -> list:
    """★B-147 §3-2 の作法＝**役職周辺確率ベクトルがまるごと一致する生存者**。

    一致すれば **belief の事後はその2人を入れ替えても不変**＝
    **その観測集合はその2人を区別していない**（どんな推論器でも区別できない）。
    """
    mine = roles.get(target) or {}
    out = []
    for n, d in roles.items():
        if n == target or n not in alive:
            continue
        keys = set(mine) | set(d)
        if all(abs(float(mine.get(k, 0.0)) - float(d.get(k, 0.0))) <= _EPS
               for k in keys):
            out.append(n)
    return sorted(out)


def seats_game(script, seed: int, loops: int = 8) -> tuple:
    """1局ぶん＝(丸損席の行, 自己検査カウンタ)。

    層別（層A／層B）は **`arena.b160_audit` と同じ式**で計算する（同一の入力関数を import）。
    """
    kuro = _true_kuromaku(script)
    hp = _KProbe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(replace(script, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    inc_names = _incident_name_by_day(state)
    supply = _board_supply(state, inc_names)
    ability = _mm_ability_anyaku(state)
    kseats = _kinshi_seats(state)
    start = _loop_start_boards(state, snaps)
    lost = _lost_loops_exact(state)
    ksupply_days = _kuromaku_supply_days(state)

    # 席側の索引：(loop, board) -> その板へ暗躍禁止を選んだ席の行
    by_lb: dict = {}
    for s in hp.seats:
        card, tgt, kind = s["chosen"]
        if card == "暗躍禁止" and kind == "board":
            by_lb.setdefault((s["loop"], tgt), []).append(s)

    chk = Counter()
    rows: list = []
    for lp in range(1, state.loop_no + 1):
        strict, _w = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        for b in sorted(strict):
            n_seats = kseats.get((lp, b), 0)
            if not n_seats:
                continue
            sup = supply.get((lp, b)) or {}
            ab = ability.get((lp, b)) or {"噂": 0, "クロマク": 0, "能力その他": 0}
            st = int((start.get(lp) or {}).get(b, 0) or 0)
            inc = (sup.get("行方不明", 0) + sup.get("邪気の汚染", 0)
                   + sup.get("事件その他", 0))
            true_unstop = st + sup.get("能力相", 0) + sup.get("相その他", 0)
            if true_unstop + inc < 2:
                continue                      # 丸損ではない
            layer = "A" if true_unstop >= 2 else ("B" if inc >= 1 else "C")
            probe_seats = by_lb.get((lp, b)) or []
            # ★自己検査①＝棋譜の枚数とプローブが観測した席数が一致するか
            if len(probe_seats) != n_seats:
                chk["★自己検査① 席数の不一致（棋譜 vs プローブ）"] += 1
            for s in probe_seats:
                roles = s.get("roles") or {}
                chars = s.get("chars") or {}
                alive = {n for n, (_a, al) in chars.items() if al}
                p_true = max((float((roles.get(n) or {}).get("クロマク", 0.0))
                              for n in kuro), default=0.0)
                p_top = max((float(d.get("クロマク", 0.0)) for d in roles.values()),
                            default=0.0)
                # 真のクロマクがその板に生存して居たか（★席時点＝**移動前**の位置）
                here_true = any(chars.get(n, (None, False))[0] == b
                                and chars.get(n, (None, False))[1] for n in kuro)
                # `_threat_board_defeat:1241` の `cultists_here` を**同じ式**で再現
                cult_here = sorted(
                    n for n, d in roles.items()
                    if float(d.get("クロマク", 0.0)) >= _SUSPECT_P
                    and chars.get(n, (None, False))[0] == b
                    and chars.get(n, (None, False))[1])
                # ★自己検査②＝真のクロマクが belief から**消えている**（p=0 かつ生存）
                for n in kuro:
                    if n in alive and float((roles.get(n) or {}).get("クロマク", 0.0)) <= 0.0:
                        chk["★自己検査② 真のクロマクを belief が消していた席"] += 1
                # ★交換可能性は「真のクロマクが生存している席」でだけ意味を持つ
                #   （死者は供給できない＝比較の土俵に載らない）。
                true_alive = [n for n in kuro if n in alive]
                exch = (_exchangeable(roles, true_alive[0], alive)
                        if true_alive else [])
                rows.append({
                    "loop": lp, "day": s["day"], "board": b, "layer": layer,
                    "p_true": round(p_true, 4), "p_top": round(p_top, 4),
                    "top_is_true": bool(p_true >= p_top - _EPS and p_top > 0.0),
                    "here_true": here_true,
                    "cult_here": cult_here,
                    "true_in_cult_here": bool(
                        here_true and any(n in cult_here for n in kuro)),
                    "kuromaku_supply": ab["クロマク"],
                    "rumor_supply": ab["噂"],
                    "start": st, "incident": inc, "true_unstop": true_unstop,
                    "lost": lp in lost,
                    "known_p": s.get("probs", {}).get(b, 0.0),
                    "exch": exch, "n_exch": len(exch),
                    "true_alive": bool(true_alive),
                    "n_kuro_days": len(ksupply_days.get((lp, b)) or []),
                    "kuro_days": sorted(ksupply_days.get((lp, b)) or []),
                    "n_alive": len(alive),
                    "in_picks": s.get("in_picks"),
                    "kinshi_break": s.get("kinshi_break"),
                    # ★フェイズ順（rules/00_rules_core.md:103→104→109）＝
                    #   3 主人公行動 → 4 行動解決（移動が先） → 5 脚本家能力（クロマク）。
                    #   ∴ 主人公が見られるのは**移動前**の位置。その日に実際に供給されたか
                    #   （＝供給時点の位置）と、席時点の位置は別物である。
                    "supplied_today": bool(
                        s["day"] in (ksupply_days.get((lp, b)) or [])),
                })
    return rows, chk


def seats(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    chk: Counter = Counter()
    ex: list = []
    scripts: set = set()
    games: set = set()
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        rows, ck = seats_game(sc, seed, loops=loops)
        chk.update(ck)
        if not rows:
            continue
        games.add((name, seed))
        scripts.add(name)
        for r in rows:
            L = r["layer"]
            c[f"[{L}] 丸損席（枚）"] += 1
            if L != "A":
                continue
            # ---- ★層A（真に止まらない＝供給源はほぼクロマク）だけを割る ----
            c["[A] 　├ 供給にクロマク≥1"] += int(r["kuromaku_supply"] >= 1)
            c["[A] 　├ L1（情報ゼロ）"] += int(r["loop"] == 1)
            c["[A] 　├ L2以降"] += int(r["loop"] >= 2)
            if r["kuromaku_supply"] < 1:
                c["[A] 　└ クロマク供給なし（噂/黒猫のみ）＝本チケットの対象外"] += 1
                continue
            c["[A-K] クロマク由来の丸損席（＝本チケットの母数）"] += 1
            c["[A-K] 　├ P(真のクロマク) ≥ 0.15（_SUSPECT_P）"] += int(
                r["p_true"] >= _SUSPECT_P)
            c["[A-K] 　├ P(真のクロマク) ≥ 0.45（_LIKELY_P）"] += int(
                r["p_true"] >= _LIKELY_P)
            c["[A-K] 　├ P(真)＝席内1位（同率含む）"] += int(r["top_is_true"])
            c["[A-K] 　├ 真のクロマクがその板に生存（★席時点＝移動前の位置）"] += int(
                r["here_true"])
            c["[A-K] 　├ ★cultists_here が非空（＝AIは既に的に疑いを見ていた）"] += int(
                bool(r["cult_here"]))
            c["[A-K] 　├ ★★真のクロマクが cultists_here に入っていた"] += int(
                r["true_in_cult_here"])
            c["[A-K] 　├ 真のクロマクは席時点では板に居なかった"] += int(not r["here_true"])
            c["[A-K] 　├ ★交換可能な生存者が居る（観測が区別していない）"] += int(
                r["n_exch"] >= 1)
            # ★フェイズ順の非対称（rules/00:103→104→109）
            c["[A-K] 　├ ★その日に実際にその板へ供給された"] += int(r["supplied_today"])
            c["[A-K] 　├ ★★供給された日なのに席時点では板に居なかった（移動で入ってきた）"] += int(
                r["supplied_today"] and not r["here_true"])
            # ★その暗躍禁止は「防御プランが選んだ折り手」か「素点で選ばれた手」か
            c["[A-K] 　├ その手が plan.picks に入っていた"] += int(bool(r["in_picks"]))
            c["[A-K] 　├ ★その手を折り手として提案した脅威が在った"] += int(
                bool(r["kinshi_break"]))
            c["[A-K] 　└ そのループを実際に落とした"] += int(r["lost"])
            # ---- ★3分割（互いに排反・合計＝母数）----
            if r["true_in_cult_here"]:
                cls = "[分類1] ★判断の問題（真のクロマクが cultists_here に居た）"
            elif r["here_true"]:
                cls = "[分類2] ★推理の問題（真は板に居たが P<0.15）"
            else:
                cls = "[分類3] ★席時点では板に居ない（行動解決の移動で入ってきた）"
            c[cls] += 1
            c[f"{cls}　└ うちその手が折り手として提案されていた"] += int(
                bool(r["kinshi_break"]))
            if len(ex) < 400:
                ex.append({"script": name, "seed": seed, **r})
    return {"days": days, "n_games": len(games), "n_scripts": len(scripts),
            "scripts": sorted(scripts), "counts": dict(c),
            "self_check": dict(chk), "examples": ex}


# ---------------------------------------------------------------------------
# Phase 3 の材料：★「位置述語を広げる」案の射程と精度（B-160 §4 と同じ作法）
#
# 現行の `cultists_here`（`agents/defense_plan.py:1241`）は
# **「クロマク疑いが**今**その板に居るか」**＝**移動前**の位置で判定する。
# ところが KB のフェイズ順（`rules/00_rules_core.md:103→104-105→109`）は
#   3 主人公行動 → 4 行動解決（★移動が先） → 5 脚本家能力（クロマクの暗躍）
# ＝**主人公が伏せた後に移動してから供給できる**。∴ 素直な是正案は
# **「その板へ**今日移動しうる**クロマク疑いが居るか」**へ広げること（＝述語 W）。
#
# ★しかし脚本家の手札には 移動↑↓／移動←→／移動斜め が揃っている
# （`rules/10_action_cards.md:29-31`）＝**2×2 の盤では任意のエリアから任意のエリアへ
# 1手で移動できる**。∴ W は「禁止エリア」でしか絞れない＝**ほぼ常に真**になりうる。
# それを**言うのではなく測る**のが本節（母数＝実際に暗躍禁止をボードへ置いた全席）。
# ---------------------------------------------------------------------------
def wide_game(script, seed: int, loops: int = 8) -> list:
    from engine.data import forbidden_of

    hp = _KProbe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(replace(script, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    inc_names = _incident_name_by_day(state)
    supply = _board_supply(state, inc_names)
    start = _loop_start_boards(state, snaps)
    lost = _lost_loops_exact(state)
    true_by_loop = {lp: _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)[0]
                    for lp in range(1, state.loop_no + 1)}
    rows: list = []
    for s in hp.seats:
        card, area, kind = s["chosen"]
        if card != "暗躍禁止" or kind != "board":
            continue
        lp = s["loop"]
        roles = s.get("roles") or {}
        chars = s.get("chars") or {}
        susp = [n for n, d in roles.items()
                if float(d.get("クロマク", 0.0)) >= _SUSPECT_P
                and chars.get(n, (None, False))[1]]
        here = [n for n in susp if chars.get(n, (None, False))[0] == area]
        # 述語 W＝その板が禁止エリアでないクロマク疑いが**どこかに**生存している
        wide = [n for n in susp if area not in (forbidden_of(n) or ())]
        sup = supply.get((lp, area)) or {}
        st = int((start.get(lp) or {}).get(area, 0) or 0)
        unstop = (st + sup.get("能力相", 0) + sup.get("相その他", 0)
                  + sup.get("行方不明", 0) + sup.get("邪気の汚染", 0)
                  + sup.get("事件その他", 0))
        rows.append({
            "loop": lp, "day": s["day"], "board": area,
            "n_susp": len(susp), "n_here": len(here), "n_wide": len(wide),
            "flip": bool(wide and not here),
            "unstop_expost": unstop,
            "true_board": area in true_by_loop.get(lp, set()),
            "lost": lp in lost,
        })
    return rows


def wide(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    for _name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        for r in wide_game(sc, seed, loops=loops):
            c["[0] 暗躍禁止をボードへ置いた全席"] += 1
            c["[0]　├ クロマク疑い（p≥0.15）が生存している"] += int(r["n_susp"] >= 1)
            c["[0]　├ 現行 cultists_here が非空（＝今その板に居る）"] += int(r["n_here"] >= 1)
            c["[0]　└ ★述語W が真（その板が禁止でない疑いが居る）"] += int(r["n_wide"] >= 1)
            # ---- ★述語H＝**現行 `cultists_here` をそのまま素点側にも効かせた**場合 ----
            #   （プランナーは既にこの述語で暗躍禁止を折り手から外している＝
            #    `agents/defense_plan.py:1241-1248`。素点側にはこのゲートが無い
            #    ＝`agents/heuristic_protagonist.py:4750-4753` は幻想限定/カルティスト限定）
            if r["n_here"] >= 1:
                c["[H] ★現行 cultists_here が非空だったのに暗躍禁止を置いた席"] += 1
                if r["unstop_expost"] >= 2:
                    c["[H]　├ ○正しい抑止（止まらない供給だけで実際に2へ届いた）"] += 1
                else:
                    c["[H]　├ ★×誤った抑止（届かなかった＝効いていたかもしれない席）"] += 1
                c["[H]　└ うち真の敗北板"] += int(r["true_board"])
            if not r["flip"]:
                continue
            c["[1] ★W が現行を反転させた席（＝暗躍禁止が折り手から消える）"] += 1
            if r["unstop_expost"] >= 2:
                c["[1]　├ ○正しい抑止（止まらない供給だけで実際に2へ届いた）"] += 1
            else:
                c["[1]　├ ★×誤った抑止（届かなかった＝効いていたかもしれない席）"] += 1
            c["[1]　└ うち真の敗北板"] += int(r["true_board"])
    n = c["[1] ★W が現行を反転させた席（＝暗躍禁止が折り手から消える）"]
    ok = c["[1]　├ ○正しい抑止（止まらない供給だけで実際に2へ届いた）"]
    nh = c["[H] ★現行 cultists_here が非空だったのに暗躍禁止を置いた席"]
    okh = c["[H]　├ ○正しい抑止（止まらない供給だけで実際に2へ届いた）"]
    return {"days": days, "counts": dict(c),
            "precision": (round(ok / n, 4) if n else None),
            "precision_here": (round(okh / nh, 4) if nh else None)}


# ---------------------------------------------------------------------------
# Phase 2：KB からクロマクに結合した公開チャネルの完全列挙
# ---------------------------------------------------------------------------
_KB_CHANNELS = [
    ("K1", "＋位置（キャラ暗躍）",
     "脚本家能力フェイズの**キャラへの暗躍+1** → 供給源はクロマクだけ＝"
     "その瞬間そのエリアに居た顔ぶれ（present）の中に居る",
     "rules/40_first_steps.md:86 / rules/50_basic_tragedy_x.md:115",
     "belief 消費済み＝agents/belief.py:448-450（_public_role_constraints の kuro ∩）"),
    ("K2", "＋位置（ボード暗躍）",
     "脚本家能力フェイズの**ボードへの暗躍+1** → クロマク∈present か 不穏な噂。"
     "噂を追加しないルール組では present の ∩ がそのままクロマク制約になる",
     "rules/40:86（自ボード限定）／rules/40:61-62（噂）",
     "belief 消費済み＝agents/belief.py:446-447（board_sets）＋:1290-1293（∩）"),
    ("K3", "＋位置（同一フェイズにボード暗躍≥2）",
     "噂は 1/loop・1箇所＝同一フェイズにボード暗躍が2箇所出たら少なくとも1つはクロマク由来"
     "＝クロマク∈(そのフェイズの present の和集合)。**噂ありのルール組にも効く**",
     "rules/40:61（噂は任意のボード1つ・1/loop）／rules/40:86",
     "belief 消費済み＝agents/belief._phase_kuromaku_unions（:587-626）＋:1294-1297"),
    ("K4", "−除外（大物のテリトリー投射）",
     "大物は present に居なくても能力を使える＝K1〜K3 のハード制約が**大物にだけ**成立しない"
     "（健全側に緩める＝真の配役を消さない）",
     "rules/20_goodwill_abilities.md:201-203 / rules/60_faq_rulings.md:180",
     "belief 消費済み＝agents/belief._oomono_relax（:409-411）"),
    ("K5", "＝事前分布（逆引きルール）",
     "クロマクを追加するルールは FS＝殺人計画(Y)・復讐者の灯火(Y)／"
     "BTX＝殺人計画(Y)・封印されしモノ(Y)＝**ルール組の枝刈りでスロットの有無が動く**",
     "rules/40:37,41 / rules/50:32,37",
     "belief 消費済み＝agents/belief._combos／_combo_pi"),
    ("K6", "＋確定（役職公開）",
     "役職が公開されればクロマクは確定する。ただしクロマクは**自発的に公開しない**"
     "（公開を伴う【強制】を持たない）＝主人公が資源を割いて暴くしかない",
     "rules/50:231（最後の戦いのために明かさない役職の筆頭にクロマク）",
     "belief 消費済み＝agents/belief._revealed_roles（:273）"),
    ("K7", "−除外（友好能力が拒否されずに解決した）",
     "★**片側だけがハード**：クロマクの条文能力は**友好無視（【任意】＝見送れる）**であって"
     "**絶対**友好無視ではない＝**解決してもクロマクを除外できない**。"
     "拒否された時も「友好無視を持つ役職の誰か」までしか言えない",
     "rules/00_rules_core.md:173-174 / rules/20:24-25 / rules/40:85（条文能力＝友好無視）",
     "★belief は**使えない**＝agents/belief.py:311-313 が"
     "「通常の友好無視は【任意】＝除外できない」と明記（＝KB どおり）"),
    ("K8", "＝盤面（ボードX＝復讐者の灯火）",
     "ルールY『復讐者の灯火』では**ボードX＝クロマクの初期エリア**"
     "＝板の敗北とクロマクの初期位置が結合する（ただし X は脚本家しか知らない）",
     "rules/40:41-43",
     "AI 消費済み（推論ではなく採点側）＝agents/defense_plan.py:1983-1999"),
    ("K9", "×（前向きチャネルは存在しない）",
     "★クロマクの供給は**必ず公開イベント（暗躍+1）として盤面に出る**（フレンドと違い"
     "痕跡が出ないことは無い）。しかし**出るのは供給した後**＝"
     "**そのループで最初の供給が起きるまでは、位置チャネルは1件も存在しない**",
     "rules/00_rules_core.md:109（脚本家能力フェイズ）",
     "＝L1 の序盤が原理的に情報ゼロになる理由"),
    ("K10", "×（位置の観測はフェイズ1つ分ずれる）",
     "★**主人公が札を伏せるのはフェイズ3、クロマクが供給するのはフェイズ5、"
     "その間のフェイズ4で移動が先に解決される**＝主人公が見て判断できる位置は"
     "**移動前**であり、クロマクは**そのあと的の板へ入ってから**供給できる。"
     "∴ 『今その板にクロマク疑いが居るか』という述語は**構造的に1手遅れる**",
     "rules/00_rules_core.md:103（3 主人公行動）→:104-105（4 行動解決・移動が先）"
     "→:109（5 脚本家能力）",
     "★AI 側の該当実装＝`agents/defense_plan.py:1241` の `cultists_here`"
     "（view の現在エリアで判定＝移動前）"),
]


def kb_text() -> str:
    out = ["=== B-162 Phase 2：KB がクロマクに結合させている公開チャネルの完全列挙 ===", ""]
    for cid, kind, body, src, use in _KB_CHANNELS:
        out.append(f"[{cid}] {kind}")
        out.append(f"      {body}")
        out.append(f"      出典＝{src}")
        out.append(f"      消費＝{use}")
        out.append("")
    out.append("★フレンド（B-147）との構造的な差＝**クロマクは供給するたびに位置を晒す**。")
    out.append("  フレンドの【強制】は「ループ終了時に死亡」「公開済み」でしか発火しない"
               "（rules/40:126-133）＝生存中・未公開なら痕跡ゼロ。")
    out.append("  クロマクは供給が公開イベント＝K1〜K3 の位置制約が毎回付く"
               "＝**belief は L2 以降ほぼ名指しできている**（§curve）。")
    out.append("★残る構造的な壁は2つ＝**K9（供給前は何も出ない）**と"
               "**K10（位置の観測がフェイズ1つ分遅れる）**。")
    return "\n".join(out)


# ---------------------------------------------------------------------------
def _switches(days: int) -> str:
    import agents.defense_plan as DP
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    return (f"_SUSPECT_P={DP._SUSPECT_P} / _LIKELY_P={DP._LIKELY_P} / "
            f"DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER} / "
            f"B100_MIX={HP.B100_MIX} / B100_THETA={HP.B100_THETA} / "
            f"B100_IRON_PROB={HP.B100_IRON_PROB} / "
            f"B141B_UNLOCK_SAME_DAY={HP.B141B_UNLOCK_SAME_DAY} / "
            f"B134_CARD_DISTANCE={DP.B134_CARD_DISTANCE} / "
            f"B162 切替口＝無し（計測のみ・agents/ 非接触） / days={days}")


def _verify_game(script, seed: int, loops: int = 8) -> bool:
    """★挙動不変の物証＝プローブ付き対局と素の対局で棋譜・結末・ループ数が完全一致。"""
    probe = replace(script, loops=loops)
    base = HeuristicProtagonist(seed)          # ★3席は同一インスタンス（既存の作法）
    s0, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": base, "p2": base, "p3": base})
    hp, mm = _KProbe(seed), _MMProbe(seed)
    s1, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    hp2 = _CalibratedProtagonist(seed)
    s2, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": hp2, "p2": hp2, "p3": hp2})
    k = [[(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
          for e in s.history] for s in (s0, s1, s2)]
    return (k[0] == k[1] == k[2] and _outcome(s0) == _outcome(s1) == _outcome(s2)
            and s0.loop_no == s1.loop_no == s2.loop_no)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-162 計測（クロマク同定の速度）")
    ap.add_argument("cmd", choices=("curve", "seats", "wide", "verify", "kb"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "kb":
        print(kb_text())
        return 0

    print(f"[切替口] {_switches(a.days)}")
    from arena.benchmark import benchmark_scripts

    if a.cmd == "verify":
        bad = n = 0
        for name, seed, sc in list(benchmark_scripts(days=a.days))[a.start:a.end]:
            n += 1
            if not _verify_game(sc, seed, loops=a.loops):
                bad += 1
                print(f"  ★不一致: {name} s{seed}")
        print(f"棋譜の不一致 = {bad} 件 / {n}局")
        return 1 if bad else 0

    if a.cmd == "curve":
        r = curve(days=a.days, loops=a.loops, start=a.start, end=a.end)
        print(f"=== B-162 Phase 1-1：ループ別 P(真のクロマク)（{a.days}日級）===")
        print(f"母数＝クロマクが配役された局 {r['n_games_kuromaku']}/{r['n_games_all']}局・"
              f"★独立脚本 {r['n_scripts_kuromaku']}/{r['n_scripts_all']}本")
        print(f"脚本＝{', '.join(r['scripts_kuromaku'])}")
        print("  loop | 席数 | 局数 | 平均P(真) | ≥0.15 | ≥0.45 | 席内1位")
        for lp, d in r["by_loop"].items():
            print(f"  L{lp}   | {d['n_seats']:>4} | {d['n_games']:>4} | "
                  f"{d['mean_p']:>9.3f} | {d['ge_suspect_rate']:>6.1%} | "
                  f"{d['ge_likely_rate']:>6.1%} | {d['top_rate']:>6.1%}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    if a.cmd == "wide":
        r = wide(days=a.days, loops=a.loops, start=a.start, end=a.end)
        print(f"=== B-162 Phase 3 材料：位置述語を広げた場合の射程と精度（{a.days}日級）===")
        for k, v in sorted(r["counts"].items()):
            print(f"  {k:<58} {v}")
        print(f"  ★述語W の精度（正しい抑止 ÷ 反転席）＝{r['precision']}")
        print(f"  ★述語H の精度（正しい抑止 ÷ H席）＝{r['precision_here']}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    r = seats(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(f"=== B-162 Phase 1-2：丸損席と P(真のクロマク) の対応（{a.days}日級 "
          f"{r['n_games']}局・★独立脚本 {r['n_scripts']}本）===")
    print(f"脚本＝{', '.join(r['scripts'])}")
    for k, v in sorted(r["counts"].items()):
        print(f"  {k:<62} {v}")
    print("\n--- ★自己検査（0 でなければ計測器のバグ） ---")
    if not r["self_check"]:
        print("  すべて 0")
    for k, v in sorted(r["self_check"].items()):
        print(f"  {k:<62} {v}")
    print("\n--- 現物（最大400件） ---")
    for e in r["examples"]:
        print(f"    {e['script']} s{e['seed']} L{e['loop']}D{e['day']} 板={e['board']} "
              f"層{e['layer']} P(真)={e['p_true']:.3f} 席内最大={e['p_top']:.3f} "
              f"真が板に居た={e['here_true']} cult_here={e['cult_here']} "
              f"クロ供給日={e['kuro_days']} 交換可能={e['n_exch']} "
              f"折り手提案={e['kinshi_break']} 敗北={e['lost']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
