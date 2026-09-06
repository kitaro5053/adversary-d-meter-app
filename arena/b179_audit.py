# -*- coding: utf-8 -*-
"""B-179 Phase 1：**TT ガードが「置いても友好3に届かない席」まで守っている**の射程を測る。

★**計測のみ**（`agents/` `engine/` `rules/` `sim/` 非接触）。
起票＝`docs/バックログ_構想メモ_FableA.md` §55（B-178 の副産物・2026-08-06）。
実戦証拠＝同 §56 (1)（`btx5_future`・ユーザーが脚本家側でプレイ）。

------------------------------------------------------------------------------
## 0. 用語（★略語を使う前に、変数が何を指すかを定義する）
------------------------------------------------------------------------------

| 語 | 何を指すか（現物の場所） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回。1日3席 |
| **友好席（GW）** | その席で選ばれた手が `友好+1`／`友好+2` で、対象がキャラであるもの |
| **step** | その札で載る友好の増分（`友好+1`→1／`友好+2`→2） |
| **g** | その席の時点（＝行動解決フェイズ**前**）の対象の友好カウンター（`view` の公開値） |
| **最終日** | `view["day"] >= view["days_per_loop"]` |
| **TT ガード** | `HeuristicProtagonist._b86_gw_keep`（`agents/heuristic_protagonist.py:2033-2034`）＝`P(TT)>0` ∪ `_tt_guards` |
| **層A** | 最終日 かつ `g + step <= 2`＝**その札を置いても友好3に届かない**＝TT 封じ手として無効 |
| **層B** | 最終日 かつ `g + step >= 3`＝**正しい封じ手**（触ってはいけない） |
| **層C** | 最終日**以外**＝最終日までに3へ積む経路の一部かもしれない（軽々に切れない） |
| **反実（G8のみ）** | `gw_keep` を空にして `card_effect.noop_reason` を呼び直した結果。★G9 の材料 `gw_final_void` は**すでに keep を除外して組まれている**ので、これだけでは G9 は動かない |
| **反実（G8+G9）** | `_b86_gw_keep` を空にしたうえで `_b109_build_futile` を**呼び直して** `gw_final_void` を組み直し、その材料で `noop_reason` を呼ぶ＝**TT ガードを外した時に実際に発火するゲート** |

★**本監査は「正解の配役」を主人公AIの判断の材料に一切使わない**（運用doc §3-7）。
使うのは `protagonist_view`・公開履歴・公開カウンター（`phase_snapshots` の `goodwill`/`alive`）だけ。
唯一 `census` サブコマンドだけが**脚本の定義**（`script.roles`）を読むが、これは
`arena/corpus_census.py` と同じ「コーパスを数える」用途であって AI には渡らない
（出力でも「監査側の後知恵」と明示する）。

------------------------------------------------------------------------------
## 1. 射程の定義（★数える前に固定する）
------------------------------------------------------------------------------

**射程（＝Phase 2 の是正が実際に触りうる席）**＝

    友好席 ∧ 対象 ∈ TT ガード ∧ 層A ∧ 反実（G8+G9）が Noop を返す

層B・層C は**射程に含めない**（層B＝正しい封じ手／層C＝日をまたぐ積み上げ）。

★**「3に届かない」は不可能証明ではない**：友好カウンターは行動カード以外でも動く
＝事件『流布』は友好を2つ移す（`rules/40_first_steps.md:154`／`rules/50_basic_tragedy_x.md:215`）、
『蝶の羽ばたき』は犯人と同エリアの1人に任意種のカウンターを1つ置ける（`rules/50:219`）、
ご神木の特性（`rules/30_characters_fs.md` 参照）もカウンターを動かす。
∴ 本監査は **`実際の最終日終了時の友好`**（`phase_snapshots` の「事件フェイズ後」）も併記し、
「g+step<=2 だったが実際は3以上になった」席を**別枠で数える**。

------------------------------------------------------------------------------
## 2. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面・単独実行）
------------------------------------------------------------------------------

    python -m arena.b179_audit verify --days 3   # 挙動不変の物証（プローブ有無で棋譜一致）
    python -m arena.b179_audit count  --days 3   # 層A/B/C の数え上げ（独立脚本数・出現率つき）
    python -m arena.b179_audit rows   --days 3 --json out.json   # 席の生データ
    python -m arena.b179_audit subset --days 3   # `_tt_guards ⊆ {P(TT)>0}` の実測検査
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b174_audit import _play_plain, apply_cfg, switches
from arena.b177_audit import _GW, _Rich
from sim import run_game

#: 最終日の「ターン終了フェイズ」が読む友好＝事件フェイズ解決**後**の値
_END_POINTS = ("事件フェイズ後", "ターン終了フェイズ後", "主人公能力フェイズ後")


# ---------------------------------------------------------------------------
# プローブ（★`_Rich` を継承し、記録を足すだけ＝`super().decide()` の戻り値をそのまま返す）
# ---------------------------------------------------------------------------
class _TT(_Rich):
    """`b177_audit._Rich` に **TT ガードの層別**の材料だけを足す（挙動不変・`verify` で実証）。"""

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        n0 = len(self.stream)
        chosen = super().decide(view, decision, options)
        if decision != "set_card" or len(self.stream) <= n0:
            return chosen
        rec = self.stream[-1]
        if rec.get("card") not in _GW or rec.get("kind") != "character":
            return chosen
        tgt = rec.get("target")
        step = 2 if rec["card"] == "友好+2" else 1
        c = next((x for x in view.get("characters", []) or []
                  if x.get("name") == tgt), None)
        g = int((c or {}).get("goodwill", 0) or 0)
        day = int(view.get("day", 1))
        last = int(view.get("days_per_loop", 99))
        keep = set(getattr(self, "_b86_gw_keep", ()) or ())
        guards = list(getattr(self, "_tt_guards", ()) or [])
        try:
            p_tt = float(self._belief.role_marginals().get(tgt, {})
                         .get("タイムトラベラー", 0.0))
        except Exception:                                    # pragma: no cover
            p_tt = float("nan")
        rec["step"] = step
        rec["友好(置く前)"] = g
        rec["友好+step"] = g + step
        rec["最終日か"] = day >= last
        rec["days_per_loop"] = last
        rec["TTガード対象"] = tgt in keep
        rec["P(TT)"] = round(p_tt, 4)
        rec["_tt_guards"] = guards
        rec["_tt_guards入り"] = tgt in guards
        # ★`keep |= set(self._tt_guards)`（`heuristic_protagonist.py:2034`）が実際に
        #   何を足しているか＝`_tt_guards` のうち `P(TT)>0` でない名前（あれば足している）。
        try:
            _m = self._belief.role_marginals()
            rec["keep|=が足した名前"] = sorted(
                n for n in guards if not (_m.get(n, {}).get("タイムトラベラー", 0.0) > 0.0))
        except Exception:                                    # pragma: no cover
            rec["keep|=が足した名前"] = None
        if not (tgt in keep):
            rec["層"] = "対象外(TTガードでない)"
            return chosen
        rec["層"] = ("A_最終日_3に届かない" if (day >= last and g + step <= 2)
                     else "B_最終日_3に届く" if day >= last
                     else "C_最終日以外")
        # ★どの免除経路を通ったか（★ここが Phase 2 の介入点を決める）：
        #   (1) スコア分岐＝`heuristic_protagonist.py:5984-5998`＝`tgt in _tt_guards` かつ
        #       友好<3 のとき **`noop_reason` へ到達せずに** TT 用の高スコアを返す。
        #   (2) G8 の keep＝`card_effect.py:184`＝(1) を通り抜けた席が `gw_keep` で素通りする。
        rec["免除経路"] = ("(1)スコア分岐(_tt_guards・友好<3)"
                          if (tgt in guards and g < 3) else "(2)G8のgw_keep")
        # ★層Bの内訳（★FableA の起票にない第4の層）＝**既に友好3以上へ重ね置き**した席。
        #   `rules/50_basic_tragedy_x.md:128` は「2つ以下」＝3で既に封じ手は成立している。
        #   ただし事件『流布』は友好を2つ**取り除く**（`rules/50:215`）＝最終日に流布が
        #   予定されていれば 3→1 に戻されうる＝重ね置きに規則上の意味がありうる。
        #   ∴ **その日に予定されている事件名（事件表＝公開情報）**を併記する。
        rec["B内訳"] = (None if rec["層"] != "B_最終日_3に届く"
                        else "B1_封じ手(友好<3→3以上)" if g < 3
                        else "B2_既に3以上へ重ね置き")
        rec["その日に予定された事件"] = sorted(
            str(i.get("name")) for i in (view.get("incidents") or [])
            if int(i.get("day", 0)) == day)
        # --- 反実：TT ガードを外したら既存ゲートは発火するか -------------------
        from dataclasses import replace as _dc_replace

        from agents.card_effect import NoopCtx, noop_reason
        base = dict(
            gw_refused=frozenset(getattr(self, "_b86_gw_refused", ())),
            gw_ignore_certain=frozenset(getattr(self, "_b86_gw_ignore_certain", ())),
            gw_info_exhausted=frozenset(getattr(self, "_b86_gw_info_exhausted", ())),
            gw_arms_mm=frozenset(getattr(self, "_b86_gw_arms_mm", ())),
        )
        ctx0 = NoopCtx(
            gw_keep=frozenset(keep),
            gw_final_void=frozenset(getattr(self, "_b109_gw_final_void", ())),
            gw_final_harm=frozenset(getattr(self, "_b109_gw_final_harm", ())),
            **base)
        n0r = noop_reason(view, rec["card"], tgt, "character", ctx0)
        rec["現行ゲート"] = None if n0r is None else n0r.reason
        n8 = noop_reason(view, rec["card"], tgt, "character",
                         _dc_replace(ctx0, gw_keep=frozenset()))
        rec["反実(G8のみ)"] = None if n8 is None else n8.reason
        # ★G9 の材料そのものが keep を除外して組まれている（`heuristic_protagonist.py:1999`）
        #   ＝**現物の関数を keep 空で呼び直して**組み直す（式は書き写さない）。
        saved_keep = getattr(self, "_b86_gw_keep", set())
        saved = (getattr(self, "_b109_gw_final_void", set()),
                 getattr(self, "_b109_gw_final_harm", set()),
                 getattr(self, "_b109_unrest_void", set()))
        try:
            self._b86_gw_keep = set()
            self._b109_build_futile(view)
            ctx9 = _dc_replace(
                ctx0, gw_keep=frozenset(),
                gw_final_void=frozenset(self._b109_gw_final_void),
                gw_final_harm=frozenset(self._b109_gw_final_harm))
            n9 = noop_reason(view, rec["card"], tgt, "character", ctx9)
            rec["反実(G8+G9)"] = None if n9 is None else n9.reason
        finally:
            self._b86_gw_keep = saved_keep
            (self._b109_gw_final_void, self._b109_gw_final_harm,
             self._b109_unrest_void) = saved
        # --- 機会費用の材料（★この席で他に打てた手はあったか） -----------------
        #   ★**再決定はしない**（副作用の恐れ）。代わりに **同じ席の options を
        #     既存の空振り判定に通し**、「空振りでない別の手」が何枚あったかを数える。
        rec["機会費用"] = self._alternatives(view, options, chosen)
        return chosen

    # -- 席の代替手（★主人公が見える情報だけ・再決定はしない） -------------------
    def _alternatives(self, view: dict, options: list[dict], chosen: dict) -> dict:
        from agents.b100_mix import futile_reason, noop_ctx_for, rumor_prob
        try:
            roles = self._belief.role_marginals()
        except Exception:                                    # pragma: no cover
            roles = {}
        ctx = noop_ctx_for(self, view)
        rp = rumor_prob(self)
        live = []
        void = []
        for o in options:
            key = (o.get("card"), o.get("target"), o.get("target_kind"))
            if key == (chosen.get("card"), chosen.get("target"),
                       chosen.get("target_kind")):
                continue
            try:
                fr = futile_reason(view, self, roles, o.get("card"), o.get("target"),
                                   o.get("target_kind"), ctx, rp)
            except Exception as e:                           # pragma: no cover
                fr = f"判定不能({e})"
            (void if fr else live).append(f"{o.get('card')}→{o.get('target')}")
        return {"選べた手の総数": len(options),
                "空振りでない別の手の数": len(live),
                "空振りと判定される別の手の数": len(void),
                "空振りでない別の手(先頭12)": live[:12]}


# ---------------------------------------------------------------------------
# 1局の実行
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int, shadow: bool = True):
    hp = _TT(seed, shadow=shadow)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return hp, st, trace


def _final_goodwill(st) -> dict:
    """(loop, キャラ名) → そのループの**最終日のターン終了フェイズが読む友好**。

    ★読むのは `goodwill`/`alive` だけ（どちらも卓上の公開情報）。`role` は読まない。
    """
    best: dict = {}
    for s in st.phase_snapshots:
        pt = s.get("point")
        if pt not in _END_POINTS:
            continue
        rank = _END_POINTS.index(pt)
        L, D = s.get("loop"), s.get("day")
        for n, c in (s.get("characters") or {}).items():
            k = (L, n)
            cur = best.get(k)
            if cur is None or (D, -rank) > (cur[0], -cur[1]):
                best[k] = (D, rank, int(c.get("goodwill", 0) or 0),
                           bool(c.get("alive", True)))
    return {k: {"最終日": v[0], "友好": v[2], "生存": v[3]} for k, v in best.items()}


def _script_sig(sc):
    from arena.corpus_census import script_signature
    return script_signature(sc)


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
        for tag, sh in (("probe(shadow=on)", True), ("probe(shadow=off)", False)):
            _hp, _st, tr = _play(sc, seed, loops, shadow=sh)
            if tr != ref:
                bad.append({"game": f"{name}#{seed}", "mode": tag})
    return {"days": days, "n_games": len(games),
            "mismatch": len(bad), "bad": bad[:20]}


# ---------------------------------------------------------------------------
# rows＝友好席の生データ（1席1行）
# ---------------------------------------------------------------------------
def collect(days: int = 3, loops: int = 8, start: int = 0,
            end: int | None = None) -> list:
    from arena.benchmark import benchmark_scripts

    apply_cfg("off")
    print(f"[切替口] {switches()} / days={days}", flush=True)
    rows: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _ = _play(sc, seed, loops)
        fin = _final_goodwill(st)
        sig = _script_sig(sc)
        # ★監査側の後知恵（**AI には渡らない**・出力の解釈のためだけに読む）＝
        #   `arena/corpus_census.py` と同じ「脚本の定義を数える」用途。
        tt_real = "タイムトラベラー" in set((sc.roles or {}).values())
        for r in hp.stream:
            if r.get("card") not in _GW:
                continue
            if r.get("kind") != "character":
                # ★ボード対象の 友好+（幻想の読み替え投資＝`heuristic_protagonist.py:5957-5970`）
                #   は本監査の層別の対象外＝**席数だけ数えて限界として報告する**。
                rows.append({"game": f"{name}#{seed}", "family": name,
                             "sig": str(hash(sig)), "★後知恵:TTが実在": tt_real,
                             "層": "対象外(ボード対象の友好+)", **r})
                continue
            row = {"game": f"{name}#{seed}", "family": name,
                   "sig": str(hash(sig)), "★後知恵:TTが実在": tt_real, **r}
            f = fin.get((r.get("loop"), r.get("target")))
            row["実際の最終日終了時の友好"] = None if f is None else f["友好"]
            row["最終日に生存"] = None if f is None else f["生存"]
            rows.append(row)
        print(f"  {name}#{seed}: 友好席 {sum(1 for x in rows if x['game'] == f'{name}#{seed}')}",
              flush=True)
    return rows


# ---------------------------------------------------------------------------
# count＝層A/B/C の数え上げ
# ---------------------------------------------------------------------------
def _tally(rows: list, key: str) -> dict:
    """層別に「席数／局数／独立脚本数」を出す。"""
    seats = Counter()
    games = defaultdict(set)
    sigs = defaultdict(set)
    for r in rows:
        k = r.get(key)
        seats[k] += 1
        games[k].add(r["game"])
        sigs[k].add(r["sig"])
    return {k: {"席": seats[k], "局": len(games[k]), "独立脚本": len(sigs[k])}
            for k in sorted(seats, key=lambda x: (-seats[x], str(x)))}


def count(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    n_games = len(list(benchmark_scripts(days=days))[start:end])
    rows = collect(days, loops, start, end)
    gw = [r for r in rows if r.get("kind") == "character"]
    board = [r for r in rows if r.get("kind") != "character"]
    kept = [r for r in gw if r.get("TTガード対象")]
    out: dict = {
        "days": days, "局数": n_games,
        "友好席の総数(キャラ対象)": len(gw),
        "★層別の対象外＝ボード対象の友好+席": {
            "席": len(board), "局": len({r["game"] for r in board})},
        "TTガード対象の友好席": _tally(kept, "層") if kept else {},
        "TTガード対象の友好席(合計)": {
            "席": len(kept), "局": len({r["game"] for r in kept}),
            "独立脚本": len({r["sig"] for r in kept})},
    }
    for layer in ("A_最終日_3に届かない", "B_最終日_3に届く", "C_最終日以外"):
        sub = [r for r in kept if r.get("層") == layer]
        if not sub:
            out[f"層 {layer}"] = {"席": 0}
            continue
        d: dict = {
            "席": len(sub), "局": len({r["game"] for r in sub}),
            "独立脚本": len({r["sig"] for r in sub}),
            "出現率(局)": f"{100.0 * len({r['game'] for r in sub}) / max(1, n_games):.1f}%",
            "族別": _tally(sub, "family"),
            "反実(G8のみ)が発火": _tally(sub, "反実(G8のみ)"),
            "反実(G8+G9)が発火": _tally(sub, "反実(G8+G9)"),
        }
        fired = [r for r in sub if r.get("反実(G8+G9)")]
        d["★射程(反実G8+G9が発火した席)"] = {
            "席": len(fired), "局": len({r["game"] for r in fired}),
            "独立脚本": len({r["sig"] for r in fired}),
            "族別": _tally(fired, "family") if fired else {}}
        if fired:
            d["★射程の機会費用"] = {
                "空振りでない別の手が1つ以上あった席":
                    sum(1 for r in fired
                        if (r.get("機会費用") or {}).get("空振りでない別の手の数", 0) > 0),
                "空振りでない別の手が0だった席":
                    sum(1 for r in fired
                        if (r.get("機会費用") or {}).get("空振りでない別の手の数", 0) == 0),
                "空振りでない別の手の数の分布":
                    dict(Counter((r.get("機会費用") or {}).get("空振りでない別の手の数")
                                 for r in fired)),
            }
        # ★札以外で友好が動いた可能性（流布・蝶の羽ばたき・ご神木）の実測
        d["実際の最終日終了時の友好の分布"] = dict(Counter(
            r.get("実際の最終日終了時の友好") for r in sub))
        d["★g+step<=2 だったが実際は3以上になった席"] = sum(
            1 for r in sub if r.get("友好+step", 9) <= 2
            and (r.get("実際の最終日終了時の友好") or 0) >= 3)
        # ★監査側の後知恵（AI には渡らない）＝そのガードは実在の TT を守っていたか
        d["後知恵：TTが実在する局の席"] = sum(1 for r in sub if r.get("★後知恵:TTが実在"))
        d["後知恵：TTが実在しない局の席"] = sum(
            1 for r in sub if not r.get("★後知恵:TTが実在"))
        d["免除経路"] = _tally(sub, "免除経路")
        if layer == "B_最終日_3に届く":
            d["★B内訳"] = _tally(sub, "B内訳")
            over = [r for r in sub if r.get("B内訳") == "B2_既に3以上へ重ね置き"]
            d["★B2(重ね置き)のうち反実(G8+G9)が発火"] = {
                "席": sum(1 for r in over if r.get("反実(G8+G9)")),
                "局": len({r["game"] for r in over if r.get("反実(G8+G9)")}),
                "独立脚本": len({r["sig"] for r in over if r.get("反実(G8+G9)")}),
                "うち その日に『流布』が予定されていた席": sum(
                    1 for r in over if r.get("反実(G8+G9)")
                    and "流布" in (r.get("その日に予定された事件") or [])),
            }
        if fired:
            d["★射程の免除経路"] = _tally(fired, "免除経路")
            d["★射程の後知恵"] = {
                "TTが実在する局の席": sum(1 for r in fired if r.get("★後知恵:TTが実在")),
                "TTが実在しない局の席": sum(
                    1 for r in fired if not r.get("★後知恵:TTが実在"))}
        out[f"層 {layer}"] = d
    # 参考＝ガード対象でない友好席（現行ゲートが既に効いている領域）
    out["参考：TTガード対象でない友好席"] = {
        "席": len(gw) - len(kept),
        "現行ゲート発火": _tally([r for r in gw if not r.get("TTガード対象")],
                                 "現行ゲート"),
    }
    return out


# ---------------------------------------------------------------------------
# subset＝`_tt_guards ⊆ {P(TT)>0}` の実測検査（keep |= の意味を測る）
# ---------------------------------------------------------------------------
def subset(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    rows = collect(days, loops, start, end)
    guard_rows = [r for r in rows if r.get("_tt_guards入り")]
    return {
        "days": days,
        "友好席の総数": len(rows),
        "_tt_guards に入っていた対象への友好席": len(guard_rows),
        "そのうち P(TT)==0 だった席（＝keep |= が実際に足した席）":
            sum(1 for r in guard_rows if not (r.get("P(TT)") or 0) > 0),
        "P(TT)>0 だが _tt_guards 外の席": sum(
            1 for r in rows if (r.get("P(TT)") or 0) > 0 and not r.get("_tt_guards入り")),
        "★`keep |= set(_tt_guards)` が実際に足した名前が1つ以上あった席": sum(
            1 for r in rows if r.get("keep|=が足した名前")),
        "その名前の例": sorted({n for r in rows
                                for n in (r.get("keep|=が足した名前") or [])})[:10],
        "_tt_guards が空でなかった席": sum(1 for r in rows if r.get("_tt_guards")),
    }


# ---------------------------------------------------------------------------
# census＝TT が実在した局の数え上げ（★監査側の後知恵・AI には渡らない）
# ---------------------------------------------------------------------------
def census(days: int = 3) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.corpus_census import script_signature

    rows = list(benchmark_scripts(days=days))
    hit = [(n, s, sc) for n, s, sc in rows
           if "タイムトラベラー" in set((sc.roles or {}).values())]
    return {"days": days, "局数": len(rows),
            "TT が配役に居る局": len(hit),
            "出現率": f"{100.0 * len(hit) / max(1, len(rows)):.1f}%",
            "独立脚本": len({script_signature(sc) for _n, _s, sc in hit}),
            "族別": dict(Counter(n for n, _s, _sc in hit))}


def main() -> None:
    ap = argparse.ArgumentParser(description="B-179 TTガードの過剰保護の計測")
    ap.add_argument("cmd", choices=["verify", "count", "rows", "subset", "census"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end)
    elif a.cmd == "rows":
        res = collect(a.days, a.loops, a.start, a.end)
    elif a.cmd == "subset":
        res = subset(a.days, a.loops, a.start, a.end)
    elif a.cmd == "census":
        res = census(a.days)
    else:
        res = count(a.days, a.loops, a.start, a.end)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
