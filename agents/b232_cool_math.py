# -*- coding: utf-8 -*-
"""B-232：`B224_COOL_FLOOR`（切替口②）への**冷却算術ゲート**（B-229 §7-1「案1」）。

## 何をするモジュールか（起票源＝B-229 検死・B-230 の残件）

B-224 の切替口②は「同署名の敗北 run の署名に今日の事件発生が載っている」とき、
その事件の**当日犯人候補への `不安-1`** に採点床（`B224_FLOOR`=103.0）を立てる。
B-229 の検死（`docs/仮_b229_log/報告_B229.md`）は、5日級 `random_BTX#1` の
**唯一の未防衛局**の敗因を **C（`B224_COOL_FLOOR`）単独**と同定し、その因果の起点が
**L6D3 の floor 1席**＝「冷やしても今日の臨界に届くことが**発火時点で算術上確定して
いる**席」だったと結論した（§4「C の因果経路」）。

本モジュールは、その席を**公開情報だけで**識別して床を立てない（＝他の手に席を譲る）。

## 冷却算術（材料はすべて公開情報＝belief も神視点も使わない）

事件チェック時の候補の不安の**下界**：

    final = u                                          # 今の不安（公開カウンター）
          + (mm が今日この候補に札を伏せている ? 0 : -1)  # 冷却1枚の実効
          + (供給実績ペアの相方が今この候補と同室 ? +1 : 0)

- 第2項＝`不安-1` は重ね置き不可＋1ループ1回（`engine/models.py:30`）＝冷却は**上限 -1/日**。
  ただし mm が同じ候補に札を伏せている場合、最悪ケースは `不安+1`（相殺で差引 0）か
  `不安禁止`（`engine/models.py:49`＝重なった `不安-1` を無効化）＝**どちらでも冷却は効かない**。
  伏せ札の**位置は公開**（中身だけ非公開）＝`view["placements"]` から読める。
- 第3項＝**過去ループ**の脚本家能力フェイズ `不安+1` の（受け手×同室 present）実績。
  B-208 ①／B-230 と同じ公開イベントチャネルで、単一ソースとして
  `HeuristicProtagonist._b230_pairs()` を再利用する。相方が**今この候補と同室**なら
  今日も +1 が入りうる（ミスリーダー能力＝同一エリアの1人へ +1・`rules/40:105-110`）。
- **フェイズ順**（`rules/00_rules_core.md`：主人公行動 → 行動解決 → 脚本家能力 → … → 事件）
  ＝カードの ±1 は行動解決で相殺し、能力の +1 はその**後**に乗り、事件はさらに後で臨界を見る。

`final >= 臨界` なら「今日この候補を冷やしても事件は止まらない」＝**証明可能な空振り**
（B-196「証明可能な空振り」・B-224 条件6〔臨界0の黒猫〕と同じ思想。条件5〔過去に試して
負けた筋〕が**事後**の証拠で除外するのに対し、本ゲートは**発火時点の算術**で除外する）。

## なぜ B-224 の取り分を壊さないか（★設計の要点＝B-229 §7-1）

B-224 が 9[fb_win]→4/5[defense] に反転させた教材局 `btx_future#4` の floor 席は
実測で2つとも `u=2 / 臨界3 / mm札=有 / 供給実績なし` ＝ `final = 2 + 0 + 0 = 2 < 3`
＝**空振りでない**＝ゲートは発火しない（Phase 0 で実装前に確認・回帰テストで固定）。
一方 `random_BTX#1` L6D3 は `u=1 / 臨界2 / mm札=有 / 供給実績ペア同室` ＝ `final = 2 >= 2`
＝発火する。**「試す」価値と「証明可能な空振り」は算術で分離できる**。

## 規律

- 材料＝`view` の公開情報のみ（カウンター・位置・mm の伏せ札**位置**・公開イベント履歴）。
  belief（`_culprit_cands`）は B-224 側が既に使っており、本ゲートは**追加の情報源を持たない**。
- 既定 OFF（`B232_COOL_MATH=False`）＝呼ばれない＝挙動 bit 不変。
"""
from __future__ import annotations

