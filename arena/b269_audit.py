# -*- coding: utf-8 -*-
"""B-269：到達可能「**下限** `L`」＝「**冷却では臨界を割れない**」席の数え上げ（★フェーズ0）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-85（B-269）／親＝§72-80・§72-82（B-266）。
試作の出典＝`docs/検死_B266_牡丹BTX3d_seed0_2026-08-19.md` §3-5（`arena/b266_audit.py bounds`）。

## なぜ下限か（双対）

B-256／B-262 の **上限** `U_p ≤ U_b ≤ U_pm` は「**届きうる**」しか言えない
（牡丹 seed0 では全13席で臨界以上＝判別力ゼロ）。「**冷却しても無駄**」を言うには
**双対の下限** が要る：

    L = u + （その日に立つ供給の下限） − （その日に打てる冷却の上限）

`L ≥ 臨界` なら、**主人公が最大限冷やしてもその日の事件フェイズで臨界以上が残る**
＝その席の冷却札は空振り。★「上限」の裏返しでは作れない（B-266 §3-5）。

### 材料は公開情報だけ（B-262 と同じ）

| 項 | 定義 | 出典 |
|---|---|---|
| 供給①`不安+1` 札 | **公開実績**（`cards_revealed`）∧ **今日その対象に脚本家の伏せ札** | B-262 の述語＝`agents/heuristic_protagonist._b262_mm_supply_likely` |
| 供給②脚本家能力 | ミスリーダーの**台**（役職周辺確率>0）が「対象自身 ∪ 対象と同室」に収まる＝誰がミスリーダーでも +1 を置ける | `rules/50_basic_tragedy_x.md:139,141`／`engine/data.ROLE_MM_UNREST` |
| 冷却①`不安-1` 札 | このループでまだ使っていない主人公が1人でも居れば 1（1キャラ1日1枚＝`rules/00:104`＋`rules/10:81`） | `view["used_cards"]` |
| 冷却②友好能力 | 同エリアの**自身以外**が持つ不安除去能力（学生／医者／アイドル／ナース／教師）。♡不足は今日の `友好+1/+2` 1枚で届くかを見る | `engine/data.GOODWILL_ABILITIES`／`sim/abilities.py:506-529`／B-174 の「自身以外」 |

★**冷却は寛容側（多め）に数える**＝1日3枠の予算も脚本家の `友好禁止`／`不安禁止` も無視する。
∴ 「**この上限でも割れない**」と言えた時だけ発火する＝**偽陽性が出にくい向き**に倒してある。

## 何を数えるか（フェーズ0＝事前登録）

| 記号 | 定義 |
|---|---|
| **N_fire** | 「**今日が致死日** ∧ **犯人が1人に確定**（`_known_culprits`）∧ `L ≥ 臨界`」の席 |
| **N_wide** | 犯人が確定していなくても「今日が致死日 ∧ 候補の誰かが `L ≥ 臨界`」の席（参考＝発火面積の上界） |
| **N_cool** | N_fire のうち**実際に冷却札**（`不安-1`）が打たれた席＝★**止める価値のある席** |
| **FP** | N_fire と言った (局,L,D) で、**その日の致死事件が実際には発生しなかった**もの＝★偽陽性 |
| **S** | N_cool の席で、冷却手を候補から外して**本物の採点器**で argmax を取り直した手＝★空いた席の振り替え先 |
| **R3** | ★牡丹の正解手3種（退避 `移動→守る対象`／供給断ち `移動禁止→供給元`／`不安-1→犯人`）の**順位と点** |

## 挙動には触れない

`agents/` `sim/` `engine/` の既定値を一切書き換えない。観測は
`agents.heuristic_protagonist.B100_HOOK`（計測専用フック・戻り値不使用）だけ。
★次点 S は**採点器を副作用なしで呼び直すだけ**（`_virus_test_targets` のような
属性差し替えすら行わない）＝`verify` で棋譜 bit 一致を実測する。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b269_audit seats  --days 3 --pick all --outdir /tmp/b269/d3
    python -m arena.b269_audit report --outdir /tmp/b269/d3
    python -m arena.b269_audit verify --days 3 --pick random_BTX:0-2
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import replace

from arena.b249_audit import _okey, check_no_knob_writes, defaults_banner
from arena.b250_audit import _truth_of, parse_pick

COOL_CARD = "不安-1"
UNREST_PLUS = "不安+1"
GW_PLUS2 = "友好+2"
GW_PLUS1 = "友好+1"
MOVE_CARDS = ("移動↑↓", "移動←→", "移動斜め")
MOVE_BAN = "移動禁止"
STUDENTS = ("男子学生", "女子学生")

#: 不安を**除去**できる友好能力（`engine/data.GOODWILL_ABILITIES` と `sim/abilities.py:506-529` の
#: 対応表から、除去側だけを抜いたもの）。値＝(必要♡〔既定値。実際は KB 側を使う〕, 対象クラス)。
#: 対象クラス "student"＝同エリアの**他の**学生／"other"＝同エリアの**他の**キャラ／
#: "nurse"＝同エリアの他のキャラのうち**不安が臨界以上**のもの（本監査では常に該当）。
COOL_ABILITIES: dict[tuple[str, str], tuple[int, str]] = {
    ("男子学生", "学生の不安除去"): (2, "student"),
    ("女子学生", "学生の不安除去"): (2, "student"),
    ("医者", "不安操作（除去/付与）"): (2, "other"),
    ("アイドル", "不安除去"): (3, "other"),
    ("ナース", "不安臨界以上のキャラの不安除去"): (2, "nurse"),
    ("教師", "学生の不安操作"): (3, "student"),
}


def knob_baseline_banner() -> str:
    """`arena/knob_audit.check_baseline` の一般形（条件表なし＝退化形）を配線する。"""
    from arena import knob_audit
    knob_audit.check_baseline({}, {}, (), driver="arena.b269_audit")
    return "knob_audit.check_baseline（空の条件表＝退化形）✅"


# ---------------------------------------------------------------------------
# 下限 L の材料（★すべて view の公開情報から）
# ---------------------------------------------------------------------------
def b262_supply_likely(view: dict, who: str) -> bool:
    """B-262 の述語＝(その対象への `不安+1` の公開実績) ∧ (今日その対象に脚本家の伏せ札)。

    ★実装（`agents/heuristic_protagonist._b262_mm_supply_likely`）と**同じ定義**を
    view から組む。実装側は `B256_UNREACHABLE_SKIP` が OFF だと材料を作らないため、
    切替口の状態に依存しないよう監査側で独立に数える（定義の食い違いは
    `tests/test_b269_audit.py` が実装と突き合わせて固定する）。
    """
    habit = False
    for e in view.get("history", ()):
        if e.get("event") != "cards_revealed":
            continue
        for p in e.get("placements", ()) or ():
            if (p.get("owner") == "mastermind" and p.get("card") == UNREST_PLUS
                    and p.get("target_kind") == "character" and p.get("target") == who):
                habit = True
    fd = any(p.get("owner") == "mastermind" and p.get("target_kind") == "character"
             and p.get("target") == who for p in view.get("placements", ()) or ())
    return bool(habit and fd)


def misleader_supply_certain(marg: dict, chars: dict, who: str) -> tuple[bool, list[str]]:
    """脚本家能力フェイズの `+1` が `who` に**確実に届く**か（★公開情報のみ）。

    ミスリーダーは「同エリアのキャラ1人（**自身を含む**）に不安+1」（`rules/50:141` の★／
    `engine/data.ROLE_MM_UNREST` の `self_target_ok`）。∴ **ミスリーダーの台**
    （役職周辺確率が正の名前の集合）が「`who` 自身 ∪ `who` と同室の生存者」に
    **収まっている**なら、誰がミスリーダーでも `who` へ +1 を置ける＝供給は確率1で可能。
    ★台が空（この脚本にミスリーダーが居ない）なら False。
    ★ファクター（学校に暗躍2以上でミスリーダーの能力を得る＝`engine/data:519`）は
      数えない＝**供給を過小に見る側**＝下限としては安全側（偽陽性を増やさない）。
    """
    support = sorted(n for n, m in marg.items() if float(m.get("ミスリーダー", 0.0)) > 1e-9)
    if not support:
        return False, []
    me = chars.get(who) or {}
    ok = all(n == who or ((chars.get(n) or {}).get("alive")
                          and (chars.get(n) or {}).get("area") == me.get("area"))
             for n in support)
    return bool(ok), support


def max_cooling(view: dict, chars: dict, who: str) -> dict:
    """その日、主人公陣営が `who` に掛けられる**冷却の上限**（★寛容側）。

    1. `不安-1` 札（フェイズ4）＝1キャラ1日1枚（`rules/00:104`＋`rules/10:81`）・
       **1ループ1回**（`rules/10:76`）＝そのループでまだ使っていない席が1つでもあれば 1。
    2. 同エリアの**自身以外**が持つ不安除去の友好能力（`COOL_ABILITIES`）。
       ♡が足りなければ、今日 `友好+2`（1/L）／`友好+1` を1枚置いて届くかを見る。
    ★1日3枠の予算も、脚本家の `友好禁止`／`不安禁止` も考慮しない（＝多めに数える）。
    """
    from engine.data import GOODWILL_ABILITIES
    n, why = 0, []
    used = view.get("used_cards") or {}
    seats = ("p1", "p2", "p3")
    free_cool = [s for s in seats if COOL_CARD not in (used.get(s) or ())]
    if free_cool:
        n += 1
        why.append(f"不安-1札(残{len(free_cool)}席)")
    free_gw2 = any(GW_PLUS2 not in (used.get(s) or ()) for s in seats)
    me = chars.get(who) or {}
    area = me.get("area")
    if area is None:
        return {"total": n, "why": ",".join(why) or "なし"}
    for name, c in chars.items():
        if name == who or not c.get("alive") or c.get("area") != area:
            continue
        for ab in GOODWILL_ABILITIES.get(name, ()):   # キャラシート＝公開情報
            key = (name, ab.get("name"))
            if key not in COOL_ABILITIES:
                continue
            need = int(ab.get("hearts", COOL_ABILITIES[key][0]))
            cls = COOL_ABILITIES[key][1]
            if cls == "student" and who not in STUDENTS:
                continue
            gw = int(c.get("goodwill", 0))
            if gw >= need:
                tag = f"♡{gw}"
            elif free_gw2 and gw + 2 >= need:
                tag = f"♡{gw}→友好+2"
            elif gw + 1 >= need:
                tag = f"♡{gw}→友好+1"
            else:
                continue
            n += 1
            why.append(f"{name}:{ab.get('name')}({tag})")
            break
    return {"total": n, "why": ",".join(why) or "なし"}


def lower_bound(view: dict, marg: dict, who: str, crit: int) -> dict:
    """★下限 `L = u + 供給の下限 − 冷却の上限` を1対象ぶん計算する（単一ソース）。"""
    chars = {c["name"]: c for c in view["characters"]}
    me = chars.get(who) or {}
    u = int(me.get("unrest", 0))
    card = 1 if b262_supply_likely(view, who) else 0
    ml_ok, ml = misleader_supply_certain(marg, chars, who)
    sup = card + (1 if ml_ok else 0)
    cool = max_cooling(view, chars, who)
    return {"who": who, "u": u, "crit": crit, "supply_card": card,
            "supply_ability": 1 if ml_ok else 0, "ml_support": ml,
            "cool": cool["total"], "cool_why": cool["why"],
            "L": u + sup - cool["total"],
            # ★L0＝**供給を1つも予測しない版**。脚本家の供給は相手の選択なので
            #   「下限」としては 0 が正しい＝`L0 ≥ 臨界` だけが**予測を含まない断言**。
            #   `L` は B-266 の試作どおり「予測つきの下限」＝相手モデルを1つ含む。
            "L0": u - cool["total"]}


# ---------------------------------------------------------------------------
# 観測
# ---------------------------------------------------------------------------
class Rec:
    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.fire: list[dict] = []     # L≥臨界 の行（1行＝1席1候補）
        self.cool: list[dict] = []     # ★致死日の候補へ `不安-1` を実打した行（L の値を問わない）
        self.err: list[str] = []
        self.meta: dict = {}
        self.incidents: list[dict] = []   # (loop, day, name, occurs)

    def to_json(self) -> dict:
        return {"counts": dict(self.c), "fire": self.fire, "cool": self.cool,
                "err": self.err, "meta": self.meta, "incidents": self.incidents}

    @classmethod
    def from_json(cls, d: dict) -> "Rec":
        r = cls()
        r.c.update(d.get("counts", {}))
        r.fire = list(d.get("fire", ()))
        r.cool = list(d.get("cool", ()))
        r.err = list(d.get("err", ()))
        r.meta = dict(d.get("meta", {}))
        r.incidents = list(d.get("incidents", ()))
        return r


def _is_cool(o: dict, who: str) -> bool:
    return (o.get("card") == COOL_CARD and o.get("target_kind") == "character"
            and o.get("target") == who)


def _rank_of(scored: list[tuple], pred) -> dict:
    """`scored`（(option, score) の列）の中で `pred` を満たす最良手の順位と点。"""
    order = sorted(scored, key=lambda t: -t[1])
    for i, (o, s) in enumerate(order, 1):
        if pred(o):
            return {"rank": i, "of": len(order), "score": round(s, 4),
                    "key": "→".join(str(x) for x in _okey(o)[:2])}
    return {"rank": None, "of": len(order), "score": None, "key": None}


def _replacement(rec: Rec, agent, view, options, best, score, row: dict) -> None:
    """★空いた席の振り替え先＝冷却手を除いて**本物の採点器**で argmax を取り直す。

    ★属性は1つも書き換えない（読むだけ）。自己検証＝同じ option を2度採点して
    同値が返ること（採点器の再入性）を全席で確認する。
    """
    who = row["who"]
    base = [(o, float(score(o))) for o in options]
    for o, s in base:                       # ★自己検証（再入性）
        if float(score(o)) != s:
            rec.c["★採点器が再入で食い違った席"] += 1
            rec.err.append(f'再入不一致 {row["script"]}#{row["seed"]} L{row["loop"]}D{row["day"]}')
            break
    rest = [(o, s) for o, s in base if not _is_cool(o, who)]
    if rest:
        top = max(rest, key=lambda t: t[1])
        row["S_card"] = top[0]["card"]
        row["S_target"] = top[0].get("target")
        row["S_kind"] = top[0].get("target_kind")
        row["S_score"] = round(top[1], 4)
    kp = getattr(agent, "_keyperson", None)
    chars = {c["name"]: c for c in view["characters"]}
    area = (chars.get(who) or {}).get("area")
    supply_src = [n for n in (row.get("ml_support") or ()) if n != who]
    row["R3_cool"] = _rank_of(base, lambda o: _is_cool(o, who))
    row["R3_evade"] = _rank_of(base, lambda o: (
        o.get("card") in MOVE_CARDS and o.get("target_kind") == "character"
        and kp is not None and o.get("target") == kp
        and (chars.get(kp) or {}).get("area") == area))
    row["R3_cut"] = _rank_of(base, lambda o: (
        o.get("card") == MOVE_BAN and o.get("target_kind") == "character"
        and o.get("target") in supply_src))
    row["kp"] = kp
    row["supply_src"] = supply_src
    row["top5"] = [{"key": "→".join(str(x) for x in _okey(o)[:2]), "score": round(s, 3)}
                   for o, s in sorted(base, key=lambda t: -t[1])[:5]]


# ---------------------------------------------------------------------------
# ★FableA 追加要求 A/B/D（2026-08-20）＝「供給を切る手」の実在と点の出所
# ---------------------------------------------------------------------------
def _return_line(score, o) -> tuple[int | None, float]:
    """`score(o)` が **`heuristic_protagonist.score` のどの行で return したか**を採る。

    `sys.settrace` で `score` フレームだけを追う（★読むだけ・戻り値は捨てない）。
    トレース中の値と素の値が食い違ったら `None` を返す（＝信用しない）。
    """
    import sys
    hit: list[int] = []

    def _local(frame, event, arg):
        if event == "return":
            hit.append(frame.f_lineno)
        return _local

    def _tracer(frame, event, arg):
        if (event == "call" and frame.f_code.co_name == "score"
                and frame.f_code.co_filename.endswith("heuristic_protagonist.py")):
            return _local
        return None

    plain = float(score(o))
    old = sys.gettrace()
    sys.settrace(_tracer)
    try:
        traced = float(score(o))
    finally:
        sys.settrace(old)
    if traced != plain or not hit:
        return None, plain
    return hit[0], plain


def _cut_candidates(view: dict, agent, who: str, options) -> dict:
    """★A＝`移動禁止`→供給元 が本当に「供給を切る手」か（3段の強さで数える）。

    - `loose` ＝ミスリーダーの台（役職周辺確率>0）の誰かへの `移動禁止`（＝本レーンの R3_cut）。
    - `mid`   ＝そのうち**犯人と別エリア**（＝今日入室してくる筋を止める形。同室者を
                ピンしても供給は切れない＝`rules/50:139` の同一エリア条件）。
    - `strict`＝さらに**今日その対象に脚本家の伏せ札**がある（＝寄せ移動の線が実在）。
                これは実装の B-64（`_b64_ml_approach_block` の条件(3)(4)）と同じ形。
    """
    marg = agent._belief.role_marginals()
    sup = {n for n, m in marg.items() if float(m.get("ミスリーダー", 0.0)) > 1e-9} - {who}
    chars = {c["name"]: c for c in view["characters"]}
    area = (chars.get(who) or {}).get("area")
    fd = {p.get("target") for p in view.get("placements", ()) or ()
          if p.get("owner") == "mastermind" and p.get("target_kind") == "character"}
    out = {"loose": [], "mid": [], "strict": []}
    for o in options:
        if o.get("card") != MOVE_BAN or o.get("target_kind") != "character":
            continue
        t = o.get("target")
        if t not in sup:
            continue
        c = chars.get(t) or {}
        out["loose"].append(t)
        if c.get("alive") and c.get("area") != area:
            out["mid"].append(t)
            if t in fd:
                out["strict"].append(t)
    return out


def _hook_factory(rec: Rec, script_obj, name: str, seed: int):
    """`B100_HOOK` に挿す観測子（★戻り値は使われない＝棋譜に影響しない）。"""
    from engine.data import unrest_threshold_of

    def _hook(agent, view, options, best, score):
        try:
            rec.c["席（set_card 決定）"] += 1
            day, lp = view.get("day"), view.get("loop")
            if day not in (getattr(agent, "_lethal_days", None) or ()):
                return
            rec.c["致死日の席"] += 1
            marg = agent._belief.role_marginals()
            chars = {c["name"]: c for c in view["characters"]}
            known = (getattr(agent, "_known_culprits", None) or {}).get(day)
            cands = (getattr(agent, "_culprit_cands", None) or {}).get(day) or ()
            if known:
                rec.c["致死日 ∧ 犯人確定 の席"] += 1
            hit_fire = False
            for cn in sorted(cands):
                c = chars.get(cn)
                if not c or not c.get("alive"):
                    continue
                crit = unrest_threshold_of(cn)
                if crit is None:
                    continue
                lb = lower_bound(view, marg, cn, int(crit))
                fire = lb["L"] >= int(crit)
                cooled = bool(best.get("card") == COOL_CARD
                              and best.get("target_kind") == "character"
                              and best.get("target") == cn)
                # ★分母（着手判断の材料）＝発火しなかった側も分布で残す
                d0 = min(4, max(-4, lb["L"] - int(crit)))
                rec.c[f"致死日の候補 L−臨界={d0}"] += 1
                rec.c[f'致死日の候補 L0−臨界={min(4, max(-4, lb["L0"] - int(crit)))}'] += 1
                rec.c[f'致死日の候補 u−臨界={min(4, max(-4, lb["u"] - int(crit)))}'] += 1
                if lb["L0"] >= int(crit):
                    rec.c["★L0≥臨界（供給を予測しない断言）"] += 1
                rec.c[f'致死日の候補 供給下限={lb["supply_card"] + lb["supply_ability"]}'] += 1
                rec.c[f'致死日の候補 冷却上限={min(4, lb["cool"])}'] += 1
                if known == cn:
                    rec.c[f"確定犯人 L−臨界={d0}"] += 1
                if cooled:
                    rec.c["致死日の候補へ `不安-1` を実打した席（全体）"] += 1
                    rec.c[f"実打冷却 L−臨界={d0}"] += 1
                    rec.c[f'実打冷却 L0−臨界={min(4, max(-4, lb["L0"] - int(crit)))}'] += 1
                if lb["L0"] >= int(crit):
                    fire = True          # ★L0 側で立つ席は必ず記録する（L ≥ L0）
                if not (fire or cooled):
                    continue
                row = dict(lb)
                row.update({"script": name, "seed": seed, "loop": lp, "day": day,
                            "seat": view.get("seat"), "n_cands": len(cands),
                            "known": bool(known == cn), "fire": bool(fire),
                            "played_cool": cooled,
                            "played": "→".join(str(x) for x in _okey(best)[:2]),
                            "true_role": _truth_of(script_obj, cn)})
                if cooled:      # ★振り替え先は「実際に冷却を打った席」でだけ数える
                    _replacement(rec, agent, view, options, best, score, row)
                    rec.cool.append(row)
                if fire:
                    hit_fire = True
                    rec.fire.append(row)
            if hit_fire:
                rec.c["L≥臨界の対象が居た席（席単位）"] += 1
        except Exception as exc:      # ★フック側の例外は呼び出し元が握り潰す＝自分で記録する
            rec.c["★フック内の例外"] += 1
            rec.err.append(f"{type(exc).__name__}: {exc}")
    return _hook


def _run(script_obj, name: str, seed: int, loops: int, rec: Rec | None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hp_mod
    from sim import run_game

    orig_hook = hp_mod.B100_HOOK
    if rec is not None:
        hp_mod.B100_HOOK = _hook_factory(rec, script_obj, name, seed)
    try:
        agent = HeuristicProtagonist(seed)
        state, _ = run_game(replace(script_obj, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": agent, "p2": agent, "p3": agent})
    finally:
        hp_mod.B100_HOOK = orig_hook
    if rec is not None:
        rec.meta.update({"winner": state.winner, "loop_no": state.loop_no,
                         "days_per_loop": script_obj.days_per_loop})
        rec.incidents = [{"loop": e.get("loop"), "day": e.get("day"),
                          "name": e.get("name"), "occurs": bool(e.get("occurs"))}
                         for e in state.history if e.get("event") == "incident"]
    return state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_seats(a) -> int:
    before = defaults_banner()
    print(f"[b269] {knob_baseline_banner()}")
    games = parse_pick(a.pick, a.days)
    os.makedirs(a.outdir, exist_ok=True)
    for name, seed, sc in games:
        p = os.path.join(a.outdir, f"{name}__{seed}.json")
        if os.path.exists(p) and not a.force:
            continue
        rec = Rec()
        st = _run(sc, name, seed, a.loops, rec)
        d = rec.to_json()
        d["meta"].update({"script": name, "seed": seed, "days": a.days,
                          "loops": a.loops, "winner": st.winner, "loop_no": st.loop_no})
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        print(f"  {name}#{seed}: 席{rec.c['席（set_card 決定）']} 致死日{rec.c['致死日の席']} "
              f"L≥臨界{len(rec.fire)} 実打冷却{len(rec.cool)} "
              f"例外{rec.c['★フック内の例外']} winner={st.winner} L{st.loop_no}")
    check_no_knob_writes(before, driver="arena.b269_audit")
    print("[b269] 切替口の健全性検査 ✅（測定前後で1bitも動いていない）")
    return 0


def cmd_verify(a) -> int:
    """★プローブの無害性＝観測あり／なしで棋譜が1ビットも動かないことを確認する。"""
    games = parse_pick(a.pick, a.days)
    bad = 0
    for name, seed, sc in games:
        s0 = _run(sc, name, seed, a.loops, None)
        s1 = _run(sc, name, seed, a.loops, Rec())
        h0 = json.dumps(s0.history, ensure_ascii=False, sort_keys=True)
        h1 = json.dumps(s1.history, ensure_ascii=False, sort_keys=True)
        ok = (h0 == h1 and s0.winner == s1.winner and s0.loop_no == s1.loop_no)
        print(f"  {name}#{seed}: 棋譜一致={ok}")
        bad += 0 if ok else 1
    print(f"[b269] プローブの無害性: 不一致 {bad} 件")
    return 1 if bad else 0


def _load(outdir: str) -> list[Rec]:
    out = []
    for fn in sorted(os.listdir(outdir)):
        if fn.endswith(".json"):
            with open(os.path.join(outdir, fn), encoding="utf-8") as f:
                out.append(Rec.from_json(json.load(f)))
    return out


def _pct(x: int, y: int) -> str:
    return f"{100.0 * x / y:.1f}%" if y else "—"


def cmd_report(a) -> int:
    recs = _load(a.outdir)
    fire = [r for rec in recs for r in rec.fire]
    cooled = [r for rec in recs for r in rec.cool]
    errs = [e for rec in recs for e in rec.err]
    seats = sum(rec.c.get("席（set_card 決定）", 0) for rec in recs)
    lethal = sum(rec.c.get("致死日の席", 0) for rec in recs)
    print(f"== {a.outdir} ==  局数 {len(recs)} ／ 席 {seats} ／ 致死日の席 {lethal}")
    print(f"[自己検証] フック内例外 {sum(rec.c.get('★フック内の例外', 0) for rec in recs)} 件"
          f" ／ 採点器の再入不一致 {sum(rec.c.get('★採点器が再入で食い違った席', 0) for rec in recs)} 件"
          f" ／ err {len(errs)} 件")
    if errs:
        print(f"  最初の err= {errs[0]}")
    known = sum(rec.c.get("致死日 ∧ 犯人確定 の席", 0) for rec in recs)
    print(f"[分母] 致死日 ∧ 犯人が1人に確定 の席 = {known}（致死日の席の {_pct(known, lethal)}）")
    agg: Counter = Counter()
    for rec in recs:
        agg.update(rec.c)
    agg0 = agg
    print(f'[L0] ★供給を予測しない断言 `L0 = u − 冷却上限 ≥ 臨界` の (席,候補) = '
          f'{agg0.get("★L0≥臨界（供給を予測しない断言）", 0)}')
    for pref in ("確定犯人 L−臨界=", "致死日の候補 L−臨界=", "致死日の候補 L0−臨界=",
                 "致死日の候補 u−臨界=", "実打冷却 L−臨界=", "実打冷却 L0−臨界=",
                 "致死日の候補 供給下限=", "致死日の候補 冷却上限="):
        row = {k[len(pref):]: v for k, v in sorted(agg.items()) if k.startswith(pref)}
        print(f"  [分布] {pref[:-1]} {row}")
    narrow = [r for r in fire if r["known"]]
    print(f"[N_fire] ★L≥臨界 の (席,候補) = {len(fire)}"
          f"（うち犯人が1人に確定＝狭い述語 = {len(narrow)}）")
    print(f"[実打冷却] 致死日の犯人候補へ `不安-1` を打った (席,候補) = {len(cooled)}"
          f"／★そのうち L≥臨界（＝空振りと判定される）= "
          f"{sum(1 for r in cooled if r['fire'])}"
          f"（狭い述語では {sum(1 for r in cooled if r['fire'] and r['known'])}）"
          f"＝**止める価値のある席**")
    # ---- 偽陽性 ----------------------------------------------------------
    occ: dict = {}
    for rec in recs:
        key0 = (rec.meta.get("script"), rec.meta.get("seed"))
        for i in rec.incidents:
            k0 = (key0, i["loop"], i["day"])
            occ[k0] = occ.get(k0, False) or i["occurs"]
    for lab, rows in (("広い（候補どまりを含む）", fire), ("狭い（犯人確定）", narrow)):
        days = {((r["script"], r["seed"]), r["loop"], r["day"]) for r in rows}
        fp = [k for k in days if not occ.get(k, False)]
        print(f"[FP:{lab}] L≥臨界 を出した (局,L,D) = {len(days)} ／"
              f" ★**その日の事件が発生しなかった** = {len(fp)}"
              f"（偽陽性率 {_pct(len(fp), len(days))}）")
        for k in sorted(fp, key=str)[:8]:
            print(f"    偽陽性: {k[0][0]}#{k[0][1]} L{k[1]}D{k[2]}")
    # ---- 振り替え先 ------------------------------------------------------
    waste = [r for r in cooled if r["fire"]]
    print(f"\n[S] ★空いた席の振り替え先（L≥臨界 かつ実打冷却の {len(waste)} 席"
          f"・冷却手を外して本物の採点器で argmax）")
    for card, cnt in Counter(f'{r.get("S_card")}→{r.get("S_target")}'
                             for r in waste).most_common(12):
        print(f"    {cnt:4d}  {card}")
    print("    （カード種別）", dict(Counter(r.get("S_card") for r in waste).most_common()))
    print(f"[S:対照] L<臨界（＝有効な冷却）の実打 {len(cooled) - len(waste)} 席の振り替え先（カード種別）",
          dict(Counter(r.get("S_card") for r in cooled if not r["fire"]).most_common()))
    # ---- 牡丹の正解手3種 -------------------------------------------------
    print(f"\n[R3] ★牡丹の正解手3種の順位（実打冷却 {len(cooled)} 席で測る）")
    for lab, key in (("冷却 `不安-1`→候補", "R3_cool"),
                     ("退避 `移動`→守る対象（同室時）", "R3_evade"),
                     ("供給断ち `移動禁止`→供給元", "R3_cut")):
        have = [r[key] for r in cooled if r.get(key) and r[key].get("rank")]
        rk = sorted(x["rank"] for x in have)
        med = rk[len(rk) // 2] if rk else None
        top1 = sum(1 for x in rk if x == 1)
        sc = sorted(x["score"] for x in have)
        print(f"    {lab}: 候補に在る席 {len(have)}/{len(cooled)}"
              f" ／ 中央順位 {med} ／ 1位 {top1} 席"
              f" ／ 点の中央 {sc[len(sc) // 2] if sc else None}")
    # ---- 内訳 ------------------------------------------------------------
    print("\n[内訳] 脚本別の L≥臨界")
    for k, v in Counter(r["script"] for r in fire).most_common(10):
        print(f"    {v:4d}  {k}")
    print("[内訳] 真の役職（L≥臨界 の対象）",
          dict(Counter(r["true_role"] for r in fire).most_common(8)))
    print("[内訳] 供給の内訳（札,能力）",
          dict(Counter((r["supply_card"], r["supply_ability"]) for r in fire).most_common()))
    print("[内訳] 冷却上限", dict(Counter(r["cool"] for r in fire).most_common()))
    print("[内訳] L−臨界", dict(Counter(r["L"] - r["crit"] for r in fire).most_common()))
    return 0


def cmd_log(a) -> int:
    """★教材棋譜に当てて B-266 の試作（検死 doc §3-5 の表）を再現する＝**定義の錨**。

    一般化した `max_cooling`（アイドル／ナース／教師と KB の必要♡を足した版）が、
    牡丹 seed0 で試作と同じ `L` を返すかを目視で突き合わせる。
    """
    from pathlib import Path
    from arena.b251_audit import belief_after
    from arena.b251_audit import load_log
    _meta, ref = load_log(Path(a.log))
    who, crit = a.who, a.critical
    print(f"犯人={who} 臨界={crit}／★B-266 検死 doc §3-5 の表＝L1D2:1 L3D2:1 L5D2:2 L6D2:2")
    seen: set = set()
    for d in ref:
        if d["decision"] != "set_card" or d["actor"] == "mastermind":
            continue
        key = (d["loop"], d["day"])
        if key in seen:
            continue
        seen.add(key)
        v = d["view"]
        if not any(c["name"] == who and c.get("alive") for c in v["characters"]):
            continue
        marg = belief_after(v, []).role_marginals()
        lb = lower_bound(v, marg, who, crit)
        print(f'L{d["loop"]}D{d["day"]} L={lb["L"]} = u{lb["u"]} + 供給'
              f'{lb["supply_card"] + lb["supply_ability"]}'
              f'(札{lb["supply_card"]}+能力{lb["supply_ability"]}) − 冷却{lb["cool"]}'
              f'  {"★冷却では割れない" if lb["L"] >= crit else "冷やせば割れる"}'
              f'  | 台={lb["ml_support"]} | 冷却内訳={lb["cool_why"]}')
    return 0


def cmd_cut(a) -> int:
    """★FableA 追加要求 A/B＝`移動禁止`→供給元 の**実在**と**点の出所**を数える。

    対象席＝「今日が致死日 ∧ その候補へ `不安-1` を実打 ∧ `L ≥ 臨界`」＝本レーンの
    「止める価値のある席」。ここで `移動禁止`→供給元 を3段（loose/mid/strict）で分類し、
    最良のものの**点**と**採点器が return した行**を採る。
    """
    from engine.data import unrest_threshold_of
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hp_mod
    from sim import run_game

    tally: Counter = Counter()
    rows: list[dict] = []

    def _mk(name, seed):
        def hook(agent, view, options, best, score):
            try:
                day = view.get("day")
                if day not in (getattr(agent, "_lethal_days", None) or ()):
                    return
                if not (best.get("card") == COOL_CARD
                        and best.get("target_kind") == "character"):
                    return
                who = best.get("target")
                if who not in ((getattr(agent, "_culprit_cands", None) or {}).get(day) or ()):
                    return
                crit = unrest_threshold_of(who)
                if crit is None:
                    return
                lb = lower_bound(view, agent._belief.role_marginals(), who, int(crit))
                if lb["L"] < int(crit):
                    tally["対照＝L<臨界（有効な冷却）の実打席"] += 1
                    return
                tally["★L≥臨界 かつ実打冷却（止める価値のある席）"] += 1
                cc = _cut_candidates(view, agent, who, options)
                for k in ("loose", "mid", "strict"):
                    if cc[k]:
                        tally[f"移動禁止→供給元 {k}"] += 1
                best_o, best_s, best_ln, best_k = None, None, None, None
                for k in ("strict", "mid", "loose"):
                    if not cc[k]:
                        continue
                    cands = [o for o in options if o.get("card") == MOVE_BAN
                             and o.get("target") in cc[k]]
                    ln_s = [( _return_line(score, o), o) for o in cands]
                    (ln, sc), o = max(ln_s, key=lambda t: t[0][1])
                    best_o, best_s, best_ln, best_k = o, sc, ln, k
                    break
                if best_o is not None:
                    tally[f"最良の段={best_k}"] += 1
                    tally[f"点={round(best_s, 2)}"] += 1
                    tally[f"return 行={best_ln}"] += 1
                    rows.append({"script": name, "seed": seed, "loop": view.get("loop"),
                                 "day": day, "seat": view.get("seat"), "who": who,
                                 "kind": best_k, "target": best_o.get("target"),
                                 "score": round(best_s, 3), "line": best_ln,
                                 "L": lb["L"], "crit": int(crit)})
            except Exception as exc:
                tally["★フック内の例外"] += 1
                rows.append({"err": f"{type(exc).__name__}: {exc}"})
        return hook

    for name, seed, sc in parse_pick(a.pick, a.days):
        hp_mod.B100_HOOK = _mk(name, seed)
        try:
            ai = HeuristicProtagonist(seed)
            run_game(replace(sc, loops=a.loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": ai, "p2": ai, "p3": ai})
        finally:
            hp_mod.B100_HOOK = None
    print(f"== b269 cut（{a.days}日級・{a.pick}）==")
    for k, v in sorted(tally.items()):
        print(f"    {k}: {v}")
    print("  --- 席ごと ---")
    for r in rows:
        print("   ", r)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B-269 フェーズ0（計測のみ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("seats", "verify"):
        p = sub.add_parser(nm)
        p.add_argument("--days", type=int, default=3)
        p.add_argument("--pick", default="all")
        p.add_argument("--loops", type=int, default=8)
        if nm == "seats":
            p.add_argument("--outdir", required=True)
            p.add_argument("--force", action="store_true")
    pr = sub.add_parser("report")
    pr.add_argument("--outdir", required=True)
    pc = sub.add_parser("cut")
    pc.add_argument("--days", type=int, default=3)
    pc.add_argument("--pick", default="all")
    pc.add_argument("--loops", type=int, default=8)
    pl = sub.add_parser("log")
    pl.add_argument("--log", default="docs/feedback_logs/"
                    "牡丹_BTX3d_seed0_殺人計画_2026-08-19.jsonl")
    pl.add_argument("--who", default="男子学生")
    pl.add_argument("--critical", type=int, default=2)
    a = ap.parse_args(argv)
    return {"seats": cmd_seats, "verify": cmd_verify, "report": cmd_report,
            "log": cmd_log, "cut": cmd_cut}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
