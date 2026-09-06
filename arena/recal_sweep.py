# -*- coding: utf-8 -*-
"""B-107b 計測：置換 `h5` の上で PRIORITY 掃引を **10定数×2周** に広げる（B-107 追加測定(A)）。

問い（`docs/監査_B107_列挙順への過適合の分離_2026-07-30.md` §7 の引き継ぎ）：
B-107 では置換 `h5` が4定数・1周の掃引で合算 +2局（ギャップの11%）しか戻らなかった。
これが (a) 探索が弱かっただけ（＝解釈B＝列挙順への過適合で完結）なのか、
(b) 列挙順そのものが定数で補償できない探索資源（＝第3の結論）なのかを分離する。

方法＝B-107 と同じ**貪欲な座標降下**（`arena/tie_noise.run_recal` と同一の目的関数
(防衛数↑, 平均L↓)）を、10定数・各5点・`h5` は2周／対照 `id` は1周で回す。
チェーンはベンチ別（3日級130局／5日級70局）＝B-107 §3c と同じ集計形。

★本モジュールは**計測専用のスタンドアロンCLI**（本番経路・既定挙動には一切影響しない）。
PRIORITY の一時上書きと復元・置換の装着と解除は `arena.tie_noise` の実装
（`_bench_point`＝`finally` で必ず復元）をそのまま使う。

★長時間測定のための**再開可能設計**：測定済みの点は state JSON の cache
（キー＝置換×日数×「既定値と異なる上書きだけ」の正規形）に保存し、
毎回の起動で座標降下を**cache の上で決定的に再生**する。cache に無い点に当たったら
実測して cache に追記保存（1点ごとに書き出し＝途中死亡しても成果が残る）。
測定は決定的（PYTHONHASHSEED=0・`id@dup` で per-game 一致を実証済み＝B-107 §2a）なので
再生はプロセスを跨いでも同一の降下経路をたどる。

使い方：
    # 初期化（計画を state に書く）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.recal_sweep init \
        --state /path/to/b107b_state.json
    # 実測（時間予算内で回して自動保存終了・繰り返し呼ぶ）
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.recal_sweep run \
        --state /path/to/b107b_state.json --budget 420
    # 進捗と結果表
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.recal_sweep status \
        --state /path/to/b107b_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from agents.heuristic_protagonist import PRIORITY

# ---------------------------------------------------------------------------
# 掃引計画（B-107b）＝10定数の選定は B-107 §3a の同点帯全数集計に従う（選定表は監査doc）。
# 先頭4つは B-107 の4定数（グリッドも B-107 §3b と同一＝比較可能性のため）。
# 残り6つは B-107 §3a の同点帯表の残る全行（72.00/58.00 帯は同値の定数が2本ずつ＝両方振る。
# 70.00 は B-107 §7-4 が「掃引していない＝5日級の回復率は過小評価の可能性」と明示した定数）。
# ---------------------------------------------------------------------------
B107B_KEYS: list[tuple[str, list[float]]] = [
    # --- B-107 の4定数（グリッド同一） ---
    ("冷却役投資+2",          [76.5, 79.0, 81.0, 83.5, 85.5]),
    ("SKテスト",              [70.5, 72.5, 75.0, 77.5, 79.5]),
    ("カルティスト剥がし_候補", [67.5, 69.5, 72.0, 74.5, 76.5]),
    ("VIP注入_実証済",        [99.5, 101.5, 103.0, 104.5, 106.5]),
    # --- 追加6定数（B-107 §3a 同点帯表の残る全行） ---
    ("SK配達ピン",            [67.5, 69.5, 72.0, 74.5, 76.5]),
    ("危険犯人_ML分離_kill",  [65.5, 67.5, 70.0, 72.5, 74.5]),
    ("冷却役同行",            [72.5, 74.5, 77.0, 79.5, 81.5]),
    ("SK配達ピン_弱",         [53.5, 55.5, 58.0, 60.5, 62.5]),
    ("TTテスト",              [53.5, 55.5, 58.0, 60.5, 62.5]),
    ("定石_準備移動",         [30.5, 32.5, 35.0, 37.5, 39.5]),
]

# チェーン＝B-107 §3c と同じ「置換×ベンチ」。h5 は2周・対照 id は1周。
B107B_CHAINS: list[dict] = [
    {"name": "h5-3day",  "perm": "h5", "days": 3, "rounds": 2},
    {"name": "h5-5day",  "perm": "h5", "days": 5, "rounds": 2},
    {"name": "id-3day",  "perm": "id", "days": 3, "rounds": 1},
    {"name": "id-5day",  "perm": "id", "days": 5, "rounds": 1},
]

_DEFAULTS: dict[str, float] = {k: float(PRIORITY[k]) for k, _ in B107B_KEYS}


# ---------------------------------------------------------------------------
# cache キー＝「既定値と異なる上書きだけ」の正規形（決定的測定なので同キー＝同結果）
# ---------------------------------------------------------------------------
def effective_overrides(overrides: dict[str, float]) -> dict[str, float]:
    """既定値と同じ上書きを落とす（＝実挙動が変わる差分だけ残す）。"""
    return {k: float(v) for k, v in overrides.items()
            if abs(float(v) - _DEFAULTS.get(k, float("nan"))) > 1e-12}


def cache_key(perm: str, days: int, overrides: dict[str, float]) -> str:
    eff = effective_overrides(overrides)
    body = ";".join(f"{k}={eff[k]:g}" for k in sorted(eff))
    return f"{perm}|{days}|{body}"


# ---------------------------------------------------------------------------
# state I/O
# ---------------------------------------------------------------------------
def load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, st: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def init_state(path: str) -> dict:
    st = {
        "plan": "b107b",
        "loops": 8,
        "keys": [[k, vs] for k, vs in B107B_KEYS],
        "chains": B107B_CHAINS,
        "defaults": _DEFAULTS,
        "cache": {},
    }
    save_state(path, st)
    return st


# ---------------------------------------------------------------------------
# 座標降下の決定的な再生（cache の上を歩き、未測定の点で止まる）
# ---------------------------------------------------------------------------
class BudgetExceeded(Exception):
    pass


def _measure(st: dict, path: str, perm: str, days: int,
             overrides: dict[str, float], deadline: float | None,
             bench=None) -> dict:
    """cache に有ればそれを、無ければ実測して cache に追記保存して返す。"""
    key = cache_key(perm, days, overrides)
    hit = st["cache"].get(key)
    if hit is not None:
        return hit
    if deadline is not None and time.monotonic() > deadline:
        raise BudgetExceeded(key)
    if bench is None:
        from arena.tie_noise import _bench_point
        bench = _bench_point
    t0 = time.monotonic()
    pt = bench(days, st.get("loops", 8), perm, effective_overrides(overrides))
    dt = time.monotonic() - t0
    st["cache"][key] = pt
    save_state(path, st)
    print(f"    [測定 {dt:5.1f}s] {key or '(基準)'} → 防衛={pt['defense']} "
          f"平均={pt['mean']:.3f}", flush=True)
    return pt


def replay_chain(st: dict, path: str, chain: dict,
                 deadline: float | None = None, bench=None,
                 verbose: bool = True) -> dict:
    """1チェーンの貪欲座標降下を cache の上で再生する（未測定点に当たると測定）。

    目的関数＝B-107 `run_recal` と同一：(防衛数↑, 平均L↓)。
    戻り値＝{"base", "final", "cur", "history", "done"}。
    """
    perm, days, rounds = chain["perm"], chain["days"], chain["rounds"]
    keys = [(k, vs) for k, vs in st["keys"]]
    cur: dict[str, float] = {}
    history: list[dict] = []
    base = _measure(st, path, perm, days, {}, deadline, bench)
    for rd in range(1, rounds + 1):
        for key, vals in keys:
            best = None
            for v in vals:
                ov = dict(cur)
                ov[key] = v
                pt = _measure(st, path, perm, days, ov, deadline, bench)
                score = (pt["defense"], -pt["mean"])
                if best is None or score > best[0]:
                    best = (score, v, pt)
            cur[key] = best[1]
            history.append({"round": rd, "key": key, "value": best[1],
                            "defense": best[2]["defense"],
                            "mean": best[2]["mean"]})
            if verbose:
                print(f"  [{chain['name']}] 周{rd} {key} := {best[1]:g}"
                      f"（防衛={best[2]['defense']} 平均={best[2]['mean']:.3f}）",
                      flush=True)
    final = _measure(st, path, perm, days, cur, deadline, bench)
    return {"base": base, "final": final, "cur": cur,
            "history": history, "done": True}


def run_all(st: dict, path: str, budget: float | None) -> bool:
    """全チェーンを順に再生。予算切れなら False（未了）。"""
    deadline = (time.monotonic() + budget) if budget else None
    for chain in st["chains"]:
        try:
            res = replay_chain(st, path, chain, deadline)
        except BudgetExceeded as e:
            print(f"⏳ 時間予算切れ（次の未測定点＝{e}）。--state を再実行してください。",
                  flush=True)
            return False
        print(f"★ [{chain['name']}] 基準 防衛={res['base']['defense']} "
              f"平均={res['base']['mean']:.3f} → 再較正後 防衛={res['final']['defense']} "
              f"平均={res['final']['mean']:.3f}　採用={res['cur']}", flush=True)
    return True


# ---------------------------------------------------------------------------
# status＝測定済み cache だけで進捗と結果表を出す（新規測定はしない）
# ---------------------------------------------------------------------------
def _flips(base_rows: dict, rows: dict) -> tuple[int, int]:
    imp = sum(1 for k, x in rows.items() if x < base_rows[k])
    reg = sum(1 for k, x in rows.items() if x > base_rows[k])
    return imp, reg


def status(st: dict, path: str) -> None:
    total = need = 0

    def counting_bench(days, loops, perm, eff):
        raise BudgetExceeded  # 実測させない

    for chain in st["chains"]:
        try:
            res = replay_chain(st, path, chain, deadline=None,
                               bench=counting_bench, verbose=False)
        except BudgetExceeded:
            print(f"[{chain['name']}] 未了（cache {len(st['cache'])} 点測定済み）")
            continue
        b, f = res["base"], res["final"]
        imp, reg = _flips(b["rows"], f["rows"])
        print(f"[{chain['name']}] 基準 {b['defense']}/{b['mean']:.3f} → "
              f"再較正後 {f['defense']}/{f['mean']:.3f} "
              f"(flip 改善{imp} 退行{reg})")
        print(f"  採用値: { {k: v for k, v in res['cur'].items() if abs(v - _DEFAULTS[k]) > 1e-12} }")
    print(f"cache 測定済み点数: {len(st['cache'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="B-107b 掃引ハーネス（純計測・resumable）")
    ap.add_argument("cmd", choices=["init", "run", "status"])
    ap.add_argument("--state", type=str, required=True)
    ap.add_argument("--budget", type=float, default=420.0,
                    help="run: この秒数を超えたら新規測定を始めず保存終了")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") is None:
        print("⚠ PYTHONHASHSEED が未固定です。", file=sys.stderr)
    if args.cmd == "init":
        if os.path.exists(args.state):
            print(f"既存の state があるため上書きしません: {args.state}",
                  file=sys.stderr)
            sys.exit(1)
        init_state(args.state)
        print(f"→ 初期化: {args.state}")
        return
    st = load_state(args.state)
    if args.cmd == "run":
        done = run_all(st, args.state, args.budget)
        print("DONE" if done else "IN_PROGRESS")
    else:
        status(st, args.state)


if __name__ == "__main__":
    main()
