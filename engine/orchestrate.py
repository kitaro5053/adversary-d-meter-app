"""自然文→盤面→裁定→（必要なら）確認盤面、の一気通貫アダプタ。

LLM呼び出しはここでは行わない。`TRANSLATION_PROMPT` を app.py 側の system に足し、
LLMに JSON を出させ、その JSON をここに渡す想定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import UNMODELED_TRAIT_WARNINGS

from .goodwill import GoodwillAbilityResult, resolve_goodwill_ability
from .incident import IncidentResult, resolve_incident
from .loop_end import LoopEndResult, resolve_loop_end
from .turn_end import TurnEndResult, resolve_turn_end
from .mm_phase import (
    MMAnryakuResult,
    MMUnrestResult,
    resolve_mm_phase_anyaku,
    resolve_mm_phase_unrest,
)
from .resolver import (
    Adjudication,
    SensitivityFlag,
    Violation,
    resolve_action_phase,
    sensitivity_check,
    validate_board,
)
from .translate import Question, load_question


@dataclass
class Outcome:
    question: Question
    violations: list[Violation]
    adjudication: Adjudication | None
    flags: list[SensitivityFlag]
    needs_confirmation: bool  # 感度フラグありなら確認盤面を出すべき
    mm_unrest: MMUnrestResult | None = None  # 脚本家能力フェイズ・不安の裁定（該当時）
    mm_anyaku: MMAnryakuResult | None = None  # 脚本家能力フェイズ・暗躍の裁定（該当時）
    incident: IncidentResult | None = None  # 事件フェイズ・発生判定（該当時）
    goodwill_ability: GoodwillAbilityResult | None = None  # 主人公能力フェイズ・友好能力（該当時）
    loop_end: LoopEndResult | None = None  # ループ終了フェイズ・タイミング裁定（該当時）
    turn_end: TurnEndResult | None = None  # ターン終了フェイズ・死亡判定（該当時）
    trait_warnings: list[str] = field(default_factory=list)  # 未実装特性の⚠（結果が変わりうる）

    @property
    def target_delta(self) -> int | None:
        if self.adjudication is None:
            return None
        tr = self.adjudication.targets.get(self.question.target)
        return tr.delta if tr else 0  # 対象に暗躍カードが無ければ +0


def adjudicate(data: dict | str) -> Outcome:
    """LLM出力JSON → 裁定結果。違反があれば裁定を止めて指摘優先で返す。

    盤面にエンジン未実装の自動特性を持つキャラ（従者・アルバイト等）が居る場合は、
    どのphaseでも trait_warnings に⚠を積む（数値は変えず「結果が変わりうる」を明示）。
    """
    q = load_question(data)
    out = _adjudicate_question(q)
    out.trait_warnings = [
        f"〈{c.name}〉: {UNMODELED_TRAIT_WARNINGS[c.name]}"
        for c in q.board.characters.values()
        if c.alive and c.name in UNMODELED_TRAIT_WARNINGS
    ]
    # ★黒猫のループ開始時暗躍（各ループ開始に神社へ暗躍+1・自動強制）を相談エンジンでも
    #   接続＝リマインドする（数値は変えない。盤面はユーザーが与える現在値なので、
    #   ループ開始直後の質問なら反映済みかを確認させる。forced_loop_start_anyaku を使用）。
    from .data import forced_loop_start_anyaku
    for board_name, n in forced_loop_start_anyaku(q.board.characters.values()):
        out.trait_warnings.append(
            f"黒猫の特性：各ループ開始時に{board_name}へ暗躍+{n}（強制・自動）。"
            "ループ開始直後の判定では、この暗躍が盤面(board_anyaku)に反映済みかご確認ください。"
        )
    return out


def _adjudicate_question(q: Question) -> Outcome:

    # 脚本家能力フェイズ：不安の数え上げ（行動解決とは別経路）。
    if q.phase == "mastermind_unrest":
        mm = resolve_mm_phase_unrest(q.board, q.board_anyaku)
        return Outcome(q, [], None, [], needs_confirmation=mm.needs_confirmation, mm_unrest=mm)

    # 脚本家能力フェイズ：暗躍の数え上げ。
    if q.phase == "mastermind_anyaku":
        tk = q.target_kind if q.target_kind in ("board", "character") else None
        ana = resolve_mm_phase_anyaku(
            q.board, q.rules,
            target=q.target if tk else None, target_kind=tk,
        )
        return Outcome(q, [], None, [], needs_confirmation=ana.needs_confirmation, mm_anyaku=ana)

    # 事件フェイズ：発生判定（犯人生存＋不安臨界以上）。
    if q.phase == "incident":
        inc = resolve_incident(q.board, q.incident, q.set_name)
        return Outcome(q, [], None, [], needs_confirmation=inc.needs_confirmation, incident=inc)

    # 主人公能力フェイズ：友好能力の使用可否・拒否可否。
    if q.phase == "goodwill_ability":
        ga = resolve_goodwill_ability(q.board, q.goodwill_ability)
        return Outcome(q, [], None, [], needs_confirmation=ga.needs_confirmation, goodwill_ability=ga)

    # ループ終了フェイズ：タイミング裁定（敗北条件成立≠即終了・ループ終了効果は即終了）。
    if q.phase == "loop_end":
        le = resolve_loop_end(q.loop_end)
        return Outcome(q, [], None, [], needs_confirmation=le.needs_confirmation, loop_end=le)

    # ターン終了フェイズ：死亡判定（シリアルキラー【強制】・任意殺害・強制連鎖）。
    if q.phase == "turn_end":
        te = resolve_turn_end(q.board, q.turn_end)
        return Outcome(q, [], None, [], needs_confirmation=te.needs_confirmation, turn_end=te)

    # ★連結裁定：行動解決 → 事件 → ループ終了 を1問で追う（2026-07-06）。
    #   行動解決の結果を盤面に適用し、犯人の"解決後の不安"で事件の発生判定を行う。
    #   事件効果はネタバレ回避のため裁定しない（ループ終了は敗北条件成立≠即終了のタイミング注記）。
    if q.phase == "connected":
        violations = validate_board(q.board)
        if violations:
            return Outcome(q, violations, None, [], needs_confirmation=False)
        adj = resolve_action_phase(q.board)
        flags = sensitivity_check(q.board, adj, q.target)
        board2 = _apply_adjudication(q.board, adj)
        inc = resolve_incident(board2, q.incident, q.set_name) \
            if q.incident.get("culprit") else None
        le = resolve_loop_end(q.loop_end) if q.loop_end else None
        nc = (bool(flags) or (inc is not None and inc.needs_confirmation)
              or (le is not None and le.needs_confirmation))
        return Outcome(q, [], adj, flags, needs_confirmation=nc, incident=inc, loop_end=le)

    # 既定：行動解決フェイズ（暗躍/移動）。
    violations = validate_board(q.board)
    if violations:
        # 前提がルール違反 → 計算を続けない（方針1：指摘優先）
        return Outcome(q, violations, None, [], needs_confirmation=False)

    adj = resolve_action_phase(q.board)
    flags = sensitivity_check(q.board, adj, q.target)
    return Outcome(q, [], adj, flags, needs_confirmation=bool(flags))


def _apply_adjudication(board, adj):
    """行動解決の結果（移動・不安・友好・暗躍）を盤面のコピーへ適用して返す（連結裁定用）。"""
    import copy
    b = copy.deepcopy(board)
    for name, area in adj.moves.items():
        if name in b.characters:
            b.characters[name].area = area
    for name, cr in adj.unrest.items():
        if name in b.characters:
            b.characters[name].unrest = cr.final
    for name, cr in adj.goodwill.items():
        if name in b.characters:
            b.characters[name].goodwill = cr.final
    for tgt, tr in adj.targets.items():
        if tr.target_kind == "character" and tgt in b.characters:
            b.characters[tgt].anyaku = max(0, b.characters[tgt].anyaku + tr.delta)
    return b


# LLM が自然文をこのJSONに変換するための指示（app.py の system に連結する想定）。
TRANSLATION_PROMPT = """\
# 盤面JSONへの変換タスク（行動解決フェイズ／脚本家能力フェイズ・不安／脚本家能力フェイズ・暗躍）
ルール質問が下記いずれかの「数え上げ」に該当するとき、まず質問文をJSONに変換してから、
判定エンジンの結果を説明してください。
- (A) 行動解決フェイズの数え上げ（暗躍/移動＋不安/友好/移動禁止）→ phase 省略（既定 action_resolution）
- (B) 脚本家能力フェイズに不安カウンターを何個置けるか → phase = "mastermind_unrest"
- (C) 脚本家能力フェイズに暗躍カウンターを何個置けるか → phase = "mastermind_anyaku"
- (D) 事件が発生するか（犯人生存＋不安臨界以上の不安） → phase = "incident"
- (E) 友好能力が使えるか／脚本家が拒否できるか → phase = "goodwill_ability"
- (F) 今ループが終了するか／敗北判定のタイミング → phase = "loop_end"
- (G) 連結：カードを置いて解決した"後"に事件が発生するか（＝行動解決→事件を1問で追う）
      → phase = "connected"。placements（行動解決の配置）と incident.culprit を両方入れる。
      エンジンが「行動解決後の犯人の不安」で事件発生を判定する（不安を自分で足さないこと）。
      例：『不安1の男子学生に不安+1を置いたら殺人事件は起きる？』→ connected。

