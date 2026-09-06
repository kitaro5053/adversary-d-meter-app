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
- rule_reveal（情報屋のルールX開示）→ 開示された名前を含む組だけ残す（複数件なら全件＝T3）。
- 敗北条件（守るべき場所＝学校暗躍<2の盤面敗北）→ ルールY消去。
- 犯人候補：発生回で先に死んだ者は除外／犯人開示で確定。
"""

from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict
from functools import lru_cache
from itertools import combinations
from math import factorial

from engine.data import (
    ROLE_CLAUSE_ABILITY,
    UNREFUSABLE_ABILITY_CHARS,
    initial_area_of,
    is_shoujo,
)

# 友好無視／絶対友好無視を持つ役職（拒否できるのはこの役職だけ＝拒否＝この役職の証拠）
_IGNORE_ROLES = frozenset(r for r, c in ROLE_CLAUSE_ABILITY.items()
                          if c in ("友好無視", "絶対友好無視"))
# ★B-101：**絶対**友好無視だけを持つ役職（＝【強制】必ず拒否する＝カルティスト/ウィッチ）。
#   「拒否されずに解決した」の逆向き観測はこの集合にだけハード制約として効く（→_gw_resolved_chars）。
_ABSOLUTE_IGNORE_ROLES = frozenset(r for r, c in ROLE_CLAUSE_ABILITY.items()
                                   if c == "絶対友好無視")
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


def _goshinboku_signals(history: list[dict]) -> tuple[bool, bool]:
    """★B-234：ご神木の特性の観測を (発生, 不発生) の2値で返す（公開履歴のみ）。

    - **発生**＝`goshinboku_move` を**脚本家能力フェイズ**（`phase == "mastermind_ability"`）で観測。
      ★主人公が主人公能力フェイズに任意で使った分（`phase == "goodwill_ability"`）は
      **証拠にならない**（カード文＝主人公側は「移してもよい」＝役職と無関係）＝phase で必ず弾く。
    - **不発生**＝`goshinboku_idle`（`sim/flow.py` が脚本家能力フェイズ末尾に発行）。

    ご神木の役職はゲームを通じて不変ゆえ、健全な実装ではこの2つが同時に立つことは無い
    （立ったら sim か演繹のどちらかが壊れている＝呼び側でバグ検出器として使う）。
    """
    moved = idle = False
    for e in history:
        ev = e.get("event")
        if ev == "goshinboku_move":
            if e.get("phase") == "mastermind_ability" and e.get("from") == _GOSHINBOKU:
                moved = True
        elif ev == "goshinboku_idle":
            idle = True
    return moved, idle


def _revealed_roles(history: list[dict]) -> dict[str, str]:
    return {e["name"]: e["role"] for e in history if e.get("event") == "role_reveal"}


def _revealed_rule_x(history: list[dict]) -> str | None:
    """最初に開示されたルールX 1件（互換用。推理は `_revealed_rule_xs` を使う）。"""
    for e in history:
        if e.get("event") == "rule_reveal" and e.get("rule_x"):
            return e["rule_x"]
    return None


def _revealed_rule_xs(history: list[dict]) -> frozenset[str]:
    """公開履歴で開示された**全て**のルールX名（重複は1つに畳む）。

    ★T3（2026-09-05・B-29x レビュー §5-3）：情報屋の友好能力（KB: 20:176＝「脚本のルールXのうち
      宣言名でないもの1つを伝える」）はループをまたいで複数回使えるため、BTX（ルールX 2つ）では
      **2件とも開示されうる**。以前は最初の1件だけを推理に使っていた＝2件目が捨てられていた。
      開示された名前は必ず脚本のルールX（KB が一意に決める事実）＝全件を絞り込みに使うのが健全。
    """
    return frozenset(e["rule_x"] for e in history
                     if e.get("event") == "rule_reveal" and e.get("rule_x"))


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


def _gw_resolved_chars(history: list[dict]) -> set[str]:
    """★B-101：**拒否されずに解決した**友好能力の行使キャラ＝絶対友好無視ではない（ハード）。

    KB根拠（`rules/` から一意に読める）：
    - `rules/00_rules_core.md:174`「**絶対友好無視**（条文能力）：このキャラは主人公からの
      友好能力使用を**必ず拒否する**」＝解決してしまった時点でその役職ではありえない。
    - `rules/50_basic_tragedy_x.md:98-99`「友好無視＝拒否『できる』／絶対友好無視＝必ず拒否
      『する』。拒否の判定は**能力を使うキャラ**の友好無視で行う（対象キャラではない）」。
    - `rules/20_goodwill_abilities.md:24-25`「空撃ちは**絶対友好無視の判別に使える定番テク**」
      ＝この推理筋は公式に想定されている。

    ∴ 制約は**片側だけがハード**：
    - 絶対友好無視（カルティスト／ウィッチ）＝【強制】必ず拒否 → 解決＝**除外できる**。
    - 通常の友好無視（クロマク/キラー/ファクター/マイナス等）＝【任意】＝見送れる →
      解決しても**何も言えない**（制約にしない。逆向き＝`_refused_chars` の A-42 情報衛生と対称）。

    除外例外＝`UNREFUSABLE_ABILITY_CHARS`（イレギュラー/ナース/妹/コピーキャット＝カード文で
    「友好無視/絶対友好無視で拒否されない」と明記・`rules/20:109` `rules/20:242`／`engine/data.py`）。
    これらのキャラの能力は絶対友好無視でも通る＝解決は何の証拠にもならない。

    ※`goodwill_used`（宣言）ではなく `goodwill_resolved`（解決）を見る＝拒否された宣言を
      誤って「解決」と読まないため（`sim/flow.py` は拒否時に resolved を発行しない）。
    """
    return {e["character"] for e in history
            if e.get("event") == "goodwill_resolved" and e.get("character")
            and e["character"] not in UNREFUSABLE_ABILITY_CHARS}


def _combo_weight_with_refusals(cast, slots, fixed, kp_shoujo, refused, _memo=None):
    """拒否されたキャラを『友好無視系の役職のどれか』に割り当てる場合分けで厳密に数える。

    counting方式は任意の部分集合制約を直接扱えないため、拒否キャラ（少数）×友好無視役職
    （少数）の割当を列挙し、各割当を fixed に固定して重みを合算する（スロット超過は0で自然に落ちる）。

    ★B-97：**fixed に既に載っている拒否キャラも検査する**。上位の `_sum_role_in` /
    `_sum_role_all_in`（KP除外・フレンド除外・SKペア・カルティスト・ML等）が拒否キャラを
    友好無視を持たない役職（例：キーパーソン）に固定して降りてくると、従来はそのキャラが
    `todo` から外れて拒否制約が丸ごと素通りしていた（実測＝実戦棋譜 L2D1 の拒否の直後も
    医者のキーパーソンが 0.245 残存＝推論が1観測ぶん遅れる原因。tt_step の refused 除外は
    この穴のTT専用の対症療法だった）。拒否は**ハード制約**（rules/50:98-99＝拒否できるのは
    「能力を使うキャラ」の友好無視／絶対友好無視のみ）ゆえ、非友好無視役職に固定された
    拒否キャラが居る世界は矛盾＝重み0。
    ※逆向き（「拒否しなかった」）は制約にしない＝友好無視は【任意】で見送れる（A-42の情報衛生）。
    """
    for c in refused:
        r = fixed.get(c)
        if r is not None and r not in _IGNORE_ROLES:
            return 0, {}
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
            if "医者" in s and _doctor_source_possible(e, doc_live[_i]):
                # 医者能力が実際に使えた＝曖昧（B-25）。★B-243（既定OFF）＝対象が医者自身
                #   なら医者起源は不可能（rules/20:228,297）＝曖昧にしない。
                continue
            s_ml = s | _relax                # B-30：大物はテリトリーから遠隔で不安+1しうる
            ml = s_ml if ml is None else (ml & s_ml)
    return kuro, ml, board_sets


#: B-61 の適用範囲（掃引で決定・`docs/監査_B61_ml_strict移植_2026-07-26.md`）。
#:   "elim" ＝**採用値**。ML専用証拠が1件でもあれば「ミスリーダー不在のルール組」を消す
#:           だけに使う（present の中身は使わない）。両ベンチ防衛非退行・3日meanも同値・
#:           5日は1局も動かず・ML分離系の発火頻度も不変（B-52 159→159）でML較正だけ改善。
#:   "both" / "all" ＝ present制約も使う（起票時の本丸＝両在組の75イベント）。**不採用**：
#:           beliefは鋭くなるが B-52分離の発火が 159→293（+84%）に増え、btx_future の
#:           L1防衛8局が L2/L3 に後退（防衛数は不変・meanは 2.038→2.092 と悪化）＝
#:           belief-ml-v2 当時の「下流の席経済が旧beliefの誤り方に共適応」の再現。
#:   "off"  ＝無効（導入前と bit-for-bit 同一）。
_B61_SCOPE = "elim"

#: ★B-101（友好能力の「解決」の逆向き観測・2026-07-29）のスコープ。
#:   "on"  ＝拒否されずに**解決した**友好能力の行使キャラを絶対友好無視役職から除外する。
#:   "off" ＝無効（導入前と bit-for-bit 同一）＝**既定**。
#: ★2026-07-30（ユーザー裁定「B101もON」）＝既定を **"on"** に切り替えた。
#:   KB根拠＝`rules/00:174`（絶対友好無視＝必ず拒否する）／`rules/50:98-99`（拒否判定は
#:   能力を使うキャラで行う）／`rules/20:24-25`（**空撃ちは絶対友好無視の判別に使える定番テク**
#:   ＝公式が想定した推理筋）。∴ 拒否されずに解決した友好能力の行使キャラが
#:   カルティスト／ウィッチでありうる、という可能世界は**規則上そもそも存在しない**。
#: ★較正の実測結果（詳細＝`docs/監査_B101_ON_カルティスト閾値の再較正_2026-07-30.md`）：
#:   前担当が疑った「カルティスト閾値ゲート（0.1/0.25/0.7）の較正ずれ」**ではなかった**。
#:   5日級 `fs5_guard` の退行9局の実体は、(c2b) カルティスト剥がし_候補 **72.00 の
#:   6人フラット同点帯の籤**で、B-101 が帯から1人（正しく）取り除くと籤の出目が変わる、というもの。
#:   対照実験＝B-101 OFF のまま帯から**無関係な1人**を人為的に外すだけで同規模の退行が出る／
#:   逆に**真犯人（医者）を外す**と退行はほぼ出ない＝この局の 2.000 は防御力ではなく籤。
#:   ∴ 閾値の再較正では回収できない（掃引で非退行点なし）。
_B101_SCOPE = "on"

#: ★B-231（2026-08-16・ユーザー指摘＝§72-18 裁定2）の切替口＝**既定 OFF**（導入前と bit-for-bit 同一）。
#:   妹の特性「**友好無視役職にできない**」（`rules/30_characters.md:66`・現物カード確認 2026-07-09）×
#:   友好無視／絶対友好無視を持つ役職＝`_IGNORE_ROLES`（`engine/data.py ROLE_CLAUSE_ABILITY`＝
#:   キラー/クロマク/カルティスト/ウィッチ/ファクター/マイナス）＝**妹はこれらに絶対に配役されない**。
#:   脚本側は `sim/state.py:238-243` が脚本検証（ValueError）で強制済み＝真の配役はこの制約を必ず
#:   満たす＝演繹はハードで健全（真の配役を消さない）。ON にすると、妹∈cast かつ友好無視系スロットの
#:   ある組で、妹の役職を「友好無視を持たない役職＋パーソン」に場合分けして厳密に数える
#:   （排反・完全＝`_combo_weight_full` の `base`）。§67-5 の「妹のフレンド/ファクター2択で公開証拠が
#:   構造的に無い」への訂正（ファクター側は演繹で除外できる）＝FB に限らず役職推定の速度に効く。
B231_IMOUTO_TRAIT: bool = True   # ★既定 ON（2026-08-17・ユーザー裁定「4つとも ON」・§72-28）

#: ★B-234（2026-08-17）の切替口＝**既定 OFF**（導入前と bit-for-bit 同一）。
#:   ご神木の特性（現物カード確認 2026-08-16）＝「主人公は主人公能力フェイズに上のカウンター1つを
#:   同エリアの他キャラへ**移してもよい**。このキャラが**友好無視を持つ場合、脚本家能力フェイズに
#:   脚本家もこの特性を用いる（強制）**」。∴ 公開情報だけで両方向の演繹が立つ：
#:   - **発生**＝脚本家能力フェイズの `goshinboku_move` を観測 ⇒ ご神木 ∈ 友好無視系役職。
#:     （`sim/legal.py:93` が友好無視役職のときしか脚本家に提供しない＝合法手定義と KB の両方に接地）
#:   - **不発生**＝`goshinboku_idle`（`sim/flow.py` が脚本家能力フェイズ末尾に発行する公開イベント
#:     ＝「カウンター>0 かつ 同エリアに生存他キャラ>0 なのに使われなかった」）⇒ ご神木 ∉ 友好無視系。
#:     ★この向きは **B-233 の是正（強制段の実装）で初めて健全になった**（それ以前は脚本家が
#:     任意に見送れた＝真の配役を消しうる誤演繹だった）。
#:   実装＝発生方向は `refused`（`_combo_weight_with_refusals`）へ相乗り、不発生方向は
#:   B-231 の妹分岐（`_combo_weight_full` の `base`）を多キャラへ一般化して相乗り。
B234_GOSHINBOKU_TRAIT: bool = True   # ★既定 ON（2026-08-17・ユーザー裁定「4つとも ON」・§72-28）

#: ご神木の名前（KB: `rules/30_characters.md`・sim/engine と同じ表記の単一ソース）
_GOSHINBOKU = "ご神木"

#: ファクターがミスリーダーの追加能力（不安+1）を得る学校の暗躍数
#: （`sim/legal.py` 正典＝`state.board_anyaku["学校"] >= 2`・KB 50:174 / 60 A10）。
_FACTOR_ML_ANYAKU = 2


def _school_anyaku_live(history: list[dict]) -> list[bool]:
    """history と同じ長さのリスト＝各イベント『時点』で **ファクターがミスリーダー能力を
    持ちえたか**（＝学校の暗躍カウンターが2以上か）。ボードの暗躍カウンターは公開情報。

    ★B-61（belief-ml-v2 の②のみを移植・2026-07-26）：mm能力フェイズの不安ソースは
    ミスリーダー／ファクター（学校暗躍≥2）／医者の友好能力の3つだけ（KB: 60 B-8/B-6・
    `sim/legal.py`）。**学校暗躍が2未満ならファクター起源は原理的に不可**＝医者起源も
    否定できていれば、その不安は**ミスリーダー由来と確定**できる（→ `_ml_strict`）。
    `_doctor_ability_live` と同じ「イベント適用**前**の値」を並べる流儀（out.append が
    delta 適用より先）。ループ開始で全カウンターは0にリセット（KB: 00）。

    ★健全性＝公開履歴からの復元が真値と一致することを実測で確認済み（2026-07-26・
    両ベンチ200局の mm能力フェイズ発動時点 **927件で不一致0**）。復元が過小になると
    「ファクター不可能」を誤断定して真の可能世界を消す＝この一致がB-61の前提。
    ボード暗躍の増加は `state.pub` で必ず公開される（行動解決 `flow` ／mm能力
    `effects` ／事件効果 ／能力による除去 -1 まで全て event="anyaku"）。
    """
    out: list[bool] = []
    an = 0
    for e in history:
        if e.get("event") == "loop_start":
            an = 0                       # ループ開始＝カウンターは全リセット（KB: 00）
        out.append(an >= _FACTOR_ML_ANYAKU)
        if e.get("event") == "anyaku" and e.get("target") == "学校":
            an = max(0, an + int(e.get("delta", 0) or 0))
    return out


def _ml_strict(history: list[dict], cast=()) -> set | None:
    """**ミスリーダー専用**の present 制約（B-61）＝『ML はこの集合の中に居る』。

    `_public_role_constraints` が返す `ml`（弱証拠）は「ミスリーダー **または** ファクター」
    の候補集合なので、**両方 slots にあるルール組では ML の特定に使えない**
    （現状 `ml_step` はその場合に制約を丸ごと捨てている＝両ベンチ200局で75イベント分の
    証拠を破棄していた・`docs/監査_棚の再発掘_2026-07-26.md` §5-1）。

    ここでは供給源を1つに絞れる不安イベントだけを ∩ する：
      (1) mm能力フェイズの不安+（`_public_role_constraints` と同じ入口）
      (2) **医者起源が不可能**＝医者が present に居ない or その時点の友好<2（B-25と同基準）
      (3) **ファクター起源が不可能**＝その時点の学校暗躍<2（`_school_anyaku_live`）
    残る供給源はミスリーダーだけ＝present（＋B-30の大物緩和）に ML が居る。
    該当イベントが1件も無ければ None（制約なし）。
    """
    strict: set | None = None
    _relax = _oomono_relax(cast)          # B-30：大物のテリトリー投射（遠隔で不安+1しうる）
    doc_live = _doctor_ability_live(history)
    fa_live = _school_anyaku_live(history)
    for _i, e in enumerate(history):
        if (e.get("phase") != "mastermind_ability" or e.get("event") != "unrest"
                or e.get("delta", 0) <= 0):
            continue
        present = e.get("present")
        if present is None:
            continue
        s = set(present)
        if "医者" in s and _doctor_source_possible(e, doc_live[_i]):
            continue                      # 医者の友好能力がありえた＝曖昧（B-25と同基準）
        if fa_live[_i]:
            continue                      # 学校暗躍≥2＝ファクター起源もありえた＝ML専用でない
        s_ml = s | _relax
        strict = s_ml if strict is None else (strict & s_ml)
    return strict



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
        if ("医者" in s and _doctor_source_possible(e, doc_live[_i])) or len(s) != 1:
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


def _mm_board_supply_candidates(e: dict, cast=()) -> set:
    """★B-113：mm能力フェイズの**ボード暗躍イベント e** を「クロマク能力の供給」として
    説明しうる供給者候補（公開情報のみ）。

    クロマクは**同エリアのキャラ1人 or 自ボード**にしか置けない（rules/40:86）＝ボード A への
    供給者はその瞬間 A に居た生存キャラ（present）に限られる。例外＝大物のテリトリー投射
    （B-30・present に居なくてもテリトリーへ遠隔供給しうる）＝`_oomono_relax` で常に候補へ
    足す（過剰包含＝「噂と誤確定しない」健全側。イベントに発動エリア欄が無いため
    テリトリー一致では絞らない＝B-30 の既存注記と同じ扱い）。
    """
    return set(e.get("present") or ()) | _oomono_relax(cast)


#: ★B-188（2026-08-07・B-187 の副産物）の切替口＝既定ON。False で **bit-for-bit 旧挙動**へ復帰。
#:   クリーン敗北の消去法（下の `_clean_defeat_constraints`）で、僕と契約しようよ！の説明可否を
#:   「任意のキャラの暗躍≥2」から「**少女**（engine.data.SHOUJO）の暗躍≥2」に限定する。
#:   KB＝`rules/50_basic_tragedy_x.md:42-43`＝契約の敗北条件は**キーパーソン**の暗躍≥2で、
#:   KPは**必ず少女**（KBが一意に決める可否＝規約 §7 判例1 の類型）。∴ 非少女にしか暗躍≥2 が
#:   無いクリーン敗北を契約世界は説明できない＝旧挙動は contract_prob の過大残存源。
#:   健全性＝belief の世界モデル自体が契約組で KP∈少女 を強制済み（`_combo_weight_and_marginals`＝
#:   非少女KP固定は weight 0）・イレギュラーの枠外役職は契約組で第2のKPを作れない
#:   （`_expand_irregular` の pool 除外）＝この消去はモデル内のどの世界も過剰に消さない。
#:   B-187（defense_plan/attack_plan の脅威候補側＝dp.B187_CONTRACT_SHOUJO_ONLY）と同根・別経路。
B188_CONTRACT_SHOUJO_ELIM: bool = True


def _defeat_loop_classes(history: list[dict]) -> dict:
    """ループ単位の敗北分類（`_clean_defeat_constraints` と `_board_x_evidence` の**単一ソース**）。

    ★B-243 で `_clean_defeat_constraints` の前半をそのまま切り出したもの（挙動は bit 不変）。
    2つの推論が「どのループの敗北を盤面に帰責してよいか」の判定を共有するために分けている
    （片方だけ条件が変わるとハード制約の健全性が崩れる＝ドリフト防止）。
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
    return {"loop_board": loop_board, "loop_char_anr": loop_char_anr,
            "defeat_loops": defeat_loops, "dirty_loops": dirty_loops,
            "effect_end_loops": effect_end_loops, "effect_end_days": effect_end_days,
            "death_days": death_days, "butterfly_loops": butterfly_loops}


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
    cls = _defeat_loop_classes(history)
    loop_board = cls["loop_board"]
    loop_char_anr = cls["loop_char_anr"]
    defeat_loops = cls["defeat_loops"]
    dirty_loops = cls["dirty_loops"]
    effect_end_loops = cls["effect_end_loops"]
    effect_end_days = cls["effect_end_days"]
    death_days = cls["death_days"]
    butterfly_loops = cls["butterfly_loops"]

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
        char_anr = loop_char_anr.get(lp, {})
        if B188_CONTRACT_SHOUJO_ELIM:
            # ★B-188：契約の敗北＝**キーパーソン**の暗躍≥2 で、KPは必ず少女（50:42-43）
            #   ＝少女の暗躍≥2 だけがクリーン敗北の契約説明になる（切替口の docstring 参照）
            contract_ok = any(v >= 2 for n, v in char_anr.items() if is_shoujo(n))
        else:
            contract_ok = any(v >= 2 for v in char_anr.values())
        if contract_ok:
            allowed.add("僕と契約しようよ！")
        if lp in butterfly_loops:
            allowed.add("未来改変プラン")
        allowed_sets.append(allowed)
    return allowed_sets, tt_required


