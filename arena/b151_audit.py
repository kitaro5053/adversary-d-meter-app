# -*- coding: utf-8 -*-
"""B-151：**事件の犯人は原理的に絞れるのか**（★計測のみ・`agents/` 非接触・挙動不変）。

## 発端

`docs/監査_B150_折り手が立たない理由_2026-08-04.md` §3-4／§3-5＝
防御が効かない主因は「札が無い」ではなく **S2＝犯人を1人に絞れない**（930／1389 件）。
`_cooling_breaks`（`agents/defense_plan.py:1119-1121`）は**生存犯人候補がちょうど1人**でないと
折り手を立てない。実測では **5人以上が 67.9%／64.0%・ちょうど1人は 26.5%／20.2%**。
★B-150 §7-10 が申し送った未決＝「**絞れるはずなのに絞れていない**」のか
「**盤上の情報では原理的に絞れない**」のか。**本レーンがこの分岐を決める。**

## 判定法（★B-147 の作法をそのまま使えるか＝使えない。代わりに何を使ったか）

B-147（フレンド同定）は **役職周辺確率ベクトルの完全一致**＝交換可能性で「原理的に不能」を
示した。**本レーンではそのままでは使えない**：

- `Belief.culprit_candidates()`（`agents/belief.py:2087-2146`）は**確率ベクトルではなく
  ハード集合**を返す（可能世界の共通部分）。集合の中は**一様**＝**定義上いつでも交換可能**
  になり、判定が自明に真になってしまう（＝何も測っていない）。
- ∴ 本レーンは**事後分布ではなく「観測の側」で対称性を測る**：
  **候補 a と b が、公開記録の中で完全に対称な足跡しか持たないなら、
  公開情報のどんな関数も a と b に違う値を割り当てられない**（＝原理的に区別不能）。
  これは B-147 の「事後が一致」を**十分条件の側から**言い直したもの
  （事後一致 ← 観測の対称性）で、**より強い主張**である。

### footprint（足跡）の3層＝狭い順に

| 層 | 何を見るか | 意味 |
|---|---|---|
| **R（規則結合・retro）** | KB が**犯人という変数に結合させている公開記録**だけ（§KB表 C1〜C10）＝`phase=="incident"` の全イベント／`death`／`culprit_reveal`／`role_reveal`／`entry` | ここが対称なら「**規則が犯人に結び付けた観測**は a と b を区別していない」 |
| **E（＋今日の発火可能性）** | R ＋ **`unrest >= 不安臨界` の真偽**（`rules/00_rules_core.md:119-121` の発生条件2＝公開カウンターから誰でも数えられる） | C11＝脚本家が犯人の不安を臨界まで上げねばならないという**唯一の前向きチャネル** |
| **W（全公開情報）** | E ＋ **公開履歴の全イベントでの言及**＋現在のカウンター/エリア/生死/公開役職＋**今ターン相手が伏せた札の宛先**（`sim/views.py:41-49`＝宛先は公開） | 上限。ここが対称なら**どんな推論器でも絶対に無理** |

★**W で割れても R/E で対称**なら、割っている情報は**規則が犯人に結合していないもの**
（エリアの違い・友好カウンターの差など）＝それで犯人を当てるのは**相関の当てずっぽう**である。
∴ 本doc は **R/E を主判定**、**W を上限の参考**として両方出す。

## ★もう1つの問い＝「そもそも犯人を同定する必要があるのか」

`rules/00_rules_core.md:119-121`＝事件は「**犯人が生存**」かつ「**犯人に不安臨界以上の不安**」の
両方でのみ発生する。∴ **候補全員を臨界未満に保てば、犯人が誰であろうと事件は発生しない。**
＝**犯人の同定は折り手の十分条件であって必要条件ではない**。
`_cooling_breaks` が見ているのは `len(alive)!=1` だが、規則が要求するのは
「**今日臨界に届きうる候補**」の側である。本計測はこの2つの数を並べて出す
（`N_alive` / `N_elig` / `N_reach(k)`）。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b151_audit verify  --days 3 --end 12
    python -m arena.b151_audit kb                       # KB のチャネル棚卸し（コード不要）
    python -m arena.b151_audit count   --days 3 --json d3.json
    python -m arena.b151_audit count   --days 5 --json d5.json
    python -m arena.b151_audit merge   --days 3 --inputs a.json b.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
# ★判定器は既存資産をそのまま使う（二重実装の禁止＝チケット §4）。
from arena.b145_audit import _outcome
from arena.b146_probe import _true_boards
from arena.b149_audit import _MMProbe
from engine.data import CHARACTER_FORBIDDEN, unrest_threshold_of
from sim import run_game

_AREAS = ("病院", "神社", "都市", "学校")

#: ★KB が「犯人」という変数に結合させている公開チャネルの**完全列挙**（行番号つき）。
#  `kb` サブコマンドが印字する。判定（層 R）の対象イベントはここから決めている。
KB_CHANNELS: tuple = (
    ("C1", "事件の発生／不発のアナウンス＋その瞬間の eligible（生存∧不安臨界以上）",
     "rules/00_rules_core.md:117-122 ／ rules/50_basic_tragedy_x.md:188",
     "その事件日に到達して初めて出る（＝回顧的）"),
    ("C2", "事件効果の着地（殺人＝犯人と同エリアの1人が死亡／自殺＝犯人が死亡／"
     "行方不明＝犯人が移動し犯人のボードに暗躍1／蝶の羽ばたき＝犯人と同エリアの1人にカウンター）",
     "rules/40_first_steps.md:148-153 ／ rules/50_basic_tragedy_x.md:191-220",
     "事件が発生して初めて出る（＝回顧的）"),
    ("C3", "黒猫の特性2＝黒猫が犯人の事件は効果が『何も起きない』に変わる",
     "rules/30_characters.md:75-77", "事件が発生して初めて出る（＝回顧的）"),
    ("C4", "★各事件の犯人は全て別キャラ（他の日の犯人が確定すると押し出される）",
     "rules/00_rules_core.md:123 ／ rules/60_faq_rulings.md:43 ／"
     " rules/70_script_creation_guide.md:179-180",
     "常時。ただし『他の日が1人に確定していること』が前提"),
    ("C5", "★神格の友好能力1＝**公開シートの事件1つ**の犯人開示（友好3・1/L）"
     "＝**未来の事件の犯人を名指しできる唯一の公開チャネル**",
     "rules/20_goodwill_abilities.md:147-151 ／ rules/30_characters.md:47",
     "神格が配役に居て、友好3を積めた時だけ（＝資源で買う）"),
    ("C6", "刑事の友好能力1＝**このループで発生した**事件1つの犯人開示（友好4・1/L）"
     "。未来の事件・不発の事件は選べない",
     "rules/20_goodwill_abilities.md:157-161 ／ rules/30_characters.md:35",
     "刑事が配役に居て友好4、かつ既に発生した事件だけ（＝回顧的かつ資源）"),
    ("C7", "手先の友好能力＝このループ中、犯人が手先の事件が発生しなくなる"
     "（使った上で発生したら犯人は手先ではない）",
     "rules/20_goodwill_abilities.md:257 ／ rules/30_characters.md:52",
     "手先が配役に居て能力を使った時だけ"),
    ("C8", "アルバイト？の特性＝配役ならびに事件の犯人かどうかがアルバイトと一致",
     "rules/30_characters.md:62", "常時（同定ではなく2人を束ねる向きに働く）"),
    ("C9", "教祖の特性＝自身が犯人の事件は効果を2回解決",
     "rules/30_characters.md:68", "事件が発生して初めて出る（＝回顧的）"),
    ("C10", "A.I. の特性②＝自身が犯人の事件の発生判定では全カウンターを不安として扱う",
     "rules/30_characters.md:50", "その事件日に到達して初めて出る（＝回顧的）"),
    ("C11", "★発生条件2＝犯人に**不安臨界以上**の不安が要る"
     "＝脚本家は犯人の不安を積まねばならない（間接・前向き）。"
     "★ただし KB 自身が**ダミー配置**を推奨している"
     "（「犯人でなくても不安を置けば犯人を隠せる」）",
     "rules/00_rules_core.md:119-121 ／ rules/70_script_creation_guide.md:101",
     "常時。ただし脚本家の行動由来＝**規則が保証する痕跡ではない**"),
)

#: 層 R が見る公開イベント（KB_CHANNELS の C1〜C10 の出所＝`sim/effects.py`・`sim/abilities.py`）。
_R_EVENTS = frozenset({"incident", "incident_effect", "death", "culprit_reveal",
                       "role_reveal", "entry", "revive", "incident_suppressed",
                       "ai_incident_effect", "protagonist_death", "guard_consumed"})


# ---------------------------------------------------------------------------
# 足跡（footprint）＝公開記録の中でそのキャラが現れる「位置」
# ---------------------------------------------------------------------------
def _mentions(node, name: str) -> tuple:
    """`node`（イベント dict 等）の中で `name` が値として現れる**経路**の列（空＝言及なし）。

    ★文字列の部分一致は使わない（`アルバイト` ⊂ `アルバイト？` の事故を避ける）＝
    完全一致のみ。リストは要素位置を潰して `[]` にする（並び順に依存させない）。
    """
    out: list[str] = []

    def walk(x, path: tuple) -> None:
        if isinstance(x, dict):
            for k in sorted(x):
                walk(x[k], path + (str(k),))
        elif isinstance(x, (list, tuple, set, frozenset)):
            for v in (sorted(x, key=repr) if isinstance(x, (set, frozenset)) else x):
                walk(v, path + ("[]",))
        elif isinstance(x, str) and x == name:
            out.append(".".join(path))

    walk(node, ())
    return tuple(sorted(out))


def _hist_fp(history: list, name: str, only: frozenset | None) -> tuple:
    """公開履歴の足跡＝(イベント通し番号, そのイベント内での出現経路) の列。

    `only` を渡すとその `event` 種だけを見る（層 R）。None＝全イベント（層 W）。
    ★通し番号を含める＝「a は 5 番目に出たが b は出ていない」を非対称として検出する。
    """
    out: list = []
    for i, e in enumerate(history):
        if only is not None and e.get("event") not in only:
            continue
        m = _mentions(e, name)
        if m:
            out.append((i, m))
    return tuple(out)


def _elig_now(ch: dict) -> bool | None:
    """今この瞬間 `unrest >= 不安臨界` か（`rules/00_rules_core.md:119-121` の発生条件2）。

    臨界が KB 未収録なら None（＝要確認。判定に使わず『不明』として持ち回る）。
    """
    th = unrest_threshold_of(ch.get("name"))
    if th is None:
        return None
    return int(ch.get("unrest", 0) or 0) >= int(th)


def _footprints(view: dict, names: list[str]) -> dict:
    """層 R / E / W の足跡を各候補について作る。"""
    hist = view.get("history") or []
    chars = {c.get("name"): c for c in (view.get("characters") or [])}
    places = view.get("placements") or []      # 今ターンの伏せ札（宛先は公開）
    out: dict = {}
    for n in names:
        ch = chars.get(n) or {"name": n}
        fp_r = _hist_fp(hist, n, _R_EVENTS)
        e_flag = _elig_now(ch)
        fp_e = (fp_r, e_flag)
        fp_w = (
            _hist_fp(hist, n, None),
            e_flag,
            bool(ch.get("alive", True)),
            ch.get("area"),
            int(ch.get("unrest", 0) or 0),
            int(ch.get("goodwill", 0) or 0),
            int(ch.get("anyaku", 0) or 0),
            ch.get("revealed_role"),
            _mentions(places, n),
            unrest_threshold_of(n),
            tuple(sorted(CHARACTER_FORBIDDEN.get(n, ()) or ())),
        )
        out[n] = {"R": fp_r, "E": fp_e, "W": fp_w}
    return out


def _classes(fps: dict, layer: str) -> list:
    """同じ足跡を持つ候補をまとめた同値クラス（大きい順）。"""
    g: dict = {}
    for n, d in fps.items():
        g.setdefault(repr(d[layer]), []).append(n)
    return sorted((sorted(v) for v in g.values()), key=lambda v: (-len(v), v))


# ---------------------------------------------------------------------------
# プローブ（挙動不変＝super() の戻り値の後で公開情報を控えるだけ）
# ---------------------------------------------------------------------------
class _Probe(HeuristicProtagonist):
    """`enumerate_threats` の**本番の引数**（`culprits`）を控え、公開情報だけで足跡を作る。

    ★`super().decide()` の返り値をそのまま返す＝**決定に一切触れない**（b150 と同じ作法）。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.seats: list[dict] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        import agents.defense_plan as dp

        orig = dp.enumerate_threats
        cap: dict = {}

        def _rec(*a, **kw):
            r = orig(*a, **kw)
            cap["kw"] = dict(kw)          # ★最後の呼び出し＝本番の引数
            cap["threats"] = list(r)      # ★本番が実際に立てた脅威（価値軸 V1 の材料）
            return r

        dp.enumerate_threats = _rec
        try:
            chosen = super().decide(view, decision, options)
        finally:
            dp.enumerate_threats = orig
        if "kw" not in cap:
            return chosen                  # プランナーが走らなかった席
        try:
            self.seats.append(self._observe(view, cap["kw"],
                                            cap.get("threats") or []))
        except Exception as e:             # noqa: BLE001
            self.seats.append({"loop": view.get("loop"), "day": view.get("day"),
                               "seat": view.get("seat"), "error": repr(e)})
        return chosen

    # -- 観測（純粋に公開情報だけ） -------------------------------------------
    def _observe(self, view: dict, kw: dict, threats: list) -> dict:
        cul = kw.get("culprits") or {}
        chars = {c.get("name"): c for c in (view.get("characters") or [])}
        cast = sorted(chars)
        d0 = int(view.get("day", 1) or 1)
        hist = view.get("history") or []
        row = {"loop": view.get("loop"), "day": d0, "seat": view.get("seat"),
               "n_cast": len(cast), "cases": [],
               "alive_now": sorted(n for n in cast
                                   if chars[n].get("alive", True))}
        # 「その事件日に既に到達したことがあるか」＝回顧チャネル C1-C3/C9/C10 の可用性
        seen_days = {e.get("day") for e in hist if e.get("event") == "incident"}
        # ★価値軸 V1＝**本番のプランナーがその事件について致命脅威を立てたか**
        #   （＝AI 自身の値踏み。二重実装しない＝`enumerate_threats` の返り値をそのまま読む）。
        #   事件族の脅威ラベル／条件noteには**事件名がそのまま入る**
        #   （`agents/defense_plan.py:1452 / :1580 / :1624 / :1675 / :1147`）。
        inc_names = {i.get("name") for i in (view.get("incidents") or ())
                     if i.get("name")}
        raised: set = set()
        sev_by_name: dict = {}
        for t in threats:
            if not getattr(t, "fatal", False):
                continue
            blob = str(getattr(t, "label", "")) + " " + " ".join(
                str(getattr(cd, "note", "") or "") + str(getattr(cd, "label", ""))
                for cd in (getattr(t, "conditions", ()) or ()))
            for nm in inc_names:
                if nm and nm in blob:
                    raised.add(nm)
                    sev_by_name[nm] = max(sev_by_name.get(nm, 0.0),
                                          float(getattr(t, "severity", 0.0)))
        for inc in (view.get("incidents") or []):
            dy = inc.get("day")
            if dy is None or dy < d0:
                continue                   # 今日以降の事件日だけ（B-150 §3-5 と同じ母集団）
            raw = set(cul.get(dy) or ())
            live = sorted(n for n in raw if chars.get(n, {}).get("alive", True))
            case = {
                "inc_day": dy, "inc_name": inc.get("name"),
                "n_raw": len(raw), "n_alive": len(live),
                # ★観測ゼロ＝候補集合が**キャスト全員のまま**＝制約が1つも掛かっていない
                "zero_obs": len(raw) == len(cast),
                # ★その事件日に過去（このループ含む）到達したことがあるか
                "day_seen": dy in seen_days,
                "cands": live,
                "raw_all": sorted(raw),
                # ★価値軸 V1＝AI がこの事件について致命脅威を立てたか＋その severity
                "v1_raised": inc.get("name") in raised,
                "v1_sev": round(sev_by_name.get(inc.get("name"), 0.0), 4),
            }
            # ★N_elig / N_reach＝『同定』ではなく『今日発火しうる候補』の数
            #   （`rules/00_rules_core.md:119-121` の発生条件2）
            for k in (0, 1, 2):
                hit = []
                unk = 0
                for n in live:
                    th = unrest_threshold_of(n)
                    if th is None:
                        unk += 1
                        continue
                    if int(chars[n].get("unrest", 0) or 0) >= th - k:
                        hit.append(n)
                case[f"n_reach{k}"] = len(hit)
                case[f"n_unknown_th{k}"] = unk
                if k == 0:
                    case["elig"] = hit
            # ★★「同定せずに折る」の可否＝eligible な候補を不安-1 で臨界未満へ落とせるか。
            #   `unrest-1 < 臨界` ⇔ `unrest == 臨界`（かつ 臨界≥1。臨界0＝黒猫は
            #   カウンター0でも条件成立＝**原理的に冷やせない**＝`rules/30_characters.md:77`）。
            fix, stuck = [], []
            for n in (case["elig"] or ()):
                th = unrest_threshold_of(n)
                u = int(chars[n].get("unrest", 0) or 0)
                (fix if (th is not None and th >= 1 and u <= th)
                 else stuck).append(n)
            case["n_fixable"] = len(fix)
            case["n_stuck"] = len(stuck)
            case["stuck"] = sorted(stuck)
            case["kuroneko_cand"] = "黒猫" in live
            # ★予測子の材料（すべて公開情報）：
            #   (a) 候補内で不安が最大の者（同点は全員＝棄権扱いにする）
            #   (b) 相手が札を宛てた回数（`cards_revealed`＝`sim/flow.py:204` は全6枚公開／
            #       今ターンの伏せ札の宛先も公開＝`sim/views.py:41-49`）
            us = {n: int(chars[n].get("unrest", 0) or 0) for n in live}
            mx = max(us.values()) if us else 0
            case["argmax_unrest"] = sorted(n for n in live if us[n] == mx)
            mmc = self._mm_card_counts(view, live)
            case["mm_cards"] = mmc
            mc = max(mmc.values()) if mmc else 0
            case["argmax_mm"] = (sorted(n for n in live if mmc[n] == mc)
                                 if mc > 0 else [])
            if len(live) >= 2:
                fps = _footprints(view, live)
                for lay in ("R", "E", "W"):
                    cls = _classes(fps, lay)
                    case[f"cls_{lay}"] = len(cls)
                    case[f"top_{lay}"] = len(cls[0]) if cls else 0
                    case[f"parts_{lay}"] = cls
            row["cases"].append(case)
        return row

    @staticmethod
    def _mm_card_counts(view: dict, names: list[str]) -> dict:
        """相手（脚本家）が各候補に札を宛てた回数（★公開情報だけ）。

        - 過去＝`cards_revealed`（`sim/flow.py:204`＝行動解決で全6枚公開・KB 00:106）。
        - 今ターン＝`view["placements"]`（宛先と持ち主は公開・中身は自席分のみ＝
          `sim/views.py:41-49`）。
        ★belief はこのチャネルを使っていない（B-147 §4 が同じチャネルを**フレンド**で測って
          無情報と判定した。本レーンは**犯人**について測り直す）。
        """
        cnt = {n: 0 for n in names}
        for e in (view.get("history") or ()):
            if e.get("event") != "cards_revealed":
                continue
            for p in (e.get("placements") or ()):
                if p.get("owner") == "mastermind" and p.get("target") in cnt:
                    cnt[p["target"]] += 1
        for p in (view.get("placements") or ()):
            if p.get("owner") == "mastermind" and p.get("target") in cnt:
                cnt[p["target"]] += 1
        return cnt


