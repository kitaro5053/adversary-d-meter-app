# -*- coding: utf-8 -*-
"""B-276 フェーズ0：opponent model＝「置ける」でなく「置く」を読む（★測定と設計案のみ・実装しない）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-94（B-276）。
出典＝§72-93（診断の完結）／§72-88（B-269 の負の結果＝偽陽性16件はすべて
「予測した供給が来なかった」）／`docs/測定_B269_下限Lの射程_負の結果_2026-08-20.md`。

## ★事前登録（測る前に固定した判定基準。最初のコミットに含める）

**フェーズ1（実装）推奨とする条件＝以下の 1 と 2 を同一の推定器が同時に満たすこと**：

1. **教材（人間の脚本家）の致死日供給の予測**＝教材棋譜のうち人間が脚本家の局の
   「今日が致死日 ∧ 対象がその日の犯人候補」の行で、推定器の予測が
   **ベースライン2種の両方**（B0＝常に置く p=1／B1＝AI コーパス全行実績率の定数）に対し
   **較正（Brier）と判別（AUC）の両方で改善**する。
   ★AUC は定数ベースラインでは 0.5＝「AUC > 0.5 かつ Brier が B0・B1 の双方より小さい」を要求。
   ★人間側の n が小さく差が言い切れない場合は「判定保留」とし、必要局数の見積りを添える
   （無理に合格・不合格にしない）。
2. **B-269 の偽陽性16件の過半（≥9）が「置かない」側に倒れる**＝同じ推定器の確率を
   τ=0.5 で2値化して供給予測を差し替えたとき、`L_pred = u + (供給予測×2値) − 冷却上限` が
   臨界未満になる。★かつ**真陽性7件（事件が実際に起きた席）の過半（≥4）は「置く」側に残る**
   （偽陽性だけでなく真陽性まで全部倒す推定器は「常に置かない」の亜種＝合格にしない。
   この第2文はチケット例示への本レーンの精緻化）。

**負の結果とする条件**＝1 または 2 のどちらかが（判定保留でなく）不成立。
負の結果でもフェーズ0の実測（事後分布・ギャップ・較正表）は成果として doc に残す。

## 定義（★すべて公開情報のみ。「置ける」は B-262/B-269 と同じ材料＝単一ソースを import）

| 記号 | 定義 | 出典 |
|---|---|---|
| **置ける（札）** | `habit`（その対象への `不安+1` の公開実績）∧ `fd`（今日その対象に脚本家の伏せ札） | `arena.b269_audit.b262_supply_likely`（B-262 の述語） |
| **置ける（能力）** | ミスリーダーの台（役職周辺確率>0）が「対象自身 ∪ 対象と同室の生存者」に収まる | `arena.b269_audit.misleader_supply_certain` |
| **置いた（札）** | その日の `cards_revealed` に owner=mastermind の `不安+1`→対象がある | 公開履歴 |
| **置いた（能力）** | その日の `unrest` イベント（phase=mastermind_ability・delta>0）の対象 | 公開履歴 |
| 致死日 | `殺人事件`／`遠隔殺人`／`病院の事件` の予定日（公開シート） | `agents.heuristic_protagonist._recompute` の `_lethal_days` と同じ名前集合（本道具はビューから鏡写しに再計算し、コーパス実測では agent の実物と毎席突き合わせて食い違いを数える） |
| dist | 対象が犯人候補に入っている最寄りの致死日までの日数（候補でなければ None） | `belief.culprit_candidates()` |

★「置いた（札）」は**脚本家の選択**を数える（`不安禁止` による打ち消しは数えない＝
主人公手札に `不安禁止` は無く、脚本家が自札を打ち消す形も無いので実務上は同値）。
★「置ける」は**予測の材料**であって真の可能集合ではない（例＝初日は habit が立たない）。
∴ 分母は「対象が生存している (局,L,D,対象) 行」全体で取り、「置ける」は行の特徴量として持つ
＝P(置いた|置ける) と P(置いた|¬置ける) の両側を測れる形にする。

## 推定器の設計案（★実装しない＝オフライン評価のみ。すべて既存材料＝公開情報から）

- **E1 局内実績率**＝この局でこの対象に「置いた」日数の運転中頻度（Laplace 1,1）。
  `p = (1 + 過去に置いた日数) / (2 + 過去の生存日数)`。★「この相手の癖」だけを読む
  （牡丹の人間脚本家＝毎日置く→p が速く 1 に寄る／AI 脚本家＝置かない→0 に寄る）。
- **E2 状況セル表**＝セル (fd, habit, 置ける(能力), dist バケット) ごとの実績率を
  AI コーパスで較正（Laplace 1,2）。コーパス自身の評価は LOSO（脚本名 leave-one-out）、
  教材への適用は**全コーパス表のまま転移**（＝転移が効くかの検査）。
- **E3 合成**＝E2 のセル率を事前分布（擬似観測 m=4）に、E1 の局内証拠でベイズ更新：
  `p = (m·p_cell + 過去に置いた日数) / (m + 過去の生存日数)`。
  ★これが「opponent model」の最小形＝集団事前＋この相手の公開実績。
- ベースライン **B0**＝常に置く（p=1・現行 B-262 上限の振る舞い）／
  **B1**＝AI コーパス全行実績率の定数。

## 挙動には触れない

`agents/` `sim/` `engine/` `rules/` を1バイトも変更しない。コーパスの観測は
`agents.heuristic_protagonist.B100_HOOK`（計測専用・戻り値不使用）だけ。教材は読むだけ
（再生もしない＝棋譜の決定行と history をそのまま数える）。
`verify` でプローブの無害性（棋譜 bit 一致）を実測する。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b276_audit corpus --days 3 --pick all --outdir /tmp/b276/d3
    python -m arena.b276_audit teach  --outdir /tmp/b276/teach
    python -m arena.b276_audit verify --days 3 --pick random_BTX:0-2
    python -m arena.b276_audit report --dirs /tmp/b276/d3,/tmp/b276/d5,/tmp/b276/teach
    python -m arena.b276_audit eval   --dirs /tmp/b276/d3,/tmp/b276/d5,/tmp/b276/teach
    python -m arena.b276_audit fp16   --b269 /tmp/b269/d3,/tmp/b269/d5 --dirs /tmp/b276/d3,/tmp/b276/d5
    python -m arena.b276_audit anchor
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from arena.b249_audit import check_no_knob_writes, defaults_banner
from arena.b250_audit import _truth_of, parse_pick
from arena.b269_audit import (  # ★「置ける」の単一ソース（B-262/B-269 と同じ定義）
    b262_supply_likely, misleader_supply_certain,
)

UNREST_PLUS = "不安+1"

#: 致死事件の名前集合（`agents/heuristic_protagonist.py` の `_recompute` が
#: `_lethal_days` に足すのと同じ3種＝内容ベースの鏡写し。行番号は持たない。
#: 食い違いはコーパス実測で agent の実物と毎席突き合わせて数える＝自己検証）。
LETHAL_INCIDENTS = frozenset({"殺人事件", "遠隔殺人", "病院の事件"})

#: 教材ディレクトリ（B-266/B-269 と同じ）。
LOGDIR = Path(__file__).resolve().parent.parent / "docs" / "feedback_logs"

#: 牡丹の教材（検問4＝§72-88 の本丸）。
BOTAN = "牡丹_BTX3d_seed0_殺人計画_2026-08-19.jsonl"

#: 推定器の擬似観測の強さ（E3）と2値化のしきい値（★事前登録＝掃引しない）。
E3_M = 4.0
TAU = 0.5


def knob_baseline_banner() -> str:
    from arena import knob_audit
    knob_audit.check_baseline({}, {}, (), driver="arena.b276_audit")
    return "knob_audit.check_baseline（空の条件表＝退化形）✅"


# ---------------------------------------------------------------------------
# 特徴量（★ビュー＝公開情報のみ）
# ---------------------------------------------------------------------------
def habit_fd(view: dict, who: str) -> tuple[bool, bool]:
    """B-262 の述語の2成分を別々に返す（★連言は `b262_supply_likely` と同値＝テストで固定）。"""
    habit = False
    for e in view.get("history", ()) or ():
        if e.get("event") != "cards_revealed":
            continue
        for p in e.get("placements", ()) or ():
            if (p.get("owner") == "mastermind" and p.get("card") == UNREST_PLUS
                    and p.get("target_kind") == "character" and p.get("target") == who):
                habit = True
    fd = any(p.get("owner") == "mastermind" and p.get("target_kind") == "character"
             and p.get("target") == who for p in view.get("placements", ()) or ())
    return habit, fd


def lethal_days_from_view(view: dict) -> set[int]:
    """公開シートの事件予定から致死日を鏡写しに再計算する（`LETHAL_INCIDENTS` 参照）。"""
    return {int(i["day"]) for i in view.get("incidents", ()) or ()
            if i.get("name") in LETHAL_INCIDENTS and i.get("day") is not None}


def day_rows(view: dict, marg: dict, cands: dict, led: set[int]) -> list[dict]:
    """1日ぶんの (対象ごと) の特徴量行。分母＝生存キャラ全員。"""
    from engine.data import unrest_threshold_of
    day = int(view.get("day"))
    lp = view.get("loop")
    out: list[dict] = []
    for c in view.get("characters", ()) or ():
        if not c.get("alive"):
            continue
        who = c["name"]
        habit, fd = habit_fd(view, who)
        ml_ok, support = misleader_supply_certain(marg, {x["name"]: x for x in view["characters"]}, who)
        cand_days = sorted(d for d in led if d >= day and who in (cands.get(d) or ()))
        dist = (cand_days[0] - day) if cand_days else None
        n_cands = len(cands.get(cand_days[0], ()) or ()) if cand_days else 0
        crit = unrest_threshold_of(who)
        out.append({
            "loop": lp, "day": day, "who": who,
            "u": int(c.get("unrest", 0)), "crit": crit,
            "habit": bool(habit), "fd": bool(fd),
            "can_card": bool(habit and fd), "can_abil": bool(ml_ok),
            "ml_n": len(support), "dist": dist, "n_cands": n_cands,
            "known": bool(n_cands == 1),
        })
    return out


# ---------------------------------------------------------------------------
# 結果（★公開履歴のみ）
# ---------------------------------------------------------------------------
def daily_mm_supply(history, loop, day, who) -> dict:
    """その (L,D) に脚本家が `who` へ供給したか（札・能力を別々に）。"""
    did_card = did_abil = False
    for e in history:
        if e.get("loop") != loop or e.get("day") != day:
            continue
        if e.get("event") == "cards_revealed":
            for p in e.get("placements", ()) or ():
                if (p.get("owner") == "mastermind" and p.get("card") == UNREST_PLUS
                        and p.get("target_kind") == "character" and p.get("target") == who):
                    did_card = True
        elif (e.get("event") == "unrest" and e.get("phase") == "mastermind_ability"
                and e.get("target") == who and int(e.get("delta", 0)) > 0):
            did_abil = True
    return {"did_card": did_card, "did_abil": did_abil,
            "did_any": bool(did_card or did_abil)}


def attach_outcomes(rows: list[dict], history) -> None:
    for r in rows:
        r.update(daily_mm_supply(history, r["loop"], r["day"], r["who"]))


# ---------------------------------------------------------------------------
# コーパス収集（AI 脚本家 vs AI 主人公）
# ---------------------------------------------------------------------------
class Rec:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.c: Counter = Counter()
        self.err: list[str] = []
        self.meta: dict = {}
        self.incidents: list[dict] = []
        self._seen: set = set()

    def to_json(self) -> dict:
        return {"rows": self.rows, "counts": dict(self.c), "err": self.err,
                "meta": self.meta, "incidents": self.incidents}

    @classmethod
    def from_json(cls, d: dict) -> "Rec":
        r = cls()
        r.rows = list(d.get("rows", ()))
        r.c.update(d.get("counts", {}))
        r.err = list(d.get("err", ()))
        r.meta = dict(d.get("meta", {}))
        r.incidents = list(d.get("incidents", ()))
        return r


def _hook_factory(rec: Rec, script_obj):
    def _hook(agent, view, options, best, score):
        try:
            key = (view.get("loop"), view.get("day"))
            if key in rec._seen:            # 1日1回（最初の席のビューで代表させる）
                return
            rec._seen.add(key)
            rec.c["日（採取）"] += 1
            marg = agent._belief.role_marginals()
            cands = getattr(agent, "_culprit_cands", None) or {}
            led = lethal_days_from_view(view)
            # ★自己検証＝鏡写しの致死日が agent の実物と食い違ったら数える
            if led != set(getattr(agent, "_lethal_days", ()) or ()):
                rec.c["★lethal_days の食い違い"] += 1
            rows = day_rows(view, marg, cands, led)
            for r in rows:
                r["true_role"] = _truth_of(script_obj, r["who"])
            rec.rows.extend(rows)
        except Exception as exc:            # フック内例外は呼び出し元が握るので自分で記録
            rec.c["★フック内の例外"] += 1
            rec.err.append(f"{type(exc).__name__}: {exc}")
    return _hook


def _run(script_obj, seed: int, loops: int, rec: Rec | None):
    from agents import HeuristicMastermind, HeuristicProtagonist
    from agents import heuristic_protagonist as hp_mod
    from sim import run_game

    orig = hp_mod.B100_HOOK
    if rec is not None:
        hp_mod.B100_HOOK = _hook_factory(rec, script_obj)
    try:
        agent = HeuristicProtagonist(seed)
        state, _ = run_game(replace(script_obj, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": agent, "p2": agent, "p3": agent})
    finally:
        hp_mod.B100_HOOK = orig
    if rec is not None:
        attach_outcomes(rec.rows, state.history)
        rec.incidents = [{"loop": e.get("loop"), "day": e.get("day"),
                          "name": e.get("name"), "occurs": bool(e.get("occurs"))}
                         for e in state.history if e.get("event") == "incident"]
        rec.meta.update({"winner": state.winner, "loop_no": state.loop_no,
                         "days_per_loop": script_obj.days_per_loop})
    return state


def cmd_corpus(a) -> int:
    before = defaults_banner()
    print(f"[b276] {knob_baseline_banner()}")
    games = parse_pick(a.pick, a.days)
    os.makedirs(a.outdir, exist_ok=True)
    for name, seed, sc in games:
        p = os.path.join(a.outdir, f"{name}__{seed}.json")
        if os.path.exists(p) and not a.force:
            continue
        rec = Rec()
        st = _run(sc, seed, a.loops, rec)
        rec.meta.update({"game": f"{name}#{seed}", "script": name, "seed": seed,
                         "days": a.days, "mm": "ai", "kind": "corpus"})
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec.to_json(), f, ensure_ascii=False)
        did = sum(1 for r in rec.rows if r.get("did_any"))
        print(f"  {name}#{seed}: 行{len(rec.rows)} 供給行{did} "
              f"例外{rec.c['★フック内の例外']} 致死日食い違い{rec.c['★lethal_days の食い違い']} "
              f"winner={st.winner} L{st.loop_no}")
    check_no_knob_writes(before, driver="arena.b276_audit")
    print("[b276] 切替口の健全性検査 ✅（測定前後で1bitも動いていない）")
    return 0


def cmd_verify(a) -> int:
    """★プローブの無害性＝観測あり／なしで棋譜が1ビットも動かないこと。"""
    bad = 0
    for name, seed, sc in parse_pick(a.pick, a.days):
        s0 = _run(sc, seed, a.loops, None)
        s1 = _run(sc, seed, a.loops, Rec())
        h0 = json.dumps(s0.history, ensure_ascii=False, sort_keys=True)
        h1 = json.dumps(s1.history, ensure_ascii=False, sort_keys=True)
        ok = (h0 == h1 and s0.winner == s1.winner and s0.loop_no == s1.loop_no)
        print(f"  {name}#{seed}: 棋譜一致={ok}")
        bad += 0 if ok else 1
    print(f"[b276] プローブの無害性: 不一致 {bad} 件")
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# 教材収集（人間の脚本家＝ai_side が protagonist の教材。読むだけ・再生しない）
# ---------------------------------------------------------------------------
def collect_log(path: Path) -> Rec | None:
    from arena.b251_audit import belief_after, load_log
    from arena.era_table import ai_side
    meta, ds = load_log(path)
    views: dict = {}
    for d in ds:
        if d.get("decision") == "set_card" and d.get("actor") != "mastermind":
            views.setdefault((d["loop"], d["day"]), d["view"])
    if not views:
        return None
    rec = Rec()
    side = ai_side(path.name)                    # 単一ソース（era 表と同じ推定）
    rec.meta.update({"game": path.name, "script": path.name, "seed": None,
                     "days": None, "kind": "teach",
                     "mm": "human" if side == "protagonist" else "ai"})
    for (lp, dy), v in sorted(views.items()):
        try:
            b = belief_after(v, [])
            marg = b.role_marginals()
            cands = b.culprit_candidates()
            led = lethal_days_from_view(v)
            rows = day_rows(v, marg, cands, led)
            for r in rows:
                r["true_role"] = "?"             # 教材＝神視点は数えない
            rec.rows.extend(rows)
            rec.c["日（採取）"] += 1
            if rec.meta.get("days") is None:
                rec.meta["days"] = v.get("days_per_loop")
        except Exception as exc:                  # noqa: BLE001  部分結果を残す
            rec.c["★採取の例外"] += 1
            rec.err.append(f"L{lp}D{dy}: {type(exc).__name__}: {exc}")
    attach_outcomes(rec.rows, meta.get("history", ()))
    rec.incidents = [{"loop": e.get("loop"), "day": e.get("day"),
                      "name": e.get("name"), "occurs": bool(e.get("occurs"))}
                     for e in meta.get("history", ()) if e.get("event") == "incident"]
    rec.meta.update({"winner": meta.get("winner"), "loop_no": meta.get("loops_played")})
    return rec


def cmd_teach(a) -> int:
    os.makedirs(a.outdir, exist_ok=True)
    logs = sorted(LOGDIR.glob("*.jsonl")) if not a.logs else \
        [LOGDIR / x.strip() for x in a.logs.split(",")]
    for p in logs:
        out = os.path.join(a.outdir, p.name.replace(".jsonl", ".json"))
        if os.path.exists(out) and not a.force:
            continue
        rec = collect_log(p)
        if rec is None:
            print(f"  {p.name}: 主人公の決定が無い＝スキップ")
            continue
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rec.to_json(), f, ensure_ascii=False)
        did = sum(1 for r in rec.rows if r.get("did_any"))
        print(f"  {p.name}: mm={rec.meta['mm']} 行{len(rec.rows)} 供給行{did} "
              f"例外{rec.c['★採取の例外']}")
    return 0


# ---------------------------------------------------------------------------
# 推定器（★オフライン評価のみ＝実装しない）
# ---------------------------------------------------------------------------
def dist_bucket(d) -> str:
    if d is None:
        return "none"
    return "0" if d == 0 else ("1" if d == 1 else "2+")


def cell_of(r: dict) -> tuple:
    return (bool(r["fd"]), bool(r["habit"]), bool(r["can_abil"]), dist_bucket(r["dist"]))


def fit_cells(rows: list[dict]) -> dict:
    t: dict = defaultdict(lambda: [0, 0])
    for r in rows:
        c = t[cell_of(r)]
        c[0] += 1 if r["did_any"] else 0
        c[1] += 1
    return dict(t)


def cell_p(table: dict, cell: tuple) -> float:
    did, n = table.get(cell, (0, 0))
    if n == 0:                                  # セルが空＝表全体の率へフォールバック
        did = sum(v[0] for v in table.values())
        n = sum(v[1] for v in table.values())
    return (did + 1.0) / (n + 2.0)


def predict_game(rows: list[dict], cells: dict, b1: float) -> None:
    """1局ぶんの行（時系列順）に E1/E2/E3/B0/B1 の予測を付ける（★行を書き足すだけ）。

    ★運転中の証拠（past_did/past_n）は **その行より前の日** の同一対象の行だけを使う
    ＝リーク無し（当日・未来の結果を見ない）。
    """
    past: dict = defaultdict(lambda: [0, 0])    # who -> [past_did, past_n]
    order = sorted(range(len(rows)), key=lambda i: (rows[i]["loop"], rows[i]["day"]))
    prev_key = None
    pending: list[int] = []
    def _flush():
        for j in pending:
            r = rows[j]
            pd, pn = past[r["who"]]
            p2 = cell_p(cells, cell_of(r))
            r["p_e1"] = (1.0 + pd) / (2.0 + pn)
            r["p_e2"] = p2
            r["p_e3"] = (E3_M * p2 + pd) / (E3_M + pn)
            r["p_b0"] = 1.0
            r["p_b1"] = b1
        for j in pending:
            r = rows[j]
            past[r["who"]][0] += 1 if r["did_any"] else 0
            past[r["who"]][1] += 1
        pending.clear()
    for i in order:
        key = (rows[i]["loop"], rows[i]["day"])
        if key != prev_key:
            _flush()
            prev_key = key
        pending.append(i)
    _flush()


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------
def brier(ps: list[float], ys: list[int]) -> float | None:
    if not ps:
        return None
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def auc(ps: list[float], ys: list[int]) -> float | None:
    """Mann–Whitney（同点は0.5）。片方のクラスが無ければ None。"""
    pos = [p for p, y in zip(ps, ys) if y]
    neg = [p for p, y in zip(ps, ys) if not y]
    if not pos or not neg:
        return None
    wins = ties = 0
    for pp in pos:
        for nn in neg:
            if pp > nn:
                wins += 1
            elif pp == nn:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def calib(ps: list[float], ys: list[int], edges=(0.2, 0.4, 0.6, 0.8)) -> list[dict]:
    bins: list[dict] = [{"lo": lo, "hi": hi, "n": 0, "p_sum": 0.0, "y_sum": 0}
                        for lo, hi in zip((0.0,) + tuple(edges), tuple(edges) + (1.0001,))]
    for p, y in zip(ps, ys):
        for b in bins:
            if b["lo"] <= p < b["hi"]:
                b["n"] += 1
                b["p_sum"] += p
                b["y_sum"] += y
                break
    return [{"bin": f'[{b["lo"]:.1f},{b["hi"] if b["hi"] <= 1 else 1.0:.1f})',
             "n": b["n"],
             "p_mean": round(b["p_sum"] / b["n"], 3) if b["n"] else None,
             "y_rate": round(b["y_sum"] / b["n"], 3) if b["n"] else None}
            for b in bins]


# ---------------------------------------------------------------------------
# 集計（report＝(a)(b)／eval＝(c)／fp16＝(d) の一部）
# ---------------------------------------------------------------------------
def _load_dir(d: str) -> list[Rec]:
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                out.append(Rec.from_json(json.load(f)))
    return out


def _load_dirs(spec: str) -> list[Rec]:
    recs: list[Rec] = []
    for d in spec.split(","):
        if d.strip():
            recs.extend(_load_dir(d.strip()))
    return recs


def _pct(x, y) -> str:
    return f"{100.0 * x / y:.1f}%({x}/{y})" if y else "—"


def _flat(recs: list[Rec]) -> list[dict]:
    rows = []
    for rec in recs:
        for r in rec.rows:
            q = dict(r)
            q["game"] = rec.meta.get("game")
            q["mm"] = rec.meta.get("mm")
            q["script"] = rec.meta.get("script")
            q["days"] = rec.meta.get("days")
            rows.append(q)
    return rows


def _post_table(rows: list[dict], keyf, label: str) -> None:
    print(f"\n  [{label}]  P(置いた|置ける) ／ P(置いた|¬置ける) ／ P(置いた)")
    groups: dict = defaultdict(list)
    for r in rows:
        groups[keyf(r)].append(r)
    for k in sorted(groups, key=str):
        g = groups[k]
        can = [r for r in g if r["can_card"] or r["can_abil"]]
        no = [r for r in g if not (r["can_card"] or r["can_abil"])]
        d_can = sum(1 for r in can if r["did_any"])
        d_no = sum(1 for r in no if r["did_any"])
        d_all = d_can + d_no
        print(f'    {str(k):24s} 置ける行 {_pct(d_can, len(can)):>16s} '
              f'／ ¬置ける行 {_pct(d_no, len(no)):>18s} ／ 全行 {_pct(d_all, len(g)):>18s}')


def cmd_report(a) -> int:
    recs = _load_dirs(a.dirs)
    rows = _flat(recs)
    print(f"== b276 report ==  局/教材 {len(recs)} ／ 行 {len(rows)}")
    exc = sum(rec.c.get("★フック内の例外", 0) + rec.c.get("★採取の例外", 0) for rec in recs)
    mis = sum(rec.c.get("★lethal_days の食い違い", 0) for rec in recs)
    print(f"[自己検証] 例外 {exc} 件 ／ 致死日の鏡写しと agent 実物の食い違い {mis} 件")
    _post_table(rows, lambda r: r["mm"], "(a) 脚本家の種別（AI/human）")
    _post_table(rows, lambda r: (r["mm"], dist_bucket(r["dist"])), "(a) 種別×致死日までの距離")
    _post_table(rows, lambda r: (r["mm"], bool(r["fd"])), "(a) 種別×伏せ札の有無")
    _post_table(rows, lambda r: (r["mm"], "確定" if r["known"] else
                                 (f"候補{min(r['n_cands'], 3)}+" if r["n_cands"] else "候補外")),
                "(a) 種別×役職の割れ具合（候補集合）")
    # ---- チャネル別 -------------------------------------------------------
    print("\n  [(a) チャネル別]  置ける→置いた")
    for mm in ("ai", "human"):
        g = [r for r in rows if r["mm"] == mm]
        cc = [r for r in g if r["can_card"]]
        ca = [r for r in g if r["can_abil"]]
        print(f'    mm={mm:5s} 札: {_pct(sum(r["did_card"] for r in cc), len(cc)):>16s}'
              f'  能力: {_pct(sum(r["did_abil"] for r in ca), len(ca)):>16s}'
              f'  ★逆向き P(置ける|置いた)='
              f'{_pct(sum(1 for r in g if r["did_any"] and (r["can_card"] or r["can_abil"])), sum(1 for r in g if r["did_any"]))}')
    # ---- (b) ギャップの大きいセル ----------------------------------------
    print("\n  [(b) セル別ギャップ]  (fd,habit,能力,dist) ごとの P(置いた)＝「置ける」との差")
    for mm in ("ai", "human"):
        g = [r for r in rows if r["mm"] == mm]
        t = fit_cells(g)
        print(f"    mm={mm}（セル: did/n）")
        for cell in sorted(t, key=str):
            did, n = t[cell]
            if n >= 5:
                print(f'      fd={cell[0]!s:5s} habit={cell[1]!s:5s} 能力={cell[2]!s:5s} '
                      f'dist={cell[3]:4s}: {_pct(did, n)}')
    # ---- 教材ごとの一覧 --------------------------------------------------
    print("\n  [教材/脚本ごと]  致死日の犯人候補行（dist=0）の 置ける→置いた")
    per: dict = defaultdict(list)
    for r in rows:
        if r["dist"] == 0:
            per[(r["mm"], r["script"])].append(r)
    for k in sorted(per, key=str):
        g = per[k]
        can = [r for r in g if r["can_card"] or r["can_abil"]]
        print(f'    {k[0]:5s} {str(k[1])[:44]:44s} 候補行{len(g):4d} '
              f'置ける{len(can):3d} → 置いた {_pct(sum(r["did_any"] for r in can), len(can))}'
              f' ／ 全候補行の供給率 {_pct(sum(r["did_any"] for r in g), len(g))}')
    return 0


def _eval_slices(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    return [
        ("全行", rows),
        ("置ける行（can_card∨can_abil）", [r for r in rows if r["can_card"] or r["can_abil"]]),
        ("致死日の犯人候補行（dist=0）", [r for r in rows if r["dist"] == 0]),
    ]


def _eval_block(label: str, rows: list[dict]) -> None:
    print(f"\n  == {label} ==")
    for sl, g in _eval_slices(rows):
        ys = [1 if r["did_any"] else 0 for r in g]
        if not g:
            print(f"    [{sl}] 行0＝未測")
            continue
        line = [f"[{sl}] n={len(g)} 実率={sum(ys) / len(ys):.3f}"]
        for est in ("p_b0", "p_b1", "p_e1", "p_e2", "p_e3"):
            ps = [r[est] for r in g]
            b = brier(ps, ys)
            u = auc(ps, ys)
            line.append(f"{est[2:]}: Brier={b:.3f} AUC={'—' if u is None else f'{u:.3f}'}")
        print("    " + "  ".join(line))
    # 較正表（E3・致死日の犯人候補行）
    g = [r for r in rows if r["dist"] == 0]
    if g:
        print("    較正（E3・dist=0 行）:", calib([r["p_e3"] for r in g],
                                                  [1 if r["did_any"] else 0 for r in g]))


def _predict_all(recs: list[Rec], cells_by_game: dict, b1: float) -> list[dict]:
    out = []
    for rec in recs:
        rows = [dict(r) for r in rec.rows]
        predict_game(rows, cells_by_game[rec.meta.get("game")], b1)
        for r in rows:
            r["game"] = rec.meta.get("game")
            r["mm"] = rec.meta.get("mm")
            r["script"] = rec.meta.get("script")
            r["days"] = rec.meta.get("days")
        out.extend(rows)
    return out


def eval_predictions(corpus: list[Rec], teach: list[Rec]) -> dict:
    """E1〜E3／B0〜B1 の予測を全行に付けて返す（★較正はコーパスのみ・LOSO）。"""
    corpus_rows = _flat(corpus)
    b1 = (sum(1 for r in corpus_rows if r["did_any"]) / len(corpus_rows)) if corpus_rows else 0.5
    by_script: dict = defaultdict(list)
    for rec in corpus:
        by_script[rec.meta.get("script")].extend(rec.rows)
    full = fit_cells(corpus_rows)
    cells_by_game: dict = {}
    for rec in corpus:                          # LOSO＝同じ脚本名を抜いて較正
        rest = [r for s, rs in by_script.items() if s != rec.meta.get("script") for r in rs]
        cells_by_game[rec.meta.get("game")] = fit_cells(rest)
    for rec in teach:                           # 教材＝全コーパス表のまま転移
        cells_by_game[rec.meta.get("game")] = full
    return {"corpus": _predict_all(corpus, cells_by_game, b1),
            "teach": _predict_all(teach, cells_by_game, b1),
            "b1": b1, "cells_full": full, "cells_by_game": cells_by_game}


def cmd_eval(a) -> int:
    recs = _load_dirs(a.dirs)
    corpus = [r for r in recs if r.meta.get("kind") == "corpus"]
    teach = [r for r in recs if r.meta.get("kind") == "teach"]
    ev = eval_predictions(corpus, teach)
    print(f"== b276 eval ==  コーパス {len(corpus)} 局 ／ 教材 {len(teach)} 本 ／ "
          f"B1（コーパス全行実績率の定数）= {ev['b1']:.4f}")
    _eval_block("AI コーパス（LOSO 較正）", ev["corpus"])
    _eval_block("教材・人間の脚本家（コーパス表を転移）",
                [r for r in ev["teach"] if r["mm"] == "human"])
    _eval_block("教材・AI 脚本家（人間主人公戦・対照）",
                [r for r in ev["teach"] if r["mm"] == "ai"])
    botan = [r for r in ev["teach"] if r["game"] == BOTAN]
    _eval_block("★検問4＝牡丹の教材（人間の供給を予測できるか）", botan)
    if botan:
        print("\n  [牡丹の時系列（犯人＝男子学生の行）]")
        for r in sorted((x for x in botan if x["who"] == "男子学生"),
                        key=lambda x: (x["loop"], x["day"])):
            print(f'    L{r["loop"]}D{r["day"]} dist={r["dist"]} 置ける=札{int(r["can_card"])}/'
                  f'能力{int(r["can_abil"])} 置いた={int(r["did_any"])} '
                  f'E1={r["p_e1"]:.2f} E2={r["p_e2"]:.2f} E3={r["p_e3"]:.2f}')
    return 0


def cmd_fp16(a) -> int:
    """★B-269 の偽陽性16件（＋真陽性7件）を推定器の確率で再裁定する（事前登録の判定2）。

    材料＝`arena.b269_audit seats` の outdir（cool 行の fire=True が「止める価値のある席」。
    その (局,L,D) の事件が不発なら偽陽性）。★b269 と同一条件（PYTHONHASHSEED=0・同 seed）で
    採ったコーパス行と (script,seed,loop,day,who) で突き合わせる。
    """
    from arena.b269_audit import Rec as Rec269
    r269: list = []
    for d in a.b269.split(","):
        for fn in sorted(os.listdir(d.strip())):
            if fn.endswith(".json"):
                with open(os.path.join(d.strip(), fn), encoding="utf-8") as f:
                    r269.append(Rec269.from_json(json.load(f)))
    # ★キーには必ず days（3日級/5日級）を含める＝同じ (script,seed,L,D) が両コーパスに
    #   実在して衝突する（B-269 doc §A3-3 が名指しした重なり＝初版はここを取り違えて
    #   FP=15/TP=8 と数えた。正しくは 16/7）。
    occ: dict = {}
    for rec in r269:
        k0 = (rec.meta.get("days"), rec.meta.get("script"), rec.meta.get("seed"))
        for i in rec.incidents:
            k = (k0, i["loop"], i["day"])
            occ[k] = occ.get(k, False) or i["occurs"]
    waste = [(rec.meta.get("days"), r) for rec in r269 for r in rec.cool if r.get("fire")]
    recs = _load_dirs(a.dirs)
    corpus = [r for r in recs if r.meta.get("kind") == "corpus"]
    ev = eval_predictions(corpus, [])
    idx = {(r["days"], r["game"], r["loop"], r["day"], r["who"]): r
           for r in ev["corpus"]}
    n_fp = n_tp = 0
    flip: Counter = Counter()
    print("== b276 fp16 ==  b269「止める価値のある席」＝", len(waste))
    print("  席 ／ FP(事件不発)? ／ L・臨界 ／ E1/E2/E3 ／ τ=0.5 の再裁定（p<τ＝置かない側）")
    for dys, w in sorted(waste, key=lambda x: (x[0], x[1]["script"], x[1]["seed"],
                                               x[1]["loop"], x[1]["day"])):
        k = ((dys, w["script"], w["seed"]), w["loop"], w["day"])
        fp = not occ.get(k, False)
        n_fp += 1 if fp else 0
        n_tp += 0 if fp else 1
        key = (dys, f'{w["script"]}#{w["seed"]}', w["loop"], w["day"], w["who"])
        row = idx.get(key)
        if row is None:
            flip["★突き合わせ不能（要調査）"] += 1
            print(f'  {dys}日級 {w["script"]}#{w["seed"]} L{w["loop"]}D{w["day"]} {w["who"]}: ★行が無い')
            continue
        line = (f'  {dys}日級 {w["script"]}#{w["seed"]} L{w["loop"]}D{w["day"]} {w["who"]:8s} '
                f'{"FP" if fp else "TP"} L={w["L"]}≥{w["crit"]} ')
        for est in ("p_e1", "p_e2", "p_e3"):
            p = row[est]
            drop = p < TAU
            lab = est[2:]
            line += f'{lab}={p:.2f}{"↓" if drop else "↑"} '
            if fp and drop:
                flip[f"FP を置かない側に倒した_{lab}"] += 1
            if (not fp) and (not drop):
                flip[f"TP を置く側に残した_{lab}"] += 1
        # ★B-269 の「予測した供給が来なかった」を席ごとに実測する（予測チャネル vs 実チャネル）
        pred = f'予測(札{w.get("supply_card", "?")}能力{w.get("supply_ability", "?")})'
        act = f'実際(札{int(row["did_card"])}能力{int(row["did_abil"])})'
        if fp:
            flip["FP のうち実供給が1つも来なかった席" if not row["did_any"]
                 else "★FP なのに実供給が来ていた席（＝原因は供給の不在ではない）"] += 1
        print(line + f'（{pred}→{act}）')
    print(f"\n  分母: FP={n_fp} ／ TP={n_tp}")
    for k in sorted(flip):
        print(f"    {k}: {flip[k]}")
    print(f"  ★事前登録の判定2＝FP の過半(≥{(n_fp // 2) + 1}) を倒し、"
          f"かつ TP の過半(≥{(n_tp // 2) + 1}) を残す推定器があるか。")
    return 0


def cmd_anchor(a) -> int:
    """★錨＝牡丹の教材の供給抽出が検死 doc §1-4 の台帳と一致するか（目視＋テストで固定）。"""
    from arena.b251_audit import load_log
    meta, _ds = load_log(LOGDIR / BOTAN)
    print("牡丹＝犯人 男子学生。検死 doc の台帳＝L1D1/L1D2/L5D1/L5D2/L6D1/L6D2 で札+能力、"
          "L3D1 は能力のみ・L3D2 は札+能力、L2/L4 は無し")
    for lp in range(1, 7):
        for dy in (1, 2, 3):
            s = daily_mm_supply(meta["history"], lp, dy, "男子学生")
            if s["did_card"] or s["did_abil"]:
                print(f'  L{lp}D{dy} 札={int(s["did_card"])} 能力={int(s["did_abil"])}')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B-276 フェーズ0（計測のみ・実装しない）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nm in ("corpus", "verify"):
        p = sub.add_parser(nm)
        p.add_argument("--days", type=int, default=3)
        p.add_argument("--pick", default="all")
        p.add_argument("--loops", type=int, default=8)
        if nm == "corpus":
            p.add_argument("--outdir", required=True)
            p.add_argument("--force", action="store_true")
    pt = sub.add_parser("teach")
    pt.add_argument("--outdir", required=True)
    pt.add_argument("--logs", default=None)
    pt.add_argument("--force", action="store_true")
    for nm in ("report", "eval"):
        p = sub.add_parser(nm)
        p.add_argument("--dirs", required=True, help="rows の outdir（カンマ区切り）")
    pf = sub.add_parser("fp16")
    pf.add_argument("--b269", required=True, help="b269_audit seats の outdir（カンマ区切り）")
    pf.add_argument("--dirs", required=True, help="b276 corpus の outdir（カンマ区切り）")
    sub.add_parser("anchor")
    a = ap.parse_args(argv)
    return {"corpus": cmd_corpus, "verify": cmd_verify, "teach": cmd_teach,
            "report": cmd_report, "eval": cmd_eval, "fp16": cmd_fp16,
            "anchor": cmd_anchor}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
