# -*- coding: utf-8 -*-
"""B-140fu：`board_removal_kb_scope` の A/B で「主人公の板推定がどこで割れるか」を実測する。

背景＝B-140（転校生を板の暗躍除去役から外す＝KB食い違いの是正／
`docs/監査_B140_転校生の板除去役誤算入_2026-08-02.md`）を land した結果、
正典条件（perm=id）で新たに敗北した局が 5日級 `random_FS` s3 の1局だけあった。
`docs/負け筋防御ツリー.md` §要対応：B-140 が「次の検死対象」として挙げた問い＝
**旧AIの無駄手（臨界2の板へ重ねる3枚目）が主人公への情報源になっており、
それが消えたことで主人公が板を読めなくなったのか**を実測で判定する。

★本モジュールは**計測専用**＝`agents/ sim/ engine/ rules/` を1行も変更しない。
主人公・脚本家とも `super()` をそのまま呼び、**戻り値の後で属性を読むだけ**
（rng を消費しない＝挙動 bit 同一）。同一性は `verify` サブコマンドが
「素の対局」と「プローブ付き対局」の結末一致で毎回確認する。

読む値（主人公の板推定・すべて `agents/heuristic_protagonist.py`）:
  `_board_defeat_probs`  … per-board「その板が敗北条件の板でありうるか」の確率
  `_b84_top_boards`      … 上の先頭グループ（板ガード +4.0 の宛先）
  `_observed_defeat_board` … 過去の敗北ループで暗躍≥2だった板の投票（実証ベース）
  `_guess_defeat_board`  … 危険板の点推定（照準）

CLI（前面実行・測定は必ず PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8）:
    python -m arena.b140fu_audit verify  --set FS --seed 3 --days 5
    python -m arena.b140fu_audit moves   --set FS --seed 3 --days 5
    python -m arena.b140fu_audit boards  --set FS --seed 3 --days 5
    python -m arena.b140fu_audit endings --set FS --seed 3 --days 5
        ... --json out.json でレコードを保存
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.heuristic import MM_PARAMS
from sim import random_script, run_game

TOGGLE = "board_removal_kb_scope"


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝super() の戻り値の後で属性を読むだけ）
# ---------------------------------------------------------------------------
def _jsonable(v):
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


class EstimateProbeProtagonist(HeuristicProtagonist):
    """主人公の板推定を decide ごとに記録する（スコアには一切触れない）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.est_records: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        probs = getattr(self, "_board_defeat_probs", None)
        rec = {
            "loop": view.get("loop"), "day": view.get("day"),
            "seat": view.get("seat"), "decision": decision,
            "chosen": _jsonable(chosen),
            "board_anyaku": dict(view.get("board_anyaku", {})),
            "probs": ({k: round(float(v), 4) for k, v in probs.items()}
                      if probs else None),
            "top_boards": _jsonable(getattr(self, "_b84_top_boards", None)),
            "odb": getattr(self, "_observed_defeat_board", None),
            "loop_lost": bool(getattr(self, "_loop_lost", False)),
            # ★B-141b で追加＝冷却の照準まわり（読むだけ）。
            "incident_danger": {str(k): round(float(v), 2) for k, v in
                                (getattr(self, "_incident_danger", {}) or {}).items()},
            "culprit_cands": {str(k): sorted(v) for k, v in
                              (getattr(self, "_culprit_cands", {}) or {}).items()},
            "known_culprits": {str(k): v for k, v in
                               (getattr(self, "_known_culprits", {}) or {}).items()},
            "unrest": {c["name"]: c["unrest"] for c in view.get("characters", [])},
            "goodwill": {c["name"]: c["goodwill"] for c in view.get("characters", [])},
        }
        try:
            rec["guess"] = self._guess_defeat_board(view)   # 純関数（rng非消費）
        except Exception as exc:                            # noqa: BLE001
            rec["guess"] = f"(取得不能: {exc!r})"
        # `_guess_defeat_board_raw` が `p_board_rules < 0.05` で早期 return したのか
        # を実測で確かめるため、同じ式を読むだけで再現する（`:4004` と同一の定義）。
        try:
            _rules = self._belief.rule_marginals()
            rec["p_board_rules"] = round(sum(
                pv for (ry, _rxs), pv in _rules.items()
                if ry in ("守るべき場所", "封印されしモノ",
                          "復讐者の灯火", "巨大時限爆弾Xの存在")), 4)
        except Exception as exc:                            # noqa: BLE001
            rec["p_board_rules"] = f"(取得不能: {exc!r})"
        self.est_records.append(rec)
        return chosen


