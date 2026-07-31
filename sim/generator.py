"""ランダムな正規脚本の生成（プレイの登場人物・ルールを毎回変えるため）。

ルールを無作為に選び、そのルールが要求する役職を、無作為に選んだキャストへ割り当てる。
validate_script を必ず通る脚本だけを返す（捏造せず、生成に失敗したら別seedで再試行）。

対象キャストは「初期エリア固定・sim が確実に裁定できる」キャラに限定（登場日つき＝転校生/神格、
初期エリアが脚本家指定＝手先/従者、被セット不可の幻想、未実装特性の拡張キャラ等は除外）。
"""

from __future__ import annotations

import random
from collections import Counter

from engine.data import SHOUJO, role_has_friendship_ignore

from .state import (
    BTX_INCIDENTS,
    BTX_RULE_X_ROLES,
    BTX_RULE_Y_ROLES,
    DEFAULT_ROLE,
    FS_INCIDENTS,
    FS_RULE_X_ROLES,
    FS_RULE_Y_ROLES,
    ROLE_MAX,
    Incident,
    Script,
    validate_script,
)

# 生成に使う安全なキャスト（初期エリア固定・特性/能力がsimで扱える）。
# ★2026-07-10：7キャラを追加統合（転校生/神格=登場タイミングは既定＝1日目/1ループ目で登場、
#   手先=初期エリアは脚本家が毎ループ指定、妹/コピーキャット/女の子/教祖=友好能力・特性を実装）。
SAFE_CAST_POOL: tuple[str, ...] = (
    "男子学生", "女子学生", "お嬢様", "委員長", "巫女", "刑事", "サラリーマン",
    "情報屋", "医者", "入院患者", "アイドル", "マスコミ", "教師", "軍人",
    "イレギュラー", "異世界人", "大物", "鑑識官", "ナース", "黒猫", "学者",
    "幻想", "A.I.",  # 能力・特性を実装済み（幻想=被セット不可＋ボード読み替え、A.I.=事件効果解決）
    "転校生", "神格", "手先", "妹", "コピーキャット", "女の子", "教祖",
    "ご神木",  # 特性（カウンター移動）は実装済み・友好能力なし・神社固定（2026-07-10）
    "従者", "アルバイト", "アルバイト？",  # 追随/パーソン化・死亡連鎖を実装（2026-07-10）
)

# ★出現率を抑えるキャラ（テスター知見 2026-07-08）：一様抽選で引かれても確率 p で
#   採用を取り消し別キャラへ差し替える＝実効出現率を p 倍にする。大物は不安臨界4で事件を
#   まず起こせず、テリトリーの選択肢だけ増える＝「入れる理由がなければ入れない」（KB:70でも
#   導入難度★5）。除外まではせず自然出現率の半分に（ユーザー指示 2026-07-08）。
RARE_CAST_RATE: dict[str, float] = {"大物": 0.5}

# ★初心者向け（メンバー偏り）モードのティア分け（ユーザー指定 2026-07-10）。
#   ほとんど Normal から選抜／Rare から1-2名／SR は4ゲームに1人（≈25%）。
#   ※sim が特性・能力を実装済みのキャスト（SAFE_CAST_POOL）に限る＝ユーザーの完全な
#     プール（転校生/ご神木/アルバイト/従者/教祖/神格/妹/コピーキャット/女の子/手先/
#     上位存在/幻想の一部）のうち未実装キャラは出せない（実装追加で拡張予定）。
BEGINNER_NORMAL: tuple[str, ...] = (
    "お嬢様", "刑事", "サラリーマン", "巫女", "女子学生", "男子学生", "情報屋",
    "医者", "入院患者", "アイドル", "教師", "異世界人", "軍人", "マスコミ",
    "ナース", "委員長", "鑑識官",
)
# ★2026-07-10：実装済みの7キャラをユーザーのティア指定どおり Rare/SR に追加。
BEGINNER_RARE: tuple[str, ...] = (
    "大物", "黒猫", "幻想", "イレギュラー",
    "転校生", "妹", "コピーキャット", "女の子", "手先",
)
BEGINNER_SR: tuple[str, ...] = ("A.I.", "学者", "神格", "教祖",
                                "ご神木", "従者", "アルバイト", "アルバイト？")
BEGINNER_SR_RATE = 0.25   # 4ゲームに1人くらい

