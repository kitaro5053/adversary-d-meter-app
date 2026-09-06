# -*- coding: utf-8 -*-
"""B-163 Phase 1：**黒猫のボード暗躍の「特別扱い」を数える**（★計測のみ・`agents/` 非接触）。

## 用語（★記号を使う前に、必ず日本語で定義する）

- **`board_anyaku`**＝「ボード名（病院／神社／都市／学校）→ そのボードに乗っている
  暗躍カウンターの個数」の辞書。主人公にも公開されている情報。
- **黒猫の特性1**＝`rules/30_characters.md:75`「**各ループ開始時に**、神社に暗躍カウンターを
  1つ置く」。置かれるのは**ループの準備段階**（`rules/00_rules_core.md:86` の
  「カウンター全除去 → ループ開始時に置くべきカウンターを置く」）。
  実装＝`engine/data.forced_loop_start_anyaku` を `sim/state.py:518-521` が1回だけ呼ぶ。
- **`_guess_defeat_board`（照準）**＝主人公AIのメソッド。返り値は
  「**このループで敗北しそうなボードは、たぶんこれ1つ**」という**点推定（ボード名 or None）**。
  この1つの値が、板ガードの +4.0 の宛先・B-59/B-67・カルティストの移動封じの**照準**になる。
  実体の計算は `_guess_defeat_board_raw`。
- **本件の対象コード**＝`agents/heuristic_protagonist.py` の `_guess_defeat_board_raw` 内の
  「神社の暗躍を1つ差し引いてから最大値を取る」3行（黒猫が**生きていれば**差し引く）。
- **照準要求**＝`_guess_defeat_board` が呼ばれた回数（1ターンに複数回呼ばれる）。
- **席**＝主人公の `set_card` 決定1回（b164_audit と同じ定義）。層別はこちらで行う。

## 数える2つの疑い（★混ぜない）

- **疑い1＝目的の混同**：差し引きの結果、全ボードが0になって照準が `None` に落ちる席が
  あるか。あるならその席で「収支側」（`defense_plan._threat_board_defeat`＝
  `board_anyaku` を素で読む）が同じボードを脅威に立てていたかを見る（＝逃げ道の検証）。
- **疑い2＝述語の誤り**：ガードが「黒猫が**今生きている**」。しかし特性1 の +1 は
  ループ開始時に置かれ、その後に黒猫が死んでも**ボードに残る**。
  ∴ 正しい述語は「黒猫が**そのループのキャストに居る**」。
  黒猫がループ途中で死ぬと差し引きが止まり、AI から見た神社の暗躍が突然 +1 跳ねる。

## 計測の方法（★判定を書き写さない）

反実仮想は「関数を書き写して分岐を変えた版」ではなく、
**`_alive("黒猫")` の返り値だけを差し替えて本物の `_guess_defeat_board` を呼び直す**
ことで作る（＝本体のロジックは 1 行も複製しない）。

- `r_now`  ＝現行（`_alive` そのまま）
- `r_nosub`＝差し引きを止めた版（黒猫を「居ない」と答えさせる）
- `r_cast` ＝疑い2 の是正版（黒猫が**キャストに居れば**差し引く）

CLI（測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`）:
    python -m arena.b163_audit verify --days 3
    python -m arena.b163_audit count  --days 3
    python -m arena.b163_audit roles  --days 3
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace

import agents.defense_plan as DP
from agents import HeuristicMastermind, HeuristicProtagonist
from arena.b145_audit import _lost_loops, _outcome
from sim import run_game

_AREAS = ("病院", "神社", "都市", "学校")
_NONE = object()          # 「上書きしない」を表す番兵


def _in_cast(view: dict, name: str) -> dict | None:
    """そのループのキャストに居るか（生死は問わない）＝特性1 が発火した条件。

    ★`sim/state.prepare_loop` はループ開始時に全キャラを生存で配置してから
    `forced_loop_start_anyaku` を呼ぶ（`sim/state.py:518-521`）＝
    「キャストに居る」＝「このループの開始時に神社へ +1 が置かれた」。
    """
    for c in view.get("characters", []) or []:
        if c.get("name") == name:
            return c
    return None


class _B163Probe(HeuristicProtagonist):
    """★`super()` の戻り値をそのまま返す＝挙動不変（`verify` で棋譜一致を物証化）。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.c: Counter = Counter()
        self.rows: list = []
        self.seat_rows: list = []
        self._kuro_override = _NONE   # _alive("黒猫") の返り値を差し替える
        self._probe_busy = False
        self._seat: Counter | None = None
        self._seat_need_e: list = []

    # -- `_alive` の 黒猫 だけを差し替える（反実仮想の唯一の継ぎ目） --------
    def _alive(self, view: dict, name: str | None):
        if name == "黒猫" and self._kuro_override is not _NONE:
            return self._kuro_override
        return super()._alive(view, name)

    # -- 照準要求ごとに3通りを取る -----------------------------------------
    def _guess_defeat_board(self, view: dict) -> str | None:
        r_now = super()._guess_defeat_board(view)
        if self._probe_busy:
            return r_now
        self._probe_busy = True
        try:
            kc = _in_cast(view, "黒猫")
            alive = bool(super()._alive(view, "黒猫"))
            ba = view.get("board_anyaku", {}) or {}
            shrine = int(ba.get("神社", 0) or 0)
            self._kuro_override = None                     # 差し引きなし
            r_nosub = super()._guess_defeat_board(view)
            self._kuro_override = kc if kc is not None else None   # 疑い2 の是正版
            r_cast = super()._guess_defeat_board(view)
        finally:
            self._kuro_override = _NONE
            self._probe_busy = False
        self._record(view, kc, alive, shrine, ba, r_now, r_nosub, r_cast)
        return r_now

    def _record(self, view, kc, alive, shrine, ba, r_now, r_nosub, r_cast):
        c, seat = self.c, self._seat
        c["照準要求（_guess_defeat_board 呼び出し）"] += 1

        def _bump(k):
            c[k] += 1
            if seat is not None:
                seat[k] = 1

        if kc is None:
            _bump("　黒猫がキャストに居ない（本件の射程外）")
            return
        _bump("黒猫がキャストに居る照準要求")
        # ---- (a) 差し引き条項 ------------------------------------------
        if alive and "神社" in ba:
            _bump("(a) 差し引き条項が真（黒猫 生存 ∧ 神社キーあり）")
            if shrine > 0:
                _bump("(a') うち実際に神社の値が1減った（神社≥1）")
        # ---- (b) 差し引きで照準が消えた / ずれた -------------------------
        if r_now != r_nosub:
            if r_now is None and r_nosub is not None:
                _bump("(b) ★差し引きの結果 照準が None に落ちた")
                _bump(f"　(b) 消えた照準の宛先＝{r_nosub}")
                self._seat_need_e.append(r_nosub)
            elif r_now is not None and r_nosub is None:
                _bump("(b-) 差し引きで逆に照準が生えた（★あれば要調査）")
            else:
                _bump("(b2) 差し引きで照準が別の板へ移った")
                _bump(f"　(b2) {r_nosub} → {r_now}")
        # ---- (c) 疑い2＝述語の反転（キャストに居るが今は死んでいる）------
        if not alive:
            _bump("(c) ★述語の反転席（黒猫がキャストに居るが 現在 死亡/不在）")
            if r_cast != r_now:
                _bump("(c') ★★うち是正で照準が変わる（現行 vs キャスト述語）")
                _bump(f"　(c') 現行={r_now} → 是正={r_cast}")
        if r_cast != r_now:
            _bump("(c'') 是正版と現行で照準が異なる照準要求（全体）")
        self.rows.append({
            "loop": view.get("loop"), "day": view.get("day"),
            "cast": True, "alive": alive, "shrine": shrine,
            "ba": {a: int(ba.get(a, 0) or 0) for a in _AREAS},
            "now": r_now, "nosub": r_nosub, "cast_pred": r_cast,
        })

    # -- 席（set_card 決定1回）で層別し、(e) を取る ------------------------
    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if decision != "set_card":
            return super().decide(view, decision, options)
        self._seat = Counter()
        self._seat_need_e = []
        chosen = super().decide(view, decision, options)
        seat, need_e = self._seat, self._seat_need_e
        self._seat, self._seat_need_e = None, []
        self.c["席（set_card 決定）"] += 1
        # ---- (e) 疑い1 の逃げ道＝収支側（_threat_board_defeat）が拾っているか
        if need_e:
            stash = getattr(self, "_b100_plan", None)
            threats = stash[0] if stash else None
            if threats is None:
                self.c["(e) 判定不能（計画スタッシュ無し）"] += 1
            else:
                bd = {t.label.split("の")[0]: t for t in threats
                      if getattr(t, "kind", "") == "board_defeat"}
                for area in need_e:
                    t = bd.get(area)
                    if t is None:
                        self.c[f"(e) ★収支側も {area} を脅威に立てていない"] += 1
                        seat["(e) 収支側も拾えていない"] = 1
                    else:
                        self.c[f"(e) 収支側は {area} を脅威に立てている"
                               f"（defendable={bool(t.defendable)}）"] += 1
                        seat["(e) 収支側で拾えている"] = 1
        for k, v in seat.items():
            self.c[f"[席] {k}"] += v
        if seat:
            self.seat_rows.append({"loop": view.get("loop"),
                                   "day": view.get("day"),
                                   "flags": sorted(seat)})
        return chosen


