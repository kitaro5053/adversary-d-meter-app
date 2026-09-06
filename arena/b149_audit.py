# -*- coding: utf-8 -*-
"""B-149：**防御3枠の予算配分**（`plan_defenses` の greedy）を数える（★計測のみ・挙動不変）。

## 発端

`docs/監査_B148_引き込み脅威のKP限定_2026-08-04.md` §4-1 の機序＝
`agents/defense_plan.plan_defenses`（`:2025-2079`）は **`seats=3` を severity 降順の greedy で
埋める**。B-148 は「脅威を1種類増やしただけで、prob 中央値 0.85 の新脅威が**上位3枠を先に食い**、
`board_defeat` と `remote_murder_vip` が落ちた」＝**明示的なトレード**だったと実測した。
∴ 問いは「脅威の見つけ方」ではなく「**3枠の配り方**」に移る。

## 測るもの（層・B-139b/B-142/B-148 の教訓＝上限値を射程と読まない）

- **L1**＝**予算制約が実際に効いている席**。2通りを別々に数える：
  - `L1full`＝3枠が**埋まりきった**席（`len(plan.picks) >= seats`）。
  - **`L1bind`＝実際に「予算のせいで」落ちた脅威がある席**
    ＝同じ `threats` を `seats=99` で引き直すと覆えるのに `seats=3` では覆えない脅威がある席。
    ★**再実装しない**＝`plan_defenses` を**そのまま2回**呼ぶ（純関数・rng 非消費）。
- **L2**＝L1bind のうち、**落ちた脅威が、その日に実際に発生した**席＝**取り逃し**。
- **L3**＝L2 のうち、**その日/そのループが実際に敗北で終わった**席＝**確実な損**。
- **★L4**＝逆向き＝**採用（covered）した脅威が空振りだった**席（＝枠を無駄に使った）。
  ★**L4 は交絡する**（覆ったから起きなかったのか、元々起きなかったのか）。§報告で明記する。

## 「発生した」の判定（ground truth・棋譜と secret_log から）

| Threat.kind | 発生とみなす事実（同一 (loop,day)） |
|---|---|
| `kp_killer` `factor_kp` `kp_sk` `virus_sk` `sk_setup` `incident_vip` `remote_murder_vip` | ラベル中のキャラが**その日に死亡**（`secret_log` の death） |
| `killer_protagonist` `mainlover_protagonist` `mainlover_chain` `hospital_protagonist` | **主人公死亡**（`protagonist_death`）がその日 |
| `board_defeat` | ラベル中の板の**暗躍がその日の終わりに ≥2** |
| `kp_anyaku` | ラベル中のキャラの**暗躍がその日の終わりに ≥2** |
| `tt_defeat` | その日に `loop_end`（TT の任意敗北を含むループ終了） |
| `butterfly` | **判定不能**＝`unknown` に計上（推定しない） |

★キャラ名の帰属は**ラベルへの文字列照合**（`arena/b145_audit.py` の `vic in t["label"]` と同じ作法）。
限界として報告に明記する。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b149_audit verify --days 3
    python -m arena.b149_audit count  --days 3 --json d3.json
    python -m arena.b149_audit count  --days 5 --json d5.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _lost_loops, _outcome, _snap_index
from arena.b146_probe import _true_boards
from engine.data import CHARACTER_FORBIDDEN
from sim import run_game

#: `plan_defenses` を「予算無限」で引き直す時の席数（純関数の再評価・挙動不変）。
_BIG_SEATS = 99

#: 本番の席数（`plan_for_belief` の既定＝`agents/defense_plan.py:2025`）。
_PROD_SEATS = 3

_AREAS = ("病院", "神社", "都市", "学校")
_NAMES = tuple(sorted(CHARACTER_FORBIDDEN, key=len, reverse=True))

#: 「その日に主人公（の陣営）が死んだ」で発生とみなす kind。
_PROT_KINDS = ("killer_protagonist", "mainlover_protagonist", "mainlover_chain",
               "hospital_protagonist")
#: 「ラベル中のキャラがその日に死んだ」で発生とみなす kind。
_DEATH_KINDS = ("kp_killer", "factor_kp", "kp_sk", "virus_sk", "sk_setup",
                "incident_vip", "remote_murder_vip")


def _names_in(label: str) -> list[str]:
    """ラベルに現れるキャラ名（最長一致優先＝アルバイト/アルバイト？の包含を解く）。"""
    out: list[str] = []
    rest = str(label)
    for n in _NAMES:
        if n in rest:
            out.append(n)
            rest = rest.replace(n, "・")
    return out


def _areas_in(label: str) -> list[str]:
    return [a for a in _AREAS if a in str(label)]


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝super() の戻り値の後で純関数を再評価するだけ）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """席ごとに `plan_defenses` の入力（脅威＋severity）と出力（3枠の割当）を控える。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.seats: list[dict] = []
        self._k_at_recs = False     # 本番が `_defense_plan_recs` を呼んだ時点の暗躍禁止フラグ
        self._in_shadow = False

    def _defense_plan_recs(self, view: dict, options: list) -> dict:
        # ★`_kinshi_used` は「この席で暗躍禁止を選んだか」で decide の中で立ち、
        #   ターンが変わると decide の中で戻る＝**decide の入口の値では再現できない**。
        #   本番が呼んだ瞬間の値を控え、シャドーではそれを使う（偽の差分を作らない）。
        if not self._in_shadow:
            self._k_at_recs = self._kinshi_used
        return super()._defense_plan_recs(view, options)

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision != "set_card":
            return chosen
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            return chosen
        import agents.defense_plan as dp

        threats, plan = stash
        row = {"loop": view.get("loop"), "day": view.get("day"),
               "seat": view.get("seat"),
               "chosen": (chosen.get("card"), chosen.get("target"),
                          chosen.get("target_kind"))}
        try:
            # ★予算だけを外して引き直す（同じ threats・同じ関数・純関数＝rng 非消費）。
            plan_inf = dp.plan_defenses(threats, seats=_BIG_SEATS)
        except Exception as e:      # noqa: BLE001
            row["error"] = repr(e)
            self.seats.append(row)
            return chosen
        cov3 = getattr(plan, "covered", {}) or {}
        covI = getattr(plan_inf, "covered", {}) or {}
        picks = [(b.card, b.target, b.target_kind, round(float(b.cost), 2))
                 for b in plan.picks]
        ts: list[dict] = []
        for t in threats:
            ts.append({
                "kind": getattr(t, "kind", None),
                "label": str(getattr(t, "label", "")),
                "prob": round(float(getattr(t, "prob", 0.0) or 0.0), 4),
                "sev": round(float(getattr(t, "severity", 0.0) or 0.0), 4),
                "fatal": bool(getattr(t, "fatal", False)),
                "breached": bool(getattr(t, "breached", False)),
                "defendable": bool(getattr(t, "defendable", False)),
                "cov": id(t) in cov3,
                "cov_inf": id(t) in covI,
                "how": str(cov3.get(id(t), "")),
                # ★席を1枚消費したか（`（既存手で兼ねる）`＝追加コスト0＝予算を食わない）
                "spent": (id(t) in cov3
                          and not str(cov3.get(id(t), "")).endswith(
                              "（既存手で兼ねる）")),
                # ★この脅威を折れる手のカード種（＝加点対象になりうるか。既定の
                #   `_plan_coeffs` は **移動禁止 のみ**に係数を返す＝
                #   `agents/heuristic_protagonist.py:636-641`）。
                "bcards": sorted({b.card for b in t.cheapest_breaks()}),
            })
        # ★予算が**採点に届くか**＝`recs`（加点表）が seats=3 と seats=∞ で違うか。
        #   `_defense_plan_recs` を本人の関数のまま2回目に呼び、`plan_defenses` だけを
        #   「予算無限」に差し替える（B-148 のシャドーと同じ作法＝純関数の再評価・
        #   `_b100_plan` / `_kinshi_used` は退避して必ず戻す）。
        prod = dict(getattr(self, "_plan_recs", {}) or {})
        _save = (getattr(self, "_b100_plan", None), self._kinshi_used)
        _orig_pd = dp.plan_defenses

        def _pd_big(threats, *, seats=_PROD_SEATS, card_turn_caps=None):
            return _orig_pd(threats, seats=_BIG_SEATS,
                            card_turn_caps=card_turn_caps)

        def _pd_rev(threats, *, seats=_PROD_SEATS, card_turn_caps=None):
            # ★「並べ方を変えたら採点が動くか」の**極端な**対照＝severity 昇順。
            #   予算（3枠）はそのまま＝順序だけを入れ替える。
            return _orig_pd(list(reversed(list(threats))), seats=seats,
                            card_turn_caps=card_turn_caps)

        try:
            self._in_shadow = True
            self._kinshi_used = self._k_at_recs
            recs0 = dict(self._defense_plan_recs(view, options))
            dp.plan_defenses = _pd_big
            recs_inf = dict(self._defense_plan_recs(view, options))
            dp.plan_defenses = _pd_rev
            recs_rev = dict(self._defense_plan_recs(view, options))
        except Exception as e:      # noqa: BLE001
            row["recs_error"] = repr(e)
            recs0 = dict(prod)
            recs_inf = dict(prod)
            recs_rev = dict(prod)
        finally:
            dp.plan_defenses = _orig_pd
            self._in_shadow = False
            self._b100_plan, self._kinshi_used = _save
        row.update({
            "n_recs": len(recs0), "n_recs_inf": len(recs_inf),
            # ★自己検査＝シャドーの seats=3 版が本番の `_plan_recs` を再現するか
            "recs_repro": recs0 == prod,
            "recs_changed": recs0 != recs_inf,
            "recs_rev_changed": recs0 != recs_rev,
            "n_threats": len(ts), "threats": ts,
            "picks": picks, "n_picks": len(picks),
            "full": len(picks) >= _PROD_SEATS,
            "n_starved": sum(1 for t in ts if t["cov_inf"] and not t["cov"]),
            "picks_inf": len(getattr(plan_inf, "picks", []) or []),
        })
        self.seats.append(row)
        return chosen


