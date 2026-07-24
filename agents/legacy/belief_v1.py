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

from engine.data import is_shoujo
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
        for kp in _keyperson_deaths(self._history):
            revealed.setdefault(kp, "キーパーソン")
        rule_x = _revealed_rule_x(self._history)
        elim_y = _rule_y_eliminations(self._history)

        combos: list[dict] = []
        weights: list[int] = []
        agg: dict[str, Counter] = {c: Counter() for c in self.cast}
        for combo in self._all_combos:
            if combo["rule_y"] in elim_y:
                continue
            if rule_x is not None and rule_x not in combo["rule_xs"]:
                continue
            w, marg = _combo_weight_and_marginals(
                self.cast, combo["slots"], revealed, combo["kp_shoujo"])
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