# ---------------------------------------------------------------------------
def _games(days: int, start: int = 0, end: int | None = None):
    from arena.benchmark import benchmark_scripts
    return list(benchmark_scripts(days=days))[start:end]


def _switches(days: int, loops: int) -> str:
    from agents.heuristic_protagonist import HeuristicProtagonist as H
    return (f"B153_JUUSHA_PAIR_BREAK={DP.B153_JUUSHA_PAIR_BREAK}"
            f" / B165_PAIR_BREAK={DP.B165_PAIR_BREAK}"
            f" / DP6_SUPPLY_LEDGER={DP.DP6_SUPPLY_LEDGER}"
            f" / B137={H.B137_DEFEAT_CAPABLE_ONLY}"
            f" / B141_COOLER_VALUE_FUTURE_ONLY="
            f"{getattr(H, 'B141_COOLER_VALUE_FUTURE_ONLY', 'n/a')}"
            f" / B100_MIX={H.B100_MIX} / B100_THETA={H.B100_THETA}"
            f" / days={days} loops={loops}")


def verify(days: int = 3, loops: int = 8, start: int = 0,
           end: int | None = None) -> dict:
    """★挙動不変の物証＝プローブ有無で棋譜が完全一致するか（全局）。"""
    bad, n = [], 0
    for name, seed, sc in _games(days, start, end):
        probe = replace(sc, loops=loops)
        a = _B163Probe(seed)
        sa, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": a, "p2": a, "p3": a})
        b = HeuristicProtagonist(seed)
        sb, _ = run_game(probe, {"mastermind": HeuristicMastermind(seed),
                                 "p1": b, "p2": b, "p3": b})
        ha = [(e.get("loop"), e.get("day"), e.get("event")) for e in sa.history]
        hb = [(e.get("loop"), e.get("day"), e.get("event")) for e in sb.history]
        n += 1
        if not (sa.winner == sb.winner and sa.loop_no == sb.loop_no and ha == hb):
            bad.append(f"{name} s{seed}: {_outcome(sa)} vs {_outcome(sb)}")
    return {"days": days, "n": n, "bad": bad}


