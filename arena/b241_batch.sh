#!/usr/bin/env bash
# B-241 の測定バッチ（★単独実行・PYTHONHASHSEED=0）。
#   bash arena/b241_batch.sh noise   # ノイズ対照点（無関係な既 land 切替口を1つ OFF）
#   bash arena/b241_batch.sh base    # 厳格版のベースライン（OFF）＝的の分母
#   bash arena/b241_batch.sh on      # 腕(B) / 腕(A) / 両方 の ON 測定
#   bash arena/b241_batch.sh sweep   # 腕(B) の掃引（MAX_DROP / MIN_GAP）
# ★`seats` は同じ1走行で「行為の数え上げ」と「per-game ベンチ」の両方を出す
#   （`arena/benchmark.loops_to_win` と同一判定）＝測定回数を半分にするため。
set -u
export PYTHONHASHSEED=0
export PYTHONIOENCODING=utf-8
OUT=docs/仮_b241_log
mkdir -p "$OUT"

run() { echo "### $*"; python -m arena.b241_probe "$@"; }

case "${1:-base}" in
noise)
  for c in b221 b222 b225 b227 b205; do
    for d in 3 5; do for p in id rev h1; do
      run seats --days $d --perm $p --ctrl-off $c \
        > "$OUT/noise_${c}_d${d}_${p}.log" 2>&1
    done; done
  done
  ;;
base)
  for d in 3 5; do for p in id rev h1; do
    run seats --days $d --perm $p --json "$OUT/base2_d${d}_${p}.json" \
      > "$OUT/base2_d${d}_${p}.log" 2>&1
  done; done
  ;;
on)
  for m in ground yield ground,yield; do
    tag=$(echo "$m" | tr ',' '+')
    for d in 3 5; do for p in id rev h1; do
      run seats --days $d --perm $p --on "$m" --json "$OUT/on_${tag}_d${d}_${p}.json" \
        > "$OUT/on_${tag}_d${d}_${p}.log" 2>&1
    done; done
  done
  ;;
sweep)
  V="${2:?掃引点名が要る}"
  case $V in
    drop5)  X="--min-hist-gap 1" ; EXTRA="--ground-max-drop 5.0" ;;
    gap2)   X="--min-hist-gap 2" ; EXTRA="" ;;
    *) echo "未知の掃引点 $V"; exit 1 ;;
  esac
  for d in 3 5; do for p in id rev h1; do
    run seats --days $d --perm $p --on ground $X $EXTRA \
      > "$OUT/sweep_${V}_d${d}_${p}.log" 2>&1
  done; done
  ;;
esac
echo "DONE $1"
