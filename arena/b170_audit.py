# -*- coding: utf-8 -*-
"""B-170 Phase 1：**「板の暗躍カウンターは減らせない」という前提を数える**
（★計測のみ・`agents/` は1行も触らない）。

## 用語（★記号を使う前に、必ず日本語で定義する）

- **`board_anyaku`**＝「ボード名（病院／神社／都市／学校）→ そのボードに乗っている
  暗躍カウンターの個数」の辞書。主人公にも公開されている情報。
- **板敗北**＝ループ終了時に、敗北ボードの暗躍カウンターが **2個以上** なら主人公の敗北
  （`agents/defense_plan.BOARD_DEFEAT_ANYAKU = 2`）。
- **`Condition`（条件）／`Break`（折り手）**＝防御プランナー
  （`agents/defense_plan.py`）の構造。「この負け筋は、これらの条件が**全部**成り立つと発火する」
  ＝条件のどれか1つを**折れば**防げる。`Condition.breaks` が空＝その条件は折れない。
  すべての条件が折れない脅威は `Threat.defendable == False`＝「防御不能」として捨てられる。
- **席**＝主人公の `set_card` 決定1回（1ターンに3席）。b163/b164 監査と同じ定義。
- **浄化係**＝板の暗躍カウンターを**主人公能力フェイズ（第6フェイズ）で剥がせる**キャラ。
  KB 上は2人だけ（単一ソース＝`sim.abilities.board_anyaku_removal_scope`）：
    - **巫女**「神社の暗躍除去」＝友好♡**3**・**巫女が神社に居ること**・**回数無制限**
      （`rules/20_goodwill_abilities.md:129`）
    - **神格**「暗躍除去（キャラ/ボード）」＝友好♡**5**・**神格がそのボードに居ること**・
      **回数無制限**（`rules/20_goodwill_abilities.md:153`）
  主人公能力フェイズ(6)は事件フェイズ(7)より前・ループ終了処理より前
  （`rules/00_rules_core.md:103-115`）＝**板敗北の判定が走る前に剥がせる**。

## 数える対象（★2つあり、混ぜない）

- **断言1＝`_add_anyaku_supply_breaks` の note**（`agents/defense_plan.py:773-775`）
  「既に暗躍が2以上＝供給を止めても戻せない（暗躍カウンターを減らす手段は無い）」。
  この文言が付いた条件は折り手ゼロ。→ 数え上げキー `(A)`。
- **断言2＝`_threat_board_defeat` の `breached` 早期 continue**（`:1359-1368`）。
  板の暗躍が既に2以上だと、**その板の負け筋は意思決定用の脅威列挙から丸ごと消える**
  （`include_breached=False` が意思決定既定）。コメントは「意思決定は覆えない」。
  → 数え上げキー `(A2)`。★こちらの方が母数が大きい。

どちらも「もう戻せない」を前提にしている。KB 上は**浄化係が居れば戻せる**。

## 反実仮想の作り方（★判定を書き写さない）

本監査は `agents/` の関数を1つも複製しない。
- `(A)` は `defense_plan._add_anyaku_supply_breaks` を**呼び出しを記録するだけのラッパ**で
  一時的に包み、**本物**を呼んで結果（`c1.breaks` が空か・note が何か）を読む。
- `(A2)` は**本物の** `plan_for_belief` を `include_breached=True` でもう一度呼び、
  `Threat.breached` が立った脅威を読む（意思決定側の呼び出しには一切触れない）。
- 「浄化係が居るか」は `sim.abilities.board_anyaku_removal_scope` と
  `engine.data.goodwill_abilities_of` を引く（キャラ名の羅列を本モジュールに複製しない）。
- 脚本家の拒否可能性は `defense_plan._friendship_ignore_prob` を**再利用**する
  （`rules/00_rules_core.md:110`＝拒否できるのは能力を使うキャラが友好無視/絶対友好無視のときだけ）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b170_audit verify --days 3
    python -m arena.b170_audit count  --days 3
    python -m arena.b170_audit list   --days 3
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.defense_plan as DP
from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _lost_loops, _outcome
from engine.data import UNREFUSABLE_ABILITY_CHARS, goodwill_abilities_of
from sim import run_game
from sim.abilities import board_anyaku_removal_scope

_AREAS = ("病院", "神社", "都市", "学校")
#: 役職が友好無視/絶対友好無視である疑いがこれ以上なら「拒否されうる」＝確定側に数えない。
#: `defense_plan.SUPPRESSOR_IGNORE_P`（抑止役の同種判定）と同じ値を借りる（二重実装の回避）。
IGNORE_P = DP.SUPPRESSOR_IGNORE_P


# ---------------------------------------------------------------------------
# 浄化係の列挙（★Phase 2 で `defense_plan` へ移すならこの形の単一ソースヘルパにする）
# ---------------------------------------------------------------------------
def purifiers_for_board(view: dict, area: str, roles: dict | None = None) -> list[dict]:
    """`area`（板）の暗躍を**今ターンの主人公能力フェイズで**1つ剥がせる浄化係を列挙する。

    返すのは辞書のリスト。各要素の意味：
      name       ＝キャラ名（巫女 or 神格）
      ability    ＝能力名
      hearts     ＝必要友好数（巫女=3／神格=5）
      goodwill   ＝いま載っている友好カウンター数
      funded     ＝`goodwill >= hearts`（今ターン既に使える）
      short      ＝あと何ハート足りないか（funded なら 0）
      refuse_p   ＝脚本家に拒否されうる確率（belief 上で友好無視系の役職である確率）
      refusable  ＝`refuse_p >= IGNORE_P`（＝安全側では「使える」と数えない）

    判定に使う条件（すべて公開情報）：
      (1) 生存していて、**その板の上に立っている**（`c.area == area`）
          ＝巫女は神社に居ないと使えない・神格はそのボードに居ないと使えない
      (2) その (キャラ, 能力) が板の暗躍を剥がせる（単一ソース＝`board_anyaku_removal_scope`）
      (3) 回数無制限（1/L は「もう使ったか」を公開情報から確定できない＝安全側で除外。
          ただし巫女・神格の暗躍除去はどちらも無制限＝実際には (3) で落ちない）
    ★空撃ち（`rules/20:125`＝暗躍が無ければ何も起きない）は呼び出し側で
      `board_anyaku[area] > 0` を見て弾くこと（本関数は「使えるか」だけを答える）。
    """
    out: list[dict] = []
    for c in view.get("characters", []) or []:
        name = c.get("name")
        if not c.get("alive", True) or c.get("area") != area:
            continue                                   # (1) 位置＝板の上に居ない
        for ab in goodwill_abilities_of(name) or []:
            if ab.get("once_per_loop"):
                continue                               # (3)
            if area not in board_anyaku_removal_scope(name, ab["name"],
                                                      boards=[area]):
                continue                               # (2)
            gw = int(c.get("goodwill", 0) or 0)
            hearts = int(ab.get("hearts", 99))
            rp = (0.0 if name in UNREFUSABLE_ABILITY_CHARS
                  else DP._friendship_ignore_prob(roles, name))
            out.append({"name": name, "ability": ab["name"], "hearts": hearts,
                        "goodwill": gw, "funded": gw >= hearts,
                        "short": max(0, hearts - gw),
                        "refuse_p": round(rp, 3), "refusable": rp >= IGNORE_P})
    return out


def purifiers_in_cast(view: dict) -> list[str]:
    """そのループのキャストに居る浄化係の名（生死・位置は問わない）＝**射程の上限**の母数。"""
    out = []
    for c in view.get("characters", []) or []:
        name = c.get("name")
        for ab in goodwill_abilities_of(name) or []:
            if board_anyaku_removal_scope(name, ab["name"]):
                out.append(name)
                break
    return out


# ---------------------------------------------------------------------------
# プローブ
# ---------------------------------------------------------------------------
_NOTE_A = "既に暗躍が2以上"          # 断言1 の note の先頭


def _char_of(view: dict, name: str) -> dict | None:
    for c in view.get("characters", []) or []:
        if c.get("name") == name:
            return c
    return None


def _reach_of(view, area, roles, opts, ba) -> dict:
    """その板に対する浄化の射程を1行にまとめる（doc 用の一覧に載せる）。

    now  ＝今ターン既に使える浄化係（カード0枚）
    card ＝今日 友好+ を1枚置けば今日使える浄化係と、その札
    need_removals ＝板を暗躍2未満へ落とすのに要る除去回数（`cur - 1`）
    days_left ＝このループの残り日数（今日を含む）＝除去できる回数の上限
    """
    cur = int(ba.get(area, 0) or 0)
    ps = purifiers_for_board(view, area, roles)
    now = [p for p in ps if p["funded"] and not p["refusable"]]
    card = []
    for p in ps:
        if p["refusable"] or p["funded"] or p["short"] > 2:
            continue
        for c_, d in (("友好+1", 1), ("友好+2", 2)):
            if d >= p["short"] and opts is not None and opts.has_char(c_, p["name"]):
                card.append({**p, "card": c_})
                break
    return {"now": now, "card": card,
            "need_removals": max(0, cur - (DP.BOARD_DEFEAT_ANYAKU - 1)),
            "days_left": DP.days_left_in_loop(view)}


class _B170Probe(HeuristicProtagonist):
    """★`super()` の戻り値をそのまま返す＝挙動不変（`verify` で棋譜一致を物証化）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.rows: list = []
        self._busy = False

    # -- 席（set_card 決定1回）ごとに、本物のプランナーをもう一度呼んで観測する ----
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision == "set_card" and not self._busy and self._belief is not None:
            self._busy = True
            try:
                self._audit_seat(view, options)
            except Exception as e:            # 監査が本体を壊さないための保険
                self.c[f"監査例外＝{type(e).__name__}"] += 1
            finally:
                self._busy = False
        return chosen

    def _audit_seat(self, view: dict, options: list) -> None:
        c = self.c
        c["席（set_card 決定）"] += 1
        ba = {a: int((view.get("board_anyaku") or {}).get(a, 0) or 0) for a in _AREAS}
        in_cast = purifiers_in_cast(view)
        if in_cast:
            c["[席] (E) 浄化係がキャストに居る席"] += 1
        # ★(A)(A2) はどちらも「板の暗躍が既に2以上」を必要条件に持つ
        #   （(A)＝`_cur_b >= BOARD_DEFEAT_ANYAKU`／(A2)＝`cur >= 2` の早期 continue）。
        #   ∴ 2以上の板が1つも無い席では両方とも原理的に立たない＝重い再計画を省く
        #   （数え上げの結果は不変。速度のためだけの門）。
        if max(ba.values()) < DP.BOARD_DEFEAT_ANYAKU:
            return
        c["[席] 板の暗躍が既に2以上（(A)(A2) の必要条件）"] += 1
        roles = self._belief.role_marginals()

        # ---- 断言1（A）＝`_add_anyaku_supply_breaks` の note を記録つきで再現 ----
        calls: list = []
        orig = DP._add_anyaku_supply_breaks

        def _wrapped(c1, _view, _roles, _opts, victim, area, supply_rumor,
                     rumor_left=None):
            orig(c1, _view, _roles, _opts, victim, area, supply_rumor,
                 rumor_left=rumor_left)
            calls.append({"victim": victim, "area": area,
                          "n_breaks": len(c1.breaks), "note": c1.note})

        DP._add_anyaku_supply_breaks = _wrapped
        try:
            _threats, _plan = DP.plan_for_belief(
                view, self._belief, options=options,
                initial_areas=self._initial_area_map(view))
        finally:
            DP._add_anyaku_supply_breaks = orig
        # 突破済み（breached）も含めた版＝断言2 の観測用（意思決定側は触らない）
        threats_b, _pb = DP.plan_for_belief(
            view, self._belief, options=options,
            initial_areas=self._initial_area_map(view), include_breached=True)

        seat_hit = False
        _opts = DP._Opts(options)
        # ---------------- (A) note「既に暗躍が2以上」 ----------------
        for call in calls:
            c["_add_anyaku_supply_breaks 呼び出し"] += 1
            if call["n_breaks"] or not str(call["note"]).startswith(_NOTE_A):
                continue
            area = call["area"]
            c["(A) note『既に暗躍が2以上＝戻せない』が付いた条件"] += 1
            c[f"　(A) その板＝{area}（暗躍{ba.get(area)}）"] += 1
            self._score_purifiers("(A)", area, view, roles, ba, _opts)
            seat_hit = True
            self.rows.append({"key": "A", "loop": view.get("loop"),
                              "day": view.get("day"), "area": area,
                              "victim": call["victim"], "board_anyaku": ba,
                              "purifiers": purifiers_for_board(view, area, roles),
                              "reach": _reach_of(view, area, roles, _opts, ba),
                              "purge_target": getattr(self, "_purge_target", None),
                              "cast": purifiers_in_cast(view)})

        # ---------------- (A2) breached で意思決定から消えた板敗北 ----------------
        for t in threats_b:
            if t.kind != "board_defeat" or not getattr(t, "breached", False):
                continue
            area = t.label.split("の")[0]
            c["(A2) 板敗北が『突破済み』として意思決定から消えた"] += 1
            c[f"　(A2) その板＝{area}（暗躍{ba.get(area)}）"] += 1
            self._score_purifiers("(A2)", area, view, roles, ba, _opts)
            seat_hit = True
            self.rows.append({"key": "A2", "loop": view.get("loop"),
                              "day": view.get("day"), "area": area,
                              "prob": round(t.prob, 3), "board_anyaku": ba,
                              "purifiers": purifiers_for_board(view, area, roles),
                              "reach": _reach_of(view, area, roles, _opts, ba),
                              "purge_target": getattr(self, "_purge_target", None),
                              "cast": purifiers_in_cast(view)})

        # ---------------- (D) 採点層が既に浄化係へ投資していたか ----------------
        if seat_hit:
            c["[席] (A)か(A2)が立った席"] += 1
            pt = getattr(self, "_purge_target", None)
            c[f"[席] (D) 採点層の浄化係投資 _purge_target＝{pt}"] += 1
            # ---------------- (E) 緩い母数＝浄化係がキャストに居るか -------------
            if in_cast:
                c["[席] (E∧hit) 諦めた席のうち浄化係がキャストに居る"] += 1
                for x in sorted(set(in_cast)):
                    c[f"[席] (E∧hit) ＝{x}"] += 1

    def _score_purifiers(self, key: str, area: str, view, roles, ba,
                         opts=None) -> None:
        """その板を今ターン剥がせる浄化係が居たかを層別する。

        層は3段（★混ぜない）：
          (b)  **今ターン既に使える**＝友好が足りていて拒否されない（＝カード0枚で折れる）
          (bC) **今ターン友好+カードを1枚置けば使える**＝不足ハートが 1〜2 で、その札が手札にある
               （★`rules/00_rules_core.md:103-111`＝主人公行動(3)→行動解決(4)で友好が載り、
                 **同じ日の**主人公能力フェイズ(6)で使える＝B-141b が是正した解禁日の算術と同型）
          (b3) 友好が不足していて今日は届かない
        さらに **間に合うか**（`feasible`）＝板の暗躍を 2 未満へ落とすには
        `cur - 1` 回の除去が要り、除去は 1ターン1回・残り日数は `days_left_in_loop`。
        """
        c = self.c
        cur = int(ba.get(area, 0) or 0)
        need_rm = max(0, cur - (DP.BOARD_DEFEAT_ANYAKU - 1))   # 何回剥がせば2未満か
        days_left = DP.days_left_in_loop(view)
        feasible = need_rm <= days_left
        tag_f = "間に合う" if feasible else "★間に合わない"
        ps = purifiers_for_board(view, area, roles)
        if not ps:
            c[f"　{key} (b0) その板に浄化係が立っていない"] += 1
            # 参考＝板の上に居ないが生存している浄化係（移動で連れて行く筋＝最小形の対象外）
            for nm in sorted(set(purifiers_in_cast(view))):
                ch = _char_of(view, nm)
                if ch and ch.get("alive", True) and ch.get("area") not in (None, area):
                    c[f"　{key} (b0') 浄化係 {nm} は生存だが別エリア（{ch.get('area')}）"] += 1
            return
        c[f"　{key} (b1) その板に浄化係が立っている（要除去{need_rm}回／残り{days_left}日＝{tag_f}）"] += 1
        # ★(b2') 脚本家の拒否（`rules/00_rules_core.md:110`）＝安全側で落ちた分を明示する。
        #   `refusable` ＝belief 上でその浄化係が友好無視/絶対友好無視の役職である確率が
        #   IGNORE_P(0.15) 以上＝「使えると確定できない」＝折り手には数えない。
        if all(p["refusable"] for p in ps):
            c[f"　{key} (b2') ★浄化係は立っているが脚本家に拒否されうる"
              f"（友好無視疑い P≧{IGNORE_P}）＝安全側では数えない"] += 1
            # 参考＝拒否の疑いを無視した「緩い」射程（＝射程の上限）
            loose_now = [p for p in ps if p["funded"]]
            if loose_now:
                c[f"　{key} (b2'緩) 拒否を無視すれば今ターン剥がせた"] += 1
        funded = [p for p in ps if p["funded"]]
        ok = [p for p in funded if not p["refusable"]]
        if ok:
            c[f"　{key} ★(b) 今ターン剥がせる浄化係が居る（{tag_f}）"] += 1
            for p in ok:
                c[f"　{key} ★(b) ＝{p['name']}（♡{p['goodwill']}/{p['hearts']}・{area}）"] += 1
            if cur <= 0:
                c[f"　{key} (b-空撃ち) 板の暗躍が0＝剥がす対象が無い"] += 1
            return
        if funded:
            c[f"　{key} (b2) 友好は足りるが拒否されうる（友好無視疑い）"] += 1
            return
        # ---- (bC) 今日 友好+ を1枚置けば今日の主人公能力フェイズで使える ----
        hit_c = False
        for p in ps:
            if p["refusable"] or p["short"] > 2:
                continue
            for card, d in (("友好+1", 1), ("友好+2", 2)):
                if d < p["short"]:
                    continue
                if opts is not None and opts.has_char(card, p["name"]):
                    c[f"　{key} ★(bC) 今日 {card}→{p['name']} で解禁できる"
                      f"（♡{p['goodwill']}→{p['goodwill'] + d}/{p['hearts']}・{tag_f}）"] += 1
                    hit_c = True
                    break
            if hit_c:
                break
        if hit_c:
            c[f"　{key} ★(bC) 今日 友好+ 1枚で解禁できる浄化係が居る（{tag_f}）"] += 1
            return
        short = min(p["short"] for p in ps)
        c[f"　{key} (b3) 友好が不足（あと{short}ハート・今日の手札では届かない）"] += 1
        # ★負の結果の裏取り＝「短い(≤2)のに札が無い」のか「そもそも遠い(≥3)」のか
        for p in ps:
            if p["refusable"] or p["funded"]:
                continue
            if p["short"] <= 2:
                have = [cd for cd in ("友好+1", "友好+2")
                        if opts is not None and opts.has_char(cd, p["name"])]
                c[f"　{key} (b3診断) {p['name']} は あと{p['short']}♡ だが"
                  f" 手札の友好+札＝{have or 'なし'}"] += 1
            else:
                c[f"　{key} (b3診断) {p['name']} は あと{p['short']}♡＝1枚では届かない"] += 1


