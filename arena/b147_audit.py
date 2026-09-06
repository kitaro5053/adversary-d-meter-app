# -*- coding: utf-8 -*-
"""B-147＝**フレンド同定（belief 側）の現状把握**。

発端＝`docs/監査_B145_フレンド引き込み移動への備え_2026-08-03.md` §2b。
B-145 は「備えが無い」の内訳を3つに割り、最大の塊が
**(ii) SK は確定だが急所（フレンド）が未同定**（3日級 14席／5日級 15席）だと示した。
本器はその (ii) 層を**再現**し、**その席で belief が何を持っていたか**を実測する。

★**二重実装しない**＝対局・層別・盤面判定はすべて `arena.b145_audit` の関数をそのまま呼ぶ。
  本器が足すのは「belief の分布をどう読むか」だけ（`KEEP_MARGINALS=True` で落ちる行を読む）。
★**挙動不変**＝`b145_audit._Probe` は `super().decide()` の戻り値の後で属性を読むだけ
  （`b145_audit verify` が素の対局との一致を毎回確認している）。

使い方:
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b147_audit split --days 3
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b147_audit split --days 5
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b147_audit channels
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from agents import HeuristicProtagonist
from arena import b145_audit

#: 「確定」とみなす確率（§2b の読み方＝pVIP=1.0／pSK=1.0 の帯）。
_CERTAIN = 0.999
#: 「未同定」とみなす確率（§2b の (ii) の定義＝pVIP<=0.5）。
_UNIDENT = 0.5
#: 同値（＝観測で区別が付いていない）とみなす確率差。
_EPS = 1e-6


# ---------------------------------------------------------------------------
# §2b の情報状態の割り（B-145 の定義をそのまま実装）
# ---------------------------------------------------------------------------
def info_state(r: dict, sk_thresh: float = _CERTAIN) -> str:
    """B-145 §2b の4層。pVIP＝当該 VIP の（その役職の）確率。

    ★`sk_thresh`＝(ii) 層で「SK は確定」とみなす下限。B-145 §2b は (iii) 層を
    pSK=1.0 と明記しているが、(ii) 層の「SK は確定」の閾値は**本文に書かれていない**。
    ∴ 本器は既定を 1.0 とし、`--sk-thresh` で感度を出せるようにした（§3-1 の突き合わせ）。
    """
    p_vip = r.get("p_kp_vip") if r.get("vip_role") == "キーパーソン" \
        else r.get("p_friend_vip")
    p_vip = float(p_vip or 0.0)
    p_sk = float(r.get("p_sk_killer") or 0.0)
    guards = bool(r.get("vip_in_guards"))
    if p_vip >= _CERTAIN and p_sk >= _CERTAIN and guards:
        return "i3_both"                     # 両方確定＝純粋な盲点（採点側の課題）
    if p_vip >= _CERTAIN and p_sk < 0.1:
        return "i1_sk_unknown"               # 急所は確定・SK が未同定
    if p_sk >= sk_thresh and p_vip <= _UNIDENT:
        return "ii_vip_unknown"              # ★SK は確定・急所が未同定＝本チケット
    return "i0_none"                         # どちらも未同定（多くは情報ゼロ席）


# ---------------------------------------------------------------------------
# belief が何を持っていたか（1席）
# ---------------------------------------------------------------------------
def belief_view(r: dict) -> dict:
    """(ii) 席で belief が**その急所の役職**について持っていた材料を読み解く。

    ★役職ごとに読む分布を変える（KP 行で P(フレンド) を読むと常に 0＝誤読になる）。
    """
    pf: dict = (r.get("p_kp_all") if r.get("vip_role") == "キーパーソン"
                else r.get("p_friend_all")) or {}
    vic = r["vip"]
    alive = set(r.get("alive_at_seat") or ())
    p_true = float(pf.get(vic, 0.0))
    # 生存者に限った順位（死者は引き込みの対象にならない＝比較の土俵を揃える）
    live_pf = {n: float(p) for n, p in pf.items() if n in alive}
    ranked = sorted(live_pf.items(), key=lambda kv: (-kv[1], kv[0]))
    rank = 1 + sum(1 for _n, p in live_pf.items() if p > p_true + _EPS)
    tie = sorted(n for n, p in live_pf.items() if abs(p - p_true) <= _EPS)
    # ★完全交換可能＝役職分布ベクトルが真フレンドと**まるごと一致**する生存者
    mf: dict = r.get("marg_full") or {}
    mine = mf.get(vic) or {}

    def _same(o: dict) -> bool:
        keys = set(mine) | set(o)
        return all(abs(float(mine.get(k, 0.0)) - float(o.get(k, 0.0))) <= 1e-5
                   for k in keys)

    exch = sorted(n for n in alive
                  if n != vic and n in mf and _same(mf[n]))
    # ★belief が使っていないチャネル＝相手の札の宛先（opponent-model）。
    #   **同値クラスの中だけ**で比べる（クラス外との比較は belief が既に片付けている）。
    mt: dict = r.get("mm_targets") or {}
    tie_mt = {n: int(mt.get(n, 0)) for n in tie}
    mt_max = max(tie_mt.values()) if tie_mt else 0
    mt_leaders = sorted(n for n, k in tie_mt.items() if k == mt_max and k > 0)
    return {
        "mm_targets_tie": tie_mt,
        "mm_leader_is_true": bool(vic in mt_leaders),
        "mm_tie_informative": bool(mt_max > 0 and len(mt_leaders) < len(tie_mt)),
        "mm_n_leaders": len(mt_leaders),
        "p_true": round(p_true, 4),
        "rank": rank,
        "n_live": len(live_pf),
        "n_pos": sum(1 for p in live_pf.values() if p > _EPS),
        "tie_size": len(tie),
        "tie": tie,
        "exch_size": 1 + len(exch),
        "exch": exch,
        "top3": [(n, round(p, 3)) for n, p in ranked[:3]],
        "p_top": round(ranked[0][1], 4) if ranked else 0.0,
        # ★「1位」＝**同率1位を含む**（辞書順のタイブレークで判定しない）。
        "top_is_true": bool(ranked and ranked[0][1] <= p_true + _EPS),
        # 観測の在庫（belief 自身の関数で数えた値）
        "n_role_reveal": r.get("n_role_reveal"),
        "n_death": r.get("n_death"),
        "fr_excluded": r.get("fr_excluded") or [],
        "true_revealed": (r.get("revealed_roles") or {}).get(vic),
        "n_worlds": r.get("n_worlds"),
    }


# ---------------------------------------------------------------------------
#: (ii) 層の「SK は確定」の感度を見る閾値（B-145 §2b との突き合わせ用）。
_SK_GRID = (_CERTAIN, 0.9, 0.5, 0.45, 0.15)


def run(days: int, loops: int = 8, dump: str | None = None,
        sk_thresh: float = _CERTAIN) -> dict:
    b145_audit.KEEP_MARGINALS = True
    res = b145_audit.run(days=days, loops=loops)
    rows = [r for r in res["rows"] if r["L1"]]
    c = Counter()
    detail: list[dict] = []
    allrows: list[dict] = []
    sens = {t: Counter() for t in _SK_GRID}
    for r in rows:
        st = info_state(r, sk_thresh)
        c[st] += 1
        c[f"{st}_{r['vip_role']}"] += 1
        for t in _SK_GRID:
            sens[t][info_state(r, t)] += 1
        bv = belief_view(r)
        rec = {"script": r["script"], "seed": r["seed"],
               "loop": r["loop"], "day": r["day"],
               "vip": r["vip"], "role": r["vip_role"],
               "sk": r["sk"], "died": r["died"],
               "loop_lost": r["loop_lost"],
               "state": st, "p_sk_killer": r.get("p_sk_killer"),
               "true_friends": r.get("true_friends") or [],
               **bv}
        allrows.append(rec)
        if st == "ii_vip_unknown":
            detail.append(rec)
    if dump:
        with open(dump, "w", encoding="utf-8") as f:
            json.dump({"days": days, "counts": dict(c), "detail": detail,
                       "rows": allrows,
                       "sens": {str(t): dict(v) for t, v in sens.items()}},
                      f, ensure_ascii=False, indent=1)
    return {"days": days, "counts": dict(c), "detail": detail,
            "rows": allrows, "sens": sens, "sk_thresh": sk_thresh,
            "n_L1": len(rows)}


_LABEL = {
    "i3_both": "(iii) 急所もSKも確定＝純粋な盲点（採点側）",
    "i1_sk_unknown": "(ii-b) 急所は確定・SKが未同定",
    "ii_vip_unknown": "★(ii) SKは確定・急所が未同定＝本チケット",
    "i0_none": "(i) どちらも未同定（情報ゼロ席を含む）",
}


def print_split(res: dict) -> None:
    c = res["counts"]
    print(f"== B-147 Phase 1：L1 席（{res['days']}日級）の情報状態＝{res['n_L1']} 席"
          f"（(ii) の SK 確定閾値 pSK≥{res['sk_thresh']}）==")
    for k in ("i3_both", "i1_sk_unknown", "ii_vip_unknown", "i0_none"):
        print(f"  {_LABEL[k]:<44} = {c.get(k, 0):>3}"
              f"（KP {c.get(k + '_キーパーソン', 0)}"
              f"／フレンド {c.get(k + '_フレンド', 0)}）")
    print("  -- (ii) の SK 確定閾値に対する感度（B-145 §2b との突き合わせ）--")
    for t in _SK_GRID:
        s = res["sens"][t]
        print(f"     pSK≥{t:<6} → (iii)={s.get('i3_both', 0):>3}"
              f" (ii-b)={s.get('i1_sk_unknown', 0):>3}"
              f" ★(ii)={s.get('ii_vip_unknown', 0):>3}"
              f" (i)={s.get('i0_none', 0):>3}")
    d = res["detail"]
    if not d:
        return
    print("")
    print(f"-- ★(ii) の {len(d)} 席で belief が持っていた材料 --")
    print("   局 / LD / 急所(役職) / P(真) / 順位 / 同値クラス / 完全交換可能 /"
          " 上位3 / 公開数 / 死者数 / フレンド除外")
    for x in d:
        print(f"   {x['script']}(s{x['seed']}) L{x['loop']}D{x['day']}"
              f" {x['vip']}({x['role']})"
              f" | P={x['p_true']:.3f} 位={x['rank']}/{x['n_live']}"
              f" 同値={x['tie_size']} 交換={x['exch_size']}"
              f" | top3={x['top3']}"
              f" | reveal={x['n_role_reveal']} death={x['n_death']}"
              f" fr_ex={len(x['fr_excluded'])}"
              f" | {'死' if x['died'] else '生'}"
              f"/{'敗' if x['loop_lost'] else '防衛'}", flush=True)
    # ---- 集計（切り分けの核心） ----------------------------------------
    n = len(d)
    n_tie = sum(1 for x in d if x["tie_size"] > 1)
    n_exch = sum(1 for x in d if x["exch_size"] > 1)
    n_top = sum(1 for x in d if x["top_is_true"])
    n_zero_obs = sum(1 for x in d if x["n_role_reveal"] == 0)
    n_p0 = sum(1 for x in d if x["p_true"] <= _EPS)
    print("")
    print(f"  ★切り分け（{n} 席）:")
    print(f"    ・真の急所が **同値クラスに埋もれている**（tie>1）  = {n_tie}"
          f"  ＝この観測集合では**区別が原理的に付かない**")
    print(f"    ・**役職分布ベクトルまで完全一致**する生存者が居る  = {n_exch}")
    print(f"    ・真の急所が **同率1位**だった（＝上に誰も居ない）  = {n_top}")
    print(f"    ・その時点までの **role_reveal が 0 件**            = {n_zero_obs}")
    print(f"    ・P(真)=0（＝その役職から除外済み＝推理の誤り）     = {n_p0}")
    inf = [x for x in d if x["mm_tie_informative"]]
    hit = sum(1 for x in inf if x["mm_leader_is_true"])
    print(f"    ・★同値クラスを**相手の札の宛先**で割れる席          = {len(inf)}"
          f"（うち最多打点が真の急所 = {hit}／外れ = {len(inf) - hit}）")
    scripts = sorted({(x["script"], x["seed"]) for x in d})
    print(f"    ・独立な局 = {len({s for s, _ in scripts})} 脚本 / {len(scripts)} 局"
          f"（seed 複製に注意＝B-145 §2a）")


# ---------------------------------------------------------------------------
# 観測チャネルの棚卸し（コードの現物）
# ---------------------------------------------------------------------------
_CHANNELS = """
== フレンド確率を動かす観測チャネル（agents/belief.py の現物）==