class AnalysisProbeMastermind(HeuristicMastermind):
    """脚本家の `_analyze` から板まわりの読みだけ控える（挙動不変）。"""

    _KEYS = ("goal_boards", "board_removal", "board_removal_boards",
             "keyperson", "killer", "kuromaku", "locked", "ba")

    def __init__(self, seed: int = 0, params: dict | None = None):
        super().__init__(seed, params=params)
        self.ana_records: list[dict] = []
        self._probe_ctx: dict = {}
        self._probe_last: dict = {}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        self._probe_ctx = {"loop": view.get("loop"), "day": view.get("day"),
                           "decision": decision}
        chosen = super().decide(view, decision, options)
        a = self._probe_last or {}
        self.ana_records.append({
            **self._probe_ctx, "chosen": _jsonable(chosen),
            "board_anyaku": dict(view.get("board_anyaku", {})),
            **{k: _jsonable(a.get(k)) for k in self._KEYS},
        })
        return chosen

    def _analyze(self, view: dict) -> dict:
        a = super()._analyze(view)
        self._probe_last = a
        return a


# ---------------------------------------------------------------------------
# 対局の実行
# ---------------------------------------------------------------------------
def build_script(set_name: str, seed: int, days: int, script_loops: int | None = None):
    sl = script_loops or (4 if days == 5 else 3)
    return random_script(set_name, seed, loops=sl, days=days)


def build_bench_script(name: str, seed: int, days: int):
    """★ベンチと**同一の脚本**を作る（B-141b で追加）。

    `arena.benchmark.benchmark_scripts` の1エントリを再現する
    （手書きサンプルは `SAMPLE_SCRIPTS[name]()`／`random_FS`・`random_BTX` は
    `random_script(set, seed, days=days)`＝**loops 引数は既定のまま**）。
    ∴ `random_FS#0` のようなベンチの局名をそのまま検死できる。
    """
    from sim.sample_scripts import SAMPLE_SCRIPTS
    if name in SAMPLE_SCRIPTS:
        return SAMPLE_SCRIPTS[name]()
    if name.startswith("random_"):
        return random_script(name.split("_", 1)[1], seed, days=days)
    raise KeyError(f"ベンチに無い局名: {name}")


# ---------------------------------------------------------------------------
# 検死する「版」（B-141b で追加）
# ---------------------------------------------------------------------------
#: 既定＝B-140（脚本家側の `board_removal_kb_scope`）。`set_variant()` で
#: **主人公側の B-141 切替口**（`agents/heuristic_protagonist.py` のクラス属性）へ
#: 差し替えられる。scope=0.0 が A（＝既定・現行挙動）、1.0 が B（＝是正版）。
_HP_VARIANT: str | None = None
_HP_KEYS = ("B141_COOLER_VALUE_FUTURE_ONLY", "B141_COOLER_ALLPAST_ONLY",
            "B141B_UNLOCK_SAME_DAY", "B143_YIELD")

#: ★**import 時のクラス既定**を控える（B-144 で `B141B_UNLOCK_SAME_DAY` と `B143_YIELD` の
#  既定が反転したため、`_apply_hp(False)` が復元する `arena.b141_ab._DEFAULTS`
#  ＝「land 前の挙動」と、**出荷時のクラス既定が別物**になった）。
#  検死のあとは**クラス既定へ戻す**（`_DEFAULTS` を残すと、同一プロセスで後から走る
#  テストが「既定が OFF になっている」状態を見てしまう＝実際に pytest で起きた）。
_HP_CLASS_DEFAULTS = {k: getattr(HeuristicProtagonist, k) for k in _HP_KEYS}


def restore_hp_defaults() -> None:
    """主人公側の切替口を**出荷時のクラス既定**へ戻す（プロセス汚染の防止）。"""
    for k, v in _HP_CLASS_DEFAULTS.items():
        setattr(HeuristicProtagonist, k, v)


def set_variant(name: str | None) -> None:
    """検死対象の版を選ぶ。`None`/"b140"＝従来（脚本家側）、それ以外は
    `arena.b141_ab.CONFIGS` の版名（"a" "a0" "b" "ab" "a0b"）。"""
    global _HP_VARIANT
    if name in (None, "b140"):
        _HP_VARIANT = None
        return
    from arena.b141_ab import CONFIGS
    if name not in CONFIGS:
        raise KeyError(f"未知の版: {name}（既知＝{sorted(CONFIGS)}）")
    _HP_VARIANT = name


