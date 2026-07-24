"""DP-1 Stage 2a：ツリー駆動評価器（**表示専用**・AIB 2026-07-18）。

全負け筋ノード（arena/losstree.LOSS_NODES）に status を付けて返す＝DP-1 の核（全数表示）。
**検出は再実装しない**：agents/defense_plan.plan_for_belief が返す (threats, plan＝covered/uncovered)
を losstree ノードへ写像する薄い層（二重実装＝ドリフトの温床を避ける・B-31/B-30② の教訓）。

## status（FableA採択の優先順・2026-07-18）
    script_impossible > gap > pruned > covered > active
- script_impossible：脚本の gate（事件一覧・ルール）で開始時に不成立＝ありえない（グレー優先）。
- gap：検出器が未実装（DETECTOR_GAPS）＝「評価器未接続」を正直に出す（穴の可視化）。現状ゼロ。
- pruned：gate は成立しうるが belief で必要役職が全世界0＝消えた（根拠つきグレー）。
- covered：実在するが plan が折り手で覆えている（B-28 で空振り札は既に除外済み）。
- active：実在する未被覆の負け筋（実在度＝検出器の prob）／gate成立・未発火は prob 0 の active。

## ★2a のスコープ（表示専用＝意思決定に一切触れない）
`evaluate_tree` は plan_for_belief を**読むだけ**＝heuristic の手選択（_plan_recs）を変えない
＝ベンチ bit-for-bit 不変（実測で証明）。covered の2条件精緻化（レース＝B-27）は 2b。
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass

from arena.losstree import LOSS_NODES


@dataclass(frozen=True)
class NodeStatus:
    node_id: str
    status: str          # script_impossible / gap / pruned / covered / active
    prob: float          # active/covered の実在度（0..1）。他は 0.0。
    reason: str          # なぜその status か（pruned の belief 根拠・covered の折り手 等）
    race: str | None = None   # 2b で埋める（レース判定）。2a では None。


# --- 検出器 kind → ノード の写像（losstree の detector 欄から導出＝単一ソース） -------
def _kind_to_nodes() -> dict[str, set[str]]:
    """各ノードの detector 関数が生成する Threat.kind を inspect で収集し kind→node_ids に。

    ★losstree の detector 欄と defense_plan の実装から機械的に作る＝手書きの写像表を持たない
    （B-31 の教訓＝二重定義はドリフトする）。tests/test_losstree.py が双方向の実在を固定。
    """
    import agents.defense_plan as DP
    fn_kinds: dict[str, set[str]] = {}
    for node in LOSS_NODES.values():
        for fn in (node.get("detector") or "").split("|"):
            fn = fn.strip()
            if not fn or fn in fn_kinds or not hasattr(DP, fn):
                continue
            src = inspect.getsource(getattr(DP, fn))
            fn_kinds[fn] = (set(re.findall(r'Threat\(\s*"([a-z_]+)"', src))
                            | set(re.findall(r'kind="([a-z_]+)"', src)))
    out: dict[str, set[str]] = {}
    for nid, node in LOSS_NODES.items():
        for fn in (node.get("detector") or "").split("|"):
            for k in fn_kinds.get(fn.strip(), ()):
                out.setdefault(k, set()).add(nid)
    return out


_KIND_TO_NODES: dict[str, set[str]] | None = None


def _kind_index() -> dict[str, set[str]]:
    global _KIND_TO_NODES
    if _KIND_TO_NODES is None:
        _KIND_TO_NODES = _kind_to_nodes()
    return _KIND_TO_NODES


# ノードに固定ボードがある場合のみ（board_defeat の写像を board 名で絞る）。
_AREAS_FOR_DORMANT: tuple[str, ...] = ("病院", "神社", "都市", "学校")
#: ★B-39（FableA裁定2＝板限定）：dormant の (b) 進行度ゼロ判定を当てる**板由来の脅威 kind のみ**。
#  キャラ由来（kp.killer 等＝ラベルにエリア名が出るが板カウンタとは無関係）へ広げると誤判定する
#  （実測＝KPとキラーが既に同居している脅威を「未活性」にしてしまう＝テストが捕捉・2026-07-20）。
_BOARD_DRIVEN_KINDS: frozenset[str] = frozenset({"board_defeat", "hospital_protagonist"})
_NODE_FIXED_BOARD = {"board.school": "学校", "board.shrine": "神社", "jaki.seal": "神社"}
# 事件系ノードが要求する事件名（incident_vip の写像を事件名で絞る）。
_NODE_INCIDENT = {"kp.incident_murder": "殺人事件", "kp.incident_hospital": "病院の事件"}


def _node_matches_threat(nid: str, threat) -> bool:
    """kind が一致した候補ノードのうち、threat の具体（ボード名/事件名/被害者役職）で更に絞る。

    ★gate（rule/incident・script_possible）が既に多くを絞る＝ここは gate では割れない
      同種内の区別（board_defeat の4ノード・incident_vip の3ノード）だけを見る。判断材料が
      無ければ True（過剰にグレーにしない＝健全側＝belief/表示の一般方針）。
    """
    label = getattr(threat, "label", "") or ""
    fixed_board = _NODE_FIXED_BOARD.get(nid)
    if fixed_board is not None and fixed_board not in label:
        return False
    inc = _NODE_INCIDENT.get(nid)
    if inc is not None and inc not in label:
        return False
    # fr.incident（フレンド）vs kp.incident_*（KP）＝incident_vip の被害者役職で分ける
    if nid == "fr.incident" and "フレンド" not in label:
        return False
    if nid in ("kp.incident_murder", "kp.incident_hospital") and "フレンド" in label:
        return False
    # fr.sk（フレンド）vs kp.sk（KP）
    if nid == "fr.sk" and "フレンド" not in label:
        return False
    if nid == "kp.sk" and "フレンド" in label:
        return False
    return True


# --- gate（script_impossible / pruned の材料） -------------------------------
def _possible_from_belief(view: dict, belief) -> tuple[set, set, set, set]:
    """belief から (可能役職, 可能ルールY, 可能ルールX, 公開事件) を取り出す（p>0 のもの）。"""
    roles: set = set()
    for dist in (belief.role_marginals() or {}).values():
        roles |= {r for r, p in dist.items() if p > 0}
    ry: set = set()
    rx: set = set()
    for (y, xs), p in (belief.rule_marginals() or {}).items():
        if p > 0:
            ry.add(y)
            rx |= set(xs)
    incidents = {i.get("name") for i in view.get("incidents", []) if i.get("name")}
    return roles, ry, rx, incidents


def _gate_structural_ok(gate: dict, possible_ry: set, possible_rx: set,
                        incidents: set) -> bool:
    """事件・ルールの gate（脚本の静的事実＝script_impossible の材料）。役職は含めない。"""
    if "incidents" in gate and not all(i in incidents for i in gate["incidents"]):
        return False
    if "incidents_any" in gate and not any(i in incidents for i in gate["incidents_any"]):
        return False
    if "rule_y" in gate and not any(y in possible_ry for y in gate["rule_y"]):
        return False
    if "rule_x" in gate and not any(x in possible_rx for x in gate["rule_x"]):
        return False
    return True


def _gate_roles_ok(gate: dict, possible_roles: set) -> bool:
    """役職の gate（belief で消えていないか＝pruned の材料）。roles_any/or_rule_x も見る。"""
    if "roles" in gate and not all(r in possible_roles for r in gate["roles"]):
        return False
    if "roles_any" in gate or "or_rule_x" in gate:
        any_role = any(r in possible_roles for r in gate.get("roles_any", []))
        any_rule = bool(gate.get("or_rule_x"))   # or_rule_x はルール側で拾う（構造ゲート済み）
        if not (any_role or any_rule):
            return False
    return True


def _is_racing(threat) -> bool:
    """★2b（B-27・保守側）：covered だが「止まらない供給」を位置で対抗しているだけか。

    脅威の条件（AND）に **unbreakable な供給条件**（折り手が無く note で理由が付いた条件＝検出器が
    『暗躍禁止で止まらないクロマク/不穏な噂/黒猫』等の供給と判定した条件）がある時 True。
    ＝別の条件（位置＝移動禁止/移動）を毎ターン折って被覆しているが、供給は止まっていない＝
    B-27 のジリ貲レース（恒久の折り手＝供給源の排除/供給圏外への退避が要る）。

    ★保守側（FableA裁定 2026-07-18＝過小・グレーにしすぎない）：供給が**暗躍禁止で止まらない**
    （クロマクのmm能力フェイズ供給・不穏な噂・黒猫）と検出器が判定した条件（note に「止まらない」）
    がある時**だけ** race にする。「暗躍禁止/移動を確保」（＝止まる供給・カードを持てば折れる＝
    枚数の問題であってレースではない）は race にしない＝誤った「レース負け」表示で脅威を隠さない。
    ★Threat.race フラグ（移動可クロマク等＝位置/情報で勝つ）も併せて拾う（検出器の race 判定）。
    """
    if getattr(threat, "race", False):
        return True
    return any(not c.breakable and "止まらない" in (c.note or "")
               for c in getattr(threat, "conditions", ()))


# --- 本体 -------------------------------------------------------------------
def evaluate_tree(view: dict, belief, options: list | None = None,
                  *, seats: int = 3) -> list[NodeStatus]:
    """全負け筋ノードに status を付ける（表示専用・意思決定に触れない）。

    plan_for_belief を読むだけ＝ベンチ bit-for-bit 不変（heuristic の _plan_recs は別呼び）。
    """
    from agents.defense_plan import plan_for_belief

    possible_roles, possible_ry, possible_rx, incidents = _possible_from_belief(view, belief)
    kind_idx = _kind_index()

    # 検出（再利用）＝threats + plan.covered。例外時は空（表示は script_impossible/pruned のみ）。
    # 値＝(prob, covered_label or None, racing)。racing＝covered だが止まらない供給条件を持つ（B-27）。
    node_active: dict[str, tuple[float, str | None, bool]] = {}
    try:
        threats, plan = plan_for_belief(view, belief, options=options)
        for t in threats:
            covered_label = plan.covered.get(id(t))
            racing = _is_racing(t) if covered_label else False
            t_area = (next((a for a in _AREAS_FOR_DORMANT if a in (t.label or "")), None)
                      if t.kind in _BOARD_DRIVEN_KINDS else None)
            for nid in kind_idx.get(t.kind, ()):
                if not _node_matches_threat(nid, t):
                    continue
                cur = node_active.get(nid)
                if cur is None or t.prob > cur[0]:
                    node_active[nid] = (t.prob, covered_label, racing, t_area)
    except Exception:  # noqa: BLE001  握り潰さず「未評価」として下で active(0) に落ちる
        node_active = {}

    out: list[NodeStatus] = []
    for nid, node in LOSS_NODES.items():
        gate = node.get("gate", {})
        # 1) script_impossible＝事件/ルールの静的ゲート不成立（ありえない脚本＝グレー優先）
        if not _gate_structural_ok(gate, possible_ry, possible_rx, incidents):
            out.append(NodeStatus(nid, "script_impossible", 0.0,
                                  "脚本の事件/ルールで開始時に不成立"))
            continue
        # 2) gap＝検出器が未実装（評価器未接続を正直に出す＝穴の可視化・DP-1 の目的）。
        #   B-34 で kp.factor を接地して現状ゼロ。新たな未接地ノードが出たらここで gap 表示。
        if node.get("detector") is None:
            out.append(NodeStatus(nid, "gap", 0.0, "検出器が未実装（評価器未接続）"))
            continue
        # 3) pruned＝belief で必要役職が全世界0
        if not _gate_roles_ok(gate, possible_roles):
            out.append(NodeStatus(nid, "pruned", 0.0,
                                  "belief で必要役職が全ての可能世界から消えた"))
            continue
        # 4) ★B-39：dormant（未活性）＝gate成立・検出器あり・**脅威がまだ進行していない**。
        #    covered の実意味が「折り手が在る」に化けていた問題の是正（開始局面で board系だけが
        #    covered・kp.anyaku は active(0) という非一貫＝ユーザー実機FB）。判定は既存材料のみ：
        #      (a) 検出器が脅威を出していない（＝旧 active(prob0)「未発火」）
        #      (b) 脅威は出たが**対象板の暗躍が0**（板限定＝FableA裁定2。キャラ系は従来挙動に落とす）
        act = node_active.get(nid)
        if act is None:
            out.append(NodeStatus(nid, "dormant", 0.0, "まだ立っていない（監視のみ）"))
            continue
        prob, cov, racing, t_area = act
        # ★2b（B-27）のレース判定は dormant より**優先**：板カウンタが0でも「暗躍禁止で止まらない
        #   供給」は既に走っている能動的脅威＝未活性ではない（テストが順序バグを捕捉・2026-07-20）。
        if not racing and t_area and (view.get("board_anyaku", {}) or {}).get(t_area, 0) == 0:
            out.append(NodeStatus(nid, "dormant", prob,
                                  f"まだ立っていない（監視のみ）＝{t_area}の暗躍0"))
            continue
        if act is not None:
            if racing:
                # ★2b（B-27）：位置で covered だが供給が止まらない（暗躍禁止で止まらないクロマク/
                #   噂/黒猫＝unbreakable な供給条件）＝毎ターン位置を折り続ける「ジリ貲レース」。
                #   FableA採択＝covered にせず active+race で出す（被覆連打が負け筋なのを可視化）。
                #   恒久の折り手（供給源の排除・供給圏外への退避）が要る＝2c/B-27 の領分。
                out.append(NodeStatus(nid, "active", prob,
                                      "止まらない供給を位置で対抗＝レース（恒久除去/退避が要る）",
                                      race="拮抗"))
            elif cov:
                out.append(NodeStatus(nid, "covered", prob, f"折り手：{cov}"))
            else:
                out.append(NodeStatus(nid, "active", prob, "実在するが未被覆"))
            continue
        # gate成立・役職可能・検出器あり だが現局面で未発火＝dormant（B-39・上の (a) で処理済み）
        out.append(NodeStatus(nid, "dormant", 0.0, "まだ立っていない（監視のみ）"))
    return out


def status_counts(statuses: list[NodeStatus]) -> dict[str, int]:
    """フッターの自己開示用＝各 status の件数（未接続 g の常時可視化・DP-1 §3）。"""
    out: dict[str, int] = {}
    for s in statuses:
        out[s.status] = out.get(s.status, 0) + 1
    return out


# --- DP-1 Stage 3：表示ヘルパー（Streamlit非依存の純関数＝両表示側で共用・テスト可能）---------
#
# ★FableA仕様（2026-07-18）：status→色。
#   script_impossible / pruned / gap → グレー（reason をツールチップ）／active → 実在度で濃淡／
#   covered → 緑。グレーは**消さず表示**（穴の可視化が DP-1 の本丸）。
#
# 各 status の視覚属性（絵文字・短ラベル・to_mermaid の classDef 名・グレー系か）。
_STATUS_VIS: dict[str, dict] = {
    "covered":          {"emoji": "🟩", "label": "折れている",   "mermaid": "covered", "gray": False},
    "active":           {"emoji": "🟥", "label": "実在・未被覆", "mermaid": "loss",    "gray": False},
    # ★B-39：dormant（未活性）＝まだ立っていない＝covered(折れている)とも ゼロ系とも別。
    #   アイコンは A-43 体系（🛡/0️⃣）と衝突しない中立色。mermaid は既存クラスに新設が要らない
    #   "gray"（視覚的に控えめ）へ寄せるが、**gray=False**＝ゼロ系ではない（まだ起こりうる）。
    #   ★view側（AIA所有）には dormant 専用の見せ方（色・凡例）の追随が要る＝申し送り済み。
    # ★A-45（view側追随・色はAIA委任）：mermaid も gray と別クラスにする＝図で「もう起きない」と
    #   同じ見え方になると B-39 の区別（まだ立っていない＝これから立ちうる）が消えるため。
    "dormant":          {"emoji": "⚪", "label": "未活性",       "mermaid": "dormant", "gray": False},
    # ★A-43③：旧「グレー」→**ゼロ 0️⃣**（＝可能世界がゼロ＝もう起きない、をそのまま言う）。
    "pruned":           {"emoji": "0️⃣", "label": "消えた",       "mermaid": "gray",    "gray": True},
    "script_impossible":{"emoji": "0️⃣", "label": "脚本上ありえない", "mermaid": "gray", "gray": True},
    # ★A-43②：`gap` から「未接続」の語を廃止（開発都合の内部語をプレイヤーに見せない）。
    #   status 値そのものは残す＝開発者モード・内部集計では引き続き区別できる。
    "gap":              {"emoji": "0️⃣", "label": "判定対象外",   "mermaid": "gray",    "gray": True},
}
# フッターの並び順（active/covered を先に＝いま効いている情報／ゼロ系は後ろ）。
# ★A-43②：`gap` の件数はユーザー向けフッターから外す（内部では status_counts で取れる）。
_STATUS_ORDER: tuple[str, ...] = ("active", "covered", "dormant", "pruned", "script_impossible")
_STATUS_JP: dict[str, str] = {
    "active": "active（実在）", "covered": "covered（折れている）",
    "dormant": "⚪未活性（まだ立っていない）",
    "pruned": "0️⃣ゼロ（消えた）", "script_impossible": "0️⃣ゼロ（脚本上ありえない）",
    "gap": "0️⃣ゼロ（判定対象外）",
}


def status_vis(status: str) -> dict:
    """status → 表示属性 {emoji,label,mermaid,gray}（未知 status も落ちない）。"""
    return _STATUS_VIS.get(status, {"emoji": "▫", "label": status, "mermaid": "loss", "gray": False})


def active_shade(prob: float) -> str:
    """active の実在度（prob 0..1）→ 濃淡マーク。0＝監視中（未発火）／高いほど濃い。"""
    if prob <= 0.0:
        return "◻"      # 監視中（脚本上は成立しうるが現局面では未発火＝prob 0 の active）
    if prob < 0.34:
        return "🔸"
    if prob < 0.67:
        return "🔶"
    return "🟥"


def race_badge(race: str | None) -> str:
    """★race 枠（DP-1 Stage 3）。2b（B-27・AIB）で evaluate_tree が実際に race を埋める。
    値『拮抗』＝covered だが止まらない供給を位置で対抗＝ジリ貧レース（恒久除去/退避が要る）。
    None なら空文字（未該当）。未知値でも落ちない（前置きバッジを出すだけ）。"""
    if not race:
        return ""
    _MARK = {"拮抗": "⛔レース（止まらぬ供給）"}
    return _MARK.get(race, f"⛔レース（{race}）") + " "


def node_status_line(ns: NodeStatus, label: str) -> str:
    """1ノードの1行表示（絵文字＋race枠＋ラベル）。ツールチップは別途 reason を使う。"""
    vis = status_vis(ns.status)
    lead = vis["emoji"] if ns.status != "active" else active_shade(ns.prob)
    return f"{race_badge(ns.race)}{lead} {label}"


def mermaid_status_map(statuses: list[NodeStatus]) -> dict[str, str]:
    """evaluate_tree の list[NodeStatus] → to_mermaid(statuses=...) が受ける {node_id: classデフ名}。
    ＝評価器（list）と描画（dict）の橋渡し（確定APIの型ギャップを1箇所で吸収）。"""
    return {ns.node_id: status_vis(ns.status)["mermaid"] for ns in statuses}


def self_disclosure_line(statuses: list[NodeStatus]) -> str:
    """★フッター自己開示（DP-1 §3 の本丸＝穴の可視化）：全ノード中の各 status 件数を常時表示。
    例『全19ノード中：active 5・covered 8・0️⃣ゼロ（消えた） 4・0️⃣ゼロ（脚本上ありえない） 2』。

    ★A-43②と DP-1 §3 の両立（設計上の緊張を明示しておく）：
      A-43②は「未接続」の語をユーザー向け表示から廃す指示だが、DP-1 §3 の本丸は
      「検出器の穴を隠さない」こと＝gap を**常時0件表示していた**のはその担保だった。
      両立案＝**gap は 0 件なら出さない・1件以上なら必ず出す**。
      ・通常プレイ（DETECTOR_GAPS 空＝gap 0）では語ごと現れない＝A-43②を満たす
      ・穴が生じた瞬間に自動で表面化する＝DP-1 §3 の担保を失わない
      （単純に _STATUS_ORDER から外すと、穴が出ても黙って隠れる＝DP-1の目的を壊す）。
    """
    counts = status_counts(statuses)
    total = len(statuses)
    parts = [f"{_STATUS_JP[k]} {counts.get(k, 0)}" for k in _STATUS_ORDER]
    if counts.get("gap", 0) > 0:
        parts.append(f"{_STATUS_JP['gap']} {counts['gap']}")
    return f"全{total}ノード中：" + "・".join(parts)


# ===========================================================================
# DP-2 2M-1：mm評価器＝勝ち筋の liveness（**視点反転の薄い層**・advisory）
# ===========================================================================
# LOSS_NODES を mm 視点で読む：負け筋＝**勝ち筋**／conds(AND)＝**達成TODO**／defenses＝**警戒すべき反撃**。
# ★DP-1 Stage 2a と同じ作法＝**検出を再実装しない**（gate=losstree.script_possible／反撃の担い手=
#   losstree.GOODWILL_DEFENSE_NOTES／供給レート=mm_lint／反撃の成立可能性=FI-5 プリミティブ）。
# ★advisory＝mm の意思決定には未配線（ベンチ bit-for-bit 不変）。接続は 2M-3（AIA）。

_BLOCKED_TH = 0.7               # 反撃がこれ以上「安定して成立する」なら blocked（FableA承認・2M-3で掃引）
#: ★B-41：反撃が「1回1除去」で対象が供給レース型（毎ターン積む勝ち筋）なら、供給≥除去で
#  完封できない＝counter を race 割引（存在はするが無力化しない）。主人公側 _is_racing の鏡像。
_COUNTER_RACE_DISCOUNT = 0.3
#: 供給レース型ノード＝毎ターンのボード暗躍供給で積む勝ち筋（1除去では追いつかない板）。
_SUPPLY_RACE_NODES: frozenset[str] = frozenset({"board.shrine", "board.school", "jaki.seal"})
#: ★B-42：cost の実効難度化＝評価器が「最安」と呼ぶノードの位置条件/事件発火の難度を織り込む
#  （AIA Stage 3a 乖離表＝cost過小の84%。fr.incident は cost=1 だが win_rate=0.00＝自然成立ゼロ）。
#  cond の refs（既存の構造化タグ）を実効難度手数へ変換：
#    REMOVAL＝位置条件（同席/present固定/エリア誘導＝乖離表(ii)pos）＝合流/退避に手が要る
#    UNREST ＝事件発火依存（犯人生存＋不安臨界＝§B）＝不安を臨界へ積む手が要る
#  ★新述語を作らず既存 refs を読むだけ（B-41 と同型＝二重実装しない）。値は実効難度の下限見積り。
_REF_EFFORT: dict[str, int] = {"REMOVAL": 1, "UNREST": 1}
_OPTIONAL_REFUSAL_DISCOUNT = 0.2  # 任意友好無視＝mmが確実に拒否できる＝大きく割引（0にはしない）
_INFO_COST_BOARD_LEAK = 1.0     # A-20：非ゴールボードへのmm能力暗躍＝クロマク位置リーク
_INFO_COST_UNREST_LEAK = 0.5    # 臨界到達させる不安＝「臨界者は犯人でない」の消去を与える（set_noise_leak既存）


@dataclass(frozen=True)
class WinLineStatus:
    node_id: str
    status: str            # not_in_script / blocked / live / winning
    cost: int | None       # 到達コスト＝残AND条件を満たすのに要する手数（到達不能/対象外は None）
    counter: float         # 反撃の重み 0..1（★存在でなく成立可能性で割引）
    info_cost: float       # この勝ち筋を進めると漏れる情報量（A-20型の既知リークのみ＝最小実装）
    reason: str


def _mm_node_pattern_match(pat: str, node_id: str) -> bool:
    """GOODWILL_DEFENSE_NOTES の nodes パターン（'kp.*' / '*.incident_*' / 完全一致）の照合。"""
    import fnmatch
    return pat == node_id or ("*" in pat and fnmatch.fnmatch(node_id, pat))


def _mm_refusal_feasibility(view_mm: dict, user: str) -> float:
    """★柱5の鏡像＝**真相ベースの確定判定**（設計ノート1・鏡像の非対称点）。

    主人公側（`_refusal_reach`）は belief 依存なので「割引に留め 0 にしない」健全側の縛りがあるが、
    **mm は自分が拒否できるかを真相の役職で確定して知っている**＝ここは確定判定でよい（縛り不要）：
      ・絶対友好無視（カルティスト/ウィッチ）＝必ず拒否＝その反撃は **成立しない（0.0）**
      ・任意の友好無視（キラー/クロマク/マイナス/ファクター）＝mmが確実に止められる＝大きく割引
      ・★イレギュラー・ナース・妹・コピーキャットの能力は**拒否不可**（KB:20）＝真相が友好無視でも割引しない
    """
    from engine.data import (UNREFUSABLE_ABILITY_CHARS, role_absolute_friendship_ignore,
                             role_has_friendship_ignore)
    if user in UNREFUSABLE_ABILITY_CHARS:
        return 1.0
    role = (view_mm.get("roles") or {}).get(user)
    if not role:
        return 1.0
    if role_absolute_friendship_ignore(role):
        return 0.0
    if role_has_friendship_ignore(role):
        return _OPTIONAL_REFUSAL_DISCOUNT
    return 1.0


def _mm_counter_weight(view_mm: dict, node_id: str, hp) -> float:
    """このノード（=mmの勝ち筋）に対する相手の反撃の重み 0..1。

    ★DP-1 covered 2条件の鏡像＝**「恒久反撃が存在する」では blocked にしない**。担い手の
    友好能力ごとに「対象存在(A1)×居場所(A2)×到達可能性(A3)×間に合うか(tempo)×拒否可否(柱5・真相)」
    を掛けた**成立可能性**で重み付け、最大値を返す（FI-5 プリミティブの鏡像流用＝新判定を作らない）。
    """
    from arena.losstree import GOODWILL_DEFENSE_NOTES
    from engine.data import ability_class_target_alive, ability_kind, goodwill_abilities_of
    from sim.abilities import is_implemented
    alive = [c["name"] for c in view_mm.get("characters", []) if c.get("alive")]
    best = 0.0
    for user, note in GOODWILL_DEFENSE_NOTES.items():
        if not any(_mm_node_pattern_match(p, node_id) for p in note.get("nodes", [])):
            continue
        cu = next((c for c in view_mm.get("characters", [])
                   if c.get("name") == user and c.get("alive")), None)
        if cu is None:
            continue                                   # 死亡/未登場＝反撃の担い手が居ない
        ref = _mm_refusal_feasibility(view_mm, user)
        if ref <= 0.0:
            continue                                   # 絶対友好無視＝確定拒否＝この反撃は成立しない
        for ab in goodwill_abilities_of(user) or []:
            name = ab["name"]
            if not is_implemented(user, name):
                continue
            feas = ref
            if not ability_class_target_alive(user, name, alive):
                continue                               # A(1) 対象クラス全滅＝成立しない
            feas *= hp._location_reach(user, name, view_mm)      # A(2) 居場所
            feas *= hp._threat_reach(user, name, view_mm)        # A(3) 到達可能性
            need = ab["hearts"] - cu.get("goodwill", 0)
            if need > 0:                                          # 未到達なら「間に合うか」も掛ける
                # ★B-41（fail-ignore の mm 版）：mm は主人公の手札を持たない＝**+2札を仮定しない**
                #   （has_plus2=False）。loops_left は mm view から明示（残ループ既知）。
                _ll = (view_mm.get("loops_total", 0) - view_mm.get("loop", 1) + 1
                       if view_mm.get("loops_total") else None)
                feas *= hp.invest_tempo(user, name, need, view_mm,
                                        ability_kind(user, name) or "",
                                        has_plus2=False, loops_left=_ll)
            # ★B-41：規模の会計＝「1回発動できる」≠「勝ち筋を無力化できる」。除去/冷却系の反撃が
            #   供給レース型ノード（毎ターン積む板）に当たる時、供給≥除去なら完封できない＝race 割引
            #   （DP-1 covered 2条件＝_is_racing の鏡像。位置で剥がせる供給は別＝板の除去レースに限定）。
            if node_id in _SUPPLY_RACE_NODES and "除去" in name:
                from arena.mm_lint import _mm_rate
                if _mm_rate(view_mm) >= 1:                        # 供給が毎ターン ≥ 除去(1/ターン)
                    feas *= _COUNTER_RACE_DISCOUNT
            best = max(best, feas)
    return best


#: ★設計ノート2の規律：条件充足は**既存の view フィールドの素直な読み**で決まるものだけを判定し、
#  それ以外は **未充足扱い（保守側＝到達コストを過小評価しない）**。新しい述語を暗黙に作らない。
#  判定できる条件クラス（label から読む・losstree の表記に依存）：
#    (1) キャラ暗躍≥2（"暗躍≥2"）  (2) ボード暗躍≥2（"暗躍が2"）  (3) 同エリア（"同エリア"）
_MM_COND_CHAR_ANYAKU2 = "暗躍≥2"
_MM_COND_BOARD_ANYAKU2 = "暗躍が2"
_MM_COND_SAME_AREA = "同エリア"


def _mm_char_of_role(view_mm: dict, role: str) -> dict | None:
    chars = {n for n, r in (view_mm.get("roles") or {}).items() if r == role}
    for c in view_mm.get("characters", []):
        if c.get("name") in chars and c.get("alive"):
            return c
    return None


def _mm_cond_satisfied(view_mm: dict, node: dict, cond: dict) -> bool | None:
    """条件が既に成立しているか。**判定不能は None**（呼び出し側で未充足扱い＝保守側）。"""
    label = cond.get("label", "")
    gate_roles = list((node.get("gate", {}) or {}).get("roles", []))
    if _MM_COND_CHAR_ANYAKU2 in label:                     # 例＝「KPに暗躍≥2が載る」
        for r in gate_roles:
            c = _mm_char_of_role(view_mm, r)
            if c is not None and (c.get("anyaku", 0) or 0) >= 2:
                return True
        return False
    if _MM_COND_BOARD_ANYAKU2 in label:                    # 例＝「学校の暗躍が2に届く」
        ba = view_mm.get("board_anyaku", {}) or {}
        return any(v >= 2 for v in ba.values())
    if _MM_COND_SAME_AREA in label and len(gate_roles) >= 2:  # 例＝「キラーとKPが同エリア」
        cs = [_mm_char_of_role(view_mm, r) for r in gate_roles[:2]]
        if all(cs) and all(c.get("area") for c in cs):
            return cs[0]["area"] == cs[1]["area"]
        return False
    return None                                            # ★判定不能＝新述語を作らない


import re as _re


def _required_anyaku(label: str) -> int:
    """cond の label から必要暗躍数 N を読む（B-44・B-42 と同じ label 読み・新述語なし）。

    losstree の表記＝「暗躍≥4」「暗躍が2」「暗躍2」等。数字が読めなければ 2（従来の既定＝暗躍2相当）。
    ＝killer4筋（暗躍≥4）が SUPPLY 枝で一律「暗躍2相当」に固定され過小評価される問題の是正
    （AIA Stage 3b＝評価器を信頼すると退行する側だった）。
    """
    m = _re.search(r"暗躍\D{0,3}(\d+)", label)   # 「暗躍≥4」「暗躍が2」「暗躍2」の数字を拾う
    return int(m.group(1)) if m else 2


def _mm_reach_cost(view_mm: dict, residual: list) -> int | None:
    """残条件を満たすのに要する手数（到達不能なら None）＝打点会計（A-18）の条件単位への一般化。

    供給レート/残日数は `arena/mm_lint` の既存述語をそのまま使う（二重実装しない）。
    ★B-42：cost に**実効難度**を織り込む＝cond の refs（REMOVAL=位置条件／UNREST=事件発火依存）から
    手数を加算する（供給を積むだけの cost 過小評価を是正・fr.incident が cost=1 で最頻選択される問題）。
    """
    from arena.mm_lint import _days_left, _mm_rate
    if not residual:
        return 0
    rate = max(1, _mm_rate(view_mm))
    days = _days_left(view_mm)
    cost = 0
    for cond in residual:
        label = cond.get("label", "")
        refs = set(cond.get("refs") or [])
        if (_MM_COND_CHAR_ANYAKU2 in label or _MM_COND_BOARD_ANYAKU2 in label
                or "SUPPLY" in refs):
            # ★B-44：必要暗躍数 N を label から読み ceil(N/rate) で課金（killer4=暗躍≥4 は N=4）。
            #   従来は一律「暗躍2相当」でしか課金せず killer4筋を過小評価＝評価器を信頼すると退行する側。
            n_req = _required_anyaku(label)
            base = -(-n_req // rate)        # ceil(N / rate)
        else:
            base = 1                       # 位置・その他＝最低1手（保守側の下限見積り）
        # ★実効難度：REMOVAL（位置＝合流/退避に手が要る）・UNREST（事件発火＝不安を臨界へ積む）を加算。
        #   SUPPLY と重なる条件は base で供給手数を数え済み＝二重計上しない。
        eff = sum(_REF_EFFORT[r] for r in refs if r in _REF_EFFORT)
        cost += base + eff
    return cost if cost <= days else None


def _mm_info_cost(view_mm: dict, node_id: str, residual: list) -> float:
    """この勝ち筋を進める手が漏らす情報量（**A-20型の既知リークのみ**＝最小実装・網羅は 2M-2）。"""
    cost = 0.0
    if node_id.startswith("board.") or node_id.startswith("jaki."):
        # A-20：ゴールでないボードへ mm能力暗躍を置くとクロマク位置が漏れる（landed）
        cost += _INFO_COST_BOARD_LEAK
    if any("不安" in (c.get("label") or "") for c in residual):
        # 既存 set_noise_leak と同源＝臨界へ届かせる不安は「臨界者は犯人でない」の消去を与える
        cost += _INFO_COST_UNREST_LEAK
    return cost


def evaluate_tree_mm(view_mm: dict, *, seats: int = 3) -> list[WinLineStatus]:
    """勝ち筋ネットワーク（=LOSS_NODES の視点反転）を mm 視点で評価する（**advisory**）。

    status＝not_in_script（脚本ゲート不成立）／winning（全AND条件が既に成立）／
            blocked（到達不能 or **安定して成立する**恒久反撃あり）／live（到達コスト付きで狙える）。
    ★mm は真相を知る＝gate は `losstree.script_possible`（単一脚本用）をそのまま使う。
    ★反撃は「存在」でなく**成立可能性**（`_mm_counter_weight`）＝DP-1 covered 2条件の鏡像。
    """
    from agents.heuristic_protagonist import HeuristicProtagonist
    from arena.losstree import script_possible

    roles = set((view_mm.get("roles") or {}).values())
    incidents = {i.get("name") for i in view_mm.get("incidents", []) if i.get("name")}
    rule_y = view_mm.get("rule_y") or ""
    rule_x = set(view_mm.get("rule_x") or []) | set(view_mm.get("rule_x2") or [])
    hp = HeuristicProtagonist(0)                  # 反撃の成立可能性を測る器（FI-5 プリミティブ）
    try:
        hp._sync(view_mm)
    except Exception:
        pass
    out: list[WinLineStatus] = []
    for nid, node in LOSS_NODES.items():
        if not script_possible(nid, roles=roles, incidents=incidents,
                               rule_y=rule_y, rule_x=rule_x):
            out.append(WinLineStatus(nid, "not_in_script", None, 0.0, 0.0, "この脚本には無い勝ち筋"))
            continue
        residual = [c for c in node.get("conds", [])
                    if _mm_cond_satisfied(view_mm, node, c) is not True]
        info = _mm_info_cost(view_mm, nid, residual)
        if not residual:
            out.append(WinLineStatus(nid, "winning", 0, 0.0, info, "全AND条件が既に成立"))
            continue
        cost = _mm_reach_cost(view_mm, residual)
        counter = _mm_counter_weight(view_mm, nid, hp)
        if cost is None:
            out.append(WinLineStatus(nid, "blocked", None, counter, info, "残ターンで到達不能"))
            continue
        if counter >= _BLOCKED_TH:
            out.append(WinLineStatus(nid, "blocked", cost, counter, info,
                                     f"安定して成立する恒久反撃あり（{counter:.2f}）"))
            continue
        out.append(WinLineStatus(nid, "live", cost, counter, info,
                                 f"残条件{len(residual)}・到達{cost}手・反撃{counter:.2f}"))
    return out
