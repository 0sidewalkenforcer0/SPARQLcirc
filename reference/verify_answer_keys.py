"""Regression for the answer/gate-IDENTITY key: it must be term-type-aware, so two DISTINCT SPARQL
solutions never collapse into one answer gate (the old raw-STR `A|x=STR(?x)` collision). Builds minimal
reified data whose two solutions used to merge under the old key and asserts (a) TWO distinct `urn:g:a:`
gates and (b) `c:binding`/`c:val` recovers the two distinct RDF terms (type / datatype / lang preserved).

Covers: control (distinct IRIs), IRI-vs-literal same lexical, datatype, language tag, delimiter injection,
OPTIONAL unbound vs literal "NULL". Run: `python3 verify_answer_keys.py` (needs the engine JAR).
"""
import os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
P = "http://example.org/paper#"
HDR = f"@prefix rdf: <{RDF}> .\n@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"

def st(tok, s, p, o):   # one Standard-reified statement
    return f"<{P}{tok}> rdf:subject {s} ; rdf:predicate <{P}{p}> ; rdf:object {o} .\n"

def run(data, query):
    d = tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False); d.write(HDR + data); d.close()
    q = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); q.write(query); q.close()
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", d.name, q.name],
                       capture_output=True, text=True)
    if any(m in r.stderr for m in ("Unresolved compilation", "Malformed", "Exception")):
        return None, None
    gates = sorted({ln.split(None, 1)[0] for ln in r.stdout.splitlines() if "urn:circuit:answer" in ln})
    vals = sorted({m.group(1) for ln in r.stdout.splitlines()
                   for m in [re.search(r"urn:circuit:val> (.+) \.\s*$", ln)] if m})
    return gates, vals

IRI = lambda x: f"<{P}{x}>"
Q = lambda body: f"SELECT {body}"
CASES = [
    ("control IRI vs IRI", st("s1", IRI("A"), "knows", IRI("foo")) + st("s2", IRI("B"), "knows", IRI("bar")),
     Q(f"?x WHERE {{ ?a <{P}knows> ?x }}"), 2),
    ("IRI vs literal (same lex)", st("s1", IRI("A"), "knows", IRI("foo")) + st("s2", IRI("B"), "knows", f'"{P}foo"'),
     Q(f"?x WHERE {{ ?a <{P}knows> ?x }}"), 2),
    ("datatype int vs string", st("s1", IRI("A"), "v", '"1"^^xsd:integer') + st("s2", IRI("B"), "v", '"1"^^xsd:string'),
     Q(f"?x WHERE {{ ?a <{P}v> ?x }}"), 2),
    ("language en vs fr", st("s1", IRI("A"), "v", '"chat"@en') + st("s2", IRI("B"), "v", '"chat"@fr'),
     Q(f"?x WHERE {{ ?a <{P}v> ?x }}"), 2),
    ("delimiter injection", st("r1", IRI("r1"), "p", '"a"') + st("r1b", IRI("r1"), "q", '"b|y=c"')
     + st("r2", IRI("r2"), "p", '"a|y=b"') + st("r2b", IRI("r2"), "q", '"c"'),
     Q(f"?x ?y WHERE {{ ?s <{P}p> ?x . ?s <{P}q> ?y }}"), 2),
    ("OPTIONAL unbound vs \"NULL\"", st("k1", IRI("Alice"), "knows", IRI("Bob")) + st("k2", IRI("Alice"), "knows", IRI("Carol"))
     + st("c1", IRI("Carol"), "city", '"NULL"'),
     Q(f"?c WHERE {{ <{P}Alice> <{P}knows> ?y OPTIONAL {{ ?y <{P}city> ?c }} }}"), 2),
]

def main():
    ok = True
    print("answer-key collision regression (distinct solutions must NOT merge):\n")
    for name, data, q, expect in CASES:
        gates, vals = run(data, q)
        if gates is None:
            print(f"  [{name:28}] ENGINE ERROR"); ok = False; continue
        good = len(gates) == expect
        ok &= good
        print(f"  [{name:28}] gates={len(gates)} (expect {expect}) {'OK' if good else 'FAIL'}  recovered={vals}")
    print("\nALL OK" if ok else "\nFAILURES")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