def _apply_hp(on: bool) -> dict:
    """主人公側の切替口を立てる／既定へ戻す。戻り＝実効値（印字用）。"""
    from arena.b141_ab import CONFIGS, _DEFAULTS
    for k, v in _DEFAULTS.items():
        setattr(HeuristicProtagonist, k, v)
    if on and _HP_VARIANT:
        for k, v in CONFIGS[_HP_VARIANT].items():
            setattr(HeuristicProtagonist, k, v)
    return {k: getattr(HeuristicProtagonist, k) for k in _HP_KEYS}


def run_one(script, seed: int, scope: float, loops: int = 8, probe: bool = True):
    """1対局を走らせる。

    - 既定の版（B-140）＝`scope` は `board_removal_kb_scope` の上書き値。
    - `set_variant("a0")` 等（B-141b）＝`scope` は 0.0/1.0 の A/B スイッチで、
      脚本家パラメータは**既定のまま**（`mm_params=None`）。

    戻り＝(state, log, hp, mm)。probe=False なら素のエージェント（同一性確認用）。
    """
    sc = replace(script, loops=loops)
    mm_params = None if _HP_VARIANT else {TOGGLE: scope}
    eff_hp = _apply_hp(bool(scope))
    if probe:
        mm = AnalysisProbeMastermind(seed, params=mm_params)
        hp = EstimateProbeProtagonist(seed)
    else:
        mm = HeuristicMastermind(seed, params=mm_params)
        hp = HeuristicProtagonist(seed)
    if _HP_VARIANT:
        print(f"    [切替口] 版={_HP_VARIANT} scope={scope} "
              f"／ 主人公の実効値={json.dumps(eff_hp)}（probe={probe}）", flush=True)
    else:
        print(f"    [切替口] MM_PARAMS['{TOGGLE}'] 既定={MM_PARAMS[TOGGLE]} "
              f"／ 実効={mm.p[TOGGLE]}（probe={probe}）", flush=True)
    try:
        state, log = run_game(sc, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        restore_hp_defaults()   # ★クラス既定へ戻す（プロセス汚染の防止）
    return state, log, hp, mm


def outcome_of(state, loops: int = 8) -> tuple[int, str]:
    """`arena.benchmark.loops_to_win` と同じ規約で (ループ数, 結末) を返す。"""
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def _moves(log) -> list[dict]:
    return [{"loop": e["loop"], "day": e["day"], "actor": e["actor"],
             "decision": e["decision"], "chosen": _jsonable(e["chosen"])}
            for e in log]


def _fmt_move(m: dict) -> str:
    c = m["chosen"]
    if "card" in c:
        body = f'{c["card"]}→{c.get("target")}'
    elif "action" in c:
        body = str(c["action"]) + (f'→{c["target"]}' if c.get("target") else "")
    else:
        body = str(c)
    return f'L{m["loop"]}D{m["day"]} {m["actor"]}/{m["decision"]}: {body}'


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------
def _header(args) -> object:
    print(f"[env] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}")
    set_variant(getattr(args, "variant", None))
    name = getattr(args, "script", None)
    if name:
        sc = build_bench_script(name, args.seed, args.days)
    else:
        name = f"random_{args.set_name}"
        sc = build_script(args.set_name, args.seed, args.days, args.script_loops)
    print(f"[script] {name} s{args.seed} days={sc.days_per_loop} "
          f"loops={sc.loops} 版={getattr(args, 'variant', None) or 'b140'}")
    print(f"  cast={list(sc.cast)}")
    print(f"  roles={sc.roles}")
    print(f"  incidents={[(i.day, i.name, i.culprit) for i in sc.incidents]}")
    return sc


def cmd_verify(args) -> int:
    """プローブ付きと素の対局が同じ結末になるか（＝挙動不変）を確認する。"""
    sc = _header(args)
    ok = True
    for scope in (0.0, 1.0):
        res = {}
        for probe in (False, True):
            state, _log, _hp, _mm = run_one(sc, args.seed, scope,
                                            loops=args.loops, probe=probe)
            res[probe] = (outcome_of(state, args.loops), dict(state.board_anyaku),
                          state.winner)
        same = res[False] == res[True]
        ok &= same
        print(f"  scope={scope}: 素={res[False]} / probe={res[True]} "
              f"→ {'一致' if same else '★不一致（プローブが挙動を変えている）'}")
    return 0 if ok else 1


def cmd_moves(args) -> int:
    """OFF/ON の棋譜を並べ、最初に分岐する手を特定する。"""
    sc = _header(args)
    runs = {}
    for scope in (0.0, 1.0):
        print(f"  --- scope={scope} ---")
        state, log, hp, mm = run_one(sc, args.seed, scope, loops=args.loops)
        runs[scope] = {"moves": _moves(log), "state": state,
                       "est": hp.est_records, "ana": mm.ana_records}
        print(f"    結末={outcome_of(state, args.loops)} 最終盤面={dict(state.board_anyaku)}")
    a, b = runs[0.0]["moves"], runs[1.0]["moves"]
    n = min(len(a), len(b))
    idx = next((i for i in range(n) if a[i] != b[i]), None)
    print("\n[分岐点]")
    if idx is None:
        print("  最初の差異なし（片方が長いだけ）" if len(a) != len(b) else "  棋譜完全一致")
    else:
        print(f"  手番 #{idx}（同一だったのは #0〜#{idx - 1}）")
        print(f"    OFF: {_fmt_move(a[idx])}")
        print(f"    ON : {_fmt_move(b[idx])}")
        lo, dy = a[idx]["loop"], a[idx]["day"]
        for scope, key in ((0.0, "OFF"), (1.0, "ON")):
            an = [r for r in runs[scope]["ana"]
                  if r["loop"] == lo and r["day"] == dy]
            if an:
                r = an[0]
                print(f"    [{key}] mm読み: goal_boards={r['goal_boards']} "
                      f"除去役={r['board_removal_boards']} locked={r['locked']} "
                      f"板={r['board_anyaku']}")
    print("\n[分岐点以降の棋譜（OFF ／ ON）]")
    start = 0 if idx is None else max(0, idx)
    for i in range(start, max(len(a), len(b))):
        la = _fmt_move(a[i]) if i < len(a) else "—"
        lb = _fmt_move(b[i]) if i < len(b) else "—"
        mark = "  " if la == lb else "≠ "
        print(f"  {mark}#{i:4d} | {la:<46s} | {lb}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({str(k): {"moves": v["moves"], "est": v["est"],
                                "ana": v["ana"],
                                "outcome": outcome_of(v["state"], args.loops),
                                "board": dict(v["state"].board_anyaku)}
                       for k, v in runs.items()}, f, ensure_ascii=False, indent=1)
        print(f"\n  → {args.json} に保存")
    return 0


