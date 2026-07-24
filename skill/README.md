# 惨劇RoopeR ルール裁定スキル（Claude.ai 版）

Streamlit + Anthropic API の `app.py` を **Claude.ai（Web）の Agent Skill** に置き換えたもの。
API を叩かず、Max プランの Opus と直接会話しながら、同梱の決定的エンジンで裁定する。

## 仕組み
- `SKILL.md` … スキルの指示書（翻訳→エンジン実行→説明の手順、基本姿勢、盤面JSONスキーマ）。
- `run_engine.py` … 盤面JSON を受け取り `engine.adjudicate()`→`render` して確定裁定テキストを標準出力に出す CLI。
  Claude.ai のコード実行ツールから `python run_engine.py board.json` で呼ぶ。
- `engine/` `rules/` … リポジトリ直下の**同一物をビルド時にコピー**して同梱（複製を git に持たない）。

`app.py` / `translate.py` の TRANSLATION_PROMPT・engine_addendum の役割は SKILL.md と run_engine.py に移植済み。
`anthropic` / `streamlit` 依存は不要（engine は標準ライブラリのみ）。

## ビルド（アップロード用 zip を作る）
```powershell
# Windows
powershell -ExecutionPolicy Bypass -File skill/build.ps1
```
```bash
# macOS / Linux / Git Bash
bash skill/build.sh
```
→ `skill/dist/sangeki-rooper-rules.zip` が生成される。

## Claude.ai へのアップロード
1. Claude.ai の Settings → Capabilities → Skills を開く（コード実行/ファイル作成を有効化しておく）。
2. `sangeki-rooper-rules.zip` をアップロード。
3. 新規チャットで「惨劇RoopeRの…」とルール質問すれば description で自動起動する。

> 注意: Skill のコード実行機能が使えるプラン/設定が必要。私的利用の範囲で使うこと
> （再配布には BakaFire 氏の許諾が必要）。

## 更新フロー
`engine/` や `rules/` を直下で更新 → `pytest tests/ -q` で検証 → build を再実行して zip を作り直し → 再アップロード。
エンジンの正は常にリポジトリ直下。skill/ 側に engine のコピーを手で置かない。

## ローカル動作確認
```bash
echo '{"set":"BTX","question_target":{"name":"病院","kind":"board"},
"characters":[{"name":"異世界人","area":"病院","alive":true}],
"placements":[{"owner":"mastermind","card":"暗躍+1","target":"病院","target_kind":"board"}]}' \
  | python skill/run_engine.py
```
