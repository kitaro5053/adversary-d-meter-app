# -*- coding: utf-8 -*-
"""B-153（＋B-157 Phase 2「自殺」）の A/B・掃引ドライバ。

切替口＝`agents.defense_plan.B153_SUICIDE`（bool・既定 False）／
`B153_JUUSHA_MOVE_COST`（float）／`B153_COOL_COST`（float）。

- **切替口の実効値を毎回印字**する（`__pycache__` 再利用の事故対策・規約 §4）。
- 標準ベンチ（`arena.benchmark.run_benchmark`）をそのまま呼ぶ＝**二重実装しない**
  （perm は既存 `arena.tie_noise.install_perm` を再利用）。作りは `arena/b159_ab.py` と同型。
- per-game 差分（flip）を**全数**出す＝集計値は入れ替わりを隠す（規約 §5）。
- `--fires` ＝**行為の数え上げ**（§11b 成果の示し方 1）＝B-153 の脅威が
  「立った回数」「その折り手が `plan_defenses` に採られた回数」
  「**既存の折り手を押しのけた回数**（B-159 の L4 と同じ定義）」。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b153_ab --days 3 \\
        --values off,1.3:1.4,1.0:1.4,1.3:1.0
    ... --fires
"""

from __future__ import annotations

import argparse
import json

import agents.defense_plan as dp
from agents import HeuristicProtagonist
from arena.benchmark import run_benchmark

#: B-153 が立てる脅威の kind（`_threat_incident_suicide`）。
_KINDS = ("incident_suicide", "incident_suicide_juusha")
#: B-153 が生む Break のラベルに必ず入る印（押しのけ判定に使う）。
_MARKS = ("★従者を", "自殺の犯人候補")


#: ★掃引の各点で**3つの切替口すべて**を明示的に設定する（取り残しを作らない）。
#  値の書き方＝`off`（全 OFF＝ベースライン）／`pb`（B-153b **単独**）／
#  `move:cool`（自殺の脅威 ON。`--plan-move-kind` / `--pair-break` を併用で足す）。
_FLAGS = {"plan_kind": False, "pair": False}


def _parse(v: str):
    s = (v or "").strip().lower()
    if s in ("off", "none", ""):
        return None
    if s == "pb":
        return "pb"
    if ":" in s:
        a, b = s.split(":", 1)
        return (float(a), float(b))
    return (float(s), dp.B153_COOL_COST)


def _apply(val):
    """★どの点でも3つの切替口を全部書く（前の点の残留で汚染しない）。"""
    if val is None:
        dp.B153_SUICIDE = False
        dp.B153_JUUSHA_PAIR_BREAK = False
        HeuristicProtagonist.B153_PLAN_MOVE_KIND = False
    elif val == "pb":
        dp.B153_SUICIDE = False
        dp.B153_JUUSHA_PAIR_BREAK = True
        HeuristicProtagonist.B153_PLAN_MOVE_KIND = False
    else:
        dp.B153_SUICIDE = True
        dp.B153_JUUSHA_MOVE_COST, dp.B153_COOL_COST = val
        dp.B153_JUUSHA_PAIR_BREAK = _FLAGS["pair"]
        HeuristicProtagonist.B153_PLAN_MOVE_KIND = _FLAGS["plan_kind"]


def _switch_line(days: int, perm: str) -> str:
    return (f"  [切替口] B153_SUICIDE 実効={dp.B153_SUICIDE}"
            f" ／ B153_JUUSHA_MOVE_COST 実効={dp.B153_JUUSHA_MOVE_COST}"
            f" ／ B153_COOL_COST 実効={dp.B153_COOL_COST}"
            f" ／ ★B153_PLAN_MOVE_KIND 実効="
            f"{HeuristicProtagonist.B153_PLAN_MOVE_KIND}"
            f" ／ ★B153_JUUSHA_PAIR_BREAK 実効={dp.B153_JUUSHA_PAIR_BREAK}"
            f" ／ B159_MISSING_BOARD={dp.B159_MISSING_BOARD}"
            f" ／ B161_COOL_COST={dp.B161_COOL_COST}"
            f" ／ B100_MIX={HeuristicProtagonist.B100_MIX}"
            f" ／ B100_THETA={HeuristicProtagonist.B100_THETA}"
            f" ／ B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
            f" ／ days={days} perm={perm}")


