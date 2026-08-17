# -*- coding: utf-8 -*-
"""1手詰め（日単位）の完全判定器 — 敗局の責任分類（AIのミス／詰み／読み合い）。

ユーザー方針（2026-07-13）：詰み判定の梯子は「1手詰めをまず完璧に」から。
ここでの「1手」＝1日。脚本家のσ（3枚の置き位置＝卓上で公開）が見えた日開始局面で：

- "defense_exists" … ∃応手R ∀中身c: 主人公が今日を凌ぐ。
                     実対局で凌げなかったなら**AIのミス**（防御手Rが証拠として返る）。
- "mate"           … ∃中身c ∀応手R: 今日負ける。どう指しても負け＝**AIの責任ではない**
                     （詰み中身cが証拠として返る。σを選んだ時点で詰み）。
- "guessing"       … どちらも不成立＝**読み合い**（∀R∃c負け かつ ∀c∃R凌ぎ＝混合戦略の領域。
                     2択を通される類はユーザー方針で許容）。
- "unknown"        … 応手列挙が予算を超えた＝棄権（嘘をつかない）。

★健全性の設計（本モジュールの核）：
1. **存在証明**（defense/mateの発見）は候補の探索順に何を使っても健全＝見つけた手が証拠。
   よって速度のためのヒューリスティック順序付けは自由（_order_responses）。
2. **不存在の主張**（defenseが無い／mateが無い）は**完全列挙**でのみ行う。列挙は
   legal.py の合法手フロンティア（loop_solver._Frontier）から再帰的に構築＝手生成を
   手書きしない。「枝刈りの keep-set に穴」という故障モード（監査 2026-07-13：脚本家の
   不安禁止・妄想ウイルスのパーソン不安・ML への暗躍/不安・フレンドの移動/遠隔殺人・
   SKブロック移動が既存 loop_solver の枝刈りから漏れていた）を構造的に排除する。
3. 開フェーズ（脚本家能力・友好能力・事件選択・ターン終了）は完全情報の逐次ミニマックス
   （全選択肢・枝刈りなし）。

★loop_solver（全σ量化・budget≥1）との関係：あちらは「脚本家がσも選べる」ゲーム全体の
3値で、中盤サイズでは枝刈り/キャップ必須＝目安。こちらは**σが既知**（実対局の観測）に
条件付けることで、完全列挙が現実的な計算量に収まる＝確定判定。1手詰めの権威はこちら。

objective:
- "survive"     … 今日 defeat が成立しない（最終日は evaluate_loop_end のボード条件まで含む）。
- "no_incident" … survive ＋ 今日の事件を発生させない。盤面レース敗北（ループ終了時の
                  暗躍条件）は「どの日の事件供給が決定打か」に分解して診断するための目的。
                  事件1回＝即負けとは限らないが「その発生を止める応手はあったか」に答える。
- ("no_death", {名前,...}) … survive ＋ 指定キャラを今日死なせない。フレンド死亡は
                  ループ終了時の敗北＝日単位の survive では捕まらない（BTX_4実測：SKが
                  フレンドを殺した日が survive で defense_exists と空虚に判定された）。

中盤日（非最終日）の "survive" は「今日即負けしない」であり「以後も安全」ではない。
分類は日単位の責任の切り分けに使う（詰み梯子の1段目）。
"""

from __future__ import annotations

import copy
from collections import Counter

from . import flow
from .legal import MASTERMIND_BOARD_CARDS
from .loop_solver import _Frontier, _plan_decider
from .state import GameState

MASTERMIND = "mastermind"
PROTAGONIST = "protagonist"


