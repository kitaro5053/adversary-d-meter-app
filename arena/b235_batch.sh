#!/bin/bash
# B-235 再測バッチ（測定専用・単独実行・PYTHONHASHSEED=0）
# 2×2 A/B（off / b230 / b231 / both）× perm 3条件 × 両ベンチ。
# ★他プロセスは kill しない（規約 §14）＝待機で譲る。
set -u
cd "$(dirname "$0")/.."
export PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8
LOG=docs/仮_b235_log
mkdir -p "$LOG"

wait_free() {  # 他レーンのベンチ／pytest の終了を待つ（kill しない）
    while pgrep -f "^python -m arena\." >/dev/null || pgrep -f "^python -m pytest" >/dev/null; do
        echo "  ... 他プロセス稼働中。30秒待機 $(date +%H:%M:%S)"
        sleep 30
    done
}

run() {  # run <days> <perm> <conds>
    local days="$1" perm="$2" conds="$3"
    local name="ab_d${days}_${perm}"
    wait_free
    echo "=== $name [$conds] : $(date +%H:%M:%S) ==="
    python -m arena.b235_ab --days "$days" --perm "$perm" --conds "$conds" \
        --out "$LOG/$name.json" > "$LOG/$name.log" 2>&1
    echo "RC=$? $(grep -c . "$LOG/$name.log") lines"
    grep -E "防衛=|flips" "$LOG/$name.log"
}

run 3 id   off,b230,b231,both,off@2
run 3 rev  off,b230,b231,both
run 3 h1   off,b230,b231,both
run 5 id   off,b230,b231,both,off@2
run 5 rev  off,b230,b231,both
run 5 h1   off,b230,b231,both
echo "ALL DONE $(date +%H:%M:%S)"
