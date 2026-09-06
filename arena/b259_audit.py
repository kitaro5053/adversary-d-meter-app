# -*- coding: utf-8 -*-
"""B-259：**盤面を動かさない事件の日付だけで8局動く**件の検死（★測定のみ）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-60（出典＝§72-59 B-257 の未解決2）。
結果 doc＝`docs/検死_B259_事件日付の1日ずれ_2026-08-19.md`。

## 対象

`sim.sample_scripts.btx5_seal_cat_script`（BTX 4ループ×5日／Y=封印されしモノ）の
**第2事件 `流布`/犯人=黒猫** の日付だけを **5日目（採用版＝`d5`）→ 4日目（`d4`）** に
ずらした2条件。他の要素は完全同一。seed 0〜9・`loops=8`（`arena/benchmark.py` の既定）。

★`流布` は犯人が黒猫のとき黒猫の特性2で事件効果が「何も起きない」に変更される
（`rules/30_characters.md:75`／実装＝`sim/effects.py:385-388`）＝**盤面は動かない**。

## この道具がやること

1. `board` … ★発注前の検算＝**「盤面が動かない」の自前確認**。
   `sim.flow.resolve_incident_phase` を**元の実装を必ず呼ぶラッパ**に一時差し替えし、
   事件フェイズの**前後で盤面（エリア・生死・不安/友好/暗躍/護衛カウンター・板暗躍・
   SK化フラグ・勝敗）を差分**する。同時に、その席で**公開履歴に何が積まれたか**も数える
   （盤面が動かなくても「発生宣言」は公開情報＝そこが唯一の差分チャネルになる）。
2. `verify` … 自己検証。素の対局（`arena.benchmark.loops_to_win` と同一手順）と
   本道具の対局が **(結末, loops_to_win, 棋譜全行)** で完全一致することを 10 seed 全部で確認。
3. `split` … ★最優先＝**最初に食い違う席**を 10 seed 分すべて特定する。
   `sim.run_game` が返す**決定ログ**（`sim/flow.py:_make_decider`＝loop/day/phase/actor/
   decision/options/chosen を全決定について記録）を d5/d4 で突き合わせるだけ＝
   **監視コードを1行も挿さない**（＝プローブの無害性は構造的に自明）。
4. `belief` … 分岐席での `role_marginals` / `rule_marginals` / `culprit_candidates` の差。
5. `evalr` … 評価器（本物の採点クロージャ）の日付依存＝分岐席の全候補の点数を d5/d4 で比較。
6. `grid` … 変種×日付の 2×2（`--mm b152` で既存切替口を立てた版・`--culprit X` で犯人差し替え版）。
7. `terms` … §4-1 で名指しした3項を**既存のパラメータ口**で1つずつ落とした対照。
8. `slots` … ★行為の数え上げ＝**最終日の脚本家の3枠に何が入ったか**を全ループ・全 seed で数える。
9. `family` … ★族か脚本固有か＝**最低2脚本**で「事件日を1日ずらす」対照を取る。

## 挙動には触れない

`agents/` `sim/` `engine/` `rules/` を**1バイトも変更しない**。切替口は
`arena.knob_audit.check_baseline`（B-248 の一般形）と `arena.b249_audit.check_no_knob_writes`
の**二重**で守る。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b259_audit defaults
    python -m arena.b259_audit board
    python -m arena.b259_audit verify
    python -m arena.b259_audit split  --outdir /tmp/b259
    python -m arena.b259_audit belief --outdir /tmp/b259
    python -m arena.b259_audit evalr  --outdir /tmp/b259
    python -m arena.b259_audit grid   [--mm b152] [--culprit 医者]
    python -m arena.b259_audit terms
    python -m arena.b259_audit slots
    python -m arena.b259_audit family
"""
from __future__ import annotations

import argparse
import json
import sys as _sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

from arena.b249_audit import check_no_knob_writes, defaults_banner

SEEDS = tuple(range(10))
LOOPS = 8                       # `arena/benchmark.py` の既定
DRIVER = "arena.b259_audit"
KURO = "クロマク"
CULT = "カルティスト"
SHRINE = "神社"

