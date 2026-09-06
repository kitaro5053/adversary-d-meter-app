# -*- coding: utf-8 -*-
"""B-239：族B（過剰行動／自傷手）の**物差し**を設計・実測するプローブ。

★読み取り専用＝`agents/` `sim/` `engine/` `rules/` には一切触れない。
挙動の書き換えはせず、`sim.flow` の2つの名前（`to_engine_board` / `resolve_action_phase`）を
**戻り値を素通しするラッパ**に一時差し替えして観測するだけ（必ず復元）。

--------------------------------------------------------------------------
## 中心となる測り方＝「席の1手抜き反実仮想（seat drop-out）」

行動解決フェイズの盤面（6枚全公開＝**公開情報**）に対し、
**主人公の3席それぞれについて「その1枚を置かなかった場合」の解決結果**を
`engine.resolve_action_phase` で決定的に再計算し、実際の解決結果と比較する。

- **無効席 (null)** ＝ 抜いても解決結果が**1ビットも変わらない**（＝その札は何もしていない）。
- **自傷席 (harm)** ＝ 抜いた方が**公開指標の意味で良かった**：
  - `harm_anyaku`   … 実際の方が暗躍カウンターの増加が**多い**
  - `harm_unrest`   … 実際の方が不安カウンターの増加が**多い**
  - `harm_goodwill` … 実際の方が友好カウンターの増加が**少ない**
  - `harm_meet`     … 実際の方が「危険ペアの同室数」が**多い**（＝退避のつもりが合流）
- **有効席 (good)** ＝ 抜くと上記のいずれかが悪化する（＝その札は仕事をしている）。

★「危険ペア」は**公開情報のみ**で作る＝**過去ループの `death` イベントの
（犠牲者, 同室だった者）ペア**（B-238 `converge` と同じ定義）。神視点（配役・rule Y）は使わない。

★型別の内訳（タスク指定の (i)〜(iv)）も同時に数える：
  - (i)   `dup_kinshi`  … 同ターンに主人公の `暗躍禁止` が2枚以上（`rules/10:25,61`＝全滅）
  - (ii)  `compose`     … 主人公の移動札が mm の移動札と**合成**された席
  - (iii) `ban_vs_move` … 主人公の `移動禁止` が実際に移動を打ち消した席／空振りした席
  - (iv)  上記以外は harm/null の分類で拾う

CLI 例:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b239_probe seats --days 3,5 --perm id,rev,h1
    PYTHONHASHSEED=0 ... python -m arena.b239_probe seats --days 3 --perm id --pro-off A
    PYTHONHASHSEED=0 ... python -m arena.b239_probe seats --days 3 --perm id --json out.json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import replace

MOVES = {"移動←→", "移動↑↓", "移動斜め"}
KINSHI = "暗躍禁止"
MOVE_BAN = "移動禁止"
ONCE = {"移動禁止", "不安-1", "友好+2"}


# ------------------------------------------------------------------ 観測の器
class Observer:
    """1局分の席の分類を溜める。"""

    def __init__(self):
        self.rows: list[dict] = []
        self.turns: list[dict] = []

    def counts(self) -> Counter:
        c = Counter()
        for r in self.rows:
            c["seats"] += 1
            c[f"card:{r['card']}"] += 1
            for k in r["tags"]:
                c[k] += 1
                if k in ("null", "harm_any", "harm_pure", "harm_meet", "harm_crit"):
                    c[f"{k}@{r['card']}"] += 1
        for t in self.turns:
            c["turns"] += 1
            for k in t["tags"]:
                c[k] += 1
        return c

    def by_loop(self) -> dict:
        """ループ番号ごとの内訳（★深いループほど危険ペアが増える交絡の統制用）。"""
        out: dict = defaultdict(Counter)
        for r in self.rows:
            c = out[r["loop"]]
            c["seats"] += 1
            for k in r["tags"]:
                c[k] += 1
        for t in self.turns:
            c = out[t["loop"]]
            c["turns"] += 1
            for k in t["tags"]:
                c[k] += 1
        return {str(k): dict(v) for k, v in sorted(out.items())}


_OBS: Observer | None = None
_STATE = None       # 直近に to_engine_board へ渡された GameState


#: 主人公側の切替口（b238_probe の表＋B-205 の暗躍禁止ガードと B-222 を明示）
KNOBS2 = {
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
    "b232": ("hp", "B232_COOL_MATH"),
    "b234": ("bl", "B234_GOSHINBOKU_TRAIT"),
    "A": ("hp", "B221_BREAKER"),
    "B": ("hp", "B222_FERRY_GEOMETRY"),
    "C": ("hp", "B224_COOL_FLOOR"),
    "D": ("hp", "B224_WEAK_MATCH"),
    "225": ("hp", "B225_REPEAT_AWARE_BREAK"),
    "227": ("hp", "B227_IRON_BAND"),
    "b205": ("hp", "B205_KINSHI_GUARD"),
}


@contextmanager
def env2(perm: str = "id", pro_off: tuple = ()):
    """perm の着脱＋主人公切替口の一時 OFF（必ず復元）。b238_probe.env の拡張版。"""
    from arena.tie_noise import install_perm, uninstall_perm
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    saved: list[tuple[object, str, object]] = []
    try:
        for k in pro_off:
            if k not in KNOBS2:
                raise SystemExit(f"未知の主人公切替口: {k}")
            where, attr = KNOBS2[k]
            obj = holders[where]
            saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, False)
        install_perm(perm)
        yield
    finally:
        uninstall_perm()
        for obj, attr, val in reversed(saved):
            setattr(obj, attr, val)


def _stub_cultist(target, target_kind):
    """反実仮想用のカルティスト無視ポリシー（脚本家AIの既定＝常に無視・A-42）。

    ★実対局の解決には使わない（実対局は元の cb をそのまま渡す）。
    """
    return True


def _outcome(board, adj):
    """解決結果を比較可能な公開ベクトルへ写す。

    ★`adj.unrest` / `adj.goodwill` は**カードが置かれた対象しか入っていない**ので、
    そのまま合計すると「札を抜くとキーごと消える＝合計が減る」という**見かけの差**が出る
    （初版のバグ＝`不安-1` が軒並み harm_unrest に化けていた）。
    ∴ **生存キャラ全員**について「解決後の値（無ければ解決前の値）」で埋める。
    """
    areas, unrest, goodwill = {}, {}, {}
    for n, c in board.characters.items():
        if not c.alive:
            continue
        areas[n] = adj.moves.get(n, c.area)
        cr = adj.unrest.get(n)
        unrest[n] = cr.final if cr is not None else c.unrest
        cg = adj.goodwill.get(n)
        goodwill[n] = cg.final if cg is not None else c.goodwill
    return {
        "areas": areas,
        "unrest": unrest,
        "goodwill": goodwill,
        # delta は「増分」＝対象に無い＝0 なので、こちらはキー欠落でも合計は同じ
        "anyaku": {t: tr.delta for t, tr in adj.targets.items() if tr.delta},
    }


def _crit_set(out):
    """不安臨界（＝キャラカードの公開情報）に達しているキャラの集合。"""
    from engine.data import unrest_threshold_of
    s = set()
    for n, v in out["unrest"].items():
        th = unrest_threshold_of(n)
        if th is not None and v >= th:
            s.add(n)
    return s


def _totals(out, danger_pairs):
    """公開指標の合算（暗躍増・不安増・友好増・危険同室ペア数）。"""
    a = sum(out["anyaku"].values())
    u = sum(out["unrest"].values())
    g = sum(out["goodwill"].values())
    meet = 0
    ar = out["areas"]
    for x, y in danger_pairs:
        if ar.get(x) is not None and ar.get(x) == ar.get(y):
            meet += 1
    return a, u, g, meet


def _danger_pairs(state):
    """公開情報のみの「危険ペア」＝過去ループの死亡イベントの（犠牲者, 同室者）。"""
    pairs = set()
    for e in state.history:
        if e.get("event") != "death":
            continue
        if (e.get("loop") or 0) >= state.loop_no:
            continue
        v = e.get("name")
        for p in (e.get("present") or ()):
            if p != v and v is not None:
                pairs.add(tuple(sorted((v, p))))
    return pairs


def _classify_turn(board, state):
    """1ターンの主人公3席を分類して Observer に積む。"""
    from engine import resolve_action_phase
    obs = _OBS
    if obs is None:
        return
    pro_idx = [i for i, p in enumerate(board.placements) if p.is_protagonist]
    if not pro_idx:
        return
    danger = _danger_pairs(state)

    base_board = copy.deepcopy(board)
    base_adj = resolve_action_phase(base_board, cultist_ignore=_stub_cultist)
    base_out = _outcome(board, base_adj)
    b_a, b_u, b_g, b_m = _totals(base_out, danger)
    b_crit = _crit_set(base_out)

    mm_move_targets = {p.target for p in board.placements
                       if not p.is_protagonist and p.card in MOVES
                       and p.target_kind == "character"}
    mm_any_targets = defaultdict(int)
    for p in board.placements:
        if not p.is_protagonist and p.card in ("暗躍+1", "暗躍+2"):
            mm_any_targets[(p.target_kind, p.target)] += 1
    n_kinshi = sum(1 for p in board.placements
                   if p.is_protagonist and p.card == KINSHI)

    for i in pro_idx:
        p = board.placements[i]
        cf = copy.deepcopy(board)
        del cf.placements[i]
        adj = resolve_action_phase(cf, cultist_ignore=_stub_cultist)
        out = _outcome(cf, adj)
        c_a, c_u, c_g, c_m = _totals(out, danger)

        tags = []
        # ★不安臨界（公開情報＝キャラカード記載）の押し上げ／引き下げ
        c_crit = _crit_set(out)
        if b_crit - c_crit:
            tags.append("harm_crit")       # 自分の札で誰かを事件可能状態にした
        if c_crit - b_crit:
            tags.append("good_crit")       # 自分の札で誰かを臨界から外した
        same = (out == base_out)
        if same:
            tags.append("null")
            if p.card in ONCE:
                tags.append("null_once")   # 1/loop 札を空打ちした席
        else:
            worse = []
            if b_a > c_a:
                worse.append("harm_anyaku")
            if b_u > c_u:
                worse.append("harm_unrest")
            if b_g < c_g:
                worse.append("harm_goodwill")
            if b_m > c_m:
                worse.append("harm_meet")
            better = (b_a < c_a) or (b_u < c_u) or (b_g > c_g) or (b_m < c_m)
            if worse:
                tags.extend(worse)
                tags.append("harm_any")
                if not better:
                    tags.append("harm_pure")   # 良い面が一つも無い＝純損
                else:
                    tags.append("harm_mixed")
                if "harm_meet" in worse:
                    if p.card in MOVES:
                        tags.append("harm_meet_move")
                    elif p.card == MOVE_BAN:
                        tags.append("harm_meet_ban")
                    else:
                        tags.append("harm_meet_other")
                    if p.card in MOVES and p.target in mm_move_targets:
                        tags.append("harm_meet_compose")
            elif better:
                tags.append("good")
            else:
                # 盤面は変わるが4つの公開指標はどれも同値（例＝無害な位置替え）
                tags.append("neutral")

        # ---- 型別（タスク (i)〜(iii)）----
        if p.card == KINSHI and n_kinshi >= 2:
            tags.append("t_dup_kinshi")
        if p.card in MOVES and p.target_kind == "character" \
                and p.target in mm_move_targets:
            tags.append("t_compose")
        if p.card == MOVE_BAN:
            if p.target in mm_move_targets:
                tags.append("t_ban_hit")
            else:
                tags.append("t_ban_void")
        if p.card == KINSHI and mm_any_targets.get((p.target_kind, p.target), 0) == 0:
            tags.append("t_kinshi_void")

        obs.rows.append({
            "loop": state.loop_no, "day": state.day,
            "owner": p.owner, "card": p.card,
            "target": p.target, "kind": p.target_kind,
            "tags": tags,
        })

    # ---- ターン単位＝「今日は3席とも打たない方が良かったか」 ----
    cf = copy.deepcopy(board)
    cf.placements = [q for j, q in enumerate(board.placements) if j not in set(pro_idx)]
    adj0 = resolve_action_phase(cf, cultist_ignore=_stub_cultist)
    out0 = _outcome(cf, adj0)
    z_a, z_u, z_g, z_m = _totals(out0, danger)
    ttags = []
    if out0 == base_out:
        ttags.append("turn_allnull")
    else:
        worse = (b_a > z_a) or (b_u > z_u) or (b_g < z_g) or (b_m > z_m)
        better = (b_a < z_a) or (b_u < z_u) or (b_g > z_g) or (b_m < z_m)
        if worse and not better:
            ttags.append("turn_pass_better")   # ★「打たない方が強い日」（1ターン視野）
        elif worse:
            ttags.append("turn_mixed")
        elif better:
            ttags.append("turn_good")
        else:
            ttags.append("turn_neutral")
    obs.turns.append({"loop": state.loop_no, "day": state.day, "tags": ttags})


@contextmanager
def instrument():
    """sim.flow の2名前をラップ（戻り値は素通し＝挙動不変）。"""
    import sim.flow as flow
    global _STATE
    orig_teb = flow.to_engine_board
    orig_res = flow.resolve_action_phase

    def teb(state):
        global _STATE
        _STATE = state
        return orig_teb(state)

    def res(board, **kw):
        # ★実対局の解決の**前**に盤面を退避（orig が board を触っても観測が汚れないように）
        snap = copy.deepcopy(board) if _OBS is not None else None
        adj = orig_res(board, **kw)          # ★実対局の結果はそのまま返す
        try:
            if snap is not None and _STATE is not None:
                _classify_turn(snap, _STATE)
        except Exception as exc:             # noqa: BLE001 観測の失敗で対局を壊さない
            print(f"⚠ 観測失敗: {exc!r}", file=sys.stderr)
        return adj

    flow.to_engine_board = teb
    flow.resolve_action_phase = res
    try:
        yield
    finally:
        flow.to_engine_board = orig_teb
        flow.resolve_action_phase = orig_res
        _STATE = None


# ------------------------------------------------------------------ 実行
def _play(sc, seed, loops=8):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    st, _ = run_game(replace(sc, loops=loops),
                     {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in st.history)
    if st.winner == "protagonist" and not fb:
        return st.loop_no, "defense"
    if fb:
        return loops + 1, ("fb_win" if st.winner == "protagonist" else "fb_loss")
    return loops + 1, "loss"


KEYS = ["seats", "null", "null_once", "harm_any", "harm_pure", "harm_mixed",
        "harm_anyaku", "harm_unrest", "harm_goodwill", "harm_meet",
        "harm_meet_move", "harm_meet_ban", "harm_meet_other", "harm_meet_compose",
        "harm_crit", "good_crit", "good", "neutral",
        "t_dup_kinshi", "t_compose", "t_ban_hit", "t_ban_void", "t_kinshi_void",
        "turns", "turn_allnull", "turn_pass_better", "turn_mixed",
        "turn_good", "turn_neutral"] + [
    f"card:{c}" for c in
    ("移動↑↓", "移動←→", "移動禁止", "友好+1", "友好+2", "不安+1", "不安-1", "暗躍禁止")
] + [
    f"{t}@{c}" for t in ("null", "harm_any", "harm_pure", "harm_meet", "harm_crit")
    for c in ("移動↑↓", "移動←→", "移動禁止", "友好+1", "友好+2", "不安+1", "不安-1", "暗躍禁止")
]


def cmd_seats(args) -> int:
    from arena.benchmark import benchmark_scripts
    global _OBS
    result = {}
    pro_off = tuple(x for x in args.pro_off.split(",") if x)
    mm_off = ()
    for days in [int(d) for d in str(args.days).split(",")]:
        for perm in args.perm.split(","):
            tot = Counter()
            per_game = []
            with env2(perm, pro_off=pro_off), instrument():
                for gname, seed, sc in benchmark_scripts(days=days):
                    _OBS = Observer()
                    ltw, outcome = _play(sc, seed, loops=args.loops)
                    c = _OBS.counts()
                    bl = _OBS.by_loop()
                    _OBS = None
                    tot.update(c)
                    per_game.append({"game": f"{gname}#{seed}", "ltw": ltw,
                                     "outcome": outcome,
                                     "counts": {k: c.get(k, 0) for k in KEYS},
                                     "by_loop": bl})
            key = f"d{days}_{perm}"
            result[key] = {"total": {k: tot.get(k, 0) for k in KEYS},
                           "games": per_game}
            n_def = sum(1 for g in per_game if g["outcome"] == "defense")
            print(f"=== days={days} perm={perm} 防衛={n_def}/{len(per_game)} "
                  f"pro_off={pro_off or '-'} mm_off={mm_off or '-'}")
            s = tot.get("seats", 1) or 1
            for k in KEYS:
                v = tot.get(k, 0)
                if "@" in k and v == 0:
                    continue
                print(f"    {k:<18} {v:>6}   ({100.0*v/s:5.2f}% of seats)")
            # 防衛/非防衛の別
            for label, sel in (("防衛局", lambda g: g["outcome"] == "defense"),
                               ("非防衛局", lambda g: g["outcome"] != "defense")):
                sub = [g for g in per_game if sel(g)]
                if not sub:
                    continue
                ss = sum(g["counts"]["seats"] for g in sub) or 1
                tt = sum(g["counts"]["turns"] for g in sub) or 1
                line = "  ".join(
                    f"{k}={sum(g['counts'][k] for g in sub)}"
                    f"({100.0*sum(g['counts'][k] for g in sub)/ss:.2f}%)"
                    for k in ("null", "harm_any", "harm_pure", "harm_meet",
                              "harm_meet_move", "good"))
                line += ("  turn_pass_better="
                         f"{sum(g['counts']['turn_pass_better'] for g in sub)}"
                         f"({100.0*sum(g['counts']['turn_pass_better'] for g in sub)/tt:.2f}%"
                         f" of {tt}turns)")
                print(f"    [{label} n={len(sub)} seats={ss}] {line}")
            sys.stdout.flush()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print(f"→ {args.json}")
    return 0


def cmd_top(args) -> int:
    """--json の結果から局別ランキングを出す（再測定なし）。"""
    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    for key, blk in data.items():
        print(f"=== {key}")
        gs = sorted(blk["games"], key=lambda g: -g["counts"][args.key])
        for g in gs[:args.top_k]:
            c = g["counts"]
            rate = 100.0 * c[args.key] / (c["seats"] or 1)
            print(f"   {g['game']:<20} {g['outcome']:<8} ltw={g['ltw']} "
                  f"{args.key}={c[args.key]:>3} /{c['seats']:>3} ({rate:5.2f}%)")
    return 0


#: 報告に載せる候補指標（席あたり率で見る）
CAND = ["null", "null_once", "harm_any", "harm_pure", "harm_meet",
        "harm_meet_move", "harm_meet_compose", "harm_crit",
        "t_dup_kinshi", "t_ban_void", "t_kinshi_void"]


MOVE_KEYS = ["card:移動↑↓", "card:移動←→"]


def _agg(games, keys, loop_max=None):
    """局リストを合算。loop_max 指定時は by_loop からその範囲だけ足す。"""
    c = Counter()
    for g in games:
        if loop_max is None:
            for k in keys + ["seats", "turns"]:
                c[k] += g["counts"].get(k, 0)
        else:
            for lp, d in g.get("by_loop", {}).items():
                if int(lp) > loop_max:
                    continue
                for k in keys + ["seats", "turns"]:
                    c[k] += d.get(k, 0)
    return c


def cmd_report(args) -> int:
    """seats の JSON 群（tag=path,... 形式）を読み、指標の質を評価する表を出す。"""
    data = {}
    for spec in args.inputs.split(","):
        tag, _, path = spec.partition("=")
        with open(path, encoding="utf-8") as fh:
            data[tag] = json.load(fh)
    conds = list(next(iter(data.values())).keys())

    print("### 1. 並び非依存性（席あたり率 %）＝ベースライン")
    base = data[args.base]
    hdr = f"{'指標':<20}" + "".join(f"{c:>10}" for c in conds) + f"{'変動幅':>10}"
    print(hdr)
    for k in CAND:
        vals = []
        for c in conds:
            t = base[c]["total"]
            vals.append(100.0 * t.get(k, 0) / (t["seats"] or 1))
        rng = max(vals) - min(vals)
        print(f"{k:<20}" + "".join(f"{v:>10.2f}" for v in vals) + f"{rng:>10.2f}")
    print(f"{'（seats 実数）':<20}"
          + "".join(f"{base[c]['total']['seats']:>10}" for c in conds))
    print(f"{'turn_pass_better%':<20}"
          + "".join(f"{100.0*base[c]['total']['turn_pass_better']/(base[c]['total']['turns'] or 1):>10.2f}"
                    for c in conds))
    print(f"{'★harm_meet_move/移動席%':<20}"
          + "".join(f"{100.0*base[c]['total']['harm_meet_move']/(sum(base[c]['total'][k] for k in MOVE_KEYS) or 1):>10.2f}"
                    for c in conds))

    print("\n### 1b. harm_any の分解（★カード別）")
    for c in conds:
        t = base[c]["total"]
        mv = sum(t.get(f"harm_any@{k}", 0) for k in ("移動↑↓", "移動←→", "移動禁止"))
        un = t.get("harm_any@不安+1", 0)
        print(f"  {c:<8} harm_any={t['harm_any']:<5} = 移動系{mv:<5}(=harm_meet {t['harm_meet']})"
              f" + 不安+1 {un:<5}(=不安+1 を打った席 {t.get('card:不安+1',0)}) "
              f"+ その他 {t['harm_any']-mv-un}")

    print("\n### 2. 非防衛局との差（★ループ深度で統制＝L1..L5 に限定した率 %）")
    for c in conds:
        gs = base[c]["games"]
        dfn = [g for g in gs if g["outcome"] == "defense"]
        bad = [g for g in gs if g["outcome"] != "defense"]
        if not bad:
            print(f"  {c}: 非防衛0局")
            continue
        deep = [g for g in dfn if g["ltw"] >= 5]
        for label, sel in (("防衛全体", dfn), ("防衛(ltw>=5)", deep), ("非防衛", bad)):
            full = _agg(sel, CAND + MOVE_KEYS)
            mv = sum(full[k] for k in MOVE_KEYS) or 1
            cap = _agg(sel, CAND, loop_max=args.loop_max)
            s = cap["seats"] or 1
            line = "  ".join(f"{k}={100.0*cap[k]/s:.2f}" for k in
                             ("null", "harm_any", "harm_meet", "harm_crit"))
            print(f"  {c:<8}{label:<14} n={len(sel):<4} "
                  f"L1..L{args.loop_max}席={s:<5} {line}"
                  f"   [全ループ] harm_meet/移動席={100.0*full['harm_meet']/mv:.2f}"
                  f"({full['harm_meet']}/{mv})")

    print("\n### 3. 単調性（切替口 OFF で指標が悪化するか）＝席あたり率 % の差分（OFF − base）")
    print(f"{'条件':<12}{'防衛数':>8}" + "".join(f"{k:>18}" for k in
          ("null", "harm_any", "harm_pure", "harm_meet", "harm_meet_move", "harm_crit")))
    for tag, d in data.items():
        for c in conds:
            t = d[c]["total"]
            bt = base[c]["total"]
            s = t["seats"] or 1
            bs = bt["seats"] or 1
            n_def = sum(1 for g in d[c]["games"] if g["outcome"] == "defense")
            cells = []
            for k in ("null", "harm_any", "harm_pure", "harm_meet",
                      "harm_meet_move", "harm_crit"):
                v = 100.0 * t.get(k, 0) / s
                b = 100.0 * bt.get(k, 0) / bs
                cells.append(f"{v:6.2f}({v-b:+5.2f})")
            print(f"{tag+'/'+c:<12}{n_def:>8}" + "".join(f"{x:>18}" for x in cells))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-239 族B 物差しプローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["seats", "top", "report"])
    ap.add_argument("--inputs", default="", help="report: tag=path,tag=path,...")
    ap.add_argument("--base", default="base")
    ap.add_argument("--loop-max", dest="loop_max", type=int, default=5)
    ap.add_argument("--days", default="3")
    ap.add_argument("--perm", default="id")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--pro-off", dest="pro_off", default="")
    ap.add_argument("--mm-off", dest="mm_off", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--key", default="harm_any")
    ap.add_argument("--top-k", type=int, default=12)
    a = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    return {"seats": cmd_seats, "top": cmd_top, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
