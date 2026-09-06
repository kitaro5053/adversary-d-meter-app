# -*- coding: utf-8 -*-
"""B-214 Phase 0：**複線演出（板へのダミー配置）** の教材全数調査＋探索コスト実測。

★読み取り専用（`agents/` `sim/` `engine/` の判断経路・既定値には触れない）。

問い（発注書 §3）:
  1. 教材（`docs/feedback_logs/*.jsonl`）の全棋譜で、人間の脚本家が置いたダミーを
     **(どの板／どのカード／何日目／その日の本命は何だったか／主人公が吸われたか)** で全数集計。
     ★「効いたダミー」を数字で定義＝**同ターンに主人公の `暗躍禁止` をその板へ引き寄せ、
       かつ同ターンに別の対象へ置いた暗躍札が通った**（`payoff`）。
  2. `allow_bluff=True` にした時の板候補の増加倍率（合法手列挙のコスト）。
  3. engine 側の健全性（`board_bluffs`＝解決されない／`cards_revealed` で公開される）は
     一次ソースで確認する＝本プローブでは (1)(2) を数える。

サブコマンド:
    python -m arena.b214_probe logs            # 教材の全数集計
    python -m arena.b214_probe optcost         # allow_bluff の候補数・列挙時間の実測

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGDIR = REPO / "docs/feedback_logs"

ANYAKU_CARDS = frozenset({"暗躍+1", "暗躍+2"})
KINSHI = "暗躍禁止"


# ---------------------------------------------------------------------------
# 決定ログ → ターン単位
# ---------------------------------------------------------------------------

def _turns(decisions: list[dict]) -> dict:
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
    """幻想が生存して居るエリア＝そこは非暗躍札も**実効**（`sim/legal.py:70-72`）。
    ★主人公手番時点のエリアによる近似（B-213 Phase 0 の弱点②と同じ）。"""
    if not view:
        return frozenset()
    return frozenset(c.get("area") for c in view.get("characters", [])
                     if c.get("name") == "幻想" and c.get("alive") and c.get("area"))


# ---------------------------------------------------------------------------
# logs：教材の全数調査
# ---------------------------------------------------------------------------

def _load_log(path: Path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    meta = lines[0] if lines and lines[0].get("type") == "meta" else {}
    return meta, [d for d in lines if d.get("type") == "decision"]


def _areas_of(view: dict | None) -> dict:
    """キャラ名 -> エリア（主人公手番時点）。"""
    if not view:
        return {}
    return {c.get("name"): c.get("area") for c in view.get("characters", [])
            if c.get("alive")}


def _analyse_turn(t: dict, tag: str, acc: Counter, rows: list) -> None:
    gen = _gensou_boards(t["pro_view"])
    areas = _areas_of(t["pro_view"])
    mm_by_target: dict = defaultdict(list)
    for c in t["mm"]:
        mm_by_target[(c["target_kind"], c["target"])].append(c["card"])

    kinshi_targets = {(c["target_kind"], c["target"])
                      for _a, c, _v in t["pro"] if c.get("card") == KINSHI}

    # その日の「本命」＝mm が置いた暗躍札（対象つき）と、その通過/阻止
    honmei = []
    for (kind, tgt), cards in mm_by_target.items():
        for card in cards:
            if card in ANYAKU_CARDS:
                blocked = (kind, tgt) in kinshi_targets
                honmei.append({"card": card, "target": tgt, "kind": kind,
                               "blocked": blocked})
    n_through = sum(1 for h in honmei if not h["blocked"])
    acc["mm_anyaku_total"] += len(honmei)
    acc["mm_anyaku_through"] += n_through

    for (kind, tgt), cards in mm_by_target.items():
        if kind != "board":
            continue
        for card in cards:
            acc["mm_board_placements"] += 1
            if card in ANYAKU_CARDS:
                acc["mm_board_anyaku"] += 1
                continue
            if tgt in gen:
                acc["mm_board_gensou_real"] += 1   # 幻想板＝実効。ダミーではない
                continue
            acc["mm_board_dummy"] += 1
            acc[f"dummy_card:{card}"] += 1
            acc[f"dummy_board:{tgt}"] += 1
            acc[f"dummy_day:{tag.split('D')[-1]}"] += 1
            sucked = ("board", tgt) in kinshi_targets
            payoff = sucked and n_through > 0
            # ★構造：同ターンの本命暗躍が「このダミー板と同じエリアに居るキャラ」か
            colo = [h for h in honmei
                    if h["kind"] == "character" and areas.get(h["target"]) == tgt]
            colo_through = [h for h in colo if not h["blocked"]]
            if sucked:
                acc["dummy_sucked"] += 1
            if payoff:
                acc["dummy_payoff"] += 1
                acc[f"payoff_card:{card}"] += 1
            if colo:
                acc["dummy_colocated"] += 1
                if colo_through:
                    acc["dummy_colocated_through"] += 1
                if sucked:
                    acc["dummy_colocated_sucked"] += 1
            rows.append({
                "tag": tag, "board": tgt, "card": card,
                "同板の他札": [c for c in cards if c != card],
                "本命": honmei, "通った本命": n_through,
                "吸った": sucked, "効いた": payoff,
                "同エリア本命": [h["target"] for h in colo],
                "同エリア本命が通った": bool(colo_through),
                "板カウンタ": (t["pro_view"] or {}).get("board_anyaku", {}),
            })
            ba = (t["pro_view"] or {}).get("board_anyaku", {}) or {}
            cur = ba.get(tgt, 0)
            mx = max(ba.values()) if ba else 0
            acc[f"dummy_cur:{cur}"] += 1
            if sucked:
                acc[f"sucked_cur:{cur}"] += 1
            acc["dummy_is_max_board" if (ba and cur == mx and mx > 0)
                else "dummy_not_max_board"] += 1


def cmd_logs(args) -> None:
    total = Counter()
    all_rows: list = []
    per_log = []
    for p in sorted(LOGDIR.glob("*.jsonl")):
        meta, decs = _load_log(p)
        acc = Counter()
        rows: list = []
        for key, t in sorted(_turns(decs).items(),
                             key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
            _analyse_turn(t, f"L{key[0]}D{key[1]}", acc, rows)
        per_log.append((p.name, acc, rows))
        total.update(acc)
        all_rows.extend([dict(r, log=p.name) for r in rows])

    print("=== 教材ごと（板配置／ダミー／吸った／効いた／暗躍 通過/総数）===")
    for name, acc, rows in per_log:
        if not acc["mm_board_placements"] and not acc["mm_anyaku_total"]:
            continue
        print(f"{name} | 板 {acc['mm_board_placements']} | ダミー {acc['mm_board_dummy']}"
              f" | 幻想実効 {acc['mm_board_gensou_real']}"
              f" | 吸った {acc['dummy_sucked']} | 効いた {acc['dummy_payoff']}"
              f" | 暗躍 {acc['mm_anyaku_through']}/{acc['mm_anyaku_total']}")

    d = total["mm_board_dummy"]
    print("\n=== 合計 ===")
    print(f"板配置 {total['mm_board_placements']} 枚"
          f"（暗躍 {total['mm_board_anyaku']}／幻想実効 {total['mm_board_gensou_real']}"
          f"／ダミー {d}）")
    if d:
        print(f"ダミーのうち 吸った {total['dummy_sucked']} ({total['dummy_sucked']/d:.0%})"
              f"／効いた(payoff) {total['dummy_payoff']} ({total['dummy_payoff']/d:.0%})")
    print(f"暗躍札 通過 {total['mm_anyaku_through']}/{total['mm_anyaku_total']}")
    for pref, title in (("dummy_card:", "ダミーに使われた札"),
                        ("dummy_board:", "ダミーを置いた板"),
                        ("dummy_day:", "ダミーを置いた日"),
                        ("payoff_card:", "効いたダミーの札")):
        items = sorted(((k.split(":", 1)[1], v) for k, v in total.items()
                        if k.startswith(pref)), key=lambda kv: (-kv[1], kv[0]))
        if items:
            print(f"  {title}: {items}")

    if d:
        print(f"★同エリア本命つきダミー {total['dummy_colocated']} ({total['dummy_colocated']/d:.0%})"
              f"／うち吸った {total['dummy_colocated_sucked']}"
              f"／うち同エリア本命が通った {total['dummy_colocated_through']}")

    if d:
        cur_items = sorted(((k.split(":")[1], v) for k, v in total.items()
                            if k.startswith("dummy_cur:")))
        suc_items = dict((k.split(":")[1], v) for k, v in total.items()
                         if k.startswith("sucked_cur:"))
        print(f"★ダミー板の暗躍カウンタ（置いた時点）: "
              + " / ".join(f"{c}枚={v}（吸った {suc_items.get(c,0)}）" for c, v in cur_items))
        print(f"★ダミー板が最大カウンタ板か: はい {total['dummy_is_max_board']}"
              f" / いいえ {total['dummy_not_max_board']}")

    print("\n--- ★吸った／効いたダミーの全件 ---")
    for r in all_rows:
        if r["吸った"]:
            print(f"  {r['log']} {r['tag']} 板={r['board']} 札={r['card']}"
                  f" 効いた={r['効いた']} 通った本命={r['通った本命']}"
                  f" 本命={[(h['card'], h['target'], 'x' if h['blocked'] else 'o') for h in r['本命']]}")
    print("\n--- 吸わなかったダミーの全件 ---")
    for r in all_rows:
        if not r["吸った"]:
            print(f"  {r['log']} {r['tag']} 板={r['board']} 札={r['card']}"
                  f" 通った本命={r['通った本命']}"
                  f" 本命={[(h['card'], h['target'], 'x' if h['blocked'] else 'o') for h in r['本命']]}")
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"total": dict(total), "rows": all_rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")


# ---------------------------------------------------------------------------
# optcost：allow_bluff の探索コスト
# ---------------------------------------------------------------------------

def cmd_optcost(args) -> None:
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from sim.legal import set_card_options
    from arena.benchmark import benchmark_scripts

    n_off = n_on = 0
    t_off = t_on = 0.0
    turns = 0
    for name, seed, sc in list(benchmark_scripts(days=args.days))[:args.games]:
        probe = replace(sc, loops=args.loops)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, log = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
        # 局の終了状態では意味がないので、決定ログの options 長を使う
        for dline in log:
            if dline.get("decision") != "set_card" or dline.get("actor") != "mastermind":
                continue
            turns += 1
            n_off += len(dline["options"])
    print(f"=== {args.days}日級 先頭{args.games}局・mm の set_card 決定 {turns} 回 ===")
    print(f"allow_bluff=False の候補総数 : {n_off}"
          f"（平均 {n_off/max(turns,1):.1f} 手/決定）")
    print("※ True 側は状態の再構築が要るため sim/legal を直接叩く measure サブコマンドで測る")


def cmd_measure(args) -> None:
    """実局面で set_card_options(allow_bluff=False/True) を両方呼び、倍率と時間を測る。"""
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from sim import legal as legal_mod
    from arena.benchmark import benchmark_scripts

    stats = {"n": 0, "off": 0, "on": 0, "t_off": 0.0, "t_on": 0.0}
    orig = legal_mod.set_card_options

    def patched(state, owner, allow_bluff=False):
        if owner == "mastermind" and not allow_bluff:
            t0 = time.perf_counter()
            a = orig(state, owner, False)
            t1 = time.perf_counter()
            b = orig(state, owner, True)
            t2 = time.perf_counter()
            stats["n"] += 1
            stats["off"] += len(a)
            stats["on"] += len(b)
            stats["t_off"] += t1 - t0
            stats["t_on"] += t2 - t1
            return a
        return orig(state, owner, allow_bluff)

    legal_mod.set_card_options = patched
    import sim.flow as flow_mod
    if hasattr(flow_mod, "set_card_options"):
        flow_mod.set_card_options = patched
    try:
        games = list(benchmark_scripts(days=args.days))[:args.games]
        for name, seed, sc in games:
            probe = replace(sc, loops=args.loops)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        legal_mod.set_card_options = orig
        if hasattr(flow_mod, "set_card_options"):
            flow_mod.set_card_options = orig
    n = max(stats["n"], 1)
    print(f"=== {args.days}日級 先頭{args.games}局・mm の set_card 決定 {stats['n']} 回 ===")
    print(f"候補数 OFF 平均 {stats['off']/n:.1f} / ON 平均 {stats['on']/n:.1f}"
          f"（倍率 {stats['on']/max(stats['off'],1):.3f}）")
    print(f"列挙時間 OFF {stats['t_off']*1000:.1f}ms / ON {stats['t_on']*1000:.1f}ms")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="B-214 Phase 0 プローブ（読み取り専用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("logs")
    p1.add_argument("--out")
    p1.set_defaults(func=cmd_logs)
    p2 = sub.add_parser("optcost")
    p2.add_argument("--days", type=int, default=3)
    p2.add_argument("--loops", type=int, default=8)
    p2.add_argument("--games", type=int, default=10)
    p2.set_defaults(func=cmd_optcost)
    p3 = sub.add_parser("measure")
    p3.add_argument("--days", type=int, default=3)
    p3.add_argument("--loops", type=int, default=8)
    p3.add_argument("--games", type=int, default=10)
    p3.set_defaults(func=cmd_measure)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
