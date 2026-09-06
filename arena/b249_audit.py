# -*- coding: utf-8 -*-
"""B-249：**2つの「しきい値の崖」が実対局のどれだけの席に効いているかを数える**（★計測のみ）。

## 発端（§72-45／B-247）

B-243 で belief（役職推論）を鋭くしたところ、**belief はどの退行局でも正しくなっている**のに
一部の局が負けに転じた。機序＝**belief が鈍かった時代に較正された「絶対確率のしきい値」が
崖として作動する**こと。特定済みの崖は2つ：

| 崖 | 実装 | 何が跳ぶか |
|---|---|---|
| **(A)** | `agents/heuristic_protagonist.py:589` `PLAN_HOT_P = 0.7`／`:1127` `v = hot if pick_heat >= hot_p else bonus` | 防御プランナー加点が **bonus → hot** に不連続に跳ぶ（`移動` は **8 → 88**／`移動禁止` は **24 → 88**） |
| **(B)** | 同 `:1437` `_CULT_MAYBE_P = 0.1`／使用箇所 `:6803` | 「belief で除外されていない」の**母集合から落ちる**。落ちた結果 `_cultist_pin_target` の候補が 1人になる/ならないが動き、`:7377` の **99.0** が立つ/立たない |

## 本モジュールが数えるもの（★定義を先に固定する）

**席（seat）** ＝ 主人公の `set_card` 決定1回（`decide(decision="set_card")` 1回）。

### 崖(A)

| 記号 | 定義 |
|---|---|
| `heat` | `plan.pick_heat[key]`＝その折り手が覆った**致命**脅威の実在度の最大値（`defense_plan.plan_defenses._heat`） |
| (a0) | 加点キーを1本以上持つ席（＝崖の判定を1回でも通った席） |
| **(a1)** | `0.5 <= heat < 0.7` の加点キーを持つ席（＝**崖の直下**） |
| **(a2)** | `0.7 <= heat < 0.9` の加点キーを持つ席（＝**崖の直上**） |
| (a2b) | `heat >= 0.9` の加点キーを持つ席（＝直上のさらに上・参考） |
| **(a3)** | **首位と次点の素点差 ≦ 跳び幅** の席（跳び幅＝その席の加点キーの `hot-bonus` の最大値。`移動`=80／`移動禁止`=64／`友好`=64／`暗躍禁止`=2） |
| **(a4)** | ★**θ を振ると `argmax(options, key=score)` が実際に入れ替わる席**（下記の掃引表） |

★(a3) は**上限**（差が跳び幅以内でも実際に入れ替わるとは限らない）。(a4) は**実測**。

### 崖(B)

| 記号 | 定義 |
|---|---|
| (b0) | `mm_char_now`（脚本家が今ターン伏せ札を置いたキャラ）が空でない席 |
| **(b1)** | `0 < p(カルティスト) <= _CULT_MAYBE_P` ＝**しきい値で母集合から落ちた**候補の数 |
| **(b2)** | `0.05 <= p <= 0.15` の**境界帯**にいる候補を持つ席 |
| (b3) | `_cultist_pin_target` が立った席／その手が実際に打たれた席 |
| **(b4)** | ★θ を振るとピンの**対象が変わる/消える/立つ**席（下記の掃引表） |
| **(b4a)** | そのうち**実手がピンだった**席＝θ を動かすと手が確実に変わる席（**実測**） |
| **(b4b)** | 「なし→立つ」のうち **99.0 なら首位を奪う**席（**上限**＝B-222 の幾何ゲート／B-54 の譲りは考慮しない） |

## 二重実装をしない（§72-34 の教訓＝「実対局の盤面から決定的に数え上げられる量」だけを使う）

- 採点は **`decide` のローカル `score`（本物のクロージャ）** を呼ぶ＝AI の採点器を書き写さない。
  取り方は **B-247 の作法をそのまま踏襲**＝`_apply_seat_flags` を記録ラッパで包み、
  呼び出し元フレーム（`decide`）のローカルを読む（`arena/b247_probe._play_traced`）。
  ★`_apply_seat_flags` の**前**に採点する＝実際に `max()` が見た点と一致する。
- 崖(A)の θ 差し替えは `HeuristicProtagonist._plan_coeffs`（本物）＋ `plan.pick_heat`（本物）から
  `:1127` の 4 行だけを再現する。★**θ=既定 で再現値が `_plan_recs` と完全一致するか**を
  毎席検証し、1件でも食い違えばその席を計数から外して `mismatch` に積む（RC=1 で落ちる）。
- 崖(B)は `decide` のローカル（`mm_char_now` / `mm_board_now` / `danger_board` /
  `board_rules_possible` / `_cultist_maybe` / `_cultist_pin_target`）を**そのまま読む**。
  θ 差し替えは `:6803`〜`:6831` の述語を再現し、★**θ=既定 で `_cultist_pin_target` と
  完全一致するか**を毎席検証する。

## 挙動には触れない

本モジュールは `agents/` `sim/` `engine/` の**既定値を一切書き換えない**。2段で検査する
（B-248 `arena/knob_audit.py` と同じ不変条件。`knob_audit` は `origin/gate/b248` にあり
main 未着地のため**取り込まず等価形をレーン内に実装**した）：

1. `check_baseline()`＝**ベースライン条件（`--cond off`）のフラグ束 == リポジトリ既定**。
2. `check_no_knob_writes()`＝**測定の前後で bool 切替口が1bitも動いていない**（条件は必ず復元される）。

★`--cond bx` は**測定条件としてだけ** `B243_BOARD_X_ROLE` を立てる（§72-45＝退行の引き金）。
  ベースラインではないので、その数字を正典値の比較に使ってはならない。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b249_audit defaults
    python -m arena.b249_audit verify  --pick random_BTX:0-1
    python -m arena.b249_audit seats   --pick random_FS:0-9,random_BTX:0-9
    python -m arena.b249_audit seats   --cond bx --json /tmp/bx.json

結果＝`docs/測定_B249_しきい値の崖の効き_2026-08-18.md`。
"""
from __future__ import annotations