def cmd_boards(args) -> int:
    """主人公の板推定を日ごと・席ごとに OFF/ON で並べる（本タスクの本題）。"""
    sc = _header(args)
    runs = {}
    for scope in (0.0, 1.0):
        print(f"  --- scope={scope} ---")
        state, log, hp, mm = run_one(sc, args.seed, scope, loops=args.loops)
        runs[scope] = {"est": hp.est_records, "state": state}
        print(f"    結末={outcome_of(state, args.loops)}")
    boards = sorted({b for r in runs[0.0]["est"] for b in (r["probs"] or {})}
                    | {b for r in runs[1.0]["est"] for b in (r["probs"] or {})})
    print("\n[板推定の比較] P=_board_defeat_probs / top=_b84_top_boards / "
          "odb=_observed_defeat_board / aim=_guess_defeat_board")
    print(f"  板の並び: {boards}")

    def key(r):
        return (r["loop"], r["day"], r["seat"], r["decision"])

    def rowtext(r):
        p = r["probs"] or {}
        ps = " ".join(f"{p.get(b, 0.0):.2f}" for b in boards)
        ba = r["board_anyaku"]
        bs = " ".join(str(ba.get(b, 0)) for b in boards)
        return (f"板[{bs}] P[{ps}] top={','.join(r['top_boards'] or []) or '-'} "
                f"odb={r['odb'] or '-'} aim={r['guess'] or '-'} "
                f"p_board_rules={r.get('p_board_rules')}")

    ea = {key(r): r for r in runs[0.0]["est"] if r["decision"] == args.decision}
    eb = {key(r): r for r in runs[1.0]["est"] if r["decision"] == args.decision}
    allk = sorted(set(ea) | set(eb))
    for k in allk:
        ra, rb = ea.get(k), eb.get(k)
        ta = rowtext(ra) if ra else "—"
        tb = rowtext(rb) if rb else "—"
        mark = "  " if ta == tb else "≠ "
        head = f'L{k[0]}D{k[1]} {k[2]}'
        print(f"  {mark}{head:<10s} OFF {ta}")
        print(f"    {'':<10s} ON  {tb}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({str(k): v["est"] for k, v in runs.items()},
                      f, ensure_ascii=False, indent=1)
        print(f"\n  → {args.json} に保存")
    return 0


