# -*- coding: utf-8 -*-
"""AIデバッグプローブ — 全候補スコアと内部推定のダンプ（挙動は本体と完全同一）。

セッションのたびに手書きしていた「スパイmax」「内部推定ダンプ」の恒久化。
使い方（対局まるごと）:

    from agents.debug import probe_game, format_records
    state, hp_recs, mm_recs = probe_game(script, seed=0)
    print(format_records(hp_recs, loop=2, top=5))

使い方（自分でループを組む）:

    hp = ProbedProtagonist(seed)
    mm = ProbedMastermind(seed)
    run_game(script, {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    hp.records / mm.records に (文脈, 上位候補スコア, 選択, 内部推定) が溜まる。

★注意：測定は PYTHONHASHSEED=0 で行うこと（スコア同点のタイブレークが
  setのハッシュ順に依存し、プロセスごとに結果が揺れる）。
"""
from __future__ import annotations

import agents.heuristic_protagonist as _hp_mod

from .heuristic import HeuristicMastermind
from .heuristic_protagonist import HeuristicProtagonist

_builtin_max = max

# ProbedProtagonist が decide ごとに記録する内部推定（存在すれば）
_ESTIMATES = (
    "_keyperson", "_killer", "_tt_guards", "_friend_guards",
    "_killer_suspects", "_kuromaku_suspects", "_kuromaku_cands",
    "_cultist_suspects", "_cultist_cands", "_sk_suspects", "_sk_cands",
    "_observed_defeat_board", "_loop_lost", "_experiment", "_kp_doomed",
)


def _snap(val):
    if isinstance(val, (set, frozenset)):
        return sorted(val)
    if isinstance(val, list):
        return list(val)
    return val


class ProbedProtagonist(HeuristicProtagonist):
    """全候補スコアを記録する主人公。スコア計算は本体そのもの＝挙動同一。

    仕組み：score/gscore は decide 内のクロージャで直接呼べないため、モジュール
    グローバルの max を一時的に差し替えて `max(options, key=score)` を横取りする
    （モジュール名前空間の max はビルトインより先に解決される）。
    """

    def __init__(self, seed: int = 0, top: int = 8):
        super().__init__(seed)
        self.records: list[dict] = []
        self._top = top

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        cap: dict = {}

        def spymax(*args, **kw):
            if (args and isinstance(args[0], list) and "key" in kw
                    and "scored" not in cap):
                key = kw["key"]
                cap["scored"] = sorted(((key(o), o) for o in args[0]),
                                       key=lambda x: -x[0])
            return _builtin_max(*args, **kw)

        had = "max" in _hp_mod.__dict__
        prev = _hp_mod.__dict__.get("max")
        _hp_mod.max = spymax
        try:
            chosen = super().decide(view, decision, options)
        finally:
            if had:
                _hp_mod.max = prev
            else:
                del _hp_mod.max
        rec = {
            "loop": view.get("loop"), "day": view.get("day"),
            "seat": view.get("seat"), "decision": decision,
            "chosen": chosen,
            "scored": cap.get("scored", [])[: self._top],
            "estimates": {k: _snap(getattr(self, k)) for k in _ESTIMATES
                          if hasattr(self, k)},
        }
        self.records.append(rec)
        return chosen


class ProbedMastermind(HeuristicMastermind):
    """全候補スコアを記録する脚本家。_pick に渡る score を横取りする（挙動同一：
    rng の消費は super()._pick 内のみ＝非プローブ時と同一系列）。"""

    def __init__(self, seed: int = 0, top: int = 8,
                 params: dict | None = None):
        super().__init__(seed, params=params)
        self.records: list[dict] = []
        self._top = top
        self._ctx: dict = {}
        self._last_analysis: dict = {}

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        self._ctx = {"loop": view.get("loop"), "day": view.get("day"),
                     "decision": decision}
        return super().decide(view, decision, options)

    def _analyze(self, view: dict) -> dict:
        a = super()._analyze(view)
        self._last_analysis = a   # 各手番の勝ち筋分析を捕捉（思考デバッグ表示用・挙動同一）
        return a

    def _pick(self, options: list[dict], score) -> dict:
        scored = sorted(((score(o), o) for o in options), key=lambda x: -x[0])
        chosen = super()._pick(options, score)
        self.records.append({**self._ctx, "chosen": chosen,
                             "scored": scored[: self._top],
                             "analysis": _mm_analysis_summary(self._last_analysis)})
        return chosen


