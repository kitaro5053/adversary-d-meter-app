# 行動解決リゾルバ（第1スライス）

KB（First Steps + Basic Tragedy X）を素材にした、**行動解決フェイズの決定的リゾルバ**。
一往復LLMが取りこぼした多段推論（カルティスト移動・暗躍禁止の自滅・死体セット・禁止エリア）を、
**毎回・誤爆なし**で裁定することを目的とする。

## なぜエンジンか（設計メモ）

検証で外れた設問（Q12/13/14/15/IP系）は、推論ではなく**盤面状態の機械的計算**だった。
プロンプトに注意書きを盛る対症療法は、隣の設問へ誤爆する（Q15対策がQ13を汚染した）。
ルールを一度きちんと符号化すれば、この相互汚染は原理的に起きない。
**信頼性はモデルの賢さでなくアーキテクチャの性質**、という結論の実装。

## 扱う範囲（現状）

1. **行動解決フェイズ（暗躍/移動）**
   - 盤面（2×2）と移動：合成（XOR／同種2枚）・禁止先で留まる（KB: 00,10,60 A-2）
   - 暗躍の解決：暗躍禁止による無効化／**複数主人公の暗躍禁止は自滅**／カルティストの暗躍禁止無視（移動後位置）／死体セット無効（KB: 10,40,00）
   - 解決順「移動 → その他」（KB: 00）
2. **脚本家能力フェイズ・不安**（`mm_phase.resolve_mm_phase_unrest`）
   - ミスリーダー(+1)／ファクター・学校暗躍2(+1)／医者(友好無視+友好2で+1)。KBに明文があるソースのみ確定。
   - 軍人・教師は脚本家フェイズ不可が確定（KB: 60 B-8）、マスコミは脚本家使用不可（B-6）＝寄与しない。
3. **脚本家能力フェイズ・暗躍**（`mm_phase.resolve_mm_phase_anyaku`）
   - クロマク(同エリア/自ボードに+1)／不穏な噂(任意ボードに+1)。暗躍禁止では止まらない（KB: 60 A16/Q3）。

範囲外（やらない）：事件の全解決、友好能力の全解決、ループ/勝敗判定、拡張セット固有要素。
不明値・未収録は**創作しない**（KBと同方針／未確定は要確認フラグ）。

## 設計の3点セット

1. **決定的リゾルバ** `resolve_action_phase(board)` — 部分盤面を inert補完（未指定の裁量カード＝無し、ただし黒猫等の強制効果は含める）の規約で解く。
2. **前提検証** `validate_board(board)` — 1ループ1回の二重使用／主人公が暗躍／ボードに暗躍以外／死体セット／同一対象二枚、をルール違反として指摘（自動補正しない）。
3. **感度チェック** `sensitivity_check(board, adj, target)` — 結論が inert仮定に依存する（load-bearing）なら、反転値つきでフラグ。人間のルールズ・ロイヤーより厳密に「曖昧さを明示して返す」。

## 使い方

```python
from engine import Board, Character, Placement, resolve_action_phase, sensitivity_check

b = Board()
b.add_character(Character("カルティスト", role="カルティスト", area="神社"))
b.add_placement(Placement("mastermind", "移動←→", "カルティスト", "character"))
b.add_placement(Placement("mastermind", "暗躍+2", "神社", "board"))
b.add_placement(Placement("p1", "暗躍禁止", "神社", "board"))

adj = resolve_action_phase(b)
print(adj.moves["カルティスト"])      # 病院
print(adj.targets["神社"].delta)       # 0
print(sensitivity_check(b, adj, "神社"))  # [現在0 / 他に暗躍禁止があれば2]
```

デモ: `python -m engine.demo` ／ テスト: `pytest tests/test_engine.py -q`（9件）

## ファイル

- `models.py` … 部分盤面・キャラ（友好カウンタ含む）・カード配置のデータ型
- `board.py` … 盤面隣接・移動合成・禁止先判定
- `data.py` … 役職→条文能力／カルティスト無視／キャラ禁止エリア（全35体）／脚本家能力フェイズの不安・暗躍ソース表（KB転記）
- `resolver.py` … 行動解決の解決・前提検証・感度チェック
- `mm_phase.py` … 脚本家能力フェイズの不安/暗躍リゾルバ（決定的）
- `translate.py` … 自然文LLM出力JSON → `Board`/`Question` への検証付き変換（捏造を弾く）
- `orchestrate.py` … JSON→裁定の一括アダプタ（phaseで分岐）＋ `TRANSLATION_PROMPT`
- `render.py` … エンジン裁定の決定的レンダリング（確認盤面2×2／裁定サマリ）
- `demo.py` … Q13の可読デモ

テスト: `pytest tests/ -q`（`test_engine.py` 9 ／ `test_translate.py` 7 ／ `test_render.py` 6 ／ `test_mm_phase.py` 16）

## 次の拡張（このスライスの自然な続き）

- `data.py` のキャラ禁止エリア・役職表を 30/40/50 から**全件転記**（今は判定に効く代表のみ）。
- LLM前段：自然文 → `Board` への翻訳（ここがエラー源なので、確認盤面UIで質問者に提示）。
- 感度チェックの軸を追加（移動の合成相手、友好禁止、強制効果の有無 など）。
- ロードマップ「(b) 正誤チェッカー」：LLM回答とエンジン裁定が食い違ったらエンジン優先で検算。
