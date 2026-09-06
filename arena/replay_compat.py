# -*- coding: utf-8 -*-
"""棋譜再生の互換層（T4b・**再生側のみ**・計測系）＝収録後に決定種が増減した旧棋譜を、
**記録の公開履歴から一意に復元できる決定だけ**補う／読み飛ばすための純関数集。

出典＝T4（`arena/score_table.py`）の smoke で `docs/feedback_logs/*.jsonl` 37 本のうち 14 本が再生停止
（従者の初期エリア B-29x ③／医者『不安操作』の宣言 B-278／カルティスト無視 A-42／途中からの棋譜）。

★原則（規約 §3「判定器は嘘をつかない」）：
  - **補うのは記録から一意に決まる決定だけ**。決まらなければ None を返す（呼び出し側はその座標で棄権）。
  - 補った／読み飛ばした事実は呼び出し側が再生結果の注記に残す（黙って直さない）。
  - `sim/` `agents/` は触らない（旧規則の盤面を現行 flow に作らせることはしない＝従者の初期エリアを
    ループ途中で変えた記録は「現行規則では再現不能」として棄権する）。

復元規則（各関数の docstring が正典）：
  1. 従者 `loop_start_area`（loop≥2）＝現行 flow は要求しない（一度きりの選択）。記録の選択が L1 で
     固定された値と同じなら読み飛ばす。違えば棄権（旧規則で毎ループ選び直していた記録＝盤面が分岐する）。
  2. `doctor_unrest_mode`＝医者♡3 の宣言。旧記録は「拒否のあと・不安>0 のときだけ」記録している。
     現行 flow が要求し記録に無いとき、公開履歴の `goodwill_used`（宣言つきなら `mode`）→ 解決直後の
     `unrest` イベント（−1＝除去／+1＝付与）→ `goodwill_resolved` で不安イベントなし（＝空撃ち＝除去）
     の順で一意に復元する。`goodwill_refused`（拒否）は旧規則では宣言が存在しない＝復元不能＝棄権。
     ★T13：教師『学生の不安操作』も同じ決定名で宣言する（KB 20:112-116）。教師の旧記録は
     **除去しかできなかった**（付与が無い）＝`mode` の無い教師の記録は、解決直後の `unrest` −1 があれば
     除去、無ければ空撃ち＝除去、拒否されても除去（旧規則の実効値が一意）と復元する。
  3. `goodwill_refuse`＝宣言（`mode`）を持つ現行の候補と、宣言キーを持たない旧記録の選択を、
     記録側のキーだけで照合する（一意に決まるときだけ）。
  4. `cultist_ignore`＝A-42 以前の AI 対局は決定が無く、engine の既定（常に無視）で解決されている。
     板が対象なら公開履歴の `anyaku` イベント（delta>0＝通った＝無視 True／無し＝False）、キャラが対象
     なら記録の盤面スナップショット（行動解決中→行動解決フェイズ後の暗躍差分）から一意に復元する。
  5. 途中からの棋譜（先頭の決定が L1D1 より後＝クラウド復元後の続き）＝先頭からの再生は原理的に不能
     ＝再生に入らず棄権する（誤った局面に記録の選択が偶然合法で乗る事故を防ぐ）。
"""

from __future__ import annotations

#: 医者『不安操作（除去/付与）』（`sim/abilities.ABILITY_IMPL` のキーと同じ文字列）。
DOCTOR_CHAR = "医者"
DOCTOR_ABILITY = "不安操作（除去/付与）"
#: ★T13：教師『学生の不安操作』＝同じ決定名 `doctor_unrest_mode` で除去/付与を宣言する。
TEACHER_CHAR = "教師"
TEACHER_ABILITY = "学生の不安操作"
#: [主] で除去/付与を宣言する能力（(キャラ, 能力名)）。`sim/abilities.ABILITY_IMPL` の `declare` と同じ集合。
UNREST_MODE_ABILITIES: frozenset = frozenset({(DOCTOR_CHAR, DOCTOR_ABILITY),
                                              (TEACHER_CHAR, TEACHER_ABILITY)})
#: 旧規則で**除去しかできなかった**能力＝`mode` 無しの記録は拒否されても「除去」と一意に決まる。
REMOVE_ONLY_BEFORE_DECLARE: frozenset = frozenset({(TEACHER_CHAR, TEACHER_ABILITY)})
#: [主] の宣言で決まる決定種と、その宣言が option に載るキー（B-278）。
DECLARATION_KEYS = ("mode",)

# 盤面スナップショット（`GameState.snapshot`）の point ラベル。
_SNAP_BEFORE = "行動解決中"
_SNAP_AFTER = "行動解決フェイズ後"


