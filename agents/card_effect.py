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

from engine.board import destination
from engine.data import forbidden_of

# 主人公の移動カード（斜めは無い）。heuristic_protagonist._MOVE_TOGGLE と同一。
MOVE_TOGGLE: dict[str, tuple[int, int]] = {"移動←→": (1, 0), "移動↑↓": (0, 1)}

# 空振り/自滅の代表値（heuristic の既存 return 値をそのまま単一ソース化＝挙動同値）。
NOOP_SCORE: float = 3.5        # 証明可能な空振り（no-op）
KP_ZONE_SCORE: float = 2.0     # KPを致死事件の kill zone へ送る（自滅・大減点）
SUICIDE_SCORE: float = -100.0  # 自滅回避（PRIORITY["自滅回避"] と同値）


@dataclass(frozen=True)
class NoopCtx:
    """述語が要る文脈（heuristic/plan の双方が自分の持ち物から組む）。

    mm_chars ＝ mmが今ターン札を伏せたキャラ（**位置は公開**・中身は伏せ）。
    kill_zone ＝ 今日の致死事件のゾーン（例：病院の事件＝"病院"）。無ければ None。
    """

    mm_chars: frozenset = frozenset()
    keyperson: str | None = None
    kill_zone: str | None = None
    kuromaku_suspects: frozenset = frozenset()
    killer_suspects: frozenset = frozenset()
    friend_guards: frozenset = frozenset()


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
    """実質移動不可か（禁止エリアが3つ＝1エリアにしか居られない・A.I./ご神木等）。

    ★静的判定＝`heuristic_protagonist._immobile_of` と同一（挙動同値のため Step 0 ではこれを使う）。
    動的解除（医者能力3で入院患者／女の子の自力解除）を織り込む判定は
    `defense_plan._cannot_move_now` 側にある（B-21）。**両者の統一は挙動変更＝別Step**。
    """
    try:
        return len(forbidden_of(name) or ()) >= 3
    except Exception:  # noqa: BLE001
        return False


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
    """
    if target_kind != "character":
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
        return None

    # --- G3：移動禁止は「そのキャラに載った今ターンの移動カード」しか打ち消せない ---
    if card == "移動禁止":
        if immobile_static(target):
            return Noop("対象が実質移動不可＝打ち消す移動が無い（空振り）", NOOP_SCORE)
        return None

    # --- G4/G5/G6：移動カード ---
    if card in MOVE_TOGGLE:
        c = _alive(view, target)
        dest = move_dest(c["area"] if c else None, card)
        # G4：行き先が禁止＝移動は不成立（その場に留まる）＝空振り
        if dest is None or dest in forbidden_of(target):
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
