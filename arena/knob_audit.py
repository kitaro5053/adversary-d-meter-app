# -*- coding: utf-8 -*-
"""切替口テーブルの健全性検査（★測定道具のみ・AI の挙動には一切触れない）。

## なぜ要るか（B-247 の測定事故＝§72-45）

A/B ドライバの多くは「条件表（`KNOBS`）の全項目を毎回書き込む」型で、

    for k, (where, attr) in KNOBS.items():
        setattr(holders[where], attr, k in on)

と書く。この形は**条件名に現れない切替口を必ず False にする**ため、
**リポジトリ既定が True の切替口を条件表に載せると、`off`（ベースライン）を含む
全条件がその切替口を切った盤面になる**。

★最も重いのは「**自前の内部整合チェックでは検出できない**」こと。全条件が同じ汚染を
受けるので `off@2`（同一バッチ末尾の再測）も per-game 完全一致してしまう。
B-247 が気づけたのは「B-243 と数値が合わない」という**外部の答え合わせ**があったから。

## 本モジュールが提供する不変条件（一般形）

★**ベースライン条件のフラグ束は、リポジトリ既定のフラグ束と一致しなければならない。**

B-247 が個別に足した `_check_defaults()`（＝「条件表の切替口は既定 False」）は、
ベースラインが `off`（＝何も ON にしない条件）の場合の特殊形にあたる。
`base`＝現行既定の4つ ON、のような条件を持つドライバ（`b237_ab`／`b240_diff`／`b241_diff`）
にも同じ検査が当たるよう、**ベースラインで ON にする切替口の集合を明示して**検査する。

## 使い方

    from arena import knob_audit
    knob_audit.check_baseline(KNOBS, _holders(), baseline=())        # off がベースライン
    knob_audit.check_baseline(KNOBS, _holders(), baseline=BASE)      # base がベースライン
    knob_audit.banner(KNOBS, _holders(), baseline=())                # 落とさず警告だけ

`check_baseline` は不一致で `SystemExit` を投げる。**過去の測定を再現したい**等で
意図的に食い違わせたい場合だけ、環境変数 `KNOB_AUDIT_ALLOW_STALE=1` で警告に落とせる。

## 静的スキャン（★将来のドライバを守る）

    PYTHONIOENCODING=utf-8 python -m arena.knob_audit scan

`arena/*.py` の条件表を静的に読み、**現在の既定が True の切替口を条件表に載せている**
ドライバを列挙する（同じモジュール内の `BASE`／`BASELINE` タプルは「ベースラインで ON」
とみなして差し引く）。`tests/test_knob_audit.py` が本一覧を回帰で固定している。
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

#: 切替口の既定値が定義されうるモジュール（先に見つかった方を採用）
HOLDER_MODULES = (
    "agents.heuristic_protagonist",
    "agents.belief",
    "agents.defense_plan",
    "agents.heuristic",
    "agents.b100_mix",
    "sim.loop_race",
)

_ENV_ALLOW = "KNOB_AUDIT_ALLOW_STALE"


# ------------------------------------------------------------------ 実行時検査
def _normalize(knobs) -> dict:
    """条件表を {条件名: (置き場キー|None, 属性名)} に正規化する。

    - `{"bx": ("bl", "B243_BOARD_X_ROLE")}`（置き場つき）
    - `{"bx": "B243_BOARD_X_ROLE"}`（単一置き場）
    のどちらも受ける。
    """
    out = {}
    for k, v in knobs.items():
        if isinstance(v, str):
            out[k] = (None, v)
        elif isinstance(v, (tuple, list)) and len(v) == 2:
            out[k] = (v[0], v[1])
        else:
            raise ValueError(f"条件表の値の形が不明: {k!r} -> {v!r}")
    return out


def _get(holders, where, attr):
    obj = holders if where is None else holders[where]
    if not hasattr(obj, attr):
        raise SystemExit(f"★条件表に未定義の切替口: {attr}")
    return getattr(obj, attr)


def baseline_delta(knobs, holders, baseline=()) -> list[tuple[str, str, bool, bool]]:
    """ベースライン条件とリポジトリ既定の差分を返す。

    戻り値＝ `[(条件名, 属性名, ベースラインでの値, リポジトリ既定値), ...]`。
    空リスト＝一致（健全）。
    """
    table = _normalize(knobs)
    on = set(baseline)
    unknown = on - set(table)
    if unknown:
        raise SystemExit(f"★baseline に条件表に無い名前がある: {sorted(unknown)}")
    bad = []
    for k, (where, attr) in table.items():
        want = k in on          # ベースライン条件がその切替口に書き込む値
        cur = _get(holders, where, attr)
        if bool(cur) is not want or not isinstance(cur, bool):
            bad.append((k, attr, want, cur))
    return bad


def _message(bad, driver: str) -> str:
    head = (f"★条件表の健全性検査に失敗（{driver or 'このドライバ'}）＝"
            "**ベースライン条件がリポジトリ既定と一致しない**。")
    body = "\n".join(
        f"    - {k}（{attr}）: ベースラインでは {want} ／ リポジトリ既定は {cur!r}"
        for k, attr, want, cur in bad)
    tail = ("\n  → 条件表の全項目は毎回書き込まれるため、このままだと **`off` を含む全条件**が\n"
            "     正典と別の盤面になる（B-247 の測定事故＝§72-45）。\n"
            "  → 対処：(a) ベースラインで ON にする切替口を `baseline=` に入れる、"
            "(b) 既定 ON の切替口を条件表から外す、\n"
            f"     のどちらか。過去の測定を再現する目的なら {_ENV_ALLOW}=1 で警告に落とせる。")
    return f"{head}\n{body}{tail}"


def check_baseline(knobs, holders, baseline=(), *, driver: str = "") -> None:
    """★不変条件＝ベースライン条件のフラグ束 == リポジトリ既定。違反で `SystemExit`。"""
    bad = baseline_delta(knobs, holders, baseline)
    if not bad:
        return
    msg = _message(bad, driver)
    if os.environ.get(_ENV_ALLOW) == "1":
        print(f"[knob_audit] ★警告（{_ENV_ALLOW}=1 のため続行）\n{msg}", file=sys.stderr,
              flush=True)
        return
    raise SystemExit(msg)


def banner(knobs, holders, baseline=(), *, driver: str = "") -> bool:
    """検査結果を1行で表示する（落とさない）。健全なら True。"""
    bad = baseline_delta(knobs, holders, baseline)
    if not bad:
        print(f"[knob_audit] ベースライン条件＝リポジトリ既定と一致 ✅"
              f"（{driver or '条件表'}・切替口 {len(_normalize(knobs))} 個）", flush=True)
        return True
    print(f"[knob_audit] {_message(bad, driver)}", flush=True)
    return False


# ------------------------------------------------------------------ 静的スキャン
def _flag_like(name) -> bool:
    return isinstance(name, str) and name[:1] == "B" and name[1:2].isdigit()


def _tables_of(tree: ast.AST) -> tuple[dict[str, dict[str, str]], set[str]]:
    """モジュールの (条件表 {表名: {条件名: 属性名}}, BASE 系に載る条件名) を返す。"""
    tables: dict[str, dict[str, str]] = {}
    base: set[str] = set()
    for node in ast.walk(tree):
        # ★`KNOBS: dict[...] = {...}` の注釈つき代入（AnnAssign）も拾う
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            tgt, val = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt, val = node.targets[0], node.value
        else:
            continue
        if not isinstance(tgt, ast.Name):
            continue
        if isinstance(val, ast.Dict):
            got = {}
            for k, v in zip(val.keys, val.values):
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    continue
                if isinstance(v, ast.Tuple) and len(v.elts) == 2 \
                        and isinstance(v.elts[1], ast.Constant) \
                        and _flag_like(v.elts[1].value):
                    got[k.value] = v.elts[1].value
                elif isinstance(v, ast.Constant) and _flag_like(v.value):
                    got[k.value] = v.value
            if got:
                tables[tgt.id] = got
        elif tgt.id in ("BASE", "BASELINE") and isinstance(val, (ast.Tuple, ast.List)):
            for e in val.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    base.add(e.value)
    return tables, base


def _defaults() -> dict[str, bool]:
    """現在のリポジトリ既定（bool の切替口のみ）。"""
    import importlib
    out: dict[str, bool] = {}
    for mod in HOLDER_MODULES:
        m = importlib.import_module(mod)
        holders = [m]
        hp = getattr(m, "HeuristicProtagonist", None)
        if hp is not None:
            holders.append(hp)
        hm = getattr(m, "HeuristicMastermind", None)
        if hm is not None:
            holders.append(hm)
        for h in holders:
            for k in dir(h):
                if _flag_like(k) and isinstance(getattr(h, k, None), bool):
                    out.setdefault(k, getattr(h, k))
    return out


def scan(root: str | Path | None = None) -> list[dict]:
    """`arena/*.py` の条件表を静的に読み、**既定 ON の切替口を載せている**表を列挙する。

    戻り値＝ `[{"file":…, "table":…, "knobs": [(条件名, 属性名), …]}, …]`（ファイル名順）。
    同一モジュールの `BASE`／`BASELINE` に載る条件名は「ベースラインで ON」とみなして除く。
    """
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    defaults = _defaults()
    found: list[dict] = []
    for p in sorted((root / "arena").glob("*.py")):
        if p.name == "knob_audit.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        tables, base = _tables_of(tree)
        for tname, tab in sorted(tables.items()):
            hits = [(k, a) for k, a in sorted(tab.items())
                    if defaults.get(a) is True and k not in base]
            if hits:
                found.append({"file": p.name, "table": tname, "knobs": hits})
    return found


def scan_assignments(root: str | Path | None = None) -> list[dict]:
    """条件表を持たず**直接代入**で切替口を書くドライバの一覧（★参考）。

    `HP.B230_X = ...` / `setattr(obj, "B230_X", ...)` / `f(B230_X=...)` の形で
    **現在の既定が True** の切替口に書き込むファイルを挙げる。
    ★ベースライン条件が何かは静的には決まらない＝**自動判定はできない**。
    「見落としが無いこと」を人が確かめるための網羅リストとして使う。
    """
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    defaults = _defaults()
    tabled = {r["file"] for r in scan(root)}
    out: list[dict] = []
    for p in sorted((root / "arena").glob("*.py")):
        if p.name == "knob_audit.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        hits: set[str] = set()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute):
                        names.append(t.attr)
                    elif isinstance(t, ast.Tuple):
                        names += [e.attr for e in t.elts if isinstance(e, ast.Attribute)]
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == "setattr" and len(node.args) >= 2:
                    a = node.args[1]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        names.append(a.value)
                names += [kw.arg for kw in node.keywords if kw.arg]
            hits |= {n for n in names if defaults.get(n) is True}
        if hits:
            out.append({"file": p.name, "knobs": sorted(hits),
                        "has_table": p.name in tabled})
    return out


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] != "scan":
        print("usage: python -m arena.knob_audit scan")
        return 2
    rows = scan()
    if not rows:
        print("[knob_audit] 既定 ON の切替口を条件表に載せているドライバは無い ✅")
        return 0
    print("[knob_audit] ★既定 ON の切替口を条件表に載せているドライバ"
          "（＝ベースライン条件がその切替口を切る型）")
    for r in rows:
        ks = ", ".join(f"{k}→{a}" for k, a in r["knobs"])
        print(f"  {r['file']} / {r['table']}: {ks}")
    print(f"  合計 {len(rows)} 表。★これ自体は必ずしも事故ではない"
          "（その切替口を切ること自体が実験の主題なら正しい）。"
          "\n  ★事故になるのは「ベースラインを正典と同じと称して報告する」場合。")
    asg = [r for r in scan_assignments() if not r["has_table"]]
    if asg:
        print("\n[knob_audit] ★参考＝条件表を持たず直接代入で既定 ON の切替口に書くドライバ"
              "（ベースラインの正しさは静的には判定できない＝人が読むこと）")
        for r in asg:
            print(f"  {r['file']}: {', '.join(r['knobs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
