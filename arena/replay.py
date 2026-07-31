"""ログ再生（計画 §3.4）— APIを叩かず、記録済みの選択でゲームを再実行する。

- ReplayAgent：記録された chosen をそのまま返すだけのエージェント。
  flow は決定的なので、同じ脚本＋同じ選択列＝同じゲームが正確に再現される。
- verify_replay：再実行の最終状態が保存された final_state と一致するか検証
  ＝ログの完全性チェック（シミュレータの非決定性・ログ欠落があればここで発覚）。
- board_json_for_decision：決定1件の view を board_viz.board_html_from_json が
  受け取れる形に変換（ビューアの盤面描画用の純関数）。
"""

from __future__ import annotations

from collections import defaultdict

from sim import run_game
from sim.state import GameState, Script


class ReplayAgent:
    """記録済みの選択列を順に返す。options との照合は flow 側で行われる。"""

    def __init__(self, chosen_seq: list[dict]):
        self._seq = list(chosen_seq)
        self._i = 0

    def decide(self, view: dict, decision: str, options: list[dict]) -> dict:
        if self._i >= len(self._seq):
            raise RuntimeError("リプレイの選択列が尽きた（ログ欠落かflowの非決定性）")
        chosen = self._seq[self._i]
        self._i += 1
        return chosen


def replay_game(script: Script, decisions: list[dict]) -> tuple[GameState, list[dict]]:
    """記録済み決定でゲームを再実行（LLM再呼び出しなし）。"""
    seqs: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        seqs[d["actor"]].append(d["chosen"])
    agents = {a: ReplayAgent(seqs.get(a, [])) for a in ("mastermind", "p1", "p2", "p3")}
    return run_game(script, agents)


def verify_replay(script: Script, meta: dict, decisions: list[dict]) -> bool:
    """再実行が保存済みの最終状態・履歴と完全一致するか。不一致は AssertionError。"""
    state, log = replay_game(script, decisions)
    assert state.to_dict() == meta["final_state"], "最終状態が一致しない"
    assert state.history == meta["history"], "公開履歴が一致しない"
    assert len(log) == meta["n_decisions"], "決定数が一致しない"
    return True


# ---------------------------------------------------------------------------
# ビューア用：決定1件 → 盤面JSON（board_viz.board_html_from_json 互換）
# ---------------------------------------------------------------------------

def board_json_for_decision(entry: dict, script: Script | None = None,
                            day_decisions: list[dict] | None = None,
                            omniscient: bool = False) -> dict:
    """決定エントリの view を盤面描画用JSONへ。

    omniscient=True（神視点）：script から全配役を、同ターンの set_card 決定から
    裏向き札の中身を補完して表示する。False（その actor の視点）は view のまま
    ＝公開情報のみ（revealed_role があれば役職として表示）。
    """
    view = entry["view"]
    chars = []
    for c in view["characters"]:
        c = dict(c)
        if omniscient and script is not None:
            c["role"] = script.role_of(c["name"])
        else:
            c["role"] = c.get("revealed_role")
        chars.append(c)

    placements = [dict(p) for p in view.get("placements", [])]
    if omniscient and day_decisions:
        # 同ターンの set_card 決定（actor＝owner）から裏向き札の中身を復元
        cards: dict[tuple[str, str], str] = {}
        for d in day_decisions:
            if d["decision"] == "set_card":
                cards[(d["actor"], d["chosen"]["target"])] = d["chosen"]["card"]
        for p in placements:
            p.setdefault("card", cards.get((p["owner"], p["target"]), "？"))

    return {
        "characters": chars,
        "placements": placements,
        "board_anyaku": view.get("board_anyaku", {}),
    }


def board_json_from_view(view: dict) -> dict:
    """protagonist/mastermind view → board_viz 用のJSON（視点はviewが既にマスク済み）。

    役職：mastermind view は view["roles"]（全配役）を持つ＝脚本家は配役を知る側なので
    盤面にも括弧書きで出す（AIC要望#2・2026-07-09。board_viz._char_label が role を
    括弧書き表示＝口は既存）。protagonist view に roles キーは無い＝従来どおり
    revealed_role（公開済み）だけ＝主人公モードの表示は不変。
    裏向き札は view["placements"] のまま（自分の札は中身、他人の札は伏せ）。
    """
    roles = view.get("roles") or {}
    chars = []
    for c in view["characters"]:
        c = dict(c)
        c["role"] = roles.get(c.get("name")) or c.get("revealed_role")
        chars.append(c)
    placements = [dict(p) for p in view.get("placements", [])]
    return {"characters": chars, "placements": placements,
            "board_anyaku": view.get("board_anyaku", {})}


