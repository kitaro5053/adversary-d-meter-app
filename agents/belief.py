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

import hashlib
from collections import Counter, OrderedDict
from functools import lru_cache
from itertools import combinations
from math import factorial

from engine.data import ROLE_CLAUSE_ABILITY, is_shoujo

# 友好無視／絶対友好無視を持つ役職（拒否できるのはこの役職だけ＝拒否＝この役職の証拠）
_IGNORE_ROLES = frozenset(r for r, c in ROLE_CLAUSE_ABILITY.items()
                          if c in ("友好無視", "絶対友好無視"))
from sim.state import (
    BTX_ROLE_UNIVERSE,
    BTX_RULE_X_ROLES,
    BTX_RULE_Y_ROLES,
    DEFAULT_ROLE,
    FS_ROLE_UNIVERSE,
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


def _expand_irregular(combo: dict, set_name: str) -> list[dict]:
    """★イレギュラー特性（sim/generator.py:111 と同型）：イレギュラーは「そのルールが
    追加しない特別役職（＝universe−パーソン−スロット）」を1つ必ず持つ（パーソン不可）。

    combo をその枠外役職 R ごとに展開し、R をスロットに足して irregular_role=R を刻む
    （＝計数側で fixed[イレギュラー]=R を消費できる＝真の世界を表現可能にする）。
    元 combo（イレギュラー無し想定）は返さない＝cast にイレギュラーが居る以上、枠外役職は必在。
    """
    universe = set(FS_ROLE_UNIVERSE if set_name == "FS" else BTX_ROLE_UNIVERSE)
    pool = sorted(universe - {DEFAULT_ROLE} - set(combo["slots"]))
    out = []
    for r in pool:
        slots = Counter(combo["slots"])
        slots[r] += 1
        out.append({**combo, "slots": dict(_capped_slots(slots)),
                    "irregular_role": r})
    return out


@lru_cache(maxsize=16)
def _combos(set_name: str, has_irregular: bool = False) -> tuple[dict, ...]:
    """ルール組の一覧。各組: {rule_y, rule_xs(tuple), slots(dict), kp_shoujo(bool),
    irregular_role(str|None)}。has_irregular=True（cast にイレギュラー在）のときは
    各組をイレギュラーの枠外役職ごとに展開する（枠外役職をスロットに追加）。"""
    base_combos: list[dict] = []
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
                    base_combos.append({"rule_y": ry, "rule_xs": (rx,),
                                        "slots": dict(_capped_slots(slots)),
                                        "kp_shoujo": False})
    elif set_name == "BTX":
        for ry, yroles in BTX_RULE_Y_ROLES.items():
            for rx1, rx2 in combinations(BTX_RULE_X_ROLES, 2):  # ルールXは異なる2つ
                slots = Counter(yroles) + Counter(BTX_RULE_X_ROLES[rx1]) \
                    + Counter(BTX_RULE_X_ROLES[rx2])
                base_combos.append({"rule_y": ry, "rule_xs": tuple(sorted((rx1, rx2))),
                                    "slots": dict(_capped_slots(slots)),
                                    "kp_shoujo": ry == _SHOUJO_RULE})
    else:
        raise ValueError(f"未対応セット: {set_name}")
    out: list[dict] = []
    for c in base_combos:
        if has_irregular:
            out.extend(_expand_irregular(c, set_name))
        else:
            out.append({**c, "irregular_role": None})
    return tuple(out)


def _multinomial(counts: list[int]) -> int:
    total = sum(counts)
    r = factorial(total)
    for c in counts:
        r //= factorial(c)
    return r


def _combo_weight_and_marginals(cast: list[str], slots: dict, fixed: dict,
                                kp_shoujo: bool, _memo: dict | None = None) -> tuple[int, dict]:
    """1ルール組の配役総数（重み）と、各キャラの役職周辺分布（重み内カウント）を返す。

    未確定キャラは対称＝役職Rの割合＝残スロット/残キャラ。僕と契約のキーパーソンだけ少女限定で補正。
    返り値 marginals[char][role] = そのキャラがroleである配役数（weightで割ると確率）。

    ★B-43：**純関数**（cast/slots/fixed/kp_shoujo だけで決まる・副作用なし）。_memo（呼び側が渡す
    per-_recompute スコープの辞書）があれば結果をキャッシュ＝同じ入力に**同じ float を返す**（演算順序も
    変えない＝bit-for-bit）。返り値は下流で読み取り専用（_acc/集約は別 dict へ足す＝inner 非破壊）＝
    キャッシュ値の共有は安全。cast は recompute 内で不変＝キーに含めない。実測重複率93.8%。"""
    if _memo is not None:
        _k = (tuple(sorted(slots.items())), tuple(sorted(fixed.items())), kp_shoujo)
        _cached = _memo.get(_k)
        if _cached is not None:
            return _cached
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
    if _memo is not None:
        _memo[_k] = (weight, marg)
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

def _keyperson_deaths(history: list[dict]) -> tuple[set[str], set[str]]:
    """1死＋同日のループ終了効果＝その死者はキーパーソン。返り値 (confirmed, tt_gated)。

    ★タイムトラベラーの任意敗北も同じ「ループ終了効果」文言で公開される（役職は伏せる）が、
    **TT任意敗北は死者を出さない**（deathless・resolve_turn_end で発火）。よって：
    - ループ終了効果が **turn_end 以外のフェイズ**（事件/主人公能力）で起きた1死＝TTでは
      説明不能（TTはturn_endでしか宣言しない）＝**曖昧さなくKP死亡＝confirmed**（全組に固定可・
      テスター実測 2026-07-13：遠隔殺人でKP死亡した局面でKPが確定しなかった）。
    - turn_end フェイズの1死＝SKの殺し（死者=SK被害者）とTT宣言が同日に重なりうる＝
      『死者=KP』か『別死＋TT』か区別できない＝**tt_gated**（TTの居ない組にだけ適用・従来どおり）。
    """
    deaths_by_dl: dict[tuple, list[str]] = {}
    end_phase_by_dl: dict[tuple, str] = {}
    toshi_by_dl: dict[tuple, int | None] = {}
    for e in history:
        dl = (e.get("loop"), e.get("day"))
        if e.get("event") == "death":
            deaths_by_dl.setdefault(dl, []).append(e["name"])
        elif e.get("event") == "loop_end" and "ループ終了効果" in str(e.get("reason", "")):
            end_phase_by_dl[dl] = str(e.get("phase", ""))
            toshi_by_dl[dl] = e.get("toshi_anyaku")   # B-34：都市暗躍（無ければ None＝旧ログ）
    confirmed: set[str] = set()
    tt_gated: set[str] = set()
    factor_possible: set[str] = set()   # B-34：都市暗躍≥2＝KP能力獲得ファクターでもありうる死者
    for dl, phase in end_phase_by_dl.items():
        names = deaths_by_dl.get(dl, [])
        if len(names) != 1:
            continue
        # turn_end 以外（事件/主人公能力）のループ終了効果＝TTでは起こせない＝KP確定。
        (confirmed if phase and phase != "turn_end" else tt_gated).add(names[0])
        # ★B-34：都市暗躍≥2（or 旧ログで不明）＝この死は KP か『都市暗躍≥2の KP能力獲得ファクター』
        #   か区別できない＝combo でファクター枠があれば KP or ファクター に緩める（健全側）。
        #   都市<2 が公開で分かれば factor 化は不可＝strict KP（精度を保つ）。
        toshi = toshi_by_dl.get(dl)
        if toshi is None or toshi >= 2:
            factor_possible.add(names[0])
    return confirmed, tt_gated, factor_possible


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


def _combo_weight_with_refusals(cast, slots, fixed, kp_shoujo, refused, _memo=None):
    """拒否されたキャラを『友好無視系の役職のどれか』に割り当てる場合分けで厳密に数える。

    counting方式は任意の部分集合制約を直接扱えないため、拒否キャラ（少数）×友好無視役職
    （少数）の割当を列挙し、各割当を fixed に固定して重みを合算する（スロット超過は0で自然に落ちる）。
    """
    todo = [c for c in refused if c not in fixed]
    if not todo:
        return _combo_weight_and_marginals(cast, slots, fixed, kp_shoujo, _memo)
    ignore_roles = [r for r in slots if r in _IGNORE_ROLES]
    if not ignore_roles:
        return 0, {}  # 友好無視役職が居ないルール組で拒否は起きない＝矛盾
    total_w = 0
    agg: dict = {}
    def _rec(i: int, fx: dict) -> None:
        nonlocal total_w
        if i == len(todo):
            w, m = _combo_weight_and_marginals(cast, slots, fx, kp_shoujo, _memo)
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


_DOCTOR_UNREST_HEARTS = 2   # 医者「不安操作（除去/付与）」の必要友好（engine.data GOODWILL_ABILITIES）


def _doctor_ability_live(history: list[dict]) -> list[bool]:
    """history と同じ長さのリスト＝各イベント『時点』で脚本家が医者の友好能力
    （不安操作・友好2）を使えたか＝医者の友好カウンターが2以上か。友好カウンターは公開情報。

    ★B-25（BTX seed14 実測 2026-07-17）：mm能力フェイズの不安ソースは
    ML/ファクター（同エリア）か 医者の友好能力だけ（KB: 60 B-8/B-6＝脚本家が使える友好能力は
    標準では医者のみ・軍人/教師は不可・マスコミは脚本家使用不可）。**医者の友好が2未満なら
    医者能力は原理的に不可＝その不安は ML/ファクター 由来と確定でき present制約をハードに使える**。
    従来は「医者が present に居る」だけで曖昧扱いして事象を丸ごと捨てていた（seed14 実測：
    不安7件中6件が医者友好0＝曖昧でないのに全件破棄→MLが最後まで {アイドル0.5, 医者0.5} で
    未確定・女の子の枝刈りもL4D1まで遅延）。医者不在キャストでは常に False＝制約は常にハード。
    """
    out: list[bool] = []
    gw = 0
    for e in history:
        if e.get("event") == "loop_start":
            gw = 0                       # ループ開始＝カウンターは全リセット（KB: 00）
        out.append(gw >= _DOCTOR_UNREST_HEARTS)
        if e.get("event") == "goodwill" and e.get("target") == "医者":
            gw = max(0, gw + int(e.get("delta", 0) or 0))
    return out


# ★B-30（大物のテリトリー投射・2026-07-17）：KB: 20 大物特性「脚本家がこのキャラクターの能力を
#   使う場合、テリトリーにいるものとして能力を使用してもよい」＝**大物は present に居なくても
#   mm能力フェイズの供給源（クロマク暗躍／ミスリーダー不安）になりうる**。よって present ベースの
#   「同エリア＝供給源はpresentの中」というハード制約は、大物が cast に居る脚本では**大物にだけ
#   成立しない**。制約集合に大物を常に足して健全側に倒す（真の配役を消さない＝可能世界0を防ぐ）。
#   ★実測（sim実装後・random_BTX s14＝大物クロマク/テリトリー神社）：この緩和が無いと
#     「L1D2 暗躍→ナース present=[ナース,幻想]」で大物がクロマク候補から消え、**可能世界0＝
#     真の配役を全消去**＝主人公AIが盲目化して防衛を落としていた。
#   ★精度の follow-up：mm能力フェイズのイベントに発動エリアを持たせれば「そのエリア==テリトリー」
#     の時だけ大物を足せる（現状はイベントに area 欄が無く、大物を常に候補に含める＝過剰包含だが健全）。
def _oomono_relax(cast) -> set:
    """大物が cast に居れば {"大物"}＝present制約の例外集合（テリトリー投射で遠隔供給しうる）。"""
    return {"大物"} if "大物" in (cast or ()) else set()


def _public_role_constraints(history: list[dict], cast=()) -> tuple[set | None, set | None, list[set]]:
    """脚本家能力フェイズの発動位置（present＝その瞬間そのエリアに居た顔ぶれ・公開）から
    役職の候補者集合を絞る。

    - キャラへの暗躍+1 → クロマクは present の中に居る（唯一の供給源・同エリア要求）。
    - ボードへの暗躍+1 → クロマク∈present か 不穏な噂（ルール組ごとに判断＝board_setsで返す）。
    - 不安+1 → ミスリーダー（/ファクター）∈present。★医者が present に居て、かつ
      **その時点で医者の友好が2以上**（＝医者の友好能力が実際に使えた）場合だけ曖昧＝
      この事象は使わない（健全側）。友好2未満なら医者能力は不可＝ML/ファクター由来と確定
      （B-25＝従来は医者presentだけで捨てており、ML確定を取りこぼしていた）。
    返り値: (クロマク候補∩, ミスリーダー候補∩, ボード事象のpresent集合リスト)。
    """
    from engine.board import AREAS
    kuro: set | None = None
    ml: set | None = None
    board_sets: list[set] = []
    _relax = _oomono_relax(cast)          # B-30：大物のテリトリー投射の例外集合
    doc_live = _doctor_ability_live(history)
    for _i, e in enumerate(history):
        if e.get("phase") != "mastermind_ability" or e.get("delta", 0) <= 0:
            continue
        present = e.get("present")
        if present is None:  # 旧ログ（present欄そのものが無い）だけ除外
            continue
        # ★present==[] は決定的証拠として使う（AIC検死 seed8・2026-07-09）：
        #   ボード暗躍で盤面に生存キャラが誰も居ない＝クロマク（同エリア/自ボード限定）
        #   では説明不能＝不穏な噂で確定。旧実装は `if not present` で None と [] を
        #   同一視し、この証拠を握り潰していた（真の脚本が最低評価に沈む実害）。
        #   キャラ暗躍/不安イベントの present は対象自身を含み空にならない＝
        #   実質効くのはボード暗躍だけで過剰消去は起きない（健全側を保つ）。
        s = set(present)
        if e.get("event") == "anyaku":
            if e.get("target") in AREAS:
                board_sets.append(s | _relax)   # B-30：大物はテリトリーへ遠隔で暗躍しうる
            else:
                s_k = s | _relax
                kuro = s_k if kuro is None else (kuro & s_k)
        elif e.get("event") == "unrest":
            if "医者" in s and doc_live[_i]:  # 医者能力が実際に使えた＝曖昧（B-25）
                continue
            s_ml = s | _relax                # B-30：大物はテリトリーから遠隔で不安+1しうる
            ml = s_ml if ml is None else (ml & s_ml)
    return kuro, ml, board_sets


def _ml_forced(history: list[dict], cast=()) -> set:
    """mm能力フェイズの不安イベントで present が単独（≤1人）だったキャラ＝その不安の供給源は
    **ミスリーダー か ファクター 確定**（唯一の在席者が源＝KB。B-18）。両方 slots のBTXルール組で
    ml_set の∩制約が丸ごと落ちる穴を塞ぐための「ML or ファクター 確定」容疑を返す。
    ★医者 present は「その時点で医者の友好が2以上＝医者のmm友好能力が実際に使えた」時だけ
    曖昧として除外する（健全側・ml_setと同基準）。友好2未満なら医者能力は不可＝単独present の
    医者自身が不安源＝ML or ファクター 確定（B-25＝従来は医者を無条件除外し、seed14 の
    L4D1『present=[医者]・友好0』というML確定証拠を取りこぼしていた）。"""
    forced: set = set()
    # ★B-30：大物のテリトリー投射（KB: 20）＝大物は present に居なくても不安+1しうる＝
    #   「単独present＝その1人が不安源」の推論が成立しない（源が大物かもしれない）。
    #   大物が cast に居る脚本ではこの確定を行わない（健全側＝誤確定で真の配役を消さない）。
    if "大物" in (cast or ()):
        return forced
    doc_live = _doctor_ability_live(history)
    for _i, e in enumerate(history):
        if (e.get("phase") != "mastermind_ability" or e.get("event") != "unrest"
                or e.get("delta", 0) <= 0):
            continue
        present = e.get("present")
        if present is None:
            continue
        s = set(present)
        if ("医者" in s and doc_live[_i]) or len(s) != 1:
            continue
        forced |= s          # 単独present＝その1人が不安源＝ML or ファクター確定
    return forced


def _phase_kuromaku_unions(history: list[dict], cast=()) -> list[set]:
    """同一 mm能力フェイズ（loop×day で一意）に『キャラ暗躍0・ボード暗躍≥2』の
    フェイズを検出し、各フェイズについて クロマク∈(そのボード暗躍群のpresentの和集合)
    という必要条件を返す。

    根拠：mm能力フェイズのボード暗躍ソースはクロマク（≤1体・≤1/フェイズ・同エリア/自ボード）と
    不穏な噂（≤1枚・1/loop）の2つだけ（legal.py 正典）。よって1フェイズにボード暗躍が2箇所出れば、
    噂は高々1つしか供給できず、**少なくとも1つはクロマク由来**＝クロマクはそのフェイズの
    ボード暗躍のいずれかのpresentに居る（和集合）。★board_sets の∩（噂なしルール組限定）と違い、
    この和集合制約は **不穏な噂∈rule_X のルール組にも効く**（B-2）＝発生エリアに居なかった
    キャラをクロマク容疑から外せる。
    キャラ暗躍があるフェイズはクロマクがそのキャラ位置に固定される（board_sets とは別経路で∩）ため
    対象外＝ボード群は全て噂側になりうる（和集合を使うと真のクロマクを誤除外しかねない・健全側）。
    """
    from engine.board import AREAS
    # ★B-30：大物のテリトリー投射＝大物は present に居なくてもボードへ暗躍しうる＝
    #   「クロマク∈(そのフェイズのボード暗躍presentの和集合)」が偽になりうる。大物が cast に
    #   居る脚本ではこの和集合制約を使わない（健全側）。
    if "大物" in (cast or ()):
        return []
    board_by_phase: dict = {}
    char_phases: set = set()
    for e in history:
        if (e.get("phase") != "mastermind_ability" or e.get("delta", 0) <= 0
                or e.get("event") != "anyaku"):
            continue
        present = e.get("present")
        if present is None:  # 旧ログ（present欄なし）は使わない
            continue
        key = (e.get("loop"), e.get("day"))
        if e.get("target") in AREAS:
            board_by_phase.setdefault(key, []).append(set(present))
        else:
            char_phases.add(key)  # キャラ暗躍＝クロマク位置が固定
    unions: list[set] = []
    for key, sets in board_by_phase.items():
        if key in char_phases:
            continue
        if len(sets) >= 2:
            unions.append(set().union(*sets))
    return unions


def _clean_defeat_constraints(history: list[dict]) -> tuple[list[set], bool]:
    """評価敗北（ループ終了時判定で敗北）ごとの「ありうるルールY」集合と、
    「死者なしのループ終了効果」＝タイムトラベラー必在フラグ。

    敗北には必ず原因がある。ループ終了時の評価で成立しうる敗北は
    (a) ルールYの盤面条件 (b) フレンド死亡 の2系統のみで、(b)は【強制】役職公開を伴う
    （60:A17）。よって「効果終了でも主人公死亡でもフレンド公開でもない敗北」の原因は
    ルールY条件＝観測された盤面がその条件を満たすルールYだけが生き残る。
    ★キャラの死そのものは曖昧化しない（KP死は効果終了・フレンド死は公開で検出できる）。
    ★ただし役職公開は一度きり＝**既公開フレンドの死**は新たな公開を出さない
    → 既知フレンドが死んだループも敗北原因の帰責に使わない（過剰消去の実測バグ）。
    """
    loop_board: dict[int, dict] = {}
    loop_char_anr: dict[int, dict] = {}
    defeat_loops: set[int] = set()
    dirty_loops: set[int] = set()      # 主人公死/フレンド死が絡む＝盤面敗北と断定できない
    effect_end_loops: set[int] = set()
    effect_end_days: set[tuple] = set()
    death_days: set[tuple] = set()
    death_names_by_loop: dict[int, set] = {}
    revealed_friends: set[str] = set()
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
            death_days.add((lp, e.get("day")))
            death_names_by_loop.setdefault(lp, set()).add(e.get("name"))
        elif ev == "protagonist_death":
            dirty_loops.add(lp)
        elif ev == "role_reveal" and e.get("role") == "フレンド":
            dirty_loops.add(lp)
            revealed_friends.add(e.get("name"))
        elif ev == "loop_end" and "ループ終了効果" in str(e.get("reason", "")):
            effect_end_loops.add(lp)
            effect_end_days.add((lp, e.get("day")))
        elif ev == "incident" and e.get("occurs") and e.get("name") == "蝶の羽ばたき":
            butterfly_loops.add(lp)
    # 既公開フレンドが死んだループ＝フレンド死敗北でありうる（公開は再発しない）
    for lp, names in death_names_by_loop.items():
        if names & revealed_friends:
            dirty_loops.add(lp)

    # 宣言"当日"死者ゼロの「ループ終了効果」＝TT の任意敗北しかない（KP死亡のループ終了
    # 効果は必ず同日に死者を伴う。ループ単位の死者ゼロ判定だとSKが序盤に殺すループで
    # TT宣言を見逃す＝実測の教訓）
    tt_required = any(key not in death_days and key[0] in defeat_loops
                      for key in effect_end_days)

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


def _turn_end_death_constraints(
        history: list[dict]) -> list[tuple[str, frozenset, bool, bool]]:
    """ターン終了フェイズの死（present付き）＝(被害者, 他の顔ぶれ, ウイルス犯行可否,
    キラー犯行可否) のリスト。

    ルール上、ターン終了フェイズにキャラを殺せるのは：
    - シリアルキラー【強制】＝2人きり（他の顔ぶれが1人ならその1人がSK候補）
    - キラー【任意】＝被害者はキーパーソンで、キラーは同席（同エリア要求）。ただし
      キラーのKP殺害は **被害者(KP)の暗躍≥2 が必須**（40:94）＝死の瞬間の被害者の暗躍
      （公開カウンター）が<2なら「キラーによる殺害」は物理的に不可能（AIC診断 2026-07-09）。
    - 妄想拡大ウイルス（BTX）でSK化したパーソン＝2人きり。ただし不安≥3で化し≤1で解除
      （不安2はヒステリシス）＝死の瞬間の相手の不安（公開カウンター）が≤1なら犯行不可能。
    """
    cons: list[tuple[str, frozenset, bool, bool]] = []
    seen: set[tuple[str, frozenset, bool, bool]] = set()
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
            # 被害者の死亡時暗躍。<2 なら「キラーによるKP殺害」は不可能（枝2を無効化）。
            # 暗躍情報が無い旧ログは None＝健全側で「ありうる」扱い（killer_ok=True）。
            va = (e.get("present_anyaku") or {}).get(e["name"])
            killer_ok = (va is None) or (va >= 2)
            key = (e["name"], others, virus_ok, killer_ok)
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


def _tt_declare_constraints(history: list[dict]) -> list[frozenset]:
    """死者ゼロのループ終了効果＝TTの任意敗北しかない（KP死は死者を伴う）。

    TTの宣言条件は「友好≤2」（50:128）＝宣言時の友好カウンター（公開）が3以上の
    キャラはTTではない。返り値: 各宣言の「友好≤2で盤上生存だった顔ぶれ」＝TTはこの中。
    """
    death_days: set[tuple] = set()
    declares: list[tuple[tuple, dict]] = []
    for e in history:
        key = (e.get("loop"), e.get("day"))
        if e.get("event") == "death":
            death_days.add(key)
        elif (e.get("event") == "loop_end"
                and "ループ終了効果" in str(e.get("reason", ""))
                and e.get("goodwill") is not None):
            declares.append((key, e["goodwill"]))
    out: list[frozenset] = []
    seen: set[frozenset] = set()
    for key, gw in declares:
        # ★宣言"当日"死者ゼロ＝TT宣言で確定（KP死のループ終了効果は必ず同日に死者を伴う。
        #   「ループ内死者ゼロ」だとSKが序盤に殺すループでTT宣言を見逃す＝実測の教訓）
        if key not in death_days:
            s = frozenset(n for n, g in gw.items() if g <= 2)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _tt_gwban_reveals(history: list[dict]) -> tuple[str | None, set]:
    """友好禁止×友好+の同時セット＝TTの公開テスト（全カード公開＋卓上カウンターから）。

    脚本家の友好禁止がキャラXに置かれ、同ターン主人公の友好+もXに置かれた（00:106で
    全カード公開＝両方見える）とき:
    - 行動解決でXの友好が増えた（goodwillイベント）＝禁止を無視した＝X=タイムトラベラー
      【確定】（友好禁止を無視するのはTT【強制】のみ。KB: 50）。
    - 増えなかった＝禁止が実効＝XはTTでない【確定否定】。
    返り値: (確定TTの名前 or None, TTでないと確定したキャラ集合)。
    """
    by_turn: dict[tuple, list[dict]] = {}
    for e in history:
        by_turn.setdefault((e.get("loop"), e.get("day")), []).append(e)
    confirmed: str | None = None
    excluded: set = set()
    for evs in by_turn.values():
        placements = None
        for e in evs:
            if e.get("event") == "cards_revealed":
                placements = e.get("placements", [])
        if not placements:
            continue
        bans = {p.get("target") for p in placements
                if p.get("owner") == "mastermind" and p.get("card") == "友好禁止"
                and p.get("target_kind") == "character"}
        plus = {p.get("target") for p in placements
                if p.get("owner") != "mastermind"
                and str(p.get("card", "")).startswith("友好+")
                and p.get("target_kind") == "character"}
        for x in bans & plus:
            landed = any(e.get("event") == "goodwill" and e.get("target") == x
                         and e.get("delta", 0) > 0
                         and e.get("phase") == "action_resolution"
                         for e in evs)
            if landed:
                confirmed = x
            else:
                excluded.add(x)
    return confirmed, excluded


def _cultist_constraints(history: list[dict]) -> list[frozenset]:
    """暗躍禁止のすり抜け＝カルティストの居場所（公開情報からの特定）。

    行動解決でボードに暗躍禁止が1枚置かれたのに暗躍+カードが通った（解決イベントの
    delta>0）＝そのエリアに立つカルティストが無視した（KB: カルティスト特性）。
    ★自滅ルールに注意：複数主人公の暗躍禁止は「盤面の枚数」で数える＝**ターゲットが
    別でも2枚あれば全部不発**（カルティスト無しで暗躍が通る）。よってそのターンの
    暗躍禁止が全体で1枚、かつそれが当該ボードに置かれていた時だけ推理に使う。
    返り値: 各事象の「その時そのエリアに居た顔ぶれ」＝カルティストはこの中。
    """
    kinshi_by_td: dict[tuple, list] = {}
    for e in history:
        if e.get("event") == "cards_revealed":
            td = (e.get("loop"), e.get("day"))
            targets = [p.get("target") for p in e.get("placements", [])
                       if p.get("card") == "暗躍禁止"]
            kinshi_by_td[td] = targets
    out: list[frozenset] = []
    seen: set[frozenset] = set()
    for e in history:
        if (e.get("event") == "anyaku" and e.get("phase") == "action_resolution"
                and e.get("delta", 0) > 0 and e.get("present")):
            td = (e.get("loop"), e.get("day"))
            kinshi = kinshi_by_td.get(td, [])
            if len(kinshi) == 1 and kinshi[0] == e.get("target"):
                s = frozenset(e["present"])
                if s not in seen:
                    seen.add(s)
                    out.append(s)
    return out


def _protagonist_death_constraints(history: list[dict]) -> list[tuple[frozenset, frozenset]]:
    """ターン終了フェイズの主人公死亡＝(暗躍≥4だった顔ぶれ, 不安≥3∧暗躍≥1だった顔ぶれ)。

    ルール上、ターン終了に主人公を殺せるのは：
    - キラー【任意】＝自身の暗躍≥4（40:95）→ キラーは前者の中
    - メインラバーズ【任意】＝不安≥3かつ暗躍≥1（50:160）→ メインラバーズは後者の中
    （病院の事件の主人公死は事件フェイズ＝phaseで除外。カウンターは卓上の公開情報）
    """
    cons: list[tuple[frozenset, frozenset]] = []
    seen: set[tuple[frozenset, frozenset]] = set()
    for e in history:
        if (e.get("event") == "protagonist_death" and e.get("phase") == "turn_end"
                and ("anyaku" in e or "unrest" in e)):
            anr = e.get("anyaku", {})
            unr = e.get("unrest", {})
            killers = frozenset(n for n, a in anr.items() if a >= 4)
            lovers = frozenset(n for n, a in anr.items()
                               if a >= 1 and unr.get(n, 0) >= 3)
            key = (killers, lovers)
            if key not in seen:
                seen.add(key)
                cons.append(key)
    return cons


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
                       pair_survivals=(), tt_excluded=frozenset(), pd_cons=(),
                       cult_sets=(), tt_sets=(), kuro_union_sets=(),
                       ml_forced=frozenset(), kp_forced=frozenset(), _memo=None):
    """位置制約（present）＋ターン終了死＋拒否制約を重ねた厳密な数え上げ。

    ★kp_forced（B-34）＝「1死＋ループ終了効果」の死者だが、この組に**ファクター枠がある**ため
      死者は キーパーソン **または** ファクター（都市暗躍≥2でKPの死亡→ループ終了効果を獲得・
      50:174 / 40:80 / 60 C-1 ※整理）＝role∈{キーパーソン, ファクター} に緩和する。ファクター枠の
      無い組（FS等）は呼び側が strict な fixed=キーパーソン のまま＝従来と完全に同一（B-30② と同型の
      健全側緩和＝新機構でハード制約『死者=KP』が偽になるのを、真の配役を消さずに扱う）。"""
    # クロマクの実効候補：キャラ事象の∩に、噂の無いルール組ではボード事象の∩も重ねる
    k_set = kuro_set
    if board_sets and "不穏な噂" not in rule_xs:
        for s in board_sets:
            k_set = s if k_set is None else (k_set & s)
    # B-2：同一mm能力フェイズにボード暗躍≥2＝噂は高々1つ供給ゆえ少なくとも1つはクロマク由来。
    #       クロマク∈(そのフェイズのボード暗躍のpresent和集合)。噂ありルール組にも効く。
    for u in kuro_union_sets:
        k_set = set(u) if k_set is None else (k_set & u)

    def base(fx):
        return _combo_weight_with_refusals(cast, slots, fx, kp_shoujo, refused, _memo)

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
        # ＋TT宣言（死者ゼロの効果終了）＝TTは「その瞬間友好≤2だった顔ぶれ」の中（50:128）
        # ＋★拒否キャラはTTではない：友好能力を拒否できるのは友好無視/絶対友好無視を持つ役職
        #   （_IGNORE_ROLES）だけで、TTの条文能力は不死のみ＝友好無視を持たない（50）。よって
        #   拒否が観測されたキャラ（refused）はTT候補から除く。この除外が無いと _sum_role_in で
        #   TTに固定した拒否キャラを _combo_weight_with_refusals が fixed 済みとして見逃し、
        #   矛盾世界（例：拒否したA.I.=TT）が残る（seed25でA.I.=TT 0.5＝ユーザー指摘のバグ）。
        if "タイムトラベラー" not in slots:
            return death_step(fx)
        cand = set(cast) - set(tt_excluded) - set(refused)
        for s in tt_sets:
            cand &= set(s)
        if len(cand) < len(cast):
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

    def cult_step(fx, i=0):
        # 暗躍禁止すり抜け＝カルティストはその場の顔ぶれの中（スロット1のとき厳密。
        # 2スロット組は「少なくとも1人」の分解が煩雑＝適用しない・健全側）
        if i >= len(cult_sets):
            return pair_step(fx)
        if slots.get("カルティスト", 0) != 1:
            if "カルティスト" not in slots:
                return 0, {}  # カルティスト無しの組ですり抜けは起きない＝矛盾
            return pair_step(fx)
        return _sum_role_in(cast, slots, kp_shoujo, refused, "カルティスト",
                            set(cult_sets[i]), fx, lambda f: cult_step(f, i + 1))

    def pd_step(fx, i=0):
        # 主人公死亡（ターン終了）＝キラー（暗躍≥4の中）∨ メインラバーズ（不安3暗躍1の中）
        if i >= len(pd_cons):
            return cult_step(fx)
        killers, lovers = pd_cons[i]
        nxt = lambda f: pd_step(f, i + 1)  # noqa: E731
        branches = []
        if killers and "キラー" in slots:
            branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                         "キラー", set(killers), fx, nxt))
        if lovers and "メインラバーズ" in slots:
            branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                         "メインラバーズ", set(lovers), fx, nxt))
        return _acc(branches)  # どちらでも説明できない組は矛盾＝0

    def death_step(fx, i=0):
        if i >= len(death_cons):
            return pd_step(fx)
        victim, others, virus_ok, killer_ok = death_cons[i]
        nxt = lambda f: death_step(f, i + 1)  # noqa: E731
        branches = []
        if len(others) == 1:
            x = next(iter(others))
            # 枝1：x がシリアルキラー（2人きりの強制殺害）
            if "シリアルキラー" in slots:
                branches.append(_sum_role_in(cast, slots, kp_shoujo, refused,
                                             "シリアルキラー", {x}, fx, nxt))
            # 枝2：x がキラーで被害者がキーパーソン（xがSKの場合と排反）。
            #   被害者の暗躍<2ならキラーのKP殺害は不可能＝この枝を張らない（AIC診断）。
            if killer_ok and "キラー" in slots and "キーパーソン" in slots:
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
            #   被害者の暗躍<2ならキラーのKP殺害は不可能＝矛盾（この死は説明不能）。
            if killer_ok and "キラー" in slots and "キーパーソン" in slots:
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
        # ★B-18：両方あるルール組でも、単独present由来の確定容疑（ml_forced）は ML か ファクター
        #   確定＝その拘束は落とさない（唯一の在席者が不安源＝どちらかの役職）。単独容疑1人なら
        #   「その1人がML」＋「その1人がファクター」の排反2枝で厳密。多人数は健全側で従来どおり弱め。
        cand_forced = ml_forced - set(fx)
        if has_ml and has_fa and len(cand_forced) == 1:
            c = next(iter(cand_forced))
            return _acc([
                _sum_role_in(cast, slots, kp_shoujo, refused,
                             "ミスリーダー", {c}, fx, kp_step),
                _sum_role_in(cast, slots, kp_shoujo, refused,
                             "ファクター", {c}, fx, kp_step)])
        return kp_step(fx)  # 両方あり×確定容疑なし＝制約を適用しない（健全・弱め）

    def _entry(fx):
        if k_set is not None:
            if "クロマク" not in slots:
                return 0, {}  # クロマクの証拠があるのに居ないルール組は矛盾
            return _sum_role_in(cast, slots, kp_shoujo, refused,
                                "クロマク", k_set, fx, ml_step)
        return ml_step(fx)

    # ★B-34：KP能力獲得ファクターの死亡＝死者は キーパーソン or ファクター（都市暗躍≥2で
    #   KPの死亡→ループ終了効果を獲得）。ファクター枠のある組でだけ呼ばれる（呼び側でゲート）。
    #   排反2枝（KP固定／ファクター固定）で厳密に数える＝真の配役を消さない（ml_forced と同型）。
    def kp_forced_step(fx):
        cand = set(kp_forced) - set(fx)
        if not cand:
            return _entry(fx)
        c = next(iter(cand))
        return _acc([
            _sum_role_in(cast, slots, kp_shoujo, refused,
                         "キーパーソン", {c}, fx, kp_forced_step),
            _sum_role_in(cast, slots, kp_shoujo, refused,
                         "ファクター", {c}, fx, kp_forced_step)])

    return kp_forced_step(fixed)


