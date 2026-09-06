# -*- coding: utf-8 -*-
"""B-153 Phase 1：**従者の特性（身代わり／追随）と自殺の射程を数える**（★計測のみ・`agents/` 非接触）。

## 発端

`docs/バックログ_構想メモ_FableA.md` §29（B-153）＝ユーザー実戦報告（5日級 `random_FS` seed 4）：
「キーパーソンと自殺の犯人が**従者の効果でくっつく**脚本なので、**従者を犯人から引き剥がす方法**を
知らないと剥がせない（主ではなく**従者側**を動かす必要がある）。**従者がキーパーソンのとき、
主が殺害されても敗北になる**」。

KB＝`rules/30_characters.md:65`（現物カード転記 2026-07-23）：
- 「同一エリアのお嬢様か大物が**移動する場合、自身への移動を無視して一緒に移動する**」＝**追随**
- 「同一エリアのお嬢様か大物が**死亡する場合、代わりに死亡する**」＝**身代わり**（主は生存・強制）
- 友好4(1/L)＝ボードの自身以外1人を**特性の対象に追加**（`rules/20_goodwill_abilities.md:323`）

★**B-157 Phase 2 の「自殺」と同一レーン**（`docs/監査_B157_*` §7-2）＝
自殺の述語は「犯人候補に VIP 疑い」**or**「犯人候補が主で従者が同エリア」＝B-153 と交差する。

## 数え上げの定義（★先に固定する・後から変えない）

**単位**＝**局**＝(脚本名, seed)。3日級130局／5日級70局。**独立脚本**＝脚本名のユニーク数。
**発動・発生の回数**＝全ループ合計（1局が複数ループを含む）。

- **(a) 配役**（静的＝対局不要）
  A1 従者が配役／A2 うち従者=KP or フレンド（＝**VIP従者**）／
  A3 うち主（お嬢様/大物）も配役＝**特性が原理的に発火しうる局**／A4＝A2∧A3＝**ユーザー報告の型**。
- **(b) 身代わり**＝`sim/effects.py:97-106` が出す秘匿イベント `juusha_substitute` の回数。
  B2＝うち**従者がVIP**＝その場で敗北条件成立（**主人公の損**）／
  B3＝うち**守られた主がVIP**＝**主人公の得**（★両刃であることを数える）／B4＝発動回にループが敗北。
- **(c) 追随**＝`sim/effects.py:187-205` が出す公開イベント
  `{phase:"action_resolution", event:"move", name:"従者"}`（★この経路以外に
  行動解決フェイズで従者の move が公開される箇所は無い＝`sim/effects.py:218` が唯一）。
  C2＝その日に**主人公が従者へ移動札を置いていた**回（＝引き剥がしを試みたが自身への移動が無視された）。
  C3＝主人公が従者へ移動札を置いた回（全体）。C4＝その日の解決後に従者と主が別エリアになった回。
- **(d) 自殺の D（直結）を B-157 とは独立に再現**＝B-157 §1-2 と同じ D の定義
  （その事件フェイズ内で敗北条件そのものが成立）を、**b157_audit を読まずに**書き直して照合する。
- **(e) 席単位＝主人公が打てたはずの手が候補に立っているか**（★母数は「危険席」）
  - **危険席H1**＝従者が生存・盤上 ∧ 主（お嬢様/大物/友好4追加）が生存 ∧ **同一エリア**
    （＝身代わりが今その場で成立しうる席）。H1a＝従者がVIP（＝主人公の損）／H1b＝主がVIP。
  - **危険席H2**＝今日以降に自殺が予定 ∧ **その真犯人が主** ∧ 従者が生存（＝B-153×B-157 の交点）。
  - e1＝options に **移動札→従者**（★主へ置いても追随される＝従者側に置く必要がある）／
    e2＝options に **不安-1→自殺の犯人**（発生そのものを止める）／
    e3＝従者の**友好4（特性対象を追加）**が使われた回。
  - ★あわせて **`enumerate_threats` の返り値に「従者」が現れたか**（＝判定器の有無）。
- **(f) 射程O′**＝★**「その場で」ではない**＝現在の**脚本家AI**が取らなかったが、
  **1手前の配置が違えば規則上その場で敗北へ届いた手**＝
  「主が死亡した場面で、**従者がVIPかつ生存・盤上だったのに別エリアに居た**」回。
  ★B-157 の O は事件効果の**対象選択1手**だったが、従者は選択を持たない（強制）ため
  **同じ強さの O は原理的に定義できない**＝**O′ と明記して別物として読む**（§6）。
  逆向きに、**主がVIPで死亡し従者（非VIP）が別エリアだった**回＝**主人公側の射程O′**（寄せれば救えた）。

## 再利用（★二重実装の禁止）

- `arena/b151_audit._Probe`（`enumerate_threats` の本番引数・返り値を控える挙動不変プローブ）を
  **継承して**席の観測を足すだけ＝プランナーの呼び出しフックは書き写していない。
- `arena/b145_audit._outcome` / `_snap_index` / `_lost_loops`。
- ★`arena/b157_audit` は **(d) の照合対象**なので **import しない**（独立再現のため）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b153_audit census --days 3            # (a) 対局不要
    python -m arena.b153_audit verify --days 3 --end 12   # 挙動不変（棋譜完全一致）＋自己検査
    python -m arena.b153_audit count  --days 3 --json d3.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _lost_loops, _outcome, _snap_index
# ★B-151 の挙動不変プローブを継承して使う（フックを書き写さない）。
from arena.b151_audit import _Probe
from engine.models import MOVE_CARDS
from sim import run_game

#: ループ終了時の敗北に直結する役職（`sim/effects.evaluate_loop_end` / `kill_character`）。
_VIP_ROLES = ("キーパーソン", "フレンド")

#: 従者の特性の既定の対象（`rules/30_characters.md:65`）。友好4で追加される分は動的。
_BASE_MASTERS = ("お嬢様", "大物")

_PROT_SEATS = ("p1", "p2", "p3")

#: ★検死（`autopsy`）専用の詳細記録フラグ。既定 False＝`count` は軽いまま。
#  記録するだけで決定には触れない（`_JProbe2` は `super().decide()` の返り値をそのまま返す）。
ALL_THREATS: bool = False


# ---------------------------------------------------------------------------
# 席のプローブ（★挙動不変＝`_Probe` の返り値をそのまま返す）
# ---------------------------------------------------------------------------
class _JProbe(_Probe):
    """`_Probe`（B-151）に **従者まわりの席観測**を足すだけのプローブ。

    ★`super().decide()` の返り値をそのまま返す＝決定に一切触れない。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.jseats: list[dict] = []
        self._last_threat_blob: str = ""

    # `_Probe.decide` が `enumerate_threats` を包んで `self.seats` へ積む。
    # ここではその後に「席で何が打てたか」を公開情報だけで控える。
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision == "set_card":
            try:
                self.jseats.append(self._jobserve(view, options, chosen))
            except Exception as e:                      # noqa: BLE001
                self.jseats.append({"loop": view.get("loop"), "day": view.get("day"),
                                    "seat": view.get("seat"), "error": repr(e)})
        return chosen

    def _jobserve(self, view: dict, options: list, chosen: dict) -> dict:
        chars = {c.get("name"): c for c in (view.get("characters") or [])}
        # ★友好4で追加された特性対象は**公開イベント**（`sim/abilities.py:488`）＝
        #   主人公も読める情報。今ループぶんだけを拾う（ループ開始で state はリセットされる）。
        loop = view.get("loop")
        added = {e.get("target") for e in (view.get("history") or ())
                 if e.get("event") == "juusha_target_added" and e.get("loop") == loop}
        masters = [n for n in (set(_BASE_MASTERS) | added) if n in chars]
        ju = chars.get("従者")
        row = {"loop": loop, "day": view.get("day"), "seat": view.get("seat"),
               "has_juusha": ju is not None,
               "ju_alive": bool(ju and ju.get("alive", True) and ju.get("area")),
               "ju_area": (ju or {}).get("area"),
               "masters": sorted(masters),
               "added": sorted(added)}
        if not row["ju_alive"]:
            return row
        co = [n for n in masters
              if chars.get(n) and chars[n].get("alive", True)
              and chars[n].get("area") == ju.get("area")]
        row["co_located"] = sorted(co)
        # ---- 席で打てた手（options は本番と同じ list）------------------------
        row["opt_move_juusha"] = sorted({o["card"] for o in options
                                         if o.get("target") == "従者"
                                         and o.get("target_kind") == "character"
                                         and o.get("card") in MOVE_CARDS})
        row["opt_cool"] = sorted({o["target"] for o in options
                                  if o.get("card") == "不安-1"
                                  and o.get("target_kind") == "character"})
        row["chosen"] = (chosen.get("card"), chosen.get("target"))
        # ★判定器の有無＝脅威に「従者」が現れたか（`_JProbe2` が上書きする）。
        row["threat_juusha"] = False
        return row