def cmd_cf(args) -> int:
    """反実仮想（既存 `arena.counterfactual` を再利用）＝指定ループの応手を1手差し替える。

    `--force "L,D,card,target,kind[;...]"` を先頭から順に消費する
    （その日の主人公セットの**先頭の席**から順に固定＝`arena.counterfactual._mk_decider`）。
    ★注意＝相手は固定戦略の1体だけ＝「このmm相手には」の話であり詰みの証明ではない
    （`arena/counterfactual.py` 冒頭の健全性の注意と同じ）。
    """
    from arena.counterfactual import continue_loop
    from arena.postmortem import replay_with_snapshots
    sc = _header(args)
    params = None if _HP_VARIANT else {TOGGLE: args.scope}
    if _HP_VARIANT:
        eff = _apply_hp(bool(args.scope))
        print(f"    [切替口] 版={_HP_VARIANT} scope={args.scope} "
              f"／ 主人公の実効値={json.dumps(eff)}")
    else:
        print(f"    [切替口] MM_PARAMS['{TOGGLE}'] 既定={MM_PARAMS[TOGGLE]} "
              f"／ 反実仮想の実効={args.scope}")
    _st, _log, snaps = replay_with_snapshots(replace(sc, loops=args.loops),
                                             args.seed, loops=args.loops,
                                             mm_params=params)
    forced: dict = {}
    for spec in (args.force or "").split(";"):
        if not spec.strip():
            continue
        lo, dy, card, tgt, kind = [x.strip() for x in spec.split(",")]
        forced.setdefault((int(lo), int(dy)), []).append(
            {"card": card, "target": tgt, "target_kind": kind})
    print(f"    差し替え: {forced}")
    lo = args.loop
    snap = snaps.get((lo, 1))
    if snap is None:
        print(f"    L{lo}D1 のスナップショットが無い（そのループに到達していない）")
        return 1
    base = continue_loop(snap, args.seed, {}, mm_params=params)
    got = continue_loop(snap, args.seed, forced, mm_params=params)
    # ★B-141b：**素の再現が本物の対局と一致しているか**を毎回印字する（規約 §3
    #   「判定器は嘘をつかない」）。`continue_loop` は**新品のエージェント**で続きを
    #   打つため、AI が view から復元できない内部状態を持つ局では再現が外れうる。
    #   外れた局では差し替えの結果を因果の根拠に使ってはならない。
    from arena.postmortem import _lost_loops
    real_def = lo not in _lost_loops(_st)
    print(f"    L{lo} 本物の対局＝{'防衛' if real_def else '敗北'}")
    print(f"    L{lo} 素の再現（差し替えなし）＝{'防衛' if base else '敗北'}"
          f"　→ {'★再現一致' if base == real_def else '★★再現不一致（この局のCFは使えない）'}")
    print(f"    L{lo} 差し替え後　　　　　＝{'防衛' if got else '敗北'}")
    if args.show_moves or args.score_day:
        ok, st, hp = _cf_replay(snap, args.seed, forced, params)
        # ★正典は `counterfactual.continue_loop`。観測用の再現がずれたら黙らず落とす。
        assert ok == got, "観測用の再現が counterfactual.continue_loop と食い違った"
        if args.score_day:
            from agents.debug import format_records
            print(f"    --- 差し替え後 L{lo}D{args.score_day} の候補スコア ---")
            for line in format_records(hp.records, loop=lo, day=args.score_day,
                                       decision="set_card", top=6).splitlines():
                print("    " + line)
        if not args.show_moves:
            return 0
        print(f"    --- 差し替え後の L{lo}（観測用の再現・判定は上と一致を検証済み） ---")
        for e in st.history:
            if e.get("loop") != lo:
                continue
            if e.get("event") == "cards_revealed":
                print(f"      D{e.get('day')} 表返し: "
                      + " ／ ".join(f'{p.get("owner")}:{p.get("card")}→{p.get("target")}'
                                    for p in e.get("placements", [])))
            elif e.get("event") in ("incident", "death", "loop_end", "loop_board"):
                body = {k: v for k, v in e.items()
                        if k not in ("loop", "day", "phase", "event", "present",
                                     "present_unrest", "present_anyaku", "goodwill")}
                print(f"      D{e.get('day')} ▶ {e['event']}: {body}")
    return 0