def _needs_bluff(state: GameState, placements) -> bool:
    """★B-223：脚本家セットに**板ダミー**（非暗躍札の板配置・幻想板の実効札を除く）があるか。

    B-214/B-215 既定 ON（2026-08-14 再基準化）以降、実対局のσには板ダミーが混ざる。
    従来の量化（allow_bluff=False）はこの中身と整合できず、
    (a) 板σが残り暗躍札より多い日＝**量化不能**（ValueError・B-220 実測＝FS#10 L5D4／BTX#3 L8D2
        ＝いずれも板σ2枚 vs 残暗躍札は暗躍+1 のみ〔暗躍+2 は 1/loop 使用済み〕）、
    (b) 整合する別中身が在る日＝**実際の中身が ∀c の量化範囲に入らない**まま判定が返る
        （＝現実が範囲外の判定）。∴ 実セットに板ダミーがある日は量化に allow_bluff を開く。
    """
    if not placements:
        return False
    gensou = {c.area for n, c in state.characters.items()
              if n == "幻想" and c.alive and c.on_board}
    return any(p.get("target_kind") == "board"
               and p.get("card") not in MASTERMIND_BOARD_CARDS
               and p.get("target") not in gensou
               for p in placements)


# ---------------------------------------------------------------------------
# 状態の軽量化と1日実行
# ---------------------------------------------------------------------------

def _slim(state: GameState) -> GameState:
    """探索用に履歴系を空にしたコピー。resolve_turn_end の護衛検索・事件発生の検出は
    「今日のイベント」しか見ないので、空から始めて問題ない（deepcopy コストが本体）。"""
    st = copy.copy(state)
    st.history = []
    st.secret_log = []
    st.phase_snapshots = []
    return copy.deepcopy(st)


def _run_plan(state: GameState, plan: list[dict],
              mm_bluff: bool = False) -> GameState:
    """★B-223：mm_bluff＝脚本家 set_card 候補に allow_bluff を開く（`run_day` は decide
    関数の属性 `mm_allow_bluff` を見る＝`sim/flow.attach_mm_bluff` と同じ配線口）。
    既定 False＝従来と1手も変わらない。"""
    st = copy.deepcopy(state)
    decider = _plan_decider(plan)
    decider.mm_allow_bluff = mm_bluff
    flow.run_day(st, decider)
    return st


def _finish(st: GameState, objective) -> str:
    """1日完走後の日単位の勝敗。"""
    if objective == "no_incident" and any(
            e.get("event") == "incident" and e.get("occurs")
            for e in st.history):   # _slim 済み＝履歴は今日の分だけ
        return MASTERMIND
    if isinstance(objective, tuple) and objective[0] == "no_death" and any(
            not st.characters[n].alive for n in objective[1]
            if n in st.characters):
        # 指定キャラ（フレンド等＝死がループ終了時敗北に繋がる者）を今日死なせない
        return MASTERMIND
    if not st.loop_end_triggered and st.day >= st.script.days_per_loop:
        flow.evaluate_loop_end(st)  # 最終日はループ終了時のボード条件まで判定
    return MASTERMIND if st.defeat else PROTAGONIST


def _day_outcome(state: GameState, plan: list[dict], objective: str,
                 mm_bluff: bool = False) -> str:
    """6枚（脚本家3＋主人公3）を与えた後の開フェーズを完全ミニマックスで解く。"""
    try:
        done = _run_plan(state, plan, mm_bluff)
    except _Frontier as f:
        controller = MASTERMIND if f.actor == "mastermind" else PROTAGONIST
        last = None
        for o in f.options:
            r = _day_outcome(state, plan + [o], objective, mm_bluff)
            if r == controller:
                return r            # 手番側が望む値＝即確定（2値なので単純）
            last = r
        return last                 # 全選択肢が相手側の値
    return _finish(done, objective)


# ---------------------------------------------------------------------------
# 列挙（合法手フロンティアから構築＝手書きの手生成なし）
# ---------------------------------------------------------------------------

def _dedup_key(placements: list[dict]) -> tuple:
    return tuple(sorted(tuple(sorted(o.items())) for o in placements))