def partial_record_start(decisions: list[dict]) -> tuple[int, int] | None:
    """記録の先頭が L1D1 より後なら (loop, day) を返す（途中からの棋譜）。先頭からなら None。

    `loop_start`（ループの準備）の決定は day が 0/1 どちらでも L1 の先頭扱い。空の記録は None
    （＝従来どおり「記録の終端」として扱う）。
    """
    if not decisions:
        return None
    first = decisions[0]
    try:
        loop = int(first.get("loop") or 0)
        day = int(first.get("day") or 0)
    except (TypeError, ValueError):
        return None
    if (loop, max(day, 1)) > (1, 1):
        return loop, day
    return None


def unrest_mode_use_of(chosen: dict | None) -> tuple[str, str, str] | None:
    """`goodwill_ability` の選択から、除去/付与を宣言する能力（医者『不安操作』／教師『学生の不安操作』）の
    **(使用キャラ, 能力名, 実効対象)** を取り出す（該当しなければ None）。

    本人＝`character`/`ability`/`target`。妹の肩代わり（`妹|...`）＝target が `大人|能力|対象` の複合文字列
    ＝大人側の (キャラ, 能力, 対象) を返す。
    """
    if not isinstance(chosen, dict):
        return None
    ch, ab, tgt = chosen.get("character"), chosen.get("ability"), chosen.get("target")
    if (ch, ab) in UNREST_MODE_ABILITIES and isinstance(tgt, str):
        return ch, ab, tgt
    if isinstance(tgt, str) and tgt.count("|") == 2:
        adult, aname, t = tgt.split("|", 2)
        if (adult, aname) in UNREST_MODE_ABILITIES:
            return adult, aname, t
    return None


def doctor_target_of(chosen: dict | None) -> str | None:
    """`goodwill_ability` の選択から、除去/付与を宣言する能力の**実効対象**を取り出す（該当しなければ None）。

    B-278 当時の名前（医者専用）。★T13 で教師『学生の不安操作』も対象＝`unrest_mode_use_of` の薄い包み。
    """
    use = unrest_mode_use_of(chosen)
    return use[2] if use else None


def _event_unrest_mode_use(ev: dict) -> tuple[str, str, str] | None:
    """公開履歴の `goodwill_used` イベントが除去/付与を宣言する能力の使用なら (使用キャラ, 能力, 実効対象)。"""
    if ev.get("event") != "goodwill_used":
        return None
    return unrest_mode_use_of({"character": ev.get("character"), "ability": ev.get("ability"),
                               "target": ev.get("target")})


def _event_doctor_target(ev: dict) -> str | None:
    """公開履歴の `goodwill_used` イベントが除去/付与を宣言する能力の使用なら実効対象を返す（旧名）。"""
    use = _event_unrest_mode_use(ev)
    return use[2] if use else None


def infer_unrest_mode(history: list[dict], loop, day, target: str, nth: int = 0,
                      user: str | None = None) -> tuple[str | None, str]:
    """除去/付与の宣言（`doctor_unrest_mode`）を公開履歴から一意に復元する。返り値 (mode or None, 根拠)。

    (loop, day) の主人公能力フェイズで実効対象が `target`（`user` を与えたときは**使用した大人**も一致）の
    `goodwill_used` を出現順に数え、`nth` 番目を採る。復元の優先順：
      a. その `goodwill_used` に `mode` がある（B-278 以降の収録）→ それ。
      b. 直後〜同能力の `goodwill_resolved` までに対象の `unrest` イベント → delta<0 ＝ remove／
         delta>0 ＝ add。
      c. `goodwill_resolved` まで不安イベントが無い → 空撃ち＝ remove（add は必ず +1 が出る）。
      d. `goodwill_refused` → 医者は旧規則では宣言が存在しない＝**復元不能**（None）。
         ★T13：教師『学生の不安操作』は旧規則で**除去しかできなかった**＝拒否されても remove と一意に決まる。
    """
    seen = 0
    events = list(history or [])
    for i, ev in enumerate(events):
        if (ev.get("loop"), ev.get("day"), ev.get("phase")) != (loop, day, "goodwill_ability"):
            continue
        use = _event_unrest_mode_use(ev)
        if use is None or use[2] != target:
            continue
        adult, aname, _ = use
        if user is not None and adult != user:
            continue
        if seen < nth:
            seen += 1
            continue
        if ev.get("mode") in ("remove", "add"):
            return ev["mode"], "goodwill_used.mode（宣言つきの収録）"
        remove_only = (adult, aname) in REMOVE_ONLY_BEFORE_DECLARE
        actor = ev.get("character")      # 履歴上の発動者（妹の肩代わりなら妹）
        for nx in events[i + 1:]:
            if (nx.get("loop"), nx.get("day"), nx.get("phase")) != (loop, day, "goodwill_ability"):
                break
            e2 = nx.get("event")
            if e2 == "goodwill_refused" and nx.get("character") == actor:
                if remove_only:
                    return "remove", f"拒否されたが {adult}『{aname}』は旧規則で除去のみ（宣言＝除去で一意）"
                return None, "拒否された（旧規則では宣言が存在せず履歴からも決まらない）"
            if e2 == "goodwill_resolved" and nx.get("character") == actor:
                return "remove", "goodwill_resolved まで不安イベント無し（空撃ち＝除去）"
            if e2 == "unrest" and nx.get("target") == target:
                try:
                    d = int(nx.get("delta") or 0)
                except (TypeError, ValueError):
                    d = 0
                if d < 0:
                    return "remove", f"直後の unrest {d:+d}（除去）"
                if d > 0:
                    return "add", f"直後の unrest {d:+d}（付与）"
            if e2 in ("goodwill_used", "goodwill_refused", "goodwill_resolved"):
                break   # 別の能力に移った＝この使用の帰結が履歴に無い
        if remove_only:
            return "remove", f"帰結が履歴に無いが {adult}『{aname}』は旧規則で除去のみ（宣言＝除去で一意）"
        return None, "goodwill_used の帰結（unrest／resolved／refused）が履歴に無い"
    who = user or "医者/教師"
    return None, f"L{loop}D{day} 主人公能力フェイズに {who}→{target} の goodwill_used が無い"


