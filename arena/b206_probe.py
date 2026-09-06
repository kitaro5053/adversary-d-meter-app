# -*- coding: utf-8 -*-
"""B-206 Phase 0：**主人公の自然な移動が「2人きり」を自作する**（当日 or 事件効果との合成）
の再現・規模計測（読み取り専用）。

発生源＝B-205 land（`docs/仮_b205_log/報告_B205.md` §3-1）＝`random_FS#6` L1D2 の
自然手 `移動↑↓→男子学生` が、同ターンの事件〔行方不明〕での犯人流入と合わさって
**{大物, 幻想} の2人きり**を作り、ターン終了フェイズで 大物 が死亡した。

サブコマンド:
    python -m arena.b206_probe why --script random_FS --seed 6 [--days 3] [--loop 1 --day 2]
        # その席の**採点表全数**（score 降順）と、view の公開情報（キャラ位置・公開事件表）、
        #   当該ターンの公開イベント列（事件効果の移動と死亡）を出す。
    python -m arena.b206_probe scan --days {3,5}
        # 両ベンチ全局を回し、「主人公の移動が2人きりを自作した」ターンを全数数える。
        #   分類＝(A) 当日成立（移動直後にその場が {VIP, SK疑い} の2人）
        #         (B) 合成成立（移動直後は2人きりでないが、同ターンの事件効果で流入して成立）
        #   さらに **実害**（同ターン終了時にその VIP が死亡したか）を数える。

★agents/ の判断経路・既定値には触れない（読むだけ）。
測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace


# ---------------------------------------------------------------------------
def _run(seed: int, sc, loops: int = 8, hook=None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hpm
    from sim import run_game
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    prev = hpm.B100_HOOK
    if hook is not None:
        hpm.B100_HOOK = hook
    try:
        state, _log = run_game(replace(sc, loops=loops),
                               {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        hpm.B100_HOOK = prev
    return state


def _find(script: str, seed: int, days: int):
    from arena.benchmark import benchmark_scripts
    for name, sd, sc in benchmark_scripts(days=days):
        if name == script and sd == seed:
            return sc
    return None


def _turn_events(state, loop, day) -> list[dict]:
    return [e for e in state.history
            if e.get("loop") == loop and e.get("day") == day]


# ---------------------------------------------------------------------------
def cmd_why(args) -> int:
    sc = _find(args.script, args.seed, args.days)
    if sc is None:
        print(f"該当の局が無い: {args.script}#{args.seed} days={args.days}")
        return 1

    rows: list[dict] = []

    def hook(agent, view, options, best, score):
        if view.get("loop") != args.loop or view.get("day") != args.day:
            return
        from engine.data import unrest_threshold_of
        from agents.defense_plan import _vip_suspects
        marg = agent._belief.role_marginals()
        elig = []
        for c in view["characters"]:
            th = unrest_threshold_of(c["name"])
            if c.get("alive", True) and c.get("area") and th is not None:
                elig.append((c["name"], c["unrest"], th,
                             "○" if c["unrest"] >= th else "-"))
        tbl = sorted(((score(o), o) for o in options), key=lambda x: -x[0])
        rows.append({
            "belief": {n: {r: round(p, 3) for r, p in sorted(
                d.items(), key=lambda x: -x[1])[:4] if p > 0.01}
                for n, d in marg.items()},
            "vip": {k: round(v, 3) for k, v in _vip_suspects(view, marg).items()},
            "不安臨界": elig,
            "自席placements": view.get("placements"),
            "seat": view.get("seat"),
            "選択": f'{best.get("card")}→{best.get("target")}',
            "prov": best.get("prov"),
            "表": [(round(s, 3), f'{o["card"]}→{o["target"]}'
                    f'({o.get("target_kind")})') for s, o in tbl],
            "characters": [(c["name"], c["area"], c["alive"], c["unrest"],
                            c["goodwill"]) for c in view["characters"]],
            "incidents": view.get("incidents"),
            "board_anyaku": view.get("board_anyaku"),
        })

    state = _run(args.seed, sc, hook=hook)
    print(f"=== {args.script}#{args.seed} days={args.days} L{args.loop}D{args.day}")
    for r in rows:
        print(f"\n--- 席 {r['seat']}  選択={r['選択']} prov={r['prov']}")
        print("  公開キャラ(name, area, alive, unrest, goodwill):")
        for c in r["characters"]:
            print("    " + json.dumps(c, ensure_ascii=False))
        print("  公開事件表: " + json.dumps(r["incidents"], ensure_ascii=False))
        print("  不安臨界(name, unrest, 臨界, 到達): "
              + json.dumps(r["不安臨界"], ensure_ascii=False))
        print("  belief(役職周辺確率 上位): "
              + json.dumps(r["belief"], ensure_ascii=False))
        print("  VIP容疑: " + json.dumps(r["vip"], ensure_ascii=False))
        print("  自席から見える伏せ札: "
              + json.dumps(r["自席placements"], ensure_ascii=False))
        print("  board_anyaku: " + json.dumps(r["board_anyaku"], ensure_ascii=False))
        print(f"  採点表（上位{args.top}）:")
        for s, lab in r["表"][:args.top]:
            print(f"    {s:9.3f}  {lab}")
    print(f"\n=== L{args.loop}D{args.day} の公開イベント列")
    for e in _turn_events(state, args.loop, args.day):
        print("   " + json.dumps(e, ensure_ascii=False)[:400])
    print(f"\n=== 対局結果 winner={getattr(state, 'winner', None)} "
          f"loop={getattr(state, 'loop_no', None)}")
    return 0


# ---------------------------------------------------------------------------
# scan：規模の全数計測
# ---------------------------------------------------------------------------
_SK_ROLE = "シリアルキラー"


def _occ(chars: dict) -> dict:
    """snapshot の characters から エリア→生存者名リスト。"""
    out: dict = {}
    for n, c in chars.items():
        if c.get("alive", True) and c.get("area"):
            out.setdefault(c["area"], []).append(n)
    return out


def cmd_scan(args) -> int:
    """全局を回し、「主人公の移動が2人きりを自作した」ターンを全数数える。

    判定は **神視点のスナップショット**（`state.phase_snapshots`）で行う＝
    これは**規模の計測**であって述語の設計ではない（述語は公開情報だけで書く）。

    手順（各ターン）:
      pre     ＝「脚本家行動フェイズ後」（＝各席が見る盤面と同じ位置情報）
      post_ar ＝「行動解決フェイズ後」
      post_in ＝「事件フェイズ後」
      死亡    ＝ turn_end の `death` イベント（`present` に2人＝2人きり殺害）

    ある死亡（被害者V・エリアA・同席がSK）について:
      - 主人公の移動札が「Aに居たキャラをAの外へ出した」なら **主人公が薄くした**
      - post_ar の時点で A が既に {V, SK} の2人 → 分類 (A) 当日成立
      - post_ar では違い、事件フェイズを経て成立 → 分類 (B) 合成成立
    """
    from arena.benchmark import benchmark_scripts

    tot_turns = 0
    sk_deaths = 0
    rows: list[dict] = []
    lost_games: set = set()

    for name, seed, sc in benchmark_scripts(days=args.days):
        state = _run(seed, sc)
        game = f"{name}#{seed}"
        if getattr(state, "winner", None) != "protagonist":
            lost_games.add(game)
        snaps: dict = {}
        for s in state.phase_snapshots:
            snaps[(s["loop"], s["day"], s["point"])] = s
        # ターン一覧＝cards_revealed が出た (loop, day)
        turn_keys = []
        pls: dict = {}
        for e in state.history:
            if e.get("event") == "cards_revealed":
                turn_keys.append((e.get("loop"), e.get("day")))
                pls[(e.get("loop"), e.get("day"))] = e.get("placements") or []
        tot_turns += len(turn_keys)
        for (loop, day) in turn_keys:
            pre = snaps.get((loop, day, "主人公行動フェイズ後"))
            post_ar = snaps.get((loop, day, "行動解決フェイズ後"))
            post_in = snaps.get((loop, day, "事件フェイズ後"))
            if pre is None or post_ar is None:
                continue
            deaths = [e for e in state.history
                      if e.get("loop") == loop and e.get("day") == day
                      and e.get("event") == "death"
                      and e.get("phase") == "turn_end"
                      and len(e.get("present") or []) == 2]
            if not deaths:
                continue
            occ_pre = _occ(pre["characters"])
            occ_ar = _occ(post_ar["characters"])
            occ_in = _occ((post_in or post_ar)["characters"])
            roles = {n: c["role"] for n, c in pre["characters"].items()}
            for d in deaths:
                present = d.get("present") or []
                victim = d.get("name")
                killer = next((n for n in present if n != victim), None)
                if roles.get(killer) != _SK_ROLE:
                    continue          # SK の2人きり殺害だけを数える
                sk_deaths += 1
                area = (post_in or post_ar)["characters"][victim]["area"]
                # 主人公の移動札が A を薄くしたか
                thinned = []
                for p in pls[(loop, day)]:
                    if p.get("owner") == "mastermind":
                        continue
                    if not str(p.get("card", "")).startswith("移動"):
                        continue
                    if p.get("card") == "移動禁止" or p.get("target_kind") != "character":
                        continue
                    t = p["target"]
                    if t in occ_pre.get(area, []) and t not in occ_ar.get(area, []):
                        thinned.append(f'{p["owner"]}:{p["card"]}→{t}')
                same_day = sorted(occ_ar.get(area, [])) == sorted([victim, killer])
                rows.append({
                    "局": game, "loop": loop, "day": day, "area": area,
                    "被害者": victim, "SK": killer,
                    "分類": "A_当日成立" if same_day else "B_合成成立",
                    "主人公が薄くした": thinned,
                    "post_ar人数": len(occ_ar.get(area, [])),
                    "post_in人数": len(occ_in.get(area, [])),
                })

    print(f"[days={args.days}] 総ターン数={tot_turns}  SKの2人きり殺害={sk_deaths}")
    n_thin = [r for r in rows if r["主人公が薄くした"]]
    n_thin_A = [r for r in n_thin if r["分類"] == "A_当日成立"]
    n_thin_B = [r for r in n_thin if r["分類"] == "B_合成成立"]
    print(f"  うち主人公の移動が殺害エリアを薄くした件＝{len(n_thin)}"
          f"（当日成立 {len(n_thin_A)} ／ 合成成立 {len(n_thin_B)}）")
    g_thin = sorted({r["局"] for r in n_thin})
    g_lost = sorted({r["局"] for r in n_thin if r["局"] in lost_games})
    print(f"  該当局={len(g_thin)}  うち最終的に防衛できなかった局={len(g_lost)}: "
          + json.dumps(g_lost, ensure_ascii=False))
    for r in rows:
        print("  " + json.dumps(r, ensure_ascii=False))
    return 0


def cmd_teleport(args) -> int:
    """★真の機序の規模＝**事件効果で SK が流入して2人きりが成立した**殺害を全数数える。

    公開情報だけで見える材料:
      - 事件の日付と種類＝公開シート（`view["incidents"]`）
      - 事件効果の移動＝公開イベント `{"phase":"incident","event":"move",...}`
        ＝**過去ループで誰が行方不明の犯人だったかは公開で分かる**
      - 各キャラの不安と不安臨界＝盤上の公開カウンター＋キャラカード

    数えるもの:
      (1) SKの2人きり殺害のうち、**SKが事件フェイズにそのエリアへ入った**もの
      (2) 行動解決フェイズ後に「生存者ちょうど1人」のエリアが在った事件日の数
          （＝述語の発火面＝過剰発火の見積り）
    """
    from arena.benchmark import benchmark_scripts

    tot_turns = 0
    n_sk_death = 0
    rows: list[dict] = []
    lone_days = 0
    reloc_days = 0
    lost_games: set = set()

    for name, seed, sc in benchmark_scripts(days=args.days):
        state = _run(seed, sc)
        game = f"{name}#{seed}"
        if getattr(state, "winner", None) != "protagonist":
            lost_games.add(game)
        snaps = {(s["loop"], s["day"], s["point"]): s
                 for s in state.phase_snapshots}
        inc_by_day = {i.day: i.name for i in sc.incidents}
        turn_keys = [(e.get("loop"), e.get("day")) for e in state.history
                     if e.get("event") == "cards_revealed"]
        tot_turns += len(turn_keys)
        for (loop, day) in turn_keys:
            post_ar = snaps.get((loop, day, "行動解決フェイズ後"))
            if post_ar is None:
                continue
            occ_ar = _occ(post_ar["characters"])
            roles = {n: c["role"] for n, c in post_ar["characters"].items()}
            ev = [e for e in state.history
                  if e.get("loop") == loop and e.get("day") == day]
            if inc_by_day.get(day) == "行方不明":
                reloc_days += 1
                if any(len(v) == 1 for v in occ_ar.values()):
                    lone_days += 1
            # 事件フェイズでの移動
            inc_moves = {e.get("name"): e.get("to") for e in ev
                         if e.get("phase") == "incident" and e.get("event") == "move"}
            for d in ev:
                if d.get("event") != "death" or d.get("phase") != "turn_end":
                    continue
                present = d.get("present") or []
                if len(present) != 2:
                    continue
                victim = d.get("name")
                killer = next((n for n in present if n != victim), None)
                if roles.get(killer) != _SK_ROLE:
                    continue
                n_sk_death += 1
                if killer in inc_moves:
                    rows.append({"局": game, "loop": loop, "day": day,
                                 "事件": inc_by_day.get(day),
                                 "SK": killer, "被害者": victim,
                                 "流入先": inc_moves[killer],
                                 "行動解決後の人数": len(occ_ar.get(inc_moves[killer], []))})

    print(f"[days={args.days}] 総ターン数={tot_turns}  SKの2人きり殺害={n_sk_death}")
    print(f"  ★うち **事件効果でSKが流入して成立**＝{len(rows)}")
    g = sorted({r["局"] for r in rows})
    gl = sorted(x for x in g if x in lost_games)
    print(f"  該当局={len(g)}: " + json.dumps(g, ensure_ascii=False))
    print(f"  うち最終的に防衛できなかった局={len(gl)}: "
          + json.dumps(gl, ensure_ascii=False))
    print(f"  【発火面】行方不明の日={reloc_days}  "
          f"うち行動解決後に「1人だけのエリア」が在った日={lone_days}")
    for r in rows:
        print("  " + json.dumps(r, ensure_ascii=False))
    return 0


def cmd_fires(args) -> int:
    """★過剰発火の局別内訳＝(b) の述語が発火した席／実際に採った席を**局ごとに**数える。

    切替口を ON にして全局を回し、各席で `_b206_bait` が非 None を返した option 数と、
    その席が実際に選んだ手が述語の推した手だったかを記録する。
    """
    from agents import heuristic_protagonist as hpm
    from agents import HeuristicProtagonist as HP
    from arena.benchmark import benchmark_scripts

    old = (HP.B206_TELEPORT_BAIT, HP._B206_BAIT_SCORE)
    HP.B206_TELEPORT_BAIT = True
    HP._B206_BAIT_SCORE = args.bait
    print(f"[切替口] B206_TELEPORT_BAIT={HP.B206_TELEPORT_BAIT} "
          f"_B206_BAIT_SCORE={HP._B206_BAIT_SCORE} days={args.days}")
    rows: dict = {}
    try:
        for name, seed, sc in benchmark_scripts(days=args.days):
            game = f"{name}#{seed}"
            cnt = {"発火席": 0, "採った席": 0, "席": []}

            def hook(agent, view, options, best, score, _c=cnt):
                hit = [o for o in options
                       if agent._b206_bait(o, view) is not None]
                if not hit:
                    return
                _c["発火席"] += 1
                took = any((best["card"], best["target"])
                           == (o["card"], o["target"]) for o in hit)
                if took:
                    _c["採った席"] += 1
                _c["席"].append(f'L{view.get("loop")}D{view.get("day")}'
                                f'{view.get("seat")}'
                                f'{"★採用" if took else ""}')

            _run(seed, sc, hook=hook)
            if cnt["発火席"]:
                rows[game] = cnt
    finally:
        (HP.B206_TELEPORT_BAIT, HP._B206_BAIT_SCORE) = old
        hpm.B100_HOOK = None
    tot_f = sum(v["発火席"] for v in rows.values())
    tot_t = sum(v["採った席"] for v in rows.values())
    print(f"発火した局={len(rows)}  発火席 合計={tot_f}  採った席 合計={tot_t}")
    for g, v in rows.items():
        print(f"  {g}: 発火{v['発火席']}席 / 採用{v['採った席']}席  "
              + json.dumps(v["席"], ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-206 Phase 0 プローブ")
    ap.add_argument("cmd", choices=["why", "scan", "teleport", "fires"])
    ap.add_argument("--bait", type=float, default=94.0)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--script", default="random_FS")
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--day", type=int, default=2)
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args(argv)
    return {"why": cmd_why, "scan": cmd_scan, "teleport": cmd_teleport,
            "fires": cmd_fires}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