def enum_mm_sets_for_sigma(state: GameState, sigma: list[tuple[str, str]],
                           allow_bluff: bool = False) -> list[list[dict]]:
    """σ（(target, kind) の多重集合）に整合する脚本家セット（中身つき）を全列挙。

    主人公の観測＝置き位置のみ。中身の候補は残り手札とカードの置ける先
    （ボードは暗躍+のみ・幻想ボード例外）で絞られる＝これが ∀c の量化範囲。
    ★B-223：allow_bluff=True＝板ダミー（非暗躍札の板置き＝KB `rules/10:70-71`）も
    ∀c の量化範囲に入れる（B-214 時代のσとの整合に必要）。既定 False＝従来と bit 同一。"""
    want = Counter(sigma)
    results: list[list[dict]] = []
    seen: set = set()

    def add(chosen: list[dict]) -> None:
        key = _dedup_key(chosen)
        if key not in seen:
            seen.add(key)
            results.append(chosen)

    def rec(chosen: list[dict], remaining: Counter) -> None:
        try:
            _run_plan(state, chosen, allow_bluff)
        except _Frontier as f:
            if f.actor != "mastermind" or f.decision != "set_card":
                if not remaining:   # 脚本家セットを抜けた＝σを使い切っていれば有効
                    add(chosen)
                return
            for o in f.options:
                k = (o["target"], o["target_kind"])
                if remaining.get(k, 0) > 0:
                    rec(chosen + [o], remaining - Counter([k]))
            return
        if not remaining:           # 1日が強制で完走した縮退ケース
            add(chosen)

    rec([], want)
    return results


def enum_prot_sets(state: GameState, mm_prefix: list[dict],
                   cap: int = 200_000,
                   allow_bluff: bool | None = None) -> list[list[dict]] | None:
    """主人公応手（3席分）の完全列挙。cap 超過は None（→ unknown で棄権）。

    応手の合法性は脚本家の中身に依らない（重ね禁止は「対象」だけを見る）ため、
    代表の mm_prefix 1つで列挙してよい。順序重複は日単位の解決結果に影響しない
    （所有席の違いは 1/L 消費＝翌日以降にのみ効く）ので多重集合で束ねる。
    ★B-223：allow_bluff＝None（既定）は mm_prefix から自動判定（板ダミーを含む
    prefix は開いた候補列でしか置けない＝計画消費の整合のため）。"""
    if allow_bluff is None:
        allow_bluff = _needs_bluff(state, mm_prefix)
    results: list[list[dict]] = []
    seen: set = set()

    def add(chosen: list[dict]) -> None:
        key = _dedup_key(chosen)
        if key not in seen:
            seen.add(key)
            results.append(chosen)

    def rec(chosen: list[dict]) -> bool:
        if len(results) > cap:
            return False
        try:
            _run_plan(state, mm_prefix + chosen, allow_bluff)
        except _Frontier as f:
            if f.actor == "mastermind" or f.decision != "set_card":
                add(chosen)         # 主人公セットを抜けた（縮退：置ける手が無い席等）
                return True
            for o in f.options:
                if not rec(chosen + [o]):
                    return False
            return True
        add(chosen)
        return True

    if not rec([]):
        return None
    return results


# ---------------------------------------------------------------------------
# 探索順（存在証明の高速化のみ・健全性に無関係）
# ---------------------------------------------------------------------------

def _order_responses(state: GameState, responses: list[list[dict]]
                     ) -> list[list[dict]]:
    """防御になりやすい応手を先頭へ（見つかれば即確定＝存在証明）。"""
    roles = {n: c.role for n, c in state.characters.items()}
    kp = next((n for n, r in roles.items() if r == "キーパーソン"), None)
    killer = next((n for n, r in roles.items() if r == "キラー"), None)
    culprit = next((i.culprit for i in state.script.incidents
                    if i.day == state.day), None)
    goal = {"学校", "神社", "病院", state.rule_y_board_x} - {None}

    def score1(p: dict) -> float:
        card, tgt = p["card"], p["target"]
        s = 0.0
        if card == "暗躍禁止" and (tgt in goal or tgt in (kp, killer)):
            s += 3
        if card == "不安-1" and tgt == culprit:
            s += 3
        if card == "移動禁止" and tgt in (killer, culprit):
            s += 2
        if card.startswith("移動") and card != "移動禁止" and tgt == kp:
            s += 2
        if card in ("友好+1", "友好+2"):
            s += 1
        return s

    return sorted(responses, key=lambda R: -sum(score1(p) for p in R))


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------

