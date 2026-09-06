#!/usr/bin/env bash
# B-289 追加発注＝番人 D1/D4 増分の全数検死（5日級・perm id と rot1・cur vs b40）。★単独実行。
set -u
cd "$(dirname "$0")/../.."
R=arena/b289_results
run() { local p=$1 m=$2; local o="$R/lint_d5_${p}_${m}"
  [ -f "$o.json" ] && { echo "skip $o"; return; }
  PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b289_d1_audit \
      --days 5 --perm "$p" --mode "$m" --quiet --json "$o.json" > "$o.txt" 2>&1
  echo "done $o"; }
for p in id rot1; do for m in cur b40; do run "$p" "$m"; done; done
echo ALLDONE > "$R/.d1audit_done"
