# 敵対D-Meter — 惨劇RoopeR入門＆研究ツールβ

BakaFire Party さん製作のボードゲーム『惨劇RoopeR』（5th・First Steps / Basic Tragedy X）の
**非公式ファンメイド**ツールです。ルール相談AI・AI対戦（主人公側／脚本家側）・脚本工房・
リプレイビューワーを提供します。

> **本リポジトリはデプロイ用ミラーです。** 開発は非公開リポジトリで行われており、
> ここへの Issue / Pull Request は監視されない場合があります。
> ご連絡・不具合報告は作者 X [@kitaro5053dev](https://x.com/kitaro5053dev) へお願いします。

## 重要な注意

- 本ツールは**非公式**です。本ツールに関する質問・不具合などを
  **公式（BakaFire Party）へ問い合わせないでください**。
- 『惨劇RoopeR』のゲーム知識があることを前提としています。原作・サークル：
  惨劇RoopeR ／ BakaFire Party。
- β版のため、AIの回答・ゲーム進行のルール処理には誤りが残っている可能性があります。
  最終判断は原本（主人公の書／脚本家の書）でお願いします。

## ライセンス

- 自作コード部分＝MIT License（`LICENSE` 参照）。
- **`rules/` およびスキルzip内のルール記述＝BakaFire Party の知的財産＝MIT対象外**
  （出典明記付き転載・[二次創作ガイドライン](https://bakafire.main.jp/rooper/sr_dl_04_sozai.htm)に基づく）。
  詳細は `LICENSE` の EXCEPTION 節を参照。

## 実行

```
pip install -r requirements.txt
streamlit run app.py
```

`ANTHROPIC_API_KEY`（相談AI用）等の secrets は Streamlit の Settings → Secrets で設定します。
未設定でもエンジン裁定・AI対戦などのオフライン機能は動作します。
