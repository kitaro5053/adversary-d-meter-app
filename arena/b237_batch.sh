#!/usr/bin/env bash
# B-237 測定バッチ（★他レーンのベンチ／pytest の終了を待ってから単独実行する。kill はしない＝規約 §14）
set -u
cd "$(dirname "$0")/.."
OUT=docs/仮_b237_log
mkdir -p "$OUT"

# ★他レーンの**ベンチ級**測定（`arena.benchmark` / `arena.b*_ab`）と pytest の終了だけを待つ。
#   他レーンの単局プローブ（`arena.b*_probe`）は待たない＝待つと無限に譲ることになるため
#   （2026-08-17 の実際：`/home/user/wt-b238` の probe が長時間走っていた）。
#   ★kill は一切しない＝規約 §14。値の非汚染は `base@2` の再測（per-game 完全一致）と
#   `base` が現行正典値と一致することで裏取りする。
wait_free() {
  while pgrep -f "^python -m arena\.benchmark" >/dev/null \
     || pgrep -f "^python -m arena\.b[0-9]*_ab" >/dev/null \
     || pgrep -f "^python -m pytest" >/dev/null; do
    echo "  … 他レーンのベンチ／pytest の終了を待機中" >&2
    sleep 30
  done
}

for days in 5 3; do
  for perm in id rev h1; do
    wait_free
    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b237_ab \
      --days "$days" --perm "$perm" --conds base,base+b237,base@2 \
      --out "$OUT/ab_d${days}_${perm}.json" 2>&1 | tee "$OUT/ab_d${days}_${perm}.log"
  done
done
echo "DONE"
