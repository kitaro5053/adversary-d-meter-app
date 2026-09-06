# -*- coding: utf-8 -*-
"""B-178 Phase 1：**解禁まで届かない投資先へ友好が流れる**の射程を測る（★計測のみ・`agents/` 非接触）。

起票＝`docs/バックログ_構想メモ_FableA.md` §54（B-177 の副産物・2026-08-06）。
出典＝`docs/監査_B177_置き換え先の測定_2026-08-06.md` §7 欠陥候補①。

------------------------------------------------------------------------------
## 0. 用語（★略語を使う前に、変数が何を指すかを定義する）
------------------------------------------------------------------------------

| 語 | 何を指すか（現物の場所） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回（`decide(view,"set_card",options)` が1つ返すこと）。1日3席 |
| **投資席（INV）** | その席で選ばれた手が `友好+1`／`友好+2` で、対象がキャラであるもの |
| **狙う能力** | その投資席で `_invest[対象]` の最大値を作った友好能力。特定は**影の再計算**（`arena.b174_audit._Probe._winner_ability`）＝**式を書き写さない** |
| **必要ハート（H）** | その能力の解禁に要る友好カウンター数（`engine.data.goodwill_abilities_of` の `hearts`） |
| **残り必要ハート（need）** | その席の時点で、解禁まであと何個必要か（`_invest_need[対象]`） |
| **予測解禁日** | `HeuristicProtagonist._cooler_unlock_day(day, need)`＝**主人公AI自身が持っている解禁日の算術**（`agents/heuristic_protagonist.py:1762`）。B-141b で off-by-one を是正済み（`B141B_UNLOCK_SAME_DAY`） |
| **実解禁日** | そのループ中に、対象キャラの**友好カウンター**が H 以上になった**最初の日**。材料＝`state.phase_snapshots` の「脚本家能力フェイズ後」（＝主人公能力フェイズ(6)の直前）の `goodwill`。★友好カウンターは**卓上の公開情報** |
| **(a) 未解禁** | その投資席の狙う能力が、**その席のループ中に一度も解禁されなかった** |
| **(b) 解禁済み・未使用** | 実解禁日はあるが、そのループ中にその能力が**一度も宣言されなかった**（`goodwill_used` イベント） |
| **(c) 解禁され使われた** | 実解禁日があり、かつそのループ中に宣言された |
| **規則上到達不能** | 主人公の行動カードだけでは、**その席の日から最終日までに H へ届かない**（下記 §1-2） |

★**本監査は「正解の配役」を一切参照しない**（運用doc `docs/運用_Opus5でのFableA運用_2026-08-01.md` §3-7）。
使うのは `protagonist_view`・公開履歴（`state.history`）・公開カウンター（`phase_snapshots` の
`goodwill`／`alive`）だけである。役職（`role`）は読まない。

------------------------------------------------------------------------------
## 1. 数え上げの定義（★数える前に固定する）
------------------------------------------------------------------------------

### 1-1. (a)/(b)/(c) は排他

投資席1つにつき、その席のループと対象と狙う能力の3つ組で判定する。
**(a) 未解禁** ⊔ **(b) 解禁済み・未使用** ⊔ **(c) 解禁され使われた** ＝ 投資席の全体。

### 1-2. 「規則上到達不能」の上界（★KB の現物から）

- 主人公は**1キャラ1ターン1枚**しか置けない（`rules/00_rules_core.md:104`
  「他の主人公が既にセットした対象には**重ねられない**」）。
- `友好+2` は**1ループ1回**（`rules/10_action_cards.md:75-76`／`engine/models.ONCE_PER_LOOP`）。
  ★主人公は3人で**各自同じ手札**（`rules/00_rules_core.md:89-90`）＝**1ループに `友好+2` は最大3枚**。
- カウンターは**ループ開始時に全除去**（`rules/00_rules_core.md:86`）＝**次ループへ持ち越せない**。

∴ 今日 `d` から最終日 `D` までの `k = D-d+1` 日で、1キャラに載せられる友好は最大
`k + min(k, p)`（`p`＝まだ使われていない `友好+2` の枚数＝`view["used_cards"]` から数える公開情報）。
これが `H - 現在の友好` に届かなければ **規則上到達不能**。

★**これは上界であって、必ずしも「不可能の証明」ではない**：友好カウンターは
事件効果・一部の能力（ご神木の特性＝カウンター移動 等）でも動きうる。
∴ 本監査の一次証拠は**実測の (a)/(b)/(c)**であり、規則上到達不能はその補助である。

------------------------------------------------------------------------------
## 2. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面・単独実行）
------------------------------------------------------------------------------

    python -m arena.b178_audit verify --days 3        # 挙動不変の物証（プローブ有無で棋譜一致）
    python -m arena.b178_audit count  --days 3        # (a)/(b)/(c) を need 別に数える
    python -m arena.b178_audit rows   --days 3 --json out.json   # 席の生データ
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b174_audit import (_loop_results, _play_plain, _used_abilities,
                              apply_cfg, switches)
from arena.b177_audit import _GW, _Rich
from sim import run_game

#: 友好カウンターを読むスナップショット点（主人公能力フェイズ(6) の直前が第一候補）
_SNAP_POINTS = ("脚本家能力フェイズ後", "行動解決フェイズ後")


def _hearts(char: str, ability: str) -> int | None:
    """その友好能力の**必要ハート数**（`engine.data` のカードデータ＝公開情報）。"""
    from engine.data import goodwill_abilities_of
    for ab in goodwill_abilities_of(char) or []:
        if ab["name"] == ability:
            return int(ab["hearts"])
    return None


def _reachable_by(day: int, last_day: int, gap: int, plus2_left: int) -> int | None:
    """`gap` ハート足りない時、主人公の行動カードだけで届く**最も早い日**（§1-2）。

    `k` 日かけると最大 `k + min(k, plus2_left)` ハート載る。届かなければ None。
    """
    if gap <= 0:
        return day
    for k in range(1, max(0, last_day - day + 1) + 1):
        if k + min(k, plus2_left) >= gap:
            return day + k - 1
    return None


# ---------------------------------------------------------------------------
# プローブ（★`_Rich` を継承し、記録を足すだけ＝`super().decide()` の戻り値をそのまま返す）
# ---------------------------------------------------------------------------
class _Unl(_Rich):
    """`b177_audit._Rich` に「解禁の見込み」の材料だけを足す（挙動不変・`verify` で実証）。"""

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        n0 = len(self.stream)
        chosen = super().decide(view, decision, options)
        if decision != "set_card" or len(self.stream) <= n0:
            return chosen
        rec = self.stream[-1]
        if rec.get("card") not in _GW or rec.get("kind") != "character":
            return chosen
        uc = view.get("used_cards") or {}
        rec["最終日"] = view.get("days_per_loop")
        rec["友好+2の残り枚数"] = sum(
            1 for s in ("p1", "p2", "p3") if "友好+2" not in (uc.get(s) or []))
        # ★既存の空振りゲート（`card_effect.noop_reason` G8/G9）が、この席の手を
        #   既に「空振り」と判定していたか＝**B-109 F1 の射程**（二重実装の確認）。
        #   ★材料は主人公AI自身が組んだ集合をそのまま読む（述語は現物の関数を呼ぶ）。
        from agents.card_effect import NoopCtx, noop_reason
        _ctx = NoopCtx(
            gw_keep=frozenset(getattr(self, "_b86_gw_keep", ())),
            gw_refused=frozenset(getattr(self, "_b86_gw_refused", ())),
            gw_ignore_certain=frozenset(getattr(self, "_b86_gw_ignore_certain", ())),
            gw_info_exhausted=frozenset(getattr(self, "_b86_gw_info_exhausted", ())),
            gw_arms_mm=frozenset(getattr(self, "_b86_gw_arms_mm", ())),
            gw_final_void=frozenset(getattr(self, "_b109_gw_final_void", ())),
            gw_final_harm=frozenset(getattr(self, "_b109_gw_final_harm", ())),
        )
        _np = noop_reason(view, rec["card"], rec.get("target"), "character", _ctx)
        rec["既存の空振りゲート"] = None if _np is None else _np.reason
        # ★TT ガード（`_b86_gw_keep`＝`rules/50:127-128`＝最終日に友好2以下だと任意敗北を
        #   宣言されうる対象）は G8 の先頭で無条件に None を返す＝**この席は空振りではない**。
        rec["TTガード対象(gw_keep)"] = rec.get("target") in getattr(self, "_b86_gw_keep", ())
        from dataclasses import replace as _dc_replace
        _np2 = noop_reason(view, rec["card"], rec.get("target"), "character",
                           _dc_replace(_ctx, gw_keep=frozenset()))
        rec["既存の空振りゲート(TT除外を外した場合)"] = None if _np2 is None else _np2.reason
        ab, need = rec.get("狙う能力"), rec.get("残り必要ハート")
        if ab is None or not isinstance(need, int) or need < 0:
            return chosen
        rec["必要ハート"] = _hearts(rec.get("target"), ab)
        rec["予測解禁日(_unlock)"] = int(self._cooler_unlock_day(view.get("day", 0), need))
        rec["規則上の最早解禁日"] = _reachable_by(
            view.get("day", 0), view.get("days_per_loop", 0), need,
            rec["友好+2の残り枚数"])
        return chosen


# ---------------------------------------------------------------------------
# 1局の実行
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int, shadow: bool = True):
    hp = _Unl(seed, shadow=shadow)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return hp, st, trace


def _goodwill_by_day(st) -> dict:
    """(loop, キャラ名) → {日: その日の主人公能力フェイズ直前の友好カウンター}。

    ★読むのは `goodwill` と `alive` だけ（どちらも卓上の公開情報）。`role` は読まない。
    """
    out: dict = {}
    for point in _SNAP_POINTS:
        for s in st.phase_snapshots:
            if s.get("point") != point:
                continue
            L, D = s.get("loop"), s.get("day")
            for n, c in (s.get("characters") or {}).items():
                if not c.get("alive"):
                    continue
                d = out.setdefault((L, n), {})
                # 先の点（脚本家能力フェイズ後）を優先＝後から上書きしない
                if D not in d:
                    d[D] = int(c.get("goodwill", 0) or 0)
    return out


def _actual_unlock_day(gw: dict, loop, char: str, hearts: int) -> int | None:
    """そのループで友好カウンターが `hearts` 以上になった**最初の日**（無ければ None）。"""
    per = gw.get((loop, char)) or {}
    days = [d for d, v in per.items() if v >= hearts]
    return min(days) if days else None


def _bucket(need) -> str:
    if not isinstance(need, int) or need < 0:
        return "?"
    return str(need) if need <= 3 else "4以上"


# ---------------------------------------------------------------------------
# verify＝挙動不変の物証（プローブ有無で棋譜が完全一致）
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
           configs=("off",), only: tuple = ()) -> dict:
    from arena.benchmark import benchmark_scripts

    games = list(benchmark_scripts(days=days))[start:end]
    if only:
        games = [g for g in games if f"{g[0]}#{g[1]}" in set(only)]
    bad = []
    for cfg in configs:
        apply_cfg(cfg)
        print(f"[切替口:{cfg}] {switches()} / days={days}", flush=True)
        for name, seed, sc in games:
            ref = _play_plain(sc, seed, loops)
            for tag, sh in (("probe(shadow=on)", True), ("probe(shadow=off)", False)):
                _hp, _st, tr = _play(sc, seed, loops, shadow=sh)
                if tr != ref:
                    bad.append({"cfg": cfg, "game": f"{name}#{seed}", "mode": tag})
    apply_cfg("off")
    return {"days": days, "n_games": len(games), "configs": list(configs),
            "mismatch": len(bad), "bad": bad[:20]}


# ---------------------------------------------------------------------------
# rows＝投資席の生データ（1席1行）
# ---------------------------------------------------------------------------
def collect(days: int = 3, loops: int = 8, cfg: str = "off",
            start: int = 0, end: int | None = None) -> list:
    from arena.benchmark import benchmark_scripts

    apply_cfg178(cfg)
    print(f"[切替口:{cfg}] {switches178()} / days={days}", flush=True)
    rows: list = []
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _ = _play(sc, seed, loops)
        used = _used_abilities(st)
        gw = _goodwill_by_day(st)
        lr = _loop_results(st)
        for r in hp.stream:
            if r.get("決定") == "友好能力の使用":
                continue
            ab = r.get("狙う能力")
            if r.get("card") not in _GW or r.get("kind") != "character" or ab is None:
                continue
            H = r.get("必要ハート")
            L, tgt = r.get("loop"), r.get("target")
            act = _actual_unlock_day(gw, L, tgt, H) if H else None
            hit = (L, tgt, ab) in used
            rows.append({
                "game": f"{name}#{seed}", "script": name, "seed": seed,
                "loop": L, "day": r.get("day"), "seat": r.get("seat"),
                "card": r.get("card"), "target": tgt, "ability": ab,
                "kind": r.get("能力の種別"),
                "need": r.get("残り必要ハート"), "hearts": H,
                "last_day": r.get("最終日"),
                "plus2_left": r.get("友好+2の残り枚数"),
                "pred_unlock": r.get("予測解禁日(_unlock)"),
                "rule_earliest": r.get("規則上の最早解禁日"),
                "actual_unlock": act,
                "existing_noop": r.get("既存の空振りゲート"),
                "gw_keep": r.get("TTガード対象(gw_keep)"),
                "existing_noop_no_ttkeep": r.get("既存の空振りゲート(TT除外を外した場合)"),
                "used_in_loop": bool(hit),
                "class": "c" if (act is not None and hit)
                         else ("b" if act is not None else "a"),
                "loop_result": lr.get(L, ""),
            })
    apply_cfg178("off")
    return rows


def count(days: int = 3, loops: int = 8, cfg: str = "off",
          start: int = 0, end: int | None = None) -> dict:
    rows = collect(days, loops, cfg, start, end)
    by_need: dict = {}
    tot = Counter()
    for r in rows:
        b = _bucket(r["need"])
        d = by_need.setdefault(b, Counter())
        for c in (d, tot):
            c["投資席"] += 1
            c[{"a": "(a) 同ループ中に未解禁",
               "b": "(b) 解禁されたが未使用",
               "c": "(c) 解禁され使われた"}[r["class"]]] += 1
            if r["rule_earliest"] is None:
                c["規則上到達不能（最終日までに H へ届かない）"] += 1
            if r["loop_result"] and "敗北" in str(r["loop_result"]):
                c["敗北ループの席"] += 1
                if r["class"] == "a":
                    c["敗北ループ かつ (a)"] += 1
            if r["kind"] in ("軽量情報開示", "重量情報開示", "情報回収"):
                c["内数：情報収集投資"] += 1
    # ★`_unlock` の予測が当たっているか（実解禁日との突き合わせ）
    pr = Counter()
    for r in rows:
        p, a = r["pred_unlock"], r["actual_unlock"]
        if p is None:
            continue
        if a is None:
            pr["実解禁なし（うち 予測>最終日＝規則上その日には無理）"
               if p > (r["last_day"] or 0) else "実解禁なし（予測日は最終日以内）"] += 1
            continue
        pr["予測＝実際" if p == a else
           ("予測が早い（＝投資を続けなかった）" if p < a
            else "★予測が遅い（＝予測が悲観的＝要調査）")] += 1
    return {
        "cfg": cfg, "days": days,
        "n_games": len({r["game"] for r in rows}),
        "n_script_families": len({r["script"] for r in rows}),
        "合計": dict(tot),
        "need別": {k: dict(v) for k, v in sorted(
            by_need.items(), key=lambda x: (x[0] == "4以上", x[0] == "?", x[0]))},
        "_unlock の予測 vs 実解禁日": dict(pr),
    }


# ---------------------------------------------------------------------------
# ab＝版ごとの成績と**局単位の diff の全数**（★`off` を毎回その場で実測する）
#   ★`_CLASS_DEFAULTS178` は**import 時のクラス既定**を捕まえる。本レーンでは
#     `B178_INVEST_REACHABLE_ONLY` の既定が False（＝OFF）なので `off` は origin/main と同値。
#     ★既定を True にした枝で回すと `off` が OFF にならない（B-174b の申し送り）。
# ---------------------------------------------------------------------------
_KEYS178 = ("B178_INVEST_REACHABLE_ONLY", "B178_KEEP_WHEN_NONE_REACHABLE",
            "B178_UNREACHABLE_VALUE_COEFF")
_CLASS_DEFAULTS178 = {k: getattr(HeuristicProtagonist, k) for k in _KEYS178}
CONFIGS178: dict[str, dict] = {
    "off": {},
    #: 版 `on`＝届かない能力を落とす（キャラごと `_invest` から消えることもある）
    "on": {"B178_INVEST_REACHABLE_ONLY": True},
    #: 版 `on2`＝**狭い版**＝「1つも届かない」キャラは従来どおり＝並べ替えだけ
    "on2": {"B178_INVEST_REACHABLE_ONLY": True,
            "B178_KEEP_WHEN_NONE_REACHABLE": True},
    #: 版 `c*`＝**軟らかい版**＝落とさず値に係数を掛ける（掃引の目盛り）
    "c00": {"B178_UNREACHABLE_VALUE_COEFF": 0.0},
    "c25": {"B178_UNREACHABLE_VALUE_COEFF": 0.25},
    "c50": {"B178_UNREACHABLE_VALUE_COEFF": 0.5},
    "c75": {"B178_UNREACHABLE_VALUE_COEFF": 0.75},
    #: 対照＝係数1.0＝挙動 bit 不変のはず（★掃引の自己検査）
    "c100": {"B178_UNREACHABLE_VALUE_COEFF": 1.0},
}


def apply_cfg178(cfg: str) -> None:
    """b174 の切替口をクラス既定へ戻したうえで、B-178 の切替口を立てる。"""
    apply_cfg("off")                      # ＝b174 側の7キーをクラス既定へ
    for k, v in _CLASS_DEFAULTS178.items():
        setattr(HeuristicProtagonist, k, v)
    for k, v in CONFIGS178[cfg].items():
        setattr(HeuristicProtagonist, k, v)


def switches178() -> str:
    return json.dumps({**json.loads(switches()),
                       **{k: getattr(HeuristicProtagonist, k) for k in _KEYS178}},
                      ensure_ascii=False)


def ab(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
       configs=("on",)) -> dict:
    from arena.b174_audit import _agg, _one
    from arena.benchmark import benchmark_scripts

    games = list(benchmark_scripts(days=days))[start:end]

    def _run(cfg: str) -> dict:
        apply_cfg178(cfg)
        print(f"  [切替口:{cfg}] {switches178()} / days={days}", flush=True)
        return {f"{n}#{s}": _one(sc, s, loops) for n, s, sc in games}

    def _by_script(res):
        d: Counter = Counter()
        for g, (o, _n, _t) in res.items():
            d[g.split("#")[0] + "/局数"] += 1
            d[g.split("#")[0] + "/防衛数"] += int(o == "defense")
        return dict(d)

    base = _run("off")
    out = {"days": days, "n_games": len(games), "off": _agg(base, loops),
           "off_by_script": _by_script(base), "versions": {}}
    for cfg in configs:
        res = _run(cfg)
        flips, moved = [], 0
        for g in base:
            bo, bn, bt = base[g]
            vo, vn, vt = res[g]
            moved += int(bt != vt)
            if (bo, bn) != (vo, vn):
                flips.append({"game": g, "off": f"{bo}:{bn}", cfg: f"{vo}:{vn}",
                              "向き": ("改善" if (vo == "defense" and
                                                  (bo != "defense" or vn < bn))
                                       else "退行")})
        out["versions"][cfg] = {"agg": _agg(res, loops), "moved": moved,
                                "flip数": len(flips), "flips": flips,
                                "by_script": _by_script(res)}
    apply_cfg178("off")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="B-178 解禁が間に合わない投資の計測")
    ap.add_argument("cmd", choices=["verify", "count", "rows", "ab"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--cfg", default="off")
    ap.add_argument("--games", default="")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end,
                     only=tuple(x for x in a.games.split(",") if x))
    elif a.cmd == "rows":
        res = collect(a.days, a.loops, a.cfg, a.start, a.end)
    elif a.cmd == "ab":
        res = ab(a.days, a.loops, a.start, a.end,
                 configs=tuple(x for x in (a.cfg or "on").split(",") if x))
    else:
        res = count(a.days, a.loops, a.cfg, a.start, a.end)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
