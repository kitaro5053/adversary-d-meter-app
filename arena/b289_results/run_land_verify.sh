#!/usr/bin/env bash
# B-289 land 後の実測再確認（mode=cur＝新しい既定そのもの）。★単独実行。
set -u
cd "$(dirname "$0")/../.."
R=arena/b289_results
run() { local d=$1 p=$2; local o="$R/land_d${d}_${p}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_probe \
      --days "$d" --perm "$p" --mode cur --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in id rot1; do run 3 "$p"; done
for p in id rev h1 rot1 h5 rot2; do run 5 "$p"; done
echo ALLDONE > "$R/.land_verify_done"
