"""凍結済みの旧版AI（過学習対策のベンチマークプール）。

軍拡競争（脚本家↔主人公の相互アップデート）で特定バージョン同士への過学習が進まないよう、
各版を**凍結して残し**、新版は「旧版・ランダム・ランダム脚本」全部に対して非退行を確認する
（arena/league.py）。凍結版は編集しない（belief も凍結コピーを使う＝基準がぶれない）。

- mastermind_v1: 2026-07-04時点（v2適応切替・情報衛生の前）
- protagonist_v1: 2026-07-05時点（belief_v1使用。mm-phase推理フィルタの前）
- protagonist_v2: 2026-07-05時点（belief_v2使用。位置戦術・拒否推理・逐次条件付けまで。
  情報収集プレイ＝v3 の前）
- protagonist_v3: 2026-07-05時点（belief_v3使用。公開情報物理フル＝位置制約/死の場合分け/
  否定形消去/生存ペア/因果の糸＋実験モード＋情報→防御変換（危険事件冷却・浄化一元投資・
  TT封じ）まで。vs mm_v2 軍拡ラウンド＝v4 の前）
- mastermind_v2: 2026-07-06時点（適応切替・情報衛生・抑制モード・賢い拒否・友好禁止の
  無駄撃ちゲートまで。残手数会計＋二正面圧力＝v3 の前）
"""

from .mastermind_v1 import HeuristicMastermind as HeuristicMastermindV1
from .mastermind_v2 import HeuristicMastermind as HeuristicMastermindV2
from .protagonist_v1 import HeuristicProtagonist as HeuristicProtagonistV1
from .protagonist_v2 import HeuristicProtagonist as HeuristicProtagonistV2
from .protagonist_v3 import HeuristicProtagonist as HeuristicProtagonistV3

__all__ = ["HeuristicMastermindV1", "HeuristicMastermindV2",
           "HeuristicProtagonistV1", "HeuristicProtagonistV2", "HeuristicProtagonistV3"]