def board_json_from_snapshot(snap: dict, omniscient: bool = False) -> dict:
    """フェイズ境界スナップショット（神視点）→ board_viz 用のJSON。

    omniscient=True：全配役＋裏向き札の中身を表示。
    False（主人公視点）：役職は公開済みのみ、裏向き札は中身を伏せる（？）。
    """
    revealed = snap.get("revealed_roles", {})
    chars = []
    for name, c in snap["characters"].items():
        chars.append({
            "name": name, "area": c["area"], "alive": c["alive"],
            "unrest": c["unrest"], "goodwill": c["goodwill"], "anyaku": c["anyaku"],
            "role": c["role"] if omniscient else revealed.get(name),
        })
    # ★reveal_cards＝行動解決中（6枚全公開）の時点は、主人公視点でも札の中身を表向きで見せる。
    show_cards = omniscient or snap.get("reveal_cards", False)
    placements = []
    for p in snap.get("turn_placements", []):
        d = {
            "owner": p["owner"], "target": p["target"], "target_kind": p["target_kind"],
            "card": p["card"] if show_cards else "？",
        }
        # ★B-100（2026-07-29）：provenance（その札がどの経路で決まったか）は**神視点のみ**運ぶ。
        #   AI脚本家の view（sim/views._masked_placements）には載せない＝情報の非対称を壊さない。
        #   表示側は board_html_from_json(dev=True) のときだけ読む（開発モード限定）。
        if omniscient and p.get("prov"):
            d["prov"] = p["prov"]
        placements.append(d)
    return {"characters": chars, "placements": placements,
            "board_anyaku": snap["board_anyaku"]}


_CTR_JP = {"anyaku": "暗躍", "unrest": "不安", "goodwill": "友好", "guard": "護衛"}