def _kuro_deaths(state) -> set:
    """黒猫が**ループ途中で**死んだ (loop, day)（`secret_log` の death 記録）。"""
    out = set()
    for e in state.secret_log:
        if e.get("event") == "death" and e.get("name") == "黒猫":
            out.add((e.get("loop"), e.get("day")))
    return out


def _shrine_removals(state) -> int:
    """★神社の暗躍カウンターが**主人公能力フェイズの友好能力で剥がされた**回数。

    FableA の起票文は「主人公にカウンターを取り除く手段は無い」と書いているが、
    これは**誤り**＝`rules/20_goodwill_abilities.md:129` 巫女「神社の暗躍除去」と
    `:153` 神格「暗躍除去（同エリアのキャラ or 自ボード）」が剥がせる
    （実装＝`sim/abilities._miko_shrine_apply` / `_remove_anyaku_apply`・
     対象範囲の単一ソース＝`sim/abilities.board_anyaku_removal_scope`）。
    ∴「黒猫がキャストに居る＝神社に+1が乗っている」も**厳密には近似**なので、
    その誤差の大きさをここで数える（0 なら近似は実務上ぴったり）。
    """
    return sum(1 for e in state.history
               if e.get("event") == "anyaku" and e.get("target") == "神社"
               and (e.get("delta") or 0) < 0)


