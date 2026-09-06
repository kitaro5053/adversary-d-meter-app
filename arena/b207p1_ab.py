# -*- coding: utf-8 -*-
"""B-207 Phase 1 の A/B・掃引ドライバ（#2 / #3 / #4 をセットで扱う）。

切替口（すべて既定 ON＝land 候補。`off` で **3つとも旧挙動へ bit 復帰**）：
  - #4 `HeuristicProtagonist.B207_KILLER_FLOOR_RESPONSIVE`
        ＋ `heuristic_protagonist.B207_KILLER_FLOOR_P`（θ）
        ＋ `PRIORITY["キラー候補反応封じ_薄疑い"]`（薄疑いの素点）
  - #2 `defense_plan.B201_LOVER_REACH`
  - #3 `HeuristicProtagonist.B207_DANGER_BONUS_TIEBREAK`

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires` ＝**行為の数え上げ**（規約 §11b・並び順に依存しない証拠）：
    ・#4 が薄疑いへ降格した席／そのうち実際に手が変わった席
    ・#2 の緩和で B-201 が発火した席／そのうち採られた席
    ・#3 で危険板ボーナスを外した席／そのうち手が変わった席

点の書式（`--values` にカンマ区切り）:
    off                     ＝3つとも OFF（基準点）
    k                       ＝#4 のみ ON（既定 θ・既定の薄疑い点）
    k70                     ＝#4 のみ ON・薄疑い点=70
    k70@0.15                ＝#4 のみ ON・薄疑い点=70・絶対θ=0.15
    k70%0.75                ＝#4 のみ ON・薄疑い点=70・相対比=0.75
    l                       ＝#2 のみ ON
    r                       ＝#3 のみ ON（板1枚のボーナス＝既定）
    r0.9                    ＝#3 のみ ON・板1枚のボーナス=0.9
    k+l+r                   ＝3つとも ON（land 候補）

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b207p1_ab \\
        --days 3 --values off,k,l,r,k+l+r --fires
"""

from __future__ import annotations

import argparse
import json

import agents.defense_plan as dp
import agents.heuristic_protagonist as hpm
from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark

_DEF_THETA = hpm.B207_KILLER_FLOOR_P
_DEF_RATIO = hpm.B207_KILLER_FLOOR_RATIO
_DEF_THIN = hpm.PRIORITY["キラー候補反応封じ_薄疑い"]
_DEF_SOLO = hpm._B207_DANGER_BONUS_SOLO