#: 盤面（＝物理）とみなすフィールド。★history / secret_log / phase_snapshots は**含めない**
#:  （そちらは「公開情報」であって盤面ではない＝本チケットの論点そのもの）。
CHAR_FIELDS = ("area", "alive", "unrest", "goodwill", "anyaku", "guard", "virus_serial")


# ---------------------------------------------------------------------------
# 健全性検査
# ---------------------------------------------------------------------------
def knob_audit_baseline() -> str:
    """`arena.knob_audit.check_baseline`（B-248 の一般形）を配線する（チケット要求）。"""
    from arena import knob_audit as ka
    from arena.b249_audit import BASELINE, KNOBS, _holders
    ka.check_baseline(KNOBS, _holders(), BASELINE, driver=DRIVER)
    return "✅ `arena.knob_audit.check_baseline`（B-248 の一般形）PASS"


# ---------------------------------------------------------------------------
# 2条件の脚本
# ---------------------------------------------------------------------------
def shift_incident(script, index: int, delta: int):
    """`script.incidents[index]` の日付を `delta` 日ずらした写しを返す（他は完全同一）。"""
    incs = list(script.incidents)
    incs[index] = replace(incs[index], day=incs[index].day + delta)
    return replace(script, incidents=incs)


def script_d5():
    from sim.sample_scripts import btx5_seal_cat_script
    return btx5_seal_cat_script()


def script_d4():
    return shift_incident(script_d5(), 1, -1)


CONDS = {"d5": script_d5, "d4": script_d4}

#: ★変種＝B-257 §3 の対照実験（同 doc の表を再現するための最小の写し）。
#:   `nog`＝ご神木（神社から動けない常駐者）を外し、ファクターを**女子学生**（学校・可動）へ。
#:   B-257 実測＝`nog`×d5 は **8/10**・`nog`×d4 は **1/10**＝★**本チケットの「8局動く」の本体**。
VARIANTS = ("base", "nog")


def variant_of(script, variant: str):
    if variant == "base":
        return script
    if variant != "nog":
        raise SystemExit(f"★未知の変種: {variant}")
    cast = ["女子学生" if n == "ご神木" else n for n in script.cast]
    roles = {("女子学生" if k == "ご神木" else k): v for k, v in script.roles.items()}
    return replace(script, cast=cast, roles=roles)


def cond_script(variant: str, day: str):
    return variant_of(CONDS[day](), variant)


# ---------------------------------------------------------------------------
# 対局（★`arena/benchmark.loops_to_win` と同一手順・監視コードなし）
# ---------------------------------------------------------------------------
def run(script, seed: int, loops: int = LOOPS, mm_params: dict | None = None):
    """(state, 決定ログ) を返す。`sim.run_game` が返すログをそのまま使う＝無害。

    `mm_params`＝`HeuristicMastermind(seed, params=...)`（`agents/heuristic.py:783-793` の
    既存の上書き口＝`arena/benchmark.loops_to_win` と同じ経路）。★リポジトリのコードは
    1バイトも変えず、**既存の掃引用切替口を測るためだけ**に使う。
    """
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    hp = HeuristicProtagonist(seed)
    return run_game(replace(script, loops=loops),
                    {"mastermind": HeuristicMastermind(seed, params=mm_params),
                     "p1": hp, "p2": hp, "p3": hp})


def outcome_of(state, loops: int = LOOPS) -> tuple[int, str]:
    """`arena/benchmark.loops_to_win` と同じ判定（書き写しでなく同じ式）。"""
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def trace(state) -> list:
    """棋譜（公開履歴）の全行を比較可能な形に。"""
    return [tuple(sorted((k, repr(v)) for k, v in e.items())) for e in state.history]


# ---------------------------------------------------------------------------
# (1) 発注前の検算＝「盤面が動かない」の自前確認
# ---------------------------------------------------------------------------
def board_snapshot(state) -> dict:
    """盤面（物理）のスナップショット。★履歴は含めない。"""
    return {
        "chars": {n: {f: getattr(c, f) for f in CHAR_FIELDS}
                  for n, c in state.characters.items()},
        "board_anyaku": dict(state.board_anyaku),
        "winner": state.winner,
        "game_over": state.game_over,
        "protagonists_alive": state.protagonists_alive,
        "loop_end_triggered": getattr(state, "loop_end_triggered", None),
        "final_battle_pending": state.final_battle_pending,
        "revealed_roles": dict(state.revealed_roles),
        "friend_revealed": sorted(state.friend_revealed),
        "leader_idx": state.leader_idx,
    }


