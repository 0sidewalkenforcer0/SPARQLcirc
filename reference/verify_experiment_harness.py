"""Offline regressions for experiment-harness interfaces and R8.3 parity guards.

These checks need no GraphDB or ProvSQL endpoint. They catch two failures that the semantic verifier suite
does not exercise: callers unpacking an outdated compile_wmc() return shape, and an incomplete independent-K
map being accepted as successful keyed parity.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import g3_pqe_latency as g3
import g4_instances
import g4_rigor
import r8_3_reconvergent as r83
import compile_portfolio
import experiment_timeouts as limits


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


def check_g4_probability_consumption():
    good = "R|8|1.0\nR|8|1.0\n"
    try:
        rows = g4_rigor.parse_provsql_checks(good, 2)
        accepted = rows == [(8, 1.0), (8, 1.0)]
    except Exception:
        accepted = False
    rejected = False
    try:
        g4_rigor.parse_provsql_checks("R|8|0.0\n", 1)
    except RuntimeError:
        rejected = True
    ok = accepted and rejected
    print(f"[G4 probability ] consumed checksum accepted, pruned/wrong sum rejected {'OK' if ok else 'FAIL'}")
    return ok


def check_canonical_timeouts():
    compile_default = compile_portfolio.d4_compile_once.__defaults__[-1]
    hard_deadline = False
    try:
        with limits.compilation_timeout(0.02):
            time.sleep(0.1)
    except limits.CompilationTimeout:
        hard_deadline = True
    ok = (limits.QUERY_TIMEOUT_S == 300 and limits.COMPILE_TIMEOUT_S == 120 and
          compile_default == limits.COMPILE_TIMEOUT_S and
          g3.compile_wmc.__defaults__[-1] == limits.COMPILE_TIMEOUT_S and
          g4_rigor.TIMEOUT == limits.QUERY_TIMEOUT_S and hard_deadline)
    print(f"[timeout policy ] query=300s + compile=120s wired to harnesses {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    all_ok = (check_compile_api() and check_parity_guards() and check_tagged_parser() and
              check_g4_probability_consumption() and check_canonical_timeouts())
    print("\nALL OK" if all_ok else "\nFAILURES")
    sys.exit(0 if all_ok else 1)