#: ★B-243（2026-08-17・ユーザー実戦フィードバック §指摘1）の切替口＝**既定 OFF**
#:   （導入前と bit-for-bit 同一）。
#:   KB＝**ボードX は特定役職の初期エリアに等しい**：
#:     - `rules/50_basic_tragedy_x.md:52`＝巨大時限爆弾Xの存在(Y) の X＝**ウィッチ**の初期エリア
#:     - `rules/40_first_steps.md:42`＝復讐者の灯火(Y) の X＝**クロマク**の初期エリア
#:   ＋ `rules/60_faq_rulings.md:112-114`（C-7）＝ボードに配置されていなくても脚本上存在すれば
#:      ボードXは定まる＝**初期エリア表（`engine.data.CHARACTER_INITIAL_AREA`）が一意に決める量**。
#:   ∴ 「クリーン評価敗北（＝`_clean_defeat_constraints` が盤面に帰責してよいと判定した敗北）」
#:   が観測されたら、そのルール組（rule_y が上の2つ）では **ボードX ∈ {そのループ終了時に
#:   暗躍≥2 だったボード}**。複数ループぶんは ∩ で絞る。
#:   ⇒ **その役職の担い手は「初期エリアが候補ボードに入るキャラ」に限られる**。
#:   ★これは既存の消去法（`_clean_defeat_constraints` が同じ敗北から
#:     「巨大時限爆弾X/復讐者の灯火 はありうる」と結論している）の**証人を捨てずに使う**だけ＝
#:     新しい仮定を足していない（既存推論が健全なら本推論も健全）。
#:   健全側の緩和：
#:     - 初期エリアが **None**（手先・従者＝ループ毎に脚本家が指定）のキャラは常に候補に残す
#:       （→60 A11＝手先がウィッチならボードXはループ毎に変わる）。
#:     - その役職のスロットが**2以上**の組（イレギュラーの枠外役職で2体になりうる）は
#:       **KB がどちらの初期エリアを X とするか決めない＝要確認**なので制約を適用しない。
B243_BOARD_X_ROLE: bool = False

