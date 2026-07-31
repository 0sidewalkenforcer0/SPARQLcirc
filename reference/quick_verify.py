#!/usr/bin/env python3
"""One-command, zero-Python-dependency smoke test for the complete local pipeline.

The integration check invokes ``CircuitRun`` itself and parses that invocation's
stdout.  It intentionally never reads ``reference/data/*.circuit.nt``, so a stale
checked-in fixture cannot make a broken Java rewriter look healthy.

Run from any directory after building ``engine/target/npcs-rewrite.jar``::

    python3 reference/quick_verify.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import circuit_io
import compile_bdd
import wmc


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference"
JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
DATA = ROOT / "engine" / "examples" / "circuit" / "drug.reified.ttl"
QUERY_FILE = ROOT / "engine" / "examples" / "circuit" / "drug3hop.sparql"
PROBABILITIES = REFERENCE / "data" / "drug.probabilities.json"

EDGES = {
    "urn:d:p1": ("urn:d:Aspirin", "urn:d:iw", "urn:d:Warfarin"),
    "urn:d:p2": ("urn:d:Warfarin", "urn:d:iw", "urn:d:Metformin"),
    "urn:d:p3": ("urn:d:Metformin", "urn:d:iw", "urn:d:Omeprazole"),
    "urn:d:p4": ("urn:d:Aspirin", "urn:d:iw", "urn:d:Ibuprofen"),
    "urn:d:p5": ("urn:d:Ibuprofen", "urn:d:iw", "urn:d:Metformin"),
    "urn:d:p6": ("urn:d:Warfarin", "urn:d:iw", "urn:d:Lisinopril"),
    "urn:d:p7": ("urn:d:Lisinopril", "urn:d:iw", "urn:d:Clopidogrel"),
    "urn:d:p8": ("urn:d:Clopidogrel", "urn:d:iw", "urn:d:Aspirin"),
}
WEIGHTS = dict(zip(EDGES, [.92, .87, .85, .78, .71, .65, .60, .55]))
SELECT = ["?z"]
QUERY = (
    "bgp",
    [
        ("urn:d:Aspirin", "urn:d:iw", "?x"),
        ("?x", "urn:d:iw", "?y"),
        ("?y", "urn:d:iw", "?z"),
    ],
)


def _run_python_checks() -> None:
    for script in ("tests.py", "wmc.py", "verify_boolean_boundary.py",
                   "verify_circuit_io.py", "verify_pqe_cli.py"):
        print(f"# running reference/{script}", flush=True)
        subprocess.run([sys.executable, script], cwd=REFERENCE, check=True)


def _check_skolem_roundtrip() -> None:
    """§4.2 end to end: the engine skolemizes on load, and the client turns the term back into the
    blank node it stands for. Before sk existed this query produced NO answer at all -- RDF4J makes
    STR(?bnode) a type error, so the answer gate's BIND was unbound and CONSTRUCT dropped it."""
    import tempfile

    print("# skolemization round trip (blank node in an answer)", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "bnode.ttl"
        data.write_text(
            "@prefix d: <urn:d:> .\n"
            "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
            "d:t1 rdf:subject d:a ; rdf:predicate d:p ; rdf:object _:x .\n", encoding="utf-8")
        query = Path(tmp) / "q.sparql"
        query.write_text("SELECT ?y WHERE { <urn:d:a> <urn:d:p> ?y }\n", encoding="utf-8")
        out = subprocess.run(
            ["java", "-cp", str(JAR), "npcs.circuit.CircuitRun", "--construction=flat",
             "Standard", str(data), str(query)],
            stdout=subprocess.PIPE, check=True).stdout.decode("utf-8")
    circ, answers, bindings = circuit_io.parse(out)
    if len(answers) != 1:
        raise AssertionError(f"a blank-node answer must survive; got {len(answers)} answer gates")
    binding = bindings[next(iter(answers))]
    if binding.get("y") != "b" + circuit_io.US + "x":
        raise AssertionError(f"sk^-1 must report the original blank node, got {binding!r}")
    print("SKOLEM ROUND TRIP OK: answer binds _:x, reported as a blank node")


def _check_composition() -> None:
    """Composed shapes against two independent oracles. Needs the jar, so it runs after it."""
    print("# running reference/verify_composition.py", flush=True)
    subprocess.run([sys.executable, "verify_composition.py"], cwd=REFERENCE, check=True)


def _fresh_engine_circuit() -> str:
    if shutil.which("java") is None:
        raise RuntimeError("java was not found; install Java 11+ before running the engine smoke test")
    if not JAR.is_file():
        raise RuntimeError("engine/target/npcs-rewrite.jar is missing; run: mvn -q -f engine/pom.xml package")

    cmd = [
        "java", "-jar", str(JAR), "circuit", "Standard", str(DATA), str(QUERY_FILE),
    ]
    print("# generating a fresh circuit through the fat-JAR circuit entry point", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-4000:]
        raise RuntimeError(f"CircuitRun failed with exit {proc.returncode}:\n{tail}")
    lines = [line for line in proc.stdout.splitlines() if line.strip().endswith(" .")]
    if not lines:
        raise RuntimeError("CircuitRun succeeded but emitted no N-Triples")
    print("# captured this run's CircuitRun stdout (no checked-in circuit fixture read)")
    return "\n".join(lines) + "\n"


def _pwe_truth():
    out = {}
    for answer, probability in wmc.pwe(QUERY, SELECT, EDGES, WEIGHTS).items():
        if probability <= 1e-12:
            continue
        values = dict(answer)
        binding = {
            var.lstrip("?"): circuit_io.canon_iri(values[var]) if var in values else "u"
            for var in SELECT
        }
        out[circuit_io.answer_key(binding)] = probability
    return out


def _check_fresh_circuit(nt: str) -> None:
    circ, answers, bindings = circuit_io.parse(nt)
    if not answers:
        raise AssertionError("the freshly generated circuit has no answer gates")

    got = {}
    for gate in answers:
        key = circuit_io.answer_key(bindings[gate])
        if key in got:
            raise AssertionError(f"duplicate structured answer binding: {key}")
        got[key] = compile_bdd.probability(circ, gate, WEIGHTS)[0]

    truth = _pwe_truth()
    if set(got) != set(truth):
        raise AssertionError(f"answer-set mismatch: circuit={sorted(got)} PWE={sorted(truth)}")
    mismatches = {
        key: (got[key], truth[key])
        for key in got
        if abs(got[key] - truth[key]) >= 1e-9
    }
    if mismatches:
        raise AssertionError(f"fresh circuit WMC != PWE: {mismatches}")

    canonical = "\n".join(sorted(nt.splitlines()))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    print(
        f"FRESH ENGINE CIRCUIT OK: triples={len(nt.splitlines())} "
        f"answers={len(answers)} sha256={digest} WMC==PWE"
    )


def _check_pqe_jar_cli() -> None:
    """Exercise the documented one-command --jar path, including App dispatch."""
    cmd = [
        sys.executable, str(REFERENCE / "pqe.py"), "--jar", str(JAR),
        "--data", str(DATA), "--query", str(QUERY_FILE),
        "--probabilities", str(PROBABILITIES), "--oracle",
    ]
    print("# exercising pqe.py --jar end-to-end entry point", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pqe.py --jar failed with exit {proc.returncode}:\n{proc.stderr[-4000:]}")
    result = json.loads(proc.stdout)
    got = {
        row["binding"]["z"]["value"]: row["probability"]
        for row in result["answers"]
    }
    truth = {
        dict(answer)["?z"]: probability
        for answer, probability in wmc.pwe(QUERY, SELECT, EDGES, WEIGHTS).items()
        if probability > 1e-12
    }
    if set(got) != set(truth) or any(abs(got[k] - truth[k]) >= 1e-9 for k in truth):
        raise AssertionError(f"pqe.py --jar result mismatch: got={got} truth={truth}")
    print(f"PQE --JAR CLI OK: answers={result['answer_count']} gates={result['gate_count']} WMC==PWE")


def main() -> int:
    try:
        _run_python_checks()
        _check_fresh_circuit(_fresh_engine_circuit())
        _check_pqe_jar_cli()
        _check_composition()
        _check_skolem_roundtrip()
    except (AssertionError, json.JSONDecodeError, KeyError, RuntimeError,
            TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"QUICK VERIFY FAILED: {exc}", file=sys.stderr)
        return 1
    print("QUICK VERIFY ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
