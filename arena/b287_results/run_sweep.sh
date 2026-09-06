#!/bin/bash
# B-287 掃引（再開可能＝既存 json はスキップ・順次単独実行）
cd /home/user/adversary-d-meter
export PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8
# 第2陣（2026-08-29）＝3形 a/b/ab を追加（off/on は第1陣の結果が有れば skip）
for days in 3 5; do
  for perm in id rev h1 rot1; do
    for mode in off on a b ab; do
      out="arena/b287_results/d${days}_${perm}_${mode}"
      if [ -s "${out}.json" ]; then echo "skip ${out}"; continue; fi
      echo "=== ${out} $(date +%H:%M:%S) ==="
      python -m arena.b287_probe --days ${days} --perm ${perm} --mode ${mode} \
        --quiet --json "${out}.json" > "${out}.txt" 2>&1 || { echo "FAIL ${out}"; exit 1; }
      tail -n +1 "${out}.txt" | head -40
    done
  done
done
echo "ALL DONE $(date +%H:%M:%S)"