出力JSON（このスキーマ以外のキーを足さない）:
{
  "set": "FS" または "BTX",
  "phase": "action_resolution"（既定）/ "mastermind_unrest" / "mastermind_anyaku",
  "question_target": {"name": "<エリア名/キャラ名/フェイズ名>",
                      "kind": "board" / "character" / "phase"},
  "characters": [{"name": "...", "role": "...", "area": "病院/神社/都市/学校",
                  "alive": true/false, "forbidden": ["..."], "goodwill": 0}],
  "placements": [{"owner": "mastermind" または "p1"/"p2"/...,
                  "card": "暗躍+2"/"暗躍禁止"/"移動←→" 等,
                  "target": "<エリア名 or キャラ名>", "target_kind": "board"/"character"}],
  "board_anyaku": {"学校": 0, "神社": 0},
  "rules": ["不穏な噂", ...],
  "incident": {"culprit": "<犯人キャラ名>", "name": "<事件名(任意)>"},
  "goodwill_ability": {"character": "<行使キャラ名>", "ability": "<能力名(任意)>"},
  "loop_end": {"protagonist_death": false, "loop_end_effect": false,
               "is_final_day_end": false, "defeat_condition_met": null},
  "assumptions": ["補完した仮定を日本語で列挙"]
}

変換ルール（行動解決・A）:
- ★**最重要：キャラの area は「ターン開始時の現在地（初期位置）」。移動カードの行き先を area に書かない。**
  移動は placements の移動カード（移動↑↓/←→/斜め）で表し、移動後の位置はエンジンが計算する。
  例：『神社に異世界人がいる。脚本家が横移動を置いて病院へ移動させた』
  → 正しい: area="神社"（現在地）＋ {card:"移動←→", target:"異世界人"}。**病院は area に入れない**
    （病院は異世界人の禁止エリアなのでエンジンが「移動不成立→神社に留まる」と裁定する）。
  → 誤り: area="病院"（行き先を現在地にしてしまう＝盤面が逆になる）。
  「〜がいる/〜にいる/初期配置」＝area。「〜へ移動/〜に移動させる」＝移動カードであって area ではない。
