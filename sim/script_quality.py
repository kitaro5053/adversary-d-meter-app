"""脚本の質（脚本家の勝ち筋の多様性）を静的解析する。

ユーザー指摘（2026-07-06）：ランダム脚本は脚本家の勝ち筋（＝主人公の敗北ルート）が
少なすぎることがある。例：殺人計画×友情サークル/不定因子χ＋事件（邪気の汚染・流布）は、
敗北ルートが全部「キラーに暗躍を載せる」に帰結し、キラーさえ抑えれば脚本家は勝てない
（事件が敗北条件と無関係）。

そこで「主人公が**別々の対策**を要する独立した勝ち筋の数」を数える。同じ対策（例：キラーの
暗躍を止める）で全部防げるなら、実質1本＝薄い脚本。2〜3本の独立ルートがある脚本を「まとも」とみなす。

★これは静的な近似（役職・事件・ルールの有無で判定）で、位置到達性までは見ない。
  脚本家は移動でキャラを寄せられるので「役がいる＝そのルートは概ね使える」を前提にする。
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.data import is_student, unrest_threshold_of

# 犯人の不安を下げられる＝事件をブロックしやすくする役割のキャラ（誰でも対象に取れる系）。
#   医者=不安1除去／ナース=臨界以上の不安を全除去（事件ブロックに特に強い）／
#   アイドル=同エリアの不安1除去／鑑識官=不安カウンターを他へ移し替え。
COOLERS_ANY: frozenset[str] = frozenset({"医者", "ナース", "アイドル", "鑑識官"})
# 学生の不安だけ下げられる（犯人が学生のときだけ効く）。
COOLERS_STUDENT: frozenset[str] = frozenset({"男子学生", "女子学生", "教師"})

# 殺害を起こす事件（犯人経由でキーパーソン/フレンドを殺しうる＝キラーとは別の勝ち筋）。
KILL_INCIDENTS: frozenset[str] = frozenset({"殺人事件", "遠隔殺人", "病院の事件"})
# 盤面（ボード）の暗躍を育てる事件（盤面敗北条件を後押し＝暗躍除去だけでは追いつかなくする）。
# ★B-36：不安拡大は除外＝その暗躍+1は board_anyaku でなく**キャラ**に付く（40:149・sim/effects.py
#   確認済）＝盤面敗北条件を直接育てない。ここに含めるのは board_anyaku を増やす事件のみ。
BOARD_FEED_INCIDENTS: frozenset[str] = frozenset({"邪気の汚染", "行方不明"})
# 盤面敗北条件を持つルールY。
BOARD_RULES: frozenset[str] = frozenset(
    {"守るべき場所", "封印されしモノ", "復讐者の灯火", "巨大時限爆弾Xの存在"})

# 勝ち筋グループ → 主人公が必要とする対策（人間向けの説明）。
_GROUP_LABEL: dict[str, str] = {
    "killer": "キラーの殺害（暗躍を止める／KPを引き離す）",
    "sk": "シリアルキラーの殺害（2人きりを作らせない・位置管理）",
    "incident_kill": "殺害系事件の抑止（犯人を冷やす／標的を退避）",
    "board": "盤面敗北条件の防衛（ゴールボードの暗躍を止める）",
    "kp_anyaku": "キーパーソンの暗躍を止める（僕と契約）",
    "mainlovers": "メインラバーズの主人公殺害（不安・暗躍を止める）",
    "tt": "タイムトラベラーの敗北宣言（友好を3以上に保つ）",
    "butterfly": "蝶の羽ばたきの発生阻止（未来改変プラン）",
}


def win_path_groups(script) -> set[str]:
    """脚本家の独立した勝ち筋（主人公が別々の対策を要する敗北ルート）の集合。

    同じ対策で防げるルートは1グループに束ねる（例：キラーのKP殺害/主人公殺害/ファクターKP化殺害は
    すべて「キラーの暗躍管理」で対処＝1グループ "killer"）。
    """
    roles = {n: script.role_of(n) for n in script.cast}
    role_set = set(roles.values())
    rule_xs = set(script.rule_xs)
    incidents = {i.name for i in script.incidents}
    ry = script.rule_y

    has_kp = "キーパーソン" in role_set
    has_friend = "フレンド" in role_set
    killable = has_kp or has_friend  # 殺せば敗北につながる役がいるか

    groups: set[str] = set()

    # 1. キラー系（KP殺害・主人公殺害・ファクターのKP化殺害）＝キラーの暗躍管理で一括対処
    if "キラー" in role_set and (has_kp or "ファクター" in role_set):
        groups.add("killer")

    # 2. シリアルキラー（2人きり殺害）＝位置管理。妄想拡大ウイルスのSK化も含む
    if "シリアルキラー" in role_set:
        groups.add("sk")
    elif "妄想拡大ウイルス" in rule_xs and any(r == "パーソン" for r in roles.values()):
        groups.add("sk")

    # 3. 殺害系事件（KP/フレンドを殺しうる）＝事件抑止/退避（キラーとは別対策）。
    #   ★ただし「困難」＝脚本家の打点が臨界に届かず実際には発生しない事件は勝ち筋に数えない
    #     （ユーザー指摘 2026-07-06：事件の発生しやすさを評価すべき／2026-07-11：1日目臨界≥3・
    #     2日目臨界≥4は死に事件として困難固定）。事件系の勝ち筋は全てこの発生可能性で門番する。
    feasible_incs = {f.name for f in incident_feasibility(script) if f.grade != "困難"}
    if killable and (incidents & KILL_INCIDENTS & feasible_incs):
        groups.add("incident_kill")

    # 4. 盤面敗北条件＝ボード防衛
    if ry in BOARD_RULES:
        groups.add("board")

    # 5. 僕と契約＝KP暗躍禁止（盤面ではなくキャラ暗躍）
    if ry == "僕と契約しようよ！" and has_kp:
        groups.add("kp_anyaku")

    # 6. メインラバーズ主人公殺害
    if "メインラバーズ" in role_set:
        groups.add("mainlovers")

    # 7. タイムトラベラー敗北宣言
    if "タイムトラベラー" in role_set:
        groups.add("tt")

    # 8. 蝶の羽ばたき×未来改変プラン（★発生しない蝶＝勝ち筋に数えない：ユーザー方針 2026-07-11）
    if ry == "未来改変プラン" and "蝶の羽ばたき" in (incidents & feasible_incs):
        groups.add("butterfly")

    return groups


# ---------------------------------------------------------------------------
# 事件の発生しやすさ（不安管理の静的近似）  ★ユーザー要望 2026-07-06
# ---------------------------------------------------------------------------
# 脚本家は犯人に不安を「1ターンにつき1つ」しか載せられない（＝日数分）。ただし
# ミスリーダーが犯人と同居していれば脚本家能力フェイズで+1され「日数×2」貯められる。
# 主人公は犯人へ不安-1を置けるが、重ね置き不可なので1ターン1枚（=-1/ターン）。不安+1は
# 不安-1より先に解決＆0未満にならないため、脚本家1/ターンに主人公-1/ターンをぶつけると
# 実質0で伸びない（初日・臨界2の犯人が不安-1だけで止まる、というユーザー指摘の構造）。
# 犯人の不安を下げる能力（医者・ナース等）が居るとブロックはさらに容易になる。
#
# ★これは静的近似：位置到達性・手札の取り合い・事件の連鎖（不安拡大等）までは見ない。
#   「脚本家の打点 vs 臨界 vs 主人公の冷却」の会計で、事件を 確実／拮抗／困難 に分類する。


@dataclass
class IncidentFeasibility:
    day: int
    name: str
    culprit: str
    threshold: int          # 犯人の不安臨界
    mm_uncontested: int     # 主人公が妨害しない場合に脚本家が積める最大不安（≒臨界に届くか）
    mm_contested: int       # 主人公が犯人を全力で冷やした場合に残る不安
    grade: str              # "確実" / "拮抗" / "困難"
    note: str


def incident_feasibility(script) -> list[IncidentFeasibility]:
    """予定事件それぞれの発生しやすさを不安会計で静的評価する。"""
    roles = {n: script.role_of(n) for n in script.cast}
    has_misleader = "ミスリーダー" in set(roles.values())
    coolers_any = [n for n in script.cast if n in COOLERS_ANY]
    coolers_student = [n for n in script.cast if n in COOLERS_STUDENT]

    out: list[IncidentFeasibility] = []
    for inc in sorted(script.incidents, key=lambda x: x.day):
        c = inc.culprit
        if c == "手先":
            out.append(IncidentFeasibility(inc.day, inc.name, c, -1, 0, 0, "困難",
                       "犯人が手先の事件は発生しない（KB: 30 特性）＝死に事件。"))
            continue
        th = unrest_threshold_of(c)
        if th is None:
            out.append(IncidentFeasibility(inc.day, inc.name, c, -1, 0, 0, "要確認",
                       "犯人の不安臨界が不明（KB外）＝発生可否を判定できない。"))
            continue
        d = inc.day
        # 脚本家の1ターンあたりの犯人への不安打点：基本1、ミスリーダー同居で+1。
        mm_rate = 1 + (1 if has_misleader else 0)
        mm_uncontested = mm_rate * d
        # ★全力冷却側の打点（2026-07-08 精緻化）：
        #   (1) ML分離：犯人がミスリーダー本人なら自己ポンプ＝分離不能で+2/日が確定。
        #       別人なら主人公が移動でMLを引き離せる＝拮抗側の会計では+1/日に落とす。
        #   (2) 冷却役の実在：能力冷却は「役職が友好無視/絶対友好無視なら脚本家が拒否」
        #       ＋「非拒否の冷却役が1人だけなら友好禁止でハーツを飢餓できる」＝
        #       非拒否ホルダー2人以上の時だけ+1/日に数える（暗躍除去と同じ実在条件。
        #       btx_future実測：冷却役=医者がカルティスト（拒否可能）で蝶が止まらなかった）。
        culprit_is_ml = roles.get(c) == "ミスリーダー"
        mm_rate_contested = mm_rate if culprit_is_ml else 1
        from engine.data import (UNREFUSABLE_ABILITY_CHARS,
                                 role_absolute_friendship_ignore,
                                 role_has_friendship_ignore)

        def _real_cooler(n: str) -> bool:
            r = roles.get(n, "パーソン")
            if n in UNREFUSABLE_ABILITY_CHARS:
                return True   # イレギュラー/ナースは拒否不可（飢餓は下の人数条件で見る）
            return not (role_has_friendship_ignore(r)
                        or role_absolute_friendship_ignore(r))

        cool_any_c = [n for n in coolers_any if n != c and _real_cooler(n)]
        cool_stu_c = [n for n in coolers_student if n != c and _real_cooler(n)]
        pool = cool_any_c + (cool_stu_c if is_student(c) else [])
        can_cool = len(pool) >= 2   # 1人はハーツ飢餓で止められる＝2人以上のみ実在
        cool_rate = 1 + (1 if can_cool else 0)
        mm_contested = max(0, (mm_rate_contested - cool_rate) * d)

        if th <= mm_contested:
            grade = "確実"
            note = "主人公が犯人を全力で冷やしても臨界に届く（止めにくい）。"
        elif th <= mm_uncontested:
            grade = "拮抗"
            note = "放置すれば発生するが、主人公が犯人へ不安-1を置けば止まる（要・毎ターン対処）。"
        else:
            grade = "困難"
            note = ("脚本家の打点（" + ("日数×2" if has_misleader else "日数分")
                    + f"＝最大{mm_uncontested}）が臨界{th}に届かない＝ほぼ死に事件。")
        # ★ユーザー方針（2026-07-11）：早い日に高臨界の犯人を置いた事件は、脚本家が不安を
        #   届かせられず実際には発生しない＝死に事件として「困難」に固定し、勝ち筋に数えない。
        #   （1日目に臨界≥3／2日目に臨界≥4。ミスリーダー同居で打点が届く形式計算でも除外する。）
        if (d == 1 and th >= 3) or (d == 2 and th >= 4):
            grade = "困難"
            note = (f"{d}日目に不安臨界{th}の犯人＝この早さでは脚本家が不安を臨界まで届かせられず"
                    "実際には発生しない（1日目臨界3以上／2日目臨界4以上は死に事件・ユーザー方針 2026-07-11）。")
        if th == 0:
            grade, note = "確実", "不安臨界0（常に発生。黒猫なら効果は『何も起きない』）。"
        if can_cool and grade != "困難":
            note += "（冷却役: " + "・".join(pool) + "）"
        out.append(IncidentFeasibility(d, inc.name, c, th, mm_uncontested,
                                       mm_contested, grade, note))
    return out


@dataclass
class BalanceGrade:
    n_paths: int
    groups: set[str]
    verdict: str


def balance_grade(script) -> BalanceGrade:
    """勝ち筋の本数から脚本の「まともさ」を判定する（ユーザー基準：独立ルートが複数あるか）。"""
    groups = win_path_groups(script)
    n = len(groups)
    if n <= 1:
        v = ("⚠ 勝ち筋が実質1本＝主人公が1つの対策（" +
             (_GROUP_LABEL.get(next(iter(groups)), "その手") if groups else "—") +
             "）で全部防げる。事件が敗北条件と無関係／殺害手段が単一の薄い脚本。")
    elif n == 2:
        v = "△ 勝ち筋2本＝最低限。もう1本あると主人公の判断が難しくなる。"
    else:
        v = f"✓ 勝ち筋{n}本＝主人公は複数の脅威を同時に捌く必要がある良い脚本。"
    return BalanceGrade(n_paths=n, groups=groups, verdict=v)


def describe_win_paths(script) -> list[str]:
    """勝ち筋グループを人間向けの説明文リストにする（評価器・ビルダー表示用）。"""
    return [_GROUP_LABEL.get(g, g) for g in sorted(win_path_groups(script))]


_GRADE_ICON = {"確実": "🟢", "拮抗": "🟡", "困難": "🔴", "要確認": "⚪"}


def incident_feasibility_lines(script) -> list[str]:
    """事件ごとの発生しやすさを人間向け1行に（評価器・ビルダー表示用）。"""
    lines = []
    for f in incident_feasibility(script):
        icon = _GRADE_ICON.get(f.grade, "")
        lines.append(f"{icon} {f.day}日目 {f.name}（犯人:{f.culprit}・臨界{f.threshold}）"
                     f"＝{f.grade}：{f.note}")
    return lines


def incident_feasibility_verdict(script) -> str | None:
    """事件の発生可能性から脚本の弱点を1行にまとめる（無ければNone）。"""
    fs = incident_feasibility(script)
    if not fs:
        return None
    hard = [f for f in fs if f.grade == "困難"]
    solid = [f for f in fs if f.grade == "確実"]
    if hard and len(hard) == len(fs):
        return ("⚠ 予定事件がすべて『困難』＝脚本家の不安の打点が臨界に届かず、"
                "事件が実質発生しない（不安拡大やミスリーダー同居で打点を作るか、"
                "臨界の低い犯人／早い日に置き換えるべき）。")
    if hard:
        names = "・".join(f"{f.day}日目{f.name}" for f in hard)
        return f"△ 発生困難な事件あり（{names}）＝打点不足で死に事件。犯人・日・ミスリーダー配置を再考。"
    if not solid:
        return ("・予定事件はすべて『拮抗』＝主人公が犯人へ不安-1を置き続ければ止まる。"
                "確実に通したい事件は臨界0の犯人・ミスリーダー同居・不安拡大で打点を補強。")
    return None


# ---------------------------------------------------------------------------
# B-46：単線脚本検出器（AIB 2026-07-23・設計提案doc land 後）
#   loop_race の verdict は全局 protagonist で判別力ゼロ（A-50提案C）。その protagonist の
#   内側で「単線＝暗躍禁止1枚/ターンで塞げる単一ボード線に集約され二者択一を強制されない」脚本を
#   検出する（§1i 二正面ドクトリンの裏返し）。★advisory＝意思決定に未配線・ベンチ不変。
#   ★検出は sim/loop_race.analyze_loop を呼ぶだけ＝二重実装しない（DP-1/DP-2 と同じ作法）。
# ---------------------------------------------------------------------------

#: soft脅威＝不安-1で毎ターン冷やせ暗躍禁止枠を食わない（backbone から除外する）。
#   btx_bomb の mainlovers が実測で pushover ＝soft妥当。sk/killer/tt は seat/位置を食う＝含めない。
_SINGLE_LINE_SOFT: frozenset[str] = frozenset({"incident_kill", "mainlovers"})
#: backbone がこの集合の単一要素なら「暗躍禁止で塞げる単一線」。
_SINGLE_LINE_BACKBONE_OK: frozenset[str] = frozenset({"board", "kp_anyaku"})
#: 暗躍禁止で止まらない供給（rule_x/rule_y に在れば単一線でも「塞げない」）。
_SINGLE_LINE_HARD_SUPPLY: tuple[str, ...] = ("不穏な噂", "黒猫")
#: soft脅威の許容上限。★経験則（母数200で防衛失敗の誤検出ゼロを確認した閾値）。
#   ★再検証トリガー＝キャラプール変更・コーパス再生成・loop_race のパス述語変更のいずれかで
#   この閾値と _SINGLE_LINE_* を再走査すること（random_BTX#7 型の fb_loss 誤検出が復活しうる）。
_SINGLE_LINE_MAX_ACTIVE: int = 2


@dataclass
class SingleLineReport:
    """単線判定。is_single_line=True＝弱い（対処容易）／no_win_line=True＝mmに勝ち筋なし。"""
    is_single_line: bool
    no_win_line: bool
    backbone: frozenset[str]
    reason: str


def single_line_report(script) -> SingleLineReport:
    """脚本が『単線（対処容易で弱い）』かを初期局面のレースから判定する（advisory）。

    ★近似＝初期局面のみ（loop_race.analyze_script と同じ・カウンター蓄積後の中盤は見ない）。
    """
    from sim.loop_race import HARD, analyze_loop
    from sim.state import GameState
    from engine.data import initial_area_of

    st = GameState(script=script)
    dyn = {n: "都市" for n in script.cast if initial_area_of(n) is None}
    st.prepare_loop(dyn)
    rep = analyze_loop(st)
    active = [p for p in rep.paths if p.grade != HARD]
    if not active:
        return SingleLineReport(False, True, frozenset(),
                                "mmに残り日数で臨界に届く勝ち筋が無い（別カテゴリ）。")
    backbone = frozenset(p.key for p in active) - _SINGLE_LINE_SOFT
    if len(backbone) != 1 or not backbone <= _SINGLE_LINE_BACKBONE_OK:
        return SingleLineReport(False, False, backbone,
                                f"backbone={set(backbone) or '∅'}＝単一の塞げるボード線でない。")
    text = (getattr(script, "rule_x", "") or "") + (script.rule_y or "")
    if any(h in text for h in _SINGLE_LINE_HARD_SUPPLY):
        return SingleLineReport(False, False, backbone,
                                "暗躍禁止で止まらない供給（不穏な噂/黒猫）がある＝単一線でも塞げない。")
    if len(active) > _SINGLE_LINE_MAX_ACTIVE:
        return SingleLineReport(False, False, backbone,
                                f"soft脅威が積む（active={len(active)}）＝二者択一を強制しうる。")
    return SingleLineReport(True, False, backbone,
                            f"単線＝{set(backbone)} を暗躍禁止で塞げば脚本家は勝てない。")


# ---------------------------------------------------------------------------
# 生成品質の追加チェック（テスター知見 2026-07-09・AIA実装）
# ①難度下限（AI主人公オラクル） ②冷却圧（安価な不安除去） ③キャラ選択の整合
# ---------------------------------------------------------------------------

@dataclass
class DifficultyProbe:
    mean_ltw: float          # AI主人公の勝利までの平均ループ数
    per_seed: list[int]      # 各seedのループ数
    verdict: str


def probe_difficulty(script, seeds=(0, 1, 2), loops: int = 4) -> DifficultyProbe:
    """AI主人公オラクル（arena.benchmark.loops_to_win・AIB提供の安定API）で難度下限を実測する。

    背景（テスター知見）：ソルバは「防衛可能性」＝難しすぎない事は保証するが、
    「簡単すぎない」保証が無く、中級者が半分以上1ループ突破する脚本が生成されうる。
    ★AI主人公は目安（人間の中級者と完全一致はしない）。全seedでL1突破＝明確な下限割れ。
    """
    from arena.benchmark import loops_to_win  # 関数内import（層の逆流を局所化）
    vals = []
    for s in seeds:
        ltw, _outcome = loops_to_win(script, s, loops=loops)
        vals.append(ltw)
    mean = sum(vals) / len(vals)
    if all(v <= 1 for v in vals):
        v = ("⚠ AI主人公が全seedで1ループ突破＝難度の下限割れ。初日から破綻している"
             "可能性が高い（勝ち筋の起動が遅い/守りが安すぎる）。事件日・配役・ルールを再考。")
    elif mean < 1.5:
        v = f"△ AI主人公がほぼ1〜2ループで突破（平均{mean:.1f}）＝易しめ。中級者なら1ループ圏。"
    else:
        v = f"✓ AI主人公の突破まで平均{mean:.1f}ループ＝下限は確保。"
    return DifficultyProbe(mean_ltw=round(mean, 2), per_seed=vals, verdict=v)


def cooling_pressure(script) -> list[str]:
    """安価な冷却解禁者（不安除去/操作・低ハート）の存在を警告する（テスター知見：医者♥2が安牌）。

    事件は「犯人の不安≥臨界」で発生＝冷却能力は事件計画への直接対抗。♥2は友好+2一発で解禁。
    """
    from engine.data import goodwill_abilities_of
    if not script.incidents:
        return []
    coolers: list[tuple[str, int]] = []
    for n in script.cast:
        for ab in goodwill_abilities_of(n) or []:
            if "不安" in ab["name"] and ("除去" in ab["name"] or "操作" in ab["name"]):
                coolers.append((n, ab["hearts"]))
    if not coolers:
        return []
    coolers.sort(key=lambda x: x[1])
    cheap = [f"{n}（♥{h}）" for n, h in coolers if h <= 2]
    out = []
    if cheap:
        out.append("⚠ 安価な冷却解禁者: " + "・".join(cheap) +
                   " ＝友好+2一発で解禁され、以後毎ターン犯人を冷やせる（事件が止まりやすい）。")
        out.append("　対策の観点: 犯人に臨界0（黒猫）を混ぜる（冷却無効）／ミスリーダー同居で"
                   "打点2/T（冷却1/Tを上回る）／脚本家は冷却者を友好禁止・殺人事件の的として想定。")
    others = [f"{n}（♥{h}）" for n, h in coolers if h > 2]
    if others:
        out.append("・その他の冷却持ち: " + "・".join(others))
    return out


def cast_coherence(script) -> list[str]:
    """キャラ選択の意図チェック（テスター知見：学生なしの教師・意味のない大物＝『AIっぽい』）。

    能力・特性の対象/前提がキャスト内に存在しないキャラを検出する。ルール違反ではなく
    「そのキャラを入れた意図が無い」警告（人間の脚本でも起こる＝studioで気づかせる）。
    """
    from engine.data import is_adult
    cast = set(script.cast)
    students = {n for n in cast if is_student(n)}
    warns: list[str] = []
    if "教師" in cast and not (students - {"教師"}):
        warns.append("教師：能力対象の学生がキャストに居ない（両能力とも空回り）")
    for s in ("男子学生", "女子学生"):
        if s in cast and not (students - {s}):
            warns.append(f"{s}：能力対象の「他の学生」が居ない（不安除去が空回り）")
    if "妹" in cast and not any(is_adult(n) for n in cast):
        warns.append("妹：能力対象の大人が居ない（肩代わりが空回り）")
    if "従者" in cast and not ({"お嬢様", "大物"} & cast):
        warns.append("従者：特性対象（お嬢様/大物）が居ない（追随・肩代わり死亡が空回り）")
    if "アルバイト？" in cast and "アルバイト" not in cast:
        warns.append("アルバイト？：特性の連動先（アルバイト）が居ない")
    if "大物" in cast and not getattr(script, "oomono_territory", None):
        warns.append("大物：テリトリー未指定（特性・能力の前提）")
    if "医者" in cast and "入院患者" not in cast:
        warns.append("医者：能力2（入院患者の禁止エリア解除）は対象不在＝能力1だけの採用か確認")
    return warns