#: ボードXを定める役職（`sim/state.py:511` と同じ対応表・KB は上の docstring 参照）。
_BOARD_X_ROLE_BY_RULE_Y: dict[str, str] = {
    "復讐者の灯火": "クロマク",
    "巨大時限爆弾Xの存在": "ウィッチ",
}


def _board_x_evidence(history: list[dict]) -> frozenset | None:
    """クリーン評価敗北から絞った **ボードX の候補ボード集合**（None＝証拠なし＝制約なし）。

    `_clean_defeat_constraints` と同じ分類（`_defeat_loop_classes`）を使う＝
    「盤面に帰責してよい敗北」の定義は単一ソース。
    ★負方向（生き延びたループ＝ボードX<2）は実装しない：主人公はループを1つ守れば
      その場でゲームが終わる（`sim/effects.py:694-698`＝`game_over`）＝観測時点として
      価値がない（推論が働くべき局面がもう無い）。
    """
    cls = _defeat_loop_classes(history)
    loop_board = cls["loop_board"]
    pos: set | None = None
    for lp in sorted(cls["defeat_loops"]):
        if lp in cls["dirty_loops"] or lp in cls["effect_end_loops"]:
            continue                       # 死亡・効果終了が絡む敗北は盤面に帰責しない
        board = loop_board.get(lp)
        if board is None:
            continue
        hot = {b for b, v in board.items() if v >= 2}
        if not hot:
            continue                       # このループの敗北は盤面ルールYでは説明されない
        pos = set(hot) if pos is None else (pos & hot)
    return frozenset(pos) if pos is not None else None