- 明示されていない裁量カードは入れない（inert補完）。ただし黒猫の毎ループ暗躍など
  強制・自動で必ず起きる効果はキャラ/盤面に反映してよい。
- ★主人公は通常3人（p1/p2/p3）おり、各自が同じ手札を1組ずつ持つ。**同じ主人公カードが複数の対象に
  出ている場合は、別々の主人公が出している＝owner を p1, p2, p3 と割り振る**（1人の主人公は同じカードを
  1枚しか持たないので、同じ owner に同名カードを2枚付けると前提違反になる）。
  例: 「主人公が大物に移動↑↓・従者にも移動↑↓」→ 大物は p1、従者は p2。暗躍禁止が2ボードなら p1・p2。
- 不安/友好/移動禁止の質問も (A) で扱う。カードを placements に入れれば数え上げる：
  不安+1/不安-1（不安）、友好+1/友好+2（友好）、移動禁止（重なった移動を打ち消す）。
  不安禁止・友好禁止（脚本家）は重なった不安/友好を全打ち消し（暗躍禁止と違い自滅は無い）。
- ★**1キャラ/1ボードに複数カードを重ねるときは owner を必ず分ける**（同一プレイヤーが1対象に2枚は
  DUP違反）。カードの持ち主：**脚本家のみ**＝暗躍+1/+2・移動斜め・友好禁止・不安禁止／
  **主人公のみ**＝暗躍禁止・友好+1/+2・移動禁止／**両方が持てる**＝不安+1/不安-1・移動↑↓/←→。
  例：『暗躍+1と不安-1が同じキャラに置かれた』→ 暗躍+1は脚本家(mastermind)専用なので、
  不安-1は主人公(p1)に割り当てる（両方をmastermindにするとDUP違反で弾かれる）。
