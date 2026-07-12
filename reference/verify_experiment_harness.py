"""Offline regressions for experiment-harness interfaces and R8.3 parity guards.

These checks need no GraphDB or ProvSQL endpoint. They catch two failures that the semantic verifier suite
does not exercise: callers unpacking an outdated compile_wmc() return shape, and an incomplete independent-K
map being accepted as successful keyed parity.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import g3_pqe_latency as g3
import g4_instances
import g4_rigor
import r8_3_reconvergent as r83


CIRC = {
    "answer": ("plus", ("token-node",)),
    "token-node": ("leaf", "token"),
}
ANS = {"answer": "A|cust=i\x1fhttp://example.org/Customer/1"}


def check_compile_api():
    result = g3.compile_wmc(CIRC, ANS)
    direct = len(result) == 5 and result[4] == {ANS["answer"]: 0.5}

    old_construct = g3.construct_bgp
    old_rigor = (g4_rigor.WARMUP, g4_rigor.RUNS)
    old_instances = (g4_instances.WARMUP, g4_instances.RUNS)
    g3.construct_bgp = lambda *args, **kwargs: (CIRC, ANS, 1.0)
    g4_rigor.WARMUP, g4_rigor.RUNS = 0, 1
    g4_instances.WARMUP, g4_instances.RUNS = 0, 1
    try:
        qfile = os.path.join(HERE, "tpch", "skeletons", "Qrecon.rq")
        rigor = g4_rigor.ours_runs("offline-smoke", "unused", "naryrel", qfile, False)
        instances = g4_instances.ours_end_to_end("unused", "naryrel", "SELECT * WHERE {}")
        callers = rigor is not None and instances[0] == 1
    finally:
        g3.construct_bgp = old_construct
        g4_rigor.WARMUP, g4_rigor.RUNS = old_rigor
        g4_instances.WARMUP, g4_instances.RUNS = old_instances

    ok = direct and callers
    print(f"[compile_wmc API] five-value map + G4 callers {'OK' if ok else 'FAIL'}")
    return ok


def check_parity_guards():
    ours = {"per": {"1": 0.375, "2": 0.4375}, "valid": True}
    complete = {"per": {"1": 0.375, "2": 0.4375}, "korder": {"1": 2, "2": 3}}
    missing_k = {"per": dict(complete["per"]), "korder": {"1": 2}}
    wrong_key = {"per": {"1": 0.375, "3": 0.4375}, "korder": {"1": 2, "3": 3}}

    good = r83.parity(ours, complete)
    incomplete = r83.parity(ours, missing_k)
    mismatched = r83.parity(ours, wrong_key)
    ok = good["agree"] and not incomplete["agree"] and incomplete["missing_k"] == 1 and not mismatched["agree"]
    print(f"[R8.3 parity   ] complete accepted, missing-K/key mismatch rejected {'OK' if ok else 'FAIL'}")
    return ok


def check_tagged_parser():
    p, k = r83._parse_provsql_rows("P|1|1\nP|2|0.4375\nK|1|2\nK|2|3\n")
    ok = p == {"1": 1.0, "2": 0.4375} and k == {"1": 2, "2": 3}
    print(f"[ProvSQL parser] explicit P/K tags, including integral probability {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    all_ok = check_compile_api() and check_parity_guards() and check_tagged_parser()
    print("\nALL OK" if all_ok else "\nFAILURES")
    sys.exit(0 if all_ok else 1)