def _board_x_role_candidates(cast, boards: frozenset) -> frozenset:
    """ボードX候補 `boards` と初期エリア表から、ボードX役職の担い手候補を返す。"""
    out = set()
    for c in cast:
        area = initial_area_of(c)
        if area is None or area in boards:   # None＝手先/従者＝ループ毎指定＝絞れない
            out.add(c)
    return frozenset(out)


#: ★B-243（ユーザー実戦フィードバック §指摘3 の周辺）の切替口2＝**既定 OFF**。
#:   KB＝`rules/20_goodwill_abilities.md:228`「医者と同一エリアにいる**他のキャラ1人**を選び」
#:   ／`:297`「同一エリアの**自身以外**から不安1除去 or 不安1付与」（現物確認済）＝
#:   **医者の友好能力は医者自身に不安を置けない**（`sim/legal.py:186` も `n != "医者"` で一致）。
#:   ∴ 脚本家能力フェイズの不安イベントの **対象が医者自身** なら、その不安は医者起源では
#:   ありえず、**ミスリーダー／ファクター起源に確定**する＝present 制約をハードに使ってよい。
#:   現行は「医者が present に居て友好2以上」だけで曖昧扱いにして事象を丸ごと捨てており
#:   （`_doctor_ability_live` 系の3経路）、教材では **mm能力フェイズの不安8件中6件**が
#:   この理由で破棄されていた（対象は全件『医者』）。KB が一意に決める**可否**の取り違え。
B243_DOCTOR_SELF_TARGET: bool = True   # ★既定 ON（2026-08-17・ユーザー裁定「医者の件既定onで良いよ」）
#   ＝KB `rules/20_goodwill_abilities.md:228,297`「同一エリアの**自身以外**」を belief が
#   使っておらず、医者を対象にした不安の観測を「医者自身の友好能力かも」として捨てていた漏れの是正。
#   健全性違反0（3日級1037時点・5日級716時点）／6条件すべてで per-game 退行ゼロ／5日級 68→69。§72-44。


def _doctor_source_possible(e: dict, live: bool) -> bool:
    """この mm能力フェイズの不安イベントを『医者の友好能力起源でありうる』と見るか。"""
    if not live:
        return False
    if B243_DOCTOR_SELF_TARGET and e.get("target") == "医者":
        return False       # 医者は自分自身を対象に取れない（rules/20:228,297）
    return True


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
            # ★#11検死（belief崩壊・2026-07-26）：アルバイトは**特性死**（上のカウンター
            #   合計≥3で死亡・KB:30・ターン終了判定）がある＝役職説明（SK/キラー/ウイルス）を
            #   要求してはならない。deathイベントには goodwill/guard が乗らず「特性死か否か」を
            #   外形判定できないため、アルバイトの死は常に制約化しない（健全側＝真の世界を
            #   消さない）。実測＝random_BTX#11 L3D2 の特性死（3人以上の場×被害者暗躍0＝
            #   キラー枝も不成立）で全可能世界が矛盾し、L3D3以降 combos=0・p_future=0 の
            #   belief全崩壊（不変量「真配役・真ルールは消えない」の破れ）を起こしていた。
            if e.get("name") == "アルバイト":
                continue
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


def _lovers_pair_constraints(history: list[dict]) -> list[tuple[frozenset, str]]:
    """★B-85：「誰かが死亡 → 別の誰かに不安+6」＝ラバーズ対の**公開事実**（rules/50:153,159）。

    KB（50 ラバーズ／メインラバーズ・現物確認）：
      - 【強制】**メインラバーズが死亡した時**、ラバーズに不安カウンターを6つ置く。
      - 【強制】**ラバーズが死亡した時**、メインラバーズに不安カウンターを6つ置く。
      ＝**対の向きは両方向**＝「死んだ方」「受け取った方」のどちらがラバーズかは +6 だけでは決まらない。
      - 両者が同時死亡した場合は何も起きない（60:A19）＝観測自体が立たない＝制約も生まれない。

    よって健全（真を排除しない）に言えるのは**対の同一性**だけ：
      受け手Y ∈ {ラバーズ, メインラバーズ} かつ 相方（もう一方の役職）∈ その日の死者。
    向きは他の証拠（例：主人公死亡＝メインラバーズ【任意】の絞り込み）が決める。

    返り値: [(その日の死者の集合, +6の受け手), ...]
    ★死者が複数居る日は集合のまま渡す＝「相方はこの中の誰か」＝過剰確定を避ける。
    ★+6は恋愛風景の【強制】でしか発生しない（unrestのdelta==6を出す経路は他に無い）＝
      恋愛風景を持たない組でこの観測が立てば、その組は矛盾＝0（enumerator側で扱う）。
    """
    out: list[tuple[frozenset, str]] = []
    seen: set[tuple[frozenset, str]] = set()
    # (loop, day) ごとに、その日に観測された死者を集める（+6より前の死だけが相方たりうる）。
    for e in history:
        if not (e.get("event") == "unrest" and e.get("delta") == 6):
            continue
        td = (e.get("loop"), e.get("day"))
        recv = e.get("target")
        if not recv:
            continue
        dead_before = frozenset(
            d.get("name") for d in history
            if d.get("event") == "death" and (d.get("loop"), d.get("day")) == td
            and d.get("name") and d.get("name") != recv
        )
        if not dead_before:
            continue          # 相方が特定できない＝制約を作らない（健全側）
        key = (dead_before, recv)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# ★B-102：事件効果の公開結果から犯人を絞る（横断・2026-07-29）
