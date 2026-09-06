#!/usr/bin/env bash
# B-288 掃引（計測のみ）。★単独実行（他レーンの測定と並走させない）。
set -u
cd "$(dirname "$0")/../.."
R=arena/b288_results
run() {  # days perm mode
  local d=$1 p=$2 m=$3
  local out="$R/d${d}_${p}_${m}"
  [ -f "$out.json" ] && { echo "skip $out"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b287_probe \
      --days "$d" --perm "$p" --mode "$m" --quiet --json "$out.json" > "$out.txt" 2>&1
  echo "done $out"
}
for p in id rev h1 rot1; do
  for m in off ab abg; do run 3 "$p" "$m"; done
done
run 3 id abgs
run 3 id abge
echo ALLDONE > "$R/.sweep_d3_done"
