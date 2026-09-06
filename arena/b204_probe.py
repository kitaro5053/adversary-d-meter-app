# -*- coding: utf-8 -*-
"""B-204 Phase 0：板ガードの**許容量**（能力チャネル併存下）を採点表で確認する読み取り専用プローブ。

★agents/ の判断経路・既定値には一切触れない（読むだけ）。

背景（バックログ §68-6／§68-7）＝板敗北ルール（爆弾X 等）の盤に対し、主人公の暗躍禁止は
**カード供給しか止められない**（`rules/10:65`＝行動解決フェイズのみ有効）。脚本家能力
（不穏な噂＝ボードに暗躍+1）は阻止不能で、しかも `rules/00:109`「複数可能なら自由順で
各1回ずつ」＝ミスリーダー等と同フェイズに併用できる。∴ 臨界2 の盤にカードで1枚通した
時点で、能力の +1 が最後の1つを無料で供給しうる。

サブコマンド:
    python -m arena.b204_probe why  [--log PATH]
        # 主人公の全決定を素で再生（bit 一致確認）し、mm が**板**に札を伏せた日の
        #   - 板ごとの現在値／許容量（臨界2 − 現在値 − k − 1）
        #   - 上位候補の採点／`暗躍禁止→{板}` の採点
        #   - P(不穏な噂)（belief の rule 事後）と k の推定
    python -m arena.b204_probe pk [--log PATH]
        # P(不穏な噂) など能力供給の事後確率の取得口の存在確認（全決定で列挙）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.debug import ProbedProtagonist

REPO = Path(__file__).resolve().parent.parent
LOG_BOMB = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_爆弾X発動後_2026-08-11.jsonl"
LOG_LOVE = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_ラバーズ連鎖_2026-08-11.jsonl"


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return lines[0], [d for d in lines if d.get("type") == "decision"]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _mm_boards(view: dict) -> list[str]:
    return sorted({p["target"] for p in view.get("placements", [])
                   if p.get("owner") == "mastermind"
                   and p.get("target_kind") == "board"})


def _p_rumor(hp) -> float:
    """belief の rule 事後から P(不穏な噂 ∈ ルールX) を取る（B-194 と同じ取得口）。"""
    try:
        return sum(p for (_ry, rxs), p in hp._belief.rule_marginals().items()
                   if "不穏な噂" in (rxs or ()))
    except Exception:
        return float("nan")


def _ability_hist(view: dict) -> tuple[dict, set]:
    """公開履歴から (loop,板)->能力フェイズ暗躍の観測数, 今ループ使用済み板 を返す。"""
    from engine.board import AREAS
    per_loop: dict = {}
    spent_now: set = set()
    loop_now = view.get("loop")
    for e in view.get("history", []) or []:
        if (e.get("event") == "anyaku"
                and e.get("phase") == "mastermind_ability"
                and e.get("target") in AREAS):
            if e.get("loop") == loop_now:
                spent_now.add(e.get("target"))
            else:
                k = (e.get("loop"), e.get("target"))
                per_loop[k] = per_loop.get(k, 0) + 1
    return per_loop, spent_now


def cmd_why(args) -> int:
    meta, decisions = _load(args.log)
    print(f"===== B-204 Phase 0  {Path(args.log).name}  (build {meta.get('tool_build')}) =====")
    hp = ProbedProtagonist(0, top=400)
    n_same = n_all = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        view, options = d["view"], d["options"]
        chosen = hp.decide(view, d["decision"], options)
        same = _clean(chosen) == _clean(d["chosen"])
        n_all += 1
        n_same += bool(same)
        if d["decision"] != "set_card":
            continue
        mmb = _mm_boards(view)
        if not (args.all or mmb):
            continue
        rec = hp.records[-1]
        mark = "" if same else f"  ≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        banr = view.get("board_anyaku") or {}
        per_loop, spent_now = _ability_hist(view)
        proven = sorted({b for (_lp, b) in per_loop})
        print(f"--- L{d['loop']}D{d['day']} {view.get('seat')}: "
              f"選択={_fmt(_clean(chosen))}{mark}")
        print(f"      mm札(板)={mmb}  board_anyaku={dict(sorted(banr.items()))}")
        print(f"      能力供給の公開実績(過去ループ)={proven}  今ループ使用済={sorted(spent_now)}  "
              f"P(不穏な噂)={_p_rumor(hp):.3f}")
        for s, o in rec["scored"][:5]:
            print(f"      TOP  {s:8.2f} {_fmt(o)}")
        for s, o in rec["scored"]:
            if o.get("card") == "暗躍禁止" and o.get("target_kind") == "board":
                cur = int(banr.get(o.get("target"), 0) or 0)
                k = 0 if o.get("target") in spent_now else (
                    1 if o.get("target") in proven else 0)
                print(f"      ★板禁止 {s:8.2f} {_fmt(o)}  現在値={cur} "
                      f"k(実績ベース)={k} 許容量={2 - cur - k - 1}")
        print(f"      _b189_futile={sorted(getattr(hp, '_b189_futile_boards_now', ()))} "
              f"_b194_futile={sorted(getattr(hp, '_b194_futile_now', ()))} "
              f"danger_board={getattr(hp, '_observed_defeat_board', None)} "
              f"probs={ {k: round(v, 3) for k, v in (getattr(hp, '_board_defeat_probs', None) or {}).items()} }")
    print(f"[再生一致] {n_same}/{n_all}")
    return 0


def cmd_pk(args) -> int:
    """能力供給の事後確率の取得口の確認（rule_marginals の中身を1回だけ全展開）。"""
    meta, decisions = _load(args.log)
    hp = ProbedProtagonist(0, top=8)
    shown = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        hp.decide(d["view"], d["decision"], d["options"])
        if d["decision"] != "set_card" or shown >= args.n:
            continue
        shown += 1
        try:
            rm = hp._belief.rule_marginals()
        except Exception as e:
            print(f"rule_marginals 取得失敗: {e!r}")
            return 1
        agg: dict = {}
        for (ry, rxs), p in rm.items():
            for r in (ry,) + tuple(rxs or ()):
                agg[r] = agg.get(r, 0.0) + p
        top = dict(sorted(agg.items(), key=lambda x: -x[1])[:12])
        print(f"--- L{d['loop']}D{d['day']} ルール周辺確率(上位12)="
              f"{json.dumps({k: round(v, 3) for k, v in top.items()}, ensure_ascii=False)}")
    return 0


class _ReplayMastermind:
    """棋譜の脚本家決定を (loop, day, decision) の出現順で再生する脚本家（B-201 と同型）。

    棋譜に無い決定（主人公が守って局面が分岐した後）は AI 脚本家へフォールバックする。
    """

    def __init__(self, decisions, seed: int = 0):
        from agents.heuristic import HeuristicMastermind
        self._queue: dict = {}
        for d in decisions:
            if d.get("actor") != "mastermind":
                continue
            self._queue.setdefault((d["loop"], d["day"], d["decision"]), []).append(d["chosen"])
        self._fallback = HeuristicMastermind(seed)
        self.n_replayed = 0
        self.n_fallback = 0

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        key = (view.get("loop"), view.get("day"), decision)
        q = self._queue.get(key) or []
        while q:
            cand = q.pop(0)
            for o in options:
                if all(o.get(k) == v for k, v in cand.items() if k != "prov"):
                    self.n_replayed += 1
                    return o
        self.n_fallback += 1
        return self._fallback.decide(view, decision, options)


def cmd_replay(args) -> int:
    """単局再現（主のゲート）：人間 mm の手をそのまま再生し、主人公AIだけ差し替える。"""
    from sim import run_game
    from sim.state import script_from_dict
    from agents.heuristic_protagonist import HeuristicProtagonist

    meta, decisions = _load(args.log)
    script = script_from_dict(meta["script"])
    for on in (False, True):
        hp = HeuristicProtagonist(0)
        hp.B204_ZERO_ALLOWANCE_GUARD = on
        mm = _ReplayMastermind(decisions, 0)
        state, _log = run_game(script, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        print(f"[B-204 {'ON ' if on else 'OFF'}] winner={state.winner} "
              f"loops_played={state.loop_no} 最終日={state.day} "
              f"（人間手の再生={mm.n_replayed} / AI委譲={mm.n_fallback}）")
    return 0


def cmd_frozen(args) -> int:
    """frozen view 再生（棋譜の view/options を素で食わせる）で ON/OFF の差分席だけ出す。"""
    meta, decisions = _load(args.log)
    rows = []
    outs: dict = {}
    for on in (False, True):
        hp = ProbedProtagonist(0, top=400)
        hp.B204_ZERO_ALLOWANCE_GUARD = on
        seq = []
        for d in decisions:
            if d.get("actor") == "mastermind":
                continue
            chosen = hp.decide(d["view"], d["decision"], d["options"])
            seq.append((d["loop"], d["day"], d["view"].get("seat"),
                        d["decision"], _fmt(_clean(chosen))))
        outs[on] = seq
    n_diff = 0
    for a, b in zip(outs[False], outs[True]):
        if a != b:
            n_diff += 1
            rows.append(f"  L{a[0]}D{a[1]} {a[2]} {a[3]}: OFF={a[4]}  →  ON={b[4]}")
    print(f"===== B-204 frozen 再生  {Path(args.log).name} =====")
    print(f"  席数={len(outs[False])}  ON/OFF 差分={n_diff}")
    for r in rows:
        print(r)
    return 0


def cmd_cf(args) -> int:
    """★反実仮想（L5 の継続）＝『L5D1 のカードを阻止していたら L5D3 はどうなるか』。

    教材（§68-7）の L5D3 の view は **D1 のカードが通った後**（都市=1）。B-204 ON では
    D1 で阻止する＝D3 の view は都市=0 になる。棋譜の view を**公開情報の範囲で**その形に
    patch（`board_anyaku['都市']=0`＋L5D1 の都市 anyaku イベントを履歴から除去）して
    採点し直す＝mm の手（D3 も都市に札）はそのまま。
    """
    meta, decisions = _load(args.log)
    tgt_board = args.board
    hp = ProbedProtagonist(0, top=400)
    hp.B204_ZERO_ALLOWANCE_GUARD = True
    print(f"===== B-204 反実仮想（L{args.loop} 継続・{tgt_board}） =====")
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        view = json.loads(json.dumps(d["view"]))
        if int(view.get("loop", 0)) == args.loop:
            # D1 のカードを阻止した世界＝そのループの行動解決フェイズ暗躍を巻き戻す
            n_undo = 0
            hist = []
            for e in view.get("history", []) or []:
                if (e.get("event") == "anyaku" and e.get("phase") == "action_resolution"
                        and e.get("target") == tgt_board and e.get("loop") == args.loop):
                    n_undo += int(e.get("delta", 0) or 0)
                    continue
                hist.append(e)
            view["history"] = hist
            ba = view.get("board_anyaku") or {}
            ba[tgt_board] = max(0, int(ba.get(tgt_board, 0) or 0) - n_undo)
            view["board_anyaku"] = ba
        chosen = hp.decide(view, d["decision"], d["options"])
        if int(view.get("loop", 0)) != args.loop or d["decision"] != "set_card":
            continue
        rec = hp.records[-1]
        print(f"--- L{d['loop']}D{d['day']} {view.get('seat')}: 反実選択={_fmt(_clean(chosen))} "
              f"（棋譜={_fmt(_clean(d['chosen']))}）  {tgt_board}={view['board_anyaku'].get(tgt_board)}")
        for s, o in rec["scored"][:3]:
            print(f"      TOP  {s:8.2f} {_fmt(o)}")
        for s, o in rec["scored"]:
            if o.get("card") == "暗躍禁止" and o.get("target") == tgt_board:
                print(f"      ★板禁止 {s:8.2f} {_fmt(o)}")
    return 0


def cmd_count(args) -> int:
    """★行為の数え上げ（規約§11b の最強証拠）＝ベンチ全局で述語が何席で True になるか。"""
    import agents.heuristic_protagonist as hpm
    from arena.benchmark import benchmark_scripts, loops_to_win

    hpm.HeuristicProtagonist.B204_ZERO_ALLOWANCE_GUARD = True
    inner = hpm.HeuristicProtagonist._b204_zero_allowance_boards
    stat = {"評価回数": 0, "★発火席": 0}
    hits: dict = {}
    cur = {"n": None}

    def counting(self, view):
        stat["評価回数"] += 1
        r = inner(self, view)
        if r:
            stat["★発火席"] += 1
            hits[cur["n"]] = hits.get(cur["n"], 0) + 1
        return r

    hpm.HeuristicProtagonist._b204_zero_allowance_boards = counting
    try:
        for name, seed, script in benchmark_scripts(days=args.days):
            cur["n"] = f"{name} s{seed}"
            loops_to_win(script, seed=seed, loops=8)
    finally:
        hpm.HeuristicProtagonist._b204_zero_allowance_boards = inner
        hpm.HeuristicProtagonist.B204_ZERO_ALLOWANCE_GUARD = False
    print(f"===== B-204 数え上げ（{args.days}日級） =====")
    print(f"  {stat}")
    print(f"  発火した局: {json.dumps(hits, ensure_ascii=False)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena.b204_probe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("why", cmd_why), ("pk", cmd_pk), ("replay", cmd_replay),
                     ("frozen", cmd_frozen), ("count", cmd_count), ("cf", cmd_cf)):
        p = sub.add_parser(name)
        p.add_argument("--log", default=str(LOG_BOMB))
        p.add_argument("--all", action="store_true")
        p.add_argument("-n", type=int, default=3)
        p.add_argument("--days", type=int, default=3)
        p.add_argument("--loop", type=int, default=5)
        p.add_argument("--board", default="都市")
        p.set_defaults(func=fn)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
