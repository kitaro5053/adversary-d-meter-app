"""ターン終了フェイズの死亡判定（フェーズ9の一部）。

範囲（KB: 40/50 役職／60 D 死亡誘発・同時処理）:
- **シリアルキラー【強制】**：同一エリアの生存キャラが自分以外1人だけ→そのキャラを死亡させる。
  2体のシリアルキラーだけが同エリア→相打ち（同時解決）。不死（タイムトラベラー）は死亡しない。
- **強制の連鎖**：キーパーソン死亡→主人公は敗北し直ちにループ終了。
  ラバーズ／メインラバーズ死亡→相方に不安+6（両者同時死亡なら何も起きない・KB: 60 D-3）。
- **任意【任意】＝脚本家の選択**：キラー（同エリアのKPに暗躍2で殺害／自身に暗躍4で主人公殺害）、
  メインラバーズ（不安3＋暗躍1で主人公殺害）、タイムトラベラー（最終日・友好2以下で主人公敗北）。
  → エンジンは「発動可能（脚本家判断）」と提示するだけ＝自動では起こさない（最終盤面は脚本家次第）。
- **【強制】は【任意】より先**（KB: 60 D-1）。複数の【強制】は同時解決（KB: 60 D-2）。

未モデル＝要確認：刑事の護衛カウンター・軍人の主人公不死・妄想拡大ウイルスの役職変化。
これらが絡む場合は warnings に積み、数値を勝手に確定しない。効果の詳細（誰を選ぶ等）は脚本家判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Board
from .turn_end_rules import (
    is_immortal,
    killer_can_kill_protagonist,
    killer_killable_keypersons,
    mainlover_can_kill_protagonist,
    serial_killer_victims,
    timetraveler_can_defeat,
)


@dataclass
class TurnEndResult:
    forced_deaths: list[dict] = field(default_factory=list)   # [{name, role, cause}] 確定死亡
    optional_actions: list[str] = field(default_factory=list)  # 任意で発動可能な効果の説明
    chains: list[str] = field(default_factory=list)            # 強制の連鎖効果の説明
    loop_ends_now: bool = False                                # ループ終了が誘発されるか
    defeat: bool | None = None                                 # 主人公の敗北が確定するか
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)          # 未モデル・要確認
    needs_confirmation: bool = False


def resolve_turn_end(board: Board, spec: dict | None = None) -> TurnEndResult:
    """ターン終了フェイズの死亡判定を裁定する。

    spec: {
        "is_final_day": bool,          # 最終日のターン終了フェイズか（タイムトラベラー判定に使用）
        "protagonist_immortal": bool,  # 主人公不死（軍人友好5等）が有効か
        "virus_rule": bool,            # 妄想拡大ウイルス（BTX）が脚本に入っているか
    }
    キャラの anyaku/unrest/goodwill/guard/area/alive/role は Board から読む。
    """
    spec = spec or {}
    is_final_day = bool(spec.get("is_final_day", False))
    protagonist_immortal = bool(spec.get("protagonist_immortal", False))
    virus_rule = bool(spec.get("virus_rule", False))

    alive = {c.name: c for c in board.characters.values() if c.alive}
    reasons: list[str] = []
    warnings: list[str] = []
    forced_deaths: list[dict] = []
    optional: list[str] = []
    chains: list[str] = []

    def _acts_serial(c) -> bool:
        # 妄想拡大ウイルス：元パーソンが不安3以上でシリアルキラー化（KB: 50 / 60 C-3）。
        return c.role == "シリアルキラー" or (
            virus_rule and c.role == "パーソン" and c.unrest >= 3)

    # 妄想ウイルスの不安2（ヒステリシス＝直前状態依存）は単発盤面では確定できない→要確認。
    if virus_rule:
        amb = [c.name for c in alive.values() if c.role == "パーソン" and c.unrest == 2]
        if amb:
            warnings.append(
                f"妄想拡大ウイルス：不安ちょうど2のパーソン{amb}のシリアルキラー化は"
                "直前状態依存（ヒステリシス・不安3で化・1以下で解除）＝単発では要確認")

    # --- シリアルキラー【強制】（同時解決：死亡前のスナップショットで対象決定） ---
    # 犠牲者算定は sim/effects と共通の turn_end_rules.serial_killer_victims（単一ソース）。
    victim_killer = serial_killer_victims(list(alive.values()), _acts_serial)
    for name, killer_name in victim_killer.items():
        killer = alive[killer_name]
        tag = "" if killer.role == "シリアルキラー" else "（妄想ウイルスでシリアルキラー化）"
        cause = f"シリアルキラー〈{killer_name}〉{tag}と{killer.area}で2人きり【強制】"
        c = alive[name]
        if is_immortal(c.role):
            reasons.append(
                f"〈{name}〉は{cause}だが不死（{c.role}）→死亡しない（発生したが何も起きない）"
            )
        elif getattr(c, "guard", 0) > 0:
            reasons.append(
                f"〈{name}〉は{cause}だが護衛カウンター({c.guard})で肩代わり"
                "→死亡せず護衛を1消費（KB: 20 刑事）"
            )
        else:
            forced_deaths.append({"name": name, "role": c.role, "cause": cause})

    dead_names = {d["name"] for d in forced_deaths}
    if len(forced_deaths) >= 2 and all(_acts_serial(alive[d["name"]]) for d in forced_deaths):
        reasons.append("シリアルキラー同士が2人きり→相打ちで両者死亡（複数の【強制】が同時解決）")

    # --- 強制の連鎖（死亡から誘発される【強制】） ---
    loop_ends = False
    defeat: bool | None = None
    if any(d["role"] == "キーパーソン" for d in forced_deaths):
        chains.append("キーパーソン死亡【強制】→ 主人公は敗北し、直ちにこのループを終了（KB: 50）")
        loop_ends = True
        defeat = True

    lov_dead = any(d["role"] == "ラバーズ" for d in forced_deaths)
    main_dead = any(d["role"] == "メインラバーズ" for d in forced_deaths)
    main_alive = [c for c in alive.values() if c.role == "メインラバーズ" and c.name not in dead_names]
    lov_alive = [c for c in alive.values() if c.role == "ラバーズ" and c.name not in dead_names]
    if lov_dead and main_dead:
        chains.append("ラバーズとメインラバーズが同時死亡→ 何も起きない（対象が既に死体・KB: 60 D-3）")
    else:
        if lov_dead and main_alive:
            chains.append(
                f"ラバーズ死亡【強制】→ メインラバーズ〈{main_alive[0].name}〉に不安+6（KB: 50）")
        if main_dead and lov_alive:
            chains.append(
                f"メインラバーズ死亡【強制】→ ラバーズ〈{lov_alive[0].name}〉に不安+6（KB: 50）")

    # --- 任意【任意】＝脚本家の選択（発動可能とだけ提示・自動では起こさない） ---
    # 発動条件は sim/effects と共通の turn_end_rules 述語（単一ソース）。
    survivors = [c for c in alive.values() if c.name not in dead_names]
    for c in survivors:
        if c.role == "キラー":
            kps = [o for o in killer_killable_keypersons(c, survivors)]
            if kps:
                optional.append(
                    f"キラー〈{c.name}〉：同エリアのキーパーソン〈{kps[0].name}〉"
                    f"（暗躍{kps[0].anyaku}≥2）を殺害可能【任意】")
            if killer_can_kill_protagonist(c):
                optional.append(
                    f"キラー〈{c.name}〉：自身の暗躍{c.anyaku}≥4→主人公を殺害可能【任意】")
        elif c.role == "メインラバーズ":
            if mainlover_can_kill_protagonist(c):
                optional.append(
                    f"メインラバーズ〈{c.name}〉：不安{c.unrest}≥3＋暗躍{c.anyaku}≥1"
                    "→主人公を殺害可能【任意】")
        elif c.role == "タイムトラベラー":
            if timetraveler_can_defeat(c, is_final_day):
                optional.append(
                    f"タイムトラベラー〈{c.name}〉：最終日・友好{c.goodwill}≤2"
                    "→主人公を敗北させ可能【任意】")

    # 主人公不死（軍人友好5等）が有効なら、主人公を「殺害」する任意は無効化される。
    # ただしタイムトラベラーの「敗北」は死亡ではないので無効化されない（KB: 50 / 20）。
    if protagonist_immortal:
        if any("主人公を殺害" in o for o in optional):
            warnings.append(
                "主人公不死が有効→キラー/メインラバーズの主人公殺害は発動しても無効（何も起きない）")
        if any("主人公を敗北" in o for o in optional):
            warnings.append(
                "主人公不死はタイムトラベラーの『敗北』は防げない（敗北は死亡ではない・KB: 50）")

    if not forced_deaths:
        reasons.insert(0, "強制の死亡（シリアルキラー等）は発生しない")
    else:
        reasons.insert(0, "確定死亡：" + "／".join(d["name"] for d in forced_deaths))

    # 任意効果があると最終盤面は脚本家の選択で変わる＝要確認。
    needs_conf = bool(optional)

    return TurnEndResult(
        forced_deaths=forced_deaths, optional_actions=optional, chains=chains,
        loop_ends_now=loop_ends, defeat=defeat, reasons=reasons,
        warnings=warnings, needs_confirmation=needs_conf,
    )