# ---------------------------------------------------------------------------
# ★スタンダードメンバー（B-53b・ユーザー要望 2026-07-26）＝メンバー調整より**さらに絞った**
#   コモンプール。「もっとキャラクターメンバーを絞ったコモンプールからしか出ないスタンダード
#   メンバー脚本が欲しい」。
#
# 選定基準＝**KB 70「キャラ導入難度（★）の目安」の★1・★2だけ**（単一の客観規準）。
#   KB 70 原文：「脚本を作り慣れないうちは★1か★2を中心に使い、必要なら★3を入れる程度に」
#              「★4以上は、なぜそのキャラを使うのかはっきり説明できるのでない限り使わない」
#   ＝★1/★2の14体＋★3の5体（異世界人/黒猫/マスコミ/鑑識官/手先＝神社の人数是正のユーザー指示 2026-07-26）＝19体（採否根拠は
#     docs/監査_B53b_スタンダードメンバー_2026-07-26.md の表）。
#   この規準は副次的に、⚠警告特性キャラ（学者/従者/アルバイト/アルバイト？/コピーキャット/
#   ご神木/大物＝`engine.data.UNMODELED_TRAIT_WARNINGS`）・変則キャラ（幻想/イレギュラー/
#   A.I./黒猫/転校生/神格/手先＝登場日・配役制約・被セット不可・臨界0等）・[拡張]カード
#   （ご神木/アルバイト/上位存在/従者/妹/女の子/教祖/コピーキャット/アルバイト？）を
#   **すべて自動的に落とす**（★3以上 or [拡張] のため）＝要望どおりの「定番だけ」になる。
#
# ★編集容易性：**プールはこの定数1つ**。入れ替えは1行足す/消すだけ（生成ロジックは触らない）。
#   ただし SAFE_CAST_POOL の部分集合であること（sim が特性・能力を裁定できる保証）＝
#   `tests/test_generator.py::test_standard_cast_pool_is_curated_subset` が固定する。
# ---------------------------------------------------------------------------
STANDARD_CAST_POOL: tuple[str, ...] = (
    # 学校（KB 30 初期エリア）
    "男子学生",     # ★1 学生事件の抑止・名脇役
    "女子学生",     # ★1 学生事件の抑止・少女＝KP候補
    "お嬢様",       # ★2 不安臨界1＝序盤の事件を作りやすい
    "委員長",       # ★1 学校の人数合わせ・盤面影響は小
    "教師",         # ★2 学生の役職開示
    # 神社
    "巫女",         # ★2 神社の暗躍除去・役職開示
    "異世界人",     # ★3 神社勢（ユーザー指示 2026-07-26＝神社が巫女だけで減りすぎるため★3を追加）
    "黒猫",         # ★3 神社勢・臨界0＝ループ開始時神社暗躍+1（simは裁定済み）
    # 都市
    "刑事",         # ★1 犯人開示／護衛付与
    "サラリーマン",  # ★1 「迷ったら採用で無難」＝主人公が未経験なら必ず採用（KB 70）
    "情報屋",       # ★2 ルールX開示
    "アイドル",     # ★2 柔軟（不安下げ/友好上げ）
    "マスコミ",     # ★3（ユーザー指示 2026-07-26）
    "鑑識官",       # ★3（ユーザー指示 2026-07-26）
    # 初期エリア指定制
    "手先",         # ★3 各ループ脚本家が初期エリア指定（ユーザー指示 2026-07-26）
    # 病院
    "医者",         # ★2 事件を止める側の要
    "入院患者",     # ★1 友好能力なし・病院固定＝人数合わせ
    "ナース",       # ★2 「無難になった医者」
    "軍人",         # ★2 不安+2／主人公不死
)


# ---------------------------------------------------------------------------
# 日数別の既定（B-53・2026-07-26）：3/4/5/6日の脚本を扱えるようにする。
# ★据え置き契約：**3日級・5日級の生成コーパスは1ビットも動かさない**（正典ベンチの脚本が
#   変わると 126/2.385（3日）・62/3.114（5日）が意味を失う）。そのため days<=5 は
#   従来の式（1〜min(days,3)件）と rng の消費順まで完全に同一にし、外挿するのは days>=6 のみ。
# ---------------------------------------------------------------------------
# UIが提示する日数の選択肢と既定（B-53b・2026-07-26）。UI側で重複定義するとズレるのでここが単一ソース。
#   根拠＝KB 40「FS推奨：1ループ3〜6日」／KB 70「BTX標準5〜7日（迷うなら6日）」＋
#   「8日以上はゲームが長すぎる」＝上限6。既定3＝既存の生成・保存の後方互換（欠落＝3）。
DAY_OPTIONS: tuple[int, ...] = (3, 4, 5, 6)
DEFAULT_DAYS: int = 3