def probe_game(script, seed: int = 0, mastermind=None):
    """1対局をプローブ付きで実行。(state, 主人公records, 脚本家records) を返す。"""
    from sim import run_game
    hp = ProbedProtagonist(seed)
    mm = mastermind if mastermind is not None else ProbedMastermind(seed)
    state, _log = run_game(script, {"mastermind": mm, "p1": hp,
                                    "p2": hp, "p3": hp})
    return state, hp.records, getattr(mm, "records", [])


def _fmt_option(o: dict) -> str:
    if "card" in o:
        return f'{o["card"]}→{o.get("target")}'
    if "action" in o:
        return f'{o["action"]}' + (f'→{o["target"]}' if o.get("target") else "")
    return str(o)


def format_records(records: list[dict], loop: int | None = None,
                   day: int | None = None, decision: str | None = None,
                   top: int = 5, with_estimates: bool = True) -> str:
    """records を人間可読のテキストに（フィルタつき）。"""
    lines: list[str] = []
    last_est = None
    for r in records:
        if loop is not None and r.get("loop") != loop:
            continue
        if day is not None and r.get("day") != day:
            continue
        if decision is not None and r.get("decision") != decision:
            continue
        head = (f'L{r.get("loop")}D{r.get("day")} {r.get("seat") or "mm"}'
                f'/{r.get("decision")}: 選択={_fmt_option(r["chosen"])}')
        lines.append(head)
        for s, o in r.get("scored", [])[:top]:
            mark = "★" if o == r["chosen"] else "  "
            lines.append(f"  {mark}{s:7.1f} {_fmt_option(o)}")
        est = r.get("estimates")
        if with_estimates and est and est != last_est:
            interesting = {k: v for k, v in est.items()
                           if v not in (None, [], False)}
            if interesting:
                lines.append("  推定: " + "  ".join(
                    f"{k.lstrip('_')}={v}" for k, v in interesting.items()))
            last_est = est
    return "\n".join(lines)


# --- 脚本家AIの思考（ProbedMastermind）を人間可読にする（主人公プレイのデバッグ表示用） ---
_MM_PATH_JP = {
    "board": "ボード敗北条件（盤面暗躍でボードを敗北に）",
    "kp": "キーパーソン暗躍（僕と契約=暗躍2で即勝ち／殺人計画=暗躍2+キラー）",
    "killer4": "主人公殺害（キラー暗躍4）",
    # ★A-74：未来改変プラン族（コストの通貨は暗躍でなく**不安**＝犯人を臨界へ運ぶ手数）
    "butterfly": "蝶の羽ばたきの発生（未来改変プラン＝発生自体がループ終了時の敗北条件）",
    "tt": "タイムトラベラーの敗北宣言（最終日に友好2以下）",
}


def _mm_analysis_summary(a: dict) -> dict:
    """_analyze の結果から、思考表示に使う戦略フィールドだけを抜き出す（記録を軽く保つ）。"""
    if not a:
        return {}
    return {k: _snap(a.get(k)) for k in
            ("keyperson", "killer", "funded", "path_costs",
             "goal_boards", "supply", "locked")}