def _cf_replay(snap, seed: int, forced: dict, params: dict):
    """`counterfactual.continue_loop` と同じ手順で回し、**state を返す**（観測用）。

    判定そのものは `continue_loop` を正典とし、呼び出し側で一致を assert する
    （本関数はドリフト検出つきの観測用ラッパ）。
    """
    from agents.debug import ProbedProtagonist
    from arena.counterfactual import _mk_decider
    from arena.postmortem import fast_copy
    from sim import flow
    from sim.state import PROTAGONIST_SEATS
    st = fast_copy(snap)
    mm = HeuristicMastermind(seed, params=params)
    hp = ProbedProtagonist(seed, top=40)   # 挙動同一（agents/debug.py の既存プローブ）
    agents = {"mastermind": mm, **{s: hp for s in PROTAGONIST_SEATS}}
    decide = _mk_decider(st, agents, forced)
    while True:
        flow.run_day(st, decide)
        if st.loop_end_triggered or st.day >= st.script.days_per_loop:
            break
        st.day += 1
    flow.evaluate_loop_end(st)
    return (not st.defeat), st, hp


def cmd_tally(args) -> int:
    """行為の数え上げ（並び順に依存しない証拠）＝札がどこへ振り替わったかをループ別に数える。

    - 脚本家：暗躍札／能力が**注目ボード**へ入った回数、不安+1 と能力が**注目キャラ**
      （＝事件の犯人）へ入った回数。
    - 主人公：1ループ1回の `不安-1` を「いつ・誰へ」使ったか（事件日に残っていたか）。
    """
    sc = _header(args)
    board, culprit = args.board, args.culprit or (
        sc.incidents[0].culprit if sc.incidents else None)
    inc_day = args.incident_day or (sc.incidents[0].day if sc.incidents else None)
    print(f"  注目: ボード={board} 犯人={culprit} 事件日=D{inc_day}")
    for scope in (0.0, 1.0):
        print(f"\n  === scope={scope} ===")
        state, log, _hp, _mm = run_one(sc, args.seed, scope, loops=args.loops)
        print(f"    結末={outcome_of(state, args.loops)}")
        mv = _moves(log)
        loops_seen = sorted({m["loop"] for m in mv})
        print(f"    L | mm暗躍→{board} | mm能力→{board} | mm不安+1→{culprit} "
              f"| mm能力→{culprit} | 主人公 不安-1 の使い先（D/席）")
        for lo in loops_seen:
            rows = [m for m in mv if m["loop"] == lo]
            anr = sum(1 for m in rows if m["actor"] == "mastermind"
                      and m["decision"] == "set_card"
                      and str(m["chosen"].get("card", "")).startswith("暗躍")
                      and m["chosen"].get("target") == board)
            ab_b = sum(1 for m in rows if m["actor"] == "mastermind"
                       and m["decision"] == "mastermind_ability"
                       and str(m["chosen"].get("target")) == board)
            unr = sum(1 for m in rows if m["actor"] == "mastermind"
                      and m["decision"] == "set_card"
                      and m["chosen"].get("card") == "不安+1"
                      and m["chosen"].get("target") == culprit)
            from collections import Counter as _C
            _abs = _C(str(m["chosen"].get("action")) for m in rows
                      if m["actor"] == "mastermind"
                      and m["decision"] == "mastermind_ability"
                      and str(m["chosen"].get("target")) == culprit)
            ab_c = "+".join(f"{k}×{v}" for k, v in sorted(_abs.items())) or "0"
            cool = [f'D{m["day"]}{m["actor"]}→{m["chosen"].get("target")}'
                    for m in rows if m["actor"] != "mastermind"
                    and m["chosen"].get("card") == "不安-1"]
            print(f"    {lo} | {anr:^13d} | {ab_b:^13d} | {unr:^16d} "
                  f"| {ab_c:<22s} | {' '.join(cool) or '—'}")
    return 0


