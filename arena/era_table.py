# -*- coding: utf-8 -*-
"""B-264：**教材ごとの era 表**（どの棋譜が、どの build・どの既定値の束で録られたか）。

★本モジュールは**読み取り専用**＝`agents/` `sim/` `engine/` `rules/` を1バイトも変更しない。
  git は **`git cat-file` で読むだけ**（checkout / reset は一切しない）。

## なぜ要るか（§72-72／B-264）

教材棋譜（`docs/feedback_logs/*.jsonl`）を現行の AI に打ち直させて「bit 一致」を主張する道具が
複数ある。既定値（切替口）が変わるたびに一致しなくなるので、B-260 が `era_pin`
（＝収録当時の既定へ倒して再生する contextmanager）を実装した。
しかし **era ピンの束は道具ごとに手で決めていた**ので、
「どの棋譜がどの build で録られ、その build の既定値の束は何だったか」を**毎回**調べ直す必要があった。

★本モジュールは**その束を git 履歴から機械的に復元する**。
教材 JSONL の meta 行に `tool_build`（コミットハッシュ）が入っているので、
**教材 → build** は読むだけで分かる。あとは **build 時点の切替口の値**を AST で抜き、
**現行 HEAD の束との差分**を取れば、それが**その教材の era ピンの候補**になる。

## サブコマンド

    python -m arena.era_table inventory                # 教材の棚卸し（build/日時/脚本/勝敗/利用道具）
    python -m arena.era_table consts  --rev HEAD       # ある rev の切替口一覧
    python -m arena.era_table diff    --rev <build>    # HEAD との差分（＝era ピンの素材）
    python -m arena.era_table pins    --log PATH       # その教材の era ピン候補（tier A/B）
    python -m arena.era_table verify  --log PATH [--pins auto|none|A|AB]   # 再生の bit 一致
    python -m arena.era_table search  --log PATH       # 一致を最大化する最小の束を山登りで探す
    python -m arena.era_table crosscheck               # 既存の手動 ERA_PINS と表の突き合わせ
    python -m arena.era_table report                   # docs/教材ごとのera表_2026-08-19.md の本体を印字

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGDIR = REPO / "docs" / "feedback_logs"

#: 切替口が住みうるツリー（★ここを広く取り、**中身は機械的に走査する**＝
#: 新しい切替口が別モジュールに生えても自動で拾う）。
SCAN_ROOTS: tuple[str, ...] = ("agents", "sim")

#: ★走査から**除外する表示専用モジュール**（B-290・2026-09-02・FableA発注）。
#:   era ピンは「**挙動に効く定数**（切替口・重み表）を収録当時の値へ戻して bit 再現する」ための
#:   仕組み。`sim/reference.py` は **UI表示用の prose だけ**を持つ（`CHARACTER_TRAITS`／
#:   `CHARACTER_ATTRIBUTES`／`INCIDENT_EFFECTS`／`ROLE_REFERENCE` 等＝`arena/play.py` の
#:   カードテキスト表示にしか使われない）＝**挙動には一切効かない**。
#:   にもかかわらず ALL_CAPS の literal なので `consts_of_source` が拾い、
#:   **prose を1文字直すたびに `diff_consts` の `changed` が非空になって era 比較が壊れる**
#:   （実害＝B-290 でキャラ特性テキストの誤りを直したら `test_牡丹の…ナイフエッジの対照` が fail。
#:    その時も再生は素 51/51・ピン束 49/51 で**bit 不変**＝典型的な誤検出だった）。
#:   ∴ **モジュール単位で除外する**。★広げないこと＝他モジュールには挙動に効く定数が同居しうる。
#:   ★穴＝将来このモジュールに挙動へ効く定数が置かれると見えなくなる。その番人＝
#:     `tests/test_b264_era_table.py::test_除外モジュールに切替口が生えていない`。
EXCLUDED_MODULES: tuple[str, ...] = ("sim/reference.py",)

#: 切替口の名前の形（例＝`B252_CULT_FLOOR`／`_B252_CULT_FLOOR_P`／`B100_MIX`）。
NAME_RE = re.compile(r"^_?B\d+[A-Z0-9_]*$")

#: ★切替口だけでは足りない＝**定数表そのもの**（`PRIORITY`／`COEFF` など）も既定値の束の一部。
#:   モジュール直下／クラス直下の **ALL_CAPS な literal 代入**をすべて拾う
#:   （bool・数値・文字列・tuple・dict・frozenset…＝`ast.literal_eval` が読める形）。
CONST_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")

#: ★`tool_build` が本リポジトリに無い時に**追加で探しに行くクローン**（読むだけ）。
#:   デプロイ用ミラー `kitaro5053/adversary-d-meter-app` は**別リポジトリ**なので、
#:   そこで録られた棋譜の build は本リポジトリの `git` では解決できない（§72-36）。
#:   ★存在しなければ黙って無視する（**無い環境で落ちない**＝表には「不明」と出る）。
EXTRA_REPOS: tuple[Path, ...] = (Path("/workspace/adversary-d-meter-app"),)

#: ★`tool_build` が本リポジトリに無い時の既知の出所（**推測ではなく doc 由来の事実**）。
#:   出典＝`docs/バックログ_構想メモ_FableA.md` §72-36／§72-38。
KNOWN_FOREIGN_BUILDS: dict[str, str] = {
    "3f055ea": ("デプロイ用ミラー `kitaro5053/adversary-d-meter-app` の HEAD＝v0.23.0"
                "（2026-07-31 19:01 JST 相当）。本リポジトリには**無い**"
                "＝別リポジトリのコミット（§72-36）"),
}


# ---------------------------------------------------------------------------
# git（読むだけ）
# ---------------------------------------------------------------------------
def _git(*args: str, repo: Path | None = None) -> str:
    return subprocess.run(("git", *args), cwd=(repo or REPO), check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def _has(rev: str, repo: Path) -> bool:
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        return False
    r = subprocess.run(("git", "cat-file", "-t", f"{rev}^{{commit}}"), cwd=repo,
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "commit"


def repo_of(rev: str) -> Path | None:
    """その rev を持っているクローンを返す（本リポジトリ→ミラーの順）。無ければ None。"""
    if _has(rev, REPO):
        return REPO
    for extra in EXTRA_REPOS:
        if extra.is_dir() and _has(rev, extra):
            return extra
    return None


def rev_exists(rev: str) -> bool:
    return repo_of(rev) is not None


def rev_info(rev: str) -> str:
    r = repo_of(rev)
    if r is None:
        return "（どのクローンにも無い）"
    tag = "" if r == REPO else f"［{r.name}］"
    return tag + _git("log", "-1", "--format=%h %ad %s",
                      "--date=format:%Y-%m-%d %H:%M", rev, repo=r).strip()


def _ls_py(rev: str, repo: Path) -> list[str]:
    """走査対象の .py（★`EXCLUDED_MODULES`＝表示専用 prose のモジュールは除く）。"""
    out = _git("ls-tree", "-r", "--name-only", rev, "--", *SCAN_ROOTS, repo=repo)
    return [p for p in out.splitlines()
            if p.endswith(".py") and p not in EXCLUDED_MODULES]


def _batch_read(rev: str, paths: list[str], repo: Path) -> dict[str, str]:
    """`git cat-file --batch` で複数ファイルを一度に読む（1ファイル1プロセスを避ける）。"""
    if not paths:
        return {}
    stdin = "".join(f"{rev}:{p}\n" for p in paths)
    proc = subprocess.run(("git", "cat-file", "--batch"), cwd=repo, input=stdin.encode(),
                          capture_output=True, check=True)
    buf, out, i = proc.stdout, {}, 0
    for p in paths:
        nl = buf.index(b"\n", i)
        header = buf[i:nl].decode()
        i = nl + 1
        parts = header.split()
        if len(parts) < 3:            # "<oid> missing"
            continue
        size = int(parts[2])
        out[p] = buf[i:i + size].decode("utf-8", "replace")
        i += size + 1                 # 本体＋末尾の改行
    return out


# ---------------------------------------------------------------------------
# 切替口の抽出（AST）
# ---------------------------------------------------------------------------
def _const(node) -> tuple[bool, object]:
    """`ast.literal_eval` で読める定数なら (True, 値)。それ以外は (False, None)。

    ★`frozenset([...])` だけは literal ではないので手当てする（`agents/` に実在する形）。
    """
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("frozenset", "set") and len(node.args) == 1):
        ok, v = _const(node.args[0])
        return (True, frozenset(v)) if ok and isinstance(v, (list, tuple, set, frozenset)) \
            else (False, None)
    try:
        return True, ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False, None


# ★B-264 修正（2026-08-20・FableA）＝**切替口とキャッシュを分ける**。
#
#   発覚＝12分割ゲートの CHUNK 9 で `test_b264_era_table.py` が3件 fail。
#   ★**単独では通る**＝**順序依存**だった。真因は本表の抽出器が
#   `sim.generator._SCRIPT_CACHE` `sim.loop_solver._CACHE_DATA/_CACHE_FILE/_CODE_HASH`
#   のような**実行時に埋まるキャッシュ**を「切替口」として拾っていたこと。
#   ソース上の値（空／None）と**実行時の値が食い違うのが正常**なので、
#   「ソースから抜いた束 == 実物のモジュール属性」という本表の不変条件が
#   **先に対局を回すテストと同じチャンクに入った回だけ**破れていた。
#
#   ★判別軸＝「空かどうか」ではなく「**モジュール内で書き換えられるか**」。
#   本物の切替口は**定義の1回しか代入されない**。キャッシュは関数の中で
#   再代入されるか、添字代入／破壊的メソッドで中身を変えられる。
#   ∴ 名前の deny-list（手で保守する）ではなく **AST で機械的に**落とす。
_MUTATORS = frozenset((
    "append", "extend", "insert", "add", "update", "clear",
    "setdefault", "pop", "popitem", "remove", "discard", "sort",
))


def mutated_names(tree: ast.AST) -> set:
    """モジュール内で**実行時に書き換えられる**名前（＝切替口ではなく状態）。

    拾うのは3経路：
      (a) 関数／メソッドの中での再代入（`global X` 経由を含む）・`AugAssign`
      (b) 添字代入 `X[k] = v` / `del X[k]`
      (c) 破壊的メソッド呼び出し `X.update(...)` `X.clear()` など
    """
    out: set = set()
    fn_types = (ast.FunctionDef, ast.AsyncFunctionDef)

    def _targets(t):
        if isinstance(t, ast.Name):
            yield ("bind", t.id)
        elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
            yield ("mut", t.value.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                yield from _targets(e)

    for node in ast.walk(tree):
        # (b)(c) は場所を問わず「中身が変わる」ので無条件に拾う
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in tgts:
                for kind, name in _targets(t):
                    if kind == "mut":
                        out.add(name)
        if isinstance(node, ast.Delete):
            for t in node.targets:
                for kind, name in _targets(t):
                    out.add(name)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _MUTATORS \
                and isinstance(node.func.value, ast.Name):
            out.add(node.func.value.id)

    # (a) 関数の中での**再代入**。★ここは Python の意味論どおり厳密に扱う＝
    #     `global X` を宣言した関数の中での再代入**だけ**がモジュール属性を書き換える。
    #     宣言の無い `X = ...` は**ローカル変数（同名の影）**であって別物。
    #     ★これを区別しないと本物の定数を落とす（実例＝
    #     `HeuristicProtagonist._AREAS` はメソッド内のローカル `_AREAS = (...)` に
    #     引っ掛かって誤検出された）。
    #     ★保守側の単純化＝`global X` の宣言自体を「書く意図」とみなして全部拾う
    #     （読み取りに `global` は不要なので、宣言があるのに書かないコードは実質無い。
    #     仮にあっても「切替口を1つ多めに疑う」側に倒れるだけ＝表の不変条件は壊れない）。
    for fn in [n for n in ast.walk(tree) if isinstance(n, fn_types)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Global):
                out.update(node.names)
    return out


def _assigns(body, mod: str, cls: str | None, out: dict) -> None:
    for st in body:
        names: list[str] = []
        val = None
        if isinstance(st, ast.Assign):
            names = [t.id for t in st.targets if isinstance(t, ast.Name)]
            val = st.value
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            names = [st.target.id]
            val = st.value
        if not names or val is None:
            continue
        ok, v = _const(val)
        if not ok:
            continue
        for n in names:
            if CONST_RE.match(n):
                out[(mod, cls, n)] = v


def consts_of_source(src: str, mod: str) -> dict:
    """1モジュールのソースから切替口を抜く（モジュール直下＋クラス直下の1段）。"""
    out: dict = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    _assigns(tree.body, mod, None, out)
    for st in tree.body:
        if isinstance(st, ast.ClassDef):
            _assigns(st.body, mod, st.name, out)
    # ★実行時に書き換えられる名前は切替口ではない（上の `mutated_names` の説明を参照）
    mut = mutated_names(tree)
    for k in [k for k in out if k[2] in mut]:
        del out[k]
    return out


def consts_at(rev: str) -> dict:
    """rev 時点の切替口の束 {(module, class|None, name): value}。"""
    repo = repo_of(rev)
    if repo is None:
        raise ValueError(f"rev {rev} を持っているクローンが無い")
    paths = _ls_py(rev, repo)
    src = _batch_read(rev, paths, repo)
    out: dict = {}
    for p, s in src.items():
        mod = p[:-3].replace("/", ".")
        out.update(consts_of_source(s, mod))
    return out


def _short(v, n: int = 90) -> str:
    """巨大な定数表（`PRIORITY`／`MM_PARAMS`）を1行で読める長さに切る。"""
    t = repr(v)
    if len(t) <= n:
        return t
    if isinstance(v, dict):
        return f"<dict {len(v)}項 {t[:n]}…>"
    return f"{t[:n]}…"


def _key(k: tuple) -> str:
    mod, cls, name = k
    return f"{mod}.{cls}.{name}" if cls else f"{mod}.{name}"


def diff_consts(build_rev: str, base_rev: str = "HEAD") -> dict:
    """build と base（既定＝HEAD）の切替口の差分。

    - `changed`  ＝両方に在って**値が違う**（★era ピンの tier A＝確実な素材）
    - `base_only`＝base にしか無い＝**収録後に追加された切替口**（tier B の素材）
    - `build_only`＝build にしか無い＝**収録後に削除された切替口**（ピンにできない）
    """
    b, h = consts_at(build_rev), consts_at(base_rev)
    changed = {k: (b[k], h[k]) for k in b if k in h and b[k] != h[k]}
    return {
        "build": build_rev, "base": base_rev,
        "n_build": len(b), "n_base": len(h),
        "changed": changed,
        "base_only": {k: h[k] for k in h if k not in b},
        "build_only": {k: b[k] for k in b if k not in h},
    }


def _is_bool(v) -> bool:
    return isinstance(v, bool)


def is_toggle(name: str) -> bool:
    r"""`B\d+_...` 形＝**切替口**（land のたびに ON/OFF が変わる）。"""
    return bool(NAME_RE.match(name))


def pins_from_diff(d: dict, tier: str = "AB") -> list[tuple]:
    """差分から era ピンの束（`arena.b251_audit.era_pin` が受け取る形）を作る。

    - **tier A（確実）**＝両方に在って値が違う bool の切替口 → **build 時点の値**へ倒す。
    - **tier B（推定・要検証）**＝収録後に追加され、HEAD で `True` の切替口 → **False** へ倒す。
      ★根拠＝本プロジェクトの作法「切替口は**既定 OFF で入れ**、land 時に ON にする」。
      ★ただし**最初から ON で入った切替口**は例外なので、**必ず実測で検証すること**。
    """
    pins: list[tuple] = []
    if "A" in tier:
        for (mod, cls, n), (bv, _hv) in sorted(d["changed"].items(), key=lambda kv: _key(kv[0])):
            pins.append((mod, cls, n, bv))
    if "B" in tier:
        for (mod, cls, n), hv in sorted(d["base_only"].items(), key=lambda kv: _key(kv[0])):
            if _is_bool(hv) and hv is True:
                pins.append((mod, cls, n, False))
    return pins


# ---------------------------------------------------------------------------
# 教材の棚卸し
# ---------------------------------------------------------------------------
def load_log(path: Path) -> tuple[dict, list[dict]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    recs = [json.loads(l) for l in lines if l.strip()]
    return recs[0], [r for r in recs if r.get("type") == "decision"]


def consumers(name: str) -> list[str]:
    """その教材を参照している道具・テストを列挙する（文字列一致・機械的）。"""
    hits: list[str] = []
    for d in ("arena", "tests", "sim", "agents", "scripts"):
        root = REPO / d
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.py")):
            try:
                if name in f.read_text(encoding="utf-8"):
                    hits.append(str(f.relative_to(REPO)))
            except (UnicodeDecodeError, OSError):
                continue
    return hits


def inventory() -> list[dict]:
    rows = []
    for p in sorted(LOGDIR.glob("*.jsonl")):
        meta, ds = load_log(p)
        sc = meta.get("script") or {}
        build = meta.get("tool_build") or ""
        prot = [d for d in ds if d.get("actor") != "mastermind"]
        rows.append({
            "file": p.name,
            "build": build or "（無し）",
            "build_found": bool(build) and rev_exists(build),
            "build_note": KNOWN_FOREIGN_BUILDS.get(build, ""),
            "saved_at": meta.get("saved_at") or "（不明）",
            "set": sc.get("set_name") or "（不明）",
            "days": sc.get("days_per_loop"),
            "loops": sc.get("loops"),
            "rule_y": sc.get("rule_y") or "",
            "winner": meta.get("winner"),
            "loops_played": meta.get("loops_played"),
            "n_dec": len(ds),
            "n_prot": len(prot),
            "consumers": consumers(p.name),
        })
    return rows


# ---------------------------------------------------------------------------
# 再生（bit 一致）
# ---------------------------------------------------------------------------
def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def replay_protagonist(path: Path) -> tuple[int, int, list[str], int]:
    """棋譜の主人公決定を素で再生し (一致数, 全数, 食い違いの席) を返す。

    ★`arena/b191_probe.py` `arena/b207_probe.py` などが持っている再生ループと**同じ手順**
      （`HeuristicProtagonist(0)` を1体作り、`decide(view, decision, options)` を順に呼ぶ）。
    """
    from agents.heuristic_protagonist import HeuristicProtagonist
    _meta, decisions = load_log(path)
    hp = HeuristicProtagonist(0)
    same = total = prefix = 0
    broke = False
    bad: list[str] = []
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        got = hp.decide(d["view"], d["decision"], d["options"])
        total += 1
        if _clean(got) == _clean(d["chosen"]):
            same += 1
            if not broke:
                prefix += 1
        else:
            broke = True
            bad.append(f'L{d.get("loop")}D{d.get("day")} {d.get("actor")} '
                       f'{d.get("decision")}: 再生={got.get("card")}→{got.get("target")} '
                       f'棋譜={d["chosen"].get("card")}→{d["chosen"].get("target")}')
    return same, total, bad, prefix


def score_with(path: Path, pins: list[tuple]) -> tuple[int, int, list[str], int]:
    """ピンを当てて再生する。★**落ちた場合は 0 点**（＝その束は era 復元として成立しない）。

    ★落ちるのは実在する事象＝`PRIORITY` だけを収録当時の表に戻すと、
      その後 land した述語が**新しいキーを引く**ので `KeyError` になる
      （定数だけを部分的に戻しても整合しない例）。
    """
    from arena.b251_audit import era_pin
    with era_pin(bool(pins), pins=tuple(pins)):
        try:
            return replay_protagonist(path)
        except Exception as e:            # noqa: BLE001（★束の非整合は測定結果の一部）
            return 0, 0, [f"★例外で再生できない: {type(e).__name__}: {e}"], 0


def build_of(path: Path) -> str:
    meta, _ = load_log(path)
    return meta.get("tool_build") or ""


def auto_pins(path: Path, tier: str = "AB") -> list[tuple]:
    b = build_of(path)
    if not b or not rev_exists(b):
        return []
    return pins_from_diff(diff_consts(b), tier=tier)


# ---------------------------------------------------------------------------
# 山登り（最小の束を探す）
# ---------------------------------------------------------------------------
def search_pins(path: Path, cands: list[tuple], rounds: int = 3,
                verbose: bool = True, seed: list[tuple] | None = None,
                mode: str = "total", from_full: bool = False
                ) -> tuple[list[tuple], int, int]:
    """候補の中から**一致数を最大にする最小の束**を貪欲に探す。

    手順＝(1) 空から始めて「入れると一致が増える1本」を足す（増分が無くなるまで）→
    (2) 入っている1本ずつ抜いて**一致が落ちない**なら抜く（＝最小化）。
    ★候補が多い時のために `rounds` で足し上げの周回数を制限する。
    """
    def _sc(pins):
        s_, t_, _b, pre = score_with(path, pins)
        return (pre if mode == "prefix" else s_), t_

    base, total = _sc([])
    n_all = total
    if verbose:
        print(f"[search] 素（ピン無し）= {base}/{total}")
    cur: list[tuple] = list(seed or [])
    best = base
    if cur:
        best, _t = _sc(cur)
        if verbose:
            print(f"[search] tier A を全部当てた（{len(cur)}本）= {best}/{n_all}")
    if from_full:
        for c in cands:                      # ★dict 値のピンがあるので set は使えない
            if c not in cur:
                cur.append(c)
        best, _t = _sc(cur)
        if verbose:
            print(f"[search] ★候補を全部当てた（{len(cur)}本）= {best}/{n_all}")
        rounds = 0
    for _ in range(rounds):
        gain = None
        for c in cands:
            if c in cur:
                continue
            s, _t = _sc(cur + [c])
            if s > best:
                if gain is None or s > gain[0]:
                    gain = (s, c)
        if gain is None:
            break
        best, c = gain
        cur.append(c)
        if verbose:
            print(f"[search] +{_key(c[:3])}={_short(c[3])} → {best}/{n_all}")
        if best == n_all:
            break
    for c in list(cur):
        rest = [x for x in cur if x != c]
        s, _t = _sc(rest)
        if s >= best:
            cur = rest
            best = s
            if verbose:
                print(f"[search] -{_key(c[:3])}（落ちない＝不要）→ {best}/{n_all}")
    return cur, best, n_all


# ---------------------------------------------------------------------------
# 既存の手動 ERA_PINS との突き合わせ
# ---------------------------------------------------------------------------
#: 手で era ピンを持っている道具（B-260／B-267 が実装）と、その教材。
MANUAL_TOOLS: tuple[tuple[str, str], ...] = (
    ("arena.b251_audit", "鈴蘭_BTX3d_seed0_同期後の再戦_2026-08-18.jsonl"),
    ("arena.b207_probe", "鈴蘭_BTX3d_seed0_land後検証_2026-08-12.jsonl"),
)


def manual_pins(mod: str) -> list[tuple]:
    import importlib
    m = importlib.import_module(mod)
    return list(getattr(m, "ERA_PINS", ()))


# ---------------------------------------------------------------------------
# 実測（measure）と表の生成（report）
# ---------------------------------------------------------------------------
#: ★**AI がどちら側を打ったか**はログに記録されていない＝**ファイル名から推定する**
#:   （`docs/feedback_logs` の命名規約＝「主人公プレイ」＝人間が主人公／
#:    「脚本家プレイ」「ユーザー脚本家」＝人間が脚本家＝**主人公は AI**）。
#:   ★推定なので、表には推定であることを明記する。実測（素の一致率）が傍証になる。
_HUMAN_PROTAGONIST_MARKS = ("主人公プレイ", "ユーザー主人公")


def ai_side(name: str) -> str:
    """その教材で **AI が打った側**（推定）。"""
    return "mastermind" if any(m in name for m in _HUMAN_PROTAGONIST_MARKS) else "protagonist"


DATA_DEFAULT = REPO / "docs" / "教材ごとのera表_実測_2026-08-19.json"


def measure(logs: list[Path] | None = None, verbose: bool = True) -> list[dict]:
    """全教材について「素／表の束（tier A+B）／最小の束」の bit 一致を実測する。"""
    rows = []
    for p in (logs or sorted(LOGDIR.glob("*.jsonl"))):
        meta, ds = load_log(p)
        b = meta.get("tool_build") or ""
        side = ai_side(p.name)
        n_prot = len([d for d in ds if d.get("actor") != "mastermind"])
        r = {"file": p.name, "build": b, "build_found": bool(b) and rev_exists(b),
             "ai_side": side, "n_prot": n_prot, "note": KNOWN_FOREIGN_BUILDS.get(b, "")}
        if verbose:
            print(f"[measure] {p.name}  build={b or '（無し）'}  AI側={side}")
        if n_prot == 0 or side != "protagonist":
            r["status"] = ("決定が無い" if n_prot == 0
                           else "★該当外＝主人公は人間が打っている（主人公再生では測れない）")
            if n_prot:
                s0, t0, _b, _pre = score_with(p, [])
                r["base"] = [s0, t0]
            rows.append(r)
            continue
        s0, t0, _b, _pre = score_with(p, [])
        r["base"] = [s0, t0]
        if not r["build_found"]:
            r["status"] = "★build を解決できない＝era ピンを導けない（不明）"
            rows.append(r)
            if verbose:
                print(f"    素 {s0}/{t0}  ★build 不明")
            continue
        d = diff_consts(b)
        cands = pins_from_diff(d, tier="AB")
        sa, ta, bad2, _p2 = score_with(p, cands)
        r["full"] = [sa, t0]
        if ta == 0 and t0:
            r["full_error"] = bad2[0] if bad2 else "再生できない"
        r["n_cands"] = len(cands)
        pins, best, total = search_pins(p, cands, verbose=False, from_full=True)
        r["min_pins"] = [[m, c, n, v] for m, c, n, v in pins if _is_bool(v)]
        r["min_other"] = [_key((m, c, n)) for m, c, n, v in pins if not _is_bool(v)]
        r["min"] = [best, total]
        r["restored"] = bool(total and best == total)
        r["status"] = "★復元できた" if r["restored"] else "★復元できない（残差あり）"
        if verbose:
            print(f"    素 {s0}/{t0} → 表の束 {sa}/{t0} → 最小の束 {best}/{total}"
                  f"（{len(pins)}本）{r['status']}")
        rows.append(r)
    return rows


def cmd_measure(a) -> int:
    rows = measure(None if not a.log else [LOGDIR / x for x in a.log])
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print(f"[measure] 書き出し: {a.out}")
    return 0


def _md_pins(r: dict) -> str:
    if not r.get("min_pins") and not r.get("min_other"):
        return "—"
    names = [f"`{_key((m, c, n))}`={v!r}" for m, c, n, v in r.get("min_pins", [])]
    names += [f"`{k}`（非bool）" for k in r.get("min_other", [])]
    return "<br>".join(names)


GEN_BEGIN = "<!-- BEGIN GENERATED: python -m arena.era_table report --write -->"
GEN_END = "<!-- END GENERATED -->"


def cmd_report(a) -> int:
    out: list[str] = []
    _p = out.append
    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    inv = {r["file"]: r for r in inventory()}
    _p(f"### 教材ごとの era 表（自動生成・{len(data)}本）")
    _p("")
    _p("`素`＝現行既定のまま再生した bit 一致／`表の束`＝本表から導いた era ピン（tier A+B）を"
       "全部当てた時／`最小の束`＝そこから落ちない1本ずつ抜いた最小限。")
    _p("")
    _p("| 教材 | tool_build | 収録 | 脚本 | AI側 | 利用道具 | 素 | 表の束 | 最小の束 | 復元 |")
    _p("|---|---|---|---|---|---|---|---|---|---|")
    for r in data:
        i = inv.get(r["file"], {})
        tools = ", ".join(Path(c).stem for c in i.get("consumers", [])
                          if c.startswith("arena/")) or "—"
        def _f(k):
            v = r.get(k)
            return f"{v[0]}/{v[1]}" if v else "—"
        bmark = r["build"] or "（無し）"
        if not r["build_found"]:
            bmark += " ★"
        _p(f"| {r['file']} | `{bmark}` | {i.get('saved_at','')} | "
           f"{i.get('set','')} {i.get('days','')}日 | {r['ai_side']} | {tools} | "
           f"{_f('base')} | {_f('full')} | {_f('min')} | {r.get('status','')} |")
    _p("")
    _p("★印＝`tool_build` が本リポジトリの git 履歴に無い（別リポジトリ／未 push）。")
    _p("")
    _p("### 復元に要る最小の束（教材ごと・実測）")
    _p("")
    for r in data:
        if r.get("min_pins") or r.get("min_other"):
            mark = "✅" if r.get("restored") else "△（完全復元には届かない）"
            _p(f"- {mark} **{r['file']}**（build `{r['build']}`）: {_md_pins(r)}")
    _p("")
    _p("### 束を当てると再生が落ちる教材（＝定数の部分的な巻き戻しが整合しない）")
    _p("")
    for r in data:
        if r.get("full_error"):
            _p(f"- **{r['file']}**: `{r['full_error']}`")
    body = "\n".join(out)
    if a.write:
        f = Path(a.write)
        txt = f.read_text(encoding="utf-8")
        i, j = txt.index(GEN_BEGIN), txt.index(GEN_END)
        f.write_text(txt[:i] + GEN_BEGIN + "\n\n" + body + "\n\n" + txt[j:],
                     encoding="utf-8")
        print(f"[report] 差し込み: {a.write}")
    else:
        print(body)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_inventory(a) -> int:
    rows = inventory()
    n_build = sum(1 for r in rows if r["build"] != "（無し）")
    n_found = sum(1 for r in rows if r["build_found"])
    n_local = sum(1 for r in rows if r["build_found"] and repo_of(r["build"]) == REPO)
    print(f"[era_table] 教材 {len(rows)}本／`tool_build` あり {n_build}本／"
          f"解決できた build {n_found}本（うち本リポジトリ {n_local}本・"
          f"別クローン {n_found - n_local}本）")
    for r in rows:
        mark = ("" if repo_of(r["build"]) == REPO
                else ("  ★別クローンで解決" if r["build_found"] else "  ★どこにも無い"))
        print(f"- {r['file']}")
        print(f"    build={r['build']}{mark}  saved_at={r['saved_at']}  "
              f"{r['set']} {r['days']}日×{r['loops']}L  winner={r['winner']}  "
              f"decisions={r['n_dec']}（主人公 {r['n_prot']}）")
        if r["build_note"]:
            print(f"    ★{r['build_note']}")
        if r["consumers"]:
            print(f"    利用: {', '.join(r['consumers'])}")
    return 0


def cmd_consts(a) -> int:
    c = consts_at(a.rev)
    print(f"[era_table] rev={a.rev}（{rev_info(a.rev)}）切替口 {len(c)}件")
    for k in sorted(c, key=_key):
        print(f"    {_key(k)} = {c[k]!r}")
    return 0


def cmd_diff(a) -> int:
    if not rev_exists(a.rev):
        print(f"[era_table] ★rev {a.rev} は本リポジトリに無い＝差分を取れない（不明）")
        if a.rev in KNOWN_FOREIGN_BUILDS:
            print(f"    ★既知: {KNOWN_FOREIGN_BUILDS[a.rev]}")
        return 1
    d = diff_consts(a.rev, a.base)
    print(f"[era_table] {a.rev}（{rev_info(a.rev)}）  vs  {a.base}（{rev_info(a.base)}）")
    print(f"    切替口の数: build {d['n_build']} / base {d['n_base']}")
    print(f"    ★値が違う（tier A・確実） {len(d['changed'])}件")
    for k in sorted(d["changed"], key=_key):
        bv, hv = d["changed"][k]
        print(f"        {_key(k)}: 収録当時={_short(bv)}  現行={_short(hv)}")
    print(f"    収録後に追加された切替口 {len(d['base_only'])}件"
          f"（うち現行 True＝tier B 候補 "
          f"{sum(1 for v in d['base_only'].values() if v is True)}件）")
    for k in sorted(d["base_only"], key=_key):
        print(f"        + {_key(k)} = {_short(d['base_only'][k])}")
    print(f"    収録後に消えた切替口 {len(d['build_only'])}件（★ピンにできない）")
    for k in sorted(d["build_only"], key=_key):
        print(f"        - {_key(k)} = {_short(d['build_only'][k])}")
    return 0


def cmd_pins(a) -> int:
    p = Path(a.log) if Path(a.log).is_absolute() else LOGDIR / a.log
    b = build_of(p)
    print(f"[era_table] {p.name}  build={b or '（無し）'}  {rev_info(b) if b else ''}")
    if not b or not rev_exists(b):
        print("    ★build を解決できない＝**era ピンを機械的に導けない**（不明）")
        if b in KNOWN_FOREIGN_BUILDS:
            print(f"    ★既知: {KNOWN_FOREIGN_BUILDS[b]}")
        return 1
    d = diff_consts(b)
    for tier in ("A", "B"):
        pins = pins_from_diff(d, tier=tier)
        label = "tier A（確実＝値が違う）" if tier == "A" else "tier B（推定＝収録後に追加され現行 ON）"
        print(f"    {label}: {len(pins)}本")
        for mod, cls, n, v in pins:
            print(f"        {_key((mod, cls, n))} → {_short(v)}")
    return 0


def cmd_verify(a) -> int:
    p = Path(a.log) if Path(a.log).is_absolute() else LOGDIR / a.log
    tier = {"none": "", "A": "A", "AB": "AB", "auto": "AB"}[a.pins]
    pins = auto_pins(p, tier) if tier else []
    same, total, bad, prefix = score_with(p, pins)
    print(f"[era_table] {p.name}  build={build_of(p)}  ピン={a.pins}（{len(pins)}本）")
    print(f"    主人公決定の bit 一致 = {same}/{total}（先頭からの連続一致 {prefix}）")
    for line in bad[: a.show]:
        print(f"    ≠ {line}")
    return 0


def cmd_search(a) -> int:
    p = Path(a.log) if Path(a.log).is_absolute() else LOGDIR / a.log
    b = build_of(p)
    if not rev_exists(b):
        print(f"[era_table] ★build {b} を解決できない＝候補を作れない（不明）")
        return 1
    d = diff_consts(b)
    seed = pins_from_diff(d, tier="A") if (a.seed_a and "A" in a.tier) else []
    cands = [c for c in pins_from_diff(d, tier=a.tier) if c not in seed]
    print(f"[era_table] {p.name}  build={b}  土台 tier A {len(seed)}本／"
          f"探索候補 {len(cands)}本（tier={a.tier}）")
    pins, best, total = search_pins(p, cands, rounds=a.rounds, seed=seed, mode=a.mode,
                                    from_full=a.from_full)
    print(f"[search] ★最小の束 {len(pins)}本 → {best}/{total}")
    for mod, cls, n, v in pins:
        print(f"    {_key((mod, cls, n))} → {_short(v)}")
    return 0


def cmd_crosscheck(a) -> int:
    rc = 0
    for mod, log in MANUAL_TOOLS:
        p = LOGDIR / log
        b = build_of(p)
        man = sorted(manual_pins(mod))
        derived = sorted(auto_pins(p, "A"))
        print(f"[crosscheck] {mod}  教材={log}  build={b}")
        print(f"    手動 ERA_PINS  ({len(man)}本): "
              + ", ".join(f"{_key(k[:3])}={k[3]!r}" for k in man))
        print(f"    表から導いた束 ({len(derived)}本): "
              + ", ".join(f"{_key(k[:3])}={k[3]!r}" for k in derived))
        extra = [k for k in derived if k not in man]
        miss = [k for k in man if k not in derived]
        if extra:
            print("    ★表にしか無い（＝手動ピンが最小限に絞った分）: "
                  + ", ".join(f"{_key(k[:3])}={k[3]!r}" for k in extra))
        if miss:
            rc = 1
            print("    ★★手動にしか無い（＝表が取りこぼした）: "
                  + ", ".join(f"{_key(k[:3])}={k[3]!r}" for k in miss))
        for label, pins in (("素", []), ("表 tier A", derived), ("手動", man)):
            s, t, _b, _pre = score_with(p, list(pins))
            print(f"    再生一致（{label}）= {s}/{t}")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m arena.era_table")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    p = sub.add_parser("consts"); p.add_argument("--rev", default="HEAD")
    p = sub.add_parser("diff")
    p.add_argument("--rev", required=True); p.add_argument("--base", default="HEAD")
    p = sub.add_parser("pins"); p.add_argument("--log", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--log", required=True)
    p.add_argument("--pins", default="auto", choices=["none", "A", "AB", "auto"])
    p.add_argument("--show", type=int, default=8)
    p = sub.add_parser("search")
    p.add_argument("--log", required=True)
    p.add_argument("--tier", default="AB", choices=["A", "B", "AB"])
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--from-full", action="store_true",
                   help="候補を全部当てた状態から始め、落ちない1本ずつ抜いて最小化する"
                        "（貪欲な足し上げが局所解に落ちるのを避ける）")
    p.add_argument("--mode", default="total", choices=["total", "prefix"],
                   help="prefix＝『先頭からの連続一致』を最大化する（分岐の玉突きを避けて根を探す）")
    p.add_argument("--no-seed-a", dest="seed_a", action="store_false", default=True,
                   help="tier A を土台に置かず、全部を貪欲探索の候補にする")
    sub.add_parser("crosscheck")
    p = sub.add_parser("measure")
    p.add_argument("--log", nargs="*", default=[])
    p.add_argument("--out", default=str(DATA_DEFAULT))
    p = sub.add_parser("report")
    p.add_argument("--data", default=str(DATA_DEFAULT))
    p.add_argument("--write", default=None,
                   help="doc の GENERATED ブロックへ差し込む（既定＝標準出力へ印字）")
    a = ap.parse_args(argv)
    return {
        "inventory": cmd_inventory, "consts": cmd_consts, "diff": cmd_diff,
        "pins": cmd_pins, "verify": cmd_verify, "search": cmd_search,
        "crosscheck": cmd_crosscheck, "measure": cmd_measure, "report": cmd_report,
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
