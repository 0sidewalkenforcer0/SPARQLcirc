"""ProvSQL-inspired exact-probability portfolio for a provenance circuit.

Motivation: ProvSQL, the strongest baseline, computes exact probability
with a cost-ranked exact portfolio. This module provides a portable subset for our own evaluation, but it
is NOT claimed to be the same portfolio: ProvSQL also has tree-decomposition and different selection logic.
The controlled head-to-head therefore uses the separate Level-1 harness (`level1_d4_headtohead.py`), which
forces one pinned compiler and does not call :func:`probability` below.

This module mirrors that chain on OUR provenance circuit (`circuit_io.parse` format):
  1. read-once      -> linear bottom-up eval; each token appears once => siblings are over disjoint
                       variables => independent, so ⊗=∏p, ⊕=1−∏(1−p), ⊖=p_m·(1−p_s) are EXACT.   O(size)
  2. possible-worlds-> brute-force 2^n enumeration for <= SMALL tokens (compile_bdd.wmc_enum).
  3. compilation    -> Tseitin CNF (export_cnf; semantically equivalent to ProvSQL's CNF, not claimed
                       byte-identical)
                       -> external d-DNNF compiler **d4** (D4 env; use d4-v2) -> weighted model count.
  fallback          -> our OBDD (compile_bdd.probability) when d4 is unavailable (arm64 / no D4 set).

NOTE: tree-decomposition — the step ProvSQL puts between (2) and (3) for bounded-treewidth circuits — is
a documented TODO here (needs a TD library); its absence only means we reach `compilation` slightly
earlier, never a wrong answer. OBDD + possible-world enumeration remain the INDEPENDENT correctness
oracle (E1/G6); this module does not replace them.
"""
import os, subprocess, tempfile, time, shutil
import compile_bdd, export_cnf, ddnnf_wmc
from experiment_timeouts import COMPILE_TIMEOUT_S, compilation_timeout

SMALL = int(os.environ.get("PORTFOLIO_PWE_MAX", "20"))       # possible-worlds only below this #tokens (2^20 = 1M)


def _children(circ, n):
    op, pl = circ[n]
    if op in ("times", "plus"): return list(pl)
    if op == "minus": return [pl[0], pl[1]]
    return []


def _reach(circ, root):
    stk, seen = [root], set()
    while stk:
        n = stk.pop()
        if n in seen: continue
        seen.add(n)
        stk.extend(_children(circ, n))
    return seen


def is_read_once(circ, root):
    """True iff the cone at `root` is a tree with distinct leaves (every reachable node is referenced as a
    child at most once). Read-once => every token appears once => the linear independence eval is EXACT."""
    ref = {}
    for n in _reach(circ, root):
        for c in _children(circ, n):
            ref[c] = ref.get(c, 0) + 1
    return all(v <= 1 for v in ref.values())


def _leaves(circ, root):
    return {circ[n][1] for n in _reach(circ, root) if circ[n][0] == "leaf"}


def prob_read_once(circ, root, P):
    """Exact probability of a READ-ONCE circuit by one bottom-up pass (independence holds)."""
    def ev(n):
        op, pl = circ[n]
        if op == "leaf":  return P[pl]
        if op == "times":
            r = 1.0
            for c in pl: r *= ev(c)
            return r
        if op == "plus":
            r = 1.0
            for c in pl: r *= (1.0 - ev(c))
            return 1.0 - r
        if op == "minus": return ev(pl[0]) * (1.0 - ev(pl[1]))
        raise ValueError("unexpected gate op: " + op)
    return ev(root)


