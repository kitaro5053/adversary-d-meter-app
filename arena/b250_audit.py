# -*- coding: utf-8 -*-
"""B-250：崖(B)＝`_CULT_MAYBE_P = 0.1` の **5日級コーパスでの追試**（★計測のみ）。

## 発端（B-249 の未解決 §4-2）

B-249 は3日級20局・495席で2つの崖を数え、**崖(B) が崖(A) の10倍以上の席に効いている**と結論した。
しかし自分の未解決事項でこう書いている：

> §72-45 の崖(B) の実害例（`random_BTX#1`＝99.0 のフェリーピンが**誤対象**に立つ）は**5日級**で、
> 3日級20局では「**別人に移る**」型の入れ替わりが **θ 全点で 0件**＝**実害例の型が再現していない**。
> ∴「崖(B) が誤対象ピンを立てる頻度」は本測定では未回答＝**5日級での追試が要る**。

本モジュールはその追試を行う。★**答える問いは1つ**＝
**「別人に移る」型の入れ替わり（ピンの対象が誤対象へ移る）は5日級で何席あるか**。

## B-249 との関係（★3日級の数字を壊さないための設計）

★**`arena/b249_audit.py` は1バイトも変更していない**。本モジュールはその**ヘルパを import して使う**
（`_pin_at` / `_recs_at` / `_argmax` / `_cult_p` / `_okey` / `_observe_cliff_a` /
`check_baseline` / `check_no_knob_writes` / `apply_cond` / `THETA_A` / `THETA_B` ...）。
∴ **B-249 の3日級の測り方・出力形式は構造的に不変**＝`python -m arena.b249_audit seats` は
本レーンでも同じ数字を出す（再現性の担保）。

## 本モジュールが**足した**もの

| 追加 | 何のため |
|---|---|
| **真の配役（`Script.role_of`）との突き合わせ** | ★「**誤対象**ピン」＝ピン先の真の役職が カルティスト でない席を**直接**数える（B-249 は θ 掃引の入れ替わりしか数えていない＝実害の頻度そのものは測れていなかった） |
| **ピン席の全数記録 `pin_rows`** | 席ごとに「ピン先・その p・真の役職・候補全員の p・しきい値からの距離」を残す＝`random_BTX#1` の機序を名指しで追える |
| **「別人に移る」席の全数記録 `move_rows`** | 本チケットの本丸。3日級では 0件だったので B-249 は rows を残していなかった |
| **`--perm`**（`arena.tie_noise.install_perm`） | §72-45 の実害例は **5日 rev** で観測された（`id` では消えて改善になる＝符号は籤）。★id だけ見ると実害例に到達できない |
| **`--outdir`＝1局1シャードの逐次書き出し＋再開** | 実行環境が約50〜60分で再起動する（規約 §10 の事故）。**局ごとに JSON を落として resume** する |
| **`report`** | シャードを集計して表を出す（対局を回さない） |

## 挙動には触れない

`agents/` `sim/` `engine/` の**既定値を一切書き換えない**。B-249 と同じ2段の自己検査を**そのまま使う**
（`check_baseline` / `check_no_knob_writes`）。★`--cond bx` と `--perm rev` は**測定条件**であって
ベースラインではない（その数字を正典値の比較に使ってはならない）。

★`arena/knob_audit.py`（B-248）は **main に着地済み**なので、`check_no_knob_writes` の
一般形として **`knob_audit` 側の検査も併走させる**（`--knob-audit`・既定 ON）。理由＝報告 §5。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b250_audit defaults --days 5
    python -m arena.b250_audit verify  --days 5 --pick random_BTX:1
    python -m arena.b250_audit seats   --days 5 --pick all --outdir /tmp/b250/off_id
    python -m arena.b250_audit seats   --days 5 --pick all --cond bx --perm rev --outdir /tmp/b250/bx_rev
    python -m arena.b250_audit report  --outdir /tmp/b250/off_id --focus random_BTX:1

結果＝`docs/測定_B250_崖Bの5日級追試_2026-08-18.md`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys as _sys
from collections import Counter
from dataclasses import replace

from arena.b249_audit import (  # noqa: F401  （★B-249 のヘルパを一切書き換えずに使う）
    MOVE_BAN, THETA_A, THETA_B, _BUCKETS, _EPS,
    Tally, _argmax, _cult_p, _observe_cliff_a, _okey, _pin_at, _recs_at,
    apply_cond, check_baseline, check_no_knob_writes, defaults_banner,
    restore_cond, _flag_snapshot, _trace,
)

CULTIST = "カルティスト"


# ---------------------------------------------------------------------------
# 局の選び方（★`arena/benchmark.py` の定義に必ず合わせる＝自前で脚本を作らない）
# ---------------------------------------------------------------------------
def parse_pick(spec: str, days: int) -> list:
    """'all' か 'random_BTX:0-19,fs5_guard:0-9' → [(name, seed, Script), ...]。

    ★プールは **`arena.benchmark.benchmark_scripts(days=days)`**（5日級＝70局）そのもの。
    """
    from arena.benchmark import benchmark_scripts
    pool = benchmark_scripts(days=days)
    if spec.strip() in ("all", "*"):
        return list(pool)
    index = {(n, s): sc for n, s, sc in pool}
    want: list[tuple[str, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, rng = part.partition(":")
        if not rng:
            raise SystemExit(f"★--pick の書式は 'name:0-9' か 'name:3' か 'all'：{part!r}")
        for chunk in rng.split("+"):
            if "-" in chunk:
                a, b = chunk.split("-")
                want.extend((name, s) for s in range(int(a), int(b) + 1))
            else:
                want.append((name, int(chunk)))
    out = []
    for key in want:
        if key not in index:
            raise SystemExit(f"★benchmark_scripts(days={days}) に無い局: {key}")
        out.append((key[0], key[1], index[key]))
    return out


# ---------------------------------------------------------------------------
# 集計器（★B-249 の Tally をそのまま継承して**足すだけ**）
# ---------------------------------------------------------------------------
class Tally5(Tally):
    def __init__(self) -> None:
        super().__init__()
        self.pin_rows: list[dict] = []    # 既定 θ でピンが立った席の全数
        self.move_rows: list[dict] = []   # ★「別人に移る」席の全数（本丸）
        #: ★ピンのゲート3条件（敗北板が判明・そこにmm札・盤面ルール未否定）を満たした席の全数。
        #:   **ピンが立たなかった席も含む**＝述語を差し替えたときの発火先を後付けで評価できる
        #:   （実装チケットへの申し送り §5 の材料）。
        self.gate_rows: list[dict] = []

    def merge(self, other) -> None:       # type: ignore[override]
        super().merge(other)
        self.pin_rows.extend(getattr(other, "pin_rows", []))
        self.move_rows.extend(getattr(other, "move_rows", []))
        self.gate_rows.extend(getattr(other, "gate_rows", []))

    def to_json(self) -> dict:
        return {"counts": dict(self.c),
                "a_theta": {str(k): dict(v) for k, v in self.a_theta.items()},
                "b_theta": {str(k): dict(v) for k, v in self.b_theta.items()},
                "heats": self.heats, "b_dropped": self.b_dropped,
                "cult_p": self.cult_p, "rows": self.rows,
                "pin_rows": self.pin_rows, "move_rows": self.move_rows,
                "gate_rows": self.gate_rows, "mismatch": self.mismatch}

    @classmethod
    def from_json(cls, d: dict) -> "Tally5":
        t = cls()
        t.c.update(d.get("counts", {}))
        for k, v in d.get("a_theta", {}).items():
            t.a_theta[float(k)].update(v)
        for k, v in d.get("b_theta", {}).items():
            t.b_theta[float(k)].update(v)
        t.heats = list(d.get("heats", []))
        t.b_dropped = list(d.get("b_dropped", []))
        t.cult_p = list(d.get("cult_p", []))
        t.rows = list(d.get("rows", []))
        t.pin_rows = list(d.get("pin_rows", []))
        t.move_rows = list(d.get("move_rows", []))
        t.gate_rows = list(d.get("gate_rows", []))
        t.mismatch = list(d.get("mismatch", []))
        return t


# ---------------------------------------------------------------------------
# 観測
# ---------------------------------------------------------------------------
def _truth_of(script, name: str) -> str:
    """真の役職（★脚本＝神視点。**AI には一切渡さない**・計数専用）。"""
    try:
        return script.role_of(name)
    except Exception:                                # pragma: no cover
        return "?"


def _cand_dump(marg, names, script) -> list:
    return sorted(({"name": n, "p": round(_cult_p(marg, n), 6),
                    "true": _truth_of(script, n)} for n in names),
                  key=lambda d: (-d["p"], d["name"]))


def _observe5(agent, view, options, best, score, fr, t: Tally5,
              script_obj, name: str, seed: int) -> None:
    """1席ぶんの計数（★読み取りと自前の再計算のみ・AI の採点器は書き写さない）。"""
    t.c["席（set_card 決定）"] += 1
    prov = best.get("prov")
    if prov:
        t.c[f"（内訳）prov={prov} の席"] += 1

    base_scores = [float(score(o)) for o in options]
    i_nat = _argmax(options, base_scores)
    if _okey(options[i_nat]) != _okey(best):
        t.c["（診断）実手が argmax と違う席（B-100 強制・計画割当・B-241/246 振替）"] += 1

    # ---------------- 崖(A)＝B-249 の実装をそのまま呼ぶ ----------------
    recs = dict(getattr(agent, "_plan_recs", {}) or {})
    stash = getattr(agent, "_b100_plan", None)
    plan = stash[1] if stash else None
    if recs and plan is not None:
        pick_keys = {(b.card, b.target, b.target_kind) for b in plan.picks}
        if not set(recs) <= pick_keys:
            t.c["★(A) plan が古い席（計数から除外）"] += 1
        else:
            _observe_cliff_a(agent, options, base_scores, i_nat, recs, plan,
                             score, t, name, seed, view)
    elif recs:
        t.c["★(A) plan を取れなかった席（計数から除外）"] += 1

    # ---------------- 崖(B) ----------------
    _observe_cliff_b5(agent, view, options, base_scores, best, fr, t,
                      script_obj, name, seed)


def _observe_cliff_b5(agent, view, options, base_scores, best, fr, t: Tally5,
                      script_obj, name: str, seed: int) -> None:
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
    if b is None:                                    # pragma: no cover
        return
    marg = b.role_marginals() or {}
    # ★B-258（2026-08-19）＝b249_audit と同型の穴。**実効の床**を読む
    #   （B-252(a) の既定 ON＝床 0.15 以降、`_CULT_MAYBE_P` 直読みでは本物と食い違う）。
    #   ★B-250 の過去の測定値は「床 0.1 時代のもの」として有効（当時は同値）。
    thr = (agent._cult_maybe_floor() if hasattr(agent, "_cult_maybe_floor")
           else agent._CULT_MAYBE_P)

    # (0) 自己検証＝母集合とピン対象の再現（★1件でも食い違えば RC=1）
    repro_maybe = {n for n in mm_char_now if _cult_p(marg, n) > thr}
    repro_pin = _pin_at(agent, view, marg, mm_char_now, mm_board_now,
                        danger_board, board_rules_possible, thr)
    # ★同型の穴の予防＝本物は一意化の後に B-252(b) のゲートを通す（既定 OFF＝無操作）。
    if repro_pin is not None and hasattr(agent, "_b252_cand_mass_ok"):
        if not agent._b252_cand_mass_ok(marg, [repro_pin]):
            repro_pin = None
    if repro_maybe != set(maybe_real) or repro_pin != pin_real:
        t.mismatch.append({"kind": "B", "script": name, "seed": seed,
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
        # ★落ちた候補のうち **真のカルティスト**が居た席＝しきい値が真犯人を捨てた席
        if any(_truth_of(script_obj, n) == CULTIST for n in dropped):
            t.c["★★(b1t) 落ちた候補に**真のカルティスト**が居た席"] += 1
    if any(0.05 - _EPS <= _cult_p(marg, n) <= 0.15 + _EPS for n in mm_char_now):
        t.c["★(b2) 境界帯 0.05≦p≦0.15 の候補を持つ席"] += 1

    # ★真のカルティストが「今ターン伏せ札を置かれたキャラ」に居るか／しきい値で落ちたか
    cult_names = [n for n in mm_char_now if _truth_of(script_obj, n) == CULTIST]
    cult_present = bool(cult_names)
    cult_dropped = any(n in dropped for n in cult_names)
    cult_in_cast = any(r == CULTIST for r in getattr(script_obj, "roles", {}).values())

    gate_ok = (danger_board is not None and danger_board in mm_board_now
               and board_rules_possible)
    if gate_ok and mm_char_now:
        t.c["(b0g) ピンのゲート3条件を満たした席"] += 1
        t.gate_rows.append({
            "script": name, "seed": seed, "loop": view.get("loop"),
            "day": view.get("day"), "danger_board": danger_board,
            "pin": pin_real, "thr": thr,
            "cult_in_cast": cult_in_cast,
            "cands": [{**d, "area": (lambda c: c["area"] if c else None)(
                agent._alive(view, d["name"]))}
                for d in _cand_dump(marg, mm_char_now, script_obj)],
            "played": _okey(best),
        })

    played_pin = _okey(best)[0] == MOVE_BAN and _okey(best)[2] == "character"
    if pin_real is not None:
        t.c["(b3) ピンが立った席"] += 1
        true_role = _truth_of(script_obj, pin_real)
        wrong = true_role != CULTIST
        if not cult_present:
            t.c["★★(b3x) ピン席のうち **候補に真のカルティストが1人も居ない** 席"] += 1
        elif cult_dropped:
            t.c["★★(b3y) ピン席のうち **しきい値が真のカルティストを落とした** 席"] += 1
        t.c["★(b3w) ピンが**誤対象**に立った席（真の役職≠カルティスト）" if wrong
            else "(b3r) ピンが真のカルティストに立った席"] += 1
        played_here = _okey(best) == (MOVE_BAN, pin_real, "character")
        if played_here:
            t.c["(b3) ★ピンの手が実際に打たれた席"] += 1
            t.c["★★(b3wp) **誤対象**ピンが実際に打たれた席" if wrong
                else "(b3rp) 正対象ピンが実際に打たれた席"] += 1
        t.pin_rows.append({
            "script": name, "seed": seed, "loop": view.get("loop"),
            "day": view.get("day"), "pin": pin_real,
            "p": round(_cult_p(marg, pin_real), 6),
            "true": true_role, "wrong": wrong, "played": played_here,
            "thr": thr, "danger_board": danger_board,
            "cands": _cand_dump(marg, mm_char_now, script_obj),
        })

    top = max(base_scores)
    _PIN_SCORE = 99.0
    for th in THETA_B:
        pin_t = _pin_at(agent, view, marg, mm_char_now, mm_board_now,
                        danger_board, board_rules_possible, th)
        tt = t.b_theta[th]
        tt["席"] += 1
        if pin_t is not None:
            tt["ピンが立つ席"] += 1
            # ★★θ ごとの「ピンの当たり外れ」＝本チケットの実害の直接指標
            if _truth_of(script_obj, pin_t) == CULTIST:
                tt["　うち 正対象（真のカルティスト）"] += 1
            else:
                tt["★うち **誤対象**"] += 1
                if not cult_present:
                    tt["　　うち 候補に真のカルティストが不在"] += 1
                elif cult_dropped:
                    tt["　　うち しきい値が真のカルティストを落とした"] += 1
                else:
                    tt["　　うち 真のカルティストは候補に残っていた"] += 1
        if pin_t == pin_real:
            continue
        tt["★ピンの対象が既定と変わる席"] += 1
        if pin_real is None:
            tt["  内訳 なし→立つ"] += 1
            if _truth_of(script_obj, pin_t) != CULTIST:
                tt["   うち 新ピンが**誤対象**"] += 1
        elif pin_t is None:
            tt["  内訳 立つ→なし"] += 1
        else:
            # ★★本丸＝「別人に移る」
            tt["  内訳 別人に移る"] += 1
            tr_d, tr_t = _truth_of(script_obj, pin_real), _truth_of(script_obj, pin_t)
            if tr_t != CULTIST:
                tt["   うち 移った先が**誤対象**"] += 1
            if tr_d == CULTIST and tr_t != CULTIST:
                tt["   ★うち 正対象→**誤対象**（実害の型）"] += 1
            t.move_rows.append({
                "theta": th, "script": name, "seed": seed,
                "loop": view.get("loop"), "day": view.get("day"),
                "from": {"name": pin_real, "p": round(_cult_p(marg, pin_real), 6),
                         "true": tr_d},
                "to": {"name": pin_t, "p": round(_cult_p(marg, pin_t), 6),
                       "true": tr_t},
                "thr": thr, "danger_board": danger_board,
                "played_pin": played_pin, "played": _okey(best),
                "cands": _cand_dump(marg, mm_char_now, script_obj),
            })
        if pin_t is not None and pin_real is None:
            _ex = any(_okey(o) == (MOVE_BAN, pin_t, "character") for o in options)
            if _ex and _PIN_SCORE > top + _EPS:
                tt["★★新ピンが首位を奪う席（上限）"] += 1
        if played_pin and _okey(best)[1] in (pin_real, pin_t):
            tt["★★実手がピンだった＝手が入れ替わりうる席"] += 1
            if len(t.rows) < 4000:
                t.rows.append({"cliff": "B", "theta": th, "script": name,
                               "seed": seed, "loop": view.get("loop"),
                               "day": view.get("day"),
                               "pin_default": pin_real, "pin_theta": pin_t,
                               "played": _okey(best)})


# ---------------------------------------------------------------------------
# 対局
# ---------------------------------------------------------------------------
def _run(script_obj, name: str, seed: int, loops: int, t: Tally5 | None):
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
                    _observe5(agent, view, options, best, score, loc, t,
                              script_obj, name, seed)
        except Exception as e:                       # pragma: no cover
            t.c[f"★観測例外 {type(e).__name__}"] += 1
        return orig(agent, view, best)

    if t is not None:
        HP._apply_seat_flags = _wrapped
    try:
        hp = HP(seed)
        state, _ = run_game(replace(script_obj, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
    finally:
        if t is not None:
            HP._apply_seat_flags = orig
    return state


def _install_perm(perm: str):
    if perm in ("id", "", None):
        return None
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    return uninstall_perm


# ---------------------------------------------------------------------------
# 追加の健全性検査（★B-248 `arena/knob_audit.py` の一般形・main 着地済み）
# ---------------------------------------------------------------------------
def knob_audit_baseline(cond: str) -> str:
    """`arena/knob_audit.py`（B-248・main 着地済み）で条件表の健全性を検査する。

    B-249 は「knob_audit が main 未着地」を理由にレーン内へ等価形を書いた。**今は main に
    あるので一般形を併走させる**（B-249 の自前検査も残す＝二重化）。使えなければ理由を返す。
    """
    try:
        from arena import knob_audit as ka
    except Exception as e:                           # pragma: no cover
        return f"（`arena.knob_audit` を import できず: {e}）"
    fn = getattr(ka, "check_baseline", None)
    if fn is None:                                   # pragma: no cover
        return "（`arena.knob_audit.check_baseline` が無い）"
    try:
        from arena.b249_audit import BASELINE, KNOBS, _holders
        fn(KNOBS, _holders(), BASELINE, driver="arena.b250_audit")
        return "✅ `arena.knob_audit.check_baseline`（B-248 の一般形）も PASS"
    except TypeError:
        return "（`knob_audit.check_baseline` の引数形が違う＝一般形は使わず B-249 の等価形のみ）"
    except SystemExit as e:                          # pragma: no cover
        raise SystemExit(f"★knob_audit の検査に失敗: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _shard_path(outdir: str, cond: str, perm: str, name: str, seed: int) -> str:
    return os.path.join(outdir, f"{cond}__{perm}__{name}__{seed}.json")


def cmd_defaults(a) -> int:
    before = defaults_banner()
    print(f"[b250] {knob_audit_baseline(a.cond)}")
    check_no_knob_writes(before, driver="arena.b250_audit")
    print("[b250] ✅ 切替口の束は測定の前後で不変（自明ケース＝まだ何も回していない）")
    return 0


def cmd_verify(a) -> int:
    before = defaults_banner()
    games = parse_pick(a.pick, a.days)
    ng = 0
    old = apply_cond(a.cond)
    un = _install_perm(a.perm)
    try:
        for name, seed, sc in games:
            s0 = _run(sc, name, seed, a.loops, None)
            s1 = _run(sc, name, seed, a.loops, Tally5())
            same = (_trace(s0) == _trace(s1) and s0.winner == s1.winner
                    and s0.loop_no == s1.loop_no)
            print(f"  {name}#{seed}: 一致={'✅' if same else '★不一致'} "
                  f"（winner={s0.winner}/{s1.winner} loop={s0.loop_no}/{s1.loop_no} "
                  f"history={len(s0.history)}/{len(s1.history)}）", flush=True)
            ng += 0 if same else 1
    finally:
        if un:
            un()
        restore_cond(old)
    check_no_knob_writes(before, driver="arena.b250_audit")
    print(f"[b250] verify: {len(games)}局中 不一致 {ng} 件")
    return 1 if ng else 0


def cmd_seats(a) -> int:
    before = defaults_banner()
    print(f"[b250] {knob_audit_baseline(a.cond)}")
    games = parse_pick(a.pick, a.days)
    os.makedirs(a.outdir, exist_ok=True)
    old = apply_cond(a.cond)
    un = _install_perm(a.perm)
    try:
        for name, seed, sc in games:
            p = _shard_path(a.outdir, a.cond, a.perm, name, seed)
            if os.path.exists(p) and not a.force:
                print(f"  skip {name}#{seed}（済）", flush=True)
                continue
            t = Tally5()
            st = _run(sc, name, seed, a.loops, t)
            d = t.to_json()
            d["meta"] = {"script": name, "seed": seed, "days": a.days,
                         "loops": a.loops, "cond": a.cond, "perm": a.perm,
                         "winner": st.winner, "loop_no": st.loop_no,
                         "final_battle": any(e.get("event") == "final_battle"
                                             for e in st.history)}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            print(f"  done {name}#{seed}: 席={t.c['席（set_card 決定）']} "
                  f"ピン席={t.c.get('(b3) ピンが立った席', 0)} "
                  f"誤対象={t.c.get('★(b3w) ピンが**誤対象**に立った席（真の役職≠カルティスト）', 0)} "
                  f"別人={sum(v.get('  内訳 別人に移る', 0) for v in t.b_theta.values())} "
                  f"winner={st.winner}/L{st.loop_no}", flush=True)
    finally:
        if un:
            un()
        restore_cond(old)
    check_no_knob_writes(before, driver="arena.b250_audit")
    return _report(a)


def _load(outdir: str, cond: str | None, perm: str | None):
    total, metas = Tally5(), []
    for fn in sorted(os.listdir(outdir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(outdir, fn), encoding="utf-8") as f:
            d = json.load(f)
        m = d.get("meta", {})
        if cond and m.get("cond") != cond:
            continue
        if perm and m.get("perm") != perm:
            continue
        total.merge(Tally5.from_json(d))
        metas.append(m)
    return total, metas


def _fmt_table(title: str, thetas, tallies: dict, keys: list, default) -> str:
    w = max(len(k) for k in keys) + 2
    head = f"{'項目':<{w}}" + "".join(f"{('θ=' + str(x) + ('★' if x == default else '')):>14}"
                                      for x in thetas)
    lines = [title, head, "-" * len(head)]
    for k in keys:
        lines.append(f"{k:<{w}}" + "".join(f"{tallies[x].get(k, 0):>14}" for x in thetas))
    return "\n".join(lines)


def _report(a) -> int:
    total, metas = _load(a.outdir, a.cond, a.perm)
    n = len(metas)
    ndef = sum(1 for m in metas if m.get("winner") == "protagonist"
               and not m.get("final_battle"))
    print(f"\n[b250] 集計＝{n}局（days={a.days} loops={a.loops}）"
          f" cond={a.cond}（{'★リポジトリ既定' if a.cond == 'off' else '★非ベースライン条件'}）"
          f" perm={a.perm} outdir={a.outdir}")
    print(f"       防衛勝ち {ndef}/{n} 局")
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
        print(f"  min={hs[0]:.4f} / max={hs[-1]:.4f} / 中央={hs[len(hs) // 2]:.4f}")
        for lo, hi in _BUCKETS:
            k = sum(1 for h in hs if lo - _EPS <= h < hi)
            print(f"  [{lo:.1f}, {min(hi, 1.0):.1f}) : {k:>6}  ({100.0 * k / len(hs):5.1f}%)")

    print()
    print(_fmt_table("【崖(A) θ 掃引＝`PLAN_HOT_P`】", THETA_A, total.a_theta,
                     ["席", "加点が既定と同じ席", "★加点の値が既定と変わる席",
                      "★★argmax が入れ替わる席"], 0.7))
    extra = sorted({k for c in total.a_theta.values() for k in c
                    if k.startswith("  入替の内訳")})
    if extra:
        print(_fmt_table("  （入替の内訳）", THETA_A, total.a_theta, extra, 0.7))

    print()
    print(_fmt_table("【崖(B) θ 掃引＝`_CULT_MAYBE_P`】", THETA_B, total.b_theta,
                     ["席", "ピンが立つ席", "　うち 正対象（真のカルティスト）",
                      "★うち **誤対象**",
                      "　　うち 候補に真のカルティストが不在",
                      "　　うち しきい値が真のカルティストを落とした",
                      "　　うち 真のカルティストは候補に残っていた",
                      "★ピンの対象が既定と変わる席",
                      "  内訳 なし→立つ", "   うち 新ピンが**誤対象**",
                      "  内訳 立つ→なし",
                      "  内訳 別人に移る", "   うち 移った先が**誤対象**",
                      "   ★うち 正対象→**誤対象**（実害の型）",
                      "★★実手がピンだった＝手が入れ替わりうる席",
                      "★★新ピンが首位を奪う席（上限）"], 0.1))

    cp = sorted(total.cult_p)
    if cp:
        print(f"\n【崖(B) 母集合候補の p(カルティスト) の分布】"
              f"（`mm_char_now` かつ p>0）{len(cp)} 件"
              f"  min={cp[0]:.4f} / 中央={cp[len(cp) // 2]:.4f} / max={cp[-1]:.4f}")
        _edges = (0.0, 0.02, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.3, 0.5, 1.01)
        for lo, hi in zip(_edges, _edges[1:]):
            k = sum(1 for x in cp if lo - _EPS <= x < hi)
            mark = "  ←★既定 0.1 の境界" if lo == 0.1 else ""
            print(f"  [{lo:.2f}, {min(hi, 1.0):.2f}) : {k:>6}"
                  f"  ({100.0 * k / len(cp):5.1f}%){mark}")

    if total.b_dropped:
        d = total.b_dropped
        print(f"\n【崖(B) 落ちた候補の数】席 {len(d)} 個・合計 {sum(d)} 人・"
              f"最大 {max(d)} 人／席・平均 {sum(d) / len(d):.3f} 人／席")

    # ★本丸＝「別人に移る」の全数
    print(f"\n【★本丸＝「別人に移る」席の全数】{len(total.move_rows)} 件"
          f"（θ 掃引の全点の合計・同一席が複数 θ で数えられる）")
    seats = {(r["script"], r["seed"], r["loop"], r["day"]) for r in total.move_rows}
    print(f"  重複を除いた**席**の数＝{len(seats)}")
    for r in total.move_rows[:a.rows or 40]:
        print("   ", json.dumps(r, ensure_ascii=False))

    # ★ピン席の要約
    pr = total.pin_rows
    if pr:
        wrong = [r for r in pr if r["wrong"]]
        wp = [r for r in wrong if r["played"]]
        print(f"\n【ピンが立った席の要約】{len(pr)} 席 ／ ★誤対象 {len(wrong)} 席"
              f"（{100.0 * len(wrong) / len(pr):.1f}%）／ そのうち実際に打たれた {len(wp)} 席")
        byg = Counter((r["script"], r["seed"]) for r in wrong)
        print("  誤対象ピンの局別内訳（上位10）:")
        for (s, sd), k in byg.most_common(10):
            print(f"    {s}#{sd}: {k} 席")
        near = [r for r in wrong if abs(r["p"] - r["thr"]) <= 0.05]
        print(f"  誤対象ピンのうち **p がしきい値 ±0.05 以内** の席＝{len(near)}")

    if a.focus:
        nm, _, sd = a.focus.partition(":")
        sd = int(sd)
        print(f"\n【★名指し追跡＝{nm}#{sd}】")
        for r in pr:
            if r["script"] == nm and r["seed"] == sd:
                print("   PIN ", json.dumps(r, ensure_ascii=False))
        for r in total.move_rows:
            if r["script"] == nm and r["seed"] == sd:
                print("   MOVE", json.dumps(r, ensure_ascii=False))
    if a.json:
        _dump_json(a.json, {"n_games": n, "n_defense": ndef, "metas": metas,
                            **total.to_json()})
        print(f"[b250] JSON: {a.json}")
    return 1 if total.mismatch else 0


def _dump_json(path: str, obj: dict) -> None:
    """★行数を抑えた書き出し＝**行の配列は1行1要素**、他は1行。

    `indent=1` で全部展開すると生データが 19 万行になりレビューに載らない。
    行の配列（`pin_rows` / `gate_rows` / `move_rows` / `metas` / `rows`）だけ
    1要素1行にし、残りは1行にまとめる（**中身は同じ JSON**）。
    """
    line_keys = ("metas", "rows", "pin_rows", "move_rows", "gate_rows")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        keys = list(obj)
        for i, k in enumerate(keys):
            tail = "," if i < len(keys) - 1 else ""
            v = obj[k]
            kj = json.dumps(k, ensure_ascii=False)
            if k in line_keys and isinstance(v, list):
                f.write(f" {kj}: [\n")
                for j, row in enumerate(v):
                    f.write("  " + json.dumps(row, ensure_ascii=False)
                            + ("," if j < len(v) - 1 else "") + "\n")
                f.write(f" ]{tail}\n")
            else:
                f.write(f" {kj}: " + json.dumps(v, ensure_ascii=False) + tail + "\n")
        f.write("}\n")


def cmd_report(a) -> int:
    return _report(a)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="arena.b250_audit", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for nm in ("defaults", "verify", "seats", "report"):
        q = sub.add_parser(nm)
        q.add_argument("--days", type=int, default=5)
        q.add_argument("--loops", type=int, default=8)
        q.add_argument("--pick", default="all")
        q.add_argument("--cond", default="off",
                       help="off（既定＝リポジトリ既定）/ bx（belief を鋭くした条件）")
        q.add_argument("--perm", default="id",
                       help="id（正典）/ rev / h1 / h2（`arena.tie_noise.install_perm`）")
        if nm in ("seats", "report"):
            q.add_argument("--outdir", required=True)
            q.add_argument("--rows", type=int, default=0)
            q.add_argument("--json", default=None)
            q.add_argument("--focus", default=None, help="例 random_BTX:1")
        if nm == "seats":
            q.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    return {"defaults": cmd_defaults, "verify": cmd_verify,
            "seats": cmd_seats, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