import argparse
import json
import sys as _sys
from collections import Counter
from dataclasses import replace

MOVE_BAN = "移動禁止"

#: 崖(A) の掃引点（既定 0.7・両端 0.0/1.01＝「常にホット」「決してホットでない」の括り）
THETA_A = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)
#: 崖(B) の掃引点（既定 0.1・0.0＝「p>0 なら母集合に残す」＝下限）
THETA_B = (0.0, 0.02, 0.05, 0.1, 0.2, 0.3)

#: heat の分布バケツ（左閉右開・最後だけ右閉）
_BUCKETS = ((0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))

_EPS = 1e-9


# ---------------------------------------------------------------------------
# ★切替口の健全性検査（B-248 `arena/knob_audit.py` の等価形）
# ---------------------------------------------------------------------------
#: 切替口の既定値が定義されうるモジュール（`arena/knob_audit.HOLDER_MODULES` と同じ）
HOLDER_MODULES = (
    "agents.heuristic_protagonist",
    "agents.belief",
    "agents.defense_plan",
    "agents.heuristic",
    "agents.b100_mix",
    "sim.loop_race",
)

#: ★条件表（`arena/knob_audit` と同じ形）。**ベースライン＝`off`＝リポジトリ既定**。
#:   `bx`＝B-243 の「belief を鋭くする」切替口（§72-45 の退行の引き金・**既定 OFF**）。
#:   本ドライバは A/B ではないが、「鋭くした後に崖がどれだけ効くか」を数えるために
#:   **測定条件としてだけ** ON にできる（★リポジトリ既定は書き換えない）。
KNOBS = {"bx": ("belief", "B243_BOARD_X_ROLE")}
#: ベースライン条件で ON にする切替口（空＝リポジトリ既定と一致していなければならない）
BASELINE: tuple = ()

#: 本測定が「値そのもの」を根拠にする定数（報告に必ず印字する）
AUDITED_CONSTS = (
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "PLAN_BONUS"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "PLAN_HOT"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "PLAN_HOT_P"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "PLAN_CLASS_COEFFS"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "PLAN_CLASS_KINDS"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "_CULT_MAYBE_P"),
    # ★B-258：崖(B) の実効床は B-252(a) の切替口で決まる＝根拠として必ず印字する
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B252_CULT_FLOOR"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "_B252_CULT_FLOOR_P"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B100_MIX"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B222_FERRY_GEOMETRY"),
)


def _flag_snapshot() -> dict:
    """現在の bool 切替口の束（{("mod.holder","ATTR"): bool}）。"""
    import importlib
    out: dict = {}
    for mod in HOLDER_MODULES:
        try:
            m = importlib.import_module(mod)
        except Exception:            # pragma: no cover （置き場が無い環境）
            continue
        holders = [(mod, m)]
        for cls in ("HeuristicProtagonist", "HeuristicMastermind"):
            obj = getattr(m, cls, None)
            if obj is not None:
                holders.append((f"{mod}.{cls}", obj))
        for where, obj in holders:
            for name in dir(obj):
                if not (name[:1] == "B" and name[1:2].isdigit()):
                    continue
                try:
                    v = getattr(obj, name)
                except Exception:    # pragma: no cover
                    continue
                if isinstance(v, bool):
                    out[(where, name)] = v
    return out