#: ループ終了時の敗北に**直結する死**（`sim/effects.py` の loop_end 効果／KB 40:126-133）。
_VIP_ROLES = ("キーパーソン", "フレンド")


def _incident_value(state, script, rule_y, board_x_by_loop) -> dict:
    """★価値軸 V2／V3（ground truth）＝**その事件が実際に敗北条件へ触れたか**。

    ユーザー実戦報告（2026-08-04）＝「2日目と5日目はほぼ意味がない事件だったので、犯人は
    絞ろうともしていない」＝手練れは**守る価値のある事件の犯人だけ**を絞る。∴ B-150 の
    S2（930／1389 件）の母数に「守る価値のない事件」がどれだけ混ざっているかを測る。

    返り値 {day: {...}}：
      - `fired`   … その事件が発生した (loop,day) の回数
      - `v2`      … True＝発生した回のうち1回でも**敗北条件に触れる帰結**が観測された／
                    False＝発生したが一度も触れなかった／None＝一度も発生していない（判定不能）
      - `v3`      … True＝発生した回のうち1回でも**そのループが敗北で終わった**（★交絡あり）
    ★「敗北条件に触れる帰結」の定義（**事件フェイズに発行された公開/秘匿イベントだけ**を見る）：
      (a) VIP（キーパーソン／フレンド）の死亡、(b) 主人公の死亡、(c) `loop_end` の誘発、
      (d) **そのループの真の敗北板**（`arena/b146_probe._true_boards` 狭義）の暗躍が増えた、
      (e) 未来改変プラン下での蝶の羽ばたきの発生（`rules/50_basic_tragedy_x.md:219-220`）。
    ★限界＝**発生しなかった事件は判定できない**（None）。因果ではなく併発の観測である。
    """
    roles = {n: script.role_of(n) for n in script.cast}
    lost: set = set()
    for e in state.history:
        if e.get("event") == "loop_end" and "敗北" in str(e.get("reason") or ""):
            lost.add(e.get("loop"))
    out: dict = {}
    for inc in script.incidents:
        out[inc.day] = {"name": inc.name, "fired": 0, "harm": 0, "lost": 0}
    # 事件フェイズのイベントを (loop,day) 単位に束ねる（公開＋秘匿の両方）
    buckets: dict = {}
    for e in list(state.history) + list(state.secret_log):
        if e.get("phase") != "incident":
            continue
        buckets.setdefault((e.get("loop"), e.get("day")), []).append(e)
    for (lp, dy), evs in buckets.items():
        row = out.get(dy)
        if row is None:
            continue
        if not any(e.get("event") == "incident" and e.get("occurs") for e in evs):
            continue
        row["fired"] += 1
        strict, _wide = _true_boards(rule_y, board_x_by_loop.get(lp), False)
        harm = False
        for e in evs:
            ev = e.get("event")
            if ev == "death" and roles.get(e.get("name")) in _VIP_ROLES:
                harm = True                      # (a)
            elif ev == "protagonist_death":
                harm = True                      # (b)
            elif ev == "loop_end":
                harm = True                      # (c)
            elif (ev == "anyaku" and int(e.get("delta", 0) or 0) > 0
                  and e.get("target") in strict):
                harm = True                      # (d)
            elif (row["name"] == "蝶の羽ばたき"
                  and rule_y == "未来改変プラン"):
                harm = True                      # (e)
        row["harm"] += int(harm)
        row["lost"] += int(lp in lost)
    for d, row in out.items():
        row["v2"] = None if row["fired"] == 0 else bool(row["harm"])
        row["v3"] = None if row["fired"] == 0 else bool(row["lost"])
    return out


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Probe(seed)
    mm = _MMProbe(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    # ★ground truth＝脚本の犯人（非公開シート＝`sim/state.Incident.culprit`）。
    #   本計測は**判定にだけ使い、足跡には一切混ぜていない**（カンニング防止）。
    truth = {i.day: i.culprit for i in script.incidents}
    val = _incident_value(state, script, mm.rule_y, mm.board_x_by_loop)
    for row in hp.seats:
        for c in row.get("cases", ()):
            v = val.get(c["inc_day"]) or {}
            c["v2"] = v.get("v2")
            c["v3"] = v.get("v3")
            c["v_fired"] = v.get("fired", 0)
    for row in hp.seats:
        for c in row.get("cases", ()):
            t = truth.get(c["inc_day"])
            c["truth"] = t
            # ★真の犯人がその席の時点で**生存していたか**（`raw_all` は belief の生の候補集合）。
            #   死亡していれば発生条件1（`rules/00_rules_core.md:120`）で事件は起こらない＝
            #   「候補に居ない」のは正しい。**belief の健全性違反はこれと分けて数える**。
            c["truth_alive"] = t in (row.get("alive_now") or ())
            c["truth_in_raw"] = t in (c.get("raw_all") or ())
            c["truth_in"] = (t in (c.get("cands") or ()))
    return {"outcome": _outcome(state), "seats": hp.seats, "truth": truth}


def _verify_game(script, seed: int, loops: int = 8) -> tuple:
    """プローブ有無で結末が一致するか（挙動不変の物証）。"""
    probe = replace(script, loops=loops)
    a = _Probe(seed)
    sa, _ = run_game(probe, {"mastermind": _MMProbe(seed),
                             "p1": a, "p2": a, "p3": a})
    b = HeuristicProtagonist(seed)
    sb, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": b, "p2": b, "p3": b})
    ha = [(e.get("loop"), e.get("day"), e.get("event")) for e in sa.history]
    hb = [(e.get("loop"), e.get("day"), e.get("event")) for e in sb.history]
    return (sa.winner == sb.winner and sa.loop_no == sb.loop_no and ha == hb,
            _outcome(sa), _outcome(sb))


def _switches(days: int, loops: int) -> str:
    import agents.defense_plan as dp
    H = HeuristicProtagonist
    return (f"[切替口] B141B_UNLOCK_SAME_DAY={H.B141B_UNLOCK_SAME_DAY}"
            f" / B143_YIELD={H.B143_YIELD} / B142_RESERVE={H.B142_RESERVE}"
            f" / B100_MIX={H.B100_MIX}"
            f" / B145_EVADE_MAX_FRIENDS={H.B145_EVADE_MAX_FRIENDS}"
            f" / B146_ODB_TIEBREAK_BOARD_LOSS_ONLY="
            f"{getattr(H, 'B146_ODB_TIEBREAK_BOARD_LOSS_ONLY', '—')}"
            f" / DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" / B134_CARD_DISTANCE={dp.B134_CARD_DISTANCE}"
            f" / B127_ANYAKU_TARGETING={dp.B127_ANYAKU_TARGETING}"
            f" / _SUSPECT_P={dp._SUSPECT_P} / _LIKELY_P={dp._LIKELY_P}"
            f" / B151 切替口=無し（計測のみ・agents/ 非接触）"
            f" / days={days} loops={loops}")


# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()
    dist = Counter()          # 生存候補数の分布（★B-150 §3-5 との外部整合）
    dist_r: dict = {0: Counter(), 1: Counter(), 2: Counter()}
    split = Counter()         # (層, 判定) -> 件
    by_seen = Counter()       # (day_seen, 層R判定) -> 件
    breakers = Counter()      # 層Eで割れた席＝何が非対称だったか
    pred = Counter()          # ★予測子の当たり外れ（層Eで割れた席）
    strat = Counter()         # ★★価値軸で層別した (定義, 値, 指標) -> 件
    scripts: set = set()
    per_game: list[dict] = []
    ex: list[dict] = []

    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts.add(name)
        res = audit_game(sc, seed, loops=loops)
        g = Counter()
        g["games"] = 1
        for row in res["seats"]:
            g["seats"] += 1
            if row.get("error"):
                g["seat_error"] += 1
                continue
            for cs in row.get("cases", ()):
                g["cases"] += 1
                na = cs["n_alive"]
                dist[na if na <= 4 else "5+"] += 1
                for k in (0, 1, 2):
                    v = cs[f"n_reach{k}"]
                    dist_r[k][v if v <= 4 else "5+"] += 1
                    if cs[f"n_unknown_th{k}"]:
                        g[f"th_unknown{k}"] += cs[f"n_unknown_th{k}"]
                # ★自己検査（本物の健全性違反）＝**真の犯人が生存しているのに**
                #   belief の生の候補集合から落ちている＝belief のバグ。
                if cs.get("truth_alive") and cs.get("truth_in_raw") is False:
                    g["truth_dropped"] += 1
                    if len(ex) < 60:
                        ex.append({"kind": "★健全性違反", "script": name,
                                   "seed": seed, **{k: cs.get(k) for k in
                                                    ("inc_day", "inc_name",
                                                     "raw_all", "truth")}})
                if not cs.get("truth_alive"):
                    # 真の犯人が既に死亡＝発生条件1が満たせない＝この事件は起こらない
                    g["truth_dead"] += 1
                # ★★価値軸（3定義）で層別＝「そもそも守る価値があったのか」
                #   ユーザー実戦報告 2026-08-04：手練れは価値ある事件の犯人だけ絞る。
                for dfn, val in (
                        ("V1 AI が致命脅威を立てた", cs.get("v1_raised")),
                        ("V1s 同上かつ severity≥0.30",
                         bool(cs.get("v1_raised")) and cs.get("v1_sev", 0) >= 0.30),
                        ("V2 発生時に敗北条件へ触れた", cs.get("v2")),
                        ("V3 発生した回にループが敗北", cs.get("v3"))):
                    key = ("該当" if val is True else
                           ("非該当" if val is False else "判定不能"))
                    strat[(dfn, key, "全事件日")] += 1
                    if na == 1:
                        strat[(dfn, key, "★候補ちょうど1人")] += 1
                    if na >= 2:
                        strat[(dfn, key, "★候補2人以上＝S2")] += 1
                        if cs["cls_R"] == 1:
                            strat[(dfn, key, "　うち層R交換可能")] += 1
                        if cs["cls_E"] == 1:
                            strat[(dfn, key, "　うち層E交換可能")] += 1
                        if cs.get("kuroneko_cand"):
                            strat[(dfn, key, "　うち黒猫が候補")] += 1
                if cs.get("zero_obs"):
                    g["zero_obs"] += 1
                if cs.get("day_seen"):
                    g["day_seen"] += 1
                if na < 2:
                    g["n_lt2"] += 1
                    continue
                g["n_ge2"] += 1
                # ★★『同定せずに折れるか』＝eligible を全員冷やせるか（母数＝候補2人以上）
                if cs["n_reach0"] == 0:
                    g["fold_free"] += 1          # 今日は誰も臨界に届いていない
                elif cs["n_stuck"] == 0:
                    g[f"fold_{min(cs['n_fixable'], 4)}cards"] += 1
                else:
                    g["fold_stuck"] += 1         # 冷やせない候補が居る（臨界0 or 超過）
                    if "黒猫" in (cs.get("stuck") or ()):
                        g["stuck_kuroneko"] += 1
                    if [n for n in (cs.get("stuck") or ()) if n != "黒猫"]:
                        g["stuck_over"] += 1     # 既に臨界を2以上超過
                if cs.get("kuroneko_cand"):
                    g["kuroneko_cand"] += 1
                for lay in ("R", "E", "W"):
                    tag = "交換可能（1クラス）" if cs[f"cls_{lay}"] == 1 else "割れる（2クラス以上）"
                    split[(lay, tag)] += 1
                by_seen[(bool(cs.get("day_seen")),
                         "1クラス" if cs["cls_R"] == 1 else "2+")] += 1
                # ★層Eで割れた席＝非対称の出所を分ける（R で既に割れていたか／eligible か）
                if cs["cls_E"] >= 2:
                    src = ("規則結合の記録（層R）で既に割れていた"
                           if cs["cls_R"] >= 2 else
                           "★eligible（unrest≥臨界）だけが割っている")
                    breakers[src] += 1
                # ★予測子の評価は**候補2人以上の全席**で行う（選択バイアスを入れない＝
                #   B-147 §4-2 の教訓＝「割れた席だけ」で測ると当たって見える）。
                _score_predictors(cs, pred)
        for k, v in g.items():
            c[k] += v
        per_game.append({"script": name, "seed": seed,
                         "outcome": res["outcome"], **dict(g)})
        if verbose:
            print(f"  {name} s{seed}: 席{g['seats']} 事件日{g['cases']}"
                  f" 候補2以上{g.get('n_ge2', 0)}", flush=True)
    return {"days": days, "counts": dict(c), "per_game": per_game,
            "scripts": sorted(scripts), "n_scripts": len(scripts),
            "n_games": len(per_game),
            "dist_alive": {str(k): v for k, v in dist.items()},
            "dist_reach": {str(k): {str(a): b for a, b in v.items()}
                           for k, v in dist_r.items()},
            "split": {f"{k[0]}|{k[1]}": v for k, v in split.items()},
            "by_seen": {f"{k[0]}|{k[1]}": v for k, v in by_seen.items()},
            "strat": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in strat.items()},
            "breakers": dict(breakers), "pred": dict(pred), "examples": ex}