class _JProbe2(_JProbe):
    """`enumerate_threats` の返り値を直接見て「従者」の出現を数えるプローブ。

    ★`_Probe.decide` は返り値を `cap["threats"]` に控えるが `_observe` の外へ出さない。
    そこで**もう一段だけ**包んで、脅威のラベル／条件note に「従者」が現れたかを控える
    （`_Probe` を書き換えない＝他レーンの計測を壊さない）。
    """

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        import agents.defense_plan as dp

        orig = dp.enumerate_threats
        hit = {"v": False}

        labs: set = set()
        allt: list = []

        def _rec(*a, **kw):
            r = orig(*a, **kw)
            if ALL_THREATS:
                allt[:] = [f"{getattr(t, 'kind', '?')}|{getattr(t, 'label', '')}"
                           f"|sev={float(getattr(t, 'severity', 0.0)):.3f}" for t in r]
            for t in r:
                blob = str(getattr(t, "label", "")) + " " + " ".join(
                    str(getattr(cd, "note", "") or "") + str(getattr(cd, "label", ""))
                    for cd in (getattr(t, "conditions", ()) or ()))
                if "従者" in blob:
                    hit["v"] = True
                    labs.add(f"{getattr(t, 'kind', '?')}|{getattr(t, 'label', '')}")
            return r

        dp.enumerate_threats = _rec
        try:
            chosen = super().decide(view, decision, options)
        finally:
            dp.enumerate_threats = orig
        if self.jseats:
            self.jseats[-1]["threat_juusha"] = hit["v"]
            self.jseats[-1]["threat_juusha_labels"] = sorted(labs)
            if ALL_THREATS:
                self.jseats[-1]["all_threats"] = list(allt)
        return chosen