def d4_compile_once(circ, root, P, d4bin=None, timeout=COMPILE_TIMEOUT_S):
    """Force one ``Tseitin CNF -> d4 -> d-DNNF`` compilation, then WMC that dump locally.

    Returns probability, d-DNNF size and separate compile/WMC times.  It is intentionally strict: a forced
    Level-1 run must abort on a missing compiler, malformed dump or timeout rather than fall back silently.
    The temporary directory is always cleaned, including across 10^4 per-answer invocations.
    """
    if d4bin is None:
        d4bin = os.environ.get("D4")
    if not d4bin:
        raise RuntimeError("d4 not available (set D4 to the pinned d4-v2 binary)")
    if not (os.path.isfile(d4bin) or shutil.which(d4bin)):
        raise RuntimeError(f"d4 binary not found: {d4bin}")
    import d4_pipeline as d4p                                  # lazy: only when d4 is actually used
    with tempfile.TemporaryDirectory(prefix="portf_") as d:
        t = time.perf_counter()
        e = export_cnf.export(circ, root, P)
        input_weights = {}
        for node, var in e["var_of"].items():
            op, payload = circ[node]
            if op == "leaf":
                input_weights[var] = (P[payload], 1.0 - P[payload])
        cnf = os.path.join(d, "c.cnf")
        with open(cnf, "w") as fh:
            fh.write(e["dimacs"])
        encode_ms = (time.perf_counter() - t) * 1000
        nnf = cnf + ".nnf"
        cmd = d4p.ddnnf_cmd(cnf, nnf, d4bin=d4bin)
        t = time.perf_counter()
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.CalledProcessError as ex:
            raise RuntimeError(f"d4 compilation failed (rc={ex.returncode}): {(ex.stderr or '')[-1000:]}") from ex
        except subprocess.TimeoutExpired as ex:
            raise RuntimeError(f"d4 compilation timed out after {timeout}s: {' '.join(cmd)}") from ex
        compile_ms = (time.perf_counter() - t) * 1000
        if not os.path.exists(nnf) or os.path.getsize(nnf) == 0:
            raise RuntimeError(f"d4 produced no d-DNNF: {' '.join(cmd)}\n{(proc.stderr or '')[-1000:]}")
        t = time.perf_counter()
        ev = ddnnf_wmc.evaluate_file(nnf, input_weights)
        wmc_ms = (time.perf_counter() - t) * 1000
    if not (-1e-9 <= ev.probability <= 1.0 + 1e-9):
        raise RuntimeError(f"d-DNNF WMC outside [0,1]: {ev.probability}")
    return dict(probability=ev.probability, ddnnf_nodes=ev.nodes, ddnnf_edges=ev.edges,
                encode_ms=encode_ms, compile_ms=compile_ms, wmc_ms=wmc_ms, nnf_format=ev.format,
                cnf_vars=e["nvars"], cnf_clauses=e["nclauses"])


def d4_wmc(circ, root, P, d4bin=None, strict=False, size=True):
    """Compatibility adapter returning ``(probability, nodes)`` or ``None`` for the auto portfolio.

    ``size`` is retained for older callers but no longer triggers a second compiler invocation. Forced
    experiments should call :func:`d4_compile_once` so the timing fields are available and failures are fatal.
    """
    try:
        r = d4_compile_once(circ, root, P, d4bin=d4bin)
        return r["probability"], (r["ddnnf_nodes"] if size else None)
    except Exception:
        if strict:
            raise
        return None


def probability(circ, root, P):
    """Exact probability + the method used, choosing the cheapest applicable exact method (ProvSQL-style).
    Returns (prob, method) where method in {read-once, possible-worlds, compilation-d4, obdd-fallback}."""
    if is_read_once(circ, root):
        return prob_read_once(circ, root, P), "read-once"
    if len(_leaves(circ, root)) <= SMALL:
        return compile_bdd.wmc_enum(circ, root, P), "possible-worlds"
    r = d4_wmc(circ, root, P)                                  # Tseitin CNF -> d4 (if D4 set)
    if r is not None:
        return r[0], "compilation-d4"
    with compilation_timeout(COMPILE_TIMEOUT_S):
        p = compile_bdd.probability(circ, root, P)[0]                  # portable fallback (no d4)
    return p, "obdd-fallback"
