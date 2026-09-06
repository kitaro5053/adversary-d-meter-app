# -*- coding: utf-8 -*-
"""B-266：牡丹 seed0〔殺人事件が算術的に止まらない局〕の検死（★測定のみ・修正は範囲外）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-77。
教材＝`docs/feedback_logs/牡丹_BTX3d_seed0_殺人計画_2026-08-19.jsonl`
（build `771b1dc`・ユーザーが脚本家・AI が主人公・**6ループで脚本家の勝ち**）。
結果 doc＝`docs/検死_B266_牡丹BTX3d_seed0_2026-08-19.md`。

## この道具がやること

`arena/b251_audit.py` の再生機構（脚本家＝記録の再生／主人公＝本物の AI に打たせ直す／
`ProbedProtagonist` が本物の採点クロージャを横取り）を**書き写さず import して**使う。
本道具が足すのは以下だけ：

1. `verify` … 再生の bit 一致。★b251 の `replay()` は脚本家の記録手が候補に無いと
   **例外で止まって live ログを捨てる**ので、本道具は live を**呼び出し側の可変リスト**に
   持たせて例外時も残す（＝**最初に食い違った席**を必ず名指しできる）。
2. `seats` … 全ループ・全日の主人公3枚を**多重集合**で並べ、ループ間の同一性行列を出す
   （★席は leader 交代で回るので、席名で比べると同型を見落とす）。
3. `arith` … ★**発注前の検算**＝犯人（既定 `男子学生`）の不安の増減を
   フェイズ順（`rules/00_rules_core.md:100-107`）で台帳にし、
   **その日に主人公陣営が使えた冷却の最大値**を実状態から数え上げて
   「最大限冷やしても事件フェイズに臨界以上か」を判定する。
   ★冷却チャネルは**カードと友好能力の両方**を数える（後述）。
4. `rank` … 指定の札／対象が各席で何点・何位だったか（b251 の `cmd_rank` と同趣旨だが、
   **食い違いを許容して部分結果でも出す**）。
5. `evade` … ★**退避の手**＝守る対象（既定 `女子学生`）を犯人のエリアから
   **出せる移動札**が候補に現れているか・何点・何位か。
   出せるかどうかは `sim.effects` の**本物の移動解決**で判定する（述語を書き写さない）。
6. `bounds` … B-256／B-262 の到達可能上限（`U_p` / `U_pm` / `U_b`）を**犯人側**に当て、
   さらにその**双対＝下限 `L`**（＝「最大限冷やしても残る不安」）を並べて比較する。
7. `internals` … 席ごとの「退避の点」と、その点を決めている内部推定。
8. ★`hashseed` … **収録時の `PYTHONHASHSEED` を掃引で当てる**（2026-08-20 追加・下記）。
9. ★`ulp` … **1 ULP のナイフエッジの席**を数える（同上）。
10. ★`pin` … 1席だけ棋譜の手に固定して残りを再生する（席間協調の玉突きの検証）。

## ★★教材の bit 一致は「1つの hash seed」では担保できない（2026-08-20・B-266 の発見）

`agents/heuristic_protagonist.py:2210` は
`self._virus_test_targets = {n for _p, n in sorted(cand, reverse=True)[:2]}`
＝`cand = [(P(パーソン), 名前), ...]` の**上位2名**を採る。
`P(パーソン)` は `agents/belief.py:2313-2316` の `cnt / total`＝**数え上げの合計順で最下位ビットが動く**。

∴ 2人の `P` が **1 ULP** だけ違うと、`sorted` はタプルの**第1要素（確率）で決着**し
（＝名前のタイブレークに**落ちない**）、**どちらが上位2枠に入るかが `PYTHONHASHSEED` で反転**する。
★差が **完全に 0.0** なら第2要素＝**名前**で決着するので **seed 非依存**
＝「完全一致」と「1 ULP 違い」で**再現性が正反対**になる。

本教材（牡丹）は L6D2 の3席がこの形で、
**seed 0/4/6〜10 では割れ、seed 1/2/3/5/11 では 122/122 完全一致**する。
★アプリ（Streamlit）は `PYTHONHASHSEED` を固定していないので、
**収録時の seed は棋譜に記録されていない**＝`tool_build` だけでは再生できない。
∴ **担保は「ある seed で完全一致すること」で取る**＝`hashseed` サブコマンド。
不変条件は `tests/test_b266_audit.py` で固定した。

## ★★B-278 以降＝この教材は **L2D1 以降 bit 再生できない**（2026-08-25・失効した前提）

★**上の「hash seed を掃引すれば完全一致する seed がある」は B-278 で失効した。**

この教材は **B-278（医者『不安操作（除去/付与）』の宣言順序の是正）より前の規則で収録**
されている。旧規則の誤りは2つ（KB `rules/20_goodwill_abilities.md:14-18` 共通手順・`:228`）：

- 除去/付与の宣言を**拒否のあと**（効果解決の内側）でしか選ばせなかった。
- 対象の不安が **0 のときは候補が『置く』だけ**で、空撃ち（`:228`＝不安0の相手に
  「取り除く」と宣言してよい）を選べず、**冷却のつもりの発動が強制的に不安+1**になった。

本教材では医者の発動9回のうち**7回がこの強制『置く』**（女子学生+1 が2回・男子学生+1 が3回・
サラリーマン+1 が2回／『取り除く』はわずか2回）。∴ 現行コードで再生すると

1. 棋譜に**存在しない宣言決定**（`doctor_unrest_mode`）を再生側が余分に出す
   ＝**#27（L2D1 p2）で必ず割れる**（`b278_declaration_insertions` が件数を返す）。
2. その宣言が『取り除く』に変わる＝**盤面そのものが分岐**する。

★**これは道具の壊れでも棋譜の壊れでもなく、規則が直ったこと自体の帰結**であり、
`PYTHONHASHSEED` の掃引では**直らない**（seed 0 でも 1 でも同じ #27 で割れる）。

★**まだ測れること**（`aligned_first_mismatch`）＝両側から宣言決定を落として整列すると
**77手目まで（L4D3 の直前まで）は一手も割れない**。規則差が主人公の手に効き始めるのは
**L4D3 p3 の `set_card`**。∴「規則差が効き始める点」は今も定量できる。

★**測れなくなったこと**＝この教材での **bit 一致そのもの**（era ピンの有無に依らない）。
1 ULP のナイフエッジについての結論は `docs/バックログ_構想メモ_FableA.md` §72-89
（B-273 フェーズ0＝**本体に着手しない**＝負の結果）で**独立に land 済み**なので、
**この教材の bit 一致から再導出してはいけない**。

## 挙動には触れない

`agents/` `sim/` `engine/` `rules/` を**1バイトも変更しない**。
`arena.knob_audit.check_baseline` と `arena.b249_audit.check_no_knob_writes` の二重で守る。

## era ピン（B-260）

教材 build `771b1dc` と `origin/main` の差分は `agents/heuristic_protagonist.py` の
**B-256 の切替口の追加のみ（既定 OFF＝挙動 bit 不変）**＝収録当時と現行で挙動が同じなので
ピンは**倒すものが無い**。ただし B-267（B-256＋B-262 を対で既定 ON）が land すると
挙動が変わるため、**そのときに倒すべき値を先に表として持っておく**（`ERA_PINS`）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b266_audit verify
    python -m arena.b266_audit seats
    python -m arena.b266_audit arith
    python -m arena.b266_audit rank --card 暗躍禁止 --target 女子学生
    python -m arena.b266_audit evade
    python -m arena.b266_audit bounds
"""
from __future__ import annotations

