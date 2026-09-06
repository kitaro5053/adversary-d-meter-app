# -*- coding: utf-8 -*-
"""B-148：**引き込み脅威が KP 限定**という非対称の射程を数える（★計測のみ・挙動不変）。

## 何を測るか

`agents/defense_plan.py` には「VIP が SK と2人きりで殺される」負け筋の検出が2本ある：

| 関数 | 距離 | 見る対象 |
|---|---|---|
| `_sk_pair_threats`（`:891`） | **同エリア** | `_vip_suspects` ＝ **KP ∪ フレンド** |
| `_threat_sk_setup`（`:995`） | **別エリア（引き込みの仕込み）** | `_suspects(roles,"キーパーソン",_LIKELY_P)` ＝ **KP 限定** |

∴ **同じ負け筋が、距離が1つ離れた瞬間にフレンドについてだけ消える**。
本レーンはこの非対称を「広げたら何席増えるか」で数える（**実際の判断経路は変えない**）。

## 層（B-139b/B-142 の教訓＝上限値を射程と読まない）

- **L1**＝広げた時に**増える脅威の総数**（席数と件数の両方）。
- **L2**＝そのうち**プランナーが実際に手を割く**席
  （＝広げた計画で新脅威が `covered` になり、かつ**採点への入力 `recs` が変わる**席）。
- **L3**＝そのうち**実際に引き込み殺害が起きてループを落としていた**（`b145_audit` の L3）席。
- **★L4**＝**新脅威のせいで KP 側の防衛が薄まる席**（B-72 の失敗モード＝二正面）。
  ＝元の計画では覆えていた負け筋が、広げた計画では `uncovered` に落ちる席。

## 計測器（**二重実装なし**）

- 対局・盤面・L1/L3 の判定は **`arena/b145_audit.py` をそのまま使う**
  （B-147 が `KEEP_MARGINALS` を足したのと同じ作法で `KEEP_SK_SETUP_SHADOW` を足した）。
- 広げた `_threat_sk_setup` は**元関数の本体をそのまま呼ぶ**
  （`_suspects` の KP 問い合わせだけ差し替える）＝述語を書き直していない。
- 挙動不変は `python -m arena.b145_audit verify --days 3` が毎回確認する。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b148_audit thresh --days 3      # ★まず閾値で弾かれないかを見る
    python -m arena.b148_audit count  --days 3
    python -m arena.b148_audit count  --days 5 --json d5.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from agents import HeuristicProtagonist
from agents.defense_plan import _LIKELY_P, _SUSPECT_P

from arena import b145_audit as B


def _switches(days: int, loops: int) -> str:
    return (f"[切替口] B141B_UNLOCK_SAME_DAY="
            f"{HeuristicProtagonist.B141B_UNLOCK_SAME_DAY}"
            f" / B143_YIELD={HeuristicProtagonist.B143_YIELD}"
            f" / B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
            f" / B100_MIX={HeuristicProtagonist.B100_MIX}"
            f" / B145_EVADE_MAX_FRIENDS="
            f"{HeuristicProtagonist.B145_EVADE_MAX_FRIENDS}"
            f" / B146_ODB_TIEBREAK_BOARD_LOSS_ONLY="
            f"{getattr(HeuristicProtagonist, 'B146_ODB_TIEBREAK_BOARD_LOSS_ONLY', '—')}"
            f" / _SUSPECT_P={_SUSPECT_P} _LIKELY_P={_LIKELY_P}"
            f" / days={days} loops={loops}")


def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    B.KEEP_SK_SETUP_SHADOW = True
    c = Counter()
    hits: list[dict] = []
    per_game: list[dict] = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        res = B.audit_game(sc, seed, loops=loops)
        seats = res.get("seats") or []
        l3 = {(lp, dy) for (lp, dy, _v) in res.get("l3_keys") or ()}
        l1 = {(lp, dy) for (lp, dy, _v) in res.get("l1_keys") or ()}
        c["games"] += 1
        c["seats"] += len(seats)
        g = Counter()
        for s in seats:
            if s.get("fr_likely"):
                g["seats_fr_likely"] += 1
            if s.get("sks_base_n"):
                g["seats_base_sk_setup"] += 1
                g["n_base_sk_setup"] += int(s["sks_base_n"])
            new = s.get("sks_new") or []
            if not new:
                continue
            g["L1_seats"] += 1
            g["L1_threats"] += len(new)
            key = (s.get("loop"), s.get("day"))
            if s.get("recs_changed") and s.get("new_covered"):
                g["L2_seats"] += 1
                if key in l3:
                    g["L3_seats"] += 1
            if s.get("thinned"):
                g["L4_seats"] += 1
            hits.append({"script": name, "seed": seed, "outcome": res["outcome"],
                         "loop": s.get("loop"), "day": s.get("day"),
                         "seat": s.get("seat"), "new": new,
                         "recs_changed": bool(s.get("recs_changed")),
                         "new_covered": s.get("new_covered") or [],
                         "thinned": s.get("thinned") or [],
                         "picks0": s.get("picks0"), "picks1": s.get("picks1"),
                         "chosen": s.get("chosen"),
                         "in_L1_turn": key in l1, "in_L3_turn": key in l3})
        for k, v in g.items():
            c[k] += v
        if g.get("L1_seats"):
            per_game.append({"script": name, "seed": seed, **dict(g)})
            print(f"  [新脅威あり] {name} s{seed}: L1席={g['L1_seats']}"
                  f" 件={g['L1_threats']} L2={g.get('L2_seats', 0)}"
                  f" L4={g.get('L4_seats', 0)}", flush=True)
        elif verbose:
            print(f"  {name} s{seed}: 新脅威なし", flush=True)
    return {"days": days, "counts": dict(c), "hits": hits, "per_game": per_game}


def thresh(days: int = 3, loops: int = 8) -> dict:
    """★まず「`_LIKELY_P=0.45` で弾かれて何も起きない」かを確かめる（チケット §4）。

    フレンド確率が各しきい値に届いた席を数えるだけ（対局は同じ経路を1回走らせる）。
    """
    from arena.benchmark import benchmark_scripts

    B.KEEP_SK_SETUP_SHADOW = True
    c = Counter()
    for name, seed, sc in list(benchmark_scripts(days=days)):
        res = B.audit_game(sc, seed, loops=loops)
        for s in (res.get("seats") or []):
            c["seats"] += 1
            if s.get("fr_suspect"):
                c[f"seats_fr>={_SUSPECT_P}"] += 1
            if s.get("fr_likely"):
                c[f"seats_fr>={_LIKELY_P}"] += 1
                c["n_fr_likely"] += len(s["fr_likely"])
            if s.get("kp_likely"):
                c[f"seats_kp>={_LIKELY_P}"] += 1
            if s.get("fr_likely") and s.get("kp_likely"):
                c["seats_both_likely"] += 1
            if s.get("sks_new"):
                c["seats_new_threat"] += 1
    return dict(c)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count", choices=["count", "thresh"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    print(_switches(a.days, a.loops), flush=True)
    if a.cmd == "thresh":
        c = thresh(days=a.days, loops=a.loops)
        print(f"== B-148 しきい値の関門（{a.days}日級・席 {c.get('seats', 0)}）==")
        for k in sorted(c):
            if k != "seats":
                print(f"   {k} = {c[k]}")
        return 0
    res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
              verbose=a.verbose)
    c = res["counts"]
    print(f"== B-148：`_threat_sk_setup` を VIP へ広げた時の射程"
          f"（{a.days}日級 {c.get('games', 0)}局・席 {c.get('seats', 0)}）==")
    print(f"  現行 `sk_setup` を出していた席 = {c.get('seats_base_sk_setup', 0)}"
          f"（脅威 {c.get('n_base_sk_setup', 0)} 件）")
    print(f"  フレンド確率が _LIKELY_P({_LIKELY_P}) 以上の席 = "
          f"{c.get('seats_fr_likely', 0)}")
    print(f"★L1 増える脅威 = {c.get('L1_threats', 0)} 件"
          f"（{c.get('L1_seats', 0)} 席・{len(res['per_game'])} 局）")
    print(f"★L2 プランナーが実際に手を割く席 = {c.get('L2_seats', 0)}")
    print(f"★L3 うち実際に引き込み殺害でループを落としていた席 = {c.get('L3_seats', 0)}")
    print(f"★L4 新脅威のせいで既存の防衛が薄まる席（B-72 二正面）= "
          f"{c.get('L4_seats', 0)}")
    if a.top and res["hits"]:
        print("")
        print("  新脅威の席の一覧（局 / LD席 / 新脅威 / 手を割くか / 薄まり）")
        for h in res["hits"][:a.top]:
            lbl = "; ".join(f"{t['vip']}(p={t['p_vip']}) prob={t['prob']}"
                            f" 折手{len(t['breaks'])}" for t in h["new"])
            print(f"   {h['script']}(s{h['seed']}) L{h['loop']}D{h['day']}"
                  f"#{h['seat']} | {lbl}"
                  f" | recs変化={h['recs_changed']} 覆った={len(h['new_covered'])}"
                  f" | 薄まり={h['thinned'] or 'なし'}"
                  f" | L1ターン={h['in_L1_turn']} L3ターン={h['in_L3_turn']}"
                  f" [{h['outcome']}]", flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
