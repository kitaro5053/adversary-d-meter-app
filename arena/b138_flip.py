# -*- coding: utf-8 -*-
"""B-138：flip した1局の**初分岐**を突き止める（off と 各版 を同じ脚本で走らせて突き合わせ）。

    PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 python -m arena.b138_flip \
        --script random_BTX --seed 6 --days 5 --configs off,b
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace

from agents.heuristic import HeuristicMastermind
from agents.debug import ProbedProtagonist
from agents.heuristic_protagonist import HeuristicProtagonist as HP
from arena.b138_ab import CONFIGS, _KEYS, _apply
from sim import random_script, run_game
from sim.sample_scripts import SAMPLE_SCRIPTS


class _P(ProbedProtagonist):
    def decide(self, view, decision, options):
        chosen = super().decide(view, decision, options)
        r = self.records[-1]
        r["danger"] = self._guess_defeat_board(view)
        r["odb"] = getattr(self, "_observed_defeat_board", None)
        r["top"] = sorted(getattr(self, "_b84_top_boards", ()) or ())
        r["probs"] = {k: round(v, 4)
                      for k, v in (getattr(self, "_board_defeat_probs", None) or {}).items()}
        return chosen


def _run(name: str, seed: int, days: int, cfg: str, loops: int, perm: str = "id"):
    from arena.tie_noise import install_perm, uninstall_perm
    _apply(cfg)
    print(f"[flip] cfg={cfg} perm={perm} 切替口="
          + json.dumps({k: getattr(HP, k) for k in _KEYS}), flush=True)
    sc = (random_script(name.replace("random_", ""), seed, days=days)
          if name.startswith("random_") else SAMPLE_SCRIPTS[name]())
    mm = HeuristicMastermind(seed)
    hp = _P(seed, top=6)
    install_perm(perm)
    try:
        state, _ = run_game(replace(sc, loops=loops),
                            {"mastermind": mm, "p1": hp, "p2": hp, "p3": hp})
    finally:
        uninstall_perm()
        _apply("off")
    return state, hp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="random_BTX")
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--loops", type=int, default=8)
    ap.add_argument("--configs", default="off,b")
    ap.add_argument("--perm", default="id")
    a = ap.parse_args(argv)
    print(f"[flip] PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', '(未固定!)')}")
    cfgs = a.configs.split(",")
    base_state, base_hp = _run(a.script, a.seed, a.days, cfgs[0], a.loops, a.perm)
    print(f"  {cfgs[0]}: winner={base_state.winner} loop={base_state.loop_no}")
    for cfg in cfgs[1:]:
        st, hp = _run(a.script, a.seed, a.days, cfg, a.loops, a.perm)
        print(f"  {cfg}: winner={st.winner} loop={st.loop_no}")
        for i, (x, y) in enumerate(zip(base_hp.records, hp.records)):
            if x["chosen"] != y["chosen"]:
                print(f"  ★初分岐 #{i} L{x['loop']}D{x['day']} {x['seat']} {x['decision']}")
                for tag, r in ((cfgs[0], x), (cfg, y)):
                    print(f"     [{tag}] 選択={json.dumps({k: v for k, v in r['chosen'].items() if k != 'prov'}, ensure_ascii=False)}"
                          f" danger={r['danger']} odb={r['odb']} top={r['top']} probs={r['probs']}")
                    for s, o in r["scored"][:6]:
                        print(f"        {s:>9.4f} "
                              f"{json.dumps({k: v for k, v in o.items() if k != 'prov'}, ensure_ascii=False)}")
                break
        else:
            print("  （記録上の分岐なし）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
