# -*- coding: utf-8 -*-
"""belief のソフト重み証拠（phase-2・per-assignment behavioral prior）。

設計＝docs/設計提案_beliefソフト重み層_AIB_2026-07-14.md。各証拠は belief.register_soft_evidence で
登録し、平均場 μ(c,r)=exp(Σ λ·assign_logweight) が per-char marginal を Sinkhorn（行＋列正規化）で
再重み付けする（μ≡1で bit-for-bit）。ハード枝刈り（現行）が絞り切れない候補内を、行動の痕跡
（確率的傾向）で傾斜させる。μ>0＝真配役は消えない。
"""
from __future__ import annotations

from collections import Counter


class MisleaderUnrestPresence:
    """phase-2 第1証拠：ミスリーダーの不安痕跡（present頻度）。

    ミスリーダーは mm能力フェイズで不安+1を置く＝その瞬間ターゲットと同エリア（present）に居る必要が
    ある。事件を起こすため繰り返し不安を注ぐので、ハードの ml_set が絞り切れない候補内でも
    **不安イベントの present に居た回数** が真ML で多い。MI検証（標準3日級・L4+の残候補集合を条件）で
    present 特徴の top-1 精度 0.574（chance 0.312 の1.8倍）＝有意な公開信号（target回数はL4+で逆相関
    0.044＝不採用）。s(c)=c が mm能力フェイズ不安イベントの present に居た回数。r=ミスリーダー のときだけ効く。
    μ(c,ミスリーダー)=exp(λ·s(c))>0 ＝真ML は消えない。λ は calibration の grid で決める。
    """

    def __init__(self, lam: float = 0.3):
        self.lam = lam
        self._cache_id = None
        self._counts: Counter = Counter()

    def _counts_for(self, ctx) -> Counter:
        hist = ctx["history"]
        if id(hist) != self._cache_id:      # 履歴は observe ごとに置換＝id で1回だけ集計
            counts: Counter = Counter()
            for e in hist:
                if (e.get("phase") == "mastermind_ability"
                        and e.get("event") == "unrest"
                        and e.get("delta", 0) > 0):
                    for p in (e.get("present") or []):
                        counts[p] += 1
            self._counts = counts
            self._cache_id = id(hist)
        return self._counts

    def assign_logweight(self, char: str, role: str, combo, ctx) -> float:
        if role != "ミスリーダー":
            return 0.0
        return float(self._counts_for(ctx).get(char, 0))