# ---------------------------------------------------------------------------
def _games(days: int, start: int = 0, end: int | None = None):
    from arena.benchmark import benchmark_scripts
    return list(benchmark_scripts(days=days))[start:end]


def _switches(days: int, loops: int) -> str:
    from agents.heuristic_protagonist import HeuristicProtagonist as H
    return (f"B153_JUUSHA_PAIR_BREAK={DP.B153_JUUSHA_PAIR_BREAK}"
            f" / B165_PAIR_BREAK={DP.B165_PAIR_BREAK}"
            f" / DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER}"
            f" / B163_KURO_IN_CAST="
            f"{getattr(H, 'B163_KURO_IN_CAST', getattr(DP, 'B163_KURO_IN_CAST', 'n/a'))}"
            f" / B141_COOLER_VALUE_FUTURE_ONLY="
            f"{getattr(H, 'B141_COOLER_VALUE_FUTURE_ONLY', 'n/a')}"
            f" / B100_MIX={H.B100_MIX} / B100_THETA={H.B100_THETA}"
            f" / IGNORE_P={IGNORE_P} / days={days} loops={loops}")


def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    """★挙動不変の物証＝プローブ有無で棋譜が完全一致するか（全局）。"""
    bad, n = [], 0
    for name, seed, sc in _games(days, start, end):
        probe = replace(sc, loops=loops)
        a = _B170Probe(seed)
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
    return {"days": days, "n": n, "bad": bad,
            "監査例外": sum(1 for _ in ())}


