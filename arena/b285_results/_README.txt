B-285〔臨界0への冷却の空振り〕計測結果（計測のみ・land 判断は FableA→ユーザー）

生成コマンド（PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8・単独実行）:
  python -m arena.b285_probe --days {3,5} --perm {id,rev,h1,rot1} --mode {off,on,live} --json ...

mode:
  off  = 現行既定（B285_SKIP_DEAD_COOL=False / B285_LIVENESS_BREAK=False）
  on   = プロトタイプ（B285_SKIP_DEAD_COOL=True）
  live = liveness 検査（B285_LIVENESS_BREAK=True＝該当席にだけ巨大値）

_judgement_table.txt = 3層＋番人の判定表（scratchpad の analyze スクリプト出力）。

★述語の版について：初回は無ガードの `th == 0` で全24条件を測り、その後
妄想拡大ウイルスの逃がしを入れた狭い述語（`_b285_cool_is_inert`）で全24条件を測り直した。
**24条件すべての要約が完全一致**（該当席の不安が全数1＝ウイルスの帯 u>=2 に1席も入らない）。
ここに残すのは狭い述語版（後者）。

------------------------------------------------------------------------------
land 後（既定 ON）の再確認＝`land_*`（2026-08-31）
------------------------------------------------------------------------------
- `land_bench3.txt` / `land_bench5.txt`＝**リポジトリ既定のまま**（B285_SKIP_DEAD_COOL=True）の
  `python -m arena.benchmark`。3日級 `defense:132 fb_loss:8`・平均 3.064／
  5日級 `defense:69 fb_loss:8 fb_win:2 loss:1`・平均 3.812＝**両ベンチ正典値と一致**
  （＝今回の land は「無駄札が消えるだけ」で正典値を動かさない）。
- `land_d5_{perm}_{off,on}.txt`（perm 4条件）と `land_d3_id_{off,on}.txt`＝land 後の probe。
  **land 前の `d5_*` / `d3_id_*` と全ファイル完全一致**（生 JSON は重複なので置いていない）。
