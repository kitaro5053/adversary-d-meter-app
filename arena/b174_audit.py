# -*- coding: utf-8 -*-
"""B-174 Phase 1：**「残る事件に対して使い道の無い冷却能力」へ友好を積んだ席を数える**（★計測のみ）。

起票＝`docs/バックログ_構想メモ_FableA.md` §50（ユーザー実戦報告 2026-08-05・`btx5_seal`）。
★本モジュールは `agents/` を1行も変更しない（`arena/` のみ）。land もしない。

---

## 0. 用語（★略語を使う前に、変数が何を指すかを定義する）

| 語 | 意味（現物） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回（`decide(view,"set_card",options)` が1つ返すこと） |
| **投資席** | その席で実際に選ばれた手が `友好+1` / `友好+2` で、対象がキャラであるもの |
| **`_invest`** | 主人公AIが毎ターン作り直す**辞書**＝**キー＝キャラ名／値＝そのキャラに友好を積む価値の点数**。作る場所＝`agents/heuristic_protagonist.py:4056 _compute_invest`。値＝そのキャラの実装済み友好能力について `_ability_value / (1 + 残り必要ハート) + tempo` の**最大値** |
| **`_ability_value(user, ability, target, view)`** | **友好能力1件の価値（点数）**を返す関数（`:1946`）。`target=None` は投資評価（最良対象を仮定） |
| **冷却能力** | `_ability_value` の枝 `"不安" in ability and "除去" in ability`（`:1990`）に入る友好能力。現行KBでは<br>男子学生/女子学生『学生の不安除去』・医者『不安操作（除去/付与）』・アイドル『不安除去』・ナース『不安臨界以上のキャラの不安除去』の**5件** |
| **`_incident_danger`** | 辞書＝**キー＝そのループで事件が予定されている日／値＝その事件の危険度（点数）**（`:1071-1190`）。材料は `view["incidents"]`＝**公開の事件予定表**（日と名前は公開＝`rules/00_rules_core.md:76` の非公開は犯人のみ） |
| **`_culprit_cands`** | 辞書＝**キー＝事件の日／値＝その事件の犯人でありうるキャラ名の集合**（`:1050`）。`belief.culprit_candidates()`＝**公開情報だけ**から作られる＝主人公が見てよい量 |
| **狙っている能力** | その投資席で `_invest[対象]` の最大値を作った能力。判定は**影の再計算**（下記 §1-2）で行う＝式を書き写さない |

★**本監査は「正解の配役」を一切参照しない**（運用doc `docs/運用_Opus5でのFableA運用_2026-08-01.md` §3-7）。
使うのは `protagonist_view`（`view`）と、そこから主人公AI自身が作った `_incident_danger` /
`_culprit_cands` / `belief` だけである。

## 1. 数え上げの定義（★数える前に固定する）

### 1-1. 「対象を持ちうるか」の述語

冷却能力 `ability`（行使者 `user`）が **その席の時点で残っている事件**に対して対処対象を持ちうる
⟺ **`d >= view["day"]` を満たす `_incident_danger` の日 `d` の `_culprit_cands[d]` の中に、
生存していて、その能力が対象にできるキャラが1人以上いる**。

「対象にできる」の条件（★すべて `rules/20_characters_abilities.md` の**カード文＝可否**）：

- **自身は対象にできない**（5件すべて「同一エリアの**自身以外**」）
  ＝`:91`（学生の不安除去＝他の学生）・`:181`（アイドル）・`:228`（医者）・`:240`/`:306`（ナース）・
  一覧 `:292-293`（男子/女子学生）・`:303`（アイドル）・`:297`（医者）。
  実装側の単一ソース＝`sim/abilities._same_area_others`（`n != user`）／`_nurse_targets`。
- **学生限定**（男子学生/女子学生『学生の不安除去』）＝対象は学生属性のみ（`engine.data.is_student`）。
- エリア一致は**要求しない**（移動で合流しうる＝健全側＝過剰に「無用」と言わない）。
- ナースの「不安臨界以上」も**要求しない**（同上・健全側）。

★**現行実装との差はただ1点＝「自身を除外していない」**（`heuristic_protagonist.py:2018-2027` の
`for cn in _culprit_cands[d]` は `cn == user` を弾かない）。日付の絞り（`d >= day`）は
B-141 の land で既に既定 ON（`B141_COOLER_VALUE_FUTURE_ONLY=True`）。

### 1-2. 分類（★局ではなく**席と行為**で数える＝規約 §11b）

| 記号 | 定義 |
|---|---|
| **INV** | 投資席（友好+1/+2 をキャラに置いた席） |
| **COOL** | INV のうち、狙っている能力が**冷却能力**である席 |
| **U** | COOL のうち §1-1 の述語が**偽**＝**残る事件に対して対象を持ちえない席**（＝本チケットの本体） |
| **U1** | U のうち **今日以降に危険事件が残っている**もの＝「残る事件の犯人候補に、この能力の対象が1人も居ない」（★ユーザーが指した形そのもの） |
| **U2** | U のうち **今日以降に危険事件が1つも残っていない**もの（＝ループ終盤）。★U2 は「冷却の価値が0」を意味しない＝不安は事件以外でも参照される（`rules/50_basic_tragedy_x.md:79` 妄想拡大ウイルス＝不安3でパーソンがSK化／`:159-160` メインラバーズ＝不安3+暗躍1で主人公死亡）＝**KB からは価値ゼロと言えない**（規約 §7 判例1 と同じ理由） |
| **U-α** | U のうち **能力の対象条件だけで言える**もの＝盤面の**生存キャラ全体**を見ても対象になりうるキャラが1人も居ない（belief 不要）。例＝女子学生の能力で「自身以外の生存学生が0人」 |
| **U-β** | U のうち **belief に依存する**もの＝対象になりうる生存キャラは居るが、**残る危険事件の犯人候補**にその対象が1人も居ない（＝ユーザーが指した形） |
| **U-self** | U のうち、**現行実装の条件**（`:2018-2027`＝自身を弾かない）では対象ありと出る席＝**残る事件の犯人候補が自分自身しか居ない**ため現行だけが誤認している席（★§1-1 との差の実測） |
| **U-fall** | U のうち、現行実装でも対象なし＝`_ability_value` が**フォールスルーの 15.0** を返しつつ投資している席（汎用能力のフォールスルーは 6.0＝`:2058`。その2.5倍） |

補助の内数：

- **U-糸**：U のうち、主人公の belief で **P(因果の糸) >= 0.5** かつ**最終ループでない**席
  （＝次ループ開始時に投資先へ不安+2 が乗る＝`rules/50_basic_tragedy_x.md:85`）。
- **U-lost**：U のうち、**その席が属するループが敗北で終わった**もの。
- **U-unused**：U のうち、その席の対象がそのループ中に**当該冷却能力を1度も使わなかった**もの
  （`goodwill_used` イベントで判定＝`sim/flow.py:128`）。

★**独立脚本数を必ず併記する**（130局/70局は seed 複製を含む）。

## 2. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面・単独実行）

    python -m arena.b174_audit verify --days 3
    python -m arena.b174_audit verify --days 5
    python -m arena.b174_audit count  --days 3
    python -m arena.b174_audit count  --days 5
    python -m arena.b174_audit game   --days 5 --game btx5_seal#0
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from agents.heuristic_protagonist import HeuristicProtagonist as HP
from engine.data import is_student
from sim import run_game

_KEYS = ("B141_COOLER_VALUE_FUTURE_ONLY", "B141_COOLER_ALLPAST_ONLY",
         "B141B_UNLOCK_SAME_DAY", "B143_YIELD",
         "B174_COOL_SELF_EXCLUDE", "B174_HAS_TARGET_SELF_EXCLUDE",
         "B174_COOL_NO_TARGET_VALUE")

#: 版（★`off` ＝現行 main のクラス既定。`_DEFAULTS` を張り直さない＝B-141cf と同じ作法）。
CONFIGS: dict[str, dict] = {
    "off":   {},
    "a":     {"B174_COOL_SELF_EXCLUDE": True},                  # (α) 可否・_ability_value
    "a2":    {"B174_HAS_TARGET_SELF_EXCLUDE": True},            # (α') 可否・_ability_has_target
    "aa2":   {"B174_COOL_SELF_EXCLUDE": True,
              "B174_HAS_TARGET_SELF_EXCLUDE": True},            # (α)+(α')＝可否の是正一式
    "b15":   {"B174_COOL_NO_TARGET_VALUE": 15.0},               # (β) 対照＝挙動不変のはず
    "b12":   {"B174_COOL_NO_TARGET_VALUE": 12.0},
    "b10":   {"B174_COOL_NO_TARGET_VALUE": 10.0},
    "b8":    {"B174_COOL_NO_TARGET_VALUE": 8.0},
    "b6":    {"B174_COOL_NO_TARGET_VALUE": 6.0},
    "b3":    {"B174_COOL_NO_TARGET_VALUE": 3.0},
    "b0":    {"B174_COOL_NO_TARGET_VALUE": 0.0},
    "aa2b6": {"B174_COOL_SELF_EXCLUDE": True,
              "B174_HAS_TARGET_SELF_EXCLUDE": True,
              "B174_COOL_NO_TARGET_VALUE": 6.0},
    "aa2b0": {"B174_COOL_SELF_EXCLUDE": True,
              "B174_HAS_TARGET_SELF_EXCLUDE": True,
              "B174_COOL_NO_TARGET_VALUE": 0.0},
}
_CLASS_DEFAULTS = {k: getattr(HP, k) for k in _KEYS}


def apply_cfg(cfg: str) -> None:
    """クラス既定へ戻してから、その版の切替口だけを立てる。"""
    for k, v in _CLASS_DEFAULTS.items():
        setattr(HP, k, v)
    for k, v in CONFIGS[cfg].items():
        setattr(HP, k, v)


def switches() -> str:
    """★毎レグで切替口の実効値を印字する（規約 §4）。"""
    return json.dumps({k: getattr(HP, k) for k in _KEYS}, ensure_ascii=False)


def is_cool(ability: str) -> bool:
    """`_ability_value` の冷却枝（`:1990`）に入る能力か＝**述語を実装と同一の式で持つ**。"""
    return "不安" in ability and "除去" in ability


def can_target(user: str, ability: str, cand: str) -> bool:
    """冷却能力 `ability`（行使者 user）が `cand` を対象にできるか（★可否＝KB のカード文）。

    `rules/20_characters_abilities.md:91,181,228,240,292-293,297,303,306`＝5件とも「自身以外」。
    学生限定は `:91`（男子学生/女子学生『学生の不安除去』）。
    実装側の単一ソース＝`sim/abilities._same_area_others`（`n != user`）。
    """
    if cand == user:
        return False
    if "学生" in ability:
        return is_student(cand)
    return True


# ---------------------------------------------------------------------------
# プローブ（★`super().decide()` の戻り値をそのまま返す＝挙動不変）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """投資席を数える観測器。`shadow=False` で影の再計算を止める（verify の対照）。"""

    #: ★席のどの情報を残すか
    def __init__(self, seed: int = 0, shadow: bool = True):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.rows: list = []
        self._shadow_on = shadow

    # -- 影の再計算：`_invest[name]` を作った能力（＝その席の狙い）を特定する ----
    def _ablate(self, view: dict, hide) -> dict:
        """`_ability_value` の投資評価だけ `hide(ability)` の分を -1e9 にして `_compute_invest`
        をもう一度回す。★式（`val/(1+need)+tempo`）を**書き写さない**ための仕掛け。"""
        keep_need = dict(getattr(self, "_invest_need", {}) or {})
        keep_tgt = dict(getattr(self, "_invest_has_tgt", {}) or {})
        base = type(self).__mro__[1]._ability_value

        def _patched(s, user, ability, target, vw):
            if target is None and hide(ability):
                return -1.0e9
            return base(s, user, ability, target, vw)

        _Probe._ability_value = _patched
        try:
            return self._compute_invest(view)
        finally:
            del _Probe._ability_value
            self._invest_need = keep_need
            self._invest_has_tgt = keep_tgt

    def _winner_ability(self, view: dict, name: str) -> str | None:
        """`_invest[name]` の最大値を作った能力名（＝その席が解禁を狙っている能力）。"""
        from engine.data import goodwill_abilities_of

        abs_ = [a["name"] for a in (goodwill_abilities_of(name) or [])]
        if len(abs_) <= 1:
            return abs_[0] if abs_ else None
        real = float((getattr(self, "_invest", {}) or {}).get(name, 0.0))
        for a in abs_:
            sh = self._ablate(view, lambda x, _a=a: x == _a)
            if abs(sh.get(name, 0.0) - real) > 1e-9:
                return a
        return None

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision != "set_card":
            return chosen
        self.c["席（set_card 決定）"] += 1
        card = chosen.get("card")
        if card not in ("友好+1", "友好+2") or chosen.get("target_kind") != "character":
            return chosen
        name = chosen.get("target")
        self.c["INV 投資席（友好+ をキャラに置いた席）"] += 1
        if not self._shadow_on:
            return chosen
        from engine.data import ability_kind
        if name not in (getattr(self, "_invest", None) or {}):
            # 解禁待ちの能力が1つも無い（全部解禁済み／未実装）＝「解禁を狙う投資」ではない
            self.c["INV-解禁待ちの能力なし（_invest に載らない対象）"] += 1
            return chosen
        ab = self._winner_ability(view, name)
        if ab is None:
            self.c["INV-狙い不明（能力なし/同点）"] += 1
            return chosen
        kd = ability_kind(name, ab) or "?"
        self.c[f"INV-狙いの kind＝{kd}"] += 1
        if kd in ("軽量情報開示", "重量情報開示", "情報回収"):
            self.c["★INV-情報収集投資（開示系の解禁を狙う席）"] += 1
        if not is_cool(ab):
            return chosen
        self.c["COOL 狙いが冷却能力の投資席"] += 1

        day = view.get("day", 0)
        danger = getattr(self, "_incident_danger", None) or {}
        cands = getattr(self, "_culprit_cands", None) or {}
        remaining_days = sorted(d for d in danger if d >= day)
        # ---- §1-1 の述語（★主人公に見える情報だけ） ----
        # `hit_cur` ＝**現行実装**が対象と見なす候補（自身を弾かない＝`:2018-2027` と同条件）
        # `hit_fix` ＝§1-1 の述語（自身を弾く＝KB のカード文どおり）
        hit_cur: set = set()
        for d in remaining_days:
            for cn in cands.get(d, ()):
                if not self._alive(view, cn):
                    continue
                if name in ("男子学生", "女子学生", "教師") and not is_student(cn):
                    continue
                hit_cur.add(cn)
        hit_fix = {cn for cn in hit_cur if can_target(name, ab, cn)}
        cur = float(self._ability_value(name, ab, None, view))
        if hit_fix:
            self.c["COOL-有効（残る事件に対象を持ちうる）"] += 1
            return chosen
        # ---- U（残る事件に対して対象を持ちえない席） ----
        alive_names = [c["name"] for c in view["characters"] if c.get("alive")]
        any_alive_target = any(can_target(name, ab, n) for n in alive_names)
        kind = "U-β(belief依存)" if any_alive_target else "U-α(対象条件だけで言える)"
        sub = ("U1（残る危険事件はあるが、その犯人候補に対象が居ない）" if remaining_days
               else "U2（今日以降に危険事件が1つも残っていない）")
        self.c["U 使い道の無い冷却能力への投資席"] += 1
        self.c[f"  {sub}"] += 1
        self.c[f"  {kind}"] += 1
        self.c["  U-self（現行だけが対象ありと誤認＝候補は自分自身のみ）" if hit_cur
               else "  U-fall（現行も対象なしを知りつつ 15.0 で投資）"] += 1
        # 因果の糸（★主人公の belief から。正解のルールは使わない）
        p_ito = sum(p for (_ry, rxs), p in self._belief.rule_marginals().items()
                    if "因果の糸" in rxs)
        last_loop = view.get("loop") == view.get("loops_total")
        if p_ito >= 0.5 and not last_loop:
            self.c["  内数 U-糸（P(因果の糸)>=0.5 かつ非最終ループ）"] += 1
        self.rows.append({
            "loop": view.get("loop"), "day": day, "seat": view.get("seat"),
            "card": card, "target": name, "ability": ab,
            "cur_value": round(cur, 2),
            "invest": round(float((getattr(self, "_invest", {}) or {}).get(name, 0.0)), 3),
            "invest_max": round(float(max((getattr(self, "_invest", {}) or {}).values(),
                                          default=0.0)), 3),
            "invest_rank": 1 + sorted((getattr(self, "_invest", {}) or {}).values(),
                                      reverse=True).index(
                (getattr(self, "_invest", {}) or {}).get(name, 0.0)),
            "need": int((getattr(self, "_invest_need", {}) or {}).get(name, -1)),
            "kind": kind, "sub": sub, "self_only": sorted(hit_cur),
            "p_ito": round(p_ito, 3), "last_loop": last_loop,
            "remaining_danger_days": remaining_days,
            "cands_on_remaining": sorted({cn for d in remaining_days
                                          for cn in cands.get(d, ())}),
        })
        return chosen


# ---------------------------------------------------------------------------
# 1局の実行
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int, shadow: bool = True):
    hp = _Probe(seed, shadow=shadow)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return hp, st, trace


def _play_plain(script, seed: int, loops: int):
    hp = HeuristicProtagonist(seed)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    return [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
            for e in st.history]


def _loop_results(st) -> dict:
    """ループ番号 → そのループの結果文字列（`loop_result` イベント）。"""
    out = {}
    for e in st.history:
        if e.get("event") == "loop_result":
            out[e.get("loop")] = str(e.get("result", ""))
    return out


def _used_abilities(st) -> set:
    """(loop, character, ability) の集合＝そのループで実際に使われた友好能力。"""
    return {(e.get("loop"), e.get("character"), e.get("ability"))
            for e in st.history if e.get("event") == "goodwill_used"}


# ---------------------------------------------------------------------------
# verify＝挙動不変の物証（プローブ有無で棋譜が完全一致）
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None) -> dict:
    from arena.benchmark import benchmark_scripts

    print(f"[切替口] {switches()} / days={days}", flush=True)
    bad = []
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        ref = _play_plain(sc, seed, loops)
        for tag, sh in (("probe(shadow=on)", True), ("probe(shadow=off)", False)):
            _hp, _st, tr = _play(sc, seed, loops, shadow=sh)
            if tr != ref:
                bad.append({"game": f"{name}#{seed}", "mode": tag})
        n += 1
    return {"days": days, "n_games": n, "mismatch": len(bad), "bad": bad[:20]}


# ---------------------------------------------------------------------------
# count＝本体の数え上げ
# ---------------------------------------------------------------------------
def count(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
          cfg: str = "off") -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.b145_audit import _outcome

    apply_cfg(cfg)
    print(f"[切替口:{cfg}] {switches()} / days={days}", flush=True)
    c: Counter = Counter()
    per_script: Counter = Counter()
    per_script_games: Counter = Counter()
    rows: list = []
    u_games: set = set()
    scripts: set = set()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _tr = _play(sc, seed, loops)
        c.update(hp.c)
        lr = _loop_results(st)
        used = _used_abilities(st)
        for r in hp.rows:
            lost = "敗北" in lr.get(r["loop"], "")
            unused = (r["loop"], r["target"], r["ability"]) not in used
            c["  内数 U-lost（その席のループが敗北で終わった）"] += int(lost)
            c["  内数 U-unused（対象がそのループ中に当該能力を1度も使わず）"] += int(unused)
            rows.append({"script": name, "seed": seed, "outcome": _outcome(st),
                         "loop_lost": lost, "ability_unused": unused, **r})
        if hp.rows:
            u_games.add(f"{name}#{seed}")
            per_script[name] += len(hp.rows)
            per_script_games[name] += 1
        scripts.add(name)
        n += 1
    return {
        "cfg": cfg, "days": days, "n_games": n, "n_scripts": len(scripts),
        "counts": dict(c),
        "U_games": len(u_games),
        "U_scripts": len(per_script),
        "U_per_script(席数)": dict(per_script),
        "U_per_script(局数)": dict(per_script_games),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# game＝1局の U 席を全部印字（★ユーザーの局の独立再現用）
# ---------------------------------------------------------------------------
def game(days: int = 5, loops: int = 8, target: str = "btx5_seal#0") -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.b145_audit import _outcome

    print(f"[切替口] {switches()} / days={days} / game={target}", flush=True)
    nm, _, sd = target.partition("#")
    seed = int(sd)
    for name, s, sc in benchmark_scripts(days=days):
        if name == nm and s == seed:
            hp, st, _tr = _play(sc, seed, loops)
            lr = _loop_results(st)
            used = _used_abilities(st)
            out = []
            for r in hp.rows:
                out.append({**r,
                            "loop_result": lr.get(r["loop"], ""),
                            "ability_unused": (r["loop"], r["target"],
                                               r["ability"]) not in used})
            return {"game": target, "outcome": _outcome(st),
                    "loop_no": st.loop_no,
                    "incidents": [{"day": i.day, "name": i.name} for i in sc.incidents],
                    "counts": dict(hp.c), "rows": out}
    raise SystemExit(f"局が見つからない: {target}")


# ---------------------------------------------------------------------------
# ab＝版ごとの成績と**局単位の diff の全数**（★`off` は毎回その場で実測する）
# ---------------------------------------------------------------------------
def _one(script, seed: int, loops: int) -> tuple:
    """1局＝(結末, 防衛までのループ数, 棋譜)。★プローブを使わない素の主人公AI。"""
    from arena.b145_audit import _outcome
    hp = HeuristicProtagonist(seed)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return _outcome(st), st.loop_no, trace


def _agg(res: dict, loops: int) -> dict:
    """`arena.benchmark` と同じ読み方＝防衛数・平均ループ数・L1・fb_loss・loss。"""
    lp = []
    defense = l1 = fb_win = fb_loss = loss = 0
    for _g, (out, n, _tr) in res.items():
        if out == "defense":
            defense += 1
            lp.append(n)
            l1 += int(n == 1)
        else:
            lp.append(loops + 1)
            fb_win += int(out == "fb_win")
            fb_loss += int(out == "fb_loss")
            loss += int(out == "loss")
    return {"defense": defense, "mean": round(sum(lp) / len(lp), 3) if lp else None,
            "L1": l1, "fb_win": fb_win, "fb_loss": fb_loss, "loss": loss}


def ab(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
       configs=("a",)) -> dict:
    """`off` を先頭で**毎回実測**し、各版を同一プロセス・同一コミットで連続比較する。"""
    from arena.benchmark import benchmark_scripts

    games = list(benchmark_scripts(days=days))[start:end]

    def _run(cfg: str) -> dict:
        apply_cfg(cfg)
        print(f"  [切替口:{cfg}] {switches()} / days={days}", flush=True)
        return {f"{name}#{seed}": _one(sc, seed, loops) for name, seed, sc in games}

    base = _run("off")
    out = {"days": days, "n_games": len(games),
           "off": _agg(base, loops), "versions": {}}
    # ★脚本別（`btx5_seal` を単独で印字するため）
    def _by_script(res):
        d: Counter = Counter()
        for g, (o, n, _t) in res.items():
            d[g.split("#")[0] + "/局数"] += 1
            d[g.split("#")[0] + "/防衛数"] += int(o == "defense")
        return dict(d)
    out["off_by_script"] = _by_script(base)
    for cfg in configs:
        res = _run(cfg)
        flips, moved = [], 0
        for g in base:
            bo, bn, bt = base[g]
            vo, vn, vt = res[g]
            if bt != vt:
                moved += 1
            if (bo, bn) != (vo, vn):
                flips.append({"game": g, "off": f"{bo}:{bn}", cfg: f"{vo}:{vn}",
                              "向き": ("改善" if (vo == "defense" and
                                                  (bo != "defense" or vn < bn))
                                       else "退行")})
        out["versions"][cfg] = {
            "agg": _agg(res, loops), "moved": moved,
            "flip数": len(flips), "flips": flips,
            "by_script": _by_script(res),
        }
    apply_cfg("off")
    return out


# ---------------------------------------------------------------------------
# why＝flip した局で「切替口が最初に手を変えた1点」を特定する（★機序の説明義務）
# ---------------------------------------------------------------------------
class _StreamProbe(HeuristicProtagonist):
    """`set_card` の決定を起きた順に記録するだけの観測器（★戻り値はそのまま）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.stream: list = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        if decision == "goodwill_ability":
            self.stream.append({"loop": view.get("loop"), "day": view.get("day"),
                                "決定": "友好能力の使用", **dict(chosen)})
        if decision == "set_card":
            rec = {"loop": view.get("loop"), "day": view.get("day"),
                   "seat": view.get("seat"), "card": chosen.get("card"),
                   "target": chosen.get("target"),
                   "kind": chosen.get("target_kind")}
            # ★その席で冷却能力の価値がいくつだったか（分岐の理由を残す）
            tgt = chosen.get("target")
            if tgt and chosen.get("card") in ("友好+1", "友好+2"):
                from engine.data import goodwill_abilities_of
                cools = [a["name"] for a in (goodwill_abilities_of(tgt) or [])
                         if is_cool(a["name"])]
                if cools:
                    rec["冷却価値"] = round(
                        float(self._ability_value(tgt, cools[0], None, view)), 2)
                    rec["残る危険事件"] = sorted(
                        d for d in (getattr(self, "_incident_danger", None) or {})
                        if d >= view.get("day", 0))
                    rec["犯人候補"] = sorted({
                        cn for d in rec["残る危険事件"]
                        for cn in (getattr(self, "_culprit_cands", {}) or {}).get(d, ())})
            self.stream.append(rec)
        return chosen


