# -*- coding: utf-8 -*-
"""B-145：**VIP（KP∪フレンド）が SK と同室（2人きり）へ引き込まれる移動**への備えの射程を数える。

★挙動不変・計測のみ（Phase 1）。`agents/ sim/ engine/ rules/` を1行も変更しない。
主人公は `super().decide()` をそのまま呼び、**戻り値の後で属性を読むだけ**（rng 非消費）。
同一性は `verify` サブコマンドが「素の対局」と「プローブ付き対局」の結末一致で毎回確認する。

発端＝`docs/監査_B144_unlock是正と押しのけ防止の対_2026-08-03.md` §4a／
`docs/負け筋防御ツリー.md` §記録：B-144「次の軍拡対象1」＝
5日級 `random_BTX#16` の L6D2 に mm が `移動←→→学者` を打ち、学者（フレンド）を
病院→神社（SK 教祖と同室）へ動かして殺害＝ループ敗北。

★**B-139b/B-142 の教訓＝上限値を射程と読まない**。最初から層に分けて数える。

## 語彙（**新語を作らない**＝既存実装の語をそのまま使う）

- **VIP**＝`agents/defense_plan._vip_suspects` の定義（「KP容疑者∪フレンド容疑者（生存）。
  **死亡でループ敗北になる守るべき対象**」）の**真値版**＝役職が `キーパーソン` or `フレンド`。
  ★数え上げは神視点で行う（射程の上限を測るため）。**AI の判断材料としては使わない**。
- **同室（2人きり）**＝`engine.turn_end_rules.serial_killer_victims` が犠牲者を出す配置
  （＝**単一ソースを import して判定**。二重実装しない）。大物テリトリーの特例も同関数が持つ。
- **アクティブSK**＝`sim.effects._is_serial` と同じ＝役職 `シリアルキラー` ∪ 妄想拡大ウイルスで
  SK化したパーソン（不安≥3でON・≤1でOFF・2は維持のヒステリシス）。
  ★スナップショットは `virus_serial` を持たないため、ウイルス側は
  「事件フェイズ後の不安」から同じヒステリシス規則で**再構成**する（`--virus off` で除外可能）。

## 層の定義（すべて棋譜＋スナップショットの実測。推定を混ぜない）

- **L0**＝そのターンの `行動解決フェイズ後` に **VIP が SK の犠牲者位置に居る**ターン
  （＝そのままターン終了フェイズを迎えれば死ぬ配置）。
- **L1＝引き込み**＝L0 のうち、**`脚本家行動フェイズ前`（その日の始まり）には成立していなかった**もの
  ＝その日の**行動解決の移動で新しく作られた**配置。機序を3つに分けて数える：
  - `vip`＝VIP 自身が動かされた／`sk`＝SK が動かされた／`third`＝第三者が退出して2人きりになった。
  - ★ mm の移動札が関与したか（`cards_revealed` の owner=mastermind × 移動札）も併記する。
- **L2＝真の射程**＝L1 のうち、**主人公がその日のうちに打てる札を持っていたのに打たなかった**もの。
  「打てる札」＝その日のいずれかの席の `options` に
  (a) `移動禁止` → **その日に実際に動いた駒**（＝mm の移動を打ち消す）、または
  (b) `移動*` → VIP か SK（＝引き離し／退避）
  が在ったこと。★これは**必要条件であって十分条件ではない**（打っても mm が別経路で
  2人きりを作り直しうる）＝**L2 は射程の上限**である（§L2 の注記を報告に必ず書く）。
- **L2s＝盲点**＝L2 のうち、**その日の主人公の防御プランナーが当該 VIP の SK 脅威を
  1つも出していなかった**ターン（`Threat.kind ∈ {kp_sk, virus_sk, sk_setup}` かつ本文に VIP 名）。
  ＝「見えていなかった」席。**是正対象はここ**。
- **L3＝確実な損**＝L1 のうち、**実際にその VIP が SK 殺害で死に、そのループを落とした**もの。

CLI（前面実行・測定は必ず PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8）:
    python -m arena.b145_audit verify --days 3
    python -m arena.b145_audit count  --days 3
    python -m arena.b145_audit count  --days 5 --json out.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from engine.board import AREAS
from engine.models import MOVE_CARDS
from engine.turn_end_rules import serial_killer_victims
from sim import run_game

#: 引き込み脅威を扱う `defense_plan` の Threat.kind（既存の語彙）。
SK_KINDS = ("kp_sk", "virus_sk", "sk_setup")

#: VIP＝`defense_plan._vip_suspects` の真値版。
VIP_ROLES = ("キーパーソン", "フレンド")

#: 「行為の数え上げ」で対照する緩和幅（`--cap` で上書き）。挙動には影響しない
#  （プローブは評価のたびに元の値へ戻す）。
_EVADE_CAP_PROBE: int = 99

#: ★B-147＝True で各行に belief の**分布そのもの**を残す（読むだけ・挙動不変・json が太る）。
#  既定 False＝B-145 当時と同じ行（B-145 の再現性を壊さない）。
KEEP_MARGINALS: bool = False

#: ★B-148＝True で各席に「`_threat_sk_setup` を VIP（KP∪フレンド）へ広げたと**仮定した**時に
#  増える脅威」のシャドー計測を足す（読むだけ・挙動不変・純関数の再評価のみ）。
#  既定 False＝B-145/B-147 当時と同じ行。詳細＝`arena/b148_audit.py`。
KEEP_SK_SETUP_SHADOW: bool = False


# ---------------------------------------------------------------------------
# ★B-148：シャドー（`_threat_sk_setup` の KP 限定を VIP へ広げたと仮定する）
# ---------------------------------------------------------------------------
def _sk_setup_new_threats(view: dict, roles: dict, options: list[dict]) -> list[dict]:
    """`_threat_sk_setup` を VIP へ広げた時に**増える**脅威（＝フレンド側の分）を返す。

    ★二重実装しない＝`agents.defense_plan._threat_sk_setup` の**本体をそのまま呼ぶ**。
    広げ方は「その関数が KP 集合を引くのに使う `_suspects` を、`_LIKELY_P` 以上の
    フレンド容疑者（既に KP 容疑でもある名前は除く）に差し替える」だけ。
    ★`_threat_sk_setup` は kps × sks の直積を独立に回して 1 ペア 1 Threat を append する
      （`agents/defense_plan.py:1008-1100`）＝
      **広げた結果 ＝ 元の結果 ∪ フレンド名だけで回した結果**（分解できる）。
      ∴ 差分の抽出にラベルの文字列パースを使わない。
    ★1 名ずつ回すのは、どのフレンドの分かを**推定せずに**確定させるため。
    """
    import agents.defense_plan as dp

    opts = dp._Opts(options or [])
    mm_chars, _boards = dp._mm_touched(view)
    kp_now = set(dp._suspects(roles, "キーパーソン", dp._LIKELY_P))
    fr_new = {n: p for n, p in dp._suspects(roles, "フレンド", dp._LIKELY_P).items()
              if n not in kp_now}
    out: list[dict] = []
    if not fr_new:
        return out
    _orig = dp._suspects
    for name in sorted(fr_new):
        def _patched(rm, role, thresh=dp._SUSPECT_P, _n=name, _p=fr_new[name]):
            if role == "キーパーソン":
                return {_n: _p}
            return _orig(rm, role, thresh)
        dp._suspects = _patched
        try:
            ts = dp._threat_sk_setup(view, roles, opts, mm_chars)
        finally:
            dp._suspects = _orig
        for t in ts:
            out.append({
                "vip": name, "p_vip": round(float(fr_new[name]), 4),
                "prob": round(float(t.prob), 4),
                "severity": round(float(t.severity), 4),
                "label": str(t.label),
                "breaks": [(b.card, b.target, b.target_kind, round(b.cost, 2),
                            bool(b.robust))
                           for c in t.conditions for b in c.breaks],
            })
    return out


def _wide_sk_setup(view, roles, opts, mm_chars):
    """`_threat_sk_setup` の VIP 版（KP 限定 ∪ フレンド）＝**本体は元関数**。"""
    import agents.defense_plan as dp

    _orig = dp._suspects

    def _patched(rm, role, thresh=dp._SUSPECT_P):
        if role == "キーパーソン":
            o = dict(_orig(rm, "キーパーソン", thresh))
            for n, p in _orig(rm, "フレンド", thresh).items():
                o[n] = max(o.get(n, 0.0), p)
            return o
        return _orig(rm, role, thresh)

    dp._suspects = _patched
    try:
        return _WIDE_BASE(view, roles, opts, mm_chars)
    finally:
        dp._suspects = _orig


_WIDE_BASE = None      # 遅延束縛（_threat_sk_setup の原本）


def _cover_map(stash) -> dict:
    """`_b100_plan`＝(threats, plan) → {(kind, label): 覆えたか}（`plan.covered` は id 索引）。"""
    if not stash:
        return {}
    threats, plan = stash
    cov = getattr(plan, "covered", {}) or {}
    return {(getattr(t, "kind", None), str(getattr(t, "label", ""))): (id(t) in cov)
            for t in (threats or ())}


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝super() の戻り値の後で属性を読むだけ）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """席ごとに options とプランナーの脅威一覧を控える（採点には一切触れない）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.seats: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        _kinshi0 = self._kinshi_used
        chosen = super().decide(view, decision, options)
        if decision != "set_card":
            return chosen
        threats = []
        plan = getattr(self, "_b100_plan", None)
        if plan:
            for t in (plan[0] or []):
                threats.append({"kind": getattr(t, "kind", None),
                                "label": str(getattr(t, "label", "")),
                                "prob": float(getattr(t, "prob", 0.0) or 0.0)})
        # ★AI が「見えているはずの材料」を読むだけ（採点には触れない）。
        marg = {}
        try:
            marg = self._belief.role_marginals() or {}
        except Exception:      # noqa: BLE001
            marg = {}
        # ★B-145 Phase 2 の「行為の数え上げ」（規約 §11b の最強の示し方）＝
        #   緩和（`B145_EVADE_MAX_FRIENDS`）で **新しく立つ退避手** の席数。
        #   純関数の再評価だけ＝rng を消費しない・戻り値には触れない。
        fired: list = []
        try:
            _cap0 = self.B145_EVADE_MAX_FRIENDS
            mm_now = {p.get("target") for p in view.get("placements", [])
                      if p.get("owner") == "mastermind"
                      and p.get("target_kind") == "character"}
            for card, tgt, kind in [(o.get("card"), o.get("target"),
                                     o.get("target_kind")) for o in options]:
                if kind != "character" or card not in ("移動←→", "移動↑↓"):
                    continue
                c = self._alive(view, tgt)
                if not c:
                    continue
                self.B145_EVADE_MAX_FRIENDS = None
                a = bool(self._b76_friend_evade_ok(view, tgt, card, c, mm_now))
                self.B145_EVADE_MAX_FRIENDS = _EVADE_CAP_PROBE
                b = bool(self._b76_friend_evade_ok(view, tgt, card, c, mm_now))
                self.B145_EVADE_MAX_FRIENDS = _cap0
                if b and not a:
                    fired.append((card, tgt))
        except Exception:      # noqa: BLE001
            self.B145_EVADE_MAX_FRIENDS = _cap0
            fired = []
        self.seats.append({
            "evade_newly_ok": sorted(fired),
            "loop": view.get("loop"), "day": view.get("day"), "seat": view.get("seat"),
            "options": [(o.get("card"), o.get("target"), o.get("target_kind"))
                        for o in options],
            "chosen": (chosen.get("card"), chosen.get("target"),
                       chosen.get("target_kind")),
            "threats": threats,
            "p_friend": {n: round(float(d.get("フレンド", 0.0)), 3)
                         for n, d in marg.items()},
            "p_kp": {n: round(float(d.get("キーパーソン", 0.0)), 3)
                     for n, d in marg.items()},
            "p_sk": {n: round(float(d.get("シリアルキラー", 0.0)), 3)
                     for n, d in marg.items()},
            "friend_guards": sorted(getattr(self, "_friend_guards", set()) or ()),
            "fatal_guards": sorted(getattr(self, "_fatal_guards", set()) or ()),
            "sk_cands": sorted(getattr(self, "_sk_cands", set()) or ()),
            "sk_strong": sorted(getattr(self, "_sk_strong", set()) or ()),
            "sk_suspects": sorted(getattr(self, "_sk_suspects", set()) or ()),
        })
        if KEEP_MARGINALS:
            # ★B-147＝「そもそも観測が有ったのか」の切り分け。**belief 自身の関数**を
            #   その時点の履歴に当てて、フレンドに効く観測チャネルの在庫を数える（読むだけ）。
            from agents import belief as _bl
            hist = list(self._belief.history() or ())
            try:
                _kp_ex, fr_ex, _tt_ex = _bl._death_role_exclusions(hist)
            except Exception:      # noqa: BLE001
                fr_ex = set()
            rev = {}
            try:
                rev = _bl._revealed_roles(hist)
            except Exception:      # noqa: BLE001
                rev = {}
            self.seats[-1].update({
                "marg_full": {n: {r: round(float(p), 6) for r, p in d.items()
                                  if p > 0}
                              for n, d in marg.items()},
                "fr_excluded": sorted(fr_ex),
                "revealed_roles": dict(rev),
                "n_role_reveal": sum(1 for e in hist
                                     if e.get("event") == "role_reveal"),
                "n_death": sum(1 for e in hist if e.get("event") == "death"),
                "n_worlds": float(self._belief.n_worlds()),
                # ★B-147＝**belief が使っていない**唯一の実在チャネル（opponent-model）。
                #   相手が過去に札を置いた宛先の累計（`cards_revealed`＝公開情報。
                #   当日の伏せ札は中身が見えないので history には出ない＝先読みではない）。
                "mm_targets": {
                    n: k for n, k in sorted(Counter(
                        p.get("target") for e in hist
                        if e.get("event") == "cards_revealed"
                        for p in (e.get("placements") or [])
                        if p.get("owner") == "mastermind"
                        and p.get("target_kind") == "character").items())},
            })
        if KEEP_SK_SETUP_SHADOW:
            self._b148_shadow(view, options, marg, _kinshi0)
        return chosen

    # -- ★B-148：シャドー計測（純関数の再評価だけ・rng 非消費・戻り値に触れない） ----
    def _b148_shadow(self, view: dict, options: list[dict], marg: dict,
                     kinshi0: bool) -> None:
        import agents.defense_plan as dp
        global _WIDE_BASE

        row = self.seats[-1]
        base_kinds = Counter(t["kind"] for t in row["threats"])
        row["sks_base_n"] = int(base_kinds.get("sk_setup", 0))
        row["sks_new"] = []
        row["kp_likely"] = sorted(dp._suspects(marg, "キーパーソン", dp._LIKELY_P))
        row["fr_likely"] = sorted(dp._suspects(marg, "フレンド", dp._LIKELY_P))
        row["fr_suspect"] = sorted(dp._suspects(marg, "フレンド", dp._SUSPECT_P))
        try:
            row["sks_new"] = _sk_setup_new_threats(view, marg, options)
        except Exception as e:      # noqa: BLE001
            row["sks_new_error"] = repr(e)
            return
        if not row["sks_new"]:
            return
        # ---- L2/L4 の材料＝**広げた版で計画を丸ごと引き直す**（順序も本物と同じ経路）--
        _kinshi_now, _plan_now = self._kinshi_used, getattr(self, "_b100_plan", None)
        self._kinshi_used = kinshi0
        try:
            recs0 = dict(self._defense_plan_recs(view, options))
            plan0 = getattr(self, "_b100_plan", None)
            cov0 = _cover_map(plan0)
            _WIDE_BASE = dp._threat_sk_setup
            dp._threat_sk_setup = _wide_sk_setup
            try:
                recs1 = dict(self._defense_plan_recs(view, options))
                plan1 = getattr(self, "_b100_plan", None)
            finally:
                dp._threat_sk_setup = _WIDE_BASE
                _WIDE_BASE = None
            cov1 = _cover_map(plan1)
            row["recs0"] = {"|".join(map(str, k)): round(v, 2)
                            for k, v in sorted(recs0.items())}
            row["recs1"] = {"|".join(map(str, k)): round(v, 2)
                            for k, v in sorted(recs1.items())}
            row["recs_changed"] = row["recs0"] != row["recs1"]
            row["picks0"] = sorted({(b.card, b.target) for b in (plan0[1].picks
                                                                if plan0 else ())})
            row["picks1"] = sorted({(b.card, b.target) for b in (plan1[1].picks
                                                                if plan1 else ())})
            # 新脅威に手が割かれたか（＝広げた計画で覆われた sk_setup×フレンド）
            new_lbls = {t["label"] for t in row["sks_new"]}
            row["new_covered"] = sorted(lb for (kd, lb), c in cov1.items()
                                        if lb in new_lbls and c)
            # ★L4＝**元は覆えていたのに、広げた結果 覆えなくなった**負け筋（B-72 二正面）
            row["thinned"] = sorted(f"{kd}|{lb}" for (kd, lb), c in cov0.items()
                                    if c and not cov1.get((kd, lb), False))
        except Exception as e:      # noqa: BLE001
            row["shadow_error"] = repr(e)
        finally:
            self._kinshi_used, self._b100_plan = _kinshi_now, _plan_now


