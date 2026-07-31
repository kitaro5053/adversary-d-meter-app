# -*- coding: utf-8 -*-
"""B-112：**ユーザーが実際にプレイした脚本**の再現（発火面積の検証用コーパス）。

起点＝ユーザーの指摘（2026-07-30）：
  「B-100 の発動が少なすぎる」。FableA の実測では、ユーザーが実際にプレイした脚本を
  AI対AIで12局回して**介入 0回**、ユーザーの実戦棋譜でも主人公の配置57件すべてに
  `prov` タグが無い（＝1度も発火していない）。

∴ **ベンチ標準130/70局だけでなく、この脚本そのもので発火面積を測る**必要がある。

## 脚本（★cast は再現＝要確認）

| 項目 | 値 | 出典 |
|---|---|---|
| セット / ループ日数 | FS / **5日** | ユーザー申告 |
| ルールY × ルールX | **守るべき場所 × 不穏な噂** | ユーザー申告 |
| 配役 | 刑事＝ミスリーダー／医者＝カルティスト／女子学生＝キーパーソン | ユーザー申告 |
| 事件 | D2 **行方不明**（犯人＝サラリーマン）／D4 **遠隔殺人**（犯人＝男子学生） | ユーザー申告 |
| cast（7人） | 男子学生・女子学生・巫女・刑事・サラリーマン・医者・黒猫 | ★**未申告＝`sim/sample_scripts.guard_script` と同じ標準7人で再現** |

★**cast は申告されていない**ので、同じルールY/Xを使う既存サンプル（`guard_script`）と
同じ7人を置いた。**役職の付いた4人（刑事・医者・女子学生＋犯人2人）は申告どおり**なので、
発火面積の議論に効く部分（脅威の型・供給候補）は再現できているとみなす。
残りの1人（巫女・黒猫の扱い）だけが再現の自由度である＝**doc に明記して扱う**。
"""

from __future__ import annotations

from sim.state import Incident, Script

#: 既定の対局数（FableA の実測が12局だったので既定を合わせる）
N_SEEDS = 12


def user_fs5() -> Script:
    """ユーザーが実際にプレイした脚本（FS 5日級・守るべき場所×不穏な噂）。"""
    return Script(
        rule_y="守るべき場所",
        rule_x="不穏な噂",
        loops=3,
        days_per_loop=5,
        cast=["男子学生", "女子学生", "巫女", "刑事", "サラリーマン", "医者", "黒猫"],
        roles={"女子学生": "キーパーソン", "医者": "カルティスト",
               "刑事": "ミスリーダー"},
        incidents=[
            Incident(day=2, name="行方不明", culprit="サラリーマン"),
            Incident(day=4, name="遠隔殺人", culprit="男子学生"),
        ],
    )


#: 名前 → 生成関数
USER_SCRIPTS = {"user_fs5": user_fs5}


def user_script_list(n_seeds: int = N_SEEDS) -> list:
    """`arena.benchmark.benchmark_scripts` と同じ `(名前, seed, Script)` の形で返す。"""
    out = []
    for name, f in USER_SCRIPTS.items():
        for seed in range(n_seeds):
            out.append((name, seed, f()))
    return out