# ---------------------------------------------------------------------------
# FS/BTX の全9事件を条文（rules/40:146-154 の事件表・rules/50:198,218）で分類した。
# 「公開情報だけで犯人について何が言えるか」は次の4型しかない：
#   mover          … 効果が**犯人自身を動かす**＝move イベントの主＝犯人。
#                    行方不明「犯人を任意のボードに移動させる」（40:153）。
#   victim         … 効果で**犯人自身が死ぬ**＝death イベントの主＝犯人。
#                    自殺「犯人は死亡する」（40:150）。
#   same_area      … 効果が**犯人と同エリアの犯人以外**を対象にする＝犯人∈死亡時の同席−死者。
#                    殺人事件「可能ならば犯人と同一のエリアにいる犯人以外の任意のキャラクター
#                    1人を死亡させる」（40:148）。
#   effect_present … 蝶の羽ばたき（50:218「犯人と同一のエリアにいるキャラクター1人」）＝
#                    **既存実装**（sim が incident_effect に present を載せる）。ここでは扱わない。
# 残る5事件は効果の対象が「任意のキャラクター」「暗躍≥2のキャラ」「病院にいる全員」「神社の
# ボード」＝**犯人の位置にも同一性にも触れない**＝公開情報から犯人について何も言えない。
#
# ---------------------------------------------------------------------------
# ★B-108：negative 方向（効果が出た → 犯人は黒猫ではない）を同じ横断表に足す（2026-07-30）
# ---------------------------------------------------------------------------
# 起票＝ユーザー実戦フィードバック「L1D4 キーパーソンが遠隔殺人で死んだのに犯人候補から
# 黒猫が消えていない」。B-102 は「positive 方向にだけ使う」ガードを置いたため、
# **『効果の発生を観測した』という強い根拠**を使えていなかった（＝取りこぼし）。
# B-102 の当時の理由は「イベントの不在に依拠する推論はログ形式変更に脆い」だったが、
# 本件は**不在ではなく発生の観測**＝根拠の強さが逆である。
#
# KB 根拠（一意に読める）：
#   `rules/30_characters.md:71-77`「黒猫の特性（現物確認済み）」
#     特性2＝**このキャラクターが犯人の事件の事件効果は「何も起きない」に変更される**。
#   `rules/60_faq_rulings.md:196`（E-7 の1番）＝どの事件でも効果は「何も起きない」に変更される
#     （発生宣言はされる）。
# ∴ **事件効果が実際に盤面に現れたなら、その事件の犯人は黒猫ではない**【ハード制約】。
#
# ★★逆は成り立たない＝**実装しない**。KB `60:E-7`「事件は発生したのに何も起きなかった」の
#   原因は黒猫を含めて5つある：
#     1 犯人が黒猫／2 殺人事件で同エリアに対象が居ない（40:148「可能ならば」）／
#     3 対象が不死＝タイムトラベラー（40:148 の★・50:192・60 A26）／
#     4 対象に護衛カウンター（死亡の代わりに護衛-1）／
#     5 病院の事件で病院に暗躍が無い（60 E-1）・遠隔殺人で暗躍2以上が居ない（40:152 条件文）。
#   ∴「効果が出なかった → 黒猫」は**誤り**。本実装は **効果を観測した方向の一方向のみ**で、
#   イベントの**不在**からは何も結論しない（B-102 の positive ガードもそのまま維持）。
#
# 表の第2要素＝「その事件の効果が**実際に解決した**ことの公開証拠となるイベント種」。
# 事件ごとに効果が違う＝証拠も違う（死亡・カウンター配置・移動）。`sim/effects.py:
# _apply_incident_effect` が `_pub`（＝卓上に見える公開イベント）で出すものだけを採る。
#   ★「何も起きなかった」通知（`incident_effect` の note）は**証拠にしない**
#     ＝上記5原因のどれでも出る（`sim/effects.py:383,406,457,469`）。
#   ★カウンター系（anyaku/unrest/goodwill）は **delta>0（＝配置）に限る**。
#     除去（流布の友好-2 等）は相手の残数が0なら盤面が動かず観測できない（00 カウンターは
#     0未満にならない）。配置なら必ず盤面に現れる。
#   ★死亡が護衛で肩代わりされた場合は `guard_consumed`（公開＝護衛カウンターが1つ減る）が
#     証拠になる（60 E-7 の4「厳密には護衛-1が起きている」）。
#   ★不死（タイムトラベラー）が対象の場合は `death_prevented` が**非公開**（`_sec`）＝
#     公開イベントが1つも出ない＝この経路は働かない（黒猫と区別できない＝正しい振る舞い）。
_INCIDENT_CULPRIT_INFERENCE: dict[str, tuple[str | None, tuple[str, ...]]] = {
    # 事件名: (positive＝犯人を名指しする型, negative＝「効果が解決した」公開証拠のイベント種)
    # 40:153 犯人を任意のボードに移動させる＋犯人のいるボードに暗躍+1（暗躍は必ず置かれる）
    "行方不明": ("mover", ("move", "anyaku")),
    # 40:150 犯人は死亡する
    "自殺": ("victim", ("death", "guard_consumed")),
    # 40:148 犯人と同一エリアの犯人以外1人を死亡
    "殺人事件": ("same_area", ("death", "guard_consumed")),
    # 50:218 犯人と同一エリアの1人にカウンター1つ（既存の incident_effect.present 経路）
    "蝶の羽ばたき": ("effect_present",
                     ("incident_effect", "anyaku", "unrest", "goodwill")),
    # 40:149 任意のキャラ2人（犯人と無関係）＝不安+2／暗躍+1
    "不安拡大": (None, ("unrest", "anyaku")),
    # 40:154 任意のキャラ2人（犯人と無関係）＝友好-2／友好+2（配置側のみ証拠）
    "流布": (None, ("goodwill",)),
    # 40:152 暗躍≥2の任意1人（犯人と無関係）★ユーザー報告（B-108）の事件
    "遠隔殺人": (None, ("death", "guard_consumed")),
    # 40:151 病院にいる全員（犯人と無関係）＋暗躍2以上なら主人公も
    "病院の事件": (None, ("death", "guard_consumed", "protagonist_death")),
    # 50:198 神社に暗躍+2（キャラを対象にしない）
    "邪気の汚染": (None, ("anyaku",)),
}

#: 黒猫（KB: 30「黒猫の特性」特性2）。役職ではなく**キャラ**＝配役に依らず常に同じ。
_KURONEKO = "黒猫"


def _occurred_incidents(history: list[dict]) -> dict[tuple, str]:
    """(loop, day) → 実際に発生した事件名（`occurs: True` の公開アナウンス）。"""
    out: dict[tuple, str] = {}
    for e in history:
        if e.get("event") == "incident" and e.get("occurs") and e.get("name"):
            out[(e.get("loop"), e.get("day"))] = e["name"]
    return out


def _is_effect_evidence(e: dict, kinds: tuple[str, ...]) -> bool:
    """イベント e が「事件効果が実際に解決した」ことの公開証拠か（kinds＝その事件の証拠種）。"""
    ev = e.get("event")
    if ev not in kinds:
        return False
    if ev in ("anyaku", "unrest", "goodwill"):
        return (e.get("delta") or 0) > 0      # 配置のみ（除去は残数0なら観測不能）
    if ev == "incident_effect":
        # 「何も起きなかった」通知は証拠にしない＝不在に依拠しないための要（★逆方向の防止）
        return bool(e.get("target") or e.get("present"))
    return True                                # death / guard_consumed / move / protagonist_death


def _incident_effect_observed_days(history: list[dict]) -> set[int]:
    """★B-108：**事件効果が実際に盤面に現れた**日の集合（黒猫を犯人候補から外すのに使う）。

    黒猫が犯人なら事件効果は「何も起きない」に変更される（KB: 30 特性2）＝
    ∴ 効果を観測できた日の犯人は黒猫ではない。**この一方向のみ**（逆は 60 E-7 より不成立）。

    ガード（B-102 と同じ思想）：
    - `phase == "incident"` のみ＝A.I. の友好能力による事件効果の再解決（phase=goodwill_ability）は
      犯人が別（60 E-6＝「発生」ともみなさない）＝混ぜない。
    - その (loop, day) の事件が `occurs: True` でアナウンスされていること。
    - 証拠は事件ごとの公開イベント種に限る（`_INCIDENT_CULPRIT_INFERENCE` の第2要素）。
    - ★イベントの**不在**は一切使わない（＝「効果が出なかった→黒猫」は実装しない）。
    """
    occurred = _occurred_incidents(history)
    days: set[int] = set()
    for e in history:
        if e.get("phase") != "incident":
            continue
        day = e.get("day")
        name = occurred.get((e.get("loop"), day))
        if day is None or not name:
            continue
        row = _INCIDENT_CULPRIT_INFERENCE.get(name)
        if row is not None and _is_effect_evidence(e, row[1]):
            days.add(day)
    return days


#: アルバイト系の犯人性は アルバイト⇄アルバイト？ で継承する（`sim/effects.py:_effective_culprit`
#: ／KB: 30 アルバイト？特性「犯人かどうかはアルバイトと一致」）＝観測された当人に確定させず
#: 両方を残す（健全側＝真の犯人を消さない）。
_ALUBAITO_PAIR = frozenset({"アルバイト", "アルバイト？"})


def _alubaito_relax(s: set) -> frozenset:
    return frozenset(s | _ALUBAITO_PAIR) if (s & _ALUBAITO_PAIR) else frozenset(s)


