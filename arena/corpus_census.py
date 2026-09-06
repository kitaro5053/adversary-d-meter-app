# -*- coding: utf-8 -*-
"""既存ベンチのコーパス構成を数える計測器（§48 の設計要点4 用）。

**何を判定するために作ったか**（バックログ §48 の設計要点3）：
チケットが「この題材は薄い」と書くときの**分母**を、推定ではなく実測で置くため。
具体的には (1) 130局／70局のうち**構造の異なる脚本が何本あるか** (2) 題材（キャラ・
役職・事件）ごとの**局数と独立脚本数** (3) 3日級ベンチと5日級ベンチが**独立か**。

★対局はしない＝脚本の定義だけを読む。∴ `agents/` `engine/` の挙動には一切触れない。

    PYTHONIOENCODING=utf-8 PYTHONHASHSEED=0 python -m arena.corpus_census
"""
from __future__ import annotations

from collections import Counter, defaultdict

from arena.benchmark import benchmark_scripts


def script_signature(sc):
    """構造署名。これが同一なら「同じ脚本」とみなす（seed 複製の検出用）。"""
    return (
        sc.set_name,
        sc.days_per_loop,
        sc.loops,
        tuple(sorted(sc.cast)),
        tuple(sorted(sc.roles.items())),
        tuple(sorted((i.day, i.name, i.culprit) for i in sc.incidents)),
    )


def census(days: int):
    rows = benchmark_scripts(days=days)
    sigs = defaultdict(list)
    family = Counter()
    for name, seed, sc in rows:
        sigs[script_signature(sc)].append((name, seed))
        family[name] += 1

    def tally(key_fn):
        games, uniq = Counter(), defaultdict(set)
        for name, seed, sc in rows:
            s = script_signature(sc)
            for k in key_fn(sc):
                games[k] += 1
                uniq[k].add(s)
        return games, uniq

    return {
        "n": len(rows),
        "rows": rows,
        "sigs": sigs,
        "family": family,
        "char": tally(lambda sc: set(sc.cast)),
        "role": tally(lambda sc: set(sc.roles.values())),
        "inc": tally(lambda sc: [i.name for i in sc.incidents]),
    }


def _print_census(days: int):
    d = census(days)
    n = d["n"]
    print("=" * 72)
    print(f"■ {days}日級ベンチ：{n}局 ／ 構造の異なる脚本＝{len(d['sigs'])}本")
    print("-- 脚本ファミリ別（★同一署名がいくつに潰れるか） --")
    for name, cnt in sorted(d["family"].items()):
        uniq = len({script_signature(sc) for nm, _s, sc in d["rows"] if nm == name})
        print(f"   {name:<14} {cnt:>3}局 → 独立 {uniq}本")
    for label, key in (("キャラ", "char"), ("役職", "role"), ("事件", "inc")):
        games, uniq = d[key]
        print(f"-- {label}別の出現（局数 ／ 独立脚本数 ／ 出現率） --")
        for k, c in sorted(games.items(), key=lambda kv: -kv[1]):
            print(f"   {k:<16} {c:>4}局 ／ 独立 {len(uniq[k]):>3}本  ({100.0 * c / n:.1f}%)")
    return d


def cross_bench_independence():
    """3日級と5日級の `random_*` が同じ seed で同じ配役を使っていないかを見る。

    ★手書き脚本は 3日級と5日級で別物（`btx_seal` と `btx5_seal` 等）なので対象外。
    """
    r3 = {(n, s): sc for n, s, sc in benchmark_scripts(days=3) if n.startswith("random_")}
    r5 = {(n, s): sc for n, s, sc in benchmark_scripts(days=5) if n.startswith("random_")}
    keys = sorted(set(r3) & set(r5))
    same_cast = same_roles = same_inc_body = same_all = 0
    for k in keys:
        a, b = r3[k], r5[k]
        c = sorted(a.cast) == sorted(b.cast)
        r = a.roles == b.roles
        i = (sorted((x.name, x.culprit) for x in a.incidents)
             == sorted((x.name, x.culprit) for x in b.incidents))
        same_cast += c
        same_roles += r
        same_inc_body += i
        same_all += (c and r and i)
    print("=" * 72)
    print(f"■ 3日級ベンチと5日級ベンチの独立性（`random_*` の共通 seed {len(keys)} 組）")
    print(f"   キャストが同一           : {same_cast}/{len(keys)}")
    print(f"   役職配置が同一           : {same_roles}/{len(keys)}")
    print(f"   事件の(名前,犯人)が同一  : {same_inc_body}/{len(keys)}")
    print(f"   ★配役も事件の中身も同一で『日付だけ違う』: {same_all}/{len(keys)}")


def scope(label, pred, show_max: int = 12):
    """薄い題材の射程を両ベンチで数える（§48 設計要点4＝出現率の併記）。"""
    for days in (3, 5):
        rows = benchmark_scripts(days=days)
        hit = [(n, s) for n, s, sc in rows if pred(sc)]
        uniq = len({script_signature(sc) for n, s, sc in rows if pred(sc)})
        n = len(rows)
        print(f"   [{days}日級] {label:<24} {len(hit):>3}/{n}局 "
              f"({100.0 * len(hit) / n:.1f}%) ／ 独立 {uniq}本")
        if 0 < len(hit) <= show_max:
            print(f"        → {', '.join(f'{a} s{b}' for a, b in hit)}")


def main():
    _print_census(3)
    _print_census(5)
    cross_bench_independence()
    print("=" * 72)
    print("■ §48 が挙げた薄い題材の射程")
    scope("従者（キャラ）", lambda sc: "従者" in sc.cast)
    scope("黒猫（キャラ）", lambda sc: "黒猫" in sc.cast)
    scope("巫女（キャラ）", lambda sc: "巫女" in sc.cast)
    scope("神格（キャラ）", lambda sc: "神格" in sc.cast)
    scope("幻想（キャラ）", lambda sc: "幻想" in sc.cast)
    scope("タイムトラベラー（役職）", lambda sc: "タイムトラベラー" in sc.roles.values())
    scope("自殺（事件）", lambda sc: any(i.name == "自殺" for i in sc.incidents))
    scope("流布（事件）", lambda sc: any(i.name == "流布" for i in sc.incidents))
    scope("流布×TT（交差）",
          lambda sc: any(i.name == "流布" for i in sc.incidents)
          and "タイムトラベラー" in sc.roles.values())


if __name__ == "__main__":
    main()
