# -*- coding: utf-8 -*-
"""B-265 プローブ（★フェーズ0＝計測のみ。`agents/` `sim/` `engine/` `rules/` は触らない）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-73（B-265）。
前提＝§72-57（B-255 の検死）＋§72-59（B-257 の対照実験）。

## ★事前登録（§72-2・規約§7 判例1＝通常ゲート）＝**実装より前のコミットで的を固定する**

本レーンが**実装の前に**確かめると宣言する量。あとから的を動かさない。

| # | 的 | 判定の形 |
|---|---|---|
| P0-1 | **仮説A**＝「居合わせ証拠が、移動不能なキャラにも他と同じ重みで積算されている」 | 居合わせチャネル（`_public_role_constraints` の `board_sets`/`kuro`・`_phase_kuromaku_unions`）を1本ずつ抜いて P(クロマク=·) が動くか。**動かない／両者を同率で動かすなら仮説Aは棄却** |
| P0-2 | **仮説B**＝「友好による除外チャネルだけが当たらない＝積む側だけ働く」 | ご神木に当たりうる除外チャネルを列挙し、**それが当たったら P(クロマク=ご神木) が上がるのか下がるのか**を符号ごと確かめる |
| P0-3 | **仮説C**＝「発火床に届く候補が『手の出せない相手』だけ＝対策が NOOP に落ちる」 | 床（0.3／0.7）を超えた候補の集合と、その候補に対して規則上その語彙の手が存在するかを席ごとに数える |
| P0-4 | belief は「移動不能」を知れるか | `agents/belief.py` の import と参照を全数走査（`engine.data.CHARACTER_FORBIDDEN` を見ているか） |
| P0-5 | 数え上げ (a)(b)(c) | (a) 移動不能な常駐者が居る局数／(b) その局で argmax が常駐者に張り付くループ%／(c) 床に届く候補が「手の出せない相手」だけになった席数 |
| P0-6 | ★**§72-53 の検算**＝belief を正しくしたら強くなるのか | **オラクル反実仮想**（真の配役を belief にソフト証拠で教え込む＝出荷不可の診断用）で `loops_to_win` が動くか。**動かないなら B-265 は実装しても強さに繋がらない＝負の結果** |

★**言わないこと**（規約§7 判例2）＝「P(クロマク=ご神木)=0.44 は高すぎる」とは言わない。
言えるのは **argmax の一致/不一致・発火の可否・回数・席数**まで。

## ★測定の落とし穴（本レーンが最初に踏んだもの）

`agents/belief.py:1989` の `_RECOMPUTE_CACHE` の署名（`_recompute_sig`・`:1993`）は
**モジュール切替口（B61/B101/B128/B188/B243/B231/B234）だけ**を含み、
**関数の差し替え（monkeypatch）は含まない**。∴ チャネルを差し替えるアブレーションは
**条件ごとに `_RECOMPUTE_CACHE.clear()` しないと前条件の結果を引く**（本レーンの初回測定はこれで
「居合わせチャネルは効果ゼロ」という**誤った結論**を出した。`tests/test_b265_probe.py` で回帰固定）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b265_probe defaults
    python -m arena.b265_probe census                     # (a) 構造センサス
    python -m arena.b265_probe ablate --script btx_seal_cat --seeds 0,1,2
    python -m arena.b265_probe stick  --script btx_seal_cat --seeds 0-9   # (b)
    python -m arena.b265_probe oracle --script btx_seal_cat --seeds 0-9   # P0-6
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
from collections import Counter
from dataclasses import replace

import agents.belief as bl
from agents.belief import Belief

DRIVER = "arena.b265_probe"
KURO = "クロマク"

#: `_kuromaku_cands` / `_kuromaku_suspects` の床（`agents/heuristic_protagonist.py:1813,1815`）
KM_CAND_FLOOR = 0.3
KM_SURE_FLOOR = 0.7


# ---------------------------------------------------------------------------
# 健全性検査（切替口が1bitも動いていないこと＝チケット要求）
# ---------------------------------------------------------------------------
def knob_audit_baseline() -> str:
    """`arena.knob_audit.check_baseline`（B-248 の一般形）を配線する。"""
    from arena import knob_audit as ka
    from arena.b249_audit import BASELINE, KNOBS, _holders
    ka.check_baseline(KNOBS, _holders(), BASELINE, driver=DRIVER)
    return "✅ `arena.knob_audit.check_baseline`（B-248 の一般形）PASS"


def knob_snapshot() -> dict:
    """`arena.b249_audit.defaults_banner` と同じ束（測定前後の比較用）。"""
    from arena.b249_audit import defaults_banner
    return defaults_banner()


def check_no_knob_writes(before: dict) -> None:
    from arena.b249_audit import check_no_knob_writes as _c
    _c(before, driver=DRIVER)


# ---------------------------------------------------------------------------
# P0-4：belief は「移動不能」を知れるか（KB由来の派生・単一ソースは engine.data）
# ---------------------------------------------------------------------------
def immobile_chars() -> set:
    """★KB が一意に決める量＝「初期エリア以外の3ボードが全部禁止エリア」＝**移動不能**。

    出典＝`rules/30_characters.md`（ご神木＝神社から動けない・禁止＝病院/都市/学校 ほか）／
    単一ソース＝`engine.data.CHARACTER_FORBIDDEN` と `engine.data.CHARACTER_INITIAL_AREA`。
    ★これは確率値ではなく**可否**なので、規約§7 判例2 に触れずに使える量である。
    """
    from engine.board import AREAS
    from engine.data import CHARACTER_FORBIDDEN, initial_area_of
    out = set()
    for c, forb in CHARACTER_FORBIDDEN.items():
        ini = initial_area_of(c)
        if ini is not None and set(AREAS) - set(forb) == {ini}:
            out.add(c)
    return out


def belief_knows_immobility() -> dict:
    """belief が移動不能を知る材料を持っているかを**実物のソースから**判定する。"""
    import inspect
    src = inspect.getsource(bl)
    return {
        "imports_CHARACTER_FORBIDDEN": "CHARACTER_FORBIDDEN" in src,
        "imports_initial_area_of": "initial_area_of" in src,
        "mentions_forbidden": "forbidden" in src,
        # belief が観測に使う唯一の材料
        "inputs": ["cast", "incidents_public", "set_name", "history"],
    }


# ---------------------------------------------------------------------------
# 対局（ベンチと同一手順）
# ---------------------------------------------------------------------------
def _script(name: str):
    from sim.sample_scripts import SAMPLE_SCRIPTS
    return SAMPLE_SCRIPTS[name]()


def play(sc, seed: int, loops: int = 8):
    """`arena.benchmark.loops_to_win` と同一手順で1局回す（履歴と結末を返す）。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    probe = replace(sc, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        res = (state.loop_no, "defense")
    elif fb:
        res = (loops + 1, "fb_win" if state.winner == "protagonist" else "fb_loss")
    else:
        res = (loops + 1, "loss")
    return state.history, res


def loop_end_indices(history) -> list[int]:
    return [i + 1 for i, e in enumerate(history) if e.get("event") == "loop_result"]


def _incidents(sc):
    return [{"day": i.day, "name": i.name} for i in sc.incidents]


def marginals_at(sc, history, k) -> dict:
    b = Belief(sc.cast, _incidents(sc), set_name=sc.set_name)
    b.observe(history[:k])
    return b.role_marginals()


# ---------------------------------------------------------------------------
# P0-1／P0-2：チャネル・アブレーション
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _patch(attr, fn):
    old = getattr(bl, attr)
    setattr(bl, attr, fn)
    try:
        yield
    finally:
        setattr(bl, attr, old)


@contextlib.contextmanager
def _knob(attr, val):
    old = getattr(bl, attr)
    setattr(bl, attr, val)
    try:
        yield
    finally:
        setattr(bl, attr, old)


def _conditions():
    """アブレーション条件表。値＝context manager を作る callable。"""
    orig_prc = bl._public_role_constraints
    orig_pku = bl._phase_kuromaku_unions

    def _nest(*cms):
        @contextlib.contextmanager
        def _c():
            with contextlib.ExitStack() as st:
                for cm in cms:
                    st.enter_context(cm)
                yield
        return _c()

    return {
        "baseline": lambda: contextlib.nullcontext(),
        # 居合わせ（present）チャネル＝仮説A の対象
        "no_board_sets": lambda: _patch(
            "_public_role_constraints",
            lambda h, cast=(): (orig_prc(h, cast)[0], orig_prc(h, cast)[1], [])),
        "no_kuro_inter": lambda: _patch(
            "_public_role_constraints",
            lambda h, cast=(): (None, orig_prc(h, cast)[1], orig_prc(h, cast)[2])),
        "no_kuro_union": lambda: _patch("_phase_kuromaku_unions",
                                        lambda h, cast=(): []),
        "no_present_all": lambda: _nest(
            _patch("_phase_kuromaku_unions", lambda h, cast=(): []),
            _patch("_public_role_constraints",
                   lambda h, cast=(): (None, orig_prc(h, cast)[1], []))),
        # 特性由来の演繹（対照）
        "no_B234": lambda: _knob("B234_GOSHINBOKU_TRAIT", False),
        "no_B231": lambda: _knob("B231_IMOUTO_TRAIT", False),
    }


def ablate(sc, seeds, resident: str, truth_kuro: str | None = None, loops: int = 8):
    """各ループ末の P(クロマク=常駐者) と P(クロマク=真値) をチャネル別に出す。"""
    rows = []
    truth = {n: sc.role_of(n) for n in sc.cast}
    truth_kuro = truth_kuro or next((n for n, r in truth.items() if r == KURO), None)
    for seed in seeds:
        history, res = play(sc, seed, loops=loops)
        idxs = loop_end_indices(history)
        for cond, mk in _conditions().items():
            # ★必須：署名に patch は含まれない（モジュール docstring 参照）
            bl._RECOMPUTE_CACHE.clear()
            with mk():
                ps = []
                for k in idxs:
                    m = marginals_at(sc, history, k)
                    ps.append((m.get(resident, {}).get(KURO, 0.0),
                               m.get(truth_kuro, {}).get(KURO, 0.0),
                               max(sc.cast, key=lambda c: m.get(c, {}).get(KURO, 0.0))))
            bl._RECOMPUTE_CACHE.clear()
            rows.append({"seed": seed, "cond": cond, "outcome": res[1],
                         "n_loops": len(idxs),
                         "p_resident_last": ps[-1][0] if ps else None,
                         "p_truth_last": ps[-1][1] if ps else None,
                         "n_argmax_resident": sum(1 for p in ps if p[2] == resident),
                         "argmax_last": ps[-1][2] if ps else None})
    return rows


# ---------------------------------------------------------------------------
# P0-5 (a)：構造センサス
# ---------------------------------------------------------------------------
def census(days: int) -> dict:
    from arena.benchmark import benchmark_scripts
    imm = immobile_chars()
    rows = benchmark_scripts(days=days)
    hit = [(g, s, tuple(sorted(set(sc.cast) & imm))) for g, s, sc in rows
           if set(sc.cast) & imm]
    return {"days": days, "n_games": len(rows), "n_hit": len(hit),
            "by_script": dict(Counter(g for g, _, _ in hit)),
            "by_chars": {"/".join(c): n for c, n in Counter(x[2] for x in hit).items()}}


# ---------------------------------------------------------------------------
# P0-5 (b)：argmax の張り付き
# ---------------------------------------------------------------------------
def stickiness(sc, seeds, resident: str, loops: int = 8) -> dict:
    n_loop = n_stick = 0
    truth = {n: sc.role_of(n) for n in sc.cast}
    tk = next((n for n, r in truth.items() if r == KURO), None)
    per = []
    for seed in seeds:
        history, res = play(sc, seed, loops=loops)
        idxs = loop_end_indices(history)
        s = 0
        for k in idxs:
            m = marginals_at(sc, history, k)
            arg = max(sc.cast, key=lambda c: m.get(c, {}).get(KURO, 0.0))
            s += int(arg == resident)
        n_loop += len(idxs)
        n_stick += s
        per.append({"seed": seed, "outcome": res[1], "loops": len(idxs), "stick": s})
    return {"resident": resident, "truth_kuromaku": tk,
            "n_loops": n_loop, "n_stick": n_stick, "per": per}


# ---------------------------------------------------------------------------
# P0-5 (c)：床に届く候補が「手の出せない相手」だけになった席（＝NOOP に落ちた席）
# ---------------------------------------------------------------------------
def noop_seats(days: int, loops: int = 8) -> dict:
    """★読み取り専用のラッパ（元の実装を必ず呼ぶ）で `_kuromaku_cands` を席ごとに数える。

    数える席＝`_kuromaku_cands` が**非空**で、かつ**全員が移動不能**（＝
    `クロマク隔離`／`クロマク剥がし`／`クロマク剥がし_候補` は 3つとも
    「対象を移動カードで敗北ボードの外へ出す」ことを発火条件に持つので、
    移動不能キャラを対象に取る手は**規則上そもそも存在しない**）。
    """
    from arena.benchmark import benchmark_scripts
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    imm_all = immobile_chars()
    orig = HP._b265_sync
    stat = {"seats": 0, "cands_nonempty": 0, "noop": 0, "games": 0,
            "games_with_noop": 0}
    cur = {"noop": 0}

    def _wrapped(self, marg, view=None):    # ★T5：本体は view を渡す（省略可）
        orig(self, marg, view)                 # ★元の実装を必ず呼ぶ
        stat["seats"] += 1
        cands = set(getattr(self, "_kuromaku_cands", ()) or ())
        if not cands:
            return
        stat["cands_nonempty"] += 1
        if cands <= imm_all:
            stat["noop"] += 1
            cur["noop"] += 1

    HP._b265_sync = _wrapped
    try:
        for gname, seed, sc in benchmark_scripts(days=days):
            if not (set(sc.cast) & imm_all):
                continue
            cur["noop"] = 0
            play(sc, seed, loops=loops)
            stat["games"] += 1
            stat["games_with_noop"] += int(cur["noop"] > 0)
    finally:
        HP._b265_sync = orig
    stat["days"] = days
    return stat


# ---------------------------------------------------------------------------
# P0-6：オラクル反実仮想（★出荷不可＝診断専用）
# ---------------------------------------------------------------------------
class _OracleNotKuromaku:
    """「この常駐者はクロマクではない」を belief にソフト証拠として教える（診断専用）。

    ★これは**神視点の情報**なので絶対に出荷しない。§72-53 の検算
    （＝指標が直れば強くなるのか）を**実装の前に**答えるためだけの道具。
    """

    def __init__(self, name: str, lam: float = 1.0):
        self.name = name
        self.lam = lam

    def assign_logweight(self, char, role, combo, ctx) -> float:
        return -60.0 if (char == self.name and role == KURO) else 0.0


def oracle(sc, seeds, resident: str, loops: int = 8) -> list[dict]:
    """常駐者を クロマク 候補から外した belief で対局し `loops_to_win` を測る。"""
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    out = []
    orig = HP.SOFT_EVIDENCE
    for seed in seeds:
        _, base = play(sc, seed, loops=loops)
        HP.SOFT_EVIDENCE = list(orig) + [lambda r=resident: _OracleNotKuromaku(r)]
        try:
            _, orc = play(sc, seed, loops=loops)
        finally:
            HP.SOFT_EVIDENCE = orig
        out.append({"seed": seed, "base": base, "oracle": orc,
                    "flip": base != orc})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _seeds(s: str) -> list[int]:
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _resident_of(sc) -> str:
    imm = immobile_chars() & set(sc.cast)
    if not imm:
        raise SystemExit(f"移動不能な常駐者が居ない脚本: {sc.cast}")
    return sorted(imm)[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=DRIVER)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("defaults")
    p = sub.add_parser("census"); p.add_argument("--days", type=int, default=0)
    p = sub.add_parser("noop"); p.add_argument("--days", type=int, default=0)
    for name in ("ablate", "stick", "oracle"):
        p = sub.add_parser(name)
        p.add_argument("--script", default="btx_seal_cat")
        p.add_argument("--seeds", default="0-9")
        p.add_argument("--resident", default="")
    a = ap.parse_args(argv)

    print(knob_audit_baseline(), flush=True)
    before = knob_snapshot()

    if a.cmd == "defaults":
        print("移動不能キャラ（KB由来）=", sorted(immobile_chars()))
        print("belief が持つ材料 =", belief_knows_immobility())
    elif a.cmd == "census":
        for d in ([a.days] if a.days else [3, 5]):
            print(census(d), flush=True)
    elif a.cmd == "noop":
        for d in ([a.days] if a.days else [3, 5]):
            print(noop_seats(d), flush=True)
    else:
        sc = _script(a.script)
        resident = a.resident or _resident_of(sc)
        seeds = _seeds(a.seeds)
        print(f"脚本={a.script} 常駐者={resident} seeds={seeds}", flush=True)
        if a.cmd == "ablate":
            for r in ablate(sc, seeds, resident):
                print(f"  s{r['seed']} {r['cond']:16s} "
                      f"P(クロマク={resident})={r['p_resident_last']:.3f} "
                      f"P(クロマク=真値)={r['p_truth_last']:.3f} "
                      f"argmax末={r['argmax_last']} "
                      f"常駐argmax={r['n_argmax_resident']}/{r['n_loops']}", flush=True)
        elif a.cmd == "stick":
            r = stickiness(sc, seeds, resident)
            print(f"  argmax が常駐者 = {r['n_stick']}/{r['n_loops']} ループ "
                  f"（真のクロマク={r['truth_kuromaku']}）", flush=True)
            for x in r["per"]:
                print(f"    s{x['seed']} {x['outcome']:8s} {x['stick']}/{x['loops']}")
        else:
            rows = oracle(sc, seeds, resident)
            nb = sum(1 for r in rows if r["base"][1] == "defense")
            no = sum(1 for r in rows if r["oracle"][1] == "defense")
            mb = sum(r["base"][0] for r in rows) / len(rows)
            mo = sum(r["oracle"][0] for r in rows) / len(rows)
            print(f"  素: 防衛{nb}/{len(rows)} 平均{mb:.2f} → "
                  f"オラクル: 防衛{no}/{len(rows)} 平均{mo:.2f}", flush=True)
            for r in rows:
                mark = "★" if r["flip"] else " "
                print(f"   {mark} s{r['seed']} {r['base']} → {r['oracle']}")

    check_no_knob_writes(before)
    print("✅ 切替口の束＝測定の前後で1bitも動いていない", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