def _score_predictors(cs: dict, pred: Counter) -> None:
    """★層Eで割れた席で、非対称チャネルが**真の犯人を指しているか**を測る（両方向）。

    予測子＝「eligible（unrest≥臨界）な候補」。**その集合の大きさが1のときだけ**
    的中/外れを数える（複数なら『指していない』＝abstain に計上）。
    帰無＝1/|候補| の期待値を積む（B-147 §4-2 と同じ作法）。
    """
    truth = cs.get("truth")
    n = cs["n_alive"]
    if not n or truth is None or not cs.get("truth_in"):
        # ★真の犯人が候補集合に居ない席は母数から外す（当てようが無い＝比較が壊れる）。
        #   大半は「真の犯人が既に死亡」＝発生条件1（`rules/00_rules_core.md:120`）を
        #   満たせない席であり、そもそも守る必要が無い。
        pred["母数外（真が候補外）"] += 1
        if not cs.get("truth_alive"):
            pred["　うち真の犯人が死亡済み"] += 1
        return
    pred["母数"] += 1
    pred["帰無の期待値×1000"] += int(round(1000.0 / n))
    for tag, sel in (("P1 eligible（unrest≥臨界）", cs.get("elig") or []),
                     ("P2 候補内で不安が最大", cs.get("argmax_unrest") or []),
                     ("P3 相手が最も札を宛てた", cs.get("argmax_mm") or [])):
        if len(sel) == 1:
            pred[f"{tag}｜断定"] += 1
            pred[f"{tag}｜" + ("的中" if sel[0] == truth else "外れ")] += 1
        else:
            pred[f"{tag}｜棄権（0人 or 複数）"] += 1
        # 再現率＝真の犯人がその集合に入っているか（断定できなくても意味がある）。
        # ★帰無＝同じ大きさの部分集合を無作為に取ったときの期待値 |sel|/|候補|。
        pred[f"{tag}｜真を含む帰無×1000"] += int(round(1000.0 * len(sel) / n))
        if truth in sel:
            pred[f"{tag}｜真を含む"] += 1