def describe_event(e: dict, secret: bool = False) -> str:
    """公開イベント/神視点ログの1件を読める日本語1行に整形する（生JSONの代替）。"""
    ev = e.get("event", "")
    if ev in _CTR_JP:
        d = e.get("delta", 0)
        sign = f"+{d}" if d > 0 else str(d)
        return f'{e.get("target")} に {_CTR_JP[ev]}{sign}'
    if ev == "cards_revealed":
        parts = []
        for p in e.get("placements", []):
            who = "脚" if p.get("owner") == "mastermind" else p.get("owner")
            parts.append(f'{who}:{p.get("card")}→{p.get("target")}')
        return "全カード公開： " + " ／ ".join(parts)
    if ev == "death":
        s = f'〈{e.get("name")}〉が死亡'
        return s + (f'（{e.get("cause")}）' if secret and e.get("cause") else "")
    if ev == "death_prevented":
        return f'〈{e.get("name")}〉は死亡しなかった（不死。{e.get("cause", "")}）'
    if ev == "guard_consumed":
        return f'〈{e.get("name")}〉の護衛カウンターが身代わりになった' \
            + (f'（{e.get("cause")}）' if secret and e.get("cause") else "")
    if ev == "protagonist_death":
        return "主人公が死亡" + (f'（{e.get("cause")}）' if secret and e.get("cause") else "")
    if ev == "protagonist_death_prevented":
        return f'主人公は死亡しなかった（軍人の不死。{e.get("cause", "")}）'
    if ev == "protagonist_immortal":
        return "このループ中、主人公は死亡しなくなった（軍人）"
    if ev == "revive":
        return f'〈{e.get("name")}〉が蘇生した'
    if ev == "move":
        return f'〈{e.get("name")}〉が {e.get("to")} へ移動'
    if ev == "move_blocked":
        area = e.get("area")
        where = f'禁止エリア〈{area}〉へ' if area else '禁止エリアへ'
        return f'〈{e.get("name")}〉は{where}移動できず不成立（その場に留まった）'
    if ev == "entry":
        return f'〈{e.get("name")}〉が {e.get("area")} に登場'
    if ev == "incident":
        return f'事件『{e.get("name")}』は' + ("発生した" if e.get("occurs") else "発生しなかった") \
            + (f'（犯人:{e.get("culprit")}）' if secret and e.get("culprit") else "")
    if ev == "incident_effect":
        return f'事件『{e.get("name")}』：{e.get("note", "")}'
    if ev == "role_reveal":
        return f'〈{e.get("name")}〉の役職が公開された：{e.get("role")}'
    if ev == "culprit_reveal":
        return f'{e.get("day")}日目の事件の犯人が判明：〈{e.get("name")}〉'
    if ev == "rule_reveal":
        return f'ルールXの一つが判明：{e.get("rule_x")}'
    if ev == "goodwill_used":
        tgt = f' → {e.get("target")}' if e.get("target") else ""
        return f'友好能力を宣言：〈{e.get("character")}〉の『{e.get("ability")}』{tgt}'
    if ev == "goodwill_refused":
        return f'　└ ✗ 脚本家が拒否した（〈{e.get("character")}〉は友好無視を持つ）'
    if ev == "goodwill_resolved":
        return f'　└ ✓ 解決（脚本家は拒否せず・効果が適用された）'
    if ev == "goshinboku_move":
        # ご神木の特性（主人公能力フェイズ・ハート不要・拒否対象外）：カウンターを同エリアへ移す。
        # A-6②：フォールバック（goshinboku_move）で生表示されていたのを標準形式に乗せる。
        return (f'〈ご神木〉の特性：{e.get("counter")}カウンターを '
                f'{e.get("from", "ご神木")} → {e.get("to")}（同エリア内）へ移した')
    if ev == "card_returned":
        return f'{e.get("owner")} の『{e.get("card")}』が手札に戻った（委員長）'
    if ev == "loop_start":
        return "── ループ開始 ──"
    if ev == "loop_end":
        # ★ループ終了「効果」＝ターン内で即ループを終わらせる処理（キーパーソン死亡・主人公死亡・
        #   タイムトラベラーの敗北宣言）。ループ終了フェイズの敗北条件判定とは別タイミング。
        #   どのフェイズで起きたかを明記する（例：ターン終了フェイズで死者なし＝TTの敗北宣言と分かる）。
        _timing = {"turn_end": "ターン終了フェイズ", "incident": "事件フェイズ",
                   "action_resolution": "行動解決フェイズ",
                   "mastermind_ability": "脚本家能力フェイズ",
                   "goodwill_ability": "主人公能力フェイズ"}.get(e.get("phase"))
        where = f"{_timing}で" if _timing else ""
        return f'⏹ {where}ループが即終了（ループ終了効果）：{e.get("reason", "").replace("（ループ終了効果）", "")}'
    if ev == "loop_board":
        ba = e.get("board_anyaku", {})
        body = " ".join(f'{a}:{n}' for a, n in ba.items() if n)
        return f'ループ終了時のボード暗躍： {body or "なし"}'
    if ev == "loop_result":
        # ★ループ終了フェイズの敗北条件判定（盤面暗躍≥2 等）。ターン内の即終了効果とは別。
        return f'🏁 ループ終了フェイズの判定：{e.get("result", "")}'
    if ev == "game_over":
        w = {"protagonist": "主人公", "mastermind": "脚本家"}.get(e.get("winner"), "?")
        return f'ゲーム終了：{w}の勝利'
    if ev == "final_battle":
        mark = "正解 ✓" if e.get("correct") else "誤答 ✗"
        return f'最後の戦い：〈{e.get("name")}〉＝{e.get("guess")} と宣言 → {mark}'
    if ev == "mm_ability":  # 神視点のみ
        return f'脚本家能力を使用：{describe_choice(e.get("choice", {}))}'
    if ev == "defeat":  # 神視点のみ
        return f'敗北条件成立：{e.get("reason", "")}'
    if ev == "turn_end_pairs":
        # ターン終了フェイズ直後、同エリアに2人きりで生き残った顔ぶれ（公開＝SK推理の材料）
        pairs = e.get("pairs", [])
        body = " ／ ".join("・".join(pr) for pr in pairs) or "なし"
        return f'ターン終了時、2人きりで生き残った組：{body}'
    if ev == "ai_incident_effect":
        return f'A.I.が事件『{e.get("name", "")}』の効果を解決した'
    if ev == "incident_suppressed":
        return f'事件『{e.get("name", "")}』は不発になった（手先の能力）'
    if ev == "forbidden_lifted":
        return f'〈{e.get("name")}〉はこのループ、禁止エリアが解除された（医者）'
    if ev == "vanish":
        return f'〈{e.get("name")}〉がこのループのゲームから取り除かれた（幻想）'
    # ★フォールバックも生JSONではなく最低限読める形に（イベント名＋主対象）
    _who = e.get("name") or e.get("target") or e.get("character") or ""
    return f'（{ev}）{_who}'.rstrip()