def _gw_ban_on(state, names=("巫女", "神格")) -> Counter:
    """★脚本家が浄化係に **友好禁止** を当てた回数（公開情報＝`cards_revealed`）。

    `sim/loop_race._has_board_removal` が「ハーツ飢餓」と呼ぶ機序：
    脚本家は友好禁止を毎ターン1枚置けるので、解禁前のホルダーが1人だけなら
    そのキャラへの友好+を永久に止められる（1ターンに載る友好は最大2）。
    ∴ 「浄化係が♡に届かない」のは偶然ではなく**脚本家の手**である可能性がある。
    """
    c: Counter = Counter()
    for e in state.history:
        if e.get("event") != "cards_revealed":
            continue
        for pl in e.get("cards", []) or e.get("placements", []) or []:
            if pl.get("card") == "友好禁止" and pl.get("target") in names:
                c[pl.get("target")] += 1
    return c


def _board_removals(state) -> list:
    """★板の暗躍が友好能力で実際に剥がされた記録（`anyaku` の負のデルタ・板が対象）。"""
    out = []
    for e in state.history:
        if e.get("event") == "anyaku" and e.get("target") in _AREAS \
                and (e.get("delta") or 0) < 0:
            out.append({"loop": e.get("loop"), "day": e.get("day"),
                        "board": e.get("target")})
    return out