def merge(parts: list[dict]) -> dict:
    out: dict = {"days": parts[0].get("days"), "counts": Counter(),
                 "per_game": [], "scripts": set(),
                 "dist_alive": Counter(), "split": Counter(), "by_seen": Counter(),
                 "breakers": Counter(), "pred": Counter(), "examples": [],
                 "strat": Counter(),
                 "dist_reach": {"0": Counter(), "1": Counter(), "2": Counter()}}
    for p in parts:
        for k in ("counts", "dist_alive", "split", "by_seen", "breakers",
                  "pred", "strat"):
            out[k].update(p.get(k) or {})
        for k, v in (p.get("dist_reach") or {}).items():
            out["dist_reach"].setdefault(str(k), Counter()).update(v or {})
        out["per_game"].extend(p.get("per_game") or [])
        out["scripts"].update(p.get("scripts") or [])
        out["examples"].extend(p.get("examples") or [])
    for k in ("counts", "dist_alive", "split", "by_seen", "breakers", "pred"):
        out[k] = dict(out[k])
    out["dist_reach"] = {k: dict(v) for k, v in out["dist_reach"].items()}
    out["scripts"] = sorted(out["scripts"])
    out["n_scripts"] = len(out["scripts"])
    out["n_games"] = len(out["per_game"])
    return out