def incident_count_range(days: int) -> tuple[int, int]:
    """1ループの事件数の (下限, 上限)。

    骨格＝「1日1事件まで」（同一日の複数事件は非対応＝`validate_script`）。
    - days<=5：`(1, min(days, 3))`＝**従来の実装と完全に同一**（据え置き契約）。
    - days>=6：`(2, 4)`＝KB からの外挿。
      * KB 70「ループ回数の見積もり表」（事件が3つ以下＝−0.4／5つ以上＝+0.4）＝
        標準帯は4件前後＝上限4は「5つ以上＝難度↑」に踏み込まない保守側。
      * KB 70「日数を決める」（BTX標準5〜7日・迷うなら6日）＝6日は標準的な長さで、
        事件1件だけでは事件フェイズがほぼ空になり、3日級で最大3件＝「ほぼ毎日事件が
        起こりうる」現行の密度設計から外れる＝下限2。
    ★KBに無い新要素は作らない（事件そのものは FS/BTX の既存プールから選ぶ）。
    """
    if days >= 6:
        return 2, 4
    return 1, min(days, 3)


def recommended_loops(days: int) -> int:
    """日数に対する推奨ループ回数（KB 70「ループ回数を決める」：標準3〜5・迷うなら4）。

    - days<=3：3（＝`random_script` の現行既定。3日級コーパスはこの値で確定済み）。
    - days>=4：4（見積もり表の日数項＝5日以下 −0.6／6日 −0.2＝日数が伸びるほど目安は
      大きい。ただしKBは「5以上はゲームが長すぎないか配慮」＝6日でも4に丸める。
      リポジトリ内の既存慣行とも一致＝`arena/crosscheck.py`・`arena/counterfactual.py`
      は5日級を loops=4 で生成している）。

    ★これは**呼び出し側が明示的に使う推奨値**であって `random_script` の既定ではない
      （既定を days 依存にすると神格の登場ループ抽選が変わり、5日級の正典コーパスが
      動いてしまう＝据え置き契約に反する）。
    """
    return 3 if days <= 3 else 4


def _beginner_cast(rng: random.Random, size: int) -> list[str]:
    """初心者向けの偏りキャスト：Normal 中心＋Rare 1-2＋SR は確率 BEGINNER_SR_RATE で1名。"""
    n_sr = 1 if rng.random() < BEGINNER_SR_RATE else 0
    n_rare = min(rng.randint(1, 2), len(BEGINNER_RARE), max(0, size - 1 - n_sr))
    n_normal = max(0, size - n_sr - n_rare)
    cast = list(rng.sample(BEGINNER_NORMAL, min(n_normal, len(BEGINNER_NORMAL))))
    cast += list(rng.sample(BEGINNER_RARE, n_rare))
    if n_sr:
        cast += list(rng.sample(BEGINNER_SR, 1))
    rng.shuffle(cast)
    return cast


def _required_roles(rng: random.Random, slots: Counter) -> list[str]:
    """役職スロット→必要配役の並び（マイナスは0〜スロット数で無作為、他は人数上限で頭打ち）。"""
    out: list[str] = []
    for role, n in slots.items():
        if role == "マイナス":
            n = rng.randint(0, n)
        else:
            n = min(n, ROLE_MAX.get(role, n))
        out.extend([role] * n)
    return out


