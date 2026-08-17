# -*- coding: utf-8 -*-
"""B-100 Phase 1：混合AI（絶対防御）の**シャドー実行**＝「介入したらどうなるか」の反実仮想。

Phase 0（`arena/b100_audit.py`）は介入の**頻度・コスト・無駄率**までしか測っていない。
本CLIは実際に `HeuristicProtagonist.B100_MIX` を立てて対局を回し、

  - ベースライン（既定OFF）との **per-game 差分（flip）**
  - **介入した局・介入回数**（`agent._b100_log`）と、その局が反転したか
  - 掃引（鉄則①の prob 閾値・席ポリシー）

を同一プロセス・同一機械で連続測定する（規約 §4＝ベースラインは毎回実測）。

★測定は必ず `PYTHONHASHSEED=0`。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_shadow --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b100_shadow --days 5 \
        --configs off,theta1,iron067
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import replace

from agents import HeuristicMastermind, HeuristicProtagonist
from sim import run_game

#: 掃引する設定（クラス属性の上書き）。"off" が必ずベースライン（既定OFF＝現行AI）。
CONFIGS: dict[str, dict] = {
    "off":        {"B100_MIX": False},
    #: ★クラス既定のまま ON にしただけの設定（＝land 候補。他の config は明示上書き）。
    #  ★Phase 2 以降のクラス既定＝同時割り当て（JOINT=True・REQUIRE_PLAN=True・ガードなし）
    #  ＝`j1plan` と同一のはず（掃引の健全性チェック）。
    "default":    {"B100_MIX": True},
    # ★注意（Phase 2 で判明）：Phase 1 の掃引はガード2本が**存在しない**状態で測った。
    #   その後ガードがクラス既定（MAX_DISPLACED=80／PROTECT_CLASSES=("友好","不安")）に
    #   なったため、下の theta1/iron* は**ガード込み**になっており Phase 1 の数値を再現しない。
    #   Phase 1 の素の挙動を再現するのは p1raw / p1rawany（ガードを明示的に外した版）。
    "p1raw":      {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_SEAT_POLICY": "last", "B100_MAX_DISPLACED": None,
                   "B100_PROTECT_CLASSES": ()},
    "p1rawany":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_SEAT_POLICY": "any", "B100_MAX_DISPLACED": None,
                   "B100_PROTECT_CLASSES": ()},
    # θ経路の一点（Phase 0 §8-1）。席は後ろから1つだけ開ける。
    "theta1":     {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_SEAT_POLICY": "last"},
    # 同じ述語を「どの席でも」＝先頭席から奪う版（対照＝席ポリシーの効き目を分離する）
    "theta1any":  {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_SEAT_POLICY": "any"},
    # 鉄則①（同型の敗北の反復1回以上）の prob 閾値の掃引（§10-2）。
    # ※iron10 は θ=1.0 の部分集合＝theta1 と同一挙動になるはず（掃引の健全性チェック）。
    "iron10":     {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": 1.0,
                   "B100_SEAT_POLICY": "last"},
    "iron067":    {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": 0.67,
                   "B100_SEAT_POLICY": "last"},
    "iron05":     {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": 0.5,
                   "B100_SEAT_POLICY": "last"},
    # ★Phase 1 の実測から出た2つの狭い述語（下の2群）。
    #  (a) 3席分岐を殺す＝「確度100%級が2本」でも1席まで（random_BTX s16 L6D4 で3席全部を
    #      退避に使い 6→7 に退行したのが実測。Phase 0 の予測に反してこの分岐は発火する）。
    "cap1":       {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_SEATS_2X100": 1, "B100_SEAT_POLICY": "last"},
    #  (b) トレッドミル対策＝同一ループ内で同じ負け筋を覆う回数の上限
    #      （random_BTX s4 は L3〜L6 の全日に発火し 3→7）。
    "cap1lim2":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_SEATS_2X100": 1, "B100_MAX_PER_LOOP": 2,
                   "B100_SEAT_POLICY": "last"},
    "cap1lim1":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_SEATS_2X100": 1, "B100_MAX_PER_LOOP": 1,
                   "B100_SEAT_POLICY": "last"},
    "lim1":       {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_PER_LOOP": 1, "B100_SEAT_POLICY": "last"},
    #  (c) ★席の経済のガード＝「押し出す手が高価なら奪わない」（Phase 1 の検死＝実測では
    #      1.0点の退避が **100点の暗躍禁止（敗北板の防御）**を押し出していた）。
    #      policy="any" と組むと「最初に見つかった**安い席**を奪う」＝Phase 0 §4 の想定に近づく。
    "g40last":    {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 40.0, "B100_SEAT_POLICY": "last"},
    "g60last":    {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 60.0, "B100_SEAT_POLICY": "last"},
    "g40any":     {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 40.0, "B100_SEAT_POLICY": "any"},
    "g60any":     {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 60.0, "B100_SEAT_POLICY": "any"},
    "g80any":     {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 80.0, "B100_SEAT_POLICY": "any"},
    #  (d) ★状態依存のガード＝「友好投資は点数が安くても押し出さない」（B-99/B-94 の機序）。
    "g40lastF":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 40.0, "B100_SEAT_POLICY": "last",
                   "B100_PROTECT_CLASSES": ("友好",)},
    "g60anyF":    {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 60.0, "B100_SEAT_POLICY": "any",
                   "B100_PROTECT_CLASSES": ("友好",)},
    "g60anyFU":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 60.0, "B100_SEAT_POLICY": "any",
                   "B100_PROTECT_CLASSES": ("友好", "不安")},
    "g80anyFU":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_IRON_PROB": None,
                   "B100_MAX_DISPLACED": 80.0, "B100_SEAT_POLICY": "any",
                   "B100_PROTECT_CLASSES": ("友好", "不安")},
    # ------------------------------------------------------------------
    # ★Phase 2（席の割り当てを3席まとめて解く）＝`agents/b100_alloc.py`。
    #   ガードは既定で**使わない**（B100_JOINT_GUARDS=False）＝FableA所見の再評価。
    # ------------------------------------------------------------------
    #: 素の Phase 2（θ=1.0・最終席を残す・計画を消費する・ガードなし）
    "j1":         {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_SPARE_LAST": True, "B100_CONSUME_PLAN": True,
                   "B100_JOINT_GUARDS": False},
    #: 対照A＝最終席も奪う（設計4を外す）
    "j1nolast":   {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_SPARE_LAST": False, "B100_CONSUME_PLAN": True,
                   "B100_JOINT_GUARDS": False},
    #: 対照B＝計画を消費しない（Phase 1 の欠陥2をそのまま残す＝機序の切り分け）
    "j1noconsume": {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                    "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                    "B100_SPARE_LAST": True, "B100_CONSUME_PLAN": False,
                    "B100_JOINT_GUARDS": False},
    #: 対照C＝Phase 1 のガード2本を復活させる（本当に不要かの実測）
    "j1guard":    {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_SPARE_LAST": True, "B100_CONSUME_PLAN": True,
                   "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": 80.0,
                   "B100_PROTECT_CLASSES": ("友好", "不安")},
    #: 設計2の literal＝fatal×defendable なら実在度に関わらず強制対象
    "jall":       {"B100_MIX": True, "B100_JOINT": True, "B100_FORCE_GATE": "all",
                   "B100_IRON_PROB": None, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    #: 鉄則①（反復1回以上）を Phase 2 の割り当てで撃つ（Phase 1 は負の結果だった）
    "j1iron067":  {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": 0.67, "B100_FORCE_GATE": "theta",
                   "B100_SPARE_LAST": True, "B100_CONSUME_PLAN": True,
                   "B100_JOINT_GUARDS": False},
    "j1iron05":   {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": 0.5, "B100_FORCE_GATE": "theta",
                   "B100_SPARE_LAST": True, "B100_CONSUME_PLAN": True,
                   "B100_JOINT_GUARDS": False},
    #: 席上限を1に固定（「確度100%級2本→3席」分岐を殺す対照）
    "j1cap1":     {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_MAX_SEATS_2X100": 1, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    #: 同一ループ内で同じ負け筋を覆う回数の上限（トレッドミル対策の再測）
    "j1lim1":     {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_MAX_PER_LOOP": 1, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    "j1lim2":     {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_MAX_PER_LOOP": 2, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    #: ★1ループで払う席の総数の上限（Phase 2 の検死＝残った退行は「同ループ2席」か
    #   「毎ループ1席を延々」の形だった）。
    "j1sl1":      {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_MAX_SEATS_PER_LOOP": 1, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    "j1sl2":      {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_MAX_SEATS_PER_LOOP": 2, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    #: ★「3席分の意図が既知のときだけ払う」＝`_turn_plan` があるターンに限る（Phase 2 の検死）。
    "j1plan":     {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_REQUIRE_PLAN": True, "B100_SPARE_LAST": True,
                   "B100_CONSUME_PLAN": True, "B100_JOINT_GUARDS": False},
    "j1plansl1":  {"B100_MIX": True, "B100_JOINT": True, "B100_THETA": 1.0,
                   "B100_IRON_PROB": None, "B100_FORCE_GATE": "theta",
                   "B100_REQUIRE_PLAN": True, "B100_MAX_SEATS_PER_LOOP": 1,
                   "B100_SPARE_LAST": True, "B100_CONSUME_PLAN": True,
                   "B100_JOINT_GUARDS": False},
    # ------------------------------------------------------------------
    # ★Phase 3（発火面積の拡大・2026-07-29）＝`default`（クラス既定）から**1つだけ**動かす。
    #   ファネル実測（`arena/b100_funnel.py`）で最大の門と分かったのは
    #   REQUIRE_PLAN（3日級 D36→E34／5日級 D63→E44）。次が MAX_SEATS（5日級 F8）。
    #   ★ここは `{"B100_MIX": True, <1項目>}` の形で書く＝他はクラス既定のまま
    #     ＝`default` との差が1項目だけであることが定義から保証される（CF帰属の作法）。
    # ------------------------------------------------------------------
    "p3s2":        {"B100_MIX": True, "B100_MAX_SEATS": 2},
    "p3s3":        {"B100_MIX": True, "B100_MAX_SEATS": 3},
    "p3noplan":    {"B100_MIX": True, "B100_REQUIRE_PLAN": False},
    "p3nolast":    {"B100_MIX": True, "B100_SPARE_LAST": False},
    "p3iron067":   {"B100_MIX": True, "B100_IRON_PROB": 0.67},
    "p3iron05":    {"B100_MIX": True, "B100_IRON_PROB": 0.5},
    #: 2項目め以降（1項目ずつの結果を見てから積む）
    "p3noplan_s2": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_MAX_SEATS": 2},
    "p3noplan_s3": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_MAX_SEATS": 3},
    "p3noplan_s3_nolast": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                           "B100_MAX_SEATS": 3, "B100_SPARE_LAST": False},
    "p3noplan_iron067": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                         "B100_IRON_PROB": 0.67},
    #: ★Phase 3 の機序直し＝計画が無いターンでは B-100 が「落ちる需要」を選べていない
    #  （検死＝`random_BTX` s4 L3D2）。払う気になったターンだけ 3席一括計画を立てる。
    "p3mkplan":    {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_MAKE_PLAN": True},
    "p3mkplan_s2": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_MAKE_PLAN": True, "B100_MAX_SEATS": 2},
    "p3mkplan_s3": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_MAKE_PLAN": True, "B100_MAX_SEATS": 3},
    "p3mkplan_s2_iron067": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                            "B100_MAKE_PLAN": True, "B100_MAX_SEATS": 2,
                            "B100_IRON_PROB": 0.67},
    #: ★Phase 3 の機序直し②＝折り手そのものが別の2人きりを自作するのを止める
    #  （検死＝`random_BTX` s4 L3D2 で `移動←→→刑事` が 男子学生 を殺した）。
    "p3sh":        {"B100_MIX": True, "B100_SELF_HARM": True},
    "p3noplan_sh": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_SELF_HARM": True},
    "p3noplan_s2_sh": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                       "B100_MAX_SEATS": 2, "B100_SELF_HARM": True},
    "p3noplan_s3_sh": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                       "B100_MAX_SEATS": 3, "B100_SELF_HARM": True},
    "p3noplan_s2_sh_iron067": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                               "B100_MAX_SEATS": 2, "B100_SELF_HARM": True,
                               "B100_IRON_PROB": 0.67},
    #: ★"strict"＝自傷の折り手を含む制約は**この席から払わない**（別の折り手へ振り替えない）
    "p3noplan_shs":    {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                        "B100_SELF_HARM": "strict"},
    "p3noplan_s2_shs": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                        "B100_MAX_SEATS": 2, "B100_SELF_HARM": "strict"},
    "p3noplan_s3_shs": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                        "B100_MAX_SEATS": 3, "B100_SELF_HARM": "strict"},
    #: ★残った1件（3日級 random_FS s10 2→3）はトレッドミル＝同じ2人きりを毎ループ
    #  作り直され、毎ループ2席を吸われる形。既存の狭い述語2本を被せた対照。
    "p3noplan_s2_shs_sl1": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                            "B100_MAX_SEATS": 2, "B100_SELF_HARM": "strict",
                            "B100_MAX_SEATS_PER_LOOP": 1},
    "p3noplan_s2_shs_sl2": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                            "B100_MAX_SEATS": 2, "B100_SELF_HARM": "strict",
                            "B100_MAX_SEATS_PER_LOOP": 2},
    "p3noplan_s2_shs_lim1": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                             "B100_MAX_SEATS": 2, "B100_SELF_HARM": "strict",
                             "B100_MAX_PER_LOOP": 1},
    #: ★機序直し③＝計画が無いターンは**最終席でだけ**払う（カスケードが構造的に起きない席）
    "p3nl":        {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_NOPLAN_LAST": True},
    "p3nl_shs":    {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_NOPLAN_LAST": True, "B100_SELF_HARM": "strict"},
    "p3nl_s2_shs": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_NOPLAN_LAST": True, "B100_SELF_HARM": "strict",
                    "B100_MAX_SEATS": 2},
    "p3nl_s3_shs": {"B100_MIX": True, "B100_REQUIRE_PLAN": False,
                    "B100_NOPLAN_LAST": True, "B100_SELF_HARM": "strict",
                    "B100_MAX_SEATS": 3},
    # ------------------------------------------------------------------
    # ★B-112（Phase 4・2026-07-30）＝**資格ゲート（θ／供給候補数）**の掃引。
    #   Phase 3 のファネル実測で「門 E/F を緩めても D（発火資格を得た席）が
    #   3日級36・5日級63 しかない」＝**面積を決めているのは資格ゲート**と分かった。
    #   ★`default`（＝main のクラス既定＝p3nl_s2_shs）から**1項目だけ**動かす形で書く。
    # ------------------------------------------------------------------
    "p4t09":   {"B100_MIX": True, "B100_THETA": 0.9},
    "p4t08":   {"B100_MIX": True, "B100_THETA": 0.8},
    "p4t067":  {"B100_MIX": True, "B100_THETA": 0.67},
    "p4t05":   {"B100_MIX": True, "B100_THETA": 0.5},
    "p4t034":  {"B100_MIX": True, "B100_THETA": 0.34},
    "p4all":   {"B100_MIX": True, "B100_FORCE_GATE": "all"},
    #: ★第2軸＝供給候補≤2（ユーザー裁定 Phase 0 §10-1）。θとは独立の発火経路。
    "p4sup":   {"B100_MIX": True, "B100_SUPPLY_GATE": True},
    #: 2軸の積み上げ（1項目ずつの結果を見てから積む）
    "p4t067_sup": {"B100_MIX": True, "B100_THETA": 0.67, "B100_SUPPLY_GATE": True},
    "p4t08_sup":  {"B100_MIX": True, "B100_THETA": 0.8, "B100_SUPPLY_GATE": True},
    "p4t05_sup":  {"B100_MIX": True, "B100_THETA": 0.5, "B100_SUPPLY_GATE": True},
    #: 供給候補の上限を 1（＝完全特定に近い）に絞った対照
    "p4sup1":  {"B100_MIX": True, "B100_SUPPLY_GATE": True, "B100_SUPPLY_MAX": 1},
    #: θを下げたうえで席を増やす／NOPLAN_LAST を外す（退行の機序の切り分け用）
    "p4t067_s3":    {"B100_MIX": True, "B100_THETA": 0.67, "B100_MAX_SEATS": 3},
    "p4t067_nonl":  {"B100_MIX": True, "B100_THETA": 0.67,
                     "B100_NOPLAN_LAST": False},
    "p4t067_nosh":  {"B100_MIX": True, "B100_THETA": 0.67,
                     "B100_SELF_HARM": False},
    # ------------------------------------------------------------------
    # ★DP-6（2026-07-31）＝B-112 の是正フラグ（`B100_REPAIR_BOARD`/`B100_REPAIR_PROB`）は
    #   **退役**（本体 `defense_plan._threat_board_defeat` をカウンタ収支に修正済み＝
    #   写しの是正は不要）。旧 `p4rep*`/`p4repp*` config は削除した（B-112 の測定値の
    #   再現には旧コミット ffbc23a を使うこと）。
    #   代わりに ablation（旧挙動へ戻す）＝ `dp6_legacy`（module フラグ）を置く。
    # ------------------------------------------------------------------
    "dp6_legacy":   {"B100_MIX": True, "DP6_SUPPLY_LEDGER": False},
    #: ★設計3の短絡を「資格つき制約を折っている席だけ守る」に絞る（G_lost_other の回収）
    "p4mg":         {"B100_MIX": True, "B100_MATCH_GATED": True},
    "p4mg_t09":     {"B100_MIX": True, "B100_MATCH_GATED": True,
                     "B100_THETA": 0.9},
    "p4mg_t067":    {"B100_MIX": True, "B100_MATCH_GATED": True,
                     "B100_THETA": 0.67},
    # ------------------------------------------------------------------
    # ★B-112 の検死から出た述語＝**θ を下げた帯では席の経済のガードが要る**。
    #   実測（`random_FS` s11 3日級・θ=0.9 で 2→3）：B-100 が L2D1 に
    #   `sk_setup`(p=0.90) を折るため **`不安-1→お嬢様`（点数33.0）を 1.0点の移動で押し出し**、
    #   そのループは **主人公の死亡**で終わった＝冷却が止めていた事件が通った。
    #   ＝Phase 1 が作った2本のガード（`B100_MAX_DISPLACED` / `B100_PROTECT_CLASSES`）は
    #     **Phase 2 の joint 経路では既定 OFF**（`B100_JOINT_GUARDS=False`）のままだった。
    # ------------------------------------------------------------------
    "p4t09_g":     {"B100_MIX": True, "B100_THETA": 0.9,
                    "B100_JOINT_GUARDS": True},
    "p4t08_g":     {"B100_MIX": True, "B100_THETA": 0.8,
                    "B100_JOINT_GUARDS": True},
    "p4t067_g":    {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_JOINT_GUARDS": True},
    "p4t05_g":     {"B100_MIX": True, "B100_THETA": 0.5,
                    "B100_JOINT_GUARDS": True},
    #: ガードの2本を分離（CF帰属）＝押し出し点数の上限だけ／保護クラスだけ
    "p4t067_gd":   {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_JOINT_GUARDS": True, "B100_PROTECT_CLASSES": ()},
    "p4t067_gp":   {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": None},
    #: トレッドミル対策（θ<1.0 の帯は sk_setup が 44/51 を占める＝毎ターン作り直される仕込み）
    "p4t067_lim1": {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_MAX_PER_LOOP": 1},
    "p4t067_sl1":  {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_MAX_SEATS_PER_LOOP": 1},
    "p4t067_g_lim1": {"B100_MIX": True, "B100_THETA": 0.67,
                      "B100_JOINT_GUARDS": True, "B100_MAX_PER_LOOP": 1},
    #: ★総合候補＝θ0.67＋ガード（DP-6：是正フラグは退役＝本体修正が常時有効）
    "p4best":      {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_JOINT_GUARDS": True},
    # ------------------------------------------------------------------
    # ★B-112 の本命候補＝**保護クラスのガードだけ**を joint 経路で有効にする
    #   （`B100_MAX_DISPLACED` は外す＝面積を潰しすぎるため。3日級の実測＝
    #    両方入れると介入 7回＝main の 13回より**減る**／保護クラスだけなら 36回）。
    #   機序＝§6-2 の退行は「**冷却（不安-1）を押し出した**」＝保護クラスがちょうど効く。
    # ------------------------------------------------------------------
    "p4t09_gp":    {"B100_MIX": True, "B100_THETA": 0.9,
                    "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": None},
    "p4t08_gp":    {"B100_MIX": True, "B100_THETA": 0.8,
                    "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": None},
    "p4t05_gp":    {"B100_MIX": True, "B100_THETA": 0.5,
                    "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": None},
    #: ★本命＝保護クラスのガードだけ（DP-6：是正フラグは退役＝本体修正が常時有効）
    "p4best2":     {"B100_MIX": True, "B100_THETA": 0.67,
                    "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": None},
    # ------------------------------------------------------------------
    # ★B-114（2026-07-31）＝θ 再掃引（新風景＝DP-6 収支化＋B-113 噂の残弾の上）。
    #   B-112 の掃引は旧・脅威モデル（負け筋の46〜56%を誤って防御不能扱い）上の測定＝無効。
    #   格子＝θ ∈ {1.0, 0.9, 0.8, 0.67} × {ガード無し, 保護クラスのみ}。
    #   欠けていた1点（θ=1.0×保護クラスのみ）だけ追加（他は既存 config を再利用）。
    # ------------------------------------------------------------------
    "p4gp":        {"B100_MIX": True,
                    "B100_JOINT_GUARDS": True, "B100_MAX_DISPLACED": None},
    #: ★B-114 対照＝θ=0.9×NL解除（ユーザー脚本の段D=4 は全て NOPLAN_LAST で落ちる＝
    #  B-113 §5-2。緑枠が実戦に出るかの直接の答えを実対局で見る）
    "p4t09_nonl":  {"B100_MIX": True, "B100_THETA": 0.9,
                    "B100_NOPLAN_LAST": False},
    # ------------------------------------------------------------------
    # ★B-116（2026-07-31）＝同一負け筋×ループ横断の発火上限（s10型トレッドミルの恒久対策）。
    #   B-114 §6-1 の検死＝θ=1.0 時代の 3日級 `random_FS` s10 は kp_sk への発火が
    #   L2〜L8 の毎ループ続き（計14回・103点を5.5点で押し出し）敗局＝θ=0.9 は対症療法で
    #   構造は残る（現行でも 5日級 s4 が同型＝L5〜L8 の4ループ連続・gap99・敗局）。
    #   `B100_LINE_CAP`＝同じ負け筋ラベルが**過去の（＝敗北で終わった）ループ K 個**で
    #   発火済みなら、そのラベルの発火資格を止める（ループ内上限 `B100_MAX_PER_LOOP` の
    #   ループ横断版・既定 None＝OFF＝挙動 bit 不変）。
    # ------------------------------------------------------------------
    #: θ=1.0 の対照（旧main＝B-114 §6-1 の有害トレッドミルを再現する陽性対照）
    "p4t10":       {"B100_MIX": True, "B100_THETA": 1.0},
    #: 上限機構（クラス既定 θ=0.9 の上に cap だけ）
    "lc2":         {"B100_MIX": True, "B100_LINE_CAP": 2},
    "lc3":         {"B100_MIX": True, "B100_LINE_CAP": 3},
    #: ★検証＝θ=1.0（構造欠陥が顕在化する世界）に cap を被せると s10 型が消えるか
    "p4t10_lc2":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_LINE_CAP": 2},
    "p4t10_lc3":   {"B100_MIX": True, "B100_THETA": 1.0, "B100_LINE_CAP": 3},
}

#: ★Phase 1 の config は Phase 2 のクラス既定（JOINT/REQUIRE_PLAN=True）を**明示的に打ち消す**
#  ＝Phase 1 の数値の再現性を守る（クラス既定が変わっても config の意味が動かない）。
_PHASE1_CONFIGS = ("p1raw", "p1rawany", "theta1", "theta1any", "iron10", "iron067",
                   "iron05", "cap1", "cap1lim2", "cap1lim1", "lim1",
                   "g40last", "g60last", "g40any", "g60any", "g80any",
                   "g40lastF", "g60anyF", "g60anyFU", "g80anyFU")
for _n in _PHASE1_CONFIGS:
    CONFIGS[_n].setdefault("B100_JOINT", False)
    CONFIGS[_n].setdefault("B100_REQUIRE_PLAN", False)

_ATTRS = ("B100_MIX", "B100_THETA", "B100_IRON_PROB", "B100_MAX_SEATS",
          "B100_MAX_SEATS_2X100", "B100_SEAT_POLICY", "B100_MAX_PER_LOOP",
          "B100_MAX_DISPLACED", "B100_PROTECT_CLASSES",
          # ★Phase 2
          "B100_JOINT", "B100_FORCE_GATE", "B100_SPARE_LAST",
          "B100_CONSUME_PLAN", "B100_JOINT_GUARDS", "B100_MAX_SEATS_PER_LOOP",
          "B100_REQUIRE_PLAN",
          # ★Phase 3（クラスに既定が無い＝getattr の既定 False で読む属性）
          "B100_MAKE_PLAN", "B100_SELF_HARM", "B100_NOPLAN_LAST",
          # ★Phase 4（B-112）＝資格ゲートの第2軸（REPAIR_* は DP-6 で退役）
          "B100_SUPPLY_GATE", "B100_SUPPLY_MAX", "B100_MATCH_GATED",
          # ★B-116＝同一負け筋×ループ横断の発火上限（クラス既定 None＝OFF）
          "B100_LINE_CAP")


def _apply(cfg: dict) -> dict:
    """クラス属性を上書きして、元の値を返す（復元用）。"""
    # ★Phase 3：クラスに既定が無い属性（`B100_MAKE_PLAN`）は False を既定として退避する
    #   ＝実装側は `getattr(agent, "B100_MAKE_PLAN", False)` で読む＝既定 OFF・挙動 bit 不変。
    import agents.defense_plan as _dp
    old = {k: getattr(HeuristicProtagonist, k, False) for k in _ATTRS}
    # ★DP-6 ablation：module フラグ（旧 truly_unstoppable へ戻す）。クラス属性ではない。
    old["DP6_SUPPLY_LEDGER"] = _dp.DP6_SUPPLY_LEDGER
    for k, v in cfg.items():
        if k == "DP6_SUPPLY_LEDGER":
            _dp.DP6_SUPPLY_LEDGER = v
        else:
            setattr(HeuristicProtagonist, k, v)
    return old


def play(script, seed: int, loops: int = 8) -> tuple[int, str, list]:
    """1局を回して (loops_to_win, 結末, 介入ログ) を返す（`arena.benchmark` と同じ数え方）。"""
    probe = replace(script, loops=loops)
    mm = HeuristicMastermind(seed)
    hp = HeuristicProtagonist(seed)
    state, _log = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    fb = any(e.get("event") == "final_battle" for e in state.history)
    if state.winner == "protagonist" and not fb:
        return state.loop_no, "defense", list(hp._b100_log)
    if fb:
        return loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss"), \
            list(hp._b100_log)
    return loops + 1, "loss", list(hp._b100_log)


def run_config(name: str, days: int, loops: int = 8, limit=None,
               verbose: bool = True, scripts: str = "bench") -> dict:
    from arena.b100_funnel import script_set
    old = _apply(CONFIGS[name])
    t0 = time.time()
    rows = []
    try:
        scripts = script_set(days, scripts)
        if limit:
            scripts = scripts[:limit]
        for gname, seed, sc in scripts:
            ltw, outcome, ilog = play(sc, seed, loops=loops)
            rows.append({"game": gname, "seed": seed, "ltw": ltw,
                         "outcome": outcome, "interventions": ilog})
            if verbose:
                mark = f" B100×{len(ilog)}" if ilog else ""
                print(f"  [{name}] {gname} s{seed}: {ltw} {outcome}{mark}",
                      file=sys.stderr, flush=True)
    finally:
        _apply(old)
    vals = [r["ltw"] for r in rows]
    return {
        "config": name, "params": CONFIGS[name], "days": days,
        "n_games": len(rows),
        "defense": sum(1 for r in rows if r["outcome"] == "defense"),
        "mean": round(sum(vals) / len(vals), 3) if vals else None,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "dist": {str(k): v for k, v in sorted(Counter(vals).items())},
        "l1": sum(1 for r in rows if r["ltw"] == 1),
        "n_intervened_games": sum(1 for r in rows if r["interventions"]),
        "n_interventions": sum(len(r["interventions"]) for r in rows),
        "elapsed": round(time.time() - t0, 1),
        "rows": rows,
    }


def compare(base: dict, var: dict) -> dict:
    """per-game 差分（flip）。ltw が小さいほど良い＝改善は負の差。"""
    bmap = {(r["game"], r["seed"]): r for r in base["rows"]}
    imp, reg = [], []
    for r in var["rows"]:
        b = bmap.get((r["game"], r["seed"]))
        if b is None or b["ltw"] == r["ltw"]:
            continue
        item = f"{r['game']} s{r['seed']} {b['ltw']}→{r['ltw']}" \
               f"（介入{len(r['interventions'])}回）"
        (imp if r["ltw"] < b["ltw"] else reg).append(item)
    return {"improved": imp, "regressed": reg}


def format_report(reps: list[dict]) -> str:
    base = reps[0]
    L = [f"B-100 シャドー実行（基準＝先頭config）: {base['days']}日級 {base['n_games']}局"
         f" ／PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}",
         "",
         "  config     | 防衛 | 平均  | L1 | 介入局 | 介入回 | 結末",
         "  -----------|------|-------|----|--------|--------|------"]
    for r in reps:
        L.append(f"  {r['config']:<10} | {r['defense']:>4} | {r['mean']:>5} |"
                 f" {r['l1']:>2} | {r['n_intervened_games']:>6} |"
                 f" {r['n_interventions']:>6} | {r['outcomes']}")
    for r in reps[1:]:
        d = compare(base, r)
        L += ["", f"  [{r['config']}] vs {base['config']}:",
              f"    改善 {len(d['improved'])}件: {d['improved'] or '—'}",
              f"    退行 {len(d['regressed'])}件: {d['regressed'] or '—'}"]
        kinds = Counter(i["kind"] for row in r["rows"] for i in row["interventions"])
        cards = Counter(i["card"] for row in r["rows"] for i in row["interventions"])
        why = Counter(i["reason"].split("／")[0].split("（")[0]
                      for row in r["rows"] for i in row["interventions"])
        L += [f"    介入の脅威種別: {dict(kinds.most_common())}",
              f"    介入で打った札: {dict(cards.most_common())}",
              f"    発火経路: {dict(why.most_common())}"]
        games = sorted({(row["game"], row["seed"]) for row in r["rows"]
                        if row["interventions"]})
        L.append(f"    介入した局: {[f'{g} s{s}' for g, s in games]}")
    L.append(f"  分布: " + " ".join(f"[{r['config']}]{r['dist']}" for r in reps))
    L.append(f"  所要 {sum(r['elapsed'] for r in reps)}秒")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-100 Phase 1：混合AIのシャドー実行")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--configs", type=str,
                    default="off,theta1,theta1any,iron10,iron067,iron05")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--scripts", type=str, default="bench",
                    choices=["bench", "user"],
                    help="bench＝標準130/70局／user＝ユーザー実戦の脚本（B-112）")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED=0 で実行してください。", file=sys.stderr)
    names = [n.strip() for n in args.configs.split(",") if n.strip()]
    for n in names:
        if n not in CONFIGS:
            raise SystemExit(f"未知の config: {n}（候補={list(CONFIGS)}）")
    reps = [run_config(n, args.days, loops=args.loops, limit=args.limit,
                       verbose=not args.quiet, scripts=args.scripts)
            for n in names]
    print(format_report(reps))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(reps, f, ensure_ascii=False, indent=1)
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
