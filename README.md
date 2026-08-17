# 惨劇RoopeR ルール相談AI（First Steps & Basic Tragedy X）

`rules/` 配下の構造化ルール知識ベース（KB）だけを根拠に、惨劇RoopeR 5th の
ルール質問へ答える Streamlit チャットアプリです。
**知識ベースに無いことは創作せず「原本確認が必要」と答える**安全設計。

これは大プロジェクトの「第1歩（④ルール相談AI）」です。ここで作る知識ベースは、
将来のルールエンジンの仕様にそのまま転用できます（＝二度手間にならない）。

---

## 0. 構成

```
rooper-rule-ai/
├── app.py              # Streamlit アプリ本体（チャット＋正誤チェッカー＋デバッグランナー）
├── requirements.txt
├── README.md
├── rules/              # 知識ベース（番号順に全連結して system に注入）
│   ├── 00_rules_core.md … 70_script_creation_guide.md（8本）
├── engine/             # 決定的判定エンジン（1ターン9フェイズの裁定対象を全実装）
│   └── models / board / data / resolver / mm_phase / incident / goodwill / loop_end /
│       translate / orchestrate / render / checker
├── skill/              # Claude.ai用スキル（engine+rulesを同梱zip化。APIを叩かず裁定。skill/README.md 参照）
└── tests/              # pytest 一式＋想定問答（scenarios.py はデバッグランナー用の構造化データ）
```

`app.py` は起動時に `rules/*.md` を**ファイル名の番号順に全連結**して1本のKBにし、
それを system プロンプトに丸ごと載せます。ファイルを足す/減らすだけでKBを更新できます
（ベクトルDB不要）。数え上げ・判定系の質問（行動解決の暗躍/移動/不安/友好、脚本家能力フェイズ、
事件の発生判定、友好能力の使用可否、ループ終了のタイミング）では `engine/` が決定的に裁定し、
LLMの自然文回答と食い違えばエンジンを優先して訂正します。

**デバッグランナー**：アプリ上部の「🧪 デバッグ：想定問答ランナー」で、`tests/scenarios.py` の
設問を取捨選択して一括実行し、翻訳JSON・エンジン裁定・LLM回答・自動判定をまとめた
レポートを出力できます（コピー or `.md` ダウンロード）。設問の追加・編集は `tests/scenarios.py` で。

---

## 1. 動かす（ローカル）

```bash
cd rooper-rule-ai
python -m venv .venv && source .venv/bin/activate   # Windowsは .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

ブラウザが開いたら、左サイドバーに Anthropic APIキー を入力して質問します。
APIキーは https://console.anthropic.com で発行できます（環境変数 `ANTHROPIC_API_KEY` でも可）。

サイドバーの「モデル」で **Sonnet 5（既定・高精度）** と
**Haiku 4.5（高速・低コスト）** を切替できます。

---

## 2. 共有・布教する（Streamlit Community Cloud）

1. このフォルダ（`rules/` 込み）を GitHub リポジトリにpush。
2. https://share.streamlit.io で「New app」→ リポジトリと `app.py` を指定。
3. Settings → Secrets に `ANTHROPIC_API_KEY="sk-..."` を登録（自分のキーで全員分を負担する場合）。
   - 各自に自分のキーを入れてもらう運用なら、Secretsは空のままでOK（サイドバー入力を使う）。
4. 発行されたURLを共有すれば、ブラウザだけで使えます。

---

## 3. コストの目安

- 既定は高精度の **Sonnet 5**。KB全文を毎回 system に載せますが、
  **プロンプトキャッシュ**で2回目以降の入力は大幅に安くなります
  （`app.py` で `cache_control: ephemeral` を設定済み。KBが大きいほど効きます）。
- 低コスト・高速を優先したいときはサイドバーで **Haiku 4.5** に切替。
- 具体的な単価は変動するため、公開・共有前に
  [Anthropicの料金ページ](https://www.anthropic.com/pricing) で最新の入力/出力単価を確認してください。

---

## 4. 精度を上げる（KBの育て方）

知識ベースは「確実に分かっていることだけ」を書き、不明値は捏造せず
`要確認（実カード）` とマークしてあります。AIもその箇所は「原本確認が必要」と答えます。
**埋めた分だけAIが正確になります。** お手元の『主人公の書／脚本家の書』を見ながら、
おすすめ順で実カード固有値を補完してください:

1. `30_characters.md` … 各キャラの **不安臨界 / 初期エリア / 禁止エリア / 属性**（事件設計に直結）
2. `20_goodwill_abilities.md` … 各友好能力の **必要友好数（ハート数）**
3. `30_characters.md` … **黒猫の特性テキスト**（2種）

KB内の記法:
- 各ファイル冒頭の `<!-- ... -->` … 私的利用ヘッダ＋出典
- `★` … 注意点・公式正誤・FAQ要参照
- `→NN` … 他ファイルへの相互参照
- 役職は「人数上限／条文能力／追加能力／逆引きルール」で統一

裁定の優先順位（KB全体の方針）:
**書籍5th本文 ＞ 書籍FAQ ＞ 公式正誤 ＞ atwiki（証跡付） ＞ atwiki（証跡なし）**

> **対応範囲（スコープ）**：本KBは **First Steps＋Basic Tragedy X** を正式対応範囲とします。`[拡張]` タグのカード（ご神木・上位存在・希望カウンター等）は「BTXに混ぜて遊べる」範囲で属性・初期エリア等のみ収録しており、**拡張セット固有のルール要素（希望カウンター等）は本KBに未収録**です。**拡張を本格運用する場合は、各拡張セットの原本が別途必要**です（AIも未収録要素は「原本確認が必要」と答えます）。

---

## 5. 著作権について（重要）

正式なルール文言・脚本は BakaFire Party の著作物です。本KBは原本の自己使用複製として
作成しています。**個人・身内での利用**を前提としてください。
原文・脚本を含む形で**一般公開・配布する場合は、権利者（BakaFire Party）の許諾を必ず確認**してください。
KBには各サンプル脚本の配役・犯人などの真実は記載しておらず、AIも回答しません（ネタバレ防止）。

---

## 6. このあとの発展

- `rules/*.md` を JSON / 型定義へ構造化 → **ルールエンジン**の土台に。
- エンジンができたら、このAIに「正誤チェッカー」として接続し、裁定の正確性をさらに担保。
- 以降、シミュレーション環境 → GM補助（読み抜け／詰み検知）→ 自律プレイヤー へと積み上げ。
- ココフォリア併走（盤面はココフォリア、ルール判断はこのAI）も想定。
