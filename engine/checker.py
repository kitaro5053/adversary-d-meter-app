"""チェッカー共通ロジック（Outcome→採点用シグナル抽出＋自動採点）。

app.py のデバッグランナーと tests/test_scenarios.py の決定的回帰の両方がここを使う。
以前は両ファイルに同じ関数が重複しており、check キーを増やすたびに二重編集が必要だった
（フェーズA/B/C/Dで計3回発生）。単一ソース化して二重メンテを解消する。

check スキーマ（scenarios.py の各設問が持つ自動判定スペック）:
    engaged   : エンジンが起動すべきか（bool）
    value     : 期待するエンジンの数値（単一対象の暗躍/不安の最終値）
    violation : 前提違反を検出すべきか（True）
    boards    : 複数ボードの期待値 {"神社":1,"病院":2}（全一致でPASS）
    unrest    : 不安カウンター最終値 {キャラ:int}（フェーズA）
    goodwill  : 友好カウンター最終値 {キャラ:int}（フェーズA）
    positions : 移動後の位置 {キャラ:エリア名}（フェーズA）
    occurs    : 事件が発生するか（bool）（フェーズB）
    usable    : 友好能力が使えるか（bool）（フェーズC）
    refuse    : 拒否可否 "不可"/"任意"/"強制"（フェーズC）
    loop_ends : ループが今終了するか（bool）（フェーズD）
    defeat    : 主人公敗北か（bool）（フェーズD）
"""

from __future__ import annotations


# ---------- Outcome → シグナル抽出 ----------

def engine_signal(outcome) -> tuple[bool, int | None, bool]:
    """(エンジン起動, 対象の数値, 前提違反) を返す。自動判定に使う。"""
    if outcome is None:
        return (False, None, False)
    if outcome.violations:
        return (True, None, True)
    if outcome.mm_unrest is not None:
        return (True, outcome.mm_unrest.total, False)
    if outcome.mm_anyaku is not None:
        v = outcome.mm_anyaku.target_total
        return (True, v if v is not None else outcome.mm_anyaku.total, False)
    if outcome.incident is not None:
        return (True, None, False)  # 事件は数値でなく occurs(bool) で判定
    if outcome.goodwill_ability is not None:
        return (True, None, False)  # 友好能力は usable(bool)/refuse(str) で判定
    if outcome.loop_end is not None:
        return (True, None, False)  # ループ終了は loop_ends(bool)/defeat で判定
    if outcome.turn_end is not None:
        return (True, None, False)  # ターン終了は deaths(set)/loop_ends/defeat で判定
    return (True, outcome.target_delta, False)


def engine_board_deltas(outcome) -> dict:
    """行動解決の各ボードの最終暗躍量 {ボード名: delta} を返す（複数ボード採点用）。"""
    if outcome is None or outcome.adjudication is None:
        return {}
    return {
        name: tr.delta
        for name, tr in outcome.adjudication.targets.items()
        if tr.target_kind == "board"
    }


def engine_counter_finals(outcome) -> dict:
    """不安/友好カウンターの最終値 {'unrest':{キャラ:最終}, 'goodwill':{...}} を返す（フェーズA採点用）。"""
    if outcome is None or outcome.adjudication is None:
        return {"unrest": {}, "goodwill": {}}
    adj = outcome.adjudication
    return {
        "unrest": {n: r.final for n, r in adj.unrest.items()},
        "goodwill": {n: r.final for n, r in adj.goodwill.items()},
    }


def engine_positions(outcome) -> dict:
    """行動解決後の各キャラの最終エリア {キャラ: エリア} を返す（移動/移動禁止の採点用）。"""
    if outcome is None or outcome.adjudication is None:
        return {}
    return dict(outcome.adjudication.moves)


def engine_incident_occurs(outcome):
    """事件フェイズの発生判定結果（True/False/None）を返す。事件でなければ None。"""
    if outcome is None or outcome.incident is None:
        return None
    return outcome.incident.occurs


def engine_goodwill(outcome) -> dict:
    """友好能力の使用可否・拒否可否を {'usable':.., 'refuse':..} で返す。該当なしは空。"""
    if outcome is None or outcome.goodwill_ability is None:
        return {}
    g = outcome.goodwill_ability
    return {"usable": g.usable, "refuse": g.refuse}


