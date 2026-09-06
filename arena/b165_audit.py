# -*- coding: utf-8 -*-
"""B-165 Phase 1：**同型の過大主張（追随・身代わりで崩れない折り手）の横断点検**（★計測のみ・`agents/` 非接触）。
   ＋ B-166（**主人公が従者を「盾」として使う筋**＝射程O′）を**同じ表で**数える。

## 発端

`docs/バックログ_構想メモ_FableA.md` §41（B-165）／§42（B-166）＝B-153 の回付。
B-153 が是正したのは `agents/defense_plan._sk_pair_threats` の**2分岐だけ**であり、
**他の脅威族に同型が残っていないかは未確認**。

KB＝`rules/30_characters.md:65`（現物カード転記 2026-07-23）：
- **追随**＝「同一エリアのお嬢様か大物が**移動する場合、自身への移動を無視して一緒に移動する**」
- **身代わり**＝「同一エリアのお嬢様か大物が**死亡する場合、代わりに死亡する**」（主は生存・強制）
- 友好4(1/L)＝ボードの自身以外1人を**特性の対象に追加**（`rules/20_goodwill_abilities.md:323`・そのループ中）

## ★数え上げの定義（先に固定する・後から変えない）

**単位**＝**席**（seat）＝主人公の `set_card` 決定1回で `plan_for_belief` が走った回。
母数の局は「**従者が配役に居る局**」だけ（3日級10局／5日級9局・B-153 実測）。
従者が居ない局では `juusha_drags` が**恒真で False**＝**射程ゼロが構造的に保証される**
（計算の節約であって恣意的な選別ではない）。独立脚本数を必ず併記する。

### 追随 X 系（折り手の過大主張＝「打っても条件が崩れない」）

| 記号 | 定義 |
|---|---|
| **X1** | 「A と B を**引き離す**」型の条件の折り手として **A に移動札**を出しているが、`juusha_drags(view, A)`＝True（A が特性対象・従者が同エリアに生存）で、**相方 B が従者**＝A を動かしても従者が追随＝**条件が崩れない**。★B-153 が `_sk_pair_threats` で消したのと同型。 |
| **X2** | **移動禁止→従者**を「留めるピン」として出しているが、同エリアに生存する特性対象が居る。`rules/10_action_cards.md:60`＝移動禁止は**重なった移動カードしか無効化しない**＝**追随は止まらない**（sim も同じ＝`sim/flow.py:228` の `apply_juusha_follow` は `adj.moves` 適用の**後**・移動禁止を参照しない）。 |
| **X3** | **移動札→従者**（＝B-153 が「正しい折り手」とした側）を出していて、同エリアに特性対象が生存し、**脚本家がその特性対象に札を伏せている**（`_mm_movers`）＝主が動けば従者自身の移動は無視される。★★**これは同型では「ない」**＝`sim/effects.apply_juusha_follow` は主が**実際に動いた**時だけ発火し、従者を**主の行き先**へ移す ∴ **従者は当該エリアから必ず出る**＝折り手は無効化されない。実害は「**行き先が主人公の選択でなくなる**」＝DP-4 が入れた退避先の安全判定（`_noop_char` の G5/G6・`avoid_areas`）が効かない、という**弱い所見**にとどまる。**過大に読まないこと**。 |

★X1/X2/X3 とも「**その席で生成された Break**」を数え、あわせて
(a) `cheapest_breaks` に残ったか／(b) `plan.picks` に入ったか／(c) 加点されたか（`_plan_recs`）／
(e) 絶対防御が強制したか（`_b100_log`）／(d) 実際に打たれたか を**同じ行で**印字する
（B-159／B-161 の教訓＝「Break を足す/消すだけで終わらせない」）。

### 身代わり Y 系（脅威側の過大主張／見落とし）

| 記号 | 定義 |
|---|---|
| **Y1** | 致命脅威の**名指しの被害者 V** が `juusha_drags(view, V)`＝True＝**V は死なない**（従者が代わりに死ぬ）。Y1a＝従者が VIP 疑い（従者が死ぬ＝結局敗北＝ラベル違いにすぎない）／**Y1b＝従者が VIP 疑いでない**＝**真の過大主張**（脅威そのものが成立しない）。 |
| **Y1c** | 参考＝2人きり族（`kp_sk`/`virus_sk`）で V が特性対象・従者が同室＝**そもそも2人きりが成立していない**（従者は3人目）＝別機序の空振り。 |
| **Y2** | 逆向き＝**従者が VIP 疑い**で、同エリアに特性対象 V が生存＝**身代わりで従者（VIP）が死にうる**席。**Y2p**＝V を名指しする致命脅威が立っている（＝**代理被覆**＝折り手は同じものが出る＝**見落としではない**）／**Y2j**＝従者自身を名指しする致命脅威が立っている／**★Y2m**＝**どちらも無い**＝身代わりで従者が死ぬ経路が脅威表に**存在しない**＝**真の見落とし**。 |

### B-166＝射程O′（主人公が従者を「盾」に使う筋）

| 記号 | 定義 |
|---|---|
| **O1** | 致命脅威の被害者 V が**特性対象（お嬢様/大物/友好4追加）**で、**従者が生存・盤上・別エリア**、かつ **options に「従者を V のエリアへ運べる移動札」がある**席＝**その席で盾を差し込めた**。 |
| **O1v** | ★**O1 のうち V が VIP 疑い**（＝守る価値がある）。**B-153 の両刃**（3日級の身代わり1件は従者〈非VIP〉が脚本家資産のお嬢様〈キラー〉を守った）を数に入れるため、**述語は必ずここで絞る**（規約 §12）。 |
| **O1x** | O1 のうち **従者自身が VIP 疑い**＝盾に使うと VIP を自分で捨てる＝**やってはいけない**席（別掲）。 |
| **O2** | O1v のうち、**その脅威が実際にそのループで発火して V が死亡した**（＝盾が効いたはずの）席。 |
| **★O3** | ★**既に成立している盾を維持できた席**（`random_FS` s18 の検死で見つかった型）＝従者（**VIP 疑いでない**）が **VIP 疑いの特性対象と同エリア**＝盾が立っている ∧ **脚本家が従者へ札を伏せている**（`_mm_movers`＝盾を剥がしに来ている）∧ **options に `移動禁止→従者` がある**。`rules/10_action_cards.md:60`＝移動禁止は重なった移動札を無効化する＝**盾を維持できる**。 |
| **O4** | O3 のうち、実際にその日**従者が動いて盾が剥がれ**、かつ**そのループでその VIP が死亡した**席。 |

★**O′ は「その場で」の手ではない**（B-153 §1 の注と同じ）＝1手前の配置の話。**必ず O′ と明記して読む**。

## 二重実装をしない（再利用）

- `agents.defense_plan.juusha_drags` / `juusha_targets_from_view` / `_mm_movers` / `_suspects` /
  `_vip_suspects` / `_char` / `_alive` / `_own_move_dest` / `_pick_for` を**そのまま import**。
- 計画スタッシュ `self._b100_plan`・加点 `self._plan_recs`・絶対防御 `self._b100_log` の読み方は
  `arena/b161_audit._FunnelProbe` と**同じ作法**（`super().decide()` の戻り値をそのまま返す＝挙動不変）。
- 結末判定は `arena/b145_audit._outcome`／敗北ループは `_lost_loops`。
- 死亡・身代わりの棋譜読み取りは `arena/b153_audit._deaths` / `_substitutions`。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b165_audit verify --days 3            # ★挙動不変（棋譜完全一致）
    python -m arena.b165_audit count  --days 3
    python -m arena.b165_audit count  --days 5 --json d5.json
    python -m arena.b165_audit sites                      # 静的＝Break 生成箇所の全数走査
    python -m arena.b165_audit cf --days 5 --script random_FS --seed 18 --loop 1 --day 3
                                                          # ★B-166 の反実仮想（盾の維持）
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.defense_plan as DP
from agents import HeuristicMastermind, HeuristicProtagonist
from agents.defense_plan import _pick_for
from arena.b145_audit import _lost_loops, _outcome, _snap_index
from arena.b153_audit import _deaths, _substitutions
from engine.models import MOVE_CARDS
from sim import run_game

#: ループ終了時の敗北に直結する役職（`sim/effects.evaluate_loop_end` / `kill_character`）。
_VIP_ROLES = ("キーパーソン", "フレンド")

#: ★「A と B を引き離す」型の条件を持つ脅威族（`agents/defense_plan.py` の全 Break 生成箇所を
#   走査して確定＝`sites` サブコマンドが同じ表を印字する）。
_PAIR_KINDS = {"kp_killer": "キラー", "factor_kp": "キラー",
               "kp_sk": "シリアルキラー", "virus_sk": None}

#: 「留めるピン」＝移動禁止の Break を出す脅威族。
_PIN_KINDS = ("kp_sk", "virus_sk", "sk_setup")


def _live_masters_with(view: dict, name: str) -> list:
    """`name` と同エリアに生存する**特性対象**（お嬢様/大物/友好4追加。`name` 自身は除く）。"""
    tgt = DP.juusha_targets_from_view(view)
    c = DP._char(view, name)
    if not c or not c.get("area"):
        return []
    return sorted(n for n in tgt
                  if n != name and DP._alive(view, n)
                  and (DP._char(view, n) or {}).get("area") == c.get("area"))


def _victim_of(view: dict, t) -> str | None:
    """脅威 t が「死ぬ」と名指ししている当人（無ければ None）。

    ★ラベルの**書式そのもの**で同定する（`agents/defense_plan.py` の f-string と1対1）。
    推測を混ぜない＝一致しなければ None を返す。
    """
    lab = str(getattr(t, "label", ""))
    kind = str(getattr(t, "kind", ""))
    for c in (view.get("characters") or ()):
        n = c.get("name")
        if not n:
            continue
        if kind == "kp_killer" and lab.startswith(f"キラーによる{n}殺害（"):
            return n
        if kind == "factor_kp" and lab.startswith(f"KP能力獲得ファクター{n}＝"):
            return n
        if kind in ("kp_sk", "virus_sk") and f"による{n}（" in lab and "2人きり" in lab:
            return n
        if kind == "incident_vip" and f"による{n}殺害（" in lab:
            return n
        if kind == "remote_murder_vip" and lab.startswith(f"遠隔殺人による{n}殺害"):
            return n
        if kind == "sk_setup" and f"による{n}の2人きり仕込み" in lab:
            return n
    return None


def _bkey(b) -> tuple:
    return (b.card, b.target, b.target_kind)


class _B165Probe(HeuristicProtagonist):
    """X1/X2/X3・Y1/Y2・O′ を席単位で数える（★`super().decide()` の戻り値をそのまま返す）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.rows: list = []          # ★現物（1行＝1 Break もしくは1脅威）
        self._roles: dict = {}

    # -- roles（belief の役職周辺確率）を本番の呼び出しから控える ---------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        orig = DP.enumerate_threats
        cap: dict = {}

        def _rec(v, roles, *a, **kw):
            r = orig(v, roles, *a, **kw)
            cap["roles"] = roles       # ★最後の呼び出し＝本番の引数
            return r

        DP.enumerate_threats = _rec
        n_log = len(getattr(self, "_b100_log", []) or [])
        try:
            chosen = super().decide(view, decision, options)
        finally:
            DP.enumerate_threats = orig
        try:
            self._observe(view, options, chosen, cap.get("roles") or {}, n_log)
        except Exception as e:                          # noqa: BLE001
            self.c[f"★観測例外 {type(e).__name__}"] += 1
        return chosen

    # ------------------------------------------------------------------
    def _observe(self, view, options, chosen, roles, n_log) -> None:
        self.c["席（set_card 決定）"] += 1
        stash = getattr(self, "_b100_plan", None)
        if not stash:
            return
        threats, plan = stash
        ju = DP._char(view, "従者")
        ju_live = bool(ju and ju.get("alive", True) and ju.get("area"))
        vips = DP._vip_suspects(view, roles)
        ju_vip = "従者" in vips
        if not ju_live:
            return
        self.c["従者が生存・盤上の席"] += 1
        picked = {_bkey(b) for b in plan.picks}
        recs = set((getattr(self, "_plan_recs", None) or {}).keys())
        new_log = (getattr(self, "_b100_log", []) or [])[n_log:]
        forced = {(e.get("card"), e.get("target"), e.get("target_kind"))
                  for e in new_log}
        ckey = (chosen.get("card"), chosen.get("target"), chosen.get("target_kind"))
        movers = DP._mm_movers(view)
        base = {"loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat")}

        def _stage(b) -> str:
            k = _bkey(b)
            return ("cheapest" if any(_bkey(x) == k for t in threats
                                      for x in t.cheapest_breaks()) else "-") \
                + ("/picks" if k in picked else "") \
                + ("/加点" if k in recs else "") \
                + ("/強制" if k in forced else "") \
                + ("/★打った" if k == ckey else "")

        ju_masters = _live_masters_with(view, "従者")
        threat_names = " ".join(str(getattr(t, "label", "")) for t in threats)

        for t in threats:
            kind = str(getattr(t, "kind", ""))
            victim = _victim_of(view, t)
            # ---- 追随 X1（A を動かす折り手が従者を引き剥がせない）--------------
            if kind in _PAIR_KINDS:
                role = _PAIR_KINDS[kind]
                if role:
                    # ★相方＝**その脅威の加害側**。`juusha_drags` が「従者が mover と同エリア」を
                    #   保証するので、従者がその役職の容疑者なら**まさに builder が使った
                    #   `near`/`sk_here` の1人**＝相方である（同定は厳密）。
                    ju_is_counterpart = "従者" in DP._suspects(roles, role)
                else:
                    # virus_sk＝builder と同じ述語（パーソン疑い ∧ 不安≥2）を使う。
                    _p = (roles.get("従者", {}) or {}).get("パーソン", 0.0)
                    _u = (ju or {}).get("unrest", 0)
                    ju_is_counterpart = (_p >= DP._SUSPECT_P and _u >= 2)
                for cd in (t.conditions or ()):
                    for b in (cd.breaks or ()):
                        if b.card not in MOVE_CARDS or b.target_kind != "character":
                            continue
                        if b.target == "従者":
                            continue
                        if not DP.juusha_drags(view, b.target):
                            continue
                        # 相方が従者か（＝この移動では引き離せない）
                        if not ju_is_counterpart:
                            self.c["[X1参考] 特性対象を動かす折り手（相方は従者でない）"] += 1
                            continue
                        self.c[f"★X1 追随で崩れない引き離し（{kind}）"] += 1
                        self.rows.append({**base, "cat": "X1", "kind": kind,
                                          "threat": str(t.label),
                                          "sev": round(float(t.severity), 3),
                                          "break": str(b.label), "card": b.card,
                                          "target": b.target, "stage": _stage(b)})
            # ---- X1' sk_setup（別エリア型）＝相方を**ラベルから厳密に同定**して数える ----
            #   構造的にゼロのはず（`_threat_sk_setup` は `sk_area == kp_area` を除外する＝
            #   従者が mover と同エリアなら従者は相方 sk ではありえない）が、**主張は実測で示す**。
            if kind == "sk_setup":
                _sk = None
                for cc in (view.get("characters") or ()):
                    if str(t.label).startswith(f"SK{cc.get('name')}による"):
                        _sk = cc.get("name")
                for cd in (t.conditions or ()):
                    for b in (cd.breaks or ()):
                        if b.card in MOVE_CARDS and b.target_kind == "character" \
                                and b.target != "従者" and _sk == "従者" \
                                and DP.juusha_drags(view, b.target):
                            self.c["★X1' 追随で崩れない引き離し（sk_setup）"] += 1
                            self.rows.append({**base, "cat": "X1", "kind": kind,
                                              "threat": str(t.label),
                                              "break": str(b.label),
                                              "stage": _stage(b)})
            # ---- 身代わり Y1（脅威の被害者が身代わりで死なない）----------------
            if victim and victim != "従者" and DP.juusha_drags(view, victim) \
                    and getattr(t, "fatal", False):
                if kind in ("kp_sk", "virus_sk"):
                    tag = "Y1c 2人きり族＝従者が3人目で成立しない（参考）"
                elif ju_vip:
                    tag = "Y1a 身代わりで死ぬ従者も VIP 疑い＝結局敗北（ラベル違い）"
                else:
                    tag = "★Y1b 身代わりで被害者が死なない＝脅威そのものが過大主張"
                self.c[f"{tag}｜{kind}"] += 1
                self.rows.append({**base, "cat": tag.split()[0].lstrip("★"),
                                  "kind": kind, "threat": str(t.label),
                                  "sev": round(float(t.severity), 3),
                                  "victim": victim, "ju_vip": ju_vip})
            # ---- B-166 射程O′（従者を盾に差し込めた席）------------------------
            if victim and victim != "従者" and getattr(t, "fatal", False) \
                    and victim in DP.juusha_targets_from_view(view) \
                    and DP._alive(view, victim):
                varea = (DP._char(view, victim) or {}).get("area")
                if varea and varea != ju.get("area"):
                    cards = sorted({o["card"] for o in options
                                    if o.get("target") == "従者"
                                    and o.get("target_kind") == "character"
                                    and o.get("card") in MOVE_CARDS
                                    and DP._own_move_dest(view, "従者", o["card"]) == varea})
                    if cards:
                        self.c["O1 従者を盾に差し込めた席（延べ・脅威ごと）"] += 1
                        if ju_vip:
                            self.c["　O1x うち従者自身が VIP 疑い＝盾に使えない"] += 1
                        elif victim in vips:
                            self.c["　★O1v うち守る対象が VIP 疑い（B-166 の射程）"] += 1
                            self.rows.append({**base, "cat": "O1v", "kind": kind,
                                              "threat": str(t.label),
                                              "sev": round(float(t.severity), 3),
                                              "victim": victim, "varea": varea,
                                              "ju_area": ju.get("area"), "cards": cards})
                        else:
                            self.c["　O1w うち守る対象が VIP 疑いでない＝★両刃（守ると損）"] += 1

        # ---- 追随 X2/X3（従者そのものに置く折り手）--------------------------
        for t in threats:
            kind = str(getattr(t, "kind", ""))
            for cd in (t.conditions or ()):
                for b in (cd.breaks or ()):
                    if b.target != "従者" or b.target_kind != "character":
                        continue
                    if b.card == "移動禁止" and ju_masters:
                        movable = [m for m in ju_masters if m in movers]
                        key = ("★X2 移動禁止→従者のピンが追随を止められない"
                               if movable else
                               "[X2参考] 同上・ただし mm は主に札を伏せていない")
                        self.c[f"{key}｜{kind}"] += 1
                        self.rows.append({**base, "cat": "X2", "kind": kind,
                                          "threat": str(t.label),
                                          "break": str(b.label),
                                          "masters": ju_masters, "movable": movable,
                                          "stage": _stage(b)})
                    if b.card in MOVE_CARDS and ju_masters:
                        movable = [m for m in ju_masters if m in movers]
                        # ★X3 は「折り手が無効になる」型では**ない**（§後述）＝
                        #   `apply_juusha_follow` は主が**実際に動いた**時だけ発火し、
                        #   従者を**主の行き先**へ移す＝従者は当該エリアから**必ず出る**。
                        #   実害は「**行き先が主人公の選択でなくなる**」＝DP-4 が入れた
                        #   退避先の安全判定（`_noop_char` の G5/G6・`avoid_areas`）が効かない、
                        #   という**弱い所見**にとどまる。過大に読まないこと。
                        key = ("[X3] 移動→従者の行き先が主に上書きされうる（弱い所見）"
                               if movable else
                               "[X3参考] 同上・ただし mm は主に札を伏せていない")
                        self.c[f"{key}｜{kind}"] += 1
                        self.rows.append({**base, "cat": "X3", "kind": kind,
                                          "threat": str(t.label),
                                          "break": str(b.label),
                                          "masters": ju_masters, "movable": movable,
                                          "stage": _stage(b)})

        # ---- B-166 O3＝★**既に成立している盾を維持できた席** -------------------
        #   `random_FS` s18 の検死で見つかった型＝盾（従者が VIP 疑いの特性対象と同エリア）が
        #   既に立っているのに、**脚本家が従者へ移動札を伏せて盾を剥がしに来ている**席。
        #   `rules/10_action_cards.md:60`＝移動禁止は重なった移動札を無効化する＝
        #   **移動禁止→従者 で盾を維持できる**（従者は特性の追随を持つが、主が動かなければ動かない）。
        if not ju_vip:
            shield_vips = [m for m in ju_masters if m in vips]
            if shield_vips:
                self.c["O3母数 盾が既に成立（従者〈非VIP疑い〉と VIP 疑いの特性対象が同エリア）"] += 1
                if "従者" in movers:
                    self.c["　O3a うち mm が従者へ札を伏せている（盾を剥がしに来ている）"] += 1
                    if any(o.get("card") == "移動禁止" and o.get("target") == "従者"
                           and o.get("target_kind") == "character" for o in options):
                        self.c["　★O3 うち options に 移動禁止→従者 がある＝盾を維持できた席"] += 1
                        self.rows.append({**base, "cat": "O3",
                                          "shield_vips": shield_vips,
                                          "ju_area": ju.get("area")})

        # ---- 身代わり Y2（従者=VIP 疑いが身代わり圏に居るとき、脅威表が見えているか）----
        #   ★Y2p＝**代理被覆**＝脅威は「主が死ぬ」として立っている＝折り手は同じものが出る
        #     （ラベルが従者でないだけ）＝**見落としではない**。
        #   ★Y2m＝**真の見落とし**＝主を名指しする致命脅威も、従者を名指しする致命脅威も
        #     1本も無い＝身代わりで従者(VIP)が死ぬ経路が脅威表に**存在しない**。
        if ju_vip and ju_masters:
            self.c["Y2母数 従者(VIP疑い)が身代わり圏の席"] += 1
            nonvip_masters = [m for m in ju_masters if m not in vips]
            if nonvip_masters:
                self.c["　Y2母数のうち主が VIP 疑いでない（VIPを回る族が主を見ない）"] += 1
            targeted = [t for t in threats if getattr(t, "fatal", False)
                        and _victim_of(view, t) in ju_masters]
            named_ju = "従者" in threat_names
            if targeted:
                self.c["　Y2p 代理被覆＝主を名指しする致命脅威が立っている"] += 1
            elif named_ju:
                self.c["　Y2j 従者自身を名指しする致命脅威が立っている"] += 1
            else:
                self.c["　★Y2m 真の見落とし＝主も従者も名指しする致命脅威が無い"] += 1
                self.rows.append({**base, "cat": "Y2m", "masters": ju_masters,
                                  "nonvip_masters": nonvip_masters,
                                  "n_fatal": sum(1 for t in threats
                                                 if getattr(t, "fatal", False))})