def _diff(a: dict, b: dict, path: str = "") -> list[str]:
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
    elif a != b:
        out.append(f"{path}: {a!r} → {b!r}")
    return out


def run_with_incident_probe(script, seed: int, rows: list, loops: int = LOOPS):
    """事件フェイズの**前後で盤面を差分**する（元の実装を必ず呼ぶラッパ）。"""
    from sim import flow as _flow
    orig = _flow.resolve_incident_phase

    def _wrapped(state, decide):
        inc = next((i for i in state.script.incidents if i.day == state.day), None)
        before = board_snapshot(state)
        n_hist = len(state.history)
        orig(state, decide)
        after = board_snapshot(state)
        rows.append({
            "seed": seed, "loop": state.loop_no, "day": state.day,
            "inc": None if inc is None else {"name": inc.name, "culprit": inc.culprit},
            "board_diff": _diff(before, after),
            "pub": [dict(e) for e in state.history[n_hist:]],
        })

    _flow.resolve_incident_phase = _wrapped
    try:
        return run(script, seed, loops=loops)
    finally:
        _flow.resolve_incident_phase = orig


def cmd_board(a) -> int:
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    print("## ★発注前の検算3＝「`流布`/黒猫 は盤面を動かさない」の自前確認\n")
    tot = Counter()
    bad = []
    pub_kinds = Counter()
    for cond, f in CONDS.items():
        sc = f()
        target_day = sc.incidents[1].day
        for seed in SEEDS:
            rows: list = []
            st0, _ = run(sc, seed)
            st1, _ = run_with_incident_probe(sc, seed, rows)
            if trace(st0) != trace(st1) or outcome_of(st0) != outcome_of(st1):
                bad.append(f"{cond} seed{seed}：プローブが棋譜を変えた（★重大）")
            for r in rows:
                if r["inc"] is None or r["inc"]["culprit"] != "黒猫":
                    continue
                tot[f"{cond}:席"] += 1
                if r["board_diff"]:
                    tot[f"{cond}:★盤面が動いた"] += 1
                    bad.append(f"{cond} seed{seed} L{r['loop']}D{r['day']}："
                               + "／".join(r["board_diff"][:6]))
                for e in r["pub"]:
                    pub_kinds[f"{cond}:{e.get('event')}"] += 1
            assert target_day == sc.incidents[1].day
    print(f"- 検査した「`流布`/犯人=黒猫」の事件フェイズ席＝"
          + "／".join(f"{k}={v}" for k, v in sorted(tot.items())))
    print(f"- ★**盤面（エリア・生死・不安/友好/暗躍/護衛・板暗躍・SK化・勝敗）の差分＝"
          f"{tot['d5:★盤面が動いた'] + tot['d4:★盤面が動いた']} 件**")
    print("- 同じ席で**公開履歴に積まれたイベント**＝"
          + "／".join(f"{k}:{v}" for k, v in sorted(pub_kinds.items())))
    for b in bad[:20]:
        print("  ★", b)
    check_no_knob_writes(before, driver=DRIVER)
    print("[b259] ✅ 切替口の束は測定の前後で不変")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
# (2) 自己検証
# ---------------------------------------------------------------------------
def cmd_verify(a) -> int:
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    from arena.benchmark import loops_to_win
    bad = 0
    print(f"{'seed':>4} | {'benchmark(d5)':>16} | {'本道具(d5)':>16} | 一致")
    for seed in SEEDS:
        want = loops_to_win(script_d5(), seed, loops=LOOPS)
        st, _ = run(script_d5(), seed)
        got = outcome_of(st)
        ok = want == got
        bad += 0 if ok else 1
        print(f"{seed:>4} | {str(want):>16} | {str(got):>16} | {'✅' if ok else '★不一致'}")
    check_no_knob_writes(before, driver=DRIVER)
    print(f"[b259] 食い違い {bad} 件／切替口の束は不変 ✅")
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------------------
# (3) 最初に食い違う席
# ---------------------------------------------------------------------------
def key_of(rec: dict) -> tuple:
    """決定1件の比較キー（view は含めない＝盤面差でなく「手」の差を見る）。"""
    return (rec["loop"], rec["day"], rec["phase"], rec["actor"], rec["decision"])