def check_no_knob_writes(before: dict, *, driver: str = "arena.b249_audit") -> None:
    """★不変条件＝**測定の前後で切替口の束が1bitも動いていない**。違反で `SystemExit`。

    B-248 `arena/knob_audit.check_baseline` は「**ベースライン条件のフラグ束 ==
    リポジトリ既定**」を検査する。本ドライバは A/B ドライバではなく**条件表を持たない**
    （既定のまま1本だけ回す）ので、その不変条件は「**ベースライン ⊆ リポジトリ既定**が
    自明に成り立つ」形に退化する。★退化した先で意味を持つのは
    「**測定中に誰も（自分も）切替口を書いていない**」＝本関数。

    ★`arena/knob_audit.py` は `origin/gate/b248` にあり main に未着地。
      **取り込まず、同等の自己検査をレーン内に書いた**（判断理由＝本 doc の報告欄）。
    """
    after = _flag_snapshot()
    bad = [(k, before.get(k), after.get(k))
           for k in sorted(set(before) | set(after), key=lambda t: (t[0], t[1]))
           if before.get(k) != after.get(k)]
    if not bad:
        return
    body = "\n".join(f"    - {w}.{a}: 測定前 {b!r} → 測定後 {c!r}" for (w, a), b, c in bad)
    raise SystemExit(
        f"★切替口の健全性検査に失敗（{driver}）＝**測定が切替口を書き換えた**。\n{body}\n"
        "  → 本ドライバは計測専用＝既定を1bitも動かしてはならない（B-247 の測定事故＝§72-45）。")


def _holders() -> dict:
    import importlib
    return {"belief": importlib.import_module("agents.belief")}


def check_baseline() -> None:
    """★不変条件＝**ベースライン条件のフラグ束 == リポジトリ既定**（B-248 と同じ検査）。

    条件表の全項目は条件を切り替えるたびに毎回書き込まれるため、**リポジトリ既定が
    True の切替口を条件表に載せると `off`（ベースライン）を含む全条件が正典と別の盤面**に
    なる（B-247 の測定事故＝§72-45）。ここで落として構造的に防ぐ。
    """
    h = _holders()
    bad = []
    for k, (where, attr) in KNOBS.items():
        obj = h[where]
        if not hasattr(obj, attr):
            raise SystemExit(f"★条件表に未定義の切替口: {attr}")
        want, cur = (k in BASELINE), getattr(obj, attr)
        if bool(cur) is not want or not isinstance(cur, bool):
            bad.append((k, attr, want, cur))
    if bad:
        body = "\n".join(f"    - {k}（{attr}）: ベースラインでは {w} ／ リポジトリ既定は {c!r}"
                         for k, attr, w, c in bad)
        raise SystemExit("★条件表の健全性検査に失敗（arena.b249_audit）＝"
                         "**ベースライン条件がリポジトリ既定と一致しない**。\n" + body)


def apply_cond(cond: str) -> dict:
    """条件を適用して旧値を返す（`restore_cond` で必ず戻す）。"""
    on = set() if cond in ("off", "") else set(cond.split("+"))
    unknown = on - set(KNOBS)
    if unknown:
        raise SystemExit(f"★未知の条件: {sorted(unknown)}（有効＝off / {'/'.join(KNOBS)}）")
    h, old = _holders(), {}
    for k, (where, attr) in KNOBS.items():
        old[k] = getattr(h[where], attr)
        setattr(h[where], attr, k in on)
    return old


def restore_cond(old: dict) -> None:
    h = _holders()
    for k, v in old.items():
        setattr(h[KNOBS[k][0]], KNOBS[k][1], v)


def defaults_banner() -> dict:
    """監査対象の定数を印字し、切替口の束を返す（測定の前後で比較する）。"""
    import importlib
    check_baseline()
    rows = {}
    for mod, cls, attr in AUDITED_CONSTS:
        obj = getattr(importlib.import_module(mod), cls)
        rows[f"{cls}.{attr}"] = getattr(obj, attr)
    print("[b249] ★監査対象の既定値（リポジトリ既定・本測定は1つも書き換えない）")
    for k, v in rows.items():
        print(f"    {k} = {v!r}")
    snap = _flag_snapshot()
    print(f"[b249] 条件表の健全性検査 ✅（ベースライン `off`＝リポジトリ既定・"
          f"切替口 {len(KNOBS)} 個）")
    print(f"[b249] 切替口（bool）{len(snap)} 個をスナップショット。ON の数＝"
          f"{sum(1 for v in snap.values() if v)}")
    return snap


