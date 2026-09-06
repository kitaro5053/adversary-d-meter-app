# -*- coding: utf-8 -*-
"""B-282〔脚本家の負け方の分類器〕＋ B-283〔遠隔殺人の完遂調査〕（計測のみ・本番経路は無変更）。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない（読むだけ）。
対局は `sim.run_game` の**素の返り値** `(state, log)` と `on_day_start` フック（run_game の
正規引数・rng を消費しない）だけを使う＝ベンチと**同一の乱数消費**が構造的に保証される
（先例＝`arena/b279_probe.py`。フックの中では `sim.loop_race.analyze_loop`＝純関数を呼ぶのみ）。

------------------------------------------------------------------------------
B-282：負け局（＝主人公が防衛した局）の「取れなかったループ」の機械分類
------------------------------------------------------------------------------
構造的事実＝防衛局では、脚本家はループ 1..(ltw−1) を取り、**最終ループ（ltw）だけを
取り損ねて敗局が確定**する（ループを1つ守られた時点でゲーム終了＝`sim/effects.evaluate_loop_end`）。
∴ 分類の単位＝**防衛されたループ（各負け局に正確に1つ）**。局数とループ数は定義上一致する
（起票の「局数・ループ数」は両方明記する）。

## ★主因の決定（事前登録＝最初のコミットで固定。辞書式＝上から最初に該当した1つ）

多重該当の扱い＝シグナルは**全て独立に記録**し（多重該当の実数も報告）、主因は以下の
辞書式順位で**一意**に決める。順位の根拠＝「④は脚本家に打つ手が無い（他の全てに優越）＞
②は主人公が現に線を折った（脚本家側の会計より説明力が強い）＞①は妨害なしの供給不足＞
③は供給も妨害も無いのに投資しなかった（残余に最も近い積極的証拠）」。

1. **④ 構造的不利**＝防衛されたループの開始時点（D1朝＝カウンター全除去後・神視点）の
   レース判定 `sim.loop_race.analyze_loop` に、活きた勝ち筋（grade≠困難）が**1本も無い**。
   ＝そのループはどう打っても取れなかった。
2. **② 読まれて折られた**＝主人公の妨害が線を現に折った証拠が最低1つ：
   (i) **冷却**＝予定事件が不発（犯人生存・不安<臨界）かつ 主人公側の犯人への不安除去
       r≥1 かつ 脚本家の犯人への供給 pump≥1 かつ **u + min(r, pump) ≥ 臨界**
       （＝除去が無ければ届いていた。min() は「不安+1→不安-1 の順で解決・0床」の保守側）。
   (ii) **暗躍禁止の直撃**＝脚本家の暗躍札が同日同対象の主人公の暗躍禁止で**実際に無効化**
       された席が1つ以上（判定＝フェイズ境界スナップショットの前後差 delta < 置いた量。
       カルティスト無視で通った席は数えない）。対象の限定＝**キャラ全席**（的づくり/契約/killer）
       ＋**勝ち筋の板**（ゴール板／病院の事件が予定されている病院）。★ゴール外の板への無効化は
       数えない（land 済みの複線演出＝ダミー配置が機能した席であり「折られた」ではない・§72-104(a)）。
   (iii) **常置被覆**＝ゴール板への主人公の暗躍禁止が「ループ日数−1」日以上置かれ、ループ終了時の
       当該板の暗躍が臨界(2)未満（＝置けば無効化される状態が実質常置＝置かなかったのは合理的で、
       線は読まれて閉じられていた）。
   ②内の優先順＝ (i) 冷却 > (ii) 直撃 > (iii) 被覆（証拠の直接性の順）。
3. **① 供給不足**＝勝ち筋事件の不発/空砲が②で説明されない：
   - **①a 不安が届かず・札は在った**＝静的会計（`sim.script_quality.incident_feasibility` の
     mm_uncontested）が臨界に届く事件が、妨害なしに不発。
   - **①c 発生したが空砲**＝事件は発生したが効果の暗躍（的/板）が無く「何も起きなかった」
     （黒猫犯人の規則上の空発生は除外して別掲）。
   - **①b 不安が届かず・札が無い**＝静的会計でも臨界に届かない（死に事件のみが不発）。
   ①内の優先順＝ a > c > b（改善可能性の高い順）。
4. **③ 会計・実行の不足**＝ゴール板の勝ち筋が活きていた（拮抗以上）のに、ループ終了時の
   当該板の暗躍が臨界未満、かつ 主人公の被覆が薄く（暗躍禁止の無い日が2日以上）、かつ
   無効化された席も無いのに、脚本家が空いた日に必要枚数を置かなかった
   （＝規則上は 暗躍+1 を毎日置けた＝B-279(d) 族の「打点会計上は打てたのに打たなかった」）。
5. **◯ 未分類（残余）**＝上のどれでもない。★残余を③に押し込まない（判定器は嘘をつかない＝
   規約§3）。典型＝殺害・接触系（killer/SK/ML/TT）の線が主で、移動の折り合いが本検出器の
   範囲外のもの。残余には活きていた勝ち筋 key を添えて報告し、代表局を手で検死する。

### 起票（§72-111）からの精緻化（根拠つき）
- 起票②の「移動・冷却」のうち**移動系の折り**は本検出器の範囲外（移動の意図の帰属は
  読み合いの復元が要る＝嘘をつかないため検出しない）。代わりに `move_clash`（同日同キャラへの
  両者の移動系札の衝突数）を**参考値として記録のみ**する（主因判定には使わない）。
- 起票①の下位区分「届かせる札は在った/無かった」に **①c（発生したが効果の暗躍が0＝空砲）**を
  追加した。根拠＝発生（不安側）と効果（暗躍側）は別の供給線で、B-283（遠隔殺人の完遂）が
  まさに①cの族＝地図の上で同じ枠に落とせる必要がある。
- 起票③「札切れ／会計の誤り」は**ゴール板の未投資**として操作的に定義した（一般の
  「打てたのに打たなかった」の全数検出は反実仮想の全列挙が要る＝B-279(d) 型の個別検出器の
  仕事）。∴ ③の実数は下界であり、残余◯に③の同族が混ざりうることを報告に明記する。

------------------------------------------------------------------------------
B-283：遠隔殺人の完遂調査（同じ抽出器で・全ループ全数）
------------------------------------------------------------------------------
遠隔殺人が予定されている全対局の**全ループ**について、入口→完遂の漏斗を数える：
  F0 予定ループ（分母） → F1 事件日まで到達（それ以前に脚本家が勝ってループ終了＝到達不要）
  → F2 犯人が事件日に生存 → F3 不安が臨界に届き**発生** → F4 暗躍≥2の的が居て**誰かが死亡**
  → F5 的の質（役職別の内訳＝KP/フレンド/ラバーズ/ML なら打点）。
各段の折れの帰属（全数）＝
  F3折れ：冷却（②(i)と同条件）／脚本家が犯人に1枚も積まず（入口手なし）／積んだが不足。
  F4折れ：的づくり0席（試みず）／的づくりが暗躍禁止で無効化 or 除去役に剥がされた／積んだが2未満。
★B-282 との重複の整理＝B-283 は**線単位の漏斗（分母＝遠隔殺人の予定ループ）**、B-282 は
**ループ単位の主因（分母＝防衛されたループ）**＝集計を混ぜない。防衛されたループに限った
交差表（B-282主因 × B-283折れ段）を別掲して二重計上を防ぐ。

------------------------------------------------------------------------------
キー（規約＝ハードコード禁止の4族への対策）
------------------------------------------------------------------------------
- B-282 行キー＝(days, script, seed, loop)／B-283 行キー＝(days, script, seed, loop, inc_day)
  ＝行を一意にする全次元。同定は内容ベース（スナップショットのカウンター差分・イベントの
  event 名フィールド）＝行番号キー・実行時キャッシュを使わない。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b282_classify --days 3
    ... --days 5 ／ --perm rev|h1|rot1（検問3） ／ --json out.json
    ... --log docs/feedback_logs/xxx.jsonl   # ★検問4＝実戦棋譜をリプレイして同じ分類器を掛ける
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import replace

from engine.data import unrest_threshold_of

_ANYAKU_CARDS = {"暗躍+1": 1, "暗躍+2": 2}
_UNREST_PLUS = {"不安+1": 1}
#: 遠隔殺人の的として「打点になる」役職（死亡が脚本家の得点に直結する）。
#: KP＝即ループ終了（rules/40:80）／フレンド＝ループ終了時死亡で敗北（rules/40:130）／
#: ラバーズ/メインラバーズ＝相方に不安+6（rules/50:153,159）。
_VALUABLE_ROLES = {"キーパーソン", "フレンド", "ラバーズ", "メインラバーズ"}

_SNAP_PRE = "主人公行動フェイズ後"      # 行動解決の直前（3+3枚伏せ済み）
_SNAP_POST = "行動解決フェイズ後"       # 行動解決の直後
_SNAP_INCIDENT = "主人公能力フェイズ後"  # 事件フェイズの直前＝発生判定時の不安

_MOVE_CARDS = {"移動↑↓", "移動←→", "移動斜め", "移動禁止"}


# ---------------------------------------------------------------------------
# 抽出（state.history / phase_snapshots / secret_log からの読み取りだけ）
# ---------------------------------------------------------------------------
def _snap_index(state) -> dict:
    """(loop, day, point) → snapshot。A-36 の重複上書きは末尾優先（dict 代入で自然に一致）。"""
    return {(s["loop"], s["day"], s["point"]): s for s in state.phase_snapshots}


def _placements_by_turn(state) -> dict:
    """(loop, day) → cards_revealed の placements（6枚：owner/card/target/target_kind）。"""
    out = {}
    for ev in state.history:
        if ev.get("event") == "cards_revealed":
            out[(ev["loop"], ev["day"])] = list(ev.get("placements") or ())
    return out


def _events_in_loop(state, loop: int) -> list[dict]:
    return [ev for ev in state.history if ev.get("loop") == loop]


def _goal_boards_for_loop(script, race_meta: dict) -> set[str]:
    """そのループのゴール板。ボードX（復讐者/爆弾X）はループ開始時の実値（race_meta に記録）。"""
    ry = script.rule_y
    if ry == "守るべき場所":
        return {"学校"}
    if ry == "封印されしモノ":
        return {"神社"}
    if ry in ("復讐者の灯火", "巨大時限爆弾Xの存在"):
        bx = race_meta.get("board_x")
        return {bx} if bx else set()
    return set()


def _culprit_of(state, loop: int, day: int, fallback: str) -> str:
    """事件の実効犯人（アルバイト？継承を含む）＝secret_log の incident イベントから。"""
    for ev in state.secret_log:
        if (ev.get("event") == "incident" and ev.get("loop") == loop
                and ev.get("day") == day and "culprit" in ev):
            return ev["culprit"]
    return fallback


def _unrest_flows(state, loop: int, culprit: str, upto_day: int) -> dict:
    """ループ内・事件日までの犯人への不安の出入り（席数ベース・内容ベース同定）。

    r（主人公側の除去）＝主人公の 不安-1 札（cards_revealed の owner≠mastermind）＋
      主人公能力フェイズ（phase==goodwill_ability）の unrest 減イベント。
    pump（脚本家側の供給）＝脚本家の 不安+N 札＋脚本家能力フェイズの unrest 増イベント＋
      事件フェイズの unrest 増イベント（不安拡大の飛び火・ラバーズ+6）＋学者特性（不安）。
    """
    r = 0
    pump = 0
    for (lp, dy), pls in _placements_by_turn(state).items():
        if lp != loop or dy > upto_day:
            continue
        for p in pls:
            if p.get("target") != culprit or p.get("target_kind") != "character":
                continue
            if p.get("card") == "不安-1" and p.get("owner") != "mastermind":
                r += 1
            elif p.get("owner") == "mastermind" and p.get("card") in _UNREST_PLUS:
                pump += _UNREST_PLUS[p["card"]]
    for ev in _events_in_loop(state, loop):
        if ev.get("day", 0) > upto_day:
            continue
        if ev.get("event") == "unrest" and ev.get("target") == culprit:
            ph = ev.get("phase")
            if ph == "goodwill_ability" and ev.get("delta", 0) < 0:
                r += -ev["delta"]
            elif ph in ("mastermind_ability", "incident") and ev.get("delta", 0) > 0:
                pump += ev["delta"]
        if (ev.get("event") == "scholar_trait" and ev.get("counter") == "不安"
                and culprit == "学者"):
            pump += 1
    return {"r": r, "pump": pump}


def _anyaku_seats(state, loop: int) -> list[dict]:
    """ループ内の脚本家の暗躍札を席単位で並べ、無効化（暗躍禁止の直撃）を前後差で判定する。

    無効化の同定＝同日同対象に主人公の暗躍禁止があり、かつフェイズ境界スナップショットの
    前後差 delta < 置いた量（＝カルティスト無視で通った席は delta で除外される）。"""
    snaps = _snap_index(state)
    rows = []
    for (lp, dy), pls in sorted(_placements_by_turn(state).items()):
        if lp != loop:
            continue
        mm_seats: dict[tuple, int] = defaultdict(int)
        kinshi: set[tuple] = set()
        for p in pls:
            key = (p.get("target"), p.get("target_kind"))
            if p.get("owner") == "mastermind" and p.get("card") in _ANYAKU_CARDS:
                mm_seats[key] += _ANYAKU_CARDS[p["card"]]
            elif p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止":
                kinshi.add(key)
        if not mm_seats:
            continue
        pre = snaps.get((lp, dy, _SNAP_PRE))
        post = snaps.get((lp, dy, _SNAP_POST))
        for (tgt, kind), placed in sorted(mm_seats.items()):
            delta = None
            if pre and post:
                if kind == "board":
                    delta = (post["board_anyaku"].get(tgt, 0)
                             - pre["board_anyaku"].get(tgt, 0))
                elif tgt in post["characters"]:
                    delta = (post["characters"][tgt]["anyaku"]
                             - pre["characters"][tgt]["anyaku"])
            blocked = ((tgt, kind) in kinshi and delta is not None
                       and delta < placed)
            rows.append({"loop": lp, "day": dy, "target": tgt, "kind": kind,
                         "placed": placed, "delta": delta,
                         "kinshi_same_day": (tgt, kind) in kinshi,
                         "nullified": bool(blocked)})
    return rows


def _char_anyaku_removals(state, loop: int, upto_day: int | None = None) -> int:
    """主人公能力フェイズでキャラの暗躍が剥がされた量（神格/転校生/鑑識官）。"""
    n = 0
    for ev in _events_in_loop(state, loop):
        if (ev.get("event") == "anyaku" and ev.get("phase") == "goodwill_ability"
                and ev.get("delta", 0) < 0 and ev.get("target") not in
                ("学校", "神社", "都市", "病院")):
            if upto_day is None or ev.get("day", 0) <= upto_day:
                n += -ev["delta"]
    return n


def _kinshi_days_on(state, loop: int, board: str) -> int:
    """そのループで主人公が暗躍禁止を board に置いた日数。"""
    days = set()
    for (lp, dy), pls in _placements_by_turn(state).items():
        if lp != loop:
            continue
        for p in pls:
            if (p.get("owner") != "mastermind" and p.get("card") == "暗躍禁止"
                    and p.get("target") == board and p.get("target_kind") == "board"):
                days.add(dy)
    return len(days)


def _mm_board_seats(state, loop: int, board: str) -> int:
    """脚本家がそのループで board に置いた暗躍席数（札＋脚本家能力）。"""
    n = 0
    for (lp, dy), pls in _placements_by_turn(state).items():
        if lp != loop:
            continue
        n += sum(1 for p in pls
                 if p.get("owner") == "mastermind" and p.get("card") in _ANYAKU_CARDS
                 and p.get("target") == board and p.get("target_kind") == "board")
    for ev in _events_in_loop(state, loop):
        if (ev.get("event") == "anyaku" and ev.get("phase") == "mastermind_ability"
                and ev.get("target") == board and ev.get("delta", 0) > 0):
            n += 1
    return n


def _loop_end_board(state, loop: int) -> dict:
    """ループ終了時の板暗躍（loop_board イベント＝evaluate_loop_end が発行）。"""
    for ev in _events_in_loop(state, loop):
        if ev.get("event") == "loop_board":
            return dict(ev.get("board_anyaku") or {})
    return {}


def _move_clashes(state, loop: int) -> int:
    """同日同キャラに両者が移動系札を重ねた席数（参考値＝主因判定には使わない）。"""
    n = 0
    for (lp, dy), pls in _placements_by_turn(state).items():
        if lp != loop:
            continue
        mm = {p.get("target") for p in pls if p.get("owner") == "mastermind"
              and p.get("card") in _MOVE_CARDS and p.get("target_kind") == "character"}
        pr = {p.get("target") for p in pls if p.get("owner") != "mastermind"
              and p.get("card") in _MOVE_CARDS and p.get("target_kind") == "character"}
        n += len(mm & pr)
    return n


# ---------------------------------------------------------------------------
# 事件行（B-282 の①②の材料。B-283 も同じ行を使う＝二重実装しない）
# ---------------------------------------------------------------------------
def _incident_rows(script, state, loop: int, feas_by_dayname: dict) -> list[dict]:
    snaps = _snap_index(state)
    rows = []
    for inc in sorted(script.incidents, key=lambda i: i.day):
        d = inc.day
        ev = next((e for e in _events_in_loop(state, loop)
                   if e.get("event") == "incident" and e.get("day") == d), None)
        if ev is None:
            rows.append({"day": d, "name": inc.name, "culprit": inc.culprit,
                         "reached": False})
            continue
        culprit = _culprit_of(state, loop, d, inc.culprit)
        th = unrest_threshold_of(culprit)
        snap = snaps.get((loop, d, _SNAP_INCIDENT))
        cs = (snap or {}).get("characters", {}).get(culprit)
        u = cs["unrest"] if cs else None
        alive = bool(cs and cs["alive"])
        flows = _unrest_flows(state, loop, culprit, d)
        occurred = bool(ev.get("occurs"))
        effect_nothing = any(
            e.get("event") == "incident_effect" and e.get("day") == d
            and e.get("name") == inc.name and "何も起きなかった" in str(e.get("note", ""))
            for e in _events_in_loop(state, loop))
        kills = [e for e in state.secret_log
                 if e.get("event") == "death" and e.get("loop") == loop
                 and e.get("day") == d and e.get("cause") == inc.name]
        feas = feas_by_dayname.get((d, inc.name))
        shortfall = (occurred is False and alive and th is not None
                     and th >= 1 and u is not None and u < th)
        cooled = (shortfall and flows["r"] >= 1 and flows["pump"] >= 1
                  and u + min(flows["r"], flows["pump"]) >= th)
        rows.append({
            "day": d, "name": inc.name, "culprit": culprit, "reached": True,
            "th": th, "u": u, "alive": alive, "occurred": occurred,
            "r": flows["r"], "pump": flows["pump"],
            "effect_nothing": effect_nothing, "black_cat": culprit == "黒猫",
            "kills": [e.get("name") for e in kills],
            "feas_grade": feas.grade if feas else None,
            "feas_uncontested": feas.mm_uncontested if feas else None,
            "shortfall": shortfall, "cooled_explains": cooled,
            "culprit_dead": (occurred is False and not alive),
        })
    return rows


# ---------------------------------------------------------------------------
# B-282：防衛されたループの主因分類（事前登録の辞書式＝モジュール docstring）
# ---------------------------------------------------------------------------
def decide_cause(n_active: int, n_cooled: int, n_null_rel: int, covered: bool,
                 n_sf_had: int, n_starved: int, n_sf_none: int,
                 underinvest: bool) -> tuple[str, str]:
    """★主因の辞書式決定（事前登録＝モジュール docstring と1対1）。純関数＝番人テストで固定。"""
    if n_active == 0:
        return "4_structural", "4"
    if n_cooled:
        return "2_read", "2i_cooled"
    if n_null_rel:
        return "2_read", "2ii_kinshi_clash"
    if covered:
        return "2_read", "2iii_covered"
    if n_sf_had:
        return "1_supply", "1a_unrest_had_cards"
    if n_starved:
        return "1_supply", "1c_effect_starved"
    if n_sf_none:
        return "1_supply", "1b_unrest_no_cards"
    if underinvest:
        return "3_bookkeeping", "3_underinvest_goal"
    return "0_unclassified", "0"


def classify_defended_loop(script, state, race_meta: dict, loop: int,
                           days: int, name: str, seed: int) -> dict:
    from sim.script_quality import incident_feasibility
    feas = {(f.day, f.name): f for f in incident_feasibility(script)}
    dpl = script.days_per_loop
    goal_boards = _goal_boards_for_loop(script, race_meta)
    hospital_line = any(i.name == "病院の事件" for i in script.incidents)

    inc_rows = _incident_rows(script, state, loop, feas)
    seats = _anyaku_seats(state, loop)
    end_board = _loop_end_board(state, loop)

    # ②(ii) 直撃：キャラ全席＋勝ち筋の板（ゴール板・病院の事件が予定なら病院）
    relevant_boards = set(goal_boards) | ({"病院"} if hospital_line else set())
    null_rel = [s for s in seats if s["nullified"]
                and (s["kind"] == "character" or s["target"] in relevant_boards)]
    null_decoy = [s for s in seats if s["nullified"]
                  and s["kind"] == "board" and s["target"] not in relevant_boards]

    # ②(iii) 常置被覆／③ ゴール板の未投資
    covered = False
    underinvest = False
    goal_detail = []
    active = race_meta.get("active_paths") or []
    goal_active = any(p["key"] in ("board", "genso_pump", "immobile_pump")
                      for p in active)
    for b in sorted(goal_boards):
        kd = _kinshi_days_on(state, loop, b)
        final = end_board.get(b, 0)
        mm_seats = _mm_board_seats(state, loop, b)
        nulled_here = any(s["nullified"] and s["kind"] == "board"
                          and s["target"] == b for s in seats)
        goal_detail.append({"board": b, "kinshi_days": kd, "final": final,
                            "mm_seats": mm_seats})
        if goal_active and final < 2:
            if kd >= dpl - 1:
                covered = True
            elif (dpl - kd) >= 2 and not nulled_here and mm_seats < 2:
                underinvest = True

    cooled = [r for r in inc_rows if r.get("cooled_explains")]
    starved = [r for r in inc_rows if r.get("occurred") and r.get("effect_nothing")
               and not r.get("black_cat")]
    sf_had = [r for r in inc_rows if r.get("shortfall") and not r.get("cooled_explains")
              and (r.get("feas_uncontested") or 0) >= (r.get("th") or 99)]
    sf_none = [r for r in inc_rows if r.get("shortfall") and not r.get("cooled_explains")
               and (r.get("feas_uncontested") or 0) < (r.get("th") or 99)]

    n_active = len(active)
    # ---- 主因（辞書式＝docstring の事前登録・decide_cause が単一ソース） ----
    cause, sub = decide_cause(n_active, len(cooled), len(null_rel), covered,
                              len(sf_had), len(starved), len(sf_none),
                              underinvest)

    return {
        "key": (days, name, seed, loop),
        "script": name, "seed": seed, "days": days, "loop": loop,
        "cause": cause, "sub": sub,
        "race_verdict": race_meta.get("verdict"),
        "active_paths": [p["key"] for p in active],
        "signals": {   # 多重該当の実数（主因と独立に全部残す）
            "s4_no_active_path": n_active == 0,
            "s2i_cooled": len(cooled),
            "s2ii_null_relevant": len(null_rel),
            "s2ii_null_decoy": len(null_decoy),
            "s2iii_covered": covered,
            "s1a": len(sf_had), "s1b": len(sf_none), "s1c": len(starved),
            "s3_underinvest": underinvest,
            "move_clash": _move_clashes(state, loop),
        },
        "incidents": inc_rows,
        "goal_detail": goal_detail,
        # ★機序掘り用の生データ（分類には未使用＝上の cause/sub は seats を数えた集計のみ依存）
        "nullified_seats": [{"day": s["day"], "target": s["target"],
                             "kind": s["kind"], "placed": s["placed"]}
                            for s in null_rel + null_decoy],
    }


# ---------------------------------------------------------------------------
# B-283：遠隔殺人の漏斗（全ループ・全数）
# ---------------------------------------------------------------------------
def remote_kill_funnel(script, state, days: int, name: str, seed: int,
                       defended_loop: int | None) -> list[dict]:
    from sim.script_quality import incident_feasibility
    remotes = [i for i in script.incidents if i.name == "遠隔殺人"]
    if not remotes:
        return []
    feas = {(f.day, f.name): f for f in incident_feasibility(script)}
    snaps = _snap_index(state)
    loops_played = sorted({ev["loop"] for ev in state.history if "loop" in ev})
    rows = []
    for inc in remotes:
        d = inc.day
        for lp in loops_played:
            ev = next((e for e in _events_in_loop(state, lp)
                       if e.get("event") == "incident" and e.get("day") == d
                       and e.get("name") == inc.name), None)
            row = {"key": (days, name, seed, lp, d),
                   "script": name, "seed": seed, "days": days,
                   "loop": lp, "inc_day": d, "defended": lp == defended_loop}
            if ev is None:
                # そのループは事件日まで進まなかった＝それ以前に脚本家が取っている
                row.update({"stage": "F1_not_reached"})
                rows.append(row)
                continue
            culprit = _culprit_of(state, lp, d, inc.culprit)
            th = unrest_threshold_of(culprit)
            snap = snaps.get((lp, d, _SNAP_INCIDENT))
            cs = (snap or {}).get("characters", {}).get(culprit)
            u = cs["unrest"] if cs else None
            alive = bool(cs and cs["alive"])
            flows = _unrest_flows(state, lp, culprit, d)
            occurred = bool(ev.get("occurs"))
            # 的づくり（キャラへの暗躍席）
            seats = [s for s in _anyaku_seats(state, lp)
                     if s["kind"] == "character" and s["day"] <= d]
            placed = sum(s["placed"] for s in seats)
            nullified = sum(s["placed"] for s in seats if s["nullified"])
            removed = _char_anyaku_removals(state, lp, upto_day=d)
            max_any = max((c["anyaku"] for c in (snap or {}).get(
                "characters", {}).values() if c["alive"]), default=0) if snap else None
            kills = [e for e in state.secret_log
                     if e.get("event") == "death" and e.get("loop") == lp
                     and e.get("day") == d and e.get("cause") == "遠隔殺人"]
            victim = kills[0]["name"] if kills else None
            vrole = script.role_of(victim) if victim else None
            row.update({
                "culprit": culprit, "th": th, "u": u, "alive": alive,
                "occurred": occurred, "r": flows["r"], "pump": flows["pump"],
                "target_seats": placed, "target_nullified": nullified,
                "target_removed": removed, "max_char_anyaku": max_any,
                "victim": victim, "victim_role": vrole,
                "feas_grade": (feas.get((d, inc.name)) or
                               type("x", (), {"grade": None})).grade,
            })
            if not alive:
                row["stage"] = "F2_culprit_dead"
            elif not occurred:
                if (flows["r"] >= 1 and flows["pump"] >= 1 and u is not None
                        and th is not None and u + min(flows["r"], flows["pump"]) >= th):
                    row["stage"] = "F3_cooled"          # 冷却が折った（②(i)と同条件）
                elif flows["pump"] == 0:
                    row["stage"] = "F3_no_entry"        # 入口手なし（1枚も積まず）
                else:
                    row["stage"] = "F3_undersupplied"   # 積んだが不足
            elif not kills:
                if placed == 0:
                    row["stage"] = "F4_no_target_attempt"   # 的づくり0席
                elif nullified >= 1 or removed >= 1:
                    row["stage"] = "F4_target_broken"       # 折られた/剥がされた
                else:
                    row["stage"] = "F4_target_short"        # 積んだが2未満
            else:
                row["stage"] = ("F5_valuable" if vrole in _VALUABLE_ROLES
                                else "F5_other_kill")
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 1局の走査（sim.run_game の素の返り値＋on_day_start フックのみ）
# ---------------------------------------------------------------------------
def probe_game(name: str, seed: int, script, days: int, loops: int = 8) -> dict:
    from agents import HeuristicMastermind, HeuristicProtagonist
    from sim import run_game
    from sim.loop_race import analyze_loop
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)

    race_at: dict[int, dict] = {}

    def on_day_start(state):
        if state.day != 1 or state.loop_no in race_at:
            return
        rep = analyze_loop(state)     # 純関数（読むだけ・rng 不使用）
        race_at[state.loop_no] = {
            "verdict": rep.verdict,
            "active_paths": [{"key": p.key, "grade": p.grade,
                              "needs_kinshi": p.needs_kinshi}
                             for p in rep.paths if p.grade != "困難"],
            "board_x": state.rule_y_board_x,
        }

    state, log = run_game(replace(script, loops=loops),
                          {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp},
                          on_day_start=on_day_start)

    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        outcome, ltw = "defense", state.loop_no
    elif fb:
        outcome = "fb_win" if state.winner == "protagonist" else "fb_loss"
        ltw = loops + 1
    else:
        outcome, ltw = "loss", loops + 1

    row = None
    if outcome == "defense":
        row = classify_defended_loop(script, state, race_at.get(ltw, {}), ltw,
                                     days, name, seed)
    funnel = remote_kill_funnel(script, state, days, name, seed,
                                ltw if outcome == "defense" else None)
    return {"script": name, "seed": seed, "days": days,
            "outcome": outcome, "loops_to_win": ltw,
            "b282": row, "b283": funnel}


# ---------------------------------------------------------------------------
# 掃引（perm＝検問3。`arena.tie_noise.install_perm`＝採点に触れない並べ替えのみ）
# ---------------------------------------------------------------------------
def sweep(days: int = 3, loops: int = 8, perm: str = "id",
          verbose: bool = True) -> dict:
    from arena.benchmark import benchmark_scripts
    from arena.tie_noise import install_perm, uninstall_perm
    install_perm(perm)
    try:
        games = []
        for name, seed, sc in benchmark_scripts(days=days):
            g = probe_game(name, seed, sc, days)
            games.append(g)
            if verbose:
                c = (g["b282"] or {}).get("sub", "-")
                print(f"  {name} s{seed}: {g['loops_to_win']} {g['outcome']} [{c}]",
                      flush=True)
    finally:
        uninstall_perm()
    return {"days": days, "loops": loops, "n_games": len(games), "perm": perm,
            "hashseed": os.environ.get("PYTHONHASHSEED", "(未固定!)"),
            "games": games}


def summarize(rep: dict) -> str:
    games = rep["games"]
    rows = [g["b282"] for g in games if g["b282"]]
    fun = [r for g in games for r in g["b283"]]
    oc = Counter(g["outcome"] for g in games)
    mean = round(sum(g["loops_to_win"] for g in games) / len(games), 3)
    L = [f"# B-282/283 計測（{rep['n_games']}局・{rep['days']}日級・perm={rep['perm']}・"
         f"PYTHONHASHSEED={rep['hashseed']}）",
         f"照合: 結末 {dict(sorted(oc.items()))} ／ 平均ループ {mean}", ""]

    L.append(f"## B-282 防衛されたループの主因（分母＝負け局 {len(rows)} 局"
             f"＝防衛ループ {len(rows)}）")
    for cause, n in Counter(r["cause"] for r in rows).most_common():
        L.append(f"  {cause}: {n} ({100*n/max(1,len(rows)):.1f}%)")
    L.append("  下位区分:")
    for sub, n in Counter(r["sub"] for r in rows).most_common():
        L.append(f"    {sub}: {n}")
    # 多重該当（シグナルの独立集計）
    sig = Counter()
    for r in rows:
        s = r["signals"]
        for k in ("s4_no_active_path", "s2iii_covered", "s3_underinvest"):
            sig[k] += 1 if s[k] else 0
        for k in ("s2i_cooled", "s2ii_null_relevant", "s2ii_null_decoy",
                  "s1a", "s1b", "s1c", "move_clash"):
            sig[k] += 1 if s[k] else 0
    L.append("  シグナル該当ループ数（独立・多重該当あり）: "
             + "  ".join(f"{k}:{v}" for k, v in sorted(sig.items())))
    # 未分類の内訳（活きていた勝ち筋）
    unc = [r for r in rows if r["cause"] == "0_unclassified"]
    if unc:
        L.append(f"  ◯未分類 {len(unc)} の活きていた勝ち筋: "
                 + "  ".join(f"{k}:{n}" for k, n in Counter(
                     tuple(sorted(set(r['active_paths']))) for r in unc).most_common(8)))
    L.append("")

    L.append(f"## B-283 遠隔殺人の漏斗（分母＝予定ループ {len(fun)}・"
             f"局数 {len({tuple(r['key'])[:3] for r in fun})}）")
    for st, n in Counter(r["stage"] for r in fun).most_common():
        L.append(f"  {st}: {n}")
    occ = [r for r in fun if r.get("occurred")]
    L.append(f"  発生（occurs=True）合計 = {len(occ)} ／ 死亡まで完遂 = "
             f"{sum(1 for r in fun if r.get('victim'))}")
    vr = Counter(r["victim_role"] or "-" for r in fun if r.get("victim"))
    if vr:
        L.append("  犠牲者の役職: " + "  ".join(f"{k}:{n}" for k, n in vr.most_common()))
    # B-282 との交差（防衛ループに限る＝二重計上の整理）
    dfun = [r for r in fun if r.get("defended")]
    if dfun:
        cause_by_key = {tuple(r["key"])[:4]: r["sub"] for r in rows}
        cross = Counter((r["stage"], cause_by_key.get(tuple(r["key"])[:4], "?"))
                        for r in dfun)
        L.append("  ★防衛ループとの交差（B-283段 × B-282主因）: "
                 + "  ".join(f"{a}×{b}:{n}" for (a, b), n in cross.most_common()))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# ★検問4：実戦棋譜（jsonl）をリプレイして同じ分類器を掛ける
# ---------------------------------------------------------------------------
class _ReplayWithB278Shim:
    """記録済みの (decision, chosen) 列を順に返すリプレイ席＋B-278 の宣言シム。

    ★§72-106 の申し送り「規則を直すたび、それ以前の教材は bit 再生できなくなる」の実物：
    B-278 以前の収録では、医者の友好能力が**拒否より前に宣言決定を持たない**
    （旧順序＝拒否→宣言）。現行規則（KB `rules/20:14-18`＝宣言→拒否）で再生すると、
    記録に無い `doctor_unrest_mode` 決定が挿入されて desync する。
    シム＝**記録の次の決定種別と一致しない `doctor_unrest_mode` だけ**、旧規則の実効値
    （対象の不安>0 なら remove／0 なら add＝旧実装の強制と同値）を view から合成して返す。
    ★T13：教師『学生の不安操作』も同じ決定名で宣言する（KB 20:112-116）。教師の旧規則は
    **除去のみ**（不安0でも空撃ち＝remove）＝`arena.replay_compat.REMOVE_ONLY_BEFORE_DECLARE`。
    それ以外の種別不一致は捏造せず RuntimeError（判定器は嘘をつかない・規約§3）。
    """

    def __init__(self, pairs: list[tuple[str, dict]]):
        self._pairs = list(pairs)
        self._i = 0
        self._last_gw_target: str | None = None
        self._last_gw_use: tuple[str, str] | None = None   # (大人, 能力名)

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if self._i < len(self._pairs) and self._pairs[self._i][0] == decision:
            _, ch = self._pairs[self._i]
            self._i += 1
            if decision == "goodwill_ability" and isinstance(ch, dict):
                from arena.replay_compat import unrest_mode_use_of
                use = unrest_mode_use_of(ch)
                self._last_gw_target = use[2] if use else ch.get("target")
                self._last_gw_use = (use[0], use[1]) if use else None
            return ch
        if decision == "doctor_unrest_mode":
            from arena.replay_compat import REMOVE_ONLY_BEFORE_DECLARE
            if self._last_gw_use in REMOVE_ONLY_BEFORE_DECLARE:
                return {"mode": "remove"}        # 教師＝旧規則は除去のみ（空撃ち含む）
            tgt = self._last_gw_target
            u = next((c["unrest"] for c in view.get("characters", ())
                      if c.get("name") == tgt), 0)
            return ({"mode": "remove"} if u > 0 else {"mode": "add"})
        exp = self._pairs[self._i][0] if self._i < len(self._pairs) else "(尽きた)"
        raise RuntimeError(f"リプレイ desync: 要求={decision} / 記録={exp}")


def scan_real_log(path: str) -> dict:
    """教材棋譜を**選択列そのまま**再実行し（AI 非依存・決定的）、再構成した state に
    本分類器を掛ける。meta の winner/loops_played と突き合わせて再現性を自己申告する。"""
    from sim import run_game
    from sim.loop_race import analyze_loop
    from sim.state import script_from_dict
    meta, decisions = None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("type") == "meta":
                meta = d
            elif d.get("type") == "decision":
                decisions.append(d)
    script = script_from_dict(meta["script"])
    seqs: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for d in decisions:
        seqs[d["actor"]].append((d["decision"], d["chosen"]))
    agents = {a: _ReplayWithB278Shim(seqs.get(a, []))
              for a in ("mastermind", "p1", "p2", "p3")}
    # ★配線の単一ソース＝`sim.flow.attach_mm_bluff`（[[wiring-forgotten-across-paths]]）：
    #   記録局の脚本家が板へのダミー配置（B-214/B-215・複線演出）を打っている場合、
    #   候補列にそれが開いていないと `chosen in options` の照合で落ちる。リプレイ席は
    #   記録どおり返すだけ＝候補を広げても再生は決定的（乱数を消費しない）。
    agents["mastermind"].wants_bluff_options = True
    race_at: dict[int, dict] = {}

    def on_day_start(state):
        if state.day != 1 or state.loop_no in race_at:
            return
        rep = analyze_loop(state)
        race_at[state.loop_no] = {
            "verdict": rep.verdict,
            "active_paths": [{"key": p.key, "grade": p.grade,
                              "needs_kinshi": p.needs_kinshi}
                             for p in rep.paths if p.grade != "困難"],
            "board_x": state.rule_y_board_x,
        }

    state, log = run_game(script, agents, on_day_start=on_day_start)
    replay_ok = (state.winner == meta.get("winner")
                 and state.loop_no == meta.get("loops_played"))
    name = os.path.basename(path)
    days = script.days_per_loop
    row = None
    fb = any(e.get("event") == "final_battle" for e in state.history)
    defended = state.loop_no if (state.winner == "protagonist" and not fb) else None
    if defended is not None:
        row = classify_defended_loop(script, state, race_at.get(defended, {}),
                                     defended, days, name, 0)
    funnel = remote_kill_funnel(script, state, days, name, 0, defended)
    return {"path": path, "winner": state.winner, "loops_played": state.loop_no,
            "replay_matches_meta": replay_ok,
            "b282": row, "b283": funnel}


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-282/283 計測")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--perm", type=str, default="id")
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--log", type=str, default=None,
                    help="実戦棋譜 jsonl をリプレイして分類（検問4）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED 未固定。測定は PYTHONHASHSEED=0 で。", file=sys.stderr)
    if args.log:
        rep = scan_real_log(args.log)
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=list))
        return
    rep = sweep(days=args.days, loops=args.loops, perm=args.perm,
                verbose=not args.quiet)
    print(summarize(rep))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, default=list)
        print(f"→ {args.json}")


if __name__ == "__main__":
    main()
