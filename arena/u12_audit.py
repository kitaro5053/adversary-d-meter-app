# -*- coding: utf-8 -*-
"""U-12：ユーザー再戦棋譜（BTX3日・爆弾X）から「L5開始時点」の再開ファイルを作り、
温存仮説（暗躍+2 を後の日まで温存して 神社 へ出せば脚本家が勝てたか）を機械的に調べる。

★このモジュールは **agents/ の判断経路・既定値には一切触れない**（読むだけ）。
  成果物＝(1) ユーザーへ渡す再開 .jsonl、(2) その検証、(3) 反実仮想の探索。

サブコマンド:
    python -m arena.u12_audit build     # 再開ファイルを書き出す
    python -m arena.u12_audit verify    # 書き出したファイルをアプリと同じ経路で復元して検証
    python -m arena.u12_audit search    # 温存仮説の探索（S1/S2）
    python -m arena.u12_audit why-d2    # L5D2 に主人公AIが病院を守った理由（点数実測）

測定は必ず `PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8`。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from agents.debug import ProbedProtagonist
from agents.heuristic import HeuristicMastermind
from arena.gamelog import (game_to_jsonl, human_choices_from_decisions, load_game,
                           load_game_lines, resume_from_meta, split_day_tail)
from arena.interactive import play_interactive
from arena.loopcap import restored_extra_loops, total_loops
from arena.play_vs_ai import run_to_pending
from sim.flow import log_safe_chosen
from sim.state import GameState

REPO = Path(__file__).resolve().parent.parent
LOG_DEFAULT = REPO / "docs/feedback_logs/2026-08-02_脚本家プレイ_BTX3日_爆弾X_再戦.jsonl"
OUT_DEFAULT = REPO / "docs/feedback_logs/再開_BTX3日_爆弾X_L5開始.jsonl"
MM_SEAT = {"mastermind"}
RESUME_KEY = (5, 1)          # 書き出す再開点＝L5 の D1 開始時点（＝L4終了直後）
TOTAL_LOOPS = 5              # 延長1回込みの総ループ数（棋譜が L5 まで進んでいる）
#: 再開ファイルの meta.saved_at（固定＝同じ入力からは毎回同じバイト列になるように）
SAVED_AT = "2026-08-02 00:00:00 UTC"


# ---------------------------------------------------------------------------
# 棋譜の再生（記録どおり＝AI非依存）
# ---------------------------------------------------------------------------
def ai_replay_safe(decisions, human_seats=MM_SEAT) -> dict[str, list[dict]]:
    """AI席の記録済み選択を席ごとに取り出す（`prov` を剥がす）。

    ★`arena.gamelog.ai_replay_from_decisions` は `prov`（B-100 の表示タグ）を剥がさないため、
      タグ付きの手が「新規列挙した options に無い」と判定され、その席が途中から live AI に
      切り替わる＝棋譜が再現できない。`split_day_tail` と同じ `log_safe_chosen` を通す。
    """
    seats = set(human_seats)
    out: dict[str, list[dict]] = {}
    for d in decisions:
        a = d.get("actor")
        if a is not None and a not in seats:
            out.setdefault(a, []).append(log_safe_chosen(d["chosen"]))
    return out


def _strip_prov(o):
    """`prov`（B-100 の表示タグ）を再帰的に落とす（比較用・純関数）。"""
    if isinstance(o, dict):
        return {k: _strip_prov(v) for k, v in o.items() if k != "prov"}
    if isinstance(o, list):
        return [_strip_prov(v) for v in o]
    return o


def _dkey(d) -> tuple:
    """決定の同一性キー（`prov` を無視して比較する）。"""
    c = {k: v for k, v in d["chosen"].items() if k != "prov"}
    return (d["loop"], d["day"], d["phase"], d["actor"], d["decision"],
            json.dumps(c, ensure_ascii=False, sort_keys=True))


def replay_upto(log_path, key=RESUME_KEY, loops=TOTAL_LOOPS):
    """棋譜を `key`（loop,day）の直前まで記録どおり再生する。

    返り値 (script5, pending_state, log, snapshot, decisions_all, meta)。
    """
    script, meta, decisions = load_game(log_path)
    before = [d for d in decisions if (d["loop"], d["day"]) < key]
    mm_before = [d["chosen"] for d in before if d["actor"] == "mastermind"]
    st, pend, log, _hp = run_to_pending(script, 0, mm_before, loops=loops,
                                        ai_replay=ai_replay_safe(before))
    if pend is None:
        raise RuntimeError(f"{key} の手前で止まらなかった（終局した）")
    if (st.loop_no, st.day) != key:
        raise RuntimeError(f"停止位置が {key} でない: {(st.loop_no, st.day)}")
    snaps = getattr(st, "day_snaps", {}) or {}
    if key not in snaps:
        raise RuntimeError(f"day_snaps に {key} が無い")
    return replace(script, loops=loops), st, log, snaps[key], decisions, meta


# ---------------------------------------------------------------------------
# build：再開ファイルの書き出し
# ---------------------------------------------------------------------------
def cmd_build(args) -> int:
    script5, st, log, snap, decisions, meta = replay_upto(args.log)
    # 全編再生が棋譜と一致するか（＝再生経路の健全性）も同時に確かめる
    mm_all = human_choices_from_decisions(decisions, MM_SEAT)
    st_all, _p, log_all, _ = run_to_pending(
        load_game(args.log)[0], 0, mm_all, loops=TOTAL_LOOPS,
        ai_replay=ai_replay_safe(decisions))
    same = [_dkey(x) for x in log_all] == [_dkey(y) for y in decisions]
    print(f"[build] 全編再生の一致 = {same}（{len(log_all)}/{len(decisions)} 決定）")
    if not same:
        raise RuntimeError("棋譜の完全再生に失敗（再開ファイルを書かない）")

    payload = game_to_jsonl(
        script5, st, log,
        resume_snapshot={"loop": RESUME_KEY[0], "day": RESUME_KEY[1], "snapshot": snap},
        saved_at=SAVED_AT)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(payload, encoding="utf-8")
    print(f"[build] 書き出し: {args.out}（{len(payload)} bytes・decisions={len(log)}）")
    print(f"[build] resume_snapshot = L{RESUME_KEY[0]}D{RESUME_KEY[1]}"
          f"／script.loops = {script5.loops}")
    return 0


# ---------------------------------------------------------------------------
# verify：アプリの📂読み込みと同じ経路で復元して検証
# ---------------------------------------------------------------------------
def _chars_of(state) -> dict:
    return {n: {"role": c.role, "area": c.area, "alive": c.alive,
                "unrest": c.unrest, "goodwill": c.goodwill,
                "paranoia_max": getattr(c, "paranoia_max", None)}
            for n, c in state.characters.items()}


def cmd_verify(args) -> int:
    # -- 期待値＝棋譜を記録どおり再生して得た L5D1 の局面 --
    script5, st_exp, log_exp, snap_exp, decisions, meta = replay_upto(args.log)
    exp = GameState.from_snapshot(snap_exp)

    # -- 実際の復元＝アプリ（play_vs_ai の📂読み込み）と同じ関数 --
    lines = [ln for ln in Path(args.out).read_text(encoding="utf-8").splitlines() if ln.strip()]
    script_obj, meta2, decisions2 = load_game_lines(lines)
    got = resume_from_meta(meta2, decisions2, MM_SEAT)
    assert got is not None, "resume_from_meta が None（snapshot 復元に失敗＝replayへ落ちる）"
    state, hc, ai, warn = got

    checks: list[tuple[str, bool, str]] = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    chk("復元位置 = L5D1", (state.loop_no, state.day) == RESUME_KEY,
        f"{(state.loop_no, state.day)}")
    chk("当日分の再生材料が空（＝L5D1の頭から）", hc == [] and ai == {},
        f"human={len(hc)} ai={ {k: len(v) for k, v in ai.items()} }")
    chk("救済warnなし", warn == "", repr(warn))
    chk("script.loops = 5（延長1回が復元される）", state.script.loops == TOTAL_LOOPS,
        str(state.script.loops))
    chk("restored_extra_loops = 1", restored_extra_loops(state) == 1,
        str(restored_extra_loops(state)))
    chk("total_loops = 5", total_loops(restored_extra_loops(state)) == TOTAL_LOOPS, "")

    # used_cards＝L5開始時点で全席リセット（1/loop札が戻っている）
    chk("used_cards が全席で空（1/loop札がリセット済み）",
        all(v == [] for v in state.used_cards.values()),
        json.dumps(state.used_cards, ensure_ascii=False))
    chk("脚本家の手札が10枚（暗躍+2 を含む）",
        sorted(state.hand_of("mastermind")) == sorted(
            ['移動↑↓', '移動←→', '移動斜め', '友好禁止', '不安+1', '不安+1',
             '不安-1', '不安禁止', '暗躍+1', '暗躍+2']),
        json.dumps(sorted(state.hand_of("mastermind")), ensure_ascii=False))

    # 盤面・キャラ（期待＝記録どおり再生した L5D1）
    chk("キャラ状態（配置/生死/不安/友好/役職）が棋譜と一致",
        _chars_of(state) == _chars_of(exp),
        json.dumps(_chars_of(state), ensure_ascii=False))
    chk("ボード暗躍が棋譜と一致", state.board_anyaku == exp.board_anyaku,
        json.dumps(state.board_anyaku, ensure_ascii=False))
    chk("ボードX（爆弾X）が棋譜と一致", state.rule_y_board_x == exp.rule_y_board_x,
        str(state.rule_y_board_x))
    chk("リーダー席が一致", state.leader_idx == exp.leader_idx, str(state.leader_idx))
    chk("公開済み役職（friend_revealed 等）が一致",
        set(getattr(state, "friend_revealed", set())) ==
        set(getattr(exp, "friend_revealed", set())), "")
    chk("使用済み友好能力が一致",
        set(state.used_goodwill) == set(exp.used_goodwill), "")

    # 履歴＝L1〜L4 の全事象 ＋ L5 の「ループ準備」まで（snapshot は prepare_loop の後で撮る）
    hist = list(state.history)
    orig_hist = [e for e in meta["history"]
                 if (int(e.get("loop", 0)), int(e.get("day", 0))) < RESUME_KEY]
    chk("履歴が L5D1 開始時点まで（L5 は loop_start のみ）",
        all((int(e.get("loop", 0)), int(e.get("day", 0))) < RESUME_KEY for e in hist)
        and all(e.get("phase") == "loop_start"
                for e in hist if int(e.get("loop", 0)) == 5),
        json.dumps([e for e in hist if int(e.get("loop", 0)) == 5], ensure_ascii=False))
    # ★`prov`（B-100 の表示タグ）は表示層専用＝比較から外す（記録どおり強制再生した手には
    #   タグが付かないため。盤面・カウンター・カードの中身は下の bit 一致で担保される）。
    chk("履歴が元棋譜の同区間と一致（件数と内容・prov除く）",
        json.dumps(_strip_prov(hist), ensure_ascii=False, sort_keys=True) ==
        json.dumps(_strip_prov(orig_hist), ensure_ascii=False, sort_keys=True),
        f"復元={len(hist)}件 / 元={len(orig_hist)}件")
    chk("履歴が記録どおり再生した局面と完全一致",
        json.dumps(hist, ensure_ascii=False, sort_keys=True) ==
        json.dumps(list(exp.history), ensure_ascii=False, sort_keys=True), "")
    chk("secret_log が記録どおり再生した局面と完全一致",
        json.dumps(list(state.secret_log), ensure_ascii=False, sort_keys=True) ==
        json.dumps(list(exp.secret_log), ensure_ascii=False, sort_keys=True), "")
    chk("snapshot 全体が記録どおり再生した局面と完全一致（bit一致）",
        json.dumps(state.to_snapshot(), ensure_ascii=False, sort_keys=True) ==
        json.dumps(exp.to_snapshot(), ensure_ascii=False, sort_keys=True), "")

    # decisions（ログ本体）＝L1〜L4 の 101 決定
    chk("同梱decisionsが L1〜L4 のみ",
        all((d["loop"], d["day"]) < RESUME_KEY for d in decisions2),
        f"{len(decisions2)}件")
    chk("同梱decisionsが元棋譜の L1〜L4 と一致",
        [_dkey(d) for d in decisions2] ==
        [_dkey(d) for d in decisions if (d["loop"], d["day"]) < RESUME_KEY], "")
    b, hd, ad = split_day_tail(decisions2, MM_SEAT, RESUME_KEY)
    chk("split_day_tail：当日分ゼロ・前日までの脚本家手数=63-9=54",
        hd == [] and ad == {} and b == len([d for d in decisions2
                                            if d["actor"] == "mastermind"]),
        f"before={b} day_human={len(hd)}")

    # 復元局面から棋譜どおりの L5 を打ち直せる（＝続きが本当に指せる）
    mm_l5 = [log_safe_chosen(d["chosen"]) for d in decisions
             if d["actor"] == "mastermind" and d["loop"] == 5]
    st5, pend5, log5, _ = run_to_pending(
        script_obj, 0, mm_l5, loops=TOTAL_LOOPS, ai_replay=None,
        initial_state=GameState.from_snapshot(state.to_snapshot()))
    l5_orig = [_dkey(d) for d in decisions if d["loop"] == 5]
    l5_got = [_dkey(d) for d in log5]
    d1_n = len([k for k in l5_orig if k[1] == 1])
    chk("復元局面から現行AIで L5D1 が棋譜どおり再現される",
        l5_got[:d1_n] == l5_orig[:d1_n], f"D1 {d1_n} 決定")
    chk("復元局面から現行AIで L5 全体を走らせても主人公の防衛勝ち",
        st5.winner == "protagonist", f"winner={st5.winner}")

    ok = True
    for name, cond, detail in checks:
        print(f"  [{'OK ' if cond else 'NG '}] {name}" + (f"  … {detail}" if detail and not cond else ""))
        ok = ok and cond
    print(f"[verify] 全 {len(checks)} 項目 … {'PASS' if ok else 'FAIL'}")
    # 参考：L5 全体は棋譜と一致するか（＝復元後の未来は現行AI＝ずれうる）
    print(f"[verify] 参考：L5 全体の一致 = {l5_got == l5_orig}"
          f"（{sum(1 for x, y in zip(l5_got, l5_orig) if x == y)}/{len(l5_orig)} 決定）")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 反実仮想（温存仮説）の探索
# ---------------------------------------------------------------------------
#: 埋め札の優先順（★宣言：計画が「暗躍札以外」を要求した枠で、この順に**合法な最初の札**を置く。
#  ただしその枠に棋譜で置かれていた札（暗躍以外）が合法ならそれを最優先＝ユーザーの実戦に最も近い）。
FILL_ORDER = ("不安+1", "不安-1", "不安禁止", "友好禁止", "移動↑↓", "移動←→", "移動斜め")


class PlanMastermind:
    """set_card は与えた計画どおりに置き、それ以外の決定は棋譜の記録を優先する脚本家。

    - `plan`＝{(loop, day): [枠, 枠, 枠]}。枠＝{"target","target_kind","card"}。
      `card` が None の枠は「埋め札」＝`prefer`（棋譜のその枠の札）→ `FILL_ORDER` の順で
      **合法な最初の札**を置く。`card` 指定の枠は合法でなければ例外（＝その手順は無効）。
    - set_card 以外（脚本家能力・事件の対象選択・ループ準備）は
      (loop, day, decision) 単位で棋譜の記録を順に消費し、非合法／記録切れなら
      `HeuristicMastermind` に委ねる（＝空白を作らない）。
    """

    class Illegal(Exception):
        pass

    def __init__(self, plan: dict, recorded: dict, seed: int = 0):
        self.plan = {k: [dict(s) for s in v] for k, v in plan.items()}
        self.recorded = {k: list(v) for k, v in recorded.items()}
        self.fallback = HeuristicMastermind(seed)
        self.fallback_used = 0

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        key = (view.get("loop"), view.get("day"))
        if decision == "set_card":
            seq = self.plan.get(key)
            if not seq:
                raise PlanMastermind.Illegal(f"計画に {key} の枠が足りない")
            slot = seq.pop(0)
            tgt, kind = slot["target"], slot["target_kind"]
            if slot.get("card"):
                c = _placement(slot["card"], tgt, kind)
                if c not in options:
                    raise PlanMastermind.Illegal(f"計画の手が非合法 {key} {c}")
                return c
            for card in ((slot.get("prefer"),) if slot.get("prefer") else ()) + FILL_ORDER:
                c = _placement(card, tgt, kind)
                if c in options:
                    return c
            raise PlanMastermind.Illegal(f"埋め札が置けない {key} {tgt}")
        rkey = (key[0], key[1], decision)
        seq = self.recorded.get(rkey)
        while seq:
            c = seq.pop(0)
            if c in options:
                return c
        self.fallback_used += 1
        return self.fallback.decide(view, decision, options)


def recorded_nonset(decisions) -> dict:
    """棋譜の脚本家決定のうち set_card 以外を (loop, day, decision) ごとに順に集める。"""
    out: dict = {}
    for d in decisions:
        if d["actor"] != "mastermind" or d["decision"] == "set_card":
            continue
        out.setdefault((d["loop"], d["day"], d["decision"]), []).append(
            log_safe_chosen(d["chosen"]))
    return out


def run_line(script5, snap, plan: dict, recorded: dict, seed: int = 0, probe_top: int = 0):
    """L5D1 の局面から、脚本家の計画 `plan` で1ループ走らせる。

    返り値 (winner, state, hp) ／ 計画が非合法なら (None, None, None)。

    ★脚本家の set_card は `allow_bluff=True` で列挙する＝**人間が脚本家のときと同じ候補**
      （不安/友好/移動をボードへ置く「ダミー配置」＝解決されない囮。棋譜でも多用されている）。
      `arena.interactive` は human_seats のときだけこれを行うので、ここで同じことをする。
    ★主人公3席は現行 `HeuristicProtagonist`（`ProbedProtagonist` は記録するだけで挙動同一）。
    """
    from sim.flow import run_loop
    from sim.legal import set_card_options
    from sim.views import mastermind_view, protagonist_view

    state = GameState.from_snapshot(snap)
    state.script = replace(state.script, loops=TOTAL_LOOPS)
    hp = ProbedProtagonist(seed, top=probe_top) if probe_top else ProbedProtagonist(seed)
    mm = PlanMastermind(plan, recorded, seed)

    def decide(actor: str, decision: str, options: list[dict]) -> dict:
        if actor == "mastermind":
            if decision == "set_card":
                options = set_card_options(state, "mastermind", allow_bluff=True)
            return mm.decide(mastermind_view(state), decision, options)
        return hp.decide(protagonist_view(state, actor), decision, options)

    try:
        run_loop(state, decide, final_battle=False,
                 human_seats=MM_SEAT, resume_from_state=True)
    except PlanMastermind.Illegal as e:
        return None, str(e), None
    return state.winner, state, hp


def _placement(card, target, kind):
    return {"card": card, "target": target, "target_kind": kind}


BOARDS = ("病院", "神社", "都市", "学校")


def kifu_l5_slots(decisions) -> dict:
    """棋譜の L5 の置き位置（3日×3枠）を取り出す。枠＝{target, target_kind, prefer(棋譜の札)}。"""
    out: dict = {}
    for day in (1, 2, 3):
        row = [log_safe_chosen(d["chosen"]) for d in decisions
               if d["loop"] == 5 and d["day"] == day
               and d["actor"] == "mastermind" and d["decision"] == "set_card"]
        out[day] = [{"target": c["target"], "target_kind": c["target_kind"],
                     "prefer": (None if str(c["card"]).startswith("暗躍") else c["card"]),
                     "card": None} for c in row]
    return out


def build_plans(decisions):
    """探索空間を作る。**範囲と打ち切り条件を明示**（隠れた足切りを作らない）。

    ★カードの経済（engine/models.ONCE_PER_LOOP）：脚本家で「1ループ1回」なのは
      **移動斜め と 暗躍+2 だけ**。**暗躍+1 は毎日でも置ける**（解決後に手札へ戻る）。
      ∴「暗躍+2 の温存」だけでなく「暗躍+1 を複数日 神社へ重ねる」も勝ち筋の候補になる。

    S-A（σ不変・完全列挙）＝**相手に見える置き位置は棋譜のまま**にして中身だけ入れ替える。
      - 暗躍+1：各日 {置かない, 枠0, 枠1, 枠2} … 4 通り/日 → 4^3 = 64
      - 暗躍+2：{置かない} ∪ 9枠 … 10 通り（暗躍+1 と同じ枠は不可）
      - 残りの枠は「その枠に棋譜で置かれていた非暗躍札 → FILL_ORDER」の順に合法な札。
      → 重複を除いて 600 前後。
    S-B（置き位置も動かす・完全列挙）＝暗躍札を 4枚の板のどこへでも置ける版。
      - 暗躍+1：各日 {置かない} ∪ {病院,神社,都市,学校} … 5 通り/日 → 5^3 = 125
      - 暗躍+2：{置かない} ∪ (3日 × 4板) … 13 通り
      - 枠の割り当ては決定的：暗躍+2 はその日の枠0、暗躍+1 は枠1（同日に暗躍+2 が無ければ枠0）。
        残りの枠は S-A と同じ規則。→ 125 × 13 = 1625 通り。
    ★打ち切り条件：**無し**（上の全通りを走らせる。時間・スコアによる足切りはしない）。
      非合法で走れなかった手順は件数を報告する。
    ★∴「この空間に勝ち筋が有る／無い」は完全列挙の主張。**この空間の外については何も主張しない**
      （＝unknown。共通規約 §3「不存在の主張は完全列挙のみ」）。
    """
    base = kifu_l5_slots(decisions)
    plans, seen = [], set()

    def emit(tag, a2, a1, reqs):
        """reqs＝{day: [(card, target, target_kind), ...]}（暗躍札の置き要求）。

        枠の決め方（決定的・宣言）：要求の対象がその日の棋譜の枠と同じならその枠に入れる
        （＝同一対象の重複置き＝規則違反 DUP_TARGET を作らない）。無ければ空いている先頭の枠。
        """
        slots = {}
        for day in (1, 2, 3):
            row = [dict(s) for s in base[day]]
            taken: set[int] = set()
            for card, tgt, kind in reqs.get(day, []):
                idx = next((i for i, s in enumerate(row)
                            if s["target"] == tgt and i not in taken), None)
                if idx is None:
                    idx = next((i for i in range(3) if i not in taken), None)
                if idx is None:
                    return
                taken.add(idx)
                row[idx] = {"target": tgt, "target_kind": kind, "prefer": None,
                            "card": card}
            if len({s["target"] for s in row}) != 3:
                return           # 同じ対象へ2枚＝規則違反（DUP_TARGET）＝この手順は作らない
            slots[(5, day)] = row
        key = json.dumps({f"{k[0]}-{k[1]}": v for k, v in slots.items()},
                         ensure_ascii=False, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        plans.append({"space": tag, "anyaku2": a2, "anyaku1": a1, "plan": slots})

    # -- S-A：置き位置は棋譜のまま（σ不変）＝中身だけ入れ替える --
    for i1 in ((None,) + tuple(range(3))):
        for i2 in ((None,) + tuple(range(3))):
            for i3 in ((None,) + tuple(range(3))):
                pos1 = {1: i1, 2: i2, 3: i3}
                for p2 in [None] + [(d, i) for d in (1, 2, 3) for i in range(3)]:
                    if p2 and pos1[p2[0]] == p2[1]:
                        continue
                    reqs: dict = {}
                    for d in (1, 2, 3):
                        if pos1[d] is not None:
                            s = base[d][pos1[d]]
                            reqs.setdefault(d, []).append(
                                ("暗躍+1", s["target"], s["target_kind"]))
                    if p2:
                        s = base[p2[0]][p2[1]]
                        reqs.setdefault(p2[0], []).append(
                            ("暗躍+2", s["target"], s["target_kind"]))
                    emit("S-A", p2, tuple(pos1[d] for d in (1, 2, 3)), reqs)

    # -- S-B：暗躍札の置き先を4板から選ぶ（σが変わる）--
    opts1 = (None,) + BOARDS
    for x1 in opts1:
        for x2 in opts1:
            for x3 in opts1:
                per_day = {1: x1, 2: x2, 3: x3}
                for p2 in [None] + [(d, b) for d in (1, 2, 3) for b in BOARDS]:
                    reqs = {}
                    for d in (1, 2, 3):
                        if p2 and p2[0] == d:
                            reqs.setdefault(d, []).append(("暗躍+2", p2[1], "board"))
                        if per_day[d]:
                            if p2 and p2[0] == d and p2[1] == per_day[d]:
                                continue     # 同じ日の同じ板へ2枚＝規則違反
                            reqs.setdefault(d, []).append(("暗躍+1", per_day[d], "board"))
                    emit("S-B", p2, tuple(per_day[d] for d in (1, 2, 3)), reqs)
    return plans


def base_plan_from_kifu(decisions) -> dict:
    """棋譜どおりの L5（基準線）。"""
    return {(5, day): [dict(log_safe_chosen(d["chosen"]), target_kind=d["chosen"]["target_kind"])
                       for d in decisions
                       if d["loop"] == 5 and d["day"] == day
                       and d["actor"] == "mastermind" and d["decision"] == "set_card"]
            for day in (1, 2, 3)}


def cmd_search(args) -> int:
    import time
    script5, st_exp, log_exp, snap, decisions, meta = replay_upto(args.log)
    recorded = recorded_nonset(decisions)
    plans = build_plans(decisions)
    if args.limit:
        plans = plans[: args.limit]
    na = sum(1 for p in plans if p["space"] == "S-A")
    print(f"[search] 探索空間 = {len(plans)} 手順（S-A={na} / S-B={len(plans) - na}）打ち切り無し")

    w, stb, _hpb = run_line(script5, snap, base_plan_from_kifu(decisions), recorded)
    print(f"[search] 基準（棋譜どおり）: winner={w} "
          f"神社暗躍={stb.board_anyaku['神社'] if stb else '-'} "
          f"病院暗躍={stb.board_anyaku['病院'] if stb else '-'}")

    from collections import Counter
    wins, illegal, rows = [], Counter(), []
    t0 = time.time()
    for i, p in enumerate(plans):
        w, st, _hp = run_line(script5, snap, p["plan"], recorded)
        if w is None:
            illegal[str(st).split("(")[0][:60]] += 1
            continue
        row = {"space": p["space"], "anyaku2": p["anyaku2"], "anyaku1": p["anyaku1"],
               "winner": w, "shrine": st.board_anyaku["神社"],
               "hospital": st.board_anyaku["病院"],
               "plan": {f"D{d}": [f"{s.get('card') or '埋'}→{s['target']}"
                                  for s in p["plan"][(5, d)]] for d in (1, 2, 3)}}
        rows.append(row)
        if w == "mastermind":
            wins.append(row)
        if args.verbose and (i % 100 == 0):
            print(f"  [{i + 1}/{len(plans)}] {time.time() - t0:.0f}s 勝ち={len(wins)}")
    print(f"[search] 走った手順 = {len(rows)}／非合法で落ちた = {sum(illegal.values())}"
          f"／所要 {time.time() - t0:.0f}s")
    for k, v in illegal.most_common():
        print(f"    非合法の内訳: {v:5d}  {k}")
    print(f"[search] 脚本家の勝ち = {len(wins)} 件 / {len(rows)}")
    for sp in ("S-A", "S-B"):
        tot = [r for r in rows if r["space"] == sp]
        wi = [r for r in tot if r["winner"] == "mastermind"]
        print(f"    {sp}: 勝ち {len(wi)} / 走った {len(tot)}")
    for r in wins[: args.show]:
        print("   ★勝ち筋:", json.dumps(r, ensure_ascii=False))
    Path(args.dump).write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print(f"[search] 全結果を {args.dump} に保存")
    return 0


# ---------------------------------------------------------------------------
# why-d2：L5D2 に主人公AIが病院を守った理由（点数の実測）
# ---------------------------------------------------------------------------
def cmd_why_d2(args) -> int:
    script5, st_exp, log_exp, snap, decisions, meta = replay_upto(args.log)
    recorded = recorded_nonset(decisions)
    base_plan = {}
    for day in (1, 2, 3):
        base_plan[(5, day)] = [log_safe_chosen(d["chosen"]) for d in decisions
                               if d["loop"] == 5 and d["day"] == day
                               and d["actor"] == "mastermind" and d["decision"] == "set_card"]
    w, st, hp = run_line(script5, snap, base_plan, recorded, probe_top=80)
    print(f"[why-d2] 基準（棋譜どおりの L5）: winner={w} "
          f"神社暗躍={st.board_anyaku['神社']} 病院暗躍={st.board_anyaku['病院']}")
    for r in hp.records:
        if r["decision"] != "set_card" or r["loop"] != 5 or r["day"] not in (1, 2, 3):
            continue
        print(f"--- L{r['loop']}D{r['day']} {r['seat']} 選択="
              f"{json.dumps({k: v for k, v in r['chosen'].items() if k != 'prov'}, ensure_ascii=False)}")
        for s, o in r["scored"]:
            if str(o.get("card")) == "暗躍禁止" or s >= 20:
                print(f"    {s:>10.4f}  "
                      f"{json.dumps({k: v for k, v in o.items() if k != 'prov'}, ensure_ascii=False)}")
        est = r.get("estimates", {})
        keep = {k: est[k] for k in ("_keyperson", "_killer", "_observed_defeat_board",
                                    "_loop_lost", "_kp_doomed") if k in est}
        print("    estimates:", json.dumps(keep, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="U-12：L5再開ファイルと温存仮説の監査")
    ap.add_argument("cmd", choices=["build", "verify", "search", "why-d2"])
    ap.add_argument("--log", default=str(LOG_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--dump", default="/tmp/u12_search.json")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0, help="デバッグ用：先頭N手順だけ走らせる")
    a = ap.parse_args(argv)
    return {"build": cmd_build, "verify": cmd_verify,
            "search": cmd_search, "why-d2": cmd_why_d2}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