def infer_doctor_mode(history: list[dict], loop, day, target: str, nth: int = 0
                      ) -> tuple[str | None, str]:
    """医者♡3 の宣言（除去/付与）を公開履歴から一意に復元する（B-278 当時の名前＝`infer_unrest_mode` の包み）。"""
    return infer_unrest_mode(history, loop, day, target, nth)


def match_ignoring_declaration(chosen: dict | None, options: list[dict],
                               keys: tuple[str, ...] = DECLARATION_KEYS) -> dict | None:
    """宣言キー（`mode`）を持たない旧記録の選択を、宣言つきの現行候補と**記録側のキーだけで**照合する。

    候補のうち「記録の全キーが一致し、余分なキーが宣言キーだけ」のものが**ちょうど1つ**ならそれを返す。
    0 個・2 個以上（＝一意でない）は None。
    """
    if not isinstance(chosen, dict):
        return None
    hits = []
    for o in options:
        if not isinstance(o, dict):
            continue
        extra = set(o) - set(chosen)
        if extra and not extra <= set(keys):
            continue
        if all(o.get(k) == v for k, v in chosen.items()):
            hits.append(o)
    return hits[0] if len(hits) == 1 else None


def infer_cultist_ignore(history: list[dict], snapshots: list[dict], loop, day,
                         target: str, target_kind: str) -> tuple[bool | None, str]:
    """カルティストの暗躍禁止無視（True＝無視して暗躍を通す）を記録から一意に復元する。

    engine は「無視の有無で暗躍が通るかが変わる対象」（暗躍禁止が実効＋暗躍+が有る）だけコールバックを
    呼ぶ＝無視なら delta>0、無視しなければ 0。
      - 板：公開履歴の `anyaku`（phase=action_resolution・target=板）は delta>0 のときだけ発行される
        → ある＝True／無い＝False。
      - キャラ：キャラ暗躍の増減は公開イベントに出ない → 記録の盤面スナップショット
        （`行動解決中`→`行動解決フェイズ後`）の暗躍差分 >0＝True／0＝False。スナップショットが無ければ None。
    """
    if target_kind == "board":
        if not history:
            return None, "公開履歴が無い（板の暗躍イベントで判定できない）"
        for ev in history or []:
            if (ev.get("loop"), ev.get("day"), ev.get("phase")) != (loop, day, "action_resolution"):
                continue
            if ev.get("event") == "anyaku" and ev.get("target") == target:
                try:
                    if int(ev.get("delta") or 0) > 0:
                        return True, f"公開履歴 anyaku {target} +{int(ev.get('delta'))}（通った＝無視）"
                except (TypeError, ValueError):
                    pass
        return False, f"公開履歴に L{loop}D{day} の anyaku {target} が無い（通らなかった＝無視せず）"
    before = after = None
    for s in snapshots or []:
        if (s.get("loop"), s.get("day")) != (loop, day):
            continue
        c = (s.get("characters") or {}).get(target)
        if not isinstance(c, dict):
            continue
        if s.get("point") == _SNAP_BEFORE:
            before = c.get("anyaku")
        elif s.get("point") == _SNAP_AFTER:
            after = c.get("anyaku")
    if before is None or after is None:
        return None, f"L{loop}D{day} の盤面スナップショット（{_SNAP_BEFORE}/{_SNAP_AFTER}）に {target} が無い"
    try:
        diff = int(after) - int(before)
    except (TypeError, ValueError):
        return None, "スナップショットの暗躍値が読めない"
    if diff > 0:
        return True, f"スナップショット {target} 暗躍 {before}→{after}（通った＝無視）"
    return False, f"スナップショット {target} 暗躍 {before}→{after}（通らなかった＝無視せず）"
