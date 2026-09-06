# -*- coding: utf-8 -*-
"""B-261 計測：`b152b_final_day_payoff` の射程再評価（★測定専用・AI の既定は1バイトも変えない）。

出典＝`docs/バックログ_構想メモ_FableA.md` §72-64（起票）／§72-62（B-259 の検死）。

## 何を測るか（★事前登録＝測る前に定義を固定する）

**Z型犯人**＝「**不安臨界0** かつ **事件効果が構造的に『何も起きない』**」犯人。
- 不安臨界0＝`rules/30_characters.md:53`（黒猫の臨界欄 **0**）／`engine/data.CHARACTER_UNREST_THRESHOLD`。
- 事件効果なし＝`rules/30_characters.md:75`（黒猫 特性2＝自身が犯人の事件効果は「何も起きない」）。
- ★**KB 収録35体のうち臨界0は黒猫だけ**（`cmd_scope` が実行時に列挙して検算する）。
  臨界1の `手先` は「犯人が手先の事件を発生させない」を持つが、これは**友好能力**
  （`rules/20`／`sim/abilities.py:326`＝主人公が使って初めて効く）＝**構造的な無効ではない**
  ので Z型に含めない。

**Z席**＝「意味の無い寄せに最終日の枠を使っている席」の操作的定義：
最終日（`day == days_per_loop`）の**脚本家 set_card 席**のうち、
1. 既定の `_analyze` で `pump_targets` に Z型犯人が入っており、
2. 既存の切替口 `b152b_final_day_payoff` を 1.0 にした**同一盤面の反実仮想**では
   その犯人が `pump_targets` から落ち、
3. その結果 **選ばれる札が変わる** 席。

★3 を満たさない席（＝落としても同じ札を選ぶ）は「枠を使っていない」＝数えない。
★**押し出されている手**＝2 の反実仮想で代わりに選ばれる札。これも同時に数える
（§72-53 の教訓＝「無駄を止めた」だけでは強さの証拠にならない＝空いた枠の行き先を先に数える）。

### ★★`slots`（席ごとの反実仮想）の限界＝**B-215 の席順入れ替えを差分として拾う**

`agents/heuristic.py:770` `B215_DECOY_FIRST`（**既定 ON**）は、本命の板暗躍を**同じターンの
次の席へ回し**、1席目にダミーを置く（`_b215_decoy_swap`）。∴ 席ごとに argmax と比べると
「既定は 0.0 の札／反実仮想は 95.0 の板暗躍」という差が出るが、**同じターンの2席目で
本命は実際に置かれている**＝枠は失われていない。
∴ ★**枚数の結論には `turns`（OFF/ON の実対局でターン単位に数える）を使うこと**。
`slots` は「切替口がどの席の採点を動かすか」の所在確認までにとどめる。

## 無害性（★プローブは棋譜を1ビットも動かさない）

- 反実仮想は `HeuristicMastermind._analyze` / `_score_set` / `_plus2_penalty` を**追加で呼ぶだけ**。
  これらは `self.rng` を**一度も使わない**（乱数は `_pick` だけ＝`agents/heuristic.py:2092`）。
- 呼ぶのは**本物の decide が終わった後**＝乱数列・option 列・戻り値に触れない。
- `self.p["b152b_final_day_payoff"]` は反実仮想の間だけ差し替え、**必ず finally で戻す**。
- `verify` サブコマンドが「プローブ有り／無しで (結末, loops_to_win, 棋譜全行) が完全一致」を検査する。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit scope   # フェーズ0-1（構造）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit verify  # 無害性・自己検証
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit slots --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit ab --days 3 --vals 0,1
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit perm --days 3 --modes id,rev,h1
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit turns --days 3 --zonly
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b261_audit split --days 3 --games "btx_seal_cat#2"
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

KNOB = "b152b_final_day_payoff"
DRIVER = "arena.b261_audit"
LOOPS = 8


# ---------------------------------------------------------------------------
# 健全性検査（★ベースライン条件＝リポジトリ既定 を実行時に検査する）
# ---------------------------------------------------------------------------
def knob_audit_baseline() -> str:
    """`arena.knob_audit.check_baseline`（B-248 の一般形）を配線する（チケット要求）。"""
    from arena import knob_audit as ka
    from arena.b249_audit import BASELINE, KNOBS, _holders
    ka.check_baseline(KNOBS, _holders(), BASELINE, driver=DRIVER)
    return "✅ `arena.knob_audit.check_baseline`（B-248 の一般形）PASS"


def check_knob_default() -> str:
    """★`MM_PARAMS[KNOB]` が**リポジトリ既定 0.0**であることを実行時に検査する。

    `knob_audit` は bool の切替口しか見ない（`_flag_snapshot` は `isinstance(v, bool)`）。
    本チケットの主役は float の切替口なので、その分をここで補う。
    """
    from agents.heuristic import MM_PARAMS
    cur = MM_PARAMS[KNOB]
    if cur != 0.0:
        raise SystemExit(
            f"★ベースライン検査に失敗（{DRIVER}）＝`MM_PARAMS[{KNOB!r}]` の"
            f"リポジトリ既定は 0.0 のはずが {cur!r}。\n"
            "  → 本レーンは既定を変更しない（測定時に params= で渡すだけ）。")
    return f"✅ `MM_PARAMS[{KNOB!r}]` = 0.0（リポジトリ既定・本測定は書き換えない）"


def banner() -> dict:
    from arena.b249_audit import defaults_banner
    snap = defaults_banner()
    print(f"[b261] {knob_audit_baseline()}")
    print(f"[b261] {check_knob_default()}")
    return snap


def done(before: dict) -> None:
    from arena.b249_audit import check_no_knob_writes
    check_no_knob_writes(before, driver=DRIVER)
    check_knob_default()
    print("[b261] ✅ 切替口の束は測定の前後で不変（bool束＋" + KNOB + "）")


# ---------------------------------------------------------------------------
# Z型犯人の定義（★KB を実行時に読む＝道具にハードコードしない・B-258 申し送り）
# ---------------------------------------------------------------------------
def zero_threshold_chars() -> list[str]:
    """不安臨界0のキャラを KB 由来テーブルから列挙する（`rules/30` の転記＝`engine/data`）。"""
    from engine.data import CHARACTER_UNREST_THRESHOLD as T
    return sorted(n for n, v in T.items() if v == 0)


def null_effect_chars() -> list[str]:
    """事件効果が構造的に「何も起きない」になる犯人（黒猫 特性2＝`rules/30:75`）。

    ★実装の単一ソース＝`sim/effects.py` の黒猫分岐。ここは名前だけを持ち、
    `cmd_scope` が「実装にその分岐が実在すること」を検算する。
    """
    return ["黒猫"]


def z_culprits() -> list[str]:
    """Z型＝臨界0 ∧ 効果なし。"""
    zs = set(zero_threshold_chars()) & set(null_effect_chars())
    return sorted(zs)


# ---------------------------------------------------------------------------
# 対局（★`arena/benchmark.loops_to_win` と同一手順）
# ---------------------------------------------------------------------------
def run(script, seed: int, loops: int = LOOPS, mm_params: dict | None = None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    hp = HeuristicProtagonist(seed)
    return run_game(replace(script, loops=loops),
                    {"mastermind": HeuristicMastermind(seed, params=mm_params),
                     "p1": hp, "p2": hp, "p3": hp})


def outcome_of(state, loops: int = LOOPS) -> tuple[int, str]:
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


def trace(state) -> list:
    return [tuple(sorted((k, repr(v)) for k, v in e.items())) for e in state.history]


def opt_str(o: dict) -> str:
    if not isinstance(o, dict):
        return repr(o)
    c, t = o.get("card"), o.get("target")
    return f"{c}→{t}" if c else repr(sorted(o.items()))


# ---------------------------------------------------------------------------
# ★プローブ＝最終日の脚本家 set_card 席で「切替口 ON の反実仮想」を取る
# ---------------------------------------------------------------------------
def _score_fn(agent, a, view):
    """`decide` の `_sc`（`agents/heuristic.py:2106-2107`）と同じ式。"""
    def _sc(o):
        return agent._score_set(o, a, view) - agent._plus2_penalty(o, a, view)
    return _sc


def run_with_slot_probe(script, seed: int, sink: list, loops: int = LOOPS,
                        final_day_only: bool = True):
    """脚本家 set_card 席を記録する（★本物の decide の**後**にonly働く）。

    `final_day_only=False` にすると**全ての日**の席を見る。★切替口の述語は
    「**事件日**が最終日か」（`cd >= days_per_loop`）を見るのであって「**今日**が最終日か」
    ではない（`agents/heuristic.py:1012`）＝**最終日より前の席も動く**。
    """
    from agents.heuristic import HeuristicMastermind as HM
    orig = HM.decide

    def _decide(self, view, decision, options):
        best = orig(self, view, decision, options)
        if decision != "set_card":
            return best
        try:
            last = view.get("days_per_loop")
            if final_day_only and (last is None or view.get("day") != last):
                return best
            a_def = self._analyze(view)
            old = self.p[KNOB]
            try:
                self.p[KNOB] = 1.0
                a_on = self._analyze(view)
            finally:
                self.p[KNOB] = old
            pt_def = [tuple(x) for x in a_def.get("pump_targets", [])]
            pt_on = [tuple(x) for x in a_on.get("pump_targets", [])]
            dropped = [x for x in pt_def if x not in pt_on]
            if not dropped:
                return best
            sc_def, sc_on = _score_fn(self, a_def, view), _score_fn(self, a_on, view)
            rank_on = sorted(((float(sc_on(o)), i) for i, o in enumerate(options)),
                             reverse=True)
            top = rank_on[0]
            ties = sum(1 for v, _i in rank_on if v == top[0])
            alt = options[top[1]]
            rank_def = sorted(((float(sc_def(o)), i) for i, o in enumerate(options)),
                              reverse=True)
            ties_def = sum(1 for v, _i in rank_def if v == rank_def[0][0])
            sink.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "dropped": [list(x) for x in dropped],
                "chosen": opt_str(best), "chosen_score": round(float(sc_def(best)), 3),
                "chosen_score_on": round(float(sc_on(best)), 3),
                "alt": opt_str(alt), "alt_score": round(float(top[0]), 3),
                "alt_score_def": round(float(sc_def(alt)), 3),
                "alt_ties": ties, "chosen_ties": ties_def,
                "changed": opt_str(best) != opt_str(alt),
                "n_opt": len(options),
            })
        except Exception as e:                                  # pragma: no cover
            sink.append({"★観測例外": f"{type(e).__name__}: {e}"})
        return best

    HM.decide = _decide
    try:
        return run(script, seed, loops=loops)
    finally:
        HM.decide = orig


# ---------------------------------------------------------------------------
# コーパス
# ---------------------------------------------------------------------------
def corpus(days: int, only: str = ""):
    from arena.benchmark import benchmark_scripts
    rows = benchmark_scripts(days=days)
    if only:
        keep = set(only.split(","))
        rows = [r for r in rows if r[0] in keep]
    return rows


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------
def cmd_scope(a) -> int:
    """★フェーズ0-1（構造）＝コーパスに Z型がどれだけ在るか。対局は回さない。"""
    before = banner()
    zt, ne, zc = zero_threshold_chars(), null_effect_chars(), z_culprits()
    print(f"\n[b261] 不安臨界0のキャラ（KB 収録全体）＝{zt}")
    print(f"[b261] 事件効果が構造的に「何も起きない」犯人＝{ne}")
    print(f"[b261] ∴ Z型（臨界0 ∧ 効果なし）＝{zc}")
    src = Path("sim/effects.py").read_text(encoding="utf-8")
    print(f"[b261] 実装の検算＝`sim/effects.py` に黒猫の効果無効分岐がある: "
          f"{'黒猫' in src}")

    for days in (a.days,) if a.days else (3, 5):
        rows = corpus(days)
        n_z = n_zero_pay = 0
        by_script: Counter = Counter()
        by_script_zp: Counter = Counter()
        scripts = set()
        for name, seed, sc in rows:
            scripts.add(name)
            last = sc.days_per_loop
            hit = [i for i in sc.incidents if i.day >= last and i.culprit in zc]
            if hit:
                n_z += 1
                by_script[name] += 1
            # 参考＝切替口の実際の射程（`_final_day_zero_payoff` が True になる事件）
            from agents.heuristic import HeuristicMastermind as HM
            view = {"characters": [{"name": n} for n in sc.cast],
                    "rule_x": sc.rule_x, "rule_x2": getattr(sc, "rule_x2", None),
                    "rule_y": sc.rule_y}
            zp = [i for i in sc.incidents if i.day >= last
                  and HM._final_day_zero_payoff(i.name, i.culprit, view, sc.roles)]
            if zp:
                n_zero_pay += 1
                by_script_zp[name] += 1
        print(f"\n### {days}日級コーパス＝**{len(rows)}局**（脚本 {len(scripts)} 種）")
        print(f"- 最終日に **Z型犯人**の事件がある局＝**{n_z}局** … {dict(by_script)}")
        print(f"- 最終日に `_final_day_zero_payoff`=True の事件がある局（切替口の射程全体）"
              f"＝**{n_zero_pay}局** … {dict(by_script_zp)}")
    done(before)
    return 0


def cmd_verify(a) -> int:
    """★自己検証＝(1) プローブ無害性 (2) `arena.benchmark` との一致。"""
    before = banner()
    rows = corpus(a.days, a.script)
    if a.limit:
        rows = rows[:a.limit]
    from arena.benchmark import loops_to_win
    bad_probe = bad_bench = 0
    for name, seed, sc in rows:
        st0, _ = run(sc, seed)
        sink: list = []
        st1, _ = run_with_slot_probe(sc, seed, sink)
        if (outcome_of(st0), trace(st0)) != (outcome_of(st1), trace(st1)):
            bad_probe += 1
            print(f"  ★食い違い（プローブ）: {name} s{seed}")
        if [r for r in sink if "★観測例外" in r]:
            bad_probe += 1
            print(f"  ★観測例外: {name} s{seed} {sink[0]}")
        if loops_to_win(sc, seed, loops=LOOPS) != outcome_of(st0):
            bad_bench += 1
            print(f"  ★食い違い（benchmark）: {name} s{seed}")
    print(f"\n[b261] 検査 {len(rows)} 局 … プローブ食い違い **{bad_probe}件** ／ "
          f"benchmark 食い違い **{bad_bench}件**")
    done(before)
    return 0 if not (bad_probe or bad_bench) else 1


def cmd_slots(a) -> int:
    """★フェーズ0-2/3＝Z席を数え、**押し出されている手**を数える。"""
    before = banner()
    zc = set(z_culprits())
    rows = corpus(a.days, a.script)
    if a.limit:
        rows = rows[:a.limit]
    n_seat = n_changed = n_zseat = 0
    pairs: Counter = Counter()
    by_script: Counter = Counter()
    alt_cards: Counter = Counter()
    chosen_cards: Counter = Counter()
    games_with = set()
    detail = []
    for name, seed, sc in rows:
        sink: list = []
        run_with_slot_probe(sc, seed, sink, final_day_only=not a.anyday)
        for r in sink:
            if "★観測例外" in r:
                raise SystemExit(f"★観測例外: {name} s{seed} {r}")
            zdrop = [d for d in r["dropped"] if d[1] in zc]
            n_seat += 1
            if not r["changed"]:
                continue
            n_changed += 1
            if not zdrop:
                continue
            n_zseat += 1
            by_script[name] += 1
            games_with.add((name, seed))
            pairs[(r["chosen"], r["alt"])] += 1
            chosen_cards[r["chosen"]] += 1
            alt_cards[r["alt"]] += 1
            detail.append({"script": name, "seed": seed, **r})
    scope = "全日" if a.anyday else "最終日"
    print(f"\n### {a.days}日級（{len(rows)}局）／集計範囲＝**{scope}の脚本家 set_card 席**")
    print(f"- 切替口が `pump_targets` を削った席＝**{n_seat}席**")
    print(f"- そのうち**選ばれる札が変わる**席＝**{n_changed}席**")
    print(f"- そのうち削られた対象に **Z型犯人**を含む席（＝★Z席）＝**{n_zseat}席**"
          f"（{len(games_with)}局）… {dict(by_script)}")
    n_tie = sum(1 for r in detail if r["alt_ties"] > 1)
    print(f"- ★うち反実仮想側が**同点帯**（`alt_ties`>1＝列挙順が勝者を決める）＝{n_tie}席"
          "（＝押し出された手の同定は保証できない席）")
    print("\n| 既定で置いた札（＝無意味な寄せ） | 反実仮想で代わりに入る札（＝押し出されていた手） | 席数 |")
    print("|---|---|---:|")
    for (c, al), n in pairs.most_common():
        print(f"| {c} | {al} | {n} |")
    if a.out:
        Path(a.out).write_text(json.dumps(detail, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        print(f"\n[b261] 明細を書き出し: {a.out}")
    done(before)
    return 0


def _bench(days: int, val: float, perm: str = "id") -> dict:
    from arena.benchmark import run_benchmark
    from arena.tie_noise import install_perm, uninstall_perm
    mm = None if val == 0.0 else {KNOB: val}
    install_perm(perm)
    try:
        rep = run_benchmark(loops=LOOPS, days=days, verbose=False, mm_params=mm)
    finally:
        uninstall_perm()
    return {
        "defense": sum(1 for r in rep["rows"] if r["outcome"] == "defense"),
        "mean": rep["mean_loops_to_win"],
        "outcomes": rep["outcomes"],
        "rows": {f'{r["script"]}#{r["seed"]}': r["loops_to_win"] for r in rep["rows"]},
        "outc": {f'{r["script"]}#{r["seed"]}': r["outcome"] for r in rep["rows"]},
    }


def _flips(base: dict, other: dict) -> list[str]:
    """★per-game flip＝`loops_to_win` **と結末の両方**を見る。

    `fb_loss → fb_win` は `loops_to_win` が両方 `loops+1` で**同値のまま結末だけ動く**
    （規約§5＝`fb_win` は防衛に数えない）。行だけを見ると取りこぼす。
    """
    out = []
    for k in sorted(base["rows"]):
        b, o = (base["rows"][k], base["outc"][k]), (other["rows"][k], other["outc"][k])
        if b != o:
            out.append(f"{k}: {b[0]}({b[1]}) → {o[0]}({o[1]})")
    return out


def cmd_ab(a) -> int:
    """★A/B＝`b152b_final_day_payoff` の掃引（既定 0.0 と 1.0・中間値も可）。"""
    before = banner()
    vals = [float(x) for x in a.vals.split(",")]
    res: dict = {}
    for v in vals:
        res[v] = _bench(a.days, v, perm=a.perm)
        r = res[v]
        print(f"  [{a.days}日級 perm={a.perm}] {KNOB}={v}: 防衛={r['defense']} "
              f"平均={r['mean']:.3f} 結末={r['outcomes']}", flush=True)
    base = res[vals[0]]
    print(f"\n### {a.days}日級・掃引（ベースライン＝{KNOB}={vals[0]}＝リポジトリ既定）")
    print("| 値 | 防衛 | 平均 | flip 数 | 内訳 |")
    print("|---:|---:|---:|---:|---|")
    for v in vals:
        fl = _flips(base, res[v])
        print(f"| {v} | **{res[v]['defense']}** | {res[v]['mean']:.3f} | {len(fl)} | "
              + "／".join(f"{k}:{c}" for k, c in sorted(res[v]["outcomes"].items())) + " |")
    for v in vals[1:]:
        fl = _flips(base, res[v])
        print(f"\n#### {KNOB}={v} の per-game flip（{len(fl)}件）")
        for line in fl:
            print(f"- {line}")
    if a.out:
        Path(a.out).write_text(json.dumps({str(k): v for k, v in res.items()},
                                          ensure_ascii=False), encoding="utf-8")
    done(before)
    return 0


def cmd_perm(a) -> int:
    """★列挙順の頑健性（規約§11b 段2）＝複数の並べ替えで A/B の符号が揃うか。"""
    before = banner()
    vals = [float(x) for x in a.vals.split(",")]
    modes = a.modes.split(",")
    table: dict = defaultdict(dict)
    for m in modes:
        for v in vals:
            r = _bench(a.days, v, perm=m)
            table[m][v] = r
            print(f"  [{a.days}日級] perm={m:5s} {KNOB}={v}: 防衛={r['defense']} "
                  f"平均={r['mean']:.3f}", flush=True)
    print(f"\n### {a.days}日級・列挙順×切替口（防衛数／平均）")
    print("| perm | " + " | ".join(f"{KNOB}={v}" for v in vals) + " | 差(最後−最初) |")
    print("|---|" + "---:|" * (len(vals) + 1))
    for m in modes:
        cells = " | ".join(f"{table[m][v]['defense']} ({table[m][v]['mean']:.2f})"
                           for v in vals)
        d = table[m][vals[-1]]["defense"] - table[m][vals[0]]["defense"]
        print(f"| {m} | {cells} | **{d:+d}** |")
    print("\n#### per-game flip 数（各 perm でベースラインとの差）")
    for m in modes:
        for v in vals[1:]:
            fl = _flips(table[m][vals[0]], table[m][v])
            print(f"- perm={m} / {KNOB}={v}: **{len(fl)}件** … "
                  + "／".join(fl[:12]) + (" …" if len(fl) > 12 else ""))
    if a.out:
        Path(a.out).write_text(
            json.dumps({m: {str(v): r for v, r in d.items()} for m, d in table.items()},
                       ensure_ascii=False), encoding="utf-8")
    done(before)
    return 0


def cmd_split(a) -> int:
    """★flip した局の**初分岐席**を出す（規約§7 の機序説明義務の材料）。

    `sim.run_game` が返す決定ログ（`sim/flow.py` `_make_decider`）を OFF/ON で
    先頭から突き合わせ、最初に食い違う添字を取るだけ＝**監視コードを挿さない**。
    """
    before = banner()
    rows = {f"{n}#{s}": (n, s, sc) for n, s, sc in corpus(a.days)}
    for key in a.games.split(","):
        if key not in rows:
            print(f"  ★コーパスに無い局: {key}")
            continue
        name, seed, sc = rows[key]
        st0, lg0 = run(sc, seed)
        st1, lg1 = run(sc, seed, mm_params={KNOB: 1.0})
        n = min(len(lg0), len(lg1))
        idx = next((i for i in range(n) if _rec(lg0[i]) != _rec(lg1[i])), None)
        print(f"\n### {key}　OFF={outcome_of(st0)} ／ ON={outcome_of(st1)}"
              f"（決定ログ長 {len(lg0)} / {len(lg1)}）")
        if idx is None:
            print("- 分岐なし（前方一致）")
            continue
        r0, r1 = lg0[idx], lg1[idx]
        print(f"- 初分岐＝L{r0['loop']}D{r0['day']} {r0['phase']} / {r0['actor']} "
              f"/ {r0['decision']}（同席={_rec(r0)[:4] == _rec(r1)[:4]}）")
        print(f"  - OFF: {opt_str(r0.get('chosen'))}")
        print(f"  - ON : {opt_str(r1.get('chosen'))}")
    done(before)
    return 0


def _rec(r: dict) -> tuple:
    return (r.get("loop"), r.get("day"), r.get("phase"), r.get("actor"),
            r.get("decision"), opt_str(r.get("chosen")))


# ---------------------------------------------------------------------------
# ★行為の数え上げ（規約§11b 段1）＝OFF/ON の**実対局**で最終日の枠に何が入ったかを数える
# ---------------------------------------------------------------------------
#: 板暗躍の札（`sim/legal.py` の列挙名）。板の敗北条件（ルールY）を積む札。
ANYAKU_CARDS = ("暗躍+1", "暗躍+2")


def _turn_counts(script, seed: int, mm_params: dict | None = None) -> dict:
    """最終日の脚本家 set_card 席を数える（★反実仮想ではなく**実際に置いた札**）。

    ★B-215（`agents/heuristic.py:770` 既定 ON）は**同じターンの中で席順を入れ替える**ので、
    「席ごとの反実仮想」は入れ替えを差分として拾ってしまう。ターン単位の枚数は
    入れ替えの影響を受けない＝こちらが正しい数え上げ。
    """
    _st, lg = run(script, seed, mm_params=mm_params)
    last = script.days_per_loop
    ml = next((n for n, r in script.roles.items() if r == "ミスリーダー"), None)
    out = {"slots": 0, "turns": set(), "board_anyaku": 0, "pull_ml": 0,
           "by_board": Counter(), "cards": Counter()}
    per_turn: dict = {}
    for r in lg:
        if r["actor"] != "mastermind" or r["decision"] != "set_card":
            continue
        if r["day"] != last:
            continue
        o = r["chosen"]
        out["slots"] += 1
        out["turns"].add(r["loop"])
        card, tgt, kind = o.get("card"), o.get("target"), o.get("target_kind")
        out["cards"][f"{card}→{kind}"] += 1
        if card in ANYAKU_CARDS and kind == "board":
            out["board_anyaku"] += 1
            out["by_board"][tgt] += 1
        if str(card).startswith("移動") and kind == "character" and tgt == ml:
            out["pull_ml"] += 1
        t = per_turn.setdefault(r["loop"], {"anyaku": 0, "ml": 0})
        if card in ANYAKU_CARDS and kind == "board":
            t["anyaku"] += 1
        if str(card).startswith("移動") and kind == "character" and tgt == ml:
            t["ml"] += 1
    out["turns"] = len(out["turns"])
    # ★B-259 の機序（寄せが敗北条件の札を枠外へ押し出す）が起きたターン＝
    #   「寄せが入っていて、かつ板暗躍が1枚も入っていない」最終日ターン。
    out["ml_and_no_anyaku"] = sum(1 for t in per_turn.values()
                                  if t["ml"] and not t["anyaku"])
    out["ml_turns"] = sum(1 for t in per_turn.values() if t["ml"])
    out["no_anyaku_turns"] = sum(1 for t in per_turn.values() if not t["anyaku"])
    return out


def cmd_turns(a) -> int:
    """★フェーズ0-2/3 の本命＝OFF と ON の実対局で最終日の枠の中身を数える。"""
    before = banner()
    zc = set(z_culprits())
    rows = corpus(a.days, a.script)
    tot = {"off": Counter(), "on": Counter()}
    per: dict = {}
    for name, seed, sc in rows:
        last = sc.days_per_loop
        has_z = any(i.day >= last and i.culprit in zc for i in sc.incidents)
        if a.zonly and not has_z:
            continue
        for tag, mm in (("off", None), ("on", {KNOB: 1.0})):
            c = _turn_counts(sc, seed, mm)
            for k in ("slots", "turns", "board_anyaku", "pull_ml",
                      "ml_and_no_anyaku", "ml_turns", "no_anyaku_turns"):
                tot[tag][k] += c[k]
            per.setdefault((name, seed), {})[tag] = c
            for b, n in c["by_board"].items():
                tot[tag][f"板:{b}"] += n
    print(f"\n### {a.days}日級（対象 {len(per)}局"
          + ("／Z型を含む局のみ" if a.zonly else "／全局") + "）＝最終日の脚本家の枠の中身")
    print("| 条件 | 最終日ターン | 枠(計) | 板暗躍 | 内訳(板) | 移動*→ML | ★寄せ有り×板暗躍0のターン |")
    print("|---|---:|---:|---:|---|---:|---:|")
    for tag in ("off", "on"):
        t = tot[tag]
        boards = "／".join(f"{k[2:]}:{v}" for k, v in sorted(t.items())
                           if str(k).startswith("板:"))
        label = "既定(OFF)" if tag == "off" else f"{KNOB}=1.0"
        print(f"| {label} | {t['turns']} | {t['slots']} | **{t['board_anyaku']}** | "
              f"{boards} | **{t['pull_ml']}** | ★**{t['ml_and_no_anyaku']}**"
              f"（寄せ有りターン {t['ml_turns']}／板暗躍0のターン {t['no_anyaku_turns']}） |")
    print("\n#### 局ごと（板暗躍の枚数が動いた局だけ）")
    for (name, seed), d in sorted(per.items()):
        if d["off"]["board_anyaku"] != d["on"]["board_anyaku"] or \
                d["off"]["pull_ml"] != d["on"]["pull_ml"]:
            print(f"- {name}#{seed}: 板暗躍 {d['off']['board_anyaku']}→{d['on']['board_anyaku']}"
                  f"／移動*→ML {d['off']['pull_ml']}→{d['on']['pull_ml']}"
                  f"／最終日ターン {d['off']['turns']}→{d['on']['turns']}")
    done(before)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog=DRIVER, description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("scope"); q.add_argument("--days", type=int, default=0)
    q = sub.add_parser("verify")
    q.add_argument("--days", type=int, default=3); q.add_argument("--limit", type=int, default=20)
    q.add_argument("--script", default="")
    q = sub.add_parser("slots")
    q.add_argument("--days", type=int, default=3); q.add_argument("--limit", type=int, default=0)
    q.add_argument("--out", default=""); q.add_argument("--script", default="")
    q.add_argument("--anyday", action="store_true")
    q = sub.add_parser("ab")
    q.add_argument("--days", type=int, default=3); q.add_argument("--vals", default="0,1")
    q.add_argument("--perm", default="id"); q.add_argument("--out", default="")
    q = sub.add_parser("turns")
    q.add_argument("--days", type=int, default=3); q.add_argument("--script", default="")
    q.add_argument("--zonly", action="store_true")
    q = sub.add_parser("split")
    q.add_argument("--days", type=int, default=3); q.add_argument("--games", required=True)
    q = sub.add_parser("perm")
    q.add_argument("--days", type=int, default=3); q.add_argument("--vals", default="0,1")
    q.add_argument("--modes", default="id,rev,h1"); q.add_argument("--out", default="")
    a = p.parse_args(argv)
    return {"scope": cmd_scope, "verify": cmd_verify, "slots": cmd_slots,
            "ab": cmd_ab, "perm": cmd_perm, "split": cmd_split,
            "turns": cmd_turns}[a.cmd](a)


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
