# -*- coding: utf-8 -*-
"""B-215 Phase 0：**先置きダミー**（1枚目に囮を置く手筋）の教材全数調査。

★読み取り専用（`agents/` `sim/` `engine/` の判断経路・既定値には触れない）。
B-214 の `arena/b214_probe.py` を **席順（何枚目に置いたか）** の軸で拡張したもの。

問い（発注書 Phase 0）:
  1. 教材の「1枚目がダミー」15枚を全数解剖する
     ＝(どの板／どのカード／その後2枚目3枚目に何を置いたか／主人公が吸われたか／
       同ターンに本命が通ったか)。
  2. ★**先置きが後置きより効いているのか**を数字で出す（吸われ率・効いた率を席順で層別）。
     効いていないなら「先置きは再現する価値がない」＝**負の結果**として報告する。

サブコマンド:
    python -m arena.b215_probe order      # 席順で層別＋1枚目ダミーの全数解剖

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGDIR = REPO / "docs/feedback_logs"

ANYAKU_CARDS = frozenset({"暗躍+1", "暗躍+2"})
KINSHI = "暗躍禁止"


def _load_log(path: Path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    meta = lines[0] if lines and lines[0].get("type") == "meta" else {}
    return meta, [d for d in lines if d.get("type") == "decision"]


def _turns(decisions: list[dict]) -> dict:
    """set_card 決定を (loop, day) 単位へ。★mm の `chosen` は **決定順**に並ぶ＝席順。"""
    turns: dict = defaultdict(lambda: {"mm": [], "pro": [], "mm_view": None,
                                       "pro_view": None})
    for d in decisions:
        if d.get("decision") != "set_card":
            continue
        key = (d.get("loop"), d.get("day"))
        if d.get("actor") == "mastermind":
            turns[key]["mm"].append(d["chosen"])
            if turns[key]["mm_view"] is None:
                turns[key]["mm_view"] = d.get("view")
        else:
            turns[key]["pro"].append((d.get("actor"), d["chosen"], d.get("view")))
            if turns[key]["pro_view"] is None:
                turns[key]["pro_view"] = d.get("view")
    return turns


def _gensou_boards(view: dict | None) -> frozenset:
    if not view:
        return frozenset()
    return frozenset(c.get("area") for c in view.get("characters", [])
                     if c.get("name") == "幻想" and c.get("alive") and c.get("area"))


def _rows_of_turn(t: dict, tag: str) -> list[dict]:
    """このターンのダミー1枚につき1行（席順つき）。"""
    gen = _gensou_boards(t["pro_view"])
    mm = t["mm"]
    kinshi_targets = {(c["target_kind"], c["target"])
                      for _a, c, _v in t["pro"] if c.get("card") == KINSHI}

    honmei = [{"card": c["card"], "target": c["target"], "kind": c["target_kind"],
               "slot": i + 1,
               "blocked": (c["target_kind"], c["target"]) in kinshi_targets}
              for i, c in enumerate(mm) if c["card"] in ANYAKU_CARDS]
    n_through = sum(1 for h in honmei if not h["blocked"])

    rows = []
    for i, c in enumerate(mm):
        if c["target_kind"] != "board" or c["card"] in ANYAKU_CARDS:
            continue
        if c["target"] in gen:
            continue                      # 幻想板＝実効。ダミーではない
        sucked = ("board", c["target"]) in kinshi_targets
        rows.append({
            "tag": tag, "slot": i + 1, "board": c["target"], "card": c["card"],
            "後続": [(j + 1, x["card"], x["target_kind"], x["target"])
                     for j, x in enumerate(mm) if j > i],
            "先行": [(j + 1, x["card"], x["target_kind"], x["target"])
                     for j, x in enumerate(mm) if j < i],
            "本命": honmei, "通った本命": n_through,
            "吸った": sucked, "効いた": bool(sucked and n_through > 0),
            "本命キャラ数": sum(1 for h in honmei if h["kind"] == "character"),
            "本命板数": sum(1 for h in honmei if h["kind"] == "board"),
            "本命が後続にあるか": any(h["slot"] > i + 1 for h in honmei),
            "本命が先行にあるか": any(h["slot"] < i + 1 for h in honmei),
            "mm枚数": len(mm),
        })
    return rows


def cmd_order(args) -> None:
    all_rows: list = []
    for p in sorted(LOGDIR.glob("*.jsonl")):
        _meta, decs = _load_log(p)
        for key, t in sorted(_turns(decs).items(),
                             key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
            for r in _rows_of_turn(t, f"L{key[0]}D{key[1]}"):
                all_rows.append(dict(r, log=p.name))

    n = len(all_rows)
    print(f"=== 教材のダミー 全 {n} 枚（B-214 の 39枚と同定義）===\n")

    # --- (1) 席順での層別 ---------------------------------------------------
    print("=== ★席順での層別（先置き vs 後置き）===")
    print("席 | 枚数 | 吸った | 吸われ率 | 効いた | 効いた率")
    for slot in (1, 2, 3):
        rs = [r for r in all_rows if r["slot"] == slot]
        if not rs:
            continue
        s = sum(1 for r in rs if r["吸った"])
        e = sum(1 for r in rs if r["効いた"])
        print(f" {slot} | {len(rs):4d} | {s:4d} | {s/len(rs):6.0%} | {e:4d} | {e/len(rs):6.0%}")
    for label, pred in (("先置き（1枚目）", lambda r: r["slot"] == 1),
                        ("後置き（2-3枚目）", lambda r: r["slot"] >= 2)):
        rs = [r for r in all_rows if pred(r)]
        s = sum(1 for r in rs if r["吸った"])
        e = sum(1 for r in rs if r["効いた"])
        print(f"{label}: {len(rs)}枚 吸った {s} ({s/max(len(rs),1):.0%})"
              f" / 効いた {e} ({e/max(len(rs),1):.0%})")

    # --- (2) 「本命との前後関係」での層別（席順そのものより機序に近い） -------
    print("\n=== ★本命暗躍との前後関係で層別 ===")
    print("層 | 枚数 | 吸った | 吸われ率 | 効いた | 効いた率")
    strata = (
        ("本命が後にある（＝先置き）", lambda r: r["本命が後続にあるか"] and not r["本命が先行にあるか"]),
        ("本命が前にある（＝後置き）", lambda r: r["本命が先行にあるか"] and not r["本命が後続にあるか"]),
        ("本命が前後両方", lambda r: r["本命が先行にあるか"] and r["本命が後続にあるか"]),
        ("本命なし", lambda r: not r["本命"]),
    )
    for label, pred in strata:
        rs = [r for r in all_rows if pred(r)]
        if not rs:
            print(f"{label} | 0 |")
            continue
        s = sum(1 for r in rs if r["吸った"])
        e = sum(1 for r in rs if r["効いた"])
        print(f"{label} | {len(rs):3d} | {s:3d} | {s/len(rs):6.0%} | {e:3d} | {e/len(rs):6.0%}")

    # --- (3) B-214 の述語（板に本命暗躍なし）と席順のクロス -----------------
    print("\n=== ★B-214 の層（同ターンに板へ本命暗躍があるか）× 席順 ===")
    for lab_b, pred_b in (("板に本命暗躍なし(79%層)", lambda r: r["本命板数"] == 0),
                          ("板に本命暗躍あり(50%層)", lambda r: r["本命板数"] > 0)):
        for lab_s, pred_s in (("先置き", lambda r: r["slot"] == 1),
                              ("後置き", lambda r: r["slot"] >= 2)):
            rs = [r for r in all_rows if pred_b(r) and pred_s(r)]
            if not rs:
                print(f"  {lab_b} × {lab_s}: 0枚")
                continue
            s = sum(1 for r in rs if r["吸った"])
            e = sum(1 for r in rs if r["効いた"])
            print(f"  {lab_b} × {lab_s}: {len(rs):3d}枚 吸った {s} ({s/len(rs):.0%})"
                  f" 効いた {e} ({e/len(rs):.0%})")

    # --- (4) 1枚目ダミーの全数解剖 -----------------------------------------
    first = [r for r in all_rows if r["slot"] == 1]
    print(f"\n=== ★「1枚目がダミー」{len(first)}枚の全数解剖 ===")
    for r in first:
        後 = " / ".join(f"{s}:{c}→{k[:1]}:{t}" for s, c, k, t in r["後続"])
        print(f"  {r['log']} {r['tag']} 板={r['board']} 札={r['card']}"
              f" | 後続= {後 or '(なし)'}"
              f" | 吸った={'○' if r['吸った'] else '×'}"
              f" 効いた={'○' if r['効いた'] else '×'}"
              f" 通った本命={r['通った本命']}/{len(r['本命'])}")
    print("\n  1枚目ダミーの札:",
          sorted(Counter(r["card"] for r in first).items(), key=lambda kv: -kv[1]))
    print("  1枚目ダミーの板:",
          sorted(Counter(r["board"] for r in first).items(), key=lambda kv: -kv[1]))
    print("  1枚目ダミーの後続に本命暗躍があるか:",
          f"あり {sum(1 for r in first if r['本命が後続にあるか'])}"
          f" / なし {sum(1 for r in first if not r['本命が後続にあるか'])}")
    print("  1枚目ダミーの後続の対象種別:",
          sorted(Counter(k for r in first for _s, _c, k, _t in r["後続"]).items()))

    # --- (5) 先置きの「1ターン2枚目のダミー」は起きているか -----------------
    multi = Counter((r["log"], r["tag"]) for r in all_rows)
    print("\n=== 1ターンあたりのダミー枚数 ===",
          sorted(Counter(multi.values()).items()))

    if args.out:
        Path(args.out).write_text(
            json.dumps(all_rows, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# orderblind：★「席順そのものは観測されうるか」の実測
# ---------------------------------------------------------------------------

def _rev_mm(seq: list) -> list:
    """listのうち owner=="mastermind" の要素だけを、位置はそのままに逆順へ入れ替える。"""
    idx = [i for i, p in enumerate(seq) if p.get("owner") == "mastermind"]
    if len(idx) < 2:
        return seq
    vals = [seq[i] for i in reversed(idx)]
    out = list(seq)
    for i, v in zip(idx, vals):
        out[i] = v
    return out


def cmd_orderblind(args) -> None:
    """脚本家の**置き順**を反転しても対局結果が変わらないことを実測する。

    根拠（一次ソース）＝`sim/views.py:41-49` `_masked_placements` は
    「位置と持ち主は公開・カード名は自分の分だけ」＝**中身は伏せ**。かつ
    `agents/heuristic_protagonist.py` の `placements` 参照は全て set/any/内包表記
    ＝**順序を読まない**。∴ 席順は相手に観測されない、が構造からの予測。
    ここではそれを**実測**する（予測で断定しない＝規約§4）。

    反転する場所は2つ：
      (a) `sim/views._masked_placements` の出力＝主人公が**手番中**に見る配置の並び
      (b) `sim/flow.to_engine_board` 直前の `state.turn_placements`
          ＝解決順・`cards_revealed`（history）に載る並び
    """
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from arena.benchmark import benchmark_scripts
    import sim.views as views_mod
    import sim.flow as flow_mod

    def _play(reverse: bool) -> list:
        rows = []
        orig_mask = views_mod._masked_placements
        orig_teb = flow_mod.to_engine_board

        def mask(state, seat):
            return _rev_mm(orig_mask(state, seat)) if reverse else orig_mask(state, seat)

        def teb(state, *a, **kw):
            if reverse:
                state.turn_placements = _rev_mm(state.turn_placements)
            return orig_teb(state, *a, **kw)

        views_mod._masked_placements = mask
        flow_mod.to_engine_board = teb
        try:
            for name, seed, sc in benchmark_scripts(days=args.days):
                mm = HeuristicMastermind(seed)
                hp = HeuristicProtagonist(seed)
                state, _log = run_game(replace(sc, loops=args.loops),
                                       {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
                fb = any(e.get("event") == "final_battle" for e in state.history)
                ltw = state.loop_no if (state.winner == "protagonist" and not fb) \
                    else args.loops + 1
                rows.append((name, seed, ltw, state.winner, fb))
        finally:
            views_mod._masked_placements = orig_mask
            flow_mod.to_engine_board = orig_teb
        return rows

    base = _play(False)
    rev = _play(True)
    diff = [(b, r) for b, r in zip(base, rev) if b != r]
    print(f"=== 席順の観測可能性（{args.days}日級 {len(base)}局・脚本家の3枚を逆順に）===")
    print(f"  素の防衛数     : {sum(1 for _n,_s,l,w,f in base if w=='protagonist' and not f)}")
    print(f"  逆順の防衛数   : {sum(1 for _n,_s,l,w,f in rev if w=='protagonist' and not f)}")
    print(f"  ★per-game 不一致: {len(diff)} 局"
          + ("" if not diff else "：" + str(diff[:10])))
    if not diff:
        print("  ⇒ **脚本家の置き順は対局結果に一切影響しない**（bit 一致）")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="B-215 Phase 0 プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("order")
    p1.add_argument("--out")
    p1.set_defaults(func=cmd_order)
    p2 = sub.add_parser("orderblind")
    p2.add_argument("--days", type=int, default=3)
    p2.add_argument("--loops", type=int, default=8)
    p2.set_defaults(func=cmd_orderblind)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
