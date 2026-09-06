# -*- coding: utf-8 -*-
"""B-157 Phase 1：**builder が無い4事件の射程を数える**（★計測のみ・`agents/` 非接触）。

## 発端

`docs/バックログ_構想メモ_FableA.md` §33（B-157）＝ユーザーの問い
「**事件自体が発生したとしても負け筋につながりようがない、というのは判断に入っていますか**」。
`agents/defense_plan.py` が名指しする事件は **病院の事件・殺人事件・遠隔殺人・蝶の羽ばたき・
邪気の汚染** の5つだけで、KB（`rules/40_first_steps.md:146-154`）の残り4つ
＝**不安拡大・自殺・行方不明・流布**には builder が無い。
∴「値踏みの結果として無視している」のか「単に未実装」なのかが**区別できていない**。

## 本計測が出す数（チケット §3）

- **27a**＝4事件について、(1) 生成脚本への出現、(2) 発生（fired）、(3) ★**敗北への寄与**。
  ★寄与は3段階で出す（強い順）：
    - **D（直結）**＝その事件フェイズ内で敗北条件そのものが成立した
      （KP/フレンド死・主人公死・`loop_end` 誘発・真の敗北板への暗躍）。
    - **M（限界必要）**＝効果が**そのループの敗北条件の成立に対して限界必要**だった
      （例＝行方不明が置いた板の暗躍が、ループ終了時にちょうど 2＝1つ減れば不成立）。
      ★これは反実仮想ではない（相手の選択が変われば別の経路が立ちうる）＝**上限の目安**。
    - **O（射程／機会）**＝★**現在の脚本家AIが取らなかったが、規則上その場で敗北へ届いた手**。
      「無害」と「AIがまだ使えていないだけ」を分けるための数＝**これが本レーンの主眼**。
- ★**流布×タイムトラベラー**は個別に数える（チケットが名指しした最も鋭い経路）。
- **27b**＝黒猫（`rules/30_characters.md:75-77` 特性2＝黒猫が犯人の事件効果は「何も起きない」）が
  犯人候補に含まれる事件で立った致命脅威の数／うち**黒猫が唯一の候補**（＝確実に無害）の数。

## 因果の限界（★先に書く）

1. **これは相関の観測であって因果の証明ではない**。実対局の再生は相手の選択に依存し、
   1手変えれば以降の全系列が変わる＝**真の反実仮想は取れない**。∴ D/M/O の3段で出す。
2. **発生しなかった事件は「無害」の証拠にならない**（発生していないだけ）。
3. **O（射程）は「現在の脚本家AIの採点」に依存する**。`agents/heuristic._score_incident`
   （`agents/heuristic.py:3032-3063`）は**事件の種別ごとの意味を見ていない**
   （行方不明のボード選択と流布の友好剥がしを**同じ採点表**で処理する）＝
   **O が大きい＝主人公側の穴が大きい**とは限らず、**両側の未実装が同時に効いている**。

## 再利用（★二重実装の禁止＝チケット §4）

- `arena/b151_audit._Probe`（`enumerate_threats` の本番引数・返り値を控える挙動不変プローブ）
  を**そのまま import**＝27b（黒猫）の材料はこれで足りる。**書き直していない**。
- `arena/b149_audit._MMProbe`（真の敗北板の材料）／`arena/b146_probe._true_boards`／
  `arena/b145_audit._outcome`・`_snap_index`・`_lost_loops` も import して使う。

CLI（前面実行・測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b157_audit verify --days 3 --end 12   # 挙動不変＋パーサ自己検査
    python -m arena.b157_audit census --days 3            # 脚本への出現（対局不要・速い）
    python -m arena.b157_audit count  --days 3 --json d3.json
    python -m arena.b157_audit merge  --days 3 --inputs a.json b.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _lost_loops, _outcome, _snap_index
from arena.b146_probe import _true_boards
from arena.b149_audit import _MMProbe
# ★27b（黒猫）の材料＝B-151 の挙動不変プローブをそのまま使う（二重実装しない）。
from arena.b151_audit import _Probe
from engine.data import GOODWILL_ABILITIES, forbidden_of, unrest_threshold_of
from sim import run_game

#: 本レーンの対象＝`agents/defense_plan.py` に builder が無い4事件。
TARGET = ("不安拡大", "自殺", "行方不明", "流布")

#: builder がある事件（対照群＝同じ物差しで並べる）。
CONTROL = ("病院の事件", "殺人事件", "遠隔殺人", "蝶の羽ばたき", "邪気の汚染")

_AREAS = ("病院", "神社", "都市", "学校")

#: ループ終了時の敗北に直結する役職（`sim/effects.evaluate_loop_end`／`kill_character`）。
_VIP_ROLES = ("キーパーソン", "フレンド")

#: TT の任意敗北の閾値（`rules/50_basic_tragedy_x.md:124-128`＝友好2以下）。
_TT_MAX = 2


# ---------------------------------------------------------------------------
# ground truth の取り出し（棋譜そのもの。推定を混ぜない）
# ---------------------------------------------------------------------------
def _lost_loops_exact(state) -> set:
    """★そのループが**敗北で終わった**か（`loop_result` は最終ループに出ない＝補う）。

    `sim/effects.evaluate_loop_end`＝敗北なし→`game_over`（`loop_result` 無し）／
    敗北かつ最終ループ→FB or 脚本家勝ち（`loop_result` 無し）／それ以外→`loop_result 敗北`。
    ∴ 最終ループは `state.defeat` を直接見るしかない。
    """
    lost = set(_lost_loops(state))
    if getattr(state, "defeat", False):
        lost.add(state.loop_no)
    return lost


def _secret_by_day(state) -> dict:
    out: dict = {}
    for e in state.secret_log:
        out.setdefault((e.get("loop"), e.get("day")), []).append(e)
    return out


def _pub_incident_phase(state) -> dict:
    """(loop, day) -> その日の**事件フェイズの公開イベント**列（順序保存）。

    ★`secret_log` は `phase` タグを持たない（`sim/effects._sec`）＝公開側で相を決める。
    """
    out: dict = {}
    for e in state.history:
        if e.get("phase") != "incident":
            continue
        out.setdefault((e.get("loop"), e.get("day")), []).append(e)
    return out


def _loop_end_boards(state) -> dict:
    """loop -> ループ終了時のボード暗躍（`loop_board` イベント＝公開）。"""
    out: dict = {}
    for e in state.history:
        if e.get("event") == "loop_board":
            out[e.get("loop")] = dict(e.get("board_anyaku") or {})
    return out


def _defeat_reasons(state) -> dict:
    """loop -> そのループで記録された敗北理由の集合（`secret_log` の `defeat`／`loop_end`）。"""
    out: dict = {}
    for e in state.secret_log:
        if e.get("event") in ("defeat", "loop_end"):
            out.setdefault(e.get("loop"), set()).add(str(e.get("reason") or ""))
    return out


def _snap(snaps: dict, loop: int, day: int, point: str) -> dict | None:
    return snaps.get((loop, day, point))


def _gw_thresholds(name: str) -> tuple:
    return tuple(sorted({a["hearts"] for a in (GOODWILL_ABILITIES.get(name) or ())}))


# ---------------------------------------------------------------------------
# 1件の発生（loop, day）の解剖
# ---------------------------------------------------------------------------
def _parse_effect(inc_name: str, evs: list) -> dict:
    """事件フェイズの公開イベント列から、その事件が**誰に何をしたか**を読む。

    ★取りこぼしは `parse_ok=False` として数える（黙って0件にしない＝自己検査の対象）。
    ★教祖の2回解決（`rules/30_characters.md:68`）は先頭1回だけを読む＝`double` に別記。
    """
    out: dict = {"parse_ok": False}
    if inc_name == "不安拡大":
        u = next((e for e in evs if e.get("event") == "unrest"
                  and int(e.get("delta", 0) or 0) == 2), None)
        a = next((e for e in evs if e.get("event") == "anyaku"
                  and int(e.get("delta", 0) or 0) == 1
                  and e.get("target") not in _AREAS), None)
        out["unrest_target"] = u.get("target") if u else None
        out["anyaku_target"] = a.get("target") if a else None
        out["parse_ok"] = u is not None            # 第2対象は生存1人なら存在しない
    elif inc_name == "流布":
        m = next((e for e in evs if e.get("event") == "goodwill"
                  and int(e.get("delta", 0) or 0) == -2), None)
        p = next((e for e in evs if e.get("event") == "goodwill"
                  and int(e.get("delta", 0) or 0) == 2), None)
        out["minus_target"] = m.get("target") if m else None
        out["plus_target"] = p.get("target") if p else None
        out["parse_ok"] = m is not None
    elif inc_name == "行方不明":
        mv = next((e for e in evs if e.get("event") == "move"), None)
        a = next((e for e in evs if e.get("event") == "anyaku"
                  and int(e.get("delta", 0) or 0) == 1
                  and e.get("target") in _AREAS), None)
        out["moved_to"] = a.get("target") if a else (mv.get("to") if mv else None)
        out["parse_ok"] = a is not None
    elif inc_name == "自殺":
        d = [e for e in evs if e.get("event") == "death"]
        out["deaths"] = [e.get("name") for e in d]
        out["parse_ok"] = True                     # 不死/ガード/身代わりで死者0もありうる
    else:
        out["parse_ok"] = True
    return out


def _analyze_firing(state, script, inc_name: str, loop: int, day: int,
                    culprit: str, evs: list, secs: list, snaps: dict,
                    strict_boards: set, rule_y: str | None,
                    end_boards: dict, reasons: set, lost: bool) -> dict:
    """1回の発生を D（直結）／M（限界必要）／O（射程）に分解する。"""
    roles = {n: script.role_of(n) for n in script.cast}
    pre = _snap(snaps, loop, day, "主人公能力フェイズ後") or {}
    post = _snap(snaps, loop, day, "事件フェイズ後") or {}
    pre_ch = (pre.get("characters") or {})
    post_ch = (post.get("characters") or {})
    pre_bd = (pre.get("board_anyaku") or {})
    final_day = day >= script.days_per_loop
    r: dict = {"loop": loop, "day": day, "name": inc_name, "culprit": culprit,
               "culprit_role": roles.get(culprit), "loop_lost": lost,
               "final_day": final_day, "nullified_kuroneko": culprit == "黒猫"}
    r.update(_parse_effect(inc_name, evs))

    # ---- D（直結）＝この事件フェイズ内で敗北条件そのものが**成立**した ------
    #      P（前進）＝敗北条件を前進させたが、この時点ではまだ成立していない。
    post_bd = (post.get("board_anyaku") or {})
    d_tags: list = []
    p_tags: list = []
    for e in evs:
        ev = e.get("event")
        if ev == "death" and roles.get(e.get("name")) in _VIP_ROLES:
            d_tags.append(f"VIP死亡({e.get('name')}/{roles.get(e.get('name'))})")
        elif ev == "protagonist_death":
            d_tags.append("主人公死亡")
        elif ev == "loop_end":
            d_tags.append("ループ終了誘発")
        elif (ev == "anyaku" and int(e.get("delta", 0) or 0) > 0
              and e.get("target") in strict_boards):
            # ★板の敗北条件は「ループ終了時に暗躍≥2」（`sim/effects.evaluate_loop_end`）＝
            #   +1 が**その場で2に到達したか**で D と P を分ける（到達＝以後除去されなければ敗北）。
            b = e.get("target")
            v = int(post_bd.get(b, 0) or 0)
            (d_tags if v >= 2 else p_tags).append(
                f"真の敗北板{b}へ暗躍+{e.get('delta')}（→{v}）")
        elif (ev == "anyaku" and int(e.get("delta", 0) or 0) > 0
              and rule_y == "僕と契約しようよ！"
              and roles.get(e.get("target")) == "キーパーソン"):
            v = int(((post_ch.get(e.get("target")) or {}).get("anyaku", 0)) or 0)
            (d_tags if v >= 2 else p_tags).append(
                f"僕と契約のKP{e.get('target')}へ暗躍+{e.get('delta')}（→{v}）")
    # ★従者の身代わり（`rules/30:65`／B-153）＝主でなく従者が死ぬ。従者が VIP なら D。
    for s in secs:
        if s.get("event") == "juusha_substitute" and inc_name in str(s.get("cause") or ""):
            r["juusha_substitute"] = s.get("protected")
            if roles.get("従者") in _VIP_ROLES:
                d_tags.append("従者の身代わりでVIP(従者)死亡")
    r["D"] = sorted(set(d_tags))
    r["P"] = sorted(set(p_tags))

    # ---- 事件別の M（限界必要）と O（射程） --------------------------------
    m_tags: list = []
    o_tags: list = []

    if inc_name == "行方不明":
        dest = r.get("moved_to")
        # M＝置いた板がそのループの真の敗北板で、ループ終了時にちょうど2（1つ減れば不成立）
        if dest in strict_boards:
            fin = int((end_boards.get(loop) or {}).get(dest, 0) or 0)
            r["end_anyaku_dest"] = fin
            if lost and fin >= 2 and any(dest in x for x in reasons):
                m_tags.append(f"敗北板{dest}の暗躍がループ終了時{fin}"
                              + ("（ちょうど2＝限界必要）" if fin == 2 else ""))
        # O＝★真の敗北板が pre で暗躍1（＝+1 で 2＝敗北成立）だったのに、そこへ運ばなかった。
        #   ★E-2（`rules/40_first_steps.md` 事件表の注・公式裁定）＝移動先に犯人の**禁止エリア**は
        #     選べない＝規則上不可能な手を「取り逃し」に数えないよう保守側で除外する
        #     （医者能力3等のこのループ限りの解除は見ていない＝**Oを過小に見積もる**側）。
        forb = forbidden_of(culprit) if culprit else frozenset()
        for b in sorted(strict_boards):
            if int(pre_bd.get(b, 0) or 0) == 1 and dest != b and b not in forb:
                o_tags.append(f"敗北板{b}が暗躍1（運べば2＝敗北成立）だが{dest}へ運んだ")

    elif inc_name == "不安拡大":
        ct = r.get("anyaku_target")
        ut = r.get("unrest_target")
        # M-1＝暗躍を積んだ相手が同ループ中に遠隔殺人で死んだ（＝弾になった）
        if ct:
            for s in secs_all_loop(state, loop, day):
                if (s.get("event") == "death" and s.get("name") == ct
                        and "遠隔殺人" in str(s.get("cause") or "")):
                    m_tags.append(f"暗躍先{ct}が同ループの遠隔殺人で死亡"
                                  + ("（VIP）" if roles.get(ct) in _VIP_ROLES else ""))
            # M-2＝僕と契約しようよ！のKPに暗躍を積み、そのループがKP暗躍で敗北
            if (rule_y == "僕と契約しようよ！" and roles.get(ct) == "キーパーソン"
                    and any("僕と契約" in x for x in reasons)):
                m_tags.append("僕と契約のKPへ暗躍＋そのループがKP暗躍≥2で敗北")
        # M-3＝不安+2 が同ループの後続事件の発生条件を満たした（連鎖・限界必要）
        if ut:
            th = unrest_threshold_of(ut)
            for inc2 in script.incidents:
                if inc2.day <= day or inc2.culprit != ut:
                    continue
                fired2 = _fired_at(state, loop, inc2.day)
                if not fired2 or th is None:
                    continue
                s2 = _snap(snaps, loop, inc2.day, "主人公能力フェイズ後") or {}
                u2 = int(((s2.get("characters") or {}).get(ut) or {}).get("unrest", 0) or 0)
                if u2 >= th and u2 - 2 < th:
                    m_tags.append(f"不安+2が後続事件({inc2.day}日・{inc2.name})の"
                                  f"発生条件を限界的に満たした（{ut} 不安{u2}/臨界{th}）")
        # O-1＝僕と契約のKPが暗躍1（+1で2＝敗北成立）だったのに別の相手へ置いた
        if rule_y == "僕と契約しようよ！":
            for n, ro in roles.items():
                if ro != "キーパーソン":
                    continue
                if (int((pre_ch.get(n) or {}).get("anyaku", 0) or 0) == 1
                        and ct != n and (pre_ch.get(n) or {}).get("alive", True)):
                    o_tags.append(f"僕と契約のKP{n}が暗躍1（+1で敗北成立）だが{ct}へ置いた")
        # O-2＝同ループに遠隔殺人が控えるVIPが暗躍1（+1で射程）だったのに置かなかった
        if any(i.name == "遠隔殺人" and i.day > day for i in script.incidents):
            for n, ro in roles.items():
                if ro not in _VIP_ROLES:
                    continue
                c0 = pre_ch.get(n) or {}
                if (c0.get("alive", True) and int(c0.get("anyaku", 0) or 0) == 1
                        and ct != n):
                    o_tags.append(f"後続の遠隔殺人の的にできるVIP{n}が暗躍1だが{ct}へ置いた")

    elif inc_name == "自殺":
        # D は上で拾っている（犯人が VIP／身代わりで従者=VIP）。M は「犯人が死んだ結果
        # そのループが敗北した」＝相関のみ＝ここでは立てない（D で足りる）。
        if roles.get(culprit) in _VIP_ROLES:
            m_tags.append(f"犯人{culprit}が{roles.get(culprit)}＝発生＝敗北条件")
        for s in secs:
            if s.get("event") == "death_prevented":
                r["death_prevented"] = s.get("name")

    elif inc_name == "流布":
        mt = r.get("minus_target")
        # ★★流布×TT＝チケットが名指しした経路。
        tts = [n for n, ro in roles.items() if ro == "タイムトラベラー"]
        r["tt"] = tts
        for t in tts:
            g0 = int((pre_ch.get(t) or {}).get("goodwill", 0) or 0)
            g1 = int((post_ch.get(t) or {}).get("goodwill", 0) or 0)
            alive = bool((pre_ch.get(t) or {}).get("alive", True))
            r.setdefault("tt_gw", {})[t] = [g0, g1]
            if not alive:
                continue
            r["tt_alive"] = True
            if mt == t and g0 > _TT_MAX >= g1:
                m_tags.append(f"★TT{t}の友好を{g0}→{g1}へ剥がした（敗北条件が復活）")
                if final_day:
                    m_tags.append("★★最終日＝この剥がしで任意敗北が宣言可能になった")
            elif g0 > _TT_MAX:
                # ★O＝TT が友好3以上（＝無力化済み）なのに、剥がし先を TT にしなかった
                o_tags.append(f"★TT{t}が友好{g0}（剥がせば敗北条件が復活）だが{mt}から剥がした"
                              + ("／★最終日＝その場で勝てた" if final_day else ""))
        # 友好しきい値割れ（冷却役・開示役の飢餓）＝能力の敷居を跨いだか
        if mt:
            g0 = int((pre_ch.get(mt) or {}).get("goodwill", 0) or 0)
            g1 = int((post_ch.get(mt) or {}).get("goodwill", 0) or 0)
            crossed = [h for h in _gw_thresholds(mt) if g0 >= h > g1]
            r["gw_crossed"] = crossed
            if crossed:
                m_tags.append(f"{mt}の友好能力の敷居{crossed}を割った（{g0}→{g1}）")

    r["M"] = sorted(set(m_tags))
    r["O"] = sorted(set(o_tags))
    return r


def secs_all_loop(state, loop: int, day_from: int) -> list:
    """そのループの `day >= day_from` の秘匿ログ（因果の下流を追う用）。"""
    return [e for e in state.secret_log
            if e.get("loop") == loop and int(e.get("day", 0) or 0) >= day_from]


def _fired_at(state, loop: int, day: int) -> bool:
    return any(e.get("event") == "incident" and e.get("occurs")
               and e.get("loop") == loop and e.get("day") == day
               and e.get("phase") == "incident" for e in state.history)


# ---------------------------------------------------------------------------
def audit_game(script, seed: int, loops: int = 8) -> dict:
    probe = replace(script, loops=loops)
    hp = _Probe(seed)                    # ★B-151 のプローブを再利用（27b の材料）
    mm = _MMProbe(seed)
    state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})

    snaps = _snap_index(state)
    pubs = _pub_incident_phase(state)
    secs = _secret_by_day(state)
    end_boards = _loop_end_boards(state)
    reasons = _defeat_reasons(state)
    lost = _lost_loops_exact(state)
    inc_by_day = {i.day: i for i in script.incidents}

    firings: list[dict] = []
    for (lp, dy), evs in sorted(pubs.items(), key=lambda x: (x[0][0] or 0, x[0][1] or 0)):
        head = next((e for e in evs if e.get("event") == "incident"), None)
        if head is None or not head.get("occurs"):
            continue
        sec = secs.get((lp, dy)) or []
        shead = next((e for e in sec if e.get("event") == "incident"), None)
        culprit = (shead or {}).get("culprit")
        inc = inc_by_day.get(dy)
        strict, _wide = _true_boards(mm.rule_y, mm.board_x_by_loop.get(lp), False)
        firings.append(_analyze_firing(
            state, script, head.get("name") or (inc.name if inc else None),
            lp, dy, culprit, evs, sec, snaps, strict, mm.rule_y,
            end_boards, reasons.get(lp) or set(), lp in lost))
    # ★ground truth＝脚本の犯人（非公開シート＝`sim/state.Incident.culprit`）を
    #   **判定にだけ**付ける（プローブの観測には一切混ぜていない＝カンニング防止）。
    truth = {i.day: i.culprit for i in script.incidents}
    for row in hp.seats:
        for cs in row.get("cases", ()):
            cs["truth"] = truth.get(cs.get("inc_day"))
    return {"outcome": _outcome(state), "firings": firings,
            "seats": hp.seats, "rule_y": mm.rule_y,
            "n_loops": state.loop_no, "lost_loops": sorted(lost)}


# ---------------------------------------------------------------------------
def run(days: int = 3, loops: int = 8, start: int = 0, end: int | None = None,
        verbose: bool = False) -> dict:
    from arena.benchmark import benchmark_scripts

    c = Counter()          # (事件名, 指標) -> 件
    tags = Counter()       # (事件名, 段, タグ) -> 件
    kuro = Counter()       # 27b
    scripts: set = set()
    games: set = set()
    ex: list = []
    tt_rows: list = []

    for name, seed, sc in list(benchmark_scripts(days=days))[start:end]:
        scripts.add(name)
        games.add((name, seed))
        res = audit_game(sc, seed, loops=loops)
        # ---- 27a：脚本への出現（局単位・事件日単位） -----------------------
        seen: set = set()
        for i in sc.incidents:
            c[(i.name, "予定件数")] += 1
            seen.add(i.name)
        for n in seen:
            c[(n, "出現局数")] += 1
        # ---- 27a：発生と寄与 ----------------------------------------------
        for f in res["firings"]:
            n = f["name"]
            c[(n, "発生")] += 1
            if f["nullified_kuroneko"]:
                c[(n, "★黒猫が犯人＝効果は何も起きない")] += 1
                continue
            if not f.get("parse_ok", True):
                c[(n, "★★パーサ失敗（自己検査）")] += 1
                if len(ex) < 40:
                    ex.append({"kind": "parse", "script": name, "seed": seed, **f})
            if f["loop_lost"]:
                c[(n, "発生した回にループが敗北（相関）")] += 1
            for lay in ("D", "P", "M", "O"):
                if f[lay]:
                    c[(n, f"{lay} 件数")] += 1
                for t in f[lay]:
                    tags[(n, lay, t[:64])] += 1
            if n == "流布" and (f.get("tt") or f.get("M") or f.get("O")):
                tt_rows.append({"script": name, "seed": seed, **f})
        # ---- 27b：黒猫と脅威（B-151 プローブの seats を読むだけ） ----------
        for row in res["seats"]:
            for cs in row.get("cases", ()):
                # ★事件名ごとの「脅威として名指しされたか」＝builder の有無を実測で確かめる
                #   （`v1_raised`＝本番 `enumerate_threats` の返り値に事件名が現れたか＝B-151 の定義）。
                nm0 = cs.get("inc_name")
                if nm0:
                    c[(nm0, "脅威判定の母数（席×日）")] += 1
                    if cs.get("v1_raised"):
                        c[(nm0, "★致命脅威として名指しされた")] += 1
                if not cs.get("v1_raised"):
                    continue
                kuro["致命脅威が立った事件日（席×日）"] += 1
                live = list(cs.get("cands") or ())
                if "黒猫" in live:
                    kuro["★うち黒猫が犯人候補に含まれる"] += 1
                    if len(live) == 1:
                        kuro["★★うち黒猫が唯一の候補＝確実に無害"] += 1
                    if cs.get("v1_sev", 0) >= 0.30:
                        kuro["　（参考）黒猫含み かつ severity≥0.30"] += 1
                # ★ground truth＝真の犯人が黒猫＝**発生しても何も起きない**（特性2）＝
                #   この脅威は最初から空砲だった（守る価値ゼロ）。
                if cs.get("truth") == "黒猫":
                    kuro["★★★真の犯人が黒猫＝この脅威は空砲だった"] += 1
        if verbose:
            print(f"  {name} s{seed}: 発生{len(res['firings'])}件"
                  f" 敗北ループ{res['lost_loops']}", flush=True)
    return {"days": days, "n_games": len(games), "n_scripts": len(scripts),
            "scripts": sorted(scripts),
            "counts": {f"{k[0]}|{k[1]}": v for k, v in c.items()},
            "tags": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in tags.items()},
            "kuro": dict(kuro), "tt_rows": tt_rows, "examples": ex}


def merge(parts: list) -> dict:
    out = {"days": parts[0].get("days"), "counts": Counter(), "tags": Counter(),
           "kuro": Counter(), "tt_rows": [], "examples": [], "scripts": set(),
           "n_games": 0}
    for p in parts:
        for k in ("counts", "tags", "kuro"):
            out[k].update(p.get(k) or {})
        out["tt_rows"].extend(p.get("tt_rows") or [])
        out["examples"].extend(p.get("examples") or [])
        out["scripts"].update(p.get("scripts") or [])
        out["n_games"] += int(p.get("n_games") or 0)
    for k in ("counts", "tags", "kuro"):
        out[k] = dict(out[k])
    out["scripts"] = sorted(out["scripts"])
    out["n_scripts"] = len(out["scripts"])
    return out


def _pc(v: int, tot: int) -> str:
    return f"{v}({100.0 * v / tot:.1f}%)" if tot else f"{v}(—)"


def report(res: dict, days: int) -> None:
    c = res["counts"]
    print(f"== B-157 Phase 1：builder が無い4事件の射程（{days}日級 {res['n_games']}局"
          f"・★独立脚本 {res['n_scripts']} 本）==")
    print(f"  脚本: {', '.join(res['scripts'])}")
    print("")
    print("  ★★27a＝4事件（builder 無し）／対照＝5事件（builder あり）")
    print("    ※予定＝脚本の事件枠（局×枠）・出現局＝その事件を含む局数・"
          "発生＝**全ループ合計の発生回数**（1枠が複数ループで発生しうる）")
    hdr = ("事件", "予定", "出現局", "発生", "黒猫無効", "敗北ループ内",
           "D直結", "P前進", "M限界必要", "O射程")
    print("    " + "".join(f"{h:>12s}" for h in hdr))
    for grp, ttl in ((TARGET, "【builder 無し】"), (CONTROL, "【builder あり＝対照】")):
        print(f"    {ttl}")
        for n in grp:
            row = (n, c.get(f"{n}|予定件数", 0), c.get(f"{n}|出現局数", 0),
                   c.get(f"{n}|発生", 0),
                   c.get(f"{n}|★黒猫が犯人＝効果は何も起きない", 0),
                   c.get(f"{n}|発生した回にループが敗北（相関）", 0),
                   c.get(f"{n}|D 件数", 0), c.get(f"{n}|P 件数", 0),
                   c.get(f"{n}|M 件数", 0), c.get(f"{n}|O 件数", 0))
            print("    " + f"{row[0]:>12s}" + "".join(f"{v:>12d}" for v in row[1:]))
    print("")
    print("  ★★★主人公プランナーがその事件を**致命脅威として名指しした**割合"
          "（母数＝`enumerate_threats` が走った席×今日以降の事件日）")
    for grp, ttl in ((TARGET, "【builder 無し】"), (CONTROL, "【builder あり＝対照】")):
        print(f"    {ttl}")
        for n in grp:
            tot = c.get(f"{n}|脅威判定の母数（席×日）", 0)
            hit = c.get(f"{n}|★致命脅威として名指しされた", 0)
            print(f"      {n:>10s}  {_pc(hit, tot)}  （母数 {tot}）")
    bad = sum(v for k, v in c.items() if "パーサ失敗" in k)
    print(f"\n  ★自己検査：効果パーサ失敗 = {bad} 件（0 が正常）")
    print("")
    print("  ★★寄与の中身（タグ別・上位）")
    for n in TARGET:
        rows = sorted(((k, v) for k, v in res["tags"].items() if k.startswith(n + "|")),
                      key=lambda x: -x[1])
        if not rows:
            continue
        print(f"    ◆ {n}")
        for k, v in rows[:12]:
            _, lay, t = k.split("|", 2)
            print(f"      [{lay}] {v:5d}  {t}")
    print("")
    print("  ★★★流布×タイムトラベラー（チケットが名指しした経路）")
    tr = res.get("tt_rows") or []
    tt_rows = [r for r in tr if r.get("tt")]
    n_alive = sum(1 for r in tt_rows if r.get("tt_alive"))
    n_hit = sum(1 for r in tr if any("TT" in x and "剥がした" in x for x in (r.get("M") or ())))
    n_fin = sum(1 for r in tr if any("最終日" in x for x in (r.get("M") or ())))
    miss = [r for r in tr if any("TT" in x for x in (r.get("O") or ()))]
    miss_fin = [r for r in miss if any("最終日" in x for x in (r.get("O") or ()))]
    print(f"    流布が発生し かつ TT が配役に居た回 = {len(tt_rows)}"
          f"（うち TT が生存 = {n_alive}）")
    print(f"    ★実際に TT の友好を3以上→2以下へ剥がした回 = {n_hit}"
          f"（うち最終日＝任意敗北が宣言可能になった回 = {n_fin}）")
    print(f"    ★★射程O＝TT が友好3以上（＝主人公が無力化済み）なのに別の相手から剥がした回"
          f" = {len(miss)}／★★★うち**最終日**＝規則上その場で脚本家が勝てた回"
          f" = {len(miss_fin)}")
    shown: set = set()
    for r in (miss_fin + miss + tt_rows):
        key = (r["script"], r["seed"], r["loop"], r["day"])
        if key in shown:
            continue
        shown.add(key)
        if len(shown) > 8:
            break
        print(f"      - {r['script']} s{r['seed']} L{r['loop']}D{r['day']}"
              f" 剥がし先={r.get('minus_target')} TT友好={r.get('tt_gw')}"
              f" M={r.get('M')} O={r.get('O')}")
    print("")
    print("  ★★27b＝黒猫（`rules/30_characters.md:75-77` 特性2＝効果は「何も起きない」）")
    k = res.get("kuro") or {}
    base = k.get("致命脅威が立った事件日（席×日）", 0)
    nul = sum(v for kk, v in c.items() if kk.endswith("|★黒猫が犯人＝効果は何も起きない"))
    fire = sum(v for kk, v in c.items() if kk.endswith("|発生"))
    print(f"    ★黒猫が犯人で効果が無効化された発生 = {_pc(nul, fire)}"
          f"（全事件の発生 {fire} 件が母数）")
    for key in ("致命脅威が立った事件日（席×日）", "★うち黒猫が犯人候補に含まれる",
                "★★うち黒猫が唯一の候補＝確実に無害",
                "★★★真の犯人が黒猫＝この脅威は空砲だった",
                "　（参考）黒猫含み かつ severity≥0.30"):
        print(f"    {key:44s} {_pc(k.get(key, 0), base)}")
    if res.get("examples"):
        print("\n  ★自己検査に引っかかった例（先頭8）")
        for e in res["examples"][:8]:
            print(f"    {e}")


# ---------------------------------------------------------------------------
def census(days: int) -> None:
    """脚本への出現だけを数える（対局不要＝射程ゼロの事件をここで落とせる）。"""
    from arena.benchmark import benchmark_scripts

    rows = list(benchmark_scripts(days=days))
    c, g, sc_names = Counter(), Counter(), set()
    tt, tt_ruf = 0, 0
    for name, _seed, s in rows:
        sc_names.add(name)
        ns = {i.name for i in s.incidents}
        for i in s.incidents:
            c[i.name] += 1
        for n in ns:
            g[n] += 1
        if "タイムトラベラー" in s.roles.values():
            tt += 1
            tt_ruf += int("流布" in ns)
    print(f"== B-157 census：{days}日級 {len(rows)}局・★独立脚本 {len(sc_names)} 本 ==")
    for n, v in c.most_common():
        mark = "★" if n in TARGET else "  "
        print(f"  {mark}{n:10s} 予定件数={v:4d}  出現局数={g[n]:4d}")
    print(f"  TT が配役に居る局 = {tt}／うち流布が事件表にある局 = {tt_ruf}")


def _verify_game(script, seed: int, loops: int = 8) -> tuple:
    """プローブ有無で棋譜が一致するか（挙動不変の物証）。"""
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
            f" / DP6_SUPPLY_LEDGER={dp.DP6_SUPPLY_LEDGER}"
            f" / B134_CARD_DISTANCE={dp.B134_CARD_DISTANCE}"
            f" / B127_ANYAKU_TARGETING={dp.B127_ANYAKU_TARGETING}"
            f" / B157 切替口=無し（計測のみ・agents/ 非接触）"
            f" / days={days} loops={loops}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="count",
                    choices=["count", "verify", "census", "merge"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--inputs", nargs="*", default=())
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "census":
        census(a.days)
        return 0
    print(_switches(a.days, a.loops), flush=True)
    if a.cmd == "verify":
        from arena.benchmark import benchmark_scripts

        bad = 0
        rows = list(benchmark_scripts(days=a.days))[
            a.start:(a.end if a.end is not None else 12)]
        for name, seed, sc in rows:
            ok, oa, ob = _verify_game(sc, seed, loops=a.loops)
            if not ok:
                bad += 1
                print(f"  ✗ {name} s{seed}: probe={oa} plain={ob}")
        print(f"棋譜の不一致 = {bad} 件 / {len(rows)}局")
        # ★パーサ自己検査も同じ subcommand で回す（効果の読み落としが0か）
        res = run(days=a.days, loops=a.loops, start=a.start,
                  end=(a.end if a.end is not None else 12))
        miss = sum(v for k, v in res["counts"].items() if "パーサ失敗" in k)
        print(f"効果パーサ失敗 = {miss} 件")
        if miss:
            for e in res["examples"][:10]:
                print(f"    {e}")
        return 1 if (bad or miss) else 0
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
