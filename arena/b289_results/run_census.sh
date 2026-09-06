#!/usr/bin/env bash
# B-289 (1) センサス（計測のみ・agents は無変更）。★単独実行。
set -u
cd "$(dirname "$0")/../.."
R=arena/b289_results
run() { local d=$1 p=$2 m=$3; local o="$R/d${d}_${p}_${m}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_probe \
      --days "$d" --perm "$p" --mode "$m" --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in id rev h1 rot1; do run 3 "$p" cur; done
echo D3DONE > "$R/.census_d3_done"
for p in id rev h1 rot1; do run 5 "$p" cur; done
echo ALLDONE > "$R/.census_done"