import argparse
import os
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from agents.debug import ProbedProtagonist
from arena.b251_audit import (  # noqa: F401  ★書き写さず import する（§72-34）
    PROBE_ATTRS, _ProbeX, _fmt, _strip, era_banner, era_pin, load_log,
)
from arena.b256_audit import bounds as b256_bounds
from sim import flow
from sim.state import GameState, script_from_dict

#: 教材棋譜（B-266 のチケットが指定した1本）。
DEFAULT_LOG = (Path(__file__).resolve().parent.parent / "docs" / "feedback_logs"
               / "牡丹_BTX3d_seed0_殺人計画_2026-08-19.jsonl")

#: ★収録当時（build `771b1dc`・2026-08-19）へ倒す表。
#:
#: ★**2026-08-20 に有効化した**＝B-267（`2fc59de`）が land して
#:   `B256_UNREACHABLE_SKIP` / `B262_BELIEF_BOUND` が**既定 ON** になった＝
#:   収録当時（両方 OFF）と現行 main で**実効挙動が違う**。
#: ★倒すのは `skip` だけ（`arena/b251_audit.ERA_PINS` と同じ理由）＝
#:   `bel` は `if self.B256_UNREACHABLE_SKIP:` ガードの内側でしか読まれない
#:   （`agents/heuristic_protagonist.py` の候補プール段）ので、`skip` を倒せば `bel` は発火しない。
#: ★`B252_CULT_FLOOR` は**倒さない**＝本教材の収録 build `771b1dc` は
#:   B-252(a) 既定 ON（`909cfc7`）の**後**なので、収録当時から True。
#:   （`arena/b251_audit` の教材＝鈴蘭は床 0.1 時代の収録なので倒す。**教材ごとに era が違う**
#:   ＝§72-70 の「床のピンを他へ機械的に流用してはいけない」の実例。）
ERA_PINS: tuple = (
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B256_UNREACHABLE_SKIP", False),
)

#: 再生の前提として毎回印字する定数。
AUDITED_CONSTS: tuple = (
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B252_CULT_FLOOR"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B256_UNREACHABLE_SKIP"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B256_INCLUDE_MM"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B262_BELIEF_BOUND"),
)

#: 本局の犯人と守る対象（`meta.script` から取れるが、CLI で差し替え可能にする）。
CULPRIT = "男子学生"
PROTECTEE = "女子学生"

#: 主人公が持つ**冷却カード**（`rules/10_action_cards.md:20-27`）。★1ループに1回。
COOL_CARD = "不安-1"
#: 主人公の手札に `不安禁止` は**存在しない**（`rules/10_action_cards.md:20-27` の8種）。
#: ∴ 脚本家の `不安+1` を**打ち消す**手段は `不安-1`（差引0）しかない。
PROT_HAND_HAS_UNREST_BAN = False


#: B-266 が `decide` の**後**に読む内部推定（★読むだけ＝採点・選択に触れない）。
B266_PROBE_ATTRS = (
    "_keyperson", "_killer", "_known_culprits", "_culprit_cands", "_lethal_days",
    "_incident_danger", "_kp_prob", "_kp_guard", "_killer_suspects", "_kinshi_used",
)


class _ProbeB266(_ProbeX):
    """`_ProbeX` に B-266 の内部推定を1件足すだけ（`super().decide()` の**後**に読む）。"""

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        snap: dict = {}
        for name in B266_PROBE_ATTRS:
            val = getattr(self, name, None)
            if isinstance(val, (set, frozenset)):
                val = sorted(val)
            elif isinstance(val, dict):
                val = {k: (sorted(v) if isinstance(v, (set, frozenset)) else v)
                       for k, v in val.items()}
            snap[name] = val
        # ★B-266：ウイルス試験対象の「上位2枠の切り口」を**その席の belief 実物**から読む。
        #   ★`belief_after()` で作り直した belief とは**最下位ビットが違う**ことがある
        #   （逐次 observe と一括 observe で合計順が変わるため）＝必ず実物を読むこと。
        try:
            marg = self._belief.role_marginals()
            snap["person_cand"] = sorted(
                ((d.get("パーソン", 0.0), n) for n, d in marg.items()
                 if d.get("パーソン", 0.0) > 0.1), reverse=True)
        except Exception as exc:                   # noqa: BLE001
            snap["person_cand"] = f"ERR {exc}"
        self.records[-1]["b266"] = snap
        return chosen


class _MMReplayTolerant:
    """記録された脚本家の選択を順に返す。★候補に無ければ**印を残して**先頭を返す。

    b251 の `_MMReplay` は例外で止まる（＝再生の担保として正しい）。本道具は
    **どこで初めて食い違ったか**を必ず名指ししたいので、止めずに続行して記録する。
    `verify` は `self.desync` が空でないことを RC=1 として扱う。
    """

    wants_bluff_options = True

    def __init__(self, seq: list[dict]):
        self._seq = list(seq)
        self._i = 0
        self.desync: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if self._i >= len(self._seq):
            self.desync.append({"i": self._i, "decision": decision,
                                "why": "脚本家の選択列が尽きた"})
            return options[0]
        chosen = self._seq[self._i]
        self._i += 1
        if chosen not in options:
            self.desync.append({"i": self._i, "decision": decision, "chosen": chosen,
                                "loop": view.get("loop"), "day": view.get("day"),
                                "why": "記録手が候補に無い", "options": options})
            return options[0]
        return chosen


