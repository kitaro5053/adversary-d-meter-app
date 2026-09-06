# -*- coding: utf-8 -*-
"""B-160 Phase 1：**「暗躍禁止で止まらない供給」の丸損席を供給源で層別する**（★計測のみ）。

## 発端

`docs/監査_B159_行方不明の脅威builder_2026-08-04.md` §5＝
`agents/defense_plan.unstoppable_supply_gap`（DP-6 の単一ソース）の `contrib` に
**不穏な噂の残弾しか入っていない**。関数の定義は
「**行動解決フェイズの暗躍カード（＝暗躍禁止で断てる供給）があと何個要るか**」なので、
**暗躍禁止で断てない供給は全部 `contrib` 側**であるべき。

KB＝`rules/10_action_cards.md:65`「暗躍禁止は**行動解決フェイズでのみ**有効」。
∴ 暗躍禁止で止まらない供給＝
  (1) **脚本家能力フェイズ**（不穏な噂 `rules/40:61-62`／クロマク `rules/40:86,88`）
  (2) **事件フェイズ**（行方不明 `rules/40:153`＝犯人のいるボードへ+1／
      邪気の汚染 `rules/50:199`＝神社へ+2）
  (3) **ループ開始時**（黒猫特性1＝神社へ+1・`rules/30`／`sim/state.py:520`）

## 本計測が出す数（★本チケットの核心＝**層別**）

B-159 §5-2 は「止まらない供給だけで2に届いた」を1つの塊で数えた。本計測はそれを
**供給源で割る**：

- **層A＝真に止まらない**（黒猫のループ開始+1／不穏な噂／クロマク／その他相）だけで
  既に閾値2へ届く＝**事件フェイズを数えても数えなくても結論が変わらない**席。
- **層B＝事件フェイズ由来**（層Aだけでは2に届かず、事件フェイズの供給を足して初めて届く）
  ＝★**別の折り手がある**（犯人冷却＝`rules/00_rules_core.md:119-121`）＝
  「暗躍禁止をやめる」判断に**発生の不確実性**が絡む層。
- **層C＝その他**（分類できなかった供給）。

★**B-159 との差（意図的な独立実装）**：B-159 の `futile` は**公開イベントだけ**を数え、
**ループ開始時の盤面（黒猫の+1）を勘定に入れていない**＝止まらない供給を**過小**に見る。
本計測は `phase_snapshots` の「L*D1 脚本家行動フェイズ前」から**ループ開始値**を取り、
`events_only`（B-159 互換）と `with_start`（是正版）の**両方**を出す。

## 因果の限界（★先に書く）

1. **ex-post の観測**＝「そのループで実際に乗った供給」で数える。主人公が**決定席の時点で
   それを知りえたか**は別問題＝`seat` 側の指標（`known_defeat_board`／`gap_old`／`gap_new`）で
   併記する。**丸損席の総数は「射程の上限」であって射程そのものではない**。
2. 1手変えれば以降の全系列が変わる＝**真の反実仮想は取れない**（B-157／B-159 と同じ）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b160_audit verify --days 3 --end 8   # プローブの挙動不変
    python -m arena.b160_audit strat  --days 3
    python -m arena.b160_audit strat  --days 5
    python -m arena.b160_audit b10    --days 3           # ★§6：B-10 が死んでいるか
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _outcome, _snap_index
from arena.b146_probe import _true_boards
from arena.b149_audit import _MMProbe
from arena.b157_audit import _lost_loops_exact
from sim import run_game

_AREAS = ("病院", "神社", "都市", "学校")

#: `_threat_board_defeat` が板を脅威化する足切り（`agents/defense_plan.py:1206`）。
RULE_P_MIN = 0.05


# ---------------------------------------------------------------------------
# ground truth（棋譜そのもの。★B-159 とは別実装＝独立再現のため）
# ---------------------------------------------------------------------------
def _incident_name_by_day(state) -> dict:
    """(loop, day) -> その日に**発生した**事件名（公開イベント `incident` の occurs=True）。"""
    out: dict = {}
    for e in state.history:
        if e.get("event") == "incident" and e.get("occurs"):
            out[(e.get("loop"), e.get("day"))] = e.get("name")
    return out


def _mm_ability_anyaku(state) -> dict:
    """(loop, board) -> {"噂": n, "クロマク": n, "能力その他": n}。

    出典＝`sim/effects.apply_mastermind_ability`（`choice["action"]`＝
    `"不穏な噂"` or `"クロマク:{名前}"`）。`_sec` は phase タグを持たないが、
    `mm_ability` イベント自体が脚本家能力フェイズ専用＝相は一意。
    """
    out: dict = {}
    for e in state.secret_log:
        if e.get("event") != "mm_ability":
            continue
        ch = e.get("choice") or {}
        if ch.get("kind") != "anyaku" or ch.get("target_kind") != "board":
            continue
        key = (e.get("loop"), ch.get("target"))
        d = out.setdefault(key, {"噂": 0, "クロマク": 0, "能力その他": 0})
        act = str(ch.get("action") or "")
        if act == "不穏な噂":
            d["噂"] += 1
        elif act.startswith("クロマク:"):
            d["クロマク"] += 1
        else:
            d["能力その他"] += 1
    return out


def _board_supply(state, incident_names: dict) -> dict:
    """(loop, board) -> 供給の内訳（★フェイズ別・公開イベントの delta>0 だけ）。"""
    out: dict = {}
    for e in state.history:
        if e.get("event") != "anyaku" or e.get("target") not in _AREAS:
            continue
        d = int(e.get("delta", 0) or 0)
        if d <= 0:
            continue
        key = (e.get("loop"), e.get("target"))
        rec = out.setdefault(key, {"行動解決": 0, "能力相": 0, "行方不明": 0,
                                   "邪気の汚染": 0, "事件その他": 0, "相その他": 0})
        ph = e.get("phase")
        if ph == "action_resolution":
            rec["行動解決"] += d
        elif ph == "mastermind_ability":
            rec["能力相"] += d
        elif ph == "incident":
            nm = incident_names.get((e.get("loop"), e.get("day")))
            if nm in ("行方不明", "邪気の汚染"):
                rec[nm] += d
            else:
                rec["事件その他"] += d
        else:
            rec["相その他"] += d
    return out


def _kinshi_seats(state) -> dict:
    """(loop, board) -> そのループに主人公が置いた暗躍禁止の枚数（公開の cards_revealed）。"""
    out: Counter = Counter()
    for e in state.history:
        if e.get("event") != "cards_revealed":
            continue
        for p in (e.get("placements") or ()):
            if (p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止"
                    and p.get("target_kind") == "board"):
                out[(e.get("loop"), p.get("target"))] += 1
    return dict(out)


def _loop_start_boards(state, snaps: dict) -> dict:
    """loop -> ループ開始時（D1 脚本家行動フェイズ前）の board_anyaku。

    ★黒猫特性1（ループ開始時に神社へ暗躍+1・`sim/state.py:520`）は**イベントを発行しない**
    ＝公開ログには現れない。ここを数えないと「止まらない供給」を過小に見る（B-159 の
    `futile` はイベントだけを見ている＝過小側）。
    """
    out: dict = {}
    for lp in range(1, state.loop_no + 1):
        sn = snaps.get((lp, 1, "脚本家行動フェイズ前"))
        if sn is not None:
            out[lp] = dict(sn.get("board_anyaku") or {})
    return out


# ---------------------------------------------------------------------------
# プローブ（★挙動不変＝`super()` の戻り値をそのまま返す）
# ---------------------------------------------------------------------------
class _SeatProbe(HeuristicProtagonist):
    """`plan_for_belief` が使う `defeat_board_probs` を席ごとに控えるだけのプローブ。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.seats: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        import agents.defense_plan as dp

        orig = dp.enumerate_threats
        cap: dict = {}

        def _rec(*a, **kw):
            r = orig(*a, **kw)
            cap["kw"] = dict(kw)
            # `enumerate_threats(view, roles, ...)`＝roles は**位置引数**（kw に来ない）
            cap["kw"].setdefault("roles", a[1] if len(a) > 1 else {})
            return r

        dp.enumerate_threats = _rec
        try:
            chosen = super().decide(view, decision, options)
        finally:
            dp.enumerate_threats = orig
        if "kw" in cap:
            probs = cap["kw"].get("defeat_board_probs") or {}
            row = {
                "loop": view.get("loop"), "day": view.get("day"),
                "probs": {k: float(v) for k, v in probs.items()},
                "banr": dict(view.get("board_anyaku") or {}),
                "chosen": (chosen.get("card"), chosen.get("target"),
                           chosen.get("target_kind")),
            }
            try:
                row["gaps"] = self._b160_gaps(view, cap["kw"])
            except Exception as e:                          # noqa: BLE001
                row["gaps"] = {}
                row["error"] = repr(e)
            self.seats.append(row)
        return chosen

    # -- ★事件フェイズ分を足した gap（**計算するだけ・使わない**） ---------------
    def _b160_gaps(self, view: dict, kw: dict) -> dict:
        """{board: (gap_old, inc_contrib)}。`gap_old` は**本番と同じ引数**で再計算する
        （`plan_for_belief` が `enumerate_threats` に渡した kw をそのまま使う＝二重実装なし）。"""
        from agents.defense_plan import (BOARD_DEFEAT_ANYAKU,
                                         _b159_missing_feeds,
                                         unstoppable_supply_gap)
        banr = view.get("board_anyaku") or {}
        culprits = kw.get("culprits") or {}
        day_now = int(view.get("day", 1) or 1)
        out: dict = {}
        for area in _AREAS:
            cur = int(banr.get(area, 0) or 0)
            gap_old = unstoppable_supply_gap(
                view, area, cur, BOARD_DEFEAT_ANYAKU,
                supply_rumor=kw.get("supply_rumor", False),
                roles=kw.get("roles") or {},
                rumor_left=kw.get("rumor_left"))
            inc = 0
            inc_strict = 0
            for i in (view.get("incidents") or ()):
                nm, dy = i.get("name"), i.get("day")
                if dy is None or dy < day_now:
                    continue
                if nm == "邪気の汚染" and area == "神社":
                    inc += 2                        # rules/50:199（神社へ+2・無条件）
                    if self._b160_certain(view, dy, culprits):
                        inc_strict += 2
                elif nm == "行方不明" and _b159_missing_feeds(view, area, dy,
                                                          culprits):
                    inc += 1                        # rules/40:153（E-2 到達判定つき）
            out[area] = (int(gap_old), int(inc), int(inc_strict))
        return out

    @staticmethod
    def _b160_certain(view: dict, dy, culprits) -> bool:
        """★狭い述語＝その日の事件が「**発生が既に確定**しており、かつ**犯人冷却では折れない**」か。

        - 発生条件＝`rules/00_rules_core.md:119-121`（犯人生存＋不安が臨界以上）。
          ∴ **生存候補が全員すでに臨界以上**なら、誰が犯人でも発生する（公開情報のみで言える）。
        - 折り手＝`_cooling_breaks`（`agents/defense_plan.py:1136`）が成立しないこと
          ＝候補が1人でない／`不安-1` が手札に無い／`不安-1` でも臨界を割れない。
          ★本計測では**在庫は見ない**（席ごとの手札に依存＝上限側に倒す＝
          「折り手がある」と言える側を厳しめに数える）。
        """
        from engine.data import unrest_threshold_of
        chars = {c.get("name"): c for c in (view.get("characters") or ())}
        cands = [n for n in ((culprits or {}).get(dy) or ())
                 if (chars.get(n) or {}).get("alive", True)
                 and (chars.get(n) or {}).get("area") is not None]
        if not cands:
            return False                     # 絞れない＝確定と言えない
        for n in cands:
            th = unrest_threshold_of(n)
            u = int((chars.get(n) or {}).get("unrest", 0) or 0)
            if th is None or u < th:
                return False                 # 1人でも臨界未満＝発生は確定でない
        if len(cands) == 1:
            th = unrest_threshold_of(cands[0])
            u = int((chars.get(cands[0]) or {}).get("unrest", 0) or 0)
            if th is not None and u - 1 < th:
                return False                 # 冷却1点で折れる＝別の折り手がある
        return True


