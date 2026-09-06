# -*- coding: utf-8 -*-
"""rev-b174 レビューレーン：B-174 land（`B174_COOL_SELF_EXCLUDE=True` 既定化）後の検死計測。

任務＝B-174 land で退行した防衛4局（＋改善6局）の検死（レビュー＝計測のみ・`agents/` 非接触）。
発注＝FableA（2026-08-07）。起票の文脈＝`docs/バックログ_構想メモ_FableA.md` §50/§58/§59/§52。

★land 後の main では `arena.b174_audit._CLASS_DEFAULTS` が **ON をクラス既定として捕まえる**ため、
`b174_audit` の `CONFIGS["off"]` は OFF にならない（引き継ぎ §1 の注意そのもの）。
∴ 本モジュールは **反実 OFF を明示値 `{"B174_COOL_SELF_EXCLUDE": False}` で張る**。

| cfg | 意味 |
|---|---|
| `on`  | `{}`＝現行 main のクラス既定（land 済み＝`B174_COOL_SELF_EXCLUDE=True`） |
| `off` | `{"B174_COOL_SELF_EXCLUDE": False}`＝**反実＝land 前（旧正典 129/2.846・63/3.643）の挙動** |

コマンド（すべて `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面 or 背面・単独実行）：

    python -m arena.rev174_audit ab   --days 3            # off→on 連続実測＋局単位 diff 全数
    python -m arena.rev174_audit ab   --days 5
    python -m arena.rev174_audit why  --days 3 --games btx_bomb#4,btx_bomb#8   # 初回分岐席
    python -m arena.rev174_audit seat --days 3 --game btx_bomb#4 --cfg on      # 席注釈つき決定列
    python -m arena.rev174_audit seat --days 3 --game btx_bomb#4 --cfg on --loop 2 --day 1

`seat` が既存 `b177_audit._Rich` の注釈へ足すもの（★すべて主人公自身の内部量＝
`protagonist_view` と belief から作られる。正解の配役は読まない）：

- `p_ito`＝`self._ito_p`（belief の rule_marginals から因果の糸の確率・`heuristic_protagonist.py:1069`）
  ＝**B-182（§59）の発火条件がこの席で立つか**を見るための量。
- `b66_gate`＝既存の因果の糸自傷ゲート `_b66_ito_selfharm_invest(view, tgt)` の実値と
  その内訳（`p_ito>=0.7`／非最終ループ／`_b66_unproven_boards>=4`／犯人候補）＝
  **B-182 (c) の「既存実装がどこまでケアしているか」**の直接測定。
- `threats`＝`self._b100_plan`（`_defense_plan_recs` が席ごとに作る脅威リストと防御計画・
  `heuristic_protagonist.py:684`）の要約＝**B-176（§52）の「確実な負け筋 vs 不確定な負け筋」**が
  この席でどう並んでいたか（fatal 脅威の prob・covered/uncovered）。

★プローブは `super().decide()` の戻り値をそのまま返し、追加は**読み取りだけ**＝挙動不変。
物証は `selfcheck`（素の主人公AIとの棋譜完全一致）で取る。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from agents.heuristic_protagonist import HeuristicProtagonist as HP
from arena.b174_audit import (_CLASS_DEFAULTS, _KEYS, _agg, _one, _stream,
                              _play_plain)
from arena.b177_audit import _Rich, _play as _rich_play, _scripts

#: 旧正典値（land 前＝反実 OFF が一致すべき値。`docs/引き継ぎ_新スレッド_2026-07-21.md` §1 の旧値）
OLD_CANON = {3: {"defense": 129, "mean": 2.846, "L1": 7, "loss": 0},
             5: {"defense": 63, "mean": 3.643, "L1": 2, "loss": 4}}

CONFIGS: dict[str, dict] = {
    "on":  {},                                    # 現行 main（land 済み既定）
    "off": {"B174_COOL_SELF_EXCLUDE": False},     # 反実＝land 前
}


def apply_cfg(cfg: str) -> None:
    """クラス既定（＝land 後の main）へ戻してから、その版の明示値だけを張る。"""
    for k, v in _CLASS_DEFAULTS.items():
        setattr(HP, k, v)
    for k, v in CONFIGS[cfg].items():
        setattr(HP, k, v)


def switches() -> str:
    return json.dumps({k: getattr(HP, k) for k in _KEYS}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# ab＝反実 OFF → 現行 ON の連続実測＋局単位 diff（★OFF が旧正典値と一致するかを先に印字）
# ---------------------------------------------------------------------------
def ab(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    games = list(benchmark_scripts(days=days))[start:end]

    def _run(cfg: str) -> dict:
        apply_cfg(cfg)
        print(f"  [切替口:{cfg}] {switches()} / days={days}", flush=True)
        return {f"{name}#{seed}": _one(sc, seed, loops) for name, seed, sc in games}

    base = _run("off")                      # 反実（land 前の挙動）
    off_agg = _agg(base, loops)
    canon = OLD_CANON.get(days, {})
    canon_ok = all(off_agg.get(k) == v for k, v in canon.items())
    res = _run("on")                        # 現行 main
    apply_cfg("on")                         # ★終了時は現行既定へ戻す
    flips, moved = [], 0
    for g in base:
        bo, bn, bt = base[g]
        vo, vn, vt = res[g]
        if bt != vt:
            moved += 1
        if (bo, bn) != (vo, vn):
            flips.append({"game": g, "off": f"{bo}:{bn}", "on": f"{vo}:{vn}",
                          "向き": ("改善" if (vo == "defense"
                                              and (bo != "defense" or vn < bn))
                                   else "退行")})
    def _by_script(r):
        d: Counter = Counter()
        for g, (o, _n, _t) in r.items():
            d[g.split("#")[0] + "/局数"] += 1
            d[g.split("#")[0] + "/防衛数"] += int(o == "defense")
        return dict(d)
    return {"days": days, "n_games": len(games),
            "off(反実=land前)": off_agg,
            "★off は旧正典値と一致するか": canon_ok,
            "旧正典値": canon,
            "on(現行main)": _agg(res, loops),
            "moved": moved, "flip数": len(flips), "flips": flips,
            "off_by_script": _by_script(base), "on_by_script": _by_script(res)}


# ---------------------------------------------------------------------------
# why＝flip 局の初回分岐席（`b174_audit._StreamProbe` を本モジュールの cfg で回す）
# ---------------------------------------------------------------------------
def why(days: int = 3, loops: int = 8, games: tuple = ()) -> dict:
    all_games = _scripts(days)
    out = []
    for g in games:
        _nm, seed, sc = all_games[g]
        apply_cfg("off")
        s0 = _stream(sc, seed, loops)
        apply_cfg("on")
        s1 = _stream(sc, seed, loops)
        i = 0
        while i < min(len(s0), len(s1)) and s0[i] == s1[i]:
            i += 1
        out.append({"game": g, "一致した席数": i,
                    "off側の席": s0[i] if i < len(s0) else None,
                    "on側の席": s1[i] if i < len(s1) else None})
    apply_cfg("on")
    return {"days": days, "起点": out}


# ---------------------------------------------------------------------------
# seat＝席注釈つき決定列（`b177_audit._Rich` ＋ 因果の糸/脅威のスナップショット）
# ---------------------------------------------------------------------------
class _SeatProbe(_Rich):
    """`_Rich` の注釈へ p_ito / b66_gate / threats を足す（★読み取りのみ＝挙動不変）。"""

    def _annotate(self, view: dict, rec: dict) -> None:
        rec["loops_total"] = view.get("loops_total")
        rec["p_ito"] = round(float(getattr(self, "_ito_p", 0.0)), 3)
        tgt = rec.get("target")
        if rec.get("kind") == "character" and tgt:
            from engine.data import unrest_threshold_of
            gate = None
            try:
                gate = bool(self._b66_ito_selfharm_invest(view, tgt))
            except Exception:
                pass
            rec["b66_gate(因果の糸自傷ゲート)"] = {
                "発火": gate,
                "p_ito>=0.7": float(getattr(self, "_ito_p", 0.0)) >= 0.7,
                "非最終ループ": (view.get("loops_total") is not None
                                 and view["loops_total"] - view.get("loop", 0) >= 1),
                "全板無実証(>=4)": len(getattr(self, "_b66_unproven_boards", ()) or ()) >= 4,
                "対象は犯人候補": tgt in (getattr(self, "_culprits", set()) or set()),
                "対象の不安臨界": unrest_threshold_of(tgt),
            }
        stash = getattr(self, "_b100_plan", None)
        if stash:
            threats, plan = stash
            fat = [t for t in threats if t.fatal]
            rec["threats(fatal)"] = [
                {"kind": t.kind, "label": t.label, "prob": round(t.prob, 3),
                 "防御可能": t.defendable, "突破済み": t.breached, "race": t.race}
                for t in sorted(fat, key=lambda t: -t.prob)]
            rec["plan.picks"] = [f"{b.card}→{b.target}（{b.label}）" for b in plan.picks]
            rec["plan.uncovered"] = [
                {"kind": t.kind, "label": t.label, "prob": round(t.prob, 3)}
                for t in plan.uncovered]

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        n0 = len(self.stream)
        chosen = super().decide(view, decision, options)
        # `_Rich.decide` が set_card の席を1件 append した直後に注釈を足す
        if decision == "set_card" and len(self.stream) > n0:
            self._annotate(view, self.stream[-1])
        return chosen


def seat(days: int, game: str, cfg: str, loops: int = 8,
         loop: int | None = None, day: int | None = None) -> dict:
    from dataclasses import replace
    from agents import HeuristicMastermind
    from sim import run_game
    from arena.b145_audit import _outcome

    _nm, seed, sc = _scripts(days)[game]
    apply_cfg(cfg)
    print(f"[切替口:{cfg}] {switches()} / days={days} / game={game}", flush=True)
    hp = _SeatProbe(seed)
    st, _ = run_game(replace(sc, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    apply_cfg("on")
    rows = hp.stream
    if loop is not None:
        rows = [r for r in rows if r.get("loop") == loop]
    if day is not None:
        rows = [r for r in rows if r.get("day") == day]
    return {"game": game, "cfg": cfg, "outcome": _outcome(st),
            "loop_no": st.loop_no,
            "incidents": [{"day": i.day, "name": i.name} for i in sc.incidents],
            "rows": rows}


# ---------------------------------------------------------------------------
# selfcheck＝プローブの挙動不変（素の主人公AIと棋譜完全一致・cfg 両方）
# ---------------------------------------------------------------------------
def selfcheck(days: int, games: tuple, loops: int = 8) -> dict:
    all_games = _scripts(days)
    bad = []
    for g in games:
        _nm, seed, sc = all_games[g]
        for cfg in ("off", "on"):
            apply_cfg(cfg)
            ref = _play_plain(sc, seed, loops)
            _hp, _st, tr = _rich_play(sc, seed, loops)   # _Rich（親）で担保済みだが
            hp2 = _SeatProbe(seed)                        # 本クラスでも取り直す
            from dataclasses import replace
            from agents import HeuristicMastermind
            from sim import run_game
            st2, _ = run_game(replace(sc, loops=loops),
                              {"mastermind": HeuristicMastermind(seed),
                               "p1": hp2, "p2": hp2, "p3": hp2})
            tr2 = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
                   for e in st2.history]
            if tr != ref or tr2 != ref:
                bad.append({"game": g, "cfg": cfg,
                            "rich一致": tr == ref, "seat一致": tr2 == ref})
    apply_cfg("on")
    return {"days": days, "n": len(games), "mismatch": len(bad), "bad": bad}


def main() -> None:
    ap = argparse.ArgumentParser(description="rev-b174 検死計測")
    ap.add_argument("cmd", choices=["ab", "why", "seat", "selfcheck"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--games", default="", help="カンマ区切りの局名（例 btx_bomb#4）")
    ap.add_argument("--game", default="btx_bomb#4")
    ap.add_argument("--cfg", default="on", choices=list(CONFIGS))
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.cmd == "ab":
        res = ab(a.days, a.loops, a.start, a.end)
    elif a.cmd == "why":
        res = why(a.days, a.loops, tuple(x for x in a.games.split(",") if x))
    elif a.cmd == "seat":
        res = seat(a.days, a.game, a.cfg, a.loops, a.loop, a.day)
    else:
        res = selfcheck(a.days, tuple(x for x in a.games.split(",") if x), a.loops)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