def replay266(path: Path, top: int = 999):
    """棋譜を再生し **(state, meta, ai, live, mm, err)** を返す（★例外でも live を捨てない）。"""
    meta, decisions = load_log(path)
    script = replace(script_from_dict(meta["script"]), loops=int(meta["loops_played"]))
    mm_seq = [d["chosen"] for d in decisions if d["actor"] == "mastermind"]
    ai = _ProbeB266(0, top=top)
    state = GameState(script=script)
    live: list[dict] = []
    mm = _MMReplayTolerant(mm_seq)
    decide = flow._make_decider(state, {"mastermind": mm, "p1": ai, "p2": ai, "p3": ai}, live)
    err = None
    try:
        flow.run_loop(state, decide, final_battle=False)
    except Exception as exc:                      # noqa: BLE001  部分結果を残すため握る
        err = f"{type(exc).__name__}: {exc}"
    return state, meta, ai, live, mm, err


def first_mismatch(live: list[dict], ref: list[dict]) -> list[str]:
    """再生と棋譜の食い違いを**最初の1件から**列挙する。"""
    bad: list[str] = []
    for i, (a, b) in enumerate(zip(live, ref)):
        ka = (a["loop"], a["day"], a["actor"], a["decision"])
        kb = (b["loop"], b["day"], b["actor"], b["decision"])
        if ka != kb or _strip(a["chosen"]) != _strip(b["chosen"]):
            bad.append(f'#{i} 再生 L{ka[0]}D{ka[1]} {ka[2]} {ka[3]} {_strip(a["chosen"])} '
                       f'／ 棋譜 L{kb[0]}D{kb[1]} {kb[2]} {kb[3]} {_strip(b["chosen"])}')
    if len(live) != len(ref):
        bad.append(f"決定数が違う: 再生{len(live)} / 棋譜{len(ref)}")
    return bad


#: ★B-278 で新設された「[主] の宣言」決定（KB `rules/20_goodwill_abilities.md:14-18`）。
#:   B-278 以前に収録された棋譜には**原理的に存在しない**（旧実装は拒否のあとにしか
#:   選ばせず、対象の不安0では選択そのものが無かった）。
B278_DECLARE_DECISIONS: tuple = ("doctor_unrest_mode",)


def b278_declaration_insertions(live: list[dict], ref: list[dict]) -> int:
    """再生が棋譜より**多く**出した「[主] の宣言」決定の数（B-278 以前の収録なら > 0）。

    ★この値が正なら、食い違いの先頭は「棋譜に無い宣言決定が挿入された」ことであって
    AI の判断が変わったことではない＝**hash seed の掃引では直らない**。
    """
    n_live = sum(1 for d in live if d["decision"] in B278_DECLARE_DECISIONS)
    n_ref = sum(1 for d in ref if d["decision"] in B278_DECLARE_DECISIONS)
    return n_live - n_ref


def aligned_first_mismatch(live: list[dict], ref: list[dict]) -> list[str]:
    """★B-278 の挿入を**両側から落として整列**した上での食い違い。

    「宣言決定が増えた」ぶんの位置ずれを除いてなお残る食い違い＝**規則差が実際に
    主人公の手を変えた点**。本教材ではそこが L4D3＝それより前は一手も割れない
    （＝B-278 以降も「どこから測れなくなったか」は定量できる）。
    """
    f = [d for d in live if d["decision"] not in B278_DECLARE_DECISIONS]
    g = [d for d in ref if d["decision"] not in B278_DECLARE_DECISIONS]
    return first_mismatch(f, g)


def knob_baseline_banner() -> str:
    """`arena/knob_audit.check_baseline` を配線する（B-248 の一般形・退化形）。"""
    from arena import knob_audit
    knob_audit.check_baseline({}, {}, (), driver="arena.b266_audit")
    return "knob_audit.check_baseline（空の条件表＝退化形）✅"


# ---------------------------------------------------------------------------
# 発注前の検算＝不安の台帳（`arith`）
# ---------------------------------------------------------------------------

def unrest_ledger(meta: dict, who: str) -> list[dict]:
    """`meta.history` から `who` の不安の増減をフェイズ順の台帳にする（★公開情報のみ）。

    数える増減チャネル（`rules/00_rules_core.md:100-107` のフェイズ順）：
      - フェイズ4 行動解決＝`cards_revealed` の `不安+1` / `不安-1`（打ち消しは `不安禁止`）
      - フェイズ5 脚本家能力＝`unrest` イベント（`phase == "mastermind_ability"`）
      - フェイズ6 主人公能力＝`unrest` イベント（`phase == "goodwill_ability"`）
      - フェイズ7 事件＝`unrest` イベント（`phase == "incident"`）
    """
    rows: list[dict] = []
    cur: dict | None = None
    for e in meta["history"]:
        lp, dy, ph = e.get("loop"), e.get("day"), e.get("phase")
        if e.get("event") == "loop_start":
            continue
        key = (lp, dy)
        if cur is None or (cur["loop"], cur["day"]) != key:
            cur = {"loop": lp, "day": dy, "cards": [], "steps": [],
                   "incident": None, "death": None}
            rows.append(cur)
        ev = e.get("event")
        if ev == "cards_revealed":
            cur["cards"] = [dict(p) for p in e.get("placements", [])]
        elif ev == "unrest" and e.get("target") == who:
            cur["steps"].append({"phase": ph, "delta": int(e.get("delta", 0))})
        elif ev == "incident":
            cur["incident"] = {"name": e.get("name"), "occurs": e.get("occurs"),
                               "eligible": e.get("eligible")}
        elif ev == "death":
            cur["death"] = {"name": e.get("name"),
                            "present_unrest": e.get("present_unrest"),
                            "present": e.get("present")}
    return rows


