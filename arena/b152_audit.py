# -*- coding: utf-8 -*-
"""B-152 Phase 2：**脚本家AIの疑問手2件の射程を数える**（★計測のみ・`agents/` 非接触）。

## 発端

`docs/バックログ_構想メモ_FableA.md` §28（B-152）＝ユーザーが 5日級 `random_FS` seed 4 を
主人公側でプレイした際の観察2件：

- **22a**＝「キーパーソンの暗躍を3にする」（打点ゼロの積み増しの疑い）
- **22b**＝「意味のない最終日の事件のためにご神木に不安を積み続ける」（価値ゼロの事件への投資の疑い）

Phase 1（KB確定）の結論は `docs/監査_B152_脚本家の疑問手2件_2026-08-04.md` §1-2。要点だけ：

- **22a**＝KP暗躍の閾値は KB 上「2」だけ。**ただし A-48/A-62 が既に上限会計を実装済み**
  （`agents/heuristic.py:2798-2801`＝除去役生存で cap=3・不在で cap=2）。
  ∴ 3 は「バグ」ではなく**設計が許した上限**。★残る穴は cap=3 の発動条件が**生存だけ**を見ており、
  **その除去を脚本家が拒否できるか**（`rules/00_rules_core.md:110`）を見ていないこと。
- **22b**＝最終日の事件は一般には有価値（フェイズ順 7 事件 → 9 ターン終了 → ループ終了処理）。
  **ただし KB から「打点ゼロが確定する4類型」が引ける**（流布×TT不在／不安拡大×非契約Y／
  自殺×非VIP犯人／黒猫が犯人）。

## 本計測が出す数（★定義を先に固定してから数える＝B-157 §1-2 の流儀）

### 22a（KP暗躍）

- **事象**＝KP の暗躍が増えたフェイズ遷移。遷移は**スナップショット境界**で切る（`sim/flow.py:151-300`）：
  `card`（脚本家行動前→行動解決後）／`mm_ability`（→脚本家能力後）／`goodwill`（→主人公能力後）／
  `incident`（→事件フェイズ後）／`turn_end`（→ターン終了後）。
- **over枚数** = `max(0, post - max(2, pre))` ＝ **KB閾値2を超えて積まれた枚数**（§1-1）。
- **over席** = over枚数 ≥ 1 の事象。層別：
  - `cap`＝`3 if 転校生/神格/鑑識官 が生存 else 2`（`agents/heuristic.py:739-741,2798-2801` の再現）
  - `cap内`（post ≤ cap＝設計どおりのバッファ）／`cap超`（post > cap＝A-62 が止め損ねた分）
  - ★★`根拠なし`＝**生存する除去役が全員「友好無視/絶対友好無視」を持つ**（＝脚本家は常に拒否できる
    ＝`agents/heuristic.py:2001-2004` は必ず拒否する）＝**バッファの前提が成立しない席**。
- **回収の実測**＝同ループ中に KP 暗躍の**除去が実際に起きたか**（goodwill 遷移で減ったか）／
  **拒否が起きたか**（`goodwill_refused`）／ループ終了時の KP 暗躍／KP暗躍が敗北に効いたか。
- **機会費用**＝over席で消費された資源（暗躍+2札／暗躍+1札／脚本家能力／事件効果）の内訳。

### 22b（最終日の事件）

- **母数**＝局×ループ×**最終日に予定された事件**。
- **打点クラス**（★KB述語・§2-2 で先に固定）＝`zero:流布TT不在` / `zero:不安拡大非契約` /
  `zero:自殺非VIP` / `zero:黒猫` / `payoff`（それ以外）。
- **投資**＝そのループ中に**脚本家が犯人へ投じた不安**（札「不安+1」枚数＋脚本家能力の不安+1回数＋
  事件効果の不安+2）。★これは**上限**＝他の動機（メインラバーズの不安3・妄想拡大ウイルス・霧まき）
  でも不安は積まれる。∴ `strict`（他の動機が構造的に無い局に限る）も併記する。
- **回収の実測**＝発生したか／発生した回に D（敗北条件成立）が立ったか
  （D判定は `arena/b157_audit._analyze_firing` を**そのまま import**＝二重実装しない）。

## 因果の限界（★先に書く）

1. **相関の観測であって因果の証明ではない**（1手変えれば以降の系列が全部変わる）。
2. 「投資」は**上限**であって意図の証明ではない（AIの内部状態を読んでいない＝`agents/` 非接触）。
3. **cap の再現**は `agents/heuristic.py:739-741` の定義の書き写しであり、AIの内部値そのものではない
   （AIを import して内部を叩くと挙動不変の保証が要る＝計測器は盤面だけを読む方針）。
   ★再現の正しさは `verify` の `cap 再現の不一致` で自己検査する。

## 再利用（★二重実装の禁止）

`arena/b145_audit`（`_outcome` `_snap_index`）／`arena/b146_probe`（`_true_boards`）／
`arena/b149_audit`（`_MMProbe`）／`arena/b157_audit`（`_analyze_firing` `_pub_incident_phase`
`_secret_by_day` `_loop_end_boards` `_defeat_reasons` `_lost_loops_exact`）を import して使う。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b152_audit verify --days 3 --end 12
    python -m arena.b152_audit count  --days 3 --json d3.json
    python -m arena.b152_audit count  --days 5 --json d5.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _outcome, _snap_index
from arena.b146_probe import _true_boards
from arena.b149_audit import _MMProbe
from arena.b157_audit import (
    _analyze_firing,
    _defeat_reasons,
    _loop_end_boards,
    _lost_loops_exact,
    _pub_incident_phase,
    _secret_by_day,
)
from engine.data import (
    UNREFUSABLE_ABILITY_CHARS,
    role_absolute_friendship_ignore,
    role_has_friendship_ignore,
    unrest_threshold_of,
)
from sim import run_game

#: KP の暗躍を剥がせる友好能力の持ち主（`agents/heuristic.py:739-741` の再現＝KB確認は
#: `rules/20_goodwill_abilities.md:122-125`(転校生) `:148,153-154`(神格) `:209-212`(鑑識官)）。
#: ★巫女は神社ボード限定・学者は自身のみ・ご神木の特性も自身のみ＝対象外（doc §1-2）。
KP_ANYAKU_REMOVERS = ("転校生", "神格", "鑑識官")

#: KB上、KPの暗躍が読まれる閾値（`rules/40:94` `rules/50:43,109` `rules/40:152`）。
KB_KP_THRESHOLD = 2

#: スナップショットの境界と、その間に走るフェイズの名前（`sim/flow.py:151-300`）。
_POINTS = ("脚本家行動フェイズ前", "行動解決フェイズ後", "脚本家能力フェイズ後",
           "主人公能力フェイズ後", "事件フェイズ後", "ターン終了フェイズ後")
_CHANNELS = ("card", "mm_ability", "goodwill", "incident", "turn_end")

#: ループ終了時の敗北に直結する役職（`sim/effects.evaluate_loop_end`／`kill_character`）。
_VIP_ROLES = ("キーパーソン", "フレンド")


# ---------------------------------------------------------------------------
# 盤面の読み取り（棋譜そのもの。推定を混ぜない）
# ---------------------------------------------------------------------------
def _ch(snap: dict | None, name: str, key: str, default=0):
    if not snap:
        return None
    c = (snap.get("characters") or {}).get(name)
    if c is None:
        return None
    return c.get(key, default)


def _alive_removers(snap: dict | None, roles: dict) -> list[dict]:
    """その時点で**生存している**KP暗躍の除去役（役職・友好数・拒否可能性つき）。"""
    out = []
    for n in KP_ANYAKU_REMOVERS:
        if not _ch(snap, n, "alive", False):
            continue
        ro = roles.get(n, "パーソン")
        refusable = bool(
            n not in UNREFUSABLE_ABILITY_CHARS
            and (role_absolute_friendship_ignore(ro) or role_has_friendship_ignore(ro)))
        out.append({"name": n, "role": ro, "refusable": refusable,
                    "goodwill": _ch(snap, n, "goodwill", 0) or 0})
    return out


def _mm_cards_by_day(state) -> dict:
    """(loop, day) -> 脚本家がセットした札の一覧（`cards_revealed`＝公開情報）。"""
    out: dict = {}
    for e in state.history:
        if e.get("event") != "cards_revealed":
            continue
        key = (e.get("loop"), e.get("day"))
        for p in e.get("placements") or ():
            if p.get("owner") == "mastermind":
                out.setdefault(key, []).append(dict(p))
    return out


def _mm_abilities_by_day(state) -> dict:
    """(loop, day) -> 脚本家能力の選択（`secret_log` の `mm_ability`）。"""
    out: dict = {}
    for e in state.secret_log:
        if e.get("event") == "mm_ability":
            out.setdefault((e.get("loop"), e.get("day")), []).append(
                dict(e.get("choice") or {}))
    return out


def _kp_removal_and_refusal(state, kp: str) -> tuple:
    """(ループ毎のKP暗躍除去の試行回数, 拒否された回数)。

    `sim/flow.py:126-133` は `goodwill_used`（対象つき）→ `goodwill_refused` or
    `goodwill_resolved` の順に公開する＝直前の `goodwill_used` と対にして読む。
    """
    tried: Counter = Counter()
    refused: Counter = Counter()
    last = None
    for e in state.history:
        ev = e.get("event")
        if ev == "goodwill_used":
            last = e
        elif ev in ("goodwill_refused", "goodwill_resolved") and last is not None:
            ab = str(last.get("ability") or "")
            if last.get("target") == kp and "暗躍" in ab:
                tried[e.get("loop")] += 1
                if ev == "goodwill_refused":
                    refused[e.get("loop")] += 1
            last = None
    return tried, refused


def _loop_days(snaps: dict) -> dict:
    """loop -> そのループに実在した day の昇順列（途中終了ループがあるので実測で取る）。"""
    out: dict = {}
    for (lp, dy, pt) in snaps:
        if pt == _POINTS[0]:
            out.setdefault(lp, set()).add(dy)
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# 22b：打点クラス（★KB述語・doc §2-2 で先に固定した4類型）
# ---------------------------------------------------------------------------
def final_day_payoff_class(inc_name: str, culprit: str, roles: dict,
                           rule_y: str | None, rule_xs: set, cast: set) -> str:
    """**最終日**に発生した場合に敗北条件を1つも動かせないか（KBから一意に言える範囲だけ）。

    ★★**最終日でも事件（フェイズ7）の後に ターン終了フェイズ（9）が走る**
    （`rules/00_rules_core.md:104-112`）＝**事件の効果はターン終了フェイズの役職能力の燃料になりうる**：
    キラー（KP暗躍≥2で殺害／自暗躍≥4で主人公殺害＝`rules/40:94-95`／`rules/50:109-110`）・
    メインラバーズ（不安≥3＋暗躍≥1で主人公殺害＝`rules/50:160`）・
    シリアルキラー（同エリアが1人になれば殺害＝`rules/50:166`）・
    タイムトラベラー（友好≤2で敗北＝`rules/50:129-131`）・妄想拡大ウイルス（不安≥3でSK化）。
    ∴ ゼロと言えるのは**これらの燃料経路が1本も立っていない盤面**に限る（★保守側＝
    判別できない時は `payoff` に倒す＝**ゼロを過小に数える**）。

    ★★**判定は「脚本＋配役」だけで行う（静的）**。理由＝22b が問うているのは
    **投資の是非**であって結果ではない。最終日の実盤面で判定すると、
    「そのループが最終日に到達しなかった（＝先にループ終了効果が出た）」局が母数から落ち、
    **既に投じられていた不安が数えられなくなる**（結果論の混入）。
    生存は見ず**配役に居れば payoff に倒す**＝さらに保守側（ゼロを過小に数える）。

    `cast` ＝脚本のキャスト（`Script.cast`）。

    - `zero:黒猫`   … `rules/30_characters.md:75-77` 特性2＝事件効果は「何も起きない」。
    - `zero:流布`   … 友好を読む敗北条件は TT のみ。A.I. 特性（全カウンターを不安扱い＝
      `rules/30:47`）が絡む余地を潰すため **A.I. が居れば payoff** に倒す。
    - `zero:不安拡大` … 不安+2／暗躍+1（`rules/40:149`＝どちらも**キャラ**）の行き先が
      キラー・メインラバーズ・僕と契約のKP・ウイルスSK化 のいずれにも無い時だけゼロ。
    - `zero:自殺`   … 犯人が KP/フレンド/ラバーズ（`rules/50:153,159`＝相方に不安6）でなく、
      従者の身代わり（`rules/30:65`）でもVIPが死なず、**同エリアにSKが居ない**時だけゼロ。
    - それ以外は `payoff`（＝最終日でも打点がありうる。**一律に下げてはいけない**）。
    """
    if culprit == "黒猫":
        return "zero:黒猫"

    def _has_role(role: str) -> bool:
        return any(r == role for r in roles.values())

    if inc_name == "流布":
        if _has_role("タイムトラベラー"):
            return "payoff"
        if "A.I." in cast:
            return "payoff"          # ★保守側（A.I.特性の余地を潰す）
        return "zero:流布"
    if inc_name == "不安拡大":
        if _has_role("キラー") or _has_role("メインラバーズ"):
            return "payoff"          # 暗躍+1→キラー4／ML暗躍1（ターン終了フェイズの主人公殺害）
        if rule_y == "僕と契約しようよ！" and _has_role("キーパーソン"):
            return "payoff"          # 暗躍+1→KP暗躍2（`rules/50:43`）
        if "妄想拡大ウイルス" in rule_xs:
            return "payoff"          # 不安≥3でパーソンがSK化＝ターン終了フェイズの殺害
        return "zero:不安拡大"
    if inc_name == "自殺":
        r = roles.get(culprit)
        if r in _VIP_ROLES or r == "ラバーズ":
            return "payoff"
        if culprit in ("お嬢様", "大物") and roles.get("従者") in _VIP_ROLES:
            return "payoff"          # 従者の身代わり（`rules/30:65`）
        if _has_role("シリアルキラー"):
            return "payoff"          # ★保守側＝死亡でSKの同室が1人になりうる
        return "zero:自殺"
    return "payoff"


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    mm = _MMProbe(seed)
    hp = HeuristicProtagonist(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    roles = {n: script.role_of(n) for n in script.cast}
    kp = next((n for n, r in roles.items() if r == "キーパーソン"), None)
    snaps = _snap_index(state)
    days_by_loop = _loop_days(snaps)
    lost = _lost_loops_exact(state)
    reasons = _defeat_reasons(state)
    cards = _mm_cards_by_day(state)
    mabl = _mm_abilities_by_day(state)
    final_day = script.days_per_loop

    # ---- 22a：KP暗躍の軌跡 ------------------------------------------------
    kp_events: list[dict] = []
    kp_loop: dict = {}
    if kp:
        tried, refused = _kp_removal_and_refusal(state, kp)
        for lp, days in sorted(days_by_loop.items()):
            removals = 0
            end_anyaku = None
            for dy in days:
                pts = [snaps.get((lp, dy, p)) for p in _POINTS]
                vals = [_ch(s, kp, "anyaku", 0) for s in pts]
                for i, chn in enumerate(_CHANNELS):
                    pre, post = vals[i], vals[i + 1]
                    if pre is None or post is None:
                        continue
                    if post < pre:
                        removals += post - pre        # 負値＝剥がされた枚数
                    if post <= pre:
                        continue
                    over = max(0, post - max(KB_KP_THRESHOLD, pre))
                    rem = _alive_removers(pts[i], roles)
                    cap = 3 if rem else 2
                    kp_events.append({
                        "loop": lp, "day": dy, "channel": chn,
                        "pre": pre, "post": post, "delta": post - pre, "over": over,
                        "cap": cap, "removers": [r["name"] for r in rem],
                        "removers_all_refusable": bool(rem) and all(
                            r["refusable"] for r in rem),
                        "removers_detail": rem,
                        "cards": [p.get("card") for p in (cards.get((lp, dy)) or ())
                                  if p.get("target") == kp],
                        "abilities": [a.get("kind") for a in (mabl.get((lp, dy)) or ())
                                      if a.get("target") == kp],
                        "final_day": dy >= final_day,
                    })
                if snaps.get((lp, dy, _POINTS[-1])):
                    end_anyaku = _ch(snaps[(lp, dy, _POINTS[-1])], kp, "anyaku", 0)
            kp_dead_by_killer = any(
                e.get("event") == "death" and e.get("name") == kp
                and e.get("loop") == lp and "キラー" in str(e.get("cause") or "")
                for e in state.secret_log)
            kp_dead_by_remote = any(
                e.get("event") == "death" and e.get("name") == kp
                and e.get("loop") == lp and "遠隔殺人" in str(e.get("cause") or "")
                for e in state.secret_log)
            kp_loop[lp] = {
                "removals": -removals, "tried": tried.get(lp, 0),
                "refused": refused.get(lp, 0), "end_anyaku": end_anyaku,
                "lost": lp in lost,
                "contract_defeat": any("僕と契約" in x for x in (reasons.get(lp) or ())),
                "kp_dead_by_killer": kp_dead_by_killer,
                "kp_dead_by_remote": kp_dead_by_remote,
            }

    # ---- 22b：最終日の事件と、その犯人へ投じられた不安 --------------------
    pubs = _pub_incident_phase(state)
    secs = _secret_by_day(state)
    end_boards = _loop_end_boards(state)
    fin_rows: list[dict] = []
    # 「他の動機で不安が積まれうる局」＝strict から外す条件（doc の因果の限界 2）
    rule_xs = set(getattr(script, "rule_x", None) and [script.rule_x] or [])
    if getattr(script, "rule_x2", None):
        rule_xs.add(script.rule_x2)
    virus = "妄想拡大ウイルス" in rule_xs
    lovers = any(r in ("ラバーズ", "メインラバーズ") for r in roles.values())
    for inc in script.incidents:
        if inc.day != final_day:
            continue
        th = unrest_threshold_of(inc.culprit)
        multi = sum(1 for i2 in script.incidents if i2.culprit == inc.culprit)
        # ★打点クラスは**脚本＋配役だけ**で決める（静的・投資時点で分かる情報のみ）。
        cls = final_day_payoff_class(inc.name, inc.culprit, roles, mm.rule_y,
                                     rule_xs, set(script.cast))
        for lp, days in sorted(days_by_loop.items()):
            reached = bool(snaps.get((lp, final_day, "主人公能力フェイズ後")))
            n_card = n_abil = n_inc = 0
            for dy in days:
                for p in cards.get((lp, dy)) or ():
                    if p.get("target") == inc.culprit and p.get("card") == "不安+1":
                        n_card += 1
                for a in mabl.get((lp, dy)) or ():
                    if a.get("target") == inc.culprit and a.get("kind") == "unrest":
                        n_abil += 1
                for e in pubs.get((lp, dy)) or ():
                    if (e.get("event") == "unrest" and e.get("target") == inc.culprit
                            and int(e.get("delta", 0) or 0) > 0):
                        n_inc += int(e.get("delta", 0) or 0)
            fired = any(
                e.get("event") == "incident" and e.get("occurs")
                and e.get("name") == inc.name
                for e in (pubs.get((lp, final_day)) or ()))
            u_end = None
            s_last = snaps.get((lp, final_day, "主人公能力フェイズ後"))
            if s_last:
                u_end = _ch(s_last, inc.culprit, "unrest", 0)
            row = {"loop": lp, "day": inc.day, "name": inc.name,
                   "culprit": inc.culprit, "culprit_role": roles.get(inc.culprit),
                   "cls": cls, "threshold": th, "unrest_end": u_end,
                   "card": n_card, "ability": n_abil, "incident": n_inc,
                   "spent": n_card + n_abil + n_inc, "fired": fired,
                   "lost": lp in lost, "reached": reached,
                   "strict": not (virus or lovers or multi > 1),
                   "D": [], "P": []}
            if fired:
                evs = pubs.get((lp, final_day)) or []
                sec = secs.get((lp, final_day)) or []
                strict_b, _w = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
                f = _analyze_firing(state, script, inc.name, lp, final_day,
                                    inc.culprit, evs, sec, snaps, strict_b, mm.rule_y,
                                    end_boards, reasons.get(lp) or set(), lp in lost)
                row["D"], row["P"] = f.get("D") or [], f.get("P") or []
            fin_rows.append(row)

    return {"outcome": _outcome(state), "kp": kp, "kp_events": kp_events,
            "kp_loop": kp_loop, "final_rows": fin_rows, "rule_y": mm.rule_y,
            "n_loops": state.loop_no, "lost_loops": sorted(lost)}


# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    a = Counter()          # 22a
    b = Counter()          # 22b
    res_ex: list = []
    fin_ex: list = []
    scripts: set = set()
    games: set = set()
    #: ★独立性の物証＝クラスごとに「どの脚本／どの局から来た数か」を控える（§11b）。
    cls_games: dict = {}
    cls_scripts: dict = {}

    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts.add(name)
        games.add((name, seed))
        r = audit_game(sc, seed, loops=loops)
        # ---- 22a ----------------------------------------------------------
        if r["kp"]:
            a["KPが配役に居る局"] += 1
            a["KPが居るループ"] += len(r["kp_loop"])
            for ev in r["kp_events"]:
                a["KP暗躍が増えた席"] += 1
                a["KP暗躍の増加枚数"] += ev["delta"]
                if ev["over"] <= 0:
                    continue
                a["★over席（閾値2を超えて積んだ）"] += 1
                a["★over枚数"] += ev["over"]
                a[f"　over席の経路：{ev['channel']}"] += 1
                if ev["post"] > ev["cap"]:
                    a["　うち cap超（A-62が止め損ねた）"] += 1
                else:
                    a["　うち cap内（設計どおりのバッファ）"] += 1
                if ev["removers_all_refusable"]:
                    a["★★うち『生存除去役が全員拒否可能』＝バッファの根拠なし"] += 1
                    a["★★同・枚数"] += ev["over"]
                    for c in ev["cards"]:
                        a[f"　　根拠なし席で消費した札：{c}"] += 1
                    for k in ev["abilities"]:
                        a[f"　　根拠なし席で消費した能力：{k}"] += 1
                    if ev["channel"] == "incident":
                        a["　　根拠なし席で消費した事件効果"] += 1
                    if len(res_ex) < 40:
                        res_ex.append({"script": name, "seed": seed, **ev})
                elif not ev["removers"]:
                    a["　うち 除去役が居ない（cap=2＝純粋な過剰）"] += 1
                else:
                    a["　うち 拒否できない除去役が生存（バッファの根拠あり）"] += 1
            for lp, st in r["kp_loop"].items():
                if st["removals"]:
                    a["◆KP暗躍が実際に剥がされたループ"] += 1
                    a["◆同・剥がされた枚数"] += st["removals"]
                if st["tried"]:
                    a["◆KP暗躍の除去が宣言されたループ"] += 1
                if st["refused"]:
                    a["◆脚本家が除去を拒否したループ"] += 1
                if st["kp_dead_by_killer"]:
                    a["◆KPがキラーに殺されたループ（暗躍2の打点）"] += 1
                if st["kp_dead_by_remote"]:
                    a["◆KPが遠隔殺人で死んだループ（暗躍2の打点）"] += 1
                if st["contract_defeat"]:
                    a["◆僕と契約でループ敗北（暗躍2の打点）"] += 1
        # ---- 22b ----------------------------------------------------------
        for row in r["final_rows"]:
            b["最終日の事件枠（局×ループ）"] += 1
            b[f"クラス：{row['cls']}"] += 1
            if row["spent"]:
                cls_games.setdefault(row["cls"], set()).add((name, seed))
                cls_scripts.setdefault(row["cls"], set()).add(name)
            b[f"事件名：{row['name']}"] += 1
            if not row["reached"]:
                b[f"★そのループが最終日に到達しなかった：{row['cls']}"] += 1
                b[f"★同・そこまでに投じた不安：{row['cls']}"] += row["spent"]
            if row["spent"]:
                b[f"投資あり：{row['cls']}"] += 1
                b[f"投資枚数：{row['cls']}"] += row["spent"]
                if row["strict"]:
                    b[f"投資枚数(strict)：{row['cls']}"] += row["spent"]
            if row["fired"]:
                b[f"発生：{row['cls']}"] += 1
                if row["D"]:
                    b[f"D成立：{row['cls']}"] += 1
                if row["P"]:
                    b[f"P前進：{row['cls']}"] += 1
            if row["cls"].startswith("zero") and row["spent"] and len(fin_ex) < 40:
                fin_ex.append({"script": name, "seed": seed, **row})
        if verbose:
            print(f"  {name} s{seed}: KP={r['kp']} over席"
                  f"={sum(1 for e in r['kp_events'] if e['over'] > 0)}"
                  f" 最終日枠={len(r['final_rows'])}", flush=True)
    for k, v in cls_games.items():
        b[f"★投資があった局数：{k}"] = len(v)
    for k, v in cls_scripts.items():
        b[f"★★投資があった独立脚本数：{k}"] = len(v)
    return {"days": days, "n_games": len(games), "n_scripts": len(scripts),
            "scripts": sorted(scripts), "a": dict(a), "b": dict(b),
            "cls_games": {k: sorted(v) for k, v in cls_games.items()},
            "a_examples": res_ex, "b_examples": fin_ex}


def merge(parts: list) -> dict:
    out = {"days": parts[0].get("days"), "a": Counter(), "b": Counter(),
           "a_examples": [], "b_examples": [], "scripts": set(), "n_games": 0}
    for p in parts:
        for k in ("a", "b"):
            out[k].update(p.get(k) or {})
        out["a_examples"].extend(p.get("a_examples") or [])
        out["b_examples"].extend(p.get("b_examples") or [])
        out["scripts"].update(p.get("scripts") or [])
        out["n_games"] += int(p.get("n_games") or 0)
    for k in ("a", "b"):
        out[k] = dict(out[k])
    out["scripts"] = sorted(out["scripts"])
    out["n_scripts"] = len(out["scripts"])
    return out


def report(res: dict, days: int) -> None:
    print(f"== B-152 Phase 2：脚本家AIの疑問手2件の射程（{days}日級 {res['n_games']}局"
          f"・★独立脚本 {res['n_scripts']} 本）==")
    print(f"  脚本: {', '.join(res['scripts'])}")
    print("")
    print("  ★22a＝KPの暗躍を「2」を超えて積んだ席（over席）")
    for k, v in sorted(res["a"].items()):
        print(f"    {k:58s} {v:6d}")
    print("")
    print("  ★22b＝最終日に予定された事件と、その犯人へ投じられた不安")
    for k, v in sorted(res["b"].items()):
        print(f"    {k:58s} {v:6d}")
    if res.get("a_examples"):
        print("")
        print("  ◆22a『根拠なし席』の例（先頭5件）")
        for e in res["a_examples"][:5]:
            print(f"    {e['script']} s{e['seed']} L{e['loop']}D{e['day']}"
                  f" {e['channel']} {e['pre']}→{e['post']}"
                  f" 除去役={e['removers_detail']} 札={e['cards']} 能力={e['abilities']}")
    if res.get("b_examples"):
        print("")
        print("  ◆22b『打点ゼロ類型に不安を投じた』例（先頭5件）")
        for e in res["b_examples"][:5]:
            print(f"    {e['script']} s{e['seed']} L{e['loop']} {e['name']}"
                  f"（犯人{e['culprit']}・臨界{e['threshold']}）{e['cls']}"
                  f" 投資={e['spent']}（札{e['card']}/能力{e['ability']}/事件{e['incident']}）"
                  f" 発生={e['fired']}")


# ---------------------------------------------------------------------------
def _verify_game(script, seed: int, loops: int = 8) -> tuple:
    """プローブ有無で棋譜が一致するか（挙動不変の物証）。"""
    probe = replace(script, loops=loops)
    a = HeuristicProtagonist(seed)
    sa, _ = run_game(probe, {"mastermind": _MMProbe(seed),
                             "p1": a, "p2": a, "p3": a})
    b = HeuristicProtagonist(seed)
    sb, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                             "p1": b, "p2": b, "p3": b})
    ha = [(e.get("loop"), e.get("day"), e.get("event")) for e in sa.history]
    hb = [(e.get("loop"), e.get("day"), e.get("event")) for e in sb.history]
    return (sa.winner == sb.winner and sa.loop_no == sb.loop_no and ha == hb,
            _outcome(sa), _outcome(sb))


class _CapProbe(HeuristicMastermind):
    """★`verify` 専用＝脚本家AIが実際に使った cap を控えるだけのプローブ（返り値に触れない）。

    計測本体（`audit_game`）はこれを**使わない**（盤面だけを読む方針）。本プローブは
    「盤面からの再現」と「AIの内部値」が一致することを確かめるためだけに使う。
    """

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.caps: list = []

    def _analyze(self, view: dict) -> dict:      # noqa: D102
        a = super()._analyze(view)
        chars = {c["name"]: c for c in view["characters"]}
        mine = 3 if any(chars.get(n, {}).get("alive")
                        for n in KP_ANYAKU_REMOVERS) else 2
        self.caps.append((self._kp_anyaku_cap(a), mine))
        return a


def _verify_cap(script, seed: int, loops: int = 8) -> tuple:
    """cap 再現の自己検査＝盤面からの再現が `_kp_anyaku_cap` の実値と一致するか。

    不一致は「定義が変わった／読み違えた」の信号＝黙って0件にしないための番人
    （`agents/heuristic.py:739-741,2798-2801`）。
    """
    probe = replace(script, loops=loops)
    mm = _CapProbe(seed)
    hp = HeuristicProtagonist(seed)
    run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    return sum(1 for x, y in mm.caps if x != y), len(mm.caps)


def _switches(days: int, loops: int) -> str:
    import agents.defense_plan as dp
    H = HeuristicProtagonist
    M = HeuristicMastermind
    p = M().p
    return (f"[切替口] anyaku_cap_gate={p['anyaku_cap_gate']}"
            f" / incident_payoff_gate={p['incident_payoff_gate']}"
            f" / incident_arith_strict={p['incident_arith_strict']}"
            f" / board_removal_kb_scope={p['board_removal_kb_scope']}"
            f" / set_kp_over={p['set_kp_over']}"
            f" / B141B_UNLOCK_SAME_DAY={H.B141B_UNLOCK_SAME_DAY}"
            f" / B143_YIELD={H.B143_YIELD} / B100_MIX={H.B100_MIX}"
            f" / DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" / B152 切替口=無し（計測のみ・agents/ 非接触）"
            f" / days={days} loops={loops}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "merge"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inputs", nargs="*", default=())
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    print(_switches(a.days, a.loops), flush=True)
    if a.cmd == "verify":
        from arena.benchmark import benchmark_scripts

        bad = capbad = captot = 0
        rows = list(benchmark_scripts(days=a.days))[
            a.start:(a.end if a.end is not None else 12)]
        for name, seed, sc in rows:
            ok, oa, ob = _verify_game(sc, seed, loops=a.loops)
            if not ok:
                bad += 1
                print(f"  ✗ {name} s{seed}: probe={oa} plain={ob}")
            nb, nt = _verify_cap(sc, seed, loops=a.loops)
            capbad += nb
            captot += nt
        print(f"棋譜の不一致 = {bad} 件 / {len(rows)}局")
        print(f"cap 再現の不一致 = {capbad} 件 / {captot} 判定")
        return 1 if (bad or capbad) else 0
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