# ---------------------------------------------------------------------------
# スナップショットからの盤面判定（engine の単一ソースを使う）
# ---------------------------------------------------------------------------
class _C:
    """`serial_killer_victims` が要求する最小の面（name/area）。"""

    __slots__ = ("name", "area")

    def __init__(self, name: str, area):
        self.name, self.area = name, area


def _victims_of(chars: dict, serial: set, oomono_territory=None) -> dict:
    """スナップショットの characters から SK【強制】の犠牲者を算定（engine を import）。"""
    on_board = [_C(n, c["area"]) for n, c in chars.items()
                if c.get("alive", True) and c.get("area") in AREAS]
    return serial_killer_victims(on_board, lambda c: c.name in serial,
                                 oomono_territory=oomono_territory)


def _snap_index(state) -> dict:
    out = {}
    for s in state.phase_snapshots:
        out[(s["loop"], s["day"], s["point"])] = s
    return out


def _virus_timeline(state, snaps: dict, use_virus: bool) -> dict:
    """(loop, day) -> そのターン終了フェイズ時点でウイルスSK化しているパーソン名の集合。

    ★`sim.effects._update_virus_serial` と同じ規則（不安≥3でON・≤1でOFF・2は維持）を、
    `事件フェイズ後` スナップショットの不安から再構成する（スナップショットは
    `virus_serial` を持たないため。`--virus off` で本経路を丸ごと外せる）。"""
    out: dict = {}
    if not use_virus or "妄想拡大ウイルス" not in getattr(state.script, "rule_xs", ()):  # noqa: E501
        return out
    persons = [n for n, c in state.characters.items()
               if state.script.role_of(n) == "パーソン"]
    if not persons:
        return out
    cur: set = set()
    keys = sorted({(k[0], k[1]) for k in snaps if k[2] == "事件フェイズ後"})
    prev_loop = None
    for loop, day in keys:
        if loop != prev_loop:
            cur = set()          # ループ開始でカウンターは戻る＝ウイルス化も解ける
            prev_loop = loop
        s = snaps[(loop, day, "事件フェイズ後")]
        for n in persons:
            c = s["characters"].get(n)
            if not c:
                continue
            if c.get("unrest", 0) >= 3:
                cur.add(n)
            elif c.get("unrest", 0) <= 1:
                cur.discard(n)
        out[(loop, day)] = set(cur)
    return out


