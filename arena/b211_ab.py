# -*- coding: utf-8 -*-
"""B-211 の A/B ドライバ（帰属器のフレンド死亡線）。

切替口＝`agents.heuristic_protagonist.HeuristicProtagonist.B211_FRIEND_DEATH_ATTR`
（bool・既定 False＝OFF）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝二重実装しない。
- per-game 差分（flip）を**全数**出す（規約 §5）。
- `--fires`＝**行為の数え上げ**（規約 §11b・並び順に依存しない一次量）＝
  `past_loss_keys` が返した `instant` 型のフレンド鍵の数と、
  実際に `repeat_count >= 1` を返した脅威のうちフレンド鍵で立ったものの数。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b211_ab --days 3 --values off,on
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b211_ab --days 5 --values off,on
"""

from __future__ import annotations

import argparse
import json

from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark


def _parse(v: str) -> bool:
    s = (v or "").strip().lower()
    if s in ("off", "0", "false", "none", ""):
        return False
    if s in ("on", "1", "true"):
        return True
    raise SystemExit(f"未知の点: {v}")


def _switch_line(days: int) -> str:
    H = HeuristicProtagonist
    from agents import b100_mix as _bm
    return (f"  [切替口] B211_FRIEND_DEATH_ATTR 実効={H.B211_FRIEND_DEATH_ATTR}"
            f" ／ module default={_bm.B211_FRIEND_DEATH_ATTR_DEFAULT}"
            f" ／ B100_MIX={H.B100_MIX} ／ B100_JOINT={H.B100_JOINT}"
            f" ／ B100_IRON_PROB={H.B100_IRON_PROB} ／ B100_THETA={H.B100_THETA}"
            f" ／ days={days}")


def _run(days: int, loops: int, on: bool, fires: bool = False) -> dict:
    H = HeuristicProtagonist
    old = H.B211_FRIEND_DEATH_ATTR
    H.B211_FRIEND_DEATH_ATTR = bool(on)
    print(_switch_line(days), flush=True)
    n = {"フレンド鍵が立ったターン": 0, "フレンド鍵の総数(ターン×名前)": 0,
         "反復判定が立った脅威(repeat>=1)": 0, "  うちフレンド鍵だけで立った脅威": 0}
    from agents import b100_alloc as _al
    from agents import b100_mix as _bm
    orig_keys = _al.past_loss_keys
    orig_rep = _al.repeat_count

    def wrap_keys(history, loop, *, friend_attr=None, _n=n):
        got = orig_keys(history, loop, friend_attr=friend_attr)
        fr = set()
        for lp, s in _bm.loss_signatures(history).items():
            if lp is None or lp >= (loop or 0):
                continue
            fr |= set(s.get("friend_deaths", ()))
        if fr:
            _n["フレンド鍵が立ったターン"] += 1
            _n["フレンド鍵の総数(ターン×名前)"] += len(fr)
        wrap_keys.last_friend = fr           # 直後の repeat_count 用
        return got

    def wrap_rep(threat, past_keys, cast, inc_names, _n=n):
        r = orig_rep(threat, past_keys, cast, inc_names)
        if r >= 1:
            _n["反復判定が立った脅威(repeat>=1)"] += 1
            fr = getattr(wrap_keys, "last_friend", set())
            if fr:
                # フレンド鍵を抜いたら 0 になるか＝この判定はフレンド線だけで立った
                ty = _bm.KIND_LOSS_TYPE.get(threat.kind)
                ctr = past_keys.get(ty)
                if ty == "instant" and ctr:
                    stripped = {k: v for k, v in ctr.items() if k not in fr}
                    keys = _bm.threat_keys(threat.label, cast, inc_names)
                    cand = [stripped.get(x, 0)
                            for x in (keys["chars"] | keys["incidents"])]
                    if max(cand + [0]) == 0:
                        _n["  うちフレンド鍵だけで立った脅威"] += 1
        return r

    try:
        if fires:
            _al.past_loss_keys = wrap_keys
            _al.repeat_count = wrap_rep
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        if fires:
            _al.past_loss_keys = orig_keys
            _al.repeat_count = orig_rep
        H.B211_FRIEND_DEATH_ATTR = old
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
    ap = argparse.ArgumentParser(description="B-211 A/B（フレンド死亡線の帰属）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,on")
    ap.add_argument("--fires", action="store_true")
    a = ap.parse_args(argv)

    base = None
    for v in [x for x in a.values.split(",") if x.strip()]:
        rep = _run(a.days, a.loops, _parse(v), fires=a.fires)
        print(f"[{v}] {_summary(rep)}")
        if a.fires:
            print("  行為の数え上げ: "
                  + json.dumps(rep.get("_fires", {}), ensure_ascii=False))
        if base is None:
            base = rep
            print("  （基準点）")
        else:
            fl = _flips(base, rep)
            print(f"  flip={len(fl)}件")
            for line in fl:
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