def _try_build(set_name: str, rng: random.Random, loops: int, days: int,
               beginner: bool = False, standard: bool = False) -> Script:
    if set_name == "FS":
        rule_y = rng.choice(list(FS_RULE_Y_ROLES))
        rule_x = rng.choice(list(FS_RULE_X_ROLES))
        rule_x2 = None
        slots = Counter(FS_RULE_Y_ROLES[rule_y]) + Counter(FS_RULE_X_ROLES[rule_x])
        inc_names = sorted(FS_INCIDENTS)
    else:
        rule_y = rng.choice(list(BTX_RULE_Y_ROLES))
        rule_x, rule_x2 = rng.sample(list(BTX_RULE_X_ROLES), 2)
        slots = (Counter(BTX_RULE_Y_ROLES[rule_y]) + Counter(BTX_RULE_X_ROLES[rule_x])
                 + Counter(BTX_RULE_X_ROLES[rule_x2]))
        inc_names = sorted(BTX_INCIDENTS)

    required = _required_roles(rng, slots)
    # ★B-53b：抽選母集団はモードで切り替える（standard＝スタンダードメンバーのみ）。
    #   standard=False では `_pool is SAFE_CAST_POOL`＝**従来と完全に同一の式**（rng消費順も不変）。
    _pool = STANDARD_CAST_POOL if standard else SAFE_CAST_POOL
    cast_size = min(len(_pool), max(len(required) + rng.randint(1, 2), 6))
    if beginner and not standard:
        cast = _beginner_cast(rng, cast_size)   # Normal中心＋Rare1-2＋SR≈1/4
    else:
        # ★standard=True はここ（一様抽選）を通る＝ティア表（BEGINNER_*）は使わない。
        #   BEGINNER_RARE/SR には変則キャラが入っており、標準プールの意味を壊すため。
        cast = rng.sample(_pool, cast_size)

        # ★出現率抑制：引かれても確率 (1-p) で別キャラへ差し替える＝実効出現率を p 倍に。
        #   （standard プールに大物は居ない＝この節は空回り。プールを編集して戻した時のため残す。）
        for rare, rate in RARE_CAST_RATE.items():
            if rare in cast and rng.random() >= rate:
                alt_pool = [c for c in _pool
                            if c not in cast and c not in RARE_CAST_RATE]
                if alt_pool:
                    cast[cast.index(rare)] = rng.choice(alt_pool)

        # ★B-50 段階B：アルバイト⇔アルバイト？は**対**（アルバイト死亡→アルバイト？登場の連鎖が
        #   成立するには両方が要る）。片方だけ引かれたら相方を同伴（枠が無い稀ケースは単独を外す）。
        #   （standard プールには両方居ない＝同様に空回り。）
        _has_ab = "アルバイト" in cast
        _has_q = "アルバイト？" in cast
        if _has_ab != _has_q:
            if len(cast) < len(_pool):
                cast.append("アルバイト？" if _has_ab else "アルバイト")
            else:
                cast.remove("アルバイト" if _has_ab else "アルバイト？")

    # 役職の割り当て（僕と契約のキーパーソンは少女限定）
    # ★イレギュラーの特性（現物カード再確認 2026-07-08）：ルール追加役職は不可・かつ
    #   パーソンにもならない＝「選ばれたルールが追加しない役職のいずれか」を必ず配役する。
    # ★アルバイト/アルバイト？は配役無視でパーソン化＝プロット役職を割り当てない（除外）。
    unassigned = [c for c in cast if c not in ("イレギュラー", "アルバイト", "アルバイト？")]
    rng.shuffle(unassigned)
    # ★A.I.の特性①（現物カード確認 2026-07-08）：パーソンにできない＝プロット役職を
    #   必ず受け取るよう割り当ての先頭へ（受け取れない場合は下でキャストから外す）。
    if "A.I." in unassigned:
        unassigned.remove("A.I.")
        unassigned.insert(0, "A.I.")
    need_shoujo_kp = rule_y == "僕と契約しようよ！"
    roles: dict[str, str] = {}
    for role in required:
        if role == "キーパーソン" and need_shoujo_kp:
            avail = [c for c in unassigned if c in SHOUJO]
            if not avail:
                raise ValueError("少女のキーパーソンを確保できない")
            pick = rng.choice(avail)
        else:
            # ★妹の特性：友好無視/絶対友好無視を持つ役職（キラー/クロマク/カルティスト等）に
            #   配役できない（validate_script と対称）＝そのロールの候補から妹を除外。
            avail = unassigned
            if role_has_friendship_ignore(role):
                avail = [c for c in unassigned if c != "妹"]
            if not avail:
                raise ValueError("プロット役職を割り当てるキャストが不足")
            pick = avail[0]
        unassigned.remove(pick)
        roles[pick] = role

    # A.I.がプロット役職を受け取れなかった（例：唯一の役職が少女限定KP）＝特性違反になる
    #   のでキャストから外す。イレギュラーには「ルールが追加しない役職」を必ず1つ配る。
    if "A.I." in cast and "A.I." not in roles:
        cast = [c for c in cast if c != "A.I."]
    if "イレギュラー" in cast:
        from .state import BTX_ROLE_UNIVERSE, FS_ROLE_UNIVERSE
        universe = set(FS_ROLE_UNIVERSE if set_name == "FS" else BTX_ROLE_UNIVERSE) - {"パーソン"}
        pool = sorted(universe - set(slots))
        if pool:
            roles["イレギュラー"] = rng.choice(pool)
        else:  # 非追加役職が残らない稀な構成＝イレギュラー自体を外す（特性を守れない）
            cast = [c for c in cast if c != "イレギュラー"]

    # ★コピーキャットの特性：他キャラ1人と同じ役職になる（人数上限無視・スロットを埋めない）。
    #   コピー元＝非パーソンの役職を持つ他キャラから抽選。居なければキャストから外す。
    if "コピーキャット" in cast and "コピーキャット" not in roles:
        sources = [n for n in roles if n not in ("コピーキャット", "イレギュラー")
                   and roles[n] != DEFAULT_ROLE]
        if sources:
            roles["コピーキャット"] = roles[rng.choice(sources)]
        else:
            cast = [c for c in cast if c != "コピーキャット"]

    # ★B-50 段階B：アルバイトに**非必須役職のみ**を配る（FableA裁定 2026-07-24）。
    #   アルバイトは配役を無視してパーソンとして振る舞う（休眠）＝勝ち筋担い手（KP/キラー/クロマク/
    #   カルティスト/SK/ML/TT/ファクター/ウィッチ/メインラバーズ/ラバーズ/フレンド）を配ると
    #   その勝ち筋が休眠して脚本が壊れる＝除外。残る非必須＝マイナス（FS）のみ＝それを配る。
    #   時限式勝ち筋（必須役職の解禁）は mm 側にアルバイト殺害プランが載ってからの将来拡張。
    #   ★アルバイト？は継承（段階A・sim/state）＝配役表上アルバイトと同役職＝ここでは配らない。
    if "アルバイト" in cast and "アルバイト" not in roles:
        _essential = {"キーパーソン", "キラー", "クロマク", "カルティスト", "シリアルキラー",
                      "ミスリーダー", "タイムトラベラー", "ファクター", "ウィッチ",
                      "メインラバーズ", "ラバーズ", "フレンド"}
        from .state import BTX_ROLE_UNIVERSE, FS_ROLE_UNIVERSE
        _uni = set(FS_ROLE_UNIVERSE if set_name == "FS" else BTX_ROLE_UNIVERSE)
        # ★slots は引かない＝アルバイトへの非必須役職は「余分な1枚」（マイナスは勝ち筋も
        #   人数上限も無い＝スロット済みでも追加可）。FS＝マイナス／BTX＝非必須が
        #   パーソンのみ＝パーソン維持（配役表上は役職を持つ＝現物どおり）。
        _noness = sorted((_uni - _essential) - {DEFAULT_ROLE})
        if _noness:
            roles["アルバイト"] = rng.choice(_noness)   # 実質マイナス（FS）

    # ★B-50 段階B：登場が遅れるキャラの登場日/ループを脚本作成時に抽選（ユーザー正典 2026-07-24）。
    #   転校生＝2日目以降の登場日／神格＝2ループ目以降の登場ループ（脚本家に最初から明示・views で公開）。
    entry_days: dict[str, int] = {}
    entry_loops: dict[str, int] = {}
    if "転校生" in cast and days >= 2:
        entry_days["転校生"] = rng.randint(2, days)
    if "神格" in cast and loops >= 2:
        entry_loops["神格"] = rng.randint(2, loops)

    # 事件：勝ち筋（主人公の敗北ルート）に寄与するよう選ぶ（ユーザー指摘 2026-07-06）。
    #   ・殺せる役（KP/フレンド）がいるなら殺害系事件＝キラーと独立の勝ち筋。
    #   ・盤面敗北ルールならボードを育てる事件。未来改変プランなら蝶の羽ばたき。
    #   残り枠は無作為で埋める（多様性）。日・犯人は別々。
    incidents = _pick_incidents(rng, rule_y, roles, cast, days, inc_names)

    # 大物がキャストに入ったら縄張りボードを指定（脚本作成時＝ここで抽選・全ループ固定）
    territory = rng.choice(["病院", "神社", "都市", "学校"]) if "大物" in cast else None

    return Script(set_name=set_name, rule_y=rule_y, rule_x=rule_x, rule_x2=rule_x2,
                  loops=loops, days_per_loop=days, cast=cast, roles=roles,
                  incidents=incidents, oomono_territory=territory,
                  entry_days=entry_days, entry_loops=entry_loops)