def _incident_effect_culprit_sets(history: list[dict]) -> list[tuple[int, frozenset]]:
    """★B-102：事件効果の**公開結果**から「その日の犯人はこの中」を読む。返り値 [(day, 候補集合)]。

    ユーザー報告（実戦棋譜 2026-07-29）＝「L1D2 行方不明（サラリーマンが移動した）を見ているのに
    犯人候補から黒猫が外れていない」。belief は蝶の羽ばたき（incident_effect.present）しか
    使っておらず、**効果そのものが犯人を名指しする型**（上の横断表）を取りこぼしていた。

    健全側のガード（真の犯人を消さないための条件。全て意図的）：
    - **positive 方向のみ**：効果が観測できた時だけ制約を作る。観測できない（黒猫が犯人＝
      「何も起きなかった」／護衛消費／不死で死ねない／対象候補が空）ケースから逆向きの
      結論は出さない。∴ 黒猫が犯人なら effect イベントが出ない＝この経路自体が働かない。
    - **`phase == "incident"` のみ**：A.I.の友好能力による事件効果の再解決
      （`sim/abilities.py`・phase=goodwill_ability）は犯人が別＝混ぜない。
    - **従者は使わない**：従者の身代わり死（KB: 30・`sim/effects.py:kill_character`）は
      **非公開**＝「死んだのは従者」でも真の対象は主（お嬢様/大物）でありうる。
      従者が絡む観測（death/move の主が従者）は丸ごと捨てる。
    - **アルバイト⇄アルバイト？は緩める**（`_alubaito_relax`）。
    """
    occurred = _occurred_incidents(history)
    out: list[tuple[int, frozenset]] = []
    seen: set[tuple[int, frozenset]] = set()
    for e in history:
        if e.get("phase") != "incident":
            continue
        _row = _INCIDENT_CULPRIT_INFERENCE.get(
            occurred.get((e.get("loop"), e.get("day"))) or "")
        kind = _row[0] if _row is not None else None
        ev, who, day = e.get("event"), e.get("name"), e.get("day")
        if not who or who == "従者" or day is None:
            continue
        if kind == "mover" and ev == "move":
            s = _alubaito_relax({who})           # 動かされた当人＝犯人
        elif kind == "victim" and ev == "death":
            s = _alubaito_relax({who})           # 死んだ当人＝犯人
        elif kind == "same_area" and ev == "death" and e.get("present"):
            rest = set(e["present"]) - {who}     # 犯人∈死亡時の同席の顔ぶれ−死者
            if not rest:
                continue
            s = _alubaito_relax(rest)
        else:
            continue
        key = (day, s)
        if key not in seen:      # 同一制約の再観測は情報を増やさない＝間引く
            seen.add(key)
            out.append(key)
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
                       ml_forced=frozenset(), kp_forced=frozenset(),
                       ml_strict=None, lovers_cons=(), gw_resolved=frozenset(),
                       non_ignore=(), bx=None, _memo=None):
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

    def _base_cwr(fx):
        return _combo_weight_with_refusals(cast, slots, fx, kp_shoujo, refused, _memo)

    # ★B-231（既定OFF）：妹の特性「友好無視役職にできない」（rules/30:66・脚本側は
    #   sim/state.py:238-243 の検証で強制済み＝真の配役は必ず満たす＝ハードで健全）。
    #   妹∈cast かつ友好無視系スロットのある組でだけ、妹の役職を「友好無視を持たない
    #   役職＋パーソン」に場合分けして数える（排反・完全＝厳密。スロット超過・少女制約
    #   等は _combo_weight_and_marginals が 0 で自然に落とす）。分岐が無い組・OFF 時は
    #   従来の base と完全同一（bit-for-bit）。
    # ★B-234（既定OFF・2026-08-17）：ご神木の「不発生」観測（`goshinboku_idle`）も
    #   **同じ形の制約**＝「このキャラの役職は友好無視系ではない」。呼び側（_recompute）が
    #   該当キャラ名を `non_ignore` で渡す＝ここは器を多キャラへ一般化しただけ。
    #   ★妹だけ（B-231 ON・B-234 OFF）のときの列挙順は導入前と完全同一＝bit-for-bit 不変。
    _ni_chars = [c for c in non_ignore if c in cast]
    if B231_IMOUTO_TRAIT and "妹" in cast and "妹" not in _ni_chars:
        _ni_chars = ["妹"] + _ni_chars
    _ni_roles = None
    if _ni_chars:
        _allowed = [r for r in sorted(slots) if r not in _IGNORE_ROLES]
        if len(_allowed) < len(slots):   # 友好無視系スロットが1つでも在る組だけ分岐
            _ni_roles = _allowed + [DEFAULT_ROLE]   # ＋パーソン（スロット外の既定役職）
        else:
            _ni_chars = []               # 分岐しても恒等＝従来の base 直行

    def base(fx):
        if not _ni_chars:
            return _base_cwr(fx)
        return _ni_step(fx, 0)

    def _ni_step(fx, i):
        if i >= len(_ni_chars):
            return _base_cwr(fx)
        ch = _ni_chars[i]
        r0 = fx.get(ch)
        if r0 is not None:               # 上流の制約が固定済み＝整合チェックのみ
            return (0, {}) if r0 in _IGNORE_ROLES else _ni_step(fx, i + 1)
        return _acc([_ni_step({**fx, ch: r}, i + 1) for r in _ni_roles])

    # ★B-101：拒否されずに解決した友好能力の行使キャラは**絶対友好無視の役職ではない**
    #   （00:174【必ず拒否する】/ 50:98-99 判定は「能力を使うキャラ」の役職）。
    #   該当役職（カルティスト/ウィッチ）の担い手【全員】を gw_resolved の外に置く＝
    #   `_sum_role_all_in` で場合分けして厳密に数える（スロット数2以上でも正しい）。
    #   ★通常の友好無視は【任意】＝見送れる＝制約にしない（片側だけがハード）。
    _abs_ignore_roles = (sorted(r for r in _ABSOLUTE_IGNORE_ROLES if r in slots)
                         if gw_resolved else [])

    def abs_ignore_step(fx, i=0):
        if i >= len(_abs_ignore_roles):
            return base(fx)
        role = _abs_ignore_roles[i]
        cand = set(cast) - set(gw_resolved)
        return _sum_role_all_in(cast, slots, kp_shoujo, refused, role, cand, fx,
                                lambda f: abs_ignore_step(f, i + 1))

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
            return abs_ignore_step(fx)
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

    def lovers_step(fx, i=0):
        """★B-85：死亡→不安+6 の対（rules/50:153,159）。対の同一性だけを課す（向きは不問）。"""
        if i >= len(lovers_cons):
            return cult_step(fx)
        dead, recv = lovers_cons[i]
        nxt = lambda f: lovers_step(f, i + 1)  # noqa: E731
        # +6 は恋愛風景の【強制】でしか起きない＝両役職の枠が無い組はこの観測を説明できない。
        if "ラバーズ" not in slots or "メインラバーズ" not in slots:
            return 0, {}
        branches = []
        # 向き①：受け手=メインラバーズ ∧ 死者(の誰か)=ラバーズ
        branches.append(_sum_role_in(
            cast, slots, kp_shoujo, refused, "メインラバーズ", {recv}, fx,
            lambda f: _sum_role_in(cast, slots, kp_shoujo, refused,
                                   "ラバーズ", set(dead), f, nxt)))
        # 向き②：受け手=ラバーズ ∧ 死者(の誰か)=メインラバーズ
        branches.append(_sum_role_in(
            cast, slots, kp_shoujo, refused, "ラバーズ", {recv}, fx,
            lambda f: _sum_role_in(cast, slots, kp_shoujo, refused,
                                   "メインラバーズ", set(dead), f, nxt)))
        return _acc(branches)

    def pd_step(fx, i=0):
        # 主人公死亡（ターン終了）＝キラー（暗躍≥4の中）∨ メインラバーズ（不安3暗躍1の中）
        if i >= len(pd_cons):
            return lovers_step(fx)
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
        has_ml = "ミスリーダー" in slots
        has_fa = "ファクター" in slots
        # ★B-61：ML専用証拠（学校暗躍<2＝ファクター起源が不可能な mm能力フェイズの不安）。
        #   弱証拠 ml_set は「ML **または** ファクター」なので両在組では使えないが、
        #   ml_strict は供給源がMLに一意＝両在組にも効き、ML不在組は矛盾にできる。
        _strict = ml_strict if _B61_SCOPE != "off" else None
        if _strict is not None:
            if not has_ml:
                # ファクターでも医者でも説明できない不安がある＝MLの居ない組は成立しない
                return 0, {}
            if _B61_SCOPE == "elim" or (_B61_SCOPE == "both" and not has_fa):
                _strict = None              # 消去だけ／両在組以外は present制約を使わない
        if _strict is not None:
            # 実効集合＝ファクター不在組では弱証拠 ml_set も ML の制約として使える（∩で最強）。
            # 両在組では ml_set がファクター起源を含みうる＝ml_strict 単独で使う。
            ml_eff = (_strict if (ml_set is None or has_fa)
                      else (ml_set & _strict))
            if has_fa:
                # ★B-18（単独present＝ML or ファクター確定）と併用する：
                #   容疑者が1人なら「その1人がML」／「その1人がファクター（＝MLは他の
                #   ml_strict 候補）」の**排反2枝**で厳密に数える（どちらの制約も落とさない）。
                cand_forced = ml_forced - set(fx)
                if len(cand_forced) == 1:
                    c = next(iter(cand_forced))

                    def _ml_elsewhere(f, _c=c):
                        return _sum_role_in(cast, slots, kp_shoujo, refused,
                                            "ミスリーダー", ml_eff - {_c},
                                            f, kp_step)

                    return _acc([
                        _sum_role_in(cast, slots, kp_shoujo, refused,
                                     "ミスリーダー", ml_eff & {c}, fx, kp_step),
                        _sum_role_in(cast, slots, kp_shoujo, refused,
                                     "ファクター", {c}, fx, _ml_elsewhere)])
            return _sum_role_in(cast, slots, kp_shoujo, refused,
                                "ミスリーダー", ml_eff, fx, kp_step)
        if ml_set is None:
            return kp_step(fx)
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

    # ★B-243（既定OFF）：ボードX役職（ウィッチ／クロマク）の担い手は「初期エリアが
    #   ボードX候補に入るキャラ」に限られる。bx=(役職名, 候補キャラの frozenset)。
    #   呼び側（_recompute）が rule_y とスロット数のゲートを済ませて渡す＝ここは器だけ。
    def bx_step(fx):
        if bx is None:
            return kp_forced_step(fx)
        _role, _cand = bx
        return _sum_role_all_in(cast, slots, kp_shoujo, refused, _role,
                                set(_cand), fx, kp_forced_step)

    return bx_step(fixed)


