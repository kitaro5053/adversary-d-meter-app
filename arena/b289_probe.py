# -*- coding: utf-8 -*-
"""B-289〔降りた後の配分〕の計測ハーネス（センサス＋掃引）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない
（読むだけ）。対局は `sim.run_game` の**素の返り値** `(state, log)` だけを使う＝ラッパを
挟まない＝ベンチと**同一の乱数消費**が構造的に保証される（先例＝`arena/b287_probe.py`／
`arena/b288_probe.py`）。

------------------------------------------------------------------------------
測るもの（発注＝FableA・B-289 (1) センサス）
------------------------------------------------------------------------------
出典＝§72-125 の物証＝「`_b286_penalty` は**折られた席から引く**だけで `alt_lines` へ
加点する経路が無い」＝降りた後の札は「次に点の高い手」へ流れるだけ。
∴ **降りた後に札がどこへ行っているか**を、減点が発火した決定ごとに全数分類する：

  (i)   alt_lines の席（＝狙った乗り換え先＝拮抗線の犯人）
  (ii)  キャラ暗躍の席（§72-125 の観察＝`revenge` で `暗躍+2` が流れた先）
  (iii) その他の板への暗躍
  (iv)  暗躍以外の札（不安・移動・友好禁止 等。alt_lines 以外の対象）
  (v)   減点後もその席のまま（＝降りきれていない）

★★用語の注意（センサスの設計上いちばん大事な点）：
  `alt_lines` は **`_analyze` の `pump_targets` から作られる犯人名の集合**（板ではない）。
  拮抗線＝「事件の犯人へ不安を積んで臨界へ届かせる線」＝**線を進める札は `不安+1`**。
  `暗躍+1/+2` はキャラの暗躍カウンタ／板の暗躍を進める札＝**犯人の不安には一切効かない**。
  ∴ 「降りた `暗躍+2` が alt_lines へ流れる」経路は**カード種の定義上そもそも存在しない**。
  センサスはこの点を数で確かめるため、(i) を「対象が alt_lines の犯人である手」全体で
  取り、**その内訳をカード種別で割る**（`不安+*` = 線を進める手／それ以外 = 進めない手）。

------------------------------------------------------------------------------
キー（規約＝監査道具の欠陥4族への対策）
------------------------------------------------------------------------------
- 行キー＝(days, script, seed, loop, day, 決定index)＝全次元・一意（sweep で番人 assert）。
- 同定は内容ベース（`_analyze` の返り値・`_b286_penalty` の実物を呼ぶ）＝行番号キー・
  実行時キャッシュ・定数直読みを使わない。
- 素点の再計算は `decide()` の `_sc` と**同じ式**（`_score_set - _plus2_penalty -
  _b286_penalty`）＝式を写経せず、可能な限り本体のメソッドをそのまま呼ぶ。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_probe \
        --days 3 --perm id --mode cur --json out.json
    ... --mode b40           # B289_ALT_BONUS=40 の対照
    ... --diff base.json cand.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace

_ANYAKU_CARDS = {"暗躍+1", "暗躍+2"}
_PUMP_CARDS = {"不安+1", "不安+2"}


# ---------------------------------------------------------------------------
# 掃引モード（B-289 の切替口）
# ---------------------------------------------------------------------------
#: "cur" = **現行既定そのもの**＝どの切替口も上書きしない（★2026-09-02 の land 以後は
#:         `B289_ALT_BONUS=40` が既定＝"cur" は ON 側になる）。
#: "off" = B-289 だけを明示 OFF（land 前の "cur" と同値＝過去の測定の再現口）。
#: bNN   = `B289_ALT_BONUS=NN`。日数ゲートは触らないので3日級では発火しない
#:         （land 前の3日級 bNN 測定を bit 再現したいときは `B289_MIN_DAYS` を 0 に倒す
#:          `g0` 系を使う）。他の切替口は一切触らない。
_MODES = {
    "cur": {},
    "off": {"B289_ALT_BONUS": 0.0},
    # 日数ゲートを開けた対照（land 前の3日級・4日級の測定条件を bit 再現する口）
    "b40g0": {"B289_ALT_BONUS": 40.0, "B289_MIN_DAYS": 0},
    "b20": {"B289_ALT_BONUS": 20.0},
    "b40": {"B289_ALT_BONUS": 40.0},
    "b60": {"B289_ALT_BONUS": 60.0},
    # 掃引の形（20 と 40 の間＝符号が反転する帯の形を見る）
    "b30": {"B289_ALT_BONUS": 30.0},
    # ★対照＝発注文どおりの「暗躍札へ加点」（線は進まないはず＝負の対照）
    "a40": {"B289_ALT_BONUS": 40.0, "B289_ALT_SCOPE": "anyaku"},
}
_MODE_FLAGS = ("B289_ALT_BONUS", "B289_ALT_SCOPE", "B289_MIN_DAYS")


def _apply_mode(HM, mode: str) -> dict:
    """★モード辞書に**書いてある切替口だけ**を倒す（書いていないものは現行既定のまま）。

    ＝"cur" は「既定そのもの」を意味する（既定が変わればモードの意味も追随する）。
    land の前後で同じ条件を測り直したいときは、明示的な "off"／"bNN" を使うこと。
    """
    old = {}
    for f, v in _MODES[mode].items():
        if f not in _MODE_FLAGS:
            raise RuntimeError(f"未知の切替口 {f}（mode={mode}）")
        if not hasattr(HM, f):
            raise RuntimeError(f"切替口 {f} が agents 側に無い（mode={mode}）")
        old[f] = getattr(HM, f)
        setattr(HM, f, v)
    return old


# ---------------------------------------------------------------------------
# 1局の走査
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int = 8) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.b279_probe import _classify_board, _is_board_anyaku
    from arena.mm_lint import lint_move
    from sim import run_game

    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    mm_recs = [r for r in log if r["actor"] == "mastermind"
               and r["decision"] in ("set_card", "mastermind_ability")]
    attr = HeuristicMastermind(seed)   # 帰属用（_analyze/_score_set は rng 不使用）

    # ★ターン単位の受け皿（同一対象への重ね置き不可＝KB rules/10_action_cards.md:81
    #   ＝乗り換え先の犯人は**1ターンに1枚しか吸えない**。∴ 決定（＝札1枚）単位の分類だけ
    #   では「配分が alt へ届いているか」を読み違える＝ターン単位でも数える）。
    turn_place: dict[tuple[int, int], list[tuple[str, str, str]]] = {}
    for r in mm_recs:
        if r["decision"] != "set_card":
            continue
        ch = r["chosen"]
        turn_place.setdefault((r["loop"], r["day"]), []).append(
            (ch["card"], ch["target"], ch["target_kind"]))

    rows = []
    for i, r in enumerate(mm_recs):
        if r["decision"] != "set_card":
            continue
        view, ch, opts = r["view"], r["chosen"], r["options"]
        a = attr._analyze(view)
        info = a.get("b286")
        pen = [attr._b286_penalty(o, a) for o in opts]
        if not any(p > 0 for p in pen):
            continue
        alt = set((info or {}).get("alt_lines") or ())

        def _base(o):
            """B-286 減点**前**・B-289 加点**前**の素点（＝`decide` の `_sc_raw`）。"""
            return attr._score_set(o, a, view) - attr._plus2_penalty(o, a, view)

        def _bonus(o):
            # ★この行に来る決定は定義上「減点が発火した決定」＝fired=True
            #   （本体 `decide()` が options を1周して求める値と同じ）。
            f = getattr(attr, "_b289_bonus", None)
            return f(o, a, view, True) if f is not None else 0.0

        def _sc(o):
            return _base(o) - attr._b286_penalty(o, a) + _bonus(o)

        pen_seats = sorted({(o["target_kind"], o["target"])
                            for o, p in zip(opts, pen) if p > 0})
        ch_pen = attr._b286_penalty(ch, a) > 0
        card, tgt, kind = ch["card"], ch["target"], ch["target_kind"]

        # 分類（(v) → (i) → (ii) → (iii) → (iv) の順で排他）
        if ch_pen:
            cls = "v_same_seat"
        elif tgt in alt and kind == "character":
            cls = "i_alt"
        elif card in _ANYAKU_CARDS and kind == "character":
            cls = "ii_char_anyaku"
        elif card in _ANYAKU_CARDS and kind == "board":
            cls = "iii_other_board"
        else:
            cls = "iv_non_anyaku"

        # alt_lines を狙える手が候補にあったか＋その最良点と選ばれた手の点差
        alt_opts = [o for o in opts
                    if o.get("target") in alt and o.get("target_kind") == "character"]
        alt_pump = [o for o in alt_opts if o["card"] in _PUMP_CARDS]
        best_alt = max((_sc(o) for o in alt_opts), default=None)
        best_alt_pump = max((_sc(o) for o in alt_pump), default=None)
        sc_ch = _sc(ch)
        # 「減点が無ければ折られた席が最善だった」＝減点が実際に手を動かした証拠
        base_best = max(_base(o) for o in opts)
        base_best_is_pen = any(_base(o) >= base_best - 1e-9
                               for o, p in zip(opts, pen) if p > 0)

        rows.append({
            "key": (days, name, seed, r["loop"], r["day"], i),
            "loop": r["loop"], "day": r["day"],
            "class": cls, "card": card, "target": tgt, "kind": kind,
            "pen_seats": pen_seats,
            "n_pen_opts": sum(1 for p in pen if p > 0),
            "alt": sorted(alt),
            "alt_opt_cards": sorted(Counter(o["card"] for o in alt_opts).items()),
            "has_alt_opt": bool(alt_opts),
            "has_alt_pump": bool(alt_pump),
            "sc_chosen": round(sc_ch, 4),
            "sc_best_alt": None if best_alt is None else round(best_alt, 4),
            "sc_best_alt_pump": (None if best_alt_pump is None
                                 else round(best_alt_pump, 4)),
            "margin_alt": (None if best_alt is None
                           else round(sc_ch - best_alt, 4)),
            "margin_alt_pump": (None if best_alt_pump is None
                                else round(sc_ch - best_alt_pump, 4)),
            "redirected": bool(base_best_is_pen and not ch_pen),
            # ★ターン単位＝この (loop, day) で alt_lines の犯人へ札が置かれたか
            "turn_alt_placed": any(
                t in alt and k == "character"
                for _c, t, k in turn_place[(r["loop"], r["day"])]),
            "turn_alt_pump": any(
                t in alt and k == "character" and c in _PUMP_CARDS
                for c, t, k in turn_place[(r["loop"], r["day"])]),
            # ★alt は在るのに候補に出ていない＝重ね置き不可でその席が埋まっている
            "alt_blocked": bool(alt) and not alt_opts,
        })

    # -- 番人 (a)：勝利条件に寄与しないボード暗躍（b279 の分類をそのまま） ----
    n_board_anyaku = n_no_contrib = n_designed_decoy = 0
    for r in mm_recs:
        if not _is_board_anyaku(r):
            continue
        n_board_anyaku += 1
        view, ch = r["view"], r["chosen"]
        board = ch.get("target")
        if _classify_board(view, board) == "no_contribution":
            n_no_contrib += 1
            a_att = attr._analyze(view)
            if (board == a_att.get("decoy_board")
                    and bool(a_att.get("decoy_funded"))):
                n_designed_decoy += 1

    # -- 番人 mm_lint D1〜D6 ---------------------------------------------------
    lint = Counter()
    for r in mm_recs:
        for det, _reason in lint_move(r["view"], r["chosen"]):
            lint[det] += 1

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
            "rows": rows,
            "guard_a": {"board_anyaku": n_board_anyaku,
                        "no_contribution": n_no_contrib,
                        "designed_decoy": n_designed_decoy},
            "lint": dict(lint)}


# ---------------------------------------------------------------------------
# 掃引
# ---------------------------------------------------------------------------
def sweep(days: int = 3, loops: int = 8, perm: str = "id", mode: str = "cur",
          verbose: bool = True) -> dict:
    from agents.heuristic import HeuristicMastermind as HM
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    old = _apply_mode(HM, mode)
    try:
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days)
            games.append(g)
            if verbose:
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} "
                      f"発火{len(g['rows'])}", flush=True)
    finally:
        uninstall_perm()
        for f, v in old.items():
            setattr(HM, f, v)
    keys = [tuple(r["key"]) for g in games for r in g["rows"]]
    assert len(keys) == len(set(keys)), "行キーが一意でない"
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "b289_mode": mode,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


_CLASSES = ("i_alt", "ii_char_anyaku", "iii_other_board", "iv_non_anyaku",
            "v_same_seat")


def summarize(rep: dict) -> str:
    games = rep["games"]
    rows = [r for g in games for r in g["rows"]]
    oc = Counter(g["outcome"] for g in games)
    mean = round(sum(g["loops_to_win"] for g in games) / len(games), 3)
    mm_total = sum(min(g["loops_to_win"], 9) - 1 for g in games)
    guard_a, lint = Counter(), Counter()
    for g in games:
        guard_a.update(g["guard_a"])
        lint.update(g["lint"])
    cls = Counter(r["class"] for r in rows)
    n = len(rows) or 1
    red = [r for r in rows if r["redirected"]]
    cls_red = Counter(r["class"] for r in red)
    nr = len(red) or 1
    # 「(i) を選べたのに選ばなかった席」の点差
    miss = [r for r in rows if r["has_alt_opt"] and r["class"] != "i_alt"]
    miss_p = [r for r in rows if r["has_alt_pump"] and r["class"] != "i_alt"]

    def _mstat(rs, key):
        v = sorted(r[key] for r in rs if r[key] is not None)
        if not v:
            return "（該当なし）"
        med = v[len(v) // 2]
        q1, q3 = v[len(v) // 4], v[(3 * len(v)) // 4]
        return (f"n={len(v)} 最小={v[0]:.1f} 四分位={q1:.1f}/{med:.1f}/{q3:.1f} "
                f"最大={v[-1]:.1f} ／ ≤60が{sum(1 for x in v if x <= 60)}件"
                f"・≤40が{sum(1 for x in v if x <= 40)}件"
                f"・≤20が{sum(1 for x in v if x <= 20)}件")

    L = [f"# B-289 センサス（{rep['n_games']}局・{rep['days']}日級・perm={rep['perm']}・"
         f"mode={rep['b289_mode']}・PYTHONHASHSEED={rep['hashseed']}）",
         f"照合: 結末 {dict(sorted(oc.items()))} ／ 平均ループ {mean}",
         "",
         f"## 第1層 減点が発火した決定 = {len(rows)}"
         f"（うち★実際に手が動いた〔減点が無ければ折られた席が素点最善〕= {len(red)}）",
         "", "### 選ばれた手の分類（発火した決定 全数）"]
    for c in _CLASSES:
        L.append(f"  - {c:16s} {cls.get(c, 0):5d}  ({100 * cls.get(c, 0) / n:5.1f}%)")
    L += ["", "### 同・★手が動いた決定に限る（＝「降りた後の配分」そのもの）"]
    for c in _CLASSES:
        L.append(f"  - {c:16s} {cls_red.get(c, 0):5d}"
                 f"  ({100 * cls_red.get(c, 0) / nr:5.1f}%)")
    turns = {}
    for g in games:
        for r in g["rows"]:
            turns[(g["script"], g["seed"], r["loop"], r["day"])] = (
                r["turn_alt_placed"], r["turn_alt_pump"], bool(r["alt"]))
    t_alt = [v for v in turns.values() if v[2]]
    L += ["",
          f"### ★ターン単位（同一対象への重ね置き不可＝alt 席は1ターン1枚しか吸えない）",
          f"  - 減点が発火したターン = {len(turns)}"
          f"（うち乗り換え先が実在 = {len(t_alt)}）",
          f"  - そのターンに alt_lines の犯人へ札を置いた = "
          f"{sum(1 for v in t_alt if v[0])}"
          + (f" ({100 * sum(1 for v in t_alt if v[0]) / len(t_alt):.1f}%)"
             if t_alt else ""),
          f"  - うち★線を進める札（不安）で置いた = "
          f"{sum(1 for v in t_alt if v[1])}"
          + (f" ({100 * sum(1 for v in t_alt if v[1]) / len(t_alt):.1f}%)"
             if t_alt else ""),
          f"  - alt は在るのに候補に出ていない決定（重ね置き不可で席が埋まっている）= "
          f"{sum(1 for r in rows if r['alt_blocked'])}",
          "",
          f"### 乗り換え先を狙える手が候補にあったか（発火した決定 全数 {len(rows)}）",
          f"  - alt_lines の犯人を対象にする手が存在 = "
          f"{sum(1 for r in rows if r['has_alt_opt'])}",
          f"  - うち★線を進める札（不安+1/+2）が存在 = "
          f"{sum(1 for r in rows if r['has_alt_pump'])}",
          f"  - alt を狙える手の**カード種**内訳 = " + "  ".join(
              f"{c}:{k}" for c, k in Counter(
                  c for r in rows for c, k in r["alt_opt_cards"]
                  for _ in range(k)).most_common(8)),
          "",
          f"### 点差（選ばれた手の点 − alt を狙う最良手の点。＝加点で覆せる幅）",
          f"  - alt 全般: {_mstat(miss, 'margin_alt')}",
          f"  - alt かつ不安札: {_mstat(miss_p, 'margin_alt_pump')}",
          "",
          f"## 第2層",
          f"  - 脚本家が取ったループ総数 Σ(min(ltw,9)−1) = {mm_total}",
          f"  - 平均 loops_to_win = {mean} ／ 防衛数（参考値・ゲート不使用）= "
          f"{oc.get('defense', 0)}",
          "",
          f"## 番人",
          f"  - (a) ボード暗躍 {guard_a['board_anyaku']} 席中 寄与しない "
          f"{guard_a['no_contribution']}"
          f"（うち設計上のダミー配置 {guard_a['designed_decoy']}）",
          f"  - mm_lint: " + "  ".join(
              f"{d}:{lint.get(d, 0)}" for d in
              ("D1", "D2", "D3", "D4", "D5", "D6")),
          ]
    return "\n".join(L)


def diff(a: dict, b: dict) -> str:
    off = {(g["script"], g["seed"]): g for g in a["games"]}
    L = [f"# B-289 diff（{a['days']}日級 perm={a['perm']}: "
         f"{a['b289_mode']} → {b['b289_mode']}）"]
    for g in b["games"]:
        k = (g["script"], g["seed"])
        o = off.get(k)
        if o and (o["loops_to_win"], o["outcome"]) != (g["loops_to_win"],
                                                       g["outcome"]):
            L.append(f"  flip {k[0]}#{k[1]}: {o['loops_to_win']}({o['outcome']})"
                     f" → {g['loops_to_win']}({g['outcome']})"
                     f" [{g['loops_to_win'] - o['loops_to_win']:+d}]")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-289 センサス／掃引")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--mode", type=str, default="cur", choices=tuple(_MODES))
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--diff", nargs=2, metavar=("A.json", "B.json"), default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.diff:
        with open(args.diff[0], encoding="utf-8") as f:
            ra = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            rb = json.load(f)
        print(diff(ra, rb))
        return
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED 未固定。測定は PYTHONHASHSEED=0 で。", file=sys.stderr)
    rep = sweep(days=args.days, loops=args.loops, perm=args.perm,
                mode=args.mode, verbose=not args.quiet)
    print(summarize(rep))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, default=list)
        print(f"→ {args.json}")


if __name__ == "__main__":
    main()