# ---------------------------------------------------------------------------
# 棋譜からの読み取り（推定を混ぜない）
# ---------------------------------------------------------------------------
def _substitutions(state) -> list[dict]:
    """身代わりの発動（`sim/effects.py:104` の秘匿イベント）。"""
    return [{"loop": e.get("loop"), "day": e.get("day"),
             "protected": e.get("protected"), "cause": str(e.get("cause") or "")}
            for e in state.secret_log if e.get("event") == "juusha_substitute"]


def _follows(state) -> list[dict]:
    """追随（`sim/effects.py:218`＝行動解決フェイズで公開される従者の move）。"""
    return [{"loop": e.get("loop"), "day": e.get("day"), "to": e.get("to")}
            for e in state.history
            if e.get("event") == "move" and e.get("name") == "従者"
            and e.get("phase") == "action_resolution"]


def _prot_juusha_moves(state) -> list[dict]:
    """主人公が従者へ置いた**移動札**（`cards_revealed`＝行動解決フェイズの公開）。"""
    out = []
    for e in state.history:
        if e.get("event") != "cards_revealed":
            continue
        for p in (e.get("placements") or ()):
            if (p.get("owner") in _PROT_SEATS and p.get("target") == "従者"
                    and p.get("target_kind") == "character"
                    and p.get("card") in MOVE_CARDS):
                out.append({"loop": e.get("loop"), "day": e.get("day"),
                            "owner": p.get("owner"), "card": p.get("card")})
    return out


def _juusha_gw4(state) -> list[dict]:
    """従者の友好4（特性対象の追加＝`sim/abilities.py:488` の公開イベント）。"""
    return [{"loop": e.get("loop"), "day": e.get("day"), "target": e.get("target")}
            for e in state.history if e.get("event") == "juusha_target_added"]


def _deaths(state) -> list[dict]:
    """全死亡（秘匿ログ＝原因つき）。"""
    return [{"loop": e.get("loop"), "day": e.get("day"), "name": e.get("name"),
             "cause": str(e.get("cause") or ""), "phase_hint": e.get("phase")}
            for e in state.secret_log if e.get("event") == "death"]


def _lost_loops_exact(state) -> set:
    """そのループが**敗北で終わった**か（最終ループは `loop_result` が出ない＝補う）。"""
    lost = set(_lost_loops(state))
    if getattr(state, "defeat", False):
        lost.add(state.loop_no)
    return lost


