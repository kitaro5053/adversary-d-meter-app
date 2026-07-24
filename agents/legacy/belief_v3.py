"""主人公の可能世界追跡（M5）— 公開情報だけから配役・ルール・犯人を絞り込む。

**LLMに生の推理をさせず、決定的コードで可能世界を絞る**（本体の「LLMは判断・計算は決定的」思想）。
出力の marginals / candidates を主人公AI（ヒューリスティック/LLM）や最後の戦いの判断材料にする。

★公開情報のみが入力（配役・犯人・ルールを覗かない）。

## スケーラブルな実装（FS/BTX共通）
可能世界＝(ルール, 配役)。FSは9ルール組で小さいが、BTXは 5ルールY × C(7,2)=21ルールX組 で、
配役まで全列挙すると数百万規模になる。そこで**配役は列挙せず、組合せ論で周辺確率を直接計算**する:
- **ルール組をまず枝刈り**（観測でルール組を絞る）。役職が1つ判明するだけでルール組は激減する。
- 各ルール組の中は、未確定キャラが対称なので「あるキャラが役職R」の確率＝残スロット数/残キャラ数。
  ルール組の重み＝配役の総数（多項係数）。僕と契約（キーパーソン=少女）だけ非対称で補正。
- FSのマイナス0〜2はルール組を3分割して吸収。

観測フィルタ:
- role_reveal（フレンド公開・サラリーマン等の開示）→ 役職確定。
- キーパーソン死亡（死亡直後にループ終了効果）→ その死者はキーパーソン。
- rule_reveal（情報屋のルールX開示）→ rule_x 確定。
- 敗北条件（守るべき場所＝学校暗躍<2の盤面敗北）→ ルールY消去。
- 犯人候補：発生回で先に死んだ者は除外／犯人開示で確定。
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from itertools import combinations
from math import factorial

from engine.data import ROLE_CLAUSE_ABILITY, is_shoujo

# 友好無視／絶対友好無視を持つ役職（拒否できるのはこの役職だけ＝拒否＝この役職の証拠）
_IGNORE_ROLES = frozenset(r for r, c in ROLE_CLAUSE_ABILITY.items()
                          if c in ("友好無視", "絶対友好無視"))
from sim.state import (
    BTX_RULE_X_ROLES,
    BTX_RULE_Y_ROLES,
    DEFAULT_ROLE,
    FS_RULE_X_ROLES,
    FS_RULE_Y_ROLES,
    ROLE_MAX,
)

_SHOUJO_RULE = "僕と契約しようよ！"  # キーパーソン＝少女限定（50:42）


# ---------------------------------------------------------------------------
# ルール組の生成（配役は列挙しない。各組は固定スロットを持つ）
# ---------------------------------------------------------------------------

def _capped_slots(slots: Counter) -> Counter:
    return Counter({r: min(n, ROLE_MAX.get(r, n)) for r, n in slots.items()})


@lru_cache(maxsize=8)
def _combos(set_name: str) -> tuple[dict, ...]:
    """ルール組の一覧。各組: {rule_y, rule_xs(tuple), slots(dict), kp_shoujo(bool)}。"""
    out: list[dict] = []
    if set_name == "FS":
        for ry, yroles in FS_RULE_Y_ROLES.items():
            for rx, xroles in FS_RULE_X_ROLES.items():
                base = Counter(yroles) + Counter(xroles)
                minus_max = base.get("マイナス", 0)
                for m in range(minus_max + 1):  # マイナスは0〜スロット数で可変
                    slots = Counter(base)
                    if minus_max:
                        slots["マイナス"] = m
                        if m == 0:
                            del slots["マイナス"]
                    out.append({"rule_y": ry, "rule_xs": (rx,),
                                "slots": dict(_capped_slots(slots)), "kp_shoujo": False})
    elif set_name == "BTX":
        for ry, yroles in BTX_RULE_Y_ROLES.items():
            for rx1, rx2 in combinations(BTX_RULE_X_ROLES, 2):  # ルールXは異なる2つ
                slots = Counter(yroles) + Counter(BTX_RULE_X_ROLES[rx1]) \
                    + Counter(BTX_RULE_X_ROLES[rx2])
                out.append({"rule_y": ry, "rule_xs": tuple(sorted((rx1, rx2))),
                            "slots": dict(_capped_slots(slots)),
                            "kp_shoujo": ry == _SHOUJO_RULE})
    else:
        raise ValueError(f"未対応セット: {set_name}")
    return tuple(out)


def _multinomial(counts: list[int]) -> int:
    total = sum(counts)
    r = factorial(total)
    for c in counts:
        r //= factorial(c)
    return r


def _combo_weight_and_marginals(cast: list[str], slots: dict, fixed: dict,
                                kp_shoujo: bool) -> tuple[int, dict]:
    """1ルール組の配役総数（重み）と、各キャラの役職周辺分布（重み内カウント）を返す。

    未確定キャラは対称＝役職Rの割合＝残スロット/残キャラ。僕と契約のキーパーソンだけ少女限定で補正。
    返り値 marginals[char][role] = そのキャラがroleである配役数（weightで割ると確率）。
    """
    n = len(cast)
    n_person = n - sum(slots.values())
    if n_person < 0:
        return 0, {}
    rem = Counter(slots)
    used_person = 0
    for ch, r in fixed.items():
        if r == DEFAULT_ROLE:
            used_person += 1
        else:
            rem[r] -= 1
    rem_person = n_person - used_person
    if any(v < 0 for v in rem.values()) or rem_person < 0:
        return 0, {}
    # 僕と契約：確定キーパーソンは少女でなければ不成立
    if kp_shoujo:
        for ch, r in fixed.items():
            if r == "キーパーソン" and not is_shoujo(ch):
                return 0, {}
    remaining = [c for c in cast if c not in fixed]
    m = len(remaining)
    rem_roles = {r: c for r, c in rem.items() if c > 0}

    # 少女限定キーパーソンがある場合の重み・分布
    kp_slots = rem_roles.get("キーパーソン", 0)
    if kp_shoujo and kp_slots > 0:
        shoujo = [c for c in remaining if is_shoujo(c)]
        if len(shoujo) < kp_slots:
            return 0, {}
        # キーパーソンを少女から kp_slots 人選ぶ→残りを自由配役
        other_counts = [c for r, c in rem_roles.items() if r != "キーパーソン"] + [rem_person]
        # 残り(m - kp_slots)人を other_counts に配る
        from math import comb
        ways_kp = comb(len(shoujo), kp_slots)
        weight = ways_kp * _multinomial(other_counts)
        marg = _marginals_shoujo_kp(remaining, shoujo, rem_roles, rem_person, kp_slots)
    else:
        counts = list(rem_roles.values()) + [rem_person]
        weight = _multinomial(counts)
        marg = {}
        for c in remaining:
            d = {r: (cnt / m) * weight for r, cnt in rem_roles.items()}
            if rem_person:
                d[DEFAULT_ROLE] = (rem_person / m) * weight
            marg[c] = d
    # 確定キャラはその役職で確定（weight全て）
    for ch, r in fixed.items():
        marg[ch] = {r: weight}
    return weight, marg


def _marginals_shoujo_kp(remaining, shoujo, rem_roles, rem_person, kp_slots):
    """僕と契約：キーパーソン少女限定での各キャラ役職カウント（weight正規化前）。"""
    from math import comb
    m = len(remaining)
    n_shoujo = len(shoujo)
    other_roles = {r: c for r, c in rem_roles.items() if r != "キーパーソン"}
    other_counts = list(other_roles.values()) + [rem_person]
    mult_all = _multinomial(other_counts)          # 残りを配る総数（キーパーソン確定後）
    ways_kp = comb(n_shoujo, kp_slots)
    marg = {}
    for c in remaining:
        d: dict = {}
        is_s = c in shoujo
        # キーパーソン割当数：少女なら C(n_shoujo-1, kp_slots-1)*mult_all、非少女は0
        if is_s:
            d["キーパーソン"] = comb(n_shoujo - 1, kp_slots - 1) * mult_all
        # 他の役職R：キーパーソンでない配役の中でのRの割合。cがキーパーソンでない場合に限る。
        #   cがキーパーソンでない確率で、残り(m-1)人と(m-kp_slots)スロットの対称性から R割合。
        not_kp_weight = (ways_kp - (comb(n_shoujo - 1, kp_slots - 1) if is_s else 0)) * mult_all
        rest = m - kp_slots  # キーパーソン以外に配られる人数
        for r, cnt in other_roles.items():
            d[r] = (cnt / rest) * not_kp_weight if rest else 0
        if rem_person and rest:
            d[DEFAULT_ROLE] = (rem_person / rest) * not_kp_weight
        marg[c] = d
    return marg


# ---------------------------------------------------------------------------
# 観測（公開イベント履歴）の解釈
# ---------------------------------------------------------------------------

def _keyperson_deaths(history: list[dict]) -> set[str]:
    """1死＋同日のループ終了効果＝その死者はキーパーソン【の候補】。

    ★タイムトラベラーの任意敗北も同じ「ループ終了効果」文言で公開される（役職は伏せる）ため、
    TT入りのルール組では『死者=KP』か『TTの宣言』かは区別できない＝この推定は
    TTの居ない組にだけ適用する（_recompute側で組ごとに分岐。全組に固定すると
    SKの殺しとTT宣言が同日に重なったとき真の世界を消す実測バグがあった）。
    """
    deaths_by_dl: dict[tuple, list[str]] = {}
    effect_end_dl: set[tuple] = set()
    for e in history:
        dl = (e.get("loop"), e.get("day"))
        if e.get("event") == "death":
            deaths_by_dl.setdefault(dl, []).append(e["name"])
        elif e.get("event") == "loop_end" and "ループ終了効果" in str(e.get("reason", "")):
            effect_end_dl.add(dl)
    out: set[str] = set()
    for dl in effect_end_dl:
        names = deaths_by_dl.get(dl, [])
        if len(names) == 1:
            out.add(names[0])
    return out


def _revealed_roles(history: list[dict]) -> dict[str, str]:
    return {e["name"]: e["role"] for e in history if e.get("event") == "role_reveal"}


def _revealed_rule_x(history: list[dict]) -> str | None:
    for e in history:
        if e.get("event") == "rule_reveal" and e.get("rule_x"):
            return e["rule_x"]
    return None


def _death_day(history: list[dict]) -> dict[tuple, int]:
    first: dict[tuple, int] = {}
    for e in history:
        if e.get("event") == "death":
            k = (e.get("loop"), e["name"])
            d = e.get("day", 0)
            if k not in first or d < first[k]:
                first[k] = d
    return first


def _refused_chars(history: list[dict]) -> set[str]:
    """友好能力を拒否されたキャラ＝役職が友好無視/絶対友好無視を持つ（強い制約。空撃ちテクの根拠）。"""
    return {e["character"] for e in history if e.get("event") == "goodwill_refused"}


def _combo_weight_with_refusals(cast, slots, fixed, kp_shoujo, refused):
    """拒否されたキャラを『友好無視系の役職のどれか』に割り当てる場合分けで厳密に数える。

    counting方式は任意の部分集合制約を直接扱えないため、拒否キャラ（少数）×友好無視役職
    （少数）の割当を列挙し、各割当を fixed に固定して重みを合算する（スロット超過は0で自然に落ちる）。
    """
    todo = [c for c in refused if c not in fixed]
    if not todo:
        return _combo_weight_and_marginals(cast, slots, fixed, kp_shoujo)
    ignore_roles = [r for r in slots if r in _IGNORE_ROLES]
    if not ignore_roles:
        return 0, {}  # 友好無視役職が居ないルール組で拒否は起きない＝矛盾
    total_w = 0
    agg: dict = {}
    def _rec(i: int, fx: dict) -> None:
        nonlocal total_w
        if i == len(todo):
            w, m = _combo_weight_and_marginals(cast, slots, fx, kp_shoujo)
            if w > 0:
                total_w += w
                for ch, d in m.items():
                    ad = agg.setdefault(ch, {})
                    for r, cnt in d.items():
                        ad[r] = ad.get(r, 0) + cnt
            return
        for r in ignore_roles:
            _rec(i + 1, {**fx, todo[i]: r})
    _rec(0, dict(fixed))
    return total_w, agg


def _public_role_constraints(history: list[dict]) -> tuple[set | None, set | None, list[set]]:
    """脚本家能力フェイズの発動位置（present＝その瞬間そのエリアに居た顔ぶれ・公開）から
    役職の候補者集合を絞る。

    - キャラへの暗躍+1 → クロマクは present の中に居る（唯一の供給源・同エリア要求）。
    - ボードへの暗躍+1 → クロマク∈present か 不穏な噂（ルール組ごとに判断＝board_setsで返す）。
    - 不安+1 → ミスリーダー（/ファクター）∈present。★医者が present に居る場合は
      医者の友好能力（友好無視＋友好2）の可能性があるため曖昧＝この事象は使わない（健全側）。
    返り値: (クロマク候補∩, ミスリーダー候補∩, ボード事象のpresent集合リスト)。
    """
    from engine.board import AREAS
    kuro: set | None = None
    ml: set | None = None
    board_sets: list[set] = []
    for e in history:
        if e.get("phase") != "mastermind_ability" or e.get("delta", 0) <= 0:
            continue
        present = e.get("present")
        if not present:  # 旧ログ（present無し）は使わない＝健全
            continue
        s = set(present)
        if e.get("event") == "anyaku":
            if e.get("target") in AREAS:
                board_sets.append(s)
            else:
                kuro = s if kuro is None else (kuro & s)
        elif e.get("event") == "unrest":
            if "医者" in s:  # 医者のmm使用の可能性＝曖昧
                continue
            ml = s if ml is None else (ml & s)
    return kuro, ml, board_sets


def _clean_defeat_constraints(history: list[dict]) -> tuple[list[set], bool]:
    """評価敗北（ループ終了時判定で敗北）ごとの「ありうるルールY」集合と、
    「死者なしのループ終了効果」＝タイムトラベラー必在フラグ。

    敗北には必ず原因がある。ループ終了時の評価で成立しうる敗北は
    (a) ルールYの盤面条件 (b) フレンド死亡 の2系統のみで、(b)は【強制】役職公開を伴う
    （60:A17）。よって「効果終了でも主人公死亡でもフレンド公開でもない敗北」の原因は
    ルールY条件＝観測された盤面がその条件を満たすルールYだけが生き残る。
    ★キャラの死そのものは曖昧化しない（KP死は効果終了・フレンド死は公開で検出できる）。
    """
    loop_board: dict[int, dict] = {}
    loop_char_anr: dict[int, dict] = {}
    defeat_loops: set[int] = set()
    dirty_loops: set[int] = set()      # 主人公死/フレンド公開が絡む＝盤面敗北と断定できない
    effect_end_loops: set[int] = set()
    deaths_by_loop: dict[int, int] = {}
    butterfly_loops: set[int] = set()
    for e in history:
        lp = e.get("loop")
        ev = e.get("event")
        if ev == "loop_board":
            loop_board[lp] = e.get("board_anyaku", {})
            loop_char_anr[lp] = e.get("char_anyaku", {})
        elif ev == "loop_result" and "敗北" in str(e.get("result", "")):
            defeat_loops.add(lp)
        elif ev == "game_over" and e.get("winner") == "mastermind":
            defeat_loops.add(lp)
        elif ev == "death":
            deaths_by_loop[lp] = deaths_by_loop.get(lp, 0) + 1
        elif ev == "protagonist_death":
            dirty_loops.add(lp)
        elif ev == "role_reveal" and e.get("role") == "フレンド":
            dirty_loops.add(lp)
        elif ev == "loop_end" and "ループ終了効果" in str(e.get("reason", "")):
            effect_end_loops.add(lp)
        elif ev == "incident" and e.get("occurs") and e.get("name") == "蝶の羽ばたき":
            butterfly_loops.add(lp)

    # 死者ゼロの「ループ終了効果」＝TT の任意敗北しかない（KP死亡は死者を伴う）
    tt_required = any(lp in effect_end_loops and deaths_by_loop.get(lp, 0) == 0
                      for lp in defeat_loops)

    allowed_sets: list[set] = []
    for lp in defeat_loops:
        if lp in dirty_loops or lp in effect_end_loops:
            continue  # 死亡・効果終了が絡む敗北はここでは使わない
        board = loop_board.get(lp)
        if board is None:
            continue
        allowed: set[str] = set()
        if board.get("学校", 0) >= 2:
            allowed.add("守るべき場所")
        if board.get("神社", 0) >= 2:
            allowed.add("封印されしモノ")
        if any(v >= 2 for v in board.values()):
            allowed.update(("復讐者の灯火", "巨大時限爆弾Xの存在"))
        if any(v >= 2 for v in loop_char_anr.get(lp, {}).values()):
            allowed.add("僕と契約しようよ！")
        if lp in butterfly_loops:
            allowed.add("未来改変プラン")
        allowed_sets.append(allowed)
    return allowed_sets, tt_required


def _turn_end_death_constraints(history: list[dict]) -> list[tuple[str, frozenset, bool]]:
    """ターン終了フェイズの死（present付き）＝(被害者, 他の顔ぶれ, ウイルス犯行可否) のリスト。

    ルール上、ターン終了フェイズにキャラを殺せるのは：
    - シリアルキラー【強制】＝2人きり（他の顔ぶれが1人ならその1人がSK候補）
    - キラー【任意】＝被害者はキーパーソンで、キラーは同席（同エリア要求）
    - 妄想拡大ウイルス（BTX）でSK化したパーソン＝2人きり。ただし不安≥3で化し≤1で解除
      （不安2はヒステリシス）＝死の瞬間の相手の不安（公開カウンター）が≤1なら犯行不可能。
    """
    cons: list[tuple[str, frozenset, bool]] = []
    seen: set[tuple[str, frozenset, bool]] = set()
    for e in history:
        if (e.get("event") == "death" and e.get("phase") == "turn_end"
                and e.get("present")):
            others = frozenset(set(e["present"]) - {e["name"]})
            if not others:
                continue
            virus_ok = True  # 不安情報が無い旧ログは「ありうる」扱い（健全側）
            if len(others) == 1:
                u = (e.get("present_unrest") or {}).get(next(iter(others)))
                if u is not None and u <= 1:
                    virus_ok = False  # 不安≤1＝ウイルスSK化は解除済み＝犯行不可能
            key = (e["name"], others, virus_ok)
            if key not in seen:  # 同一制約の重複は情報を増やさない＝間引く
                seen.add(key)
                cons.append(key)
    return cons


def _ito_signal(history: list[dict]) -> bool | None:
    """因果の糸（BTX・【強制】ループ開始時、前ループ終了時に友好があった全員に不安+2）の
    在/不在シグナル。True=必在／False=不可能／None=不明。

    - ループ開始フェイズの不安+2 が観測された → 因果の糸あり（唯一のループ開始不安ソース）
    - 前ループ終了時に友好持ちが居た（loop_board の char_goodwill・公開カウンター）のに、
      次ループ開始で不安が置かれなかった → 因果の糸なし（強制効果の不発はありえない）
    """
    goodwill_at_end: dict[int, bool] = {}
    started_loops: set[int] = set()
    start_unrest_loops: set[int] = set()
    for e in history:
        lp = e.get("loop")
        ev = e.get("event")
        if ev == "loop_board":
            gw = e.get("char_goodwill")
            if gw is not None:  # 旧ログ（キー無し）は不在判定に使わない＝健全
                goodwill_at_end[lp] = bool(gw)
        elif ev == "loop_start":
            started_loops.add(lp)
        elif ev == "unrest" and e.get("phase") == "loop_start" and e.get("delta") == 2:
            start_unrest_loops.add(lp)
    if start_unrest_loops:
        return True
    for lp, had in goodwill_at_end.items():
        if had and (lp + 1) in started_loops and (lp + 1) not in start_unrest_loops:
            return False
    return None


def _death_role_exclusions(history: list[dict]) -> tuple[set, set, set]:
    """死からの否定形消去。返り値 (KP除外集合, フレンド除外集合, TT除外集合)。

    - キーパーソンの死は【強制】即ループ終了（効果終了）。効果終了が一度も無いループの
      死者はキーパーソンではない。
    - フレンドの死はループ終了評価で【強制】役職公開される（60:A17＝主人公死亡終了でも）。
      評価が行われ（loop_board あり）フレンド公開が無かったループの死者はフレンドではない。
      ★異世界人の蘇生（revive・公開）で終了時生存に戻った死者は対象外（公開されないのが正しい）。
    """
    deaths_by_loop: dict[int, set[str]] = {}
    revived_by_loop: dict[int, set[str]] = {}
    effect_end_loops: set[int] = set()
    friend_reveal_loops: set[int] = set()
    evaluated_loops: set[int] = set()
    revealed_friends: set[str] = set()
    for e in history:
        lp = e.get("loop")
        ev = e.get("event")
        if ev == "death":
            deaths_by_loop.setdefault(lp, set()).add(e["name"])
        elif ev == "revive":
            revived_by_loop.setdefault(lp, set()).add(e["name"])
        elif ev == "loop_end" and "ループ終了効果" in str(e.get("reason", "")):
            effect_end_loops.add(lp)
        elif ev == "role_reveal" and e.get("role") == "フレンド":
            friend_reveal_loops.add(lp)
            revealed_friends.add(e["name"])
        elif ev == "loop_board":
            evaluated_loops.add(lp)
    kp_ex: set = set()
    fr_ex: set = set()
    for lp, names in deaths_by_loop.items():
        if lp not in effect_end_loops:
            kp_ex |= names  # ループを止めなかった死＝キーパーソンではない
        if lp in evaluated_loops and lp not in friend_reveal_loops:
            # 公開なし評価ループの死者＝フレンドではない（蘇生済み・既公開は除く）
            fr_ex |= names - revived_by_loop.get(lp, set()) - revealed_friends
    # タイムトラベラーは不死＝死亡イベント自体が出ない（死は防がれ公開されない）。
    # よって一度でも死んだキャラはTTではない（自明に健全な否定形）。
    tt_ex: set = set().union(*deaths_by_loop.values()) if deaths_by_loop else set()
    return kp_ex, fr_ex, tt_ex


def _pair_survivals(history: list[dict]) -> list[tuple[frozenset, frozenset]]:
    """ターン終了のシリアルキラー解決直後に「2人きりで無事」だったペア（公開盤面）。

    返り値: (ペア, そのうち不安≥3だったメンバー集合) のリスト。
    - SKの殺害は【強制】＝生き残ったペアに活動中のSKは居ない（否定形の物理）。
    - 妄想拡大ウイルス（BTX）：不安≥3のパーソンはSK化＝生き残ったペアの不安≥3メンバーは
      ウイルス組では「パーソンでない」（否定形＝ウイルス試験の受け皿）。
    例外はbelief側で分岐：相方がタイムトラベラー（不死＝殺せない）の割当だけ許す。
    護衛消費ペアはsim側で除外済み。同一制約の再観測は情報を増やさない＝間引く。
    """
    out: list[tuple[frozenset, frozenset]] = []
    seen: set[tuple[frozenset, frozenset]] = set()
    for e in history:
        if e.get("event") == "turn_end_pairs":
            unrest = e.get("unrest", {})
            for pair in e.get("pairs", []):
                key = frozenset(pair)
                hot = frozenset(n for n in key if unrest.get(n, 0) >= 3)
                if len(key) == 2 and (key, hot) not in seen:
                    seen.add((key, hot))
                    out.append((key, hot))
    return out


def _sum_role_in(cast, slots, kp_shoujo, refused, role: str, cand: set, fixed: dict, then):
    """『役職 role の担い手は cand の中に居る』制約：候補への割当を場合分けして合算。

    role のスロットは1（クロマク/ミスリーダー/ファクターは各ルール組で高々1）なので
    割当は排反＝単純合算で厳密。確定済みなら整合チェックのみ。
    """
    already = [c for c, r in fixed.items() if r == role]
    if already:
        return then(fixed) if already[0] in cand else (0, {})
    total = 0
    agg: dict = {}
    for c in cand:
        if c in fixed:
            continue
        w, m = then({**fixed, c: role})
        if w > 0:
            total += w
            for ch, d in m.items():
                ad = agg.setdefault(ch, {})
                for r, cnt in d.items():
                    ad[r] = ad.get(r, 0) + cnt
    return total, agg


def _sum_role_all_in(cast, slots, kp_shoujo, refused, role: str, cand: set,
                     fixed: dict, then):
    """『役職 role の担い手【全員】が cand の中』制約（スロット数2以上にも対応）。

    残スロット分を cand から選ぶ組合せで場合分け（排反＝厳密）。
    確定済みの担い手が cand の外なら矛盾（0）。
    """
    from itertools import combinations
    for c, r in fixed.items():
        if r == role and c not in cand:
            return 0, {}
    k = slots.get(role, 0) - sum(1 for r in fixed.values() if r == role)
    if k <= 0:
        return then(fixed)
    free = sorted(c for c in cand if c not in fixed)
    if len(free) < k:
        return 0, {}
    total = 0
    agg: dict = {}
    for comb in combinations(free, k):
        w, m = then({**fixed, **{c: role for c in comb}})
        if w > 0:
            total += w
            for ch, d in m.items():
                ad = agg.setdefault(ch, {})
                for r, cnt in d.items():
                    ad[r] = ad.get(r, 0) + cnt
    return total, agg


def _combo_weight_full(cast, slots, fixed, kp_shoujo, refused, rule_xs,
                       kuro_set, ml_set, board_sets, death_cons=(),
                       kp_excluded=frozenset(), friend_excluded=frozenset(),
                       pair_survivals=(), tt_excluded=frozenset()):
    """位置制約（present）＋ターン終了死＋拒否制約を重ねた厳密な数え上げ。"""
    # クロマクの実効候補：キャラ事象の∩に、噂の無いルール組ではボード事象の∩も重ねる
    k_set = kuro_set
    if board_sets and "不穏な噂" not in rule_xs:
        for s in board_sets:
            k_set = s if k_set is None else (k_set & s)

    def base(fx):
        return _combo_weight_with_refusals(cast, slots, fx, kp_shoujo, refused)

    def _acc(pairs):
        total = 0
        agg: dict = {}
        for w, m in pairs:
            if w > 0:
                total += w
                for ch, d in m.items():
                    ad = agg.setdefault(ch, {})
                    for r, cnt in d.items():
                        ad[r] = ad.get(r, 0) + cnt
        return total, agg

    def kp_step(fx):
        # 否定形：ループを止めなかった死者はキーパーソンではない
        if kp_excluded and "キーパーソン" in slots:
            cand = set(cast) - set(kp_excluded)
            return _sum_role_in(cast, slots, kp_shoujo, refused,
                                "キーパーソン", cand, fx, fr_step)
        return fr_step(fx)

    def fr_step(fx):
        # 否定形：公開なし評価ループの死者はフレンドではない（全スロット対象）
        if friend_excluded and slots.get("フレンド", 0):
            cand = set(cast) - set(friend_excluded)
            return _sum_role_all_in(cast, slots, kp_shoujo, refused,
                                    "フレンド", cand, fx, tt_step)
        return tt_step(fx)

    def tt_step(fx):
        # 否定形：死んだキャラはタイムトラベラーではない（TTは不死＝死亡が公開されない）
        if tt_excluded and "タイムトラベラー" in slots:
            cand = set(cast) - set(tt_excluded)
            return _sum_role_in(cast, slots, kp_shoujo, refused,
                                "タイムトラベラー", cand, fx, death_step)
        return death_step(fx)

    # 生存ペアから組ごとの制約列を作る（SK制約＋ウイルス制約の連鎖＝各段の枝は排反）
    pair_cons: list[tuple] = []
    for _pair, _hot in pair_survivals:
        if "シリアルキラー" in slots:
            pair_cons.append(("sk", _pair))
        if "妄想拡大ウイルス" in rule_xs:
            for _x in _hot:  # 不安≥3で無事＝ウイルス組では「SK化するパーソン」ではない
                partner = next(iter(_pair - {_x}))
                pair_cons.append(("virus", _x, partner))

    def pair_step(fx, i=0):
        if i >= len(pair_cons):
            return base(fx)
        con = pair_cons[i]
        nxt = lambda f: pair_step(f, i + 1)  # noqa: E731
        branches = []
        if con[0] == "sk":
            # 『SKはこのペアの外』∨『SK=一方 ∧ 相方=タイムトラベラー（不死）』
            a, b = sorted(con[1])
            branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                         "シリアルキラー", set(cast) - {a, b}, fx, nxt))
            if "タイムトラベラー" in slots:
                for sk, tt in ((a, b), (b, a)):
                    def tb(f, t=tt):
                        return _sum_role_in(cast, slots, kp_shoujo, refused,
                                            "タイムトラベラー", {t}, f, nxt)
                    branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                                 "シリアルキラー", {sk}, fx, tb))
        else:
            # 『xはパーソンでない（＝どれかの役職）』∨『x=パーソン ∧ 相方=TT（殺せない）』
            _t, x, partner = con
            if x in fx:
                if fx[x] != DEFAULT_ROLE:
                    branches.append(nxt(fx))
                elif "タイムトラベラー" in slots:
                    branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                                 "タイムトラベラー", {partner}, fx, nxt))
            else:
                # 直接固定（フレンド等の複数スロット役職も数え上げ側が正しく処理する）
                for r in slots:
                    branches.append(nxt({**fx, x: r}))
                if "タイムトラベラー" in slots:
                    def tb2(f, p=partner):
                        return _sum_role_in(cast, slots, kp_shoujo, refused,
                                            "タイムトラベラー", {p}, f, nxt)
                    branches.append(tb2({**fx, x: DEFAULT_ROLE}))
        return _acc(branches)

    def death_step(fx, i=0):
        if i >= len(death_cons):
            return pair_step(fx)
        victim, others, virus_ok = death_cons[i]
        nxt = lambda f: death_step(f, i + 1)  # noqa: E731
        branches = []
        if len(others) == 1:
            x = next(iter(others))
            # 枝1：x がシリアルキラー（2人きりの強制殺害）
            if "シリアルキラー" in slots:
                branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                             "シリアルキラー", {x}, fx, nxt))
            # 枝2：x がキラーで被害者がキーパーソン（xがSKの場合と排反）
            if "キラー" in slots and "キーパーソン" in slots:
                def kb(f, v=victim):
                    return _sum_role_in(cast, slots, kp_shoujo, refused,
                                        "キーパーソン", {v}, f, nxt)
                branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                             "キラー", {x}, fx, kb))
            # 枝3：x がウイルスでSK化したパーソン（BTX・xの不安≤1観測なら不可能）
            if "妄想拡大ウイルス" in rule_xs and virus_ok:
                if x not in fx:
                    branches.append(nxt({**fx, x: DEFAULT_ROLE}))
                elif fx[x] == DEFAULT_ROLE:
                    branches.append(nxt(fx))
        else:
            # 3人以上の場でのターン終了死＝キラーのキーパーソン殺害のみ
            # （SK/ウイルスSKは「2人きり」でしか殺せない＝ウイルス組でも同じ）
            if "キラー" in slots and "キーパーソン" in slots:
                def kb2(f, v=victim):
                    return _sum_role_in(cast, slots, kp_shoujo, refused,
                                        "キーパーソン", {v}, f, nxt)
                branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                             "キラー", set(others), fx, kb2))
        return _acc(branches)  # どの枝も成立しない組は矛盾＝0

    def ml_step(fx):
        if ml_set is None:
            return kp_step(fx)
        has_ml = "ミスリーダー" in slots
        has_fa = "ファクター" in slots
        if has_ml and not has_fa:
            return _sum_role_in(cast, slots, kp_shoujo, refused,
                                "ミスリーダー", ml_set, fx, kp_step)
        if has_fa and not has_ml:
            return _sum_role_in(cast, slots, kp_shoujo, refused,
                                "ファクター", ml_set, fx, kp_step)
        return kp_step(fx)  # 両方あるルール組は制約を適用しない（健全・弱め）

    if k_set is not None:
        if "クロマク" not in slots:
            return 0, {}  # クロマクの証拠があるのに居ないルール組は矛盾
        return _sum_role_in(cast, slots, kp_shoujo, refused,
                            "クロマク", k_set, fixed, ml_step)
    return ml_step(fixed)


def _mm_phase_signals(history: list[dict]) -> dict:
    """脚本家能力フェイズの公開イベントから「供給源の存在」を読む（ルール接地の推理）。

    - 不安+1（mm phase）→ ミスリーダー（またはBTXのファクター）が脚本に居る。
    - キャラへの暗躍+1（mm phase）→ クロマクが居る（唯一の供給源）。
    - ボードへの暗躍+1（mm phase）→ クロマク か 不穏な噂 のどちらかが有る。
    ※医者の友好能力のmm phase使用（友好無視＋友好2）はsim未実装のため考慮外（実装時に緩める）。
    phaseタグの無い旧ログではシグナルが立たない＝フィルタ不発（健全側に倒れる）。
    """
    from engine.board import AREAS
    sig = {"ml_unrest": False, "char_anyaku": False, "board_anyaku": False}
    for e in history:
        if e.get("phase") != "mastermind_ability" or e.get("delta", 0) <= 0:
            continue
        if e.get("event") == "unrest":
            # ★医者が居合わせた不安は医者の友好能力（友好無視＋友好2）の可能性＝証拠にしない
            if e.get("present") and "医者" in e["present"]:
                continue
            sig["ml_unrest"] = True
        elif e.get("event") == "anyaku":
            if e.get("target") in AREAS:
                sig["board_anyaku"] = True
            else:
                sig["char_anyaku"] = True
    return sig


def _combo_matches_signals(combo: dict, sig: dict) -> bool:
    slots = combo["slots"]
    if sig["ml_unrest"] and "ミスリーダー" not in slots and "ファクター" not in slots:
        return False
    if sig["char_anyaku"] and "クロマク" not in slots:
        return False
    if sig["board_anyaku"] and "クロマク" not in slots \
            and "不穏な噂" not in combo["rule_xs"]:
        return False
    return True


def _rule_y_eliminations(history: list[dict]) -> set[str]:
    """役職起因でない盤面敗北で学校暗躍<2なら守るべき場所ではない（健全・保守的）。"""
    loop_board: dict[int, dict] = {}
    defeat_loops: set[int] = set()
    dirty_loops: set[int] = set()
    for e in history:
        lp = e.get("loop")
        ev = e.get("event")
        if ev == "loop_board":
            loop_board[lp] = e.get("board_anyaku", {})
        elif ev == "loop_result" and "敗北" in str(e.get("result", "")):
            defeat_loops.add(lp)
        elif ev == "game_over" and e.get("winner") == "mastermind":
            defeat_loops.add(lp)
        elif ev in ("death", "protagonist_death"):
            dirty_loops.add(lp)
        elif ev == "role_reveal" and e.get("role") == "フレンド":
            dirty_loops.add(lp)
    elim: set[str] = set()
    for lp in defeat_loops:
        if lp in dirty_loops:
            continue
        board = loop_board.get(lp)
        if board is not None and board.get("学校", 0) < 2:
            elim.add("守るべき場所")
    return elim


# ---------------------------------------------------------------------------
# Belief 本体
# ---------------------------------------------------------------------------

class Belief:
    """公開情報からルール組を絞り、配役の周辺確率を数え上げで求める（FS/BTX共通）。"""

    def __init__(self, cast, incidents_public: list[dict], set_name: str = "FS"):
        self.cast = list(cast)
        self.incidents = list(incidents_public)
        self.set_name = set_name
        self._all_combos = _combos(set_name)
        self._history: list[dict] = []
        # 観測後に効くもの（初期は無観測）
        self._alive_combos = list(self._all_combos)
        self._weights: list[int] = []
        self._marginals: dict = {}
        self._recompute()

    # -- 観測 --------------------------------------------------------------

    def observe(self, history: list[dict]) -> None:
        self._history = list(history)
        self._recompute()

    def _recompute(self) -> None:
        revealed = _revealed_roles(self._history)
        kp_victims = _keyperson_deaths(self._history)  # TT無し組にのみ適用（関数docstring参照）
        rule_x = _revealed_rule_x(self._history)
        elim_y = _rule_y_eliminations(self._history)
        sig = _mm_phase_signals(self._history)
        refused = _refused_chars(self._history)
        kuro_set, ml_set, board_sets = _public_role_constraints(self._history)
        allowed_y_sets, tt_required = _clean_defeat_constraints(self._history)
        death_cons = _turn_end_death_constraints(self._history)
        pair_surv = _pair_survivals(self._history)
        ito = _ito_signal(self._history)
        kp_ex, fr_ex, tt_ex = _death_role_exclusions(self._history)
        # 正の情報（役職公開・KP死推理）と否定形が矛盾しないよう、確定済みは除外しない
        kp_ex -= {n for n, r in revealed.items() if r == "キーパーソン"}
        fr_ex -= {n for n, r in revealed.items() if r == "フレンド"}
        tt_ex -= {n for n, r in revealed.items() if r == "タイムトラベラー"}

        combos: list[dict] = []
        weights: list[int] = []
        agg: dict[str, Counter] = {c: Counter() for c in self.cast}
        for combo in self._all_combos:
            if combo["rule_y"] in elim_y:
                continue
            if rule_x is not None and rule_x not in combo["rule_xs"]:
                continue
            if not _combo_matches_signals(combo, sig):
                continue
            # クリーン敗北の消去法：各クリーン敗北ループで条件を満たしえたルールYだけが残る
            if any(combo["rule_y"] not in allowed for allowed in allowed_y_sets):
                continue
            # 死者なしのループ終了効果＝タイムトラベラーの任意敗北しかない
            if tt_required and "タイムトラベラー" not in combo["slots"]:
                continue
            # 因果の糸の在/不在（【強制】ループ開始不安の観測/不発から確定）
            if ito is True and "因果の糸" not in combo["rule_xs"]:
                continue
            if ito is False and "因果の糸" in combo["rule_xs"]:
                continue
            # 1死＋ループ終了効果：TTの居ない組では死者=キーパーソン確定
            # （TT入りの組は「TTの任意敗北」でも説明がつく＝固定しない・健全側）
            fixed = revealed
            if kp_victims and "タイムトラベラー" not in combo["slots"]:
                fixed = dict(revealed)
                contradiction = False
                for v in kp_victims:
                    if fixed.get(v, "キーパーソン") != "キーパーソン":
                        contradiction = True
                        break
                    fixed[v] = "キーパーソン"
                if contradiction:
                    continue
            w, marg = _combo_weight_full(
                self.cast, combo["slots"], fixed, combo["kp_shoujo"], refused,
                combo["rule_xs"], kuro_set, ml_set, board_sets, death_cons,
                kp_ex, fr_ex, pair_surv, tt_ex)
            if w <= 0:
                continue
            combos.append(combo)
            weights.append(w)
            for c in self.cast:
                for r, cnt in marg.get(c, {}).items():
                    agg[c][r] += cnt
        self._alive_combos = combos
        self._weights = weights
        self._total = sum(weights)
        self._marginals = agg

    # -- 出力 --------------------------------------------------------------

    def role_marginals(self) -> dict[str, dict[str, float]]:
        total = self._total or 1
        return {c: {r: cnt / total for r, cnt in self._marginals[c].items() if cnt > 0}
                for c in self.cast}

    def rule_marginals(self) -> dict[tuple, float]:
        total = self._total or 1
        out: Counter = Counter()
        for combo, w in zip(self._alive_combos, self._weights):
            out[(combo["rule_y"], combo["rule_xs"])] += w
        return {k: v / total for k, v in out.items()}

    def role_gini(self, char: str) -> float:
        """キャラCの役職開示の情報価値＝1−Σp²（Gini不純度）。

        countingの性質：C=役職r で条件付けると残存世界はちょうど p(r)·W。
        よって開示後の期待残存世界は W·Σp² ＝ 期待削減率は 1−Σp²。
        確定済み（p=1）なら0＝開示しても何も得られない。
        """
        marg = self.role_marginals().get(char, {})
        return 1.0 - sum(p * p for p in marg.values())

    def top_rule_prob(self) -> float:
        """最有力ルール組の確率（1に近い＝ルールはほぼ確定＝ルールX開示の価値が低い）。"""
        rm = self.rule_marginals()
        return max(rm.values()) if rm else 1.0

    def most_likely_role(self, role: str) -> tuple[str | None, float]:
        total = self._total or 1
        best, best_p = None, 0.0
        for c in self.cast:
            p = self._marginals[c].get(role, 0) / total
            if p > best_p:
                best, best_p = c, p
        return best, best_p

    def culprit_candidates(self) -> dict[int, set[str]]:
        death_day = _death_day(self._history)
        fired: dict[tuple, bool] = {}
        revealed_culprit: dict[int, str] = {}
        for e in self._history:
            if e.get("event") == "incident":
                fired[(e.get("loop"), e.get("day"))] = bool(e.get("occurs"))
            elif e.get("event") == "culprit_reveal":
                revealed_culprit[e["day"]] = e["name"]
        loops = {lp for (lp, _dy) in fired}
        # ★卓上の物理（eligible＝その瞬間の「生存＆不安臨界以上」・公開カウンターから自明）：
        #   発生→犯人∈eligible／不発→犯人∉eligible。強力な絞り込み。
        eligible_obs: list[tuple[int, bool, set]] = []
        for e in self._history:
            if e.get("event") == "incident" and "eligible" in e:
                eligible_obs.append((e.get("day"), bool(e.get("occurs")),
                                     set(e["eligible"])))
        cand: dict[int, set[str]] = {}
        for inc in self.incidents:
            day = inc["day"]
            if day in revealed_culprit:
                cand[day] = {revealed_culprit[day]}
                continue
            s = set(self.cast)
            for lp in loops:
                if fired.get((lp, day)):
                    for c in list(s):
                        dd = death_day.get((lp, c))
                        if dd is not None and dd < day:
                            s.discard(c)
            for d, occurred, elig in eligible_obs:
                if d != day:
                    continue
                s = (s & elig) if occurred else (s - elig)
            cand[day] = s
        for _ in range(len(cand)):
            singles = {next(iter(s)) for s in cand.values() if len(s) == 1}
            for day, s in cand.items():
                if len(s) > 1:
                    cand[day] = s - singles
        return cand

    def summary(self) -> dict:
        role_targets = {}
        for role in ("キーパーソン", "クロマク", "キラー", "シリアルキラー", "ミスリーダー",
                     "カルティスト", "フレンド", "タイムトラベラー", "ウィッチ",
                     "ラバーズ", "メインラバーズ", "ファクター"):
            name, p = self.most_likely_role(role)
            if name and p > 0:
                role_targets[role] = {"name": name, "prob": round(p, 3)}
        rules = sorted(self.rule_marginals().items(), key=lambda kv: -kv[1])
        return {
            "worlds_remaining": self._total,
            "worlds_total": sum(_combo_weight_and_marginals(
                self.cast, c["slots"], {}, c["kp_shoujo"])[0] for c in self._all_combos),
            "combos_remaining": len(self._alive_combos),
            "combos_total": len(self._all_combos),
            "role_targets": role_targets,
            "rule_top": [{"rule_y": ry, "rule_x": "/".join(rxs), "prob": round(p, 3)}
                         for (ry, rxs), p in rules[:3]],
            "culprit_candidates": {d: sorted(s) for d, s in self.culprit_candidates().items()},
        }
