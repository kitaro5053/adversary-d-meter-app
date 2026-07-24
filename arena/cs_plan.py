# -*- coding: utf-8 -*-
"""DP-2 2M-2：CS（カバーストーリー）選定の機械化＋資源競合行列（**advisory＝表示/助言専用**）。

「本命でない勝ち筋を囮として演出する」の形式化（DP-2 doc §2）。設計＝
docs/提案_DP2_2M2_CS選定と資源競合行列_2026-07-19.md（§9＝ユーザー裁定が実装の確定条件）。

## 2段構成（filter → rank）＝混ぜない
- **filter（足切り）＝適格条件1・4**：脚本上ありえない／相手beliefで棄却済み／演出が本命を
  リークする候補を落とす。
- **rank（順位付け）＝適格条件2・3**：★**偽装コストが第1キー・資源競合回避が第2キー**
  （ユーザー裁定 2026-07-19）。
  > 理由（手練れの実感・提案書§9-2）＝「**CSを維持するのは大変＝偽装コストが安くないと
  > 博打になる**」。＝この優先順を後から逆転させない（競合回避を第1キーにしたくなる誘惑への抑止）。

## advisory（2M-3まで意思決定に入れない）
このモジュールは heuristic の採点（_score_set/_score_ability）を**呼ばない・変えない**＝
ベンチ bit-for-bit 不変。mm への接続は 2M-3。

## 再実装しない（DP-2 doc §3）
- 勝ち筋の liveness＝2M-1 `evaluate_tree_mm`（AIB）
- 折り手データ＝`defense_plan.plan_for_belief` の `Threat.conditions[].breaks[].card`
  ★`options=defender_toolkit(view)` を渡すこと（渡さないと breaks が空＝実測済みの落とし穴）
- kind→node 写像＝2M-1 と同じ `losstree_eval._kind_index/_node_matches_threat`
- 情報衛生＝`mm_lint.lint_move`（無駄手と判定される手はCS演出に使わない＝A-20/D5の陽性利用）
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 資源クラス（ユーザー裁定1＝move と move_ban は分ける）
# ---------------------------------------------------------------------------
# 主人公の防御資源＝「その日に置ける札の種類×枚数」。同じ資源を食う勝ち筋を2本立てても、
# 相手は1枚で両方に対応できる＝CSとして機能しない（＝「二者択一の強制」の逆）。
# ★move（動かす）と move_ban（止める）を分けるのはユーザー裁定：相手の択が変わるため。
RESOURCE_OF_CARD: dict[str, str] = {
    "暗躍禁止": "anyaku_ban",
    "移動禁止": "move_ban",
    "移動↑↓": "move", "移動←→": "move", "移動斜め": "move",
    "不安-1": "unrest_minus",
    "友好+1": "goodwill", "友好+2": "goodwill",
}
RESOURCE_CLASSES = ("anyaku_ban", "move_ban", "move", "unrest_minus", "goodwill", "other")


@dataclass(frozen=True)
class CSPlan:
    """CS選定の結果（advisory）。main＝本命の勝ち筋／cs＝囮に選んだ勝ち筋。"""
    main: str
    cs: str | None
    disguise: float                  # 偽装コスト 0..1（低いほど良い＝★第1キー）
    conflict: float | None           # 資源競合 0..1（低いほど良い＝第2キー）／None＝未測定
    info_cost: float                 # CSを進めると漏れる情報量（2M-1 由来）
    reason: str
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (node_id, 落選理由)


# ---------------------------------------------------------------------------
# 資源プロファイルと競合（条件3）
# ---------------------------------------------------------------------------
def _threats_of_node(threats, node_id: str) -> list:
    """脅威（負け筋）のうち node_id に写像されるもの＝2M-1 と同じ写像を流用（再実装しない）。"""
    from arena.losstree_eval import _kind_index, _node_matches_threat
    idx = _kind_index()
    return [t for t in threats
            if node_id in idx.get(t.kind, ()) and _node_matches_threat(node_id, t)]


# losstree の静的 defenses ラベル → 資源クラス（★カード名ではなく自然文なのでキーワード写像）。
# 長いキーワードを先に見る（「移動禁止」を「移動」より先に）。
_DEFENSE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("暗躍禁止", "anyaku_ban"), ("暗躍除去", "goodwill"), ("暗躍供給を断つ", "anyaku_ban"),
    ("移動禁止", "move_ban"), ("ピン", "move_ban"),
    ("退避", "move"), ("引き込み", "move"), ("位置細工", "move"), ("注入", "move"),
    ("不安-1", "unrest_minus"), ("冷却", "unrest_minus"),
    ("友好", "goodwill"), ("♡", "goodwill"),
)


def _static_profile(node_id: str) -> dict[str, float]:
    """losstree の `conds[].defenses`（静的な防御手段ラベル）から資源分布を引く。

    ★動的な threats（`plan_for_belief`）との**相補**として要る：実測（BTX/FS 各4脚本・全日・
    337ペア）で threats 由来のプロファイルが非空になるのは board.x/pro.hospital2/board.shrine/
    jaki.seal の**ボード系4ノードだけ**＝キャラ殺害系（kp.*/fr.*/pro.killer4…）は常に空で、
    資源競合が 337中3ペアしか測れなかった。逆に静的 defenses は キャラ系に厚く
    board.school/board.x/board.kp_anyaku が0件＝ちょうど裏返し。よって両者の和を取る。
    """
    from arena.losstree import LOSS_NODES
    prof: Counter = Counter()
    node = LOSS_NODES.get(node_id) or {}
    for c in node.get("conds", []):
        for d in c.get("defenses", []):
            label = d.get("label", "")
            for kw, cls in _DEFENSE_KEYWORDS:
                if kw in label:
                    prof[cls] += 1.0
                    break
            else:
                prof["other"] += 1.0
    return dict(prof)


def _dynamic_profile(node_id: str, threats) -> dict[str, float]:
    """防御プランナの脅威（`Threat.conditions[].breaks[].card`）から資源分布を引く＝**現局面の実測**。

    条件のOR（どれか1枚で折れる）は 1/len(breaks) で按分＝「その条件を折るのに、どの資源が
    どれだけ要るか」の期待値。AND条件は足し合わせる。正規化しない（合成側で行う）。
    """
    prof: Counter = Counter()
    for t in _threats_of_node(threats, node_id):
        for c in t.conditions:
            brks = list(c.breaks)
            if not brks:
                continue                      # 折れない条件＝主人公の資源を食わない
            w = 1.0 / len(brks)
            for b in brks:
                prof[RESOURCE_OF_CARD.get(b.card, "other")] += w
    return dict(prof)


def _normalise(prof: dict[str, float]) -> dict[str, float]:
    total = sum(prof.values())
    return {k: v / total for k, v in prof.items()} if total else {}


def resource_profile_parts(node_id: str,
                           threats) -> tuple[dict[str, float], dict[str, float]]:
    """`(dynamic, static)` を**由来を保ったまま**返す（それぞれ個別に正規化）。

    ★FableA確認事項（2M-2 land時）への対応＝`resource_profile` は両者を合算後に正規化するため
    出力から由来を復元できない。2M-3以降で「**現局面の実測（dynamic）を優先**」といった
    精緻化を入れる余地を残すための分離口（既存の `resource_profile` の戻り値は不変＝additive）。
    """
    return _normalise(_dynamic_profile(node_id, threats)), _normalise(_static_profile(node_id))


def resource_profile(node_id: str, threats) -> dict[str, float]:
    """その勝ち筋を折るために主人公が使う資源の分布（合計1に正規化・空なら空dict）。

    動的（threats の breaks）＋静的（losstree defenses）の**和**＝どちらかが疎でも測れる
    （理由は `_static_profile` の docstring）。由来を分けて取りたい場合は
    `resource_profile_parts`（合成前の2本を返す）を使う。
    """
    prof: Counter = Counter(_static_profile(node_id))
    prof.update(_dynamic_profile(node_id, threats))
    return _normalise(dict(prof))


def resource_conflict(prof_a: dict[str, float], prof_b: dict[str, float]) -> float | None:
    """0＝競合なし（別資源を食う＝良いCS）／1＝完全競合（同じ札で両方折れる＝CSにならない）。
    **None＝未測定**（どちらかの勝ち筋がまだ防御プランナの脅威として立っていない）。

    ＝資源プロファイルのコサイン類似度。★None を 1.0（最悪）に潰さないのは実測に基づく：
    ループ初日の脅威はボード系しか立たず、キャラ殺害系（kp.factor/fr.incident 等）は
    プロファイルが空になる＝「資源を食わせない囮」ではなく**まだ測れないだけ**。
    ここを最悪扱いにすると、初日は全候補が競合1.00＝第2キーが定数化して死ぬ
    （実測 BTX6/FS3/BTX14/BTX0 で全件1.00になった）。未測定は rank では中立に落とし、
    表示では「未測定」と明示する（＝黙って悪い値を出さない）。
    """
    if not prof_a or not prof_b:
        return None
    keys = set(prof_a) | set(prof_b)
    dot = sum(prof_a.get(k, 0.0) * prof_b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in prof_a.values()))
    nb = math.sqrt(sum(v * v for v in prof_b.values()))
    return round(dot / (na * nb), 4) if na and nb else None


# ---------------------------------------------------------------------------
# 偽装コスト（条件2）＝★rank の第1キー
# ---------------------------------------------------------------------------
def arming_moves(node_id: str, view_mm: dict) -> set[tuple[str, str]]:
    """その勝ち筋を進める mm の手（(target, target_kind) の集合）＝偽装コストの材料。

    ★再実装しない：ノード→標的の対応は 2M-1 の `_NODE_FIXED_BOARD`（board.school→学校 等）と
    losstree の `gate`（roles/roles_any/incidents/incidents_any）を流用する。脚本ごとに変わるもの
    （board.x の板・事件の犯人・KPの実キャラ）は view から解決する＝ここで表を持たない。
    ＝「暗躍/不安を積む先（ボード/キャラ）」がその勝ち筋を進める手＝CS演出に使える手でもある。
    """
    from arena.losstree import LOSS_NODES
    from arena.losstree_eval import _NODE_FIXED_BOARD
    node = LOSS_NODES.get(node_id) or {}
    gate = node.get("gate") or {}
    out: set[tuple[str, str]] = set()
    roles_map = view_mm.get("roles") or {}

    # ① 固定ボード（board.school=学校／board.shrine・jaki.seal=神社）＝2M-1 の写像をそのまま使う
    fixed = _NODE_FIXED_BOARD.get(node_id)
    if fixed:
        out.add((fixed, "board"))
    # ② board.x（復讐者の灯火／巨大時限爆弾Xの存在）＝板が脚本ごとに変わる＝view から解決
    if node_id == "board.x":
        bx = view_mm.get("rule_y_board_x")
        if bx:
            out.add((bx, "board"))
    # ③ board.kp_anyaku（僕と契約しようよ！）＝KP本人へ暗躍を積む＝キャラ標的
    if node_id == "board.kp_anyaku":
        out |= {(n, "character") for n, rr in roles_map.items() if rr == "キーパーソン"}
    # ④ 事件ゲート＝その事件の犯人に不安を積むのが「進める手」（病院の事件は板でもある）
    inc_names = set(gate.get("incidents") or []) | set(gate.get("incidents_any") or [])
    if inc_names:
        for inc in (view_mm.get("incidents") or []):
            if inc.get("name") in inc_names and inc.get("culprit"):
                out.add((inc["culprit"], "character"))
        if node_id == "pro.hospital2":
            out.add(("病院", "board"))
    # ⑤ 役職ゲート＝真相の配役から実キャラへ解決（キラー/ファクター/フレンド等の標的）
    want = set(gate.get("roles") or []) | set(gate.get("roles_any") or [])
    out |= {(n, "character") for n, rr in roles_map.items() if rr in want}
    return out


def disguise_cost(main_id: str, cs_id: str, view_mm: dict) -> float:
    """CSを演出するのに本命と**別の手**をどれだけ余分に使うか（0＝タダ／1＝完全に別の手）。

    ★rank の**第1キー**（ユーザー裁定 2026-07-19・提案書§9-2）：
      > 「**CSを維持するのは大変＝偽装コストが安くないと博打になる**」（手練れの実感）
    ＝本命の手がそのままCSの演出になるペアを最優先で選ぶ（この優先順を後から逆転させない）。
    filler理論との接続＝mmは毎日3枚必ず置く＝「どうせ置く枠で演出できるCS」が最良。
    """
    main_moves = arming_moves(main_id, view_mm)
    cs_moves = arming_moves(cs_id, view_mm)
    if not cs_moves:
        return 1.0                            # 演出する手が無い＝CSとして立てられない
    shared = len(main_moves & cs_moves)
    return round(1.0 - shared / len(cs_moves), 4)


# ---------------------------------------------------------------------------
# 情報衛生（条件4）＝mm_lint 流用
# ---------------------------------------------------------------------------
def cs_moves_are_clean(node_id: str, view_mm: dict) -> list[str]:
    """CS演出の手を mm_lint に掛け、無駄手（D1..D6）に当たる理由を返す（空＝衛生的）。

    ★「透明に死んだ手でCSを演出する」＝情報を持つ相手には見え見え＝CSとして逆効果
    （A-20/D5 の教訓の陽性利用）。新しいリーク判定は作らない＝既存検出器をそのまま使う。
    """
    from arena.mm_lint import lint_move
    out: list[str] = []
    for tgt, kind in sorted(arming_moves(node_id, view_mm)):
        mv = {"card": "暗躍+1", "target": tgt, "target_kind": kind}
        out += [f"{tgt}: {r}" for _d, r in lint_move(view_mm, mv)]
    return out


# ---------------------------------------------------------------------------
# 退化decoyの足切り（DP-2 2M-3 Stage 0・FableA裁定1・2026-07-20）
# ---------------------------------------------------------------------------
def is_degenerate_decoy(main_id: str, cs_id: str, view_mm: dict) -> str:
    """decoy として**機能しない**候補を弾く（該当理由の文字列／空文字＝適格）。

    ★背景（実測で見つけた構造的欠陥）：`disguise_cost` は手の共有が多いほど安く、共有が
    **完全**なら 0.0＝第1キーで最優先になる。しかしそれは「囮が本命と同じ場所を指している」
    ＝**独立した存在感ゼロ＝囮として無価値**。実対局218件中 **89件（40.8%）**がこれだった。
    ＝ユーザー裁定「偽装コストが第1キー」は正しい（維持コストの高い囮は博打）が、
    「安い」の下限に**囮として機能する下限**が無かった。
    ★直し方＝**順位付けには触らず適格条件を足す**（キー順の哲学を変えない・FableA承認）。

    条件1＝`arming(cs) ⊆ arming(main)`：囮を進める手が本命を進める手に含まれる＝
      相手から見て別方向の圧力が立たない＝吸引ゼロ。
    """
    main_moves = arming_moves(main_id, view_mm)
    cs_moves = arming_moves(cs_id, view_mm)
    if not cs_moves:
        return "演出する手が無い（立てられない）"
    if cs_moves <= main_moves:
        return "本命と同じ手しか使わない（独立した存在感ゼロ＝吸引しない）"
    return ""


def main_node_of(path: str, goal_boards, view_mm: dict, live: set[str]) -> str | None:
    """現行の経路名（board/kp/killer4）＋ goal_boards → 現局面で live な losstree ノード。

    ★Stage 0b の切り分けで見つけた**写像の穴の修正**（FableA条件2）：
      `board` 経路は「ゴールボードの敗北」だが、**どの板か**は goal_boards で決まる。
      旧監査の粗い表は board.school/shrine/x/kp_anyaku だけを見ており、
      **`goal_boards={病院}`（ルールY=殺人計画などで A-18 の病院ゲートだけが立つ脚本）を
      拾えず「本命が live でない」と誤判定**していた（5日14件中5件がこれ）。
      病院ゴールは losstree では `pro.hospital2`（病院暗躍≥2で主人公殺害）に当たる。
    ★残り9件は写像の穴ではなく**評価器の判定差**（board.shrine が counter=1.00 で blocked＝
      「巫女の神社暗躍除去が安定して成立する」と評価器が見ている）。これは 2M-1 側の較正問題で
      Stage 1 の守備範囲外＝呼び出し側で**現行ロジックへフォールバック**する（安全側）。
    """
    gb = set(goal_boards or ())
    cands: list[str] = []
    if path == "board":
        for b in gb:
            if b == "学校":
                cands.append("board.school")
            elif b == "神社":
                cands += ["board.shrine", "jaki.seal"]
            elif b == "病院":
                cands.append("pro.hospital2")       # ★穴の修正
            if b and b == view_mm.get("rule_y_board_x"):
                cands.append("board.x")
    elif path == "kp":
        cands += ["board.kp_anyaku", "kp.killer"]
    elif path == "killer4":
        cands.append("pro.killer4")
    for nid in cands:
        if nid in live:
            return nid
    return None


def decoy_board_of(node_id: str | None, view_mm: dict) -> str | None:
    """ノード → 偽装先ボード（無ければ None）。Stage 1 の board_only と heuristic 配線の単一ソース。

    ★病院は除く：病院は「病院の事件」で**実効果が出る**板＝偽装（捨て暗躍）ではなく本物の
    第2経路になる（現行 `_prefs` も病院を偽装先から外している＝その判断を引き継ぐ）。
    """
    if not node_id:
        return None
    boards = [t for t, k in arming_moves(node_id, view_mm) if k == "board" and t != "病院"]
    return boards[0] if boards else None


def is_fully_competing(conflict: float | None) -> bool:
    """資源競合が完全（1.0）＝decoy失格か。★未測定（None）は落とさない。

    ★FableA裁定1（2026-07-20）＝**除外で正しい**。理由をそのまま記す：
      資源競合1.00 のペアは「**相手の1枚が両方に効く**」＝吸引ゼロ＝decoy の定義
      （二者択一の強制）を満たさない。「同じ板に2本の敗北ルールが載る」ケースの正当な価値は
      decoy ではなく**本命の強化／レース**であり、それは 2M-1 の FORCED/winning 判定が
      既に扱う領域＝**decoy枠から外しても価値は失われない**。
    """
    return conflict is not None and conflict >= 1.0


# ---------------------------------------------------------------------------
# 本体：filter → rank
# ---------------------------------------------------------------------------
def choose_cover_story(view_mm: dict, belief, main_id: str, *,
                       info_budget: float = 0.75,
                       w_disguise: float = 1.0, w_conflict: float = 0.5,
                       w_info: float = 0.25,
                       board_only: bool = False) -> CSPlan:
    """本命 main_id に対する decoy を1本選ぶ。

    ★意図的decoyは**1本**（ユーザー裁定3）。filler由来の自然発生decoy（attack_plan の役割）は
    本数に数えない＝ここでは「意図して立てる囮」だけを選ぶ。
    ★重みは引数の既定値に置く＝将来の「作者指示」（自然言語→パラメータ・バックログ§6）が
    外から上書きできる形にしておく（今は使わない・強制もしない）。
    ★Stage 0（2026-07-20）＝退化decoyと完全競合を filter で落とす（`is_degenerate_decoy` /
    `is_fully_competing`）。rank のキー順（偽装コスト第1・資源競合第2）は**不変**。

    `board_only`＝**ボードを標的にできる候補だけ**に絞る（Stage 1）。
    ★根拠＝Stage 0b の実測：現行 `decoy_board` との食い違いの**約7割**（3日28/38・5日57/80）が
    「評価器のdecoyがボードでない」だった＝この絞りなしに置換すると現行のボード偽装が消え、
    「現行と同じ土俵での比較」が成立しない。キャラ標的の解禁は Stage 2。
    """
    from agents.attack_plan import defender_toolkit
    from agents.defense_plan import plan_for_belief
    from arena.losstree_eval import evaluate_tree_mm

    lines = evaluate_tree_mm(view_mm)                     # 2M-1（★belief引数なし＝mmは真相を知る）
    rejected: list[tuple[str, str]] = []

    # 資源プロファイルは filter（完全競合の足切り）でも使うので先に用意する。
    try:
        opts = defender_toolkit(_protagonist_like(view_mm))
        threats, _plan = plan_for_belief(_protagonist_like(view_mm), belief,
                                         options=opts, include_breached=True)
    except Exception:                                     # noqa: BLE001  測れない＝静的分のみで続行
        threats = []
    main_prof = resource_profile(main_id, threats)

    # --- filter（足切り＝条件1・4＋Stage 0の退化/完全競合）--------------------
    cand: list = []
    conf: dict[str, float | None] = {}
    for w in lines:
        if w.node_id == main_id:
            continue
        if w.status not in ("live", "winning"):           # 条件1a：脚本上ありえる／到達しうる
            rejected.append((w.node_id, f"status={w.status}（脚本上ありえない/到達不能）"))
            continue
        if w.info_cost > info_budget:                     # 条件4：演出が本命をリークしすぎない
            rejected.append((w.node_id, f"info_cost={w.info_cost:.2f} > 予算{info_budget}"))
            continue
        dirty = cs_moves_are_clean(w.node_id, view_mm)    # 条件4：演出手が透明に死んでいない
        if dirty:
            rejected.append((w.node_id, f"演出手が無駄手＝見え見え（{dirty[0][:40]}…）"))
            continue
        if board_only and not decoy_board_of(w.node_id, view_mm):   # ★Stage 1
            rejected.append((w.node_id, "ボードを標的にできない（Stage 1＝ボード限定）"))
            continue
        degen = is_degenerate_decoy(main_id, w.node_id, view_mm)   # ★Stage 0
        if degen:
            rejected.append((w.node_id, f"退化decoy＝{degen}"))
            continue
        c = resource_conflict(main_prof, resource_profile(w.node_id, threats))
        if is_fully_competing(c):                                  # ★Stage 0
            rejected.append((w.node_id, "資源競合1.00＝相手の1枚が両方に効く（吸引ゼロ）"))
            continue
        conf[w.node_id] = c
        cand.append(w)

    if not cand:
        return CSPlan(main_id, None, 1.0, None, 0.0,
                      "適格なdecoy候補なし（脚本・情報衛生・退化の足切り後にゼロ）", rejected)

    # --- rank（順位付け＝条件2・3。★偽装コストが第1キー）---------------------
    scored = []
    for w in cand:
        dis = disguise_cost(main_id, w.node_id, view_mm)
        con = conf[w.node_id]
        con_r = 0.5 if con is None else con    # 未測定＝中立に落とす（順位に影響させない）
        # ★第1キー＝偽装コスト（低いほど良い）／第2キー＝資源競合（低いほど良い）／情報は減点。
        score = (1.0 - dis) * w_disguise + (1.0 - con_r) * w_conflict - w.info_cost * w_info
        scored.append((dis, con_r, score, con, w))
    # ソートも「偽装コスト第1・競合第2」を明示（scoreの重みだけに委ねない＝裁定の可読化）
    scored.sort(key=lambda x: (x[0], x[1], -x[2]))
    dis, _cr, _sc, con, best = scored[0]
    con_txt = "未測定" if con is None else f"{con:.2f}"
    return CSPlan(main_id, best.node_id, dis, con, best.info_cost,
                  f"偽装コスト{dis:.2f}（第1キー）・資源競合{con_txt}（第2キー）"
                  f"・info{best.info_cost:.2f}", rejected)


# ---------------------------------------------------------------------------
# DP-2 2M-3 Stage 1'：偽装ボードの belief 駆動選定（2026-07-20・FableA承認）
# ---------------------------------------------------------------------------
# ★これは choose_cover_story（live勝ち筋どうしの decoy 選定）とは**適用範囲が別**：
#   偽装ボードは「この脚本では負け筋でない板」＝実在する勝ち筋の集合には居ない
#   （Stage 1 で board_only decoy ≡ ∅ を証明）。＝相手の belief 空間の住人。
#   よって live ノードでなく**盤面の4板**を候補にし、疑い度（belief周辺確率）で選ぶ。
_RY_TO_BOARD: dict[str, str] = {"守るべき場所": "学校", "封印されしモノ": "神社"}
_BOARDX_RULES: frozenset[str] = frozenset({"復讐者の灯火", "巨大時限爆弾Xの存在"})


def board_suspicion(belief, view_mm: dict) -> dict[str, float]:
    """主人公 belief で各板が「敗北板だ」と疑われている確率（板→p）。

    ＝`belief.rule_marginals()` を板ごとに周辺化する。守るべき場所→学校／封印されしモノ→神社／
    復讐者の灯火・巨大時限爆弾X→view の rule_y_board_x。belief 不能なら空 dict。
    """
    out: Counter = Counter()
    if belief is None:
        return {}
    try:
        marg = belief.rule_marginals() or {}
    except Exception:  # noqa: BLE001  belief 不能（拡張キャスト等）＝疑い測れず
        return {}
    bx = view_mm.get("rule_y_board_x")
    for (ry, _rx), p in marg.items():
        if p <= 0:
            continue
        b = _RY_TO_BOARD.get(ry) or (bx if ry in _BOARDX_RULES else None)
        if b:
            out[b] += p
    return dict(out)


def choose_decoy_board(view_mm: dict, belief, goal_boards, main_node: str | None,
                       threats=None, *, susp_min: float = 0.10) -> tuple[str | None, str]:
    """偽装ボードを1枚選ぶ（無ければ None）。返り値＝(board, 理由)。

    ★キー順（FableA承認 2026-07-20・**適用範囲の線引き**）：
      候補は「盤面の板」＝演出手が常に『その板への暗躍』1種類＝**偽装コストが候補間で定数**。
      ＝`choose_cover_story` の第1キー（偽装コスト）は板候補では効かない（定数）＝座が空く。
      よってここでは **①疑い度 susp（吸引力の実体）②資源競合（cs_plan流用）③残コスト** の順。
      ★これはユーザー裁定「偽装コストが第1キー」に**反しない**：あの裁定は
      *live勝ち筋どうしの decoy 選定*（choose_cover_story）に対するもので、
      *板候補の偽装先選定*（本関数）は**適用範囲が別**。混同しないための明記（FableA指示）。

    ★filter＝ゴール板でない・病院でない（病院は実効果が出る＝本物の第2経路）・susp≥susp_min。
      適格ゼロなら **立てない**（None）＝Stage 1'a の主眼（疑われていない板への捨て暗躍を止める）。
    """
    susp = board_suspicion(belief, view_mm)
    goal = set(goal_boards or ())
    cands = [b for b in ("神社", "学校", "都市")
             if b not in goal and b != "病院" and susp.get(b, 0.0) >= susp_min]
    if not cands:
        return None, ("疑われている非ゴール板なし（susp<%.2f）＝立てない" % susp_min)

    def _board_conflict(b: str) -> float:
        if not (main_node and threats is not None):
            return 0.5                       # 測れない＝中立
        # 板 b の資源プロファイル＝board.* ノードのそれと同型（暗躍禁止/暗躍除去）。
        bnode = {"学校": "board.school", "神社": "board.shrine"}.get(b)
        c = resource_conflict(resource_profile(main_node, threats),
                              resource_profile(bnode, threats)) if bnode else None
        return 0.5 if c is None else c

    ba = view_mm.get("board_anyaku", {}) or {}
    cands.sort(key=lambda b: (-susp[b], _board_conflict(b), max(0, 2 - ba.get(b, 0))))
    best = cands[0]
    return best, ("疑い度%.2f（第1キー）で選定" % susp[best])


def _protagonist_like(view_mm: dict) -> dict:
    """mm view を主人公 view 相当に落とす（防御プランナは主人公視点の view を要求する）。
    ★真相（roles）はそのまま渡さない＝防御側の資源見積りに神視点を混ぜない（健全側）。"""
    v = dict(view_mm)
    v.pop("roles", None)
    v.pop("secret_log", None)
    return v


def node_label(node_id: str) -> str:
    """ノードID → 人間向けの書き下し（★A-43④：`kp.killer` のような内部IDを見せない）。

    ラベルは losstree の定義を流用＝**新規転記なし**。未知IDはそのまま返す（落ちない）。
    """
    from arena.losstree import LOSS_NODES
    return (LOSS_NODES.get(node_id) or {}).get("label") or node_id


def render_cs_md(plan: CSPlan) -> str:
    """CSPlan を markdown に（🛠開発者モード表示用）。"""
    if plan.cs is None:
        return f"**CS候補なし** — {plan.reason}"
    con = ("**未測定**　＝この勝ち筋はまだ防御プランナの脅威として立っていない"
           if plan.conflict is None else
           f"**{plan.conflict:.2f}**　＝相手の同じ札で両方折れてしまわないか")
    return (f"**本命**：{node_label(plan.main)}　／　"
            f"**見せ球（decoy）**：{node_label(plan.cs)}\n\n"
            f"- 偽装コスト（★第1キー・低いほど良い）：**{plan.disguise:.2f}**"
            f"　＝本命の手がそのままCS演出になるか\n"
            f"- 資源競合（第2キー・低いほど良い）：{con}\n"
            f"- 情報コスト：{plan.info_cost:.2f}\n"
            f"- 判定：{plan.reason}")
