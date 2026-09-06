# -*- coding: utf-8 -*-
"""B-245（的A）：**供給役の同定に、公開の `present` 交差を使う**。

## 何をするモジュールか（起票源＝B-244 §3-2／§6 的A・バックログ §72-35）

`HeuristicProtagonist._b202_pairs`（不安チャネルの供給分離）は、供給役 `supp` を
**belief のミスリーダー周辺確率**で同定する（受け手と同室で `P(ML) > 0.05` の者）。
分岐 (ii)（**供給役の側を動かす**）は「1枚で確実に切れる」ことを担保するため
`len(supp) == 1` を要求する。∴ **受け手の部屋に ML 候補が2人居るだけで死ぬ**。

ユーザー実戦棋譜（`docs/feedback_logs/鈴蘭_BTX3d_seed0_4つON後検証_2026-08-17.jsonl`）の
L4D2 がまさにその形＝受け手（医者）の部屋に **巫女 P=0.372・黒猫 P=0.107** の2人が居て
`len(supp)==2` → 分岐 (ii) が落ち、その席は `不安+1→巫女`（犯人候補ポンプ）を選んだ。
★**供給は位置で決まり不安では決まらない**（`rules/40_first_steps.md:108`＝ミスリーダーは
**同一エリア**のキャラ1人に不安+1）＝供給役に不安を積むのは供給に無関係。

★ところが**公開情報だけで供給役は一意に割れていた**：
能力フェイズの不安+1 は公開イベント（`unrest` × `phase=="mastermind_ability"` × `delta>0`）で
**発動時の同室の顔ぶれ `present` も公開**されている。その **`present` の交差**は
教材では L3 以降ずっと `{巫女}`。★**この交差は B-208 が既に計算している**
（`_b208_pairs` の `common = frozenset.intersection(*presents) - {rcv}`）。

∴ 本モジュールは**新語彙ではなく既存2語彙の配線**＝B-202 の供給役同定に B-208 の材料を流す。

## 述語（狭い・加点側のみ）

`supp` が **2人以上**のときだけ、過去ループの `present` 交差を取り、
その交差が **`supp` の中のただ1人**を指すなら `supp` をその1人に狭める。
それ以外は**現行と bit 同一**（`supp` をそのまま返す）。

- ★**cap を1つも足さない**。落とす手も、席を譲らせる手も無い＝
  同じ席の**宛先が振り替わる**だけ（分岐 (ii) が既存の `PRIORITY["不安供給分離_受け手"]` を
  返せるようになる）。§72-32 判例1／B-241 判例1（**席の調停は玉突きを起こす**）を避ける設計。
- **材料は公開情報のみ**＝公開イベント履歴の `target`／`present`／`loop`。神視点は使わない。
- **過去ループの観測だけ**を使う（`e["loop"] != 現在ループ`）＝`_b208_pairs`／`_b230_pairs` と
  同じ規律（当ループの観測は当ループの推理に使わない）。

## 弱点（正直な申告）

- 交差は**受け手が部屋を移った場合も混ぜて取る**（B-208 と同じ）。真の供給役が
  受け手に追随していれば交差に残るが、**偶然ずっと同行していた第三者も残る**
  ＝交差が一意でも供給役とは限らない。∴ 一意性は**証拠の強さ**であって証明ではない。
- 脚本家は**ダミー配置で `present` を汚染できる**（B-241 判例3＝「過去ループ実績は
  脚本家が能動的に汚染できる信号」）。ただし本語彙が汚染で失うのは「1枚で切れる」の
  確からしさだけで、**cap を持たないので他の手を潰すことはない**。
"""
from __future__ import annotations


def present_intersection(view: dict, rcv: str) -> frozenset:
    """受け手 `rcv` への能力供給が観測された時の `present` の**交差** − {rcv}。

    材料＝公開イベント `unrest` × `phase=="mastermind_ability"` × `delta>0` の `present`。
    ★**過去ループの観測だけ**を使う（`_b208_pairs`／`_b230_pairs` と同じ規律）。
    観測が1件も無ければ空集合を返す。
    """
    cur = view.get("loop")
    presents: list = []
    for e in view.get("history", []) or []:
        if (e.get("event") == "unrest"
                and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0
                and e.get("loop") != cur
                and e.get("target") == rcv):
            pres = frozenset(e.get("present") or ())
            if pres:
                presents.append(pres)
    if not presents:
        return frozenset()
    return frozenset.intersection(*presents) - {rcv}


def narrow_supplier(view: dict, rcv: str, supp) -> tuple:
    """`supp`（受け手と同室の ML 候補）を公開の `present` 交差で一意化する。

    `len(supp) >= 2` かつ交差が `supp` の中のただ1人を指すときだけ、その1人だけの
    タプルを返す。それ以外は `tuple(supp)` をそのまま返す（＝現行と bit 同一）。
    """
    supp = tuple(supp)
    if len(supp) < 2:
        return supp
    common = present_intersection(view, rcv)
    if not common:
        return supp
    here = tuple(n for n in supp if n in common)
    return here if len(here) == 1 else supp
