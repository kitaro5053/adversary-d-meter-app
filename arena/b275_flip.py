# -*- coding: utf-8 -*-
"""B-275 フェーズ0：**完全同点帯の解像度＝perm flip の帰属分布**（★測定のみ・実装しない）。

チケット＝`docs/提案_B275_同点帯の解像度_2026-08-20.md` (a) 案のフェーズ0。
出典＝§72-89（B-273ph0＝perm 分散の帰属は完全同点が100%・1 ULP は 0%）／
§72-78（perm 4条件）／B-105・B-107（同点帯は定数では補償できない）。
道具の土台＝`arena/b273_ulp.py`（Recorder の trace/moves・site は内容ベース同定）。

## ★発注前の検算（4検問・規約の要求）

1. **§72-48＝原理的に非ゼロか**：非ゼロ（実測済み＝perm を振ると勝敗が 3日級 41〜84局・
   5日級 29〜41局 動く。B-273ph0）。★本走で再確認してから使う（下の (0)）。
2. **§72-53＝強さに繋がるか**：本チケット自体は計測のみ＝強さを主張しない。
   フェーズ1（意味の軸）に繋がるかの判定材料を出す。★flip には両向きがある＝
   「id が列挙順の籤でたまたま勝っていた局」（ltw_id < ltw_perm）を**壊さない**ことが
   フェーズ1 の必要条件になる＝その局数と site を明示的に数える。
3. **§72-78＝較正条件固有でないか**：perm そのものを測定対象にするので自動的に満たす。
   脚本別の層別も出す。
4. **§72-88＝動機の局面を捕まえるか**：`btx_seal_cat`（3日級）の perm=id 固有の穴
   （平均 6.9 vs 他 perm 2.0〜3.0）が (a) の帰属で説明できるか＝**本丸**。

## ★事前登録（測る前に固定・この docstring が最初のコミットに含まれる）

### 測るもの（定義を測定後に動かさない）

- **(0) 前提の再確認**＝flip 局数（id vs rev/h1/h5・3日級と5日級）と、
  完全同点を含む決定席数（3日級 id＝2,787/9,693 の再現）。
- **(a) 分岐点の特定**＝flip 局（loops_to_win が動いた局）ごとに、
  **最初に打つ手が食い違った decide**（moves 列の先頭不一致＝以降は局面がずれて比較不能）を
  特定する。その decide 内の shim 呼び出し（float 第1キーの sorted/max/min）のうち、
  **decide の戻り値と同内容を返した最後の呼び出し＝「決着 call」**の site へ帰属させる。
  併せて decide 内で**最初に結果が食い違った呼び出し＝「起点 call」**も控える
  （起点＝列挙順が最初に効いた場所／決着＝手が実際に変わった場所。同一とは限らない）。
  決着 call の分類（base=id 側の cls）：
  - `exact`＝勝者と次点のキーが完全同点＝**列挙順決着**（B-275 の主役）。
  - `ulp`／`clear`／shim 外（計画の next() 等）＝別枠で数え、無理に exact に寄せない。
- **(b) 帰属分布**＝(a) を site 別・脚本別・向き別（perm改善＝ltw_perm<ltw_id／perm悪化）に
  分母つきで集計。★「分岐点の site」と「勝敗が動いた機序」は別物になりうるので、
  **反実仮想**で裏を取る：
  - `inject`＝全席 id のまま、分岐 decide **1席だけ** perm を適用 → ltw が perm 側へ動くか。
  - `revert`＝全席 perm のまま、分岐 decide 1席だけ id に戻す → ltw が id 側へ**戻る**か。
  分類＝「単一分岐で再現/復元」「動かず（下流の同点再抽選が本体）」「第3の結果」。
  実行不能な組は「未測」と明記する。
- **(c) 上位 site の中身**＝集中している site について、分岐 decide の同点グループ
  （列挙順が選んだ手／捨てた手＝語彙・対象・点数）を局面レベルで数例示す。
- **(d) 意味の軸の候補**＝(c) の実例に基づき 1〜3個（実装しない・報告本文）。

### 判定基準（事前登録＝2026-08-23・測定前に固定）

両コーパス合算（3日級 id vs rev/h1/h5 ＋ 5日級 同・flip 延べ局）で：

1. **帰属の健全性**＝決着 call が「exact（完全同点）」に帰属する flip が過半であること
   （B-273ph0 の「帰属100%完全同点」の追認。崩れたら前提から報告し直す）。
2. **★主判定**＝**flip 延べ局の過半（>50%）が上位3 site（決着 site 別）に帰属するなら**
   「意味の軸」設計（フェーズ1 提案）に進む価値あり。
   **上位3 site で過半に届かなければ負の結果**（→提案メモ (c) 探索案 or (d) 現状維持へ）。
3. **検問4**＝`btx_seal_cat`（3日級）の flip の帰属 site が特定でき、その同点の中身が
   perm=id 固有の穴（6.9 vs 2.0〜3.0）の機序を説明できること（説明できなければ
   主判定が○でも「動機の局面は未捕捉」と明記する）。

## ★挙動には触れない

`agents/` `sim/` `engine/` `rules/` は**1バイトも変更しない**。本モジュールは
b273_ulp の shim（元の組み込みをそのまま呼ぶ＝比較結果は 1 bit も変えない）と
tie_noise の perm ラッパ（B-105 の計測装置）を組み合わせるだけ。
反実仮想（inject/revert）は**測定専用の別走行**で、本番経路・既定には触れない。

CLI（測定は `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    # フェーズ0 一式（コーパス走行 → flip 帰属 → 反実仮想 → JSON 保存）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b275_flip phase0 \
        --days 3 --perms rev,h1,h5 --out /tmp/b275_d3.json

    # 保存済み JSON から報告を再表示（事前登録判定を含む）
    PYTHONIOENCODING=utf-8 python -m arena.b275_flip report /tmp/b275_d3.json /tmp/b275_d5.json

    # (c) 個別局の分岐 decide の同点グループを覗く
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b275_flip probe \
        --days 3 --game btx_seal_cat#4 --idx 123 --perm rev
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

from arena import b273_ulp as U
from arena import tie_noise

#: 本タスクの perm 対（§72-78 の表＝id 133／rev 137／h5 138／h1 139 に対応）。
PERMS = ("rev", "h1", "h5")


# ---------------------------------------------------------------------------
# decide 序数つき Recorder（trace/moves の席ラベルを decide 単位で一意にする）
# ---------------------------------------------------------------------------
class DecideIndexedRecorder(U.Recorder):
    """席ラベル末尾に `@decide序数`（＝その局の moves 内 index）を付ける。

    ★B-273 の席ラベル `L{loop}D{day}{seat}:{decision}` は**一意でない**
    （mm の set_card は同日3席ある）。B-276 の「結合キーの次元不足」（4例目）の教訓
    ＝**行を一意にする全次元をキーに入れる**。序数は `len(self._mv)`＝decide 開始時点の
    moves 長＝その decide の moves index そのもの（ctx 代入→shim 呼び出し→moves append の
    順序が b273_ulp の _install_ctx で固定されている）。
    """

    def seat_key(self) -> str:
        return f"{U.Recorder.seat_key(self)}@{len(self._mv)}"

    def begin_game(self, gid: str) -> None:
        # ★メモリ対策＝前局の trace の site/cls/seat 文字列を intern して共有する。
        prev = self.game
        if prev is not None:
            for e in self.trace.get(prev, ()):
                e[0] = sys.intern(e[0])
                e[3] = sys.intern(e[3])
                e[4] = sys.intern(e[4])
        U.Recorder.begin_game(self, gid)


def _seat_idx(seat: str) -> int:
    return int(seat.rsplit("@", 1)[1])


def run_corpus_indexed(days: int, perm: str, loops: int = 8,
                       limit: int | None = None, verbose: bool = True,
                       only: list[str] | None = None) -> dict:
    """b273_ulp.run_corpus を DecideIndexedRecorder で走らせる（挙動は無改変）。"""
    saved = U.REC
    U.REC = DecideIndexedRecorder()
    try:
        return U.run_corpus(days=days, perm=perm, loops=loops, limit=limit,
                            keep_trace=True, verbose=verbose, only=only)
    finally:
        U.REC = saved


# ---------------------------------------------------------------------------
# (a) flip 局の分岐点と帰属
# ---------------------------------------------------------------------------
def first_move_divergence(ma: list, mb: list) -> int | None:
    """最初に打つ手が食い違った decide の序数（無ければ None）。"""
    n = min(len(ma), len(mb))
    for i in range(n):
        if ma[i][1] != mb[i][1] or ma[i][0] != mb[i][0]:
            return i
    return n if len(ma) != len(mb) else None


def _slice(trace: list, idx: int) -> list:
    tag = str(idx)
    return [e for e in trace if e[4].rsplit("@", 1)[1] == tag]


def attribute_flip(a: dict, b: dict, gid: str) -> dict:
    """flip 局 gid の分岐点を特定し、決着 call／起点 call の site へ帰属させる。

    - 決着 call＝分岐 decide 内で **decide の戻り値と同内容**（canon digest 一致）を
      返した最後の shim 呼び出し。cls=exact なら「完全同点の列挙順決着」。
    - 起点 call＝分岐 decide 内で最初に結果が食い違った shim 呼び出し
      （★分岐 decide より前の decide にも結果違いの call はあるが、そこでは
      **手が変わっていない**＝消費されない差。分岐点で数える、が事前登録の定義）。
    """
    ma, mb = a["moves"][gid], b["moves"][gid]
    i = first_move_divergence(ma, mb)
    out = {"game": gid, "idx": i}
    if i is None:
        out["sel_site"], out["sel_cls"] = "(手の食い違い無し)", "none"
        return out
    if i >= min(len(ma), len(mb)):
        out["seat"] = "(手数違い)"
        out["sel_site"], out["sel_cls"] = "(手数違い)", "none"
        return out
    out["seat"] = ma[i][0]
    sa = _slice(a["trace"].get(gid, ()), i)
    sb = _slice(b["trace"].get(gid, ()), i)
    sel_a = next((e for e in reversed(sa) if e[2] == ma[i][1]), None)
    sel_b = next((e for e in reversed(sb) if e[2] == mb[i][1]), None)
    if sel_a is not None:
        out["sel_site"] = sel_a[0]
        out["sel_cls"] = sel_a[3]
        if sel_b is not None and sel_b[0] != sel_a[0]:
            out["sel_note"] = f"b側の決着callは別site: {sel_b[0]}"
    else:
        out["sel_site"] = "(shim外＝計画/next等)"
        out["sel_cls"] = "none"
    n = min(len(sa), len(sb))
    j = next((k for k in range(n)
              if sa[k][0] != sb[k][0] or sa[k][2] != sb[k][2]), None)
    if j is not None:
        out["origin_site"], out["origin_cls"] = sa[j][0], sa[j][3]
    elif len(sa) != len(sb):
        out["origin_site"], out["origin_cls"] = "(call数違い)", "none"
    else:
        out["origin_site"], out["origin_cls"] = "(slice内で結果差なし)", "none"
    return out


def flip_rows(a: dict, b: dict) -> list[dict]:
    """勝敗（loops_to_win）が動いた局の一覧＋帰属。"""
    ra = {r["game"]: r["loops_to_win"] for r in a["rows"]}
    rb = {r["game"]: r["loops_to_win"] for r in b["rows"]}
    rows = []
    for g in sorted(g for g in ra if g in rb and ra[g] != rb[g]):
        at = attribute_flip(a, b, g)
        at["ltw_id"], at["ltw_p"] = ra[g], rb[g]
        at["dir"] = "perm改善" if rb[g] < ra[g] else "perm悪化"
        at["script"] = g.rsplit("#", 1)[0]
        rows.append(at)
    return rows


def moves_diverged_count(a: dict, b: dict) -> int:
    n = 0
    for gid, ma in a["moves"].items():
        mb = b["moves"].get(gid)
        if mb is not None and first_move_divergence(ma, mb) is not None:
            n += 1
    return n


# ---------------------------------------------------------------------------
# (b) 反実仮想＝分岐 decide 1席だけを入れ替える別走行
# ---------------------------------------------------------------------------
def run_single_swap(days: int, gid: str, swap_idx: int | None, perm: str,
                    mode: str = "inject", loops: int = 8,
                    capture_idx: int | None = None,
                    pre_swaps: dict[int, str] | None = None) -> dict:
    """1局だけ走らせ、decide 序数 `swap_idx` の1席だけ並び順を入れ替える。

    - mode="inject"＝全席 id のまま、`swap_idx` **だけ** perm を適用。
    - mode="revert"＝全席 perm のまま、`swap_idx` だけ id に戻す。
    - mode="plain" ＝入れ替え無し（swap_idx 無視＝対照走行）。
    `pre_swaps`＝{decide序数: perm} を追加適用（連鎖の2段目以降の capture 用）。
    `capture_idx` を与えるとその decide 中の sorted/max/min の中身
    （キーと候補の要約）を `cap` に控える（(c) の材料）。
    ★挙動への影響は**この走行の中だけ**（decide は必ず原状復帰する）。
    """
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.benchmark import benchmark_scripts
    from sim import run_game
    import agents.belief as _bel

    name, seed_s = gid.rsplit("#", 1)
    seed = int(seed_s)
    sc = next((s for nm, sd, s in benchmark_scripts(days=days)
               if nm == name and sd == seed), None)
    if sc is None:
        raise KeyError(f"脚本が見つからない: {gid} (days={days})")

    _bel._RECOMPUTE_CACHE.clear()          # ★§72-78：条件が変わる走行の前に必ず落とす
    tie_noise.uninstall_perm()             # decide を原本に戻してから包む
    counter = {"i": -1}
    rec = {"swap_digest": None, "swap_seat": None, "n_decides": 0}
    cap: list = []
    saved = []
    for cls, is_prot in ((HeuristicProtagonist, True), (HeuristicMastermind, False)):
        orig = cls.decide
        saved.append((cls, orig))

        def mk(orig=orig, is_prot=is_prot):
            def decide(self, view, decision, options):
                counter["i"] += 1
                i = counter["i"]
                rec["n_decides"] = i + 1
                if (is_prot and decision in tie_noise._PERM_DECISIONS
                        and len(options) > 1):
                    if pre_swaps and i in pre_swaps:
                        options = tie_noise.permute(options, pre_swaps[i])
                    if mode == "inject":
                        if i == swap_idx:
                            options = tie_noise.permute(options, perm)
                    elif mode == "revert":
                        if i != swap_idx:
                            options = tie_noise.permute(options, perm)
                    elif mode != "plain":
                        raise ValueError(f"unknown mode: {mode}")
                if i == capture_idx:
                    _cap_begin(cap)
                    try:
                        res = orig(self, view, decision, options)
                    finally:
                        _cap_end()
                else:
                    res = orig(self, view, decision, options)
                if i == swap_idx:
                    rec["swap_digest"] = U._dig(U._canon(res))
                    rec["swap_seat"] = (f'L{view.get("loop")}D{view.get("day")}'
                                        f'{"mm" if not is_prot else view.get("seat")}'
                                        f':{decision}')
                return res
            return decide
        cls.decide = mk()
    try:
        probe = replace(sc, loops=loops)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        for cls, orig in saved:
            cls.decide = orig
        _bel._RECOMPUTE_CACHE.clear()
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw, outcome = loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    else:
        ltw, outcome = loops + 1, "loss"
    return {"game": gid, "mode": mode, "perm": perm, "swap_idx": swap_idx,
            "ltw": ltw, "outcome": outcome, "swap_digest": rec["swap_digest"],
            "swap_seat": rec["swap_seat"], "n_decides": rec["n_decides"],
            "cap": cap}


def counterfactual(days: int, row: dict, perm: str,
                   move_dig_id: str, move_dig_p: str) -> dict:
    """(b) の裏取り＝inject／revert の2方向。分類も付す。"""
    out = {}
    inj = run_single_swap(days, row["game"], row["idx"], perm, mode="inject")
    out["inject_ltw"], out["inject_outcome"] = inj["ltw"], inj["outcome"]
    # 分岐の再現確認＝入れ替えた1席の手が perm 走行の手と一致していること
    out["inject_repro"] = (inj["swap_digest"] == move_dig_p)
    if inj["ltw"] == row["ltw_p"]:
        out["inject_cls"] = "単一分岐で再現"
    elif inj["ltw"] == row["ltw_id"]:
        out["inject_cls"] = "動かず"
    else:
        out["inject_cls"] = "第3の結果"
    rev = run_single_swap(days, row["game"], row["idx"], perm, mode="revert")
    out["revert_ltw"], out["revert_outcome"] = rev["ltw"], rev["outcome"]
    out["revert_repro"] = (rev["swap_digest"] == move_dig_id)
    if rev["ltw"] == row["ltw_id"]:
        out["revert_cls"] = "単一分岐で復元"
    elif rev["ltw"] == row["ltw_p"]:
        out["revert_cls"] = "戻らず"
    else:
        out["revert_cls"] = "第3の結果"
    return out


def run_traced(days: int, gid: str, swaps: dict[int, str] | None = None,
               loops: int = 8) -> dict:
    """1局を **indexed trace つき**で走らせる（`swaps={decide序数: perm}` を注入可）。

    ★用途＝連鎖の解剖（inject が「動かず」のとき、**次に**列挙順が効いた decide を
    特定する）。基準は id 並び＝`swaps` の席だけ perm を当てる。挙動は走行内のみ。
    """
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.benchmark import benchmark_scripts
    from sim import run_game
    import agents.belief as _bel

    name, seed_s = gid.rsplit("#", 1)
    seed = int(seed_s)
    sc = next((s for nm, sd, s in benchmark_scripts(days=days)
               if nm == name and sd == seed), None)
    if sc is None:
        raise KeyError(f"脚本が見つからない: {gid} (days={days})")
    swaps = swaps or {}
    saved_rec = U.REC
    U.REC = DecideIndexedRecorder()
    _bel._RECOMPUTE_CACHE.clear()
    tie_noise.uninstall_perm()
    orig = HeuristicProtagonist.decide

    def wrapper(self, view, decision, options):
        i = len(U.REC._mv)          # ★decide 序数＝moves の現在長（ctx が後で append）
        pm = swaps.get(i)
        if (pm and decision in tie_noise._PERM_DECISIONS and len(options) > 1):
            options = tie_noise.permute(options, pm)
        return orig(self, view, decision, options)

    HeuristicProtagonist.decide = wrapper
    try:
        U._install_ctx()            # 現在の decide（＝wrapper）を包む
        U.install()
        U.REC.on = True
        U.REC.begin_game(gid)
        probe = replace(sc, loops=loops)
        mm = HeuristicMastermind(seed)
        hp = HeuristicProtagonist(seed)
        state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        U.REC.on = False
        U.uninstall()
        U._uninstall_ctx()
        HeuristicProtagonist.decide = orig
        rec = U.REC
        U.REC = saved_rec
        _bel._RECOMPUTE_CACHE.clear()
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        ltw, outcome = state.loop_no, "defense"
    elif fb:
        ltw, outcome = loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
    else:
        ltw, outcome = loops + 1, "loss"
    return {"days": days, "perm": f"id+swaps{sorted(swaps)}", "loops": loops,
            "hashseed": os.environ.get("PYTHONHASHSEED"),
            "rows": [{"game": gid, "loops_to_win": ltw, "outcome": outcome}],
            "moves": rec.moves, "trace": rec.trace, "sites": rec.sites}


def chain(days: int, gid: str, perm: str, start_idx: int,
          max_steps: int = 6) -> dict:
    """★連鎖の解剖＝flip を再現するのに必要な「列挙順の分岐」を1つずつ注入して数える。

    perm 走行（正）と「id＋注入席の集合」を突き合わせ、次に手が食い違う decide を
    注入集合へ足していく。ltw が perm 側に一致した時点の集合＝**flip の必要分岐列**。
    ★perm 走行は同一局の only= 単局再走（コーパス走行と同じ結果になることは
    rows で確認できる）。max_steps で打ち切り（打ち切りは「未確定」と報告する）。
    """
    target = run_corpus_indexed(days, perm, verbose=False, only=[gid])
    ltw_target = target["rows"][0]["loops_to_win"]
    swaps: dict[int, str] = {start_idx: perm}
    steps = [{"idx": start_idx}]
    out = {"game": gid, "perm": perm, "ltw_target": ltw_target, "steps": steps,
           "complete": False}
    for _ in range(max_steps):
        cur = run_traced(days, gid, swaps)
        ltw = cur["rows"][0]["loops_to_win"]
        out["ltw_now"] = ltw
        if ltw == ltw_target:
            out["complete"] = True
            break
        at = attribute_flip(cur, target, gid)
        i = at.get("idx")
        if i is None or i in swaps:
            out["note"] = f"食い違いが見つからない/重複 idx={i}"
            break
        swaps[i] = perm
        steps.append({"idx": i, "seat": at.get("seat"),
                      "site": at.get("sel_site"), "cls": at.get("sel_cls")})
    out["n_steps"] = len(swaps)
    return out


# ---------------------------------------------------------------------------
# (c) 分岐 decide の中身（同点グループ）を覗く capture shim
# ---------------------------------------------------------------------------
_CAP: dict = {"out": None}
_CAP_INSTALLED: list = []


def _fmt_item(o) -> str:
    if isinstance(o, dict):
        if "card" in o:
            tk = o.get("target_kind")
            return f'{o["card"]}→{o.get("target")}' + (f'[{tk}]' if tk else "")
        if "ability" in o:
            return f'{o.get("character")}:{o.get("ability")}→{o.get("target")}'
        if "action" in o:
            return f'action={o.get("action")}'
    s = U._canon(o)
    return s if len(s) <= 90 else s[:87] + "..."


def _cap_shim_extreme(orig, kind):
    def shim(*args, **kw):
        key = kw.get("key")
        if _CAP["out"] is None or len(args) != 1 or not callable(key):
            return orig(*args, **kw)
        items = list(args[0])
        box: list = []
        kw2 = dict(kw)
        kw2["key"] = U._wrap_key(key, box)
        res = orig(items, **kw2)
        if len(box) == len(items) and len(items) >= 2:
            _CAP["out"].append({"site": U._site(kind), "kind": kind,
                                "pairs": [[repr(k), _fmt_item(it)]
                                          for it, k in zip(items, box)][:200],
                                "res": _fmt_item(res)})
        return res
    return shim


def _cap_shim_sorted(orig):
    def shim(*args, **kw):
        key = kw.get("key")
        if _CAP["out"] is None or len(args) != 1 or not callable(key):
            return orig(*args, **kw)
        items = list(args[0])
        box: list = []
        kw2 = dict(kw)
        kw2["key"] = U._wrap_key(key, box)
        res = orig(items, **kw2)
        if len(box) == len(items) and len(items) >= 2:
            _CAP["out"].append({"site": U._site("sorted"), "kind": "sorted",
                                "pairs": [[repr(k), _fmt_item(it)]
                                          for it, k in zip(items, box)][:200],
                                "res": "|".join(_fmt_item(x) for x in res[:3])})
        return res
    return shim


def _cap_begin(out: list) -> None:
    if not _CAP_INSTALLED:
        import importlib
        for name in U.AGENT_MODULES:
            try:
                m = importlib.import_module(name)
            except Exception:
                continue
            for kind, shim in (("sorted", _cap_shim_sorted(U._B_SORTED)),
                               ("max", _cap_shim_extreme(U._B_MAX, "max")),
                               ("min", _cap_shim_extreme(U._B_MIN, "min"))):
                had = kind in m.__dict__
                _CAP_INSTALLED.append((m, kind, had, m.__dict__.get(kind)))
                setattr(m, kind, shim)
    _CAP["out"] = out


def _cap_end() -> None:
    _CAP["out"] = None
    while _CAP_INSTALLED:
        m, kind, had, prev = _CAP_INSTALLED.pop()
        if had:
            setattr(m, kind, prev)
        else:
            m.__dict__.pop(kind, None)


def tie_group(cap: list, site: str) -> dict | None:
    """capture 結果から、当該 site の**勝者と同点だった候補群**を取り出す。"""
    ent = next((c for c in reversed(cap) if c["site"] == site), None)
    if ent is None:
        return None
    pairs = ent["pairs"]
    if not pairs:
        return None
    if ent["kind"] == "max":
        top = max(pairs, key=lambda kv: _key_of(kv[0]))
        tk = _key_of(top[0])
    elif ent["kind"] == "min":
        top = min(pairs, key=lambda kv: _key_of(kv[0]))
        tk = _key_of(top[0])
    else:
        tk = max(_key_of(p[0]) for p in pairs)
    tied = [p for p in pairs if _key_of(p[0]) == tk]
    return {"site": site, "kind": ent["kind"], "n": len(pairs),
            "top_key": repr(tk), "tied": tied, "res": ent["res"]}


def _key_of(rk: str):
    try:
        return eval(rk, {"__builtins__": {}}, {})       # repr された float/tuple を戻す
    except Exception:
        return rk


# ---------------------------------------------------------------------------
# フェーズ0 ドライバ
# ---------------------------------------------------------------------------
def phase0(days: int, perms=PERMS, loops: int = 8, cf: bool = True,
           n_examples: int = 4, limit: int | None = None,
           verbose: bool = True) -> dict:
    """コーパス走行 → flip 帰属 → 反実仮想 → 例の採取。まとめを返す（trace は捨てる）。"""
    print(f"★PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')} days={days} "
          f"perms={','.join(perms)}（基準は id）", flush=True)
    base = run_corpus_indexed(days, "id", loops=loops, limit=limit, verbose=verbose)
    sc = U.seat_counts(base)
    out = {"days": days, "loops": loops,
           "hashseed": os.environ.get("PYTHONHASHSEED"),
           "base_defense": sum(1 for r in base["rows"] if r["outcome"] == "defense"),
           "base_rows": {r["game"]: r["loops_to_win"] for r in base["rows"]},
           "seat_counts_id": {"all": sc["all"], "exact": sc["exact"],
                              "ulp": sc["ulp"]},
           "pairs": {}}
    print(f'  [id] 防衛={out["base_defense"]} 決定席={sc["all"]} '
          f'完全同点を含む席={sc["exact"]}', flush=True)
    for pm in perms:
        pr = run_corpus_indexed(days, pm, loops=loops, limit=limit, verbose=verbose)
        rows = flip_rows(base, pr)
        pdef = sum(1 for r in pr["rows"] if r["outcome"] == "defense")
        nd = moves_diverged_count(base, pr)
        print(f'  [id vs {pm}] 手が食い違った局={nd} flip={len(rows)} '
              f'防衛 {out["base_defense"]}→{pdef}', flush=True)
        for r in rows:
            if r["idx"] is None:
                continue
            i = r["idx"]
            mdi = base["moves"][r["game"]][i][1] if i < len(base["moves"][r["game"]]) else None
            mdp = pr["moves"][r["game"]][i][1] if i < len(pr["moves"][r["game"]]) else None
            if cf and mdi is not None and mdp is not None:
                r["cf"] = counterfactual(days, r, pm, mdi, mdp)
        # (c) 例の採取＝決着 site 上位から、exact 帰属の flip 局を数例
        top_sites = Counter(r["sel_site"] for r in rows).most_common(3)
        examples = []
        for site, _n in top_sites:
            got = 0
            for r in rows:
                if got >= n_examples:
                    break
                if r["sel_site"] != site or r.get("idx") is None:
                    continue
                one = run_single_swap(days, r["game"], None, pm, mode="plain",
                                      capture_idx=r["idx"])
                tg = tie_group(one["cap"], site)
                if tg is not None:
                    tg["game"], tg["idx"], tg["seat"] = r["game"], r["idx"], r["seat"]
                    tg["cls"], tg["dir"] = r["sel_cls"], r["dir"]
                    examples.append(tg)
                    got += 1
        out["pairs"][pm] = {"defense": pdef, "n_moves_diverged": nd,
                            "flips": rows, "examples": examples}
    return out


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------
def render_report(results: list[dict]) -> str:
    lines = []
    pooled_all: list[dict] = []
    for res in results:
        d = res["days"]
        lines.append(f'\n########## {d}日級（基準 perm=id・PYTHONHASHSEED={res["hashseed"]}） ##########')
        sc = res["seat_counts_id"]
        lines.append(f'  (0) 決定席={sc["all"]} ／ 完全同点を含む席={sc["exact"]} '
                     f'({100.0 * sc["exact"] / max(1, sc["all"]):.0f}%) ／ 1ULPタイを含む席={sc["ulp"]}')
        pooled: list[dict] = []
        for pm, pd in res["pairs"].items():
            rows = pd["flips"]
            pooled.extend(rows)
            lines.append(f'  --- id({res["base_defense"]}) vs {pm}({pd["defense"]}): '
                         f'flip {len(rows)} 局（手の食い違い {pd["n_moves_diverged"]} 局） ---')
            for label, key in (("決着site", "sel_site"), ("起点site", "origin_site")):
                c = Counter(r.get(key, "?") for r in rows)
                for s, n in c.most_common(6):
                    lines.append(f'      {label} {s:64s} {n:3d}/{len(rows)}')
            ccls = Counter(r["sel_cls"] for r in rows)
            lines.append("      決着callの分類: " +
                         " / ".join(f"{k}={v}" for k, v in ccls.most_common()))
            cdir = Counter(r["dir"] for r in rows)
            lines.append("      向き: " + " / ".join(f"{k}={v}" for k, v in cdir.most_common()))
            cscript = Counter(r["script"] for r in rows)
            lines.append("      脚本別: " + " ".join(f"{k}={v}" for k, v in
                                                  sorted(cscript.items(), key=lambda kv: -kv[1])))
            cfr = [r for r in rows if "cf" in r]
            if cfr:
                ci = Counter(r["cf"]["inject_cls"] for r in cfr)
                cv = Counter(r["cf"]["revert_cls"] for r in cfr)
                ri = sum(1 for r in cfr if r["cf"]["inject_repro"])
                rv = sum(1 for r in cfr if r["cf"]["revert_repro"])
                lines.append(f'      反実仮想 inject（分母{len(cfr)}・分岐再現{ri}）: '
                             + " / ".join(f"{k}={v}" for k, v in ci.most_common()))
                lines.append(f'      反実仮想 revert（分母{len(cfr)}・分岐再現{rv}）: '
                             + " / ".join(f"{k}={v}" for k, v in cv.most_common()))
            else:
                lines.append("      反実仮想: 未測")
        # コーパス内 pooled
        c = Counter(r["sel_site"] for r in pooled)
        top3 = c.most_common(3)
        n3 = sum(n for _s, n in top3)
        lines.append(f'  == {d}日級 pooled（延べ {len(pooled)} flip）: '
                     f'上位3 site 帰属 {n3}/{len(pooled)} '
                     f'({100.0 * n3 / max(1, len(pooled)):.0f}%) ==')
        for s, n in top3:
            lines.append(f'      {s:64s} {n:3d}')
        pooled_all.extend(pooled)
    # ★事前登録判定（両コーパス合算）
    n = len(pooled_all)
    exact = sum(1 for r in pooled_all if r["sel_cls"] == "exact")
    c = Counter(r["sel_site"] for r in pooled_all)
    top3 = c.most_common(3)
    n3 = sum(x for _s, x in top3)
    lines.append(f'\n===== ★事前登録判定（両コーパス合算・延べ {n} flip） =====')
    lines.append(f'  判定1（帰属の健全性）: exact 帰属 {exact}/{n} '
                 f'({100.0 * exact / max(1, n):.0f}%) → {"○（過半）" if exact * 2 > n else "✗"}')
    lines.append(f'  判定2（★主判定）: 上位3 site 帰属 {n3}/{n} '
                 f'({100.0 * n3 / max(1, n):.0f}%) → '
                 f'{"○ フェーズ1に進む価値あり" if n3 * 2 > n else "✗ 負の結果（散らばっている）"}')
    for s, x in top3:
        lines.append(f'      {s:64s} {x:3d}')
    lines.append('  判定3（検問4＝btx_seal_cat）: 本文の層別を参照（機序の説明は報告本文）')
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-275 フェーズ0：perm flip の帰属分布")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("phase0")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--perms", default=",".join(PERMS))
    p.add_argument("--loops", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-cf", action="store_true")
    p.add_argument("--out", default=None)
    r = sub.add_parser("report")
    r.add_argument("paths", nargs="+")
    q = sub.add_parser("probe")
    q.add_argument("--days", type=int, default=3)
    q.add_argument("--game", required=True)
    q.add_argument("--idx", type=int, required=True)
    q.add_argument("--perm", default="id")
    args = ap.parse_args(argv)

    if args.cmd == "phase0":
        res = phase0(days=args.days, perms=tuple(args.perms.split(",")),
                     loops=args.loops, cf=not args.no_cf, limit=args.limit)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            print(f"  → {args.out}")
        print(render_report([res]))
        return 0
    if args.cmd == "report":
        results = []
        for pth in args.paths:
            with open(pth, encoding="utf-8") as f:
                results.append(json.load(f))
        print(render_report(results))
        return 0
    if args.cmd == "probe":
        mode = "plain" if args.perm == "id" else "inject"
        one = run_single_swap(args.days, args.game,
                              args.idx if mode == "inject" else None,
                              args.perm, mode=mode, capture_idx=args.idx)
        print(f'game={args.game} idx={args.idx} perm={args.perm} '
              f'ltw={one["ltw"]} outcome={one["outcome"]}')
        for c in one["cap"]:
            print(f'  {c["kind"]:6s} {c["site"]}')
            for k, it in c["pairs"][:40]:
                print(f'    {k:>28s}  {it}')
            print(f'    → {c["res"]}')
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
