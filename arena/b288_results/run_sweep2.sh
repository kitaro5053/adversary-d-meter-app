#!/usr/bin/env bash
# B-288 掃引 第2陣＝4日級（id/rev/h1＝§72-122 の3本）と5日級（既定 ON を壊していないか）。
set -u
cd "$(dirname "$0")/../.."
R=arena/b288_results
run() { local d=$1 p=$2 m=$3; local o="$R/d${d}_${p}_${m}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b287_probe \
      --days "$d" --perm "$p" --mode "$m" --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in id rev h1; do for m in cur5 on4 on4g; do run 4 "$p" "$m"; done; done
echo D4DONE > "$R/.sweep_d4_done"
for p in id rev h1 rot1; do for m in cur5 cur5g; do run 5 "$p" "$m"; done; done
echo ALLDONE > "$R/.sweep_d5_done"
