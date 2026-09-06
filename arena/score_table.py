# -*- coding: utf-8 -*-
"""棋譜再生の点数表（T4・汎用・**計測のみ**）＝人間戦の決定録を固定教材にし、脚本家AIの各決定を
局面ごとに再生して「options 全席の点数・記録の選択・現行AIならどれを選ぶか」を表にする。

出典＝`docs/提案_設計論点3つ_Fable切替_2026-09-04.md` §1.2 ②／§1.3-1。原型＝`lane/b291-partial` の
`arena/b291_score_table.py`（B-291 専用）と §72-136 の「L2D1 を3腕で再生」。

★本モジュールは `agents/` `sim/` `engine/` `rules/` を import 以外で1バイトも触らない。
★**嘘をつかない**：
  - 再生（記録の選択列を現行の `sim.flow.run_loop` に流す）が途中で止まった棋譜は
    「どこで止まったか（ループ・日・フェイズ・席・決定種・理由）」を出して**棄権**する。止まる前の
    決定ぶんはそのまま出す（停止後は出さない）。停止の種類：
      illegal   ＝記録の選択が現行の合法手に無い（例＝B-29x ③ 従者の初期エリアは一度きり＝
                  旧棋譜の L2 以降の `loop_start_area` が現行では発生しない）
      desync    ＝現行 flow が要求した決定種と記録の次の決定種が違う（収録後に決定種が増減した。
                  例＝B-278 `doctor_unrest_mode`）／脚本家の記録が尽きたのに他席に記録が残る
      exhausted ＝記録の終端（途中保存の棋譜）＝停止前の決定はすべて有効
      partial   ＝★T4b：記録の先頭が L1D1 より後（クラウド復元後の続き＝先頭の決定が欠落）＝再生に入らない
      rule_changed＝★T4b：収録後の規則変更で現行 flow では再現できない記録（従者の初期エリアをループ途中で
                  変えた／医者の宣言が拒否されて履歴から決まらない）＝互換層でも復元不能＝棄権
  - ★T4b 互換層（`arena/replay_compat.py`）＝収録後に決定種が増減した旧棋譜を、**記録の公開履歴から
    一意に復元できる決定だけ**補う／読み飛ばす。補った事実は `ReplayResult.compat_notes` に残す
    （黙って直さない）。復元できない座標は従来どおり棄権する。
  - 点数の内訳は**既存の採点関数が返す範囲だけ**（`_score_set`／`_plus2_penalty`／`_b286_penalty`／
    `_b289_bonus`／`_b293_move_is_void`＝`HeuristicMastermind.decide` の `_sc` と同一の式）。
    採点関数の無い決定種（`goodwill_refuse`・`cultist_ignore`・`scholar_counter`）は
    「採点関数なし（規則で決定）」と明記し、現行AIの選択だけを出す＝内訳を捏造しない。

2つの材料（`--source`）：
  replay（既定）＝記録の選択列で局面を**現行コードで再構成**し、脚本家の各決定で (1) 現行の options 全席を
      採点、(2) 現行AI（`HeuristicMastermind`）の `decide()` を実際に呼んで選択を採り（rng も本番と同じ順で
      消費）、(3) 局面は記録どおりに進める（返すのは記録の選択）。再生した options が記録の options と違えば
      `options 不一致` として数える（収録後のコード変更の目印）。
      対局の枠（総ループ数・最後の戦いの有無）は記録から推定する（アプリは script.loops と別に
      4ループ打ち切り＋延長で回す＝`arena/loopcap`）。完走時は最終状態・公開履歴を meta と照合する。
  recorded＝記録の view/options を**そのまま**現行AIに通す（§72-129／§72-136 と同じ作法）。局面は
      再構成しない＝収録時コードの候補列で採点する。再生が止まる棋譜でも全決定を出せるが、
      view の形式差で採点できない決定は「採点不能」と出す。
  auto＝replay で止まったら、残りの脚本家決定を recorded で補う（各決定に材料を明記）。

★T11（2026-09-06）＝**主人公席（p1/p2/p3）にも拡張**（`--side mm|prot|both`・既定 both）。
  出典＝`docs/索引_疑問手局面_人間戦棋譜_2026-09-06.md` §0・§6-3（索引の 71/133 局面が主人公側＝従来は対象外）。
  - 主人公の各決定（`set_card`／`goodwill_ability`／`doctor_unrest_mode`／`final_battle_guess`）で、
    現行 `HeuristicProtagonist(seed)`（p1〜p3 で1体を共有＝`arena/play_vs_ai._ai_protagonists` と同じ）の
    `decide()` を実際に呼び、**options 全席の点数**と現行AIの選択、記録の選択（人間 or 旧AI）を表にする。
  - ★点数は**採点式を写さない**。`agents/debug.ProbedProtagonist` と同じ仕組みで、`decide()` 内の
    `max(options, key=score)` を横取りして **AI が実際に見た採点器**を全席に当てる（観測口＝
    モジュール名前空間の `max`／計画席は `_plan_turn` に渡る採点器／どちらも通らない席は
    `B100_HOOK`（計測専用フック）の採点器＝**事後採点**と明記する）。
  - 「語彙」列＝`_plan_recs`（防御プランナーの加点＝`plan+88.0` 等）と `card_effect.noop_reason`
    （公開情報から証明可能な空振り／自滅の理由）。★noop の材料は監査側の `b100_mix.noop_ctx_for`＋板の
    伏せ位置（G1〜G7）＝本体の G8〜G10（友好の空振り等）の材料は持たない＝**語彙列に出ない noop が
    ありうる**（点数には本体の判定が入っている）。
  - 採点器を持たない決定種（`doctor_unrest_mode`＝options[0]／`final_battle_guess`＝belief の argmax）は
    「採点関数なし」と明記し現行AIの選択だけを出す。
  - ★席間協調フラグ（`--prot-flags record|ai`・既定 record）：現行AIは `decide()` の最後に
    `_apply_seat_flags(view, best)`（暗躍禁止の1ターン1枚・ピンの残数・VIP注入の席＝単一ソース）を
    **自分の選んだ手**で更新する。人間が主人公の棋譜では先席の手が現行AIの選択と食い違うので、
    record（既定）＝食い違った席では B-186 の `_b186_snapshot`／`_b186_restore` でフラグを `_sync` 直後
    （ターン境界の初期化後・採点前）へ戻し、
    **記録の手**で `_apply_seat_flags` を当て直す（＝後席は記録の局面どおりの前提で採点される。
    既存メソッドを呼ぶだけ＝agents/ 非接触）。ai＝何もしない（現行AIが自分の手を打った前提のまま＝
    人間の暗躍禁止が「1ターン2枚目」扱いで -100 になる等の歪みが後席に残る）。どちらでも
    先席が食い違ったターンの後席には `prior_diverged` 印を付ける。食い違いの無い席・棋譜では両者は同一。
    ★record でも `_turn_plan`（先頭席が3席ぶん一括計画する過剰需要ターン）は現行AIの計画のまま。
  - `--side mm` の出力は T4/T4b と **bit 同一**（主人公席を一切呼ばない＝`tests/test_t11_score_table_prot.py`）。
  - 棄権の作法は従来どおり（illegal／desync／exhausted／partial／rule_changed）。主人公席で記録に無い
    決定種を現行 flow が要求すれば desync で棄権する（捏造しない）。

CLI:
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.score_table \
      docs/feedback_logs/ランダムBTX5d_seed1_ユーザー主人公_2026-09-02.jsonl --loop 2 --day 1 --source auto
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.score_table <jsonl> --format tsv > out.tsv
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.score_table --smoke docs/feedback_logs/*.jsonl
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.score_table <jsonl> --side prot --loop 2 --day 1 --source auto
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field, replace

#: 採点関数を持つ決定種。
SCORED_DECISIONS = ("set_card", "mastermind_ability", "turn_end_ability",
                    "incident_choice", "loop_start_area")
#: 採点関数が無く述語で決まる決定種（現行AIの選択だけ出す＝内訳を捏造しない）。
UNSCORED_NOTE = "採点関数なし（規則で決定）"
SOURCES = ("replay", "recorded", "auto")
#: ★T11：採点する席。mm＝脚本家だけ（T4/T4b と bit 同一）／prot＝主人公だけ／both＝両方（既定）。
SIDES = ("mm", "prot", "both")
PROT_SEATS = ("p1", "p2", "p3")
#: 主人公側で採点器（`max(options, key=...)`）を持つ決定種。
PROT_SCORED_DECISIONS = ("set_card", "goodwill_ability")
PROT_UNSCORED_NOTE = "採点関数なし（規則／推定で決定＝現行AIの選択だけ）"
#: ★T11：先席が食い違った席の席間協調フラグの扱い（record＝記録の手で当て直す（既定）／ai＝現行AIの手のまま）。
PROT_FLAGS = ("record", "ai")
#: 主人公席の点数の観測口（`Entry.score_mode`）。
SCORE_MODE_LABEL = {
    "max": "",                                     # decide() の max(options, key=score) を横取り＝AI が見た点そのもの
    "plan": "計画席（`_plan_turn` に渡った採点器で採点）",
    "post": "★事後採点（計画席＝`max` を通らない席・席フラグ更新後の採点器＝暗躍禁止等は歪む）",
}


def _coord(loop, day, phase) -> str:
    """座標表示。`loop_start`（ループの準備）は日に属さないので日を出さない。"""
    if phase == "loop_start":
        return f"L{loop} loop_start"
    return f"L{loop}D{day} {phase}"


class ReplayStop(Exception):
    """再生が止まった。座標と種類（illegal／desync／exhausted）を運ぶ。"""

    def __init__(self, actor: str, decision: str, view: dict, reason: str,
                 chosen: dict | None = None, kind: str = "illegal"):
        self.actor, self.decision, self.reason, self.chosen = actor, decision, reason, chosen
        self.kind = kind
        self.loop = view.get("loop")
        self.day = view.get("day")
        self.phase = view.get("phase")
        super().__init__(self.describe())

    def describe(self) -> str:
        where = f"{_coord(self.loop, self.day, self.phase)} {self.actor} {self.decision}"
        tail = f"＝{_label(self.chosen)}" if self.chosen is not None else ""
        return f"{where}：{self.reason}{tail}"


@dataclass
class Entry:
    """脚本家の決定1件ぶんの点数表。"""
    idx: int                      # 脚本家決定の通し番号（1始まり・記録順）
    loop: int
    day: int
    phase: str
    decision: str
    recorded: dict                # 記録の選択（棋譜）
    current: dict | None          # 現行AIの選択（`decide()` を実際に呼んだ結果）
    rows: list[dict]              # options 全席（scored なら点数つき・総点降順）
    scored: bool                  # 採点関数があるか
    options_match: bool           # 採点に使った options が記録の options と一致するか
    source: str = "replay"        # "replay"＝現行コードで再構成した局面／"recorded"＝記録の view/options
    b215_swap: bool = False       # 現行AIの選択が席順入れ替え（B-215）で決まったか
    note: str = ""
    # ★T11：主人公席の拡張（脚本家の Entry は既定値のまま＝従来と同じ）。
    actor: str = "mastermind"     # "mastermind"／"p1"／"p2"／"p3"
    seq: int = 0                  # 対局内の決定順（両席を混ぜて並べる鍵。記録順＝0 始まり）
    score_mode: str = ""          # 主人公席の点数の観測口（"max"／"plan"／"post"＝SCORE_MODE_LABEL）
    prior_diverged: bool = False  # 同じターンの先席（主人公）で記録と現行AIが食い違っていたか

    @property
    def n_options(self) -> int:
        return len(self.rows)

    @property
    def diverged(self) -> bool:
        """記録の選択と現行AIの選択が食い違うか（現行AIが例外で無選択なら False）。"""
        return self.current is not None and not _same(self.current, self.recorded)

    def top_total(self) -> float | None:
        return max((r["total"] for r in self.rows), default=None) if self.scored else None

    def total_of(self, chosen: dict | None) -> float | None:
        if not self.scored or chosen is None:
            return None
        for r in self.rows:
            if _same(r["option"], chosen):
                return r["total"]
        return None


@dataclass
class ReplayResult:
    path: str
    meta: dict
    entries: list[Entry] = field(default_factory=list)
    stop: ReplayStop | None = None
    error: str = ""               # 再生中の想定外例外（シミュレータ側の RuntimeError 等）
    source: str = "replay"        # 要求した材料（replay／recorded／auto）
    n_replayed: int = 0           # 再生できた決定数（全席）
    n_recorded: int = 0           # 記録の決定数（全席）
    n_recorded_mm: int = 0        # 記録の脚本家決定数
    loops_used: int | None = None         # 再生に使った総ループ数（記録から推定）
    final_battle_used: bool | None = None  # 再生で最後の戦いを行ったか（記録から推定）
    final_match: bool | None = None    # 完走時：最終状態が meta と一致するか
    history_match: bool | None = None  # 完走時：公開履歴が meta と一致するか
    seed: int = 0
    # ★T4b：互換層が補った／読み飛ばした決定の注記（座標つき・1件1行）。空＝互換層は働いていない。
    compat_notes: list[str] = field(default_factory=list)
    # ★T11：主人公席。`entries` は従来どおり脚本家だけ（既存の呼び手・テストの契約を変えない）。
    side: str = "both"
    prot_entries: list[Entry] = field(default_factory=list)
    n_recorded_prot: int = 0      # 記録の主人公決定数（p1+p2+p3）
    prot_flags: str = "record"    # 先席食い違い後の席間フラグ（record／ai）
    n_flags_resynced: int = 0     # record で記録の手にフラグを当て直した席数

    @property
    def n_replay_prot_entries(self) -> int:
        return sum(1 for e in self.prot_entries if e.source == "replay")

    @property
    def n_prot_diverged(self) -> int:
        return sum(1 for e in self.prot_entries if e.diverged)

    def entries_for(self, side: str | None = None) -> list[Entry]:
        """席で絞った Entry 列。both＝脚本家と主人公を対局の決定順（seq）で混ぜる。"""
        side = self.side if side is None else side
        if side == "mm":
            return list(self.entries)
        if side == "prot":
            return list(self.prot_entries)
        return sorted(self.entries + self.prot_entries, key=lambda e: e.seq)

    @property
    def replay_completed(self) -> bool:
        return self.stop is None and not self.error and self.source != "recorded"

    @property
    def replay_exhausted(self) -> bool:
        return self.stop is not None and self.stop.kind == "exhausted"

    @property
    def n_replay_entries(self) -> int:
        return sum(1 for e in self.entries if e.source == "replay")

    def status_line(self) -> str:
        if self.source == "recorded":
            return "記録の view/options で採点（再生なし＝収録時コードの候補列・局面は再構成していない）"
        if self.stop is not None:
            if self.stop.kind == "exhausted":
                s = (f"記録の終端に到達（途中保存の棋譜）：{self.stop.describe()}"
                     f"＝停止前の {self.n_replay_entries} 件は有効")
            else:
                s = f"★棄権（再生が途中で停止・{self.stop.kind}）：{self.stop.describe()}"
        elif self.error:
            s = f"★棄権（再生中の例外）：{self.error}"
        else:
            v = ("最終状態一致" if self.final_match else "最終状態不一致") + "・" + \
                ("履歴一致" if self.history_match else "履歴不一致")
            s = f"再生 完走（{self.n_replayed}/{self.n_recorded} 決定・{v}）"
        n_rec = sum(1 for e in self.entries if e.source == "recorded")
        if n_rec:
            s += f"／残り {n_rec} 件は記録の view/options で採点（auto）"
        if self.compat_notes:
            s += f"／互換補完 {len(self.compat_notes)} 件（T4b・公開履歴から一意に復元／読み飛ばし）"
        return s


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------
def _strip(chosen):
    from sim.flow import log_safe_chosen   # ★B-115：prov 剥がしの単一ソース
    return log_safe_chosen(chosen)


def _strip_prov_deep(obj):
    """dict/list を再帰的に写し、`prov` キーだけ落とす（履歴照合用の純関数）。"""
    if isinstance(obj, dict):
        return {k: _strip_prov_deep(v) for k, v in obj.items() if k != "prov"}
    if isinstance(obj, list):
        return [_strip_prov_deep(v) for v in obj]
    return obj


def _same(a: dict | None, b: dict | None) -> bool:
    return a is not None and b is not None and _strip(a) == _strip(b)


def _label(o: dict | None) -> str:
    if o is None:
        return "—"
    from arena.replay import describe_choice
    return describe_choice(o)


def _mm_class():
    from agents.heuristic import HeuristicMastermind
    return HeuristicMastermind


def _prot_class():
    from agents.heuristic_protagonist import HeuristicProtagonist
    return HeuristicProtagonist


_builtin_max = max


# ---------------------------------------------------------------------------
# ★T11：主人公席の観測（採点式を写さず、AI が実際に使った採点器を横取りする）
# ---------------------------------------------------------------------------
def prot_probe_decide(hp, view: dict, decision: str, options: list[dict], on_synced=None
                      ) -> tuple[dict | None, list[dict] | None, str, str]:
    """現行 `HeuristicProtagonist.decide()` を**1回だけ**呼び、(chosen, rows, score_mode, note) を返す。

    on_synced＝decide() 内の `_sync(view)`（ターン境界のフラグ初期化と belief 更新）が**最初に**終わった直後に
    1回呼ぶコールバック（`--prot-flags record` の snapshot 用＝B-186 が snapshot を取るのと同じ時点）。

    rows[i] = {"option": o, "total": float}（options 全席・列挙順）。採点器の観測口（優先順）：
      "max"  ＝ `decide()` 内の `max(options, key=score)`（`agents/debug.ProbedProtagonist` と同じ横取り＝
               モジュール名前空間の `max` がビルトインより先に解決される）。**AI が見た点そのもの**。
      "plan" ＝ 過剰需要ターンの先頭席は `_plan_turn(view, options, score)` に採点器が渡って `max` を通らない
               ＝そこで観測（計画前の点＝席フラグは未更新）。
      "post" ＝ 計画席の2席目以降・強制席は採点器がどこにも渡らない＝決定確定後に呼ばれる
               `B100_HOOK(agent, view, options, best, score)`（計測専用・既定 None）の採点器で**事後採点**
               ＝席フラグ更新後の点（暗躍禁止は「1ターン2枚目」扱いで -100 になる等）＝注記で明示する。
    採点器が観測できない決定種（`doctor_unrest_mode`／`final_battle_guess`）は rows=None。
    ★options は**写し**を渡す（AI が採用手の dict に `prov` を書き込む経路があり、記録の照合に混ざらないように）。
    ★B100_HOOK を立てても AI の判断は変わらない（フックの戻り値は不使用・`_defense_plan_recs` が計画を
      スタッシュするだけ＝`agents/heuristic_protagonist.py` の契約）。
    """
    import agents.heuristic_protagonist as _hp_mod
    opts = [dict(o) for o in options]
    cap: dict = {}

    def _capture(key, mode: str) -> None:
        # 先に観測した方を採る。ただし "plan"（先頭席の計画検討）の後に "max" が呼ばれた＝計画が空で
        # 通常の max に落ちた席＝"max" を正とする（同じ採点器・同じ時点＝値は同一で、表示の観測口だけ直す）。
        if "err" in cap or ("rows" in cap and not (mode == "max" and cap.get("mode") == "plan")):
            return
        try:
            cap["rows"] = [{"option": o, "total": float(key(o))} for o in opts]
            cap["mode"] = mode
        except Exception as e:  # noqa: BLE001  採点器の例外＝採点不能（捏造しない）
            cap["err"] = f"{type(e).__name__}: {e}"

    def spymax(*args, **kw):
        if args and args[0] is opts and "key" in kw:
            _capture(kw["key"], "max")
        return _builtin_max(*args, **kw)

    orig_plan = hp._plan_turn

    def plan_wrap(view_, options_, score_fn, *a, **k):
        if options_ is opts:
            _capture(score_fn, "plan")
        return orig_plan(view_, options_, score_fn, *a, **k)

    def hook(agent, view_, options_, best, score_fn):
        if options_ is opts:
            _capture(score_fn, "post")

    orig_sync = hp._sync
    synced = []

    def sync_wrap(view_):
        orig_sync(view_)
        if on_synced is not None and not synced:
            synced.append(True)
            on_synced()

    had = "max" in _hp_mod.__dict__
    prev_max = _hp_mod.__dict__.get("max")
    prev_hook = _hp_mod.B100_HOOK
    _hp_mod.max = spymax
    _hp_mod.B100_HOOK = hook
    hp._plan_turn = plan_wrap
    if on_synced is not None:
        hp._sync = sync_wrap
    note = ""
    try:
        chosen = hp.decide(view, decision, opts)
    except Exception as e:  # noqa: BLE001
        chosen = None
        note = f"現行AIの decide が例外（{type(e).__name__}: {e}）"
    finally:
        _hp_mod.B100_HOOK = prev_hook
        if had:
            _hp_mod.max = prev_max
        else:
            _hp_mod.__dict__.pop("max", None)
        hp.__dict__.pop("_plan_turn", None)
        hp.__dict__.pop("_sync", None)
    if "err" in cap:
        note = (note + "／" if note else "") + f"採点不能（採点器の例外 {cap['err']}）"
    return chosen, cap.get("rows"), cap.get("mode", ""), note


def prot_vocab(hp, view: dict, options: list[dict]) -> dict[tuple, str]:
    """主人公席の「語彙」列＝{(card,target,kind): ラベル}。

    - `plan±x`＝`_plan_recs`（防御プランナーの加点。decide() が今の席で計算した現物を読む）。
    - `noop:理由`＝`card_effect.noop_reason`（公開情報から証明可能な空振り／自滅）。材料は監査側の
      `b100_mix.noop_ctx_for`＋板の伏せ位置（G1〜G7）＝本体の G8〜G10 の材料は持たない（docstring 参照）。
    材料が無い／例外＝ラベル無し（捏造しない）。
    """
    out: dict[tuple, str] = {}
    recs = getattr(hp, "_plan_recs", None) or {}
    ctx = None
    try:
        from agents.b100_mix import noop_ctx_for
        from agents.card_effect import noop_reason
        boards = frozenset(p.get("target") for p in (view.get("placements") or [])
                           if p.get("owner") == "mastermind" and p.get("target_kind") == "board")
        ctx = replace(noop_ctx_for(hp, view), mm_boards=boards)
    except Exception:  # noqa: BLE001
        noop_reason = None
    for o in options:
        if "card" not in o:
            continue
        key = (o.get("card"), o.get("target"), o.get("target_kind"))
        parts = []
        if key in recs:
            parts.append(f"plan{float(recs[key]):+.1f}")
        if ctx is not None and noop_reason is not None:
            try:
                n = noop_reason(view, key[0], key[1], key[2], ctx)
            except Exception:  # noqa: BLE001
                n = None
            if n is not None:
                parts.append(f"noop:{n.reason}")
        if parts:
            out[key] = "・".join(parts)
    return out


def resync_seat_flags(hp, snap: dict, view: dict, recorded: dict) -> str:
    """★T11 `--prot-flags record`：食い違った席の席間協調フラグを **記録の手** で当て直す。

    decide 前の snapshot（`_b186_snapshot`＝B-186 Phase 1b が忠実逐次評価に使う既存の口）へ戻してから、
    現行AIが自分の手で呼ぶのと同じ `_apply_seat_flags(view, best)`（単一ソース）を記録の手で呼ぶ。
    既存メソッドを呼ぶだけ＝agents/ には触れない。失敗したら理由を返す（成功＝空文字）。
    """
    try:
        hp._b186_restore(snap)
        hp._apply_seat_flags(view, recorded)
    except Exception as e:  # noqa: BLE001
        return f"席フラグの記録合わせに失敗（{type(e).__name__}: {e}）"
    return ""


def _build_prot_entry(hp, actor: str, idx: int, seq: int, view: dict, decision: str,
                      options: list[dict], recorded: dict, rec_options: list[dict] | None,
                      decisions_filter: set[str] | None, source: str,
                      prior_diverged: bool, flags: str = "record") -> tuple[Entry, bool]:
    """主人公の1決定ぶん＝options 全席の点数＋現行AIの選択。`decide()` は必ず1回呼ぶ（rng・belief の
    更新順を本番と揃える＝採点しない決定種でも呼ぶ）。返り値 (Entry, フラグを記録の手に当て直したか)。"""
    match = (rec_options is not None
             and [_strip(o) for o in rec_options] == [_strip(o) for o in options])
    # ★record：snapshot は decide() 内の `_sync`（ターン境界でフラグを初期化する）の**直後**に取る。
    #   decide の前に取ると前ターンのフラグ（例＝暗躍禁止使用済み）を持ち越して当て直してしまう。
    box: dict = {}
    on_synced = None
    if flags == "record" and decision == "set_card" and hasattr(hp, "_b186_snapshot"):
        on_synced = lambda: box.setdefault("snap", hp._b186_snapshot())  # noqa: E731
    current, rows, mode, note = prot_probe_decide(hp, view, decision, options, on_synced)
    snap = box.get("snap")
    want = decisions_filter is None or decision in decisions_filter
    scored = rows is not None and want
    if scored:
        vocab = prot_vocab(hp, view, options) if decision == "set_card" else {}
        for r in rows:
            o = r["option"]
            r["vocab"] = vocab.get((o.get("card"), o.get("target"), o.get("target_kind")), "")
        rows.sort(key=lambda r: -r["total"])
        if SCORE_MODE_LABEL.get(mode):
            note = (note + "／" if note else "") + SCORE_MODE_LABEL[mode]
    else:
        rows = [{"option": o} for o in options]
        if want and "採点不能" not in note:
            note = (note + "／" if note else "") + PROT_UNSCORED_NOTE
    resynced = False
    if snap is not None and current is not None and not _same(current, recorded):
        err = resync_seat_flags(hp, snap, view, recorded)
        if err:
            note = (note + "／" if note else "") + err
        else:
            resynced = True
    return Entry(idx=idx, loop=view.get("loop"), day=view.get("day"), phase=view.get("phase"),
                 decision=decision, recorded=_strip(recorded),
                 current=_strip(current) if current is not None else None, rows=rows,
                 scored=scored, options_match=match, source=source, note=note,
                 actor=actor, seq=seq, score_mode=mode if scored else "",
                 prior_diverged=prior_diverged), resynced


# ---------------------------------------------------------------------------
# 採点（純関数＝`decide()` と同一の式。rng は消費しない）
# ---------------------------------------------------------------------------
def score_options(mm, view: dict, decision: str, options: list[dict]) -> tuple[list[dict], bool]:
    """options 全席を現行AIの採点関数で点数化する。返り値 (rows, scored)。

    rows[i] = {"option": o, "total": float, ...内訳}（scored なら総点降順・同点は列挙順）。
    scored=False の決定種は点数なし（rows は列挙順・`total` キー無し）。
    """
    if decision == "set_card":
        from agents.heuristic import _B293_VOID
        a = mm._analyze(view)
        fired = bool(mm.B289_ALT_BONUS) and any(
            mm._b286_penalty(o, a) > 0 for o in options)
        on293 = mm.B293_SKIP_IMMOBILE_MOVE or mm.B293_SKIP_BLOCKED_DEST
        rows = []
        for o in options:
            void = bool(on293 and mm._b293_move_is_void(o, view))
            raw = mm._score_set(o, a, view)
            plus2 = mm._plus2_penalty(o, a, view)
            pen = mm._b286_penalty(o, a)
            bon = mm._b289_bonus(o, a, view, fired)
            rows.append({"option": o, "total": _B293_VOID if void else raw - plus2 - pen + bon,
                         "raw": raw, "plus2": plus2, "b286": pen, "b289": bon,
                         "void": void})
        rows.sort(key=lambda r: -r["total"])
        return rows, True
    if decision in ("mastermind_ability", "turn_end_ability", "incident_choice"):
        a = mm._analyze(view)
        fn = {"mastermind_ability": mm._score_ability,
              "turn_end_ability": mm._score_turn_end,
              "incident_choice": mm._score_incident}[decision]
        rows = [{"option": o, "total": float(fn(o, a))} for o in options]
        rows.sort(key=lambda r: -r["total"])
        return rows, True
    if decision == "loop_start_area":
        rows = [{"option": o, "total": float(mm._score_loop_area(o, view))} for o in options]
        rows.sort(key=lambda r: -r["total"])
        return rows, True
    return [{"option": o} for o in options], False


def _build_entry(mm, idx: int, view: dict, decision: str, options: list[dict],
                 recorded: dict, rec_options: list[dict] | None,
                 decisions_filter: set[str] | None, source: str) -> Entry:
    """1決定ぶん＝採点表＋現行AIの選択。`decide()` は必ず1回呼ぶ（rng の消費順を本番と揃える）。"""
    match = (rec_options is not None
             and [_strip(o) for o in rec_options] == [_strip(o) for o in options])
    note = ""
    if decisions_filter is None or decision in decisions_filter:
        try:
            rows, scored = score_options(mm, view, decision, options)
        except Exception as e:  # noqa: BLE001  記録の view の形式差など＝捏造せず「採点不能」
            rows, scored = [{"option": o} for o in options], False
            note = f"採点不能（{type(e).__name__}: {e}）"
    else:
        rows, scored = [{"option": o} for o in options], False
    if not scored and not note:
        note = UNSCORED_NOTE
    before = mm._b215_stats.get("swap", 0)
    try:
        current = mm.decide(view, decision, options)
    except Exception as e:  # noqa: BLE001
        current = None
        note = (note + "／" if note else "") + f"現行AIの decide が例外（{type(e).__name__}: {e}）"
    swap = mm._b215_stats.get("swap", 0) > before
    return Entry(idx=idx, loop=view.get("loop"), day=view.get("day"), phase=view.get("phase"),
                 decision=decision, recorded=_strip(recorded), current=current, rows=rows,
                 scored=scored, options_match=match, source=source, b215_swap=swap, note=note)


# ---------------------------------------------------------------------------
# 再生（記録の選択列で局面を進めつつ、脚本家席で現行AIの採点と選択を採る）
# ---------------------------------------------------------------------------
class _Seat:
    """記録の (決定種, 選択) 列を順に返す席。決定種が違う／合法手に無い／尽きた → ReplayStop。

    ★T4b：`peek()`／`skip()` は互換層（`arena/replay_compat`）が「記録の次」を覗いて読み飛ばす／
    差し替えるための口。`_recs` の各要素は (決定種, 選択, 記録の options, 記録行の座標 (loop, day))。
    """

    def __init__(self, actor: str, recs: list[dict], registry: dict):
        self.actor = actor
        self._recs = [(d["decision"], _strip(d["chosen"]), d.get("options") or None,
                       (d.get("loop"), d.get("day")))
                      for d in recs]
        self._i = 0
        self._registry = registry
        registry[actor] = self

    @property
    def remaining(self) -> int:
        return len(self._recs) - self._i

    def peek(self) -> tuple | None:
        """記録の次（消費しない）。尽きていれば None。"""
        return self._recs[self._i] if self._i < len(self._recs) else None

    def skip(self) -> None:
        """記録の次を1件読み飛ばす（互換層の読み飛ばし／差し替え用）。"""
        self._i += 1

    def next_recorded(self, view: dict, decision: str, options: list[dict]
                      ) -> tuple[dict, list[dict] | None]:
        if self._i >= len(self._recs):
            others = {a: s.remaining for a, s in self._registry.items()
                      if a != self.actor and s.remaining > 0}
            if others:
                raise ReplayStop(self.actor, decision, view,
                                 f"{self.actor} の記録が尽きたが他席に未消費の記録が残る {others}",
                                 kind="desync")
            raise ReplayStop(self.actor, decision, view, "記録の終端（記録の選択列が尽きた）",
                             kind="exhausted")
        rdec, chosen, rec_opts, _at = self._recs[self._i]
        if rdec != decision:
            raise ReplayStop(self.actor, decision, view,
                             f"現行 flow は {decision} を要求したが記録の次の決定は {rdec}"
                             f"（収録後に決定種が増減した／局面がずれた）", chosen, kind="desync")
        self._i += 1
        if chosen not in options:
            raise ReplayStop(self.actor, decision, view, "記録の選択が現行の合法手に無い", chosen)
        return chosen, rec_opts


def _record_has_bluff_options(decisions: list[dict]) -> bool:
    """記録の脚本家 set_card 候補に板へのダミー配置（暗躍以外の札→board）があるか＝人間脚本家の
    候補列（`play_interactive` は allow_bluff=True）／B-214 ON の AI の候補列の目印。"""
    for d in decisions:
        if d.get("actor") == "mastermind" and d.get("decision") == "set_card":
            for o in d.get("options") or ():
                if o.get("target_kind") == "board" and not str(o.get("card", "")).startswith("暗躍"):
                    return True
    return False


def infer_frame(script, meta: dict, decisions: list[dict]) -> tuple[int, bool]:
    """記録から対局の枠（総ループ数・最後の戦いの有無）を推定する。

    アプリは script.loops とは別に「4ループ打ち切り＋延長」（`arena/loopcap`）で回し、人間=脚本家
    モードは最後の戦いを行わない（`arena/play_vs_ai.run_to_pending(final_battle=False)`）。
    保存された script.loops はモードにより延長前の値のことがある＝記録の最大ループを下限にする。
    """
    loops = int(getattr(script, "loops", 0) or 0)
    try:
        loops = max(loops, int(meta.get("loops_played") or 0))
    except (TypeError, ValueError):
        pass
    loops = max([loops] + [int(d.get("loop") or 0) for d in decisions])
    fb = any(d.get("decision") == "final_battle_guess" for d in decisions)
    return loops, fb


def replay_with_scores(script, meta: dict, decisions: list[dict], *, seed: int = 0,
                       decisions_filter: set[str] | None = None,
                       path: str = "", mm=None, side: str = "both", hp=None,
                       prot_flags: str = "record") -> ReplayResult:
    """記録の選択列で対局を再生し、脚本家（と ★T11 主人公）の各決定の点数表を集める（止まったら棄権を記録）。

    side="mm" のとき主人公AIは生成も呼び出しもしない（T4/T4b と bit 同一）。
    """
    from sim import mastermind_view, protagonist_view
    from sim.flow import run_loop
    from sim.state import ONE_TIME_INITIAL_AREA, GameState, validate_script
    from arena import replay_compat as compat     # ★T4b 互換層（純関数）
    res = ReplayResult(path=path, meta=meta, seed=seed, n_recorded=len(decisions),
                       n_recorded_mm=sum(1 for d in decisions if d.get("actor") == "mastermind"),
                       side=side, prot_flags=prot_flags,
                       n_recorded_prot=sum(1 for d in decisions if d.get("actor") in PROT_SEATS))
    mm = mm if mm is not None else _mm_class()(seed)
    if side != "mm" and hp is None:
        hp = _prot_class()(seed)          # p1〜p3 で1体を共有（`arena/play_vs_ai._ai_protagonists` と同じ）
    prot_entries: list[Entry] = res.prot_entries
    turn_div: set[tuple] = set()          # 主人公の先席が食い違ったターン {(loop, day)}
    registry: dict[str, _Seat] = {}
    seats = {a: _Seat(a, [d for d in decisions if d.get("actor") == a], registry)
             for a in ("mastermind", "p1", "p2", "p3")}
    loops, fb = infer_frame(script, meta, decisions)
    res.loops_used, res.final_battle_used = loops, fb
    entries: list[Entry] = []
    log: list[dict] = []
    notes = res.compat_notes
    try:
        # ★T4b（5）：途中からの棋譜は先頭からの再生が原理的に不能＝再生に入らず棄権する。
        #   （入ってしまうと L1D1 の局面に L3D3 の記録の選択が**偶然合法で乗り**、誤った局面を採点する。）
        _p = compat.partial_record_start(decisions)
        if _p is not None:
            _first = decisions[0]
            raise ReplayStop(_first.get("actor", "?"), _first.get("decision", "?"),
                             {"loop": _p[0], "day": _p[1], "phase": _first.get("phase")},
                             f"記録の先頭が L{_p[0]}D{_p[1]}（途中からの棋譜＝先頭の決定が欠落）"
                             f"＝先頭からの再生は不能", kind="partial")
        sc = replace(script, loops=loops) if loops != script.loops else script
        validate_script(sc)
        state = GameState(script=sc)
        _hist = meta.get("history") or []
        _snaps = meta.get("snapshots") or []
        _last_gw: dict[str, dict | None] = {}     # 席ごとの直近 goodwill_ability の選択（宣言の対象用）
        _doc_nth: dict[tuple, int] = {}           # (loop, day, user, target) ごとの宣言要求の回数

        def _compat(actor: str, decision: str, options: list[dict], view: dict
                    ) -> tuple[dict, list[dict] | None] | None:
            """★T4b 互換層。記録から一意に復元できるときだけ (chosen, rec_opts) を返す。
            復元不能なら ReplayStop（rule_changed／desync）。互換の出番でなければ None（＝従来の照合へ）。"""
            seat = seats[actor]
            where = _coord(state.loop_no, state.day, state.phase)
            # (1) 従者の loop_start_area（loop≥2）＝現行 flow は要求しない＝読み飛ばす／違えば棄権。
            while True:
                nxt = seat.peek()
                if (nxt is None or nxt[0] != "loop_start_area"
                        or nxt[1].get("name") not in ONE_TIME_INITIAL_AREA
                        or int(nxt[3][0] or 0) < 2):
                    break
                name, area = nxt[1].get("name"), nxt[1].get("area")
                fixed = state.fixed_initial_areas.get(name)
                if fixed is not None and fixed == area:
                    seat.skip()
                    notes.append(f"L{nxt[3][0]} loop_start {actor} loop_start_area（{name}→{area}）を"
                                 f"読み飛ばし＝一度きりの選択（B-29x ③）で L1 の {fixed} と同じ")
                    continue
                raise ReplayStop(actor, "loop_start_area",
                                 {"loop": nxt[3][0], "day": nxt[3][1], "phase": "loop_start"},
                                 f"旧規則で {name} の初期エリアをループ途中で変更（L1={fixed}→{area}）"
                                 f"＝現行規則（一度きりの選択・B-29x ③）では再現不能", nxt[1],
                                 kind="rule_changed")
            nxt = seat.peek()
            # (2) 除去/付与の宣言（B-278 医者♡3／★T13 教師♡3『学生の不安操作』）＝記録に無ければ
            #     公開履歴から一意に復元する。
            if decision == "doctor_unrest_mode":
                use = compat.unrest_mode_use_of(_last_gw.get(actor))
                if use is None:
                    return None
                user, _aname, target = use
                # 同日・同大人・同対象の何回目の宣言要求か（記録にある要求も数える＝履歴の出現順と揃える）。
                key = (state.loop_no, state.day, user, target)
                nth = _doc_nth.get(key, 0)
                _doc_nth[key] = nth + 1
                if nxt is not None and nxt[0] == decision:
                    return None          # 記録にある（旧位置でも席単位の順序は同じ）＝従来の照合へ
                mode, why = compat.infer_unrest_mode(_hist, state.loop_no, state.day, target, nth,
                                                     user=user)
                pick = next((o for o in options if o.get("mode") == mode), None) if mode else None
                if pick is None:
                    if nxt is None:
                        return None          # 記録の終端＝従来の exhausted/desync 判定に委ねる
                    raise ReplayStop(actor, decision, view,
                                     f"{user}→{target} の宣言（除去/付与）が記録に無く復元不能：{why}",
                                     kind="rule_changed")
                notes.append(f"{where} {actor} doctor_unrest_mode（{user}→{target}）を"
                             f"{'除去' if mode == 'remove' else '付与'}で補完＝{why}")
                return pick, None
            # (3) goodwill_refuse＝宣言キー（mode）を持たない旧記録の選択を記録側のキーだけで照合する。
            if (decision == "goodwill_refuse" and nxt is not None and nxt[0] == decision
                    and nxt[1] not in options):
                m = compat.match_ignoring_declaration(nxt[1], options)
                if m is not None:
                    seat.skip()
                    notes.append(f"{where} {actor} goodwill_refuse の記録（宣言キー無し）を"
                                 f"宣言つきの候補と照合＝{_label(m)}")
                    return m, nxt[2]
                return None
            # (4) cultist_ignore（A-42）＝記録に無ければ公開履歴／盤面スナップショットから一意に復元する。
            if decision == "cultist_ignore" and (nxt is None or nxt[0] != decision):
                o0 = options[0]
                target, kind = o0.get("target"), o0.get("target_kind")
                val, why = compat.infer_cultist_ignore(_hist, _snaps, state.loop_no, state.day,
                                                       target, kind)
                pick = next((o for o in options if bool(o.get("ignore")) is val), None) \
                    if val is not None else None
                if pick is None:
                    if nxt is None:
                        return None
                    raise ReplayStop(actor, decision, view,
                                     f"カルティストの無視（{target}）が記録に無く復元不能：{why}",
                                     kind="rule_changed")
                notes.append(f"{where} {actor} cultist_ignore（{target}）を"
                             f"{'無視' if val else '無視せず'}で補完＝{why}")
                return pick, None
            return None

        def decide(actor: str, decision: str, options: list[dict]) -> dict:
            if not options:
                raise RuntimeError(f"{actor} の {decision} に合法手が無い（シミュレータバグ）")
            view = mastermind_view(state) if actor == "mastermind" \
                else protagonist_view(state, actor)
            got = _compat(actor, decision, options, view)
            if got is None:
                chosen, rec_opts = seats[actor].next_recorded(view, decision, options)
            else:
                chosen, rec_opts = got
            if decision == "goodwill_ability":
                _last_gw[actor] = chosen
            if actor == "mastermind":
                entries.append(_build_entry(mm, len(entries) + 1, view, decision, options,
                                            chosen, rec_opts, decisions_filter, "replay"))
                entries[-1].seq = len(log)
                if got is not None and rec_opts is None:
                    entries[-1].note = ((entries[-1].note + "／") if entries[-1].note else "") + \
                        "互換補完（記録に無い決定を公開履歴から復元・T4b）"
            elif side != "mm" and actor in PROT_SEATS:
                # ★T11：主人公席＝現行 HeuristicProtagonist を実際に呼ぶ（局面は記録どおりに進める）。
                turn = (state.loop_no, state.day)
                e, rs = _build_prot_entry(hp, actor, len(prot_entries) + 1, len(log), view, decision,
                                          options, chosen, rec_opts, decisions_filter, "replay",
                                          prior_diverged=turn in turn_div, flags=prot_flags)
                if got is not None and rec_opts is None:
                    e.note = ((e.note + "／") if e.note else "") + \
                        "互換補完（記録に無い決定を公開履歴から復元・T4b）"
                prot_entries.append(e)
                res.n_flags_resynced += int(rs)
                if e.diverged:
                    turn_div.add(turn)
            log.append({"loop": state.loop_no, "day": state.day, "phase": state.phase,
                        "actor": actor, "decision": decision, "chosen": chosen})
            return chosen

        # ★B-214/B-215：候補列の作り方を本番と揃える（`sim.flow.attach_mm_bluff` と同じ配線）。
        #   人間=脚本家の記録は `play_interactive` が allow_bluff=True で候補を作っている＝記録に
        #   ダミー配置の候補があればこちらも開く。
        decide.mm_allow_bluff = bool(getattr(mm, "wants_bluff_options", False)
                                     or _record_has_bluff_options(decisions))
        run_loop(state, decide, final_battle=fb)
    except ReplayStop as e:
        res.stop = e
    except Exception as e:  # noqa: BLE001  シミュレータ側の例外＝棄権（座標は分かる範囲で）
        res.error = f"{type(e).__name__}: {e}"
    else:
        try:
            res.final_match = state.to_dict() == meta.get("final_state")
            # ★B-100 の `prov`（表示層の由来印）は AI主人公が option dict に書き込むため記録の
            #   `cards_revealed` に混ざる。盤面の事実ではないので照合の前に両側から剥がす。
            res.history_match = (_strip_prov_deep(list(state.history))
                                 == _strip_prov_deep(list(meta.get("history") or [])))
        except Exception as e:  # noqa: BLE001
            res.error = f"最終状態の照合で例外: {type(e).__name__}: {e}"
    finally:
        res.n_replayed = len(log)
        res.entries = entries
    return res


def score_recorded(meta: dict, decisions: list[dict], *, seed: int = 0,
                   decisions_filter: set[str] | None = None, path: str = "",
                   mm=None, start: int = 0, into: ReplayResult | None = None,
                   side: str = "both", hp=None, start_prot: int = 0,
                   prot_flags: str = "record") -> ReplayResult:
    """記録の view/options をそのまま現行AIに通す（再生なし＝§72-129／§72-136 の作法）。

    start＝記録の脚本家決定のうち何件目から（auto の続き用）。into＝既存の結果に追記する。
    ★T11：side != "mm" なら主人公決定（p1/p2/p3）も同じ作法で通す（start_prot＝何件目から）。
    """
    mm_recs = [d for d in decisions if d.get("actor") == "mastermind"]
    prot_recs = [d for d in decisions if d.get("actor") in PROT_SEATS]
    res = into if into is not None else ReplayResult(
        path=path, meta=meta, seed=seed, source="recorded", n_recorded=len(decisions),
        n_recorded_mm=len(mm_recs), side=side, n_recorded_prot=len(prot_recs), prot_flags=prot_flags)
    mm = mm if mm is not None else _mm_class()(seed)
    if side != "mm" and hp is None:
        hp = _prot_class()(seed)
    seq_of = {id(d): i for i, d in enumerate(decisions)}     # 記録順＝seq（両席を混ぜて並べる鍵）
    for k, d in enumerate(mm_recs):
        if k < start:
            continue
        view, options = d.get("view"), d.get("options") or []
        if not isinstance(view, dict) or not options:
            res.entries.append(Entry(
                idx=k + 1, loop=d.get("loop"), day=d.get("day"), phase=d.get("phase"),
                decision=d.get("decision", "?"), recorded=_strip(d.get("chosen")), current=None,
                rows=[{"option": o} for o in options], scored=False, options_match=True,
                source="recorded", note="採点不能（記録に view/options が無い）", seq=seq_of[id(d)]))
            continue
        e = _build_entry(mm, k + 1, view, d.get("decision", "?"), options, d.get("chosen"),
                         options, decisions_filter, "recorded")
        # 記録の view は loop/day/phase を持つが、記録行の座標を正とする（view 側は収録時の形式）。
        e.loop, e.day, e.phase = d.get("loop", e.loop), d.get("day", e.day), d.get("phase", e.phase)
        e.seq = seq_of[id(d)]
        res.entries.append(e)
    if side == "mm":
        return res
    turn_div: set[tuple] = set()
    for k, d in enumerate(prot_recs):
        if k < start_prot:
            continue
        actor = str(d.get("actor"))
        view, options = d.get("view"), d.get("options") or []
        turn = (d.get("loop"), d.get("day"))
        if not isinstance(view, dict) or not options:
            res.prot_entries.append(Entry(
                idx=k + 1, loop=d.get("loop"), day=d.get("day"), phase=d.get("phase"),
                decision=d.get("decision", "?"), recorded=_strip(d.get("chosen")), current=None,
                rows=[{"option": o} for o in options], scored=False, options_match=True,
                source="recorded", note="採点不能（記録に view/options が無い）",
                actor=actor, seq=seq_of[id(d)]))
            continue
        e, rs = _build_prot_entry(hp, actor, k + 1, seq_of[id(d)], view, d.get("decision", "?"),
                                  options, d.get("chosen"), options, decisions_filter, "recorded",
                                  prior_diverged=turn in turn_div, flags=prot_flags)
        e.loop, e.day, e.phase = d.get("loop", e.loop), d.get("day", e.day), d.get("phase", e.phase)
        res.prot_entries.append(e)
        res.n_flags_resynced += int(rs)
        if e.diverged:
            turn_div.add(turn)
    return res


def analyze(path: str, *, source: str = "replay", seed: int = 0,
            decisions_filter: set[str] | None = None, side: str = "both",
            prot_flags: str = "record") -> ReplayResult:
    """決定録1本を読み、指定の材料で脚本家（と ★T11 主人公）の全決定の点数表を集める。"""
    from arena.gamelog import load_game
    if source not in SOURCES:
        raise ValueError(f"source は {SOURCES} のいずれか: {source}")
    if side not in SIDES:
        raise ValueError(f"side は {SIDES} のいずれか: {side}")
    if prot_flags not in PROT_FLAGS:
        raise ValueError(f"prot_flags は {PROT_FLAGS} のいずれか: {prot_flags}")
    script, meta, decisions = load_game(path)
    if source == "recorded":
        return score_recorded(meta, decisions, seed=seed, decisions_filter=decisions_filter,
                              path=path, side=side, prot_flags=prot_flags)
    mm = _mm_class()(seed)
    hp = _prot_class()(seed) if side != "mm" else None
    res = replay_with_scores(script, meta, decisions, seed=seed,
                             decisions_filter=decisions_filter, path=path, mm=mm, side=side, hp=hp,
                             prot_flags=prot_flags)
    res.source = source
    if source == "auto" and not res.replay_completed and not res.replay_exhausted:
        # 再生が止まった＝残りの決定は記録の view/options で補う（同じ mm／hp＝rng の順は連続）。
        score_recorded(meta, decisions, seed=seed, decisions_filter=decisions_filter,
                       path=path, mm=mm, start=res.n_replay_entries, into=res,
                       side=side, hp=hp, start_prot=res.n_replay_prot_entries, prot_flags=prot_flags)
    return res


def select_entries(res: ReplayResult, loop: int | None = None, day: int | None = None,
                   decisions: set[str] | None = None, side: str = "mm") -> list[Entry]:
    """座標・決定種で絞る。side＝"mm"（既定＝従来どおり脚本家だけ）／"prot"／"both"（決定順に混ぜる）。"""
    out = []
    for e in res.entries_for(side):
        if loop is not None and e.loop != loop:
            continue
        if day is not None and e.day != day:
            continue
        if decisions is not None and e.decision not in decisions:
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# 表示（Markdown / TSV）
# ---------------------------------------------------------------------------
def _fmt(x) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, float):
        return "VOID" if x <= -1e8 else f"{x:.1f}"
    return str(x)


def _neg(x) -> str:
    """減点列の表示（0 なら符号無し・それ以外は負号つき）。"""
    return "0.0" if not x else _fmt(-x)


def _pos(x) -> str:
    return "0.0" if not x else "+" + _fmt(x)


def _marks(e: Entry, o: dict) -> str:
    m = []
    if _same(o, e.recorded):
        m.append("記録")
    if _same(o, e.current):
        m.append("現行")
    return "・".join(m)


def _prot_marks(e: Entry, o: dict) -> str:
    """主人公席の印。記録と現行が食い違う席は「★記録」「★現行」で別々の行に立つ（一致なら「記録・現行」）。"""
    m = _marks(e, o)
    return ("★" + m) if (m and e.diverged) else m


def _visible_rows(e: Entry, top: int) -> list[dict]:
    shown = e.rows[:top]
    for r in e.rows[top:]:
        if _same(r["option"], e.recorded) or _same(r["option"], e.current):
            shown.append(r)
    return shown


def header_lines(res: ReplayResult) -> list[str]:
    from arena.gamelog import _tool_build
    m = res.meta or {}
    sc = m.get("script") or {}
    frame = ""
    if res.loops_used is not None:
        frame = (f" 再生の枠: 総ループ {res.loops_used}・最後の戦い"
                 f"{'あり' if res.final_battle_used else 'なし'}（記録から推定）")
    L = [f"# 点数表 {res.path}",
         f"- 脚本: {sc.get('set_name', '?')} {sc.get('days_per_loop', '?')}日×{sc.get('loops', '?')}L "
         f"rule_y={sc.get('rule_y', '?')} rule_x={sc.get('rule_x', '?')}"
         + (f"/{sc['rule_x2']}" if sc.get("rule_x2") else ""),
         f"- 収録: tool_build={m.get('tool_build') or '?'} hash_seed={m.get('hash_seed')} "
         f"winner={m.get('winner')} loops_played={m.get('loops_played')}",
         f"- 採点: tool_build={_tool_build() or '?'} PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')} "
         f"AI seed={res.seed}（seed は同点のタイブレークにだけ効く） source={res.source}{frame}",
         f"- {res.status_line()}"]
    if res.side != "prot":
        L.append(f"- 脚本家決定 {len(res.entries)}/{res.n_recorded_mm} 件（うち options 不一致 "
                 f"{sum(1 for e in res.entries if not e.options_match)} 件）")
    if res.side != "mm":
        # ★T11：主人公席の要約（`--side mm` ではこの2行は出ない＝T4/T4b と bit 同一）。
        P = res.prot_entries
        L.append(f"- 主人公決定 {len(P)}/{res.n_recorded_prot} 件（席 p1/p2/p3・うち記録と現行AIの食い違い "
                 f"{res.n_prot_diverged} 件・options 不一致 {sum(1 for e in P if not e.options_match)} 件・"
                 f"事後採点 {sum(1 for e in P if e.score_mode == 'post')} 件）"
                 f" 席間フラグ={res.prot_flags}"
                 + (f"（食い違った {res.n_flags_resynced} 席で記録の手に当て直し）" if res.prot_flags == "record"
                    else "（現行AI自身の手のまま＝先席食い違い後の後席は歪みうる）"))
        L.append("- 主人公席の点＝現行 HeuristicProtagonist（p1〜p3 で1体・seed 同上）の `decide()` が使った採点器を"
                 "横取りして全席に当てた値（式の写しではない）。語彙＝plan（防御プランナー加点）／noop（証明可能な"
                 "空振り・監査側材料 G1〜G7）")
    return L


def render_prot_entry_md(e: Entry, top: int = 12) -> list[str]:
    """★T11：主人公席1決定ぶんの Markdown。"""
    tags = []
    if e.diverged:
        tags.append("★食い違い（記録≠現行AI）")
    if e.prior_diverged:
        tags.append("先席食い違いあり")
    if e.source == "recorded":
        tags.append("記録の view/options で採点")
    if not e.options_match:
        tags.append("options 記録と不一致")
    if e.note:
        tags.append(e.note)
    head = (f"## #{e.actor}-{e.idx} {_coord(e.loop, e.day, e.phase)} {e.actor} {e.decision}  "
            f"記録＝{_label(e.recorded)} ／ 現行AI＝{_label(e.current)}")
    if tags:
        head += "  〔" + "・".join(tags) + "〕"
    L = [head]
    if e.scored:
        L.append("| 順 | 点 | 席 | 手 | 語彙 | 印 |")
        L.append("|---:|---:|---|---|---|---|")
        for r in _visible_rows(e, top):
            L.append(f"| {e.rows.index(r) + 1} | {_fmt(r['total'])} | {e.actor} | {_label(r['option'])} | "
                     f"{r.get('vocab', '')} | {_prot_marks(e, r['option'])} |")
        if e.n_options > top:
            L.append(f"（全 {e.n_options} 席中 上位 {top} と記録/現行の席のみ表示）")
    else:
        L.append("| 席 | 手 | 印 |")
        L.append("|---|---|---|")
        for r in e.rows:
            L.append(f"| {e.actor} | {_label(r['option'])} | {_prot_marks(e, r['option'])} |")
    return L


def render_entry_md(e: Entry, top: int = 12) -> list[str]:
    if e.actor != "mastermind":
        return render_prot_entry_md(e, top)
    tags = []
    if e.source == "recorded":
        tags.append("記録の view/options で採点")
    if e.b215_swap:
        tags.append("席順入れ替え B-215")
    if not e.options_match:
        tags.append("options 記録と不一致")
    if e.note:
        tags.append(e.note)
    head = (f"## #{e.idx} {_coord(e.loop, e.day, e.phase)} {e.decision}  "
            f"記録＝{_label(e.recorded)} ／ 現行AI＝{_label(e.current)}")
    if tags:
        head += "  〔" + "・".join(tags) + "〕"
    L = [head]
    if e.decision == "set_card" and e.scored:
        L.append("| 順 | 総点 | 素点 | +2減点 | B286減点 | B289加点 | 札→対象 | 印 |")
        L.append("|---:|---:|---:|---:|---:|---:|---|---|")
        for r in _visible_rows(e, top):
            L.append(f"| {e.rows.index(r) + 1} | {_fmt(r['total'])} | {_fmt(r['raw'])} | "
                     f"{_neg(r['plus2'])} | {_neg(r['b286'])} | {_pos(r['b289'])} | "
                     f"{_label(r['option'])} | {_marks(e, r['option'])} |")
    elif e.scored:
        L.append("| 順 | 点 | 手 | 印 |")
        L.append("|---:|---:|---|---|")
        for r in _visible_rows(e, top):
            L.append(f"| {e.rows.index(r) + 1} | {_fmt(r['total'])} | {_label(r['option'])} | "
                     f"{_marks(e, r['option'])} |")
    else:
        L.append("| 手 | 印 |")
        L.append("|---|---|")
        for r in e.rows:
            L.append(f"| {_label(r['option'])} | {_marks(e, r['option'])} |")
    if e.n_options > top and e.scored:
        L.append(f"（全 {e.n_options} 席中 上位 {top} と記録/現行の席のみ表示）")
    return L


def render_md(res: ReplayResult, entries: list[Entry] | None = None, top: int = 12) -> str:
    L = header_lines(res)
    for e in (res.entries_for() if entries is None else entries):
        L.append("")
        L += render_entry_md(e, top)
    if res.source != "recorded" and (res.stop is not None or res.error):
        L.append("")
        if res.replay_exhausted:
            L.append(f"記録の終端：{res.stop.describe()}（途中保存の棋譜＝上の決定はすべて有効）。")
        else:
            n_rec = sum(1 for e in res.entries if e.source == "recorded")
            n_rec_p = sum(1 for e in res.prot_entries if e.source == "recorded")
            tail = ""
            if res.side != "mm":
                tail = (f"主人公席は以降 {n_rec_p} 件を記録の view/options で採点した（再生ではない）。" if n_rec_p
                        else f"主人公席は以降を出力しない（上の {res.n_replay_prot_entries} 件は停止前まで有効）。")
            L.append(f"★棄権：{res.stop.describe() if res.stop else res.error}。"
                     + (f"以降 {n_rec} 件は記録の view/options で採点した（再生ではない）。" if n_rec
                        else f"以降の決定は出力しない（上の {res.n_replay_entries} 件は停止前まで有効）。")
                     + tail)
    if res.compat_notes:
        L.append("")
        L.append(f"互換補完（T4b・{len(res.compat_notes)} 件＝記録に無い決定を公開履歴から一意に復元／"
                 "現行 flow が要求しない決定を読み飛ばし。捏造なし）：")
        L += [f"- {n}" for n in res.compat_notes]
    return "\n".join(L)


TSV_COLS = ("file", "idx", "loop", "day", "phase", "decision", "rank", "total", "raw",
            "plus2", "b286", "b289", "void", "option", "recorded", "current",
            "options_match", "b215_swap", "scored", "source")
#: ★T11：`--side prot|both` のときだけ末尾に足す列（`--side mm` は TSV_COLS のまま＝bit 同一）。
TSV_COLS_T11 = ("seat", "vocab", "score_mode", "diverged", "prior_diverged")


def render_tsv(res: ReplayResult, entries: list[Entry] | None = None, top: int = 12,
               with_header: bool = True) -> str:
    cols = TSV_COLS if res.side == "mm" else TSV_COLS + TSV_COLS_T11
    L = ["\t".join(cols)] if with_header else []
    base = os.path.basename(res.path)
    for e in (res.entries_for() if entries is None else entries):
        rows = _visible_rows(e, top) if e.scored else e.rows
        for r in rows:
            vals = (
                base, e.idx, e.loop, e.day, e.phase, e.decision, e.rows.index(r) + 1,
                r.get("total"), r.get("raw"), r.get("plus2"), r.get("b286"), r.get("b289"),
                r.get("void", False), _label(r["option"]), _same(r["option"], e.recorded),
                _same(r["option"], e.current), e.options_match, e.b215_swap, e.scored, e.source)
            if res.side != "mm":
                vals += (e.actor, r.get("vocab", ""), e.score_mode, e.diverged, e.prior_diverged)
            L.append("\t".join(_fmt(v) for v in vals))
    if res.source != "recorded" and (res.stop is not None or res.error):
        stop_vals = [base, "", "", "", "", "STOP", "", "", "", "", "", "", "",
                     (res.stop.describe() if res.stop else res.error),
                     "", "", "", "", "", (res.stop.kind if res.stop else "error")]
        if res.side != "mm":
            stop_vals += [""] * len(TSV_COLS_T11)
        L.append("\t".join(stop_vals))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def from_log(path: str, loop: int | None = None, day: int | None = None, *,
             seed: int = 0, top: int = 12, fmt: str = "md", source: str = "replay",
             decisions: set[str] | None = None, side: str = "both",
             prot_flags: str = "record") -> str:
    """決定録1本 → 点数表（文字列）。loop/day 省略＝全決定。side＝mm／prot／both（★T11・既定 both）。"""
    res = analyze(path, source=source, seed=seed, decisions_filter=decisions, side=side,
                  prot_flags=prot_flags)
    entries = select_entries(res, loop, day, decisions, side=side)
    if fmt == "tsv":
        return render_tsv(res, entries, top)
    return render_md(res, entries, top)


def smoke_state(res: ReplayResult) -> str:
    if res.replay_completed:
        return "完走"
    if res.replay_exhausted:
        return "終端"
    return "停止"


def smoke_line(path: str, seed: int = 0, side: str = "both") -> str:
    """1本ぶんの要約1行（再生できたか／どこで止まったか）。side != "mm" なら主人公席の件数も末尾に足す。"""
    try:
        res = analyze(path, source="replay", seed=seed, side=side)
    except Exception as e:  # noqa: BLE001  読込不能も棄権として1行で返す
        return f"{os.path.basename(path)}\t読込不能\t\t\t{type(e).__name__}: {e}"
    s = (f"{os.path.basename(path)}\t{smoke_state(res)}"
         f"\t脚本家決定={len(res.entries)}/{res.n_recorded_mm}"
         f"\toptions不一致={sum(1 for e in res.entries if not e.options_match)}"
         f"\t互換補完={len(res.compat_notes)}"
         f"\t{res.status_line()}")
    if side != "mm":
        s += (f"\t主人公決定={len(res.prot_entries)}/{res.n_recorded_prot}"
              f"\t食い違い={res.n_prot_diverged}")
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="棋譜再生の点数表（T4・計測のみ）")
    ap.add_argument("logs", nargs="+", help="決定録 jsonl（複数可）")
    ap.add_argument("--loop", type=int, default=None)
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--decision", default=None,
                    help="決定種で絞る（例 set_card,mastermind_ability）。省略＝脚本家の全決定")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0, help="現行AIの seed（同点のタイブレークのみ）")
    ap.add_argument("--source", choices=SOURCES, default="replay",
                    help="replay＝現行コードで局面を再構成（既定）／recorded＝記録の view/options／"
                         "auto＝再生が止まったら残りを recorded で補う")
    ap.add_argument("--format", choices=("md", "tsv"), default="md")
    ap.add_argument("--side", choices=SIDES, default="both",
                    help="★T11：採点する席。mm＝脚本家だけ（T4/T4b と bit 同一）／prot＝主人公 p1/p2/p3 だけ／"
                         "both＝両方を決定順に混ぜる（既定）")
    ap.add_argument("--prot-flags", choices=PROT_FLAGS, default="record",
                    help="★T11：先席が記録と食い違った席の席間協調フラグ。record＝記録の手で当て直す（既定）／"
                         "ai＝現行AI自身の手のまま")
    ap.add_argument("--smoke", action="store_true",
                    help="表を出さず、各棋譜が再生できたか／どこで止まったかを1行ずつ出す")
    ns = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("★警告: PYTHONHASHSEED=0 が付いていない（同点の解け方が hash 順で変わりうる）",
              file=sys.stderr)
    dec = {s.strip() for s in ns.decision.split(",") if s.strip()} if ns.decision else None
    if ns.smoke:
        counts = {"完走": 0, "終端": 0, "停止": 0, "読込不能": 0}
        for p in ns.logs:
            line = smoke_line(p, ns.seed, ns.side)
            st = line.split("\t")[1]
            counts[st] = counts.get(st, 0) + 1
            print(line)
        print("# " + " ".join(f"{k}={v}" for k, v in counts.items()) + f" / {len(ns.logs)}")
        return 0
    for i, p in enumerate(ns.logs):
        out = from_log(p, ns.loop, ns.day, seed=ns.seed, top=ns.top, fmt=ns.format,
                       source=ns.source, decisions=dec, side=ns.side, prot_flags=ns.prot_flags)
        if ns.format == "tsv" and i > 0:
            out = "\n".join(out.splitlines()[1:])
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