def engine_loop_end(outcome) -> dict:
    """ループ終了/敗北の裁定を {'loop_ends':.., 'defeat':..} で返す。

    ループ終了フェイズ（loop_end）とターン終了フェイズ（turn_end のKP死亡連鎖）の
    両方が loop_ends/defeat を出しうるので、該当する方から拾う。該当なしは空。
    """
    if outcome is None:
        return {}
    if outcome.loop_end is not None:
        le = outcome.loop_end
        return {"loop_ends": le.loop_ends_now, "defeat": le.defeat}
    if outcome.turn_end is not None:
        te = outcome.turn_end
        return {"loop_ends": te.loop_ends_now, "defeat": te.defeat}
    return {}


def engine_turn_end(outcome) -> dict:
    """ターン終了の死亡判定を {'deaths': set, 'optional': int} で返す。該当なしは空。"""
    if outcome is None or outcome.turn_end is None:
        return {}
    te = outcome.turn_end
    return {"deaths": {d["name"] for d in te.forced_deaths},
            "optional": len(te.optional_actions)}


# ---------- 自動採点 ----------

def grade_scenario(check: dict | None, engaged: bool, value, violation: bool,
                   board_deltas: dict | None = None,
                   counters: dict | None = None, positions: dict | None = None,
                   occurs=None, gw_ability: dict | None = None,
                   loop_end: dict | None = None, turn_end: dict | None = None) -> str:
    """check スペックとエンジンシグナルを突き合わせて "PASS"/"FAIL"/"—" を返す。"""
    if not check:
        return "—"
    board_deltas = board_deltas or {}
    counters = counters or {"unrest": {}, "goodwill": {}}
    positions = positions or {}
    gw_ability = gw_ability or {}
    loop_end = loop_end or {}
    turn_end = turn_end or {}
    oks: list[bool] = []
    if "engaged" in check:
        oks.append(engaged == check["engaged"])
    if "value" in check:
        oks.append(bool(engaged) and value == check["value"])
    if "violation" in check:
        oks.append(bool(engaged) and violation == check["violation"])
    if "boards" in check:
        # ボード別の期待値（複数ボード設問）。全ボード一致でPASS。
        oks.append(
            bool(engaged)
            and all(board_deltas.get(b) == v for b, v in check["boards"].items())
        )
    # フェーズA：不安/友好カウンターの最終値・移動後の位置（キャラ別・全一致でPASS）。
    # カード無しのキャラは辞書に載らないため、期待側は初期0/初期位置として扱う。
    if "unrest" in check:
        act = counters.get("unrest", {})
        oks.append(bool(engaged) and all(act.get(c, 0) == v for c, v in check["unrest"].items()))
    if "goodwill" in check:
        act = counters.get("goodwill", {})
        oks.append(bool(engaged) and all(act.get(c, 0) == v for c, v in check["goodwill"].items()))
    if "positions" in check:
        oks.append(bool(engaged) and all(positions.get(c) == a for c, a in check["positions"].items()))
    if "occurs" in check:
        oks.append(bool(engaged) and occurs == check["occurs"])
    if "usable" in check:
        oks.append(bool(engaged) and gw_ability.get("usable") == check["usable"])
    if "refuse" in check:
        oks.append(bool(engaged) and gw_ability.get("refuse") == check["refuse"])
    if "loop_ends" in check:
        oks.append(bool(engaged) and loop_end.get("loop_ends") == check["loop_ends"])
    if "defeat" in check:
        oks.append(bool(engaged) and loop_end.get("defeat") == check["defeat"])
    if "deaths" in check:
        # ターン終了の確定死亡（強制）。名前の集合が一致でPASS。
        oks.append(bool(engaged) and turn_end.get("deaths", set()) == set(check["deaths"]))
    if "optional" in check:
        oks.append(bool(engaged) and turn_end.get("optional", 0) == check["optional"])
    if not oks:
        return "—"
    return "PASS" if all(oks) else "FAIL"


def grade_outcome(check: dict | None, outcome) -> str:
    """Outcome を直接受けて採点する簡易ラッパ（テスト用の一発呼び出し）。"""
    engaged, value, violation = engine_signal(outcome)
    return grade_scenario(
        check, engaged, value, violation,
        engine_board_deltas(outcome), engine_counter_finals(outcome),
        engine_positions(outcome), engine_incident_occurs(outcome),
        engine_goodwill(outcome), engine_loop_end(outcome),
        engine_turn_end(outcome),
    )
