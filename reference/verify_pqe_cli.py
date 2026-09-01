"""Regression for the user-facing circuit -> answer-probability CLI."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--production", action="store_true",
        help="exercise the default native CUDD path instead of the test oracle",
    )
    args = parser.parse_args(argv)
    backend_args = [] if args.production else ["--oracle"]
    expected_backend = "cudd" if args.production else "oracle"
    nt = """\
<urn:test:t> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Times> .
<urn:test:t> <urn:circuit:in> <urn:test:x> .
<urn:test:t> <urn:circuit:feeds> <urn:test:a> .
<urn:test:a> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Plus> .
<urn:test:a> <urn:circuit:answer> "A|z=urn:test:answer" .
<urn:test:a> <urn:circuit:binding> <urn:test:b> .
<urn:test:b> <urn:circuit:var> "z" .
<urn:test:b> <urn:circuit:val> <urn:test:answer> .
"""
    duplicate_nt = """\
<urn:test:t1> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Times> .
<urn:test:t1> <urn:circuit:in> <urn:test:x1> .
<urn:test:t1> <urn:circuit:feeds> <urn:test:a1> .
<urn:test:a1> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Plus> .
<urn:test:a1> <urn:circuit:answer> "A|z=urn:test:answer" .
<urn:test:a1> <urn:circuit:binding> <urn:test:b1> .
<urn:test:b1> <urn:circuit:var> "z" .
<urn:test:b1> <urn:circuit:val> <urn:test:answer> .
<urn:test:t2> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Times> .
<urn:test:t2> <urn:circuit:in> <urn:test:x2> .
<urn:test:t2> <urn:circuit:feeds> <urn:test:a2> .
<urn:test:a2> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Plus> .
<urn:test:a2> <urn:circuit:answer> "A|z=urn:test:answer" .
<urn:test:a2> <urn:circuit:binding> <urn:test:b2> .
<urn:test:b2> <urn:circuit:var> "z" .
<urn:test:b2> <urn:circuit:val> <urn:test:answer> .
"""
    with tempfile.TemporaryDirectory() as td:
        circuit = Path(td) / "circuit.nt"
        probabilities = Path(td) / "probabilities.json"
        circuit.write_text(nt, encoding="utf-8")
        probabilities.write_text(json.dumps({"urn:test:x": 0.37}), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HERE / "pqe.py"), "--circuit", str(circuit),
             "--probabilities", str(probabilities), *backend_args,
             "--compile-mode", "shared"],
            check=True, text=True, stdout=subprocess.PIPE,
        )
        per_root = subprocess.run(
            [sys.executable, str(HERE / "pqe.py"), "--circuit", str(circuit),
             "--probabilities", str(probabilities), *backend_args,
             "--compile-mode", "per-root"],
            check=True, text=True, stdout=subprocess.PIPE,
        )
        probabilities.write_text("{}", encoding="utf-8")
        missing = subprocess.run(
            [sys.executable, str(HERE / "pqe.py"), "--circuit", str(circuit),
             "--probabilities", str(probabilities), *backend_args],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if (missing.returncode == 0 or "missing probabilities" not in missing.stderr
                or "Traceback" in missing.stderr):
            raise AssertionError(f"CLI did not report missing weights cleanly: {missing.stderr!r}")

        circuit.write_text(duplicate_nt, encoding="utf-8")
        probabilities.write_text(
            json.dumps({"urn:test:x1": 0.5, "urn:test:x2": 0.5}),
            encoding="utf-8",
        )
        merged = subprocess.run(
            [sys.executable, str(HERE / "pqe.py"), "--circuit", str(circuit),
             "--probabilities", str(probabilities), *backend_args,
             "--compile-mode", "shared"],
            check=True, text=True, stdout=subprocess.PIPE,
        )
    result = json.loads(r.stdout)
    per_root_result = json.loads(per_root.stdout)
    merged_result = json.loads(merged.stdout)
    row = result["answers"][0]
    if (result["answer_count"] != 1
            or row["binding"]["z"] != {"type": "iri", "value": "urn:test:answer"}
            or abs(row["probability"] - 0.37) >= 1e-12
            or result["compilation"]["backend"] != expected_backend
            or result["compilation"]["mode"] != "shared"
            or per_root_result["compilation"]["mode"] != "per-root"
            or abs(per_root_result["answers"][0]["probability"] - row["probability"]) >= 1e-12):
        raise AssertionError(f"unexpected CLI result: {result!r}")
    merged_row = merged_result["answers"][0]
    merge_metrics = merged_result["answer_root_normalization"]
    if (merged_result["answer_count"] != 1
            or merged_result["raw_answer_root_count"] != 2
            or merge_metrics["merge_plus_nodes"] != 1
            or merge_metrics["merge_plus_edges"] != 2
            or abs(merged_row["probability"] - 0.75) >= 1e-12):
        raise AssertionError(f"duplicate answer roots were not merged: {merged_result!r}")
    print("pqe CLI: ALL OK")


if __name__ == "__main__":
    main()
