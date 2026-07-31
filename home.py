# -*- coding: utf-8 -*-
"""ホーム（インデックス）ページ — アプリを開いて最初に出る解説ページ。

Streamlitの素のUIではなく、HTML/CSSで組んだ「READMEのような解説ページ」を
st.markdown(unsafe_allow_html=True) で描く。各モードへの遷移は、モードごとの
ナビゲーションボタン（app_mode を切替）と、左サイドバーの「モード」radio の両方から可能。

★説明文は「ボタンに見えない」フラットなデザイン（左アクセントのみ・枠/角丸/塗り無し）にし、
その直下に明快なボタンを1つ置く＝説明を押し間違える混乱を防ぐ（ユーザー指摘 2026-07-10）。

app.py から render_home(app_version, build_info, mobile) で呼ぶ。
set_page_config は呼ばない（app.py 側が設定済み）。
"""

from __future__ import annotations

import os

import streamlit as st

# ★C-14：Claude.ai スキル版（本人のプラン内で質問数無制限）の公開DL。released zip は
#   skill/dist/ に追跡コミット（skill/build.sh 相当を python で生成）。無ければDL欄を出さない。
_SKILL_ZIP_PATH = os.path.join("skill", "dist", "sangeki-rooper-rules.zip")


def _skill_zip_bytes() -> bytes | None:
    try:
        with open(_SKILL_ZIP_PATH, "rb") as f:
            return f.read()
    except Exception:  # noqa: BLE001  未ビルド等はDL欄を出さない（壊れない）
        return None

# モードの説明（emoji, ラベル=app_modeの値, 見出し, 説明）。
# ★ラベルは app.py の radio options と完全一致させる（ナビボタンで app_mode に代入するため）。
_MODES = [
    ("🎮", "🎮 主人公としてプレイ", "主人公としてプレイ（一人回し）",
     "あなたが主人公3席を担当し、AI脚本家に挑む練習モード。"
     "コーチ（AIの推奨手・方針）やチュートリアル教材つきで、防衛の基本から学べます。"),
    ("🎭", "🎭 脚本家としてプレイ", "脚本家としてプレイ",
     "あなたが脚本家となり、AI主人公に挑みます。"
     "AI主人公の推理（内省パネル）を覗きながら、情報の隠し方・勝ち筋の作り方を試せます。"),
    ("🔁", "🔁 リプレイビューワー", "リプレイビューワー",
     "保存した対局ログ（.jsonl）を読み込み、日単位で盤面・推理の推移を振り返ります。"),
    ("💬", "💬 相談AI", "ルール相談AI",
     "ルールの疑問に、知識ベース（KB）＋決定的な裁定エンジンで答えます。"
     "暗躍・不安・友好・移動の数え上げや事件の発生判定を、毎回誤爆なく計算。"
     "知識に無いことは創作せず「原本を確認してください」と答えます。"),
]

_NOTES = [
    # ★C-11：前提・非公式・公式への問い合わせ防止（ユーザー確定文言＝そのまま）。連絡先も含む。
    "本サイトは BakaFire Party さん製作のボードゲーム『惨劇RoopeR』のゲーム知識があることを"
    "前提としています。本サイトは非公式のファンメイドサイトです。本サイトに関する質問・不具合などを"
    "公式（BakaFire Party）へ問い合わせないでください。ご連絡は作者（X "
    '<a href="https://x.com/kitaro5053dev" target="_blank" rel="noopener">@kitaro5053dev</a>）まで。',
    "対象は惨劇RoopeR 5th の First Steps（FS）と Basic Tragedy X（BTX）。拡張セットは範囲外です。",
    "特定の脚本のネタバレ（配役・犯人・バッドエンド条件）は扱いません。",
    # ★C-16：相談AIだけでなくプレイモード（ゲーム進行のルール処理）にも誤りが残りうる旨を明示。
    "β版のため、相談AIの回答だけでなく、プレイモード（ゲーム進行のルール処理）にも誤りが残っている"
    "可能性があります。裁定は原本（主人公の書／脚本家の書）が最終判断です。おかしな挙動を見つけたら"
    "🐛からご報告ください。",
]

# CSS は1回だけ注入する（ヒーロー＋フラットな説明＋注意書き）。
_STYLE = '''
<style>
.hb-wrap { max-width: 860px; margin: 0 auto; }
.hb-hero {
  border-radius: 16px; padding: 34px 30px 30px;
  background: linear-gradient(135deg, #2b2140 0%, #3a2a55 45%, #7a2140 100%);
  color: #f6f1ff; box-shadow: 0 8px 30px rgba(0,0,0,.28);
  border: 1px solid rgba(255,255,255,.08);
}
.hb-title { font-size: 1.9rem; font-weight: 800; letter-spacing:.02em; margin:0; line-height:1.25; }
.hb-sub { margin-top: 8px; opacity:.85; font-size:.98rem; }
.hb-badges { margin-top: 16px; display:flex; flex-wrap:wrap; gap:8px; }
.hb-badge {
  font-size:.78rem; padding:3px 11px; border-radius:999px;
  background: rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.18);
}
.hb-lead { margin: 22px auto 6px; font-size:1.0rem; line-height:1.85; opacity:.92; }
.hb-h { font-weight:700; margin: 26px 0 4px; font-size:1.05rem; opacity:.9; }
/* ★説明＝フラット（ボタンに見えないよう 枠/角丸/塗り は付けない・左アクセントのみ）。 */
.hb-desc { padding: 2px 2px 4px 13px; border-left: 3px solid rgba(150,110,190,.5); min-height: 4.6em; }
.hb-name { font-weight:700; font-size:1.0rem; margin-bottom:3px; }
.hb-text { font-size:.87rem; line-height:1.65; opacity:.8; }
.hb-notes { margin:0; padding-left:1.15em; font-size:.9rem; line-height:1.85; opacity:.8; }
</style>
'''


