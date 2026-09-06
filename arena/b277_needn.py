# -*- coding: utf-8 -*-
"""B-277：B-276 の必要局数と「置ける」定義の精査（★測定のみ・挙動は1バイトも変えない）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-100（ユーザー裁定＝案B）。
正典＝`docs/測定_B276_置けるvs置く_負の結果_2026-08-21.md`（特に §8「人間側の n」）。
材料は `arena/b276_audit.py` が採った行をそのまま読む（採取ロジックは複製しない＝単一ソース）。

## 本モジュールが答える3つの問い

1. **判定1 の評価母集団は何か**＝`arena.b276_audit._eval_slices` の第3スライス
   ＝**`dist == 0` の全行**（「置ける」で絞った行ではない）。
   ∴ doc §8 の「1局あたり dist=0 の候補行は約12行」は**この母集団と整合している**。
   一方 §72-100 が数えた「置ける∧dist=0＝2.3行/局」は**別の母集団**（条件付き率
   `P(置いた|置ける)` の分母）＝2つを混同すると必要局数が10倍以上ずれる。

2. **必要局数**＝行は局内で相関する（同じ人間・同じ脚本・同じキャラが何日も出る）。
   ∴ iid 前提の「100行あれば ±0.1」は使えない。**局クラスタ・ブートストラップ**で
   実測の設計効果ごと見積もる（`needn` サブコマンド）。

3. **判定2 に人間の教材は効くか**＝`arena.b276_audit.cmd_fp16` は
   `eval_predictions(corpus, [])` を呼ぶ＝**教材を1行も使わない**。
   人間データを無限に積んだ場合の上界を、**人間だけで較正したセル表**で
   FP16 席を塗り直して近似する（`oracle` サブコマンド）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b277_needn defs   --dirs /tmp/b276/teach
    python -m arena.b277_needn needn  --dirs /tmp/b276/teach [--family 鈴蘭]
    python -m arena.b277_needn oracle --dirs /tmp/b276/d3,/tmp/b276/d5,/tmp/b276/teach \
                                      --b269 /tmp/b269/d3,/tmp/b269/d5
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
from collections import defaultdict

from arena.b276_audit import Rec, cell_of, cell_p, fit_cells

#: 候補となる「置ける」の定義（★正典を書き換えるものではない＝比較のための候補）。
#: 現行の正典＝`(1)`（札チャネル）と `(2)`（札∨能力）＝`arena.b269_audit.b262_supply_likely` 由来。
CAN_DEFS: dict = {
    "(0) 連言なし(dist=0 全行)": lambda r: True,
    "(1) 現行連言 habit∧fd": lambda r: bool(r["habit"] and r["fd"]),
    "(2) 現行『置ける』(札∨能力)": lambda r: bool((r["habit"] and r["fd"]) or r["can_abil"]),
    "(3) habit のみ": lambda r: bool(r["habit"]),
    "(4) fd のみ": lambda r: bool(r["fd"]),
    "(5) habit∨fd": lambda r: bool(r["habit"] or r["fd"]),
}

#: 判定1 の評価母集団＝`arena.b276_audit._eval_slices` の第3スライスと同じ述語
#: （★内容ベースの鏡写し。行番号は持たない。同値性は `tests/test_b277_needn.py` が固定）。
def is_judge1_row(r: dict) -> bool:
    """判定1（教材の致死日供給の予測）が評価する行か＝`dist == 0`。"""
    return r.get("dist") == 0


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------
def load_dir(d: str) -> list[Rec]:
    out: list[Rec] = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                out.append(Rec.from_json(json.load(f)))
    return out


def load_dirs(spec: str) -> list[Rec]:
    recs: list[Rec] = []
    for d in spec.split(","):
        if d.strip():
            recs.extend(load_dir(d.strip()))
    return recs


def human_games(recs: list[Rec], prefix: str | None = None) -> list[list[dict]]:
    """人間が脚本家の教材を「1局＝1リスト」で返す（★局がクラスタの単位）。"""
    out = []
    for rec in recs:
        if rec.meta.get("kind") != "teach" or rec.meta.get("mm") != "human":
            continue
        name = str(rec.meta.get("game") or "")
        if prefix and not name.startswith(prefix):
            continue
        out.append([dict(r, game=name) for r in rec.rows])
    return out


# ---------------------------------------------------------------------------
# 局クラスタ・ブートストラップ
# ---------------------------------------------------------------------------
def _rate(rows: list[dict]) -> float | None:
    return (sum(1 for r in rows if r["did_any"]) / len(rows)) if rows else None


def cluster_ci(pools: list[list[dict]], k: int, rng: random.Random,
               b: int = 2000) -> tuple[float, float, float] | None:
    """k 局を**局単位**で復元抽出したときの率の (2.5%, 中央, 97.5%)。"""
    if not pools:
        return None
    vs = []
    for _ in range(b):
        rows = [r for _ in range(k) for r in rng.choice(pools)]
        v = _rate(rows)
        if v is not None:
            vs.append(v)
    if len(vs) < 50:
        return None
    vs.sort()
    return vs[int(0.025 * len(vs))], st.median(vs), vs[int(0.975 * len(vs)) - 1]


def iid_ci(pools: list[list[dict]], k: int, rng: random.Random,
           b: int = 2000) -> tuple[float, float, float] | None:
    """★誤った前提（行が iid）で同じ行数を引いた場合。設計効果の分母に使う。"""
    flat = [r for p in pools for r in p]
    if not flat or not pools:
        return None
    n = max(1, int(round(k * len(flat) / len(pools))))
    vs = []
    for _ in range(b):
        rows = [rng.choice(flat) for _ in range(n)]
        v = _rate(rows)
        if v is not None:
            vs.append(v)
    if len(vs) < 50:
        return None
    vs.sort()
    return vs[int(0.025 * len(vs))], st.median(vs), vs[int(0.975 * len(vs)) - 1]


def half_width(ci) -> float:
    return (ci[2] - ci[0]) / 2.0 if ci else float("nan")


def design_effect(pools: list[list[dict]], k: int, rng: random.Random,
                  b: int = 2000) -> float:
    """設計効果＝(クラスタ半値幅 / iid 半値幅)^2。1 より大きいほど「行数は水増し」。"""
    hc = half_width(cluster_ci(pools, k, rng, b))
    hi = half_width(iid_ci(pools, k, rng, b))
    return (hc / hi) ** 2 if hi else float("nan")


def games_needed(pools: list[list[dict]], target_hw: float, rng: random.Random,
                 b: int = 1500, ks=(4, 8, 16, 32, 64, 128, 256, 512)) -> int | None:
    """半値幅が `target_hw` 以下になる最小の k（実測曲線の最初の到達点）。"""
    for k in ks:
        if half_width(cluster_ci(pools, k, rng, b)) <= target_hw:
            return k
    return None


# ---------------------------------------------------------------------------
# 判定2 の上界（人間だけで較正したセル表で FP16 席を塗り直す）
# ---------------------------------------------------------------------------
def human_cell_table(recs: list[Rec]) -> dict:
    rows = [r for g in human_games(recs) for r in g]
    return fit_cells(rows)


def repaint_seats(seats: list[dict], table: dict, tau: float = 0.5) -> dict:
    """FP/TP 席を与えられたセル表の確率で再裁定する。seats の各要素＝
    {"row": b276 の行, "fp": bool}。戻り＝倒せた FP 数・残した TP 数・席ごとの p。"""
    out = {"fp": 0, "tp": 0, "fp_dropped": 0, "tp_kept": 0, "detail": []}
    for s in seats:
        p = cell_p(table, cell_of(s["row"]))
        out["fp" if s["fp"] else "tp"] += 1
        if s["fp"] and p < tau:
            out["fp_dropped"] += 1
        if (not s["fp"]) and p >= tau:
            out["tp_kept"] += 1
        out["detail"].append({"fp": s["fp"], "p": p, "cell": cell_of(s["row"])})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_defs(a) -> int:
    recs = load_dirs(a.dirs)
    games = human_games(recs, a.family)
    print(f"== (b) 「置ける」定義ごとの母集団（人間 mm 教材 {len(games)} 局・dist=0 に限定）==")
    print(f'{"定義":26s}{"合計行":>7s}{"陽性":>6s}{"率":>7s}{"行/局":>7s}'
          f'{"中央":>6s}{"Q1":>5s}{"Q3":>5s}{"最小":>5s}{"最大":>5s}{"0行の局":>8s}')
    for lab, sel in CAN_DEFS.items():
        ns, ps = [], []
        for g in games:
            sub = [r for r in g if is_judge1_row(r) and sel(r)]
            ns.append(len(sub))
            ps.append(sum(1 for r in sub if r["did_any"]))
        q = st.quantiles(ns, n=4) if len(ns) > 3 else [float("nan")] * 3
        tot = sum(ns)
        print(f'{lab:26s}{tot:>7d}{sum(ps):>6d}{(sum(ps) / tot if tot else float("nan")):>7.3f}'
              f'{st.mean(ns):>7.2f}{st.median(ns):>6.1f}{q[0]:>5.1f}{q[2]:>5.1f}'
              f'{min(ns):>5d}{max(ns):>5d}{sum(1 for x in ns if x == 0):>8d}')
    return 0


def cmd_needn(a) -> int:
    recs = load_dirs(a.dirs)
    games = human_games(recs, a.family)
    rng = random.Random(a.seed)
    print(f"== (a) 必要局数＝局クラスタ・ブートストラップ（母集団 {len(games)} 局・B={a.b}）==")
    print("   ★行は局内で相関する＝iid 前提の『100行で ±0.1』は使えない。")
    ks = tuple(int(x) for x in a.ks.split(","))
    print(f'{"定義":26s}{"点推定":>8s}{"設計効果":>9s}' + "".join(f"k={k}".rjust(9) for k in ks)
          + f'   ←95%CI 半値幅 ／ ±{a.target:.2f} に要る局数')
    for lab, sel in CAN_DEFS.items():
        pools = [[r for r in g if is_judge1_row(r) and sel(r)] for g in games]
        flat = [r for p in pools for r in p]
        pe = _rate(flat)
        line = f'{lab:26s}{(pe if pe is not None else float("nan")):>8.3f}' \
               f'{design_effect(pools, len(games), rng, a.b):>9.2f}'
        for k in ks:
            line += f'{half_width(cluster_ci(pools, k, rng, a.b)):>9.3f}'
        need = games_needed(pools, a.target, rng, max(500, a.b // 3))
        print(line + f'   {"" if need is None else str(need) + "局"}')
    return 0


def cmd_oracle(a) -> int:
    from arena.b269_audit import Rec as Rec269
    from arena.b276_audit import eval_predictions
    recs = load_dirs(a.dirs)
    corpus = [r for r in recs if r.meta.get("kind") == "corpus"]
    teach = [r for r in recs if r.meta.get("kind") == "teach"]
    ev = eval_predictions(corpus, teach)
    r269: list = []
    for d in a.b269.split(","):
        for fn in sorted(os.listdir(d.strip())):
            if fn.endswith(".json"):
                with open(os.path.join(d.strip(), fn), encoding="utf-8") as f:
                    r269.append(Rec269.from_json(json.load(f)))
    occ: dict = {}
    for rec in r269:
        k0 = (rec.meta.get("days"), rec.meta.get("script"), rec.meta.get("seed"))
        for i in rec.incidents:
            k = (k0, i["loop"], i["day"])
            occ[k] = occ.get(k, False) or i["occurs"]
    idx = {(r["days"], r["game"], r["loop"], r["day"], r["who"]): r for r in ev["corpus"]}
    seats: list[dict] = []
    for rec in r269:
        dys = rec.meta.get("days")
        for w in rec.cool:
            if not w.get("fire"):
                continue
            row = idx.get((dys, f'{w["script"]}#{w["seed"]}', w["loop"], w["day"], w["who"]))
            if row is None:
                continue
            fp = not occ.get(((dys, w["script"], w["seed"]), w["loop"], w["day"]), False)
            seats.append({"row": row, "fp": fp,
                          "name": f'{dys}日級 {w["script"]}#{w["seed"]} '
                                  f'L{w["loop"]}D{w["day"]} {w["who"]}'})
    tab_h = human_cell_table(recs)
    tab_a = ev["cells_full"]
    res_h = repaint_seats(seats, tab_h)
    res_a = repaint_seats(seats, tab_a)
    print("== ★判定2 に人間の教材は効くか（人間だけで較正したセル表で FP16 席を塗り直す）==")
    print(f'{"席":44s}{"FP/TP":>6s}{"セル":>28s}{"AI率":>8s}{"人間率":>14s}')
    for s, d_h, d_a in zip(seats, res_h["detail"], res_a["detail"]):
        did, n = tab_h.get(d_h["cell"], (0, 0))
        cell_txt = "%.2f(%d/%d)" % (d_h["p"], did, n)
        print("%-44s%6s%28s%8.2f%14s" % (s["name"], "FP" if s["fp"] else "TP",
                                         str(d_h["cell"]), d_a["p"], cell_txt))
    print(f'\n  分母 FP={res_h["fp"]} TP={res_h["tp"]}')
    print(f'  AI 較正セル表:   FP を倒せた {res_a["fp_dropped"]} ／ TP 残し {res_a["tp_kept"]}')
    print(f'  人間較正セル表: FP を倒せた {res_h["fp_dropped"]} ／ TP 残し {res_h["tp_kept"]}'
          f'  （必要＝FP≥{res_h["fp"] // 2 + 1}・TP≥{res_h["tp"] // 2 + 1}）')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B-277（計測のみ・挙動は変えない）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("defs", "needn"):
        p = sub.add_parser(nm)
        p.add_argument("--dirs", required=True, help="b276 の teach/corpus outdir（カンマ区切り）")
        p.add_argument("--family", default=None, help="教材名の接頭辞で層を絞る（例 鈴蘭）")
        if nm == "needn":
            p.add_argument("--seed", type=int, default=20260824)
            p.add_argument("--b", type=int, default=2000)
            p.add_argument("--ks", default="8,20,40,80,160,320")
            p.add_argument("--target", type=float, default=0.10)
    po = sub.add_parser("oracle")
    po.add_argument("--dirs", required=True)
    po.add_argument("--b269", required=True)
    a = ap.parse_args(argv)
    return {"defs": cmd_defs, "needn": cmd_needn, "oracle": cmd_oracle}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
