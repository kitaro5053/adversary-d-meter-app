"""札の実効性述語＝**単一チョークポイント**（B-28・AIB 2026-07-17）。

`agents/heuristic_protagonist.py` の空振りゲート（`_base_score` が 3.5 等の低値を返す箇所）と
`agents/defense_plan.py` の折り手（Break）生成が、**同一の述語**を参照するための唯一の真実。

## なぜ要るか（B-28監査の結論）

    score(o) = _base_score(o) + PLAN_HOT(+88)      # 折り手に選ばれたら加点

`_base_score` が「証明可能に空振り」と判定して 3.5 に落としても、plan が同じ手を折り手として
出していれば +88 されて上書きされる。**plan 側に同じゲートが無い限り heuristic のゲートは効かない**
＝B-21（移動不能へのピン 3.5→91.5）・B-26（mm札なしへのピン→89.0）・B-21b（relocate）で
3回踏んだ同一構造。個別に塞ぐのをやめ、両者が同じ述語を見る形で構造的に封鎖する。

## 契約

- 判定材料は**公開情報のみ**（盤面・mmのセット位置・カウンター）＋belief由来の容疑集合（ctx）。
- **判定不能は None（＝無効とは言えない）＝健全側**。過剰減点・過剰非提示をしない。
- `Noop.score` は heuristic が返す代表値（既存値をそのまま単一ソース化）。
  plan 側は `noop_reason(...) is not None` を「その折り手を出さない」条件に使う。

## DP-1 Stage 2 との関係

Stage 2 の `covered` 判定は **(1) 空振りでない（`noop_reason is None`）** かつ
**(2) レースに勝つ（`sim/loop_race._race_grade`＝B-27）** の2条件（FableA採択 2026-07-17）。
本モジュールは (1) を提供する。
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.board import compose_moves, destination
from engine.data import forbidden_of, unrest_threshold_of
from engine.models import MOVE_CARDS
from sim.state import current_forbidden_from_view

# 主人公の移動カード（斜めは無い）。heuristic_protagonist._MOVE_TOGGLE と同一。
MOVE_TOGGLE: dict[str, tuple[int, int]] = {"移動←→": (1, 0), "移動↑↓": (0, 1)}

# 空振り/自滅の代表値（heuristic の既存 return 値をそのまま単一ソース化＝挙動同値）。
NOOP_SCORE: float = 3.5        # 証明可能な空振り（no-op）
KP_ZONE_SCORE: float = 2.0     # KPを致死事件の kill zone へ送る（自滅・大減点）
SUICIDE_SCORE: float = -100.0  # 自滅回避（PRIORITY["自滅回避"] と同値）
# ★B-86'：友好+ が**無駄ではなく有害**な席（＝相手に弾を渡す）。空振り(3.5)より下に置く。
GW_ARMS_MM_SCORE: float = 2.0  # 医者×友好無視×友好2＝脚本家能力フェイズの不安+1を解禁
# ★B-109：最終日の友好+ が**因果の糸で有害**な席（友好0→1にするとループ終了時に
#   「友好が置かれていた」が成立し、次ループ開始時にそのキャラへ不安+2＝`rules/50:85`）。
#   値は GW_ARMS_MM_SCORE と同一＝**新しいマジックナンバーを増やさない**。
GW_END_HARM_SCORE: float = GW_ARMS_MM_SCORE


@dataclass(frozen=True)
class NoopCtx:
    """述語が要る文脈（heuristic/plan の双方が自分の持ち物から組む）。

    mm_chars ＝ mmが今ターン札を伏せたキャラ（**位置は公開**・中身は伏せ）。
    kill_zone ＝ 今日の致死事件のゾーン（例：病院の事件＝"病院"）。無ければ None。
    mm_boards ＝ mmが今ターン札を伏せた**ボード**（同じく位置だけ公開）。
      ★既定 **None＝「材料が無い＝判定不能」**（`frozenset()` は「1枚も置いていない」の意）。
      None のとき G7（板の空振り判定）は None を返す＝健全側＝**既存の呼び出し側の挙動は不変**。
    """

    mm_chars: frozenset = frozenset()
    keyperson: str | None = None
    kill_zone: str | None = None
    kuromaku_suspects: frozenset = frozenset()
    killer_suspects: frozenset = frozenset()
    friend_guards: frozenset = frozenset()
    mm_boards: frozenset | None = None
    # ★B-86'（G8＝友好+ の空振り／有害）の材料。**すべて既定は空**＝材料が無い＝
    #   G8 は何も判定しない＝**既存の呼び出し側（defense_plan 等）の挙動は完全に不変**。
    gw_keep: frozenset = frozenset()            # 切ってはいけない対象（TT の可能性＝rules/50:127-128）
    gw_refused: frozenset = frozenset()         # 拒否を観測した＝友好無視保持の証明（rules/20:24）
    gw_ignore_certain: frozenset = frozenset()  # 友好無視を持つ配役が確定的
    gw_info_exhausted: frozenset = frozenset()  # 自身開示しか持たず、その役職は既知
    gw_arms_mm: frozenset = frozenset()         # 友好2で脚本家に弾を渡す（医者・rules/60:81 B-8）
    # ★B-109（G9/G10＝「このターン何も変えない手」）の材料。**すべて既定は空**＝材料が無い＝
    #   何も判定しない＝**既存の呼び出し側（defense_plan 等）の挙動は完全に不変**。
    gw_final_void: frozenset = frozenset()      # (キャラ, step) ＝最終日に置いても新規解禁が無い
    gw_final_harm: frozenset = frozenset()      # 上のうち因果の糸で有害になるキャラ（友好0のとき）
    unrest_void: frozenset = frozenset()        # 不安-1 が算術的にゼロなキャラ（残り事件0 等）
    # ★B-155（G2 の例外条項に**距離の条件**を足す切替口）。**既定 None＝判定しない**＝
    #   材料が無い＝**既存の呼び出し側（defense_plan・b100_mix 等）の挙動は完全に不変**。
    #   意味＝「不安0＋mm札あり」の例外を認める上限距離（`th - unrest <= gap` の時だけ例外）。
    unrest_decoy_gap: int | None = None
    #: ★B-155 変種＝距離の条件を **L1D1（情報ゼロの初手）だけ**に限る（既定 False＝全ターン）。
    #  ユーザー指摘の現物が L1D1／L1D2 であること＋B-45 の L1D1 定石との席の取り合いが
    #  実測されていることから、**最も狭い述語**として用意した掃引口。
    unrest_decoy_opening_only: bool = False


@dataclass(frozen=True)
class Noop:
    """その札がその対象に無効/自滅である理由と、heuristic が返すべき代表値。"""

    reason: str
    score: float


def _char(view: dict, name: str | None) -> dict | None:
    if not name:
        return None
    for c in view.get("characters", []):
        if c.get("name") == name:
            return c
    return None


def _alive(view: dict, name: str | None) -> dict | None:
    c = _char(view, name)
    return c if (c and c.get("alive", True) and c.get("area") is not None) else None


def immobile_static(name: str) -> bool:
    """**静的**な実質移動不可（カード印刷の禁止エリアが3つ＝A.I./ご神木/入院患者/女の子）。

    ★T1（2026-09-04）：G3/G4 の判定は本関数を使わなくなった＝`immobile_now`（公開履歴の
      解除を織り込む・B-293 と同じ狭い述語）に置き換えた。本関数は「カード上の性質」の
      問い合わせ用に残す（呼び出し側＝テスト／注記のみ）。
    """
    try:
        return len(forbidden_of(name) or ()) >= 3
    except Exception:  # noqa: BLE001
        return False


def immobile_now(view: dict, name: str) -> bool:
    """**今**（このループ・この盤面で）完全に不動か＝**どの移動札をどう合成しても現在地に留まる**。

    ★T1（2026-09-04）＝脚本家側 B-293（`agents/heuristic._b293_move_is_void`）と**同じ狭い述語**：
      - 禁止エリアは静的な `engine.data.forbidden_of` を直読みせず
        `sim.state.current_forbidden_from_view` から取る＝医者の友好能力3（入院患者の禁止解除）／
        女の子の友好能力1（`rules/20_goodwill_abilities.md`）で**このループ中だけ解除**されている
        局面を「動けない」扱いしない。公開履歴 `forbidden_lifted`（`view["history"]`・
        `view["loop"]` 一致分のみ）から再構成する＝神視点にならない。
      - 「不動」＝移動札3種（`engine.models.MOVE_CARDS`）の各行き先（`engine.board` の合成）が
        **すべて**禁止＝現在地以外の3エリアが全部禁止。合成の結果は必ずこの3エリアか現在地の
        いずれか（2×2盤・XOR）なので、これは `rules/60` A-2（合成してから禁止判定1回）の下で
        **KB が一意に決める**空振り。
      - 対象が盤上に居ない（`area` None＝未登場／死体）＝材料が無い＝**False（言えない側）**。
    """
    c = _char(view, name)
    area = (c or {}).get("area")
    if not area:
        return False
    forb = current_forbidden_from_view(view, name)
    if not forb:
        return False
    return all(destination(area, compose_moves([mc])) in forb for mc in MOVE_CARDS)


def move_dest(src: str | None, card: str) -> str | None:
    """移動カードで src からどこへ行くか（未登場/非移動カードは None）。"""
    t = MOVE_TOGGLE.get(card)
    return destination(src, t) if (src and t) else None


def noop_reason(view: dict, card: str, target: str, target_kind: str,
                ctx: NoopCtx) -> Noop | None:
    """その札がその対象に対して『公開情報から証明可能に無効（空振り）／自滅』なら Noop、
    有効かもしれないなら None。**判定不能は None（健全側）**。

    ★Step 0（挙動同値リファクタ）時点で実装するのは、heuristic に既にあったゲートのみ：
      G1 暗躍禁止→キャラ・mm札なし ／ G2 不安-1→不安0・mm札なし ／ G3 移動禁止→移動不能 ／
      G4 移動→行き先が禁止（不成立） ／ G5 移動→KPを致死zoneへ ／ G6 移動→クロマク送り込み。
    plan 側への接続（G1/G2/G4/G5/G6 の穴埋め）は Step 1 以降＝段階land（B-28 §4）。
    ★B-103（2026-07-30）で **G7＝板への暗躍禁止の空振り** を追加（`ctx.mm_boards` が必要。
      未指定＝None なら従来どおり None を返す＝呼び出し側の挙動は不変）。
    """
    # --- G7：暗躍禁止は「そのボードに載った今ターンの暗躍+」しか打ち消せない -------------
    # ★G1（キャラ版）とまったく同じ KB 帰結の**ボード版**。B-28 の単一チョークポイントは
    #   `target_kind != "character"` で早期 return していたため、板だけこの述語を持たず、
    #   `heuristic_protagonist._base_score` が void な板へ 80+暗躍 を出し続けていた
    #   （手練れユーザーが2度指摘した疑問手＝B-103）。
    # ルール接地：
    #   - 暗躍禁止が無効化するのは「**重なった**暗躍+1/+2」だけ（`rules/10:61`）＝
    #     既にボードに載っているカウンターは除去しない。
    #   - 暗躍禁止は**行動解決フェイズでのみ**有効（`rules/10:65`）＝クロマク・不穏な噂
    #     （`rules/40:62,88`・`rules/60:105 C-5`）・黒猫のループ開始時 神社+1（`rules/30:75`）・
    #     事件由来の暗躍は**どれも止まらない**。
    #   - 主人公は脚本家の**セット位置を見てから**置く（`rules/00:105-106`）＝void の判定は
    #     推定ではなく**公開情報からの確定**。
    #   ∴ mm が今ターンその板に札を1枚も置いていない＝打ち消せる暗躍+が存在しない＝
    #     **算術的にゼロ**（賭けの要素すら無い）。
    if target_kind == "board" and card == "暗躍禁止" and ctx.mm_boards is not None:
        if target not in ctx.mm_boards:
            return Noop("mm板札なし＝今ターン打ち消す暗躍+が無い（空振り）", NOOP_SCORE)
        return None
    if target_kind != "character":
        return None

    # --- G8：友好+ の空振り／有害（B-86'・手練れユーザーが2度指摘した無駄打ち）------------
    # ルール接地（`rules/` から一意に読める部分だけを使う）：
    #   - 友好能力は「記載されたマーク（ハート）の個数**以上**の友好」で使える（`rules/20:20`）
    #     ＝友好+ の価値は「次の閾値へ到達させて能力を実際に使えるようにすること」にある。
    #   - 拒否できるのは**能力を使うキャラ**が友好無視／絶対友好無視を持つ場合のみ
    #     （`rules/20:24`・`rules/00:173-174`）。★強さの区別：
    #       **絶対**友好無視＝【強制】必ず拒否＝**規則上ゼロが保証される**。
    #       通常の友好無視＝【任意】＝拒否は脚本家の選択＝厳密には規則上ゼロではなく
    #       「**有能な相手なら必ず拒否するので実質ゼロ**」。
    #     ∴ どちらを集合に入れるかは**呼び出し側の閾値**で決める（本述語は集合を信じる）。
    #   - 配役は**ゲーム中固定**（非公開シート＝`rules/00:76`）＝「拒否された」「役職が開示された」は
    #     **永続的な事実**＝以後の全ループで有効。
    #   - ★例外＝タイムトラベラー（`rules/50:127-128`）＝最終日のターン終了フェイズに友好2以下だと
    #     任意敗北を宣言されうる＝**友好3以上は敗北条件の封じ手**。∴ TT の可能性が残る対象は
    #     `gw_keep` に入れて**絶対に切らない**（切ってよいのは将来にわたって価値ゼロのものだけ）。
    #   - ★空撃ちの情報価値（`rules/20:25`＝絶対友好無視の判別に使える定番テク）を潰さないため、
    #     **確定していない相手は集合に入れない**（呼び出し側の責務）。
    if card in ("友好+1", "友好+2"):
        if target in ctx.gw_keep:
            return None
        c = _alive(view, target)
        step = 2 if card == "友好+2" else 1
        # ★無駄ではなく**有害**：医者が友好無視を持つ場合、友好2以上で脚本家が
        #   **脚本家能力フェイズ**に医者の能力（不安±1）を使えるようになる（`rules/60:81` B-8・
        #   実カード表記＝`rules/20:230-232`「友好無視を持ち、かつ友好2以上」）。
        #   ＝主人公が自分で相手に供給源を渡す手。標準では**医者だけ**（軍人・教師は不可）。
        if target in ctx.gw_arms_mm and c is not None \
                and (c.get("goodwill", 0) or 0) + step >= 2:
            return Noop("医者×友好無視＝友好2で脚本家能力フェイズの不安+1を解禁（有害）",
                        GW_ARMS_MM_SCORE)
        if target in ctx.gw_refused:
            return Noop("拒否を観測＝友好無視保持が判明（配役は固定）＝以後も使えない",
                        NOOP_SCORE)
        if target in ctx.gw_ignore_certain:
            return Noop("友好無視を持つ配役が確定＝友好能力は拒否される", NOOP_SCORE)
        if target in ctx.gw_info_exhausted:
            return Noop("自身開示済み＝この能力から得られる情報は既に持っている（空振り）",
                        NOOP_SCORE)
        # --- G9（B-109）：**最終日**に置いても新たに解禁される能力が無い友好+ -------------
        # ルール接地：
        #   - 友好カウンターの効果は「友好能力の使用可否」だけ（`rules/20:20`＝ハート数**以上**で
        #     使用可・`rules/20:22`＝使っても減らない／1ループ1回の能力はそのループ1回のみ）。
        #   - 友好能力を使うのは**主人公能力フェイズ**＝同じ日の行動解決フェイズの**後**。
        #   - ループ開始時に**全カード・全ボードのカウンターを除去**（`rules/00:86`）。
        #   ∴ 最終日に置いた友好は「その日の主人公能力フェイズで**新たに**使える能力」を
        #      解禁しない限り、ループ終了で消える＝**算術的にゼロ**。
        #   ★TT（`rules/50:127-128`）は `gw_keep` で既に上で除外済み＝ここには来ない。
        if (target, step) in ctx.gw_final_void:
            if target in ctx.gw_final_harm:
                return Noop("最終日＋新規解禁なし＋因果の糸＝次ループに不安2を呼ぶ（有害）",
                            GW_END_HARM_SCORE)
            return Noop("最終日＋新規解禁なし＝ループ終了でカウンターごと消える（空振り）",
                        NOOP_SCORE)
        return None

    # --- G1：暗躍禁止は「そのキャラに載った今ターンの暗躍+」しか打ち消せない ---
    if card == "暗躍禁止":
        if target not in ctx.mm_chars:
            return Noop("mm札なし＝今ターン打ち消す暗躍+が無い（空振り）", NOOP_SCORE)
        return None

    # --- G2：不安-1 は床0＝不安0かつmm札なしなら確実に空振り ---
    if card == "不安-1":
        c = _alive(view, target)
        if c and c.get("unrest", 0) == 0 and target not in ctx.mm_chars:
            return Noop("不安0＋mm札なし＝床0で空振り", NOOP_SCORE)
        # --- B-155：例外条項（mm札あり）に**距離の条件**を足す（既定 None＝無効） -------
        # ルール接地（**距離の部分は規則が決めない＝価値の話**。ここで規則が言うのは
        # 「重なった時の順序」だけ＝`rules/10_action_cards.md:35`『不安-1』と重なると
        # 『不安+1』が先。∴ 不安0の対象では、mm札が `不安+1` でない限り `不安-1` は
        # 床0で**算術的にゼロ**）。ところが**伏せ札の中身は見えない**（`sim/views.py:41-49`）＝
        # 「mm札がある」だけを例外にすると、脚本家の `移動` 札がそのまま冷却札を焼く囮になる
        # （ユーザー実戦 2026-08-04・B-155）。∴ **臨界までの距離**で例外を絞る：
        #   `th - unrest <= gap` ＝ gap=1 なら「今日の +1 が当たれば臨界に届く」対象だけ。
        # ★`不安-1` は 1/loop（`engine/models.py:29`）＝チーム3枚/ループ。対して `不安+1` は
        #   毎日戻る2枚（同 `:38`・`sim/flow.py:251-255`）＝遠い対象への先回りは常に負ける勘定。
        if (c and c.get("unrest", 0) == 0 and target in ctx.mm_chars
                and ctx.unrest_decoy_gap is not None
                and (not ctx.unrest_decoy_opening_only
                     or (view.get("loop") == 1 and view.get("day") == 1))):
            th = unrest_threshold_of(target)
            if th and (int(th) - int(c.get("unrest", 0) or 0)) > ctx.unrest_decoy_gap:
                return Noop("不安0＋mm札はあるが臨界まで遠い＝今日の不安+1でも届かない"
                            "（囮で 1/L 冷却札を焼く）", NOOP_SCORE)
        # --- G10（B-109）：不安を参照する帰結がこのループにもう存在しない -----------------
        # ルール接地：
        #   - 不安カウンターの効果は「事件の発生条件」だけ（`rules/00:30,38`＝不安臨界以上で
        #     事件発生の条件／`rules/40:157`＝発生条件2つを両方満たすと必ず発生）。
        #     FS の役職（`rules/40`）に不安の閾値を参照するものは**1つも無い**。
        #     BTX だけが2つ例外を持つ＝妄想拡大ウイルス(X)（`rules/50:79-81`）と
        #     メインラバーズ（`rules/50:159-160`）＝呼び出し側が集合から外す責務を持つ。
        #   - ループ開始時に全カウンターを除去（`rules/00:86`）。
        #   ∴ このループに残り事件が1件も無ければ、不安を1つ減らしても**算術的にゼロ**。
        if target in ctx.unrest_void:
            return Noop("このループに残り事件なし＋不安参照の役職/ルールなし＝空振り",
                        NOOP_SCORE)
        return None

    # --- G3：移動禁止は「そのキャラに載った今ターンの移動カード」しか打ち消せない ---
    if card == "移動禁止":
        # ★T1：静的 `immobile_static` → `immobile_now`（当ループの解除を織り込む・B-293 と同型）。
        if immobile_now(view, target):
            return Noop("対象が実質移動不可＝打ち消す移動が無い（空振り）", NOOP_SCORE)
        return None

    # --- G4/G5/G6：移動カード ---
    if card in MOVE_TOGGLE:
        c = _alive(view, target)
        dest = move_dest(c["area"] if c else None, card)
        # G4：行き先が禁止＝移動は不成立（その場に留まる）＝空振り
        # ★T1-lite（2026-09-04）：禁止エリアの**取得元だけ**を当ループの解除を織り込んだ公開情報版
        #   （`sim.state.current_forbidden_from_view`）に替えた。述語（「自札の行き先が禁止なら空振り」）
        #   は従来のまま＝mm札の有無で分岐しない。※`rules/60` A-2（合成後に禁止判定1回）に照らした
        #   狭化（mm札あり→None）は lane/t1-prot-forbidden 2a17c15c に分離＝T1b で別途設計
        #   （ベンチ検死で同点帯の列挙順と `_relocate_breaks` の折り手復活に副作用が出たため）。
        if dest is None or dest in current_forbidden_from_view(view, target):
            return Noop("行き先が禁止エリア＝移動不成立（空振り）", NOOP_SCORE)
        # G5：致死事件の kill zone へ KP を動かさない（クロマク等の移動は正当用途があるのでKP限定）
        if ctx.kill_zone is not None and dest == ctx.kill_zone \
                and target == ctx.keyperson:
            return Noop("KPを今日の致死事件のkill zoneへ送る＝自滅", KP_ZONE_SCORE)
        # G6（B-22）：確定クロマクを「キラー疑い＋KP/フレンド疑いが同居する」エリアへ送り込む
        #   ＝供給→殺害を主人公自ら完成させる自殺手。引き離す移動は dest に victim が居ない＝不発火。
        if target in ctx.kuromaku_suspects and dest != (c["area"] if c else None):
            killer_at_dest = any(
                (kc := _alive(view, k)) and kc["area"] == dest
                for k in ctx.killer_suspects if k != target)
            if killer_at_dest:
                vip_at_dest = (
                    ctx.keyperson and ctx.keyperson != target
                    and (_alive(view, ctx.keyperson) or {}).get("area") == dest
                ) or any(
                    f != target and (fc := _alive(view, f)) and fc["area"] == dest
                    for f in ctx.friend_guards)
                if vip_at_dest:
                    return Noop("確定クロマクをキラー＋VIP同居エリアへ送り込む＝自滅", SUICIDE_SCORE)
        return None

    return None