def _hero_html(with_title: bool = True) -> str:
    """トップの見出しHTML。with_title=False＝画像バナー使用時（タイトル/サブはバナー内に
    含まれるため重複させない・リード文とモード見出しだけ出す）。"""
    hero = '''
  <div class="hb-hero">
    <p class="hb-title">🎭 敵対D-Meter</p>
    <div class="hb-sub">惨劇RoopeR入門＆研究ツールβ ── ルール相談AI ＆ AIと学ぶ対戦練習（First Steps / Basic Tragedy X）</div>
  </div>''' if with_title else ''
    return f'''
<div class="hb-wrap">{hero}
  <p class="hb-lead">惨劇RoopeR（5th）のルール相談と、AIと対戦して学ぶ練習環境をまとめたページです。
  下のボタン、または左のメニュー「モード」から選んでください。</p>
  <div class="hb-h">モード</div>
</div>
'''


def _desc_html(emoji: str, head: str, text: str) -> str:
    return (f'<div class="hb-desc"><div class="hb-name">{emoji} {head}</div>'
            f'<div class="hb-text">{text}</div></div>')


def _notes_html() -> str:
    notes = "".join(f"<li>{n}</li>" for n in _NOTES)
    return (f'<div class="hb-wrap"><div class="hb-h">ご利用にあたって</div>'
            f'<ul class="hb-notes">{notes}</ul></div>')


def render_home(app_version: str = "", build_info: str = "",
                mobile: bool = False) -> None:
    """ホーム解説ページ＋各モードへのナビゲーションボタンを描く。

    ナビボタンは app_mode（サイドバーradioのkey）に代入して rerun＝以後の切替と同一経路。
    on_click コールバックは次回runのwidget生成前に走るため、radioのkeyへ安全に代入できる。
    """
    st.markdown(_STYLE, unsafe_allow_html=True)
    # ★オリジナルバナー（ユーザー制作 2026-07-25）：assets/banner.jpg があれば画像タイトル、
    #   無ければ従来のテキストタイトル（フォールバック＝ミラー欠落やローカルでも壊れない）。
    _banner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banner.jpg")
    if os.path.exists(_banner):
        try:
            st.image(_banner, use_container_width=True)
        except TypeError:  # 旧Streamlit（use_container_width未対応）
            st.image(_banner, use_column_width=True)
        st.markdown(_hero_html(with_title=False), unsafe_allow_html=True)
    else:
        st.markdown(_hero_html(), unsafe_allow_html=True)

    def _go(mode_label: str) -> None:
        st.session_state["app_mode"] = mode_label

    # モードごとに「フラットな説明」＋「その直下のボタン」を1組で並べる（PCは2列・スマホは1列）。
    ncols = 1 if mobile else 2
    rows = [_MODES[i:i + ncols] for i in range(0, len(_MODES), ncols)]
    for row in rows:
        cols = st.columns(len(row))
        for col, (emoji, label, head, text) in zip(cols, row):
            with col:
                st.markdown(_desc_html(emoji, head, text), unsafe_allow_html=True)
                st.button(f"▶ {head} を開く", key=f"home_go_{label}",
                          use_container_width=True, on_click=_go, args=(label,))

    st.markdown(_notes_html(), unsafe_allow_html=True)

    # ★C-14：たくさん質問したい方へ＝Claude.ai スキル版（zip）を直接配布（公開DL）。
    _zip = _skill_zip_bytes()
    if _zip:
        st.markdown('<div class="hb-wrap"><div class="hb-h">🧩 たくさん質問したい方へ（スキル版）'
                    '</div></div>', unsafe_allow_html=True)
        st.caption("下記の zip を Claude.ai のスキルとしてアップロードすると、ご自分の Claude プラン内で"
                   "質問数無制限に使えます（運営の月間上限に左右されません）。非公式・出典明記は zip 内の"
                   "SKILL.md をご確認ください。質問は X @kitaro5053dev へ。")
        st.download_button("⬇ スキル版をダウンロード（.zip）", data=_zip,
                           file_name="sangeki-rooper-rules.zip", mime="application/zip",
                           key="home_skill_dl")

    if app_version or build_info:
        st.caption(f"version {app_version}　·　build {build_info}")