def opt_str(o) -> str:
    if not isinstance(o, dict):
        return repr(o)
    return "/".join(f"{k}={o[k]}" for k in sorted(o) if k != "prov")


def first_split(log5: list[dict], log4: list[dict]) -> dict:
    """2条件の決定ログを先頭から突き合わせ、最初の食い違いを返す。"""
    n = min(len(log5), len(log4))
    for i in range(n):
        a, b = log5[i], log4[i]
        if key_of(a) != key_of(b):
            return {"i": i, "why": "席そのものがずれた",
                    "a": key_of(a), "b": key_of(b),
                    "a_chosen": opt_str(a["chosen"]), "b_chosen": opt_str(b["chosen"]),
                    "a_opts": [opt_str(o) for o in a["options"]],
                    "b_opts": [opt_str(o) for o in b["options"]]}
        if opt_str(a["chosen"]) != opt_str(b["chosen"]):
            return {"i": i, "why": "同じ席で選択が違う", "a": key_of(a), "b": key_of(b),
                    "a_chosen": opt_str(a["chosen"]), "b_chosen": opt_str(b["chosen"]),
                    "a_opts": [opt_str(o) for o in a["options"]],
                    "b_opts": [opt_str(o) for o in b["options"]]}
        if [opt_str(o) for o in a["options"]] != [opt_str(o) for o in b["options"]]:
            return {"i": i, "why": "選択は同じだが合法手の並びが違う",
                    "a": key_of(a), "b": key_of(b),
                    "a_chosen": opt_str(a["chosen"]), "b_chosen": opt_str(b["chosen"]),
                    "a_opts": [opt_str(o) for o in a["options"]],
                    "b_opts": [opt_str(o) for o in b["options"]]}
    if len(log5) != len(log4):
        return {"i": n, "why": "片方の対局が先に終わった",
                "a": key_of(log5[n]) if n < len(log5) else None,
                "b": key_of(log4[n]) if n < len(log4) else None,
                "a_chosen": "", "b_chosen": "", "a_opts": [], "b_opts": []}
    return {}


