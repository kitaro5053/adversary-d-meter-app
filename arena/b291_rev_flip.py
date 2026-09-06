# -*- coding: utf-8 -*-
"""B-291 の rev 逆行の検死（★計測のみ・本番経路は無変更／読むだけ）。

発注＝FableA（§72-130 の申し送り＝所見4）＝「rev が全腕で唯一逆行する機序の解明」。

本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない。
対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う＝ラッパを挟まない＝
ベンチと同一の乱数消費が構造的に保証される（先例＝`arena/b287_probe.py`／`b291_probe.py`）。

測るもの
--------
(1) **決定列の差分**＝同一局を OFF と ON で走らせ、**最初に食い違った決定**を特定し、
    そこから終局までの脚本家の伏せ札列・板の暗躍・犯人の不安を並べる。
(2) **「元から二正面が立っているターン」の内訳**＝OFF の `split` ターン
    （＝囮＝broken の板と、本命＝broken でない板へ、同一ターンに暗躍札）の
    局・ループ・日・板の組み合わせ。★flip した局との**重なり**を数える。
(3) **B-289 の結合の切り分け**＝`on` と `on289`（B-291 ON かつ B-289 OFF）の
    per-game 差分＝flip が `on289` でも同じ向きに出るか。

同定は内容ベース（`b286_line_signals` をそのまま引く）＝行番号キーを使わない。
行キー＝(script, seed, loop, day)＝全次元・一意（`_split_turns` で assert）。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b291_rev_flip \
        --perm rev --days 5 --out out.txt
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

_ANYAKU = {"暗躍+1", "暗躍+2"}

#: 腕＝クラス属性の上書き（`agents/` の既定は1バイトも書き換えない）。
MODES = {
    "off":   {},
    "on":    {"B291_BAIT_EXEMPT": True},
    "on289": {"B291_BAIT_EXEMPT": True, "B289_ALT_BONUS": 0.0},
}
FLAGS = ("B291_BAIT_EXEMPT", "B291_BAIT_MAX", "B289_ALT_BONUS")
DEFAULTS = {"B291_BAIT_EXEMPT": False, "B291_BAIT_MAX": 1, "B289_ALT_BONUS": 40.0}


class arm:
    """腕（クラス属性の束）を一時的に倒すコンテキスト＝例外時も必ず復元。"""

    def __init__(self, mode: str):
        self.mode = mode

    def __enter__(self):
        from agents.heuristic import HeuristicMastermind as HM
        self.HM = HM
        self.old = {f: getattr(HM, f) for f in FLAGS}
        for f in FLAGS:
            setattr(HM, f, MODES[self.mode].get(f, DEFAULTS[f]))
        return self

    def __exit__(self, *exc):
        for f, v in self.old.items():
            setattr(self.HM, f, v)


def play(name: str, seed: int, script, mode: str, loops: int = 8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    with arm(mode):
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        return run_game(replace(script, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})


def outcome_of(state, loops: int = 8):
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return "defense", state.loop_no
    if fb:
        return ("fb_win" if state.winner == "protagonist" else "fb_loss"), loops + 1
    return "loss", loops + 1


# ---------------------------------------------------------------------------
# (2) 「元から二正面が立っているターン」＝OFF の split ターンの内訳
# ---------------------------------------------------------------------------
def split_turns(name: str, seed: int, state, log) -> list[dict]:
    """同一ターンに 囮（broken の板）と 本命（broken でない板）へ暗躍札を置いたターン。

    「囮」の判定＝**そのターンの脚本家の最初の `set_card` の view**（ターン頭）で
    `b286_line_signals` を引いた `broken_seats`＝AI 自身の検出定義そのまま。
    """
    from agents.heuristic import b286_line_signals
    tv: dict[tuple[int, int], dict] = {}
    for r in log:
        if r["actor"] == "mastermind" and r["decision"] == "set_card":
            tv.setdefault((r["loop"], r["day"]), r["view"])
    rows = []
    for ev in state.history:
        if ev.get("event") != "cards_revealed":
            continue
        lp, day = ev.get("loop"), ev.get("day")
        v = tv.get((lp, day))
        if v is None:
            continue
        broken = {t for k, t in b286_line_signals(v)["broken_seats"] if k == "board"}
        boards = {p.get("target") for p in ev.get("placements", ()) or ()
                  if p.get("owner") == "mastermind" and p.get("card") in _ANYAKU
                  and p.get("target_kind") == "board"}
        if boards & broken and boards - broken:
            rows.append({"script": name, "seed": seed, "loop": lp, "day": day,
                         "bait": sorted(boards & broken),
                         "main": sorted(boards - broken)})
    return rows


# ---------------------------------------------------------------------------
# (1) 決定列の差分
# ---------------------------------------------------------------------------
def _sig(r: dict):
    return (r["loop"], r["day"], r["actor"], r["decision"],
            tuple(sorted((r.get("chosen") or {}).items(), key=lambda kv: str(kv[0]))))


def first_divergence(log_a: list, log_b: list):
    for i, (ra, rb) in enumerate(zip(log_a, log_b)):
        if _sig(ra) != _sig(rb):
            return i, ra, rb
    return (None, None, None) if len(log_a) == len(log_b) else \
        (min(len(log_a), len(log_b)), None, None)


def turn_lines(state, log, from_loop: int, from_day: int) -> list[str]:
    """食い違ったターン以降の「伏せ札列＋盤面」を1ターン1ブロックで出す。"""
    tv: dict[tuple[int, int], dict] = {}
    for r in log:
        if r["actor"] == "mastermind" and r["decision"] == "set_card":
            tv.setdefault((r["loop"], r["day"]), r["view"])
    out = []
    for ev in state.history:
        if ev.get("event") != "cards_revealed":
            continue
        lp, day = ev.get("loop"), ev.get("day")
        if (lp, day) < (from_loop, from_day):
            continue
        v = tv.get((lp, day)) or {}
        ba = {b: n for b, n in (v.get("board_anyaku") or {}).items() if n}
        un = {c["name"]: c.get("unrest", 0) for c in v.get("characters", ())
              if c.get("unrest", 0)}
        mm_p = [f"{p['card']}→{p['target']}" for p in ev.get("placements", ()) or ()
                if p.get("owner") == "mastermind"]
        pr_p = [f"{p['card']}→{p['target']}" for p in ev.get("placements", ()) or ()
                if p.get("owner") != "mastermind"]
        out.append(f"  L{lp}D{day} 盤{ba or '-'} 不安{un or '-'}\n"
                   f"      mm: {'  '.join(mm_p)}\n"
                   f"      pr: {'  '.join(pr_p)}")
    return out


# ---------------------------------------------------------------------------
# 走査
# ---------------------------------------------------------------------------
def run(perm: str = "rev", days: int = 5, loops: int = 8,
        targets: tuple[tuple[str, int], ...] = (("random_BTX", 1), ("random_BTX", 3)),
        sweep: bool = True, verbose: bool = True) -> str:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    L: list[str] = [f"# B-291 rev 検死（{days}日級・perm={perm}）"]
    scripts = None
    res: dict[str, dict] = {}
    splits: list[dict] = []
    install_perm(perm)
    try:
        scripts = list(benchmark_scripts(days=days))
        for mode in (("off", "on", "on289") if sweep else ()):
            res[mode] = {}
            for name, seed, sc in scripts:
                state, log = play(name, seed, sc, mode, loops)
                oc, ltw = outcome_of(state, loops)
                res[mode][(name, seed)] = (ltw, oc)
                if mode == "off":
                    splits.extend(split_turns(name, seed, state, log))
                if verbose:
                    print(f"  {mode} {name}#{seed}: {ltw} {oc}", flush=True)
        # -- (1) 決定列の差分（対象局のみ・OFF と ON を並べる） --
        for tname, tseed in targets:
            sc = next((s for n, sd, s in scripts if n == tname and sd == tseed), None)
            if sc is None:
                L.append(f"\n## {tname}#{tseed}: 母集団に無い")
                continue
            sa, la = play(tname, tseed, sc, "off", loops)
            sb, lb = play(tname, tseed, sc, "on", loops)
            i, ra, rb = first_divergence(la, lb)
            L.append(f"\n## (1) {tname}#{tseed}  OFF={outcome_of(sa, loops)} "
                     f"→ ON={outcome_of(sb, loops)}")
            if ra is None:
                L.append("  差分なし（決定列が一致）")
                continue
            L.append(f"  ★最初の食い違い＝決定#{i}  L{ra['loop']}D{ra['day']} "
                     f"{ra['actor']}/{ra['decision']}")
            L.append(f"      OFF: {ra['chosen']}")
            L.append(f"      ON : {rb['chosen']}")
            b = ra["view"].get("b286") if isinstance(ra.get("view"), dict) else None
            L.append(f"      （その決定の候補数 OFF={len(ra.get('options') or ())}"
                     f" ON={len(rb.get('options') or ())}{'' if b is None else ' b286=' + str(b)}）")
            L.append("  --- OFF 以降のターン ---")
            L += turn_lines(sa, la, ra["loop"], ra["day"])
            L.append("  --- ON 以降のターン ---")
            L += turn_lines(sb, lb, ra["loop"], ra["day"])
    finally:
        uninstall_perm()

    if not sweep:
        return "\n".join(L)
    # -- (2) split ターンの内訳と flip との重なり --
    keys = [(r["script"], r["seed"], r["loop"], r["day"]) for r in splits]
    assert len(keys) == len(set(keys)), "split ターンの行キーが一意でない"
    flip_on = {k for k in res["off"] if res["off"][k] != res["on"][k]}
    flip_289 = {k for k in res["off"] if res["off"][k] != res["on289"][k]}
    L.append(f"\n## (2) OFF の「囮＋本命が同時」ターン = {len(splits)} 件")
    for r in splits:
        mark = "★flip局" if (r["script"], r["seed"]) in flip_on else ""
        L.append(f"  {r['script']}#{r['seed']} L{r['loop']}D{r['day']} "
                 f"囮{r['bait']} 本命{r['main']} {mark}")
    sp_games = {(r["script"], r["seed"]) for r in splits}
    L.append(f"  局の異なり = {len(sp_games)} 局 ／ うち ON で flip した局 = "
             f"{len(sp_games & flip_on)} 局")
    L.append(f"  split ターンのうち flip 局に属する件数 = "
             f"{sum(1 for r in splits if (r['script'], r['seed']) in flip_on)}")

    # -- (3) B-289 結合の切り分け --
    L.append(f"\n## (3) per-game 差分（Σ第2層＝Σ(min(ltw,9)−1)）")
    tot = {m: sum(min(v[0], 9) - 1 for v in res[m].values()) for m in res}
    L.append(f"  off={tot['off']}  on={tot['on']}({tot['on']-tot['off']:+d})  "
             f"on289={tot['on289']}({tot['on289']-tot['off']:+d})")
    L.append(f"  flip(off→on) = {len(flip_on)} 局 ／ flip(off→on289) = {len(flip_289)} 局")
    L.append(f"{'局':<22} {'OFF':>14} {'ON':>14} {'ON289':>14}  一致")
    for k in sorted(flip_on | flip_289):
        o, a, c = res["off"][k], res["on"][k], res["on289"][k]
        same = "同方向" if a == c else ("★on289で戻る" if c == o else "別方向")
        L.append(f"{k[0]+'#'+str(k[1]):<22} {str(o):>14} {str(a):>14} "
                 f"{str(c):>14}  {same}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-291 rev 逆行の検死")
    ap.add_argument("--perm", default="rev")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--out", default=None)
    ap.add_argument("--targets", default=None,
                    help="例 random_FS:3,random_FS:16（省略時＝random_BTX#1,#3）")
    ap.add_argument("--no-sweep", action="store_true",
                    help="(2)(3) の3腕×全局の走査を省き、(1) の決定列だけ出す")
    ap.add_argument("-q", "--quiet", action="store_true")
    ns = ap.parse_args(argv)
    tg = (tuple((t.split(":")[0], int(t.split(":")[1]))
                for t in ns.targets.split(",")) if ns.targets
          else (("random_BTX", 1), ("random_BTX", 3)))
    txt = run(perm=ns.perm, days=ns.days, loops=ns.loops, targets=tg,
              sweep=not ns.no_sweep, verbose=not ns.quiet)
    if ns.out:
        with open(ns.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