def _pick_incidents(rng, rule_y, roles, cast, days, inc_names):
    """勝ち筋に寄与する事件を優先して選ぶ（残り枠は無作為）。"""
    from .script_quality import BOARD_RULES, KILL_INCIDENTS

    avail = set(inc_names)
    role_set = set(roles.values())
    killable = "キーパーソン" in role_set or "フレンド" in role_set

    wanted: list[str] = []
    if rule_y == "未来改変プラン" and "蝶の羽ばたき" in avail:
        wanted.append("蝶の羽ばたき")                       # 発生で即敗北条件
    if rule_y in BOARD_RULES:
        if rule_y == "封印されしモノ" and "邪気の汚染" in avail:
            wanted.append("邪気の汚染")                     # 神社+2
        elif "行方不明" in avail:
            wanted.append("行方不明")                       # 任意ボードへ暗躍+1
    if killable:
        kills = [k for k in ("殺人事件", "遠隔殺人", "病院の事件") if k in avail]
        if kills:
            wanted.append(rng.choice(kills))               # キラーと独立の殺害ルート
    wanted = list(dict.fromkeys(wanted))                    # 重複除去（順序維持）

    # ★B-53：日数別の既定（days<=5 は従来式と完全同一＝rng の消費順も不変）。
    min_inc, max_inc = incident_count_range(days)
    n_inc = min(max(len(wanted), rng.randint(min_inc, max_inc)), max_inc, len(cast))
    names = wanted[:n_inc]
    while len(names) < n_inc:                               # 残り枠は無作為
        names.append(rng.choice(sorted(avail)))

    inc_days = sorted(rng.sample(range(1, days + 1), n_inc))
    culprits = rng.sample(cast, n_inc)
    return [Incident(day=d, name=nm, culprit=c)
            for d, nm, c in zip(inc_days, names, culprits)]