def _card_delta(cards: list[dict], who: str) -> tuple[int, list[str]]:
    """行動解決フェイズでの `who` への不安増減（★`不安禁止` の打ち消しを含む）。"""
    mine = [c for c in cards if c.get("target") == who and c.get("target_kind") == "character"]
    banned = any(c.get("card") == "不安禁止" for c in mine)
    d, why = 0, []
    for c in mine:
        if c["card"] == "不安+1":
            why.append(f'{c["owner"]}:不安+1' + ("(禁止で無効)" if banned else ""))
            d += 0 if banned else 1
        elif c["card"] == "不安-1":
            why.append(f'{c["owner"]}:不安-1' + ("(禁止で無効)" if banned else ""))
            d += 0 if banned else -1
    return d, why


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def cmd_verify(args) -> int:
    path = Path(args.log)
    _meta, ref = load_log(path)
    state, meta, _ai, live, mm, err = replay266(path, top=1)
    print(f"棋譜: {path.name}  build(meta)={meta.get('tool_build')}")
    print(f"決定数: 再生={len(live)} / 棋譜={len(ref)}  winner={state.winner} loops={state.loop_no}")
    if err:
        print("  再生中の例外:", err)
    bad = first_mismatch(live, ref)
    for d in mm.desync:
        print(f'  脚本家側の食い違い: L{d.get("loop")}D{d.get("day")} {d["decision"]} '
              f'{d.get("chosen")} ← {d["why"]}')
    if bad:
        for b in bad[:20]:
            print("  NG:", b)
        print(f"NG {len(bad)} 件＝割れている（★割れた席より前の点数は有効）")
        ins = b278_declaration_insertions(live, ref)
        if ins > 0:
            print(f"   ★B-278：再生は棋譜に無い「[主] の宣言」決定を {ins} 件多く出している"
                  "＝**この教材は B-278 以前の規則で収録**されている"
                  "（医者『不安操作』の除去/付与を拒否のあとにしか選ばせず、"
                  "対象の不安0では強制的に『置く』だった）。")
            print("   ★∴ bit 一致は hash seed の掃引では回復しない。"
                  "整列後の食い違い（規則差が主人公の手を変えた点）＝"
                  + (next((b for b in aligned_first_mismatch(live, ref)
                           if b.startswith("#")), "なし")))
            print("   ★詳細＝`docs/検死_B266_牡丹BTX3d_seed0_2026-08-19.md` §2-1b。")
        else:
            print("   ★教材の収録時 hash seed は棋譜に記録されていない＝"
                  "`python -m arena.b266_audit hashseed` で掃引すること（B-266）。")
        return 1
    if state.to_dict() != meta["final_state"]:
        print("  NG: 最終状態が meta.final_state と一致しない")
        return 1
    print("OK: 主人公の全決定・最終状態が棋譜と一致（＝以降の点数はその席の実値）")
    return 0


def cmd_seats(args) -> int:
    """全ループ・全日の主人公3枚を**多重集合**で並べ、ループ間の同一性を判定する。"""
    _meta, ref = load_log(Path(args.log))
    by_day: dict = {}
    for d in ref:
        if d["decision"] != "set_card" or d["actor"] == "mastermind":
            continue
        ch = d["chosen"]
        by_day.setdefault((d["loop"], d["day"]), []).append(
            (d["actor"], f'{ch["card"]}→{ch.get("target")}'))
    print("主人公3枚（★席名は leader 交代で回るので多重集合で比べる）")
    ms: dict = {}
    for (lp, dy), items in sorted(by_day.items()):
        cards = sorted(x[1] for x in items)
        ms[(lp, dy)] = tuple(cards)
        seats = " ".join(f"{a}:{c}" for a, c in items)
        print(f"L{lp}D{dy}  {seats}")
    days = sorted({dy for _lp, dy in ms})
    loops = sorted({lp for lp, _dy in ms})
    print("\n同一性行列（多重集合が一致するループ対）")
    for dy in days:
        print(f" D{dy}:")
        for i, a in enumerate(loops):
            row = []
            for b in loops:
                if (a, dy) not in ms or (b, dy) not in ms:
                    row.append(" -")
                else:
                    row.append(" ○" if ms[(a, dy)] == ms[(b, dy)] else " ×")
            if (a, dy) in ms:
                print(f"   L{a}: " + "".join(row) + f"   {list(ms[(a, dy)])}")
    print("\nループ全体（そのループの全日の多重集合を連結）が一致するループ対:")
    whole = {lp: tuple(ms[(lp, dy)] for dy in days if (lp, dy) in ms) for lp in loops}
    for i, a in enumerate(loops):
        same = [b for b in loops if b != a and whole[b] == whole[a]]
        print(f"   L{a} == {same or 'なし'}  （日数={len(whole[a])}）")
    return 0


def cmd_arith(args) -> int:
    """★発注前の検算＝犯人の不安台帳と「最大限冷やした場合の下限」。"""
    path = Path(args.log)
    meta, ref = load_log(path)
    who = args.who
    sc = meta["script"]
    crit = args.critical
    print(f"犯人={who}（不安臨界={crit}・`rules/30_characters.md:30` 独立検証済み）")
    inc = [i for i in sc.get("incidents", [])]
    print(f"事件={inc}")
    rows = unrest_ledger(meta, who)
    # 各日の状態（エリア・友好）を history から復元できないので、棋譜の view を使う。
    view_by_day: dict = {}
    for d in ref:
        if d["decision"] == "set_card" and d["actor"] != "mastermind":
            view_by_day.setdefault((d["loop"], d["day"]), d["view"])
    u = 0
    cur_loop = None
    for r in rows:
        if r["loop"] != cur_loop:
            cur_loop = r["loop"]
            u = 0
            print(f"\n=== L{cur_loop} ===")
        cd, why = _card_delta(r["cards"], who)
        start = u
        u += cd
        line = [f'開始{start}', f'解決{cd:+d}({",".join(why) or "なし"})']
        for s in r["steps"]:
            u += s["delta"]
            line.append(f'{s["phase"]}{s["delta"]:+d}')
        v = view_by_day.get((r["loop"], r["day"])) or {}
        chars = {c["name"]: c for c in v.get("characters", [])}
        me = chars.get(who, {})
        # ★その日に主人公陣営が使えた**冷却の最大値**（実状態から数え上げる）
        cool = _max_cooling(v, chars, who, meta, r)
        floor = start + cd_supply_only(r, who) - cool["total"]
        print(f'L{r["loop"]}D{r["day"]} ' + " ".join(line) +
              f' → 事件フェイズ {u}  臨界{"到達" if u >= crit else "未達"}'
              f'  [最大冷却={cool["total"]}({cool["why"]}) → 下限={floor}]')
        if r["incident"]:
            print(f'        事件: {r["incident"]}')
        if r["death"]:
            print(f'        死亡: {r["death"]["name"]} 同室={r["death"]["present"]}')
    return 0


def cd_supply_only(r: dict, who: str) -> int:
    """その日に**実際に供給された分だけ**（冷却を除く）を数える。"""
    d = 0
    mine = [c for c in r["cards"]
            if c.get("target") == who and c.get("target_kind") == "character"]
    banned = any(c.get("card") == "不安禁止" for c in mine)
    for c in mine:
        if c["card"] == "不安+1" and not banned:
            d += 1
    for s in r["steps"]:
        if s["phase"] == "mastermind_ability" and s["delta"] > 0:
            d += s["delta"]
    return d