def cmd_scores(args) -> int:
    """指定席の候補スコアを OFF/ON で並べる（`agents.debug` の既存プローブを再利用）。"""
    from agents.debug import ProbedMastermind, ProbedProtagonist, format_records
    sc = _header(args)
    for scope in (0.0, 1.0):
        eff = _apply_hp(bool(scope))
        mm = ProbedMastermind(args.seed, top=args.top,
                              params=None if _HP_VARIANT else {TOGGLE: scope})
        hp = ProbedProtagonist(args.seed, top=args.top)
        print(f"\n  === scope={scope} ===")
        if _HP_VARIANT:
            print(f"    [切替口] 版={_HP_VARIANT} ／ 主人公の実効値={json.dumps(eff)}")
        else:
            print(f"    [切替口] MM_PARAMS['{TOGGLE}'] 既定={MM_PARAMS[TOGGLE]} "
                  f"／ 実効={mm.p[TOGGLE]}")
        state, _log = run_game(replace(sc, loops=args.loops),
                               {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        print(f"    結末={outcome_of(state, args.loops)}（素の対局と一致すべき）")
        who = args.who
        recs = hp.records if who != "mm" else mm.records
        txt = format_records(recs, loop=args.loop, day=args.day,
                             decision=args.decision, top=args.top)
        for line in txt.splitlines():
            if who in ("mm", "all") or not args.seat or f" {args.seat}/" in line \
                    or line.startswith("  "):
                print("    " + line)
    restore_hp_defaults()
    return 0


def cmd_daily(args) -> int:
    """指定ループを日ごとに OFF/ON で並べる（手・その日の盤面/不安・その日の事件）。"""
    sc = _header(args)
    runs = {}
    for scope in (0.0, 1.0):
        print(f"  --- scope={scope} ---")
        state, log, hp, mm = run_one(sc, args.seed, scope, loops=args.loops)
        runs[scope] = {"moves": _moves(log), "log": log, "state": state}
        print(f"    結末={outcome_of(state, args.loops)}")

    def day_start_chars(log, loop, day):
        """その日の最初の決定の view から日開始のキャラ状態を取る（公開情報）。"""
        for e in log:
            if e["loop"] == loop and e["day"] == day:
                return {c["name"]: c for c in e["view"]["characters"]}, \
                    dict(e["view"].get("board_anyaku", {}))
        return None, None

    watch = args.watch.split(",") if args.watch else []
    for loop in args.loops_show:
        print(f"\n===== L{loop} =====")
        for day in range(1, sc.days_per_loop + 1):
            print(f"  -- D{day} --")
            for scope, tag in ((0.0, "OFF"), (1.0, "ON ")):
                chars, ba = day_start_chars(runs[scope]["log"], loop, day)
                if chars is None:
                    print(f"    {tag} （この日は無い＝ループが既に終了）")
                    continue
                w = "  ".join(
                    f"{n}[{chars[n]['area']}不{chars[n]['unrest']}"
                    f"好{chars[n]['goodwill']}暗{chars[n]['anyaku']}"
                    f"{'' if chars[n]['alive'] else '†'}]"
                    for n in watch if n in chars)
                print(f"    {tag} 板={ba} {w}")
                ms = [m for m in runs[scope]["moves"]
                      if m["loop"] == loop and m["day"] == day]
                for m in ms:
                    print(f"        {_fmt_move(m)}")
                for e in runs[scope]["state"].history:
                    if e.get("loop") == loop and e.get("day") == day and \
                            e.get("event") in ("incident", "death", "loop_end",
                                               "loop_board", "loop_result"):
                        body = {k: v for k, v in e.items()
                                if k not in ("loop", "day", "phase", "event",
                                             "present", "present_unrest",
                                             "present_anyaku", "goodwill")}
                        print(f"        ▶ {e['event']}: {body}")
    return 0


def cmd_cool(args) -> int:
    """★B-141b：冷却の照準まわり（`_incident_danger` / `_culprit_cands` /
    `_known_culprits` / 不安）を席ごとに OFF/ON で並べる。"""
    sc = _header(args)
    runs = {}
    for scope in (0.0, 1.0):
        print(f"  --- scope={scope} ---")
        state, _log, hp, _mm = run_one(sc, args.seed, scope, loops=args.loops)
        runs[scope] = hp.est_records
        print(f"    結末={outcome_of(state, args.loops)}")

    def key(r):
        return (r["loop"], r["day"], r["seat"], r["decision"])

    def txt(r):
        return (f'danger={r["incident_danger"]} 候補={r["culprit_cands"]} '
                f'確定={r["known_culprits"]} 不安={r["unrest"]} '
                f'選択={r["chosen"].get("card")}→{r["chosen"].get("target")}')

    ea = {key(r): r for r in runs[0.0]
          if r["decision"] == "set_card"
          and (args.loop is None or r["loop"] == args.loop)
          and (args.day is None or r["day"] == args.day)}
    eb = {key(r): r for r in runs[1.0]
          if r["decision"] == "set_card"
          and (args.loop is None or r["loop"] == args.loop)
          and (args.day is None or r["day"] == args.day)}
    for k in sorted(set(ea) | set(eb)):
        ta = txt(ea[k]) if k in ea else "—"
        tb = txt(eb[k]) if k in eb else "—"
        mark = "  " if ta == tb else "≠ "
        print(f'  {mark}L{k[0]}D{k[1]} {k[2]}')
        print(f"      OFF {ta}")
        print(f"      ON  {tb}")
    return 0


def cmd_endings(args) -> int:
    """各ループの終わり方（公開 loop_end / 秘匿 defeat / 板の最終値）を並べる。"""
    sc = _header(args)
    for scope in (0.0, 1.0):
        print(f"\n  === scope={scope} ===")
        state, _log, _hp, _mm = run_one(sc, args.seed, scope, loops=args.loops)
        print(f"    結末={outcome_of(state, args.loops)} winner={state.winner}")
        for e in state.history:
            if e.get("event") in ("loop_end", "loop_board", "loop_result",
                                  "death", "incident", "final_battle"):
                if e.get("event") == "incident" and not e.get("occurs"):
                    continue
                body = {k: v for k, v in e.items()
                        if k not in ("loop", "day", "phase", "event")}
                print(f"    公開 L{e.get('loop')}D{e.get('day')} "
                      f"{e.get('event')}: {body}")
        for e in state.secret_log:
            if e.get("event") in ("defeat", "loop_end"):
                print(f"    秘匿 L{e.get('loop')}D{e.get('day')} {e.get('event')}: "
                      f"{e.get('reason')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-140fu：板推定の A/B 検死（計測のみ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, doc in (("verify", cmd_verify, "プローブの挙動不変を確認"),
                          ("moves", cmd_moves, "棋譜の分岐点"),
                          ("boards", cmd_boards, "板推定の OFF/ON 比較"),
                          ("daily", cmd_daily, "指定ループの日ごと比較"),
                          ("scores", cmd_scores, "指定席の候補スコア比較"),
                          ("cool", cmd_cool, "冷却の照準（危険度・犯人候補・不安）の比較"),
                          ("tally", cmd_tally, "行為の数え上げ（札の振り替え先）"),
                          ("cf", cmd_cf, "反実仮想（応手の差し替え）"),
                          ("endings", cmd_endings, "ループの終わり方")):
        p = sub.add_parser(name, help=doc)
        p.add_argument("--set", dest="set_name", choices=("FS", "BTX"), default="FS")
        p.add_argument("--script", type=str, default=None,
                       help="ベンチの局名（例 btx5_seal / random_FS）。"
                            "指定するとベンチと同一の脚本を作る")
        p.add_argument("--variant", type=str, default=None,
                       help="検死する版。既定=b140（脚本家側）／"
                            "a・a0・b・ab・a0b＝B-141 の主人公側切替口")
        p.add_argument("--seed", type=int, default=3)
        p.add_argument("--days", type=int, default=5)
        p.add_argument("--loops", type=int, default=8)
        p.add_argument("--script-loops", type=int, default=None)
        p.add_argument("--json", type=str, default=None)
        if name == "boards":
            p.add_argument("--decision", type=str, default="set_card")
        if name == "cool":
            p.add_argument("--loop", type=int, default=None)
            p.add_argument("--day", type=int, default=None)
        if name == "cf":
            p.add_argument("--scope", type=float, default=1.0)
            p.add_argument("--loop", type=int, default=4)
            p.add_argument("--force", type=str, default="",
                           help='"L,D,カード,対象,種別[;...]"（種別＝character/board）')
            p.add_argument("--show-moves", action="store_true",
                           help="差し替え後のそのループの表返し・事件を印字する")
            p.add_argument("--score-day", type=int, default=None,
                           help="差し替え後の指定日の候補スコアを印字する")
        if name == "tally":
            p.add_argument("--board", type=str, default="病院")
            p.add_argument("--culprit", type=str, default=None)
            p.add_argument("--incident-day", type=int, default=None)
        if name == "scores":
            p.add_argument("--loop", type=int, default=4)
            p.add_argument("--day", type=int, default=None)
            p.add_argument("--decision", type=str, default="set_card")
            p.add_argument("--seat", type=str, default=None)
            p.add_argument("--who", type=str, default="hp",
                           choices=("hp", "mm", "all"))
            p.add_argument("--top", type=int, default=8)
        if name == "daily":
            p.add_argument("--loops-show", type=int, nargs="+", default=[3, 4],
                           help="日ごとに並べるループ番号")
            p.add_argument("--watch", type=str, default=None,
                           help="状態を印字するキャラ名（カンマ区切り）")
        p.set_defaults(func=fn)
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください（再現が壊れます）。")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