def _lost_loops(state) -> dict:
    out = {}
    for e in state.history:
        if e.get("event") == "loop_result" and "敗北" in str(e.get("result", "")):
            out[e.get("loop")] = str(e.get("result", ""))
    return out


def _sk_deaths(state) -> set:
    """(loop, day, name)＝SK【強制】で死んだ記録（secret_log＝原因つき）。"""
    out = set()
    for e in state.secret_log:
        if e.get("event") == "death" and "シリアルキラー" in str(e.get("cause", "")):
            out.add((e.get("loop"), e.get("day"), e.get("name")))
    return out


def _outcome(state) -> str:
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return "defense"
    if fb:
        return "fb_win" if state.winner == "protagonist" else "fb_loss"
    return "loss"


def _mm_moves(state, loop: int, day: int) -> set:
    """その日に mm が移動札を置いた対象（cards_revealed＝公開情報）。"""
    out = set()
    for e in state.history:
        if (e.get("loop"), e.get("day")) != (loop, day):
            continue
        if e.get("event") != "cards_revealed":
            continue
        for p in e.get("placements", []):
            if p.get("owner") == "mastermind" and p.get("card") in MOVE_CARDS:
                out.add(p.get("target"))
    return out


# ---------------------------------------------------------------------------
# 1局の監査
# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8, use_virus: bool = True) -> dict:
    probe = replace(script, loops=loops)
    hp = _Probe(seed)
    state, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    virus = _virus_timeline(state, snaps, use_virus)
    lost = _lost_loops(state)
    deaths = _sk_deaths(state)
    territory = getattr(state.script, "oomono_territory", None)
    role_sk = {n for n in state.characters
               if state.script.role_of(n) == "シリアルキラー"}
    vips = {n for n in state.characters
            if state.script.role_of(n) in VIP_ROLES}
    seats_by_day: dict = {}
    for s in hp.seats:
        seats_by_day.setdefault((s["loop"], s["day"]), []).append(s)

    rows = []
    if vips and (role_sk or virus):
        turns = sorted({(k[0], k[1]) for k in snaps if k[2] == "行動解決フェイズ後"})
        for loop, day in turns:
            after = snaps.get((loop, day, "行動解決フェイズ後"))
            before = snaps.get((loop, day, "脚本家行動フェイズ前"))
            if after is None or before is None:
                continue
            # アクティブSK＝役職SK ∪ そのターンのウイルスSK化（同ターン終了時に効く集合）
            sk = set(role_sk) | set(virus.get((loop, day), ()))
            if not sk:
                continue
            v_after = _victims_of(after["characters"], sk, territory)
            v_before = _victims_of(before["characters"], sk, territory)
            for vic, killer in sorted(v_after.items()):
                if vic not in vips:
                    continue
                row = {
                    "loop": loop, "day": day, "vip": vic, "sk": killer,
                    "vip_role": state.script.role_of(vic),
                    "area": after["characters"][vic]["area"],
                    "new": vic not in v_before,        # L1＝その日に新しく作られた
                }
                # 機序（before → after の移動）
                a0 = before["characters"].get(vic, {}).get("area")
                a1 = after["characters"].get(vic, {}).get("area")
                s0 = before["characters"].get(killer, {}).get("area")
                s1 = after["characters"].get(killer, {}).get("area")
                movers = {n for n, c in after["characters"].items()
                          if before["characters"].get(n, {}).get("area") != c["area"]}
                if a0 != a1:
                    mech = "vip"
                elif s0 != s1:
                    mech = "sk"
                elif movers:
                    mech = "third"
                else:
                    mech = "none"
                row["mech"] = mech
                row["movers"] = sorted(movers)
                mmv = _mm_moves(state, loop, day)
                row["mm_move_targets"] = sorted(mmv)
                row["mm_driven"] = bool(mmv & (movers | {vic, killer}))
                # ---- L2 の材料＝その日の席の options / 実際に打った手 ----------
                seats = seats_by_day.get((loop, day), [])
                opt_pin = set()
                opt_move = set()
                for s in seats:
                    for card, tgt, kind in s["options"]:
                        if kind != "character":
                            continue
                        if card == "移動禁止":
                            opt_pin.add(tgt)
                        elif card in MOVE_CARDS:
                            opt_move.add(tgt)
                played = {(c, t) for s in seats for (c, t, _k) in [s["chosen"]]}
                row["could_pin"] = sorted(opt_pin & movers)
                row["could_move"] = sorted(opt_move & {vic, killer})
                row["played_pin"] = sorted(
                    t for (c, t) in played if c == "移動禁止" and t in movers)
                row["played_move"] = sorted(
                    t for (c, t) in played if c in MOVE_CARDS and t in {vic, killer})
                # ---- L2s の材料＝プランナーが VIP の SK 脅威を出していたか ------
                seen = []
                for s in seats:
                    for t in s["threats"]:
                        if t["kind"] in SK_KINDS and vic in t["label"]:
                            seen.append((t["kind"], round(t["prob"], 3)))
                row["threat_seen"] = sorted(set(seen))
                # ---- AI が持っていた材料（是正が**表現可能か**の判定材料） ---------
                s0v = seats[0] if seats else {}
                row["p_friend_vip"] = (s0v.get("p_friend") or {}).get(vic)
                row["p_kp_vip"] = (s0v.get("p_kp") or {}).get(vic)
                row["p_sk_killer"] = (s0v.get("p_sk") or {}).get(killer)
                row["vip_in_guards"] = bool(
                    vic in (s0v.get("friend_guards") or [])
                    or vic in (s0v.get("fatal_guards") or []))
                row["sk_in_pools"] = sorted(
                    k for k in ("sk_cands", "sk_strong", "sk_suspects")
                    if killer in (s0v.get(k) or []))
                if KEEP_MARGINALS:
                    # ★B-147＝推理側の切り分け用。**分布そのもの**を落とす（読むだけ・挙動不変）。
                    row["p_friend_all"] = dict(s0v.get("p_friend") or {})
                    row["p_kp_all"] = dict(s0v.get("p_kp") or {})
                    row["p_sk_all"] = dict(s0v.get("p_sk") or {})
                    row["friend_guards"] = list(s0v.get("friend_guards") or ())
                    row["true_friends"] = sorted(
                        n for n in state.characters
                        if state.script.role_of(n) == "フレンド")
                    row["marg_full"] = s0v.get("marg_full") or {}
                    row["fr_excluded"] = list(s0v.get("fr_excluded") or ())
                    row["revealed_roles"] = dict(s0v.get("revealed_roles") or {})
                    row["n_role_reveal"] = s0v.get("n_role_reveal")
                    row["n_death"] = s0v.get("n_death")
                    row["n_worlds"] = s0v.get("n_worlds")
                    row["mm_targets"] = dict(s0v.get("mm_targets") or {})
                    row["alive_at_seat"] = sorted(
                        n for n, c in (before["characters"] or {}).items()
                        if c.get("alive", True))
                # ---- L3 の材料 -----------------------------------------------
                row["died"] = (loop, day, vic) in deaths
                row["loop_lost"] = loop in lost
                row["loop_result"] = lost.get(loop)
                rows.append(row)
    return {"outcome": _outcome(state), "rows": rows,
            # ★B-148＝席そのもの（シャドー計測の粒度）。既定 False では付けない。
            "seats": (hp.seats if KEEP_SK_SETUP_SHADOW else None),
            "l1_keys": sorted({(r["loop"], r["day"], r["vip"]) for r in rows
                               if r.get("new")}),
            "l3_keys": sorted({(r["loop"], r["day"], r["vip"]) for r in rows
                               if r.get("new") and r["died"] and r["loop_lost"]}),
            "has_sk": bool(role_sk or virus), "vips": sorted(vips),
            "evade_seats": sum(1 for s in hp.seats if s.get("evade_newly_ok")),
            "evade_list": [(s["loop"], s["day"], s["seat"], s["evade_newly_ok"])
                           for s in hp.seats if s.get("evade_newly_ok")]}


