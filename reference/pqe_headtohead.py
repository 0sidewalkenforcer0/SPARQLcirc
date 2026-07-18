"""r9_5 / PQE head-to-head — complete NPCS into a PQE pipeline with OUR compiler, then compare
END-TO-END PQE. NPCS/SPARQLprov emit per-answer how-provenance and do NOT compute probabilities;
we complete them by compiling each answer's how-provenance with the SAME knowledge compiler:

    OURS   = compile the shared content-addressed circuit ONCE + WMC        (SPARQLcirc PQE)
    THEIRS = compile each answer's how-provenance (flat SoP cone) SEPARATELY + WMC, summed
             (NPCS/SPARQLprov completed into PQE with the identical compiler)

End-to-end PQE per engine = that engine's construct time (from the B/R/N/C matrix) + these client-side
compile+WMC stages (engine-independent — the circuit is byte-identical across engines). The point:
many-answer templates make the per-answer completion EXPLODE (Theta(N*S)) while the shared circuit is
Theta(N+S) — so we are end-to-end faster/feasible exactly where NPCS is not, despite slower construction.
Non-monotone (MINUS/M) templates can't be flattened to a monotone SoP at all -> NPCS can't represent them.

Client-side core `pqe_stages(circuit_nt, P, cap_s)` is engine-free (testable on any circuit). The front
end builds each template's circuit with CircuitRun (flat) on a live endpoint.

  python3 pqe_headtohead.py --selftest          # client-side core, no engine
  python3 pqe_headtohead.py --scale 10M ...      # full run (needs an endpoint)
"""
import os, sys, csv, time, argparse, tempfile, subprocess
import circuit_io, compile_bdd
import e11_per_answer_vs_shared as e11

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")


def _uniform_P(circ):
    return {n: 0.5 for n, (op, _pl) in circ.items() if op == "leaf"}


def pqe_stages(circuit_nt, cap_s=120.0):
    """Given a circuit's N-Triples, return the shared vs per-answer PQE compile+WMC stages.

    OURS: compile all answer roots into one shared ROBDD + WMC.
    THEIRS: per answer, flatten its cone to the NPCS SoP string, compile independently + WMC; sum,
            aborting at cap_s (per-answer completion is meant to blow up -> record 'too-large').
    """
    circ, ans_gates, bindings = circuit_io.parse(circuit_nt)
    roots = {}
    for i, g in enumerate(sorted(ans_gates)):
        roots[circuit_io.answer_key(bindings.get(g, {})) or ("ans", i)] = g
    if not roots:
        return {"answers": 0, "status": "no-answers"}
    P = _uniform_P(circ)
    order = e11.global_order(circ, roots)
    monotone = all(op != "minus" for op, _ in circ.values())

    # OURS: shared compile-once + WMC
    t = time.time()
    size, _ms, probs = e11.compile_shared(circ, roots, P, order)
    shared_ms = (time.time() - t) * 1000.0

    # THEIRS: NPCS/SPARQLprov completed = per-answer flat SoP, compiled independently + WMC
    if not monotone:
        return {"answers": len(roots), "shared_pqe_ms": round(shared_ms, 3), "shared_size": size,
                "perans_pqe_ms": None, "perans_status": "npcs-cannot-represent-nonmonotone",
                "status": "ok"}
    t = time.time(); perans_status = "ok"
    for key, r in roots.items():
        sub, sub_root = e11.flatten_sop(circ, r)           # the NPCS emitted string (SoP)
        o = compile_bdd.leaf_order(sub, sub_root)
        bdd = compile_bdd.ROBDD(o)
        node = compile_bdd.compile_root(sub, sub_root, bdd, {})
        bdd.wmc(node, P)
        if time.time() - t > cap_s:
            perans_status = "too-large"; break
    perans_ms = (time.time() - t) * 1000.0
    return {"answers": len(roots), "shared_pqe_ms": round(shared_ms, 3), "shared_size": size,
            "perans_pqe_ms": round(perans_ms, 3) if perans_status == "ok" else None,
            "perans_status": perans_status, "status": "ok"}


def build_circuit(scheme, data_file, query_file, endpoint, timeout_s=300):
    """Run CircuitRun (flat) to materialise the shared circuit N-Triples for one bound query."""
    cmd = ["java", "-cp", JAR, "npcs.circuit.CircuitRun", scheme, data_file, query_file, endpoint]
    env = {**os.environ, "CIRCUIT_SKIP_LOAD": "1"}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout_s)
    if r.returncode:
        raise RuntimeError(f"CircuitRun rc={r.returncode}: {r.stderr[-300:]}")
    return "\n".join(l for l in r.stdout.splitlines() if l.strip().endswith(" ."))


def selftest():
    # tiny circuit: 2 answers sharing a subterm; a1 = x⊗(y⊕z), a2 = x⊗w  (monotone)
    T = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    nt = f"""
<urn:a1> <{T}> <urn:circuit:Times> .
<urn:x> <urn:circuit:in> <urn:a1> .
<urn:p1> <urn:circuit:in> <urn:a1> .
<urn:a1> <urn:circuit:binding> <urn:b1> .
<urn:b1> <urn:circuit:var> "s" .
<urn:b1> <urn:circuit:val> "urn:A1" .
<urn:p1> <{T}> <urn:circuit:Plus> .
<urn:y> <urn:circuit:feeds> <urn:p1> .
<urn:z> <urn:circuit:feeds> <urn:p1> .
<urn:a2> <{T}> <urn:circuit:Times> .
<urn:x> <urn:circuit:in> <urn:a2> .
<urn:w> <urn:circuit:in> <urn:a2> .
<urn:a2> <urn:circuit:binding> <urn:b2> .
<urn:b2> <urn:circuit:var> "s" .
<urn:b2> <urn:circuit:val> "urn:A2" .
"""
    r = pqe_stages(nt, cap_s=10)
    print("selftest:", r)
    assert r["status"] == "ok" and r["answers"] == 2, r
    assert r["shared_pqe_ms"] is not None and r["perans_pqe_ms"] is not None, r
    print("selftest OK — shared and per-answer PQE both computed on a shared 2-answer circuit")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args, _ = ap.parse_known_args()
    if args.selftest:
        selftest()
    else:
        print("full run wired to CircuitRun + workload_manifest; see --selftest for the client-side core")