def classify_day(state: GameState, sigma=None, mm_set=None,
                 objective: str = "survive", cap: int = 200_000,
                 allow_bluff: bool | None = None) -> dict:
    """日開始局面 state（run_day 直前）とσで、今日の完全判定を行う。

    sigma: [(target, kind), ...]（3件）。mm_set（実対局の中身つき placements）を
    渡すとσはそこから取る。返り値:
      {"verdict": "defense_exists"|"mate"|"guessing"|"unknown",
       "defense": R or None, "mate_content": c or None,
       "n_contents": int, "n_responses": int}

    ★B-223 allow_bluff＝∀c の量化範囲に板ダミー中身を入れるか。
      None（既定）＝mm_set から自動判定：実セットが板ダミーを含む日だけ開く
      （＝従来判定できていた日は bit 同一のまま、B-214 時代の量化不能を解消）。
      ★既知の限界（正直に明記）＝実セットが板ダミーを**含まない**日は従来どおり閉じる
      ＝「脚本家はダミーも置き得た」という認識的な量化範囲より狭い（従来からの仕様）。
      完全な範囲で問うときは allow_bluff=True を明示する。
    """
    if sigma is None:
        if not mm_set:
            raise ValueError("sigma か mm_set のどちらかが必要")
        sigma = [(p["target"], p["target_kind"]) for p in mm_set]
    base = _slim(state)
    if allow_bluff is None:
        allow_bluff = _needs_bluff(base, mm_set)
    contents = enum_mm_sets_for_sigma(base, list(sigma), allow_bluff)
    if not contents:
        raise ValueError(f"σに整合する脚本家セットが無い（snapshotとσの不整合）: {sigma}")
    responses = enum_prot_sets(base, contents[0], cap=cap, allow_bluff=allow_bluff)
    if responses is None:
        return {"verdict": "unknown", "reason": f"応手が{cap}通り超",
                "defense": None, "mate_content": None,
                "n_contents": len(contents), "n_responses": -1}
    ordered = _order_responses(base, responses)

    ocache: dict = {}

    def outcome(M: list[dict], R: list[dict]) -> str:
        key = (_dedup_key(M), _dedup_key(R))
        if key not in ocache:
            ocache[key] = _day_outcome(base, M + R, objective, allow_bluff)
        return ocache[key]

    # 防御の存在証明（∃R ∀c）：見つかれば確定。全滅なら「防御なし」も確定（完全列挙）。
    defense = None
    for R in ordered:
        if all(outcome(M, R) == PROTAGONIST for M in contents):
            defense = R
            break
    if defense is not None:
        return {"verdict": "defense_exists", "defense": defense,
                "mate_content": None,
                "n_contents": len(contents), "n_responses": len(responses)}

    # 詰みの存在証明（∃c ∀R）：ordered の反証（凌ぐR）を先に探すため走査順は ordered。
    for M in contents:
        if all(outcome(M, R) == MASTERMIND for R in ordered):
            return {"verdict": "mate", "defense": None, "mate_content": M,
                    "n_contents": len(contents), "n_responses": len(responses)}

    # ∀R∃c 負け（防御なし）かつ ∀c∃R 凌ぎ（詰みなし）＝読み合い。
    return {"verdict": "guessing", "defense": None, "mate_content": None,
            "n_contents": len(contents), "n_responses": len(responses)}


def find_defenses(state: GameState, sigma=None, mm_set=None,
                  objective: str = "survive", cap: int = 200_000,
                  max_found: int | None = None,
                  allow_bluff: bool | None = None) -> list[list[dict]] | None:
    """防御応手（∀中身で凌ぐR）を列挙する — 学習の教師信号用（引き継ぎ§5）。

    classify_day の defense 探索の find-all 版。「AIの選択が防御集合に入っているか」
    で採点する用途（複数解があるため単一の防御例との一致で罰しない）。
    max_found で打ち切り可。応手列挙が cap 超過なら None（棄権）。
    allow_bluff＝classify_day と同じ（★B-223・None＝mm_set から自動判定）。"""
    if sigma is None:
        if not mm_set:
            raise ValueError("sigma か mm_set のどちらかが必要")
        sigma = [(p["target"], p["target_kind"]) for p in mm_set]
    base = _slim(state)
    if allow_bluff is None:
        allow_bluff = _needs_bluff(base, mm_set)
    contents = enum_mm_sets_for_sigma(base, list(sigma), allow_bluff)
    if not contents:
        raise ValueError(f"σに整合する脚本家セットが無い: {sigma}")
    responses = enum_prot_sets(base, contents[0], cap=cap, allow_bluff=allow_bluff)
    if responses is None:
        return None
    ocache: dict = {}

    def outcome(M, R):
        key = (_dedup_key(M), _dedup_key(R))
        if key not in ocache:
            ocache[key] = _day_outcome(base, M + R, objective, allow_bluff)
        return ocache[key]

    found: list[list[dict]] = []
    for R in _order_responses(base, responses):
        if all(outcome(M, R) == PROTAGONIST for M in contents):
            found.append(R)
            if max_found is not None and len(found) >= max_found:
                break
    return found


