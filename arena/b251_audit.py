# -*- coding: utf-8 -*-
"""B-251：**実戦棋譜の検死**（★計測のみ・AIの挙動には触れない）。

教材＝`docs/feedback_logs/鈴蘭_BTX3d_seed0_同期後の再戦_2026-08-18.jsonl`
（ユーザーが脚本家・AIが主人公・build `93afdfd`・5ループで脚本家の勝ち）。

## この道具がやること

保存済みの棋譜（JSONL）を **脚本家の選択だけ再生**し、**主人公は本物の AI に打たせ直す**。
主人公の選択が1手でも棋譜と食い違えば `verify` が RC=1 で落ちる＝**再現の担保**。
再現できている限り、ここで観測した点数・内部推定は**その席で実際に max() が見た値**である。

- 脚本家＝`_MMReplay`（記録された `chosen` を順に返すだけ）。
  ★`wants_bluff_options = True`＝人間の脚本家UIと同じ候補列（`sim/legal.set_card_options`
  の `allow_bluff`）を開く。これが無いと板へのダミー配置が非合法になり再生が割れる。
- 主人公＝`agents.debug.ProbedProtagonist`（**採点は本体そのもの**＝モジュール名前空間の
  `max` を差し替えて `max(options, key=score)` を横取りする）。`top` を大きく取れば全順位が出る。
- 内部推定は `decide` の**後**に読むだけ（`_ProbeX`）＝挙動に一切触れない。

## 二重実装をしない（§72-34）

採点器も合法手生成器も**書き写さない**。
- 点数＝`ProbedProtagonist` が横取りした本物のクロージャの値。
- 主人公能力フェイズの候補＝`sim.legal.goodwill_ability_options` を**記録ラッパで包んで**
  呼ぶ（`goodwill` サブコマンド）。`sim/flow._run_goodwill_phase` は
  `len(options) == 1`（pass のみ）なら `decide` を呼ばない＝**棋譜に決定行が無い**ことと
  「候補が生成されなかった」ことは同義ではない。ここを決定的に切り分けるための包み。

## 挙動には触れない

`agents/` `sim/` `engine/` の既定値を書き換えない。`verify` が
「主人公の全決定が棋譜と bit 一致」「最終状態が `meta.final_state` と一致」を確認する
＝差し替えが起きていれば必ず落ちる。

## ★era ピン（B-260・2026-08-19）

教材棋譜は **床 0.1 時代（build `93afdfd`）の収録**なので、
**現行のリポジトリ既定のまま再生すると数席が食い違う**（詳細＝`ERA_PINS` のコメント）。
∴ 本 CLI の**既定は「収録当時の既定へ倒して再生する」**＝`verify` は素で通る。
現行既定で再生したいときは `--no-era-pin`。
★**どちらで走ったかは毎回 1行目に印字される**（後から報告を読む人が取り違えないため）。

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:

    python -m arena.b251_audit verify                 # ★era ピン ON（既定）
    python -m arena.b251_audit --no-era-pin verify    # 現行のリポジトリ既定で再生
    python -m arena.b251_audit goodwill
    python -m arena.b251_audit internals
    python -m arena.b251_audit rank --card 友好 --target サラリーマン
    python -m arena.b251_audit seat --at L1D3 --seat p2 --top 12

結果＝`docs/検死_B251_鈴蘭BTX3d_seed0_5連敗_2026-08-18.md`。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from agents.debug import ProbedProtagonist
from sim import flow
from sim.state import GameState, script_from_dict

#: 教材棋譜（B-251 のチケットが指定した1本）。
DEFAULT_LOG = (Path(__file__).resolve().parent.parent / "docs" / "feedback_logs"
               / "鈴蘭_BTX3d_seed0_同期後の再戦_2026-08-18.jsonl")

# ---------------------------------------------------------------------------
# ★B-260：era ピン（教材棋譜が録られた**当時の既定**へ倒して再生する明示スイッチ）
# ---------------------------------------------------------------------------
#: 収録当時（build `93afdfd`・2026-08-18）と現在で**食い違う既定**を、収録当時の値へ倒す表。
#:
#: ★なぜ倒すのか
#:   2026-08-19 のユーザー裁定（§72-56）で `B252_CULT_FLOOR` が**既定 ON** になり、
#:   カルティスト移動封じピンの母集合の**絶対床**が `_CULT_MAYBE_P`(0.1) から
#:   `_B252_CULT_FLOOR_P`(0.15) に上がった。教材棋譜は**床 0.1 時代の収録**なので、
#:   現行既定のまま再生すると「しきい値の直上（0.1 < p <= 0.15）」の帯に居た候補だけが
#:   母集合から落ち、その席のピンが消える。
#:   ＝**棋譜が壊れたのでも道具が壊れたのでもなく、既定が変わった**。
#:   本道具の契約は「保存済みの棋譜が1手も食い違わずに再生できる＝ここで読む点数は
#:   実際に max() が見た値」という**その棋譜についての不変量**なので、
#:   B-201／B-204／B-202／B-192／B-258 と同じ作法で**収録当時の既定へ倒して**再生する。
#:
#: ★どの席が動くのか（B-258 が `tests/test_b251_audit.py` で名指しで固定した実測）
#:   - 鈴蘭ログ **L3D3 p1**＝`移動禁止→巫女`（p(カルティスト|巫女)=0.1133）が消える
#:     → 玉突きで p2 の手も動く（計2席・最終状態は一致）。
#:   - 封印ログ **L2D3 p3**＝`移動禁止→転校生`（p=0.1062）が消える
#:     → 玉突きで p1/p2 も動く（計3席）。
#:   いずれも B-252(a) が狙って消した**型I（しきい値の直上）**そのもの（§72-53／§72-56）。
#:
#: ★追加（B-267・2026-08-19）＝`B256_UNREACHABLE_SKIP` が**既定 ON** になった
#:   （ユーザー裁定＝`B262_BELIEF_BOUND` と対で ON）。教材棋譜は**両方 OFF 時代の収録**。
#:   現行既定のまま再生すると「不安3へ算術的に届きようがない対象」がウイルス試験の
#:   候補プールから落ち、その席の手が変わる。
#: ★どの席が動くのか（実測・2026-08-19）
#:   - 鈴蘭ログ **L3D2 p1**＝棋譜 `不安+1→女子学生` が再生では `不安+1→サラリーマン`。
#:   - 玉突きで **L2D3 p2**＝棋譜 `友好+1→巫女` が再生では `移動←→→巫女`（計2席）。
#:   ＝倒さないと `NG 2 件`（RC=1）／倒すと **OK（全決定・最終状態が一致）**。
#: ★`B262_BELIEF_BOUND`（同時に既定 ON）は**倒さない**＝**最小限の原則**（B-260 の教訓）。
#:   `bel` は `skip` の内側でしか読まれない（`heuristic_protagonist` の
#:   `if self.B256_UNREACHABLE_SKIP:` ガード内＝`tests/test_b256_unreachable_unrest.py`
#:   ／`tests/test_b262_belief_bound.py` の構造テストが固定）ので、`skip` を倒せば
#:   `bel` は**発火しない**。実測でも `skip` のみで OK ／ `bel` のみでは NG 2 件のまま。
#:   ∴ 印字だけする（`AUDITED_CONSTS`）。
#: ★倒す対象は**最小限**＝実測で再生が割れた切替口だけ。他の切替口は1bitも触らない。
ERA_PINS: tuple = (
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B252_CULT_FLOOR", False),
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B256_UNREACHABLE_SKIP", False),
)

#: era ピンの状態と一緒に**必ず印字する**定数（`arena/b249_audit.AUDITED_CONSTS` と同趣旨）。
#: ★B-249 が B-258 で `B252_CULT_FLOOR` を足したのと同じ理由＝
#:   「この再生が何を根拠に成立しているか」を出力だけで追えるようにする。
AUDITED_CONSTS: tuple = (
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B252_CULT_FLOOR"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "_B252_CULT_FLOOR_P"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "_CULT_MAYBE_P"),
    # ★B-267：倒す方（skip）と、倒さないが効き方の前提になる方（bel）を両方印字する。
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B256_UNREACHABLE_SKIP"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B262_BELIEF_BOUND"),
)


def _holder(mod: str, cls: str | None):
    """定数の持ち主を解決する（`cls=None` ＝モジュール直下の定数）。"""
    m = importlib.import_module(mod)
    return m if cls is None else getattr(m, cls)


def _where(cls: str | None, attr: str) -> str:
    return f"{cls}.{attr}" if cls else attr


@contextlib.contextmanager
def era_pin(enabled: bool = True, pins: tuple = ERA_PINS):
    """`enabled=True` の間だけ `pins` を収録当時の値へ倒す（★例外時も必ず復元）。

    `agents/` は**1バイトも変更しない**＝クラス／モジュール属性を測定中だけ差し替え、
    `finally` で戻す。`enabled=False` は**何もしない**（現行のリポジトリ既定のまま再生する）。

    ★`pins` を引数に取るのは、**同型の道具が自分の era 表を渡して使い回せる**ようにするため
      （二重実装をしない＝§72-34。B-250/B-252 が B-249 のヘルパを import するのと同じ作法）。
    """
    if not enabled:
        yield False
        return
    saved: list = []
    try:
        for mod, cls, attr, val in pins:
            obj = _holder(mod, cls)
            saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, val)
        yield True
    finally:
        # ★例外が出ても・途中で失敗しても、倒した分だけ必ず戻す。
        for obj, attr, old in saved:
            setattr(obj, attr, old)


def era_banner(enabled: bool, *, pins: tuple = ERA_PINS,
               consts: tuple = AUDITED_CONSTS, tag: str = "b251") -> None:
    """★**どちらの既定で再生したか**を必ず印字する（後から報告を読む人の取り違え防止）。

    ★`era_pin()` の**内側**で呼ぶこと＝印字する値は「実際に再生で使われる実効値」。
    """
    mode = ("収録当時の既定へ倒して再生（era ピン ON＝既定）"
            if enabled else "現行のリポジトリ既定で再生（--no-era-pin）")
    print(f"[{tag}] era ピン: {mode}")
    for mod, cls, attr in consts:
        print(f"    {_where(cls, attr)} = {getattr(_holder(mod, cls), attr)!r}")
    if enabled:
        pinned = ", ".join(f"{_where(cls, attr)}={val!r}" for _m, cls, attr, val in pins)
        print(f"    ★測定中だけ倒した既定（finally で必ず復元）: {pinned}")
    else:
        print("    ★既定は1つも倒していない＝棋譜と食い違う席が出るのが正常"
              "（名指しの席＝各道具の `ERA_PINS` のコメント参照）")


#: `decide` の後に読み出す内部推定（読むだけ＝挙動に触れない）。
PROBE_ATTRS = (
    "_observed_defeat_board", "_b195_card_proven", "_purge_target",
    "_incident_danger", "_cooler_invested", "_invest", "_invest_need",
    "_gw_blocked", "_keyperson", "_killer", "_loop_lost", "_experiment",
    "_b66_decoy_boards", "_board_defeat_probs",
)


def load_log(path: Path) -> tuple[dict, list[dict]]:
    """JSONL を (meta, decisions) に割る。"""
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not rows or rows[0].get("type") != "meta":
        raise ValueError(f"1行目が meta でない: {path}")
    return rows[0], [r for r in rows[1:] if r.get("type") == "decision"]


class ReplayDerailed(RuntimeError):
    """★再生が**脱線**した（＝脚本家の記録手が候補に無い／選択列が尽きた）。

    B-267（2026-08-19）で追加。主人公側の既定が動くと、その日の盤面自体が棋譜と変わり、
    **脚本家の記録手がそもそも合法手として出てこない**ことがある（実例＝`--no-era-pin` で
    seal 棋譜を再生すると L4D1 の `ご神木:move` が候補に無い）。
    これは道具のバグでも棋譜の破損でもなく「**再生が割れた**」の最も強い形なので、
    `verify` は traceback ではなく **NG 扱い（RC=1）** で報告する。
    """


class _MMReplay:
    """記録された脚本家の選択を順に返すだけ（人間の席の再生）。"""

    #: 人間の脚本家UIと同じ候補列を開く（`sim/flow.attach_mm_bluff` が読む）。
    wants_bluff_options = True

    def __init__(self, seq: list[dict]):
        self._seq = list(seq)
        self._i = 0

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if self._i >= len(self._seq):
            raise ReplayDerailed("脚本家の選択列が尽きた（棋譜が途中で切れている）")
        chosen = self._seq[self._i]
        self._i += 1
        if chosen not in options:
            raise ReplayDerailed(
                f"脚本家の記録手が候補に無い（{self._i}手目 {decision}）: {chosen}")
        return chosen


class _ProbeX(ProbedProtagonist):
    """`ProbedProtagonist` に内部推定のスナップショットを1件足すだけ。

    ★読み出しは `super().decide()` の**後**＝採点にも選択にも影響しない。
    """

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        chosen = super().decide(view, decision, options)
        rec = self.records[-1]
        snap: dict = {}
        for name in PROBE_ATTRS:
            val = getattr(self, name, None)
            snap[name] = sorted(val) if isinstance(val, (set, frozenset)) else val
        try:
            snap["danger_board"] = self._guess_defeat_board(view)
        except Exception as exc:                     # noqa: BLE001  観測失敗は握って残す
            snap["danger_board"] = f"ERR {exc}"
        try:
            ry: dict = {}
            for (y, _xs), p in self._belief.rule_marginals().items():
                ry[y] = ry.get(y, 0.0) + p
            snap["rule_y"] = {k: round(v, 4) for k, v in
                              sorted(ry.items(), key=lambda t: -t[1]) if v > 1e-4}
            snap["witch"] = self._belief.most_likely_role("ウィッチ")
            snap["witch_marg"] = {n: round(d.get("ウィッチ", 0.0), 4)
                                  for n, d in self._belief.role_marginals().items()}
        except Exception as exc:                     # noqa: BLE001
            snap["rule_y"] = f"ERR {exc}"
            snap["witch"] = None
            snap["witch_marg"] = {}
        snap["board_anyaku"] = dict(view.get("board_anyaku") or {})
        rec["probe"] = snap
        return chosen


#: 主人公能力フェイズの候補が出ない理由を、`sim/legal.goodwill_ability_options`（`sim/legal.py:196-227`）の
#  ふるいと**同じ順序**で1つずつ突き合わせて説明する。★判定そのものは本物の関数を呼ぶ
#  （`ability_targets` / `is_implemented` / `goshinboku_move_options`）＝述語を書き写さない。
def _why_no_options(state, used_this_turn) -> list[str]:
    from engine.data import goodwill_abilities_of
    from sim.abilities import ability_targets, is_implemented
    from sim.effects import goshinboku_move_options
    out: list[str] = []
    for name, c in state.characters.items():
        for ab in goodwill_abilities_of(name) or []:
            aname, need = ab["name"], ab["hearts"]
            tag = f"{name}:{aname}(♡{need})"
            if not c.alive:
                out.append(f"{tag} ×死亡"); continue
            if not c.on_board:
                out.append(f"{tag} ×未登場"); continue
            if not is_implemented(name, aname):
                out.append(f"{tag} ×未実装"); continue
            if c.goodwill < need:
                out.append(f"{tag} ×友好{c.goodwill}<{need}"); continue
            if (name, aname) in used_this_turn:
                out.append(f"{tag} ×このターン使用済"); continue
            if ab["once_per_loop"] and (name, aname) in state.used_goodwill:
                out.append(f"{tag} ×1/Lで使用済"); continue
            tgts = list(ability_targets(state, name, aname))
            if not tgts:
                out.append(f"{tag} ×対象なし（現在地={c.area}）"); continue
            out.append(f"{tag} ○対象={tgts}")
    if ("ご神木", "trait") in used_this_turn:
        out.append("ご神木:特性 ×このターン使用済")
    else:
        gc = state.characters.get("ご神木")
        if gc is None:
            pass
        elif not (gc.alive and gc.on_board):
            out.append("ご神木:特性 ×不在")
        else:
            opts = goshinboku_move_options(state)
            if opts:
                out.append(f"ご神木:特性 ○{len(opts)}件")
            else:
                mates = [n for n, c2 in state.characters.items()
                         if n != "ご神木" and c2.alive and c2.on_board and c2.area == gc.area]
                out.append(
                    "ご神木:特性 ×"
                    + ("同エリアに他キャラなし" if not mates
                       else f"ご神木にカウンターが1つも無い（友好{gc.goodwill}/不安{gc.unrest}/暗躍{gc.anyaku}・同室={mates}）"))
    return out


def replay(path: Path, top: int = 999, spy_goodwill: bool = False):
    """棋譜を再生して (state, meta, ai, live_log, gw_log) を返す。

    脚本家＝記録の再生／主人公＝本物の AI（打ち直し）。`spy_goodwill=True` のときだけ
    `sim.legal.goodwill_ability_options` を**記録ラッパで包む**（呼び出しは本物へ委譲）。
    """
    meta, decisions = load_log(path)
    script = replace(script_from_dict(meta["script"]), loops=int(meta["loops_played"]))
    mm_seq = [d["chosen"] for d in decisions if d["actor"] == "mastermind"]

    gw_log: list[dict] = []
    real_gw = flow.legal.goodwill_ability_options

    def _spy(state, used=frozenset()):
        opts = real_gw(state, used)
        miko = state.characters.get("巫女")
        gw_log.append({
            "loop": state.loop_no, "day": state.day, "leader": state.leader,
            "n": len(opts), "options": [dict(o) for o in opts],
            "board_anyaku": dict(state.board_anyaku),
            "chars": {n: {"area": c.area, "goodwill": c.goodwill, "unrest": c.unrest,
                          "anyaku": c.anyaku, "alive": c.alive, "on_board": c.on_board}
                      for n, c in state.characters.items()},
            "why": _why_no_options(state, used),
            "miko": None if miko is None else (miko.area, miko.goodwill, miko.alive),
        })
        return opts

    ai = _ProbeX(0, top=top)
    state = GameState(script=script)
    live: list[dict] = []
    decide = flow._make_decider(
        state, {"mastermind": _MMReplay(mm_seq), "p1": ai, "p2": ai, "p3": ai}, live)
    if spy_goodwill:
        flow.legal.goodwill_ability_options = _spy
    try:
        flow.run_loop(state, decide, final_battle=False)
    finally:
        flow.legal.goodwill_ability_options = real_gw
    return state, meta, ai, live, gw_log


def _strip(chosen: dict) -> dict:
    """表示層の provenance（`prov`）を剥がした写し（`sim/flow.log_safe_chosen` と同趣旨）。"""
    return {k: v for k, v in chosen.items() if k != "prov"}


def check_replay(state, meta: dict, live: list[dict]) -> list[str]:
    """再生が棋譜と完全一致するか。食い違いの説明文リスト（空＝一致）を返す。"""
    _m, ref = load_log(Path(meta["__path__"])) if "__path__" in meta else (None, None)
    if ref is None:
        raise ValueError("check_replay には meta['__path__'] が要る")
    bad: list[str] = []
    if len(live) != len(ref):
        bad.append(f"決定数が違う: 再生{len(live)} / 棋譜{len(ref)}")
    for i, (a, b) in enumerate(zip(live, ref)):
        ka = (a["loop"], a["day"], a["phase"], a["actor"], a["decision"])
        kb = (b["loop"], b["day"], b["phase"], b["actor"], b["decision"])
        if ka != kb or _strip(a["chosen"]) != _strip(b["chosen"]):
            bad.append(f"#{i} 再生 {ka} {_strip(a['chosen'])} / 棋譜 {kb} {_strip(b['chosen'])}")
    if state.to_dict() != meta["final_state"]:
        bad.append("最終状態が meta.final_state と一致しない")
    return bad


def _fmt(o: dict) -> str:
    if "card" in o:
        return f'{o["card"]}→{o.get("target")}'
    if "action" in o:
        return o["action"] + (f'→{o.get("target")}' if o.get("target") else "")
    if "character" in o:
        return f'{o["character"]}:{o.get("ability")}→{o.get("target")}'
    return json.dumps(o, ensure_ascii=False)


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def cmd_verify(args) -> int:
    path = Path(args.log)
    print(f"棋譜: {path.name}")
    try:
        state, meta, ai, live, _gw = replay(path, top=1)
    except ReplayDerailed as exc:
        # ★B-267＝主人公の既定が動くと盤面ごと変わり、脚本家の記録手が合法手から消える。
        #   traceback ではなく「割れている」として RC=1 で報告する（`--no-era-pin` の正常系）。
        print(f"  NG: 再生が脱線した＝{exc}")
        print("NG（脱線）＝再生が割れている（この棋譜での測定は無効）")
        return 1
    meta["__path__"] = str(path)
    bad = check_replay(state, meta, live)
    print(f"build(meta)={meta.get('tool_build')} 決定数={len(live)} winner={state.winner} "
          f"loops={state.loop_no}")
    if bad:
        for b in bad[:20]:
            print("  NG:", b)
        print(f"NG {len(bad)} 件＝再生が割れている（この棋譜での測定は無効）")
        return 1
    print("OK: 主人公の全決定・最終状態が棋譜と一致（＝以降の点数はその席の実値）")
    return 0


def cmd_goodwill(args) -> int:
    """主人公能力フェイズの**候補列**を毎回そのまま出す（生成されたか／pass したか）。"""
    path = Path(args.log)
    state, meta, ai, live, gw = replay(path, top=1, spy_goodwill=True)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    used = {(d["loop"], d["day"]): d for d in live if d["decision"] == "goodwill_ability"}
    print("主人公能力フェイズ：候補列（pass のみ＝n=1 なら flow は decide を呼ばない）")
    for g in gw:
        names = [_fmt(o) if o.get("action") != "pass" else "pass" for o in g["options"]]
        mark = "決定あり" if (g["loop"], g["day"]) in used else "決定なし"
        print(f'L{g["loop"]}D{g["day"]} leader={g["leader"]} n={g["n"]} [{mark}] '
              f'巫女(エリア,友好)={g["miko"][:2] if g["miko"] else None} '
              f'神社暗躍={g["board_anyaku"].get("神社")} :: {names}')
        if args.why:
            for line in g["why"]:
                print("      " + line)
    n_gen = sum(1 for g in gw if g["n"] > 1)
    print(f"\n合計 {len(gw)} 回のうち、pass 以外の候補が生成されたのは {n_gen} 回")
    return 0


def cmd_internals(args) -> int:
    """日ごと（その日の先頭席）の内部推定を出す。"""
    path = Path(args.log)
    state, meta, ai, live, _gw = replay(path, top=1)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    seen: set = set()
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        key = (r["loop"], r["day"])
        if key in seen:
            continue
        seen.add(key)
        p = r["probe"]
        print(f'L{r["loop"]}D{r["day"]} danger_board={p["danger_board"]} '
              f'odb={p["_observed_defeat_board"]} card_proven={p["_b195_card_proven"]} '
              f'purge={p["_purge_target"]}')
        print(f'    ruleY={p["rule_y"]}  ウィッチ最有力={p["witch"]}')
        print(f'    板の敗北確率={ {k: round(v, 3) for k, v in (p["_board_defeat_probs"] or {}).items()} } '
              f'囮判定={p["_b66_decoy_boards"]} 盤面暗躍={p["board_anyaku"]}')
    return 0


def cmd_rank(args) -> int:
    """指定した札／対象の option が各席で何点・何位だったかを一覧する。"""
    path = Path(args.log)
    state, meta, ai, live, _gw = replay(path, top=args.top)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    hits = 0
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        scored = r["scored"]
        total = len(scored)
        for i, (v, o) in enumerate(scored):
            if args.card and args.card not in str(o.get("card", "")):
                continue
            if args.target and args.target != o.get("target"):
                continue
            hits += 1
            top_v, top_o = scored[0]
            print(f'L{r["loop"]}D{r["day"]} {r["seat"]:3s} {_fmt(o):22s} '
                  f'{v:8.2f} {i + 1:3d}位/{total}  '
                  f'首位={_fmt(top_o)}({top_v:.2f}) 実手={_fmt(r["chosen"]):18s} '
                  f'purge={r["probe"]["_purge_target"]} danger={r["probe"]["danger_board"]}')
    print(f"\n該当 {hits} 件")
    return 0


def cmd_seat(args) -> int:
    """1席の上位候補を全部出す（--at L1D3 --seat p2）。"""
    path = Path(args.log)
    state, meta, ai, live, _gw = replay(path, top=args.top)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    want = args.at.upper() if args.at else None
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        tag = f'L{r["loop"]}D{r["day"]}'
        if want and tag != want:
            continue
        if args.seat and r["seat"] != args.seat:
            continue
        p = r["probe"]
        print(f'== {tag} {r["seat"]} 実手={_fmt(r["chosen"])} '
              f'danger_board={p["danger_board"]} purge={p["_purge_target"]}')
        for i, (v, o) in enumerate(r["scored"][: args.show]):
            print(f'   {i + 1:3d}. {v:8.2f}  {_fmt(o)}')
        print()
    return 0


def cmd_guard(args) -> int:
    """`暗躍禁止` の宛先を、**脚本家がその日その対象へ実際に伏せた札**（神視点＝棋譜の
    脚本家決定）と突き合わせる。宛先が板でない席で何が起きていたかを数える。"""
    path = Path(args.log)
    state, meta, ai, live, _gw = replay(path, top=args.top)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    # 脚本家がその日その対象へ置いた札（神視点）＝棋譜の mastermind set_card 決定。
    mm_by_day: dict = {}
    _m, decisions = load_log(path)
    for d in decisions:
        if d["actor"] == "mastermind" and d["decision"] == "set_card":
            mm_by_day.setdefault((d["loop"], d["day"]), []).append(d["chosen"])
    from collections import Counter
    tally: Counter = Counter()
    print("AI の 暗躍禁止：宛先／点数／同じ席で board を選んだ場合の点数／脚本家の実札")
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        ch = r["chosen"]
        if ch.get("card") != "暗躍禁止":
            continue
        scored = r["scored"]
        best_board = next(((v, o) for v, o in scored
                           if o.get("card") == "暗躍禁止"
                           and o.get("target_kind") == "board"), None)
        my = next(v for v, o in scored if o is ch or o == ch)
        real = [c for c in mm_by_day.get((r["loop"], r["day"]), [])
                if c.get("target") == ch.get("target")]
        tally[(ch.get("target_kind"), ch.get("target"))] += 1
        bb = f'{_fmt(best_board[1])}({best_board[0]:.2f})' if best_board else "なし"
        print(f'L{r["loop"]}D{r["day"]} {r["seat"]:3s} → {ch.get("target"):5s} '
              f'{my:7.2f} / 板の最上位={bb:24s} 脚本家の実札={[c.get("card") for c in real]}')
    print("\n宛先の内訳:", dict(tally))
    return 0


def cmd_truth(args) -> int:
    """★B-250 の教訓（「正解が候補集合に入っているか」を先に分けて数える）を B-251 に当てる。

    毎日（その日の先頭席）について
      - ルールY の真値（`meta.script.rule_y`）が belief の台（support）に残っているか・確率・順位
      - ウィッチの真の担当（＝ボードX の持ち主）が役職周辺確率の台に残っているか・確率・順位
      - 真の敗北板が `_board_defeat_probs` の台に残っているか・確率・順位
    を出す。**「候補に無い（belief の問題）」と「候補にあるが下位（評価器/点推定の問題）」を分ける**。
    """
    path = Path(args.log)
    state, meta, ai, live, _gw = replay(path, top=1)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    sc = meta["script"]
    true_y = sc.get("rule_y")
    roles = sc.get("roles") or {}
    true_witch = next((n for n, r in roles.items() if r == "ウィッチ"), None)
    true_board = (meta.get("final_state") or {}).get("rule_y_board_x")
    print(f"真値: ルールY={true_y} / ウィッチ={true_witch} / 敗北板={true_board}")

    def _rank(d: dict, key):
        if key is None or key not in d:
            return None, 0.0
        order = sorted(d.items(), key=lambda t: -t[1])
        for i, (k, v) in enumerate(order):
            if k == key:
                return i + 1, v
        return None, 0.0

    seen: set = set()
    for r in ai.records:
        if r["decision"] != "set_card":
            continue
        key = (r["loop"], r["day"])
        if key in seen:
            continue
        seen.add(key)
        p = r["probe"]
        ry = p["rule_y"] if isinstance(p["rule_y"], dict) else {}
        ry_rank, ry_p = _rank(ry, true_y)
        wr = p.get("witch_marg") or {}
        w_rank, w_p = _rank(wr, true_witch)
        bp = p.get("_board_defeat_probs") or {}
        b_rank, b_p = _rank({k: v for k, v in bp.items() if v > 0}, true_board)
        print(f'L{r["loop"]}D{r["day"]} '
              f'ルールY: {"台にあり" if ry_rank else "★台から消えた"} p={ry_p:.3f} {ry_rank}位/{len(ry)}  |  '
              f'ウィッチ: {"台にあり" if w_rank else "★台から消えた"} p={w_p:.3f} {w_rank}位/{len([1 for v in wr.values() if v > 0])}  |  '
              f'敗北板: {"台にあり" if b_rank else "★台から消えた"} p={b_p:.3f} {b_rank}位  '
              f'(点推定={p["danger_board"]})')
    return 0


def rule_y_marginals(belief) -> dict:
    """ルールY だけの周辺確率（`Belief.rule_marginals` は (Y, Xs) の組が鍵）。"""
    out: dict = {}
    for (y, _xs), p in belief.rule_marginals().items():
        out[y] = out.get(y, 0.0) + p
    return out


def belief_after(view: dict, extra: list[dict]):
    """棋譜の公開履歴に**仮想の観測**を足して belief を作り直す（読み取り専用の反実仮想）。

    ★`agents.belief.Belief` は公開履歴だけを材料にする純関数的な推論器＝
    「その観測が実際に起きていたら事後がどう動いたか」をこの形で測れる。
    """
    from agents.belief import Belief
    cast = [c["name"] for c in view["characters"]]
    b = Belief(cast, view.get("incidents", []), set_name=view.get("set", "FS"))
    b.observe(list(view.get("history", [])) + list(extra))
    return b


def cmd_reveal(args) -> int:
    """★(1-b)：**友好能力の宣言に対する応答（拒否／解決）が belief をどう動かすか**を実測する。

    材料＝棋譜の最終席の公開履歴。そこへ仮想の観測を1件足して事後を比べる。
    ★KB の正しい読み（`rules/20_goodwill_abilities.md:24` / `engine/data.ROLE_CLAUSE_ABILITY`）：
      拒否できるのは**友好無視／絶対友好無視**を持つ役職＝BTX ではキラー／クロマク／
      カルティスト／ウィッチ／ファクターの5つ。しかも通常の友好無視は【任意】＝
      **1回の拒否では「絶対」か「通常」かの区別すらつかない**。∴ 拒否1回から言えるのは
      「その5役職のいずれか」までで、**ウィッチ確定ではない**。
    """
    path = Path(args.log)
    state, meta, ai, live, _gw = replay(path, top=1)
    meta["__path__"] = str(path)
    if check_replay(state, meta, live):
        print("NG: 再生が割れている", file=sys.stderr)
        return 1
    seats = [d for d in live if d["decision"] == "set_card" and d["actor"] != "mastermind"]
    view = seats[-1]["view"]
    who = args.who
    lp, dy = view.get("loop"), view.get("day")

    def _show(label: str, extra: list[dict]) -> None:
        b = belief_after(view, extra)
        ry = {k: round(v, 4) for k, v in
              sorted(rule_y_marginals(b).items(), key=lambda t: -t[1]) if v > 1e-4}
        rm = {k: round(v, 3) for k, v in
              sorted(b.role_marginals().get(who, {}).items(), key=lambda t: -t[1]) if v > 1e-3}
        print(f"{label}\n    ルールY={ry}\n    {who}の役職={rm}")

    print(f"材料＝L{lp}D{dy} の公開履歴（{path.name}）／注目キャラ={who}")
    _show("[実測] 棋譜そのまま", [])
    _show(f"[仮想] {who} の友好能力が**拒否された**",
          [{"loop": lp, "day": dy, "phase": "goodwill_ability",
            "event": "goodwill_refused", "character": who, "ability": args.ability}])
    _show(f"[仮想] {who} の友好能力が**拒否されず解決した**",
          [{"loop": lp, "day": dy, "phase": "goodwill_ability",
            "event": "goodwill_resolved", "character": who, "ability": args.ability}])
    if args.also:
        _show(f"[仮想] {who} と {args.also} の**2人が拒否した**",
              [{"loop": lp, "day": dy, "phase": "goodwill_ability",
                "event": "goodwill_refused", "character": c, "ability": args.ability}
               for c in (who, args.also)])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B-251 実戦検死（計測のみ）")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="棋譜JSONL")
    # ★B-260：era ピンの明示スイッチ（既定＝収録当時の既定へ倒して再生する）。
    ap.add_argument("--era-pin", dest="era_pin", action="store_true", default=True,
                    help="教材棋譜が録られた当時の既定へ倒して再生する（★既定）")
    ap.add_argument("--no-era-pin", dest="era_pin", action="store_false",
                    help="現行のリポジトリ既定のまま再生する（食い違う席が出るのが正常）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify", help="再生が棋譜と一致するか（RC=1で不一致）")
    pgw = sub.add_parser("goodwill", help="主人公能力フェイズの候補列を毎回出す")
    pgw.add_argument("--why", action="store_true",
                     help="候補が出ない理由を legal.py と同じ順序のふるいで説明する")
    sub.add_parser("internals", help="日ごとの内部推定（危険板・odb・purge・ルールY）")

    pr = sub.add_parser("rank", help="指定した札/対象の点数と順位")
    pr.add_argument("--card", default=None, help="札名の部分一致（例 友好）")
    pr.add_argument("--target", default=None, help="対象名の完全一致")
    pr.add_argument("--top", type=int, default=999, help="記録する順位の深さ")

    ps = sub.add_parser("seat", help="1席の上位候補")
    ps.add_argument("--at", default=None, help="L1D3 のような席の位置")
    ps.add_argument("--seat", default=None, help="p1/p2/p3")
    ps.add_argument("--top", type=int, default=999)
    ps.add_argument("--show", type=int, default=10)

    sub.add_parser("truth", help="真値が belief の台に残っているか（候補集合と順位を分けて数える）")

    prv = sub.add_parser("reveal", help="友好能力の拒否/解決が belief をどう動かすかの反実仮想")
    prv.add_argument("--who", default="サラリーマン")
    prv.add_argument("--ability", default="自身の役職開示")
    prv.add_argument("--also", default=None, help="2人目が拒否した場合も見る")

    pg = sub.add_parser("guard", help="暗躍禁止の宛先と脚本家の実札の突き合わせ")
    pg.add_argument("--top", type=int, default=999)

    args = ap.parse_args(argv)
    cmds = {"verify": cmd_verify, "goodwill": cmd_goodwill, "internals": cmd_internals,
            "rank": cmd_rank, "seat": cmd_seat, "guard": cmd_guard,
            "truth": cmd_truth, "reveal": cmd_reveal}
    # ★era ピンは**全サブコマンド**に掛ける（`verify` 以外も同じ棋譜を再生するため）。
    #   `era_banner` はピンの内側＝印字される値は実際に再生で使われた実効値。
    with era_pin(args.era_pin) as pinned:
        era_banner(bool(pinned))
        return cmds[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
