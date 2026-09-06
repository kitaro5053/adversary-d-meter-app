# -*- coding: utf-8 -*-
"""B-206 の A/B・掃引ドライバ（(a) 自傷ガード ／ (b) SK 着地点の待ち伏せ）。

切替口＝`agents.heuristic_protagonist.HeuristicProtagonist`
  - `B206_SELF_HARM_MOVE`（bool・既定 False＝OFF）／`_B206_SK_P`／`_B206_SCORE`
  - `B206_TELEPORT_BAIT`（bool・既定 False＝OFF）／`_B206_BAIT_SCORE`

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires`＝**行為の数え上げ**（規約 §11b）＝述語が発火した席数（＝採点で上限を
  被せた回数）と、そのうち**実際に手が変わった席**の数。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b206_ab --days 3 \\
        --values off,tp90,tp102,tp125 --fires
    ... --values off,sh,sh+tp102             # sh＝自傷ガード・tpN＝待ち伏せ(点N)
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark

_DEF_SCORE = -100.0


def _parse(v: str):
    """点の指定。

    "off"           ＝両方 OFF（基準点）
    "sh" / "sh0.9"  ＝(a) 自傷ガードのみ ON（数値＝`_B206_SK_P`）
    "tp90"          ＝(b) 着地点の待ち伏せのみ ON（数値＝`_B206_BAIT_SCORE`）
    "sh+tp90"       ＝両方 ON
    """
    s = (v or "").strip().lower()
    if s in ("off", "none", ""):
        return {"sh": False, "tp": False}
    out = {"sh": False, "tp": False, "sk_p": 0.5, "bait": 80.0}
    for part in s.split("+"):
        part = part.strip()
        if part.startswith("sh"):
            out["sh"] = True
            if part[2:]:
                out["sk_p"] = float(part[2:])
        elif part.startswith("tp"):
            out["tp"] = True
            if part[2:]:
                out["bait"] = float(part[2:])
        elif part:
            raise SystemExit(f"未知の点: {v}")
    return out


def _apply(val) -> None:
    """★どの点でも全切替口を全部書く（前の点の残留で汚染しない）。"""
    H = HeuristicProtagonist
    H.B206_SELF_HARM_MOVE = bool(val.get("sh"))
    H.B206_TELEPORT_BAIT = bool(val.get("tp"))
    H._B206_SK_P = float(val.get("sk_p", 0.5))
    H._B206_SCORE = _DEF_SCORE
    H._B206_BAIT_SCORE = float(val.get("bait", 80.0))


def _switch_line(days: int) -> str:
    H = HeuristicProtagonist
    return (f"  [切替口] B206_SELF_HARM_MOVE 実効={H.B206_SELF_HARM_MOVE}"
            f" ／ B206_TELEPORT_BAIT 実効={H.B206_TELEPORT_BAIT}"
            f" ／ _B206_BAIT_SCORE={H._B206_BAIT_SCORE}"
            f" ／ _B206_SK_P={H._B206_SK_P} ／ _B206_SCORE={H._B206_SCORE}"
            f" ／ B100_SELF_HARM={H.B100_SELF_HARM}"
            f" ／ B205_KINSHI_GUARD={H.B205_KINSHI_GUARD}"
            f" ／ days={days}")


def _run(days: int, loops: int, val, fires: bool = False) -> dict:
    old = (HeuristicProtagonist.B206_SELF_HARM_MOVE,
           HeuristicProtagonist.B206_TELEPORT_BAIT,
           HeuristicProtagonist._B206_SK_P,
           HeuristicProtagonist._B206_SCORE,
           HeuristicProtagonist._B206_BAIT_SCORE)
    _apply(val)
    print(_switch_line(days), flush=True)
    n = {"自傷述語が発火した席": 0, "抑止できず自傷手を選んだ席": 0,
         "抑止した手の総数": 0, "待ち伏せが発火した席": 0, "待ち伏せを採った席": 0}
    orig = HeuristicProtagonist._b206_self_harm
    orig_bait = HeuristicProtagonist._b206_bait
    orig_decide = HeuristicProtagonist.decide

    def wrap_decide(self, view, decision, options, _n=n):
        best = orig_decide(self, view, decision, options)
        if decision == "set_card" and self.B206_TELEPORT_BAIT:
            bh = [o for o in options if orig_bait(self, o, view) is not None]
            if bh:
                _n["待ち伏せが発火した席"] += 1
                if any((best["card"], best["target"]) == (o["card"], o["target"])
                       for o in bh):
                    _n["待ち伏せを採った席"] += 1
        if decision == "set_card" and self.B206_SELF_HARM_MOVE:
            hit = [o for o in options if orig(self, o, view)]
            if hit:
                _n["自傷述語が発火した席"] += 1
                _n["抑止した手の総数"] += len(hit)
                if any((best["card"], best["target"]) == (o["card"], o["target"])
                       for o in hit):
                    _n["抑止できず自傷手を選んだ席"] += 1
        return best

    try:
        if fires:
            HeuristicProtagonist.decide = wrap_decide
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if fires:
            HeuristicProtagonist.decide = orig_decide
        (HeuristicProtagonist.B206_SELF_HARM_MOVE,
         HeuristicProtagonist.B206_TELEPORT_BAIT,
         HeuristicProtagonist._B206_SK_P,
         HeuristicProtagonist._B206_SCORE,
         HeuristicProtagonist._B206_BAIT_SCORE) = old
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
            out.append(f"    {k[0]}#{k[1]}: {ra['loops_to_win']}"
                       f"[{ra['outcome']}] → {rb['loops_to_win']}[{rb['outcome']}]")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-206 A/B・掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,tp90")
    ap.add_argument("--fires", action="store_true")
    a = ap.parse_args(argv)

    base = None
    for v in [x for x in a.values.split(",") if x.strip()]:
        val = _parse(v)
        rep = _run(a.days, a.loops, val, fires=a.fires)
        print(f"[{v}] {_summary(rep)}")
        if a.fires:
            print("  行為の数え上げ: "
                  + json.dumps(rep.get("_fires", {}), ensure_ascii=False))
        if base is None:
            base = rep
            print("  （基準点）")
        else:
            fl = _flips(base, rep)
            print(f"  flip={len(fl)}件" + ("" if not fl else ""))
            for line in fl:
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