def count(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    c: Counter = Counter()
    names: set = set()
    pur_names: set = set()
    ex: list = []
    n = 0
    for name, seed, sc in _games(days, start, end):
        names.add(name)
        cast_pur = sorted({x for x in sc.cast if x in ("巫女", "神格")})
        if cast_pur:
            pur_names.add(name)
            c["局＝浄化係（巫女/神格）がキャストに居る"] += 1
            for x in cast_pur:
                c[f"　局＝キャストに {x} が居る"] += 1
        hp = _B170Probe(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        lost = set(_lost_loops(state))
        if getattr(state, "defeat", False):
            lost.add(state.loop_no)
        c.update(hp.c)
        c[f"結末＝{_outcome(state)}"] += 1
        for nm, k in _gw_ban_on(state).items():
            c[f"★脚本家が {nm} に友好禁止を当てた回数（ハーツ飢餓）"] += k
        rem = _board_removals(state)
        c["★板の暗躍が実際に剥がされた回数（主人公の友好能力）"] += len(rem)
        if rem:
            c["★同・それが起きた局数"] += 1
            for r in rem:
                c[f"　★剥がされた板＝{r['board']}"] += 1
        for r in hp.rows:
            tag = "敗北ループ" if r["loop"] in lost else "防衛ループ"
            c[f"[層別] {r['key']}／{tag}"] += 1
            rc = r.get("reach") or {}
            ok = rc.get("now") or rc.get("card")
            if ok:
                _w = "カード0枚" if rc.get("now") else "友好+1枚で解禁"
                _f = ("間に合う" if rc.get("need_removals", 9) <= rc.get("days_left", 0)
                      else "★間に合わない")
                c[f"[層別] {r['key']}＋浄化の射程あり（{_w}・{_f}）／{tag}"] += 1
                # ★(d) その条件が立った席で、採点層は既に浄化係へ投資しようとしていたか
                c[f"[層別] {r['key']}＋射程あり／採点層の投資先"
                  f" _purge_target＝{r.get('purge_target')}"] += 1
                if len(ex) < 40:
                    rr = dict(r)
                    rr["game"] = f"{name} s{seed}"
                    rr["lost_loop"] = r["loop"] in lost
                    rr["outcome"] = _outcome(state)
                    ex.append(rr)
        n += 1
    return {"days": days, "n_games": n, "n_scripts": len(names),
            "n_scripts_with_purifier": len(pur_names),
            "scripts_with_purifier": sorted(pur_names),
            "counts": dict(c), "examples": ex}


def listing(days: int = 3, loops: int = 8, start: int = 0,
            end: int | None = None) -> dict:
    """★「諦めた席」を**全数**、脚本名・ループ・日・友好数つきで印字する（doc 用の一覧）。"""
    out: list = []
    n = 0
    for name, seed, sc in _games(days, start, end):
        hp = _B170Probe(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        lost = set(_lost_loops(state))
        if getattr(state, "defeat", False):
            lost.add(state.loop_no)
        for r in hp.rows:
            rc = r.get("reach") or {}
            if not (rc.get("now") or rc.get("card")):
                continue
            out.append({"game": f"{name} s{seed}", "rule_y": sc.rule_y,
                        "key": r["key"], "loop": r["loop"], "day": r["day"],
                        "板": r["area"], "board_anyaku": r["board_anyaku"],
                        "要除去回数": rc.get("need_removals"),
                        "残り日数": rc.get("days_left"),
                        "今ターン剥がせる浄化係": rc.get("now"),
                        "今日 友好+1枚で解禁できる浄化係": rc.get("card"),
                        "そのループを落としたか": r["loop"] in lost,
                        "結末": _outcome(state)})
        n += 1
    return {"days": days, "n_games": n, "n_rows": len(out), "rows": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "count", "list"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    a = ap.parse_args()
    print(f"== 切替口 == {_switches(a.days, a.loops)}")
    fn = {"verify": verify, "count": count, "list": listing}[a.cmd]
    r = fn(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
