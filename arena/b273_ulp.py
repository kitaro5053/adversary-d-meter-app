# -*- coding: utf-8 -*-
"""B-273 フェーズ0：**1 ULP タイの射程をコーパス全体で数える**（★測定のみ・実装は範囲外）。

チケット＝`docs/バックログ_構想メモ_FableA.md` §72-86（フェーズ0）／§72-84（本体）。
出典＝§72-82（B-266 追補＝`PYTHONHASHSEED` で 1 ULP のナイフエッジが反転する発見）。

## ★発注前の検算（規約の要求。最初のコミットに書く）

1. **§72-48 教訓1＝その量は原理的に非ゼロになりうるか。**
   ★**なりうる（部分的に確認済み）**。§72-82 が教材1本（牡丹 seed0・BTX 3日級）で
   **1 ULP タイ 9席／完全一致 9席**（seed 0）を実測している。∴ 指標 (a) が
   「原理的に 0」ということは無い。ただし ★**コーパス全体で非ゼロか**（＝教材1本の
   特異事象か、コーパス全域に散らばる構造的な量か）は**別問題で、本チケットが測る対象**。
   一方 (b)（hash seed で**実際に**選択が変わる席）は **§72-82 が牡丹で実在を示している**
   （seed 0 と seed 1 で別の手＝L6D2）＝これも原理的に非ゼロ。

2. **§72-53＝その指標の改善が「強さ」に繋がるか。**
   ★**繋がるとは言えない。本チケットは強さを主張しない。**
   1 ULP タイを決定的なタイブレーク（例＝丸めてから名前順）に置き換えても、
   **決着先が「別の対象」に変わるだけ**で、良くも悪くもなりうる。
   丸め幅は KB が決めない量＝規約§7 判例1 により**通常ゲート**の改修であって
   rule-rational 免除の対象ではない。∴ ★**本チケットの成果は「射程の見積り」まで**であり、
   結論は「B-273 本体に着手する価値がある席数があるか」だけを言う。
   ★得られるのは強さではなく**再現性**（同じ入力から同じ手が出ること）である点も明示する。

## 何を数えるか（★事前登録＝測る前にここに書いた定義から動かさない）

- **(a) 1 ULP 以内で決着した席**＝`sorted` / `max` / `min` の**決着点**（勝者と次点の境目）で
  **差が 0 でなく、かつ 4 ULP 以内**だったもの。
  ★`sorted` はタプルの第1要素（float）で決着するので、**差が 0 でない限り第2要素
  （名前など）のタイブレークに落ちない**＝**最下位ビットが結果を決めた**候補。
  ★(a) は「**危うい**」だけであって実害ではない（＝上界）。
- **(b) `PYTHONHASHSEED` を振ると実際に選ばれる対象が変わる席**＝**実害**。
  seed 違いの2本のトレースを**先頭から同期して突き合わせ**、
  **最初に結果が食い違った呼び出し**を名指しする（そこから先は局面自体がずれるので比較不能）。
- **(c) 同型の箇所**＝`agents/` 全体で `sorted()` / `max()` / `min()` の
  **キーの第1成分が float** である決定箇所。★静的な洗い出しではなく
  **実際に走った呼び出しを全部見る**（＝`agents.*` のモジュール名前空間の
  `sorted`/`max`/`min` を差し替えて横取りする。ビルトインより先に解決される）。
  ★`:2210` だけが問題とは限らない、という前提で全件数える。
- **完全一致（差が厳密に 0.0）**は (a) と**別に数える**＝これは第2要素のタイブレーク
  （名前）か、キーが同点なら**入力の並び順**で決まる＝`perm` 側の分散源であって
  最下位ビットの話ではない。★**混同しない**。

## ★★実測の結論（2026-08-20・`lane/b273`・両コーパス・`PYTHONHASHSEED` 0/1/2/3）

| | 3日級 140局 | 5日級 80局 |
|---|---|---|
| float 第1キーの決定席（総数） | 9,693 | 10,425 |
| **(a) 1 ULP タイを含む席** | 14〜17 | 12〜18 |
| ★**うち決定を変えうる切り口に載った席** | **0** | **2〜4** |
| **(b) hash seed で実際に打つ手が変わった局** | 0 / 0 / 10 (seed 1/2/3) | 0 / 1 / 0 |
| ★**うち 1 ULP タイに帰属した局** | **0** | **0**（`:8599` `:122` の**完全同点**） |
| ★**勝敗（loops_to_win）が動いた局** | **0**（防衛 133 固定） | **0**（防衛 73 固定） |

★**perm（席の並べ替え）との関係＝別物**。`perm` を振ると手は 78〜139 局で変わり
勝敗も 29〜84 局動くが、その**帰属は 100% が「完全同点」**＝
★**1 ULP で説明できる局は両コーパス・3種の置換すべてで 0**。

★**何が担保しているか**（再発時の道しるべ）：
1. **消費形**＝`:2210` は `sorted(...)[:2]` を**集合**として使う。∴ 効くのは
   「2位/3位の境目 かつ 候補3人以上」だけで、実測のタイの大半はそれより下位に出る
   （3日級＝14席すべてが下位＝実効0）。
2. **B-267（`B256_UNREACHABLE_SKIP`＋`B262_BELIEF_BOUND` の既定 ON）**が候補プールを削る。
   反実仮想（両方 OFF＝land 前の挙動を bit 再現＝3日級 防衛133/平均3.079）では
   **1 ULP タイ 14→38 席・実効 0→4 席**に増える。★ただし 5日級では実効 3→2 と
   **減らない**＝**B-267 は普遍的な担保ではない**（3日級で効いているだけ）。
3. ∴ ★**「原理的に0」ではない**。5日級には実効な切り口が 2〜4 席あり、
   seed 0〜3 では符号が反転しなかったというだけ（＝経験的な0）。

## ★挙動には触れない

`agents/` `sim/` `engine/` `rules/` を**1バイトも変更しない**。本モジュールは
モジュール名前空間の `sorted`/`max`/`min` を**実行時に**包むだけで、
包みは**元の組み込み関数をそのまま呼ぶ**（比較の結果は 1 bit も変えない）。
`key=` は**呼び直さない**（副作用の二重発火を避けるため、元の `max`/`min`/`sorted` が
呼ぶキー関数を包んで**その戻り値を控える**）。

CLI（測定は `PYTHONIOENCODING=utf-8`。★本件は hash seed を振るのが目的なので
`PYTHONHASHSEED` は**明示的に指定し、出力に必ず印字する**）:

    # (a)(c)＝コーパス全体を1本走らせて float 第1キーの決定箇所を全件数える
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b273_ulp corpus \
        --days 3 --perm id --out /tmp/b273_d3_s0_id.json

    # (b)＝2本のトレースを突き合わせて「実際に変わった席」を数える
    PYTHONIOENCODING=utf-8 python -m arena.b273_ulp diff \
        /tmp/b273_d3_s0_id.json /tmp/b273_d3_s1_id.json

    # ★perm との関係＝perm × hash seed の格子でベンチ行を取る（shim 無し＝速い）
    PYTHONIOENCODING=utf-8 python -m arena.b273_ulp grid \
        --days 3 --perms id,rev,h1,h2 --seeds 0,1,2,3 --out /tmp/b273_grid3.json
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import json
import math
import os
import re
import linecache
import sys
from collections import Counter, defaultdict

_B_SORTED = builtins.sorted
_B_MAX = builtins.max
_B_MIN = builtins.min

#: ★包む対象＝`agents/` の実働モジュール（`legacy` と `llm_*` と `debug` は本番経路外）。
AGENT_MODULES = (
    "agents.heuristic_protagonist", "agents.heuristic", "agents.belief",
    "agents.base", "agents.attack_plan", "agents.defense_plan", "agents.card_effect",
    "agents.soft_evidence", "agents.b100_alloc", "agents.b100_mix",
    "agents.b221_breaker", "agents.b224_channel", "agents.b232_cool_math",
    "agents.b240_cool_budget", "agents.b241_seat_arb", "agents.b245_supplier_id",
    "agents.b246_aim", "agents.b253_unrest_yield",
)

#: 「1 ULP タイ」の判定幅（★§72-82 の `arena/b266_audit.person_top2_knife_edge` と同一定義）。
ULP_FACTOR = 4

_ADDR = re.compile(r"0x[0-9a-fA-F]+")


def is_ulp_tie(a: float, b: float) -> bool:
    """`a` と `b` が「差 0 でなく 4 ULP 以内」か（★b266_audit と同じ定義）。"""
    gap = a - b
    return gap != 0.0 and abs(gap) <= ULP_FACTOR * math.ulp(_B_MAX(abs(a), abs(b), 1e-300))


def _f1(k):
    """キーの『float 第1成分』。float でなければ `None`。

    ★`int` / `str` / `bool` は丸め誤差を持たない＝最下位ビットの話にならないので除外する。
    """
    t = type(k)
    if t is float:
        return k
    if t is tuple and k and type(k[0]) is float:
        return k[0]
    return None


def _canon(o) -> str:
    """hash seed に依存しない正規表現（dict/set は並べ替えてから文字列化）。"""
    t = type(o)
    if t is dict:
        return "{" + ",".join(f"{k!r}:{_canon(v)}"
                              for k, v in _B_SORTED(o.items(), key=lambda kv: repr(kv[0]))) + "}"
    if t is list or t is tuple:
        return "[" + ",".join(_canon(x) for x in o) + "]"
    if t is set or t is frozenset:
        return "{" + ",".join(_B_SORTED(_canon(x) for x in o)) + "}"
    if t is float:
        return repr(o)
    return _ADDR.sub("0xX", repr(o))


def _dig(s: str) -> str:
    return hashlib.md5(s.encode("utf-8", "replace")).hexdigest()[:10]


# ---------------------------------------------------------------------------
# 記録器
# ---------------------------------------------------------------------------
class Recorder:
    """float 第1キーの決定箇所を全件控える。

    - `sites[site]` … 呼び出し回数・float 第1キーの回数・1 ULP タイ・完全同点。
    - `trace[game]` … `(site, keys_digest, result_digest, cls)` の**並び**
      （★(b) の突き合わせに使う。先頭から同期して最初の食い違いを名指しする）。
    """

    def __init__(self, keep_trace: bool = True):
        self.sites: dict[str, Counter] = defaultdict(Counter)
        self.seat_hits: dict[str, list] = defaultdict(list)
        self.trace: dict[str, list] = {}
        #: ★(b) の**基準**＝AI が実際に打った手の並び（`decide` の戻り値）。
        #:   `sorted` の全順序は消費されない部分まで含むので、
        #:   「手が変わったか」は**戻り値そのもの**で判定する（過検出を避ける）。
        self.moves: dict[str, list] = {}
        self.game: str | None = None
        self._cur: list = []
        self._mv: list = []
        self.marks: list = []
        self.ctx: dict = {}
        self.keep_trace = keep_trace
        self.on = False

    # -- ゲーム／席の文脈 ---------------------------------------------------
    def begin_game(self, gid: str) -> None:
        self.game = gid
        self._cur = []
        self._mv = []
        self.trace[gid] = self._cur
        self.moves[gid] = self._mv

    def seat_key(self) -> str:
        c = self.ctx
        return f'L{c.get("loop")}D{c.get("day")}{c.get("seat") or "mm"}:{c.get("decision")}'

    # -- 1件記録 -----------------------------------------------------------
    def note(self, site: str, kind: str, winner_f, runner_f, keys_dig, res_dig, n: int):
        s = self.sites[site]
        s["n_items"] += n
        cls = "-"
        if winner_f is not None and runner_f is not None:
            s["float_calls"] += 1
            if winner_f == runner_f:
                s["exact"] += 1
                cls = "exact"
            elif is_ulp_tie(winner_f, runner_f):
                s["ulp"] += 1
                cls = "ulp"
                if len(self.seat_hits[site]) < 40:
                    self.seat_hits[site].append(
                        {"game": self.game, "seat": self.seat_key(),
                         "gap": winner_f - runner_f, "w": repr(winner_f), "r": repr(runner_f)})
            else:
                s["clear"] += 1
                cls = "clear"
            if cls in ("ulp", "exact"):
                self.marks.append([site, cls, 0])
            if self.keep_trace:
                self._cur.append([site, keys_dig, res_dig, cls, self.seat_key()])


REC = Recorder()


# ---------------------------------------------------------------------------
# shim（★元の組み込み関数をそのまま呼ぶ＝比較の結果は 1 bit も変えない）
# ---------------------------------------------------------------------------
def _site(kind: str) -> str:
    """呼び出し箇所の同定キー。

    ★B-273 追補（2026-08-20・合流ゲートで発覚）＝初版は `f_lineno`（行番号）をキーに
    していたが、**行番号は land のたびにズレる**（B-265 の +112 行で `:2210`→`:2212` に
    動き、台帳とテストが一斉に落ちた）。B-258／B-264 が見つけた「監査道具の定数
    ハードコード」と同じ族。∴ **行番号ではなく行の中身**（qualname＋ソース断片）で同定する。
    同一 qualname 内の同種呼び出しは断片で区別される（完全同文が2行あれば併合される＝
    その場合は台帳の断片一意性テストが落ちて知らせる）。
    """
    f = sys._getframe(2)
    mod = f.f_globals.get("__name__", "?")
    qn = getattr(f.f_code, "co_qualname", f.f_code.co_name)
    # ★断片は**切らない**（64字で切った初版は `[:2]` が断片から落ちて台帳が引けなかった）
    frag = " ".join(linecache.getline(f.f_code.co_filename, f.f_lineno).split())
    return f"{mod}:{qn}:{kind}#{frag}"


def _wrap_key(k, box):
    def wk(x):
        v = k(x)
        box.append(v)
        return v
    return wk


def _shim_extreme(orig, kind: str):
    def shim(*args, **kw):
        key = kw.get("key")
        if not REC.on or len(args) != 1 or not callable(key):
            return orig(*args, **kw)          # `max(0, x)` 等＝選択ではない
        box: list = []
        kw2 = dict(kw)
        kw2["key"] = _wrap_key(key, box)
        res = orig(list(args[0]), **kw2)
        st = _site(kind)
        REC.sites[st]["calls"] += 1
        if len(box) < 2:
            REC.sites[st]["n_lt2"] += 1
            return res
        fs = [_f1(v) for v in box]
        if fs[0] is None or any(v is None for v in fs):
            REC.sites[st]["nonfloat"] += 1
            return res
        # 勝者＝最大／最小（同値が複数なら**先頭**が勝者＝`max` の仕様）。
        # 次点＝勝者1個ぶんだけ除いた残りの最大／最小。
        # ★キーの形＝タプル（第2要素に名前などの決定的タイブレークが在る）か
        #   素の float（同点は**入力の並び順**で決まる）か。残存リスクの層が違う。
        REC.sites[st]["key_tuple" if type(box[0]) is tuple else "key_scalar"] += 1
        srt = _B_SORTED(fs, reverse=(kind == "max"))
        win, runner = srt[0], srt[1]
        REC.note(st, kind, win, runner,
                 _dig("|".join(repr(v) for v in fs)), _dig(_canon(res)), len(fs))
        return res
    return shim


def _shim_sorted(orig):
    def shim(*args, **kw):
        key = kw.get("key")
        if not REC.on or len(args) != 1:
            return orig(*args, **kw)
        st0 = _site("sorted")
        REC.sites[st0]["calls"] += 1
        if callable(key):
            box: list = []
            kw2 = dict(kw)
            kw2["key"] = _wrap_key(key, box)
            res = orig(list(args[0]), **kw2)
            # ★出力順のキー＝控えたキーを同じ向きに並べ直したもの
            #   （同値の並び順は隣接差の計算に影響しない）。
            keys_in_order = (_B_SORTED(box, reverse=bool(kw.get("reverse")))
                             if len(box) >= 2 else None)
        else:
            res = orig(*args, **kw)
            keys_in_order = res if len(res) >= 2 else None
        if keys_in_order is None or len(keys_in_order) < 2:
            REC.sites[st0]["n_lt2"] += 1
            return res
        fs = [_f1(v) for v in keys_in_order]
        st = st0
        if fs[0] is None or any(v is None for v in fs):
            REC.sites[st]["nonfloat"] += 1
            return res
        # ★決着点＝隣接ペア。最小の非ゼロ差が「最も危うい切り口」。
        gaps = [abs(fs[i] - fs[i + 1]) for i in range(len(fs) - 1)]
        ties = [i for i, g in enumerate(gaps) if is_ulp_tie(fs[i], fs[i + 1])]
        exact = [i for i, g in enumerate(gaps) if g == 0.0]
        s = REC.sites[st]
        s["n_items"] += len(fs)
        s["float_calls"] += 1
        s["key_tuple" if type(keys_in_order[0]) is tuple else "key_scalar"] += 1
        if ties:
            s["ulp"] += 1
            s["ulp_pairs"] += len(ties)
            if 0 in ties:
                s["ulp_cut1"] += 1
            if 1 in ties:
                s["ulp_cut2"] += 1
            if _B_MIN(ties) <= 1:
                s["ulp_top2"] += 1
            REC.marks.append([st, "ulp", _B_MIN(ties)])
            if len(REC.seat_hits[st]) < 40:
                i = ties[0]
                REC.seat_hits[st].append(
                    {"game": REC.game, "seat": REC.seat_key(), "pair": i,
                     "n": len(fs), "gap": fs[i] - fs[i + 1],
                     "w": repr(fs[i]), "r": repr(fs[i + 1])})
        elif exact:
            REC.marks.append([st, "exact", _B_MIN(exact)])
        if exact:
            s["exact"] += 1
        if not ties and not exact:
            s["clear"] += 1
        if REC.keep_trace:
            REC._cur.append([st, _dig("|".join(repr(v) for v in fs)),
                             _dig(_canon(res)), "ulp" if ties else ("exact" if exact else "clear"),
                             REC.seat_key()])
        return res
    return shim


_INSTALLED: list = []


def install() -> None:
    if _INSTALLED:
        return
    for name in AGENT_MODULES:
        try:
            m = importlib.import_module(name)
        except Exception:
            continue
        for kind, shim in (("sorted", _shim_sorted(_B_SORTED)),
                           ("max", _shim_extreme(_B_MAX, "max")),
                           ("min", _shim_extreme(_B_MIN, "min"))):
            had = kind in m.__dict__
            _INSTALLED.append((m, kind, had, m.__dict__.get(kind)))
            setattr(m, kind, shim)


def uninstall() -> None:
    while _INSTALLED:
        m, kind, had, prev = _INSTALLED.pop()
        if had:
            setattr(m, kind, prev)
        else:
            m.__dict__.pop(kind, None)


# ---------------------------------------------------------------------------
# コーパス走行
# ---------------------------------------------------------------------------
_CTX_INSTALLED: list = []


def _uninstall_ctx() -> None:
    while _CTX_INSTALLED:
        cls, orig = _CTX_INSTALLED.pop()
        cls.decide = orig


def _install_ctx() -> None:
    """`decide` を包んで席の文脈と**打った手**を控える（★戻り値は素通し）。

    ★**冪等にすること**＝同一プロセスで2回走らせると二重に包まれ、
      `moves` が1手につき2件になって突き合わせがずれる（実測で踏んだ）。
    """
    from agents import HeuristicMastermind, HeuristicProtagonist
    _uninstall_ctx()
    for cls, is_mm in ((HeuristicProtagonist, False), (HeuristicMastermind, True)):
        orig = cls.decide
        _CTX_INSTALLED.append((cls, orig))

        def mk(orig=orig, is_mm=is_mm):
            def decide(self, view, decision, options):
                REC.ctx = {"loop": view.get("loop"), "day": view.get("day"),
                           "seat": ("mm" if is_mm else view.get("seat")),
                           "decision": decision}
                if not REC.on:
                    return orig(self, view, decision, options)
                REC.marks = []
                res = orig(self, view, decision, options)
                REC._mv.append([REC.seat_key(), _dig(_canon(res)), REC.marks])
                REC.marks = []
                return res
            return decide
        cls.decide = mk()


def run_corpus(days: int, perm: str = "id", loops: int = 8,
               limit: int | None = None, keep_trace: bool = True,
               verbose: bool = True, only: list[str] | None = None,
               knobs: dict | None = None) -> dict:
    """コーパス全体（3日級140局／5日級80局）を走らせて (a)(b)(c) の材料を控える。

    `knobs` … ★**反実仮想の測定用**＝`HeuristicProtagonist` のクラス属性を一時的に
    差し替える（例＝`{"B256_UNREACHABLE_SKIP": False}`）。「今 (b) が 0 なのは何が
    担保しているのか」を数字で言うために使う。★**本番の既定は変えない**
    （終了時に必ず戻す）。§72-78 に従い `agents.belief._RECOMPUTE_CACHE` を
    条件ごとに `clear()` する（monkeypatch を署名に含まないため）。
    """
    from dataclasses import replace
    from agents import HeuristicMastermind, HeuristicProtagonist
    from arena.benchmark import benchmark_scripts
    from arena import tie_noise
    from sim import run_game

    import agents.belief as _bel
    _bel._RECOMPUTE_CACHE.clear()          # ★§72-78：条件ごとに必ず落とす
    saved: dict = {}
    if knobs:
        for k, v in knobs.items():
            saved[k] = getattr(HeuristicProtagonist, k)
            setattr(HeuristicProtagonist, k, v)
    REC.__init__(keep_trace=keep_trace)
    # ★順序が重要＝`tie_noise.install_perm` は `HeuristicProtagonist.decide` を
    #   **取り込み時の原本に戻してから**包む。先に文脈ラッパを入れると剥がされ、
    #   主人公の席ラベルが直前の脚本家のまま残る（数値には影響しないがラベルが壊れる）。
    tie_noise.install_perm(perm)
    _install_ctx()
    install()
    rows = []
    try:
        REC.on = True
        picked = [(nm, sd, sc) for nm, sd, sc in benchmark_scripts(days=days)
                  if only is None or f"{nm}#{sd}" in only]
        for i, (name, seed, sc) in enumerate(picked):
            if limit is not None and i >= limit:
                break
            gid = f"{name}#{seed}"
            REC.begin_game(gid)
            probe = replace(sc, loops=loops)
            mm = HeuristicMastermind(seed)
            hp = HeuristicProtagonist(seed)
            state, _ = run_game(probe, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
            fb = any(e.get("event") == "final_battle" for e in state.history)
            if state.winner == "protagonist" and not fb:
                ltw, outcome = state.loop_no, "defense"
            elif fb:
                ltw, outcome = loops + 1, ("fb_win" if state.winner == "protagonist" else "fb_loss")
            else:
                ltw, outcome = loops + 1, "loss"
            rows.append({"game": gid, "loops_to_win": ltw, "outcome": outcome,
                         "calls": len(REC.trace.get(gid, ()))})
            if verbose and (i + 1) % 20 == 0:
                print(f"  ...{i + 1} 局", flush=True)
    finally:
        REC.on = False
        uninstall()
        _uninstall_ctx()
        tie_noise.uninstall_perm()
        for k, v in saved.items():
            setattr(HeuristicProtagonist, k, v)
        _bel._RECOMPUTE_CACHE.clear()
    return {"days": days, "perm": perm, "loops": loops, "knobs": knobs or {},
            "hashseed": os.environ.get("PYTHONHASHSEED"),
            "rows": rows,
            "sites": {k: dict(v) for k, v in REC.sites.items()},
            "seat_hits": {k: v for k, v in REC.seat_hits.items()},
            "moves": REC.moves,
            "trace": REC.trace if keep_trace else {}}


def summarize_sites(res: dict) -> str:
    sites = res["sites"]
    lines = [f'== (a)(c) float 第1キーの決定箇所（{res["days"]}日級 {len(res["rows"])}局 '
             f'perm={res["perm"]} PYTHONHASHSEED={res["hashseed"]}） ==',
             f'{"箇所":48s} {"呼出":>9s} {"float":>9s} {"1ULP":>7s} {"完全同点":>8s} {"明確":>9s}']
    tot = Counter()
    for site, c in _B_SORTED(sites.items(), key=lambda kv: -kv[1].get("ulp", 0)):
        if not c.get("float_calls"):
            continue
        for k, v in c.items():
            tot[k] += v
        lines.append(f'{site:48s} {c["calls"]:9d} {c.get("float_calls", 0):9d} '
                     f'{c.get("ulp", 0):7d} {c.get("exact", 0):8d} {c.get("clear", 0):9d}')
    lines.append(f'{"（合計）":48s} {tot["calls"]:9d} {tot["float_calls"]:9d} '
                 f'{tot["ulp"]:7d} {tot["exact"]:8d} {tot["clear"]:9d}')
    n_nofloat = sum(1 for c in sites.values() if not c.get("float_calls"))
    lines.append(f'  ★float 第1キーを一度も使わなかった決定箇所＝{n_nofloat} 個（除外）')
    lines.append(f'  ★(a) 1 ULP 以内で決着した呼び出し＝**{tot["ulp"]} 件**'
                 f'（＝最下位ビットが結果を決めうる。★これは上界＝実害ではない）')
    lines.append(f'  （参考）完全同点で決着＝{tot["exact"]} 件'
                 f'＝**並び順**で決まる＝`perm` 側の分散源（★1 ULP と混同しない）')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# (b) 2本のトレースの突き合わせ
# ---------------------------------------------------------------------------
def diff_moves(a: dict, b: dict) -> dict:
    """★(b) の本体＝**AI が実際に打った手**の並びを先頭から突き合わせる。

    `sorted` の全順序は消費されない部分まで含む（例＝`[:2]` しか使わない箇所で
    5番目と6番目が入れ替わっても手は変わらない）ので、**`decide` の戻り値**を基準にする。
    最初に手が食い違った席で止め（そこから先は局面がずれて比較不能）、
    その席の中で発火していた「1 ULP タイ」「完全同点」を**帰属先の候補**として並べる。
    """
    out = {"n_games": 0, "diverged": [], "same": [], "by_site": Counter(),
           "by_cls": Counter(), "no_mark": 0}
    for gid, ma in a["moves"].items():
        mb = b["moves"].get(gid)
        if mb is None:
            continue
        out["n_games"] += 1
        n = _B_MIN(len(ma), len(mb))
        first = next((i for i in range(n) if ma[i][1] != mb[i][1] or ma[i][0] != mb[i][0]), None)
        if first is None and len(ma) != len(mb):
            first = n
        if first is None:
            out["same"].append(gid)
            continue
        marks = ma[first][2] if first < n else []
        seat = ma[first][0] if first < n else "(手数違い)"
        # ★帰属＝その席で発火していたタイのうち、**消費されうる切り口**（top2 以内）を優先。
        cand = [m for m in marks if m[1] == "ulp" and m[2] <= 1] or \
               [m for m in marks if m[1] == "ulp"] or \
               [m for m in marks if m[1] == "exact"]
        if cand:
            out["by_site"][cand[0][0]] += 1
            out["by_cls"][cand[0][1] + ("(top2内)" if cand[0][2] <= 1 else "(下位)")] += 1
        else:
            out["no_mark"] += 1
            out["by_site"]["(この席にタイ無し＝上流由来)"] += 1
            out["by_cls"]["タイ無し"] += 1
        out["diverged"].append({"game": gid, "idx": first, "seat": seat,
                                "marks": marks[:6], "n_moves": [len(ma), len(mb)]})
    ra = {r["game"]: r for r in a["rows"]}
    rb = {r["game"]: r for r in b["rows"]}
    out["outcome_flips"] = _B_SORTED(g for g in ra if g in rb
                                     and ra[g]["loops_to_win"] != rb[g]["loops_to_win"])
    out["defense_a"] = sum(1 for r in a["rows"] if r["outcome"] == "defense")
    out["defense_b"] = sum(1 for r in b["rows"] if r["outcome"] == "defense")
    return out


def diff_traces(a: dict, b: dict) -> dict:
    """（参考）`sorted`/`max`/`min` の**結果そのもの**の並びで突き合わせる版。

    ★消費されない下位の入れ替わりも「食い違い」に数えるので **(b) の上界**になる。
    正式な (b) は `diff_moves`（実際に打った手）で数える。
    """
    ta, tb = a["trace"], b["trace"]
    out = {"n_games": 0, "diverged": [], "same": [], "by_site": Counter(),
           "by_cls": Counter(), "keys_differ": 0, "keys_same": 0}
    for gid, sa in ta.items():
        sb = tb.get(gid)
        if sb is None:
            continue
        out["n_games"] += 1
        n = _B_MIN(len(sa), len(sb))
        first = next((i for i in range(n) if sa[i][2] != sb[i][2] or sa[i][0] != sb[i][0]), None)
        if first is None and len(sa) != len(sb):
            first = n
        if first is None:
            out["same"].append(gid)
            continue
        if first < n:
            site, kd, _rd, cls, seat = sa[first]
            out["by_site"][site] += 1
            out["by_cls"][cls] += 1
            if kd == sb[first][1]:
                out["keys_same"] += 1
            else:
                out["keys_differ"] += 1
            out["diverged"].append({"game": gid, "idx": first, "site": site,
                                    "seat": seat, "cls": cls,
                                    "keys_same": kd == sb[first][1],
                                    "n_calls": [len(sa), len(sb)]})
        else:
            out["diverged"].append({"game": gid, "idx": first, "site": "(長さ違い)",
                                    "seat": "?", "cls": "?", "keys_same": None,
                                    "n_calls": [len(sa), len(sb)]})
            out["by_site"]["(長さ違い)"] += 1
    ra = {r["game"]: r for r in a["rows"]}
    rb = {r["game"]: r for r in b["rows"]}
    out["outcome_flips"] = _B_SORTED(g for g in ra if g in rb
                                     and ra[g]["loops_to_win"] != rb[g]["loops_to_win"])
    out["defense_a"] = sum(1 for r in a["rows"] if r["outcome"] == "defense")
    out["defense_b"] = sum(1 for r in b["rows"] if r["outcome"] == "defense")
    return out


def summarize_diff(a: dict, b: dict, d: dict) -> str:
    lines = [f'== (b) 条件を振ると**実際に打つ手**が変わる席 '
             f'（{a["days"]}日級 perm {a["perm"]}→{b["perm"]} '
             f'PYTHONHASHSEED {a["hashseed"]}→{b["hashseed"]}） ==',
             f'  対象 {d["n_games"]} 局 ／ ★**手が食い違った局＝{len(d["diverged"])} 局**'
             f'（最後まで完全一致＝{len(d["same"])} 局）']
    lines.append("  最初に手が変わった席で発火していたタイ（局ごとに1件・帰属候補）:")
    for site, n in d["by_site"].most_common():
        lines.append(f"    {site:52s} {n:4d} 局")
    lines.append("  分類: " + " / ".join(f"{k}={v}" for k, v in d["by_cls"].most_common()))
    lines.append(f'  ★勝敗（loops_to_win）まで動いた局＝**{len(d["outcome_flips"])} 局**'
                 f' 防衛数 {d["defense_a"]} → {d["defense_b"]}')
    if d["outcome_flips"]:
        lines.append("    " + ", ".join(d["outcome_flips"][:30]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ★perm × hash seed の格子（shim 無し＝素のベンチ）
# ---------------------------------------------------------------------------
def run_grid_one(days: int, perm: str, loops: int = 8) -> dict:
    from arena import tie_noise
    from arena.benchmark import run_benchmark
    tie_noise.install_perm(perm)
    try:
        rep = run_benchmark(loops=loops, days=days, verbose=False)
    finally:
        tie_noise.uninstall_perm()
    return {"perm": perm, "days": days,
            "hashseed": os.environ.get("PYTHONHASHSEED"),
            "defense": sum(1 for r in rep["rows"] if r["outcome"] == "defense"),
            "mean": rep["mean_loops_to_win"],
            "rows": {f'{r["script"]}#{r["seed"]}': r["loops_to_win"] for r in rep["rows"]}}


# ---------------------------------------------------------------------------
# ★総括レポート（(a) 席数・(b) 実際に変わった席・perm との関係）
# ---------------------------------------------------------------------------
def seat_counts(res: dict, cut: int = 1) -> dict:
    """(a) を**席**（1決定＝1回の `decide`）の粒度で数える。

    ★`sorted` は**消費されうる切り口**（先頭2件の境目まで＝`top2`）と
      **下位の入れ替わり**（結果に出ない可能性が高い）を**分けて**数える。
      `max`/`min` は決着点が1つしかないので常に `top2` 扱い。
    """
    n_all = n_ulp = n_ulp_top2 = n_exact = 0
    by_site: Counter = Counter()
    by_site_top2: Counter = Counter()
    games_top2 = set()
    for gid, mv in res["moves"].items():
        for seat, _dg, marks in mv:
            n_all += 1
            ulps = [m for m in marks if m[1] == "ulp"]
            if ulps:
                n_ulp += 1
                for m in ulps:
                    by_site[m[0]] += 1
                top2 = [m for m in ulps if m[2] <= cut]
                if top2:
                    n_ulp_top2 += 1
                    games_top2.add(gid)
                    for m in top2:
                        by_site_top2[m[0]] += 1
            if any(m[1] == "exact" for m in marks):
                n_exact += 1
    return {"ulp": n_ulp, "ulp_top2": n_ulp_top2, "exact": n_exact, "all": n_all,
            "by_site": dict(by_site), "by_site_top2": dict(by_site_top2),
            "ulp_games_top2": len(games_top2)}


#: ★**消費形の台帳**＝`sorted(...)` の結果を実際にどこまで使うか。
#:   `(k, as_set)` … 先頭 `k` 件を使う／`as_set=True` なら**集合**として使う（順序不問）。
#:   - `sorted(cand, reverse=True)[:2]`（§72-82 出典の席） → `(2, True)`
#:     ∴ 効くのは **2位と3位の境目（pair==1）かつ候補が3人以上**のときだけ。
#:       `pair==0`（1位/2位）はどちらも集合に入る＝**無害**、`n==2` は全員入る＝**無害**。
#:   - `ranked = sorted(`（`_tt_guards = [n for _p, n in ranked[:3]]` が消費） → `(3, False)`
#:     ＝**並び順のまま**使うので `pair 0,1` は順序を、`pair 2` は当落を動かす。
#:   ★台帳に無い箇所は保守的に `pair <= 1` を実効とみなす。
#:   ★この切り分けをしないと (a) を過大に見積もる（実測で `n==2, pair==0` を踏んだ）。
#: ★キーは**ソース断片**（site 文字列への部分一致）。行番号は使わない（上の `_site` 参照）。
CONSUMERS: tuple[tuple[str, tuple[int, bool]], ...] = (
    ("sorted(cand, reverse=True)[:2]", (2, True)),
    ("ranked = sorted(", (3, False)),
)


def _consumer_of(site: str) -> tuple[int, bool]:
    for frag, v in CONSUMERS:
        if frag in site:
            return v
    return (2, False)      # 台帳に無い箇所＝保守的に pair<=1 を実効とみなす


def _is_effective(site: str, h: dict) -> bool:
    if "pair" not in h:
        return True                          # max/min＝決着点は1つ＝常に実効
    k, as_set = _consumer_of(site)
    pair, n = h["pair"], h.get("n", 0)
    if pair > k - 1:
        return False                         # 消費されない下位の入れ替わり
    if pair == k - 1:
        return n > k                         # 当落の境目（候補が k 人以下なら全員入る）
    return not as_set                        # 内部の並び順（集合として使うなら無害）


def effective_cut_seats(res: dict) -> dict:
    """★(a) の**実効**値＝「その席の決定を実際に変えうる 1 ULP タイ」だけを数える。

    `seat_hits`（席ごとの記録・箇所あたり40件まで）の `pair`／候補数 `n` を
    `CONSUMERS` の消費形と突き合わせる。
    """
    out: dict[str, list] = defaultdict(list)
    for site, hits in res.get("seat_hits", {}).items():
        for h in hits:
            if _is_effective(site, h):
                out[site].append(h)
    return dict(out)


def _rows(res: dict) -> dict:
    return {r["game"]: r["loops_to_win"] for r in res["rows"]}


def report(paths: list[str]) -> str:
    """`corpus --out` の JSON 群を読んで (a)(b)＋`perm` との関係をまとめる。"""
    runs = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        runs[(r["days"], r["perm"], str(r["hashseed"]))] = r
    days_set = _B_SORTED({k[0] for k in runs})
    out = []
    for d in days_set:
        base = runs.get((d, "id", "0"))
        if base is None:
            continue
        n = len(base["rows"])
        out.append(f"\n########## {d}日級（{n}局・perm=id が基準） ##########")
        out.append("--- (a) 1 ULP 以内で決着した席（★上界＝『危うい』だけで実害ではない） ---")
        for sd in _B_SORTED({k[2] for k in runs if k[0] == d and k[1] == "id"}, key=int):
            r = runs[(d, "id", sd)]
            sc = seat_counts(r)
            eff = effective_cut_seats(r)
            n_eff = sum(len(v) for v in eff.values())
            out.append(f"  PYTHONHASHSEED={sd:>2}: 全決定席={sc['all']:6d} ／ "
                       f"1ULPタイを含む席={sc['ulp']:4d} ／ "
                       f"★うち**消費されうる切り口**(先頭2件の境目まで)={sc['ulp_top2']:4d} 席"
                       f"（{sc['ulp_games_top2']}局） ／ 完全同点を含む席={sc['exact']:6d}")
            out.append(f"      ★★実効（決定を実際に変えうる切り口だけ）＝**{n_eff} 席**"
                       + ("".join(f"\n         {h['game']} {h['seat']} pair={h['pair']} n={h['n']}"
                                  for v in eff.values() for h in v) if n_eff else ""))
            for site, k in _B_SORTED(sc["by_site"].items(), key=lambda kv: -kv[1]):
                t2 = sc["by_site_top2"].get(site, 0)
                out.append(f"      {site:52s} 1ULP {k:4d} 席（うち切り口 {t2} 席）")
        out.append("--- (b) ★条件を振ると**実際に打つ手**が変わる席（＝実害） ---")
        for sd in _B_SORTED({k[2] for k in runs if k[0] == d and k[1] == "id"}, key=int):
            if sd == "0":
                continue
            dd = diff_moves(base, runs[(d, "id", sd)])
            tr = diff_traces(base, runs[(d, "id", sd)])
            out.append(f"  hash seed 0 vs {sd}: ★手が食い違った局={len(dd['diverged']):3d}/{n}"
                       f" ／ 最後まで完全一致={len(dd['same']):3d}"
                       f" ／ ★勝敗まで動いた局={len(dd['outcome_flips']):3d}"
                       f" ／ 防衛 {dd['defense_a']}→{dd['defense_b']}"
                       f"  （参考：並び順まで見た上界={len(tr['diverged'])} 局）")
            out.append("      帰属: " + " / ".join(f"{k}={v}" for k, v in dd["by_cls"].most_common()))
            for site, c in dd["by_site"].most_common(6):
                out.append(f"      {site:52s} {c:4d} 局")
            if dd["outcome_flips"]:
                out.append("      勝敗が動いた局: " + ", ".join(dd["outcome_flips"][:20]))
        out.append("--- ★perm との関係（★同一視しない＝別の分散源） ---")
        rb = _rows(base)
        hash_flip_union: set = set()
        for sd in _B_SORTED({k[2] for k in runs if k[0] == d and k[1] == "id"}, key=int):
            if sd != "0":
                rh = _rows(runs[(d, "id", sd)])
                hash_flip_union |= {g for g in rb if rb[g] != rh.get(g)}
        for pm in _B_SORTED({k[1] for k in runs if k[0] == d and k[1] != "id"}):
            pr = runs.get((d, pm, "0"))
            if pr is None:
                continue
            dp = diff_moves(base, pr)
            rp = _rows(pr)
            perm_flips = {g for g in rb if rb[g] != rp.get(g)}
            out.append(f"  perm id vs {pm}（hash seed 0 固定）: 手が食い違った局={len(dp['diverged']):3d}"
                       f" ／ ★勝敗まで動いた局={len(perm_flips):3d}"
                       f" ／ 防衛 {dp['defense_a']}→{dp['defense_b']}")
            out.append("      帰属: " + " / ".join(f"{k}={v}" for k, v in dp["by_cls"].most_common()))
            ul = sum(v for k, v in dp["by_cls"].items() if k.startswith("ulp"))
            out.append(f"      ★perm 側の食い違いのうち **1 ULP タイで説明できる局＝{ul}**"
                       f"／説明できない局＝{len(dp['diverged']) - ul}")
            inter = perm_flips & hash_flip_union
            out.append(f"      勝敗が動いた局の重なり: perm({pm})={len(perm_flips)} "
                       f"∩ hash seed 総和={len(hash_flip_union)} → 共通={len(inter)}"
                       f" ＝ perm 側の {100.0 * len(inter) / _B_MAX(1, len(perm_flips)):.0f}%")
    return "\n".join(out)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-273 フェーズ0：1 ULP タイの射程を数える")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("corpus")
    c.add_argument("--days", type=int, default=3)
    c.add_argument("--perm", default="id")
    c.add_argument("--loops", type=int, default=8)
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--out", default=None)
    c.add_argument("--no-trace", action="store_true")
    c.add_argument("--knobs", default=None,
                   help="反実仮想: 例 B256_UNREACHABLE_SKIP=0,B262_BELIEF_BOUND=0")
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    g = sub.add_parser("grid1")
    g.add_argument("--days", type=int, default=3)
    g.add_argument("--perm", default="id")
    g.add_argument("--out", default=None)
    rp = sub.add_parser("report")
    rp.add_argument("paths", nargs="+")
    rk = sub.add_parser("risk")
    rk.add_argument("path")
    ce = sub.add_parser("census")
    ce.add_argument("--ran", default=None, help="corpus --out の JSON（発火した箇所と突き合わせる）")
    args = ap.parse_args(argv)

    if args.cmd == "report":
        print(report(args.paths))
        return 0
    if args.cmd == "risk":
        with open(args.path, encoding="utf-8") as f:
            print(risk_table(json.load(f)))
        return 0
    if args.cmd == "census":
        rows = static_census()
        ran = None
        if args.ran:
            with open(args.ran, encoding="utf-8") as f:
                ran = json.load(f)["sites"]
        print(summarize_census(rows, ran))
        return 0

    if args.cmd == "corpus":
        print(f"★PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')}"
              f"（この値で走った結果です）perm={args.perm} days={args.days}", flush=True)
        kn = None
        if args.knobs:
            kn = {}
            for kv in args.knobs.split(","):
                k, v = kv.split("=")
                kn[k.strip()] = bool(int(v))
            print(f"★反実仮想の切替口: {kn}（測定専用・既定は変えない）", flush=True)
        res = run_corpus(days=args.days, perm=args.perm, loops=args.loops,
                         limit=args.limit, keep_trace=not args.no_trace, knobs=kn)
        print(summarize_sites(res))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            print(f"  → {args.out}")
        return 0
    if args.cmd == "diff":
        with open(args.a, encoding="utf-8") as f:
            a = json.load(f)
        with open(args.b, encoding="utf-8") as f:
            b = json.load(f)
        print(summarize_diff(a, b, diff_traces(a, b)))
        return 0
    if args.cmd == "grid1":
        r = run_grid_one(days=args.days, perm=args.perm)
        print(f'days={r["days"]} perm={r["perm"]} PYTHONHASHSEED={r["hashseed"]} '
              f'防衛={r["defense"]} 平均={r["mean"]:.3f}', flush=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False)
        return 0
    return 1



# ---------------------------------------------------------------------------
# (c) ★静的な洗い出し＝`agents/` 全体の「選択」呼び出しを AST で列挙する
# ---------------------------------------------------------------------------
def static_census(root: str = "agents") -> list[dict]:
    """`sorted()` / `max()` / `min()` のうち **候補から1つ（または上位k個）を選ぶ形**を列挙。

    ★分類の基準（実行時の (a)(b) と突き合わせるための静的な母集合）：
    - `select` … 引数が**単一のイテラブル**＝候補集合からの選択（＝タイブレークが問題になる）。
      `key=` の有無は問わない（`sorted(cand)` のようにタプルを直接並べる形も含む）。
    - `clamp`  … `max(0, x)` / `min(a, b)` の**多引数**形＝値の丸め＝**選択ではない**。
    - `agg`    … `max(d.values())` のように**値だけ**を取り出す形（`key=` なし・
      戻り値を選択に使わない）も母集合に入れる（実行時に float 第1キーなら数える）。
    """
    import ast
    from pathlib import Path
    out = []
    for p in _B_SORTED(Path(root).rglob("*.py")):
        if "legacy" in p.parts or p.name in ("debug.py",) or p.name.startswith("llm_"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            fn = node.func.id
            if fn not in ("sorted", "max", "min"):
                continue
            kw = {k.arg for k in node.keywords if k.arg}
            kind = "select" if len(node.args) == 1 else "clamp"
            if kind == "select" and "key" not in kw and fn in ("max", "min"):
                kind = "agg"
            out.append({"file": str(p), "line": node.lineno, "fn": fn,
                        "kind": kind, "has_key": "key" in kw})
    return out


def risk_table(res: dict) -> str:
    """★(c) 残存リスクの層別＝float 第1キーの決定箇所を**キーの形**で分ける。

    - `tuple` … `(確率, 名前)` の形＝**完全同点は名前で決着＝決定的**。
      危ないのは **1 ULP だけ違う**場合のみ（＝本チケットの本題）。
    - `scalar` … 素の float ＝`sorted` は安定・`max` は先頭勝ち＝
      **完全同点は入力の並び順で決まる**（＝`perm` / 反復順の分散源。1 ULP とは別問題）。
    """
    lines = [f'== (c) 残存リスクの層別（{res["days"]}日級 {len(res["rows"])}局 '
             f'perm={res["perm"]} PYTHONHASHSEED={res["hashseed"]}） ==',
             f'{"箇所":52s} {"キー形":>6s} {"float呼出":>9s} {"1ULP":>6s} {"切り口":>6s} {"完全同点":>8s}']
    n_t = n_s = n_t_tie = n_s_tie = 0
    for site, c in _B_SORTED(res["sites"].items()):
        if not c.get("float_calls"):
            continue
        shape = "tuple" if c.get("key_tuple", 0) >= c.get("key_scalar", 0) else "scalar"
        cut = c.get("ulp_top2", 0) if ":sorted" in site else c.get("ulp", 0)
        lines.append(f'{site:52s} {shape:>6s} {c["float_calls"]:9d} '
                     f'{c.get("ulp", 0):6d} {cut:6d} {c.get("exact", 0):8d}')
        if shape == "tuple":
            n_t += 1
            n_t_tie += 1 if c.get("ulp") else 0
        else:
            n_s += 1
            n_s_tie += 1 if c.get("ulp") else 0
    lines.append(f'  ★タプル第1要素で決着する箇所＝{n_t} 個'
                 f'（うち 1 ULP タイが実測された＝{n_t_tie} 個／'
                 f'一度も出ていない＝{n_t - n_t_tie} 個＝「たまたま出ていないだけ」の残存）')
    lines.append(f'  素の float キーの箇所＝{n_s} 個'
                 f'（うち 1 ULP タイが実測された＝{n_s_tie} 個）'
                 f'／★こちらは完全同点が**並び順**で決まる＝`perm` 側の話')
    return "\n".join(lines)


def summarize_census(rows: list[dict], ran: dict | None = None) -> str:
    """★母集合の切り分け（実行時の (a)(b) と突き合わせるための静的な台帳）。

    - `select` … **候補から選ぶ**形＝タイブレークが結果を変えうる＝**本チケットの対象**。
    - `agg`    … `max(d.values())` のように**数値そのもの**を返す形＝
      どの要素が勝ったかは結果に出ない＝**構造的に対象外**（並び順にも hash seed にも依存しない）。
    - `clamp`  … `max(0, x)` の多引数形＝値の丸め＝**対象外**。
    """
    c = Counter(f'{r["fn"]}/{r["kind"]}' for r in rows)
    lines = [f"== (c) `agents/` 静的洗い出し（{len(rows)} 呼び出し） ==",
             "  " + " ".join(f"{k}={v}" for k, v in _B_SORTED(c.items()))]
    sel = [r for r in rows if r["kind"] == "select"]
    agg = [r for r in rows if r["kind"] == "agg"]
    lines.append(f"  ★対象＝候補からの選択（select）＝**{len(sel)} 箇所**"
                 f"／構造的に対象外＝値取り(agg) {len(agg)} ＋ 値の丸め(clamp) "
                 f"{len(rows) - len(sel) - len(agg)} 箇所")
    if ran is not None:
        fired, floaty = set(), set()
        for site, cnt in ran.items():
            mod, ln, _k = site.rsplit(":", 2)
            key = (mod.replace(".", "/") + ".py", int(ln))
            fired.add(key)
            if cnt.get("float_calls"):
                floaty.add(key)
        hit = [r for r in sel if (r["file"], r["line"]) in fired]
        fl = [r for r in sel if (r["file"], r["line"]) in floaty]
        lines.append(f"  本コーパスで発火した select＝{len(hit)} 箇所／"
                     f"そのうち **float を第1キーにしていた＝{len(fl)} 箇所**"
                     f"（＝(a)(b) を数える対象）／未発火＝{len(sel) - len(hit)} 箇所")
        for r in _B_SORTED(sel, key=lambda r: (r["file"], r["line"])):
            k = (r["file"], r["line"])
            if k in floaty:
                lines.append(f"    ★float第1キー {r['file']}:{r['line']} {r['fn']}")
        for r in _B_SORTED(sel, key=lambda r: (r["file"], r["line"])):
            k = (r["file"], r["line"])
            if k in fired and k not in floaty:
                lines.append(f"    発火/非float {r['file']}:{r['line']} {r['fn']}")
        for r in _B_SORTED(sel, key=lambda r: (r["file"], r["line"])):
            if (r["file"], r["line"]) not in fired:
                lines.append(f"    未発火     {r['file']}:{r['line']} {r['fn']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(_cli())
