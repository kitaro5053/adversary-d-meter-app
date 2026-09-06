#!/bin/bash
# B-236 組み合わせバッチ（測定専用・単独実行・PYTHONHASHSEED=0）
# 2^4 全数（16条件）＋ off@2（状態漏れ検査）× perm 3条件 × 両ベンチ。
# ★他プロセスは kill しない（規約 §14）＝待機で譲る。
set -u
cd "$(dirname "$0")/.."
export PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8
LOG=docs/仮_b236_log
mkdir -p "$LOG"

wait_free() {  # 他レーンのベンチ／pytest の終了を待つ（kill しない）
    while pgrep -f "^python -m arena\." >/dev/null || pgrep -f "^python -m pytest" >/dev/null; do
        echo "  ... 他プロセス稼働中。30秒待機 $(date +%H:%M:%S)"
        sleep 30
    done
}

run() {  # run <days> <perm>
    local days="$1" perm="$2"
    local name="ab_d${days}_${perm}"
    if [ -s "$LOG/$name.json" ]; then echo "SKIP $name (既存)"; return; fi
    wait_free
    echo "=== $name : $(date +%H:%M:%S) ==="
    python -m arena.b236_ab --days "$days" --perm "$perm" --conds ALL16 \
        --out "$LOG/$name.json" > "$LOG/$name.log" 2>&1
    echo "RC=$?"
    grep -E "防衛=" "$LOG/$name.log"
}

for spec in "$@"; do
    run "${spec%%:*}" "${spec##*:}"
done
echo "ALL DONE $(date +%H:%M:%S)"