def classify(rows: list[dict]) -> None:
    for r in rows:
        r["L0"] = True
        r["L1"] = bool(r["new"])
        preventable = bool(r["could_pin"] or r["could_move"])
        acted = bool(r["played_pin"] or r["played_move"])
        r["L2"] = bool(r["L1"] and preventable and not acted)
        r["L2s"] = bool(r["L2"] and not r["threat_seen"])
        r["L3"] = bool(r["L1"] and r["died"] and r["loop_lost"])


# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, verbose: bool = False,
        start: int = 0, end: int | None = None, use_virus: bool = True) -> dict:
    from arena.benchmark import benchmark_scripts

    allrows: list[dict] = []
    base = Counter()
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = audit_game(sc, seed, loops=loops, use_virus=use_virus)
        base["games"] += 1
        base["evade_newly_ok_seats"] += res["evade_seats"]
        if res["evade_seats"]:
            base["evade_newly_ok_games"] += 1
        if res["has_sk"]:
            base["games_with_sk"] += 1
        if res["vips"]:
            base["games_with_vip"] += 1
        for r in res["rows"]:
            allrows.append({"script": name, "seed": seed,
                            "outcome": res["outcome"], **r})
        if res["evade_seats"]:
            print(f"  [退避が新しく立つ席] {name} s{seed}: {res['evade_list']}",
                  flush=True)
        if verbose:
            print(f"  {name} s{seed}: 行 {len(res['rows'])} [{res['outcome']}]",
                  flush=True)
    classify(allrows)
    c = Counter()
    for r in allrows:
        c["L0"] += 1
        c[f"L0_mech_{r['mech']}"] += 1
        for k in ("L1", "L2", "L2s", "L3"):
            if r[k]:
                c[k] += 1
        if r["L1"]:
            c[f"L1_mech_{r['mech']}"] += 1
            c[f"L1_role_{r['vip_role']}"] += 1
            if r["mm_driven"]:
                c["L1_mm_driven"] += 1
            if r["threat_seen"]:
                c["L1_threat_seen"] += 1
            if r["died"]:
                c["L1_died"] += 1
        if r["L2"]:
            c[f"L2_mech_{r['mech']}"] += 1
            if r["could_pin"]:
                c["L2_pin_available"] += 1
            if r["could_move"]:
                c["L2_move_available"] += 1
        if r["L3"]:
            c[f"L3_mech_{r['mech']}"] += 1
            c[f"L3_role_{r['vip_role']}"] += 1
            if r["threat_seen"]:
                c["L3_threat_seen"] += 1
    return {"days": days, "base": dict(base), "counts": dict(c), "rows": allrows}


