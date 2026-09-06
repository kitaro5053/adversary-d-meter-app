# -*- coding: utf-8 -*-
"""pytest プラグイン（測定専用・リポジトリのファイルを一切変更しない）。

環境変数 `B236_ON` に切替口をカンマ区切りで並べると、**セッション開始時にクラス属性
／モジュールフラグを上書き**して「既定 ON にしたら壊れるテスト」を列挙する。

    B236_ON=b232,b234 python -m pytest tests/ -q -p b236_force_on
"""
import os

KNOBS = {
    "b230": ("hp", "B230_SPLIT_GEOMETRY"),
    "b231": ("bl", "B231_IMOUTO_TRAIT"),
    "b232": ("hp", "B232_COOL_MATH"),
    "b234": ("bl", "B234_GOSHINBOKU_TRAIT"),
}


def pytest_configure(config):
    names = [n.strip() for n in (os.environ.get("B236_ON") or "").split(",") if n.strip()]
    if not names:
        return
    from agents import HeuristicProtagonist as HP
    import agents.belief as bl
    holders = {"hp": HP, "bl": bl}
    for n in names:
        where, attr = KNOBS[n]
        setattr(holders[where], attr, True)
    config._b236_on = names


def pytest_report_header(config):
    names = getattr(config, "_b236_on", None)
    if names:
        return f"[B-236] 切替口を強制 ON: {names}（測定専用・ファイル不変）"
    return None