def _pc(v: int, tot: int) -> str:
    return f"{v}({100.0 * v / tot:.1f}%)" if tot else f"{v}(—)"


def report(res: dict, days: int) -> None:
    c = res["counts"]
    nge = c.get("n_ge2", 0)
    ncs = c.get("cases", 0)
    print(f"== B-151：犯人は原理的に絞れるか（{days}日級 {res['n_games']}局"
          f"・★独立脚本 {res['n_scripts']} 本・set_card席 {c.get('seats', 0)}）==")
    print(f"  脚本: {', '.join(res['scripts'])}")
    print("")
    print("  ★自己検査（0 が正常）")
    print(f"    席エラー = {c.get('seat_error', 0)}"
          f"／★★健全性違反（真の犯人が**生存しているのに** belief の候補集合から落ちた）= "
          f"{c.get('truth_dropped', 0)}"
          f"／不安臨界が KB 未収録の候補 = {c.get('th_unknown0', 0)}")
    print(f"    （参考＝真の犯人が既に死亡していた事件日 = "
          f"{_pc(c.get('truth_dead', 0), ncs)}"
          f"＝発生条件1〔rules/00_rules_core.md:120〕を満たせない＝守る必要が無い席）")
    print("")
    print(f"  今日以降の事件日（席×日） = {ncs} 件"
          f"／うち候補が2人以上 = {nge}／1人以下 = {c.get('n_lt2', 0)}")
    print(f"    ★観測ゼロ（候補集合がキャスト全員のまま＝制約が1つも掛かっていない） = "
          f"{_pc(c.get('zero_obs', 0), ncs)}")
    print(f"    ★その事件日に過去到達済み（回顧チャネル C1-C3/C9/C10 が使える） = "
          f"{_pc(c.get('day_seen', 0), ncs)}")
    print("")
    print("  ★★生存候補数の分布（B-150 §3-5 と同じ母数＝外部整合の照合先）")
    tot = sum(res["dist_alive"].values()) or 1
    print("    " + "  ".join(f"{k}人={_pc(v, tot)}" for k, v in
                             sorted(res["dist_alive"].items(),
                                    key=lambda x: (x[0] == "5+", x[0]))))
    print("")
    print("  ★★『今日発火しうる候補』の数の分布"
          "（`rules/00_rules_core.md:119-121`＝発生条件2。★同定ではなく発火可能性）")
    for k in ("0", "1", "2"):
        d = res["dist_reach"].get(k) or {}
        t2 = sum(d.values()) or 1
        lbl = ("unrest≥臨界（今すでに発火可能）" if k == "0"
               else f"unrest≥臨界−{k}（脚本家が+{k}で届かせうる）")
        print(f"    {lbl:34s} " + "  ".join(
            f"{a}人={_pc(b, t2)}" for a, b in
            sorted(d.items(), key=lambda x: (x[0] == "5+", x[0]))))
    _strat_report(res)
    print("")
    print("  ★★★『犯人を同定せずに折れるか』＝eligible な候補を全員 不安-1 で臨界未満へ"
          f"（候補2人以上の {nge} 件が母数。`rules/00_rules_core.md:119-121` 発生条件2）")
    for key, lbl in (("fold_free", "今日は eligible が0人＝今日は発生しない"),
                     ("fold_1cards", "★不安-1 **1枚**で全 eligible を落とせる"),
                     ("fold_2cards", "不安-1 2枚で落とせる"),
                     ("fold_3cards", "不安-1 3枚で落とせる"),
                     ("fold_4cards", "不安-1 4枚以上が要る"),
                     ("fold_stuck", "★冷やせない候補が居る（臨界0＝黒猫／既に臨界超過）")):
        print(f"    {lbl:44s} {_pc(c.get(key, 0), nge)}")
    print(f"      うち黒猫が原因 = {c.get('stuck_kuroneko', 0)}"
          f"／臨界超過が原因 = {c.get('stuck_over', 0)}")
    print(f"    （参考＝候補に黒猫が含まれる事件日 = {_pc(c.get('kuroneko_cand', 0), nge)}"
          f"。黒猫は不安臨界0＝カウンター0でも条件成立"
          f"＝**原理的に冷やせない**〔rules/30_characters.md:77〕）")
    print("")
    print(f"  ★★★交換可能性の判定（候補2人以上の {nge} 件が母数）")
    for lay, ttl in (("R", "層R＝KB が犯人に結合させた公開記録だけ（C1-C10）"),
                     ("E", "層E＝R ＋ unrest≥臨界の真偽（C11）"),
                     ("W", "層W＝全公開情報（上限・規則結合でない差も含む）")):
        ok = res["split"].get(f"{lay}|交換可能（1クラス）", 0)
        ng = res["split"].get(f"{lay}|割れる（2クラス以上）", 0)
        print(f"    {ttl}")
        print(f"      ★原理的に絞れない（全候補が1クラス） = {_pc(ok, nge)}"
              f"　｜推論の余地あり（2クラス以上） = {_pc(ng, nge)}")
    print("")
    print("  ★層R の判定 × その事件日に到達済みか（回顧チャネルの可用性との関係）")
    for seen in (False, True):
        a = res["by_seen"].get(f"{seen}|1クラス", 0)
        b = res["by_seen"].get(f"{seen}|2+", 0)
        lbl = "到達済み（回顧チャネルあり）" if seen else "未到達（回顧チャネル無し）"
        print(f"    {lbl:28s} 1クラス={a}  2クラス以上={b}"
              f"（計 {a + b}）")
    print("")
    if res.get("breakers"):
        print("  ★層E で割れた席＝非対称の出所")
        for k, v in sorted(res["breakers"].items(), key=lambda x: -x[1]):
            print(f"    {v:7d}  {k}")
    p = res.get("pred") or {}
    base = p.get("母数", 0)
    if base:
        print("")
        print("  ★★公開情報の予測子は真の犯人を当てられるか"
              "（★候補2人以上の**全席**＝選択バイアス無し。B-147 §4-2 の作法）")
        null = p.get("帰無の期待値×1000", 0) / 1000.0 / base
        print(f"    母数 = {base} 件（真が候補外で除外 = "
              f"{p.get('母数外（真が候補外）', 0)}）／★帰無（候補から無作為に1人）= {null:.3f}")
        for tag in ("P1 eligible（unrest≥臨界）", "P2 候補内で不安が最大",
                    "P3 相手が最も札を宛てた"):
            dec = p.get(f"{tag}｜断定", 0)
            hit = p.get(f"{tag}｜的中", 0)
            mis = p.get(f"{tag}｜外れ", 0)
            inc = p.get(f"{tag}｜真を含む", 0)
            inull = p.get(f"{tag}｜真を含む帰無×1000", 0) / 1000.0 / base
            acc = (hit / dec) if dec else 0.0
            print(f"    {tag:26s} 断定 {dec:5d}（全体の"
                  f"{100.0 * dec / base:4.1f}%）｜的中 {hit:5d} / 外れ {mis:5d}"
                  f" ＝ **{acc:.3f}**（帰無 {null:.3f}）"
                  f"｜真を含む {inc}/{base}＝**{inc / base:.3f}**（帰無 {inull:.3f}）")
    if res.get("examples"):
        print("")
        print("  ★自己検査に引っかかった例（先頭12）")
        for e in res["examples"][:12]:
            print(f"    {e}")