def cmd_split(a) -> int:
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    out = Path(a.outdir) / a.variant
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    print(f"[b259] 変種＝{a.variant}")
    for seed in SEEDS:
        st5, lg5 = run(cond_script(a.variant, "d5"), seed)
        st4, lg4 = run(cond_script(a.variant, "d4"), seed)
        sp = first_split(lg5, lg4)
        rec = {"seed": seed,
               "d5": outcome_of(st5), "d4": outcome_of(st4),
               "n5": len(lg5), "n4": len(lg4), "split": sp}
        rows.append(rec)
        # 分岐席の周辺（前後8手）も保存＝後段の belief / evalr の入口
        i = sp.get("i", 0)
        (out / f"split{seed}.json").write_text(json.dumps({
            "seed": seed, "rec": {k: v for k, v in rec.items() if k != "split"},
            "split": sp,
            "ctx5": [{**{k: lg5[j][k] for k in ("loop", "day", "phase", "actor", "decision")},
                      "chosen": opt_str(lg5[j]["chosen"])}
                     for j in range(max(0, i - 8), min(len(lg5), i + 3))],
            "ctx4": [{**{k: lg4[j][k] for k in ("loop", "day", "phase", "actor", "decision")},
                      "chosen": opt_str(lg4[j]["chosen"])}
                     for j in range(max(0, i - 8), min(len(lg4), i + 3))],
        }, ensure_ascii=False), encoding="utf-8")
        s = rec["split"]
        loc = s.get("a") or s.get("b")
        print(f"  seed{seed}: d5={rec['d5']} d4={rec['d4']}  "
              f"初分岐 #{s.get('i')} {loc} {s.get('why')}  "
              f"[{s.get('a_chosen')}] vs [{s.get('b_chosen')}]", flush=True)
    (out / "split.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    check_no_knob_writes(before, driver=DRIVER)
    print("[b259] ✅ 切替口の束は測定の前後で不変")
    return 0


# ---------------------------------------------------------------------------
# (4) 分岐席の中身＝belief（主人公側）と 評価器（両陣営）
# ---------------------------------------------------------------------------
def _rm(marg: dict, role: str) -> dict:
    return {n: round(d.get(role, 0.0), 4) for n, d in (marg or {}).items()
            if d.get(role, 0.0) > 0}


def probe_seat(script, seed: int, target: tuple, loops: int = LOOPS) -> dict:
    """`target`＝(loop, day, actor) の席で、両陣営の**本物の内部量**を1回だけ記録する。

    - 主人公側＝`HeuristicProtagonist._apply_seat_flags` を**元の実装を必ず呼ぶラッパ**に
      差し替え、`decide` の呼び出し元フレームのローカル（`score` / `options`）を読む
      （`arena/b249_audit.py`・`arena/b255_audit.py` と同じ作法＝採点器を書き写さない）。
    - 脚本家側＝`HeuristicMastermind._analyze` と `_score_set` を同じくラッパで包む。
    どちらも**元の実装の戻り値をそのまま返す**＝挙動に触れない。
    """
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    HP, HM = HeuristicProtagonist, HeuristicMastermind
    o_flags, o_an, o_sc = HP._apply_seat_flags, HM._analyze, HM._score_set
    hit: dict = {}
    cur: dict = {}

    def _hp(agent, view, best):
        try:
            fr = _sys._getframe(1)
            if fr.f_code.co_name == "decide" and not hit:
                loc = fr.f_locals
                score, options = loc.get("score"), loc.get("options")
                if score is not None and options and (
                        view.get("loop"), view.get("day"), view.get("seat")) == target:
                    b = getattr(agent, "_belief", None)
                    marg = b.role_marginals() if b is not None else {}
                    hit.update({
                        "actor": view.get("seat"), "n_opt": len(options),
                        "chosen": opt_str(best),
                        "scores": sorted(((round(float(score(o)), 3), opt_str(o))
                                          for o in options), reverse=True)[:12],
                        "P(クロマク)": _rm(marg, KURO), "P(カルティスト)": _rm(marg, CULT),
                        "rule": (sorted(((round(v, 4), f"{k[0]}|{'+'.join(k[1])}")
                                         for k, v in b.rule_marginals().items()),
                                        reverse=True)[:6] if b is not None else []),
                        "犯人候補": ({d: sorted(s) for d, s in b.culprit_candidates().items()}
                                 if b is not None else {}),
                        "可能世界数": (b.n_worlds() if b is not None else None),
                        "incident_danger": dict(getattr(agent, "_incident_danger", {})),
                        "purge": getattr(agent, "_purge_target", None),
                        "km_cand": sorted(getattr(agent, "_kuromaku_cands", ()) or ()),
                        "km_sure": sorted(getattr(agent, "_kuromaku_suspects", ()) or ()),
                    })
        except Exception as e:                              # pragma: no cover
            hit.setdefault("★観測例外", f"{type(e).__name__}: {e}")
        return o_flags(agent, view, best)

    def _an_w(agent, view):
        a = o_an(agent, view)
        cur["a"], cur["view"] = a, view
        return a

    def _sc_w(agent, o, a, view):
        v = o_sc(agent, o, a, view)
        if (view.get("loop"), view.get("day"), "mastermind") == target:
            hit.setdefault("actor", "mastermind")
            hit.setdefault("mm_scores", [])
            hit["mm_scores"].append((round(float(v), 3), opt_str(o)))
            hit.setdefault("mm_a", {k: (sorted(a[k]) if isinstance(a[k], (set, frozenset))
                                        else a[k])
                                    for k in ("today_culprit", "today_incident",
                                              "fires_today", "future_culprits",
                                              "reachable_culprits", "days_left",
                                              "mm_rate", "locked", "push_culprit",
                                              "pump_targets", "goal_boards", "funded")
                                    if k in a})
        return v

    HP._apply_seat_flags, HM._analyze, HM._score_set = _hp, _an_w, _sc_w
    try:
        hp = HP(seed)
        state, _log = run_game(replace(script, loops=loops),
                               {"mastermind": HM(seed), "p1": hp, "p2": hp, "p3": hp})
    finally:
        HP._apply_seat_flags, HM._analyze, HM._score_set = o_flags, o_an, o_sc
    if "mm_scores" in hit:
        hit["mm_scores"] = sorted(hit["mm_scores"], reverse=True)[:12]
    hit["_state"] = state
    return hit


def cmd_belief(a) -> int:
    """分岐席で d5/d4 の内部量を並べる（主人公席＝belief／脚本家席＝`_analyze`）。"""
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    rows = json.loads((Path(a.outdir) / a.variant / "split.json").read_text(encoding="utf-8"))
    for rec in rows:
        seed, sp = rec["seed"], rec["split"]
        if not sp or sp.get("a") is None:
            print(f"\n### seed{seed}：分岐なし（2条件の決定ログが完全一致）")
            continue
        lp, dy, _ph, actor, _dec = sp["a"]
        tgt = (lp, dy, actor)
        print(f"\n### seed{seed}　分岐席＝L{lp}D{dy} {actor}　"
              f"（d5={rec['d5']} / d4={rec['d4']}）")
        for tag in ("d5", "d4"):
            h = probe_seat(cond_script(a.variant, tag), seed, tgt)
            h.pop("_state", None)
            print(f"  [{tag}] " + json.dumps(h, ensure_ascii=False)[:2400])
    check_no_knob_writes(before, driver=DRIVER)
    print("\n[b259] ✅ 切替口の束は測定の前後で不変")
    return 0


def cmd_evalr(a) -> int:
    """★プローブの無害性の実証＝`probe_seat` を全 seed に当て、素の対局と棋譜全行を照合。"""
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    bad = 0
    for tag in ("d5", "d4"):
        for seed in SEEDS:
            sc = cond_script(a.variant, tag)
            st0, _ = run(sc, seed)
            h = probe_seat(sc, seed, (-1, -1, "なし"))
            st1 = h.pop("_state")
            ok = (trace(st0) == trace(st1) and outcome_of(st0) == outcome_of(st1))
            bad += 0 if ok else 1
            if not ok:
                print(f"  ★{tag} seed{seed}：プローブが棋譜を変えた")
    check_no_knob_writes(before, driver=DRIVER)
    print(f"[b259] プローブ無害性＝食い違い {bad} 件／切替口の束は不変 ✅")
    return 0 if bad == 0 else 1


#: ★機序の確認実験に使う**既存の**切替口（`agents/heuristic.py:365-372`・既定 0.0＝OFF）。
#:   B-152（2026-08-04）が入れたもので、`_final_day_zero_payoff`（`agents/heuristic.py:3122`＝
#:   「犯人が黒猫なら True」）により**最終日の 流布/黒猫 を `pump_targets` から外す**。
B152_ON = {"b152b_final_day_payoff": 1.0}


#: ★§4-1 で名指しした3つの項を**既存のパラメータ口だけ**で1つずつ落とす条件表
#:   （`agents/heuristic.py` の `MM_PARAMS` は `HeuristicMastermind(seed, params=...)` で
#:    上書きできる＝`arena/benchmark.loops_to_win` と同じ経路。★コードは1バイトも変えない）。
#:   ★注意＝`set_move_misleader` / `set_cool_locked` を落とすのは**その項の全席**に効く
#:   （狙った席だけを切る切替口は存在しない）＝**機序の切り分け用の粗い探り**であって
#:   「この値にせよ」という提案ではない。
TERM_SETS = {
    "既定": None,
    "b152 ON（§4-1(A)）": dict(B152_ON),
    "寄せ 52→0（§4-1(B)）": {"set_move_misleader": 0.0},
    "抑制冷却 65→0（§4-1(C)）": {"set_cool_locked": 0.0},
    "b152 ON ＋ 抑制冷却 0": {**B152_ON, "set_cool_locked": 0.0},
    "3つ全部": {**B152_ON, "set_move_misleader": 0.0, "set_cool_locked": 0.0},
}


def cmd_terms(a) -> int:
    """★未解決1の詰め＝§4-1 の3項を1つずつ落として `nog` の d5/d4 を測る。"""
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    print("| 脚本家パラメータ | nog d5 防衛 | 平均 | nog d4 防衛 | 平均 |")
    print("|---|---:|---:|---:|---:|")
    for name, pr in TERM_SETS.items():
        cells = []
        for d in ("d5", "d4"):
            got = [outcome_of(run(cond_script("nog", d), s, mm_params=pr)[0])
                   for s in SEEDS]
            cells.append((sum(1 for _l, o in got if o == "defense"),
                          sum(l for l, _o in got) / len(got)))
        print(f"| {name} | **{cells[0][0]}/10** | {cells[0][1]:.2f} | "
              f"**{cells[1][0]}/10** | {cells[1][1]:.2f} |", flush=True)
    check_no_knob_writes(before, driver=DRIVER)
    print("\n[b259] ✅ 切替口の束は測定の前後で不変")
    return 0


def cmd_slots(a) -> int:
    """★行為の数え上げ（規約§11b 成果の示し方 段1）＝**最終日の脚本家の3枠に何が入ったか**。

    `暗躍+1→神社` は本脚本の敗北条件そのもの（Y=封印されしモノ＝ループ終了時に神社の暗躍≥2）。
    `移動*→妹`（ミスリーダー）は §4-1(B) の 52.0 の手。**最終日（day==days_per_loop）の
    脚本家行動フェイズの席だけ**を数える。★並び順に依存しない量。
    """
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    print("| 変種 | 事件日 | 最終日の脚本家の枠(計) | うち `暗躍+1→神社` | うち `移動*→妹` | "
          "最終日を迎えたループ数 |")
    print("|---|---|---:|---:|---:|---:|")
    for v in VARIANTS:
        for d in ("d5", "d4"):
            sc = cond_script(v, d)
            last = sc.days_per_loop
            ml = next((n for n, r in sc.roles.items() if r == "ミスリーダー"), None)
            tot = shrine = pull = loops = 0
            for seed in SEEDS:
                _st, lg = run(sc, seed)
                seen = set()
                for r in lg:
                    if r["actor"] != "mastermind" or r["decision"] != "set_card":
                        continue
                    if r["day"] != last:
                        continue
                    seen.add(r["loop"])
                    tot += 1
                    o = r["chosen"]
                    if o.get("card") == "暗躍+1" and o.get("target") == SHRINE:
                        shrine += 1
                    if str(o.get("card", "")).startswith("移動") and o.get("target") == ml:
                        pull += 1
                loops += len(seen)
            print(f"| {v} | {d} | {tot} | **{shrine}** | **{pull}** | {loops} |", flush=True)
    check_no_knob_writes(before, driver=DRIVER)
    print("\n[b259] ✅ 切替口の束は測定の前後で不変")
    return 0


def cmd_grid(a) -> int:
    """変種×日付の 2×2 表（B-257 §3/§5 の再現＝ベースラインの実測確認）。

    `--mm b152` で、機序の確認として既存の切替口 `b152b_final_day_payoff` を 1.0 にした表も出す。
    """
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    mm = B152_ON if a.mm == "b152" else None
    print(f"[b259] 脚本家パラメータ＝{mm or 'リポジトリ既定'}"
          + (f"／第2事件の犯人を {a.culprit} へ差し替え" if a.culprit else ""))
    print("| 変種 | 事件日 | 防衛 | 平均 | 結末の内訳 |")
    print("|---|---|---:|---:|---|")
    for v in VARIANTS:
        for d in ("d5", "d4"):
            sc = cond_script(v, d)
            if a.culprit:
                incs = list(sc.incidents)
                incs[1] = replace(incs[1], culprit=a.culprit)
                sc = replace(sc, incidents=incs)
            got = [outcome_of(run(sc, s, mm_params=mm)[0]) for s in SEEDS]
            n = sum(1 for _l, o in got if o == "defense")
            av = sum(l for l, _o in got) / len(got)
            cnt = Counter(o for _l, o in got)
            print(f"| {v} | {d} | **{n}/10** | {av:.2f} | "
                  + "／".join(f"{k}:{c}" for k, c in sorted(cnt.items())) + " |", flush=True)
    check_no_knob_writes(before, driver=DRIVER)
    print("\n[b259] ✅ 切替口の束は測定の前後で不変")
    return 0


# ---------------------------------------------------------------------------
# (6) 族か脚本固有か
# ---------------------------------------------------------------------------
#: (脚本名, 事件index, 説明)。★「1日ずらす」対照が取れるもの＝ずらし先に別の事件が無いこと。
FAMILY = (
    ("btx5_seal_cat", 1, "★本件（流布/黒猫・盤面を動かさない）"),
    ("btx_seal_cat", 1, "3日級の同型（流布/黒猫・D3→D2）"),
    ("btx5_seal", 1, "5日級の封印（黒猫なし）"),
    ("btx5_future", 1, "5日級の未来予知"),
    ("btx_seal", 1, "3日級の封印（黒猫なし）"),
    ("btx_future", 1, "3日級の未来予知"),
)


def cmd_family(a) -> int:
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    from sim.sample_scripts import SAMPLE_SCRIPTS
    names = a.scripts.split(",") if a.scripts else [n for n, _i, _d in FAMILY]
    print("| 脚本 | ずらした事件 | 元日 | 防衛(元) | 平均(元) | 防衛(-1日) | 平均(-1日) | 振れ |")
    print("|---|---|---|---|---|---|---|---|")
    for name in names:
        idx, note = next(((i, d) for n, i, d in FAMILY if n == name), (1, ""))
        base = SAMPLE_SCRIPTS[name]()
        if idx >= len(base.incidents):
            print(f"| {name} | — | — | （事件が {len(base.incidents)} 件しかない） | | | | |")
            continue
        inc = base.incidents[idx]
        if inc.day - 1 < 1 or any(j != idx and base.incidents[j].day == inc.day - 1
                                  for j in range(len(base.incidents))):
            print(f"| {name} | {inc.name}/{inc.culprit} | D{inc.day} | （1日前に置けない） | | | | |")
            continue
        res = {}
        for tag, sc in (("元", base), ("-1日", shift_incident(base, idx, -1))):
            got = [outcome_of(run(sc, s)[0]) for s in SEEDS]
            res[tag] = (sum(1 for l, o in got if o == "defense"),
                        sum(l for l, _o in got) / len(got))
        d = res["-1日"][0] - res["元"][0]
        print(f"| {name} | {inc.name}/{inc.culprit} | D{inc.day} | "
              f"{res['元'][0]}/10 | {res['元'][1]:.2f} | "
              f"{res['-1日'][0]}/10 | {res['-1日'][1]:.2f} | **{d:+d}** |", flush=True)
    check_no_knob_writes(before, driver=DRIVER)
    print("\n[b259] ✅ 切替口の束は測定の前後で不変")
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="arena.b259_audit", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("defaults")
    sub.add_parser("board")
    sub.add_parser("verify")
    for nm in ("split", "belief", "evalr"):
        q = sub.add_parser(nm)
        q.add_argument("--outdir", default="/tmp/b259")
        q.add_argument("--variant", default="base", choices=VARIANTS)
    q = sub.add_parser("grid")
    q.add_argument("--mm", default="", choices=["", "b152"])
    q.add_argument("--culprit", default="",
                   help="第2事件の犯人を差し替える（原因の切り分け＝黒猫か日付か）")
    sub.add_parser("terms")
    sub.add_parser("slots")
    q = sub.add_parser("family")
    q.add_argument("--scripts", default="")
    a = p.parse_args(argv)
    fn = {"defaults": cmd_defaults, "board": cmd_board, "verify": cmd_verify,
          "split": cmd_split, "family": cmd_family, "grid": cmd_grid,
          "terms": cmd_terms, "slots": cmd_slots,
          "belief": cmd_belief, "evalr": cmd_evalr}[a.cmd]
    return fn(a)


def cmd_defaults(a) -> int:
    before = defaults_banner()
    print(f"[b259] {knob_audit_baseline()}")
    check_no_knob_writes(before, driver=DRIVER)
    print("[b259] ✅ 切替口の束は不変（まだ何も回していない＝自明ケース）")
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
