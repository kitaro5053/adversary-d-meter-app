#!/usr/bin/env bash
# B-240 Phase 2 の測定バッチ（★単独実行・PYTHONHASHSEED=0）。
#   bash arena/b240_batch.sh bench   # ベースライン→ON→ベースライン再測（状態漏れ検査）
#   bash arena/b240_batch.sh seats   # 的（浪費席）の ON 側 census
#   bash arena/b240_batch.sh sweep   # 掃引（cap / min_u / gap）
set -u
export PYTHONHASHSEED=0
export PYTHONIOENCODING=utf-8
OUT=docs/仮_b240_log
mkdir -p "$OUT"

run() { echo "### $*"; python -m arena.b240_probe "$@"; }

case "${1:-bench}" in
bench)
  for d in 5 3; do
    for p in id rev h1; do
      run bench --days $d --perm $p        > "$OUT/bench_d${d}_off_${p}.log" 2>&1
      run bench --days $d --perm $p --on   > "$OUT/bench_d${d}_on_${p}.log"  2>&1
    done
  done
  # ★同一バッチ末尾でベースラインを再測＝状態漏れ検査（per-game 完全一致を要求）
  for d in 5 3; do
    for p in id rev h1; do
      run bench --days $d --perm $p      > "$OUT/bench_d${d}_off2_${p}.log" 2>&1
    done
  done
  ;;
seats)
  for d in 5 3; do
    for p in id rev h1; do
      run seats --days $d --perm $p --on --truth --wide \
        > "$OUT/seats_d${d}_on_${p}.log" 2>&1
    done
  done
  ;;
sweep)
  # ★適応的な掃引：まず 5日級 id で「何かが動く点があるか」を1周だけ見る。
  for v in cap0 cap6 cap40 minu0 gap2 floor lastn2; do
    case $v in
      cap0)   X="--cap 0.0" ;;
      cap6)   X="--cap 6.0" ;;
      cap40)  X="--cap 40.0" ;;
      minu0)  X="--set-min-u 0" ;;
      gap2)   X="--set-gap 2" ;;
      floor)  X="--scope floor" ;;
      lastn2) X="--last-n 2" ;;
    esac
    run bench --days 5 --perm id --on $X > "$OUT/bench_d5_on_id_${v}.log" 2>&1
    run bench --days 3 --perm id --on $X > "$OUT/bench_d3_on_id_${v}.log" 2>&1
  done
  ;;
sweep2)
  # 第2周＝1周目で動いた点だけを rev/h1 へ広げる（$2 に掃引点の名前を渡す）
  V="${2:?掃引点名が要る}"
  case $V in
    cap0)   X="--cap 0.0" ;;
    cap6)   X="--cap 6.0" ;;
    cap40)  X="--cap 40.0" ;;
    minu0)  X="--set-min-u 0" ;;
    gap2)   X="--set-gap 2" ;;
    floor)  X="--scope floor" ;;
    lastn2) X="--last-n 2" ;;
    *) echo "未知の掃引点 $V"; exit 1 ;;
  esac
  for p in rev h1; do
    run bench --days 5 --perm $p --on $X > "$OUT/bench_d5_on_${p}_${V}.log" 2>&1
    run bench --days 3 --perm $p --on $X > "$OUT/bench_d3_on_${p}_${V}.log" 2>&1
  done
  ;;
esac
echo "DONE $1"
