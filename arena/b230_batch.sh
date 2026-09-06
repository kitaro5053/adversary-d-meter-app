#!/bin/bash
# B-230 Phase 2 測定バッチ（単独実行前提・PYTHONHASHSEED=0）
# ログ＝docs/仮_b230_log/ 配下（チャンクごとに commit+push はエージェントが行う）
set -u
cd "$(dirname "$0")/.."
export PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8
LOG=docs/仮_b230_log

run() {  # run <logname> <args...>
    local name="$1"; shift
    echo "=== $name : $(date +%H:%M:%S) ==="
    python -m arena.b230_probe "$@" > "$LOG/$name.log" 2>&1
    echo "RC=$? $(tail -1 "$LOG/$name.log")"
}

# 1) ベースライン（OFF）と ON＝id（bit チェック＋per-game）
# done: bench_d3_off_id
run bench_d3_on_id  bench --days 3 --perm id --on
run bench_d5_off_id bench --days 5 --perm id
run bench_d5_on_id  bench --days 5 --perm id --on
# 2) perm rev / h1
run bench_d3_off_rev bench --days 3 --perm rev
run bench_d3_on_rev  bench --days 3 --perm rev --on
run bench_d3_off_h1  bench --days 3 --perm h1
run bench_d3_on_h1   bench --days 3 --perm h1 --on
run bench_d5_off_rev bench --days 5 --perm rev
run bench_d5_on_rev  bench --days 5 --perm rev --on
run bench_d5_off_h1  bench --days 5 --perm h1
run bench_d5_on_h1   bench --days 5 --perm h1 --on
# 3) 行為の的（ON の数え上げ）
run seats_d5_on seats --days 5 --on --verbose
run seats_d3_on seats --days 3 --on --verbose
# 4) cap 掃引（id）＝既定6.0 は 1) で測定済み
for cap in 0.0 20.0 40.0; do
    run bench_d5_on_id_cap$cap bench --days 5 --perm id --on --cap "$cap"
    run bench_d3_on_id_cap$cap bench --days 3 --perm id --on --cap "$cap"
done
echo "ALL DONE $(date +%H:%M:%S)"
