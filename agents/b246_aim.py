# -*- coding: utf-8 -*-
"""B-246：**行き先検査**＝`冷却役同行`(77.0)／`寄せ`(42.0) の宛先の振り替え。

## 起票（バックログ §72-41）＝4本のレーンの負けが一点に収束した

| レーン | やったこと | 負け方 |
|---|---|---|
| B-237 | 分離が冗長な席を**譲らせる** | 対象局に触れず |
| B-240 | 床を**撤回する** | 1局も動かず（候補を落としても自前の点で選び直される） |
| B-241 | 席を**調停する** | **玉突き**で退行8局 |
| B-245 | 述語を**開く** | 1席も取れない（開いても **77.0 に 4.00 点差で負ける**） |

∴ 共通の診断＝**席は点数の競争で決まる。単一語彙の小細工では競争に勝てない。**
名指しされた真のボトルネック＝**`冷却役同行`(77.0) が行き先を検査していない**
（B-244 疑問手1「候補7人中1人しか覆えない席に 77点」／疑問手3・4「行き先の部屋に
誰が居るかを一切見ない」）。

## ★設計（4本の負けから導いた制約に厳密に従う）

1. **採点・床・cap には一切触れない**。`best = max(options, key=score)` の**直後**で
   **選ばれた後の手の宛先だけ**を差し替える（B-241 `seat_arb` と同じ層）。
2. ★**振り替え先は「実手と同点」に限る**。
   ＝**席を失うリスクが構造的にゼロ**（点数の競争に勝つ必要が無い）
   ＝§72-40 の判例「実装前に対抗手の点数を数えろ」を**設計で満たす**（差は常に 0.00）。
   ＝§72-32 判例1（落としても選び直される）・§72-39 判例1（譲らせると玉突き）を
   **構造的に回避**する（落とさない・譲らない・同じ語彙が同じ席を取り続ける）。
3. **振り替え先は同じ語彙の手に限る**（述語が成立する手だけ）。
   ＝§72-29「ある語彙から見て無価値」を他語彙の席を潰す根拠にしない。
4. **強制席（`prov=="b100"`）には触れない**。

## 行き先の良し悪しの定義（材料はすべて decide 時点の公開情報＋belief の候補集合）

- `冷却役同行`：**行き先の部屋で覆える危険事件の犯人候補の人数**（多いほど良い）。
  候補集合＝`_incident_danger`（危険度 60 以上・今日以降）×`_culprit_cands`、
  「覆える」＝`_ability_value(冷却役, 不安除去, 候補, view) >= 45.0`
  （★クラス限定〔学生の不安除去は非学生に 15.0〕はこの門で効く＝B-244 §2(1)）。
- `寄せ`：**行き先の部屋に「危険事件の犯人候補」が居ない**こと
  （B-244 疑問手3・4＝運び込んだ先が犯人の部屋だった＝供給を自分から合流させる形）。

★同数のときは**振り替えない**（実手を維持）。振り替え先が複数あるときの選択は
**名前まで含む全順序**で決める＝**列挙順に依存しない**（`tests/test_b246_aim.py` で固定）。
"""
from __future__ import annotations

from engine.data import goodwill_abilities_of

COOLER_NAMES = ("医者", "アイドル", "ナース")


# ------------------------------------------------------------------ 共通部品
def _move_dest_of(agent, view: dict, o: dict):
    """(動かすキャラの dict, 行き先エリア) — 移動札×キャラ以外は (None, None)。"""
    from .heuristic_protagonist import _MOVE_TOGGLE, _move_dest
    if o.get("target_kind") != "character" or o.get("card") not in _MOVE_TOGGLE:
        return None, None
    c = agent._alive(view, o.get("target"))
    if not c:
        return None, None
    return c, _move_dest(c.get("area"), o["card"])


def danger_cands(agent, view: dict) -> dict:
    """{事件日: (危険度, [犯人候補])} — 今日以降・危険度60以上（実装本体と同じ門）。"""
    today = view.get("day")
    out: dict = {}
    for d, dg in (getattr(agent, "_incident_danger", {}) or {}).items():
        if dg < 60.0 or (today is not None and d < today):
            continue
        cands = (getattr(agent, "_culprit_cands", {}) or {}).get(d, ())
        if cands:
            out[d] = (float(dg), list(cands))
    return out


# ------------------------------------------------------------ 冷却役同行 --
def escort_cover(agent, view: dict, o: dict) -> tuple:
    """この移動手が**行き先で覆う**犯人候補の集合と、その危険度の合計。

    空集合を返したら「この手は `冷却役同行` の手ではない」。
    """
    c, dest = _move_dest_of(agent, view, o)
    if c is None or dest is None:
        return frozenset(), 0.0
    tgt = o["target"]
    abis = [a for a in (goodwill_abilities_of(tgt) or [])
            if "不安" in a["name"] and "除去" in a["name"]
            and c["goodwill"] >= a["hearts"]]
    if not abis:
        return frozenset(), 0.0
    hits: set = set()
    dsum = 0.0
    for _d, (dg, cands) in sorted(danger_cands(agent, view).items()):
        for cn in cands:
            if cn == tgt or cn in hits:
                continue
            cc = agent._alive(view, cn)
            if not cc or cc.get("area") != dest:
                continue
            if any(agent._ability_value(tgt, a["name"], cn, view) >= 45.0
                   for a in abis):
                hits.add(cn)
                dsum += dg
    return frozenset(hits), dsum


