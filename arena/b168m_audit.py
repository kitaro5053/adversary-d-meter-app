# -*- coding: utf-8 -*-
"""B-168m：**ボードへのブラフの実効を実測する**（★計測のみ・`agents/` 非接触）。

## 何を測るか（★言葉の定義は doc §1 が正典＝`docs/監査_B168m_板ブラフの実効_2026-08-05.md`）

**ブラフの定義（ユーザー正典 2026-08-06）**＝「ボードやキャラクターに対して、**勝利に直接繋がる
効果ではない札**を伏せ、相手の行動や思考を誘導すること」（`docs/バックログ_構想メモ_FableA.md` §44）。

本モジュールが測るのは**ボードへのブラフ**（例示 a・b・g・i）だけ。
`rules/10_action_cards.md:71`＝「それ以外のカードはブラフとしてボードにセットされることがある」。
∴ ここで使う札は **暗躍+1／暗躍+2 以外**（`sim/legal.MASTERMIND_BOARD_CARDS` の補集合）で、
**幻想が居ない板**にだけ置く（幻想の板では非暗躍札も解決される＝`sim/legal.py:70-72`・`rules/30:55`
＝例示 c＝「他の効果を兼ねるお得なレアケース」になり、純粋な囮ではなくなるため除外）。
★キャラクターへのブラフ（例示 d・e・f）は**機序が別**（ユーザー確認済み）＝本モジュールの対象外。

## 測り方（★合成 view を作らない）

実対局を普通に回し、**主人公行動フェイズの直前**（＝脚本家が3枚伏せ終わった瞬間）で
`GameState` と主人公AIを **deepcopy** し、その複製の上で「脚本家の伏せ札の集合」だけを
差し替えて、主人公AIの**3席の決定をやり直す**。複製は捨てる＝本局は 1bit も汚れない
（`verify` がこれを実証する）。

4本の腕（arm）：

| arm | 脚本家の伏せ札 | 何のため |
|---|---|---|
| **arm0＝実手** | 実際に置いた3枚 | 基準（本局と一致することを verify で確認） |
| **arm1＝供出** | 実手から1枚を**取り除いただけ**（2枚） | 「その板に何も置かない」対照 |
| **arm2＝ブラフ** | arm1 ＋ **板 B に非暗躍札1枚** | ブラフを打った場合 |
| **arm3＝実弾** | arm1 ＋ **板 B に 暗躍+1** | ★**例示 h＝二正面の両置き**（ユーザー明示＝これは**ブラフではない**）。ブラフは実弾を節約する手段なので、**比べるべき相手はこれ** |

★**arm1↔arm2 が「板 B に伏せ札が1枚増えた」ことだけの差**＝機序（Q1）の単離。
★**arm2↔arm3 が「同じ席に囮を置くか実弾を置くか」**＝採算（Q2）の本体。
★**arm0↔arm2 が実際の取引**（手札1枚と席1つを、板 B の囮に振り替えた）。
arm3 は残り手札に 暗躍+1 があるときだけ組める（無い機会は `arm3=None`）。

取り除く1枚（＝ブラフに振り替える原資）は **最後のキャラ対象の伏せ札**（無ければ最後の伏せ札）。
＝板に置いた暗躍（本命でありうる）を壊さない側に倒す。実際に何を供出したかは行ごとに印字する。

## 出す数（★席で数える。局数では語らない＝規約 §11b）

- **座**＝主人公の `set_card` 決定1回。1ターン3座。
- **釣れた座**＝arm1 では板 B へ暗躍禁止を置かなかったのに、arm2 では置いた座。
- **押しのけた手**＝その座が arm1 で選んでいた (card,target)。★B-161 の教訓＝
  「折り手が立つ」ことと「買われる」ことは別＝**何が消えたか**まで印字する。
- **無償だった座**＝arm1 と arm2 で3座とも同じ＝ブラフが 0対1 の交換になった（脚本家の丸損）。

層別（★主キー）＝**相手（主人公）から見て「暗躍禁止を割かねばならない筋」が何本あるか**
（`_kinshi_lines`＝主人公AI自身の脅威リストから数える）。FableA の仮説
「ボードへのブラフの必要条件＝相手から見て暗躍を要する筋が2つ以上」の直接の検証キー。
副キー＝板名／ルールY（封印されしモノ＝神社・守るべき場所＝学校・復讐者の灯火/巨大時限爆弾X＝board_x）／
**脚本家が実際に何本の筋に資金を出しているか**（`HeuristicMastermind._analyze(view)["funded"]`＝§1i の二正面）。

## 二重実装をしない

- 主人公行動フェイズの進め方は `sim/flow.py:168-176` と同型（3行）。
- 結末の判定は `arena/b145_audit._outcome` を import。
- 局面の索引は `arena/b145_audit._snap_index` を import（B-145/B-157/B-161 と同じ）。
- 脚本家の資金化（funded）は `agents/heuristic.HeuristicMastermind._analyze` を**呼ぶ**（写さない）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b168m_audit verify --days 3 --end 6
    python -m arena.b168m_audit q1     --days 3
    python -m arena.b168m_audit q1     --days 5
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _outcome, _snap_index  # noqa: F401  (index は将来の検死用)
from engine.board import AREAS
from engine.models import ANRYAKU_PLUS
from sim import legal, protagonist_view
from sim.flow import _make_decider, run_loop
from sim.state import PROTAGONIST_SEATS, GameState

# ブラフに使う札の優先順（★暗躍+1/+2 は除く＝解決されてしまう＝(A) にならない）。
# 脚本家の手札で最も「他所での仕事が少ない」順に並べた（不安禁止＝主人公の不安-1を消す札で
# 用途が最も狭い→友好禁止→移動系。実際に使える札は残り手札で決まる）。
_BLUFF_PREF: tuple[str, ...] = ("不安禁止", "友好禁止", "移動斜め", "移動←→", "移動↑↓",
                                "不安+1", "不安-1")

_RY_BOARD = {"守るべき場所": "学校", "封印されしモノ": "神社"}


def _mm_placements(state: GameState) -> list[dict]:
    return [p for p in state.turn_placements if p["owner"] == "mastermind"]


def _gensou_boards(state: GameState) -> set[str]:
    return {c.area for n, c in state.characters.items()
            if n == "幻想" and c.alive and c.on_board}


def _ry_goal_board(state: GameState) -> str | None:
    """脚本のルールYが指す『常に本命』の板（ユーザー主張 Q3 の層別キー）。

    ★`rule_y_board_x`（復讐者の灯火＝クロマク初期エリア／巨大時限爆弾Xの存在＝ウィッチ初期エリア）は
    **ループごとに決まる GameState 側の値**（`sim/state.py:448,510-516`）＝Script には無い。"""
    ry = getattr(state.script, "rule_y", None)
    if ry in _RY_BOARD:
        return _RY_BOARD[ry]
    if ry in ("復讐者の灯火", "巨大時限爆弾Xの存在"):
        return getattr(state, "rule_y_board_x", None)
    return None


def _kinshi_lines(hp) -> list[str]:
    """★「相手（主人公）から見て、暗躍禁止を割かねばならない筋」のラベル一覧。

    主人公AI自身が作った脅威リスト（`heuristic_protagonist._defense_plan_recs` が
    `self._b100_plan = (_threats, plan)` として持つ＝`agents/heuristic_protagonist.py:679-684`）を
    **そのまま読む**（二重実装しない）。条件は
    「致死（fatal）」かつ「実在度 prob ≥ 0.05」かつ「**折り手に `暗躍禁止` があるもの**」。
    ★折り手の生成は `defense_plan._add_kinshi_break`（単一ソース）＝
    「暗躍禁止では止まらない供給」の筋は最初から除かれる＝**主人公が実際に席を割く筋だけ**が残る。
    ★材料は view（公開情報）と belief だけ＝**正解の配役は一切使っていない**（運用doc §3-7）。"""
    pl = getattr(hp, "_b100_plan", None)
    if not pl:
        return []
    out = []
    for t in (pl[0] or []):
        if not getattr(t, "fatal", False) or float(getattr(t, "prob", 0.0)) < 0.05:
            continue
        if any(b.card == "暗躍禁止" for c in t.conditions for b in c.breaks):
            out.append(t.label)
    return sorted(set(out))


def _run_seats(state: GameState, hp) -> tuple[list[dict], list[str]]:
    """その局面から主人公の3座を進める（`sim/flow.py:168-176` と同型）。state は破壊的に進む。

    返り値の2つ目＝**先頭の座を決めた直後**に主人公AIが持っていた「暗躍禁止を要する筋」。"""
    out = []
    lines: list[str] = []
    for i in range(3):
        seat = PROTAGONIST_SEATS[(state.leader_idx + i) % 3]
        opts = legal.set_card_options(state, seat)
        if not opts:
            out.append({"seat": seat, "card": None, "target": None, "kind": None})
            continue
        view = protagonist_view(state, seat)
        chosen = hp.decide(view, "set_card", opts)
        if i == 0:
            lines = _kinshi_lines(hp)
        state.turn_placements.append({"owner": seat, **chosen})
        out.append({"seat": seat, "card": chosen["card"], "target": chosen["target"],
                    "kind": chosen["target_kind"]})
    return out, lines


def _arm(state: GameState, hp, mm_set: list[dict]) -> tuple[list[dict], list[str]]:
    """脚本家の伏せ札を `mm_set` に差し替えた複製の上で3座を走らせる（複製は捨てる）。"""
    st = copy.deepcopy(state)
    hp2 = copy.deepcopy(hp)
    st.turn_placements = [dict(p) for p in mm_set]
    return _run_seats(st, hp2)


def _key(d: dict) -> tuple:
    return (d["card"], d["target"], d["kind"])


class _Probe:
    """1局ぶんの反実仮想を集める。`run_loop` の decide をラップして局面を掴む。"""

    def __init__(self, script, seed: int, loops: int, collect: bool = True):
        self.script = replace(script, loops=loops)
        self.seed = seed
        self.collect = collect
        self.mm = HeuristicMastermind(seed)
        self.hp = HeuristicProtagonist(seed)
        self.rows: list[dict] = []
        self.c: Counter = Counter()
        self.state: GameState | None = None
        self._done_this_turn: tuple | None = None

    # -- 本体 ------------------------------------------------------------
    def run(self):
        state = GameState(script=self.script)
        self.state = state
        agents = {"mastermind": self.mm, "p1": self.hp, "p2": self.hp, "p3": self.hp}
        log: list[dict] = []
        base = _make_decider(state, agents, log)

        def hooked(actor, decision, options):
            if (self.collect and decision == "set_card" and actor != "mastermind"
                    and not [p for p in state.turn_placements
                             if p["owner"] != "mastermind"]):
                key = (state.loop_no, state.day)
                if self._done_this_turn != key:
                    self._done_this_turn = key
                    try:
                        self._measure(state)
                    except Exception as e:      # 計測の失敗で本局を壊さない
                        self.c[f"計測例外＝{type(e).__name__}"] += 1
            return base(actor, decision, options)

        run_loop(state, hooked)
        return state, log

    # -- 反実仮想 --------------------------------------------------------
    def _measure(self, state: GameState) -> None:
        mm_set = _mm_placements(state)
        if len(mm_set) < 2:
            self.c["対象外＝脚本家の伏せ札が2枚未満（縮退）"] += 1
            return
        mm_targets = {p["target"] for p in mm_set}
        gensou = _gensou_boards(state)
        cands = [a for a in AREAS if a not in mm_targets and a not in gensou]
        if not cands:
            self.c["対象外＝ブラフを置ける板が無い"] += 1
            return

        # 供出する1枚＝最後のキャラ対象（無ければ最後の伏せ札）
        idx = next((i for i in range(len(mm_set) - 1, -1, -1)
                    if mm_set[i]["target_kind"] == "character"), len(mm_set) - 1)
        sac = mm_set[idx]
        keep = [p for i, p in enumerate(mm_set) if i != idx]

        # 残り手札から (A) に使える札を選ぶ（暗躍+ は除外＝解決されてしまう）
        hand = list(state.hand_of("mastermind"))
        for p in keep:
            if p["card"] in hand:
                hand.remove(p["card"])
        bluffs = [c for c in _BLUFF_PREF if c in hand and c not in ANRYAKU_PLUS]
        if not bluffs:
            self.c["対象外＝(A) に使える非暗躍札が手札に無い"] += 1
            return
        bluff = bluffs[0]

        # 脚本家の資金化（§1i の二正面）＝本人の分析器を呼ぶ（写さない）
        try:
            from sim import mastermind_view
            a = self.mm._analyze(mastermind_view(state))
            funded = sorted(a.get("funded") or ())
        except Exception:
            funded = ["?"]

        # ★例示 h＝「実弾の両置き」の対照。残り手札に 暗躍+1 があるときだけ組める。
        live = "暗躍+1" if "暗躍+1" in hand else None

        arm0, _l0 = _arm(state, self.hp, mm_set)
        arm1, lines1 = _arm(state, self.hp, keep)
        ry_board = _ry_goal_board(state)
        ba = dict(state.board_anyaku)

        for b in cands:
            arm2, _l2 = _arm(state, self.hp, keep + [{"owner": "mastermind", "card": bluff,
                                                      "target": b, "target_kind": "board"}])
            arm3 = None
            if live:
                arm3, _l3 = _arm(state, self.hp,
                                 keep + [{"owner": "mastermind", "card": live,
                                          "target": b, "target_kind": "board"}])
            moved = [i for i in range(3) if _key(arm1[i]) != _key(arm2[i])]
            lured = [i for i in range(3)
                     if _key(arm2[i]) == ("暗躍禁止", b, "board")
                     and _key(arm1[i]) != ("暗躍禁止", b, "board")]
            lured3 = ([i for i in range(3)
                       if _key(arm3[i]) == ("暗躍禁止", b, "board")
                       and _key(arm1[i]) != ("暗躍禁止", b, "board")] if arm3 else [])
            self.rows.append({
                "script": getattr(self.script, "name", None),
                "seed": self.seed, "loop": state.loop_no, "day": state.day,
                "board": b, "ry": getattr(state.script, "rule_y", None),
                "ry_board": ry_board, "is_ry_board": (b == ry_board),
                "board_anyaku": ba.get(b, 0),
                "funded": funded, "n_funded": len([f for f in funded if f != "?"]),
                # ★相手（主人公）から見て暗躍禁止を要する筋（arm1＝ブラフ無しの局面で数える）
                "kinshi_lines": lines1, "n_kinshi_lines": len(lines1),
                "bluff_card": bluff, "live_card": live,
                "sac": [sac["card"], sac["target"], sac["target_kind"]],
                "mm_set": [[p["card"], p["target"], p["target_kind"]] for p in mm_set],
                "arm0": [_key(x) for x in arm0],
                "arm1": [_key(x) for x in arm1],
                "arm2": [_key(x) for x in arm2],
                "arm3": ([_key(x) for x in arm3] if arm3 else None),
                "lured3": lured3,
                "moved": moved, "lured": lured,
                "same_as_arm0": [_key(arm0[i]) == _key(arm2[i]) for i in range(3)],
            })


# ---------------------------------------------------------------------------
# verify＝**本局が 1bit も汚れていない**ことと、arm0 が本局と一致することの実証
# ---------------------------------------------------------------------------
def _plain(script, seed: int, loops: int) -> tuple:
    hp = HeuristicProtagonist(seed)
    st, log = _run(script, seed, loops, hp=hp)
    return st, log


def _run(script, seed: int, loops: int, hp=None):
    from sim import run_game
    hp = hp or HeuristicProtagonist(seed)
    return run_game(replace(script, loops=loops),
                    {"mastermind": HeuristicMastermind(seed),
                     "p1": hp, "p2": hp, "p3": hp})


def _sig(log: list[dict]) -> list:
    return [(e["loop"], e["day"], e["actor"], e["decision"],
             tuple(sorted(e["chosen"].items(), key=lambda kv: kv[0])))
            for e in log]


def verify(days: int = 3, loops: int = 8, start: int = 0, end: int | None = 6) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    bad: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        st_a, log_a = _run(sc, seed, loops)                     # 素の対局
        pr = _Probe(sc, seed, loops, collect=True)              # 計測つき対局
        st_b, log_b = pr.run()
        ok = (_sig(log_a) == _sig(log_b) and _outcome(st_a) == _outcome(st_b)
              and st_a.loop_no == st_b.loop_no)
        c["棋譜一致" if ok else "★棋譜不一致"] += 1
        if not ok:
            bad.append({"script": name, "seed": seed})
        # arm0（複製上の再現）が本局の決定と一致するか
        by_turn = {}
        for e in log_b:
            if e["decision"] == "set_card" and e["actor"] != "mastermind":
                by_turn.setdefault((e["loop"], e["day"]), []).append(
                    (e["chosen"]["card"], e["chosen"]["target"],
                     e["chosen"]["target_kind"]))
        seen = set()
        for r in pr.rows:
            k = (r["loop"], r["day"])
            if k in seen:
                continue
            seen.add(k)
            real = by_turn.get(k)
            c["arm0=本局" if real == [tuple(x) for x in r["arm0"]]
              else "★arm0≠本局"] += 1
            if real != [tuple(x) for x in r["arm0"]] and len(bad) < 10:
                bad.append({"script": name, "seed": seed, "turn": k,
                            "real": real, "arm0": r["arm0"]})
        c.update(pr.c)
    return {"days": days, "counts": dict(c), "bad": bad[:10]}


# ---------------------------------------------------------------------------
# q1/q2/q3＝ファネル
# ---------------------------------------------------------------------------
def q1(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
       dump: str | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    by_board: dict = {}
    by_ry: dict = {}
    by_funded: dict = {}
    by_lines: dict = {}
    displaced: Counter = Counter()
    gained: Counter = Counter()
    rows_all: list = []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        pr = _Probe(sc, seed, loops, collect=True)
        pr.run()
        n += 1
        c.update(pr.c)
        for r in pr.rows:
            r["script"] = name
            rows_all.append(r)
            b = r["board"]
            key_ry = (f'{r["ry"]}／{"★本命板そのもの" if r["is_ry_board"] else "本命板でない板"}'
                      f'（ルールYの板={r["ry_board"]}）')
            kf = ("二正面(funded≥2)" if r["n_funded"] >= 2
                  else ("単線(funded=1)" if r["n_funded"] == 1 else "資金ゼロ(funded=0)"))
            nk = r["n_kinshi_lines"]
            kk = f'★暗躍禁止を要する筋＝{"2本以上" if nk >= 2 else f"{nk}本"}'
            for d, k in ((by_board, b), (by_ry, key_ry), (by_funded, kf),
                         (by_lines, kk)):
                s = d.setdefault(k, Counter())
                s["機会（ターン×板）"] += 1
                s["座＝3×機会"] += 3
                s["動いた座(arm1→arm2)"] += len(r["moved"])
                s["★釣れた座（板Bへ暗躍禁止）"] += len(r["lured"])
                if not r["moved"]:
                    s["★無償だった機会（3座とも不動）"] += 1
                if r["lured"]:
                    s["釣れた機会"] += 1
                if r["arm3"] is not None:
                    s["arm3を組めた機会"] += 1
                    s["arm3（実弾）で釣れた座"] += len(r["lured3"])
            c["機会（ターン×板）"] += 1
            c["座"] += 3
            c["動いた座(arm1→arm2)"] += len(r["moved"])
            c["★釣れた座"] += len(r["lured"])
            c["★無償だった機会（3座とも不動）"] += 0 if r["moved"] else 1
            for i in r["lured"]:
                displaced[f'{r["arm1"][i][0]}→{r["arm1"][i][2]}'] += 1
                gained[f'板{b}(暗躍{r["board_anyaku"]})'] += 1
    out = {"days": days, "n_games": n, "counts": dict(c),
           "★by_kinshi_lines": {k: dict(v) for k, v in sorted(by_lines.items())},
           "by_board": {k: dict(v) for k, v in sorted(by_board.items())},
           "by_ry": {k: dict(v) for k, v in sorted(by_ry.items())},
           "by_funded": {k: dict(v) for k, v in sorted(by_funded.items())},
           "押しのけた手（釣れた座）": dict(displaced.most_common()),
           "釣った板": dict(gained.most_common(20))}
    if dump:
        with open(dump, "w", encoding="utf-8") as f:
            for r in rows_all:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out["dump"] = dump
        out["n_rows"] = len(rows_all)
    return out


# ---------------------------------------------------------------------------
# q2＝採算（★ユーザーの論点＝「席の価値は脚本家の方が高いので1対1の交換は損」）
# ---------------------------------------------------------------------------
# 主人公の札が脚本家の札を実際に無効化する組（`rules/10_action_cards.md:56-63`）。
#   暗躍禁止＝重なった暗躍+1/+2 を無効化／移動禁止＝重なった移動カードを無効化。
#   不安-1＝無効化ではないが同一対象の 不安+1 と相殺（＝打点を消す）＝「相殺」として別に数える。
_CANCEL = {"暗躍禁止": frozenset(ANRYAKU_PLUS),
           "移動禁止": frozenset({"移動↑↓", "移動←→", "移動斜め"})}
_OFFSET = {"不安-1": frozenset({"不安+1"})}


def _blocked(seats: list, mm_cards: list) -> tuple[int, int, list]:
    """(無効化された枚数, 相殺された枚数, 内訳)。seats＝(card,target,kind) の3座。"""
    hard = soft = 0
    detail = []
    for card, tgt, _kind in mm_cards:
        for s in seats:
            if s[1] != tgt:
                continue
            if card in _CANCEL.get(s[0], ()):
                hard += 1
                detail.append(f"無効化 {s[0]}→{tgt}({card})")
                break
            if card in _OFFSET.get(s[0], ()):
                soft += 1
                detail.append(f"相殺 {s[0]}→{tgt}({card})")
                break
    return hard, soft, detail


def _anyaku_through(seats: list, mm_cards: list) -> int:
    """その腕で**実際に盤/キャラに乗る暗躍点**（暗躍+1=1・+2=2・暗躍禁止で消えた分は0）。

    ★これが脚本家の通貨（`agents/heuristic.path_costs`＝「あと何個の暗躍が要るか」）＝
    採算をカード枚数でなく**暗躍点**で数え直すための量。"""
    tot = 0
    for card, tgt, _kind in mm_cards:
        v = ANRYAKU_PLUS.get(card)
        if not v:
            continue
        if any(s[0] == "暗躍禁止" and s[1] == tgt for s in seats):
            continue
        tot += v
    return tot


def q2(path: str) -> dict:
    """`q1 --dump` の JSONL を読んで採算を数える（★再実行は不要＝同じ計測の別の集計）。"""
    c: Counter = Counter()
    by_funded: dict = {}
    by_isry: dict = {}
    by_sac: dict = {}
    by_lines: dict = {}
    ex: list = []
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(ln) for ln in f if ln.strip()]
    for r in rows:
        mm_set = [tuple(x) for x in r["mm_set"]]
        sac = tuple(r["sac"])
        keep = list(mm_set)
        keep.remove(sac)                     # 供出した1枚（重複時はどれでも同一）
        a0 = [tuple(x) for x in r["arm0"]]
        a1 = [tuple(x) for x in r["arm1"]]
        a2 = [tuple(x) for x in r["arm2"]]
        h0, s0, _d0 = _blocked(a0, mm_set)   # 実手3枚のうち封じられた数
        h1, s1, _d1 = _blocked(a1, keep)     # 供出のみ（ブラフ無し）＝2枚のうち
        h2, s2, d2 = _blocked(a2, keep)      # ブラフあり＝残した2枚のうち
        # 実際に通った実札の枚数（★脚本家の取り分＝多いほど良い）
        thru0 = len(mm_set) - h0
        thru1 = len(keep) - h1
        thru2 = len(keep) - h2
        # ★暗躍点の会計（枚数でなく通貨で数える）。sac が非暗躍札なら arm0 と arm2 の
        #   暗躍供給は**同一**＝比較が対等になる（sac が暗躍札の機会は別層に分ける）。
        sac_any = sac[0] in ANRYAKU_PLUS
        an0 = _anyaku_through(a0, mm_set)
        an1 = _anyaku_through(a1, keep)
        an2 = _anyaku_through(a2, keep)
        # ★arm3＝例示 h（実弾の両置き）。板 B に置いた 暗躍+1 も勘定に入れる。
        a3 = [tuple(x) for x in r["arm3"]] if r.get("arm3") else None
        an3 = None
        if a3 is not None:
            an3 = _anyaku_through(
                a3, keep + [(r["live_card"], r["board"], "board")])
        kf = ("二正面(funded>=2)" if r["n_funded"] >= 2
              else ("単線(funded=1)" if r["n_funded"] == 1 else "資金ゼロ(funded=0)"))
        ky = "本命板(ルールYの板)" if r["is_ry_board"] else "本命でない板"
        ks = "供出したのが暗躍札" if sac_any else "★供出したのが非暗躍札（暗躍供給は不変）"
        nk = r.get("n_kinshi_lines", -1)
        kk = f'★暗躍禁止を要する筋＝{"2本以上" if nk >= 2 else f"{nk}本"}'
        for d, k in ((by_funded, kf), (by_isry, ky), (by_sac, ks), (by_lines, kk)):
            t = d.setdefault(k, Counter())
            t["機会"] += 1
            t["arm0 通った実札(/3)"] += thru0
            t["arm1 通った実札(/2)"] += thru1
            t["arm2 通った実札(/2)"] += thru2
            t["★arm1→arm2 で救われた実札"] += (h1 - h2)
            t["★arm0→arm2 の実札収支"] += (thru2 - thru0)
            t["arm0 通った暗躍点"] += an0
            t["arm1 通った暗躍点"] += an1
            t["arm2 通った暗躍点"] += an2
            t["★arm0→arm2 の暗躍点収支"] += (an2 - an0)
            t["★arm1→arm2 の暗躍点収支"] += (an2 - an1)
            t["釣れた座"] += len(r["lured"])
            t["動いた座"] += len(r["moved"])
            if an3 is not None:
                t["arm3を組めた機会"] += 1
                t["arm3 通った暗躍点（実弾を含む）"] += an3
                t["  同機会の arm2 通った暗躍点"] += an2
                t["  同機会の arm1 通った暗躍点"] += an1
                t["★arm2→arm3 の暗躍点収支（実弾−ブラフ）"] += (an3 - an2)
                t["arm3（実弾）で釣れた座"] += len(r.get("lured3") or ())
                t["arm3 で実弾が止められた機会"] += (
                    1 if any(s[0] == "暗躍禁止" and s[1] == r["board"] for s in a3) else 0)
        c["機会"] += 1
        c["★arm1→arm2 で救われた実札（無効化）"] += (h1 - h2)
        c["★arm1→arm2 で救われた実札（相殺）"] += (s1 - s2)
        c["★arm0→arm2 の実札収支（通った枚数の差）"] += (thru2 - thru0)
        c["★arm0→arm2 の暗躍点収支"] += (an2 - an0)
        c["★arm1→arm2 の暗躍点収支"] += (an2 - an1)
        c["供出した札＝" + sac[0]] += 1
        if h1 - h2 > 0 and len(ex) < 25:
            ex.append({"script": r["script"], "seed": r["seed"], "loop": r["loop"],
                       "day": r["day"], "board": r["board"], "ry": r["ry"],
                       "mm_set": r["mm_set"], "sac": r["sac"],
                       "bluff": r["bluff_card"], "funded": r["funded"],
                       "arm1": r["arm1"], "arm2": r["arm2"],
                       "arm1が封じていた": _d1, "arm2が封じた": d2})
    return {"n_rows": len(rows), "counts": dict(c),
            "★by_kinshi_lines": {k: dict(v) for k, v in sorted(by_lines.items())},
            "by_funded": {k: dict(v) for k, v in sorted(by_funded.items())},
            "by_is_ry_board": {k: dict(v) for k, v in sorted(by_isry.items())},
            "by_sac": {k: dict(v) for k, v in sorted(by_sac.items())},
            "examples": ex}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-168m 板ブラフの実効（計測のみ）")
    ap.add_argument("mode", choices=["verify", "q1", "q2"])
    ap.add_argument("--rows", default=None, help="q2: q1 --dump の JSONL")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--dump", default=None)
    a = ap.parse_args(argv)
    if a.mode == "verify":
        r = verify(days=a.days, loops=a.loops, start=a.start,
                   end=(a.end if a.end is not None else 6))
    elif a.mode == "q2":
        r = q2(a.rows)
    else:
        r = q1(days=a.days, loops=a.loops, start=a.start, end=a.end, dump=a.dump)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