def defense_exists(state: GameState, sigma=None, mm_set=None,
                   objective: str = "survive", cap: int = 200_000,
                   max_scan: int | None = None,
                   allow_bluff: bool | None = None) -> str:
    """σに対して凌ぐ応手が「在るか」だけを、走査予算 max_scan つきで判定する。

    返り値: "yes"（防御を発見）/ "no"（全応手を走査し1つも無い＝詰み側）/
            "unknown"（応手列挙が cap 超過、または max_scan 内に見つからず未走査が残る）。
    防御ミス率の分母（defended+miss）は "no"/"unknown" を含まない＝予算で打ち切って
    "unknown" に丸めても指標は不変（no_defense証明の全走査コストを避けるための実用版）。
    allow_bluff＝classify_day と同じ（★B-223・None＝mm_set から自動判定）。
    """
    if sigma is None:
        if not mm_set:
            raise ValueError("sigma か mm_set のどちらかが必要")
        sigma = [(p["target"], p["target_kind"]) for p in mm_set]
    base = _slim(state)
    if allow_bluff is None:
        allow_bluff = _needs_bluff(base, mm_set)
    contents = enum_mm_sets_for_sigma(base, list(sigma), allow_bluff)
    if not contents:
        raise ValueError(f"σに整合する脚本家セットが無い: {sigma}")
    responses = enum_prot_sets(base, contents[0], cap=cap, allow_bluff=allow_bluff)
    if responses is None:
        return "unknown"
    ocache: dict = {}

    def outcome(M, R):
        key = (_dedup_key(M), _dedup_key(R))
        if key not in ocache:
            ocache[key] = _day_outcome(base, M + R, objective, allow_bluff)
        return ocache[key]

    ordered = _order_responses(base, responses)
    scanned = 0
    for R in ordered:
        if max_scan is not None and scanned >= max_scan:
            return "unknown"        # 予算切れ＝未走査が残る（no とは言い切れない）
        scanned += 1
        if all(outcome(M, R) == PROTAGONIST for M in contents):
            return "yes"
    return "no"                     # 全応手を走査し防御なし


def is_defense(state: GameState, response: list[dict], sigma=None, mm_set=None,
               objective: str = "survive",
               allow_bluff: bool | None = None) -> bool:
    """特定の応手 response が σ に整合する全中身に対して凌ぐか（安価な単一チェック）。

    find_defenses が全応手を列挙するのに対し、これは response 1つを σ整合の
    中身（≤百通り程度）に当てるだけ＝|contents| 局で判定できる。
    「AIが実際に打った手は防御だったか」の採点に使う（defense_audit / 係数学習）。
    response の要素は {card,target,target_kind}（owner は無視）。
    allow_bluff＝classify_day と同じ（★B-223・None＝mm_set から自動判定）。
    """
    if sigma is None:
        if not mm_set:
            raise ValueError("sigma か mm_set のどちらかが必要")
        sigma = [(p["target"], p["target_kind"]) for p in mm_set]
    base = _slim(state)
    if allow_bluff is None:
        allow_bluff = _needs_bluff(base, mm_set)
    contents = enum_mm_sets_for_sigma(base, list(sigma), allow_bluff)
    if not contents:
        raise ValueError(f"σに整合する脚本家セットが無い: {sigma}")
    R = [{"card": p["card"], "target": p["target"],
          "target_kind": p["target_kind"]} for p in response]
    return all(_day_outcome(base, M + R, objective, allow_bluff) == PROTAGONIST
               for M in contents)