def _max_cooling(view: dict, chars: dict, who: str, meta: dict, row: dict) -> dict:
    """その日、主人公陣営が `who` に掛けられた**冷却の上限（寛容側）**を数える。

    数えるチャネル（★すべて事件フェイズ7より前に解決する＝`rules/00_rules_core.md:100-111`）：
      1. `不安-1` カード（フェイズ4）＝1キャラ1日1枚（`rules/00:104`＋`rules/10:81`）。
         **1ループ1回**（`rules/10:76`）なので、そのループでまだ使っていない主人公が
         1人でも居れば 1。
      2. **学生の不安除去**（♡2・`rules/20_goodwill_abilities.md:36-37` 定義は `:89-91`）
         ＝`who` と同一エリアの**他の学生**（男子学生／女子学生）が使う。フェイズ6。
         ★**1/L 制限が無い**＝毎日使える。♡2 に足りなければ、その日 `友好+1/+2` を
         1枚置いて届くかを見る（`友好+2` は 1/L・`rules/10:24`）。
      3. **医者の不安操作（除去）**（♡2・`rules/20:46` 定義は `:225-228`）＝同一エリアの医者。
         同じく足りなければ友好札1枚で届くかを見る。
    ★**寛容な上限**＝1日3枠という席の予算も、脚本家の `友好禁止`／`不安禁止` も考慮しない。
      ∴ 「この上限でも臨界を割れない」と言えたときだけ**算術的に間に合わない**と断言できる。
    """
    STUDENTS = ("男子学生", "女子学生")
    n, why = 0, []
    used = view.get("used_cards") or {}
    seats = ("p1", "p2", "p3")
    free_cool = [s for s in seats if COOL_CARD not in (used.get(s) or [])]
    if free_cool:
        n += 1
        why.append(f"不安-1札(残{len(free_cool)}人)")
    free_gw2 = [s for s in seats if "友好+2" not in (used.get(s) or [])]
    me = chars.get(who) or {}
    area = me.get("area")
    for name, c in chars.items():
        if name == who or not c.get("alive") or c.get("area") != area:
            continue
        gw = int(c.get("goodwill", 0))
        usable = (name in STUDENTS and who in STUDENTS) or name == "医者"
        if not usable:
            continue
        label = "学生の不安除去" if name in STUDENTS else "不安操作"
        if gw >= 2:
            n += 1
            why.append(f"{name}:{label}(♡{gw})")
        elif gw + 2 >= 2 and free_gw2:
            n += 1
            why.append(f"{name}:{label}(♡{gw}→友好+2で♡{gw + 2})")
        elif gw + 1 >= 2:
            n += 1
            why.append(f"{name}:{label}(♡{gw}→友好+1で♡{gw + 1})")
    return {"total": n, "why": ",".join(why) or "なし"}


def attach_views(ai, live) -> None:
    """`ProbedProtagonist.records` に `view` を後付けする（★記録は view を持たない）。

    `live`（`sim/flow._make_decider` の決定ログ）の主人公決定と `ai.records` は
    **同じ順序で1対1**＝インデックスで突き合わせる。食い違ったら例外で止める。
    """
    prot = [d for d in live if d["actor"] != "mastermind"]
    if len(prot) != len(ai.records):
        raise RuntimeError(f"決定数が合わない: live={len(prot)} records={len(ai.records)}")
    for d, r in zip(prot, ai.records):
        if (d["loop"], d["day"], d["decision"]) != (r["loop"], r["day"], r["decision"]):
            raise RuntimeError(f"並びが合わない: {d['loop']}D{d['day']} {d['decision']}")
        r["view"] = d["view"]


def cmd_rank(args) -> int:
    """指定した札／対象の点数と順位（★食い違いがあっても部分結果を出す）。"""
    path = Path(args.log)
    _meta, ref = load_log(path)
    _state, _m, ai, live, _mm, err = replay266(path, top=args.top)
    attach_views(ai, live)
    bad = first_mismatch(live, ref)
    cut = None
    if bad:
        cut = bad[0]
        print(f"★注意: 再生が割れている（{len(bad)}件）。最初の食い違い= {cut}")
    hits = 0
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        scored = r["scored"]
        total = len(scored)
        for i, (v, o) in enumerate(scored):
            if args.card and args.card not in str(o.get("card", "")):
                continue
            if args.target and args.target != o.get("target"):
                continue
            hits += 1
            top_v, top_o = scored[0]
            print(f'L{r["loop"]}D{r["day"]} {r["seat"]:3s} {_fmt(o):22s} '
                  f'{v:8.2f} {i + 1:3d}位/{total}  '
                  f'首位={_fmt(top_o)}({top_v:.2f}) 実手={_fmt(r["chosen"]):18s}')
    print(f"\n該当 {hits} 件")
    return 0


def _move_targets(view: dict, who: str) -> dict:
    """`who` に移動札を置いたときの移動先を**本物の盤面解決器**で調べる（述語を書き写さない）。

    `engine/board.py:41-60`（`compose_moves` / `destination`）と
    `engine/data.py:129`（`forbidden_of`）をそのまま呼ぶ。
    ★主人公が1枚だけ置いた場合の行き先＝脚本家の移動札が重なると合成で変わる
      （`rules/10_action_cards.md:44-52`）ので、これは「主人公だけが置いた場合」の値。
    """
    from engine.board import compose_moves, destination
    from engine.data import forbidden_of
    chars = {c["name"]: c for c in view["characters"]}
    area = (chars.get(who) or {}).get("area")
    out: dict = {}
    if area is None:
        return out
    ng = forbidden_of(who)
    for card in ("移動↑↓", "移動←→"):
        try:
            dest = destination(area, compose_moves([card]))
            out[card] = area if dest in ng else dest
        except Exception as exc:                  # noqa: BLE001
            out[card] = f"ERR {exc}"
    return out