def escort_redirect(agent, view: dict, options: list, best: dict, score):
    """`冷却役同行` の実手を、**同点でより多くの候補を覆う行き先**へ振り替える。

    振り替えが無ければ None（＝実手のまま）。
    """
    if not getattr(agent, "B246_ESCORT_AIM", False):
        return None
    if best.get("prov") == "b100":
        return None
    from .heuristic_protagonist import PRIORITY
    try:
        s_best = float(score(best))
    except Exception:
        return None
    # ★実手が `冷却役同行` の手か＝素点が 77.0 ちょうど（この分岐は素の PRIORITY を返す）
    #   ∧ 述語成立。床・cap が当たった手はここに来ない＝安全側。
    if abs(s_best - PRIORITY["冷却役同行"]) > 1e-9:
        return None
    cur, cur_d = escort_cover(agent, view, best)
    if not cur:
        return None
    # ★掃引口＝**belief が収束していない局面では振り替えない**。
    #   被覆の数え上げは「候補が何人その部屋に居るか」なので、候補集合が配役の大半を
    #   占める局面（L1 等）では**部屋の人口を数えているだけ**になり、防御の指標にならない
    #   （実測＝`random_BTX#7` L1D2 は候補6人＝ほぼ全員・被覆3は「都市が混んでいる」の意）。
    #   None＝制限なし（初版の挙動）。
    lim = getattr(agent, "B246_MAX_CANDS", None)
    if lim is not None:
        union: set = set()
        for _d, (_dg, cands) in danger_cands(agent, view).items():
            union |= set(cands)
        if len(union) > int(lim):
            return None
    pool = []
    for o in options:
        if o is best:
            continue
        try:
            s = float(score(o))
        except Exception:
            continue
        if abs(s - s_best) > 1e-9:         # ★同点だけ＝席を失うリスクがゼロ
            continue
        hits, dsum = escort_cover(agent, view, o)
        if len(hits) <= len(cur):          # 同数は振り替えない（実手を維持）
            continue
        pool.append((len(hits), dsum, str(o.get("card")), str(o.get("target")), o))
    if not pool:
        return None
    # ★列挙順非依存の全順序＝被覆人数→危険度合計→カード名→対象名
    pool.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))
    _ncand = len({cn for _d, (_dg, cs) in danger_cands(agent, view).items()
                  for cn in cs})
    _diag(agent, view, "escort", best, pool[0][4], len(cur), pool[0][0], s_best,
          ncand=_ncand)
    return pool[0][4]


# ------------------------------------------------------------------ 寄せ --
def yose_fire(agent, view: dict, o: dict) -> bool:
    """この手が `寄せ`(42.0) の述語を満たすか（実装本体と同じ材料）。"""
    c, dest = _move_dest_of(agent, view, o)
    if c is None or dest is None:
        return False
    tgt = o["target"]
    mm_char_now = {p.get("target") for p in (view.get("placements") or [])
                   if p.get("owner") == "mastermind"
                   and p.get("target_kind") == "character"}
    if tgt not in mm_char_now:
        return False
    if tgt == getattr(agent, "_keyperson", None):
        return False
    if tgt in (getattr(agent, "_killer_suspects", ()) or ()):
        return False
    return any((cl := agent._alive(view, n)) and cl.get("area") == dest
               for n in COOLER_NAMES)


def yose_culprits_at_dest(agent, view: dict, o: dict) -> frozenset:
    """`寄せ` の行き先に居る**危険事件の犯人候補**（居たら「汚れた行き先」）。"""
    c, dest = _move_dest_of(agent, view, o)
    if c is None or dest is None:
        return frozenset()
    out: set = set()
    for _d, (_dg, cands) in sorted(danger_cands(agent, view).items()):
        for cn in cands:
            if cn == o.get("target"):
                continue
            cc = agent._alive(view, cn)
            if cc and cc.get("area") == dest:
                out.add(cn)
    return frozenset(out)


def yose_redirect(agent, view: dict, options: list, best: dict, score):
    """`寄せ` の実手が犯人候補の部屋へ運ぶなら、**同点の清潔な行き先**へ振り替える。"""
    if not getattr(agent, "B246_YOSE_AIM", False):
        return None
    if best.get("prov") == "b100":
        return None
    try:
        s_best = float(score(best))
    except Exception:
        return None
    if abs(s_best - 42.0) > 1e-9:
        return None
    if not yose_fire(agent, view, best):
        return None
    if not yose_culprits_at_dest(agent, view, best):
        return None                        # 行き先は既に清潔＝振り替え不要
    pool = []
    for o in options:
        if o is best:
            continue
        try:
            s = float(score(o))
        except Exception:
            continue
        if abs(s - s_best) > 1e-9:
            continue
        if not yose_fire(agent, view, o):
            continue
        if yose_culprits_at_dest(agent, view, o):
            continue
        pool.append((str(o.get("card")), str(o.get("target")), o))
    if not pool:
        return None
    pool.sort(key=lambda t: (t[0], t[1]))
    _diag(agent, view, "yose", best, pool[0][2], -1, -1, s_best)
    return pool[0][2]


# ------------------------------------------------------------------ 検死 --
#: 計測専用フック（`arena/b246_ab.py` が差し込む。既定 None＝何も記録しない）。
DIAG = None


def _diag(agent, view, kind, best, alt, cov_from, cov_to, s, ncand=None):
    if DIAG is None:
        return
    DIAG.append({"kind": kind, "loop": view.get("loop"), "day": view.get("day"),
                 "seat": view.get("seat"), "score": round(s, 4),
                 "from": f'{best.get("card")}→{best.get("target")}',
                 "to": f'{alt.get("card")}→{alt.get("target")}',
                 "cov": [cov_from, cov_to], "ncand": ncand})