def _mm_phase_signals(history: list[dict]) -> dict:
    """脚本家能力フェイズの公開イベントから「供給源の存在」を読む（ルール接地の推理）。

    - 不安+1（mm phase）→ ミスリーダー（またはBTXのファクター）が脚本に居る。
    - キャラへの暗躍+1（mm phase）→ クロマクが居る（唯一の供給源）。
    - ボードへの暗躍+1（mm phase）→ クロマク か 不穏な噂 のどちらかが有る。
    ※医者の友好能力のmm phase使用（友好無視＋友好2）はsim未実装のため考慮外（実装時に緩める）。
    phaseタグの無い旧ログではシグナルが立たない＝フィルタ不発（健全側に倒れる）。
    """
    from collections import Counter as _C

    from engine.board import AREAS
    sig = {"ml_unrest": False, "char_anyaku": False, "board_anyaku": False,
           "rumor_and_kuromaku": False}
    # ★同一 mm能力フェイズ（loop×day で一意）の暗躍イベント数（場所不問）。
    #   ≥2 なら「クロマク＋不穏な噂の両方が発火」＝両確定（AIC検死 seed8 の一般化）。
    anyaku_per_phase: _C = _C()
    for e in history:
        if e.get("phase") != "mastermind_ability" or e.get("delta", 0) <= 0:
            continue
        if e.get("event") == "unrest":
            # ★医者が居合わせた不安は医者の友好能力（友好無視＋友好2）の可能性＝証拠にしない
            if e.get("present") and "医者" in e["present"]:
                continue
            sig["ml_unrest"] = True
        elif e.get("event") == "anyaku":
            # ★loop/day が揃うイベントだけ「同一フェイズ」に数える（旧ログ/合成イベントで
            #   loop/day 欠落＝(None,None) に誤集約して両確定が暴発するのを防ぐ＝健全側）。
            #   実対局のイベントは state.pub で必ず loop/day を持つ＝実害なし。
            if e.get("loop") is not None and e.get("day") is not None:
                anyaku_per_phase[(e["loop"], e["day"])] += 1
            if e.get("target") in AREAS:
                sig["board_anyaku"] = True
            else:
                sig["char_anyaku"] = True
    # mm能力フェイズの暗躍ソースはクロマク（≤1体・≤1/フェイズ）と不穏な噂（≤1/loop）の
    # 2つだけ（legal.py 正典・クロマクはrule_Yのみ＝最大1体）。よって1フェイズに暗躍
    # イベントが2つ出れば両ソース発火＝クロマク実在かつ不穏な噂∈rule_X（場所は問わない）。
    if any(cnt >= 2 for cnt in anyaku_per_phase.values()):
        sig["rumor_and_kuromaku"] = True
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
    # 同一mm能力フェイズに暗躍イベント≥2＝クロマク実在かつ不穏な噂∈rule_X（両確定）
    if sig["rumor_and_kuromaku"] and ("クロマク" not in slots
                                      or "不穏な噂" not in combo["rule_xs"]):
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
# _recompute のプロセス内メモ化（cast×set×公開履歴の純関数）
# ---------------------------------------------------------------------------
# 脚本家AIは1ターンに何度も同一の公開履歴で base belief を観測し直す（reveal_cost/採点）。
# 実測（5日級）：1ゲームで _recompute が195回＝ターン数(≈20)の約10倍。署名が同じ観測は
# 再計算せず結果を共有する（3席の主人公・脚本家の base も公開履歴が同じなら共有される）。
# _recompute は (cast, set_name, history) だけの関数（incidents や instance 状態に非依存）＝
# 署名が一致すれば結果は厳密に同一。結果は読み取り専用で使う前提（role_marginals 等は新dictを作る）。
_RECOMPUTE_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_RECOMPUTE_CACHE_MAX = 1024