- **area は必須（空欄・省略にしない）**。質問文にキャラの居場所が書かれていない場合でも、
  不安/友好/暗躍だけの数え上げ（移動が絡まない）なら area は結果に影響しないので、
  有効エリア（病院/神社/都市/学校）のどれかを入れてよい（例: 迷ったら "病院"）。
  移動が絡む質問では、キャラの現在地（ターン開始時の位置）を正しく入れる。
- forbidden は分かる場合のみ。省略すればエンジンがキャラ既定値で補完する。

変換ルール（脚本家能力フェイズ・不安・B）:
- phase = "mastermind_unrest"、question_target は {"name": "脚本家能力フェイズ(不安)", "kind": "phase"}。
- 関係するキャラを characters に入れ、各キャラの role（配役）と goodwill（友好カウンタ数）、area を必ず書く。
  「医者がミスリーダー」なら name="医者", role="ミスリーダー"。友好2なら goodwill=2。
- ファクターの判定に関わるなら board_anyaku に学校等の暗躍数を入れる（不明なら空でよい）。
- placements は通常不要（空配列）。エンジンが医者条件(友好無視+友好2)やミスリーダー能力等を判定する。

変換ルール（脚本家能力フェイズ・暗躍・C）:
- phase = "mastermind_anyaku"。特定ボード/キャラを問うなら question_target をそのボード/キャラに、
  総数を問うなら {"name": "脚本家能力フェイズ(暗躍)", "kind": "phase"}。
- クロマクが関与するなら characters に role="クロマク" で入れ area を書く。
- 「不穏な噂」が脚本に入っているなら rules に "不穏な噂" を入れる（暗躍+1の源になる）。
- placements は通常不要。エンジンがクロマク/不穏な噂の暗躍を数え、暗躍禁止で止まらない旨も返す。

変換ルール（事件フェイズ・発生判定・D）:
- phase = "incident"。question_target は {"name": "<事件名/事件フェイズ>", "kind": "phase"} でよい。
- 犯人はユーザーが述べたキャラ＝ incident.culprit に入れ、そのキャラを characters にも入れる。
  犯人の現在の不安カウンタ数を characters[].unrest に、生死を alive に必ず書く（例: 不安2なら unrest=2）。
- 不安臨界はエンジンがキャラ名から補完する（分かっていて上書きしたい時のみ incident.unrest_threshold）。
- ネタバレ回避：犯人はユーザー入力のまま扱い、事件効果（誰が死ぬ等）はJSONに入れない・創作しない。

変換ルール（主人公能力フェイズ・友好能力・E）:
- phase = "goodwill_ability"。question_target は {"name": "<キャラ名/能力名>", "kind": "phase"} でよい。
- 行使キャラを characters に入れ、role（配役）・goodwill（友好カウンタ数）・alive を必ず書く。
  goodwill_ability.character に行使キャラ名、複数能力を持つキャラなら ability に能力名も入れる。
- 必要友好数と拒否可否はエンジンが判定する。能力の効果は裁定しない（一般説明に留める）。

変換ルール（ループ終了フェイズ・タイミング・F）:
- phase = "loop_end"。characters は不要。question_target は {"name": "ループ終了", "kind": "phase"}。
- loop_end に状況フラグを入れる：主人公死亡=protagonist_death、キーパーソン死亡等のループ終了効果=loop_end_effect、
  最終日のターン終了=is_final_day_end。敗北条件が成立しているかが分かるなら defeat_condition_met（脚本依存）。
- 敗北条件そのものはネタバレのため創作しない。分からなければ defeat_condition_met は null（要確認）。

共通:
- このJSON変換は内部処理。ユーザーには最終的な日本語の裁定と、感度/要確認フラグがあれば
  「この前提に依存／KB未確定」という注意を伝える。
"""
