"""1ループの詰み判定（レース/被覆解析）— 全ゲーム木を展開しない実用ソルバ。

Phase 2 で「厳密な全列挙は中盤局面で非現実的（分岐 10^6〜10^7）」と実測で確定した。
そこで方向転換（ユーザー承認 2026-07-06）：勝ち筋ごとに「脚本家の到達 vs 主人公の被覆」を
閉じた式で評価する。全局面で高速・構成的に健全寄り。

判定の核（★健全性の要）：
- **塞げない供給**（＝主人公がどう動いても止められない打点）だけで敗北条件に届くなら、
  主人公に手段が無い＝脚本家の確定勝ち。真に塞げないのは＝
    * 不穏な噂（脚本家能力フェイズの暗躍+1・ボード直指定・1/loop＝位置で妨害できない）
    * 事件効果の暗躍（邪気の汚染＝神社+2 等・発生が「確実」なもの。※incident_feasibility で会計）
    * 既に置かれていて除去役の居ない暗躍カウンター
  ★クロマク能力・カルティストのボード無視は「塞げない」ではない：主人公がクロマク/
    カルティストをボードから引き離せば止まる（位置で対処＝塞げる側に会計）。暗躍除去役が
    居れば既存カウンターも剥がせる＝確実を緩和。これらを塞げない扱いにすると偽陽性が出る。
- **被覆（covering）**：主人公が1ターンに止められる枠は限られる（暗躍禁止は実質1枚/ターン＝
  複数主人公が出すと自滅。不安-1は重ね置き不可で1犯人1枚）。同時に暗躍禁止を要する脅威が
  "独立した源で"2つ以上なら（二正面）、どれかは通る＝脚本家の確定勝ち。
  ★同じ源の脅威（例：キラーのKP殺害 killer_kp とキラーの主人公殺害 killer4）は PathRace.source で
  重複排除し、二正面を過剰計上しない。

★移動モデル（ユーザー知見 2026-07-06）：盤面2×2、移動はトグル合成。**主人公は隣接移動のみ
  （←→/↑↓）で斜めを持たず、脚本家は斜め＋移動禁止で妨害できる**。よって：
  - 主人公は「ピン留め（移動禁止で足止め）」は確実だが、**「別ボードへ動かす（分離）」は脚本家に
    妨害されると当てにできない**。→ キラーのKP殺害は「KPを引き離す」で防げる前提にせず、確実な
    防御＝KP暗躍を暗躍禁止で止める（needs_kinshi=True）とみなす。SKの2人きり回避も分離依存＝
    保守的に拮抗のまま。
  - 対角移動（学校↔病院・神社↔都市）は脚本家しか作れない＝主人公の到達可能域は隣接に限られる。

各勝ち筋を3段階に：
  "確実"(forced)   … 塞げない供給が臨界に届く／既に敗北条件成立＝脚本家の確定勝ち
  "拮抗"(contested)… 塞げる供給で届く＝主人公が毎ターン専用の枠で対処すれば止まる
  "困難"(hard)     … 全供給でも残り日数で臨界に届かない＝この勝ち筋は不発

総合判定：
  ∃確実 → "mastermind" ／ 拮抗が被覆枠を超える → "mastermind"（二正面）
  拮抗が枠内で全て塞げる or 全て困難 → "protagonist" ／ どちらとも言えない → "contested"

★健全性の検証と近似の限界（正直に）：
- board（ゴール盤面）系は「自分では動かせないカウンター」＝塞げない供給の会計が厳密に効き、
  小局面で loop_solver の brute と全一致（回帰テスト済み）。kp_anyaku（僕と契約）・killer4
  （主人公殺害）も同型（分離の逃げ道が無い＝暗躍を止める/除去するしかない）で健全側。
- **killer_kp（キラーのKP殺害）は保守的な近似**：同エリア要求があり主人公はKPを引き離せば
  防げる（分離の逃げ道）。ここを厳密に解くには位置の追いかけっこ＋移動禁止＋読み合いが要り、
  かつ brute 検証は4キャラ以上で重すぎて回らない。よって「拮抗」に倒す＝**脚本家の詰みを
  過小申告しうる**（偽陽性は出さない＝安全側）。厳密が要る局面は loop_solver（小局面限定）。
- 位置の多段移動・伏せカードの読み合いの細部は厳密には解かない。中盤も含め速く「詰み/防衛/
  読み合い」の目安を出す実用ツールという位置づけ。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.data import goodwill_abilities_of

from .script_quality import incident_feasibility

FORCED = "確実"
CONTESTED = "拮抗"
HARD = "困難"

MASTERMIND = "mastermind"
PROTAGONIST = "protagonist"
CONTESTED_V = "contested"


@dataclass
class PathRace:
    key: str
    grade: str            # 確実 / 拮抗 / 困難
    needs_kinshi: bool    # 主人公が「暗躍禁止1枚/ターン」で塞ぐ必要がある脅威か（被覆の競合資源）
    note: str
    source: str = ""      # 脅威の源（被覆の重複排除用。空なら key を源とみなす）

    def src(self) -> str:
        return self.source or self.key


@dataclass
class LoopRaceReport:
    verdict: str
    days_left: int
    paths: list[PathRace] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
def _alive(state) -> dict:
    return {n: c for n, c in state.characters.items() if c.alive and c.on_board}


def _role_named(state, role: str):
    return [n for n, c in state.characters.items() if c.role == role]


def _has_board_removal(state, board: str) -> bool:
    """board の暗躍を剥がせる主人公側の能力持ちが生存しているか（＝カウンターを減らせる）。

    ★実在条件（2026-07-08）：
    (1) 拒否可能性：能力持ちの役職が友好無視/絶対友好無視なら脚本家が拒否できる
        ＝除去は実在しない（イレギュラー・ナースだけ拒否不可。KB: 20）。
    (2) ハーツ飢餓：脚本家は友好禁止を毎ターン1枚置ける＝解禁前のホルダーが1人だけ
        なら、そのキャラへの友好+を永久に止められる（1ターンの+は最大2）。
        よって「既に必要ハーツ解禁済み」か「非拒否ホルダーが2人以上（両方は
        止められない）」の時だけ実在（btx_seal実測：巫女♡3を友好禁止で飢餓させ、
        黒猫+噂の止まらない2点で毎ループ封印成立＝除去は幻だった）。"""
    from engine.data import (UNREFUSABLE_ABILITY_CHARS,
                             role_absolute_friendship_ignore,
                             role_has_friendship_ignore)
    locked_holders = 0
    for n, c in state.characters.items():
        if not c.alive:
            continue
        if n not in UNREFUSABLE_ABILITY_CHARS and (
                role_has_friendship_ignore(c.role)
                or role_absolute_friendship_ignore(c.role)):
            continue                     # 脚本家が拒否する＝除去は成立しない
        for ab in goodwill_abilities_of(n) or []:
            if "暗躍除去" not in ab["name"]:
                continue
            if n == "巫女" and board != "神社":
                continue                 # 巫女の除去は神社限定
            if c.goodwill >= ab["hearts"]:
                return True              # 解禁済み＝もう止められない
            locked_holders += 1
            break
    return locked_holders >= 2           # 2人以上なら友好禁止で両方は飢餓させられない


def _has_char_anyaku_removal(state) -> bool:
    """キャラの暗躍カウンターを剥がせる能力持ちが生存しているか（神格/転校生/鑑識官）。"""
    for n in ("神格", "転校生", "鑑識官"):
        c = state.characters.get(n)
        if c and c.alive and c.on_board:
            return True
    return False


def _goal_boards(state) -> set:
    ry = state.script.rule_y
    goal = set()
    if ry == "守るべき場所":
        goal.add("学校")
    elif ry == "封印されしモノ":
        goal.add("神社")
    elif ry in ("復讐者の灯火", "巨大時限爆弾Xの存在") and state.rule_y_board_x:
        goal.add(state.rule_y_board_x)
    # ★病院の事件は「ループ終了時の盤面敗北条件」ではなく事件効果（病院暗躍≥1で殺害）＝
    #   incident_kill パスで会計する（ここでゴールボード扱いしない＝誤判定を防ぐ）。
    return goal


def _kuromaku_count(state) -> int:
    """生存・盤上のクロマク数。2×2盤面では任意ボード/キャラへ1手で寄れる＝各1/ターンの
    暗躍+1（脚本家能力フェイズ＝暗躍禁止で止まらない）を任意の対象へ注げるとみなす（健全側）。"""
    return sum(1 for n in _role_named(state, "クロマク")
               if state.characters[n].alive and state.characters[n].on_board)


def _race_grade(cur: int, threshold: int, unblock_per_turn: int, card_per_turn: int,
                days: int, once_unblock: int = 0):
    """暗躍/不安の"レース会計"を3段階に。返り値 (grade, needs_kinshi)。

    unblock_per_turn＝暗躍禁止で止まらない打点/ターン（クロマク・事件・カルティスト無視等）。
    card_per_turn＝暗躍禁止で止まる打点/ターン（普通の暗躍カード）。once_unblock＝1回だけの止まらない打点（噂等）。
    - 止まらない供給だけで臨界に届く → 確実（needs_kinshi=False）
    - 全供給で届くが止まる分が要る → 拮抗（needs_kinshi=True＝毎ターン暗躍禁止で止める）
    - 全供給でも届かない → 困難
    """
    unblock_reach = cur + unblock_per_turn * days + once_unblock
    total_reach = cur + (unblock_per_turn + card_per_turn) * days + once_unblock
    if unblock_reach >= threshold:
        return FORCED, False
    if total_reach >= threshold:
        return CONTESTED, True
    return HARD, False


def _board_path(state, board: str, days_left: int) -> PathRace:
    cur = state.board_anyaku.get(board, 0)
    removal = _has_board_removal(state, board)
    once = 1 if ("不穏な噂" in state.script.rule_xs and not state.rumor_used) else 0
    # 邪気の汚染（神社+2）：★「確実（FORCED）」の時だけ止まらない打点として足す。
    # 拮抗（犯人を冷やせば止まる）まで足すと、防衛可能な脚本を防衛不能と誤判定する
    # （2026-07-08：黒猫を外した btx_seal が誤って mastermind のままになった実測）。
    # 拮抗の邪気は冷却の需要として別カウント（不安-1は席ごとに使える＝暗躍禁止の
    # 1枚制限とは別資源なので、被覆（kinshi_sources）には数えない）。
    for f in incident_feasibility(state.script):
        if f.grade == FORCED and f.day >= state.day and f.name == "邪気の汚染" and board == "神社":
            once += 2
    # ★塞げない供給は「主人公が位置で妨害できないもの」に限る＝不穏な噂（ボード直指定）・
    #   確実な事件・既存カウンター（除去役なし）。クロマクのボード注ぎは主人公がクロマクを
    #   ボードから引き離せば止まる（移動＝位置で対処＝塞げる側）。カルティスト無視も同様に
    #   カルティスト退避で塞げる。＝クロマクは card（塞げる）側に置く（偽陽性回避）。
    unblock = 0
    card = 1 + _kuromaku_count(state)
    if cur >= 2 and not removal:
        return PathRace("board", FORCED, False,
                        f"{board}の暗躍が既に{cur}（≥2）で除去役なし＝敗北条件成立済み。",
                        source=f"board:{board}")
    grade, kinshi = _race_grade(cur, 2, unblock, card, days_left, once)
    if grade == FORCED and removal:
        grade, kinshi = CONTESTED, True   # 除去役が居れば毎ターン剥がして防げる
    note = {FORCED: f"{board}へ暗躍禁止で止まらない打点だけで臨界2に届く（現在{cur}）。",
            CONTESTED: f"{board}は暗躍カードで臨界2に届くが暗躍禁止で止められる（現在{cur}・残り{days_left}日）。",
            HARD: f"{board}は残り{days_left}日の打点では臨界2に届かない（現在{cur}）。"}[grade]
    return PathRace("board", grade, kinshi, note, source=f"board:{board}")


def _genso_pump_path(state, board: str, days_left: int):
    """★幻想＝クロマクがゴールボードに立つ二正面（2026-07-07）。

    通常のクロマクは「キャラへの直接移動で引き剥がせる」＝card側（塞げる）だが、
    幻想は行動カードを直接セットできない（KB: 30）＝引き剥がしはボード経由の移動のみ。
    そのボード枠は暗躍禁止と同一（主人公は同一対象に重ねられない）＝
    - 暗躍禁止を置けばカードは止まるが能力の+1/日（脚本家能力フェイズ）は素通り
    - 引き剥がしを置けばポンプは逸れるが同ターンのボードカードが通る
    の二者択一を毎ターン迫られる。独立した暗躍禁止要求として計上し、カード経路
    （board:{board}）との被覆超過＝二正面でMASTERMIND判定に乗せる。
    ※移動合成の読み合い（引き剥がし後の連れ戻し）を含むため FORCED にはしない（健全側）。
    """
    g = state.characters.get("幻想")
    if not (g and g.alive and g.on_board and g.area == board and g.role == "クロマク"):
        return None
    cur = state.board_anyaku.get(board, 0)
    if cur + days_left < 2 or _has_board_removal(state, board):
        return None
    return PathRace(
        "genso_pump", CONTESTED, True,
        f"幻想＝クロマクが{board}に立つ＝能力の+1/日は暗躍禁止で止まらず、引き剥がしは"
        f"ボード経由＝暗躍禁止と{board}の枠を食い合う（現在{cur}・残り{days_left}日）。",
        source=f"genso_pump:{board}")


def _immobile_pump_path(state, board: str, days_left: int):
    """★移動不可のクロマク/カルティストがゴールボードに張り付く＝防御不能（ユーザー確認 2026-07-13）。

    クロマク＝能力(+1/日・脚本家能力フェイズ)は暗躍禁止で止まらず、通常は「キャラへ移動で
    引き剥がせる」＝card側だが、移動不可なら引き剥がせない＝止まらない。カルティスト＝その板の
    暗躍禁止を無視する＝主人公のボード暗躍禁止が効かず、移動でも剥がせない＝行動解決の暗躍+が通る。
    どちらも「移動で剥がす」唯一の対処が封じられる＝FORCED（幻想=ボード移動で逸らせる より強い）。

    ★移動可否は**現在の禁止エリア**（c.forbidden＝医者能力3の解除等を反映）で判定する。
      入院患者は医者能力3で禁止解除されうる＝医者が盤上なら詰みにしない（ユーザー注意）。
      少女は禁止エリア無し＝そもそも張り付かない。除去役（暗躍除去）が居れば防げるので除外。"""
    from engine.board import AREAS
    from engine.data import forbidden_of
    cur = state.board_anyaku.get(board, 0)
    if cur + days_left < 2 or _has_board_removal(state, board):
        return None
    lifted = getattr(state, "forbidden_lifted", None) or set()
    doctor = any(n == "医者" and c.alive for n, c in state.characters.items())
    for role in ("クロマク", "カルティスト"):
        for n in _role_named(state, role):
            c = state.characters.get(n)
            if not (c and c.alive and c.on_board and c.area == board):
                continue
            # 現在の禁止エリア＝静的 forbidden_of から forbidden_lifted（医者能力3等）を除く。
            forb = frozenset() if n in lifted else (forbidden_of(n) or frozenset())
            # この板以外へ行けない＝張り付き（移動で剥がせない）。
            if set(AREAS) - set(forb) != {board}:
                continue
            # ★入院患者(医者能力3)・少女は移動可になりうる＝詰みにしない（ユーザー注意 2026-07-13）。
            #   少女は元々禁止エリア無しでここに来ないが、明示的に除外して健全側に倒す。
            if n == "少女" or (n == "入院患者" and doctor):
                continue
            return PathRace(
                "immobile_pump", FORCED, False,
                f"移動不可の{role}〈{n}〉が{board}に張り付く＝引き剥がせず暗躍禁止でも"
                f"止まらない供給＝防御不能（現在{cur}・残り{days_left}日）。",
                source=f"immobile_pump:{board}")
    return None


def _killer_paths(state, days_left: int) -> list[PathRace]:
    """キラーの勝ち筋：キーパーソン殺害（KP暗躍≥2＋同エリア）／主人公殺害（キラー暗躍≥4）。

    ★2×2盤面ではキラーは1手でKPの居るエリアへ寄れる＝同エリアは移動禁止/移動で毎ターン
    争う関係。KP暗躍を臨界へ運べるかを暗躍会計で判定（クロマクは暗躍禁止で止まらない）。
    """
    out: list[PathRace] = []
    killers = [n for n in _role_named(state, "キラー")
               if state.characters[n].alive and state.characters[n].on_board]
    if not killers:
        return out
    km = _kuromaku_count(state)
    char_removal = _has_char_anyaku_removal(state)
    kps = [n for n in _role_named(state, "キーパーソン")
           if state.characters[n].alive and state.characters[n].on_board]
    # (1) キーパーソン殺害：KP暗躍≥2＋キラー同エリアで殺害。
    #   ★逃げ道：キラー殺害は「同エリア」要求なので、主人公はKPを移動で引き離せば防げる
    #   （盤面/自カウンター系と違い"塞げない供給で確定"にはならない）＝常に拮抗で扱う（健全側＝
    #   偽陽性回避）。分離が資源競合するのは被覆側で killer4 と別枠に数える。
    for kp in kps:
        cur = state.characters[kp].anyaku
        # KP暗躍が臨界に届く見込みが全く無いなら脅威にしない（クロマク＋カードの上限で判定）。
        if cur + (km + 1) * days_left < 2:
            continue
        # ★移動モデル（ユーザー知見 2026-07-06）：主人公は隣接移動のみ・脚本家は斜め＋移動禁止で
        #   妨害する＝「KPを引き離す（分離）」は当てにできない。確実な防御はKP暗躍を止める暗躍禁止
        #   ＝needs_kinshi=True（被覆では killer4 と同じ源"killer"にまとめて二正面の過剰計上を防ぐ）。
        out.append(PathRace("killer_kp", CONTESTED, True,
                   f"キーパーソン〈{kp}〉はキラー殺害圏（暗躍2＋同エリア）。分離は妨害され当てに"
                   f"できず、確実な防御はKPの暗躍を暗躍禁止で止めること（現在暗躍{cur}）。",
                   source="killer"))
    # (2) 主人公殺害：キラー自身に暗躍≥4（同エリア不要＝分離では防げない）。
    #   クロマクのキラー暗躍注ぎは、キラーとクロマクを引き離せば止まる＝塞げる側（card）。
    for k in killers:
        cur = state.characters[k].anyaku
        grade, kinshi = _race_grade(cur, 4, 0, 1 + km, days_left)
        if grade == HARD:
            continue
        if grade == FORCED and char_removal:
            grade, kinshi = CONTESTED, True   # 暗躍除去役が居れば剥がして防げる
        note = (f"キラー〈{k}〉へクロマクの暗躍（止まらない）で暗躍4に届く＝主人公殺害（現在{cur}）。"
                if grade == FORCED
                else f"キラー〈{k}〉は暗躍カードで4に届くが暗躍禁止/除去で止められる（現在{cur}）。")
        out.append(PathRace("killer4", grade, kinshi, note, source="killer"))
    return out


def _sk_path(state, days_left: int) -> PathRace | None:
    """シリアルキラー（妄想拡大ウイルスのSK化含む）：2人きりで殺害＝位置管理で防ぐ。
    ★同エリア（2人きり）要求＝分離の逃げ道あり＝保守的に拮抗（偽陽性回避）。"""
    sk = [n for n in _role_named(state, "シリアルキラー")
          if state.characters[n].alive and state.characters[n].on_board]
    sk += [n for n, c in state.characters.items()
           if c.virus_serial and c.alive and c.on_board]
    virus = ("妄想拡大ウイルス" in state.script.rule_xs
             and any(c.role == "パーソン" for c in state.characters.values()))
    if not sk and not virus:
        return None
    who = "／".join(sorted(set(sk))) if sk else "パーソン(ウイルスでSK化)"
    return PathRace("sk", CONTESTED, False,
                    f"シリアルキラー（{who}）は2人きりで【強制】殺害。誰とも2人きりにしない"
                    "位置管理で防ぐ（分離の逃げ道あり）。")


def _mainlovers_path(state, days_left: int) -> PathRace | None:
    """メインラバーズ：不安≥3かつ暗躍≥1で主人公殺害（50:160）。同エリア不要＝分離では防げず、
    不安-1／暗躍禁止で閾値を割れば防げる＝拮抗。"""
    mls = [n for n in _role_named(state, "メインラバーズ")
           if state.characters[n].alive and state.characters[n].on_board]
    if not mls:
        return None
    ml = mls[0]
    c = state.characters[ml]
    # 不安 or 暗躍のどちらかを割れば防げる＝暗躍禁止に一本化しない（needs_kinshi=False）。
    return PathRace("mainlovers", CONTESTED, False,
                    f"メインラバーズ〈{ml}〉は不安≥3かつ暗躍≥1で主人公を殺害"
                    f"（現在 不安{c.unrest}/暗躍{c.anyaku}）。不安-1か暗躍禁止で閾値を割れば防げる。")


def _tt_path(state, days_left: int) -> PathRace | None:
    """タイムトラベラー：最終日のターン終了に友好≤2なら主人公敗北を宣言できる（50:128）。
    友好禁止は無視されるので主人公はいつでも友好を盛れる＝友好3以上に保てば防げる。"""
    tts = [n for n in _role_named(state, "タイムトラベラー")
           if state.characters[n].alive and state.characters[n].on_board]
    if not tts:
        return None
    tt = tts[0]
    g = state.characters[tt].goodwill
    is_final = state.day >= state.script.days_per_loop
    if is_final and g <= 2 and g + 2 < 3:  # 最終日・友好0＝この1ターンで3に届かない
        return PathRace("tt", FORCED, False,
                        f"タイムトラベラー〈{tt}〉は最終日・友好{g}＝この1ターンで友好3に"
                        "届かず敗北宣言される。")
    if g < 3:
        return PathRace("tt", CONTESTED, False,
                        f"タイムトラベラー〈{tt}〉は最終日に友好≤2で敗北宣言（現在友好{g}）。"
                        "友好+（友好禁止は無視＝必ず通る）で3以上に保てば防げる。")
    return None  # 友好3以上＝宣言不可＝脅威ではない


def _butterfly_path(state, days_left: int) -> PathRace | None:
    """未来改変プラン×蝶の羽ばたき：発生で敗北条件成立。発生可否は incident_feasibility で会計。"""
    if state.script.rule_y != "未来改変プラン":
        return None
    for f in incident_feasibility(state.script):
        if f.name == "蝶の羽ばたき" and f.day >= state.day:
            if f.grade == FORCED:
                return PathRace("butterfly", FORCED, False,
                                f"{f.day}日目 蝶の羽ばたき（犯人{f.culprit}）は確実に発生＝"
                                "未来改変プランで敗北。")
            if f.grade == CONTESTED:
                return PathRace("butterfly", CONTESTED, False,
                                f"{f.day}日目 蝶の羽ばたき（犯人{f.culprit}）は放置で発生＝"
                                "犯人を毎ターン冷やせば止まる。")
    return None


def _incident_kill_paths(state, days_left: int) -> list[PathRace]:
    """殺害系事件の発生しやすさ（incident_feasibility）を勝ち筋として反映。"""
    from .script_quality import KILL_INCIDENTS
    role_set = {c.role for c in state.characters.values()}
    if not ({"キーパーソン", "フレンド"} & role_set):
        return []
    out = []
    for f in incident_feasibility(state.script):
        if f.name not in KILL_INCIDENTS or f.day < state.day:
            continue
        if f.grade == FORCED:
            out.append(PathRace("incident_kill", FORCED, False,
                       f"{f.day}日目 {f.name}（犯人{f.culprit}）は確実に発生＝殺害の勝ち筋。"))
        elif f.grade == CONTESTED:
            out.append(PathRace("incident_kill", CONTESTED, False,
                       f"{f.day}日目 {f.name}（犯人{f.culprit}）は放置で発生＝犯人を毎ターン"
                       "冷やせば止まる（不安-1）。"))
    return out


def _kp_anyaku_path(state, days_left: int) -> PathRace | None:
    """僕と契約しようよ！＝キーパーソンに暗躍≥2でループ終了時敗北。"""
    if state.script.rule_y != "僕と契約しようよ！":
        return None
    kps = _role_named(state, "キーパーソン")
    if not kps:
        return None
    kp = kps[0]
    c = state.characters[kp]
    if not (c.alive and c.on_board):
        return None
    cur = c.anyaku
    if cur >= 2 and not _has_char_anyaku_removal(state):
        return PathRace("kp_anyaku", FORCED, False,
                        f"キーパーソン〈{kp}〉の暗躍が既に{cur}（≥2）＝敗北条件成立済み。")
    # クロマクのKP暗躍注ぎは主人公がKP/クロマクを引き離せば止まる＝塞げる側（card）。
    grade, kinshi = _race_grade(cur, 2, 0, 1 + _kuromaku_count(state), days_left)
    note = {FORCED: f"キーパーソン〈{kp}〉へクロマクの暗躍（止まらない）で臨界2に届く（現在{cur}）。",
            CONTESTED: f"キーパーソン〈{kp}〉は暗躍カードで臨界2に届くが暗躍禁止で止められる（現在{cur}）。",
            HARD: f"キーパーソン〈{kp}〉は残り日数で暗躍2に届かない（現在{cur}）。"}[grade]
    return PathRace("kp_anyaku", grade, kinshi, note)


# ---------------------------------------------------------------------------
def analyze_loop(state) -> LoopRaceReport:
    """現局面から、このループの詰みをレース/被覆で判定する（高速・全局面対応・近似）。"""
    days_left = max(0, state.script.days_per_loop - state.day + 1)
    paths: list[PathRace] = []

    for b in _goal_boards(state):
        paths.append(_board_path(state, b, days_left))
        gp = _genso_pump_path(state, b, days_left)
        if gp:
            paths.append(gp)
        ip = _immobile_pump_path(state, b, days_left)   # 移動不可クロマク/カルティスト張り付き
        if ip:
            paths.append(ip)
    kp = _kp_anyaku_path(state, days_left)
    if kp:
        paths.append(kp)
    paths.extend(_killer_paths(state, days_left))
    paths.extend(_incident_kill_paths(state, days_left))
    for _p in (_sk_path(state, days_left), _mainlovers_path(state, days_left),
               _tt_path(state, days_left), _butterfly_path(state, days_left)):
        if _p:
            paths.append(_p)

    active = [p for p in paths if p.grade != HARD]
    forced = [p for p in active if p.grade == FORCED]
    contested = [p for p in active if p.grade == CONTESTED]

    if forced:
        return LoopRaceReport(MASTERMIND, days_left, paths,
                              "塞げない勝ち筋がある（" + "／".join(p.key for p in forced) + "）。")
    # 被覆：暗躍禁止を要求する拮抗脅威が"独立した源で"2つ以上＝主人公は1枚/ターンしか出せず
    #   二正面で崩れる。★同じ源（例：killer_kp と killer4 は同じキラー）は1つに数える（重複排除）。
    kinshi_sources = {p.src() for p in contested if p.needs_kinshi}
    if len(kinshi_sources) >= 2:
        return LoopRaceReport(MASTERMIND, days_left, paths,
                              "暗躍禁止を要する独立脅威が2つ以上＝1枚/ターンでは両方塞げない（二正面）。")
    if not active:
        return LoopRaceReport(PROTAGONIST, days_left, paths,
                              "残り日数で臨界に届く勝ち筋が無い（全て困難）。")
    # 塞げる脅威だけ＝主人公が専用の枠で毎ターン対処すれば防げる（近似：防衛可）。
    return LoopRaceReport(PROTAGONIST, days_left, paths,
                          "残る脅威は塞げる範囲（暗躍禁止/不安-1で毎ターン対処可能）。")


def analyze_script(script) -> LoopRaceReport:
    """脚本の初期局面（ループ開始・カウンター0）でレース判定する（評価器/ビルダー用）。

    ★塞げない供給が初手から臨界に届く脚本（例：不穏な噂＋短い日数、確実な事件）を検出できる。
    """
    from engine.data import initial_area_of
    from .state import GameState
    st = GameState(script=script)
    # 手先等（初期エリアが脚本家指定）は既定エリアを仮置き（初期局面の目安）。
    dyn = {n: "都市" for n in script.cast if initial_area_of(n) is None}
    st.prepare_loop(dyn)
    return analyze_loop(st)


def describe_report(rep: LoopRaceReport) -> list[str]:
    """レース判定を人間向けの行リストに。"""
    icon = {MASTERMIND: "🔴 脚本家の確定勝ち",
            PROTAGONIST: "🟢 主人公の防衛可",
            CONTESTED_V: "🟡 読み合い"}[rep.verdict]
    lines = [f"{icon}（残り{rep.days_left}日）：{rep.reason}"]
    gi = {FORCED: "🔴", CONTESTED: "🟡", HARD: "⚪"}
    for p in rep.paths:
        lines.append(f"  {gi.get(p.grade, '')} [{p.key}] {p.grade}：{p.note}")
    return lines
