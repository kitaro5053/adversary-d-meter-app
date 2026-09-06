# -*- coding: utf-8 -*-
"""B-226 A/B ドライバ：既定 OFF の4語彙（A/B/C/D）の組み合わせを実測する。

対象の切替口（すべて main に land 済み・既定 OFF・レーン推薦値で倒す）：
  A = B221_BREAKER（反復非適応ブレーカ・cap8pair 構成）
  B = B222_FERRY_GEOMETRY（フェリーピンの幾何ゲート・DIAG_FREE_PINS=2）
  C = B224_COOL_FLOOR（1敗発火の当日冷却 floor103・yield8・条件5/6込み）
  D = B224_WEAK_MATCH（手単位反復 cap8・予防札除外）＝B221_BREAKER とセット

★構造的注意（実装読解＝arena/b226_ab.py 起票時に確認）：
  D は `avoid_moves(weak=True)` で一致判定を**置換**する（`require_pair` は無視）＝
  **A+D はフラグ集合として D と完全同一**（A={BREAKER:T}⊂D={BREAKER:T,WEAK:T}）。
  ∴ 「全部入り A+B+C+D」は定義上 BCD に等しい（別測定は不要）。

`arena/b221_ab.py`／`b224_ab.py` と同じ思想＝クラス属性を実行時に退避→復元して
ベンチを回すだけ（ファイル不変・挙動への介入なし・単独実行前提）。
追加＝**発火席の数え上げプローブ**（読み取り専用）：
  HP.decide（set_card）の後に、この席で各語彙が「生きていた」か
  （回避集合∩options・floor 対象 option・yield 対象 option・B222 ゲート発火）を数え、
  同じ席で複数語彙が発火した「衝突」も数える。decide の戻り値・乱数消費には
  一切触れない（OFF 基準の bit 一致で無害性を検証する）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b226_ab --days 3 \
        --conds off,A,B,C,D --perm id --out docs/仮_b226_log/ab_d3_single.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from agents import HeuristicProtagonist as HP

_A = {"B221_BREAKER": True}
_B = {"B222_FERRY_GEOMETRY": True}
_C = {"B224_COOL_FLOOR": True}
_D = {"B221_BREAKER": True, "B224_WEAK_MATCH": True}
#: ★B-228＝D の絞りなし版（B-224 当時の v2 の再現用。D は既定で
#  `B224_WEAK_EXCLUDE_PIN=True`＝BTX#16 絞りあり＝「完全体」）。
_DX = {"B221_BREAKER": True, "B224_WEAK_MATCH": True,
       "B224_WEAK_EXCLUDE_PIN": False}

#: 条件名 → 上書きするクラス属性（載っていない属性は既定値のまま）。
CONDS: dict[str, dict] = {
    "off": {},
    "A": dict(_A),
    "B": dict(_B),
    "C": dict(_C),
    "D": dict(_D),
    "Dx": dict(_DX),
    "BCDx": {**_B, **_C, **_DX},
    "AB": {**_A, **_B},
    "AC": {**_A, **_C},
    "BC": {**_B, **_C},
    "BD": {**_B, **_D},
    "CD": {**_C, **_D},
    "ABC": {**_A, **_B, **_C},
    "BCD": {**_B, **_C, **_D},   # ＝定義上の「全部入り」（A+D≡D）
}

#: 退避対象（CONDS が触りうる属性＋各語彙の従属定数の全集合）。
_ATTRS = ("B221_BREAKER", "B221_CAP", "B221_REQUIRE_PAIR", "B221_BLOCK_BREAKS",
          "B224_WEAK_MATCH", "B224_WEAK_EXCLUDE_PIN",
          "B224_COOL_FLOOR", "B224_FLOOR", "B224_YIELD_CAP",
          "B222_FERRY_GEOMETRY", "B222_DIAG_FREE_PINS")


class FireProbe:
    """発火席の数え上げ（読み取り専用・挙動非接触）。

    席＝1回の set_card decide。語彙の「発火」の定義（保守的＝
    「この席の採点/ゲートに実際に触れた」）：
      brk   ＝ 回避集合（_b221_avoid_today）と options の積が非空（cap が当たる option がある）
      floor ＝ _b224_cool_today の対象への `不安-1` option が存在（floor が立つ）
      yield ＝ _b224_yield_today 非空かつ対象への非`不安-1` option が存在（cap が当たる）
      b222  ＝ この席の採点中に _b222_ferry_geom が非 None を1回以上返した（ゲート発火）
    衝突＝同じ席で2語彙以上が発火。option 単位の直接衝突
    （floor∩brk＝floor103 を cap8 が上書き／yield∩brk＝同値 cap の重なり）も数える。
    """

    def __init__(self):
        self.seats = 0
        self.fire = Counter()          # 語彙 → 発火席数
        self.collide = Counter()       # frozenset(語彙) → 席数（2語彙以上のみ）
        self.opt_floor_and_brk = 0     # 同一 option が floor 対象∧回避集合（cap が floor を上書き）
        self.opt_yield_and_brk = 0     # 同一 option が yield 対象∧回避集合（同値 cap の重なり）
        self._b222_hits = 0            # decide 内カウンタ（wrapper が加算）

    def snapshot(self) -> dict:
        return {
            "seats": self.seats,
            "fire": dict(self.fire),
            "collide": {"+".join(sorted(k)): v for k, v in self.collide.items()},
            "opt_floor_and_brk": self.opt_floor_and_brk,
            "opt_yield_and_brk": self.opt_yield_and_brk,
        }


_PROBE: FireProbe | None = None
_ORIG_GEOM = HP._b222_ferry_geom
#: ★プローブの内側＝**インストール時点の HP.decide**（perm ラッパを含む）を動的に包む。
#  固定の退避（import 時の decide）だと `arena.tie_noise.install_perm` のラッパを
#  上書きで潰してしまい、perm=rev/h1 が id の再測になる（B-226 実測で検出した事故）。
#  順序契約＝install_perm → install_probe（probe が外側・perm が内側）。
_INNER_DECIDE = HP.decide


def _probed_decide(self, view, decision, options):
    global _PROBE
    if _PROBE is None or decision != "set_card":
        return _INNER_DECIDE(self, view, decision, options)
    _PROBE._b222_hits = 0
    res = _INNER_DECIDE(self, view, decision, options)
    p = _PROBE
    p.seats += 1
    fired = set()
    av = getattr(self, "_b221_avoid_today", None) or frozenset()
    cool = getattr(self, "_b224_cool_today", None) or frozenset()
    yld = getattr(self, "_b224_yield_today", None) or frozenset()
    n_floor_brk = n_yield_brk = 0
    hit_brk = hit_floor = hit_yield = False
    for o in options:
        key = (o.get("card"), o.get("target"), o.get("target_kind"))
        in_av = key in av
        if in_av:
            hit_brk = True
        if (cool and o.get("card") == "不安-1"
                and o.get("target_kind") == "character"
                and o.get("target") in cool):
            hit_floor = True
            if in_av:
                n_floor_brk += 1
        if (yld and o.get("card") != "不安-1"
                and o.get("target_kind") == "character"
                and o.get("target") in yld):
            hit_yield = True
            if in_av:
                n_yield_brk += 1
    if hit_brk:
        fired.add("brk")
    if hit_floor:
        fired.add("floor")
    if hit_yield:
        fired.add("yield")
    if p._b222_hits:
        fired.add("b222")
    for f in fired:
        p.fire[f] += 1
    if len(fired) >= 2:
        p.collide[frozenset(fired)] += 1
    p.opt_floor_and_brk += n_floor_brk
    p.opt_yield_and_brk += n_yield_brk
    return res


def _probed_geom(self, view, area, goal):
    r = _ORIG_GEOM(self, view, area, goal)
    if _PROBE is not None and r is not None:
        _PROBE._b222_hits += 1
    return r


def install_probe() -> FireProbe:
    """★必ず install_perm の**後**に呼ぶ（現在の decide＝perm ラッパを内側に取り込む）。"""
    global _PROBE, _INNER_DECIDE
    _PROBE = FireProbe()
    _INNER_DECIDE = HP.decide
    HP.decide = _probed_decide
    HP._b222_ferry_geom = _probed_geom
    return _PROBE


def uninstall_probe() -> None:
    """★必ず uninstall_perm の**前**に呼ぶ（内側に取り込んだ decide へ戻す）。"""
    global _PROBE
    _PROBE = None
    HP.decide = _INNER_DECIDE
    HP._b222_ferry_geom = _ORIG_GEOM


def run_cond(name: str, days: int, loops: int = 8, perm: str = "id",
             probe: bool = True) -> dict:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    old = {a: getattr(HP, a) for a in _ATTRS}
    for k, v in CONDS[name].items():
        setattr(HP, k, v)
    install_perm(perm)
    pr = install_probe() if probe else None
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if probe:
            uninstall_probe()
        uninstall_perm()
        for a, v in old.items():
            setattr(HP, a, v)
    n_def = sum(1 for r in rep["rows"] if r["outcome"] == "defense")
    print(f"  [{days}日級 perm={perm}] {name}: 防衛={n_def} "
          f"平均={rep['mean_loops_to_win']:.3f} 結末={rep['outcomes']}",
          flush=True)
    if pr is not None:
        rep["fire"] = pr.snapshot()
        print(f"    発火席: {pr.snapshot()}", flush=True)
    return rep


def flips(base: dict, other: dict) -> list[str]:
    rows_b = {f'{r["script"]}#{r["seed"]}': r for r in base["rows"]}
    out = []
    for r in other["rows"]:
        k = f'{r["script"]}#{r["seed"]}'
        b = rows_b[k]
        if (b["loops_to_win"], b["outcome"]) != (r["loops_to_win"], r["outcome"]):
            out.append(f'{k}: {b["loops_to_win"]}[{b["outcome"]}] → '
                       f'{r["loops_to_win"]}[{r["outcome"]}]')
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--conds", default="off,A,B,C,D")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    conds = a.conds.split(",")
    res = {c: run_cond(c, a.days, a.loops, a.perm, probe=not a.no_probe)
           for c in conds}
    base = res.get("off")
    if base is not None:
        for c in conds:
            if c == "off":
                continue
            fl = flips(base, res[c])
            print(f"  flips off→{c}（{len(fl)}件）:")
            for f in fl:
                print(f"    {f}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({c: {"rows": r["rows"], "outcomes": r["outcomes"],
                           "mean": r["mean_loops_to_win"],
                           "fire": r.get("fire")}
                       for c, r in res.items()}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
