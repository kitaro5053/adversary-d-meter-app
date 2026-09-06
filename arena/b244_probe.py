# -*- coding: utf-8 -*-
"""B-244：ユーザー（人間の脚本家）実戦棋譜の**検死**プローブ。

★読み取り専用＝`agents/` `sim/` `engine/` `rules/` には一切触れない。

教材＝`docs/feedback_logs/鈴蘭_BTX3d_seed0_4つON後検証_2026-08-17.jsonl`。
この棋譜は**途中（L3D3）から**しか `decision` 行を持たない（保存局面からの再開）ため、
L1・L2 の疑問手はログの `view` からは読めない。そこで：

  meta.history（全日の6枚公開）＋ meta.secret_log（脚本家能力の選択）から
  **脚本家席の選択列を復元**し、`arena.interactive.play_interactive` で
  human_seats={"mastermind"} として1局まるごと再実行する。
  主人公席は `agents.debug.ProbedProtagonist`＝**本体と同一挙動**で全候補スコアを記録。

再実行が meta.history と**完全一致**すれば、復元は正しい（＝検死の土台が健全）。

★B-263（2026-08-20・verify の2バグ修正）：
  1. **`prov` 剥がし**＝再実行側の history は主人公の採択に `prov`（★argmax と一致した席に
     付く**注記タグ**・原因ラベルではない＝B-266 の教訓。`sim/flow.log_safe_chosen` 参照）を
     残すが、収録側の `meta.history` は持たない＝剥がさず突き合わせると**中身が同一の行が
     偽の不一致**になる（実測＝既定教材の idx=8）。→ `_strip_prov`（`arena/u12_audit` と同趣旨・
     placements が入れ子なので**再帰**で剥がす）を通してから比較する。
  2. **全行検査**＝旧実装は最初の相違で表示を打ち切り、`zip` が短い側で黙って切れる＝
     「23/98 行で done」と報告していた。→ `compare_histories` が**分母＝max(両者の行数)で
     全行を検査し切り**、不一致 idx の全列挙・未生成/余剰の行数・prov だけの差を分けて返す。
★era について（B-263 の切り分け・実測）：既定教材の残る実不一致（idx=14〜＝L1D3 p3 の
  `暗躍禁止→刑事`（再実行）vs `→都市`（棋譜））は、(a) 現行切替口 108 本の単独 OFF 掃引で
  1本も直らず、(b) PYTHONHASHSEED 0〜15 で不変＝hash seed でも説明が付かない。収録 build
  `3f055ea` は**デプロイ用ミラー（別リポジトリ）のコミットで本リポジトリから解決不能**（§72-72
  B-264 の族）＝era ピンを付けても部分ピンにしかならないので**付けない**（B-260 の教訓＝
  部分ピンは「era 補正済み」と誤読させるので有害）。∴ この教材の「bit 一致 n/N」を
  再現性の担保として読んではいけない。

CLI:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b244_probe verify
    PYTHONHASHSEED=0 ... python -m arena.b244_probe scores --loop 1 --day 3 --top 12
    PYTHONHASHSEED=0 ... python -m arena.b244_probe repeat
    PYTHONHASHSEED=0 ... python -m arena.b244_probe separation
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG = ("docs/feedback_logs/鈴蘭_BTX3d_seed0_4つON後検証_2026-08-17.jsonl")


# ------------------------------------------------------------------ ログ読み
def load_log(path: str = LOG):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    meta = rows[0]
    decisions = [r for r in rows[1:] if r.get("type") == "decision"]
    return meta, decisions


def mm_choice_queues(meta: dict) -> dict:
    """(loop, day) -> {"set": [...], "ability": [...], "incident": name|None,
                       "turn_end": bool}"""
    q: dict = defaultdict(lambda: {"set": [], "ability": [], "incident": None,
                                   "turn_end": False})
    for h in meta["history"]:
        if h.get("event") == "cards_revealed":
            key = (h["loop"], h["day"])
            for p in h["placements"]:
                if p["owner"] == "mastermind":
                    q[key]["set"].append({"card": p["card"], "target": p["target"],
                                          "target_kind": p["target_kind"]})
    for s in meta.get("secret_log") or []:
        key = (s["loop"], s["day"])
        if s.get("event") == "mm_ability":
            q[key]["ability"].append(dict(s["choice"]))
        elif s.get("event") == "death":
            q[key]["incident"] = s.get("name")
        elif s.get("event") == "protagonist_death":
            q[key]["turn_end"] = True
    return q


class MMOracle:
    """棋譜から脚本家席の1決定を答える。play_interactive の PendingHuman に応じる。"""

    def __init__(self, meta: dict):
        self.q = mm_choice_queues(meta)
        self.used: dict = defaultdict(lambda: {"set": 0, "ability": 0})

    def answer(self, state, decision: str, options: list[dict]) -> dict:
        key = (state.loop_no, state.day)
        rec = self.q[key]
        if decision == "set_card":
            i = self.used[key]["set"]
            self.used[key]["set"] += 1
            want = rec["set"][i]
            for o in options:
                if o == want:
                    return o
            raise RuntimeError(f"{key} set_card 復元失敗: {want}")
        if decision == "mastermind_ability":
            i = self.used[key]["ability"]
            if i < len(rec["ability"]):
                self.used[key]["ability"] += 1
                want = rec["ability"][i]
                for o in options:
                    if all(o.get(k) == v for k, v in want.items()):
                        return o
                raise RuntimeError(f"{key} mm_ability 復元失敗: {want} / {options}")
            for o in options:
                if o.get("action") == "pass":
                    return o
            raise RuntimeError(f"{key} pass が無い: {options}")
        if decision == "incident_choice":
            for o in options:
                if o.get("target") == rec["incident"]:
                    return o
            return options[0]
        if decision == "turn_end_ability":
            if rec["turn_end"]:
                for o in options:
                    if o.get("action") != "pass":
                        return o
            for o in options:
                if o.get("action") == "pass":
                    return o
            return options[0]
        raise RuntimeError(f"未知の脚本家決定: {decision} / {options}")


# --------------------------------------------- 主人公席を棋譜に固定するプローブ
def build_forced(meta: dict) -> dict:
    """主人公席の記録済み選択（棋譜どおり）を decide 種別ごとのキューに。"""
    sets: dict = {}
    gw: dict = defaultdict(list)
    mode: dict = defaultdict(list)
    hist = meta["history"]
    for i, h in enumerate(hist):
        key = (h.get("loop"), h.get("day"))
        if h.get("event") == "cards_revealed":
            for p in h["placements"]:
                if p["owner"] != "mastermind":
                    sets[(key[0], key[1], p["owner"])] = {
                        "card": p["card"], "target": p["target"],
                        "target_kind": p["target_kind"]}
        elif h.get("event") == "goodwill_used":
            gw[key].append({"character": h["character"], "ability": h["ability"],
                            "target": h.get("target")})
            if h["character"] == "医者":
                # 直後の unrest イベントの符号で remove/add を復元
                for j in range(i + 1, min(i + 4, len(hist))):
                    n = hist[j]
                    if n.get("event") == "unrest" and n.get("target") == h.get("target"):
                        mode[key].append({"mode": "remove" if n["delta"] < 0 else "add"})
                        break
                    if n.get("event") == "goodwill_resolved":
                        # 不安0で除去が空振り＝unrest イベントが出ない＝remove 扱い
                        mode[key].append({"mode": "remove"})
                        break
    return {"set": sets, "gw": gw, "mode": mode}


def _trace_lines(key, opt):
    """`key(opt)` を1回だけ再評価し、`heuristic_protagonist.py` 内の return 行を全部返す。"""
    import sys as _sys
    seen: list = []

    def tr(frame, event, arg):
        if frame.f_code.co_filename.endswith("heuristic_protagonist.py"):
            if event == "return":
                seen.append((frame.f_code.co_name, frame.f_lineno, arg))
            return tr
        return None
    _sys.settrace(tr)
    try:
        val = key(opt)
    finally:
        _sys.settrace(None)
    return val, seen


def make_forced_class():
    import agents.heuristic_protagonist as _mod
    from engine.data import goodwill_abilities_of
    from agents.debug import _ESTIMATES, _snap, ProbedProtagonist
    from agents.heuristic_protagonist import HeuristicProtagonist

    _bmax = max

    class ForcedProbed(ProbedProtagonist):
        """棋譜どおりに指すが、**現行AIが選んだであろう手**と全候補スコアも記録する。

        ★`ProbedProtagonist.decide` は使わず同じ仕掛けを内製する（採点クロージャ `key`
        そのものを records に残すため＝`explain` が return 行を追える）。挙動は同一。
        """

        def __init__(self, meta, seed: int = 0, top: int = 12, trace_top: int = 0,
                     free_from_loop: int | None = None):
            super().__init__(seed, top=top)
            self.trace_top = trace_top
            #: このループ以降は棋譜で縛らず現行AIに自由に打たせる（反実仮想用）
            self.free_from_loop = free_from_loop
            self.f = build_forced(meta)
            self.gw_i: dict = defaultdict(int)
            self.mode_i: dict = defaultdict(int)
            self.dev: list[dict] = []

        def _probe_decide(self, view, decision, options):
            cap: dict = {}

            def spymax(*a, **kw):
                if a and isinstance(a[0], list) and "key" in kw and "scored" not in cap:
                    cap["key"] = kw["key"]
                    cap["scored"] = sorted(((kw["key"](o), o) for o in a[0]),
                                           key=lambda x: -x[0])
                return _bmax(*a, **kw)
            had = "max" in _mod.__dict__
            prev = _mod.__dict__.get("max")
            _mod.max = spymax
            try:
                chosen = HeuristicProtagonist.decide(self, view, decision, options)
            finally:
                if had:
                    _mod.max = prev
                else:
                    del _mod.max
            self.records.append({
                "loop": view.get("loop"), "day": view.get("day"),
                "seat": view.get("seat"), "decision": decision, "chosen": chosen,
                "scored": cap.get("scored", [])[: self._top],
                "key": cap.get("key"), "options": list(options),
                "estimates": {k: _snap(getattr(self, k)) for k in _ESTIMATES
                              if hasattr(self, k)},
                "plan_recs": dict(getattr(self, "_plan_recs", {}) or {}),
                "threats": [(t.kind, t.label, round(t.prob, 3), t.fatal, t.due_day)
                            for t in (getattr(self, "_b100_plan", None) or ([], None))[0]],
                "picks": [(b.card, b.target, b.target_kind, b.label)
                          for b in (getattr(self, "_b100_plan", (None, None))[1].picks
                                    if getattr(self, "_b100_plan", None) else [])],
            })
            self.records[-1]["extra"] = {
                "culprit_cands": {d: sorted(v) for d, v in
                                  (getattr(self, "_culprit_cands", {}) or {}).items()},
                "incident_danger": dict(getattr(self, "_incident_danger", {}) or {}),
                "lethal_culprits": sorted(getattr(self, "_lethal_culprits", ()) or ()),
                "loop_lost": bool(getattr(self, "_loop_lost", False)),
                "b221_avoid": sorted(getattr(self, "_b221_avoid_today", ()) or ()),
                "areas": {c["name"]: c["area"] for c in view["characters"]
                          if c.get("alive")},
                "abil": {},
                "b202_pairs": [list(map(str, x)) for x in
                               (self._b202_pairs(view) or ())],
                "b208_pairs": [list(map(str, x)) for x in
                               (self._b208_pairs(view) or ())],
                "lethal_days": sorted(getattr(self, "_lethal_days", ()) or ()),
                "cooled_days": sorted(getattr(self, "_cooled_days", ()) or ()),
                "ml_marg": ({n: round(v.get("ミスリーダー", 0.0), 3)
                             for n, v in self._belief.role_marginals().items()}
                            if self._belief is not None else {}),
                "caps": {
                    "b224_yield_today": sorted(getattr(self, "_b224_yield_today", ()) or ()),
                    "b224_cool_today": sorted(getattr(self, "_b224_cool_today", ()) or ()),
                    "b230_selfcancel": {
                        f"{c}->{t}": bool(self._b230_selfcancel(
                            {"card": c, "target": t, "target_kind": "character"}, view))
                        for c in ("移動←→", "移動↑↓") for t in ("医者", "巫女")},
                    "b237_redundant": {
                        f"{c}->{t}": bool(self._b237_redundant(
                            {"card": c, "target": t, "target_kind": "character"}, view))
                        for c in ("移動←→", "移動↑↓") for t in ("医者", "巫女")},
                    "planned_moves": dict(getattr(self, "_planned_moves", {}) or {}),
                },
                "b202_eval": {
                    f'{c}->{t}': self._b202_unrest_sep(
                        {"card": c, "target": t, "target_kind": "character"}, view)
                    for c in ("移動←→", "移動↑↓") for t in ("医者", "巫女")},
                "worlds": (self._belief.summary().get("worlds_remaining")
                           if self._belief is not None else None),
            }
            try:
                for _u in ("女子学生", "男子学生", "医者"):
                    for _ab in (goodwill_abilities_of(_u) or []):
                        for _t in [c["name"] for c in view["characters"] if c.get("alive")]:
                            if _t == _u:
                                continue
                            self.records[-1]["extra"]["abil"][
                                f"{_u}/{_ab['name']}->{_t}"] = round(
                                    self._ability_value(_u, _ab["name"], _t, view), 2)
            except Exception as e:      # noqa: BLE001
                self.records[-1]["extra"]["abil_err"] = repr(e)
            # ★採点の由来（return 行）は **decide 直後に** 取る（self の内部状態がその席の
            #   ままのうちに再評価する＝あとで呼ぶと _plan_recs 等が別席のものになり嘘になる）。
            if self.trace_top and cap.get("key"):
                self.records[-1]["trace"] = [
                    (sc, o, _trace_lines(cap["key"], o))
                    for sc, o in cap["scored"][: self.trace_top]]
            return chosen

        def decide(self, view, decision, options):
            own = self._probe_decide(view, decision, options)
            key = (view.get("loop"), view.get("day"))
            want = None
            if self.free_from_loop is not None and (key[0] or 0) >= self.free_from_loop:
                self.records[-1]["forced"] = None
                return own
            if decision == "set_card":
                want = self.f["set"].get((key[0], key[1], view.get("seat")))
            elif decision == "goodwill_ability":
                q = self.f["gw"][key]
                i = self.gw_i[key]
                if i < len(q):
                    for o in options:
                        if all(o.get(k) == v for k, v in q[i].items()):
                            want = o
                            self.gw_i[key] += 1
                            break
                if want is None:
                    for o in options:
                        if o.get("action") == "pass":
                            want = o
                            break
            elif decision == "doctor_unrest_mode":
                q = self.f["mode"][key]
                i = self.mode_i[key]
                if i < len(q):
                    want = q[i]
                    self.mode_i[key] += 1
            # ★B-263：`prov`（注記タグ）は **options の要素を in-place で書き換えて**付く
            #   （`agents/heuristic_protagonist._b100_tag_match`）＝AIの採択と棋譜の手が同じ
            #   時ほど `want not in options` が偽で失敗し「棋譜で縛れなかった（forced=None）」と
            #   誤記していた（実測＝既定教材で set_card 5席）。剥がして突き合わせ、返すのは
            #   **options 内の実要素**（`arena/interactive` の合法手検証は素の `in` なので）。
            opt = None if want is None else _match_option(want, options)
            if opt is None:
                self.records[-1]["forced"] = None
                return own
            self.records[-1]["forced"] = opt
            self.records[-1]["own"] = own
            if _strip_prov(opt) != _strip_prov(own):
                self.dev.append({"loop": key[0], "day": key[1],
                                 "seat": view.get("seat"), "decision": decision,
                                 "log": opt, "now": own,
                                 "scored": self.records[-1]["scored"]})
            return opt

    return ForcedProbed


def rerun_forced(meta: dict, seed: int = 0, trace_top: int = 0,
                 free_from_loop: int | None = None):
    """主人公席を棋譜に固定して再実行（＝盤面は棋譜と完全一致）。

    `free_from_loop=N` を渡すと **ループ N 以降だけ現行AIに自由に打たせる**（反実仮想）。
    脚本家席は棋譜の選択のまま＝「人間が同じ手を繰り返したら」という仮定つきの観測。
    """
    Forced = make_forced_class()
    return rerun(meta, seed, hp_factory=lambda: Forced(
        meta, seed, trace_top=trace_top, free_from_loop=free_from_loop))


# ------------------------------------------------------------- 局の再実行
def rerun(meta: dict, seed: int = 0, hp_factory=None):
    """棋譜の脚本家選択を復元して1局を再実行し (state, log, hp) を返す。"""
    from agents.debug import ProbedProtagonist
    from arena.interactive import PendingHuman, play_interactive
    from sim.state import script_from_dict

    script = script_from_dict(meta["script"])
    script = replace(script, loops=int(meta.get("loops_played") or script.loops))
    factory = hp_factory or (lambda: ProbedProtagonist(seed))
    oracle = MMOracle(meta)   # ★消費位置は試行をまたいで持ち越す（新しい1決定だけ問われる）
    choices: list[dict] = []
    while True:
        # ★再実行のたびに主人公AIを作り直す（app 側と同じ＝streamlit は毎回ゼロから
        #   `_ai_protagonists` を作る）。使い回すと試行をまたいで内部状態が汚染される。
        hp = factory()
        ai = {"p1": hp, "p2": hp, "p3": hp}
        try:
            state, log = play_interactive(script, ai, {"mastermind"}, choices,
                                          final_battle=False)
            return state, log, hp
        except PendingHuman as p:
            try:
                choices.append(oracle.answer(p.state, p.decision, p.options))
            except Exception as e:          # noqa: BLE001
                # ★B-263：復元失敗（上流の実分岐で options が変わった等）でも、そこまでの
                #   history を verify が突き合わせられるように部分状態を例外へ添える。
                e.partial_state = p.state   # type: ignore[attr-defined]
                raise


def _strip_prov(o):
    """`prov`（表示層の注記タグ・`sim/flow.log_safe_chosen` 参照）を**再帰的に**剥がした写し。

    ★B-263：`arena/b251_audit._strip` はトップレベルのみ（chosen が平坦なので足りる）だが、
    history の `cards_revealed` は placements の**入れ子の中**に prov が付く＝再帰が要る
    （`arena/u12_audit._strip_prov` と同趣旨）。
    """
    if isinstance(o, dict):
        return {k: _strip_prov(v) for k, v in o.items() if k != "prov"}
    if isinstance(o, list):
        return [_strip_prov(v) for v in o]
    return o


def _match_option(want: dict, options: list):
    """options から `want` と一致する**実要素**を返す（無ければ None）＝B-263。

    突き合わせは **prov（注記タグ）を剥がして**行う（`prov` は options の要素を
    in-place で書き換えて付くので、素の `in` はAIの採択と同じ手ほど見失う）。
    返すのが実要素なのは `arena/interactive` の合法手検証（素の `in`）を通すため。
    """
    sw = _strip_prov(want)
    for o in options:
        if _strip_prov(o) == sw:
            return o
    return None


def compare_histories(got: list, want: list) -> dict:
    """★B-263：**全行を検査し切る**突き合わせ（途中の不一致で止まらない・zip で切らない）。

    分母＝`max(len(got), len(want))`＝**片側にしか無い行も不一致として数える**。
    返り値:
      checked      検査した行数（＝分母。必ず max(len(got), len(want))）
      matched      prov を剥がして一致した行数
      mismatched   不一致の idx **全列挙**（片側欠けも含む）
      missing      行数差 |len(got)-len(want)|（短い側の欠け＝未生成/余剰）
      prov_only    prov を剥がすと一致する idx（＝偽不一致だった行。一致扱い・記録のみ）
    """
    n = max(len(got), len(want))
    sg = [_strip_prov(r) for r in got]
    sw = [_strip_prov(r) for r in want]
    mismatched: list[int] = []
    prov_only: list[int] = []
    for i in range(n):
        if i >= len(sg) or i >= len(sw) or sg[i] != sw[i]:
            mismatched.append(i)
        elif got[i] != want[i]:
            prov_only.append(i)
    return {"checked": n, "matched": n - len(mismatched), "mismatched": mismatched,
            "missing": abs(len(got) - len(want)), "prov_only": prov_only}


def verify(meta: dict, seed: int = 0, show: int = 3) -> bool:
    want = meta["history"]
    aborted = None
    state = None
    try:
        state, log, hp = rerun(meta, seed)
        got = state.history
    except Exception as e:                  # noqa: BLE001
        # ★B-263：再実行が完走しなくても「done」とは言わない＝中断を明示し、
        #   部分 history があればそこまでを（分母は棋譜全行のまま）突き合わせる。
        aborted = e
        st = getattr(e, "partial_state", None)
        got = list(st.history) if st is not None else []
    r = compare_histories(got, want)
    ok = (not r["mismatched"]) and aborted is None
    print(f"再実行 history 一致: {ok}  (再実行 {len(got)} 件 / 棋譜 {len(want)} 件 / "
          f"検査 {r['checked']}/{r['checked']} 行・一致 {r['matched']}・"
          f"不一致 {len(r['mismatched'])}・片側のみ {r['missing']} 行)")
    if aborted is not None:
        print(f"  ★再実行が完走しない（そこまでの {len(got)} 行だけ突き合わせた）: {aborted!r}")
    if r["prov_only"]:
        print(f"  prov（注記タグ）だけの差＝一致扱い: idx={r['prov_only']}")
    if r["mismatched"]:
        head = ", ".join(map(str, r["mismatched"][:30]))
        more = f" …(全{len(r['mismatched'])}件)" if len(r["mismatched"]) > 30 else ""
        print(f"  不一致 idx: [{head}]{more}")
        for i in r["mismatched"][:show]:
            a = _strip_prov(got[i]) if i < len(got) else "(再実行に無い)"
            b = _strip_prov(want[i]) if i < len(want) else "(棋譜に無い)"
            print(f"  相違 idx={i}\n   再実行: {json.dumps(a, ensure_ascii=False)}"
                  f"\n   棋譜  : {json.dumps(b, ensure_ascii=False)}")
    if state is not None:
        fs_ok = _strip_prov(state.to_dict()) == _strip_prov(meta["final_state"])
    else:
        fs_ok = False
    print(f"final_state 一致: {fs_ok}")
    return ok and fs_ok


# ------------------------------------------------------------------- 表示
def _fmt(o: dict) -> str:
    if "card" in o:
        return f'{o["card"]}→{o.get("target")}'
    if "ability" in o:
        return f'{o.get("character")}:{o["ability"]}→{o.get("target")}'
    if "action" in o:
        return f'{o["action"]}' + (f'→{o["target"]}' if o.get("target") else "")
    return str(o)


def cmd_scores(meta, args):
    state, log, hp = rerun(meta, args.seed)
    for r in hp.records:
        if args.loop and r.get("loop") != args.loop:
            continue
        if args.day and r.get("day") != args.day:
            continue
        if args.decision and r.get("decision") != args.decision:
            continue
        print(f'--- L{r["loop"]}D{r["day"]} {r["seat"]} {r["decision"]} '
              f'選択={_fmt(r["chosen"])}')
        for s, o in r["scored"][: args.top]:
            print(f'   {"★" if o == r["chosen"] else "  "}{s:8.2f} {_fmt(o)}')
        est = {k: v for k, v in (r.get("estimates") or {}).items()
               if v not in (None, [], False, {})}
        if est and args.est:
            print("   推定: " + "  ".join(f"{k.lstrip('_')}={v}" for k, v in est.items()))


def cmd_explain(meta, args):
    """疑問手の席で「どの return がその点数を返したか」を行番号で特定する。

    ★仕組み＝記録しておいた採点クロージャ `key` を**もう一度**呼び、`sys.settrace` で
    `agents/heuristic_protagonist.py` 内の return 行を記録するだけ（純関数の再評価）。
    """
    import sys as _sys
    state, log, hp = rerun_forced(meta, args.seed)

    def where(key, opt):
        seen: list = []

        def tr(frame, event, arg):
            if frame.f_code.co_filename.endswith("heuristic_protagonist.py"):
                if event == "return":
                    seen.append((frame.f_code.co_name, frame.f_lineno, arg))
                return tr
            return None
        _sys.settrace(tr)
        try:
            key(opt)
        finally:
            _sys.settrace(None)
        return seen

    for r in hp.records:
        if args.loop and r["loop"] != args.loop:
            continue
        if args.day and r["day"] != args.day:
            continue
        if args.seat and r["seat"] != args.seat:
            continue
        if args.decision and r["decision"] != args.decision:
            continue
        if not r.get("key"):
            continue
        pick = r.get("forced") or r["chosen"]
        print(f'===== L{r["loop"]}D{r["day"]} {r["seat"]} {r["decision"]} 採択={_fmt(pick)}')
        for s, o in r["scored"][: args.top]:
            w = where(r["key"], o)
            base = [x for x in w if x[0] == args.fn]
            trail = " / ".join(f"{n}:{ln}={v}" for n, ln, v in base[-args.depth:])
            if not base:
                trail = " < ".join(f"{n}:{ln}" for n, ln, _v in w[-args.depth:][::-1])
            print(f'   {"★" if o == pick else "  "}{s:8.2f} {_fmt(o):<22} ← {trail}')


def cmd_repeat(meta, args):
    """L間で主人公の手がどれだけ一致するか（反復非適応の数え上げ）。"""
    per: dict = defaultdict(dict)
    for h in meta["history"]:
        if h.get("event") != "cards_revealed":
            continue
        for i, p in enumerate(h["placements"]):
            if p["owner"] != "mastermind":
                per[(h["loop"], h["day"])][p["owner"]] = (p["card"], p["target"])
    mm: dict = defaultdict(list)
    for h in meta["history"]:
        if h.get("event") != "cards_revealed":
            continue
        mm[(h["loop"], h["day"])] = sorted(
            (p["card"], p["target"]) for p in h["placements"]
            if p["owner"] == "mastermind")
    loops = sorted({k[0] for k in per})
    days = sorted({k[1] for k in per})
    for a in loops:
        for b in loops:
            if b <= a:
                continue
            same = tot = same_mm = tot_mm = 0
            det = []
            for d in days:
                if (a, d) not in per or (b, d) not in per:
                    continue
                sa = sorted(per[(a, d)].values())
                sb = sorted(per[(b, d)].values())
                inter = list(sa)
                hit = 0
                for x in sb:
                    if x in inter:
                        inter.remove(x)
                        hit += 1
                same += hit
                tot += max(len(sa), len(sb))
                det.append(f"D{d}:{hit}/{max(len(sa), len(sb))}")
                if mm[(a, d)] == mm[(b, d)]:
                    same_mm += 1
                tot_mm += 1
            print(f"L{a} vs L{b}: 主人公の同一札 {same}/{tot}  ({' '.join(det)})"
                  f"   脚本家の同一日 {same_mm}/{tot_mm}")


def cmd_separation(meta, args):
    """★主題の数え上げ＝「ミスリーダー候補」と「事件の犯人候補」の同室席を数える。

    公開情報のみで作る：
      - 犯人候補＝belief.culprit_candidates()（その日の事件の犯人になりうる者）
      - ミスリーダー候補＝belief.role_marginals() で ミスリーダー確率>0 の者
    主人公の行動解決フェイズ後（＝脚本家能力フェイズの直前）の同室を数える。
    """
    from agents.belief import Belief
    script = meta["script"]
    cast = script["cast"]
    inc_public = [{"day": i["day"], "name": i["name"]} for i in script["incidents"]]
    hist = meta["history"]
    # 日ごとに、その日の cards_revealed 直前までの履歴で belief を作る
    state, log, hp = rerun(meta, args.seed)
    # 各 protagonist_set 決定時の view から「行動解決前」の配置を得るのは難しいので、
    # 行動解決後の配置は次の日の view / turn_end_pairs から近似せず、log の
    # goodwill_ability / mastermind_ability の view（＝行動解決後）を使う。
    seen = set()
    for r in log:
        if r["decision"] not in ("mastermind_ability", "goodwill_ability"):
            continue
        key = (r["loop"], r["day"])
        if key in seen:
            continue
        seen.add(key)
        view = r["view"]
        cut = []
        for h in hist:
            if (h.get("loop"), h.get("day")) == key and h.get("event") == "cards_revealed":
                break
            cut.append(h)
        b = Belief(list(cast), list(inc_public), script["set_name"])
        b.observe(cut)
        marg = b.role_marginals()
        misl = {c for c in cast if marg.get(c, {}).get("ミスリーダー", 0) > 0.001}
        culp = set()
        for d, s in b.culprit_candidates().items():
            culp |= set(s)
        area = {c["name"]: c["area"] for c in view["characters"] if c.get("alive")}
        pairs = [(m, c) for m in misl for c in culp
                 if m != c and area.get(m) and area.get(m) == area.get(c)]
        print(f"L{key[0]}D{key[1]}  同室(ミスリーダー候補×犯人候補)={len(pairs)}  "
              f"{sorted(set(pairs))}")
        print(f"      ミスリーダー候補={sorted(misl)}  犯人候補={sorted(culp)}")



def cmd_supply(meta, args):
    """★族の物差し案＝「供給ペア同室席」を公開情報だけで数え上げる。

    材料（すべて公開イベント）：
      - `unrest`×`phase=mastermind_ability`×`delta>0` の (target=受け手, present=同室者)
        ＝**能力供給の実績**（`_b230_pairs` と同じ一次情報）。
      - present の**交差**を取ると供給役の候補が絞れる（1人に落ちることがある）。
    数える席：その日の行動解決**後**に (受け手, 供給役候補) が同室で、受け手が
    belief の当日/未来の犯人候補であるのに、主人公3席が誰もその同室を切らなかったターン。
    """
    from agents.belief import Belief
    from collections import defaultdict as _dd
    sc = meta["script"]
    inc_public = [{"day": i["day"], "name": i["name"]} for i in sc["incidents"]]
    hist = meta["history"]
    state, log, hp = rerun_forced(meta, args.seed)

    # 日ごとの「行動解決後」view（脚本家能力フェイズの決定の view）
    post: dict = {}
    for r in log:
        k = (r["loop"], r["day"])
        if r["decision"] == "mastermind_ability" and k not in post:
            post[k] = r["view"]

    n_pair = n_broken = 0
    for (lp, dy), view in sorted(post.items()):
        # この時点までの公開履歴（当ループの観測は使わない＝_b230_pairs と同じ規律）
        obs: dict = _dd(list)
        for e in hist:
            if (e.get("event") == "unrest" and e.get("phase") == "mastermind_ability"
                    and int(e.get("delta", 0) or 0) > 0 and e.get("loop") < lp):
                obs[e["target"]].append(set(e.get("present") or ()) - {e["target"]})
        if not obs:
            print(f"L{lp}D{dy}  供給実績なし")
            continue
        b = Belief(list(sc["cast"]), list(inc_public), sc["set_name"])
        cut = []
        for e in hist:
            if (e.get("loop"), e.get("day")) == (lp, dy)                     and e.get("event") == "cards_revealed":
                break
            cut.append(e)
        b.observe(cut)
        culp = {c for v in b.culprit_candidates().values() for c in v}
        area = {c["name"]: c["area"] for c in view["characters"] if c.get("alive")}
        for rcv, sets in sorted(obs.items()):
            inter = set.intersection(*sets) if sets else set()
            union = set().union(*sets) if sets else set()
            here = {s for s in (inter or union) if area.get(s) == area.get(rcv)}
            tag = "交差" if inter else "和"
            if here:
                n_pair += 1
                print(f"L{lp}D{dy}  供給ペア同室: 受け手={rcv}(犯人候補={rcv in culp}) "
                      f"供給役候補({tag})={sorted(inter or union)} 同室={sorted(here)} "
                      f"@{area.get(rcv)}")
            else:
                n_broken += 1
                print(f"L{lp}D{dy}  分離済み: 受け手={rcv}@{area.get(rcv)} "
                      f"供給役候補({tag})={sorted(inter or union)}")
    print(f"--- 同室のまま残した席={n_pair} / 分離できていた席={n_broken}")



def cmd_whatif(meta, args):
    """★的A の当たり確認（**プローブ側の一時パッチのみ**・production 非接触）。

    仮説＝B-202 の供給役同定（`_b202_pairs` の `supp`＝belief のミスリーダー周辺確率）を、
    **B-208 が既に持っている公開 `present` の交差**で一意化できるときはそれで置き換える。
    盤面は棋譜に固定したまま、各席で現行AIが選ぶ手がどう変わるかだけを観測する。
    """
    from agents.heuristic_protagonist import HeuristicProtagonist as HP
    orig = HP._b202_pairs

    def patched(self, view):
        out = orig(self, view)
        if not out:
            return out
        cur = view.get("loop")
        seen: dict = {}
        for e in view.get("history", []) or []:
            if (e.get("event") == "unrest"
                    and e.get("phase") == "mastermind_ability"
                    and int(e.get("delta", 0) or 0) > 0
                    and e.get("loop") != cur):
                pres = frozenset(e.get("present") or ())
                if pres:
                    seen.setdefault(e.get("target"), []).append(pres)
        fixed = []
        for cn, area, supp, ml_free, due in out:
            pres = seen.get(cn)
            if pres and len(supp) > 1:
                common = frozenset.intersection(*pres) - {cn}
                here = [n for n in sorted(common)
                        if n in supp and (self._alive(view, n) or {}).get("area") == area]
                if len(here) == 1:
                    supp = tuple(here)
            fixed.append((cn, area, supp, ml_free, due))
        return tuple(fixed)

    base = {}
    state, log, hp = rerun_forced(meta, args.seed)
    for r in hp.records:
        if r["decision"] == "set_card":
            base[(r["loop"], r["day"], r["seat"])] = r["chosen"]
    HP._b202_pairs = patched
    try:
        state2, log2, hp2 = rerun_forced(meta, args.seed)
    finally:
        HP._b202_pairs = orig
    n = 0
    for r in hp2.records:
        if r["decision"] != "set_card":
            continue
        k = (r["loop"], r["day"], r["seat"])
        if base.get(k) != r["chosen"]:
            n += 1
            print(f"L{k[0]}D{k[1]} {k[2]}: 現行={_fmt(base.get(k))} → パッチ後={_fmt(r['chosen'])}")
    print(f"--- 変化した席={n}（棋譜の盤面は固定＝同一局面での比較）")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "scores", "repeat", "separation", "explain", "supply", "whatif"])
    ap.add_argument("--log", default=LOG)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--day", type=int, default=0)
    ap.add_argument("--decision", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--est", action="store_true")
    ap.add_argument("--seat", default="")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--fn", default="_base_score")
    args = ap.parse_args(argv)
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("!! PYTHONHASHSEED=0 で実行すること", file=sys.stderr)
    meta, decisions = load_log(args.log)
    if args.cmd == "verify":
        # ★B-263：一致しなければ RC=1（「done」と黙って返さない）。
        return 0 if verify(meta, args.seed) else 1
    elif args.cmd == "scores":
        cmd_scores(meta, args)
    elif args.cmd == "repeat":
        cmd_repeat(meta, args)
    elif args.cmd == "separation":
        cmd_separation(meta, args)
    elif args.cmd == "explain":
        cmd_explain(meta, args)
    elif args.cmd == "supply":
        cmd_supply(meta, args)
    elif args.cmd == "whatif":
        cmd_whatif(meta, args)


if __name__ == "__main__":
    sys.exit(main())