def _parse(v: str) -> dict:
    """点の書式（#4）＝`k<薄疑い点>@<絶対θ>%<相対比>`（どれも省略可）。"""
    s = (v or "").strip().lower()
    out = {"k": False, "l": False, "r": False,
           "theta": _DEF_THETA, "ratio": _DEF_RATIO, "thin": _DEF_THIN,
           "solo": _DEF_SOLO}
    if s in ("off", "none", ""):
        return out
    for part in s.split("+"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("k"):
            out["k"] = True
            body = part[1:]
            if "%" in body:
                body, ra = body.split("%", 1)
                out["ratio"] = float(ra)
            if "@" in body:
                body, th = body.split("@", 1)
                out["theta"] = float(th)
            if body:
                out["thin"] = float(body)
        elif part == "l":
            out["l"] = True
        elif part.startswith("r"):
            out["r"] = True
            if part[1:]:
                out["solo"] = float(part[1:])
        else:
            raise SystemExit(f"未知の点: {v}")
    return out


def _apply(val: dict) -> None:
    """★どの点でも全切替口を全部書く（前の点の残留で汚染しない）。"""
    HeuristicProtagonist.B207_KILLER_FLOOR_RESPONSIVE = bool(val["k"])
    HeuristicProtagonist.B207_DANGER_BONUS_TIEBREAK = bool(val["r"])
    dp.B201_LOVER_REACH = bool(val["l"])
    hpm.B207_KILLER_FLOOR_P = float(val["theta"])
    hpm.B207_KILLER_FLOOR_RATIO = float(val["ratio"])
    hpm.PRIORITY["キラー候補反応封じ_薄疑い"] = float(val["thin"])
    hpm._B207_DANGER_BONUS_SOLO = float(val["solo"])


def _switch_line(days: int) -> str:
    H = HeuristicProtagonist
    return (f"  [切替口] #4 B207_KILLER_FLOOR_RESPONSIVE={H.B207_KILLER_FLOOR_RESPONSIVE}"
            f"（絶対θ={hpm.B207_KILLER_FLOOR_P} 相対比="
            f"{hpm.B207_KILLER_FLOOR_RATIO} 薄疑い点="
            f"{hpm.PRIORITY['キラー候補反応封じ_薄疑い']}）"
            f" ／ #2 B201_LOVER_REACH={dp.B201_LOVER_REACH}"
            f" ／ #3 B207_DANGER_BONUS_TIEBREAK={H.B207_DANGER_BONUS_TIEBREAK}"
            f"（板1枚のボーナス={hpm._B207_DANGER_BONUS_SOLO}）"
            f" ／ B201_MAINLOVER_ANYAKU_PIN={dp.B201_MAINLOVER_ANYAKU_PIN}"
            f" ／ B204_ZERO_ALLOWANCE_GUARD={H.B204_ZERO_ALLOWANCE_GUARD}"
            f" ／ days={days}")


def _run(days: int, loops: int, val: dict, fires: bool = False) -> dict:
    old = (HeuristicProtagonist.B207_KILLER_FLOOR_RESPONSIVE,
           HeuristicProtagonist.B207_DANGER_BONUS_TIEBREAK,
           dp.B201_LOVER_REACH, hpm.B207_KILLER_FLOOR_P,
           hpm.B207_KILLER_FLOOR_RATIO,
           hpm.PRIORITY["キラー候補反応封じ_薄疑い"],
           hpm._B207_DANGER_BONUS_SOLO)
    _apply(val)
    print(_switch_line(days), flush=True)
    n = {"#4 薄疑いの席（候補に在った）": 0, "#4 うち薄疑いの封じを選んだ席": 0,
         "#2 B-201が発火した席": 0, "#2 その手を選んだ席": 0,
         "#3 板1枚×危険板の席": 0, "#3 うちその板を選んだ席": 0}
    orig_decide = HeuristicProtagonist.decide

    def wrap_decide(self, view, decision, options, _n=n):
        best = orig_decide(self, view, decision, options)
        if decision != "set_card":
            return best
        mm_chars = frozenset(p["target"] for p in view.get("placements", [])
                             if p.get("owner") == "mastermind"
                             and p.get("target_kind") == "character")
        mm_boards = frozenset(p["target"] for p in view.get("placements", [])
                              if p.get("owner") == "mastermind"
                              and p.get("target_kind") == "board")
        # --- #4：薄疑い降格の席
        # ★数え上げは**切替口に依らず**同じ述語で数える（off と on を比べるため）。
        if not getattr(self, "_kinshi_used", False):
            thin = []
            for o in options:
                if o.get("card") != "暗躍禁止" or o.get("target_kind") != "character":
                    continue
                tgt = o["target"]
                if tgt not in mm_chars or tgt not in getattr(self, "_killer_suspects", ()):
                    continue
                c = self._alive(view, tgt)
                if (c and c["anyaku"] == 0
                        and self._b207_thin_killer(
                            tgt, self._killer_prob.get(tgt, 0.0))):
                    thin.append(tgt)
            if thin:
                _n["#4 薄疑いの席（候補に在った）"] += 1
                if best.get("card") == "暗躍禁止" and best.get("target") in thin:
                    _n["#4 うち薄疑いの封じを選んだ席"] += 1
        # --- #2：B-201 の発火席（緩和ぶんを含む）
        if dp.B201_MAINLOVER_ANYAKU_PIN:
            try:
                roles = self._belief.role_marginals()
            except Exception:
                roles = {}
            pins = [o["target"] for o in options
                    if o.get("card") == "暗躍禁止" and o.get("target_kind") == "character"
                    and dp.mainlover_anyaku_pin_live(view, o["target"], mm_chars, roles)]
            if pins:
                _n["#2 B-201が発火した席"] += 1
                if best.get("card") == "暗躍禁止" and best.get("target") in pins:
                    _n["#2 その手を選んだ席"] += 1
        # --- #3：危険板ボーナスを外した席
        if len(mm_boards) == 1:
            db = self._guess_defeat_board(view)
            if db is not None and db in mm_boards:
                _n["#3 板1枚×危険板の席"] += 1
                if best.get("card") == "暗躍禁止" and best.get("target") == db:
                    _n["#3 うちその板を選んだ席"] += 1
        return best

    try:
        if fires:
            HeuristicProtagonist.decide = wrap_decide
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if fires:
            HeuristicProtagonist.decide = orig_decide
        (HeuristicProtagonist.B207_KILLER_FLOOR_RESPONSIVE,
         HeuristicProtagonist.B207_DANGER_BONUS_TIEBREAK,
         dp.B201_LOVER_REACH, hpm.B207_KILLER_FLOOR_P,
         hpm.B207_KILLER_FLOOR_RATIO,
         hpm.PRIORITY["キラー候補反応封じ_薄疑い"],
         hpm._B207_DANGER_BONUS_SOLO) = old
    if fires:
        rep["_fires"] = dict(n)
    return rep


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def _rows(rep: dict) -> dict:
    return {(r["script"], r["seed"]): r for r in rep.get("rows", [])}


def _flips(base: dict, new: dict) -> list[str]:
    a, b = _rows(base), _rows(new)
    out = []
    for k in sorted(a.keys() | b.keys()):
        ra, rb = a.get(k), b.get(k)
        if ra is None or rb is None:
            out.append(f"    {k[0]}#{k[1]}: 片側のみ")
            continue
        if (ra["loops_to_win"], ra["outcome"]) != (rb["loops_to_win"], rb["outcome"]):
            sign = "改善" if rb["loops_to_win"] < ra["loops_to_win"] else "退行"
            out.append(f"    [{sign}] {k[0]}#{k[1]}: {ra['loops_to_win']}"
                       f"[{ra['outcome']}] → {rb['loops_to_win']}[{rb['outcome']}]")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-207 Phase 1 A/B・掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,k+l+r")
    ap.add_argument("--fires", action="store_true")
    ap.add_argument("--out", default=None, help="JSON 出力先（点ごとの rows を保存）")
    ap.add_argument("--perm", default="id",
                    help="★摂動耐性（規約 §11b）＝`options` の**並び順だけ**を置換して"
                         "測る（採点は無改変）。`arena.tie_noise.permute` の mode 名。")
    a = ap.parse_args(argv)

    if a.perm and a.perm != "id":
        from arena.tie_noise import install_perm
        install_perm(a.perm)
        print(f"  [摂動] options 並び順の置換 mode={a.perm}", flush=True)
    base = None
    dump: dict = {}
    for v in [x for x in a.values.split(",") if x.strip()]:
        rep = _run(a.days, a.loops, _parse(v), fires=a.fires)
        dump[v] = rep.get("rows", [])
        print(f"[{v}] {_summary(rep)}", flush=True)
        if a.fires:
            print("  行為の数え上げ: "
                  + json.dumps(rep.get("_fires", {}), ensure_ascii=False), flush=True)
        if base is None:
            base = rep
            print("  （基準点）", flush=True)
        else:
            fl = _flips(base, rep)
            print(f"  flip={len(fl)}件", flush=True)
            for line in fl:
                print(line, flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