# ---------------------------------------------------------------------------
def _switches(days: int, loops: int) -> str:
    H = HeuristicProtagonist
    return (f"[切替口] ★B153_JUUSHA_PAIR_BREAK={DP.B153_JUUSHA_PAIR_BREAK}"
            f" / B153_SUICIDE={DP.B153_SUICIDE}"
            f" / B153_PLAN_MOVE_KIND={H.B153_PLAN_MOVE_KIND}"
            f" / B153_JUUSHA_MOVE_COST={DP.B153_JUUSHA_MOVE_COST}"
            f" / B153_COOL_COST={DP.B153_COOL_COST}"
            f" / B159_MISSING_BOARD={DP.B159_MISSING_BOARD}"
            f" / B161_COOL_COST={DP.B161_COOL_COST}"
            f" / B161_UNREST_COEFF={H.B161_UNREST_COEFF}"
            f" / DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER}"
            f" / B100_MIX={H.B100_MIX} / B100_THETA={H.B100_THETA}"
            f" / days={days} loops={loops}")


def _games(days: int, start: int = 0, end: int | None = None):
    from arena.benchmark import benchmark_scripts
    return list(benchmark_scripts(days=days))[start:end]


def count(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    """★本計測。従者が配役に居る局だけを対局する（他は射程ゼロが構造的に保証される）。"""
    c: Counter = Counter()
    rows: list = []
    names: set = set()
    played: list = []
    for name, seed, sc in _games(days, start, end):
        names.add(name)
        if "従者" not in sc.cast:
            continue
        played.append((name, seed))
        hp = _B165Probe(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        roles = {n: sc.role_of(n) for n in sc.cast}
        lost = set(_lost_loops(state))
        if getattr(state, "defeat", False):
            lost.add(state.loop_no)
        subs = _substitutions(state)
        deaths = _deaths(state)
        c["対局した局（従者が配役）"] += 1
        c["　身代わりの発動（全ループ）"] += len(subs)
        for k, v in hp.c.items():
            c[k] += v
        for r in hp.rows:
            r["game"] = f"{name} s{seed}"
            r["ju_role"] = roles.get("従者")
            rows.append(r)
        # ---- 棋譜と突合（★「立った」と「実際に起きた」を分ける）------------------
        dead = {(d["loop"], d["name"]) for d in deaths}
        #   ★盾が実際に剥がれた (loop, day)＝**行動解決フェイズ後**のスナップショットで
        #     従者と当該 VIP が別エリアになっていること。
        #     （★通常の移動は history に `move` イベントを出さない＝`sim/flow.py:216-228` は
        #       `adj.moves` を無言で適用し、`move` を公開するのは **従者の追随**だけ。
        #       ∴ イベントで数えると必ず 0 になる＝**位置スナップショットで判定する**。）
        snaps = _snap_index(state)
        for r in rows:
            if r.get("game") != f"{name} s{seed}":
                continue
            r["loop_lost"] = r["loop"] in lost
            if r.get("cat") in ("O1v", "Y1b", "Y1a"):
                r["victim_died_in_loop"] = (r["loop"], r.get("victim")) in dead
            if r.get("cat") == "O3":
                ch = ((snaps.get((r["loop"], r["day"], "行動解決フェイズ後")) or {})
                      .get("characters") or {})
                ja = (ch.get("従者") or {}).get("area")
                r["ju_area_after"] = ja
                r["shield_broken_that_day"] = bool(ch) and any(
                    (ch.get(v) or {}).get("area") != ja for v in r.get("shield_vips") or ())
                r["shield_vip_died_in_loop"] = any(
                    (r["loop"], v) in dead for v in r.get("shield_vips") or ())
    def _n(cat, key):
        return sum(1 for r in rows if r.get("cat") == cat and r.get(key))
    c["　★O2 O1v のうち実際にその V がそのループで死亡した席"] = _n("O1v", "victim_died_in_loop")
    c["　O3b O3 のうち実際にその日盾が剥がれた席（行動解決フェイズ後に別エリア）"] = _n(
        "O3", "shield_broken_that_day")
    c["　★O4 O3 のうちその日盾が剥がれ、かつそのループでその VIP が死亡した席"] = sum(
        1 for r in rows if r.get("cat") == "O3"
        and r.get("shield_broken_that_day") and r.get("shield_vip_died_in_loop"))
    c["　★Y1b検証 うち実際にその V がそのループで死亡した席"] = _n("Y1b", "victim_died_in_loop")
    c["　 Y1a検証 うち実際にその V がそのループで死亡した席"] = _n("Y1a", "victim_died_in_loop")
    return {"days": days, "counts": dict(c), "rows": rows,
            "n_scripts": len(names), "n_played": len(played),
            "played": sorted(played)}


def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    """★挙動不変の物証＝プローブ有無で棋譜が完全一致するか（従者を含む局だけ）。"""
    bad, n = [], 0
    for name, seed, sc in _games(days, start, end):
        if "従者" not in sc.cast:
            continue
        probe = replace(sc, loops=loops)
        a = _B165Probe(seed)
        sa, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": a, "p2": a, "p3": a})
        b = HeuristicProtagonist(seed)
        sb, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": b, "p2": b, "p3": b})
        ha = [(e.get("loop"), e.get("day"), e.get("event")) for e in sa.history]
        hb = [(e.get("loop"), e.get("day"), e.get("event")) for e in sb.history]
        n += 1
        if not (sa.winner == sb.winner and sa.loop_no == sb.loop_no and ha == hb):
            bad.append(f"{name} s{seed}: {_outcome(sa)} vs {_outcome(sb)}")
    return {"days": days, "n": n, "bad": bad}


def sites() -> None:
    """★静的走査＝`agents/defense_plan.py` の **Break 生成箇所を全数**印字して分類する。

    「同型が残っていないか」の主張は**不存在の主張**＝完全列挙でしか言えない（規約 §3）。
    """
    import inspect
    src = inspect.getsource(DP).splitlines()
    print("== `agents/defense_plan.py` の Break 生成箇所（全数）==")
    for i, ln in enumerate(src, 1):
        s = ln.strip()
        if s.startswith("#"):
            continue
        if "Break(" in s or "_relocate_breaks(" in s:
            print(f"  :{i:5d}  {s}")


class _ForcedShield(HeuristicProtagonist):
    """★B-166 の反実仮想＝指定した (loop, day) の席で `移動禁止→従者` を強制する。

    それ以外の席は現行AIのまま（`super().decide()` をそのまま返す）＝
    **「その日にその手が成立したか」までしか言えない**（以降は相手の選択も変わりうる）。
    """

    def __init__(self, seed: int = 0, at: tuple | None = None):
        super().__init__(seed)
        self.at = at
        self.forced = 0

    def decide(self, view, decision, options):
        if self.at and decision == "set_card" \
                and (view.get("loop"), view.get("day")) == self.at:
            for o in options:
                if (o.get("card") == "移動禁止" and o.get("target") == "従者"
                        and o.get("target_kind") == "character"):
                    self.forced += 1
                    return o
        return super().decide(view, decision, options)


def counterfactual(days: int, script_name: str, seed: int, loop: int, day: int,
                   loops: int = 8) -> None:
    """★盾（従者の身代わり）を `移動禁止→従者` で維持したらどうなったか（OFF/ON 対比）。"""
    rows = [r for r in _games(days) if r[0] == script_name and r[1] == seed]
    if not rows:
        print(f"該当なし: {script_name} s{seed}（{days}日級）")
        return
    sc = rows[0][2]
    print(f"== 反実仮想 {script_name} s{seed}（{days}日級）"
          f" L{loop}D{day} に `移動禁止→従者` を強制 ==")
    print(f"  roles = {{{', '.join(f'{n}:{sc.role_of(n)}' for n in sorted(sc.cast))}}}")
    for on in (False, True):
        hp = _ForcedShield(seed, at=(loop, day) if on else None)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        d = [(e.get("loop"), e.get("day"), e.get("name"), str(e.get("cause")))
             for e in state.secret_log
             if e.get("event") == "death" and e.get("loop") == loop]
        subs = [(e.get("loop"), e.get("day"), e.get("protected"))
                for e in state.secret_log if e.get("event") == "juusha_substitute"]
        lr = [(e.get("day"), e.get("result")) for e in state.history
              if e.get("event") == "loop_result" and e.get("loop") == loop]
        print(f"  force={on} 強制枚数={hp.forced} 結末={_outcome(state)}"
              f" loops={state.loop_no}")
        print(f"     L{loop} の死亡 = {d}")
        print(f"     身代わり      = {subs}")
        print(f"     L{loop} の結果 = {lr}")


def report(res: dict, days: int) -> None:
    print(f"== B-165/B-166 Phase 1（{days}日級・★独立脚本 {res['n_scripts']} 本"
          f"／★従者が配役の局 {res['n_played']} 局を対局）==")
    print(f"  対局した局: {', '.join(f'{n} s{s}' for n, s in res['played'])}")
    for k, v in sorted(res["counts"].items()):
        print(f"  {k:66s} {v}")
    rows = res.get("rows") or []
    if rows:
        print("\n  ★現物（カテゴリごとに先頭8件）")
        for cat in ("X1", "X2", "X3", "Y1b", "Y1a", "Y1c", "Y2m", "O1v", "O3"):
            sub = [r for r in rows if r.get("cat") == cat]
            if not sub:
                continue
            print(f"   -- {cat}（{len(sub)}件）--")
            for r in sub[:8]:
                print(f"      {r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="B-165/B-166 Phase 1（計測のみ）")
    ap.add_argument("cmd", choices=["count", "verify", "sites", "cf"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--script", default="random_FS")
    ap.add_argument("--seed", type=int, default=18)
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--day", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "sites":
        sites()
        return
    if a.cmd == "cf":
        print(_switches(a.days, a.loops))
        counterfactual(a.days, a.script, a.seed, a.loop, a.day, loops=a.loops)
        return
    print(_switches(a.days, a.loops))
    if a.cmd == "verify":
        r = verify(a.days, a.loops, a.start, a.end)
        print(f"棋譜の不一致 = {len(r['bad'])} 件 / {r['n']}局"
              f"（★従者を含む局だけを検査）")
        for b in r["bad"]:
            print(f"  ★不一致: {b}")
        raise SystemExit(1 if r["bad"] else 0)
    res = count(a.days, a.loops, a.start, a.end)
    report(res, a.days)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