def _recompute_sig(cast, set_name, history) -> str:
    h = hashlib.md5()
    h.update(repr(tuple(cast)).encode("utf-8"))
    h.update(set_name.encode("utf-8"))
    h.update(repr(history).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Belief 本体
# ---------------------------------------------------------------------------

class Belief:
    """公開情報からルール組を絞り、配役の周辺確率を数え上げで求める（FS/BTX共通）。"""

    def __init__(self, cast, incidents_public: list[dict], set_name: str = "FS"):
        self.cast = list(cast)
        self.incidents = list(incidents_public)
        self.set_name = set_name
        # cast にイレギュラーが居れば「枠外役職を1つ持つ」特性を combo に展開する
        self._all_combos = _combos(set_name, "イレギュラー" in self.cast)
        self._history: list[dict] = []
        # ★ソフト証拠レジストリ（CSP4SDG型ソフト重み層・設計提案2026-07-14）。
        #   既定は空＝π=1＝挙動 bit-for-bit 不変（int数え上げのまま）。register_soft_evidence で
        #   1本ずつ較正導入する。非空時は世界に乗算重み π を掛ける（π>0保証＝真配役は消えない）。
        self._soft_evidence: list = []
        # 観測後に効くもの（初期は無観測）
        self._alive_combos = list(self._all_combos)
        self._weights: list = []
        self._marginals: dict = {}
        self._recompute()

    def register_soft_evidence(self, ev) -> None:
        """ソフト証拠を1本登録し、再計算する。ev は combo_logweight(combo, ctx) か
        assign_logweight(char, role, combo, ctx)（フェーズ2）と属性 lam（信頼度）を持つ。
        既定オフ（未登録）なら belief は現行と完全同一。"""
        self._soft_evidence.append(ev)
        self._recompute()

    def _combo_pi(self, combo) -> float:
        """フェーズ1（per-combo）のソフト重み π＝Π_k exp(λ_k · s_k(combo))。
        combo_logweight を持つ証拠だけ寄与（フェーズ2の assign 証拠はここには効かない）。
        π>0 を常に保証（exp なので）＝ハードで生き残った真配役を消さない。"""
        import math
        ctx = {"history": self._history, "cast": self.cast,
               "set_name": self.set_name}
        logp = 0.0
        for ev in self._soft_evidence:
            fn = getattr(ev, "combo_logweight", None)
            if fn is None:
                continue
            logp += getattr(ev, "lam", 1.0) * fn(combo, ctx)
        return math.exp(logp)

    # -- 観測 --------------------------------------------------------------

    def observe(self, history: list[dict]) -> None:
        self._history = list(history)
        # ★ソフト証拠はインスタンス固有＝共有キャッシュは使えない（非空時はバイパス）。
        if self._soft_evidence:
            self._recompute()
            return
        sig = _recompute_sig(self.cast, self.set_name, self._history)
        hit = _RECOMPUTE_CACHE.get(sig)
        if hit is not None:  # 同一観測は再計算せずキャッシュを共有（読み取り専用で使う）
            _RECOMPUTE_CACHE.move_to_end(sig)
            (self._alive_combos, self._weights, self._total,
             self._marginals, self._w_rumor, self._kuro_nonrumor) = hit
            return
        self._recompute()
        _RECOMPUTE_CACHE[sig] = (self._alive_combos, self._weights, self._total,
                                 self._marginals, self._w_rumor, self._kuro_nonrumor)
        if len(_RECOMPUTE_CACHE) > _RECOMPUTE_CACHE_MAX:
            _RECOMPUTE_CACHE.popitem(last=False)

    def _recompute(self) -> None:
        revealed = _revealed_roles(self._history)
        # kp_confirmed＝曖昧さなくKP死亡（全組に固定）／kp_gated＝turn_end死＝TT無し組にのみ適用。
        kp_confirmed, kp_gated, kp_factor_possible = _keyperson_deaths(self._history)
        rule_x = _revealed_rule_x(self._history)
        elim_y = _rule_y_eliminations(self._history)
        sig = _mm_phase_signals(self._history)
        refused = _refused_chars(self._history)
        kuro_set, ml_set, board_sets = _public_role_constraints(self._history, self.cast)
        kuro_unions = _phase_kuromaku_unions(self._history, self.cast)
        ml_forced = _ml_forced(self._history, self.cast)
        allowed_y_sets, tt_required = _clean_defeat_constraints(self._history)
        death_cons = _turn_end_death_constraints(self._history)
        pd_cons = _protagonist_death_constraints(self._history)
        cult_sets = _cultist_constraints(self._history)
        tt_sets = _tt_declare_constraints(self._history)
        # 友好禁止×友好+の公開テスト：TT確定（正）／TT除外（負）
        tt_confirm, tt_gwban_ex = _tt_gwban_reveals(self._history)
        if tt_confirm:
            tt_sets = list(tt_sets) + [frozenset({tt_confirm})]
        pair_surv = _pair_survivals(self._history)
        ito = _ito_signal(self._history)
        kp_ex, fr_ex, tt_ex = _death_role_exclusions(self._history)
        tt_ex |= tt_gwban_ex  # 友好禁止が実効だったキャラはTTでない（公開テストの否定形）
        # 正の情報（役職公開・KP死推理）と否定形が矛盾しないよう、確定済みは除外しない
        kp_ex -= {n for n, r in revealed.items() if r == "キーパーソン"}
        fr_ex -= {n for n, r in revealed.items() if r == "フレンド"}
        tt_ex -= {n for n, r in revealed.items() if r == "タイムトラベラー"}

        # ★B-43：per-_recompute スコープの純関数メモ（history が変わるたび新規＝古い値を持ち越さない）。
        _cwm_memo: dict = {}
        combos: list[dict] = []
        weights: list[int] = []
        agg: dict[str, Counter] = {c: Counter() for c in self.cast}
        # ★reveal_cost 高速化用の集計（クロマク暴露のworlds_afterをO(1)で出すため）：
        #   w_rumor＝不穏な噂を持つ組の重み総和／kuro_nonrumor[c]＝不穏な噂を持たない組での
        #   「c がクロマク」の世界数。クロマクは1スロット＝各cで排反。詳細は reveal_cost_from。
        w_rumor = 0
        kuro_nonrumor: Counter = Counter()
        # ★フェーズ2：per-assignment ソフト証拠（μ の平均場適用）の準備。assign_logweight を持つ
        #   証拠だけが役職↔キャラの marginal を再重み付けする。既定（無し）なら下の集約は現行と同一。
        _assign_evs = [ev for ev in self._soft_evidence
                       if hasattr(ev, "assign_logweight")]
        _soft_ctx = ({"history": self._history, "cast": self.cast,
                      "set_name": self.set_name} if _assign_evs else None)
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
            # （友好禁止無視の公開テストでTTが確定した場合も、TT必在）
            if (tt_required or tt_confirm) and "タイムトラベラー" not in combo["slots"]:
                continue
            # 因果の糸の在/不在（【強制】ループ開始不安の観測/不発から確定）
            if ito is True and "因果の糸" not in combo["rule_xs"]:
                continue
            if ito is False and "因果の糸" in combo["rule_xs"]:
                continue
            # 1死＋ループ終了効果：事件/主人公能力フェイズの死は全組でKP確定（kp_confirmed）、
            # turn_end の死はTT任意敗北と曖昧＝TTの居ない組でのみKP固定（kp_gated）。
            _kp_fix = kp_confirmed | (
                kp_gated if "タイムトラベラー" not in combo["slots"] else frozenset())
            fixed = revealed
            # ★B-34：この組にファクター枠があると、1死＋ループ終了の死者は KP or ファクター
            #   （都市暗躍≥2でKPの死亡→ループ終了効果を獲得＝50:174/40:80）＝strict に KP 固定すると
            #   真のファクター配役を消す（実測：worlds でファクターが p=0）。ファクター枠のある組は
            #   kp_forced（KP or ファクター の2枝）へ回し、無い組（FS等）は従来どおり strict KP。
            _factor_slot = "ファクター" in combo["slots"]
            _kp_forced_here: frozenset = frozenset()
            if _kp_fix:
                fixed = dict(revealed)
                contradiction = False
                relaxed: set = set()
                for v in _kp_fix:
                    rv = revealed.get(v)
                    if rv is not None:
                        # 公開済み＝KP、または（ファクター枠×都市≥2）ファクターなら整合。他は矛盾。
                        if rv == "キーパーソン" or (
                                rv == "ファクター" and _factor_slot
                                and v in kp_factor_possible):
                            continue
                        contradiction = True
                        break
                    if _factor_slot and v in kp_factor_possible:
                        relaxed.add(v)          # KP or ファクター（都市≥2で獲得しうる・2枝）
                    else:
                        fixed[v] = "キーパーソン"  # strict（ファクター枠なし or 都市<2＝factor化不可）
                if contradiction:
                    continue
                _kp_forced_here = frozenset(relaxed)
            # ★イレギュラーの枠外役職をこの combo の R に固定（スロットに R が足されている）。
            #   公開役職と矛盾するなら不成立。
            irr_role = combo.get("irregular_role")
            if irr_role is not None:
                if fixed.get("イレギュラー", irr_role) != irr_role:
                    continue
                if fixed is revealed:
                    fixed = dict(revealed)
                fixed["イレギュラー"] = irr_role
            w, marg = _combo_weight_full(
                self.cast, combo["slots"], fixed, combo["kp_shoujo"], refused,
                combo["rule_xs"], kuro_set, ml_set, board_sets, death_cons,
                kp_ex, fr_ex, pair_surv, tt_ex, pd_cons, cult_sets, tt_sets,
                kuro_union_sets=kuro_unions, ml_forced=ml_forced,
                kp_forced=_kp_forced_here, _memo=_cwm_memo)
            if w <= 0:
                continue
            # ★ソフト層（既定オフ＝scale は int 1＝bit-for-bit 不変）。非空時のみ per-combo の
            #   乗算重み π を掛ける（フェーズ1）。π>0 保証＝真配役は消えない。
            if self._soft_evidence:
                pi = self._combo_pi(combo)
                w = w * pi
                scale = pi
            else:
                scale = 1
            combos.append(combo)
            weights.append(w)
            is_rumor = "不穏な噂" in combo["rule_xs"]
            if is_rumor:
                w_rumor += w
            if _assign_evs:
                # ★平均場 per-assignment μ を Sinkhorn（行=per-charの役職総和／列=per-roleのスロット総和
                #   を both 保存）で適用＝μ≡1 で恒等（bit-for-bit）。per-char renorm 単独だと競合候補を
                #   deflate できず役職列和が膨張する（精度特性化 2026-07-14：ML列和が1.12→1.45）。列正規化を
                #   加えて exact な重み付き permanent に近づける（μ>0＝真配役は消えない）。
                import math
                M: dict = {}
                R: dict = {}          # 目標行和（base の char総和）
                Ccol: Counter = Counter()   # 目標列和（base の役職スロット総和）
                for c in self.cast:
                    cnt = marg.get(c, {})
                    if not cnt:
                        continue
                    row = {}
                    for r, k in cnt.items():
                        lw = 0.0
                        for ev in _assign_evs:
                            lw += getattr(ev, "lam", 1.0) * ev.assign_logweight(
                                c, r, combo, _soft_ctx)
                        row[r] = k * math.exp(lw)
                        Ccol[r] += k
                    M[c] = row
                    R[c] = sum(cnt.values())
                for _ in range(8):    # Sinkhorn 反復（μ≡1なら初回から不変）
                    for c, row in M.items():
                        s = sum(row.values())
                        if s > 0:
                            f = R[c] / s
                            for r in row:
                                row[r] *= f
                    colsum: Counter = Counter()
                    for row in M.values():
                        for r, v in row.items():
                            colsum[r] += v
                    for row in M.values():
                        for r in row:
                            if colsum[r] > 0:
                                row[r] *= Ccol[r] / colsum[r]
                for c, row in M.items():
                    for r, v in row.items():
                        agg[c][r] += v * scale
                        if not is_rumor and r == "クロマク":
                            kuro_nonrumor[c] += v * scale
            else:
                for c in self.cast:
                    cnt = marg.get(c, {})
                    for r, k in cnt.items():
                        agg[c][r] += k * scale
                    if not is_rumor:
                        kk = cnt.get("クロマク", 0)
                        if kk:
                            kuro_nonrumor[c] += kk * scale
        self._alive_combos = combos
        self._weights = weights
        self._total = sum(weights)
        self._marginals = agg
        self._w_rumor = w_rumor
        self._kuro_nonrumor = kuro_nonrumor

    # -- 出力 --------------------------------------------------------------

    def n_worlds(self) -> int:
        """現在生き残っている可能世界の重み総和（＝配役割り当ての数え上げ）。

        観測が増える（history が伸びる）ほど単調に減る。reveal_cost の土台。"""
        return self._total

    def history(self) -> list[dict]:
        """観測済み公開履歴のコピー（reveal_cost が hypo_events を継ぎ足す土台）。"""
        return list(self._history)

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
        # ★効果の着地（蝶の羽ばたき等「犯人と同エリアの1人」系）：着地エリアの顔ぶれ
        #   （公開情報＝卓上で誰でも見える）＝犯人はその中（テスター指摘 2026-07-09）。
        effect_present: list[tuple[int, set]] = []
        for e in self._history:
            if e.get("event") == "incident" and "eligible" in e:
                eligible_obs.append((e.get("day"), bool(e.get("occurs")),
                                     set(e["eligible"])))
            elif e.get("event") == "incident_effect" and e.get("present"):
                effect_present.append((e.get("day"), set(e["present"])))
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
            for d, present in effect_present:
                if d == day:
                    s &= present
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


# ---------------------------------------------------------------------------
# reveal_cost（情報コスト）— 脚本家AIが「この能力を使うと主人公の世界線がどれだけ
# 畳まれるか」を情報コストとしてスコアに引くための純関数（B3・2026-07-08 AIB）。
#
# テスター指摘の系統的な解：クロマク/ミスリーダーの能力は present（発動位置の顔ぶれ・
# 公開）を主人公に晒す＝可能世界を畳む。畳む量を測れれば、脚本家AIは
# 「利得（不安/暗躍）− λ·reveal_cost」で情報衛生を計算できる。
#
# 定義：reveal_cost = 観測 history だけの可能世界数 − (history + hypo_events) の可能世界数。
#   hypo_events＝その手を打ったとき卓上に発行される公開イベント列
#   （例：クロマク能力→{"event":"anyaku","phase":"mastermind_ability","target":X,
#     "delta":1,"present":[...]}）。history は単調に伸びる＝world数は非増加＝コスト≥0。
# 健全性：Belief は主人公が持つのと同じ公開情報から数える＝返す量は「主人公の
#   世界線の畳まれ」そのもの（脚本家の神視点は使わない）。
# ---------------------------------------------------------------------------

def _role_entropy_bits(dist: dict) -> float:
    """役職周辺分布のシャノンエントロピー[bit]（Σは確率が正の項のみ）。"""
    import math
    s = -sum(p * math.log2(p) for p in dist.values() if p > 0)
    return s if s > 0 else 0.0   # 確定分布は -0.0 でなく 0.0 を返す


def _fast_worlds_after(base: "Belief", hypo_events: list[dict]) -> int | None:
    """クロマク暴露（mm能力フェイズの anyaku+present を1件）の worlds_after を、base の
    集計から厳密にO(1)で返す。対応外の hypo は None（＝呼び側でフル再計算）。

    根拠（_public_role_constraints と _combo_matches_signals に厳密対応）：
    - キャラへの暗躍 → 供給源はクロマクのみ（不穏な噂はボード専用）＝クロマク∈present。
      クロマク不在の組は char_anyaku シグナルで消去。
      worlds_after = Σ_{p∈present} marginals[p]['クロマク']（全組・クロマクは1スロット＝排反）。
    - ボードへの暗躍 → クロマク（同エリア/自ボード）か 不穏な噂。不穏な噂を持つ組は無制約
      （w_rumor 全通し）、持たない組はクロマク∈present。
      worlds_after = w_rumor + Σ_{p∈present} kuro_nonrumor[p]。
    """
    if len(hypo_events) != 1:
        return None
    e = hypo_events[0]
    if not (e.get("phase") == "mastermind_ability" and e.get("event") == "anyaku"
            and e.get("delta", 0) > 0):
        return None
    present = e.get("present")
    if present is None:  # present欄が無い hypo は制約を生まない＝フルに委ねる
        return None
    if not hasattr(base, "_kuro_nonrumor"):
        return None
    from engine.board import AREAS
    # ★B-30：大物のテリトリー投射（KB: 20）＝大物は present に居なくても供給源になりうる＝
    #   _public_role_constraints と同じ緩和をここにも適用する（高速路は同関数の論理の複製＝
    #   片方だけ直すと fast≠full になる。実測：緩和前 fast=480 vs full=860 で不一致）。
    P = set(present) | _oomono_relax(base.cast)
    if e.get("target") in AREAS:  # ボード暗躍
        return base._w_rumor + sum(base._kuro_nonrumor.get(p, 0) for p in P)
    # キャラ暗躍
    return sum(base._marginals.get(p, {}).get("クロマク", 0) for p in P)


def reveal_cost_from(base: "Belief", hypo_events: list[dict],
                     targets=None) -> dict:
    """観測済み Belief に hypo_events を仮想追加したときの情報コストを測る。

    base＝既に observe(history) 済みの Belief（「前」の再計算を避けるため使い回す）。
    返り値 dict:
      worlds_before / worlds_after / worlds_collapsed（=before-after・≥0）/
      collapse_frac（=collapsed/before）。
      targets 指定時は targets[役職] ごとに
      {entropy_before, entropy_after, entropy_drop}[bit]（そのキャラの役職分布）。
    """
    before = base.n_worlds()
    # ★高速路（targets無しのworlds_afterのみ要求時）：クロマク暴露（mm能力フェイズの
    #   anyaku+present）は before の集計から O(1) で worlds_after が出せる＝毎回フル再計算
    #   （Belief再構築＋observe）を避ける。他の hypo 形状は None を返しフル計算へ（安全側）。
    #   正しさは tests/test_belief_reveal_fast.py がフル計算との一致で固定。
    after_belief = None
    after = _fast_worlds_after(base, hypo_events) if not targets else None
    if after is None:
        after_belief = Belief(base.cast, base.incidents, base.set_name)
        after_belief.observe(base.history() + list(hypo_events))
        after = after_belief.n_worlds()
    collapsed = max(0, before - after)
    out = {
        "worlds_before": before,
        "worlds_after": after,
        "worlds_collapsed": collapsed,
        "collapse_frac": (collapsed / before) if before > 0 else 0.0,
    }
    if targets:
        mb = base.role_marginals()
        ma = after_belief.role_marginals()
        ent = {}
        for name in targets:
            eb = _role_entropy_bits(mb.get(name, {}))
            ea = _role_entropy_bits(ma.get(name, {}))
            ent[name] = {"entropy_before": eb, "entropy_after": ea,
                         "entropy_drop": max(0.0, eb - ea)}
        out["targets"] = ent
    return out


def reveal_cost(cast, incidents_public: list[dict], set_name: str,
                history: list[dict], hypo_events: list[dict],
                targets=None) -> dict:
    """reveal_cost_from の一発版（base Belief を内部で組む）。

    同一 history で複数の hypo_events を評価するなら、Belief を1つ observe して
    reveal_cost_from を繰り返す方が速い（「前」を1回しか計算しない）。
    """
    base = Belief(cast, incidents_public, set_name)
    base.observe(history)
    return reveal_cost_from(base, hypo_events, targets=targets)