def _stream(script, seed: int, loops: int) -> list:
    hp = _StreamProbe(seed)
    run_game(replace(script, loops=loops),
             {"mastermind": HeuristicMastermind(seed), "p1": hp, "p2": hp, "p3": hp})
    return hp.stream


def why(days: int = 3, loops: int = 8, cfg: str = "a", games: tuple = ()) -> dict:
    """flip した局について、**OFF と cfg で最初に決定が食い違った席**を印字する。"""
    from arena.benchmark import benchmark_scripts

    all_games = {f"{name}#{seed}": sc for name, seed, sc in benchmark_scripts(days=days)}
    out = []
    for g in games:
        sc = all_games[g]
        seed = int(g.split("#")[1])
        apply_cfg("off")
        s0 = _stream(sc, seed, loops)
        apply_cfg(cfg)
        s1 = _stream(sc, seed, loops)
        apply_cfg("off")
        i = 0
        while i < min(len(s0), len(s1)) and s0[i] == s1[i]:
            i += 1
        out.append({"game": g, "cfg": cfg, "一致した席数": i,
                    "off側の席": s0[i] if i < len(s0) else None,
                    f"{cfg}側の席": s1[i] if i < len(s1) else None})
    print(f"[切替口] {switches()} / days={days}", flush=True)
    return {"days": days, "cfg": cfg, "起点": out}


