# -*- coding: utf-8 -*-
"""B-207 Phase 0：land 済み述語（B-201／B-202／B-204／B-206）が
**land 後の実戦棋譜で一度も発火しなかった理由**を、条件ごと・評価値つきで確定する
読み取り専用プローブ。

★`agents/` の判断経路・既定値には一切触れない（読むだけ）。実装は行わない。

教材＝`docs/feedback_logs/鈴蘭_BTX3d_seed0_land後検証_2026-08-12.jsonl`
（build ea9837b＝B-201/202/204/206 land 全部入り）。

サブコマンド:
    python -m arena.b207_probe pred   [--log PATH]
        # 主人公の全決定を素で再生（bit 一致確認）し、各 set_card 席で
        #   - B-201 `mainlover_anyaku_pin_live` の 4 条件を**個別に**評価（値つき）
        #   - B-202 `_b202_pairs` の前提（致死日・犯人一意・能力実証・算術・両輪）
        #   - B-204 `board_zero_allowance` の 4 条件を**板ごとに**評価（値つき）
        #   - B-206 `_b206_teleport_sk` の G1（今日の事件種）
    python -m arena.b207_probe attrib [--log PATH]
        # `暗躍禁止` 候補の得点が **PRIORITY のどの項から来たか**を実測で帰属させる
        #   （PRIORITY dict の __getitem__ を観測してオプション別に記録）
    python -m arena.b207_probe cf     [--log PATH]
        # 反実仮想＝述語の条件を1つずつ緩めた時に、その席の選択が変わるか

## ★era ピン（B-260・2026-08-19）

教材は **build ea9837b（2026-08-12）＝B-207 Phase 1 land 前**の収録なので、
**現行のリポジトリ既定のまま再生すると 5席が食い違う（38/43）**。
∴ 本 CLI の**既定は「収録当時の既定へ倒して再生する」**＝`pred` は素で **43/43**（実測）。
現行既定で再生したいときは `--no-era-pin`。
★**どちらで走ったかは毎回 1行目に印字される**。実装は `arena.b251_audit.era_pin` を
  **import して使う**（二重実装をしない＝§72-34）。

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import builtins
import json
from pathlib import Path

_builtin_max = builtins.max

import agents.heuristic_protagonist as _hp_mod
from agents.debug import ProbedProtagonist
from arena.b251_audit import era_banner, era_pin   # ★B-260（二重実装をしない＝§72-34）

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "docs/feedback_logs/鈴蘭_BTX3d_seed0_land後検証_2026-08-12.jsonl"
_MOVE = ("移動↑↓", "移動←→", "移動↖↘")

# ---------------------------------------------------------------------------
# ★B-260：era ピン（教材が録られた**当時の既定**へ倒して再生する明示スイッチ）
# ---------------------------------------------------------------------------
#: ★なぜ倒すのか
#:   本プローブの教材は **build ea9837b（2026-08-12）＝B-207 Phase 1 の land 前**の収録。
#:   その直後（2026-08-12〜13）に B-207 Phase 1 が land し、
#:   `B201_LOVER_REACH`（B-201 の条件(4) を「残り日程で暗躍2に届きうる」へ緩和）と
#:   採点側の2つ（`B207_KILLER_FLOOR_RESPONSIVE`／`B207_DANGER_BONUS_TIEBREAK`。
#:   後者は 2026-08-13 のユーザー裁定で既定 ON）が**既定 ON** になった。
#:   ＝現行既定のまま再生すると、本プローブが「その席で実際に max() が見た値」と
#:   称して読む点数が**別の席の値**になる。`tests/test_b201_mainlover_pin.py` と
#:   `tests/test_b207p1_score_inversion.py` がテスト側で倒しているのと同じ束。
#:
#: ★実測（`pred` の bit 一致・2026-08-19）
#:   素（現行既定）38/43 ／ reach のみ 40/43 ／ killer のみ 40/43 ／ tie のみ 39/43 ／
#:   killer+tie 41/43 ／ **この3つ全部＝43/43**（＝完全再現）。
#:
#: ★`B252_CULT_FLOOR`（B-252(a)・2026-08-19 既定 ON）は**倒さない**＝最小限の原則。
#:   この棋譜には「しきい値の直上（0.1 < p ≦ 0.15）」の帯に居る席が無く、
#:   倒しても倒さなくても 38/43 のまま（実測）＝倒す理由が無い。印字だけする。
#:
#: ★追加（B-267・2026-08-19）＝`B256_UNREACHABLE_SKIP` が**既定 ON** になった
#:   （ユーザー裁定＝`B262_BELIEF_BOUND` と対で ON）。教材は**両方 OFF 時代の収録**。
#:   ★実測＝倒さないと `pred` の bit 一致が **43/43 → 42/43**（不安3へ算術的に届かない
#:     対象がウイルス試験の候補プールから落ちて、その席の手が変わる）。
#:     `skip` を倒すと **43/43** に戻る（他の3ピンはそのまま）。
#: ★`B262_BELIEF_BOUND` は**倒さない**＝**最小限の原則**（B-260 の教訓＝`B252_CULT_FLOOR`
#:   を機械的に流用したら b202/b204 は逆に悪化した）。`bel` は `skip` の内側でしか
#:   読まれないので `skip` を倒せば発火しない。実測でも `bel` のみでは 42/43 のまま・
#:   `skip` のみで 43/43・両方倒しても 43/43（＝`bel` を足しても何も変わらない）。印字だけする。
ERA_PINS: tuple = (
    ("agents.defense_plan", None, "B201_LOVER_REACH", False),
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B207_KILLER_FLOOR_RESPONSIVE", False),
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B207_DANGER_BONUS_TIEBREAK", False),
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B256_UNREACHABLE_SKIP", False),
)

#: era ピンの状態と一緒に必ず印字する定数（倒さないものも根拠として載せる）。
AUDITED_CONSTS: tuple = (
    ("agents.defense_plan", None, "B201_LOVER_REACH"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B207_KILLER_FLOOR_RESPONSIVE"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist",
     "B207_DANGER_BONUS_TIEBREAK"),
    # ★倒さないが根拠として印字する（この棋譜では効かないことを実測済み）
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B252_CULT_FLOOR"),
    # ★B-267：倒す方（skip）と、倒さないが効き方の前提になる方（bel）を両方印字する。
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B256_UNREACHABLE_SKIP"),
    ("agents.heuristic_protagonist", "HeuristicProtagonist", "B262_BELIEF_BOUND"),
)


def _load(path) -> tuple[dict, list[dict]]:
    lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    return lines[0], [d for d in lines if d.get("type") == "decision"]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "prov"}


def _fmt(o: dict) -> str:
    return f'{o.get("card")}→{o.get("target")}'


def _mm_chars(view: dict) -> frozenset:
    return frozenset(p["target"] for p in view.get("placements", [])
                     if p.get("owner") == "mastermind"
                     and p.get("target_kind") == "character")


def _mm_boards(view: dict) -> frozenset:
    return frozenset(p["target"] for p in view.get("placements", [])
                     if p.get("owner") == "mastermind"
                     and p.get("target_kind") == "board")


# ---------------------------------------------------------------------------
# 述語ごとの「条件別」評価（本体の述語を**書き写さず**、同じ部品を呼んで値を見る）
# ---------------------------------------------------------------------------
def _b201_conditions(hp, view: dict, name: str) -> list[str]:
    """`defense_plan.mainlover_anyaku_pin_live` の 4 条件を個別に評価して返す。

    ★条件の**判定そのもの**は本体の部品（`_char`／`remote_murder_pin_live`／
      `_suspects`／`REMOTE_MURDER_ANYAKU`）を呼ぶ＝二重実装しない。
    """
    import agents.defense_plan as dp
    mm_chars = _mm_chars(view)
    try:
        roles = hp._belief.role_marginals()
    except Exception:
        roles = {}
    c = dp._char(view, name)
    d0 = view.get("day", 1)
    rows = []
    # (1) 今日 mm 札あり
    rows.append(f"(1)mm札あり={name in mm_chars}")
    # (2) 暗躍ちょうど0
    rows.append(f"(2)暗躍=={c.get('anyaku') if c else None}"
                f"→{'OK' if c and c.get('anyaku', 0) == 0 else 'NG'}")
    # (3) メインラバーズ実在度
    p_ml = float((roles or {}).get(name, {}).get("メインラバーズ", 0.0))
    rows.append(f"(3)P(ML)={p_ml:.3f} vs θ={dp.B201_MAINLOVER_P}"
                f"→{'OK' if p_ml >= dp.B201_MAINLOVER_P else 'NG'}")
    # (4) 残り日程の遠隔殺人 × ラバーズ疑いが的
    has_rm = any(i.get("name") == "遠隔殺人" and i.get("day") is not None
                 and i.get("day") >= d0 for i in view.get("incidents", []) or [])
    lovers = []
    ok4 = False
    for lv in dp._suspects(roles, "ラバーズ"):
        if lv == name or not dp._alive(view, lv):
            continue
        lc = dp._char(view, lv)
        a = lc.get("anyaku", 0) if lc else None
        legal = bool(lc and a >= dp.REMOTE_MURDER_ANYAKU)
        pinned = bool(dp.remote_murder_pin_live(view, lv, mm_chars, None))
        ok4 = ok4 or legal or pinned
        lovers.append(f"{lv}(P={float(roles.get(lv, {}).get('ラバーズ', 0.0)):.2f}"
                      f"/暗躍{a}/既に合法={legal}/今日的={pinned})")
    rows.append(f"(4)遠隔殺人残={has_rm} ラバーズ疑い=[{', '.join(lovers) or 'なし'}]"
                f"→{'OK' if has_rm and ok4 else 'NG'}")
    live = dp.mainlover_anyaku_pin_live(view, name, mm_chars, roles)
    rows.append(f"★述語={live}")
    return rows


def _b204_conditions(hp, view: dict) -> list[str]:
    """`defense_plan.board_zero_allowance` の 4 条件を板ごとに評価。"""
    import agents.defense_plan as dp
    from engine.board import AREAS
    probs = getattr(hp, "_board_defeat_probs", None)
    try:
        p_rumor = dp.rumor_p(hp._belief)
        rleft = dp.rumor_left_for(hp._belief, view)
    except Exception as e:
        return [f"  [B-204] belief 参照に失敗: {e!r}"]
    rows = [f"  [B-204] P(不穏な噂)={p_rumor:.3f} vs θ={dp.B204_RUMOR_P}"
            f"  噂残弾={rleft}"]
    for a in AREAS:
        cur = int((view.get("board_anyaku") or {}).get(a, 0) or 0)
        dprob = float((probs or {}).get(a, 0.0) or 0.0)
        allow = dp.board_card_allowance(view, a, supply_rumor=True, rumor_left=rleft)
        proven = dp.board_card_supply_proven(view, a)
        live = dp.board_zero_allowance(view, a, hp._belief, probs)
        rows.append(f"    {a}: 現在値={cur} (1)敗北P={dprob:.3f} "
                    f"(3)許容量={allow} (4)カード供給実績={proven} ★述語={live}")
    rows.append(f"    _b66_unproven_boards={sorted(getattr(hp, '_b66_unproven_boards', ()))} "
                f"_b204_zero_allow_now={sorted(getattr(hp, '_b204_zero_allow_now', ()))}")
    return rows


def _b202_conditions(hp, view: dict) -> list[str]:
    """`_b202_pairs` が空になる原因を、条件①〜⑥の順に値つきで出す。"""
    from agents.heuristic_protagonist import unrest_threshold_of
    today = int(view.get("day", 1) or 1)
    lethal = sorted(d for d in getattr(hp, "_lethal_days", ()) if d >= today)
    rows = [f"  [B-202] _lethal_days(残)={lethal} "
            f"_cooled_days={sorted(getattr(hp, '_cooled_days', ()))}"]
    if not lethal:
        rows.append("    → 残り致死日なし＝入口で不成立")
        return rows
    pump: dict = {}
    for e in view.get("history", []) or []:
        if (e.get("event") == "unrest" and e.get("phase") == "mastermind_ability"
                and int(e.get("delta", 0) or 0) > 0):
            k = (e.get("loop"), e.get("target"))
            pump[k] = pump.get(k, 0) + 1
    rows.append(f"    ③能力フェイズ不安+1 の観測={json.dumps({f'L{k[0]}:{k[1]}': v for k, v in pump.items()}, ensure_ascii=False)}")
    for due in lethal:
        cands = [n for n in getattr(hp, "_culprit_cands", {}).get(due, ())
                 if hp._alive(view, n) is not None]
        line = f"    D{due}: ①犯人候補={sorted(cands)}"
        if len(cands) != 1:
            rows.append(line + " → 一意でない＝不成立")
            continue
        cn = cands[0]
        seen = max((v for (lp, n), v in pump.items() if n == cn), default=0)
        cc = hp._alive(view, cn)
        th = unrest_threshold_of(cn)
        line += (f" 犯人={cn} ③実証回数={seen}(要{hp._B202_PUMP_MIN}) "
                 f"不安={cc['unrest'] if cc else None}/臨界={th}")
        if seen < hp._B202_PUMP_MIN:
            rows.append(line + " → ③不成立")
            continue
        if cc is None or th is None:
            rows.append(line + " → 不明")
            continue
        if cc["unrest"] >= th:
            rows.append(line + " → ②既に臨界＝冷却の管轄")
            continue
        if cc["unrest"] + (due - today + 1) < th:
            rows.append(line + " → ④冷却で足りる")
            continue
        rows.append(line + " → ①〜④成立")
    rows.append(f"    _b202_pairs={hp._b202_pairs(view)}")
    return rows


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------
def cmd_pred(args) -> int:
    meta, decisions = _load(args.log)
    print(f"===== B-207 Phase 0 / 述語の条件別評価  {Path(args.log).name} "
          f"(build {meta.get('tool_build')}) =====")
    hp = ProbedProtagonist(0, top=400)
    n_same = n_all = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        view, options = d["view"], d["options"]
        chosen = hp.decide(view, d["decision"], options)
        same = _clean(chosen) == _clean(d["chosen"])
        n_all += 1
        n_same += bool(same)
        if d["decision"] != "set_card":
            continue
        rec = hp.records[-1]
        mark = "" if same else f"  ≠棋譜（棋譜={_fmt(_clean(d['chosen']))}）"
        print(f"\n--- L{d['loop']}D{d['day']} {view.get('seat')}: "
              f"選択={_fmt(_clean(chosen))}{mark}")
        print(f"      mm札 キャラ={sorted(_mm_chars(view))} 板={sorted(_mm_boards(view))}"
              f"  板暗躍={json.dumps(view.get('board_anyaku'), ensure_ascii=False)}")
        for s, o in rec["scored"][:3]:
            print(f"      TOP {s:8.2f} {_fmt(o)}")
        for s, o in rec["scored"]:
            if o.get("card") == "暗躍禁止" and o.get("target") in args.watch:
                print(f"      ・  {s:8.2f} {_fmt(o)}")
        # --- B-201
        for name in args.ml:
            print(f"      [B-201/{name}] " + "  ".join(_b201_conditions(hp, view, name)))
        # --- B-202
        for r in _b202_conditions(hp, view):
            print(r)
        # --- B-204
        for r in _b204_conditions(hp, view):
            print(r)
        # --- B-206
        print(f"      [B-206] _b206_teleport_sk={hp._b206_teleport_sk(view)} "
              f"（今日の事件={sorted({i.get('name') for i in (view.get('incidents') or []) if i.get('day') == view.get('day')})}）")
    print(f"\n[probe] 主人公決定の bit 一致 = {n_same}/{n_all}")
    return 0


class _AttribProtagonist(ProbedProtagonist):
    """`PRIORITY[...]` の参照を**候補ごと**に記録する主人公（挙動は本体と同一）。

    仕組み＝`max(options, key=score)` を横取りする既存の spymax（`agents/debug`）に加え、
    `heuristic_protagonist.PRIORITY` を記録付き dict へ差し替え、`key(o)` の前後で
    参照キーを回収する。**採点そのものは本体**＝書き写していない。
    """

    def decide(self, view, decision, options):
        self.attrib: dict = {}
        rec_keys: list = []

        class _RecDict(dict):
            def __getitem__(self, k):
                rec_keys.append(k)
                return dict.__getitem__(self, k)

        prio_prev = _hp_mod.PRIORITY
        _hp_mod.PRIORITY = _RecDict(prio_prev)
        prev_max = _hp_mod.__dict__.get("max")
        had = "max" in _hp_mod.__dict__
        outer = self

        def spymax(*a, **kw):
            if a and isinstance(a[0], list) and "key" in kw and not outer.attrib:
                key = kw["key"]
                for o in a[0]:
                    rec_keys.clear()
                    s = key(o)
                    outer.attrib[(o.get("card"), o.get("target"),
                                  o.get("target_kind"))] = (s, tuple(rec_keys))
            return _builtin_max(*a, **kw)

        _hp_mod.max = spymax
        try:
            return super(ProbedProtagonist, self).decide(view, decision, options)
        finally:
            _hp_mod.PRIORITY = prio_prev
            if had:
                _hp_mod.max = prev_max
            else:
                _hp_mod.__dict__.pop("max", None)


def cmd_attrib(args) -> int:
    meta, decisions = _load(args.log)
    print(f"===== B-207 Phase 0 / `暗躍禁止` の得点帰属  {Path(args.log).name} "
          f"(build {meta.get('tool_build')}) =====")
    hp = _AttribProtagonist(0, top=400)
    n_same = n_all = 0
    for d in decisions:
        if d.get("actor") == "mastermind":
            continue
        chosen = hp.decide(d["view"], d["decision"], d["options"])
        n_all += 1
        n_same += bool(_clean(chosen) == _clean(d["chosen"]))
        if d["decision"] != "set_card":
            continue
        print(f"\n--- L{d['loop']}D{d['day']} {d['view'].get('seat')}: "
              f"選択={_fmt(_clean(chosen))}")
        rows = [(s, k, tgt) for (card, tgt, kind), (s, k) in hp.attrib.items()
                if card == "暗躍禁止"]
        for s, keys, tgt in sorted(rows, key=lambda x: -x[0])[:6]:
            print(f"      {s:8.2f} 暗躍禁止→{tgt}  ←PRIORITY {list(keys) or '（定数/ no-op）'}")
    print(f"\n[probe] 主人公決定の bit 一致 = {n_same}/{n_all}")
    return 0


def cmd_cf(args) -> int:
    """反実仮想＝条件を1つずつ緩めた時に、その席で `暗躍禁止→刑事` が採用されるか。

    ★緩めるのは**プローブ実行中のモジュール定数だけ**（`agents/` のファイルは変更しない）。
    """
    import agents.defense_plan as dp
    meta, decisions = _load(args.log)
    variants = {
        "現行": {},
        "A:(4)を外す（ラバーズの的化を要求しない）": {"drop4": True},
        "B:(3)θ=0.0（実在度を問わない）": {"theta": 0.0},
        "C:(2)を外す（暗躍1でも打つ）": {"drop2": True},
        "D:A+C（(2)(4) 両方外す）": {"drop4": True, "drop2": True},
    }
    orig = dp.mainlover_anyaku_pin_live
    for label, cfg in variants.items():
        theta_old = dp.B201_MAINLOVER_P
        if "theta" in cfg:
            dp.B201_MAINLOVER_P = cfg["theta"]

        def patched(view, name, mm_chars, roles, day_now=None, _cfg=cfg):
            if name not in mm_chars:
                return False
            c = dp._char(view, name)
            if not c or not c.get("alive", True):
                return False
            if not _cfg.get("drop2") and c.get("anyaku", 0) != 0:
                return False
            if (roles or {}).get(name, {}).get("メインラバーズ", 0.0) < dp.B201_MAINLOVER_P:
                return False
            if _cfg.get("drop4"):
                return True
            return orig(view, name, mm_chars, roles, day_now)

        dp.mainlover_anyaku_pin_live = patched
        try:
            hp = ProbedProtagonist(0, top=400)
            picks = []
            for d in decisions:
                if d.get("actor") == "mastermind":
                    continue
                chosen = hp.decide(d["view"], d["decision"], d["options"])
                if d["decision"] == "set_card":
                    tag = "" if _clean(chosen) == _clean(d["chosen"]) else "★"
                    picks.append(f"L{d['loop']}D{d['day']}{d['view'].get('seat')}"
                                 f"={tag}{_fmt(_clean(chosen))}")
        finally:
            dp.mainlover_anyaku_pin_live = orig
            dp.B201_MAINLOVER_P = theta_old
        print(f"[{label}]")
        print("    " + " / ".join(picks))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B-207 Phase 0（読み取り専用プローブ）")
    ap.add_argument("cmd", choices=["pred", "attrib", "cf"])
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--ml", nargs="*", default=["刑事"], help="メインラバーズ疑いの席")
    ap.add_argument("--watch", nargs="*",
                    default=["刑事", "男子学生", "医者", "都市", "神社"])
    # ★B-260：era ピンの明示スイッチ（既定＝収録当時の既定へ倒して再生する）。
    ap.add_argument("--era-pin", dest="era_pin", action="store_true", default=True,
                    help="教材が録られた当時の既定へ倒して再生する（★既定）")
    ap.add_argument("--no-era-pin", dest="era_pin", action="store_false",
                    help="現行のリポジトリ既定のまま再生する（食い違う席が出るのが正常）")
    a = ap.parse_args(argv)
    with era_pin(a.era_pin, pins=ERA_PINS) as pinned:
        era_banner(bool(pinned), pins=ERA_PINS, consts=AUDITED_CONSTS, tag="b207")
        return {"pred": cmd_pred, "attrib": cmd_attrib, "cf": cmd_cf}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