def verify(days: int = 3, loops: int = 8, n: int = 12) -> int:
    from arena.benchmark import benchmark_scripts

    bad = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[:n]:
        s0, _ = run_game(replace(sc, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": (h0 := HeuristicProtagonist(seed)),
                          "p2": h0, "p3": h0})
        s1, _ = run_game(replace(sc, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": (h1 := _Probe(seed)), "p2": h1, "p3": h1})
        ok = (s0.winner == s1.winner and s0.loop_no == s1.loop_no
              and len(s0.history) == len(s1.history)
              and [e.get("event") for e in s0.history]
              == [e.get("event") for e in s1.history])
        print(f"  {name} s{seed}: {'一致' if ok else '★不一致'}"
              f" winner={s0.winner}/{s1.winner} loop={s0.loop_no}/{s1.loop_no}"
              f" hist={len(s0.history)}/{len(s1.history)}", flush=True)
        bad += 0 if ok else 1
    print(f"不一致 = {bad} 件")
    return 1 if bad else 0


_MECH = {"vip": "VIPが動かされた", "sk": "SKが動かされた",
         "third": "第三者が退出した", "none": "移動なし"}


def main(argv=None) -> int:
    global _EVADE_CAP_PROBE
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count", choices=["count", "verify"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--cap", type=int, default=_EVADE_CAP_PROBE,
                    help="行為の数え上げで対照する B145_EVADE_MAX_FRIENDS（挙動不変）")
    ap.add_argument("--virus", default="on", choices=["on", "off"],
                    help="妄想拡大ウイルスによるSK化を数えるか（off＝役職SKのみ）")
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    _EVADE_CAP_PROBE = a.cap
    if a.cmd == "verify":
        return verify(days=a.days, loops=a.loops)
    print(f"[切替口] B141B_UNLOCK_SAME_DAY="
          f"{HeuristicProtagonist.B141B_UNLOCK_SAME_DAY}"
          f" / B143_YIELD={HeuristicProtagonist.B143_YIELD}"
          f" / B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
          f" / B100_MIX={HeuristicProtagonist.B100_MIX}"
          f" / B145_EVADE_MAX_FRIENDS="
          f"{HeuristicProtagonist.B145_EVADE_MAX_FRIENDS}（対照 cap={a.cap}）"
          f" / virus={a.virus} / days={a.days} loops={a.loops}", flush=True)
    res = run(days=a.days, loops=a.loops, verbose=a.verbose,
              start=a.start, end=a.end, use_virus=(a.virus == "on"))
    b, c = res["base"], res["counts"]
    print(f"== B-145：VIP が SK と同室へ引き込まれる移動 — 射程"
          f"（{a.days}日級 {b.get('games', 0)}局／SK在り {b.get('games_with_sk', 0)}局"
          f"／VIP在り {b.get('games_with_vip', 0)}局）==")
    print(f"★L0 行動解決後に VIP が SK の犠牲者位置 = {c.get('L0', 0)}")
    for m in ("vip", "sk", "third", "none"):
        if c.get(f"L0_mech_{m}"):
            print(f"   ├ {_MECH[m]} = {c[f'L0_mech_{m}']}")
    print(f"★L1 その日に**新しく作られた**引き込み = {c.get('L1', 0)}"
          f"（mmの移動札が関与 {c.get('L1_mm_driven', 0)}"
          f"／KP {c.get('L1_role_キーパーソン', 0)}"
          f"・フレンド {c.get('L1_role_フレンド', 0)}）")
    for m in ("vip", "sk", "third"):
        if c.get(f"L1_mech_{m}"):
            print(f"   ├ {_MECH[m]} = {c[f'L1_mech_{m}']}")
    print(f"   ├ プランナーが当該VIPのSK脅威を出していた = {c.get('L1_threat_seen', 0)}")
    print(f"   └ 実際に死んだ = {c.get('L1_died', 0)}")
    print(f"★L2 阻止札を持っていたのに打たなかった（射程の**上限**）= {c.get('L2', 0)}"
          f"（移動禁止が在った {c.get('L2_pin_available', 0)}"
          f"／退避移動が在った {c.get('L2_move_available', 0)}）")
    print(f"★L2s うち**プランナーが脅威を1つも出していなかった**（盲点）= {c.get('L2s', 0)}")
    print(f"★★行為の数え上げ：緩和(cap={a.cap})で**新しく立つ退避手**の席 = "
          f"{b.get('evade_newly_ok_seats', 0)}（{b.get('evade_newly_ok_games', 0)}局）")
    print(f"★L3 実際に殺害が起きてループを落とした = {c.get('L3', 0)}"
          f"（KP {c.get('L3_role_キーパーソン', 0)}"
          f"・フレンド {c.get('L3_role_フレンド', 0)}"
          f"／うち脅威が見えていた {c.get('L3_threat_seen', 0)}）")
    hits = [r for r in res["rows"] if r["L1"]]
    if a.top and hits:
        print("")
        print("  L1 席の一覧（局 / LD / VIP(役職)←SK / 機序 / 阻止札 / 脅威 / 層 / 結末）")
        for h in hits[:a.top]:
            lv = "L3" if h["L3"] else ("L2s" if h["L2s"] else
                                       ("L2" if h["L2"] else "L1"))
            print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                  f" {h['vip']}({h['vip_role']})←{h['sk']}@{h['area']}"
                  f" | {_MECH[h['mech']]}"
                  f" | pin={h['could_pin']} move={h['could_move']}"
                  f" | pF={h.get('p_friend_vip')} pKP={h.get('p_kp_vip')}"
                  f" pSK={h.get('p_sk_killer')} guards={h.get('vip_in_guards')}"
                  f" pool={h.get('sk_in_pools')}"
                  f" | 脅威={h['threat_seen'] or 'なし'}"
                  f" | {lv} | {'死' if h['died'] else '生'}"
                  f"/{'敗' if h['loop_lost'] else '防衛'} [{h['outcome']}]",
                  flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