# ---------------------------------------------------------------------------
# kifu＝ユーザー実戦の棋譜（`mmv_*.jsonl`）の**主人公view だけ**を使って述語を評価する
# ---------------------------------------------------------------------------
def kifu(path: str) -> dict:
    """★使うのは各 decision の `view`（＝`protagonist_view`）だけ。

    `meta.script.roles` / `incidents[].culprit`（＝正解の配役）は**読まない**
    （運用doc §3-7）。belief は `view["history"]`（公開履歴）から作り直す。
    """
    print(f"[切替口] {switches()} / kifu={path}", flush=True)
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("type") == "decision":
                recs.append(r)
    out = []
    for r in recs:
        if r.get("decision") != "set_card" or r.get("actor") == "mastermind":
            continue
        ch = r.get("chosen") or {}
        if ch.get("card") not in ("友好+1", "友好+2") \
                or ch.get("target_kind") != "character":
            continue
        view = r["view"]
        name = ch["target"]
        hp = HeuristicProtagonist(0)
        hp._sync(view)
        from engine.data import goodwill_abilities_of
        cools = [a["name"] for a in (goodwill_abilities_of(name) or [])
                 if is_cool(a["name"])]
        if not cools:
            continue
        ab = cools[0]
        day = view.get("day", 0)
        danger = getattr(hp, "_incident_danger", None) or {}
        cands = getattr(hp, "_culprit_cands", None) or {}
        rem = sorted(d for d in danger if d >= day)
        hit_cur = {cn for d in rem for cn in cands.get(d, ())
                   if hp._alive(view, cn)
                   and not (name in ("男子学生", "女子学生", "教師")
                            and not is_student(cn))}
        hit_fix = {cn for cn in hit_cur if can_target(name, ab, cn)}
        inv = hp._compute_invest(view)
        out.append({
            "loop": view["loop"], "day": day, "seat": r.get("actor"),
            "card": ch["card"], "target": name, "ability": ab,
            "残る危険事件の日": rem,
            "危険度": {d: round(danger[d], 2) for d in rem},
            "残る事件の犯人候補": {d: sorted(cands.get(d, ())) for d in rem},
            "現行の対象集合": sorted(hit_cur),
            "KB通りの対象集合(自身を除外)": sorted(hit_fix),
            "使い道なし(U)": not hit_fix,
            "_ability_value": round(float(hp._ability_value(name, ab, None, view)), 2),
            "_invest[対象]": round(float(inv.get(name, 0.0)), 3),
            "_invest の最大値": round(float(max(inv.values(), default=0.0)), 3),
            "_invest 順位": 1 + sorted(inv.values(), reverse=True).index(
                inv.get(name, 0.0)),
        })
    return {"kifu": path, "n_decisions": len(recs), "友好投資席": out}


def main() -> None:
    ap = argparse.ArgumentParser(description="B-174 Phase 1 計測")
    ap.add_argument("cmd", choices=["verify", "count", "game", "kifu", "ab", "why"])
    ap.add_argument("--games", default="", help="why: カンマ区切りの局名（例 btx_bomb#1）")
    ap.add_argument("--path", default=None, help="kifu: mmv_*.jsonl のパス")
    ap.add_argument("--cfg", default="off", help=f"版（{'/'.join(CONFIGS)}）")
    ap.add_argument("--configs", default="a", help="ab: カンマ区切りの版リスト")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--game", default="btx5_seal#0")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end)
    elif a.cmd == "count":
        res = count(a.days, a.loops, a.start, a.end, a.cfg)
    elif a.cmd == "ab":
        res = ab(a.days, a.loops, a.start, a.end,
                 tuple(x for x in a.configs.split(",") if x))
    elif a.cmd == "why":
        res = why(a.days, a.loops, a.cfg,
                  tuple(x for x in a.games.split(",") if x))
    elif a.cmd == "kifu":
        res = kifu(a.path)
    else:
        res = game(a.days, a.loops, a.game)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