class _MMProbe(HeuristicMastermind):
    """★真の敗北板を控えるだけの脚本家プローブ（`super()` の戻り値に触れない）。

    `rule_y_board_x`（復讐者の灯火＝クロマク初期／巨大時限爆弾X＝ウィッチ初期）は
    **脚本家ビュー専用の秘匿情報**（`sim/views.py:94`）＝**計測器の中だけ**で使い、
    主人公AIの判断経路には一切入れない（`arena/b146_probe.py` と同じ作法）。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.rule_y = None
        self.board_x_by_loop: dict = {}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        self.rule_y = view.get("rule_y") or self.rule_y
        self.board_x_by_loop[view.get("loop")] = view.get("rule_y_board_x")
        return chosen


# ---------------------------------------------------------------------------
# ground truth（棋譜＋スナップショット。推定を混ぜない）
# ---------------------------------------------------------------------------
def _deaths_by_day(state) -> dict:
    out: dict = {}
    for e in state.secret_log:
        if e.get("event") == "death":
            out.setdefault((e.get("loop"), e.get("day")), set()).add(e.get("name"))
    return out


def _prot_deaths(state) -> set:
    return {(e.get("loop"), e.get("day")) for e in state.secret_log
            if e.get("event") == "protagonist_death"}


def _loop_end_days(state) -> set:
    return {(e.get("loop"), e.get("day")) for e in state.history
            if e.get("event") == "loop_end"}


def _day_end_snap(snaps: dict) -> dict:
    """(loop, day) -> その日の最後のスナップショット（暗躍/不安の日終値）。"""
    order = ["ターン終了フェイズ後", "事件フェイズ後", "主人公能力フェイズ後",
             "脚本家能力フェイズ後", "行動解決フェイズ後"]
    out: dict = {}
    for (lp, dy, pt), s in snaps.items():
        cur = out.get((lp, dy))
        rank = order.index(pt) if pt in order else 99
        if cur is None or rank < cur[0]:
            out[(lp, dy)] = (rank, s)
    return {k: v[1] for k, v in out.items()}


def _realized(kind: str, label: str, key, deaths, protd, ends, dayend,
              strict_boards=None) -> bool | None:
    """その脅威が主張する事象が、その (loop, day) に実際に起きたか。None＝判定不能。

    ★`board_defeat` は **strict_boards（＝そのループの真の敗北板）に入っている板**が
      その日の終わりに暗躍≥2 になった時だけ「発生」とする。
      真の敗北板でない板が2に届いても**ループ敗北にはならない**＝発生ではない
      （`strict_boards=None` を渡すと旧来の緩い判定＝「どの板でも2に届けば発生」）。
    """
    if kind in _DEATH_KINDS:
        ns = _names_in(label)
        if not ns:
            return None
        d = deaths.get(key, set())
        return any(n in d for n in ns)
    if kind in _PROT_KINDS:
        return key in protd
    if kind == "board_defeat":
        ar = _areas_in(label)
        s = dayend.get(key)
        if not ar or s is None:
            return None
        if strict_boards is not None:
            ar = [a for a in ar if a in strict_boards]
            if not ar:
                return False      # 真の敗北板ではない＝この負け筋は起きえない
        ba = s.get("board_anyaku") or {}
        return any(int(ba.get(a, 0)) >= 2 for a in ar)
    if kind == "kp_anyaku":
        ns = _names_in(label)
        s = dayend.get(key)
        if not ns or s is None:
            return None
        ch = s.get("characters") or {}
        return any(int((ch.get(n) or {}).get("anyaku", 0)) >= 2 for n in ns)
    if kind == "tt_defeat":
        return key in ends
    return None


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Probe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(probe, {"mastermind": mm,
                                "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    dayend = _day_end_snap(snaps)
    deaths = _deaths_by_day(state)
    protd = _prot_deaths(state)
    ends = _loop_end_days(state)
    lost = _lost_loops(state)
    for row in hp.seats:
        key = (row.get("loop"), row.get("day"))
        row["loop_lost"] = row.get("loop") in lost
        strict, _wide = _true_boards(mm.rule_y,
                                     mm.board_x_by_loop.get(row.get("loop")),
                                     False)
        row["true_boards"] = sorted(strict)
        for t in row.get("threats", ()):
            t["real"] = _realized(t["kind"], t["label"], key,
                                  deaths, protd, ends, dayend,
                                  strict_boards=strict)
            # ★旧来の緩い判定（どの板でも暗躍2で「発生」）＝比較用に残す
            t["real_loose"] = _realized(t["kind"], t["label"], key,
                                        deaths, protd, ends, dayend)
    # ★行為の数え上げ＝自チームが**同じ対象に『移動禁止』と『移動』を実際に置いた**ターン
    #   （`rules/10_action_cards.md:20,60`＝移動禁止は重なった移動カードを無効化する
    #   ＝どちらかは必ず空打ちになる）。計画の重複割当が**盤上で実現したか**を数える。
    by_turn: dict = {}
    for row in hp.seats:
        c, t, k = row.get("chosen") or (None, None, None)
        if k == "character":
            by_turn.setdefault((row.get("loop"), row.get("day")), []).append((c, t))
    played_conflicts = []
    for key, plays in sorted(by_turn.items()):
        pins = {t for c, t in plays if c == "移動禁止"}
        movs = {t for c, t in plays if c in ("移動←→", "移動↑↓")}
        both = pins & movs
        if both:
            played_conflicts.append((key[0], key[1], sorted(both)))
    return {"outcome": _outcome(state), "seats": hp.seats,
            "played_conflicts": played_conflicts,
            "n_turns": len(by_turn),
            "lost_loops": sorted(lost)}


def conflict_count(days: int = 3, loops: int = 8) -> dict:
    """★行為の数え上げ（素の対局・プローブなし）＝自チームが**同じキャラに
    『移動禁止』と『移動』を同時に置いた**ターンを数える。

    `rules/10_action_cards.md:20,60`＝移動禁止は**重なった移動カードを無効化**する
    ＝この2枚が同じ対象に乗ったターンは、**必ずどちらかが空打ち**になる。
    計画（`plan_defenses`）は (card,target,kind) が違えば別手として3枠に並べるので、
    この矛盾を構造的に見ていない（`agents/defense_plan.py:2060-2079`）。
    """
    from arena.benchmark import benchmark_scripts

    c = Counter()
    rows: list[dict] = []
    for name, seed, sc in list(benchmark_scripts(days=days)):
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        by_turn: dict = {}
        for e in state.history:
            if e.get("event") != "cards_revealed":
                continue
            for pl in e.get("placements", []) or []:
                if pl.get("owner") == "mastermind":
                    continue
                if pl.get("target_kind") != "character":
                    continue
                by_turn.setdefault((e.get("loop"), e.get("day")), []).append(
                    (pl.get("card"), pl.get("target")))
        for key, plays in sorted(by_turn.items()):
            c["turns"] += 1
            pins = {t for cd, t in plays if cd == "移動禁止"}
            movs = {t for cd, t in plays if cd in ("移動←→", "移動↑↓")}
            both = pins & movs
            if both:
                c["conflict_turns"] += 1
                c["conflict_targets"] += len(both)
                rows.append({"script": name, "seed": seed, "loop": key[0],
                             "day": key[1], "targets": sorted(both)})
        c["games"] += 1
    return {"counts": dict(c), "rows": rows}


def _verify_game(script, seed: int, loops: int = 8) -> tuple:
    """プローブ有無で結末が一致するか（挙動不変の物証）。"""
    probe = replace(script, loops=loops)
    a = _Probe(seed)
    sa, _ = run_game(probe, {"mastermind": _MMProbe(seed),
                             "p1": a, "p2": a, "p3": a})
    b = HeuristicProtagonist(seed)
    sb, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": b, "p2": b, "p3": b})
    ha = [(e.get("loop"), e.get("day"), e.get("event")) for e in sa.history]
    hb = [(e.get("loop"), e.get("day"), e.get("event")) for e in sb.history]
    return (sa.winner == sb.winner and sa.loop_no == sb.loop_no and ha == hb,
            _outcome(sa), _outcome(sb))


def _switches(days: int, loops: int) -> str:
    import agents.defense_plan as dp
    H = HeuristicProtagonist
    return (f"[切替口] B141B_UNLOCK_SAME_DAY={H.B141B_UNLOCK_SAME_DAY}"
            f" / B143_YIELD={H.B143_YIELD} / B142_RESERVE={H.B142_RESERVE}"
            f" / B100_MIX={H.B100_MIX}"
            f" / B145_EVADE_MAX_FRIENDS={H.B145_EVADE_MAX_FRIENDS}"
            f" / B146_ODB_TIEBREAK_BOARD_LOSS_ONLY="
            f"{getattr(H, 'B146_ODB_TIEBREAK_BOARD_LOSS_ONLY', '—')}"
            f" / B149_ORDER={getattr(H, 'B149_ORDER', '—')}"
            f" / B149_DUP_PENALTY={getattr(dp, 'B149_DUP_PENALTY', '—')}"
            f" / PLAN_SEATS={_PROD_SEATS} / _LIKELY_P={dp._LIKELY_P}"
            f" / days={days} loops={loops}")


def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    kind_ad = Counter()      # 採用（covered）された脅威の kind
    kind_dr = Counter()      # 予算で落ちた脅威の kind
    kind_ad_real = Counter()
    kind_dr_real = Counter()
    margins: list[float] = []
    sev_ad: list[float] = []
    sev_dr: list[float] = []
    l2_rows: list[dict] = []
    l4_rows: list[dict] = []
    per_game: list[dict] = []
    dup_seats = Counter()
    dup_cards = Counter()
    pick_cards = Counter()
    conflict_rows: list[dict] = []
    # ★較正＝severity が「その日に本当に起きるか」をどれだけ当てているか。
    #   `uncov`（＝1枚も割かれなかった脅威）は**防御による打ち消しが無い**＝交絡のない標本。
    calib = Counter()
    kind_un = Counter()
    kind_un_real = Counter()

    def _bucket(s: float) -> str:
        for hi in (0.05, 0.1, 0.2, 0.4, 0.7):
            if s < hi:
                return f"<{hi}"
        return ">=0.7"

    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = audit_game(sc, seed, loops=loops)
        g = Counter()
        g["games"] = 1
        g["turns"] = int(res.get("n_turns") or 0)
        g["played_conflict_turns"] = len(res.get("played_conflicts") or ())
        for pc in (res.get("played_conflicts") or ()):
            conflict_rows.append({"script": name, "seed": seed,
                                  "loop": pc[0], "day": pc[1], "targets": pc[2]})
        for row in res["seats"]:
            g["seats"] += 1
            ts = row.get("threats") or []
            if not ts:
                continue
            g["seats_with_threats"] += 1
            g["n_threats"] += len(ts)
            g["n_defendable"] += sum(1 for t in ts if t["defendable"])
            g["n_uncov"] += sum(1 for t in ts if not t["cov"])
            g[f"npicks_{row.get('n_picks')}"] += 1
            g[f"nthreats_{min(len(ts), 9)}"] += 1
            g["n_recs"] += int(row.get("n_recs") or 0)
            if row.get("n_recs"):
                g["seats_with_recs"] += 1
            if row.get("full"):
                g["L1full"] += 1
            if not row.get("recs_repro", True):
                g["recs_repro_fail"] += 1   # ★自己検査（0 でなければ計測器が疑わしい）
            if row.get("recs_changed"):
                g["L1eff"] += 1        # ★予算が採点（加点表）に届いた席
            if row.get("recs_rev_changed"):
                g["L1rev"] += 1        # ★並べ方（severity 昇順）が採点に届いた席
            starved = [t for t in ts if t["cov_inf"] and not t["cov"]]
            adopted = [t for t in ts if t["cov"]]
            spent = [t for t in ts if t["spent"]]
            g["n_adopted"] += len(adopted)
            g["n_spent"] += len(spent)
            g["n_reuse"] += len(adopted) - len(spent)
            for t in ts:
                st = "spent" if t["spent"] else ("reuse" if t["cov"] else "uncov")
                calib[(st, _bucket(t["sev"]), "n")] += 1
                if t["real"] is True:
                    calib[(st, _bucket(t["sev"]), "real")] += 1
                elif t["real"] is None:
                    calib[(st, _bucket(t["sev"]), "unk")] += 1
                if st == "uncov":
                    kind_un[t["kind"]] += 1
                    if t["real"] is True:
                        kind_un_real[t["kind"]] += 1
            for t in spent:      # ★kind 別の的中率は「席を消費した採用」で見る
                kind_ad[t["kind"]] += 1
                sev_ad.append(t["sev"])
                if t["real"] is True:
                    kind_ad_real[t["kind"]] += 1
            # ★L4＝**席を消費した**採用が空振り（その日に発生しなかった）
            whiff = [t for t in spent if t["real"] is False]
            hit = [t for t in spent if t["real"] is True]
            g["n_adopted_hit"] += len(hit)
            g["n_adopted_whiff"] += len(whiff)
            g["n_adopted_unknown"] += sum(1 for t in spent if t["real"] is None)
            if whiff:
                g["L4_seats"] += 1
                if len(hit) == 0:
                    g["L4_seats_allwhiff"] += 1
            for p in (row.get("picks") or ()):
                pick_cards[f"採用:{p[0]}"] += 1
            # ★同一対象への重複割当（同じキャラ/板に2枠以上）
            tg = Counter((p[1], p[2]) for p in (row.get("picks") or ()))
            if any(v >= 2 for v in tg.values()):
                g["dup_seats"] += 1
                for k, v in tg.items():
                    if v < 2:
                        continue
                    dup_seats[f"{k[0]}({k[1]})x{v}"] += 1
                    cards = sorted(p[0] for p in (row.get("picks") or ())
                                   if (p[1], p[2]) == k)
                    dup_cards["＋".join(cards)] += 1
                    # ★同一対象に「動かすな」と「動かせ」を同時に配っている席
                    if any(x == "移動禁止" for x in cards) and \
                            any(x.startswith("移動") and x != "移動禁止"
                                for x in cards):
                        g["dup_pin_and_move"] += 1
            if not starved:
                continue
            g["L1bind"] += 1
            g["n_starved"] += len(starved)
            # ★予算が AI の決定に届くか＝落ちた脅威が「加点対象カード（移動禁止）」で
            #   折れるのでなければ、覆っても `recs` は1件も増えない＝決定は不変。
            if any("移動禁止" in (t.get("bcards") or ()) for t in starved):
                g["L1bind_kinshi"] += 1
            if any(p[0] == "移動禁止" for p in (row.get("picks") or ())):
                g["L1bind_has_pin_pick"] += 1
            for t in starved:
                kind_dr[t["kind"]] += 1
                sev_dr.append(t["sev"])
                pick_cards[f"落選:{'/'.join(t.get('bcards') or ())}"] += 1
                if t["real"] is True:
                    kind_dr_real[t["kind"]] += 1
            # ★僅差か大差か＝「落ちた最上位」と「**席を消費した**採用の最下位」の severity 差
            #   （reuse は追加コスト0＝予算を食わないので比較対象にしない）
            if spent:
                mtop = max(t["sev"] for t in starved)
                mbot = min(t["sev"] for t in spent)
                margins.append(round(mtop - mbot, 4))
            miss_loose = [t for t in starved if t.get("real_loose") is True]
            if miss_loose:
                g["L2_seats_loose"] += 1
            miss = [t for t in starved if t["real"] is True]
            if miss and row.get("recs_changed"):
                g["L2_seats_eff"] += 1
            if miss:
                g["L2_seats"] += 1
                g["n_missed"] += len(miss)
                if row.get("loop_lost"):
                    g["L3_seats"] += 1
                l2_rows.append({"script": name, "seed": seed,
                                "loop": row["loop"], "day": row["day"],
                                "seat": row["seat"], "lost": row["loop_lost"],
                                "missed": [(t["kind"], t["label"], t["sev"])
                                           for t in miss],
                                "adopted": [(t["kind"], t["label"], t["sev"],
                                             t["real"]) for t in adopted],
                                "picks": row["picks"]})
            if whiff and miss:
                g["L4_and_L2_seats"] += 1
                l4_rows.append({"script": name, "seed": seed,
                                "loop": row["loop"], "day": row["day"],
                                "seat": row["seat"],
                                "whiff": [(t["kind"], t["sev"]) for t in whiff],
                                "missed": [(t["kind"], t["sev"]) for t in miss]})
        for k, v in g.items():
            c[k] += v
        per_game.append({"script": name, "seed": seed,
                         "outcome": res["outcome"], **dict(g)})
        if verbose:
            print(f"  {name} s{seed}: 席{g['seats']} L1full={g.get('L1full', 0)}"
                  f" L1bind={g.get('L1bind', 0)} L2={g.get('L2_seats', 0)}"
                  f" L3={g.get('L3_seats', 0)} L4={g.get('L4_seats', 0)}",
                  flush=True)
    return {"days": days, "counts": dict(c), "per_game": per_game,
            "kind_adopted": dict(kind_ad), "kind_dropped": dict(kind_dr),
            "kind_adopted_real": dict(kind_ad_real),
            "kind_dropped_real": dict(kind_dr_real),
            "margins": margins, "sev_adopted": sev_ad, "sev_dropped": sev_dr,
            "l2_rows": l2_rows, "l4_rows": l4_rows,
            "dup_targets": dict(dup_seats), "dup_cards": dict(dup_cards),
            "cards": dict(pick_cards),
            "calib": {f"{a}|{b}|{c}": v for (a, b, c), v in calib.items()},
            "kind_uncov": dict(kind_un), "kind_uncov_real": dict(kind_un_real),
            "conflict_rows": conflict_rows}


def _q(xs: list[float]) -> str:
    if not xs:
        return "—"
    ys = sorted(xs)
    n = len(ys)

    def p(f):
        return ys[min(n - 1, int(f * n))]
    return (f"n={n} min={ys[0]:.3f} p25={p(.25):.3f} med={p(.5):.3f}"
            f" p75={p(.75):.3f} max={ys[-1]:.3f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "conflict"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    print(_switches(a.days, a.loops), flush=True)
    if a.cmd == "conflict":
        r = conflict_count(days=a.days, loops=a.loops)
        cc = r["counts"]
        print(f"== B-149 行為の数え上げ（{a.days}日級 {cc.get('games', 0)}局）==")
        print(f"  自チームが同じキャラに『移動禁止』＋『移動』を置いたターン = "
              f"{cc.get('conflict_turns', 0)} / 全 {cc.get('turns', 0)} ターン"
              f"（対象 {cc.get('conflict_targets', 0)} 体）")
        for h in r["rows"][:a.top]:
            print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                  f" 対象={h['targets']}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
        return 0
    if a.cmd == "verify":
        from arena.benchmark import benchmark_scripts

        bad = 0
        for name, seed, sc in list(benchmark_scripts(days=a.days))[
                a.start:(a.end if a.end is not None else 12)]:
            ok, oa, ob = _verify_game(sc, seed, loops=a.loops)
            if not ok:
                bad += 1
                print(f"  ✗ {name} s{seed}: probe={oa} plain={ob}")
        print(f"不一致 = {bad} 件")
        return 1 if bad else 0
    res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
              verbose=a.verbose)
    c = res["counts"]
    print(f"== B-149：防御3枠の予算配分（{a.days}日級 {c.get('games', 0)}局・"
          f"set_card席 {c.get('seats', 0)}）==")
    print(f"  脅威が1件以上ある席 = {c.get('seats_with_threats', 0)}"
          f"（脅威 {c.get('n_threats', 0)} 件）")
    print(f"★L1full 3枠が埋まりきった席 = {c.get('L1full', 0)}")
    print(f"★L1bind 予算のせいで脅威が落ちた席 = {c.get('L1bind', 0)}"
          f"（落ちた脅威 {c.get('n_starved', 0)} 件）")
    print(f"   　うち落ちた脅威を『移動禁止』で折れた席 = "
          f"{c.get('L1bind_kinshi', 0)}"
          f"（＝加点 recs に届きうる席。他は覆っても決定不変）")
    print(f"   　うち計画に『移動禁止』の pick が在る席 = "
          f"{c.get('L1bind_has_pin_pick', 0)}")
    print(f"★L1eff 予算が**加点表 recs を実際に変えた**席 = {c.get('L1eff', 0)}"
          f"（recs が1件以上ある席 = {c.get('seats_with_recs', 0)}"
          f" / recs 総数 {c.get('n_recs', 0)}）")
    print(f"★L1rev **並べ方だけ**（severity 昇順＝極端な対照・予算3枠のまま）で"
          f"recs が変わる席 = {c.get('L1rev', 0)}")
    print(f"   　自己検査：シャドー(seats=3) が本番の recs を再現しなかった席 = "
          f"{c.get('recs_repro_fail', 0)}（0 が正常）")
    print(f"★L2 落ちた脅威がその日に実際に発生した席 = {c.get('L2_seats', 0)}"
          f"（{c.get('n_missed', 0)} 件・うち recs も変わる席 "
          f"{c.get('L2_seats_eff', 0)}）")
    print(f"   　（参考）旧来の緩い判定＝真の敗北板を問わない L2 = "
          f"{c.get('L2_seats_loose', 0)}")
    print(f"★L3 うちそのループが敗北で終わった席 = {c.get('L3_seats', 0)}")
    print(f"★L4 席を消費した採用が空振りだった席 = {c.get('L4_seats', 0)}"
          f"（うち採用が全部空振り = {c.get('L4_seats_allwhiff', 0)}）")
    print(f"★L4∧L2 同じ席で空振りに枠を使い、実際に起きた脅威を落とした席 = "
          f"{c.get('L4_and_L2_seats', 0)}")
    print(f"   採用 {c.get('n_adopted', 0)} 件＝席を消費 {c.get('n_spent', 0)}"
          f" / 既存手で兼ねる {c.get('n_reuse', 0)}")
    print(f"   席を消費した採用の内訳＝的中 {c.get('n_adopted_hit', 0)}"
          f" / 空振り {c.get('n_adopted_whiff', 0)}"
          f" / 判定不能 {c.get('n_adopted_unknown', 0)}")
    print(f"★★盤上で実際に『移動禁止』と『移動』を同じ対象へ置いたターン = "
          f"{c.get('played_conflict_turns', 0)} / 全 {c.get('turns', 0)} ターン"
          f"（rules/10_action_cards.md:20,60＝必ず片方が空打ち）")
    print(f"   同一対象へ2枠以上を割いた席 = {c.get('dup_seats', 0)}"
          f"（うち『移動禁止』と『移動』を同じ対象へ = "
          f"{c.get('dup_pin_and_move', 0)}）")
    print(f"   防御可能な脅威 = {c.get('n_defendable', 0)} 件"
          f" / 覆えなかった脅威 = {c.get('n_uncov', 0)} 件")
    print("   picks 枚数の分布： " + " ".join(
        f"{k.split('_')[1]}枚={c[k]}" for k in sorted(c) if k.startswith("npicks_")))
    print("   脅威件数の分布：  " + " ".join(
        f"{k.split('_')[1]}件={c[k]}" for k in sorted(c)
        if k.startswith("nthreats_")))
    print("")
    print(f"  severity（採用）  {_q(res['sev_adopted'])}")
    print(f"  severity（落選）  {_q(res['sev_dropped'])}")
    print(f"  ★margin＝落選最上位 − 採用最下位  {_q(res['margins'])}")
    m = res["margins"]
    if m:
        print(f"    margin<=0（落選の方が下位＝正しい順） = "
              f"{sum(1 for x in m if x <= 0)}/{len(m)}")
        print(f"    0<margin<=0.05（僅差） = "
              f"{sum(1 for x in m if 0 < x <= 0.05)}/{len(m)}")
        print(f"    margin>0.05 = {sum(1 for x in m if x > 0.05)}/{len(m)}")
    print("")
    print("  席を消費して採用された脅威の kind（件／うち的中）")
    for k in sorted(res["kind_adopted"], key=lambda x: -res["kind_adopted"][x]):
        print(f"    {k:24s} {res['kind_adopted'][k]:6d}"
              f" / {res['kind_adopted_real'].get(k, 0)}")
    print("  予算で落ちた脅威の kind（件／うち発生）")
    for k in sorted(res["kind_dropped"], key=lambda x: -res["kind_dropped"][x]):
        print(f"    {k:24s} {res['kind_dropped'][k]:6d}"
              f" / {res['kind_dropped_real'].get(k, 0)}")
    print("  ★severity の較正（帯 → 件数／その日に発生／判定不能）")
    cb = res["calib"]
    bks = ["<0.05", "<0.1", "<0.2", "<0.4", "<0.7", ">=0.7"]
    for st, ttl in (("spent", "席を消費した採用"), ("reuse", "既存手で兼ねる"),
                    ("uncov", "1枚も割かれず（★交絡なし）")):
        line = "    " + f"{ttl:24s}"
        for b in bks:
            n = cb.get(f"{st}|{b}|n", 0)
            r = cb.get(f"{st}|{b}|real", 0)
            line += f" {b}:{r}/{n}"
        print(line)
    print("  ★1枚も割かれなかった脅威の kind（件／うち発生）＝防御の交絡なし")
    for k in sorted(res["kind_uncov"], key=lambda x: -res["kind_uncov"][x]):
        print(f"    {k:24s} {res['kind_uncov'][k]:6d}"
              f" / {res['kind_uncov_real'].get(k, 0)}")
    print("  席を消費した pick のカード種／落選脅威の折り手カード種")
    for k, v in sorted(res["cards"].items(), key=lambda x: (x[0][:3], -x[1])):
        print(f"    {k:34s} {v}")
    if res.get("dup_cards"):
        print("  重複割当のカード組")
        for k, v in sorted(res["dup_cards"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {k:34s} {v}")
    if res["dup_targets"]:
        print("  重複割当の宛先 top")
        for k, v in sorted(res["dup_targets"].items(), key=lambda x: -x[1])[:10]:
            print(f"    {k:30s} {v}")
    if a.top and res["l2_rows"]:
        print("")
        print("  L2（取り逃し）席の一覧")
        for h in res["l2_rows"][:a.top]:
            print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                  f"#{h['seat']} lost={h['lost']}"
                  f" | 落選={[(k, s) for k, _l, s in h['missed']]}"
                  f" | 採用={[(k, s, r) for k, _l, s, r in h['adopted']]}",
                  flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
