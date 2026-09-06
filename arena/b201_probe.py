# -*- coding: utf-8 -*-
"""B-201 Phase 0：教材棋譜（鈴蘭 BTX3d seed0 ラバーズ連鎖・build 05f3355）で
**要衝拒否**（`暗躍禁止→刑事` ／ `暗躍禁止→男子学生`）が選ばれない理由を採点表で確定する
読み取り専用プローブ。

★agents/ の判断経路・既定値には一切触れない（読むだけ）。

サブコマンド:
    python -m arena.b201_probe why  [--log PATH]
        # 主人公の全決定を素で再生（bit 一致確認）し、**mm が要衝キャラに札を伏せた日**の
        #   - 上位候補の採点／`暗躍禁止→{刑事,男子学生}` の採点
        #   - belief の役職周辺確率（メインラバーズ／ラバーズ）
        #   - プランナー脅威（mainlover_*／remote_murder_vip）の実在度・折り手
        #   - B-100 強制割当の介入ログ
    python -m arena.b201_probe alloc [--log PATH]
        # `B176_SETUP_KINDS` から mainlover_chain を外した場合の資格判定の差分（読み取り専用）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.debug import ProbedProtagonist

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_ラバーズ連鎖_2026-08-11.jsonl"
#: 教材の要衝＝(i) 遠隔殺人の的にされるラバーズ (ii) メインラバーズの発動要件
CHOKE = ("刑事", "男子学生")


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return lines[0], [d for d in lines if d.get("type") == "decision"]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _mm_chars(view: dict) -> list[str]:
    return sorted({p["target"] for p in view.get("placements", [])
                   if p.get("owner") == "mastermind"
                   and p.get("target_kind") == "character"})


def _threats_for(hp, view: dict, options: list[dict]):
    """プランナー脅威をその view で素で再列挙（読み取り専用・意思決定に影響しない）。"""
    from agents.defense_plan import plan_for_belief
    try:
        threats, plan = plan_for_belief(view, hp._belief, options)
    except Exception as e:      # 観測専用＝落ちても本体の結論を壊さない
        print(f"      [脅威] 再列挙に失敗: {e!r}")
        return [], None
    return threats, plan


def cmd_why(args) -> int:
    meta, decisions = _load(args.log)
    print(f"===== B-201 Phase 0  {Path(args.log).name}  (build {meta.get('tool_build')}) =====")
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
        mmc = _mm_chars(view)
        hot = [c for c in CHOKE if c in mmc]
        rec = hp.records[-1]
        mark = "" if same else f"  ≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        flag = f"  ★要衝に mm 札あり: {hot}" if hot else ""
        print(f"--- L{d['loop']}D{d['day']} {view.get('seat')}: "
              f"選択={_fmt(_clean(chosen))}{mark}{flag}")
        print(f"      mm札(キャラ)={mmc}  "
              f"mm札(板)={sorted({p['target'] for p in view.get('placements', []) if p.get('owner') == 'mastermind' and p.get('target_kind') == 'board'})}")
        for s, o in rec["scored"][:4]:
            print(f"      TOP  {s:8.2f} {_fmt(o)}")
        for s, o in rec["scored"]:
            if o.get("card") == "暗躍禁止" and o.get("target_kind") == "character" \
                    and o.get("target") in CHOKE:
                print(f"      ★禁止 {s:8.2f} {_fmt(o)}")
        if not (args.all or hot):
            continue
        # --- belief の役職周辺確率
        try:
            rm = hp._belief.role_marginals()
        except Exception:
            rm = {}
        for r in ("メインラバーズ", "ラバーズ"):
            row = {n: round(dist.get(r, 0.0), 3) for n, dist in rm.items()
                   if dist.get(r, 0.0) > 0.01}
            row = dict(sorted(row.items(), key=lambda x: -x[1]))
            print(f"      P({r})={json.dumps(row, ensure_ascii=False)}")
        # --- 該当キャラの状態
        for n in CHOKE:
            c = hp._alive(view, n)
            print(f"      {n}: {'生存' if c else '死亡/不在'} "
                  f"{'暗躍=%d 不安=%d エリア=%s' % (c['anyaku'], c['unrest'], c['area']) if c else ''}")
        # --- プランナー脅威
        threats, _plan = _threats_for(hp, view, options)
        for t in threats:
            if t.kind not in ("mainlover_chain", "mainlover_protagonist",
                              "remote_murder_vip", "killer_protagonist"):
                continue
            brks = [f"{b.card}→{b.target}(cost{b.cost}{'/robust' if b.robust else ''})"
                    for cond in t.conditions for b in cond.breaks]
            print(f"      [脅威] {t.kind} p={t.prob:.3f} due={getattr(t, 'due_day', None)} "
                  f"『{t.label}』 折り手={brks}")
        # --- B-127 述語
        from agents.defense_plan import remote_murder_pin_live
        for n in CHOKE:
            print(f"      _rm_target_now({n}) = "
                  f"{remote_murder_pin_live(view, n, frozenset(mmc))}  "
                  f"／ _b127_target_pin_ok = {hp._b127_target_pin_ok(view, n, set(mmc))}  "
                  f"／ VIP集合入り = {n in (getattr(hp, '_friend_guards', set()) | getattr(hp, '_fatal_guards', set()))}")
        # --- B-100 強制割当
        log = [r for r in hp._b100_log
               if r.get("loop") == view.get("loop") and r.get("day") == view.get("day")]
        if log:
            for r in log[-1:]:
                print(f"      [B-100] {json.dumps(r, ensure_ascii=False)[:400]}")
    print(f"[probe] 主人公決定の bit 一致 = {n_same}/{n_all}")
    return 0


def cmd_alloc(args) -> int:
    """`B176_SETUP_KINDS` から mainlover_chain を外したら資格が付くか（読み取り専用）。"""
    from agents import b100_alloc
    from agents.defense_plan import plan_for_belief
    meta, decisions = _load(args.log)
    hp = ProbedProtagonist(0, top=400)
    print("===== B-201 Phase 0 / B-176 除外の影響（読み取り専用） =====")
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        view, options = d["view"], d["options"]
        hp.decide(view, d["decision"], options)
        if d["decision"] != "set_card":
            continue
        threats, _plan = plan_for_belief(view, hp._belief, options)
        for t in threats:
            if t.kind != "mainlover_chain":
                continue
            brks = [(b.card, b.target) for cond in t.conditions for b in cond.breaks]
            gate_now = b100_alloc.gate_reason(
                {"threat": t, "repeat": 0, "top": False, "n_supply": None},
                theta=hp.B100_THETA if hasattr(hp, "B100_THETA") else 0.9,
                iron_prob=None, force_gate="theta",
                tonight_prob=hp.B176_TONIGHT_PROB, today=view.get("day"))
            old = b100_alloc.B176_SETUP_KINDS
            b100_alloc.B176_SETUP_KINDS = frozenset({"sk_setup"})
            try:
                gate_off = b100_alloc.gate_reason(
                    {"threat": t, "repeat": 0, "top": False, "n_supply": None},
                    theta=hp.B100_THETA if hasattr(hp, "B100_THETA") else 0.9,
                    iron_prob=None, force_gate="theta",
                    tonight_prob=hp.B176_TONIGHT_PROB, today=view.get("day"))
            finally:
                b100_alloc.B176_SETUP_KINDS = old
            print(f"L{d['loop']}D{d['day']} {view.get('seat')}: mainlover_chain "
                  f"p={t.prob:.3f} due={getattr(t, 'due_day', None)} 折り手={brks}\n"
                  f"    資格(現行)={gate_now}  資格(除外を外す)={gate_off}")
    return 0


# ---------------------------------------------------------------------------
# 単局再現（`replay`）＝教材の脚本家（人間）の手を**そのまま再生**して、
#   主人公AIだけを差し替えた 1 対局を最後まで回す。
#   ★人間 mm の手が尽きた（＝主人公が守って局面が分岐した）以降は AI 脚本家に委ねる。
# ---------------------------------------------------------------------------
class _ReplayMastermind:
    """棋譜の脚本家決定を (loop, day, decision) の出現順で再生する脚本家。

    棋譜に無い決定（主人公が守って局面が分岐した後）は AI 脚本家へフォールバックする
    ＝「人間の手が尽きるまでは完全再生」という単局再現の意味を保つ。
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
    from sim import run_game
    from sim.state import script_from_dict
    from agents.heuristic_protagonist import HeuristicProtagonist
    import agents.defense_plan as _dp

    meta, decisions = _load(args.log)
    script = script_from_dict(meta["script"])
    for on in (False, True):
        _dp.B201_MAINLOVER_ANYAKU_PIN = on
        hp = HeuristicProtagonist(0)
        hp.B201_MAINLOVER_PIN = on
        mm = _ReplayMastermind(decisions, 0)
        state, _log = run_game(script, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        print(f"[B-201 {'ON ' if on else 'OFF'}] winner={state.winner} "
              f"loops_played={state.loop_no} 最終日={state.day} "
              f"主人公生存={not getattr(state, 'defeat', False)} "
              f"（人間手の再生={mm.n_replayed} / AI委譲={mm.n_fallback}）")
    _dp.B201_MAINLOVER_ANYAKU_PIN = False
    return 0


def cmd_count(args) -> int:
    """★行為の数え上げ（規約§11b の最強証拠）＝ベンチ全局で述語が何席で True になるか。

    どの条件で落ちたかも同時に数える（不発火の説明責任）。判定は本体の述語を通す
    （書き写さない）＝観測のためのラッパで前置条件だけを別途数える。
    """
    import agents.defense_plan as dp
    import agents.heuristic_protagonist as hpm
    from arena.benchmark import benchmark_scripts, loops_to_win

    dp.B201_MAINLOVER_ANYAKU_PIN = True
    hpm.HeuristicProtagonist.B201_MAINLOVER_PIN = True
    inner = dp.mainlover_anyaku_pin_live
    stat = {"評価回数": 0, "1_mm札なし": 0, "2_暗躍≥1": 0, "3_実在度<θ": 0,
            "4_遠隔殺人なし": 0, "4_ラバーズ疑い不在": 0, "★発火": 0}
    hits: dict = {}
    cur = {"n": None}

    def counting(view, name, mm_chars, roles, day_now=None):
        stat["評価回数"] += 1
        r = inner(view, name, mm_chars, roles, day_now)
        if r:
            stat["★発火"] += 1
            hits[cur["n"]] = hits.get(cur["n"], 0) + 1
            return r
        c = dp._char(view, name)
        d0 = view.get("day", 1) if day_now is None else day_now
        if name not in mm_chars:
            stat["1_mm札なし"] += 1
        elif c and c.get("anyaku", 0) != 0:
            stat["2_暗躍≥1"] += 1
        elif not any(i.get("name") == "遠隔殺人" and i.get("day") is not None
                     and i.get("day") >= d0 for i in view.get("incidents", []) or []):
            stat["4_遠隔殺人なし"] += 1
        elif not [n for n in dp._suspects(roles, "ラバーズ")
                  if n != name and dp._alive(view, n)]:
            stat["4_ラバーズ疑い不在"] += 1
        else:
            stat["3_実在度<θ"] += 1
        return r

    dp.mainlover_anyaku_pin_live = counting
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            cur["n"] = f"{name}#{seed}"
            loops_to_win(sc, seed, loops=8)
    finally:
        dp.mainlover_anyaku_pin_live = inner
        dp.B201_MAINLOVER_ANYAKU_PIN = False
        hpm.HeuristicProtagonist.B201_MAINLOVER_PIN = False
    print(f"days={args.days}  " + json.dumps(stat, ensure_ascii=False))
    print("  発火局: " + (json.dumps(hits, ensure_ascii=False) if hits else "なし"))
    return 0


def _slot_stats(placements_by_turn) -> dict:
    """`暗躍禁止` の**単一スロット**の使われ方を数える（神視点＝mm の札の中身も見る）。

    - `自滅`＝1ターンに主人公が2枚以上の `暗躍禁止` を出した回数
      （`rules/10_action_cards.md:25,61`＝**暗躍禁止自体が無効化される**／
       実装＝`engine/resolver.py:331-336` が複製前の実カード枚数で数える）。
    - `空打ち`＝置いた対象へ mm が**その日 暗躍+ を置いていない**（＝1枚も打ち消せない）回数。
      ★これは**神視点でしか判定できない**（主人公に見えるのは伏せ札の位置だけ）＝
      「AI が避けられたはずの無駄打ち」ではなく「釣りが成立した回数」の指標。
    - `未使用`＝主人公が誰も `暗躍禁止` を出さなかったターン。
    """
    st = {"ターン数": 0, "使用ターン": 0, "未使用ターン": 0, "★自滅": 0,
          "空打ち": 0, "命中": 0}
    for pls in placements_by_turn:
        st["ターン数"] += 1
        kin = [p for p in pls if p.get("owner") != "mastermind"
               and p.get("card") == "暗躍禁止"]
        if not kin:
            st["未使用ターン"] += 1
            continue
        st["使用ターン"] += 1
        if len(kin) >= 2:
            st["★自滅"] += 1
        mm_anyaku = {(p.get("target"), p.get("target_kind"))
                     for p in pls if p.get("owner") == "mastermind"
                     and str(p.get("card", "")).startswith("暗躍+")}
        for k in kin:
            if (k.get("target"), k.get("target_kind")) in mm_anyaku:
                st["命中"] += 1
            else:
                st["空打ち"] += 1
    return st


def cmd_slots(args) -> int:
    """★暗躍禁止の単一スロット監査＝教材2局とベンチ全局で `自滅／空打ち／命中` を数える。"""
    from arena.benchmark import benchmark_scripts
    from agents import HeuristicMastermind, HeuristicProtagonist
    from dataclasses import replace
    from sim import run_game

    for p in (LOG, LOG.parent / "鈴蘭_BTX3d_seed0_爆弾X発動後_2026-08-11.jsonl"):
        if not p.exists():
            continue
        meta, _dec = _load(p)
        turns = [e["placements"] for e in meta.get("history", [])
                 if e.get("event") == "cards_revealed"]
        print(f"[教材] {p.name}: " + json.dumps(_slot_stats(turns), ensure_ascii=False))

    agg = {}
    for name, seed, sc in benchmark_scripts(days=args.days):
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _log = run_game(replace(sc, loops=8),
                               {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        turns = [e["placements"] for e in state.history
                 if e.get("event") == "cards_revealed"]
        for k, v in _slot_stats(turns).items():
            agg[k] = agg.get(k, 0) + v
    print(f"[ベンチ days={args.days}] " + json.dumps(agg, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-201 Phase 0：要衝拒否の不発火プローブ")
    ap.add_argument("cmd", choices=["why", "alloc", "replay", "count", "slots"])
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--days", type=int, default=3, help="count 用（3 or 5）")
    ap.add_argument("--all", action="store_true", help="全ターンで詳細を出す")
    a = ap.parse_args(argv)
    return {"why": cmd_why, "alloc": cmd_alloc, "replay": cmd_replay,
            "count": cmd_count, "slots": cmd_slots}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