def mastermind_mind_md(records: list[dict], loop: int | None = None,
                       day: int | None = None, top: int = 6) -> str:
    """ProbedMastermind の records を markdown 化（脚本家AIの思考デバッグ）。

    その手番の「勝ち筋読み」（どの敗北ルートにあと何個・供給がいくつ・どれに資金を出すか）と、
    各セット手のスコア（高いほど優先）を出す＝どの手が何点で、なぜ高い/低いかが読める。
    """
    recs = [r for r in records if r.get("decision") == "set_card"
            and (loop is None or r.get("loop") == loop)
            and (day is None or r.get("day") == day)]
    if not recs:
        return "_（この時点の脚本家AIのセット判断はまだありません）_"
    lines: list[str] = []
    a = recs[0].get("analysis") or {}
    pc = a.get("path_costs") or {}
    funded = set(a.get("funded") or [])
    if pc:
        lines.append("**脚本家AIの勝ち筋読み**（この手番の戦略）")
        lines.append(f"- 残供給（このループで置ける暗躍の目安）：**{a.get('supply', '?')}**")
        for p, c in sorted(pc.items(), key=lambda kv: kv[1]):
            mark = "✅ **資金投入（この筋を進める）**" if p in funded else "⏸ 見送り（届かない/高い）"
            lines.append(f"- {_MM_PATH_JP.get(p, p)}：あと **{c:.1f}** 個　→ {mark}")
        _extra = []
        if a.get("keyperson"):
            _extra.append(f"KP＝{a['keyperson']}")
        if a.get("killer"):
            _extra.append(f"キラー＝{a['killer']}")
        if a.get("goal_boards"):
            _extra.append(f"敗北ボード＝{'・'.join(a['goal_boards'])}")
        if a.get("locked"):
            _extra.append("勝ち確モード（情報流出を抑える）")
        if _extra:
            lines.append("- " + "／".join(_extra))
    lines.append("\n**各セット手のスコア**（★＝実際に伏せた手。高い順に優先／同点はタイブレーク）")
    for r in recs:
        ch = r["chosen"]
        lines.append(f"- **{r.get('day')}日目**：〈{_fmt_option(ch)}〉を伏せた")
        for s, o in r.get("scored", [])[:top]:
            mark = "★" if o == ch else "・"
            lines.append(f"    {mark} `{s:6.1f}`　{_fmt_option(o)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FI-6：友好投資スコアの内訳（🛠開発者モード表示用・表示専用＝意思決定に触れない）
# ---------------------------------------------------------------------------
def invest_breakdown(hp, view: dict, *, autosync: bool = True) -> list[dict]:
    """主人公の友好投資スコアを「なぜその値か」に分解して返す（表示専用）。

    ★呼び出し契約（A-40 接続で実際に踏んだ穴・2026-07-19）：hp は**その view に `_sync` 済み**で
    あること。未同期（`_belief` 未生成）の新品エージェントで呼ばれると内部推定が無く落ちるため、
    `autosync=True`（既定）なら内部で `hp._sync(view)` して整えてから計算する（UI からは同期を
    意識せず呼べる）。表示専用なので同期に失敗した場合は空リストを返す（画面を壊さない）。

    ★`_compute_invest` と**同一の計算**を再現する（別式を作らない＝probe整合・ドリフト防止）：
        score = value/(1+need) + _TEMPO_TIEBREAK × tempo
    value には FI-5 step1/2 の reach が既に畳み込まれている（巫女=base×居場所／役職開示×居場所／
    不安除去×到達可能性）ので、内訳では reach 係数を**別途そのまま**併記して読めるようにする。
    返す行＝投資候補（need>0 の実装済み能力）ごと。score 降順。
    """
    from sim.abilities import is_implemented
    from engine.data import ability_kind, goodwill_abilities_of

    if autosync and getattr(hp, "_belief", None) is None:
        try:
            hp._sync(view)
        except Exception:
            return []          # 表示専用＝同期できない view では静かに空（画面を壊さない）
    used = view.get("used_cards", {})
    has_plus2 = (isinstance(used, dict)
                 and any("友好+2" not in used.get(s, []) for s in ("p1", "p2", "p3")))
    loops_left = (view["loops_total"] - view["loop"] + 1) if "loops_total" in view else None
    is_fb = bool(getattr(hp, "_loop_lost", False))
    rows: list[dict] = []
    for c in view.get("characters", []):
        n = c.get("name")
        if not c.get("alive") or c.get("area") is None:
            continue
        for ab in goodwill_abilities_of(n) or []:
            name = ab["name"]
            if not is_implemented(n, name):
                continue
            need = ab["hearts"] - c.get("goodwill", 0)
            if need <= 0:
                continue
            val = hp._ability_value(n, name, None, view)
            kind = ability_kind(n, name) or ""
            tempo = hp.invest_tempo(n, name, need, view, kind, has_plus2=has_plus2,
                                    is_fb=is_fb, loops_left=loops_left)
            rows.append({
                "character": n, "ability": name, "kind": kind,
                "hearts": ab["hearts"], "need": need,
                "value": val,
                "loc_reach": hp._location_reach(n, name, view),
                "threat_reach": hp._threat_reach(n, name, view),
                "has_target": hp._ability_has_target(n, name, view),
                "tempo": tempo,
                "tie": hp._TEMPO_TIEBREAK * tempo,
                "score": val / (1.0 + need) + hp._TEMPO_TIEBREAK * tempo,
            })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def format_invest_breakdown(rows: list[dict], top: int = 8) -> str:
    """invest_breakdown を人間可読に（🛠開発者モードで1行呼び出しできる形）。"""
    if not rows:
        return "（投資候補なし＝全能力が発動可能 or 対象不在）"
    out = ["score = value/(1+need) + tie（tempoはタイブレーク限定＝valを割らない）",
           "  score  value  /need  tie(tempo)  居場所 到達  対象  kind        キャラ・能力"]
    for r in rows[:top]:
        out.append(
            f"  {r['score']:6.2f} {r['value']:6.1f}  /{r['need']:<4d}"
            f" {r['tie']:5.2f}({r['tempo']:.2f})"
            f"  {r['loc_reach']:.2f}  {r['threat_reach']:.2f}"
            f"  {'有' if r['has_target'] else '無'}  {r['kind']:<10s}"
            f"  {r['character']}『{r['ability']}』♡{r['hearts']}")
    return "\n".join(out)
