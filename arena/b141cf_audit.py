# -*- coding: utf-8 -*-
"""B-141cf：`B141_COOLER_VALUE_FUTURE_ONLY` を ON にすると動く局の**全数検死**（★計測のみ）。

起票＝`docs/バックログ_構想メモ_FableA.md` §14（★末尾の「再測定（2026-08-05）」「ユーザー裁定」
「最有力の仮説＝走査順依存の露出」）。前レーンの実測＝`docs/監査_B141_過去事件での冷却役過大評価_2026-08-03.md`。

## 用語（★略語を使う前に定義する）

- **`_incident_danger`** ＝ 主人公AIが毎ターン作り直す辞書。
  **キー＝そのループで事件が予定されている日（1始まり）／値＝その事件の危険度の点数**。
  作られる場所＝`agents/heuristic_protagonist.py:1071-1190`。材料は `view["incidents"]`
  ＝**そのループの事件予定表**（`sim/views.py:107`＝`[{"day":…, "name":…}, …]`）。
- **「冷却」** ＝ 友好能力「不安-1（不安除去）」で犯人候補の不安カウンターを減らすこと。
- **`_ability_value(user, ability, target, view)`** ＝ その友好能力の**点数**を返す関数。
  「不安除去」の枝（`:1986-2020`）は `_incident_danger` を走査し、
  **最初に見つかった「生きている犯人候補」で `return max(45.0, 危険度*0.9) * reach`** する。
- **切替口 `B141_COOLER_VALUE_FUTURE_ONLY`（本doc では版 `a`）** ＝ この走査で
  **`d < view["day"]`（＝もう過ぎた日）のエントリを飛ばす**。既定 False。
  ※`B141_COOLER_ALLPAST_ONLY` を**併用**した版が本来の `a0`（狭い述語版）。**別物**。

## 出す数（定義を先に固定する）

- **flip** ＝ その局の (結末, 防衛までのループ数) が OFF と ON で違うこと。
- **moved** ＝ 棋譜（`(loop, day, event, name)` 列）が動いた局（flip しなくても動くことがある）。
- **AV ストリーム** ＝ 1局の間に起きた「不安除去」枝の `_ability_value` 呼び出しを、
  **起きた順に**並べた列。各要素に (loop, day, user, target, 走査で採用した日, 返り値) を持つ。
  OFF と ON は分岐するまで完全に同じ状態を通るので、**最初に要素が食い違う位置**＝
  **その局で a が最初に効いた1点**（＝退行/改善の起点）。

## 分類（★先に固定する。局単位）

| 記号 | 定義 |
|---|---|
| **R** | 起点の呼び出しで ON の返り値が**走査順に依らず一意**（過去日を落として 15.0 へ落ちた／
  未来の危険事件が実質1つしかない）＝**a の規則的に正しい効果** |
| **S** | 起点の呼び出しで ON の返り値が**`_incident_danger` の並べ替えで変わる**
  ＝**どの未来事件の危険度を採るかが走査順で決まっている**＝a とは無関係の既存欠陥の露出 |
| **N** | 最初に決定が変わった席の**その日の全決定（3席のカード＋友好能力の使用と対象）の
  多重集合が同一**＝席の割り当てが入れ替わっただけ＝ベンチのノイズ（B-161 §4-4(1) と同型） |
| **O** | 上のどれでもない＝機序を個別に書く |

★優先順位＝**S → N → R → O**（起点の機序を先に見る）。副表として R/S/O × N/非N も出す。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面実行）:

    python -m arena.b141cf_audit flips  --days 3 --cfg a
    python -m arena.b141cf_audit why    --days 5 --game random_BTX#2 --cfg a
    python -m arena.b141cf_audit order   --days 3
    python -m arena.b141cf_audit verify  --days 3 --end 12
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.heuristic_protagonist import HeuristicProtagonist as HP
from arena.b145_audit import _outcome          # 結末の判定は既存資産を再利用
from arena.b161_audit import _agg, _one        # 集計・1局実行も既存資産を再利用
from sim import run_game

#: 版（★`off` は**現行 main のクラス既定**＝B-144 land 後。`_DEFAULTS` を張り直さない）。
CONFIGS: dict[str, dict] = {
    "off": {},
    "a":   {"B141_COOLER_VALUE_FUTURE_ONLY": True},
    "a0":  {"B141_COOLER_VALUE_FUTURE_ONLY": True, "B141_COOLER_ALLPAST_ONLY": True},
}
_KEYS = ("B141_COOLER_VALUE_FUTURE_ONLY", "B141_COOLER_ALLPAST_ONLY",
         "B141B_UNLOCK_SAME_DAY", "B143_YIELD")
_CLASS_DEFAULTS = {k: getattr(HP, k) for k in _KEYS}

#: ★いま対局が走っている版（影の再評価で切替口を触った後、**ここへ戻す**）。
_LIVE = "off"


def _apply(cfg: str) -> None:
    for k, v in _CLASS_DEFAULTS.items():
        setattr(HP, k, v)
    for k, v in CONFIGS[cfg].items():
        setattr(HP, k, v)


def _set_live(cfg: str) -> None:
    global _LIVE
    _LIVE = cfg
    _apply(cfg)


def _switches() -> str:
    return json.dumps({k: getattr(HP, k) for k in _KEYS})


def _is_cool(ability: str) -> bool:
    return "不安" in ability and "除去" in ability


# ---------------------------------------------------------------------------
# AV ストリームを取る観測器（★`_ability_value` を包むだけ＝AI の実行経路は本体と同一）
# ---------------------------------------------------------------------------
class _RecDict(dict):
    """`items()` が「何番目の走査で、どのキーまで進んだか」を記録する辞書。

    ★本体のループ本体を**書き写さずに**「走査で採用された日」を知るための仕掛け。
    `_ability_value` の `for d, danger in self._incident_danger.items()` が
    **最初に作る generator**（gen 0）が本体のループ。`return` で抜けると generator は
    最後に渡したキーのまま閉じる＝**そのキーが採用された日**。最後まで回り切った場合は
    `_EXHAUSTED` を積む＝「1件も採用せず 15.0 へ落ちた」。
    """

    _EXHAUSTED = "__exhausted__"

    def __init__(self, src, log: list):
        super().__init__(src)
        self._log = log

    def items(self):
        gid = len(self._log)
        self._log.append([])
        for k, v in dict.items(self):
            self._log[gid].append(k)
            yield (k, v)
        self._log[gid].append(self._EXHAUSTED)


def _order_variants(idg: dict) -> list[tuple[str, dict]]:
    """`_incident_danger` の並べ替え版（走査順依存を暴くための対照）。

    ★実運用の並びは `view["incidents"]` の順＝**日付昇順**（全脚本で検証済＝`--check-sorted`）。
    """
    it = list(idg.items())
    return [("日付昇順", dict(sorted(it, key=lambda kv: kv[0]))),
            ("日付降順", dict(sorted(it, key=lambda kv: -kv[0]))),
            ("危険度降順", dict(sorted(it, key=lambda kv: (-kv[1], kv[0])))),
            ("逆順", dict(reversed(it)))]


class _AVStream(HeuristicProtagonist):
    """「不安除去」枝の `_ability_value` 呼び出しを**起きた順に**記録する。

    ★`super()` の戻り値をそのまま返す＝**挙動不変**（物証＝`verify`）。
    各行に「並べ替えたときの返り値の集合」（`ord_off` / `ord_a`）も入れる＝
    その呼び出しが**走査順に依存しているか**をその場で確定させる。
    """

    #: 影の再評価（並べ替え対照）を取るか。`verify` では False にして純粋な記録だけにする。
    probe_order = True

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.av: list[dict] = []
        self.dec: list[dict] = []
        self._rec = True

    def _ability_value(self, user, ability, target, view) -> float:
        if not (self._rec and _is_cool(ability)):
            return super()._ability_value(user, ability, target, view)
        idg = getattr(self, "_incident_danger", None)
        if not idg:
            v = super()._ability_value(user, ability, target, view)
            self.av.append(self._row(user, target, view, None, v, {}, {}))
            return v
        log: list = []
        self._incident_danger = _RecDict(idg, log)
        try:
            v = super()._ability_value(user, ability, target, view)
        finally:
            self._incident_danger = idg
        sel = None
        if log and log[0] and log[0][-1] != _RecDict._EXHAUSTED:
            sel = log[0][-1]
        ordv = self._shadow_orders(user, ability, target, view, idg) \
            if self.probe_order else {}
        self.av.append(self._row(user, target, view, sel, v, idg, ordv))
        return v

    def _shadow_orders(self, user, ability, target, view, idg) -> dict:
        """`_incident_danger` を並べ替えて `_ability_value` を**影で**評価し直す。

        ★状態は書き換えない（`_incident_danger` を戻し、切替口は `_LIVE` へ戻す）。
        `_ability_value` が再入可能なことは前レーン `arena/b141_audit.py` の
        3回まわし（`ctrl_mismatch=0`）で確認済み。物証＝本モジュールの `verify`。
        """
        out: dict = {}
        self._rec = False
        try:
            for tag, perm in _order_variants(dict(idg)):
                self._incident_danger = perm
                for cfg in ("off", "a", "a0"):
                    _apply(cfg)
                    out[f"{tag}/{cfg}"] = round(float(
                        HeuristicProtagonist._ability_value(
                            self, user, ability, target, view)), 4)
        finally:
            self._incident_danger = idg
            _apply(_LIVE)
            self._rec = True
        return out

    def _row(self, user, target, view, sel, v, idg, ordv) -> dict:
        r = {"loop": view.get("loop"), "day": view.get("day"),
             "user": user, "target": target,
             "sel": sel, "v": round(float(v), 4),
             "idg": [(int(d), round(float(g), 2)) for d, g in idg.items()]}
        if ordv:
            # ★実運用の並び＝`view["incidents"]` の順＝**日付昇順**（`verify` で全脚本を確認）。
            #   ∴ `日付昇順/a` が「版 a を入れた時に実際に返る値」＝`a_nat`。
            r["a_nat"] = ordv.get("日付昇順/a")
            r["ord_off"] = sorted({v2 for k, v2 in ordv.items()
                                   if k.endswith("/off")})
            r["ord_a"] = sorted({v2 for k, v2 in ordv.items()
                                 if k.endswith("/a")})
            r["ord_a0"] = sorted({v2 for k, v2 in ordv.items()
                                  if k.endswith("/a0")})
        return r

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        ch = super().decide(view, decision, options)
        self.dec.append({
            "loop": view.get("loop"), "day": view.get("day"),
            "decision": decision, "leader": view.get("leader"),
            "hand": tuple(sorted(view.get("my_cards", []) or [])),
            "n_av": len(self.av),
            "chosen": {k: ch.get(k) for k in
                       ("card", "target", "target_kind", "character", "ability")
                       if k in ch}})
        return ch


def _game(days: int, key: str):
    from arena.benchmark import benchmark_scripts
    for name, seed, sc in benchmark_scripts(days=days):
        if f"{name}#{seed}" == key:
            return name, seed, sc
    raise SystemExit(f"局が見つからない: {key}")


def _run_stream(sc, seed: int, cfg: str, loops: int = 8) -> tuple:
    _set_live(cfg)
    print(f"  [切替口:{cfg}] {_switches()}", flush=True)
    hp = _AVStream(seed)
    st, _ = run_game(replace(sc, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"),
              e.get("occurs"))
             for e in st.history]
    _set_live("off")
    return hp, _outcome(st), st.loop_no, trace


# ---------------------------------------------------------------------------
# Phase 0＝flip の再現（per-game 全数）
# ---------------------------------------------------------------------------
def flips(days: int = 3, loops: int = 8, cfg: str = "a") -> dict:
    from arena.benchmark import benchmark_scripts

    games = list(benchmark_scripts(days=days))

    def _leg(tag: str) -> dict:
        _set_live(tag)
        print(f"  [切替口:{tag}] {_switches()} / days={days}", flush=True)
        out = {f"{name}#{seed}": _one(sc, seed, loops) for name, seed, sc in games}
        _set_live("off")
        return out

    base, cur = _leg("off"), _leg(cfg)

    def _lw(v):
        return v[1] if v[0] == "defense" else loops + 1

    rows, moved = [], []
    for k in base:
        o, n = base[k], cur[k]
        if o[2] != n[2]:
            moved.append(k)
        if (o[0], _lw(o)) != (n[0], _lw(n)):
            rows.append({"game": k, "off": [o[0], _lw(o)], "on": [n[0], _lw(n)],
                         "dir": ("改善" if _lw(n) < _lw(o) else "退行")})
    return {"days": days, "cfg": cfg, "off": _agg(base), "on": _agg(cur),
            "n_moved": len(moved), "moved": moved,
            "n_flip": len(rows), "flips": rows,
            "平均ループ": {"off": round(sum(_lw(v) for v in base.values())
                                      / max(1, len(base)), 3),
                          "on": round(sum(_lw(v) for v in cur.values())
                                      / max(1, len(cur)), 3)}}


# ---------------------------------------------------------------------------
# Phase 1＝1局の検死（起点の呼び出しと、最初に決定が変わった席）
# ---------------------------------------------------------------------------
def why(days: int, key: str, cfg: str = "a", loops: int = 8) -> dict:
    name, seed, sc = _game(days, key)
    hp0, o0, l0, t0 = _run_stream(sc, seed, "off", loops)
    hp1, o1, l1, t1 = _run_stream(sc, seed, cfg, loops)

    # (1) AV ストリームの最初の食い違い
    #     ★2種類を分けて出す：
    #       first_av  ＝ 行のどこかが違う最初の呼び出し（`sel`＝採用した日だけが違う場合を含む）。
    #       first_val ＝ ★**返り値 `v` が違う**最初の呼び出し＝**この局で a が最初に効いた1点**。
    #       `sel` だけが違っても AI の挙動は1ビットも変わらない（返るのは数値だけ）＝
    #       起点は必ず `first_val` の側で見る。
    n = min(len(hp0.av), len(hp1.av))

    def _first(pred) -> dict | None:
        for x in range(n):
            if pred(hp0.av[x], hp1.av[x]):
                a, b = hp0.av[x], hp1.av[x]
                return {"idx": x, "off": a, "on": b,
                        "同一局面": all(a[k] == b[k] for k in
                                        ("loop", "day", "user", "target", "idg"))}
        return None

    first_av = _first(lambda a, b: a != b)
    first_val = _first(lambda a, b: a["v"] != b["v"])
    # (2) 決定ストリームの最初の食い違い
    #     ★比較は**決定そのもの**（loop/day/種別/リーダー/選んだ手）だけ。
    #       `n_av`（その時点までの呼び出し数）は観測用の付随情報で、AI の挙動ではない
    #       ＝ここに入れると「同じ手を打っているのに違う席」と誤検出する。
    def _dk(d: dict) -> tuple:
        return (d["loop"], d["day"], d["decision"], d["leader"],
                json.dumps(d["chosen"], ensure_ascii=False, sort_keys=True))

    j = 0
    m = min(len(hp0.dec), len(hp1.dec))
    while j < m and _dk(hp0.dec[j]) == _dk(hp1.dec[j]):
        j += 1
    first_dec = None
    if j < m:
        first_dec = {"idx": j, "off": hp0.dec[j], "on": hp1.dec[j]}
        lp, dy = hp0.dec[j]["loop"], hp0.dec[j]["day"]
        # ★その日の**全決定**（3席のカード＋友好能力の使用）を並べる。
        #   N（籤帯）の判定は「この一覧の多重集合が同一か」で行う＝カードだけでなく
        #   **能力の対象**まで含める（対象が違えば冷やした相手が違う＝実挙動の差）。
        first_dec["その日の席"] = {
            "off": [{"kind": d["decision"], **d["chosen"]} for d in hp0.dec
                    if d["loop"] == lp and d["day"] == dy],
            "on": [{"kind": d["decision"], **d["chosen"]} for d in hp1.dec
                   if d["loop"] == lp and d["day"] == dy]}
        first_dec["その日の3席"] = {
            "off": [d["chosen"] for d in hp0.dec
                    if d["loop"] == lp and d["day"] == dy
                    and d["decision"] == "set_card"],
            "on": [d["chosen"] for d in hp1.dec
                   if d["loop"] == lp and d["day"] == dy
                   and d["decision"] == "set_card"]}
    # (2b) ★**最初に決定が変わる席までに返り値が変わった呼び出しを全数**数える
    #      （起点1件だけでなく、決定を動かしうる呼び出しを漏れなく点検する）。
    nav = hp0.dec[j]["n_av"] if first_dec else min(len(hp0.av), len(hp1.av))
    scan = {"照合した呼び出し数": nav, "返り値が変わった呼び出し": 0,
            "うち走査順依存(S)": 0, "うち全部過去→15.0": 0,
            "うち未来の事件へ乗り換え": 0, "S の現物": []}
    for x in range(min(nav, len(hp0.av), len(hp1.av))):
        a, b = hp0.av[x], hp1.av[x]
        if a["v"] == b["v"]:
            continue
        scan["返り値が変わった呼び出し"] += 1
        if len(b.get("ord_a") or []) > 1:
            scan["うち走査順依存(S)"] += 1
            if len(scan["S の現物"]) < 5:
                scan["S の現物"].append({"idx": x, "off": a, "on": b})
        if b.get("sel") is None:
            scan["うち全部過去→15.0"] += 1
        else:
            scan["うち未来の事件へ乗り換え"] += 1
    # (3) 棋譜の最初の食い違い
    k = 0
    q = min(len(t0), len(t1))
    while k < q and t0[k] == t1[k]:
        k += 1
    return {"game": key, "days": days, "cfg": cfg,
            "off": {"outcome": o0, "loop_no": l0, "n_av": len(hp0.av),
                    "n_dec": len(hp0.dec)},
            "on": {"outcome": o1, "loop_no": l1, "n_av": len(hp1.av),
                   "n_dec": len(hp1.dec)},
            "first_av": first_av, "first_val": first_val, "first_dec": first_dec,
            "scan": scan,
            "first_hist": {"idx": k, "off": t0[k] if k < q else None,
                           "on": t1[k] if k < q else None},
            "hist_head_off": t0[max(0, k - 3):k + 4],
            "hist_head_on": t1[max(0, k - 3):k + 4]}


# ---------------------------------------------------------------------------
# Phase 2＝走査順依存そのものの射程（★未来日どうしの競合を数える）
# ---------------------------------------------------------------------------
def _tally_row(c: Counter, r: dict) -> None:
    """1呼び出し分の記録を数える（★数える軸の定義は本関数が単一ソース）。

    - `呼び出し(不安除去)` ＝ 「不安除去」枝の `_ability_value` 呼び出し。
    - `エントリあり` ＝ `_incident_danger` が空でない呼び出し。
    - `未来2件以上` ＝ `d >= view["day"]` のエントリが2件以上ある呼び出し。
    - `現行競合` ＝ **現行（OFF）で**並べ替えると返り値が変わる呼び出し
      （過去日と未来日の競合も含む＝前レーンの「混在」とは**定義が違う**）。
    - ★`未来競合` ＝ **版 a（過去日を飛ばす）で**並べ替えると返り値が変わる呼び出し
      ＝**どの未来事件の危険度を採るかが走査順で決まっている**＝Phase 2 の本命。
    """
    c["呼び出し(不安除去)"] += 1
    idg = r.get("idg") or []
    if not idg:
        return
    c["エントリあり"] += 1
    day = r["day"]
    fut = [d for d, _ in idg if d >= day]
    if len(fut) >= 2:
        c["未来2件以上"] += 1
    if len(idg) - len(fut) > 0:
        c["過去日あり"] += 1
    if len(r.get("ord_off") or []) > 1:
        c["現行競合"] += 1
    if len(r.get("ord_a") or []) > 1:
        c["★未来競合"] += 1
        if len(fut) >= 2:
            c["★未来競合(未来2件以上)"] += 1
        # ★実運用の並び（日付昇順＝近い日が先に当たる）で、より危険な未来事件の
        #   危険度を取り逃していた呼び出し＝「近い日 ＞ 危険な日」になっている実害の形。
        if r.get("a_nat") is not None and r["a_nat"] < max(r["ord_a"]):
            c["★未来競合_近い日を採って高い危険度を逃した"] += 1
    if len(r.get("ord_a0") or []) > 1:
        c["a0での競合"] += 1


def order(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    rows: list = []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, _o, _l, _t = _run_stream(sc, seed, "off", loops)
        for r in hp.av:
            _tally_row(c, r)
            if len(r.get("ord_a") or []) > 1 and len(rows) < 40:
                rows.append({"game": f"{name}#{seed}", **r})
        n += 1
    return {"days": days, "n_games": n, "counts": dict(c), "rows": rows[:40]}


# ---------------------------------------------------------------------------
# 挙動不変の物証
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    _set_live("off")
    bad, unsorted_sc = [], []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        ds = [i.day for i in sc.incidents]
        if ds != sorted(ds):
            unsorted_sc.append(f"{name}#{seed}:{ds}")
        probe = replace(sc, loops=loops)
        base = HeuristicProtagonist(seed)
        s0, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": base, "p2": base, "p3": base})
        for probe_order in (False, True):
            hp = _AVStream(seed)
            hp.probe_order = probe_order
            s1, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                     "p1": hp, "p2": hp, "p3": hp})
            k0 = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
                  for e in s0.history]
            k1 = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
                  for e in s1.history]
            if not (k0 == k1 and _outcome(s0) == _outcome(s1)
                    and s0.loop_no == s1.loop_no):
                bad.append(f"{name}#{seed}:probe_order={probe_order}")
        n += 1
    return {"days": days, "n_games": n, "mismatch": bad,
            "事件予定表が日付昇順でない脚本": unsorted_sc,
            "ok": not bad and not unsorted_sc}


def classify(w: dict) -> dict:
    """1局の検死結果を **S → N → R → O** の順で分類する（★定義は本関数が単一ソース）。

    - **S** ＝ 起点の呼び出しで、ON の返り値が `_incident_danger` の並べ替えで変わる
      （`ord_a` の値が2種類以上）＝どの未来事件の危険度を採るかが走査順で決まっている。
    - **N** ＝ 最初に決定が変わった席の**その日の set_card 3席の (カード,対象) 多重集合が同一**
      ＝席の割り当てが入れ替わっただけ。
    - **R** ＝ 起点で OFF が採用していた日が**実際に `view["day"]` より前**で、
      ON の返り値が走査順に依らず一意＝a の規則的に正しい効果。
    - **O** ＝ 上のどれでもない（機序を個別に書く）。
    """
    fa, fd = w.get("first_val"), w.get("first_dec")
    if not fa:
        return {"class": "O", "理由": "返り値が変わった呼び出しが1つも無い（a 以外の経路で動いた）"}
    off, on = fa["off"], fa["on"]
    #: ★S は起点1件だけでなく、**最初に決定が変わる席までの全呼び出し**で判定する
    #   （どれか1つでも走査順に依存していれば S 扱い＝仮説に有利な側へ倒す）。
    sc = w.get("scan") or {}
    s_flag = (len(on.get("ord_a") or []) > 1
              or (sc.get("うち走査順依存(S)") or 0) > 0)
    n_flag = None
    if fd and fd.get("その日の席"):
        mo = sorted(json.dumps(c, ensure_ascii=False, sort_keys=True)
                    for c in fd["その日の席"]["off"])
        mn = sorted(json.dumps(c, ensure_ascii=False, sort_keys=True)
                    for c in fd["その日の席"]["on"])
        n_flag = (mo == mn)
    past = off.get("sel") is not None and off["sel"] < off["day"]
    if s_flag:
        cls = "S"
    elif n_flag:
        cls = "N"
    elif past:
        cls = "R"
    else:
        cls = "O"
    return {"class": cls, "S判定(走査順依存)": s_flag, "N判定(同一多重集合)": n_flag,
            "出どころ入替(過去日→未来日)": (off.get("sel") is not None
                                          and on.get("sel") is not None
                                          and off["sel"] != on["sel"]),
            "起点idx": fa.get("idx"),
            "OFFが採用した日": off.get("sel"), "ONが採用した日": on.get("sel"),
            "その時の日付": off.get("day"), "OFFの点数": off.get("v"),
            "ONの点数": on.get("v"), "並べ替え時のON点数": on.get("ord_a"),
            "事件予定と危険度": off.get("idg"), "全数走査": sc}


def triage(days: int, keys: list[str], cfg: str = "a", loops: int = 8) -> dict:
    out = []
    for k in keys:
        w = why(days, k, cfg=cfg, loops=loops)
        out.append({"game": k, **classify(w), "detail": w})
    tab = Counter(r["class"] for r in out)
    return {"days": days, "cfg": cfg, "分類": dict(tab), "局": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-141cf 検死（計測のみ）")
    ap.add_argument("cmd", choices=("flips", "why", "triage", "order", "verify"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--cfg", default="a", choices=tuple(CONFIGS))
    ap.add_argument("--game", default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "flips":
        r = flips(days=a.days, loops=a.loops, cfg=a.cfg)
    elif a.cmd == "why":
        r = why(days=a.days, key=a.game, cfg=a.cfg, loops=a.loops)
    elif a.cmd == "triage":
        r = triage(days=a.days, keys=[s for s in (a.game or "").split(",") if s],
                   cfg=a.cfg, loops=a.loops)
    elif a.cmd == "order":
        r = order(days=a.days, loops=a.loops, start=a.start, end=a.end)
    else:
        r = verify(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
