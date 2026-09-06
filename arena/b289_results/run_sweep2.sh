#!/usr/bin/env bash
# B-289 掃引 第2陣＝(a) 発注文どおりの対照 scope=anyaku／(b) perm を2条件追加／(c) b30。
set -u
cd "$(dirname "$0")/../.."
R=arena/b289_results
run() { local d=$1 p=$2 m=$3; local o="$R/s_d${d}_${p}_${m}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_probe \
      --days "$d" --perm "$p" --mode "$m" --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in id rev h1 rot1; do run 5 "$p" a40; done
for p in h5 rot2; do for m in cur b40; do run 5 "$p" "$m"; done; done
run 5 id b30
echo ALLDONE > "$R/.sweep2_done"