def _strat_report(res: dict) -> None:
    """★★第3の軸＝「その事件はそもそも守る価値があったのか」で S2 を層別する。

    発端＝ユーザー実戦報告（2026-08-04・5日級 `random_FS` seed 4 を手練れが3ループで防衛）：
    **「2日目と5日目はほぼ意味がない事件だったので、犯人は絞ろうともしていない」**。
    ★1局の観察＝一般則にしない（規約 §11b）。**4定義で測って感度を出す**。
    """
    st = res.get("strat") or {}
    if not st:
        return
    print("")
    print("  ★★★【第3の軸】その事件はそもそも守る価値があったのか"
          "（★定義依存＝4定義で感度を出す。ユーザー実戦報告 2026-08-04 発）")
    rows = ("全事件日", "★候補ちょうど1人", "★候補2人以上＝S2",
            "　うち層R交換可能", "　うち層E交換可能", "　うち黒猫が候補")
    for dfn in ("V1 AI が致命脅威を立てた", "V1s 同上かつ severity≥0.30",
                "V2 発生時に敗北条件へ触れた", "V3 発生した回にループが敗北"):
        tot_all = sum(st.get(f"{dfn}|{k}|全事件日", 0)
                      for k in ("該当", "非該当", "判定不能"))
        s2_all = sum(st.get(f"{dfn}|{k}|★候補2人以上＝S2", 0)
                     for k in ("該当", "非該当", "判定不能"))
        print(f"\n    ◆ {dfn}（全事件日 {tot_all}／うち S2 {s2_all}）")
        print(f"      {'指標':22s}" + "".join(f"{k:>14s}"
                                              for k in ("該当", "非該当", "判定不能")))
        for r in rows:
            vals = [st.get(f"{dfn}|{k}|{r}", 0) for k in ("該当", "非該当", "判定不能")]
            print(f"      {r:22s}" + "".join(f"{v:>14d}" for v in vals))
        hit = st.get(f"{dfn}|該当|★候補2人以上＝S2", 0)
        print(f"      ★『価値あり』に限った S2 = {hit} 件"
              f"（S2 全体 {s2_all} の {100.0 * hit / s2_all if s2_all else 0:.1f}%）")