def _incident_firings(state) -> list[dict]:
    """発生した事件（公開）＋その事件フェイズの公開イベント列＋秘匿の犯人。"""
    pub: dict = {}
    for e in state.history:
        if e.get("phase") == "incident":
            pub.setdefault((e.get("loop"), e.get("day")), []).append(e)
    sec: dict = {}
    for e in state.secret_log:
        sec.setdefault((e.get("loop"), e.get("day")), []).append(e)
    out = []
    for key, evs in sorted(pub.items(), key=lambda x: (x[0][0] or 0, x[0][1] or 0)):
        head = next((e for e in evs if e.get("event") == "incident"), None)
        if head is None or not head.get("occurs"):
            continue
        s = sec.get(key) or []
        shead = next((e for e in s if e.get("event") == "incident"), None)
        out.append({"loop": key[0], "day": key[1], "name": head.get("name"),
                    "culprit": (shead or {}).get("culprit"), "evs": evs, "secs": s})
    return out


# ---------------------------------------------------------------------------
# (d) 自殺の D（直結）を **B-157 とは独立に**再現する
# ---------------------------------------------------------------------------
def _suicide_D(script, firing: dict) -> list:
    """B-157 §1-2 の D の定義（その事件フェイズ内で敗北条件そのものが成立）を独立に実装。

    ★自殺の効果は「犯人が死亡する」だけ（`rules/40_first_steps.md:150`）＝
    板の暗躍も KP 暗躍も動かない ∴ D の判定材料は **死亡系のイベントだけ**で足りる。
    """
    roles = {n: script.role_of(n) for n in script.cast}
    tags: list = []
    for e in firing["evs"]:
        ev = e.get("event")
        if ev == "death" and roles.get(e.get("name")) in _VIP_ROLES:
            tags.append(f"VIP死亡({e.get('name')}/{roles.get(e.get('name'))})")
        elif ev == "protagonist_death":
            tags.append("主人公死亡")
        elif ev == "loop_end":
            tags.append("ループ終了誘発")
    return sorted(set(tags))


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _JProbe2(seed)
    mm = HeuristicMastermind(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    roles = {n: script.role_of(n) for n in script.cast}
    snaps = _snap_index(state)
    lost = _lost_loops_exact(state)
    subs = _substitutions(state)
    fols = _follows(state)
    pmoves = _prot_juusha_moves(state)
    gw4 = _juusha_gw4(state)
    deaths = _deaths(state)
    firings = _incident_firings(state)

    # ---- 追随の日ごとの照合（主人公が従者へ移動札を置いていたか／剥がせたか）----
    pmove_days = {(m["loop"], m["day"]) for m in pmoves}
    for f in fols:
        f["prot_tried"] = (f["loop"], f["day"]) in pmove_days
    # 「引き剥がし成功」＝主人公が従者へ移動札を置いた日で、行動解決後に
    #   従者と主が別エリアになった（＝追随が起きなかった or 主が動かなかった）
    for m in pmoves:
        s = snaps.get((m["loop"], m["day"], "行動解決フェイズ後")) or {}
        ch = s.get("characters") or {}
        ju = ch.get("従者") or {}
        added = {e["target"] for e in gw4 if e["loop"] == m["loop"]}
        ms = [n for n in (set(_BASE_MASTERS) | added) if n in ch]
        same = [n for n in ms if (ch.get(n) or {}).get("alive", True)
                and (ch[n] or {}).get("area") == ju.get("area")]
        m["separated"] = (not same)
        m["snap_ok"] = bool(s)

    # ---- (b) 身代わりの分類 -------------------------------------------------
    for s in subs:
        s["ju_role"] = roles.get("従者")
        s["protected_role"] = roles.get(s["protected"])
        s["ju_is_vip"] = roles.get("従者") in _VIP_ROLES
        s["protected_is_vip"] = roles.get(s["protected"]) in _VIP_ROLES
        s["loop_lost"] = s["loop"] in lost

    # ---- (d) 自殺の D ------------------------------------------------------
    sui = []
    for f in firings:
        if f["name"] != "自殺":
            continue
        row = {"loop": f["loop"], "day": f["day"], "culprit": f["culprit"],
               "culprit_role": roles.get(f["culprit"]),
               "kuroneko": f["culprit"] == "黒猫",
               "loop_lost": f["loop"] in lost,
               "D": _suicide_D(script, f)}
        row["substituted"] = any(x.get("event") == "juusha_substitute"
                                 for x in f["secs"])
        row["deaths"] = [e.get("name") for e in f["evs"] if e.get("event") == "death"]
        sui.append(row)

    # ---- (f) 射程O′＝主が死んだ場面で従者が生存していた（＝身代わりが起きなかった）----
    # ★これは**厳密**である＝`sim/effects.kill_character` は身代わり判定を死亡処理の**前**に
    #   置く（`sim/effects.py:97-106`）ので、「主が身代わりなしで死んだ」なら
    #   **その瞬間に従者が『生存 かつ 盤上 かつ 同エリア』ではなかった**ことが確定する。
    #   従者の生死はこのループの従者の death イベントの有無で厳密に決まる。
    #   位置（どのエリアに居たか）は**報告用**にフェイズ境界スナップから近似して添える。
    oprime = []
    masters_all = set(_BASE_MASTERS) & set(script.cast)
    ju_dead_at = {}
    for d in deaths:
        if d["name"] == "従者":
            ju_dead_at.setdefault(d["loop"], d["day"])
    for d in deaths:
        if d["name"] not in masters_all:
            continue
        dd = ju_dead_at.get(d["loop"])
        if dd is not None and dd <= (d["day"] or 0):
            continue                      # その時点で従者は既に死亡＝射程外
        key = None
        for pt in ("主人公能力フェイズ後", "行動解決フェイズ後", "主人公行動フェイズ後"):
            if (d["loop"], d["day"], pt) in snaps:
                key = (d["loop"], d["day"], pt)
                break
        ch = ((snaps.get(key) or {}).get("characters") or {})
        ju = ch.get("従者") or {}
        if not ju.get("area"):
            continue                      # 盤上に居ない（未登場等）＝射程外
        oprime.append({**d, "ju_area": ju.get("area"),
                       "master_area": (ch.get(d["name"]) or {}).get("area"),
                       "ju_role": roles.get("従者"),
                       "ju_is_vip": roles.get("従者") in _VIP_ROLES,
                       "master_is_vip": roles.get(d["name"]) in _VIP_ROLES,
                       "snap_point": key[2] if key else None})

    return {"outcome": _outcome(state), "n_loops": state.loop_no,
            "lost_loops": sorted(lost), "roles": roles,
            "subs": subs, "follows": fols, "prot_moves": pmoves, "gw4": gw4,
            "suicide": sui, "oprime": oprime, "jseats": hp.jseats,
            "cast": sorted(script.cast)}


# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    scripts: set = set()
    games: set = set()
    ex: list = []
    seat_rows: list = []

    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts.add(name)
        games.add((name, seed))
        has_j = "従者" in sc.cast
        if has_j:
            c["A1 従者が配役に居る局"] += 1
            ju_vip = sc.role_of("従者") in _VIP_ROLES
            has_m = bool(set(_BASE_MASTERS) & set(sc.cast))
            if ju_vip:
                c["A2 うち従者=KP/フレンド（VIP従者）"] += 1
            if has_m:
                c["A3 うち主（お嬢様/大物）も配役＝特性が発火しうる"] += 1
            if ju_vip and has_m:
                c["A4 ★A2∧A3＝ユーザー報告の型"] += 1
        has_sui = any(i.name == "自殺" for i in sc.incidents)
        if has_sui:
            c["A8 自殺が事件表にある局（(d) の母数）"] += 1
        # ★対局するのは「従者が居る局」＋「自殺が予定されている局」だけ。
        #   従者の特性は従者が居ないと原理的に発火せず、自殺の D は自殺が無いと原理的に立たない
        #   ＝**射程ゼロが構造的に保証される局を落としている**（計算の節約であって恣意ではない）。
        if not (has_j or has_sui):
            continue
        res = audit_game(sc, seed, loops=loops)
        c["対局した局（従者あり or 自殺あり）"] += 1
        if has_j:
            c["　うち従者あり"] += 1
            c[f"　結末（従者あり）＝{res['outcome']}"] += 1

        # ---- (b) --------------------------------------------------------
        for s in res["subs"]:
            c["B1 身代わりの発動"] += 1
            if s["ju_is_vip"]:
                c["B2 ★うち従者がVIP＝その場で敗北条件成立（主人公の損）"] += 1
            if s["protected_is_vip"]:
                c["B3 ★うち守られた主がVIP＝主人公の得（身代わりが防御になった）"] += 1
            if s["loop_lost"]:
                c["B4 うち発動回にループが敗北（相関）"] += 1
            if len(ex) < 60:
                ex.append({"kind": "sub", "script": name, "seed": seed, **s})
        # ---- (c) --------------------------------------------------------
        for f in res["follows"]:
            c["C1 追随の発動"] += 1
            if f["prot_tried"]:
                c["C2 ★うち同じ日に主人公が従者へ移動札を置いていた（自身への移動が無視された）"] += 1
        for m in res["prot_moves"]:
            c["C3 主人公が従者へ移動札を置いた"] += 1
            if m["separated"]:
                c["C4 うち解決後に従者と主が別エリア（引き剥がし成立）"] += 1
        for g in res["gw4"]:
            c["e3 従者の友好4（特性対象の追加）が使われた"] += 1
            if len(ex) < 60:
                ex.append({"kind": "gw4", "script": name, "seed": seed, **g})
        # ---- (d) --------------------------------------------------------
        for s in res["suicide"]:
            c["D0 自殺の発生（★全ての自殺予定局が母数＝B-157 と同じ）"] += 1
            if s["kuroneko"]:
                c["D0k うち黒猫が犯人＝効果は何も起きない"] += 1
                continue
            if s["D"]:
                c["D1 ★自殺の D（直結）"] += 1
            if s["substituted"]:
                c["D2 ★うち従者の身代わりが挟まった"] += 1
            if len(ex) < 60:
                ex.append({"kind": "sui", "script": name, "seed": seed, **s})
        # ---- (f) --------------------------------------------------------
        for o in res["oprime"]:
            c["F1 主が死亡した場面で従者が生存・別エリア（O′の母数）"] += 1
            if o["ju_is_vip"]:
                c["F2 ★うち従者がVIP＝寄っていれば脚本家が勝てた（脚本家側のO′）"] += 1
            if o["master_is_vip"] and not o["ju_is_vip"]:
                c["F3 ★うち主がVIPで従者が非VIP＝寄せていれば主人公が救えた（主人公側のO′）"] += 1
            if len(ex) < 60:
                ex.append({"kind": "oprime", "script": name, "seed": seed, **o})
        # ---- (e) 席単位 --------------------------------------------------
        true_sui_days = {i.day for i in sc.incidents
                         if i.name == "自殺" and i.culprit in _BASE_MASTERS}
        for r in res["jseats"]:
            if r.get("error"):
                c["★席プローブの例外"] += 1
                continue
            if not r.get("has_juusha"):
                continue
            c["E0 set_card 席（従者が配役）"] += 1
            if not r.get("ju_alive"):
                continue
            c["E0a うち従者が生存・盤上"] += 1
            h1 = bool(r.get("co_located"))
            if h1:
                c["E1 ★危険席H1＝従者と主が同エリア（身代わりが今その場で成立しうる）"] += 1
                if res["roles"].get("従者") in _VIP_ROLES:
                    c["E1a ★★うち従者がVIP（＝主人公の損の席）"] += 1
                if any(res["roles"].get(n) in _VIP_ROLES for n in r["co_located"]):
                    c["E1b うち主がVIP（＝身代わりが防御になる席）"] += 1
                if r.get("opt_move_juusha"):
                    c["E1-e1 ★うち options に**従者へ置ける移動札**があった"] += 1
                if r.get("chosen") and r["chosen"][1] == "従者" \
                        and r["chosen"][0] in MOVE_CARDS:
                    c["E1-e1打 ★★うち実際に従者へ移動札を打った"] += 1
                if r.get("threat_juusha"):
                    c["E1-T ★★★うち脅威表に「従者」が現れた"] += 1
                    for lb in (r.get("threat_juusha_labels") or ()):
                        c[f"　　脅威ラベル＝{lb[:56]}"] += 1
            h2 = bool(true_sui_days and any(
                d >= (r.get("day") or 1) for d in true_sui_days))
            if h2:
                c["E2 ★危険席H2＝今日以降に自殺が予定・真犯人が主"] += 1
                if r.get("opt_cool"):
                    c["E2-e2 ★うち options に 不安-1 があった"] += 1
                if any(t in _BASE_MASTERS for t in (r.get("opt_cool") or ())):
                    c["E2-e2m ★★うち 不安-1 を**主（真犯人）**へ置けた"] += 1
                if (r.get("chosen") and r["chosen"][0] == "不安-1"
                        and r["chosen"][1] in _BASE_MASTERS):
                    c["E2-e2打 ★★★うち実際に主へ 不安-1 を打った"] += 1
                if r.get("threat_juusha"):
                    c["E2-T うち脅威表に「従者」が現れた"] += 1
            if h1 or h2:
                seat_rows.append({"script": name, "seed": seed, **r})
        if verbose:
            print(f"  {name} s{seed}: 身代わり{len(res['subs'])} 追随{len(res['follows'])}"
                  f" 自殺{len(res['suicide'])} 結末={res['outcome']}", flush=True)

    return {"days": days, "n_games": len(games), "n_scripts": len(scripts),
            "scripts": sorted(scripts), "counts": dict(c),
            "examples": ex, "seat_rows": seat_rows[:400]}


# ---------------------------------------------------------------------------
def census(days: int) -> dict:
    """(a) 配役の数え上げ（★対局不要）。"""
    from arena.benchmark import benchmark_scripts

    rows = list(benchmark_scripts(days=days))
    c: Counter = Counter()
    names: set = set()
    with_j: set = set()
    for name, _seed, s in rows:
        names.add(name)
        c["局"] += 1
        if "従者" not in s.cast:
            continue
        with_j.add(name)
        c["A1 従者が配役"] += 1
        c[f"　従者の役職＝{s.role_of('従者')}"] += 1
        ju_vip = s.role_of("従者") in _VIP_ROLES
        ms = sorted(set(_BASE_MASTERS) & set(s.cast))
        if ju_vip:
            c["A2 ★従者=KP/フレンド"] += 1
        if ms:
            c["A3 ★主（お嬢様/大物）も配役"] += 1
            c[f"　　主＝{'/'.join(ms)}"] += 1
        if ju_vip and ms:
            c["A4 ★★A2∧A3＝ユーザー報告の型"] += 1
        sui = [i for i in s.incidents if i.name == "自殺"]
        if sui:
            c["A5 従者あり かつ 自殺が事件表にある"] += 1
            if any(i.culprit in _BASE_MASTERS for i in sui):
                c["A6 ★★★自殺の犯人が主＝B-153×B-157 の交点"] += 1
            if any(i.culprit == "従者" for i in sui):
                c["A7 自殺の犯人が従者自身"] += 1
    print(f"== B-153 census：{days}日級 {len(rows)}局・★独立脚本 {len(names)} 本 ==")
    print(f"  従者を含む脚本＝{sorted(with_j) or 'なし'}")
    for k, v in c.items():
        print(f"  {k:52s} {v}")
    return {"days": days, "counts": dict(c), "n_games": len(rows),
            "n_scripts": len(names)}


# ---------------------------------------------------------------------------
def autopsy(days: int, script_name: str, seed: int, loops: int = 8,
            loop_filter: tuple = ()) -> None:
    """★1局の検死＝「主人公が何を見て、何を打てて、何を打ったか」を全席印字する。"""
    from arena.benchmark import benchmark_scripts

    global ALL_THREATS
    rows = [r for r in benchmark_scripts(days=days)
            if r[0] == script_name and r[1] == seed]
    if not rows:
        print(f"該当なし: {script_name} s{seed} ({days}日級)")
        return
    sc = rows[0][2]
    ALL_THREATS = True
    try:
        res = audit_game(sc, seed, loops=loops)
    finally:
        ALL_THREATS = False
    print(f"== 検死 {script_name} s{seed}（{days}日級）==")
    print(f"  cast   = {', '.join(res['cast'])}")
    print(f"  roles  = {res['roles']}")
    print(f"  事件   = " + " / ".join(
        f"{i.day}日 {i.name} 犯人={i.culprit}" for i in sc.incidents))
    print(f"  結末   = {res['outcome']}（{res['n_loops']}ループ）"
          f" 敗北ループ={res['lost_loops']}")
    print(f"  身代わり= {res['subs']}")
    print(f"  追随    = {res['follows']}")
    print(f"  自殺    = {[{k: v for k, v in s.items()} for s in res['suicide']]}")
    print("")
    for r in res["jseats"]:
        if loop_filter and r.get("loop") not in loop_filter:
            continue
        if r.get("error"):
            print(f"  L{r.get('loop')}D{r.get('day')} {r.get('seat')} 例外={r['error']}")
            continue
        print(f"  L{r['loop']}D{r['day']} {r['seat']}"
              f" 従者@{r.get('ju_area')} 同室主={r.get('co_located')}"
              f" 追加対象={r.get('added')}")
        print(f"      打てた従者移動札={r.get('opt_move_juusha')}"
              f" / 冷却できた相手={r.get('opt_cool')}")
        print(f"      実際に打った手={r.get('chosen')}")
        for t in (r.get("all_threats") or ())[:6]:
            print(f"      脅威: {t}")


def _verify_game(script, seed: int, loops: int = 8) -> tuple:
    """プローブ有無で棋譜が一致するか（★挙動不変の物証）。"""
    probe = replace(script, loops=loops)
    a = _JProbe2(seed)
    sa, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
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
            f" / DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" / B134_CARD_DISTANCE={dp.B134_CARD_DISTANCE}"
            f" / B127_ANYAKU_TARGETING={dp.B127_ANYAKU_TARGETING}"
            f" / B159_MISSING_BOARD={dp.B159_MISSING_BOARD}"
            f" / B161_COOL_COST={dp.B161_COOL_COST}"
            f" / ★B153_SUICIDE={dp.B153_SUICIDE}"
            f" / ★B153_PLAN_MOVE_KIND={H.B153_PLAN_MOVE_KIND}"
            f" / B153_JUUSHA_MOVE_COST={dp.B153_JUUSHA_MOVE_COST}"
            f" / B153_COOL_COST={dp.B153_COOL_COST}"
            f" / days={days} loops={loops}")