def _rows(days: int, loops: int, val, perm: str = "id") -> dict:
    old = (dp.B153_SUICIDE, dp.B153_JUUSHA_MOVE_COST, dp.B153_COOL_COST,
           dp.B153_JUUSHA_PAIR_BREAK, HeuristicProtagonist.B153_PLAN_MOVE_KIND)
    _apply(val)
    print(_switch_line(days, perm), flush=True)
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        (dp.B153_SUICIDE, dp.B153_JUUSHA_MOVE_COST, dp.B153_COOL_COST,
         dp.B153_JUUSHA_PAIR_BREAK,
         HeuristicProtagonist.B153_PLAN_MOVE_KIND) = old
        uninstall_perm()
    return rep


def count_fires(days: int, loops: int, val, perm: str = "id") -> dict:
    """★行為の数え上げ（挙動は変えない＝ラッパは戻り値をそのまま返す）。

    - `gen`  ＝B-153 の脅威が **立った** 回数（席×事件日）
    - `gen_j`＝うち **身代わり腕**（`incident_suicide_juusha`）
    - `pick` ＝B-153 の折り手が `plan_defenses` に **採られた** 回数
    - `displace` ＝★**押しのけ**＝B-153 の折り手を採ったことで、
      **無ければ採られていた別の手**が `plan.picks` から消えた回数
      （同一席で ON/OFF の `plan_defenses` を二重に解いて比較する。
      ON 側の結果だけを本番へ返す＝OFF 側は捨てる＝挙動不変）。
    """
    from arena.tie_noise import install_perm, uninstall_perm

    old = (dp.B153_SUICIDE, dp.B153_JUUSHA_MOVE_COST, dp.B153_COOL_COST)
    _apply(val)
    n = {"gen": 0, "gen_j": 0, "pick": 0, "displace": 0, "seats": 0}

    orig_th = dp._threat_incident_suicide
    orig_plan = dp.plan_defenses

    def th(*a, **kw):
        r = orig_th(*a, **kw)
        n["gen"] += len(r)
        n["gen_j"] += sum(1 for t in r if t.kind == "incident_suicide_juusha")
        return r

    def plan(threats, **kw):
        p = orig_plan(threats, **kw)
        n["seats"] += 1
        mine = [b for b in (p.picks or [])
                if any(m in (b.label or "") for m in _MARKS)]
        if mine:
            n["pick"] += len(mine)
            import copy
            t2 = [t for t in copy.deepcopy(list(threats)) if t.kind not in _KINDS]
            p0 = orig_plan(t2, **kw)
            k1 = {(b.card, b.target, b.target_kind) for b in (p.picks or [])}
            k0 = {(b.card, b.target, b.target_kind) for b in (p0.picks or [])}
            n["displace"] += len(k0 - k1)
        return p

    dp._threat_incident_suicide = th
    dp.plan_defenses = plan
    install_perm(perm)
    try:
        print(_switch_line(days, perm), flush=True)
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        dp._threat_incident_suicide = orig_th
        dp.plan_defenses = orig_plan
        (dp.B153_SUICIDE, dp.B153_JUUSHA_MOVE_COST, dp.B153_COOL_COST) = old
        uninstall_perm()
    return {**n, "rep": rep}