[+] 直接確定（P→1.0）
  1. `role_reveal`（role="フレンド"）
     - 収集 = `_revealed_roles`（belief.py:273）
     - 適用 = `_recompute` の `revealed` → fixed（belief.py:1878 近傍）
     - 発生源 = 巫女／サラリーマン等の役職公開、および**フレンド死亡時の【強制】公開**
       （rules/40_first_steps.md:130／rules/50_basic_tragedy_x.md:146）

[-] 否定形（P→0.0）
  2. 「評価が行われた（loop_board あり）のに フレンド公開が無かった」ループの死者
     - 収集 = `_death_role_exclusions`（belief.py:796・返り値2番目 `fr_ex`）
     - 適用 = `_recompute` の `friend_excluded`（belief.py:1340-1344）→ `fr_step`（:1339）
     - 根拠 = 60:A17（主人公死亡終了でも公開される）
     - 除外の除外 = 蘇生（revive）・既公開フレンド（belief.py:831）
  3. 他役職の確定による**押し出し**（あるキャラが SK/KP/ML 等に確定すると
     フレンドの残スロットが他へ回る）＝`_combo_weight_full` の数え上げ（belief.py:1275）

[=] 事前分布（スロット数）の変化
  4. ルール組の枝刈り（`_rule_y_eliminations`・`_mm_phase_signals`・`rule_reveal` 等）で
     **フレンドのスロット数を持つルールXが生き残るか**が変わる
     （BTX＝友情サークル(2人)／潜む殺人鬼(1人)、FS＝最低の却本）
     - `_combos`（belief.py:87）が slots を作り、`_combo_pi`（:1810）で重み付け

