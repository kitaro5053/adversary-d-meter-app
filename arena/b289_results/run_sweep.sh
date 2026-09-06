#!/usr/bin/env bash
# B-289 掃引（cur ＋ 加点 20/40/60）。★単独実行（他レーンの測定と並走させない）。
set -u
cd "$(dirname "$0")/../.."
R=arena/b289_results
run() { local d=$1 p=$2 m=$3; local o="$R/s_d${d}_${p}_${m}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_probe \
      --days "$d" --perm "$p" --mode "$m" --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in id rev h1 rot1; do for m in cur b20 b40 b60; do run 3 "$p" "$m"; done; done
echo D3DONE > "$R/.sweep_d3_done"
for p in id rev h1 rot1; do for m in cur b20 b40 b60; do run 5 "$p" "$m"; done; done
echo ALLDONE > "$R/.sweep_done"
