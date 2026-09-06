#!/usr/bin/env bash
# B-245 の測定バッチ（★単独実行・PYTHONHASHSEED=0）。
#   bash arena/b245_batch.sh base    # ベースライン census（6条件）
#   bash arena/b245_batch.sh noise   # ノイズ対照点（無関係な切替口の反転）
#   bash arena/b245_batch.sh on      # 的A ON（6条件）
#   bash arena/b245_batch.sh onb     # 的B ON（6条件）
#   bash arena/b245_batch.sh base2   # 同一バッチ末尾の再測（状態漏れ検査）
set -u
export PYTHONHASHSEED=0
export PYTHONIOENCODING=utf-8
OUT=docs/仮_b245_log
mkdir -p "$OUT"

run() { echo "### $*" >&2; python -m arena.b245_probe census "$@"; }

case "${1:-base}" in
base|base2|base3)
  for d in 3 5; do
    for p in id rev h1; do
      run --days $d --perm $p --json "$OUT/${1}_d${d}_${p}.json" \
        > "$OUT/${1}_d${d}_${p}.log" 2>&1
    done
  done
  ;;
noise)
  # ★無関係な切替口の反転＝主指標のノイズ対照点（B-239／B-241 と同じ作法）
  for n in B205_KINSHI_GUARD=0 B221_BREAKER=0 B227_IRON_BAND=0; do
    for d in 3 5; do
      for p in id rev h1; do
        run --days $d --perm $p --noise "$n" --json "$OUT/noise_${n%%=*}_d${d}_${p}.json" \
          > "$OUT/noise_${n%%=*}_d${d}_${p}.log" 2>&1
      done
    done
  done
  ;;
on|onb)
  FLAG="--on"; [ "$1" = onb ] && FLAG="--on-b"
  for d in 3 5; do
    for p in id rev h1; do
      run --days $d --perm $p $FLAG --json "$OUT/${1}_d${d}_${p}.json" \
        > "$OUT/${1}_d${d}_${p}.log" 2>&1
    done
  done
  ;;
*) echo "未知のバッチ $1"; exit 1 ;;
esac
echo "DONE $1"
