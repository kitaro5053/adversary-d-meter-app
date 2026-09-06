# -*- coding: utf-8 -*-
"""B-246：**行き先検査**（的C）の計測プローブ（読み取り専用）。

起点＝バックログ §72-41（FableA 整理＝4本の負けが一点に収束した）。
名指しされた真のボトルネック＝

- `PRIORITY["冷却役同行"]`(77.0)＝「解禁済み冷却役を危険事件の犯人候補と同エリアへ寄せる」。
  **どの候補の部屋へ運んでも 77.0** ＝ **行き先が何人の候補を覆うかを見ていない**
  （B-244 疑問手1＝「候補7人中1人しか覆えない席に 77点」）。
- `寄せ`(42.0)＝「脚本家が札を伏せたキャラを不安低減キャラ（医者/アイドル/ナース）の部屋へ寄せる」。
  **行き先の部屋に他に誰が居るかを一切見ない**（B-244 疑問手3・4）。

## 本プローブが数えるもの（すべて decide 時点の材料のみ）

各 `set_card` 席で全候補のスコアを捕捉し（`agents.debug.ProbedProtagonist` と同じ
`max` 横取り＝挙動同一）、以下を独立実装の述語で数える：

1. `esc_seat`   ＝ 実手が `冷却役同行` の席（述語成立 ∧ スコアが 77.0 ちょうど）。
2. `esc_cov`    ＝ その席で**実手の行き先が覆う候補の人数**（distinct）。
3. ★`esc_tie_better` ＝ **同点（実手と同スコア）の別候補に、`冷却役同行` の述語が成立し、
   かつ覆う人数が厳密に多いものが在る席**＝**振り替え先が存在する席**。
   ★これが本チケットの「対抗手の点数を先に数える」（§72-40 判例）の中身＝
   **同点なら振り替えは席を失わない**（点数の競争に勝つ必要が無い）。
4. `esc_any_better` ＝ 同点に限らず options 全体で覆う人数が多い候補が在る席（上限の参考）。
5. `yose_seat` / `yose_bad` / `yose_tie_clean` ＝ `寄せ`(42.0) の同型の数え上げ。
   `bad` ＝ 行き先に**近い危険事件の犯人候補**が居る（＝運び込む先が犯人の部屋）。

## 規律

- production（`agents/ sim/ engine/`）には触れない（クラス属性の退避→復元のみ）。
- 神視点は使わない（配役・犯人は参照しない）。材料は view の公開情報＋
  belief 由来の `_culprit_cands` / `_incident_danger`（B-224/B-241 と同じ情報源）。
- 述語は実装の写しになりうる（同じ材料を見る）＝そのことを報告に明記する。

## CLI

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b246_probe census --days 3 --perm id
    PYTHONHASHSEED=0 ... python -m arena.b246_probe census --days 5 --perm rev --detail 1
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace

from agents.heuristic_protagonist import _MOVE_TOGGLE, _move_dest
from engine.data import goodwill_abilities_of

ESC_SCORE = 77.0
YOSE_SCORE = 42.0
COOLERS = ("医者", "アイドル", "ナース")


# ---------------------------------------------------------------- 環境の着脱
@contextmanager
def pro_flags(**attrs):
    """HeuristicProtagonist のクラス属性を一時的に上書き（退避→復元）。"""
    from agents import HeuristicProtagonist as HP
    saved = []
    try:
        for k, v in attrs.items():
            saved.append((k, getattr(HP, k)))
            setattr(HP, k, v)
        yield
    finally:
        for k, v in reversed(saved):
            setattr(HP, k, v)


#: ★ノイズ対照点用＝既に land 済みの主人公側の切替口（B-239／B-241／B-245 と同じ顔ぶれ）。
CTRL_KNOBS = {
    "b205": ("hp", "B205_KINSHI_GUARD"),
    "b221": ("hp", "B221_BREAKER"),
    "b227": ("hp", "B227_IRON_BAND"),
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b232": ("hp", "B232_COOL_MATH"),
}


@contextmanager
def ctrl_off(names: tuple):
    """対照点＝**無関係な既 land 切替口**を OFF にする（B-239 の作法・退避→復元）。"""
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    saved = []
    try:
        for n in names:
            if not n:
                continue
            if n not in CTRL_KNOBS:
                raise SystemExit(f"未知の対照切替口: {n}")
            where, attr = CTRL_KNOBS[n]
            obj = holders[where]
            saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, False)
        yield
    finally:
        for obj, attr, val in reversed(saved):
            setattr(obj, attr, val)


# ------------------------------------------------------------- 独立実装の述語
def _dest_of(agent, view: dict, o: dict):
    """(移動元キャラ, 行き先) — 移動札×キャラ以外は (None, None)。"""
    if o.get("target_kind") != "character" or o.get("card") not in _MOVE_TOGGLE:
        return None, None
    c = agent._alive(view, o.get("target"))
    if not c:
        return None, None
    return c, _move_dest(c.get("area"), o["card"])


def danger_cands(agent, view: dict) -> dict:
    """{事件日: (危険度, [犯人候補])} — 今日以降・危険度60以上（実装と同じ門）。"""
    today = view.get("day")
    out = {}
    for d, dg in (getattr(agent, "_incident_danger", {}) or {}).items():
        if dg < 60.0 or (today is not None and d < today):
            continue
        cands = (getattr(agent, "_culprit_cands", {}) or {}).get(d, ())
        if cands:
            out[d] = (dg, list(cands))
    return out


def esc_hits(agent, view: dict, o: dict) -> set:
    """この option が `冷却役同行` の述語を満たすなら、**行き先で覆う候補の集合**。

    ★`heuristic_protagonist.py` の当該ブロックの独立実装（同じ材料・同じ門）。
    """
    c, dest = _dest_of(agent, view, o)
    if c is None or dest is None:
        return set()
    tgt = o["target"]
    abis = goodwill_abilities_of(tgt) or []
    cool_abis = [a for a in abis
                 if "不安" in a["name"] and "除去" in a["name"]
                 and c["goodwill"] >= a["hearts"]]
    if not cool_abis:
        return set()
    hits = set()
    for _d, (_dg, cands) in danger_cands(agent, view).items():
        for cn in cands:
            if cn == tgt or cn in hits:
                continue
            cc = agent._alive(view, cn)
            if not cc or cc.get("area") != dest:
                continue
            if any(agent._ability_value(tgt, a["name"], cn, view) >= 45.0
                   for a in cool_abis):
                hits.add(cn)
    return hits


def esc_reach(agent, view: dict, o: dict) -> set:
    """`冷却役同行` の**候補全体**（この tgt が覆いうる候補＝行き先を問わない）。"""
    c, dest = _dest_of(agent, view, o)
    if c is None:
        return set()
    tgt = o["target"]
    abis = [a for a in (goodwill_abilities_of(tgt) or [])
            if "不安" in a["name"] and "除去" in a["name"]
            and c["goodwill"] >= a["hearts"]]
    out = set()
    for _d, (_dg, cands) in danger_cands(agent, view).items():
        for cn in cands:
            if cn == tgt or cn in out:
                continue
            cc = agent._alive(view, cn)
            if not cc:
                continue
            if any(agent._ability_value(tgt, a["name"], cn, view) >= 45.0
                   for a in abis):
                out.add(cn)
    return out


def esc_here(agent, view: dict, o: dict) -> set:
    """`冷却役同行` の tgt が**今いる部屋で既に覆えている**候補（＝動かない方の被覆）。

    ★実装の当該ブロックには `_cc3["area"] == c["area"]` を見る行が在るが本体が `pass`
    （＝何もしない）＝**既に同室でも運ぶ手に 77.0 を払う**。その死角を数える。
    """
    c, _dest = _dest_of(agent, view, o)
    if c is None:
        return set()
    tgt = o["target"]
    abis = [a for a in (goodwill_abilities_of(tgt) or [])
            if "不安" in a["name"] and "除去" in a["name"]
            and c["goodwill"] >= a["hearts"]]
    out = set()
    for _d, (_dg, cands) in danger_cands(agent, view).items():
        for cn in cands:
            if cn == tgt or cn in out:
                continue
            cc = agent._alive(view, cn)
            if not cc or cc.get("area") != c.get("area"):
                continue
            if any(agent._ability_value(tgt, a["name"], cn, view) >= 45.0
                   for a in abis):
                out.add(cn)
    return out


def pull_options(agent, view: dict, scored: list, esc_o: dict) -> list:
    """★もう一方の振り替え軸＝「**冷却役を運ぶ**」でなく「**候補を冷却役の部屋へ引く**」。

    `esc_o` の冷却役 tgt が今いる部屋へ、その冷却役が覆える候補を1人運ぶ option を
    （スコアつきで）列挙する。同じ「同室化」という目的を、別の対象で達成する手。
    """
    c, _ = _dest_of(agent, view, esc_o)
    if c is None:
        return []
    tgt = esc_o["target"]
    area = c.get("area")
    abis = [a for a in (goodwill_abilities_of(tgt) or [])
            if "不安" in a["name"] and "除去" in a["name"]
            and c["goodwill"] >= a["hearts"]]
    coolable = set()
    for _d, (_dg, cands) in danger_cands(agent, view).items():
        for cn in cands:
            if cn == tgt or cn in coolable:
                continue
            if agent._alive(view, cn) and any(
                    agent._ability_value(tgt, a["name"], cn, view) >= 45.0
                    for a in abis):
                coolable.add(cn)
    out = []
    for s, o in scored:
        if o.get("target") not in coolable:
            continue
        oc, odest = _dest_of(agent, view, o)
        if oc is None or odest != area:
            continue
        out.append((s, o))
    return out


def yose_fire(agent, view: dict, o: dict) -> bool:
    """この option が `寄せ`(42.0) の述語を満たすか（独立実装）。"""
    c, dest = _dest_of(agent, view, o)
    if c is None or dest is None:
        return False
    tgt = o["target"]
    mm_char_now = {p["target"] for p in (view.get("placements") or [])
                   if p.get("owner") == "mastermind"
                   and p.get("target_kind") == "character"}
    if tgt not in mm_char_now:
        return False
    if tgt == getattr(agent, "_keyperson", None):
        return False
    if tgt in (getattr(agent, "_killer_suspects", ()) or ()):
        return False
    return any((cl := agent._alive(view, n)) and cl.get("area") == dest
               for n in COOLERS)


def yose_bad(agent, view: dict, o: dict) -> set:
    """`寄せ` の行き先に居る**近い危険事件の犯人候補**（＝運び込む先が犯人の部屋）。"""
    c, dest = _dest_of(agent, view, o)
    if c is None or dest is None:
        return set()
    out = set()
    for _d, (_dg, cands) in danger_cands(agent, view).items():
        for cn in cands:
            if cn == o["target"]:
                continue
            cc = agent._alive(view, cn)
            if cc and cc.get("area") == dest:
                out.add(cn)
    return out


# ------------------------------------------------------------- 計測ラッパ --
@contextmanager
def instrument(sink: list):
    """decide を包んで全候補スコア＋述語の数え上げを記録する（読み取り専用）。"""
    import agents.heuristic_protagonist as _hp_mod
    from agents import HeuristicProtagonist as HP
    orig = HP.decide
    builtin_max = max

    def wrapped(self, view, decision, options):
        cap: dict = {}

        ids = frozenset(id(o) for o in options)

        def spymax(*args, **kw):
            # ★`max(options, key=score)`（決定の本体）だけを拾う。
            #   ★`arena.tie_noise.install_perm` は options を**並べ替えた別リスト**にして
            #     渡すので identity 比較は使えない。中身（dict の id 集合）で一致を見る。
            #   計画層が先に呼ぶことがあるので**最後の一致**を採る（決定は最後）。
            if (args and isinstance(args[0], list) and "key" in kw
                    and len(args[0]) == len(options)
                    and frozenset(id(o) for o in args[0]) == ids):
                try:
                    cap["scored"] = [(kw["key"](o), o) for o in args[0]]
                except Exception:
                    pass
            return builtin_max(*args, **kw)

        had = "max" in _hp_mod.__dict__
        prev = _hp_mod.__dict__.get("max")
        _hp_mod.max = spymax
        try:
            chosen = orig(self, view, decision, options)
        finally:
            if had:
                _hp_mod.max = prev
            else:
                _hp_mod.__dict__.pop("max", None)
        if decision != "set_card":
            return chosen
        scored = cap.get("scored")
        rec = {"loop": view.get("loop"), "day": view.get("day"),
               "seat": view.get("seat"),
               "card": chosen.get("card"), "target": chosen.get("target"),
               "kind": chosen.get("target_kind"), "prov": chosen.get("prov"),
               "scored": scored is not None}
        if scored is None:
            sink.append(rec)
            return chosen
        smap = {id(o): s for s, o in scored}
        s_ch = smap.get(id(chosen))
        rec["score"] = s_ch
        # ---- 冷却役同行 -------------------------------------------------
        # ★スコアで先に刈る（77.0 ちょうど以外は述語を評価しない＝計測コストの削減。
        #   `_base_score` はこの分岐で `PRIORITY["冷却役同行"]` を**素の値で return** する）。
        ch_hits = (esc_hits(self, view, chosen)
                   if (s_ch is not None and abs(s_ch - ESC_SCORE) < 1e-9) else set())
        if ch_hits:
            rec["esc"] = True
            rec["esc_cov"] = len(ch_hits)
            rec["esc_reach"] = len(esc_reach(self, view, chosen))
            tie_best = 0
            any_best = 0
            tie_alt = None
            for s, o in scored:
                if o is chosen:
                    continue
                h = esc_hits(self, view, o)
                if not h:
                    continue
                if len(h) > any_best:
                    any_best = len(h)
                if abs(s - s_ch) < 1e-9 and len(h) > tie_best:
                    tie_best, tie_alt = len(h), o
            rec["esc_tie_best"] = tie_best
            rec["esc_any_best"] = any_best
            if tie_best > len(ch_hits):
                rec["esc_tie_better"] = True
                rec["esc_alt"] = f'{tie_alt["card"]}→{tie_alt.get("target")}'
            if any_best > len(ch_hits):
                rec["esc_any_better"] = True
            # ★死角(1)＝運ぶ前の部屋で既に覆えていた候補（実装の `pass` の跡）
            here = esc_here(self, view, chosen)
            rec["esc_here"] = len(here)
            # ★死角(2)＝別軸の振り替え＝「候補を冷却役の部屋へ引く」
            pulls = pull_options(self, view, scored, chosen)
            rec["esc_pull_n"] = len(pulls)
            if pulls:
                bs, bo = max(pulls, key=lambda x: x[0])
                rec["esc_pull_top"] = round(bs, 2)
                rec["esc_pull_opt"] = f'{bo["card"]}→{bo.get("target")}'
                # 引いた後の被覆＝今の部屋の被覆 +1（引く相手は here に居ない）
                if len(here) + 1 > len(ch_hits):
                    rec["esc_pull_better"] = True
        # ---- 寄せ ---------------------------------------------------------
        if (s_ch is not None and abs(s_ch - YOSE_SCORE) < 1e-9
                and yose_fire(self, view, chosen)):
            rec["yose"] = True
            bad = yose_bad(self, view, chosen)
            rec["yose_bad"] = sorted(bad)
            if bad:
                for s, o in scored:
                    if o is chosen:
                        continue
                    if yose_fire(self, view, o) and not yose_bad(self, view, o):
                        tag = f'{o["card"]}→{o.get("target")}@{round(s, 2)}'
                        if abs(s - s_ch) < 1e-9:
                            rec["yose_tie_clean"] = tag
                            break
                        rec.setdefault("yose_any_clean", tag)
        sink.append(rec)
        return chosen

    HP.decide = wrapped
    try:
        yield
    finally:
        HP.decide = orig


def _play(name: str, seed: int, days: int, loops: int):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import random_script, run_game
    from sim.sample_scripts import SAMPLE_SCRIPTS
    if name.startswith("random_"):
        sc = random_script(name.split("_", 1)[1], seed, days=days)
    else:
        sc = SAMPLE_SCRIPTS[name]()
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(replace(sc, loops=loops),
                        {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return state


def _flags(args) -> dict:
    on = set(x for x in (getattr(args, "on", "") or "").split(",") if x)
    f: dict = {}
    if "escort" in on:
        f["B246_ESCORT_AIM"] = True
    if "yose" in on:
        f["B246_YOSE_AIM"] = True
    if getattr(args, "max_cands", None) is not None:
        f["B246_MAX_CANDS"] = args.max_cands
    return f


def cmd_census(args) -> int:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    tot = Counter()
    per_game: dict = {}
    bench: dict = {}
    cov_hist: Counter = Counter()
    diag: list = []
    if args.why:                    # ★計測専用フック（採点・選択には影響しない）
        import agents.b246_aim as _aim
        _aim.DIAG = diag
    install_perm(args.perm)
    ctrl = tuple(x for x in (getattr(args, "ctrl_off", "") or "").split(",") if x)
    cctx = ctrl_off(ctrl)
    cctx.__enter__()
    ctx = pro_flags(**_flags(args))
    ctx.__enter__()
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            key = f"{name}#{seed}"
            if args.games and key not in set(args.games.split(",")):
                continue
            sink: list = []
            with instrument(sink):
                st = _play(name, seed, args.days, args.loops)
            fb = any(e.get("event") == "final_battle" for e in st.history)
            if st.winner == "protagonist" and not fb:
                ltw, outcome = st.loop_no, "defense"
            elif fb:
                ltw = args.loops + 1
                outcome = "fb_win" if st.winner == "protagonist" else "fb_loss"
            else:
                ltw, outcome = args.loops + 1, "loss"
            bench[key] = (ltw, outcome)
            g = Counter()
            for r in sink:
                g["seat"] += 1
                if not r.get("scored"):
                    g["no_scores"] += 1
                if r.get("esc"):
                    g["esc_seat"] += 1
                    cov_hist[r["esc_cov"]] += 1
                    if r["esc_cov"] < r.get("esc_reach", 0):
                        g["esc_partial"] += 1
                    if r.get("esc_tie_better"):
                        g["esc_tie_better"] += 1
                    if r.get("esc_any_better"):
                        g["esc_any_better"] += 1
                    if r.get("esc_here", 0) >= r["esc_cov"]:
                        g["esc_here_ge"] += 1
                    if r.get("esc_pull_n"):
                        g["esc_pull_avail"] += 1
                    if r.get("esc_pull_better"):
                        g["esc_pull_better"] += 1
                if r.get("yose"):
                    g["yose_seat"] += 1
                    if r.get("yose_bad"):
                        g["yose_bad"] += 1
                        if r.get("yose_tie_clean"):
                            g["yose_tie_clean"] += 1
                        if r.get("yose_any_clean"):
                            g["yose_any_clean"] += 1
                if args.detail and (r.get("esc_tie_better") or r.get("esc_pull_better")
                                    or (args.detail > 1 and r.get("esc"))
                                    or r.get("yose_bad")):
                    print(f"  {key} L{r['loop']}D{r['day']} {r['seat']} "
                          f"{r['card']}→{r['target']} {r.get('score')} "
                          f"esc={r.get('esc')} cov={r.get('esc_cov')}"
                          f"/reach={r.get('esc_reach')}/here={r.get('esc_here')}"
                          f" tie={r.get('esc_tie_best')}"
                          f" any={r.get('esc_any_best')} alt={r.get('esc_alt')}"
                          f" 引き={r.get('esc_pull_opt')}@{r.get('esc_pull_top')}"
                          f" ｜yose={r.get('yose')} bad={r.get('yose_bad')}"
                          f" clean={r.get('yose_tie_clean')}/{r.get('yose_any_clean')}")
            tot.update(g)
            per_game[key] = dict(g)
    finally:
        ctx.__exit__(None, None, None)
        cctx.__exit__(None, None, None)
        uninstall_perm()
        if args.why:
            import agents.b246_aim as _aim
            _aim.DIAG = None
    print(f"days={args.days} perm={args.perm} on={_flags(args) or '-'} "
          f"ctrl_off={ctrl or '-'}: 席={tot['seat']}"
          f" スコア未捕捉={tot['no_scores']}"
          f" ｜★冷却役同行席={tot['esc_seat']}"
          f"（覆い残しあり={tot['esc_partial']}"
          f" ★同点に上位の振り替え先={tot['esc_tie_better']}"
          f" 同点外も含む={tot['esc_any_better']}）"
          f" 運ぶ前の部屋の被覆≧運んだ先={tot['esc_here_ge']}"
          f" 引き手あり={tot['esc_pull_avail']}"
          f" ★引きの方が覆う={tot['esc_pull_better']}）"
          f" ｜寄せ席={tot['yose_seat']}（行き先に犯人候補={tot['yose_bad']}"
          f" 同点に清潔な振り替え先={tot['yose_tie_clean']}"
          f" 同点外も含む={tot['yose_any_clean']}）")
    print(f"  [冷却役同行 実手の被覆人数の分布] {dict(sorted(cov_hist.items()))}")
    if diag:
        kc = Counter(d["kind"] for d in diag)
        print(f"  [★実際に振り替えた席] {dict(kc)}")
        for d in diag:
            print(f"    {d['kind']} L{d['loop']}D{d['day']} {d['seat']} "
                  f"{d['from']} → {d['to']} 点={d['score']} 被覆={d['cov']}")
    n_def = sum(1 for v in bench.values() if v[1] == "defense")
    mean = round(sum(v[0] for v in bench.values()) / len(bench), 3) if bench else 0
    print(f"  [同一走行の per-game ベンチ] 防衛={n_def}/{len(bench)} 平均={mean} "
          f"結末={dict(Counter(v[1] for v in bench.values()))}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"total": dict(tot), "per_game": per_game,
                       "cov_hist": {str(k): v for k, v in cov_hist.items()},
                       "diag": diag,
                       "bench": {k: list(v) for k, v in bench.items()}}, f,
                      ensure_ascii=False, indent=1)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-246 計測プローブ（読み取り専用）")
    ap.add_argument("cmd", choices=["census"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--perm", default="id")
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--games", default="")
    ap.add_argument("--detail", type=int, default=0)
    ap.add_argument("--json", default="")
    ap.add_argument("--on", default="", help="escort,yose（既定 OFF）")
    ap.add_argument("--ctrl-off", default="",
                    help="ノイズ対照点＝無関係な既 land 切替口を OFF（b205,b221,b227,b230,b232）")
    ap.add_argument("--max-cands", type=int, default=None,
                    help="掃引口＝候補集合がこの人数以下の席だけ振り替える")
    ap.add_argument("--why", action="store_true",
                    help="実際に振り替えた席を列挙（計測専用フック）")
    args = ap.parse_args(argv)
    return cmd_census(args)


if __name__ == "__main__":
    raise SystemExit(main())
