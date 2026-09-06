# -*- coding: utf-8 -*-
"""B-279 フェーズ0：脚本家AI の3つの弱さ仮説を**数える**計測ハーネス（本番経路は無変更）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を1バイトも import 以外で触らない
（読むだけ）。対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う
＝ラッパを挟まない＝ベンチと**同一の乱数消費**が構造的に保証される。

------------------------------------------------------------------------------
測るもの（起票＝`docs/バックログ_構想メモ_FableA.md` §72-103）
------------------------------------------------------------------------------
**(a) 勝利条件に無関係なボードへの暗躍**
  「そのボードの暗躍が勝利条件に寄与する」の判定は**既存の単一ソース**
  `arena.mm_lint._defeat_boards(view)` をそのまま使う（新しい表を発明しない）。
  同関数の KB 接地＝
    - 守るべき場所＝学校≥2（`rules/40_first_steps.md:47`）
    - 封印されしモノ＝神社≥2（`rules/50_basic_tragedy_x.md:38`）
    - 復讐者の灯火／巨大時限爆弾X＝ボードX≥2（`rules/40:42`／`rules/50:52`）
    - 病院の事件＝病院暗躍1以上で同エリア全滅・2以上で主人公死亡
      （`rules/40:151`／`rules/50:205`）※犯人が臨界に届く見込みのある日だけ
  ★`_defeat_boards` が拾っていない KB 上の板暗躍の用途が1つある＝**ファクター**
  （`rules/50_basic_tragedy_x.md:174-175`＝学校暗躍≥2でミスリーダー能力／
   都市暗躍≥2でキーパーソン能力を得る）。これは「敗北条件そのもの」ではないので
  単一ソースは変えず、**拡張分として別カラムで併記**する（`factor_gain`）。

**(b) 脚本家AI が自分の犯人の不安を下げた席**
  `不安-1`（キャラ対象）／脚本家能力 `unrest_minus`（医者の友好能力を脚本家が使う形）で、
  対象が「**今日以降に事件がある犯人**」であるものを数える。
  機序は `sys.settrace` で `_score_set` / `_score_ability` の**実際に通った return 行**を
  取り、**行番号ではなく行の中身（ソース文字列）**をキーにして集計する
  （規約＝ハードコード禁止の4族「行番号キー」対策）。

**(c) 方針転換の不在**
  同じ (script, seed, day) で、あるループの脚本家の伏せ札3枚が**直前ループと同一**か。
  席単位（1枚ごと）と ターン単位（3枚の多重集合）の両方を数える。
  さらに「**前ループの同じ席が主人公の暗躍禁止で打ち消されていた**」ものを切り出す
  （＝露見済みなのに同じ配置を繰り返した席）。

**(d) ★(a)(b)(c) の外で見つかった機序（教材の実機序）**
  遠隔殺人の打点ゲート（`agents/heuristic.py` の A-56②／A-67／A-71）は
  「殺して得になる的（KP/フレンド/ラバーズ）が **暗躍≥1 で既に居る**、または
  **暗躍+2 が手札に残っていて的を1枚で作れる**」時しか犯人を `reachable_culprits` に
  載せない。∴ **`暗躍+2` を板に切った瞬間**、まだ `暗躍+1` を2日積めば的が作れる
  局面でも遠隔殺人の筋ごと勘定から落ちる。反実仮想（`used_cards` から `暗躍+2` を
  1枚抜いて `_analyze` をやり直す）で、犯人が `reachable` に戻る席を数える。

------------------------------------------------------------------------------
CLI
------------------------------------------------------------------------------
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b279_probe --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b279_probe --days 5
    ... --json out.json      # 生の席テーブルつき
    ... --trace-b            # (b) の機序トレース（重い＝該当席だけ再評価）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace

from engine.data import unrest_threshold_of

# ---------------------------------------------------------------------------
# 暗躍札の種別（KB: rules/10_action_cards.md:38-39）
# ---------------------------------------------------------------------------
_ANYAKU_CARDS = {"暗躍+1": 1, "暗躍+2": 2}


def _is_board_anyaku(rec: dict) -> bool:
    """脚本家の「ボードへの暗躍」1手か（伏せ札 or 脚本家能力）。"""
    ch = rec["chosen"]
    if rec["decision"] == "set_card":
        return (ch.get("target_kind") == "board"
                and ch.get("card") in _ANYAKU_CARDS)
    if rec["decision"] == "mastermind_ability":
        return (ch.get("kind") == "anyaku" and ch.get("target_kind") == "board")
    return False


def _unrest_delta(rec: dict):
    """脚本家の不安操作を (対象, ±1) に正規化（不安操作でなければ None）。"""
    ch = rec["chosen"]
    if rec["decision"] == "set_card" and ch.get("target_kind") == "character":
        if ch.get("card") == "不安+1":
            return ch["target"], 1
        if ch.get("card") == "不安-1":
            return ch["target"], -1
    if rec["decision"] == "mastermind_ability":
        if ch.get("kind") == "unrest":
            return ch.get("target"), 1
        if ch.get("kind") == "unrest_minus":
            return ch.get("target"), -1
    return None


# ---------------------------------------------------------------------------
# ファクター拡張（`_defeat_boards` に無い KB 上の板暗躍の用途）
# ---------------------------------------------------------------------------
def _factor_boards(view: dict) -> set[str]:
    """ファクターが能力を得る板（`rules/50_basic_tragedy_x.md:174-175`）。

    学校暗躍≥2＝ミスリーダー能力（不安供給）／都市暗躍≥2＝キーパーソン能力。
    ファクターが生存して盤上に居る時だけ意味がある。"""
    roles = view.get("roles") or {}
    fac = next((n for n, r in roles.items() if r == "ファクター"), None)
    if not fac:
        return set()
    c = next((c for c in view["characters"] if c["name"] == fac), None)
    if not c or not c.get("alive"):
        return set()
    return {"学校", "都市"}


def _classify_board(view: dict, board: str) -> str:
    """(a) の分類。単一ソース＝`arena.mm_lint._defeat_boards`。"""
    from arena.mm_lint import _defeat_boards
    if board in _defeat_boards(view):
        return "win_condition"
    if board in _factor_boards(view):
        return "factor_gain"
    return "no_contribution"


# ---------------------------------------------------------------------------
# 機序トレース（return 行の**中身**をキーにする＝行番号キー禁止）
# ---------------------------------------------------------------------------
def _trace_score(mm, rec: dict) -> tuple[str, float]:
    """chosen の採点を再実行し「最後に実行された agents/heuristic.py の行の中身」を返す。

    純関数（`_score_set` / `_score_ability` は rng を消費しない）＝対局を汚さない。
    行番号は**返さない**（キーはソース行の文字列＝内容ベース同定）。"""
    import agents.heuristic as H
    view = rec["view"]
    a = mm._analyze(view)
    src_file = H.__file__
    hits: list[int] = []

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != src_file:
            return None
        if event == "call":
            return tracer
        if event == "line":
            hits.append(frame.f_lineno)
        return tracer

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        if rec["decision"] == "set_card":
            val = mm._score_set(rec["chosen"], a, view)
        else:
            val = mm._score_ability(rec["chosen"], a)
    finally:
        sys.settrace(old)
    # 最後に実行された行＝return 行。中身（strip 済みソース）をキーにする。
    import linecache
    line_txt = ""
    for ln in reversed(hits):
        t = linecache.getline(src_file, ln).strip()
        if t.startswith("return"):
            line_txt = t
            break
    if not line_txt and hits:
        line_txt = linecache.getline(src_file, hits[-1]).strip()
    return line_txt, val


# ---------------------------------------------------------------------------
# 1局の走査
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int = 8,
               trace_b: bool = False) -> dict:
    """1対局を回し、(a)(b)(c)(d) の席テーブルを返す。

    ★`sim.run_game` の素の返り値だけを使う＝ベンチと同一の対局（ラッパ無し）。"""
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    mm_recs = [r for r in log if r["actor"] == "mastermind"
               and r["decision"] in ("set_card", "mastermind_ability")]

    # -- (a) ボード暗躍 ------------------------------------------------------
    rows_a = []
    attr_mm = HeuristicMastermind(seed)   # 帰属用（_analyze は純関数・rng 不使用）
    for i, r in enumerate(mm_recs):
        if not _is_board_anyaku(r):
            continue
        view, ch = r["view"], r["chosen"]
        board = ch["target"]
        cls = _classify_board(view, board)
        # ★寄与しない席だけ、脚本家AI 自身の帰属（設計上の偽装ボードか）を引く。
        #   `decoy_board`＝M1b 偽装ボード（`_analyze_impl`）。`b193_ry_boards`＝B-193 の本命盤。
        is_decoy = None
        if cls != "win_condition":
            a_att = attr_mm._analyze(view)
            is_decoy = (board == a_att.get("decoy_board")
                        and bool(a_att.get("decoy_funded")))
        rows_a.append({
            "is_designed_decoy": is_decoy,
            "key": (days, name, seed, r["loop"], r["day"], i),
            "loop": r["loop"], "day": r["day"], "decision": r["decision"],
            "card": ch.get("card") or ch.get("action"), "board": board,
            "cls": cls,
            "cur": view["board_anyaku"].get(board, 0),
            "rule_y": view.get("rule_y"),
        })

    # -- (b) 自分の犯人を冷やした席 -----------------------------------------
    rows_b = []
    for i, r in enumerate(mm_recs):
        um = _unrest_delta(r)
        if not um or um[1] >= 0:
            continue
        tgt, view = um[0], r["view"]
        c = next((c for c in view["characters"] if c["name"] == tgt), None)
        if c is None:
            continue
        upcoming = sorted(inc["day"] for inc in view["incidents"]
                          if inc["culprit"] == tgt and inc["day"] >= view["day"])
        th = unrest_threshold_of(tgt)
        row = {
            "key": (days, name, seed, r["loop"], r["day"], i),
            "loop": r["loop"], "day": r["day"], "decision": r["decision"],
            "target": tgt, "unrest": c["unrest"], "th": th,
            "alive": c["alive"],
            "upcoming_inc_days": upcoming,
            "is_own_culprit": bool(upcoming) and c["alive"],
            # 「その1手で臨界から遠ざかったか」＝不安>0（0なら盤面は動かない＝A-66 filler）
            "effective": c["unrest"] > 0,
            "board_anyaku": dict(view.get("board_anyaku") or {}),
            "rule_y": view.get("rule_y"),
            "script": name, "seed": seed, "days": days,
        }
        if trace_b and row["is_own_culprit"] and row["effective"]:
            try:
                row["branch"], row["score"] = _trace_score(
                    HeuristicMastermind(seed), r)
            except Exception as e:          # トレース失敗は嘘をつかず記録する
                row["branch"], row["score"] = f"(trace失敗: {e})", None
        rows_b.append(row)

    # -- (d) 遠隔殺人の打点ゲートが「暗躍+2 を使い切った」だけで閉じた席 ----
    #    ★(a)(b)(c) の外で見つかった機序（教材の実機序）。反実仮想＝`used_cards` から
    #    `暗躍+2` を1枚抜いた view で `_analyze` をやり直し、犯人が reachable に戻るか。
    #    戻るなら「その日の犯人への不安+1」は**A-67/A-71 の緩和が閉じたためだけに**
    #    見送られている＝打点会計の可否が 1/loop 札の残数に張り付いている。
    rows_d = []
    probe_mm = HeuristicMastermind(seed)      # 採点用（純関数・rng を消費しない）
    for i, r in enumerate(mm_recs):
        if r["decision"] != "set_card":
            continue
        view = r["view"]
        used = (view.get("used_cards", {}) or {}).get("mastermind", []) or []
        if "暗躍+2" not in used:
            continue
        upcoming = [inc for inc in view["incidents"]
                    if inc["name"] == "遠隔殺人" and inc["day"] >= view["day"]]
        if not upcoming:
            continue
        a = probe_mm._analyze(view)
        for inc in upcoming:
            cn = inc["culprit"]
            c = next((c for c in view["characters"] if c["name"] == cn), None)
            th = unrest_threshold_of(cn)
            if (c is None or not c["alive"] or th is None or th < 1
                    or c["unrest"] >= th or cn in a["reachable_culprits"]):
                continue
            cf_view = json.loads(json.dumps(view))     # 反実仮想用の複製
            cf_view["used_cards"]["mastermind"] = [
                x for x in cf_view["used_cards"]["mastermind"] if x != "暗躍+2"]
            a_cf = probe_mm._analyze(cf_view)
            if cn not in a_cf["reachable_culprits"]:
                continue
            o = {"card": "不安+1", "target": cn, "target_kind": "character"}
            rows_d.append({
                "key": (days, name, seed, r["loop"], r["day"], i),
                "loop": r["loop"], "day": r["day"], "culprit": cn,
                "unrest": c["unrest"], "th": th, "inc_day": inc["day"],
                "score_actual": probe_mm._score_set(o, a, view),
                "score_cf": probe_mm._score_set(o, a_cf, cf_view),
                "chosen": r["chosen"].get("card"),
            })

    # -- (c) 前ループと同一の配置 -------------------------------------------
    # 脚本家の伏せ札（set_card）を (loop, day) ごとに席順で束ねる。
    by_turn: dict[tuple[int, int], list[tuple]] = {}
    for r in mm_recs:
        if r["decision"] != "set_card":
            continue
        ch = r["chosen"]
        by_turn.setdefault((r["loop"], r["day"]), []).append(
            (ch["card"], ch["target"], ch["target_kind"]))
    # 主人公の暗躍禁止（板宛て）を (loop, day) ごとに集める＝露見の指標
    kinshi: dict[tuple[int, int], set[str]] = {}
    for ev in state.history:
        if ev.get("event") != "cards_revealed":
            continue
        k = (ev["loop"], ev["day"])
        for p in ev.get("placements", ()):
            if (p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止"
                    and p.get("target_kind") == "board"):
                kinshi.setdefault(k, set()).add(p["target"])

    rows_c = []
    for (loop, day), seats in sorted(by_turn.items()):
        prev = by_turn.get((loop - 1, day))
        if prev is None:
            continue
        prev_ms = Counter(prev)
        cur_ms = Counter(seats)
        same_turn = (prev_ms == cur_ms)
        prev_kinshi = kinshi.get((loop - 1, day), set())
        cur_kinshi = kinshi.get((loop, day), set())
        for j, s in enumerate(seats):
            # 「同一の配置」＝同じ (札, 対象, 対象種別) が前ループの同日にも置かれていた席。
            repeated = prev_ms.get(s, 0) > 0
            blocked_prev = (s[2] == "board" and s[0] in _ANYAKU_CARDS
                            and s[1] in prev_kinshi)
            rows_c.append({
                "key": (days, name, seed, loop, day, j),
                "turn_key": (days, name, seed, loop, day),
                "loop": loop, "day": day, "seat": j,
                "card": s[0], "target": s[1], "target_kind": s[2],
                "repeated": bool(repeated),
                "same_turn": bool(same_turn),
                "repeated_after_block": bool(repeated and blocked_prev),
                # ★同じ手を繰り返して**また**同じ板を守られた席（＝反復が現に罰された）
                "blocked_again": bool(repeated and blocked_prev
                                      and s[1] in cur_kinshi),
            })

    # 対局の帰結（ベンチと同じ定義）
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome, ltw = "defense", state.loop_no
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
        ltw = loops + 1
    else:
        outcome, ltw = "loss", loops + 1

    return {"script": name, "seed": seed, "days": days,
            "outcome": outcome, "loops_to_win": ltw,
            "a": rows_a, "b": rows_b, "c": rows_c, "d": rows_d,
            "n_mm_set": sum(1 for r in mm_recs if r["decision"] == "set_card")}


# ---------------------------------------------------------------------------
# 掃引
# ---------------------------------------------------------------------------
def sweep(days: int = 3, loops: int = 8, trace_b: bool = False,
          verbose: bool = True, perm: str = "id", b280: bool = False,
          b281: bool = False) -> dict:
    """★`perm`＝主人公側の候補列の並べ替え（`arena.tie_noise.install_perm`）。
    §72-78（検問3）＝数え上げが較正条件（perm=id）固有でないかを見るための条件振り。
    採点には一切触れない＝同点帯の解け方だけが変わる。

    ★`b281`＝旧・B-281（順位ガード）の切替口を ON にする引数だった。
    ★★B-281 は**構造的失敗で不採用**（§72-109）＝`HeuristicMastermind.B281_CLOSER_FIRST` は
    main に存在しない。∴ `b281=True` は **ValueError**（黙って無視しない＝測ったつもりの事故を防ぐ）。
    引数自体は測定記録（§72-107 の4条件表）の再現手順との互換のため残す。

    ★`b280`＝脚本家AI の切替口 `HeuristicMastermind.B280_ANYAKU_HORIZON` を ON にして
    同じ数え上げを掛ける（B-280 の第1層＝「打点ゲートが +2 の残数だけで閉じた席」の前後比較）。
    **検出器 (d) は一切変えない**＝変わるのは対局の側だけ。"""
    from agents import HeuristicMastermind
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    if b281:
        raise ValueError(
            "b281=True は測定不能＝B-281 は不採用（§72-109）で "
            "HeuristicMastermind.B281_CLOSER_FIRST は存在しない")
    _old_b280 = HeuristicMastermind.B280_ANYAKU_HORIZON
    HeuristicMastermind.B280_ANYAKU_HORIZON = bool(b280)
    try:
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days, loops=loops, trace_b=trace_b)
            games.append(g)
            if verbose:
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']}",
                      flush=True)
    finally:
        uninstall_perm()
        HeuristicMastermind.B280_ANYAKU_HORIZON = _old_b280
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "b280": bool(b280), "b281": bool(b281),
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


def summarize(rep: dict) -> str:
    games = rep["games"]
    a_rows = [r for g in games for r in g["a"]]
    b_rows = [r for g in games for r in g["b"]]
    c_rows = [r for g in games for r in g["c"]]
    d_rows = [r for g in games for r in g["d"]]
    L = [f"# B-279 フェーズ0 計測（{rep['n_games']}局・{rep['days']}日級・"
         f"perm={rep.get('perm', 'id')}・B280={rep.get('b280', False)}・"
         f"B281={rep.get('b281', False)}・"
         f"PYTHONHASHSEED={rep['hashseed']}）", ""]

    # ベンチ照合（bit 一致の自己申告）
    oc = Counter(g["outcome"] for g in games)
    mean = round(sum(g["loops_to_win"] for g in games) / len(games), 3)
    L.append(f"照合: 結末 {dict(sorted(oc.items()))} ／ 平均ループ {mean}")
    L.append("")

    # (a)
    ca = Counter(r["cls"] for r in a_rows)
    L.append(f"## (a) ボードへの暗躍 {len(a_rows)} 席")
    for k in ("win_condition", "factor_gain", "no_contribution"):
        L.append(f"  - {k}: {ca.get(k, 0)}")
    nog = [r for r in a_rows if r["cls"] == "no_contribution"]
    L.append(f"  ★勝利条件に寄与しない席 = {len(nog)} / {len(a_rows)} "
             f"({100 * len(nog) / max(1, len(a_rows)):.1f}%)")
    L.append("  内訳（板別）: " + "  ".join(
        f"{b}:{n}" for b, n in Counter(r["board"] for r in nog).most_common()))
    L.append("  内訳（ルールY別）: " + "  ".join(
        f"{b}:{n}" for b, n in Counter(str(r["rule_y"]) for r in nog).most_common()))
    L.append("  内訳（カード別）: " + "  ".join(
        f"{b}:{n}" for b, n in Counter(str(r["card"]) for r in nog).most_common()))
    L.append("  ★脚本家AI 自身の帰属: 設計上の偽装ボード(M1b decoy_board)="
             + str(sum(1 for r in nog if r.get("is_designed_decoy")))
             + " ／ それ以外=" + str(sum(1 for r in nog
                                        if not r.get("is_designed_decoy"))))
    L.append("")

    # (b)
    own = [r for r in b_rows if r["is_own_culprit"]]
    own_eff = [r for r in own if r["effective"]]
    L.append(f"## (b) 脚本家の不安- 席 {len(b_rows)}")
    L.append(f"  - うち対象が『今日以降に事件がある自分の犯人』= {len(own)}")
    L.append(f"  - うち**盤面が実際に動く**（不安>0）= {len(own_eff)}  ★これが本題")
    L.append(f"  - 不安0への不安-1（A-66 filler＝盤面不変）= {len(b_rows) - len([r for r in b_rows if r['effective']])}")
    if own_eff:
        L.append("  臨界との距離 (不安/臨界): " + "  ".join(
            f"u{u}/th{th}:{n}" for (u, th), n in
            Counter((r["unrest"], r["th"]) for r in own_eff).most_common()))
        br = Counter(r.get("branch", "(未トレース)") for r in own_eff)
        L.append("  機序（return 行の中身別）:")
        for txt, n in br.most_common():
            L.append(f"    {n:4d}  {txt}")
        L.append("  代表例:")
        for r in own_eff[:5]:
            L.append(f"    {r['script']} s{r['seed']}({r['days']}日) "
                     f"L{r['loop']}D{r['day']} 不安-1→{r['target']} "
                     f"(u={r['unrest']}/th={r['th']}, 事件日{r['upcoming_inc_days']}, "
                     f"ルールY={r['rule_y']}, 盤={r['board_anyaku']})")
    L.append("")

    # (c)
    rep_seats = [r for r in c_rows if r["repeated"]]
    turns = {tuple(r["turn_key"]): r["same_turn"] for r in c_rows}
    same_turns = sum(1 for v in turns.values() if v)
    L.append(f"## (c) 前ループと比較できた席 {len(c_rows)}（ターン {len(turns)}）")
    L.append(f"  - 席が前ループの同日に**同一**（札×対象）= {len(rep_seats)} "
             f"({100 * len(rep_seats) / max(1, len(c_rows)):.1f}%)")
    L.append(f"  - ターン3枚が**丸ごと同一**（多重集合一致）= {same_turns} "
             f"({100 * same_turns / max(1, len(turns)):.1f}%)")
    ra = [r for r in rep_seats if r["repeated_after_block"]]
    L.append(f"  - ★前ループの同席が主人公の暗躍禁止で潰されたのに同じ板へ再置＝{len(ra)}")
    L.append(f"    うち**再置もまた同じ板を守られた**（反復が現に罰された）＝"
             f"{sum(1 for r in ra if r['blocked_again'])}")
    L.append("  反復席の内訳（札）: " + "  ".join(
        f"{b}:{n}" for b, n in Counter(r["card"] for r in rep_seats).most_common()))
    L.append("")

    # (d)
    L.append(f"## (d) ★(a)(b)(c)の外：遠隔殺人の打点ゲートが『暗躍+2 を使い切った』"
             f"だけで閉じた席 = {len(d_rows)}")
    L.append(f"  - 局数 {len({tuple(r['key'])[:3] for r in d_rows})} ／ "
             f"ループ数 {len({tuple(r['key'])[:4] for r in d_rows})}")
    if d_rows:
        L.append("  犯人への 不安+1 の採点（実際 → 暗躍+2 未使用の反実仮想）:")
        for (sa, sc), n in Counter((r["score_actual"], r["score_cf"])
                                   for r in d_rows).most_common(6):
            L.append(f"    {n:4d}  {sa} → {sc}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# ★§72-88 検問4：動機となった局面（教材棋譜）を捕まえるか
# ---------------------------------------------------------------------------
def scan_log(path: str) -> dict:
    """`docs/feedback_logs/*.jsonl`（実戦棋譜）に (a)(b)(c) の判定器を掛ける。

    棋譜の各 decision には決定時の `view` がそのまま入っている＝**対局を回さずに**
    同じ判定器を当てられる（＝動機となった局面を捕まえるかの直接確認）。"""
    recs, meta = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") == "meta":
                meta = d
            elif (d.get("type") == "decision" and d.get("actor") == "mastermind"
                  and d.get("decision") in ("set_card", "mastermind_ability")):
                recs.append(d)
    hits_a, hits_b = [], []
    for r in recs:
        view, ch = r["view"], r["chosen"]
        if _is_board_anyaku(r):
            cls = _classify_board(view, ch["target"])
            hits_a.append({"loop": r["loop"], "day": r["day"],
                           "card": ch.get("card") or ch.get("action"),
                           "board": ch["target"], "cls": cls})
        um = _unrest_delta(r)
        if um and um[1] < 0:
            tgt = um[0]
            c = next((c for c in view["characters"] if c["name"] == tgt), None)
            upcoming = sorted(inc["day"] for inc in view["incidents"]
                              if inc["culprit"] == tgt and inc["day"] >= view["day"])
            if c is None:
                continue
            hits_b.append({"loop": r["loop"], "day": r["day"], "target": tgt,
                           "unrest": c["unrest"], "th": unrest_threshold_of(tgt),
                           "upcoming_inc_days": upcoming,
                           "is_own_culprit": bool(upcoming) and c["alive"],
                           "effective": c["unrest"] > 0,
                           "board_anyaku": dict(view.get("board_anyaku") or {})})
    # (c) 前ループ同日と同一の伏せ札
    by_turn: dict[tuple[int, int], list[tuple]] = {}
    for r in recs:
        if r["decision"] != "set_card":
            continue
        ch = r["chosen"]
        by_turn.setdefault((r["loop"], r["day"]), []).append(
            (ch["card"], ch["target"], ch["target_kind"]))
    hits_c = []
    for (loop, day), seats in sorted(by_turn.items()):
        prev = by_turn.get((loop - 1, day))
        if prev is None:
            continue
        hits_c.append({"loop": loop, "day": day, "seats": seats,
                       "prev": prev,
                       "same_turn": Counter(seats) == Counter(prev),
                       "repeated_seats": sum(1 for s in seats
                                             if Counter(prev).get(s, 0) > 0)})
    return {"path": path, "winner": (meta or {}).get("winner"),
            "a": hits_a, "b": hits_b, "c": hits_c}


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-279 フェーズ0 計測")
    ap.add_argument("--log", type=str, default=None,
                    help="実戦棋譜 jsonl に判定器を掛ける（§72-88 検問4）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--trace-b", action="store_true")
    ap.add_argument("--perm", type=str, default="id",
                    help="主人公側の候補列の並べ替え（id/rev/rot1/h1...・検問3用）")
    ap.add_argument("--b280", action="store_true",
                    help="脚本家AI の B280_ANYAKU_HORIZON を ON にして掃引する")
    ap.add_argument("--b281", action="store_true",
                    help="（不採用＝§72-109）指定すると ValueError。再現手順互換のため引数のみ残置")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.log:
        rep = scan_log(args.log)
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=list))
        return
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED 未固定。測定は PYTHONHASHSEED=0 で。", file=sys.stderr)
    rep = sweep(days=args.days, loops=args.loops, trace_b=args.trace_b,
                verbose=not args.quiet, perm=args.perm, b280=args.b280,
                b281=args.b281)
    print(summarize(rep))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, default=list)
        print(f"→ {args.json}")


if __name__ == "__main__":
    main()
