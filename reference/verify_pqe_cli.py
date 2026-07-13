"""Offline regression for the user-facing circuit -> answer-probability CLI."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def main():
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
    with tempfile.TemporaryDirectory() as td:
        circuit = Path(td) / "circuit.nt"
        probabilities = Path(td) / "probabilities.json"
        circuit.write_text(nt, encoding="utf-8")
        probabilities.write_text(json.dumps({"urn:test:x": 0.37}), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(HERE / "pqe.py"), "--circuit", str(circuit),
             "--probabilities", str(probabilities)],
            check=True, text=True, stdout=subprocess.PIPE,
        )
        probabilities.write_text("{}", encoding="utf-8")
        missing = subprocess.run(
            [sys.executable, str(HERE / "pqe.py"), "--circuit", str(circuit),
             "--probabilities", str(probabilities)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if (missing.returncode == 0 or "missing probabilities" not in missing.stderr
                or "Traceback" in missing.stderr):
            raise AssertionError(f"CLI did not report missing weights cleanly: {missing.stderr!r}")
    result = json.loads(r.stdout)
    row = result["answers"][0]
    if (result["answer_count"] != 1
            or row["binding"]["z"] != {"type": "iri", "value": "urn:test:answer"}
            or abs(row["probability"] - 0.37) >= 1e-12):
        raise AssertionError(f"unexpected CLI result: {result!r}")
    print("pqe CLI: ALL OK")


if __name__ == "__main__":
    main()
