# -*- coding: utf-8 -*-
"""B-159 Phase 1：**行方不明の脅威 builder の射程を数える**（★計測のみ・`agents/` 非接触）。

## 発端

`docs/監査_B157_事件の値踏みの穴_2026-08-04.md` §3-2＝**主人公プランナーの脅威名指し率が
「行方不明」で 0.0%**（母数 3日級633／5日級519）。同 §2-2＝5日級の発生28回のうち
**12回が真の敗北板の暗躍を2へ到達**させ、**うち11回はループ終了時ちょうど2**（1つ減れば敗北しなかった）。

KB＝`rules/40_first_steps.md:153`「犯人を任意のボードに移動させる。その後、**犯人のいるボードに
暗躍カウンターを1つ置く**」＋公式裁定 E-2（同 `:160`＝移動先に犯人の禁止エリアは選べない）。

## 本計測が出す数

**B-142 と同じ3層の数え上げ**（`docs/監査_B142_1L冷却札の予約_2026-08-03.md` §2a）：

- **L1＝射程候補**（主人公の**公開情報＋自分の belief だけ**で判定・神視点を使わない）
  ＝`set_card` の席で、
  (a) 主人公が敗北板と見ている板 `b`（`_defeat_board_probs[b] >= 0.05`＝
      `_threat_board_defeat` の足切りと同じ）の**暗躍が現在ちょうど1**、
  (b) **今日以降**に未発生の事件「行方不明」が予定されている日 `d` がある、
  (c) その日の**犯人候補の誰か**が `b` へ移動できる（＝E-2 を通した供給到達。
      単一ソース＝`sim.state.missing_incident_boards_from_view`。候補が空＝絞れない＝
      **到達扱い＝安全側**）。
  ∴ **1つ足されると2＝板の敗北条件が成立する席**。
- **L2＝真の射程**（棋譜で裏取り）＝L1 のうち、`b` が**真の敗北板**（脚本家view専用の
  `rule_y` / `rule_y_board_x`＝計測器の中だけで使う）で、かつ**その日の行方不明が実際に発生し
  `b` へ暗躍を置いて 2 以上に到達**した席。
- **L3＝確実な損**＝L2 のうち、**そのループを実際に落とし**、敗北理由に `b` が挙がっている席。

さらに **L1〜L3 のそれぞれで「主人公がその席で実際に打てた折り手があったか」** を数える
（チケット §3-3＝「主人公が実際に打てた折り手があった席」）：

- **折り手A＝犯人冷却**（`不安-1`）。KB の発生条件＝`rules/00_rules_core.md:119-121`
  （犯人生存＋犯人の不安が臨界以上）。★**暗躍禁止は折り手にならない**＝
  `rules/10_action_cards.md:65`「暗躍禁止は**行動解決フェイズでのみ**有効」／事件は
  フェイズ7＝**事件効果の暗躍は暗躍禁止で止まらない**（→ `rules/40:62` と同じ理屈）。
- **折り手B＝板の暗躍を先に剥がす**＝巫女（神社・友好3）／神格（自ボード・友好5）のみ
  （`rules/20_goodwill_abilities.md:40,61`）。カードには板の暗躍除去が無い
  （`rules/00_rules_core.md:89` の手札8枚）。**本計測では在庫の有無だけ数える**。

## 因果の限界（★先に書く）

1. **相関の観測であって因果の証明ではない**（B-157 §6-1 と同じ）。1手変えれば以降の全系列が
   変わる＝真の反実仮想は取れない。L3 は「**上限の目安**」。
2. **O（射程）＝脚本家AIの取り逃し**は B-157 が数えた別の量。**本レーンは主人公側**＝
   混同しない（チケット §3-3）。
3. L1 の (c) は候補が空のとき**到達扱い**＝**L1 を過大に見積もる側**（安全側）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b159_audit verify --days 3 --end 12   # 挙動不変の自己検査
    python -m arena.b159_audit count  --days 3
    python -m arena.b159_audit count  --days 5
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
# ★ground truth の取り出しは **B-157 の実装をそのまま再利用**（二重実装しない）。
#   `_lost_loops_exact`／`_defeat_reasons`／`_loop_end_boards`／`_pub_incident_phase`／
#   `_parse_effect`／`_snap` は B-157 Phase 1 で自己検査（パーサ失敗0件）済み。
from arena.b157_audit import (_defeat_reasons, _loop_end_boards,
                              _lost_loops_exact, _parse_effect,
                              _pub_incident_phase, _snap)
from engine.data import unrest_threshold_of
from sim import run_game
from sim.state import missing_incident_boards_from_view

#: `_threat_board_defeat` が板を脅威化する足切り（`agents/defense_plan.py:1157`）。
RULE_P_MIN = 0.05

_AREAS = ("病院", "神社", "都市", "学校")


# ---------------------------------------------------------------------------
# ground truth（棋譜そのもの。推定を混ぜない）＝B-157 の実装を再利用し、
# 本レーン固有の「行方不明が実際に置いた板」だけを `_parse_effect` から作る。
# ---------------------------------------------------------------------------
def _missing_firings(state) -> dict:
    """{(loop, day): 置いた板}＝行方不明が実際に暗躍1を置いた先（公開情報）。"""
    out: dict = {}
    for (lp, dy), evs in _pub_incident_phase(state).items():
        head = next((e for e in evs if e.get("event") == "incident"), None)
        if head is None or not head.get("occurs") or head.get("name") != "行方不明":
            continue
        out[(lp, dy)] = _parse_effect("行方不明", evs).get("moved_to")
    return out


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝`super()` の戻り値をそのまま返す・B-151/_Probe と同じ作法）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """`enumerate_threats` の**本番の引数と返り値**を控え、公開情報だけで L1 を作る。"""

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
            cap["threats"] = list(r)
            return r

        dp.enumerate_threats = _rec
        try:
            chosen = super().decide(view, decision, options)
        finally:
            dp.enumerate_threats = orig
        if "kw" in cap:
            try:
                row = self._observe(view, cap["kw"], cap["threats"] or [], options)
                if row["hits"]:
                    self.seats.append(row)
            except Exception as e:                       # noqa: BLE001
                self.seats.append({"loop": view.get("loop"), "day": view.get("day"),
                                   "error": repr(e), "hits": []})
        return chosen

    # -- 観測（★公開情報＋自分の belief だけ・神視点なし） -------------------
    def _observe(self, view: dict, kw: dict, threats: list,
                 options: list[dict]) -> dict:
        chars = {c.get("name"): c for c in (view.get("characters") or [])}
        d0 = int(view.get("day", 1) or 1)
        banr = view.get("board_anyaku") or {}
        probs = kw.get("defeat_board_probs") or {}
        cul = kw.get("culprits") or {}
        crit = kw.get("criticals") or {}
        opt_keys = {(o.get("card"), o.get("target"), o.get("target_kind"))
                    for o in (options or [])}
        # このループで既に発生した事件日（公開履歴）＝未発生の行方不明だけを見る
        fired = {e.get("day") for e in (view.get("history") or [])
                 if e.get("event") == "incident" and e.get("occurs")
                 and e.get("loop") == view.get("loop")}
        missings = [i for i in (view.get("incidents") or [])
                    if i.get("name") == "行方不明" and i.get("day") is not None
                    and i.get("day") >= d0 and i.get("day") not in fired]
        # board_defeat 脅威の被覆状況（本番の返り値から読む＝二重実装しない）
        bd_breaks: dict = {}
        for t in threats:
            if getattr(t, "kind", "") != "board_defeat":
                continue
            for a in _AREAS:
                if a in str(getattr(t, "label", "")):
                    bd_breaks[a] = sum(len(c.breaks or ())
                                       for c in (t.conditions or ()))
        hits: list = []
        for b, rp in probs.items():
            if rp < RULE_P_MIN or int(banr.get(b, 0) or 0) != 1:
                continue
            for inc in missings:
                dy = inc.get("day")
                cands = sorted(n for n in (cul.get(dy) or ())
                               if chars.get(n, {}).get("alive", True))
                if cands:
                    feed: set = set()
                    for cn in cands:
                        feed |= set(missing_incident_boards_from_view(view, cn))
                    reach = b in feed
                else:
                    reach = True          # 絞れない＝到達扱い（安全側＝L1を過大に）
                if not reach:
                    continue
                # ---- 折り手A＝犯人冷却の在庫と有効性 -----------------------
                cool_have = [n for n in cands
                             if ("不安-1", n, "character") in opt_keys]
                # 「冷やせば発生を止められる」候補＝不安-1 で臨界を割れる
                #   （臨界0＝黒猫は割れない＝B-151 の知見）
                breakable = []
                for n in cands:
                    th = crit.get(n)
                    if th is None:
                        th = unrest_threshold_of(n)
                    u = int((chars.get(n) or {}).get("unrest", 0) or 0)
                    if th is not None and th >= 1 and u <= th:
                        breakable.append(n)
                hits.append({
                    "board": b, "rule_p": round(float(rp), 4),
                    "inc_day": dy, "n_cands": len(cands), "cands": cands,
                    # ★現行 `_cooling_breaks`（`agents/defense_plan.py:1119-1121`）は
                    #   「生存候補がちょうど1人」しか折り手にしない
                    "single_cand": len(cands) == 1,
                    "cool_have": cool_have,
                    "cool_all_have": bool(cands) and len(cool_have) == len(cands),
                    "breakable": breakable,
                    "all_breakable": bool(cands) and len(breakable) == len(cands),
                    "bd_breaks": bd_breaks.get(b, 0),
                })
        return {"loop": view.get("loop"), "day": d0, "seat": view.get("seat"),
                "hits": hits}


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Probe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    lost = _lost_loops_exact(state)
    reasons = _defeat_reasons(state)
    firings = _missing_firings(state)
    end_boards = _loop_end_boards(state)
    snaps = _snap_index(state)

    rows: list = []
    for row in hp.seats:
        lp = row.get("loop")
        strict, _wide = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        for h in row.get("hits", ()):
            b, dy = h["board"], h["inc_day"]
            # L2＝真の敗北板 × その日の行方不明が実際に b へ置いて2以上に到達
            delivered = firings.get((lp, dy)) == b
            after = None
            if delivered:
                sn = _snap(snaps, lp, dy, "事件フェイズ後") or {}
                after = int(((sn.get("board_anyaku") or {}).get(b, 0)) or 0)
            l2 = bool(b in strict and delivered and (after or 0) >= 2)
            fin = int((end_boards.get(lp) or {}).get(b, 0) or 0)
            l3 = bool(l2 and lp in lost and any(b in x for x in (reasons.get(lp) or ())))
            rows.append({**h, "loop": lp, "day": row["day"], "seat": row.get("seat"),
                         "true_board": b in strict, "delivered": delivered,
                         "after": after, "end_anyaku": fin,
                         "L2": l2, "L3": l3, "loop_lost": lp in lost})
    return {"outcome": _outcome(state), "rows": rows, "rule_y": mm.rule_y,
            "n_loops": state.loop_no, "lost_loops": sorted(lost)}


# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    scripts: set = set()
    games: set = set()
    ex: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts.add(name)
        games.add((name, seed))
        res = audit_game(sc, seed, loops=loops)
        for r in res["rows"]:
            c["L1 射程候補（席×板×事件日）"] += 1
            c["　├ 候補がちょうど1人（現行 _cooling_breaks が要求）"] += int(r["single_cand"])
            c["　├ 候補全員に 不安-1 の在庫がある"] += int(r["cool_all_have"])
            c["　├ 候補全員が冷却で臨界を割れる（黒猫等を除く）"] += int(r["all_breakable"])
            c["　├ ★折り手Aが今この席で成立（在庫×有効×1人）"] += int(
                r["single_cand"] and r["cool_all_have"] and r["all_breakable"])
            c["　├ ★折り手Aが成立（在庫×有効・人数不問）"] += int(
                r["cool_all_have"] and r["all_breakable"])
            c["　└ その板の board_defeat 脅威に折り手が0本"] += int(r["bd_breaks"] == 0)
            if r["true_board"]:
                c["L1 のうち b が真の敗北板"] += 1
            if r["delivered"]:
                c["L1 のうち行方不明が実際に b へ置いた"] += 1
            # ★キーは層ごとに接頭辞（L2:／L3:）を付ける＝**Counter のキー衝突を防ぐ**
            #   （初版は L2 と L3 で同一文字列を使っており合算されていた＝集計の誤読の元）。
            if r["L2"]:
                c["L2 真の射程（真の敗北板へ実際に届いて2到達）"] += 1
                c["　L2├ うち候補がちょうど1人"] += int(r["single_cand"])
                c["　L2├ ★うち折り手Aが成立（在庫×有効・人数不問）"] += int(
                    r["cool_all_have"] and r["all_breakable"])
                c["　L2└ ★うち折り手Aが成立（現行の1人制約つき）"] += int(
                    r["single_cand"] and r["cool_all_have"] and r["all_breakable"])
            if r["L3"]:
                c["L3 確実な損（そのループを実際に落とした）"] += 1
                c["　L3├ ★うち折り手Aが成立（在庫×有効・人数不問）"] += int(
                    r["cool_all_have"] and r["all_breakable"])
                c["　L3├ ★うち折り手Aが成立（現行の1人制約つき）"] += int(
                    r["single_cand"] and r["cool_all_have"] and r["all_breakable"])
                c["　L3└ ループ終了時の板暗躍がちょうど2（1つ減れば不成立）"] += int(
                    r["end_anyaku"] == 2)
                if len(ex) < 60:
                    ex.append({"script": name, "seed": seed, **r})
        if verbose:
            print(f"  {name} s{seed}: L1={sum(1 for _ in res['rows'])}"
                  f" 敗北ループ{res['lost_loops']}", flush=True)
    return {"days": days, "n_games": len(games), "n_scripts": len(scripts),
            "scripts": sorted(scripts), "counts": dict(c), "examples": ex}


# ---------------------------------------------------------------------------
# ★追加計測（Phase 1b）：**暗躍禁止の無駄打ち**（行為の数え上げ＝規約 §11b の証拠1）
#
# 機序＝`rules/10_action_cards.md:65`「暗躍禁止は**行動解決フェイズでのみ**有効」。
# ∴ 板の暗躍のうち **脚本家能力フェイズ**（不穏な噂 `rules/40:61-62`／クロマク `rules/40:88`）と
#    **事件フェイズ**（行方不明 `rules/40:153`）で乗る分は**暗躍禁止では止まらない**。
# ∴ その2経路だけで既に閾値2へ届くループでは、その板へ置いた暗躍禁止は
#    **敗北を1つも防げていない**＝席の丸損。
# ---------------------------------------------------------------------------
def _board_supply_by_phase(state, area: str, loop: int) -> dict:
    """`loop` 中に `area` へ乗った暗躍を**フェイズ別**に合計する（公開ログ）。"""
    out = {"action_resolution": 0, "mastermind_ability": 0, "incident": 0,
           "other": 0}
    for e in state.history:
        if (e.get("loop") != loop or e.get("event") != "anyaku"
                or e.get("target") != area):
            continue
        d = int(e.get("delta", 0) or 0)
        if d <= 0:
            continue
        ph = e.get("phase")
        out[ph if ph in out else "other"] += d
    return out


def _kinshi_seats(state, area: str, loop: int) -> int:
    """`loop` 中に主人公が `area` へ置いた暗躍禁止の枚数（席数）。"""
    n = 0
    for e in state.history:
        if e.get("loop") != loop or e.get("event") != "cards_revealed":
            continue
        for p in (e.get("placements") or ()):
            if (p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止"
                    and p.get("target") == area and p.get("target_kind") == "board"):
                n += 1
    return n


def futile_game(script, seed: int, loops: int = 8) -> dict:
    """1局ぶんの「無駄打ちだった暗躍禁止」を数える（挙動不変＝素のAIで回す）。"""
    hp = HeuristicProtagonist(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(replace(script, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    lost = _lost_loops_exact(state)
    end_boards = _loop_end_boards(state)
    rows: list = []
    for lp in range(1, state.loop_no + 1):
        strict, _w = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        for b in sorted(strict):
            seats = _kinshi_seats(state, b, lp)
            if not seats:
                continue
            sup = _board_supply_by_phase(state, b, lp)
            unstop = sup["mastermind_ability"] + sup["incident"] + sup["other"]
            rows.append({"loop": lp, "board": b, "seats": seats,
                         "unstoppable": unstop, "incident": sup["incident"],
                         "ability": sup["mastermind_ability"],
                         "action": sup["action_resolution"],
                         "end": int((end_boards.get(lp) or {}).get(b, 0) or 0),
                         "lost": lp in lost})
    return rows


def futile(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    ex: list = []
    games: set = set()
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        games.add((name, seed))
        for r in futile_game(sc, seed, loops=loops):
            c["真の敗北板へ暗躍禁止を置いたループ×板"] += 1
            c["　└ そこで使った席（枚数）"] += r["seats"]
            if r["unstoppable"] >= 2:
                c["★止まらない供給だけで2に届いた（暗躍禁止では防げない）"] += 1
                c["　├ ★そこで丸損した席（枚数）"] += r["seats"]
                c["　├ うちそのループを実際に落とした"] += int(r["lost"])
                c["　└ うち行方不明の寄与が1以上"] += int(r["incident"] >= 1)
                if len(ex) < 30:
                    ex.append({"script": name, "seed": seed, **r})
    return {"days": days, "n_games": len(games), "counts": dict(c),
            "examples": ex}


# ---------------------------------------------------------------------------
# ★追加計測（Phase 1c）：**述語が狭すぎて取りこぼしていないか**
#
# L1 は「主人公の決定席の時点で板の暗躍が**ちょうど1**」を要求する（チケット指定の最狭）。
# ところが「0 → 行動解決で mm の暗躍カードが通って1 → 事件で2」という**同日合成**では、
# 決定席の時点では 0 なので L1 に入らない。その取りこぼしを数える。
# ---------------------------------------------------------------------------
def wide_game(script, seed: int, loops: int = 8) -> list:
    """行方不明が**真の敗北板を2以上へ到達させた**発生ごとに、
    その日の**主人公行動フェイズ時点**の板の暗躍を控える。"""
    hp = HeuristicProtagonist(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(replace(script, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    out: list = []
    for (lp, dy), b in _missing_firings(state).items():
        strict, _w = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        if b not in strict:
            continue
        post = _snap(snaps, lp, dy, "事件フェイズ後") or {}
        after = int(((post.get("board_anyaku") or {}).get(b, 0)) or 0)
        if after < 2:
            continue
        # 主人公が札を置く直前の盤面（フェイズ境界のスナップショット・公開情報）
        pre = (_snap(snaps, lp, dy, "主人公行動フェイズ前")
               or _snap(snaps, lp, dy, "ターン開始")
               or _snap(snaps, lp, dy - 1, "ターン終了フェイズ後") or {})
        before = int(((pre.get("board_anyaku") or {}).get(b, 0)) or 0)
        out.append({"loop": lp, "day": dy, "board": b,
                    "before_seat": before, "after": after,
                    "snap_ok": bool(pre)})
    return out


def wide(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    ex: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        for r in wide_game(sc, seed, loops=loops):
            c["★行方不明が真の敗北板を2以上へ到達させた発生"] += 1
            if not r["snap_ok"]:
                c["　（スナップショット取得不能＝判定不能）"] += 1
                continue
            key = ("　├ 決定席の時点で板の暗躍が1（＝L1 の述語が捕まえる）"
                   if r["before_seat"] == 1
                   else f"　├ 決定席の時点で板の暗躍が{r['before_seat']}"
                        "（★L1 の述語が取りこぼす）")
            c[key] += 1
            if len(ex) < 40:
                ex.append({"script": name, "seed": seed, **r})
    return {"days": days, "counts": dict(c), "examples": ex}


def _switches(days: int) -> str:
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    return (f"B141B_UNLOCK_SAME_DAY={HP.B141B_UNLOCK_SAME_DAY} / "
            f"B143_YIELD={HP.B143_YIELD} / B142_RESERVE={HP.B142_RESERVE} / "
            f"B100_MIX={HP.B100_MIX} / A78_FORBIDDEN_AWARE={HP.A78_FORBIDDEN_AWARE} / "
            f"B159_MISSING_BOARD={getattr(HP, 'B159_MISSING_BOARD', '（未実装）')} / "
            f"days={days}")


def report(res: dict, days: int) -> None:
    print(f"=== B-159 Phase 1：行方不明の射程（{days}日級 {res['n_games']}局・"
          f"独立脚本{res['n_scripts']}本）===")
    print(f"[切替口] {_switches(days)}")
    print(f"脚本＝{', '.join(res['scripts'])}")
    for k, v in res["counts"].items():
        print(f"  {k:<52} {v}")
    if res["examples"]:
        print("\n--- L3 の現物（最大60件） ---")
        for e in res["examples"][:60]:
            print(f"  {e['script']} s{e['seed']} L{e['loop']}D{e['day']}席{e['seat']} "
                  f"板={e['board']}(p={e['rule_p']}) 事件日={e['inc_day']} "
                  f"候補={e['cands']} 冷却在庫={e['cool_have']} "
                  f"割れる={e['breakable']} 終了時暗躍={e['end_anyaku']}")


# ---------------------------------------------------------------------------
def _verify_game(script, seed: int, loops: int = 8) -> tuple:
    """プローブ有無で棋譜が完全一致するか（挙動不変の物証）。"""
    probe = replace(script, loops=loops)
    base_p = HeuristicProtagonist(seed)
    base_m = HeuristicMastermind(seed)
    s0, _ = run_game(probe, {"mastermind": base_m, "p1": base_p,
                             "p2": base_p, "p3": base_p})
    res = audit_game(script, seed, loops=loops)
    k0 = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
          for e in s0.history]
    # 同一条件でもう一度プローブ付きで回して棋譜を取る
    hp = _Probe(seed)
    mm = _MMProbe(seed)
    s1, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    k1 = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
          for e in s1.history]
    ok = (k0 == k1 and _outcome(s0) == _outcome(s1)
          and s0.loop_no == s1.loop_no)
    return ok, res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-159 Phase 1 計測")
    ap.add_argument("cmd", choices=("count", "verify", "futile", "wide"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    from arena.benchmark import benchmark_scripts

    if a.cmd == "verify":
        print(f"[切替口] {_switches(a.days)}")
        bad = 0
        n = 0
        for name, seed, sc in list(benchmark_scripts(days=a.days))[a.start:a.end]:
            ok, _ = _verify_game(sc, seed, loops=a.loops)
            n += 1
            if not ok:
                bad += 1
                print(f"  ★不一致: {name} s{seed}")
        print(f"棋譜の不一致 = {bad} 件 / {n}局")
        return 1 if bad else 0

    if a.cmd == "wide":
        print(f"[切替口] {_switches(a.days)}")
        r = wide(days=a.days, loops=a.loops, start=a.start, end=a.end)
        print(f"=== B-159 Phase 1c：述語の取りこぼし（{a.days}日級）===")
        for k, v in r["counts"].items():
            print(f"  {k:<58} {v}")
        for e in r["examples"]:
            print(f"    {e['script']} s{e['seed']} L{e['loop']}D{e['day']} "
                  f"板={e['board']} 決定席={e['before_seat']} → 事件後={e['after']}")
        return 0

    if a.cmd == "futile":
        print(f"[切替口] {_switches(a.days)}")
        r = futile(days=a.days, loops=a.loops, start=a.start, end=a.end)
        print(f"=== B-159 Phase 1b：暗躍禁止の無駄打ち（{a.days}日級 {r['n_games']}局）===")
        for k, v in r["counts"].items():
            print(f"  {k:<52} {v}")
        for e in r["examples"]:
            print(f"    {e['script']} s{e['seed']} L{e['loop']} 板={e['board']} "
                  f"暗躍禁止{e['seats']}枚 ／止まらない供給={e['unstoppable']}"
                  f"（能力{e['ability']}＋事件{e['incident']}）"
                  f"／行動解決で通った分={e['action']} 終了時={e['end']} "
                  f"敗北={e['lost']}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0

    res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
              verbose=a.verbose)
    report(res, a.days)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