def count_pb_fires(days: int, loops: int, perm: str = "id") -> dict:
    """★B-153b の**行為の数え上げ**（§11b 成果の示し方 1＝並び順に依存しない数）。

    - `checked` ＝`juusha_drags` が呼ばれた回数（＝2人きり脅威に従者が絡んだ判定）
    - `dropped` ＝★**追随で引き離せない折り手を実際に消した回数**（True を返した回数）
    ★ラッパは戻り値をそのまま返す＝挙動不変。
    """
    from arena.tie_noise import install_perm, uninstall_perm

    old = (dp.B153_JUUSHA_PAIR_BREAK, dp.B153_SUICIDE)
    dp.B153_JUUSHA_PAIR_BREAK = True
    dp.B153_SUICIDE = False
    n = {"checked": 0, "dropped": 0}
    orig = dp.juusha_drags

    def wrap(view, name):
        r = orig(view, name)
        n["checked"] += 1
        n["dropped"] += int(bool(r))
        return r

    dp.juusha_drags = wrap
    install_perm(perm)
    try:
        print(_switch_line(days, perm), flush=True)
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        dp.juusha_drags = orig
        (dp.B153_JUUSHA_PAIR_BREAK, dp.B153_SUICIDE) = old
        uninstall_perm()
    return {**n, "rep": rep}


def _key(r: dict) -> tuple:
    return (r["script"], r["seed"])


def _summary(rep: dict) -> str:
    """★規約 §5＝`fb_win` は防衛に数えない（`outcomes["defense"]` が防衛数）。"""
    oc = rep.get("outcomes", {})
    dist = rep.get("distribution", {})
    return (f"防衛={oc.get('defense', 0)}／{rep['n_games']}"
            f"  平均={rep['mean_loops_to_win']}"
            f"  L1={dist.get('1', 0)}"
            f"  loss={oc.get('loss', 0) + oc.get('fb_loss', 0)}"
            f"（fb_win={oc.get('fb_win', 0)}）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-153 A/B・掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--values", default="off,1.3:1.4")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--fires", action="store_true")
    ap.add_argument("--pb-fires", action="store_true",
                    help="★B-153b の行為の数え上げ（消した折り手の回数）")
    ap.add_argument("--plan-move-kind", action="store_true",
                    help="★加点の門を開く（HeuristicProtagonist.B153_PLAN_MOVE_KIND）")
    ap.add_argument("--pair-break", action="store_true",
                    help="★B-153b＝追随で引き離せない折り手を消す"
                         "（agents.defense_plan.B153_JUUSHA_PAIR_BREAK）")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    _FLAGS["plan_kind"] = bool(a.plan_move_kind)
    _FLAGS["pair"] = bool(a.pair_break)
    vals = [_parse(v) for v in a.values.split(",")]
    if a.pb_fires:
        r = count_pb_fires(a.days, a.loops, perm=a.perm)
        print(f"  B-153b: juusha_drags 判定={r['checked']} "
              f"★追随で引き離せない折り手を消した={r['dropped']} "
              f"／{_summary(r['rep'])}", flush=True)
        return 0
    if a.fires:
        for v in vals:
            r = count_fires(a.days, a.loops, v, perm=a.perm)
            print(f"  値={v}: 脅威={r['gen']}（身代わり腕={r['gen_j']}） "
                  f"採用={r['pick']} ★押しのけ={r['displace']} "
                  f"プラン解いた席={r['seats']} ／{_summary(r['rep'])}", flush=True)
        return 0

    base = None
    out = []
    for v in vals:
        rep = _rows(a.days, a.loops, v, perm=a.perm)
        rows = {_key(r): r["loops_to_win"] for r in rep["rows"]}
        line = f"  値={v}: {_summary(rep)}"
        if base is None:
            base = rows
        else:
            flips = [(k, base[k], rows[k]) for k in rows if base[k] != rows[k]]
            imp = [f for f in flips if f[2] < f[1]]
            reg = [f for f in flips if f[2] > f[1]]
            line += f"  flip={len(flips)}（改善{len(imp)}／退行{len(reg)}）"
            for k, b, c in sorted(flips):
                line += f"\n      {k[0]} s{k[1]}: {b} → {c}"
        print(line, flush=True)
        out.append({"value": v, "summary": rep})
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