#: ★B-128（2026-08-01）の切替口＝既定ON。False で **bit-for-bit 旧挙動**へ復帰する。
#:   同一の脚本家能力フェイズに「ミスリーダーの追加能力でしか説明できない不安+1」が2件出たら、
#:   その能力の持ち主が2人居る＝ミスリーダーは人数上限1人（KB: 50:137-139・`sim/state.ROLE_MAX`）
#:   なので、もう1人はファクター（KB: 50:174,177）＝ファクターを追加するルールは
#:   **不定因子χ だけ**（KB: 50:88／逆引き 50:178）＝ルールXが確定する。
B128_COUNT_TO_FACTOR: bool = True

#: KB上のML能力保持者の最大数＝ミスリーダー1（上限1人）＋ファクター1（不定因子χの1枠）。
#: これを超える件数は KB 上あり得ない＝要求値をここでクランプする（詳細は
#: `_ml_ability_holders_needed` の docstring と `docs/監査_B128_*` §4-2）。
_B128_HOLDER_CAP = 2


def _ml_ability_holders_needed(history: list[dict]) -> tuple[int, bool]:
    """同一の脚本家能力フェイズ（loop×day で一意）の不安+1の件数から、
    **ミスリーダーの追加能力の持ち主が最低何人必要か**を返す（B-128）。

    根拠＝脚本家能力フェイズに `event="unrest", delta>0` を publish しうる主体は
    `sim/legal.mastermind_ability_options` の完全列挙より次の2種だけ：
      (a) `"ミスリーダー:{name}"`＝ミスリーダー、または **学校暗躍≥2 のファクター**
          （KB: 50:137-139 / 50:174,177）。**1キャラ1ターン1回**（`used` キー）。
      (b) `"医者:医者"`＝医者の友好能力（友好無視＋友好2・KB: 60 B-8）。**1ターン1回**。
    ∴ 同一フェイズの件数 n から医者ぶん（多くとも1件）を引いた残りは、
    (a) の持ち主の人数の下限になる。

    健全側の3つのガード（真の配役を消さない＝可能世界0を作らない）：
      1. 医者は `_doctor_ability_live`（B-25 と同一判定＝医者が present かつその時点の友好≥2）
         で「使えた」ときだけ1件を差し引く。新しい例外機構は作らない。
      2. **学校暗躍<2 のフェイズでは制約を作らない**（ファクターがML能力を持てない＝
         KB上どの世界でも2件を説明できない＝制約を掛けると全世界が消える）。要確認として
         anomaly を立てるに留める。
      3. **3件以上は KB 上あり得ない**（上限2）。素直に要求すると全世界が消えるので
         `_B128_HOLDER_CAP` にクランプする（`need>=3` の含意は `need>=2` の含意を必ず含む
         ＝より弱い結論に落とすのは常に安全側）。同じく anomaly を立てる。

    返り値: (need, anomaly)
      need    ＝ 全フェイズを通じた必要人数の最大（0/1＝新しい情報なし＝制約なし）。
      anomaly ＝ クランプ or 「学校暗躍<2 なのに2件以上」を実際に観測した（＝要確認）。
    """
    doc_live = _doctor_ability_live(history)
    fa_live = _school_anyaku_live(history)
    per_phase: dict[tuple, list[int]] = {}
    for i, e in enumerate(history):
        if (e.get("phase") != "mastermind_ability" or e.get("event") != "unrest"
                or e.get("delta", 0) <= 0):
            continue
        # ★loop/day が揃うイベントだけ「同一フェイズ」に数える（旧ログ/合成イベントで
        #   loop/day 欠落＝(None,None) に誤集約して件数が暴発するのを防ぐ＝健全側。
        #   `anyaku_per_phase`（同型の暗躍側の実装）と同じ流儀）。
        if e.get("loop") is None or e.get("day") is None:
            continue
        per_phase.setdefault((e["loop"], e["day"]), []).append(i)
    need = 0
    anomaly = False
    for idxs in per_phase.values():
        # 医者の友好能力は1ターン1回＝多くとも1件しか説明できない（B-25 と同じ判定関数）
        doc_cap = 1 if any("医者" in set(history[i].get("present") or ()) and doc_live[i]
                           for i in idxs) else 0
        raw = len(idxs) - doc_cap
        if raw < 2:
            continue
        if not any(fa_live[i] for i in idxs):
            anomaly = True      # ガード2＝ファクターがML能力を持てない局面＝要確認（制約にしない）
            continue
        if raw > _B128_HOLDER_CAP:
            anomaly = True      # ガード3＝KB上あり得ない件数＝要確認（クランプして使う）
        need = max(need, min(raw, _B128_HOLDER_CAP))
    return need, anomaly


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
            if (e.get("present") and "医者" in e["present"]
                    and not (B243_DOCTOR_SELF_TARGET and e.get("target") == "医者")):
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
    # ★B-128：不安側の同型（件数→供給源の人数）。上の rumor_and_kuromaku が暗躍側で
    #   既に持っている推論を、不安側にも入れる（詳細＝_ml_ability_holders_needed）。
    sig["ml_unrest_need"], sig["b128_anomaly"] = _ml_ability_holders_needed(history)
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
    # ★B-128：同一mm能力フェイズにML能力由来の不安が need 件＝その能力の持ち主が need 人必要。
    #   ミスリーダーは人数上限1人（ROLE_MAX）・ファクターは不定因子χの1枠＝need=2 は実質
    #   「ミスリーダー在 かつ ファクター在」＝**不定因子χ∈rule_X の確定**になる。
    #   ★スロット数の和で書くのは、イレギュラー展開（_expand_irregular）で枠外役職として
    #     ファクターが足された組も正しく拾うため（`不定因子χ ∈ rule_xs` で書くと取りこぼす）。
    need = sig.get("ml_unrest_need", 0)
    if B128_COUNT_TO_FACTOR and need >= 2:
        if slots.get("ミスリーダー", 0) + slots.get("ファクター", 0) < need:
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
    # ★モジュール全体のトグル（_recompute の結果を変える）を署名に含める＝トグルを切り替えた
    #   テスト/掃引で**前のトグルの結果を引く取り違え**を塞ぐ（B-101 で追加。B-61 も同型）。
    h.update(f"|B61={_B61_SCOPE}|B101={_B101_SCOPE}"
             f"|B128={int(B128_COUNT_TO_FACTOR)}"
             f"|B188={int(B188_CONTRACT_SHOUJO_ELIM)}"
             f"|B243bx={int(B243_BOARD_X_ROLE)}"
             f"|B243doc={int(B243_DOCTOR_SELF_TARGET)}"
             f"|B231={int(B231_IMOUTO_TRAIT)}"
             f"|B234={int(B234_GOSHINBOKU_TRAIT)}".encode("utf-8"))
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
        # ★B-113：噂消費証明のメモ（observe ごとにクリア。キー=(loop, len(history))）
        self._rumor_spent_cache: dict = {}
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
        self._rumor_spent_cache = {}   # ★B-113：履歴が差し替わった＝メモ無効化
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
        # ★T3：開示済みルールXは全件（frozenset）＝BTX で2件開示なら両方を含む組だけ残す。
        rule_xs_revealed = _revealed_rule_xs(self._history)
        elim_y = _rule_y_eliminations(self._history)
        sig = _mm_phase_signals(self._history)
        refused = _refused_chars(self._history)
        # ★B-234（既定OFF）：ご神木の特性の観測を両方向とも制約に落とす（`_GOSHINBOKU ∈ cast` で短絡）。
        #   - 発生（脚本家能力フェイズの `goshinboku_move`）⇒ ご神木 ∈ 友好無視系
        #     ＝`refused`（「このキャラの役職は友好無視系」）と**同じ意味の制約**＝器を相乗り。
        #   - 不発生（`goshinboku_idle`）⇒ ご神木 ∉ 友好無視系 ＝ `non_ignore`（B-231 の器）へ。
        #   ★両立は原理的に起こらない（役職はゲームを通じて不変）。万一同時に立ったら sim か
        #     どちらかの演繹が壊れている＝**発生（実イベントの直接証拠）を優先し不発生を捨てる**
        #     ＝可能世界を全滅させない安全側（プローブ `both_signals` が検出器として数える）。
        non_ignore: tuple = ()
        if B234_GOSHINBOKU_TRAIT and _GOSHINBOKU in self.cast:
            _g_moved, _g_idle = _goshinboku_signals(self._history)
            if _g_moved:
                refused = set(refused) | {_GOSHINBOKU}
            elif _g_idle:
                non_ignore = (_GOSHINBOKU,)
        # ★B-101（既定OFF）：拒否されずに解決した友好能力＝行使キャラは絶対友好無視ではない。
        gw_resolved = (frozenset(_gw_resolved_chars(self._history))
                       if _B101_SCOPE != "off" else frozenset())
        kuro_set, ml_set, board_sets = _public_role_constraints(self._history, self.cast)
        kuro_unions = _phase_kuromaku_unions(self._history, self.cast)
        ml_forced = _ml_forced(self._history, self.cast)
        ml_strict = _ml_strict(self._history, self.cast)   # B-61：ML専用のpresent制約
        allowed_y_sets, tt_required = _clean_defeat_constraints(self._history)
        # ★B-243（既定OFF）：クリーン評価敗北 → ボードX の候補ボード → その役職
        #   （ウィッチ／クロマク）の担い手候補（初期エリア表が一意に決める＝KB接地）。
        _bx_boards = _board_x_evidence(self._history) if B243_BOARD_X_ROLE else None
        _bx_chars = (_board_x_role_candidates(self.cast, _bx_boards)
                     if _bx_boards is not None else None)

        def _bx_for(combo):
            """この combo に効く (役職名, 候補キャラ) を返す（効かないなら None）。"""
            if _bx_chars is None:
                return None
            role = _BOARD_X_ROLE_BY_RULE_Y.get(combo["rule_y"])
            # スロット1の組だけ＝2体（イレギュラーの枠外役職）では KB が X を決めない＝要確認
            if role is None or combo["slots"].get(role, 0) != 1:
                return None
            return (role, _bx_chars)
        death_cons = _turn_end_death_constraints(self._history)
        pd_cons = _protagonist_death_constraints(self._history)
        cult_sets = _cultist_constraints(self._history)
        lovers_cons = _lovers_pair_constraints(self._history)   # ★B-85
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
            # 開示集合 ⊆ 組のルールX でなければ除外（1件開示なら従来と同一の述語）
            if rule_xs_revealed and not rule_xs_revealed.issubset(combo["rule_xs"]):
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
                kp_forced=_kp_forced_here, ml_strict=ml_strict,
                lovers_cons=lovers_cons, gw_resolved=gw_resolved,
                non_ignore=non_ignore, bx=_bx_for(combo), _memo=_cwm_memo)
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
        # ★B-102：効果そのものが犯人を名指しする型（行方不明＝移動した当人／自殺＝死んだ当人／
        #   殺人事件＝同席の顔ぶれ−死者）。横断表＝_INCIDENT_CULPRIT_INFERENCE。
        eff_culprit = _incident_effect_culprit_sets(self._history)
        # ★B-108：効果が実際に盤面に現れた日＝その日の犯人は黒猫ではない（KB: 30 黒猫 特性2）。
        #   ★一方向のみ＝「効果が出なかった→黒猫」は実装しない（KB: 60 E-7＝他に4原因）。
        effect_seen_days = _incident_effect_observed_days(self._history)
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
            for d, cs in eff_culprit:      # ★B-102
                if d == day:
                    s &= cs
            if day in effect_seen_days:    # ★B-108
                s.discard(_KURONEKO)
            cand[day] = s
        for _ in range(len(cand)):
            singles = {next(iter(s)) for s in cand.values() if len(s) == 1}
            for day, s in cand.items():
                if len(s) > 1:
                    cand[day] = s - singles
        return cand

    def rumor_spent_this_loop(self, loop: int | None = None) -> bool:
        """★B-113：『不穏な噂』（任意のボード1つに暗躍+1・**1/loop**＝rules/40:61／
        rules/50:75）が**このループで既に消費された**ことを、公開情報から KB 上
        証明できるときだけ True を返す。

        用途＝DP-6 の収支式 `defense_plan.unstoppable_supply_gap(rumor_left=...)` への注入
        （True → rumor_left=0 ＝「現在値1の板」が暗躍禁止で防御可能に戻る帯）。

        ★安全側の設計（B-108 の作法＝**発生の観測だけ**を根拠にする）：
        - 証明できなければ **False（＝残弾あり扱い＝防御不能側に倒す）**。
          「残弾なし」と誤確定する方向の誤りは防御を1本失うため絶対に避ける。
        - イベントの**不在**（噂がまだ見えない等）からは何も結論しない。
        - belief が矛盾状態（可能世界0）なら棄権＝False。

        証明チャネル（mm能力フェイズの暗躍ソースはクロマクと噂の2つだけ＝legal.py 正典）：
        (1) このループのボード暗躍イベントで、クロマク由来でありうる供給者候補
            （present∪大物投射＝`_mm_board_supply_candidates`）の**全員が belief 上
            P(クロマク)=0**（＝可能世界の完全列挙で0）＝クロマクでは説明不能＝噂で確定。
            present=[]（盤上に誰も居ないボードへの暗躍）の決定的証拠（AIC検死 seed8・
            `_public_role_constraints` の既存注記）はこの特殊例として含まれる。
        (2) このループの**同一 mm能力フェイズに暗躍イベントが2つ以上**＝クロマクは
            1体まで・1フェイズ1回まで＝少なくとも1つは噂（B-2 `rumor_and_kuromaku` と
            同じ根拠）。場所を問わず「このループで噂が1発消費された」ことが確定する。

        loop＝現在のループ番号（呼び出し側の view["loop"]）。None なら履歴中の最大 loop
        （＝最後に観測したループ）を使う。履歴の loop と一致しない値を渡しても
        該当イベントが無い＝False（健全側）になるだけ。
        """
        if loop is None:
            loops = [e.get("loop") for e in self._history
                     if e.get("loop") is not None]
            if not loops:
                return False
            loop = max(loops)
        key = (loop, len(self._history))
        hit = self._rumor_spent_cache.get(key)
        if hit is not None:
            return hit
        res = self._rumor_spent_proof(loop)
        self._rumor_spent_cache[key] = res
        return res

    def _rumor_spent_proof(self, loop: int) -> bool:
        from engine.board import AREAS
        if self._total <= 0:
            return False        # 矛盾状態＝棄権（残弾あり側に倒す）
        marg = self.role_marginals()

        def _kuro_possible(name: str) -> bool:
            # role_marginals は cnt>0 の役職しか載せない＝ get()==0.0 は
            # 「可能世界の完全列挙でクロマク割り当てが1つも無い」ことの厳密判定。
            return marg.get(name, {}).get("クロマク", 0.0) > 0.0

        anyaku_per_day: Counter = Counter()
        for e in self._history:
            if (e.get("phase") != "mastermind_ability"
                    or e.get("event") != "anyaku"
                    or e.get("delta", 0) <= 0 or e.get("loop") != loop):
                continue
            if e.get("day") is not None:
                anyaku_per_day[e["day"]] += 1
            if e.get("target") not in AREAS:
                continue        # キャラ暗躍＝クロマク由来（噂はボード専用）＝帰属不要
            present = e.get("present")
            if present is None:
                continue        # 旧ログ（present欄なし）＝帰属しない（健全側）
            cands = _mm_board_supply_candidates(e, self.cast)
            if not any(_kuro_possible(n) for n in cands):
                return True     # チャネル(1)：クロマクでは説明不能＝噂で確定
        # チャネル(2)：同一フェイズに暗躍イベント≥2＝少なくとも1つは噂（B-2）
        return any(cnt >= 2 for cnt in anyaku_per_day.values())

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