from engine.data import unrest_threshold_of


def supply_obs(agent, view: dict) -> dict:
    """{受け手: {供給役候補: 観測された過去ループ数}}（脚本家能力フェイズの 不安+1）。

    `HeuristicProtagonist._b230_pairs` と**同じ公開イベント**を読むが、こちらは
    「何ループで観測したか」を数える（`B232_SUPPLY_MIN_OBS` の掃引用）。
    無向＝受け手側／供給役側のどちらから引いても同じ組を返す。
    ループ内で不変（材料＝過去ループの公開履歴のみ）＝ループ単位でキャッシュ。
    """
    lp = view.get("loop")
    if getattr(agent, "_b232_obs_key", None) == lp:
        return agent._b232_obs_cache
    out: dict = {}
    for e in view.get("history", []) or []:
        if (e.get("event") == "unrest"
                and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0
                and e.get("loop") != lp):
            rcv = e.get("target")
            for p in (e.get("present") or ()):
                if p == rcv:
                    continue
                out.setdefault(rcv, {}).setdefault(p, set()).add(e.get("loop"))
                out.setdefault(p, {}).setdefault(rcv, set()).add(e.get("loop"))
    agent._b232_obs_key = lp
    agent._b232_obs_cache = out
    return out


def cool_math_final(agent, view: dict, cand: str) -> tuple | None:
    """候補の冷却算術。`(final, th, u, cool, supply)` か、判定不能なら None。

    判定不能＝キャラが盤上にいない／臨界が 0・不明（＝B-224 条件6 が既に除外）。
    """
    th = unrest_threshold_of(cand)
    if not th:
        return None
    c = agent._alive(view, cand)
    if c is None:
        return None
    u = int(c.get("unrest", 0) or 0)
    area = c.get("area")
    # 冷却1枚の実効＝mm の伏せ札が同じ候補に重なっていれば 0（不安+1 で相殺／不安禁止で無効）
    mm_on = any(p.get("owner") == "mastermind"
                and p.get("target_kind") == "character"
                and p.get("target") == cand
                for p in (view.get("placements") or []))
    cool = 0 if mm_on else -1
    # 能力フェイズの供給＝過去ループの実績ペアの相方が今この候補と同室か。
    # ★`B232_SUPPLY_MIN_OBS`＝その組を**何ループで観測したか**の下限（掃引口）。
    #   能力の宛先は「同室の1人」から mm が**選ぶ**＝1回の観測は「今日も来る」を
    #   保証しない（実測＝`btx_future#*` で 1回観測の組が今日は別人へ向かった）。
    min_obs = int(getattr(agent, "B232_SUPPLY_MIN_OBS", 1) or 1)
    supply = 0
    obs = supply_obs(agent, view).get(cand) or {}
    for other, loops in obs.items():
        if len(loops) < min_obs:
            continue
        oc = agent._alive(view, other)
        if oc is not None and oc.get("area") == area:
            supply = 1
            break
    return (u + cool + supply, th, u, cool, supply)


def hopeless_cool_targets(agent, view: dict, cands) -> frozenset:
    """`cands` のうち**発火時点で証明可能な空振り**（算術上勝てない）候補の集合。

    `B232_REQUIRE_SUPPLY`（既定 True＝狭い版）＝公開の**能力供給実績が同室にある**
    証拠が立っている候補だけを対象にする。False（広い版＝B-229 §7-1 の字義）＝
    カード算術だけで臨界に届く候補も対象にする。
    """
    if not getattr(agent, "B232_COOL_MATH", False):
        return frozenset()
    need_supply = bool(getattr(agent, "B232_REQUIRE_SUPPLY", True))
    out = set()
    for cand in cands:
        got = cool_math_final(agent, view, cand)
        if got is None:
            continue
        final, th, _u, _cool, supply = got
        if need_supply and not supply:
            continue
        if final >= th:
            out.add(cand)
    return frozenset(out)
