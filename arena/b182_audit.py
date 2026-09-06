# -*- coding: utf-8 -*-
"""B-182 Phase 1：**因果の糸ケアの射程計測**（★計測のみ・`agents/` `engine/` `rules/` `sim/` 非接触）。

起票＝`docs/バックログ_構想メモ_FableA.md` §59（ユーザー実戦 2026-08-05 `btx5_seal`
「因果の糸をケアするコードが必要」）。★射程訂正＝同 §60-2：
**B-174 退行4局の回収装置ではない**（flip 局の全席で P(因果の糸)=0.0）。本チケットの射程は
**ベンチ flip の外＝行為指標**（B-179 Phase 1 の「因果の糸＝有害」13席/19席の側）。
★**ベンチが動かなくても負の結果と誤読しない**。

KB＝`rules/50_basic_tragedy_x.md:85`＝因果の糸：**各ループの開始時、ひとつ前のループ終了時に
友好カウンターが置かれていたキャラクター全員に不安カウンターを2つ置く**。

------------------------------------------------------------------------------
## 0. 用語（★略語を使う前に、変数が何を指すかを定義する）
------------------------------------------------------------------------------

| 語 | 何を指すか（現物の場所） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回。1日3席 |
| **友好席** | その席で選ばれた手が `友好+1`/`友好+2` で対象がキャラのもの |
| **P(糸)** | 主人公 belief の `rule_marginals()` から因果の糸を含む組の周辺確率＝`heuristic_protagonist.py:1135` の `_ito_p`（唯一の取得口。源泉＝`belief._ito_signal(:786)`＝【強制】効果の観測/不発） |
| **高確率** | `P(糸) >= 0.7`（既存 b66 ゲートの閾値 `:4688` と同一）／**確定**＝`>= 0.999` |
| **終盤** | `day >= days_per_loop - 1`（最終日・最終々日） |
| **非最終ループ** | `loops_total - loop >= 1`（次ループが存在＝+2 が実際に来る側） |
| **+2 受領者** | 公開履歴の `{"phase":"loop_start","event":"unrest","delta":2}`（`sim/state.py:530-537`＝因果の糸の**専用**発行。学者特性は `scholar_trait`・別名＝混同しない）の (ループM, キャラ) |
| **b66 ゲート** | `HeuristicProtagonist._b66_ito_selfharm_invest`（`:4667`）＝糸下の犯人候補への友好投資を 2.0 へ格下げする既存ケア |
| **素通し席** | 高確率×非最終ループの友好席のうち b66 ゲートが False で、対象が実際に次ループ頭 +2 を受領したもの |
| **有害** | 素通し席のうち、+2 が次ループで H1/H2/H3（§2）のどれかを起こしたもの＝**Phase 2 の的** |

★**主人公AIの判断材料に「正解の配役」は一切使わない**（運用doc §3-7）。使うのは
`protagonist_view`・公開履歴・公開カウンターだけ。**監査側の後知恵**として2つだけ
真相を読む（出力で明示）：(a) 局数分母＝脚本定義の `rule_xs`（`census` 用途＝
`arena/corpus_census.py` と同じ）、(b) 発生済み事件の犯人名（`secret_log`）＝
「+2 が事件発生に枢要だったか」の帰結判定。どちらも AI には渡らない。

------------------------------------------------------------------------------
## 1. 数えるもの（チケットの (i)〜(iv)・★数える前に固定）
------------------------------------------------------------------------------

- **(i)** 因果の糸が rule に入っている局数（独立脚本数を分母に併記）＋belief 側の到達率
  （高確率/確定に達した局数＝検出の被覆）。
- **(ii)** 高確率/確定 × 非最終ループ × **終盤**の友好席：キャラ別・対象の種別
  （公開フレンド＝ループ開始の自動友好+1で+2が投資ゼロでも残る側 ／ 犯人候補 ／ その他）・
  最終日/最終々日・その友好が実際に**次ループ頭 +2** になったか。
- **(iii)** +2 受領者（ループM, キャラ）ごとの帰結（§2 の H1/H2/H3）と源泉
  （主人公投資あり／公開フレンド自動+1のみ／その他）。
- **(iv)** b66 ゲートの発火席数（実採点中に True を返した席×対象）と、**素通しした有害席**の数
  ＝Phase 2 の的の規模。不発火の**初手条件**（`:4688-4696` の判定順で最初に落ちた条件）別に分解。

------------------------------------------------------------------------------
## 2. +2 の帰結（H1/H2/H3）の操作化
------------------------------------------------------------------------------

| 帰結 | 定義（現物） |
|---|---|
| **H1 臨界到達@ループ頭** | snapshot（ループM, day1,「脚本家行動フェイズ前」）で受領者の不安 >= 不安臨界（`engine/data.unrest_threshold_of`） |
| **H1b 臨界まで1** | 同上で `臨界 - 不安 == 1`（+1 で事件圏＝mm に頭金を渡した状態） |
| **H2 事件発生に枢要** | ループMで発生（occurs=True）した事件の犯人（★後知恵＝secret_log）が受領者で、発生直前（同日「主人公能力フェイズ後」snapshot）の不安から 2 を引くと臨界未満＝**+2 が無ければ発生しなかった**（他の不安操作が同一という近似つき＝出力に明記） |
| **H2b 事件発生・非枢要** | 犯人＝受領者で発生したが、-2 しても臨界以上（どのみち発生） |
| **H3 冷却の後手** | ループMの **day<=2** に主人公席が受領者へ `不安-1` を置いた（+2 の尻拭いに序盤の席を使った） |

------------------------------------------------------------------------------
## 3. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・単独実行）
------------------------------------------------------------------------------

    python -m arena.b182_audit verify --days 3   # 挙動不変の物証（プローブ有無で棋譜一致）
    python -m arena.b182_audit count  --days 3   # (i)〜(iv) の数え上げ
    python -m arena.b182_audit rows   --days 3 --json out.json   # 生データ（席・受領者・素通し）
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import replace

from agents import HeuristicMastermind
from arena.b174_audit import _loop_results, _play_plain, apply_cfg, switches
from arena.b177_audit import _GW, _Rich
from engine.data import unrest_threshold_of
from sim import run_game

_HIGH = 0.7     # b66 ゲート（heuristic_protagonist.py:4688）と同一の閾値
_SURE = 0.999   # 「確定」（【強制】効果の観測で belief が 1.0 に至る）


# ---------------------------------------------------------------------------
# プローブ（★`_Rich` を継承し記録を足すだけ＝`super().decide()` の戻り値をそのまま返す。
#   shadow=False 固定＝b174 の影の再計算（能力アブレーション）は本監査では使わない）
# ---------------------------------------------------------------------------
class _Ito(_Rich):
    """友好席に P(糸)・b66 ゲート・対象種別の注釈を足す（挙動不変＝`verify` で実証）。"""

    def __init__(self, seed: int = 0, shadow: bool = False):
        super().__init__(seed, shadow=shadow)
        #: (loop, day, seat, tgt) → 実採点中に b66 ゲートが True を返したか
        self.gate_hits: set = set()
        self.gate_evals: set = set()
        self._b182_annotating = False

    # -- b66 ゲートの発火の計数（★返り値は1ビットも変えない） -------------------
    def _b66_ito_selfharm_invest(self, view: dict, tgt: str) -> bool:
        r = super()._b66_ito_selfharm_invest(view, tgt)
        if not self._b182_annotating:
            k = (view.get("loop"), view.get("day"), view.get("seat"), tgt)
            self.gate_evals.add(k)
            if r:
                self.gate_hits.add(k)
        return r

    # -- b66 が不発火だった時、判定順（:4688-4696）で最初に落ちた条件 -------------
    #   ★式は書き写さない＝現物と同じ属性を同じ順序で読むだけ（行番号を根拠に併記）
    def _b66_first_fail(self, view: dict, tgt: str) -> str | None:
        if getattr(self, "_ito_p", 0.0) < 0.7:
            return "P(糸)<0.7 (:4688)"
        if "loops_total" not in view or view["loops_total"] - view["loop"] < 1:
            return "最終ループ (:4690)"
        if len(getattr(self, "_b66_unproven_boards", ())) < 4:
            return "板線の無実証<4 (:4692)"
        if tgt not in getattr(self, "_culprits", set()):
            return "犯人候補でない (:4694)"
        if not unrest_threshold_of(tgt):
            return "臨界0/なし (:4696)"
        return None

    # -- 現行の G8/G9（B-86'/B-109）判定＝既存ケアとの重なりを見る -----------------
    def _noop_now(self, view: dict, card: str, tgt: str) -> str | None:
        from agents.card_effect import NoopCtx, noop_reason
        ctx = NoopCtx(
            gw_keep=frozenset(getattr(self, "_b86_gw_keep", ()) or ()),
            gw_refused=frozenset(getattr(self, "_b86_gw_refused", ()) or ()),
            gw_ignore_certain=frozenset(
                getattr(self, "_b86_gw_ignore_certain", ()) or ()),
            gw_info_exhausted=frozenset(
                getattr(self, "_b86_gw_info_exhausted", ()) or ()),
            gw_arms_mm=frozenset(getattr(self, "_b86_gw_arms_mm", ()) or ()),
            gw_final_void=frozenset(getattr(self, "_b109_gw_final_void", ()) or ()),
            gw_final_harm=frozenset(getattr(self, "_b109_gw_final_harm", ()) or ()))
        n = noop_reason(view, card, tgt, "character", ctx)
        return None if n is None else n.reason

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        n0 = len(self.stream)
        chosen = super().decide(view, decision, options)
        if decision != "set_card" or len(self.stream) <= n0:
            return chosen
        rec = self.stream[-1]
        if "card" not in rec:
            return chosen
        p = float(getattr(self, "_ito_p", 0.0) or 0.0)
        dpl = int(view.get("days_per_loop", 99))
        lt = view.get("loops_total")
        rec["P(糸)"] = round(p, 4)
        rec["最終ループか"] = bool(lt is not None
                                   and int(lt) - int(view.get("loop", 1)) < 1)
        rec["終盤か"] = int(view.get("day", 1)) >= dpl - 1
        rec["最終日か"] = int(view.get("day", 1)) >= dpl
        if rec.get("card") in _GW and rec.get("kind") == "character":
            tgt = rec["target"]
            c = next((x for x in view.get("characters") or []
                      if x.get("name") == tgt), None)
            step = 2 if rec["card"] == "友好+2" else 1
            rec["step"] = step
            rec["友好(置く前)"] = int((c or {}).get("goodwill", 0) or 0)
            rec["対象は犯人候補"] = tgt in getattr(self, "_culprits", set())
            rec["対象の不安臨界"] = unrest_threshold_of(tgt)
            # 公開フレンド＝役職公開済み（公開履歴の role_reveal）＝ループ開始の自動友好+1
            #   （`sim/state.py:522-526`）で、投資ゼロでも次ループ頭 +2 が残る側
            rec["対象は公開フレンド"] = tgt in {
                e.get("name") for e in view.get("history") or []
                if e.get("event") == "role_reveal" and e.get("role") == "フレンド"}
            self._b182_annotating = True
            try:
                rec["b66ゲート"] = bool(self._b66_ito_selfharm_invest(view, tgt))
            finally:
                self._b182_annotating = False
            rec["b66不発火の初手条件"] = (None if rec["b66ゲート"]
                                          else self._b66_first_fail(view, tgt))
            rec["現行G8G9"] = self._noop_now(view, rec["card"], tgt)
        return chosen


# ---------------------------------------------------------------------------
# 1局の実行と帰結の抽出
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int):
    hp = _Ito(seed)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return hp, st, trace


def _snap_unrest(st) -> dict:
    """(loop, day, point) → {キャラ: 不安}（phase_snapshots＝卓上の公開カウンター）。"""
    return {(s["loop"], s["day"], s["point"]):
            {n: int(c.get("unrest", 0) or 0)
             for n, c in (s.get("characters") or {}).items()}
            for s in st.phase_snapshots}


def _script_sig(sc):
    from arena.corpus_census import script_signature
    return script_signature(sc)


def _analyze_game(name: str, seed: int, sc, loops: int) -> dict:
    """1局を回し、席行・+2受領者行・素通し行を返す。"""
    hp, st, _ = _play(sc, seed, loops)
    game = f"{name}#{seed}"
    sig = str(hash(_script_sig(sc)))
    dpl = int(sc.days_per_loop)

    # --- 公開履歴から ------------------------------------------------------
    ito_recv = [(e.get("loop"), e.get("target")) for e in st.history
                if e.get("phase") == "loop_start" and e.get("event") == "unrest"
                and e.get("delta") == 2]
    friend_reveal_loop: dict = {}
    for e in st.history:
        if e.get("event") == "role_reveal" and e.get("role") == "フレンド":
            friend_reveal_loop.setdefault(e.get("name"), e.get("loop"))
    loop_end_gw: dict = {}
    for e in st.history:
        if e.get("event") == "loop_board":
            loop_end_gw[e.get("loop")] = dict(e.get("char_goodwill") or {})
    # --- ★後知恵（AI には渡らない）：発生済み事件の犯人（secret_log） ----------
    occurred = [(e.get("loop"), e.get("day"), e.get("name"), e.get("culprit"))
                for e in st.secret_log
                if e.get("event") == "incident" and e.get("occurs")]
    snap = _snap_unrest(st)
    results = _loop_results(st)

    # --- 席行 --------------------------------------------------------------
    seats = []
    p_by_loop_lastday: dict = {}          # ループ → 終盤で観測した P(糸) の最大
    invest_by_loop: dict = defaultdict(list)   # ループ → [(day, tgt, card)]
    cool_by_loop: dict = defaultdict(list)     # ループ → [(day, tgt)]（不安-1）
    for r in hp.stream:
        if "card" not in r:
            continue
        L, D = r.get("loop"), r.get("day")
        if r.get("card") in _GW and r.get("kind") == "character":
            invest_by_loop[L].append((D, r.get("target"), r.get("card")))
        if r.get("card") == "不安-1" and r.get("kind") == "character":
            cool_by_loop[L].append((D, r.get("target")))
        if r.get("終盤か"):
            p_by_loop_lastday[L] = max(p_by_loop_lastday.get(L, 0.0),
                                       float(r.get("P(糸)", 0.0)))
        row = {"game": game, "family": name, "sig": sig, **r}
        seats.append(row)

    # --- +2 受領者行 -------------------------------------------------------
    recips = []
    for M, who in ito_recv:
        L = M - 1
        th = unrest_threshold_of(who)
        head = snap.get((M, 1, "脚本家行動フェイズ前"), {}).get(who)
        h1 = bool(th is not None and head is not None and head >= th)
        h1b = bool(th is not None and head is not None and th - head == 1)
        h2 = h2b = False
        h2_days = []
        for (iL, iD, iname, iculp) in occurred:
            if iL != M or iculp != who or th is None:
                continue
            pre = snap.get((iL, iD, "主人公能力フェイズ後"), {}).get(who)
            if pre is None:
                continue
            if pre - 2 < th:
                h2 = True
            else:
                h2b = True
            h2_days.append((iD, iname))
        h3_seats = [(d, t) for (d, t) in cool_by_loop.get(M, []) if t == who and d <= 2]
        inv_prev = [(d, c) for (d, t, c) in invest_by_loop.get(L, []) if t == who]
        was_friend = who in friend_reveal_loop and friend_reveal_loop[who] < M
        src = ("主人公投資あり" if inv_prev
               else "公開フレンド自動+1のみ" if was_friend else "その他源泉")
        recips.append({
            "game": game, "family": name, "sig": sig,
            "受領ループM": M, "キャラ": who, "不安臨界": th,
            "ループ頭の不安": head,
            "H1_臨界到達@頭": h1, "H1b_臨界まで1": h1b,
            "H2_事件発生に枢要(★後知恵)": h2, "H2b_発生・非枢要(★後知恵)": h2b,
            "H2の事件(日,名)": h2_days,
            "H3_冷却の後手席数(day<=2)": len(h3_seats), "H3の席": h3_seats,
            "有害(H1orH2orH3)": bool(h1 or h2 or len(h3_seats) > 0),
            "源泉": src, "前ループの投資(日,札)": inv_prev,
            "前ループ終了時の友好": loop_end_gw.get(L, {}).get(who),
            "P(糸)@前ループ終盤": round(p_by_loop_lastday.get(L, 0.0), 4),
            "ループMの結果": results.get(M),
        })
    recip_key = {(r["受領ループM"], r["キャラ"]): r for r in recips}

    # --- 素通し行（高確率×非最終ループの友好席 × ゲート False × 実際に+2） -------
    passthru = []
    for r in seats:
        if r.get("card") not in _GW or r.get("kind") != "character":
            continue
        if float(r.get("P(糸)", 0.0)) < _HIGH or r.get("最終ループか"):
            continue
        rec2 = recip_key.get((r["loop"] + 1, r["target"]))
        row = {**r, "次ループ+2を受領": rec2 is not None,
               "+2の帰結": (None if rec2 is None else {
                   k: rec2[k] for k in
                   ("H1_臨界到達@頭", "H1b_臨界まで1", "H2_事件発生に枢要(★後知恵)",
                    "H3_冷却の後手席数(day<=2)", "有害(H1orH2orH3)", "源泉")})}
        passthru.append(row)

    return {
        "game": game, "family": name, "sig": sig, "dpl": dpl,
        "★後知恵:糸がruleに実在": "因果の糸" in (sc.rule_xs or ()),
        "P(糸)の最大観測": max((float(r.get("P(糸)", 0.0)) for r in seats),
                               default=0.0),
        "seats": seats, "recips": recips, "passthru": passthru,
        "gate_hits": sorted(hp.gate_hits), "gate_evals": len(hp.gate_evals),
    }


# ---------------------------------------------------------------------------
# verify＝挙動不変の物証（プローブ有無で棋譜が完全一致）
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    apply_cfg("off")
    print(f"[切替口] {switches()} / days={days}", flush=True)
    games = list(benchmark_scripts(days=days))[start:end]
    bad = []
    for name, seed, sc in games:
        ref = _play_plain(sc, seed, loops)
        _hp, _st, tr = _play(sc, seed, loops)
        if tr != ref:
            bad.append({"game": f"{name}#{seed}"})
    return {"days": days, "n_games": len(games),
            "mismatch": len(bad), "bad": bad[:20]}


# ---------------------------------------------------------------------------
# rows／count
# ---------------------------------------------------------------------------
def collect(days: int = 3, loops: int = 8, start: int = 0,
            end: int | None = None) -> list:
    from arena.benchmark import benchmark_scripts

    apply_cfg("off")
    print(f"[切替口] {switches()} / days={days}", flush=True)
    out = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        g = _analyze_game(name, seed, sc, loops)
        out.append(g)
        print(f"  {g['game']}: 席{len(g['seats'])} +2受領{len(g['recips'])} "
              f"素通し候補{len(g['passthru'])} ゲート発火{len(g['gate_hits'])}",
              flush=True)
    return out


def _tally(rows: list, key) -> dict:
    """席数／局数／独立脚本数（b179_audit と同じ出し方）。"""
    f = key if callable(key) else (lambda r: r.get(key))
    seats = Counter()
    games = defaultdict(set)
    sigs = defaultdict(set)
    for r in rows:
        k = f(r)
        seats[k] += 1
        games[k].add(r["game"])
        sigs[k].add(r["sig"])
    return {str(k): {"席": seats[k], "局": len(games[k]), "独立脚本": len(sigs[k])}
            for k in sorted(seats, key=lambda x: (-seats[x], str(x)))}


def count(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    games = collect(days, loops, start, end)
    n_games = len(games)
    n_sigs = len({g["sig"] for g in games})

    # ---- (i) 分母 ----------------------------------------------------------
    ito_games = [g for g in games if g["★後知恵:糸がruleに実在"]]
    high_games = [g for g in games if g["P(糸)の最大観測"] >= _HIGH]
    sure_games = [g for g in games if g["P(糸)の最大観測"] >= _SURE]
    out: dict = {
        "days": days, "局数": n_games, "独立脚本": n_sigs,
        "(i) 因果の糸の分母": {
            "rule に入っている局(★後知恵)": {
                "局": len(ito_games),
                "独立脚本": len({g["sig"] for g in ito_games})},
            "belief が高確率(>=0.7)に達した局": len(high_games),
            "belief が確定(>=0.999)に達した局": len(sure_games),
            "★rule に実在するのに高確率へ達しなかった局": sum(
                1 for g in ito_games if g["P(糸)の最大観測"] < _HIGH),
            "★rule に無いのに高確率へ達した局": sum(
                1 for g in high_games if not g["★後知恵:糸がruleに実在"]),
        },
    }

    seats = [r for g in games for r in g["seats"]]
    gw = [r for r in seats if r.get("card") in _GW and r.get("kind") == "character"]

    # ---- (ii) 高確率×非最終ループ×終盤の友好席 ------------------------------
    hot = [r for r in gw if float(r.get("P(糸)", 0.0)) >= _HIGH
           and not r.get("最終ループか")]
    hot_end = [r for r in hot if r.get("終盤か")]

    def _cls(r):
        return ("公開フレンド" if r.get("対象は公開フレンド")
                else "犯人候補" if r.get("対象は犯人候補") else "その他")
    out["(ii) 高確率×非最終ループの友好席"] = {
        "全日": {"席": len(hot), "局": len({r['game'] for r in hot}),
                 "独立脚本": len({r['sig'] for r in hot})},
        "終盤(最終日・最終々日)": {
            "席": len(hot_end), "局": len({r['game'] for r in hot_end}),
            "独立脚本": len({r['sig'] for r in hot_end}),
            "うち確定(P>=0.999)": sum(1 for r in hot_end
                                      if float(r.get("P(糸)", 0)) >= _SURE),
            "最終日/最終々日": _tally(hot_end, "最終日か"),
            "キャラ別": _tally(hot_end, "target"),
            "対象の種別": _tally(hot_end, _cls),
            "b66ゲート発火": _tally(hot_end, "b66ゲート"),
            "現行G8G9の判定": _tally(hot_end, "現行G8G9"),
            "族別": _tally(hot_end, "family"),
        },
    }

    # ---- (iii) +2 受領者の帰結 --------------------------------------------
    recips = [r for g in games for r in g["recips"]]
    harm = [r for r in recips if r["有害(H1orH2orH3)"]]
    out["(iii) 次ループ頭+2の帰結（受領者=ループM×キャラ単位）"] = {
        "+2受領の総数": len(recips), "局": len({r['game'] for r in recips}),
        "独立脚本": len({r['sig'] for r in recips}),
        "源泉別": _tally(recips, "源泉"),
        "H1 臨界到達@ループ頭": sum(1 for r in recips if r["H1_臨界到達@頭"]),
        "H1b 臨界まで1": sum(1 for r in recips if r["H1b_臨界まで1"]),
        "H2 事件発生に枢要(★後知恵・近似=他の不安操作固定)": sum(
            1 for r in recips if r["H2_事件発生に枢要(★後知恵)"]),
        "H2b 発生・非枢要": sum(1 for r in recips if r["H2b_発生・非枢要(★後知恵)"]),
        "H3 冷却の後手（day<=2 の不安-1 が1席以上）": sum(
            1 for r in recips if r["H3_冷却の後手席数(day<=2)"] > 0),
        "H3 に使われた序盤席の総数": sum(
            r["H3_冷却の後手席数(day<=2)"] for r in recips),
        "有害(H1orH2orH3)の受領者": {
            "数": len(harm), "局": len({r['game'] for r in harm}),
            "独立脚本": len({r['sig'] for r in harm}),
            "源泉別": _tally(harm, "源泉")},
        "★前ループ終盤で P(糸)>=0.7 だった受領者": sum(
            1 for r in recips if r["P(糸)@前ループ終盤"] >= _HIGH),
        "うち有害": sum(1 for r in harm if r["P(糸)@前ループ終盤"] >= _HIGH),
    }

    # ---- (iv) b66 ゲートの発火と素通し --------------------------------------
    fired = [k for g in games for k in g["gate_hits"]]
    fired_games = {g["game"] for g in games if g["gate_hits"]}
    passthru = [r for g in games for r in g["passthru"]]
    pt_recv = [r for r in passthru if r["次ループ+2を受領"]]
    pt_harm = [r for r in pt_recv if (r["+2の帰結"] or {}).get("有害(H1orH2orH3)")]
    chosen_fired = [r for r in gw if r.get("b66ゲート")]
    out["(iv) b66ゲート（既存ケア）"] = {
        "実採点中に True を返した(席×対象)の数": len(fired),
        "発火した局": len(fired_games),
        "★ゲートが True なのに友好+が選ばれた席": {
            "席": len(chosen_fired), "局": len({r['game'] for r in chosen_fired})},
        "素通し（高確率×非最終ループの友好席でゲートFalse）": {
            "席": len([r for r in passthru if not r.get("b66ゲート")]),
            "うち実際に次ループ+2を受領": len(
                [r for r in pt_recv if not r.get("b66ゲート")]),
            "★うち有害(H1orH2orH3)＝Phase 2 の的": {
                "席": len([r for r in pt_harm if not r.get("b66ゲート")]),
                "局": len({r['game'] for r in pt_harm if not r.get("b66ゲート")}),
                "独立脚本": len({r['sig'] for r in pt_harm
                                 if not r.get("b66ゲート")})},
            "不発火の初手条件": _tally(
                [r for r in passthru if not r.get("b66ゲート")],
                "b66不発火の初手条件"),
            "有害席の不発火の初手条件": _tally(
                [r for r in pt_harm if not r.get("b66ゲート")],
                "b66不発火の初手条件"),
            "有害席の終盤/非終盤": _tally(
                [r for r in pt_harm if not r.get("b66ゲート")], "終盤か"),
            "有害席の対象の種別": _tally(
                [r for r in pt_harm if not r.get("b66ゲート")], _cls),
        },
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="B-182 因果の糸ケアの射程計測")
    ap.add_argument("cmd", choices=["verify", "count", "rows"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    # ★Phase 2：B182_ITO_COOLDOWN の ON/OFF 両側を同じ計測器で数えるための切替
    #   （default＝クラス既定のまま。apply_cfg("off") は b174 の _KEYS しか張り直さない
    #   ＝ここで立てた値は計測中も生きる）。実効値は毎回印字する（規約 §4）。
    ap.add_argument("--b182", choices=["default", "on", "off"], default="default")
    a = ap.parse_args()
    from agents.heuristic_protagonist import HeuristicProtagonist as _HP
    if a.b182 != "default":
        _HP.B182_ITO_COOLDOWN = (a.b182 == "on")
    print(f"[B182_ITO_COOLDOWN] {_HP.B182_ITO_COOLDOWN}", flush=True)
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end)
    elif a.cmd == "rows":
        res = collect(a.days, a.loops, a.start, a.end)
    else:
        res = count(a.days, a.loops, a.start, a.end)
    txt = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    print(txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