# ---------------------------------------------------------------------------
def strat_game(script, seed: int, loops: int = 8) -> list:
    """1局ぶんの「真の敗北板へ置いた暗躍禁止」を loop×board で層別する。"""
    hp = _SeatProbe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(replace(script, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    inc_names = _incident_name_by_day(state)
    supply = _board_supply(state, inc_names)
    ability = _mm_ability_anyaku(state)
    seats = _kinshi_seats(state)
    start = _loop_start_boards(state, snaps)
    lost = _lost_loops_exact(state)
    # 主人公が「敗北板」と見ていたか＝そのループの席の最大 rule_p
    known: dict = {}
    for s in hp.seats:
        for b, p in (s.get("probs") or {}).items():
            k = (s["loop"], b)
            known[k] = max(known.get(k, 0.0), float(p))

    rows: list = []
    for lp in range(1, state.loop_no + 1):
        strict, _w = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        for b in sorted(strict):
            n_seats = seats.get((lp, b), 0)
            if not n_seats:
                continue
            sup = supply.get((lp, b)) or {"行動解決": 0, "能力相": 0, "行方不明": 0,
                                          "邪気の汚染": 0, "事件その他": 0,
                                          "相その他": 0}
            ab = ability.get((lp, b)) or {"噂": 0, "クロマク": 0, "能力その他": 0}
            st = int((start.get(lp) or {}).get(b, 0) or 0)
            inc = sup["行方不明"] + sup["邪気の汚染"] + sup["事件その他"]
            # ★層A（真に止まらない）＝黒猫のループ開始値＋能力相（噂・クロマク）＋相その他
            true_unstop = st + sup["能力相"] + sup["相その他"]
            rows.append({
                "loop": lp, "board": b, "seats": n_seats,
                "start": st, "action": sup["行動解決"], "ability": sup["能力相"],
                "rumor": ab["噂"], "kuromaku": ab["クロマク"],
                "ability_other": ab["能力その他"],
                "missing": sup["行方不明"], "jaki": sup["邪気の汚染"],
                "inc_other": sup["事件その他"], "phase_other": sup["相その他"],
                "true_unstop": true_unstop, "incident": inc,
                "unstop_events": sup["能力相"] + inc + sup["相その他"],
                "lost": lp in lost,
                "known_p": round(known.get((lp, b), 0.0), 4),
            })
    return rows


def strat(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    ex: list = []
    games: set = set()
    scripts: set = set()
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        games.add((name, seed))
        scripts.add(name)
        for r in strat_game(sc, seed, loops=loops):
            # ★キーは層ごとに接頭辞を必ず変える（B-159 の初版が Counter のキー衝突で
            #   誤読を生んだ＝同じ轍を踏まない）。
            c["[0] 真の敗北板へ暗躍禁止を置いたループ×板"] += 1
            c["[0]　└ そこで使った席（枚数）"] += r["seats"]
            # ---- B-159 互換（イベントだけ＝ループ開始値を数えない） ----
            if r["unstop_events"] >= 2:
                c["[互換] 止まらない供給だけで2に届いた（B-159 §5-2 と同じ数え方）"] += 1
                c["[互換]　└ 丸損した席（枚数）"] += r["seats"]
                c["[互換]　└ うち行方不明の寄与≥1"] += int(r["missing"] >= 1)
            # ---- 是正版（ループ開始値＝黒猫特性1 を含む） ----
            tot = r["true_unstop"] + r["incident"]
            if tot < 2:
                continue
            c["[是正] 止まらない供給だけで2に届いた（黒猫のループ開始値込み）"] += 1
            c["[是正]　└ 丸損した席（枚数）"] += r["seats"]
            c["[是正]　└ うちそのループを実際に落とした"] += int(r["lost"])
            # ---- ★層別（本チケットの核心） ----
            if r["true_unstop"] >= 2:
                c["[A] 真に止まらないだけで2（事件を数えても結論不変）"] += 1
                c["[A]　└ 席（枚数）"] += r["seats"]
                c["[A]　├ 供給に噂≥1"] += int(r["rumor"] >= 1)
                c["[A]　├ 供給にクロマク≥1"] += int(r["kuromaku"] >= 1)
                c["[A]　├ 供給に黒猫（ループ開始値≥1）"] += int(r["start"] >= 1)
                c["[A]　├ L1（情報ゼロのループ）"] += int(r["loop"] == 1)
                c["[A]　├ L2以降"] += int(r["loop"] >= 2)
                c["[A]　└ 主人公が敗北板と見ていた（rule_p≥0.05）"] += int(
                    r["known_p"] >= RULE_P_MIN)
            elif r["incident"] >= 1:
                c["[B] ★事件フェイズを足して初めて2（別の折り手＝犯人冷却あり）"] += 1
                c["[B]　└ 席（枚数）"] += r["seats"]
                c["[B]　├ 行方不明の寄与≥1"] += int(r["missing"] >= 1)
                c["[B]　├ 邪気の汚染の寄与≥1"] += int(r["jaki"] >= 1)
                c["[B]　├ その他事件の寄与≥1"] += int(r["inc_other"] >= 1)
                c["[B]　├ 事件だけで2に届く（事件寄与≥2）"] += int(r["incident"] >= 2)
                c["[B]　├ L1（情報ゼロのループ）"] += int(r["loop"] == 1)
                c["[B]　├ L2以降"] += int(r["loop"] >= 2)
                c["[B]　└ 主人公が敗北板と見ていた（rule_p≥0.05）"] += int(
                    r["known_p"] >= RULE_P_MIN)
                if len(ex) < 60:
                    ex.append({"script": name, "seed": seed, **r})
            else:
                c["[C] その他（分類不能）"] += 1
                c["[C]　└ 席（枚数）"] += r["seats"]
                if len(ex) < 60:
                    ex.append({"script": name, "seed": seed, **r})
    return {"days": days, "n_games": len(games), "n_scripts": len(scripts),
            "scripts": sorted(scripts), "counts": dict(c), "examples": ex}


# ---------------------------------------------------------------------------
# ★Phase 1-4：**素朴な述語（事件寄与をそのまま contrib に足す）の精度**
#
# 述語＝`gap_new = gap_old - inc_contrib` が **≤0 に反転**した板では
# `truly_unstoppable=True` になり `_add_kinshi_break` が呼ばれない
# ＝**その板への暗躍禁止が折り手から消える**（`agents/defense_plan.py:1245`）。
# ∴ 席レベルの射程＝「反転した板へ実際に暗躍禁止を置いた席」。
# その席を ex-post で2つに割る：
#   ・**正しい抑止**＝そのループの止まらない供給だけで実際に2へ届いた（＝丸損だった）
#   ・★**誤った抑止**＝届かなかった（＝その暗躍禁止は効いていたかもしれない）
#     ＝B-159 §5-3 が警告した「事件が発生しなかったループで板を素通しにする」形。
# ---------------------------------------------------------------------------
def flip_game(script, seed: int, loops: int = 8) -> list:
    hp = _SeatProbe(seed)
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
        lp = s["loop"]
        for area, (gap_old, inc, inc_strict) in (s.get("gaps") or {}).items():
            if inc <= 0:
                continue
            gap_new = gap_old - inc
            placed = (s["chosen"] == ("暗躍禁止", area, "board"))
            sup = supply.get((lp, area)) or {}
            st = int((start.get(lp) or {}).get(area, 0) or 0)
            unstop = (st + sup.get("能力相", 0) + sup.get("相その他", 0)
                      + sup.get("行方不明", 0) + sup.get("邪気の汚染", 0)
                      + sup.get("事件その他", 0))
            rows.append({
                "loop": lp, "day": s["day"], "board": area,
                "gap_old": gap_old, "inc": inc, "gap_new": gap_new,
                "inc_strict": inc_strict,
                "flip": bool(gap_old >= 1 and gap_new <= 0),
                "flip_strict": bool(gap_old >= 1 and gap_old - inc_strict <= 0),
                "placed": placed,
                "true_board": area in true_by_loop.get(lp, set()),
                "known_p": round(float((s.get("probs") or {}).get(area, 0.0)), 4),
                "unstop_expost": unstop, "lost": lp in lost,
            })
    return rows


def flip(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    ex: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        for r in flip_game(sc, seed, loops=loops):
            c["[0] 事件寄与>0 の（席×板）"] += 1
            # ---- ★狭い述語（発生確定×冷却で折れない×邪気＝板固定）----
            if r["flip_strict"]:
                c["[S] ★狭い述語が反転した"] += 1
                if r["placed"]:
                    c["[S]　├ ★★その席で実際に暗躍禁止をその板へ置いていた（＝射程）"] += 1
                    if r["unstop_expost"] >= 2:
                        c["[S]　│　├ ○正しい抑止"] += 1
                    else:
                        c["[S]　│　└ ×誤った抑止"] += 1
            if not r["flip"]:
                continue
            c["[1] ★述語が反転した（gap_old≥1 → gap_new≤0）"] += 1
            c["[1]　├ うち真の敗北板"] += int(r["true_board"])
            c["[1]　└ うち主人公が敗北板と見ていた（rule_p≥0.05）"] += int(
                r["known_p"] >= RULE_P_MIN)
            if not r["placed"]:
                continue
            c["[2] ★★その席で実際に暗躍禁止をその板へ置いていた（＝射程）"] += 1
            if r["unstop_expost"] >= 2:
                c["[2]　├ ○正しい抑止（止まらない供給だけで実際に2へ届いた）"] += 1
            else:
                c["[2]　├ ★×誤った抑止（届かなかった＝効いていたかもしれない席）"] += 1
                if len(ex) < 60:
                    ex.append({"script": name, "seed": seed, **r})
            c["[2]　└ うち真の敗北板"] += int(r["true_board"])
    return {"days": days, "counts": dict(c), "examples": ex}


# ---------------------------------------------------------------------------
# ★§6：B-10（邪気の汚染の犯人冷却）が**決定へ届いているか**
#
# 経路は2本しかない（`agents/heuristic_protagonist.py`）：
#   (1) 加点＝`_defense_plan_recs`（:647）→ `_plan_recs`（:5943 で素点に加算）。
#       `_plan_coeffs`（:638）は `PLAN_CLASS_COEFFS`（:506）に載る種別しか返さない。
#       `不安-1` の種別は `_plan_card_class`（:61-71）で **"不安"** ＝**未登録＝None＝加点0**。
#   (2) 絶対防御＝`b100_mix.forced_pick` / `b100_alloc.allocate`（B100_JOINT=True）。
#       資格は `gate_reason`（`agents/b100_alloc.py:246`）＝θ経路のみ（鉄則は
#       `B100_IRON_PROB=None` で OFF・供給ゲートも既定 OFF）。
# ∴ **`_b100_log` の reason に「邪気の汚染」が現れた回数**＝決定へ届いた回数。
# ---------------------------------------------------------------------------
class _B10Probe(HeuristicProtagonist):
    """邪気の汚染の犯人冷却が (a)生成 (b)plan採用 (c)加点 (d)絶対防御 のどこまで届いたか。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.b10 = Counter()
        self.b10_probs: list = []
        self.hits: list = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        n_log = len(getattr(self, "_b100_log", []) or [])
        chosen = super().decide(view, decision, options)
        stash = getattr(self, "_b100_plan", None)
        if stash:
            threats, plan = stash
            gen: set = set()
            for t in threats:
                if getattr(t, "kind", "") != "board_defeat":
                    continue
                for cd in (t.conditions or ()):
                    for b in (cd.breaks or ()):
                        if "邪気の汚染" in str(b.label):
                            gen.add((b.card, b.target, b.target_kind))
                            self.b10_probs.append(round(float(t.prob), 4))
            if gen:
                self.b10["(a) 邪気の汚染の犯人冷却 Break が生成された席"] += 1
                picked = {(b.card, b.target, b.target_kind) for b in plan.picks}
                if gen & picked:
                    self.b10["(b) それが plan.picks に採られた席"] += 1
                if gen & set((getattr(self, "_plan_recs", None) or {}).keys()):
                    self.b10["(c) ★加点された席（PLAN_CLASS_COEFFS 経由）"] += 1
                if any((chosen.get("card"), chosen.get("target"),
                        chosen.get("target_kind")) == k for k in gen):
                    self.b10["(d) その席で実際にその手が打たれた"] += 1
                    self.hits.append({"loop": view.get("loop"),
                                      "day": view.get("day"),
                                      "card": chosen.get("card"),
                                      "target": chosen.get("target")})
        for e in (getattr(self, "_b100_log", []) or [])[n_log:]:
            if "邪気の汚染" in str(e.get("reason") or ""):
                self.b10["(e) ★絶対防御(B-100)が邪気の汚染の冷却を強制した席"] += 1
        return chosen


def b10_game(script, seed: int, loops: int = 8) -> tuple:
    hp = _B10Probe(seed)
    mm = HeuristicMastermind(seed)
    run_game(replace(script, loops=loops),
             {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return hp.b10, hp.b10_probs, hp.hits


def b10(days: int = 3, loops: int = 8, start: int = 0,
        end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    probs: list = []
    hits: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        cc, pp, hh = b10_game(sc, seed, loops=loops)
        c.update(cc)
        probs.extend(pp)
        for h in hh:
            hits.append({"script": name, "seed": seed, **h})
    return {"days": days, "counts": dict(c), "n_probs": len(probs),
            "prob_min": (min(probs) if probs else None),
            "prob_max": (max(probs) if probs else None),
            "hits": hits[:40]}


# ---------------------------------------------------------------------------
def _switches(days: int) -> str:
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    import agents.defense_plan as DP
    return (f"B160_INCIDENT_SUPPLY={getattr(DP, 'B160_INCIDENT_SUPPLY', '（未実装）')} / "
            f"DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER} / "
            f"B159_MISSING_BOARD={getattr(DP, 'B159_MISSING_BOARD', '（未実装）')} / "
            f"B100_MIX={HP.B100_MIX} / B100_THETA={HP.B100_THETA} / "
            f"B100_IRON_PROB={HP.B100_IRON_PROB} / "
            f"B141B_UNLOCK_SAME_DAY={HP.B141B_UNLOCK_SAME_DAY} / days={days}")


def _verify_game(script, seed: int, loops: int = 8) -> bool:
    probe = replace(script, loops=loops)
    # ★3席は**同一インスタンス**（席間協調＝既存の作法。別インスタンスにすると挙動が変わる）
    base = HeuristicProtagonist(seed)
    s0, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": base, "p2": base, "p3": base})
    hp, mm = _SeatProbe(seed), _MMProbe(seed)
    s1, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    hp2 = _B10Probe(seed)
    s2, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": hp2, "p2": hp2, "p3": hp2})
    k = [[(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
          for e in s.history] for s in (s0, s1, s2)]
    return (k[0] == k[1] == k[2] and _outcome(s0) == _outcome(s1) == _outcome(s2)
            and s0.loop_no == s1.loop_no == s2.loop_no)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-160 Phase 1 計測")
    ap.add_argument("cmd", choices=("strat", "verify", "b10", "flip"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    from arena.benchmark import benchmark_scripts

    print(f"[切替口] {_switches(a.days)}")
    if a.cmd == "verify":
        bad = n = 0
        for name, seed, sc in list(benchmark_scripts(days=a.days))[a.start:a.end]:
            n += 1
            if not _verify_game(sc, seed, loops=a.loops):
                bad += 1
                print(f"  ★不一致: {name} s{seed}")
        print(f"棋譜の不一致 = {bad} 件 / {n}局")
        return 1 if bad else 0

    if a.cmd == "flip":
        r = flip(days=a.days, loops=a.loops, start=a.start, end=a.end)
        print(f"=== B-160 Phase 1-4：素朴な述語の精度（{a.days}日級）===")
        for k, v in r["counts"].items():
            print(f"  {k:<58} {v}")
        print("\n--- ★誤った抑止の現物（最大60件） ---")
        for e in r["examples"]:
            print(f"    {e['script']} s{e['seed']} L{e['loop']}D{e['day']} "
                  f"板={e['board']} gap {e['gap_old']}→{e['gap_new']} "
                  f"（事件寄与{e['inc']}）真板={e['true_board']} "
                  f"実際の止まらない供給={e['unstop_expost']} 敗北={e['lost']}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    if a.cmd == "b10":
        r = b10(days=a.days, loops=a.loops, start=a.start, end=a.end)
        print(f"=== B-160 §6：B-10（邪気の汚染の犯人冷却）は決定へ届くか（{a.days}日級）===")
        for k, v in sorted(r["counts"].items()):
            print(f"  {k:<56} {v}")
        print(f"  board_defeat 実在度の観測 n={r['n_probs']} "
              f"min={r['prob_min']} max={r['prob_max']}")
        for h in r["hits"]:
            print(f"    {h['script']} s{h['seed']} L{h['loop']}D{h['day']} "
                  f"{h['card']}→{h['target']}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    r = strat(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(f"=== B-160 Phase 1：丸損席の層別（{a.days}日級 {r['n_games']}局・"
          f"独立脚本{r['n_scripts']}本）===")
    print(f"脚本＝{', '.join(r['scripts'])}")
    for k, v in r["counts"].items():
        print(f"  {k:<58} {v}")
    print("\n--- 層B／層C の現物（最大60件） ---")
    for e in r["examples"]:
        print(f"    {e['script']} s{e['seed']} L{e['loop']} 板={e['board']} "
              f"禁止{e['seats']}枚 開始{e['start']} 行動{e['action']} "
              f"噂{e['rumor']} クロ{e['kuromaku']} 行方不明{e['missing']} "
              f"邪気{e['jaki']} 他事件{e['inc_other']} "
              f"／敗北={e['lost']} known_p={e['known_p']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
