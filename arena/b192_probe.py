# -*- coding: utf-8 -*-
"""B-192：黒猫/巫女の左右往復のプローブ（Phase 0＝読み取り専用／Phase 1＝前後比較）。

教材＝docs/feedback_logs/鈴蘭_BTX3d_seed0_再戦_2026-08-08.jsonl の L2
（L2D1 p2 / L2D2 p1 / L2D3 p1 ＝移動←→→黒猫、L2D2 p3 ＝移動←→→巫女）。

★why/scale は agents/ の判断経路・既定値には一切触れない（読むだけ）。

サブコマンド:
    python -m arena.b192_probe why
        # 教材棋譜の該当席を再構成し、採点内訳（どの述語が何点・次点は何か）を出す。
        # B-110（手詰まり移動 floor）の寄与は本体メソッドを横取りして実値＋成分で表示。
        # 併せて B-110 OFF のアブレーション（同席で何が選ばれるか）も出す。
    python -m arena.b192_probe scale --days 3   （/ --days 5）
        # ベンチ全局で「同一キャラを同一ループ内に2回以上・正味変位ゼロで動かした席」を数える。
        # 各席の採点（実効手>=8.0 か床帯<8.0 か）で「浪費と断定できる往復」を分類する。
    python -m arena.b192_probe phase1
        # ★Phase 1（B192_TEST_INTERFERE_DISCOUNT / B192_KEEP_OWN_PAIR）の単局前後比較：
        # 教材4席をフラグ4組合せ {OFF, (i), (ii), 両方} で再生し、
        # 移動候補（黒猫/巫女/医者）の採点と選択の変化を並べる。

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.debug import ProbedProtagonist
from agents.heuristic_protagonist import _MOVE_TOGGLE, _move_dest

REPO = Path(__file__).resolve().parent.parent
LOG_REMATCH = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_再戦_2026-08-08.jsonl"
TARGETS = {(2, 1, "p2"), (2, 2, "p1"), (2, 2, "p3"), (2, 3, "p1")}
EFFECTIVE_MIN = 8.0   # 最小の実効手（B-63 冷却席の譲り）＝これ未満は床帯（空振り3.5〜B110上限7.5）


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return lines[0], [d for d in lines if d.get("type") == "decision"]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _key(o: dict) -> tuple:
    return (o.get("card"), o.get("target"), o.get("target_kind"))


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _hook_idle(hp: ProbedProtagonist) -> dict:
    """`_b110_idle_move` を横取りし、(loop,day,seat,card,target) → (実値, 成分, danger)
    を記録する（値は本体そのもの＝挙動同一）。"""
    orig = hp._b110_idle_move
    cap: dict = {}

    def rec(o, view, ctx, danger_board):
        v = orig(o, view, ctx, danger_board)
        if v is None:
            return v
        # 成分の再構成（本体と同じ材料・観測専用。合計はvとクロスチェックする）
        parts = {"base": hp._B110_IDLE_BASE}
        if getattr(hp, "_loop_lost", False):
            parts["lost(負け確)"] = hp._B110_IDLE_LOST
        tgt = o.get("target")
        if any(p.get("owner") == "mastermind" and p.get("target_kind") == "character"
               and p.get("target") == tgt for p in view.get("placements", [])):
            parts["interfere(mm札干渉)"] = hp._B110_IDLE_INTERFERE
        c = next((x for x in view.get("characters", []) if x.get("name") == tgt), None)
        focus = danger_board or getattr(hp, "_observed_defeat_board", None)
        dest = _move_dest(c.get("area"), o.get("card")) if c else None
        if focus and c and c.get("area") == focus and dest != focus:
            parts["focus_out(焦点外し)"] = hp._B110_IDLE_FOCUS_OUT
            n_unknown = sum(1 for x in view.get("characters", [])
                            if x.get("alive") and x.get("area") == focus
                            and x.get("name") != tgt
                            and hp._gini.get(x.get("name"), 0.0) > 0.02)
            parts["info(情報近似)"] = min(1.5, hp._B110_IDLE_INFO * n_unknown)
        k = (view.get("loop"), view.get("day"), view.get("seat"),
             o.get("card"), o.get("target"))
        cap[k] = (v, parts, danger_board, focus,
                  (c.get("area") if c else None), dest)
        return v

    hp._b110_idle_move = rec
    return cap


# ---------------------------------------------------------------- why --------

def replay(path, hp: ProbedProtagonist, focus_keys, verbose: bool) -> tuple[int, int]:
    _meta, decisions = _load(path)
    idle_cap = _hook_idle(hp)
    n_same = n_all = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        chosen = hp.decide(d["view"], d["decision"], d["options"])
        same = _clean(chosen) == _clean(d["chosen"])
        n_all += 1
        n_same += bool(same)
        k3 = (d["loop"], d["day"], d.get("actor"))
        if not verbose or d["decision"] != "set_card" or k3 not in focus_keys:
            continue
        view = d["view"]
        rec = hp.records[-1]
        mark = "＝棋譜どおり" if same else f"≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        print(f"--- L{d['loop']}D{d['day']} {d.get('actor')}: "
              f"選択={_fmt(_clean(chosen))} {mark}")
        scored = rec["scored"]
        print("      [上位6]")
        for s, o in scored[:6]:
            print(f"        {s:8.2f} {_fmt(o)}")
        # 次点＝選択と同一(card,target)でない最良の候補
        ck = _key(_clean(chosen))
        runner = next(((s, o) for s, o in scored if _key(o) != ck), None)
        cs = next((s for s, o in scored if _key(o) == ck), None)
        print(f"      [選択スコア] {cs}  [次点] "
              f"{runner[0]:.2f} {_fmt(runner[1])}" if runner else "      [次点] なし")
        band = "床帯(<8.0＝実効手なし)" if (cs is not None and cs < EFFECTIVE_MIN) \
            else "実効帯(>=8.0)"
        print(f"      [帯] 選択={band}")
        print("      [移動候補の採点]")
        for s, o in scored:
            if o.get("card") in _MOVE_TOGGLE:
                ik = (d["loop"], d["day"], view.get("seat"),
                      o.get("card"), o.get("target"))
                extra = ""
                if ik in idle_cap:
                    v, parts, danger, focus, src, dest = idle_cap[ik]
                    ptxt = "+".join(f"{n}={x}" for n, x in parts.items())
                    chk = "OK" if abs(min(sum(parts.values()), hp._B110_IDLE_CAP) - v) < 1e-9 \
                        else "★成分と不一致"
                    extra = (f"  [B110 idle={v} ({ptxt}) 検算{chk}"
                             f" {src}→{dest} focus={focus}]")
                print(f"        {s:8.2f} {_fmt(o)}{extra}")
        # 文脈：danger・板・belief（カルティスト等の役職周辺確率＝仮説(a)の判定材料）
        est = rec.get("estimates", {})
        anr = json.dumps(view.get("board_anyaku"), ensure_ascii=False)
        print(f"      board_anyaku={anr}  odb={est.get('_observed_defeat_board')}  "
              f"loop_lost={est.get('_loop_lost')}")
        marg = hp._belief.role_marginals()
        for name in ("黒猫", "巫女"):
            m = marg.get(name, {})
            top = sorted(m.items(), key=lambda x: -x[1])[:4]
            print(f"      P(役職|{name})=" +
                  json.dumps({r: round(p, 4) for r, p in top}, ensure_ascii=False))
        areas = {c["name"]: c["area"] for c in view.get("characters", [])
                 if c.get("alive")}
        print(f"      配置={json.dumps(areas, ensure_ascii=False)}")
    return n_same, n_all


def cmd_why(args) -> int:
    print("===== B-192 why：教材棋譜の往復席の採点内訳 =====")
    print("--- パスA：現行main既定のまま再生 ---")
    hp = ProbedProtagonist(0, top=4000)
    a = replay(args.log, hp, TARGETS, verbose=True)
    print(f"[probe] パスA bit一致 = {a[0]}/{a[1]}")
    print()
    print("--- パスB：B189_KINSHI_FUTILE=False（棋譜を生んだ旧常駐モジュール相当）---")
    hp_b = ProbedProtagonist(0, top=4000)
    hp_b.B189_KINSHI_FUTILE = False
    b = replay(args.log, hp_b, TARGETS, verbose=True)
    print(f"[probe] パスB bit一致 = {b[0]}/{b[1]}")
    print()
    print("--- パスC（アブレーション）：_B110_IDLE_ON=False ＋ B189=False ---")
    hp_c = ProbedProtagonist(0, top=4000)
    hp_c.B189_KINSHI_FUTILE = False
    hp_c._B110_IDLE_ON = False
    c = replay(args.log, hp_c, TARGETS, verbose=True)
    print(f"[probe] パスC bit一致 = {c[0]}/{c[1]}")
    return 0


# ---------------------------------------------------------------- phase1 -----

def cmd_phase1(args) -> int:
    """Phase 1 フラグの単局前後比較（教材4席・4組合せ）。"""
    _meta, decisions = _load(args.log)
    combos = [("OFF", False, False), ("(i)降格のみ", True, False),
              ("(ii)自壊ガードのみ", False, True), ("両方", True, True)]
    for label, fi, fii in combos:
        print(f"===== {label}  (B192_TEST_INTERFERE_DISCOUNT={fi} "
              f"B192_KEEP_OWN_PAIR={fii}) =====")
        hp = ProbedProtagonist(0, top=4000)
        hp.B192_TEST_INTERFERE_DISCOUNT = fi
        hp.B192_KEEP_OWN_PAIR = fii
        for d in decisions:
            if d.get("actor") == "mastermind":
                continue
            chosen = hp.decide(d["view"], d["decision"], d["options"])
            k3 = (d["loop"], d["day"], d.get("actor"))
            if d["decision"] != "set_card" or k3 not in TARGETS:
                continue
            kifu = _clean(d["chosen"])
            mark = "＝棋譜どおり" if _clean(chosen) == kifu \
                else f"≠棋譜（棋譜={_fmt(kifu)}）"
            print(f"  L{d['loop']}D{d['day']} {d.get('actor')}: "
                  f"選択={_fmt(_clean(chosen))} {mark}")
            for s, o in hp.records[-1]["scored"]:
                if o.get("card") in _MOVE_TOGGLE \
                        and o.get("target") in ("黒猫", "巫女", "医者"):
                    print(f"      {s:8.2f} {_fmt(o)}")
    return 0


# ---------------------------------------------------------------- scale ------

def cmd_scale(args) -> int:
    from dataclasses import replace
    from collections import Counter, defaultdict

    from agents import HeuristicMastermind
    from arena.benchmark import benchmark_scripts
    from sim import run_game

    days = args.days
    print(f"===== B-192 scale：{days}日級ベンチ全局の往復席カウント =====")
    outcomes = Counter()
    multi_groups = []         # 同一(loop,キャラ)へ主人公移動2席以上（[席=(day,seat,card,score,idle)]）
    move_seats_total = 0
    floor_moves_total = 0
    n_games = 0
    for name, seed, sc in benchmark_scripts(days=days):
        n_games += 1
        probe = replace(sc, loops=8)
        mm = HeuristicMastermind(seed)
        hp = ProbedProtagonist(seed, top=4000)
        idle_cap = _hook_idle(hp)
        state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        fb = any(e.get("event") == "final_battle" for e in state.history)
        if state.winner == "protagonist" and not fb:
            outcomes["defense"] += 1
        elif fb:
            outcomes["fb_win" if state.winner == "protagonist" else "fb_loss"] += 1
        else:
            outcomes["loss"] += 1
        # 主人公の移動席を (loop, target) でグループ化
        groups: dict = defaultdict(list)
        for rec in hp.records:
            if rec["decision"] != "set_card":
                continue
            ch = rec["chosen"]
            if ch.get("card") not in _MOVE_TOGGLE:
                continue
            move_seats_total += 1
            ck = _key(_clean(ch))
            cs = next((s for s, o in rec["scored"] if _key(o) == ck), None)
            if cs is not None and cs < EFFECTIVE_MIN:
                floor_moves_total += 1
            ik = (rec["loop"], rec["day"], rec["seat"], ch["card"], ch["target"])
            idle = idle_cap.get(ik)
            groups[(rec["loop"], ch["target"])].append(
                (rec["day"], rec["seat"], ch["card"], cs,
                 (idle[0] if idle else None)))
        for (loop, tgt), seats in groups.items():
            if len(seats) >= 2:
                multi_groups.append((f"{name}#{seed}", loop, tgt, seats))
    print(f"[sanity] 局数={n_games}  結末={dict(outcomes)}")
    print(f"[全体] 主人公の移動席 総数={move_seats_total}  "
          f"うち床帯(<{EFFECTIVE_MIN})={floor_moves_total}")
    # 分類：
    #  net-zero   ＝グループ内トグルXOR=(0,0)（偶数往復＝正味変位ゼロ）
    #  reversal   ＝隣接する同一トグルのペア（1席で行き、次の席の寄与がそれを打ち消す）
    #               ※奇数回の振動（教材の黒猫L2＝←→×3）はこちらで拾う
    #  floor_pair ＝reversal ペアのうち戻し側の席が床帯（<8.0）＝採点根拠が消えているのに
    #               往復した席＝浪費と断定できる
    n_netzero = 0
    per_char = Counter()
    seats_in_multi = 0
    rev_pairs = 0
    rev_pair_floor_back = 0
    rev_pair_both_eff = 0
    all_floor_groups = 0
    for game, loop, tgt, seats in multi_groups:
        per_char[tgt] += len(seats)
        seats_in_multi += len(seats)
        x = y = 0
        for _d, _s, card, _sc, _iv in seats:
            tx, ty = _MOVE_TOGGLE[card]
            x ^= tx
            y ^= ty
        if (x, y) == (0, 0):
            n_netzero += 1
        if all(s[3] is not None and s[3] < EFFECTIVE_MIN for s in seats):
            all_floor_groups += 1
        i = 0
        while i + 1 < len(seats):
            if _MOVE_TOGGLE[seats[i][2]] == _MOVE_TOGGLE[seats[i + 1][2]]:
                rev_pairs += 1
                back_sc = seats[i + 1][3]
                if back_sc is not None and back_sc < EFFECTIVE_MIN:
                    rev_pair_floor_back += 1
                fwd_sc = seats[i][3]
                if (fwd_sc is not None and fwd_sc >= EFFECTIVE_MIN
                        and back_sc is not None and back_sc >= EFFECTIVE_MIN):
                    rev_pair_both_eff += 1
                i += 2
            else:
                i += 1
    print(f"[複数移動] 同一キャラ×同一ループに移動2席以上のグループ={len(multi_groups)}  "
          f"関与席={seats_in_multi}")
    print(f"[複数移動] うち正味変位ゼロ(XOR=0)={n_netzero}  "
          f"全席床帯={all_floor_groups}")
    print(f"[往復ペア] 隣接同一トグル（行って戻す）={rev_pairs}  "
          f"うち戻し席が床帯(<{EFFECTIVE_MIN}＝浪費断定)={rev_pair_floor_back}  "
          f"両席とも実効帯(>=8.0＝毎回理由あり)={rev_pair_both_eff}")
    print(f"[キャラ別・複数移動の席数] {dict(per_char.most_common())}")
    if args.detail:
        for game, loop, tgt, seats in multi_groups:
            rows = "  ".join(f"D{d}{s}:{c}({sc if sc is None else round(sc, 2)}"
                             f"/idle={iv})" for d, s, c, sc, iv in seats)
            print(f"    {game} L{loop} {tgt}: {rows}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-192 Phase 0：往復席のプローブ")
    ap.add_argument("cmd", choices=["why", "scale", "phase1"])
    ap.add_argument("--log", default=str(LOG_REMATCH))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args(argv)
    return {"why": cmd_why, "scale": cmd_scale, "phase1": cmd_phase1}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
