#!/usr/bin/env python3
"""Validate asserted and mixed GraphDB repositories before a formal run."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Sequence
import urllib.error
import urllib.request


SCHEMA = "wdbench-graphdb-store-evidence-v1"
DATASET_SCHEMA = "wdbench-rdfstar11-dataset-v1"
JSON_RESULTS = "application/sparql-results+json"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

GENERATED_STATE_ASK = """ASK {
  {
    VALUES ?predicate {
      <urn:sc:message>
      <urn:sc:gate>
      <urn:circuit:in>
      <urn:circuit:feeds>
      <urn:circuit:minuend>
      <urn:circuit:subtrahend>
      <urn:circuit:answerRoot>
      <urn:circuit:rlvl>
      <urn:circuit:rpath>
      <urn:circuit:rfrom>
      <urn:circuit:rto>
    }
    ?subject ?predicate ?object .
  }
  UNION
  {
    VALUES ?gateType {
      <urn:circuit:Times>
      <urn:circuit:Plus>
      <urn:circuit:Minus>
    }
    ?subject a ?gateType .
  }
  UNION
  {
    GRAPH ?graph {
      ?subject ?predicate ?object .
    }
    FILTER(STRSTARTS(STR(?graph), "urn:circuit:run:"))
  }
}"""


class ValidationError(RuntimeError):
    """The loaded repositories do not match the prepared source files."""


def _positive_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return result


def _read_url(request: urllib.request.Request, timeout_s: float) -> bytes:
    try:
        with _OPENER.open(request, timeout=timeout_s) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read(2048).decode("utf-8", errors="replace")
        raise ValidationError("HTTP %d: %s" % (error.code, detail.strip())) from error
    except urllib.error.URLError as error:
        raise ValidationError("endpoint request failed: %s" % error) from error


def _repository_size(endpoint: str, timeout_s: float) -> int:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/size", headers={"Accept": "text/plain"}
    )
    raw = _read_url(request, timeout_s)
    try:
        return int(raw.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError) as error:
        raise ValidationError("repository size is not an integer") from error


def _ask(endpoint: str, query: str, timeout_s: float) -> bool:
    request = urllib.request.Request(
        endpoint,
        data=query.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/sparql-query", "Accept": JSON_RESULTS},
    )
    try:
        payload = json.loads(_read_url(request, timeout_s).decode("utf-8"))
        value = payload["boolean"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValidationError("ASK response is not valid SPARQL Results JSON") from error
    if not isinstance(value, bool):
        raise ValidationError("ASK response boolean is invalid")
    return value


def _atomic_json(path: Path, value: Any) -> None:
    if path.exists():
        raise ValidationError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise ValidationError("partial output already exists: %s" % partial)
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def validate(
    metadata_path: Path,
    base_endpoint: str,
    mixed_endpoint: str,
    timeout_s: float,
) -> Dict[str, Any]:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValidationError("timeout must be positive and finite")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValidationError("invalid dataset metadata: %s" % error) from error
    if metadata.get("schema") != DATASET_SCHEMA or metadata.get("status") != "complete":
        raise ValidationError("unexpected dataset metadata schema or status")
    if metadata.get("ground") is not True or metadata.get("blank_node_terms") != 0:
        raise ValidationError("prepared dataset is not certified as ground")
    direct_triples = int(metadata["direct_triples"])
    if direct_triples < 1:
        raise ValidationError("prepared dataset is empty")
    base_size = _repository_size(base_endpoint, timeout_s)
    mixed_size = _repository_size(mixed_endpoint, timeout_s)
    expected_mixed = direct_triples * 2
    checks = {
        "base_size_equals_prepared_direct_lines": base_size == direct_triples,
        "mixed_size_equals_direct_plus_occurrences": mixed_size == expected_mixed,
        "base_is_nonempty": _ask(base_endpoint, "ASK { ?s ?p ?o }", timeout_s),
        "base_has_no_occurrence_statements": not _ask(
            base_endpoint,
            "ASK { << ?s ?p ?o >> <http://example.org/occurrenceOf> ?token }",
            timeout_s,
        ),
        "old_rdf_star_occurrence_query_matches": _ask(
            mixed_endpoint,
            "ASK { << ?s ?p ?o >> <http://example.org/occurrenceOf> ?token }",
            timeout_s,
        ),
        "mixed_has_no_generated_circuit_state": not _ask(
            mixed_endpoint,
            GENERATED_STATE_ASK,
            timeout_s,
        ),
    }
    return {
        "schema": SCHEMA,
        "status": "ok" if all(checks.values()) else "invalid",
        "dataset_metadata": str(metadata_path),
        "base_endpoint": base_endpoint,
        "mixed_endpoint": mixed_endpoint,
        "prepared_dataset_ground": True,
        "prepared_direct_lines": direct_triples,
        "expected_base_statements": direct_triples,
        "observed_base_statements": base_size,
        "expected_mixed_statements": expected_mixed,
        "observed_mixed_statements": mixed_size,
        "checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--base-endpoint", required=True)
    parser.add_argument("--mixed-endpoint", required=True)
    parser.add_argument("--timeout", type=_positive_float, default=5000.0)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate(
            args.metadata.resolve(),
            args.base_endpoint,
            args.mixed_endpoint,
            args.timeout,
        )
        _atomic_json(args.out.resolve(), result)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