def cmd_evade(args) -> int:
    """★退避の手＝守る対象を犯人のエリアから**出せる**移動札が候補にあるか／何位か。"""
    path = Path(args.log)
    _meta, ref = load_log(path)
    _state, _m, ai, live, _mm, err = replay266(path, top=args.top)
    attach_views(ai, live)
    bad = first_mismatch(live, ref)
    if bad:
        print(f"★注意: 再生が割れている（{len(bad)}件）。最初= {bad[0]}")
    who, culprit = args.who, args.culprit
    print(f"退避の対象={who} / 犯人={culprit}")
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        v = r["view"]
        chars = {c["name"]: c for c in v["characters"]}
        me, cu = chars.get(who) or {}, chars.get(culprit) or {}
        if not me.get("alive") or me.get("area") != cu.get("area"):
            continue
        dests = _move_targets(v, who)
        scored = r["scored"]
        total = len(scored)
        found = []
        for i, (val, o) in enumerate(scored):
            if o.get("target") != who or not str(o.get("card", "")).startswith("移動"):
                continue
            dest = dests.get(o["card"])
            ok = dest is not None and dest != cu.get("area") and not str(dest).startswith("ERR")
            found.append((i + 1, val, o["card"], dest, ok))
        top_v, top_o = scored[0]
        print(f'L{r["loop"]}D{r["day"]} {r["seat"]:3s} 同室({me.get("area")}) '
              f'実手={_fmt(r["chosen"]):18s} 首位={_fmt(top_o)}({top_v:.2f})')
        if not found:
            print("        ★退避の手が候補に**存在しない**")
        for rank, val, card, dest, ok in found:
            print(f'        {card}→{who} 行先={dest} '
                  f'{"★引き離せる" if ok else "引き離せない"} {val:8.2f} {rank:3d}位/{total}')
    return 0


#: ★B-266（2026-08-20）＝**同点の解け方が hash seed で変わる席**の検出。
#:
#: 機序（実測で確定）＝`agents/heuristic_protagonist.py:2187-2210` の
#:   `self._virus_test_targets = {n for _p, n in sorted(cand, reverse=True)[:2]}`
#: は `cand = [(P(パーソン), 名前), ...]` の**上位2名**を採る。
#: `P(パーソン)` は `agents/belief.py:2313-2316`（`cnt / total`）＝
#: **数え上げの合計順で最下位ビットが動く**。
#: ∴ 2人の `P` が **1 ULP** だけ違うと、`sorted` はタプルの第1要素で決着し
#: （＝名前によるタイブレークに**落ちない**）、**どちらが上位2枠に入るかが hash seed で反転する**。
#: ★完全一致（差 0.0）なら第2要素＝**名前**で決着するので **seed 非依存**。
#: ＝「1 ULP 違い」と「完全一致」で**再現性が正反対**になる。
UNREST_GOAL = 3


def person_top2_knife_edge(ai) -> dict:
    """`decide` 後の AI から「上位2枠の切り口が 1 ULP タイか」を読む（★読むだけ）。

    返り値＝`{"cut": (p_in, name_in), "next": (p_out, name_out), "ulp": bool, "gap": float}`。
    候補が2人以下なら `ulp=False`（切り口が無い＝安全）。
    """
    import math
    marg = ai._belief.role_marginals()
    cand = sorted(((d.get("パーソン", 0.0), n) for n, d in marg.items()
                   if d.get("パーソン", 0.0) > 0.1), reverse=True)
    if len(cand) <= 2:
        return {"cut": cand[-1] if cand else None, "next": None, "ulp": False, "gap": None}
    p_in, n_in = cand[1]
    p_out, n_out = cand[2]
    gap = p_in - p_out
    # ★「名前のタイブレークに落ちない」＝差が 0 でない、かつ数 ULP 以内
    ulp = gap != 0.0 and gap <= 4 * math.ulp(max(abs(p_in), abs(p_out), 1e-300))
    return {"cut": (p_in, n_in), "next": (p_out, n_out), "ulp": bool(ulp), "gap": gap}


def cmd_ulp(args) -> int:
    """★席ごとに「ウイルス試験対象の上位2枠の切り口」が 1 ULP タイかを数える。

    ★これが真の席は **`PYTHONHASHSEED` を変えると手が変わりうる**＝
      その席の bit 一致は「収録時の hash seed が同じだった」ことに依存する。
    ★読むのは **その席の `self._belief` の実物**（`_ProbeB266` が decide 後に控えた値）。
    """
    import math
    path = Path(args.log)
    _meta, ref = load_log(path)
    _state, _m, ai, live, _mm, _err = replay266(path, top=1)
    print(f"PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')}（★この値で走った結果です）")
    print("席ごとの『ウイルス試験対象 上位2枠の切り口』"
          "（`agents/heuristic_protagonist.py:2210` の sorted(cand, reverse=True)[:2]）")
    n_ulp = n_exact = 0
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        cand = (r.get("b266") or {}).get("person_cand")
        if not isinstance(cand, list) or len(cand) <= 2:
            continue
        gap = cand[1][0] - cand[2][0]
        ulp = gap != 0.0 and abs(gap) <= 4 * math.ulp(max(cand[1][0], cand[2][0], 1e-300))
        if ulp:
            n_ulp += 1
            tag = "  ★1 ULP タイ＝hash seed で反転しうる"
        elif gap == 0.0:
            n_exact += 1
            tag = "  （完全一致＝名前で決着＝seed 非依存）"
        else:
            tag = ""
        if ulp or args.all:
            print(f'L{r["loop"]}D{r["day"]} {r["seat"]:3s} '
                  f'2位={cand[1][1]}({cand[1][0]!r}) 3位={cand[2][1]}({cand[2][0]!r}) '
                  f'差={gap:+.3e}{tag}')
    print(f"\n★1 ULP タイの席＝{n_ulp} 件（hash seed 依存）／完全一致の席＝{n_exact} 件（seed 非依存）")
    return 0


