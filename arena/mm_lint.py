# -*- coding: utf-8 -*-
"""mm_lint — 脚本家AI（HeuristicMastermind）の「規則上そのループで効果を持ち得ない手」を
機械検出する監査ツール（監査第2弾・docs/監査_mm_lint_計画_2026-07-15.md）。

原則（厳守）：
1. **検出のみ・修正禁止**：違反を列挙するだけ。修正は違反クラスごとに個別チケット化しFableA裁定を通す
   （A-5(e)の教訓＝「一見無駄だが戦略的に正当」があり得る）。
2. **規則ベースの証明可能性**：検出条件は「規則上そのループで効果を持ち得ない」ことがコードから
   証明できるものに限る（戦略選好の良し悪しは対象外）。
3. **恒久化**：使い捨てスクリプトにしない。将来の mm 変更の回帰監視（無駄手カウントが増えたら退行）に使う。

検出器（5種＝A-5(a)〜(d)＋A-18の一般形）：
  D1 届かない不安／D2 経過済み犯人への不安操作／D3 発動条件を欠く能力への友好禁止／
  D4 過剰暗躍／D5 解決機会ゼロのボード暗躍。

使い方：
  from arena.mm_lint import sweep_report
  print(sweep_report())            # 標準ベンチコーパス（3日130+5日70）を掃引しMarkdownで返す
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as _replace

from engine.board import AREAS
from engine.data import goodwill_abilities_of, unrest_threshold_of


@dataclass
class Violation:
    detector: str          # "D1".."D5"
    seed: int
    days: int
    loop: int
    day: int
    move: dict
    reason: str


# ---------------------------------------------------------------------------
# view からの規則ベース補助（mm の内部スコアには依存しない＝独立に証明する）
# ---------------------------------------------------------------------------
def _char(view: dict, name: str) -> dict | None:
    for c in view["characters"]:
        if c["name"] == name:
            return c
    return None


def _mm_rate(view: dict) -> int:
    # ミスリーダー同居で不安2/ターン積める（heuristic と同じ供給上限）。
    return 2 if "ミスリーダー" in (view.get("roles") or {}).values() else 1


def _days_left(view: dict) -> int:
    return max(1, view.get("days_per_loop", view["day"]) - view["day"] + 1)


def _reachable_culprits(view: dict) -> set[str]:
    """今日以降の事件で、犯人が臨界に届く（or 既に臨界以上/臨界0）＝発生見込みのある犯人集合。"""
    day = view["day"]
    rate = _mm_rate(view)
    out: set[str] = set()
    for inc in view["incidents"]:
        cn, cd = inc["culprit"], inc["day"]
        if cd < day:
            continue
        c = _char(view, cn)
        th = unrest_threshold_of(cn)
        if not c or not c["alive"] or th is None:
            continue
        turns = cd - day + 1
        if th == 0 or c["unrest"] >= th or c["unrest"] + rate * turns >= th:
            out.add(cn)
    return out


def _defeat_boards(view: dict) -> set[str]:
    """暗躍≥2 が敗北/主人公殺害につながる「ゴールボード」（規則から・A-18準拠）。
    学校（守るべき場所）／神社（封印）／ボードX（復讐者・爆弾X）／病院（reachableな病院の事件）。"""
    ry = view.get("rule_y")
    out: set[str] = set()
    if ry == "守るべき場所":
        out.add("学校")
    elif ry == "封印されしモノ":
        out.add("神社")
    elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") and view.get("rule_y_board_x"):
        out.add(view["rule_y_board_x"])
    reach = _reachable_culprits(view)
    for inc in view["incidents"]:
        if (inc["name"] == "病院の事件" and inc["day"] >= view["day"]
                and inc["culprit"] in reach):
            out.add("病院")
    return out


def _board_has_remover(view: dict, board: str) -> bool:
    """そのボードの暗躍を剥がせる主人公側の役が居るか（除去役＝積み増しが正当化されうる）。"""
    for c in view["characters"]:
        if not c["alive"]:
            continue
        for ab in goodwill_abilities_of(c["name"]) or []:
            nm = ab["name"]
            if "暗躍除去" not in nm:
                continue
            named = {b for b in AREAS if b in nm}
            if board in named or not named:   # エリア名入り＝限定／無し＝汎用
                return True
    return False


def _unrest_move(move: dict):
    """set_card 不安± / mm能力 unrest(±) を (target, delta) に正規化（他は None）。"""
    if move.get("target_kind") == "character" and move.get("card") in ("不安+1", "不安-1"):
        return move["target"], (1 if move["card"] == "不安+1" else -1)
    if move.get("kind") == "unrest":
        return move["target"], 1
    if move.get("kind") == "unrest_minus":
        return move["target"], -1
    return None


def _is_board_anyaku(move: dict) -> bool:
    return (move.get("target_kind") == "board"
            and (str(move.get("card", "")).startswith("暗躍") or move.get("kind") == "anyaku"))


# ---------------------------------------------------------------------------
# 検出器（各々 reason:str | None を返す。証明可能な「効果を持ち得ない」手のみ検出）
# ---------------------------------------------------------------------------
def detect_d1(view: dict, move: dict) -> str | None:
    """D1 届かない不安：不安+ を、供給上限×残解決回数でも臨界に届かないキャラへ（A-5a一般形）。

    ★A-25（2026-07-16）：**臨界0キャラ（黒猫）への不安+** も検出に追加。臨界0は不安0でも事件が
      発生＝最初から eligible（公開候補者リスト）に載っており、不安を足しても候補者リストは動かない
      ＝霧にならない。かつ臨界0はキャラカード表＝**公開情報**＝主人公にも「犯人でも不安不要」が
      自明＝ブラフ価値ゼロ（旧実装は th==0 を明示的に除外していた）。
      ウイルスガードは D2/heuristic と同型（不安がSK化の実弾になるルールでは無駄と断じない）。
    """
    um = _unrest_move(move)
    if not um or um[1] <= 0:
        return None
    tgt = um[0]
    c, th = _char(view, tgt), unrest_threshold_of(tgt)
    if not c or not c["alive"] or th is None:
        return None
    if th == 0:
        if "妄想拡大ウイルス" in {view.get("rule_x"), view.get("rule_x2")}:
            return None                      # 不安はSK化の実弾＝臨界0でも無駄でない
        return (f"{tgt}（不安臨界0＝公開情報）は不安0でも事件が発生し常に eligible＝"
                f"不安+を置いても候補者リストは動かない＝霧にならない")
    if c["unrest"] + _mm_rate(view) * _days_left(view) < th:
        return (f"{tgt}（不安{c['unrest']}/臨界{th}）は残{_days_left(view)}日×供給{_mm_rate(view)}"
                f"でも臨界に届かない＝不安+は霧にならない")
    return None


def detect_d2(view: dict, move: dict) -> str | None:
    """D2 経過済み犯人への不安操作：犯人としての事件日が全て過ぎたキャラへの不安±（A-5b一般形）。"""
    um = _unrest_move(move)
    if not um:
        return None
    # ★A-22ガード：不安が事件以外で効くルール（妄想拡大ウイルスのSK化等）では不安操作は実弾＝
    #   「経過済み犯人への不安」でも純無駄でない＝偽陽性なので除外（heuristic の A-22 停止条件と対）。
    if "妄想拡大ウイルス" in {view.get("rule_x"), view.get("rule_x2")}:
        return None
    tgt, day = um[0], view["day"]
    inc_days = [i["day"] for i in view["incidents"] if i["culprit"] == tgt]
    if inc_days and all(d < day for d in inc_days):
        return f"{tgt} の犯人事件日{sorted(inc_days)}は全て過去（現D{day}）＝不安操作は事件に無効"
    return None


def _class_target_alive(view: dict, user: str, ability: str) -> bool:
    """★A-23：対象クラス限定の能力（「学生の…」＝学生属性）に生存する対象が居るか。
    heuristic._ability_class_target_alive と対（あちらは chars dict・こちらは view）。"""
    from sim.reference import CHARACTER_ATTRIBUTES
    if "学生" not in ability:
        return True                       # クラス限定でない能力はこの関数では判定しない
    return any(c["name"] != user and c["alive"]                 # 「他の学生」＝自分は対象外
               and "学生" in CHARACTER_ATTRIBUTES.get(c["name"], "")
               for c in view["characters"])


def detect_d3(view: dict, move: dict) -> str | None:
    """D3 発動条件を欠く能力への友好禁止（A-5c一般形）：ループ制限未達／対象不在の能力へ。

    ★友好禁止は対象の友好蓄積＝その**全能力**を止める手＝「1つでも生きた能力がある」なら無駄では
      ない。よって**全能力が発動条件を欠く時だけ**無駄と断じる（旧実装は1つでも欠ければ検出＝
      教師『学生の不安操作』(空)＋『学生の役職開示』(生)のような複数能力キャラで偽陽性になる。
      A-23で条件を足すと顕在化するため同時に是正）。
    ★A-23（2026-07-16）：対象クラス（学生等）の生存者ゼロを条件に追加。旧D3が seed13 の
      教師/男子学生（対学生能力・学生は全滅 or 本人のみ）を拾えなかったのはこの条件が無いため。
    """
    if move.get("card") != "友好禁止" or move.get("target_kind") != "character":
        return None
    tgt = move["target"]
    loop = view.get("loop", 1)
    reach = _reachable_culprits(view)
    abilities = goodwill_abilities_of(tgt) or []
    if not abilities:
        return None            # 能力を持たないキャラへの友好禁止は D3 の射程外（filler＝D1系の領域）
    reasons: list[str] = []
    for ab in abilities:
        nm = ab["name"]
        if "第2" in nm and "以降" in nm and loop < 2:
            reasons.append(f"『{nm}』はL{loop}では発動不可")
        elif ("不安" in nm and ("除去" in nm or "操作" in nm)) and not reach:
            reasons.append(f"『{nm}』は臨界へ届く犯人が居ない＝発動対象なし")
        elif not _class_target_alive(view, tgt, nm):
            reasons.append(f"『{nm}』は対象クラス（学生）の生存者が居ない＝発動対象なし")
        else:
            return None        # ★生きた能力が1つでもある＝友好禁止は無駄でない
    return f"{tgt} の全能力が発動条件を欠く（{'／'.join(reasons)}）＝友好禁止は無意味"


def detect_d4(view: dict, move: dict) -> str | None:
    """D4 過剰暗躍：除去役の無いゴールボードへ、既に暗躍≥2（達成）からの積み増し（A-5d一般形）。"""
    if not _is_board_anyaku(move):
        return None
    board = move["target"]
    cur = view["board_anyaku"].get(board, 0)
    if cur < 2 or board not in _defeat_boards(view) or _board_has_remover(view, board):
        return None
    return f"{board} は既に暗躍{cur}（≥2＝ゴール達成）・除去役なし＝これ以上の暗躍は無駄"


def detect_d5(view: dict, move: dict) -> str | None:
    """D5 解決機会ゼロのボード暗躍：そのボードを参照する事件の解決機会が残っていない配置（A-18一般形）。
    病院＝病院の事件のボード。病院の事件が全て過去なら暗躍しても発生機会ゼロ（例：D2病院の事件後のD3病院暗躍）。"""
    if not _is_board_anyaku(move):
        return None
    board, day = move["target"], view["day"]
    if board != "病院":
        return None
    # ★A-59（2026-07-24）：病院が**ボードX（復讐者の灯火／巨大時限爆弾X）＝ゴール盤**の局では、
    #   病院暗躍は病院の事件でなく敗北条件（病院≥2）に直接寄与する正当な手＝D5の対象外。
    #   従来は病院を「病院の事件」専用と仮定し、ボードX=病院の正当なゴール盤暗躍を「解決機会なし」と
    #   誤検出していた（_defeat_boards は 87行で正しくボードXを含むのに detect_d5 が不整合だった）。
    #   実測＝D5全体29手中28手がこの偽陽性（真のD5違反は1手・filler）。
    if board in _defeat_boards(view):
        return None
    hosp_days = [i["day"] for i in view["incidents"] if i["name"] == "病院の事件"]
    if not hosp_days:
        return "病院の事件が脚本に無い＝病院暗躍は発生に寄与しない"
    if all(d < day for d in hosp_days):
        return f"病院の事件（日{sorted(hosp_days)}）は全て過去（現D{day}）＝病院暗躍に解決機会なし"
    return None


def detect_d6(view: dict, move: dict) -> str | None:
    """D6 不死で無効な事件の犯人への不安（A-38）：自殺×不死犯人（タイムトラベラー等）＝発火しても
    犯人が死なず打点0（60:A26「発生したが何も起きなかった」）＋death_prevented で役職が公開露呈。
    ＝mmが臨界に届かせるための不安投資は純損（棋譜：妹=TT の自殺に不安3投資→TT バレ）。"""
    from engine.data import ROLE_CLAUSE_ABILITY
    um = _unrest_move(move)
    if not um or um[1] <= 0:                         # 不安+ のみ（冷やしは対象外）
        return None
    tgt = um[0]
    for inc in view["incidents"]:
        if (inc["culprit"] == tgt and inc["day"] >= view["day"]
                and inc["name"] == "自殺"
                and ROLE_CLAUSE_ABILITY.get(view.get("roles", {}).get(tgt, "")) == "不死"):
            role = view["roles"].get(tgt, "")
            return (f"{tgt}（{role}=不死）は自殺の犯人＝発火しても不死で死なず打点0"
                    f"＋death_preventedで役職露呈＝不安は純損")
    return None


_DETECTORS = {"D1": detect_d1, "D2": detect_d2, "D3": detect_d3,
              "D4": detect_d4, "D5": detect_d5, "D6": detect_d6}


def lint_move(view: dict, move: dict) -> list[tuple[str, str]]:
    """1手に全検出器を掛け、(detector, reason) のリストを返す（該当なしなら空）。"""
    out = []
    for name, fn in _DETECTORS.items():
        r = fn(view, move)
        if r:
            out.append((name, r))
    return out


# ---------------------------------------------------------------------------
# 掃引：標準ベンチコーパスを回し、mm の各決定に検出器を掛ける
# ---------------------------------------------------------------------------
class _LintMastermind:
    """HeuristicMastermind を包み、各決定の (view, decision, chosen) を記録する（挙動は同一）。"""

    def __init__(self, seed: int):
        from agents import HeuristicMastermind
        self._mm = HeuristicMastermind(seed)
        self.moves: list[tuple[dict, str, dict]] = []

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = self._mm.decide(view, decision, options)
        if decision in ("set_card", "mastermind_ability"):
            self.moves.append((view, decision, chosen))
        return chosen


def lint_game(script, seed: int, days: int) -> list[Violation]:
    """1対局を回し、mm の各手に検出器を掛けて違反を集める。"""
    from agents.heuristic_protagonist import HeuristicProtagonist
    from sim import run_game
    mm = _LintMastermind(seed)
    hp = HeuristicProtagonist(seed)
    run_game(_replace(script, loops=8), {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    out: list[Violation] = []
    for view, _dec, chosen in mm.moves:
        for det, reason in lint_move(view, chosen):
            out.append(Violation(det, seed, days, view.get("loop", 0),
                                 view.get("day", 0), chosen, reason))
    return out


def sweep(days_list=(3, 5)) -> list[Violation]:
    """標準ベンチコーパス（3日130局＋5日70局）を掃引し、全違反を返す。要 PYTHONHASHSEED=0。"""
    from arena.benchmark import benchmark_scripts
    out: list[Violation] = []
    for days in days_list:
        for _name, seed, sc in benchmark_scripts(days=days):
            out.extend(lint_game(sc, seed, days))
    return out


def sweep_report(days_list=(3, 5)) -> str:
    """掃引して検出器別の件数＋代表実例3件を Markdown で返す（結果doc用の素体）。"""
    vios = sweep(days_list)
    n_games = {3: 130, 5: 70}
    total_games = sum(n_games.get(d, 0) for d in days_list)
    lines = [f"# mm_lint 掃引結果（{total_games}局・要 PYTHONHASHSEED=0）", ""]
    lines.append("| 検出器 | 違反手数 | 局数 |")
    lines.append("|---|---|---|")
    by_det: dict[str, list[Violation]] = {}
    for v in vios:
        by_det.setdefault(v.detector, []).append(v)
    for det in ("D1", "D2", "D3", "D4", "D5"):
        vs = by_det.get(det, [])
        n_g = len({(v.seed, v.days) for v in vs})
        lines.append(f"| {det} | {len(vs)} | {n_g} |")
    lines.append("")
    for det in ("D1", "D2", "D3", "D4", "D5"):
        vs = by_det.get(det, [])
        lines.append(f"## {det}（{len(vs)}手）")
        for v in vs[:3]:
            lines.append(f"- seed{v.seed}({v.days}日) L{v.loop}D{v.day} "
                         f"`{v.move.get('card') or v.move.get('kind')}→{v.move.get('target')}`：{v.reason}")
        if not vs:
            lines.append("- （違反なし）")
        lines.append("")
    return "\n".join(lines)
