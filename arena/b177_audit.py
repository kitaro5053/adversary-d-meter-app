# -*- coding: utf-8 -*-
"""B-177：**B-174 の是正で「何に置き換わったのか」を測る**（★計測のみ・`agents/` 非接触）。

起票＝`docs/バックログ_構想メモ_FableA.md` §53（ユーザー裁定 2026-08-06
「測ってからで。まぁ飲むつもりではあるけれど、先にはからないと忘れそうだからね。」）。
親＝§50（B-174）／監査doc＝`docs/監査_B174_使い道の無い能力への投資_2026-08-05.md`。

------------------------------------------------------------------------------
## 0. このモジュールが答える問い
------------------------------------------------------------------------------

B-174 の版 `a`（`B174_COOL_SELF_EXCLUDE=True`＝**冷却能力の対象から行使者自身を除く**）は
**規則としては正しい**が、防衛が **3日級 129→127・5日級 63→61** と合計4局減る。

∴ 問うのは「是正が正しいか」ではなく **「是正後に何へ置き換わったのか」**。
flip した10局それぞれについて、**最初に決定が分岐した席**で

- **OFF は何を打ち／ON は何を打ったか**（現物のカード名・対象名）
- **置き換え先はどういう性質の手か**（下記 R1〜R4）
- **その置き換えがその局の敗北へどう繋がったか**

を出す。

------------------------------------------------------------------------------
## 1. 用語（★略語を使う前に、変数が何を指すかを定義する）
------------------------------------------------------------------------------

| 語 | 何を指すか（現物の場所） |
|---|---|
| **席（seat）** | 主人公の `set_card` 決定1回（`decide(view,"set_card",options)` が1つ返すこと） |
| **投資席** | その席で選ばれた手が `友好+1`/`友好+2` で、対象がキャラであるもの |
| **`_invest`** | 主人公AIが毎ターン作り直す**辞書**＝**キー＝キャラ名／値＝そのキャラに友好を積む価値の点数**（`agents/heuristic_protagonist.py:4131 _compute_invest`） |
| **狙う能力** | その投資席で `_invest[対象]` の最大値を作った友好能力。特定は**影の再計算**（`b174_audit._Probe._winner_ability`＝能力1件ずつ投資評価を -1e9 に差し替えて `_compute_invest` を回し直す）＝**式を書き写さない** |
| **冷却能力** | `_ability_value` の枝 `"不安" in ability and "除去" in ability`（`:2042`）に入る友好能力＝現行KBでは5件（男子学生／女子学生『学生の不安除去』・医者『不安操作』・アイドル『不安除去』・ナース『不安臨界以上のキャラの不安除去』） |
| **U（使い道の無い冷却投資）** | その席の時点で**残っている危険事件**（`_incident_danger` の `d >= day`）の**犯人候補**（`_culprit_cands[d]`＝belief＝公開情報だけ）の中に、その冷却能力が**対象にできる生存キャラが1人も居ない**こと。「対象にできる」の可否は `b174_audit.can_target`（自身以外／学生限定）＝`rules/20_characters_abilities.md` のカード文 |
| **flip** | その局の (結末, 防衛までのループ数) が OFF と ON で違うこと |

★**本監査は「正解の配役」を一切参照しない**（運用doc
`docs/運用_Opus5でのFableA運用_2026-08-01.md` §3-7）。使うのは `protagonist_view` と、
そこから主人公AI自身が作った `_incident_danger` / `_culprit_cands` / `_belief` だけ。

------------------------------------------------------------------------------
## 2. 置き換え先の分類（★定義を先に固定し、後から変えない＝§53 の表そのまま）
------------------------------------------------------------------------------

| 分類 | 定義（本モジュールでの操作化） |
|---|---|
| **R1＝別の投資先へ移った** | ON も `友好+n`→キャラだが**対象が別キャラ**。★その新しい投資先の狙う能力に **U 判定（上の述語）を当てる** |
| **R2＝投資をやめて別種の手へ** | ON のカードが `友好+n` 以外（冷却・暗躍禁止・移動・移動禁止・友好禁止…）。★その手が**空振りでないか**を `agents/b100_mix.futile_reason`＋`arena/futile_audit` の分類器で見る |
| **R3＝同じ穴に落ちた** | R1 のうち、**新しい投資先の狙う能力も U**（＝使えない能力）だったもの＝**是正が不完全＝述語の穴** |
| **R4＝順序が変わっただけ** | その日の3席の**カード多重集合が OFF と ON で同一**（＝どの席が打つかだけが替わった＝籤帯。`docs/監査_B161_冷却が決定へ届かない_2026-08-04.md` §4-4(1) と同型） |

判定の優先順位＝**R4 → R3 → R1 → R2**（R4 は「そもそも置き換わっていない」ので最優先、
R3 は R1 の部分集合なので R1 より先に見る）。

------------------------------------------------------------------------------
## 3. CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`・前面・単独実行）
------------------------------------------------------------------------------

    python -m arena.b177_audit verify --days 3          # 挙動不変の物証（プローブ有無で棋譜一致）
    python -m arena.b177_audit story --days 3 --games btx_bomb#1,btx_bomb#3
    python -m arena.b177_audit story --days 5 --games random_FS#3
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b174_audit import (_Probe, _loop_results, _play_plain, _used_abilities,
                              apply_cfg, can_target, is_cool, switches)
from engine.data import is_student
from sim import run_game

#: 友好投資のカード（`sim/legal.py` の札名）
_GW = ("友好+1", "友好+2")


# ---------------------------------------------------------------------------
# プローブ（★`super().decide()` の戻り値をそのまま返す＝挙動不変。`verify` で実証）
# ---------------------------------------------------------------------------
class _Rich(_Probe):
    """`b174_audit._Probe` を継承し、**全ての `set_card` 席**を注釈つきで記録する。

    `_Probe` から受け継ぐもの＝`_ablate` / `_winner_ability`（狙う能力の特定）と U 数え上げ。
    本クラスが足すのは「**席の列（stream）**」と、非投資席の**空振り判定**だけ。
    """

    def __init__(self, seed: int = 0, shadow: bool = True):
        super().__init__(seed, shadow=shadow)
        self.stream: list[dict] = []

    # -- 影の再計算の重複を避ける（★同じ席で `_Probe` と本クラスが2回呼ぶため） --
    def _winner_ability(self, view: dict, name: str):
        """`_Probe._winner_ability` の**1件キャッシュ**（返り値は同一＝挙動不変）。

        ★キャッシュキーに `id(view)` を使うので、**直前の view を参照で保持**して
        id が再利用されないようにする（保持しないと gc 後に同じ id が別 view に付きうる）。
        """
        key = (id(view), name)
        if getattr(self, "_wa_key", None) == key:
            return self._wa_val
        val = super()._winner_ability(view, name)
        self._wa_key, self._wa_val, self._wa_view = key, val, view
        return val

    # -- 冷却能力の「残る事件に対する使い道」（★b174 と同一の述語） --------------
    def _cool_usability(self, view: dict, user: str, ability: str) -> dict:
        day = view.get("day", 0)
        danger = getattr(self, "_incident_danger", None) or {}
        cands = getattr(self, "_culprit_cands", None) or {}
        rem = sorted(d for d in danger if d >= day)
        hit_cur: set = set()
        for d in rem:
            for cn in cands.get(d, ()):
                if not self._alive(view, cn):
                    continue
                if user in ("男子学生", "女子学生", "教師") and not is_student(cn):
                    continue
                hit_cur.add(cn)
        hit_fix = {cn for cn in hit_cur if can_target(user, ability, cn)}
        return {
            "残る危険事件の日": rem,
            "危険度": {d: round(float(danger[d]), 1) for d in rem},
            "残る事件の犯人候補": sorted({cn for d in rem for cn in cands.get(d, ())}),
            "現行が対象と見なす候補": sorted(hit_cur),
            "KB通りの対象(自身を除外)": sorted(hit_fix),
            "★U（使い道なし）": not hit_fix,
            "_ability_value": round(float(self._ability_value(user, ability, None, view)), 2),
        }

    # -- 非投資席が「空振り」か（★主人公が見える情報だけ） ----------------------
    def _futility(self, view: dict, card: str, target, kind) -> dict:
        out: dict = {}
        from agents.b100_mix import futile_reason, noop_ctx_for, rumor_prob
        try:
            roles = self._belief.role_marginals()
        except Exception:
            roles = {}
        try:
            out["b100_mix.futile_reason"] = futile_reason(
                view, self, roles, card, target, kind,
                noop_ctx_for(self, view), rumor_prob(self))
        except Exception as e:                                  # pragma: no cover
            out["b100_mix.futile_reason"] = f"判定不能({e})"
        if kind == "character" and card == "不安-1":
            from arena.futile_audit import classify_unrest_down
            from engine.data import unrest_threshold_of
            out["futile_audit.不安-1"] = classify_unrest_down(view, self, target)
            c = next((x for x in view.get("characters", []) or []
                      if x.get("name") == target), None)
            day = view.get("day", 0)
            danger = getattr(self, "_incident_danger", None) or {}
            cands = getattr(self, "_culprit_cands", None) or {}
            rem = sorted(d for d in danger if d >= day)
            out["冷却の当て先"] = {
                "対象の不安": (c or {}).get("unrest"),
                "対象の不安臨界": unrest_threshold_of(target),
                "残る危険事件の日": rem,
                "★対象は残る事件の犯人候補か": sorted(
                    d for d in rem if target in (cands.get(d, ()) or ())),
            }
        if kind == "character" and card in _GW:
            from arena.futile_audit import classify_goodwill_final
            out["futile_audit.最終日友好"] = classify_goodwill_final(
                view, self, target, card)
        return out

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)   # ＝`_Probe.decide`
        if decision == "goodwill_ability":
            self.stream.append({"loop": view.get("loop"), "day": view.get("day"),
                                "決定": "友好能力の使用", **dict(chosen)})
            return chosen
        if decision != "set_card":
            return chosen
        card, tgt = chosen.get("card"), chosen.get("target")
        kind = chosen.get("target_kind")
        rec: dict = {"loop": view.get("loop"), "day": view.get("day"),
                     "seat": view.get("seat"), "card": card,
                     "target": tgt, "kind": kind}
        # ★その日の最初の席で盤面のカウンターを1行だけ残す（★カウンターは公開情報＝
        #   `rules/00_rules_core.md`。冷却が「事件日に効いているか」を見るための物証）。
        k = (view.get("loop"), view.get("day"))
        if k != getattr(self, "_snap_key", None):
            self._snap_key = k
            rec["日初の盤面"] = {
                c["name"]: f"不安{c.get('unrest', 0)}/友好{c.get('goodwill', 0)}"
                           f"@{c.get('area')}"
                for c in view.get("characters", []) or [] if c.get("alive")}
            rec["日初の板の暗躍"] = dict(view.get("board_anyaku") or {})
        if not self._shadow_on:
            self.stream.append(rec)
            return chosen
        if card in _GW and kind == "character":
            inv = getattr(self, "_invest", None) or {}
            ab = self._winner_ability(view, tgt) if tgt in inv else None
            rec["狙う能力"] = ab
            if ab is not None:
                from engine.data import ability_kind
                rec["能力の種別"] = ability_kind(tgt, ab)
                # ★冷却でない能力（開示系など）に B-174 の述語は当たらない＝
                #   代わりに「今この盤面で発動対象を持つか」を記録する（B-4b/c の関数）。
                rec["今この盤面で発動対象あり"] = bool(
                    self._ability_has_target(tgt, ab, view))
                if is_cool(ab):
                    rec["冷却の使い道"] = self._cool_usability(view, tgt, ab)
            rec["_invest[対象]"] = (round(float(inv[tgt]), 3) if tgt in inv
                                    else "対象は _invest に載っていない"
                                         "（解禁待ちの実装済み能力が無い）")
            rec["_invest の最大値"] = round(float(max(inv.values(), default=0.0)), 3)
            rec["_invest 順位"] = (
                1 + sorted(inv.values(), reverse=True).index(inv[tgt])
                if tgt in inv else None)
            rec["残り必要ハート"] = int(
                (getattr(self, "_invest_need", {}) or {}).get(tgt, -1))
        else:
            rec["空振り判定"] = self._futility(view, card, tgt, kind)
        self.stream.append(rec)
        return chosen


def _seat_key(r: dict) -> tuple:
    """★席の同一性＝「誰がいつどこへ何を置いたか」だけ（注釈は比較に使わない）。"""
    if r.get("決定") == "友好能力の使用":
        return ("gw_ability", r.get("loop"), r.get("day"),
                r.get("character"), r.get("ability"), r.get("target"))
    return ("set_card", r.get("loop"), r.get("day"), r.get("seat"),
            r.get("card"), r.get("target"), r.get("kind"))


# ---------------------------------------------------------------------------
# 1局の実行
# ---------------------------------------------------------------------------
def _play(script, seed: int, loops: int, shadow: bool = True):
    hp = _Rich(seed, shadow=shadow)
    st, _ = run_game(replace(script, loops=loops),
                     {"mastermind": HeuristicMastermind(seed),
                      "p1": hp, "p2": hp, "p3": hp})
    trace = [(e.get("loop"), e.get("day"), e.get("event"), e.get("name"))
             for e in st.history]
    return hp, st, trace


def _scripts(days: int) -> dict:
    from arena.benchmark import benchmark_scripts
    return {f"{n}#{s}": (n, s, sc) for n, s, sc in benchmark_scripts(days=days)}


# ---------------------------------------------------------------------------
# verify＝挙動不変の物証（プローブ有無で棋譜が完全一致／OFF と ON の両方で）
# ---------------------------------------------------------------------------
def verify(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
           configs=("off", "a"), only: tuple = ()) -> dict:
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
# story＝1局の「置き換え先」を機序つきで出す（★本チケットの本体）
# ---------------------------------------------------------------------------
def _events_by_day(st) -> dict:
    """(loop, day) → その日に起きた出来事の短い列（事件の発生・死亡・敗北・ループ終了）。"""
    out: dict = {}
    keep = ("incident", "death", "defeat", "loop_end", "loop_result",
            "culprit_reveal", "incident_effect", "final_battle", "game_over")
    for e in st.history:
        ev = e.get("event")
        if ev not in keep:
            continue
        k = (e.get("loop"), e.get("day"))
        s = ev
        if ev == "incident":
            # ★`eligible`＝その瞬間「生存かつ不安臨界以上」のキャラ＝**公開情報**
            #   （卓上の不安カウンターから誰でも数えられる＝`sim/effects.py:369-374`）。
            s = (f"事件『{e.get('name')}』 occurs={e.get('occurs')}"
                 f" 不安臨界以上={e.get('eligible')}")
        elif ev == "incident_effect":
            s = f"事件効果『{e.get('name')}』{e.get('note') or e.get('target') or ''}"
        elif ev == "death":
            s = f"死亡『{e.get('name')}』{e.get('cause') or ''}"
        elif ev == "defeat":
            s = f"★敗北条件『{e.get('reason')}』"
        elif ev == "loop_end":
            s = f"ループ終了『{e.get('reason')}』"
        elif ev == "loop_result":
            s = f"ループ結果『{e.get('result')}』"
        elif ev == "culprit_reveal":
            s = f"犯人公開 day={e.get('day')} {e.get('name')}"
        elif ev == "final_battle":
            s = f"最後の戦い {e.get('name')}→{e.get('guess')} correct={e.get('correct')}"
        elif ev == "game_over":
            s = f"決着 winner={e.get('winner')}"
        out.setdefault(k, []).append(s)
    return out


def _day_cards(stream: list) -> dict:
    """(loop, day) → その日に置かれた3席の [(seat, card, target, kind)]（置いた順）。"""
    out: dict = {}
    for r in stream:
        if r.get("決定") == "友好能力の使用":
            continue
        out.setdefault((r["loop"], r["day"]), []).append(
            (r.get("seat"), r.get("card"), r.get("target"), r.get("kind")))
    return out


def _day_snap(stream: list) -> dict:
    """(loop, day) → その日の最初の席で見えていた盤面（不安/友好/エリア・板の暗躍）。"""
    out: dict = {}
    for r in stream:
        if "日初の盤面" in r:
            out[(r["loop"], r["day"])] = {"キャラ": r["日初の盤面"],
                                          "板の暗躍": r.get("日初の板の暗躍")}
    return out


def _classify(off_rec: dict, on_rec: dict, same_day_multiset: bool) -> dict:
    """★§2 の分類（優先順位＝R4 → R3 → R1 → R2）。"""
    if same_day_multiset:
        return {"分類": "R4", "根拠": "その日の3席のカード多重集合が OFF と ON で同一"
                              "＝どの席が打つかだけが替わった（籤帯）"}
    oc, ot = off_rec.get("card"), off_rec.get("target")
    nc, nt = on_rec.get("card"), on_rec.get("target")
    if nc in _GW and on_rec.get("kind") == "character":
        u = ((on_rec.get("冷却の使い道") or {}).get("★U（使い道なし）"))
        if u is True:
            return {"分類": "R3", "根拠": f"置き換え先 {nt} の狙う能力"
                    f"『{on_rec.get('狙う能力')}』も U（残る事件に対象を持ちえない）"
                    "＝是正が不完全＝述語の穴"}
        return {"分類": "R1", "根拠": f"友好が {ot} → {nt} へ移った。"
                f"狙う能力『{on_rec.get('狙う能力')}』"
                f"（種別 {on_rec.get('能力の種別')}）／U 判定＝{u}"}
    return {"分類": "R2", "根拠": f"投資（{oc}→{ot}）をやめて別種の手（{nc}→{nt}）へ"}


def story(days: int = 3, loops: int = 8, game: str = "btx_bomb#1",
          cfg: str = "a") -> dict:
    from arena.b145_audit import _outcome

    name, seed, sc = _scripts(days)[game]
    apply_cfg("off")
    print(f"  [切替口:off] {switches()} / {game}", flush=True)
    p0, st0, _ = _play(sc, seed, loops)
    apply_cfg(cfg)
    print(f"  [切替口:{cfg}] {switches()} / {game}", flush=True)
    p1, st1, _ = _play(sc, seed, loops)
    apply_cfg("off")

    s0, s1 = p0.stream, p1.stream
    i = 0
    while i < min(len(s0), len(s1)) and _seat_key(s0[i]) == _seat_key(s1[i]):
        i += 1
    off_rec = s0[i] if i < len(s0) else None
    on_rec = s1[i] if i < len(s1) else None

    d0, d1 = _day_cards(s0), _day_cards(s1)
    key = (off_rec or {}).get("loop"), (off_rec or {}).get("day")
    same_ms = (Counter(c[1:] for c in d0.get(key, ()))
               == Counter(c[1:] for c in d1.get(key, ())))
    # ★局全体でも多重集合を見る（同日で閉じない入替の検出）
    all_ms = (Counter(c[1:] for v in d0.values() for c in v)
              == Counter(c[1:] for v in d1.values() for c in v))

    e0, e1 = _events_by_day(st0), _events_by_day(st1)
    n0, n1 = _day_snap(s0), _day_snap(s1)
    days_all = sorted(set(d0) | set(d1) | set(e0) | set(e1),
                      key=lambda k: (k[0] if k[0] is not None else 99,
                                     k[1] if k[1] is not None else 99))
    table = []
    for k in days_all:
        a = [f"{s}:{c}→{t}" for s, c, t, _kd in d0.get(k, ())]
        b = [f"{s}:{c}→{t}" for s, c, t, _kd in d1.get(k, ())]
        table.append({"loop": k[0], "day": k[1],
                      "OFF": a, "ON": b,
                      "同一多重集合": Counter(x.split(":", 1)[1] for x in a)
                      == Counter(x.split(":", 1)[1] for x in b),
                      "OFF出来事": e0.get(k, []), "ON出来事": e1.get(k, []),
                      "OFF日初盤面": n0.get(k), "ON日初盤面": n1.get(k)})

    return {
        "game": game, "days": days, "cfg": cfg,
        "OFF": {"結末": _outcome(st0), "loop_no": st0.loop_no,
                "ループ結果": _loop_results(st0)},
        "ON": {"結末": _outcome(st1), "loop_no": st1.loop_no,
               "ループ結果": _loop_results(st1)},
        "分岐": {
            "一致した席数": i,
            "OFF側の席": off_rec,
            "ON側の席": on_rec,
            "同日の多重集合が一致": same_ms,
            "局全体の多重集合が一致": all_ms,
            **_classify(off_rec or {}, on_rec or {}, same_ms),
        },
        "日別": table,
        "OFFで使われた友好能力": sorted(
            f"L{l}:{c}『{a}』" for l, c, a in _used_abilities(st0)),
        "ONで使われた友好能力": sorted(
            f"L{l}:{c}『{a}』" for l, c, a in _used_abilities(st1)),
        # ★「誰が誰に使ったか」＝R1/R3 の「置き換え先の能力は実際に働いたのか」の物証
        "OFFの友好能力宣言": [
            f"L{r.get('loop')}D{r.get('day')} {r.get('character')}"
            f"『{r.get('ability')}』→{r.get('target')}"
            for r in s0 if r.get("決定") == "友好能力の使用"],
        "ONの友好能力宣言": [
            f"L{r.get('loop')}D{r.get('day')} {r.get('character')}"
            f"『{r.get('ability')}』→{r.get('target')}"
            for r in s1 if r.get("決定") == "友好能力の使用"],
    }


# ---------------------------------------------------------------------------
# unlock＝「投資席の狙う能力は、そのループ中に解禁されて使われたか」を数える
#   （★§7 欠陥候補①＝「解禁まで届かない投資先へ友好が流れる」の射程を測る）
# ---------------------------------------------------------------------------
def unlock(days: int = 3, loops: int = 8, cfg: str = "off",
           start: int = 0, end: int | None = None) -> dict:
    """投資席を `残り必要ハート(need)` 別に分け、**同じループ中にその能力が実際に
    宣言されたか**を数える。★`need` は「あと何ハート要るか」＝解禁の遠さ。"""
    from arena.benchmark import benchmark_scripts

    apply_cfg(cfg)
    print(f"[切替口:{cfg}] {switches()} / days={days}", flush=True)
    by_need: dict = {}
    tot = Counter()
    scripts: set = set()
    n = 0
    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        hp, st, _ = _play(sc, seed, loops)
        used = _used_abilities(st)
        for r in hp.stream:
            if r.get("決定") == "友好能力の使用":
                continue
            ab = r.get("狙う能力")
            if r.get("card") not in _GW or r.get("kind") != "character" or ab is None:
                continue
            need = r.get("残り必要ハート")
            hit = (r["loop"], r["target"], ab) in used
            d = by_need.setdefault(need, Counter())
            d["投資席"] += 1
            d["同ループ中に解禁され使われた"] += int(hit)
            tot["投資席"] += 1
            tot["同ループ中に解禁され使われた"] += int(hit)
        scripts.add(name)
        n += 1
    apply_cfg("off")
    return {"cfg": cfg, "days": days, "n_games": n, "n_script_families": len(scripts),
            "合計": dict(tot),
            "need別": {str(k): dict(v) for k, v in sorted(
                by_need.items(), key=lambda x: (x[0] is None, x[0]))}}


def main() -> None:
    ap = argparse.ArgumentParser(description="B-177 置き換え先の測定")
    ap.add_argument("cmd", choices=["verify", "story", "unlock"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--cfg", default="a")
    ap.add_argument("--games", default="")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.cmd == "verify":
        res = verify(a.days, a.loops, a.start, a.end,
                     only=tuple(x for x in a.games.split(",") if x))
    elif a.cmd == "unlock":
        res = unlock(a.days, a.loops, a.cfg, a.start, a.end)
    else:
        res = {g: story(a.days, a.loops, g, a.cfg)
               for g in (x for x in a.games.split(",") if x)}
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()
