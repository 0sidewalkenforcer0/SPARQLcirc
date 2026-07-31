#!/usr/bin/env python3
"""Build or load an engine RDF circuit and return term-aware answer probabilities.

Production examples (run from ``reference/`` after installing CUDD)::

    python3 pqe.py --circuit data/drug.circuit.nt \
        --probabilities data/drug.probabilities.json

    python3 pqe.py --jar ../engine/target/npcs-rewrite.jar \
        --data data/drug.reified.ttl --query queries/drug3hop.sparql \
        --probabilities data/drug.probabilities.json

The probability file is a JSON object from the complete leaf/token IRI emitted by
the circuit to a number in [0, 1]. Output is JSON; RDF term kinds are retained.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import circuit_io
import compiler


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--circuit", type=Path, help="existing N-Triples circuit")
    source.add_argument("--jar", type=Path, help="built engine fat JAR; constructs the circuit first")
    p.add_argument("--data", type=Path, help="reified Turtle input (required with --jar)")
    p.add_argument("--query", type=Path, help="SELECT query file (required with --jar)")
    p.add_argument("--probabilities", type=Path, required=True,
                   help="JSON object mapping complete token IRIs to probabilities")
    p.add_argument("--scheme", default="Standard", choices=("Standard", "SPARQL_Star"))
    p.add_argument("--endpoint", help="optional remote SPARQL query endpoint")
    p.add_argument("--construction", choices=("factored", "flat"),
                   help="construction mode passed to the engine (default: the engine's own "
                        "default, factored); flat is the read-only-endpoint route")
    p.add_argument("--compile-mode", default="shared", choices=("shared", "per-root"),
                   help="one shared CUDD manager (default) or one manager per answer root")
    p.add_argument("--oracle", action="store_true",
                   help="testing only: use the bundled Python ROBDD instead of production CUDD")
    return p


def _load_probabilities(path: Path) -> dict[str, float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("the probability file must contain one JSON object")
    out = {}
    for token, value in raw.items():
        if not isinstance(token, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("probabilities must map string token IRIs to numbers")
        value = float(value)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"probability for {token!r} is outside [0, 1]: {value}")
        out[token] = value
    return out


def _build_circuit(args: argparse.Namespace) -> str:
    if args.circuit is not None:
        if (args.data is not None or args.query is not None or args.endpoint is not None
                or args.construction is not None):
            raise ValueError(
                "--data, --query, --endpoint and --construction are only valid with --jar")
        return args.circuit.read_text(encoding="utf-8")

    if args.data is None or args.query is None:
        raise ValueError("--jar requires both --data and --query")
    cmd = ["java", "-jar", str(args.jar), "circuit"]
    if args.construction is not None:
        cmd.append(f"--construction={args.construction}")
    cmd += [args.scheme, str(args.data), str(args.query)]
    if args.endpoint:
        cmd.append(args.endpoint)
    # Keep the construction plan/progress visible on stderr; capture only the
    # N-Triples stream that becomes this invocation's WMC input.
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    return completed.stdout.decode("utf-8")


def _term_json(canonical: str) -> dict[str, str]:
    if canonical == "u":
        return {"type": "unbound"}
    parts = canonical.split(circuit_io.US)
    if parts[0] == "i" and len(parts) == 2:
        return {"type": "iri", "value": parts[1]}
    if parts[0] == "b" and len(parts) == 2:
        return {"type": "blank", "value": parts[1]}
    if parts[0] == "l" and len(parts) == 4:
        result = {"type": "literal", "value": parts[1], "datatype": parts[2]}
        if parts[3]:
            result["language"] = parts[3]
        return result
    raise ValueError(f"invalid canonical RDF term from circuit parser: {canonical!r}")


def evaluate(nt: str, probabilities: dict[str, float], compile_mode: str = "shared",
             oracle: bool = False) -> dict:
    circ, answers, bindings = circuit_io.parse(nt)
    ordered_roots = sorted(answers, key=lambda g: circuit_io.answer_key(bindings[g]))
    roots = {}
    seen_bindings = set()
    for root in ordered_roots:
        binding_key = circuit_io.answer_key(bindings[root])
        if binding_key in seen_bindings:
            raise ValueError(f"multiple answer roots carry the same structured binding: {binding_key}")
        seen_bindings.add(binding_key)
        roots[binding_key] = root

    required = set(compiler.deterministic_order(circ, roots))
    missing = sorted(required - probabilities.keys())
    if missing:
        preview = ", ".join(missing[:5])
        suffix = " ..." if len(missing) > 5 else ""
        raise ValueError(f"missing probabilities for {len(missing)} token(s): {preview}{suffix}")

    batch = compiler.compile_many(
        circ, roots, mode=compile_mode, backend="oracle" if oracle else "cudd")
    answer_probabilities = batch.wmc_many(probabilities)
    root_sizes = batch.root_sizes()
    rows = []
    for root in ordered_roots:
        binding_key = circuit_io.answer_key(bindings[root])
        rows.append({
            "binding": {name: _term_json(value) for name, value in sorted(bindings[root].items())},
            "probability": answer_probabilities[binding_key],
            "bdd_nodes": root_sizes[binding_key],
            "root": root,
        })
    return {
        "answers": rows,
        "answer_count": len(rows),
        "gate_count": len(circ),
        "compilation": dict(batch.metrics),
    }


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        probabilities = _load_probabilities(args.probabilities)
        result = evaluate(_build_circuit(args), probabilities,
                          compile_mode=args.compile_mode, oracle=args.oracle)
    except (KeyError, OSError, RecursionError, RuntimeError, TypeError,
            ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"pqe: error: {exc}\n")
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