# ---------------------------------------------------------------------------
# 局の選び方（★決定的・名前で明示する）
# ---------------------------------------------------------------------------
def parse_pick(spec: str, days: int) -> list:
    """'random_FS:0-9,random_BTX:0-9' → [(name, seed, Script), ...]（列挙順は benchmark 準拠）。"""
    from arena.benchmark import benchmark_scripts

    want: list[tuple[str, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, rng = part.partition(":")
        if not rng:
            raise SystemExit(f"★--pick の書式は 'name:0-9' か 'name:3'：{part!r}")
        for chunk in rng.split("+"):
            if "-" in chunk:
                a, b = chunk.split("-")
                want.extend((name, s) for s in range(int(a), int(b) + 1))
            else:
                want.append((name, int(chunk)))
    pool = {(n, s): sc for n, s, sc in benchmark_scripts(days=days)}
    out = []
    for key in want:
        if key not in pool:
            raise SystemExit(f"★benchmark_scripts(days={days}) に無い局: {key}")
        out.append((key[0], key[1], pool[key]))
    return out


# ---------------------------------------------------------------------------
# 観測（★B-247 の作法＝`_apply_seat_flags` を記録ラッパで包む）
# ---------------------------------------------------------------------------
def _okey(o: dict) -> tuple:
    return (o.get("card"), o.get("target"), o.get("target_kind"))


class Tally:
    """1回の測定の集計器。"""

    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.heats: list[float] = []
        self.a_theta: dict = {t: Counter() for t in THETA_A}
        self.b_theta: dict = {t: Counter() for t in THETA_B}
        self.b_dropped: list[int] = []
        self.cult_p: list[float] = []
        self.rows: list[dict] = []
        self.mismatch: list[dict] = []

    def merge(self, other: "Tally") -> None:
        self.c.update(other.c)
        self.heats.extend(other.heats)
        for t in THETA_A:
            self.a_theta[t].update(other.a_theta[t])
        for t in THETA_B:
            self.b_theta[t].update(other.b_theta[t])
        self.b_dropped.extend(other.b_dropped)
        self.cult_p.extend(other.cult_p)
        self.rows.extend(other.rows)
        self.mismatch.extend(other.mismatch)


def _recs_at(agent, plan, recs_keys: set, theta: float | None) -> dict:
    """`heuristic_protagonist.py:1120-1131` の 4 行だけを θ 差し替えで再現する。

    θ=None＝各種別が持つ既定の `hot_p` を使う（＝**現行と完全一致するはず**＝自己検証）。
    ★フィルタ（種別・kind・自滅回避）は**再現しない**＝`recs_keys`（本物が通した鍵）で絞る。
    """
    out: dict = {}
    for b in plan.picks:
        key = (b.card, b.target, b.target_kind)
        if key not in recs_keys:
            continue
        coef = agent._plan_coeffs(b.card)
        if coef is None:                     # pragma: no cover （鍵が通った以上あり得ない）
            continue
        bonus, hot, hot_p, weak = coef
        thr = hot_p if theta is None else theta
        v = hot if plan.pick_heat.get(key, 0.0) >= thr else bonus
        if not b.robust:
            v *= weak
        out[key] = max(out.get(key, v), v)
    return out


def _argmax(options: list, scores: list) -> int:
    """`max(options, key=score)` と同じ勝ち方（**最初の最大値**が勝つ）。"""
    best_i, best_s = 0, scores[0]
    for i in range(1, len(options)):
        if scores[i] > best_s:
            best_i, best_s = i, scores[i]
    return best_i


def _jump_of(agent, plan, recs_keys: set) -> tuple[float, str]:
    """この席の跳び幅（`hot - bonus` の最大）と、その種別名。"""
    best, who = 0.0, ""
    for b in plan.picks:
        key = (b.card, b.target, b.target_kind)
        if key not in recs_keys:
            continue
        coef = agent._plan_coeffs(b.card)
        if coef is None:                     # pragma: no cover
            continue
        bonus, hot, _hp, weak = coef
        j = (hot - bonus) * (1.0 if b.robust else weak)
        if j > best:
            best, who = j, b.card
    return best, who


def _cult_p(marg: dict, name: str) -> float:
    return float(marg.get(name, {}).get("カルティスト", 0.0))


def _pin_at(agent, view, marg, mm_char_now, mm_board_now, danger_board,
            board_rules_possible, theta: float):
    """`heuristic_protagonist.py:6802-6831` の述語を θ 差し替えで再現する。"""
    if not (danger_board is not None and danger_board in (mm_board_now or set())
            and board_rules_possible):
        return None
    cand = [n for n in sorted(mm_char_now or ())
            if _cult_p(marg, n) > theta
            and (cc := agent._alive(view, n)) and cc["area"] != danger_board]
    return cand[0] if len(cand) == 1 else None


def _observe(agent, view, options, best, score, fr_locals, t: Tally,
             script: str, seed: int) -> None:
    """1席ぶんの計数（★読み取りと自前の再計算のみ）。"""
    t.c["席（set_card 決定）"] += 1
    prov = best.get("prov")
    if prov:
        t.c[f"（内訳）prov={prov} の席"] += 1

    # ---------------- 素点（本物の score クロージャ） ----------------
    base_scores = [float(score(o)) for o in options]
    i_nat = _argmax(options, base_scores)
    if _okey(options[i_nat]) != _okey(best):
        t.c["（診断）実手が argmax と違う席（B-100 強制・計画割当・B-241/246 振替）"] += 1

    # ---------------- 崖(A) ----------------
    recs = dict(getattr(agent, "_plan_recs", {}) or {})
    stash = getattr(agent, "_b100_plan", None)
    plan = stash[1] if stash else None
    if recs and plan is not None:
        pick_keys = {(b.card, b.target, b.target_kind) for b in plan.picks}
        if not set(recs) <= pick_keys:
            t.c["★(A) plan が古い席（計数から除外）"] += 1
        else:
            _observe_cliff_a(agent, options, base_scores, i_nat, recs, plan,
                             score, t, script, seed, view)
    elif recs:
        t.c["★(A) plan を取れなかった席（計数から除外）"] += 1

    # ---------------- 崖(B) ----------------
    _observe_cliff_b(agent, view, options, base_scores, best, fr_locals,
                     t, script, seed)


def _observe_cliff_a(agent, options, base_scores, i_nat, recs, plan,
                     score, t: Tally, script, seed, view) -> None:
    keys = set(recs)
    # (0) 自己検証＝θ 既定での再現が `_plan_recs` と完全一致するか
    repro = _recs_at(agent, plan, keys, None)
    if repro != recs:
        t.mismatch.append({"kind": "A", "script": script, "seed": seed,
                           "loop": view.get("loop"), "day": view.get("day"),
                           "recs": {json.dumps(k, ensure_ascii=False): v
                                    for k, v in recs.items()},
                           "repro": {json.dumps(k, ensure_ascii=False): v
                                     for k, v in repro.items()}})
        t.c["★(A) 再現が `_plan_recs` と食い違った席"] += 1
        return

    t.c["(a0) 加点キーを持つ席"] += 1
    heats = [float(plan.pick_heat.get(k, 0.0)) for k in keys]
    t.heats.extend(heats)
    for lo, hi in _BUCKETS:
        if any(lo - _EPS <= h < hi for h in heats):
            t.c[f"(a-dist) heat∈[{lo},{hi if hi <= 1.0 else 1.0}] を持つ席"] += 1
    if any(0.5 - _EPS <= h < 0.7 - _EPS for h in heats):
        t.c["★(a1) 崖の直下 0.5≦heat<0.7 を持つ席"] += 1
    if any(0.7 - _EPS <= h < 0.9 - _EPS for h in heats):
        t.c["★(a2) 崖の直上 0.7≦heat<0.9 を持つ席"] += 1
    if any(h >= 0.9 - _EPS for h in heats):
        t.c["(a2b) heat≧0.9 を持つ席"] += 1

    # (a3) 首位と次点の素点差 ≦ 跳び幅
    jump, who = _jump_of(agent, plan, keys)
    srt = sorted(base_scores, reverse=True)
    gap = (srt[0] - srt[1]) if len(srt) > 1 else float("inf")
    if jump > 0 and gap <= jump + _EPS:
        t.c["★(a3) 首位と次点の素点差 ≦ 跳び幅 の席"] += 1
        t.c[f"(a3内訳) 跳び幅 {jump:.0f}（{who}）"] += 1

    # (a4) θ を振ると argmax が実際に入れ替わるか
    aff = [i for i, o in enumerate(options) if _okey(o) in keys]
    for th in THETA_A:
        recs_t = _recs_at(agent, plan, keys, th)
        tt = t.a_theta[th]
        tt["席"] += 1
        if recs_t == recs:
            tt["加点が既定と同じ席"] += 1
            continue
        tt["★加点の値が既定と変わる席"] += 1
        sc_t = list(base_scores)
        old = agent._plan_recs
        try:
            agent._plan_recs = recs_t
            for i in aff:
                sc_t[i] = float(score(options[i]))
        finally:
            agent._plan_recs = old
        j = _argmax(options, sc_t)
        if _okey(options[j]) != _okey(options[i_nat]):
            tt["★★argmax が入れ替わる席"] += 1
            tt[f"  入替の内訳 {_okey(options[i_nat])[0]} → {_okey(options[j])[0]}"] += 1
            if len(t.rows) < 200:
                t.rows.append({"cliff": "A", "theta": th, "script": script,
                               "seed": seed, "loop": view.get("loop"),
                               "day": view.get("day"),
                               "from": _okey(options[i_nat]),
                               "to": _okey(options[j]),
                               "heats": sorted(round(h, 4) for h in heats)})


def _observe_cliff_b(agent, view, options, base_scores, best, fr,
                     t: Tally, script, seed) -> None:
    mm_char_now = fr.get("mm_char_now") or set()
    mm_board_now = fr.get("mm_board_now") or set()
    danger_board = fr.get("danger_board")
    board_rules_possible = bool(fr.get("board_rules_possible"))
    pin_real = fr.get("_cultist_pin_target")
    maybe_real = fr.get("_cultist_maybe")
    if maybe_real is None:
        t.c["★(B) ローカルを取れなかった席（計数から除外）"] += 1
        return
    b = getattr(agent, "_belief", None)
    if b is None:                            # pragma: no cover
        return
    marg = b.role_marginals() or {}
    # ★B-258（2026-08-19）＝**実効の床**を読む。ここは長らく `agent._CULT_MAYBE_P`（0.1）を
    #   直に読んでいたが、B-252(a) がユーザー裁定で既定 ON（床 0.15・§72-56）になった時点で
    #   本物（`heuristic_protagonist:_cult_maybe_floor()`）と食い違い、自己検証が全席で
    #   mismatch を積むようになった＝**道具側のバグ**（AI の挙動ではない）。
    #   ★B-249／B-250 の過去の測定値は「床 0.1 時代のもの」として有効（当時は同値）。
    thr = (agent._cult_maybe_floor() if hasattr(agent, "_cult_maybe_floor")
           else agent._CULT_MAYBE_P)

    # (0) 自己検証＝母集合とピン対象の再現
    repro_maybe = {n for n in mm_char_now if _cult_p(marg, n) > thr}
    repro_pin = _pin_at(agent, view, marg, mm_char_now, mm_board_now,
                        danger_board, board_rules_possible, thr)
    # ★同型の穴の予防＝本物は一意化の後に B-252(b) のゲートを通す（`:6888`）。
    #   既定（`B252_CULT_UNIQUE=False`）では常に True＝ここは無操作。
    if repro_pin is not None and hasattr(agent, "_b252_cand_mass_ok"):
        if not agent._b252_cand_mass_ok(marg, [repro_pin]):
            repro_pin = None
    if repro_maybe != set(maybe_real) or repro_pin != pin_real:
        t.mismatch.append({"kind": "B", "script": script, "seed": seed,
                           "loop": view.get("loop"), "day": view.get("day"),
                           "maybe_real": sorted(maybe_real),
                           "maybe_repro": sorted(repro_maybe),
                           "pin_real": pin_real, "pin_repro": repro_pin})
        t.c["★(B) 再現がローカルと食い違った席"] += 1
        return

    if mm_char_now:
        t.c["(b0) 脚本家が今ターン伏せ札を置いたキャラが居る席"] += 1
    t.cult_p.extend(p for n in mm_char_now if (p := _cult_p(marg, n)) > 0.0)
    dropped = [n for n in mm_char_now if 0.0 < _cult_p(marg, n) <= thr + _EPS]
    t.b_dropped.append(len(dropped))
    if dropped:
        t.c["★(b1) しきい値で母集合から落ちた候補がある席"] += 1
        t.c[f"(b1内訳) 落ちた候補 {len(dropped)}人 の席"] += 1
    if any(0.05 - _EPS <= _cult_p(marg, n) <= 0.15 + _EPS for n in mm_char_now):
        t.c["★(b2) 境界帯 0.05≦p≦0.15 の候補を持つ席"] += 1
    if pin_real is not None:
        t.c["(b3) ピンが立った席"] += 1
        if _okey(best) == (MOVE_BAN, pin_real, "character"):
            t.c["(b3) ★ピンの手が実際に打たれた席"] += 1

    played_pin = _okey(best)[0] == MOVE_BAN and _okey(best)[2] == "character"
    top = max(base_scores)
    #: `:7377` のピンは 99.0 を返す（B-222 ゲート／B-54 譲りの分岐に入らない限り）
    _PIN_SCORE = 99.0
    for th in THETA_B:
        pin_t = _pin_at(agent, view, marg, mm_char_now, mm_board_now,
                        danger_board, board_rules_possible, th)
        tt = t.b_theta[th]
        tt["席"] += 1
        if pin_t is not None:
            tt["ピンが立つ席"] += 1
        if pin_t == pin_real:
            continue
        tt["★ピンの対象が既定と変わる席"] += 1
        if pin_real is None:
            tt["  内訳 なし→立つ"] += 1
        elif pin_t is None:
            tt["  内訳 立つ→なし"] += 1
        else:
            tt["  内訳 別人に移る"] += 1
        if pin_t is not None and pin_real is None:
            # ★新たに立つピンが首位を奪うか（**上限**＝B-222 の幾何ゲート／B-54 の
            #   譲りに入らないと仮定して 99.0 が付くとみなす）
            _ex = any(_okey(o) == (MOVE_BAN, pin_t, "character") for o in options)
            if _ex and _PIN_SCORE > top + _EPS:
                tt["★★新ピンが首位を奪う席（上限）"] += 1
        if played_pin and _okey(best)[1] in (pin_real, pin_t):
            tt["★★実手がピンだった＝手が入れ替わりうる席"] += 1
            if len(t.rows) < 400:
                t.rows.append({"cliff": "B", "theta": th, "script": script,
                               "seed": seed, "loop": view.get("loop"),
                               "day": view.get("day"),
                               "pin_default": pin_real, "pin_theta": pin_t,
                               "played": _okey(best)})


def _run(script, name: str, seed: int, loops: int, t: Tally | None):
    """1局を回す。t が None なら素の対局（`verify` の対照）。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game

    HP = HeuristicProtagonist
    orig = HP._apply_seat_flags

    def _wrapped(agent, view, best):
        try:
            fr = _sys._getframe(1)
            if fr.f_code.co_name == "decide":
                loc = fr.f_locals
                score = loc.get("score")
                options = loc.get("options")
                if score is not None and options:
                    _observe(agent, view, options, best, score, loc, t, name, seed)
        except Exception as e:                       # pragma: no cover
            t.c[f"★観測例外 {type(e).__name__}"] += 1
        return orig(agent, view, best)

    if t is not None:
        HP._apply_seat_flags = _wrapped
    try:
        hp = HP(seed)
        state, _ = run_game(replace(script, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
    finally:
        if t is not None:
            HP._apply_seat_flags = orig
    return state


def _trace(state) -> list:
    return [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"),
             e.get("area"), e.get("role"), e.get("card"), e.get("target"))
            for e in state.history]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_defaults(a) -> int:
    before = defaults_banner()
    check_no_knob_writes(before)
    print("[b249] ✅ 切替口の束は測定の前後で不変（自明ケース＝まだ何も回していない）")
    return 0


def cmd_verify(a) -> int:
    """★プローブを付けても棋譜が **1ビットも変わらない** ことを確認する。"""
    before = defaults_banner()
    games = parse_pick(a.pick, a.days)
    ng = 0
    old = apply_cond(a.cond)
    try:
        for name, seed, sc in games:
            s0 = _run(sc, name, seed, a.loops, None)
            s1 = _run(sc, name, seed, a.loops, Tally())
            same = (_trace(s0) == _trace(s1) and s0.winner == s1.winner
                    and s0.loop_no == s1.loop_no)
            print(f"  {name}#{seed}: 一致={'✅' if same else '★不一致'} "
                  f"（winner={s0.winner}/{s1.winner} loop={s0.loop_no}/{s1.loop_no} "
                  f"history={len(s0.history)}/{len(s1.history)}）")
            ng += 0 if same else 1
    finally:
        restore_cond(old)
    check_no_knob_writes(before)
    print(f"[b249] verify: {len(games)}局中 不一致 {ng} 件")
    return 1 if ng else 0


def _fmt_table(title: str, thetas, tallies: dict, keys: list, default) -> str:
    w = max(len(k) for k in keys) + 2
    head = f"{'項目':<{w}}" + "".join(f"{('θ=' + str(x) + ('★' if x == default else '')):>14}"
                                      for x in thetas)
    lines = [title, head, "-" * len(head)]
    for k in keys:
        lines.append(f"{k:<{w}}" + "".join(f"{tallies[x].get(k, 0):>14}" for x in thetas))
    return "\n".join(lines)


def cmd_seats(a) -> int:
    before = defaults_banner()
    games = parse_pick(a.pick, a.days)
    total = Tally()
    old = apply_cond(a.cond)
    try:
        for name, seed, sc in games:
            t = Tally()
            _run(sc, name, seed, a.loops, t)
            total.merge(t)
    finally:
        restore_cond(old)
    check_no_knob_writes(before)

    print(f"\n[b249] 対局＝{len(games)}局（days={a.days} loops={a.loops}）"
          f" cond={a.cond}（{'★リポジトリ既定' if a.cond == 'off' else '★非ベースライン条件'}）"
          f" pick={a.pick}")
    print("=" * 78)
    print("【席の計数】")
    for k, v in sorted(total.c.items()):
        print(f"  {v:>7}  {k}")

    if total.mismatch:
        print(f"\n★★自己検証の食い違い {len(total.mismatch)} 件（先頭3件）")
        for m in total.mismatch[:3]:
            print("   ", json.dumps(m, ensure_ascii=False))

    hs = sorted(total.heats)
    print(f"\n【崖(A) heat の分布】加点キー {len(hs)} 本")
    if hs:
        print(f"  min={hs[0]:.4f} / max={hs[-1]:.4f} / "
              f"中央={hs[len(hs) // 2]:.4f}")
        for lo, hi in _BUCKETS:
            n = sum(1 for h in hs if lo - _EPS <= h < hi)
            print(f"  [{lo:.1f}, {min(hi, 1.0):.1f}) : {n:>6}  ({100.0 * n / len(hs):5.1f}%)")

    print()
    print(_fmt_table("【崖(A) θ 掃引＝`PLAN_HOT_P` を振ると席はどう動くか】", THETA_A,
                     total.a_theta,
                     ["席", "加点が既定と同じ席", "★加点の値が既定と変わる席",
                      "★★argmax が入れ替わる席"], 0.7))
    extra = sorted({k for c in total.a_theta.values() for k in c
                    if k.startswith("  入替の内訳")})
    if extra:
        print(_fmt_table("  （入替の内訳）", THETA_A, total.a_theta, extra, 0.7))

    print()
    print(_fmt_table("【崖(B) θ 掃引＝`_CULT_MAYBE_P` を振ると席はどう動くか】", THETA_B,
                     total.b_theta,
                     ["席", "ピンが立つ席", "★ピンの対象が既定と変わる席",
                      "  内訳 なし→立つ", "  内訳 立つ→なし", "  内訳 別人に移る",
                      "★★実手がピンだった＝手が入れ替わりうる席",
                      "★★新ピンが首位を奪う席（上限）"], 0.1))

    cp = sorted(total.cult_p)
    if cp:
        print(f"\n【崖(B) 母集合候補の p(カルティスト) の分布】"
              f"（`mm_char_now` かつ p>0）{len(cp)} 件"
              f"  min={cp[0]:.4f} / 中央={cp[len(cp) // 2]:.4f} / max={cp[-1]:.4f}")
        _edges = (0.0, 0.02, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.3, 0.5, 1.01)
        for lo, hi in zip(_edges, _edges[1:]):
            n = sum(1 for x in cp if lo - _EPS <= x < hi)
            mark = "  ←★既定 0.1 の境界" if lo == 0.1 else ""
            print(f"  [{lo:.2f}, {min(hi, 1.0):.2f}) : {n:>6}"
                  f"  ({100.0 * n / len(cp):5.1f}%){mark}")

    if total.b_dropped:
        d = total.b_dropped
        print(f"\n【崖(B) 落ちた候補の数】席 {len(d)} 個・合計 {sum(d)} 人・"
              f"最大 {max(d)} 人／席・平均 {sum(d) / len(d):.3f} 人／席")

    if a.rows:
        print(f"\n【入替の実例（先頭 {a.rows} 件）】")
        for r in total.rows[:a.rows]:
            print("   ", json.dumps(r, ensure_ascii=False))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"pick": a.pick, "days": a.days, "n_games": len(games),
                       "counts": dict(total.c),
                       "a_theta": {str(k): dict(v) for k, v in total.a_theta.items()},
                       "b_theta": {str(k): dict(v) for k, v in total.b_theta.items()},
                       "heats": total.heats, "b_dropped": total.b_dropped,
                       "rows": total.rows, "mismatch": total.mismatch},
                      f, ensure_ascii=False, indent=1)
        print(f"[b249] JSON: {a.json}")
    return 1 if total.mismatch else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="arena.b249_audit", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for nm in ("defaults", "verify", "seats"):
        q = sub.add_parser(nm)
        q.add_argument("--days", type=int, default=3)
        q.add_argument("--loops", type=int, default=8)
        q.add_argument("--pick", default="random_FS:0-9,random_BTX:0-9")
        q.add_argument("--cond", default="off",
                       help="off（既定＝リポジトリ既定）/ bx（belief を鋭くした条件）")
        if nm == "seats":
            q.add_argument("--rows", type=int, default=0)
            q.add_argument("--json", default=None)
    a = p.parse_args(argv)
    return {"defaults": cmd_defaults, "verify": cmd_verify, "seats": cmd_seats}[a.cmd](a)


if __name__ == "__main__":                      # pragma: no cover
    raise SystemExit(main())
