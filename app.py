"""
惨劇RoopeR 5th ルール相談AI（First Steps & Basic Tragedy X）

rules/ 配下の構造化ルールKB（00〜70 のMarkdown）だけを根拠に、
ルールの質問へ答える Streamlit チャットアプリ。

設計思想:
- 知識ベース(KB)に書いてあることだけを根拠にする。無いことは創作しない。
- 『要確認（実カード）』とマークされた値や、記述が無い点は「原本確認が必要」と答える。
- 特定の脚本（シナリオ）の配役・犯人・真実は明かさない（ネタバレ防止）。

技術:
- 既定モデル: Claude Sonnet 5（高精度）／高速・低コスト重視なら Haiku 4.5 に切替可。
- KB全文を system に注入し、cache_control: ephemeral でプロンプトキャッシュ（2回目以降を安価化）。
- ベクトルDBは使わない（KBが system に丸ごと載るサイズのため不要）。

実行:  streamlit run app.py
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re

import anthropic
import streamlit as st

import presence  # 同時接続数（プロセス内メモリで計測・外部ストレージ不要）
import cloud      # Supabase永続機能（未設定でno-op）。まずは疎通確認用の app_open のみ配線


def _detect_mobile() -> bool:
    """スマホ表示にするか（PC版に影響を出さない別分岐用）。判定は3段構え：
    ①サイドバーの手動切替（session_state）②URL ?view=mobile ③User-Agent自動判定。
    ★Streamlitのバージョン差（本番1.40+／ローカル1.28）を吸収するため全て防御的に読む。
    """
    ov = st.session_state.get("_view_override", "auto")  # "auto"/"pc"/"mobile"
    if ov == "mobile":
        return True
    if ov == "pc":
        return False
    # URLパラメータ（?view=mobile でブックマーク可能）
    qp: dict = {}
    try:
        qp = dict(st.query_params)                       # Streamlit 1.30+
    except Exception:  # noqa: BLE001
        try:
            qp = {k: (v[0] if isinstance(v, list) else v)
                  for k, v in st.experimental_get_query_params().items()}
        except Exception:  # noqa: BLE001
            qp = {}
    v = str(qp.get("view", "")).lower()
    if v in ("mobile", "sp", "m"):
        return True
    if v in ("pc", "desktop"):
        return False
    # User-Agent 自動判定（st.context は 1.37+。無ければ PC 扱いにフォールバック）
    try:
        ua = st.context.headers.get("User-Agent", "")
    except Exception:  # noqa: BLE001
        ua = ""
    return bool(re.search(r"Mobi|Android|iPhone|iPod|Windows Phone", ua))

from board_viz import board_html_from_json, board_html_from_outcome
from builder import render_builder

from engine.checker import (
    engine_board_deltas,
    engine_counter_finals,
    engine_goodwill,
    engine_incident_occurs,
    engine_loop_end,
    engine_positions,
    engine_signal,
    grade_outcome,
    grade_scenario,
)
from engine.orchestrate import TRANSLATION_PROMPT, adjudicate
from engine.render import (
    render_confirmation_board,
    render_verdict,
    vocab_cheatsheet,
)
from engine.translate import TranslationError

# ページ幅はモード＋端末で出し分ける：一人回し（🎮）とビューア（🔁）はPCなら wide、
# 相談AI（💬）は centered。
# ★スマホは常に centered（狭幅で wide の横並びが崩れるのを防ぐ・PC版のコードパスは不変）。
# モードは radio(key="app_mode") が session_state に保持する値を set_page_config 前に参照する。
_MOBILE = _detect_mobile()
_mode = str(st.session_state.get("app_mode", ""))
_wide_mode = (_mode.startswith("🎮") or _mode.startswith("🔁")
              or _mode.startswith("🎭") or _mode.startswith("📜"))
st.set_page_config(
    page_title="敵対D-Meter", page_icon="🎭",  # C-11：サイト名（旧「〜研究ツールα」廃止）
    layout="centered" if _MOBILE else ("wide" if _wide_mode else "centered"),
)
if _MOBILE:
    # スマホ向けの軽いCSS（横スクロール抑止・余白圧縮・タップ領域確保）。PCには注入しない。
    # ★st.columns は狭幅で自動縦積みになるが、本アプリの横並び（配置先/カード・ボタン行・
    #   ログナビ・役職メモ表）は横のまま使いたいので nowrap＋min-width:0 で横並びを維持する。
    st.markdown(
        "<style>"
        "div.block-container{padding-top:2.2rem;padding-left:0.6rem;padding-right:0.6rem;}"
        "div[data-testid='stHorizontalBlock']{gap:0.3rem;flex-wrap:nowrap !important;}"
        "div[data-testid='stHorizontalBlock']>div[data-testid='column'],"
        "div[data-testid='stHorizontalBlock']>div[data-testid='stColumn']"
        "{min-width:0 !important;}"
        "button[kind]{min-height:2.4rem;}"
        "</style>", unsafe_allow_html=True)

# ---- C-1：公開必須の永続フッター（帰属・免責・プライバシー）----
# 全モードで常時表示する。各モードは描画後 st.stop() するが、この固定フッターは**ディスパッチ前に
# 1回だけ DOM へ入れる**＝以降どのモードで stop しても残る＝単一チョークポイント（配線忘れ防止）。
# native <details>（JS不要）で既定は1行の折り畳み＝スマホでも邪魔にならない・タップで全文。
# 文面は docs/公開運用方針.md §5（帰属/免責/プライバシー）を**そのまま**（創作しない）。
# engine/rules 非変更＝skill 再ビルド不要。フッターは全モード最下部（bottom:0）で統一し、
# 💬モードのみ st.chat_input（画面下固定＝.stChatFloatingInputContainer / 新版 stBottom）を
# フッターの高さぶん持ち上げて重なりを避ける（版差に強い＝フッター位置は動かさない）。
_chat_lift = (
    "div.stChatFloatingInputContainer,div[data-testid=\"stChatInput\"],"
    "div[data-testid=\"stBottom\"],div[data-testid=\"stBottomBlockContainer\"]"
    "{bottom:2.2rem !important;}"
    if _mode.startswith("💬") else "")
st.markdown(
    "<style>"
    "#rooper-footer{position:fixed;left:0;right:0;bottom:0;z-index:90;"
    "font-size:0.72rem;line-height:1.3;background:var(--background-color,#ffffff);"
    "border-top:1px solid rgba(128,128,128,0.35);padding:2px 12px;"
    "color:var(--text-color,#31333F);opacity:0.94;}"
    "#rooper-footer summary{cursor:pointer;list-style:none;opacity:0.85;"
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}"
    "#rooper-footer summary::-webkit-details-marker{display:none;}"
    "#rooper-footer .pf-body{padding:4px 0 2px;opacity:0.97;"
    "white-space:normal;max-height:40vh;overflow-y:auto;}"
    "#rooper-footer a{color:inherit;text-decoration:underline;}"
    "div.block-container{padding-bottom:3.4rem;}"
    + _chat_lift +
    "</style>"
    "<div id=\"rooper-footer\"><details>"
    "<summary>ℹ️ 非公式ファンツール／免責・プライバシー（タップで詳細）</summary>"
    "<div class=\"pf-body\">"
    "本作は『惨劇RoopeR』（BakaFire Party）の<b>非公式</b>ファン二次創作です。"
    "原作／サークル：惨劇RoopeR ／ BakaFire Party。"
    "ルール記述は惨劇RoopeR ルールブック等を出典とします。二次創作ガイドライン："
    "<a href=\"https://bakafire.main.jp/rooper/sr_dl_04_sozai.htm\" target=\"_blank\""
    " rel=\"noopener\">bakafire.main.jp/rooper</a><br>"
    "<b>免責</b>：非公式・AI回答・ゲーム進行のルール処理は誤りうる・最終判断は原本"
    "（主人公の書／脚本家の書）で。<br>"
    "<b>プライバシー</b>：入力は AI 処理のため外部（Anthropic API）へ送信／"
    "同時接続計測は匿名（保存なし）。<br>"
    # ★C-11：公式への問い合わせ防止（必須表記扱い）。
    "<b>お問い合わせ</b>：本サイトは非公式のファンメイドです。不具合・質問を"
    "公式（BakaFire Party）へ問い合わせないでください（下記 作者 X へ）。<br>"
    "<b>連絡先</b>：作者 X "
    "<a href=\"https://x.com/kitaro5053dev\" target=\"_blank\" rel=\"noopener\">"
    "@kitaro5053dev</a>（不具合報告・ご意見はこちらへも）。"
    "</div></details></div>",
    unsafe_allow_html=True)

# 連結対象のKBディレクトリ。00_, 10_, ... のように番号プレフィクスで順序を制御する。
RULES_DIR = "rules"

KB_TITLE = "惨劇RoopeR 5th / First Steps & Basic Tragedy X"

MODELS = {
    "Sonnet 5（高精度／既定）": "claude-sonnet-5",
    "Haiku 4.5（高速・低コスト）": "claude-haiku-4-5-20251001",
}

# 翻訳器（質問→盤面JSONの抽出）の既定モデル。回答モデルとは分離（サイドバーで変更可）。
# 実測（v0.16.1の翻訳経由採点・32問）：Sonnet 26 vs Haiku 25 で Sonnet が僅差リード＋起動数も多い。
# 温度バグ修正でSonnet翻訳が実行できるようになったので、既定をSonnetにする。
TRANSLATOR_MODEL = "claude-sonnet-5"

# 機能の節目で手動更新する人間可読バージョン（git短縮ハッシュとは別の目印）。
APP_VERSION = "0.22.0"  # β公開初版：C-18安定版隠蔽・メンバー調整ラベル・アルバイト対/遅延登場・AI強化一式


@st.cache_data
def get_build_info() -> str:
    """デプロイ識別用のビルド情報＝コミット短縮ハッシュ＋起動時刻(UTC)。

    Streamlit Cloud はリポジトリを git clone して動かすため多くの場合
    コミットハッシュを取得できる。取れない環境では起動時刻のみ返す。
    @st.cache_data なので、この値は「このデプロイが配信を始めた時点」で固定され、
    再デプロイ／再起動のたびに更新される（push反映の確認に使える）。
    """
    import subprocess
    from datetime import datetime, timezone

    rev = ""
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=3,
        ).decode().strip()
    except Exception:  # noqa: BLE001  # gitが無い／リポジトリでない等
        rev = ""
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{rev} ・ {started}" if rev else started


def _get_app_channel() -> str:
    """C-2：公開チャンネル。secrets or 環境変数 APP_CHANNEL＝'stable' のときだけ安定版、
    それ以外（未設定含む）は全て開発版扱い（安全側＝未設定の環境を『安定版』と誤表示しない）。"""
    val = ""
    try:
        if "APP_CHANNEL" in st.secrets:
            val = str(st.secrets["APP_CHANNEL"])
    except Exception:  # noqa: BLE001  secrets 未設定でも動く
        val = ""
    val = (val or os.environ.get("APP_CHANNEL", "")).strip().lower()
    return "stable" if val == "stable" else "dev"


APP_CHANNEL = _get_app_channel()
_IS_STABLE = APP_CHANNEL == "stable"
# チャンネル/版バッジ（サイドバー build hash 隣＋開発版の常時バナーで使う）。
CHANNEL_BADGE = (f"✅ 安定版 v{APP_VERSION}" if _IS_STABLE
                 else f"🧪 開発版 v{APP_VERSION}-dev")


def _report_form_url() -> str:
    """C-3：外部バグ報告フォームURL（secrets or 環境変数 REPORT_FORM_URL）。未設定なら空文字＝
    現行の qa_log/ログ提出フローにフォールバック（フォームURLは人間側C-4で用意・未設定でも壊れない）。"""
    try:
        if "REPORT_FORM_URL" in st.secrets:
            return str(st.secrets["REPORT_FORM_URL"]).strip()
    except Exception:  # noqa: BLE001  secrets 未設定でも動く
        pass
    return os.environ.get("REPORT_FORM_URL", "").strip()


def _diag_string(mode: str = "", board_hash: str = "", error: str = "") -> str:
    """バグ報告に自動添付する診断情報（版・モード・盤面ハッシュ）。個人情報/入力文は含めない。"""
    parts = [CHANNEL_BADGE, f"build:{get_build_info()}"]
    if mode:
        parts.append(f"mode:{mode[:2]}")
    if board_hash:
        parts.append(f"board:{board_hash}")
    if error:
        parts.append(f"err:{error[:180]}")
    return " / ".join(parts)


def render_report_block(mode: str = "", board_hash: str = "", error: str = "") -> None:
    """C-3：🐛バグ報告の導線（外部フォーム＋自動添付の診断情報）。フォーム未設定でも壊れない
    ＝診断情報を提示して現行の qa_log/ログ提出でも拾える形にする。公開運用方針 §3(a)。"""
    diag = _diag_string(mode, board_hash, error)
    url = _report_form_url()
    if url:
        # ★C-3b：URL テンプレートに {ver}/{mode}/{hash} プレースホルダがあれば、3値を URL エンコード
        #   して差し込んだ**事前入力URL**にする（entry ID はコードに持たない＝フォーム作り替えは
        #   secrets のテンプレート書き換えだけで追従）。プレースホルダ無しの素の URL は現行動作＝後方互換。
        if any(ph in url for ph in ("{ver}", "{mode}", "{hash}")):
            from urllib.parse import quote
            url = (url.replace("{ver}", quote(CHANNEL_BADGE))
                      .replace("{mode}", quote(mode))
                      .replace("{hash}", quote(board_hash)))
        try:
            st.link_button("🐛 バグ報告フォームを開く", url)
        except Exception:  # noqa: BLE001  古いStreamlitは link_button 非対応→markdownリンク
            st.markdown(f"[🐛 バグ報告フォームを開く]({url})")
        # 診断情報の st.code 表示は維持＝エラー文など事前入力欄に無い情報の貼り付け用（C-3b）。
        st.caption("フォームが開きます（版・モードは事前入力済み）。エラー文など不足分は下の診断情報を貼り付けてください。")
    else:
        st.caption("🐛 下の診断情報を添えて作者にご報告ください（報告フォームは準備中です）。")
    st.code(diag, language=None)


def _run_guarded(mode_label: str, fn) -> None:
    """C-3：危険フロー（play系）の未捕捉例外でページごと落ちるのを防ぐエラー境界。
    例外時は『🐛から報告を』＋診断情報（版・モード）＋ホーム復帰導線を出して st.stop()。
    公開運用方針 §3(a)。正常時は fn() の描画がそのまま出る（挙動不変）。"""
    try:
        fn()
    except Exception as e:  # noqa: BLE001  未捕捉例外を握ってページ全体の白画面を防ぐ
        st.error("⚠️ 表示中にエラーが発生しました。お手数ですが 🐛 からご報告ください。")
        # ★C-10：エラーの自動記録＝報告してくれない一般ユーザーの障害も拾う。版/チャンネル/モード/
        #   例外種別のみ（入力文・配役・犯人などの内容は渡さない）。Supabase 未設定なら no-op。
        try:
            cloud.log_event("error", mode=mode_label[:2], exc=type(e).__name__,
                            app_version=APP_VERSION)
        except Exception:  # noqa: BLE001  記録失敗でエラー画面を壊さない（fire-and-forget）
            pass
        try:
            render_report_block(mode=mode_label, error=repr(e))
        except Exception:  # noqa: BLE001  報告UI自体の二次失敗でも復帰導線は必ず出す
            pass
        if st.button("🏠 ホームに戻る", key="_err_home_btn"):
            st.session_state["app_mode"] = "🏠 ホーム"
            st.rerun()
        st.stop()


# ★C-15：支出上限案内の文言（月間上限到達＝相談AIの一時休止）。
_AI_PAUSED_MSG = (
    "⏸ 申し訳ありません。β期間中のAI利用枠（運営の月間上限）に達したため、相談AIは一時休止中です。"
    "時間をおくか翌月にお試しください。たくさん使いたい方は、ホームからスキル版（Claude.ai用・"
    "質問数無制限）をダウンロードできます。")


def _is_quota_error(exc: Exception) -> bool:
    """C-15：課金/上限/quota系エラーか（＝休止案内の対象）。anthropic SDK の例外型＋レスポンスの
    error.type で判別する（脆い文字列マッチはしない）。判別不能は False＝「一時的」に倒す
    （上限でないのに休止と誤表示しない）。RateLimitError(429)＝月間上限/レート、.type=billing_error
    ＝課金。overloaded(529)や一般の400/500は一時障害＝対象外。"""
    try:
        import anthropic
    except Exception:  # noqa: BLE001
        return False
    if isinstance(exc, anthropic.RateLimitError):        # 429＝月間上限/レート
        return True
    etype = getattr(exc, "type", None)                   # APIStatusError系は .type を持つ
    return isinstance(etype, str) and etype == "billing_error"


@st.cache_data
def load_kb(rules_dir: str) -> tuple[str, list[str]]:
    """rules/*.md を番号順に連結して1本のKB文字列にする。

    返り値: (連結済みKB本文, 読み込んだファイル名リスト)
    """
    paths = sorted(glob.glob(os.path.join(rules_dir, "*.md")))
    if not paths:
        raise FileNotFoundError(rules_dir)

    chunks: list[str] = []
    names: list[str] = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            body = f.read()
        name = os.path.basename(p)
        names.append(name)
        # どのファイル由来か分かるよう区切りヘッダを付ける（参照明記の助けにもなる）。
        chunks.append(f"\n\n===== ファイル: {name} =====\n{body}")

    return "".join(chunks).strip(), names


def build_system_prompt(kb: str) -> str:
    return (
        f"あなたは『{KB_TITLE}』専門のルール相談アシスタントです。"
        "脚本家（GM）やプレイヤーからのルールの質問に、下記【ルール知識ベース】だけを"
        "根拠に、正確かつ簡潔に答えてください。\n\n"
        "# 厳守事項\n"
        "- 回答は必ず【ルール知識ベース】の記述のみを根拠にする。知識ベースに無いことは"
        "推測や一般論で補わない。\n"
        "- 値や仕様が『要確認（実カード）』とマークされている、または該当記述が存在しない"
        "場合は、はっきりと『この点は知識ベースに未確定です。原本（主人公の書／脚本家の書）で"
        "確認してください』と伝える。決してルールを創作しない。\n"
        "- 前提検証: 質問の前提自体がルール違反の場合（例: 1ループに1回のカードを"
        "同一ターンに複数回使用、禁止エリアへの移動、主人公が暗躍カウンターを置く、"
        "ボードに友好/不安カウンターを置く、死体に行動カードをセット等）は、"
        "その前提が成立しないことと根拠（知識ベースの該当箇所）を指摘し、"
        "誤った前提のまま計算・回答を続けない。勝手に前提を補正して答えることもしない"
        "（必要なら正しい前提をユーザーに確認する）。\n"
        "- ただし前提検証は『ルール上不可能な操作』に限る。**答えられる質問を"
        "『成立しない／別の前提が必要』と言って回避しない**。ユーザーが提示した配役や状況"
        "（例:『医者がミスリーダー』）は所与として受け入れ、勝手に別役職・別条件を要求しない。"
        "条件で結論が分かれる場合は、まず質問文の最も素直な読みでの結論を**断定**し、"
        "別の読み（別役職・別フェイズ等）での結論は短い『補足』に留める。\n"
        "- 知識ベース内の『★』は注意点・公式正誤・FAQ要参照のマーカー。関連する★があれば"
        "その内容も反映する。\n"
        "- 友好能力の『必要友好数（ハート数）』は 20_goodwill_abilities.md の各キャラ節および"
        "一覧表の値をそのまま使う（推測しない）。これはキャラの『不安臨界』(→30_characters.md)"
        "とは**別の数値**なので絶対に混同しない。ハート数を答える時は必ず20の記載を確認する。\n"
        "- 根拠にした知識ベースのファイル名と見出し（セクション名）を回答の最後に"
        "『参照: 60_faq_rulings.md / 〇〇』のように明記する。\n"
        "- ネタバレ防止: 特定の脚本（シナリオ）の正解・配役・犯人・バッドエンド条件は"
        "知らない前提で、一般ルールのみ説明する。配役の推測は行わない。\n"
        "- First Steps（FS）と Basic Tragedy X（BTX）で仕様が異なる点（例: 最後の戦いの有無、"
        "ルールXの枚数、登場役職・事件）に注意し、どちらのセットの話かを意識して答える。"
        "質問でセットが不明なら必要に応じて確認する。\n"
        "- 『Basic Tragedy X』のXはアルファベット。ギリシャ文字の『χ』は別物のルール"
        "『不定因子χ』を指すので混同しない。\n"
        "- 日本語で、結論 → 理由 → 参照 の順に簡潔に。\n\n"
        "# ルール知識ベース\n" + kb
    )


# ---- 正誤チェッカー（行動解決フェイズ：暗躍/移動の決定的検算） ----

def build_translation_system() -> str:
    """質問→盤面JSONの抽出器プロンプト。対象外なら applicable:false。"""
    return (
        "あなたは『惨劇RoopeR(FS/BTX)』の質問を、判定エンジン用のJSONへ変換する抽出器です。"
        "あなた自身は裁定・推論をしません（盤面の抽出のみ）。対象は次の7種類："
        "(A)行動解決フェイズの数え上げ（暗躍/移動/不安/友好/移動禁止）、"
        "(B)脚本家能力フェイズに不安を何個置けるか、(C)脚本家能力フェイズに暗躍を何個置けるか、"
        "(D)事件が発生するか、(E)友好能力が使えるか・脚本家が拒否できるか、"
        "(F)ループが終了するか（タイミング裁定）、"
        "(G)連結：カードを置いて解決した後に事件が発生するか（行動解決→事件を1問で）。\n\n"
        + TRANSLATION_PROMPT
        + "\n\n# 適用範囲の判定（重要）\n"
        "- (G)例：『不安1の男子学生に不安+1を置いたら殺人事件は発生する？』"
        "→ phase=\"connected\"、placements に不安+1、incident.culprit=男子学生。"
        "不安を自分で足さず、行動解決後の不安でエンジンが判定する。\n"
        "- 質問が (A)〜(G) のいずれにも該当しないなら、必ず次だけを出力してください：\n"
        '  {"applicable": false}\n'
        "- 該当する場合のみ、上記スキーマのJSONを出力（キー applicable は不要）。\n"
        "- (B)例：『医者がミスリーダーで友好2、脚本家能力フェイズに不安をいくつ置ける？』"
        "→ phase=\"mastermind_unrest\"、医者を role=ミスリーダー・goodwill=2 で入れる。\n"
        "- (C)例：『神社にクロマク、脚本家能力フェイズに神社へ暗躍をいくつ置ける？』"
        "→ phase=\"mastermind_anyaku\"、クロマクを role=クロマク・area=神社 で入れる。\n"
        "- (D)例：『犯人が巫女の事件。巫女は生存で不安2。発生する？』"
        "→ phase=\"incident\"、巫女を alive=true・unrest=2 で characters に入れ、"
        "incident={\"culprit\": \"巫女\"}。\n"
        "- (E)例：『巫女に友好3。神社の暗躍除去は使える？拒否できる？』"
        "→ phase=\"goodwill_ability\"、巫女を goodwill=3 で入れ、"
        "goodwill_ability={\"character\": \"巫女\", \"ability\": \"神社の暗躍除去\"}。\n"
        "- (F)例：『3日目に敗北条件が成立。ループは終了する？』"
        "→ phase=\"loop_end\"、loop_end={\"defeat_condition_met\": true}（charactersは不要）。\n"
        "- 盤面/配役/友好数が質問文から定まらないなら applicable:false。\n"
        "- ただし『引っかけ』『前提が怪しい』と感じても、暗躍/移動や脚本家能力フェイズの"
        "数え上げに該当するなら applicable:true で翻訳すること（例:『神社の暗躍は有効？』で"
        "カードが病院にしか無くても、question_target=神社で翻訳すればエンジンが神社+0を出す）。\n"
        "- 死体に行動カードをセットする話も、その死体キャラ(alive:false)とセットを盤面に起こす"
        "（エンジンが前提違反 SET_ON_CORPSE を検出する）。\n"
        "- ★過剰起動しない：エンジンは (A)〜(F) の限られた数え上げ／可否だけを裁定する。"
        "**フェイズ順・タイミングの推論（シリアルキラー等の殺害はいつ起きるか、"
        "『〜が発生するから使えない』のような時系列の因果、能力の効果そのもの、"
        "事件効果の内容、勝敗の最終判定）は範囲外＝applicable:false**にして、"
        "エンジンに投げずにKB回答へ回す。例：『シリアルキラーに殺されるから主人公能力フェイズで"
        "友好能力は使えない？』は友好能力の"
        "使用可否(E)ではなく、死亡タイミングの推論なので applicable:false。\n\n"
        "# 使用可能な語彙（これ以外を使うとエンジンに弾かれる）\n"
        + vocab_cheatsheet()
        + "\n\n# 出力形式\n- JSONオブジェクトのみを出力。前置き・説明・```などの枠を一切付けない。\n"
    )


def _extract_json(text: str) -> dict | None:
    """LLM出力テキストから最初のJSONオブジェクトを取り出して辞書化する。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def translate_question(client, model: str, messages: list[dict]) -> tuple[dict | None, dict]:
    """会話履歴→盤面JSON。(data|None, debug) を返す。

    debug = {"reason": 非起動の理由(str)|None, "raw": 翻訳LLMの生出力(str)}。
    以前は失敗を黙ってNoneにしていたため「なぜ非起動か」が全く見えなかった。
    """
    try:
        # ★ temperature は渡さない。claude-sonnet-5 等の新世代は temperature が廃止され、
        #   指定すると 400 (invalid_request_error) で全滅する（v0.12以来のSonnet翻訳非起動の真因）。
        #   翻訳は制約の強い抽出タスクなので既定温度でも十分安定する。
        resp = client.messages.create(
            model=model,
            max_tokens=2000,  # 多キャラ盤面JSONが途中で切れないよう拡大（旧1024で truncation 疑い）
            system=build_translation_system(),
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as e:  # noqa: BLE001
        return None, {"reason": f"翻訳API例外: {type(e).__name__}: {e}", "raw": ""}
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None, {"reason": "翻訳出力からJSONを抽出できなかった", "raw": text}
    if data.get("applicable") is False:
        return None, {"reason": "翻訳が『対象外(applicable:false)』と判定", "raw": text}
    return data, {"reason": None, "raw": text}


def run_engine(data: dict):
    """JSON→裁定。(outcome, error) を返す。

    スキーマ違反/例外時は (None, 理由) を返し、呼び出し側は通常回答へフォールバックしつつ
    「なぜエンジンが起動しなかったか」を可視化できる（黙って握りつぶさない）。
    """
    try:
        return adjudicate(data), None
    except TranslationError as e:
        return None, f"翻訳/スキーマ拒否: {e}"
    except Exception as e:  # noqa: BLE001
        return None, f"裁定エラー: {type(e).__name__}: {e}"


# ---- デバッグ：想定問答ランナー ----

def load_scenarios() -> list[dict]:
    """tests/scenarios.py を（パッケージ化せず）ファイルから読み込む。

    ファイルが無い／壊れている場合でもアプリ全体を落とさず、空リストを返す
    （デバッグランナーは無効表示になるだけ）。
    """
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), "tests", "scenarios.py")
    if not os.path.exists(path):
        return []
    try:
        spec = importlib.util.spec_from_file_location("rooper_scenarios", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return list(mod.SCENARIOS)
    except Exception:  # noqa: BLE001  # 壊れていてもアプリは生かす
        return []


def engine_addendum_text(outcome) -> str | None:
    """回答LLMへ注入する『エンジン裁定（最優先・確定）』ブロック。"""
    if outcome is None:
        return None
    txt = (
        "# エンジン裁定（決定的・確定済み・最優先）\n"
        "下記はルールエンジンが厳密計算した【確定結論】です。あなたの役割は、この結論を"
        "ユーザーに分かりやすく説明することだけです。**数値を自分で計算し直してはいけません。**\n\n"
        "厳守事項:\n"
        "1. 各ボード/対象の最終値は、下記エンジン裁定の数字を**そのまま**結論に使う"
        "（例: エンジンが「病院 +0」なら答えも +0。勝手に「+2−1=+1」などと再計算しない）。\n"
        "2. もし自分の推論がエンジンの数字と食い違ったら、**エンジンが正・あなたの推論が誤り**。"
        "自分の計算を捨て、エンジンの数字に合わせる。\n"
        "3. 回答の冒頭で、エンジンの最終値を先に明記してから理由を述べる。\n"
        "4. 用語の注意: 『暗躍禁止が実効』＝その暗躍+は**全て打ち消されて0**（−1ではない）。"
        "『主人公2枚で自滅』＝暗躍禁止が**不発**になり暗躍+はそのまま通る。"
        "『カルティストが無視』＝そのボードの暗躍禁止は**不発**。\n"
        "5. **移動の成否・各キャラの最終的な現在地もエンジンが確定済み。**誰がどこにいるか・"
        "移動できたかを自分で判断し直さない。移動先に依存する能力（カルティストの暗躍禁止無視など）は、"
        "移動が成立した先にしか働かない。\n"
        "（例: エンジンが「異世界人は禁止エリアで移動できず神社に留まる」と言ったら、"
        "病院へ移動した前提に作り替えない。移動が不成立なら病院の暗躍禁止は無視されず実効し、病院は +0 のまま）。\n"
        "6. **数値以外の結論もエンジン確定**：事件の「発生する/しない」、友好能力の「使える/使えない」と"
        "拒否可否（不可/任意/強制）、ループの「終了する/しない」と勝敗は、下記の結論をそのまま使う。"
        "自分のフェイズ順推論で上書きしない（例: シリアルキラーの殺害はターン終了フェイズ＝"
        "主人公能力フェイズより後、敗北条件の途中成立ではループは中断しない）。\n\n"
        + render_verdict(outcome)
    )
    if outcome.needs_confirmation:
        txt += (
            "\n\n# 感度/確認フラグあり\n"
            "**上記の確定数値そのものは変えないこと。**この『感度』は、もし盤面が違っていれば"
            "（例: 主人公の暗躍禁止がもう1枚あれば）結論が反転しうる、という"
            "仮定依存の注意書きにすぎません。今の盤面での最終値はエンジンの数字が確定です。"
            "回答冒頭で『未指定カードは無しと仮定した結論』である旨を一言添えるに留めてください。"
        )
    return txt


# シグナル抽出（engine_signal 等）と自動採点（grade_scenario）は engine/checker.py に共通化した。
# tests/test_scenarios.py も同じ関数を使う＝checkキー追加時の二重メンテを解消（v0.14.1）。


def answer_once(client, model: str, messages: list[dict], system_blocks: list[dict]) -> str:
    resp = client.messages.create(
        model=model, max_tokens=4096, system=system_blocks, messages=messages,
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def run_one_scenario(client, model: str, sc: dict, system_prompt: str,
                     translator_model: str = TRANSLATOR_MODEL) -> dict:
    """1設問を実パイプライン（翻訳→エンジン→回答）で走らせ、結果をまとめる。"""
    messages = [{"role": "user", "content": sc["question"]}]
    data, trans_dbg = translate_question(client, translator_model, messages)

    # 翻訳経由の裁定（LLM翻訳→エンジン）。engine_input とは別に翻訳精度を測る用。
    live_outcome, live_err = (run_engine(data) if data else (None, trans_dbg.get("reason")))
    live_grade = grade_outcome(sc.get("check"), live_outcome) if sc.get("check") else "—"
    if live_outcome is None and live_err is None:
        live_err = trans_dbg.get("reason")

    # シナリオが engine_input を持つ場合は、LLM翻訳のブレを排して決定的に裁定する
    # （エンジン採点＝auto は engine_input で安定させる。翻訳精度＝auto_live は data で測る）。
    engine_input = sc.get("engine_input")
    if engine_input is not None:
        outcome, eng_err = run_engine(engine_input)
        engine_source = "engine_input（決定的）"
    else:
        outcome, eng_err = live_outcome, live_err
        engine_source = "LLM翻訳"
    engaged, value, violation = engine_signal(outcome)
    board_deltas = engine_board_deltas(outcome)
    counters = engine_counter_finals(outcome)
    positions = engine_positions(outcome)
    occurs = engine_incident_occurs(outcome)
    gw_ability = engine_goodwill(outcome)
    loop_end = engine_loop_end(outcome)

    add = engine_addendum_text(outcome)
    system_blocks = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    if add:
        system_blocks.append({"type": "text", "text": add})
    try:
        answer = answer_once(client, model, messages, system_blocks)
    except Exception as e:  # noqa: BLE001
        answer = f"[回答エラー] {e}"

    return {
        "id": sc["id"], "category": sc["category"], "tags": sc.get("tags", []),
        "question": sc["question"], "expected": sc["expected"],
        "translation": data, "engaged": engaged, "value": value, "violation": violation,
        "engine_error": eng_err, "board_deltas": board_deltas, "engine_source": engine_source,
        "occurs": occurs, "gw_ability": gw_ability, "loop_end": loop_end,
        "verdict": render_verdict(outcome) if outcome is not None else "",
        "answer": answer,
        "auto": grade_scenario(sc.get("check"), engaged, value, violation, board_deltas,
                               counters, positions, occurs, gw_ability, loop_end),
        # 翻訳経由（LLM翻訳→エンジン）の結果＝翻訳精度の実測。
        "auto_live": live_grade,
        "live_engaged": live_outcome is not None,
        "live_error": live_err,
        "translator_model": translator_model,
    }


def result_value_str(r: dict) -> str:
    """結果1件の「値」表示（デバッグ表・レポート共通）。"""
    if r["violation"]:
        return "前提違反"
    occ = r.get("occurs")
    if occ is not None:
        return "発生する" if occ else "発生しない"
    gw = r.get("gw_ability") or {}
    if gw:
        u = {True: "使える", False: "使えない", None: "要確認"}[gw.get("usable")]
        return f"{u}／拒否{gw.get('refuse')}"
    le = r.get("loop_end") or {}
    if le:
        ends = "終了" if le.get("loop_ends") else "継続"
        d = {True: "・主敗北", False: "・主勝利", None: ""}[le.get("defeat")]
        return f"{ends}{d}"
    bd = r.get("board_deltas") or {}
    if len(bd) >= 2:
        return " / ".join(f"{b}+{d}" for b, d in bd.items())
    if r["value"] is None:
        return "—"
    return f"+{r['value']}"


def build_debug_report(results: list[dict], model: str) -> str:
    import datetime as _dt
    import json as _json

    val_str = result_value_str
    _tr = results[0].get("translator_model", "") if results else ""
    _tr_name = next((k for k, v in MODELS.items() if v == _tr), _tr)
    _grad = [r for r in results if r.get("auto_live") in ("PASS", "FAIL")]
    _lp = sum(1 for r in _grad if r["auto_live"] == "PASS")
    _leng = sum(1 for r in results if r.get("live_engaged"))

    lines = ["# 惨劇RoopeR 想定問答 デバッグ結果",
             f"- 回答モデル: {model} ／ 翻訳器: {_tr_name}",
             f"- 日時: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"- 件数: {len(results)}",
             (f"- 翻訳経由の精度: 採点可{len(_grad)}件中 PASS {_lp}"
              f"（エンジン起動 {_leng}/{len(results)}）" if _grad else ""),
             "",
             "## サマリ",
             "| id | 区分 | エンジン | 値 | 自動判定 | 翻訳起動 | 翻訳経由 |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        eng = "起動" if r["engaged"] else "非起動"
        leng = "○" if r.get("live_engaged") else "✕"
        lines.append(f"| {r['id']} | {r['category']} | {eng} | {val_str(r)} | {r['auto']} "
                     f"| {leng} | {r.get('auto_live', '—')} |")
    lines.append("")
    lines.append("## 詳細")
    for r in results:
        tag = "".join(r["tags"])
        lines.append(f"### {r['id']} {tag} [{r['category']}] — 自動判定: {r['auto']}")
        lines.append(f"**Q:** {r['question']}")
        lines.append(f"**期待:** {r['expected']}")
        eng = "起動" if r["engaged"] else "非起動"
        src = f"（入力: {r.get('engine_source', '—')}）" if r["engaged"] else ""
        lines.append(f"**エンジン:** {eng}{src} ／ 値: {val_str(r)}")
        if not r["engaged"] and r.get("engine_error"):
            # 翻訳は出たのにエンジンが起動しなかった理由を必ず可視化（黙って握りつぶさない）。
            lines.append(f"**⚠ エンジン非起動の理由:** {r['engine_error']}")
        # 翻訳経由（LLM翻訳→エンジン）の結果＝翻訳精度。engine_input採点とは別に併記。
        _live = "○起動" if r.get("live_engaged") else "✕非起動"
        lines.append(f"**翻訳経由:** {_live} ／ 判定 {r.get('auto_live', '—')}"
                     + (f" ／ 理由: {r['live_error']}" if r.get("live_error") else ""))
        if r["translation"] is not None:
            lines.append("**翻訳JSON:**")
            lines.append("```json")
            lines.append(_json.dumps(r["translation"], ensure_ascii=False, indent=2))
            lines.append("```")
        if r["verdict"]:
            lines.append("**エンジン裁定:**")
            lines.append("```")
            lines.append(r["verdict"])
            lines.append("```")
        lines.append("**LLM回答:**")
        lines.append(r["answer"])
        lines.append("")
        lines.append("---")
    return "\n".join(lines)


def get_shared_api_key() -> str:
    """アプリ側のキー（Streamlit Secrets / 環境変数）を取得する。

    重要: この戻り値は絶対に st.text_input の value= 等で画面に描画しないこと。
    描画すると（type="password" でも）値が閲覧者のブラウザに渡ってしまう。
    """
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # noqa: BLE001  # secrets.toml が無いローカル環境など
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


# ---- テスター向け: フィードバック＆セッションログ ----

FB_TAGS = [
    "翻訳（盤面の理解）が違う",
    "数値・結論が違う",
    "説明が分かりにくい",
    "ネタバレ/範囲外に踏み込んだ",
    "その他",
]


def feedback_ui(idx: int) -> None:
    """回答1件への Good/Bad/Bug フィードバック（qa_log[idx] に記録）。"""
    entry = st.session_state.qa_log[idx]
    fb = entry.get("feedback")
    # ボタン押下自体が rerun を起こすので明示的な st.rerun() は不要（二重rerunでの入力ちらつきを避ける）。
    c1, c2, c3, _sp = st.columns([1, 1, 1, 4])
    if c1.button(("✅" if fb == "good" else "") + "👍 Good", key=f"fbg{idx}"):
        entry["feedback"] = "good" if fb != "good" else None
        if entry["feedback"] == "good":
            cloud.log_feedback(entry)   # B：👍は即送信（タグ/コメント不要）。未設定でno-op。
    if c2.button(("✅" if fb == "bad" else "") + "👎 Bad", key=f"fbb{idx}"):
        entry["feedback"] = "bad" if fb != "bad" else None
    if c3.button(("✅" if fb == "bug" else "") + "🐛 Bug", key=f"fbu{idx}"):
        entry["feedback"] = "bug" if fb != "bug" else None
    if entry.get("feedback") in ("bad", "bug"):
        tags = st.multiselect(
            "どこが問題？（任意・複数可）", FB_TAGS,
            default=entry.get("fb_tags", []), key=f"fbt{idx}",
        )
        comment = st.text_area(
            "コメント（正しい答え・気づいたこと等）",
            value=entry.get("fb_comment", ""), key=f"fbc{idx}", height=80,
        )
        # 明示的な「記録する」ボタンで確定（押すまで保存されない＝入力途中で勝手に変わらない）。
        if st.button("💾 このフィードバックを記録する", key=f"fbs{idx}"):
            entry["fb_tags"] = tags
            entry["fb_comment"] = comment
            entry["fb_saved"] = True
            cloud.log_feedback(entry)   # B：👎🐛はタグ/コメント確定時に送信。未設定でno-op。
        if entry.get("fb_saved"):
            st.success("✅ 記録しました。サイドバーの「テストログ」からダウンロードできます"
                       "（内容を直したら再度『記録する』を押してください）。")
        else:
            st.caption("↑ 入力後に「💾 記録する」を押すと保存されます。")
        # C-3：外部フォームが設定されていれば、そこへの誘導も出す（.jsonl を送らない一般ユーザーの
        #   拾い口。盤面ハッシュ＝質問＋翻訳から安定生成し個人情報は含めない。未設定なら現行のまま）。
        if _report_form_url():
            _bh = hashlib.sha1(
                (entry.get("question", "") + str(entry.get("translation", "")))
                .encode("utf-8")).hexdigest()[:8]
            with st.expander("🐛 バグ報告フォームで詳しく報告する（任意）"):
                render_report_block(mode="💬", board_hash=_bh)


def render_engine_extras(entry: dict) -> None:
    """回答の前に出す共通パーツ（エンジンバッジ・盤面・裁定expander）。"""
    if entry.get("engaged"):
        st.caption("⚙️ エンジン裁定あり（数値・結論は決定的計算に基づく）")
    else:
        st.caption("📖 KB回答（エンジン非起動＝知識ベースからの説明）")
        # 非起動の理由と翻訳の生出力を検証用に開示（黙って握りつぶさない）。
        if entry.get("engine_error") or entry.get("translation_raw"):
            with st.expander("🔍 なぜエンジン非起動？（開発者向け）"):
                if entry.get("engine_error"):
                    st.markdown(f"**理由:** {entry['engine_error']}")
                if entry.get("translation_raw"):
                    st.caption("翻訳LLMの生出力:")
                    st.code(entry["translation_raw"][:1500], language="json")
    bi, bo = entry.get("board_in"), entry.get("board_out")
    if bi and bo:
        col1, col2 = st.columns(2)
        with col1:
            st.caption("📥 入力盤面（AIの理解）")
            st.markdown(bi, unsafe_allow_html=True)
        with col2:
            st.caption("📤 裁定後の盤面")
            st.markdown(bo, unsafe_allow_html=True)
    elif bi:
        st.caption("📥 入力盤面（AIの理解）")
        st.markdown(bi, unsafe_allow_html=True)
    if entry.get("verdict"):
        with st.expander("🔧 エンジン裁定（決定的・検算用）"):
            st.markdown(entry["verdict"])


_FB_MARK = {"good": "👍", "bad": "👎", "bug": "🐛", None: "—"}


def build_session_log_md(entries: list[dict], tester: str) -> str:
    """テスターが提出する人間可読ログ（.md）。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 惨劇RoopeR テストセッションログ",
        f"- テスター: {tester or '（未記入）'}",
        f"- 日時: {now} ／ 件数: {len(entries)}",
        f"- アプリ: v{APP_VERSION} ／ ビルド: {get_build_info()}",
        "",
        "## サマリ",
        "| # | FB | エンジン | 質問 |",
        "|---|---|---|---|",
    ]
    for i, e in enumerate(entries, 1):
        q = e["question"].replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| {i} | {_FB_MARK[e.get('feedback')]} | "
            f"{'起動' if e.get('engaged') else '非起動'} | {q[:40]} |"
        )
    lines.append("")
    lines.append("## 詳細")
    for i, e in enumerate(entries, 1):
        lines.append(f"### #{i} [{_FB_MARK[e.get('feedback')]}] {e['ts']}（{e['model']}）")
        lines.append(f"**Q:** {e['question']}")
        if e.get("feedback") in ("bad", "bug"):
            tags = "・".join(e.get("fb_tags") or []) or "（タグなし）"
            lines.append(f"**指摘:** {tags} ／ {e.get('fb_comment') or '（コメントなし）'}")
        if e.get("translation"):
            lines.append("**翻訳JSON:**")
            lines.append("```json")
            lines.append(json.dumps(e["translation"], ensure_ascii=False, indent=1))
            lines.append("```")
        else:
            lines.append(f"**エンジン非起動の理由:** {e.get('engine_error') or '（不明）'}")
            if e.get("translation_raw"):
                lines.append("**翻訳LLMの生出力:**")
                lines.append("```")
                lines.append(str(e["translation_raw"])[:1200])
                lines.append("```")
        if e.get("verdict"):
            lines.append("**エンジン裁定:**")
            lines.append("```")
            lines.append(e["verdict"])
            lines.append("```")
        lines.append("**回答:**")
        lines.append(e["answer"])
        lines.append("")
        lines.append("---")
    return "\n".join(lines)


def build_session_log_jsonl(entries: list[dict], tester: str) -> str:
    """機械可読ログ（.jsonl）。盤面HTMLは除外（翻訳JSONから再生成可能なため）。"""
    out = []
    for e in entries:
        rec = {k: v for k, v in e.items() if k not in ("board_in", "board_out")}
        rec["tester"] = tester
        out.append(json.dumps(rec, ensure_ascii=False))
    return "\n".join(out)


# ==== モード切替（相談AIチャット ⇄ AIと一人回し練習）====
# サイドバー最上部でモードを選ぶ。一人回しモードでは arena/play.py の本体を呼び、
# 相談AIのチャット/ビルダー/ランナーは描画しない（st.stopで以降を止める）。
# 相談AI側の機能は「💬 ルール相談AI」モードでそのまま維持される。
def _set_view_override() -> None:
    st.session_state["_view_override"] = {
        "🖥 自動": "auto", "💻 PC": "pc", "📱 スマホ": "mobile",
    }[st.session_state["_view_radio"]]


with st.sidebar:
    # ★C-12：🛠 開発者用（負け筋ネットワーク閲覧＝開発者・協力者向け）は安定版では非表示。
    #   判定は C-2 のチャンネル（_IS_STABLE）を流用。安定版に 🛠 が session に残っていても
    #   options 外だと radio がエラーになるため、その場合はホームへ戻す（fail-safe）。
    _mode_options = ["🏠 ホーム", "🎮 主人公としてプレイ", "🎭 脚本家としてプレイ",
                     "📜 脚本工房", "🔁 リプレイビューワー", "💬 相談AI"]
    if not _IS_STABLE:
        _mode_options.append("🛠 開発者用")
    if st.session_state.get("app_mode") not in _mode_options:
        st.session_state["app_mode"] = "🏠 ホーム"
    _app_mode = st.radio(
        "モード",
        _mode_options,
        key="app_mode",
        help="ホーム（このサイトの案内）／AI脚本家に挑む一人回し練習／"
             "あなたが脚本家としてAI主人公に挑む（AIの推理を見ながら）／"
             "脚本工房（脚本を組み・難易度を診断し・『プレイ用JSON』でそのまま遊ぶ）／"
             "保存した対局ログ(.jsonl)を日単位で振り返るビューア／ルールの質問に答えるチャット／"
             "開発者用（負け筋ネットワークのレビュー資料）を切り替えます。",
    )
    # 表示（PC/スマホ）切替。既定は端末自動判定。スマホでは横並びを縦積みにして見やすくする。
    _view_labels = ["🖥 自動", "💻 PC", "📱 スマホ"]
    _view_idx = {"auto": 0, "pc": 1, "mobile": 2}.get(
        st.session_state.get("_view_override", "auto"), 0)
    st.radio("表示", _view_labels, index=_view_idx, horizontal=True,
             key="_view_radio", on_change=_set_view_override,
             help="スマホで横並びが崩れる場合は『📱 スマホ』に。PCは『💻 PC』のまま。")
    st.divider()

# 同時接続数の計測: 全モード共通で刻む（🎮/🔁 は下で st.stop() するため分岐の前に置く）。
# 画面には何も出さない（表示は開発者モードのときだけ・下のサイドバー）。
presence.render_heartbeat()

# Supabase疎通確認：セッション1回だけ app_open を送る（未設定なら no-op）。
# ※本格的なイベント/コスト保護/フィードバック/棋譜の配線は AIA/AIB タスク（handoff参照）。
if not st.session_state.get("_cloud_app_open_sent"):
    st.session_state["_cloud_app_open_sent"] = True
    cloud.log_event("app_open", app_version=APP_VERSION)

# Supabase疎通診断: URLに ?cloudtest=1 を付けた時だけ表示（一般ユーザーには出ない）。
try:
    _cloudtest = st.query_params.get("cloudtest")
except Exception:  # noqa: BLE001
    _cloudtest = None
if _cloudtest:
    st.subheader("🔌 Supabase 疎通診断")
    st.json(cloud.ping())
    st.caption("この結果を作者へ共有してください（診断用・?cloudtest=1 のときだけ表示）。")
    st.stop()


# --- D 告知バナー／フィーチャーフラグ（AIC handoff・全モード共通）。ネット呼び出しはキャッシュ ---
_DEFAULT_DAILY_LIMIT = 50   # A コスト保護の日次上限（app_config の "daily_limit" で上書き可）


@st.cache_data(ttl=120, show_spinner=False)
def _cloud_config_cached() -> dict:
    return cloud.get_config() or {}


@st.cache_data(ttl=120, show_spinner=False)
def _cloud_announcement_cached():
    return cloud.get_announcement()


# D：告知（active な行があれば全モードの最上部に出す）。未設定/障害時は None＝何も出さない。
_ann = _cloud_announcement_cached()
if _ann and _ann.get("message"):
    _lvl = str(_ann.get("level") or "info").lower()
    {"warning": st.warning, "error": st.error}.get(_lvl, st.info)("📢 " + str(_ann["message"]))

# C-2：開発版の常時バナー（全モード最上部・告知と同じ単一チョークポイント）。安定版では出さない。
# チャンネル判定は secrets/環境変数 APP_CHANNEL（未設定＝開発版扱い＝安全側）。
if not _IS_STABLE:
    st.warning(CHANNEL_BADGE + "｜不安定・予告なく壊れる／データはリセットされることがあります。")

# モード切替の匿名イベント（メタのみ・内容は送らない）。切替時だけ1回。
if st.session_state.get("_cloud_last_mode") != _app_mode:
    st.session_state["_cloud_last_mode"] = _app_mode
    cloud.log_event("mode", mode=_app_mode[:2], app_version=APP_VERSION)

# ★C-3：各モードの描画を _run_guarded で包む＝未捕捉例外でページごと白画面になるのを防ぎ、
#   🐛報告導線＋ホーム復帰を出す（公開運用方針 §3(a)。正常時は描画そのまま＝挙動不変）。
if _app_mode.startswith("🏠"):
    from home import render_home

    # 最初に開くインデックス（HTML解説ページ）。ナビボタン/サイドバーで各モードへ。
    _run_guarded("🏠", lambda: render_home(
        app_version=APP_VERSION, build_info=get_build_info(), mobile=_MOBILE))
    st.stop()

if _app_mode.startswith("🎮"):
    from arena.play import render_play

    st.session_state["app_version"] = APP_VERSION  # 保存ログに版数を残す（play側が参照）
    # 自前サイドバー＋本体を描画（set_page_configは呼ばない）。
    _run_guarded("🎮", lambda: render_play(mobile=_MOBILE, stable=_IS_STABLE))
    st.stop()                    # 以降（相談AIのチャット等）は描画しない

if _app_mode.startswith("🎭"):
    from arena.play_vs_ai import render_play_vs_ai

    st.session_state["app_version"] = APP_VERSION
    # 人間=脚本家 vs AI主人公＋主人公AI内省パネル。
    _run_guarded("🎭", lambda: render_play_vs_ai(mobile=_MOBILE))
    st.stop()

if _app_mode.startswith("🔁"):
    from arena.viewer import render_viewer

    # 埋込＝アップロード専用・神視点既定OFF（身内公開でも推理のネタバレを避ける）。
    _run_guarded("🔁", lambda: render_viewer(mobile=_MOBILE, embedded=True))
    st.stop()                    # 以降（相談AIのチャット等）は描画しない

if _app_mode.startswith("🛠"):
    from arena.losstree_view import render_losstree_view

    # 開発者用：負け筋ネットワーク（DP-1 Stage 1）のレビュー閲覧＝ユーザー/手練れ協力者が
    # スマホから完全性レビューできるようにする（表示のみ・ゲーム状態に触れない）。
    _run_guarded("🛠", lambda: render_losstree_view(mobile=_MOBILE))
    st.stop()

if _app_mode.startswith("📜"):
    from script_studio import render_studio

    # 脚本工房：脚本を組む→難易度診断→「🎮 プレイ用JSON」で 🎮/🎭 の『✍️ 自作脚本で始める』へ。
    #   ＝「工房で作る→診断する→そのまま遊ぶ」が一本の動線になる（自作脚本のプレイ投入口=427377a）。
    _run_guarded("📜", render_studio)
    st.stop()

# ---- サイドバー: 設定 ----
with st.sidebar:
    st.header("設定")
    # value は必ず空にする。Secret/環境変数の値をここに入れると、
    # 入力欄の初期値として全閲覧者のブラウザに送られてしまう（伏字でも実値は読める）。
    user_key = st.text_input(
        "Anthropic APIキー（任意）",
        type="password",
        value="",
        help="通常は空欄でOK（アプリ側のキーを使用）。自分のキーで使いたい場合のみ入力してください。",
    )
    # ★app_config化（運用値をデプロイなしで変更可・未設定は従来既定）：
    #   既定回答モデルを app_config の "default_model"（モデルID・例 "claude-haiku-4-5-20251001"）で
    #   上書き可能に。無効/未設定なら先頭（従来どおり）。
    _cfg = _cloud_config_cached()
    _ans_keys = list(MODELS.keys())
    _ans_idx = next((i for i, k in enumerate(_ans_keys)
                     if MODELS[k] == _cfg.get("default_model")), 0)
    model_label = st.selectbox("モデル（回答）", _ans_keys, index=_ans_idx)
    model = MODELS[model_label]

    # 翻訳器（質問→盤面JSON抽出）モデル。既定は app_config "default_translator_model"（未設定は
    # TRANSLATOR_MODEL＝Sonnet 5）。Sonnetの翻訳精度を試すとき用（非起動理由は「🔍なぜ非起動」で可視化）。
    _tr_keys = list(MODELS.keys())
    _tr_default_id = _cfg.get("default_translator_model", TRANSLATOR_MODEL)
    _tr_default = next((i for i, k in enumerate(_tr_keys) if MODELS[k] == _tr_default_id),
                       _tr_keys.index(next(k for k, v in MODELS.items()
                                           if v == TRANSLATOR_MODEL)))
    with st.expander("⚙️ 詳細設定（翻訳器）"):
        tr_label = st.selectbox(
            "翻訳器モデル", _tr_keys, index=_tr_default,
            help="質問→盤面JSONの抽出に使うモデル。回答モデルとは分離。"
            "既定Haikuは抽出に忠実・高速。Sonnetは精度が高い可能性があるが、前置きを足して"
            "失敗することもある（デバッグランナーの『翻訳経由』列で精度を実測できる）。",
        )
        translator_model = MODELS[tr_label]

    if st.button("会話をリセット"):
        st.session_state.messages = []
        st.rerun()
    st.caption("※リセットしてもテストログは残ります")

# 実際に使うキー: ユーザー入力があれば優先、無ければアプリ側のキー（Secrets/環境変数）。
# get_shared_api_key() の戻り値は画面に出さないこと。
api_key = user_key.strip() or get_shared_api_key()

# ---- 本体 ----
st.title("🎭 惨劇RoopeR ルール相談AI_α")
st.caption(
    "First Steps & Basic Tragedy X 対応 ・ "
    "知識ベース駆動（無いことは創作せず原本確認を促す設計）"
)

with st.expander("📖 はじめに（使い方）— 最初にお読みください", expanded=True):
    st.markdown(
        "### このアプリについて\n"
        "惨劇RoopeR 5th の **First Steps / Basic Tragedy X** のルール質問に答えます"
        "（拡張セットは範囲外＝「原本を確認してください」と答えます）。\n\n"
        "**2つの仕組みで答えています**\n"
        "- 📖 **LLM（Claude）**：ルールの知識ベースを読んで自然文で回答します。\n"
        "- ⚙️ **判定エンジン**：暗躍/不安/友好の数え上げ、事件の発生、友好能力の可否、"
        "ループ終了のタイミングなどは、**決定的なプログラムが厳密に計算**します。"
        "回答の上に「⚙️ エンジン裁定あり」と出ていれば、その数値・結論はエンジンの計算です。\n\n"
        "**使い方のコツ**\n"
        "- まずは一番下の入力欄に、ルールの質問を**テキストで**気軽にどうぞ。\n"
        "- **応答には少し時間がかかります**（数秒〜十数秒）。**考えている間は画面右上の人型アイコンが"
        "動いています**。それが止まれば回答完了の合図です。焦らず待ってください。\n"
        "- 複雑な盤面（多キャラ・全ボード）は「🛠️ 盤面ビルダー」で組むと確実です"
        "（翻訳を通さず直接エンジンが裁定。**盤面ビルダーの結果にも 👍/👎/🐛 が付きます**）。\n"
        "- 回答の下の **👍 / 👎 / 🐛** で評価してください。おかしい所は **🐛** が特に助かります。\n"
        "- ひと通り試したら、サイドバー「📥 ログをダウンロード」で作者に送ってください🙏\n\n"
        "**答えやすい質問・答えにくい質問**\n"
        "- ◎ 一般ルール：『鑑識官の友好能力は空撃ちできる？』"
        "『暗躍禁止を複数の主人公が出すとどうなる？』\n"
        "- ◎ 具体的な盤面：『神社にカルティスト、病院に暗躍+2と暗躍禁止。病院はいくつ増える？』\n"
        "- △ **ネタバレは答えません**：個別脚本の犯人・配役・真相。\n"
        "- △ **範囲外**：拡張セット固有のルールは「原本確認」と答えます。"
    )

# ★C-14：Claude.ai スキル版（質問数無制限）の公開DL。案内＋download_button（未ビルドなら出さない）。
try:
    with open(os.path.join("skill", "dist", "sangeki-rooper-rules.zip"), "rb") as _f:
        _skill_zip = _f.read()
except Exception:  # noqa: BLE001  未ビルド等はDL欄を出さない（壊れない）
    _skill_zip = None
if _skill_zip:
    with st.expander("🧩 たくさん質問したい方へ（スキル版・質問数無制限）", expanded=False):
        st.caption("この zip を Claude.ai のスキルとしてアップロードすると、ご自分の Claude プラン内で"
                   "質問数無制限に使えます（運営の月間上限に左右されません）。非公式・出典明記は zip 内の"
                   "SKILL.md をご確認ください。質問は X @kitaro5053dev へ。")
        st.download_button("⬇ スキル版をダウンロード（.zip）", data=_skill_zip,
                           file_name="sangeki-rooper-rules.zip", mime="application/zip",
                           key="chat_skill_dl")

try:
    kb, kb_files = load_kb(RULES_DIR)
except FileNotFoundError:
    st.error(f"知識ベースが見つかりません: {RULES_DIR}/*.md を配置してください。")
    st.stop()

with st.sidebar:
    st.caption("読込済みKB:")
    for n in kb_files:
        st.caption(f"・{n}")

    st.divider()
    st.caption(CHANNEL_BADGE)  # C-2：チャンネル/版バッジ（build hash 隣＝障害報告の版特定用）
    st.caption(f"バージョン {APP_VERSION}")
    st.caption(f"ビルド: {get_build_info()}")

    # 開発者モード：ONのときだけ想定問答ランナー（作者用）を表示。既定OFF＝テスターには見えない。
    # ★安定版（β公開）ではチェックボックス自体を出さない＝dev_mode恒偽（C-18の相談AI側・
    #   ユーザー指示 2026-07-24）。session に dev_mode が残っていても参照しない＝安全側。
    dev_mode = False if _IS_STABLE else st.checkbox(
        "🔧 開発者モード", value=False, key="dev_mode",
        help="想定問答ランナー（一括実行→レポート）を表示します。テスターは不要です。",
    )

    # 同時接続数（再起動タイミング判断用）。開発者モードのときだけ表示。
    if dev_mode:
        presence.render_count()
        # ★Supabase 管理ビュー（回収側）：送信は配線済みだが「見る」仕組みが無かった＝
        #   フィードバック(👍👎🐛)と共有棋譜を dev限定で一覧・ダウンロード（FableAタスク）。
        #   ★RLS：feedback/events は anon SELECT 不可（insert専用）＝一覧には Secrets の
        #     `SUPABASE_SERVICE_KEY`（service-role）が必要。games は anon read 可。
        #   ★★個人情報/ネタバレのマスキング検討：feedback.payload に自由記述コメント（PII化しうる）、
        #     games.payload に配役/犯人（ネタバレ）が入りうる。ここでは**要約のみ表示**（コメントは
        #     40字トリム・棋譜payloadは全文出さずコード/日時/channelのみ）＝全文はダウンロードに回す。
        #     公開範囲を広げる/画面に全文出す前に匿名化・マスキング方針を確定すること。
        if cloud.enabled():
            with st.expander("🗄 Supabase 管理ビュー（フィードバック・共有棋譜）", expanded=False):
                import json as _json
                _fb = cloud.admin_list("feedback", limit=200)
                st.caption(f"👍👎🐛 フィードバック {len(_fb)}件"
                           + ("" if _fb else "（0件 or service key未設定で読取不可）"))
                if _fb:
                    st.dataframe(
                        [{"日時": str(r.get("created_at", ""))[:19],
                          "種別": (r.get("payload") or {}).get("kind", ""),
                          "コメント(要約)": str((r.get("payload") or {}).get("comment", ""))[:40]}
                         for r in _fb],
                        use_container_width=True, hide_index=True)
                    st.download_button(
                        "⬇ feedback.json（全文）", _json.dumps(_fb, ensure_ascii=False, indent=1),
                        file_name="feedback.json", mime="application/json", key="dl_fb")
                _gm = cloud.admin_list("games", limit=200)
                st.caption(f"☁ 共有棋譜 {len(_gm)}件")
                if _gm:
                    st.dataframe(
                        [{"コード": r.get("code", ""), "日時": str(r.get("created_at", ""))[:19],
                          "channel": r.get("channel", "")} for r in _gm],
                        use_container_width=True, hide_index=True)
                    st.download_button(
                        "⬇ games.json（配役/犯人含む＝ネタバレ注意）",
                        _json.dumps(_gm, ensure_ascii=False, indent=1),
                        file_name="games.json", mime="application/json", key="dl_gm")
                st.caption("⚠ feedback/events の一覧には Secrets に SUPABASE_SERVICE_KEY(service-role) "
                           "が必要（RLSでanon読取不可）。games は anon読取可。"
                           "payload の個人情報(コメント)・ネタバレ(配役/犯人)の取扱いに注意。")

    # --- テストログ（テスターが結果を提出するためのダウンロード） ---
    # ★安定版（β公開）では節ごと出さない（テスター名欄含む・一般ユーザーの報告経路は
    #   報告フォーム/X＝ユーザー指示 2026-07-24）。
    if not _IS_STABLE:
        st.divider()
        st.subheader("🧪 テストログ")
        tester_name = st.text_input("テスター名（任意）", key="tester_name")
        _log = st.session_state.setdefault("qa_log", [])
        n_fb = sum(1 for e in _log if e.get("feedback"))
        st.caption(f"このセッションの記録: {len(_log)}件（フィードバック {n_fb}件）")
        if _log:
            _stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            # テスターはこの .jsonl だけ送ればOK。
            st.download_button(
                "📥 ログをダウンロード (.jsonl)",
                build_session_log_jsonl(_log, tester_name),
                file_name=f"rooper_testlog_{_stamp}.jsonl", mime="application/json",
            )
            st.caption("この .jsonl ファイルを作者に送ってください🙏")
            # .md（人間可読）は開発者モードのときだけ（テスターには不要）。
            if dev_mode:
                st.download_button(
                    "📥 (開発者用) .md でもダウンロード",
                    build_session_log_md(_log, tester_name),
                    file_name=f"rooper_testlog_{_stamp}.md", mime="text/markdown",
                )

system_prompt = build_system_prompt(kb)

# --- 盤面ビルダー（GUIで盤面→翻訳を通さず直接裁定。APIキー不要） ---
with st.expander("🛠️ 盤面ビルダー（翻訳を通さず直接裁定）", expanded=False):
    render_builder(feedback_ui=feedback_ui, app_version=APP_VERSION, build_info=get_build_info())

if "messages" not in st.session_state:
    st.session_state.messages = []
st.session_state.setdefault("qa_log", [])

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        _idx = m.get("log_idx")
        if m["role"] == "assistant" and _idx is not None and _idx < len(st.session_state.qa_log):
            render_engine_extras(st.session_state.qa_log[_idx])
            st.markdown(m["content"])
            feedback_ui(_idx)
        else:
            st.markdown(m["content"])

# --- サンプル質問（ワンタップ投入・テスター機能 Phase 2 / 2026-07-06） ---
_SAMPLE_QUESTIONS = {
    "行動解決": "病院に暗躍+2と暗躍禁止が置かれています。病院の暗躍はいくつになりますか？",
    "カルティスト": "神社にカルティストがいて、神社に暗躍+1と暗躍禁止が置かれました。神社の暗躍はどうなりますか？",
    "脚本家能力": "クロマクの暗躍+1は、主人公の暗躍禁止で止められますか？",
    "不安の解決": "不安が1のキャラに、脚本家が不安+1、主人公が不安-1を置きました。最終的な不安はいくつですか？",
    "事件の発生": "犯人が生存していて、不安が不安臨界と同じとき、事件は発生しますか？",
    "友好能力": "鑑識官の友好能力は空撃ち（対象がいなくても宣言）できますか？",
    "ループ終了": "敗北条件がループの途中で成立したら、すぐにそのループは終わりますか？",
    "役職・特性": "イレギュラーはどんな役職に配役できますか？",
}
with st.expander("💡 サンプル質問（ワンタップで送信）", expanded=False):
    st.caption("フェイズ別の例題です。ボタンを押すとそのまま質問できます（お試し・動作確認に）。")
    _scols = st.columns(2)
    for _i, (_label, _q) in enumerate(_SAMPLE_QUESTIONS.items()):
        if _scols[_i % 2].button(_label, key=f"sample_q_{_i}",
                                 use_container_width=True, help=_q):
            st.session_state["_pending_prompt"] = _q
            st.rerun()

# チャット入力。サンプルボタンで積んだ質問（_pending_prompt）があればそれを採用。
prompt = st.chat_input("ルールの質問をどうぞ（例: 鑑識官の友好能力は空撃ちできる？）") \
    or st.session_state.pop("_pending_prompt", None)
# ★C-15：支出上限に達したら以降の送信も静かに休止案内を継続（赤いスタックトレースを見せない）。
if st.session_state.get("_ai_quota_paused"):
    st.warning(_AI_PAUSED_MSG)
    prompt = None                       # 翻訳/回答APIを呼ばない
# ★C-13：β費用防御＝1セッションの質問数上限（超過は翻訳/回答APIを呼ばない）。支出上限(C-4)と二段構え。
_ASK_LIMIT = 10
if prompt is not None and st.session_state.get("chat_ask_count", 0) >= _ASK_LIMIT:
    # ★C-14：上限到達時はスキル版DL（ホーム）を案内（旧「X DMで」→公開DLに差し替え）。
    st.info(f"β期間中は1セッション {_ASK_LIMIT} 問までです。ページを開き直すと再度使えます。"
            "たくさん質問したい方は、ホームからスキル版（Claude.ai用・質問数無制限）をダウンロードできます。")
    prompt = None                       # 送信を無効化＝以降の翻訳/回答APIを呼ばない
if prompt:
    st.session_state["chat_ask_count"] = st.session_state.get("chat_ask_count", 0) + 1
    if not api_key:
        st.error(
            "APIキーが設定されていません。"
            "サイドバーにご自分のキーを入力するか、アプリ管理者にお問い合わせください。"
        )
        st.stop()

    # A コスト保護（AIC handoff）：アプリ共有キー利用時のみ日次クォータ（自前キー入力時は対象外）。
    #   未設定/障害時は fail-open（cloud.check_quota が True を返す）＝可用性優先。
    if not user_key.strip():
        _limit = int(_cloud_config_cached().get("daily_limit", _DEFAULT_DAILY_LIMIT))
        _ok, _used = cloud.check_quota(_limit)
        if not _ok:
            st.error("本日の無料利用上限に達しました🙏 続けてお使いになるには、サイドバーに"
                     "ご自分の Anthropic APIキーを入力してください（自分のキーなら上限はありません）。")
            st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    client = anthropic.Anthropic(api_key=api_key)

    # --- 正誤チェッカー: 質問→盤面JSON→エンジン裁定 ---
    # 翻訳器は選択モデル（既定Haiku）。回答は回答モデル（Sonnet等）。両者は分離。
    engine_data, trans_dbg = translate_question(client, translator_model, st.session_state.messages)
    if engine_data:
        outcome, _eng_err = run_engine(engine_data)
    else:
        outcome, _eng_err = None, trans_dbg.get("reason")  # 翻訳が非起動を選んだ理由
    # 非起動の理由（翻訳失敗 or 裁定拒否）。翻訳生出力は debug 用に保持。
    non_engage_reason = _eng_err
    trans_raw = trans_dbg.get("raw", "")
    # 共通イベント：質問（★内容＝質問文/配役/犯人は送らない＝メタのみ）。engine起動有無・モデル。
    cloud.log_event("ask", model=model, engine=bool(engine_data),
                    own_key=bool(user_key.strip()), app_version=APP_VERSION)

    engine_addendum = None
    confirm_md = None
    if outcome is not None:
        # デバッグランナーと同一の強化済み注入文に統一（移動結果の釘刺し・用語注意を共有）。
        engine_addendum = engine_addendum_text(outcome)
        if outcome.needs_confirmation:
            confirm_md = render_confirmation_board(outcome)

    # system: KB（キャッシュ）ブロック ＋ 必要ならエンジン裁定ブロック。
    # 1ブロック目は従来と同一文字列なのでプロンプトキャッシュは引き続き効く。
    system_blocks = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    if engine_addendum:
        system_blocks.append({"type": "text", "text": engine_addendum})

    # 空回答の原因究明用に、ストリーム完了後の stop_reason 等を拾う受け皿。
    _diag: dict = {}

    def stream_response():
        with client.messages.stream(
            model=model,
            max_tokens=4096,  # 長い可能性列挙が途中で切れないよう拡大（旧2048で truncation 報告あり）
            system=system_blocks,
            messages=[
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages
            ],
        ) as stream:
            for text in stream.text_stream:
                yield text
            # ストリーム完了後にメタ情報を回収（空回答時の診断＝stop_reason 等）。
            try:
                fm = stream.get_final_message()
                _diag["stop_reason"] = getattr(fm, "stop_reason", None)
                _diag["n_content_blocks"] = len(getattr(fm, "content", []) or [])
            except Exception:  # noqa: BLE001
                pass

    # ログエントリ＝この回答1件の完全な記録（盤面ビジュアル込み）。
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "question": prompt,
        "model": model,
        "app_version": APP_VERSION,
        "build": get_build_info(),
        "engaged": outcome is not None,
        "engine_error": non_engage_reason,
        "translation": engine_data,
        "translation_raw": trans_raw,
        "verdict": render_verdict(outcome) if outcome is not None else "",
        "board_in": board_html_from_json(engine_data) if engine_data else None,
        "board_out": board_html_from_outcome(outcome, engine_data),
        "answer": "",
        "feedback": None, "fb_tags": [], "fb_comment": "",
    }

    with st.chat_message("assistant"):
        shown_parts: list[str] = []
        # エンジンバッジ＋盤面ビジュアル＋裁定expander（履歴再描画と同じ部品）。
        render_engine_extras(entry)
        # 感度フラグありなら、回答前に確認盤面（2×2＋仮定＋⚠）を提示。
        if confirm_md:
            st.markdown(confirm_md)
            shown_parts.append(confirm_md)
        _msgs = [{"role": m["role"], "content": m["content"]}
                 for m in st.session_state.messages]
        try:
            full = st.write_stream(stream_response)
        except Exception as e:  # noqa: BLE001
            # ★C-15：課金/上限系は休止案内、それ以外は一時障害案内（赤いスタックトレースは見せない）。
            if _is_quota_error(e):
                st.session_state["_ai_quota_paused"] = True   # 以降の送信も静かに案内継続
                st.warning(_AI_PAUSED_MSG)
                full = _AI_PAUSED_MSG
            else:
                st.warning("一時的なエラーが発生しました。少し時間をおいて再試行してください"
                           "（続く場合は 🐛 からご報告ください）。")
                full = ""
        # 空回答対策：複雑な盤面で稀にLLMのストリームが空になる。
        # ① 非ストリームで1回リトライ → ② それでも空ならエンジン裁定を回答として提示
        #    （エンジン起動時は決定的な確定結果があるので、必ず答えを見せられる）。
        #    ★C-15：課金/上限で休止した時はリトライしない（同じ上限を再度叩かない）。
        if not (full or "").strip() and not st.session_state.get("_ai_quota_paused"):
            try:
                full = answer_once(client, model, _msgs, system_blocks)
            except Exception as _e2:  # noqa: BLE001
                if _is_quota_error(_e2):
                    st.session_state["_ai_quota_paused"] = True
                    st.warning(_AI_PAUSED_MSG)
                    full = _AI_PAUSED_MSG
                else:
                    full = ""
            if (full or "").strip():
                st.markdown(full)
        if not (full or "").strip():
            # 原因究明：空回答時のstop_reason等をログに残す（テスター提出jsonlで後追いできる）。
            entry["empty_diag"] = dict(_diag)
            if outcome is not None:
                full = ("※AIの文章生成が空だったため、エンジンの確定裁定をそのまま表示します。\n\n"
                        + render_verdict(outcome))
            else:
                full = "（回答が空でした。もう一度お試しください。エラーが続く場合は🐛でご報告を）"
            st.markdown(full)
        shown_parts.append(full)
        entry["answer"] = full
        st.session_state.qa_log.append(entry)
        log_idx = len(st.session_state.qa_log) - 1
        feedback_ui(log_idx)

    # 履歴に残す本文 = 確認盤面(あれば) ＋ 回答本文。log_idx で盤面・フィードバックに再接続。
    st.session_state.messages.append(
        {"role": "assistant", "content": "\n\n".join(shown_parts), "log_idx": log_idx}
    )


# ==== 開発者ツール（デバッグ：想定問答ランナー）＝ページ下部に配置 ====
if dev_mode:
    # --- 通しシミュレーション（連結裁定・実験）＝sim/ を叩いて1ゲーム流す ---
    with st.expander("🎬 通しシミュレーション（連結裁定・実験）", expanded=False):
        try:
            from sim_runner import render_sim_runner
            render_sim_runner()
        except Exception as _e:  # noqa: BLE001  # sim側WIPのimport等で落ちても本体は生かす
            st.error(f"通しシミュレーションを読み込めません: {type(_e).__name__}: {_e}")

    # --- デバッグ：想定問答ランナー（選択→一括実行→統合レポート） ---
    with st.expander("🧪 デバッグ：想定問答ランナー", expanded=False):
        st.caption(
            "選んだ設問をAPIで一括実行し、AIに渡しやすい統合レポートを作ります。"
            "各設問につきAPIを2回（翻訳＋回答）呼びます。"
        )
        _scn = load_scenarios()
        if not _scn:
            st.info(
                "想定問答データ（tests/scenarios.py）が見つかりません。"
                "リポジトリに `tests/scenarios.py` を追加すると、このデバッグランナーが有効になります。"
            )
        _cats: list[str] = []
        for _s in _scn:
            if _s["category"] not in _cats:
                _cats.append(_s["category"])

        c1, c2 = st.columns([1, 2])
        only_warn = c1.checkbox("⚠ だけ", value=False)
        cat_sel = c2.multiselect("カテゴリで絞る（空＝全部）", options=_cats, default=[])

        pool = _scn
        if only_warn:
            pool = [s for s in pool if "⚠" in s.get("tags", [])]
        if cat_sel:
            pool = [s for s in pool if s["category"] in cat_sel]
        pool_ids = [s["id"] for s in pool]
        warn_ids = [s["id"] for s in pool if "⚠" in s.get("tags", [])]

        q_of = {s["id"]: s["question"] for s in _scn}
        chosen = st.multiselect(
            "流す設問を選ぶ", options=pool_ids,
            default=(warn_ids or pool_ids),
            format_func=lambda i: f"{i}: {q_of[i][:26]}…",
        )
        dcol1, dcol2 = st.columns(2)
        dbg_label = dcol1.radio("回答モデル", list(MODELS.keys()), index=0, key="dbg_model")
        dbg_model = MODELS[dbg_label]
        dbg_tr_label = dcol2.radio("翻訳器モデル", list(MODELS.keys()), index=_tr_default, key="dbg_tr")
        dbg_translator = MODELS[dbg_tr_label]
        st.caption(
            "『翻訳経由』列＝LLM翻訳→エンジンの結果（＝翻訳精度の実測）。"
            "『エンジン』列＝engine_input直叩き（＝翻訳を介さない決定的裁定）。"
            "翻訳器を切替えて『翻訳経由』のPASS数を比べれば、どのモデルが正確に翻訳できるか分かる。"
        )

        run_dbg = st.button(f"▶ 実行（{len(chosen)}件）", disabled=not chosen or not api_key)
        if not api_key:
            st.info("APIキーが未設定です（サイドバーまたは Secrets に設定してください）。")

        if run_dbg:
            client = anthropic.Anthropic(api_key=api_key)
            target = [s for s in _scn if s["id"] in chosen]
            results: list[dict] = []
            prog = st.progress(0.0, text="準備中…")
            for i, sc in enumerate(target, 1):
                prog.progress((i - 1) / len(target), text=f"{sc['id']} を実行中…")
                results.append(run_one_scenario(client, dbg_model, sc, system_prompt,
                                                translator_model=dbg_translator))
                prog.progress(i / len(target), text=f"{sc['id']} 完了")
            prog.empty()
            st.session_state["debug_results"] = results
            st.session_state["debug_translator"] = dbg_translator
            st.session_state["debug_model"] = dbg_model

        if st.session_state.get("debug_results"):
            results = st.session_state["debug_results"]
            npass = sum(1 for r in results if r["auto"] == "PASS")
            nfail = sum(1 for r in results if r["auto"] == "FAIL")
            nskip = len(results) - npass - nfail
            st.markdown(f"**エンジン採点: {len(results)}件 ／ ✅PASS {npass}・❌FAIL {nfail}・➖{nskip}**")
            # 翻訳経由の精度（翻訳→エンジンが正解にたどり着いたか＝翻訳器の実力）。
            gradable = [r for r in results if r.get("auto_live") in ("PASS", "FAIL")]
            if gradable:
                lp = sum(1 for r in gradable if r["auto_live"] == "PASS")
                leng = sum(1 for r in results if r.get("live_engaged"))
                tr = st.session_state.get("debug_translator", "")
                tr_name = next((k for k, v in MODELS.items() if v == tr), tr)
                st.markdown(
                    f"**翻訳経由の精度（翻訳器={tr_name}）: 採点可{len(gradable)}件中 "
                    f"✅{lp}・❌{len(gradable) - lp} ／ エンジン起動 {leng}/{len(results)}件**"
                )
            st.dataframe(
                [{"id": r["id"],
                  "エンジン": "起動" if r["engaged"] else "非起動",
                  "値": result_value_str(r),
                  "判定": r["auto"],
                  "翻訳起動": "○" if r.get("live_engaged") else "✕",
                  "翻訳経由": r.get("auto_live", "—")} for r in results],
                hide_index=True, use_container_width=True,
            )
            report = build_debug_report(results, st.session_state.get("debug_model", dbg_model))
            st.caption("↓ この内容をコピーしてAIに貼ると、まとめて点検できます")
            st.code(report, language="markdown")
            st.download_button("レポートをダウンロード (.md)", report,
                               file_name="rooper_debug_report.md", mime="text/markdown")