def cmd_hashseed(args) -> int:
    """★**教材の bit 一致を hash seed の掃引で担保する**（B-266 の是正）。

    `PYTHONHASHSEED` はプロセス起動時にしか効かないので、**自分自身を子プロセスで**
    seed ごとに走らせて `verify` の結果を集める。
    ★合格条件＝**どれか1つの seed で棋譜と完全一致する**こと
      （収録時の hash seed は棋譜に記録されていない＝`tool_build` だけでは再生できない）。
    """
    import subprocess
    import sys as _sys
    seeds = [int(x) for x in args.seeds.split(",")] if "," in args.seeds else \
        list(range(int(args.seeds.split("-")[0]), int(args.seeds.split("-")[1]) + 1))
    env0 = dict(os.environ)
    env0["PYTHONIOENCODING"] = "utf-8"
    hit = []
    print(f"棋譜: {Path(args.log).name}  ★収録時の PYTHONHASHSEED は棋譜に記録されていない")
    for sd in seeds:
        env = dict(env0)
        env["PYTHONHASHSEED"] = str(sd)
        r = subprocess.run([_sys.executable, "-m", "arena.b266_audit",
                            "--log", args.log, "verify"],
                           capture_output=True, text=True, env=env)
        ok = r.returncode == 0
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith(("OK", "NG "))), "?")
        first = next((ln for ln in r.stdout.splitlines() if "NG: #" in ln), "")
        first = first.split("／")[0].strip() if first else ""
        print(f"  seed={sd:>3} RC={r.returncode} {line[:40]:40s} {first[:70]}")
        if ok:
            hit.append(sd)
    if hit:
        print(f"\n★OK: seed {hit} で棋譜と**完全一致**＝再生は可能。"
              f"収録時の hash seed が {hit} のいずれかだった（＝アプリは PYTHONHASHSEED を固定していない）。")
        return 0
    print("\nNG: どの seed でも一致しない＝hash seed では説明が付かない（別の原因）")
    return 1


def cmd_pin(args) -> int:
    """★席間協調の玉突きの検証＝**1席だけ棋譜の手に固定**して残りを再生する。

    `--at L6D2 --seat p1` で、その席だけ棋譜どおりに打たせ、後続席が一致するかを見る。
    ★AI には触れない（`decide` の**戻り値を差し替える**ラッパ1枚）。

    ★**限界（正直に）**＝差し替えは `decide` の**後**なので、AI の席内部状態
    （`_kinshi_used` / `_b100_placed` など）は**自分が選んだ手**で更新されたままになる。
    ∴ 「後続席が一致した」は強い証拠だが、「一致しなかった」は
    **玉突きでない証拠としてはやや弱い**（この状態のずれでも動きうる）。
    ★本件の決着は `hashseed` 掃引の方（seed 1 で 122/122）で取っている。
    """
    import os
    path = Path(args.log)
    meta, ref = load_log(path)
    want = (args.at.upper(), args.seat)
    from dataclasses import replace as _replace
    from sim.state import GameState as _GS, script_from_dict as _sfd
    pinned = {}
    for d in ref:
        if d["actor"] == "mastermind" or d["decision"] != "set_card":
            continue
        if (f'L{d["loop"]}D{d["day"]}', d["actor"]) == want:
            pinned[(d["loop"], d["day"], d["actor"])] = _strip(d["chosen"])
    if not pinned:
        print(f"NG: {want} の席が棋譜に無い")
        return 1

    class _Pin:
        def __init__(self, inner):
            self.inner = inner
            self.records = inner.records

        def decide(self, view, decision, options):
            key = (view.get("loop"), view.get("day"), view.get("seat"))
            got = self.inner.decide(view, decision, options)
            if decision == "set_card" and key in pinned:
                for o in options:
                    if _strip(o) == pinned[key]:
                        return o
            return got

    script = _replace(_sfd(meta["script"]), loops=int(meta["loops_played"]))
    mm = _MMReplayTolerant([d["chosen"] for d in ref if d["actor"] == "mastermind"])
    ai = _Pin(_ProbeB266(0, top=1))
    state = _GS(script=script)
    live: list = []
    decide = flow._make_decider(state, {"mastermind": mm, "p1": ai, "p2": ai, "p3": ai}, live)
    try:
        flow.run_loop(state, decide, final_battle=False)
    except Exception as exc:                       # noqa: BLE001
        print("再生中の例外:", exc)
    bad = first_mismatch(live, ref)
    print(f"PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')}  固定した席={want}")
    only = [b for b in bad if b.startswith("#")]
    for b in only[:10]:
        print("  NG:", b)
    print(f"食い違い {len(only)} 件 / 最終状態一致={state.to_dict() == meta['final_state']}")
    return 0 if not only else 1


def cmd_internals(args) -> int:
    """★席ごとに「退避の点」と、その点を決めている内部推定を並べる。"""
    path = Path(args.log)
    _meta, ref = load_log(path)
    _state, _m, ai, live, _mm, err = replay266(path, top=args.top)
    attach_views(ai, live)
    bad = first_mismatch(live, ref)
    if bad:
        print(f"★注意: 再生が割れている（{len(bad)}件）。最初= {bad[0]}")
    who, culprit = args.who, args.culprit
    print("席ごとの『退避の点』と、その点を決めている内部推定")
    print("  ★退避の分岐＝`agents/heuristic_protagonist.py:8081-8104`")
    print("     (a2) 確定犯人が守る対象と同室 → 115.0 ／ 候補どまり かつ 致死日 → 100.0")
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        sc = {(_fmt(o)): v for v, o in r["scored"]}
        b = r.get("b266") or {}
        kc = b.get("_known_culprits") or {}
        cc = b.get("_culprit_cands") or {}
        day = r["day"]
        print(f'L{r["loop"]}D{day} {r["seat"]:3s} '
              f'移動↑↓→{who}={sc.get(f"移動↑↓→{who}")} 暗躍禁止→{who}={sc.get(f"暗躍禁止→{who}")} '
              f'| kp={b.get("_keyperson")} kp_guard={b.get("_kp_guard")} '
              f'確定犯人={kc} 候補={ {k: v for k, v in cc.items()} } '
              f'致死日={b.get("_lethal_days")}')
    return 0


def b262_supply_likely(view: dict, who: str) -> bool:
    """B-262 の述語（`origin/lane/b262` の `_b262_mm_supply_likely`）を**公開情報から再現**する。

    ★本体は main に未 land（既定 OFF の切替口も無い）ので、道具側で**定義どおり**に組む：
      (1) その対象へ `不安+1` を置いた**公開実績**（`cards_revealed`）
      ∧ (2) **今日その対象に脚本家の伏せ札**（`view["placements"]` の owner=mastermind）。
    ★B-262 が land したら本関数は捨てて実装を import すること（二重実装の解消）。
    """
    habit = False
    for e in view.get("history", []):
        if e.get("event") != "cards_revealed":
            continue
        for pl in e.get("placements", []):
            if (pl.get("owner") == "mastermind" and pl.get("card") == "不安+1"
                    and pl.get("target_kind") == "character" and pl.get("target") == who):
                habit = True
    fd = any(pl.get("owner") == "mastermind" and pl.get("target_kind") == "character"
             and pl.get("target") == who for pl in view.get("placements", ()) or ())
    return habit and fd


