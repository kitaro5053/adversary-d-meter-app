"""惨劇RoopeR 裁定エンジン CLI 入口（Claude.ai Skill 用）。

app.py（Streamlit）の translate→adjudicate→render→engine_addendum の流れを
1コマンドに束ねたもの。Claude.ai のコード実行ツールから

    python run_engine.py board.json
    （または  echo '<JSON>' | python run_engine.py  ）

で呼び出し、標準出力の「エンジン裁定（決定的・確定済み・最優先）」ブロックを
そのまま最終回答の根拠にする。数値の再計算は禁止（この出力が確定）。
"""

from __future__ import annotations

import json
import os
import sys

# 同梱された engine/ をimport可能にする（このファイルと同じ階層に engine/ がある想定）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 日本語（エリア名・役職名）を確実に扱うため入出力をUTF-8に固定する。
# Windows の既定 cp932 だと stdin の日本語が壊れて「未知のエリア」になるため。
for _stream in (sys.stdout, sys.stderr):
    reconfig = getattr(_stream, "reconfigure", None)
    if reconfig:
        reconfig(encoding="utf-8")

from engine.orchestrate import adjudicate  # noqa: E402
from engine.render import (  # noqa: E402
    render_confirmation_board,
    render_verdict,
)
from engine.translate import TranslationError  # noqa: E402


ADDENDUM_HEADER = (
    "# エンジン裁定（決定的・確定済み・最優先）\n"
    "下記はルールエンジンが厳密計算した【確定結論】です。あなたの役割は、この結論を\n"
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
    "移動が成立した先にしか働かない。"
    "（例: エンジンが「異世界人は禁止エリアで移動できず神社に留まる」と言ったら、"
    "病院へ移動した前提に作り替えない。移動が不成立なら病院の暗躍禁止は無視されず実効し、病院は +0 のまま）。\n"
    "6. **数値以外の結論もエンジン確定**：事件の「発生する/しない」、友好能力の「使える/使えない」と"
    "拒否可否（不可/任意/強制）、ループの「終了する/しない」と勝敗は、下記の結論をそのまま使う。"
    "自分のフェイズ順推論で上書きしない（例: シリアルキラーの殺害はターン終了フェイズ＝"
    "主人公能力フェイズより後、敗北条件の途中成立ではループは中断しない）。\n\n"
)

CONFIRM_NOTE = (
    "\n\n# 感度/確認フラグあり\n"
    "**上記の確定数値そのものは変えないこと。**この『感度』は、もし盤面が違っていれば"
    "（例: 主人公の暗躍禁止がもう1枚あれば）結論が反転しうる、という"
    "仮定依存の注意書きにすぎません。今の盤面での最終値はエンジンの数字が確定です。"
    "回答冒頭で『未指定カードは無しと仮定した結論』である旨を一言添えるに留めてください。\n"
)


def _read_input() -> str:
    """引数のファイル、無ければ標準入力からJSON文字列を得る（UTF-8固定）。"""
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            return f.read()
    # stdin もバイト列からUTF-8で復号（プラットフォーム既定エンコーディングに依存しない）。
    return sys.stdin.buffer.read().decode("utf-8")


def main() -> int:
    raw = _read_input().strip()
    if not raw:
        print("入力JSONが空です。盤面JSONを渡してください。", file=sys.stderr)
        return 2

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSONパース失敗: {e}", file=sys.stderr)
        return 2

    try:
        outcome = adjudicate(data)
    except TranslationError as e:
        # スキーマ拒否＝前提が創作/矛盾。回答層は「エンジン起動せず」を可視化して通常回答へ。
        print(f"[エンジン非起動] 翻訳/スキーマ拒否: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[エンジン非起動] 裁定エラー: {type(e).__name__}: {e}")
        return 1

    out = ADDENDUM_HEADER + render_verdict(outcome)
    if outcome.needs_confirmation:
        out += "\n\n" + render_confirmation_board(outcome) + CONFIRM_NOTE
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
