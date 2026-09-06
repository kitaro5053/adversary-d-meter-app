# -*- coding: utf-8 -*-
"""B-180 Phase 1：**事件効果が明かす「犯人のエリア」**の射程を測る（★計測のみ・`agents/`非接触）。

起票＝`docs/バックログ_構想メモ_FableA.md` §56(3)（ユーザー実戦 2026-08-06・`btx5_future`）。
★本モジュールは `agents/` `engine/` `rules/` `sim/` を1行も変更しない（`arena/` のみ）。

------------------------------------------------------------------------------
## 0. 用語（★略語を使う前に、変数が何を指すかを定義する）
------------------------------------------------------------------------------

| 語 | 何を指すか（現物の場所） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回（`decide(view,"set_card",options)` が1つ返すこと）。1日3席 |
| **冷却席（COOL）** | その席で選ばれた手が `不安-1` で、対象がキャラであるもの（＋B-133 の板読み替え＝`不安-1→幻想のいる板`） |
| **残る事件** | その席の時点で `d >= view["day"]` を満たす**公開**事件（`view["incidents"]`＝日と名前のみ公開＝`rules/00_rules_core.md:76`） |
| **`_culprit_cands`** | 辞書＝キー＝事件の日／値＝**公開情報だけ**から作った犯人候補集合（`agents/heuristic_protagonist.py:1050`／中身＝`agents/belief.py:2087 culprit_candidates`） |
| **DEAD 席** | COOL 席のうち、対象が**残る事件のどの犯人候補集合にも入っていない**＝公開情報だけで「この不安は残る事件に一切効かない」と言える席 |
| **DEAD-eff 席** | DEAD のうち、**事件効果チャネルを外すと候補に戻る**席＝除外の功が B-180 の述語にある席（＝本チケットの literal な射程） |
| **ALT** | その席の `options` に「残る事件の犯人候補（生存・臨界あり・不安≥1）への `不安-1`」が実際に存在したか＝**手を変えうるか** |

★**本監査は「正解の配役」を一切参照しない**（運用doc `docs/運用_Opus5でのFableA運用_2026-08-01.md` §3-7）。
使うのは `protagonist_view`・公開履歴（`state.history`）・主人公AI自身が公開情報から作った
`_culprit_cands` / `belief` だけ。脚本の `roles` / `incidents[].culprit` は読まない。

------------------------------------------------------------------------------
## 1. KB 横断表：どの事件効果が「犯人／犯人のエリア」を公開情報として明かすか
------------------------------------------------------------------------------

FS 7種（`rules/40_first_steps.md:146-154`）＋BTX 固有2種（`rules/50_basic_tragedy_x.md:196,218`）
＝**全9種**を条文から分類した（`REVEAL_TABLE`）。★「割れない」と判定した5種も表に残す。

------------------------------------------------------------------------------
## 2. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面・単独実行）
------------------------------------------------------------------------------

    python -m arena.b180_audit table                    # §1 の横断表を印字
    python -m arena.b180_audit verify    --days 3       # プローブ有無で棋譜が一致する物証
    python -m arena.b180_audit incidents --days 3       # (a)(b)＝明かす事件の発生回数と絞り込み量
    python -m arena.b180_audit seats     --days 3       # (c)＝手を変えうる席の層別
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.belief as B
from agents import HeuristicMastermind, HeuristicProtagonist
from agents.belief import Belief
from engine.data import unrest_threshold_of
from sim import run_game

# ---------------------------------------------------------------------------
# §1 KB 横断表（★条文と行番号だけから書く。実装は見ない）
# ---------------------------------------------------------------------------
#: 事件名 → (KB行, 条文要旨, 公開情報として犯人について何が言えるか, 型)
#:   型 "culprit"   ＝犯人その人が割れる
#:   型 "area"      ＝犯人のいるエリア（の顔ぶれ）が割れる
#:   型 None        ＝犯人について何も言えない
REVEAL_TABLE: dict[str, tuple[str, str, str, str | None]] = {
    "殺人事件": (
        "rules/40_first_steps.md:148 ／ rules/50_basic_tragedy_x.md:191",
        "可能ならば犯人と同一のエリアにいる犯人以外の任意のキャラクター1人を死亡させる。",
        "死者の**居たエリア**＝犯人のエリア。∴ 犯人 ∈（死亡時に同席していた顔ぶれ − 死者）。"
        "★不発（何も起きなかった）からは何も言えない＝原因が5つある（rules/60:196 E-7）。",
        "area"),
    "自殺": (
        "rules/40_first_steps.md:150 ／ rules/50_basic_tragedy_x.md:199",
        "犯人は死亡する。",
        "**死んだ当人が犯人**＝犯人その人が割れる（最強）。"
        "★従者の身代わり死は非公開＝従者が絡む観測は使えない。",
        "culprit"),
    "行方不明": (
        "rules/40_first_steps.md:153 ／ rules/50_basic_tragedy_x.md:212",
        "犯人を任意のボードに移動させる。その後、犯人のいるボードに暗躍カウンターを1つ置く。",
        "**移動させられた当人が犯人**。移動先が現在地でも「犯人を動かす」宣言と暗躍の置き場所は"
        "卓上で見える＝**移動後のエリア**も割れる。★移動前のエリアは割れない。",
        "culprit"),
    "蝶の羽ばたき": (
        "rules/50_basic_tragedy_x.md:219（BTX 限定）",
        "友好/不安/暗躍から1種を選び、犯人と同一のエリアにいるキャラクター1人にそれを1つ置く。",
        "対象の**居るエリア**＝犯人のエリア。∴ 犯人 ∈ そのエリアの顔ぶれ（犯人自身を含む）。"
        "★黒猫も犯人になれる（rules/60 A14）＝顔ぶれから黒猫を外さない。",
        "area"),
    "不安拡大": (
        "rules/40_first_steps.md:149 ／ rules/50_basic_tragedy_x.md:195",
        "任意のキャラクター1人に不安+2、別の任意のキャラクター1人に暗躍+1。",
        "対象は「任意のキャラクター」＝犯人の位置にも同一性にも触れない＝**何も言えない**。",
        None),
    "邪気の汚染": (
        "rules/50_basic_tragedy_x.md:197（BTX 限定）",
        "神社に暗躍カウンターを2つ置く。",
        "対象がボード固定＝**何も言えない**。",
        None),
    "病院の事件": (
        "rules/40_first_steps.md:151 ／ rules/50_basic_tragedy_x.md:205",
        "病院に暗躍1以上なら病院の全員が死亡。2以上なら主人公も死亡。",
        "対象は病院＝犯人の位置に触れない＝**何も言えない**。",
        None),
    "遠隔殺人": (
        "rules/40_first_steps.md:152 ／ rules/50_basic_tragedy_x.md:209",
        "暗躍カウンターが2つ以上置かれているキャラクターがいる場合、その中から任意の1人を死亡させる。",
        "対象は暗躍で決まる＝犯人の位置に触れない＝**何も言えない**。",
        None),
    "流布": (
        "rules/40_first_steps.md:154 ／ rules/50_basic_tragedy_x.md:215",
        "任意のキャラクター1人から友好-2、別の任意のキャラクター1人に友好+2。",
        "対象は「任意のキャラクター」＝**何も言えない**。",
        None),
}

#: 犯人／犯人のエリアを明かす事件（＝B-180 の対象）
REVEALING = frozenset(n for n, r in REVEAL_TABLE.items() if r[3] is not None)


def table() -> None:
    print("| 事件 | KB行 | 効果（条文要旨） | 公開情報として割れること | 型 |")
    print("|---|---|---|---|---|")
    for n, (src, txt, says, kind) in REVEAL_TABLE.items():
        print(f"| {n} | `{src}` | {txt} | {says} | {kind or '—（割れない）'} |")


# ---------------------------------------------------------------------------
# §2 事件効果チャネルのアブレーション（★実装の式を書き写さず、入口を塞ぐ）
# ---------------------------------------------------------------------------
#: 事件効果が犯人／犯人のエリアを名指しする経路は belief に3本ある：
#:   (1) `incident_effect` の `present`（蝶の羽ばたき）＝`agents/belief.py:2107,2132-2134`
#:   (2) `_incident_effect_culprit_sets`（mover/victim/same_area＝B-102）＝`:1173-1218,2111,2135-2137`
#:   (3) `_incident_effect_observed_days`（B-108＝効果が出た日の犯人は黒猫ではない）＝`:1135,2114,2138`
#: (3) は「犯人のエリア」ではなく黒猫特性の話なので**アブレーションに含めない**（B-180 の射程外）。
def _cands_without_effect_channel(belief) -> dict:
    """(1)(2) を止めた `culprit_candidates()`＝**B-180 の述語が無かった場合**の候補集合。"""
    hist = [e for e in belief._history
            if not (e.get("event") == "incident_effect"
                    and (e.get("present") or e.get("target")))]
    b2 = Belief(belief.cast, belief.incidents, set_name=belief.set_name)
    orig = B._incident_effect_culprit_sets
    B._incident_effect_culprit_sets = lambda _h: []
    try:
        b2.observe(hist)
        return b2.culprit_candidates()
    finally:
        B._incident_effect_culprit_sets = orig


# ---------------------------------------------------------------------------
# §3 席の観測器
# ---------------------------------------------------------------------------
def _cool_target(hp, view: dict, o: dict) -> str | None:
    """その option が実効的に不安-1 を与えるキャラ（B-133 の板読み替えを含む）。
    ★読み替えの単一ソースは実装側（`heuristic_protagonist._b133_cool_target`）を呼ぶ＝二重定義しない。"""
    if o.get("card") != "不安-1":
        return None
    if o.get("target_kind") == "character":
        return o.get("target")
    return hp._b133_cool_target(view, o)


class _Probe(HeuristicProtagonist):
    """冷却席を数える観測器。`shadow=False` で追加計算を止める（verify の対照）。"""

    def __init__(self, seed: int = 0, shadow: bool = True):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.rows: list = []
        self._shadow_on = shadow
        self._abl_cache: dict = {}

    def _ablated(self, view: dict) -> dict:
        key = (view.get("loop"), view.get("day"), len(view.get("history", ())))
        hit = self._abl_cache.get(key)
        if hit is None:
            hit = _cands_without_effect_channel(self._belief)
            self._abl_cache[key] = hit
        return hit

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision != "set_card" or not self._shadow_on:
            return chosen
        self.c["席（set_card 決定）"] += 1
        tgt = _cool_target(self, view, chosen)
        if tgt is None:
            return chosen
        self.c["COOL 冷却席（不安-1 をキャラに実効投下した席）"] += 1

        day = view["day"]
        alive = {ch["name"]: ch for ch in view["characters"] if ch["alive"]}
        cands = getattr(self, "_culprit_cands", None) or {}
        rem_days = sorted({i["day"] for i in view.get("incidents", ())
                           if i.get("day") is not None and i["day"] >= day})
        if not rem_days:
            self.c["  内数 COOL-残る事件が0（ループ終盤）"] += 1
            return chosen
        union = set().union(*(cands.get(d, set()) for d in rem_days)) if rem_days else set()
        if tgt in union:
            self.c["  内数 COOL-対象は残る事件の犯人候補（＝筋の通った冷却）"] += 1
            return chosen

        # ---- DEAD 席：公開情報だけで「残る事件に効かない」と言える ----
        self.c["★DEAD 対象が残る事件のどの犯人候補にも居ない席"] += 1
        abl = self._ablated(view)
        union_abl = set().union(*(abl.get(d, set()) for d in rem_days))
        by_effect = tgt in union_abl
        if by_effect:
            self.c["★★DEAD-eff うち除外の功が事件効果チャネル（B-180 の述語）にある席"] += 1

        # 手を変えうるか＝その席の options に「生きた犯人候補への不安-1」が在ったか
        alt = sorted({t for o in options
                      if (t := _cool_target(self, view, o)) in union
                      and t in alive and (unrest_threshold_of(t) or 0) > 0
                      and alive[t]["unrest"] >= 1})
        if alt:
            self.c["  内数 DEAD-ALT 同じ席で生きた候補を冷やす選択肢が在った"] += 1
            if by_effect:
                self.c["  内数 DEAD-eff-ALT（★(c) 本体）"] += 1

        # 不安を参照する事件以外のKB経路が残っているか（規約 §7 判例1(a0) と同じ留保）
        rm = self._belief.rule_marginals()
        p_virus = sum(p for (_ry, rxs), p in rm.items() if "妄想拡大ウイルス" in rxs)
        p_ito = sum(p for (_ry, rxs), p in rm.items() if "因果の糸" in rxs)
        p_ml = self._belief.role_marginals().get(tgt, {}).get("メインラバーズ", 0.0)
        clean = (p_virus <= 0.0 and p_ito <= 0.0 and p_ml <= 0.0)
        if clean:
            self.c["  内数 DEAD-CLEAN 不安を参照する他KB経路も全て確率0"] += 1
            if by_effect and alt:
                self.c["  内数 DEAD-eff-ALT-CLEAN（★最も強い主張の席）"] += 1

        self.rows.append({
            "loop": view.get("loop"), "day": day, "target": tgt,
            "unrest": alive.get(tgt, {}).get("unrest"),
            "th": unrest_threshold_of(tgt),
            "rem_days": rem_days,
            "cands": {d: sorted(cands.get(d, ())) for d in rem_days},
            "cands_ablated": {d: sorted(abl.get(d, ())) for d in rem_days},
            "by_effect": by_effect, "alt": alt, "clean": clean,
            "p_virus": round(p_virus, 3), "p_ito": round(p_ito, 3),
            "p_ml_target": round(p_ml, 3),
        })
        return chosen


def _play(script, seed: int, loops: int, shadow: bool = True):
    hp = _Probe(seed, shadow=shadow)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"),
              e.get("target"), e.get("to")) for e in st.history]
    return hp, st, trace


# ---------------------------------------------------------------------------
# verify＝観測器が対局を変えていない物証
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    bad = []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        _a, _s, t1 = _play(sc, seed, loops, shadow=True)
        _b, _s2, t2 = _play(sc, seed, loops, shadow=False)
        n += 1
        if t1 != t2:
            bad.append(f"{name}#{seed}")
    return {"days": days, "n_games": n, "mismatch": bad}


# ---------------------------------------------------------------------------
# incidents＝(a)(b)：犯人を明かす事件の発生回数と、その絞り込み量
# ---------------------------------------------------------------------------
def incidents(days: int = 3, loops: int = 8, start: int = 0,
              end: int | None = None) -> dict:
    """公開履歴だけを材料に、REVEALING 事件の発生と candidate 絞り込みを数える。

    (a) 発生回数＝`incident` の `occurs: True` かつ名前が REVEALING（★公開アナウンス）。
    (b) 絞り込み量＝そのループ終了時点の履歴で
        `|事件効果チャネル無しの候補| − |現行の候補|`（対象日ごと）。
    """
    from arena.benchmark import benchmark_scripts

    c: Counter = Counter()
    narrowed: Counter = Counter()
    per_script_games: Counter = Counter()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        n += 1
        hp = HeuristicProtagonist(seed)
        st, _ = run_game(replace(sc, loops=loops),
                         {"mastermind": HeuristicMastermind(seed),
                          "p1": hp, "p2": hp, "p3": hp})
        cast = list(sc.cast)
        pub_inc = [{"day": i.day, "name": i.name} for i in sc.incidents]
        hit_game = False
        for e in st.history:
            if e.get("event") == "incident" and e.get("occurs") \
                    and e.get("name") in REVEALING:
                c[f"(a) 発生：{e['name']}"] += 1
                c["(a) 発生：合計（犯人/エリアを明かす事件）"] += 1
        # ループ末（loop_result 直後）の履歴断面で (b) を測る
        cut = [i for i, e in enumerate(st.history) if e.get("event") == "loop_result"]
        for i in cut:
            h = st.history[:i + 1]
            b = Belief(cast, pub_inc, set_name=sc.set_name)
            b.observe(h)
            real = b.culprit_candidates()
            abl = _cands_without_effect_channel(b)
            for d, s in real.items():
                gain = len(abl.get(d, set())) - len(s)
                if gain > 0:
                    narrowed[f"day{d}"] += gain
                    c["(b) 事件効果チャネルで追加除外された候補（延べ・ループ末断面）"] += gain
                    hit_game = True
        if hit_game:
            per_script_games[name] += 1
    return {"days": days, "n_games": n, "counts": dict(c),
            "narrowed_by_day": dict(narrowed),
            "(b) 効いた局数（脚本ファミリ別）": dict(per_script_games)}


# ---------------------------------------------------------------------------
# seats＝(c)：冷却席の層別
# ---------------------------------------------------------------------------
def seats(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.corpus_census import script_signature

    c: Counter = Counter()
    rows: list = []
    dead_games: set = set()
    eff_games: set = set()
    dead_sigs: set = set()
    eff_sigs: set = set()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _t = _play(sc, seed, loops)
        c.update(hp.c)
        n += 1
        sig = script_signature(sc)
        if hp.rows:
            dead_games.add(f"{name}#{seed}")
            dead_sigs.add(sig)
        if any(r["by_effect"] for r in hp.rows):
            eff_games.add(f"{name}#{seed}")
            eff_sigs.add(sig)
        for r in hp.rows:
            rows.append({"script": name, "seed": seed, **r})
    return {"days": days, "n_games": n, "counts": dict(c),
            "DEAD 局数": len(dead_games), "DEAD 独立脚本数": len(dead_sigs),
            "DEAD-eff 局数": len(eff_games), "DEAD-eff 独立脚本数": len(eff_sigs),
            "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["table", "verify", "incidents", "seats"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.mode == "table":
        table()
        return
    fn = {"verify": verify, "incidents": incidents, "seats": seats}[a.mode]
    res = fn(days=a.days, loops=a.loops, start=a.start, end=a.end)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    out = {k: v for k, v in res.items() if k != "rows"}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