def misleader_supply_certain(view: dict, who: str) -> tuple[bool, dict]:
    """★脚本家能力フェイズの `+1` が `who` に**確実に届く**か（公開情報のみ）。

    材料＝`agents/belief.py` の役職周辺確率（ミスリーダー）と、公開の
    `mastermind_ability / unrest` イベント（`present` 付き＝同エリア条件が公開される）。
    ミスリーダーは **自身を対象に取れる**（`rules/50_basic_tragedy_x.md:141` の★）ので、
    「ミスリーダーの台が {`who` 自身} ∪ {`who` と同室の者} に収まっている」なら
    **誰がミスリーダーでも `who` に +1 を置ける**＝供給は確率1で可能。
    """
    from arena.b251_audit import belief_after
    b = belief_after(view, [])
    marg = {n: m.get("ミスリーダー", 0.0) for n, m in b.role_marginals().items()}
    support = {n for n, p in marg.items() if p > 1e-9}
    chars = {c["name"]: c for c in view["characters"]}
    me = chars.get(who) or {}
    ok_set = {n for n in support
              if n == who or ((chars.get(n) or {}).get("alive")
                              and (chars.get(n) or {}).get("area") == me.get("area"))}
    return (bool(support) and support == ok_set,
            {n: round(marg[n], 3) for n in sorted(support)})


def cmd_bounds(args) -> int:
    """★B-256／B-262 の到達可能**上限**を犯人側に当て、必要な**下限**と並べて比べる。

    - `U_p = u + R` / `U_pm = u + 2R` は `arena/b256_audit.bounds`（実装と同じ式の単一ソース）。
    - `U_b` は B-262 の述語で `U_p`／`U_pm` を切り替えた版。
    - ★`L`（下限）＝**その日、主人公が最大限冷やしても事件フェイズに残る不安**
      ＝ `u + (脚本家の供給の下限) − (主人公の冷却の上限)`。
      供給の下限は**公開情報から立つ分だけ**を数える
      （`不安+1` 札＝B-262 の述語／脚本家能力＝ミスリーダーの台が同室に収まっているか）。
    """
    path = Path(args.log)
    meta, ref = load_log(path)
    who, crit = args.who, args.critical
    print(f"犯人={who} 臨界={crit}（`rules/30_characters.md:30`）")
    print("U_p=u+R（主人公のみ・B-256）／U_pm=u+2R／U_b=B-262 の述語で切替")
    print("L = u + 供給の下限 − 冷却の上限 ＝★**上限の双対**（実装は存在しない）")
    seen: set = set()
    for d in ref:
        if d["decision"] != "set_card" or d["actor"] == "mastermind":
            continue
        key = (d["loop"], d["day"])
        if key in seen:
            continue
        seen.add(key)
        v = d["view"]
        chars = {c["name"]: c for c in v["characters"]}
        me = chars.get(who) or {}
        if not me.get("alive"):
            continue
        u = int(me.get("unrest", 0))
        R, U_p, U_pm = b256_bounds(u, v.get("day"), v.get("days_per_loop"))
        likely = b262_supply_likely(v, who)
        U_b = u + R * (2 if likely else 1)
        ml_ok, ml = misleader_supply_certain(v, who)
        cool = _max_cooling(v, chars, who, meta, {"cards": [], "steps": []})
        sup = (1 if likely else 0) + (1 if ml_ok else 0)
        L = u + sup - cool["total"]
        print(f'L{d["loop"]}D{d["day"]} u={u} R={R} | 上限 U_p={U_p} U_pm={U_pm} '
              f'U_b={U_b}(札の述語={"真" if likely else "偽"}) '
              f'≥臨界? U_p:{"○" if U_p >= crit else "×"} U_b:{"○" if U_b >= crit else "×"}')
        print(f'        下限 L={L} = u{u} + 供給下限{sup}'
              f'(札{1 if likely else 0}+能力{1 if ml_ok else 0}) − 冷却上限{cool["total"]}'
              f'  {"★冷却では臨界を割れない" if L >= crit else "冷やせば臨界未満にできる"}'
              f'  | ミスリーダーの台={ml} 同室に収まる={ml_ok} | 冷却内訳={cool["why"]}')
    print("\n★読み方＝上限 U はこの局では**ほぼ常に臨界以上**（＝『届きうる』しか言えない）。")
    print("  『冷却では間に合わない』を言うには**下限 L** が要る＝B-256/B-262 の道具は"
          "そのままでは判定できない（双対が未実装）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B-266 検死（計測のみ）")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--era-pin", dest="era_pin", action="store_true", default=True)
    ap.add_argument("--no-era-pin", dest="era_pin", action="store_false")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify")
    sub.add_parser("seats")

    pa = sub.add_parser("arith")
    pa.add_argument("--who", default=CULPRIT)
    pa.add_argument("--critical", type=int, default=2)

    pr = sub.add_parser("rank")
    pr.add_argument("--card", default=None)
    pr.add_argument("--target", default=None)
    pr.add_argument("--top", type=int, default=999)

    pe = sub.add_parser("evade")
    pe.add_argument("--who", default=PROTECTEE)
    pe.add_argument("--culprit", default=CULPRIT)
    pe.add_argument("--top", type=int, default=999)

    pi = sub.add_parser("internals")
    pi.add_argument("--who", default=PROTECTEE)
    pi.add_argument("--culprit", default=CULPRIT)
    pi.add_argument("--top", type=int, default=999)

    ph = sub.add_parser("hashseed")
    ph.add_argument("--seeds", default="0-11",
                    help="掃引する PYTHONHASHSEED（例 0-11 / 0,1,2）")

    pu = sub.add_parser("ulp")
    pu.add_argument("--all", action="store_true", help="タイでない席も出す")

    pp = sub.add_parser("pin")
    pp.add_argument("--at", default="L6D2")
    pp.add_argument("--seat", default="p1")

    pb = sub.add_parser("bounds")
    pb.add_argument("--who", default=CULPRIT)
    pb.add_argument("--critical", type=int, default=2)

    args = ap.parse_args(argv)
    cmds = {"verify": cmd_verify, "seats": cmd_seats, "arith": cmd_arith,
            "rank": cmd_rank, "evade": cmd_evade, "bounds": cmd_bounds,
            "internals": cmd_internals, "hashseed": cmd_hashseed,
            "ulp": cmd_ulp, "pin": cmd_pin}
    with era_pin(args.era_pin, pins=ERA_PINS) as pinned:
        era_banner(bool(pinned), pins=ERA_PINS, consts=AUDITED_CONSTS, tag="b266")
        print(knob_baseline_banner())
        return cmds[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