def report(res: dict, days: int) -> None:
    print(f"== B-153 Phase 1：従者の特性と自殺の射程（{days}日級 {res['n_games']}局"
          f"・★独立脚本 {res['n_scripts']} 本）==")
    print(f"  脚本: {', '.join(res['scripts'])}")
    for k, v in res["counts"].items():
        print(f"  {k:64s} {v}")
    if res.get("examples"):
        print("\n  ★現物（先頭20）")
        for e in res["examples"][:20]:
            print(f"    {e}")
    rows = res.get("seat_rows") or []
    print(f"\n  ★危険席の現物（{len(rows)} 件のうち先頭12）")
    for r in rows[:12]:
        print(f"    {r['script']} s{r['seed']} L{r['loop']}D{r['day']} {r['seat']}"
              f" 従者@{r.get('ju_area')} 同室={r.get('co_located')}"
              f" 移動札={r.get('opt_move_juusha')} 打={r.get('chosen')}"
              f" 脅威に従者={r.get('threat_juusha')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "census", "merge", "autopsy"])
    ap.add_argument("--script", default="random_FS")
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--loop", type=int, nargs="*", default=())
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inputs", nargs="*", default=())
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--on", action="store_true",
                    help="★B-153 の切替口を ON にして計測する"
                         "（B153_SUICIDE / B153_PLAN_MOVE_KIND）")
    ap.add_argument("--move-cost", type=float, default=None)
    ap.add_argument("--cool-cost", type=float, default=None)
    a = ap.parse_args(argv)
    if a.on:
        import agents.defense_plan as _dp
        _dp.B153_SUICIDE = True
        HeuristicProtagonist.B153_PLAN_MOVE_KIND = True
        if a.move_cost is not None:
            _dp.B153_JUUSHA_MOVE_COST = a.move_cost
        if a.cool_cost is not None:
            _dp.B153_COOL_COST = a.cool_cost
    if a.cmd == "census":
        census(a.days)
        return 0
    print(_switches(a.days, a.loops), flush=True)
    if a.cmd == "autopsy":
        autopsy(a.days, a.script, a.seed, loops=a.loops,
                loop_filter=tuple(a.loop))
        return 0
    if a.cmd == "verify":
        from arena.benchmark import benchmark_scripts

        bad = 0
        rows = list(benchmark_scripts(days=a.days))
        rows = [r for r in rows if "従者" in r[2].cast][
            a.start:(a.end if a.end is not None else 12)]
        for name, seed, sc in rows:
            ok, oa, ob = _verify_game(sc, seed, loops=a.loops)
            if not ok:
                bad += 1
                print(f"  ✗ {name} s{seed}: probe={oa} plain={ob}")
        print(f"棋譜の不一致 = {bad} 件 / {len(rows)}局（★従者を含む局だけを検査）")
        return 1 if bad else 0
    if a.cmd == "merge":
        parts = []
        for p in a.inputs:
            with open(p, encoding="utf-8") as f:
                parts.append(json.load(f))
        res = {"days": parts[0]["days"], "counts": dict(Counter()), "examples": [],
               "seat_rows": [], "scripts": set(), "n_games": 0, "n_scripts": 0}
        cc: Counter = Counter()
        for p in parts:
            cc.update(p.get("counts") or {})
            res["examples"].extend(p.get("examples") or [])
            res["seat_rows"].extend(p.get("seat_rows") or [])
            res["scripts"].update(p.get("scripts") or [])
            res["n_games"] += int(p.get("n_games") or 0)
        res["counts"] = dict(cc)
        res["scripts"] = sorted(res["scripts"])
        res["n_scripts"] = len(res["scripts"])
    else:
        res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
                  verbose=a.verbose)
    report(res, a.days)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
