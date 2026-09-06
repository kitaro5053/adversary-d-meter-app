#!/usr/bin/env bash
# B-288 掃引 第3陣＝guard "ever" 形の残り perm（3日級）。
set -u
cd "$(dirname "$0")/../.."
R=arena/b288_results
run() { local d=$1 p=$2 m=$3; local o="$R/d${d}_${p}_${m}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b287_probe \
      --days "$d" --perm "$p" --mode "$m" --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in rev h1 rot1; do run 3 "$p" abge; done
echo DONE > "$R/.sweep_ever_done"
