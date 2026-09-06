#!/usr/bin/env bash
# B-232 Phase 2 の測定バッチ（単独実行前提・既存ログはスキップ＝再起動から再開可）。
#   使い方: bash arena/b232_batch.sh
# 全て PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8。出力＝docs/仮_b232_log/。
set -u
cd "$(dirname "$0")/.."
export PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8
OUT=docs/仮_b232_log

run() {  # run <logfile> <args...>
  local f="$OUT/$1"; shift
  if [ -s "$f" ]; then echo "skip $f"; return 0; fi
  echo "run  $f : $*"
  python -m arena.b232_probe "$@" > "$f" 2>&1 || { rm -f "$f"; echo "FAIL $f"; return 1; }
  tail -1 "$f"
}

# --- ① 主軸（id）＝OFF ベースライン と ON 既定 ---------------------------
run bench_d5_off_id.log  bench --days 5 --perm id
run bench_d5_on_id.log   bench --days 5 --perm id --on
run bench_d3_off_id.log  bench --days 3 --perm id
run bench_d3_on_id.log   bench --days 3 --perm id --on
# --- ② 主指標（行為の数え上げ）＝ON 既定 ---------------------------------
run seats_d5_on.log      seats --days 5 --on
run seats_d3_on.log      seats --days 3 --on
# --- ③ perm 3条件（rev/h1） ---------------------------------------------
run bench_d5_off_rev.log bench --days 5 --perm rev
run bench_d5_on_rev.log  bench --days 5 --perm rev --on
run bench_d5_off_h1.log  bench --days 5 --perm h1
run bench_d5_on_h1.log   bench --days 5 --perm h1 --on
run bench_d3_off_rev.log bench --days 3 --perm rev
run bench_d3_on_rev.log  bench --days 3 --perm rev --on
run bench_d3_off_h1.log  bench --days 3 --perm h1
run bench_d3_on_h1.log   bench --days 3 --perm h1 --on
# --- ④ 掃引：広い版（供給算術を要求しない＝B-229 の字義） ----------------
run seats_d5_on_rs0.log  seats --days 5 --on --require-supply 0
run seats_d3_on_rs0.log  seats --days 3 --on --require-supply 0
run bench_d5_on_id_rs0.log bench --days 5 --perm id --on --require-supply 0
run bench_d3_on_id_rs0.log bench --days 3 --perm id --on --require-supply 0
# --- ⑤ 掃引：scope=floor（yield は B-224 のまま＝最小変更版） ------------
run seats_d5_on_floor.log  seats --days 5 --on --scope floor
run seats_d3_on_floor.log  seats --days 3 --on --scope floor
run bench_d5_on_id_floor.log bench --days 5 --perm id --on --scope floor
run bench_d3_on_id_floor.log bench --days 3 --perm id --on --scope floor
echo "=== batch done ==="
# --- ⑥ 掃引：scope=floor の perm 3条件（yield 側の影響の分離） --------------
run bench_d3_on_rev_floor.log bench --days 3 --perm rev --on --scope floor
run bench_d3_on_h1_floor.log  bench --days 3 --perm h1 --on --scope floor
run bench_d5_on_rev_floor.log bench --days 5 --perm rev --on --scope floor
run bench_d5_on_h1_floor.log  bench --days 5 --perm h1 --on --scope floor
echo "=== batch2 done ==="