def count(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    c: Counter = Counter()
    names: set = set()
    kuro_names: set = set()
    ex: list = []
    n = 0
    for name, seed, sc in _games(days, start, end):
        names.add(name)
        if "黒猫" in sc.cast:
            kuro_names.add(name)
            c["局＝黒猫がキャストに居る"] += 1
        hp = _B163Probe(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        lost = set(_lost_loops(state))
        if getattr(state, "defeat", False):
            lost.add(state.loop_no)
        c.update(hp.c)
        c[f"結末＝{_outcome(state)}"] += 1
        kd = _kuro_deaths(state)
        c["黒猫がループ途中で死んだ (loop,day) 件数"] += len(kd)
        nrem = _shrine_removals(state)
        c["★神社の暗躍が友好能力で剥がされた回数（巫女/神格）"] += nrem
        if nrem:
            c["★同・それが起きた局数"] += 1
        for r in hp.seat_rows:
            tag = "敗北ループ" if r["loop"] in lost else "防衛ループ"
            for f in r["flags"]:
                c[f"[層別] {f}／{tag}"] += 1
        for r in hp.rows:
            if (r["now"] != r["nosub"] or r["now"] != r["cast_pred"]) \
                    and len(ex) < 40:
                r = dict(r)
                r["game"] = f"{name} s{seed}"
                r["lost_loop"] = r["loop"] in lost
                ex.append(r)
        n += 1
    return {"days": days, "n_games": n, "n_scripts": len(names),
            "n_scripts_with_kuro": len(kuro_names),
            "scripts_with_kuro": sorted(kuro_names),
            "counts": dict(c), "examples": ex}


def flip(days: int = 3, loops: int = 8, start: int = 0,
         end: int | None = None) -> dict:
    """(c') ★述語の是正で照準が変わる照準要求を**全数**、脚本名・ループ・日つきで印字する。

    ★doc は「具体的な脚本名・日・点数を必ず添える」（FableA の指示）＝そのための一覧。
    """
    out: list = []
    n = 0
    for name, seed, sc in _games(days, start, end):
        if "黒猫" not in sc.cast:
            n += 1
            continue
        hp = _B163Probe(seed)
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": HeuristicMastermind(seed),
                             "p1": hp, "p2": hp, "p3": hp})
        lost = set(_lost_loops(state))
        if getattr(state, "defeat", False):
            lost.add(state.loop_no)
        for r in hp.rows:
            if r["now"] == r["cast_pred"]:
                continue
            out.append({"game": f"{name} s{seed}", "rule_y": sc.rule_y,
                        "loop": r["loop"], "day": r["day"],
                        "黒猫生存": r["alive"], "神社": r["shrine"],
                        "board_anyaku": r["ba"],
                        "現行の照準": r["now"], "是正後の照準": r["cast_pred"],
                        "そのループを落としたか": r["loop"] in lost,
                        "結末": _outcome(state)})
        n += 1
    return {"days": days, "n_games": n, "n_flip_calls": len(out), "flips": out}


def roles(days: int = 3, loops: int = 8, start: int = 0,
          end: int | None = None) -> dict:
    """(f) 死文の確認＝`_suspects(roles, "黒猫")` が一度でも非空を返すか。

    `agents/b100_mix.KIND_SUPPLY_ROLES` の `"黒猫"` は
    `supply_candidate_count` から `defense_plan._suspects` へ渡る。
    `_suspects` は**役職名**で引くので、belief の役職キーに「黒猫」が無ければ常に空。
    ★ここでは belief が実際に返す役職キーの全集合を列挙して不存在を示す（完全列挙）。
    """
    from agents.b100_mix import KIND_SUPPLY_ROLES
    from agents.defense_plan import _suspects

    keys: set = set()
    c: Counter = Counter()

    class _P(_B163Probe):
        def decide(self, view, decision, options):
            r = super().decide(view, decision, options)
            if decision == "set_card" and self._belief is not None:
                try:
                    rm = self._belief.role_marginals()
                except Exception:
                    return r
                for _nm, dist in rm.items():
                    keys.update(dist.keys())
                c["role_marginals を読んだ席"] += 1
                if _suspects(rm, "黒猫"):
                    c["★_suspects(roles, 黒猫) が非空だった席"] += 1
                else:
                    c["_suspects(roles, 黒猫) が空だった席"] += 1
            return r

    n = 0
    for _name, seed, sc in _games(days, start, end):
        hp = _P(seed)
        run_game(replace(sc, loops=loops),
                 {"mastermind": HeuristicMastermind(seed),
                  "p1": hp, "p2": hp, "p3": hp})
        n += 1
    return {"days": days, "n_games": n,
            "belief の役職キー全集合": sorted(keys),
            "「黒猫」は役職キーか": "黒猫" in keys,
            "KIND_SUPPLY_ROLES に黒猫を持つ型":
                sorted(k for k, v in KIND_SUPPLY_ROLES.items() if "黒猫" in v),
            "counts": dict(c)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "count", "roles", "flip"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    a = ap.parse_args()
    print(f"== 切替口 == {_switches(a.days, a.loops)}")
    fn = {"verify": verify, "count": count, "roles": roles, "flip": flip}[a.cmd]
    r = fn(days=a.days, loops=a.loops, start=a.start, end=a.end)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
