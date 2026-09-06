# -*- coding: utf-8 -*-
"""B-150：**`defendable=False` の内訳を割る**（★計測のみ・`agents/` 非接触・挙動不変）。

## 発端

`docs/監査_B149_防御3枠の予算配分_2026-08-04.md` §0-1／§3-1 の最大の発見＝
**覆えなかった脅威 2394／3017 件のうち 98%／97% は `defendable=False`（折り手が1つも立たない）**。
予算落ちは 50／81 件だけ。∴ 主人公が脅威に応えられない理由は「枠の取り合い」ではなく
「**そもそも打つ手が無い**」。B-149 §8-9 はここを**未分離**として申し送っている。本レーンが割る。

## `defendable` の定義（現物）

`agents/defense_plan.py:103-105`
```python
@property
def defendable(self) -> bool:
    return any(c.breakable for c in self.conditions)
```
`Condition.breakable`＝`agents/defense_plan.py:78-80`＝`bool(self.breaks)`。
∴ **`defendable=False` ⇔ 全 `Condition.breaks` が空**。`breaks` を積むのは
`enumerate_threats`（`:1706`）配下の脅威ビルダだけで、そこには**手札（`opts`）を見るゲート**と
**盤面だけで決まるゲート**が混在している。本レーンはその2種を**反実仮想**で分離する。

## 分類（★定義）

各席で、本番と**同じ引数**の `enumerate_threats` を **`options` だけ差し替えて**引き直す
（純関数・rng 非消費＝B-149 のシャドーと同じ作法）。差し替える `options`＝
**`_full_options`＝この席が「手札の制約を一切受けない」場合に置ける手の全体**
（8種すべて × 生存・盤上の全キャラ ＋ 暗躍禁止×全ボード ＋ 幻想が居るボードには全種）。
実在庫との差＝(a) 1/L 札（`移動禁止`／`友好+2`／`不安-1`＝`engine/models.py:28-31`）を
この席がこのループで既に切った、(b) 同ターンに自席/他席が既にその対象へ置いた
（`sim/legal.py:66-70` の `banned_targets`）。

| 分類 | 定義（実測できる形） |
|---|---|
| **在庫** | 本番 `defendable=False` かつ **`_full_options` では `defendable=True`**＝折り手は概念上存在するが、その席の手札事情で今は出せない |
| **供給/構造** | **`_full_options` でも `defendable=False`**＝手札をいくら増やしても折れない。理由は条件の `note` と条件ラベルで細分する |
| **過剰報告** | 上記と**直交**する軸＝その脅威が主張する事象が **その日に起きなかった**（`real is False`）。ground truth の判定器は **`arena/b149_audit._realized` をそのまま import**（二重実装しない） |

★**過剰報告は「防御の失敗」ではなく「脅威検出側の話」**＝ここが大きければ B-149 の 98% の
解釈自体が変わる（チケット §4 の指示）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b150_audit verify --days 3 --end 12
    python -m arena.b150_audit count  --days 3 --json d3.json
    python -m arena.b150_audit count  --days 5 --json d5.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
# ★判定器は既存資産をそのまま使う（二重実装の禁止＝チケット §3）。
from arena.b145_audit import _lost_loops, _outcome, _snap_index
from arena.b146_probe import _true_boards
from arena.b149_audit import (_MMProbe, _day_end_snap, _deaths_by_day,
                              _loop_end_days, _prot_deaths, _realized)
from engine.data import CHARACTER_FORBIDDEN
from engine.models import PROTAGONIST_HAND
from sim import run_game

_AREAS = ("病院", "神社", "都市", "学校")
#: 主人公がボードへ置けるカード（`sim/legal.py:22` PROTAGONIST_BOARD_CARDS）。
_PROT_BOARD_CARDS = frozenset({"暗躍禁止"})
_NAMES = tuple(sorted(CHARACTER_FORBIDDEN, key=len, reverse=True))


# ---------------------------------------------------------------------------
# 反実仮想の手札＝「手札の制約を一切受けない」席
# ---------------------------------------------------------------------------
def _full_options(view: dict) -> list[dict]:
    """`sim/legal.set_card_options` の**手札制約と重ね置き制約だけ**を外した option 列。

    - キャラ対象＝生存かつ盤上（`area is not None`）・**幻想を除く**（`sim/legal.py:33-36`）
    - ボード対象＝`暗躍禁止` は全ボード（`sim/legal.py:22`）／
      **幻想が居るボード**には全種（`sim/legal.py:71-73`・KB 30 幻想特性）
    ★ここに**規則上置けない手を混ぜない**（混ぜると「在庫の問題」の偽陽性になる）。
    """
    chars = [c.get("name") for c in view.get("characters", []) or []
             if c.get("alive", True) and c.get("area") is not None
             and c.get("name") != "幻想"]
    g_area = next((c.get("area") for c in view.get("characters", []) or []
                   if c.get("name") == "幻想" and c.get("alive", True)
                   and c.get("area") is not None), None)
    out: list[dict] = []
    for card in PROTAGONIST_HAND:
        for n in chars:
            out.append({"card": card, "target": n, "target_kind": "character"})
        for a in _AREAS:
            if card in _PROT_BOARD_CARDS or a == g_area:
                out.append({"card": card, "target": a, "target_kind": "board"})
    return out


# ---------------------------------------------------------------------------
# 理由コード（条件の note ＋ ラベルを正規化）
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    """キャラ名/エリア名を伏せて理由コードにする（席ごとの表記ゆれを潰す）。"""
    t = str(s or "")
    for n in _NAMES:
        t = t.replace(n, "〈名〉")
    for a in _AREAS:
        t = t.replace(a, "〈場〉")
    return " ".join(t.split())


def _cond_reason(label: str, note: str) -> str:
    """折れなかった条件1つの理由コード。note があればそれ、無ければラベル。"""
    nt = _norm(note)
    if nt:
        return nt[:70]
    return "note無し／" + _norm(label)[:60]


#: ★『供給/構造』の理由コード → **粗い原因バケット**（doc の Phase 1 表と1対1）。
#  ここは**判定ではなく分類の索引**＝どの理由コードがどのコード地点から来るかは
#  `docs/監査_B150_折り手が立たない理由_2026-08-04.md` §2 の表が正典。
_BUCKETS: tuple = (
    # (部分一致キー, バケット名)
    ("不穏な噂", "S1 規則上止まらない供給"),
    ("既に暗躍が2以上", "S1 規則上止まらない供給"),
    ("移動不可のクロマク", "S1 規則上止まらない供給"),
    ("犯人の不安が臨界に届く", "S2 犯人を1人に絞れない／冷却が届かない"),
    ("蝶の羽ばたきの犯人", "S2 犯人を1人に絞れない／冷却が届かない"),
    ("に居る（効果で死ぬ立ち位置）", "S3 折り手が空振り確定（退避先・向き・移動不能）"),
    ("に暗躍2が載る", "S3 折り手が空振り確定（暗躍禁止に打ち消す札が無い等）"),
    ("この供給を今ターン折る手が手札に無い",
     "S3 折り手が空振り確定（暗躍禁止に打ち消す札が無い等）"),
    ("クロマク供給を今ターン折る移動札が手札に無い",
     "S3 折り手が空振り確定（退避先・向き・移動不能）"),
    ("キラーとKPが同エリア", "S3 折り手が空振り確定（退避先・向き・移動不能）"),
    ("VIPとSKが同エリアで2人きり", "S3 折り手が空振り確定（退避先・向き・移動不能）"),
    ("メインラバーズの不安3以上", "S4 到達不能（1枚では閾値を跨げない）"),
    ("TTの友好が2以下", "S4 到達不能（1枚では閾値を跨げない）"),
    ("2人きりにされうる", "S5 相手が別ルートで作り直せる（ピンが折らない）"),
    ("恋愛連鎖", "S6 設計上ほぼ折り手を持たない条件"),
)


def _bucket(reason: str) -> str:
    for k, b in _BUCKETS:
        if k in reason:
            return b
    return "S9 未分類（★doc に出す）"


def _why_missing(view: dict, card: str, target: str) -> str:
    """その (card,target) が**本番の options に無かった**理由（公開情報だけで確定する）。

    `sim/legal.set_card_options`（`:57-84`）が options から落とす要因は2つしかない：
      (1) **手札に無い**＝1/L 札（`移動禁止`／`友好+2`／`不安-1`＝`engine/models.py:28-31`）を
          **この席がこのループで既に切った**（`sim/state.hand_of` `:545-550`）。
      (2) **対象が既に埋まっている**＝同ターンに自席/他席が既にその対象へ置いた
          （`sim/legal.py:66-70` の `banned_targets`）。
    どちらでもないなら "その他"（＝計測器の想定漏れ＝報告に出す）。
    """
    if card not in (view.get("hand") or []):
        return "1/L札を既に消費（この席・このループ）"
    banned = {p.get("target") for p in (view.get("placements") or [])
              if p.get("owner") != "mastermind"}
    if target in banned:
        return "対象が既に埋まっている（自席/他席の同ターン配置）"
    return "その他（計測器の想定漏れ）"


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝super() の戻り値の後で純関数を再評価するだけ）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """`enumerate_threats` の**本番の引数**を控え、`options` だけ差し替えて引き直す。"""

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
            cap["a"], cap["kw"] = a, dict(kw)   # ★最後の呼び出し＝本番の引数
            return r

        dp.enumerate_threats = _rec
        try:
            chosen = super().decide(view, decision, options)
        finally:
            dp.enumerate_threats = orig
        if "a" not in cap:
            return chosen                        # プランナーが走らなかった席
        stash = getattr(self, "_b100_plan", None)
        prod_ts = list(stash[0]) if stash else None
        # ★このターン、自チームが**この席より前に**既に置いた枚数（0＝そのターンの先頭席）。
        #   `sim/legal.py:66-70` の `banned_targets` はこの枚数だけ対象を減らす＝
        #   「在庫」の主因が席順なのかを分離するための鍵。
        n_prior = sum(1 for p in (view.get("placements") or [])
                      if p.get("owner") != "mastermind")
        row = {"loop": view.get("loop"), "day": view.get("day"),
               "seat": view.get("seat"), "n_prior": n_prior}
        try:
            kw0 = dict(cap["kw"])
            kwF = dict(cap["kw"])
            kwF["options"] = _full_options(view)
            ts0 = orig(*cap["a"], **kw0)         # 本番の再評価（自己検査用）
            tsF = orig(*cap["a"], **kwF)         # 手札無制限
        except Exception as e:                   # noqa: BLE001
            row["error"] = repr(e)
            self.seats.append(row)
            return chosen
        # ★自己検査1＝本番の再評価が production の `threats` を bit 再現するか
        sig0 = [(t.kind, t.label, t.defendable) for t in ts0]
        row["repro"] = (prod_ts is None
                        or sig0 == [(t.kind, t.label, t.defendable)
                                    for t in prod_ts])
        # ★自己検査2＝options を増やしても脅威の**並びと本数**は変わらない
        #   （prob/severity は opts 非依存＝index で対応づけられる）。
        row["aligned"] = ([(t.kind, t.label) for t in ts0]
                          == [(t.kind, t.label) for t in tsF])
        ths: list[dict] = []
        mono_bad = 0
        for i, t in enumerate(ts0):
            tf = tsF[i] if row["aligned"] else None
            d0 = bool(t.defendable)
            dF = bool(tf.defendable) if tf is not None else None
            if d0 and dF is False:
                mono_bad += 1                    # ★単調性違反（0 が正常）
            rec = {"kind": t.kind, "label": str(t.label),
                   "sev": round(float(t.severity), 4),
                   "prob": round(float(t.prob), 4),
                   "fatal": bool(t.fatal), "race": bool(getattr(t, "race", False)),
                   "breached": bool(getattr(t, "breached", False)),
                   "defendable": d0, "defendable_full": dF,
                   "n_cond": len(t.conditions)}
            if not d0:
                # 折れなかった条件ごとに：手札を増やせば折れたか／理由コード
                cr: list[str] = []
                inv: list[str] = []
                inv_why: list[str] = []
                for j, c in enumerate(t.conditions):
                    cf = (tf.conditions[j]
                          if tf is not None and j < len(tf.conditions) else None)
                    if cf is not None and cf.breakable:
                        inv.append(_norm(c.label)[:60])   # 在庫で折れた条件
                        for b in cf.breaks:               # ★何が欠けていたのか
                            inv_why.append(
                                f"{b.card}／" + _why_missing(view, b.card, b.target))
                    else:
                        cr.append(_cond_reason(c.label, c.note))
                rec["reasons"] = cr
                rec["inv_conds"] = inv
                rec["inv_why"] = sorted(set(inv_why))
            ths.append(rec)
        # ★S2 の仮説検定＝「冷却の折り手が立たないのは犯人が1人に絞れていないから」。
        #   `_cooling_breaks`（`agents/defense_plan.py:1119-1121`）は
        #   **生存候補がちょうど1人**のときしか折り手を作らない。今日以降の事件日ごとに
        #   生存候補数を数える（`culprits` は本番と同じ belief 由来の引数をそのまま使う）。
        try:
            cul = (cap["kw"].get("culprits") or {})
            d0 = int(view.get("day", 1) or 1)
            live = {c.get("name") for c in (view.get("characters") or [])
                    if c.get("alive", True)}
            hist = []
            for inc in (view.get("incidents") or []):
                dy = inc.get("day")
                if dy is None or dy < d0:
                    continue
                hist.append(len([c for c in (cul.get(dy) or set()) if c in live]))
            row["culprit_live"] = hist
        except Exception:                        # noqa: BLE001
            row["culprit_live"] = []
        row["mono_bad"] = mono_bad
        row["threats"] = ths
        self.seats.append(row)
        return chosen


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Probe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    snaps = _snap_index(state)
    dayend = _day_end_snap(snaps)
    deaths = _deaths_by_day(state)
    protd = _prot_deaths(state)
    ends = _loop_end_days(state)
    lost = _lost_loops(state)
    days_of_loop: dict = {}
    for (lp, dy) in dayend:
        days_of_loop.setdefault(lp, set()).add(dy)
    for row in hp.seats:
        lp, dy = row.get("loop"), row.get("day")
        row["loop_lost"] = lp in lost
        strict, _wide = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        row["true_boards"] = sorted(strict)
        for t in row.get("threats", ()):
            t["real"] = _realized(t["kind"], t["label"], (lp, dy),
                                  deaths, protd, ends, dayend, strict_boards=strict)
            # ★そのループの**どの日か**に実現したか（「今日は来なかっただけ」を分ける）
            rl = False
            unk = False
            for d2 in sorted(days_of_loop.get(lp, ())):
                v = _realized(t["kind"], t["label"], (lp, d2),
                              deaths, protd, ends, dayend, strict_boards=strict)
                if v is True:
                    rl = True
                    break
                if v is None:
                    unk = True
            t["real_loop"] = None if (not rl and unk) else rl
            # ★board_defeat 限定＝そのラベルの板が**そのループの真の敗北板**か。
            #   偽なら「その板が2に届いてもループ敗北にはならない」＝脅威の的自体が的外れ
            #   （主人公は rule_y を知らないので**合理的な保険**でもある。§解釈は doc）。
            if t["kind"] == "board_defeat":
                t["on_true_board"] = any(a in strict for a in _AREAS
                                         if a in str(t["label"]))
    return {"outcome": _outcome(state), "seats": hp.seats,
            "lost_loops": sorted(lost)}


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
            f" / DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" / B134_CARD_DISTANCE={dp.B134_CARD_DISTANCE}"
            f" / B127_ANYAKU_TARGETING={dp.B127_ANYAKU_TARGETING}"
            f" / _SUSPECT_P={dp._SUSPECT_P} / _LIKELY_P={dp._LIKELY_P}"
            f" / B150 切替口=無し（計測のみ・agents/ 非接触）"
            f" / days={days} loops={loops}")


def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    by_kind = Counter()          # (kind, cls) -> 件
    by_kind_real = Counter()     # (kind, cls, real) -> 件
    reasons = Counter()          # (kind, reason) -> 件
    inv_conds = Counter()        # (kind, 条件ラベル) -> 件（在庫で折れた条件）
    inv_why = Counter()          # (kind, 欠けた理由) -> 件
    buckets = Counter()          # (kind, 原因バケットの組) -> 件（★供給の内訳）
    bucket_real = Counter()      # 同上・うちその日に発生
    bucket_flat = Counter()      # バケット単体 -> 件（脅威を跨いだ粗集計）
    sev_by_cls: dict = {"在庫": [], "供給": [], "可": []}
    d_loop: dict = {}            # (script,seed,loop,kind,label) -> 集約
    d_turn: dict = {}            # (script,seed,loop,day,kind,label) -> 集約
    per_game: list[dict] = []
    scripts: set = set()
    ex_rows: list[dict] = []

    def _acc(store, key, cls, real, lost):
        d = store.get(key)
        if d is None:
            d = store[key] = {"可": 0, "在庫": 0, "供給": 0,
                              "real": False, "lost": bool(lost)}
        d[cls] += 1
        d["real"] = d["real"] or (real is True)
        d["lost"] = d["lost"] or bool(lost)

    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts.add(name)
        res = audit_game(sc, seed, loops=loops)
        g = Counter()
        g["games"] = 1
        for row in res["seats"]:
            g["seats"] += 1
            if row.get("error"):
                g["seat_error"] += 1
                continue
            if not row.get("repro", True):
                g["repro_fail"] += 1
            if not row.get("aligned", True):
                g["align_fail"] += 1
            g["mono_bad"] += int(row.get("mono_bad") or 0)
            ts = row.get("threats") or []
            if not ts:
                continue
            g["seats_with_threats"] += 1
            g["n_threats"] += len(ts)
            for nlv in (row.get("culprit_live") or ()):
                g["culprit_live_" + (str(nlv) if nlv <= 4 else "5+")] += 1
            first = int(row.get("n_prior") or 0) == 0
            if first:
                g["seats_first"] += 1
                g["n_threats_first"] += len(ts)
            for t in ts:
                if t["defendable"]:
                    cls = "可"
                    g["n_defendable"] += 1
                elif t["defendable_full"]:
                    cls = "在庫"
                    g["n_stock"] += 1
                else:
                    cls = "供給"
                    g["n_supply"] += 1
                if first:
                    g[{"可": "n_defendable", "在庫": "n_stock",
                       "供給": "n_supply"}[cls] + "_first"] += 1
                    by_kind[(t["kind"], cls + "@先頭席")] += 1
                sev_by_cls[cls].append(t["sev"])
                by_kind[(t["kind"], cls)] += 1
                r = t.get("real")
                by_kind_real[(t["kind"], cls,
                              "T" if r is True else ("F" if r is False else "U"))] += 1
                rl = t.get("real_loop")
                if cls != "可":
                    g[f"{cls}_real_" + ("T" if r is True else
                                        ("F" if r is False else "U"))] += 1
                    g[f"{cls}_loop_" + ("T" if rl is True else
                                        ("F" if rl is False else "U"))] += 1
                    if r is True and row.get("loop_lost"):
                        g[f"{cls}_real_lost"] += 1
                    for rs in (t.get("reasons") or ()):
                        reasons[(t["kind"], rs)] += 1
                    for ic in (t.get("inv_conds") or ()):
                        inv_conds[(t["kind"], ic)] += 1
                    for iw in (t.get("inv_why") or ()):
                        inv_why[(t["kind"], iw)] += 1
                if t["kind"] == "board_defeat":
                    g["bd_" + cls + ("_真板" if t.get("on_true_board")
                                     else "_偽板")] += 1
                if cls == "供給":
                    bs = sorted({_bucket(rs) for rs in (t.get("reasons") or ())})
                    sig = "＋".join(bs) or "（理由なし）"
                    buckets[(t["kind"], sig)] += 1
                    if r is True:
                        bucket_real[(t["kind"], sig)] += 1
                    for b in bs:
                        bucket_flat[b] += 1
                lost = bool(row.get("loop_lost"))
                _acc(d_loop, (name, seed, row["loop"], t["kind"], t["label"]),
                     cls, r, lost)
                _acc(d_turn, (name, seed, row["loop"], row["day"],
                              t["kind"], t["label"]), cls, r, lost)
                if cls == "在庫" and r is True and len(ex_rows) < 400:
                    ex_rows.append({"script": name, "seed": seed,
                                    "loop": row["loop"], "day": row["day"],
                                    "seat": row["seat"], "kind": t["kind"],
                                    "label": t["label"], "sev": t["sev"],
                                    "lost": row.get("loop_lost"),
                                    "inv": t.get("inv_conds"),
                                    "why": t.get("inv_why")})
        for k, v in g.items():
            c[k] += v
        per_game.append({"script": name, "seed": seed,
                         "outcome": res["outcome"], **dict(g)})
        if verbose:
            print(f"  {name} s{seed}: 席{g['seats']} 脅威{g['n_threats']}"
                  f" 可{g.get('n_defendable', 0)} 在庫{g.get('n_stock', 0)}"
                  f" 供給{g.get('n_supply', 0)}", flush=True)
    def _roll(store) -> dict:
        """独立な脅威の集約＝『そのループ/ターン中に**一度でも** defendable になったか』。"""
        cnt = Counter()
        for d in store.values():
            cnt["n"] += 1
            tag = ("可あり" if d["可"] else ("在庫のみ" if d["在庫"] else "供給のみ"))
            if d["可"] == 0 and d["在庫"] and d["供給"]:
                tag = "在庫+供給"
            cnt[tag] += 1
            if d["real"]:
                cnt[tag + "|発生"] += 1
                if d["lost"]:
                    cnt[tag + "|発生かつ敗北"] += 1
        return dict(cnt)

    return {"days": days, "counts": dict(c), "per_game": per_game,
            "scripts": sorted(scripts),
            "n_scripts": len(scripts), "n_games": len(per_game),
            "by_kind": {f"{k[0]}|{k[1]}": v for k, v in by_kind.items()},
            "by_kind_real": {f"{k[0]}|{k[1]}|{k[2]}": v
                             for k, v in by_kind_real.items()},
            "reasons": {f"{k[0]}|{k[1]}": v for k, v in reasons.items()},
            "inv_conds": {f"{k[0]}|{k[1]}": v for k, v in inv_conds.items()},
            "inv_why": {f"{k[0]}|{k[1]}": v for k, v in inv_why.items()},
            "buckets": {f"{k[0]}|{k[1]}": v for k, v in buckets.items()},
            "bucket_real": {f"{k[0]}|{k[1]}": v for k, v in bucket_real.items()},
            "bucket_flat": dict(bucket_flat),
            "distinct_loop": _roll(d_loop), "distinct_turn": _roll(d_turn),
            "sev": {k: v for k, v in sev_by_cls.items()},
            "stock_hit_rows": ex_rows}


def merge(parts: list[dict]) -> dict:
    """チャンク分割した `run` の結果を合算する（キーは互いに素な局＝単純加算）。"""
    out: dict = {"days": parts[0].get("days"), "counts": Counter(),
                 "per_game": [], "scripts": set(),
                 "by_kind": Counter(), "by_kind_real": Counter(),
                 "reasons": Counter(), "inv_conds": Counter(),
                 "inv_why": Counter(), "buckets": Counter(),
                 "bucket_real": Counter(), "bucket_flat": Counter(),
                 "distinct_loop": Counter(), "distinct_turn": Counter(),
                 "sev": {"可": [], "在庫": [], "供給": []},
                 "stock_hit_rows": []}
    for p in parts:
        for k in ("counts", "by_kind", "by_kind_real", "reasons", "inv_conds",
                  "inv_why", "buckets", "bucket_real", "bucket_flat",
                  "distinct_loop", "distinct_turn"):
            out[k].update(p.get(k) or {})
        out["per_game"].extend(p.get("per_game") or [])
        out["scripts"].update(p.get("scripts") or [])
        for cls in out["sev"]:
            out["sev"][cls].extend((p.get("sev") or {}).get(cls) or [])
        out["stock_hit_rows"].extend(p.get("stock_hit_rows") or [])
    for k in ("counts", "by_kind", "by_kind_real", "reasons", "inv_conds",
              "inv_why", "buckets", "bucket_real", "bucket_flat",
              "distinct_loop", "distinct_turn"):
        out[k] = dict(out[k])
    out["scripts"] = sorted(out["scripts"])
    out["n_scripts"] = len(out["scripts"])
    out["n_games"] = len(out["per_game"])
    return out


def _q(xs: list) -> str:
    if not xs:
        return "—"
    ys = sorted(xs)
    n = len(ys)

    def p(f):
        return ys[min(n - 1, int(f * n))]
    return (f"n={n} min={ys[0]:.3f} p25={p(.25):.3f} med={p(.5):.3f}"
            f" p75={p(.75):.3f} max={ys[-1]:.3f}")


def report(res: dict, days: int, top: int = 24) -> None:
    a_days, a_top = days, top
    c = res["counts"]
    n_th = c.get("n_threats", 0)
    print(f"== B-150：`defendable=False` の内訳（{a_days}日級 {res['n_games']}局"
          f"・独立脚本 {res['n_scripts']} 本・set_card席 {c.get('seats', 0)}）==")
    print(f"  ★自己検査：本番の再評価が再現しなかった席 = {c.get('repro_fail', 0)}"
          f"／並びがずれた席 = {c.get('align_fail', 0)}"
          f"／単調性違反（手札を増やしたら折れなくなった脅威） = {c.get('mono_bad', 0)}"
          f"（いずれも 0 が正常。席エラー={c.get('seat_error', 0)}）")
    print(f"  脅威が1件以上ある席 = {c.get('seats_with_threats', 0)}"
          f"（脅威 {n_th} 件）")
    for cls, key in (("可（defendable=True）", "n_defendable"),
                     ("★在庫（手札を増やせば折れた）", "n_stock"),
                     ("★供給/構造（増やしても折れない）", "n_supply")):
        v = c.get(key, 0)
        vf = c.get(key + "_first", 0)
        nf = c.get("n_threats_first", 0)
        print(f"    {cls:34s} {v:7d} 件"
              f"（{(100.0 * v / n_th if n_th else 0):.1f}%）"
              f"　｜★そのターンの先頭席だけ {vf:6d} 件"
              f"（{(100.0 * vf / nf if nf else 0):.1f}%）")
    print(f"    （先頭席＝自チームがまだ1枚も置いていない席 = "
          f"{c.get('seats_first', 0)} 席／脅威 {c.get('n_threats_first', 0)} 件。"
          f"`sim/legal.py:66-70` の対象重複禁止を受けない席）")
    print("")
    print("  ★『発生した／敗北に繋がった』の併記（B-149 §⑧-2 の教訓）")
    for cls in ("在庫", "供給"):
        tT = c.get(f"{cls}_real_T", 0)
        tF = c.get(f"{cls}_real_F", 0)
        tU = c.get(f"{cls}_real_U", 0)
        lT = c.get(f"{cls}_loop_T", 0)
        lF = c.get(f"{cls}_loop_F", 0)
        lU = c.get(f"{cls}_loop_U", 0)
        print(f"    {cls}：その日に発生 {tT} / 発生せず {tF} / 判定不能 {tU}"
              f"　｜そのループ中に発生 {lT} / 一度も発生せず {lF} / 判定不能 {lU}"
              f"　｜発生かつそのループが敗北 {c.get(f'{cls}_real_lost', 0)}")
    print("")
    print("  ★独立な脅威＝席の重複を排除（3席×日で水増しされる分を落とす）")
    for ttl, key in (("ターン単位 (script,seed,loop,day,kind,label)", "distinct_turn"),
                     ("ループ単位 (script,seed,loop,kind,label)", "distinct_loop")):
        d = res[key]
        print(f"    {ttl}  総数 {d.get('n', 0)}")
        for tag in ("可あり", "在庫のみ", "在庫+供給", "供給のみ"):
            print(f"      {tag:8s} {d.get(tag, 0):6d} 本"
                  f"／うち発生 {d.get(tag + '|発生', 0)}"
                  f"／うち発生かつそのループが敗北 "
                  f"{d.get(tag + '|発生かつ敗北', 0)}")
    print("")
    print("  ★脅威の種類別（全席の件数 可/在庫/供給｜先頭席のみ｜その日に発生した件数）")
    kinds = sorted({k.split("|")[0] for k in res["by_kind"]},
                   key=lambda x: -sum(res["by_kind"].get(f"{x}|{cl}", 0)
                                      for cl in ("可", "在庫", "供給")))
    for k in kinds:
        ok = res["by_kind"].get(f"{k}|可", 0)
        st = res["by_kind"].get(f"{k}|在庫", 0)
        sp = res["by_kind"].get(f"{k}|供給", 0)
        okf = res["by_kind"].get(f"{k}|可@先頭席", 0)
        stf = res["by_kind"].get(f"{k}|在庫@先頭席", 0)
        spf = res["by_kind"].get(f"{k}|供給@先頭席", 0)
        print(f"    {k:22s} 可={ok:6d} 在庫={st:5d} 供給={sp:6d}"
              f" ｜先頭席 可={okf:5d} 在庫={stf:4d} 供給={spf:5d}"
              f" ｜発生 可={res['by_kind_real'].get(f'{k}|可|T', 0)}"
              f" 在庫={res['by_kind_real'].get(f'{k}|在庫|T', 0)}"
              f" 供給={res['by_kind_real'].get(f'{k}|供給|T', 0)}")
    print("")
    print("  ★S2 の仮説検定＝今日以降の事件日ごとの**生存犯人候補数**の分布"
          "（`agents/defense_plan.py:1119-1121`＝ちょうど1人のときだけ冷却が折り手になる）")
    ks = sorted([k for k in c if k.startswith("culprit_live_")])
    tot = sum(c[k] for k in ks) or 1
    print("    " + " ".join(
        f"{k.split('_')[-1]}人={c[k]}({100.0 * c[k] / tot:.1f}%)" for k in ks))
    print("")
    print("  ★board_defeat だけ＝そのラベルの板が『そのループの真の敗北板』か"
          "（偽＝2に届いてもループ敗北にならない板＝rule_y 非公開ゆえの保険）")
    for cls in ("可", "在庫", "供給"):
        print(f"    {cls:4s} 真の敗北板 {c.get('bd_' + cls + '_真板', 0):6d}"
              f" / それ以外の板 {c.get('bd_' + cls + '_偽板', 0):6d}")
    print("")
    print("  ★★『供給/構造』の原因バケット（条件を跨いだ粗集計・1脅威が複数に属しうる）")
    for b, v in sorted((res.get("bucket_flat") or {}).items(), key=lambda x: -x[1]):
        print(f"    {v:7d}  {b}")
    print("")
    print(f"  ★★『供給/構造』の脅威ごとの原因の組 top{a_top}（kind｜組｜件／うち発生）")
    for (k, v) in sorted((res.get("buckets") or {}).items(),
                         key=lambda x: -x[1])[:a_top]:
        kk, sg = k.split("|", 1)
        print(f"    {v:7d} / 発生{(res.get('bucket_real') or {}).get(k, 0):4d}"
              f"  {kk:22s} {sg}")
    print("")
    print(f"  ★『供給/構造』の理由コード top{a_top}（脅威kind｜折れなかった条件の理由）")
    for (k, v) in sorted(res["reasons"].items(), key=lambda x: -x[1])[:a_top]:
        kk, rs = k.split("|", 1)
        print(f"    {v:7d}  {kk:22s} {rs}")
    if res["inv_conds"]:
        print("")
        print("  ★『在庫』で折れた条件 top12（＝手札さえあれば折れた条件）")
        for (k, v) in sorted(res["inv_conds"].items(), key=lambda x: -x[1])[:12]:
            kk, rs = k.split("|", 1)
            print(f"    {v:7d}  {kk:22s} {rs}")
    if res.get("inv_why"):
        print("")
        print("  ★★『在庫』の内訳＝本番の options に無かった理由（カード／理由）")
        for (k, v) in sorted(res["inv_why"].items(), key=lambda x: -x[1])[:a_top]:
            kk, rs = k.split("|", 1)
            print(f"    {v:7d}  {kk:22s} {rs}")
    print("")
    print(f"  severity（可）  {_q(res['sev']['可'])}")
    print(f"  severity（在庫）{_q(res['sev']['在庫'])}")
    print(f"  severity（供給）{_q(res['sev']['供給'])}")
    if res["stock_hit_rows"]:
        print("")
        print(f"  ★在庫落ちかつその日に発生した席（先頭 {a_top}）")
        for h in res["stock_hit_rows"][:a_top]:
            print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                  f"#{h['seat']} lost={h['lost']} {h['kind']} sev={h['sev']}"
                  f" | {h['label']} | 在庫で折れた条件={h['inv']} | 欠品={h.get('why')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "merge"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inputs", nargs="*", default=())
    ap.add_argument("--top", type=int, default=24)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    print(_switches(a.days, a.loops), flush=True)
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
    if a.cmd == "merge":
        parts = []
        for p in a.inputs:
            with open(p, encoding="utf-8") as f:
                parts.append(json.load(f))
        res = merge(parts)
    else:
        res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
                  verbose=a.verbose)
    report(res, a.days, a.top)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