def kb_report() -> None:
    print("== B-151：KB が『犯人』に結合させている公開チャネルの完全列挙 ==")
    print("（判定の層 R はここの C1〜C10 を対象イベントにしている）")
    for cid, desc, src, when in KB_CHANNELS:
        print(f"\n  [{cid}] {desc}")
        print(f"       出典 : {src}")
        print(f"       可用 : {when}")
    print("\n  ★要旨＝**C1〜C3・C6・C9・C10 はすべて『その事件日に到達してから』**しか出ない"
          "（回顧的）。")
    print("  ★**未来の事件の犯人を名指しできる公開チャネルは C5（神格・友好3・1/L）だけ**"
          "＝資源で買う道であり、盤面が勝手に教えてくれる痕跡ではない。")
    print("  ★C11（不安の積み上がり）は規則が保証する痕跡ではない"
          "＝KB 自身がダミー配置を推奨している（rules/70_script_creation_guide.md:101）。")
    print("  ★C4（各事件の犯人は全て別キャラ・rules/00_rules_core.md:123）は"
          "belief が既に消費している（agents/belief.py:2141-2146 の singles ループ）。")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "merge", "kb"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inputs", nargs="*", default=())
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "kb":
        kb_report()
        return 0
    print(_switches(a.days, a.loops), flush=True)
    if a.cmd == "verify":
        from arena.benchmark import benchmark_scripts

        bad = 0
        for name, seed, sc in list(benchmark_scripts(days=a.days))[
                a.start:(a.end if a.end is not None else 12)]:
            ok, oa, ob = _verify_game(sc, seed, loops=a.loops)
            if not ok:
                bad += 1
                print(f"  ✗ {name} s{seed}: probe={oa} plain={ob}")
        print(f"不一致 = {bad} 件")
        return 1 if bad else 0
    if a.cmd == "merge":
        parts = []
        for p in a.inputs:
            with open(p, encoding="utf-8") as f:
                parts.append(json.load(f))
        res = merge(parts)
    else:
        res = run(days=a.days, loops=a.loops, start=a.start, end=a.end,
                  verbose=a.verbose)
    report(res, a.days)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