def describe_choice(chosen: dict) -> str:
    """決定dictを『カード→対象』のような短い日本語1行に整形（ビューアの一覧表示用）。

    情報量の乏しい生JSON（card/target等のキー名）ではなく、値だけを読める形にする。
    """
    if chosen.get("action") == "pass":
        return "パス"
    if "refuse" in chosen:                                 # 友好能力の拒否/解決（脚本家）
        who, ab = chosen.get("character", ""), chosen.get("ability", "")
        tgt = chosen.get("target", "")
        verb = "🚫 拒否する" if chosen["refuse"] else "✅ 通す（解決させる）"
        tail = f"（→{tgt}）" if tgt else ""
        return f"{verb}：〈{who}〉の『{ab}』{tail}"
    if "mode" in chosen:                                    # 医者：不安の除去/付与の選択
        return {"remove": "不安を1つ除去", "add": "不安を1つ付与"}.get(chosen["mode"],
                                                                     str(chosen["mode"]))
    if chosen.get("kind") == "goshinboku" or "goshinboku" in chosen:  # ご神木のカウンター移動（脚本家能力/主人公能力 共通）
        # 主人公側＝{"goshinboku":attr,...}／脚本家側＝{"action":"ご神木:move","kind":"goshinboku","counter":attr,...}。
        # ★学者特性（{"counter":日本語}）より先に判定：ご神木のmm手は counter も持つため誤吸収を防ぐ。
        _attr = chosen.get("goshinboku") or chosen.get("counter")
        _cj = {"anyaku": "暗躍", "unrest": "不安", "goodwill": "友好"}.get(_attr, _attr)
        _tgt = chosen.get("target", "")
        return f'〈ご神木〉『{_cj}カウンターを同エリアへ移す』' + (f' → {_tgt}' if _tgt else "")
    if "counter" in chosen:                                 # 学者特性のカウンター選択
        return f'学者特性: {chosen["counter"]}カウンター'
    if "card" in chosen and "target" in chosen:            # set_card
        return f'{chosen["card"]} → {chosen["target"]}'
    if "ability" in chosen:                                 # 友好能力（誰の・何を・どこへ）
        return f'〈{chosen["character"]}〉の『{chosen["ability"]}』 → {chosen["target"]}'
    if "role" in chosen and "character" in chosen:          # 最後の戦いの役職宣言
        return f'〈{chosen["character"]}〉＝{chosen["role"]} と宣言'
    if chosen.get("action") == "cultist_ignore":            # カルティストの暗躍禁止無視（任意発動）
        v = "暗躍禁止を無視（暗躍を通す）" if chosen.get("ignore") else "無視しない（暗躍を打ち消す）"
        return f'🕵 {v} → {chosen.get("target", "")}'
    if "action" in chosen:                                  # 脚本家能力・ターン終了能力
        act = chosen["action"]
        if "target" in chosen:
            kind = {"anyaku": "暗躍+1", "unrest": "不安+1"}.get(chosen.get("kind"), "")
            return f'{act}（{kind + " → " if kind else "→ "}{chosen["target"]}）'
        return str(act)
    if "target" in chosen:                                  # 事件・移動先など
        return f'対象: {chosen["target"]}'
    if "name" in chosen and "area" in chosen:               # loop_start_area
        return f'{chosen["name"]} を {chosen["area"]} に配置'
    return str(chosen)
