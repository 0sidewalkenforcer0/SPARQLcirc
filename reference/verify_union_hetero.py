"""Heterogeneous-UNION answer-recovery regression.

A projected variable that is UNBOUND in a UNION arm (or projected but never bound anywhere) must NOT make
its answer disappear from post-processing. Before the fix, bgp() built the readable c:answer label with a
raw STR(?v) over every projected var assumed bound; in a heterogeneous UNION that made ?anskey unbound, so
the whole `c:answer` triple was dropped — and circuit_io.parse() identified answers ONLY by c:answer, so
the (otherwise valid, c:binding-carrying) answer gate was silently lost. Fix: (a) ansKey guards each var
with IF(BOUND(...)); (b) circuit_io treats any gate with c:binding as an answer gate.

Cases (the ones the prior UNION test — same var both arms — did NOT cover):
  1. UNION arms bind DIFFERENT vars: `{?x :p :o} UNION {?y :q :o}` -> 2 answers, each with one var unbound.
  2. A projected var appears in NO pattern: `SELECT ?x ?z WHERE { ?x :p :o }` -> 1 answer, ?z unbound.
"""
import subprocess, os, sys, tempfile
import circuit_io, compile_bdd

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
DATA = """@prefix : <http://ex/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
:t1 rdf:subject :s1 ; rdf:predicate :p ; rdf:object :o .
:t2 rdf:subject :s2 ; rdf:predicate :q ; rdf:object :o .
"""
I = circuit_io.canon_iri
CASES = [  # (label, query, expected {frozenset-of-(var,canon): approx-prob})
    ("union-different-vars",
     "PREFIX : <http://ex/>\nSELECT ?x ?y WHERE { { ?x :p :o } UNION { ?y :q :o } }\n",
     {frozenset({("x", I("http://ex/s1")), ("y", "u")}): 0.5,
      frozenset({("x", "u"), ("y", I("http://ex/s2"))}): 0.5}),
    ("projected-var-never-bound",
     "PREFIX : <http://ex/>\nSELECT ?x ?z WHERE { ?x :p :o }\n",
     {frozenset({("x", I("http://ex/s1")), ("z", "u")}): 0.5}),
]

def run(query):
    d = tempfile.mkdtemp(prefix="unionhet_")
    dp = os.path.join(d, "d.ttl"); open(dp, "w").write(DATA)
    qp = os.path.join(d, "q.rq");  open(qp, "w").write(query)
    return subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", dp, qp],
                          capture_output=True, text=True, check=True).stdout

def main():
    if not os.path.exists(JAR):
        print("jar not built:", JAR); sys.exit(2)
    allok = True
    for label, q, expect in CASES:
        nt = run(q)
        circ, ans, binds = circuit_io.parse(nt)
        P = {circ[x][1]: 0.5 for x in circ if circ[x][0] == "leaf"}
        got = {}
        for g in ans:
            key = frozenset(binds[g].items())
            got[key] = round(compile_bdd.probability(circ, g, P)[0], 6)
        # every answer gate must ALSO carry a c:answer label now (regression on the emitter fix)
        n_label = sum(1 for line in nt.splitlines() if "urn:circuit:answer" in line)
        count_ok = len(got) == len(expect)
        keys_ok = set(got) == set(expect)
        prob_ok = all(abs(got.get(k, -1) - v) < 1e-9 for k, v in expect.items())
        label_ok = n_label == len(expect)
        ok = count_ok and keys_ok and prob_ok and label_ok
        allok &= ok
        print(f"[{label:26}] answers={len(got)}/{len(expect)} keys={'ok' if keys_ok else 'BAD'} "
              f"probs={'ok' if prob_ok else 'BAD'} c:answer-labels={n_label} {'OK' if ok else 'FAIL'}")
        if not ok:
            for k in sorted(set(got) | set(expect), key=str):
                print(f"    {dict(k)}  got={got.get(k)}  want={expect.get(k)}")
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