[ソフト重み] `agents/soft_evidence.py`
  5. 現在**登録されている証拠は `MisleaderUnrestPresence` の1つだけ**
     （role="ミスリーダー" のときだけ効く＝**フレンドには 1.0 で無風**）

★KB 上の帰結（rules/40:126-133・rules/50:143-148）：
  フレンドは **条文能力なし**・追加能力は2つとも
  「**ループ終了時に死亡している**」「**役職が公開されたことがある**」を条件とする。
  ∴ **生存していて未公開のフレンドは、公開情報に痕跡を1つも残さない。**
  上の 1〜4 のうち、生存中の未公開フレンドに効くのは **2・3・4（＝他者の情報による消去法）だけ**
  であって、**本人を名指しする観測は存在しない**。
"""


# ---------------------------------------------------------------------------
# 1局の明細＝「同値クラスの面々について、公開履歴に何が書いてあったか」
# ---------------------------------------------------------------------------
#: 名前が出るだけで「その人を名指しする観測」とは言えないイベント（位置・盤面の記述）。
_POSITIONAL = ("cards_revealed", "move", "unrest", "goodwill", "anyaku",
               "incident", "phase", "day_start", "loop_start")


def case(script_name: str, seed: int, loop: int, day: int, days: int,
         loops: int = 8) -> int:
    """指定席で、真の急所と同値クラスの面々について**公開履歴の中身**を突き合わせる。

    ★「観測が無い」を主張するには、**在ったのに使っていない**の可能性を潰す必要がある。
    そこで公開履歴を名前で引き、**どのイベント種別に何回出たか**を並べる。
    """
    from dataclasses import replace

    from agents import HeuristicMastermind
    from arena.benchmark import benchmark_scripts
    from sim import run_game

    target = None
    for name, sd, sc in benchmark_scripts(days=days):
        if name == script_name and sd == seed:
            target = sc
            break
    if target is None:
        print(f"該当なし: {script_name} s{seed}（{days}日級）")
        return 1
    b145_audit.KEEP_MARGINALS = True
    hp = b145_audit._Probe(seed)
    state, _ = run_game(replace(target, loops=loops),
                        {"mastermind": HeuristicMastermind(seed),
                         "p1": hp, "p2": hp, "p3": hp})
    seat = next((s for s in hp.seats
                 if s["loop"] == loop and s["day"] == day), None)
    if seat is None:
        print(f"該当席なし: L{loop}D{day}")
        return 1
    truth = {n: state.script.role_of(n) for n in state.characters}
    friends = sorted(n for n, r in truth.items() if r == "フレンド")
    pf = seat["p_friend"]
    print(f"== {script_name}(s{seed}) {days}日級 L{loop}D{day} ==")
    print(f"  真のフレンド = {friends}")
    print(f"  P(フレンド) = "
          f"{sorted(((n, p) for n, p in pf.items() if p > 0), key=lambda x: -x[1])}")
    top = max(pf.values()) if pf else 0.0
    tie = sorted(n for n, p in pf.items() if abs(p - top) <= 1e-6)
    print(f"  同率トップの同値クラス = {tie}")
    # 公開履歴の prefix（その席の時点まで）
    hist = [e for e in state.history
            if (e.get("loop"), e.get("day")) < (loop, day)]
    print(f"  公開履歴 prefix = {len(hist)} 件"
          f"（role_reveal {sum(1 for e in hist if e.get('event') == 'role_reveal')} 件"
          f"／death {sum(1 for e in hist if e.get('event') == 'death')} 件）")
    print("  -- 同値クラスの面々が公開履歴に出る回数（イベント種別ごと）--")
    for n in tie:
        cnt: Counter = Counter()
        for e in hist:
            blob = json.dumps(e, ensure_ascii=False)
            if n in blob:
                cnt[e.get("event")] += 1
        print(f"    {n}（真={truth.get(n)}）: {dict(sorted(cnt.items()))}")
    # ★belief が**使っていない**唯一の実在チャネル＝相手の札の宛先（opponent-model）。
    #   `cards_revealed` は主人公の札も含むので **owner="mastermind" だけ**を数える。
    print("  -- ★相手が札を置いた回数（belief は使っていない＝opponent-model の材料）--")
    for n in tie:
        k = sum(1 for e in hist if e.get("event") == "cards_revealed"
                for p in (e.get("placements") or [])
                if p.get("owner") == "mastermind" and p.get("target") == n)
        print(f"    {n}（真={truth.get(n)}）: mm の札 {k} 回")
    print("  -- 非位置イベント（役職を名指ししうる観測）だけを抜き出す --")
    hit = 0
    for e in hist:
        if e.get("event") in _POSITIONAL:
            continue
        blob = json.dumps(e, ensure_ascii=False)
        if any(n in blob for n in tie):
            hit += 1
            print(f"    {e}")
    if not hit:
        print("    ★該当なし＝同値クラスの誰も、位置以外の観測に一度も現れていない。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="split",
                    choices=["split", "channels", "case"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument("--script", default="btx_seal")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--loop", type=int, default=2)
    ap.add_argument("--day", type=int, default=2)
    ap.add_argument("--sk-thresh", type=float, default=_CERTAIN,
                    help="(ii) 層で「SK は確定」とみなす pSK の下限")
    a = ap.parse_args(argv)
    if a.cmd == "channels":
        print(_CHANNELS)
        return 0
    if a.cmd == "case":
        return case(a.script, a.seed, a.loop, a.day, a.days, a.loops)
    print(f"[切替口] B141B_UNLOCK_SAME_DAY="
          f"{HeuristicProtagonist.B141B_UNLOCK_SAME_DAY}"
          f" / B143_YIELD={HeuristicProtagonist.B143_YIELD}"
          f" / B142_RESERVE={HeuristicProtagonist.B142_RESERVE}"
          f" / B145_EVADE_MAX_FRIENDS="
          f"{HeuristicProtagonist.B145_EVADE_MAX_FRIENDS}"
          f" / B146_ODB_TIEBREAK_BOARD_LOSS_ONLY="
          f"{getattr(HeuristicProtagonist, 'B146_ODB_TIEBREAK_BOARD_LOSS_ONLY', None)}"
          f" / days={a.days} loops={a.loops}", flush=True)
    print_split(run(days=a.days, loops=a.loops, dump=a.json,
                    sk_thresh=a.sk_thresh))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