def random_script(set_name: str = "FS", seed: int = 0,
                  loops: int = 3, days: int = 3, min_paths: int = 2,
                  min_ltw: int | None = None,
                  ltw_seeds: tuple[int, ...] = (0, 1, 2),
                  beginner: bool = False, standard: bool = False) -> Script:
    """無作為な正規脚本を1つ返す（同一seedなら同一脚本＝再現可能）。

    ★キャストの母集団は3モード（B-53b・2026-07-26）：
      - 既定＝`SAFE_CAST_POOL`（37体・simが裁定できる全キャラ）
      - `beginner=True`＝メンバー調整（Normal中心＋Rare1-2＋SR≈1/4のティア抽選）
      - `standard=True`＝**スタンダードメンバー**（`STANDARD_CAST_POOL` 19体のみ＝KB70の
        導入難度★1・★2＝定番だけ。⚠警告特性・変則・[拡張]キャラは出ない）
      `beginner` と `standard` を同時に指定した場合は `standard` が優先（ティア表 BEGINNER_* には
      変則キャラが入っており、標準プールの意味を壊すため）。
      ★`standard=False`（既定）では従来と**完全に同一**の生成経路＝正典コーパス不変。

    ★days（1ループの日数）＝**3/4/5/6日に対応**（B-53・2026-07-26）。事件数の既定は
    `incident_count_range(days)`、ループ回数の推奨は `recommended_loops(days)` を参照
    （loops 引数の既定は 3 のまま＝3日級/5日級の生成コーパス据え置き）。
    KB根拠＝40「FS推奨：1ループ3〜6日」／70「BTX標準5〜7日（迷うなら6日）」。

    ★脚本家の勝ち筋（主人公の敗北ルート）が min_paths 本以上ある候補を優先する
    （ユーザー指摘 2026-07-06：勝ち筋が実質1本＝キラーを止めれば脚本家が勝てない薄い脚本を避ける）。
    ★FB専用脚本のNGフィルタ（ユーザー方針 2026-07-08）：1ループ防衛不能（mastermind）の
    候補は除外＝最後の戦いでしか勝てないシナリオは基本NG。判定の正は詰みソルバ
    （solve_script＝初日厳密＋レース葉）。レース解析は高速な事前フィルタとして使い、
    レースを通った候補だけソルバで検証する（ソルバ呼び出しは最大 _SOLVER_BUDGET 回）。
    正規脚本のうち条件を満たす勝ち筋最多のものを返す（全滅時はベストを返す＝生成失敗にしない）。

    ★A2 難度下限フィルタ（opt-in・2026-07-09）：min_ltw を指定すると、防衛可能性を満たした
    候補をさらに AI主人公オラクル（script_quality.probe_difficulty＝arena.benchmark.loops_to_win）
    で測り、平均突破ループ数が min_ltw 未満（＝易しすぎ／下限割れ）の脚本を弾く。既定 None では
    この検査を一切行わない＝**生成コーパスは完全に不変**（既定の seed→脚本 対応を壊さない）。
    probe は AIゲームを回す重い検査なので予算制（_PROBE_BUDGET）。
    """
    key = (set_name, seed, loops, days, min_paths, min_ltw, ltw_seeds, beginner, standard)
    cached = _SCRIPT_CACHE.get(key)
    if cached is not None:
        return cached
    from .loop_race import MASTERMIND, analyze_script
    from .loop_solver import solve_script
    from .script_quality import win_path_groups

    rng = random.Random(seed)
    best: Script | None = None
    best_key = (-1, -1, -1, -1.0)   # (ソルバ防衛可能, レース防衛可能, 勝ち筋数, 難度)
    solver_budget = _SOLVER_BUDGET
    probe_budget = _PROBE_BUDGET
    for _ in range(120):
        try:
            script = _try_build(set_name, rng, loops, days, beginner=beginner,
                                standard=standard)
            validate_script(script)
        except ValueError:
            continue
        n = len(win_path_groups(script))
        race_ok = 1 if analyze_script(script).verdict != MASTERMIND else 0
        solver_ok = -1   # 未検証
        ltw = -1.0       # 未測定（min_ltw未指定なら常に-1.0＝tiebreakに影響せず）
        if race_ok and n >= min_paths and solver_budget > 0:
            solver_budget -= 1
            try:
                # ★B-53（2026-07-26）：日数が長いと詰みソルバは分オーダー＝実用外（実測：
                #   6日級BTXの1本で>100秒／3日・5日は1〜数秒）。元々 except 節が
                #   「ソルバ未対応（長日数等）はレース判定のまま採用可」を許していたので、
                #   例外任せにせず日数で明示ゲートする（レース解析は健全側・全局面で瞬時）。
                #   days<=_SOLVER_MAX_DAYS（＝3日級/5日級）は従来と完全に同一の経路。
                solver_ok = (1 if solve_script(script) != MASTERMIND else 0) \
                    if days <= _SOLVER_MAX_DAYS else -1
            except Exception:
                solver_ok = -1   # ソルバ未対応（長日数等）はレース判定のまま採用可
            if solver_ok != 0:
                # ★A2 難度下限（opt-in）：易しすぎる脚本を弾く。probe は重いので予算制。
                if min_ltw is not None and probe_budget > 0:
                    probe_budget -= 1
                    from .script_quality import probe_difficulty
                    ltw = probe_difficulty(script, seeds=ltw_seeds).mean_ltw
                    if ltw < min_ltw:
                        cand_key = (1, race_ok, n, ltw)  # 防衛可能だが易しい＝bestに残し探索継続
                        if cand_key > best_key:
                            best, best_key = script, cand_key
                        continue
                _SCRIPT_CACHE[key] = script
                return script
        cand_key = (max(solver_ok, 0) if solver_ok >= 0 else 0, race_ok, n, ltw)
        if cand_key > best_key:
            best, best_key = script, cand_key
    if best is None:
        raise RuntimeError(f"ランダム脚本生成に失敗（{set_name}）")
    _SCRIPT_CACHE[key] = best
    return best


# ソルバ検証の呼び出し上限（1生成あたり）とプロセス内キャッシュ
_SOLVER_BUDGET = 5
# ソルバ検証を行う日数の上限（B-53）。これを超える日数（6日級）はレース判定を正とする
# ＝生成が分オーダーになるのを避ける。3日級/5日級（<=5）は従来どおりソルバで検証する。
_SOLVER_MAX_DAYS = 5
# 難度下限プローブ（AIゲームを回す＝重い）の呼び出し上限（min_ltw 指定時のみ消費）
_PROBE_BUDGET = 8
_SCRIPT_CACHE: dict = {}
